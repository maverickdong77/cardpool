"""JP 新卡盒自動補資料模組(2026-05-24)。

入口:
  scrape_set(set_code)         - 完整補一個新卡盒進 DB
  scrape_missing_cards(set_code) - 補既有卡盒漏卡
  daily_backfill_loop()        - 排程入口、跑 pending queue

對應 spec:docs/superpowers/specs/2026-05-24-jp-set-backfill-design.md
"""
import asyncio
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite

DB_PATH = str(Path(__file__).resolve().parent.parent.parent / "cards.db")

# ========== 設定值(調整時改這裡)==========
MAX_SETS_PER_DAY = 2          # 一天最多補幾個卡盒
SLEEP_BETWEEN_CARDS = 2.0     # 卡之間隔幾秒
MAX_RETRIES_PER_CARD = 3      # 每張卡最多重試幾次
RETRY_BACKOFF = [2, 10, 30]   # 重試間隔(秒)
MAX_CONSECUTIVE_FAILS = 5     # 連續失敗多少張就放棄整任務
RUNNING_STUCK_THRESHOLD_MIN = 30  # 「running 但 X 分鐘前」判定死掉
DAILY_RUN_HOUR = 3            # 排程觸發時間(凌晨 03:00)


# ========== 主入口(後續 Task 9-10-12 實作)==========

async def scrape_set(set_code: str, source_hint: str = "manual") -> dict:
    """完整補一個新卡盒進 DB。Task 9 實作。"""
    raise NotImplementedError("Task 9")


async def scrape_missing_cards(set_code: str, source_hint: str = "manual") -> dict:
    """補既有卡盒漏卡。Task 10 實作。"""
    raise NotImplementedError("Task 10")


async def daily_backfill_loop():
    """排程入口、跑 pending queue。Task 12 實作。"""
    raise NotImplementedError("Task 12")


# ========== 內部 helper(Task 3+ 實作)==========

async def allocate_new_pg(set_code: str) -> int:
    """分配新 pg。

    普通擴充包(set_code 不以 -P 結尾):取 1-999 區段現有 max + 1
    Promo set(以 -P 結尾):取 9100+ 區段現有 max + 1(避開既有 9001-9099)
    """
    is_promo = set_code.endswith('-P') or set_code.endswith('-PROMO')
    async with aiosqlite.connect(DB_PATH) as db:
        if is_promo:
            row = await (await db.execute(
                "SELECT MAX(CAST(pg AS INTEGER)) FROM jp_card_list_set "
                "WHERE CAST(pg AS INTEGER) >= 9100"
            )).fetchone()
            current_max = row[0] if row[0] else 9099
            return max(9100, current_max + 1)
        else:
            row = await (await db.execute(
                "SELECT MAX(CAST(pg AS INTEGER)) FROM jp_card_list_set "
                "WHERE CAST(pg AS INTEGER) < 9000"
            )).fetchone()
            current_max = row[0] if row[0] else 0
            return current_max + 1


async def detect_dead_running_jobs():
    """撈 status='running' 但 started_at > 30 min 前的、判定死掉、改回 pending。"""
    cutoff = datetime.now() - timedelta(minutes=RUNNING_STUCK_THRESHOLD_MIN)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE set_backfill_jobs SET status='pending', started_at=NULL "
            "WHERE status='running' AND started_at < ?",
            (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        await db.commit()


async def enqueue_set(set_code: str, source_hint: str) -> Optional[int]:
    """加任務進排隊。已存在 pending/running 就跳過、回 None。"""
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (await db.execute(
            "SELECT id FROM set_backfill_jobs "
            "WHERE set_code = ? AND status IN ('pending', 'running') LIMIT 1",
            (set_code,),
        )).fetchone()
        if existing:
            return None
        cur = await db.execute(
            "INSERT INTO set_backfill_jobs (set_code, source_hint, status) "
            "VALUES (?, ?, 'pending')",
            (set_code, source_hint),
        )
        await db.commit()
        return cur.lastrowid
