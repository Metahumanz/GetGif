import concurrent.futures
import functools
import json
import logging
import os
import re
import subprocess
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import imageio_ffmpeg

from .config import (
    AUTO_EXIT_DELAY,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    HISTORY_FILE,
    MAX_HISTORY_ITEMS,
    SCAN_CACHE_TTL,
    VIDEO_EXTENSIONS,
)


FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()


class HeartbeatFilter(logging.Filter):
    def filter(self, record):
        return "/api/heartbeat" not in record.getMessage()


def get_folder_creation_time(folder_path: str) -> float:
    try:
        return os.stat(folder_path).st_ctime
    except OSError:
        return 0.0


def get_subprocess_worker_count(job_count: int, hard_cap: int = 8) -> int:
    if job_count <= 1:
        return 1

    cpu_total = os.cpu_count() or 4
    baseline = max(2, cpu_total // 2)
    return min(job_count, baseline, hard_cap)


def summarize_ffmpeg_error(stderr_text: str) -> str:
    lines = [line.strip() for line in stderr_text.splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if "error" in lowered or "invalid" in lowered or "failed" in lowered:
            return line
    if lines:
        return lines[-1]
    return "FFmpeg 执行失败"


def discover_videos(source_dir: str) -> list[dict]:
    videos = []
    source_path = Path(source_dir).resolve()

    for root, _dirs, files in os.walk(source_path):
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in VIDEO_EXTENSIONS:
                full_path = Path(root) / filename
                videos.append(
                    {
                        "path": str(full_path),
                        "name": full_path.stem,
                        "ext": ext,
                        "folder": str(root),
                        "folder_ctime": get_folder_creation_time(root),
                    }
                )

    videos.sort(key=lambda item: item["folder_ctime"])
    return videos


@functools.lru_cache(maxsize=1024)
def get_video_info(video_path: str) -> dict:
    info = {"duration": 0.0, "width": 0, "height": 0}
    try:
        result = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-i", video_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        output = result.stderr

        match_dur = re.search(r"Duration:\s(\d+):(\d+):(\d+\.\d+)", output)
        if match_dur:
            hours, minutes, seconds = match_dur.groups()
            info["duration"] = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

        match_res = re.search(r",\s(\d{2,5})x(\d{2,5})", output)
        if match_res:
            info["width"] = int(match_res.group(1))
            info["height"] = int(match_res.group(2))
    except Exception as exc:
        print(f"获取视频信息失败: {exc}")
    return info


def normalize_export_mode(value: str) -> str:
    return "image" if str(value).lower() == "image" else "gif"


def normalize_image_format(value: str) -> str:
    return "jpg" if str(value).lower() == "jpg" else "png"


def build_scale_filter(width: int, height: int, scale_mode: str) -> str:
    if scale_mode == "auto":
        if width > 0:
            return f"scale={width}:-1"
        if height > 0:
            return f"scale=-1:{height}"
        return ""

    if width > 0 and height > 0:
        return f"scale={width}:{height}"
    if width > 0:
        return f"scale={width}:-1"
    if height > 0:
        return f"scale=-1:{height}"
    return ""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sanitize_filename_component(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", str(name or ""))
    cleaned = cleaned.strip().rstrip(". ")
    return cleaned


def render_output_basename(template: str, video_name: str, index: int, export_mode: str, output_ext: str) -> str:
    value = (template or DEFAULT_CONFIG["output_name_template"]).strip()
    replacements = {
        "{video_name}": video_name,
        "{index}": str(index),
        "{index2}": f"{index:02d}",
        "{index3}": f"{index:03d}",
        "{mode}": export_mode,
        "{format}": output_ext,
    }
    for token, token_value in replacements.items():
        value = value.replace(token, token_value)
    value = sanitize_filename_component(value)
    return value or f"{sanitize_filename_component(video_name) or 'output'}_{index:02d}"


def ensure_unique_name(base_name: str, used_names: set[str]) -> str:
    candidate = base_name
    suffix = 2
    while candidate.lower() in used_names:
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(candidate.lower())
    return candidate


def format_log_lines(logs: list[dict]) -> str:
    return "\n".join(f"[{item['time']}] [{item['level'].upper()}] {item['message']}" for item in logs)


class GetGifService:
    def __init__(self):
        self.tasks = {}
        self.task_history = []
        self.heartbeat_ts = {}
        self.scan_cache = {}
        self.task_lock = threading.Lock()
        self.history_lock = threading.Lock()
        self.scan_cache_lock = threading.Lock()
        self.activity_lock = threading.Lock()
        self.last_activity_time = time.time()
        self.active_task_id = None

        self._load_task_history()
        self._configure_logging()
        self._start_monitor()
        self._start_queue_worker()

    def _configure_logging(self):
        log = logging.getLogger("werkzeug")
        if not any(isinstance(item, HeartbeatFilter) for item in log.filters):
            log.addFilter(HeartbeatFilter())

    def _start_monitor(self):
        threading.Thread(target=self._monitor_activity, daemon=True).start()

    def _start_queue_worker(self):
        threading.Thread(target=self._queue_worker, daemon=True).start()

    def _monitor_activity(self):
        while True:
            time.sleep(2)
            with self.activity_lock:
                inactive_duration = time.time() - self.last_activity_time

            if inactive_duration > AUTO_EXIT_DELAY:
                print(f"检测到 {AUTO_EXIT_DELAY} 秒无活动，正在退出程序...")
                os._exit(0)

    def update_activity(self):
        with self.activity_lock:
            self.last_activity_time = time.time()

    def _load_task_history(self):
        if not HISTORY_FILE.exists():
            return
        try:
            with HISTORY_FILE.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                self.task_history = data[:MAX_HISTORY_ITEMS]
        except Exception as exc:
            print(f"加载历史记录失败: {exc}")

    def _save_task_history(self):
        try:
            with HISTORY_FILE.open("w", encoding="utf-8") as handle:
                json.dump(self.task_history[:MAX_HISTORY_ITEMS], handle, indent=2, ensure_ascii=False)
        except Exception as exc:
            print(f"保存历史记录失败: {exc}")

    def load_config(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                    return {**DEFAULT_CONFIG, **json.load(handle)}
            except Exception:
                pass
        return DEFAULT_CONFIG.copy()

    def save_config(self, config: dict):
        try:
            with CONFIG_FILE.open("w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"保存配置失败: {exc}")

    def prune_scan_cache(self):
        now = time.time()
        with self.scan_cache_lock:
            expired_ids = [
                scan_id
                for scan_id, item in self.scan_cache.items()
                if now - item["created_at"] > SCAN_CACHE_TTL
            ]
            for scan_id in expired_ids:
                self.scan_cache.pop(scan_id, None)

    def store_scan_cache(self, source_dir: str, videos: list[dict]) -> str:
        self.prune_scan_cache()
        scan_id = str(uuid.uuid4())[:8]
        with self.scan_cache_lock:
            self.scan_cache[scan_id] = {
                "source_dir": str(Path(source_dir).resolve()),
                "videos": videos,
                "created_at": time.time(),
            }
        return scan_id

    def get_cached_scan(self, scan_id: str, source_dir: str) -> list[dict] | None:
        if not scan_id:
            return None

        self.prune_scan_cache()
        source_key = str(Path(source_dir).resolve())
        with self.scan_cache_lock:
            item = self.scan_cache.get(scan_id)
            if not item or item["source_dir"] != source_key:
                return None
            return item["videos"]

    def _append_task_log(self, task: dict, level: str, message: str):
        logs = task.setdefault("logs", [])
        logs.append({"time": now_iso(), "level": level, "message": message})
        if len(logs) > 1000:
            del logs[:-1000]

    def _get_queue_position_locked(self, task_id: str) -> int | None:
        queued_ids = [item["id"] for item in self.tasks.values() if item["status"] == "queued" and not item.get("cancelled")]
        if task_id not in queued_ids:
            return None
        return queued_ids.index(task_id) + 1

    def _build_task_snapshot(self, task: dict, include_logs: bool = False) -> dict:
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
            "queue_position": self._get_queue_position_locked(task["id"]),
            "latest_message": task.get("progress", {}).get("message") if task.get("progress") else "",
            "log_count": len(task.get("logs", [])),
        }
        if include_logs:
            snapshot["logs"] = list(task.get("logs", []))
        return snapshot

    def _archive_task(self, task_id: str):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task or task.get("archived", False):
                return
            task["archived"] = True
            entry = self._build_task_snapshot(task, include_logs=True)

        with self.history_lock:
            self.task_history = [item for item in self.task_history if item.get("id") != task_id]
            self.task_history.insert(0, entry)
            self.task_history = self.task_history[:MAX_HISTORY_ITEMS]
            self._save_task_history()

    def _queue_worker(self):
        while True:
            task_to_run = None

            with self.task_lock:
                if self.active_task_id:
                    active = self.tasks.get(self.active_task_id)
                    if active and active["status"] in {"queued", "scanning", "processing"} and not active.get("cancelled"):
                        task_to_run = None
                    else:
                        self.active_task_id = None

                if task_to_run is None and self.active_task_id is None:
                    for task in self.tasks.values():
                        if task["status"] == "queued" and not task.get("cancelled"):
                            self.active_task_id = task["id"]
                            task["started_at"] = now_iso()
                            self._append_task_log(task, "info", "任务进入执行阶段")
                            task_to_run = (
                                task["id"],
                                task["source_dir"],
                                task["output_dir"],
                                dict(task.get("params", {})),
                                task.get("cached_videos"),
                            )
                            break

            if task_to_run is None:
                time.sleep(0.5)
                continue

            try:
                self.run_task(*task_to_run)
            finally:
                with self.task_lock:
                    if self.active_task_id == task_to_run[0]:
                        self.active_task_id = None

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
            if task_id not in self.tasks:
                return

            task = self.tasks[task_id]
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
            if task_id not in self.tasks:
                return True

            task = self.tasks[task_id]
            if task.get("cancelled", False):
                return True

            last_hb = self.heartbeat_ts.get(task_id, time.time())
            if time.time() - last_hb > 30:
                task["cancelled"] = True
                task["status"] = "timeout"
                return True
        return False

    def extract_outputs(
        self,
        video_info: dict,
        output_dir: str,
        task_id: str,
        video_index: int,
        total_videos: int,
        params: dict,
    ) -> dict:
        video_path = video_info["path"]
        video_name = video_info["name"]

        p_skip_head = params.get("skip_head", 30)
        p_skip_tail = params.get("skip_tail", 15)
        p_output_count = params.get("num_gifs", 16)
        p_export_mode = normalize_export_mode(params.get("export_mode", "gif"))
        p_image_format = normalize_image_format(params.get("image_format", "png"))
        p_name_template = params.get("output_name_template", DEFAULT_CONFIG["output_name_template"])
        p_duration = params.get("gif_duration", 5)
        p_fps = params.get("gif_fps", 10)
        p_width = params.get("gif_width", 480)
        p_height = params.get("gif_height", 0)
        p_scale_mode = params.get("scale_mode", "auto")
        p_use_gpu = params.get("use_gpu", False)
        p_use_parallel = params.get("use_parallel", True)
        output_label = "GIF" if p_export_mode == "gif" else p_image_format.upper()

        result = {
            "video": video_path,
            "name": video_name,
            "status": "processing",
            "outputs": [],
            "error": None,
        }

        sub_dir = Path(output_dir) / video_name
        sub_dir.mkdir(parents=True, exist_ok=True)
        result["output_dir"] = str(sub_dir)

        try:
            if p_output_count <= 0:
                raise ValueError("每视频张数必须大于 0")

            v_info = get_video_info(video_path)
            duration = v_info["duration"]
            if duration <= 0:
                result["status"] = "error"
                result["error"] = "无法读取视频时长或视频损坏"
                self.update_task_progress(task_id, video_index, total_videos, video_name, "error", result["error"])
                with self.task_lock:
                    task = self.tasks.get(task_id)
                    if task:
                        self._append_task_log(task, "err", f"{video_name} 失败: {result['error']}")
                return result

            start_time = p_skip_head
            end_time = duration - p_skip_tail

            if end_time <= start_time:
                start_time = 0
                end_time = duration
                if p_export_mode == "gif" and end_time <= p_duration:
                    result["status"] = "skipped"
                    result["error"] = f"视频时长太短 ({duration:.1f}秒)"
                    self.update_task_progress(task_id, video_index, total_videos, video_name, "skipped", result["error"])
                    with self.task_lock:
                        task = self.tasks.get(task_id)
                        if task:
                            self._append_task_log(task, "warn", f"{video_name} 跳过: {result['error']}")
                    return result

            effective_duration = end_time - start_time
            segment_length = effective_duration / p_output_count
            completed_segments = 0
            seg_lock = threading.Lock()
            scale_filter = build_scale_filter(p_width, p_height, p_scale_mode)
            output_ext = "gif" if p_export_mode == "gif" else p_image_format
            used_names = set()
            output_plan = []

            for item_index in range(1, p_output_count + 1):
                base_name = render_output_basename(p_name_template, video_name, item_index, p_export_mode, output_ext)
                unique_name = ensure_unique_name(base_name, used_names)
                output_plan.append(
                    {
                        "filename": f"{unique_name}.{output_ext}",
                        "path": sub_dir / f"{unique_name}.{output_ext}",
                    }
                )

            with self.task_lock:
                task = self.tasks.get(task_id)
                if task:
                    self._append_task_log(task, "info", f"开始处理视频: {video_name}")

            def process_segment(segment_index: int):
                nonlocal completed_segments
                if self.is_task_cancelled(task_id):
                    return None

                segment_start = start_time + segment_index * segment_length
                output_time = segment_start + segment_length / 2
                output_time = max(start_time, output_time)
                output_time = min(output_time, max(start_time, end_time - 0.05))

                planned_output = output_plan[segment_index]
                output_filename = planned_output["filename"]
                output_path = planned_output["path"]

                cmd = [FFMPEG_PATH]
                if p_use_gpu:
                    cmd.extend(["-hwaccel", "auto"])

                cmd.extend(["-threads", "1", "-filter_threads", "1"])

                if p_export_mode == "gif":
                    gif_filter = f"fps={p_fps}"
                    if scale_filter:
                        gif_filter = f"{gif_filter},{scale_filter}"
                    gif_filter = (
                        f"{gif_filter},split[a][b];"
                        "[a]palettegen=stats_mode=diff[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=3"
                    )
                    gif_start = segment_start + (segment_length - p_duration) / 2
                    gif_start = max(start_time, gif_start)
                    cmd.extend(
                        [
                            "-y",
                            "-ss",
                            str(gif_start),
                            "-t",
                            str(p_duration),
                            "-i",
                            video_path,
                            "-an",
                            "-sn",
                            "-v",
                            "error",
                            "-vf",
                            gif_filter,
                            str(output_path),
                        ]
                    )
                else:
                    cmd.extend(
                        [
                            "-y",
                            "-ss",
                            str(output_time),
                            "-i",
                            video_path,
                            "-frames:v",
                            "1",
                            "-an",
                            "-sn",
                            "-v",
                            "error",
                        ]
                    )
                    if scale_filter:
                        cmd.extend(["-vf", scale_filter])
                    if p_image_format == "jpg":
                        cmd.extend(["-q:v", "2"])
                    cmd.append(str(output_path))

                completed = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
                if completed.returncode != 0:
                    raise RuntimeError(summarize_ffmpeg_error(completed.stderr))

                with seg_lock:
                    completed_segments += 1
                    current_count = completed_segments

                gif_progress = current_count / p_output_count * 100
                self.update_task_progress(
                    task_id,
                    video_index,
                    total_videos,
                    video_name,
                    "processing",
                    f"正在导出 {output_label} {current_count}/{p_output_count}",
                    gif_progress,
                    step_index=current_count - 1,
                    steps_per_video=p_output_count,
                )

                return {
                    "filename": output_filename,
                    "path": str(output_path),
                    "time_start": round(output_time, 2),
                }

            if p_use_parallel:
                max_workers = get_subprocess_worker_count(p_output_count)
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_idx = {
                        executor.submit(process_segment, segment_index): segment_index
                        for segment_index in range(p_output_count)
                    }
                    for future in concurrent.futures.as_completed(future_to_idx):
                        item = future.result()
                        if item:
                            result["outputs"].append(item)
                        if self.is_task_cancelled(task_id):
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
            else:
                for segment_index in range(p_output_count):
                    item = process_segment(segment_index)
                    if item:
                        result["outputs"].append(item)
                    if self.is_task_cancelled(task_id):
                        break

            if self.is_task_cancelled(task_id):
                result["status"] = "cancelled"
                result["error"] = "任务已取消"
                with self.task_lock:
                    task = self.tasks.get(task_id)
                    if task:
                        self._append_task_log(task, "warn", f"视频处理被取消: {video_name}")
                return result

            result["status"] = "done"
            result["outputs"].sort(key=lambda item: item["filename"])
            self.update_task_progress(
                task_id,
                video_index,
                total_videos,
                video_name,
                "done",
                f"完成，共生成 {len(result['outputs'])} 张{output_label}",
            )
            with self.task_lock:
                task = self.tasks.get(task_id)
                if task:
                    self._append_task_log(task, "ok", f"{video_name} 完成，共生成 {len(result['outputs'])} 个文件")
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            self.update_task_progress(task_id, video_index, total_videos, video_name, "error", str(exc))
            with self.task_lock:
                task = self.tasks.get(task_id)
                if task:
                    self._append_task_log(task, "err", f"{video_name} 失败: {exc}")

        return result

    def run_task(self, task_id: str, source_dir: str, output_dir: str, params: dict, cached_videos: list[dict] | None = None):
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            task["status"] = "scanning" if cached_videos is None else "processing"
            task["start_time"] = time.time()
            task["started_at"] = task.get("started_at") or now_iso()
            self.heartbeat_ts[task_id] = time.time()
            self._append_task_log(task, "info", f"任务开始，输出目录: {output_dir}")

        try:
            videos = cached_videos if cached_videos is not None else discover_videos(source_dir)
            total = len(videos)

            with self.task_lock:
                task = self.tasks[task_id]
                task["total_videos"] = total
                if total == 0:
                    task["status"] = "done"
                    task["finished_at"] = now_iso()
                    task["summary"] = {"total": 0, "done": 0, "error": 0, "skipped": 0}
                    task["progress"] = {"overall": 100, "message": "未找到视频"}
                    self._append_task_log(task, "warn", "任务结束：未找到可处理视频")
                else:
                    task["status"] = "processing"
                    self._append_task_log(task, "info", f"扫描完成，共 {total} 个视频")
            if total == 0:
                self._archive_task(task_id)
                return

            done_count = 0
            error_count = 0
            skip_count = 0

            for index, video in enumerate(videos):
                if self.is_task_cancelled(task_id):
                    with self.task_lock:
                        task = self.tasks[task_id]
                        task["status"] = "cancelled"
                        task["finished_at"] = now_iso()
                        self._append_task_log(task, "warn", "任务已取消")
                    self._archive_task(task_id)
                    return

                result = self.extract_outputs(video, output_dir, task_id, index, total, params)
                if result["status"] == "done":
                    done_count += 1
                elif result["status"] == "error":
                    error_count += 1
                elif result["status"] == "skipped":
                    skip_count += 1

            with self.task_lock:
                task = self.tasks[task_id]
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
                self._append_task_log(task, "ok", f"任务完成: {done_count} 成功, {error_count} 失败, {skip_count} 跳过")
            self._archive_task(task_id)
        except Exception as exc:
            with self.task_lock:
                task = self.tasks[task_id]
                task["status"] = "error"
                task["error"] = str(exc)
                task["finished_at"] = now_iso()
                self._append_task_log(task, "err", f"任务异常退出: {exc}")
            self._archive_task(task_id)
            traceback.print_exc()

    def create_task(self, source_dir: str, output_dir: str, params: dict, scan_id: str = "") -> dict:
        os.makedirs(output_dir, exist_ok=True)

        task_id = str(uuid.uuid4())[:8]
        cached_videos = self.get_cached_scan(scan_id, source_dir)
        task_params = {**DEFAULT_CONFIG, **params}
        task_params["export_mode"] = normalize_export_mode(task_params.get("export_mode", "gif"))
        task_params["image_format"] = normalize_image_format(task_params.get("image_format", "png"))
        task_params["output_name_template"] = (
            str(task_params.get("output_name_template", DEFAULT_CONFIG["output_name_template"])).strip()
            or DEFAULT_CONFIG["output_name_template"]
        )

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
            self._append_task_log(task, "info", f"任务已创建，待处理目录: {source_dir}")
            self.tasks[task_id] = task
            self.heartbeat_ts[task_id] = time.time()
            queue_position = self._get_queue_position_locked(task_id)
            self._append_task_log(task, "info", f"任务已加入队列，排队位置: {queue_position}")

        return {"task_id": task_id, "status": "queued", "queue_position": queue_position}

    def get_task_status(self, task_id: str) -> dict | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None

            return self._build_task_snapshot(task)

    def cancel_task(self, task_id: str) -> bool:
        should_archive = False
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            task["cancelled"] = True
            if task["status"] == "queued":
                task["status"] = "cancelled"
                task["finished_at"] = now_iso()
                task["progress"] = {"overall": 0, "message": "任务已取消（队列中）"}
                self._append_task_log(task, "warn", "任务在队列中被取消")
                should_archive = True
            else:
                self._append_task_log(task, "warn", "已请求取消当前任务")
        if should_archive:
            self._archive_task(task_id)
            return True
        return True

    def heartbeat(self, task_id: str = "") -> str:
        self.update_activity()
        with self.task_lock:
            if task_id in self.tasks:
                self.heartbeat_ts[task_id] = time.time()
                return self.tasks[task_id]["status"]
        return "alive"

    def list_task_dashboard(self) -> dict:
        with self.task_lock:
            current = None
            queue = []
            for task in self.tasks.values():
                if task["status"] in {"scanning", "processing"}:
                    current = self._build_task_snapshot(task)
                elif task["status"] == "queued" and not task.get("cancelled"):
                    queue.append(self._build_task_snapshot(task))

        with self.history_lock:
            history = [
                {
                    key: value
                    for key, value in item.items()
                    if key != "logs"
                }
                for item in self.task_history
            ]

        return {
            "current": current,
            "queue": queue,
            "history": history,
        }

    def get_task_log_text(self, task_id: str) -> tuple[str, str] | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if task:
                return (format_log_lines(task.get("logs", [])), f"getgif_{task_id}.log.txt")

        with self.history_lock:
            for item in self.task_history:
                if item.get("id") == task_id:
                    return (format_log_lines(item.get("logs", [])), f"getgif_{task_id}.log.txt")
        return None

    def scan_videos(self, source_dir: str) -> dict:
        videos = discover_videos(source_dir)
        if not videos:
            return {"count": 0, "videos": [], "scan_id": self.store_scan_cache(source_dir, [])}

        def process_video_info(video: dict) -> dict:
            vinfo = get_video_info(video["path"])
            return {
                "name": video["name"],
                "ext": video["ext"],
                "folder": video["folder"],
                "res": f"{vinfo['width']}x{vinfo['height']}",
                "width": vinfo["width"],
                "height": vinfo["height"],
                "folder_ctime": datetime.fromtimestamp(video["folder_ctime"]).strftime("%Y-%m-%d %H:%M:%S"),
            }

        max_workers = get_subprocess_worker_count(len(videos))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(process_video_info, videos))

        return {
            "count": len(videos),
            "videos": results,
            "scan_id": self.store_scan_cache(source_dir, videos),
        }

    def browse_directory(self) -> str:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            return filedialog.askdirectory(parent=root)
        finally:
            root.destroy()

    def open_folder(self, path: str) -> bool:
        if not path or not os.path.isdir(path):
            return False
        os.startfile(path)
        return True
