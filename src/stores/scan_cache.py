import threading
import time
import uuid
from pathlib import Path

from ..core.config import SCAN_CACHE_TTL


class ScanCache:
    def __init__(self, ttl_seconds: int = SCAN_CACHE_TTL):
        self.ttl_seconds = ttl_seconds
        self._cache = {}
        self._lock = threading.Lock()

    def prune(self):
        now = time.time()
        with self._lock:
            expired_ids = [
                scan_id
                for scan_id, item in self._cache.items()
                if now - item["created_at"] > self.ttl_seconds
            ]
            for scan_id in expired_ids:
                self._cache.pop(scan_id, None)

    def store(self, source_dir: str, videos: list[dict]) -> str:
        self.prune()
        scan_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._cache[scan_id] = {
                "source_dir": str(Path(source_dir).resolve()),
                "videos": videos,
                "created_at": time.time(),
            }
        return scan_id

    def get(self, scan_id: str, source_dir: str) -> list[dict] | None:
        if not scan_id:
            return None

        self.prune()
        source_key = str(Path(source_dir).resolve())
        with self._lock:
            item = self._cache.get(scan_id)
            if not item or item["source_dir"] != source_key:
                return None
            return item["videos"]
