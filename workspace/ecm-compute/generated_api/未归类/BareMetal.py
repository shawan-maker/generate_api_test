"""
API 层: default/BareMetal — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- default/BareMetal ----


def default_list_BareMetal(c):
    """list: /estack/api/estack-console-backend-bms/acl/v3/BareMetal/userconfig/IRONIC_LIST_FIELDS
    """
    url = "/estack/api/estack-console-backend-bms/acl/v3/BareMetal/userconfig/IRONIC_LIST_FIELDS"
    return ApiResponse(c.request(
        "GET", url))


def default_exec_BareMetal(c):
    """action: /estack/api/estack-console-backend-bms/acl/v3/BareMetal/check/IBIronic
    """
    url = "/estack/api/estack-console-backend-bms/acl/v3/BareMetal/check/IBIronic"
    return ApiResponse(c.request(
        "GET", url))


def default_list_BareMetal(c):
    """list: /estack/api/estack-console-backend-bms/acl/v3/BareMetal/stats
    """
    url = "/estack/api/estack-console-backend-bms/acl/v3/BareMetal/stats"
    return ApiResponse(c.request(
        "GET", url))


def default_list_BareMetal(c):
    """list: /estack/api/estack-console-backend-bms/acl/v3/BareMetal/with/network
    """
    url = "/estack/api/estack-console-backend-bms/acl/v3/BareMetal/with/network"
    return ApiResponse(c.request(
        "GET", url))
