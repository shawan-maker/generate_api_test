"""
ecso.py — ECSO 全栈专属云登录策略

核心流程：填充凭据 → 点击登录 → 触发 NEED_DIGIT_CAPTCHA → OCR 算术答案 → 弹窗确定
移植自 login-kit 的 ecso.js

关键注意事项：
- 验证码输入框在弹窗内（el-dialog.img-code-dialog），不在登录表单里
- 必须点弹窗的"确定"按钮才会携带 answer 提交，点登录页的"登录"只重发 username+password
"""

import asyncio
import logging

from ..strategy_base import LoginStrategy, LoginResult, LoginConfig

LOG = logging.getLogger("strategy.ecso")


class EcsoStrategy(LoginStrategy):
    """ECSO 全栈专属云 — 算术验证码"""

    key = "ecso"
    label = "ECSO 全栈专属云"

    def get_selectors(self) -> dict:
        return {
            "username": 'input[placeholder="用户名 / 手机号"]',
            "password": 'input[placeholder="密码"]',
            "login_button": 'button:has-text("登录")',
            "captcha_input": 'input[placeholder="请输入验证码"]',
            "captcha_img": 'img.img-code-pic',
            "confirm_button": '.img-code-dialog .el-button--primary',
        }

    def get_defaults(self) -> dict:
        return {
            "login_url": "/ecso/login/ecso-login-static/login",
            "probe_url": "/ecso/operation/ecso-operation-admin-static/",
            "captcha_type": "arithmetic",
            "fresh_ttl_seconds": 1800,
        }

    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """ECSO 算术验证码登录（移植自 ecso.js）"""
        username_sel = config.selectors.get("username", self.get_selectors()["username"])
        password_sel = config.selectors.get("password", self.get_selectors()["password"])
        captcha_input_sel = 'input[placeholder="请输入验证码"]'

        # 拦截 unified-login 响应
        login_type_holder = [None]
        login_body_holder = ['']

        async def _on_login_resp(resp):
            try:
                if "/unified-login" in resp.url:
                    text = await resp.text()
                    login_body_holder[0] = text or ''
                    import json
                    j = json.loads(text)
                    if j and j.get("entity"):
                        login_type_holder[0] = j["entity"].get("type")
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_on_login_resp(r)))

        try:
            LOG.info("[ecso] 打开登录页")
            await page.wait_for_timeout(1000)

            # 等待表单
            try:
                await page.wait_for_selector(username_sel, timeout=15000)
            except Exception:
                LOG.warning("[ecso] 用户名输入框未出现")
                return LoginResult.FAILED

            await page.wait_for_timeout(800)

            # 填充凭据
            await page.fill(username_sel, config.username)
            await page.fill(password_sel, config.password)
            await page.wait_for_timeout(400)

            # 第 1 次点登录（触发 NEED_DIGIT_CAPTCHA）
            LOG.info("[ecso] 第 1 次点击登录（触发验证码）")
            await self._click_login(page)

            # 等待验证码输入框
            try:
                await page.wait_for_selector(captcha_input_sel, timeout=8000)
            except Exception:
                # 可能直接登录成功
                if await self._check_logged_in(page, context, quick=True):
                    LOG.info("[ecso] 登录成功（无验证码直接通过）")
                    return LoginResult.SUCCESS
                # 再点一次登录
                await self._click_login(page)
                try:
                    await page.wait_for_selector(captcha_input_sel, timeout=8000)
                except Exception:
                    LOG.warning("[ecso] 未出现验证码输入框")
                    return LoginResult.FAILED

            # 验证码求解循环（最多 8 次）
            last_solved_src = None
            locked = False
            last_msg = ''

            for attempt in range(1, 9):
                LOG.info(f"[ecso] 验证码尝试 {attempt}/8")

                # 获取验证码图片（等待加载完成）
                src = await self._get_captcha_src(page)
                if not src:
                    await self._refresh_captcha(page)
                    src = await self._get_captcha_src(page)
                    if not src:
                        continue

                # 避免提交同一张图
                if src == last_solved_src:
                    await self._refresh_captcha(page)
                    src = await self._get_captcha_src(page)
                    if not src or src == last_solved_src:
                        continue

                # OCR 识别
                b64 = src.split(',')[1] if ',' in src else src
                try:
                    from ..captcha.arithmetic import ArithmeticSolver
                    solver = ArithmeticSolver()
                    ans = solver.solve(b64)
                except ImportError:
                    LOG.error("[ecso] captcha/arithmetic 模块未实现")
                    return LoginResult.FAILED

                LOG.info(f"[ecso] 第 {attempt} 次 OCR 答案: {ans!r}")
                if not ans:
                    await self._refresh_captcha(page)
                    continue

                # 清空上次响应标记
                login_type_holder[0] = None
                login_body_holder[0] = ''

                # 填充答案到弹窗输入框
                try:
                    await page.fill(captcha_input_sel, str(ans))
                    await page.wait_for_timeout(300)
                except Exception as e:
                    LOG.warning(f"[ecso] 答案填充失败: {e}")
                    continue

                # 点弹窗的"确定"按钮（关键！）
                ok = await self._click_confirm_captcha(page)
                if not ok:
                    LOG.warning("[ecso] 确定按钮点击失败")
                    continue

                last_solved_src = src

                # 等待服务端响应
                for _ in range(25):
                    if login_type_holder[0] and login_type_holder[0] != 'NEED_DIGIT_CAPTCHA':
                        break
                    if await self._check_logged_in(page, context, quick=True):
                        break
                    await page.wait_for_timeout(200)

                # 成功判定
                if await self._check_logged_in(page, context):
                    LOG.info("[ecso] 登录成功")
                    await page.wait_for_timeout(2000)
                    return LoginResult.SUCCESS

                # 锁定检测
                lt = login_type_holder[0]
                lb = login_body_holder[0]
                if lt and lt != 'NEED_DIGIT_CAPTCHA':
                    import re
                    if re.search(r'FAIL|ERROR|错误|锁定|LOCK|REFUSE|REJECT', lt, re.IGNORECASE) or \
                       re.search(r'错误|锁定|失败|拒绝', lb):
                        locked = True
                        last_msg = f"{lt} | {lb[:120]}"
                        break

            if locked:
                LOG.error(f"[ecso] 凭据被服务端拒绝/锁定: {last_msg}")
                return LoginResult.LOCKED

            LOG.error(f"[ecso] 验证码多次未通过。最后状态: {last_msg or login_type_holder[0]}")
            return LoginResult.FAILED

        except Exception as e:
            LOG.error(f"[ecso] 登录异常: {e}")
            return LoginResult.FAILED

    async def probe(self, page, probe_url: str) -> bool:
        """探测 — 访问控制台首页，未登录会被重定向回 /login"""
        try:
            await page.wait_for_timeout(2500)
            if "/login" in page.url.lower():
                return False
            has_form = await page.evaluate(
                '() => !!document.querySelector(\'input[placeholder="用户名 / 手机号"]\')'
            )
            return not has_form
        except Exception:
            return "/login" not in page.url.lower()

    async def _click_login(self, page):
        """点击登录页的登录按钮"""
        try:
            await page.locator('button:has-text("登录")').first.click(timeout=4000)
            return
        except Exception:
            pass
        try:
            await page.evaluate("""() => {
              const b = Array.from(document.querySelectorAll('button')).find(
                x => (x.innerText || '').trim() === '登录');
              if (b) b.click();
            }""")
        except Exception:
            pass

    async def _click_confirm_captcha(self, page) -> bool:
        """点击验证码弹窗的"确定"按钮"""
        # 方法 1：.img-code-dialog .el-button--primary
        try:
            await page.locator('.img-code-dialog .el-button--primary').click(timeout=4000)
            return True
        except Exception:
            pass
        # 方法 2：弹窗内的"确定"按钮
        try:
            dlg = page.locator('.img-code-dialog')
            await dlg.locator('button:has-text("确定")').click(timeout=4000)
            return True
        except Exception:
            pass
        # 方法 3：JS 兜底
        try:
            ok = await page.evaluate("""() => {
              const d = document.querySelector('.img-code-dialog');
              const b = d && Array.from(d.querySelectorAll('button')).find(
                x => (x.innerText || '').trim() === '确定');
              if (b) { b.click(); return true; }
              return false;
            }""")
            return bool(ok)
        except Exception:
            return False

    async def _get_captcha_src(self, page):
        """等待验证码图片加载完成并获取 data URL"""
        try:
            await page.wait_for_function("""() => {
              const im = document.querySelector('img.img-code-pic');
              return im && im.complete && im.naturalWidth > 10
                && typeof im.src === 'string' && im.src.startsWith('data:image');
            }""", timeout=8000)
        except Exception:
            return None
        try:
            return await page.evaluate("""() => {
              const im = document.querySelector('img.img-code-pic');
              return im && im.src ? im.src : null;
            }""")
        except Exception:
            return None

    async def _refresh_captcha(self, page):
        """点击验证码图片刷新"""
        try:
            await page.evaluate("""() => {
              const i = document.querySelector('img.img-code-pic');
              if (i) i.click();
            }""")
            await page.wait_for_timeout(800)
        except Exception:
            pass

    async def _check_logged_in(self, page, context, quick=False) -> bool:
        """检查是否已登录"""
        url = page.url
        if "/login" not in url.lower():
            if quick:
                return True
            cookies = await context.cookies()
            if cookies:
                return True
            try:
                has_form = await page.evaluate(
                    '() => !!document.querySelector(\'input[placeholder="用户名 / 手机号"]\')'
                )
            except Exception:
                has_form = False
            return not has_form
        if not quick:
            cookies = await context.cookies()
            import re
            return any(
                re.search(r'token|session|jsession|sid|ticket|auth|sso', c["name"], re.IGNORECASE)
                and c.get("value")
                for c in cookies
            )
        return False
