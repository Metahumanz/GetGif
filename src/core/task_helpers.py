from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def format_log_lines(logs: list[dict]) -> str:
    return "\n".join(f"[{item['time']}] [{item['level'].upper()}] {item['message']}" for item in logs)
