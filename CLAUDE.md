# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 系統定位

寶可夢卡 PSA 鑑定價格查詢服務。**架構是雙伺服器**：
- 本目錄 = FastAPI 後端（port 8000）
- 前端 SPA 在**姊妹目錄** `C:\Users\Dong Ying\Desktop\卡波\index.html`、由獨立的 `python -m http.server 8080` 提供
- 整套用 `..\卡波\卡波.hta`（Windows HTA splash）雙擊啟動、自動 detect+起兩個伺服器

README.md 提到的 `static/index.html` 已刪除、`python -m app.main` 過時 — 以本檔為準。

## 常用指令

```powershell
# 啟動後端（port 8000）— run_api.py 預設設 CARDPOOL_DISABLE_JOBS=1 不跑背景排程
./Python/bin/python.exe run_api.py

# 啟動前端靜態伺服器（在 ..\卡波\）
cd "..\卡波" ; python -m http.server 8080

# 一鍵全套啟動
..\卡波\卡波.hta
```

**Python 解譯器一律用 `./Python/bin/python.exe`**（embedded、含已裝 site-packages）、不要用系統 python。

```powershell
# 直接 DB 查詢 — 必須 PYTHONIOENCODING=utf-8（Windows cp950 預設會炸 JP/ZH 輸出）
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; ..."
```

```powershell
# 停掉並重啟後端（HTA 啟動的 PID 不會自動換新 code）
$pid_=(netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]; taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py  # 接著 hit /api/cardlist/sets 確認 200
```

**沒有正式 test 套件**。根目錄的 `test_*.py`（test_snkr_fix / test_ebay_filter 等）是臨時 debug 腳本、不是回歸測試。改 code 後測試方式 = 重啟後端 → 手動打 endpoint / 開瀏覽器看頁面。

## 三層架構

```
雙擊 卡波.hta
   ├─► python http.server :8080 → ..\卡波\index.html（單檔 SPA）
   │                                    │ fetch (API_BASE=:8000)
   └─► python run_api.py :8000 → FastAPI / uvicorn → cards.db (SQLite, 43 tables)
```

### 後端（`app/`）

| 檔案 | 角色 |
|------|------|
| `app/main.py` | FastAPI 入口、~85 個 endpoint |
| `app/database.py` | DB 存取 helper（`get_cards_by_set` 等）|
| `app/scraper/ebay.py` | eBay 爬蟲（**Playwright sync API** via ThreadPoolExecutor）|
| `app/scraper/snkrdunk_http.py` | SNKR 爬蟲（**httpx async**、目前主路徑）|
| `app/scraper/snkrdunk.py` | SNKR 舊 Selenium 版本、保留 fallback 但已不主用 |
| `app/jobs/` | APScheduler 背景任務（HTA 啟動模式預設關閉、`/api/admin/jobs/{name}/start` 觸發）|

### 前端

`..\卡波\index.html` 單檔 SPA、hash routing（`#/sets` / `#/set?set=X` / `#/detail?set=X&card=Y`）、Chart.js 4.4 多源價格圖表。API_BASE 自動讀 `location.hostname:8000`。

## 資料模型 — 關鍵表

| 表 | 筆數 | 角色 |
|----|------|------|
| `card_list` | ~50k | 多語言主卡表、PK=id；`cards` 表是 PSA cert 用途暫未使用 |
| `jp_card_list` | 21,552 | **JP 體系主表**、PK=cardID、含 card_number、`prices_synced_at`（SNKR）、`ebay_prices_synced_at` |
| `jp_card_list_set` | 368 | JP set 主表、`name_jp` 格式為 `日文 (中文)`（用 `lastIndexOf(' (')` 拆）— **pg→中文名對照表查 `docs/jp_sets_lookup.md`**（user 聽不懂 pg 數字、提到具體 set 要附中文名）|
| `card_prices` | ~107k | **統一價格表**、UNIQUE 鍵 `(set_id, card_number, source, listing_url)`、不依 cardID |
| `snkrdunk_mapping` | 232k | SNKR product → card 對映、`is_pokemon` flag 標記是否為寶可夢卡 |
| `pokemon_dict` | 1,025 | EN/JP/romaji 寶可夢名字典（全國圖鑑覆蓋）|
| `jp_term_dict` | 1,495 | **trainer/energy/item JP→EN 字典**、Bulbapedia `\|jname=` 多源驗證、source 欄含 `bulba_jname_verified` / `user_manual`、`_translate_jp_card_name_to_en` fallback 在 pokemon_dict miss 後查這裡 |
| `en_card_list` / `tw_card_list` | 20k / 11k | EN / TW 體系、目前只 SNKR/eBay 部分覆蓋 |

