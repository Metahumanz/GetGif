"""H.264 / H.265 编码器目录与本机可用性探测。

职责：
- 列出 ffmpeg 中存在的编码器（软件 + 硬件）。
- 对硬件编码器做一次微型试编码，确认本机显卡/驱动确实可用。
- 根据用户选择（auto / 具体编码器名）解析出最终编码器及其参数。

说明：
- 检测结果在进程生命周期内缓存一次（首次 /api/encoders 请求会稍慢）。
- 软件编码器（libx264 / libx265）只要 ffmpeg 带即视为可用。
- 硬件编码器必须在试编码通过后才会标记为可用。
"""

import functools
import subprocess

import imageio_ffmpeg

# 整个应用共享的 ffmpeg 可执行文件路径（由 imageio-ffmpeg 提供）
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

# 编码器定义：
#   name          ffmpeg 编码器名
#   codec         编码标准（h264 / h265）
#   kind          software / hardware
#   vendor        硬件厂商（软件为 None）
#   label         界面显示名
#   args          质量相关参数（不同编码器的质量参数体系不同）
#   max_parallel  硬件编码时建议的最大并行进程数（0 表示不额外限制）
ENCODER_DEFS = [
    {
        "name": "libx264",
        "codec": "h264",
        "kind": "software",
        "vendor": None,
        "label": "libx264（软件 · CPU）",
        "args": ["-preset", "medium", "-crf", "18"],
        "max_parallel": 0,
    },
    {
        "name": "libx265",
        "codec": "h265",
        "kind": "software",
        "vendor": None,
        "label": "libx265（软件 · CPU）",
        "args": ["-preset", "medium", "-crf", "23", "-tag:v", "hvc1"],
        "max_parallel": 0,
    },
    {
        "name": "h264_nvenc",
        "codec": "h264",
        "kind": "hardware",
        "vendor": "NVIDIA",
        "label": "NVIDIA NVENC H.264（硬件）",
        "args": ["-rc", "vbr", "-cq", "21", "-b:v", "0"],
        "max_parallel": 4,
    },
    {
        "name": "hevc_nvenc",
        "codec": "h265",
        "kind": "hardware",
        "vendor": "NVIDIA",
        "label": "NVIDIA NVENC H.265（硬件）",
        "args": ["-rc", "vbr", "-cq", "26", "-b:v", "0", "-tag:v", "hvc1"],
        "max_parallel": 4,
    },
    {
        "name": "h264_qsv",
        "codec": "h264",
        "kind": "hardware",
        "vendor": "Intel",
        "label": "Intel QSV H.264（硬件）",
        "args": ["-global_quality", "21", "-look_ahead", "1"],
        "max_parallel": 4,
    },
    {
        "name": "hevc_qsv",
        "codec": "h265",
        "kind": "hardware",
        "vendor": "Intel",
        "label": "Intel QSV H.265（硬件）",
        "args": ["-global_quality", "24", "-look_ahead", "1", "-tag:v", "hvc1"],
        "max_parallel": 4,
    },
    {
        "name": "h264_amf",
        "codec": "h264",
        "kind": "hardware",
        "vendor": "AMD",
        "label": "AMD AMF H.264（硬件）",
        "args": ["-rc", "cqp", "-qp_i", "20", "-qp_p", "22"],
        "max_parallel": 4,
    },
    {
        "name": "hevc_amf",
        "codec": "h265",
        "kind": "hardware",
        "vendor": "AMD",
        "label": "AMD AMF H.265（硬件）",
        "args": ["-rc", "cqp", "-qp_i", "22", "-qp_p", "24", "-tag:v", "hvc1"],
        "max_parallel": 4,
    },
]

# 每个编码标准对应的界面标签
CODEC_LABELS = [
    {"id": "h264", "label": "H.264 / AVC"},
    {"id": "h265", "label": "H.265 / HEVC"},
]


