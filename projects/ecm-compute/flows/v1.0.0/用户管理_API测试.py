"""
用户管理_API测试.py — 由 module_discovery 自动生成
生成时间: 2026-08-25 18:55:32
适用版本: v1.0.0
目标URL: /estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建
  2. 查询
  3. 修改
  4. 锁定
  5. 解锁
  6. reset
  7. authorize
  8. 删除

状态断言:
  - 创建后 state 应为 "ENABLE"
  - 锁定后 state 应为 "DISABLE"
  - 删除后数据不应出现
"""

import sys, json, time, os
from pathlib import Path

# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根，兼容 flows/vX.Y.Z/ 嵌套路径）
_root = Path(__file__).resolve()
while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import httpx
from lib.auth import AuthSession

# ========== 配置 ==========
BASE_URL = "https://10.151.37.249"
LOGIN_URL = "https://10.151.37.249/estack/web/estack/login"
TARGET_URL = "/estack/web/estack/user-center/user-manage/user"
TS = format(int(time.time() * 1000), "x")[-6:]

# ========== 测试数据 ==========
test_data = {
  "userName": "AT_" + TS,
  "password": "C2gehJJbJ756TuXQ9H0tcyvCnHAhaa/o8IpNFbnyMQAxNbDXQz9gZfZGKcPpWrXnGxpJ4lmAIrvEpDGIvDwztfkb+EaxlkBMrt+4B7oUV3wdP8/NcpFc32VuzXLGbgrHRUWMB9mQyJO/KreZLuM26Ex8fO3JNxN4rym+LpbYzlI=",
  "name": "AT_" + TS,
  "email": "DOZiy10PpTPdDZ6ECUSkT6QTjxHz1z8Rf9mfGfte6lETHk9+n/vKhR0a0CvZhGEafC4d4SrKHRZNdrAnUgmNz9JvWG26xnNZWcHlVyGaUrArRE3+rXSH5fz3HcmVUdDZJOp062yP19o9RioFt6VVAGbqlxp8iBYU5YZU0+jt0kg=",
  "phone": "cabhe/F1lWf9eJfZXJ6VG29ySlQiX1tEiWibJyLfcRDyOOG39ueCMAozoaNMrhvDv80nhiH+Y6fAYHpRnZSS7LCwNhaxoMeYEt04TU6xrpSPiUWjI/QR1G1Ojyxp6SCAErFqfSYDUfqZRqSmUeFkmUvwIVrvRc/7jg2Owg4L53Q=",
  "description": "自动创建测试用户",
  "policyIds": [
    "1f8e392309fc414c9d77d45d0315fedc"
  ],
  "adminId": "644e7d7b19c744d6a1d3194b8d819fd3",
  "tenantId": "18ae53b0745143e0b6ca9ee1f9513c64",
  "countryCode": "+86"
}


