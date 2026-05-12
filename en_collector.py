import httpx
import json
import time
import sqlite3
from datetime import datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_en_collector.log'
BASE = 'https://api.pokemontcg.io/v2'

client = httpx.Client(
    headers={"User-Agent": UA, "Accept": "application/json"},
    timeout=60,
)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = "[" + ts + "] " + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch_all_sets():
    url = BASE + '/sets?pageSize=250'
    r = client.get(url)
    r.raise_for_status()
    data = r.json()
    return data.get('data', [])


def fetch_cards_for_set(set_id):
    all_cards = []
    page = 1
    while True:
        url = BASE + '/cards?q=set.id:' + set_id + '&pageSize=250&page=' + str(page)
        r = client.get(url)
        if r.status_code != 200:
            log('  ERROR set=' + set_id + ' page=' + str(page) + ' status=' + str(r.status_code))
            break
        data = r.json()
        cards = data.get('data', [])
        if not cards:
            break
        all_cards.extend(cards)
        total = data.get('totalCount', 0)
        if len(all_cards) >= total:
            break
        page += 1
        time.sleep(0.3)
    return all_cards


def insert_set(c, s):
    c.execute("""
        INSERT OR REPLACE INTO en_card_list_set
        (set_id, name, series, printed_total, total, release_date, ptcgo_code, logo_url, symbol_url, legalities)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        s.get('id'),
        s.get('name'),
        s.get('series'),
        s.get('printedTotal'),
        s.get('total'),
        s.get('releaseDate'),
        s.get('ptcgoCode'),
        (s.get('images') or {}).get('logo'),
        (s.get('images') or {}).get('symbol'),
        json.dumps(s.get('legalities') or {}, ensure_ascii=False),
    ))


def insert_card(c, card):
    set_obj = card.get('set') or {}
    images = card.get('images') or {}
    tcgp = card.get('tcgplayer') or {}
    cm = card.get('cardmarket') or {}
    hp_raw = card.get('hp')
    try:
        hp_int = int(hp_raw) if hp_raw else None
    except (TypeError, ValueError):
        hp_int = None
    pokedex = card.get('nationalPokedexNumbers') or []
    pokedex_no = pokedex[0] if pokedex else None
    c.execute("""
        INSERT OR REPLACE INTO en_card_list
        (pokemontcg_id, set_id, number, name, supertype, subtypes, hp, types, rarity,
         artist, flavor_text, pokedex_number, regulation_mark,
         set_name, series, set_release_date,
         abilities, attacks, weaknesses, retreat_cost, legalities,
         image_small_url, image_large_url,
         tcgplayer_url, tcgplayer_prices, tcgplayer_updated_at,
         cardmarket_url, cardmarket_prices, cardmarket_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?)
    """, (
        card.get('id'),
        set_obj.get('id'),
        card.get('number'),
        card.get('name'),
        card.get('supertype'),
        json.dumps(card.get('subtypes') or [], ensure_ascii=False),
        hp_int,
        json.dumps(card.get('types') or [], ensure_ascii=False),
        card.get('rarity'),
        card.get('artist'),
        card.get('flavorText'),
        pokedex_no,
        card.get('regulationMark'),
        set_obj.get('name'),
        set_obj.get('series'),
        set_obj.get('releaseDate'),
        json.dumps(card.get('abilities') or [], ensure_ascii=False),
        json.dumps(card.get('attacks') or [], ensure_ascii=False),
        json.dumps(card.get('weaknesses') or [], ensure_ascii=False),
        card.get('convertedRetreatCost'),
        json.dumps(card.get('legalities') or {}, ensure_ascii=False),
        images.get('small'),
        images.get('large'),
        tcgp.get('url'),
        json.dumps(tcgp.get('prices') or {}, ensure_ascii=False) if tcgp else None,
        tcgp.get('updatedAt'),
        cm.get('url'),
        json.dumps(cm.get('prices') or {}, ensure_ascii=False) if cm else None,
        cm.get('updatedAt'),
    ))


def main(target_set_id=None):
    open(LOG, "w").close()
    log('EN collector start')
    
    log('Fetching all sets')
    sets = fetch_all_sets()
    log('  total sets: ' + str(len(sets)))
    
    if target_set_id:
        sets = [s for s in sets if s.get('id') == target_set_id]
        log('  filtered to target: ' + str(len(sets)) + ' set')
        if not sets:
            log('  ERROR: target set not found')
            return
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    total_cards_imported = 0
    
    for i, s in enumerate(sets):
        set_id = s.get('id')
        set_name = s.get('name')
        
        insert_set(c, s)
        
        cards = fetch_cards_for_set(set_id)
        for card in cards:
            insert_card(c, card)
        
        total_cards_imported += len(cards)
        
        progress = str(i + 1) + '/' + str(len(sets))
        log('  ' + progress + ' ' + set_id + ' (' + str(set_name) + ') cards=' + str(len(cards)))
        
        conn.commit()
        time.sleep(0.3)
    
    log('Done')
    log('Total sets imported: ' + str(len(sets)))
    log('Total cards imported: ' + str(total_cards_imported))
    
    conn.close()


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        main(target)
    finally:
        client.close()