"""
API 层: pegasi/menu — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- pegasi/menu ----


def pegasi_create_menu(c, **extra):
    """create: /estack/api/estack/pegasi/v1/menu/side-tree
    body_keys: ["isVisible", "userId"]
    """
    url = "/estack/api/estack/pegasi/v1/menu/side-tree"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def pegasi_list_menu(c, **extra):
    """list: /estack/api/estack/pegasi/v1/menu/portal/tree
    body_keys: ["isAll", "rootId"]
    """
    url = "/estack/api/estack/pegasi/v1/menu/portal/tree"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def pegasi_exec_menu(c, **extra):
    """action: /estack/api/estack/pegasi/inner/v1/menu/check-auth/list
    body_keys: ["resourceTypes"]
    """
    url = "/estack/api/estack/pegasi/inner/v1/menu/check-auth/list"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def pegasi_list_menu(c, **extra):
    """list: /estack/api/estack/pegasi/v1/menu/tree
    body_keys: ["isVisible", "rootId", "userId"]
    """
    url = "/estack/api/estack/pegasi/v1/menu/tree"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def pegasi_list_menu(c):
    """list: /estack/api/estack/pegasi/v1/menu/favorite/list
    """
    url = "/estack/api/estack/pegasi/v1/menu/favorite/list"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_list_menu(c):
    """list: /estack/api/estack/pegasi/v1/menu/access-log
    """
    url = "/estack/api/estack/pegasi/v1/menu/access-log"
    return ApiResponse(c.request(
        "GET", url))
