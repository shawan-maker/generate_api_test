"""
API 层: draco/basic-config — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/basic-config ----


def draco_get_basic_config(c):
    """detail: /estack/api/estack/draco/v1/basic-config/ai
    """
    url = "/estack/api/estack/draco/v1/basic-config/ai"
    return ApiResponse(c.request(
        "GET", url))
