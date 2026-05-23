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
from playwright_stealth import Stealth

# 2026-05-21 Path B1：用 stealth lib 取代手動 add_init_script、繞 anti-bot fingerprint
_stealth = Stealth()

# 匯率
USD_TO_TWD = 32.0
TWD_TO_USD = 1 / USD_TO_TWD

# 共用的線程池 — 6 worker 支援 2 卡並行 × 3 query 各/卡
_executor = ThreadPoolExecutor(max_workers=6)

# language code → eBay Language filter value（對應 eBay sidebar 的 Language facet）
# 過濾「卡片本身」的印刷語言、不是 listing 標題語言
_EBAY_LANG_MAP = {
    "jp": "Japanese",
    "en": "English",
    "tw": "Chinese",
}

# eBay Pokémon Individual Cards 類別 ID（_dcat 參數）
_EBAY_POKEMON_CAT = "183454"

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


# 通用 stopwords：太短或太通用、不能單獨用來辨識
_NAME_STOPWORDS = {
    'the', 'and', 'pokemon', 'pokémon', 'card', 'cards', 'tcg', 'psa',
    'ex', 'gx', 'gmax', 'vmax', 'vstar', 'union',
    # 'V' 太短會被長度過濾掉
    # 2026-05-19：rarity tail 不參與 AND match
    # 賣家標題常省略稀有度（如 "Mega Charizard X ex #110 PSA 10" 沒寫 SAR）。
    # 卡號 N/T 強制 + JP token OR rule 仍能擋同卡號不同 set 的誤抓。
    # Dry-run 對 949/110 SAR：OLD=0 → NEW=419 通過、抽 10 筆 100% 同名同卡。
    'sar', 'sr', 'ur', 'ar', 'rr', 'hr', 'chr', 'ssr', 'tg', 'pr', 'tr',
}
# 全形→半形對應（jp 標題常見 ex/EX）
_FW_HALFW_MAP = str.maketrans('０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ',
                              '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')


def _significant_name_tokens(name: str):
    """從卡名抽出可拿來 match 的關鍵 token：
       - 拆分空格/連字號
       - 長度 ≥ 3
       - 排除 stopwords
       - 對 JP 字元保留整段（不拆字元）"""
    if not name:
        return []
    out = []
    # JP chunks（連續 Japanese chars）保留原樣
    for chunk in re.findall(r'[ぁ-ゟ゠-ヿ一-龯ーｱ-ﾝ々]+|[A-Za-z\']+|[０-９]+|\d+', name):
        chunk = chunk.translate(_FW_HALFW_MAP).strip("'")
        if len(chunk) >= 3 and chunk.lower() not in _NAME_STOPWORDS:
            out.append(chunk)
    return out


def _title_has_card_name_token(title: str, card_name_en: str, card_name_jp: str) -> bool:
    """標題必須含卡名關鍵 token。

    判斷規則（任一通過即可）：
      A. EN tokens 全部命中（避免 `Ball` 共用 → Master Ball 誤殺 Ultra Ball）
      B. JP tokens 任一命中（JP 字元為單一 chunk、直接 substring）

    用途：JP scraper 沒帶 set token 時、靠卡名鎖定正確卡片
    （避免「#138 + PSA 10 + Japanese」抓到任何 #138 卡）。
    """
    if not title:
        return False
    title_norm = title.translate(_FW_HALFW_MAP)
    tokens_en = _significant_name_tokens(card_name_en or '')
    tokens_jp = _significant_name_tokens(card_name_jp or '')
    if not tokens_en and not tokens_jp:
        return True  # 無 token 可比、不擋

    # A. EN：全部 token 命中
    if tokens_en:
        en_all = all(
            re.search(rf"\b{re.escape(tok)}\b", title_norm, re.IGNORECASE)
            for tok in tokens_en
        )
        if en_all:
            return True

    # B. JP：任一 token 命中（substring）
    for tok in tokens_jp:
        if tok in title or tok in title_norm:
            return True

    return False


_NON_POKEMON_KEYWORDS = re.compile(
    r"\b(basketball|football|baseball|soccer|NBA|NFL|MLB|panini|prizm|topps|donruss|"
    r"doncic|ohtani|lebron|jordan|bird|celtics|lakers)\b",
    re.IGNORECASE,
)


