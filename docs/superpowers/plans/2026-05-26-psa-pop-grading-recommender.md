# PSA POP 真實鑑定存世量整合 + 該不該送鑑定推薦器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把卡片詳情頁的「PSA 拍賣分布」舊區塊（資料是「拍賣筆數」、不是真 POP）升級為「真 PSA POP（存世量）+ 該不該送鑑定推薦器」、含 Dashboard 風 UI、4 級鑑定費下拉、3 幣別切換、淨利 + 通過率雙標準推薦判斷。

**Architecture:** Backend 透過既有 `PSASession`（過 Cloudflare 的 stealth playwright session）打 PSA 官方 `populationSummary` API 取真 POP、寫入 `card_list` 既有 POP 欄位（schema 已就位無需 ALTER）。詳情頁 endpoint 多帶 SNKR 裸卡平均 + PSA 10 中位數兩個 subquery。前端 Dashboard 用 conic-gradient 圓圈 + 試算表 + JS 即時重算推薦。Lazy refresh：30 天過期 + 每張卡 24h 內 2 次限速、用 `psa_pop_refresh_log` 小表追蹤。

**Tech Stack:**
- Backend: FastAPI、SQLite (cards.db)、playwright + playwright-stealth（既有 `PSASession`）
- Frontend: 純 HTML / CSS / JS（單檔 SPA `..\卡波\index.html`）、Chart.js（既有、不動）
- 沒有正式 test 套件（CLAUDE.md 規定）— 每個 task 用 manual verification command 取代 unit test

**Pre-requisites:**
- Spec：`docs/superpowers/specs/2026-05-25-psa-pop-grading-recommender-design.md` (commit b05d2a5)
- 既有 `app/scraper/psa_apr.py` `PSASession` 類別正常運作
- `psa_apr_card_mapping` 表有 6,889 個 spec_id 對映可重用
- 工作目錄：`C:\Users\Dong Ying\Desktop\Cardpool Price Searching\`

**File Structure:**
- Create: `_backfill_psa_pop.py`（local-only、`.gitignore` 排除、開發 backfill 腳本）
- Create: `_test_psa_pop_spotcheck.py`（local-only、verification helper）
- Modify: `app/scraper/psa_apr.py`（加 `PSASession.get_population_summary(spec_id)` method）
- Modify: `app/database.py`（init_db 加 `psa_pop_refresh_log` 表 CREATE）
- Modify: `app/main.py`（改 `get_card_detail` SQL + response + lazy refresh + 加 manual refresh endpoint）
- Modify: `..\卡波\index.html`（刪舊 PSA 拍賣分布、新增 Dashboard HTML/CSS/JS）
- DB 改動：清掉 `card_list` 既有 3,575 卡 psa_pop 欄位、新增 `psa_pop_refresh_log` 表、backfill 高稀有度 ~7,000-9,000 卡

---

## Phase 1：資料層（~1 天）

### Task 1：backup cards.db 並建 psa_pop_refresh_log 表

**Files:**
- Backup: `cards.db.before-psa-pop-backfill-YYYYMMDD-HHMMSS`
- Modify: `app/database.py`（init_db 加 CREATE TABLE）

- [ ] **Step 1：backup cards.db**

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item cards.db "cards.db.before-psa-pop-backfill-$ts"
```

驗證：`ls cards.db*` 看到新 backup 檔、size ≈ 850MB。

- [ ] **Step 2：寫 init_db 加 psa_pop_refresh_log CREATE TABLE**

讀 `app/database.py` 找到 `init_db()` 函式內最後一個 `CREATE TABLE IF NOT EXISTS` 後、加入：

```python
    conn.execute("""
        CREATE TABLE IF NOT EXISTS psa_pop_refresh_log (
            set_id      TEXT NOT NULL,
            card_number TEXT NOT NULL,
            refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_psa_pop_refresh_log_card
          ON psa_pop_refresh_log(set_id, card_number, refreshed_at)
    """)
```

- [ ] **Step 3：手動執行一次 init_db 套用新表**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "from app.database import init_db; init_db(); print('init_db OK')"
```

預期：印 `init_db OK`、不 raise。

- [ ] **Step 4：驗證新表存在**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute(\"SELECT sql FROM sqlite_master WHERE name='psa_pop_refresh_log'\").fetchone()
print('table:', r)
r2 = c.execute(\"SELECT sql FROM sqlite_master WHERE name='idx_psa_pop_refresh_log_card'\").fetchone()
print('index:', r2)
"
```

預期：兩個 print 都出現 CREATE 語句。

- [ ] **Step 5：commit**

```bash
git add app/database.py
git commit -m "$(cat <<'EOF'
feat(db): add psa_pop_refresh_log table for lazy refresh rate limit

每張卡 24h 內最多 2 次 PSA POP refresh 的限速追蹤表、
與設計 spec doc/superpowers/specs/2026-05-25-psa-pop-grading-recommender-design.md §5.3 對應。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2：清掉舊「拍賣筆數」資料

**Files:** 直接對 cards.db 跑 SQL（不寫進 code）

- [ ] **Step 1：先 count 確認影響範圍**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute('SELECT COUNT(*) FROM card_list WHERE psa_pop_total IS NOT NULL').fetchone()
print('將清掉:', r[0], '張卡的舊 psa_pop_* 資料')
"
```

預期：印 `將清掉: 3575 張卡...`（±誤差幾張、近期可能變動）。

- [ ] **Step 2：執行 UPDATE SET NULL**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
n = c.execute('''
    UPDATE card_list
    SET psa_pop10 = NULL, psa_pop9 = NULL, psa_pop8 = NULL,
        psa_pop7 = NULL, psa_pop6 = NULL, psa_pop5 = NULL,
        psa_pop_total = NULL, psa_gem_rate = NULL,
        psa_pop_updated_at = NULL
    WHERE psa_pop_total IS NOT NULL
''').rowcount
c.commit()
print('cleared:', n)
"
```

預期：印 `cleared: 3575`（±誤差）。

- [ ] **Step 3：驗證清乾淨**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute('SELECT COUNT(*) FROM card_list WHERE psa_pop_total IS NOT NULL').fetchone()
print('還剩:', r[0], '張有資料 (應為 0)')
"
```

預期：印 `還剩: 0`。

（這個 step 不 commit、純 DB 操作、backup 已在 task 1 完成）

---

### Task 3：在 PSASession 加 get_population_summary method

**Files:**
- Modify: `app/scraper/psa_apr.py`（在 `PSASession` class 內加新 method）

- [ ] **Step 1：讀 psa_apr.py 看 PSASession class 結構**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import inspect
from app.scraper.psa_apr import PSASession
print([m for m in dir(PSASession) if not m.startswith('_')])
"
```

預期：印 `['close', 'get_sales_history', 'open', 'search_spec_ids']`。

- [ ] **Step 2：在 `get_sales_history` 下方新加 method**

修改 `app/scraper/psa_apr.py`、在 `get_sales_history` 之後（約 line 150 後）加：

```python
    # ---- populationSummary（真 PSA POP 存世量） ----
    POP_API_URL = f"{PSA_BASE}/api/psa/researchJourney/spec/{{spec_id}}/PSA/populationSummary?filter=all"

    def get_population_summary(self, spec_id: str) -> dict | None:
        """打 PSA 官方 populationSummary API、回傳完整 POP dict 或 None。

        回傳格式（成功）：
        {
          "total": {"totalCount": int},
          "gemRate": float,
          "grade10": {"totalCount": int},
          "grade9": {"totalCount": int},
          ... grade1..grade10 / grade1_5..grade9_5 / authentic
        }
        """
        url = self.POP_API_URL.format(spec_id=spec_id)
        try:
            resp = self._page.request.get(url, timeout=20000)
            if resp.status != 200:
                return None
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict) or "total" not in data:
            return None
        return data
