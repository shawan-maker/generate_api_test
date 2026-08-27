"""
API 层: draco/groups — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/groups ----


def draco_list_groups(c):
    """list: /estack/api/estack/draco/v1/groups
    """
    url = "/estack/api/estack/draco/v1/groups"
    return ApiResponse(c.request(
        "GET", url))
