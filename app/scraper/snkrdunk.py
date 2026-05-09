import re
import asyncio
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional
from app.scraper.browser_pool import get_browser

JPY_TO_TWD = 0.20

_executor = ThreadPoolExecutor(max_workers=2)

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"


def _set_name_variants(s: str) -> list:
    """產生 set_name_jp 的常見寫法變體，逐一嘗試以提高 mapping 命中率。

    處理觀察到的差異：
    - 「メガ」 vs 「MEGA」 開頭（e.g. メガドリームex vs MEGAドリームex）
    - 「ex」 vs 「EX」 大小寫
    - 全形 vs 半形數字
    """
    if not s:
        return []
    out = [s]
    # メガ ↔ MEGA 互換
    if s.startswith("メガ"):
        out.append("MEGA" + s[2:])
    elif s.startswith("MEGA"):
        out.append("メガ" + s[4:])
    # ex 大小寫
    if "ex" in s:
        out.append(s.replace("ex", "EX"))
    if "EX" in s:
        out.append(s.replace("EX", "ex"))
    # 去重保留順序
    seen = set()
    uniq = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def _lookup_apparel_id(card_number: Optional[str],
                        set_name_jp: Optional[str],
                        card_name_jp: Optional[str]) -> Optional[int]:
    """從 snkrdunk_mapping 表查 apparel_id。只用於日文 metadata 充足的卡。

    對映策略（從嚴到鬆）：
      1. set_name_jp + card_number 完全比對（每個 variant 都試）
      2. full_title 含 set_name_jp + card_number
      3. card_name_jp + card_number（同 set 多版本時可能誤命中）
    """
    if not card_number:
        return None
    n = re.sub(r"\D", "", card_number).lstrip("0") or "0"
    if n == "0":
        return None
    try:
        conn = sqlite3.connect(str(_DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            variants = _set_name_variants(set_name_jp) if set_name_jp else []

            # 1. exact set_name_jp + card_number（試所有變體）
            for v in variants:
                row = conn.execute(
                    "SELECT apparel_id FROM snkrdunk_mapping "
                    "WHERE is_pokemon=1 AND set_name_jp=? AND card_number=? "
                    "ORDER BY apparel_id LIMIT 1",
                    (v, n),
                ).fetchone()
                if row:
                    return row["apparel_id"]

            # 2. fallback: full_title 含 set_name_jp（試所有變體）+ card_number
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

            # 3. card_name_jp + card_number 模糊比對
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
        print(f"SNKR mapping lookup failed: {e}")
        return None


def _clean_set_id(set_id: str) -> str:
    if not set_id:
        return ""
    sid = re.sub(r"^(en|jp|zh|cn)-", "", set_id)
    return sid.replace("-", " ").strip()


def _title_matches_card_number(title: str, card_number: str) -> bool:
    """SNKRDUNK 標題常見格式 [M2a 234/193] 或 234/193 或 No.234"""
    if not card_number:
        return True
    try:
        n = int(re.sub(r"\D", "", card_number) or "0")
    except ValueError:
        return False
    if n == 0:
        return False
    patterns = [
        rf"\b0*{n}\s*/\s*\d+",
        rf"#0*{n}\b",
        rf"No\.?\s*0*{n}\b",
        rf"\[[^\]]*\b0*{n}\s*/\s*\d+[^\]]*\]",
    ]
    return any(re.search(p, title, re.IGNORECASE) for p in patterns)


def _parse_jp_relative_date(text: str) -> datetime:
    """解析「3時間前」「2日前」「1週間前」「1ヶ月前」"""
    now = datetime.now()
    if not text:
        return now
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
    m = re.search(r"(\d+)\s*分前", text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    # 絕對日期 MM/DD
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m:
        try:
            return now.replace(month=int(m.group(1)), day=int(m.group(2)))
        except ValueError:
            pass
    return now


def _find_best_product(page, card_number: str, set_name: str) -> Optional[str]:
    """在搜尋結果中找最匹配的商品連結。

    規則：候選必須同時命中 card_number 和至少一個 set_name token（>=2 字），
    兩個都硬性條件——不然寧可回 None，避免配到完全不同系列的卡。
    """
    items = page.query_selector_all("a[href*='/apparels/']")
    clean_set = _clean_set_id(set_name).lower() if set_name else ""
    set_tokens = [tok for tok in clean_set.split() if len(tok) >= 2]

    candidates = []
    for item in items[:20]:
        href = item.get_attribute("href") or ""
        if "/apparels/" not in href:
            continue
        try:
            text = item.inner_text()
        except Exception:
            continue

        cnum_hit = bool(card_number and _title_matches_card_number(text, card_number))
        if card_number and not cnum_hit:
            continue

        set_hit = (not set_tokens) or any(tok in text.lower() for tok in set_tokens)
        if set_tokens and not set_hit:
            # 系列 token 一個都沒命中 → 幾乎可以確定是不同系列的同卡號
            continue

        score = (10 if cnum_hit else 0) + (sum(1 for t in set_tokens if t in text.lower()))
        if score > 0:
            full = f"https://snkrdunk.com{href}" if href.startswith("/") else href
            candidates.append((score, full, text[:80]))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _parse_absolute_jp_date(text: str):
    """解析 '2026/04/17' 格式；失敗回 None"""
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", text.strip())
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _scrape_detail_psa10(page, detail_url: str, title_hint: str) -> list:
    """走 sales-histories 子頁抓 PSA10 成交紀錄。

    sales-histories 頁把各狀態（A/B/C/D/PSA10/PSA9/PSA8以下）分成多個區段，
    每筆紀錄是三行：時間 / 狀態 / 金額（純數字，無 ¥）。
    """
    # 從商品 URL 擷取 id，切到 sales-histories 子頁
    m = re.search(r"/apparels/(\d+)", detail_url)
    if not m:
        return []
    apparel_id = m.group(1)
    history_url = f"https://snkrdunk.com/apparels/{apparel_id}/sales-histories?slide=right"

    page.goto(history_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3500)
    # 滾動觸發 lazy render
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 1200)")
        page.wait_for_timeout(600)

    # 優先從 og:title 取商品名
    try:
        og = page.query_selector('meta[property="og:title"]')
        if og:
            og_content = (og.get_attribute("content") or "").strip()
            og_content = re.sub(r"の価格.*$", "", og_content)
            og_content = re.sub(r"の新品/中古.*$", "", og_content)
            og_content = re.sub(r"｜スニダン.*$", "", og_content).strip()
            if og_content and not og_content.isdigit():
                title_hint = og_content[:150]
    except Exception:
        pass

    try:
        body_text = page.inner_text("body")
    except Exception:
        return []

    compact = [l.strip() for l in body_text.split("\n") if l.strip()]

    # 找 PSA10 區段範圍
    start = end = -1
    for idx, line in enumerate(compact):
        if "PSA10の売買履歴" in line and start == -1:
            start = idx + 1
        elif start != -1 and ("PSA10の売買相場" in line or "PSA10を即" in line):
            end = idx
            break
    if start == -1:
        return []
    if end == -1:
        end = len(compact)

    results = []
    record_idx = 0
    i = start
    while i < end - 2:
        # 格式：時間 / PSA10 / 金額
        if compact[i + 1] == "PSA10":
            time_str = compact[i]
            price_str = compact[i + 2]
            m = re.match(r"([\d,]+)$", price_str)
            if m:
                try:
                    jpy = float(m.group(1).replace(",", ""))
                except ValueError:
                    i += 1
                    continue
                abs_dt = _parse_absolute_jp_date(time_str)
                sale_dt = abs_dt if abs_dt else _parse_jp_relative_date(time_str)
                # stable URL：用 ISO date + 整數價格當 fragment，跨次 refresh 同一筆會撞同一 url
                _date_key = (sale_dt.date().isoformat() if sale_dt else f"idx{record_idx}")
                uniq_url = f"https://snkrdunk.com/apparels/{apparel_id}#psa10-{_date_key}-{int(jpy)}"
                results.append({
                    "listing_title": title_hint,
                    "price_jpy": jpy,
                    "price_twd": round(jpy * JPY_TO_TWD, 0),
                    "listing_url": uniq_url,
                    "sale_date": sale_dt.isoformat(),
                    "source": "snkrdunk",
                })
                record_idx += 1
                i += 3
                continue
        i += 1

    return results


def _scrape_by_apparel_id_sync(apparel_id: int, title_hint: str = "") -> list:
    """直接打 /apparels/{id}/sales-histories，不走搜尋。"""
    context = None
    try:
        browser = get_browser()
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ja-JP",
        )
        page = context.new_page()
        detail_url = f"https://snkrdunk.com/apparels/{apparel_id}"
        return _scrape_detail_psa10(page, detail_url, title_hint or f"apparel/{apparel_id}")
    except Exception as e:
        print(f"SNKRDUNK 直取失敗 ({apparel_id}): {e}")
        return []
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


def _scrape_snkrdunk_sync(query: str, card_number: Optional[str] = None,
                          set_name: Optional[str] = None) -> list:
    """[legacy] 走搜尋頁。SNKRDUNK 已將搜尋改為登入後才可用，現在會回 fallback 頁。
    保留作為 PSA cert 查詢的最後手段；正常卡片查詢請走 _scrape_by_apparel_id_sync。
    """
    context = None
    try:
        browser = get_browser()
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ja-JP",
        )
        page = context.new_page()

        search_url = f"https://snkrdunk.com/search?q={query.replace(' ', '+')}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        try:
            page.wait_for_selector("a[href*='/apparels/']", timeout=10000)
        except Exception:
            return []

        # 防 fallback「おすすめアイテム」頁誤命中：搜尋失敗時 page.title() 會是該名稱
        try:
            if "おすすめアイテム" in page.title():
                return []
        except Exception:
            pass

        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(500)

        best_url = _find_best_product(page, card_number, set_name)
        if not best_url:
            return []

        title_hint = query
        for it in page.query_selector_all("a[href*='/apparels/']"):
            href = it.get_attribute("href") or ""
            if href in best_url:
                try:
                    title_hint = it.inner_text().split("\n")[0].strip()[:100] or query
                except Exception:
                    pass
                break

        return _scrape_detail_psa10(page, best_url, title_hint)

    except Exception as e:
        print(f"SNKRDUNK 搜尋失敗: {e}")
        return []
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass


class SnkrdunkScraper:
    async def search_by_psa_cert(self, cert_number: str) -> list:
        query = f"PSA {cert_number}"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _scrape_snkrdunk_sync, query, None, None)

    async def search_by_card_name(self, card_name: str, grade: str = "10",
                                   card_number: Optional[str] = None,
                                   set_name: Optional[str] = None,
                                   set_name_jp: Optional[str] = None,
                                   card_name_jp: Optional[str] = None) -> list:
        """SNKRDUNK 是日文站，只對有日文 metadata 的卡查；純英文卡直接回空。

        策略：先查 snkrdunk_mapping 表（apparel_id 直查），找不到才退回搜尋。
        SNKRDUNK 搜尋頁已改為需登入，所以 mapping 是主要路徑。
        """
        if not set_name_jp and not card_name_jp:
            return []

        # 1. mapping 直查（主要路徑）
        apparel_id = _lookup_apparel_id(card_number, set_name_jp, card_name_jp)
        if apparel_id:
            loop = asyncio.get_event_loop()
            title_hint = f"{card_name_jp or card_name} {card_number or ''}".strip()
            results = await loop.run_in_executor(
                _executor, _scrape_by_apparel_id_sync, apparel_id, title_hint
            )
            return results

        # 2. fallback: 走搜尋（多半失敗，但留著作為 mapping 表沒收錄時的最後手段）
        queries = []
        if set_name_jp and card_number:
            queries.append(f"{set_name_jp} {card_number}")
        if card_name_jp and card_number:
            queries.append(f"{card_name_jp} {card_number}")

        loop = asyncio.get_event_loop()
        for q in queries:
            results = await loop.run_in_executor(
                _executor, _scrape_snkrdunk_sync, q, card_number, set_name_jp
            )
            if results:
                return results
        return []

    async def close(self):
        pass


