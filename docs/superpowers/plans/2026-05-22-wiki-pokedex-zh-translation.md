# Wiki 寶可夢圖鑑 JP→ZH 翻譯管線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 52poke wiki 全國圖鑑為權威字典、寫跨 set 通用的 JP→ZH 翻譯函式、套到搜尋頁 + 分類頁 + set 詳情頁、把 jp 卡中文涵蓋率從 45% (per-card lookup) 提到 70-85% (per-pokemon)。

**Architecture:** wiki 圖鑑 → pokemon_dict.name_zh + jp_term_dict.name_zh 兩張字典 → 翻譯函式 `_translate_jp_card_name_to_zh()` 處理人物の / メガ / 地區形 / 後綴 + core 查詢 → 三個 endpoint 統一套用 → 既有 `_JP_ZH_LOOKUP` per-card 字典當 fallback。

**Tech Stack:** Python 3.14 (embedded `./Python/bin/python.exe`) / SQLite (cards.db, WAL) / FastAPI / aiosqlite / playwright MCP / httpx。Wiki 擋 WebFetch、必須用 playwright `browser_evaluate`。

**Spec ref:** `docs/superpowers/specs/2026-05-22-wiki-pokedex-zh-translation-design.md`

---

## File Structure

**Create:**
- `_scrape_wiki_pokedex.py` — 用 playwright MCP 抓 wiki 9 個 table 產 JSON（local-only、`_` 開頭被 `.gitignore` 擋）
- `_wiki_pokedex_zh.json` — 1,025 條 jp → zh 對照表（含變體）
- `_apply_wiki_pokedex_zh.py` — JSON 寫進 `pokemon_dict.name_zh`
- `_apply_jp_term_dict_zh.py` — 從 `_jp_zh_translations.json` 抽 trainer 條目回填 `jp_term_dict.name_zh`
- `_test_translate_zh.py` — unit test 給新翻譯函式（`_` 開頭、local-only、不靠 pytest）
- `_audit_jp_zh_missing.py` — 全量盤點 miss 卡、湊 50 個輸出 `_jp_zh_missing.md`
- `_jp_zh_missing.md` — miss 表（給 user 看、決定下一輪手工補）

**Modify:**
- `app/database.py`：init_db CREATE TABLE 加 `pokemon_dict.name_zh` + `jp_term_dict.name_zh` 兩條（schema 雙寫一致性）；`search_cards_in_list` 的 `_search_jp` 加翻譯 fallback
- `app/main.py`：新增 `_translate_jp_card_name_to_zh()` 函式（緊鄰 `_translate_jp_card_name_to_en`）；`category_pokemon_cards` / `category_character_cards` 對 jp-* row 套新函式；`get_cards_by_set` jp 分支也改套（PROGRESS.md 寫該函式在 `app/database.py:793`、要交叉確認）

---

## Pitfalls 提醒（從 PROGRESS.md 抽）

實作每個 task 之前再讀一次：

1. **schema 雙寫一致性**：手動 ALTER TABLE 後一定要同步寫進 `app/database.py:init_db()` 的 CREATE TABLE。
2. **改 DB 前先 backup**：`cp cards.db cards.db.before-pokedex-zh-YYYYMMDD-HHMMSS`。
3. **DB 改動腳本以 `_` 開頭命名**：`.gitignore` 排除、保 local-only、**不要 `git add -f`**。
4. **HTA 模式重啟 API 才生效**：`taskkill /F /PID <listener>; ./Python/bin/python.exe run_api.py`。
5. **Windows cp950**：所有 Python 印 JP/ZH 字串的指令前要加 `PYTHONIOENCODING=utf-8`。
6. **wiki 擋 WebFetch**：對 `wiki.52poke.com` 用 playwright MCP `browser_navigate` + `browser_evaluate` 抽 HTML。
7. **PowerShell `||` parse error**：所有 SQL 寫進 `_*.py` 不要 inline。
8. **Python `./Python/bin/python.exe` 一律用**、不要用系統 python。

---

## Task 1: 爬 wiki 全國圖鑑、產 JSON

**Files:**
- Create: `_scrape_wiki_pokedex.py`
- Create: `_wiki_pokedex_zh.json`

- [ ] **Step 1.1: 寫爬蟲腳本（用 playwright MCP）**

因為 playwright MCP 工具只能在 Claude 對話內呼叫、不是 `pip install` 套件、本 task 的「爬蟲」實際是 **腳本協助 + Claude 直接用 MCP 操作**。腳本職責 = 把 MCP 抽出來的 raw cell array parse 成 JSON。

Create file `_scrape_wiki_pokedex.py`：

