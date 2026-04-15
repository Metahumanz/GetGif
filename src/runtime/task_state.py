import threading
import time
import uuid

from ..core.config import DEFAULT_CONFIG
from ..core.task_helpers import format_log_lines, now_iso
from ..media.video_pipeline import normalize_export_mode, normalize_image_format


class TaskStateStore:
    def __init__(self):
        self.tasks = {}
        self.heartbeat_ts = {}
        self.task_lock = threading.Lock()

    def append_log_entry(self, task: dict, level: str, message: str):
        logs = task.setdefault("logs", [])
        logs.append({"time": now_iso(), "level": level, "message": message})
        if len(logs) > 1000:
            del logs[:-1000]

    def get_queue_position_locked(self, task_id: str) -> int | None:
        queued_ids = [item["id"] for item in self.tasks.values() if item["status"] == "queued" and not item.get("cancelled")]
        if task_id not in queued_ids:
            return None
        return queued_ids.index(task_id) + 1

    def build_task_snapshot(self, task: dict, include_logs: bool = False) -> dict:
        params = task.get("params", {})
        export_mode = normalize_export_mode(params.get("export_mode", "gif"))
        image_format = normalize_image_format(params.get("image_format", "png"))
        snapshot = {
            "id": task["id"],
            "status": task["status"],
            "source_dir": task["source_dir"],
            "output_dir": task["output_dir"],
            "created_at": task.get("created_at"),
            "started_at": task.get("started_at"),
            "finished_at": task.get("finished_at"),
            "total_videos": task.get("total_videos", 0),
            "progress": task.get("progress"),
            "error": task.get("error"),
            "summary": task.get("summary"),
            "export_mode": export_mode,
            "image_format": image_format,
            "output_name_template": params.get("output_name_template", DEFAULT_CONFIG["output_name_template"]),
            "num_outputs": params.get("num_gifs", DEFAULT_CONFIG["num_gifs"]),
            "queue_position": self.get_queue_position_locked(task["id"]),
            "latest_message": task.get("progress", {}).get("message") if task.get("progress") else "",
            "log_count": len(task.get("logs", [])),
        }
        if include_logs:
            snapshot["logs"] = list(task.get("logs", []))
        return snapshot

    def create_task(self, source_dir: str, output_dir: str, task_params: dict, cached_videos: list[dict] | None) -> dict:
        task_id = str(uuid.uuid4())[:8]
        with self.task_lock:
            task = {
                "id": task_id,
                "source_dir": source_dir,
                "output_dir": output_dir,
                "status": "queued",
                "created_at": now_iso(),
                "started_at": None,
                "finished_at": None,
                "total_videos": 0,
                "progress": None,
                "cancelled": False,
                "params": task_params,
                "cached_videos": cached_videos,
                "logs": [],
            }
            self.append_log_entry(task, "info", f"任务已创建，待处理目录: {source_dir}")
            self.tasks[task_id] = task
            self.heartbeat_ts[task_id] = time.time()
            queue_position = self.get_queue_position_locked(task_id)
            self.append_log_entry(task, "info", f"任务已加入队列，排队位置: {queue_position}")

        return {"task_id": task_id, "status": "queued", "queue_position": queue_position}

    def get_task_snapshot(self, task_id: str) -> dict | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            return self.build_task_snapshot(task)

    def list_live_tasks(self) -> tuple[dict | None, list[dict]]:
        with self.task_lock:
            current = None
            queue = []
            for task in self.tasks.values():
                if task["status"] in {"scanning", "processing"}:
                    current = self.build_task_snapshot(task)
                elif task["status"] == "queued" and not task.get("cancelled"):
                    queue.append(self.build_task_snapshot(task))
        return current, queue

    def get_live_log_text(self, task_id: str) -> tuple[str, str] | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            return (format_log_lines(task.get("logs", [])), f"getgif_{task_id}.log.txt")

    def mark_task_archived(self, task_id: str) -> dict | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task or task.get("archived", False):
                return None
            task["archived"] = True
            return self.build_task_snapshot(task, include_logs=True)

    def mark_task_started(self, task_id: str, output_dir: str, has_cached_videos: bool) -> bool:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            task["status"] = "processing" if has_cached_videos else "scanning"
            task["start_time"] = time.time()
            task["started_at"] = task.get("started_at") or now_iso()
            self.heartbeat_ts[task_id] = time.time()
            self.append_log_entry(task, "info", f"任务开始，输出目录: {output_dir}")
        return True

    def apply_scan_result(self, task_id: str, total: int) -> bool:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False

            task["total_videos"] = total
            if total == 0:
                task["status"] = "done"
                task["finished_at"] = now_iso()
                task["summary"] = {"total": 0, "done": 0, "error": 0, "skipped": 0}
                task["progress"] = {"overall": 100, "message": "未找到视频"}
                self.append_log_entry(task, "warn", "任务结束：未找到可处理视频")
                return False

            task["status"] = "processing"
            self.append_log_entry(task, "info", f"扫描完成，共 {total} 个视频")
            return True

    def mark_video_started(self, task_id: str, video_name: str):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if task:
                self.append_log_entry(task, "info", f"开始处理视频: {video_name}")

    def record_video_result(self, task_id: str, video_name: str, result: dict):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return

            if result["status"] == "done":
                self.append_log_entry(task, "ok", f"{video_name} 完成，共生成 {len(result['outputs'])} 个文件")
            elif result["status"] == "error":
                self.append_log_entry(task, "err", f"{video_name} 失败: {result['error']}")
            elif result["status"] == "skipped":
                self.append_log_entry(task, "warn", f"{video_name} 跳过: {result['error']}")
            elif result["status"] == "cancelled":
                self.append_log_entry(task, "warn", f"视频处理被取消: {video_name}")

    def mark_task_cancelled(self, task_id: str):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "cancelled"
            task["finished_at"] = now_iso()
            self.append_log_entry(task, "warn", "任务已取消")

    def mark_task_finished(self, task_id: str, total: int, done_count: int, error_count: int, skip_count: int):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "done"
            task["finished_at"] = now_iso()
            task["summary"] = {
                "total": total,
                "done": done_count,
                "error": error_count,
                "skipped": skip_count,
            }
            task["progress"] = {
                "overall": 100,
                "current_video": "全部完成",
                "current_video_index": total,
                "total_videos": total,
                "video_status": "done",
                "message": f"处理完成: {done_count} 成功, {error_count} 失败, {skip_count} 跳过",
                "gif_progress": 100,
            }
            self.append_log_entry(task, "ok", f"任务完成: {done_count} 成功, {error_count} 失败, {skip_count} 跳过")

    def mark_task_failed(self, task_id: str, exc: Exception):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "error"
            task["error"] = str(exc)
            task["finished_at"] = now_iso()
            self.append_log_entry(task, "err", f"任务异常退出: {exc}")

    def update_task_progress(
        self,
        task_id: str,
        video_index: int,
        total_videos: int,
        video_name: str,
        status: str,
        message: str = "",
        gif_progress: float = 0,
        step_index: int = 0,
        steps_per_video: int = 1,
    ):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return

            total_steps = total_videos * steps_per_video
            current_step = video_index * steps_per_video + (step_index + 1)

            if status in ["done", "error", "skipped"] and step_index == 0:
                current_step = (video_index + 1) * steps_per_video

            overall = (current_step / total_steps) * 100 if total_steps > 0 else 0
            eta_str = "--:--"

            if current_step > 0 and "start_time" in task:
                elapsed = time.time() - task["start_time"]
                remaining_steps = total_steps - current_step
                time_per_step = elapsed / current_step
                remaining_time = time_per_step * remaining_steps

                minutes, seconds = divmod(int(remaining_time), 60)
                if minutes > 60:
                    hours, minutes = divmod(minutes, 60)
                    eta_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                else:
                    eta_str = f"{minutes:02d}:{seconds:02d}"

            task["progress"] = {
                "overall": round(overall, 1),
                "current_video": video_name,
                "current_video_index": video_index + 1,
                "total_videos": total_videos,
                "video_status": status,
                "message": message,
                "gif_progress": round(gif_progress, 1),
                "eta": eta_str,
                "elapsed": round(time.time() - task.get("start_time", time.time()), 1),
            }

    def is_task_cancelled(self, task_id: str) -> bool:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return True
            if task.get("cancelled", False):
                return True

            last_hb = self.heartbeat_ts.get(task_id, time.time())
            if time.time() - last_hb > 30:
                task["cancelled"] = True
                task["status"] = "timeout"
                return True
        return False

    def cancel_task_request(self, task_id: str) -> tuple[bool, bool]:
        should_archive = False
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False, False

            task["cancelled"] = True
            if task["status"] == "queued":
                task["status"] = "cancelled"
                task["finished_at"] = now_iso()
                task["progress"] = {"overall": 0, "message": "任务已取消（队列中）"}
                self.append_log_entry(task, "warn", "任务在队列中被取消")
                should_archive = True
            else:
                self.append_log_entry(task, "warn", "已请求取消当前任务")
        return True, should_archive

    def heartbeat(self, task_id: str = "") -> str:
        with self.task_lock:
            if task_id in self.tasks:
                self.heartbeat_ts[task_id] = time.time()
                return self.tasks[task_id]["status"]
        return "alive"
