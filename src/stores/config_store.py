import json

from ..core.config import CONFIG_FILE, DEFAULT_CONFIG


class ConfigStore:
    def load(self) -> dict:
        if CONFIG_FILE.exists():
            try:
                with CONFIG_FILE.open("r", encoding="utf-8") as handle:
                    return {**DEFAULT_CONFIG, **json.load(handle)}
            except Exception:
                pass
        return DEFAULT_CONFIG.copy()

    def save(self, config: dict):
        try:
            with CONFIG_FILE.open("w", encoding="utf-8") as handle:
                json.dump(config, handle, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"保存配置失败: {exc}")
