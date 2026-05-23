# JP 新卡盒自動補資料系統(設計文件 spec)

> 日期:2026-05-24
> 作者:Dong Ying / Claude
> 狀態:Brainstorm 通過、等寫實作計畫(plan)

---

## 一、為什麼要做這個

### 現況問題

2026-05-23 加了「首頁今日熱門 = SNKR 熱門排行」功能後、發現一個體驗問題:

- SNKR 熱門排行 top 10 有 8 張是 **M5「アビスアイ」**(中文「深淵之眼」)單卡、這是 SNKR 上最新最熱的擴充包
- 但**這個卡盒還沒進我們 DB**(jp_card_list 沒收)、所以 user 點下去**只能跳 SNKR 商品頁、看不到本站的價格走勢 / 收藏功能**
- 試跑後實際 mapping 率 7/30(全 30 張中只有 7 張能對到本站詳情頁)、體驗很差

### 目標

寫一套**可重用、可排程、可手動觸發的「補卡盒進 DB」系統**、滿足:

- **使用情境 1**:補一個全新卡盒(例:M5 アビスアイ、從零開始)
- **使用情境 2**:補既有卡盒的漏卡(例:M4 #84-114 SAR/UR/MUR 變體)
- **使用情境 3**:未來 EN / TW 卡表也能用同一套系統(可重用)

### 對齊的 user 偏好

- ✅ 全套規則化、抽成獨立可重用的程式檔(術語:reusable module)
- ✅ 每日清晨自動跑、不用 user 手動觸發
- ✅ 補完後接著補價格(SNKR + eBay)、user 第二天進詳情頁就能看走勢
- ✅ 保守限流(一天最多 2 個卡盒、卡之間隔 2 秒)、不撞外部網站防爬規則

---

## 二、整體架構(Section 1 of brainstorm)

### 系統元件

```
+-----------------------------------------------------+
| 1. app/scraper/jp_set_backfill.py(主模組)         |
|    └─ scrape_set(set_code) → 完整補一個卡盒        |
|    └─ scrape_missing_cards(set_code) → 補漏卡      |
+-----------------------------------------------------+
              ↑               ↑               ↑
+------------+  +-----------+  +----------------------+
| 排程       |  | admin     |  | SNKR 熱門爬蟲偵測     |
| 每日 03:00 |  | endpoint  |  | (snkr_hot.refresh)   |
+------------+  +-----------+  +----------------------+
```

### 三層工作分離

| 層 | 角色 | 寫在哪 |
|---|---|---|
| **偵測** | SNKR 熱門爬完、發現新 set_code → 加進排隊表 | `_refresh_snkr_hot_items` 內補一段 detect 邏輯 |
| **執行** | 從排隊表取 pending、實際爬卡盒、寫進 DB | `app/scraper/jp_set_backfill.py`(新檔) |
| **通知** | 寫結果進 `set_backfill_jobs.error_msg`、PROGRESS.md | 同上、附 print 進 `_run_api.log` |

### 為什麼這樣切?

三個角色解耦、改一個不影響另兩個:
- 未來想換偵測來源(改用 pokemon-card.com 官方公告當訊號)、不用動補卡盒邏輯
- 未來想加 web 後台、只要 polling `set_backfill_jobs` 表、不用改爬蟲

---

## 三、資料模型(Section 2 of brainstorm)

### 新表:`set_backfill_jobs`(補卡盒任務排隊表)

```sql
CREATE TABLE set_backfill_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    set_code TEXT NOT NULL,
    source_hint TEXT,
    -- 'snkr_hot_detect' / 'manual_admin' / 'm4_漏卡補抓' 等、追溯用
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending / running / done / failed
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    cards_scraped INTEGER DEFAULT 0,
    cards_translated INTEGER DEFAULT 0,
    error_msg TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sbj_status ON set_backfill_jobs(status, created_at);
CREATE INDEX idx_sbj_set_code ON set_backfill_jobs(set_code);
```

**去重邏輯放在 application 層、不靠 UNIQUE 約束**:

INSERT 新任務前先跑這條 SQL 查 pending:

```sql
SELECT id FROM set_backfill_jobs
 WHERE set_code = ? AND status IN ('pending', 'running')
 LIMIT 1;
```

有結果就跳過、沒結果才 INSERT。理由:`done` / `failed` 狀態可以多筆(歷史紀錄)、不適合 UNIQUE。

### 既有表的變動

| 表 | 變動類型 | 細節 |
|---|---|---|
| `jp_card_list_set`(368 筆 JP 卡盒主表) | **INSERT** 新 pg | 自動分配新 pg(規則見下)、`name_jp`「日文 (中文)」格式、`logo_url` 從 pokemon-card.com 抓、`hit_cnt` 補完後填卡片數 |
| `jp_card_list`(21,552 筆 JP 卡主表) | **INSERT** 新卡(M5 預期 80-120 筆) | 每張卡 `pg` / `set_code` / `card_number` / `name_jp` / `rarity` / `image_id` / `source='pokemon-card.com'` |
| `pokemon_dict`(1,025 條寶可夢字典) | 可能 **INSERT** 0-N 筆 | M5 出現從沒在卡牌出過的寶可夢時補(極少數) |
| `jp_term_dict`(1,495 條訓練家/道具字典) | 可能 **INSERT** N 筆 | M5 新訓練家 / 道具 / 能量、從 52poke wiki 抓中譯後補 |
| `snkrdunk_mapping`(232k SNKR 商品對映) | **不動** | 既有 SNKR sync 用 set_name_jp + card_number lookup、不需要先建 mapping |

#### 新 pg 分配規則

```python
def allocate_new_pg(set_code: str) -> int:
    # promo set(SV-P / M-P 等以 -P 結尾)走 9000+ 區段
    if set_code.endswith('-P') or set_code.endswith('-PROMO'):
        # 既有 promo:9001/9002/9003
        # 新 promo:從 9100 起跳(避免跟 9001-9099 既有混)
        return max(9100, current_max_in_9000_range() + 1)
    # 普通擴充包:走 1-999 區段
    # 既有最大是 953(M4)、新 M5 = 954
    return current_max_in_1_999_range() + 1
```

**注意**:既有有些奇怪 pg(如 950 = M2a / 951 = MC / 850 = SI 等)、不是嚴格按時間排、分配時跳過已用即可。

### 資料流動(完整週期、以補 M5 為例)

```
Day 0(2026-05-24)
├─ 18:00 user 訪問首頁 → SNKR 熱門爬一次
│  └─ 偵測:set_code=M5 不在 jp_card_list
│     └─ INSERT set_backfill_jobs (set_code='M5', status='pending',
│                                  source_hint='snkr_hot_detect')
│
Day 1 凌晨 03:00 排程啟動
│
├─ Step 1:從 set_backfill_jobs 取 status='pending'、最多 2 個
│  └─ status: pending → running、started_at 寫入
│
├─ Step 2:爬 pokemon-card.com「アビスアイ」搜尋頁
│  └─ 拿到 set 頁面 URL + 卡片列表 URL
│
├─ Step 3:解析卡片列表
│  └─ 拿到每張卡 cardID + name_jp + card_number + rarity + image_id
│  └─ 一張卡之間 sleep 2 秒
│
├─ Step 4:寫進 jp_card_list_set(新 pg)+ jp_card_list(N 張卡)
│  └─ 每張卡寫完就 commit(可中斷續跑)
│  └─ cards_scraped 計數更新
│
├─ Step 5:抓卡圖 — 優先 artofpkm.com、沒有 fallback pokemon-card.com
│  └─ image_url 寫進 jp_card_list
│
├─ Step 6:翻譯
│  └─ 第 1 層:既有 _translate_jp_card_name_to_zh 管線
│  └─ 第 2 層:命中失敗 → 爬 52poke wiki 該卡盒繁中版頁 → 補 jp_term_dict
│  └─ 第 3 層:仍找不到 → name_zh=NULL、寫進 _miss_translate.html 給 user 手動補
│  └─ cards_translated 計數更新
│
├─ Step 7:觸發 SNKR + eBay 價格補抓
│  └─ 用既有 _backfill_all_jp_snkr / _backfill_all_jp_ebay
│     對新 pg 的卡跑(prices_synced_at IS NULL gating 自動 pick up)
│
└─ Step 8:status: running → done、finished_at 寫入
   └─ 下一個 pending 重複 Step 2-7

Day 1 18:00 之後 user 訪問首頁 → SNKR 熱門爬一次(24h cache 過期、Day 0 18:00 爬到此時剛好滿 24h)
└─ 這次 set_code=M5 已在 DB → set_id 回填 954 → 前端 M5 卡片能跳本站詳情
```

### 補漏卡(M4 #84-114)流程

跟新卡盒一樣、差別在 Step 4 是 **INSERT OR IGNORE**、原 #1-83 已存在跳過、只寫新編號。`source_hint='m4_漏卡補抓'`。

---

## 四、錯誤處理 + 翻譯漏字補抓(Section 3 of brainstorm)

### 錯誤情況跟對策

| 情況 | 對策 |
|---|---|
| **被網站擋(403/500/timeout)** | 同一張卡最多重試 3 次、間隔 2 秒→10 秒→30 秒。3 次失敗該張跳過。連續 5 張失敗 → 整任務 `failed` |
| **卡盒在來源網站不存在** | 第一步搜尋頁回空 → 直接 `failed`、`error_msg='來源網站找不到該卡盒'` |
| **跑到一半 API 掛掉** | 每張卡寫完就 commit。重啟後撈 `status='running' AND started_at > 30 分鐘前` 的、判定死掉、status 改回 `pending` 重新排 |
| **翻譯命中率不夠** | 三層 fallback(見下) |
| **同 set_code 對多 pg** | LIMIT 1 取最常見的、不停下流程 |

### 翻譯來源優先順序

| 順序 | 來源 | 抓什麼 | 命中後 |
|---|---|---|---|
| 1 | `pokemon_dict`(1,025 條) | 直接查 name_jp | 取中譯 |
| 2 | `jp_term_dict`(1,495 條) | 同上 | 取中譯 |
| 3 | 52poke wiki 該卡盒繁中版頁 | 表格內 jp→中譯 | 寫進 `jp_term_dict`、下次直接命中 |
| 4 | 52poke wiki 該卡盒日文版頁 | 同上(繁中版不存在時) | 同上 |
| 5 | 都找不到 | 留 NULL、生 `_miss_translate.html` | user 看圖手動補 |

### 安全網:寫進 PROGRESS.md Known Pitfalls

每次任務跑完(不管成功失敗)、結果 append 進 PROGRESS.md。例:

```
- 2026-05-25 03:08 M5 アビスアイ 補完:113 卡、翻譯 109/113、4 張 user 手動補
```

---

## 五、API + UI 配合(Section 4 of brainstorm)

### Admin endpoint(三個、暫不加密碼、跟現有 `/api/admin/snkr-hot/refresh` 一致)

| Endpoint | 用途 |
|---|---|
| `GET /api/admin/set-backfill/status` | 看 queue / running / recent_done / recent_failed |
| `POST /api/admin/set-backfill/{set_code}` | 手動加 set 進排隊、不等到清晨 |
| `POST /api/admin/set-backfill/{id}/retry` | 失敗任務重跑 |

### `GET .../status` 回傳格式

```json
{
  "queue": [
    {"id": 3, "set_code": "OP14", "status": "pending",
     "created_at": "2026-05-24 18:45:00"}
  ],
  "running": [
    {"id": 2, "set_code": "M5", "status": "running",
     "started_at": "2026-05-24 03:00:12",
     "cards_scraped": 47, "cards_translated": 47,
     "elapsed_seconds": 287}
  ],
  "recent_done": [...],
  "recent_failed": [...]
}
```

### 一般 user 看的前端提示(`卡波/index.html`)

**SNKR 熱門卡片的右下角角標、根據狀態變化**:

| 狀態 | 角標 | 點擊行為 |
|---|---|---|
| 該卡盒在 DB 內(有 set_id) | 無角標 | 跳本站詳情頁 `#/detail?set=X&card=Y` |
| 該卡盒在排隊或執行中 | 灰色「🕒 補資料中」 | 跳 SNKR 商品頁(維持原行為) |
| 該卡盒不在 DB 也沒在排隊 | 「↗ SNKR」 | 跳 SNKR 商品頁 |

**怎麼知道某張卡屬於「排隊或執行中」?**

`/api/snkr/hot` endpoint 多回一個欄位 `backfill_status`:從 `set_backfill_jobs` JOIN 取最新 status。

### 首頁不加額外進度行

避免占畫面、未來 user 真有感再補。

---

## 六、測試 + 驗證(Section 5 of brainstorm)

### 4 階段動工 + 每階段驗

#### Stage 1:dry-run、不寫 DB

```powershell
./Python/bin/python.exe -m app.scraper.jp_set_backfill --set-code M5 --dry-run
```

**通過條件**:
- 卡數 ≥ 80
- 每張卡有 name_jp + card_number + image_id
- 沒撞 403 / timeout

#### Stage 2:接 admin endpoint、手動觸發

```powershell
curl -X POST http://localhost:8000/api/admin/set-backfill/M5
```

**通過條件**:
- `jp_card_list WHERE pg=954` 有 80-120 列
- 至少 80% 卡有 name_zh
- `set_backfill_jobs WHERE set_code='M5'` 顯示 `status='done'`

#### Stage 3:接 SNKR 熱門偵測、跑端到端

清掉 SNKR 熱門 24h cache、重爬:
- `_resolve_snkr_title_to_card` 找 M5 → pg=954 對到
- 前端首頁 M5 卡片角標消失 → 點下去跳本站詳情頁
- playwright 隨機抽 3 張驗證

#### Stage 4:接排程、第二天清晨自動跑

```powershell
curl -X POST http://localhost:8000/api/admin/jobs/set-backfill-daily/start
```

第二天早上看 `GET /api/admin/set-backfill/status` 跟 `_run_api.log`。

### 抽樣驗證資料品質(每補完一個 set 必做)

抽 10 張人工核對、通過率 ≥9/10 才算成功:

| 檢查項 | 對照來源 |
|---|---|
| jp 名 | pokemon-card.com 該卡頁面 |
| 中譯 | 52poke wiki |
| 卡圖 | 肉眼看圖跟卡名是否對應 |
| 稀有度 | SNKR title 內的 rarity |
| 跨 set 沒污染 | cardID 範圍是否連續 |

### 失敗情境驗證(optional、主流程完成才做)

- 假網址 → 看 retry 3 次後 `status=failed`
- Ctrl+C kill → 重啟看 `running > 30 分鐘` 自動改回 `pending`
- 同 set_code INSERT 兩次 → 看是否去重

---

## 七、常數 / 設定值

放在 `app/scraper/jp_set_backfill.py` 開頭、未來調整直接改:

```python
MAX_SETS_PER_DAY = 2          # 一天最多補幾個卡盒
SLEEP_BETWEEN_CARDS = 2.0     # 卡之間隔幾秒
MAX_RETRIES_PER_CARD = 3      # 每張卡最多重試幾次
RETRY_BACKOFF = [2, 10, 30]   # 重試間隔(秒)
MAX_CONSECUTIVE_FAILS = 5     # 連續失敗多少張就放棄整任務
RUNNING_STUCK_THRESHOLD_MIN = 30  # 「running 但 X 分鐘前」判定死掉
DAILY_RUN_HOUR = 3            # 排程觸發時間(凌晨 03:00)
```

---

## 八、未來延伸(本次不做)

- **EN / TW 卡表也用這套**:Stage 1-4 完成後、抽出共用邏輯到 `jp_set_backfill` 的父類別、`en_set_backfill` / `tw_set_backfill` 繼承
- **後台 web UI**:目前只有 JSON endpoint、未來可加 admin 頁顯示 queue / running / 進度條
- **使用者 push notification**:M5 補完後通知 user「你關注的卡盒已就緒」、需要 user 開啟通知偏好

---

## 九、Open Questions(暫無)

5 個 design section 都通過、所有決策已確認、沒有未解問題。

---

## 十、Constants 對齊既有 PROGRESS.md Known Pitfalls

實作時要注意的歷史教訓(避免重蹈):

- **`cards.db` 不適合並行兩個 backfill writer**(Pitfall #1):補卡盒過程中啟動 SNKR + eBay backfill 要序列、不要同時跑
- **重複 `cardID`**(Pitfall #2):新寫進 jp_card_list 用 INSERT OR IGNORE on `(pg, card_number)`、避免重複
- **Set 名拆解**(Pitfall #3):`name_jp` 用「日文 (中文)」格式寫
- **PowerShell 寫 .ps1 含中文要加 BOM**(Pitfall):若這次補的 module 含 .ps1 helper、要加 UTF-8 BOM
