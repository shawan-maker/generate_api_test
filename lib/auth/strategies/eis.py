"""
eis.py — EIS 边缘智能小站登录策略

核心流程：等待表单 → OCR 识别图片字符验证码 → 填充（用户名/密码/验证码）
        → 点击登录 → 监听 POST /api/auth/v1/login 响应 → 失败刷新验证码重试

2026-11-19 独立脚本调试结论（scripts/debug_eis_login.py）：
- Element UI + Vue；账号登录表单可见，CA 证书登录表单隐藏（placeholder 部分同名，
  选择器必须带 :visible）
- 图片字符验证码：.identify-code-item img，src=data:image/jpeg;base64（ddddocr 可识别）
- 密码加密（encipher=true）：由前端 JS 自动完成（GET /api/auth/v1/info/key 取 RSA 公钥 →
  POST /api/auth/v1/login?keyId=md5(...)- 提交密文）。策略只需浏览器内填表提交，
  无需自行实现加密
- 登录成功：URL 跳转离开 /page/eis/login（→ /overview），cookie 写入 token / refresh_token
- 登录失败可重试：「图片验证码不正确」（CONSOLE_BUSINESS_EXCEPTION, resultCode=201016）
- 会话为纯 cookie 型：API 请求带 cookie 即可（Authorization: Bearer 亦兼容）

探测（probe）：未登录访问受保护页（/overview）会被路由到 /page/eis/login，
以「URL 含 /login」+ 登录表单可见性判定未登录。
"""

import asyncio
import json
import logging
import re

from ..strategy_base import LoginStrategy, LoginResult, LoginConfig

LOG = logging.getLogger("strategy.eis")


