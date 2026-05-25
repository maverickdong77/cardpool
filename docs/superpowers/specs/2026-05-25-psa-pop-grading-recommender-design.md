---
date: 2026-05-25
status: approved-by-user
title: PSA POP（真實鑑定存世量）整合 + 「該不該送鑑定」推薦器
---

# PSA POP 整合 + 推薦器設計文件

## 1. 背景

寶可夢卡 PSA 鑑定價格查詢服務目前在卡片詳情頁顯示「PSA 拍賣分布」區塊、但**資料來源是「PSA APR 拍賣成交筆數」、不是 PSA 官方真實鑑定存世量**。前端 UI 也誠實標註「⚠️ 真 PSA Population 待整合」。

User 兩篇參考資料推升「升級為真 POP」+「加上『該不該送鑑定』推薦判斷器」的需求：
- 〈日版寶可夢卡牌鑑定指南〉PSA 10 對日版 SAR 溢價 38-75%、$75 法則、稀有度優先序、PSA Japan 4 級費用表
- 〈鑑定卡入門指南〉新手術語、市場現況、POP 概念解釋

## 2. 既有現狀（不要重做）

### 2.1 DB schema 已就位

`card_list` 表已有完整 POP 欄位（無需 ALTER）：
- `psa_pop10` / `psa_pop9` / `psa_pop8` / `psa_pop7` / `psa_pop6` / `psa_pop5`
- `psa_pop_total`
- `psa_gem_rate`（PSA 10 通過率）
- `psa_pop_updated_at`

### 2.2 既有資料填充 7.5%

47,432 卡裡 3,575 卡（7.5%）有資料、但**內容是「拍賣筆數」、不是真 POP**。範例：Magikarp #80 三重節拍 DB 顯示 `psa_pop10=6,043` 而真 POP 為 `53,912`、差 9 倍。

### 2.3 前端 UI 區塊已寫

`..\卡波\index.html:2940-2997` 已有「PSA 拍賣分布」5 cell 表格 + GEM 比例 footer + 「真 POP 待整合」disclaimer。

### 2.4 既有 PSA scraper 可重用

`app/scraper/psa_apr.py` 已有 `PSASession` 類別（過 Cloudflare 的 stealth playwright session）、爬 PSA `salesHistory` 端點正常運作。

### 2.5 spec_id 對映 6,889 筆

`psa_apr_card_mapping` 表已有 6,889 個 (set_id, card_number) → spec_id 對映、高稀有度熱門卡多數已涵蓋。

## 3. Spike 結果（2026-05-25）

確認 PSA 官方有公開 POP endpoint：

```
GET https://www.psacard.com/api/psa/researchJourney/spec/{spec_id}/PSA/populationSummary?filter=all
```

- 過 Cloudflare（reuse `PSASession`）後 **200 OK**
- 不需要 PSA 帳號登入
- 兩張卡測試成功：
  - Magikarp #80 三重節拍（spec=8422222）：總 59,120 / PSA 10 = 53,912 / GEM 91.19%
  - Eevee #78 朱紅迷霧（spec=10648023）：總 31,310 / PSA 10 = 26,998 / GEM 86.23%
- 回傳完整 JSON：authentic / grade1-grade10 / half grades (1.5/2.5/...) / total / gemRate

## 4. 設計決策摘要

| 項目 | 決策 |
|---|---|
| POP 資料來源 | PSA 官方 `populationSummary` API |
| 涵蓋範圍 | 高稀有度卡（SAR / UR / SR / MUR）優先、~7,000-9,000 張 |
| UI 風格 | Dashboard（圓圈通過率 + 試算表 + POP 5 cell） |
| 裸卡價來源 | `card_prices` source='snkrdunk' + psa_grade IS NULL 近 90 天平均 |
| PSA 10 預估賣價 | DB 既有 PSA 10 中位數（≥5 筆）、不足 fallback 打 PSA `salesHistory` |
| PSA 鑑定費 | user 下拉選 4 級、預設 Value ¥4,980 |
| 推薦門檻 | 通過率 + 淨利 雙標準 |
| 整體架構 | 背景 backfill + 30 天 lazy refresh + 前端算推薦 |
| Lazy refresh 限速 | 每張卡 24 小時內最多 2 次 |
| 幣別 | 預設新台幣（TWD）、可切 USD / JPY |
| 匯率 | reuse 既有 hardcode（JPY_TO_TWD=0.22、USD_TO_TWD=32.0）、約月更 |
| 免責文字 | 「※ 試算價格僅供參考、未計入平台手續費」 |
| 上線 | 5 階段、~3.5-4 天 |

## 5. 資料層設計

### 5.1 不動既有 schema

