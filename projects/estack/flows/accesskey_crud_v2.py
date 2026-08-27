"""
accesskey CRUD 端到端验证 v2 —— 基于 sess.get_client() 自动管理 cookie 生命周期。
完全遵循 EcsCloud session.js 的设计: cookie优先 → TTL→服务端探活→自动重登。

入口统一处理: 确保 CD 到项目根再执行。
"""
import sys, json, uuid, asyncio, yaml, time, os
from pathlib import Path

# ---- 项目根路径（确保无论从哪执行都能工作）----
_PROJ = Path(__file__).resolve().parents[3]   # flows/estack/projects/API_AI_test
os.chdir(str(_PROJ))
sys.path.insert(0, str(_PROJ))

from lib import auth


async def get_session() -> auth.AuthSession:
    """
    获取 AuthSession, 自动尝试复用已有 cookie。
    逻辑流程:
      1. 尝试从本地加载已保存的 context + cookies
      2. TTL 有效 → 直接返回 (cookie 还能用)
      3. TTL 过期 → 服务端探活 → 有效则刷新 TTL
      4. 探活失败 → 走浏览器登录获取新 cookie
    """
    profile_path = _PROJ / "projects" / "estack" / "profile.yaml"
    profile = yaml.safe_load(open(profile_path))

    sess = auth.AuthSession(profile)
    out_dir = _PROJ / "projects" / "estack" / "output" / "config"
    sess.context_path = str(out_dir / "context.json")
    base_url = profile.get("base_url", "")

    # Step 1: 加载本地上下文
    restored = sess.load_context()
    if restored:
        print("✅ 从本地恢复 cookie（TTL 内）")
        return sess

    # Step 2: 尝试服务端探活
    if sess.could_be_valid():
        alive = sess.probe_online(base_url)
        if alive:
            print("✅ 本地 cookie 探活成功, 仍有效")
            sess.token_issued_at = time.time()
            return sess
        print("⚠️ 本地 cookie 已过期, 需要重新登录")

    # Step 3: 浏览器登录
    print("📡 浏览器登录中...")
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
            raise RuntimeError("登录失败")
        print("✅ 登录成功, cookies 已保存")
        await browser.close()

    return sess


def check(label, ok, detail=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon} {label}{' — ' + detail[:200] if detail else ''}")
    return ok


def main():
    print("=" * 60)
    print("AccessKey CRUD v2 — 基于 get_client() 自动鉴权")
    print("=" * 60)

    # ---- 1. 获取 session（自动 cookie 管理）----
    sess = asyncio.run(get_session())
    profile = yaml.safe_load(open(_PROJ / "projects" / "estack" / "profile.yaml"))
    base_url = profile.get("base_url", "")
    api_base = profile.get("api_base", "")

    # ---- 2. 通过 get_client() 获取带 cookie 的 httpx Client ----
    # base_url 为主域名, api_base 为空; CRUD 脚本手动拼接 api_base 前缀
    c = sess.get_client(base_url, api_base="")
    if not c:
        print("❌ 无法获取 client, 需要重新登录")
        sess = asyncio.run(get_session())
        c = sess.get_client(base_url, api_base="")
        if not c:
            print("❌ 重登后仍然无法获取 client")
            return 1

    print(f"✅ Client: {c.base_url}")
    API = api_base  # /estack/api/estack

    ak_name = f"AT_CRUD_{uuid.uuid4().hex[:6]}"
    created_id = None

    # ---- 3. CREATE ----
    print(f"\n【CREATE】POST {API}/draco/v1/accesskey/create")
    try:
        resp = c.post(f"{API}/draco/v1/accesskey/create",
                      json={"name": ak_name, "remark": "e2e-crud-v2"})
        body = resp.json() if resp.text else {}
        check(f"HTTP {resp.status_code}", resp.status_code < 300)
        check("success==true", body.get("success") is True, f"errorCode={body.get('errorCode')}")
        check(f"响应结构: {list(body.keys()) if body else 'empty'}", True)
        if body.get("success"):
            entity = body.get("entity") or body.get("data") or {}
            check(f"entity 字段: {list(entity.keys()) if entity else 'empty'}", True)
            created_id = entity.get("id") or entity.get("accessKeyId") or entity.get("keyId") or entity.get("secretKey")
            # 也可能在 entity 下面还有一层
            if not created_id and isinstance(entity, dict):
                for sub_key in ("record", "result", "data", "item"):
                    sub = entity.get(sub_key)
                    if isinstance(sub, dict):
                        created_id = sub.get("id") or sub.get("accessKeyId")
                        if created_id:
                            break
            check("有 created_id 或 accessKey", bool(created_id), f"entity={json.dumps(entity, ensure_ascii=False)[:300]}")
    except Exception as e:
        check(f"请求异常: {e}", False)
        return 1

    if not created_id:
        check("无法继续 CRUD", False, "没有 created_id")
        return 1

    # ---- 4. LIST ----
    print(f"\n【LIST】POST {API}/draco/v1/accesskey/list")
    resp2 = c.post(f"{API}/draco/v1/accesskey/list",
                   json={"pageNum": 1, "pageSize": 50})
    body2 = resp2.json() if resp2.text else {}
    # accesskey list 返回分页: entity.total/list/pageNum...
    entity2 = body2.get("entity", {}) if isinstance(body2.get("entity"), dict) else {}
    items = entity2.get("list") or entity2.get("records") or body2.get("data") or []
    items = items if isinstance(items, list) else []
    # 列表项字段: accessKeyId / userName / status, 没有 name
    found = any(item.get("accessKeyId") == created_id for item in items)
    check(f"列表中找到 created_id", found, f"共 {len(items)} 项")

    # ---- 5. DETAIL ----
    print("\n【DETAIL】查找详情")
    detail = None
    for item in items:
        if item.get("accessKeyId") == created_id:
            detail = item
            break
    check("找到详情记录", bool(detail))
    if detail:
        status_val = detail.get("status") or detail.get("state") or "N/A"
        check(f"状态: {status_val}", True)
        check(f"详情字段: {list(detail.keys())}", True)

    # ---- 6. DELETE/CLEANUP ----
    print(f"\n【CLEANUP】DELETE {API}/draco/v1/accesskey/{created_id}")
    resp4 = c.request("DELETE",
                      f"{API}/draco/v1/accesskey/{created_id}")
    if resp4.status_code < 300:
        check("DELETE 成功", True)
    else:
        # 兜底: disable
        resp5 = c.post(f"{API}/draco/v1/accesskey/disable",
                       json={"id": created_id})
        check(f"DISABLE 兜底 (HTTP {resp5.status_code})", resp5.status_code < 300)

    print("\n" + "=" * 60)
    print("✅ CRUD 端到端验证完成")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    exit(main())
