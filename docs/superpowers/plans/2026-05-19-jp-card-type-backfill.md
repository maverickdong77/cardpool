# jp_card_list.card_type 補齊 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `jp_card_list.card_type` 從 0% 覆蓋率補到 100%（21,552 張卡），分類成 `pokemon` / `trainer` / `energy` 三類、資料源為 pokemon-card.com 詳情頁。

**Architecture:** 沿用 `jp_detail_crawl_v2.py` 已驗證的 sequential httpx scraper 框架（2s/卡、403 backoff 300s、5 連 403 abort）、加上新的 `parse_card_type()` 函式（已在 9 個樣本 9/9 驗證通過）。Backfill 腳本以 `--limit 10` 試跑、人工確認、再放量全跑、最後跑 verify SQL。

**Tech Stack:** Python 3.11(`./Python/bin/python.exe`)/ httpx sync / SQLite (`cards.db`) / PowerShell（Windows、必須 `PYTHONIOENCODING=utf-8` 印 JP/ZH）。

---

## 重要環境細節（**動手前必讀**）

- 工作目錄：`C:\Users\Dong Ying\Desktop\Cardpool Price Searching\`
- Python：**一律用 `./Python/bin/python.exe`、不要用系統 python**
- DB：`cards.db`（SQLite、815MB）
- 編碼：印 JP/ZH 一律先 `$env:PYTHONIOENCODING="utf-8"`
- Shell：PowerShell（指令鏈用 `;` 不用 `&&`、丟棄 stderr 用 `2>$null` 不用 `2>/dev/null`）
- URL pattern：`https://www.pokemon-card.com/card-search/details.php/card/{cardID}/regu/all`
- `card_type` 欄位已存在於 `jp_card_list`（`phaseB_schema.sql:10` ALTER 加的）、本 plan **不改 schema**
- 後端 endpoint code 本次完全不動 → **不需重啟 uvicorn**

### Safeguards

