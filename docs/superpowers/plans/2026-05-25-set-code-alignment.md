# Set Code 對映表 — 三表一勞永逸 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans (single session 跑、簡單) 或 superpowers:subagent-driven-development（fresh subagent per task）來照這個 plan 跑。Steps 用 checkbox (`- [ ]`) 追進度。

**Goal:** 補齊 `card_sets.set_code` 欄位、讓 `card_list` / `jp_card_list` / `en_card_list` 三表能透過 set_code 跨表精確識別「同一個卡盒」、解掉 pokemon endpoint UNION 三表時 dedupe key 誤殺 reprint 的副作用。

**Architecture:** card_sets 表已有 `set_code` 欄位但幾乎全 NULL（637 row 內只 3 有填）。本 plan **不新建表**、用既有欄位 backfill。對 jp- prefix slug、靠 `card_sets.name_jp` 比對 `jp_card_list_set.name_jp`（拆「日 (中)」格式）拿 set_code；對 en- prefix slug、靠 `card_sets.name` 比對 `en_card_list.set_name`。對 dedupe key = (set_code, card_number)、跨表撞到表示同卡跨來源、保留優先 source。

**Tech Stack:** SQLite (cards.db)、Python (./Python/bin/python.exe)、FastAPI（app/main.py）

---

## Known Pitfalls 記住的（動工前自我核對）

- **改 DB 前先 backup**：`cp cards.db cards.db.before-set-code-backfill-YYYYMMDD-HHMMSS`
- **改 backend code 必重啟**：HTA 起的 PID 跑舊 code、要 kill + run_api
- **set 名拆解**：jp_card_list_set.name_jp 格式「日 (中)」、用 `lastIndexOf(' (')` 拆
- **NFC normalize**：日文字串比對前先 `unicodedata.normalize("NFC", s)`
- **UNIQUE constraint**：card_sets 對 set_code 沒 UNIQUE、補欄無風險
- **改 backend 要 restart**

---

## Task 1: backup cards.db + 確認現狀

**Files:**
- 沒檔案改、只 backup

- [ ] **Step 1: backup**

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item cards.db "cards.db.before-set-code-backfill-$ts"
```

- [ ] **Step 2: 確認 backup 完成**

```powershell
Get-ChildItem cards.db*.before-set-code-backfill-* | Sort LastWriteTime -Desc | Select -First 1
```

預期：看到剛剛的 backup 檔、size ~800-900 MB。

---

## Task 2: 寫 backfill 腳本（auto-match jp 系 + en 系）

**Files:**
- Create: `_backfill_card_sets_set_code.py`

- [ ] **Step 1: 寫 backfill 腳本**

```python
"""
補齊 card_sets.set_code 欄位、讓 card_list ↔ jp_card_list ↔ en_card_list 三表能用 set_code 跨表對齊。

策略：
1. jp- prefix slug：用 card_sets.name_jp 比對 jp_card_list_set.name_jp（拆「日 (中)」)、找 pg → 從 jp_card_list 反查 set_code。
2. en- prefix slug：用 card_sets.name 比對 en_card_list.set_name → 取 en_card_list.set_id 當 set_code。

兩階段：
- Phase A: auto-match、寫進 card_sets.set_code、印 match_method + confidence
- Phase B: 列 unmatched（沒對到的）給 user 看、之後手動補
"""
import sqlite3
import unicodedata
import re

DB = "cards.db"


def nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s) if s else ""


def jp_set_name_normalize(name_jp: str | None) -> str:
    """jp_card_list_set.name_jp 拆「日 (中)」、抽日文部分、再剝『拡張パック「XX」』前綴"""
    if not name_jp:
        return ""
    s = name_jp
    if " (" in s and s.endswith(")"):
        s = s[:s.rfind(" (")]
    # 剝常見前綴
    for prefix in ("拡張パック", "ハイクラスパック", "強化拡張パック", "ハイクラスデッキ",
                   "スタートデッキ", "プレミアムトレーナーボックス", "デッキビルドBOX",
                   "スターターセット", "Vスタートデッキ"):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
            break
    # 剝外圍「」
    s = s.strip().strip("「」 ")
    return nfc(s)


