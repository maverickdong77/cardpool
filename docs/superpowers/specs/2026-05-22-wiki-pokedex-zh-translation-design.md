# Design Spec：以 wiki 全國圖鑑為主、跨 set 通用的 JP→ZH 翻譯管線

**日期**：2026-05-22
**作者**：Claude（per user brainstorm）
**狀態**：草案、待 user 審

---

## 1. 背景與動機

### 1.1 現狀

前端 JP 卡中文顯示已在 **set 詳情頁**接通、共 9,757 條翻譯（asia.pokemon-card.com + 52poke wiki TCG 列表 + wiki 卡片級 search 混合來源）。**搜尋頁 + 寶可夢分類頁 + 訓練家分類頁**仍純日文。

### 1.2 之前嘗試的失敗教訓

5/22 凌晨試「wiki override 為主」3 版全部 revert：
- 寬鬆覆蓋：撞「跨 set 同號碼錯位」（pg=738 / pg=861 wiki 編號跟 jp_card_list 編號完全錯位）
- set 級對齊度 % 過濾：pg=861 整體 30% 內、但 #63-69 局部連環錯位漏網
- strict per-card verify：只覆蓋 2 條、結論「wiki vs asia 對寶可夢翻譯本來基本一致」

### 1.3 結構障礙

`card_list`（artofpkm 系統、27,108 jp 卡 / 448 set）跟 `jp_card_list`（pokemon-card.com 官方系統、21,552 卡 / 368 set）是**兩個獨立宇宙**、卡號編號規則不同。試「反填 card_list.name_zh」路徑命中率僅 3.9%（1,054 卡）。

### 1.4 真正策略

從 **per-card 對映** 換成 **per-pokemon 對映**：以 wiki「寶可夢列表（按全國圖鑑編號）」為權威字典（1,025 條 + 變體 ~50）、寫跨 set 通用翻譯函式、3 個 endpoint 統一套用。

---

## 2. 翻譯規則（最終版）

### 2.1 範例對照

| jp 原文 | 中文輸出 | 規則 |
|---|---|---|
| `ピカチュウ` | 皮卡丘 | base 直查 |
| `ピカチュウex` | 皮卡丘ex | base + 後綴連寫 |
| `リザードンVMAX` | 噴火龍VMAX | base + 後綴連寫 |
| `アローラ ディグダ` | 阿羅拉地鼠 | 地區形 + base 連寫 |
| `アローラ ライチュウGX` | 阿羅拉雷丘GX | 地區形 + base + 後綴連寫 |
| `ガラル ヤドン` | 伽勒爾呆呆獸 | 地區形連寫 |
| `ヒスイ ゾロア` | 洗翠索羅亞 | 地區形連寫 |
| `パルデア ケンタロス` | 帕底亞肯泰羅 | 地區形連寫 |
| `メガフシギバナEX` | Mega妙蛙花EX | Mega + base + 後綴連寫 |
| `ロケット団のミュウツー` | 火箭隊的超夢 | 人物的 + base 連寫 |

### 2.2 規則細節

**後綴清單**（保留英文原文、連寫、不空格）：`ex / EX / V / VMAX / VSTAR / VUNION / GMAX / GX`

**地區形 jp → zh**：
- `アローラ` → 阿羅拉
- `ガラル` → 伽勒爾
- `ヒスイ` → 洗翠
- `パルデア` → 帕底亞

**Mega 前綴**：
- `メガ` → Mega

**人物の 前綴**：
- 用 `jp_term_dict.name_zh` 抓「ロケット団 → 火箭隊」這種、加「的」+ base 連寫

---

## 3. 系統架構

```
[權威來源：wiki 全國圖鑑頁]
       https://wiki.52poke.com/wiki/寶可夢列表（按全國圖鑑編號）
       9 個 table、覆蓋 #0001-#1025、含阿羅拉/伽勒爾/洗翠/帕底亞變體
                ↓ 爬蟲（playwright、wiki 擋 WebFetch）
[字典：pokemon_dict.name_zh (1,025)]   ← 寶可夢主體中文字典
[字典：jp_term_dict.name_zh (1,495)]   ← trainer/item 中文字典
       ↑ 補回填（從既有 _jp_zh_translations.json 抽 trainer 條目）
                ↓ runtime
[翻譯函式：_translate_jp_card_name_to_zh()]
       仿 _translate_jp_card_name_to_en、含後綴 + 地區形 + メガ + 人物の
                ↓
[3 個 endpoint 統一套用]
  - set 詳情頁：get_cards_by_set jp 分支
  - 搜尋頁：search_cards_in_list jp 分支
  - 分類頁：category_pokemon_cards / category_character_cards
                ↓
[前端 cardItemHtml] 顯示「日文 (中文)」
```

