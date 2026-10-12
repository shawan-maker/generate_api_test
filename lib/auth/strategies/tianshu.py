"""
tianshu.py — 天枢运维平台登录策略

核心流程：清除旧 localStorage → 填充凭据 → OCR 字符验证码 → 填充 → 登录
移植自 login-kit 的 tianshu.js

特殊要求：
- 必须持久化 localStorage（SPA 检查 ud_token）
- 使用 fetch(img.src) 获取浏览器内验证码 blob（不是重新请求）
- 探测用轮询（hash SPA URL 不变，检查 DOM 状态）
"""

import re
import asyncio
import logging

from ..strategy_base import LoginStrategy, LoginResult, LoginConfig

LOG = logging.getLogger("strategy.tianshu")


class TianshuStrategy(LoginStrategy):
    """天枢运维平台 — 字符 OCR 验证码"""

    key = "tianshu"
    label = "天枢运维平台"

    def get_selectors(self) -> dict:
        return {
            "username": 'input[placeholder="请输入用户名"]',
            "password": 'input[type="password"]',
            "captcha_input": 'input[name="captcha"]',
            "captcha_img": 'img.verifycode',
        }

    def get_defaults(self) -> dict:
        return {
            "login_url": "/#/login",
            "probe_url": "/#/",
            "captcha_type": "ocr",
            "fresh_ttl_seconds": 1800,
        }

    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """天枢字符验证码登录（移植自 tianshu.js）"""
        username_sel = config.selectors.get("username", self.get_selectors()["username"])
        password_sel = config.selectors.get("password", self.get_selectors()["password"])
        captcha_input_sel = config.selectors.get("captcha_input", self.get_selectors()["captcha_input"])

        # 拦截 /auth/login 响应
        login_response_holder = [None]

        async def _on_login_resp(resp):
            try:
                if re.search(r'/auth/login($|\?)', resp.url) and resp.request.method == 'POST':
                    text = await resp.text()
                    login_response_holder[0] = {
                        'status': resp.status,
                        'body': text,
                        'url': resp.url,
                    }
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_on_login_resp(r)))

        try:
            LOG.info("[tianshu] 打开登录页")
            await page.wait_for_timeout(1000)

            # 等待表单元素
            try:
                await page.wait_for_selector(username_sel, timeout=15000)
                await page.wait_for_selector(password_sel, timeout=15000)
                await page.wait_for_selector('img.verifycode', timeout=15000)
            except Exception as e:
                LOG.warning(f"[tianshu] 登录表单未出现: {e}")
                return LoginResult.FAILED

            await page.wait_for_timeout(500)

            # 清除旧 localStorage（防止旧 token 自动跳转）
            try:
                await page.evaluate("() => { try { localStorage.clear(); } catch (e) {} }")
            except Exception:
                pass

            # 验证码求解循环（最多 5 次）
            locked = False
            last_msg = ''

            for attempt in range(1, 6):
                LOG.info(f"[tianshu] 登录尝试 {attempt}/5")
                login_response_holder[0] = None

                # 获取验证码
                try:
                    b64 = await self._get_captcha_bytes(page)
                except Exception as e:
                    LOG.warning(f"[tianshu] 验证码获取失败: {e}")
                    b64 = None

                code = ''
                if b64:
                    try:
                        import base64
                        img_bytes = base64.b64decode(b64)
                        from ..captcha.ocr import OcrSolver
                        solver = OcrSolver()
                        code = solver.solve(img_bytes)
                    except ImportError:
                        LOG.error("[tianshu] captcha/ocr 模块未实现")
                        return LoginResult.FAILED
                    except Exception as e:
                        LOG.warning(f"[tianshu] OCR 异常: {e}")

                LOG.info(f"[tianshu] 第 {attempt} 次 OCR 结果: {code!r}")
                if not code:
                    await self._refresh_captcha(page)
                    continue

                # 填充表单
                await page.fill(username_sel, config.username)
                await page.fill(password_sel, config.password)
                try:
                    await page.fill(captcha_input_sel, code)
                except Exception:
                    # 回退：找验证码旁边的输入框
                    try:
                        await page.fill('input[name="captcha"]', code)
                    except Exception:
                        LOG.warning("[tianshu] 验证码输入框填充失败")
                        continue

                await page.wait_for_timeout(300)

                # 点击登录
                await self._click_login(page)

                # 等待响应
                for _ in range(30):
                    if login_response_holder[0]:
                        break
                    await page.wait_for_timeout(200)

                resp = login_response_holder[0]
                if not resp:
                    LOG.warning("[tianshu] 未收到登录响应，刷新验证码重试")
                    await self._refresh_captcha(page)
                    continue

                # 解析响应
                parsed = None
                try:
                    import json
                    parsed = json.loads(resp['body'])
                except Exception:
                    pass

                last_msg = ''
                if parsed:
                    last_msg = parsed.get('message', '') or parsed.get('error', '')
                else:
                    last_msg = resp['body']

                if parsed and parsed.get('success'):
                    LOG.info(f"[tianshu] 登录成功! message={last_msg}")
                    await page.wait_for_timeout(2000)
                    return LoginResult.SUCCESS
                elif re.search(r'错误的用户名或密码|账号.{0,4}锁定|已被锁定|locked', last_msg or '', re.IGNORECASE):
                    locked = True
                    LOG.error(f"[tianshu] 服务端判定锁定: {last_msg}")
                    break
                else:
                    LOG.info(f"[tianshu] 登录失败: {last_msg}，刷新验证码重试")
                    await self._refresh_captcha(page)

            if locked:
                return LoginResult.LOCKED

            LOG.error(f"[tianshu] 已达最大重试次数。最后错误: {last_msg}")
            return LoginResult.FAILED

        except Exception as e:
            LOG.error(f"[tianshu] 登录异常: {e}")
            return LoginResult.FAILED

    async def probe(self, page, probe_url: str) -> bool:
        """探测 — hash SPA 不能只看 URL，检查 DOM 状态（轮询 12s）"""
        try:
            await page.wait_for_timeout(1000)
            import time
            deadline = time.time() + 12
            while time.time() < deadline:
                try:
                    st = await page.evaluate("""() => ({
                        onLogin: !!document.querySelector('img.verifycode') ||
                                 !!document.querySelector('input[placeholder="请输入用户名"]'),
                        url: location.href,
                    })""")
                except Exception:
                    st = {'onLogin': True, 'url': ''}

                if not st.get('onLogin') and '#/login' not in st.get('url', '').lower():
                    return True
                await page.wait_for_timeout(1000)
            return False
        except Exception:
            return False

    async def _get_captcha_bytes(self, page) -> str:
        """通过 fetch(img.src) 获取验证码图片的 base64 编码"""
        return await page.evaluate("""async () => {
            const img = document.querySelector('img.verifycode');
            if (!img) throw new Error('captcha img not found');
            const r = await fetch(img.src);
            const buf = await r.arrayBuffer();
            const u8 = new Uint8Array(buf);
            let bin = '';
            for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
            return btoa(bin);
        }""")

    async def _refresh_captcha(self, page):
        """点击验证码图片刷新"""
        try:
            await page.evaluate("""() => {
                const i = document.querySelector('img.verifycode');
                if (i) i.click();
            }""")
            await page.wait_for_timeout(1200)
        except Exception:
            pass

    async def _click_login(self, page):
        """点击登录按钮（JS 兜底）"""
        return await page.evaluate("""() => {
            const b = Array.from(document.querySelectorAll('button')).find(
                x => (x.innerText || '').replace(/\\s+/g, '').trim() === '登录'
            );
            if (b) { b.click(); return true; }
            return false;
        }""")
