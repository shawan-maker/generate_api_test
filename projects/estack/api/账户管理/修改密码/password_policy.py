"""
API 层: draco/password-policy — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/password-policy ----


def draco_get_password_policy(c):
    """detail: /estack/api/estack/draco/v1/password-policy
    """
    url = "/estack/api/estack/draco/v1/password-policy"
    return ApiResponse(c.request(
        "GET", url))
