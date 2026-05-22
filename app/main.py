import os
import re
import sys
import time
import asyncio
from contextlib import asynccontextmanager

# Windows 需要 ProactorEventLoop 才能支援 subprocess
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
# LINE Bot 用 lazy import：環境裡 linebot.v3 可能在某些情況卡住 import，
# 為了不讓 server 卡 startup，改在 /webhook 端點被打時才載入。
WebhookHandler = None
InvalidSignatureError = Exception
MessageEvent = None
TextMessageContent = None

def _lazy_load_linebot():
    global WebhookHandler, InvalidSignatureError, MessageEvent, TextMessageContent
    if WebhookHandler is not None:
        return True
    try:
        from linebot.v3 import WebhookHandler as _WH
        from linebot.v3.exceptions import InvalidSignatureError as _ISE
        from linebot.v3.webhooks import MessageEvent as _ME, TextMessageContent as _TMC
        WebhookHandler = _WH
        InvalidSignatureError = _ISE
        MessageEvent = _ME
        TextMessageContent = _TMC
        return True
    except Exception as e:
        print(f"[Cardpool] LINE bot lazy load failed: {e}")
        return False
from app.scraper.ebay import get_ebay_prices
from app.scraper.snkrdunk import get_snkrdunk_prices
from app.scraper.pokemon_tcg import search_pokemon_card
from app.scraper.tcgcollector import get_sets, get_set_cards
from app.models.card_sets import get_all_jp_sets, get_all_en_sets, get_set_by_id
from app.database import (
    get_card_by_psa,
    search_cards_by_name,
    get_latest_prices,
    get_ebay_prices as get_db_ebay_prices,
    get_snkrdunk_prices as get_db_snkrdunk_prices,
    get_all_card_sets,
    get_cards_by_set,
    search_cards_in_list,
    get_card_set_stats,
)
from app.set_categories import get_all_categories, get_set_order
from app.job_controller import jobs as job_ctrl
from app import auth as auth_mod
from app import marketplace as mp

# 載入環境變數
load_dotenv()

# 資料庫路徑
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cards.db")


