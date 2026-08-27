"""
角色管理_API测试.py — 由 module_discovery 自动生成
生成时间: 2026-08-27 17:50:34
适用版本: v1.0.0
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/role

执行流程:
  1. 创建
  2. 修改
  3. 删除

状态断言:
  - 删除后数据不应出现
"""

import sys, json, time, os
from pathlib import Path

# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）
_root = Path(__file__).resolve()
while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

import requests
import urllib3
from lib.auth import AuthSession

# 禁用 SSL 警告（自签证书环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ========== 配置 ==========
BASE_URL = "https://10.151.61.248"
LOGIN_URL = "https://10.151.61.248/estack/web/estack/login"
TARGET_URL = "https://10.151.61.248/estack/web/estack/user-center/user-manage/role"
TS = format(int(time.time() * 1000), "x")[-6:]

# ========== 测试数据（来自 Stage 2 捕获的 create 请求体）==========
test_data = {
  "tenantId": "cf2ecc23183244d8959023943338ac98",
  "policyName": "autotest87823612",
  "description": "自动测试创建于823620",
  "policyCategory": "ORGANIZATION",
  "menuIdList": [
    "all-resource",
    "a92d9efd05da4b1db5e3ee2f630e82d7",
    "2dbfe534b60d46189c727ab9a1613c31",
    "ccc0450fd0174f66b55f49c3632a4992",
    "fae3d97810c94a1482b227ecd26c3245",
    "ed76f32fd324468ea41b3648a8820df7",
    "b89a5e55185c4221948f400650c0c295",
    "573d7fa496684d4a9f5971dbe2caad5d",
    "b2dc70c7f80849d89c7826345977a4a6",
    "6e293f4a1f664208ab4b301c58b9fb44",
    "8363eea87dd34a7ab05835d787dc466d",
    "d9c6fabfa3dd42e794c79b660634c90b",
    "83cb6834fa444d738e03a26ff1122119",
    "2c72d9a9f5c4446c8a006d0e722d1f58",
    "36c325679bdf4997a10d728f76388a30",
    "b15cfad201554aabadbd250e364b9721",
    "ac7d649c721a4314a67bad03848a42ca",
    "3312b058fa894996960ef3ad951dd1da",
    "f6dad81fdd9a4cb28be4fcac4285d656",
    "a1ef3ab0e0704367aa8548ae1dbaa2e7",
    "ce71e113b1ae484dae21f93d35c0fc96",
    "00fd4034e21f4c9fb68e42932d169a65",
    "62a4d9110008467180f9bb61eb8d5b3a",
    "647d881e603341d0b03783551a25a675",
    "d58a037291c14257a4d9a8cb97b70df0",
    "5188297991d946f78bba1df27f509c8e",
    "6191622cfa2241d0aaea991be304d321",
    "b921a6c5018146f180bb6ad63425aa5f",
    "43c6f09a2750447cb23f4c57e02f8ab5",
    "d3d84a0fb13f4ef8b6018958f17d9038",
    "775d69a08f60425a9c9162f5b279fb65",
    "7817d9ba9a594601937f44486fa1638b",
    "87b95f407d8d40a9b0f8f77a259f07a9",
    "657309ff5fc8481d8d31fb5ad6ae4aa0",
    "d5d72305d6f745abab87484896522756",
    "91c45f372ae34f36905a26706c1adea6",
    "aa7fbebe5512478189ca65ddc0e52188",
    "da036e06d69a435e8a40191af84f6682",
    "4fd4b119c6bf46afa2994b879218270b",
    "3f921bc4b84f4774a76475e7432890db",
    "3375fec9a10643769b9e830df79a4874",
    "8d46f545e5e145a38ffcdda832bb69aa",
    "4ad620b66f6d498ead44b40c3ee98b1f",
    "365acbab232e44e1a88f3df622f0427f",
    "8a60979c2a1d4390ab0d017ecb6b4334",
    "60597817b8e34d4595a940e19a632220",
    "c07b73c8f2f24ec783c64a6ed5395fde",
    "c7ef52f7cedf4fa4b6ab837377ca4fec",
    "178e64c3c36d4470bf76c8c82d40a40e",
    "3b8d12c96b0d4b4daf1bab3a8371bc48",
    "07845ff1e0454cfa91c232778172ee50",
    "b9ba6e31bfc8499d8376d8e3d89e5d1d",
    "c5f728a9616d4dcdaac9845e00e4da7a",
    "e367f36085d34d29bd040c552cb6a5b6",
    "257c5dc4bc824e1c9183887485ab4b0a",
    "e3c54fc65514497784a6f7352f5f46f0",
    "b3f4f356da4e45cbb00817e996d47779",
    "fd74c14f8c924fc39f52f4df376f71ec",
    "dee3f9266a264706b232ab4c321bf695",
    "91cc9b68c0394312903a269f99a19019",
    "4e15bf6d440b4462b1dd3ef801d371dc",
    "d4d3df4c79664188a35640efa408e986",
    "60670423261744f9a68009944fa26861",
    "9f1b1143d6d240fd8dcde58f3b72e6ce",
    "e9be7a734de6453dac935fce138bb971",
    "a72e01f8b8c4419495fca22be3fb25ae",
    "76cd8d79ff2f4e9db550c2cc544e30c0"
  ],
  "policyDocument": "{\"version\":\"v1\",\"statement\":[{\"effect\":\"allow\",\"action\":[\"WORKFLOW:*\"],\"resource\":[\"*\"]}]}"
}


