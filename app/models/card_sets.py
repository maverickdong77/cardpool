"""
Pokemon TCG 卡片系列資料
包含日文、英文版本對應
"""

# 系列資料結構
# {
#     "id": 系列ID,
#     "name_jp": 日文名稱,
#     "name_en": 英文名稱,
#     "name_zh": 中文名稱,
#     "release_date": 發售日期,
#     "language": 語系 (jp/en/zh),
#     "total_cards": 總卡數,
# }

# 日文系列 (最新到最舊)
JP_SETS = [
    # Mega Evolution 系列
    {
        "id": "M2a",
        "name_jp": "メガドリーム",
        "name_en": "Mega Dream",
        "release_date": "2025-01",
        "language": "jp",
        "total_cards": 234,
    },
    {
        "id": "M2b",
        "name_jp": "メガナイト",
        "name_en": "Mega Night",
        "release_date": "2025-01",
        "language": "jp",
        "total_cards": 234,
    },
    {
        "id": "M1a",
        "name_jp": "メガスパーク",
        "name_en": "Mega Spark",
        "release_date": "2024-10",
        "language": "jp",
        "total_cards": 188,
    },
    # Scarlet & Violet 系列
    {
        "id": "SV8a",
        "name_jp": "超電ブレイカー",
        "name_en": "Terastal Festival ex",
        "release_date": "2024-10",
        "language": "jp",
        "total_cards": 108,
    },
    {
        "id": "SV8",
        "name_jp": "シャイニートレジャーex",
        "name_en": "Shiny Treasure ex",
        "release_date": "2023-12",
        "language": "jp",
        "total_cards": 355,
    },
    {
        "id": "SV7a",
        "name_jp": "パラダイムトリガー",
        "name_en": "Paradigm Trigger",
        "release_date": "2024-07",
        "language": "jp",
        "total_cards": 108,
    },
    {
        "id": "SV7",
        "name_jp": "ステラミラクル",
        "name_en": "Stellar Miracle",
        "release_date": "2024-07",
        "language": "jp",
        "total_cards": 102,
    },
    {
        "id": "SV6a",
        "name_jp": "ナイトワンダラー",
        "name_en": "Night Wanderer",
        "release_date": "2024-06",
        "language": "jp",
        "total_cards": 64,
    },
    {
        "id": "SV6",
        "name_jp": "変幻の仮面",
        "name_en": "Mask of Change",
        "release_date": "2024-04",
        "language": "jp",
        "total_cards": 101,
    },
    {
        "id": "SV5a",
        "name_jp": "クリムゾンヘイズ",
        "name_en": "Crimson Haze",
        "release_date": "2024-03",
        "language": "jp",
        "total_cards": 72,
    },
    {
        "id": "SV5K",
        "name_jp": "ワイルドフォース",
        "name_en": "Wild Force",
        "release_date": "2024-01",
        "language": "jp",
        "total_cards": 71,
    },
    {
        "id": "SV5M",
        "name_jp": "サイバージャッジ",
        "name_en": "Cyber Judge",
        "release_date": "2024-01",
        "language": "jp",
        "total_cards": 71,
    },
    {
        "id": "SV4a",
        "name_jp": "シャイニートレジャーex",
        "name_en": "Shiny Treasure ex",
        "release_date": "2023-12",
        "language": "jp",
        "total_cards": 355,
    },
    {
        "id": "SV4K",
        "name_jp": "古代の咆哮",
        "name_en": "Ancient Roar",
        "release_date": "2023-10",
        "language": "jp",
        "total_cards": 66,
    },
    {
        "id": "SV4M",
        "name_jp": "未来の一閃",
        "name_en": "Future Flash",
        "release_date": "2023-10",
        "language": "jp",
        "total_cards": 66,
    },
    {
        "id": "SV3a",
        "name_jp": "レイジングサーフ",
        "name_en": "Raging Surf",
        "release_date": "2023-09",
        "language": "jp",
        "total_cards": 62,
    },
    {
        "id": "SV3",
        "name_jp": "黒炎の支配者",
        "name_en": "Ruler of the Black Flame",
        "release_date": "2023-07",
        "language": "jp",
        "total_cards": 108,
    },
    {
        "id": "SV2a",
        "name_jp": "ポケモンカード151",
        "name_en": "Pokemon Card 151",
        "release_date": "2023-06",
        "language": "jp",
        "total_cards": 165,
    },
    {
        "id": "SV2P",
        "name_jp": "スノーハザード",
        "name_en": "Snow Hazard",
        "release_date": "2023-04",
        "language": "jp",
        "total_cards": 71,
    },
    {
        "id": "SV2D",
        "name_jp": "クレイバースト",
        "name_en": "Clay Burst",
        "release_date": "2023-04",
        "language": "jp",
        "total_cards": 71,
    },
    {
        "id": "SV1a",
        "name_jp": "トリプレットビート",
        "name_en": "Triplet Beat",
        "release_date": "2023-03",
        "language": "jp",
        "total_cards": 73,
    },
    {
        "id": "SV1S",
        "name_jp": "スカーレットex",
        "name_en": "Scarlet ex",
        "release_date": "2023-01",
        "language": "jp",
        "total_cards": 78,
    },
    {
        "id": "SV1V",
        "name_jp": "バイオレットex",
        "name_en": "Violet ex",
        "release_date": "2023-01",
        "language": "jp",
        "total_cards": 78,
    },
    # 25th Anniversary
    {
        "id": "S8a",
        "name_jp": "25th アニバーサリーコレクション",
        "name_en": "25th Anniversary Collection",
        "release_date": "2021-10",
        "language": "jp",
        "total_cards": 28,
    },
    {
        "id": "S8a-P",
        "name_jp": "25th アニバーサリー プロモ",
        "name_en": "25th Anniversary Promo",
        "release_date": "2021-10",
        "language": "jp",
        "total_cards": 25,
    },
]

