import httpx
import re
import time
import json
import sqlite3
from datetime import datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_tw_collector.log'
BASE = 'https://asia.pokemon-card.com'

client = httpx.Client(
    headers={"User-Agent": UA, "Accept": "text/html", "Referer": BASE + "/tw/card-search/"},
    timeout=30,
    follow_redirects=True,
)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = "[" + ts + "] " + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def warm_session():
    client.get(BASE + "/tw/card-search/")


def get_expansion_codes():
    r = client.get(BASE + "/tw/card-search/")
    codes = re.findall(r'expansionCodes=([A-Z0-9.\-]+?)["&]', r.text)
    return sorted(set(codes))


def fetch_list_cardids(expansion_code):
    all_ids = []
    page = 1
    while page <= 60:
        url = BASE + "/tw/card-search/list/?expansionCodes=" + expansion_code + "&pageNo=" + str(page)
        r = client.get(url)
        if r.status_code != 200:
            break
        ids = re.findall(r'/tw/card-search/detail/(\d+)/', r.text)
        unique_on_page = sorted(set(ids), key=int)
        if not unique_on_page:
            break
        new_ids = [i for i in unique_on_page if i not in all_ids]
        if not new_ids:
            break
        all_ids.extend(new_ids)
        page += 1
        time.sleep(0.2)
    return all_ids


