"""
ecscloud.py — EcsCloud/estack 登录策略

从 auth.py 的 login_with_browser() 抽取。
核心流程：Vue 组件注入 → 点击认证 → 滑块缺口检测 → 人类化拖拽 → 点击登录
"""

import re
import asyncio
import logging
import time
import json

from ..strategy_base import LoginStrategy, LoginResult, LoginConfig
from ..captcha.slider import SliderSolver

LOG = logging.getLogger("strategy.ecscloud")

# ========== Vue 组件注入 JS ==========
# 从 auth.py 的 login_with_browser 抽取：通过 Vue 实例设置用户名密码
FIND_LC_SRC = r"""
function findLC_smart() {
  const all = Array.prototype.slice.call(document.querySelectorAll('.zong-login-page,.login-page,[class*=login-container],[class*=login-wrap],.el-card'));
  for (const root of all) {
    const stack = [root];
    while (stack.length) {
      const cur = stack.shift();
      if (!cur) continue;
      const vm = cur.__vue__;
      if (vm && vm.loginForm && typeof vm.loginForm === 'object') return vm;
      if (cur.children) for (const c of cur.children) stack.push(c);
    }
  }
  return null;
}
"""


class EcsCloudStrategy(LoginStrategy):
    """EcsCloud/estack 登录策略 — 滑块验证码"""

    key = "ecscloud"
    label = "EcsCloud 移动云控制台"

    def __init__(self):
        self.slider = SliderSolver()
        self._captcha_material = None

    def get_selectors(self) -> dict:
        return {
            "username": 'input[placeholder="用户名"]',
            "password": 'input[placeholder="登录密码"]',
            "login_button": 'button:has-text("登录")',
            "auth_button": 'button:has-text("点击完成认证")',
        }

    def get_defaults(self) -> dict:
        return {
            "login_url": "/estack/web/estack/login",
            "probe_url": "/estack/web/estack/portal",
            "captcha_type": "slider",
            "material_url_regex": "pictures-verification",
            "auth_button_text": "点击完成认证",
            "success_url_keyword": "/portal",
            "fresh_ttl_seconds": 1800,
        }

    async def login(self, page, context, config: LoginConfig) -> LoginResult:
        """EcsCloud 滑块登录完整流程"""

        auth_text = config.auth_button_text or "点击完成认证"
        login_text = "登录"
        material_re = config.material_url_regex or "pictures-verification"

        # 重置 captcha 素材
        self._captcha_material = None

        # 拦截 captcha 素材响应
        async def _on_response(resp):
            try:
                if self._captcha_material:
                    return
                matched = bool(material_re and re.search(material_re, resp.url))
                j = None
                if matched:
                    try:
                        j = await resp.json()
                    except Exception:
                        j = None
                else:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            j = await resp.json()
                    except Exception:
                        j = None
                if j and isinstance(j.get("entity"), dict) and j["entity"].get("backImage"):
                    self._captcha_material = j["entity"]
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(_on_response(r)))

        # 等待页面加载
        await page.wait_for_timeout(3000)

        # 关闭 cookie 横幅
        try:
            await page.evaluate("""() => {
              document.querySelectorAll('.cookie-banner,#cookie-banner,[class*=cookie-consent]').forEach(e=>e.style.display='none');
            }""")
        except Exception:
            pass

        # 切换语言为中文
        await self._switch_to_chinese(page)

        # 填充用户名密码（通过 Vue 组件注入）
        username_sel = config.selectors.get("username", self.get_selectors()["username"])
        password_sel = config.selectors.get("password", self.get_selectors()["password"])

        # 使用 Playwright fill 输入（触发所有原生输入事件，确保 Vue 表单验证正常工作）
        try:
            await page.fill(username_sel, config.username)
            await page.fill(password_sel, config.password)
            await page.wait_for_timeout(500)
            LOG.info("[ecscloud] 已填充凭据")
        except Exception as e:
            LOG.warning(f"[ecscloud] Playwright fill 失败，尝试 Vue 注入: {e}")
            vue_ok = await self._fill_via_vue(page, config.username, config.password)
            if not vue_ok:
                # 双重保障：Vue 注入后再用 fill
                await page.fill(username_sel, config.username)
                await page.fill(password_sel, config.password)
            await page.wait_for_timeout(800)
            LOG.info("[ecscloud] 已填充凭据 (Vue注入)")

        # ★ 判断登录模式：检查是否有"点击完成认证"按钮
        has_auth_button = await page.evaluate("""(authText) => {
          const buttons = Array.from(document.querySelectorAll('button'));
          return buttons.some(b => {
            const r = b.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (b.textContent||'').trim() === authText;
          });
        }""", auth_text)

        if has_auth_button:
            # 旧模式：先点"点击完成认证" → 等待滑块出现 → 滑块验证 → 点"登录"
            LOG.info(f"[ecscloud] 登录模式: 旧版 (有认证按钮 '{auth_text}')")

            # 点击前截图
            try:
                await page.screenshot(path="debug_before_click_auth.png", full_page=True)
                LOG.info("[ecscloud] 点击前截图已保存: debug_before_click_auth.png")
            except Exception as e:
                LOG.warning(f"[ecscloud] 点击前截图失败: {e}")

            # 监听网络请求
            network_requests = []
            page.on("request", lambda req: network_requests.append({
                "url": req.url,
                "method": req.method,
                "timestamp": time.time()
            }))

            click_ok = await self._click_auth_button(page, auth_text)
            LOG.info(f"[ecscloud] 认证按钮点击结果: {click_ok}")

            # 等待滑块组件出现（最多5秒）
            slider_appeared = await self._wait_for_slider(page, timeout_ms=5000)
            if not slider_appeared:
                LOG.warning("[ecscloud] 首次点击后滑块未出现，检查页面状态...")

                # 详细检查页面状态
                page_state = await page.evaluate("""() => {
                    const url = window.location.href;
                    const allButtons = Array.from(document.querySelectorAll('button'));
                    const visibleButtons = allButtons.filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    }).map(b => ({
                        text: b.textContent.trim(),
                        classes: b.className,
                        id: b.id
                    })).slice(0, 10);

                    // 检查是否有错误提示
                    const errors = Array.from(document.querySelectorAll('.el-message, .el-notification, .error, .alert, [class*="error"], [class*="alert"]'))
                        .filter(e => e.offsetWidth > 0 || e.offsetHeight > 0)
                        .map(e => ({
                            text: e.textContent.trim().substring(0, 100),
                            classes: e.className
                        }));

                    // 检查是否有弹框
                    const modals = Array.from(document.querySelectorAll('.el-dialog, .el-dialog__wrapper, .modal, [class*="modal"], [class*="dialog"]'))
                        .filter(e => e.offsetWidth > 0 || e.offsetHeight > 0)
                        .map(e => ({
                            text: e.textContent.trim().substring(0, 100),
                            classes: e.className,
                            id: e.id
                        }));

                    // 检查验证码相关元素（即使不可见）
                    const verifyElements = Array.from(document.querySelectorAll('[class*="verify"], [class*="slider"], [class*="captcha"], #slideVerify, .double-slide-verify-container'))
                        .map(e => ({
                            tag: e.tagName,
                            classes: e.className,
                            id: e.id,
                            visible: e.offsetWidth > 0 || e.offsetHeight > 0,
                            rect: e.getBoundingClientRect()
                        }));

                    return {
                        url,
                        visibleButtons,
                        errors,
                        hasModals: modals.length > 0,
                        modalCount: modals.length,
                        modals,
                        verifyElements: verifyElements.slice(0, 20)
                    };
                }""")
                LOG.info(f"[ecscloud] 页面状态: {json.dumps(page_state, indent=2, ensure_ascii=False)}")
                LOG.info(f"[ecscloud] 点击后网络请求数: {len(network_requests)}")
                if network_requests:
                    for req in network_requests[-10:]:  # 只显示最后10个请求
                        LOG.info(f"  - {req['method']} {req['url']}")

                # 点击后截图
                try:
                    await page.screenshot(path="debug_after_click_auth.png", full_page=True)
                    LOG.info("[ecscloud] 点击后截图已保存: debug_after_click_auth.png")
                except Exception as e:
                    LOG.warning(f"[ecscloud] 点击后截图失败: {e}")

                click_ok = await self._click_auth_button(page, auth_text)
                LOG.info(f"[ecscloud] 重试点击结果: {click_ok}")
                slider_appeared = await self._wait_for_slider(page, timeout_ms=5000)

            LOG.info(f"[ecscloud] 已点击认证按钮, slider_appeared={slider_appeared}, material={'yes' if self._captcha_material else 'no'}")
            trigger_text = auth_text
        else:
            # 新模式：先点"登录" → 滑块验证 → 自动登录
            LOG.info(f"[ecscloud] 登录模式: 新版 (无认证按钮，直接点 '{login_text}')")
            await page.evaluate("""(t) => {
              function isVisible(el) {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden';
              }
              const vis = Array.from(document.querySelectorAll('button')).filter(isVisible);
              let target = vis.find(b => (b.textContent||'').trim() === t);
              if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
              if (target) target.click();
            }""", login_text)
            await page.wait_for_timeout(2000)
            LOG.info(f"[ecscloud] 已点击登录按钮, material={'yes' if self._captcha_material else 'no'}")
            trigger_text = login_text

        # 滑块验证（最多 4 轮）— 两种模式共用
        verify_ok = False
        for attempt in range(4):
            geo = await self.slider.read_geometry(page)
            LOG.info(f"[ecscloud] 第{attempt+1}轮: geo_ok={geo.get('ok')}, reason={geo.get('reason', 'ok')}, "
                     f"total={geo.get('total', '?')}, material={'yes' if self._captcha_material else 'no'}")

            if not geo.get("ok"):
                # 如果是首次尝试，等待更久让滑块渲染（headless 模式可能需要额外时间）
                if attempt == 0:
                    LOG.info("[ecscloud] 首次未检测到滑块，额外等待 3s...")
                    await page.wait_for_timeout(3000)
                    geo = await self.slider.read_geometry(page)
                    if geo.get("ok"):
                        LOG.info(f"[ecscloud] 等待后检测到滑块: dispW={geo.get('dispW')}")
                    else:
                        LOG.info(f"[ecscloud] geo 仍不可见 (reason={geo.get('reason')}), 重点击触发按钮")
                        if has_auth_button:
                            await self._click_auth_button(page, auth_text)
                        else:
                            await page.evaluate("""(t) => {
                              function isVisible(el) {
                                if (!el) return false;
                                const rect = el.getBoundingClientRect();
                                if (rect.width === 0 || rect.height === 0) return false;
                                const style = window.getComputedStyle(el);
                                return style.display !== 'none' && style.visibility !== 'hidden';
                              }
                              const vis = Array.from(document.querySelectorAll('button')).filter(isVisible);
                              let target = vis.find(b => (b.textContent||'').trim() === t);
                              if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
                              if (target) target.click();
                            }""", login_text)
                        await page.wait_for_timeout(4000)  # 更长的等待时间
                        geo = await self.slider.read_geometry(page)
                        if not geo.get("ok"):
                            continue
                else:
                    LOG.info(f"[ecscloud] geo 不可见 (reason={geo.get('reason')}), 重点击触发按钮")
                    if has_auth_button:
                        await self._click_auth_button(page, auth_text)
                    else:
                        await page.evaluate("""(t) => {
                          function isVisible(el) {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) return false;
                            const style = window.getComputedStyle(el);
                            return style.display !== 'none' && style.visibility !== 'hidden';
                          }
                          const vis = Array.from(document.querySelectorAll('button')).filter(isVisible);
                          let target = vis.find(b => (b.textContent||'').trim() === t);
                          if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
                          if (target) target.click();
                        }""", login_text)
                    await page.wait_for_timeout(4000)
                    geo = await self.slider.read_geometry(page)
                    if not geo.get("ok"):
                        await page.wait_for_timeout(800)
                        continue

            mat = self._captcha_material
            if not mat or not mat.get("backImage"):
                LOG.info(f"[ecscloud] 第{attempt+1}轮: 素材未捕获")
                await page.wait_for_timeout(800)
                continue

            try:
                gap = self.slider.solve(mat["backImage"], mat["slidingImage"])
            except Exception as e:
                LOG.error(f"[ecscloud] 缺口识别异常: {e}")
                gap = {"gap_location": 0}

            if gap.get("gap_location", 0) <= 0:
                await page.wait_for_timeout(500)
                continue

            dist = self.slider.compute_drag_distance(geo, gap)
            LOG.info(f"[ecscloud] 缺口: loc={gap['gap_location']} method={gap.get('chosen_method','?')} dist={dist:.1f}")

            await self.slider.human_drag(page, geo["handle"], dist)
            await page.wait_for_timeout(2000)

            verify_ok = await self._poll_verify(page)
            LOG.info(f"[ecscloud] 验证结果: {verify_ok}")
            if verify_ok:
                break

            # 刷新验证码重试
            await self._refresh_captcha(page)
            await page.wait_for_timeout(2000)

        if not verify_ok:
            LOG.error("[ecscloud] 滑块验证失败")
            return LoginResult.FAILED

        # 旧模式：滑块验证通过后还需点击"登录"按钮
        if has_auth_button:
            LOG.info("[ecscloud] 旧模式: 滑块验证通过，点击登录按钮")
            await page.evaluate("""(t) => {
              function isVisible(el) {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return false;
                const style = window.getComputedStyle(el);
                return style.display !== 'none' && style.visibility !== 'hidden';
              }
              const vis = Array.from(document.querySelectorAll('button')).filter(isVisible);
              let target = vis.find(b => (b.textContent||'').trim() === t);
              if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
              if (target) target.click();
            }""", login_text)
            await page.wait_for_timeout(5000)
        else:
            # 新模式：滑块通过后自动登录，等待页面跳转
            await page.wait_for_timeout(5000)

        # 关闭"密码提醒"对话框（如果有）
        try:
            await page.evaluate("""() => {
              const btns = document.querySelectorAll('.el-message-box__btns button');
              for (const b of btns) {
                if ((b.textContent||'').trim() === '取消' || (b.textContent||'').trim() === '我知道了') {
                  b.click(); return;
                }
              }
            }""")
        except Exception:
            pass

        # 处理 token：从 localStorage 读取或从 cookie 注入
        token_key = config.token_key or "estackToken"
        cookie_token_key = config.cookie_token_key or "accessToken"

        token = None
        if token_key:
            token = await page.evaluate(f"() => localStorage.getItem('{token_key}')")

        if not token:
            try:
                cookies = await context.cookies()
                t = None
                if cookie_token_key:
                    t = next((c["value"] for c in cookies if c["name"] == cookie_token_key), None)
                if not t and token_key:
                    t = next((c["value"] for c in cookies if c["name"] == token_key), None)
                if not t:
                    t = next((c["value"] for c in cookies if c["name"] in ("access_token", "Authorization", "session_id", "sid")), None)
                if t and token_key:
                    await page.evaluate(f"() => localStorage.setItem('{token_key}', '{t}')")
                    token = t
                    LOG.info(f"[ecscloud] 已从 cookie 注入 token 到 localStorage.{token_key}")
            except Exception as e:
                LOG.warning(f"[ecscloud] token 注入失败: {e}")

        if not token:
            LOG.warning("[ecscloud] 登录后未取到 token")

        # 验证 URL 是否包含 /portal
        current_url = page.url
        if "/portal" in current_url:
            LOG.info("[ecscloud] 登录成功（URL 包含 /portal）")
            return LoginResult.SUCCESS
        elif "/login" not in current_url:
            LOG.info(f"[ecscloud] 登录成功（URL 已离开登录页: {current_url}）")
            return LoginResult.SUCCESS
        else:
            LOG.warning(f"[ecscloud] 登录可能失败（URL 仍在登录页: {current_url}）")
            return LoginResult.FAILED

    async def probe(self, page, probe_url: str) -> bool:
        """探测会话是否有效 — 轮询检测 SPA 重定向（最多 8 秒）

        SPA 认证检查是异步的：前端 JS 发 API → 判断 token 有效 → 无效则跳转 /login。
        单次检查可能在 SPA 完成前就返回，导致误判 cookie 有效。
        轮询 8 秒给 SPA 足够时间完成认证 + 重定向。
        """
        import time
        try:
            deadline = time.time() + 8
            while time.time() < deadline:
                current_url = page.url
                if "/login" in current_url:
                    return False
                # 检查是否有登录表单元素（双重验证：URL + DOM）
                has_login = await page.evaluate("""() => {
                  return !!(document.querySelector('input[placeholder="用户名"]')
                    || document.querySelector('.zong-login-page')
                    || document.querySelector('.login-page')
                    || document.querySelector('input[placeholder="登录密码"]'));
                }""")
                if has_login:
                    return False
                # 如果 URL 已离开 probe_url 且不是登录页，说明 SPA 已正常跳转
                if probe_url not in current_url and "/login" not in current_url:
                    return True
                await page.wait_for_timeout(1000)
            # 超时：最终检查
            return "/login" not in page.url
        except Exception:
            return "/login" not in page.url

    async def _fill_via_vue(self, page, username: str, password: str) -> bool:
        """通过 Vue 组件注入用户名密码，并触发响应式更新"""
        try:
            await page.evaluate(FIND_LC_SRC)
            result = await page.evaluate(f"""(username, password) => {{
              const lc = findLC_smart();
              if (!lc || !lc.loginForm) return {{success: false, error: 'no_vm'}};

              // 尝试多个可能的字段名
              const userKeys = ['username', 'userName', 'user', 'loginName', 'account'];
              const passKeys = ['password', 'passwd', 'pass'];

              let userKey = null, passKey = null;
              for (const key of userKeys) {{
                if (key in lc.loginForm) {{ userKey = key; break; }}
              }}
              for (const key of passKeys) {{
                if (key in lc.loginForm) {{ passKey = key; break; }}
              }}

              if (!userKey || !passKey) {{
                return {{success: false, error: 'no_fields', keys: Object.keys(lc.loginForm)}};
              }}

              // 使用 Vue.$set 触发响应式更新
              const Vue = window.Vue || lc.constructor;
              if (Vue && Vue.set) {{
                Vue.set(lc.loginForm, userKey, username);
                Vue.set(lc.loginForm, passKey, password);
              }} else {{
                lc.loginForm[userKey] = username;
                lc.loginForm[passKey] = password;
              }}

              // 触发 input 事件
              const inputs = document.querySelectorAll('input[placeholder*="用户名"], input[placeholder*="密码"], input[type="text"], input[type="password"]');
              inputs.forEach(input => {{
                const event = new Event('input', {{ bubbles: true }});
                input.dispatchEvent(event);
                const changeEvent = new Event('change', {{ bubbles: true }});
                input.dispatchEvent(changeEvent);
              }});

              // 强制更新 Vue 组件
              if (lc.$forceUpdate) {{
                lc.$forceUpdate();
              }}

              // ★ 等待 Vue 渲染完成（关键步骤）
              return new Promise((resolve) => {{
                if (lc.$nextTick) {{
                  lc.$nextTick(() => {{
                    resolve({{success: true, userKey, passKey}});
                  }});
                }} else {{
                  setTimeout(() => {{
                    resolve({{success: true, userKey, passKey}});
                  }}, 100);
                }}
              }});
            }}""", username, password)
            LOG.info(f"[ecscloud] Vue 注入结果: {result}")
            return result.get('success', False) if isinstance(result, dict) else bool(result)
        except Exception as e:
            LOG.debug(f"[ecscloud] Vue 注入失败: {e}")
            return False

    async def _switch_to_chinese(self, page):
        """将 estack 登录页语言切换为简体中文"""
        try:
            done = await page.evaluate("""() => {
                const triggers = document.querySelectorAll('.lang-style');
                for (const el of triggers) {
                    if (el.offsetWidth > 0) {
                        el.click();
                        return 'clicked_lang_style';
                    }
                }
                const all = document.querySelectorAll('span, div');
                for (const el of all) {
                    if (el.offsetWidth > 0 && (el.textContent||'').trim() === 'English') {
                        el.click();
                        return 'clicked_English_span';
                    }
                }
                return 'no_trigger';
            }""")
            await asyncio.sleep(1.5)

            clicked = await page.evaluate("""() => {
                const items = document.querySelectorAll('.el-dropdown-menu li, .lang-drop-item li');
                for (const item of items) {
                    if ((item.textContent||'').trim() === '简体中文') {
                        item.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                await asyncio.sleep(0.8)
            return clicked
        except Exception:
            return False

    async def _click_auth_button(self, page, text):
        """点击认证按钮 — 优先找可见按钮的坐标，用 mouse.click"""
        # 策略1（最可靠）：JavaScript 找可见按钮的坐标，用 Playwright mouse.click
        try:
            box = await page.evaluate("""(text) => {
                const buttons = Array.from(document.querySelectorAll('button'));
                // 只选可见的按钮（rect 宽度和高度都大于0）
                for (const btn of buttons) {
                    if (btn.textContent.includes(text)) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                        }
                    }
                }
                return null;
            }""", text)

            if box and box['width'] > 0 and box['height'] > 0:
                LOG.info(f"[ecscloud] JS获取可见按钮位置: {box}")
                await page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                await asyncio.sleep(1)
                return True
            else:
                LOG.warning(f"[ecscloud] 未找到可见按钮或按钮尺寸为0: {box}")
        except Exception as e:
            LOG.warning(f"[ecscloud] 策略1 JS+mouse.click 失败: {e}")

        # 策略2（备选）：JavaScript 直接调用 visible button 的 click()
        try:
            done = await page.evaluate("""(text) => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons) {
                    if (btn.textContent.includes(text)) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            }""", text)
            if done:
                LOG.info("[ecscloud] 策略2 JS btn.click() 成功")
                await asyncio.sleep(1)
                return True
            else:
                LOG.warning("[ecscloud] 策略2未找到可见按钮")
        except Exception as e:
            LOG.warning(f"[ecscloud] 策略2点击失败: {e}")

        return False

    async def _wait_for_slider(self, page, timeout_ms=5000):
        """等待滑块验证码组件出现"""
        import time
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            # 检查两种验证码容器
            result = await page.evaluate("""() => {
                const slideVerify = document.querySelectorAll('#slideVerify');
                const doubleSlide = document.querySelectorAll('.double-slide-verify-container');
                const visible = (els) => Array.from(els).some(e => {
                    const r = e.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                return {
                    slideVerify_count: slideVerify.length,
                    slideVerify_visible: visible(slideVerify),
                    double_container_count: doubleSlide.length,
                    double_container_visible: visible(doubleSlide),
                    any_visible: visible(slideVerify) || visible(doubleSlide)
                };
            }""")
            if result['any_visible']:
                LOG.info(f"[ecscloud] 滑块组件已出现: {result}")
                return True
            await asyncio.sleep(0.5)
        LOG.warning(f"[ecscloud] 滑块组件等待超时 ({timeout_ms}ms)")
        return False

    async def _poll_verify(self, page, max_ms=6000):
        """轮询滑块验证结果"""
        import time
        deadline = time.time() + max_ms / 1000.0
        while time.time() < deadline:
            r = await page.evaluate("""() => {
              const svs=[...document.querySelectorAll('#slideVerify')];
              const txt=document.body.innerText||'';
              const st=svs.map(e=>{const v=e.__vue__;return v?{s:!!v.containerSuccess,f:!!v.containerFail}:{s:false,f:false};});
              if(st.some(x=>x.s)) return {ok:true,reason:'containerSuccess'};
              if(/校验成功|验证成功|通过验证|认证成功/.test(txt)) return {ok:true,reason:'text_success'};
              if(svs.length===0) return {ok:true,reason:'slider_destroyed'};
              if(st.some(x=>x.f)||/验证失败|校验失败/.test(txt)) return {ok:false,reason:'fail_toast'};
              return {ok:false,reason:'pending'};
            }""")
            if r.get("ok") or r.get("reason") == "fail_toast":
                return r.get("ok", False)
            await asyncio.sleep(0.2)
        return False

    async def _refresh_captcha(self, page):
        """刷新滑块验证码"""
        try:
            await page.evaluate("""() => {
              const svs=[...document.querySelectorAll('#slideVerify')]; const sv=svs.find(e=>{const r=e.getBoundingClientRect();return r.width>0;});
              if(sv){const b=sv.querySelector('.slide-verify-refresh-icon,[class*=refresh]'); if(b){b.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}}
            }""")
        except Exception:
            pass
        self._captcha_material = None
