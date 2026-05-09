"""pokemontcg.io API — 免費公開的 TCGplayer + Cardmarket 價格

替代 Collectr（其 API 是 paid + iOS app only）。pokemontcg.io 免費、無 auth、
提供 TCGplayer (US) 和 Cardmarket (EU) 即時市場價，正好補我們缺的 EN 卡片價格。

使用：
  GET https://api.pokemontcg.io/v2/cards?q=set.id:base1+name:charizard

回應結構（精簡）：
  {data: [{
    id: 'base1-4',
    set: {id, name, releaseDate, ...},
    number: '4', name: 'Charizard', rarity: 'Rare Holo',
    tcgplayer: {prices: {holofoil: {low, mid, high, market, directLow}, ...}, updatedAt},
    cardmarket: {prices: {averageSellPrice, lowPrice, trendPrice, avg1, avg7, avg30}, updatedAt},
  }]}

可塞進 card_prices(source='tcgplayer' | 'cardmarket')。
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"
USD_TO_TWD = 31.5
EUR_TO_TWD = 34.0
HEADERS = {"User-Agent": "cardpool/1.0", "Accept": "application/json"}


async def fetch_card(client: httpx.AsyncClient, set_id: str, number: str) -> Optional[dict]:
    """用 set.id + number 查 — 適合我們本身有對映 set_id 時"""
    url = "https://api.pokemontcg.io/v2/cards"
    params = {"q": f"set.id:{set_id} number:{number}", "pageSize": 1}
    try:
        r = await client.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        cards = data.get("data") or []
        return cards[0] if cards else None
    except Exception:
        return None


def card_to_records(card: dict) -> list[dict]:
    """從一張卡的 API 結果取出價格，組成 card_prices row 格式"""
    if not card: return []
    out = []
    # TCGplayer (USD)
    tp = card.get("tcgplayer") or {}
    tp_prices = tp.get("prices") or {}
    tp_updated = tp.get("updatedAt", "")
    sale_date = re.sub(r'/', '-', tp_updated[:10]) if tp_updated else None
    tp_url = tp.get("url") or f"https://tcgplayer.com/search?q={card.get('name','')}"
    for variant, p in tp_prices.items():
        # variant: holofoil / normal / 1stEditionHolofoil / reverseHolofoil ...
        market = p.get("market") or p.get("mid") or p.get("low")
        if not market: continue
        price_usd = float(market)
        out.append({
            "source": "tcgplayer",
            "listing_title": f"TCGplayer {variant} (market price) — {card.get('name','')}",
            "price_usd": price_usd,
            "price_twd": round(price_usd * USD_TO_TWD, 2),
            "listing_url": tp_url + f"#{variant}",
            "sale_date": sale_date,
            "search_language": "en",
            "_variant": variant,
        })
    # Cardmarket (EUR)
    cm = card.get("cardmarket") or {}
    cm_prices = cm.get("prices") or {}
    cm_updated = cm.get("updatedAt", "")
    cm_sale_date = re.sub(r'/', '-', cm_updated[:10]) if cm_updated else None
    cm_url = cm.get("url") or ""
    avg7 = cm_prices.get("avg7") or cm_prices.get("trendPrice") or cm_prices.get("averageSellPrice")
    if avg7:
        price_eur = float(avg7)
        out.append({
            "source": "cardmarket",
            "listing_title": f"Cardmarket avg7 — {card.get('name','')}",
            "price_usd": None,
            "price_twd": round(price_eur * EUR_TO_TWD, 2),
            "listing_url": cm_url,
            "sale_date": cm_sale_date,
            "search_language": "en",
            "_variant": "avg7",
        })
    return out


# 我們的 set_id (en-Base-Set) → pokemontcg.io set.id (base1)
# pokemontcg.io 用 ptcgoCode + 序號，需要 mapping
# 簡單做法：用 set.name 模糊比對

SET_NAME_CACHE = {}


async def resolve_set_id(client: httpx.AsyncClient, our_set_name: str) -> Optional[str]:
    """fuzzy-match 我們的 set 名稱到 pokemontcg.io 的 set.id"""
    if our_set_name in SET_NAME_CACHE:
        return SET_NAME_CACHE[our_set_name]
    url = "https://api.pokemontcg.io/v2/sets"
    params = {"q": f'name:"{our_set_name}"'}
    try:
        r = await client.get(url, params=params, timeout=10)
        if r.status_code != 200:
            SET_NAME_CACHE[our_set_name] = None
            return None
        data = r.json()
        sets = data.get("data") or []
        if sets:
            sid = sets[0]["id"]
            SET_NAME_CACHE[our_set_name] = sid
            return sid
    except Exception:
        pass
    SET_NAME_CACHE[our_set_name] = None
    return None


if __name__ == "__main__":
    # smoke test
    async def main():
        async with httpx.AsyncClient(headers=HEADERS) as c:
            sid = await resolve_set_id(c, "Base")
            print(f"Base → {sid}")
            card = await fetch_card(c, sid, "4")
            if card:
                recs = card_to_records(card)
                print(f"records: {len(recs)}")
                for r in recs:
                    print(f"  {r['source']:12s} {r['_variant']:25s} TWD ${r['price_twd']}")
    asyncio.run(main())