async def _ebay_revalidator_loop(job=None):
    """連續跑 eBay 重驗批次（高速 mode：3 天內掃完 5 萬筆）。

    參數（預設）：
      - concurrency=5（5 個 chromium worker 並行；每個 ~250MB）
      - throttle=0.8s/筆
      - batch=500/輪
      - cooldown 60s
      - 風控保護：access_denied 比率 > 35% → 冷卻 5 分鐘

    預估速度：5 worker × ~1.6s/item / 5 = ~0.32s/item wall clock
              500 筆/批 ≈ 3 分鐘 + 60s cool = 4 分鐘/批
              50,000 筆 / (500/批) = 100 批 × 4 分鐘 = ~6.7 小時
              即使遇到風控冷卻翻倍：~14 小時 → 仍可一夜跑完
    """
    import asyncio as _aio
    from app.jobs.revalidate_ebay import revalidate_batch

    # 環境變數可關掉自動排程（debug / 測試時用）
    if os.getenv("CARDPOOL_DISABLE_REVALIDATOR") == "1":
        print("[scheduler] 已停用 (CARDPOOL_DISABLE_REVALIDATOR=1)")
        if job: job.set_status("disabled", "由環境變數停用")
        return

    if job: await job.sleep_or_signal(30)
    else: await _aio.sleep(30)
    cooldown_s = 60
    while True:
        try:
            if job:
                await job.wait_if_paused()
                if job.is_stopped: break
                job.set_status("running", f"執行 eBay 重驗批次 (cooldown={cooldown_s}s)")
            print(f"[scheduler] 啟動 eBay 重驗批次 (cooldown={cooldown_s}s)")
            _t0 = time.time()
            stats = await _aio.to_thread(
                revalidate_batch, 500, None, None, None, 5, 0.8, True
            )
            _bd = round(time.time() - _t0, 2)
            print(f"[scheduler] 完成：{stats}  ({_bd}s)")
            if job: job.bump_batch(last_batch=stats, batch_duration_s=_bd)
            checked = stats.get("checked", 0)
            denied = stats.get("access_denied", 0)
            # 自適應：access_denied > 35% 認為被風控，冷卻翻倍（最多 30 分鐘）
            if checked > 0 and denied / checked > 0.35:
                cooldown_s = min(cooldown_s * 2, 1800)
                print(f"[scheduler] ⚠ 風控偵測 (denied {denied}/{checked})，cooldown 拉到 {cooldown_s}s")
                if job: job.set_status("running", f"⚠ 風控偵測，冷卻 {cooldown_s}s")
            else:
                cooldown_s = max(60, cooldown_s // 2)  # 沒事就慢慢恢復到 60s
            # 沒東西要驗 → 進入 long sleep（每天一次）
            if checked == 0:
                print("[scheduler] 無待驗項目，進入每日模式")
                if job:
                    job.set_status("running", "無待驗項目，等 24h")
                    await job.sleep_or_signal(24 * 60 * 60)
                else:
                    await _aio.sleep(24 * 60 * 60)
                continue
        except _aio.CancelledError:
            print("[scheduler] cancelled")
            raise
        except Exception as e:
            print(f"[scheduler] 錯誤：{e}")
            if job:
                job.error = str(e)
                job.set_status("running", f"錯誤：{e}")
            cooldown_s = min(cooldown_s * 2, 1800)
        if job:
            job.set_status("running", f"冷卻中 ({cooldown_s}s)")
            await job.sleep_or_signal(cooldown_s)
        else:
            await _aio.sleep(cooldown_s)


async def _hot_sets_loop(job=None):
    """高頻刷新「最新 N 個 set」的卡（新彈 PSA10 量大，需要更頻繁更新）。

    每 6 小時跑一次，只跑 jp 最新 80 個 set + en 最新 80 個 set 內的卡。
    比 24h 的 daily refresh 更頻繁，覆蓋熱門新彈。
    """
    import asyncio as _aio
    import sqlite3
    from app.jobs.sync_all_cards import sync_batch

    if os.getenv("CARDPOOL_DISABLE_HOT_SETS") == "1":
        print("[hot-sets] 已停用")
        if job: job.set_status("disabled", "由環境變數停用")
        return

    if job: await job.sleep_or_signal(900)
    else: await _aio.sleep(900)

    while True:
        try:
            if job:
                await job.wait_if_paused()
                if job.is_stopped: break
            # 撈最新 80 個 jp set + 80 個 en set 的 set_id list
            with sqlite3.connect(DB_PATH) as conn:
                jp_sets = [r[0] for r in conn.execute(
                    "SELECT set_id FROM card_sets WHERE set_id LIKE 'jp-%' ORDER BY id DESC LIMIT 80"
                )]
                en_sets = [r[0] for r in conn.execute(
                    "SELECT set_id FROM card_sets WHERE set_id LIKE 'en-%' ORDER BY id DESC LIMIT 80"
                )]
            target_sets = jp_sets + en_sets
            print(f"[hot-sets] 開始刷新最新 {len(target_sets)} 個 set 內的卡")
            _round_start = time.time()
            if job:
                job.set_status("running", "round 開始",
                               round_started_at=_round_start,
                               round_total=len(target_sets), round_idx=0)
            # 對每個 set 跑一次 refresh（stale_days=0 強制全部更新）
            total = {"cards": 0, "ebay_new": 0, "snkr_new": 0, "errors": 0}
            for idx, sid in enumerate(target_sets, 1):
                if job:
                    await job.wait_if_paused()
                    if job.is_stopped: break
                    job.set_status("running",
                                   f"刷新 set {sid} ({idx}/{len(target_sets)})",
                                   round_idx=idx)
                try:
                    s = await _aio.to_thread(
                        sync_batch, "all", 0, 999999, sid, 3, 0.5, False
                    )
                    for k in total: total[k] += s.get(k, 0)
                except Exception as e:
                    print(f"[hot-sets] {sid} 錯誤: {e}")
            _round_dur = round(time.time() - _round_start, 1)
            print(f"[hot-sets] 一輪完成：{total} ({_round_dur}s)")
            if job:
                job.bump_batch(last_round=total,
                               last_round_duration_s=_round_dur)
                if job.is_stopped: break
        except _aio.CancelledError:
            raise
        except Exception as e:
            print(f"[hot-sets] 致命錯誤：{e}")
            if job:
                job.error = str(e)
                job.set_status("running", f"錯誤：{e}")
        # 每 6 小時跑一次
        if job:
            job.set_status("running", "一輪完成，等 6h")
            await job.sleep_or_signal(6 * 60 * 60)
        else:
            await _aio.sleep(6 * 60 * 60)


async def _sync_all_loop(job=None):
    """每日全卡 sync 排程（涵蓋 card_list 中每一張卡）。

    第一輪：跑 backfill mode（沒抓過的 ~25k 張卡）
    之後每 24h：跑 refresh mode（last_sync 超過 1 天的卡）

    每張卡同時跑 eBay + SNKR（en 卡只跑 eBay）。
    一輪預估：
      - backfill 25k 張 × ~6s avg / 3 worker = ~14 小時
      - daily refresh 17k 已抓 × ~6s / 3 worker = ~10 小時
    """
    import asyncio as _aio
    from app.jobs.sync_all_cards import sync_batch

    if os.getenv("CARDPOOL_DISABLE_SYNC_ALL") == "1":
        print("[sync-all-scheduler] 已停用")
        if job: job.set_status("disabled", "由環境變數停用")
        return

    if job: await job.sleep_or_signal(300)
    else: await _aio.sleep(300)

    # === 第一輪：backfill 沒抓過的 ===
    print("[sync-all-scheduler] 開始首輪 backfill（補抓全部沒資料的卡）")
    while True:
        try:
            if job:
                await job.wait_if_paused()
                if job.is_stopped: break
                job.set_status("running", "backfill 中（補抓未抓過的卡）")
            _t0 = time.time()
            stats = await _aio.to_thread(
                sync_batch, "backfill", 1, 500, None, 5, 0.3, True
            )
            _bd = round(time.time() - _t0, 2)
            print(f"[sync-all-scheduler] backfill batch 完成：{stats} ({_bd}s)")
            if job: job.bump_batch(last_batch=stats, mode="backfill",
                                   batch_duration_s=_bd)
            if stats.get("cards", 0) == 0:
                print("[sync-all-scheduler] backfill 完成（無剩餘）→ 進入 refresh 模式")
                break
            if job: await job.sleep_or_signal(60)
            else: await _aio.sleep(60)
        except _aio.CancelledError:
            raise
        except Exception as e:
            print(f"[sync-all-scheduler] backfill 錯誤：{e}")
            if job:
                job.error = str(e)
                await job.sleep_or_signal(300)
            else: await _aio.sleep(300)

    if job and job.is_stopped: return

    # === 後續：每日 refresh stale 卡 ===
    while True:
        try:
            if job:
                await job.wait_if_paused()
                if job.is_stopped: break
            print("[sync-all-scheduler] 每日 refresh stale 卡")
            # 每輪 batch 500 張，分批跑直到沒 stale 卡
            while True:
                if job:
                    await job.wait_if_paused()
                    if job.is_stopped: break
                    job.set_status("running", "refresh 中（更新 stale 卡）")
                _t0 = time.time()
                stats = await _aio.to_thread(
                    sync_batch, "refresh", 1, 500, None, 5, 0.3, True
                )
                _bd = round(time.time() - _t0, 2)
                print(f"[sync-all-scheduler] refresh batch：{stats} ({_bd}s)")
                if job: job.bump_batch(last_batch=stats, mode="refresh",
                                       batch_duration_s=_bd)
                if stats.get("cards", 0) == 0:
                    break
                if job: await job.sleep_or_signal(60)
                else: await _aio.sleep(60)
            print("[sync-all-scheduler] 一輪 refresh 完成，等 24h")
            if job and job.is_stopped: break
        except _aio.CancelledError:
            raise
        except Exception as e:
            print(f"[sync-all-scheduler] refresh 錯誤：{e}")
            if job:
                job.error = str(e)
                job.set_status("running", f"錯誤：{e}")
        if job:
            job.set_status("running", "一輪完成，等 24h")
            await job.sleep_or_signal(24 * 60 * 60)
        else:
            await _aio.sleep(24 * 60 * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理"""
    print("[Cardpool] Starting...")

    # 確保排程要用的表已建立
    try:
        from app.jobs.revalidate_ebay import ensure_tables
        ensure_tables()
    except Exception as e:
        print(f"[Cardpool] ensure_tables fail: {e}")

    # 確保 watchlist 表已建立
    try:
        import sqlite3
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.execute("""CREATE TABLE IF NOT EXISTS watchlists (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       set_id TEXT NOT NULL,
                       card_number TEXT NOT NULL,
                       created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       UNIQUE(user_id, set_id, card_number)
                     )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_watchlists_user ON watchlists(user_id, created_at DESC)")
        c.commit(); c.close()
        print("[Cardpool] watchlists table ready")
    except Exception as e:
        print(f"[Cardpool] watchlists table init fail: {e}")

    # 註冊排程 job
    # CARDPOOL_DISABLE_JOBS=1 → 只 register 不 start（純 API 模式，省 RAM）
    disable_jobs = os.getenv("CARDPOOL_DISABLE_JOBS", "").lower() in ("1", "true", "yes")
    ebay_job = job_ctrl.register("ebay-revalidator", "eBay 重驗排程", factory=_ebay_revalidator_loop)
    sync_job = job_ctrl.register("sync-all", "全卡每日 sync（backfill + refresh）", factory=_sync_all_loop)
    hot_job = job_ctrl.register("hot-sets", "熱門 set 每 6h 高頻刷新", factory=_hot_sets_loop)

    # 手動觸發的 backfill job（不會自動啟動，user 從 /admin/jobs 點 start）
    from app.jobs.backfill_prices import backfill_loop as _backfill_loop
    job_ctrl.register("backfill-prices", "缺價卡片 backfill (手動)", factory=_backfill_loop)

    if disable_jobs:
        print("[Cardpool] CARDPOOL_DISABLE_JOBS=1 → 排程 job 不啟動（純 API 模式）")
    else:
        ebay_job.start()
        sync_job.start()
        hot_job.start()

    yield

    # 關閉
    for j in job_ctrl.jobs.values():
        j.stop()
    for j in job_ctrl.jobs.values():
        if j._task is not None:
            try:
                await j._task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
    try:
        from app.scraper.browser_pool import close_browser
        close_browser()
    except Exception as e:
        print(f"[Cardpool] Browser close failed: {e}")
    print("[Cardpool] Shutdown")


app = FastAPI(
    title="Cardpool Price Searching",
    description="Pokemon PSA Card Price Search API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — 讓「卡波」前端（不同 origin）能呼叫本 API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 掛載靜態檔案
static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# LINE Webhook Handler — lazy init
handler = None

def _get_handler():
    """lazy 取 WebhookHandler（避免 server startup 時 linebot import 卡死）"""
    global handler
    if handler is not None:
        return handler
    if not _lazy_load_linebot():
        return None
    handler = WebhookHandler(os.getenv("LINE_CHANNEL_SECRET", ""))

    @handler.add(MessageEvent, message=TextMessageContent)
    def handle_text_message(event):
        from app.line_bot import handle_message
        asyncio.create_task(handle_message(event))

    return handler


# ==================== LINE Webhook ====================

@app.post("/webhook")
async def line_webhook(request: Request):
    """LINE Webhook 端點"""
    h = _get_handler()
    if h is None:
        raise HTTPException(status_code=503, detail="LINE bot module unavailable")
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    body_text = body.decode("utf-8")

    try:
        h.handle(body_text, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    return JSONResponse(content={"status": "ok"})


# ==================== API 端點 ====================

@app.get("/")
async def root():
    """首頁 - 重定向到 LIFF 頁面"""
    liff_path = os.path.join(static_path, "liff", "index.html")
    if os.path.exists(liff_path):
        with open(liff_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    return {"message": "Cardpool Price Searching API is running", "docs": "/docs"}


@app.get("/liff")
async def liff_page():
    """LIFF 卡片搜尋頁面"""
    liff_path = os.path.join(static_path, "liff", "index.html")
    if os.path.exists(liff_path):
        with open(liff_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="LIFF page not found")


@app.get("/liff/card")
async def liff_card_page():
    """LIFF 卡片詳情頁面"""
    card_path = os.path.join(static_path, "liff", "card.html")
    if os.path.exists(card_path):
        with open(card_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="Card page not found")


@app.get("/api/health")
async def health_check():
    """健康檢查"""
    return {"status": "healthy", "service": "cardpool", "version": "multilang-v1"}


@app.get("/api/search/psa/{cert_number}")
async def search_by_psa_api(cert_number: str):
    """用 PSA 編號搜尋 API"""
    # 並行查詢 eBay 和 SNKRDUNK
    ebay_task = get_ebay_prices(cert_number, is_cert=True)
    snkrdunk_task = get_snkrdunk_prices(cert_number, is_cert=True)

    ebay_results, snkrdunk_results = await asyncio.gather(ebay_task, snkrdunk_task)

    return {
        "query": cert_number,
        "query_type": "psa_cert",
        "ebay": ebay_results,
        "snkrdunk": snkrdunk_results,
    }


@app.get("/api/search/name/{card_name}")
async def search_by_name_api(card_name: str, grade: str = "10"):
    """用卡片名稱搜尋 API"""
    # 並行執行 eBay 和 SNKRDUNK 搜尋
    ebay_task = get_ebay_prices(card_name, is_cert=False, grade=grade)
    snkrdunk_task = get_snkrdunk_prices(card_name, is_cert=False, grade=grade)

    ebay_results, snkrdunk_results = await asyncio.gather(ebay_task, snkrdunk_task)

    return {
        "query": card_name,
        "query_type": "card_name",
        "grade": grade,
        "ebay": ebay_results,
        "snkrdunk": snkrdunk_results,
    }


@app.get("/api/card/{cert_number}")
async def get_card_api(cert_number: str):
    """取得卡片詳細資訊"""
    card = await get_card_by_psa(cert_number)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")

    prices = await get_latest_prices(cert_number)
    return {
        "card": card,
        "latest_prices": prices,
    }


@app.get("/api/card/{cert_number}/history")
async def get_price_history_api(cert_number: str, limit: int = 20):
    """取得價格歷史"""
    ebay_history = await get_db_ebay_prices(cert_number, limit)
    snkrdunk_history = await get_db_snkrdunk_prices(cert_number, limit)

    return {
        "cert_number": cert_number,
        "ebay_history": ebay_history,
        "snkrdunk_history": snkrdunk_history,
    }


@app.get("/api/pokemon/cards/{name}")
async def search_pokemon_cards_api(name: str, limit: int = 20):
    """搜尋官方卡片資料（包含高品質圖片）"""
    cards = await search_pokemon_card(name, limit=limit)
    return {
        "query": name,
        "cards": cards,
    }


@app.get("/api/pokemon/search/{name}")
async def search_pokemon_with_prices(name: str, limit: int = 10):
    """搜尋卡片 + 價格（整合官方圖片和成交價格）"""
    import asyncio

    # 同時搜尋官方卡片和價格
    pokemon_task = search_pokemon_card(name, limit=limit)
    ebay_task = get_ebay_prices(name, is_cert=False)

    pokemon_cards, ebay_results = await asyncio.gather(pokemon_task, ebay_task)

    # 整合結果
    results = []
    for card in pokemon_cards:
        # 嘗試匹配價格資料
        matched_prices = []
        card_name_lower = card["name"].lower()

        for ebay in ebay_results:
            title_lower = ebay.get("listing_title", "").lower()
            # 簡單匹配：卡片名稱出現在標題中
            if card_name_lower in title_lower:
                matched_prices.append(ebay)

        results.append({
            "card": card,
            "image_url": card.get("image_large") or card.get("image_small"),
            "prices": matched_prices[:3],  # 最多 3 筆價格
            "avg_price_twd": sum(p["price_twd"] for p in matched_prices[:3]) / len(matched_prices[:3]) if matched_prices else None,
        })

    return {
        "query": name,
        "results": results,
        "total_cards": len(pokemon_cards),
        "total_prices": len(ebay_results),
    }


# ==================== 系列 API ====================

@app.get("/api/sets/{language}")
async def get_card_sets_api(language: str = "jp"):
    """取得卡片系列列表"""
    import httpx

    if language == "en":
        # 英文：使用 Pokemon TCG API
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.pokemontcg.io/v2/sets",
                    params={"orderBy": "-releaseDate", "pageSize": "50"},
                    timeout=30.0
                )
                data = response.json()

                sets = []
                for s in data.get("data", []):
                    sets.append({
                        "id": s.get("id"),
                        "name": s.get("name"),
                        "series": s.get("series"),
                        "total_cards": s.get("total"),
                        "release_date": s.get("releaseDate"),
                        "logo": s.get("images", {}).get("logo"),
                        "symbol": s.get("images", {}).get("symbol"),
                        "language": "en",
                    })
                return {"sets": sets, "source": "pokemontcg", "language": "en"}
        except Exception as e:
            return {"sets": get_all_en_sets(), "source": "local", "language": "en"}

    elif language == "jp":
        # 日文：從 TCG Collector 取得
        try:
            sets = await get_sets("jp")
            if sets:
                return {"sets": sets, "source": "tcgcollector", "language": "jp"}
        except Exception as e:
            print(f"TCG Collector 失敗: {e}")

        # 備用：本地資料
        jp_sets = get_all_jp_sets()
        return {"sets": jp_sets, "source": "local", "language": "jp"}

    elif language == "zh":
        # 中文：暫時返回空（之後建立）
        return {"sets": [], "source": "local", "language": "zh", "message": "中文系列建置中"}


@app.get("/api/sets/{language}/live")
async def get_card_sets_live_api(language: str = "jp"):
    """從 TCG Collector 取得最新系列列表"""
    sets = await get_sets(language)
    return {"sets": sets, "source": "tcgcollector"}


@app.get("/api/set/{set_id}/cards")
async def get_set_cards_api(set_id: str):
    """取得特定系列的卡片列表"""
    set_info = get_set_by_id(set_id)
    if not set_info:
        raise HTTPException(status_code=404, detail="Set not found")

    # 從 TCG Collector 取得卡片
    set_url = f"https://www.tcgcollector.com/cards/{set_id.lower()}-japanese"
    cards = await get_set_cards(set_url)

    return {
        "set": set_info,
        "cards": cards,
    }


# ==================== 卡表 API（從資料庫）====================

@app.get("/api/cardlist/sets")
async def get_cardlist_sets(language: str = "jp"):
    """取得資料庫中的系列列表"""
    sets = await get_all_card_sets(language)
    stats = await get_card_set_stats()
    return {
        "sets": sets,
        "stats": stats,
        "language": language,
    }


@app.get("/api/cardlist/sets/{set_id}")
async def get_cardlist_by_set(set_id: str):
    """取得特定系列的卡片"""
    cards = await get_cards_by_set(set_id)
    if not cards:
        raise HTTPException(status_code=404, detail="Set not found or empty")
    return {
        "set_id": set_id,
        "cards": cards,
        "total": len(cards),
    }


# artofpkm 抓取單一 set 頁：logo banner + 發售日期
_ARTOFPKM_LOGO_LOCK = asyncio.Lock()
_ARTOFPKM_INFLIGHT: dict[int, asyncio.Task] = {}

_ARTOFPKM_MONTH = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
    "jul": "07", "aug": "08", "sep": "09", "sept": "09", "oct": "10", "nov": "11", "dec": "12",
}


def _parse_artofpkm_date(text: str) -> str | None:
    """artofpkm 的日期格式如 'Jan. 20, 2023' → '2023-01-20'"""
    m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+(\d{1,2}),\s*(\d{4})', text)
    if not m:
        return None
    mon = _ARTOFPKM_MONTH.get(m.group(1).lower())
    if not mon:
        return None
    return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"


async def _scrape_artofpkm_meta(art_id: int) -> dict:
    """抓 artofpkm.com/sets/{art_id}：{logo_url, release_date}"""
    import httpx
    url = f"https://www.artofpkm.com/sets/{art_id}"
    out = {"logo_url": None, "release_date": None}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 cardpool"})
            if r.status_code != 200:
                return out
            text = r.text
            m = re.search(r'(https://www\.artofpkm\.com/rails/active_storage/[^\s"\'<>]+)', text)
            if m:
                out["logo_url"] = m.group(1)
            else:
                m = re.search(r'(/rails/active_storage/[^\s"\'<>]+)', text)
                if m:
                    out["logo_url"] = "https://www.artofpkm.com" + m.group(1)
            out["release_date"] = _parse_artofpkm_date(text)
    except Exception as e:
        print(f"[artofpkm meta] {art_id} fail: {e}")
    return out


async def _scrape_artofpkm_logo(art_id: int) -> str | None:
    """相容呼叫：只回 logo_url，但同時把 release_date 寫回 DB"""
    meta = await _scrape_artofpkm_meta(art_id)
    if meta["release_date"]:
        try:
            import sqlite3
            c = sqlite3.connect(DB_PATH)
            c.execute("UPDATE artofpkm_sets SET release_date = ? WHERE id = ? AND (release_date IS NULL OR release_date = '')",
                      (meta["release_date"], art_id))
            c.commit(); c.close()
        except Exception:
            pass
    return meta["logo_url"]


_TRENDING_CACHE = {"data": None, "ts": 0}


async def _scrape_snkr_hottest_apparels(limit: int = 60) -> list[int]:
    """從 SNKR 熱門搜尋頁解析 apparel_id 清單（已按熱度排序）。"""
    import re
    import httpx
    url = "https://snkrdunk.com/search?keywords=%E3%83%88%E3%83%AC%E3%82%AB&searchCategoryIds=6&sort=hottest&page=1"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 Chrome/124"})
            if r.status_code != 200:
                return []
            ids = re.findall(r"/apparels/(\d+)", r.text)
            seen = list(dict.fromkeys(ids))[:limit]
            return [int(x) for x in seen]
    except Exception as e:
        print(f"[trending] snkr scrape err: {e}")
        return []


def _is_individual_card(full_title: str) -> bool:
    """判斷 SNKR 商品是個別卡 (True) 還是整盒/組 (False)

    個別卡的 SNKR title 都有 `[set_code N]` 或 `[set_code N/T]` 格式（卡號）。
    BOX / 組 / 商品 沒有這個括號卡號標記。
    """
    import re
    if not full_title:
        return False
    # [M2 013/080] / [M-P 020] / [S-P 208] / [s12a 212/172] / [SV-P 260] / [MC 765/742] 等
    return bool(re.search(r"\[\s*[\w-]+\s+\d+(?:/\d+)?\s*\]", full_title))


def _parse_snkr_full_title(full_title: str) -> dict:
    """從 SNKR full_title 解析 card_number, set_code, set_name_jp"""
    import re
    out = {"card_number": None, "set_code": None, "set_name_jp": None, "rarity": None}
    m = re.search(r"\[\s*([\w-]+)\s+(\d+)(?:/\d+)?\s*\]", full_title)
    if m:
        out["set_code"] = m.group(1)
        out["card_number"] = str(int(m.group(2)))
    # 取「卡號 後面那個 ()」內的內容當主要 set 描述
    after_brackets = full_title.split("]", 1)[-1] if "]" in full_title else full_title
    paren_m = re.search(r"\(([^)]+)\)", after_brackets)
    if paren_m:
        in_paren = paren_m.group(1)
        # 「XX」一般是 set 名；取第一個 「」
        sn_m = re.search(r"「([^」]+)」", in_paren)
        if sn_m:
            out["set_name_jp"] = sn_m.group(1)
        else:
            # 無 「」 → 整個 () 內容當 set name（罕見）
            out["set_name_jp"] = in_paren.strip()
    return out


def _normalize_jp_name(s: str) -> str:
    """正規化 set name：去空白、Pokemon↔Pokémon、メガ↔MEGA、ex↔EX"""
    if not s:
        return ""
    s = s.replace(" ", "").replace("　", "")
    s = s.replace("Pokémon", "Pokemon")
    return s.lower()


@app.get("/api/stats/trending")
async def get_trending_cards(window: str = "7d", limit: int = 20):
    """首頁熱門卡 — 以實際成交量排名。

    主軸（2026-05-08 改）：直接從 card_volume_stats 取 ORDER BY sales_7d/30d/all DESC。
    這個表在 sync_all.py / sync_card_prices_api 的每張卡 sync 結尾被更新。

    舊邏輯（爬 SNKR hottest HTML 頁）已棄用 — 那是模糊的「熱搜」訊號、
    跟真實買家成交脫鉤。
    """
    import aiosqlite
    import time as _t

    # 5 分鐘 cache（query 仍便宜，但首頁高流量時還是快）
    CACHE_TTL = 300
    cache_key = f"vol:{window}:{limit}"
    if _TRENDING_CACHE.get("key") == cache_key and (_t.time() - _TRENDING_CACHE["ts"]) < CACHE_TTL:
        return {"trending": _TRENDING_CACHE["data"], "window": window, "source": "volume", "cached": True}

    sales_col = {"7d": "sales_7d", "30d": "sales_30d", "all": "sales_all"}.get(window, "sales_7d")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(f"""
            SELECT v.set_id, v.card_number,
                   v.{sales_col} AS sales,
                   v.sales_7d, v.sales_30d, v.sales_all,
                   v.last_sale_at,
                   cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity,
                   cl.snkr_listing_count, cl.snkr_min_price_jpy,
                   cs.name AS set_name, cs.name_zh AS set_name_zh, cs.name_jp AS set_name_jp,
                   (SELECT ROUND(AVG(price_twd), 0) FROM card_prices cp
                     WHERE cp.set_id=v.set_id AND cp.card_number=v.card_number
                       AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-{ {'7d':7,'30d':30,'all':99999}[window] if window in ('7d','30d','all') else 7 } days')
                       AND cp.price_twd IS NOT NULL) AS avg_price
              FROM card_volume_stats v
              LEFT JOIN card_list cl ON cl.set_id=v.set_id AND cl.card_number=v.card_number
              LEFT JOIN card_sets cs ON cs.set_id=v.set_id
             WHERE v.{sales_col} > 0
             ORDER BY v.{sales_col} DESC
             LIMIT ?
        """, (limit,))).fetchall()

    out = [dict(r) for r in rows]
    _TRENDING_CACHE["key"] = cache_key
    _TRENDING_CACHE["data"] = out
    _TRENDING_CACHE["ts"] = _t.time()
    return {"trending": out, "window": window, "source": "volume"}


# ---- 舊的 SNKR-hottest 邏輯保留為 fallback（不再為主用，前端已不呼叫）----
async def _trending_legacy_snkr_hottest(window: str, limit: int):
    import aiosqlite
    apparel_ids = await _scrape_snkr_hottest_apparels(limit * 3)
    if not apparel_ids:
        days = {"7d": 7, "30d": 30, "all": 99999}.get(window, 7)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(f"""
                SELECT cp.set_id, cp.card_number, COUNT(*) AS sales,
                       ROUND(AVG(cp.price_twd), 0) AS avg_price,
                       MAX(cp.price_twd) AS max_price, MIN(cp.price_twd) AS min_price,
                       cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity,
                       cs.name AS set_name, cs.name_zh AS set_name_zh
                FROM card_prices cp
                LEFT JOIN card_list cl ON cl.set_id=cp.set_id AND cl.card_number=cp.card_number
                LEFT JOIN card_sets cs ON cs.set_id=cp.set_id
                WHERE COALESCE(cp.sale_date, cp.created_at) >= date('now', '-{days} days')
                  AND cp.price_twd IS NOT NULL
                GROUP BY cp.set_id, cp.card_number
                HAVING sales >= 3
                ORDER BY sales DESC LIMIT ?
            """, (limit,))).fetchall()
        return {"trending": [dict(r) for r in rows], "window": window, "source": "local_fallback"}

    # apparel_id → (full_title, set_id, card_number) via mapping
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ",".join("?" * len(apparel_ids))
        mapping_rows = await (await db.execute(
            f"SELECT apparel_id, full_title, set_name_jp, card_number FROM snkrdunk_mapping WHERE apparel_id IN ({placeholders})",
            apparel_ids,
        )).fetchall()
        mapping = {r["apparel_id"]: r for r in mapping_rows}

        out = []
        seen_card_keys = set()
        for aid in apparel_ids:
            if aid not in mapping:
                continue
            m = mapping[aid]
            if not _is_individual_card(m["full_title"] or ""):
                continue  # 整盒商品跳過
            # 取 set_name_jp / card_number：若 mapping 欄位是 NULL，從 full_title 解析
            sn_jp = m["set_name_jp"]
            sn_num = m["card_number"]
            if not sn_jp or not sn_num:
                parsed = _parse_snkr_full_title(m["full_title"] or "")
                sn_jp = sn_jp or parsed.get("set_name_jp")
                sn_num = sn_num or parsed.get("card_number")
            if not sn_num:
                continue

            # 反查我們的 set_id：用 snkr 的 set_name_jp，但加 メガ↔MEGA / ex↔EX 等變體
            our_card = None
            if sn_jp:
                from app.scraper.snkrdunk_http import _set_name_variants
                variants = _set_name_variants(sn_jp)
                # 加 Pokémon ↔ Pokemon 等 normalize
                extras = []
                for v in variants:
                    if "Pokemon" in v:
                        extras.append(v.replace("Pokemon", "Pokémon"))
                    if "Pokémon" in v:
                        extras.append(v.replace("Pokémon", "Pokemon"))
                variants = list(dict.fromkeys(variants + extras))
                for v in variants:
                    cur = await db.execute("""
                        SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity,
                               cs.name AS set_name, cs.name_zh AS set_name_zh,
                               (SELECT COUNT(*) FROM card_prices cp
                                  WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                    AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')
                                    AND cp.price_twd IS NOT NULL) AS sales,
                               (SELECT ROUND(AVG(price_twd),0) FROM card_prices cp
                                  WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                    AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')) AS avg_price
                        FROM card_list cl
                        LEFT JOIN card_sets cs ON cs.set_id=cl.set_id
                        WHERE cl.set_id LIKE 'jp-%'
                          AND cl.card_number = CAST(CAST(? AS INTEGER) AS TEXT)
                          AND cs.name_jp = ?
                        LIMIT 1
                    """, (sn_num, v))
                    our_card = await cur.fetchone()
                    if our_card:
                        break

                # fallback：模糊比對（主要對映 ex 大小寫差異）
                if not our_card:
                    for v in variants:
                        cur2 = await db.execute("""
                            SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity,
                                   cs.name AS set_name, cs.name_zh AS set_name_zh,
                                   (SELECT COUNT(*) FROM card_prices cp
                                      WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                        AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')) AS sales,
                                   (SELECT ROUND(AVG(price_twd),0) FROM card_prices cp
                                      WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                        AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')) AS avg_price
                            FROM card_list cl
                            LEFT JOIN card_sets cs ON cs.set_id=cl.set_id
                            WHERE cl.set_id LIKE 'jp-%'
                              AND cl.card_number = CAST(CAST(? AS INTEGER) AS TEXT)
                              AND cs.name_jp LIKE '%' || ? || '%'
                            LIMIT 1
                        """, (sn_num, v))
                        our_card = await cur2.fetchone()
                        if our_card:
                            break

                # 最後 fallback：normalized 比對（去空白、é/é、大小寫）
                if not our_card:
                    target_norm = _normalize_jp_name(sn_jp)
                    cur3 = await db.execute("""
                        SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity,
                               cs.name AS set_name, cs.name_zh AS set_name_zh, cs.name_jp AS cs_name_jp,
                               (SELECT COUNT(*) FROM card_prices cp
                                  WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                    AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')) AS sales,
                               (SELECT ROUND(AVG(price_twd),0) FROM card_prices cp
                                  WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                                    AND COALESCE(cp.sale_date, cp.created_at) >= date('now','-7 days')) AS avg_price
                        FROM card_list cl
                        LEFT JOIN card_sets cs ON cs.set_id=cl.set_id
                        WHERE cl.set_id LIKE 'jp-%'
                          AND cl.card_number = CAST(CAST(? AS INTEGER) AS TEXT)
                          AND cs.name_jp IS NOT NULL
                        LIMIT 200
                    """, (sn_num,))
                    candidates = await cur3.fetchall()
                    for cand in candidates:
                        if _normalize_jp_name(cand["cs_name_jp"]) == target_norm:
                            our_card = cand
                            break
            if not our_card:
                continue
            key = (our_card["set_id"], our_card["card_number"])
            if key in seen_card_keys:
                continue
            seen_card_keys.add(key)
            out.append({**dict(our_card), "snkr_apparel_id": aid, "snkr_rank": len(out) + 1})
            if len(out) >= limit:
                break

    return {"trending": out, "window": window, "source": "snkr_hottest"}


@app.get("/api/cardlist/sets/{set_id}/latest-prices")
async def get_set_latest_prices(set_id: str):
    """單一 endpoint 一次拿整個 set 所有卡的最新成交價（前端 filter / sort 用）

    回傳：{card_number: latest_price_twd, ...}
    優先取 sale_date 最新；無 sale_date 取 created_at 最新。
    """
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute("""
            SELECT card_number, price_twd
            FROM (
              SELECT card_number, price_twd,
                     ROW_NUMBER() OVER (PARTITION BY card_number
                       ORDER BY COALESCE(sale_date, created_at) DESC) AS rn
              FROM card_prices
              WHERE set_id = ? AND price_twd IS NOT NULL
            )
            WHERE rn = 1
        """, (set_id,))).fetchall()
    return {"prices": {r[0]: r[1] for r in rows}}


