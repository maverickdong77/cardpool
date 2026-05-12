import httpx
import re
import time
import json
import sqlite3
from datetime import datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_tw_collector_backfill.log'
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


def parse_detail(html, card_id):
    out = {'cardID': int(card_id)}
    
    h1 = re.search(r'<h1[^>]*class="pageHeader[^>]*>(.*?)</h1>', html, re.DOTALL)
    if h1:
        text = re.sub(r'<[^>]+>', ' ', h1.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
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
    log('TW backfill start')
    
    with open('_tw_rarity_full.json', encoding='utf-8') as f:
        rmap = json.load(f)
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    imported = set(str(r[0]) for r in c.execute("SELECT cardID FROM tw_card_list").fetchall())
    mapped_ids = set(rmap['cardid_to_rarity'].keys())
    missing = sorted(mapped_ids - imported, key=int)
    
    log('missing cardIDs to fetch: ' + str(len(missing)))
    
    # warm session
    client.get(BASE + "/tw/card-search/")
    
    fetched = 0
    failed = 0
    redirected = 0
    
    for cid in missing:
        url = BASE + "/tw/card-search/detail/" + cid + "/"
        try:
            r = client.get(url, follow_redirects=False)
        except Exception as e:
            failed += 1
            continue
        if r.status_code == 302:
            redirected += 1
            continue
        if r.status_code != 200:
            failed += 1
            continue
        card_data = parse_detail(r.text, cid)
        if 'name_zh' not in card_data:
            failed += 1
            continue
        rarity = rmap['cardid_to_rarity'].get(cid)
        cardtype = rmap['cardid_to_cardtype'].get(cid)
        insert_card(c, card_data, rarity, cardtype)
        fetched += 1
        if fetched % 200 == 0:
            conn.commit()
            log('  progress: ' + str(fetched) + '/' + str(len(missing)) + ' (failed=' + str(failed) + ' 302=' + str(redirected) + ')')
        time.sleep(0.12)
    
    conn.commit()
    
    log('Done')
    log('fetched: ' + str(fetched))
    log('failed: ' + str(failed))
    log('302 redirect: ' + str(redirected))
    
    final = c.execute("SELECT COUNT(*) FROM tw_card_list").fetchone()[0]
    log('tw_card_list final rows: ' + str(final))
    
    rarity_filled = c.execute("SELECT COUNT(*) FROM tw_card_list WHERE rarity IS NOT NULL").fetchone()[0]
    log('rarity filled: ' + str(rarity_filled) + ' / ' + str(final))
    
    conn.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        client.close()