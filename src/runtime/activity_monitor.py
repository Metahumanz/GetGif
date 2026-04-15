import os
import threading
import time

from ..core.config import AUTO_EXIT_DELAY


class ActivityMonitor:
    def __init__(self, timeout_seconds: int = AUTO_EXIT_DELAY, check_interval: int = 2):
        self.timeout_seconds = timeout_seconds
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._last_activity_time = time.time()
        threading.Thread(target=self._monitor, daemon=True).start()

    def touch(self):
        with self._lock:
            self._last_activity_time = time.time()

    def _monitor(self):
        while True:
            time.sleep(self.check_interval)
            with self._lock:
                inactive_duration = time.time() - self._last_activity_time

            if inactive_duration > self.timeout_seconds:
                print(f"检测到 {self.timeout_seconds} 秒无活动，正在退出程序...")
                os._exit(0)