@app.get("/api/cardlist/sets/{set_id}/preview-image")
async def get_set_preview_image(set_id: str):
    """取得 set 預覽圖。優先 artofpkm logo（DB 快取＋即時抓取），fallback pokellector logo。"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 1) artofpkm 對映
        match = conn.execute(
            "SELECT art_id FROM artofpkm_set_match WHERE our_set_id = ?", (set_id,)
        ).fetchone()
        art_id = match["art_id"] if match else None

        # 2) artofpkm_sets.logo_url 已快取
        if art_id is not None:
            row2 = conn.execute(
                "SELECT logo_url FROM artofpkm_sets WHERE id = ?", (art_id,)
            ).fetchone()
            if row2 and row2["logo_url"]:
                return {"set_id": set_id, "image_url": row2["logo_url"], "source": "artofpkm"}

        # 3) fallback：依序看 jp/tw/en 新表、再看舊 card_sets
        fallback_logo = None
        # 3a) JP: pg 是純數字
        if set_id.isdigit():
            jp_row = conn.execute(
                "SELECT logo_url FROM jp_card_list_set WHERE pg = ?", (set_id,)
            ).fetchone()
            if jp_row and jp_row["logo_url"]:
                fallback_logo = jp_row["logo_url"]
                # JP thumb 是 /assets/... 相對路徑、要前置官方 domain
                if fallback_logo.startswith("/"):
                    fallback_logo = "https://www.pokemon-card.com" + fallback_logo
        # 3b) TW: expansion_code (含字母大寫 / 有 dash)
        if not fallback_logo:
            tw_row = conn.execute(
                "SELECT logo_url FROM tw_card_list_set WHERE expansion_code = ?", (set_id,)
            ).fetchone()
            if tw_row and tw_row["logo_url"]:
                fallback_logo = tw_row["logo_url"]
        # 3c) EN
        if not fallback_logo:
            en_row = conn.execute(
                "SELECT logo_url FROM en_card_list_set WHERE set_id = ?", (set_id,)
            ).fetchone()
            if en_row and en_row["logo_url"]:
                fallback_logo = en_row["logo_url"]
        # 3d) 舊 card_sets
        if not fallback_logo:
            old_row = conn.execute(
                "SELECT logo_url FROM card_sets WHERE set_id = ?", (set_id,)
            ).fetchone()
            fallback_logo = old_row["logo_url"] if old_row else None
    finally:
        conn.close()

    # 4) 沒 artofpkm 對映：直接回 pokellector
    if art_id is None:
        return {"set_id": set_id, "image_url": fallback_logo, "source": "card_sets" if fallback_logo else None}

    # 5) 即時抓 artofpkm（同 art_id 共用 inflight）
    async with _ARTOFPKM_LOGO_LOCK:
        task = _ARTOFPKM_INFLIGHT.get(art_id)
        if not task:
            task = asyncio.create_task(_scrape_artofpkm_logo(art_id))
            _ARTOFPKM_INFLIGHT[art_id] = task
    try:
        logo = await task
    finally:
        async with _ARTOFPKM_LOGO_LOCK:
            _ARTOFPKM_INFLIGHT.pop(art_id, None)

    # 6) 寫回快取
    if logo:
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("UPDATE artofpkm_sets SET logo_url = ? WHERE id = ?", (logo, art_id))
            conn.commit()
        finally:
            conn.close()
        return {"set_id": set_id, "image_url": logo, "source": "artofpkm"}

    # 7) artofpkm 抓失敗 → 回 pokellector
    return {"set_id": set_id, "image_url": fallback_logo, "source": "card_sets" if fallback_logo else None}


@app.get("/api/cardlist/search")
async def search_cardlist(q: str, limit: int = 300, language: str = ""):
    """搜尋卡表中的卡片"""
    if not q or len(q) < 2:
        raise HTTPException(status_code=400, detail="Query too short")
    cards = await search_cards_in_list(q, limit, language=language)
    return {
        "query": q,
        "cards": cards,
        "total": len(cards),
        "language": language or "all",
    }


@app.get("/api/cardlist/categories")
async def get_cardlist_categories(language: str = "jp"):
    """取得分類列表（含系列）"""
    categories = get_all_categories(language)
    all_sets = await get_all_card_sets(language)

    # 過濾掉 0 張卡片的系列
    all_sets = [s for s in all_sets if s.get("total_cards", 0) > 0]

    # DB 中的 set_id 格式為 "{lang}-{raw_set_id}"，分類表使用 raw_set_id
    prefix = f"{language}-"

    def strip_prefix(sid: str) -> str:
        return sid[len(prefix):] if sid.startswith(prefix) else sid

    # 建立 raw_set_id -> set 的對照
    set_map = {strip_prefix(s["set_id"]): s for s in all_sets}

    result = []
    used_raw_ids = set()

    for cat in categories:
        cat_sets = []
        for raw_set_id in cat["sets"]:
            if raw_set_id in set_map:
                cat_sets.append(set_map[raw_set_id])
                used_raw_ids.add(raw_set_id)

        # 只加入有系列的分類
        if cat_sets or cat["id"] == "other":
            result.append({
                "id": cat["id"],
                "name": cat["name"],
                "name_zh": cat["name_zh"],
                "logo": cat.get("logo", ""),
                "sets": cat_sets,
                "count": len(cat_sets),
            })

    # 把沒分類的系列加到 other
    other_sets = [s for s in all_sets if strip_prefix(s["set_id"]) not in used_raw_ids]
    for cat in result:
        if cat["id"] == "other":
            cat["sets"] = other_sets
            cat["count"] = len(other_sets)

    return {"categories": result, "language": language}


# ==================== 價格歷史 API ====================

def _title_matches_card(title: str, card_name: str, card_number: str,
                          source: str = "", set_id: str = "") -> bool:
    """安全網：擋舊快取裡不同卡名混進來的污染資料（例如查 Golett 回傳 Lucario）。

    SNKR 紀錄是從 mapping 表用 apparel_id 直查的，本身就精確 → 信任 source
    （直接放行，跳過 card_number / 卡名 / PSA 檢查；標題是 placeholder 也 OK）
    eBay 紀錄走搜尋查的、需要嚴格過濾。

    set_id：用來判斷該卡是 jp-* 還是 en-*，防止 EN/JP 版本混淆。
    """
    import re as _re
    # SNKR / PriceCharting / PSA APR / TCGplayer / Cardmarket 都是用 set+number 精準對映
    if source in ("snkrdunk", "pricecharting", "psa_apr", "tcgplayer", "cardmarket"):
        return True
    if not title:
        return False
    t = title.lower()
    # 卡號 N/T 格式（取 card_number 第一個純數字段）
    nums = _re.findall(r"\d+", card_number or "")
    if nums:
        n = int(nums[0])
        num_patterns = [
            rf"\b0*{n}\s*/\s*\d+",
            rf"#\s*0*{n}\b",
            rf"\b0*{n}\s+of\s+\d+",
        ]
        if not any(_re.search(p, t) for p in num_patterns):
            return False
    # eBay：必須是 PSA 10（擋舊快取殘留的非 PSA10 紀錄）
    t_norm = _re.sub(r"psa\s*", "psa ", t)
    if not _re.search(r"\bpsa\s+10\b(?!\d|\.\d)", t_norm):
        return False
    if _re.search(r"\bpsa\s+[1-9]\b(?!\d)", t_norm):
        return False
    # 多卡 lot："PSA 10s" 複數、"LOT OF" 等（混合多張卡的拍賣）
    if _re.search(r"\bpsa\s+10s\b|\blot\s+of\b|\b2\s+graded\s+cards\b", t):
        return False
    # 擋其他評級機構：CGC/BGS/Beckett/HGA/GMA/TAG/SGC/ACE/ARS/CSG/MNT 等 + 數字
    if _re.search(r"\b(cgc|bgs|beckett|hga|gma|tag|sgc|ace|ars|csg|mnt|sbc|egs)\s*\d", t):
        return False
    if _re.search(r"\bpristine\s*10\b", t):
        return False

    # === 語言版本驗證（防止 JP/EN 混淆 + 其他語版誤入）===
    # 其他語版（韓/中/印/德/法/西等）：兩邊都不收
    if _re.search(r"\bkorea(n)?\b|韓[國語文]", t, _re.I):
        return False
    if _re.search(r"\bchinese\b|\bs[\s\-]?chinese\b|\bt[\s\-]?chinese\b|繁體|簡體|中文版|傳統中文|簡體中文", t, _re.I):
        return False
    if _re.search(r"\bindonesian?\b|\bspanish\b|\bgerman\b|\bfrench\b|\bitalian\b|\bportuguese\b|\bdutch\b|\brussian\b", t, _re.I):
        return False

    has_japanese = bool(_re.search(r"\b(japan(ese)?|jpn|jap)\b", t, _re.I))
    has_jp_chars = bool(_re.search(r"[぀-ゟ゠-ヿ]", title))  # 平假名 / 片假名

    if set_id.startswith("en-"):
        # EN 卡：明確含 JP 標記就擋掉（那是 JP 版）
        if has_japanese or has_jp_chars:
            return False
    # JP 卡：寬鬆處理（賣家可能沒寫 Japanese，但 eBay 搜尋時加了 Japanese 已篩過一輪）
    # 上面的「明確他國語版」過濾足夠擋大部分污染

    # 卡名主關鍵字（第一個英文單字，>=3 字）— 只對 eBay
    if card_name:
        tokens = [w for w in _re.findall(r"[A-Za-z]{3,}", card_name.lower())
                  if w not in ("the", "ex", "gx", "psa", "pokemon")]
        if tokens and not any(w in t for w in tokens):
            return False
    return True


@app.get("/api/prices/{set_id}/{card_number}")
async def get_card_prices(set_id: str, card_number: str):
    """取得卡片的價格歷史（會套用 listing_title 安全網過濾舊污染資料）"""
    import aiosqlite

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # 先取卡片的官方名稱與圖片（用來做標題過濾、0 hits 時也能顯示卡片資訊）
        cur = await db.execute(
            """SELECT cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity, cl.rarity_ocr,
                      cl.psa_pop10, cl.psa_pop9, cl.psa_pop8, cl.psa_pop7, cl.psa_pop6,
                      cl.psa_pop_total, cl.psa_gem_rate,
                      cl.snkr_listing_count, cl.snkr_min_price_jpy, cl.snkr_listing_updated_at,
                      cs.name AS set_name, cs.set_code AS set_code, cs.total_cards AS set_total,
                      v.sales_7d, v.sales_30d, v.sales_all,
                      jcl.cardID AS jp_card_id,
                      jcl.hp AS jp_hp, jcl.illustrator AS jp_illustrator,
                      jcl.types_json AS jp_types_json, jcl.attacks_json AS jp_attacks_json,
                      jcl.weakness AS jp_weakness, jcl.resistance AS jp_resistance,
                      jcl.retreat_cost AS jp_retreat_cost, jcl.regulation_mark AS jp_regulation_mark
               FROM card_list cl
               LEFT JOIN card_sets cs ON cl.set_id = cs.set_id
               LEFT JOIN card_volume_stats v ON v.set_id = cl.set_id AND v.card_number = cl.card_number
               LEFT JOIN jp_card_list jcl ON jcl.pg = cl.set_id AND jcl.card_number = cl.card_number
               WHERE cl.set_id=? AND cl.card_number=?""",
            (set_id, card_number),
        )
        row = await cur.fetchone()
        card_name = row["name"] if row else ""
        card_meta = {
            "name": row["name"] if row else None,
            "name_jp": row["name_jp"] if row else None,
            "name_zh": row["name_zh"] if row else None,
            "image_url": row["image_url"] if row else None,
            "rarity": row["rarity"] if row else None,
            "rarity_ocr": row["rarity_ocr"] if row else None,
            "psa_pop10": row["psa_pop10"] if row else None,
            "psa_pop9": row["psa_pop9"] if row else None,
            "psa_pop8": row["psa_pop8"] if row else None,
            "psa_pop7": row["psa_pop7"] if row else None,
            "psa_pop6": row["psa_pop6"] if row else None,
            "psa_pop_total": row["psa_pop_total"] if row else None,
            "psa_gem_rate": row["psa_gem_rate"] if row else None,
            "snkr_listing_count": row["snkr_listing_count"] if row else None,
            "snkr_min_price_jpy": row["snkr_min_price_jpy"] if row else None,
            "snkr_listing_updated_at": row["snkr_listing_updated_at"] if row else None,
            "sales_7d": row["sales_7d"] if row else None,
            "sales_30d": row["sales_30d"] if row else None,
            "sales_all": row["sales_all"] if row else None,
            "set_name": row["set_name"] if row else None,
            "set_code": row["set_code"] if row else None,
            "set_total": row["set_total"] if row else None,
            "card_number": card_number,
            # JP detail enrichment（從 jp_card_list JOIN 進來、非 JP set 自然為 None）
            "jp_card_id": row["jp_card_id"] if row else None,
            "hp": row["jp_hp"] if row else None,
            "illustrator": row["jp_illustrator"] if row else None,
            "types": (
                __import__('json').loads(row["jp_types_json"])
                if row and row["jp_types_json"] else []
            ),
            "attacks": (
                __import__('json').loads(row["jp_attacks_json"])
                if row and row["jp_attacks_json"] else []
            ),
            "weakness": row["jp_weakness"] if row else None,
            "resistance": row["jp_resistance"] if row else None,
            "retreat_cost": row["jp_retreat_cost"] if row else None,
            "regulation_mark": row["jp_regulation_mark"] if row else None,
        } if row else None

        # Fallback: pg=950 / 9001 / 9002 / 9003 等尚未 INSERT 到 card_list 的 JP set，
        # 從 jp_card_list 取 + 帶上新欄位（hp/types/attacks/W/R/R/illust/regulation_mark）
        if not card_meta:
            import json as _json_fallback
            cur_jp = await db.execute("""
                SELECT jcl.cardID,
                       jcl.name_jp,
                       ('https://www.pokemon-card.com' || jcl.thumb_url) AS image_url,
                       jcl.rarity, jcl.set_code, jcl.set_name_jp AS set_name,
                       jcl.hp, jcl.illustrator, jcl.card_number,
                       jcl.types_json, jcl.attacks_json, jcl.weakness,
                       jcl.resistance, jcl.retreat_cost, jcl.regulation_mark,
                       jcls.hit_cnt AS set_total
                FROM jp_card_list jcl
                JOIN jp_card_pg_link pl ON pl.cardID = jcl.cardID
                LEFT JOIN jp_card_list_set jcls ON jcls.pg = pl.pg
                WHERE pl.pg = ? AND jcl.card_number = ?
                LIMIT 1
            """, (set_id, card_number))
            jrow = await cur_jp.fetchone()
            if jrow:
                card_name = jrow["name_jp"] or ""
                card_meta = {
                    "name": None,
                    "name_jp": jrow["name_jp"],
                    "name_zh": None,
                    "image_url": jrow["image_url"],
                    "rarity": jrow["rarity"],
                    "rarity_ocr": None,
                    "psa_pop10": None, "psa_pop9": None, "psa_pop8": None,
                    "psa_pop7": None, "psa_pop6": None,
                    "psa_pop_total": None, "psa_gem_rate": None,
                    "snkr_listing_count": None, "snkr_min_price_jpy": None,
                    "snkr_listing_updated_at": None,
                    "sales_7d": None, "sales_30d": None, "sales_all": None,
                    "set_name": jrow["set_name"],
                    "set_code": jrow["set_code"],
                    "set_total": jrow["set_total"],
                    "card_number": card_number,
                    "jp_card_id": jrow["cardID"],
                    "hp": jrow["hp"],
                    "illustrator": jrow["illustrator"],
                    "types": _json_fallback.loads(jrow["types_json"]) if jrow["types_json"] else [],
                    "attacks": _json_fallback.loads(jrow["attacks_json"]) if jrow["attacks_json"] else [],
                    "weakness": jrow["weakness"],
                    "resistance": jrow["resistance"],
                    "retreat_cost": jrow["retreat_cost"],
                    "regulation_mark": jrow["regulation_mark"],
                }

        # 取得價格歷史
        # 只回必要欄位，省 payload (省 listing_title 等大 text 一半 size)
        cursor = await db.execute("""
            SELECT id, source, price_jpy, price_usd, price_twd,
                   listing_title, listing_url, sale_date, created_at,
                   search_language, psa_grade
            FROM card_prices
            WHERE set_id = ? AND card_number = ?
            ORDER BY COALESCE(sale_date, created_at) DESC
            LIMIT 2000
        """, (set_id, card_number))
        rows = await cursor.fetchall()
        all_prices = [dict(row) for row in rows]

        # 套用安全網過濾：只留 title 真的符合這張卡的紀錄
        prices = [
            p for p in all_prices
            if _title_matches_card(
                p.get("listing_title", ""), card_name, card_number,
                source=p.get("source", ""), set_id=set_id,
            )
        ]

        # 補上 search_language（舊資料無此欄位，從 set_id 推定）
        card_lang = "jp" if set_id.startswith("jp-") else ("en" if set_id.startswith("en-") else "")
        for p in prices:
            if not p.get("search_language"):
                # SNKRDUNK / PriceCharting / PSA APR 都跟著 set_id 語系；eBay 也是
                if p["source"] == "snkrdunk":
                    p["search_language"] = "jp"
                else:
                    p["search_language"] = card_lang

        def _stat(rows):
            vals = [r["price_twd"] for r in rows if r.get("price_twd")]
            if not vals:
                return {"count": 0, "avg": None, "min": None, "max": None, "latest": None}
            return {
                "count": len(vals),
                "avg": round(sum(vals) / len(vals), 0),
                "min": min(vals),
                "max": max(vals),
                "latest": vals[0],
            }

        ebay_jp = [p for p in prices if p["source"] == "ebay" and p.get("search_language") == "jp"]
        ebay_en = [p for p in prices if p["source"] == "ebay" and p.get("search_language") == "en"]
        snkr_all = [p for p in prices if p["source"] == "snkrdunk"]
        pc_all = [p for p in prices if p["source"] == "pricecharting"]
        psa_all = [p for p in prices if p["source"] == "psa_apr"]
        tcgp_all = [p for p in prices if p["source"] == "tcgplayer"]
        cm_all = [p for p in prices if p["source"] == "cardmarket"]

        stats = {
            "ebay": _stat([p for p in prices if p["source"] == "ebay"]),
            "snkrdunk": _stat(snkr_all),
            "pricecharting": _stat(pc_all),
            "psa_apr": _stat(psa_all),
            "tcgplayer": _stat(tcgp_all),
            "cardmarket": _stat(cm_all),
            "ebay_jp": _stat(ebay_jp),
            "ebay_en": _stat(ebay_en),
            "snkrdunk_jp": _stat(snkr_all),
            "pricecharting_jp": _stat(pc_all),
            "psa_apr_jp": _stat(psa_all),
        }

        # sync 歷史（前端用來判斷「未掃過」vs「掃過但 0 hit」）
        sync_history = {"attempt_count": 0, "zero_hit_count": 0, "total_hits": 0, "last_attempt": None}
        try:
            cur_h = await db.execute(
                "SELECT attempt_count, zero_hit_count, total_hits, last_attempt "
                "FROM card_sync_history WHERE set_id=? AND card_number=?",
                (set_id, card_number),
            )
            h_row = await cur_h.fetchone()
            if h_row:
                sync_history = {
                    "attempt_count": h_row["attempt_count"] or 0,
                    "zero_hit_count": h_row["zero_hit_count"] or 0,
                    "total_hits": h_row["total_hits"] or 0,
                    "last_attempt": h_row["last_attempt"],
                }
        except Exception:
            pass

        # Phase 2: 加入訂單簿資料（PSA10/9/Raw 各一份）
        orderbook = {}
        for g in (10, 9, 0):
            try:
                ob = await mp.get_orderbook(set_id, card_number, g)
                orderbook[str(g)] = {
                    "lowest_ask": ob["lowest_ask"],
                    "highest_bid": ob["highest_bid"],
                    "ask_depth": ob["ask_depth"],
                    "bid_depth": ob["bid_depth"],
                    "last_trade_price": ob["last_trade_price"],
                    "last_trade_at": ob["last_trade_at"],
                }
            except Exception:
                orderbook[str(g)] = None

        return {
            "set_id": set_id,
            "card_number": card_number,
            "card_language": card_lang,
            "card": card_meta,
            "prices": prices,
            "stats": stats,
            "sync_history": sync_history,
            "orderbook": orderbook,
        }


def _hamming_hex(a: str, b: str) -> int:
    """兩個同長度 hex hash 的 hamming distance（bits）"""
    if not a or not b or len(a) != len(b):
        return 999
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 999


@app.get("/api/prices/sibling/{set_id}/{card_number}")
async def get_sibling_prices(set_id: str, card_number: str):
    """找對應語言版本的同卡片（同插畫，不同語言）。

    配對策略：
      1. 優先：image_phash hamming ≤ 6 → 同插畫不同語言版（最準）
      2. fallback：同 cl.name 同卡號同 set（若 phash 還沒 backfill 到）
      3. 都找不到 → sibling=None，前端顯示「無對應版本」
    """
    import aiosqlite
    if set_id.startswith("jp-"):
        target_prefix = "en-"
    elif set_id.startswith("en-"):
        target_prefix = "jp-"
    else:
        return {"sibling": None, "stats": None}

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 取本卡 metadata + phash
        src = await (await db.execute(
            "SELECT name, image_phash FROM card_list WHERE set_id=? AND card_number=?",
            (set_id, card_number),
        )).fetchone()
        if not src or not src["name"]:
            return {"sibling": None, "stats": None}
        name = src["name"]
        src_phash = src["image_phash"]

        sibling = None
        match_method = None

        # 策略 1：phash 配對（最可靠）
        # - hamming ≤ 4：純信任 phash
        # - hamming 5-6：要求 name 也對得上（避免不同 Pokemon 共用相似背景的假陽性）
        # - hamming ≥ 7：拒絕
        if src_phash:
            cands = await (await db.execute(
                f"""SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.name_zh,
                           cl.image_url, cl.image_phash, cs.name as set_name,
                           (SELECT COUNT(*) FROM card_prices cp
                              WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number) AS price_count
                    FROM card_list cl
                    LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
                    WHERE cl.set_id LIKE ?
                      AND cl.image_phash IS NOT NULL AND cl.image_phash != ''""",
                (f"{target_prefix}%",),
            )).fetchall()
            src_name_norm = (name or "").strip().lower()
            best = None
            best_dist = 7
            for c in cands:
                d = _hamming_hex(src_phash, c["image_phash"])
                if d >= best_dist:
                    continue
                # hamming 5-6 嚴格要求 name 一致（避免假陽性）
                if d >= 5:
                    cand_name_norm = (c["name"] or "").strip().lower()
                    if not cand_name_norm or cand_name_norm != src_name_norm:
                        continue
                best, best_dist = c, d
                if d == 0:
                    break  # 完美命中
            if best is not None:
                sibling = best
                match_method = f"phash_d{best_dist}"

        # 策略 2 fallback：同名同卡號同 set
        if sibling is None:
            suffix = set_id[3:]
            candidate_set_ids = [f"{target_prefix}{suffix}"]
            if target_prefix == "en-":
                candidate_set_ids.append(f"en-{suffix}-EN")
            else:
                if suffix.endswith("-EN"):
                    candidate_set_ids.append(f"jp-{suffix[:-3]}")
            placeholders = ",".join("?" * len(candidate_set_ids))
            sibling = await (await db.execute(
                f"""SELECT cl.set_id, cl.card_number, cl.name, cl.name_jp, cl.name_zh,
                           cl.image_url, cs.name as set_name,
                           (SELECT COUNT(*) FROM card_prices cp
                              WHERE cp.set_id=cl.set_id AND cp.card_number=cl.card_number) AS price_count
                    FROM card_list cl
                    LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
                    WHERE cl.set_id IN ({placeholders})
                      AND cl.card_number = ?
                      AND cl.name = ?
                    ORDER BY price_count DESC LIMIT 1""",
                (*candidate_set_ids, card_number, name),
            )).fetchone()
            if sibling:
                match_method = "name_set"

        if not sibling:
            return {"sibling": None, "stats": None, "match_method": None}

        sibling_dict = dict(sibling)
        sibling_dict["match_method"] = match_method

        if sibling["price_count"] == 0:
            return {"sibling": sibling_dict, "stats": None, "match_method": match_method}

        prices = await (await db.execute(
            """SELECT price_twd FROM card_prices
               WHERE set_id=? AND card_number=? AND price_twd IS NOT NULL
               ORDER BY created_at DESC LIMIT 50""",
            (sibling["set_id"], sibling["card_number"]),
        )).fetchall()
        vals = [p["price_twd"] for p in prices]
        stats = None
        if vals:
            stats = {
                "count": len(vals),
                "avg": round(sum(vals) / len(vals), 0),
                "min": min(vals),
                "max": max(vals),
                "latest": vals[0],
            }
        return {"sibling": sibling_dict, "stats": stats, "match_method": match_method}


@app.post("/api/prices/sync/{set_id}/{card_number}")
async def sync_card_prices_api(set_id: str, card_number: str):
    """同步單張卡片的價格（即時爬取）。

    依 set_id 前綴判斷語言：
      - en-* 卡：只查 eBay（SNKRDUNK 是日文站，英卡會抓錯版本）
      - jp-* 卡：eBay 加 "Japanese" 關鍵字 + 查 SNKRDUNK（需有 JP metadata）
    """
    import aiosqlite

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT cl.name, cl.name_jp AS card_name_jp,
                      cs.name AS set_name_en, cs.name_jp AS set_name_jp,
                      cs.set_code AS set_code
               FROM card_list cl
               LEFT JOIN card_sets cs ON cl.set_id = cs.set_id
               WHERE cl.set_id = ? AND cl.card_number = ?""",
            (set_id, card_number)
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Card not found")
        card_name = row["name"]
        card_name_jp = row["card_name_jp"]
        set_name_en = row["set_name_en"]
        set_name_jp = row["set_name_jp"]
        set_code = row["set_code"] or None

        # JP 卡無 name_jp 時嘗試從 pokemon_dict 反查（覆蓋 996 個 unique 寶可夢名）
        # 處理常見前綴/後綴：Mega/Dark/Light/X/Y/Imakuni? / ex/V/VMAX/VSTAR/GX/δ/-EX 等
        if (not card_name_jp) and card_name and set_id.startswith("jp-"):
            import re as _re_pkm
            base = card_name
            # 去後綴：ex/V/VMAX/VSTAR/GX/EX/δ/LV.X/LEGEND，以及 Mega 系列的 X/Y 形態
            base = _re_pkm.sub(r"\s+(ex|EX|V|VMAX|VSTAR|GX|δ|G|LV\.?X|LEGEND)\b.*$", "", base, flags=_re_pkm.IGNORECASE)
            base = _re_pkm.sub(r"\s+[XY]$", "", base)  # Mega Charizard X / Mega Mewtwo Y
            # 去前綴
            base = _re_pkm.sub(r"^\s*(Mega|M-?|Dark|Light|Shining|Imakuni\?)\s+", "", base, flags=_re_pkm.IGNORECASE)
            # 去括號 (Holographic) 等
            base = _re_pkm.sub(r"\s*\([^)]*\)", "", base).strip()
            # 嘗試 exact match → 否則 LIKE
            for q, params in [
                ("SELECT name_jp FROM pokemon_dict WHERE name_en = ? COLLATE NOCASE LIMIT 1", (base,)),
                ("SELECT name_jp FROM pokemon_dict WHERE ? LIKE name_en || ' %' OR ? LIKE '% ' || name_en COLLATE NOCASE ORDER BY length(name_en) DESC LIMIT 1", (card_name, card_name)),
            ]:
                cur_pkm = await db.execute(q, params)
                pkm_row = await cur_pkm.fetchone()
                if pkm_row and pkm_row["name_jp"]:
                    card_name_jp = pkm_row["name_jp"]
                    break

    # 從 set_id 前綴判斷語言
    language = "en" if set_id.startswith("en-") else ("jp" if set_id.startswith("jp-") else "")

    ebay_task = get_ebay_prices(
        card_name, is_cert=False, grade="10",
        card_number=card_number, set_name=set_name_en or set_id,
        language=language,
        card_name_jp=card_name_jp,  # JP 卡時會多跑一條日文名 query
    )

    if language == "en":
        # 英文卡跳過 SNKRDUNK（會配錯成日版）
        async def _empty():
            return []
        snkr_task = _empty()
    else:
        snkr_task = get_snkrdunk_prices(
            card_name, is_cert=False, grade="10",
            card_number=card_number, set_name=set_name_en or set_id,
            set_name_jp=set_name_jp, card_name_jp=card_name_jp,
            set_code=set_code,
            full_history=True,
        )

    ebay_results, snkr_results = await asyncio.gather(ebay_task, snkr_task)

    # 儲存結果（寫入 search_language 標記版本）
    # SNKRDUNK 永遠是日版；eBay 由 set_id 前綴決定
    ebay_lang = "jp" if language == "jp" else "en"
    saved = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for r in ebay_results[:30]:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO card_prices
                    (set_id, card_number, card_name, source, price_usd, price_twd,
                     listing_title, listing_url, sale_date, search_language)
                    VALUES (?, ?, ?, 'ebay', ?, ?, ?, ?, ?, ?)
                """, (set_id, card_number, card_name, r.get("price_usd"),
                      r.get("price_twd"), r.get("listing_title"),
                      r.get("listing_url"), r.get("sale_date"), ebay_lang))
                saved += 1
            except Exception:
                pass

        for r in snkr_results:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO card_prices
                    (set_id, card_number, card_name, source, price_jpy, price_twd,
                     listing_title, listing_url, sale_date, search_language, psa_grade)
                    VALUES (?, ?, ?, 'snkrdunk', ?, ?, ?, ?, ?, 'jp', 10)
                """, (set_id, card_number, card_name, r.get("price_jpy"),
                      r.get("price_twd"), r.get("listing_title"),
                      r.get("listing_url"), r.get("sale_date")))
                saved += 1
            except Exception:
                pass

        await db.commit()

    # ==== sync 完成後：抓 SNKR listingCount（掛單數）+ 重算 volume_stats ====
    if language == "jp" and set_name_jp:
        try:
            from app.scraper.snkrdunk_http import (
                _lookup_apparel_id, fetch_apparel, DEFAULT_HEADERS,
            )
            from app.database import update_snkr_listing_meta
            import httpx as _httpx
            apparel_id = _lookup_apparel_id(card_number, set_name_jp, card_name_jp, set_code=set_code)
            if apparel_id:
                async with _httpx.AsyncClient(headers=DEFAULT_HEADERS) as _c:
                    a = await fetch_apparel(_c, apparel_id)
                    if a:
                        await update_snkr_listing_meta(
                            set_id, card_number,
                            listing_count=a.get("usedListingCount") or 0,
                            min_price_jpy=a.get("usedMinPrice") or None,
                        )
        except Exception:
            pass  # listing meta 不阻擋主流程

    try:
        from app.database import update_volume_stats
        await update_volume_stats(set_id, card_number)
    except Exception:
        pass

    return {
        "set_id": set_id,
        "card_number": card_number,
        "card_name": card_name,
        "card_name_jp": card_name_jp,  # 含 pokemon_dict 反查的結果
        "language": language,
        "ebay_count": len(ebay_results),
        "snkrdunk_count": len(snkr_results),
        "saved": saved,
    }


