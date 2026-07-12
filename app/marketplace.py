"""
Marketplace 模組 — Phase 2 Bid/Ask 訂單簿與撮合。

設計原則：
- 撮合單位：(set_id, card_number, grade) 為一個獨立 orderbook
- Ask = 賣家要價（listings.ask_price_twd），低者優先
- Bid = 買家出價（bids.bid_price_twd），高者優先
- 撮合條件：highest_bid >= lowest_ask
- 同價多單：FIFO（先進先出，依 created_at）
- 撮合後：listing.status='sold', bid.status='matched'，產生 trade
"""
from datetime import datetime, timedelta
from typing import Optional

import aiosqlite
from fastapi import HTTPException

from app.database import DB_PATH
from app.db_pg import get_db

DEFAULT_EXPIRES_DAYS = 30
GRADES_ALLOWED = (10, 9, 0)  # 0 = Raw 未鑑定

# 品相選項（參考 SNKR Dunk，主要用於 Raw 卡 grade=0）
CONDITIONS_ALLOWED = ("mint", "near_mint", "excellent", "good", "poor")
CONDITIONS_ZH = {
    "mint": "全新未拆",
    "near_mint": "近全新",
    "excellent": "優良",
    "good": "良好",
    "poor": "普通",
}

PROTECTIVE_CASE_PRICE_TWD = 50  # 保護殼加購費（新台幣）

# 管理員 LINE User ID（環境變數 ADMIN_LINE_USER_ID，未設定則不推送）
import os as _os
ADMIN_LINE_USER_ID = _os.environ.get("ADMIN_LINE_USER_ID", "")


def _validate_grade(grade: int) -> int:
    try:
        g = int(grade)
    except Exception:
        raise HTTPException(status_code=400, detail="grade 必須是 10/9/0")
    if g not in GRADES_ALLOWED:
        raise HTTPException(status_code=400, detail="grade 必須是 10/9/0")
    return g


def _validate_price(price) -> float:
    try:
        p = float(price)
    except Exception:
        raise HTTPException(status_code=400, detail="價格必須是數字")
    if p <= 0 or p > 10_000_000:
        raise HTTPException(status_code=400, detail="價格範圍錯誤")
    return round(p, 0)


async def _card_exists(set_id: str, card_number: str) -> bool:
    # Box marketplace (2026-05-25 加): 用 set_id='__box__' + card_number=apparel_id 偽 ID
    # 改 check snkr_box_items 表、不去 card_list 找
    if set_id == "__box__":
        try:
            apparel_id_int = int(card_number)
        except ValueError:
            return False
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT 1 FROM snkr_box_items WHERE apparel_id=? LIMIT 1",
                (apparel_id_int,),
            )
            return await cur.fetchone() is not None

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM card_list WHERE set_id=? AND card_number=? LIMIT 1",
            (set_id, card_number),
        )
        return await cur.fetchone() is not None


# ============================================================
#   ORDER BOOK QUERY
# ============================================================

async def get_orderbook(set_id: str, card_number: str, grade: int) -> dict:
    """回傳指定卡 + grade 的當下訂單簿。"""
    grade = _validate_grade(grade)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            """SELECT MIN(ask_price_twd) AS lowest, COUNT(*) AS depth
               FROM listings
               WHERE set_id=? AND card_number=? AND grade=? AND status='active'""",
            (set_id, card_number, grade),
        )
        ask = await cur.fetchone()

        cur = await db.execute(
            """SELECT MAX(bid_price_twd) AS highest, COUNT(*) AS depth
               FROM bids
               WHERE set_id=? AND card_number=? AND grade=? AND status='active'""",
            (set_id, card_number, grade),
        )
        bid = await cur.fetchone()

        cur = await db.execute(
            """SELECT price_twd, completed_at, created_at
               FROM trades
               WHERE set_id=? AND card_number=? AND grade=?
               ORDER BY id DESC LIMIT 1""",
            (set_id, card_number, grade),
        )
        last = await cur.fetchone()

    return {
        "set_id": set_id,
        "card_number": card_number,
        "grade": grade,
        "lowest_ask": ask["lowest"] if ask and ask["lowest"] else None,
        "ask_depth": ask["depth"] if ask else 0,
        "highest_bid": bid["highest"] if bid and bid["highest"] else None,
        "bid_depth": bid["depth"] if bid else 0,
        "last_trade_price": last["price_twd"] if last else None,
        "last_trade_at": (last["completed_at"] or last["created_at"]) if last else None,
    }


