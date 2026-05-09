"""
Pokemon TCG 官方卡片圖片爬蟲
使用 Pokemon TCG API (pokemontcg.io) 獲取高品質卡片圖片
"""

import httpx
from typing import Optional
import asyncio

# Pokemon TCG API (免費，不需要 API Key)
API_BASE = "https://api.pokemontcg.io/v2"


async def search_pokemon_card(name: str, limit: int = 10) -> list:
    """
    搜尋寶可夢卡片，回傳官方圖片
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # 搜尋卡片
            params = {
                "q": f'name:"{name}"',
                "pageSize": limit,
                "orderBy": "-set.releaseDate",  # 最新的優先
            }

            response = await client.get(f"{API_BASE}/cards", params=params)
            response.raise_for_status()
            data = response.json()

            cards = []
            for card in data.get("data", []):
                cards.append({
                    "id": card.get("id"),
                    "name": card.get("name"),
                    "set_name": card.get("set", {}).get("name"),
                    "set_id": card.get("set", {}).get("id"),
                    "number": card.get("number"),
                    "rarity": card.get("rarity"),
                    "image_small": card.get("images", {}).get("small"),
                    "image_large": card.get("images", {}).get("large"),
                    "artist": card.get("artist"),
                    "types": card.get("types", []),
                    "hp": card.get("hp"),
                })

            return cards

        except Exception as e:
            print(f"Pokemon TCG API 錯誤: {e}")
            return []


async def get_card_image(card_name: str) -> Optional[str]:
    """
    取得單張卡片的官方圖片 URL
    """
    cards = await search_pokemon_card(card_name, limit=1)
    if cards:
        return cards[0].get("image_large") or cards[0].get("image_small")
    return None


async def get_card_images_batch(card_names: list) -> dict:
    """
    批次取得多張卡片的圖片
    """
    result = {}
    for name in card_names:
        image = await get_card_image(name)
        if image:
            result[name] = image
    return result


# 測試
if __name__ == "__main__":
    async def test():
        print("搜尋 Pikachu 卡片...")
        cards = await search_pokemon_card("pikachu", limit=5)

        for card in cards:
            print(f"\n{card['name']} ({card['set_name']})")
            print(f"  稀有度: {card['rarity']}")
            print(f"  圖片: {card['image_small']}")

    asyncio.run(test())
