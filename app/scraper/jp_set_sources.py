"""JP 卡盒資料三個來源網站爬蟲。

來源:
  PokemonCardComSource    - pokemon-card.com(主要資料、cardID / name_jp / rarity / image_id)
  ArtOfPkmSource          - artofpkm.com(HD 卡圖、image_url 升級)         [Task 6 實作]
  Bulbapedia52pokeSource  - 52poke wiki(中譯)                              [Task 7 實作]

設計：每個 source 一個 class、共用 retry 邏輯、httpx async。

# 注意事項 (Task 4 spike 發現)

1. **pokemon-card.com 搜尋是 JS-driven 的 XHR**：HTML 沒有 inline 搜尋結果、
   plan 寫的 `expansionCodes?=` regex 根本不存在於頁面。**改用更可靠的方式**:
   `/card-search/index.php` (任何 query 都行) 的 HTML 嵌入了完整的 `pg` 下拉選單、
   含所有官方卡盒的 (pg, canonical_jp_name) mapping、總共 ~81 entries 涵蓋近期所有 set。
   直接 parse 這份 dropdown 比 fuzzy parse 搜尋結果可靠 N 倍。

2. **NFD vs NFC 編碼地雷**：pokemon-card.com 的日文用 NFD (decomposed) 形式、
   例：`ビ` 在 HTML 裡是 `ヒ` (U+30D2) + 濁點 U+3099、而不是 precomposed `ビ` (U+30D3)。
   直接做字串比對會炸 (False negative)。**所有比對前統一 NFC normalize**。

3. **pg 是 pokemon-card.com 內部 set 編號**：跟我們 `jp_card_list_set.pg` 同一個體系、
   可以直接拿來 join、不需要額外轉換。Promo 是 `M-P` / `SV-P` 等字串、非數字。
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Optional

import httpx

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0"

# Embedded pg dropdown pattern in pokemon-card.com card-search HTML
# 範例：{ name: "pg", value: "954", group: "group-item-name", label: "拡張パック「アビスアイ」" }
_PG_ENTRY_RE = re.compile(
    r'\{\s*name:\s*"pg",\s*value:\s*"([^"]+)",\s*group:\s*"[^"]+",\s*label:\s*"([^"]+)"\s*\}'
)


def _nfc(s: str) -> str:
    """Normalize to NFC (precomposed) — 處理 pokemon-card.com 的 NFD 日文。"""
    return unicodedata.normalize("NFC", s)


class PokemonCardComSource:
    """pokemon-card.com 官方來源 — Task 4 提供搜尋頁 set lookup。

    主要 API：
      search_set_by_jp_name(jp_name) — 從嵌入 dropdown 找 (pg, canonical_jp_name)

    用法：
      src = PokemonCardComSource()
      result = await src.search_set_by_jp_name("アビスアイ")
      # → {"pg": "954", "canonical_jp_name": "拡張パック「アビスアイ」", "set_url": "...?pg[]=954"}
    """

    BASE = "https://www.pokemon-card.com"
    SEARCH_PATH = "/card-search/index.php"

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        # cache pg dropdown — 整站共用、一次抓就好
        self._pg_list_cache: Optional[list[tuple[str, str]]] = None

    async def _fetch_pg_list(self) -> list[tuple[str, str]]:
        """抓 card-search 頁、parse 嵌入的 pg dropdown、回 [(pg, canonical_jp_name), ...]。

        Cached per instance。整站 set list 不常變、整個 process lifetime 一份就夠。
        """
        if self._pg_list_cache is not None:
            return self._pg_list_cache

        url = f"{self.BASE}{self.SEARCH_PATH}"
        async with httpx.AsyncClient(
            timeout=self.timeout, headers={"User-Agent": UA}
        ) as client:
            r = await client.get(url)
            if r.status_code != 200:
                raise RuntimeError(
                    f"pokemon-card.com search page returned {r.status_code}"
                )
            html = _nfc(r.text)

        matches = _PG_ENTRY_RE.findall(html)
        # 第一筆通常是「指定なし」(value="")、過濾掉
        result = [(pg, label) for pg, label in matches if pg]
        self._pg_list_cache = result
        return result

    async def search_set_by_jp_name(self, jp_name: str) -> Optional[dict]:
        """從 pokemon-card.com 嵌入 dropdown 找對應卡盒。

        Args:
          jp_name: 日文卡盒名 (e.g. "アビスアイ"、"拡張パック「インフェルノX」")
                   會做 substring NFC match、所以短關鍵字 (e.g. "アビスアイ") 也能命中。

        Returns:
          {"pg": "954",
           "canonical_jp_name": "拡張パック「アビスアイ」",
           "set_url": "https://www.pokemon-card.com/card-search/index.php?pg[]=954"}
          找不到回 None。

        Note:
          set_url 是用 pg filter 的 search URL。直接 httpx 抓會被 CloudFront 503
          (cached error page)、downstream 要用 Playwright 渲染或直接用 pg 自行構造後續查詢。
        """
        if not jp_name:
            return None

        query = _nfc(jp_name).strip()
        if not query:
            return None

        pg_list = await self._fetch_pg_list()

        # 優先順序：
        # 1. 完全相等 (canonical_jp_name == query)
        # 2. 「拡張パック「query」」 / 「強化拡張パック「query」」 / 「ハイクラスパック「query」」 等 wrap
        # 3. label substring match (query in label)
        exact = [(pg, label) for pg, label in pg_list if label == query]
        if exact:
            pg, label = exact[0]
            return self._to_result(pg, label)

        # wrap match — 用「」括住 query
        bracket_query = f"「{query}」"
        bracket = [(pg, label) for pg, label in pg_list if bracket_query in label]
        if bracket:
            pg, label = bracket[0]
            return self._to_result(pg, label)

        # substring fallback (謹慎用、寬鬆 match 容易誤判)
        substr = [(pg, label) for pg, label in pg_list if query in label]
        if substr:
            pg, label = substr[0]
            return self._to_result(pg, label)

        return None

    def _to_result(self, pg: str, label: str) -> dict:
        return {
            "pg": pg,
            "canonical_jp_name": label,
            "set_url": f"{self.BASE}{self.SEARCH_PATH}?pg[]={pg}",
        }


# ========== Task 6 / 7 預留 ==========


class ArtOfPkmSource:
    """artofpkm.com HD 卡圖來源。Task 6 實作。"""

    BASE = "https://www.artofpkm.com"


class Bulbapedia52pokeSource:
    """52poke wiki 中譯來源。Task 7 實作。"""

    BASE = "https://wiki.52poke.com"