**Promo set pg**：9001（SV-P 朱紫期）/ 9002（M-P MEGA 期）/ 9003（朱紫期但 MEGA 階段、由 cardID 推算）/ 950（高級擴充包 MEGAドリームex M2a）/ 951（バトルコレクション 合輯再印 774 卡）。

## 價格 sync endpoint 三套並存

| Endpoint | 用途 | max_pages | psa_grade |
|----------|------|-----------|-----------|
| `POST /api/prices/sync/{set_id}/{cn}` | 即時 sync（SNKR + eBay 並行）| 5 | NULL |
| `POST /api/prices/sync_snkr/{pg}/{cn}` | SNKR-only full-history backfill | 500 | 10 |
| `POST /api/prices/sync_ebay/{pg}/{cn}` | eBay-only full-history backfill | 50 | 10 |

`sync_snkr` / `sync_ebay` 從 `jp_card_list` JOIN `jp_card_list_set` 直查（不依賴 `card_list` 主表）、成功後 UPDATE 對應的 `*_synced_at` 欄位用 `(pg, card_number)` **不是 cardID**（避免重複 cardID 漏標）。

### Backfill 驅動腳本

| 腳本 | concurrency | gating |
|------|-------------|--------|
| `_backfill_all_jp_snkr.py` | 8（httpx 輕量）| `prices_synced_at IS NULL` |
| `_backfill_all_jp_ebay.py` | 2（Playwright 重、c=4 會 OOM backend）| `ebay_prices_synced_at IS NULL` |

兩者皆可中斷續跑、`*_synced_at IS NULL` gating 自動 pick up 未完成的。

### 長時 eBay backfill 防 hang

**eBay scraper（Playwright sync in asyncio）每 ~5 小時會整個 hang**：兩個 worker 同時 await response 不回來、cards.db 完全沒寫入、processes 還活著但沉默。Phase 4 全 JP backfill 2 次撞到。

解法：`_resilient_backfill.ps1` wrapper — 每分鐘看 `cards.db-wal` 大小變化、idle >7 min 就 `Get-Process python | Stop-Process -Force` 然後重起 API + backfill cycle、`ebay_prices_synced_at IS NULL` gating 自動接續。pending=0 時自動結束。**不要手動 babysit、直接 `powershell -ExecutionPolicy Bypass -File _resilient_backfill.ps1` run in background**。

進度監看：`_monitor_progress.py`（self-tracking state in `_monitor_state.txt`、印一行 `done/total +delta_cards +delta_rows`）配 `Monitor` 每 5 min 跑一次。

## 常見坑

- **cards.db 不適合並行兩個 backfill writer**：SQLite WAL mode 解 reader 並行、writer 仍序列化。任何長 tx + 短 tx 並存會餓死短 tx。實證 2026-05-20：card_type backfill 50 卡 commit 一次、寫鎖 hold ~100s、那段時間 eBay sync 全 `database is locked` → 500（aiosqlite default 5s timeout 救不到）。**規則：任何兩個寫 cards.db 的 backfill 不要同時跑**。要嘛序列（先 A 跑完再 B）、要嘛把長 tx 改 per-card commit（fsync 多 50x 但 lock 窗口從 100s 壓到 ms）。提案並行 cards.db 寫之前要明確說「但會撞 SQLite 寫鎖」。
- **重複 cardID**：`jp_card_list` 有 785 組 `(pg, card_number)` 對映 ≥2 個 cardID。任何 UPDATE / SELECT 統一用 `(pg, card_number)` 而非 cardID。`card_prices` 鍵不含 cardID。
- **Set 名拆解**：`jp_card_list_set.name_jp` 用 `日文 (中文)` 格式、scraper `_set_name_variants`（`app/scraper/snkrdunk_http.py:99`）會脫括號 + 按空格拆 token、且**尾部 token 優先**（複合 set 名子集名常在後、避免前綴撞舊 set）。
- **JP→EN 卡名翻譯管線**（`app/main.py:_translate_jp_card_name_to_en`）：
  1. Strip HTML（Bulbapedia `<span class="pcg pcg-megamark"></span>` → is_mega=True）
  2. 前綴：`ロケット団の` → `Team Rocket's`
  3. 地區形：`ガラル ` / `アローラ ` / `ヒスイ ` / `パルデア ` → `Galarian` / `Alolan` / `Hisuian` / `Paldean`（注意尾空格）
  4. `メガ` → `Mega`
  5. 後綴 `VMAX/VSTAR/VUNION/GMAX/GX/EX/ex/V` 抽取
  6. core 先查 `pokemon_dict`、miss 再查 `jp_term_dict`、再 miss 用全名查 `jp_term_dict`
  7. 全 miss 回 `None`、caller 走 `card_name_jp` 路徑（不再 fallback 到 `name_alt`、99.5% 也是日文）
