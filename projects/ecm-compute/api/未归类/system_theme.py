"""
API 层: pegasi/system-theme — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- pegasi/system-theme ----


def pegasi_list_system_theme(c):
    """list: /estack/api/estack/pegasi/v1/system-theme
    """
    url = "/estack/api/estack/pegasi/v1/system-theme"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_list_system_theme(c):
    """list: /estack/api/estack/pegasi/v1/system-theme/default/tab
    """
    url = "/estack/api/estack/pegasi/v1/system-theme/default/tab"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_list_system_theme(c):
    """list: /estack/api/estack/pegasi/inner/v1/system-theme/default/tab
    """
    url = "/estack/api/estack/pegasi/inner/v1/system-theme/default/tab"
    return ApiResponse(c.request(
        "GET", url))