```python
"""Parse wiki 全國圖鑑表格 raw cell 陣列、產出 _wiki_pokedex_zh.json。

不直接打 wiki — wiki 擋 WebFetch、由 Claude 用 playwright MCP browser_evaluate
抽出 9 個 table.eplist 的所有 row cells、寫進 _wiki_raw_cells.json
（每 row 是 list of strings）。本腳本讀該檔、按欄位 index parse。

cell 順序（從 sample 觀察）：
    [0] 編號 (#0001)
    [1] 圖像 (空字串、img alt only)
    [2] (?) 可能是空、或第二圖
    [3] 中文 (妙蛙種子 / 小拉達\n阿羅拉的樣子)
    [4] 日文 (フシギダネ)
    [5] 英文 (Bulbasaur)
    [6+] 屬性

特殊處理：
- 中文欄含「\n阿羅拉的樣子」這種變體標註、要 strip 只留 base 名
- 阿羅拉/伽勒爾/洗翠/帕底亞 變體在 wiki 跟 base 同一 row、不另存
- # 開頭抽數字當 key
"""
import json
import re
import sys

RAW_PATH = "_wiki_raw_cells.json"
OUT_PATH = "_wiki_pokedex_zh.json"

VARIANT_TAGS = (
    "\n阿羅拉的樣子", "\n伽勒爾的樣子", "\n洗翠的樣子", "\n帕底亞的樣子",
    "\n阿羅拉的樣子", "（阿羅拉）", "（伽勒爾）",
)

def parse_row(cells):
    if len(cells) < 6:
        return None
    num_raw = cells[0].strip()
    m = re.match(r'^#(\d+)', num_raw)
    if not m:
        return None
    pid = str(int(m.group(1)))  # 去 leading 0
    cn = cells[3].strip()
    for tag in VARIANT_TAGS:
        if tag in cn:
            cn = cn.split(tag)[0].strip()
    jp = cells[4].strip()
    en = cells[5].strip()
    if not cn or not jp:
        return None
    return pid, {"name_jp": jp, "name_zh": cn, "name_en": en}


def main():
    try:
        raw = json.load(open(RAW_PATH, encoding="utf-8"))
    except FileNotFoundError:
        print(f"找不到 {RAW_PATH}、要先用 Claude playwright MCP 抽 wiki cell")
        sys.exit(1)
    out = {}
    skipped = 0
    for row in raw:
        result = parse_row(row)
        if result is None:
            skipped += 1
            continue
        pid, data = result
        out[pid] = data
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"寫入 {OUT_PATH}：{len(out)} 條 / 跳過 {skipped} row")


if __name__ == "__main__":
    main()
```

- [ ] **Step 1.2: 用 playwright MCP 開 wiki 頁、抽 raw cells 寫進 `_wiki_raw_cells.json`**

對 Claude agent 操作步驟（不是 shell command）：

1. `mcp__plugin_playwright_playwright__browser_navigate` 開 `https://wiki.52poke.com/wiki/%E5%AE%9D%E5%8F%AF%E6%A2%A6%E5%88%97%E8%A1%A8%EF%BC%88%E6%8C%89%E5%85%A8%E5%9B%BD%E5%9B%BE%E9%89%B4%E7%BC%96%E5%8F%B7%EF%BC%89`
2. `mcp__plugin_playwright_playwright__browser_evaluate` 跑：

```javascript
() => {
  const tables = document.querySelectorAll('table.eplist');
  const all_rows = [];
  for (const t of tables) {
    const rows = t.querySelectorAll('tr');
    for (let i = 1; i < rows.length; i++) {  // 跳過表頭
      const cells = Array.from(rows[i].querySelectorAll('th, td')).map(c => c.innerText.trim());
      if (cells.length >= 6 && cells[0].startsWith('#')) {
        all_rows.push(cells);
      }
    }
  }
  return all_rows;
}
```

3. 把結果寫到 `_wiki_raw_cells.json`（JSON.stringify、Write tool）

- [ ] **Step 1.3: 跑腳本產 JSON、印 10 條樣本**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _scrape_wiki_pokedex.py
```

預期 output：
```
寫入 _wiki_pokedex_zh.json：~1025 條 / 跳過 0 row
```

驗證印 10 條樣本：

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import json; d=json.load(open('_wiki_pokedex_zh.json',encoding='utf-8')); import itertools; [print(k, v) for k,v in itertools.islice(d.items(),10)]"
```

預期 output 含：
```
1 {'name_jp': 'フシギダネ', 'name_zh': '妙蛙種子', 'name_en': 'Bulbasaur'}
25 {'name_jp': 'ピカチュウ', 'name_zh': '皮卡丘', 'name_en': 'Pikachu'}
...
1025 {'name_jp': 'モモワロウ', 'name_zh': '桃歹郎', 'name_en': 'Pecharunt'}
```

- [ ] **Step 1.4: 不 commit、保 local**

`_scrape_wiki_pokedex.py` 跟 `_wiki_pokedex_zh.json` 都被 `.gitignore` 排除、不執行 `git add`。

---

## Task 2: pokemon_dict 加 name_zh 欄位 + 灌資料

**Files:**
- Modify: `app/database.py`（init_db 區段 CREATE TABLE pokemon_dict）
- Create: `_apply_wiki_pokedex_zh.py`
- Modify: `cards.db`（透過腳本）

- [ ] **Step 2.1: backup cards.db**

```powershell
cp cards.db cards.db.before-pokedex-zh-20260522-HHMMSS
```

把 HHMMSS 換成當下時間（例如 `20260522-143000`）。

- [ ] **Step 2.2: 改 app/database.py init_db、加 pokemon_dict.name_zh**

先 grep 找到 pokemon_dict CREATE TABLE：

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "open('app/database.py',encoding='utf-8').readlines()" | findstr -n "pokemon_dict"
```

預期 CREATE TABLE 區段類似：
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS pokemon_dict (
        id INTEGER PRIMARY KEY,
        name_en TEXT,
        name_jp TEXT,
        romaji TEXT
    )
""")
```

用 Edit tool 改成：
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS pokemon_dict (
        id INTEGER PRIMARY KEY,
        name_en TEXT,
        name_jp TEXT,
        romaji TEXT,
        name_zh TEXT
    )
