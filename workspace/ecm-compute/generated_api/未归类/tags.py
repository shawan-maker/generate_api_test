"""
API 层: default/tags — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/tags ----


def default_create_tags(c, **extra):
    """create: /estack/api/estack/libra/inner/v1/tags/resources/tags
    body_keys: ["resources"]
    """
    url = "/estack/api/estack/libra/inner/v1/tags/resources/tags"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