```

- [ ] **Step 3：smoke test — Magikarp spec=8422222（spike 已驗）**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
from app.scraper.psa_apr import PSASession
with PSASession() as s:
    pop = s.get_population_summary('8422222')
    if pop:
        print('total:', pop['total']['totalCount'])
        print('gemRate:', pop['gemRate'])
        print('grade10:', pop['grade10']['totalCount'])
    else:
        print('FAIL')
"
```

預期：印
```
total: 59120
gemRate: 91.19
grade10: 53912
```

若失敗（Cloudflare 擋）retry 一次。若仍失敗、stop + 通報。

- [ ] **Step 4：commit**

```bash
git add app/scraper/psa_apr.py
git commit -m "$(cat <<'EOF'
feat(scraper): add PSASession.get_population_summary for real PSA POP

打 PSA 官方 populationSummary API 拿真實鑑定存世量、與
salesHistory 走同一個 stealth session、共用 Cloudflare cookie。

Spike 驗證：Magikarp #80 spec=8422222 拿到 total=59120 / PSA10=53912 / GEM=91.19%

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4：寫 _backfill_psa_pop.py 腳本

**Files:**
- Create: `_backfill_psa_pop.py`（local-only、`.gitignore` 規則 `_*.py` 自動排除）

- [ ] **Step 1：建立腳本**

寫 `_backfill_psa_pop.py`、完整內容：

```python
"""
PSA POP backfill 腳本（high-rarity 卡優先）

- 用 PSASession 過 Cloudflare、reuse session 跑完全部
- 對 SAR / UR / SR / MUR 卡、有 spec_id 直接打 POP API、沒 spec_id 先 search
- per-card commit（避免長 tx 鎖死其他 SQL）
- 可中斷續跑：gating WHERE psa_pop_updated_at IS NULL
- 每張 2 秒 sleep、撞 Cloudflare retry 3 次（30s / 2min / 5min）
- 跑不到 spec_id 寫入 _psa_pop_unmatched.txt
"""
from __future__ import annotations
import os, sys, sqlite3, time, signal
from pathlib import Path

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(Path(__file__).parent))

from app.scraper.psa_apr import PSASession, build_search_query

DB = Path(__file__).parent / "cards.db"
UNMATCHED_LOG = Path(__file__).parent / "_psa_pop_unmatched.txt"

# 高稀有度 + IS NULL gating
SELECT_PENDING = """
SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.rarity,
       cs.name AS set_name,
       m.spec_id
FROM card_list cl
LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
LEFT JOIN psa_apr_card_mapping m
       ON m.set_id = cl.set_id AND m.card_number = cl.card_number
WHERE cl.rarity IN ('SAR', 'UR', 'SR', 'MUR')
  AND cl.psa_pop_updated_at IS NULL
ORDER BY cl.set_id, cl.card_number
"""

UPDATE_POP = """
UPDATE card_list
SET psa_pop10 = ?, psa_pop9 = ?, psa_pop8 = ?, psa_pop7 = ?, psa_pop6 = ?, psa_pop5 = ?,
    psa_pop_total = ?, psa_gem_rate = ?, psa_pop_updated_at = datetime('now')
WHERE set_id = ? AND card_number = ?
"""

INSERT_MAPPING = """
INSERT OR REPLACE INTO psa_apr_card_mapping (set_id, card_number, spec_id, updated_at)
VALUES (?, ?, ?, datetime('now'))
"""


def pop_to_update_args(pop: dict, set_id: str, card_number: str) -> tuple:
    g = lambda k: (pop.get(k) or {}).get("totalCount")
    return (
        g("grade10"), g("grade9"), g("grade8"), g("grade7"), g("grade6"), g("grade5"),
        pop["total"]["totalCount"], pop.get("gemRate"),
        set_id, card_number,
    )


