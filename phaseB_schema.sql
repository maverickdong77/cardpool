ALTER TABLE pokemon_card_jp_official RENAME TO jp_card_list;

ALTER TABLE pokemon_card_jp_official_set RENAME TO jp_card_list_set;

ALTER TABLE jp_card_list ADD COLUMN card_number TEXT;
ALTER TABLE jp_card_list ADD COLUMN rarity TEXT;
ALTER TABLE jp_card_list ADD COLUMN illustrator TEXT;
ALTER TABLE jp_card_list ADD COLUMN hp INTEGER;
ALTER TABLE jp_card_list ADD COLUMN image_phash TEXT;
ALTER TABLE jp_card_list ADD COLUMN card_type TEXT;
ALTER TABLE jp_card_list ADD COLUMN detail_synced_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS en_card_list (
    cardID INTEGER PRIMARY KEY AUTOINCREMENT,
    pokemontcg_id TEXT NOT NULL UNIQUE,
    set_id TEXT NOT NULL,
    number TEXT NOT NULL,
    name TEXT NOT NULL,
    supertype TEXT,
    subtypes TEXT,
    hp INTEGER,
    types TEXT,
    rarity TEXT,
    artist TEXT,
    flavor_text TEXT,
    pokedex_number INTEGER,
    regulation_mark TEXT,
    set_name TEXT,
    series TEXT,
    set_release_date TEXT,
    abilities TEXT,
    attacks TEXT,
    weaknesses TEXT,
    retreat_cost INTEGER,
    legalities TEXT,
    image_small_url TEXT,
    image_large_url TEXT,
    image_phash TEXT,
    tcgplayer_url TEXT,
    tcgplayer_prices TEXT,
    tcgplayer_updated_at TEXT,
    cardmarket_url TEXT,
    cardmarket_prices TEXT,
    cardmarket_updated_at TEXT,
    source TEXT DEFAULT 'pokemontcg.io',
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (set_id, number)
);

CREATE INDEX IF NOT EXISTS idx_en_pokemontcg_id ON en_card_list(pokemontcg_id);
CREATE INDEX IF NOT EXISTS idx_en_set_id ON en_card_list(set_id);
CREATE INDEX IF NOT EXISTS idx_en_name ON en_card_list(name);
CREATE INDEX IF NOT EXISTS idx_en_pokedex ON en_card_list(pokedex_number);

CREATE TABLE IF NOT EXISTS en_card_list_set (
    set_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    series TEXT,
    printed_total INTEGER,
    total INTEGER,
    release_date TEXT,
    ptcgo_code TEXT,
    logo_url TEXT,
    symbol_url TEXT,
    legalities TEXT,
    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);