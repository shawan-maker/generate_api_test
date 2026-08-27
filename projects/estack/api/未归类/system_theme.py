"""
API 层: pegasi/system-theme — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- pegasi/system-theme ----


def pegasi_get_system_theme(c):
    """detail: /estack/api/estack/pegasi/v1/system-theme
    """
    url = "/estack/api/estack/pegasi/v1/system-theme"
    return ApiResponse(c.request(
        "GET", url))
