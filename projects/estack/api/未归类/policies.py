"""
API 层: draco/policies — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/policies ----


def draco_list_policies(c, **extra):
    """list: /estack/api/estack/draco/v1/policies/list
    body_keys: ["isIncludeDefaultPolicy", "pageNum", "pageSize", "policyCategory", "policyType"]
    """
    url = "/estack/api/estack/draco/v1/policies/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
