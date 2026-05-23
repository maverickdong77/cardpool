# JP Set ID 統一改名 Implementation Plan

> ⚠️ **2026-05-22 status：未動工、僅供未來參考。** User 後來表示真正需求是「對照表」而非「rename DB」、改交付 `docs/jp_sets_lookup.md`。此 plan 留作日後若真要改 pg → set_code/slug 的完整 cascade 設計參考。
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 jp_card_list_set 的 368 個純數字 pg（如 `9001`、`949`、`950`），全面改名為 `set_code`（如 `SV-P`、`M2`、`M2a`）或 `jp-XXX` slug（如 `jp-Inferno-X`、`jp-Mega-Brave`），讓全系統 set 識別符跟 card_sets 既有的 `jp-XXX` 命名統一。同步合併 9003→9001 + 修首頁排序。

**Architecture:** 三階段：
1. **Phase 0–2（design + dry-run，0 DB 寫入）**：產出 359 個 pg 的 mapping 表 → user 人工 review → 在 cards.db 副本上 dry-run、驗 row count
2. **Phase 3–6（DB cascade migration）**：backup → atomic UPDATE 10 張表 → 重啟 API + 驗證
3. **Phase 7–8（順手活）**：9003 合併進 9001、首頁排序修正

**Tech Stack:** SQLite + Python (aiosqlite/sqlite3) + FastAPI backend + 純 HTML/JS frontend。Cascade migration 用單一 BEGIN IMMEDIATE transaction。

---

## 設計依據與盤點數據

### 範圍

| 表 | 欄位 | 受影響 row 數 | 備註 |
|---|---|---|---|
| `jp_card_list_set` | pg (PK) | **368** | 主表 |
| `jp_card_list` | pg, set_code | **21,552** | 卡明細 |
| `jp_card_pg_link` | pg | **22,173** | junction 表（含一卡多 pg）|
| `jp_set_era_map` | pg (INT), set_code | **368** | era 對映 |
| `card_sets` | set_id | **4** | 只 9001/9002/9003/950 用純數字 |
| `card_list` | set_id | **680** | 同上 4 個合成 pg 寫進來的 |
| `card_volume_stats` | set_id | **250** | 只 950 用 |
| `card_prices` | set_id | **372,375** ⚠️ | 全部純數字 pg 的價格資料 |

**不需動的表**（雖然有 set_id 但不是 pg）：
- `artofpkm_cards.set_id`（artofpkm.com internal id、不是我們的 pg）
- `card_sync_history.set_id`（全 jp-XXX，0 純數字）
- `en_card_list*` / `pricecharting_*` / `psa_apr_card_mapping`（純英文系列、不涉及 jp pg）
- `snkrdunk_mapping.set_code`（已用 set_code、不是 pg）

### 359 個有效 pg 的 mapping 來源

| 來源 | 覆蓋率 | 策略 |
|---|---|---|
| 有 dominant set_code | 280/359 (78%) | **首選 set_code**（如 SV-P、M2a、s11、sv1） |
| 多 set_code 混雜 | ≥20 個 | 改用 jp-XXX slug（多 set_code 代表「產品」、不是「set」） |
| 無 set_code | 79/359 (22%) | **必須**從 name_jp 生成 jp-XXX slug |

### 命名規則（候選 — 待 Phase 0 user 決策）

**選項 A（推薦）**：以 set_code 為主、缺則生 slug
- pg=949 → `M2` (set_code 'M2' dominant)
- pg=950 → `M2a` (合成 pg → set_code)
- pg=9001 → `SV-P` (合成 + 合併 9003)
- pg=27（無 set_code）→ `jp-DPP-Heatran-vs-Regigigas` (slug)
- 優：set_code 短、有官方背書
- 缺：set_code 與 slug 兩種風格混存

**選項 B**：全部統一 `jp-XXX` slug 格式
- pg=949 → `jp-Inferno-X`
- pg=950 → `jp-MEGA-Dream-ex`
- pg=9001 → `jp-Scarlet-Violet-Promos`
- pg=27 → `jp-DPP-Heatran-vs-Regigigas`
- 優：與 card_sets 既有 458 個 jp-XXX 完全一致
- 缺：set_code 資訊吃進 slug、URL 變長

**Phase 0 Task 1 開頭，user 要明確選 A 或 B**。本 plan 後面所有範例用 **選項 A**（如選 B、各 Task 把 set_code 改成 slug 即可）。

---

## Phase 0: Mapping Sheet 設計與生成

### Task 0.1: 決定命名規則（user 決策、5 分鐘）

**Files:** 無

- [ ] **Step 1: 跟 user 確認三件事**

問題清單（用 AskUserQuestion）：
1. 命名規則 A（set_code+slug 混搭）vs B（全 slug）
2. slug 風格：`jp-Inferno-X`（mixed case）vs `jp-inferno-x`（lowercase）— 既有 card_sets 用 mixed case
3. 9003 確認合併進 9001 (兩個都 set_code=SV-P)

- [ ] **Step 2: 記錄在 plan top 的「命名規則」section**

把決策結果寫進 plan、後續 Task 引用此規則生 mapping。

### Task 0.2: 生成 mapping 候選表（自動腳本）

