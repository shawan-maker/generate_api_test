"""
API 层: draco/basic-config — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/basic-config ----


def draco_list_basic_config(c):
    """list: /estack/api/estack/draco/v1/basic-config/ai
    """
    url = "/estack/api/estack/draco/v1/basic-config/ai"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_basic_config(c):
    """list: /estack/api/estack/draco/inner/v1/basic-config/AccessHJ
    """
    url = "/estack/api/estack/draco/inner/v1/basic-config/AccessHJ"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_basic_config(c):
    """list: /estack/api/estack/draco/inner/v1/basic-config/Currency
    """
    url = "/estack/api/estack/draco/inner/v1/basic-config/Currency"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_basic_config(c):
    """list: /estack/api/estack/draco/inner/v1/basic-config/MaxBandwidth
    """
    url = "/estack/api/estack/draco/inner/v1/basic-config/MaxBandwidth"
    return ApiResponse(c.request(
        "GET", url))