# 英文系列
EN_SETS = [
    {
        "id": "MEW",
        "name_en": "151",
        "jp_equivalent": "SV2a",
        "release_date": "2023-09",
        "language": "en",
        "total_cards": 207,
    },
    {
        "id": "PAL",
        "name_en": "Paldea Evolved",
        "jp_equivalent": "SV2P/SV2D",
        "release_date": "2023-06",
        "language": "en",
        "total_cards": 279,
    },
    {
        "id": "SVI",
        "name_en": "Scarlet & Violet",
        "jp_equivalent": "SV1S/SV1V",
        "release_date": "2023-03",
        "language": "en",
        "total_cards": 258,
    },
    {
        "id": "PRE",
        "name_en": "Prismatic Evolutions",
        "release_date": "2025-01",
        "language": "en",
        "total_cards": 180,
    },
    {
        "id": "SSP",
        "name_en": "Surging Sparks",
        "release_date": "2024-11",
        "language": "en",
        "total_cards": 252,
    },
]

# 稀有度對照
RARITY_MAP = {
    # 日文
    "SAR": "Special Art Rare",
    "SR": "Super Rare",
    "AR": "Art Rare",
    "RR": "Double Rare",
    "R": "Rare",
    "U": "Uncommon",
    "C": "Common",
    "UR": "Ultra Rare",
    "HR": "Hyper Rare",
    "S": "Shiny",
    "SSR": "Shiny Super Rare",
    # 英文
    "SIR": "Special Illustration Rare",
    "IR": "Illustration Rare",
    "FA": "Full Art",
}

# 熱門卡片名稱對照 (日文 -> 英文)
CARD_NAME_MAP = {
    "ピカチュウ": "Pikachu",
    "リザードン": "Charizard",
    "ミュウツー": "Mewtwo",
    "ミュウ": "Mew",
    "イーブイ": "Eevee",
    "ゲンガー": "Gengar",
    "カイリュー": "Dragonite",
    "ルギア": "Lugia",
    "レックウザ": "Rayquaza",
    "ギラティナ": "Giratina",
    "アルセウス": "Arceus",
    "パルキア": "Palkia",
    "ディアルガ": "Dialga",
    "リーリエ": "Lillie",
    "マリィ": "Marnie",
    "セレナ": "Serena",
    "カミツレ": "Elesa",
    "ナンジャモ": "Iono",
    "オモダカ": "Geeta",
    "ネモ": "Nemona",
    "カメックス": "Blastoise",
    "フシギバナ": "Venusaur",
}


def get_set_by_id(set_id: str) -> dict | None:
    """根據系列 ID 取得系列資訊"""
    set_id_upper = set_id.upper()
    for s in JP_SETS + EN_SETS:
        if s["id"].upper() == set_id_upper:
            return s
    return None


def get_jp_card_name(en_name: str) -> str | None:
    """英文名稱轉日文"""
    for jp, en in CARD_NAME_MAP.items():
        if en.lower() == en_name.lower():
            return jp
    return None


def get_en_card_name(jp_name: str) -> str | None:
    """日文名稱轉英文"""
    return CARD_NAME_MAP.get(jp_name)


def get_all_jp_sets() -> list:
    """取得所有日文系列"""
    return JP_SETS


def get_all_en_sets() -> list:
    """取得所有英文系列"""
    return EN_SETS
