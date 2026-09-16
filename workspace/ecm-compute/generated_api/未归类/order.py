"""
API 层: default/order — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/order ----


def default_list_order(c, **extra):
    """list: /estack/api/estack/orion/v1/order/queryInstancePriceInfoExt
    body_keys: ["instanceIds", "userId"]
    """
    url = "/estack/api/estack/orion/v1/order/queryInstancePriceInfoExt"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
