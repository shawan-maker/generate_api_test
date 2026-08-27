"""
API 层: draco/images — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/images ----


def draco_exec_images(c):
    """action: /estack/api/estack/draco/v1/images/pictures
    """
    url = "/estack/api/estack/draco/v1/images/pictures"
    return ApiResponse(c.request(
        "GET", url))


def draco_exec_images(c):
    """action: /estack/api/estack/draco/v1/images/pictures-verification
    """
    url = "/estack/api/estack/draco/v1/images/pictures-verification"
    return ApiResponse(c.request(
        "GET", url))


def draco_exec_images(c):
    """action: /estack/api/estack/draco/v1/images/checkcap/code
    """
    url = "/estack/api/estack/draco/v1/images/checkcap/code"
    return ApiResponse(c.request(
        "GET", url))