class EisStrategy(LoginStrategy):
    """EIS 边缘智能小站 — 图片字符 OCR 验证码（前端自动 RSA 加密）"""

    key = "eis"
    label = "EIS 边缘智能小站"

    def get_selectors(self) -> dict:
        return {
            "username": 'input[placeholder="用户名"]:visible',
            "password": 'input[placeholder="密码"]:visible',
            "captcha_input": 'input[placeholder="验证码"]:visible',
            "captcha_img": '.identify-code-item img',
            "login_button": 'button.submit_btn.login-item-password',
        }

    def get_defaults(self) -> dict:
        return {
            "login_url": "/page/eis/login",
            "probe_url": "/overview",
            "captcha_type": "ocr",
            "fresh_ttl_seconds": 1800,
        }

    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """EIS 图片验证码登录（验证码识别失败自动刷新重试，最多 5 次）"""
        sels = self.get_selectors()
        username_sel = config.selectors.get("username", sels["username"])
        password_sel = config.selectors.get("password", sels["password"])
        captcha_input_sel = config.selectors.get("captcha_input", sels["captcha_input"])
        captcha_img_sel = config.selectors.get("captcha_img", sels["captcha_img"])
        login_btn_sel = config.selectors.get("login_button", sels["login_button"])

        # 拦截 POST /api/auth/v1/login 响应（判定失败原因）
        login_resp_holder = [None]

        async def _on_login_resp(resp):
            try:
                if re.search(r"/api/auth/v1/login", resp.url) and resp.request.method == "POST":
                    text = await resp.text()
                    login_resp_holder[0] = {
                        "status": resp.status,
                        "body": text or "",
                    }
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_on_login_resp(r)))

        try:
            LOG.info("[eis] 打开登录页，等待表单")
            try:
                await page.wait_for_selector(username_sel, timeout=15000)
                await page.wait_for_selector(password_sel, timeout=15000)
                await page.wait_for_selector(captcha_input_sel, timeout=15000)
            except Exception as e:
                LOG.warning(f"[eis] 登录表单未出现: {e}")
                return LoginResult.FAILED

            await page.wait_for_timeout(800)

            locked = False
            last_msg = ""

            for attempt in range(1, 6):
                LOG.info(f"[eis] 登录尝试 {attempt}/5")
                login_resp_holder[0] = None

                # 获取验证码图片并 OCR 识别
                src = await self._get_captcha_src(page, captcha_img_sel)
                code = None
                if src:
                    code = await asyncio.to_thread(self._solve_captcha, src)
                LOG.info(f"[eis] 第 {attempt} 次 OCR 结果: {code!r}")
                if not code:
                    await self._refresh_captcha(page, captcha_img_sel)
                    continue

                # 填充表单
                try:
                    await page.fill(username_sel, config.username)
                    await page.fill(password_sel, config.password)
                    await page.fill(captcha_input_sel, code)
                except Exception as e:
                    LOG.warning(f"[eis] 表单填充失败: {e}")
                    continue
                await page.wait_for_timeout(300)

                # 点击登录
                if not await self._click_login(page, login_btn_sel):
                    LOG.warning("[eis] 登录按钮点击失败")
                    continue

                # 等待响应 / URL 跳转（最多 10s）
                for _ in range(20):
                    await page.wait_for_timeout(500)
                    if login_resp_holder[0]:
                        break
                    if "login" not in page.url.lower():
                        break

                # 成功判定 1：URL 跳转离开登录页（最强信号）
                if "login" not in page.url.lower():
                    LOG.info(f"[eis] 登录成功，已跳转: {page.url}")
                    # 等待首页请求完成（azCode 等响应 cookie 写入）
                    await page.wait_for_timeout(2000)
                    return LoginResult.SUCCESS

                # 解析响应失败原因
                resp = login_resp_holder[0]
                msg = ""
                if resp:
                    try:
                        j = json.loads(resp["body"])
                        msg = (j.get("resultMessage") or "") or ""
                    except Exception:
                        msg = resp["body"][:120]

                if msg:
                    LOG.info(f"[eis] 登录失败响应: {msg}")
                last_msg = msg

                # 锁定 / 凭据错误 → 停止重试（重试无意义且有锁号风险）
                if re.search(r"锁定|被锁|禁止|封禁|lock", msg, re.IGNORECASE):
                    locked = True
                    LOG.error(f"[eis] 账号被锁定: {msg}")
                    break
                if re.search(r"密码错误|用户名或密码|用户名不存在|认证失败", msg, re.IGNORECASE):
                    locked = True
                    LOG.error(f"[eis] 凭据被拒绝: {msg}")
                    break

                # 其余（验证码不正确等）→ 刷新验证码重试
                await self._refresh_captcha(page, captcha_img_sel)

            if locked:
                return LoginResult.LOCKED

            LOG.error(f"[eis] 已达最大重试次数。最后错误: {last_msg or '无响应'}")
            return LoginResult.FAILED

        except Exception as e:
            LOG.error(f"[eis] 登录异常: {e}")
            return LoginResult.FAILED

    async def probe(self, page, probe_url: str) -> bool:
        """探测 — 未登录访问受保护页会被路由回 /page/eis/login"""
        try:
            await page.wait_for_timeout(2500)
            if "/login" in page.url.lower():
                return False
            # 兜底：检查登录表单是否可见（SPA 路由可能未改变 URL）
            has_form = await page.evaluate(
                """() => {
                const el = document.querySelector('input[placeholder="用户名"]');
                return !!(el && (el.offsetWidth || el.offsetHeight));
            }"""
            )
            return not has_form
        except Exception:
            return "/login" not in page.url.lower()

    # ========== 内部工具 ==========

    def _solve_captcha(self, src: str):
        """OCR 识别验证码（data URL / base64）。在 worker 线程执行避免阻塞事件循环"""
        try:
            from ..captcha.ocr import OcrSolver
            solver = OcrSolver()
            return solver.solve(src)
        except ImportError:
            LOG.error("[eis] captcha/ocr 模块不可用（ddddocr 未安装？）")
            return None
        except Exception as e:
            LOG.warning(f"[eis] OCR 异常: {e}")
            return None

    async def _get_captcha_src(self, page, captcha_img_sel) -> str | None:
        """获取当前验证码图片的 data URL（等待图片加载完成）"""
        try:
            await page.wait_for_function(
                """(sel) => {
                const im = document.querySelector(sel);
                return im && im.complete && typeof im.src === 'string'
                    && im.src.startsWith('data:image');
            }""",
                arg=captcha_img_sel,
                timeout=8000,
            )
            return await page.evaluate(
                "(sel) => { const im = document.querySelector(sel); return im ? im.src : null; }",
                arg=captcha_img_sel,
            )
        except Exception:
            # 兜底：任意 data:image 的 img
            try:
                return await page.evaluate(
                    """() => {
                    const im = document.querySelector('img[src^="data:image"]');
                    return im ? im.src : null;
                }"""
                )
            except Exception:
                return None

    async def _refresh_captcha(self, page, captcha_img_sel):
        """点击验证码图片刷新"""
        try:
            await page.evaluate(
                """(sel) => {
                const im = document.querySelector(sel);
                if (im) im.click();
            }""",
                arg=captcha_img_sel,
            )
            await page.wait_for_timeout(1000)
        except Exception:
            pass

    async def _click_login(self, page, login_btn_sel) -> bool:
        """点击登录按钮（locator 优先，JS 兜底）"""
        try:
            loc = page.locator(login_btn_sel).first
            if await loc.count() > 0:
                await loc.click(timeout=4000)
                return True
        except Exception:
            pass
        try:
            ok = await page.evaluate(
                """() => {
                const cand = Array.from(document.querySelectorAll('button')).filter(
                    b => !!(b.offsetWidth) && /^登\\s*录$/.test((b.innerText || '').trim())
                );
                if (cand.length) { cand[0].click(); return true; }
                return false;
            }"""
            )
            return bool(ok)
        except Exception:
            return False
