"""
API 层: draco/products — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/products ----


def draco_list_products(c):
    """list: /estack/api/estack/draco/v1/products
    """
    url = "/estack/api/estack/draco/v1/products"
    return ApiResponse(c.request(
        "GET", url))
