來源：asia.pokemon-card.com/hk-en

endpoint: GET /hk-en/card-search/list/?expansionCodes={CODE}\&pageNo={N}

list 頁只給 cardID、要去 detail 頁拿 name + 豐富 stats

detail 頁含: name, image, set, set\_code, HP, type, attacks, weakness, resistance, retreat, evolution, stage

image URL pattern: /hk-en/card-img/default00024977.png（從 cardID derive）

status: 未建、明天寫 collector



\## 來源探勘狀況



| 站 | URL | 反爬 | list 頁 | detail 頁 | 狀態 |

|---|---|---|---|---|---|

| JP | pokemon-card.com | 無 | API JSON | SSR | ✅ 已 import |

| TW | asia.pokemon-card.com/tw | 無 (Apache) | SSR HTML 含 cardID + image | 待驗證 | ✅ 結構驗證 |

| HK-EN | asia.pokemon-card.com/hk-en | 無 (Apache) | SSR HTML 含 cardID only | SSR HTML 豐富 | ✅ 結構驗證 |



\## 跨語對映（Phase 2）



不今晚做、不明天做。



未來計畫：

&#x20; - 三個表都算 image\_phash

&#x20; - 同 phash 表示同 artwork、可能是同卡跨語版本

&#x20; - hamming distance threshold（如 ≤ 3）找候選

&#x20; - 用 Pokedex No. + rarity + illustrator 多重訊號驗證

&#x20; - 建 card\_translation\_link 表存對映

&#x20;   

複雜度：高、需要實驗

優先度：低、MVP 階段使用者可手動切語言查

原則：對映只能 card level、不能 set level（因 EN 跟 JP/TW set 結構不對應）



\## 不做的事



\- 不從舊 card\_list 遷移 PSA / SNKR 資料（之後重抓）

\- 不今晚實作跨語對映（Phase 2）

\- 不爬 detail page 補 rarity / illustrator（之後）

\- 不刪舊 card\_list / artofpkm\_cards（保留為 legacy）

\- 不嘗試 reconcile 三來源 set 卡數差異（各信各的）



\## 實作順序



\### Day 2026-05-11（明天）

1\. ALTER pokemon\_card\_jp\_official RENAME TO jp\_card\_list

2\. 加 phash / rarity / illustrator / hp 欄位

3\. 寫 EN collector（hk-en 站、list + detail）

4\. 寫 TW collector（tw 站、list 直接拿 cardID + image、name 走 detail）

5\. 跑試水（一個 set 驗證資料品質）



\### Day 2026-05-12+

1\. 評估是否需要全量爬 detail（rarity / illustrator）

2\. EN 全量 import

3\. TW 全量 import



\### Day 2026-05-13+

1\. phash 計算（jp / en / tw）

2\. 跨語對映實驗

3\. 重抓 PSA pop / SNKR price



\## Resources



\- ground truth datasets:

&#x20; - \_official\_all\_sets.json (590 jp set)

&#x20; - \_official\_all\_cards.json (37,229 jp card)

&#x20; 

\- existing tables:

&#x20; - pokemon\_card\_jp\_official 20,557 row（明天 rename）

&#x20; - pokemon\_card\_jp\_official\_set 590 row

&#x20; - artofpkm\_cards 16,367 row（legacy）

&#x20; - card\_list 50,695 row（legacy）



\- key endpoints:

&#x20; - JP: https://www.pokemon-card.com/card-search/resultAPI.php

&#x20; - TW: https://asia.pokemon-card.com/tw/card-search/list/?expansionCodes={CODE}

&#x20; - HK-EN: https://asia.pokemon-card.com/hk-en/card-search/list/?expansionCodes={CODE}



\- HK-EN expansion codes seen: ME01, ME02, ME03, ME2, MEP, RSV10, SV03, SV04, SV05, SV06, RSV10.5

\- TW expansion codes seen: M1L, M1S, M2, M3, M4, MBD, MBG, MC, MJ, SV10

