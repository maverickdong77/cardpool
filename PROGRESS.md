# Cardpool 查價機器人 — 進度追蹤

> 後續 Claude loop 任務請從「下一步待辦」往下做，做完一項就打勾並補註日期。

---

## 🔥 下一步待辦（從這往下做）

### 0. 等 backfill_snkr 全量跑完（背景進行中）
- 啟動於 2026-04-28 02:21
- 待補 jp-* 卡：**20,769 張**；新 lookup 命中率 ~52% → 預估 **~10,800 張會抓到 SNKR PSA10**
- 速度 0.5-0.8 卡/s，**ETA 6-12 小時**
- 進度看 `backfill_snkr.log`；做完會回到 sync_all + 主 API 雙進程
- /loop 可邊跑邊做下一項，**不要 kill 它**

### 1. 跑完新增卡的翻譯（最優先）
- [x] 2026-04-28 執行 `translate_new_cards.py` — 5,300/5,300 都翻譯成功，2,251 補上 name_jp
- 注意 SQL 有 cross-join bug 報出 2.4M 筆「待翻譯」（實際只 5,300 卡，每張被改多次同樣值），結果無誤
- fuzzy match 對映的 set source=pokellector，不會被誤改 ✓

### 2. 補中文卡名（name_zh）— ✅ 完成（2026-04-28）
- [x] 寫 `scrape_zh_translations.py`（step 1 / step 3 / test 三模式）
- [x] **資料來源**：`/tw/card-search/` 內 `<input class="expansionCode">` + 對應 label，一次抓到所有 128 個 expansionCode 與中文 set 名
- [x] **新表 `zh_set_mapping`**（PK=expansion_code，欄位 name_zh / label_full / mapped_set_id / map_method）
- [x] **新欄位 `card_list.name_zh`**（已 ALTER TABLE）
- [x] **自動對映 64/128**：
  - exact_zh：23 個（card_sets.name_zh 完全比對）
  - snkr_jp_exact / snkr_jp_like / snkr_jp_like_multi：41 個（透過 SNKRDUNK 取 set_name_jp 模糊比對）
- [x] **後端 API**：`search_cards_in_list` 加 `cl.name_zh LIKE` 條件；`/api/prices/{set_id}/{card_number}` 回傳含 `card.name_zh`；分類 cards endpoint 同步加 `cl.name_zh`
- [x] **前端**：`card.js` 標題優先用 name_zh，副標顯示英文+日文；`app.js` modal、`category-detail.html` 同樣
- [x] **跑全部 64 set step 3**：寫入 6,829 筆 name_zh，涵蓋 63 個 jp-* set
- [x] **修正 MC 誤對映**：MC（中文「初階牌組100對戰收藏」是 2026 新套，含莉佳角色卡）被 SNKRDUNK fuzzy 誤對到 jp-Start-Deck-100-Battle-Collection（2022 舊套），清掉 100 筆誤寫入；MC mapped_set_id 設 NULL（jp-* DB 無對應 set）
- [x] **驗證**：搜尋「迷你芙」「皮卡丘」「路卡利歐」全命中；prices API 回傳 name_zh
- 最終結果：**6,729 筆 name_zh / 62 個 set**（總卡 42,924）
- 對不上 64 個 expansion 多為 V 起始牌組/特典卡/AC/AS（VSTAR 與 V-UNION 早期）/朱紫 ex 起始組合，可手動補表

### 2.5 仍可加強
- [ ] 對映失敗 64 個 expansion 中，已有 jp 對應 set 但因 SNKR 沒索引而漏掉的可手動補
- [ ] `card_sets.name_zh` 補齊（目前只有 129 個 jp set 有，可從 zh_set_mapping.name_zh 反向回填）

### 3. 加分類頁面（C 計畫）
- [x] 2026-04-28 後端 4 個 API：
  - `GET /api/category/pokemon/list` — 1,025 隻寶可夢清單
  - `GET /api/category/character/list` — 317 個訓練家清單
  - `GET /api/category/pokemon/{id}/cards` — 該寶可夢出現過的卡（測 Pikachu 25 → 225 張卡 ✓）
  - `GET /api/category/character/{id}/cards`
- [x] 前端 3 頁：`category-pokemon.html` / `category-character.html` / `category-detail.html`
- [x] 路由：`/pokemon`、`/characters`、`/category/pokemon/{id}`、`/category/character/{id}` 全 200
- [x] 首頁加 nav tab 連到分類頁

