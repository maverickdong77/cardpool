"""
SNKRDUNK 純 httpx scraper（取代原 chromium 版）

關鍵發現：snkrdunk.com 有公開 JSON API
  GET /v1/apparels/{apparel_id}/sales-history?page={n}&per_page=20&condition_id={cid}
  - PSA10 = condition_id 22
  - PSA9  = condition_id 23
  - 不需登入、不需 JS 渲染
  - 絕對日期格式 YYYY/MM/DD（舊資料），相對日期 X時間前/X日前（新資料）

效能：
  - 一張卡 ~0.3s（vs chromium ~1.6s，5x 提升）
  - 並行可拉到 30+
  - 沒 RAM 開銷（chromium 一個 ~250MB）

對外介面跟 app/scraper/snkrdunk.py 完全一樣：
  await get_snkrdunk_prices(query, is_cert=False, grade='10',
                             card_number=..., set_name=..., set_name_jp=..., card_name_jp=...)
回 list[dict]，每筆含 listing_title / price_jpy / price_twd / listing_url / sale_date / source。

對 mapping 表的依賴跟原版一樣（_lookup_apparel_id），所以 build_snkr_mapping.py 邏輯不用改。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

JPY_TO_TWD = 0.20
DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"

# PSA grade → SNKRDUNK condition_id
GRADE_TO_CONDITION = {
    "10": 22,
    "9": 23,
}

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Referer": "https://snkrdunk.com/",
}


# ============================================================
#   Date parsing — copied from snkrdunk.py to keep behavior identical
# ============================================================

def _parse_jp_relative_date(text: str) -> datetime:
    now = datetime.now()
    if not text:
        return now
    m = re.search(r"(\d+)\s*分前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    m = re.search(r"(\d+)\s*時間前", text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r"(\d+)\s*日前", text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*週間前", text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    m = re.search(r"(\d+)\s*(?:ヶ月|か月|ヵ月)前", text)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m:
        try:
            return now.replace(month=int(m.group(1)), day=int(m.group(2)))
        except ValueError:
            pass
    return now


def _parse_absolute_jp_date(text: str) -> Optional[datetime]:
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", text.strip())
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


# ============================================================
#   Mapping lookup — same logic as snkrdunk.py (sync, sqlite)
# ============================================================

def _set_name_variants(s: str) -> list[str]:
    if not s:
        return []
    out = [s]
    if s.startswith("メガ"):
        out.append("MEGA" + s[2:])
    elif s.startswith("MEGA"):
        out.append("メガ" + s[4:])
    if "ex" in s:
        out.append(s.replace("ex", "EX"))
    if "EX" in s:
        out.append(s.replace("EX", "ex"))
    # 英數 ↔ 日文/漢字 邊界 + - 空格 variant
    # 例：'25thアニバーサリー' ↔ '25th アニバーサリー'
    add_space = re.compile(r'(?<=[A-Za-z0-9])(?=[ぁ-ヿ一-龯])|(?<=[ぁ-ヿ一-龯])(?=[A-Za-z0-9])')
    rm_space = re.compile(r'(?<=[A-Za-z0-9]) +(?=[ぁ-ヿ一-龯])|(?<=[ぁ-ヿ一-龯]) +(?=[A-Za-z0-9])')
    extra = []
    for v in out:
        spaced = add_space.sub(' ', v)
        if spaced != v: extra.append(spaced)
        nospace = rm_space.sub('', v)
        if nospace != v: extra.append(nospace)
    out.extend(extra)
    seen, uniq = set(), []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _lookup_apparel_id(card_number: Optional[str],
                       set_name_jp: Optional[str],
                       card_name_jp: Optional[str]) -> Optional[int]:
    """從 snkrdunk_mapping 表查 apparel_id。等同 snkrdunk.py::_lookup_apparel_id"""
    if not card_number:
        return None
    n = re.sub(r"\D", "", card_number).lstrip("0") or "0"
    if n == "0":
        return None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            variants = _set_name_variants(set_name_jp) if set_name_jp else []

            for v in variants:
                row = conn.execute(
                    "SELECT apparel_id FROM snkrdunk_mapping "
                    "WHERE is_pokemon=1 AND set_name_jp=? AND card_number=? "
                    "ORDER BY apparel_id LIMIT 1",
                    (v, n),
                ).fetchone()
                if row:
                    return row["apparel_id"]

            for v in variants:
                row = conn.execute(
                    "SELECT apparel_id FROM snkrdunk_mapping "
                    "WHERE is_pokemon=1 AND card_number=? "
                    "AND full_title LIKE ? "
                    "ORDER BY apparel_id LIMIT 1",
                    (n, f"%{v}%"),
                ).fetchone()
                if row:
                    return row["apparel_id"]

            if card_name_jp:
                row = conn.execute(
                    "SELECT apparel_id FROM snkrdunk_mapping "
                    "WHERE is_pokemon=1 AND card_number=? "
                    "AND (card_name LIKE ? OR full_title LIKE ?) "
                    "ORDER BY apparel_id LIMIT 1",
                    (n, f"%{card_name_jp}%", f"%{card_name_jp}%"),
                ).fetchone()
                if row:
                    return row["apparel_id"]
            return None
        finally:
            conn.close()
    except Exception as e:
        print(f"[snkrdunk_http] mapping lookup failed: {e}")
        return None


def _lookup_full_title(apparel_id: int) -> str:
    """從 snkrdunk_mapping 表查 apparel 的完整標題（拿來當 listing_title）"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            row = conn.execute(
                "SELECT full_title FROM snkrdunk_mapping WHERE apparel_id=? LIMIT 1",
                (apparel_id,),
            ).fetchone()
            return row[0] if row else f"apparel/{apparel_id}"
        finally:
            conn.close()
    except Exception:
        return f"apparel/{apparel_id}"


