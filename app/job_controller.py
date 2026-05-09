"""背景排程任務的可控制器。

提供 pause / resume / stop / start，以及目前狀態查詢，給 /admin/jobs UI 使用。
每個 Job 有一個 factory（產生 coroutine 的 function），這樣 stop 後可以再 start。
"""
import asyncio
import time
from typing import Callable, Dict, Optional


class Job:
    def __init__(self, name: str, label: str = "",
                 factory: Optional[Callable] = None):
        self.name = name
        self.label = label or name
        self._factory = factory
        self._pause_event: Optional[asyncio.Event] = None
        self._signal_event: Optional[asyncio.Event] = None
        self._stop_flag = False
        self._task: Optional[asyncio.Task] = None
        self.status = "idle"
        self.activity = "等待啟動"
        self.last_update = time.time()
        self.stats: Dict = {}
        self.error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.batches_done = 0

    def _ensure_events(self):
        if self._pause_event is None:
            self._pause_event = asyncio.Event()
            self._pause_event.set()
        if self._signal_event is None:
            self._signal_event = asyncio.Event()

    @property
    def is_paused(self) -> bool:
        return self._pause_event is not None and not self._pause_event.is_set()

    @property
    def is_stopped(self) -> bool:
        return self._stop_flag

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def wait_if_paused(self):
        """暫停點：在每次批次開始前呼叫，讓使用者可以攔下下一批。"""
        self._ensure_events()
        if not self._pause_event.is_set():
            self.set_status("paused", "已暫停，等待恢復")
            await self._pause_event.wait()
            if not self._stop_flag:
                self.set_status("running", "已恢復，繼續執行")

    async def sleep_or_signal(self, seconds: float):
        """睡 N 秒，但 pause/stop 會立即喚醒（避免長 sleep 卡住）。"""
        self._ensure_events()
        self._signal_event.clear()
        try:
            await asyncio.wait_for(self._signal_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def pause(self) -> bool:
        self._ensure_events()
        if not self.is_running:
            return False
        self._pause_event.clear()
        self._signal_event.set()
        self.set_status("paused", "已暫停（等下批結束）")
        return True

    def resume(self) -> bool:
        self._ensure_events()
        if not self.is_running:
            return False
        self._stop_flag = False
        self._pause_event.set()
        self._signal_event.set()
        self.set_status("running", "恢復執行")
        return True

    def stop(self) -> bool:
        self._ensure_events()
        if not self.is_running:
            self.set_status("stopped", "已停止")
            return False
        self._stop_flag = True
        self._pause_event.set()
        self._signal_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self.set_status("stopping", "停止中…")
        return True

    def start(self) -> bool:
        """啟動 / 重啟此 job。"""
        if self.is_running:
            return False
        if self._factory is None:
            return False
        self._ensure_events()
        self._stop_flag = False
        self._pause_event.set()
        self.error = None
        self.batches_done = 0
        coro = self._factory(self)
        self._task = asyncio.create_task(coro)
        self._task.add_done_callback(self._on_task_done)
        self.started_at = time.time()
        self.set_status("running", "啟動中")
        return True

    def _on_task_done(self, task: asyncio.Task):
        """task 結束時自動把狀態翻成 stopped 或 finished/error"""
        try:
            if task.cancelled():
                self.set_status("stopped", "已停止")
            elif task.exception() is not None:
                self.error = repr(task.exception())
                self.set_status("error", f"錯誤：{task.exception()}")
            else:
                self.set_status("finished", "已完成")
        except asyncio.CancelledError:
            self.set_status("stopped", "已停止")
        except Exception:
            pass

    def set_status(self, status: str, activity: str = "", **stats):
        self.status = status
        if activity:
            self.activity = activity
        self.last_update = time.time()
        if stats:
            self.stats.update(stats)

    def bump_batch(self, **stats):
        self.batches_done += 1
        if stats:
            self.stats.update(stats)
        self.last_update = time.time()

    def to_dict(self):
        return {
            "name": self.name,
            "label": self.label,
            "status": self.status,
            "is_paused": self.is_paused,
            "is_stopped": self.is_stopped,
            "is_running": self.is_running,
            "activity": self.activity,
            "last_update": self.last_update,
            "last_update_iso": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(self.last_update)
            ),
            "stats": self.stats,
            "error": self.error,
            "started_at": self.started_at,
            "batches_done": self.batches_done,
        }


class JobController:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}

    def register(self, name: str, label: str = "",
                 factory: Optional[Callable] = None) -> Job:
        if name in self.jobs:
            j = self.jobs[name]
            if label:
                j.label = label
            if factory:
                j._factory = factory
            return j
        j = Job(name, label, factory)
        self.jobs[name] = j
        return j

    def get(self, name: str) -> Optional[Job]:
        return self.jobs.get(name)

    def list_all(self):
        return [j.to_dict() for j in self.jobs.values()]


jobs = JobController()
