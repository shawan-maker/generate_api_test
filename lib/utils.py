"""
utils.py — 通用工具函数

包含跨模块复用的工具函数，如安全写入、Session ID 生成等。
"""
import time
import random
import json
from pathlib import Path
from typing import Any


def safe_write(path: Path, data: str, encoding: str = "utf-8"):
    """
    安全写入：临时文件 + rename 兜底，Windows EPERM 防护。

    对齐 EcsCloud safeWriteFileSync：
    - 先写临时文件 .tmp
    - 尝试 rename（原子操作）
    - rename 失败则直接写目标文件
    - 最多重试 8 次，每次间隔 0.3s
    - 最后兜底直接写（尽力而为）

    Args:
        path: 目标文件路径
        data: 写入内容（字符串）
        encoding: 编码（默认 utf-8）
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    for attempt in range(8):
        try:
            tmp.write_text(data, encoding=encoding)
            try:
                tmp.rename(path)
                return
            except OSError:
                # rename 失败（可能被占用），直接写目标文件
                path.write_text(data, encoding=encoding)
                return
        except OSError:
            # 写临时文件失败，等待后重试
            time.sleep(0.3)

    # 最后兜底：直接写目标文件（尽力而为）
    try:
        path.write_text(data, encoding=encoding)
    except Exception:
        pass


def safe_write_json(path: Path, data: Any, ensure_ascii: bool = False, indent: int = 2):
    """
    安全写入 JSON 文件。

    Args:
        path: 目标文件路径
        data: 写入内容（任意类型，将被序列化为 JSON）
        ensure_ascii: 是否转义非 ASCII 字符（默认 False）
        indent: 缩进空格数（默认 2）
    """
    json_str = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    safe_write(path, json_str)


def generate_session_id() -> str:
    """
    生成唯一 session ID: YYYYMMDDThhmmss_XXXX（4位随机后缀）。

    Returns:
        格式如 "20260825T143022_a7f3"
    """
    now = time.localtime()
    ts = time.strftime("%Y%m%dT%H%M%S", now)
    rand = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=4))
    return f"{ts}_{rand}"


def safe_read_json(path: Path, default: Any = None) -> Any:
    """
    安全读取 JSON 文件，失败时返回默认值。

    Args:
        path: 文件路径
        default: 读取失败时的默认值（默认 None）

    Returns:
        解析后的 JSON 数据，或 default
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
