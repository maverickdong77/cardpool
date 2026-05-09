import sqlite3
import aiosqlite
from datetime import datetime
from typing import Optional
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cards.db")


def init_db():
    """初始化資料庫"""
    conn = sqlite3.connect(DB_PATH)
    # SQLite 預設 FK 校驗是關的；schema 創建期間打開以使 ON DELETE CASCADE 生效。
    # TODO(stage 2+): operational 端（aiosqlite scattered connections）需統一一個
    # connection helper 也跑 PRAGMA foreign_keys = ON，否則 cascade 不會在運行期執行。
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    # 系列表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id TEXT UNIQUE,
            name TEXT,
            name_jp TEXT,
            logo_url TEXT,
            total_cards INTEGER,
            release_date TEXT,
            language TEXT DEFAULT 'jp',
            source TEXT DEFAULT 'pokellector',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 卡片資料表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_id TEXT,
            card_number TEXT,
            name TEXT,
            name_jp TEXT,
            image_url TEXT,
            rarity TEXT,
            card_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(set_id, card_number)
        )
    """)

    # 卡片基本資訊表 (PSA 卡片)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            psa_cert_number TEXT UNIQUE,
            card_name TEXT,
            card_name_jp TEXT,
            set_name TEXT,
            card_number TEXT,
            grade TEXT,
            year TEXT,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # eBay 價格紀錄表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ebay_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER,
            psa_cert_number TEXT,
            price_usd REAL,
            price_twd REAL,
            sale_date TIMESTAMP,
            listing_title TEXT,
            listing_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES cards(id)
        )
    """)

    # SNKRDUNK 價格紀錄表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS snkrdunk_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER,
            psa_cert_number TEXT,
            price_jpy REAL,
            price_twd REAL,
            sale_date TIMESTAMP,
            listing_title TEXT,
            listing_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES cards(id)
        )
    """)

    # 搜尋紀錄表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            search_query TEXT,
            search_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ========== Phase 2: marketplace ==========
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            display_name TEXT,
            line_user_id TEXT,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            set_id TEXT NOT NULL,
            card_number TEXT NOT NULL,
            grade INTEGER NOT NULL,
            psa_cert_number TEXT,
            ask_price_twd REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            matched_trade_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(set_id, card_number, grade, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_listings_user ON listings(user_id, status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            set_id TEXT NOT NULL,
            card_number TEXT NOT NULL,
            grade INTEGER NOT NULL,
            bid_price_twd REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            matched_trade_id INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bids_active ON bids(set_id, card_number, grade, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_bids_user ON bids(user_id, status)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            listing_id INTEGER NOT NULL,
            bid_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            set_id TEXT NOT NULL,
            card_number TEXT NOT NULL,
            grade INTEGER NOT NULL,
            price_twd REAL NOT NULL,
            fee_twd REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (listing_id) REFERENCES listings(id),
            FOREIGN KEY (bid_id) REFERENCES bids(id),
            FOREIGN KEY (buyer_id) REFERENCES users(id),
            FOREIGN KEY (seller_id) REFERENCES users(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_buyer ON trades(buyer_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_seller ON trades(seller_id, status)")

    # ========== 成交量統計（每張卡 7d/30d/all 累計）==========
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_volume_stats (
            set_id TEXT NOT NULL,
            card_number TEXT NOT NULL,
            sales_7d INTEGER DEFAULT 0,
            sales_30d INTEGER DEFAULT 0,
            sales_all INTEGER DEFAULT 0,
            last_sale_at TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (set_id, card_number)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_volume_7d ON card_volume_stats(sales_7d DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_volume_30d ON card_volume_stats(sales_30d DESC)")

    # ========== card_list 加 SNKR 即時掛單欄位（idempotent ALTER）==========
    for col, typ in [
        ("snkr_listing_count", "INTEGER"),
        ("snkr_min_price_jpy", "INTEGER"),
        ("snkr_listing_updated_at", "TIMESTAMP"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE card_list ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # 已存在

    # ========== Stage 1: secondary source 抽象層 ==========
    # card_field_sources：紀錄每張卡每個欄位的「目前最佳值」與來源。
    # UNIQUE(card_id, field_name) — 一張卡同欄位只一筆當前值。
    # priority 邏輯由 application 層判斷（見 app/sources/priority.py），DB 不存。
    #
    # Stage 3 一次性 schema rename：'name_en' → 'name'（card_list 從來只有 'name'
    # 欄位，schema 對齊 reality）。偵測舊 'name_en' CHECK 才 drop，避免 init_db()
    # 每次啟動都把已寫入的資料清掉。未來若有第二次 schema 改動需要走真 migration
    # （INSERT INTO new SELECT FROM old → drop old → rename）。
    cursor.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='card_field_sources'"
    )
    row = cursor.fetchone()
    if row and "'name_en'" in row[0]:
        cursor.execute("DROP TABLE card_field_sources")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS card_field_sources (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id      INTEGER NOT NULL,
            field_name   TEXT    NOT NULL CHECK (field_name IN (
                             'name_jp', 'name', 'name_zh', 'rarity', 'image_url'
                         )),
            source_name  TEXT    NOT NULL CHECK (source_name IN (
                             'manual', 'artofpkm', 'pokellector', '_52poke', 'ocr'
                         )),
            value        TEXT,
            confidence   INTEGER NOT NULL DEFAULT 100,
            updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (card_id) REFERENCES card_list(id) ON DELETE CASCADE,
            UNIQUE (card_id, field_name)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cfs_card ON card_field_sources(card_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cfs_field ON card_field_sources(field_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cfs_source ON card_field_sources(source_name)")

    conn.commit()
    conn.close()


async def update_volume_stats(set_id: str, card_number: str):
    """重算單張卡的 7d/30d/all 成交筆數，UPSERT 進 card_volume_stats"""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("""
            SELECT
                SUM(CASE WHEN COALESCE(sale_date, created_at) >= date('now', '-7 days') THEN 1 ELSE 0 END) AS s7,
                SUM(CASE WHEN COALESCE(sale_date, created_at) >= date('now', '-30 days') THEN 1 ELSE 0 END) AS s30,
                COUNT(*) AS sall,
                MAX(COALESCE(sale_date, created_at)) AS last_sale
            FROM card_prices
            WHERE set_id = ? AND card_number = ?
        """, (set_id, card_number))).fetchone()
        s7, s30, sall, last = (row[0] or 0, row[1] or 0, row[2] or 0, row[3])
        await db.execute("""
            INSERT INTO card_volume_stats (set_id, card_number, sales_7d, sales_30d, sales_all, last_sale_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(set_id, card_number) DO UPDATE SET
                sales_7d = excluded.sales_7d,
                sales_30d = excluded.sales_30d,
                sales_all = excluded.sales_all,
                last_sale_at = excluded.last_sale_at,
                updated_at = CURRENT_TIMESTAMP
        """, (set_id, card_number, s7, s30, sall, last))
        await db.commit()


async def update_snkr_listing_meta(set_id: str, card_number: str, listing_count: int, min_price_jpy: Optional[int]):
    """sync 結尾呼叫，把當下的掛單數/最低價寫進 card_list"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE card_list
               SET snkr_listing_count = ?,
                   snkr_min_price_jpy = ?,
                   snkr_listing_updated_at = CURRENT_TIMESTAMP
             WHERE set_id = ? AND card_number = ?
        """, (listing_count, min_price_jpy, set_id, card_number))
        await db.commit()


async def save_card(card_data: dict) -> int:
    """儲存卡片資訊"""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT OR REPLACE INTO cards
            (psa_cert_number, card_name, card_name_jp, set_name, card_number, grade, year, image_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            card_data.get("psa_cert_number"),
            card_data.get("card_name"),
            card_data.get("card_name_jp"),
            card_data.get("set_name"),
            card_data.get("card_number"),
            card_data.get("grade"),
            card_data.get("year"),
            card_data.get("image_url"),
            datetime.now()
        ))
        await db.commit()
        return cursor.lastrowid


async def save_ebay_price(price_data: dict):
    """儲存 eBay 價格"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO ebay_prices
            (card_id, psa_cert_number, price_usd, price_twd, sale_date, listing_title, listing_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            price_data.get("card_id"),
            price_data.get("psa_cert_number"),
            price_data.get("price_usd"),
            price_data.get("price_twd"),
            price_data.get("sale_date"),
            price_data.get("listing_title"),
            price_data.get("listing_url")
        ))
        await db.commit()


