from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
CONFIG_FILE = BASE_DIR / "settings.json"

HOST = "127.0.0.1"
PORT = 6543
AUTO_EXIT_DELAY = 300
SCAN_CACHE_TTL = 180

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
    "gif_duration": 5,
    "gif_fps": 10,
    "gif_width": 480,
    "gif_height": 0,
    "scale_mode": "auto",
    "use_gpu": False,
    "use_parallel": True,
}