def _is_pokemon_listing(title: str) -> bool:
    """非 Pokemon 卡（運動 / 明星）反向擋。
    2026-05-18: 不再硬性要求標題含 "Pokemon" — 很多 PSA 10 JP 卡賣家標題只寫
    「PSA 10 Pikachu ex SAR 234/193 MEGA Dream ex M2a」、無 Pokemon 字眼但顯然是 Pokemon 卡。
    靠 _NON_POKEMON_KEYWORDS 反向擋掉 NBA/NFL/Panini 等運動卡即可。
    """
    if not title:
        return False
    if _NON_POKEMON_KEYWORDS.search(title):
        return False
    return True


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


# 2026-05-20: eBay 升級 sold-listings anti-bot — 純粹 homepage warm 不夠、會被踢去 signin。
# 深 warmup 多訪一個 Pokemon Trading Cards category 頁、建立 session 深度。
_EBAY_POKEMON_BROWSE_URL = "https://www.ebay.com/b/Pokemon-Individual-Trading-Cards/183454/bn_1842009"


def _warmup_ebay_session(page, deep: bool = False) -> None:
    """訪 homepage + 可選的 Pokemon 卡 category 頁、build session 深度後讓 cookies 成形。

    deep=True 多 +3-4s 但能繞過「單訪 homepage 直 jump sold URL」的 anti-bot signal。
    """
    try:
        page.goto("https://www.ebay.com/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(3000)
        if deep:
            page.goto(_EBAY_POKEMON_BROWSE_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)
    except Exception:
        pass


def _scrape_ebay_sync(url: str, cert_number: str = None, card_number: str = None,
                       set_name: str = None, verify_redirects: bool = True,
                       language: str = "", max_pages: int = 5,
                       card_name_en: str = None, card_name_jp: str = None) -> list:
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
        # 2026-05-21 Path B1：用 stealth lib 套用所有 anti-detection（含 navigator.webdriver、plugins、
        # WebGL、permissions API 等）取代原本手動的 add_init_script。
        _stealth.apply_stealth_sync(page)

        # 沒快取就 warm 一次（2026-05-20 改 deep warmup、訪 homepage + Pokemon category）
        if not cached or (now - cached_at) >= _COOKIE_TTL:
            _warmup_ebay_session(page, deep=True)
            try:
                _tls.ebay_cookies = context.cookies()
                _tls.ebay_cookies_at = now
            except Exception:
                pass

        # === 多頁分頁抓取 ===
        # 不同頁的 URL：第 1 頁用原 URL；第 2 頁起加 &_pgn=N
        seen_item_ids = set()  # 用 item_id 去重（多賣家標題完全相同、改 url 才能正確 dedup）
        for page_num in range(1, max_pages + 1):
            page_url = url if page_num == 1 else f"{url}&_pgn={page_num}"

            # 2026-05-20 升級：頁 1 加 retry-on-signin-redirect 邏輯。
            # eBay sold-listings 防爬蟲若觸發 → redirect 到 signin.ebay.com、
            # `.su-card-container` 永不出現、原邏輯 8s timeout 直接 return []。
            # 修法：偵測 signin redirect → 深 warmup 重新建立 session → 重試。
            page_attempts = 3 if page_num == 1 else 1
            page_ok = False
            for attempt in range(page_attempts):
                try:
                    page.goto(page_url, wait_until="domcontentloaded", timeout=15000)
                    # 給 redirect / JS 觸發空間
                    page.wait_for_timeout(2000)

                    if "signin.ebay.com" in page.url:
                        if attempt < page_attempts - 1:
                            # 重 warm + 重試（不 drop cookies、讓 session 自然加深）
                            _warmup_ebay_session(page, deep=True)
                            try:
                                _tls.ebay_cookies = context.cookies()
                                _tls.ebay_cookies_at = _time.time()
                            except Exception:
                                pass
                            continue
                        # 用完 retry：sold listings 完全擋下、回空
                        return []

                    # 2026-05-18: 拉到 8s — eBay 在 cookie 不完整時頁面渲染變慢、3s 會誤回 0
                    page.wait_for_selector(".su-card-container", timeout=8000)
                    # selector 第 1 個出現後、繼續等 page 把剩餘 items 渲染完（_ipg=240 需要時間）
                    page.wait_for_timeout(5000)
                    # 觸發 lazy-load：scroll 到底再 scroll 回去、確保所有 cards 渲染
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(500)
                    page_ok = True
                    break
                except Exception:
                    if page_num == 1 and attempt < page_attempts - 1:
                        # 頁 1 失敗也試 warm + retry
                        _warmup_ebay_session(page, deep=True)
                        try:
                            _tls.ebay_cookies = context.cookies()
                            _tls.ebay_cookies_at = _time.time()
                        except Exception:
                            pass
                        continue
                    if page_num == 1:
                        return []
                    break

            if not page_ok:
                break

            cards = page.query_selector_all(".su-card-container")
            if not cards:
                break

            page_added = 0
            # _ipg=240 後不限 30 — 全部處理
            for card in cards:
                try:
                    title_elem = card.query_selector(".s-card__title")
                    if not title_elem:
                        continue
                    title = title_elem.inner_text().strip()
                    # 去尾巴 accessibility 字串 "Opens in a new window or tab"（eBay anchor inner_text 包含此 sr-only 字眼）
                    title = re.sub(r"\s*Opens in a new window or tab\s*$", "", title, flags=re.IGNORECASE).strip()
                    if "Shop on eBay" in title:
                        continue

                    # 提前取 item_id 做 dedup（避免多賣家相同標題被誤殺）
                    _link_pre = card.query_selector(".s-card__link")
                    _link_pre_href = _link_pre.get_attribute("href") if _link_pre else None
                    _item_id_pre = _extract_item_id(_link_pre_href)
                    if _item_id_pre and _item_id_pre in seen_item_ids:
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
                    # JP 卡跳過 — query 已無 set token、且日文標題 set 名變化大、易誤殺
                    if set_name and language != "jp" and not _title_has_set_token(title, set_name, language):
                        continue

                    # 卡名 token 過濾（JP 沒 set 過濾、靠卡名鎖定）：
                    # 標題必須含 card_name_en 或 card_name_jp 任一個關鍵 token、
                    # 否則只憑 card_number 會誤入其他 set 同號的卡
                    if (card_name_en or card_name_jp) and not _title_has_card_name_token(
                        title, card_name_en, card_name_jp
                    ):
                        continue

                    # PSA 10 必須在標題（嚴格：排除 PSA 1~9、PSA 100+ 等誤命中）
                    import re as _re_psa
                    # 2026-05-19：normalize 把 "PSA-10" / "PSA  10" / "PSA10" 都換成 "PSA 10"
                    # 之前只 normalize 空白、漏 dash 變體
                    t_norm = _re_psa.sub(r"PSA[\s\-]*", "PSA ", title.upper())
                    if not _re_psa.search(r"\bPSA\s+10\b(?!\d|\.\d)", t_norm):
                        continue
                    # 額外擋雙重評級裡的 PSA 9/8/7 等
                    if _re_psa.search(r"\bPSA\s+[1-9]\b(?!\d)", t_norm):
                        continue
                    # 多卡 lot 拍賣
                    if _re_psa.search(r"\bPSA\s+10S\b|\bLOT\s+OF\b|\b2\s+GRADED\b", t_norm):
                        continue
                    # 擋其他評級機構（容忍 "ACE Grade 10" 等中間插 GRADE/GRADING 字眼）
                    if _re_psa.search(
                        r"\b(CGC|BGS|BECKETT|HGA|GMA|TAG|SGC|ACE|ARS|CSG|MNT|SBC|EGS)"
                        r"(?:\s+GRAD(?:E|ING))?\s*\d",
                        t_norm,
                    ):
                        continue
                    # 擋「PRISTINE 10」等非 PSA 用語
                    if _re_psa.search(r"\bPRISTINE\s*10\b", t_norm):
                        continue

                    # 語言版本檢查（jp 卡只收日版、en 卡只收英版、韓中版兩邊都不收）
                    if not _passes_lang_filter(title, language):
                        continue
                    if _item_id_pre:
                        seen_item_ids.add(_item_id_pre)

                    price_elem = card.query_selector(".s-card__price")
                    if not price_elem:
                        continue
                    price_text = price_elem.inner_text()
                    price_usd, price_twd = _parse_price(price_text)
                    if price_usd is None:
                        continue

                    # link 已在 dedup 階段取到、不必再 query
                    link = _link_pre_href
                    item_id = _item_id_pre

                    # blocklist 過濾（在 verify 前先擋已知壞 item）
                    if item_id and item_id in blocklist:
                        continue
                    # 正規化 listing_url：剝 query params（_skw/epid/itmmeta 等變動值）
                    if item_id:
                        link = f"https://www.ebay.com/itm/{item_id}"

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
                                   verify_redirects: bool = True,
                                   max_pages: int = 5,
                                   set_code_en: str = None,
                                   set_name_en: str = None,
                                   release_year: int = None,
                                   rarity_full: str = None) -> list:
        """用卡片名稱搜尋。

        2026-05-22 v2 (user PSA-label 規格)：
          `{year} POKEMON JAPANESE {set_code_en} {set_name_en} {rarity_full} {card_name UPPER} PSA {grade}`
        例：`2026 POKEMON JAPANESE M4 Ninja Spinner SPECIAL ART RARE MEGA GRENINJA EX PSA 10`
        - 拿掉 `#{num}`（user 規格無、且賣家標題慣例 #num 多放在 set name 後或省略）
        - set_code 與 set_name 用空格分（用 `-` 會 trigger splashui、但 eBay search hyphen/space 都 match）
        - 卡名 UPPER（賣家標題慣例）
        - 加 rarity_full（SPECIAL ART RARE 等、recall +73% on 953/114 spot-check）

        缺 set_code_en / set_name_en / release_year / rarity_full 任一時自動跳過、不會 break。
        拿掉 Query B（日文名 query）— 新 query 已用「POKEMON JAPANESE」target JP listings。
        """
        def _build_url(name: str) -> str:
            # 2026-05-22 v2: PSA label query format
            parts = []
            if release_year:
                parts.append(str(release_year))
            parts.append("POKEMON JAPANESE")
            if set_code_en:
                parts.append(set_code_en)
            if set_name_en:
                parts.append(set_name_en)
            if rarity_full:
                parts.append(rarity_full)
            if name:
                parts.append(name.upper())
            parts.append(f"PSA {grade}")
            q = " ".join(p for p in parts if p)

            # 2026-05-22 ablation 第一輪：拿掉 _sop=13（_in_kw + _ipg + _sop 三個合一起會 trigger splashui）
            # 2026-05-22 ablation 第二輪（user 觀察「引號 + POKEMON 不重要」反饋）：
            #   - 新 query 含「POKEMON」是 trust signal、不能拿
            #   - 拿掉 _in_kw=4：UI 顯示沒引號（不再 phrase exact match）、實測 listings 55 → 260（+4.7x）
            #   - CLAUDE.md 舊 query 寫「_in_kw=4 recall +12x」、但新 PSA-label query 反向（query 已含足夠 token）
            extra = [
                "LH_Sold=1",
                "LH_Complete=1",
                "_ipg=240",
            ]
            return f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(q)}&" + "&".join(extra)

        loop = asyncio.get_event_loop()

        # 2026-05-22: 只跑一條 Query A（PSA-label query format）、拿掉 JP name Query B
        url = _build_url(card_name)
        # 同樣把 card_number=None 傳給 _scrape_ebay_sync（post-filter 不要強制 #num token match、
        # query 已沒含 #num、保留 card_number filter 會排除掉很多 valid listings）
        results = await loop.run_in_executor(
            _executor, _scrape_ebay_sync, url, None, card_number, set_name, verify_redirects, language, max_pages, card_name, card_name_jp
        )
        return results

    async def close(self):
        """關閉（保留介面相容性）"""
        pass