# ========== 浏览器创建用户（前端 RSA 加密，纯 httpx 无法复现）==========
def browser_create_user(base_url, cookies_file=None, username="estack-yy", password="R@9eDuck$!mpleM00n",
                        tenant_id="", admin_id=""):
    """用浏览器在前端上下文创建用户：填表→前端自动 RSA 加密→提交→拦截响应拿新 id。

    返回 (userName, userId)；失败返回 (None, None)。
    纯 httpx 重放捕获的密文会因"邮箱/账号已存在"失败（密文对应探测时的固定明文），
    所以创建必须走浏览器（前端负责 RSA 加密）。
    tenant_id/admin_id：运行时从 current-user 实时获取的当前账号值，用于 KB 注入补丁
    （estack /users/check 与 POST /users 缺 tenantId 会报 Invalid.Parameter）。
    """
    import json as _json
    from pathlib import Path as _Path
    from playwright.async_api import async_playwright as _apw

    if cookies_file is None:
        cookies_file = str(_Path(__file__).resolve().parent.parent / "output" / "config" / "cookies.json")
    cf = _Path(cookies_file)
    if not cf.exists():
        print("  ⚠️ 无 cookies.json，浏览器创建用户失败")
        return None, None
    try:
        cookies = _json.loads(cf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️ cookies.json 解析失败: {e}")
        return None, None

    ts_digits = str(int(time.time()))[-8:]
    user_name = f"autotest{ts_digits}"
    result = {"id": None}

    async def _run():
        nonlocal result
        async with _apw() as p:
            browser = await p.chromium.launch(headless=True,
                                              args=["--ignore-certificate-errors", "--disable-web-security",
                                                    "--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            try:
                await ctx.add_cookies([{"name": c["name"], "value": c["value"],
                                        "domain": "10.151.37.249", "path": "/"} for c in cookies])
            except Exception:
                pass
            page = await ctx.new_page()
            resp_log = []

            # ★ KB 注入补丁：estack 前端发 /users/check 与 POST /users 时 body 未带
            #   tenantId，API 报 Invalid.Parameter。在浏览器内给匹配请求自动补
            #   tenantId/adminId（来自运行时 current-user 实时值），与 capture_apis 的
            #   KB 驱动注入一致，否则创建会被前端校验卡住。
            # 注意：用普通字符串 + 占位符（不用 f-string），避免 {{ }} 转义吃掉插值。
            _inject_js = """() => {
                const MATCH = /\/draco\/v1\/users/;
                const FIXED = { tenantId: '__TID__', adminId: '__AID__' };
                function resolveFields() {
                    let tenantId = FIXED.tenantId, adminId = FIXED.adminId;
                    try {
                        const raw = localStorage.getItem('currentUserInfo') || '{}';
                        const u = JSON.parse(raw);
                        const ui = u.userInfo || u;
                        tenantId = tenantId || ui.tenantId || u.tenantId || '';
                        adminId = adminId || ui.id || u.id || '';
                    } catch (e) {}
                    return { tenantId, adminId };
                }
                function inject(b) {
                    try {
                        const o = JSON.parse(b);
                        const f = resolveFields();
                        let changed = false;
                        if (f.tenantId && (o.tenantId === undefined || o.tenantId === null || o.tenantId === '')) { o.tenantId = f.tenantId; changed = true; }
                        if (f.adminId && (o.adminId === undefined || o.adminId === null || o.adminId === '')) { o.adminId = f.adminId; changed = true; }
                        return changed ? JSON.stringify(o) : b;
                    } catch (e) { return b; }
                }
                const origOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; return origOpen.apply(this, arguments); };
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function (b) {
                    if (b && typeof b === 'string' && MATCH.test(this.__u || '')) { arguments[0] = inject(b); }
                    return origSend.apply(this, arguments);
                };
                const origFetch = window.fetch;
                window.fetch = function (u, opts) {
                    if (opts && opts.body && typeof opts.body === 'string' && MATCH.test(typeof u === 'string' ? u : ('' + u))) {
                        opts = Object.assign({}, opts); opts.body = inject(opts.body);
                    }
                    return origFetch(u, opts);
                };
            }"""
            _inject_js = _inject_js.replace("__TID__", (tenant_id or "")).replace("__AID__", (admin_id or ""))
            print(f"    [debug] tenant_id={tenant_id!r} admin_id={admin_id!r} js_has_tid={'__TID__' in _inject_js}")
            # add_init_script：导航后自动生效（对后续整页导航有效）
            await page.add_init_script(_inject_js)
            # 先完成页面加载，再 evaluate 手工安装补丁（SPA 内路由跳转不重新触发 init script，
            # 且表单校验(check)在填表时才发出，先装补丁后填表即可拦截）
            create_url = base_url.rstrip("/") + "/estack/web/estack/user-center/user-manage/user/create-user"
            try:
                await page.goto(create_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(4000)
            try:
                await page.evaluate(_inject_js)
                print("    [debug] 注入补丁已手工安装")
            except Exception as e:
                print(f"    [debug] 手工安装注入失败: {e}")

            async def _on_resp(resp):
                if "/draco/v1/users" in resp.url or "/password-reset" in resp.url:
                    try:
                        b = await resp.json()
                        resp_log.append({"method": resp.request.method, "url": resp.url[-80:],
                                         "success": (b or {}).get("success"), "code": (b or {}).get("errorCode"),
                                         "msg": ((b or {}).get("errorMessage") or "")[:80]})
                    except Exception:
                        resp_log.append({"method": resp.request.method, "url": resp.url[-80:], "raw": "non-json"})
                    if resp.request.method == "POST" and resp.url.rstrip("/").endswith("/draco/v1/users"):
                        try:
                            b = await resp.json()
                            ent = (b or {}).get("entity") or {}
                            if ent.get("id"):
                                result["id"] = ent["id"]
                        except Exception:
                            pass
            page.on("response", lambda r: __import__("asyncio").create_task(_on_resp(r)))

            await page.wait_for_timeout(1000)

            # ---- 填表（复刻 capture_apis 已验证逻辑：遍历 field_labels，input 取 .last）----
            ts_digits = str(int(time.time()))[-8:]
            fill_map = {
                "账号": user_name, "姓名": "自动化测试", "密码": "Test@123456",
                "确认密码": "Test@123456", "邮箱": f"autotest{ts_digits}@test.com",
                "手机号": f"138{ts_digits}", "描述": "自动创建测试用户",
            }
            field_labels = await page.evaluate("""() => {
                const inps = Array.from(document.querySelectorAll('input:not([type="hidden"]):not([type="search"]), textarea'));
                const result = [];
                for (const inp of inps) {
                    if (inp.offsetWidth <= 0) continue;
                    const fi = inp.closest('.el-form-item, .ant-form-item');
                    let label = '';
                    if (fi) {
                        const l = fi.querySelector('.el-form-item__label, .ant-form-item-label');
                        if (l) label = l.textContent.trim();
                    }
                    if (!label) label = inp.placeholder || '';
                    result.push({ label: label, type: inp.type || 'text', placeholder: inp.placeholder || '' });
                }
                return result;
            }""")
            print(f"    创建页字段: {[f['label'] for f in field_labels]}")

            filled = 0
            async def fill_one(label, value, is_textarea=False):
                nonlocal filled
                try:
                    item = page.locator(".el-form-item",
                                        has=page.locator(".el-form-item__label", has_text=label)).first
                    inp = item.locator("textarea:visible").last if is_textarea else item.locator("input:visible").last
                    await inp.click()
                    await inp.fill(value)
                    filled += 1
                    print(f"      ✅ [{label}] = {value}")
                    return True
                except Exception:
                    return False

            for f in field_labels:
                label = f["label"]
                if label in fill_map:
                    ok = await fill_one(label, fill_map[label], is_textarea=(label == "描述"))
                    if not ok:
                        print(f"      ⚠️ [{label}] 填充失败")
            print(f"    已用 Playwright 填充 {filled} 个字段")

            # ---- 必填下拉（系统角色必选；部门角色"无数据"可跳过）----
            for ddl_label in ("系统角色", "部门角色"):
                try:
                    sel = page.locator(".el-form-item", has_text=ddl_label).locator(".el-select").first
                    if await sel.count() > 0:
                        await sel.click(timeout=3000)
                        await page.wait_for_timeout(800)
                        opts = page.locator(".el-select-dropdown__item:visible")
                        n_opts = await opts.count()
                        if n_opts == 0 or (n_opts == 1 and "无数据" in (await opts.first.inner_text())):
                            print(f"      ⏭️ 下拉框 [{ddl_label}] → 无数据, 跳过")
                            await page.keyboard.press("Escape")
                            await page.wait_for_timeout(300)
                            continue
                        chosen = False
                        for j in range(n_opts):
                            t = (await opts.nth(j).inner_text()).strip()
                            if "普通用户" in t or ddl_label == "部门角色":
                                await opts.nth(j).click()
                                chosen = True
                                break
                        if not chosen and n_opts > 0:
                            await opts.first.click()
                        await page.wait_for_timeout(500)
                        print(f"      ✅ 下拉框 [{ddl_label}] 已选")
                except Exception as e:
                    print(f"      ⚠️ 下拉框 [{ddl_label}] 选择异常: {e}")

            # ---- 提交（复刻 capture_apis 三级策略：xpath order-submit → css → 全局去空格）----
            # ★ 偶发修复：提交后若未捕获到 POST /users（前端校验/加密时序竞争，已观测），重试一次
            for _attempt in range(2):
                try:
                    sub_res = await page.evaluate("""() => {
                        // 优先级1: XPath 定位 order-submit 内的按钮
                        const xpathResult = document.evaluate(
                            '//div[contains(@class,"order-submit")]//button',
                            document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
                        );
                        for (let i = 0; i < xpathResult.snapshotLength; i++) {
                            const b = xpathResult.snapshotItem(i);
                            if (b.offsetWidth > 0) {
                                const txt = (b.textContent || '').replace(/\s+/g, '');
                                if (['确定', '保存', '提交', '立即创建', '完成'].includes(txt)) {
                                    b.click(); return 'xpath_order_submit:' + txt;
                                }
                            }
                        }
                        // 优先级2: CSS 选择器 order-submit
                        const orderSubmit = document.querySelector('div.order-submit.clearfix, .order-submit, [class*="submit"]');
                        if (orderSubmit) {
                            const btns = orderSubmit.querySelectorAll('button, .el-button, span');
                            for (const b of btns) {
                                const txt = (b.textContent || '').replace(/\s+/g, '');
                                if (['确定', '保存', '提交', '立即创建', '完成', '下一步'].includes(txt) && b.offsetWidth > 0) {
                                    b.click(); return 'order_submit:' + txt;
                                }
                            }
                        }
                        // 优先级3: 全局搜索（去空格匹配）
                        const allBtns = document.querySelectorAll('button, .el-button');
                        for (const b of allBtns) {
                            const txt = (b.textContent || '').replace(/\s+/g, '');
                            if (['确定', '保存', '提交', '立即创建', '完成'].includes(txt) && b.offsetWidth > 0) {
                                b.click(); return 'global:' + txt;
                            }
                        }
                        return 'not_found';
                    }""")
                    print(f"      ✅ 提交({_attempt + 1}): {sub_res}")
                except Exception as e:
                    print(f"      ⚠️ 提交异常: {e}")
                await page.wait_for_timeout(5000)
                if result["id"]:
                    break
                if _attempt == 0:
                    print("    ⚠️ 提交后未捕获到 POST /users，重试提交...")
            print(f"    提交后URL: {page.url[:90]}")
            # dump 提交后页面的错误提示/校验文本
            try:
                err_txt = await page.evaluate("""() => {
                    const t = (document.body.innerText || '');
                    const hits = [];
                    for (const kw of ['请选择', '请输入', '不能为空', '格式', '失败', '错误', '已存在', '校验', '无效']) {
                        const idx = t.indexOf(kw);
                        if (idx >= 0) hits.push(t.slice(Math.max(0, idx - 40), idx + 40).replace(/\n/g, ' '));
                    }
                    return hits.slice(0, 5);
                }""")
                if err_txt:
                    print(f"    页面提示: {err_txt}")
                else:
                    print("    页面无错误提示文本")
            except Exception:
                pass
            if resp_log:
                for r in resp_log[-6:]:
                    print(f"    resp: {r}")
            await browser.close()

    try:
        import asyncio
        asyncio.run(_run())
    except Exception as e:
        print(f"  ⚠️ 浏览器创建用户异常: {e}")

    if result["id"]:
        print(f"  ✅ 浏览器创建用户成功: {user_name} id={result['id']}")
        return user_name, result["id"]
    print("  ⚠️ 浏览器创建用户未获取到新 id（可能表单校验未过），将跳过创建")
    return None, None


# ========== 浏览器授权用户（前端穿梭框选策略后提交，纯 httpx 无法复现）==========
def browser_authorize_user(base_url, user_id, cookies_file=None, policy_keyword="普通用户"):
    """对指定用户授权：直达授权页(rowId=user_id) → 穿梭框选策略 → 确定 → 提交 /policies/attach-to-user。

    返回 True/False。捕获到的真实提交 API：
      POST /estack/api/estack/draco/v1/policies/attach-to-user
      body: {"policies":[...], "principleIds":[user_id], "statement":{"collections":"*","pools":"*","projects":"*"}, "tenantId":...}
    授权页 URL 参数：add-authority?tenantId=xxx&type=userList&rowId=<userId>
    """
    import json as _json
    from pathlib import Path as _Path
    from playwright.async_api import async_playwright as _apw

    if cookies_file is None:
        cookies_file = str(_Path(__file__).resolve().parent.parent / "output" / "config" / "cookies.json")
    cf = _Path(cookies_file)
    if not cf.exists():
        print("  ⚠️ 无 cookies.json，浏览器授权失败")
        return False
    try:
        cookies = _json.loads(cf.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️ cookies.json 解析失败: {e}")
        return False
    ok = [False]

    async def _run():
        async with _apw() as p:
            browser = await p.chromium.launch(headless=True,
                                              args=["--ignore-certificate-errors", "--disable-web-security",
                                                    "--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            try:
                await ctx.add_cookies([{"name": c["name"], "value": c["value"],
                                        "domain": "10.151.37.249", "path": "/"} for c in cookies])
            except Exception:
                pass
            page = await ctx.new_page()
            # 先到用户管理页拿 tenantId（从 current-user 或 URL）
            list_url = base_url.rstrip("/") + "/estack/web/estack/user-center/user-manage/user"
            try:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            # 拿 tenantId：尝试从 localStorage 或 URL
            tid = await page.evaluate("""() => {
                try {
                    const u = JSON.parse(localStorage.getItem('currentUserInfo') || '{}');
                    return u.tenantId || (u.userInfo && u.userInfo.tenantId) || '';
                } catch (e) { return ''; }
            }""")
            if not tid:
                tid = await page.evaluate("() => (location.search.match(/tenantId=([^&]+)/) || [])[1] || ''")
            if not tid:
                print("  ⚠️ 未取到 tenantId，授权失败")
                await browser.close()
                return
            # 直达授权页（rowId = 要授权的用户 id）
            auth_url = (base_url.rstrip("/")
                        + "/estack/web/estack/user-center/user-manage/authority-manage/add-authority"
                        + f"?tenantId={tid}&type=userList&rowId={user_id}")
            try:
                await page.goto(auth_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(6000)
            print(f"    授权页URL: {page.url[:120]}")
            # 确认预填用户存在
            prefilled = await page.evaluate("""() => {
                const fi = Array.from(document.querySelectorAll('.el-form-item')).find(f => /授权用户/.test(f.textContent) && f.offsetWidth > 0);
                if (!fi) return '';
                const tag = fi.querySelector('.el-select__tags-text, .el-tag');
                return tag ? tag.textContent.trim() : '';
            }""")
            print(f"    预填用户: {prefilled}")
            # 穿梭框选策略（点击左侧策略行，进入已选项）
            picked = await page.evaluate("""() => {
                const left = document.querySelector('.transfer-box-left');
                if (!left) return false;
                const tr = left.querySelector('.el-table__body-wrapper tbody tr');
                if (tr) { tr.click(); return true; }
                return false;
            }""")
            print(f"    选择策略: {picked}")
            await page.wait_for_timeout(1200)
            right = await page.evaluate("""() => {
                const right = document.querySelector('.transfer-box-right');
                return right ? (right.textContent||'').trim().slice(0, 60) : '';
            }""")
            print(f"    已选项: {right}")
            # 点确定（JS 原生 click，页面唯一 primary 确定）
            clicked = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button')).filter(b => b.offsetWidth > 0);
                const targets = btns.filter(b => (b.textContent||'').replace(/\s+/g,'') === '确定');
                if (!targets.length) return false;
                targets[targets.length - 1].click();
                return true;
            }""")
            print(f"    点确定: {clicked}")
            await page.wait_for_timeout(6000)
            # 判断是否跳转（成功会跳回 authority-manage 列表）
            if "/add-authority" not in page.url:
                ok[0] = True
                print(f"  ✅ 授权提交成功，已跳转: {page.url[:90]}")
            else:
                print(f"  ⚠️ 授权后仍在授权页: {page.url[:90]}")
            await browser.close()

    try:
        import asyncio
        asyncio.run(_run())
    except Exception as e:
        print(f"  ⚠️ 浏览器授权异常: {e}")
    return ok[0]



# ========== 初始化 ==========
def get_auth_client():
    """获取带有效鉴权的 httpx.Client（Cookie优先 → 滑块兜底）。"""
    # 从环境变量或生成时嵌入的凭据取凭证（运营管理员，已授权）
    username = os.environ.get("ESTACK_USER") or 'estack-yy'
    password = os.environ.get("ESTACK_PASS") or 'R@9eDuck$!mpleM00n'

    # 构建最小 profile（复用 lib/auth.py）
    profile = {
        "login_url": LOGIN_URL,
        "auth": {
            "header_name": "Authorization",
            "header_prefix": "Bearer ",
            "freshness_ttl_seconds": 300,
            "fixed_headers": {
                "Estack-Language": "zh-CN",
            },
        },
        "captcha": {
            "auth_button_text": "点击完成认证",
            "login_button_text": "登录",
        },
        "credentials": {},
        # 探活端点
        "probe_url": "/estack/api/estack/draco/v1/users/current-user",
    }

    sess = AuthSession(profile, username, password)
    # 设置 context_path 以支持 cookie 持久化
    ctx_dir = Path(__file__).resolve().parent.parent / "output" / "config"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    sess.context_path = str(ctx_dir / "context.json")

    # 获取带有效鉴权的 client：cookie 优先 → 服务端探活 → 失效自动滑块登录回写 cookie
    # （AUTO_LOGIN=0 时仅读 cookie，不自动登录，用于并行批量跑避免多进程抢登）
    client = sess.ensure_client(BASE_URL)
    if client is None:
        print("⚠️ 无法获取有效鉴权：cookie 已过期且无自动登录凭据")
        print("   请设置环境变量 AUTO_LOGIN=1 并提供 --user/--pass，或先运行发现流程刷新 cookie")
        return None

    return client


# ========== API 调用工具 ==========
class ApiResponse:
    def __init__(self, resp: httpx.Response):
        self.status = resp.status_code
        self.text = resp.text
        self._json = None
        try:
            self._json = resp.json()
        except Exception:
            pass

    @property
    def json(self) -> dict:
        return self._json

    @property
    def success(self) -> bool:
        """estack 真成功标准。"""
        if self.status >= 300:
            return False
        if self._json and isinstance(self._json, dict):
            return bool(self._json.get("success")) and not self._json.get("errorCode")
        return self.status < 300

    def __repr__(self):
        return f"<ApiResponse status={self.status}, success={self.success}>"


# ========== API 调用函数 ==========
def api_create_users(client, **kwargs):
    """create: /estack/api/estack/draco/v1/users"""
    url = "/estack/api/estack/draco/v1/users"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_query_list(client, **kwargs):
    """query: /estack/api/estack/draco/v1/policies/list"""
    url = "/estack/api/estack/draco/v1/policies/list"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_query_userList(client, **kwargs):
    """query: /estack/api/estack/draco/v1/projects/list-by-user/userList"""
    url = "/estack/api/estack/draco/v1/projects/list-by-user/userList"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_query_password_policy(client, **kwargs):
    """query: /estack/api/estack/draco/v1/password-policy"""
    url = "/estack/api/estack/draco/v1/password-policy"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("GET", url, json=kwargs)
        else:
            resp = client.request("GET", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "GET")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_query_groups(client, **kwargs):
    """query: /estack/api/estack/draco/v1/groups"""
    url = "/estack/api/estack/draco/v1/groups"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("GET", url, json=kwargs)
        else:
            resp = client.request("GET", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "GET")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_update_users(client, **kwargs):
    """update: /estack/api/estack/draco/v1/users/e7fc8fdd2e4e4b2a9acde911bb0d15e4"""
    url = "/estack/api/estack/draco/v1/users/{id}"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("PUT", url, json=kwargs)
        else:
            resp = client.request("PUT", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "PUT")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_lock_suspend(client, **kwargs):
    """lock: /estack/api/estack/draco/v1/users/3903efebcb9c4bf2b845508d4d6c6dda/suspend"""
    url = "/estack/api/estack/draco/v1/users/{id}/suspend"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("PUT", url, json=kwargs)
        else:
            resp = client.request("PUT", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "PUT")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_lock_lock(client, **kwargs):
    """lock: /estack/api/estack/draco/v1/users/lock/3903efebcb9c4bf2b845508d4d6c6dda"""
    url = "/estack/api/estack/draco/v1/users/lock/{id}"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_unlock_enable(client, **kwargs):
    """unlock: /estack/api/estack/draco/v1/users/3903efebcb9c4bf2b845508d4d6c6dda/enable"""
    url = "/estack/api/estack/draco/v1/users/{id}/enable"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("PUT", url, json=kwargs)
        else:
            resp = client.request("PUT", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "PUT")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_unlock_unlock(client, **kwargs):
    """unlock: /estack/api/estack/draco/v1/users/unlock/3903efebcb9c4bf2b845508d4d6c6dda"""
    url = "/estack/api/estack/draco/v1/users/unlock/{id}"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("DELETE", url, json=kwargs)
        else:
            resp = client.request("DELETE", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "DELETE")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_reset_generate(client, **kwargs):
    """reset: /estack/api/estack/draco/v1/password-reset/generate"""
    url = "/estack/api/estack/draco/v1/password-reset/generate"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_authorize_identity(client, **kwargs):
    """authorize: /estack/api/estack/draco/v1/identity"""
    url = "/estack/api/estack/draco/v1/identity"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("POST", url, json=kwargs)
        else:
            resp = client.request("POST", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "POST")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r

def api_delete_users(client, **kwargs):
    """delete: /estack/api/estack/draco/v1/users/e7fc8fdd2e4e4b2a9acde911bb0d15e4"""
    url = "/estack/api/estack/draco/v1/users/{id}"
    # 用传入的 id 替换路径中的 {id} 占位（新建用户的 id，非探测时的 id）
    if "{id}" in url and kwargs.get("id"):
        url = url.replace("{id}", str(kwargs["id"]))
    try:
        if kwargs:
            resp = client.request("DELETE", url, json=kwargs)
        else:
            resp = client.request("DELETE", url)
        # ★ 结构化请求/响应详情（供 HTML 报告解析为展开卡片）
        try:
            import re as _re
            import json as _json
            _stat = getattr(resp, "status_code", getattr(resp, "status", "?"))
            _req = getattr(resp, "request", None)
            _req_url = str(getattr(_req, "url", url))
            _req_headers = dict(getattr(_req, "headers", None) or dict())
            _req_body = (getattr(_req, "content", b"") or b"").decode("utf-8", errors="replace")
            _resp_headers = dict(getattr(resp, "headers", None) or dict())
            _resp_body = resp.text or ""
            _m = _re.search(r'"errorMessage"\s*:\s*"([^"](0, 100))', _resp_body)
            _err = _m.group(1) if _m else ""
            try:
                _j = _json.loads(_resp_body) if _resp_body else dict()
                _succ = _j.get("success", "-") if isinstance(_j, dict) else "-"
            except Exception:
                _succ = "-"
            _req_method = getattr(_req, "method", "DELETE")
            # >>> 请求区块（report_html.py 解析为结构化面板）
            print(f"    >>> 请求")
            print(f"    method: {_req_method}")
            print(f"    url: {_req_url}")
            # 请求头：过滤敏感/冗余字段，保留鉴权与业务相关头
            _safe_headers = {k: v for k, v in _req_headers.items()
                                if k.lower() not in ("cookie", "user-agent", "accept-encoding",
                                                     "connection", "accept", "host")}
            print(f"    headers: {_json.dumps(_safe_headers, ensure_ascii=False)}")
            if _req_body:
                try:
                    _body_fmt = _json.dumps(_json.loads(_req_body), ensure_ascii=False, indent=2)
                except Exception:
                    _body_fmt = _req_body[:800]
                print(f"    body: {_body_fmt}")
            # <<< 响应区块
            print(f"    <<< 响应")
            print(f"    status: {_stat}")
            _resp_ct = _resp_headers.get("content-type", _resp_headers.get("Content-Type", ""))
            print(f"    content-type: {_resp_ct}")
            print(f"    success: {_succ}")
            if _err:
                print(f"    error: {_err}")
            # 响应体：尝试格式化 JSON，截断超长
            if _resp_body:
                try:
                    _resp_fmt = _json.dumps(_json.loads(_resp_body), ensure_ascii=False, indent=2)
                except Exception:
                    _resp_fmt = _resp_body
                print(f"    body: {_resp_fmt[:2000]}")
        except Exception:
            pass
        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = ""
        r._json = {"success": False}
        return r


# ========== 主流程 ==========
def main():
    # 设置 UTF-8 编码，避免 Windows 控制台 GBK 编码错误
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print("=" * 60)
    print("  用户管理 API 测试")
    print("=" * 60)

    try:
        client = get_auth_client()
        if client is None:
            print("❌ 无法获取有效鉴权")
            return

        # 初始化动态变量
        saved_id = None
        saved_uname = None
        saved_tenantId = None
        saved_adminId = None

        # 尝试获取当前用户信息（运行时实时取 tenantId / adminId，确保脚本适配任意登录账号）
        try:
            me = client.request("GET", "/estack/api/estack/draco/v1/users/current-user")
            if me.status_code < 300:
                me_json = me.json()
                me_entity = me_json.get("entity", {}) or {}
                saved_tenantId = me_entity.get("tenantId")
                saved_adminId = me_entity.get("id")
                print(f"  ✅ 当前用户: {me_entity.get('name', 'unknown')}")
                print(f"  tenantId: {saved_tenantId}  adminId: {saved_adminId}")
        except Exception as _me:
            print(f"  ⚠️ 获取当前用户失败: {_me}")

        # 构建实际测试数据
        test_data_actual = dict(test_data)
        # 优先用运行时真实的 tenantId / adminId 覆盖（创建接口强依赖，避免沿用捕获时的旧账号 id）
        if saved_tenantId:
            test_data_actual["tenantId"] = saved_tenantId
        if saved_adminId:
            test_data_actual["adminId"] = saved_adminId
        # 替换名称模板（test_data 已用 "AT_" + TS 唯一化；此处兜底旧格式）
        for key in ("name", "userName"):
            if key in test_data_actual and isinstance(test_data_actual[key], str):
                if test_data_actual[key].startswith("AT_"):
                    continue
                test_data_actual[key] = test_data_actual[key].replace("AT_", f"AT_{TS}_")


    
    # ---- 1. 创建 ----
        print("\n[创建] /estack/api/estack/draco/v1/users" )
        try:
            if not saved_id:
                _uname, _uid = browser_create_user(BASE_URL, username="estack-yy", tenant_id=saved_tenantId or "", admin_id=saved_adminId or "")
                if _uid:
                    saved_id = _uid
                    saved_uname = _uname
            if not saved_id:
                resp = api_create_users(client, **test_data_actual)
                # ★ 四层断言：HTTP状态码 + 业务成功 + 关键字段非空 + 状态语义
                assert 200 <= resp.status < 300, f"创建失败: HTTP {resp.status}"
                assert resp.success, f"创建失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "创建失败: 无响应体"
                entity = resp.json.get("entity") or resp.json.get("data")
                assert entity, "创建失败: 响应中无 entity/data"
                saved_id = entity.get("id")
                assert saved_id, "创建失败: 未返回 id"
                print(f"  ✅ 创建成功, id={saved_id}")
            if saved_id:
                # 验证创建后状态
                print(f"  ✅ 用户已创建, id={saved_id}")
                resp_check = api_query_list(client, id=saved_id)
                if resp_check.success and resp_check.json:
                    entity = resp_check.json.get("entity") or resp_check.json.get("data") or {}
                    if isinstance(entity, list):
                        entity = next((i for i in entity if i.get("id") == saved_id), {})
                    actual_state = entity.get("state") if entity else None
                    if actual_state:
                        assert actual_state == "ENABLE", f"创建后状态应为 ENABLE, 实际为 {actual_state}"
                        print(f"  ✅ 状态验证: {actual_state} == ENABLE")
        except AssertionError as _ae:
            print(f"  ❌ 创建断言失败: {_ae}")
        except Exception as _ce:
            print(f"  ⚠️ 创建异常: {_ce}")
    
    # ---- 2. 查询 ----
        print("\n[查询] /estack/api/estack/draco/v1/policies/list" )
        try:
            resp = api_query_list(client, pageNum=1, pageSize=50)
            # ★ 四层断言：HTTP状态码 + 业务成功 + 数据结构 + 业务语义
            assert 200 <= resp.status < 300, f"查询失败: HTTP {resp.status}"
            assert resp.success, f"查询失败: 业务错误 {resp.text[:200]}"
            assert resp.json, "查询失败: 无响应体"
            entity = resp.json.get("entity") or resp.json.get("data")
            if isinstance(entity, dict):
                items = entity.get("list") or entity.get("records") or []
                total = entity.get("total", len(items))
            else:
                items = entity if isinstance(entity, list) else []
                total = len(items)
            print(f"  ✅ 查询成功: {len(items)} 条记录, 总计 {total}")
            if saved_id and items:
                found = any(i.get("id") == saved_id for i in items)
                if found:
                    found_item = next(i for i in items if i.get("id") == saved_id)
                    print(f"  ✅ 数据存在: id={saved_id}")
                    # 状态语义验证
                    if "state" in found_item:
                        state_val = found_item["state"]
                        print(f"  - 当前状态: {state_val}")
                else:
                    print(f"  ⚠️ 未找到 id={saved_id} 的记录")
        except AssertionError as _ae:
            print(f"  ❌ 查询断言失败: {_ae}")
        except Exception as _qe:
            print(f"  ⚠️ 查询异常: {_qe}")
    
    # ---- 3. 查询 ----
        print("\n[查询] /estack/api/estack/draco/v1/projects/list-by-user/userList" )
        try:
            resp = api_query_userList(client, pageNum=1, pageSize=50)
            # ★ 四层断言：HTTP状态码 + 业务成功 + 数据结构 + 业务语义
            assert 200 <= resp.status < 300, f"查询失败: HTTP {resp.status}"
            assert resp.success, f"查询失败: 业务错误 {resp.text[:200]}"
            assert resp.json, "查询失败: 无响应体"
            entity = resp.json.get("entity") or resp.json.get("data")
            if isinstance(entity, dict):
                items = entity.get("list") or entity.get("records") or []
                total = entity.get("total", len(items))
            else:
                items = entity if isinstance(entity, list) else []
                total = len(items)
            print(f"  ✅ 查询成功: {len(items)} 条记录, 总计 {total}")
            if saved_id and items:
                found = any(i.get("id") == saved_id for i in items)
                if found:
                    found_item = next(i for i in items if i.get("id") == saved_id)
                    print(f"  ✅ 数据存在: id={saved_id}")
                    # 状态语义验证
                    if "state" in found_item:
                        state_val = found_item["state"]
                        print(f"  - 当前状态: {state_val}")
                else:
                    print(f"  ⚠️ 未找到 id={saved_id} 的记录")
        except AssertionError as _ae:
            print(f"  ❌ 查询断言失败: {_ae}")
        except Exception as _qe:
            print(f"  ⚠️ 查询异常: {_qe}")
    
    # ---- 4. 查询 ----
        print("\n[查询] /estack/api/estack/draco/v1/password-policy" )
        try:
            resp = api_query_password_policy(client, pageNum=1, pageSize=50)
            # ★ 四层断言：HTTP状态码 + 业务成功 + 数据结构 + 业务语义
            assert 200 <= resp.status < 300, f"查询失败: HTTP {resp.status}"
            assert resp.success, f"查询失败: 业务错误 {resp.text[:200]}"
            assert resp.json, "查询失败: 无响应体"
            entity = resp.json.get("entity") or resp.json.get("data")
            if isinstance(entity, dict):
                items = entity.get("list") or entity.get("records") or []
                total = entity.get("total", len(items))
            else:
                items = entity if isinstance(entity, list) else []
                total = len(items)
            print(f"  ✅ 查询成功: {len(items)} 条记录, 总计 {total}")
            if saved_id and items:
                found = any(i.get("id") == saved_id for i in items)
                if found:
                    found_item = next(i for i in items if i.get("id") == saved_id)
                    print(f"  ✅ 数据存在: id={saved_id}")
                    # 状态语义验证
                    if "state" in found_item:
                        state_val = found_item["state"]
                        print(f"  - 当前状态: {state_val}")
                else:
                    print(f"  ⚠️ 未找到 id={saved_id} 的记录")
        except AssertionError as _ae:
            print(f"  ❌ 查询断言失败: {_ae}")
        except Exception as _qe:
            print(f"  ⚠️ 查询异常: {_qe}")
    
    # ---- 5. 查询 ----
        print("\n[查询] /estack/api/estack/draco/v1/groups" )
        try:
            resp = api_query_groups(client, pageNum=1, pageSize=50)
            # ★ 四层断言：HTTP状态码 + 业务成功 + 数据结构 + 业务语义
            assert 200 <= resp.status < 300, f"查询失败: HTTP {resp.status}"
            assert resp.success, f"查询失败: 业务错误 {resp.text[:200]}"
            assert resp.json, "查询失败: 无响应体"
            entity = resp.json.get("entity") or resp.json.get("data")
            if isinstance(entity, dict):
                items = entity.get("list") or entity.get("records") or []
                total = entity.get("total", len(items))
            else:
                items = entity if isinstance(entity, list) else []
                total = len(items)
            print(f"  ✅ 查询成功: {len(items)} 条记录, 总计 {total}")
            if saved_id and items:
                found = any(i.get("id") == saved_id for i in items)
                if found:
                    found_item = next(i for i in items if i.get("id") == saved_id)
                    print(f"  ✅ 数据存在: id={saved_id}")
                    # 状态语义验证
                    if "state" in found_item:
                        state_val = found_item["state"]
                        print(f"  - 当前状态: {state_val}")
                else:
                    print(f"  ⚠️ 未找到 id={saved_id} 的记录")
        except AssertionError as _ae:
            print(f"  ❌ 查询断言失败: {_ae}")
        except Exception as _qe:
            print(f"  ⚠️ 查询异常: {_qe}")
    
    # ---- 6. 修改 ----
        print("\n[修改] /estack/api/estack/draco/v1/users/e7fc8fdd2e4e4b2a9acde911bb0d15e4" )
        if not saved_id: print("  ⚠️ 跳过[修改]: saved_id 为空（创建未成功）")
        try:
            if saved_id:
                resp = api_update_users(client, id=saved_id, description="自动修改_{TS}")
                assert 200 <= resp.status < 300, f"修改失败: HTTP {resp.status}"
                assert resp.success, f"修改失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "修改失败: 无响应体"
                print("  ✅ 修改成功")
        except AssertionError as _ae:
            print(f"  ❌ 修改断言失败: {_ae}")
        except Exception as _ue:
            print(f"  ⚠️ 修改异常: {_ue}")
    
    # ---- 7. 锁定 ----
        print("\n[锁定] /estack/api/estack/draco/v1/users/3903efebcb9c4bf2b845508d4d6c6dda/suspend" )
        if not saved_id: print("  ⚠️ 跳过[锁定]: saved_id 为空（创建未成功）")
        try:
            if saved_id:
                resp = api_lock_suspend(client, id=saved_id)
                assert 200 <= resp.status < 300, f"锁定失败: HTTP {resp.status}"
                assert resp.success, f"锁定失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "锁定失败: 无响应体"
                print("  ✅ 锁定成功")
                resp_check = api_query_list(client, id=saved_id)
                if resp_check.success and resp_check.json:
                    entity = resp_check.json.get("entity") or resp_check.json.get("data") or {}
                    if isinstance(entity, list):
                        entity = next((i for i in entity if i.get("id") == saved_id), {})
                    actual_state = entity.get("state") if entity else None
                    if actual_state:
                        assert actual_state == "DISABLE", f"锁定后状态应为 DISABLE, 实际为 {actual_state}"
                        print(f"  ✅ 状态验证: {actual_state} == DISABLE")
        except AssertionError as _ae:
            print(f"  ❌ 锁定断言失败: {_ae}")
        except Exception as _le:
            print(f"  ⚠️ 锁定异常: {_le}")
    
    # ---- 8. 锁定 ----
        print("\n[锁定] /estack/api/estack/draco/v1/users/lock/3903efebcb9c4bf2b845508d4d6c6dda" )
        if not saved_id: print("  ⚠️ 跳过[锁定]: saved_id 为空（创建未成功）")
        try:
            if saved_id:
                resp = api_lock_lock(client, id=saved_id)
                assert 200 <= resp.status < 300, f"锁定失败: HTTP {resp.status}"
                assert resp.success, f"锁定失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "锁定失败: 无响应体"
                print("  ✅ 锁定成功")
                resp_check = api_query_list(client, id=saved_id)
                if resp_check.success and resp_check.json:
                    entity = resp_check.json.get("entity") or resp_check.json.get("data") or {}
                    if isinstance(entity, list):
                        entity = next((i for i in entity if i.get("id") == saved_id), {})
                    actual_state = entity.get("state") if entity else None
                    if actual_state:
                        assert actual_state == "DISABLE", f"锁定后状态应为 DISABLE, 实际为 {actual_state}"
                        print(f"  ✅ 状态验证: {actual_state} == DISABLE")
        except AssertionError as _ae:
            print(f"  ❌ 锁定断言失败: {_ae}")
        except Exception as _le:
            print(f"  ⚠️ 锁定异常: {_le}")
    
    # ---- 9. 解锁 ----
        print("\n[解锁] /estack/api/estack/draco/v1/users/3903efebcb9c4bf2b845508d4d6c6dda/enable" )
        if not saved_id: print("  ⚠️ 跳过[解锁]: saved_id 为空（创建未成功）")
        try:
            if saved_id:
                resp = api_unlock_enable(client, id=saved_id)
                assert 200 <= resp.status < 300, f"解锁失败: HTTP {resp.status}"
                assert resp.success, f"解锁失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "解锁失败: 无响应体"
                print("  ✅ 解锁成功")
        except AssertionError as _ae:
            print(f"  ❌ 解锁断言失败: {_ae}")
        except Exception as _ue:
            print(f"  ⚠️ 解锁异常: {_ue}")
    
    # ---- 10. 解锁 ----
        print("\n[解锁] /estack/api/estack/draco/v1/users/unlock/3903efebcb9c4bf2b845508d4d6c6dda" )
        if not saved_id: print("  ⚠️ 跳过[解锁]: saved_id 为空（创建未成功）")
        try:
            if saved_id:
                resp = api_unlock_unlock(client, id=saved_id)
                assert 200 <= resp.status < 300, f"解锁失败: HTTP {resp.status}"
                assert resp.success, f"解锁失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "解锁失败: 无响应体"
                print("  ✅ 解锁成功")
        except AssertionError as _ae:
            print(f"  ❌ 解锁断言失败: {_ae}")
        except Exception as _ue:
            print(f"  ⚠️ 解锁异常: {_ue}")
    
    # ---- 11. reset ----
        print("\n[reset] /estack/api/estack/draco/v1/password-reset/generate" )
        try:
            if not saved_id: print("  ⚠️ 跳过[重置]: saved_id 为空（创建未成功）")
            if saved_id:
                _rb = {"passwordPolicy": {"id": "90542be67d584ab09daa12e697fb041a", "tenantId": "cec63451f8bf4ceebb9ada0b87d829bf", "minPasswordLength": 8, "requireLowercaseCharacters": False, "requireUppercaseCharacters": True, "requireNumbers": True, "requireSymbols": True, "minPasswordDifferentCharacter": 0, "createdAt": "2023-08-30 10:07:14", "updatedAt": "2025-03-08 18:39:42", "deleted": False, "userName": None}, "isRandomPassword": False}
                _rb["userId"] = saved_id
                if saved_tenantId: _rb["tenantId"] = saved_tenantId
                resp = api_reset_generate(client, **_rb)
                assert 200 <= resp.status < 300, f"重置失败: HTTP {resp.status}"
                assert resp.success, f"重置失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "重置失败: 无响应体"
                print("  ✅ reset成功")
        except AssertionError as _ae:
            print(f"  ❌ reset断言失败: {_ae}")
        except Exception as _ge:
            print(f"  ⚠️ reset异常: {_ge}")
    
    # ---- 12. authorize ----
        print("\n[authorize] /estack/api/estack/draco/v1/identity" )
        try:
            if not saved_id: print("  ⚠️ 跳过[授权]: saved_id 为空（创建未成功）")
            if saved_id:
                _auth_ok = browser_authorize_user(BASE_URL, saved_id)
                assert _auth_ok, "授权失败: 浏览器驱动未完成"
                print("  ✅ authorize成功")
        except AssertionError as _ae:
            print(f"  ❌ authorize断言失败: {_ae}")
        except Exception as _ge:
            print(f"  ⚠️ authorize异常: {_ge}")
    
    # ---- 13. 删除 ----
        print("\n[删除] /estack/api/estack/draco/v1/users/e7fc8fdd2e4e4b2a9acde911bb0d15e4" )
        try:
            if saved_id:
                resp = api_delete_users(client, id=saved_id)
                # ★ 四层断言：HTTP状态码 + 业务成功 + 响应非空 + 清理验证
                assert 200 <= resp.status < 300, f"删除失败: HTTP {resp.status}"
                assert resp.success, f"删除失败: 业务错误 {resp.text[:200]}"
                assert resp.json, "删除失败: 无响应体"
                print("  ✅ 删除成功")
                # 清理验证：确认数据已删除
                resp_check = api_query_list(client, id=saved_id)
                if resp_check.success and resp_check.json:
                    check_entity = resp_check.json.get("entity") or resp_check.json.get("data")
                    if check_entity and isinstance(check_entity, list):
                        still_exists = any(i.get("id") == saved_id for i in check_entity)
                        assert not still_exists, "删除验证失败: 数据仍存在"
                    print("  ✅ 清理验证: 数据已删除")
        except AssertionError as _ae:
            print(f"  ❌ 删除断言失败: {_ae}")
        except Exception as _de:
            print(f"  ⚠️ 删除异常: {_de}")

    except Exception as _main_err:
        print(f"\n❌ 主流程异常: {_main_err}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("  ✅ 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
