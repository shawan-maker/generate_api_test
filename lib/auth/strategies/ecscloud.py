"""
ecscloud.py — EcsCloud/estack 登录策略

从 auth.py 的 login_with_browser() 抽取。
核心流程：Vue 组件注入 → 点击认证 → 滑块缺口检测 → 人类化拖拽 → 点击登录
"""

import re
import asyncio
import logging

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

        # 先尝试 Vue 注入
        vue_ok = await self._fill_via_vue(page, config.username, config.password)
        if not vue_ok:
            # 回退到 Playwright fill
            await page.fill(username_sel, config.username)
            await page.fill(password_sel, config.password)

        await page.wait_for_timeout(800)
        LOG.info("[ecscloud] 已填充凭据")

        # 点击认证按钮
        await self._click_auth_button(page, auth_text)
        await page.wait_for_timeout(2500)
        LOG.info(f"[ecscloud] 已点击认证按钮, material={'yes' if self._captcha_material else 'no'}")

        # 滑块验证（最多 4 轮）
        verify_ok = False
        for attempt in range(4):
            geo = await self.slider.read_geometry(page)
            LOG.info(f"[ecscloud] 第{attempt+1}轮: geo_ok={geo.get('ok')}, material={'yes' if self._captcha_material else 'no'}")

            if not geo.get("ok"):
                LOG.info("[ecscloud] geo 不可见, 重点击认证按钮")
                await self._click_auth_button(page, auth_text)
                await page.wait_for_timeout(2500)
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

        # 点击登录按钮
        LOG.info("[ecscloud] 点击登录按钮")
        await page.evaluate("""(t) => {
          const vis=Array.prototype.slice.call(document.querySelectorAll('button'))
            .filter(b=>{const r=b.getBoundingClientRect();return r.width>0&&r.height>0;});
          let target = vis.find(b => (b.textContent||'').trim() === t);
          if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
          if (target) target.click();
        }""", login_text)
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
        """通过 Vue 组件注入用户名密码"""
        try:
            await page.evaluate(FIND_LC_SRC)
            result = await page.evaluate(f"""() => {{
              const lc = findLC_smart();
              if (!lc || !lc.loginForm) return false;
              lc.loginForm.username = {username!r};
              lc.loginForm.password = {password!r};
              return true;
            }}""")
            return bool(result)
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
        """多策略点击认证按钮"""
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if await btn.is_visible(timeout=1500):
                await btn.click(timeout=5000, force=True)
                return True
        except Exception:
            pass
        try:
            done = await page.evaluate("""(t) => {
              const nodes=[...document.querySelectorAll('*')].filter(n=>(n.textContent||'').trim()===t && n.children.length===0);
              if(nodes[0]){nodes[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));return true;}
              const all=[...document.querySelectorAll('div,span,button,a')].filter(n=>(n.textContent||'').trim().includes(t));
              if(all[all.length-1]){all[all.length-1].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,view:window}));return true;}
              return false;
            }""", text)
            return bool(done)
        except Exception:
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