async def get_ebay_prices(query: str, is_cert: bool = False, grade: str = "10",
                           card_number: str = None, set_name: str = None,
                           language: str = "",
                           card_name_jp: str = None,
                           verify_redirects: bool = None,
                           full_history: bool = False,
                           set_code_en: str = None,
                           set_name_en: str = None,
                           release_year: int = None,
                           rarity_full: str = None) -> list:
    """取得 eBay 價格（便捷函數）。

    language: 透過 eBay `Language` filter 過濾卡片印刷語言（jp/en/tw、見 _EBAY_LANG_MAP）；
    若同時提供 card_name_jp 會多跑一條日文名 query 合併結果（去重）。

    verify_redirects: None 用 default(True)。批次 sync 時可傳 False 跳過 navigate verify
                       （加快 ~2s/筆，靠 blocklist + revalidator 後續補驗）。
                       環境變數 CARDPOOL_EBAY_SKIP_VERIFY=1 可全域關閉。
    full_history: True 時 max_pages=5（_ipg=240 配下 = 1200 筆上限、足夠）；
                  False 時 max_pages=2（即時 sync = 480 筆）
    """
    import os as _os
    if verify_redirects is None:
        verify_redirects = _os.getenv("CARDPOOL_EBAY_SKIP_VERIFY") != "1"

    # _ipg=240 大幅提高單頁筆數、相應減少 page 數
    max_pages = 5 if full_history else 2

    scraper = EbayScraper()
    try:
        if is_cert:
            return await scraper.search_by_psa_cert(query)
        else:
            return await scraper.search_by_card_name(
                query, grade, card_number, set_name, language, card_name_jp,
                verify_redirects=verify_redirects,
                max_pages=max_pages,
                set_code_en=set_code_en,
                set_name_en=set_name_en,
                release_year=release_year,
                rarity_full=rarity_full,
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