@app.post("/api/prices/sync_snkr/{pg}/{card_number}")
async def sync_snkr_full_history(pg: str, card_number: str):
    """SNKR-only full-history backfill 專用 endpoint。

    與 /api/prices/sync/ 不同：
      - 跳過 eBay（pilot/全量跑只動 SNKR、加速 ~80%）
      - full_history=True：max_pages=500、抓到第一筆
      - 直接從 jp_card_list / jp_card_list_set 查（不依賴 card_list 主表）
      - 成功後 UPDATE jp_card_list.prices_synced_at（resume gating）

    pg：jp_card_list_set.pg（數字字串、如 "950"）
    card_number：jp_card_list.card_number
    """
    import aiosqlite

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT jcl.cardID, jcl.name_jp AS card_name_jp, jcl.name_alt AS card_name_en,
                      jcl.set_code AS set_code,
                      jcls.name_jp AS set_name_jp
               FROM jp_card_list jcl
               LEFT JOIN jp_card_list_set jcls ON jcls.pg = jcl.pg
               WHERE jcl.pg = ? AND jcl.card_number = ?""",
            (pg, card_number),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"jp_card_list pg={pg} card_number={card_number} not found")
        card_id = row["cardID"]
        card_name_jp = row["card_name_jp"]
        card_name_en = row["card_name_en"] or card_name_jp or ""
        set_name_jp = row["set_name_jp"] or ""
        set_code = row["set_code"] or None

    # set_id 用於寫 card_prices：與既有 promo 寫法一致（pg 數字字串）
    set_id = pg

    snkr_results = await get_snkrdunk_prices(
        card_name_en, is_cert=False, grade="10",
        card_number=card_number, set_name=set_id,
        set_name_jp=set_name_jp, card_name_jp=card_name_jp,
        set_code=set_code,
        full_history=True,
    )

    saved = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for r in snkr_results:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO card_prices
                    (set_id, card_number, card_name, source, price_jpy, price_twd,
                     listing_title, listing_url, sale_date, search_language, psa_grade)
                    VALUES (?, ?, ?, 'snkrdunk', ?, ?, ?, ?, ?, 'jp', 10)
                """, (set_id, card_number, card_name_jp or card_name_en,
                      r.get("price_jpy"), r.get("price_twd"),
                      r.get("listing_title"), r.get("listing_url"),
                      r.get("sale_date")))
                saved += 1
            except Exception:
                pass

        # 標記已 sync — resume gating（即使 0 筆也標、避免重複跑無 mapping 的卡）
        # 用 (pg, card_number) 而非 cardID：同組可能有 ≥2 個 cardID（785 組重複），一次標齊避免 metadata gap
        await db.execute(
            "UPDATE jp_card_list SET prices_synced_at = CURRENT_TIMESTAMP "
            "WHERE pg = ? AND card_number = ?",
            (pg, card_number),
        )
        await db.commit()

    return {
        "pg": pg,
        "card_number": card_number,
        "cardID": card_id,
        "snkrdunk_count": len(snkr_results),
        "saved": saved,
    }