- **`name_alt` 已棄用作翻譯來源**：保留欄位但 `_translate_jp_card_name_to_en` 不再 fallback 到它。
- **eBay scraper 是 sync Playwright in asyncio**：會印「using Playwright Sync API inside the asyncio loop」warning、但能跑；長時間累積會 hang（~5 小時、見上「長時 eBay backfill 防 hang」section）；批次 backfill 用 c=2。
- **eBay query / post-filter 設計**（2026-05-22 v2 PSA-label 規格、取代 2026-05-17 v1）：
  - `_build_url`（`app/scraper/ebay.py:650`）格式：
    `{release_year} POKEMON JAPANESE {set_code_en} {set_name_en} {rarity_full} {card_name UPPER} PSA {grade}`
    範例：`2025 POKEMON JAPANESE M2 Inferno X SPECIAL ART RARE MEGA CHARIZARD X EX PSA 10`
    — 模仿賣家標題（直接抄 PSA label 上的字）、precision 100%、阻擋跨 set 同名同號污染（Pitfall「跨 set 同名同號 SNKR/eBay 污染」）
    — Recall 比 v1 舊 query 低 ~50%（v1 是 `PSA 10 {name} #{num}` + `_in_kw=4` 搜 title+description）、user 接受此 trade-off（寧缺勿錯）
  - URL aspect params 只保留：`LH_Sold=1 / LH_Complete=1 / _ipg=240`（拿掉 `_dcat / Grade / Language / _sop / _in_kw`、見下方 trigger 警告）
  - 拿掉 Query B（日文名 query）— 新 query 已用「POKEMON JAPANESE」target JP listings、不需要 JP fallback
  - **三個 param 合起來 trigger eBay splashui challenge wall**（2026-05-22 ablation 確認）：
    `_sop=13` + `_ipg=240` + `_in_kw=4` — 任兩個過、三個合一起就被擋。**保留至多 2 個、絕對不要三個都加**
  - **`-` 連字號在 _nkw query 也 trigger splashui**：賣家標題用 `M4-NINJA SPINNER`、但我們 query 必須改用空格 `M4 NINJA SPINNER`、eBay search 內部 hyphen/space 都 match 同一批 listings
  - **POKEMON 是 trust signal**：「POKEMON」keyword 不能拿掉、否則被 splashui 擋（賣家標題 100% 含 Pokemon、eBay 視為一般 trading card 玩家正常 search）
  - **`_PG_TO_EBAY_INFO` dict**（`app/main.py`）：pg → English set abbrev + set name + release_year。目前覆蓋 5 個熱門 pg：
    949 = M2 Inferno X (2025) / 950 = M2a MEGA Dream ex (2025) / 951 = MC Start Deck 100 Battle Collection (2025) / 952 = M3 Munikis Zero (2026) / 953 = M4 Ninja Spinner (2026)。
    未來新 set 加 entry 即可。fallback 從 `jp_card_list_set.release_date` 拆 year
  - **`_RARITY_TO_EBAY` dict**（`app/main.py`）：SAR→"SPECIAL ART RARE" / SR→"SUPER RARE" / UR / AR / RR / HR / CHR / SSR / CSR / MUR 縮寫對全名映射。普卡 C / U / R / 無標示**不放 query**（賣家標題很少寫普卡稀有度、加進 query 會降 recall）
  - **post-filter `_title_has_card_name_token`**（`app/scraper/ebay.py:_significant_name_tokens`）：EN token **全部命中** OR JP token **任一命中**、避免 `Ball` 共用導致 Master Ball 誤命中 Ultra Ball
  - JP 卡 bypass `_title_has_set_token` 過濾（query 已無 set token）
- **HTA 啟動模式**：`run_api.py` 設 `CARDPOOL_DISABLE_JOBS=1`、背景排程預設不跑、避免影響前端瀏覽。要手動啟動：`POST /api/admin/jobs/{name}/start`。
- **Windows cp950 編碼**：`./Python/bin/python.exe -c "..."` 印 JP/ZH 必加 `PYTHONIOENCODING=utf-8`、否則 UnicodeEncodeError。
- **資料夾命名含全形字**：前端在「卡波/」、bash 操作需用 quote `"卡波/index.html"`、PowerShell 沒問題。

## 操作慣例