async def save_snkrdunk_price(price_data: dict):
    """儲存 SNKRDUNK 價格"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO snkrdunk_prices
            (card_id, psa_cert_number, price_jpy, price_twd, sale_date, listing_title, listing_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            price_data.get("card_id"),
            price_data.get("psa_cert_number"),
            price_data.get("price_jpy"),
            price_data.get("price_twd"),
            price_data.get("sale_date"),
            price_data.get("listing_title"),
            price_data.get("listing_url")
        ))
        await db.commit()


async def get_card_by_psa(psa_cert_number: str) -> Optional[dict]:
    """用 PSA 編號查詢卡片"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM cards WHERE psa_cert_number = ?",
            (psa_cert_number,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def search_cards_by_name(name: str, limit: int = 10) -> list:
    """用名稱搜尋卡片"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM cards
            WHERE card_name LIKE ? OR card_name_jp LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (f"%{name}%", f"%{name}%", limit))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_ebay_prices(psa_cert_number: str, limit: int = 10) -> list:
    """取得 eBay 價格歷史"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM ebay_prices
            WHERE psa_cert_number = ?
            ORDER BY sale_date DESC
            LIMIT ?
        """, (psa_cert_number, limit))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_snkrdunk_prices(psa_cert_number: str, limit: int = 10) -> list:
    """取得 SNKRDUNK 價格歷史"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM snkrdunk_prices
            WHERE psa_cert_number = ?
            ORDER BY sale_date DESC
            LIMIT ?
        """, (psa_cert_number, limit))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_latest_prices(psa_cert_number: str) -> dict:
    """取得最新價格（eBay + SNKRDUNK）"""
    ebay_prices = await get_ebay_prices(psa_cert_number, limit=1)
    snkrdunk_prices = await get_snkrdunk_prices(psa_cert_number, limit=1)

    return {
        "ebay": ebay_prices[0] if ebay_prices else None,
        "snkrdunk": snkrdunk_prices[0] if snkrdunk_prices else None
    }


async def save_search_history(user_id: str, query: str, search_type: str):
    """儲存搜尋紀錄"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO search_history (user_id, search_query, search_type)
            VALUES (?, ?, ?)
        """, (user_id, query, search_type))
        await db.commit()


# ==================== 卡表相關 ====================

async def save_card_set(set_data: dict):
    """儲存系列資料"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO card_sets
            (set_id, name, name_jp, logo_url, total_cards, release_date, language, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            set_data.get("id") or set_data.get("set_id"),
            set_data.get("name"),
            set_data.get("name_jp"),
            set_data.get("logo_url"),
            set_data.get("total_cards"),
            set_data.get("release_date"),
            set_data.get("language", "jp"),
            set_data.get("source", "pokellector"),
            datetime.now()
        ))
        await db.commit()


