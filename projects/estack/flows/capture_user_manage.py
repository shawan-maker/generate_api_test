"""
capture_user_manage.py

登录 estack → 跳转用户管理页面 → 逐一点击操作按钮(新增/编辑/锁定/删除)，
拦截 XHR 抓取 API, 补全 catalog。

用法: python capture_user_manage.py
"""
import sys, json, time, asyncio, re, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import auth, slider
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

PROJ_DIR = Path(__file__).resolve().parent
PROFILE = json.loads(open(PROJ_DIR / "profile.json").read()) if (PROJ_DIR / "profile.json").exists() else {}
# 硬编码凭据
USERNAME = "estack-yy"
PASSWORD = "R@9eDuck$!mpleM00n"
BASE_URL = "https://console-estack-syyhb.cmecloud.cn"
LOGIN_URL = f"{BASE_URL}/estack/web/estack/login"
TARGET_URL = f"{BASE_URL}/estack/web/estack/user-center/user-manage/user"
OUTPUT = PROJ_DIR.parent / "kb" / "user_manage_captured.json"


async def main():
    captured = []
    samples = {}
    current_ctx = "init"

    def capture(method, url, body=None):
        try:
            u = str(url)
            if "/estack/api/estack/" not in u:
                return
            p = u.split("?")[0]
            if re.search(r"\.(js|css|png|jpg|svg|woff|ttf|ico|map)$", p):
                return
            if "/web/" in p:
                return
            captured.append(dict(method=method, url=u, pathname=p,
                                 body=body, ctx=current_ctx,
                                 ts=time.time()))
        except Exception:
            pass

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--ignore-certificate-errors", "--disable-web-security", "--no-sandbox"],
        )
        ctx = await browser.new_context(viewport=dict(width=1600, height=1000), locale="zh-CN")
        page = await ctx.new_page()

        # 拦截请求
        page.on("request", lambda req: capture(req.method, req.url, req.post_data()))

        # 响应样本
        async def on_resp(resp):
            u = resp.url
            if "/estack/api/estack/" not in u:
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                b = await resp.text()
                if b and len(b) > 20:
                    pn = u.split("?")[0]
                    samples.setdefault(pn, [])
                    if len(samples[pn]) < 2:
                        samples[pn].append(dict(status=resp.status,
                                                body=(b[:2000] + "...") if len(b) > 2000 else b))
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # ==== 登录 ====
        print("🔄 打开登录页...")
        await page.goto(LOGIN_URL, wait_until="load", timeout=45000)
        await asyncio.sleep(5)

        # 切中文
        try:
            has = await page.evaluate("() => { const e = document.querySelector('.lang-style'); return e && e.offsetWidth > 0; }")
            if has:
                await page.click(".lang-style")
                await asyncio.sleep(1)
                await page.evaluate("""() => {
                    const items = document.querySelectorAll('.el-dropdown-menu__item');
                    for (const i of items) { if (i.textContent.trim() === '简体中文') { i.click(); return; } }
                }""")
                await asyncio.sleep(1)
                print("✅ 中文")
        except Exception:
            pass

        # 填表
        await page.fill('input[placeholder="用户名"]', USERNAME)
        await page.fill('input[placeholder="登录密码"]', PASSWORD)
        await asyncio.sleep(0.5)

        # 认证按钮
        current_ctx = "auth"
        try:
            btn = page.locator("button:has-text('认证')")
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print("✅ 认证按钮")
        except Exception:
            pass
        await asyncio.sleep(3)

        # 滑块
        current_ctx = "slider"
        geo = await slider.read_slider_geo(page)
        mat = None

        # 拦截验证码素材
        async def on_resp_cap(resp):
            nonlocal mat
            try:
                if mat:
                    return
                j = await resp.json()
                if isinstance(j.get("entity"), dict) and j["entity"].get("backImage"):
                    mat = j["entity"]
            except Exception:
                pass
        page.on("response", lambda r: asyncio.create_task(on_resp_cap(r)))

        for attempt in range(4):
            if not geo.get("ok"):
                try:
                    btn = page.locator("button:has-text('认证')")
                    await btn.click()
                except Exception:
                    pass
                await asyncio.sleep(2)
                geo = await slider.read_slider_geo(page)
            if not mat or not mat.get("backImage"):
                await asyncio.sleep(1)
                continue
            try:
                gap = slider.solve_slider(mat["backImage"], mat["slidingImage"])
            except Exception:
                gap = dict(gap_location=0)
            if gap.get("gap_location", 0) <= 0:
                await asyncio.sleep(0.5)
                continue
            dist = slider.compute_drag_distance(geo, gap)
            print(f"  slider dist={dist:.1f}")
            await slider.human_drag(page, geo["handle"], dist)
            await asyncio.sleep(2)
            v = await slider.poll_verify(page)
            if v:
                print(f"✅ 滑块通过 (第{attempt+1}次)")
                break
            # 刷新
            try:
                await page.evaluate("""() => {
                    const sv = document.querySelector('#slideVerify');
                    if (sv) { const r = sv.querySelector('[class*=refresh]'); if (r) r.click(); }
                }""")
            except Exception:
                pass
            mat = None
            await asyncio.sleep(2)
        else:
            print("⚠️ 滑块未通过，请手动完成滑块，然后按回车继续...")
            await asyncio.sleep(3)

        # 点击登录
        current_ctx = "login"
        try:
            btn = page.locator("button:has-text('登录')")
            if await btn.is_visible(timeout=2000):
                await btn.click()
                print("✅ 登录按钮")
        except Exception:
            pass
        await asyncio.sleep(5)

        # 等待登录
        logged_in = False
        for _ in range(15):
            tok = await page.evaluate("() => localStorage.getItem('estackToken')")
            if tok:
                logged_in = True
                print(f"✅ 登录成功 token={tok[:12]}...")
                break
            ck = await ctx.cookies()
            if any(c["name"] == "accessToken" for c in ck):
                logged_in = True
                print("✅ 登录成功(cookie)")
                break
            await asyncio.sleep(2)

        if not logged_in:
            print("⚠️ 登录失败，尝试直连...")

        # ==== 导航到用户管理 ====
        current_ctx = "pageload"
        print(f"🔄 导航到用户管理: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="load", timeout=45000)
        await asyncio.sleep(8)
        print("✅ 用户管理页面已加载")

        # ==== 操作 ====
        # 1. 列表
        current_ctx = "list"
        await asyncio.sleep(3)
        print("\n[1/5] 列表 — 已自动加载")

        # 2. 新增
        current_ctx = "create"
        print("\n[2/5] 新增用户...")
        try:
            btn = page.locator("span:has-text('新增用户')").first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                print("  ✅ 点击新增")
                await asyncio.sleep(4)
                # 读弹窗
                ff = await page.evaluate("""() => {
                    const d = document.querySelector('.el-dialog');
                    if (!d) return null;
                    const inps = d.querySelectorAll('input, textarea, .el-select .el-input__inner');
                    return Array.from(inps).map(i => ({ n: i.name || i.placeholder || i.className || '?', t: i.type || 'text' }));
                }""")
                if ff:
                    print(f"  表单字段({len(ff)}): {[f['n'] for f in ff[:10]]}")
                # 关闭
                try:
                    cl = page.locator(".el-dialog__headerbtn").first
                    if await cl.is_visible(timeout=1000):
                        await cl.click()
                        await asyncio.sleep(1)
                except Exception:
                    pass
        except Exception as e:
            print(f"  ❌ {e}")

        # 3. 编辑
        current_ctx = "edit"
        print("\n[3/5] 编辑...")
        try:
            btn = page.locator("span:has-text('编辑')").first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                print("  ✅ 点击编辑")
                await asyncio.sleep(4)
                try:
                    cl = page.locator(".el-dialog__headerbtn").first
                    if await cl.is_visible(timeout=1000):
                        await cl.click()
                except Exception:
                    pass
        except Exception as e:
            print(f"  ❌ {e}")

        # 4. 锁定
        current_ctx = "lock"
        print("\n[4/5] 锁定...")
        for label in ["锁定", "禁用", "停用"]:
            try:
                btn = page.locator(f"span:has-text('{label}')").first
                if await btn.is_visible(timeout=1000):
                    await btn.click()
                    await asyncio.sleep(2)
                    try:
                        ok = page.locator("button:has-text('确定')").first
                        if await ok.is_visible(timeout=1000):
                            await ok.click()
                            await asyncio.sleep(2)
                    except Exception:
                        pass
                    print(f"  ✅ {label}")
                    break
            except Exception:
                pass

        # 5. 删除
        current_ctx = "delete"
        print("\n[5/5] 删除...")
        try:
            btn = page.locator("span:has-text('删除')").first
            if await btn.is_visible(timeout=3000):
                await btn.click()
                await asyncio.sleep(2)
                try:
                    ok = page.locator("button:has-text('确定')").first
                    if await ok.is_visible(timeout=1000):
                        await ok.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass
                print("  ✅ 删除")
        except Exception as e:
            print(f"  ❌ {e}")

        # ==== 保存 ====
        # 去重
        uniq = {}
        for api in captured:
            k = api["method"] + " " + api["pathname"]
            if k not in uniq:
                uniq[k] = dict(method=api["method"], pathname=api["pathname"],
                               actions=set(), body=api.get("body"))
            uniq[k]["actions"].add(api.get("ctx", "?"))
            if api.get("body") and not uniq[k].get("body"):
                uniq[k]["body"] = api["body"]

        out = dict(
            captureTime=time.strftime("%Y-%m-%dT%H:%M:%S"),
            targetUrl=TARGET_URL,
            total=len(captured),
            unique=len(uniq),
            endpoints=[dict(method=e["method"], pathname=e["pathname"],
                            actions=list(e["actions"]),
                            requestBody=e.get("body"))
                       for e in uniq.values()],
            responseSamples=samples,
        )

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        open(OUTPUT, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n✅ 已保存 {len(uniq)} 个唯一端点 => {OUTPUT}")
        for ep in sorted(out["endpoints"], key=lambda x: x["pathname"]):
            print(f"  {ep['method']:6s} {ep['pathname']:<50s} [{','.join(ep['actions'])}]")

        await asyncio.sleep(5)
        print("\n⚠️ 浏览器保持打开。手动关闭或 Ctrl+C 退出。")

if __name__ == "__main__":
    asyncio.run(main())
