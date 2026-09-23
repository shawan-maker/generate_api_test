"""
auth_runner.py — 浏览器登录 + cookie 管理

从 run.py 提取的登录相关逻辑，供单模块和批量模式复用。
"""

import os
import json
import logging
from pathlib import Path

LOG = logging.getLogger("auth_runner")


async def login_with_playwright(page, context, login_url, username, password,
                                project_profile: dict = None):
    """登录：cookie 优先 → 失效后走滑块自动登录（复用 lib/auth.py）。

    从 project_profile 读取 auth/captcha 配置，不硬编码。

    注意：cookie 保存由调用方（run.py）负责，保存到 workspace/<project>/output/config/
    """
    from lib import auth

    p = project_profile or {}
    profile = {
        "login_url": login_url,
        "auth": p.get("auth", {}),
        "captcha": p.get("captcha", {}),
    }

    sess = auth.AuthSession(profile, username, password)
    LOG.info("  执行滑块登录（复用 login_with_browser）...")
    ok = await sess.login_with_browser(page, context)

    return ok


async def ensure_browser_login(page, context, profile: dict, base_url: str,
                               login_url: str, username: str, password: str):
    """确保浏览器已登录：先尝试 cookie，失败则滑块登录。

    Args:
        page: Playwright page 对象
        context: Playwright context 对象
        profile: 项目 profile.yaml 内容
        base_url: 基础 URL
        login_url: 登录页 URL
        username: 用户名
        password: 密码

    Returns:
        True 如果登录成功
    """
    cookie_file = Path(__file__).resolve().parents[1] / "projects" / "output" / "config" / "cookies.json"

    # 尝试从 output/config 加载已有 cookie
    logged_in = False
    project_dir = None
    # 从 profile 推断 project_dir
    # cookie_file 应由调用方提供，这里用通用逻辑
    if cookie_file.exists():
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            if cookies:
                await context.add_cookies(cookies)
                try:
                    await page.goto(base_url, wait_until="load", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)
                if "/login" not in page.url:
                    LOG.info("  ✅ Cookie 有效，直接进入系统")
                    logged_in = True
        except Exception:
            pass

    if not logged_in:
        LOG.info("  执行滑块登录...")
        ok = await login_with_playwright(page, context, login_url, username, password,
                                        project_profile=profile)
        if not ok:
            LOG.error("❌ 登录失败")
            return False
        LOG.info("  ✅ 登录成功")

    return True


def resolve_credentials(profile: dict, user_arg: str = None, pass_arg: str = None):
    """从参数或 profile 解析登录凭据。

    Args:
        profile: 项目 profile.yaml 内容
        user_arg: --user 命令行参数
        pass_arg: --pass 命令行参数

    Returns:
        (username, password) 元组
    """
    creds = profile.get("credentials", {}) or {}
    username = (user_arg
                or creds.get("username")
                or os.environ.get(creds.get("username_env", ""), "")
                or "")
    password = (pass_arg
                or creds.get("password")
                or os.environ.get(creds.get("password_env", ""), "")
                or "")
    return username, password
