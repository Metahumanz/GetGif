import logging
import threading
import time

from ..core.task_helpers import now_iso
from .task_state import TaskStateStore


class HeartbeatFilter(logging.Filter):
    def filter(self, record):
        return "/api/heartbeat" not in record.getMessage()


class TaskQueueManager:
    def __init__(self, state_store: TaskStateStore, run_task_callback, activity_monitor=None):
        self.state_store = state_store
        self.run_task_callback = run_task_callback
        self.activity_monitor = activity_monitor
        self.active_task_id = None
        self._configure_logging()
        threading.Thread(target=self._queue_worker, daemon=True).start()

    def _configure_logging(self):
        log = logging.getLogger("werkzeug")
        if not any(isinstance(item, HeartbeatFilter) for item in log.filters):
            log.addFilter(HeartbeatFilter())

    def _has_keep_running_task_locked(self) -> bool:
        """是否存在尚未结束的“关闭页面后继续”任务（排队中或执行中）。"""
        for task in self.state_store.tasks.values():
            if task.get("cancelled"):
                continue
            if task["status"] in {"queued", "scanning", "processing"} and task.get("params", {}).get("keep_running"):
                return True
        return False

    def _queue_worker(self):
        while True:
            task_to_run = None

            with self.state_store.task_lock:
                if self.active_task_id:
                    active = self.state_store.tasks.get(self.active_task_id)
                    if active and active["status"] in {"queued", "scanning", "processing"} and not active.get("cancelled"):
                        task_to_run = None
                    else:
                        self.active_task_id = None

                if task_to_run is None and self.active_task_id is None:
                    for task in self.state_store.tasks.values():
                        if task["status"] == "queued" and not task.get("cancelled"):
                            self.active_task_id = task["id"]
                            task["started_at"] = now_iso()
                            self.state_store.append_log_entry(task, "info", "任务进入执行阶段")
                            task_to_run = (
                                task["id"],
                                task["source_dir"],
                                task["output_dir"],
                                dict(task.get("params", {})),
                                task.get("cached_videos"),
                            )
                            break

                # “关闭页面后继续”的任务在排队期间也要保活，防止页面关闭后程序自动退出
                if self.activity_monitor and self._has_keep_running_task_locked():
                    self.activity_monitor.touch()

            if task_to_run is None:
                time.sleep(0.5)
                continue

            try:
                self.run_task_callback(*task_to_run)
            finally:
                with self.state_store.task_lock:
                    if self.active_task_id == task_to_run[0]:
                        self.active_task_id = None
