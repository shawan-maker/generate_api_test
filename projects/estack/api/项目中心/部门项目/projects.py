"""
API 层: draco/projects — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/projects ----


def draco_list_projects(c, **extra):
    """list: /estack/api/estack/draco/v1/projects/list
    body_keys: ["filterJoinedProject", "isDisplayChildren", "pageNum", "pageSize", "statusList"]
    """
    url = "/estack/api/estack/draco/v1/projects/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))
