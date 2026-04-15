import os
import traceback

from ..core.config import DEFAULT_CONFIG
from ..media.video_pipeline import (
    collect_scan_results,
    discover_videos,
    extract_outputs,
    normalize_export_mode,
    normalize_image_format,
)
from ..stores.history_store import TaskHistoryStore
from ..stores.scan_cache import ScanCache
from .activity_monitor import ActivityMonitor
from .task_history_runtime import TaskHistoryRuntime
from .task_queue import TaskQueueManager
from .task_state import TaskStateStore


class TaskRuntime:
    def __init__(self, activity_monitor: ActivityMonitor, scan_cache: ScanCache, history_store: TaskHistoryStore):
        self.activity_monitor = activity_monitor
        self.scan_cache = scan_cache
        self.state_store = TaskStateStore()
        self.history_runtime = TaskHistoryRuntime(self.state_store, history_store)
        self.queue_manager = TaskQueueManager(self.state_store, self.run_task)

    def run_task(self, task_id: str, source_dir: str, output_dir: str, params: dict, cached_videos: list[dict] | None = None):
        if not self.state_store.mark_task_started(task_id, output_dir, cached_videos is not None):
            return

        try:
            videos = cached_videos if cached_videos is not None else discover_videos(source_dir)
            total = len(videos)

            if not self.state_store.apply_scan_result(task_id, total):
                self.history_runtime.archive_task(task_id)
                return

            done_count = 0
            error_count = 0
            skip_count = 0

            for index, video in enumerate(videos):
                if self.state_store.is_task_cancelled(task_id):
                    self.state_store.mark_task_cancelled(task_id)
                    self.history_runtime.archive_task(task_id)
                    return

                self.state_store.mark_video_started(task_id, video["name"])

                result = extract_outputs(
                    video,
                    output_dir,
                    params,
                    is_cancelled=lambda: self.state_store.is_task_cancelled(task_id),
                    on_progress=lambda status, message="", gif_progress=0, step_index=0, steps_per_video=1: self.state_store.update_task_progress(
                        task_id,
                        index,
                        total,
                        video["name"],
                        status,
                        message,
                        gif_progress,
                        step_index,
                        steps_per_video,
                    ),
                )
                self.state_store.record_video_result(task_id, video["name"], result)

                if result["status"] == "done":
                    done_count += 1
                elif result["status"] == "error":
                    error_count += 1
                elif result["status"] == "skipped":
                    skip_count += 1

            self.state_store.mark_task_finished(task_id, total, done_count, error_count, skip_count)
            self.history_runtime.archive_task(task_id)
        except Exception as exc:
            self.state_store.mark_task_failed(task_id, exc)
            self.history_runtime.archive_task(task_id)
            traceback.print_exc()

    def create_task(self, source_dir: str, output_dir: str, params: dict, scan_id: str = "") -> dict:
        os.makedirs(output_dir, exist_ok=True)

        cached_videos = self.scan_cache.get(scan_id, source_dir)
        task_params = {**DEFAULT_CONFIG, **params}
        task_params["export_mode"] = normalize_export_mode(task_params.get("export_mode", "gif"))
        task_params["image_format"] = normalize_image_format(task_params.get("image_format", "png"))
        task_params["output_name_template"] = (
            str(task_params.get("output_name_template", DEFAULT_CONFIG["output_name_template"])).strip()
            or DEFAULT_CONFIG["output_name_template"]
        )
        return self.state_store.create_task(source_dir, output_dir, task_params, cached_videos)

    def get_task_status(self, task_id: str) -> dict | None:
        return self.state_store.get_task_snapshot(task_id)

    def cancel_task(self, task_id: str) -> bool:
        exists, should_archive = self.state_store.cancel_task_request(task_id)
        if not exists:
            return False
        if should_archive:
            self.history_runtime.archive_task(task_id)
        return True

    def heartbeat(self, task_id: str = "") -> str:
        self.activity_monitor.touch()
        return self.state_store.heartbeat(task_id)

    def list_task_dashboard(self) -> dict:
        return self.history_runtime.list_dashboard()

    def get_task_log_text(self, task_id: str) -> tuple[str, str] | None:
        return self.history_runtime.get_task_log_text(task_id)

    def scan_videos(self, source_dir: str) -> dict:
        videos = discover_videos(source_dir)
        if not videos:
            return {"count": 0, "videos": [], "scan_id": self.scan_cache.store(source_dir, [])}

        return {
            "count": len(videos),
            "videos": collect_scan_results(videos),
            "scan_id": self.scan_cache.store(source_dir, videos),
        }
