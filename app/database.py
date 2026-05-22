import sqlite3
import aiosqlite
import json
from datetime import datetime
from typing import Optional
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cards.db")

_JP_ZH_LOOKUP = {}
_jp_zh_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_jp_zh_translations.json")
if os.path.exists(_jp_zh_path):
    try:
        with open(_jp_zh_path, encoding="utf-8") as _f:
            _JP_ZH_LOOKUP = json.load(_f)
        print(f"[jp_zh] 載入 {len(_JP_ZH_LOOKUP)} 條 JP→ZH 翻譯")
    except Exception as _e:
        print(f"[jp_zh] 載入失敗：{_e}")


def _norm_card_num_for_zh(s):
    if s is None:
        return ""
    s = str(s).strip().lstrip("#").split("/")[0]
    return s.lstrip("0") or "0"


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
            phone TEXT,
            phone_verified INTEGER DEFAULT 0,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # users 表 idempotent ALTER（既有 DB 補欄位）
    for col, typ in [
        ("phone", "TEXT"),
        ("phone_verified", "INTEGER DEFAULT 0"),
        ("role", "TEXT NOT NULL DEFAULT 'user'"),
    ]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # 已存在
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # 手機簡訊驗證碼快取（註冊 / 換手機用）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS phone_codes (
            phone TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Email 驗證碼（流程 A：先驗證才註冊、暫存註冊資料）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_verifications (
            email TEXT PRIMARY KEY,
            code TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_verifications_expires ON email_verifications(expires_at)")

    # 忘記密碼 token
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id)")

    # 賣家 KYC（撥款前必要實名 + 銀行帳戶）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS seller_profiles (
            user_id INTEGER PRIMARY KEY,
            real_name TEXT,
            id_last4 TEXT,
            bank_code TEXT,
            bank_account TEXT,
            kyc_status TEXT NOT NULL DEFAULT 'none',
            kyc_verified_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    # 收貨地址簿（kind: home 宅配 / cvs 7-11/全家門市取貨）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS address_book (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'home',
            recipient TEXT NOT NULL,
            phone TEXT NOT NULL,
            zipcode TEXT,
            addr_line TEXT,
            cvs_brand TEXT,
            cvs_store_id TEXT,
            cvs_store_name TEXT,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_address_user ON address_book(user_id, is_default DESC)")

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


def _detect_lang_from_set_id(set_id: str) -> str:
    """從 set_id 格式推斷語言：jp pg 是純數字（或 JP 官方 promo 代碼如 SV-P/M-P）、
    en 是小寫 (base1)、tw 是大寫含 - (S8)。"""
    s = (set_id or "").strip()
    if s.isdigit():
        return "jp"
    # JP 官方 promo pg（pokemon-card.com 公式代碼）
    if s in ("SV-P", "M-P", "S-P", "SM-P", "XY-P", "BW-P", "DP-P", "PCG-P"):
        return "jp"
    if s and s == s.upper():
        return "tw"
    return "en"