**Files:**
- Create: `_pg_rename_mapping_gen.py`（生 mapping 候選）
- Create: `_pg_rename_mapping.csv`（產出檔、給 user 編輯）

- [ ] **Step 1: 寫 mapping 生成器腳本**

```python
# _pg_rename_mapping_gen.py
import sqlite3
import re
import csv
from collections import Counter

c = sqlite3.connect('cards.db')

# 對每個 pg，產出候選新 ID + 來源
rows = c.execute("""
    SELECT j.pg, j.name_jp, j.hit_cnt
    FROM jp_card_list_set j
    WHERE (j.name_jp IS NOT NULL AND j.name_jp != '')
      AND j.name_jp NOT LIKE '%」「%'
      AND j.name_jp NOT LIKE '%amazon%'
    ORDER BY CAST(j.pg AS INTEGER)
""").fetchall()

def derive_set_code(pg):
    """撈 pg 在 jp_card_list 的 dominant set_code + purity (該 code 占該 pg 卡數比例)"""
    codes = c.execute(
        "SELECT set_code, COUNT(*) FROM jp_card_list WHERE pg=? AND set_code IS NOT NULL AND set_code != '' GROUP BY set_code",
        (pg,)
    ).fetchall()
    if not codes:
        return None, 0.0
    total = sum(n for _, n in codes)
    codes.sort(key=lambda x: -x[1])
    dominant_code, dominant_n = codes[0]
    return dominant_code, dominant_n / total

def derive_slug_from_namejp(name_jp):
    """從 name_jp 提取「主要 set 名」生 slug。
    name_jp 格式: '日文 (中文翻譯)' 或多段
    1. 抓第一個 「」 內容（拡張パック「XXX」 → XXX）
    2. 或抓 '日文' 部分（去括號中文）
    3. romaji 化（手動 mapping 表）+ kebab-case
    """
    # 抓「」內主名
    m = re.search(r'「([^」]+)」', name_jp)
    if m:
        core_jp = m.group(1)
    else:
        # 沒「」，取第一個 ' (' 之前
        core_jp = name_jp.split(' (')[0].strip()
    # 簡單 ASCII-friendly slug（先用 JP 直接、Phase 0 review 時 user 補英文 slug）
    slug_seed = core_jp
    return slug_seed

# 命名規則 A：dominant set_code purity ≥0.7 → set_code；否則 slug
out = []
for pg, name_jp, hit_cnt in rows:
    code, purity = derive_set_code(pg)
    slug_seed = derive_slug_from_namejp(name_jp)
    if code and purity >= 0.7:
        proposed = code
        reason = f'set_code (purity={purity:.2f})'
    else:
        proposed = f'jp-{slug_seed}'  # JP slug needs human translation
        reason = f'slug from name_jp (set_code purity={purity:.2f}, code={code})'
    out.append({
        'pg': pg,
        'hit_cnt': hit_cnt,
        'name_jp': name_jp,
        'dominant_set_code': code or '',
        'set_code_purity': f'{purity:.2f}',
        'slug_seed': slug_seed,
        'proposed_new_id': proposed,
        'reason': reason,
        'approved_new_id': '',  # user 填這欄
        'note': '',
    })

with open('_pg_rename_mapping.csv', 'w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=list(out[0].keys()))
    writer.writeheader()
    writer.writerows(out)

print(f'wrote _pg_rename_mapping.csv with {len(out)} rows')
```

- [ ] **Step 2: 跑腳本**

```powershell
./Python/bin/python.exe _pg_rename_mapping_gen.py
```

Expected: `wrote _pg_rename_mapping.csv with 359 rows`

- [ ] **Step 3: 印前 30 row + 後 10 row 給 user 看 sample**

```powershell
Import-Csv _pg_rename_mapping.csv | Select-Object -First 30 | Format-Table pg, dominant_set_code, set_code_purity, proposed_new_id, name_jp -AutoSize
```

### Task 0.3: User Review Mapping CSV

**Files:**
- Modify: `_pg_rename_mapping.csv`（user 編輯 `approved_new_id` 欄）

- [ ] **Step 1: User 編輯 CSV**

User 用 Excel/Notepad++ 開 `_pg_rename_mapping.csv`、檢查每一行：
- 對 set_code purity ≥ 0.95 的、proposed_new_id 通常可直接 copy 到 approved_new_id
- 對 slug seed 是 JP 的、需要手動翻譯成英文 slug（如「インフェルノX」→ `jp-Inferno-X`）
- 對多 set_code 混雜的（如 pg=301）、需要 user 決定產品名稱

- [ ] **Step 2: 寫驗證腳本檢查 user 編輯結果**

Create: `_pg_rename_mapping_verify.py`

