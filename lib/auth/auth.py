"""
鉴权与登录 —— 对应 EcsCloud 的 session.js（cookie 为唯一可信源 + 新鲜度缓存 + 服务端探活 + 失效自动重登）。

设计原则（与 EcsCloud lib/session.js 对齐）：
  * cookies.json 是「唯一可信数据源」，Playwright/ httpx 都直接用它（纯净数组）。
  * meta.json 记录 savedAt，用于「新鲜度」判断，避免无谓的探活/登录。
  * 是否登录成功以「服务端探测」为准（current-user 接口 success=true 即有效），绝不靠本地猜测。
  * 仅在服务端证伪时才调用滑块登录；登录带外层重试（滑块不稳）。
  * 真实鉴权 token = cookies.json 中的 cookie_token_key（配置在 auth_config 中）。
  * 对外同步接口 ensure_client()：cookie 有效就用；失效才自动登录并回写 cookie；调用方无需关心细节。

⚠️ 早期版本 get_client 把本地 token 恢复成占位符 "***restored***" 再去 Bearer，导致探活必失败、脚本拿不到 client。
   本版彻底修正：token 一律从 cookie 取真实值；get_client 直接委托 ensure_client（失效自动登录）。
"""
import os
import re
import json
import time
import asyncio
import base64
import httpx
from pathlib import Path
from typing import Optional

from . import slider
try:
    from module_discovery import const as _const
except ImportError:
    _const = None