def normalize_video_codec(value: str) -> str:
    """把用户输入归一化为 h264 / h265。"""
    lowered = str(value or "h264").strip().lower()
    if lowered in {"h265", "hevc", "x265", "265"}:
        return "h265"
    return "h264"


def normalize_video_encoder(value: str) -> str:
    """把用户输入归一化为 auto / 具体编码器名。"""
    lowered = str(value or "auto").strip().lower()
    return lowered or "auto"


def list_ffmpeg_encoders() -> set[str]:
    """读取 `ffmpeg -encoders` 输出，返回 ffmpeg 支持的编码器名集合。"""
    try:
        completed = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30,
        )
    except Exception as exc:
        print(f"读取 ffmpeg 编码器列表失败: {exc}")
        return set()

    names = set()
    for line in completed.stdout.splitlines():
        parts = line.split()
        # 编码器行形如：" V....D libx264  libx264 H.264 / ..."，
        # 首字符为类型标记（V=视频, A=音频, S=字幕），第二个 token 是编码器名
        if len(parts) >= 2 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def probe_hardware_encoder(encoder_name: str) -> bool:
    """对硬件编码器做一次微型试编码，验证本机显卡/驱动真实可用。"""
    cmd = [
        FFMPEG_PATH,
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=256x144:rate=30",
        "-frames:v",
        "3",
        "-vf",
        "format=yuv420p",
        "-an",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=30,
        )
        return completed.returncode == 0
    except Exception as exc:
        print(f"硬件编码器探测失败 {encoder_name}: {exc}")
        return False


@functools.lru_cache(maxsize=1)
def get_encoder_catalog() -> dict:
    """构建编码器目录（进程内缓存一次）。

    返回结构：
    {
        "codecs":   [{"id": "h264", "label": "H.264 / AVC"}, ...],
        "encoders": [{"name", "codec", "kind", "vendor", "label",
                      "available", "max_parallel"}, ...],
    }
    """
    supported = list_ffmpeg_encoders()
    encoders = []
    for definition in ENCODER_DEFS:
        entry = {
            "name": definition["name"],
            "codec": definition["codec"],
            "kind": definition["kind"],
            "vendor": definition["vendor"],
            "label": definition["label"],
            "args": list(definition["args"]),
            "max_parallel": definition["max_parallel"],
            "available": False,
        }
        if definition["name"] not in supported:
            entry["available"] = False
            entry["label"] = f"{definition['label']}（ffmpeg 不支持）"
        elif definition["kind"] == "software":
            entry["available"] = True
        else:
            entry["available"] = probe_hardware_encoder(definition["name"])
            if not entry["available"]:
                entry["label"] = f"{definition['label']}（本机不可用）"
        encoders.append(entry)

    return {"codecs": CODEC_LABELS, "encoders": encoders}


def resolve_encoder(video_codec: str, requested_encoder: str, catalog: dict) -> dict:
    """根据编码标准和用户选择，解析出最终使用的编码器定义。

    规则：
    - auto：优先本机可用的硬件编码器（NVIDIA > Intel > AMD），否则用软件编码器。
    - 显式编码器名：必须可用且与所选编码标准匹配，否则抛出 ValueError。
    """
    codec = normalize_video_codec(video_codec)
    requested = normalize_video_encoder(requested_encoder)

    candidates = [
        entry for entry in catalog["encoders"] if entry["codec"] == codec and entry["available"]
    ]
    if not candidates:
        raise ValueError(f"本机没有可用的 {codec.upper()} 编码器，请检查 ffmpeg 或显卡驱动")

    if requested == "auto":
        hardware = [entry for entry in candidates if entry["kind"] == "hardware"]
        return (hardware or candidates)[0]

    match = next((entry for entry in candidates if entry["name"] == requested), None)
    if match is None:
        available_names = "、".join(entry["name"] for entry in candidates)
        raise ValueError(f"编码器 {requested} 不可用；{codec.upper()} 可用的编码器: {available_names}")
    return match