# ========== 鉴权 ==========
def get_auth_session():
    """获取带有效鉴权的 requests.Session（Cookie优先 → 滑块兜底）。"""
    username = os.environ.get("ESTACK_USER") or 'estack-yy'
    password = os.environ.get("ESTACK_PASS") or 'R@9eDuck$!mpleM00n'

    profile = {
        "login_url": LOGIN_URL,
        "auth": {
            "header_name": "Authorization",
            "header_prefix": "Bearer ",
            "freshness_ttl_seconds": 300,
            "fixed_headers": {"Estack-Language": "zh-CN"},
        },
        "captcha": {
            "auth_button_text": "点击完成认证",
            "login_button_text": "登录",
        },
        "credentials": {"username": 'estack-yy', "password": 'R@9eDuck$!mpleM00n'},
        "probe_url": "/estack/api/estack/draco/v1/users/current-user",
    }

    sess = AuthSession(profile, username, password)
    ctx_dir = Path(__file__).resolve().parent.parent / "output" / "config"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    sess.set_context_path(str(ctx_dir / "context.json"))

    session = requests.Session()
    session.verify = False
    session.headers.update({"Estack-Language": "zh-CN", "Content-Type": "application/json"})

    # 尝试已有 cookie
    sess.load_context()
    if sess.token and sess.probe_online(BASE_URL):
        session.headers["Authorization"] = f"Bearer {sess.token}"
        for c in sess._cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        print("  ✅ 使用已有鉴权")
        return session

    # 自动登录
    if os.environ.get("AUTO_LOGIN", "1") != "0":
        client = sess.ensure_client(BASE_URL)
        if client and sess.token:
            session.headers["Authorization"] = f"Bearer {sess.token}"
            for c in sess._cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
            print("  ✅ 自动登录成功")
            return session

    print("❌ 无法获取有效鉴权")
    print("   请设置 AUTO_LOGIN=1 或先运行发现流程刷新 cookie")
    return None


# ========== 响应封装 ==========
class ApiResponse:
    """包装 requests.Response，提供统一的成功判断。"""
    def __init__(self, resp: requests.Response):
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
        """estack 真成功标准：HTTP < 300 + success=true + 无 errorCode。"""
        if self.status >= 300:
            return False
        if self._json and isinstance(self._json, dict):
            return bool(self._json.get("success")) and not self._json.get("errorCode")
        return self.status < 300

    def __repr__(self):
        return f"<ApiResponse status={self.status}, success={self.success}>"