async def get_orderbook_depth(set_id: str, card_number: str, grade: int, limit: int = 20) -> dict:
    """回傳訂單簿深度：ASK 全 list (低→高) + BID 全 list (高→低)、各取前 limit 筆。
    Box marketplace 用：盒裝詳情頁顯多筆掛單。隱藏 user_id、只回 masked alias。
    """
    grade = _validate_grade(grade)
    async with get_db() as db:
        db.row_factory = aiosqlite.Row

        ask_cur = await db.execute(
            """SELECT l.id, l.user_id, l.ask_price_twd AS price, l.created_at,
                      COALESCE(u.display_name, 'user_' || l.user_id) AS user_name
               FROM listings l
               LEFT JOIN users u ON u.id = l.user_id
               WHERE l.set_id=? AND l.card_number=? AND l.grade=? AND l.status='active'
               ORDER BY l.ask_price_twd ASC, l.created_at ASC
               LIMIT ?""",
            (set_id, card_number, grade, limit),
        )
        asks = [dict(r) for r in await ask_cur.fetchall()]

        bid_cur = await db.execute(
            """SELECT b.id, b.user_id, b.bid_price_twd AS price, b.created_at,
                      COALESCE(u.display_name, 'user_' || b.user_id) AS user_name
               FROM bids b
               LEFT JOIN users u ON u.id = b.user_id
               WHERE b.set_id=? AND b.card_number=? AND b.grade=? AND b.status='active'
               ORDER BY b.bid_price_twd DESC, b.created_at ASC
               LIMIT ?""",
            (set_id, card_number, grade, limit),
        )
        bids = [dict(r) for r in await bid_cur.fetchall()]

    # Mask user_name 隱私 (保留前 2 字、後綴 ***)
    def _mask(name):
        if not name:
            return 'user_***'
        if len(name) <= 2:
            return name + '***'
        return name[:2] + '***'

    for row in asks + bids:
        row['user_name'] = _mask(row['user_name'])
        row.pop('user_id', None)  # 不回 user_id

    return {
        'set_id': set_id,
        'card_number': card_number,
        'grade': grade,
        'asks': asks,
        'bids': bids,
    }



# ============================================================
#   CREATE LISTING (sell)
# ============================================================

async def create_listing(user_id: int, payload: dict) -> dict:
    set_id = (payload.get("set_id") or "").strip()
    card_number = (payload.get("card_number") or "").strip()
    grade = _validate_grade(payload.get("grade"))
    ask_price = _validate_price(payload.get("ask_price_twd"))
    psa_cert = (payload.get("psa_cert_number") or "").strip() or None
    condition = (payload.get("condition") or "").strip().lower() or None
    description = (payload.get("description") or "").strip()[:500] or None

    if not set_id or not card_number:
        raise HTTPException(status_code=400, detail="set_id / card_number 必填")
    if not await _card_exists(set_id, card_number):
        raise HTTPException(status_code=404, detail="找不到此卡片")
    if grade in (10, 9) and not psa_cert:
        raise HTTPException(status_code=400, detail="PSA 鑑定卡需填入鑑定編號")
    if condition and condition not in CONDITIONS_ALLOWED:
        raise HTTPException(status_code=400, detail=f"condition 須為 {'/'.join(CONDITIONS_ALLOWED)}")

    expires_at = (datetime.utcnow() + timedelta(days=DEFAULT_EXPIRES_DAYS)).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO listings
               (user_id, set_id, card_number, grade, psa_cert_number, ask_price_twd,
                condition, description, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, set_id, card_number, grade, psa_cert, ask_price,
             condition, description, expires_at),
        )
        await db.commit()
        listing_id = cur.lastrowid

    # 嘗試撮合
    trade = await _match_after_listing(listing_id)
    out = await get_listing(listing_id)
    if trade:
        out["matched_trade"] = trade

    # 寄上架成功 email（非同步，不阻塞回傳）
    import asyncio as _asyncio
    _asyncio.create_task(_email_listing_confirmed(user_id, out))

    return out


async def _email_listing_confirmed(user_id: int, listing: dict) -> None:
    """上架成功後非同步寄信給賣家。"""
    from app.email_sender import send_listing_confirmed
    import aiosqlite as _aio
    async with _aio.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT email FROM users WHERE id=?", (user_id,))).fetchone()
    if not row or not row[0]:
        return
    try:
        send_listing_confirmed(
            to_email=row[0],
            set_id=listing.get("set_id", ""),
            card_number=listing.get("card_number", ""),
            grade=listing.get("grade", 0),
            ask_price_twd=listing.get("ask_price_twd", 0),
            condition=listing.get("condition"),
            listing_id=listing.get("id"),
        )
    except Exception as e:
        print(f"[email] listing_confirmed err: {e}")


