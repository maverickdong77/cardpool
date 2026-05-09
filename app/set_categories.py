"""
日文卡片系列分類
按照 Pokellector 的順序排列
"""

# 分類圖標 (從 Pokellector 取得)
CATEGORY_LOGOS = {
    "mega": "https://den-media.pokellector.com/logos/Mega-Series.logo.418.png",
    "sv": "https://den-media.pokellector.com/logos/Scarlet-Violet.logo.359.png",
    "swsh": "https://den-media.pokellector.com/logos/Sword-Shield.logo.282.png",
    "sm": "https://den-media.pokellector.com/logos/Sun-Moon.logo.199.png",
    "xy": "https://den-media.pokellector.com/logos/Pokemon-XY.logo.132.png",
    "bw": "https://den-media.pokellector.com/logos/Black-White.logo.13.png",
    "bw-promos": "https://den-media.pokellector.com/logos/Black-White-Promos.logo.25.png",
    "legend": "https://den-media.pokellector.com/logos/Legend.logo.246.png",
    "dpt": "https://den-media.pokellector.com/logos/DPt.logo.252.png",
    "ppp": "https://den-media.pokellector.com/logos/PPP-Promos.logo.128.png",
    "dp": "https://den-media.pokellector.com/logos/DP-Era.logo.273.png",
    "ecard": "https://den-media.pokellector.com/logos/e-Card-Era.logo.389.png",
    "vs": "https://den-media.pokellector.com/logos/Pokemon-VS.logo.276.png",
    "neo": "https://den-media.pokellector.com/logos/Neo.logo.323.png",
    "original": "https://den-media.pokellector.com/logos/Original.logo.312.png",
    "vending": "https://den-media.pokellector.com/logos/Vending.logo.394.png",
    "other": "",
}