# ========== API 调用函数（来自 Stage 2 捕获的端点）==========
def api_create_policies(session, url=None, **kwargs):
    """create: /estack/api/estack/draco/v1/policies"""
    if url is None:
        url = BASE_URL + "/estack/api/estack/draco/v1/policies"
    else:
        url = BASE_URL + url
    if "{id}" in url:
        id_val = kwargs.pop("id", None)
        if id_val:
            url = url.replace("{id}", str(id_val))
            # 只有 PUT/PATCH 的 body 需要 id（Phase 2 body sample 含 id 字段）
            # DELETE body 不含 id，不能多传
            if "POST" in ("PUT", "PATCH"):
                kwargs["id"] = id_val
    try:
        req_kwargs = {}
        if kwargs:
            if "POST" == "GET":
                req_kwargs["params"] = kwargs
            elif "POST" == "DELETE" and kwargs:
                # DELETE 可能需要 body（某些 API 设计）
                req_kwargs["json"] = kwargs
            else:
                req_kwargs["json"] = kwargs

        resp = session.post(url, **req_kwargs)

        # 打印请求/响应详情
        try:
            _req_body = resp.request.body or ""
            if isinstance(_req_body, bytes):
                _req_body = _req_body.decode("utf-8", errors="replace")
            print(f"    >>> {resp.request.method} {resp.request.url}")
            if _req_body:
                try:
                    print(f"    body: {json.dumps(json.loads(_req_body), ensure_ascii=False, indent=2)}")
                except Exception:
                    print(f"    body: {_req_body[:500]}")
            print(f"    <<< {resp.status_code} {resp.headers.get('content-type', '')}")
            if resp.text:
                try:
                    _rb = json.loads(resp.text)
                    print(f"    success: {_rb.get('success', '-')}")
                    if _rb.get("errorMessage"):
                        print(f"    error: {_rb['errorMessage']}")
                    print(f"    body: {json.dumps(_rb, ensure_ascii=False, indent=2)[:2000]}")
                except Exception:
                    print(f"    body: {resp.text[:500]}")
        except Exception:
            pass

        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = str(e)
        r._json = {"success": False}
        return r

def api_update_policies(session, url=None, **kwargs):
    """update: /estack/api/estack/draco/v1/policies/{id}"""
    if url is None:
        url = BASE_URL + "/estack/api/estack/draco/v1/policies/{id}"
    else:
        url = BASE_URL + url
    if "{id}" in url:
        id_val = kwargs.pop("id", None)
        if id_val:
            url = url.replace("{id}", str(id_val))
            # 只有 PUT/PATCH 的 body 需要 id（Phase 2 body sample 含 id 字段）
            # DELETE body 不含 id，不能多传
            if "PUT" in ("PUT", "PATCH"):
                kwargs["id"] = id_val
    try:
        req_kwargs = {}
        if kwargs:
            if "PUT" == "GET":
                req_kwargs["params"] = kwargs
            elif "PUT" == "DELETE" and kwargs:
                # DELETE 可能需要 body（某些 API 设计）
                req_kwargs["json"] = kwargs
            else:
                req_kwargs["json"] = kwargs

        resp = session.put(url, **req_kwargs)

        # 打印请求/响应详情
        try:
            _req_body = resp.request.body or ""
            if isinstance(_req_body, bytes):
                _req_body = _req_body.decode("utf-8", errors="replace")
            print(f"    >>> {resp.request.method} {resp.request.url}")
            if _req_body:
                try:
                    print(f"    body: {json.dumps(json.loads(_req_body), ensure_ascii=False, indent=2)}")
                except Exception:
                    print(f"    body: {_req_body[:500]}")
            print(f"    <<< {resp.status_code} {resp.headers.get('content-type', '')}")
            if resp.text:
                try:
                    _rb = json.loads(resp.text)
                    print(f"    success: {_rb.get('success', '-')}")
                    if _rb.get("errorMessage"):
                        print(f"    error: {_rb['errorMessage']}")
                    print(f"    body: {json.dumps(_rb, ensure_ascii=False, indent=2)[:2000]}")
                except Exception:
                    print(f"    body: {resp.text[:500]}")
        except Exception:
            pass

        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = str(e)
        r._json = {"success": False}
        return r

