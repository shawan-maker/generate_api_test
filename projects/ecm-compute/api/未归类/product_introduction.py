"""
API 层: pegasi/product-introduction — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- pegasi/product-introduction ----


def pegasi_list_product_introduction(c):
    """list: /estack/api/estack/pegasi/v1/product-introduction/portal
    """
    url = "/estack/api/estack/pegasi/v1/product-introduction/portal"
    return ApiResponse(c.request(
        "GET", url))
