"""
API 层: draco/authority — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/authority ----


def draco_get_authority(c):
    """detail: /estack/api/estack/draco/v1/authority/national
    """
    url = "/estack/api/estack/draco/v1/authority/national"
    return ApiResponse(c.request(
        "GET", url))
