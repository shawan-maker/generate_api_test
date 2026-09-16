"""
API 层: default/resource-quota-items — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/resource-quota-items ----


def default_list_resource_quota_items(c):
    """list: /estack/api/estack/virgo/v1/resource-quota-items/ECS
    """
    url = "/estack/api/estack/virgo/v1/resource-quota-items/ECS"
    return ApiResponse(c.request(
        "GET", url))