async def get_snkrdunk_prices(query: str, is_cert: bool = False, grade: str = "10",
                                card_number: Optional[str] = None,
                                set_name: Optional[str] = None,
                                set_name_jp: Optional[str] = None,
                                card_name_jp: Optional[str] = None) -> list:
    """主入口：2026-05-05 改 delegate 到 httpx 版本（snkrdunk_http），50x 提速。

    SNKRDUNK 已暴露公開 JSON API `/v1/apparels/{id}/sales-history`，不需 chromium。
    舊 chromium 版本 (_scrape_snkrdunk_sync / _scrape_by_apparel_id_sync) 保留供
    refresh_snkr.py / backfill_snkr.py 使用，以後可以分階段汰換。
    """
    from app.scraper.snkrdunk_http import get_snkrdunk_prices as _http_get
    try:
        return await _http_get(
            query, is_cert=is_cert, grade=grade,
            card_number=card_number, set_name=set_name,
            set_name_jp=set_name_jp, card_name_jp=card_name_jp,
        )
    except Exception as e:
        print(f"[snkrdunk] httpx fallback to chromium due to: {e}")
        # fallback：原 chromium 版（保險）
        scraper = SnkrdunkScraper()
        try:
            if is_cert:
                return await scraper.search_by_psa_cert(query)
            return await scraper.search_by_card_name(
                query, grade, card_number, set_name, set_name_jp, card_name_jp
            )
        finally:
            await scraper.close()


if __name__ == "__main__":
    async def test():
        print("Testing SNKRDUNK scraper (detail page)...")
        results = await get_snkrdunk_prices(
            "メガリザードンX", is_cert=False,
            card_number="110", set_name="jp-Inferno-X",
        )
        print(f"Found {len(results)} PSA10 records")
        for r in results[:5]:
            print(f"  ¥{r['price_jpy']:,.0f} / NT${r['price_twd']:,.0f} — {r['sale_date']}")
    asyncio.run(test())
