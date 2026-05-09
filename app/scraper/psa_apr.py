"""
PSA APR (Auction Prices Realized) scraper

走法：
  1. 用 playwright + stealth 過 Cloudflare（~2 秒一次熱身）
  2. 對 PSA APR 搜尋頁 GET：
       https://www.psacard.com/auctionprices/search?q={query}
     抓 HTML 裡 /spec/psa/{specId} 連結 → spec_id 候選清單
  3. 對每個 spec_id 打公開 JSON API：
       https://www.psacard.com/api/psa/researchJourney/spec/{specId}/salesHistory?pn=1&ps={N}&g=&q=false&gt=ALL
     回 {sales: [...], totalCount}，每筆 sale 含
       saleDate (ISO), salePrice (USD), gradeValue (10=PSA10), listingURL, auctionHouse, certNumber

對映表
  psa_apr_card_mapping(set_id, card_number, spec_id) PRIMARY KEY (set_id, card_number)

整合到 card_prices(source='psa_apr')，listing_url 用 listingURL 或 spec/psa/{id}#{saleItemId}
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from playwright.sync_api import sync_playwright, BrowserContext, Page
from playwright_stealth import Stealth

USD_TO_TWD = 31.5
DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"

PSA_BASE = "https://www.psacard.com"
SEARCH_URL = f"{PSA_BASE}/auctionprices/search?q={{q}}"
API_URL = f"{PSA_BASE}/api/psa/researchJourney/spec/{{spec_id}}/salesHistory?pn=1&ps={{ps}}&g=&q=false&gt=ALL"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def ensure_tables():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS psa_apr_card_mapping (
            set_id TEXT NOT NULL,
            card_number TEXT NOT NULL,
            spec_id TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (set_id, card_number)
        );
        CREATE INDEX IF NOT EXISTS idx_psa_spec ON psa_apr_card_mapping(spec_id);
    """)
    conn.commit()
    conn.close()


# =========================================================
#   Browser-managed PSA session（過 Cloudflare 一次取 cookies）
# =========================================================

class PSASession:
    """共用一個 stealth browser session，cookie 持有 cf_clearance 後可發 N 次 request"""

    def __init__(self):
        self._stealth_cm = None
        self._pw = None
        self._browser = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def open(self):
        self._stealth_cm = Stealth().use_sync(sync_playwright())
        self._pw = self._stealth_cm.__enter__()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._ctx = self._browser.new_context(
            user_agent=UA,
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        self._page = self._ctx.new_page()
        # 暖機 — 訪問主頁取 cf_clearance cookie
        self._page.goto(f"{PSA_BASE}/auctionprices", wait_until="domcontentloaded", timeout=30000)
        # 等 challenge 過
        for _ in range(30):
            self._page.wait_for_timeout(1000)
            if "Just a moment" not in self._page.title():
                break
        return self

    def close(self):
        try:
            if self._browser: self._browser.close()
        except Exception: pass
        try:
            if self._stealth_cm: self._stealth_cm.__exit__(None, None, None)
        except Exception: pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *args):
        self.close()

    # ---- 搜尋 ----
    SPEC_LINK_RE = re.compile(r'href="(/spec/psa/(\d+))"')

    def search_spec_ids(self, query: str) -> list[str]:
        """搜尋頁 → spec_id list（最多前 30 個）。
        必須用 page.goto 讓 JS 渲染搜尋結果（page.request.get 拿原始 HTML 沒結果）。
        """
        from urllib.parse import quote
        url = f"{PSA_BASE}/auctionprices/search?q={quote(query)}"
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # 等 challenge + JS 渲染
            for _ in range(15):
                self._page.wait_for_timeout(500)
                if "Just a moment" not in self._page.title():
                    break
            # 等搜尋結果出現
            try:
                self._page.wait_for_selector('a[href^="/spec/psa/"]', timeout=8000)
            except Exception:
                pass
            html = self._page.content()
        except Exception:
            return []
        seen: list[str] = []
        for m in self.SPEC_LINK_RE.finditer(html):
            sid = m.group(2)
            if sid not in seen:
                seen.append(sid)
        return seen

    # ---- salesHistory ----
    def get_sales_history(self, spec_id: str, page_size: int = 100) -> list[dict]:
        url = API_URL.format(spec_id=spec_id, ps=page_size)
        try:
            resp = self._page.request.get(url, timeout=20000)
            if resp.status != 200:
                return []
            data = resp.json()
        except Exception:
            return []
        return data.get("sales", []) if isinstance(data, dict) else []


# =========================================================
#   Search query 建立 — set + 卡號
# =========================================================

def build_search_query(set_name: str, card_name: str | None, card_number: str) -> str:
    """組搜尋字串。PSA APR 對 'Pokemon Japanese SetName CardName CardNumber' 通常能找到"""
    parts = []
    if not (set_name or "").lower().startswith("pokemon"):
        parts.append("Pokemon")
    if "japanese" not in (set_name or "").lower():
        parts.append("Japanese")
    if set_name:
        parts.append(set_name)
    if card_name:
        # 拿英文部分（去掉日文）
        en_part = re.sub(r"[぀-ゟ゠-ヿ]+", "", card_name).strip()
        if en_part:
            parts.append(en_part[:30])
    if card_number:
        parts.append(str(card_number).lstrip("0") or "0")
    return " ".join(parts)[:80]


# =========================================================
#   把 sales 轉成 card_prices row
# =========================================================

def sale_to_record(sale: dict, set_id: str, card_number: str) -> Optional[dict]:
    grade = sale.get("gradeValue")
    if grade != 10:
        return None
    price_usd = sale.get("salePrice")
    if not price_usd or float(price_usd) <= 0:
        return None
    sale_date_iso = sale.get("saleDate") or ""
    # ISO → YYYY-MM-DD
    sale_date = sale_date_iso[:10] if sale_date_iso else None
    sale_id = sale.get("saleItemId") or sale.get("certNumber") or ""
    listing_url = sale.get("listingURL") or f"{PSA_BASE}/spec/psa/{sale.get('specId')}#{sale_id}"
    auction_house = sale.get("auctionHouse") or "PSA"
    cert = sale.get("certNumber") or ""
    title = f"PSA 10 {auction_house} {sale_date} cert:{cert}"
    return {
        "listing_title": title,
        "price_usd": float(price_usd),
        "price_twd": round(float(price_usd) * USD_TO_TWD, 2),
        "listing_url": listing_url,
        "sale_date": sale_date,
        "source": "psa_apr",
        "search_language": "jp" if set_id.startswith("jp-") else "en",
        "_sale_id": sale_id,
    }


if __name__ == "__main__":
    # quick smoke test
    ensure_tables()
    with PSASession() as s:
        for q in [
            "Charizard Inferno X 110",
            "Pokemon Japanese Inferno X Charizard 110",
            "Inferno X Charizard 110",
            "Mega Charizard 110",
            "Pikachu Promo M-P 020",
        ]:
            spec_ids = s.search_spec_ids(q)
            print(f"\n[{q}] specs: {len(spec_ids)}")
            if spec_ids:
                print(f"  first 5: {spec_ids[:5]}")
                # 取 first 看 sales
                sales = s.get_sales_history(spec_ids[0], 100)
                psa10 = [x for x in sales if x.get("gradeValue") == 10]
                print(f"  spec {spec_ids[0]}: {len(sales)} sales, {len(psa10)} PSA10")
                if psa10:
                    print(f"  sample: {psa10[0].get('auctionHouse')} {psa10[0].get('saleDate')[:10]} ${psa10[0].get('salePrice')}")
