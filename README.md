# Cardpool — 寶可夢卡 PSA 鑑定價格查詢服務

PSA 鑑定卡多源價格查詢、收藏 / 持倉管理、盒裝交易撮合的個人開發專案。後端為 FastAPI + SQLite、前端為單檔 SPA、資料來自 SNKRDUNK（日本卡市場）與 eBay（國際成交價）。

---

## 架構總覽

**雙伺服器設計**：

```
雙擊  ../卡波/卡波.hta  (Windows HTA 啟動器)
  ├─► python -m http.server :8080  →  ..\卡波\index.html   (單檔 SPA、Chart.js 圖表)
  │                                          │  fetch (API_BASE = location.hostname:8000)
  └─► python run_api.py     :8000  →  FastAPI / uvicorn    →  cards.db (SQLite、~43 tables)
```

- **後端**（本 repo）：FastAPI、~85 個 endpoint、含 SNKRDUNK + eBay 爬蟲、JP→EN / JP→ZH 翻譯管線、APScheduler 背景任務
- **前端**（**姊妹目錄 `..\卡波\index.html`、不在本 repo**）：單檔 SPA、hash routing、Chart.js 4.4 多源價格圖表
- **啟動器** `..\卡波\卡波.hta`：Windows HTA 雙擊執行、自動 detect + 起兩個 server、開瀏覽器（帶 cache buster 繞 browser cache）

> ⚠️ 前端 SPA 跟 HTA 啟動器都在姊妹目錄 `..\卡波\`、目前不在本 repo。要跑完整系統需要 owner 提供姊妹目錄檔案。

---

## 本機開發環境

### 環境要求

- **作業系統**：Windows 10/11（PowerShell）
- **Python**：**用內附嵌入式 Python `./Python/bin/python.exe`、不要用系統 Python**（嵌入式已裝好所有 site-packages、跨機器一致）
- **資料庫**：`cards.db`（SQLite、~800 MB+、含 ~43 張表）—— **不在 repo 內**、要 owner 提供
- **環境變數**：複製 `.env.example` 為 `.env`、填 LINE Bot token 等

### 安裝依賴（首次設定）

如果用系統 Python 不用內附 embedded、跑：

```powershell
pip install -r requirements.txt
playwright install chromium
```

主要依賴：FastAPI / uvicorn / httpx / aiosqlite / Playwright / APScheduler / BeautifulSoup / line-bot-sdk

### 啟動

```powershell
# 啟動後端（port 8000）
./Python/bin/python.exe run_api.py

# 另開一個 terminal 啟動前端（在姊妹目錄）
cd "..\卡波" ; python -m http.server 8080
```

或者一鍵啟動（含自動偵測 + cache buster）：

```powershell
..\卡波\卡波.hta
```

啟動後：
- 前端：http://localhost:8080
- 後端 API：http://localhost:8000
- Swagger API 文件：http://localhost:8000/docs

### 停掉並重啟後端（改 backend code 必做）

```powershell
$pid_ = (netstat -ano | findstr ":8000 .*LISTENING").Split()[-1]
taskkill /F /PID $pid_
./Python/bin/python.exe run_api.py
```

`run_api.py` 預設 `reload=False`、改 code 不會自動重啟（HTA 啟動的 PID 也是、要手動 kill + run）。

### Windows 編碼注意

直接 `python.exe -c "..."` 印 JP / ZH 內容、必加 `PYTHONIOENCODING=utf-8`、否則 Windows cp950 預設會炸 UnicodeEncodeError：

```powershell
$env:PYTHONIOENCODING="utf-8"; ./Python/bin/python.exe -c "import sqlite3; ..."
```

---

## 後端目錄結構

```
app/
├── main.py              # FastAPI 入口、~85 個 endpoint
├── database.py          # SQLite schema init + helper（get_cards_by_set 等）
├── marketplace.py       # 盒裝交易 Bid/Ask 訂單簿撮合模組（Phase 2）
├── line_bot.py          # LINE Bot webhook handler
├── auth.py              # 使用者認證 + role-based gating
├── models/              # Pydantic models（card_sets 等）
├── scraper/
│   ├── ebay.py              # eBay 爬蟲（Playwright sync API via ThreadPoolExecutor）
│   ├── snkrdunk_http.py     # SNKR 爬蟲主路徑（httpx async + apparel_id mapping）
│   ├── snkrdunk.py          # SNKR 舊 Selenium 版本（保留 fallback、目前不主用）
│   ├── pokemon_tcg.py       # Pokemon TCG 來源輔助
│   └── tcgcollector.py      # TCGCollector 來源輔助
└── jobs/                # APScheduler 背景任務（HTA 模式預設關閉、手動觸發）

