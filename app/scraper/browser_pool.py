"""
Playwright 每線程瀏覽器管理
Playwright sync_api 的 greenlet 無法跨線程共用，所以使用 thread-local。
每個 worker 線程自己持有一個 browser，後續呼叫可重用（不需要重啟 Chromium）。
"""
import atexit
import threading
from playwright.sync_api import sync_playwright, Browser, Playwright


_tls = threading.local()
_registry_lock = threading.Lock()
_registry: list = []  # (playwright, browser) tuples for cleanup


def get_browser() -> Browser:
    """取得此線程的 Chromium（若未啟動則啟動，重用可省 5-10 秒冷啟）"""
    browser = getattr(_tls, "browser", None)
    if browser is not None:
        try:
            if browser.is_connected():
                return browser
        except Exception:
            pass

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
