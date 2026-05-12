import httpx
import re
import time
import sqlite3
from datetime import datetime
from urllib.parse import unquote

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_jp_detail_crawl_slow.log'
BASE = 'https://www.pokemon-card.com'
SLEEP_PER_CARD = 2.0
SLEEP_AFTER_403 = 300

client = httpx.Client(
    headers={"User-Agent": UA, "Accept": "text/html"},
    timeout=30,
    follow_redirects=True,
)

RARITY_LETTER_TO_LABEL = {
    'c': 'C', 'u': 'U', 'r': 'R',
    'rr': 'RR', 'rrr': 'RRR',
    'sr': 'SR', 'hr': 'HR', 'ur': 'UR',
    'ar': 'AR', 'sar': 'SAR',
    'pr': 'PR', 'tr': 'TR',
    'k': 'K', 'a': 'A',
    's': 'S', 'ssr': 'SSR',
    'ace': 'ACE', 'bwr': 'BWR',
    'mur': 'MUR', 'ma': 'MA',
}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = "[" + ts + "] " + msg
    print(line, flush=True)
    f = open(LOG, "a", encoding="utf-8")
    f.write(line + "\n")
    f.close()


def parse_detail(html):
    out = {}
    m = re.search(r'/assets/images/card/rarity/ic_rare_([a-z]+)_c\.gif', html)
    if m:
        out['rarity'] = RARITY_LETTER_TO_LABEL.get(m.group(1), m.group(1).upper())
    m = re.search(r'<span class="hp-num">(\d+)</span>', html)
    if m:
        try:
            out['hp'] = int(m.group(1))
        except ValueError:
            pass
    m = re.search(r'illust=([^"&]+)"', html)
    if m:
        out['illustrator'] = unquote(m.group(1))
    return out


def main():
    f = open(LOG, "a", encoding="utf-8")
    f.write("\n=== run start ===\n")
    f.close()
    log('JP detail SLOW crawl start (' + str(SLEEP_PER_CARD) + 's per card)')
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    to_crawl = c.execute("SELECT cardID FROM jp_card_list WHERE detail_synced_at IS NULL ORDER BY cardID").fetchall()
    log('to crawl: ' + str(len(to_crawl)))
    
    fetched = 0
    failed = 0
    parsed_rarity = 0
    parsed_hp = 0
    parsed_illu = 0
    consec_403 = 0
    
    for (cid,) in to_crawl:
        url = BASE + "/card-search/details.php/card/" + str(cid) + "/regu/all"
        try:
            r = client.get(url)
        except Exception as e:
            failed += 1
            time.sleep(SLEEP_PER_CARD)
            continue
        
        if r.status_code == 403:
            consec_403 += 1
            log('  403 on cardID ' + str(cid) + ' consec=' + str(consec_403) + ' sleeping ' + str(SLEEP_AFTER_403) + 's')
            time.sleep(SLEEP_AFTER_403)
            failed += 1
            continue
        
        consec_403 = 0
        
        if r.status_code != 200:
            failed += 1
            time.sleep(SLEEP_PER_CARD)
            continue
        
        d = parse_detail(r.text)
        c.execute("""
            UPDATE jp_card_list 
            SET hp = COALESCE(?, hp),
                illustrator = COALESCE(?, illustrator),
                rarity = COALESCE(?, rarity),
                detail_synced_at = CURRENT_TIMESTAMP
            WHERE cardID = ?
        """, (d.get('hp'), d.get('illustrator'), d.get('rarity'), cid))
        
        fetched += 1
        if d.get('rarity'):
            parsed_rarity += 1
        if d.get('hp'):
            parsed_hp += 1
        if d.get('illustrator'):
            parsed_illu += 1
        
        if fetched % 200 == 0:
            conn.commit()
            log('  progress: ' + str(fetched) + '/' + str(len(to_crawl)) + ' rarity=' + str(parsed_rarity) + ' hp=' + str(parsed_hp) + ' illu=' + str(parsed_illu) + ' failed=' + str(failed))
        
        time.sleep(SLEEP_PER_CARD)
    
    conn.commit()
    log('Done')
    log('fetched: ' + str(fetched))
    log('failed: ' + str(failed))
    log('parsed rarity/hp/illu: ' + str(parsed_rarity) + '/' + str(parsed_hp) + '/' + str(parsed_illu))
    
    rc = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL").fetchone()[0]
    hc = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE hp IS NOT NULL").fetchone()[0]
    ic = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE illustrator IS NOT NULL").fetchone()[0]
    log('final jp_card_list: rarity=' + str(rc) + ' hp=' + str(hc) + ' illu=' + str(ic))
    
    conn.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        client.close()