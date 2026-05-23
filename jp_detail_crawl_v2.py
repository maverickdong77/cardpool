"""
jp_detail_crawl_v2 — 對 jp_card_list 抓 pokemon-card.com 卡片詳細頁。
擴充版（v1 只抓 hp/illu/rarity）：另抓 card_number / types_json / attacks_json /
weakness / resistance / retreat_cost / regulation_mark。

用法：
  python jp_detail_crawl_v2.py           # 全跑（extended_detail_synced_at IS NULL）
  python jp_detail_crawl_v2.py --pg 950  # 只跑 pg=950（smoke test 用）
  python jp_detail_crawl_v2.py --limit 5 # 只跑前 5 張（測試用）

設計：
- sequential、2s/卡、403 -> 300s backoff（同 v1）
- COALESCE UPDATE：不覆蓋既有 non-NULL 值
- 每 50 卡 commit、每 200 卡 log progress
- 重跑安全：以 extended_detail_synced_at IS NULL 篩選、自動跳過已完成卡
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from urllib.parse import unquote

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_jp_detail_crawl_v2.log'
BASE = 'https://www.pokemon-card.com'
SLEEP_PER_CARD = 2.0
SLEEP_AFTER_403 = 300

RARITY_LETTER_TO_LABEL = {
    'c': 'C', 'u': 'U', 'r': 'R', 'rr': 'RR', 'rrr': 'RRR',
    'sr': 'SR', 'hr': 'HR', 'ur': 'UR', 'ar': 'AR', 'sar': 'SAR',
    'pr': 'PR', 'tr': 'TR', 'k': 'K', 'a': 'A', 's': 'S', 'ssr': 'SSR',
    'ace': 'ACE', 'bwr': 'BWR', 'mur': 'MUR', 'ma': 'MA',
}

TYPE_ICONS = {
    'grass', 'fire', 'water',
    'lightning', 'electric',     # 都包：BW 期前 lightning、SM 期後 electric
    'psychic', 'fighting',
    'dark', 'darkness',          # pokemon-card.com 現代用 dark、舊期可能 darkness
    'metal', 'steel',            # pokemon-card.com 現代用 steel、舊期可能 metal
    'fairy', 'dragon', 'colorless',
}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_detail(html):
    out = {}

    # rarity
    m = re.search(r'/assets/images/card/rarity/ic_rare_([a-z]+)_c\.gif', html)
    if m:
        out['rarity'] = RARITY_LETTER_TO_LABEL.get(m.group(1), m.group(1).upper())

    # hp
    m = re.search(r'<span class="hp-num">(\d+)</span>', html)
    if m:
        try:
            out['hp'] = int(m.group(1))
        except ValueError:
            pass

    # illustrator
    m = re.search(r'illust=([^"&]+)"', html)
    if m:
        out['illustrator'] = unquote(m.group(1))

    # card_number — 裸整數字串，對齊 card_list / snkrdunk_mapping
    # 現代卡：「003 / 193」、DP 期：「DPBP#003」、其他不一
    m = re.search(r'<div class="subtext[^"]*">([\s\S]*?)</div>', html)
    if m:
        text = re.sub(r'<[^>]+>', ' ', m.group(1))
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        nm = re.search(r'(\d+)', text)
        if nm:
            out['card_number'] = str(int(nm.group(1)))  # "3" / "115"

    # regulation_mark / set_code variant — <img class="img-regulation" alt="M2a" />
    # 注意：alt 多半是 set_code（M2a/DP3/S1W/SM4A），非真規則字母（D/E/F/G/H/I/J）
    # 但既然順手抓、留著無害（未來如需區分再洗）
    m = re.search(r'<img[^>]*class="img-regulation"[^>]*alt="([^"]+)"', html)
    if m:
        out['regulation_mark'] = m.group(1)

    # types — 抓 .hp-type 區塊內所有 icon-{type}
    # 註：pokemon-card.com 用 icon-none 渲染 Colorless 屬性（與 attack cost 的「無色能量」共用）
    # 在 type 區塊出現的 'none' 一律當作 Colorless 屬性
    m = re.search(r'<span class="hp-type">[^<]*</span>([\s\S]*?)</div>', html)
    if m:
        types = []
        for cls in re.findall(r'icon-([a-z]+)\s+icon', m.group(1)):
            mapped = 'colorless' if cls == 'none' else cls
            if mapped in TYPE_ICONS and mapped not in types:
                types.append(mapped)
        if types:
            out['types_json'] = json.dumps(types)

    # attacks — 只在 <h2>ワザ</h2> 區塊內
    waza_m = re.search(r'<h2[^>]*>ワザ</h2>([\s\S]*?)(?:<h2|<table)', html)
    if waza_m:
        waza = waza_m.group(1)
        attacks = []
        for h4m in re.finditer(r'<h4>([\s\S]*?)</h4>(?:\s*<p[^>]*>([\s\S]*?)</p>)?', waza):
            head = h4m.group(1)
            body = h4m.group(2) or ''
            energies = re.findall(r'icon-([a-z]+)\s+icon', head)
            dmg = None
            dm = re.search(r'<span class="f_right[^"]*">([^<]+)</span>', head)
            if dm:
                dmg = dm.group(1).strip()
            name = re.sub(r'<[^>]+>', '', head).replace('\xa0', '').replace('&nbsp;', '').strip()
            if dmg:
                name = name.replace(dmg, '').strip()
            text = re.sub(r'<[^>]+>', '', body).strip() or None
            if name or energies:
                attacks.append({
                    'cost': energies,
                    'name': name,
                    'damage': dmg,
                    'text': text,
                })
        if attacks:
            out['attacks_json'] = json.dumps(attacks, ensure_ascii=False)

    # Weakness / Resistance / Retreat — table with 弱点/抵抗力/にげる headers
    tbl_m = re.search(
        r'<table[^>]*>\s*<tr>\s*<th>弱点</th>\s*<th>抵抗力</th>\s*<th>にげる</th>\s*</tr>\s*<tr>([\s\S]*?)</tr>\s*</table>',
        html,
    )
    if tbl_m:
        row = tbl_m.group(1)
        tds = re.findall(r'<td[^>]*>([\s\S]*?)</td>', row)
        if len(tds) >= 3:
            wm = re.search(r'icon-([a-z]+)\s+icon[^>]*>\s*</span>([^<]*)', tds[0])
            if wm and wm.group(1) != 'none':
                mod = wm.group(2).strip()
                if mod:
                    out['weakness'] = wm.group(1) + mod
            rm = re.search(r'icon-([a-z]+)\s+icon[^>]*>\s*</span>([^<]*)', tds[1])
            if rm and rm.group(1) != 'none':
                mod = rm.group(2).strip()
                if mod:
                    out['resistance'] = rm.group(1) + mod
            out['retreat_cost'] = len(re.findall(r'icon-none\s+icon', tds[2]))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pg', type=str, default=None, help='Limit to one pg (e.g. 950)')
    ap.add_argument('--limit', type=int, default=None, help='Limit number of cards')
    ap.add_argument('--force', action='store_true', help='Re-crawl even if extended_detail_synced_at is set')
    args = ap.parse_args()

    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n=== run start " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ===\n")
    log(f'v2 crawl start; SLEEP_PER_CARD={SLEEP_PER_CARD}s; args={vars(args)}')

    client = httpx.Client(
        headers={"User-Agent": UA, "Accept": "text/html"},
        timeout=30,
        follow_redirects=True,
    )

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # Build selection query
    where_clauses = []
    params = []
    if not args.force:
        where_clauses.append('extended_detail_synced_at IS NULL')
    if args.pg is not None:
        where_clauses.append('pg = ?')
        params.append(args.pg)
    where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
    limit_sql = f' LIMIT {args.limit}' if args.limit else ''
    query = f'SELECT cardID FROM jp_card_list{where_sql} ORDER BY CAST(pg AS INTEGER) DESC, cardID{limit_sql}'
    to_crawl = c.execute(query, params).fetchall()
    log(f'to crawl: {len(to_crawl)} cards')

    fetched = failed = 0
    counts = {k: 0 for k in
              ['hp', 'illustrator', 'rarity', 'card_number', 'regulation_mark',
               'types_json', 'attacks_json', 'weakness', 'resistance', 'retreat_cost']}
    consec_403 = 0
    consec_fail = 0

    try:
        for i, (cid,) in enumerate(to_crawl, 1):
            url = f"{BASE}/card-search/details.php/card/{cid}/regu/all"
            try:
                r = client.get(url)
            except Exception as e:
                failed += 1
                consec_fail += 1
                if consec_fail >= 10:
                    log(f'  ABORT: 10 consecutive request failures (last: {e})')
                    break
                time.sleep(SLEEP_PER_CARD)
                continue

            if r.status_code == 403:
                consec_403 += 1
                log(f'  403 on cardID {cid} consec={consec_403}, sleeping {SLEEP_AFTER_403}s')
                if consec_403 >= 5:
                    log('  ABORT: 5 consecutive 403s, IP likely blocked')
                    break
                time.sleep(SLEEP_AFTER_403)
                failed += 1
                continue

            consec_403 = 0
            consec_fail = 0

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
                    card_number = COALESCE(?, card_number),
                    regulation_mark = COALESCE(?, regulation_mark),
                    types_json = COALESCE(?, types_json),
                    attacks_json = COALESCE(?, attacks_json),
                    weakness = COALESCE(?, weakness),
                    resistance = COALESCE(?, resistance),
                    retreat_cost = COALESCE(?, retreat_cost),
                    detail_synced_at = CURRENT_TIMESTAMP,
                    extended_detail_synced_at = CURRENT_TIMESTAMP
                WHERE cardID = ?
            """, (
                d.get('hp'), d.get('illustrator'), d.get('rarity'),
                d.get('card_number'), d.get('regulation_mark'),
                d.get('types_json'), d.get('attacks_json'),
                d.get('weakness'), d.get('resistance'), d.get('retreat_cost'),
                cid,
            ))

            fetched += 1
            for k in counts:
                if d.get(k) is not None:
                    counts[k] += 1

            if fetched % 50 == 0:
                conn.commit()
            if fetched % 200 == 0:
                stats = ' '.join(f'{k}={counts[k]}' for k in ('card_number', 'types_json', 'attacks_json', 'weakness'))
                log(f'  progress {fetched}/{len(to_crawl)} | {stats} | failed={failed}')

            time.sleep(SLEEP_PER_CARD)

        conn.commit()
        log(f'Done. fetched={fetched} failed={failed}')
        for k in counts:
            log(f'  field {k}: {counts[k]} / {fetched}')

    finally:
        conn.close()
        client.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
