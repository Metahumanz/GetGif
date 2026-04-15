import json
import threading

from ..core.config import HISTORY_FILE, MAX_HISTORY_ITEMS
from ..core.task_helpers import format_log_lines


class TaskHistoryStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._items = []
        self._load()

    def _load(self):
        if not HISTORY_FILE.exists():
            return
        try:
            with HISTORY_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                self._items = data[:MAX_HISTORY_ITEMS]
        except Exception as exc:
            print(f"加载历史记录失败: {exc}")

    def _save(self):
        try:
            with HISTORY_FILE.open("w", encoding="utf-8") as handle:
                json.dump(self._items[:MAX_HISTORY_ITEMS], handle, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"保存历史记录失败: {exc}")

    def archive(self, entry: dict):
        with self._lock:
            self._items = [item for item in self._items if item.get("id") != entry.get("id")]
            self._items.insert(0, entry)
            self._items = self._items[:MAX_HISTORY_ITEMS]
            self._save()

    def list_summaries(self) -> list[dict]:
        with self._lock:
            return [{key: value for key, value in item.items() if key != "logs"} for item in self._items]

    def get_log_text(self, task_id: str) -> tuple[str, str] | None:
        with self._lock:
            for item in self._items:
                if item.get("id") == task_id:
                    return (format_log_lines(item.get("logs", [])), f"getgif_{task_id}.log.txt")
        return None