- **改 DB 前先 backup**：`cp cards.db cards.db.before-<reason>-YYYYMMDD-HHMMSS`、根目錄常見 `cards.db.before-jp-set-trans-batch{N}-*` / `before-promo-fixes-*` / `before-ispokemon-fix-*` / `before-resync-zeroprice-*` / `before-ebay-backfill-*` 等。
- **DB 改動腳本以 `_` 開頭命名**：`_apply_*.py`、`_backfill_*.py`、`_audit_*.py` 等。產生的審閱 markdown 是 `TRANSLATION_REVIEW_BATCH{N}.md`。
- **改 backend 必重啟**：`reload=False`、HTA 起的 PID 跑舊 code 直到手動 kill+重啟。
- **日工作記錄寫在姊妹目錄**：`C:\Users\Dong Ying\Desktop\卡波\工作統整_YYYY-MM-DD.md`、依日期追加章節（最後 §N 結尾接新 §N+1）。
- **JP eBay 覆蓋現況**：21,552 卡中 86.2% 0-hit（trade-off：precision ≥95%、recall ~14%）。trainer 桶（ナンジャモ/ボスの指令/ハイパーボール 等）覆蓋率最低、因 token filter 嚴格。要放寬看 `app/scraper/ebay.py:_title_has_card_name_token`。
- **eBay `_NAME_STOPWORDS` 含 rarity tail**（2026-05-19 升級）：`SAR / SR / UR / AR / RR / HR / CHR / SSR / TG / PR / TR` 全在 stopwords 內、不參與 EN token AND match。賣家標題常省略稀有度（如 `Mega Charizard X ex #110 PSA 10` 沒寫 SAR）、AND filter 不放這些 tail 會誤排 90% 以上 listing。實測對 949/110 SAR 從 6 → 593 row（12.9x）、抽 10 row 9/10 同名同卡（1/10 lot 標題）。**未來改 ebay scraper、不要把 rarity tail 拿出 stopwords**。trade-off：precision 95% → ~90%、recall 14% → 30-50%。
- **SNKR / eBay lookup：set_code 第一優先 + 寧缺勿錯**（2026-05-19 升級）：跨 set 同名同號污染（例 M2a Mega Dream vs M1L Mega Brave 都有 メガルカリオex #92）是歷史 lookup fallback 的大坑。`_lookup_apparel_id` 已改用 Stage 0 = `WHERE set_code=? COLLATE NOCASE AND card_number=?`、最精準。**若 caller 給了 set_code 但 SNKR mapping 沒對應、就直接 `return None`、不要走 Stage 1-3 set_name/card_name fallback**（fallback 會跨 set 亂抓、產生 cross-set pollution）。寧可前端顯示「暫無紀錄」、不要顯示錯卡的價格。同邏輯適用於 eBay scraper 未來如果建 mapping。caller chain（main.py 的 sync endpoints）必須從 jp_card_list.set_code 取後傳入。

## 我的偏好與慣例

> 這裡放「不會變」的規則。每次開 session 自動載入。
> 每天在變的東西請放 PROGRESS.md，不要放這裡。

### 溝通
- 一律用繁體中文回答。
- **解釋要用白話、技術詞要附說明（user 強調過 2+ 次、最重要規則之一）**：

  禁止：只丟術語沒解釋。例：「sustained-load throttle」「stealth lib」「sticky footer」「Ralph Loop」「prerequisite」「stochastic」「session 太淺」「anti-bot fingerprint」「aspect-ratio」「root cause」「ToS」「deep warmup」 — 這些**對 user 都是黑話**。

  做法：用「白話 + 括號附原文」格式。

  | ❌ 不要這樣 | ✅ 改成這樣 |
  |-------------|-----------|
  | scraper sustained-load throttle | 爬蟲跑太久、被 eBay 認出來限速（術語：throttle） |
  | 用 stealth lib | 裝一個讓爬蟲「假裝是真人」的套件（叫 playwright-stealth） |
  | sticky footer | 按鈕黏在底部、滑動時不會跟著捲走（CSS 叫 sticky） |
  | Ralph Loop | 讓 AI 重複跑同一個指令、直到做完才停的模式 |
  | prerequisite checks | 動工前要先做的檢查 |
  | session 太淺 | 我們爬蟲的「假裝瀏覽紀錄」太短、看起來像機器人 |
  | aspect-ratio 9:5 | 圖表寬高比例 9:5（橫的長方形） |
  | root cause | 真正的源頭問題 |

  **判斷標準**：每寫一個英文詞、問自己「user 看不看得懂」。看不懂就改寫或加說明。**寧可句子變長、不要 user 看不懂**。

  範例 / 選項 / AskUserQuestion 的 option label 跟 description 全部適用此規則 — 不只是回答 text。

