"""
session.py — 统一登录会话

所有框架登录的唯一入口。实现 cookie 优先 → 新鲜度缓存 → 服务端探测 → 证伪才登录 的完整流程。
沿用 login-kit 的 session.js 架构。
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from .cookie_store import CookieStore
from .strategy_base import LoginConfig, LoginResult, LoginStrategy

LOG = logging.getLogger("login_session")


class LoginSession:
    """统一登录会话 — 所有框架登录的唯一入口

    职责：
    - Cookie 读写（通过 CookieStore）
    - 新鲜度检查
    - 服务端探测
    - 登录重试编排
    - Cookie 注入到 Playwright context
    - localStorage/sessionStorage 持久化
    """

    def __init__(self, project_dir: Path, config: LoginConfig):
        """
        Args:
            project_dir: 项目目录（workspace/<project>/）
            config: 登录配置
        """
        self.project_dir = project_dir
        self.config = config
        self.store = CookieStore()

    async def ensure_auth(self, page, context, strategy: LoginStrategy) -> bool:
        """确保认证有效的主流程

        流程：
        1. 尝试加载本地 cookie（不论 TTL）
        2. 服务端探测（访问 probe_url）
        3. 探测成功 → 刷新 TTL 并返回
        4. 探测失败 → 执行登录（strategy.login，带重试）
        5. 登录成功 → 保存 cookies + localStorage + sessionStorage

        Args:
            page: Playwright Page 对象
            context: Playwright BrowserContext 对象
            strategy: 登录策略实例

        Returns:
            bool: 是否成功（已认证或登录成功）
        """
        # Step 1: 尝试加载本地 cookie（不论 TTL 是否过期）
        cookies = self.store.load(self.project_dir)
        if cookies:
            filtered = self.store.filter_by_host(cookies, self.config.host)

            if filtered:
                await context.add_cookies(filtered)

                # 注入 localStorage（如果配置了持久化）
                # 关键：使用 addInitScript 确保在任何页面加载前注入
                if self.config.persist_localStorage:
                    await self._inject_localStorage(context)

                # Step 2: 服务端探测（即使 TTL 过期也尝试）
                if self.config.probe_url:
                    probe_url = self.config.base_url.rstrip("/") + self.config.probe_url
                    # 关键：hash SPA 需要先跳到 about:blank 再跳到目标页
                    # 否则 addInitScript 不会重新触发
                    if probe_url.startswith(self.config.base_url) and '#' in probe_url:
                        try:
                            await page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
                        except Exception:
                            pass
                    try:
                        await page.goto(probe_url, wait_until="networkidle", timeout=15000)
                        if await strategy.probe(page, probe_url):
                            # Step 3: 探测成功 → 刷新 TTL
                            is_fresh = self.store.is_fresh(self.project_dir, self.config.fresh_ttl_seconds)
                            if is_fresh:
                                LOG.info("✅ Cookie 有效，复用现有会话")
                            else:
                                LOG.info("✅ Cookie TTL 已过期，但服务端仍有效，复用会话并刷新 TTL")
                            # 刷新 savedAt（延长 TTL）
                            self.store.save(self.project_dir, filtered)
                            return True
                    except Exception as e:
                        LOG.warning(f"探测失败: {e}")
                else:
                    # 无 probe_url，依赖 TTL
                    if self.store.is_fresh(self.project_dir, self.config.fresh_ttl_seconds):
                        LOG.info("✅ Cookie 新鲜，跳过探测")
                        return True
                    else:
                        LOG.warning("Cookie TTL 已过期，且无 probe_url，执行登录")

        # Step 4: 探测失败或无 cookie，执行登录（带重试）
        # ★ 探测失败后删除旧 cookie，防止登录失败后下次仍复用失效的 cookie
        if cookies:
            self.store.clear(self.project_dir)
            LOG.info("  探测失败，已清除旧 cookie")
        # 关键：登录时不注入 localStorage（避免污染登录表单）
        for attempt in range(self.config.max_login_retries):
            LOG.info(f"执行登录（尝试 {attempt + 1}/{self.config.max_login_retries}）")

            try:
                # 登录页用 networkidle 确保所有资源加载完成
                await page.goto(self.config.login_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                result = await strategy.login(page, context, self.config)

                if result == LoginResult.SUCCESS:
                    # Step 4: 保存 cookies + storage
                    cookies = await context.cookies()
                    self.store.save(self.project_dir, cookies)

                    if self.config.persist_localStorage:
                        await self._save_localStorage(page)
                    if self.config.persist_sessionStorage:
                        await self._save_sessionStorage(page)

                    LOG.info("✅ 登录成功，cookies 已保存")
                    return True

                elif result == LoginResult.LOCKED:
                    LOG.error("❌ 账号被锁定，停止重试")
                    return False

                # FAILED: 等待后重试
                if attempt < self.config.max_login_retries - 1:
                    LOG.warning(f"登录失败，2秒后重试...")
                    await asyncio.sleep(2)

            except Exception as e:
                LOG.error(f"登录异常: {e}")
                if attempt < self.config.max_login_retries - 1:
                    await asyncio.sleep(2)

        LOG.error("❌ 登录失败，已达最大重试次数")
        return False

    async def _inject_localStorage(self, context):
        """注入 localStorage 到 Playwright context（通过 addInitScript）"""
        storage = self.store.load_localStorage(self.project_dir)
        if not storage:
            return

        # 构建注入脚本
        script = "(() => {\n"
        for key, value in storage.items():
            # 转义特殊字符
            escaped_value = value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
            script += f"  localStorage.setItem('{key}', '{escaped_value}');\n"
        script += "})();"

        await context.add_init_script(script)
        LOG.debug(f"已注入 localStorage: {len(storage)} 项")

    async def _save_localStorage(self, page):
        """保存页面的 localStorage"""
        try:
            storage = await page.evaluate("() => JSON.parse(JSON.stringify(localStorage))")
            self.store.save_localStorage(self.project_dir, storage)
        except Exception as e:
            LOG.warning(f"localStorage 保存失败: {e}")

    async def _save_sessionStorage(self, page):
        """保存页面的 sessionStorage"""
        try:
            storage = await page.evaluate("() => JSON.parse(JSON.stringify(sessionStorage))")
            self.store.save_sessionStorage(self.project_dir, storage)
        except Exception as e:
            LOG.warning(f"sessionStorage 保存失败: {e}")
