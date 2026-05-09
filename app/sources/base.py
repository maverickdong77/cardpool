"""Secondary source 抽象層 — Stage 1 骨架。

每個 source（artofpkm / pokellector / manual / _52poke ...）繼承 SecondarySource，
回傳 list[CardRecord]。priority 邏輯在寫入端處理（這檔不負責）。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CardRecord:
    """單張卡的 source-side payload。

    card_number: 該 source 的 card number（例：'1', 'TG1', '756/742'）
    fields: 該 source 提供的欄位值（key 是 'name_jp' / 'name_en' / 'name_zh'
            / 'rarity' / 'image_url' 等；只塞這個 source 真的有的）
    source_meta: source-specific metadata（例：artofpkm seq、image_id），
                 不直接落 DB，由 caller 視需要存在別處
    """
    card_number: str
    fields: dict[str, str] = field(default_factory=dict)
    source_meta: dict = field(default_factory=dict)


class SecondarySource(ABC):
    """每個資料來源的 base class。

    name: source 識別字串（會寫入 card_field_sources.source_name）
    provided_fields: 這個 source 會提供哪些欄位（例：{'name_jp', 'image_url'}）
    """
    name: str
    provided_fields: set[str]

    @abstractmethod
    def fetch_set(
        self,
        source_set_id: str,
        max_cards: Optional[int] = None,
    ) -> list[CardRecord]:
        """抓取單一 set 的所有 CardRecord。

        source_set_id: 該 source 自己的 set id（例：artofpkm 的 581）；
                       對映回我們 card_list.set_id 由 caller 處理。
        max_cards: 採樣模式上限（preview / dry-run 用），None = 全抓。
                   實作端可選擇截 listing 或截 detail，但回傳長度不得超過此值。
        """
        ...
