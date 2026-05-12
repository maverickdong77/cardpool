import sqlite3

conn = sqlite3.connect('cards.db')
c = conn.cursor()

print('=== 動作前 ===')
total = c.execute("SELECT COUNT(*) FROM jp_card_list").fetchone()[0]
has_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL").fetchone()[0]
null_r = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NULL").fetchone()[0]
already_no_mark = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity = '無標示'").fetchone()[0]
print('  total: ' + str(total))
print('  has_rarity: ' + str(has_r))
print('  null: ' + str(null_r))
print('  已標 無標示: ' + str(already_no_mark))

# 只標 detail 已 crawl 過 + rarity 仍 NULL 的
# 條件: detail_synced_at IS NOT NULL（detail 抓過了、但沒 rarity image）
result = c.execute("""
    UPDATE jp_card_list 
    SET rarity = '無標示'
    WHERE rarity IS NULL 
      AND detail_synced_at IS NOT NULL
""")
updated = result.rowcount
conn.commit()

print()
print('updated: ' + str(updated) + ' rows (detail crawled but no rarity image)')

print()
print('=== 動作後 ===')
has_r2 = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL").fetchone()[0]
null_r2 = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity IS NULL").fetchone()[0]
no_mark2 = c.execute("SELECT COUNT(*) FROM jp_card_list WHERE rarity = '無標示'").fetchone()[0]
print('  total: ' + str(total))
print('  has_rarity: ' + str(has_r2) + ' (' + str(round(100*has_r2/total, 1)) + '%)')
print('  null: ' + str(null_r2))
print('  無標示: ' + str(no_mark2))

print()
print('=== rarity 分佈 ===')
rs = c.execute("SELECT rarity, COUNT(*) FROM jp_card_list GROUP BY rarity ORDER BY COUNT(*) DESC LIMIT 25").fetchall()
for r, cnt in rs:
    print('  ' + str(r) + ': ' + str(cnt))

print()
print('=== 仍 NULL 的 sample 5 個 ===')
samples = c.execute("SELECT cardID, name_jp, set_code, detail_synced_at FROM jp_card_list WHERE rarity IS NULL LIMIT 5").fetchall()
for cid, name, set_c, sync in samples:
    print('  cid=' + str(cid) + ' set=' + str(set_c) + ' synced=' + str(sync) + ' name=' + str(name))

conn.close()