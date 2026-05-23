"""
Playwright 每線程瀏覽器管理
Playwright sync_api 的 greenlet 無法跨線程共用，所以使用 thread-local。
每個 worker 線程自己持有一個 browser，後續呼叫可重用（不需要重啟 Chromium）。

2026-05-21 升級（Path B2 browser recycle）：
- 加 thread-local context 計數器、每 N 個 context 就 close + relaunch browser
- 清光累積的 cookies + browser fingerprint、避免 eBay 持續跑久就 silent throttle
- Stealth 不在此層套用、由 caller (ebay.py) per-page 呼叫 apply_stealth_sync
"""
import atexit
import threading
from playwright.sync_api import sync_playwright, Browser, Playwright


# 每線程開過多少個 context 就強制 recycle browser。
# 5/21 probe 實證：fresh stealth browser 對單卡可拿 95+ raw listings；連跑 100 卡（200 context）
# 後絕大多數空頁。throttle 從 ~10-20 卡（20-40 context）就開始。
# 改 30 = 約每 15 卡 recycle 一次、cold start 約 +10s/recycle ≈ 額外 +0.7s/卡 平均成本。
RECYCLE_AFTER_N_CONTEXTS = 30


_tls = threading.local()
_registry_lock = threading.Lock()
_registry: list = []  # (playwright, browser) tuples for cleanup


def _close_thread_browser() -> None:
    """關掉此線程的 browser + playwright (recycle 用)，從 registry 移除避免 atexit 重複 close。

    2026-05-21 bug fix：recycle 時順手清 ebay scraper 的 cookies cache
    （否則新 browser context 仍會 inject 舊 cookies、recycle 等於白做）。
    """
    browser = getattr(_tls, "browser", None)
    pw = getattr(_tls, "playwright", None)
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass
    with _registry_lock:
        _registry[:] = [(p, b) for (p, b) in _registry if p is not pw and b is not browser]
    _tls.browser = None
    _tls.playwright = None

    # Cross-module clear: ebay scraper 有獨立 _tls.ebay_cookies cache、
    # recycle browser 時必須一起清、否則 fresh browser 載 stale cookies = 等於沒 recycle
    try:
        from app.scraper import ebay as _ebay_mod
        _ebay_mod._tls.ebay_cookies = None
        _ebay_mod._tls.ebay_cookies_at = 0
    except Exception:
        pass


def get_browser() -> Browser:
    """取得此線程的 Chromium（若未啟動則啟動，重用可省 5-10 秒冷啟）

    每呼叫一次 += 1 context_count；達 RECYCLE_AFTER_N_CONTEXTS 就 close + relaunch。
    """
    count = getattr(_tls, "context_count", 0)
    if count >= RECYCLE_AFTER_N_CONTEXTS:
        # 達到 recycle 閾值、強制重啟 browser（清光 cookies + fingerprint）
        _close_thread_browser()
        count = 0

    browser = getattr(_tls, "browser", None)
    if browser is not None:
        try:
            if browser.is_connected():
                _tls.context_count = count + 1
                return browser
        except Exception:
            pass

    # 啟動新 browser
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
        ],
    )
    _tls.playwright = pw
    _tls.browser = browser
    _tls.context_count = 1

    with _registry_lock:
        _registry.append((pw, browser))

    return browser


def close_browser():
    """關閉所有線程的瀏覽器（關閉應用時呼叫）"""
    with _registry_lock:
        for pw, browser in _registry:
            try:
                browser.close()
            except Exception:
                pass
            try:
                pw.stop()
            except Exception:
                pass
        _registry.clear()


atexit.register(close_browser)
