"""
API 层: delphini/dynamic-dictionary — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- delphini/dynamic-dictionary ----


def delphini_exec_dynamic_dictionary(c, **extra):
    """action: /estack/api/estack/delphini/v1/dynamic-dictionary/json
    body_keys: ["categories", "languages"]
    """
    url = "/estack/api/estack/delphini/v1/dynamic-dictionary/json"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
