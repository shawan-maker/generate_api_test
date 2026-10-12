"""
auth_runner.py — 浏览器登录适配层（委托给 LoginSession）

薄包装器，将旧的调用接口适配到新的 LoginSession 架构。
"""

import logging
from pathlib import Path

from lib.auth import LoginSession, LoginConfig, LoginResult
from lib.auth.strategies.registry import get_strategy

LOG = logging.getLogger("auth_runner")


async def login_with_playwright(page, context, login_url, username, password,
                                project_profile: dict = None):
    """登录：委托给 LoginSession + EcsCloud 策略。

    从 project_profile 读取 auth/captcha 配置，不硬编码。

    注意：cookie 保存由调用方（run.py）负责，保存到 workspace/<project>/output/config/
    """
    p = project_profile or {}
    profile = {
        "strategy": p.get("strategy", "ecscloud"),
        "login_url": login_url,
        "auth": p.get("auth", {}),
        "captcha": p.get("captcha", {}),
        "selectors": p.get("selectors", {}),
    }

    config = LoginConfig.from_profile(profile, login_url=login_url)
    config.username = username
    config.password = password

    strategy_name = profile.get("strategy", "ecscloud")
    strategy = get_strategy(strategy_name)

    # 使用临时目录（cookie 由调用方保存）
    import tempfile
    temp_dir = Path(tempfile.mkdtemp())
    session = LoginSession(temp_dir, config)

    result = await strategy.login(page, context, config)

    # 清理临时目录
    try:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    return result == LoginResult.SUCCESS


def resolve_credentials(profile: dict, user_arg: str = None, pass_arg: str = None):
    """从参数或 profile 解析登录凭据。

    Args:
        profile: 项目 profile.yaml 内容
        user_arg: --user 命令行参数
        pass_arg: --pass 命令行参数

    Returns:
        (username, password) 元组
    """
    import os
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
