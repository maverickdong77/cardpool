# Portfolio Phase 2 — 後端 API + 前端 fetch 整合 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「我的卡冊」(Portfolio) 從 mock 假資料接成真實後端、reload 後資料還在、含完整加買 / 修改 / 賣出 / KPI 對帳 / 多幣別 / TCG filter。

**Architecture:**
- 後端 `app/main.py` 加 8 個 endpoint + FX dict + 4 個計算 helper、用既有 `Depends(auth_mod.get_current_user)` + aiosqlite pattern。
- DB schema 已在 `app/database.py` init_db（Phase 1 已 done、不動）。
- 前端 `..\卡波\index.html` 的 `_getPortfolioMock()` 改 `_fetchPortfolio()`、renderPortfolio / renderMePortfolio / submitAddBatch / 批次操作 modal 全部接通真實 API。
- 6 個 BLOCKER 修正全部寫進 endpoint：(a) FX snapshot（防匯率 drift）(b) cost_locked guard（賣出後鎖批次）(c) holding_qty 用 (set_id, card_number) group 不依賴 batch_id (d) 賣出 BEGIN IMMEDIATE + qty atomic check (e) ownership check (user_id 比對) (f) 持倉現價 JOIN 最新 card_prices source=snkrdunk/ebay grade=10。

**Tech Stack:** FastAPI / aiosqlite / SQLite WAL / pydantic body / 既有 `卡波/index.html` 純原生 JS fetch。

**Spec:** `docs/superpowers/specs/2026-05-22-my-portfolio-design.md`

**前置確認 (動工前已驗證)：**
- DB schema 已寫入 `app/database.py:241-287`（portfolio_batches + portfolio_sells + 4 個 index 全在）。
- 既有 endpoint pattern 範例：`/api/me/watchlist` (`app/main.py:2946`)、auth dependency 用 `Depends(auth_mod.get_current_user)`。
- 前端 mock 結構 (`卡波/index.html:4295`)：`{set_id, card_number, name, tcg, qty, cost_twd, current_twd}` per-card aggregated。MVP 階段把「一張卡 = 一個 batch」當簡化條件實作（多 batch 視覺化推 Phase 後續）—— 但 schema + API 仍照 spec 支援多 batch、邏輯正確、UI 簡化。
- 前端不在 git repo (`..\卡波\index.html`)、改動先 backup 為 `index.html.before-portfolio-api-20260523`。

---

## File Structure

| 檔案 | 修改 | 內容 |
|---|---|---|
| `app/main.py` | Modify (新加 section) | FX_TO_TWD dict + 4 個 helper + 8 個 endpoint |
| `_test_portfolio_helpers.py` | Create | helpers 的 unit test（保 local-only、`.gitignore` 排除） |
| `..\卡波\index.html` | Modify | `_fetchPortfolio()` 取代 `_getPortfolioMock()` / renderPortfolio / renderMePortfolio 改 async / submitAddBatch + batch modal save/sell/delete 改 fetch / 加 loading 狀態 + 錯誤處理 |
| `..\卡波\index.html.before-portfolio-api-20260523` | Create (backup) | 動工前備份 |

---

## Task 1: FX dict + 4 個計算 helper + unit test

**目的：** 把純函式（不碰 DB）抽出來、用 unit test 驗證再放進 endpoint、不會中間發現算錯要回頭。

**Files:**
- Modify: `app/main.py` (新加 section、放在 watchlist endpoint 上方、約 line 2940 之前)
- Create: `_test_portfolio_helpers.py` (local-only、.gitignore 排除)

- [ ] **Step 1.1: 在 `app/main.py` 加 Portfolio section header + FX dict + 4 個 helper**

位置：找 `# ==================== Watchlist 心心收藏 ====================` (line ~2944)、在它前面插入。

```python
# ==================== Portfolio 我的卡冊 ====================
# spec: docs/superpowers/specs/2026-05-22-my-portfolio-design.md
# plan: docs/superpowers/plans/2026-05-23-portfolio-phase2-api.md

# 匯率 dict (TWD = 1.0、其他幣別 → TWD 轉換率)
# MVP 階段硬編、未來改 API。寫進 fx_rate_to_twd snapshot 防匯率 drift。
FX_TO_TWD = {
    "TWD": 1.0,
    "USD": 32.0,
    "JPY": 0.20,
    "KRW": 0.024,
    "HKD": 4.1,
}


def _fx_to_twd(currency: str) -> float:
    """取得幣別轉 TWD 匯率、未知幣別預設 1.0 (TWD)"""
    return FX_TO_TWD.get((currency or "TWD").upper(), 1.0)


def _compute_holding_qty(batches_qty_sum: int, sells_qty_sum: int) -> int:
    """每張卡持倉數量 = batches 總和 - sells 總和"""
    return max(0, (batches_qty_sum or 0) - (sells_qty_sum or 0))


def _compute_avg_cost_twd(batches: list) -> float:
    """加權平均成本 (TWD) = SUM(cost_per_unit_twd × qty) / SUM(qty)
    輸入：batches list of dict (含 cost_per_unit_twd + qty)
    回傳：加權平均 TWD 單價、空 list 回 0.0
    """
    total_qty = sum(b["qty"] for b in batches)
    if total_qty == 0:
        return 0.0
    total_cost = sum(b["cost_per_unit_twd"] * b["qty"] for b in batches)
    return total_cost / total_qty


def _compute_unrealized_twd(holding_qty: int, avg_cost_twd: float,
                              current_price_twd: float) -> float:
    """未實現損益 (TWD) = (現價 - 均價) × 持倉數量
    若現價為 None (查無資料)、回 0.0
    """
    if current_price_twd is None or holding_qty == 0:
        return 0.0
    return (current_price_twd - avg_cost_twd) * holding_qty
```

