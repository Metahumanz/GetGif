import concurrent.futures
import functools
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg

from ..core.config import DEFAULT_CONFIG, VIDEO_EXTENSIONS


FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()


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


def collect_scan_results(videos: list[dict]) -> list[dict]:
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
        return list(executor.map(process_video_info, videos))


def extract_outputs(
    video_info: dict,
    output_dir: str,
    params: dict,
    is_cancelled,
    on_progress,
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
            on_progress("error", result["error"])
            return result

        start_time = p_skip_head
        end_time = duration - p_skip_tail

        if end_time <= start_time:
            start_time = 0
            end_time = duration
            if p_export_mode == "gif" and end_time <= p_duration:
                result["status"] = "skipped"
                result["error"] = f"视频时长太短 ({duration:.1f}秒)"
                on_progress("skipped", result["error"])
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

        def process_segment(segment_index: int):
            nonlocal completed_segments
            if is_cancelled():
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

            on_progress(
                "processing",
                f"正在导出 {output_label} {current_count}/{p_output_count}",
                current_count / p_output_count * 100,
                current_count - 1,
                p_output_count,
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
                    if is_cancelled():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
        else:
            for segment_index in range(p_output_count):
                item = process_segment(segment_index)
                if item:
                    result["outputs"].append(item)
                if is_cancelled():
                    break

        if is_cancelled():
            result["status"] = "cancelled"
            result["error"] = "任务已取消"
            return result

        result["status"] = "done"
        result["outputs"].sort(key=lambda item: item["filename"])
        on_progress("done", f"完成，共生成 {len(result['outputs'])} 张{output_label}")
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        on_progress("error", str(exc))

    return result
