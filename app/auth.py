"""
Auth 模組 — Phase 2 marketplace 用。

- 密碼：PBKDF2-HMAC-SHA256（stdlib，不引入 bcrypt 等新依賴）
- Session：DB 表 + Bearer Token（伺服端 lookup，不用 JWT）

Token 從 HTTP header `Authorization: Bearer <token>` 帶入。
"""
import hashlib
import secrets
import re
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from fastapi import Header, HTTPException, status

from app.database import DB_PATH

# pbkdf2 參數
PBKDF2_ITER = 200_000
PBKDF2_HASH = "sha256"
SALT_BYTES = 16
SESSION_DAYS = 30

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ====================== Password ======================

def hash_password(password: str) -> str:
    """回傳格式：pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>"""
    if not password or len(password) < 6:
        raise ValueError("密碼至少 6 字元")
    salt = secrets.token_bytes(SALT_BYTES)
    h = hashlib.pbkdf2_hmac(PBKDF2_HASH, password.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.pbkdf2_hmac(PBKDF2_HASH, password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(expected, actual)
    except Exception:
        return False


# ====================== User CRUD ======================

async def create_user(email: str, password: str, display_name: Optional[str] = None) -> dict:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="email 格式錯誤")
    try:
        pw_hash = hash_password(password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    async with aiosqlite.connect(DB_PATH) as db:
        # 檢查 email 是否已用
        cur = await db.execute("SELECT id FROM users WHERE email=?", (email,))
        if await cur.fetchone():
            raise HTTPException(status_code=409, detail="此 email 已註冊")
        cur = await db.execute(
            "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
            (email, display_name or email.split("@")[0], pw_hash),
        )
        await db.commit()
        uid = cur.lastrowid
    return await get_user_by_id(uid)


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, display_name, line_user_id, phone, phone_verified, role, created_at FROM users WHERE id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def authenticate(email: str, password: str) -> Optional[dict]:
    email = (email or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, email, display_name, line_user_id, phone, phone_verified, role, password_hash FROM users WHERE email=?",
            (email,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "line_user_id": row["line_user_id"],
        "phone": row["phone"],
        "phone_verified": row["phone_verified"],
        "role": row["role"],
    }


# ====================== Sessions ======================

async def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
        await db.commit()
    return token


async def delete_session(token: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE token=?", (token,))
        await db.commit()


async def get_user_by_session(token: str) -> Optional[dict]:
    if not token:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT u.id, u.email, u.display_name, u.line_user_id,
                      u.phone, u.phone_verified, u.role, s.expires_at
               FROM sessions s JOIN users u ON s.user_id=u.id
               WHERE s.token=?""",
            (token,),
        )
        row = await cur.fetchone()
    if not row:
        return None
    # 檢查過期
    try:
        exp = datetime.fromisoformat(row["expires_at"])
        if exp < datetime.utcnow():
            await delete_session(token)
            return None
    except Exception:
        pass
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "line_user_id": row["line_user_id"],
        "phone": row["phone"],
        "phone_verified": row["phone_verified"],
        "role": row["role"],
    }


# ====================== FastAPI dependency ======================

async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """FastAPI Depends — 強制登入。從 Authorization: Bearer <token> 取 user。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登入")
    token = authorization.split(" ", 1)[1].strip()
    user = await get_user_by_session(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登入逾期，請重新登入")
    return user


async def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """FastAPI Depends — 不強制登入。沒登入回 None。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return await get_user_by_session(token)


def require_role(*allowed_roles: str):
    """FastAPI Depends factory — 限制只有指定 role 可呼叫。
    用法：user: dict = Depends(require_role('staff'))
         user: dict = Depends(require_role('authenticator', 'staff'))
    """
    async def _checker(user: dict = None, authorization: Optional[str] = Header(None)) -> dict:
        u = await get_current_user(authorization)
        if u.get("role") not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="權限不足")
        return u
    return _checker


# ====================== Login Log ======================

async def log_login(user_id: int, method: str, ip: Optional[str] = None,
                    user_agent: Optional[str] = None) -> None:
    """記錄登入事件（fire-and-forget）。"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO login_logs (user_id, method, ip, user_agent) VALUES (?, ?, ?, ?)",
            (user_id, method, ip, user_agent),
        )
        await db.commit()


# ====================== Google OAuth2 ======================

import os as _os

GOOGLE_CLIENT_ID = _os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = _os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# CSRF state 暫存（記憶體即可，30 秒有效）
_oauth_states: dict[str, float] = {}


def build_google_auth_url(redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(code: str, redirect_uri: str) -> Optional[dict]:
    """用 code 換 token，再拿 userinfo。失敗回 None。"""
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        })
        if token_resp.status_code != 200:
            print(f"[Google OAuth] token exchange fail: {token_resp.text}")
            return None
        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            return None
        info_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if info_resp.status_code != 200:
            return None
        return info_resp.json()


async def get_or_create_google_user(google_info: dict) -> dict:
    """從 Google userinfo 找或建立本站 user，回 user dict。"""
    google_id = google_info.get("sub", "")
    email = (google_info.get("email") or "").lower().strip()
    display_name = google_info.get("name") or email.split("@")[0]
    avatar_url = google_info.get("picture") or None

    if not google_id or not email:
        raise HTTPException(status_code=400, detail="Google 帳號缺少必要資訊")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 1) 先用 google_id 找
        row = await (await db.execute(
            "SELECT id FROM users WHERE google_id=?", (google_id,)
        )).fetchone()
        if row:
            uid = row["id"]
            # 更新頭像（若有變）
            await db.execute(
                "UPDATE users SET avatar_url=? WHERE id=?", (avatar_url, uid)
            )
            await db.commit()
            return await get_user_by_id(uid)

        # 2) 用 email 找（密碼帳號合併）
        row = await (await db.execute(
            "SELECT id FROM users WHERE email=?", (email,)
        )).fetchone()
        if row:
            uid = row["id"]
            await db.execute(
                "UPDATE users SET google_id=?, oauth_provider='google', avatar_url=? WHERE id=?",
                (google_id, avatar_url, uid),
            )
            await db.commit()
            return await get_user_by_id(uid)

        # 3) 全新用戶 — password_hash 留空字串（不能用密碼登入，只能 Google）
        cur = await db.execute(
            """INSERT INTO users
               (email, display_name, password_hash, google_id, oauth_provider, avatar_url)
               VALUES (?, ?, '', ?, 'google', ?)""",
            (email, display_name, google_id, avatar_url),
        )
        await db.commit()
        return await get_user_by_id(cur.lastrowid)


async def get_user_by_id(user_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, email, display_name, line_user_id, phone, phone_verified,
                      role, oauth_provider, avatar_url, created_at
               FROM users WHERE id=?""",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