docs/
├── jp_sets_lookup.md         # JP set pg → 中文名 / 日文名 / set_code 對照表（368 set）
└── superpowers/plans/        # 多個 implementation plan 文件

run_api.py               # 啟動 backend 的小 wrapper（含 CARDPOOL_DISABLE_JOBS=1 預設）
requirements.txt
.env.example             # LINE Bot token / BASE_URL 等
Procfile / runtime.txt / nixpacks.toml   # Railway / Render 部署設定
```

---

## 資料模型 — 關鍵表

| 表 | 筆數 | 用途 |
|------|------|------|
| `card_list` | ~50k | 多語言主卡表（card_id PK） |
| `jp_card_list` | 21,552 | **JP 體系主表**（pokemon-card.com 直接爬、含 `prices_synced_at` / `ebay_prices_synced_at` 追蹤欄）|
| `jp_card_list_set` | 368 | JP set 主表（含日文名 + 中文名）|
| `card_prices` | ~107k | **統一價格表**、UNIQUE 鍵 `(set_id, card_number, source, listing_url)` |
| `snkrdunk_mapping` | 232k | SNKR 商品 → 卡片對映、`is_pokemon` flag 標記 |
| `pokemon_dict` | 1,025 | EN/JP/romaji 寶可夢名字典（全國圖鑑覆蓋）|
| `jp_term_dict` | 1,495 | trainer/energy/item JP→EN 字典（Bulbapedia 多源驗證）|
| `en_card_list` / `tw_card_list` | 20k / 11k | EN / TW 體系卡表 |
| `users` / `seller_profiles` / `address_book` | — | MVP 階段認證 + 賣家 KYC + 地址簿 |
| `listings` / `bids` / `trades` | — | 盒裝交易訂單簿（Phase 2 Bid/Ask 撮合）|
| `price_alerts` / `notifications` | — | 到價通知（Phase 1）+ 通知收件夾 |

> 完整 schema spec 跟踩過的坑見 [CLAUDE.md](CLAUDE.md) 「資料模型」段。

---

## 主要 API endpoint 分類

### 卡表 / 搜尋
- `GET /api/cardlist/sets?language=jp|en|tw` — 卡盒系列列表
- `GET /api/cardlist/{set_id}/cards` — 指定 set 的卡片
- `GET /api/search/name/{name}` — 卡名搜尋（中／英／日／編號）
- `GET /api/search/psa/{cert}` — PSA cert 編號查詢

### 價格 sync
- `POST /api/prices/sync/{set_id}/{cn}` — 即時 sync（SNKR + eBay 並行、max_pages=5）
- `POST /api/prices/sync_snkr/{pg}/{cn}` — SNKR-only full-history backfill（max_pages=500、PSA10）
- `POST /api/prices/sync_ebay/{pg}/{cn}` — eBay-only full-history backfill（max_pages=50、PSA10）

### 圖鑑
- `GET /api/category/pokemon/list` — 1,025 隻寶可夢圖鑑
- `GET /api/category/character/list` — 317 個訓練家圖鑑
- `GET /api/category/pokemon/{id}/cards` — 該寶可夢出現過的所有卡
- `GET /api/category/character/{id}/cards` — 該訓練家出現過的所有卡

### 盒裝交易（Phase 2）
- `POST /api/listings/box` — 建立盒裝賣單
- `POST /api/bids/box` — 出價買單
- `GET /api/orderbook/{set_id}/{card_number}/depth` — 訂單簿深度
- `POST /api/alerts/box` — 設定到價通知
- `GET /api/notifications/me` — 通知收件夾

### 認證 / 使用者（MVP）
- `POST /api/auth/register` / `POST /api/auth/login`
- `GET /api/users/me` / `POST /api/users/me/address`
- `POST /api/seller/kyc` — 賣家 KYC 申請

### 其他
- `GET /api/proxy_img?url=...` — 跨域圖檔 CORS proxy（白名單 artofpkm / pokemondb / PokeAPI）
- `POST /webhook` — LINE Bot webhook

完整列表見 http://localhost:8000/docs（Swagger UI）。

---

## 爬蟲設計重點

### eBay（`app/scraper/ebay.py`）

- **架構**：Playwright sync API 跑在 asyncio loop 內、用 ThreadPoolExecutor 隔離（會印 warning 但能跑）
- **Query 規格（PSA-label v2、2026-05-22 起）**：模擬賣家標題格式
  `{release_year} POKEMON JAPANESE {set_code_en} {set_name_en} {rarity_full} {card_name UPPER} PSA {grade}`
  範例：`2025 POKEMON JAPANESE M2 Inferno X SPECIAL ART RARE MEGA CHARIZARD X EX PSA 10`
- **trade-off**：precision 100%（阻擋跨 set 同名同號污染）、recall ~14%（賣家標題不規範會 miss）
- **anti-bot 對應**：
  - playwright-stealth 套件繞 basic detection
  - deep warmup（多訪 Pokemon Individual Cards category 建 session 深度）
  - retry-on-signin（偵測 redirect 到 signin.ebay.com 重 warmup 3 次）
  - 連續長跑 ~5 小時會 silent hang、用 `_resilient_backfill.ps1` 自動偵測 + restart
- **重要 query trigger 警告**：`_sop=13` + `_ipg=240` + `_in_kw=4` 三個 URL param 合起來會被 eBay splashui challenge wall 擋、保留至多 2 個

### SNKRDUNK（`app/scraper/snkrdunk_http.py`）

- **架構**：httpx async（輕量、concurrency=8、不需 browser）
- **流程**：先查 `snkrdunk_mapping` 拿 apparel_id → 直打 `/apparels/{id}/sales-histories?slide=right` 抓 PSA10 成交歷史
- **lookup 規則**：`set_code` 為 Stage 0 第一優先 + **寧缺勿錯**（set_code 給了但 mapping 沒對應就 return None、不要 set_name fallback、避免跨 set 污染）
- **法律注意**：SNKR 利用規約第 7 條第 1 項第 13 號禁止 scraping、現有設計是灰色地帶、私人使用為主、商業化要重評風險

### 翻譯管線（`_translate_jp_card_name_to_en` / `_translate_jp_card_name_to_zh`）

- 7 步管線：HTML strip → 前綴（ロケット団の等）→ 地區形（ガラル等）→ メガ → 後綴（VMAX/VSTAR/GX/EX/ex 等）→ pokemon_dict 查 core → jp_term_dict fallback → 全名 jp_term_dict 兜底
- 字典來源：Bulbapedia `|jname=` 多源驗證（pokemon_dict 1,025 隻 + jp_term_dict 1,495 條 trainer/energy/item）
- 中文翻譯來源優先序：52poke 神奇寶貝百科 > Bulbapedia > 官方 hardcode 兜底

---

## 給接手工程師的閱讀順序

1. **本 README** — 起專案 + 架構總覽
2. **[CLAUDE.md](CLAUDE.md)** — 給 AI 用的 working memory、含完整 schema spec、Known Pitfalls（28+ 條防雷知識）、爬蟲詳細設計、翻譯管線細節
3. **[PROGRESS.md](PROGRESS.md)** — 開發歷程 + 每日工作日誌（最新在最下面）+ Known Pitfalls 完整版（96+ 條）+ 下一步待辦
4. **[docs/jp_sets_lookup.md](docs/jp_sets_lookup.md)** — JP set pg → 中文名 / 日文名 / set_code / 發售日對照表（368 set）
5. **[docs/superpowers/plans/](docs/superpowers/plans/)** — 各功能的 implementation plan 文件

---

## 部署（Railway / Render、目前未上線）

repo 內已有部署設定檔：
- `Procfile` — `web: uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- `runtime.txt` — `python-3.11.0`
- `nixpacks.toml` — chromium 安裝（給 Playwright 用）

部署到 Railway：
1. New Project → Deploy from GitHub repo
2. 設定環境變數：`LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET` / `BASE_URL`
3. 部署完成後取得網址、回 LINE Developers Console 設定 Webhook URL

部署到 Render：流程類似、`render.yaml` 範例見舊版 git history。

> 部署上線會帶 anti-bot 風險（雲端 IP 容易被 eBay / SNKRDUNK 標記、本機 IP 較不會）、且需重新評估 SNKR ToS 風險。**目前以本機開發為主**。

---

## 注意事項

- eBay / SNKRDUNK 爬蟲可能因網站改版失效、anti-bot 偵測有持續升級
- 價格資料僅供參考、不代表實際市場價值
- 請遵守各網站的使用條款（特別是 SNKR ToS）

## 授權

僅供個人使用、非公開散布。
