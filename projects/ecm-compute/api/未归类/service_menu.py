"""
API 层: pegasi/service-menu — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

# ---- pegasi/service-menu ----


def pegasi_exec_service_menu(c):
    """action: /estack/api/estack/pegasi/inner/v1/service-menu/check-auth/DECLOUD
    """
    url = "/estack/api/estack/pegasi/inner/v1/service-menu/check-auth/DECLOUD"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_exec_service_menu(c):
    """action: /estack/api/estack/pegasi/inner/v1/service-menu/check-auth/IRONIC
    """
    url = "/estack/api/estack/pegasi/inner/v1/service-menu/check-auth/IRONIC"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_exec_service_menu(c):
    """action: /estack/api/estack/pegasi/inner/v1/service-menu/check-auth/ECS
    """
    url = "/estack/api/estack/pegasi/inner/v1/service-menu/check-auth/ECS"
    return ApiResponse(c.request(
        "GET", url))


def pegasi_create_service_menu(c, **extra):
    """create: /estack/api/estack/pegasi/inner/v1/service-menu/service-project/pools
    body_keys: ["projectId", "resourceType"]
    """
    url = "/estack/api/estack/pegasi/inner/v1/service-menu/service-project/pools"
    return ApiResponse(c.request(
        "POST", url, json=extra or None))


def pegasi_exec_service_menu(c):
    """action: /estack/api/estack/pegasi/inner/v1/service-menu/check-auth/EAS
    """
    url = "/estack/api/estack/pegasi/inner/v1/service-menu/check-auth/EAS"
    return ApiResponse(c.request(
        "GET", url))