def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # 1. 抓 jp_card_list_set 全 list、normalize name
    jp_sets = c.execute(
        "SELECT pg, name_jp FROM jp_card_list_set ORDER BY release_date DESC"
    ).fetchall()
    jp_norm_to_pg: dict[str, str] = {}
    for pg, name_jp in jp_sets:
        norm = jp_set_name_normalize(name_jp)
        if norm and norm not in jp_norm_to_pg:
            jp_norm_to_pg[norm] = pg

    # 2. 每個 pg 從 jp_card_list 取最常見 set_code
    pg_to_set_code: dict[str, str] = {}
    for pg, _ in jp_sets:
        row = c.execute(
            "SELECT set_code FROM jp_card_list WHERE pg = ? AND set_code IS NOT NULL "
            "GROUP BY set_code ORDER BY COUNT(*) DESC LIMIT 1",
            (pg,),
        ).fetchone()
        if row and row[0]:
            pg_to_set_code[pg] = row[0]

    # 3. jp 系 backfill：card_sets jp-* → match name_jp → 拿 pg → 拿 set_code
    jp_card_sets = c.execute(
        "SELECT set_id, name_jp FROM card_sets WHERE set_id LIKE 'jp-%' AND set_code IS NULL"
    ).fetchall()

    jp_matched = 0
    jp_no_match = []
    for set_id, name_jp in jp_card_sets:
        norm = jp_set_name_normalize(name_jp) if name_jp else ""
        # 嚴格 match
        pg = jp_norm_to_pg.get(norm) if norm else None
        if pg and pg in pg_to_set_code:
            sc = pg_to_set_code[pg]
            c.execute("UPDATE card_sets SET set_code = ? WHERE set_id = ?", (sc, set_id))
            jp_matched += 1
        else:
            jp_no_match.append((set_id, name_jp, norm))

    # 4. en 系 backfill：card_sets en-* → match name → en_card_list.set_id
    en_card_sets = c.execute(
        "SELECT set_id, name FROM card_sets WHERE set_id LIKE 'en-%' AND set_code IS NULL"
    ).fetchall()

    # 抽 en_card_list distinct (set_id, set_name)
    en_sets = c.execute(
        "SELECT DISTINCT set_id, set_name FROM en_card_list WHERE set_name IS NOT NULL"
    ).fetchall()
    en_name_to_setid: dict[str, str] = {}
    for sid, nm in en_sets:
        if nm:
            key = nm.strip().lower()
            if key and key not in en_name_to_setid:
                en_name_to_setid[key] = sid

    en_matched = 0
    en_no_match = []
    for set_id, name in en_card_sets:
        key = name.strip().lower() if name else ""
        sid = en_name_to_setid.get(key)
        if sid:
            c.execute("UPDATE card_sets SET set_code = ? WHERE set_id = ?", (sid, set_id))
            en_matched += 1
        else:
            en_no_match.append((set_id, name, key))

    conn.commit()

    print(f"=== JP 系 backfill ===")
    print(f"  matched: {jp_matched}")
    print(f"  no_match: {len(jp_no_match)}")
    print("\n--- JP unmatched samples ---")
    for r in jp_no_match[:15]:
        print(r)

    print(f"\n=== EN 系 backfill ===")
    print(f"  matched: {en_matched}")
    print(f"  no_match: {len(en_no_match)}")
    print("\n--- EN unmatched samples ---")
    for r in en_no_match[:15]:
        print(r)

    # 5. 加 index
    print("\n=== 加 index ===")
    c.execute("CREATE INDEX IF NOT EXISTS idx_card_sets_set_code ON card_sets(set_code)")
    conn.commit()
    print("done")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 跑腳本**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe _backfill_card_sets_set_code.py
