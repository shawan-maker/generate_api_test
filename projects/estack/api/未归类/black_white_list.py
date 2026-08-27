"""
API 层: draco/black-white-list — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/black-white-list ----


def draco_get_black_white_list(c):
    """detail: /estack/api/estack/draco/v1/black-white-list/system-config
    """
    url = "/estack/api/estack/draco/v1/black-white-list/system-config"
    return ApiResponse(c.request(
        "GET", url))


def draco_create_black_white_list(c, **extra):
    """create: /estack/api/estack/draco/v1/black-white-list
    body_keys: ["enableList", "pageNum", "pageSize", "type"]
    """
    url = "/estack/api/estack/draco/v1/black-white-list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