async def cancel_listing(user_id: int, listing_id: int) -> dict:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT user_id, status FROM listings WHERE id=?",
            (listing_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="找不到此掛單")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="不是你的掛單")
        if row[1] != "active":
            raise HTTPException(status_code=400, detail=f"狀態 {row[1]} 無法取消")
        await db.execute(
            "UPDATE listings SET status='cancelled' WHERE id=?",
            (listing_id,),
        )
        await db.commit()
    return await get_listing(listing_id)


async def get_listing(listing_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM listings WHERE id=?", (listing_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ============================================================
#   CREATE BID (buy)
# ============================================================

async def create_bid(user_id: int, payload: dict) -> dict:
    set_id = (payload.get("set_id") or "").strip()
    card_number = (payload.get("card_number") or "").strip()
    grade = _validate_grade(payload.get("grade"))
    bid_price = _validate_price(payload.get("bid_price_twd"))
    add_case = 1 if payload.get("add_protective_case") else 0
    case_price = PROTECTIVE_CASE_PRICE_TWD if add_case else 0

    if not set_id or not card_number:
        raise HTTPException(status_code=400, detail="set_id / card_number 必填")
    if not await _card_exists(set_id, card_number):
        raise HTTPException(status_code=404, detail="找不到此卡片")

    expires_at = (datetime.utcnow() + timedelta(days=DEFAULT_EXPIRES_DAYS)).isoformat()
    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO bids
               (user_id, set_id, card_number, grade, bid_price_twd,
                add_protective_case, protective_case_price_twd, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, set_id, card_number, grade, bid_price,
             add_case, case_price, expires_at),
        )
        await db.commit()
        bid_id = cur.lastrowid

    trade = await _match_after_bid(bid_id)
    out = await get_bid(bid_id)
    if trade:
        out["matched_trade"] = trade
    return out


async def cancel_bid(user_id: int, bid_id: int) -> dict:
    async with get_db() as db:
        cur = await db.execute(
            "SELECT user_id, status FROM bids WHERE id=?",
            (bid_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="找不到此出價")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="不是你的出價")
        if row[1] != "active":
            raise HTTPException(status_code=400, detail=f"狀態 {row[1]} 無法取消")
        await db.execute("UPDATE bids SET status='cancelled' WHERE id=?", (bid_id,))
        await db.commit()
    return await get_bid(bid_id)


async def delete_bid_record(user_id: int, bid_id: int) -> dict:
    """硬刪除自己 cancelled / expired / matched 的 bid。active 不能刪。"""
    async with get_db() as db:
        cur = await db.execute("SELECT user_id, status FROM bids WHERE id=?", (bid_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="找不到此出價")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="不是你的出價")
        if row[1] == "active":
            raise HTTPException(status_code=400, detail="進行中的出價需先取消")
        await db.execute("DELETE FROM bids WHERE id=?", (bid_id,))
        await db.commit()
    return {"ok": True, "id": bid_id}


async def delete_listing_record(user_id: int, listing_id: int) -> dict:
    """硬刪除自己 cancelled / expired / sold 的 listing。active 不能刪。"""
    async with get_db() as db:
        cur = await db.execute("SELECT user_id, status FROM listings WHERE id=?", (listing_id,))
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="找不到此掛單")
        if row[0] != user_id:
            raise HTTPException(status_code=403, detail="不是你的掛單")
        if row[1] == "active":
            raise HTTPException(status_code=400, detail="進行中的掛單需先取消")
        await db.execute("DELETE FROM listings WHERE id=?", (listing_id,))
        await db.commit()
    return {"ok": True, "id": listing_id}


async def get_bid(bid_id: int) -> Optional[dict]:
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM bids WHERE id=?", (bid_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ============================================================
#   MATCHING ENGINE
# ============================================================