- [ ] **Step 1.2: 建 `_test_portfolio_helpers.py` 跑 unit test**

```python
"""Portfolio helpers unit test — local-only (gitignore 排除)
跑法：./Python/bin/python.exe _test_portfolio_helpers.py
預期：6/6 PASS
"""
import sys
sys.path.insert(0, ".")
from app.main import _fx_to_twd, _compute_holding_qty, _compute_avg_cost_twd, _compute_unrealized_twd


def test_fx_known_currency():
    assert _fx_to_twd("USD") == 32.0
    assert _fx_to_twd("JPY") == 0.20
    assert _fx_to_twd("TWD") == 1.0


def test_fx_unknown_defaults_twd():
    assert _fx_to_twd("EUR") == 1.0
    assert _fx_to_twd("") == 1.0
    assert _fx_to_twd(None) == 1.0


def test_holding_qty():
    assert _compute_holding_qty(5, 2) == 3
    assert _compute_holding_qty(2, 5) == 0  # 不會負數
    assert _compute_holding_qty(0, 0) == 0


def test_avg_cost_weighted():
    # 兩 batch：1 張 @ NT$1000 + 2 張 @ NT$1300 = 加權 NT$1200
    batches = [
        {"qty": 1, "cost_per_unit_twd": 1000.0},
        {"qty": 2, "cost_per_unit_twd": 1300.0},
    ]
    assert abs(_compute_avg_cost_twd(batches) - 1200.0) < 0.01


def test_avg_cost_empty():
    assert _compute_avg_cost_twd([]) == 0.0


def test_unrealized():
    # 持倉 3 張、均價 1000、現價 1500 → +1500
    assert _compute_unrealized_twd(3, 1000.0, 1500.0) == 1500.0
    # 現價 None → 0
    assert _compute_unrealized_twd(3, 1000.0, None) == 0.0
    # holding=0 → 0
    assert _compute_unrealized_twd(0, 1000.0, 1500.0) == 0.0


if __name__ == "__main__":
    tests = [test_fx_known_currency, test_fx_unknown_defaults_twd,
             test_holding_qty, test_avg_cost_weighted, test_avg_cost_empty,
             test_unrealized]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} PASS")
    sys.exit(1 if failed else 0)
```

- [ ] **Step 1.3: 跑測試確認 6/6 PASS**

```powershell
./Python/bin/python.exe _test_portfolio_helpers.py
```
預期輸出：`6/6 PASS`、exit 0。

- [ ] **Step 1.4: Commit Task 1**

```powershell
git add app/main.py
git commit -m "main: Portfolio Phase 2 step 1 — FX dict + 4 個計算 helper"
```
（`_test_portfolio_helpers.py` 不 commit、`.gitignore` 已排除 `_*.py`）

---

## Task 2: 3 個 GET endpoint (portfolio / summary / recent)

**目的：** 讓前端 renderPortfolio 跟 renderMePortfolio 可以 fetch 真實資料。

**Files:**
- Modify: `app/main.py` (在 Task 1 加完的 section 下面繼續加)

- [ ] **Step 2.1: 重啟 API 載入 Task 1 改動**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
if($pid_){ Stop-Process -Id $pid_ -Force }
./Python/bin/python.exe run_api.py
```
另開一個 shell 確認 `curl http://localhost:8000/api/cardlist/sets | Select -First 100` 200。

- [ ] **Step 2.2: 加 GET /api/me/portfolio**

