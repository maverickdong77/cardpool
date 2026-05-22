# 我的卡冊（Portfolio）功能設計文件

> **Status**: Design approved by user 2026-05-22 — ready for implementation plan
> **Brainstorm session**: `.superpowers/brainstorm/82154-1779444871/` (15 個 mockup 迭代)
> **Related plan**: 沿用 5/19 GoldenGem Phase B plan `C:\Users\Dong Ying\.claude\plans\gentle-inventing-ripple.md`

---

## 1. 功能目標

讓使用者**記錄自己買的卡** + **追蹤現在漲跌**。完整持倉系統 = 多批次 / 加權平均 / 賣出 / 已實現損益 / 多幣別 / 多 TCG 分類。

**為什麼做這個**：MVP 第一個「主動價值」功能 — 不只是查價、還能幫使用者管理收藏資產。對齊 GoldenGem.cc 同類功能。

---

## 2. 範圍邊界

### In scope（這次做）

- 持倉登記（買進批次 / 數量 / 成本 / 幣別 / 鑑定等級 / 備註 / 購買日）
- 賣出登記（從某批次扣量、計算已實現損益）
- KPI 卡片（總成本 / 總市值 / 未實現損益 / 已實現損益）
- 「我的帳戶」加 tab「我的卡冊」顯示概要（最近 5-10 張）+ 「查看完整卡冊」連結
- 完整持倉頁 `#/portfolio` 獨立路由
- TCG 分類 label filter（精靈球 / 草帽 / Ankh / 王冠 + 灰掉的「+ 自訂」）
- 多幣別輸入（TWD / USD / JPY / KRW / HKD）+ 購買當下匯率 snapshot

### Out of scope（這次不做、未來 Phase E）

- 分享持倉（去隱私化版本）
- 導出 CSV
- 販售模式（從持倉直接上架）
- 暗色主題
- 自訂分類（user 自己加「我的鑑定卡」「待整理」等 tag）
- 海賊王 / 遊戲王 / 魔法風雲會的**實際資料**（DB 目前只有寶可夢卡）— label filter UI 留位置、灰掉「即將推出」、等未來爬其他 TCG 資料後自動可點

---

## 3. UI 設計

### 3.1 持倉頁 `#/portfolio` Layout（B3 風格）

```
┌────────────────────────────────────────────┐
│ [全部 (5)] [⚪ 寶可夢 (5)] [👒 海賊王 灰]    │  ← Label filter chip 列
│ [☥ 遊戲王 灰] [♚ 魔法風雲會 灰] [+ 自訂 灰] │
├────────────────────────────────────────────┤
│  ┌──┐ ┌──┐ ┌──┐ │ ┌──────────┐           │
│  │卡│ │卡│ │卡│ │ │ 目前：全部 │           │
│  │圖│ │圖│ │圖│ │ │           │           │
│  │+28%│-5%│+24│ │ │ 總成本    │           │
│  │皮卡│超夢│噴火│ │ │ $45,200  │           │
│  │1張+│1張-│2張+│ │ │ 總市值    │           │
│  │$900│$150│$2.6│ │ │ $58,100  │           │
│  └──┘ └──┘ └──┘ │ │ 未實現    │           │
│  ┌──┐ ┌──┐ ┌──┐ │ │ +$12,900  │           │
│  │卡│ │卡│ │+加│ │ │ +28.5%    │           │
│  │圖│ │圖│ │ 買│ │ │───────    │           │
│  │+15│0% │   │ │ │ 已實現    │           │
│  │妙蛙│傑尼│   │ │ │ +$3,200   │           │
│  └──┘ └──┘ └──┘ │ └──────────┘           │
└────────────────────────────────────────────┘
```

**主區（左、2/3 寬）**：每張卡為一個 grid item
- 右上角百分比 chip：賺綠 / 賠紅 / 平盤灰
- 卡圖縮圖
- 卡名（粗體）
- 副標：「N 張 · `<金額>`」、金額部分用紅綠色（賺綠 / 賠紅 / 平盤灰）

