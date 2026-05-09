"""eBay listing 排程式重驗工具。

目的：
  eBay 賣出/下架的 listing 會在數天/週後被合併到 /p/{epid} 產品目錄頁。
  那目錄頁此刻可能只列 CGC/BGS 在售品，使用者點 PSA 10 紀錄會看到非 PSA 結果。
  本工具定期重 navigate DB 中所有 ebay listing_url，
  發現會 redirect 到 /p/ 的就加入 ebay_blocklist 並從 card_prices 移除。

執行方式：
  CLI 批次（背景跑）：
    python -m app.jobs.revalidate_ebay --limit 200
    python -m app.jobs.revalidate_ebay --recheck-days 30 --limit 500
  指定卡：
    python -m app.jobs.revalidate_ebay --card jp-MEGA-Dream-ex/44
  全部跑（小心，5 萬筆要跑很久）：
    python -m app.jobs.revalidate_ebay --all

策略：
  - 優先驗證從沒驗過的 item_id
  - 其次依 last_checked_at 由舊到新（LRU）
  - 預設 throttle 1.2s/筆，避免被 eBay 風控
  - 並行：用 N 個 page 並行 navigate（預設 3，太多會被擋）
"""

import argparse
import asyncio
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

DB_PATH = Path(__file__).resolve().parent.parent.parent / "cards.db"

# 預設 throttle / 並行參數
DEFAULT_THROTTLE_S = 1.2
DEFAULT_CONCURRENCY = 3
DEFAULT_BATCH_LIMIT = 200


# ==================== Schema ====================