- **問 user 技術決策、不要只丟術語問「要不要做」**（2026-05-22 升級 + 同日修正）：問 user 問題前自我檢查 — 這條問題裡有沒有 user 看不懂的術語？user 看了能不能評估？如果問題本身是純技術細節（如「SQLite UNIQUE 約束 PRAGMA 不顯示」「cascade dedupe 順序」「IntegrityError」「composite PK」這類）、**先在問題前用白話解釋為什麼這對 user 重要**（如「未來我寫資料庫腳本會撞到、現在記下來避免重蹈」）、再問 yes/no。**若 user 答「看不懂」、不要因此自行決定 — 而是改用更白話的方式重新講一次、再問**。User 看不懂代表我問的方式有問題、不代表他不想決定。**所有決定都要跟 user 討論**（除了下一段「### 決策」寫的純技術細節例外：變數命名、import 順序、純語法選擇）。決定權永遠屬於 user、我的角色是「把技術問題翻譯成白話 + 列選項 + 給推薦」。

- **講 JP set 不要只丟 pg 數字、要附中文名**（2026-05-22 升級）：user 看不懂 `9001` / `9002` / `949` / `950` 這些 internal 編號。提到任何 JP set、**每次都要帶中文名**、不只第一次提及。格式：「中文名 (pg=XXXX)」或「中文名 (pg=XXXX, set_code=YYY)」、不要單獨丟 pg。例：「『朱紫期 promo (MEGA 階段)』(pg=9003) 併入『朱紫期 promo』(pg=9001)」、不是「9003 → 9001」。對照表在 `docs/jp_sets_lookup.md`、Read 該檔可查 368 個 set 的 pg → 中文名 / 日文名 / set_code / 發售日。對照表用 `_gen_jp_sets_lookup.py` 重生（jp_card_list_set 改了重跑）。同樣道理 set_code 短代碼（SV-P / M2a / M2 等）對 user 也不直觀、雖比純數字好、但**仍要附中文名**。

### 決策
- **做決定前先提問、不要猜 user 想法、不要沒問就動手做大決定**：規劃 / 設計過程中遇到「砍 feature / 改範圍 / 改方向 / 加減 phase / 重排優先序」這類**有方向性的決策**，即使分析非常清楚、即使 user 看了 99% 會同意、也要先用 `AskUserQuestion` 拿到確認再動。決策權屬於 user、我的角色是分析 + 給建議 + 列選項 + 提出強烈推薦。AskUserQuestion 可以同時列「我推薦的選項」+「其他可行選項」、讓 user 可選但不替他決定。「先做了再讓 user 改」≠「先問再做」、前者讓 user 感覺被剝奪選擇權。例外：純技術細節（變數命名、import 順序、語法選擇）不用每個都問。