`card_list.psa_pop10..psa_pop_total..psa_gem_rate..psa_pop_updated_at` 全保留、語意改為「真 PSA POP」。

### 5.2 清掉舊資料

user 第 1 塊已確認 **不另存舊「拍賣筆數」資料**、直接 overwrite 既有欄位。Phase 1 第一步：
```sql
UPDATE card_list
SET psa_pop10 = NULL, psa_pop9 = NULL, psa_pop8 = NULL,
    psa_pop7 = NULL, psa_pop6 = NULL, psa_pop5 = NULL,
    psa_pop_total = NULL, psa_gem_rate = NULL,
    psa_pop_updated_at = NULL
WHERE psa_pop_total IS NOT NULL;  -- 影響 3,575 row
```

backup：`cards.db.before-psa-pop-backfill-YYYYMMDD-HHMMSS`

### 5.3 新增小表 `psa_pop_refresh_log`

用於 24h / 卡 / 2 次的限速。

```sql
CREATE TABLE IF NOT EXISTS psa_pop_refresh_log (
    set_id      TEXT NOT NULL,
    card_number TEXT NOT NULL,
    refreshed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_psa_pop_refresh_log_card
  ON psa_pop_refresh_log(set_id, card_number, refreshed_at);
```

同步寫進 `app/database.py:init_db()` 的 CREATE TABLE（CLAUDE.md「schema 雙寫一致性」Pitfall）。

### 5.4 backfill 腳本 `_backfill_psa_pop.py`（local-only、`.gitignore` 排除）

```python
# pseudo
sess = PSASession().open()  # 過 Cloudflare
for card in select_high_rarity_pending():
    spec = mapping.get(card) or sess.search_spec_ids(query)[0]
    if not spec:
        log_unmatched(card)
        continue
    try:
        pop = sess.get_population_summary(spec)  # 新 method
        update_card_pop(card, pop)
        commit()  # per-card commit、避免 lock 餓死 lazy refresh
    except CloudflareBlocked:
        retry_with_backoff(30, 120, 300)  # 3 次
    sleep(2)  # 2 秒/張
```

`select_high_rarity_pending()` SQL：
```sql
SELECT set_id, card_number, name, name_jp, rarity
FROM card_list
WHERE rarity IN ('SAR', 'UR', 'SR', 'MUR')
  AND psa_pop_updated_at IS NULL
ORDER BY card_number  -- 任意順序、僅為確定性
```

預估時間：~7,000-9,000 張 × 2 秒/張 ≈ 4-5 小時

### 5.5 Lazy refresh 觸發條件

詳情頁 endpoint 內：
```python
if row.psa_pop_updated_at:
    age_days = (now - row.psa_pop_updated_at).days
    if age_days < 30:
        return  # 不 refresh
# 30 天 + IS NULL 都走以下
recent_refresh_count = await db.fetch_val("""
    SELECT COUNT(*) FROM psa_pop_refresh_log
    WHERE set_id=? AND card_number=? AND refreshed_at > datetime('now', '-24 hours')
""", set_id, card_number)
if recent_refresh_count >= 2:
    return  # 24h 撞兩次、silent skip
# 非阻塞觸發
asyncio.create_task(do_lazy_refresh(set_id, card_number))
```

## 6. 後端 API 設計

### 6.1 改既有 endpoint：`GET /api/cards/{set_id}/{card_number}`

位置：`app/main.py` `get_card_detail()`（行號約 1990-2200）。

改 3 處：

**6.1.1 SQL 加 subquery 計算裸卡 + PSA 10 中位數**

```sql
SELECT cl.*,
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

FROM card_list cl
WHERE cl.set_id=? AND cl.card_number=?
```

**6.1.2 response dict 新增欄位**

```python
{
  # ... 既有欄位 ...
  "psa_pop": {
    "grade10": row["psa_pop10"],
    "grade9": row["psa_pop9"],
    "grade8": row["psa_pop8"],
    "grade7": row["psa_pop7"],
    "grade6": row["psa_pop6"],
    "grade5_and_below": (row["psa_pop5"] or 0),  # 可選聚合
    "total": row["psa_pop_total"],
    "gem_rate": row["psa_gem_rate"],
    "updated_at": row["psa_pop_updated_at"],
    "source": "psa_official",
  },
  "snkr_raw_avg_jpy": row["snkr_raw_avg_jpy"],
  "psa10_market_jpy": row["psa10_market_jpy"],
  "psa10_market_source": (
    f"db_psa10_90d_n{row['psa10_market_n']}"
    if row['psa10_market_n'] >= 5
    else "psa_official_fallback"  # 觸發背景 fetch、本次 psa10_market_jpy 仍可能為 None
  ),
}
```

