"""
API 层: default/port — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/port ----


def default_list_port(c, **extra):
    """list: /estack/api/estack-vpc-server-console/customer/v3/port/list
    body_keys: ["rangeInServerIds", "types"]
    """
    url = "/estack/api/estack-vpc-server-console/customer/v3/port/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