### 資料來源優先序（2026-05-25 升級）
- **遇到資料來源衝突時（卡名 / 卡圖 / 卡號 / set 內容 等），優先採信日本官方 `pokemon-card.com`**（已 mirror 到 `jp_card_list` 表 + `thumb_url` 欄）。
- artofpkm.com / pokellector / Bulbapedia 等第三方來源**只當輔助、不當 source of truth**。實證：2026-05-25 發現 artofpkm 對某些 jp set（Pokemon-151 / Shiny-Treasures-ex / VSTAR-Universe 等）image_url 順位整套偏移、且對 Dark-Phantasma / Galactics-Conquest / Awakening-of-Psychic-Kings 等 set 直接「整套打散重組」、收的卡列表跟官方完全不同。`name_jp` 多半對、但 `image_url` 不可信、`card_number` 跟官方不同步。
- 例外：對 `jp_card_list` 沒覆蓋的 set（promo set XY-P / SwSh-P / BW-P 等舊期 promo、或老 random pack 像 2009 movie / battle starter pack），需要其他來源時、優先順序：**pokemon-card.com `details.php/card/{id}` 個別卡頁 → Bulbapedia `|jname=` 驗證 → pokellector → artofpkm**。
- SNKR / eBay 賣家標的 `set_code` / `card_number` 信任度跟 `jp_card_list` 同級（賣家依官方規格標、跟官方一致），可用來輔助確認 `(set_code, card_number)` 對映。
- 未來新增爬蟲 / sync endpoint **一律用 jp_card_list 風格的 `(pg, card_number)`** 寫入 `card_prices`、不用 card_list 的 `(set_id, card_number)` slug（後者錯位風險高）。
- **中文翻譯來源優先序**（2026-05-25 PM 升級）：
  1. **52poke 神奇寶貝百科**（[wiki.52poke.com](https://wiki.52poke.com/)）為主：httpx 直接 fetch、無 Cloudflare。個別 page title「{中文名} - 神奇寶貝百科」格式好 parse。**Cheat 表**：`zh-hant/遊戲人物列表（在其他語言中）` 一頁 488 條 EN/JP/繁中對映、一次 fetch 全得到。
  2. **Bulbapedia 輔助**：對 httpx + headless playwright 雙擋 Cloudflare、要 user-data-dir persistent_context 繞。In other languages 表 regex 易誤抓 voice actor / 集名（如 Ash Ketchum 抓到「賀世芳」是配音員）、quality 要 spot check 多。Bulbapedia 留給 52poke 沒收的 set / unique 角色名比對用。
  3. **hardcode 官方常見譯名兜底**：知名主角 / 通用職位類（如 Ash Ketchum → 小智、Team Rocket Grunt → 火箭隊手下）52poke search miss 時直接 hardcode（屬於「外部 source 失效時的 finalize」、不違反禁手寫 seed 原則）。
  4. **避雷**：jp_term_dict 內 27 條 name_zh 填日文 katakana 當「翻譯」（v1 反查 garbage 風險）、UPDATE 過 SET NULL 但未來新建 dict 條目要記得「沒譯到 = NULL、不 copy name_jp 假裝有譯」。

### UI 顯示慣例
- **多語顯示三行置中**（2026-05-25 PM 升級）：character 角色頁、寶可夢圖鑑、set 列表卡片等含多語名顯示場景、**預設「主標中文 / 第二行日文 / 第三行英文」三行置中**（CSS `text-align:center`、各行同 class 同字體 / 字級、不混 main/sub 大小區別）。對應 frontend pattern：
  - JP set 卡名兩行（jp + zh）
  - EN set 卡名兩行（en + zh）
  - Character / Pokedex 三行（zh + jp + en）
  缺哪一語就跳過該行（不顯示空 div）。
- **CSS font-weight 不要超過字體實際載入的字重範圍**（2026-05-25 PM 升級）：實證 Plus Jakarta Sans 最大字重 800、CSS 設 900 會跑 synthetic bold（瀏覽器人工加粗）、不同字級下視覺不一致。**通則**：設 `font-weight: N` 前先查該 font-family 從 Google Fonts 載入了哪些 wght（看 `<link href="...&family=...:wght@A;B;C">`）、N 必須在範圍內、否則改用最近真實字重值。

### Git
- **Commit 要按語意拆、不要混**：開始 commit 前先用 `git diff --numstat` / `git log --oneline` 確認哪些是這次工作、哪些是之前累積的未 commit 改動。多個語意的改動分多個 commit、不混進同一個。拆 commit 用「備份混合狀態 → checkout HEAD → 重做本次改動 → 產 patch → 還原混合 → `git apply -R` 拆出舊改動」這套 patch 法。

### 程式碼風格（範例 — 請使用者依實際習慣調整）
- 純 HTML / CSS / 原生 JS，不引入框架或建置工具。
- CSS class 命名統一風格；不寫 inline style。
- JS 用 const / let，不用 var。

### 硬規則（範例 — 請使用者依實際習慣調整）
- 改完程式碼後，先自己檢查邏輯有無問題再說「完成」。
- 不要動第三方 / vendor 檔案。

### 工作流程
- 開工用 /today，收工用 /wrap。
- **/today 開工流程：先依目前進度（PROGRESS.md 工作日誌 + Known Pitfalls + 開放選項）列出可選的「大項」（如：網站開發 / Bug 修補 / 資料庫補齊 / MVP 推進 / scraper 改進 / 卡表完善 等），讓 user 選大項。再切到該大項下的小項目。不要直接從 PROGRESS.md 末條跳一個小任務開工。**
- Known Pitfalls 累積在 PROGRESS.md；確定是結構性的坑，再升級搬來這份 CLAUDE.md。
- **自動挑選適合的 plugin / skill、不要等 user 提醒**：每個任務開始前掃 system reminder 列出的 skill 清單、跟任務 description 對得上就直接 invoke Skill tool。常見對應：寫 plan → `writing-plans` / `brainstorming`；排查 bug → `systematic-debugging`；完成前 → `verification-before-completion`；改 CLAUDE.md → `claude-md-management:revise-claude-md`；前端 UI → `frontend-design`；查文件 → context7 MCP；網頁測試 → playwright MCP。不確定要不要用、傾向「用」（using-superpowers 原則「1% chance 就用」）。
- **長時爬蟲 / backfill 期間每 30-60 min 主動查 row 數**：不只看 driver log 的 ok/fail 計數、直接 query DB 看實際 row 寫入。連續幾百卡 0 row 就是異常、立即報 user + 提議暫停診斷。Driver 報告「ok」≠ 實際成功。
- **踩到坑當下就記、不要等收工**：bug / 誤判 / 設計失誤 / 反覆撞同樣的錯 → 立刻 Edit `PROGRESS.md` `## Known Pitfalls` 區段加進去（不必先問、這是 documentation）、然後在回覆裡 mention「已加進 Known Pitfalls」讓 user 知道。避免 /wrap 時忘記細節。
- **user 給 URL / 截圖 / 參考資料、第一動作必須是打開看實際內容**（2026-05-22 升級）：不可以憑檔名 / URL 路徑猜內容、不可以套用前面想的策略當作那個 URL 是。具體要做：
  - URL → 用 WebFetch 或 playwright（wiki 之類防 WebFetch 的）打開、看 HTML 結構 / 資料樣本
  - 截圖 → 必須用 Read 看圖、不能憑檔名猜
  - 程式碼片段 / 設定檔 → 必須讀完整檔
  - 看完才能回應 user 或設計策略
  - **同一 session 我已違反 2 次被罵**：(a) user 訊息「set to set」我直接寫腳本沒問意圖、(b) user 給 wiki URL「花椰猴（BW-P）」我沒點開直接套用前面 wiki search by jp 名策略。已存進 [feedback memory](memory/feedback_check_user_urls_first.md)。
- **修改全域設定（CSS body font / font stack / 全域 class / API 共用 schema 等）時、優先用 scope 區分而非全域 override**（2026-05-22 升級）：全域 override 副作用不可控、會傷其他元素。對「想動 A 不要動 B」的需求、用 CSS class scope / lang attribute / namespace 等 scope 工具精準框範圍。實證：今天改 DotGothic16 字體 stack 把它放進 body font-family fallback、結果它無 unicode-range 限制、搶到所有 CJK 漢字、user 看到首頁中文「寶可夢卡牌」也變 pixel 反彈「不能只改 X 嗎、其他中文也被改掉了」。正解：加 `.jp-pixel` class wrap 日文段、CSS 用兩個 @font-face（一個 unicode-range 限制版進 body、一個無限制版只給 class 用）。**通則**：CSS / 字體 / 配置改動前自問「這改動會不會搶到我沒預期的元素？」、若答 yes、用 class / scope 工具隔離、不要全域改。
- **設計類決策（字體 / 排版 / 配色 / 元件樣式）優先生 preview / mockup 給 user 看、不要只用文字描述**（2026-05-22 升級）：user 對視覺敏感、文字描述「Plus Jakarta Sans 圓潤現代」對 user 沒實際意義、要看到才能決定。實作方式：建一個 `_*_preview.html` 或 `_*_mockup.html`（依 `.gitignore` 規則 local-only）、含所有候選 option 的視覺呈現（同一段 sample text 各 option 渲染）、給 user URL 看完選一個。實證：今天字體選擇先用 AskUserQuestion 列 4 個英文字體文字選項、user 選 "Other: Superpowers Brainstorming顯示給我看"、改成寫 `_font_preview.html` 含 31 種字體（英 15 / 中 4 / 日 12）才有效。**通則**：設計類決策、AskUserQuestion 文字選項不夠、要 visual mockup。
- **UI / 視覺改動「邊做邊看」原則**（2026-05-22 升級）：implementation 階段遇到任何 UI 改動（layout / modal / 圖示 / 顏色 / 表格 / 元件 / 視覺風格 等）、優先順序是「先做可看的 mockup → user 看 → 確認 OK 才進下個 phase」。具體做法：(a) inline write code、playwright 截 screenshot 發給 user 看、(b) 或 push 到 visual companion 給 user 點選。**不要「寫一大堆 code 後 user 才看」**。MVP / 開發階段尤其適用。複雜 UI 一次截多張 screenshot 比較。實證：今天 portfolio 功能用此流程跑、寫 1 段 UI 截圖 → user 看 → 改 → 再截 → ... 反覆 6-7 輪、最終出來的 UI 完全對齊 user 心目中的、沒有「做完才發現方向錯」的浪費。
- **長時 backfill 跑完每個卡盒系列、自動列高稀有度 0 row 卡清單給 user verify**（2026-05-22 確立）：每個 set（pg）跑完後、列出該 set 中 `ebay_prices_synced_at IS NOT NULL` 但 `card_prices` 0 row 的高稀有度卡（SAR / SR / UR / AR / RR / HR / CHR / SSR / CSR / MUR）給 user 手動 eBay 搜尋 verify。表格欄位：**卡號 / 稀有度 / 日文名 / 英文名**。**普卡（C / U / R / 無標示）省略**（賣家很少送 PSA 10、列出 100+ 張 user 也難逐張 verify、列了反而干擾）。user 收到後手動驗證、如果發現某張卡實際 eBay 有資料 → 表示我們 query 設計仍有 false negative、回頭針對該卡的真實 listing title 模式 micro-adjust query。實證：5/22 跑 5 pg 共 1,282 卡、列 71 張高稀有度 0 row 給 user verify。**未來其他 backfill（EN 卡表 / 新 set / 重 sync 等）都適用此流程**。
- **scraper / 補抓寫 DB 前、要先 visual report 給 user verify 再寫**（2026-05-24 升級）：scraper 撈到的新資料、寫進 DB 前**先做 visual report HTML**（含卡圖 + listing 縮圖 + 標題 + 價格 + 標記按鈕「×同卡漏抓」/「✓驗過真 0」+ 改英文名 + localStorage 持久化 + 匯出 markdown 回報）、用 `python -m http.server 8081` serve 給 user 看、user 視覺判斷哪些是真同卡 / 哪些是跨 set 污染、標記後**匯出回報清單**貼回對話、我才實際 INSERT card_prices。**避免直接寫 DB 後發現污染要 revert**。實證：5/24 對 54 張 0-row 用 v2 query 重爬、user 視覺判斷 1/54 漏抓 (Psyduck AR)、其他 53 是市場真 0 或跨 set 污染 — 避免錯誤寫 53 筆。**通則**：所有 scraper / 補抓 / 重爬任務、寫 DB 前都用 visual companion 視覺化、user verify 後再 commit DB。**唯一例外**：(a) user 明確說「全部寫進去」、(b) 寫量 < 5 筆的小量補、(c) 全量 backfill（如 5/22 1,282 卡 PSA-label v2、太多沒法逐張 verify、靠 query 設計 precision 保證）。實作參考：`_recrawl_54_html.py` template。

## 編碼行為準則

降低常見 LLM 寫程式錯誤的行為準則。與專案特定指令合併使用。

**權衡**：這些準則偏向謹慎而非速度。對瑣碎任務、用判斷力。

### 1. 先想再寫

**不要猜想。不要藏起困惑。把權衡攤開來說。**

實作前：
- 明確說出你的假設。不確定就問。
- 如果存在多種解讀、列出來 — 不要默默自己選。
- 如果有更簡單的方法、講出來。值得時就 push back。
- 如果有不清楚的、停下來。指出哪裡讓你困惑、問。

### 2. 簡單優先

**用解決問題所需的最少 code。沒有臆測性的東西。**

- 不做沒被要求的 feature。
- 不為單次使用的 code 做抽象。
- 不做沒被要求的「彈性」或「可配置性」。
- 不為不可能發生的情境做錯誤處理。
- 如果你寫了 200 行但可以是 50 行、重寫。

問自己：「資深工程師會說這太複雜嗎？」如果會、就簡化。

### 3. 外科手術式改動

**只動你必須動的。只清你自己造成的爛攤子。**

編輯既有 code 時：
- 不要「順手改善」相鄰的 code、註解、或格式。
- 不要 refactor 沒壞的東西。
- 配合既有風格、即使你會用別的方式寫。
- 如果你發現無關的 dead code、講出來 — 不要刪。

當你的改動造成 orphans（孤兒 import / 變數 / 函式）：
- 移除 **你的** 改動讓它們變成 unused 的東西。
- 沒被要求就不要移除既有的 dead code。

測試方式：每一行改動都該能直接 trace 回使用者的需求。

### 4. 目標驅動執行

**定義成功條件。Loop 到驗證通過為止。**

把任務轉成可驗證的目標：
- 「加 validation」→「對不合法 input 寫 test、再讓它通過」
- 「修 bug」→「寫一個重現 bug 的 test、再讓它通過」
- 「Refactor X」→「確認改前改後 test 都過」

多步任務、先列簡短 plan：
```
1. [步驟] → 驗證：[檢查]
2. [步驟] → 驗證：[檢查]
3. [步驟] → 驗證：[檢查]
```

強的成功條件讓你能獨立 loop。弱的條件（「讓它 work」）需要不停 clarify。

---

**這些準則生效的徵兆**：diff 裡不必要的改動變少、因為過度複雜要重寫的次數變少、clarifying questions 在實作前出現而非在錯了之後。