PSA 10 中位數樣本不足（< 5 筆）時：
- response 內 `psa10_market_jpy` 為 NULL（或可用 DB 內所有 PSA 10 平均、若 ≥ 1 筆）
- 同時 `asyncio.create_task(background_fetch_psa_sales_history(set_id, card_number))` 觸發背景拉 PSA 官方 `salesHistory` 30 筆寫入 `card_prices`、下次 user 看到就有完整中位數

**6.1.3 結尾觸發 lazy refresh**

```python
asyncio.create_task(maybe_lazy_refresh_pop(set_id, card_number, row))
```

### 6.2 新增 endpoint：`POST /api/psa/pop/refresh/{set_id}/{card_number}`

給「手動更新」按鈕。同樣套 24h 兩次限制。

回傳：
```json
{ "ok": true, "updated_at": "2026-05-25T13:00:00" }
// 或
{ "ok": false, "reason": "rate_limited", "next_available_in_hours": 12 }
```

## 7. 前端 UI 設計

### 7.1 取代既有「PSA 拍賣分布」區塊

`..\卡波\index.html:2940-2997` 整段「PSA 拍賣分布」區塊刪除、替換為新 Dashboard。

### 7.2 Dashboard 元件結構（5 塊）

```
+--------------------------------------------------+
| [⚠] 邊緣案例                                     |   ← 推薦結論 header
| 理由：通過率高、但淨利為負      貨幣: [TWD ▼]    |
+--------------------------------------------------+
| ⭕ 91%      PSA 10 通過率 91.19%                |   ← 圓圈通過率
|           日版剛開包平均 60-80%、這張高於平均    |
+--------------------------------------------------+
| 裸卡現價             NT$ 770                     |   ← 試算表
| PSA 鑑定費 [Value ▼] −NT$ 1,096                  |
| 運費                 −NT$ 176                    |
| PSA 10 預估賣價       NT$ 1,760                  |
| 預估淨利              −NT$ 282  ⚠                |
+--------------------------------------------------+
| [PSA 10] [PSA 9] [PSA 8] [PSA 7] [≤PSA 6]       |   ← POP 5 cell bar
| 53,912   3,794    680     283     442            |
+--------------------------------------------------+
| ※ 試算價格僅供參考、未計入平台手續費             |   ← disclaimer
| （SNKR 5.5% / eBay 12%）                         |
+--------------------------------------------------+
```

### 7.3 推薦判斷公式（前端 JS）

```javascript
function calcRecommendation(psa10Market, snkrRaw, psaFee, gemRate) {
  const shipping = 800;  // JPY 固定
  const netProfit = psa10Market - snkrRaw - psaFee - shipping;

  if (gemRate == null || snkrRaw == null || psa10Market == null) {
    return { status: 'insufficient', netProfit: null };
  }
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
```

### 7.4 鑑定費下拉

```html
<select id="psaTier" onchange="recalcDashboard()">
  <option value="3980">Value Bulk ¥3,980（90 工作日）</option>
  <option value="4980" selected>Value ¥4,980（75 工作日）</option>
  <option value="6980">Value Plus ¥6,980（45 工作日）</option>
  <option value="9980">Regular ¥9,980（25 工作日）</option>
</select>
```

`localStorage.setItem('psaTier', value)` 記住。

### 7.5 幣別下拉

```html
<select id="psaCurrency" onchange="recalcDashboard()">
  <option value="TWD" selected>NT$（新台幣）</option>
  <option value="USD">$（美元）</option>
  <option value="JPY">¥（日圓）</option>
</select>
```

匯率 hardcode（與既有 scraper 一致、派生自 USD_TO_TWD=32.0、JPY_TO_TWD=0.22）：
```javascript
const FX_JPY_TO = {
  TWD: 0.22,                  // ¥1 = NT$0.22
  USD: 0.22 / 32.0,           // ¥1 ≈ $0.006875（派生、避免兩處常數對不齊）
  JPY: 1,
};
const FX_SYMBOL = { TWD: 'NT$', USD: '$', JPY: '¥' };
// 用法：displayPrice(jpyAmount, currency) = jpyAmount * FX_JPY_TO[currency]
```

`localStorage.setItem('psaCurrency', value)` 記住。

### 7.6 資料缺失降級

| 缺什麼 | 顯示 |
|---|---|
| POP 全 NULL | Dashboard 隱藏、改顯「資料準備中、預計 5 分鐘後可用」+ trigger backfill task |
| 裸卡 SNKR 平均拿不到 | 試算表第 1 行顯「資料不足」、推薦器顯「請等更多 SNKR 成交資料」 |
| PSA 10 預估賣價拿不到 | 同上 |
| GEM rate 為 null（但有 POP） | JS 自己算 `grade10 / total * 100` |