async def _match_after_listing(listing_id: int) -> Optional[dict]:
    """新 listing 進來：找最高的同卡 active bid，若 bid >= ask → 撮合。
    成交價 = listing.ask_price_twd（賣家要價），符合一般訂單簿規則：先掛者得價"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM listings WHERE id=? AND status='active'", (listing_id,))
        listing = await cur.fetchone()
        if not listing:
            return None

        cur = await db.execute(
            """SELECT * FROM bids
               WHERE set_id=? AND card_number=? AND grade=? AND status='active'
                 AND user_id != ?
                 AND bid_price_twd >= ?
               ORDER BY bid_price_twd DESC, created_at ASC
               LIMIT 1""",
            (listing["set_id"], listing["card_number"], listing["grade"],
             listing["user_id"], listing["ask_price_twd"]),
        )
        bid = await cur.fetchone()

    if not bid:
        return None

    # listing 較晚 → 成交價用 bid 的價（先掛的 bid 拿到他願付的最高價，但實際只付 ask）
    # 對齊一般訂單簿：成交價 = 先進場那邊掛的價
    # 這裡 listing 後進，price = bid_price_twd（先進）。等同：賣家以買家原出價成交，賣家賺價差。
    # 反向情境（_match_after_bid）則 price = ask_price_twd
    return await _create_trade(listing, bid, price=bid["bid_price_twd"])


async def _match_after_bid(bid_id: int) -> Optional[dict]:
    """新 bid 進來：找最低的同卡 active listing，若 ask <= bid → 撮合。
    成交價 = listing.ask_price_twd（先掛的賣家要價）"""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM bids WHERE id=? AND status='active'", (bid_id,))
        bid = await cur.fetchone()
        if not bid:
            return None

        cur = await db.execute(
            """SELECT * FROM listings
               WHERE set_id=? AND card_number=? AND grade=? AND status='active'
                 AND user_id != ?
                 AND ask_price_twd <= ?
               ORDER BY ask_price_twd ASC, created_at ASC
               LIMIT 1""",
            (bid["set_id"], bid["card_number"], bid["grade"],
             bid["user_id"], bid["bid_price_twd"]),
        )
        listing = await cur.fetchone()

    if not listing:
        return None

    return await _create_trade(listing, bid, price=listing["ask_price_twd"])


async def _create_trade(listing, bid, price: float) -> dict:
    """產生 trade 並更新 listing/bid 狀態。成交後非同步通知管理員。"""
    # sqlite3.Row / aiosqlite.Row 轉成 dict，讓後面 .get() 正常用
    if not isinstance(bid, dict):
        bid = dict(bid)
    if not isinstance(listing, dict):
        listing = dict(listing)
    add_case = bid.get("add_protective_case") or 0
    case_price = bid.get("protective_case_price_twd") or 0

    async with get_db() as db:
        cur = await db.execute(
            """INSERT INTO trades
               (listing_id, bid_id, buyer_id, seller_id, set_id, card_number, grade,
                price_twd, fee_twd, add_protective_case, protective_case_price_twd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (listing["id"], bid["id"], bid["user_id"], listing["user_id"],
             listing["set_id"], listing["card_number"], listing["grade"],
             price, round(price * 0.05, 0),  # 5% 平台費（賣家側預扣）
             add_case, case_price),
        )
        trade_id = cur.lastrowid
        await db.execute(
            "UPDATE listings SET status='sold', matched_trade_id=? WHERE id=?",
            (trade_id, listing["id"]),
        )
        await db.execute(
            "UPDATE bids SET status='matched', matched_trade_id=? WHERE id=?",
            (trade_id, bid["id"]),
        )
        await db.commit()

        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
        row = await cur.fetchone()

    trade = dict(row) if row else {}

    # 非同步通知管理員（不阻塞回傳）
    import asyncio as _asyncio
    _asyncio.create_task(_notify_admin_new_trade(trade, listing, bid))

    return trade


