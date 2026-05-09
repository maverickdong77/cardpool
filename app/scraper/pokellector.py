"""
Pokellector 爬蟲
抓取日版和英版 Pokemon TCG 系列和卡片資料
"""

import re
import asyncio
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from datetime import datetime


# 日文和英文來源
BASE_URL_JP = "https://jp.pokellector.com"
BASE_URL_EN = "https://www.pokellector.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def get_base_url(language: str = "jp") -> str:
    """取得對應語言的 BASE_URL"""
    return BASE_URL_JP if language == "jp" else BASE_URL_EN


async def get_all_sets(language: str = "jp") -> list:
    """取得所有系列（支援 jp 或 en）"""
    sets = []
    base_url = get_base_url(language)

    async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
        try:
            response = await client.get(f"{base_url}/sets")
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # 找所有系列連結
            set_links = soup.select("a[href*='-Expansion']")

            for link in set_links:
                href = link.get("href", "")
                name = link.get_text(strip=True)

                # 提取系列 ID，加上語言前綴避免重複
                match = re.search(r'/([^/]+)-Expansion/?', href)
                raw_set_id = match.group(1) if match else None
                set_id = f"{language}-{raw_set_id}" if raw_set_id else None

                # 圖片
                img = link.find("img")
                logo_url = img.get("src") if img else None

                if set_id and name:
                    sets.append({
                        "id": set_id,
                        "name": name,
                        "url": f"{base_url}{href}" if href.startswith("/") else href,
                        "logo_url": logo_url,
                        "language": language,
                    })

        except Exception as e:
            print(f"取得系列列表失敗 ({language}): {e}")

    return sets


async def get_set_cards(set_url: str, language: str = "jp") -> list:
    """取得特定系列的所有卡片"""
    cards = []
    base_url = get_base_url(language)

    async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
        try:
            response = await client.get(set_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # 找所有卡片連結 (格式: /Set-Name-Expansion/Pokemon-Name-Card-Number)
            card_links = soup.select("a[href*='-Card-']")

            for link in card_links:
                try:
                    href = link.get("href", "")

                    # 從 URL 提取編號和名稱
                    # 格式: /Ninja-Spinner-Expansion/Weedle-Card-1
                    match = re.search(r'/([^/]+)-Card-(\d+)/?$', href)
                    if not match:
                        continue

                    card_name_raw = match.group(1)
                    card_number = match.group(2)

                    # 清理名稱 (把 - 換成空格)
                    card_name = card_name_raw.replace("-", " ")

                    # 卡片圖片
                    img = link.find("img")
                    image_url = None
                    if img:
                        image_url = img.get("src") or img.get("data-src")

                    # 避免重複
                    if any(c["number"] == card_number for c in cards):
                        continue

                    cards.append({
                        "number": card_number,
                        "name": card_name,
                        "image_url": image_url,
                        "url": f"{base_url}{href}" if href.startswith("/") else href,
                        "rarity": None,
                    })

                except Exception as e:
                    continue

            # 依編號排序
            cards.sort(key=lambda x: int(x["number"]) if x["number"] else 0)

        except Exception as e:
            print(f"取得卡片列表失敗: {e}")

    return cards


async def get_card_detail(card_url: str) -> Optional[dict]:
    """取得卡片詳細資訊"""
    async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
        try:
            response = await client.get(card_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            # 卡片名稱
            title = soup.select_one("h1, .card-name")
            name = title.get_text(strip=True) if title else None

            # 大圖
            img = soup.select_one(".card-image img, .main-image img")
            image_url = img.get("src") if img else None

            # 其他資訊
            info = {}
            info_rows = soup.select(".card-info tr, .info-row")
            for row in info_rows:
                cells = row.find_all(["td", "th", "span"])
                if len(cells) >= 2:
                    key = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    info[key] = value

            return {
                "name": name,
                "image_url": image_url,
                "info": info,
            }

        except Exception as e:
            print(f"取得卡片詳情失敗: {e}")
            return None


# 測試
if __name__ == "__main__":
    async def test():
        print("取得日版系列...")
        sets = await get_all_sets()
        print(f"找到 {len(sets)} 個系列")

        for s in sets[:10]:
            print(f"  - {s['name']} ({s['id']})")

        if sets:
            print(f"\n取得第一個系列的卡片: {sets[0]['name']}")
            cards = await get_set_cards(sets[0]['url'])
            print(f"找到 {len(cards)} 張卡片")
            for c in cards[:5]:
                print(f"  - {c['name']} #{c['number']}")

    asyncio.run(test())
