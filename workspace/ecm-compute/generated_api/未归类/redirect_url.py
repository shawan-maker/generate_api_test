"""
API 层: draco/redirect-url — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/redirect-url ----


def draco_list_redirect_url(c, **extra):
    """list: /estack/api/estack/draco/v1/redirect-url/list
    body_keys: ["resourceTypes"]
    """
    url = "/estack/api/estack/draco/v1/redirect-url/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