### 4. 重跑 SNKR mapping 對映新 set
- [x] 2026-04-28 改 `backfill_snkr.py` 加 `--no-ebay-required` 參數
- [x] 2026-04-28 修 `_lookup_apparel_id` 重大 bug：原本 SNKR mapping 解析 title 時，紀念套牌的 set_name_jp 只抓到「」內副標（如「ピカチュウ」），丟掉前面整段 set 全名（如「ポケモンワールドチャンピオンシップス2023横浜 記念デッキ」）。修法：lookup 時加 fallback `full_title LIKE %set_name_jp%`，不用重建 mapping 表
- [x] 2026-04-28 命中率從 0% 提升到 **52%**（抽 50 張新 set 卡測試）
- [x] 2026-04-28 啟動全量 backfill（背景中，見上面「項目 0」）
- 後續 TODO：等 backfill 跑完後抽樣檢查命中品質、可能要再對特殊 set 名做補強對映

### 5. 前端 UX 改善：無資料卡的友善提示（新增）
- [ ] 目前無 SNKR/eBay 紀錄的卡顯示「暫無成交記錄」（line 179），但整個 stats 區塊保留 `-`
- [ ] 建議：加「即時查詢」按鈕觸發 `POST /api/prices/sync/{set_id}/{card_number}`
- [ ] 區分兩種狀態：「冷門卡（市場無流通）」vs「未掃描過（可手動觸發）」
- 影響檔：`static/liff/card.html`、`static/liff/card.js`

### 6. 後續（次優先）
- [ ] PSA Population Report（Pikawu 顯示的 2,891 張那種）— 需另外抓 PSA 官網
- [ ] 補 `card_sets.release_date`（artofpkm 沒有，可能要從 pokellector 補）
- [ ] LINE Webhook 綁定（`.env` 已有憑證）— **用戶說等所有功能到位再做**
- [ ] 部署 Railway / Render — **同上**

### 已知背景進程
- `sync_all.py` PID 17864（自 2026-04-27 16:48 起跑，新版 mapping-first code）
  - 跑 en-* 為主；跑回 jp-* 時會自動命中 SNKR mapping
  - 進度在 sync_all.log，重啟會續跑（看 card_prices）
- 主 API uvicorn PID 26812 (port 8000)

### 啟動方式（重啟用）
```bash
cd "C:\Users\Dong Ying\Desktop\Cardpool Price Searching"
./Python/bin/python.exe sync_all.py                                     # sync_all（背景）
./Python/bin/python.exe -c "import uvicorn; uvicorn.run('app.main:app', port=8000, reload=False)"   # API
./Python/bin/python.exe backfill_snkr.py                                # 補 jp-* SNKR
```

---

## 最新更新（2026-04-28）— artofpkm 整合 + 翻譯字典

### 已完成
- [x] **爬 artofpkm 全 413 set / 16,367 卡** → 暫存 `artofpkm_sets` + `artofpkm_cards`
  - 腳本：`scrape_artofpkm.py`，2 分鐘跑完
  - metadata 補抓：`refresh_artofpkm_meta.py`（412/413 set 抓到 name_jp）
- [x] **fuzzy match 對映 artofpkm ↔ 我們 card_sets**
  - 腳本：`match_artofpkm.py`，結果寫進 `artofpkm_set_match` 表
  - 結果：145 個 set 對到（101 exact + 44 fuzzy），147 個是真新 set
- [x] **驗證關鍵假設：artofpkm 順序 = SNKR/Pokellector 卡號**
  - jp-Alter-Genesis #100 三神（アルセウス&ディアルガ&パルキアGX）在 artofpkm 也是 #100 ✓
- [x] **apply_artofpkm.py：套用變更（已備份）**
  - Phase 1：145 個對映 set → 更新 **10,852 張卡的 image_url**（artofpkm 高解析）
  - Phase 2：新增 **147 個 set / 5,300 張新卡**（卡名暫用羅馬字、name_jp NULL）
  - 備份檔：`cards.db.backup-before-artofpkm-20260427-212752`
- [x] **建翻譯字典 `build_translate_dict.py`**
  - `pokemon_dict`：1,025 隻寶可夢（id, name_en, name_jp, romaji）
  - `character_dict`：317 個訓練家/角色

### 重要狀態
- card_sets：en 182 + jp 355（從 208 → 355，多 147）
- card_list：42,924（從 37,624 → 42,924）
- 16,152 張卡用 artofpkm 高解析圖
- card_prices 44,611 筆全保留、SNKR mapping 22,241 全保留

### 注意事項
- 新增 set 的卡 `name` 還是羅馬字（待 `translate_new_cards.py` 跑完才翻成正常英文名）
- artofpkm slug 化偶有重複 dash bug（如 `jp-Start-Deck-100-Battle-Collection--CoroCiào-Ver`），不影響功能但可優化

---

## 最新更新（2026-04-27）