```python
import csv
from collections import Counter

with open('_pg_rename_mapping.csv', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

issues = []
approved_ids = []
for r in rows:
    new_id = r['approved_new_id'].strip()
    if not new_id:
        issues.append(f"pg={r['pg']} 缺 approved_new_id")
        continue
    # 規則：set_code (短代碼) 或 jp-XXX
    if not (new_id.startswith('jp-') or re.match(r'^[A-Za-z][A-Za-z0-9\-]{0,15}$', new_id)):
        issues.append(f"pg={r['pg']} new_id='{new_id}' 格式不對")
    approved_ids.append(new_id)

# 檢查唯一性（除了 9001/9003 的重複故意外）
dup_check = Counter(approved_ids)
dups = {k: v for k, v in dup_check.items() if v > 1}
if dups:
    issues.append(f'重複 new_id: {dups}')

if issues:
    print('=== ISSUES ===')
    for i in issues:
        print(i)
    raise SystemExit(1)

print(f'OK: {len(rows)} mapping rows pass validation')
print(f'unique new_id: {len(dup_check)}')
```

- [ ] **Step 3: 跑驗證、確認 0 issue**

```powershell
./Python/bin/python.exe _pg_rename_mapping_verify.py
```

Expected: `OK: 359 mapping rows pass validation`

如果有 issue、user 改 CSV 後 re-run、直到 pass。

### Task 0.4: 把 mapping 載入成 Python dict + commit 給後續 task 用

**Files:**
- Create: `_pg_rename_mapping.py`（mapping 資料）

- [ ] **Step 1: 從 CSV 載入成 dict、寫成 module**

```python
# _pg_rename_mapping.py — auto-generated from _pg_rename_mapping.csv
PG_RENAME_MAP = {
    # pg (str) → new_id (str)
    "1": "DP1",
    "2": "DP1-S",
    # ... 359 entries
    "9001": "SV-P",
    "9002": "M-P",
    "9003": "SV-P",  # merged into 9001 (same target)
    "950": "M2a",
    "951": "MC",
    "952": "M3",
    "953": "M4",
}

# 反向 mapping (給 rollback 用)
PG_RESTORE_MAP = {v: k for k, v in PG_RENAME_MAP.items() if v != "SV-P"}  # SV-P 衝突要特殊處理
```

實際從 CSV 生成：

```python
# _pg_rename_mapping_load.py
import csv

with open('_pg_rename_mapping.csv', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

with open('_pg_rename_mapping.py', 'w', encoding='utf-8') as f:
    f.write('# auto-generated from _pg_rename_mapping.csv\n')
    f.write('PG_RENAME_MAP = {\n')
    for r in rows:
        f.write(f'    "{r["pg"]}": "{r["approved_new_id"]}",\n')
    f.write('}\n')

print('wrote _pg_rename_mapping.py')
```

```powershell
./Python/bin/python.exe _pg_rename_mapping_load.py
```

- [ ] **Step 2: 驗 import 可用**

```powershell
./Python/bin/python.exe -c "from _pg_rename_mapping import PG_RENAME_MAP; print(len(PG_RENAME_MAP), 'entries'); print(PG_RENAME_MAP['9001'], PG_RENAME_MAP['949'])"
```

Expected: `359 entries / SV-P M2`（或 user 設定的值）

---

## Phase 1: Code Path Scan + Change List

### Task 1.1: 找出所有 hardcoded 引用

**Files:**
- Create: `_pg_rename_codepaths.md`（記錄要改的所有檔案 + 行號）

- [ ] **Step 1: Grep 全 codebase 找硬編 pg 值**

```powershell
# 已知：app/database.py:718 hardcode '9001','9002','9003'
# 找其他可能的
```

對下列 pattern 在 `app/` 和 `卡波/` 下 grep：
- `'9001'` / `'9002'` / `'9003'` / `'950'` / `'951'`
- `pg\s*=\s*['"][0-9]+`
- `set_id\s*=\s*['"][0-9]+`
- `WHERE.*pg.*IN`

- [ ] **Step 2: 寫進 `_pg_rename_codepaths.md`**

每筆記 `file:line | snippet | 改法`。預期 ≤ 10 處需要改。

### Task 1.2: 識別「結構性 numeric pg 假設」的 code

**Files:** 無

- [ ] **Step 1: 找會把 pg 當 INTEGER 用的地方**

危險 pattern：
- `CAST(pg AS INTEGER)` — 排序用、改名後會 fail
- `j.pg + 0` 或類似算術運算
- `ORDER BY pg ASC`（純數字 vs 字串排序不同）

```
Grep target: CAST.*pg.*INTEGER, ORDER BY pg
```

- [ ] **Step 2: 寫進 codepaths.md「結構性改動」section**

預期會找到：
- `database.py:604` `CAST(j.pg AS INTEGER) ASC`（fallback 排序）
- 其他可能在 main.py 或 scraper

**結論**：改名後排序要改成「先 CAST 失敗就用字典序」或乾脆改用 release_date 排序（與 Phase 8 排序修正合併）。

---

## Phase 2: Dry-Run on cards.db Copy

### Task 2.1: 複製 DB + 寫 dry-run migration script

**Files:**
- Copy: `cards.db.dryrun-pg-rename`
- Create: `_pg_rename_apply.py`（cascade UPDATE script、可重用於 production）

- [ ] **Step 1: 複製 cards.db**

```powershell
Copy-Item cards.db cards.db.dryrun-pg-rename
```

- [ ] **Step 2: 寫 cascade migration script**

