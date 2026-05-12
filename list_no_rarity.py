import sqlite3

conn = sqlite3.connect('cards.db')
c = conn.cursor()

print('=== 沒 rarity 的卡 by set ===')
rows = c.execute("""
    SELECT set_code, COUNT(*) FROM jp_card_list 
    WHERE rarity IS NULL 
    GROUP BY set_code 
    ORDER BY COUNT(*) DESC
""").fetchall()
total_no_r = sum(cnt for _, cnt in rows)
print('total without rarity: ' + str(total_no_r))
print()
for s, cnt in rows:
    print('  ' + str(s) + ': ' + str(cnt))

print()
print('=== 各 set 完整度（has_rarity / total）===')
all_sets = c.execute("""
    SELECT set_code,
           COUNT(*) as total,
           SUM(CASE WHEN rarity IS NULL THEN 0 ELSE 1 END) as has_r
    FROM jp_card_list 
    GROUP BY set_code 
    ORDER BY total DESC
    LIMIT 25
""").fetchall()
for s, total, has_r in all_sets:
    pct = round(100 * has_r / total, 0) if total > 0 else 0
    print('  ' + str(s).ljust(8) + ' total=' + str(total).ljust(5) + ' has_rarity=' + str(has_r).ljust(5) + ' (' + str(int(pct)) + '%)')

conn.close()