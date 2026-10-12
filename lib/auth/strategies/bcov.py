"""
bcov.py — BCov 移动云运维平台登录策略

核心流程：填充凭据 → 点击验证 → 拦截 pictures-verification → flood-fill 缺口检测 → 拖拽 → 登录
移植自 login-kit 的 bcov.js
"""

import re
import asyncio
import logging

from ..strategy_base import LoginStrategy, LoginResult, LoginConfig

LOG = logging.getLogger("strategy.bcov")


class BcovStrategy(LoginStrategy):
    """BCov 移动云运维平台 — 拼图滑块验证码"""

    key = "bcov"
    label = "BCov 移动云运维平台"

    def __init__(self):
        self._latest_captcha = None
        self._captcha_version = 0

    def get_selectors(self) -> dict:
        return {
            "username": 'input[placeholder="用户名"]',
            "password": 'input[placeholder="登录密码"]',
            "login_button": 'button:has-text("登录")',
            "auth_button": 'button:has-text("点击完成验证")',
        }

    def get_defaults(self) -> dict:
        return {
            "login_url": "/bcov/login",
            "probe_url": "/bcov/overview/index",
            "captcha_type": "puzzle",
            "material_url_regex": "pictures-verification",
            "fresh_ttl_seconds": 1800,
        }

    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """BCov 拼图滑块登录

        流程（移植自 bcov.js）：
        1. 填充用户名密码
        2. 拦截 pictures-verification POST 响应
        3. 点击"点击完成验证"按钮
        4. 等待素材，调用 detect_bcov_slot（v6 flood-fill）
        5. 人类化拖拽（30 步，yJitter=1.5）
        6. 检查登录按钮是否可用
        7. 点击"登录"，等待 BCOV-Token cookie
        """
        self._latest_captcha = None
        self._captcha_version = 0

        username_sel = config.selectors.get("username", self.get_selectors()["username"])
        password_sel = config.selectors.get("password", self.get_selectors()["password"])
        auth_btn_text = config.auth_button_text or "点击完成验证"

        # 拦截 captcha 响应（POST pictures-verification）
        async def _on_response(resp):
            try:
                if "pictures-verification" in resp.url and resp.request.method == "POST":
                    text = await resp.text()
                    import json
                    body = json.loads(text)
                    if body and isinstance(body.get("entity"), dict) and body["entity"].get("backImage"):
                        self._latest_captcha = body["entity"]
                        self._captcha_version += 1
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        await page.wait_for_timeout(2000)

        # 填充凭据
        await page.fill(username_sel, config.username)
        await page.fill(password_sel, config.password)
        await page.wait_for_timeout(300)

        # 最多 5 次验证码尝试
        success = False
        for attempt in range(1, 6):
            LOG.info(f"[bcov] 验证码尝试 {attempt}/5")
            my_version = self._captcha_version
            self._latest_captcha = None

            # 检查验证按钮是否可见
            try:
                verify_btn = page.locator(f"button:has-text('{auth_btn_text}')").first
                verify_visible = await verify_btn.is_visible(timeout=3000)
            except Exception:
                verify_visible = False

            if not verify_visible:
                # 检查登录按钮是否已可用（可能之前已验证过）
                try:
                    login_btn = page.locator('button:has-text("登录")').first
                    login_visible = await login_btn.is_visible(timeout=2000)
                    login_disabled = await login_btn.is_disabled() if login_visible else True
                    if login_visible and not login_disabled:
                        LOG.info("[bcov] 登录按钮已可用，跳过验证码")
                        success = True
                        break
                except Exception:
                    pass

                # 重载页面重试
                LOG.info("[bcov] 验证按钮不可见，重载页面")
                await page.reload(wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)
                await page.fill(username_sel, config.username)
                await page.fill(password_sel, config.password)
                continue

            # 点击验证按钮
            await verify_btn.click()

            # 等待 captcha 响应
            for _ in range(40):
                if self._captcha_version > my_version:
                    break
                await page.wait_for_timeout(200)

            if not self._latest_captcha:
                LOG.warning("[bcov] 未捕获到验证码 API 响应")
                continue

            # 检测缺口位置
            captcha = self._latest_captcha
            LOG.info(f"[bcov] yHeight={captcha.get('yHeight', 0)}, "
                     f"piece b64={len(captcha.get('slidingImage', ''))}, "
                     f"back b64={len(captcha.get('backImage', ''))}")

            try:
                await page.wait_for_selector('.slide-verify-slider', timeout=10000)
                await page.wait_for_timeout(600)
            except Exception:
                LOG.warning("[bcov] 滑块未出现")
                continue

            try:
                from ..captcha.puzzle import PuzzleSolver
                solver = PuzzleSolver()
                slot_x = solver.detect(
                    captcha["backImage"],
                    captcha["slidingImage"],
                    captcha.get("yHeight", 0)
                )
            except ImportError:
                LOG.error("[bcov] captcha/puzzle 模块未实现")
                return LoginResult.FAILED

            LOG.info(f"[bcov] 检测到缺口 X={slot_x}")
            if slot_x < 0 or slot_x > 330:
                LOG.warning(f"[bcov] 缺口 X 不合法: {slot_x}")
                continue

            # 读取滑块位置并拖拽
            try:
                slider_box = await page.locator('.slide-verify-slider:visible').first.bounding_box()
            except Exception:
                slider_box = None

            if not slider_box:
                LOG.warning("[bcov] 滑块位置读取失败")
                continue

            handle_x = slider_box["x"] + 4
            handle_y = slider_box["y"] + slider_box["height"] / 2
            target_x = handle_x + slot_x

            LOG.info(f"[bcov] 拖拽 ({handle_x:.0f},{handle_y:.0f}) -> ({target_x:.0f},{handle_y:.0f}), dist={slot_x}")

            from ..human_drag import human_like_drag
            await human_like_drag(page,
                handle_x, handle_y,
                target_x, handle_y,
                steps=30, y_jitter=1.5, overshoot=0)

            await page.wait_for_timeout(2000)

            # 检查登录按钮是否可用（通过 JS 检查可见且非 disabled）
            try:
                btn_status = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const vis = btns.filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    const loginBtn = vis.find(b => (b.textContent || '').trim() === '登录');
                    if (!loginBtn) return { found: false, disabled: true };
                    return { found: true, disabled: loginBtn.disabled };
                }""")
                is_disabled = btn_status.get("disabled", True)
                btn_found = btn_status.get("found", False)
            except Exception:
                is_disabled = True
                btn_found = False

            LOG.info(f"[bcov] 登录按钮 found={btn_found}, disabled={is_disabled}")
            if btn_found and not is_disabled:
                LOG.info("[bcov] 验证码通过")
                success = True
                break

            await page.wait_for_timeout(1500)

        if not success:
            LOG.error("[bcov] 验证码 5 次尝试后仍未通过")
            return LoginResult.FAILED

        # 点击登录（多策略：Playwright → JS 兜底）
        LOG.info("[bcov] 点击登录按钮")
        clicked = False

        # 方法 1：Playwright locator
        try:
            login_btn = page.get_by_role("button", name="登录", exact=True)
            await login_btn.wait_for(state="visible", timeout=5000)
            await login_btn.click()
            clicked = True
        except Exception as e:
            LOG.debug(f"[bcov] Playwright locator 失败: {e}")

        # 方法 2：JS 兜底（找到可见的登录按钮）
        if not clicked:
            try:
                clicked = await page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const vis = btns.filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    });
                    // 精确匹配"登录"文本
                    let target = vis.find(b => (b.textContent || '').trim() === '登录');
                    if (!target) {
                        // 宽松匹配
                        target = vis.find(b => (b.textContent || '').trim().includes('登录'));
                    }
                    if (target) {
                        target.click();
                        return true;
                    }
                    return false;
                }""")
            except Exception as e:
                LOG.warning(f"[bcov] JS 兜底点击失败: {e}")

        if not clicked:
            LOG.error("[bcov] 登录按钮点击失败")
            return LoginResult.FAILED

        await page.wait_for_timeout(3000)

        # 等待 BCOV-Token cookie 和 URL 离开登录页
        for i in range(10):
            current_url = page.url
            cookies = await context.cookies()
            has_token = any(
                re.match(r"BCOV-Token", c["name"], re.IGNORECASE) and c["value"]
                for c in cookies
            )
            if has_token and "/login" not in current_url.lower():
                LOG.info(f"[bcov] 登录成功 url={current_url} (BCOV-Token 已下发)")
                return LoginResult.SUCCESS
            await page.wait_for_timeout(1000)

        LOG.error(f"[bcov] 点击登录后未取得会话凭证，最终 URL: {page.url}")
        return LoginResult.FAILED

    async def probe(self, page, probe_url: str) -> bool:
        """探测会话是否有效 — 访问 overview/index，跳登录页即失效"""
        try:
            await page.wait_for_timeout(2500)
            current_url = page.url
            if "/login" in current_url.lower():
                return False
            return True
        except Exception:
            return False