def api_delete_policies(session, url=None, **kwargs):
    """delete: /estack/api/estack/draco/v1/policies/{id}"""
    if url is None:
        url = BASE_URL + "/estack/api/estack/draco/v1/policies/{id}?tenantId=cf2ecc23183244d8959023943338ac98"
    else:
        url = BASE_URL + url
    if "{id}" in url:
        id_val = kwargs.pop("id", None)
        if id_val:
            url = url.replace("{id}", str(id_val))
            # 只有 PUT/PATCH 的 body 需要 id（Phase 2 body sample 含 id 字段）
            # DELETE body 不含 id，不能多传
            if "DELETE" in ("PUT", "PATCH"):
                kwargs["id"] = id_val
    try:
        req_kwargs = {}
        if kwargs:
            if "DELETE" == "GET":
                req_kwargs["params"] = kwargs
            elif "DELETE" == "DELETE" and kwargs:
                # DELETE 可能需要 body（某些 API 设计）
                req_kwargs["json"] = kwargs
            else:
                req_kwargs["json"] = kwargs

        resp = session.delete(url, **req_kwargs)

        # 打印请求/响应详情
        try:
            _req_body = resp.request.body or ""
            if isinstance(_req_body, bytes):
                _req_body = _req_body.decode("utf-8", errors="replace")
            print(f"    >>> {resp.request.method} {resp.request.url}")
            if _req_body:
                try:
                    print(f"    body: {json.dumps(json.loads(_req_body), ensure_ascii=False, indent=2)}")
                except Exception:
                    print(f"    body: {_req_body[:500]}")
            print(f"    <<< {resp.status_code} {resp.headers.get('content-type', '')}")
            if resp.text:
                try:
                    _rb = json.loads(resp.text)
                    print(f"    success: {_rb.get('success', '-')}")
                    if _rb.get("errorMessage"):
                        print(f"    error: {_rb['errorMessage']}")
                    print(f"    body: {json.dumps(_rb, ensure_ascii=False, indent=2)[:2000]}")
                except Exception:
                    print(f"    body: {resp.text[:500]}")
        except Exception:
            pass

        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = str(e)
        r._json = {"success": False}
        return r


def step_create(session, state):
    """CRUD 步骤: create"""
    print("\n[创建] /estack/api/estack/draco/v1/policies")
    try:
        create_body = state["create_body"]
        resp = api_create_policies(session, **create_body)
        # 四层断言：HTTP状态码 + 业务成功 + 响应体 + ID提取
        assert 200 <= resp.status < 300, f"创建失败: HTTP {resp.status}"
        assert resp.success, f"创建失败: 业务错误 {resp.text[:200]}"
        assert resp.json, "创建失败: 无响应体"
        entity = resp.json.get("entity") or resp.json.get("data")
        assert entity, "创建失败: 响应中无 entity/data"
        state['id'] = entity.get('id')
        assert state['id'], '创建失败: 未返回 id'
        # 保存名称字段供 update 步骤使用
        for _nk in ('policyName', 'userName', 'name'):
            if _nk in create_body:
                state[_nk] = create_body[_nk]
            if _nk in entity:
                state[_nk] = entity[_nk]
        print(f"  ✅ 创建成功, id={state['id']}")
    except AssertionError as _ae:
        print(f"  ❌ 创建断言失败: {_ae}")
    except Exception as _ce:
        print(f"  ⚠️ 创建异常: {_ce}")

def step_update(session, state):
    """CRUD 步骤: update"""
    print("\n[修改] /estack/api/estack/draco/v1/policies/{id}")
    if not state['id']:
        print("  ⚠️ 跳过[修改]: id 为空（创建未成功）")
        return
    try:
        # 只发送 Phase 2 update body sample 的字段（忠实对应）
        _update_keys = ['id', 'policyName', 'description', 'tenantId']
        update_body = {k: state["create_body"][k] for k in _update_keys if k in state.get("create_body", {})}
        update_body["id"] = state["id"]
        update_body["description"] = f"自动修改_{TS}"
        # 保持名称字段与创建时一致
        for _nk in ("policyName", "userName", "name"):
            if state.get(_nk) and _nk in update_body:
                update_body[_nk] = state[_nk]
        resp = api_update_policies(session, **update_body)
        assert 200 <= resp.status < 300, f"修改失败: HTTP {resp.status}"
        assert resp.success, f"修改失败: 业务错误 {resp.text[:200]}"
        assert resp.json, "修改失败: 无响应体"
        print("  ✅ 修改成功")
    except AssertionError as _ae:
        print(f"  ❌ 修改断言失败: {_ae}")
    except Exception as _ue:
        print(f"  ⚠️ 修改异常: {_ue}")

