import sqlite3

conn = sqlite3.connect('cards.db')
c = conn.cursor()

# 整 set 設計上沒 rarity 的、直接標「無標示」
SYSTEMATIC_NO_RARITY_SETS = ['MC', 'SI', 'XY', 'SVM', 'CP4', 'SVD']

print('=== 動作前 ===')
for s in SYSTEMATIC_NO_RARITY_SETS:
    total = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE set_code = ?", (s,)).fetchone()[0]
    null_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE set_code = ? AND rarity IS NULL", (s,)).fetchone()[0]
    print('  ' + s + ': total=' + str(total) + ' null_rarity=' + str(null_r))

# 統一標為「無標示」
total_updated = 0
for s in SYSTEMATIC_NO_RARITY_SETS:
    result = c.execute("UPDATE jp_card_list SET rarity = '無標示' WHERE set_code = ? AND rarity IS NULL", (s,))
    total_updated += result.rowcount

conn.commit()
print()
print('updated: ' + str(total_updated) + ' rows')

print()
print('=== 動作後 ===')
for s in SYSTEMATIC_NO_RARITY_SETS:
    total = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE set_code = ?", (s,)).fetchone()[0]
    null_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE set_code = ? AND rarity IS NULL", (s,)).fetchone()[0]
    no_mark = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE set_code = ? AND rarity = '無標示'", (s,)).fetchone()[0]
    print('  ' + s + ': total=' + str(total) + ' null=' + str(null_r) + ' 無標示=' + str(no_mark))

print()
print('=== 全表 rarity 完整度 ===')
total = c.execute("SELECT COUNT(*) FROM jp_card_list").fetchone()[0]
has_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL").fetchone()[0]
null_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NULL").fetchone()[0]
print('  total: ' + str(total))
print('  has rarity: ' + str(has_r) + ' (' + str(round(100*has_r/total, 1)) + '%)')
print('  null: ' + str(null_r) + ' (' + str(round(100*null_r/total, 1)) + '%)')

conn.close()