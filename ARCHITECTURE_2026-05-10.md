# 卡波 Cardpool 資料來源架構（2026-05-10 版）

## 核心決定

以 pokemon-card.com 官方 API 為 JP 卡 source of truth、新建 pokemon_card_jp_official 表承接、artofpkm 降級為 image 索引、既有 card_list 不動、透過 image_id 慢慢 join 補欄位。

## 為什麼

2026-05-09 Stage 1-3 災難根因：artofpkm 是 art-centric 不是 card-centric，用它當 source of truth 寫錯 766 張卡導致 DB corruption。

教訓：source of truth 必須權威。換成 pokemon-card.com 官方 API。

## 5 個 hard data 答案

來源：_analyze_official_vs_db.py 跑出 _analysis_report.txt

Q1: 590 官方 set vs DB 458 jp set name normalized match 只 52 個（11%）
Q2: 官方 cardID 不在 DB image_url（cardID 不能當 join key）
Q3: 官方 image_id 跟 artofpkm image_id 重疊 11,914（76%/73% coverage）← 黃金 join key
Q4: DB 多出 13k 卡集中在大型 promo set，可能是 artofpkm 結構問題
Q5: artofpkm 16,367 筆索引乾淨可用、降級為 image 索引

## 三層架構

Layer 1: pokemon_card_jp_official（新建）
  - source of truth，37,229 rows from official API
  - weekly refresh

Layer 2: artofpkm_cards（既有，保留）
  - image / romaji 索引，16,367 rows
  - read-only after this point

Layer 3: card_list（既有，保留）
  - legacy data，不動
  - 透過 image_id join Layer 1 升級欄位
  - 保留 PSA pop / SNKR price 歷史

## 新表 schema

CREATE TABLE pokemon_card_jp_official (
    cardID          INTEGER PRIMARY KEY,
    pg              TEXT NOT NULL,
    name_jp         TEXT NOT NULL,
    name_alt        TEXT,
    thumb_url       TEXT NOT NULL,
    image_id        TEXT,
    set_code        TEXT,
    romaji_name     TEXT,
    set_name_jp     TEXT,
    source          TEXT DEFAULT 'pokemon-card.com',
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_jp_official_pg ON pokemon_card_jp_official(pg);
CREATE INDEX idx_jp_official_image_id ON pokemon_card_jp_official(image_id);
CREATE INDEX idx_jp_official_set_code ON pokemon_card_jp_official(set_code);

CREATE TABLE pokemon_card_jp_official_set (
    pg              TEXT PRIMARY KEY,
    name_jp         TEXT NOT NULL,
    hit_cnt         INTEGER NOT NULL,
    synced_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

## 4 個 Stage

Stage 1（今晚）: 建表 + 全量 import 37,229 張，不動 card_list
Stage 2（之後）: 手動建 set_pg ↔ db_set_id 對映
Stage 3（之後）: 透過 image_id 升級 card_list 欄位
Stage 4（之後）: UX 切換用新表

## 不做的事

- 不刪 card_list 任何 row
- 不在這次刷新動 artofpkm_cards
- 不做 fuzzy match set 名（11% 通過率不值）
- 不寫 over-engineered abstraction
- 不批次處理 13k 重複

## ground truth dataset

_official_all_sets.json (590 個 set)
_official_all_cards.json (37,229 張卡)
_analysis_report.txt (audit 報告)

## key endpoints

GET https://www.pokemon-card.com/card-search/resultAPI.php?pg={N}&regulation=all&page={M}
GET https://www.pokemon-card.com/card-search/details.php/card/{cardID}/regu/all