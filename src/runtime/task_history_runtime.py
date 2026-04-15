from ..stores.history_store import TaskHistoryStore
from .task_state import TaskStateStore


class TaskHistoryRuntime:
    def __init__(self, state_store: TaskStateStore, history_store: TaskHistoryStore):
        self.state_store = state_store
        self.history_store = history_store

    def archive_task(self, task_id: str):
        entry = self.state_store.mark_task_archived(task_id)
        if entry:
            self.history_store.archive(entry)

    def list_dashboard(self) -> dict:
        current, queue = self.state_store.list_live_tasks()
        return {
            "current": current,
            "queue": queue,
            "history": self.history_store.list_summaries(),
        }

    def get_task_log_text(self, task_id: str) -> tuple[str, str] | None:
        return self.state_store.get_live_log_text(task_id) or self.history_store.get_log_text(task_id)