# 系列分類 (按順序)
SET_CATEGORIES = [
    {
        "id": "mega",
        "name": "Mega Series",
        "name_zh": "超級進化系列",
        "logo": CATEGORY_LOGOS["mega"],
        "sets": [
            "Ninja-Spinner",
            "Munikis-Zero",
            "MEGA-Dream-ex",
            "Inferno-X",
            "Mega-Brave",
            "Mega-Symphonia",
            "Mega-Series-Promos",
            "MEGA-Promos",
            "Premium-Trainer-Box-MEGA",
            "Starter-Set-MEGA-Mega-Gengar-ex",
            "Starter-Set-MEGA-Mega-Diancie-ex",
            "Starter-Set-ex-Marnies-Morpeko-Grimmsnarl-ex",
            "Starter-Set-ex-Stevens-Beldum-Metagross-ex",
            "Start-Deck-100-Battle-Collection",         # 2026 莉佳 Erika 起始包
            "Start-Deck-100-Battle-Collection--CoroCiào-Ver",
        ]
    },
    {
        "id": "sv",
        "name": "Scarlet & Violet Series",
        "name_zh": "朱紫系列",
        "logo": CATEGORY_LOGOS["sv"],
        "sets": [
            "White-Flare",
            "Black-Bolt",
            "Glory-of-Team-Rocket",
            "Hot-Air-Arena",
            "Hot-Wind-Arena",                  # artofpkm 別名
            "Battle-Partners",
            "Terastal-Festival-ex",
            "Super-Electric-Breaker",
            "Paradise-Dragona",
            "Stella-Miracle",
            "Night-Wanderer",
            "Mask-of-Change",
            "Crimson-Haze",
            "Cyber-Judge",
            "Wild-Force",
            "Shiny-Treasure-ex",
            "Shiny-Treasures-ex",
            "Future-Flash",
            "Ancient-Roar",
            "Raging-Surf",
            "Ragins-Surf",
            "Ruler-of-the-Black-Flame",
            "Pokemon-151",
            "Snow-Hazard",
            "Clay-Burst",
            "Triplet-Beat",
            "Triple-Beat",
            "Violet-ex",
            "Scarlet-ex",
            "Scarlet-Violet-Japanese-Promos",
            # 起始牌組 / 構築盒 / 戰鬥組
            "ex-Start-Decks",
            "ex-Special-Set",
            "Battle-Academy",
            "Pokémon-Card-Game-Classic",
            "Extra-Regulation-Box",
            "Starter-Deck-Build-Set-Ancient-Koraidon-ex",
            "Starter-Deck-Build-Set-Future-Miraidon-ex",
            "Special-Deck-Set-ex-Venusaur-Charizard-Blastoise",
            "Deck-Build-Box-Battle-Partners",
            "Deck-Build-Box-Stellar-Miracle",
            "Deck-Build-Box-Ruler-of-the-Black-Flame",
            "Scarlet-Violet-Starter-set-ex-Pikachu-ex-Pawmot",
            "Scarlet-Violet-Starter-set-ex-Sprigatito-Lucario-ex",
            "Scarlet-Violet-Starter-set-ex-Quaxly-Mimikyu-ex",
            "Scarlet-Violet-Starter-set-ex-Fuecoco-Ampharos-ex",
            "Starter-Set-Tera-Mewtwo-ex",
            "Starter-Set-Tera-Skeledirge-ex",
            "Starter-Set-Tera-Type-Stellar-Ceruledge-ex",
            "Starter-Set-Tera-Type-Stellar-Sylveon-ex",
            "Battle-Master-Deck-Terastal-Charizard-ex",
            "Battle-Master-Deck-Chien-Pao-ex",
            "Premium-Trainer-Box-ex",
            "Start-Deck-Generations",
        ]
    },
    {
        "id": "swsh",
        "name": "Sword & Shield Series",
        "name_zh": "劍盾系列",
        "logo": CATEGORY_LOGOS["swsh"],
        "sets": [
            "VSTAR-Universe",
            "Paradigm-Trigger",
            "Incandescent-Arcana",
            "Lost-Abyss",
            "Pokemon-GO",
            "Japanese-Pokemon-GO",
            "Dark-Phantasma",
            "Time-Gazer",
            "Space-Juggler",
            "Battle-Region",
            "Star-Birth",
            "Start-Deck-100",
            "Start-Deck-100-Corocoro-Version",
            # 注意：Start-Deck-100-Battle-Collection 是 2026 Mega 期的莉佳 Erika 系列、不是 SwSh 期
            # 已移到 mega 分類
            "VMAX-Climax",
            "25th-Anniversary-Promo-Pack",
            "25th-Anniversary-Collection",
            "25th-Anniversary-Golden-Box",
            "Fusion-ARTS",
            "Blue-Sky-Stream",
            "Towering-Perfection",
            "Skyscraping-Perfect",                     # artofpkm 別名（摩天巔峰）
            "Eevee-Heroes",
            "Jet-Black-Spirit",
            "Jet-Black-Poltergeist",                   # artofpkm 別名（漆黑幽魂）
            "Silver-Lance",
            "Matchless-Fighter",
            "Matchless-Fighters",                      # artofpkm 別名（雙璧戰士）
            "Rapid-Strike-Master",
            "Single-Strike-Master",
            "Shiny-Star-V",
            "Electrifying-Tackle",
            "Legendary-Pulse",
            "Infinity-Zone",
            "Explosive-Flame-Walker",
            "Rebellion-Crash",
            "VMAX-Rising",
            "Sword",
            "Shield",
            "Japanese-Sword-Shield-Promos",
            "Eevee-Heroes-VMAX-Special-Set",
            "Silver-Lance-Jet-Black-Spirit-Promo-Set",
            "VMAX-Special-Set",
            # 起始牌組 / Premium Box
            "Starter-Set-V",
            "V-Start-Deck",
            "Family-Pokémon-Card-Game-Sword-Shield",
            "Premium-Trainer-Box-2019",
            "Premium-Trainer-Box--Rapid-Strike-Single-Strike",
            "Premium-Trainer-Box--VSTAR",
            "Special-Deck-Set-Zacian-Zamazenta-VS-Eternatus",
            "Special-Deck-Set-Charizard-VSTAR-vs-Rayquaza-VMAX",
            "High-Class-Deck-Inteleon-VMAX",
            "High-Class-Deck-Gengar-VMAX",
            "Starter-Set-VMAX-Charizard",
            "Starter-Set-VMAX-Venusaur",
            "Starter-Set-VMAX-Blastoise",
            "Starter-Set-VMAX-Grimmsnarl",
            "Starter-Set-VSTAR-Lucario",
            "Starter-Set-VSTAR-Darkrai",
            "VSTAR-VMAX-High-Class-Deck-Deoxys",
            "VSTAR-VMAX-High-Class-Deck-Zeraora",
            "VSTAR-Special-Set",
            "VMAX-Special-Set-Eevee-Heroes",
            "Special-Set-VUNION",
            "Zacian-Zamazenta-Box",
            "Pokémon-World-Championship-2023-Yokohama-Memorial-Deck-Pikachu",
            "Jumbo-Pack-Set--Silver-Lance-Jet-Black-Poltergeist",
            "World-Champions-Pack",
        ]
    },
    {
        "id": "sm",
        "name": "Sun & Moon Series",
        "name_zh": "太陽月亮系列",
        "logo": CATEGORY_LOGOS["sm"],
        "sets": [
            "Tag-Team-GX-All-Stars",
            "Alter-Genesis",
            "Dream-League",
            "Remix-Bout",
            "Miracle-Twins",
            "Sky-Legend",
            "Detective-Pikachu",
            "Japanese-Detective-Pikachu",
            "GG-End",
            "Double-Blaze",
            "Full-Metal-Wall",
            "Night-Unison",
            "Tag-Bolt",
            "Ultra-Shiny-GX",
            "Dark-Order",
            "Explosive-Impact",
            "Fairy-Rise",
            "Thunderclap-Spark",
            "Charisma-of-the-Cracked-Sky",
            "Forbidden-Light",
            "Japanese-Forbidden-Light",
            "Ultra-Force",
            "Ultra-Sun",
            "Ultra-Moon",
            "GX-Battle-Boost",
            "The-Transdimensional-Beast",
            "Ultra-Dimensional-Beast",                 # artofpkm 別名
            "The-Awoken-Hero",
            "Strengthening-Expansion-Shining-Legends",
            "Shining-Legend",                          # artofpkm 別名
            "Light-Consuming-Darkness",
            "Light-Devouring-Darkness",                # artofpkm 別名
            "Seen-the-Rainbow-Battle",
            "Did-You-See-the-Fighting-Rainbow",        # artofpkm 別名
            "Strengthening-Expansion-Pack-Beyond-A-New-Challenge",
            "Strengthening-Expansion-Pack-Beyond-A-New-Challeng",
            "Beyond-A-New-Challenge",                  # artofpkm 別名
            "Alola-Moonlight",
            "Islands-Awaiting-You",
            "Sun-Moon-Strengthening-Expansion",
            "Collection-Sun",
            "Collection-Moon",
            "Sun-Moon",                                # artofpkm 雙合本
            "Japanese-Sun-Moon-Promos",
            "Champion-Road",
            "Dragon-Storm",
            "Ash-vs-Team-Rocket-Battle-Set",
            "30-Card-Deck-Match-Set-Ash-vs-Team-Rocket",
            "Pikachu-New-Friends",
            "Pikachu-and-their-New-Friends",           # artofpkm 別名
            "Tapu-Bulu-GX-Enhanced-Starter-Set",
            "Starter-Set-Tapu-Bulu-GX",
            "Rockruff-Full-Power-Deck",
            "Corocoro-Rockruff-Full-Power-Deck",
            "Starter-Set-Decks",
            "Premium-Trainer-Box",
            # 起始牌組 / 構築盒
            "Starter-Deck-GX",
            "Starter-Set-GX",
            "Starter-Set-GX-Series",
            "Starter-Set-Legend-Solgaleo-GX-Lunala-GX",
            "Starter-Set-Tag-Team-GX-Darkrai-Umbreon-GX-Espeon-Deoxys-GX",
            "Premium-Trainer-Box--Tag-Team-GX",
            "Premium-Trainer-Box-Ultra-Sun-Ultra-Moon",
            "Deck-Build-Box-Ultra-Sun-Ultra-Moon",
            "Deck-Build-Box--Tag-Team-GX",
            "Family-Pokémon-Card-Game-Sun-Moon",
        ]
    },
    {
        "id": "xy",
        "name": "XY Series",
        "name_zh": "XY系列",
        "logo": CATEGORY_LOGOS["xy"],
        "sets": [
            "The-Best-of-XY",
            "20th-Anniversary-Collection",
            "20th-Anniversary",                        # artofpkm 別名
            "Mythical-Legendary-Dream-Holo-Collection",
            "Explosive-Warrior",
            "Ruthless-Rebel",
            "Premium-Champion-Pack-EX-M-BREAK",
            "Premium-Champion-Pack-EX-x-M-x-BREAK",
            "Mega-Audino-EX-Mega-Battle-Deck",
            "Mega-Battle-Deck-M-Audino-EX",            # artofpkm 別名
            "Awakening-of-Psychic-Kings",
            "Awakening-Psychic-King",                  # artofpkm 別名
            "Zygarde-EX-Perfect-Battle-Deck",
            "Perfect-Battle-Deck-Zygarde-EX",          # artofpkm 別名
            "Pokemon-Card-Game-Starter-Pack",
            "Starter-Pack",                            # artofpkm 別名
            "Pokekyun-Collection",
            "Rage-of-the-Broken-Sky",
            "Golduck-BREAK-Palkia-EX-Combo-Deck",
            "Combo-Deck-Golduck-BREAK-Palkia-EX",      # artofpkm 別名
            "Noivern-BREAK-Evolution-Pack",
            "BREAK-Evolution-Pack-Noivern-BREAK",      # artofpkm 別名
            "Raichu-BREAK-Evolution-Pack",
            "BREAK-Evolution-Pack-Raichu-BREAK",       # artofpkm 別名
            "Blue-Impact",
            "Red-Flash",
            "Legendary-Holo-Collection",
            "Bandit-Ring",
            "Mega-Rayquaza-EX-Battle-Deck",
            "Battle-Deck-60-Mega-Rayquaza-EX",         # artofpkm 別名
            "Emerald-Break",
            "Magma-Gang-vs-Aqua-Gang-Double-Crisis",
            "Team-Magma-vs-Team-Aqua-Double-Crisis",   # artofpkm 別名
            "Gaia-Volcano",
            "Tidal-Storm",
            "Hyper-Metal-Chain-Deck",
            "Hyper-Metal-Chain-Deck-Dialga-EX-Aegislash-EX",
            "Phantom-Gate",
            "Rising-Fist",
            "M-Charizard-EX-Mega-Battle-Deck",
            "Mega-Battle-Deck-M-Charizard-EX",         # artofpkm 別名
            "Wild-Blaze",
            "Xerneas-Half-Deck",
            "Yveltal-Half-Deck",
            "Collection-Y",
            "Collection-X",
            "XY-Beginning-Set",
            "Japanese-XY-Promos",
            "M-Master-Deck-Build-Box-Power-Style",
            "M-Master-Deck-Build-Box-Speed-Style",
            "Super-Legend-Set-Xerneas-EX-Yveltal-EX",
            "Trainer-Battle-Deck--Brock-of-Pewter-City-Gym-Misty-of-Cerulean-City-Gym",
        ]
    },
    {
        "id": "bw",
        "name": "Black & White Series",
        "name_zh": "黑白系列",
        "logo": CATEGORY_LOGOS["bw"],
        "sets": [
            "EX-Battle-Boost",
            "Megalo-Cannon",
            "Thunder-Knuckle",
            "Spiral-Force",
            "Plasma-Gale",
            "Freeze-Bolt",
            "Cold-Flare",
            "Dragon-Blade",
            "Dragon-Blast",
            "Dark-Rush",
            "Hail-Blizzard",
            "Psycho-Drive",
            "Red-Collection",
            "White-Collection",
            "Black-Collection",
            # 起始/戰鬥牌組
            "BW-P-Promotional-cards",
            "Beginning-Set-DX-Pikachu-Version",
            "National-Beginning-Set",
            "Entry-Pack-08",
            "Master-Deck-Build-Box",
            "Battle-Strength-Deck-Cobalion",
            "Battle-Strength-Deck-Terrakion",
            "Battle-Strength-Deck-Virizion",
            "Battle-Strength-Deck-Zekrom-EX",
            "Battle-Strength-Deck-Reshiram-EX",
            "Battle-Strength-Deck-Keldeo",
            "Battle-Strength-Deck-Black-Kyurem-EX",
            "Battle-Strength-Deck-White-Kyurem-EX",
            "Battle-Theme-Deck-Victini",
            "Battle-Gift-Set-Thundurus-VS-Tornadus",
            "Team-Plasmas-Powered-Half-Deck",
            "Tournament-Starter-Set-30-Emboar-EX-vs-Togekiss-EX",
            "30-Card-Battle-Deck-Set-Mewtwo-VS-Genesect",
            "Everyones-Exciting-Battle",
            "Journey-Partners",
            "Intense-Fight-in-the-Destroyed-Sky",
        ]
    },
    {
        "id": "bw-promos",
        "name": "Black & White Promos Series",
        "name_zh": "黑白特典系列",
        "logo": CATEGORY_LOGOS["bw-promos"],
        "sets": [
            "Mewtwo-Vs-Genesect-Genesect",
            "Mewtwo-Vs-Genesect-Mewtwo",
            "Blastoise-Kyurem-Combo-Deck",
            "Shiny-Collection",
            "Exciting-Battle-for-Everyone",
            "Team-Plasma-Battle-Gift-Set",
            "Keldeo-Battle-Strength-Deck",
            "Garchomp-Half-Deck",
            "Hydreigon-Half-Deck",
            "Dragon-Selection",
            "Japanese-Black-White-Promos",
        ]
    },
    {
        "id": "legend",
        "name": "Legend Series",
        "name_zh": "傳說系列",
        "logo": CATEGORY_LOGOS["legend"],
        "sets": [
            "Clash-at-the-Summit",
            "Reviving-Legends",
            "HeartGold-Collection",
            "SoulSilver-Collection",
            "Lost-Link",
            "Legend-Promos",
        ]
    },
    {
        "id": "dpt",
        "name": "DPt Series",
        "name_zh": "DPt系列",
        "logo": CATEGORY_LOGOS["dpt"],
        "sets": [
            "Advent-of-Arceus",
            "Shaymin-LV-X-Collection-Pack",
            "Shaymin-LVX-Collection-Pack",
            "Beat-of-the-Frontier",
            "Bonds-to-the-End-of-Time",
            "Galactics-Conquest",
            "DPt-Promos",
            "DPt-P-Promotional-cards",
            "Entry-Pack-DPt",
            "Gift-Box-DPt",
            "Battle-Starter-Pack-Infernape-vs-Gallade",
            "Battle-Starter-Pack-Garchomp-vs-Charizard",
            "Battle-Starter-Pack-Heatran-vs-Regigigas",
            "Battle-Starter-Pack-Giratina-vs-Dialga",
            "2009-Movie-Commemoration-Random-Pack",
            "Pokémon-Battle-Tour-09-Challenge-Deck",
            "Melee-Pokémon-Scramble-x-Pokémon-Card-Game",
        ]
    },
    {
        "id": "ppp",
        "name": "PPP Promos Series",
        "name_zh": "PPP特典系列",
        "logo": CATEGORY_LOGOS["ppp"],
        "sets": [
            "PPP-Promos",
        ]
    },
    {
        "id": "dp",
        "name": "DP Era Series",
        "name_zh": "DP系列",
        "logo": CATEGORY_LOGOS["dp"],
        "sets": [
            "DP-Promos",
            "DP-P-Promotional-Cards",
            "Cry-from-the-Mysterious",
            "Cries-of-Secrecy",                        # artofpkm 別名
            "Temple-of-Anger",
            "Temple-of-Wrath",                         # artofpkm 別名
            "Dawn-Dash",
            "Moonlit-Pursuit",
            "Shining-Darkness",
            "Secret-of-the-Lakes",
            "Secret-of-the-Lake",                      # artofpkm 別名
            "Space-Time-Creation",
            # 起始牌組 / 戰鬥組
            "Battle-Starter-Pack-Magmortar-vs-Electivire",
            "Battle-Starter-Deck-Torterra",
            "Battle-Starter-Deck-Magmortar",
            "Battle-Starter-Deck-Blastoise",
            "Battle-Starter-Deck-Raichu",
            "Constructed-Standard-Deck-Tyranitar",
            "Constructed-Standard-Deck-Steelix",
            "Constructed-Standard-Deck-Palkia-LVX",
            "Constructed-Half-Deck-Rampardos-the-Attacker",
            "Leafeon-vs-Metagross-Expert-Deck-Online",
        ]
    },
    {
        "id": "ecard",
        "name": "e-Card Era Series",
        "name_zh": "e-Card系列",
        "logo": CATEGORY_LOGOS["ecard"],
        "sets": [
            "The-Town-on-No-Map",
            "Wind-from-the-Sea",
            "Split-Earth",
            "Mysterious-Mountains",
        ]
    },
    {
        "id": "vs",
        "name": "Pokemon VS Series",
        "name_zh": "Pokemon VS系列",
        "logo": CATEGORY_LOGOS["vs"],
        "sets": [
            "Pokemon-VS",
        ]
    },
    {
        "id": "neo",
        "name": "Neo Series",
        "name_zh": "Neo系列",
        "logo": CATEGORY_LOGOS["neo"],
        "sets": [
            "Darkness-and-to-Light",
            "Awakening-Legends",
            "Crossing-the-Ruins",
            "Gold-Silver-to-a-New-World",
        ]
    },
    {
        "id": "original",
        "name": "Original Series",
        "name_zh": "初代系列",
        "logo": CATEGORY_LOGOS["original"],
        "sets": [
            "Challenge-from-the-Darkness",
            "Leaders-Stadium",
            "Rocket-Gang",
            "Mystery-of-the-Fossils",
            "Pokemon-Jungle",
            "Expansion-Pack",
        ]
    },
    {
        "id": "vending",
        "name": "Vending Series",
        "name_zh": "販賣機系列",
        "logo": CATEGORY_LOGOS["vending"],
        "sets": [
            "Vending-Series-3-Green",
            "Vending-Series-2-Red",
            "Vending-Series-1-Blue",
            "Vending-Series-Blue",
        ]
    },
    {
        "id": "other",
        "name": "Other / Promos",
        "name_zh": "其他 / 特典",
        "logo": CATEGORY_LOGOS["other"],
        "sets": []
    },
]