### 重大發現：SNKRDUNK 搜尋頁改版鎖入
2026-04-23 之後 SNKRDUNK 把 `/search?q=` 改成需登入才可用。未登入打搜尋會 redirect
到 `/accounts/login?nextUrl=/search...`，最終 fallback 到「おすすめアイテム」推薦頁
（固定那 3 件商品）。這就是為什麼整個 sync_all.log 從 4/22 開跑只命中 SNKR 1 次。

驗證細節（已確認）：
- ✅ 商品頁 `/apparels/{id}` 不需登入，能拿到完整資料
- ✅ sales-histories 子頁 `/apparels/{id}/sales-histories?slide=right` 也不需登入
- ❌ 搜尋頁 `/search?q=...` 一律打不開
- ✅ 公開 sitemap：`https://snkrdunk.com/en/sitemap/sitemap-index-en-product-trading-card-single.xml`
  含 8 個子 sitemap、共 232,602 個 trading-card id

### 解決方案：建立 apparel_id 對映表（繞過搜尋）
1. 從 sitemap 拿全部 232,602 個 trading-card id
2. 並行 GET `/apparels/{id}` 主頁，從 `<title>` 解析：
   ```
   ネジキ SR[s11 115/100](拡張パック「ロストアビス」)
        ↓
   set_code=s11, card_number=115, set_name_jp=ロストアビス
   ```
3. 過濾標題含「拡張パック / ハイクラスパック / ポケモン...」的 → 約 15,000 張 Pokemon 卡
4. 存進新表 `snkrdunk_mapping(apparel_id, set_code, card_number, set_name_jp, ...)`
5. SNKR scraper 改成先查 mapping → 找到 apparel_id 就直接打 sales-histories

實測 throughput：~235 req/s（httpx async + 20 concurrency）；全 232k 約 16 分鐘建完。

### 已完成
- [x] **build_snkr_mapping.py**：從 sitemap 建立 apparel_id 對映表
  - 新增 SQLite 表 `snkrdunk_mapping`（PK=apparel_id，欄位含 set_code、card_number、
    card_total、set_name_jp、card_name、full_title、is_pokemon、last_check）
  - 索引：`(set_code, card_number)`、`(set_name_jp, card_number)`、`is_pokemon`
  - 命令：`./Python/bin/python.exe build_snkr_mapping.py --all`
- [x] **改寫 `app/scraper/snkrdunk.py`**：
  - 新增 `_lookup_apparel_id(card_number, set_name_jp, card_name_jp)`
    先用 set_name_jp + card_number 比對；fallback 用 card_name_jp 模糊比對
  - 新增 `_scrape_by_apparel_id_sync(apparel_id, title_hint)`：直接打商品頁、不走搜尋
  - `search_by_card_name` 改成 mapping-first：找到 apparel_id 就直走，找不到才退回搜尋
  - 舊搜尋路徑加保險：`page.title()` 是「おすすめアイテム」就 return []（防 fallback 頁誤命中）
- [x] **backfill_snkr.py**：補已 sync 過但缺 SNKR 的 jp-* 卡
  - 條件：`card_prices` 已有 ebay 紀錄、無 snkrdunk 紀錄、`card_sets.name_jp` 非 NULL
  - 1,120 張待補；命中率測試 8/10（80%）；單卡 ~16 筆 PSA10 紀錄
  - 命令：`./Python/bin/python.exe backfill_snkr.py [--limit N]`

### 端對端驗證（2026-04-27 16:39）
測 10 張 jp-* 卡：寫入 128 筆 SNKR PSA10 紀錄
範例（jp-Alter-Genesis）：
- #100 Arceus Dialga Palkia GX：20 筆，¥138,500–165,000
- #102 Naganadel Guzzlord：20 筆，¥12,000–25,000
- #104 Mega Lopunny Jigglypuff：20 筆，¥38,800–56,000

### 此次背景任務狀態（2026-04-27 16:40）
- `sync_all.py` PID 34560：續跑 en-* 卡（從 [1/29371] 起）
- `build_snkr_mapping.py --all` PID 22180：跑全量 232,602 id；16:40 時進度 57%、ETA 6 分
- `backfill_snkr.py` PID 39328：1,120 卡，0.5 卡/s（mapping 還在跑佔資源）

### 下一步
- 等 mapping 完成 → backfill 應加速到 ~2 卡/s，30-40 分跑完
- backfill 第二輪：第一輪會有 miss（mapping 對映率不到 100%，因 `card_sets.name_jp`
  只有 129/208 系列填了；補 name_jp 後再跑可命中更多）
- sync_all 跑回 jp-* 系列時會自動命中 mapping、寫入 SNKR

---

## 最新更新（2026-04-22 晚）

