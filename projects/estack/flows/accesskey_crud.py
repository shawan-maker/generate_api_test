"""
accesskey CRUD 端到端用例 —— 手写验证。

先获取 token（用 lib/auth.py 登录），再用 api 层薄函数做 CRUD 循环。
这不是 pytest 而是验证脚本, 成功后可作为 flows/ 的参考实现。
"""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib import auth, slider
from lib.api_client import get_client, ApiResponse, get_fn
proj_dir = Path(__file__).resolve().parents[1]
import yaml

# ---- 1. 加载 profile + 登录 ----
profile = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "profile.yaml"))
sess = auth.AuthSession(profile)
if not sess.have_credentials():
    print("请设置 ESTACK_USER / ESTACK_PASS 环境变量")
    exit(1)

print("=" * 60)
print("AccessKey CRUD 端到端验证")
print("=" * 60)

# ---- 2. 用 Playwright 获取 token（复用已有登录流程）----
import asyncio
from playwright.async_api import async_playwright


async def get_token():
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
            return None
        print(f"✅ 登录成功, token 已获取")
        return sess.token


token = asyncio.run(get_token())
if not token:
    exit(1)

# ---- 3. 创建 httpx client ----
c = get_client(proj_dir, token)
print(f"✅ Client 已创建 (base={c.base_url})")

# ---- 4. CRUD 验证 ----
def step(label, ok=True):
    icon = "✅" if ok else "❌"
    print(f"\n{icon} {label}")

# 4a. 创建 AccessKey
step("【CREATE】POST /draco/v1/accesskey/create")
draco_create_accesskey = get_fn("estack", "draco", "accesskey", "create")
draco_list_accesskey = get_fn("estack", "draco", "accesskey", "list")
import uuid
ak_name = f"AT_TEST_{uuid.uuid4().hex[:8]}"
resp = draco_create_accesskey(c, name=ak_name, remark="auto-test-crud")
print(f"  status={resp.status}, success={resp.success}")
if resp.json:
    print(f"  response: {json.dumps(resp.json, ensure_ascii=False)[:300]}")
assert resp.success, f"创建失败: {resp.json}"

# 提取 created id
j = resp.json
created_id = None
if j:
    created_id = j.get("data", {}).get("id") or j.get("id") or j.get("entity", {}).get("id")
    print(f"  created_id={created_id}")
step("CREATE 成功", bool(created_id))

# 4b. 查询列表
step("【LIST】POST /draco/v1/accesskey/list")
resp2 = draco_list_accesskey(c, pageNum=1, pageSize=20)
print(f"  status={resp2.status}, success={resp2.success}")
assert resp2.success, f"列表查询失败: {resp2.json}"

# 检查列表中有刚创建的 key
found = False
if resp2.json:
    items = resp2.json.get("data") or resp2.json.get("entity") or []
    for item in items if isinstance(items, list) else []:
        if item.get("name") == ak_name:
            found = True
            print(f"  在列表中找到: {item.get('name')} (id={item.get('id')})")
            break
    print(f"  列表共 {len(items)} 条" if isinstance(items, list) else f"  响应结构: {list(resp2.json.keys())}")
step("LIST 成功", found)

# 4c. 详情（如果没有专用 detail 端点，用 list+filter 代替）
step("【DETAIL】通过 list 精确过滤")
# accesskey 没有独立的 detail 端点，通过 list+created_id 验证
resp3 = draco_list_accesskey(c, pageNum=1, pageSize=50)
detail_found = None
if resp3.json:
    items = resp3.json.get("data") or resp3.json.get("entity") or []
    if isinstance(items, list):
        for item in items:
            if item.get("id") == created_id or item.get("name") == ak_name:
                detail_found = item
                # 状态校验: 检查 status/state 字段
                status_val = detail_found.get("status") or detail_found.get("state") or ""
                print(f"  status_field={status_val}")
                break
step(f"DETAIL 找到记录", bool(detail_found))

# 4d. 删除（accesskey 可能没有 delete 端点, 在这里先检查）
step("【CLEANUP】清理测试资源")
# 如果没有 delete 端点, 用后门或标记为待清理
# 先尝试直接 delete 端点
try:
    draco_delete_accesskey = get_fn("estack", "draco", "accesskey", "delete")
    resp4 = draco_delete_accesskey(c, id=created_id)
    print(f"  delete: status={resp4.status}")
except (ImportError, AttributeError):
    print(f"  ⚠️ 无 delete 函数 (accesskey 可能不支持删除)")
    print(f"  → 资源 {ak_name} (id={created_id}) 需手动清理或通过控制台删除")

print("\n" + "=" * 60)
print("CRUD 验证完成")
print("=" * 60)
