"""
API 层: libra/notice — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- libra/notice ----


def libra_list_notice(c):
    """list: /estack/api/estack/libra/v1/notice/new/count/all-type
    """
    url = "/estack/api/estack/libra/v1/notice/new/count/all-type"
    return ApiResponse(c.request(
        "GET", url))
