"""
API 层: draco/tenants — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/tenants ----


def draco_list_tenants(c):
    """list: /estack/api/estack/draco/v1/tenants/display-by-role
    """
    url = "/estack/api/estack/draco/v1/tenants/display-by-role"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_tenants(c):
    """list: /estack/api/estack/draco/v1/tenants/users
    """
    url = "/estack/api/estack/draco/v1/tenants/users"
    return ApiResponse(c.request(
        "GET", url))


def draco_get_tenants(c):
    """detail: /estack/api/estack/draco/v1/tenants/root
    """
    url = "/estack/api/estack/draco/v1/tenants/root"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_tenants(c):
    """list: /estack/api/estack/draco/v1/tenants/children
    """
    url = "/estack/api/estack/draco/v1/tenants/children"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_tenants(c):
    """list: /estack/api/estack/draco/v1/tenants/display
    """
    url = "/estack/api/estack/draco/v1/tenants/display"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_tenants(c):
    """list: /estack/api/estack/draco/v1/tenants/users/display-unit-total
    """
    url = "/estack/api/estack/draco/v1/tenants/users/display-unit-total"
    return ApiResponse(c.request(
        "GET", url))