def parse_detail(html, card_id):
    out = {'cardID': int(card_id)}
    
    h1 = re.search(r'<h1[^>]*class="pageHeader[^>]*>(.*?)</h1>', html, re.DOTALL)
    if h1:
        text = re.sub(r'<[^>]+>', ' ', h1.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        out['h1_text'] = text
        for prefix in ['基礎', '1階進化', '2階進化', '物品', '訓練家', '寶可夢道具', '化石', '統元寶可夢', '能源', '特殊能源']:
            if text.startswith(prefix + ' '):
                out['stage'] = prefix
                out['name_zh'] = text[len(prefix)+1:].strip()
                break
        if 'stage' not in out:
            out['name_zh'] = text
    
    img = re.search(r'<div class="cardImage">\s*<img\s+src="([^"]+)"', html)
    if img:
        out['thumb_url'] = img.group(1)
        m_iid = re.search(r'tw0*(\d+)\.png', img.group(1))
        if m_iid:
            out['image_id'] = m_iid.group(1).zfill(8)
    
    hp = re.search(r'<span class="hitPoint">[^<]*</span>\s*<span class="number">(\d+)</span>', html)
    if hp:
        try:
            out['hp'] = int(hp.group(1))
        except ValueError:
            pass
    
    set_link = re.search(r'<section class="expansionLinkColumn">.*?<a[^>]+href="[^"]*expansionCodes=([^"&]+)[^"]*">\s*([^<]+)\s*</a>', html, re.DOTALL)
    if set_link:
        out['expansion_code'] = set_link.group(1).strip()
        out['set_name_zh'] = set_link.group(2).strip()
    
    alpha = re.search(r'<span class="alpha">\s*([A-Z])\s*</span>', html)
    if alpha:
        out['regulation_mark'] = alpha.group(1)
    
    coll = re.search(r'<span class="collectorNumber">\s*(\d+)\s*/\s*(\d+)\s*</span>', html)
    if coll:
        out['card_number'] = coll.group(1)
        out['printed_total'] = int(coll.group(2))
    
    return out


def insert_card(c, card_data, rarity, cardtype):
    c.execute("""
        INSERT OR REPLACE INTO tw_card_list
        (cardID, expansion_code, name_zh, stage, card_type, hp, card_number,
         printed_total, regulation_mark, thumb_url, image_id, set_name_zh, rarity,
         source, synced_at, detail_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'asia.pokemon-card.com',
         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (
        card_data.get('cardID'),
        card_data.get('expansion_code'),
        card_data.get('name_zh'),
        card_data.get('stage'),
        cardtype,
        card_data.get('hp'),
        card_data.get('card_number'),
        card_data.get('printed_total'),
        card_data.get('regulation_mark'),
        card_data.get('thumb_url'),
        card_data.get('image_id'),
        card_data.get('set_name_zh'),
        rarity,
    ))


def main():
    open(LOG, "w").close()
    log('TW collector start')
    
    log('Load rarity mapping')
    with open('_tw_rarity_full.json', encoding='utf-8') as f:
        rmap_data = json.load(f)
    cardid_to_rarity = rmap_data['cardid_to_rarity']
    cardid_to_cardtype = rmap_data['cardid_to_cardtype']
    log('  rarity entries: ' + str(len(cardid_to_rarity)))
    
    warm_session()
    
    log('Discover expansion codes')
    codes = get_expansion_codes()
    log('  codes: ' + str(codes))
    
    # ensure tw_card_list tables exist
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS tw_card_list (
            cardID INTEGER PRIMARY KEY,
            expansion_code TEXT,
            name_zh TEXT,
            stage TEXT,
            card_type TEXT,
            hp INTEGER,
            card_number TEXT,
            printed_total INTEGER,
            regulation_mark TEXT,
            thumb_url TEXT,
            image_id TEXT,
            set_name_zh TEXT,
            rarity TEXT,
            image_phash TEXT,
            illustrator TEXT,
            source TEXT DEFAULT 'asia.pokemon-card.com',
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            detail_synced_at TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tw_expansion ON tw_card_list(expansion_code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tw_name ON tw_card_list(name_zh)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tw_rarity ON tw_card_list(rarity)')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tw_card_list_set (
            expansion_code TEXT PRIMARY KEY,
            name_zh TEXT,
            card_count INTEGER,
            synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    
    # Phase 1: 跑全 expansion list、收 cardIDs
    log('Phase 1: collect all cardIDs via expansionCodes')
    all_cardids = set()
    for i, code in enumerate(codes):
        ids = fetch_list_cardids(code)
        all_cardids.update(ids)
        c.execute("INSERT OR REPLACE INTO tw_card_list_set (expansion_code, card_count) VALUES (?, ?)", (code, len(ids)))
        conn.commit()
        log('  ' + str(i+1) + '/' + str(len(codes)) + ' ' + code + ' cards=' + str(len(ids)))
        time.sleep(0.2)
    log('  total unique cardIDs from list: ' + str(len(all_cardids)))
    
    # Phase 2: fetch detail for each
    log('Phase 2: fetch detail + insert')
    total_to_fetch = len(all_cardids)
    fetched = 0
    failed = 0
    for cid in sorted(all_cardids, key=int):
        url = BASE + "/tw/card-search/detail/" + cid + "/"
        try:
            r = client.get(url)
        except Exception as e:
            failed += 1
            continue
        if r.status_code != 200:
            failed += 1
            continue
        card_data = parse_detail(r.text, cid)
        rarity = cardid_to_rarity.get(cid)
        cardtype = cardid_to_cardtype.get(cid)
        insert_card(c, card_data, rarity, cardtype)
        fetched += 1
        if fetched % 100 == 0:
            conn.commit()
            log('  progress: ' + str(fetched) + '/' + str(total_to_fetch) + ' (' + str(failed) + ' failed)')
        time.sleep(0.15)
    conn.commit()
    
    log('Done')
    log('Total fetched: ' + str(fetched))
    log('Total failed: ' + str(failed))
    
    final_count = c.execute("SELECT COUNT(*) FROM tw_card_list").fetchone()[0]
    log('tw_card_list final rows: ' + str(final_count))
    
    rarity_filled = c.execute("SELECT COUNT(*) FROM tw_card_list WHERE rarity IS NOT NULL").fetchone()[0]
    log('rarity filled: ' + str(rarity_filled) + ' / ' + str(final_count))
    
    conn.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        client.close()