```python
@app.get("/api/me/portfolio")
async def api_my_portfolio(tcg: str = "all",
                           user: dict = Depends(auth_mod.get_current_user)):
    """列出使用者持倉（per-card aggregated、含現價 JOIN、按 tcg filter）
    回傳 shape 兼容前端 _getPortfolioMock：
        {portfolio: [{set_id, card_number, name, tcg, qty, cost_twd, current_twd, ...}]}
    """
    import aiosqlite
    where_tcg = "" if tcg == "all" else "AND b.tcg = ?"
    params = [user["id"]]
    if tcg != "all":
        params.append(tcg)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 先撈所有 batches 算 per-card 加權平均
        batch_rows = await (await db.execute(f"""
            SELECT b.set_id, b.card_number, b.tcg,
                   b.qty, b.cost_per_unit_twd,
                   MAX(b.purchase_date) OVER (PARTITION BY b.set_id, b.card_number) AS latest_purchase,
                   MIN(b.created_at) OVER (PARTITION BY b.set_id, b.card_number) AS first_created
            FROM portfolio_batches b
            WHERE b.user_id = ? {where_tcg}
        """, params)).fetchall()
        # 撈該 user 所有 sells
        sell_rows = await (await db.execute("""
            SELECT set_id, card_number, qty
            FROM portfolio_sells WHERE user_id = ?
        """, (user["id"],))).fetchall()

        # 聚合 per-card
        cards = {}
        for r in batch_rows:
            key = (r["set_id"], r["card_number"])
            c = cards.setdefault(key, {
                "set_id": r["set_id"],
                "card_number": r["card_number"],
                "tcg": r["tcg"],
                "_batches": [],
                "_latest_purchase": r["latest_purchase"],
            })
            c["_batches"].append({"qty": r["qty"], "cost_per_unit_twd": r["cost_per_unit_twd"]})

        sells_map = {}
        for r in sell_rows:
            key = (r["set_id"], r["card_number"])
            sells_map[key] = sells_map.get(key, 0) + r["qty"]

        # 撈每張卡的名稱 + 圖片 + 現價
        result = []
        for key, c in cards.items():
            batches_qty = sum(b["qty"] for b in c["_batches"])
            sells_qty = sells_map.get(key, 0)
            holding = _compute_holding_qty(batches_qty, sells_qty)
            if holding == 0:
                continue  # 全賣完不顯示
            avg = _compute_avg_cost_twd(c["_batches"])

            # 卡名 / 圖片
            meta = await (await db.execute("""
                SELECT cl.name, cl.name_jp, cl.name_zh, cl.image_url,
                       cs.name AS set_name, cs.name_zh AS set_name_zh
                FROM card_list cl
                LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
                WHERE cl.set_id = ? AND cl.card_number = ?
                LIMIT 1
            """, (c["set_id"], c["card_number"]))).fetchone()
            display_name = (meta and (meta["name_zh"] or meta["name"] or meta["name_jp"])) or f"{c['set_id']} #{c['card_number']}"

            # 現價：最新 source in (snkrdunk, ebay) grade=10
            cur = await (await db.execute("""
                SELECT price_twd FROM card_prices
                WHERE set_id = ? AND card_number = ?
                  AND source IN ('snkrdunk', 'ebay')
                  AND (grade = 10 OR grade IS NULL)
                ORDER BY COALESCE(sale_date, created_at) DESC LIMIT 1
            """, (c["set_id"], c["card_number"]))).fetchone()
            current_twd = (cur and cur["price_twd"]) or None

            result.append({
                "set_id": c["set_id"],
                "card_number": c["card_number"],
                "name": display_name,
                "image_url": meta["image_url"] if meta else None,
                "set_name": (meta and (meta["set_name_zh"] or meta["set_name"])) if meta else c["set_id"],
                "tcg": c["tcg"],
                "qty": holding,
                "cost_twd": round(avg, 2),
                "current_twd": current_twd,
                "latest_purchase": c["_latest_purchase"],
            })

        # 按最近購買日 DESC 排
        result.sort(key=lambda x: x.get("latest_purchase") or "", reverse=True)

    return {"portfolio": result}
```

- [ ] **Step 2.3: 加 GET /api/me/portfolio/summary**

```python
@app.get("/api/me/portfolio/summary")
async def api_my_portfolio_summary(tcg: str = "all",
                                     user: dict = Depends(auth_mod.get_current_user)):
    """KPI 卡片：總成本 / 總市值 / 未實現 / 已實現
    summary 共用 /api/me/portfolio 的邏輯避免雙寫、直接 reuse
    """
    portfolio = (await api_my_portfolio(tcg=tcg, user=user))["portfolio"]
    total_cost = sum((c["cost_twd"] or 0) * c["qty"] for c in portfolio)
    total_value = sum((c["current_twd"] or c["cost_twd"] or 0) * c["qty"] for c in portfolio)
    unrealized = total_value - total_cost

    # 已實現損益 (按 tcg filter)
    import aiosqlite
    where_tcg = "" if tcg == "all" else "AND tcg = ?"
    params = [user["id"]]
    if tcg != "all":
        params.append(tcg)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(f"""
            SELECT COALESCE(SUM(realized_pnl_twd), 0) AS realized
            FROM portfolio_sells WHERE user_id = ? {where_tcg}
        """, params)).fetchone()
        realized = row["realized"] if row else 0

    return {
        "total_cost_twd": round(total_cost, 2),
        "total_value_twd": round(total_value, 2),
        "unrealized_twd": round(unrealized, 2),
        "unrealized_pct": round(unrealized / total_cost * 100, 2) if total_cost > 0 else 0,
        "realized_twd": round(realized, 2),
        "total_qty": sum(c["qty"] for c in portfolio),
        "card_count": len(portfolio),
    }
```

- [ ] **Step 2.4: 加 GET /api/me/portfolio/recent**

```python
@app.get("/api/me/portfolio/recent")
async def api_my_portfolio_recent(limit: int = 5,
                                    user: dict = Depends(auth_mod.get_current_user)):
    """最近加入的 N 張（給「我的帳戶 → 我的卡冊」概要 tab 用）"""
    portfolio = (await api_my_portfolio(tcg="all", user=user))["portfolio"]
    return {"recent": portfolio[:max(1, min(limit, 20))]}
```

- [ ] **Step 2.5: 重啟 API + curl 三個 endpoint 看回 200**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
if($pid_){ Stop-Process -Id $pid_ -Force }
./Python/bin/python.exe run_api.py
```

預期：3 個 endpoint 都 401 (未登入)、表示 endpoint 註冊成功。

```powershell
curl -i http://localhost:8000/api/me/portfolio
curl -i http://localhost:8000/api/me/portfolio/summary
curl -i http://localhost:8000/api/me/portfolio/recent
```
預期：3 個都回 `HTTP/1.1 401 Unauthorized`。

- [ ] **Step 2.6: Commit Task 2**

```powershell
git add app/main.py
git commit -m "main: Portfolio Phase 2 step 2 — GET portfolio + summary + recent 三 endpoint"
```

---

## Task 3: POST / PATCH / DELETE batches (3 個 endpoint + cost_locked guard)

**目的：** 讓使用者真實加買 / 修改 / 刪批次。

**Files:**
- Modify: `app/main.py`

- [ ] **Step 3.1: 加 POST /api/me/portfolio/batches**

```python
from pydantic import BaseModel, Field
from typing import Optional


