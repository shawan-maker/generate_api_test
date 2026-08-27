"""
CRUD 端到端验证 —— 登录 → token → API 调用 → 断言 → 清理。
这是真正跑通"用到实际系统的入口"。
"""
import sys, json, uuid, asyncio, time, yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import auth
from lib.api_client import get_client, ApiResponse, get_fn  # 统一从框架层导入
import httpx


profile_path = Path(__file__).resolve().parents[1] / "profile.yaml"
profile = yaml.safe_load(open(profile_path))

base_url = profile.get("base_url", "")
api_base = profile.get("api_base", "")
header_name = profile.get("auth", {}).get("header_name", "Authorization")
header_prefix = profile.get("auth", {}).get("header_prefix", "Bearer ")
fixed_headers = profile.get("auth", {}).get("fixed_headers", {})
sid = fixed_headers.get("SID", "")
estack_lang = fixed_headers.get("Estack-Language", "zh-CN")


async def main():
    # ---- 1. 登录拿 token ----
    print("=" * 60)
    print("AccessKey CRUD 端到端验证")
    print("=" * 60)
    print("\n📡 登录中...")

    sess = auth.AuthSession(profile)
    if not sess.have_credentials():
        print("❌ 请设置 ESTACK_USER / ESTACK_PASS")
        return 1

    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            "--ignore-certificate-errors", "--disable-web-security",
            "--no-sandbox",
        ])
        ctx = await browser.new_context(viewport={"width": 1600, "height": 1000})
        page = await ctx.new_page()
        ok = await sess.login_with_browser(page, ctx)
        if not ok:
            print("❌ 登录失败")
            return 1
        print("✅ 登录成功")
        token = sess.token
        await browser.close()

    # ---- 2. 创建 httpx client ----
    headers = dict(fixed_headers or {})
    headers[header_name] = f"{header_prefix}{token}"
    headers["Content-Type"] = "application/json"
    c = httpx.Client(base_url=base_url + api_base, headers=headers, verify=False, timeout=30)
    print(f"✅ Client: {c.base_url}")

    # ---- 3. CRUD ----
    def api_req(method, path, **kwargs):
        """直接 httpx 调用（不依赖 api 层生成的函数, 因为可能函数名与实际 endpoint 不匹配）"""
        resp = c.request(method, path, **kwargs)
        try:
            body = resp.json()
        except Exception:
            body = None
        return resp.status_code, body

    results = {"passed": 0, "failed": 0, "steps": []}

    def check(label, condition, detail=""):
        ok = bool(condition)
        status = "✅" if ok else "❌"
        results["passed" if ok else "failed"] += 1
        results["steps"].append({"label": label, "ok": ok, "detail": detail})
        print(f"  {status} {label}{' — ' + detail[:200] if detail else ''}")

    ak_name = f"AT_CRUD_{uuid.uuid4().hex[:6]}"
    created_id = None

    # ---- 3a. CREATE ----
    print("\n【STEP 1】创建 AccessKey")
    status, body = api_req("POST", "/estack/api/estack/draco/v1/accesskey/create",
                           json={"name": ak_name, "remark": "e2e-verify"})
    check(f"HTTP {status}", status < 300, f"success={body.get('success') if body else 'N/A'}")
    check("success==true", body and body.get("success") is True, f"errorCode={body.get('errorCode')}")
    if body and body.get("success"):
        entity = body.get("entity") or body.get("data") or {}
        created_id = entity.get("id")
        check("有 created_id", bool(created_id), f"id={created_id}")

    # ---- 3b. LIST ----
    print("\n【STEP 2】查询列表")
    status, body = api_req("POST", "/estack/api/estack/draco/v1/accesskey/list",
                           json={"pageNum": 1, "pageSize": 50})
    check(f"HTTP {status}", status < 300)
    items = []
    if body:
        items = body.get("data") or body.get("entity") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("records") or []
    found = any(item.get("name") == ak_name for item in items if isinstance(items, list))
    check(f"列表中找到 '{ak_name}'", found, f"共 {len(items)} 项")

    # ---- 3c. DETAIL (通过 list+filter 模拟) ----
    print("\n【STEP 3】详情（list+filter）")
    detail = None
    for item in items if isinstance(items, list) else []:
        if item.get("name") == ak_name:
            detail = item
            break
    check("找到详情记录", bool(detail))
    if detail:
        status_field = detail.get("status") or detail.get("state") or ""
        check(f"状态字段: {status_field}", bool(status_field),
              f"全部字段: {list(detail.keys())}")

    # ---- 3d. DELETE/CLEANUP ----
    print("\n【STEP 4】清理（尝试删除或禁用）")
    if created_id:
        status, body = api_req("DELETE",
                               f"/estack/api/estack/draco/v1/accesskey/{created_id}")
        if status < 300:
            check("DELETE OK", True, f"HTTP {status}")
        else:
            # 可能 accesskey 不支持删除, 尝试禁用
            status2, body2 = api_req("POST",
                                     f"/estack/api/estack/draco/v1/accesskey/disable",
                                     json={"id": created_id})
            check("DISABLE 兜底", status2 < 300,
                  f"DELETE failed({status}), DISABLE→{status2}")
    else:
        print("  ⏭️ 跳过（无 created_id）")

    # ---- 报告 ----
    print("\n" + "=" * 60)
    total = results["passed"] + results["failed"]
    print(f"📊 结果: ✅ {results['passed']}/{total} 通过, "
          f"❌ {results['failed']}/{total} 失败")
    print("=" * 60)

    # 资源 cleanup 通知
    if created_id and not status < 300:
        print(f"\n⚠️ 残留资源: {ak_name} (id={created_id}) 需手动清理")

    return 0 if results["failed"] == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