async def get_all_card_sets(language: str = "jp") -> list:
    """從三語 set 表取得系列列表。回傳統一欄位：
    set_id, name, name_jp, name_zh, logo_url, total_cards, release_date, language, source
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        result: list = []

        if language == "jp":
            # era 來自 jp_set_era_map（規則推論、590 set 全有 era）
            # logo/display_order 仍從 artofpkm 取（對得到的 42 個 set 才有）
            cursor = await db.execute("""
                SELECT CAST(j.pg AS TEXT) AS set_id,
                       j.pg AS pg,
                       j.name_jp,
                       j.hit_cnt AS total_cards,
                       j.logo_url AS jp_logo_url,
                       j.release_date AS jp_release_date,
                       em.era AS era,
                       MIN(a.id) AS artofpkm_id,
                       MIN(a.display_order) AS art_display_order,
                       MIN(a.release_date) AS art_release_date,
                       MIN(a.logo_url) AS art_logo_url
                FROM jp_card_list_set j
                LEFT JOIN jp_set_era_map em ON em.pg = j.pg
                LEFT JOIN card_sets cs ON cs.name_jp = j.name_jp AND cs.language='jp'
                LEFT JOIN artofpkm_set_match m ON m.our_set_id = cs.set_id
                LEFT JOIN artofpkm_sets a ON a.id = m.art_id
                WHERE (j.name_jp IS NOT NULL AND j.name_jp != '')
                  AND j.name_jp NOT LIKE '%」「%'
                  AND j.name_jp NOT LIKE '%amazon%'
                  AND j.name_jp NOT LIKE '%ポケモンセンターオンライン%'
                  AND j.name_jp NOT LIKE '%購入はこちら%'
                GROUP BY j.pg
                ORDER BY COALESCE(j.release_date, MIN(a.release_date)) IS NULL,
                         COALESCE(j.release_date, MIN(a.release_date)) DESC,
                         CAST(j.pg AS INTEGER) ASC
            """)
            for row in await cursor.fetchall():
                d = dict(row)
                # logo 優先：artofpkm（curated, 設計過的 logo） → jp_card_list_set（卡的縮圖、暫代）
                logo = d.get("art_logo_url") or d.get("jp_logo_url")
                # JP logo 可能是 /assets/... 相對路徑、需前置官方 domain
                if logo and logo.startswith("/"):
                    logo = "https://www.pokemon-card.com" + logo
                result.append({
                    "set_id": d["set_id"],
                    "name": d.get("name_jp"),
                    "name_jp": d.get("name_jp"),
                    "name_zh": None,
                    "logo_url": logo,
                    "total_cards": d.get("total_cards"),
                    # release_date 優先：jp_card_list_set.release_date（pokemon-card.com 官方）→ artofpkm fallback
                    "release_date": d.get("jp_release_date") or d.get("art_release_date"),
                    # 前端 filter 要 art_id != null；對不到 artofpkm 時用 pg 當 synthetic id
                    "art_id": d.get("artofpkm_id") if d.get("artofpkm_id") is not None else d.get("pg"),
                    "era": d.get("era") or "Other",
                    # display_order 沒 artofpkm 時留 null，讓前端 ?? 落到 99999
                    "display_order": d.get("art_display_order"),
                    "language": "jp",
                    "source": "pokellector_jp",
                })

        elif language == "en":
            cursor = await db.execute("""
                SELECT set_id, name, series, total AS total_cards,
                       release_date, logo_url, symbol_url
                FROM en_card_list_set
                ORDER BY release_date IS NULL, release_date DESC, set_id ASC
            """)
            for row in await cursor.fetchall():
                d = dict(row)
                result.append({
                    "set_id": d["set_id"],
                    "name": d.get("name"),
                    "name_jp": None,
                    "name_zh": None,
                    "logo_url": d.get("logo_url"),
                    "symbol_url": d.get("symbol_url"),
                    "total_cards": d.get("total_cards"),
                    "release_date": d.get("release_date"),
                    "series": d.get("series"),
                    "era": d.get("series") or "Other",
                    # 前端 filterByLang('en') 不要求 art_id，但帶 set_id 當 synthetic 給統一介面
                    "art_id": d["set_id"],
                    "language": "en",
                    "source": "pokemontcg",
                })

        elif language == "tw":
            cursor = await db.execute("""
                SELECT s.expansion_code AS set_id,
                       s.name_zh,
                       s.card_count AS total_cards,
                       s.logo_url,
                       em.era AS era
                FROM tw_card_list_set s
                LEFT JOIN tw_set_era_map em ON em.expansion_code = s.expansion_code
                ORDER BY s.expansion_code DESC
            """)
            for row in await cursor.fetchall():
                d = dict(row)
                result.append({
                    "set_id": d["set_id"],
                    "name": d.get("name_zh"),
                    "name_jp": None,
                    "name_zh": d.get("name_zh"),
                    "logo_url": d.get("logo_url"),
                    "total_cards": d.get("total_cards"),
                    "release_date": None,
                    "era": d.get("era") or "Other",
                    # 前端 filterByLang('tw') 不需 art_id，但為了相容 setCardHtml 也帶上
                    "art_id": d["set_id"],
                    "language": "tw",
                    "source": "pokemon_asia",
                })

        return result


async def get_cards_by_set(set_id: str) -> list:
    """取得系列的所有卡片。由 set_id 格式自動判斷語言（jp 數字、en 小寫、tw 大寫）。
    回傳統一欄位：set_id, card_number, name, name_jp, name_zh, image_url, rarity, language
    """
    lang = _detect_lang_from_set_id(set_id)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        if lang == "jp":
            # JOIN jp_card_pg_link 支援 N:N（一張卡可屬多個 pg）
            cursor = await db.execute("""
                SELECT ? AS set_id,
                       c.cardID,
                       c.card_number, c.name_jp, c.thumb_url AS image_url,
                       c.rarity, c.illustrator, c.hp, c.image_id
                FROM jp_card_list c
                JOIN jp_card_pg_link l ON l.cardID = c.cardID
                WHERE l.pg = ?
                ORDER BY CAST(c.card_number AS INTEGER), c.image_id
            """, (set_id, set_id))
            rows = [dict(r) for r in await cursor.fetchall()]

            # promo set (9001/9002/9003) 為各期 promo 集合、每張卡發售日期不同
            # 用 cardID 對照各非-promo expansion 的 min_cardID 推算 inferred_release_date
            if set_id in ("9001", "9002", "9003"):
                cur2 = await db.execute("""
                    SELECT s.pg, s.release_date, s.name_jp AS set_name, MIN(c.cardID) AS min_cid
                    FROM jp_card_list_set s
                    JOIN jp_card_pg_link l ON l.pg = s.pg
                    JOIN jp_card_list c ON c.cardID = l.cardID
                    WHERE s.release_date IS NOT NULL AND s.pg NOT IN ('9001','9002','9003')
                    GROUP BY s.pg
                    HAVING MIN(c.cardID) IS NOT NULL
                    ORDER BY MIN(c.cardID) ASC
                """)
                ranges = [
                    (r["min_cid"], r["release_date"], r["set_name"])
                    for r in await cur2.fetchall()
                ]

                def _infer(cid):
                    best_date = None
                    best_name = None
                    for mc, dt, nm in ranges:
                        if mc <= cid:
                            best_date = dt
                            best_name = nm
                        else:
                            break
                    return best_date, best_name

                for r in rows:
                    cid = r.get("cardID")
                    if cid is not None:
                        dt, nm = _infer(cid)
                        r["inferred_release_date"] = dt
                        r["inferred_set_name"] = nm

                # 每張卡的「収録商品」(pokemon-card.com 官方)：取 MIN(product_pg) 當 canonical
                cur3 = await db.execute("""
                    SELECT p.cardID, p.product_pg, p.product_name_jp, p.product_name_zh
                    FROM jp_promo_card_product p
                    JOIN (
                        SELECT cardID, MIN(product_pg) AS min_pg
                        FROM jp_promo_card_product
                        WHERE product_pg > 0
                        GROUP BY cardID
                    ) m ON m.cardID = p.cardID AND m.min_pg = p.product_pg
                """)
                product_map = {
                    r["cardID"]: (r["product_pg"], r["product_name_jp"], r["product_name_zh"])
                    for r in await cur3.fetchall()
                }
                for r in rows:
                    cid = r.get("cardID")
                    if cid in product_map:
                        r["product_pg"] = product_map[cid][0]
                        r["product_name_jp"] = product_map[cid][1]
                        r["product_name_zh"] = product_map[cid][2]

            for r in rows:
                # JP thumb_url 是 /assets/... 相對路徑、要前置官方 domain
                u = r.get("image_url")
                if u and u.startswith("/"):
                    r["image_url"] = "https://www.pokemon-card.com" + u
                r["name"] = r.get("name_jp")
                cn_key = _norm_card_num_for_zh(r.get("card_number"))
                r["name_zh"] = _JP_ZH_LOOKUP.get(f"{set_id}/{cn_key}")
                r["language"] = "jp"
            return rows

        if lang == "en":
            cursor = await db.execute("""
                SELECT set_id, number AS card_number, name,
                       image_large_url AS image_url, image_small_url,
                       rarity, artist AS illustrator, hp, types, supertype
                FROM en_card_list
                WHERE set_id = ?
                ORDER BY CAST(REPLACE(REPLACE(number,'a',''),'b','') AS INTEGER)
            """, (set_id,))
            rows = [dict(r) for r in await cursor.fetchall()]
            for r in rows:
                r["name_jp"] = None
                r["name_zh"] = None
                r["language"] = "en"
            return rows

        # tw
        cursor = await db.execute("""
            SELECT expansion_code AS set_id,
                   card_number, name_zh, thumb_url AS image_url,
                   rarity, illustrator, hp, card_type, stage
            FROM tw_card_list
            WHERE expansion_code = ?
            ORDER BY card_number
        """, (set_id,))
        rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            r["name"] = r.get("name_zh")
            r["name_jp"] = None
            r["language"] = "tw"
        return rows


async def search_cards_in_list(query: str, limit: int = 300, language: str = "") -> list:
    """在三語新卡表中搜尋卡片（依 language 選表，空字串=全部）。

    編號支援：純數字 / "043" / "#43" / "43/185"
    language: 'jp' / 'en' / 'tw' / '' (全部)
    回傳統一欄位：set_id, card_number, name, name_jp, name_zh, image_url, rarity, set_name, language
    """
    import re
    from app.pokemon_names import translate_to_english, translate_jp_to_english

    # 原始 query 用於 JP/TW（比對本地語言欄位）
    # 翻譯後 query 用於 EN（比對英文 name 欄位）
    query_orig = query.strip()
    query_en = translate_jp_to_english(translate_to_english(query_orig)).strip()

    def _normalize_number(s: str) -> str:
        s = s.strip()
        if "/" in s:
            s = s.split("/", 1)[0]
        s = s.lstrip("0") or "0"
        return s

    def _parse(q: str):
        n: Optional[str] = None
        name: Optional[str] = None
        if q.startswith('#'):
            n = _normalize_number(q[1:])
        elif re.match(r'^\d+(/\d+)?$', q):
            n = _normalize_number(q)
        else:
            m = re.match(r'^(.+?\D)\s*#?\s*(\d+(?:/\d+)?)$', q)
            if m and m.group(1).strip():
                name = m.group(1).strip()
                n = _normalize_number(m.group(2))
            else:
                name = q
        return name, n

    name_part_local, number_part = _parse(query_orig)
    name_part_en, _ = _parse(query_en)
    name_like_local = f"%{name_part_local}%" if name_part_local else None
    name_like_en = f"%{name_part_en}%" if name_part_en else None
    # 給原本各搜尋函式用的 alias（向後相容）
    name_part = name_part_local
    name_like = name_like_local

    async def _search_jp(db) -> list:
        where = []
        params: list = []
        if name_part:
            where.append("(c.name_jp LIKE ? OR c.romaji_name LIKE ?)")
            params += [name_like, name_like]
        if number_part:
            where.append("CAST(c.card_number AS INTEGER) = CAST(? AS INTEGER)")
            params.append(number_part)
        wsql = " AND ".join(where) if where else "1=1"
        cur = await db.execute(f"""
            SELECT CAST(c.pg AS TEXT) AS set_id,
                   c.card_number, c.name_jp, c.thumb_url AS image_url, c.rarity,
                   c.set_name_jp AS set_name
            FROM jp_card_list c
            WHERE {wsql}
            ORDER BY CAST(c.pg AS INTEGER) DESC
            LIMIT ?
        """, (*params, limit))
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["name"] = r.get("name_jp")
            r["name_zh"] = None
            r["language"] = "jp"
        return rows

    async def _search_en(db) -> list:
        where = []
        params: list = []
        if name_part_en:
            where.append("c.name LIKE ?")
            params.append(name_like_en)
        if number_part:
            where.append("CAST(REPLACE(REPLACE(c.number,'a',''),'b','') AS INTEGER) = CAST(? AS INTEGER)")
            params.append(number_part)
        wsql = " AND ".join(where) if where else "1=1"
        cur = await db.execute(f"""
            SELECT c.set_id, c.number AS card_number, c.name,
                   c.image_large_url AS image_url, c.image_small_url,
                   c.rarity, c.set_name AS set_name
            FROM en_card_list c
            WHERE {wsql}
            ORDER BY c.set_release_date DESC
            LIMIT ?
        """, (*params, limit))
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["name_jp"] = None
            r["name_zh"] = None
            r["language"] = "en"
        return rows

    async def _search_tw(db) -> list:
        where = []
        params: list = []
        if name_part:
            where.append("c.name_zh LIKE ?")
            params.append(name_like)
        if number_part:
            where.append("CAST(c.card_number AS INTEGER) = CAST(? AS INTEGER)")
            params.append(number_part)
        wsql = " AND ".join(where) if where else "1=1"
        cur = await db.execute(f"""
            SELECT c.expansion_code AS set_id,
                   c.card_number, c.name_zh, c.thumb_url AS image_url, c.rarity,
                   c.set_name_zh AS set_name
            FROM tw_card_list c
            WHERE {wsql}
            LIMIT ?
        """, (*params, limit))
        rows = [dict(r) for r in await cur.fetchall()]
        for r in rows:
            r["name"] = r.get("name_zh")
            r["name_jp"] = None
            r["language"] = "tw"
        return rows

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if language == "jp":
            return await _search_jp(db)
        if language == "en":
            return await _search_en(db)
        if language == "tw":
            return await _search_tw(db)
        # 全部：合併三語、各取 limit 再截
        out = await _search_jp(db) + await _search_en(db) + await _search_tw(db)
        return out[:limit]


async def get_card_set_stats() -> dict:
    """卡表統計：三語 set 與卡片總數加總。"""
    async with aiosqlite.connect(DB_PATH) as db:
        jp_sets = (await (await db.execute("SELECT COUNT(*) FROM jp_card_list_set")).fetchone())[0]
        en_sets = (await (await db.execute("SELECT COUNT(*) FROM en_card_list_set")).fetchone())[0]
        tw_sets = (await (await db.execute("SELECT COUNT(*) FROM tw_card_list_set")).fetchone())[0]
        jp_cards = (await (await db.execute("SELECT COUNT(*) FROM jp_card_list")).fetchone())[0]
        en_cards = (await (await db.execute("SELECT COUNT(*) FROM en_card_list")).fetchone())[0]
        tw_cards = (await (await db.execute("SELECT COUNT(*) FROM tw_card_list")).fetchone())[0]
        return {
            "total_sets": jp_sets + en_sets + tw_sets,
            "total_cards": jp_cards + en_cards + tw_cards,
            "by_language": {
                "jp": {"sets": jp_sets, "cards": jp_cards},
                "en": {"sets": en_sets, "cards": en_cards},
                "tw": {"sets": tw_sets, "cards": tw_cards},
            },
        }


# 初始化資料庫
init_db()