def ensure_tables():
    """確保 ebay_blocklist + ebay_url_check 兩張表存在"""
    with sqlite3.connect(str(DB_PATH)) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS ebay_blocklist (
                item_id TEXT PRIMARY KEY,
                reason TEXT,
                detected_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS ebay_url_check (
                item_id TEXT PRIMARY KEY,
                last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                result TEXT,
                final_url TEXT,
                check_count INTEGER DEFAULT 1
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_url_check_lru ON ebay_url_check(last_checked_at)")
        c.commit()


def _extract_item_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"/itm/(\d+)", url)
    return m.group(1) if m else None


@contextmanager
def _db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ==================== Pick batch ====================

def pick_batch(limit: int = DEFAULT_BATCH_LIMIT,
                recheck_days: Optional[int] = None,
                set_id: Optional[str] = None,
                card_number: Optional[str] = None) -> list:
    """選出要驗證的 item_id。

    優先序：
      1. card_prices 裡有但 ebay_url_check 從未驗過的（NULL last_checked_at）
      2. last_checked_at 早於 recheck_days 天前（如指定）
      3. 否則依 last_checked_at 由舊到新

    回傳：[item_id, ...]
    """
    with _db() as conn:
        # 從 card_prices 先抓所有 distinct ebay item_id
        where = ["source='ebay'", "listing_url LIKE '%/itm/%'"]
        params = []
        if set_id:
            where.append("set_id=?"); params.append(set_id)
        if card_number:
            where.append("card_number=?"); params.append(card_number)
        sql = f"SELECT DISTINCT listing_url FROM card_prices WHERE {' AND '.join(where)}"
        urls = [r["listing_url"] for r in conn.execute(sql, params)]
        all_ids = []
        seen = set()
        for u in urls:
            iid = _extract_item_id(u)
            if iid and iid not in seen:
                seen.add(iid); all_ids.append(iid)

        # 已 blocklisted 的不必重驗（已被擋）
        blocklisted = {r["item_id"] for r in conn.execute("SELECT item_id FROM ebay_blocklist")}

        # 已驗過的 last_checked_at
        last_check = {}
        for r in conn.execute("SELECT item_id, last_checked_at FROM ebay_url_check"):
            last_check[r["item_id"]] = r["last_checked_at"]

        candidates = [iid for iid in all_ids if iid not in blocklisted]

        # priority: never-checked first, then oldest checked
        never = [iid for iid in candidates if iid not in last_check]
        checked = [(iid, last_check[iid]) for iid in candidates if iid in last_check]
        checked.sort(key=lambda x: x[1])  # oldest first

        if recheck_days is not None:
            from datetime import datetime, timedelta
            cutoff = (datetime.now() - timedelta(days=recheck_days)).isoformat()
            checked = [(iid, t) for iid, t in checked if t < cutoff]

        ordered = never + [iid for iid, _ in checked]
        return ordered[:limit] if limit else ordered


# ==================== Check one ====================

def _check_one_sync(context, item_id: str, throttle: float = 0) -> tuple:
    """navigate 一個 /itm/{item_id}，回傳 (result, final_url)。

    result: 'ok' | 'redirect_to_p' | 'access_denied' | 'error'
    """
    page = None
    try:
        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.goto(
            f"https://www.ebay.com/itm/{item_id}",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        page.wait_for_timeout(400)
        final = page.url
        if "/p/" in final:
            return "redirect_to_p", final
        try:
            title = page.title()
            if title and "Access Denied" in title:
                return "access_denied", final
        except Exception:
            pass
        return "ok", final
    except Exception as e:
        return "error", f"err: {str(e)[:200]}"
    finally:
        if page:
            try: page.close()
            except Exception: pass
        if throttle > 0:
            time.sleep(throttle)


def _record_result(item_id: str, result: str, final_url: str):
    """寫 ebay_url_check + （若 redirect）寫 ebay_blocklist + 刪 card_prices"""
    with _db() as conn:
        # upsert ebay_url_check（檢查次數 +1）
        conn.execute("""
            INSERT INTO ebay_url_check (item_id, last_checked_at, result, final_url, check_count)
            VALUES (?, CURRENT_TIMESTAMP, ?, ?, 1)
            ON CONFLICT(item_id) DO UPDATE SET
                last_checked_at = CURRENT_TIMESTAMP,
                result = excluded.result,
                final_url = excluded.final_url,
                check_count = check_count + 1
        """, (item_id, result, final_url))

        # 若 redirect 到 /p/ → 加 blocklist + 刪 card_prices
        if result == "redirect_to_p":
            conn.execute("""
                INSERT OR REPLACE INTO ebay_blocklist (item_id, reason, detected_url)
                VALUES (?, ?, ?)
            """, (item_id, "auto-revalidator: redirects to /p/ catalog", final_url))
            cur = conn.execute(
                "DELETE FROM card_prices WHERE source='ebay' AND listing_url LIKE ?",
                (f"%/itm/{item_id}%",),
            )
            return cur.rowcount
        return 0


# ==================== Main batch loop ====================

def revalidate_batch(limit: int = DEFAULT_BATCH_LIMIT,
                     recheck_days: Optional[int] = None,
                     set_id: Optional[str] = None,
                     card_number: Optional[str] = None,
                     concurrency: int = DEFAULT_CONCURRENCY,
                     throttle: float = DEFAULT_THROTTLE_S,
                     verbose: bool = True) -> dict:
    """跑一輪批次重驗。回傳統計 dict。

    Playwright sync API 不能跨 thread 共用 browser（greenlet 限制），
    所以每個 worker thread 自己啟動獨立的 playwright instance。
    """
    from playwright.sync_api import sync_playwright

    ensure_tables()
    item_ids = pick_batch(limit, recheck_days, set_id, card_number)

    if verbose:
        print(f"[revalidator] 待驗 {len(item_ids)} 個 item_id "
              f"(limit={limit}, recheck_days={recheck_days}, "
              f"set={set_id}, card_num={card_number}, concurrency={concurrency})")

    if not item_ids:
        return {"checked": 0, "ok": 0, "blocked": 0, "deleted_rows": 0,
                "access_denied": 0, "errors": 0, "duration_s": 0}

    start = time.time()
    stats = {"checked": 0, "ok": 0, "blocked": 0, "deleted_rows": 0,
             "access_denied": 0, "errors": 0}
    stats_lock = __import__("threading").Lock()

    def worker(my_ids: list, worker_idx: int):
        # 每個 thread 開自己的 playwright + chromium browser
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    locale="en-US",
                )
                # warm cookies：先 navigate 首頁拿 eBay anti-bot cookies
                try:
                    warm = ctx.new_page()
                    warm.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    )
                    warm.goto("https://www.ebay.com/", wait_until="domcontentloaded", timeout=15000)
                    warm.wait_for_timeout(1500)
                    warm.close()
                except Exception as e:
                    if verbose:
                        print(f"[w{worker_idx}] warm-up 失敗: {e}")
                try:
                    for i, iid in enumerate(my_ids):
                        result, final = _check_one_sync(ctx, iid, throttle=throttle)
                        deleted = _record_result(iid, result, final)
                        with stats_lock:
                            stats["checked"] += 1
                            if result == "ok": stats["ok"] += 1
                            elif result == "redirect_to_p":
                                stats["blocked"] += 1
                                stats["deleted_rows"] += deleted
                            elif result == "access_denied": stats["access_denied"] += 1
                            else: stats["errors"] += 1

                        if verbose and (i + 1) % 20 == 0:
                            print(f"[w{worker_idx}] 進度 {i+1}/{len(my_ids)} | "
                                  f"ok={stats['ok']} blocked={stats['blocked']} "
                                  f"denied={stats['access_denied']} err={stats['errors']}")
                finally:
                    try: ctx.close()
                    except Exception: pass
                    try: browser.close()
                    except Exception: pass
        except Exception as e:
            print(f"[w{worker_idx}] worker 致命錯誤: {e}")

    # 分配（小批次自動調低 concurrency）
    n_workers = min(concurrency, len(item_ids))
    buckets = [[] for _ in range(n_workers)]
    for i, iid in enumerate(item_ids):
        buckets[i % n_workers].append(iid)

    import threading
    threads = []
    for idx, b in enumerate(buckets):
        if not b: continue
        t = threading.Thread(target=worker, args=(b, idx), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    stats["duration_s"] = round(time.time() - start, 1)
    if verbose:
        print(f"[revalidator] 完成 | {stats}")
    return stats


def revalidate_card(set_id: str, card_number: str, **kwargs) -> dict:
    """重驗單張卡的所有 ebay 紀錄"""
    return revalidate_batch(limit=999, set_id=set_id, card_number=card_number, **kwargs)


# ==================== CLI ====================

def main():
    p = argparse.ArgumentParser(description="eBay listing 重驗工具")
    p.add_argument("--limit", type=int, default=DEFAULT_BATCH_LIMIT,
                   help=f"批次上限（預設 {DEFAULT_BATCH_LIMIT}）")
    p.add_argument("--recheck-days", type=int, default=None,
                   help="重驗超過 N 天前驗過的 item（預設只挑沒驗過的）")
    p.add_argument("--card", type=str, default=None,
                   help="指定單張卡：set_id/card_number 例如 jp-MEGA-Dream-ex/44")
    p.add_argument("--all", action="store_true",
                   help="跑全部（忽略 limit）")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    p.add_argument("--throttle", type=float, default=DEFAULT_THROTTLE_S,
                   help=f"每筆間隔秒數（預設 {DEFAULT_THROTTLE_S}）")
    args = p.parse_args()

    set_id = card_num = None
    if args.card:
        if "/" not in args.card:
            print("--card 格式必須是 set_id/card_number", file=sys.stderr)
            sys.exit(2)
        set_id, card_num = args.card.split("/", 1)

    limit = 0 if args.all else args.limit
    revalidate_batch(
        limit=limit if limit > 0 else 999999,
        recheck_days=args.recheck_days,
        set_id=set_id, card_number=card_num,
        concurrency=args.concurrency,
        throttle=args.throttle,
    )


if __name__ == "__main__":
    main()