---

## 4. 元件設計

### 4.1 `pokemon_dict` 新增 `name_zh` 欄位

```sql
ALTER TABLE pokemon_dict ADD COLUMN name_zh TEXT;
-- 同步寫進 app/database.py init_db() 的 CREATE TABLE（Pitfall #1 防 schema 雙寫一致性）
```

### 4.2 `jp_term_dict` 新增 `name_zh` 欄位

```sql
ALTER TABLE jp_term_dict ADD COLUMN name_zh TEXT;
-- 同步寫進 app/database.py init_db() 的 CREATE TABLE
```

### 4.3 新函式 `_translate_jp_card_name_to_zh(jp_name: str) -> Optional[str]`

位置：`app/main.py`（緊鄰 `_translate_jp_card_name_to_en`）

虛擬碼：
```python
def _translate_jp_card_name_to_zh(jp_name: str) -> Optional[str]:
    if not jp_name:
        return None

    # 1. Strip HTML（Bulbapedia megamark span）
    name = re.sub(r'<[^>]+>', '', jp_name).strip()
    is_mega_from_html = '<span class="pcg pcg-megamark"' in jp_name

    # 2. 人物の 前綴（先處理、優先級高）
    char_prefix_zh = None
    m = re.match(r'^(.+?の)(.+)$', name)
    if m:
        char_jp = m.group(1)[:-1]  # 去除「の」
        char_zh = _query_jp_term_dict_zh(char_jp)  # 查 jp_term_dict
        if char_zh:
            char_prefix_zh = char_zh + '的'
            name = m.group(2)

    # 3. メガ 前綴
    is_mega = is_mega_from_html or name.startswith('メガ')
    if name.startswith('メガ'):
        name = name[2:]

    # 4. 地區形前綴
    region_zh = None
    for jp_prefix, zh_prefix in [
        ('アローラ ', '阿羅拉'),
        ('ガラル ', '伽勒爾'),
        ('ヒスイ ', '洗翠'),
        ('パルデア ', '帕底亞'),
    ]:
        if name.startswith(jp_prefix):
            region_zh = zh_prefix
            name = name[len(jp_prefix):]
            break

    # 5. 後綴抽取（保留英文、連寫）
    suffix = ''
    for sfx in ['VMAX', 'VSTAR', 'VUNION', 'GMAX', 'GX', 'EX', 'ex', 'V']:
        if name.endswith(sfx):
            suffix = sfx
            name = name[:-len(sfx)].strip()
            break

    # 6. core 查字典（pokemon_dict.name_zh 優先、miss 查 jp_term_dict.name_zh）
    core_zh = _query_pokemon_dict_zh(name)
    if not core_zh:
        core_zh = _query_jp_term_dict_zh(name)
    if not core_zh:
        return None

    # 7. 組合
    result = ''
    if char_prefix_zh:
        result += char_prefix_zh
    if is_mega:
        result += 'Mega'
    if region_zh:
        result += region_zh
    result += core_zh
    result += suffix

    return result
```

### 4.4 翻譯來源優先順序（user 選擇：新管線優先）

```python
zh = _translate_jp_card_name_to_zh(name_jp) \
     or _JP_ZH_LOOKUP.get(f"{pg}/{cn_normalized}")
```

新管線（per-pokemon）優先、舊字典 `_JP_ZH_LOOKUP`（per-card override）當 fallback。

---

## 5. 工作流程（7 步驟）

### 5.1 爬 wiki 全國圖鑑

**腳本**：`_scrape_wiki_pokedex.py`
**輸出**：`_wiki_pokedex_zh.json`
```json
{
  "1": {"name_jp": "フシギダネ", "name_zh": "妙蛙種子"},
  "2": {"name_jp": "フシギソウ", "name_zh": "妙蛙草"},
  ...
  "1025": {"name_jp": "モモワロウ", "name_zh": "桃歹郎"}
}
```

