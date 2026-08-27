"""
API 层: draco/attachments — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/attachments ----


def draco_create_attachments(c, **extra):
    """create: /estack/api/estack/draco/v1/attachments
    body_keys: ["pageNum", "pageSize", "searchType", "tenantId"]
    """
    url = "/estack/api/estack/draco/v1/attachments"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