def main(limit: int | None = None):
    conn = sqlite3.connect(str(DB), timeout=30)
    cur = conn.cursor()
    rows = cur.execute(SELECT_PENDING).fetchall()
    total = len(rows)
    if limit:
        rows = rows[:limit]
    print(f"pending high-rarity cards: {total}, this run: {len(rows)}")

    ok = 0; fail = 0; unmatched = 0
    sess = PSASession().open()

    interrupted = [False]
    def _sigint(signum, frame): interrupted[0] = True; print("\nSIGINT received, finishing current card...")
    signal.signal(signal.SIGINT, _sigint)

    try:
        for i, (set_id, cn, name, name_jp, rarity, set_name, spec_id) in enumerate(rows, 1):
            if interrupted[0]: break
            try:
                if not spec_id:
                    q = build_search_query(set_name or "", name or name_jp, cn)
                    candidates = sess.search_spec_ids(q)
                    spec_id = candidates[0] if candidates else None
                    if spec_id:
                        conn.execute(INSERT_MAPPING, (set_id, cn, spec_id))
                        conn.commit()
                if not spec_id:
                    unmatched += 1
                    with open(UNMATCHED_LOG, "a", encoding="utf-8") as f:
                        f.write(f"{set_id}\t{cn}\t{name}\t{name_jp}\t{rarity}\n")
                    continue

                pop = None
                for attempt, backoff in [(1, 0), (2, 30), (3, 120)]:
                    if backoff: time.sleep(backoff)
                    pop = sess.get_population_summary(spec_id)
                    if pop: break

                if not pop:
                    fail += 1
                    print(f"[{i}/{len(rows)}] FAIL {set_id}/{cn} spec={spec_id}")
                else:
                    conn.execute(UPDATE_POP, pop_to_update_args(pop, set_id, cn))
                    conn.commit()
                    ok += 1
                    print(f"[{i}/{len(rows)}] OK {set_id}/{cn} pop10={(pop.get('grade10') or {}).get('totalCount')} total={pop['total']['totalCount']}")

                time.sleep(2)
            except KeyboardInterrupt:
                interrupted[0] = True; break
            except Exception as e:
                fail += 1
                print(f"[{i}/{len(rows)}] ERR {set_id}/{cn}: {e}")
                time.sleep(2)
    finally:
        sess.close()
        conn.close()
        print(f"\nDONE: ok={ok} fail={fail} unmatched={unmatched}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    main(args.limit)
```

- [ ] **Step 2：dry-run 5 張卡測試**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe _backfill_psa_pop.py --limit 5
```

預期：印 5 行 `[i/5] OK set_id/cn pop10=N total=M`、最後 `DONE: ok=5 fail=0 unmatched=0`。
若有 unmatched、檢查 `_psa_pop_unmatched.txt` 確認原因。

- [ ] **Step 3：驗證 5 張卡寫進 DB**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
rs = c.execute('SELECT set_id, card_number, name, psa_pop10, psa_pop_total, psa_gem_rate, psa_pop_updated_at FROM card_list WHERE psa_pop_updated_at IS NOT NULL ORDER BY psa_pop_updated_at DESC LIMIT 5').fetchall()
for r in rs:
    print(r)
"
```

預期：印 5 行最近寫入的卡、`psa_pop10` 數字看起來合理（不再是「拍賣筆數」級別、是「真 POP」級別 — 例如熱門卡 PSA 10 = 千或萬位）。

- [ ] **Step 4：commit `.gitignore`（如果改了）**

```bash
git status
```
若 `_backfill_psa_pop.py` 沒出現在 untracked、表示 `.gitignore` 已排除、不用動。
若有出現、verify `.gitignore` 含 `_*.py` rule、不另 commit 該腳本。

（不 commit 腳本本身、它是 local-only）

---

### Task 5：跑全量高稀有度 backfill

**Files:** 純資料填充、無 code

- [ ] **Step 1：估算待跑數量**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute(\"SELECT COUNT(*) FROM card_list WHERE rarity IN ('SAR','UR','SR','MUR') AND psa_pop_updated_at IS NULL\").fetchone()
print('待跑高稀有度卡:', r[0])
print('預估時間:', r[0] * 2 / 3600, '小時 (2秒/張)')
"
```

預期：~7,000-9,000 張、~4-5 小時。

- [ ] **Step 2：背景啟動全量 backfill**

```powershell
$env:PYTHONIOENCODING="utf-8"; Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "_backfill_psa_pop.py" -RedirectStandardOutput "_backfill_psa_pop.log" -RedirectStandardError "_backfill_psa_pop.err" -NoNewWindow -PassThru
```

預期：印 PID、process 在背景跑。

- [ ] **Step 3：每 30-60 分鐘看一次進度**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
done = c.execute(\"SELECT COUNT(*) FROM card_list WHERE rarity IN ('SAR','UR','SR','MUR') AND psa_pop_updated_at IS NOT NULL\").fetchone()[0]
total = c.execute(\"SELECT COUNT(*) FROM card_list WHERE rarity IN ('SAR','UR','SR','MUR')\").fetchone()[0]
print(f'進度: {done}/{total} ({100*done/total:.1f}%)')
"
```

依 CLAUDE.md 規則「長時爬蟲期間每 30-60 min 主動查 row 數」。

若連續 100 卡 0 row 寫入 → 異常、stop + 通報。

- [ ] **Step 4：等跑完 + spot-check 10 張**

當 done == total 或 `_backfill_psa_pop.log` 印 `DONE`、停腳本（若還活著）。

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
rs = c.execute('''
    SELECT set_id, card_number, name, rarity, psa_pop10, psa_pop_total, psa_gem_rate
    FROM card_list
    WHERE psa_pop_updated_at IS NOT NULL AND rarity IN ('SAR','UR','SR','MUR')
    ORDER BY psa_pop_total DESC
    LIMIT 10
''').fetchall()
for r in rs:
    print(r)
"
```

預期：印 10 行、`psa_pop10` / `psa_pop_total` 數字看起來合理（熱門卡萬位數、冷門 SR 數百~數千）。

- [ ] **Step 5：把 Magikarp 拿來校對 spike 結果**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute('SELECT psa_pop10, psa_pop_total, psa_gem_rate FROM card_list WHERE set_id LIKE \"%Triple-Beat\" AND card_number=\"80\"').fetchone()
print('Magikarp #80:', r)
# 預期: (53912, 59120, 91.19) ±幾百張誤差（PSA 即時資料）
"
```

預期：印 `(53912, 59120, 91.19)` 或非常接近（PSA 每日新增鑑定數）。若差很多、表示 backfill 邏輯有 bug。

（不 commit、純資料填充）

---

## Phase 2：後端 API（~半天）

### Task 6：改 get_card_detail SQL 加裸卡平均 + PSA 10 中位數

**Files:**
- Modify: `app/main.py`（`get_card_detail` 函式）

- [ ] **Step 1：找 get_card_detail 函式位置**

```powershell
$env:PYTHONIOENCODING="utf-8"; Select-String -Path app/main.py -Pattern "def get_card_detail" -SimpleMatch | Select-Object LineNumber, Line
```

預期：印一行 `LineNumber: ~1990  Line: async def get_card_detail(...)`。記下 line number。

- [ ] **Step 2：找 SQL SELECT 開頭 line（內含 psa_pop10）**

```powershell
$env:PYTHONIOENCODING="utf-8"; Select-String -Path app/main.py -Pattern "cl\.psa_pop10" | Select-Object LineNumber, Line
```

預期：印一行 `LineNumber: ~2026`。

- [ ] **Step 3：修改 SQL — 在 FROM 之前加 3 個 subquery**

讀 `app/main.py` line 2020-2050（含 SELECT clause）、找到 `FROM card_list cl` 行、在其上方 SELECT 欄位最後一個欄位之後加：

```sql
,
                      (SELECT AVG(price_jpy)
                       FROM card_prices
                       WHERE set_id=cl.set_id AND card_number=cl.card_number
                         AND source='snkrdunk'
                         AND (psa_grade IS NULL OR psa_grade = 0)
                         AND sold_at > datetime('now', '-90 days')
                      ) AS snkr_raw_avg_jpy,

                      (SELECT price_jpy
                       FROM card_prices
                       WHERE set_id=cl.set_id AND card_number=cl.card_number
                         AND psa_grade=10
                         AND sold_at > datetime('now', '-90 days')
                       ORDER BY price_jpy
                       LIMIT 1 OFFSET (
                         SELECT (COUNT(*)-1)/2 FROM card_prices
                         WHERE set_id=cl.set_id AND card_number=cl.card_number
                           AND psa_grade=10 AND sold_at > datetime('now', '-90 days')
                       )
                      ) AS psa10_market_jpy,

                      (SELECT COUNT(*)
                       FROM card_prices
                       WHERE set_id=cl.set_id AND card_number=cl.card_number
                         AND psa_grade=10 AND sold_at > datetime('now', '-90 days')
                      ) AS psa10_market_n
```

- [ ] **Step 4：找 response dict 拼裝位置**

```powershell
$env:PYTHONIOENCODING="utf-8"; Select-String -Path app/main.py -Pattern '"psa_pop10":' | Select-Object LineNumber
```

預期：印多個 LineNumber（每個 NULL fallback 處）。記下「主要 row 拼裝那塊」的 line。

- [ ] **Step 5：在 response dict 主拼裝塊加 3 個欄位 + psa_pop nested object**

找到 `"psa_pop10": row["psa_pop10"]` 那行附近、在它後面加：

```python
            "snkr_raw_avg_jpy": row["snkr_raw_avg_jpy"] if row else None,
            "psa10_market_jpy": row["psa10_market_jpy"] if row else None,
            "psa10_market_n": row["psa10_market_n"] if row else 0,
            "psa10_market_source": (
                f"db_psa10_90d_n{row['psa10_market_n']}"
                if row and (row["psa10_market_n"] or 0) >= 5
                else ("psa_official_fallback" if row else None)
            ),
```

並同步在所有「row is None」的 fallback dict（grep 出有 2-3 個）加：
```python
                    "snkr_raw_avg_jpy": None,
                    "psa10_market_jpy": None,
                    "psa10_market_n": 0,
                    "psa10_market_source": None,
```

- [ ] **Step 6：重啟 API**

```powershell
$pid_ = (netstat -ano | findstr ":8000.*LISTENING").Split()[-1]
if ($pid_) { Stop-Process -Id $pid_ -Force }
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_api.log" -RedirectStandardError "_api.err" -NoNewWindow
Start-Sleep -Seconds 4
netstat -ano | findstr ":8000.*LISTENING"
```

預期：印 LISTENING + 新 PID。若沒 listening、看 `_api.err` 排查語法錯誤。

- [ ] **Step 7：spot-check API 回傳**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import json, urllib.request
url = 'http://localhost:8000/api/cards/jp-Triple-Beat/80'
data = json.load(urllib.request.urlopen(url))
print('snkr_raw_avg_jpy:', data.get('snkr_raw_avg_jpy'))
print('psa10_market_jpy:', data.get('psa10_market_jpy'))
print('psa10_market_n:', data.get('psa10_market_n'))
print('psa10_market_source:', data.get('psa10_market_source'))
print('psa_pop10:', data.get('psa_pop10'))
print('psa_gem_rate:', data.get('psa_gem_rate'))
"
```

預期：4 個新欄位都有值（PSA 10 中位數可能 None 若該卡無 PSA 10 成交資料）。

- [ ] **Step 8：commit**

```bash
git add app/main.py
git commit -m "$(cat <<'EOF'
feat(api): get_card_detail multi-query SNKR raw avg + PSA 10 median

詳情頁 endpoint 額外計算：
- snkr_raw_avg_jpy: SNKR source + psa_grade IS NULL 近 90 天裸卡平均
- psa10_market_jpy: PSA 10 近 90 天中位數 (SQL OFFSET 法)
- psa10_market_n: 樣本筆數、< 5 觸發 fallback 標記

對應 spec §6.1.1 / §6.1.2。
重啟 API 後 spot-check jp-Triple-Beat/80 通過。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7：加 lazy refresh trigger + manual refresh endpoint

**Files:**
- Modify: `app/main.py`（get_card_detail 結尾加 trigger、加新 endpoint）

- [ ] **Step 1：在 main.py 適當位置加 helper coroutine**

在 `get_card_detail` 函式之前（同檔案上方）加：

```python
async def _maybe_lazy_refresh_pop(set_id: str, card_number: str, pop_updated_at):
    """超過 30 天 + 24h 內 < 2 次 refresh 才實際打 PSA。non-blocking。"""
    from datetime import datetime, timedelta
    import aiosqlite
    # 30 天 fresh check
    if pop_updated_at:
        try:
            ts = datetime.fromisoformat(str(pop_updated_at).replace("Z", "+00:00"))
            if datetime.now() - ts.replace(tzinfo=None) < timedelta(days=30):
                return
        except Exception:
            pass
    # 24h 限速
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT COUNT(*) FROM psa_pop_refresh_log
            WHERE set_id=? AND card_number=? AND refreshed_at > datetime('now','-24 hours')
        """, (set_id, card_number))
        n = (await cur.fetchone())[0]
        if n >= 2:
            return
    # 觸發實際 refresh（reuse Phase 1 backfill 邏輯 — 簡化為單張 sync）
    try:
        await _do_single_psa_pop_refresh(set_id, card_number)
    except Exception as e:
        print(f"lazy_refresh error {set_id}/{card_number}: {e}")


async def _do_single_psa_pop_refresh(set_id: str, card_number: str):
    """實際打 PSA + 寫 DB + log。run in threadpool to keep sync PSASession sync."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sync_single_psa_pop_refresh, set_id, card_number)


def _sync_single_psa_pop_refresh(set_id: str, card_number: str):
    import sqlite3
    from app.scraper.psa_apr import PSASession, build_search_query
    conn = sqlite3.connect(DB_PATH, timeout=30)
    row = conn.execute("""
        SELECT cl.name, cl.name_jp, cs.name AS set_name, m.spec_id
        FROM card_list cl
        LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
        LEFT JOIN psa_apr_card_mapping m ON m.set_id=cl.set_id AND m.card_number=cl.card_number
        WHERE cl.set_id=? AND cl.card_number=?
    """, (set_id, card_number)).fetchone()
    if not row:
        conn.close(); return
    name, name_jp, set_name, spec_id = row
    with PSASession() as sess:
        if not spec_id:
            q = build_search_query(set_name or "", name or name_jp, card_number)
            cands = sess.search_spec_ids(q)
            spec_id = cands[0] if cands else None
            if spec_id:
                conn.execute("""INSERT OR REPLACE INTO psa_apr_card_mapping
                    (set_id, card_number, spec_id, updated_at)
                    VALUES (?,?,?,datetime('now'))""", (set_id, card_number, spec_id))
                conn.commit()
        if not spec_id:
            conn.close(); return
        pop = sess.get_population_summary(spec_id)
    if pop:
        g = lambda k: (pop.get(k) or {}).get("totalCount")
        conn.execute("""UPDATE card_list SET
            psa_pop10=?, psa_pop9=?, psa_pop8=?, psa_pop7=?, psa_pop6=?, psa_pop5=?,
            psa_pop_total=?, psa_gem_rate=?, psa_pop_updated_at=datetime('now')
            WHERE set_id=? AND card_number=?""",
            (g("grade10"), g("grade9"), g("grade8"), g("grade7"), g("grade6"), g("grade5"),
             pop["total"]["totalCount"], pop.get("gemRate"), set_id, card_number))
        conn.execute("""INSERT INTO psa_pop_refresh_log (set_id, card_number)
            VALUES (?,?)""", (set_id, card_number))
        conn.commit()
    conn.close()
```

- [ ] **Step 2：在 get_card_detail 結尾觸發**

找 `get_card_detail` 函式內 `return ...` 之前、加：

```python
    import asyncio
    asyncio.create_task(_maybe_lazy_refresh_pop(
        set_id, card_number,
        row["psa_pop_updated_at"] if row else None
    ))
```

- [ ] **Step 3：加 manual refresh endpoint**

在 main.py 適當位置（其他 POST endpoint 旁）加：

```python
@app.post("/api/psa/pop/refresh/{set_id}/{card_number}")
async def manual_refresh_psa_pop(set_id: str, card_number: str):
    """手動觸發 PSA POP refresh。受 24h 兩次限速。"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT COUNT(*) FROM psa_pop_refresh_log
            WHERE set_id=? AND card_number=? AND refreshed_at > datetime('now','-24 hours')
        """, (set_id, card_number))
        n = (await cur.fetchone())[0]
        if n >= 2:
            return {"ok": False, "reason": "rate_limited", "next_available_in_hours": 24}
    try:
        await _do_single_psa_pop_refresh(set_id, card_number)
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT psa_pop_updated_at FROM card_list WHERE set_id=? AND card_number=?",
                                   (set_id, card_number))
            ts = (await cur.fetchone())[0]
        return {"ok": True, "updated_at": ts}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
```

- [ ] **Step 4：重啟 API**

```powershell
$pid_ = (netstat -ano | findstr ":8000.*LISTENING").Split()[-1]
if ($pid_) { Stop-Process -Id $pid_ -Force }
Start-Process -FilePath "./Python/bin/python.exe" -ArgumentList "run_api.py" -RedirectStandardOutput "_api.log" -RedirectStandardError "_api.err" -NoNewWindow
Start-Sleep -Seconds 4
netstat -ano | findstr ":8000.*LISTENING"
```

預期：API 重新 listening。

- [ ] **Step 5：spot-check manual refresh endpoint**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/api/psa/pop/refresh/jp-Triple-Beat/80', method='POST')
print(json.load(urllib.request.urlopen(req)))
"
```

預期：印 `{'ok': True, 'updated_at': '2026-05-26 XX:XX:XX'}`（取決於 PSA 是否成功回應）。

第二次馬上打：
```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/api/psa/pop/refresh/jp-Triple-Beat/80', method='POST')
print(json.load(urllib.request.urlopen(req)))
"
```

預期：第二次仍 OK（1/2、未達上限）。
第三次打：
```powershell
# 重複上面第三次
```
預期：`{'ok': False, 'reason': 'rate_limited', 'next_available_in_hours': 24}`。

- [ ] **Step 6：commit**

```bash
git add app/main.py
git commit -m "$(cat <<'EOF'
feat(api): lazy refresh + manual refresh endpoints for PSA POP

- _maybe_lazy_refresh_pop: 30 天過期 + 24h 兩次限制、非阻塞觸發
- POST /api/psa/pop/refresh/{set_id}/{card_number}: 手動觸發
- 限速透過 psa_pop_refresh_log 表

對應 spec §5.5 / §6.2。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 3：前端 Dashboard（~1 天）

### Task 8：刪舊 PSA 拍賣分布區塊

**Files:**
- Modify: `..\卡波\index.html`（刪 line 2940-2997 整段）

- [ ] **Step 1：先找 line 範圍**

```powershell
$env:PYTHONIOENCODING="utf-8"; Select-String -Path "..\卡波\index.html" -Pattern "psa_pop10..pop5 目前來源是 PSA APR" | Select-Object LineNumber
$env:PYTHONIOENCODING="utf-8"; Select-String -Path "..\卡波\index.html" -Pattern '真 PSA Population' | Select-Object LineNumber
```

預期：印 line ~2952 + line ~2988（區塊上下界）。

- [ ] **Step 2：刪除舊區塊 + 留 placeholder anchor**

讀 `..\卡波\index.html` line 2950-2997、整段刪除、改為：

```javascript
  // PSA POP Dashboard 區塊 - 由 buildPsaDashboard() 動態生成
  const popDashboardHtml = buildPsaDashboard(c);
```

且把後面 `${popTable}` 換成 `${popDashboardHtml}`。

- [ ] **Step 3：暫時讓 buildPsaDashboard 回空字串、確認 detail 仍 render**

在 index.html script 區（找其他 helper function 旁）加：

```javascript
function buildPsaDashboard(c) {
  return '<!-- PSA Dashboard placeholder -->';
}
```

- [ ] **Step 4：reload 詳情頁實機驗證舊區塊消失**

開瀏覽器 `http://localhost:8080/index.html?v=20260526-1#/detail?set=jp-Triple-Beat&card=80`、Ctrl+F5 強 reload。

預期：詳情頁正常開、PSA 拍賣分布區塊消失（其他不變）。

（這 step 不 commit、等 task 9-11 寫完整個 Dashboard 一起 commit）

---

### Task 9：寫 Dashboard CSS

**Files:**
- Modify: `..\卡波\index.html`（在 `<style>` 區加 CSS）

- [ ] **Step 1：找 style 區結束 line**

```powershell
$env:PYTHONIOENCODING="utf-8"; Select-String -Path "..\卡波\index.html" -Pattern "^\s*</style>" | Select-Object -First 1 LineNumber
```

- [ ] **Step 2：在 </style> 之前加完整 Dashboard CSS**

加：

```css
/* === PSA POP Dashboard === */
.psa-dashboard { background: #fff; border: 2px solid #4caf50; border-radius: 10px;
  padding: 12px; margin: 12px 0; }
.psa-dashboard.warn { border-color: #ff9800; }
.psa-dashboard.bad  { border-color: #f44336; }
.psa-dashboard.insufficient { border-color: #999; background:#fafafa; }
.psa-rec-header { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.psa-rec-icon { width:38px; height:38px; border-radius:50%; color:#fff;
  display:flex; align-items:center; justify-content:center; font-size:22px; font-weight:800;
  background:#4caf50; }
.psa-dashboard.warn .psa-rec-icon { background:#ff9800; }
.psa-dashboard.bad  .psa-rec-icon { background:#f44336; }
.psa-dashboard.insufficient .psa-rec-icon { background:#999; }
.psa-rec-title { font-weight:800; font-size:16px; }
.psa-rec-reason { font-size:11px; color:#555; }
.psa-rec-currency { margin-left:auto; }
.psa-rec-currency select { font-size:12px; padding:2px 6px; }
.psa-gem-circle-row { display:flex; align-items:center; gap:12px;
  background:#fff8e1; border-radius:8px; padding:10px; margin-bottom:10px; }
.psa-gem-circle { width:64px; height:64px; border-radius:50%;
  display:flex; align-items:center; justify-content:center; position:relative; flex-shrink:0; }
.psa-gem-circle::after { content: attr(data-rate); position:absolute;
  background:#fff; width:48px; height:48px; border-radius:50%;
  display:flex; align-items:center; justify-content:center;
  font-weight:800; font-size:13px; color:#2e7d32; }
.psa-gem-text { flex:1; font-size:12px; line-height:1.5; }
.psa-calc-table { width:100%; border-collapse:collapse; font-size:13px; }
.psa-calc-table td { padding:5px 4px; border-bottom:1px solid #eee; }
.psa-calc-table td:nth-child(2) { text-align:right; font-weight:700; }
.psa-calc-table td.muted { color:#999; font-style:italic; }
.psa-calc-table .profit td { background:#e8f5e9; font-weight:800; color:#1b5e20; border-bottom:none; }
.psa-calc-table .profit.neg td { background:#ffebee; color:#c62828; }
.psa-tier-select { font-size:12px; padding:1px 4px; }
.psa-pop-bar { display:grid; grid-template-columns:repeat(5,1fr);
  gap:4px; margin-top:10px; }
.psa-pop-bar > div { background:#f5f5f5; border-radius:4px;
  padding:6px 4px; text-align:center; font-size:11px; }
.psa-pop-bar .gem { background:#fff3e0; font-weight:700; }
.psa-pop-bar .lbl { font-size:10px; color:#666; display:block; }
.psa-pop-bar .num { font-size:13px; font-weight:700; }
.psa-disclaimer { font-size:11px; color:#888; margin-top:8px;
  text-align:right; font-style:italic; }
.psa-stale-hint { font-size:11px; color:#999; text-align:right;
  margin-top:4px; cursor:pointer; }
.psa-stale-hint:hover { color:#1976d2; text-decoration:underline; }
```

- [ ] **Step 3：reload 確認 CSS 沒打壞既有頁面**

開瀏覽器 hard reload、看其他 view（sets / search）是否仍正常 render。
預期：完全沒視覺影響（CSS 沒被觸發）。

（不 commit、累積到 task 11 一起）

---

### Task 10：寫 buildPsaDashboard JS + 推薦演算式

**Files:**
- Modify: `..\卡波\index.html`（替換 task 8 step 3 的 placeholder）

- [ ] **Step 1：找 task 8 step 3 的 placeholder 並替換**

把：

```javascript
function buildPsaDashboard(c) {
  return '<!-- PSA Dashboard placeholder -->';
}
```

替換為完整 implementation：

```javascript
// PSA POP Dashboard
const PSA_FX = { TWD: 0.22, USD: 0.22 / 32.0, JPY: 1 };
const PSA_SYMBOL = { TWD: 'NT$', USD: '$', JPY: '¥' };
const PSA_TIERS = [
  { v: 3980, label: 'Value Bulk ¥3,980（90 工作日）' },
  { v: 4980, label: 'Value ¥4,980（75 工作日）' },
  { v: 6980, label: 'Value Plus ¥6,980（45 工作日）' },
  { v: 9980, label: 'Regular ¥9,980（25 工作日）' },
];
const PSA_SHIPPING_JPY = 800;

function psaFmt(jpyAmount, currency) {
  if (jpyAmount == null) return '—';
  const rate = PSA_FX[currency] || 1;
  const converted = jpyAmount * rate;
  return PSA_SYMBOL[currency] + ' ' + Math.round(converted).toLocaleString();
}

function calcPsaRecommendation(psa10Market, snkrRaw, psaFee, gemRate) {
  if (gemRate == null || snkrRaw == null || psa10Market == null) {
    return { status: 'insufficient', netProfit: null, icon: '?',
             label: '資料不足', reason: '裸卡或 PSA 10 賣價尚無樣本' };
  }
  const netProfit = psa10Market - snkrRaw - psaFee - PSA_SHIPPING_JPY;
  if (gemRate > 80 && netProfit > 1500) {
    return { status: 'recommended', netProfit, icon: '✓',
             label: '建議送 PSA 鑑定', reason: '通過率高、淨利為正' };
  }
  if (gemRate >= 60 && netProfit > -1500) {
    return { status: 'marginal', netProfit, icon: '⚠',
             label: '邊緣案例', reason: '通過率或淨利在邊緣' };
  }
  return { status: 'not_recommended', netProfit, icon: '✗',
           label: '不推薦送鑑', reason: '通過率偏低或淨利為負' };
}

function buildPsaDashboard(c) {
  const pop10 = c.psa_pop10;
  const popTotal = c.psa_pop_total;
  const gemRate = c.psa_gem_rate ?? (pop10 && popTotal ? (pop10 / popTotal * 100) : null);
  const snkrRaw = c.snkr_raw_avg_jpy;
  const psa10Market = c.psa10_market_jpy;
  const updatedAt = c.psa_pop_updated_at;

  if (pop10 == null && popTotal == null) {
    return `<div class="psa-dashboard insufficient">
      <div class="psa-rec-header">
        <div class="psa-rec-icon">…</div>
        <div>
          <div class="psa-rec-title">資料準備中</div>
          <div class="psa-rec-reason">PSA POP 資料尚未抓取、請稍後再試</div>
        </div>
      </div>
    </div>`;
  }

  const currency = localStorage.getItem('psaCurrency') || 'TWD';
  const tier = parseInt(localStorage.getItem('psaTier') || '4980', 10);
  const rec = calcPsaRecommendation(psa10Market, snkrRaw, tier, gemRate);
  const cls = { recommended:'', marginal:'warn', not_recommended:'bad',
                insufficient:'insufficient' }[rec.status] || '';

  const tierOptions = PSA_TIERS.map(t =>
    `<option value="${t.v}"${t.v === tier ? ' selected' : ''}>${t.label}</option>`
  ).join('');
  const currOptions = ['TWD','USD','JPY'].map(cu =>
    `<option value="${cu}"${cu === currency ? ' selected' : ''}>${PSA_SYMBOL[cu]}（${cu}）</option>`
  ).join('');

  const profitClass = rec.netProfit != null && rec.netProfit < 0 ? 'profit neg' : 'profit';
  const profitDisplay = rec.netProfit != null ? psaFmt(rec.netProfit, currency) : '—';

  const gemRateText = gemRate != null ? gemRate.toFixed(2) + '%' : '—';
  const gemRateDeg = gemRate != null ? (gemRate / 100) : 0;
  const gemColor = gemRate != null && gemRate > 80 ? '#4caf50' :
                   gemRate != null && gemRate >= 60 ? '#ff9800' : '#f44336';
  const gemHint = gemRate == null ? '通過率資料不足' :
                  gemRate > 80 ? '日版剛開包平均 60-80%、這張高於平均' :
                  gemRate >= 60 ? '在日版平均 60-80% 範圍內' :
                  '低於日版剛開包平均 60-80%';

  // 過老提示
  let staleHint = '';
  if (updatedAt) {
    const ageMs = Date.now() - new Date(updatedAt).getTime();
    const ageDays = Math.floor(ageMs / 86400000);
    if (ageDays > 30) {
      staleHint = `<div class="psa-stale-hint" onclick="psaManualRefresh('${c.set_id}','${c.card_number}')">資料 ${ageDays} 天前 · 點此更新</div>`;
    }
  }

  return `
    <div class="psa-dashboard ${cls}">
      <div class="psa-rec-header">
        <div class="psa-rec-icon">${rec.icon}</div>
        <div>
          <div class="psa-rec-title">${rec.label}</div>
          <div class="psa-rec-reason">理由：${rec.reason}</div>
        </div>
        <div class="psa-rec-currency">
          貨幣 <select onchange="psaChangeCurrency(this.value)">${currOptions}</select>
        </div>
      </div>

      <div class="psa-gem-circle-row">
        <div class="psa-gem-circle"
             style="background: conic-gradient(${gemColor} 0 ${gemRateDeg*100}%, #e0e0e0 ${gemRateDeg*100}% 100%);"
             data-rate="${gemRate != null ? Math.round(gemRate) + '%' : '—'}"></div>
        <div class="psa-gem-text">
          <b>PSA 10 通過率 ${gemRateText}</b><br>
          ${gemHint}
        </div>
      </div>

      <table class="psa-calc-table">
        <tr>
          <td>裸卡現價</td>
          <td class="${snkrRaw == null ? 'muted' : ''}">${snkrRaw == null ? '資料不足' : psaFmt(snkrRaw, currency)}</td>
        </tr>
        <tr>
          <td>PSA 鑑定費 <select class="psa-tier-select" onchange="psaChangeTier(this.value)">${tierOptions}</select></td>
          <td>−${psaFmt(tier, currency)}</td>
        </tr>
        <tr>
          <td>運費</td>
          <td>−${psaFmt(PSA_SHIPPING_JPY, currency)}</td>
        </tr>
        <tr>
          <td>PSA 10 預估賣價</td>
          <td class="${psa10Market == null ? 'muted' : ''}">${psa10Market == null ? '資料不足' : psaFmt(psa10Market, currency)}</td>
        </tr>
        <tr class="${profitClass}">
          <td>預估淨利</td>
          <td>${profitDisplay} ${rec.icon}</td>
        </tr>
      </table>

      <div class="psa-pop-bar">
        <div class="gem"><span class="lbl">PSA 10</span><span class="num">${(c.psa_pop10 ?? 0).toLocaleString()}</span></div>
        <div><span class="lbl">PSA 9</span><span class="num">${(c.psa_pop9 ?? 0).toLocaleString()}</span></div>
        <div><span class="lbl">PSA 8</span><span class="num">${(c.psa_pop8 ?? 0).toLocaleString()}</span></div>
        <div><span class="lbl">PSA 7</span><span class="num">${(c.psa_pop7 ?? 0).toLocaleString()}</span></div>
        <div><span class="lbl">≤PSA 6</span><span class="num">${((c.psa_pop6 ?? 0) + (c.psa_pop5 ?? 0)).toLocaleString()}</span></div>
      </div>

      <div class="psa-disclaimer">※ 試算價格僅供參考、未計入平台手續費（SNKR 5.5% / eBay 12%）</div>
      ${staleHint}
    </div>
  `;
}

function psaChangeCurrency(v) {
  localStorage.setItem('psaCurrency', v);
  // 重 render 詳情頁
  if (state.view === 'detail') renderDetail();
}

function psaChangeTier(v) {
  localStorage.setItem('psaTier', v);
  if (state.view === 'detail') renderDetail();
}

async function psaManualRefresh(setId, cardNumber) {
  try {
    const r = await fetch(`${API_BASE}/api/psa/pop/refresh/${setId}/${cardNumber}`, { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      alert('更新成功、重新載入中...');
      if (state.view === 'detail') renderDetail();
    } else if (data.reason === 'rate_limited') {
      alert('已達單張卡 24 小時內 2 次更新上限、請明天再試');
    } else {
      alert('更新失敗：' + (data.reason || 'unknown'));
    }
  } catch (e) {
    alert('更新失敗：' + e.message);
  }
}
```

- [ ] **Step 2：reload 詳情頁實機驗證**

開瀏覽器 hard reload `http://localhost:8080/index.html?v=20260526-2#/detail?set=jp-Triple-Beat&card=80`。

預期：
- Dashboard 區塊顯示出來
- 推薦結論（依資料可能 ⚠ 或 ✗）
- 圓圈通過率 91%
- 試算表 4 行 + 淨利
- POP 5 cell bar 顯示真數字
- 「※ 試算價格僅供參考」小灰字
- 鑑定費下拉 + 幣別下拉 切換時即時重算

- [ ] **Step 3：手動測試切換**

切換鑑定費下拉：4 個級別。
切換幣別下拉：TWD / USD / JPY。
驗證每次切換淨利數字立刻變、且推薦 icon / 顏色對應變化。

（不 commit、累積到 task 11 統一）

---

### Task 11：把 Dashboard 整合到 detail 區塊 final commit

**Files:**
- Modify: `..\卡波\index.html`（多輪改動統一 commit）

- [ ] **Step 1：完整 spot-check 5 張不同卡**

開瀏覽器、分別 visit：
- `#/detail?set=jp-Triple-Beat&card=80`（Magikarp AR）
- `#/detail?set=jp-Crimson-Haze&card=78`（Eevee）
- `#/detail?set=jp-Battle-Partners&card=109`（N's Reshiram）
- 1 張只有 POP 沒裸卡價的卡（降級顯示）
- 1 張完全沒 POP 的卡（資料準備中）

預期：5 張都正常 render、各種降級狀態符合預期。

- [ ] **Step 2：git status 確認改動範圍**

```bash
git status
```
預期：只有 `..\卡波\` 那邊的 index.html 改動（前端 repo 獨立、改動會出現在「卡波」目錄的 git）。

讀 CLAUDE.md「Commit 要按語意拆」、確認沒混到其他改動。

- [ ] **Step 3：commit（在卡波 repo）**

切到 `..\卡波\` 目錄、commit：

```bash
cd "..\卡波"
git add index.html
git commit -m "$(cat <<'EOF'
feat(detail): PSA POP Dashboard + 該不該送鑑定推薦器

- 替換舊「PSA 拍賣分布」區塊
- buildPsaDashboard: 推薦結論 / 圓圈通過率 / 試算表 / 5 cell POP / 免責
- 4 級鑑定費下拉、3 幣別下拉、localStorage 記住
- calcPsaRecommendation: 通過率 + 淨利雙標準
- 過老資料 (> 30 天) 顯示「點此更新」+ psaManualRefresh()
- 對應 spec §7.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
cd "..\Cardpool Price Searching"
```

---

## Phase 4：lazy refresh polish（~半天）

### Task 12：驗證 24h 兩次限制實際運作

**Files:** 無 code、純測試

- [ ] **Step 1：清掉測試卡的 refresh log**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.execute(\"DELETE FROM psa_pop_refresh_log WHERE set_id='jp-Triple-Beat' AND card_number='80'\")
c.commit()
print('cleared')
"
```

- [ ] **Step 2：連續打 3 次 manual refresh、確認第 3 次被擋**

```powershell
1..3 | ForEach-Object {
  $env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import json, urllib.request
req = urllib.request.Request('http://localhost:8000/api/psa/pop/refresh/jp-Triple-Beat/80', method='POST')
r = json.load(urllib.request.urlopen(req))
print('attempt', $_, ':', r)
"
}
```

預期：
- attempt 1: `{'ok': True, 'updated_at': '...'}`
- attempt 2: `{'ok': True, 'updated_at': '...'}`
- attempt 3: `{'ok': False, 'reason': 'rate_limited', 'next_available_in_hours': 24}`

- [ ] **Step 3：檢查 psa_pop_refresh_log 確實記錄 2 筆**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
rs = c.execute(\"SELECT * FROM psa_pop_refresh_log WHERE set_id='jp-Triple-Beat' AND card_number='80' ORDER BY refreshed_at DESC\").fetchall()
for r in rs: print(r)
"
```

預期：印 2 筆 row（attempt 1 + 2、attempt 3 被擋沒寫入）。

（不 commit、純驗證）

---

### Task 13：驗證 lazy refresh 30 天過期觸發

**Files:** 無 code、人為設置過期測試

- [ ] **Step 1：人為設置 Magikarp psa_pop_updated_at 為 31 天前**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
c.execute(\"UPDATE card_list SET psa_pop_updated_at = datetime('now', '-31 days') WHERE set_id='jp-Triple-Beat' AND card_number='80'\")
c.execute(\"DELETE FROM psa_pop_refresh_log WHERE set_id='jp-Triple-Beat' AND card_number='80'\")
c.commit()
print('set as stale 31 days ago + cleared refresh log')
"
```

- [ ] **Step 2：打 detail endpoint 觸發 lazy refresh**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import json, urllib.request
url = 'http://localhost:8000/api/cards/jp-Triple-Beat/80'
data = json.load(urllib.request.urlopen(url))
print('updated_at returned:', data.get('psa_pop_updated_at'))
"
```

- [ ] **Step 3：等 10-30 秒、看 refresh log 是否被寫入**

```powershell
Start-Sleep -Seconds 20
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
rs = c.execute(\"SELECT * FROM psa_pop_refresh_log WHERE set_id='jp-Triple-Beat' AND card_number='80'\").fetchall()
print('refresh log:', rs)
r2 = c.execute(\"SELECT psa_pop_updated_at FROM card_list WHERE set_id='jp-Triple-Beat' AND card_number='80'\").fetchone()
print('new updated_at:', r2)
"
```

預期：refresh log 有 1 筆新 row、`psa_pop_updated_at` 從 31 天前變成近時間（lazy refresh 成功觸發）。

（不 commit、純驗證）

---

### Task 14：驗證資料缺失降級

**Files:** 無 code、找實際缺資料的卡測試

- [ ] **Step 1：找一張只有 POP 沒裸卡價的卡**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
rs = c.execute('''
    SELECT cl.set_id, cl.card_number, cl.name, cl.psa_pop_total,
           (SELECT AVG(price_jpy) FROM card_prices
            WHERE set_id=cl.set_id AND card_number=cl.card_number
              AND source='snkrdunk' AND (psa_grade IS NULL OR psa_grade=0)
              AND sold_at > datetime('now','-90 days')) AS snkr_raw,
           (SELECT COUNT(*) FROM card_prices
            WHERE set_id=cl.set_id AND card_number=cl.card_number
              AND psa_grade=10 AND sold_at > datetime('now','-90 days')) AS psa10_n
    FROM card_list cl
    WHERE cl.psa_pop_total IS NOT NULL AND cl.psa_pop_total > 1000
    ORDER BY cl.set_id, cl.card_number
    LIMIT 200
''').fetchall()
# 找 snkr_raw IS NULL 但 psa10_n > 0 的卡
target = next((r for r in rs if r[4] is None and r[5] > 0), None)
print('target:', target)
"
```

預期：找到一張這類卡（例如冷門 set 的 SR）。記下 set_id / card_number。

- [ ] **Step 2：開瀏覽器看降級顯示**

開 `http://localhost:8080/index.html?v=20260526-3#/detail?set=<target_set>&card=<target_cn>`、hard reload。

預期：
- POP 5 cell bar 正常顯示
- 試算表「裸卡現價」row 顯「資料不足」（灰斜體）
- 推薦結論顯「資料不足、裸卡或 PSA 10 賣價尚無樣本」
- 整體 Dashboard 邊框灰色（insufficient 樣式）

- [ ] **Step 3：找完全沒 POP 的卡測試**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "
import sqlite3
c = sqlite3.connect('cards.db')
r = c.execute(\"SELECT set_id, card_number, name FROM card_list WHERE rarity IN ('C','U') AND psa_pop_total IS NULL ORDER BY set_id LIMIT 1\").fetchone()
print(r)
"
```

開瀏覽器看那張卡的詳情頁。

預期：Dashboard 顯「資料準備中」+ 「PSA POP 資料尚未抓取、請稍後再試」。

（不 commit、純驗證）

---

## Phase 5：驗收 + commit（~半天）

### Task 15：拆 commit + 更新 PROGRESS.md

**Files:**
- Modify: `PROGRESS.md`（新增工作日誌）

- [ ] **Step 1：檢視所有 commit history**

```bash
git log --oneline -20
```

預期：看到 Phase 1-3 的 commits（task 1 / 3 / 6 / 7 / 11）、加上其他既有。

- [ ] **Step 2：PROGRESS.md 加新工作日誌段落**

讀 PROGRESS.md 最後一則工作日誌、append 新段：

```markdown
### 2026-05-26 — PSA POP 真實鑑定存世量整合 + 該不該送鑑定推薦器

#### 完成

**1. Spike 確認 PSA POP API**
- endpoint：`GET /api/psa/researchJourney/spec/{spec_id}/PSA/populationSummary?filter=all`
- 過 Cloudflare 後 200 OK、不需要 PSA 帳號
- 兩張卡 spike 驗證：Magikarp #80 真 POP 59,120 / PSA 10 = 53,912 / GEM 91.19%

**2. spec + plan 文件**
- `docs/superpowers/specs/2026-05-25-psa-pop-grading-recommender-design.md` (b05d2a5)
- `docs/superpowers/plans/2026-05-26-psa-pop-grading-recommender.md`

**3. Phase 1 資料層**
- 新表 `psa_pop_refresh_log`（24h 限速追蹤）
- 清掉舊「拍賣筆數」3,575 卡資料、改填真 PSA POP
- `_backfill_psa_pop.py` 跑高稀有度 ~N 卡（SAR/UR/SR/MUR）、ETA ~5 小時

**4. Phase 2 後端**
- `app/scraper/psa_apr.py`：加 `PSASession.get_population_summary()` method
- `app/main.py` `get_card_detail`：SQL 加 SNKR 裸卡平均 + PSA 10 中位數 subquery
- 加 `_maybe_lazy_refresh_pop`（30 天過期 + 24h 兩次限速、非阻塞）
- 加 `POST /api/psa/pop/refresh/{set_id}/{card_number}` 手動更新 endpoint

**5. Phase 3 前端 Dashboard**
- `..\卡波\index.html`：刪舊「PSA 拍賣分布」區塊、改成 `buildPsaDashboard()`
- 含推薦結論 header / 圓圈通過率 / 試算表 / POP 5 cell bar / 免責
- 4 級鑑定費下拉 + 3 幣別下拉 + localStorage 記住
- `calcPsaRecommendation()` 通過率 + 淨利雙標準
- 過老資料 (> 30 天) 顯示「點此更新」

#### 進行中
無

#### 踩到的坑
無新增

#### 明天的下一步
1. 用戶實機驗收 PSA Dashboard 效果
2. 持續 lazy refresh 對熱門卡資料的累積
3. 後續：考慮跑剩下的低稀有度卡 backfill（~40k 卡、~50 小時）
```

- [ ] **Step 3：commit PROGRESS.md**

```bash
git add PROGRESS.md
git commit -m "$(cat <<'EOF'
docs(progress): PSA POP integration + grading recommender 5/26 工作日誌

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16：user 實機驗收

**Files:** 無

- [ ] **Step 1：給 user 帶 cache buster 的 URL**

```
http://localhost:8080/index.html?v=20260526-FINAL#/detail?set=jp-Triple-Beat&card=80
```

附訊息：
> Dashboard 上線、請打開上面 URL hard reload、實機操作、切換鑑定費下拉、切換幣別、看推薦結論是否合理。也試其他 set。

- [ ] **Step 2：等 user 回饋**

預期 user 給回饋：
- 滿意 → 結束、開展其他工作
- 「淨利顏色不對」/「圓圈角度看不清」/ 等微調 → 個別修

- [ ] **Step 3：依 user 回饋微調 + 補 commit**

依實際 feedback 修 `..\卡波\index.html`、重新 commit。

---

## Self-Review

**Spec coverage（spec 章節 vs plan task）**：
- spec §5.1 schema 已就位 → 不用做任何事 ✓（plan 沒 task、預期）
- spec §5.2 清舊資料 → Task 2 ✓
- spec §5.3 psa_pop_refresh_log 表 → Task 1 ✓
- spec §5.4 backfill 腳本 → Task 4 + Task 5（跑全量）✓
- spec §5.5 lazy refresh 條件 → Task 7（內含 30 天 + 24h 限速邏輯）+ Task 13（驗證）✓
- spec §6.1 改既有 endpoint → Task 6 + Task 7 ✓
- spec §6.2 manual refresh endpoint → Task 7 ✓
- spec §7.1 取代舊 UI 區塊 → Task 8 ✓
- spec §7.2 Dashboard 結構 → Task 10 ✓
- spec §7.3 推薦公式 → Task 10（calcPsaRecommendation）✓
- spec §7.4 鑑定費下拉 → Task 10 ✓
- spec §7.5 幣別下拉 → Task 10 ✓
- spec §7.6 資料缺失降級 → Task 10 + Task 14（驗證）✓
- spec §7.7 過老提示 → Task 10（含 psaManualRefresh）✓
- spec §8 錯誤處理 → 散在 Task 4（retry / unmatched log）+ Task 7（rate limit）+ Task 10（降級）✓
- spec §9 上線順序 5 階段 → 完全對應 Phase 1-5 ✓
- spec §10 回滾 → backup 已在 Task 1、code rollback git revert 不需 task ✓
- spec §11 不做清單 → plan 沒寫任何「不做」內的東西 ✓

**Placeholder scan**：grep 過、無 TBD/TODO/FIXME/XXX。每 step 有完整 code or command。

**Type consistency**：
- `_maybe_lazy_refresh_pop(set_id, card_number, pop_updated_at)` 參數 Task 7 定義、Task 7 step 2 呼叫一致 ✓
- `PSASession.get_population_summary(spec_id)` Task 3 定義、Task 4 / Task 7 重用一致 ✓
- `buildPsaDashboard(c)` Task 8 step 3 placeholder、Task 10 完整實作、Task 8 step 2 呼叫 ✓
- `calcPsaRecommendation(psa10Market, snkrRaw, psaFee, gemRate)` Task 10 唯一定義 ✓
- `psaChangeCurrency(v)` / `psaChangeTier(v)` / `psaManualRefresh(s,c)` Task 10 定義、HTML 內 onclick 呼叫一致 ✓
- DB 欄位名 `psa_pop10/9/8/7/6/5/total` + `psa_gem_rate` + `psa_pop_updated_at` 各 task 一致 ✓
- `psa_pop_refresh_log(set_id, card_number, refreshed_at)` Task 1 定義、Task 7 + 12 + 13 重用一致 ✓

無 issue、plan 可執行。

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-26-psa-pop-grading-recommender.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