**做法**：playwright 開 wiki URL、`browser_evaluate` 抽 9 個 `table.eplist` 內的 row、parse 每 row 的編號 / 中文 / 日文（依 sample，cell index：0=編號 / 3=中文 / 4=日文 / 5=英文）。

### 5.2 寫進 pokemon_dict.name_zh

**腳本**：`_apply_wiki_pokedex_zh.py`
**做法**：
1. backup cards.db
2. ALTER TABLE ADD COLUMN (idempotent、檢查欄位存在)
3. UPDATE pokemon_dict SET name_zh=? WHERE id=?
4. 同步更新 `app/database.py:init_db()` CREATE TABLE

### 5.3 回填 jp_term_dict.name_zh

**腳本**：`_apply_jp_term_dict_zh.py`
**做法**：
1. backup cards.db
2. ALTER TABLE ADD COLUMN
3. 從既有 `_jp_zh_translations.json` 抽出歸類 trainer 卡的條目（如「博士の研究」「ボスの指令」「ジニア」「妮莫の特訓」等）
4. 跟 jp_term_dict.name_jp 比對、UPDATE name_zh
5. 對映方式：精確比對 name_jp 字面

### 5.4 寫新翻譯函式 + unit test

**位置**：`app/main.py`（緊鄰 `_translate_jp_card_name_to_en`）
**Unit test**（在 `app/main.py` 內或 `test_translate_zh.py`）：
覆蓋所有 § 2.1 範例對照表 + 邊界 case（空字串、HTML tag、未知寶可夢、後綴衝突等）

### 5.5 改 3 個 endpoint

| 檔案 | 函式 | 修改 |
|---|---|---|
| `app/database.py:get_cards_by_set` jp 分支 | line ~793 | 把 `_JP_ZH_LOOKUP.get()` 改成「先 _translate 再 fallback _JP_ZH_LOOKUP」 |
| `app/database.py:search_cards_in_list` jp 分支 | line ~830 | 新增 _translate fallback（jp 體系 row）|
| `app/main.py:category_pokemon_cards` | line ~2871 | 對 cl.set_id LIKE 'jp-%' 的 row 套 _translate |
| `app/main.py:category_character_cards` | line ~2916 | 同上 |

### 5.6 重啟 API + playwright 驗證

| 頁面 | 驗證點 |
|---|---|
| set 詳情頁 pg=949 / pg=950 / pg=9001 | 既有翻譯不要倒退 |
| 搜尋頁 q=pikachu / ピカチュウ / 皮卡丘 | jp 卡有中文 |
| 寶可夢分類頁 #/category/pokemon/25 (皮卡丘) | jp 卡有中文 |
| 訓練家分類頁 #/category/character/1 | trainer 卡有中文（看 jp_term_dict 回填多少）|

### 5.7 全量盤點 miss + user in-loop 補

**腳本**：`_audit_jp_zh_missing.py`
**做法**：
1. 全量掃 `jp_card_list` (21,552) + `card_list jp-*` (27,108)
2. 對每張卡跑 `_translate_jp_card_name_to_zh(name_jp)`
3. 回 None 的卡進 missing list
4. **累積到 50 個就先停**、輸出 markdown 表
5. 輸出檔：`_jp_zh_missing.md`、欄位：set 中文名 / pg / 卡號 / 日文卡名

**user in-loop 補翻譯**（不在程式自動範圍）：
- user 看 50 個 miss 後、手動更新對應字典（pokemon_dict / jp_term_dict / 或加進 _JP_ZH_LOOKUP per-card override）
- user 過程中會「教 Claude 認卡」、補翻譯規則或邊界 case
- 補完後重跑 audit、再湊下一批 50 個、循環

---

## 6. 錯誤處理

| 情境 | 處理 |
|---|---|
| jp 名查 pokemon_dict.name_zh 是 NULL | 查 jp_term_dict.name_zh |
| jp_term_dict 也沒（純文字題卡 / 罕見變體） | fallback `_JP_ZH_LOOKUP` per-card override |
| 全 miss | 回 None、前端不顯示括號（純日文）、進 audit miss list |
| 地區形前綴有但 base 沒翻譯 | 整張回 None（不顯示「阿羅拉???」）|
| 後綴抽錯 / 異常格式 | regex 比對保守、抽不出整段查、查不到 None |
| HTML tag 殘留 | strip 處理（既有邏輯複用）|