**KPI sidebar（右、1/3 寬）**：
- 上方小標籤「目前：全部」(切 filter 會跟著改、如「目前：寶可夢」)
- 總成本 / 總市值 / 未實現損益 + % / 已實現損益（最後分隔線後）

**「+ 加買」**：grid 最後一格、虛線框、點開 加買 modal

### 3.2 4 個 TCG 圖示（SVG inline）

| TCG | 圖示 | 設計 |
|---|---|---|
| 寶可夢 | 精靈球 | 紅色上半 + 黑橫條中線 + 淡灰 `#f5f5f5` 下半 + 中央淡灰按鈕黑邊。**結構性中線跟按鈕邊保留**、外圈無邊 |
| 海賊王 | 草帽 | 黃帽簷 + 黃帽頂 + 紅帽帶 + 淡黃高光、無描邊 |
| 遊戲王 | Ankh（古埃及生命之鑰） | 金色漸層實心（`#fde047` → `#ca8a04`）、頂部圓環粗 stroke、橫桿 + 直桿圓角 |
| 魔法風雲會 | 王冠 | 金色基座 + 3 個尖點 + 3 顆寶石（紅藍綠在尖點頂端）+ 中央紫色大寶石（白高光）、無描邊 |

**SVG 完整 source code 詳見** brainstorm session `.superpowers/brainstorm/82154-1779444871/content/label-icons-final.html`

### 3.3 「我的帳戶」概要 tab

```
我的收藏 | 我的掛單 | 我的出價 | 我的成交 | 我的卡冊 ★ | 訊息
─────────────────────────────────────────────────────────
[全部 (5)] [⚪ 寶可夢 (5)] [👒 海賊王 灰] ...        ← 同 portfolio 頁的 filter
總成本 $45,200    總市值 $58,100   未實現 +$12,900  ← 縮版橫排 KPI

最近加入的 5 張：
┌──┐ ┌──┐ ┌──┐ ┌──┐ ┌──┐
│卡│ │卡│ │卡│ │卡│ │卡│
└──┘ └──┘ └──┘ └──┘ └──┘

[查看完整卡冊 →] 跳 #/portfolio
```

「我的帳戶」tab 是縮版：KPI 橫排（不豎排）、只顯示最近 5 張卡、底部按鈕跳完整頁。

### 3.4 加買 modal

點「+ 加買」開 modal、欄位：
- 搜尋卡（既有 search bar 重用 / autocomplete）
- 數量
- 單價成本
- 幣別（5 選 1，預設 TWD）
- 鑑定等級（PSA10 / PSA9 / .. / 未鑑定）
- 購買日期（date picker、預設今天）
- 備註（選填、textarea 100 字）
- TCG 分類（auto-detect 從卡的 set_id 推、預設 pokemon、未來 user 可改）

### 3.5 賣出 modal

從持倉卡片內展開 → 顯示已有批次 list → 對某批次點「賣出」→ 開 modal：
- 賣出數量（不能超過該批次剩餘量、後端 atomic check）
- 賣出單價
- 幣別
- 賣出日期

---

## 4. 資料模型（DB Schema）

沿用 5/19 Phase B plan、加 `tcg` 欄位支援多 TCG 分類。

### `portfolio_batches`（買進批次）

```sql
CREATE TABLE IF NOT EXISTS portfolio_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    tcg TEXT NOT NULL DEFAULT 'pokemon',  -- 新加：pokemon / onepiece / yugioh / mtg
    set_id TEXT NOT NULL,
    card_number TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK(qty > 0),
    cost_per_unit REAL NOT NULL,            -- 原幣別單價
    currency TEXT NOT NULL DEFAULT 'TWD',   -- TWD / USD / JPY / KRW / HKD
    fx_rate_to_twd REAL NOT NULL,           -- 購買當下匯率 snapshot（防匯率 drift）
    cost_per_unit_twd REAL NOT NULL,        -- cost_per_unit × fx_rate_to_twd（避免反覆運算）
    grade TEXT,                              -- PSA10 / PSA9 / ... / unrated
    note TEXT,
    purchase_date TEXT NOT NULL,
    cost_locked INTEGER NOT NULL DEFAULT 0, -- 已有 sell 後鎖、防 edit 改壞歷史損益
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_pf_batches_user ON portfolio_batches(user_id, set_id, card_number);
CREATE INDEX idx_pf_batches_tcg ON portfolio_batches(user_id, tcg);
```