class AuthSession:
    def __init__(self, profile: dict, username: str = None, password: str = None):
        self.profile = profile or {}
        self.auth_cfg = self.profile.get("auth", {})
        self.cap_cfg = self.profile.get("captcha", {})
        self.login_flow_cfg = self.profile.get("login_flow", {})
        _creds = self.profile.get("credentials", {}) or {}
        self.username = (username
                        or os.environ.get(_creds.get("username_env", ""), "")
                        or _creds.get("username")
                        or "")
        self.password = (password
                        or os.environ.get(_creds.get("password_env", ""), "")
                        or _creds.get("password")
                        or "")
        self.token = None                 # 真实 token = cookie_token_key 配置的 cookie 值
        self.token_issued_at = 0
        self._cookies = []                # cookie 列表（唯一可信源）
        self.ttl = int(self.auth_cfg.get("freshness_ttl_seconds", 1800))
        self.fixed_headers = self.auth_cfg.get("fixed_headers", {})
        self.context_path = None          # 由调用方设置：projects/<id>/output/config/context.json
        self._cookie_file = None
        self._meta_file = None
        self._log_fn = print
        # 自动登录总开关：默认开；AUTO_LOGIN=0 时关闭（并行运行器只读 cookie，避免多进程抢登）
        self.auto_login_enabled = os.environ.get("AUTO_LOGIN", "1") != "0"
        self._proj_dir = None

    @staticmethod
    def load_auth_json(auth_json_path) -> dict:
        """
        从 auth.json 读取凭据（对齐 EcsCloud session.js loadAuthConfig）。

        文件格式：
        {
            "auto_login": { "user": "xxx", "pass": "xxx" },
            "target": "dw",
            "login_url": "https://..."
        }

        Returns:
            {"username": str, "password": str, "loginUrl": str, "target": str}
        """
        if not auth_json_path or not Path(auth_json_path).exists():
            return {"username": "", "password": "", "loginUrl": "", "target": ""}
        try:
            cfg = json.loads(Path(auth_json_path).read_text(encoding="utf-8"))
            auto = cfg.get("auto_login", {})
            return {
                "username": auto.get("user", ""),
                "password": auto.get("pass", ""),
                "loginUrl": cfg.get("login_url", ""),
                "target": cfg.get("target", ""),
            }
        except Exception:
            return {"username": "", "password": "", "loginUrl": "", "target": ""}

    def resolve_credentials(self, username=None, password=None) -> dict:
        """
        凭据优先级（从高到低）：
          1. 函数参数
          2. 环境变量（AUTO_LOGIN_USER / AUTO_LOGIN_PASS）
          3. auth.json（output/config/auth.json）
          4. profile.yaml credentials

        对齐 EcsCloud session.js resolveCredentials()。
        """
        file_cfg = {}
        if self.context_path:
            auth_path = Path(self.context_path).parent / "auth.json"
            file_cfg = self.load_auth_json(auth_path)

        _creds = self.profile.get("credentials", {}) or {}
        resolved_user = (
            username
            or os.environ.get("AUTO_LOGIN_USER", "")
            or file_cfg.get("username", "")
            or os.environ.get(_creds.get("username_env", ""), "")
            or _creds.get("username", "")
        )
        resolved_pass = (
            password
            or os.environ.get("AUTO_LOGIN_PASS", "")
            or file_cfg.get("password", "")
            or os.environ.get(_creds.get("password_env", ""), "")
            or _creds.get("password", "")
        )
        return {"username": resolved_user, "password": resolved_pass}

    # ---------- 路径解析 ----------
    def set_context_path(self, path):
        """设置 context.json 路径，并推导同目录的 cookies.json / meta.json。"""
        self.context_path = path
        if path:
            p = Path(path)
            self._cookie_file = str(p.parent / "cookies.json")
            self._meta_file = str(p.parent / "meta.json")

    # ---------- 凭据 ----------
    def have_credentials(self) -> bool:
        return bool(self.username) and bool(self.password)

    def load_secrets_file(self, path):
        p = Path(path)
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            self.username = self.username or d.get("username")
            self.password = self.password or d.get("password")

    # ---------- Cookie 持久化（唯一可信源，纯净数组）----------
    def save_cookies(self, cookies: list):
        """写回 cookies.json（供 addCookies / httpx 直接使用），并刷新 meta。"""
        if not self._cookie_file:
            if self.context_path:
                self.set_context_path(self.context_path)
        if not self._cookie_file:
            return
        Path(self._cookie_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self._cookie_file).write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_meta({"savedAt": time.time(), "count": len(cookies) if isinstance(cookies, list) else 0})
        self._cookies = cookies

    def load_cookies(self) -> list:
        """读取 cookies（纯净数组，无硬编码兜底）。缺失/损坏返回 []。"""
        if not self._cookie_file or not Path(self._cookie_file).exists():
            return []
        try:
            arr = json.loads(Path(self._cookie_file).read_text(encoding="utf-8"))
            return arr if isinstance(arr, list) else []
        except Exception:
            return []

    def _extract_token(self, cookies) -> Optional[str]:
        """从 cookie 中提取 token 值，cookie_token_key 从 auth_cfg 读取。"""
        cookie_token_key = self.auth_cfg.get("cookie_token_key", "")
        token_key = self.auth_cfg.get("token_key", "")

        # 主查找：使用 profile 中配置的 cookie_token_key
        if cookie_token_key:
            for c in cookies or []:
                if c.get("name") == cookie_token_key:
                    return c.get("value")

        # 次查找：使用 token_key（localStorage key 有时与 cookie key 相同）
        if token_key:
            for c in cookies or []:
                if c.get("name") == token_key:
                    return c.get("value")

        # 兜底：常见通用 token cookie 名称
        for c in cookies or []:
            if c.get("name") in ("access_token", "Authorization", "session_id", "sid"):
                return c.get("value")
        return None

    def _read_meta(self) -> dict:
        if self._meta_file and Path(self._meta_file).exists():
            try:
                return json.loads(Path(self._meta_file).read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _write_meta(self, meta: dict):
        if not self._meta_file:
            return
        Path(self._meta_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self._meta_file).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def _touch_meta(self):
        m = self._read_meta()
        m["savedAt"] = time.time()
        self._write_meta(m)
        self.token_issued_at = m["savedAt"]

    def save_context(self, path=None, cookies: list = None):
        """兼容旧接口：保存 cookies.json + meta.json（context.json 仅留脱敏元信息）。"""
        path = path or self.context_path
        if path:
            self.set_context_path(path)
        if cookies:
            self.save_cookies(cookies)

    def load_context(self, path=None) -> bool:
        """
        从本地读取 cookie（唯一可信源）。
        返回 True 表示 cookie 文件存在（是否新鲜由 is_fresh / 探活决定）。
        token 直接取 cookie_token_key 配置的真实值（不再用占位符）。
        """
        if path:
            self.set_context_path(path)
        cookies = self.load_cookies()
        if not cookies:
            return False
        self._cookies = cookies
        self.token = self._extract_token(cookies)
        self.token_issued_at = self._read_meta().get("savedAt", 0)
        return True

    def is_fresh(self) -> bool:
        """新鲜度窗口：在 TTL 内视为可信，跳过探活。"""
        if not self._cookies:
            return False
        return (time.time() - self.token_issued_at) < self.ttl

    # ---------- 服务端探活 ----------
    def probe_online(self, base_url: str = None) -> bool:
        """
        用已有 token + cookie 发一个轻量请求到服务端，判断 token 是否真实有效。
        对应 EcsCloud session.js ensureValidAuth → 服务端探测。
        """
        if not self.token or not self._cookies:
            self._log_fn(f"[auth] 探活: 缺 token={bool(self.token)} cookies={bool(self._cookies)}")
            return False
        probe_path = self.profile.get("probe_url", "")
        if not probe_path:
            self._log_fn("[auth] 探活: profile.yaml 缺少 probe_url，跳过")
            return False
        target = (base_url or "").rstrip("/") + probe_path
        try:
            header_name = self.auth_cfg.get("header_name", "Authorization")
            header_prefix = self.auth_cfg.get("header_prefix", "Bearer ")
            probe_headers = {
                "User-Agent": "Mozilla/5.0",
                header_name: f"{header_prefix}{self.token}",
                **self.fixed_headers,
            }
            jar = httpx.Cookies()
            for c in self._cookies:
                jar.set(c.get("name", ""), c.get("value", ""),
                        domain=c.get("domain", ""), path=c.get("path", "/"))
            resp = httpx.get(target, cookies=jar, headers=probe_headers, verify=False, timeout=10)
            if resp.status_code >= 300:
                self._log_fn(f"[auth] 探活: HTTP {resp.status_code}")
                return False
            try:
                body = resp.json()
                if body.get("success") is True and not body.get("errorCode"):
                    self.token_issued_at = time.time()   # 刷新新鲜度
                    return True
                self._log_fn(f"[auth] 探活: 业务失败 success={body.get('success')} errorCode={body.get('errorCode')}")
                return False
            except Exception:
                return resp.status_code < 300
        except Exception as e:
            self._log_fn(f"[auth] 探活异常: {e}")
            return False

    # ---------- 构建 httpx.Client ----------
    def _build_client(self, base_url: str = None, api_base: str = "") -> httpx.Client:
        headers = dict(self.fixed_headers or {})
        header_name = self.auth_cfg.get("header_name", "Authorization")
        header_prefix = self.auth_cfg.get("header_prefix", "Bearer ")
        if self.token:
            headers[header_name] = f"{header_prefix}{self.token}"
        headers["Content-Type"] = "application/json"

        full_base = (base_url or "").rstrip("/")
        if api_base:
            full_base += api_base

        client = httpx.Client(base_url=full_base, headers=headers, verify=False, timeout=30)
        for c in self._cookies:
            client.cookies.set(c.get("name", ""), c.get("value", ""),
                               domain=c.get("domain", ""), path=c.get("path", "/"))
        return client

    # ---------- 核心编排：cookie 优先 → 探活 → 失效自动登录 ----------
    def ensure_client(self, base_url: str = None, api_base: str = "", launch_browser: bool = True) -> Optional[httpx.Client]:
        """
        获取带有效鉴权的 httpx.Client（同步接口，供生成脚本直接调用）。

        流程（对齐 EcsCloud ensureValidAuth）：
          1) 加载本地 cookie（唯一可信源）；新鲜度窗口内 → 直接信任返回 client
          2) 有 cookie → 服务端探活；通过 → 刷新新鲜度返回 client
          3) 缺失/失效 → 有凭据且自动登录开启 → 浏览器滑块登录，回写 cookie，重试探活
          4) 仍失败/无凭据 → 返回 None（调用方处理）

        调用方：client = sess.ensure_client(BASE_URL)；为 None 时说明需人工干预。
        """
        base_url = base_url or self.profile.get("base_url", "")
        # 1) 加载本地 cookie
        self.load_context(self.context_path)
        # 2) 新鲜度窗口内 → 直接信任，跳过探活（减少探测/登录次数）
        if self._cookies and self.is_fresh():
            return self._build_client(base_url, api_base)
        # 3) 有 cookie → 服务端探活
        if self._cookies and self.probe_online(base_url):
            self._touch_meta()
            return self._build_client(base_url, api_base)
        # 4) 缺失/失效 → 自动登录
        if not self.auto_login_enabled:
            self._log_fn("[auth] cookie 失效且 AUTO_LOGIN=0，返回 None")
            return None
        # 解析凭据（支持 auth.json）
        creds = self.resolve_credentials()
        if not creds["username"] or not creds["password"]:
            self._log_fn("[auth] cookie 失效且无可用凭据（未设置 AUTO_LOGIN_USER/AUTO_LOGIN_PASS，也未在 auth.json 中配置）")
            return None
        # 更新凭据到实例变量，供 _login_flow 使用
        self.username = creds["username"]
        self.password = creds["password"]
        if launch_browser and self._do_browser_login(base_url):
            self.load_context(self.context_path)
            if self._cookies and self.probe_online(base_url):
                self._touch_meta()
                return self._build_client(base_url, api_base)
        self._log_fn("[auth] 自动登录后仍无法建立有效会话")
        return None

    def _do_browser_login(self, base_url: str = None) -> bool:
        """同步包装：启动无头浏览器走滑块登录（失败重试）。"""
        try:
            return asyncio.run(self._login_flow(base_url))
        except Exception as e:
            self._log_fn(f"[auth] 浏览器登录异常: {e}")
            return False

    async def _login_flow(self, base_url: str = None):
        """浏览器登录全流：启动 → 滑块登录 → 回写 cookie。"""
        from playwright.async_api import async_playwright
        login_url = self.profile.get("login_url")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--ignore-certificate-errors", "--disable-web-security",
                      "--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1600, "height": 1000},
                locale="zh-CN",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = await context.new_page()
            try:
                ok = await self.login_with_browser(page, context)
                if ok and self.context_path:
                    cookies = await context.cookies()
                    self.save_cookies(cookies)
            finally:
                await browser.close()
            return ok

    # ---------- 旧接口兼容（委托 ensure_client）----------
    def get_client(self, base_url: str = None, api_base: str = "") -> Optional[httpx.Client]:
        """兼容旧调用：直接委托 ensure_client（cookie 优先，失效自动登录）。"""
        return self.ensure_client(base_url, api_base)

    def could_be_valid(self) -> bool:
        if not self._cookies:
            return False
        if (time.time() - self.token_issued_at) < self.ttl * 2:
            return True
        return False

    def headers(self) -> dict:
        h = dict(self.fixed_headers)
        if self.token:
            prefix = self.auth_cfg.get("header_prefix", "Bearer ")
            h[self.auth_cfg.get("header_name", "Authorization")] = f"{prefix}{self.token}"
        return h

    # ---------- 滑块登录(浏览器) ----------
    async def login_with_browser(self, page, context, max_retry=4):
        """
        用 Playwright page 走完整滑块登录。成功返回 True 并把 token 写入 self.token。
        captcha 素材接口会被拦截缓存到 self._captcha_material。
        """
        from playwright.async_api import TimeoutError as PWTimeout

        login_url = self.profile.get("login_url")
        auth_text = self.cap_cfg.get("auth_button_text", "点击完成认证")
        login_text = self.cap_cfg.get("login_button_text", "登录")
        success_kw = self.cap_cfg.get("success_url_keyword", "/portal")
        material_re = self.cap_cfg.get("material_url_regex", "pictures-verification")

        self._captcha_material = None

        async def _on_response_async(resp):
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

        page.on("response", lambda r: asyncio.create_task(_on_response_async(r)))

        await page.goto(login_url, wait_until="load", timeout=30000)
        await page.wait_for_timeout(4000)

        try:
            await page.evaluate("""() => {
              document.querySelectorAll('.cookie-banner,#cookie-banner,[class*=cookie-consent]').forEach(e=>e.style.display='none');
            }""")
        except Exception:
            pass

        await self._switch_to_chinese(page)

        # 登录表单选择器：优先 login_flow 配置，回退到 const 默认值
        if _const:
            default_user_sel = _const.DEFAULT_LOGIN_USERNAME_SELECTOR
            default_pass_sel = _const.DEFAULT_LOGIN_PASSWORD_SELECTOR
        else:
            default_user_sel = 'input[placeholder="用户名"]'
            default_pass_sel = 'input[placeholder="登录密码"]'
        username_selector = self.login_flow_cfg.get("username_selector", default_user_sel)
        password_selector = self.login_flow_cfg.get("password_selector", default_pass_sel)

        await page.fill(username_selector, self.username)
        await page.fill(password_selector, self.password)
        await page.wait_for_timeout(800)
        self._log_fn("[auth] 已填用户名密码")

        await self._click_auth_button(page, auth_text)
        await page.wait_for_timeout(2500)
        self._log_fn(f"[auth] 已点击认证按钮, captcha_material={'yes' if self._captcha_material else 'no'}")

        verify_ok = False
        for attempt in range(max_retry):
            geo = await slider.read_slider_geo(page)
            self._log_fn(f"[auth] 第{attempt+1}轮: geo_ok={geo.get('ok')}, material={'yes' if self._captcha_material else 'no'}")
            if not geo.get("ok"):
                self._log_fn("[auth] geo 不可见, 重点击认证按钮")
                await self._click_auth_button(page, auth_text)
                await page.wait_for_timeout(2500)
                geo = await slider.read_slider_geo(page)
                self._log_fn(f"[auth] retry geo: ok={geo.get('ok')}")
                if not geo.get("ok"):
                    await page.wait_for_timeout(800)
                    continue
            mat = self._captcha_material
            if not mat or not mat.get("backImage"):
                self._log_fn(f"[auth] 第{attempt+1}轮: 验证码素材尚未捕获 (material={'yes' if mat else 'no'})")
                await page.wait_for_timeout(800)
                continue
            try:
                gap = slider.solve_slider(mat["backImage"], mat["slidingImage"])
            except Exception as e:
                self._log_fn(f"[auth] 缺口识别异常: {e}")
                gap = {"gap_location": 0}
            if gap.get("gap_location", 0) <= 0:
                await page.wait_for_timeout(500)
                continue
            dist = slider.compute_drag_distance(geo, gap)
            self._log_fn(f"[auth] 缺口: loc={gap['gap_location']} method={gap['chosen_method']} dist={dist:.1f}")
            await slider.human_drag(page, geo["handle"], dist)
            await page.wait_for_timeout(2000)
            verify_ok = await self._poll_verify(page)
            self._log_fn(f"[auth] poll_result: {verify_ok}")
            if verify_ok:
                break
            await self._refresh_captcha(page, auth_text)
            await page.wait_for_timeout(2000)

        if not verify_ok:
            self._log_fn("[auth] ✗ 滑块验证失败")
            return False

        self._log_fn(f"[auth] 尝试点击登录按钮 ({login_text})")
        btn_info = await page.evaluate("""(t) => {
            const btns = document.querySelectorAll('button');
            const vis = Array.from(btns).filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
            return vis.map(b => ({ text: (b.textContent||'').trim(), visible: true }));
        }""", login_text)
        self._log_fn(f"[auth] 可见按钮: {[b['text'] for b in btn_info]}")
        await page.evaluate("""(t) => {
          const vis=Array.prototype.slice.call(document.querySelectorAll('button'))
            .filter(b=>{const r=b.getBoundingClientRect();return r.width>0&&r.height>0;});
          let target = vis.find(b => (b.textContent||'').trim() === t);
          if (!target) target = vis.find(b => (b.textContent||'').replace(/\\s+/g, '') === t.replace(/\\s+/g, ''));
          if (target) target.click();
        }""", login_text)
        await page.wait_for_timeout(5000)

        self._log_fn(f"[auth] 登录按钮点击后 URL: {page.url}")
        await page.wait_for_timeout(1000)

        # 从 auth_cfg 读取 token_key（localStorage key）和 cookie_token_key
        token_key = self.auth_cfg.get("token_key", "")
        cookie_token_key = self.auth_cfg.get("cookie_token_key", "")

        # 尝试从 localStorage 读取 token
        token = None
        if token_key:
            token = await page.evaluate(f"() => localStorage.getItem('{token_key}')")
        self._log_fn(f"[auth] localStorage.{token_key or 'token'}: {'✅' if token else '❌ 空(尝试从 cookie 注入)'}")

        if not token:
            try:
                cookies = await context.cookies()
                # 主查找：cookie_token_key
                t = None
                if cookie_token_key:
                    t = next((c["value"] for c in cookies if c["name"] == cookie_token_key), None)
                # 次查找：token_key
                if not t and token_key:
                    t = next((c["value"] for c in cookies if c["name"] == token_key), None)
                # 兜底：常见通用 cookie 名称
                if not t:
                    t = next((c["value"] for c in cookies if c["name"] in ("access_token", "Authorization", "session_id", "sid")), None)

                if t:
                    # 注入到 localStorage，让前端 SPA 能正常使用
                    if token_key:
                        await page.evaluate(f"() => localStorage.setItem('{token_key}', '{t}')")
                    token = t
                    self._log_fn(f"[auth] ✅ 已从 cookie 注入 token 到 localStorage.{token_key}")
                else:
                    token = None
            except Exception as e:
                self._log_fn(f"[auth] 注入 token 失败: {e}")
                token = None

        if not token:
            self._log_fn("[auth] ✗ 登录后未取到 token")
            return False

        self.token = token
        self.token_issued_at = time.time()
        try:
            cookies = await context.cookies()
            self.save_cookies(cookies)
        except Exception:
            pass
        return True

    async def _switch_to_chinese(self, page):
        """将 estack 登录页语言切换为简体中文 (Element UI el-dropdown 或 lang-style div)。"""
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
            self._log_fn(f"[auth] 语言切换: {done}")
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
                self._log_fn("[auth] ✅ 已切换到简体中文")
                await asyncio.sleep(0.8)
            else:
                self._log_fn("[auth] ⚠️ 未找到'简体中文'菜单项, 可能已是中文")
            return clicked
        except Exception as e:
            self._log_fn(f"[auth] 语言切换异常: {e}")
            return False

    async def _click_auth_button(self, page, text):
        """多策略点击: 首选 Playwright 原生 click, 兜底到文本节点 dispatch。"""
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

    async def _refresh_captcha(self, page, auth_text):
        try:
            await page.evaluate("""() => {
              const svs=[...document.querySelectorAll('#slideVerify')]; const sv=svs.find(e=>{const r=e.getBoundingClientRect();return r.width>0;});
              if(sv){const b=sv.querySelector('.slide-verify-refresh-icon,[class*=refresh]'); if(b){b.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}}
            }""")
        except Exception:
            pass
        self._captcha_material = None