# ============================================================
#   API fetcher
# ============================================================

async def fetch_sales_history(
    client: httpx.AsyncClient,
    apparel_id: int,
    grade: str = "10",
    max_pages: int = 25,
    per_page: int = 20,
) -> list[dict]:
    """直打 SNKRDUNK JSON API，分頁抓全部 PSA{grade} 紀錄。

    回原始 list（每筆含 price/date/condition），尚未 normalize 成我們 DB 格式。
    """
    cid = GRADE_TO_CONDITION.get(str(grade))
    if cid is None:
        return []

    out: list[dict] = []
    for page in range(1, max_pages + 1):
        url = (
            f"https://snkrdunk.com/v1/apparels/{apparel_id}/sales-history"
            f"?page={page}&per_page={per_page}&condition_id={cid}"
        )
        try:
            r = await client.get(url, timeout=15)
        except httpx.HTTPError as e:
            print(f"[snkrdunk_http] aid={apparel_id} page={page} fetch err: {e}")
            break
        if r.status_code != 200:
            break
        try:
            j = r.json()
        except Exception:
            break
        history = j.get("history") or []
        if not history:
            break
        out.extend(history)
        if len(history) < per_page:
            break  # 最後一頁
    return out


def _to_record(apparel_id: int, raw: dict, title_hint: str) -> Optional[dict]:
    """SNKR 原始紀錄 → 我們 DB 統一格式。失敗時回 None。"""
    price = raw.get("price")
    date_str = raw.get("date") or ""
    if not price:
        return None
    try:
        jpy = float(price)
    except (TypeError, ValueError):
        return None
    abs_dt = _parse_absolute_jp_date(date_str)
    sale_dt = abs_dt if abs_dt else _parse_jp_relative_date(date_str)
    # stable URL：跟 chromium 版一致，跨 refresh 撞同一筆
    date_key = sale_dt.date().isoformat() if sale_dt else "unknown"
    uniq_url = f"https://snkrdunk.com/apparels/{apparel_id}#psa10-{date_key}-{int(jpy)}"
    return {
        "listing_title": title_hint,
        "price_jpy": jpy,
        "price_twd": round(jpy * JPY_TO_TWD, 0),
        "listing_url": uniq_url,
        "sale_date": sale_dt.isoformat() if sale_dt else None,
        "source": "snkrdunk",
    }


async def scrape_apparel(
    client: httpx.AsyncClient,
    apparel_id: int,
    title_hint: str = "",
    grade: str = "10",
    max_pages: int = 25,
) -> list[dict]:
    """主入口：apparel_id → list[normalized records]"""
    raw_history = await fetch_sales_history(client, apparel_id, grade=grade, max_pages=max_pages)
    if not raw_history:
        return []
    if not title_hint:
        title_hint = _lookup_full_title(apparel_id)
    out = []
    for raw in raw_history:
        rec = _to_record(apparel_id, raw, title_hint)
        if rec:
            out.append(rec)
    return out