# JP 卡名 → EN 翻譯（多層 fallback：HTML strip → 地區/Team Rocket 前綴 → メガ →
#   後綴抽取 → pokemon_dict / jp_term_dict 查找）
import re as _re_jp2en
_JP_CHAR_RE = _re_jp2en.compile(r'[ぁ-ゟ゠-ヿ一-龯]')
_CARD_SUFFIX_RE = _re_jp2en.compile(r'(VMAX|VSTAR|VUNION|V-UNION|GMAX|GX|EX|ex|V)$')
_HTML_TAG_RE = _re_jp2en.compile(r'<[^>]+>')
_MEGA_HTML_RE = _re_jp2en.compile(r'pcg-megamark')
_REGIONAL_PREFIXES = [
    ('ガラル ', 'Galarian'),
    ('アローラ ', 'Alolan'),
    ('ヒスイ ', 'Hisuian'),
    ('パルデア ', 'Paldean'),
    ('ガラルの', 'Galarian'),
    ('ヒスイの', 'Hisuian'),
]
_TEAM_ROCKET_PREFIX = 'ロケット団の'  # → "Team Rocket's"

# JP→ZH 地區形 prefix（連寫無空格、user 偏好「阿羅拉小拉達」格式）
_REGIONAL_PREFIXES_ZH = [
    ('ガラル ', '伽勒爾'),
    ('アローラ ', '阿羅拉'),
    ('ヒスイ ', '洗翠'),
    ('パルデア ', '帕底亞'),
    ('ガラルの', '伽勒爾'),
    ('ヒスイの', '洗翠'),
]


async def _translate_jp_card_name_to_en(card_name_jp: str, db) -> str | None:
    """JP→EN multi-rule translation.

    Order:
      1. HTML strip (Bulbapedia <span class='pcg pcg-megamark'></span> → Mega marker)
      2. ロケット団の prefix → Team Rocket's
      3. 地區形 prefix (ガラル/アローラ/ヒスイ/パルデア) → Galarian/etc.
      4. メガ prefix → Mega
      5. Suffix V/VMAX/VSTAR/GX/EX/ex/GMAX/V-UNION
      6. core lookup: pokemon_dict 優先、jp_term_dict 次之
      7. 仍 miss → 全名 jp_term_dict（處理 trainer/energy）
      8. 全 miss → None（caller 走 card_name_jp 路徑）
    """
    if not card_name_jp:
        return None

    raw = card_name_jp.strip()

    # 1. HTML
    is_mega = bool(_MEGA_HTML_RE.search(raw))
    name = _HTML_TAG_RE.sub('', raw).strip()

    # 2. Team Rocket
    is_team_rocket = False
    if name.startswith(_TEAM_ROCKET_PREFIX):
        is_team_rocket = True
        name = name[len(_TEAM_ROCKET_PREFIX):].strip()

    # 3. Regional
    regional = None
    for prefix, label in _REGIONAL_PREFIXES:
        if name.startswith(prefix):
            regional = label
            name = name[len(prefix):].strip()
            break

    # 4. Mega
    if name.startswith('メガ'):
        is_mega = True
        name = name[2:].strip()

    # 5. Suffix
    m = _CARD_SUFFIX_RE.search(name)
    suffix = m.group(0) if m else ''
    core = (name[:-len(suffix)] if suffix else name).strip()

    en_core = None
    if core:
        if _JP_CHAR_RE.search(core):
            # 6. pokemon_dict 優先
            cur = await db.execute(
                "SELECT name_en FROM pokemon_dict WHERE name_jp = ? LIMIT 1", (core,)
            )
            row = await cur.fetchone()
            if row:
                en_core = row[0]
            else:
                # jp_term_dict
                cur = await db.execute(
                    "SELECT name_en FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (core,)
                )
                row = await cur.fetchone()
                if row:
                    en_core = row[0]
        else:
            # core 已是英數 — 直接用
            en_core = core

    # 7. 全名 fallback（trainer / energy / 含空格與其他符號的卡名）
    if not en_core:
        cur = await db.execute(
            "SELECT name_en FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (raw,)
        )
        row = await cur.fetchone()
        if row:
            en_core = row[0]
            # reset 前綴/後綴標記 — 全名查到不需再加飾詞
            is_mega = False
            is_team_rocket = False
            regional = None
            suffix = ''

    if not en_core:
        return None

    parts = []
    if is_team_rocket:
        parts.append("Team Rocket's")
    if is_mega:
        parts.append('Mega')
    if regional:
        parts.append(regional)
    parts.append(en_core)
    if suffix:
        parts.append(suffix)
    return ' '.join(parts)


async def _translate_jp_card_name_to_zh(card_name_jp, db):
    """JP→ZH 翻譯（per-pokemon 對映、跨 set 通用）。

    順序：HTML strip → 人物の → メガ → 地區形 → 後綴 → core 查 pokemon_dict / jp_term_dict
         → 全名 fallback → 組合（所有飾詞跟 core 之間連寫無空格）。
    """
    if not card_name_jp:
        return None

    raw = card_name_jp.strip()
    is_mega = bool(_MEGA_HTML_RE.search(raw))
    name = _HTML_TAG_RE.sub('', raw).strip()

    # 人物の prefix（查 jp_term_dict 拿中文人名）
    char_prefix_zh = None
    if 'の' in name:
        cut = name.index('の')
        char_jp = name[:cut]
        rest = name[cut + 1:].strip()
        if char_jp and rest:
            cur = await db.execute(
                "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (char_jp,)
            )
            row = await cur.fetchone()
            if row and row[0]:
                char_prefix_zh = row[0] + '的'
                name = rest

    # Mega
    if name.startswith('メガ'):
        is_mega = True
        name = name[2:].strip()

    # 地區形（連寫無空格）
    regional_zh = None
    for prefix, label in _REGIONAL_PREFIXES_ZH:
        if name.startswith(prefix):
            regional_zh = label
            name = name[len(prefix):].strip()
            break

    # 後綴抽取
    m = _CARD_SUFFIX_RE.search(name)
    suffix = m.group(0) if m else ''
    core = (name[:-len(suffix)] if suffix else name).strip()

    zh_core = None
    if core:
        if _JP_CHAR_RE.search(core):
            cur = await db.execute(
                "SELECT name_zh FROM pokemon_dict WHERE name_jp = ? LIMIT 1", (core,)
            )
            row = await cur.fetchone()
            if row and row[0]:
                zh_core = row[0]
            else:
                cur = await db.execute(
                    "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (core,)
                )
                row = await cur.fetchone()
                if row and row[0]:
                    zh_core = row[0]

    # 全名 fallback
    if not zh_core:
        cur = await db.execute(
            "SELECT name_zh FROM jp_term_dict WHERE name_jp = ? LIMIT 1", (raw,)
        )
        row = await cur.fetchone()
        if row and row[0]:
            zh_core = row[0]
            char_prefix_zh = None
            is_mega = False
            regional_zh = None
            suffix = ''

    if not zh_core:
        return None

    out = ''
    if char_prefix_zh:
        out += char_prefix_zh
    if is_mega:
        out += 'Mega'
    if regional_zh:
        out += regional_zh
    out += zh_core
    out += suffix
    return out


# 2026-05-22: PSA-label query format mapping。賣家 eBay listing title 直接抄 PSA label 的字、
# 模仿這個格式可以大幅提升 recall。Hardcode 5 pg = 涵蓋目前 1,309 待 sync 卡（其他 pg 已 sync）。
# 未來新 set 加 entry 即可。Schema 不動、保 KISS。
_PG_TO_EBAY_INFO = {
    "949": {"set_code_en": "M2",  "set_name_en": "Inferno X",                       "release_year": 2025},
    "950": {"set_code_en": "M2a", "set_name_en": "MEGA Dream ex",                   "release_year": 2025},
    "951": {"set_code_en": "MC",  "set_name_en": "Start Deck 100 Battle Collection","release_year": 2025},
    "952": {"set_code_en": "M3",  "set_name_en": "Munikis Zero",                    "release_year": 2026},
    "953": {"set_code_en": "M4",  "set_name_en": "Ninja Spinner",                   "release_year": 2026},
}

# 2026-05-22: Rarity 縮寫 → PSA label / eBay listing 慣用全名。User 953/114 spot-check
# 加 SAR → "SPECIAL ART RARE" 比不加 rarity recall +73%（45 → 78 listings）。
# 沒列在 dict 的 rarity（C / U / R / 無標示 等）不加進 query — 賣家標題很少寫普卡稀有度。
_RARITY_TO_EBAY = {
    "SAR": "SPECIAL ART RARE",
    "SR":  "SUPER RARE",
    "UR":  "ULTRA RARE",
    "AR":  "ART RARE",
    "RR":  "DOUBLE RARE",
    "HR":  "HYPER RARE",
    "CHR": "CHARACTER RARE",
    "SSR": "SHINY SUPER RARE",
    "CSR": "CHARACTER SUPER RARE",
    "MUR": "MEGA ULTRA RARE",
}


@app.post("/api/prices/sync_ebay/{pg}/{card_number}")
async def sync_ebay_full_history(pg: str, card_number: str):
    """eBay-only full-history backfill 專用 endpoint。

    與 /api/prices/sync/ 不同：
      - 跳過 SNKRDUNK（eBay-only）
      - full_history=True：max_pages=50
      - 直接從 jp_card_list / jp_card_list_set 查（不依賴 card_list 主表）
      - INSERT 寫 psa_grade=10（與 SNKR full-history backfill 對齊）
      - 成功後 UPDATE jp_card_list.ebay_prices_synced_at（resume gating）
      - 用 pokemon_dict 把日文卡名翻成英文、提升 eBay 英文標題 listing 命中率
      - 2026-05-22：query 改用 PSA-label 格式（year+POKEMON JAPANESE+abbrev+set_name+#num+name+PSA grade）

    pg：jp_card_list_set.pg（數字字串、如 "950"）
    card_number：jp_card_list.card_number
    """
    import aiosqlite
    from app.scraper.ebay import get_ebay_prices

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT jcl.cardID, jcl.name_jp AS card_name_jp, jcl.name_alt AS card_name_en,
                      jcl.rarity AS card_rarity,
                      jcls.name_jp AS set_name_jp, jcls.release_date AS set_release_date
               FROM jp_card_list jcl
               LEFT JOIN jp_card_list_set jcls ON jcls.pg = jcl.pg
               WHERE jcl.pg = ? AND jcl.card_number = ?""",
            (pg, card_number),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"jp_card_list pg={pg} card_number={card_number} not found")
        card_id = row["cardID"]
        card_name_jp = row["card_name_jp"]
        # 先嘗試 JP→EN 翻譯、fallback 到 name_alt / JP 原名
        translated_en = await _translate_jp_card_name_to_en(card_name_jp, db)
        card_name_en = translated_en or row["card_name_en"] or card_name_jp or ""

        # 2026-05-22: 取 PSA-label query 所需的 set abbrev / set name / release year / rarity
        info = _PG_TO_EBAY_INFO.get(pg, {})
        set_code_en = info.get("set_code_en")
        set_name_en = info.get("set_name_en")
        release_year = info.get("release_year")
        # 若 hardcode dict 沒覆蓋 pg、fallback 從 release_date 拆年份
        if release_year is None and row["set_release_date"]:
            try:
                release_year = int(str(row["set_release_date"])[:4])
            except (ValueError, TypeError):
                release_year = None
        # Rarity 縮寫 → 賣家標題慣用全名（SAR → "SPECIAL ART RARE"）
        rarity_full = _RARITY_TO_EBAY.get(row["card_rarity"]) if row["card_rarity"] else None

    set_id = pg

    # set_name=None：query 不放 set token、scraper 也對 JP 卡 bypass set-token post-filter
    ebay_results = await get_ebay_prices(
        card_name_en, is_cert=False, grade="10",
        card_number=card_number, set_name=None,
        language="jp", card_name_jp=card_name_jp,
        verify_redirects=False,  # 2026-05-18 v2: 關掉 verify、靠 title regex（PSA 10 + non-PSA blacklist）把關
                                 # 79+ catalog-redirect 的 row 多半是 eBay LH_Sold 確認的真實成交、只是 listing 被聚到 /p/ catalog
        full_history=True,
        set_code_en=set_code_en,
        set_name_en=set_name_en,
        release_year=release_year,
        rarity_full=rarity_full,
    )

    saved = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for r in ebay_results:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO card_prices
                    (set_id, card_number, card_name, source, price_usd, price_twd,
                     listing_title, listing_url, sale_date, search_language, psa_grade)
                    VALUES (?, ?, ?, 'ebay', ?, ?, ?, ?, ?, 'jp', 10)
                """, (set_id, card_number, card_name_jp or card_name_en,
                      r.get("price_usd"), r.get("price_twd"),
                      r.get("listing_title"), r.get("listing_url"),
                      r.get("sale_date")))
                saved += 1
            except Exception:
                pass

        # 標記已 sync（用 (pg, card_number) 避免 cardID 重複組漏標）
        await db.execute(
            "UPDATE jp_card_list SET ebay_prices_synced_at = CURRENT_TIMESTAMP "
            "WHERE pg = ? AND card_number = ?",
            (pg, card_number),
        )
        await db.commit()

    return {
        "pg": pg,
        "card_number": card_number,
        "cardID": card_id,
        "ebay_count": len(ebay_results),
        "saved": saved,
    }


# ==================== Phase 2：Auth / Marketplace ====================
from fastapi import Depends, Body, Header

import re as _re_phone
import secrets as _secrets
from datetime import datetime, timedelta

PHONE_RE = _re_phone.compile(r'^09\d{8}$')


def _is_dev_mode() -> bool:
    """開發模式：把驗證碼回給前端 toast 顯示。生產要關掉。"""
    return os.getenv("CARDPOOL_DEV_MODE", "1") == "1"


@app.post("/api/auth/phone/send-code")
async def auth_phone_send_code(payload: dict = Body(...)):
    """送 6 位數簡訊驗證碼。dev 模式下會把 code 回前端"""
    phone = (payload.get("phone") or "").strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="無效手機號碼")
    import aiosqlite
    # 先檢查 phone 是不是已經有人用
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM users WHERE phone=?", (phone,))
        if await cur.fetchone():
            raise HTTPException(status_code=409, detail="此手機號碼已被註冊")
    code = "".join(_secrets.choice("0123456789") for _ in range(6))
    expires = (datetime.utcnow() + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO phone_codes (phone, code, attempts, expires_at) VALUES (?, ?, 0, ?)",
            (phone, code, expires),
        )
        await db.commit()
    # TODO: 對接真實 SMS 服務（Twilio / Vonage / 三竹簡訊 等）
    print(f"[SMS] {phone} → {code} (expires {expires})")
    out = {"ok": True, "expires_in": 600}
    if _is_dev_mode():
        out["dev_code"] = code
    return out


async def _verify_phone_code(phone: str, code: str) -> bool:
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT code, attempts, expires_at FROM phone_codes WHERE phone=?",
            (phone,),
        )
        row = await cur.fetchone()
        if not row:
            return False
        stored, attempts, expires_at = row
        if attempts >= 5:
            return False
        # 檢查到期
        try:
            exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() > exp:
                return False
        except Exception:
            return False
        await db.execute("UPDATE phone_codes SET attempts=attempts+1 WHERE phone=?", (phone,))
        if stored != code:
            await db.commit()
            return False
        # 驗證成功 → 刪除避免重用
        await db.execute("DELETE FROM phone_codes WHERE phone=?", (phone,))
        await db.commit()
        return True


