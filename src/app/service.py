from ..platform.system_ops import SystemOps
from ..runtime.activity_monitor import ActivityMonitor
from ..runtime.task_runtime import TaskRuntime
from ..stores.config_store import ConfigStore
from ..stores.history_store import TaskHistoryStore
from ..stores.scan_cache import ScanCache


class GetGifService:
    def __init__(self):
        self.config_store = ConfigStore()
        self.activity_monitor = ActivityMonitor()
        self.scan_cache = ScanCache()
        self.history_store = TaskHistoryStore()
        self.task_runtime = TaskRuntime(self.activity_monitor, self.scan_cache, self.history_store)
        self.system_ops = SystemOps()

    def load_config(self) -> dict:
        return self.config_store.load()

    def save_config(self, config: dict):
        self.config_store.save(config)

    def create_task(self, source_dir: str, output_dir: str, params: dict, scan_id: str = "") -> dict:
        return self.task_runtime.create_task(source_dir, output_dir, params, scan_id)

    def get_task_status(self, task_id: str) -> dict | None:
        return self.task_runtime.get_task_status(task_id)

    def list_task_dashboard(self) -> dict:
        return self.task_runtime.list_task_dashboard()

    def get_task_log_text(self, task_id: str) -> tuple[str, str] | None:
        return self.task_runtime.get_task_log_text(task_id)

    def cancel_task(self, task_id: str) -> bool:
        return self.task_runtime.cancel_task(task_id)

    def heartbeat(self, task_id: str = "") -> str:
        return self.task_runtime.heartbeat(task_id)

    def scan_videos(self, source_dir: str) -> dict:
        return self.task_runtime.scan_videos(source_dir)

    def browse_directory(self) -> str:
        return self.system_ops.browse_directory()

    def open_folder(self, path: str) -> bool:
        return self.system_ops.open_folder(path)