""")
```

- [ ] **Step 2.3: 寫 _apply_wiki_pokedex_zh.py**

Create file:

```python
"""ALTER pokemon_dict 加 name_zh（idempotent）、灌 _wiki_pokedex_zh.json。"""
import sqlite3
import json
import sys

DB = "cards.db"
JSON_PATH = "_wiki_pokedex_zh.json"


def ensure_column():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(pokemon_dict)")
    cols = [r[1] for r in cur.fetchall()]
    if "name_zh" not in cols:
        cur.execute("ALTER TABLE pokemon_dict ADD COLUMN name_zh TEXT")
        conn.commit()
        print("ALTER 加 name_zh 欄位 OK")
    else:
        print("name_zh 欄位已存在、跳過 ALTER")
    conn.close()


def apply_data():
    data = json.load(open(JSON_PATH, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    updated = 0
    missing = []
    for pid, entry in data.items():
        pid_int = int(pid)
        zh = entry.get("name_zh")
        if not zh:
            continue
        cur.execute("UPDATE pokemon_dict SET name_zh = ? WHERE id = ?", (zh, pid_int))
        if cur.rowcount > 0:
            updated += cur.rowcount
        else:
            missing.append((pid, entry.get("name_jp"), zh))
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM pokemon_dict WHERE name_zh IS NOT NULL")
    total_with_zh = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pokemon_dict WHERE name_zh IS NULL")
    still_null = cur.fetchone()[0]
    conn.close()
    print(f"UPDATE 成功 row: {updated}")
    print(f"pokemon_dict 有 name_zh 總數: {total_with_zh}")
    print(f"仍 NULL: {still_null}")
    if missing:
        print(f"wiki 有 id 但 pokemon_dict 沒對應 (跳過): {len(missing)} 條")
        for m in missing[:5]:
            print(f"  {m}")


if __name__ == "__main__":
    ensure_column()
    apply_data()
```

- [ ] **Step 2.4: 跑腳本**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _apply_wiki_pokedex_zh.py
```

預期：
```
ALTER 加 name_zh 欄位 OK
UPDATE 成功 row: ~1025
pokemon_dict 有 name_zh 總數: ~1025
仍 NULL: 0
```

- [ ] **Step 2.5: 驗證抽 sample**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); [print(r) for r in c.execute('SELECT id, name_en, name_jp, name_zh FROM pokemon_dict WHERE id IN (1, 25, 150, 658, 720, 1025)')]"
```

預期：
```
(1, 'Bulbasaur', 'フシギダネ', '妙蛙種子')
(25, 'Pikachu', 'ピカチュウ', '皮卡丘')
(150, 'Mewtwo', 'ミュウツー', '超夢')
(658, 'Greninja', 'ゲッコウガ', '甲賀忍蛙')
(720, 'Hoopa', 'フーパ', '胡帕')
(1025, 'Pecharunt', 'モモワロウ', '桃歹郎')
```

- [ ] **Step 2.6: commit `app/database.py` 改動**

只 commit `app/database.py`、不 commit `_apply_*` 或 JSON。

```powershell
git add app/database.py
git commit -m "$(cat <<'EOF'
db: pokemon_dict 加 name_zh 欄位

把 wiki 全國圖鑑中文寫進 pokemon_dict.name_zh、為跨 set 通用 JP->ZH
翻譯管線鋪路。同步更新 init_db() CREATE TABLE 保 schema 一致性。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: jp_term_dict 加 name_zh 欄位 + 從現有 _jp_zh_translations.json 回填

**Files:**
- Modify: `app/database.py`（init_db CREATE TABLE jp_term_dict）
- Create: `_apply_jp_term_dict_zh.py`
- Modify: `cards.db`

- [ ] **Step 3.1: 改 app/database.py init_db、加 jp_term_dict.name_zh**

grep 找 jp_term_dict CREATE TABLE 區段：

```powershell
Get-Content app/database.py | Select-String -Pattern "jp_term_dict" -Context 0,15
```

預期既有 schema:
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS jp_term_dict (
        name_jp     TEXT PRIMARY KEY,
        name_en     TEXT NOT NULL,
        category    TEXT,
        confidence  REAL,
        sources     TEXT,
        raw_lookup  TEXT,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
```

用 Edit tool 改成（在 `raw_lookup` 後面、`created_at` 前面加 `name_zh`）：
```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS jp_term_dict (
        name_jp     TEXT PRIMARY KEY,
        name_en     TEXT NOT NULL,
        category    TEXT,
        confidence  REAL,
        sources     TEXT,
        raw_lookup  TEXT,
        name_zh     TEXT,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
```

- [ ] **Step 3.2: 寫 _apply_jp_term_dict_zh.py**

Create file:

```python
"""ALTER jp_term_dict 加 name_zh（idempotent）、回填從 _jp_zh_translations.json。

策略：
- _jp_zh_translations.json 的 key 是 set_id/card_number、value 是 zh 卡名
- 但 jp_term_dict.name_jp 是日文 term（如「博士の研究」「ボスの指令」）
- 為了找出哪些 lookup value 對應到 jp_term_dict、要去 jp_card_list 撈 (pg, card_number) → name_jp
- 接著看 name_jp 在不在 jp_term_dict、命中就 UPDATE

注意：本步驟保守、只回填 jp_term_dict.name_jp 跟 jp_card_list.name_jp 完全相等
       的條目（避免 trainer 卡 + 寶可夢卡同名同號污染）。