# ===== Email 驗證碼註冊（流程 A：先驗證才註冊）=====

EMAIL_CODE_TTL_MIN = 10              # 驗證碼 10 分鐘過期
EMAIL_CODE_RESEND_COOLDOWN_SEC = 60  # 重發冷卻 60 秒
EMAIL_CODE_MAX_ATTEMPTS = 5          # 最多驗證 5 次


def _gen_email_code() -> str:
    """產 6 位數字驗證碼"""
    return "".join(_secrets.choice("0123456789") for _ in range(6))


@app.post("/api/auth/register-request")
async def auth_register_request(payload: dict = Body(...)):
    """流程 A 階段 1：填註冊表單 → 暫存資料 + 寄驗證碼到 email
    payload: {email, password, display_name?}
    回應：{ok: true, message, dev_code?}
    """
    from app.email_sender import send_verification_code as _send_code
    import aiosqlite

    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    display_name = (payload.get("display_name") or "").strip() or email.split("@")[0]

    # 1. 格式驗證
    if not auth_mod.EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="email 格式錯誤")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 字元")

    # 2. email 已註冊檢查
    async with aiosqlite.connect(DB_PATH) as db:
        if await (await db.execute("SELECT id FROM users WHERE email=?", (email,))).fetchone():
            raise HTTPException(status_code=409, detail="此 email 已註冊")

        # 3. 重發冷卻：同 email 60 秒內已寄過就擋
        cur = await db.execute(
            "SELECT created_at FROM email_verifications WHERE email=?", (email,)
        )
        row = await cur.fetchone()
        if row:
            try:
                last = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                if (datetime.utcnow() - last).total_seconds() < EMAIL_CODE_RESEND_COOLDOWN_SEC:
                    raise HTTPException(status_code=429, detail=f"請等 {EMAIL_CODE_RESEND_COOLDOWN_SEC} 秒再重發")
            except ValueError:
                pass

        # 4. 產 code + hash 密碼 + UPSERT 暫存
        try:
            pw_hash = auth_mod.hash_password(password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        code = _gen_email_code()
        expires = (datetime.utcnow() + timedelta(minutes=EMAIL_CODE_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")

        await db.execute(
            """INSERT INTO email_verifications (email, code, password_hash, display_name, attempts, expires_at, created_at)
               VALUES (?, ?, ?, ?, 0, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(email) DO UPDATE SET
                   code=excluded.code,
                   password_hash=excluded.password_hash,
                   display_name=excluded.display_name,
                   attempts=0,
                   expires_at=excluded.expires_at,
                   created_at=CURRENT_TIMESTAMP""",
            (email, code, pw_hash, display_name, expires),
        )
        await db.commit()

    # 5. 寄信
    ok = _send_code(email, code)
    if not ok:
        raise HTTPException(status_code=502, detail="寄信失敗、請稍後再試")

    out = {"ok": True, "message": f"驗證碼已寄到 {email}（10 分鐘內有效）"}
    if _is_dev_mode():
        out["dev_code"] = code
    return out


@app.post("/api/auth/register-verify")
async def auth_register_verify(payload: dict = Body(...)):
    """流程 A 階段 2：使用者輸入驗證碼 → 建帳號 + 回 session token
    payload: {email, code}
    回應：{user, token}
    """
    import aiosqlite

    email = (payload.get("email") or "").strip().lower()
    code = (payload.get("code") or "").strip()

    if not email or not code:
        raise HTTPException(status_code=400, detail="缺少 email 或驗證碼")
    if not _re_phone.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="驗證碼格式錯誤（6 位數字）")

    async with aiosqlite.connect(DB_PATH) as db:
        # 1. 查暫存資料
        cur = await db.execute(
            "SELECT code, password_hash, display_name, attempts, expires_at FROM email_verifications WHERE email=?",
            (email,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="找不到此 email 的驗證紀錄、請重新註冊")
        stored_code, pw_hash, display_name, attempts, expires_at = row

        # 2. 超過嘗試次數
        if attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="嘗試次數過多、請重新註冊")

        # 3. 過期
        try:
            exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() > exp:
                await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
                await db.commit()
                raise HTTPException(status_code=400, detail="驗證碼已過期、請重新註冊")
        except ValueError:
            raise HTTPException(status_code=400, detail="驗證資料異常、請重新註冊")

        # 4. attempt++
        await db.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE email=?", (email,))

        # 5. 比對 code
        if stored_code != code:
            await db.commit()
            raise HTTPException(status_code=400, detail="驗證碼錯誤")

        # 6. 通過 → 二次檢查 email 沒被搶建（race condition 防禦）
        if await (await db.execute("SELECT id FROM users WHERE email=?", (email,))).fetchone():
            await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
            await db.commit()
            raise HTTPException(status_code=409, detail="此 email 已被註冊")

        # 7. 建 user（直接 INSERT、不走 create_user 因為 password_hash 已預先 hash 好）
        cur = await db.execute(
            "INSERT INTO users (email, display_name, password_hash) VALUES (?, ?, ?)",
            (email, display_name, pw_hash),
        )
        uid = cur.lastrowid

        # 8. 刪暫存
        await db.execute("DELETE FROM email_verifications WHERE email=?", (email,))
        await db.commit()

    # 9. 建 session
    user = await auth_mod.get_user_by_id(uid)
    token = await auth_mod.create_session(uid)
    return {"user": user, "token": token}


@app.post("/api/auth/register")
async def auth_register(payload: dict = Body(...)):
    """[DEPRECATED 2026-05-22] 舊「手機驗證碼」註冊已淘汰、改用 email 驗證碼兩階段流程：
    - POST /api/auth/register-request：寄驗證碼到 email
    - POST /api/auth/register-verify：輸入驗證碼 + 建帳號
    """
    raise HTTPException(
        status_code=410,
        detail="此端點已淘汰、請改用 /api/auth/register-request + /api/auth/register-verify",
    )


@app.post("/api/auth/find-email")
async def auth_find_email(payload: dict = Body(...)):
    """[DEPRECATED 2026-05-22] 現在不再用手機註冊、email 直接就是登入帳號、不需要此端點"""
    raise HTTPException(
        status_code=410,
        detail="此端點已淘汰、email 就是登入帳號、無需手機找回",
    )


def _mask_email(e: str) -> str:
    """abc@example.com → a**c@example.com"""
    if not e or "@" not in e: return e
    user, dom = e.split("@", 1)
    if len(user) <= 2: m = user[0] + "*"
    else: m = user[0] + "*" * (len(user) - 2) + user[-1]
    return f"{m}@{dom}"


# ===== 忘記密碼 =====

@app.post("/api/auth/forgot-password")
async def auth_forgot_password(payload: dict = Body(...)):
    """寄重設密碼連結到 email
    回應永遠 ok（避免外洩 email 是否存在）
    payload: {email}
    """
    email = (payload.get("email") or "").strip().lower()
    out = {"ok": True, "message": "若該 email 已註冊，重設連結已寄出"}
    if not email:
        return out
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, display_name FROM users WHERE email=?", (email,))
        u = await cur.fetchone()
        if not u:
            return out  # 不洩漏存在性
        token = _secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, u[0], expires),
        )
        await db.commit()
    # TODO: 對接真實郵件（SendGrid / SES / SMTP）
    frontend_base = os.getenv("CARDPOOL_FRONTEND_URL", "http://localhost:8080").rstrip("/")
    reset_link = f"{frontend_base}/index.html#/reset?token={token}"
    print(f"[EMAIL] {email} → 重設密碼連結 {reset_link} (1 小時內有效)")
    if _is_dev_mode():
        out["dev_token"] = token
        out["dev_link"] = reset_link
    return out


@app.post("/api/auth/reset-password")
async def auth_reset_password(payload: dict = Body(...)):
    """用 token 重設密碼
    payload: {token, password, password2}
    """
    token = (payload.get("token") or "").strip()
    password = payload.get("password", "")
    password2 = payload.get("password2", "")
    if not token:
        raise HTTPException(status_code=400, detail="無效 token")
    if password != password2:
        raise HTTPException(status_code=400, detail="兩次密碼不一致")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 字元")
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id, used, expires_at FROM password_resets WHERE token=?",
            (token,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="無效或過期的 token")
        user_id, used, expires_at = row
        if used:
            raise HTTPException(status_code=400, detail="此連結已使用過")
        try:
            exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
            if datetime.utcnow() > exp:
                raise HTTPException(status_code=400, detail="連結已過期")
        except ValueError:
            raise HTTPException(status_code=400, detail="無效 token")
        # 更新密碼
        try:
            new_hash = auth_mod.hash_password(password)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        await db.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user_id))
        await db.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
        # 撤銷該 user 全部 session（強制重新登入）
        await db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        await db.commit()
    return {"ok": True, "message": "密碼已重設，請重新登入"}


@app.post("/api/auth/login")
async def auth_login(payload: dict = Body(...)):
    email = payload.get("email", "")
    password = payload.get("password", "")
    user = await auth_mod.authenticate(email, password)
    if not user:
        raise HTTPException(status_code=401, detail="email 或密碼錯誤")
    token = await auth_mod.create_session(user["id"])
    return {"user": user, "token": token}


@app.post("/api/auth/logout")
async def auth_logout(authorization: str = Header(None)):
    if authorization and authorization.lower().startswith("bearer "):
        await auth_mod.delete_session(authorization.split(" ", 1)[1].strip())
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user: dict = Depends(auth_mod.get_current_user)):
    return {"user": user}


@app.get("/api/orderbook/{set_id}/{card_number}")
async def api_orderbook(set_id: str, card_number: str, grade: int = 10):
    return await mp.get_orderbook(set_id, card_number, grade)


@app.post("/api/listings")
async def api_create_listing(payload: dict = Body(...),
                             user: dict = Depends(auth_mod.get_current_user)):
    return await mp.create_listing(user["id"], payload)


@app.delete("/api/listings/{listing_id}")
async def api_cancel_listing(listing_id: int,
                             user: dict = Depends(auth_mod.get_current_user)):
    return await mp.cancel_listing(user["id"], listing_id)


@app.post("/api/bids")
async def api_create_bid(payload: dict = Body(...),
                         user: dict = Depends(auth_mod.get_current_user)):
    return await mp.create_bid(user["id"], payload)


@app.delete("/api/bids/{bid_id}")
async def api_cancel_bid(bid_id: int,
                         user: dict = Depends(auth_mod.get_current_user)):
    return await mp.cancel_bid(user["id"], bid_id)


@app.delete("/api/bids/{bid_id}/record")
async def api_delete_bid_record(bid_id: int,
                                user: dict = Depends(auth_mod.get_current_user)):
    """刪除已結束（cancelled / expired / matched）的 bid 紀錄"""
    return await mp.delete_bid_record(user["id"], bid_id)


@app.delete("/api/listings/{listing_id}/record")
async def api_delete_listing_record(listing_id: int,
                                    user: dict = Depends(auth_mod.get_current_user)):
    """刪除已結束（cancelled / expired / sold）的 listing 紀錄"""
    return await mp.delete_listing_record(user["id"], listing_id)


@app.get("/api/me/listings")
async def api_my_listings(status: str = "",
                          user: dict = Depends(auth_mod.get_current_user)):
    return {"listings": await mp.my_listings(user["id"], status or None)}


@app.get("/api/me/bids")
async def api_my_bids(status: str = "",
                      user: dict = Depends(auth_mod.get_current_user)):
    return {"bids": await mp.my_bids(user["id"], status or None)}


@app.get("/api/me/trades")
async def api_my_trades(user: dict = Depends(auth_mod.get_current_user)):
    return {"trades": await mp.my_trades(user["id"])}


# ==================== 評價系統 + 等級 + 徽章 ====================

def _compute_user_level(trade_count: int) -> dict:
    """根據完成交易數計算等級
    LV1: 0-4 trades (新手)
    LV2: 5-19  (一般)
    LV3: 20-49 (熟手)
    LV4: 50-99 (老練)
    LV5: 100-249 (專家)
    LV6: 250-499 (大師)
    LV7: 500+ (傳奇)
    """
    levels = [
        (0, 1, "新手"),
        (5, 2, "一般"),
        (20, 3, "熟手"),
        (50, 4, "老練"),
        (100, 5, "專家"),
        (250, 6, "大師"),
        (500, 7, "傳奇"),
    ]
    lv, name = 1, "新手"
    next_at = 5
    for threshold, level, label in levels:
        if trade_count >= threshold:
            lv, name = level, label
        else:
            next_at = threshold
            break
    return {"level": lv, "label": name, "trades": trade_count, "next_at": next_at if lv < 7 else None}


def _compute_badges(trade_count: int, avg_rating: float, rating_count: int) -> list:
    """根據等級 + 評分計算徽章"""
    out = []
    # 等級徽章
    if trade_count >= 100: out.append({"key": "expert", "label": "🌟 專家賣家", "type": "level"})
    elif trade_count >= 50: out.append({"key": "veteran", "label": "💎 老練", "type": "level"})
    elif trade_count >= 20: out.append({"key": "skilled", "label": "✨ 熟手", "type": "level"})
    # 優良用戶徽章
    if rating_count >= 10 and avg_rating >= 4.8: out.append({"key": "excellent", "label": "🏆 優良用戶", "type": "honor"})
    elif rating_count >= 5 and avg_rating >= 4.5: out.append({"key": "trusted", "label": "👍 高信譽", "type": "honor"})
    return out


async def _user_stats(db, user_id: int) -> dict:
    """合算 user 交易數 + 收到的評分平均"""
    # 完成交易數（buyer + seller 都算）
    cur = await db.execute(
        "SELECT COUNT(*) FROM trades WHERE (buyer_id=? OR seller_id=?) AND status='completed'",
        (user_id, user_id),
    )
    trade_count = (await cur.fetchone())[0] or 0
    # 別人給的評分
    cur = await db.execute(
        "SELECT AVG(rating), COUNT(*) FROM trade_ratings WHERE ratee_id=?",
        (user_id,),
    )
    row = await cur.fetchone()
    avg_rating = float(row[0]) if row[0] is not None else 0.0
    rating_count = row[1] or 0
    return {
        "trade_count": trade_count,
        "avg_rating": round(avg_rating, 2),
        "rating_count": rating_count,
        "level": _compute_user_level(trade_count),
        "badges": _compute_badges(trade_count, avg_rating, rating_count),
    }


@app.get("/api/users/{user_id}/profile")
async def api_user_profile(user_id: int):
    """公開 user profile：display_name + 等級 + 評分 + 徽章"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, display_name FROM users WHERE id=?", (user_id,))
        u = await cur.fetchone()
        if not u:
            raise HTTPException(status_code=404, detail="user not found")
        stats = await _user_stats(db, user_id)
        return {
            "user_id": user_id,
            "display_name": u["display_name"] or f"用戶 {user_id}",
            **stats,
        }


@app.get("/api/me/profile")
async def api_my_profile(user: dict = Depends(auth_mod.get_current_user)):
    """我自己的 profile"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        stats = await _user_stats(db, user["id"])
    return {
        "user_id": user["id"],
        "display_name": user.get("display_name") or user.get("email", "").split("@")[0],
        "email": user.get("email"),
        **stats,
    }


@app.post("/api/trades/{trade_id}/rating")
async def api_post_rating(trade_id: int, payload: dict = Body(...),
                          user: dict = Depends(auth_mod.get_current_user)):
    """對特定 trade 評分對方
    payload: {rating: 1-5, comment?: str}
    自動推導被評者：trade.buyer_id != user → ratee=buyer，反之
    """
    rating_val = int(payload.get("rating", 0))
    if rating_val < 1 or rating_val > 5:
        raise HTTPException(status_code=400, detail="rating 必須 1-5")
    comment = (payload.get("comment") or "")[:500]
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT buyer_id, seller_id, status FROM trades WHERE id=?", (trade_id,)
        )
        t = await cur.fetchone()
        if not t:
            raise HTTPException(status_code=404, detail="trade not found")
        if user["id"] not in (t["buyer_id"], t["seller_id"]):
            raise HTTPException(status_code=403, detail="不是這筆交易的當事人")
        if t["status"] != "completed":
            raise HTTPException(status_code=400, detail="交易未完成、無法評價")
        role = "buyer" if user["id"] == t["buyer_id"] else "seller"
        ratee_id = t["seller_id"] if role == "buyer" else t["buyer_id"]
        # 防重複評（UNIQUE(trade_id, rater_id)）
        try:
            await db.execute(
                """INSERT INTO trade_ratings (trade_id, rater_id, ratee_id, role, rating, comment)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (trade_id, user["id"], ratee_id, role, rating_val, comment),
            )
            await db.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"已評過或重複：{e}")
    return {"ok": True, "trade_id": trade_id, "rating": rating_val}


@app.get("/api/trades/{trade_id}/rating")
async def api_get_rating(trade_id: int,
                         user: dict = Depends(auth_mod.get_current_user)):
    """看自己對該 trade 是否已評過 + 對方對自己的評價"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        my = await (await db.execute(
            "SELECT rating, comment, created_at FROM trade_ratings WHERE trade_id=? AND rater_id=?",
            (trade_id, user["id"]),
        )).fetchone()
        their = await (await db.execute(
            "SELECT rating, comment, created_at FROM trade_ratings WHERE trade_id=? AND ratee_id=?",
            (trade_id, user["id"]),
        )).fetchone()
    return {
        "my_rating_to_other": dict(my) if my else None,
        "received_rating": dict(their) if their else None,
    }


