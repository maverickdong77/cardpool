"""
PostgreSQL compatibility wrapper for asyncpg.

get_db() returns a PGConn (PostgreSQL) when DATABASE_URL is set,
otherwise falls back to a plain aiosqlite connection.
This lets existing aiosqlite-style code work unchanged.
"""
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, date, time as _time
from typing import Optional

import asyncpg

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")
USE_PG: bool = bool(DATABASE_URL)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        # Strip pgbouncer=true from Supabase URL; asyncpg ignores that param
        # and requires statement_cache_size=0 for PgBouncer compatibility.
        url = DATABASE_URL.split("?")[0]
        _pool = await asyncpg.create_pool(
            url,
            statement_cache_size=0,
            min_size=1,
            max_size=5,
        )
    return _pool


class _PGRow:
    """
    Dict-like + tuple-unpackable row.
    Supports: row["key"], row[0], a, b = row, dict(row).
    Datetime values are auto-converted to ISO strings for SQLite compatibility.
    """
    __slots__ = ("_data", "_keys", "_vals")

    def __init__(self, record):
        keys = list(record.keys())
        vals = []
        data = {}
        for k, v in zip(keys, record.values()):
            if isinstance(v, (datetime, date, _time)):
                v = v.isoformat()
            data[k] = v
            vals.append(v)
        self._data = data
        self._keys = keys
        self._vals = vals

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._vals[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._vals)

    def __bool__(self):
        return True

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def values(self):
        return iter(self._vals)


class _PGCursor:
    """Mimics aiosqlite cursor: fetchone(), fetchall(), lastrowid."""
    __slots__ = ("_rows", "lastrowid")

    def __init__(self, rows=None, lastrowid=None):
        self._rows = [_PGRow(r) for r in rows] if rows else []
        self.lastrowid = lastrowid

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class PGConn:
    """
    Wraps asyncpg.Connection to mimic aiosqlite Connection.
    - Converts ? → $N placeholders
    - Appends RETURNING id to INSERT for lastrowid support
    - commit() is a no-op (transaction auto-commits on context exit)
    - row_factory is accepted but ignored
    """
    __slots__ = ("_conn", "row_factory")

    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn
        self.row_factory = None

    @staticmethod
    def _to_pg(sql: str) -> str:
        n = 0
        def _sub(m):
            nonlocal n
            n += 1
            return f"${n}"
        return re.sub(r"\?", _sub, sql)

    async def execute(self, sql: str, params=()):
        pg = self._to_pg(sql)
        args = list(params) if params else []
        up = sql.lstrip().upper()

        if up.startswith(("SELECT", "WITH")):
            rows = await self._conn.fetch(pg, *args)
            return _PGCursor(rows=rows)

        if up.startswith("INSERT"):
            try:
                ret = pg.rstrip(" \n\r\t;") + " RETURNING id"
                row = await self._conn.fetchrow(ret, *args)
                return _PGCursor(lastrowid=row["id"] if row else None)
            except asyncpg.UndefinedColumnError:
                await self._conn.execute(pg, *args)
                return _PGCursor()
            except Exception:
                await self._conn.execute(pg, *args)
                return _PGCursor()

        await self._conn.execute(pg, *args)
        return _PGCursor()

    async def commit(self):
        pass  # transaction auto-commits on pg_connect() context exit


@asynccontextmanager
async def pg_connect():
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield PGConn(conn)


@asynccontextmanager
async def get_db():
    """
    Universal DB context manager for marketplace tables.
    Uses PostgreSQL when DATABASE_URL env var is set, else SQLite.
    """
    if USE_PG:
        async with pg_connect() as conn:
            yield conn
    else:
        import aiosqlite
        from app.database import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            yield db


async def init_pg_tables() -> None:
    """Create all marketplace tables in PostgreSQL (idempotent)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id             SERIAL PRIMARY KEY,
                    email          TEXT UNIQUE NOT NULL,
                    display_name   TEXT,
                    line_user_id   TEXT,
                    password_hash  TEXT NOT NULL,
                    phone          TEXT,
                    phone_verified INTEGER NOT NULL DEFAULT 0,
                    role           TEXT NOT NULL DEFAULT 'user',
                    google_id      TEXT,
                    oauth_provider TEXT,
                    avatar_url     TEXT,
                    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)"
            )

            # expires_at stored as TEXT (ISO string) to match SQLite behaviour
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token      TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)"
            )

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS email_verifications (
                    email         TEXT PRIMARY KEY,
                    code          TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    display_name  TEXT,
                    attempts      INTEGER NOT NULL DEFAULT 0,
                    expires_at    TEXT NOT NULL,
                    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS login_logs (
                    id         SERIAL PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    method     TEXT NOT NULL DEFAULT 'password',
                    ip         TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS password_resets (
                    token      TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    used       INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS listings (
                    id               SERIAL PRIMARY KEY,
                    user_id          INTEGER NOT NULL REFERENCES users(id),
                    set_id           TEXT NOT NULL,
                    card_number      TEXT NOT NULL,
                    grade            INTEGER NOT NULL,
                    psa_cert_number  TEXT,
                    ask_price_twd    REAL NOT NULL,
                    condition        TEXT,
                    description      TEXT,
                    status           TEXT NOT NULL DEFAULT 'active',
                    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at       TEXT,
                    matched_trade_id INTEGER
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_user ON listings(user_id, status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_listings_active ON listings(set_id, card_number, grade, status)"
            )

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS bids (
                    id               SERIAL PRIMARY KEY,
                    user_id          INTEGER NOT NULL REFERENCES users(id),
                    set_id           TEXT NOT NULL,
                    card_number      TEXT NOT NULL,
                    grade            INTEGER NOT NULL,
                    bid_price_twd    REAL NOT NULL,
                    status           TEXT NOT NULL DEFAULT 'active',
                    created_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at       TEXT,
                    matched_trade_id INTEGER
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bids_user ON bids(user_id, status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bids_active ON bids(set_id, card_number, grade, status)"
            )

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          SERIAL PRIMARY KEY,
                    listing_id  INTEGER REFERENCES listings(id),
                    bid_id      INTEGER REFERENCES bids(id),
                    seller_id   INTEGER NOT NULL REFERENCES users(id),
                    buyer_id    INTEGER NOT NULL REFERENCES users(id),
                    set_id      TEXT NOT NULL,
                    card_number TEXT NOT NULL,
                    grade       INTEGER NOT NULL,
                    price_twd   REAL NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending',
                    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_seller ON trades(seller_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_buyer ON trades(buyer_id)"
            )

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS shop_items (
                    id          SERIAL PRIMARY KEY,
                    title       TEXT NOT NULL,
                    description TEXT,
                    price_twd   REAL NOT NULL,
                    image_url   TEXT,
                    stock       INTEGER NOT NULL DEFAULT 0,
                    is_active   INTEGER NOT NULL DEFAULT 1,
                    created_by  INTEGER,
                    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS shop_orders (
                    id              SERIAL PRIMARY KEY,
                    user_id         INTEGER NOT NULL REFERENCES users(id),
                    item_id         INTEGER NOT NULL REFERENCES shop_items(id),
                    qty             INTEGER NOT NULL DEFAULT 1,
                    total_price_twd REAL NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    notes           TEXT,
                    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shop_orders_user ON shop_orders(user_id)"
            )