"""
import sqlite3
import json

DB = "cards.db"
JSON_PATH = "_jp_zh_translations.json"


def ensure_column():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(jp_term_dict)")
    cols = [r[1] for r in cur.fetchall()]
    if "name_zh" not in cols:
        cur.execute("ALTER TABLE jp_term_dict ADD COLUMN name_zh TEXT")
        conn.commit()
        print("ALTER 加 name_zh 欄位 OK")
    else:
        print("name_zh 欄位已存在、跳過 ALTER")
    conn.close()


def apply_data():
    lookup = json.load(open(JSON_PATH, encoding="utf-8"))
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 取 jp_term_dict 所有 name_jp（set 給快速查 in）
    cur.execute("SELECT name_jp FROM jp_term_dict")
    term_names = set(r[0] for r in cur.fetchall())
    print(f"jp_term_dict 共 {len(term_names)} 條 term")

    # 對 lookup 每個 key (pg/cn)、查 jp_card_list 拿 name_jp、看在不在 term_names
    updates = {}  # name_jp -> name_zh
    miss_cards = 0
    for key, zh in lookup.items():
        try:
            pg, cn = key.split("/", 1)
        except ValueError:
            continue
        row = cur.execute(
            "SELECT name_jp FROM jp_card_list WHERE pg=? AND card_number=? LIMIT 1",
            (pg, cn),
        ).fetchone()
        if not row:
            miss_cards += 1
            continue
        name_jp = row["name_jp"]
        if name_jp in term_names:
            # 同 name_jp 多次出現、後寫覆蓋前寫；jp_term_dict 是 PK name_jp、本來就 unique
            updates[name_jp] = zh

    # 寫進 DB
    updated = 0
    for name_jp, zh in updates.items():
        cur.execute("UPDATE jp_term_dict SET name_zh=? WHERE name_jp=?", (zh, name_jp))
        if cur.rowcount > 0:
            updated += cur.rowcount
    conn.commit()

    # 統計
    total_with_zh = cur.execute(
        "SELECT COUNT(*) FROM jp_term_dict WHERE name_zh IS NOT NULL"
    ).fetchone()[0]
    still_null = cur.execute(
        "SELECT COUNT(*) FROM jp_term_dict WHERE name_zh IS NULL"
    ).fetchone()[0]
    conn.close()
    print(f"準備回填 distinct name_jp: {len(updates)}")
    print(f"UPDATE 成功 row: {updated}")
    print(f"jp_term_dict 有 name_zh 總數: {total_with_zh}")
    print(f"仍 NULL: {still_null}")
    print(f"lookup key 對不到 jp_card_list: {miss_cards}")


if __name__ == "__main__":
    ensure_column()
    apply_data()
```

- [ ] **Step 3.3: 跑腳本**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _apply_jp_term_dict_zh.py
```

預期（實際數字看 lookup 含多少 trainer）：
```
ALTER 加 name_zh 欄位 OK
jp_term_dict 共 1495 條 term
準備回填 distinct name_jp: ~150-400
UPDATE 成功 row: ~150-400
jp_term_dict 有 name_zh 總數: ~150-400
仍 NULL: ~1100-1300
lookup key 對不到 jp_card_list: <100
```

- [ ] **Step 3.4: 驗證 sample**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db'); c.row_factory=sqlite3.Row; [print(dict(r)) for r in c.execute('SELECT name_jp, name_en, name_zh, category FROM jp_term_dict WHERE name_zh IS NOT NULL LIMIT 10')]"
```

預期看到 trainer 卡日中對照：
```
{'name_jp': '博士の研究', 'name_en': "Professor's Research", 'name_zh': '博士的研究', 'category': 'trainer'}
{'name_jp': 'ボスの指令', 'name_en': "Boss's Orders", 'name_zh': '老大的命令', 'category': 'trainer'}
...
```

- [ ] **Step 3.5: commit app/database.py 改動**

```powershell
git add app/database.py
git commit -m "$(cat <<'EOF'
db: jp_term_dict 加 name_zh 欄位

從現有 _jp_zh_translations.json 抽 trainer 條目回填到 jp_term_dict.name_zh、
讓新翻譯函式可以查 jp_term_dict 拿 trainer/item 中文。同步更新 init_db()。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 寫 unit test（test-first、TDD）

**Files:**
- Create: `_test_translate_zh.py`

- [ ] **Step 4.1: 寫測試框架（不靠 pytest、用 plain assert）**

CLAUDE.md 寫「沒有正式 test 套件」、不引 pytest，用 plain `assert` + 自己印 PASS/FAIL。

Create `_test_translate_zh.py`：

```python
"""Unit test 給 _translate_jp_card_name_to_zh（async function）。

