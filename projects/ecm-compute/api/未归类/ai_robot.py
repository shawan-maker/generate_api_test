"""
API 层: default/ai-robot — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/ai-robot ----


def default_create_ai_robot(c, **extra):
    """create: /estack/api/estack/taurus/v1/ai-robot/conversations
    body_keys: ["limit", "noloading", "userId"]
    """
    url = "/estack/api/estack/taurus/v1/ai-robot/conversations"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