async def save_card_to_list(set_id: str, card_data: dict):
    """儲存卡片到卡表"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO card_list
            (set_id, card_number, name, name_jp, image_url, rarity, card_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            set_id,
            card_data.get("number"),
            card_data.get("name"),
            card_data.get("name_jp"),
            card_data.get("image_url"),
            card_data.get("rarity"),
            card_data.get("url")
        ))
        await db.commit()


async def get_all_card_sets(language: str = "jp") -> list:
    """取得所有系列；release_date 缺值時 fallback 到 artofpkm_sets.release_date。"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT cs.*,
                   COALESCE(NULLIF(cs.release_date, ''), a.release_date) AS effective_release_date,
                   a.release_date AS artofpkm_release_date,
                   a.id AS artofpkm_id,
                   a.display_order AS art_display_order,
                   a.era AS art_era
            FROM card_sets cs
            LEFT JOIN artofpkm_set_match m ON m.our_set_id = cs.set_id
            LEFT JOIN artofpkm_sets a ON a.id = m.art_id
            WHERE cs.language = ?
            ORDER BY cs.updated_at DESC
        """, (language,))
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            # 用 effective 覆蓋 release_date 給前端用
            if d.get("effective_release_date"):
                d["release_date"] = d["effective_release_date"]
            # 把 artofpkm_id 重命名成統一欄位 (前端可用)
            d["art_id"] = d.get("artofpkm_id")
            d["display_order"] = d.get("art_display_order")
            d["era"] = d.get("art_era")
            result.append(d)
        return result


async def get_cards_by_set(set_id: str) -> list:
    """取得系列的所有卡片"""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT * FROM card_list
            WHERE set_id = ?
            ORDER BY CAST(card_number AS INTEGER)
        """, (set_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def search_cards_in_list(query: str, limit: int = 300, language: str = "") -> list:
    """在卡表中搜尋卡片（支援名稱、編號、名稱+編號、中文/日文名稱）。

    編號支援：
      - 純數字: "43" 或 "043"（自動忽略前綴零）
      - 帶 #: "#43"
      - 含分母: "43/185" 或 "043/185"（取分子）

    language: 'jp' / 'en' / '' (全部)
    """
    import re
    from app.pokemon_names import translate_to_english, translate_jp_to_english

    # 中文/日文轉英文
    query = translate_to_english(query)
    query = translate_jp_to_english(query)

    def _normalize_number(s: str) -> str:
        """剝前綴零、若是 N/T 取分子。"""
        s = s.strip()
        if "/" in s:
            s = s.split("/", 1)[0]
        s = s.lstrip("0") or "0"
        return s

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        name_part = None
        number_part = None

        q = query.strip()

        # 純編號優先：純數字 / N/T / #N
        if q.startswith('#'):
            number_part = _normalize_number(q[1:])
        elif re.match(r'^\d+(/\d+)?$', q):
            number_part = _normalize_number(q)
        else:
            # name + 編號（編號前必須有非數字字元）
            match = re.match(r'^(.+?\D)\s*#?\s*(\d+(?:/\d+)?)$', q)
            if match and match.group(1).strip():
                name_part = match.group(1).strip()
                number_part = _normalize_number(match.group(2))
            else:
                name_part = q

        # language filter（SQL 層）
        lang_clause = ""
        lang_params: list = []
        if language in ("jp", "en"):
            lang_clause = " AND cl.set_id LIKE ?"
            lang_params = [f"{language}-%"]

        # 建立查詢（name 比對含中文名 cl.name_zh）
        if name_part and number_part:
            cursor = await db.execute(f"""
                SELECT cl.*, cs.name as set_name
                FROM card_list cl
                LEFT JOIN card_sets cs ON cl.set_id = cs.set_id
                WHERE (cl.name LIKE ? OR cl.name_jp LIKE ? OR cl.name_zh LIKE ?)
                  AND CAST(cl.card_number AS INTEGER) = CAST(? AS INTEGER)
                  {lang_clause}
                ORDER BY cs.updated_at DESC
                LIMIT ?
            """, (f"%{name_part}%", f"%{name_part}%", f"%{name_part}%", number_part, *lang_params, limit))
        elif number_part:
            cursor = await db.execute(f"""
                SELECT cl.*, cs.name as set_name
                FROM card_list cl
                LEFT JOIN card_sets cs ON cl.set_id = cs.set_id
                WHERE CAST(cl.card_number AS INTEGER) = CAST(? AS INTEGER)
                  {lang_clause}
                ORDER BY cs.updated_at DESC
                LIMIT ?
            """, (number_part, *lang_params, limit))
        else:
            cursor = await db.execute(f"""
                SELECT cl.*, cs.name as set_name
                FROM card_list cl
                LEFT JOIN card_sets cs ON cl.set_id = cs.set_id
                WHERE (cl.name LIKE ? OR cl.name_jp LIKE ? OR cl.name_zh LIKE ?)
                  {lang_clause}
                ORDER BY cs.updated_at DESC, CAST(cl.card_number AS INTEGER)
                LIMIT ?
            """, (f"%{name_part}%", f"%{name_part}%", f"%{name_part}%", *lang_params, limit))

        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_card_set_stats() -> dict:
    """取得卡表統計"""
    async with aiosqlite.connect(DB_PATH) as db:
        # 系列數
        cursor = await db.execute("SELECT COUNT(*) FROM card_sets")
        set_count = (await cursor.fetchone())[0]

        # 卡片數
        cursor = await db.execute("SELECT COUNT(*) FROM card_list")
        card_count = (await cursor.fetchone())[0]

        return {
            "total_sets": set_count,
            "total_cards": card_count,
        }


# 初始化資料庫
init_db()
