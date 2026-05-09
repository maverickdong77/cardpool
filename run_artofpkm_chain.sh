#!/usr/bin/env bash
# artofpkm 重抓後一條龍跑下游
set -e
cd "$(dirname "$0")"
PY=./Python/bin/python.exe

echo "=== [1/5] fetch_artofpkm_order (display_order) ==="
$PY -X utf8 fetch_artofpkm_order.py 2>&1 | tail -5

echo
echo "=== [2/5] fetch_artofpkm_eras (era) ==="
$PY -X utf8 fetch_artofpkm_eras.py 2>&1 | tail -25

echo
echo "=== [3/5] backfill_artofpkm_set_meta (logo + release_date) ==="
$PY -X utf8 backfill_artofpkm_set_meta.py 2>&1 | tail -3

echo
echo "=== [4/5] match_artofpkm (set match) ==="
$PY -X utf8 match_artofpkm.py 2>&1 | tail -5

echo
echo "=== [5/5] sync_card_list_from_artofpkm (UPSERT 全變體) ==="
$PY -X utf8 sync_card_list_from_artofpkm.py 2>&1 | tail -10

echo
echo "=== propagate logo to card_sets ==="
$PY -X utf8 -c "
import sqlite3
conn = sqlite3.connect('cards.db')
n = conn.execute('''
    UPDATE card_sets
       SET logo_url = (
           SELECT a.logo_url
             FROM artofpkm_set_match m
             JOIN artofpkm_sets a ON a.id = m.art_id
            WHERE m.our_set_id = card_sets.set_id AND a.logo_url IS NOT NULL
       )
     WHERE card_sets.language='jp'
       AND EXISTS (
           SELECT 1 FROM artofpkm_set_match m
                  JOIN artofpkm_sets a ON a.id = m.art_id
            WHERE m.our_set_id = card_sets.set_id AND a.logo_url IS NOT NULL
       )
''').rowcount
conn.commit()
print(f'card_sets.logo_url updated: {n}')
"

echo
echo "=== verify jp-Start-Deck-100-Battle-Collection ==="
$PY -X utf8 -c "
import sqlite3
conn = sqlite3.connect('cards.db')
print(conn.execute(\"SELECT COUNT(*) FROM card_list WHERE set_id='jp-Start-Deck-100-Battle-Collection'\").fetchone())
print(conn.execute(\"SELECT total_cards FROM card_sets WHERE set_id='jp-Start-Deck-100-Battle-Collection'\").fetchone())
"

echo
echo "All done."
