"""
auth_service.py — 统一认证服务

所有执行入口（管线运行、模块探测、并行执行等）的认证统一接口。
原则：cookie 优先 → 新鲜度检查 → 服务端探测 → 失效才登录。
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("auth_service")


class AuthService:
    """统一认证服务 — 所有入口的唯一认证接口

    职责：
    - 凭据解析（credentials.yaml > 环境变量 > profile.yaml）
    - Cookie 新鲜度检查（CookieStore）
    - 浏览器认证（LoginSession + Strategy）
    - HTTP Session 构建（API 测试脚本用）

    用法：
        auth = AuthService(project_dir, workspace_dir, profile)
        # 浏览器入口
        await auth.ensure_browser_auth(page, context)
        # HTTP 入口
        session = auth.ensure_http_session()
        # 仅检查
        auth.check_cookie_fresh()
    """

    def __init__(self, project_dir: Path, workspace_dir: Path, profile: dict,
                 user_override: str = None, pass_override: str = None):
        """
        Args:
            project_dir: 项目目录 (projects/<name>/)
            workspace_dir: 工作目录 (workspace/<name>/)
            profile: profile.yaml 内容
            user_override: 命令行 --user 覆盖
            pass_override: 命令行 --pass 覆盖
        """
        self.project_dir = project_dir
        self.workspace_dir = workspace_dir
        self.profile = profile
        self._user_override = user_override
        self._pass_override = pass_override

        # 延迟导入（避免循环依赖）
        from lib.auth.cookie_store import CookieStore
        from lib.auth.session import LoginSession
        from lib.auth.strategy_base import LoginConfig
        from lib.auth.strategies.registry import get_strategy

        self._CookieStore = CookieStore
        self._LoginSession = LoginSession
        self._LoginConfig = LoginConfig
        self._get_strategy = get_strategy

        self._store = CookieStore()

    def _resolve_credentials(self) -> dict:
        """解析凭据 — credentials.yaml(base_url匹配) > 环境变量 > profile.yaml

        Returns:
            {"username": ..., "password": ...}
        """
        base_url = self.profile.get("base_url", "")

        # 1. 从 credentials.yaml 按 base_url 匹配
        site_creds = {}
        try:
            import yaml
            creds_file = Path(__file__).resolve().parents[2] / "config" / "credentials.yaml"
            if creds_file.exists():
                all_creds = yaml.safe_load(creds_file.read_text(encoding="utf-8")) or {}
                sites = all_creds.get("sites", {})
                if base_url in sites:
                    site_creds = sites[base_url]
                    LOG.debug(f"凭据来源: credentials.yaml (base_url={base_url})")
        except Exception as e:
            LOG.debug(f"加载 credentials.yaml 失败: {e}")

        # 2. 从 profile.yaml 的 credentials 字段
        profile_creds = self.profile.get("credentials", {}) or {}

        # 3. 合并：显式覆盖 > credentials.yaml > 环境变量 > profile.yaml
        username = (
            self._user_override
            or site_creds.get("username")
            or os.environ.get(profile_creds.get("username_env", ""), "")
            or profile_creds.get("username")
            or ""
        )
        password = (
            self._pass_override
            or site_creds.get("password")
            or os.environ.get(profile_creds.get("password_env", ""), "")
            or profile_creds.get("password")
            or ""
        )

        return {"username": username, "password": password}

    def _build_login_config(self) -> "LoginConfig":
        """构建 LoginConfig"""
        creds = self._resolve_credentials()
        base_url = self.profile.get("base_url", "")
        login_url = self.profile.get("login_url", "")

        # 合并 credentials.yaml 中的 auth 配置（优先于 profile.yaml）
        site_auth = {}
        try:
            import yaml
            creds_file = Path(__file__).resolve().parents[2] / "config" / "credentials.yaml"
            if creds_file.exists():
                all_creds = yaml.safe_load(creds_file.read_text(encoding="utf-8")) or {}
                sites = all_creds.get("sites", {})
                site_auth = sites.get(base_url, {}).get("auth", {})
        except Exception:
            pass

        # profile.yaml 的 auth 配置
        profile_auth = self.profile.get("auth", {}) or {}

        # 合并：credentials.yaml auth > profile.yaml auth
        merged_auth = {**profile_auth, **site_auth}

        config_profile = {
            "strategy": self.profile.get("strategy", "ecscloud"),
            "base_url": base_url,
            "login_url": login_url,
            "probe_url": self.profile.get("probe_url", ""),
            "auth": merged_auth,
            "captcha": self.profile.get("captcha", {}),
            "selectors": self.profile.get("selectors", {}),
            "session": self.profile.get("session", {}),
        }

        config = self._LoginConfig.from_profile(config_profile, base_url=base_url, login_url=login_url)
        config.username = creds["username"]
        config.password = creds["password"]

        return config

    def _get_strategy_instance(self):
        """获取策略实例"""
        strategy_name = self.profile.get("strategy", "ecscloud")
        return self._get_strategy(strategy_name)

    # ========== 公共接口 ==========

    def check_cookie_fresh(self) -> bool:
        """仅检查 cookie 新鲜度（不触发登录）

        Returns:
            True 表示 cookie 在 TTL 内
        """
        config = self._build_login_config()
        return self._store.is_fresh(self.workspace_dir, config.fresh_ttl_seconds)

    async def ensure_browser_auth(self, page, context) -> bool:
        """浏览器认证 — 有 page/context 的入口使用

        完整流程：
        1. CookieStore.is_fresh() → 快速判断
        2. CookieStore.load() + filter_by_host() → 域名过滤
        3. context.add_cookies() + localStorage 注入
        4. 服务端探测 (strategy.probe)
        5. 失效 → LoginSession.ensure_auth() (带重试)

        Args:
            page: Playwright Page 对象
            context: Playwright BrowserContext 对象

        Returns:
            True 表示认证成功
        """
        config = self._build_login_config()

        if not config.username or not config.password:
            LOG.error("❌ 缺少登录凭据（请检查 credentials.yaml 或设置环境变量）")
            return False

        strategy = self._get_strategy_instance()
        session = self._LoginSession(self.workspace_dir, config)

        return await session.ensure_auth(page, context, strategy)

    async def refresh_browser_cookies(self) -> bool:
        """强制刷新 cookie — 启动新浏览器登录

        用于：
        - 并行执行中期刷新
        - 测试脚本重试前
        - 发现流程中途刷新

        Returns:
            True 表示刷新成功
        """
        config = self._build_login_config()

        if not config.username or not config.password:
            LOG.error("❌ 无法刷新 cookie: 缺少登录凭据")
            return False

        try:
            from playwright.async_api import async_playwright
            from lib.browser_launcher import launch_browser

            strategy = self._get_strategy_instance()
            session = self._LoginSession(self.workspace_dir, config)

            async with async_playwright() as pw:
                browser, context, page = await launch_browser(pw, headless=False)
                try:
                    success = await session.ensure_auth(page, context, strategy)
                    if success:
                        cookies = await context.cookies()
                        LOG.info(f"✅ 新 cookie 已保存 ({len(cookies)} 条)")
                    return success
                finally:
                    await browser.close()
        except Exception as e:
            LOG.error(f"cookie 刷新异常: {e}")
            return False

    def ensure_http_session(self):
        """HTTP 认证 — API 测试脚本使用（框架内 full 模式）

        流程：
        1. CookieStore 加载 cookie
        2. 构建 requests.Session（含 cookie + auth header）
        3. cookie 失效时不自动登录（由外部确保）

        Returns:
            requests.Session 或 None
        """
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        config = self._build_login_config()

        # 加载 cookie
        cookies = self._store.load(self.workspace_dir)
        if not cookies:
            LOG.warning("无可用 cookie")
            return None

        # 域名过滤
        filtered = self._store.filter_by_host(cookies, config.host)

        # 提取 token
        token = None
        if config.cookie_token_key:
            for c in filtered:
                if c.get("name") == config.cookie_token_key:
                    token = c.get("value")
                    break

        # 构建 session
        session = requests.Session()
        session.verify = False
        session.headers["Content-Type"] = "application/json"

        if token:
            session.headers[config.header_name] = f"{config.header_prefix}{token}"

        for c in filtered:
            session.cookies.set(
                c["name"], c["value"],
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
            )

        return session