### `portfolio_sells`（賣出記錄）

```sql
CREATE TABLE IF NOT EXISTS portfolio_sells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    batch_id INTEGER,                        -- 從哪個批次扣（刪批次後 SET NULL、不影響已實現損益）
    set_id TEXT NOT NULL,                    -- 冗餘儲存（防 batch 刪後仍可查）
    card_number TEXT NOT NULL,
    tcg TEXT NOT NULL DEFAULT 'pokemon',     -- 冗餘儲存
    qty INTEGER NOT NULL CHECK(qty > 0),
    sell_price_per_unit REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'TWD',
    fx_rate_to_twd REAL NOT NULL,            -- 賣出當下匯率 snapshot
    sell_price_per_unit_twd REAL NOT NULL,
    realized_pnl_twd REAL NOT NULL,          -- (sell_price - 該批次 cost_per_unit) × qty（TWD）
    sell_date TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (batch_id) REFERENCES portfolio_batches(id) ON DELETE SET NULL
);
CREATE INDEX idx_pf_sells_user ON portfolio_sells(user_id);
CREATE INDEX idx_pf_sells_card ON portfolio_sells(user_id, set_id, card_number);
```

### 寫進 `app/database.py` init_db()

**雙寫一致性**：schema 必須同時寫進 `init_db()`（避免新環境 init 缺表）。

---

## 5. API Endpoints

`app/main.py` 新加 section `# ==================== Portfolio 持倉 ====================`：

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/me/portfolio?tcg=all\|pokemon\|...` | 列出持倉卡片（含現價 JOIN、未實現損益、按 tcg filter） |
| GET | `/api/me/portfolio/summary?tcg=all\|pokemon\|...` | KPI 卡片資料（總成本 / 總市值 / 未實現 / 已實現） |
| GET | `/api/me/portfolio/recent?limit=5` | 「我的帳戶」概要 tab 用、最近 N 張 |
| POST | `/api/me/portfolio/batches` | 新增批次 |
| PATCH | `/api/me/portfolio/batches/{id}` | 編輯批次（含 cost_locked guard：有 sells 後拒改 cost / qty） |
| DELETE | `/api/me/portfolio/batches/{id}` | 刪批次（先檢查 sells 用此 batch_id 的話、刪批次後 sells 的 batch_id SET NULL、保留歷史損益） |
| POST | `/api/me/portfolio/sells` | 賣出（atomic check：BEGIN IMMEDIATE + qty 不超剩餘） |
| DELETE | `/api/me/portfolio/sells/{id}` | 撤回賣出（已實現變回未實現） |

### 6 個 BLOCKER 修正（沿用 5/19 plan）

1. **FX rate snapshot** 防匯率 drift（2024 ¥30000 @ 0.22 不被今天 0.20 改寫）
2. **cost_locked** 欄位 + PATCH guard 防 edit-batch-after-sell 改壞歷史損益
3. **持倉數量公式**改用 `(set_id, card_number)` group、不依賴 batch_id（刪批次後仍正確）
4. **賣出 qty atomic check**（BEGIN IMMEDIATE）防併發超賣
5. **Watchlist 5 張限額**改 atomic INSERT WHERE 子句防 TOCTOU（這個是 Phase A 範圍、portfolio 不受影響）
6. **分享持倉**只 expose qty + 現價 + 漲跌%、絕不 expose 成本 / 損益（這個是 Phase E 範圍、本次不做）

---

## 6. 計算邏輯

```python
# 每張卡持倉數量 = 該卡所有 batches 數量總和 - 該卡所有 sells 數量總和
holding_qty = SUM(batches.qty WHERE same (set_id, card_number) AND user_id)
            - SUM(sells.qty WHERE same (set_id, card_number) AND user_id)

