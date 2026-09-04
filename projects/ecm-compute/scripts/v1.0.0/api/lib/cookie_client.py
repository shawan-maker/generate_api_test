"""cookie_client.py — 轻量 cookie 鉴权（生成脚本专用）

生成脚本只做 cookie 注入，不做登录。登录方式各项目不同无法统一，
但 cookie 格式（Playwright JSON 数组）是统一的。

Cookie 来源约定:
  - 框架层 (--stage 1/2) 执行时自动登录并保存到 config/cookies.json
  - 用户从浏览器 DevTools 导出 cookie
  - CI 流程定期刷新 cookie

本模块不依赖 Playwright / OpenCV / httpx，仅用 requests + urllib3。
"""

import json
import sys
from pathlib import Path
from typing import Optional


def load_cookies(cookie_path) -> tuple:
    """读取 cookies.json，返回 (cookies_list, token_value_or_None)。

    Args:
        cookie_path: cookies.json 的路径（str 或 Path）

    Returns:
        (cookies, token) 元组。文件不存在或解析失败返回 ([], None)。
    """
    path = Path(cookie_path)
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return [], None
        token = get_token(data)
        return data, token
    except Exception:
        return [], None


def get_token(cookies: list, token_key: str = "accessToken") -> Optional[str]:
    """从 cookie 列表提取 token 值。

    查找优先级:
    1. token_key 参数指定的 cookie name（默认 accessToken）
    2. 兜底: estackToken / Authorization

    Args:
        cookies: Playwright 格式的 cookie 列表
        token_key: 优先查找的 cookie name

    Returns:
        token 字符串，未找到返回 None
    """
    if not cookies:
        return None
    # 主查找
    for c in cookies:
        if c.get("name") == token_key:
            return c.get("value")
    # 兜底
    fallback_names = {"estackToken", "Authorization"} - {token_key}
    for c in cookies:
        if c.get("name") in fallback_names:
            return c.get("value")
    return None


def build_requests_session(cookies: list, token: str, auth_config: dict):
    """构建已鉴权的 requests.Session（API 脚本用）。

    Args:
        cookies: Playwright 格式的 cookie 列表
        token: 鉴权 token 值
        auth_config: 鉴权配置，包含 header_name, header_prefix, fixed_headers

    Returns:
        配置好的 requests.Session 实例
    """
    import requests
    import urllib3

    # 禁用 SSL 验证警告
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    session = requests.Session()
    session.verify = False

    # 固定头部
    fixed_headers = dict(auth_config.get("fixed_headers", {}))
    fixed_headers["Content-Type"] = "application/json"
    session.headers.update(fixed_headers)

    # 鉴权头
    header_name = auth_config.get("header_name", "Authorization")
    header_prefix = auth_config.get("header_prefix", "Bearer ")
    if token:
        session.headers[header_name] = f"{header_prefix}{token}"

    # Cookie
    for c in cookies:
        session.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain", ""),
            path=c.get("path", "/"),
        )

    return session


async def apply_to_playwright_context(context, cookies: list):
    """注入 cookie 到 Playwright BrowserContext（UI 脚本用）。

    Args:
        context: Playwright BrowserContext 实例
        cookies: Playwright 格式的 cookie 列表
    """
    if cookies:
        await context.add_cookies(cookies)


async def inject_token_to_storage(page, token: str, storage_type: str = "localStorage",
                            token_key: str = "estackToken"):
    """注入 token 到 localStorage / sessionStorage（UI 脚本用）。

    Args:
        page: Playwright Page 实例
        token: token 值
        storage_type: "localStorage" 或 "sessionStorage"
        token_key: 存储的 key 名
    """
    if token and storage_type:
        await page.evaluate(
            f"() => {storage_type}.setItem('{token_key}', '{token}')"
        )


def require_auth(config_dir, auth_config: dict):
    """一键鉴权：读 cookie → 提取 token → 构建 Session。

    API 脚本专用。鉴权失败时打印错误并 sys.exit(1)。

    Args:
        config_dir: config/ 目录路径（cookies.json 所在位置）
        auth_config: 鉴权配置

    Returns:
        (requests.Session, cookies_list) 元组
    """
    cookie_path = Path(config_dir) / "cookies.json"
    cookies, token = load_cookies(cookie_path)

    if not cookies or not token:
        print("=" * 60)
        print("  ❌ 鉴权失败: 未找到有效的 cookies.json")
        print("=" * 60)
        print()
        print(f"  期望文件: {cookie_path}")
        print()
        print("  解决方法:")
        print("    1. 运行框架刷新 cookie:")
        print("       python -m module_discovery.run --project <project> --stage 1 ...")
        print("    2. 从浏览器 DevTools 导出 cookie 到上述路径")
        print()
        sys.exit(1)

    session = build_requests_session(cookies, token, auth_config)
    print(f"  ✅ Cookie 鉴权成功 ({len(cookies)} cookies, token={'yes' if token else 'no'})")
    return session, cookies