EN_SET_CATEGORIES = [
    {
        "id": "en-sv", "name": "Scarlet & Violet Era", "name_zh": "朱紫系列",
        "logo": CATEGORY_LOGOS["sv"],
        "sets": [
            "Destined-Rivals", "Journey-Together", "Prismatic-Evolutions",
            "Surging-Sparks", "Stellar-Crown", "Shrouded-Fable",
            "Twilight-Masquerade", "Temporal-Forces", "Paldean-Fates",
            "Paradox-Rift", "Obsidian-Flames", "Scarlet-Violet-151",
            "Paldea-Evolved", "Scarlet-Violet-English",
            "Scarlet-Violet-Energies", "Scarlet-Violet-English-Promos",
            "White-Flare-EN", "Black-Bolt-EN",
        ],
    },
    {
        "id": "en-swsh", "name": "Sword & Shield Era", "name_zh": "劍盾系列",
        "logo": CATEGORY_LOGOS["swsh"],
        "sets": [
            "Crown-Zenith", "Silver-Tempest", "Lost-Origin",
            "English-Pokemon-Go", "Astral-Radiance", "Brilliant-Stars",
            "Fusion-Strike", "Celebrations", "Evolving-Skies",
            "Chilling-Reign", "Battle-Styles", "Shining-Fates",
            "Vivid-Voltage", "Champions-Path", "Darkness-Ablaze",
            "Rebel-Clash", "English-Sword-Shield", "English-Sword-Shield-Promos",
        ],
    },
    {
        "id": "en-sm", "name": "Sun & Moon Era", "name_zh": "太陽月亮系列",
        "logo": CATEGORY_LOGOS["sm"],
        "sets": [
            "Cosmic-Eclipse", "HiddFates", "Unified-Minds", "Unbrok-Bonds",
            "UnbrokBonds", "English-Detective-Pikachu", "Team-Up",
            "Lost-Thunder", "Dragon-Majesty", "Celestial-Storm",
            "English-ForbiddLight", "Ultra-Prism", "Crimson-Invasion",
            "Shining-Legends", "Burning-Shadows", "Guardians-Rising",
            "English-Sun-Moon", "English-Sun-Moon-Promos",
        ],
    },
    {
        "id": "en-xy", "name": "XY Era", "name_zh": "XY系列",
        "logo": CATEGORY_LOGOS["xy"],
        "sets": [
            "Evolutions", "Steam-Siege", "Fates-Collide", "Generations",
            "BREAKPoint", "XY-BREAKthrough", "Ancient-Origins", "Roaring-Skies",
            "Double-Crisis", "Primal-Clash", "Phantom-Forces", "Furious-Fists",
            "XY-Flashfire", "XY", "Kalos-Starter-Set", "English-XY-Promos",
            "Radiant-Collection", "Dragon-Vault",
        ],
    },
    {
        "id": "en-bw", "name": "Black & White Era", "name_zh": "黑白系列",
        "logo": CATEGORY_LOGOS["bw"],
        "sets": [
            "Legendary-Treasures", "Plasma-Blast", "Plasma-Freeze",
            "Plasma-Storm", "Boundaries-Crossed", "Dragons-Exalted",
            "Dark-Explorers", "Next-Destinies", "Noble-Victories",
            "Emerging-Powers", "English-Black-White", "English-Black-White-Promos",
        ],
    },
    {
        "id": "en-hgss", "name": "HeartGold & SoulSilver Era", "name_zh": "金銀魂心系列",
        "logo": CATEGORY_LOGOS["legend"],
        "sets": [
            "Call-of-Legends", "HS-Triumphant", "HS-Undaunted", "HS-Unleashed",
            "HeartGold-SoulSilver", "HeartGold-SoulSilver-Promos",
        ],
    },
    {
        "id": "en-dp", "name": "Diamond & Pearl / Platinum Era", "name_zh": "鑽石珍珠系列",
        "logo": CATEGORY_LOGOS["dp"],
        "sets": [
            "Platinum-Arceus", "Platinum-Supreme-Victors", "Platinum-Rising-Rivals",
            "Platinum", "Stormfront", "Legends-Awakened", "Majestic-Dawn",
            "Great-Encounters", "Secret-Wonders", "Mysterious-Treasures",
            "Diamond-Pearl", "DP-Black-Star-Promos",
        ],
    },
    {
        "id": "en-ex", "name": "EX Era", "name_zh": "EX系列",
        "logo": CATEGORY_LOGOS["ecard"],
        "sets": [
            "EX-Power-Keepers", "EX-Dragon-Frontiers", "EX-Crystal-Guardians",
            "EX-Holon-Phantoms", "EX-Legend-Maker", "EX-Delta-Species",
            "EX-UnseForces", "EX-Emerald", "EX-Deoxys", "EX-Team-Rocket-Returns",
            "EX-FireRed-LeafGreen", "EX-HiddLegends", "EX-Team-Magma-vs-Team-Aqua",
            "EX-Dragon", "EX-Sandstorm", "EX-Ruby-Sapphire",
        ],
    },
    {
        "id": "en-ecard", "name": "e-Card Era", "name_zh": "e-Card系列",
        "logo": CATEGORY_LOGOS["ecard"],
        "sets": ["Skyridge", "Aquapolis", "Expedition"],
    },
    {
        "id": "en-neo", "name": "Neo Era", "name_zh": "Neo系列",
        "logo": CATEGORY_LOGOS["neo"],
        "sets": ["Neo-Destiny", "Neo-Revelation", "Neo-Discovery", "Neo-Genesis"],
    },
    {
        "id": "en-original", "name": "Original / Gym Era", "name_zh": "初代系列",
        "logo": CATEGORY_LOGOS["original"],
        "sets": [
            "Legendary-Collection", "Gym-Challenge", "Gym-Heroes",
            "Team-Rocket", "Base-Set-2", "Fossil", "Jungle",
            "Base-Set", "Southern-Islands",
        ],
    },
    {
        "id": "en-mega", "name": "Mega Evolution (2026)", "name_zh": "超級進化系列",
        "logo": CATEGORY_LOGOS["mega"],
        "sets": [
            "Ninja-Spinner", "Munikis-Zero", "MEGA-Dream-ex", "Inferno-X",
            "Mega-Brave", "Mega-Symphonia", "Mega-Evolution",
            "Mega-Evolution-Black-Star-Promos", "Phantasmal-Flames",
            "Ascended-Heroes", "Perfect-Order", "Chaos-Rising",
        ],
    },
    {
        "id": "en-pop", "name": "POP Series", "name_zh": "POP 特典系列",
        "logo": CATEGORY_LOGOS["ppp"],
        "sets": [f"POP-Series-{i}" for i in range(1, 10)],
    },
    {
        "id": "en-mcd", "name": "McDonald's Collections", "name_zh": "麥當勞系列",
        "logo": CATEGORY_LOGOS["other"],
        "sets": [
            "McDonalds-25th-Anniversary", "McDonalds-Dragon-Discovery",
            "McDonalds-Match-Battle", "McDonalds-2023",
            "McDonalds-Collection-2019-FR",
        ] + [f"McDonalds-Collection-{y}" for y in range(2011, 2020)],
    },
    {
        "id": "en-promos", "name": "Other Promos", "name_zh": "其他特典",
        "logo": CATEGORY_LOGOS["other"],
        "sets": [
            "Nintendo-Promos", "Wizards-of-the-Coast-Promos",
            "Topps-Series-1", "Topps-Series-2", "Topps-Series-3",
            "Pokemon-Futsal-Promos", "Pokemon-Rumble", "Best-of-Game",
        ],
    },
    {
        "id": "other", "name": "Other", "name_zh": "其他",
        "logo": CATEGORY_LOGOS["other"], "sets": [],
    },
]


def get_category_for_set(set_id: str) -> dict:
    """取得系列所屬的分類"""
    for category in SET_CATEGORIES:
        if set_id in category["sets"]:
            return category
    return SET_CATEGORIES[-1]


def get_all_categories(language: str = "jp") -> list:
    """取得所有分類（依語言）"""
    if language == "en":
        return EN_SET_CATEGORIES
    return SET_CATEGORIES


def get_set_order(set_id: str) -> int:
    """取得系列的排序順序（越小越前面）"""
    for cat_idx, category in enumerate(SET_CATEGORIES):
        if set_id in category["sets"]:
            try:
                set_idx = category["sets"].index(set_id)
                return cat_idx * 1000 + set_idx
            except ValueError:
                pass
    return 99999