async def _notify_admin_new_trade(trade: dict, listing: dict, bid: dict) -> None:
    """成交後：LINE push + in-app notification + 3 封 email（買家/賣家/管理員）。"""
    import httpx
    import aiosqlite as _aio
    from app import email_sender as _es

    trade_id = trade.get("id")
    set_id = trade.get("set_id", "")
    card_number = trade.get("card_number", "")
    price = trade.get("price_twd", 0)
    fee = trade.get("fee_twd", 0)
    grade = trade.get("grade")
    add_case = bool(trade.get("add_protective_case", 0))
    buyer_id = trade.get("buyer_id")
    seller_id = trade.get("seller_id")
    condition = listing.get("condition") or ""

    grade_label = {10: "PSA 10", 9: "PSA 9", 0: "Raw"}.get(grade, str(grade))
    case_note = "（含保護殼 +NT$50）" if add_case else ""
    cond_note = f"  品相：{CONDITIONS_ZH.get(condition, condition)}" if condition else ""

    title = "🛒 新訂單成交通知"
    body = (
        f"Trade #{trade_id}  {set_id} #{card_number}  {grade_label}{cond_note}\n"
        f"成交價：NT${price:,.0f}{case_note}\n"
        f"賣家 ID：{seller_id}  買家 ID：{buyer_id}"
    )
    link_url = f"#/admin/trades/{trade_id}"

    # 寫 admin in-app notification（user_id=0 表示後台）
    try:
        async with _aio.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO notifications (user_id, kind, title, body, link_url, channel, line_pushed)
                   VALUES (0, 'new_trade', ?, ?, ?, 'inapp', 0)""",
                (title, body, link_url),
            )
            await db.commit()
    except Exception as e:
        print(f"[admin_notify] DB write err: {e}")

    # 拉買家 / 賣家 email
    buyer_email = seller_email = None
    try:
        async with _aio.connect(DB_PATH) as db:
            brow = await (await db.execute("SELECT email FROM users WHERE id=?", (buyer_id,))).fetchone()
            srow = await (await db.execute("SELECT email FROM users WHERE id=?", (seller_id,))).fetchone()
        buyer_email = brow[0] if brow else None
        seller_email = srow[0] if srow else None
    except Exception as e:
        print(f"[admin_notify] fetch emails err: {e}")

    # Email：買家成交通知
    if buyer_email:
        try:
            _es.send_trade_matched_buyer(
                to_email=buyer_email,
                set_id=set_id, card_number=card_number, grade=grade,
                price_twd=price, add_protective_case=add_case, trade_id=trade_id,
            )
        except Exception as e:
            print(f"[email] buyer trade err: {e}")

    # Email：賣家成交通知
    if seller_email:
        try:
            _es.send_trade_matched_seller(
                to_email=seller_email,
                set_id=set_id, card_number=card_number, grade=grade,
                price_twd=price, fee_twd=fee, condition=condition or None, trade_id=trade_id,
            )
        except Exception as e:
            print(f"[email] seller trade err: {e}")

    # Email：管理員新訂單
    try:
        _es.send_admin_new_order(
            set_id=set_id, card_number=card_number, grade=grade,
            price_twd=price, buyer_id=buyer_id, seller_id=seller_id,
            add_protective_case=add_case, condition=condition or None, trade_id=trade_id,
        )
    except Exception as e:
        print(f"[email] admin order err: {e}")

    # LINE Push 給管理員
    line_token = _os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if ADMIN_LINE_USER_ID and line_token:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    "https://api.line.me/v2/bot/message/push",
                    headers={
                        "Authorization": f"Bearer {line_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "to": ADMIN_LINE_USER_ID,
                        "messages": [{"type": "text", "text": f"{title}\n\n{body}"}],
                    },
                )
        except Exception as e:
            print(f"[admin_notify] LINE push err: {e}")


# ============================================================
#   USER VIEWS
# ============================================================

async def my_listings(user_id: int, status: Optional[str] = None) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = """SELECT l.*, cl.name AS card_name, cl.name_zh, cl.image_url, cs.name AS set_name
                 FROM listings l
                 LEFT JOIN card_list cl ON l.set_id=cl.set_id AND l.card_number=cl.card_number
                 LEFT JOIN card_sets cs ON l.set_id=cs.set_id
                 WHERE l.user_id=?"""
        args = [user_id]
        if status:
            sql += " AND l.status=?"
            args.append(status)
        sql += " ORDER BY l.id DESC"
        cur = await db.execute(sql, args)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def my_bids(user_id: int, status: Optional[str] = None) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        sql = """SELECT b.*, cl.name AS card_name, cl.name_zh, cl.image_url, cs.name AS set_name
                 FROM bids b
                 LEFT JOIN card_list cl ON b.set_id=cl.set_id AND b.card_number=cl.card_number
                 LEFT JOIN card_sets cs ON b.set_id=cs.set_id
                 WHERE b.user_id=?"""
        args = [user_id]
        if status:
            sql += " AND b.status=?"
            args.append(status)
        sql += " ORDER BY b.id DESC"
        cur = await db.execute(sql, args)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def my_trades(user_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT t.*, cl.name AS card_name, cl.name_zh, cl.image_url, cs.name AS set_name,
                      CASE WHEN t.buyer_id=? THEN 'buy' ELSE 'sell' END AS side
               FROM trades t
               LEFT JOIN card_list cl ON t.set_id=cl.set_id AND t.card_number=cl.card_number
               LEFT JOIN card_sets cs ON t.set_id=cs.set_id
               WHERE t.buyer_id=? OR t.seller_id=?
               ORDER BY t.id DESC""",
            (user_id, user_id, user_id),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