### 7.7 過老資料提示

`psa_pop_updated_at > 30 天` → POP 區塊右上角灰字「資料 N 天前 · 點此更新」、點 → 打 `/api/psa/pop/refresh/...` endpoint。

## 8. 錯誤處理

| 場景 | 處理 |
|---|---|
| PSA Cloudflare 暫擋 | backfill 3 次 retry（30s / 2min / 5min）、仍失敗 skip + log + `psa_pop_updated_at` 不變 |
| PSA search 找不到 spec_id | skip 該卡、寫入 `_psa_pop_unmatched.txt`、user 看到「資料準備中」 |
| backfill 跑一半 process 死 | gating `psa_pop_updated_at IS NULL` 自動續跑 |
| PSA 偵測 rate limit | backfill 自動降速 2s → 4s/卡、再撞就停 20 分鐘冷卻 |
| Lazy refresh 撞 24h 兩次 | silent skip |
| 前端淨利除以 0 | 試算表顯「資料不足」、推薦停用 |
| `cards.db-wal` 鎖太久 | per-card commit、避免長 tx 鎖死 |

## 9. 上線順序（5 階段、~3.5-4 天）

### Phase 1 — 資料層（~1 天）

1. backup `cards.db.before-psa-pop-backfill-YYYYMMDD-HHMMSS`
2. 清掉舊「拍賣筆數」3,575 卡資料（UPDATE SET NULL）
3. 建 `psa_pop_refresh_log` 表（含寫進 `app/database.py:init_db()`）
4. 寫 `_backfill_psa_pop.py`、reuse `psa_apr.py:PSASession` + 新 method `get_population_summary(spec_id)`
5. 跑高稀有度卡 backfill（~4-5 小時、可中斷續跑）
6. spot-check 10 張卡 PSA 數字對得起來

### Phase 2 — 後端 API（~半天）

1. 改 `get_card_detail` SQL JOIN + response 新欄位
2. 加 `maybe_lazy_refresh_pop` 非阻塞觸發
3. 加 `POST /api/psa/pop/refresh/{set_id}/{card_number}` endpoint
4. 重啟 API（HTA mode 注意 kill PID）
5. spot-check 5 個 set 詳情頁 API 回傳

### Phase 3 — 前端 Dashboard（~1 天）

1. mockup B 結構轉成 `..\卡波\index.html` 內 cardDetail render
2. 加 CSS（`.rec-dashboard` / `.circle` / `.calc-table` / `.pop-bar`）
3. 鑑定費下拉 + 幣別下拉 + JS 互動 + localStorage
4. 推薦公式 implementation
5. 資料缺失降級
6. spot-check 10 張卡實機效果（user reload）

### Phase 4 — lazy refresh polish（~半天）

1. 「資料 N 天前」過老提示
2. 確認 24h 兩次限制實際運作（用 console.log + DB log 驗）
3. 手動更新按鈕測試

### Phase 5 — 驗收 + 拆 commit（~半天）

1. user hard reload 看實機（mockup B 變 production）
2. 抓 bug
3. 拆 commit（資料層 / 後端 / 前端 / docs 分 commit）
4. 更新 PROGRESS.md 工作日誌

## 10. 失敗回滾

- **DB**：backup 復原（cards.db.before-* 檔案）
- **後端 code**：git revert + 重啟 API
- **前端 code**：git revert（`..\卡波\index.html`）

## 11. 不做（YAGNI）

- ❌ user 自選 set 全量 sync PSA POP（只跑高稀有度、其他靠 lazy）
- ❌ PSA 9 / PSA 8 的推薦判斷（只判 PSA 10）
- ❌ 美國 PSA 費用切換（只用 PSA Japan ¥）
- ❌ USD 即時匯率 API（reuse 既有 hardcode）
- ❌ SNKR / eBay 平台手續費內化（前面決定不算、寫進 disclaimer）

## 12. 參考資料

- 〈日版寶可夢卡牌鑑定指南〉samuraiswordtokyo.com/zh-hant/blogs/news/japanese-pokemon-card-grading-guide
- 〈鑑定卡入門指南〉pikawu.com/learn/beginner
- spike 結果：cards.db `psa_apr_card_mapping` Magikarp #80 / Eevee #78 兩張卡 POP 實際拿到 200 OK
- 既有 scraper：`app/scraper/psa_apr.py` `PSASession` 類別
- 既有 endpoint：`app/main.py:get_card_detail` 約 1990-2200 行
- 既有前端 UI 區塊：`..\卡波\index.html:2940-2997`
