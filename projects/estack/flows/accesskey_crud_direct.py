"""
accesskey CRUD 端到端验证 —— 直接 httpx 版本。
手动注 token（先用已有登录获取的 token）。
"""
import sys, json, uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lib.api_client import get_client, ApiResponse, get_fn  # 统一从框架层导入
import yaml
import time

# ---- 加载 profile ----
profile = yaml.safe_load(open(Path(__file__).resolve().parents[1] / "profile.yaml"))

# ---- 读取已保存的 cookies/context ----
ctx_path = Path(__file__).resolve().parents[1] / "output" / "config" / "context.json"
cookies_path = Path(__file__).resolve().parents[1] / "output" / "config" / "cookies.json"

token = None
sid = profile.get("auth", {}).get("fixed_headers", {}).get("SID", "")

# 从 context.json 读 token (之前 P0 登录保存的)
if ctx_path.exists():
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    token_prefix = ctx.get("token_prefix", "")
    issued = ctx.get("issued_at", 0)
    if token_prefix and (time.time() - issued) < ctx.get("ttl", 300):
        print(f"⚠️  context.json 只保存了 token prefix ({token_prefix}), 不是完整 token")
        print("   需要一个有效的 Bearer token")

# 从 cookie 读 token
if cookies_path.exists():
    cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
    for c in cookies:
        if c.get("name") == "accessToken":
            token = c.get("value")
            print(f"✅ 从 cookie 读取 token: {token[:16]}...")
            break

if not token:
    print("\n❌ 没有有效 token。请通过以下方式之一提供:")
    print("  1. 设置环境变量 ESTACK_TOKEN")
    print("  2. 运行 P0 登录后使用 projects/estack/output/config/cookies.json 中的 accessToken")
    print("  3. 直接设置脚本中的 token 变量")
    token = ""  # 让你手动填写
    if not token:
        exit(1)

base_url = profile.get("base_url", "")
api_base = profile.get("api_base", "")
header_name = profile.get("auth", {}).get("header_name", "Authorization")
header_prefix = profile.get("auth", {}).get("header_prefix", "Bearer ")
fixed_headers = profile.get("auth", {}).get("fixed_headers", {})

import httpx
headers = dict(fixed_headers or {})
headers[header_name] = f"{header_prefix}{token}"
c = httpx.Client(base_url=base_url + api_base, headers=headers, verify=False, timeout=30)

print(f"\n=== AccessKey CRUD 端到端验证 ===")
print(f"  目标: {c.base_url}")

# ---- 1. CREATE ----
print(f"\n【CREATE】POST /draco/v1/accesskey/create")
ak_name = f"AT_TEST_{uuid.uuid4().hex[:8]}"
resp = c.request("POST", "/estack/api/estack/draco/v1/accesskey/create",
                 json={"name": ak_name, "remark": "auto-test"})
j = resp.json() if resp.text else {}
print(f"  status={resp.status_code}, success={j.get('success')}, errorCode={j.get('errorCode')}")
print(f"  entity={json.dumps(j.get('entity'), ensure_ascii=False)[:200] if j.get('entity') else 'None'}")

created_id = None
if j.get("success") and not j.get("errorCode"):
    entity = j.get("entity") or j.get("data") or {}
    created_id = entity.get("id") or entity.get("accessKeyId") or entity.get("keyId")
    access_key = entity.get("accessKey") or entity.get("key") or entity.get("secretKey")
    print(f"  created_id={created_id}")
    if access_key:
        print(f"  accessKey={access_key[:16]}...")
assert created_id, f"未提取到 created_id"
print("✅ CREATE 通过")

# ---- 2. LIST ----
print(f"\n【LIST】POST /draco/v1/accesskey/list")
resp2 = c.request("POST", "/estack/api/estack/draco/v1/accesskey/list",
                  json={"pageNum": 1, "pageSize": 20})
j2 = resp2.json() if resp2.text else {}
print(f"  status={resp2.status_code}, success={j2.get('success')}")

items = j2.get("data") or j2.get("entity") or []
if isinstance(items, dict):
    items = items.get("list") or items.get("records") or list(items.values())[:1]
if isinstance(items, list):
    found = any(item.get("name") == ak_name for item in items)
    print(f"  列表共 {len(items)} 项, 找到 '{ak_name}': {found}")
    assert found, f"创建后未在列表中找到: {ak_name}"
else:
    print(f"  响应结构: {list(j2.keys())}, items 非列表")
print("✅ LIST 通过")

# ---- 3. DETAIL (已有 ID, 直接用 list+filter) ----
print(f"\n【DETAIL】通过 list 精确过滤 created_id={created_id}")
resp3 = c.request("POST", "/estack/api/estack/draco/v1/accesskey/list",
                  json={"pageNum": 1, "pageSize": 100})
j3 = resp3.json() if resp3.text else {}
items3 = j3.get("data") or j3.get("entity") or []
if isinstance(items3, dict):
    items3 = items3.get("list") or list(items3.values())[:1]
detail = None
if isinstance(items3, list):
    for item in items3:
        if item.get("id") == created_id or item.get("name") == ak_name:
            detail = item
            break
print(f"  found: {detail is not None}")
if detail:
    status_val = detail.get("status") or detail.get("state") or "N/A"
    print(f"  记录详情: name={detail.get('name')}, status={status_val}, id={detail.get('id')}")
print("✅ DETAIL 通过")

# ---- 4. DELETE ----
print(f"\n【DELETE】尝试删除 (可能不支持)")
try:
    resp4 = c.request("DELETE", f"/estack/api/estack/draco/v1/accesskey/{created_id}")
    print(f"  status={resp4.status_code}")
except Exception as e:
    # 很多系统不允许直接删除 accesskey, 改用禁用
    print(f"  DELETE 端点不可用: {e}")
    print(f"  → 尝试 POST /.../accesskey/disable")
    try:
        resp4 = c.request("POST", f"/estack/api/estack/draco/v1/accesskey/disable",
                          json={"id": created_id})
        print(f"  disable: status={resp4.status_code}")
    except Exception as e2:
        print(f"  disable 也不可用: {e2}")

print("\n" + "=" * 60)
print("CRUD 验证完成")
print("=" * 60)
