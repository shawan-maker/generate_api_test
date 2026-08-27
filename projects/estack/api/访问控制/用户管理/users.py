"""
API 层: draco/users — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- draco/users ----


def draco_get_users(c):
    """detail: /estack/api/estack/draco/v1/users/current-user
    """
    url = "/estack/api/estack/draco/v1/users/current-user"
    return ApiResponse(c.request(
        "GET", url))


def draco_exec_users(c):
    """action: /estack/api/estack/draco/v1/users/query-login-policy
    """
    url = "/estack/api/estack/draco/v1/users/query-login-policy"
    return ApiResponse(c.request(
        "GET", url))


def draco_exec_users(c, **extra):
    """action: /estack/api/estack/draco/v1/users/login
    body_keys: ["capcode", "password", "username", "xpos"]
    """
    url = "/estack/api/estack/draco/v1/users/login"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def draco_get_users(c):
    """detail: /estack/api/estack/draco/v1/users/password/expire-notice
    """
    url = "/estack/api/estack/draco/v1/users/password/expire-notice"
    return ApiResponse(c.request(
        "GET", url))


def draco_list_users(c, id: str):
    """list: /estack/api/estack/draco/v1/users/{id}/data-perfection
    """
    url = f"/estack/api/estack/draco/v1/users/{id}/data-perfection"
    return ApiResponse(c.request(
        "GET", url))


def draco_get_users(c):
    """detail: /estack/api/estack/draco/v1/users/detail
    """
    url = "/estack/api/estack/draco/v1/users/detail"
    return ApiResponse(c.request(
        "GET", url))