```python
# _pg_rename_apply.py
import sqlite3
import sys
from _pg_rename_mapping import PG_RENAME_MAP

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else 'cards.db'

print(f'applying to {DB_PATH}')
c = sqlite3.connect(DB_PATH)
c.execute('PRAGMA foreign_keys = OFF')  # SQLite 無 FK 約束、保險起見

# 記錄 before counts (validation)
def counts():
    return {
        'jp_card_list_set': c.execute('SELECT COUNT(*) FROM jp_card_list_set').fetchone()[0],
        'jp_card_list': c.execute('SELECT COUNT(*) FROM jp_card_list').fetchone()[0],
        'jp_card_pg_link': c.execute('SELECT COUNT(*) FROM jp_card_pg_link').fetchone()[0],
        'jp_set_era_map': c.execute('SELECT COUNT(*) FROM jp_set_era_map').fetchone()[0],
        'card_sets_numeric': c.execute("SELECT COUNT(*) FROM card_sets WHERE set_id GLOB '[0-9]*'").fetchone()[0],
        'card_list_numeric': c.execute("SELECT COUNT(*) FROM card_list WHERE set_id GLOB '[0-9]*'").fetchone()[0],
        'card_volume_stats_numeric': c.execute("SELECT COUNT(*) FROM card_volume_stats WHERE set_id GLOB '[0-9]*'").fetchone()[0],
        'card_prices_numeric': c.execute("SELECT COUNT(*) FROM card_prices WHERE set_id GLOB '[0-9]*'").fetchone()[0],
    }

before = counts()
print('=== before ===')
for k, v in before.items(): print(f'  {k}: {v:,}')

# Atomic transaction
c.execute('BEGIN IMMEDIATE')

# === 預處理 1: jp_set_era_map.pg INTEGER → TEXT (schema rebuild) ===
print('rebuilding jp_set_era_map with pg TEXT...')
c.execute('CREATE TABLE jp_set_era_map_new (pg TEXT PRIMARY KEY, set_code TEXT, era TEXT NOT NULL, source TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
c.execute('INSERT INTO jp_set_era_map_new SELECT CAST(pg AS TEXT), set_code, era, source, updated_at FROM jp_set_era_map')
c.execute('DROP TABLE jp_set_era_map')
c.execute('ALTER TABLE jp_set_era_map_new RENAME TO jp_set_era_map')

# === 預處理 2: 9003 → 9001 合併（特殊 case、含 composite PK 衝突處理）===
print('handling 9003 → 9001 merge...')
# 先看是否有 (cardID, pg='9001') 與 (cardID, pg='9003') 同 cardID 撞 (composite PK 衝突)
dup_cardids = [r[0] for r in c.execute("""
    SELECT cardID FROM jp_card_pg_link WHERE pg='9001'
    INTERSECT
    SELECT cardID FROM jp_card_pg_link WHERE pg='9003'
""").fetchall()]
if dup_cardids:
    print(f'  found {len(dup_cardids)} cardID in both 9001 and 9003 — deleting 9003 entries (9001 wins)')
    qmarks = ','.join('?' * len(dup_cardids))
    c.execute(f"DELETE FROM jp_card_pg_link WHERE pg='9003' AND cardID IN ({qmarks})", dup_cardids)

# 同樣邏輯處理 jp_card_list (PK 是 cardID 不是 composite、所以不會撞、UPDATE 即可)
c.execute("UPDATE jp_card_list SET pg='9001' WHERE pg='9003'")
c.execute("UPDATE jp_card_pg_link SET pg='9001' WHERE pg='9003'")
c.execute("UPDATE jp_set_era_map SET pg='9001' WHERE pg='9003'")  # 已是 TEXT
c.execute("DELETE FROM jp_card_list_set WHERE pg='9003'")
c.execute("DELETE FROM card_sets WHERE set_id='9003'")  # 4 rows 內的一個
c.execute("UPDATE card_list SET set_id='9001' WHERE set_id='9003'")
c.execute("UPDATE card_prices SET set_id='9001' WHERE set_id='9003'")

# === 主 rename loop — 對 358 個剩餘 pg（9003 已合併）===
# 注意：jp_card_pg_link composite PK (cardID, pg)。理論上不會有同 cardID 跨多 pg、
# 但保險起見、先檢查 mapping 是否會在 jp_card_pg_link 產生衝突
for old_pg, new_id in PG_RENAME_MAP.items():
    if old_pg == '9003':  # already merged above
        continue
    if old_pg == new_id:  # no-op (set_code 已等於 pg、極少見)
        continue

    # 防 composite PK 衝突：jp_card_pg_link
    dup_check = c.execute("""
        SELECT COUNT(*) FROM jp_card_pg_link
        WHERE pg=? AND cardID IN (SELECT cardID FROM jp_card_pg_link WHERE pg=?)
    """, (new_id, old_pg)).fetchone()[0]
    if dup_check > 0:
        # 不該發生、但若 mapping 把兩 pg 對到同 new_id 會撞、先刪 old 那邊衝突 row
        c.execute("""
            DELETE FROM jp_card_pg_link WHERE pg=?
            AND cardID IN (SELECT cardID FROM jp_card_pg_link WHERE pg=?)
        """, (old_pg, new_id))

    # 1. jp_card_list_set (PK = pg、不會衝突因為 old_pg ≠ new_id)
    c.execute("UPDATE jp_card_list_set SET pg=? WHERE pg=?", (new_id, old_pg))
    # 2. jp_card_list (PK = cardID、不 composite)
    c.execute("UPDATE jp_card_list SET pg=? WHERE pg=?", (new_id, old_pg))
    # 3. jp_card_pg_link (composite PK、剩下的 row 直接 UPDATE)
    c.execute("UPDATE jp_card_pg_link SET pg=? WHERE pg=?", (new_id, old_pg))
    # 4. jp_set_era_map (pg 已是 TEXT、可直接 UPDATE)
    c.execute("UPDATE jp_set_era_map SET pg=? WHERE pg=?", (new_id, old_pg))
    # 5. card_sets (4 rows 可能命中)
    c.execute("UPDATE card_sets SET set_id=? WHERE set_id=?", (new_id, old_pg))
    # 6. card_list
    c.execute("UPDATE card_list SET set_id=? WHERE set_id=?", (new_id, old_pg))
    # 7. card_volume_stats
    c.execute("UPDATE card_volume_stats SET set_id=? WHERE set_id=?", (new_id, old_pg))
    # 8. card_prices
    c.execute("UPDATE card_prices SET set_id=? WHERE set_id=?", (new_id, old_pg))

c.execute('COMMIT')

after = counts()
print('=== after ===')
for k, v in after.items(): print(f'  {k}: {v:,}')

# Validation
print('=== validation ===')
for k in before:
    if k.endswith('_numeric'):
        # numeric should be 0 after
        if after[k] != 0:
            print(f'  WARN: {k} still has {after[k]} numeric rows')
    else:
        # row counts should be preserved (except jp_card_list_set lost 9003)
        diff = after[k] - before[k]
        if k == 'jp_card_list_set' and diff == -1:
            print(f'  OK: {k} lost 1 row (9003 deleted)')
        elif diff == 0:
            print(f'  OK: {k} count preserved')
        else:
            print(f'  WARN: {k} diff={diff}')

c.close()
print('done')
```

