import sqlite3

conn = sqlite3.connect('cards.db')
c = conn.cursor()

print('=== jp_card_list summary ===')
total = c.execute("SELECT COUNT(*) FROM jp_card_list").fetchone()[0]
print('  total: ' + str(total))

print()
print('=== rarity 分佈 ===')
rs = c.execute("SELECT rarity, COUNT(*) FROM jp_card_list GROUP BY rarity ORDER BY COUNT(*) DESC").fetchall()
for r, cnt in rs:
    print('  ' + str(r) + ': ' + str(cnt))

print()
print('=== 沒 rarity 的卡 set 分佈（top 10）===')
no_r = c.execute("SELECT set_code, COUNT(*) FROM jp_card_list WHERE rarity IS NULL GROUP BY set_code ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
for s, cnt in no_r:
    print('  ' + str(s) + ': ' + str(cnt))

print()
print('=== 有 rarity 的卡 set 分佈（top 10）===')
has_r = c.execute("SELECT set_code, COUNT(*) FROM jp_card_list WHERE rarity IS NOT NULL GROUP BY set_code ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
for s, cnt in has_r:
    print('  ' + str(s) + ': ' + str(cnt))

print()
print('=== sample 沒 rarity 的卡 5 個 ===')
samples = c.execute("SELECT cardID, name_jp, set_code, illustrator FROM jp_card_list WHERE rarity IS NULL ORDER BY cardID DESC LIMIT 5").fetchall()
for cid, name, set_c, illu in samples:
    print('  ' + str(cid) + ' set=' + str(set_c) + ' name=' + str(name) + ' illu=' + str(illu))

conn.close()