```

預期：JP matched 200-400、EN matched 100-170、各 unmatched 列出來看。

---

## Task 3: 審 unmatched + 手動補

**Files:**
- Modify: `_backfill_card_sets_set_code.py` 加 `MANUAL_MAP` dict

- [ ] **Step 1: 看 unmatched 列表、判斷 root cause**

跑 Task 2 後印出來 unmatched 列表、人工分類：
- 「set name 寫法不一致」（如「ロケット団の栄光」vs「Glory of Team Rocket」）→ manual map
- 「card_sets 有但 jp_card_list 沒收」（如老 promo set XY-P / SwSh-P）→ 接受 NULL、不勉強對齊
- 「拼字小差異」→ 用 fuzzy match 嘗試

- [ ] **Step 2: 補 MANUAL_MAP dict 進腳本**

格式：
```python
MANUAL_MAP_JP = {
    "jp-Glory-of-Team-Rocket": "M3-P",  # 或對應的 set_code
    "jp-Hot-Air-Arena": "SV12",
    ...
}
MANUAL_MAP_EN = {
    "en-McDonalds-Dragon-Discovery": "mcd25",  # 或對應的 en_card_list.set_id
    ...
}
```

腳本內 apply：
```python
for set_id, code in MANUAL_MAP_JP.items():
    c.execute(
        "UPDATE card_sets SET set_code = ? WHERE set_id = ? AND set_code IS NULL",
        (code, set_id),
    )
```

- [ ] **Step 3: 重跑（idempotent）+ 確認 coverage**

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; c=sqlite3.connect('cards.db').cursor(); print('jp filled:', c.execute(\"SELECT COUNT(*) FROM card_sets WHERE set_id LIKE 'jp-%' AND set_code IS NOT NULL\").fetchone()); print('en filled:', c.execute(\"SELECT COUNT(*) FROM card_sets WHERE set_id LIKE 'en-%' AND set_code IS NOT NULL\").fetchone())"
```

預期：jp_filled > 250、en_filled > 130。

---

## Task 4: 改 pokemon endpoint 用 set_code dedupe

**Files:**
- Modify: `app/main.py` 內 `category_pokemon_cards` (line ~3691-3750)

設計：dedupe key 改成 `(set_code, card_number)`，跨表用 set_code 對齊。card_list 卡若 set_code 跟 jp_card_list / en_card_list 已撈到的卡撞同 (set_code, card_number)、card_list 讓步（不重複顯示）。card_list 卡如果 set_code 是 NULL（即 jp_card_list / en_card_list 沒收這 set）保留。

- [ ] **Step 1: 改 SQL JOIN 取 card_sets.set_code**

card_list JOIN card_sets 已存在、加 `cs.set_code` 進 SELECT。jp_card_list 本身已有 set_code 欄。en_card_list 用 set_id 當 set_code（lowercase code 跟 jp 系 set_code 風格一致）。

```python
# card_list query 改 SELECT 加 cs.set_code
cl_sql = f"""
    SELECT cl.set_id, cs.name AS set_name, cs.name_jp AS set_name_jp,
           cs.release_date, cs.set_code AS _cs_set_code,
           cl.card_number, cl.name, cl.name_jp, cl.name_zh,
           cl.image_url, cl.rarity
    FROM card_list cl
    LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
    WHERE ...
"""

# jp_card_list query 已有 set_code、加進 SELECT
jp_sql = """
    SELECT jcl.pg AS set_id, jcl.set_code AS _cs_set_code,
           jcls.name_jp AS _set_full, jcls.release_date,
           jcl.card_number, jcl.name_jp,
           jcl.thumb_url AS image_url, jcl.rarity
    FROM jp_card_list jcl
    LEFT JOIN jp_card_list_set jcls ON jcls.pg = jcl.pg
    WHERE ...
"""

# en_card_list set_id 當 set_code
en_sql = """
    SELECT set_id, set_id AS _cs_set_code, set_name,
           set_release_date AS release_date,
           number AS card_number, name,
           COALESCE(image_large_url, image_small_url) AS image_url,
           rarity
    FROM en_card_list
    WHERE ...
"""
```