- [ ] **Step 3: 跑 dry-run**

```powershell
./Python/bin/python.exe _pg_rename_apply.py cards.db.dryrun-pg-rename
```

Expected output:
```
=== before ===
  jp_card_list_set: 368
  jp_card_list: 21,552
  ...
=== after ===
  jp_card_list_set: 367  (lost 9003)
  ...
=== validation ===
  OK: ...
```

### Task 2.2: Dry-run 驗證 — 抽樣 + 完整檢查

**Files:** 無

- [ ] **Step 1: 對 dryrun DB 跑完整 numeric-pg 殘留檢查**

```python
# _pg_rename_dryrun_verify.py
import sqlite3
c = sqlite3.connect('cards.db.dryrun-pg-rename')

# 找任何殘留 numeric set_id / pg
checks = [
    ("jp_card_list_set", "SELECT pg FROM jp_card_list_set WHERE pg GLOB '[0-9]*'"),
    ("jp_card_list pg", "SELECT DISTINCT pg FROM jp_card_list WHERE pg GLOB '[0-9]*'"),
    ("jp_card_pg_link", "SELECT DISTINCT pg FROM jp_card_pg_link WHERE pg GLOB '[0-9]*'"),
    ("card_sets numeric", "SELECT set_id FROM card_sets WHERE set_id GLOB '[0-9]*'"),
    ("card_list numeric", "SELECT DISTINCT set_id FROM card_list WHERE set_id GLOB '[0-9]*'"),
    ("card_volume_stats numeric", "SELECT DISTINCT set_id FROM card_volume_stats WHERE set_id GLOB '[0-9]*'"),
    ("card_prices numeric", "SELECT DISTINCT set_id FROM card_prices WHERE set_id GLOB '[0-9]*'"),
]
for name, q in checks:
    rows = c.execute(q).fetchall()
    if rows:
        print(f'FAIL: {name} 殘留 {len(rows)} 筆: {rows[:5]}')
    else:
        print(f'OK: {name} no numeric residue')
```

```powershell
./Python/bin/python.exe _pg_rename_dryrun_verify.py
```

Expected: 全 OK。

- [ ] **Step 2: 抽 10 個 pg 看 mapping 前後對得起來**

```python
# _pg_rename_dryrun_spotcheck.py
import sqlite3
from _pg_rename_mapping import PG_RENAME_MAP

c_old = sqlite3.connect('cards.db')
c_new = sqlite3.connect('cards.db.dryrun-pg-rename')

# 抽 10 個 pg
test_pgs = ['9001', '9002', '950', '951', '949', '1', '301', '27', '744', '925']
for old in test_pgs:
    new = PG_RENAME_MAP.get(old, '?')
    old_cnt = c_old.execute("SELECT COUNT(*) FROM card_prices WHERE set_id=?", (old,)).fetchone()[0]
    new_cnt = c_new.execute("SELECT COUNT(*) FROM card_prices WHERE set_id=?", (new,)).fetchone()[0]
    # 9001 + 9003 應該合到 SV-P
    extra = ''
    if old == '9001':
        old_9003 = c_old.execute("SELECT COUNT(*) FROM card_prices WHERE set_id='9003'").fetchone()[0]
        extra = f' (+ 9003: {old_9003})'
        old_cnt += old_9003
    status = 'OK' if old_cnt == new_cnt else f'MISMATCH (lost {old_cnt-new_cnt})'
    print(f'  {old}→{new}: before={old_cnt}{extra} after={new_cnt} {status}')
```