1. **任何 DB 改動前先 backup**：`cp cards.db cards.db.before-card-type-backfill-YYYYMMDD-HHMMSS`
2. **檔名命名慣例**：DB 改動腳本以 `_` 開頭、放根目錄（例：`_backfill_card_type.py`、`_verify_card_type.py`）
3. **跑前一定先確認 gating SQL 數**（預期 21,552）
4. **commit 前一定先 `git status` + `git diff --numstat`** 確認範圍、本工作區當前有大量未 commit 改動（CLAUDE.md / PROGRESS.md / app/scraper/*.py / 多份 TRANSLATION_REVIEW_BATCH*.md / cards.db.* backup），**本 plan 的每個 commit 只能含本次 card_type backfill 範疇的檔案**

### Parser 邏輯（已驗證 9/9）

```python
import re

def parse_card_type(html):
    """Return one of: 'pokemon' / 'trainer' / 'energy' / None."""
    m = re.search(
        r'<h2[^>]*class="[^"]*mt20[^"]*"[^>]*>\s*'
        r'(基本エネルギー|特殊エネルギー|グッズ|サポート|スタジアム|ポケモンのどうぐ)\s*</h2>',
        html,
    )
    if m:
        cat = m.group(1)
        return 'energy' if 'エネルギー' in cat else 'trainer'
    if re.search(r'<span class="hp-num">\d+</span>', html):
        return 'pokemon'
    return None
```

Pilot 在 9 個樣本（涵蓋全 6 種 sub-type、含 fake-energy item「エネルギーつけかえ」）拿到 9/9。樣本 HTML 在 `_sample_html/`、pilot script 在 `_pilot_parse_card_type.py`。

---

## File Structure

| 檔案 | 角色 | 操作 |
|------|------|------|
| `_backfill_card_type.py` | 主 backfill 腳本（httpx sync、2s/卡、403 backoff、COALESCE UPDATE） | Task 1 Create |
| `_card_type_backfill.log` | 進度 log（每 200 卡輸出 bucket count） | Task 1 ~ 3 自動產生 |
| `cards.db.before-card-type-backfill-<timestamp>` | DB backup | Task 2 Create |
| `_verify_card_type.py` | Verify 腳本（bucket count + sanity SQL + 抽樣） | Task 4 Create |
| `_card_type_verify.txt` | Verify 報告（人工可讀） | Task 4 自動產生 |
| `docs/superpowers/plans/2026-05-19-jp-card-type-backfill.md` | 本 plan | 已存在（這份） |

---

## Task 1：寫 `_backfill_card_type.py` 主腳本

**Files:**
- Create: `C:/Users/Dong Ying/Desktop/Cardpool Price Searching/_backfill_card_type.py`

- [ ] **Step 1：寫整份 `_backfill_card_type.py`**

```python
"""
_backfill_card_type — 對 jp_card_list 抓 pokemon-card.com 詳情頁、補 card_type 欄位。

card_type ∈ {pokemon, trainer, energy}：
  - h2.mt20 為「基本エネルギー / 特殊エネルギー」→ energy
  - h2.mt20 為「グッズ / サポート / スタジアム / ポケモンのどうぐ」→ trainer
  - 否則若有 span.hp-num → pokemon
  - 都不是 → None(不寫入、等下次跑)

用法：
  ./Python/bin/python.exe _backfill_card_type.py             # 全跑(card_type IS NULL)
  ./Python/bin/python.exe _backfill_card_type.py --limit 10  # 只跑 10 張試
  ./Python/bin/python.exe _backfill_card_type.py --pg 950    # 只跑 pg=950
  ./Python/bin/python.exe _backfill_card_type.py --force     # 重跑、忽略既有 card_type

設計：
- sequential、2s/卡、403 -> 300s backoff、5 連續 403 abort
- COALESCE UPDATE：不覆蓋既有 non-NULL 值(與 jp_detail_crawl_v2 一致)
- 每 50 卡 commit、每 200 卡 log 三桶 count
- gating：card_type IS NULL(--force 取消)
"""
import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
DB = 'cards.db'
LOG = '_card_type_backfill.log'
BASE = 'https://www.pokemon-card.com'
SLEEP_PER_CARD = 2.0
SLEEP_AFTER_403 = 300


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_card_type(html):
    """Return one of: 'pokemon' / 'trainer' / 'energy' / None."""
    m = re.search(
        r'<h2[^>]*class="[^"]*mt20[^"]*"[^>]*>\s*'
        r'(基本エネルギー|特殊エネルギー|グッズ|サポート|スタジアム|ポケモンのどうぐ)\s*</h2>',
        html,
    )
    if m:
        cat = m.group(1)
        return 'energy' if 'エネルギー' in cat else 'trainer'
    if re.search(r'<span class="hp-num">\d+</span>', html):
        return 'pokemon'
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pg', type=str, default=None, help='Limit to one pg (e.g. 950)')
    ap.add_argument('--limit', type=int, default=None, help='Limit number of cards')
    ap.add_argument('--force', action='store_true', help='Re-crawl even if card_type is set')
    args = ap.parse_args()

    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n=== run start " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ===\n")
    log(f'card_type backfill start; SLEEP_PER_CARD={SLEEP_PER_CARD}s; args={vars(args)}')

    client = httpx.Client(
        headers={"User-Agent": UA, "Accept": "text/html"},
        timeout=30,
        follow_redirects=True,
    )

    conn = sqlite3.connect(DB)
    c = conn.cursor()

    where_clauses = []
    params = []
    if not args.force:
        where_clauses.append('card_type IS NULL')
    if args.pg is not None:
        where_clauses.append('pg = ?')
        params.append(args.pg)
    where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''
    limit_sql = f' LIMIT {args.limit}' if args.limit else ''
    query = f'SELECT cardID FROM jp_card_list{where_sql} ORDER BY CAST(pg AS INTEGER) DESC, cardID{limit_sql}'
    to_crawl = c.execute(query, params).fetchall()
    log(f'to crawl: {len(to_crawl)} cards')

    fetched = failed = unclassified = 0
    buckets = {'pokemon': 0, 'trainer': 0, 'energy': 0}
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

            ctype = parse_card_type(r.text)

            c.execute(
                "UPDATE jp_card_list SET card_type = COALESCE(?, card_type) WHERE cardID = ?",
                (ctype, cid),
            )

            fetched += 1
            if ctype in buckets:
                buckets[ctype] += 1
            else:
                unclassified += 1

            if fetched % 50 == 0:
                conn.commit()
            if fetched % 200 == 0:
                log(
                    f'  progress {fetched}/{len(to_crawl)} | '
                    f'pokemon={buckets["pokemon"]} trainer={buckets["trainer"]} energy={buckets["energy"]} '
                    f'unclassified={unclassified} failed={failed}'
                )

            time.sleep(SLEEP_PER_CARD)

        conn.commit()
        log(
            f'Done. fetched={fetched} failed={failed} | '
            f'pokemon={buckets["pokemon"]} trainer={buckets["trainer"]} energy={buckets["energy"]} '
            f'unclassified={unclassified}'
        )

    finally:
        conn.close()
        client.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
```

- [ ] **Step 2：syntax sanity check（不執行抓取、純載入模組）**

```powershell
./Python/bin/python.exe -c "import importlib.util; spec = importlib.util.spec_from_file_location('m', '_backfill_card_type.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('import ok')"
```

預期輸出：`import ok`。如果 `SyntaxError` / `ImportError` 就回去修。

- [ ] **Step 3：用 `_sample_html/` 樣本對 `parse_card_type` 做離線驗證（不打網路）**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c @"
import sys
sys.path.insert(0, '.')
from _backfill_card_type import parse_card_type
cases = {
    'pokemon_48523.html': 'pokemon',
    'supporter_nanjamo_43205.html': 'trainer',
    'trainer_item_48672.html': 'trainer',
    'trainer_supporter_48668.html': 'trainer',
    'trainer_item_with_energy_name_1108.html': 'trainer',
    'trainer_stadium_36035.html': 'trainer',
    'trainer_tool_40992.html': 'trainer',
    'energy_basic_1.html': 'energy',
    'energy_special_doubleColorless_33064.html': 'energy',
}
ok = 0
for fn, expect in cases.items():
    with open(f'_sample_html/{fn}', encoding='utf-8') as f:
        got = parse_card_type(f.read())
    flag = 'OK' if got == expect else 'FAIL'
    if got == expect:
        ok += 1
    print(f'{flag} {fn}: expect={expect} got={got}')
print(f'{ok}/{len(cases)}')
"@
```

預期輸出：9 行 `OK ...` + 最後一行 `9/9`。任一 FAIL 都要停下來修 `parse_card_type` 後重跑。

- [ ] **Step 4：先看 git status、確認 commit 範圍只含本任務檔案**

```powershell
git status --short
git diff --numstat
```

確認看到 `?? _backfill_card_type.py`、其他 modified / untracked 都不會在這次 commit 內。

- [ ] **Step 5：Commit Task 1**

```powershell
git add _backfill_card_type.py
git commit -m "新增 _backfill_card_type.py：jp_card_list card_type 補齊腳本（pokemon/trainer/energy 三分類、parser 已在 9 樣本驗證 9/9）"
```

---

## Task 2：DB backup + live 試跑 10 張 + 人工 spot-check

**Files:**
- Create: `C:/Users/Dong Ying/Desktop/Cardpool Price Searching/cards.db.before-card-type-backfill-<YYYYMMDD-HHMMSS>`（backup）
- Modify: `C:/Users/Dong Ying/Desktop/Cardpool Price Searching/cards.db`（10 行）

- [ ] **Step 1：確認 gating SQL 預期數量**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; con=sqlite3.connect('cards.db'); n=con.execute('SELECT COUNT(*) FROM jp_card_list WHERE card_type IS NULL').fetchone()[0]; total=con.execute('SELECT COUNT(*) FROM jp_card_list').fetchone()[0]; print(f'NULL={n} / total={total}')"
```

預期：`NULL=21552 / total=21552`。若不符（例：之前測試已寫過一些），記下實際值、繼續即可。

- [ ] **Step 2：Backup DB**

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"; Copy-Item cards.db "cards.db.before-card-type-backfill-$ts"; Get-ChildItem "cards.db.before-card-type-backfill-$ts" | Select-Object Name, Length
```

預期：印出新 backup 檔名 + 約 815MB 大小。**這步沒成功的話絕對不要繼續。**

- [ ] **Step 3：Live 試跑 10 張**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe _backfill_card_type.py --limit 10
```

預期：~20 秒（10 卡 × 2s）跑完、log 末行類似 `Done. fetched=10 failed=0 | pokemon=X trainer=Y energy=Z unclassified=0`。

- [ ] **Step 4：人工 spot-check 10 張結果**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c @"
import sqlite3
con = sqlite3.connect('cards.db')
rows = con.execute('''
    SELECT cardID, pg, card_number, name_jp, card_type, hp
    FROM jp_card_list
    WHERE card_type IS NOT NULL
    ORDER BY CAST(pg AS INTEGER) DESC, cardID
    LIMIT 10
''').fetchall()
for r in rows:
    print(r)
print('---')
print('bucket counts:')
for row in con.execute(\"SELECT card_type, COUNT(*) FROM jp_card_list WHERE card_type IS NOT NULL GROUP BY card_type\").fetchall():
    print(row)
"@
```

預期：10 行每行 `(cardID, pg, card_number, name_jp, card_type, hp)`、card_type 不為 NULL。`pokemon` 卡通常 hp 有值（如 60 / 120）、`trainer` / `energy` 卡 hp 為 None。

**人工確認重點（一卡一卡看）：**
- pokemon 卡 → name_jp 是寶可夢名（如「ピカチュウ」「ヒビキのカイロス」）、hp 不為 None
- trainer 卡 → name_jp 是道具/支援者/競技場名（如「ハイパーボール」「ナンジャモ」「トキワの森」）、hp 為 None
- energy 卡 → name_jp 含「エネルギー」（如「基本草エネルギー」「ダブル無色エネルギー」）、hp 為 None
- **特別注意**「エネルギーつけかえ」這類 fake-energy item：name_jp 含「エネルギー」**但** card_type 應為 `trainer`（h2 是「グッズ」）

- [ ] **Step 5：PAUSE — 等使用者確認 10 張結果 OK 才繼續放量**

把 Step 4 的輸出直接貼給使用者、明確問：「這 10 張結果 OK 嗎？OK 我才放量跑剩下 21,542 張（ETA ~12h）」。

**使用者點頭前不要 commit、不要進 Task 3。** 沒有使用者確認、本任務不完成。

- [ ] **Step 6：使用者確認 OK 後、commit Task 2**

```powershell
git status --short
git diff --numstat
# 預期看到 cards.db 變更（10 行 card_type）+ 新增 _card_type_backfill.log + cards.db.before-card-type-backfill-* 一個 backup
git add _card_type_backfill.log
# 注意：cards.db 跟 backup 通常不 git add（太大、且 repo 慣例如此）
# 如果 cards.db 在 .gitignore 沒被排除、本次也明確不要 git add cards.db
git commit -m "card_type 試跑 10 張：DB backup + 人工 spot-check 通過、log 入庫"
```

如果 `cards.db` 沒在 `.gitignore`、改為：

```powershell
git add _card_type_backfill.log
git commit -m "card_type 試跑 10 張：DB backup + 人工 spot-check 通過、log 入庫（cards.db / backup 不入 git）"
```

---

## Task 3：放量跑全量（21,542 張剩餘）

**Files:**
- Modify: `cards.db`（~21,542 行 card_type）
- Modify: `_card_type_backfill.log`（append 進度）

- [ ] **Step 1：再確認 gating 數**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; con=sqlite3.connect('cards.db'); print('NULL=', con.execute('SELECT COUNT(*) FROM jp_card_list WHERE card_type IS NULL').fetchone()[0])"
```

預期：`NULL= 21542`（Task 2 已補 10 張）。

- [ ] **Step 2：啟 background job**

```powershell
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "_backfill_card_type.py" -NoNewWindow -RedirectStandardOutput "_card_type_backfill.stdout.log" -RedirectStandardError "_card_type_backfill.stderr.log"
```

或者用 Bash tool 的 `run_in_background`：

```bash
./Python/bin/python.exe _backfill_card_type.py
```

ETA：21,542 × 2s ≈ **11.96 小時**。

- [ ] **Step 3：每 30 ~ 60 min 監看一次 log（不要每分鐘 poll）**

```powershell
Get-Content _card_type_backfill.log -Tail 10
```

預期看到類似（每 200 卡一行）：
```
[2026-05-19 14:23:11]   progress 5000/21542 | pokemon=2900 trainer=1700 energy=80 unclassified=0 failed=2
```

如果連續兩次（>30 min）看不到新 progress line、檢查：
- `Get-Process | Where-Object {$_.ProcessName -eq "python"}` 看 process 還活不活
- `Get-Content _card_type_backfill.log -Tail 30` 看是不是撞到 403 backoff（會看到 `403 on cardID ... sleeping 300s`）
- 5 連續 403 會自動 abort、log 末行會出現 `ABORT: 5 consecutive 403s`

**注意：本腳本是 httpx sync、沒有 jp eBay scraper 那個 ~5 小時 Playwright hang 問題、不需要 `_resilient_backfill.ps1` wrapper。**

- [ ] **Step 4：等 background job 完成**

完成判定（兩個都要滿足）：

```powershell
# (a) log 末行有 Done.
Get-Content _card_type_backfill.log -Tail 5
# 預期出現：Done. fetched=21542 failed=X | pokemon=... trainer=... energy=... unclassified=...

# (b) 剩餘 NULL 數
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; print(sqlite3.connect('cards.db').execute('SELECT COUNT(*) FROM jp_card_list WHERE card_type IS NULL').fetchone()[0])"
# 預期 0（容忍 < 50 殘留、若全是 404 / parse-fail 老卡是可接受的；若 > 50、Task 4 verify 階段要列出來查原因）
```

如果 NULL > 50、列出來看：

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; rows=sqlite3.connect('cards.db').execute('SELECT cardID, pg, card_number, name_jp FROM jp_card_list WHERE card_type IS NULL LIMIT 50').fetchall(); [print(r) for r in rows]"
```

判斷殘留是「真的 parse 不出來、需要 patch parser」還是「該卡 URL 已 404 / 重定向」。若是 parse fail、補 parser 後 `./Python/bin/python.exe _backfill_card_type.py`（gating 會自動 pick up 剩下的）。

- [ ] **Step 5：Commit Task 3**

```powershell
git status --short
git diff --numstat
# 預期：cards.db 大量 row 改動 + _card_type_backfill.log append
git add _card_type_backfill.log
git commit -m "card_type 全量 backfill 完成：21,542 張卡分類入庫（pokemon/trainer/energy 三桶）"
```

---

## Task 4：寫 `_verify_card_type.py` + 三桶分布 + Sanity SQL + 抽樣 + 跨表 cross-check

**Files:**
- Create: `C:/Users/Dong Ying/Desktop/Cardpool Price Searching/_verify_card_type.py`
- Create: `C:/Users/Dong Ying/Desktop/Cardpool Price Searching/_card_type_verify.txt`（執行產出）

- [ ] **Step 1：寫整份 `_verify_card_type.py`**

```python
"""
_verify_card_type — 對 jp_card_list.card_type backfill 跑驗證 SQL、輸出可讀報告。

驗證項：
1. 三桶分布(pokemon / trainer / energy / NULL)+ 佔比
2. Sanity #1：card_type='pokemon' AND hp IS NULL(預期 < 100，少量老卡 hp 沒抓)
3. Sanity #2：card_type='energy' AND name_jp NOT LIKE '%エネルギー%'(預期 0 或極少)
4. Sanity #3：card_type='trainer' AND name_jp LIKE '%エネルギー%'(揭露 fake-energy item、列出來)
5. 抽樣每桶 10 張人工看
6. Cross-check：jp_term_dict.category 為 trainer/item/supporter/tool/stadium 的卡名
   是否在 jp_card_list 對應 card_type 為 'trainer'

用法：
  ./Python/bin/python.exe _verify_card_type.py
  輸出到 stdout + `_card_type_verify.txt`
"""
import sqlite3
import sys
from datetime import datetime

DB = 'cards.db'
OUT = '_card_type_verify.txt'


def section(out, title):
    line = '\n' + '=' * 60 + f'\n{title}\n' + '=' * 60
    print(line, flush=True)
    out.write(line + '\n')


def write(out, text):
    print(text, flush=True)
    out.write(text + '\n')


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    with open(OUT, 'w', encoding='utf-8') as out:
        out.write(f'card_type verify report — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')

        section(out, '1. card_type 三桶分布')
        total = c.execute('SELECT COUNT(*) FROM jp_card_list').fetchone()[0]
        write(out, f'total jp_card_list rows: {total}')
        rows = c.execute('''
            SELECT COALESCE(card_type, 'NULL') AS ct, COUNT(*) AS n
            FROM jp_card_list GROUP BY ct ORDER BY n DESC
        ''').fetchall()
        for ct, n in rows:
            pct = n / total * 100 if total else 0
            write(out, f'  {ct:10s}: {n:>6d}  ({pct:5.2f}%)')

        section(out, '2. Sanity #1: card_type=pokemon AND hp IS NULL(預期 < 100)')
        cnt = c.execute(
            "SELECT COUNT(*) FROM jp_card_list WHERE card_type='pokemon' AND hp IS NULL"
        ).fetchone()[0]
        write(out, f'count: {cnt}')
        if cnt > 0:
            samples = c.execute(
                "SELECT cardID, pg, card_number, name_jp FROM jp_card_list "
                "WHERE card_type='pokemon' AND hp IS NULL LIMIT 20"
            ).fetchall()
            for r in samples:
                write(out, f'  {r}')

        section(out, '3. Sanity #2: card_type=energy AND name_jp NOT LIKE %エネルギー%(預期 0 或極少)')
        cnt = c.execute(
            "SELECT COUNT(*) FROM jp_card_list "
            "WHERE card_type='energy' AND (name_jp IS NULL OR name_jp NOT LIKE '%エネルギー%')"
        ).fetchone()[0]
        write(out, f'count: {cnt}')
        if cnt > 0:
            samples = c.execute(
                "SELECT cardID, pg, card_number, name_jp FROM jp_card_list "
                "WHERE card_type='energy' AND (name_jp IS NULL OR name_jp NOT LIKE '%エネルギー%') LIMIT 20"
            ).fetchall()
            for r in samples:
                write(out, f'  {r}')

        section(out, '4. Sanity #3: card_type=trainer AND name_jp LIKE %エネルギー%(fake-energy item、列出來人工 confirm)')
        rows = c.execute(
            "SELECT cardID, pg, card_number, name_jp FROM jp_card_list "
            "WHERE card_type='trainer' AND name_jp LIKE '%エネルギー%' "
            "ORDER BY CAST(pg AS INTEGER) DESC LIMIT 50"
        ).fetchall()
        write(out, f'count (showing up to 50): {len(rows)}')
        for r in rows:
            write(out, f'  {r}')
        write(out, '\n(預期項目：エネルギーつけかえ / エネルギーリサイクル / エネルギー回収 等 trainer item)')

        for ct in ('pokemon', 'trainer', 'energy'):
            section(out, f'5. 抽樣 10 張 card_type={ct}')
            rows = c.execute(
                "SELECT cardID, pg, card_number, name_jp, hp "
                "FROM jp_card_list WHERE card_type=? "
                "ORDER BY RANDOM() LIMIT 10",
                (ct,),
            ).fetchall()
            for r in rows:
                write(out, f'  {r}')

        section(out, '6. Cross-check jp_term_dict vs jp_card_list.card_type')
        try:
            mismatches_trainer = c.execute('''
                SELECT t.jp_name, t.category, j.cardID, j.pg, j.card_type
                FROM jp_term_dict t
                JOIN jp_card_list j ON j.name_jp = t.jp_name
                WHERE t.category IN ('item', 'supporter', 'stadium', 'tool', 'trainer')
                  AND (j.card_type IS NULL OR j.card_type != 'trainer')
                LIMIT 100
            ''').fetchall()
            write(out, f'jp_term_dict 標 item/supporter/stadium/tool/trainer 但 card_type 不為 trainer 的卡: {len(mismatches_trainer)} (limit 100)')
            for r in mismatches_trainer[:30]:
                write(out, f'  {r}')

            mismatches_energy = c.execute('''
                SELECT t.jp_name, t.category, j.cardID, j.pg, j.card_type
                FROM jp_term_dict t
                JOIN jp_card_list j ON j.name_jp = t.jp_name
                WHERE t.category = 'energy'
                  AND (j.card_type IS NULL OR j.card_type != 'energy')
                LIMIT 100
            ''').fetchall()
            write(out, f'\njp_term_dict 標 energy 但 card_type 不為 energy 的卡: {len(mismatches_energy)} (limit 100)')
            for r in mismatches_energy[:30]:
                write(out, f'  {r}')
        except sqlite3.OperationalError as e:
            write(out, f'(skip: jp_term_dict cross-check failed: {e})')

        section(out, 'VERIFY DONE')
        write(out, f'report written to {OUT}')

    conn.close()


if __name__ == '__main__':
    main()
```

- [ ] **Step 2：跑 verify 腳本**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe _verify_card_type.py
```

預期輸出（量級參考、實際依資料）：

```
1. card_type 三桶分布
total jp_card_list rows: 21552
  pokemon   : ~14000  (~65%)
  trainer   :  ~6500  (~30%)
  energy    :  ~1000  (~5%)
  NULL      :    < 50

2. Sanity #1: pokemon AND hp IS NULL
count: < 100

3. Sanity #2: energy AND name NOT LIKE エネルギー
count: 0 ~ 5

4. Sanity #3: trainer AND name LIKE エネルギー (fake-energy items)
count: ~10 ~ 30   ← 列出來人工看

5. 抽樣每桶 10 張 ← 人工看每張對不對

6. Cross-check jp_term_dict
mismatch count 應該 < 50（jp_term_dict 名稱有時含修飾、寬鬆匹配下少量正常）
```

- [ ] **Step 3：人工 review `_card_type_verify.txt`**

逐項看：

| Section | 異常判準 | 處置 |
|---------|---------|------|
| 1 三桶分布 | NULL > 50 | 列出 NULL 卡、查 URL 是否 404 / 改 parser 後重跑 |
| 2 pokemon hp NULL | > 200 | 太多代表 hp 抓壞、不關 card_type 但要 file 一個 follow-up |
| 3 energy 反例 | > 0 | 列出來人工看、可能 parser bug |
| 4 trainer 含エネルギー | 0 | 反而異常、代表「エネルギーつけかえ」這類沒被分對 |
| 5 抽樣 | 任一張看起來分錯 | 開該卡 URL 對 HTML、修 parser、重跑該卡 |
| 6 cross-check | mismatch > 100 | 列出 mismatch、判斷是 dict 標錯還是 card_type 抓錯 |

- [ ] **Step 4：抽樣 5 張人工開 pokemon-card.com 詳情頁眼睛確認**

從 Section 5 各桶抽樣裡挑 5 張、開 `https://www.pokemon-card.com/card-search/details.php/card/{cardID}/regu/all` 對眼確認分類正確。**至少 5/5 通過才算 verify 過。**

- [ ] **Step 5：Commit Task 4 + 標記 plan 完成**

```powershell
git status --short
git diff --numstat
git add _verify_card_type.py _card_type_verify.txt
git commit -m "新增 _verify_card_type.py + verify 報告：card_type backfill 三桶分布通過、sanity SQL + 抽樣 + jp_term_dict cross-check 全綠"
```

---

## Self-Review（writing-plans skill 要求）

**Spec coverage check：**
- [x] 主 backfill 腳本含 httpx sync / 2s/卡 / `--limit` `--pg` `--force` / 403 backoff 300s / 5 連 403 abort / 每 50 卡 commit / 每 200 卡 log progress（bucket count + failed count）/ log 寫 `_card_type_backfill.log` / `WHERE cardID = ?` / `COALESCE(?, card_type)` / parser inline → **Task 1**
- [x] DB backup + live 試跑 10 張 + 人工 spot-check + 顯式 PAUSE → **Task 2**
- [x] 放量跑全量 21,542 + 監看 log + 完成判定 → **Task 3**
- [x] verify 腳本含三桶分布 + 三個 sanity SQL + 各桶抽樣 10 + jp_term_dict cross-check → **Task 4**
- [x] 每 Task 結尾 commit 步驟、commit message 繁中、commit 前 `git status` + `git diff --numstat`
- [x] Safeguards：DB backup / 範圍明確 / gating SQL 確認 → 在 plan 開頭明列、每 Task 套用
- [x] Follow-up section（下方）

**Placeholder scan：** 沒有「TBD」「TODO」「add appropriate error handling」「similar to」等占位語。每步驟都有具體指令或具體 SQL 或具體 code。

**Type consistency：** `parse_card_type()` 函式簽名（`html: str → 'pokemon' | 'trainer' | 'energy' | None`）三個 Task 一致；DB schema（`jp_card_list.card_type TEXT`、`WHERE cardID = ?`）一致；log 檔名 `_card_type_backfill.log` 三 Task 一致。

---

## Follow-up（本 plan 不做、紀錄到 PROGRESS.md）

1. **`jp_card_list` 整表不在 `app/database.py:init_db()`**：本表是 phaseB ALTER 出來的、未來在新環境跑 `init_db()` 會缺整個 jp_card_list / jp_card_list_set / card_type / detail_synced_at 等欄位。建議補一個 `CREATE TABLE IF NOT EXISTS jp_card_list (...)` 到 `init_db()`。**本 plan 範圍不含、僅標記。**

2. **schema CHECK 約束**：Task 4 verify 若全綠、可考慮加 `CHECK (card_type IN ('pokemon', 'trainer', 'energy'))` 到 jp_card_list、避免未來寫入錯誤值。需要 `CREATE TABLE ... CHECK ...`（SQLite ALTER 不支援加 CHECK、要 rebuild table）。**本 plan 不做。**

3. **`pokemon hp IS NULL` 殘留**：若 Task 4 Sanity #1 顯示 > 50 張、跟本任務無關（v2 crawl 該抓 hp 的）、但值得 file 一個獨立 follow-up 去查 parser 漏哪些舊期格式。

---

## Execution Handoff

Plan 完成、存到 `docs/superpowers/plans/2026-05-19-jp-card-type-backfill.md`。兩種 execution 選項：

1. **Subagent-Driven（推薦）**：每個 Task 派一個 fresh subagent、Task 間 review 一次、快速迭代。特別適合 Task 2 的「PAUSE for user」斷點。
2. **Inline Execution**：在本 session 用 `executing-plans` 跑、批次 checkpoint review。Task 3 是 ~12h 跑、需要 background job 模式。

**建議走 Subagent-Driven**：Task 2 結尾有人工 spot-check 斷點、Task 3 是長時 background job 適合切換 session、Task 4 verify 跑完報告也適合獨立 review。
