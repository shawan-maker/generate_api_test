"""
P0 巡游骨架 —— 登录(滑块) -> 菜单树抓取 -> XHR 拦截 -> 归并 -> 落盘。
入口: run_p0.py 或直接 `python -m discovery.playwright_crawl`。
离线演示: 设置环境变量 PLAYWRIGHT_OFFLINE=1 时, 不启浏览器, 直接用 sample_capture.json 跑通归并+导出流水线。
"""
import os
import sys
import json
import asyncio
import argparse
import logging
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib import catalog, nav, auth  # noqa: E402

LOG = logging.getLogger("p0")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

ROOT = Path(__file__).resolve().parents[1]


def load_profile(profile_path: str) -> dict:
    with open(profile_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


async def collect_responses(page, captures: list, api_base: str):
    """挂 response 监听, 收集 API 调用(请求+响应)。"""
    async def on_response(resp):
        url = resp.url
        if api_base not in url:
            return
        method = resp.request.method
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            return
        # 跳过静态/文档
        if any(url.endswith(e) for e in (".js", ".css", ".png", ".ico", ".svg", ".woff2", ".map")):
            return
        try:
            req = resp.request
            q = {k: v[0] if isinstance(v, list) and v else v for k, v in parse_qs(urlsplit(url).query).items()}
            rb = None
            pd = req.post_data
            if pd:
                try:
                    rb = json.loads(pd)
                except Exception:
                    rb = pd
            try:
                body = await resp.json()
            except Exception:
                try:
                    body = await resp.text()
                except Exception:
                    body = None
            captures.append({
                "method": method,
                "url": url,
                "path": urlsplit(url).path,
                "query": q,
                "request_headers": dict(req.headers),
                "request_body": catalog.desensitize(rb),
                "status": resp.status,
                "response_body": catalog.desensitize(body),
                "source": "traffic",
                "ts": asyncio.get_event_loop().time(),
            })
        except Exception as e:
            LOG.warning(f"capture error: {e}")
    page.on("response", lambda r: asyncio.create_task(on_response(r)))


async def visit_and_collect(page, item: dict, captures: list, base_url: str = "", wait_ms=2500,
                            idx: int = 0, total: int = 0):
    """点击菜单项(精确匹配文本)进入功能页, 让其 XHR 自然发生; 顺手点'新建'触发表单选项接口(不提交)。"""
    label = item.get("label")
    if not label or item.get("is_group"):
        return
    group = item.get("group", "")
    before = len(captures)
    LOG.info(f"[{idx}/{total}] 巡游: {group + ' / ' if group else ''}{label}")
    # 展开所属子菜单
    if group:
        try:
            await page.evaluate("""(g) => {
              const t=[...document.querySelectorAll('.el-submenu__title')].find(e=>(e.textContent||'').trim()===g);
              if(t) t.click();
            }""", group)
        except Exception:
            pass
        await page.wait_for_timeout(500)
    # 精确点击菜单项
    clicked = await page.evaluate("""(lbl) => {
      const items=[...document.querySelectorAll('.el-menu-item')];
      const el=items.find(e=>(e.textContent||'').trim()===lbl) || items.find(e=>(e.textContent||'').includes(lbl));
      if(el){el.click();return true;} return false;
    }""", label)
    if not clicked:
        LOG.warning(f"[{idx}/{total}] 未找到/无法点击菜单项: {label}")
        return
    await page.wait_for_timeout(wait_ms)
    # 尝试点开"新建"表单(只开不提交), 抓选项加载接口
    opened_form = False
    for lab in ("新建", "创建", "添加"):
        try:
            btn = page.locator(f"button:has-text('{lab}')").first
            if await btn.is_visible(timeout=800):
                await btn.click(timeout=1500)
                await page.wait_for_timeout(1500)
                opened_form = True
                # 扫描弹窗内的表单字段(Element UI el-dialog), 抽取 label/placeholder/必填性
                from discovery.form_extractor import extract_el_dialog_fields
                form_fields = await extract_el_dialog_fields(page, captures, label + "_新建")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
                break
        except Exception:
            pass
    LOG.info(f"[{idx}/{total}] ↳ 新捕获 {len(captures) - before} 条请求"
             f"{' (含新建表单)' if opened_form else ''}, 累计 {len(captures)}")


MAX_PAGES = 60


async def run_p0(profile_path: str, username: str = None, password: str = None, headless: bool = True, max_pages: int = MAX_PAGES):
    profile = load_profile(profile_path)
    proj_dir = ROOT / "projects" / profile["name"]
    api_base = profile.get("api_base", "")
    base_url = profile.get("base_url", "")
    kb_dir = proj_dir / "kb"
    cap_dir = proj_dir / "captures"
    out_dir = proj_dir / "output" / "config"
    kb_dir.mkdir(parents=True, exist_ok=True)
    cap_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    sess = auth.AuthSession(profile, username, password)
    sess.context_path = str(out_dir / "context.json")
    secrets_file = proj_dir / ".secrets.yaml"
    if secrets_file.exists():
        sess.load_secrets_file(str(secrets_file))
    if not sess.have_credentials():
        LOG.error("缺少凭据: 请设置环境变量 ESTACK_USER / ESTACK_PASS, 或提供 --user --password")
        return None

    captures = []
    menu_items = []

    # ===== 浏览器登录 =====
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, args=[
            "--ignore-certificate-errors", "--disable-web-security",
            "--disable-blink-features=AutomationControlled", "--no-sandbox",
        ])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000},
                                       user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        page = await ctx.new_page()
        await collect_responses(page, captures, api_base)

        LOG.info("开始滑块登录...")
        ok = await sess.login_with_browser(page, ctx)
        if not ok:
            LOG.error("登录失败, 终止。")
            await browser.close()
            return None
        LOG.info("登录成功, token 已获取。")
        sess.save_context()

        # ===== 跳到能渲染菜单的页面(menu.menu_landing 优先, 否则控制台主页) =====
        # 注意: menu_landing 嵌在 profile 的 menu: 段下, 顶层同名键仅作兼容
        landing = (profile.get("menu", {}) or {}).get("menu_landing") or profile.get("menu_landing")
        if landing:
            target_url = base_url.rstrip("/") + landing
        else:
            target_url = (base_url.rstrip("/") + (profile.get("web_entry") or "")).rstrip("/") or base_url
        LOG.info(f"跳转到菜单页: {target_url}")
        try:
            await page.goto(target_url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            LOG.warning(f"goto 菜单页超时(继续): {e}")
        try:
            await page.wait_for_selector(".el-menu-item", timeout=20000)
        except Exception:
            LOG.warning("未等到 .el-menu-item, 尝试直接抓取")
        await page.wait_for_timeout(2000)

        # ===== 菜单树抓取 =====
        LOG.info("抓取菜单树(DOM)...")
        menu_items = await nav.capture_menu_via_dom(page)
        # 菜单 API 兜底(用已登录 token 直接 http 调)
        try:
            import httpx
            async with httpx.AsyncClient(verify=False, headers=sess.headers(), timeout=10) as hc:
                async def getf(m, u):
                    r = await hc.get(u if u.startswith("http") else base_url + u)
                    try:
                        return r.status_code, r.json()
                    except Exception:
                        return r.status_code, None
                # 仅对"真实 URL"(不含正则元字符)发起请求; 正则占位符是提示, 待实采后替换
                import re as _re
                real_eps = [e for e in profile.get("menu", {}).get("api_endpoints", [])
                            if e.startswith("http") or ("/" in e and not _re.search(r"[.*()?+]", e))]
                api_menu = await nav.capture_menu_via_api(getf, real_eps)
                if api_menu:
                    menu_items = api_menu + menu_items
        except Exception as e:
            LOG.warning(f"菜单 API 抓取失败(已用 DOM 结果): {e}")

        # ===== 巡游: 逐个点击功能菜单项 =====
        targets = [it for it in menu_items if it.get("label") and not it.get("is_group")]
        LOG.info(f"菜单抓取完成: {len(menu_items)} 项, 其中可巡游功能页 {len(targets)} 个(上限 {max_pages})")
        plan = targets[:max_pages]
        raw_path_live = cap_dir / "p0_captures.json"
        for i, it in enumerate(plan, 1):
            try:
                await visit_and_collect(page, it, captures, base_url, idx=i, total=len(plan))
            except Exception as e:
                LOG.warning(f"[{i}/{len(plan)}] 巡游异常(跳过): {it.get('label')} -> {e}")
            # 增量落盘: 中断也不丢已抓到的流量
            try:
                raw_path_live.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        await browser.close()

    return write_artifacts(profile, captures, menu_items, kb_dir, cap_dir, out_dir)


def write_artifacts(profile, captures, menu_items, kb_dir, cap_dir, out_dir):
    """归并 + 落盘 + 摘要。run_p0 与 --remerge 共用, 保证两条路产物一致。"""
    crud_ov = catalog.load_crud_overrides(kb_dir)
    if crud_ov:
        LOG.info(f"应用 crud_overrides 人工校正 {len(crud_ov)} 条")
    merged = catalog.merge_captures(captures, profile, crud_ov)
    openapi_catalog = catalog.build_api_catalog(merged, profile)

    raw_path = cap_dir / "p0_captures.json"
    cat_path = kb_dir / "api_catalog.json"
    menu_path = kb_dir / "menu_tree.json"
    raw_path.write_text(json.dumps(captures, ensure_ascii=False, indent=2), encoding="utf-8")
    cat_path.write_text(json.dumps(openapi_catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    menu_path.write_text(json.dumps(menu_items, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人类可读摘要: 按 CRUD 分组, 便于人工校正
    by_crud = {}
    for ep in merged.values():
        by_crud.setdefault(ep["x-kb-crud"], []).append(ep)
    summary = [
        f"# P0 发现摘要 — {profile.get('display_name','')}",
        f"- 拦截 API 调用: {len(captures)} 次",
        f"- 归并端点: {len(merged)} 个",
        f"- 功能菜单: {len(menu_items)} 项",
        f"- 人工校正: {len(crud_ov)} 条 (kb/crud_overrides.json)",
        "\n## 端点分布",
    ]
    for k in ("create", "list", "detail", "update", "delete", "action"):
        if k in by_crud:
            summary.append(f"- {k}: {len(by_crud[k])} 个")
    summary.append("\n## 接口清单(按 CRUD 分组)")
    for k in ("create", "list", "detail", "update", "delete", "action"):
        for ep in sorted(by_crud.get(k, []), key=lambda e: e["path_template"]):
            summary.append(f"- `{k}` {ep['method']} {ep['path_template']}  "
                           f"[{ep['x-kb-service']}/{ep['x-kb-resource']}] x{ep['count']}")
    summary.append("\n## 功能菜单")
    summary.append(nav.summary_menu(menu_items))
    (out_dir / "p0_summary.md").write_text("\n".join(summary), encoding="utf-8")

    LOG.info(f"完成: {len(captures)} 条流量 -> {len(merged)} 个端点, 菜单 {len(menu_items)} 项")
    LOG.info(f"  - 原始拦截: {raw_path}")
    LOG.info(f"  - 接口目录: {cat_path}")
    LOG.info(f"  - 菜单树:   {menu_path}")
    LOG.info(f"  - 摘要:     {out_dir/'p0_summary.md'}")
    return {"captures": len(captures), "endpoints": len(merged), "menu": len(menu_items)}


def run_remerge(profile_path: str):
    """
    离线重归并: 复用已抓到的 p0_captures.json + menu_tree.json 重算目录。
    改了分类器或 crud_overrides.json 后用它, 不必重新登录抓包(省时且不打扰被测系统)。
    """
    profile = load_profile(profile_path)
    proj_dir = ROOT / "projects" / profile["name"]
    kb_dir, cap_dir = proj_dir / "kb", proj_dir / "captures"
    out_dir = proj_dir / "output" / "config"
    for d in (kb_dir, cap_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)
    captures = _load_json(cap_dir / "p0_captures.json") or []
    menu_items = _load_json(kb_dir / "menu_tree.json") or []
    if not captures:
        LOG.error(f"没有可复用的流量: {cap_dir/'p0_captures.json'} 不存在或为空。请先跑一次真实巡游。")
        return None
    LOG.info(f"[重归并] 复用 {len(captures)} 条已抓流量, 菜单 {len(menu_items)} 项")
    return write_artifacts(profile, captures, menu_items, kb_dir, cap_dir, out_dir)


def run_offline(profile_path: str):
    """不启浏览器, 用 sample_capture.json 演示归并+导出流水线。"""
    profile = load_profile(profile_path)
    sample = _load_json(ROOT / "discovery" / "sample_capture.json") or []
    merged = catalog.merge_captures(sample, profile)
    openapi_catalog = catalog.build_api_catalog(merged, profile)
    proj_dir = ROOT / "projects" / profile["name"]
    kb_dir = proj_dir / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    cat_path = kb_dir / "api_catalog.json"
    cat_path.write_text(json.dumps(openapi_catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info(f"[离线] 用 {len(sample)} 条样本归并出 {len(merged)} 个端点 -> {cat_path}")
    for k, ep in sorted(merged.items()):
        LOG.info(f"  - {ep['method']} {ep['path_template']}  [{ep['x-kb-crud']}/{ep['x-kb-service']}]")
    return {"captures": len(sample), "endpoints": len(merged)}


def main():
    ap = argparse.ArgumentParser(description="API_AI_test P0 巡游")
    ap.add_argument("--profile", default=str(ROOT / "projects" / "estack" / "profile.yaml"))
    ap.add_argument("--user", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false")
    ap.add_argument("--offline", action="store_true", help="不启浏览器, 用 sample_capture.json 演示")
    ap.add_argument("--remerge", action="store_true",
                    help="不启浏览器, 复用已抓 p0_captures.json 重算目录(改分类器/overrides 后用)")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="巡游功能页上限(默认 60)")
    args = ap.parse_args()

    if args.remerge:
        run_remerge(args.profile)
        return
    if args.offline or os.environ.get("PLAYWRIGHT_OFFLINE") == "1":
        run_offline(args.profile)
        return
    asyncio.run(run_p0(args.profile, args.user, args.password, args.headless, args.max_pages))


if __name__ == "__main__":
    main()