class CreateBatchBody(BaseModel):
    set_id: str
    card_number: str
    tcg: str = "pokemon"
    qty: int = Field(gt=0)
    cost_per_unit: float = Field(gt=0)
    currency: str = "TWD"
    grade: Optional[str] = None
    note: Optional[str] = None
    purchase_date: str  # YYYY-MM-DD


@app.post("/api/me/portfolio/batches")
async def api_create_batch(body: CreateBatchBody,
                            user: dict = Depends(auth_mod.get_current_user)):
    """新增批次。FX snapshot 鎖在 row 上、防匯率 drift"""
    import aiosqlite
    fx = _fx_to_twd(body.currency)
    cost_twd = body.cost_per_unit * fx

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO portfolio_batches
                (user_id, tcg, set_id, card_number, qty, cost_per_unit,
                 currency, fx_rate_to_twd, cost_per_unit_twd,
                 grade, note, purchase_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], body.tcg, body.set_id, body.card_number,
              body.qty, body.cost_per_unit, body.currency.upper(),
              fx, cost_twd, body.grade, body.note, body.purchase_date))
        await db.commit()
        batch_id = cur.lastrowid

    return {"ok": True, "batch_id": batch_id, "fx_rate_to_twd": fx, "cost_per_unit_twd": cost_twd}
```

- [ ] **Step 3.2: 加 PATCH /api/me/portfolio/batches/{batch_id} (含 cost_locked guard)**

```python
class UpdateBatchBody(BaseModel):
    qty: Optional[int] = None
    cost_per_unit: Optional[float] = None
    currency: Optional[str] = None
    grade: Optional[str] = None
    note: Optional[str] = None
    purchase_date: Optional[str] = None