### 新完成
- [x] **英文卡表全量同步**：182 系列 / 19,874 張卡（149/182 有 logo）
- [x] **搜尋頁語言過濾**：搜尋輸入下方加 `[全部 / 日文 / 英文]` 子分頁
  位置：`static/liff/index.html` `.search-lang-filter`、`app.js` `searchLang` 變數
- [x] **卡片語言徽章**：搜尋結果每張卡左上角顯示「日 / 英」標籤，紅=日 藍=英
- [x] **多語言價格查詢**：
  - `/api/prices/sync/{set_id}/{card_number}` 依 `set_id` 前綴判斷語言
  - 英文卡（`en-*`）跳過 SNKRDUNK，避免抓到日版價格
  - 日文卡（`jp-*`）eBay 搜尋自動加 "Japanese" 關鍵字
  - 回傳含 `language` 欄位
- [x] **詳情頁顯示語言標籤**：`card.js` 在副標題加「【日文版】/【英文版】」

### 啟動注意
- 兩支程式可同時跑：
  - 主 API（uvicorn）：`./Python/bin/python.exe -m app.main`
  - 全量同步：`./Python/bin/python.exe sync_all.py`
- 重啟 uvicorn 不影響 sync_all（不同 PID）

## 最新更新（2026-04-22）

### 已完成
- [x] **SNKRDUNK PSA10 爬蟲修復**：改抓 `/apparels/{id}/sales-histories?slide=right` 子頁
- [x] **eBay 搜尋格式調整**：名稱 + 卡號 + 卡盒系列 + PSA 10 順序
- [x] **清除 115,066 筆舊污染快取**（SNKRDUNK 20 筆乾淨資料保留）
- [x] **API 加查詢時安全網**：讀快取前會檢查 listing_title 是否含卡號 N/T 與卡名關鍵字
- [x] **爬蟲過濾再強化**（2026-04-22 晚）：
  - eBay 必須含 "Pokemon" 字樣，擋掉 Panini/NBA/MLB 卡
  - 系列名要求「全部」主要 token 都命中（不是單 token）
  - 卡號策略：標題有 `\d+/\d+` 時，分子必須等於 N（擋 Landorus 137/086 誤命中 #1）
  - SNKRDUNK：英文卡（無日文 metadata）直接跳過，避免配錯成日文卡
  - 搜尋字串明確加 PSA10 關鍵字
- [x] **全卡價格同步啟動**（`sync_all.py`，2026-04-22 02:53 起跑）
  - 總卡數：37,623；速度 ~5-8 秒/張；預計 2-3 天
  - 斷線可續跑（跳過已在 `card_prices` 的卡）
  - 進度檔：`sync_all.log`

### 伺服器
- 啟動方式：`cd "C:\Users\Dong Ying\Desktop\Cardpool Price Searching" && ./Python/bin/python.exe -m app.main`
- 本機網址：http://localhost:8000/
- Swagger 文件：http://localhost:8000/docs

## 下一步待辦（按優先）

1. **建立英文卡表**
   - 方法比照日文卡表（已用 pokellector 抓完 logo / 分類圖）
   - 英文來源：https://www.pokellector.com/sets
   - 英文版系列/卡片名稱直接翻譯即可（不需查中文官方）

2. **搜尋頁加入中/英/日分頁切換**
   - 位置：`app/main.py` 主頁 / `static/liff/` 前端
   - 功能：切換後顯示對應語言卡表與搜尋結果

3. **多語言價格查詢**
   - 支援輸入：中文 / 英文 / 日文 / 卡片編號 / 任意名稱 + 編號組合
   - 同一張卡會因語言版本不同而有價格差，要分別查詢並標示語言

4. **LINE Webhook 綁定**
   - 已有 `.env` 憑證
   - 待在 LINE Developers 後台設定 Webhook URL

5. **部署到 Railway 或 Render**
   - 已有 `Procfile` / `nixpacks.toml` / `runtime.txt`

## 規格備忘

- **eBay 搜尋格式（顯示時）**：`{英文名稱} [{卡號}] ({系列})` + `PSA {等級}`
  - 實際打到 eBay URL 時去掉括號（eBay 搜尋不吃括號字元）
- **SNKRDUNK PSA10 來源**：`sales-histories?slide=right` 子頁，從 `状態PSA10の売買履歴` 區段到 `状態PSA10の売買相場` 為止
- **匯率**：USD_TO_TWD=32.0；JPY_TO_TWD=0.22（寫死在 scraper）

## 檔案位置提示

- SNKRDUNK 爬蟲：`app/scraper/snkrdunk.py`
- eBay 爬蟲：`app/scraper/ebay.py`
- 驗證腳本：`test_snkr_fix.py`、`test_ebay_fix.py`
