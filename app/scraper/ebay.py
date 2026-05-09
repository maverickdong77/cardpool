import re
import asyncio
import sqlite3
import threading
import time as _time
from pathlib import Path
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional
from app.scraper.browser_pool import get_browser

# 匯率
USD_TO_TWD = 32.0
TWD_TO_USD = 1 / USD_TO_TWD

# 共用的線程池 — 6 worker 支援 2 卡並行 × 3 query 各/卡
_executor = ThreadPoolExecutor(max_workers=6)

# 每個線程快取 eBay cookies，避免每次搜尋都跑 homepage warmup (1.5s × N)
_tls = threading.local()
_COOKIE_TTL = 30 * 60  # 30 分鐘後重新 warm

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"


def _ensure_blocklist():
    """確保 ebay_blocklist 表存在（避免 sqlite missing-table）"""
    try:
        with sqlite3.connect(str(_DB_PATH)) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS ebay_blocklist (
                item_id TEXT PRIMARY KEY,
                reason TEXT,
                detected_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""")
    except Exception:
        pass


def _get_blocklist() -> set:
    """讀取 ebay_blocklist 中所有 item_id"""
    _ensure_blocklist()
    try:
        with sqlite3.connect(str(_DB_PATH)) as c:
            return {r[0] for r in c.execute("SELECT item_id FROM ebay_blocklist")}
    except Exception:
        return set()


def _add_to_blocklist(item_id: str, reason: str, detected_url: str = ""):
    """新增 item_id 到 blocklist（已存在則覆蓋 reason）"""
    _ensure_blocklist()
    try:
        with sqlite3.connect(str(_DB_PATH)) as c:
            c.execute(
                "INSERT OR REPLACE INTO ebay_blocklist (item_id, reason, detected_url) VALUES (?, ?, ?)",
                (item_id, reason, detected_url),
            )
            c.commit()
    except Exception:
        pass


def _extract_item_id(url: str) -> Optional[str]:
    """從 eBay URL 取出 /itm/{id} 的數字"""
    if not url:
        return None
    m = re.search(r"/itm/(\d+)", url)
    return m.group(1) if m else None


def _title_matches_card_number(title: str, card_number: str) -> bool:
    r"""檢查標題是否含此卡號。

    策略：
    - 如果 title 有任何 `\d+/\d+` 格式（卡號 N/T 表示法），
      其中「分子」必須等於 N —— 否則認定是不同卡片。
    - 如果 title 完全沒有 N/T 格式，才退回看 `#N` / `No.N` 等寬鬆寫法。
    """
    if not card_number:
        return True
    nums = re.findall(r"\d+", card_number)
    if not nums:
        return False
    n = int(nums[0])
    if n == 0:
        return False

    slash_pairs = re.findall(r"\b(\d+)\s*/\s*(\d+)\b", title)
    if slash_pairs:
        # 只要有任一對的「分子」等於 n（考慮零填充）即命中
        return any(int(a) == n for a, _ in slash_pairs)

    # 無 N/T 格式才用 #N 等退路
    patterns = [
        rf"(?:^|[\s\-\(\[])#\s*0*{n}(?!\d)",
        rf"\b0*{n}\s+of\s+\d+(?!\d)",
        rf"\bNo\.?\s*0*{n}(?!\d)",
    ]
    return any(re.search(p, title, re.IGNORECASE) for p in patterns)


def _title_has_set_token(title: str, set_name: str, language: str = "") -> bool:
    """標題必須含 set 名稱的關鍵字。

    - en 卡：嚴格，所有 >=3 字元 token 都要在標題（避免 Black Bolt 只命中 Black）
    - jp 卡：寬鬆，至少 1 個 token 命中即可（JP 賣家常用 set 代號 M3/M2a 不寫完整名）
    """
    if not set_name:
        return True
    tokens = [t for t in _clean_set_id(set_name).split() if len(t) >= 3]
    if not tokens:
        return True
    matches = [re.search(rf"\b{re.escape(t)}\b", title, re.IGNORECASE) for t in tokens]
    if language == "jp":
        return any(matches)
    return all(matches)


_NON_POKEMON_KEYWORDS = re.compile(
    r"\b(basketball|football|baseball|soccer|NBA|NFL|MLB|panini|prizm|topps|donruss|"
    r"doncic|ohtani|lebron|jordan|bird|celtics|lakers)\b",
    re.IGNORECASE,
)


def _is_pokemon_listing(title: str) -> bool:
    """標題必須含 Pokemon 字樣且不能是其他運動/明星卡。"""
    if not title:
        return False
    if _NON_POKEMON_KEYWORDS.search(title):
        return False
    return bool(re.search(r"pok[eé]mon|ポケモン|寶可夢", title, re.IGNORECASE))


_LANG_OTHER_REGION = re.compile(
    r"\bkorea(n)?\b|韓[國語文]|"
    r"\bchinese\b|\bs[\s\-]?chinese\b|\bt[\s\-]?chinese\b|繁體|簡體|中文版|傳統中文|簡體中文|"
    r"\bindonesian?\b|\bspanish\b|\bgerman\b|\bfrench\b|\bitalian\b|\bportuguese\b|\bdutch\b|\brussian\b",
    re.IGNORECASE,
)
_LANG_JAPANESE = re.compile(r"\b(japan(ese)?|jpn|jap)\b", re.IGNORECASE)
_JP_CHARS = re.compile(r"[぀-ゟ゠-ヿ]")  # 平假名 / 片假名


def _passes_lang_filter(title: str, language: str = "") -> bool:
    """eBay 標題語言檢查：

    - 其他語版（韓/中/印/德/法/西等）：兩邊都不收
    - en 卡：標題含 JP 標記就擋掉（避免 JP 版誤入英版）
    - jp 卡：寬鬆（eBay 搜尋已加 Japanese 過濾一輪，且很多日版標題沒寫 Japanese）
    """
    if not title:
        return False
    if _LANG_OTHER_REGION.search(title):
        return False
    if language == "en":
        if _LANG_JAPANESE.search(title) or _JP_CHARS.search(title):
            return False
    return True


def _scrape_ebay_sync(url: str, cert_number: str = None, card_number: str = None,
                       set_name: str = None, verify_redirects: bool = True,
                       language: str = "", max_pages: int = 5) -> list:
    """同步爬取 eBay（在線程中執行，重用瀏覽器）。

    verify_redirects=True：scrape 完後 navigate 到每個 /itm/ URL 看 final URL，
    如果 redirect 到 /p/ catalog 頁面（賣出後合併進產品目錄，常含 CGC/BGS 而非 PSA）
    → 跳過該筆並寫入 ebay_blocklist。對單卡互動式 sync 加 ~2s/筆但能擋掉誤判。
    批次同步可關閉以加速。

    language="jp" / "en"：擋掉韓/中版以及不對版的卡（jp 卡只收日版、en 卡只收英版）
    max_pages：抓多少頁（每頁 30 筆）；用來取得更長時間範圍的歷史紀錄
    """
    results = []
    context = None
    blocklist = _get_blocklist()

    try:
        browser = get_browser()
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="en-US",
        )

        # 注入快取的 eBay cookies (30 分鐘有效)，跳過 homepage warmup
        cached = getattr(_tls, "ebay_cookies", None)
        cached_at = getattr(_tls, "ebay_cookies_at", 0)
        now = _time.time()
        if cached and (now - cached_at) < _COOKIE_TTL:
            try:
                context.add_cookies(cached)
            except Exception:
                cached = None  # cookies 失效就重 warm

        page = context.new_page()
        # 隱藏 webdriver flag, 否則 eBay 直接 Access Denied
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # 沒快取就 warm 一次 homepage
        if not cached or (now - cached_at) >= _COOKIE_TTL:
            try:
                page.goto("https://www.ebay.com/", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                _tls.ebay_cookies = context.cookies()
                _tls.ebay_cookies_at = now
            except Exception:
                pass

        # === 多頁分頁抓取 ===
        # 不同頁的 URL：第 1 頁用原 URL；第 2 頁起加 &_pgn=N
        seen_titles = set()
        for page_num in range(1, max_pages + 1):
            page_url = url if page_num == 1 else f"{url}&_pgn={page_num}"
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_selector(".su-card-container", timeout=8000)
            except Exception:
                # 第 1 頁失敗 → 整個查詢無結果；後續頁失敗 → 提前結束
                if page_num == 1:
                    return []
                break

            cards = page.query_selector_all(".su-card-container")
            if not cards:
                break

            page_added = 0
            for card in cards[:30]:
                try:
                    title_elem = card.query_selector(".s-card__title")
                    if not title_elem:
                        continue
                    title = title_elem.inner_text().strip()
                    if "Shop on eBay" in title or title in seen_titles:
                        continue

                    if cert_number and cert_number not in title:
                        continue

                    # 必須是 Pokemon 卡（擋掉籃球/棒球/Panini 等）
                    if not _is_pokemon_listing(title):
                        continue

                    # 強制過濾：標題必須含正確卡號
                    if card_number and not _title_matches_card_number(title, card_number):
                        continue

                    # 收緊：標題必須含系列名稱全部主要 token
                    if set_name and not _title_has_set_token(title, set_name, language):
                        continue

                    # PSA 10 必須在標題（嚴格：排除 PSA 1~9、PSA 100+ 等誤命中）
                    import re as _re_psa
                    t_norm = _re_psa.sub(r"PSA\s*", "PSA ", title.upper())
                    if not _re_psa.search(r"\bPSA\s+10\b(?!\d|\.\d)", t_norm):
                        continue
                    # 額外擋雙重評級裡的 PSA 9/8/7 等
                    if _re_psa.search(r"\bPSA\s+[1-9]\b(?!\d)", t_norm):
                        continue
                    # 多卡 lot 拍賣
                    if _re_psa.search(r"\bPSA\s+10S\b|\bLOT\s+OF\b|\b2\s+GRADED\b", t_norm):
                        continue
                    # 擋其他評級機構
                    if _re_psa.search(
                        r"\b(CGC|BGS|BECKETT|HGA|GMA|TAG|SGC|ACE|ARS|CSG|MNT|SBC|EGS)\s*\d",
                        t_norm,
                    ):
                        continue
                    # 擋「PRISTINE 10」等非 PSA 用語
                    if _re_psa.search(r"\bPRISTINE\s*10\b", t_norm):
                        continue

                    # 語言版本檢查（jp 卡只收日版、en 卡只收英版、韓中版兩邊都不收）
                    if not _passes_lang_filter(title, language):
                        continue
                    seen_titles.add(title)

                    price_elem = card.query_selector(".s-card__price")
                    if not price_elem:
                        continue
                    price_text = price_elem.inner_text()
                    price_usd, price_twd = _parse_price(price_text)
                    if price_usd is None:
                        continue

                    link_elem = card.query_selector(".s-card__link")
                    link = link_elem.get_attribute("href") if link_elem else None

                    # blocklist 過濾（在 verify 前先擋已知壞 item）
                    item_id = _extract_item_id(link)
                    if item_id and item_id in blocklist:
                        continue

                    image_url = None
                    img_elem = card.query_selector("img")
                    if img_elem:
                        image_url = page.evaluate("(el) => el.src", img_elem)
                        if image_url and "ebaystatic.com" in image_url:
                            image_url = None

                    caption_elem = card.query_selector(".s-card__caption")
                    caption = caption_elem.inner_text() if caption_elem else ""
                    subtitle_elem = card.query_selector(".s-card__subtitle")
                    subtitle = subtitle_elem.inner_text() if subtitle_elem else ""
                    sale_date = _parse_date(caption) or _parse_date(subtitle)

                    results.append({
                        "listing_title": title,
                        "price_usd": price_usd,
                        "price_twd": price_twd,
                        "listing_url": link,
                        "image_url": image_url,
                        "sale_date": sale_date.isoformat() if sale_date else None,
                        "source": "ebay",
                    })
                    page_added += 1

                except Exception as e:
                    print(f"解析 eBay 項目失敗: {e}")
                    continue

            # 此頁完全沒有命中 → 後續頁通常也不會有，提前結束
            if page_added == 0 and page_num > 1:
                break

        # === 二階段驗證：對每個 /itm/ navigate 看 final URL，擋下 redirect 到 /p/ 的 ===
        if verify_redirects and results:
            verify_page = context.new_page()
            verify_page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            verified = []
            for r in results:
                item_id = _extract_item_id(r.get("listing_url"))
                if not item_id:
                    verified.append(r)
                    continue
                try:
                    # 用最簡 URL（去掉冗長 query string）以加快 navigate
                    verify_page.goto(
                        f"https://www.ebay.com/itm/{item_id}",
                        wait_until="domcontentloaded", timeout=12000,
                    )
                    verify_page.wait_for_timeout(500)
                    final = verify_page.url
                    if "/p/" in final:
                        # listing 已下架，被合併到產品目錄頁；擋下並記入 blocklist
                        _add_to_blocklist(
                            item_id,
                            "auto: redirects to /p/ catalog (mixed grade)",
                            final,
                        )
                        print(f"[ebay verify] 擋下 {item_id} → {final}")
                        continue
                    verified.append(r)
                except Exception as e:
                    # 驗證失敗（timeout / Access Denied）→ 保留（避免誤殺正常 listing）
                    print(f"[ebay verify] {item_id} verify fail，保留: {e}")
                    verified.append(r)
            try:
                verify_page.close()
            except Exception:
                pass
            results = verified

    except Exception as e:
        print(f"eBay 搜尋失敗: {e}")

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass

    return results


def _parse_price(price_text: str) -> tuple[Optional[float], Optional[float]]:
    """解析價格文字，回傳 (USD, TWD)"""
    if not price_text:
        return None, None

    price_text = price_text.strip()

    # 處理 NT$ 格式
    nt_match = re.search(r"NT\$?\s*([\d,]+)", price_text)
    if nt_match:
        price_twd = float(nt_match.group(1).replace(",", ""))
        price_usd = round(price_twd * TWD_TO_USD, 2)
        return price_usd, price_twd

    # 處理 USD 格式
    usd_match = re.search(r"(?:US\s*)?\$\s*([\d,]+\.?\d*)", price_text)
    if usd_match:
        price_usd = float(usd_match.group(1).replace(",", ""))
        price_twd = round(price_usd * USD_TO_TWD, 0)
        return price_usd, price_twd

    return None, None


def _parse_date(date_text: str) -> Optional[datetime]:
    """解析日期文字。回 None 讓上層改用 created_at（DB 的實際抓取時間）。"""
    if not date_text:
        return None

    try:
        match = re.search(r"Sold\s+(\w+\s+\d+,?\s*\d*)", date_text)
        if match:
            date_str = match.group(1)
            for fmt in ["%b %d, %Y", "%b %d %Y", "%b %d"]:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    if parsed.year == 1900:
                        parsed = parsed.replace(year=datetime.now().year)
                    return parsed
                except ValueError:
                    continue
    except Exception:
        pass

    return None


def _clean_set_id(set_id: str) -> str:
    """剝語言前綴、連字號換空格，e.g. en-Ancient-Origins -> Ancient Origins"""
    if not set_id:
        return ""
    sid = re.sub(r"^(en|jp|zh|cn)-", "", set_id)
    return sid.replace("-", " ").strip()


class EbayScraper:
    """eBay 已成交價格爬蟲"""

    async def search_by_psa_cert(self, cert_number: str) -> list:
        """用 PSA 認證編號搜尋"""
        url = f"https://www.ebay.com/sch/i.html?_nkw=PSA+{cert_number}&LH_Sold=1&LH_Complete=1&_sop=13"
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _scrape_ebay_sync, url, cert_number, None, None)

    async def search_by_card_name(self, card_name: str, grade: str = "10",
                                   card_number: str = None, set_name: str = None,
                                   language: str = "",
                                   card_name_jp: str = None,
                                   verify_redirects: bool = True) -> list:
        """用卡片名稱搜尋。

        組合順序固定：名稱 → 卡號 → 卡盒系列 → PSA 等級。
        （對應用戶指定的顯示格式 `名稱 [卡號] (系列)` ——
         eBay 搜尋不吃括號，所以 URL 只用關鍵字串接，括號僅保留於日誌/UI。）

        language: "jp" 時附加 "Japanese"，避免抓到英文版價格。
        card_name_jp: 日卡時若有日文名，會額外跑一條「日文名+卡號+PSA10」query
                      合併去重（catches 日文標題的賣家）。
        """
        def _build_url(name: str, append_jp_tag: bool) -> str:
            parts = [name]
            if card_number:
                parts.append(str(card_number))
            if set_name:
                parts.append(_clean_set_id(set_name))
            parts.append(f"PSA {grade}")
            if append_jp_tag:
                parts.append("Japanese")
            q = " ".join(p for p in parts if p)
            return f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q)}&LH_Sold=1&LH_Complete=1&_sop=13"

        loop = asyncio.get_event_loop()

        # Query A: 英文名 + ("Japanese" if jp)
        url_a = _build_url(card_name, language == "jp")
        task_a = loop.run_in_executor(
            _executor, _scrape_ebay_sync, url_a, None, card_number, set_name, verify_redirects, language, 5
        )

        # Query B: 日文名（僅 JP 卡且有日文名時）— 不加 Japanese tag（日文名本身就是 JP signal）
        if language == "jp" and card_name_jp and card_name_jp.strip():
            url_b = _build_url(card_name_jp, False)
            task_b = loop.run_in_executor(
                _executor, _scrape_ebay_sync, url_b, None, card_number, None, verify_redirects, language, 5
            )
            results_a, results_b = await asyncio.gather(task_a, task_b)
            # 去重：以 listing_url 為 key
            seen = set()
            merged = []
            for r in (results_a + results_b):
                key = r.get("listing_url") or r.get("listing_title")
                if key and key not in seen:
                    seen.add(key)
                    merged.append(r)
            return merged

        return await task_a

    async def close(self):
        """關閉（保留介面相容性）"""
        pass


async def get_ebay_prices(query: str, is_cert: bool = False, grade: str = "10",
                           card_number: str = None, set_name: str = None,
                           language: str = "",
                           card_name_jp: str = None,
                           verify_redirects: bool = None) -> list:
    """取得 eBay 價格（便捷函數）。

    language="jp" 時會在搜尋字串加 Japanese；
    若同時提供 card_name_jp 會多跑一條日文名 query 合併結果（去重）。

    verify_redirects: None 用 default(True)。批次 sync 時可傳 False 跳過 navigate verify
                       （加快 ~2s/筆，靠 blocklist + revalidator 後續補驗）。
                       環境變數 CARDPOOL_EBAY_SKIP_VERIFY=1 可全域關閉。
    """
    import os as _os
    if verify_redirects is None:
        verify_redirects = _os.getenv("CARDPOOL_EBAY_SKIP_VERIFY") != "1"

    scraper = EbayScraper()
    try:
        if is_cert:
            return await scraper.search_by_psa_cert(query)
        else:
            return await scraper.search_by_card_name(
                query, grade, card_number, set_name, language, card_name_jp,
                verify_redirects=verify_redirects,
            )
    finally:
        await scraper.close()


# 測試用
if __name__ == "__main__":
    async def test():
        print("Testing eBay scraper...")
        print("=" * 60)

        results = await get_ebay_prices("pikachu", is_cert=False)
        print(f"\nFound {len(results)} results for 'pikachu':\n")

        for i, r in enumerate(results[:5], 1):
            print(f"{i}. {r['listing_title'][:60]}...")
            print(f"   USD: ${r['price_usd']:.2f} / TWD: NT${r['price_twd']:,.0f}")
            print()

    asyncio.run(test())