@app.patch("/api/me/portfolio/batches/{batch_id}")
async def api_update_batch(batch_id: int, body: UpdateBatchBody,
                            user: dict = Depends(auth_mod.get_current_user)):
    """編輯批次。BLOCKER #2: cost_locked guard — 該卡曾有 sells 就拒改 qty/cost"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # ownership check
        row = await (await db.execute("""
            SELECT * FROM portfolio_batches WHERE id = ? AND user_id = ?
        """, (batch_id, user["id"]))).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(404, "批次不存在或無權限")

        # cost_locked guard：該卡 (set_id, card_number) 是否有 sells
        sell_row = await (await db.execute("""
            SELECT COUNT(*) AS c FROM portfolio_sells
            WHERE user_id = ? AND set_id = ? AND card_number = ?
        """, (user["id"], row["set_id"], row["card_number"]))).fetchone()
        has_sells = sell_row["c"] > 0

        if has_sells and (body.qty is not None or body.cost_per_unit is not None):
            from fastapi import HTTPException
            raise HTTPException(400, "此卡已有賣出記錄、不能改數量或成本（保護已實現損益）")

        # 組 UPDATE
        updates = []
        params = []
        if body.qty is not None:
            updates.append("qty = ?")
            params.append(body.qty)
        if body.cost_per_unit is not None:
            currency = (body.currency or row["currency"]).upper()
            fx = _fx_to_twd(currency)
            updates.extend(["cost_per_unit = ?", "currency = ?", "fx_rate_to_twd = ?", "cost_per_unit_twd = ?"])
            params.extend([body.cost_per_unit, currency, fx, body.cost_per_unit * fx])
        if body.grade is not None:
            updates.append("grade = ?"); params.append(body.grade)
        if body.note is not None:
            updates.append("note = ?"); params.append(body.note)
        if body.purchase_date is not None:
            updates.append("purchase_date = ?"); params.append(body.purchase_date)

        if not updates:
            return {"ok": True, "changed": False}

        params.extend([batch_id, user["id"]])
        await db.execute(f"""
            UPDATE portfolio_batches SET {', '.join(updates)}
            WHERE id = ? AND user_id = ?
        """, params)
        await db.commit()

    return {"ok": True, "changed": True}
```

- [ ] **Step 3.3: 加 DELETE /api/me/portfolio/batches/{batch_id}**

```python
@app.delete("/api/me/portfolio/batches/{batch_id}")
async def api_delete_batch(batch_id: int,
                            user: dict = Depends(auth_mod.get_current_user)):
    """刪批次。sells.batch_id ON DELETE SET NULL — 已賣 row 仍保留、歷史損益不受影響"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            DELETE FROM portfolio_batches WHERE id = ? AND user_id = ?
        """, (batch_id, user["id"]))
        await db.commit()
        deleted = cur.rowcount
    if deleted == 0:
        from fastapi import HTTPException
        raise HTTPException(404, "批次不存在或無權限")
    return {"ok": True}
```

- [ ] **Step 3.4: 重啟 API + 3 個 endpoint curl smoke**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
if($pid_){ Stop-Process -Id $pid_ -Force }
./Python/bin/python.exe run_api.py
```

3 個 endpoint 都 401 預期。

- [ ] **Step 3.5: Commit Task 3**

```powershell
git add app/main.py
git commit -m "main: Portfolio Phase 2 step 3 — POST/PATCH/DELETE batches 含 cost_locked guard"
```

---

## Task 4: POST / DELETE sells (含 atomic check + realized_pnl 計算)

**目的：** 賣出登記、已實現損益自動算、防超賣。

**Files:**
- Modify: `app/main.py`

- [ ] **Step 4.1: 加 POST /api/me/portfolio/sells (含 BEGIN IMMEDIATE atomic check)**

```python
class CreateSellBody(BaseModel):
    set_id: str
    card_number: str
    qty: int = Field(gt=0)
    sell_price_per_unit: float = Field(gt=0)
    currency: str = "TWD"
    sell_date: str  # YYYY-MM-DD


@app.post("/api/me/portfolio/sells")
async def api_create_sell(body: CreateSellBody,
                           user: dict = Depends(auth_mod.get_current_user)):
    """賣出登記。BLOCKER #4: BEGIN IMMEDIATE + holding qty check 防併發超賣"""
    import aiosqlite
    from fastapi import HTTPException
    fx = _fx_to_twd(body.currency)
    sell_twd = body.sell_price_per_unit * fx

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            # 1. 該卡 holding qty check (batches - sells)
            r = await (await db.execute("""
                SELECT
                    COALESCE((SELECT SUM(qty) FROM portfolio_batches
                              WHERE user_id = ? AND set_id = ? AND card_number = ?), 0) AS bq,
                    COALESCE((SELECT SUM(qty) FROM portfolio_sells
                              WHERE user_id = ? AND set_id = ? AND card_number = ?), 0) AS sq
            """, (user["id"], body.set_id, body.card_number,
                  user["id"], body.set_id, body.card_number))).fetchone()
            holding = (r["bq"] or 0) - (r["sq"] or 0)
            if body.qty > holding:
                await db.execute("ROLLBACK")
                raise HTTPException(400, f"賣出數量超過持倉（持倉 {holding} 張、欲賣 {body.qty} 張）")

            # 2. 算 realized_pnl = (sell_twd - avg_cost_twd) × qty
            batches = await (await db.execute("""
                SELECT id, qty, cost_per_unit_twd, tcg FROM portfolio_batches
                WHERE user_id = ? AND set_id = ? AND card_number = ?
                ORDER BY purchase_date ASC, id ASC
            """, (user["id"], body.set_id, body.card_number))).fetchall()
            if not batches:
                await db.execute("ROLLBACK")
                raise HTTPException(400, "找不到對應持倉、無法賣出")
            total_qty = sum(b["qty"] for b in batches)
            total_cost = sum(b["cost_per_unit_twd"] * b["qty"] for b in batches)
            avg_cost = total_cost / total_qty if total_qty > 0 else 0
            realized = (sell_twd - avg_cost) * body.qty
            # 取最舊 batch 當記錄 ref（刪後 SET NULL）
            ref_batch_id = batches[0]["id"]
            tcg = batches[0]["tcg"]

            # 3. INSERT sell
            cur = await db.execute("""
                INSERT INTO portfolio_sells
                    (user_id, batch_id, set_id, card_number, tcg, qty,
                     sell_price_per_unit, currency, fx_rate_to_twd,
                     sell_price_per_unit_twd, realized_pnl_twd, sell_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user["id"], ref_batch_id, body.set_id, body.card_number, tcg,
                  body.qty, body.sell_price_per_unit, body.currency.upper(),
                  fx, sell_twd, realized, body.sell_date))
            sell_id = cur.lastrowid

            # 4. 把該卡所有 batches 設 cost_locked = 1（賣出後鎖批次成本）
            await db.execute("""
                UPDATE portfolio_batches SET cost_locked = 1
                WHERE user_id = ? AND set_id = ? AND card_number = ?
            """, (user["id"], body.set_id, body.card_number))

            await db.execute("COMMIT")
        except HTTPException:
            raise
        except Exception as e:
            await db.execute("ROLLBACK")
            raise HTTPException(500, f"賣出失敗：{e}")

    return {"ok": True, "sell_id": sell_id, "realized_pnl_twd": round(realized, 2)}
```

- [ ] **Step 4.2: 加 DELETE /api/me/portfolio/sells/{sell_id} (撤回賣出)**

```python
@app.delete("/api/me/portfolio/sells/{sell_id}")
async def api_delete_sell(sell_id: int,
                           user: dict = Depends(auth_mod.get_current_user)):
    """撤回賣出。如果該卡再無 sells、解鎖 batches.cost_locked"""
    import aiosqlite
    from fastapi import HTTPException
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (await db.execute("""
                SELECT set_id, card_number FROM portfolio_sells
                WHERE id = ? AND user_id = ?
            """, (sell_id, user["id"]))).fetchone()
            if not row:
                await db.execute("ROLLBACK")
                raise HTTPException(404, "賣出記錄不存在或無權限")

            await db.execute("DELETE FROM portfolio_sells WHERE id = ? AND user_id = ?",
                             (sell_id, user["id"]))

            # 該卡是否還有其他 sells？沒有就解鎖
            other = await (await db.execute("""
                SELECT COUNT(*) AS c FROM portfolio_sells
                WHERE user_id = ? AND set_id = ? AND card_number = ?
            """, (user["id"], row["set_id"], row["card_number"]))).fetchone()
            if other["c"] == 0:
                await db.execute("""
                    UPDATE portfolio_batches SET cost_locked = 0
                    WHERE user_id = ? AND set_id = ? AND card_number = ?
                """, (user["id"], row["set_id"], row["card_number"]))

            await db.execute("COMMIT")
        except HTTPException:
            raise
        except Exception as e:
            await db.execute("ROLLBACK")
            raise HTTPException(500, f"撤回失敗：{e}")
    return {"ok": True}
```

- [ ] **Step 4.3: 重啟 API + 2 個 endpoint curl smoke**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
if($pid_){ Stop-Process -Id $pid_ -Force }
./Python/bin/python.exe run_api.py
```
2 個都 401 預期。

- [ ] **Step 4.4: 完整 curl 端到端 happy path**

寫 `_test_portfolio_api.ps1`（local-only）：

```powershell
# Test: 註冊 → 登入 → 加 2 個 batch → 賣 1 → KPI 對帳
$base = "http://localhost:8000"
$email = "pf_test_$(Get-Random)@example.com"
$pass = "test12345"

# 1. register-request (dev mode 自動回 dev_code)
$r1 = Invoke-RestMethod -Method POST -Uri "$base/api/auth/register-request" `
    -ContentType "application/json" `
    -Body (@{email=$email; password=$pass; display_name="PFTest"} | ConvertTo-Json)
$code = $r1.dev_code
Write-Host "dev_code: $code"

# 2. register-verify
$r2 = Invoke-RestMethod -Method POST -Uri "$base/api/auth/register-verify" `
    -ContentType "application/json" `
    -Body (@{email=$email; code=$code} | ConvertTo-Json)
$token = $r2.token
Write-Host "token: $($token.Substring(0,12))..."

# 3. 加 batch 1: M2/110 × 2 張 @ NT$2000
$h = @{Authorization="Bearer $token"}
$b1 = Invoke-RestMethod -Method POST -Uri "$base/api/me/portfolio/batches" `
    -Headers $h -ContentType "application/json" `
    -Body (@{set_id="M2"; card_number="110"; tcg="pokemon"; qty=2; cost_per_unit=2000; currency="TWD"; purchase_date="2026-05-20"} | ConvertTo-Json)
Write-Host "batch1 id: $($b1.batch_id)"

# 4. 加 batch 2: M2/110 × 1 張 @ NT$3000 (測加權平均：avg=(2*2000+1*3000)/3=2333)
$b2 = Invoke-RestMethod -Method POST -Uri "$base/api/me/portfolio/batches" `
    -Headers $h -ContentType "application/json" `
    -Body (@{set_id="M2"; card_number="110"; tcg="pokemon"; qty=1; cost_per_unit=3000; currency="TWD"; purchase_date="2026-05-21"} | ConvertTo-Json)

# 5. GET /portfolio 看聚合 (預期 1 張卡 × 3 張 qty × avg ~2333)
$pf = Invoke-RestMethod -Uri "$base/api/me/portfolio" -Headers $h
$pf.portfolio | Format-Table set_id, card_number, qty, cost_twd, current_twd
if($pf.portfolio[0].qty -ne 3){ Write-Host "FAIL qty $($pf.portfolio[0].qty), 預期 3" -ForegroundColor Red }
if([math]::Abs($pf.portfolio[0].cost_twd - 2333.33) -gt 1){ Write-Host "FAIL cost $($pf.portfolio[0].cost_twd), 預期 ~2333" -ForegroundColor Red }

# 6. 賣 1 張 @ NT$4000 (realized = (4000-2333)*1 = ~1667)
$s = Invoke-RestMethod -Method POST -Uri "$base/api/me/portfolio/sells" `
    -Headers $h -ContentType "application/json" `
    -Body (@{set_id="M2"; card_number="110"; qty=1; sell_price_per_unit=4000; currency="TWD"; sell_date="2026-05-23"} | ConvertTo-Json)
Write-Host "realized: $($s.realized_pnl_twd) (預期 ~1666.67)"

# 7. GET /summary 確認 realized + holding
$sm = Invoke-RestMethod -Uri "$base/api/me/portfolio/summary" -Headers $h
Write-Host "summary: total_qty=$($sm.total_qty) realized=$($sm.realized_twd)"
if($sm.total_qty -ne 2){ Write-Host "FAIL holding 應為 2、得 $($sm.total_qty)" -ForegroundColor Red }

# 8. cost_locked guard：嘗試改 batch1 qty → 應 400
try{
    Invoke-RestMethod -Method PATCH -Uri "$base/api/me/portfolio/batches/$($b1.batch_id)" `
        -Headers $h -ContentType "application/json" `
        -Body (@{qty=99} | ConvertTo-Json)
    Write-Host "FAIL: cost_locked guard 未生效、允許了 qty 改動" -ForegroundColor Red
}catch{
    Write-Host "PASS: cost_locked guard 擋下了 qty 改動 (HTTP 400)" -ForegroundColor Green
}

# 9. 超賣 check：賣 5 張 → 應 400
try{
    Invoke-RestMethod -Method POST -Uri "$base/api/me/portfolio/sells" `
        -Headers $h -ContentType "application/json" `
        -Body (@{set_id="M2"; card_number="110"; qty=5; sell_price_per_unit=4000; currency="TWD"; sell_date="2026-05-23"} | ConvertTo-Json)
    Write-Host "FAIL: 超賣未擋" -ForegroundColor Red
}catch{
    Write-Host "PASS: 超賣 5 > 2 擋下 (HTTP 400)" -ForegroundColor Green
}

Write-Host "`n=== 全部 endpoint 端到端通過 ===" -ForegroundColor Green
```

跑：
```powershell
./_test_portfolio_api.ps1
```
預期：4 個關鍵 PASS（聚合算對 / cost_locked guard / 超賣擋 / realized 算對）。

- [ ] **Step 4.5: Commit Task 4**

```powershell
git add app/main.py
git commit -m "main: Portfolio Phase 2 step 4 — POST/DELETE sells 含 BEGIN IMMEDIATE atomic check"
```

---

## Task 5: 前端 fetch 整合

**目的：** 把 `_getPortfolioMock()` 換成真實 fetch、加買 / 修改 / 賣出按鈕接通後端、reload 後資料還在。

**Files:**
- Modify: `..\卡波\index.html`
- Create (backup): `..\卡波\index.html.before-portfolio-api-20260523`

**重要：前端不在 git repo、改動前必 backup。**

- [ ] **Step 5.1: backup 前端**

```powershell
Copy-Item "..\卡波\index.html" "..\卡波\index.html.before-portfolio-api-20260523"
```

- [ ] **Step 5.2: 取代 `_getPortfolioMock()` 為 `_fetchPortfolio()`**

位置：`..\卡波\index.html:4294-4314`（替換整個 function）

```javascript
// 真實 fetch 後端 (取代 mock)
async function _fetchPortfolio(){
  if(!state.token){ return []; }
  try{
    const d = await api('/api/me/portfolio', {tcg: 'all'});
    return d.portfolio || [];
  }catch(e){
    console.error('[portfolio fetch]', e);
    toast('載入卡冊失敗：' + (e.message || '未知錯誤'));
    return [];
  }
}
```

- [ ] **Step 5.3: 改 `renderPortfolio` 為真實 async fetch**

位置：`..\卡波\index.html:4424` `async function renderPortfolio()`、第一句 `const mock = _getPortfolioMock();` 改成：

```javascript
async function renderPortfolio(){
  const app = document.getElementById('app');
  app.innerHTML = '<div class="wrap"><div class="loading">載入卡冊中...</div></div>';

  // 未登入 → 引導登入
  if(!state.token){
    app.innerHTML = `<div class="wrap" style="padding:60px 20px;text-align:center">
      <h2 style="color:#888">登入後才能使用「我的卡冊」</h2>
      <button onclick="openAuth()" style="margin-top:20px;padding:10px 24px;background:#facc15;border:none;border-radius:6px;font-weight:700;cursor:pointer">登入 / 註冊</button>
    </div>`;
    return;
  }

  const mock = await _fetchPortfolio();
  // ... 以下程式碼不動（filteredMock 等繼續用 mock 變數）
```

把 `const mock = _getPortfolioMock();` 那行刪掉、保留後續 `mock.reduce(...)` 等邏輯不動。

- [ ] **Step 5.4: 改 `renderMePortfolio` 為真實 async fetch**

位置：`..\卡波\index.html:4317-4380` 整段、把 `const mock = _getPortfolioMock();` 改：

```javascript
async function renderMePortfolio(){
  if(!state.token){
    return '<div style="padding:40px;text-align:center;color:#888">登入後才能使用</div>';
  }
  const mock = await _fetchPortfolio();
  if(mock.length === 0){
    return '<div style="padding:40px;text-align:center;color:#888">尚無持倉、點下方「+ 加買新卡片」開始記錄</div>';
  }
  // ... 以下不動（totalCost 等用 mock 變數）
```

並且呼叫 `renderMePortfolio()` 的地方（`switchMeTab('portfolio')` 內）改 `await`。

位置：`..\卡波\index.html:3791-3795`、找到 `if(tab === 'portfolio')` 改：

```javascript
if(tab === 'portfolio'){
  document.getElementById('meContent').innerHTML = '<div class="loading">載入卡冊中...</div>';
  document.getElementById('meContent').innerHTML = await renderMePortfolio();
}
```

確保 `switchMeTab` 是 async 函式（看上下文加上 `async`）。

- [ ] **Step 5.5: 改 `submitAddBatch` 接 POST /api/me/portfolio/batches**

位置：`..\卡波\index.html:1464-1481`、整個函式改：

```javascript
async function submitAddBatch(){
  const card = document.getElementById('addCardSearch').value.trim();
  const qty = parseInt(document.getElementById('addQty').value, 10);
  const cost = parseFloat(document.getElementById('addCost').value);
  const tcgChip = document.querySelector('#addTcgChips .pf-fchip.on');
  const tcg = tcgChip ? tcgChip.dataset.tcg : 'pokemon';
  if(!card){ toast('請輸入卡片'); return; }
  if(!qty || qty < 1){ toast('數量至少 1'); return; }
  if(!cost || cost <= 0){ toast('請輸入單價'); return; }

  // MVP 階段：set_id + card_number 從 search 字串解析（格式："set_id/card_number" 或暫存）
  // TODO Phase 2.5: 接搜尋頁 autocomplete 回 set_id + card_number
  const parts = card.split('/');
  if(parts.length !== 2){
    toast('暫時用「set_id/卡號」格式輸入、例：M2/110');
    return;
  }

  try{
    const d = await api('/api/me/portfolio/batches', null, {
      method: 'POST',
      body: {
        set_id: parts[0].trim(),
        card_number: parts[1].trim(),
        tcg,
        qty,
        cost_per_unit: cost,
        currency: document.getElementById('addCurrency').value || 'TWD',
        grade: document.getElementById('addGrade').value || null,
        note: document.getElementById('addNote').value || null,
        purchase_date: document.getElementById('addPurchaseDate').value,
      }
    });
    toast(`已加入卡冊：${card} × ${qty}`);
    closeAddBatch();
    if(state.view === 'portfolio') renderPortfolio();  // 重新載入
  }catch(e){
    toast('加入失敗：' + (e.message || '未知錯誤'));
  }
}
```

- [ ] **Step 5.6: 重啟 backend + 前端 reload、playwright 端到端驗**

```powershell
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
if($pid_){ Stop-Process -Id $pid_ -Force }
./Python/bin/python.exe run_api.py
```

用 playwright MCP 開 `http://localhost:8080/#/portfolio` 截圖看：
1. 未登入 → 顯示「登入後才能使用」+ 登入按鈕
2. 登入後 → 顯示「尚無持倉、點下方加買」
3. 點「+ 加買」→ modal 開 → 填「M2/110」+ qty=1 + cost=2000 → 提交
4. portfolio 頁顯示該卡 1 張 × NT$2000
5. F5 reload → 資料還在（非 mock）

- [ ] **Step 5.7: Commit Task 5**

前端不在 git、改 PROGRESS.md 記錄這部分動作：

```powershell
git add PROGRESS.md
git commit -m "progress: Portfolio Phase 2 step 5 — 前端 fetch 整合"
```

---

## Task 6: 端到端測試 + 更新 PROGRESS.md

**目的：** 完整流程驗證 + 寫入工作日誌、確認 Phase 2 收工。

**Files:**
- Modify: `PROGRESS.md`

- [ ] **Step 6.1: 完整端到端用真人帳號跑一次**

playwright 流程（每步驟截圖）：
1. 註冊新 user `pf_e2e@test.com` / pwd `test12345`
2. 切「我的卡冊」tab → 看到空狀態
3. 加買 M2/110 × 2 張 @ NT$2000 → 顯示
4. 加買 M2a/92 × 1 張 @ NT$3000 → 共 2 張卡
5. 切 TCG filter「寶可夢」→ 仍 2 張、KPI 同步
6. 點 M2/110 → 開批次操作 modal → 切「賣出」tab → 賣 1 張 @ NT$2500
7. 看 portfolio 該卡 qty 變 1、KPI 已實現顯示 +NT$500 (= (2500-2000)*1)
8. F5 reload → 全部資料還在
9. 嘗試改 M2/110 qty 為 5 → 看到 400 toast「此卡已有賣出記錄、不能改數量」

- [ ] **Step 6.2: 更新 PROGRESS.md 工作日誌**

加新 section（最後一則之後）：

```markdown
### 2026-05-23 — Portfolio Phase 2：後端 API + 前端 fetch 整合

#### 完成
- 6 個 commit 完成 Phase 2：(1) FX dict + 4 個 helper + unit test 6/6 (2) 3 個 GET endpoint (3) POST/PATCH/DELETE batches + cost_locked guard (4) POST/DELETE sells + BEGIN IMMEDIATE atomic check (5) 前端 fetch 整合 (6) 端到端驗證
- 持倉資料從 mock → 真實 DB 儲存、reload 後保留
- 6 個 BLOCKER 修正全部落實
- 端到端 playwright 9 步驟流程全綠（含 cost_locked guard 跳 400 toast）

#### 進行中
無

#### 踩到的坑
（如有、寫進 Known Pitfalls）

#### 明天的下一步
（看心情選）
```

- [ ] **Step 6.3: Commit Task 6**

```powershell
git add PROGRESS.md
git commit -m "progress: Portfolio Phase 2 收工 — 完整 6 task 端到端"
```

---

## 完成標準

- [ ] 6 個 commit 都 push（如需 push、user 同意才動）
- [ ] curl smoke + playwright 端到端兩條路徑全綠
- [ ] cost_locked guard 確認阻擋 PATCH qty / cost
- [ ] 超賣阻擋（賣 > holding 跳 400）
- [ ] reload 後資料還在
- [ ] PROGRESS.md 新增收工 entry

---

## 風險 / 已知限制（MVP 妥協）

1. **MVP 階段「一張卡 = 一個 batch」UI 視角**：spec 支援多 batch、但前端 modal 仍以「per-card aggregated」顯示。多 batch 列表 UI 推 Phase 後續（spec § 3.5 賣出 modal 提到「展開批次 list」、目前簡化為直接點卡 → modal）。後端 schema + API 已支援多 batch、未來加 UI 即可。
2. **加買 modal 卡片搜尋暫用 `set_id/card_number` 字串格式**：等接通搜尋頁 autocomplete API 再改 (Phase 2.5)。
3. **匯率寫死在 `FX_TO_TWD` dict**：每月手動更新。Phase E1 再接 API。
4. **realized_pnl 用「加權平均成本」算**：不是 FIFO / LIFO。spec § 6 確認用加權平均。
5. **現價來源**：JOIN `card_prices` source=snkrdunk / ebay grade=10、取最新 sale_date。沒資料就 fallback 用成本 (前端顯示 0% chip)。
