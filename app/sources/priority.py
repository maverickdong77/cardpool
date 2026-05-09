"""每個欄位的 source 優先順序（小 index = 高優先）。

寫入 card_field_sources 時：若新 source 的 priority 高於現有 source，蓋掉；
否則保留現有值。優先順序在 application 層用，DB 不存。
"""
FIELD_PRIORITY: dict[str, list[str]] = {
    'name_jp':   ['manual', 'artofpkm', 'pokellector'],
    'name_en':   ['manual', 'artofpkm', 'pokellector'],
    'name_zh':   ['manual', '_52poke', 'pokellector'],
}
