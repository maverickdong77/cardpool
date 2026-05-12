import httpx
import re
import time
import sqlite3
from datetime import datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_jp_rarity_backfill.log'
BASE = 'https://www.pokemon-card.com'
SLEEP_PER_CARD = 1.0
SLEEP_AFTER_403 = 300

client = httpx.Client(
    headers={"User-Agent": UA, "Accept": "text/html"},
    timeout=30,
    follow_redirects=True,
)

RARITY_MAP = {
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


def parse_rarity(html):
    # 試「沒 _c」的高稀有度 pattern
    m = re.search(r'/assets/images/card/rarity/ic_rare_([a-z]+)\.gif', html)
    if m:
        return RARITY_MAP.get(m.group(1), m.group(1).upper())
    return None


def main():
    f = open(LOG, "a", encoding="utf-8")
    f.write("\n=== run start ===\n")
    f.close()
    log('JP rarity backfill start')
    
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    to_crawl = c.execute("SELECT cardID FROM jp_card_list WHERE rarity IS NULL ORDER BY cardID").fetchall()
    log('to crawl (rarity NULL): ' + str(len(to_crawl)))
    
    fetched = 0
    failed = 0
    parsed = 0
    
    for (cid,) in to_crawl:
        url = BASE + "/card-search/details.php/card/" + str(cid) + "/regu/all"
        try:
            r = client.get(url)
        except Exception:
            failed += 1
            time.sleep(SLEEP_PER_CARD)
            continue
        
        if r.status_code == 403:
            log('  403 cardID ' + str(cid) + ' sleep ' + str(SLEEP_AFTER_403) + 's')
            time.sleep(SLEEP_AFTER_403)
            failed += 1
            continue
        
        if r.status_code != 200:
            failed += 1
            time.sleep(SLEEP_PER_CARD)
            continue
        
        rarity = parse_rarity(r.text)
        
        if rarity:
            c.execute("UPDATE jp_card_list SET rarity = ? WHERE cardID = ?", (rarity, cid))
            parsed += 1
        
        fetched += 1
        if fetched % 300 == 0:
            conn.commit()
            log('  progress: ' + str(fetched) + '/' + str(len(to_crawl)) + ' parsed=' + str(parsed) + ' failed=' + str(failed))
        
        time.sleep(SLEEP_PER_CARD)
    
    conn.commit()
    log('Done')
    log('fetched: ' + str(fetched))
    log('rarity parsed: ' + str(parsed))
    log('failed: ' + str(failed))
    
    final = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL").fetchone()[0]
    log('jp_card_list rarity total: ' + str(final) + ' / 20557')
    
    conn.close()


if __name__ == "__main__":
    try:
        main()
    finally:
        client.close()