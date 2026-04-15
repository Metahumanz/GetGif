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

from .config import AUTO_EXIT_DELAY, CONFIG_FILE, DEFAULT_CONFIG, SCAN_CACHE_TTL, VIDEO_EXTENSIONS


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


class GetGifService:
    def __init__(self):
        self.tasks = {}
        self.heartbeat_ts = {}
        self.scan_cache = {}
        self.task_lock = threading.Lock()
        self.scan_cache_lock = threading.Lock()
        self.activity_lock = threading.Lock()
        self.last_activity_time = time.time()

        self._configure_logging()
        self._start_monitor()

    def _configure_logging(self):
        log = logging.getLogger("werkzeug")
        if not any(isinstance(item, HeartbeatFilter) for item in log.filters):
            log.addFilter(HeartbeatFilter())

    def _start_monitor(self):
        threading.Thread(target=self._monitor_activity, daemon=True).start()

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

    def extract_gifs(
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
        p_num_gifs = params.get("num_gifs", 16)
        p_duration = params.get("gif_duration", 5)
        p_fps = params.get("gif_fps", 10)
        p_width = params.get("gif_width", 480)
        p_height = params.get("gif_height", 0)
        p_scale_mode = params.get("scale_mode", "auto")
        p_use_gpu = params.get("use_gpu", False)
        p_use_parallel = params.get("use_parallel", True)

        result = {
            "video": video_path,
            "name": video_name,
            "status": "processing",
            "gifs": [],
            "error": None,
        }

        sub_dir = Path(output_dir) / video_name
        sub_dir.mkdir(parents=True, exist_ok=True)
        result["output_dir"] = str(sub_dir)

        try:
            v_info = get_video_info(video_path)
            duration = v_info["duration"]
            if duration <= 0:
                result["status"] = "error"
                result["error"] = "无法读取视频时长或视频损坏"
                self.update_task_progress(task_id, video_index, total_videos, video_name, "error", result["error"])
                return result

            start_time = p_skip_head
            end_time = duration - p_skip_tail

            if end_time <= start_time:
                start_time = 0
                end_time = duration
                if end_time <= p_duration:
                    result["status"] = "skipped"
                    result["error"] = f"视频时长太短 ({duration:.1f}秒)"
                    self.update_task_progress(task_id, video_index, total_videos, video_name, "skipped", result["error"])
                    return result

            effective_duration = end_time - start_time
            segment_length = effective_duration / p_num_gifs
            completed_segments = 0
            seg_lock = threading.Lock()

            def process_segment(segment_index: int):
                nonlocal completed_segments
                if self.is_task_cancelled(task_id):
                    return None

                segment_start = start_time + segment_index * segment_length
                gif_start = segment_start + (segment_length - p_duration) / 2
                gif_start = max(start_time, gif_start)

                gif_filename = f"{video_name}_{segment_index + 1:02d}.gif"
                gif_path = sub_dir / gif_filename

                scale_filter = ""
                if p_scale_mode == "auto":
                    if p_width > 0 and p_height > 0:
                        scale_filter = f",scale={p_width}:-1"
                    elif p_width > 0:
                        scale_filter = f",scale={p_width}:-1"
                    elif p_height > 0:
                        scale_filter = f",scale=-1:{p_height}"
                else:
                    if p_width > 0 and p_height > 0:
                        scale_filter = f",scale={p_width}:{p_height}"
                    elif p_width > 0:
                        scale_filter = f",scale={p_width}:-1"
                    elif p_height > 0:
                        scale_filter = f",scale=-1:{p_height}"

                filter_complex = (
                    f"fps={p_fps}{scale_filter},split[a][b];"
                    "[a]palettegen=stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=3"
                )

                cmd = [FFMPEG_PATH]
                if p_use_gpu:
                    cmd.extend(["-hwaccel", "auto"])

                cmd.extend(["-threads", "1", "-filter_threads", "1"])
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
                        filter_complex,
                        str(gif_path),
                    ]
                )

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

                gif_progress = current_count / p_num_gifs * 100
                self.update_task_progress(
                    task_id,
                    video_index,
                    total_videos,
                    video_name,
                    "processing",
                    f"正在截取 GIF {current_count}/{p_num_gifs}",
                    gif_progress,
                    step_index=current_count - 1,
                    steps_per_video=p_num_gifs,
                )

                return {
                    "filename": gif_filename,
                    "path": str(gif_path),
                    "time_start": round(gif_start, 2),
                }

            if p_use_parallel:
                max_workers = get_subprocess_worker_count(p_num_gifs)
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_idx = {
                        executor.submit(process_segment, segment_index): segment_index
                        for segment_index in range(p_num_gifs)
                    }
                    for future in concurrent.futures.as_completed(future_to_idx):
                        item = future.result()
                        if item:
                            result["gifs"].append(item)
                        if self.is_task_cancelled(task_id):
                            executor.shutdown(wait=False, cancel_futures=True)
                            break
            else:
                for segment_index in range(p_num_gifs):
                    item = process_segment(segment_index)
                    if item:
                        result["gifs"].append(item)
                    if self.is_task_cancelled(task_id):
                        break

            if self.is_task_cancelled(task_id):
                result["status"] = "cancelled"
                result["error"] = "任务已取消"
                return result

            result["status"] = "done"
            result["gifs"].sort(key=lambda item: item["filename"])
            self.update_task_progress(
                task_id,
                video_index,
                total_videos,
                video_name,
                "done",
                f"完成，共生成 {len(result['gifs'])} 张GIF",
            )
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            self.update_task_progress(task_id, video_index, total_videos, video_name, "error", str(exc))

        return result

    def run_task(self, task_id: str, source_dir: str, output_dir: str, params: dict, cached_videos: list[dict] | None = None):
        with self.task_lock:
            self.tasks[task_id]["status"] = "scanning" if cached_videos is None else "processing"
            self.tasks[task_id]["start_time"] = time.time()
            self.heartbeat_ts[task_id] = time.time()

        try:
            videos = cached_videos if cached_videos is not None else discover_videos(source_dir)
            total = len(videos)

            with self.task_lock:
                self.tasks[task_id]["total_videos"] = total
                if total == 0:
                    self.tasks[task_id]["status"] = "done"
                    self.tasks[task_id]["progress"] = {"overall": 100, "message": "未找到视频"}
                    return
                self.tasks[task_id]["status"] = "processing"

            done_count = 0
            error_count = 0
            skip_count = 0

            for index, video in enumerate(videos):
                if self.is_task_cancelled(task_id):
                    with self.task_lock:
                        self.tasks[task_id]["status"] = "cancelled"
                    return

                result = self.extract_gifs(video, output_dir, task_id, index, total, params)
                if result["status"] == "done":
                    done_count += 1
                elif result["status"] == "error":
                    error_count += 1
                elif result["status"] == "skipped":
                    skip_count += 1

            with self.task_lock:
                self.tasks[task_id]["status"] = "done"
                self.tasks[task_id]["summary"] = {
                    "total": total,
                    "done": done_count,
                    "error": error_count,
                    "skipped": skip_count,
                }
                self.tasks[task_id]["progress"] = {
                    "overall": 100,
                    "current_video": "全部完成",
                    "current_video_index": total,
                    "total_videos": total,
                    "video_status": "done",
                    "message": f"处理完成: {done_count} 成功, {error_count} 失败, {skip_count} 跳过",
                    "gif_progress": 100,
                }
        except Exception as exc:
            with self.task_lock:
                self.tasks[task_id]["status"] = "error"
                self.tasks[task_id]["error"] = str(exc)
            traceback.print_exc()

    def create_task(self, source_dir: str, output_dir: str, params: dict, scan_id: str = "") -> dict:
        os.makedirs(output_dir, exist_ok=True)

        task_id = str(uuid.uuid4())[:8]
        cached_videos = self.get_cached_scan(scan_id, source_dir)

        with self.task_lock:
            self.tasks[task_id] = {
                "id": task_id,
                "source_dir": source_dir,
                "output_dir": output_dir,
                "status": "queued",
                "created_at": datetime.now().isoformat(),
                "total_videos": 0,
                "progress": None,
                "cancelled": False,
            }
            self.heartbeat_ts[task_id] = time.time()

        threading.Thread(
            target=self.run_task,
            args=(task_id, source_dir, output_dir, params, cached_videos),
            daemon=True,
        ).start()

        return {"task_id": task_id, "status": "started"}

    def get_task_status(self, task_id: str) -> dict | None:
        with self.task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None

            return {
                "id": task["id"],
                "status": task["status"],
                "source_dir": task["source_dir"],
                "output_dir": task["output_dir"],
                "total_videos": task["total_videos"],
                "progress": task["progress"],
                "error": task.get("error"),
                "summary": task.get("summary"),
            }

    def cancel_task(self, task_id: str) -> bool:
        with self.task_lock:
            if task_id not in self.tasks:
                return False
            self.tasks[task_id]["cancelled"] = True
            return True

    def heartbeat(self, task_id: str = "") -> str:
        self.update_activity()
        with self.task_lock:
            if task_id in self.tasks:
                self.heartbeat_ts[task_id] = time.time()
                return self.tasks[task_id]["status"]
        return "alive"

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
