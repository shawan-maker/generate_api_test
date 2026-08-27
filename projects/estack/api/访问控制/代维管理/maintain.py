"""
API 层: draco/maintain — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/maintain ----


def draco_list_maintain(c, **extra):
    """list: /estack/api/estack/draco/v1/maintain/list
    body_keys: ["pageNum", "pageSize"]
    """
    url = "/estack/api/estack/draco/v1/maintain/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
