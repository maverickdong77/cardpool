"""
PriceCharting scraper — PSA 10 historical sold listings (3 年歷史)

關鍵發現：
  GET https://www.pricecharting.com/game/{set_slug}/{card_slug}?show=historicSales
  → HTML 內含 <tr id="ebay-{id}"> 的真實 eBay 成交紀錄
  → 含 date / price / title（grade 在 title 內判斷）
  → 純靜態 HTML，不需 JS。
  → 老卡（2021）有 3+ 年資料；新卡（2025）有 6-8 個月。

對外介面
  await get_pricecharting_history(set_id, card_number, db_path) -> list[dict]
  每筆：{listing_title, price_usd, price_twd, listing_url, sale_date,
         source='pricecharting', search_language='jp'}

set/card mapping 從 pricecharting_set_mapping / pricecharting_card_mapping 兩張表查。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

USD_TO_TWD = 31.5  # 概估
DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


# row 結構：<tr id="ebay-XXXX"> ... <td class="date">YYYY-MM-DD</td>
#                                  ... <td class="title"><a ...>{title}</a> [eBay]
#                                  ... <td class="numeric"><span class="js-price">${price}</span>
_ROW_RE = re.compile(r'<tr id="ebay-(\d+)"[^>]*>(.+?)</tr>', re.DOTALL)
_DATE_RE = re.compile(r'<td class="date">\s*(\d{4}-\d{2}-\d{2})')
_TITLE_RE = re.compile(r'<td class="title">[^<]*<a[^>]*href="([^"]+)"[^>]*>\s*([^<]+)', re.DOTALL)
_PRICE_RE = re.compile(r'<span class="js-price"[^>]*>\s*\$([\d,]+(?:\.\d+)?)')


def _is_psa10_title(title: str) -> bool:
    """判斷 listing title 是不是 PSA 10。
    排除 BGS 9.5 / Beckett 9.5 同時也含 PSA10 字樣的混合 grade。
    """
    if not title:
        return False
    t = title.upper()
    # 直接帶 PSA10 / PSA 10
    if not re.search(r'PSA\s*10\b', t):
        return False
    # 排除非 PSA 評分混入
    if re.search(r'(BGS|BECKETT|CGC|SGC)\s*(9\.5|10|MINT|GEM)', t):
        # 如果只有 PSA 10 就好；BGS 9.5 + PSA 10 就是同卡有兩家評鑑
        # 為了乾淨，只要混入 BGS/Beckett/CGC/SGC 通通排掉
        return False
    return True


def parse_pricecharting_html(html: str) -> list[dict]:
    """解析 historicSales 頁面 → list of {date, price_usd, title, url, listing_id, is_psa10}"""
    out = []
    for m in _ROW_RE.finditer(html):
        listing_id = m.group(1)
        block = m.group(2)
        d = _DATE_RE.search(block)
        if not d:
            continue
        sale_date = d.group(1)
        t = _TITLE_RE.search(block)
        if not t:
            continue
        url = (t.group(1) or "").replace("&amp;", "&")
        title = (t.group(2) or "").strip()
        p = _PRICE_RE.search(block)
        if not p:
            continue
        try:
            price_usd = float(p.group(1).replace(",", ""))
        except ValueError:
            continue
        out.append({
            "listing_id": listing_id,
            "sale_date": sale_date,
            "price_usd": price_usd,
            "listing_title": title,
            "listing_url": url,
            "is_psa10": _is_psa10_title(title),
        })
    return out


def _lookup_pc_url(set_id: str, card_number: str, db_path: Path = DB_PATH) -> Optional[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT pc_url FROM pricecharting_card_mapping WHERE set_id=? AND card_number=?",
            (set_id, str(card_number).strip()),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def fetch_html(client: httpx.AsyncClient, url: str) -> Optional[str]:
    try:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


async def get_pricecharting_history(
    set_id: str,
    card_number: str,
    db_path: Path = DB_PATH,
    client: Optional[httpx.AsyncClient] = None,
    psa10_only: bool = True,
) -> list[dict]:
    pc_url = _lookup_pc_url(set_id, card_number, db_path)
    if not pc_url:
        return []
    full = pc_url if pc_url.startswith("http") else f"https://www.pricecharting.com{pc_url}"
    if "?" in full:
        full = f"{full}&show=historicSales"
    else:
        full = f"{full}?show=historicSales"

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True)
    try:
        html = await fetch_html(client, full)
    finally:
        if own_client:
            await client.aclose()

    if not html:
        return []

    rows = parse_pricecharting_html(html)
    if psa10_only:
        rows = [r for r in rows if r["is_psa10"]]

    out = []
    for r in rows:
        usd = r["price_usd"]
        out.append({
            "listing_title": r["listing_title"],
            "price_jpy": None,
            "price_twd": round(usd * USD_TO_TWD, 2),
            "price_usd": usd,
            "listing_url": r["listing_url"] or full,
            "sale_date": r["sale_date"],
            "source": "pricecharting",
            "search_language": "jp",
            "_listing_id": r["listing_id"],
        })
    return out


async def main_test():
    # 自我測試 / sanity check
    test_cases = [
        ("jp-Inferno-X", "110"),               # MEGA Charizard X EX (2025)
        ("jp-25th-Anniversary-Collection", "1"),  # Pikachu 25th (2021)
    ]
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS, follow_redirects=True) as client:
        for set_id, card_no in test_cases:
            url = _lookup_pc_url(set_id, card_no)
            if not url:
                print(f"[skip] no pc_url for {set_id} #{card_no}")
                continue
            recs = await get_pricecharting_history(set_id, card_no, client=client)
            print(f"{set_id} #{card_no}: {len(recs)} PSA10 records")
            if recs:
                first = min(recs, key=lambda r: r["sale_date"])
                last = max(recs, key=lambda r: r["sale_date"])
                print(f"  range: {first['sale_date']} → {last['sale_date']}")
                print(f"  sample: ${last['price_usd']} on {last['sale_date']} — {last['listing_title'][:60]}")


if __name__ == "__main__":
    asyncio.run(main_test())