# 每張卡均價 = 持倉中 batches 的加權平均（已賣出的批次不算）
# 注意：FIFO / LIFO / 加權平均 三選一 → MVP 用加權平均（簡單）
avg_cost_twd = SUM(batches.cost_per_unit_twd * batches.qty) / SUM(batches.qty)
             FOR all batches with same (set_id, card_number) AND user_id

# 未實現損益 = (現價 - 均價) × 持倉數量
unrealized_twd = (current_price_twd - avg_cost_twd) × holding_qty

# 已實現損益 = 所有 sells 的 realized_pnl_twd 加總
realized_twd = SUM(sells.realized_pnl_twd) FOR user

# 現價來源：JOIN card_prices 取該卡 source = 'snkrdunk' / 'ebay' 最新 grade=10 價格
# （沿用既有 latest-prices 邏輯、不另做）
```

---

## 7. 匯率設計

- 5 個幣別：TWD / USD / JPY / KRW / HKD
- 匯率寫入「購買 / 賣出當下」snapshot 進 `fx_rate_to_twd` 欄位（防匯率波動改寫歷史損益）
- 匯率來源：MVP **硬編在 server 端 dict**（每月手動更新）。未來再評估接 API（exchangerate-api.com 免費 1500 req/mo）

```python
FX_TO_TWD = {  # 寫進 app/main.py 或 app/fx.py
    "TWD": 1.0,
    "USD": 32.0,
    "JPY": 0.20,
    "KRW": 0.024,
    "HKD": 4.1,
}
# 未來替換為 API 取回；endpoint 用當下值寫進 snapshot 欄位
```

---

## 8. 「我的帳戶」概要 tab vs 完整 `#/portfolio` 頁

| | 我的帳戶 → 我的卡冊（縮版）| `#/portfolio` 完整頁 |
|---|---|---|
| 入口 | `/me` 頁 tab 切換 | 路由 `#/portfolio` |
| KPI 顯示 | 橫排 3 個 | 直排 sidebar 4 個 + 已實現 |
| 卡片列表 | 最近 5 張 grid | 全部、可 scroll |
| Label filter | 有（但縮排） | 有（完整）|
| 加買 modal | 無、引導到完整頁 | 有 |
| 賣出 modal | 無 | 有 |
| 「查看完整 →」 | 有按鈕跳 #/portfolio | 無 |

---

## 9. 開放議題（暫不擋住實作）

- **匯率每月更新機制**：MVP 階段 hardcode、需 user 每月手動改 dict。未來轉 API 是 Phase E1。
- **TCG 自動偵測**：set_id 開頭 `jp-*` / `en-*` 都是寶可夢、自動填 `tcg='pokemon'`。未來爬其他 TCG 進 DB 後需要對映表。
- **海賊王 / 遊戲王 / 魔法風雲會 實際資料**：DB 目前無、label chip 灰掉、user 加買時不會出現。等其他 TCG 卡表灌進 DB 再開放（另開獨立爬蟲工程）。

---

## 10. 視覺風格

- **顏色**：賺綠 `#10b981` / 賠紅 `#dc2626` / 平盤灰 `#6b7280`
- **chip 風格**：白底 + `#d4d4d4` 1.5px 邊 + 圓角 18px + 6-8px gap
- **百分比 chip**：右上角 `border-radius:10px` 小 chip、賺綠 `#10b981` 底 + 白字、賠紅 `#dc2626` 底 + 白字
- **副標金額**：賺綠 / 賠紅、`font-weight:700`、跟前面「N 張」灰字（`#888`）區隔

---

## 11. 預估實作工時

| 段 | 內容 | 工時 |
|---|---|---|
| Phase 1 | DB schema + init_db | 30 min |
| Phase 2 | 8 個 API endpoint + 計算 logic + 匯率 dict | 2-3 hr |
| Phase 3 | `#/portfolio` 完整頁 UI（B3 layout + label filter + KPI sidebar + 4 個 SVG 圖示） | 2-3 hr |
| Phase 4 | 加買 modal + 賣出 modal | 1-1.5 hr |
| Phase 5 | 「我的帳戶」加 tab + 縮版概要 | 30 min |
| Phase 6 | 端到端測試（curl + 瀏覽器手動）| 30 min |
| **總計** | | **6-8 hr** |
