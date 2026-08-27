"""
API 层: cancer/pools — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- cancer/pools ----


def cancer_list_pools(c):
    """list: /estack/api/estack/cancer/v1/pools/list/region/public
    """
    url = "/estack/api/estack/cancer/v1/pools/list/region/public"
    return ApiResponse(c.request(
        "GET", url))


def cancer_list_pools(c, **extra):
    """list: /estack/api/estack/cancer/v1/pools/list/public
    body_keys: ["productList"]
    """
    url = "/estack/api/estack/cancer/v1/pools/list/public"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def cancer_create_pools(c, **extra):
    """create: /estack/api/estack/cancer/inner/v1/pools/list/with-region/public
    body_keys: ["ignoreInvalid"]
    """
    url = "/estack/api/estack/cancer/inner/v1/pools/list/with-region/public"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def cancer_list_pools(c, **extra):
    """list: /estack/api/estack/cancer/inner/v1/pools/list/public
    """
    url = "/estack/api/estack/cancer/inner/v1/pools/list/public"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def cancer_list_pools(c):
    """list: /estack/api/estack/cancer/inner/v1/pools/list/region/public
    """
    url = "/estack/api/estack/cancer/inner/v1/pools/list/region/public"
    return ApiResponse(c.request(
        "GET", url))
