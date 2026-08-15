from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "settings.json"
HISTORY_FILE = BASE_DIR / "task_history.json"

HOST = "127.0.0.1"
PORT = 6543
AUTO_EXIT_DELAY = 300
SCAN_CACHE_TTL = 180
MAX_HISTORY_ITEMS = 100

# 心跳保活策略：
# - 浏览器每 3 秒发一次心跳；超过 HEARTBEAT_TIMEOUT 秒没收到心跳时，任务“暂停”等待，
#   心跳恢复后自动继续（应对标签页休眠/电脑短暂睡眠等场景，而不是直接取消任务）。
# - 超过 HEARTBEAT_GIVE_UP 秒仍无心跳才真正停止任务（状态为 timeout/已断开）。
HEARTBEAT_TIMEOUT = 90
HEARTBEAT_GIVE_UP = 1800

VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".webm", ".m4v", ".mpg", ".mpeg", ".3gp", ".ts",
}

DEFAULT_CONFIG = {
    "source_dir": "",
    "output_dir": "",
    "skip_head": 30,
    "skip_tail": 15,
    "num_gifs": 16,
    "export_mode": "gif",
    "image_format": "png",
    "video_codec": "h264",
    "video_encoder": "auto",
    "output_name_template": "{video_name}_{index2}",
    "gif_duration": 5,
    "gif_fps": 10,
    "gif_width": 480,
    "gif_height": 0,
    "scale_mode": "auto",
    "use_gpu": False,
    "use_parallel": True,
    "keep_running": False,
}
