"""
helpers.py — 由 module_discovery Stage 5 自动导出

测试数据生成函数集，供 API 测试脚本、Postman Pre-request Script 共享。
所有生成函数以当前时间戳为种子，保证每次运行数据唯一。
"""

import json
import time
import random
import uuid as _uuid_mod
from pathlib import Path


# ────────────────── 认证 ──────────────────

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


# ────────────────── 基础工具 ──────────────────

def gen_timestamp() -> str:
    """生成当前时间戳（毫秒级），例如 '1725780000123'。"""
    return str(int(time.time() * 1000))


def _ts6() -> str:
    """生成 6 位十六进制时间戳后缀（用于构造唯一值）。"""
    return format(int(time.time() * 1000), "x")[-6:]


# ────────────────── 数据生成函数 ──────────────────

def gen_test_name(field_name: str = "") -> str:
    """
    生成唯一测试名称（用于 name 角色字段，如 userName、name）。

    Args:
        field_name: 字段名（仅用于可读性，不影响生成值）

    Returns:
        例如 'AT_a3f2c1'
    """
    return f"AT_{_ts6()}"


def gen_mutable_value() -> str:
    """
    生成可变字段值（用于 mutable 角色字段，如 description）。

    Returns:
        例如 'auto_modified_a3f2c1'
    """
    return f"auto_modified_{_ts6()}"


def gen_email() -> str:
    """
    生成测试邮箱地址。

    Returns:
        例如 'at_a3f2c1@test.com'
    """
    return f"at_{_ts6()}@test.com"


def gen_phone() -> str:
    """
    生成测试手机号（11 位中国大陆号码）。

    Returns:
        例如 '1380a3f2c1' → 注意：ts6 含字母，此处用随机数字替代
    """
    return f"138{random.randint(10000000, 99999999)}"


def gen_uuid() -> str:
    """
    生成 UUID v4 字符串。

    Returns:
        例如 '550e8400-e29b-41d4-a716-446655440000'
    """
    return str(_uuid_mod.uuid4())


def gen_hex_id() -> str:
    """
    生成 32 位十六进制 ID（无连字符的 UUID）。

    Returns:
        例如 '550e8400e29b41d4a716446655440000'
    """
    return _uuid_mod.uuid4().hex


def gen_random_int() -> str:
    """
    生成 8 位随机整数。

    Returns:
        例如 '47291830'
    """
    return str(random.randint(10000000, 99999999))


def gen_text() -> str:
    """
    生成通用文本值。

    Returns:
        例如 'auto_a3f2c1'
    """
    return f"auto_{_ts6()}"