---

## 7. 驗證 / 測試計畫

| 階段 | 驗證方式 |
|---|---|
| § 5.1 完成 | 印 10 條樣本（含 base + 變體 + 最新 #1021-#1025）給 user 看 |
| § 5.2 完成 | `SELECT COUNT(*) FROM pokemon_dict WHERE name_zh IS NULL` 應該很少（只 wiki 漏的）|
| § 5.3 完成 | 抽 20 個 jp_term_dict.name_zh 已填、檢查跟 jp_term_dict.name_jp 對得齊 |
| § 5.4 完成 | unit test 全綠（含 § 2.1 範例 + 邊界 case 15+）|
| § 5.5 完成 | 重啟 API、3 個頁面 playwright spot-check 翻譯正確 |
| § 5.6 完成 | 隨機抽 30 張 jp 卡、人工目測翻譯正確率 ≥ 95% |
| § 5.7 完成 | miss markdown 表存在、user 看完表後給回饋 |

---

## 8. Pitfalls 預防（從 PROGRESS.md Known Pitfalls 抽）

- **§ 5.2 / § 5.3 ALTER TABLE 必須同步寫進 init_db()**（Pitfall #1）
- **改 DB 前 backup cards.db**（CLAUDE.md 慣例）：`cards.db.before-pokedex-zh-YYYYMMDD-HHMMSS`
- **PowerShell `||` parse error**：所有 SQL 寫進 `_*.py` 不要 inline
- **DB 改動腳本以 `_` 開頭命名**（.gitignore 排除、保 local）
- **HTA 模式重啟 API 才生效**：每次改 backend code 都要 kill PID + run_api.py
- **Windows cp950**：Python 印 JP/ZH 必加 `PYTHONIOENCODING=utf-8`
- **wiki 403**：對 wiki.52poke.com 用 playwright `browser_evaluate`、不用 WebFetch

---

## 9. 估時 / 範圍

| 工項 | 估時 |
|---|---|
| 1. 爬 wiki 圖鑑 + 寫 JSON | 30 min |
| 2. apply 進 pokemon_dict | 15 min |
| 3. 回填 jp_term_dict.name_zh | 30 min |
| 4. 寫翻譯函式 + unit test | 1 hr |
| 5. 改 3 個 endpoint | 30 min |
| 6. 重啟 + playwright 驗證 | 30 min |
| 7. miss audit + 輸出 50 條表 | 30 min |
| **總計** | **約 3-4 小時** |

---

## 10. 涵蓋預估

| 範圍 | 涵蓋 |
|---|---|
| 含寶可夢主體 + 用 base 寶可夢命名的 jp 卡 | 大多數（>80% 暫估）|
| Trainer / item 卡（jp_term_dict 有中文的） | 看回填多少 |
| 純文字題卡 / 罕見 trainer / 變體 | fallback `_JP_ZH_LOOKUP` |
| 真實命中率（jp_card_list） | 從 ~45%（per-card lookup） → 預估 **70-85%** |

---

## 11. 後續延伸（不在本 spec 範圍）

- user 看 miss 表後決定下一輪要補哪些字典（手動 in-loop）
- card_list jp-* 體系（artofpkm）的卡有沒有要加 set 對應？這 spec 只解搜尋 / 分類頁顯示、不解 card_list ↔ jp_card_list 結構性對映
- 拓展到 EN 搜尋 / 拼音搜尋（user 用「皮卡丘」「pikachu」「ピカチュウ」「pikatyu」都查得到）

---

## 12. 開放問題（user 看 spec 時決定）

無。所有設計決定已在 brainstorm 過程對齊：
- ✅ 變體形態連寫格式（阿羅拉小拉達）
- ✅ Mega 前綴連寫格式（Mega妙蛙花）
- ✅ 人物の 連寫格式（火箭隊的超夢）
- ✅ 翻譯來源優先順序（新管線優先、舊字典 fallback）
- ✅ miss 後續流程（湊 50 個 → 表 → user 手動補）