跑法：./Python/bin/python.exe _test_translate_zh.py
"""
import asyncio
import sys
import aiosqlite

DB = "cards.db"


async def main():
    from app.main import _translate_jp_card_name_to_zh

    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row

        cases = [
            # 純寶可夢
            ("ピカチュウ",                "皮卡丘"),
            ("リザードン",                "噴火龍"),
            ("ミュウツー",                "超夢"),

            # base + 後綴
            ("ピカチュウex",              "皮卡丘ex"),
            ("リザードンVMAX",            "噴火龍VMAX"),
            ("ピカチュウV",               "皮卡丘V"),
            ("リザードンVSTAR",           "噴火龍VSTAR"),
            ("カイリューGX",              "快龍GX"),

            # 地區形（連寫、無空格）
            ("アローラ ディグダ",          "阿羅拉地鼠"),
            ("ガラル ヤドン",              "伽勒爾呆呆獸"),
            ("ヒスイ ゾロア",              "洗翠索羅亞"),
            ("パルデア ケンタロス",        "帕底亞肯泰羅"),
            ("アローラ ライチュウGX",      "阿羅拉雷丘GX"),

            # Mega
            ("メガフシギバナEX",          "Mega妙蛙花EX"),
            ("メガリザードン",            "Megaリザードン"),  # core 沒查到、應 None

            # HTML megamark
            ('<span class="pcg pcg-megamark"></span>リザードン',  "Mega噴火龍"),

            # 人物の
            ("ロケット団のミュウツー",    "火箭隊的超夢"),  # 若 jp_term_dict 有 ロケット団 → 火箭隊

            # 邊界
            ("",                            None),
            (None,                          None),
            ("非常奇怪的卡名沒對應",         None),
        ]

        passed = 0
        failed = 0
        for jp, expected in cases:
            actual = await _translate_jp_card_name_to_zh(jp, db)
            if actual == expected:
                print(f"  PASS  jp={jp!r:40} -> {actual!r}")
                passed += 1
            else:
                print(f"  FAIL  jp={jp!r:40} expected={expected!r} actual={actual!r}")
                failed += 1
        print()
        print(f"{passed} passed, {failed} failed")
        sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4.2: 跑測試確認全 FAIL**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_translate_zh.py
```

預期：
```
ImportError: cannot import name '_translate_jp_card_name_to_zh' from 'app.main'
```

或：
```
NameError: name '_translate_jp_card_name_to_zh' is not defined
```

→ 確認 test 在「函式還沒寫」階段預期失敗。

---

## Task 5: 實作 _translate_jp_card_name_to_zh、讓 test 通過

**Files:**
- Modify: `app/main.py`（在 `_translate_jp_card_name_to_en` 之後 line ~1975 加新函式）

- [ ] **Step 5.1: Read app/main.py line 1860-1980 確認既有常數位置**

既有常數（line 1864-1876）：
```python
_JP_CHAR_RE        = re.compile(r'[ぁ-ゟ゠-ヿ一-龯]')
_CARD_SUFFIX_RE    = re.compile(r'(VMAX|VSTAR|VUNION|V-UNION|GMAX|GX|EX|ex|V)$')
_HTML_TAG_RE       = re.compile(r'<[^>]+>')
_MEGA_HTML_RE      = re.compile(r'pcg-megamark')
_REGIONAL_PREFIXES = [('ガラル ', 'Galarian'), ('アローラ ', 'Alolan'), ('ヒスイ ', 'Hisuian'), ('パルデア ', 'Paldean'), ('ガラルの', 'Galarian'), ('ヒスイの', 'Hisuian')]
_TEAM_ROCKET_PREFIX = 'ロケット団の'
```

新函式用同一批正則 reuse、加一份 ZH 對應的 prefix list（不要動 `_REGIONAL_PREFIXES` 的 EN label）。

- [ ] **Step 5.2: 在 app/main.py:1877 加 _REGIONAL_PREFIXES_ZH 常數**

Edit tool 在 `_TEAM_ROCKET_PREFIX = 'ロケット団の'` 之後加：

```python
# JP→ZH 地區形 prefix（連寫、無空格、user 偏好）
_REGIONAL_PREFIXES_ZH = [
    ('ガラル ', '伽勒爾'),
    ('アローラ ', '阿羅拉'),
    ('ヒスイ ', '洗翠'),
    ('パルデア ', '帕底亞'),
    ('ガラルの', '伽勒爾'),
    ('ヒスイの', '洗翠'),
]
```

- [ ] **Step 5.3: 在 app/main.py:1975 之後（_translate_jp_card_name_to_en 結束處）加新函式**

Edit tool 加：

```python
async def _translate_jp_card_name_to_zh(card_name_jp: str, db) -> str | None:
    """JP→ZH 翻譯（per-pokemon 對映、跨 set 通用）。

    順序：
      1. HTML strip (Bulbapedia <span class='pcg pcg-megamark'></span> → Mega marker)
      2. 人物の prefix → 查 jp_term_dict.name_zh、加「的」
      3. メガ prefix → Mega（保留英文）
      4. 地區形 prefix (ガラル/アローラ/ヒスイ/パルデア) → 伽勒爾/阿羅拉/洗翠/帕底亞
      5. 後綴抽取（VMAX/VSTAR/GMAX/GX/EX/ex/V 保留英文）
      6. core 查找 pokemon_dict.name_zh、miss 再查 jp_term_dict.name_zh
      7. 全名 fallback 查 jp_term_dict.name_zh
      8. 全 miss → None（caller 走純日文路徑）

    所有飾詞跟 core 之間 **連寫無空格**（user 偏好、跟 EN 版的 join(' ') 不同）。
    """
    if not card_name_jp:
        return None

    raw = card_name_jp.strip()

    # 1. HTML
    is_mega = bool(_MEGA_HTML_RE.search(raw))
    name = _HTML_TAG_RE.sub('', raw).strip()

    # 2. 人物の prefix（先試短前綴、用 jp_term_dict 查）
    char_prefix_zh = None
    if 'の' in name:
        cut = name.index('の')
        char_jp = name[:cut]
        rest = name[cut + 1:].strip()
        if char_jp and rest:
            cur = await db.execute(
                "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (char_jp,)
            )
            row = await cur.fetchone()
            if row and row[0]:
                char_prefix_zh = row[0] + '的'
                name = rest

    # 3. Mega
    if name.startswith('メガ'):
        is_mega = True
        name = name[2:].strip()

    # 4. Regional ZH
    regional_zh = None
    for prefix, label in _REGIONAL_PREFIXES_ZH:
        if name.startswith(prefix):
            regional_zh = label
            name = name[len(prefix):].strip()
            break

    # 5. Suffix
    m = _CARD_SUFFIX_RE.search(name)
    suffix = m.group(0) if m else ''
    core = (name[:-len(suffix)] if suffix else name).strip()

    zh_core = None
    if core:
        if _JP_CHAR_RE.search(core):
            # 6. pokemon_dict.name_zh 優先
            cur = await db.execute(
                "SELECT name_zh FROM pokemon_dict WHERE name_jp = ? LIMIT 1", (core,)
            )
            row = await cur.fetchone()
            if row and row[0]:
                zh_core = row[0]
            else:
                # jp_term_dict.name_zh
                cur = await db.execute(
                    "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (core,)
                )
                row = await cur.fetchone()
                if row and row[0]:
                    zh_core = row[0]

    # 7. 全名 fallback（純文字題卡）
    if not zh_core:
        cur = await db.execute(
            "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (raw,)
        )
        row = await cur.fetchone()
        if row and row[0]:
            zh_core = row[0]
            # 全名查到、不再加飾詞
            char_prefix_zh = None
            is_mega = False
            regional_zh = None
            suffix = ''

    if not zh_core:
        return None

    # 8. 組合（連寫無空格）
    out = ''
    if char_prefix_zh:
        out += char_prefix_zh
    if is_mega:
        out += 'Mega'
    if regional_zh:
        out += regional_zh
    out += zh_core
    out += suffix
    return out
```

- [ ] **Step 5.4: 跑 test 確認全 PASS**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_translate_zh.py
```

預期所有寶可夢 case PASS、人物の case 看 jp_term_dict 有沒有「ロケット団」這條。

如果 ロケット団 case FAIL，是 Task 3 沒回填到、屬於 expected 行為（_jp_zh_translations.json 沒對應條目）— 標記為 known gap、後續手工補。

`メガリザードン` 預期 → "Mega噴火龍"。如果 case 寫「應 None」、修正 test。

如有實際 FAIL，回去檢查 Task 5 邏輯。

- [ ] **Step 5.5: commit app/main.py 新函式**

```powershell
git add app/main.py
git commit -m "$(cat <<'EOF'
main: 加 _translate_jp_card_name_to_zh 跨 set 通用翻譯函式

仿 _translate_jp_card_name_to_en 結構、走 per-pokemon 對映：
人物の 前綴 + メガ + 地區形 + 後綴抽取 + pokemon_dict.name_zh 查找。
飾詞跟 core 之間連寫無空格（user 偏好「阿羅拉地鼠」「Mega妙蛙花」格式）。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 改 get_cards_by_set jp 分支套新函式

**Files:**
- Modify: `app/database.py` line ~793 (`_JP_ZH_LOOKUP.get(...)` 替換)

- [ ] **Step 6.1: Read app/database.py:780-800 確認既有 lookup 邏輯**

預期既有：
```python
for r in rows:
    u = r.get("image_url")
    if u and u.startswith("/"):
        r["image_url"] = "https://www.pokemon-card.com" + u
    r["name"] = r.get("name_jp")
    cn_key = _norm_card_num_for_zh(r.get("card_number"))
    r["name_zh"] = _JP_ZH_LOOKUP.get(f"{set_id}/{cn_key}")
    r["language"] = "jp"
```

- [ ] **Step 6.2: 改成新管線優先、舊字典 fallback**

`get_cards_by_set` 是 async function、它有 `db` connection 在 scope 內（看 line ~700 應該有 `async with aiosqlite.connect(...)`、或接 db 參數）。

先檢查既有 signature：

```powershell
Get-Content app/database.py | Select-String -Pattern "async def get_cards_by_set" -Context 0,5
```

如果 db 已在 scope（local async with），直接用。如果沒有、要在函式內開 db connection。

Edit tool 把 `r["name_zh"] = _JP_ZH_LOOKUP.get(f"{set_id}/{cn_key}")` 改成：

```python
from app.main import _translate_jp_card_name_to_zh
zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
if not zh:
    cn_key = _norm_card_num_for_zh(r.get("card_number"))
    zh = _JP_ZH_LOOKUP.get(f"{set_id}/{cn_key}")
r["name_zh"] = zh
```

注意：`from app.main import _translate_jp_card_name_to_zh` 移到 function body 內、避免 circular import（app.main 已 import app.database）。

- [ ] **Step 6.3: 重啟 API 驗證**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

打 set 詳情頁、確認 jp 卡有中文：

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import urllib.request, json; d=json.loads(urllib.request.urlopen('http://localhost:8000/api/cardlist/sets/949').read()); [print(c.get('card_number'), c.get('name'), '|', c.get('name_zh')) for c in d.get('cards', [])[:10]]"
```

預期 jp 卡前 10 條 name_zh 都有值（不是 None）。

- [ ] **Step 6.4: commit**

```powershell
git add app/database.py
git commit -m "$(cat <<'EOF'
db: get_cards_by_set jp 分支套新翻譯管線

從原本只查 _JP_ZH_LOOKUP per-card lookup、改成「新管線優先 + 舊字典 fallback」。
新管線跨 set 通用、覆蓋大多含寶可夢主體的卡。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 改 search_cards_in_list jp 分支套新函式

**Files:**
- Modify: `app/database.py:876-900` (`_search_jp` 內部、JP rows 的 name_zh=None 那行)

- [ ] **Step 7.1: Read app/database.py:876-900**

既有 _search_jp 內部：
```python
rows = [dict(r) for r in await cur.fetchall()]
for r in rows:
    r["name"] = r.get("name_jp")
    r["name_zh"] = None
    r["language"] = "jp"
return rows
```

- [ ] **Step 7.2: 替換 name_zh=None 那行**

Edit tool 改成：

```python
rows = [dict(r) for r in await cur.fetchall()]
from app.main import _translate_jp_card_name_to_zh
for r in rows:
    r["name"] = r.get("name_jp")
    zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
    if not zh:
        set_id = r.get("set_id")
        cn_key = _norm_card_num_for_zh(r.get("card_number"))
        zh = _JP_ZH_LOOKUP.get(f"{set_id}/{cn_key}")
    r["name_zh"] = zh
    r["language"] = "jp"
return rows
```

- [ ] **Step 7.3: 重啟 API、驗證搜尋頁有中文**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

打 search endpoint：

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import urllib.request, json; d=json.loads(urllib.request.urlopen('http://localhost:8000/api/cardlist/search?q=ピカチュウ&language=jp').read()); [print(c.get('card_number'), c.get('name'), '|', c.get('name_zh')) for c in d.get('cards', [])[:10]]"
```

預期前 10 條中含「皮卡丘」等中文。

- [ ] **Step 7.4: commit**

```powershell
git add app/database.py
git commit -m "$(cat <<'EOF'
db: search_cards_in_list jp 分支套新翻譯管線

搜尋頁原本 jp 卡 name_zh=None、改成走新翻譯函式 + _JP_ZH_LOOKUP fallback。
首次讓搜尋頁 jp 卡看得到中文。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 改 category endpoint 套新函式

**Files:**
- Modify: `app/main.py:2871` (`category_pokemon_cards`)
- Modify: `app/main.py:2916` (`category_character_cards`)

- [ ] **Step 8.1: 確認既有 SQL（jp-* 跟 en-* 都會回）**

兩個函式都從 `card_list cl` 撈、含 jp-* 跟 en-* row。要對 set_id LIKE 'jp-%' 的 row 套翻譯（這些 row 走 artofpkm 體系、name_zh 可能是 NULL 或舊翻譯）。

但注意 PROGRESS.md Known Pitfalls #34：card_list jp-* 體系跟 jp_card_list 編號不同步、不能用 (set_id, card_number) 對 _JP_ZH_LOOKUP（會 mismatch）。**改用走新函式（per-pokemon、不依賴 set_id）+ name_jp 查 pokemon_dict**。

- [ ] **Step 8.2: 改 category_pokemon_cards**

在 `rows = await ...fetchall()` 之後（main.py:~2907 附近、return 之前）加 post-process：

```python
rows = await (await db.execute(sql, params)).fetchall()
out_rows = [dict(r) for r in rows]
for r in out_rows:
    if r.get("set_id", "").startswith("jp-"):
        zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
        if zh:
            r["name_zh"] = zh
        # 若 zh 沒翻到、保留 card_list.name_zh 既有值（不蓋 NULL）

return {
    "pokemon": {"id": pkm_id, "name_en": name_en, "name_jp": name_jp},
    "count": len(out_rows),
    "cards": out_rows,
}
```

Edit tool 操作：把原本 `[dict(r) for r in rows]` 那行（在 return 內）整段抽出來、改成上面結構。

- [ ] **Step 8.3: 改 category_character_cards（同 Task 8.2 邏輯）**

同樣對 category_character_cards 的 return 加 post-process：

```python
rows = await (await db.execute(sql, params)).fetchall()
out_rows = [dict(r) for r in rows]
for r in out_rows:
    if r.get("set_id", "").startswith("jp-"):
        zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
        if zh:
            r["name_zh"] = zh

return {
    "character": {"id": char_id, "name_en": name_en, "name_jp": name_jp},
    "count": len(out_rows),
    "cards": out_rows,
}
```

- [ ] **Step 8.4: 重啟 API、驗證分類頁有中文**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

打 category endpoint（pikachu id=25）：

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "import urllib.request, json; d=json.loads(urllib.request.urlopen('http://localhost:8000/api/category/pokemon/25/cards').read()); jp_cards=[c for c in d.get('cards', []) if c.get('set_id','').startswith('jp-')]; [print(c.get('set_id'), c.get('card_number'), c.get('name'), '|', c.get('name_zh')) for c in jp_cards[:10]]"
```

預期 jp-* set 的卡前 10 條多數有 name_zh = 皮卡丘 / 皮卡丘 V / 皮卡丘 VMAX 等。

- [ ] **Step 8.5: commit**

```powershell
git add app/main.py
git commit -m "$(cat <<'EOF'
main: category endpoint 套新翻譯管線

寶可夢分類頁 / 訓練家分類頁原本只看 card_list.name_zh（artofpkm 體系、
9.9% hit）、改成對 jp-* set 的 row 套 _translate_jp_card_name_to_zh、
走 per-pokemon 對映、覆蓋大幅提升。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: playwright 端到端驗證 3 個頁面

**Files:** 無（純驗證）

- [ ] **Step 9.1: 開瀏覽器看 set 詳情頁 pg=949**

`mcp__plugin_playwright_playwright__browser_navigate` 開 `http://localhost:8080/index.html#/set?set=949`

`browser_snapshot` 確認 cardItemHtml 顯示「日文 (中文)」格式、所有卡都有中文（spot-check 前 18 張）。

預期類似 PROGRESS.md 寫的：「ナゾノクサ (走路草) / クサイハナ (臭臭花) / ラフレシア (霸王花) / メガヘラクロスex (Mega赫拉克羅斯ex)」。

- [ ] **Step 9.2: 開搜尋頁、搜「ピカチュウ」**

`browser_navigate` 開 `http://localhost:8080/index.html#/search?q=ピカチュウ`

`browser_snapshot` 確認 jp 卡都有「日文 (中文)」格式顯示。

- [ ] **Step 9.3: 開分類頁 #/category/pokemon/25 (皮卡丘)**

`browser_navigate` 開 `http://localhost:8080/index.html#/category/pokemon/25`

`browser_snapshot` 確認 jp-* set 的卡都有中文（之前是純日文）。

- [ ] **Step 9.4: 開分類頁 #/category/character/1 (任一訓練家)**

確認 trainer 卡有中文（看 jp_term_dict 回填多少）。

- [ ] **Step 9.5: 抽 30 張 jp 卡人工目測翻譯正確率**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe -c "
import urllib.request, json, random
random.seed(42)
all_cards = []
for pg in ['949', '950', '951', '952', '9001']:
    d = json.loads(urllib.request.urlopen(f'http://localhost:8000/api/cardlist/sets/{pg}').read())
    all_cards.extend(d.get('cards', []))
sample = random.sample([c for c in all_cards if c.get('name_zh')], 30)
for c in sample:
    print(c.get('card_number'), c.get('name'), '|', c.get('name_zh'))
"
```

人工檢視 30 條對照、確認翻譯正確率 ≥ 95%。

- [ ] **Step 9.6: 關 browser**

`mcp__plugin_playwright_playwright__browser_close`

---

## Task 10: miss 盤點、輸出 50 條表給 user

**Files:**
- Create: `_audit_jp_zh_missing.py`
- Create: `_jp_zh_missing.md`

- [ ] **Step 10.1: 寫 _audit_jp_zh_missing.py**

Create file:

```python
"""全量盤點 jp 卡翻譯 miss、湊 50 個輸出 markdown 表。

