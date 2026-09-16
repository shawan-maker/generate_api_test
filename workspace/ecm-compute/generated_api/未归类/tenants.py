"""
API 层: draco/tenants — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/tenants ----


def draco_list_tenants(c, **extra):
    """list: /estack/api/estack/draco/inner/v1/tenants/list
    """
    url = "/estack/api/estack/draco/inner/v1/tenants/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
