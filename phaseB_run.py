import sqlite3

with open('_phaseB.sql', encoding='utf-8') as f:
    sql_script = f.read()

conn = sqlite3.connect('cards.db')
c = conn.cursor()

statements = [s.strip() for s in sql_script.split(';') if s.strip()]
print('Total statements: ' + str(len(statements)))
print()

for i, stmt in enumerate(statements):
    first_line = stmt.split('\n')[0][:80]
    try:
        c.execute(stmt)
        print('[' + str(i+1) + '/' + str(len(statements)) + '] OK: ' + first_line)
    except sqlite3.OperationalError as e:
        print('[' + str(i+1) + '/' + str(len(statements)) + '] SKIP (' + str(e) + '): ' + first_line)

conn.commit()

print()
print('=== verify ===')
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'jp_%' OR name LIKE 'en_%') ORDER BY name").fetchall()
for t in tables:
    cnt = c.execute('SELECT COUNT(*) FROM ' + t[0]).fetchone()[0]
    print('  ' + t[0] + ' rows: ' + str(cnt))

print()
print('jp_card_list columns:')
cols = c.execute('PRAGMA table_info(jp_card_list)').fetchall()
for col in cols:
    print('  ' + col[1] + ' (' + col[2] + ')')

conn.close()
print()
print('Done.')