# ==================== 站內私訊（聯絡買賣家） ====================

@app.post("/api/messages")
async def api_send_message(payload: dict = Body(...),
                            user: dict = Depends(auth_mod.get_current_user)):
    """送訊息給對方
    payload: {receiver_id, body, trade_id? (optional)}
    """
    receiver_id = int(payload.get("receiver_id", 0))
    body = (payload.get("body") or "").strip()[:1000]
    trade_id = payload.get("trade_id")
    if not receiver_id or not body:
        raise HTTPException(status_code=400, detail="receiver_id 與 body 必填")
    if receiver_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能傳給自己")
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        # 驗證 receiver 存在
        cur = await db.execute("SELECT 1 FROM users WHERE id=?", (receiver_id,))
        if not await cur.fetchone():
            raise HTTPException(status_code=404, detail="收件者不存在")
        # 若帶 trade_id，驗證雙方都是當事人
        if trade_id:
            cur = await db.execute(
                "SELECT buyer_id, seller_id FROM trades WHERE id=?", (trade_id,)
            )
            t = await cur.fetchone()
            if not t:
                raise HTTPException(status_code=404, detail="交易不存在")
            if {user["id"], receiver_id} != {t[0], t[1]}:
                raise HTTPException(status_code=403, detail="非該交易當事人")
        cur = await db.execute(
            "INSERT INTO messages (trade_id, sender_id, receiver_id, body) VALUES (?, ?, ?, ?)",
            (trade_id, user["id"], receiver_id, body),
        )
        msg_id = cur.lastrowid
        await db.commit()
    return {"ok": True, "id": msg_id}


@app.get("/api/me/messages/threads")
async def api_my_threads(user: dict = Depends(auth_mod.get_current_user)):
    """列出我的對話對象（每個對方一個 thread，顯示最後一則訊息 + 未讀數）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 每個對方的最後一則訊息 + 未讀數
        rows = await (await db.execute("""
            WITH pairs AS (
                SELECT
                    CASE WHEN sender_id=? THEN receiver_id ELSE sender_id END AS other_id,
                    id, body, created_at, sender_id, receiver_id, read_at, trade_id
                FROM messages
                WHERE sender_id=? OR receiver_id=?
            ),
            latest AS (
                SELECT other_id, MAX(created_at) AS last_at FROM pairs GROUP BY other_id
            )
            SELECT p.other_id, p.body AS last_body, p.created_at AS last_at,
                   p.sender_id AS last_sender, u.display_name AS other_name,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.sender_id = p.other_id AND m.receiver_id = ? AND m.read_at IS NULL) AS unread
            FROM pairs p
            JOIN latest l ON l.other_id = p.other_id AND l.last_at = p.created_at
            JOIN users u ON u.id = p.other_id
            ORDER BY p.created_at DESC
        """, (user["id"], user["id"], user["id"], user["id"]))).fetchall()
    return {"threads": [dict(r) for r in rows]}


@app.get("/api/me/messages/with/{other_id}")
async def api_thread_messages(other_id: int, limit: int = 100,
                               user: dict = Depends(auth_mod.get_current_user)):
    """跟某 user 的完整對話"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT id, sender_id, receiver_id, body, read_at, created_at, trade_id
            FROM messages
            WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
            ORDER BY created_at ASC
            LIMIT ?
        """, (user["id"], other_id, other_id, user["id"], limit))).fetchall()
        # 標記對方傳給我的為已讀
        await db.execute(
            "UPDATE messages SET read_at=CURRENT_TIMESTAMP WHERE sender_id=? AND receiver_id=? AND read_at IS NULL",
            (other_id, user["id"]),
        )
        # 對方 profile (display_name)
        u = await (await db.execute("SELECT id, display_name FROM users WHERE id=?", (other_id,))).fetchone()
        await db.commit()
    return {
        "other": dict(u) if u else {"id": other_id, "display_name": f"用戶 {other_id}"},
        "messages": [dict(r) for r in rows],
    }


@app.get("/api/me/messages/unread-count")
async def api_unread_count(user: dict = Depends(auth_mod.get_current_user)):
    """未讀訊息總數（用於導航小紅點）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE receiver_id=? AND read_at IS NULL",
            (user["id"],),
        )
        n = (await cur.fetchone())[0]
    return {"unread": n}


@app.get("/api/users/{user_id}/ratings")
async def api_user_ratings(user_id: int, limit: int = 20):
    """公開 user 收到的評價（最新 N 筆）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT tr.rating, tr.comment, tr.role, tr.created_at,
                      u.display_name AS rater_name
               FROM trade_ratings tr
               JOIN users u ON u.id = tr.rater_id
               WHERE tr.ratee_id = ?
               ORDER BY tr.created_at DESC
               LIMIT ?""",
            (user_id, limit),
        )).fetchall()
    return {"ratings": [dict(r) for r in rows]}


# ==================== Watchlist 心心收藏 ====================

@app.get("/api/me/watchlist")
async def api_my_watchlist(user: dict = Depends(auth_mod.get_current_user)):
    """取得使用者所有收藏卡，附帶卡片資料 + 最新價"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute("""
            SELECT w.id AS watch_id, w.set_id, w.card_number, w.created_at,
                   cl.name, cl.name_jp, cl.name_zh, cl.image_url,
                   cs.name AS set_name, cs.name_zh AS set_name_zh,
                   (SELECT price_twd FROM card_prices cp
                     WHERE cp.set_id=w.set_id AND cp.card_number=w.card_number
                     ORDER BY COALESCE(sale_date, created_at) DESC LIMIT 1) AS latest_price
            FROM watchlists w
            LEFT JOIN card_list cl ON cl.set_id=w.set_id AND cl.card_number=w.card_number
            LEFT JOIN card_sets cs ON cs.set_id=w.set_id
            WHERE w.user_id = ?
            ORDER BY w.created_at DESC
        """, (user["id"],))).fetchall()
    return {"watchlist": [dict(r) for r in rows]}


@app.get("/api/me/watchlist/check/{set_id}/{card_number}")
async def api_watchlist_check(set_id: str, card_number: str,
                               user: dict = Depends(auth_mod.get_current_user)):
    """檢查指定卡片是否已收藏（前端心心 icon 狀態用）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            "SELECT 1 FROM watchlists WHERE user_id=? AND set_id=? AND card_number=? LIMIT 1",
            (user["id"], set_id, card_number),
        )).fetchone()
    return {"watched": row is not None}


@app.post("/api/me/watchlist/{set_id}/{card_number}")
async def api_watchlist_add(set_id: str, card_number: str,
                             user: dict = Depends(auth_mod.get_current_user)):
    """加入收藏"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO watchlists (user_id, set_id, card_number) VALUES (?, ?, ?)",
                (user["id"], set_id, card_number),
            )
            await db.commit()
        except Exception:
            pass  # UNIQUE constraint：已存在就不動
    return {"ok": True, "watched": True}


@app.delete("/api/me/watchlist/{set_id}/{card_number}")
async def api_watchlist_remove(set_id: str, card_number: str,
                                user: dict = Depends(auth_mod.get_current_user)):
    """移除收藏"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM watchlists WHERE user_id=? AND set_id=? AND card_number=?",
            (user["id"], set_id, card_number),
        )
        await db.commit()
    return {"ok": True, "watched": False}


# ==================== 分類：寶可夢 / 訓練家 ====================

