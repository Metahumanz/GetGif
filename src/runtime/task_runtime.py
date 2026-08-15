import os
import time
import traceback

from ..core.config import DEFAULT_CONFIG, HEARTBEAT_GIVE_UP, HEARTBEAT_TIMEOUT
from ..media.encoder_catalog import (
    get_encoder_catalog,
    normalize_video_codec,
    normalize_video_encoder,
    resolve_encoder,
)
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


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class TaskRuntime:
    def __init__(self, activity_monitor: ActivityMonitor, scan_cache: ScanCache, history_store: TaskHistoryStore):
        self.activity_monitor = activity_monitor
        self.scan_cache = scan_cache
        self.state_store = TaskStateStore()
        self.history_runtime = TaskHistoryRuntime(self.state_store, history_store)
        self.queue_manager = TaskQueueManager(self.state_store, self.run_task, self.activity_monitor)

    def _wait_heartbeat(self, task_id: str) -> bool:
        """等待浏览器心跳；返回 True 表示任务应停止（用户取消或心跳中断超时）。

        心跳短暂中断（HEARTBEAT_TIMEOUT 秒内）不暂停；超过该阈值时任务“暂停”等待，
        心跳恢复后自动继续；超过 HEARTBEAT_GIVE_UP 秒仍无心跳才真正停止任务。
        若任务开启了 keep_running（关闭页面后继续），则完全不受心跳影响。
        """
        while True:
            with self.state_store.task_lock:
                task = self.state_store.tasks.get(task_id)
                if not task:
                    return True
                if task.get("cancelled"):
                    return True

                # 用户选择“关闭页面后继续”：不因心跳中断暂停或停止
                if task.get("params", {}).get("keep_running"):
                    return False

                last_hb = self.state_store.heartbeat_ts.get(task_id, 0)
                gap = time.time() - last_hb if last_hb else 0
                if gap <= HEARTBEAT_TIMEOUT:
                    if task.get("paused"):
                        task["paused"] = False
                        self.state_store.append_log_entry(task, "info", "心跳恢复，任务继续执行")
                    return False

                if gap > HEARTBEAT_GIVE_UP:
                    task["cancelled"] = True
                    task["cancel_reason"] = "timeout"
                    task["paused"] = False
                    return True

                if not task.get("paused"):
                    task["paused"] = True
                    self.state_store.append_log_entry(task, "warn", "心跳中断，任务已暂停，等待浏览器恢复...")

            # 暂停期间保活（防止程序整体退出），每秒重查一次心跳
            self.activity_monitor.touch()
            time.sleep(1)

    def _on_task_progress(self, task_id, video_index, total_videos, video_name, status, message="", gif_progress=0, step_index=0, steps_per_video=1):
        # 任务在处理时保活，防止长时间运行且页面无心跳时程序整体退出
        self.activity_monitor.touch()
        self.state_store.update_task_progress(
            task_id, video_index, total_videos, video_name, status, message, gif_progress, step_index, steps_per_video
        )

    def run_task(self, task_id: str, source_dir: str, output_dir: str, params: dict, cached_videos: list[dict] | None = None):
        if not self.state_store.mark_task_started(task_id, output_dir, cached_videos is not None):
            return

        try:
            self.activity_monitor.touch()
            videos = cached_videos if cached_videos is not None else discover_videos(source_dir)
            total = len(videos)

            if not self.state_store.apply_scan_result(task_id, total):
                self.history_runtime.archive_task(task_id)
                return

            done_count = 0
            error_count = 0
            skip_count = 0

            for index, video in enumerate(videos):
                if self._wait_heartbeat(task_id):
                    reason = self.state_store.get_cancel_reason(task_id)
                    if reason == "timeout":
                        self.state_store.mark_task_timeout(task_id)
                    else:
                        self.state_store.mark_task_cancelled(task_id)
                    self.history_runtime.archive_task(task_id)
                    return

                self.state_store.mark_video_started(task_id, video["name"])

                result = extract_outputs(
                    video,
                    output_dir,
                    params,
                    is_cancelled=lambda: self.state_store.is_task_cancelled(task_id),
                    on_progress=lambda status, message="", gif_progress=0, step_index=0, steps_per_video=1: self._on_task_progress(
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
        task_params["video_codec"] = normalize_video_codec(task_params.get("video_codec", "h264"))
        task_params["video_encoder"] = normalize_video_encoder(task_params.get("video_encoder", "auto"))
        task_params["keep_running"] = _to_bool(task_params.get("keep_running", False))
        task_params["output_name_template"] = (
            str(task_params.get("output_name_template", DEFAULT_CONFIG["output_name_template"])).strip()
            or DEFAULT_CONFIG["output_name_template"]
        )
        if task_params["export_mode"] == "mp4":
            # 提前校验编码器，避免任务跑到一半才发现编码器不可用
            resolve_encoder(task_params["video_codec"], task_params["video_encoder"], get_encoder_catalog())
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