def step_delete(session, state):
    """CRUD 步骤: delete"""
    print("\n[删除] /estack/api/estack/draco/v1/policies/{id}")
    try:
        if not state['id']:
            print("  ⚠️ 跳过[删除]: id 为空")
            return
        delete_body = {'roleId': state['id'], 'tenantId': state.get('create_body', {}).get('tenantId', state.get('tenantId', ''))}
        resp = api_delete_policies(session, id=state["id"], **delete_body)
        assert 200 <= resp.status < 300, f"删除失败: HTTP {resp.status}"
        assert resp.success, f"删除失败: 业务错误 {resp.text[:200]}"
        assert resp.json, "删除失败: 无响应体"
        print("  ✅ 删除成功")
    except AssertionError as _ae:
        print(f"  ❌ 删除断言失败: {_ae}")
    except Exception as _de:
        print(f"  ⚠️ 删除异常: {_de}")


# ========== 步骤字典（支持单步骤执行: python script.py create update）==========
STEPS = {"create": step_create, "update": step_update, "delete": step_delete}


def main():
    """主流程：鉴权 → 获取当前用户 → 按顺序执行 CRUD 步骤。"""
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print("=" * 60)
    print("  角色管理 API 测试")
    print("=" * 60)

    session = get_auth_session()
    if session is None:
        return

    # 共享状态（跨步骤传递数据）
    state = {"id": None, "uname": None, "tenantId": None, "adminId": None,
             "policyName": None}

    # 获取当前用户信息
    try:
        me = session.get(f"{BASE_URL}/estack/api/estack/draco/v1/users/current-user")
        if me.status_code < 300:
            me_json = me.json()
            me_entity = me_json.get("entity", {}) or {}
            state["tenantId"] = me_entity.get("tenantId")
            state["adminId"] = me_entity.get("id")
            print(f"  ✅ 当前用户: {me_entity.get('name', 'unknown')}")
            print(f"  tenantId: {state['tenantId']}  adminId: {state['adminId']}")
    except Exception as _me:
        print(f"  ⚠️ 获取当前用户失败: {_me}")

    # 构建 create body：动态替换名称字段（保留 Stage 2 捕获的 tenantId）
    create_body = dict(test_data)
    for k in ("policyName", "userName", "name", "account"):
        if k in create_body and isinstance(create_body[k], str):
            if not create_body[k].startswith("AT_"):
                create_body[k] = "AT_" + create_body[k]
            create_body[k] = f"AT_{TS}_" + create_body[k][3:] if create_body[k].startswith("AT_") else "AT_" + create_body[k]
    state["create_body"] = create_body

    # 解析命令行参数（支持单步骤执行）
    all_steps = list(STEPS.keys())
    if len(sys.argv) > 1:
        steps_to_run = sys.argv[1:]
        for name in steps_to_run:
            if name not in STEPS:
                print(f"❌ 未知步骤: {name}")
                print(f"可用步骤: {', '.join(all_steps)}")
                sys.exit(1)
    else:
        steps_to_run = all_steps

    print(f"\n🚀 执行步骤: {', '.join(steps_to_run)} (共 {len(steps_to_run)}/{len(all_steps)})")

    try:
        for _step_name in steps_to_run:
            print(f"\n{'=' * 60}")
            print(f"步骤: {_step_name}")
            print("=" * 60)
            STEPS[_step_name](session, state)
    except Exception as _main_err:
        print(f"\n❌ 主流程异常: {_main_err}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("  ✅ 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
