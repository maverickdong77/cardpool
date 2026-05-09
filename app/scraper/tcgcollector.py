"""
TCG Collector 爬蟲
取得完整的 Pokemon TCG 系列和卡片資料
"""

import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional
from playwright.sync_api import sync_playwright

_executor = ThreadPoolExecutor(max_workers=1)


def _scrape_sets_sync(language: str = "jp") -> list:
    """爬取系列列表"""
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.new_page()

            # TCG Collector 系列頁面
            url = f"https://www.tcgcollector.com/sets/{language}?cardCountMode=anyCardVariant&releaseDateOrder=newToOld&displayAs=images"
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # 等待系列卡片載入
            try:
                page.wait_for_selector(".set-card", timeout=15000)
            except Exception:
                browser.close()
                return []

            # 滾動載入更多
            for _ in range(3):
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(500)

            # 解析系列
            set_cards = page.query_selector_all(".set-card")

            for card in set_cards[:50]:  # 取前 50 個系列
                try:
                    # 系列名稱
                    name_elem = card.query_selector(".set-name, h3, .name")
                    if not name_elem:
                        continue
                    name = name_elem.inner_text().strip()

                    # 系列連結
                    link_elem = card.query_selector("a")
                    link = link_elem.get_attribute("href") if link_elem else None

                    # 從連結提取系列 ID
                    set_id = None
                    if link:
                        match = re.search(r'/cards/([^/?]+)', link)
                        if match:
                            set_id = match.group(1)

                    # 發售日期
                    date_elem = card.query_selector(".release-date, .date")
                    release_date = date_elem.inner_text().strip() if date_elem else None

                    # 圖片
                    img_elem = card.query_selector("img")
                    image_url = img_elem.get_attribute("src") if img_elem else None

                    results.append({
                        "id": set_id,
                        "name": name,
                        "release_date": release_date,
                        "image_url": image_url,
                        "language": language,
                        "url": f"https://www.tcgcollector.com{link}" if link else None,
                    })

                except Exception as e:
                    print(f"解析系列失敗: {e}")
                    continue

            browser.close()

    except Exception as e:
        print(f"TCG Collector 爬取失敗: {e}")

    return results


def _scrape_set_cards_sync(set_url: str) -> list:
    """爬取特定系列的所有卡片"""
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.new_page()

            page.goto(set_url, wait_until="domcontentloaded", timeout=30000)

            # 等待卡片載入
            try:
                page.wait_for_selector(".card-image, .card-item", timeout=15000)
            except Exception:
                browser.close()
                return []

            # 滾動載入所有卡片
            for _ in range(10):
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(300)

            # 解析卡片
            cards = page.query_selector_all(".card-image, .card-item, [class*='card']")

            for card in cards:
                try:
                    # 卡片圖片
                    img_elem = card.query_selector("img")
                    if not img_elem:
                        continue

                    image_url = img_elem.get_attribute("src") or img_elem.get_attribute("data-src")
                    alt_text = img_elem.get_attribute("alt") or ""

                    # 卡片連結
                    link_elem = card if card.tag_name == 'a' else card.query_selector("a")
                    card_url = link_elem.get_attribute("href") if link_elem else None

                    # 從連結或 alt 提取卡片編號
                    card_number = None
                    if card_url:
                        match = re.search(r'/(\d+)(?:\?|$)', card_url)
                        if match:
                            card_number = match.group(1)

                    results.append({
                        "number": card_number,
                        "name": alt_text,
                        "image_url": image_url,
                        "url": f"https://www.tcgcollector.com{card_url}" if card_url else None,
                    })

                except Exception as e:
                    continue

            browser.close()

    except Exception as e:
        print(f"爬取卡片失敗: {e}")

    return results


async def get_sets(language: str = "jp") -> list:
    """取得系列列表"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _scrape_sets_sync, language)


async def get_set_cards(set_url: str) -> list:
    """取得特定系列的所有卡片"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _scrape_set_cards_sync, set_url)


# 測試
if __name__ == "__main__":
    async def test():
        print("取得日文系列...")
        sets = await get_sets("jp")
        print(f"找到 {len(sets)} 個系列")
        for s in sets[:5]:
            print(f"  - {s['name']} ({s['id']})")

    asyncio.run(test())