```powershell
./Python/bin/python.exe _pg_rename_dryrun_spotcheck.py
```

Expected: 全部 OK。

---

## Phase 3: Production Migration

### Task 3.1: Backup

**Files:**
- Copy: `cards.db.before-pg-rename-20260522-HHMMSS`

- [ ] **Step 1: Backup**

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item cards.db "cards.db.before-pg-rename-$ts"
Get-Item "cards.db.before-pg-rename-*" | Format-List Name, Length
```

Expected: 檔案大小 ~854MB（與當前 cards.db 一致）

### Task 3.2: 停 API + 確認 0 reader

**Files:** 無

- [ ] **Step 1: 找 API PID**

```powershell
netstat -ano | findstr ":8000 .*LISTENING"
```

- [ ] **Step 2: Stop API**

```powershell
$pid_ = (netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
taskkill /F /PID $pid_
```

Expected: `SUCCESS: The process with PID xxx has been terminated.`

- [ ] **Step 3: 確認沒有其他 python 在動 cards.db**

```powershell
Get-Process python -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, StartTime
```

Expected: 空 或 確認沒有跑 cards.db 的 process。

### Task 3.3: 跑 production migration

**Files:**
- Modify: `cards.db`

- [ ] **Step 1: 跑 _pg_rename_apply.py 對 production cards.db**

```powershell
./Python/bin/python.exe _pg_rename_apply.py cards.db
```

Expected: same output as dry-run。

- [ ] **Step 2: 跑 verify 對 production cards.db**

```powershell
./Python/bin/python.exe _pg_rename_dryrun_verify.py
# (脚本需稍改 — 把 cards.db.dryrun-pg-rename 改 cards.db)
```

Expected: 全 OK。

---

## Phase 4: Update Code 引用

### Task 4.1: 改 database.py hardcoded refs

**Files:**
- Modify: `app/database.py:718`

- [ ] **Step 1: 改 line 718**

原：
```python
WHERE s.release_date IS NOT NULL AND s.pg NOT IN ('9001','9002','9003')
```

改：
```python
WHERE s.release_date IS NOT NULL AND s.pg NOT IN ('SV-P','M-P')
```

(9003 已合併進 9001 = SV-P、移除 9003 ref)

- [ ] **Step 2: 跑 grep 確認沒有其他 numeric pg hardcoded**

```powershell
# 用 Grep tool 找 '9001'|'9002'|'9003'|'950'|'951' 在 app/ 下、確認 0 命中
```

### Task 4.2: 改 jp_set_era_map schema (pg INTEGER → TEXT)

**Files:**
- Modify: `app/database.py`（init_db CREATE TABLE）

- [ ] **Step 1: 改 schema**

從：
```python
CREATE TABLE jp_set_era_map (
    pg INTEGER PRIMARY KEY,
    ...
)
```

改：
```python
CREATE TABLE jp_set_era_map (
    pg TEXT PRIMARY KEY,
    ...
)
```

- [ ] **Step 2: 在 production DB 改 column type**

SQLite 無法直接 ALTER COLUMN TYPE、需要 rebuild table：

```python
# _alter_era_map_pg_type.py
import sqlite3
c = sqlite3.connect('cards.db')
c.execute('BEGIN IMMEDIATE')
c.execute('CREATE TABLE jp_set_era_map_new (pg TEXT PRIMARY KEY, set_code TEXT, era TEXT NOT NULL, source TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
c.execute('INSERT INTO jp_set_era_map_new SELECT CAST(pg AS TEXT), set_code, era, source, updated_at FROM jp_set_era_map')
c.execute('DROP TABLE jp_set_era_map')
c.execute('ALTER TABLE jp_set_era_map_new RENAME TO jp_set_era_map')
c.execute('COMMIT')
print(c.execute('SELECT COUNT(*) FROM jp_set_era_map').fetchone())
```

Run before or after main migration? 建議在 Phase 3 migration script 內、改成同一 transaction。

### Task 4.3: 重啟 API + smoke test

**Files:** 無

- [ ] **Step 1: 重啟 API**

```powershell
./Python/bin/python.exe run_api.py
```

(背景跑 or 新視窗)

- [ ] **Step 2: 確認 /api/cardlist/sets 200**

```powershell
Invoke-RestMethod http://localhost:8000/api/cardlist/sets?language=jp | Select-Object -ExpandProperty sets | Select-Object -First 5 set_id, name, total_cards
```

Expected: 顯示 5 個 set、`set_id` 是新名（如 SV-P, M2 等）、不是 9001/949。

- [ ] **Step 3: 確認 /api/cardlist/sets/{set_id} 對新 ID 200**

```powershell
Invoke-RestMethod http://localhost:8000/api/cardlist/sets/SV-P | Select-Object total
```

Expected: 332（合併後 9001 + 9003）

- [ ] **Step 4: 確認 /api/prices/sync 對新 ID 200**

```powershell
Invoke-RestMethod -Method POST http://localhost:8000/api/prices/sync_snkr/SV-P/254
```

Expected: 200 + 寫入 SNKR row（或 0 row 若該卡 SNKR mapping 沒有）。

---

## Phase 5: Frontend Update

### Task 5.1: 確認前端是否 hardcode 任何 set_id

**Files:**
- Inspect: `卡波\index.html`

- [ ] **Step 1: Grep 找 hardcoded set_id**

```
Grep target in 卡波/index.html: '9001'|'9002'|'9003'|'950'|'951'
```

預期 0 命中（前端應該全 dynamic 從 API 拿）。

- [ ] **Step 2: 測首頁 + set 詳情頁 + 卡詳情頁正常**

開 `http://localhost:8080/` → 卡盒系列、點任一 set → 卡列表、點任一卡 → 詳情 + 價格圖表。

每一頁都應該 work、URL 變成 `#/set?set=SV-P` 而非 `#/set?set=9001`。

---

## Phase 6: Sort Fix (首頁排序)

### Task 6.1: 改 sortSetsCmp 改成 release_date 優先

**Files:**
- Modify: `卡波\index.html:1342-1361`

- [ ] **Step 1: 改 sortSetsCmp 函式 JP 分支**

原：
```javascript
function sortSetsCmp(a, b, lang){
  if(lang === 'jp'){
    const aa = (a.display_order ?? 99999);
    const bb = (b.display_order ?? 99999);
    if(aa !== bb) return aa - bb;
    const ai = (a.art_id ?? -1), bi = (b.art_id ?? -1);
    if(ai !== bi) return bi - ai;
  } else {
    // en/tw 已經按 release_date 排
  }
  const da = a.release_date || '', db = b.release_date || '';
  if(da && db) return db.localeCompare(da);
  if(da) return -1;
  if(db) return 1;
  return (b.id||0) - (a.id||0);
}
```

改：
```javascript
function sortSetsCmp(a, b, lang){
  // release_date 優先（新到舊）；缺日期的推到後面
  const da = a.release_date || '', db = b.release_date || '';
  if(da && db && da !== db) return db.localeCompare(da);  // 新到舊
  if(da && !db) return -1;
  if(!da && db) return 1;

  // fallback to display_order (JP only)
  if(lang === 'jp'){
    const aa = (a.display_order ?? 99999);
    const bb = (b.display_order ?? 99999);
    if(aa !== bb) return aa - bb;
  } else {
    const oa = (a.order_index ?? 99999);
    const ob = (b.order_index ?? 99999);
    if(oa !== ob) return oa - ob;
  }

  return (b.id||0) - (a.id||0);
}
```

- [ ] **Step 2: Reload 前端、確認首頁 12 個 set 順序為 release_date DESC**

開 `http://localhost:8080/`、看「卡盒系列」section 前 12 個。應該是：
- 2025-12-19 系列在最前
- 2025-11-28 系列次之
- ...

預期前 12 個全是 2025 年發行的。

---

## Phase 7: Validation + Commit

### Task 7.1: 全系統 smoke test

**Files:** 無

- [ ] **Step 1: 列 8 個關鍵 endpoint 全打一遍**

```
GET /api/cardlist/sets?language=jp           → 期望返回 SV-P/M2/M-P 等新 ID
GET /api/cardlist/sets/SV-P                  → 期望返回 332 卡
GET /api/cardlist/sets/M2                    → Mega Brave 92 卡
GET /api/cardlist/sets/SV-P/preview-image    → 圖片
GET /api/cardlist/sets/SV-P/latest-prices    → 價格列表
POST /api/prices/sync_snkr/SV-P/254          → SNKR sync OK
POST /api/prices/sync_ebay/SV-P/254          → (eBay 仍被擋、但 endpoint 不應 500)
GET /api/category/pokemon/25/cards           → 皮卡丘的 SV-P 卡應該還能撈到
```

- [ ] **Step 2: 抽 5 個原本 9001 的 sync_snkr/sync_ebay request 用新 ID 再打一次、確認價格依舊在**

```powershell
Invoke-RestMethod http://localhost:8000/api/cardlist/sets/SV-P/latest-prices |
    Select-Object -ExpandProperty cards |
    Select-Object -First 5 card_number, name_jp, snkrdunk_price_min_psa10
```

Expected: 5 個 card、價格非 null。

### Task 7.2: 更新 CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 改 「資料模型 — 關鍵表」段**

原：
```
| `jp_card_list_set` | 368 | JP set 主表、`name_jp` 格式為 `日文 (中文)`...
```

加上一段：
```
**Set ID 命名（2026-05-22 統一）**：jp_card_list_set.pg 已從純數字 (pokemon-card.com internal series ID) 改為 set_code (短代碼如 `SV-P`/`M2a`) 或 `jp-XXX` slug。對映表保留在 `_pg_rename_mapping.csv`、rollback 用 `cards.db.before-pg-rename-*` backup。
```

原：
```
**Promo set pg**：9001（SV-P 朱紫期）/ 9002（M-P MEGA 期）/ 9003（朱紫期但 MEGA 階段、由 cardID 推算）/ ...
```

改：
```
**Promo set ID**：SV-P（朱紫期、含 MEGA 階段 promo、共 332 卡）/ M-P（MEGA 期、98 卡）/ M2a（高級擴充包 MEGAドリームex 250 卡）/ MC（バトルコレクション 合輯再印 774 卡）。原 pg=9003 已併入 SV-P (2026-05-22)。
```

### Task 7.3: 更新 PROGRESS.md

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: 加進 2026-05-22 工作日誌**

寫一段「完成 pg → set_code/slug rename + 9003 合併 + 首頁排序修正」。

- [ ] **Step 2: 加 Known Pitfalls**

```
- **改 pg 等於改全系統 set 識別符**（2026-05-22 經驗）：jp_card_list_set.pg 被 10 張表引用（含 card_prices 372k 行）、改名需 atomic cascade UPDATE。Migration 步驟：(a) backup (b) 寫 mapping CSV + user review (c) dry-run 在 copy DB (d) atomic UPDATE 全表 (e) update database.py hardcoded refs (f) 重啟 API smoke test。未來新增「合成 pg」前要思考能否直接用真實 set_code。
```

### Task 7.4: Commit

**Files:** 無

- [ ] **Step 1: `git diff --numstat` 確認改動範圍**

預期改動：
- `app/database.py`（hardcoded 9001/9002/9003 + jp_set_era_map schema）
- `卡波/index.html`（sortSetsCmp + 可能其他）
- `CLAUDE.md` / `PROGRESS.md`
- 新增 `docs/superpowers/plans/2026-05-22-jp-pg-rename.md`

注意：`_pg_rename_*.py / .csv` 全部以 `_` 開頭、依 .gitignore 排除、不會 commit。

- [ ] **Step 2: commit**

```bash
git add app/database.py 卡波/index.html CLAUDE.md PROGRESS.md docs/superpowers/plans/2026-05-22-jp-pg-rename.md
git commit -m "$(cat <<'EOF'
Unify JP set IDs: rename 368 numeric pg → set_code/jp-XXX slug

- jp_card_list_set.pg now uses set_code (SV-P/M2a/etc) or jp-XXX slug
- jp_set_era_map.pg INTEGER → TEXT
- 372k card_prices.set_id rows cascade-updated atomically
- 9003 (SV-P MEGA era) merged into 9001 (SV-P), losing redundant split
- Frontend sortSetsCmp now sorts by release_date DESC (newest first)
- card_sets / card_list / card_volume_stats numeric set_ids removed

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Rollback Strategy

任何 Phase 出錯：

### 完全 rollback（Phase 3 之後撞牆）

```powershell
# 停 API
$pid_ = (netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
taskkill /F /PID $pid_

# 還原 cards.db
$bk = Get-Item cards.db.before-pg-rename-* | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Copy-Item $bk.FullName cards.db -Force

# 還原 code
git checkout app/database.py 卡波/index.html

# 重啟
./Python/bin/python.exe run_api.py
```

### 部分 rollback（mapping CSV 改錯）

如果只是個別 pg 改錯：
1. 用 `_pg_rename_mapping.csv` 找原本 pg 跟錯的 new_id
2. 寫 patch script 對應修正
3. 不需全 rollback

---

## Risk Assessment

| 風險 | 機率 | 影響 | 緩解 |
|---|---|---|---|
| Mapping CSV 漏改 row → 殘留 numeric set_id | 中 | 中 (UI 顯示混亂) | dry-run verify 殘留檢查 |
| card_prices 372k cascade 跑太久 / 鎖太久 | 低 | 高 (整個 atomic) | 用 BEGIN IMMEDIATE、預期 < 30s |
| 命名衝突 (兩個 pg 對到同 new_id) | 中 | 高 (UNIQUE 違反) | Task 0.3 unique check |
| 前端 bookmark 失效 | 高 | 低 (hobby project) | 接受、URL 改了就是改了 |
| 改完後發現 mapping 有錯 | 中 | 高 (價格資料找不到) | backup + rollback script |
| 9003 合併丟資料 | 低 | 中 | 9003 只 15 張卡 + 已用 SV-P set_code、合併無資料損失 |
| jp_set_era_map schema 改 INT→TEXT 後其他 query fail | 中 | 中 | Task 4.2 改 schema 後跑全 endpoint smoke test |

---

## Time Estimate

| Phase | 時間 | 性質 |
|---|---|---|
| 0 (mapping + user review) | 2-4 hr | **user 主導 manual review** |
| 1 (code scan) | 30 min | 我跑 |
| 2 (dry-run) | 30 min | 我跑 |
| 3 (production migration) | 30 min | 我跑 + user 確認停 API |
| 4 (code update) | 1 hr | 我改 + smoke test |
| 5 (frontend) | 30 min | 我改 |
| 6 (sort fix) | 15 min | 我改 |
| 7 (validation + commit) | 30 min | 我跑 |
| **總計** | **5.5–7.5 hr** | 約一個工作天 |

實際瓶頸是 Phase 0 user review 359 row mapping CSV——這需要 user 親自看每筆、特別是 79 個無 set_code 的 + 多 set_code 混雜的。