- [ ] **Step 2: dedupe 邏輯改用 (set_code, card_number)**

```python
seen: dict[tuple, dict] = {}
out_rows: list[dict] = []

# 優先 jp_card_list / en_card_list
for r in jp_rows + en_rows:
    sc = r.pop("_cs_set_code", None)
    if not sc:
        out_rows.append(r)
        continue
    key = (sc.upper(), str(r.get("card_number") or ""))
    if key in seen:
        continue
    seen[key] = r
    out_rows.append(r)

# card_list 補
for r in cl_rows:
    sc = r.pop("_cs_set_code", None)
    if not sc:
        # card_sets 沒填 set_code 的、保留（jp_card_list / en_card_list 沒收這 set）
        out_rows.append(r)
        continue
    key = (sc.upper(), str(r.get("card_number") or ""))
    if key in seen:
        continue
    seen[key] = r
    out_rows.append(r)
```

- [ ] **Step 3: 整合進 endpoint**

把整個 `category_pokemon_cards` 改成跟 character endpoint 類似的三表 UNION 結構、但 dedupe 用 set_code。

- [ ] **Step 4: 重啟 backend + 驗證**

```powershell
$listener = (netstat -ano | Select-String ":8000 .*LISTENING" | Select-Object -First 1).ToString().Trim() -split '\s+'
Stop-Process -Id $listener[-1] -Force
./Python/bin/python.exe run_api.py
```

跑 `_test_pokemon_endpoint.py`、預期：
- Pikachu 446 → 470-490（補 jp_card_list 新 set 漏的、不再 -12）
- Charizard 205 → 215-230
- Leafeon 71 → 75-80
- language=jp / language=en filter 也對

---

## Task 5: 改 character endpoint 用 set_code dedupe

**Files:**
- Modify: `app/main.py` 內 `category_character_cards` (line ~3753-3860)

跟 Task 4 同樣思路、把 dedupe key 從 (name_jp/name, card_number) 改成 (set_code, card_number)。

- [ ] **Step 1: 同樣加 _cs_set_code 進 SELECT**

- [ ] **Step 2: dedupe 改 (set_code, card_number)**

- [ ] **Step 3: 重啟 + 驗證 N (id=160)**

預期：N 仍 100-110 張（dedupe 由 (name_jp, card_number) 改 (set_code, card_number)、結果應差不多、不影響）

---

## Task 6: 加 Pitfall 進 PROGRESS.md

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: 加新 Pitfall**

新增：
> **跨表 dedupe 用 (set_code, card_number) 不能用 (name_jp, card_number)**：對 trainer 卡（N / 火箭隊手下 等）reprint 少、(name_jp, card_number) dedupe 也 work；但對寶可夢卡 reprint 多（皮卡丘 #1 在 8 個不同卡盒都當 #1 放）、用 (name_jp, card_number) 會誤殺。要 (set_code, card_number) + 加上 card_sets.set_code 跨表對齊（5/25 補完）。

---

## Task 7: 寫 wrap 進 PROGRESS.md + commit

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 1: 寫今天的工作日誌**
- [ ] **Step 2: 確認 backup 還在、不誤刪**
- [ ] **Step 3: commit**

```powershell
git add app/main.py
git add PROGRESS.md
git commit -m "fix(category): pokemon/character endpoint 用 set_code 跨表 dedupe，補 card_sets.set_code 欄"
```

---

## 自審 (Self-Review)

- ✅ Spec 全包：3 表對映 + endpoint 用 + 驗證
- ✅ 無 placeholder
- ✅ 型別一致（set_code 一律 TEXT 字串 normalize 為 upper）
- ✅ 動工前 backup
- ✅ Pitfall 記下