async def fetch_apparel(client: httpx.AsyncClient, apparel_id: int) -> Optional[dict]:
    """商品 metadata：minPrice / listingCount / usedMinPrice / usedListingCount。"""
    url = f"https://snkrdunk.com/v1/apparels/{apparel_id}"
    try:
        r = await client.get(url, timeout=15)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except Exception:
        return None


async def fetch_sales_chart(
    client: httpx.AsyncClient,
    apparel_id: int,
    range_key: str = "all",
    condition_id: Optional[int] = 22,
) -> list[tuple[datetime, float]]:
    """價格走勢時序。回傳 [(datetime, jpy), ...]。

    range_key: 'all' / 'oneWeek' / 'oneMonth' / 'threeMonths'
    condition_id: 22=PSA10, 23=PSA9, None=不過濾
    """
    qs = f"?range={range_key}"
    if condition_id is not None:
        qs += f"&condition_id={condition_id}"
    url = f"https://snkrdunk.com/v1/apparels/{apparel_id}/sales-chart{qs}"
    try:
        r = await client.get(url, timeout=15)
    except httpx.HTTPError:
        return []
    if r.status_code != 200:
        return []
    try:
        j = r.json()
    except Exception:
        return []
    points = j.get("points") or []
    out = []
    for p in points:
        if not isinstance(p, list) or len(p) < 2:
            continue
        ts_ms, price = p[0], p[1]
        try:
            dt = datetime.fromtimestamp(int(ts_ms) / 1000)
            out.append((dt, float(price)))
        except (TypeError, ValueError, OSError):
            continue
    return out


# ============================================================
#   Public API — drop-in replacement for snkrdunk.SnkrdunkScraper
# ============================================================

class SnkrdunkHttpScraper:
    """跟 SnkrdunkScraper 介面一樣，但純 httpx 不用 chromium"""

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(headers=DEFAULT_HEADERS)

    async def search_by_psa_cert(self, cert_number: str) -> list[dict]:
        # SNKR 沒有公開 PSA cert 反查 API；保留介面，永遠回空
        # （原 chromium 版走 search 頁也已經被擋成需登入）
        return []

    async def search_by_card_name(
        self,
        card_name: str,
        grade: str = "10",
        card_number: Optional[str] = None,
        set_name: Optional[str] = None,
        set_name_jp: Optional[str] = None,
        card_name_jp: Optional[str] = None,
    ) -> list[dict]:
        if not set_name_jp and not card_name_jp:
            return []
        apparel_id = _lookup_apparel_id(card_number, set_name_jp, card_name_jp)
        if not apparel_id:
            return []
        # 用 mapping 表的完整 SNKR 標題（含 [SetCode N/T] 格式），這樣 _title_matches_card
        # 的 N/T 規則才能驗證；空字串時 scrape_apparel 自動 lookup
        return await scrape_apparel(self._client, apparel_id, "", grade=grade)

    async def close(self):
        if self._owned_client:
            await self._client.aclose()


async def get_snkrdunk_prices(
    query: str,
    is_cert: bool = False,
    grade: str = "10",
    card_number: Optional[str] = None,
    set_name: Optional[str] = None,
    set_name_jp: Optional[str] = None,
    card_name_jp: Optional[str] = None,
) -> list[dict]:
    """Drop-in 替換 app.scraper.snkrdunk.get_snkrdunk_prices"""
    scraper = SnkrdunkHttpScraper()
    try:
        if is_cert:
            return await scraper.search_by_psa_cert(query)
        return await scraper.search_by_card_name(
            query, grade, card_number, set_name, set_name_jp, card_name_jp
        )
    finally:
        await scraper.close()


# ============================================================
#   CLI / smoke test
# ============================================================

if __name__ == "__main__":
    async def smoke():
        # M2 110 = メガリザードンXex (Inferno X) — 已知有 PSA10 資料的卡
        print("=== smoke test: jp-Inferno-X #110 メガリザードンXex ===")
        results = await get_snkrdunk_prices(
            "メガリザードンXex",
            is_cert=False,
            card_number="110",
            set_name="jp-Inferno-X",
            set_name_jp="インフェルノX",
            card_name_jp="メガリザードンXex",
        )
        print(f"Got {len(results)} PSA10 records")
        for r in results[:5]:
            print(f"  ¥{r['price_jpy']:>8,.0f}  NT${r['price_twd']:>7,.0f}  {r['sale_date']}  {r['listing_title'][:60]}")

    asyncio.run(smoke())
