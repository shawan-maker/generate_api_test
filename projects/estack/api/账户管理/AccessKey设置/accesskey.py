"""
API 层: draco/accesskey — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/accesskey ----


def draco_list_accesskey(c, **extra):
    """list: /estack/api/estack/draco/v1/accesskey/list
    body_keys: ["pageNum", "pageSize"]
    """
    url = "/estack/api/estack/draco/v1/accesskey/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def draco_create_accesskey(c, **extra):
    """create: /estack/api/estack/draco/v1/accesskey/create
    """
    url = "/estack/api/estack/draco/v1/accesskey/create"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