@app.get("/pokemon")
async def category_pokemon_page():
    """寶可夢分類頁（HTML）"""
    p = os.path.join(static_path, "liff", "category-pokemon.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="page not found")


@app.get("/characters")
async def category_characters_page():
    """訓練家/角色分類頁（HTML）"""
    p = os.path.join(static_path, "liff", "category-character.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="page not found")


@app.get("/category/pokemon/{pkm_id}")
async def category_pokemon_detail(pkm_id: int):
    """寶可夢詳情頁（顯示該寶可夢出現過的卡）"""
    p = os.path.join(static_path, "liff", "category-detail.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="page not found")


@app.get("/category/character/{char_id}")
async def category_character_detail(char_id: int):
    """訓練家詳情頁"""
    p = os.path.join(static_path, "liff", "category-detail.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="page not found")


@app.get("/api/category/pokemon/list")
async def category_pokemon_list():
    """所有寶可夢清單"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, name_en, name_jp FROM pokemon_dict ORDER BY id"
        )).fetchall()
        return [
            {"id": r["id"], "name_en": r["name_en"], "name_jp": r["name_jp"]}
            for r in rows
        ]


@app.get("/api/category/character/list")
async def category_character_list():
    """所有訓練家/角色清單（含代表圖：優先 artofpkm 訓練家頭像，fallback 找代表卡圖）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT cd.id, cd.name_en, cd.name_jp,
                   COALESCE(
                     cd.image_url,
                     (SELECT cl.image_url FROM card_list cl
                      WHERE (cl.name LIKE cd.name_en || '%' OR cl.name LIKE '%' || cd.name_en || ' %')
                        AND cl.image_url IS NOT NULL
                      LIMIT 1)
                   ) AS image_url,
                   (cd.image_url IS NOT NULL) AS has_artofpkm
            FROM character_dict cd
            ORDER BY (cd.image_url IS NULL), cd.name_en
            """
        )).fetchall()
        return [
            {
                "id": r["id"],
                "name_en": r["name_en"],
                "name_jp": r["name_jp"],
                "image_url": r["image_url"],
            }
            for r in rows
        ]


@app.get("/api/category/pokemon/{pkm_id}/cards")
async def category_pokemon_cards(pkm_id: int):
    """某寶可夢出現過的卡（依英文名 / 日文名 contain 比對）"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT name_en, name_jp FROM pokemon_dict WHERE id=?", (pkm_id,)
        )
        pkm = await cur.fetchone()
        if not pkm:
            raise HTTPException(status_code=404, detail="pokemon not found")

        name_en = pkm["name_en"]
        name_jp = pkm["name_jp"]

        # 詞邊界比對：純名（exact）+ 前後接空白 + 中日文模糊
        # 不加 exact match 會漏掉純名「Bulbasaur」這類 (現在 cl.name 大多是純名沒後綴)
        sql = """
            SELECT cl.set_id, cs.name AS set_name, cs.name_jp AS set_name_jp,
                   cl.card_number, cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity
            FROM card_list cl
            LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
            WHERE (cl.name = ?
                   OR cl.name LIKE ? OR cl.name LIKE ? OR cl.name LIKE ?
                   OR cl.name_jp LIKE ?)
              AND cl.image_url IS NOT NULL
            ORDER BY cs.release_date DESC, cl.set_id, CAST(cl.card_number AS INTEGER)
            LIMIT 1000
        """
        en = name_en
        params = (
            en,                                    # 純名（最重要）
            f"{en} %", f"% {en} %", f"% {en}",     # 詞邊界
            f"%{name_jp}%" if name_jp else "____", # 日文 fallback
        )
        rows = await (await db.execute(sql, params)).fetchall()
        out_rows = [dict(r) for r in rows]
        for r in out_rows:
            sid = r.get("set_id") or ""
            if sid.startswith("jp-") and r.get("name_jp"):
                zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
                if zh:
                    r["name_zh"] = zh

        return {
            "pokemon": {"id": pkm_id, "name_en": name_en, "name_jp": name_jp},
            "count": len(out_rows),
            "cards": out_rows,
        }


@app.get("/api/category/character/{char_id}/cards")
async def category_character_cards(char_id: int):
    """某訓練家/角色出現過的卡"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT name_en, name_jp FROM character_dict WHERE id=?", (char_id,)
        )
        ch = await cur.fetchone()
        if not ch:
            raise HTTPException(status_code=404, detail="character not found")

        name_en = ch["name_en"]
        name_jp = ch["name_jp"]
        sql = """
            SELECT cl.set_id, cs.name AS set_name, cs.name_jp AS set_name_jp,
                   cl.card_number, cl.name, cl.name_jp, cl.name_zh, cl.image_url, cl.rarity
            FROM card_list cl
            LEFT JOIN card_sets cs ON cs.set_id = cl.set_id
            WHERE (cl.name = ?
                   OR cl.name LIKE ? OR cl.name LIKE ? OR cl.name LIKE ?
                   OR cl.name_jp LIKE ?)
              AND cl.image_url IS NOT NULL
            ORDER BY cs.release_date DESC, cl.set_id, CAST(cl.card_number AS INTEGER)
            LIMIT 1000
        """
        params = (
            name_en,
            f"{name_en} %", f"% {name_en} %", f"% {name_en}",
            f"%{name_jp}%" if name_jp else "____",
        )
        rows = await (await db.execute(sql, params)).fetchall()
        out_rows = [dict(r) for r in rows]
        for r in out_rows:
            sid = r.get("set_id") or ""
            if sid.startswith("jp-") and r.get("name_jp"):
                zh = await _translate_jp_card_name_to_zh(r.get("name_jp"), db)
                if zh:
                    r["name_zh"] = zh
        return {
            "character": {"id": char_id, "name_en": name_en, "name_jp": name_jp},
            "count": len(out_rows),
            "cards": out_rows,
        }


# ==================== Admin: eBay blocklist & revalidation ====================

@app.get("/api/admin/blocklist")
async def admin_list_blocklist(limit: int = 100):
    """列出最近加入的 ebay_blocklist 項目"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM ebay_blocklist ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )).fetchall()
        total = (await (await db.execute("SELECT COUNT(*) FROM ebay_blocklist")).fetchone())[0]
        return {"total": total, "items": [dict(r) for r in rows]}


@app.post("/api/admin/blocklist/{item_id}")
async def admin_add_blocklist(item_id: str, request: Request):
    """手動把 eBay item_id 加入 blocklist 並刪掉現有 card_prices 紀錄。

    body 可選 {"reason": "..."}。回傳刪除筆數。
    """
    import aiosqlite
    item_id = re.sub(r"\D", "", item_id)
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id must be digits")
    try:
        body = await request.json()
        reason = body.get("reason") or "manual user flag"
    except Exception:
        reason = "manual user flag"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO ebay_blocklist (item_id, reason, detected_url)
            VALUES (?, ?, ?)
        """, (item_id, reason, ""))
        cur = await db.execute(
            "DELETE FROM card_prices WHERE source='ebay' AND listing_url LIKE ?",
            (f"%/itm/{item_id}%",),
        )
        await db.commit()
        return {"item_id": item_id, "deleted_rows": cur.rowcount, "reason": reason}


@app.delete("/api/admin/blocklist/{item_id}")
async def admin_remove_blocklist(item_id: str):
    """從 blocklist 移除（後續 sync 會重新接受該 item）"""
    import aiosqlite
    item_id = re.sub(r"\D", "", item_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM ebay_blocklist WHERE item_id=?", (item_id,))
        await db.commit()
        return {"item_id": item_id, "removed": cur.rowcount}


@app.post("/api/admin/revalidate-ebay")
async def admin_revalidate_ebay(limit: int = 100, recheck_days: int = None,
                                  concurrency: int = 3):
    """手動觸發 eBay 重驗批次（預設 100 筆，避免 API 卡太久）"""
    from app.jobs.revalidate_ebay import revalidate_batch
    stats = await asyncio.to_thread(
        revalidate_batch, limit, recheck_days, None, None, concurrency, 1.2, True
    )
    return stats


@app.post("/api/admin/revalidate-ebay/{set_id}/{card_number}")
async def admin_revalidate_card(set_id: str, card_number: str):
    """重驗某張卡的所有 ebay 紀錄（用於 user 反映該卡有壞資料）"""
    from app.jobs.revalidate_ebay import revalidate_card
    stats = await asyncio.to_thread(
        revalidate_card, set_id, card_number, concurrency=3, throttle=1.2, verbose=True
    )
    return stats


@app.get("/api/admin/sync-history-stats")
async def admin_sync_history_stats():
    """sync history 表統計：被自動跳過的卡數、有 hit 的卡數等"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # 確保表在
        try:
            from app.jobs.sync_all_cards import ensure_history_table
            ensure_history_table()
        except Exception:
            pass
        total = (await (await db.execute("SELECT COUNT(*) FROM card_sync_history")).fetchone())[0]
        zero_2 = (await (await db.execute(
            "SELECT COUNT(*) FROM card_sync_history WHERE zero_hit_count >= 2 AND total_hits = 0"
        )).fetchone())[0]
        zero_5 = (await (await db.execute(
            "SELECT COUNT(*) FROM card_sync_history WHERE zero_hit_count >= 5 AND total_hits = 0"
        )).fetchone())[0]
        had_hits = (await (await db.execute(
            "SELECT COUNT(*) FROM card_sync_history WHERE total_hits > 0"
        )).fetchone())[0]
        return {
            "tracked_cards": total,
            "skipped_in_backfill": zero_2,
            "skipped_in_refresh": zero_5,
            "ever_had_hits": had_hits,
        }


@app.post("/api/admin/sync-history-reset")
async def admin_sync_history_reset(set_id: str = None):
    """清掉 history 讓某個 set（或全部）重新嘗試。"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        if set_id:
            cur = await db.execute("DELETE FROM card_sync_history WHERE set_id=?", (set_id,))
        else:
            cur = await db.execute("DELETE FROM card_sync_history")
        await db.commit()
        return {"deleted": cur.rowcount, "set_id": set_id or "all"}


@app.post("/api/admin/sync-all")
async def admin_sync_all(mode: str = "all", stale_days: int = 1,
                          limit: int = 200, prefix: str = None,
                          concurrency: int = 3):
    """手動觸發全卡 sync。
    mode: backfill (從沒抓過) / refresh (stale > stale_days) / all
    """
    from app.jobs.sync_all_cards import sync_batch
    stats = await asyncio.to_thread(
        sync_batch, mode, stale_days, limit, prefix, concurrency, 0.5, True
    )
    return stats


@app.get("/api/admin/sync-all-stats")
async def admin_sync_all_stats():
    """全卡 sync 覆蓋率與新鮮度"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        total = (await (await db.execute("SELECT COUNT(*) FROM card_list")).fetchone())[0]
        with_any = (await (await db.execute(
            "SELECT COUNT(DISTINCT set_id || '|' || card_number) FROM card_prices"
        )).fetchone())[0]
        with_ebay = (await (await db.execute(
            "SELECT COUNT(DISTINCT set_id || '|' || card_number) FROM card_prices WHERE source='ebay'"
        )).fetchone())[0]
        with_snkr = (await (await db.execute(
            "SELECT COUNT(DISTINCT set_id || '|' || card_number) FROM card_prices WHERE source='snkrdunk'"
        )).fetchone())[0]
        # stale 計算
        stale_1d = (await (await db.execute("""
            SELECT COUNT(*) FROM (
              SELECT cl.set_id, cl.card_number, MAX(cp.created_at) ls
              FROM card_list cl JOIN card_prices cp
                ON cp.set_id=cl.set_id AND cp.card_number=cl.card_number
              GROUP BY cl.set_id, cl.card_number
              HAVING (julianday('now') - julianday(MAX(cp.created_at))) >= 1
            )
        """)).fetchone())[0]
        return {
            "card_list_total": total,
            "with_any_price": with_any,
            "with_ebay": with_ebay,
            "with_snkr": with_snkr,
            "never_synced": total - with_any,
            "stale_over_1day": stale_1d,
            "coverage_pct": round(with_any / total * 100, 1) if total else 0,
        }


@app.post("/api/admin/refresh-snkr")
async def admin_refresh_snkr(limit: int = 100, concurrency: int = 3):
    """手動觸發 SNKRDUNK PSA10 價格刷新批次"""
    from app.jobs.refresh_snkr import refresh_batch
    stats = await asyncio.to_thread(
        refresh_batch, limit, None, None, concurrency, 1.0, True
    )
    return stats


@app.post("/api/admin/refresh-snkr/{set_id}/{card_number}")
async def admin_refresh_snkr_card(set_id: str, card_number: str):
    """重抓單張卡的 SNKR PSA10 紀錄"""
    from app.jobs.refresh_snkr import refresh_batch
    stats = await asyncio.to_thread(
        refresh_batch, 1, set_id, card_number, 1, 0.5, True
    )
    return stats


@app.get("/api/admin/snkr-stats")
async def admin_snkr_stats():
    """SNKR 資料新鮮度總覽"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        total_records = (await (await db.execute(
            "SELECT COUNT(*) FROM card_prices WHERE source='snkrdunk'"
        )).fetchone())[0]
        cards_with_snkr = (await (await db.execute(
            "SELECT COUNT(DISTINCT set_id || '|' || card_number) FROM card_prices WHERE source='snkrdunk'"
        )).fetchone())[0]
        # 新鮮度分布：last_sync 距離 < 1 / 7 / 30 天
        from datetime import datetime, timedelta
        now = datetime.now()
        rows = await (await db.execute("""
            SELECT set_id, card_number, MAX(created_at) AS last_sync
            FROM card_prices WHERE source='snkrdunk'
            GROUP BY set_id, card_number
        """)).fetchall()
        last_syncs = [r["last_sync"] for r in rows]
        def days_ago(s):
            try:
                return (now - datetime.fromisoformat(s.replace(' ', 'T'))).days
            except Exception: return None
        d = [days_ago(s) for s in last_syncs]
        d = [x for x in d if x is not None]
        buckets = {
            "<= 1 day": sum(1 for x in d if x <= 1),
            "<= 7 days": sum(1 for x in d if x <= 7),
            "<= 30 days": sum(1 for x in d if x <= 30),
            "> 30 days": sum(1 for x in d if x > 30),
        }
        return {
            "total_records": total_records,
            "cards_with_snkr": cards_with_snkr,
            "freshness": buckets,
            "oldest_sync": min(last_syncs) if last_syncs else None,
            "newest_sync": max(last_syncs) if last_syncs else None,
        }


@app.get("/api/admin/ebay-stats")
async def admin_ebay_stats():
    """eBay 資料/驗證狀態總覽"""
    import aiosqlite
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        # distinct item_id 數
        cur = await db.execute("""
            SELECT listing_url FROM card_prices
            WHERE source='ebay' AND listing_url LIKE '%/itm/%'
        """)
        ids = set()
        for r in await cur.fetchall():
            m = re.search(r"/itm/(\d+)", r["listing_url"] or "")
            if m: ids.add(m.group(1))
        total_items = len(ids)

        block_count = (await (await db.execute("SELECT COUNT(*) FROM ebay_blocklist")).fetchone())[0]

        check_rows = await (await db.execute("""
            SELECT result, COUNT(*) c FROM ebay_url_check GROUP BY result
        """)).fetchall()
        check_breakdown = {r["result"]: r["c"] for r in check_rows}
        checked_total = sum(check_breakdown.values())

        oldest = (await (await db.execute(
            "SELECT MIN(last_checked_at) FROM ebay_url_check"
        )).fetchone())[0]
        latest = (await (await db.execute(
            "SELECT MAX(last_checked_at) FROM ebay_url_check"
        )).fetchone())[0]

        return {
            "total_unique_items": total_items,
            "blocklisted": block_count,
            "verified_total": checked_total,
            "verified_breakdown": check_breakdown,
            "oldest_check": oldest,
            "latest_check": latest,
            "remaining_to_verify": max(0, total_items - checked_total - block_count),
        }


# ==================== Admin: 排程任務即時控制（暫停/停止/重啟）====================

def _system_memory_info():
    """取得系統與當前 process 的記憶體用量；若沒裝 psutil 則回基本資訊"""
    info = {}
    try:
        import psutil
        vm = psutil.virtual_memory()
        info["system"] = {
            "total_mb": round(vm.total / 1024 / 1024),
            "used_mb": round(vm.used / 1024 / 1024),
            "available_mb": round(vm.available / 1024 / 1024),
            "percent": vm.percent,
        }
        proc = psutil.Process(os.getpid())
        rss_mb = round(proc.memory_info().rss / 1024 / 1024)
        children = proc.children(recursive=True)
        children_rss = 0
        chrome_count = 0
        chrome_rss = 0
        for c in children:
            try:
                rss = c.memory_info().rss / 1024 / 1024
                children_rss += rss
                name = (c.name() or "").lower()
                if "chrome" in name or "chromium" in name:
                    chrome_count += 1
                    chrome_rss += rss
            except Exception:
                pass
        info["process"] = {
            "pid": os.getpid(),
            "rss_mb": rss_mb,
            "children_count": len(children),
            "children_rss_mb": round(children_rss),
            "chromium_count": chrome_count,
            "chromium_rss_mb": round(chrome_rss),
            "total_mb": round(rss_mb + children_rss),
        }
    except ImportError:
        info["error"] = "psutil 未安裝；pip install psutil 後即可顯示記憶體用量"
    except Exception as e:
        info["error"] = str(e)
    return info


def _fmt_duration(s) -> str:
    if s is None: return "—"
    s = max(0, int(s))
    if s < 60: return f"{s} 秒"
    if s < 3600: return f"{s // 60} 分鐘"
    if s < 86400:
        h = s / 3600; return f"{h:.1f} 小時"
    return f"{s / 86400:.1f} 天"


async def _db_rate_window(db, sql_template) -> float:
    """從多個時間窗（30/180/720/1440 分）取速率，取最大值。

    取最大值是因為短窗可能正好碰到 server 閒置（剛啟動 / 冷卻 / 暫停），
    用最大值代表「系統實際跑起來時的速率」，ETA 才不會被低估。
    """
    rates = []
    for window_min in (30, 180, 720, 1440):
        try:
            n = (await (await db.execute(
                sql_template.format(win=window_min)
            )).fetchone())[0]
        except Exception:
            return 0.0
        if n >= 3:
            rates.append(n / (window_min * 60))
    return max(rates) if rates else 0.0


async def _compute_jobs_progress():
    """從 DB 計算各 job 的 done/total + 最近 DB 活動速率（給 ETA 當 fallback）"""
    import aiosqlite
    out = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # ebay-revalidator
        try:
            cur = await db.execute("""
                SELECT listing_url FROM card_prices
                WHERE source='ebay' AND listing_url LIKE '%/itm/%'
            """)
            items_in_prices = set()
            for r in await cur.fetchall():
                m = re.search(r"/itm/(\d+)", r["listing_url"] or "")
                if m: items_in_prices.add(m.group(1))
            total = len(items_in_prices)
            checked_ids = {r["item_id"] for r in await (await db.execute(
                "SELECT item_id FROM ebay_url_check"
            )).fetchall()}
            blocked_ids = {r["item_id"] for r in await (await db.execute(
                "SELECT item_id FROM ebay_blocklist"
            )).fetchall()}
            verified = items_in_prices & (checked_ids | blocked_ids)
            done = len(verified)
            remaining = max(0, total - done)
            db_rate = await _db_rate_window(db, """
                SELECT COUNT(*) FROM ebay_url_check
                WHERE last_checked_at >= datetime('now', '-{win} minutes')
            """)
            out["ebay-revalidator"] = {
                "label": "已驗證 eBay 項目",
                "done": done, "total": total, "remaining": remaining,
                "active_remaining": remaining,
                "percent": round(done / total * 100, 1) if total else 100.0,
                "_db_rate": db_rate,
            }
        except Exception as e:
            out["ebay-revalidator"] = {"error": str(e)}

        # sync-all：分階段，backfill 用 never，refresh 用 stale
        try:
            total = (await (await db.execute(
                "SELECT COUNT(*) FROM card_list"
            )).fetchone())[0]
            done = (await (await db.execute(
                "SELECT COUNT(DISTINCT set_id || '|' || card_number) FROM card_prices"
            )).fetchone())[0]
            stale = (await (await db.execute("""
                SELECT COUNT(*) FROM (
                  SELECT cl.set_id, cl.card_number, MAX(cp.created_at) ls
                  FROM card_list cl JOIN card_prices cp
                    ON cp.set_id=cl.set_id AND cp.card_number=cl.card_number
                  GROUP BY cl.set_id, cl.card_number
                  HAVING (julianday('now') - julianday(MAX(cp.created_at))) >= 1
                )
            """)).fetchone())[0]
            never = max(0, total - done)
            # 階段：先 backfill 再 refresh
            if never > 0:
                phase = "backfill"
                active_remaining = never
                extra = f"backfill 中；另有 {stale} 張過期需 refresh"
            else:
                phase = "refresh"
                active_remaining = stale
                extra = "backfill 完成；refresh 階段"
            db_rate = await _db_rate_window(db, """
                SELECT COUNT(DISTINCT set_id || '|' || card_number)
                FROM card_prices
                WHERE created_at >= datetime('now', '-{win} minutes')
            """)
            out["sync-all"] = {
                "label": f"全卡覆蓋率（{phase} 階段）",
                "done": done, "total": total,
                "remaining": never, "stale": stale,
                "active_remaining": active_remaining,
                "phase": phase,
                "percent": round(done / total * 100, 1) if total else 0.0,
                "extra": extra,
                "_db_rate": db_rate,
            }
        except Exception as e:
            out["sync-all"] = {"error": str(e)}

    return out


def _attach_eta(job, progress: dict) -> dict:
    """從 job.stats 推算 rate、ETA。優先順序：本批 batch 速率 > 最近 DB 活動速率"""
    if "error" in progress:
        return progress
    s = job.stats or {}
    # 階段剩餘量（sync-all 在 backfill 階段用 never、refresh 用 stale）
    rem = progress.get("active_remaining", progress.get("remaining", 0))
    rate = None
    rate_source = None

    # 1) 用最近一批的耗時（最即時、最準）
    bd = s.get("batch_duration_s")
    lb = s.get("last_batch") or {}
    if bd and bd > 0:
        if lb.get("checked"):
            rate = lb["checked"] / bd; rate_source = "batch"
        elif lb.get("cards"):
            rate = lb["cards"] / bd; rate_source = "batch"

    # 2) fallback：DB 最近 30 分活動量
    if not rate or rate <= 0:
        db_rate = progress.get("_db_rate") or 0
        if db_rate > 0:
            rate = db_rate
            rate_source = "db"

    if rate and rate > 0 and rem > 0:
        progress["rate_per_min"] = round(rate * 60, 1)
        progress["rate_source"] = rate_source
        progress["eta_seconds"] = int(rem / rate)
        progress["eta_label"] = _fmt_duration(rem / rate)
    elif rem == 0:
        progress["rate_per_min"] = None
        progress["rate_source"] = None
        progress["eta_seconds"] = 0
        progress["eta_label"] = "已完成"
    else:
        progress["rate_per_min"] = None
        progress["rate_source"] = None
        progress["eta_seconds"] = None
        progress["eta_label"] = "—"

    if job.is_paused:
        progress["eta_label"] = "暫停中（不前進）"
    elif not job.is_running:
        progress["eta_label"] = "未執行"
    progress.pop("_db_rate", None)
    return progress


def _hot_sets_round_progress(job) -> dict:
    """hot-sets 是循環任務 → 顯示「本輪」進度"""
    s = job.stats or {}
    total = s.get("round_total")
    idx = s.get("round_idx") or 0
    started = s.get("round_started_at")
    if not total:
        return {"label": "本輪刷新進度", "done": 0, "total": 0,
                "percent": 0, "remaining": 0,
                "eta_label": "尚未開始本輪"}
    pct = round(idx / total * 100, 1)
    out = {
        "label": "本輪刷新進度",
        "done": idx, "total": total,
        "remaining": max(0, total - idx),
        "percent": pct,
    }
    if started and idx > 0:
        elapsed = time.time() - started
        per = elapsed / idx
        out["rate_per_min"] = round(60 / per, 1) if per > 0 else None
        out["eta_seconds"] = int(per * (total - idx))
        out["eta_label"] = _fmt_duration(per * (total - idx))
    else:
        out["eta_label"] = "—"
    if job.is_paused:
        out["eta_label"] = "暫停中（不前進）"
    elif not job.is_running:
        out["eta_label"] = "未執行"
    return out


@app.get("/admin/jobs")
async def admin_jobs_page():
    """排程任務即時控制 UI"""
    p = os.path.join(static_path, "liff", "admin-jobs.html")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read(), media_type="text/html; charset=utf-8")
    raise HTTPException(status_code=404, detail="admin-jobs.html not found")


@app.get("/api/admin/jobs")
async def admin_list_jobs():
    """列出所有排程任務的即時狀態 + 進度 + 系統記憶體"""
    db_progress = await _compute_jobs_progress()
    job_dicts = []
    for j in job_ctrl.jobs.values():
        d = j.to_dict()
        if j.name == "hot-sets":
            d["progress"] = _hot_sets_round_progress(j)
        elif j.name in db_progress:
            d["progress"] = _attach_eta(j, db_progress[j.name])
        else:
            d["progress"] = None
        job_dicts.append(d)
    return {
        "jobs": job_dicts,
        "memory": _system_memory_info(),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


@app.post("/api/admin/jobs/{name}/pause")
async def admin_job_pause(name: str):
    j = job_ctrl.get(name)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    ok = j.pause()
    return {"name": name, "ok": ok, "status": j.to_dict()}


@app.post("/api/admin/jobs/{name}/resume")
async def admin_job_resume(name: str):
    j = job_ctrl.get(name)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    ok = j.resume()
    return {"name": name, "ok": ok, "status": j.to_dict()}


@app.post("/api/admin/jobs/{name}/stop")
async def admin_job_stop(name: str):
    j = job_ctrl.get(name)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    ok = j.stop()
    return {"name": name, "ok": ok, "status": j.to_dict()}


@app.post("/api/admin/jobs/{name}/start")
async def admin_job_start(name: str):
    j = job_ctrl.get(name)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    ok = j.start()
    return {"name": name, "ok": ok, "status": j.to_dict()}


@app.get("/api/admin/backfill/settings")
async def admin_backfill_get_settings():
    """取得 backfill job 目前的參數設定（concurrency / batch size 等）"""
    from app.jobs.backfill_prices import get_settings
    return get_settings()


@app.post("/api/admin/backfill/settings")
async def admin_backfill_update_settings(payload: dict):
    """改 backfill job 參數。有效 keys: lang/concurrency/batch_size/cooldown/max_batches

    注意：只有在 job 還沒啟動時改才生效。已 running 的會用既有設定跑完。
    """
    from app.jobs.backfill_prices import update_settings, get_settings
    allowed = {"lang", "concurrency", "batch_size", "cooldown", "max_batches"}
    cleaned = {k: v for k, v in (payload or {}).items() if k in allowed}
    if "lang" in cleaned and cleaned["lang"] not in ("jp", "en", "all"):
        raise HTTPException(status_code=400, detail="lang must be jp/en/all")
    update_settings(**cleaned)
    return {"ok": True, "settings": get_settings()}


@app.post("/api/admin/jobs/close-browsers")
async def admin_close_browsers():
    """強制關閉所有 chromium worker 釋放記憶體（下次 sync 會自動重開）"""
    try:
        from app.scraper.browser_pool import close_browser, _registry
        before = len(_registry)
        close_browser()
        return {"ok": True, "browsers_closed": before, "memory": _system_memory_info()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 開發/測試用 ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