範圍：jp_card_list (21,552 卡) + card_list jp-* (27,108 卡)
做法：對每張卡跑 _translate_jp_card_name_to_zh、回 None 就進 miss list
輸出：_jp_zh_missing.md
"""
import asyncio
import sys
import aiosqlite

DB = "cards.db"
MAX_MISS = 50
OUT_PATH = "_jp_zh_missing.md"


async def main():
    from app.main import _translate_jp_card_name_to_zh

    miss = []
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row

        # jp_card_list 體系
        async for r in await db.execute(
            """SELECT jcl.pg, jcl.card_number, jcl.name_jp,
                      jcls.name_jp AS set_name_jp
               FROM jp_card_list jcl
               LEFT JOIN jp_card_list_set jcls ON jcls.pg = jcl.pg
               ORDER BY CAST(jcl.pg AS INTEGER) DESC, jcl.card_number"""
        ):
            if len(miss) >= MAX_MISS:
                break
            zh = await _translate_jp_card_name_to_zh(r["name_jp"], db)
            if zh is None:
                miss.append({
                    "source": "jp_card_list",
                    "set_name": r["set_name_jp"] or "",
                    "pg": r["pg"],
                    "card_number": r["card_number"],
                    "name_jp": r["name_jp"],
                })

        # 若還沒湊滿 50、補 card_list jp-*
        if len(miss) < MAX_MISS:
            async for r in await db.execute(
                """SELECT cl.set_id, cl.card_number, cl.name_jp,
                          cs.name_jp AS set_name_jp
                   FROM card_list cl
                   LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
                   WHERE cl.set_id LIKE 'jp-%' AND cl.name_jp IS NOT NULL
                     AND (cl.name_zh IS NULL OR cl.name_zh = '')
                   ORDER BY cl.set_id, cl.card_number"""
            ):
                if len(miss) >= MAX_MISS:
                    break
                zh = await _translate_jp_card_name_to_zh(r["name_jp"], db)
                if zh is None:
                    miss.append({
                        "source": "card_list",
                        "set_name": r["set_name_jp"] or r["set_id"],
                        "pg": r["set_id"],
                        "card_number": r["card_number"],
                        "name_jp": r["name_jp"],
                    })

    # 輸出 markdown
    lines = [
        "# JP→ZH 翻譯 miss 表（前 {} 筆）".format(len(miss)),
        "",
        "> 自動產出、給 user 看哪些 jp 卡新管線翻不到。",
        "> 補翻譯方式：把對應 (name_jp → name_zh) 寫進 pokemon_dict 或 jp_term_dict、",
        "> 或加 per-card override 進 `_jp_zh_translations.json`。",
        "",
        "| # | 來源 | set | pg/set_id | 卡號 | 日文卡名 |",
        "|---|---|---|---|---|---|",
    ]
    for i, m in enumerate(miss, 1):
        lines.append(f"| {i} | {m['source']} | {m['set_name']} | {m['pg']} | {m['card_number']} | {m['name_jp']} |")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"寫入 {OUT_PATH}：{len(miss)} 條 miss")
    print()
    print("前 10 條 preview：")
    for m in miss[:10]:
        print(f"  {m['source']} | {m['pg']} | #{m['card_number']} | {m['name_jp']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 10.2: 跑腳本**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _audit_jp_zh_missing.py
```

預期：
```
寫入 _jp_zh_missing.md：50 條 miss
前 10 條 preview：
  jp_card_list | 953 | #138 | 妮莫の特訓
  jp_card_list | 953 | #142 | ...
  ...
```

- [ ] **Step 10.3: 給 user 看 markdown**

把 `_jp_zh_missing.md` 內容貼給 user、或直接 SendUserFile（看 task 結束怎麼交付）。

User 看完後：
- 決定要補哪些（手動寫進 pokemon_dict / jp_term_dict / `_jp_zh_translations.json`）
- 補完重跑 audit、產下一輪 50 條表
- 循環直到 user 滿意

本 task 「自動化部分」完成 = audit 腳本能跑、產表給 user 看就結束。

---

## Final Check

- [ ] **Step F.1: 跑全部 unit test 確認沒倒退**

```powershell
PYTHONIOENCODING=utf-8 ./Python/bin/python.exe _test_translate_zh.py
```

預期全 PASS。

- [ ] **Step F.2: 看 git log 確認 commit 拆乾淨**

```powershell
git log --oneline -10
```

預期看到分開的 commit：
- `db: pokemon_dict 加 name_zh 欄位`
- `db: jp_term_dict 加 name_zh 欄位`
- `main: 加 _translate_jp_card_name_to_zh 跨 set 通用翻譯函式`
- `db: get_cards_by_set jp 分支套新翻譯管線`
- `db: search_cards_in_list jp 分支套新翻譯管線`
- `main: category endpoint 套新翻譯管線`

5-6 個 commit、每個語意分明。

- [ ] **Step F.3: 把 _jp_zh_missing.md 內容貼給 user、本 plan 結束**

跟 user 講：
1. 翻譯管線已上線、set 詳情 + 搜尋 + 分類三個頁面 jp 卡都看得到中文
2. 涵蓋率從 ~45% 提到 估 70-85%（user 看 spot-check 30 張驗證）
3. miss 50 條表已存 `_jp_zh_missing.md`、user 決定要補哪些

---

## 估時對照

| Task | 估時 |
|---|---|
| 1. 爬 wiki + 產 JSON | 30 min |
| 2. pokemon_dict + apply | 20 min |
| 3. jp_term_dict + 回填 | 30 min |
| 4. 寫 unit test | 30 min |
| 5. 實作翻譯函式 | 30 min |
| 6. get_cards_by_set | 15 min |
| 7. search_cards_in_list | 15 min |
| 8. category endpoint | 15 min |
| 9. playwright 驗證 + 30 張 spot-check | 30 min |
| 10. miss audit | 20 min |
| **總計** | **約 3.5-4 小時** |
