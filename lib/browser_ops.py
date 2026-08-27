"""
lib/browser_ops.py - 通用浏览器操作函数

职责：
- 浏览器驱动的创建资源（填表→提交→拦截响应拿 ID）
- 浏览器驱动的授权操作（穿梭框选策略→提交）
- Cookie domain 自动提取
- KB 注入补丁（从 KB 配置动态构建）

用法：
    from lib.browser_ops import browser_create_resource, browser_authorize_resource

    user_name, user_id = browser_create_resource(
        base_url="https://10.151.37.249",
        create_url="/estack/web/estack/user-center/user-manage/user/create-user",
        cookies_file="output/config/cookies.json",
        fill_map={"账号": "autotest123", "姓名": "自动化测试", ...},
        response_intercept_url="/draco/v1/users",
    )
"""

import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Tuple, List

LOG = logging.getLogger("browser_ops")


def _extract_domain(base_url: str) -> str:
    """从 BASE_URL 提取域名（用于 cookie 注入）。

    例: "https://10.151.37.249" → "10.151.37.249"
        "https://example.com:8080" → "example.com"
    """
    try:
        domain = base_url.split("//")[1].split("/")[0].split(":")[0]
        return domain
    except (IndexError, AttributeError):
        return ""


def _load_cookies(cookies_file: str) -> Optional[list]:
    """加载 cookies.json，返回 cookies 列表。失败返回 None。"""
    cf = Path(cookies_file)
    if not cf.exists():
        LOG.warning(f"  无 cookies.json: {cf}")
        return None
    try:
        return json.loads(cf.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.warning(f"  cookies.json 解析失败: {e}")
        return None


def browser_create_resource(
    base_url: str,
    create_url: str,
    cookies_file: str = None,
    fill_map: Dict[str, str] = None,
    response_intercept_url: str = "",
    inject_js: str = "",
    headless: bool = True,
) -> Tuple[Optional[str], Optional[str]]:
    """用浏览器在前端上下文创建资源：填表→前端加密→提交→拦截响应拿新 id。

    Args:
        base_url: 系统基础 URL（如 https://10.151.37.249）
        create_url: 创建页完整路径（如 /estack/web/.../create-user）
        cookies_file: cookies.json 路径（默认 output/config/cookies.json）
        fill_map: {label: value} 填充映射
        response_intercept_url: 拦截的响应 URL 片段（如 "/draco/v1/users"）
        inject_js: KB 注入补丁 JS（可选，为空则不注入）
        headless: 是否无头模式

    Returns:
        (resource_name, resource_id): 成功时返回名称和 ID；失败返回 (None, None)
    """
    from playwright.async_api import async_playwright

    if cookies_file is None:
        cookies_file = str(Path(__file__).resolve().parent.parent / "output" / "config" / "cookies.json")

    cookies = _load_cookies(cookies_file)
    if cookies is None:
        return None, None

    domain = _extract_domain(base_url)
    full_url = base_url.rstrip("/") + create_url
    result = {"id": None, "name": None}

    async def _run():
        nonlocal result
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--ignore-certificate-errors", "--disable-web-security",
                      "--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                viewport={"width": 1600, "height": 1000}, locale="zh-CN")

            # 注入 cookies
            try:
                await ctx.add_cookies([
                    {"name": c["name"], "value": c["value"],
                     "domain": domain or c.get("domain", ""), "path": "/"}
                    for c in cookies
                ])
            except Exception:
                pass

            page = await ctx.new_page()

            # 注入 KB 补丁
            if inject_js:
                try:
                    await page.add_init_script(inject_js)
                except Exception as e:
                    LOG.warning(f"    注入补丁应用失败: {e}")

            # 导航到创建页
            try:
                await page.goto(full_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(4000)

            # 手工安装注入补丁（SPA 内路由跳转不重新触发 init script）
            if inject_js:
                try:
                    await page.evaluate(inject_js)
                except Exception:
                    pass

            # 监听响应，拦截创建 API 拿 ID
            async def _on_resp(resp):
                if response_intercept_url and response_intercept_url in resp.url:
                    if resp.request.method == "POST":
                        try:
                            b = await resp.json()
                            ent = (b or {}).get("entity") or {}
                            if ent.get("id"):
                                result["id"] = ent["id"]
                        except Exception:
                            pass
            page.on("response", lambda r: __import__("asyncio").create_task(_on_resp(r)))

            # 扫描表单字段并填充
            field_labels = await page.evaluate("""() => {
                const inps = Array.from(document.querySelectorAll(
                    'input:not([type="hidden"]):not([type="search"]), textarea'));
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
                    result.push({label, type: inp.type || 'text', placeholder: inp.placeholder || ''});
                }
                return result;
            }""")
            LOG.info(f"    创建页字段: {[f['label'] for f in field_labels]}")

            filled = 0
            for f in field_labels:
                label = f["label"]
                if label in fill_map:
                    is_textarea = (f["type"] == "textarea" or label in ("描述", "备注", "说明"))
                    try:
                        item = page.locator(".el-form-item",
                                            has=page.locator(".el-form-item__label", has_text=label)).first
                        inp = item.locator("textarea:visible").last if is_textarea else item.locator("input:visible").last
                        await inp.click()
                        await inp.fill(fill_map[label])
                        filled += 1
                    except Exception:
                        pass

            LOG.info(f"    已填充 {filled} 个字段")

            # 选择下拉框（系统角色等）
            selects = await page.query_selector_all('.el-select')
            for sel in selects:
                try:
                    await sel.click()
                    await page.wait_for_timeout(500)
                    options = await page.query_selector_all('.el-select-dropdown__item:visible')
                    if options:
                        await options[0].click()
                        await page.wait_for_timeout(300)
                    else:
                        await page.keyboard.press('Escape')
                except Exception:
                    await page.keyboard.press('Escape')

            # 提交（三级策略）
            for _attempt in range(2):
                try:
                    sub_res = await page.evaluate("""() => {
                        // 优先级1: XPath order-submit
                        const xpathResult = document.evaluate(
                            '//div[contains(@class,"order-submit")]//button',
                            document, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
                        for (let i = 0; i < xpathResult.snapshotLength; i++) {
                            const b = xpathResult.snapshotItem(i);
                            if (b.offsetWidth > 0) {
                                const txt = (b.textContent || '').replace(/\\s+/g, '');
                                if (['确定', '保存', '提交', '立即创建', '完成'].includes(txt)) {
                                    b.click(); return 'xpath:' + txt;
                                }
                            }
                        }
                        // 优先级2: CSS order-submit
                        const os = document.querySelector('.order-submit, [class*="submit"]');
                        if (os) {
                            const btns = os.querySelectorAll('button, .el-button');
                            for (const b of btns) {
                                const txt = (b.textContent || '').replace(/\\s+/g, '');
                                if (['确定', '保存', '提交', '立即创建', '完成'].includes(txt) && b.offsetWidth > 0) {
                                    b.click(); return 'css:' + txt;
                                }
                            }
                        }
                        // 优先级3: 全局
                        const allBtns = document.querySelectorAll('button, .el-button');
                        for (const b of allBtns) {
                            const txt = (b.textContent || '').replace(/\\s+/g, '');
                            if (['确定', '保存', '提交', '立即创建', '完成'].includes(txt) && b.offsetWidth > 0) {
                                b.click(); return 'global:' + txt;
                            }
                        }
                        return 'not_found';
                    }""")
                    LOG.info(f"      提交({_attempt + 1}): {sub_res}")
                except Exception as e:
                    LOG.warning(f"      提交异常: {e}")

                await page.wait_for_timeout(5000)
                if result["id"]:
                    break

            await browser.close()

    try:
        import asyncio
        asyncio.run(_run())
    except Exception as e:
        LOG.warning(f"  浏览器创建资源异常: {e}")

    if result["id"]:
        LOG.info(f"  浏览器创建资源成功: id={result['id']}")
        return result.get("name"), result["id"]

    LOG.warning("  浏览器创建资源未获取到 ID")
    return None, None


def browser_authorize_resource(
    base_url: str,
    resource_id: str,
    auth_url_path: str = "",
    cookies_file: str = None,
    policy_keyword: str = "普通用户",
    headless: bool = True,
) -> bool:
    """对指定资源授权：直达授权页→穿梭框选策略→确定→提交。

    Args:
        base_url: 系统基础 URL
        resource_id: 要授权的资源 ID
        auth_url_path: 授权页路径（如 /estack/web/.../add-authority）
        cookies_file: cookies.json 路径
        policy_keyword: 策略关键词（用于选择策略）
        headless: 是否无头模式

    Returns:
        True/False: 授权是否成功
    """
    from playwright.async_api import async_playwright

    if cookies_file is None:
        cookies_file = str(Path(__file__).resolve().parent.parent / "output" / "config" / "cookies.json")

    cookies = _load_cookies(cookies_file)
    if cookies is None:
        return False

    domain = _extract_domain(base_url)
    ok = [False]

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--ignore-certificate-errors", "--disable-web-security",
                      "--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )
            ctx = await browser.new_context(
                viewport={"width": 1600, "height": 1000}, locale="zh-CN")

            try:
                await ctx.add_cookies([
                    {"name": c["name"], "value": c["value"],
                     "domain": domain or c.get("domain", ""), "path": "/"}
                    for c in cookies
                ])
            except Exception:
                pass

            page = await ctx.new_page()

            # 先到列表页拿 tenantId
            list_url = base_url.rstrip("/") + auth_url_path.rsplit("/", 2)[0] if auth_url_path else base_url
            try:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)

            # 拿 tenantId
            tid = await page.evaluate("""() => {
                try {
                    const u = JSON.parse(localStorage.getItem('currentUserInfo') || '{}');
                    return u.tenantId || (u.userInfo && u.userInfo.tenantId) || '';
                } catch (e) { return ''; }
            }""")

            if not tid:
                LOG.warning("  未取到 tenantId，授权失败")
                await browser.close()
                return

            # 直达授权页
            full_auth_url = (base_url.rstrip("/") + auth_url_path
                             + f"?tenantId={tid}&type=userList&rowId={resource_id}")
            try:
                await page.goto(full_auth_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            await page.wait_for_timeout(6000)
            LOG.info(f"    授权页URL: {page.url[:120]}")

            # 穿梭框选策略
            picked = await page.evaluate("""() => {
                const left = document.querySelector('.transfer-box-left');
                if (!left) return false;
                const tr = left.querySelector('.el-table__body-wrapper tbody tr');
                if (tr) { tr.click(); return true; }
                return false;
            }""")
            LOG.info(f"    选择策略: {picked}")
            await page.wait_for_timeout(1200)

            # 点确定
            clicked = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetWidth > 0);
                const targets = btns.filter(b =>
                    (b.textContent||'').replace(/\\s+/g,'') === '确定');
                if (!targets.length) return false;
                targets[targets.length - 1].click();
                return true;
            }""")
            LOG.info(f"    点确定: {clicked}")
            await page.wait_for_timeout(6000)

            # 判断是否跳转（成功会跳回列表页）
            if "/add-authority" not in page.url:
                ok[0] = True
                LOG.info(f"  授权提交成功，已跳转: {page.url[:90]}")
            else:
                LOG.warning(f"  授权后仍在授权页: {page.url[:90]}")

            await browser.close()

    try:
        import asyncio
        asyncio.run(_run())
    except Exception as e:
        LOG.warning(f"  浏览器授权异常: {e}")

    return ok[0]
