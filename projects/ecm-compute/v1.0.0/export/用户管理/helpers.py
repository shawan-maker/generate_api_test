"""
helpers.py — 由 module_discovery Stage 5 自动导出

高层辅助函数，供外部测试平台调用。
内部实现（extract_by_path、build_auth_header 等）不导出。
"""

import json
import time
import random
from pathlib import Path


def get_token_by_cookie(cookie_path: str, token_key: str = "accessToken") -> str:
    """
    从 Cookie 文件提取 Token。

    Args:
        cookie_path: Cookie JSON 文件路径
        token_key: Cookie 中的 Token 字段名

    Returns:
        Token 字符串

    Raises:
        FileNotFoundError: cookie_path 不存在
        ValueError: 未找到指定 token_key
    """
    path = Path(cookie_path)
    if not path.exists():
        raise FileNotFoundError(f"Cookie 文件不存在: {cookie_path}")

    with open(path, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    for cookie in cookies:
        if cookie.get("name") == token_key:
            value = cookie.get("value", "")
            if value:
                return value

    raise ValueError(f"Cookie 中未找到 token_key={token_key}")


def gen_timestamp() -> str:
    """
    生成当前时间戳（毫秒级）。

    Returns:
        时间戳字符串，例如 "1725780000123"
    """
    return str(int(time.time() * 1000))


def gen_unique_name(prefix: str, ts: str, original: str) -> str:
    """
    生成唯一名称（用于 create 步骤的 name 字段）。

    Args:
        prefix: 前缀，例如 "AT"
        ts: 时间戳字符串
        original: 原始名称

    Returns:
        格式化名称，例如 "AT_1725780000123_test_user"
    """
    return f"{prefix}_{ts}_{original}"


def gen_mutable_value(ts: str) -> str:
    """
    生成可变字段值（用于 update 步骤的 description 等字段）。

    Args:
        ts: 时间戳字符串

    Returns:
        格式化值，例如 "自动修改_1725780000123"
    """
    return f"自动修改_{ts}"
