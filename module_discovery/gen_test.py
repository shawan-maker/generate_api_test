"""
gen_test.py — Stage 4: 生成可执行的 API 测试脚本

根据 Stage 2/3 的分析结果，生成包含：
  - CRUD 顺序执行
  - 数据依赖注入（自动传递 id）
  - 状态断言
  - 正确的鉴权初始化

使用 requests 库发送 HTTP 请求。
所有 API 调用参数从 Stage 2 捕获的 request_body_sample 驱动生成。
"""

import json
import re
import time
import logging
from pathlib import Path
from typing import Optional, List
from . import const

# Jinja2 可选依赖（渐进式迁移）
try:
    from jinja2 import Environment, FileSystemLoader
    _JINJA2_AVAILABLE = True
    _TEMPLATE_DIR = Path(__file__).parent / "templates"
except ImportError:
    _JINJA2_AVAILABLE = False

LOG = logging.getLogger("gen_test")


# ========== Jinja2 模板辅助 ==========

def _render_with_jinja(template_name: str, **kwargs) -> Optional[str]:
    """Jinja2 模板渲染（失败返回 None，调用方回退到字符串拼接）。"""
    if not _JINJA2_AVAILABLE or not _TEMPLATE_DIR.exists():
        return None
    try:
        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            keep_trailing_newline=True,
        )
        template = env.get_template(template_name)
        return template.render(**kwargs)
    except Exception as e:
        LOG.warning(f"Jinja2 模板渲染失败，回退到字符串拼接: {e}")
        return None


# ========== Query 函数选择 ==========

def _select_query_fn(core_apis: dict) -> Optional[str]:
    """选择用于状态验证的查询函数名（多策略选择）。

    优先级：列表查询 > 详情查询 > 任意 query 端点
    """
    query_eps = core_apis.get("query", [])
    if not query_eps:
        return None

    list_fn = None
    detail_fn = None
    any_fn = None

    for ep in query_eps:
        pathname = ep["pathname"]
        clean_path = re.sub(r"[0-9a-f]{32}", "", pathname)
        segments = [s for s in clean_path.strip("/") if s]
        resource_name = segments[-1] if segments else "query"
        safe_name = resource_name.replace("-", "_").replace(".", "_")
        fn_name = f"api_query_{safe_name}"

        if not re.search(r"[0-9a-f]{20,}", pathname):
            if any(kw in pathname.lower() for kw in ("/list", "/page", "/search", "/all")):
                return fn_name
            if list_fn is None:
                list_fn = fn_name
        elif detail_fn is None:
            detail_fn = fn_name

        if any_fn is None:
            any_fn = fn_name

    return list_fn or detail_fn or any_fn


# ========== 主入口 ==========

def generate_script(analysis: dict, api_capture: dict, ui_result: dict,
                    project_id: str, module_name: str, base_url: str,
                    login_url: str, target_url: str,
                    username: str = "", password: str = "",
                    version: str = "") -> str:
    """生成可执行的 Python API 测试脚本。"""

    crud_order = analysis.get("crud_order", [])
    core_apis = analysis.get("core_apis", {})
    state_assertions = analysis.get("state_assertions", {})
    dependencies = analysis.get("dependencies", {})

    state_field = state_assertions.get("state_field")
    after_create = state_assertions.get("after_create")
    after_lock = state_assertions.get("after_lock")
    after_unlock = state_assertions.get("after_unlock")

    L = []  # 输出行列表

    # ========== 文件头 ==========
    L.append('"""')
    L.append(f'{module_name}_API测试.py — 由 module_discovery 自动生成')
    L.append(f'生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}')
    if version:
        L.append(f'适用版本: {version}')
    L.append(f'目标URL: {target_url}')
    L.append('')
    L.append('执行流程:')
    step_names = {
        "create": "创建", "query": "查询", "detail": "详情",
        "update": "修改", "lock": "锁定", "unlock": "解锁",
        "delete": "删除", "export": "导出", "execute": "其他操作",
    }
    for i, step in enumerate(crud_order, 1):
        L.append(f'  {i}. {step_names.get(step, step)}')
    if state_assertions:
        L.append('')
        L.append('状态断言:')
        if after_create:
            L.append(f'  - 创建后 state 应为 "{after_create}"')
        if after_lock:
            L.append(f'  - 锁定后 state 应为 "{after_lock}"')
        if after_unlock:
            L.append(f'  - 解锁后 state 应为 "{after_unlock}"')
        if state_assertions.get("after_delete") == "NOT_EXIST":
            L.append('  - 删除后数据不应出现')
    L.append('"""')
    L.append('')

    # ========== 导入 ==========
    L.append('import sys, json, time, os')
    L.append('from pathlib import Path')
    L.append('')
    L.append('# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）')
    L.append('_root = Path(__file__).resolve()')
    L.append("while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():")
    L.append('    _root = _root.parent')
    L.append('sys.path.insert(0, str(_root))')
    L.append('')
    L.append('import requests')
    L.append('import urllib3')
    L.append('from lib.auth import AuthSession')
    L.append('')
    L.append('# 禁用 SSL 警告（自签证书环境）')
    L.append('urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)')
    L.append('')

    # ========== 配置 ==========
    L.append('# ========== 配置 ==========')
    L.append(f'BASE_URL = "{base_url}"')
    L.append(f'LOGIN_URL = "{login_url}"')
    L.append(f'TARGET_URL = "{target_url}"')
    L.append('TS = format(int(time.time() * 1000), "x")[-6:]')
    L.append('')

    # ========== 测试数据 ==========
    # 从创建 API 的请求体样本提取参数结构
    create_apis = core_apis.get("create", [])
    create_body_sample = None
    if create_apis:
        create_body_sample = create_apis[0].get("request_body_sample") or (
            (create_apis[0].get("bodies") or [None])[0])

    L.append('# ========== 测试数据（来自 Stage 2 捕获的 create 请求体）==========')
    if create_body_sample:
        try:
            body = json.loads(create_body_sample) if isinstance(create_body_sample, str) else create_body_sample
            L.append(f'test_data = {json.dumps(body, indent=2, ensure_ascii=False)}')
        except Exception:
            L.append('test_data = {}')
    else:
        L.append('test_data = {}')
    L.append('')

    # ========== 鉴权 ==========
    L.append('''
# ========== 鉴权 ==========
def get_auth_session():
    """获取带有效鉴权的 requests.Session（Cookie优先 → 滑块兜底）。"""
    username = os.environ.get("ESTACK_USER") or "__USERNAME__"
    password = os.environ.get("ESTACK_PASS") or "__PASSWORD__"

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
        "credentials": {"username": "__USERNAME__", "password": "__PASSWORD__"},
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
''')

    # ========== ApiResponse ==========
    L.append('''
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
''')

    # ========== API 调用函数 ==========
    L.append('')
    L.append('# ========== API 调用函数（来自 Stage 2 捕获的端点）==========')

    query_fn_for_verify = _select_query_fn(core_apis)
    seen_fns = set()
    fn_defs = {}  # {step: (fn_name, pathname, method, body_sample)}

    for step in crud_order:
        if step not in core_apis:
            continue
        for ep in core_apis[step]:
            method = ep["method"]
            pathname = ep["pathname"]
            body_sample = ep.get("request_body_sample") or (
                (ep.get("bodies") or [None])[0])

            # 生成安全的函数名
            clean_path = re.sub(r"[0-9a-f]{32}", "", pathname)
            segments = [s for s in clean_path.strip("/").split("/") if s]
            resource_name = segments[-1] if segments else step
            safe_name = resource_name.replace("-", "_").replace(".", "_")
            fn_name = f"api_{step}_{safe_name}"

            if fn_name in seen_fns:
                continue
            seen_fns.add(fn_name)

            # URL 中的真实 ID → {id} 占位
            path_for_url = re.sub(r"[0-9a-f]{32}", "{id}", pathname)

            fn_defs[step] = (fn_name, pathname, method, body_sample, ep.get("query_params", {}))
            L.append(_gen_api_function(fn_name, step, path_for_url, method, ep.get("query_params", {})))

    L.append('')

    # ========== CRUD 步骤函数 ==========
    step_functions = {}

    for step in crud_order:
        if step not in fn_defs:
            continue
        fn_name, pathname, method, body_sample, query_params = fn_defs[step]
        step_fn_name = f"step_{step}"
        step_functions[step] = step_fn_name

        if step == "create":
            L.append(_gen_step_create(fn_name, pathname, body_sample,
                                      query_fn_for_verify, state_field, after_create))
        elif step == "update":
            L.append(_gen_step_update(fn_name, pathname, body_sample))
        elif step == "delete":
            L.append(_gen_step_delete(fn_name, pathname, body_sample,
                                      query_fn_for_verify, query_params))
        elif step == "query":
            L.append(_gen_step_query(fn_name, pathname, body_sample,
                                      state_field, after_lock))
        elif step == "lock":
            L.append(_gen_step_lock(fn_name, pathname, body_sample,
                                     query_fn_for_verify, state_field, after_lock))
        elif step == "unlock":
            L.append(_gen_step_unlock(fn_name, pathname, body_sample,
                                       query_fn_for_verify, state_field, after_unlock))
        else:
            L.append(_gen_step_generic(step, fn_name, pathname, body_sample))

    # ========== STEPS + main() ==========
    steps_entries = ", ".join(f'"{s}": {fn}' for s, fn in step_functions.items())
    L.append(f'''
# ========== 步骤字典（支持单步骤执行: python script.py create update）==========
STEPS = {{{steps_entries}}}


def main():
    """主流程：鉴权 → 获取当前用户 → 按顺序执行 CRUD 步骤。"""
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print("=" * 60)
    print("  __MODULE_NAME__ API 测试")
    print("=" * 60)

    session = get_auth_session()
    if session is None:
        return

    # 共享状态（跨步骤传递数据）
    state = {{"id": None, "uname": None, "tenantId": None, "adminId": None,
             "policyName": None}}

    # 获取当前用户信息
    try:
        me = session.get(f"{{BASE_URL}}/estack/api/estack/draco/v1/users/current-user")
        if me.status_code < 300:
            me_json = me.json()
            me_entity = me_json.get("entity", {{}}) or {{}}
            state["tenantId"] = me_entity.get("tenantId")
            state["adminId"] = me_entity.get("id")
            print(f"  ✅ 当前用户: {{me_entity.get('name', 'unknown')}}")
            print(f"  tenantId: {{state['tenantId']}}  adminId: {{state['adminId']}}")
    except Exception as _me:
        print(f"  ⚠️ 获取当前用户失败: {{_me}}")

    # 构建 create body：动态替换名称字段（保留 Stage 2 捕获的 tenantId）
    create_body = dict(test_data)
    for k in ("policyName", "userName", "name", "account"):
        if k in create_body and isinstance(create_body[k], str):
            if not create_body[k].startswith("AT_"):
                create_body[k] = "AT_" + create_body[k]
            create_body[k] = f"AT_{{TS}}_" + create_body[k][3:] if create_body[k].startswith("AT_") else "AT_" + create_body[k]
    state["create_body"] = create_body

    # 解析命令行参数（支持单步骤执行）
    all_steps = list(STEPS.keys())
    if len(sys.argv) > 1:
        steps_to_run = sys.argv[1:]
        for name in steps_to_run:
            if name not in STEPS:
                print(f"❌ 未知步骤: {{name}}")
                print(f"可用步骤: {{', '.join(all_steps)}}")
                sys.exit(1)
    else:
        steps_to_run = all_steps

    print(f"\\n🚀 执行步骤: {{', '.join(steps_to_run)}} (共 {{len(steps_to_run)}}/{{len(all_steps)}})")

    try:
        for _step_name in steps_to_run:
            print(f"\\n{{'=' * 60}}")
            print(f"步骤: {{_step_name}}")
            print("=" * 60)
            STEPS[_step_name](session, state)
    except Exception as _main_err:
        print(f"\\n❌ 主流程异常: {{_main_err}}")
        import traceback
        traceback.print_exc()

    print("\\n" + "=" * 60)
    print("  ✅ 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
''')

    script = "\n".join(L)

    # 注入凭据和模块名
    script = script.replace('"__USERNAME__"', repr(username or "estack-yy"))
    script = script.replace('"__PASSWORD__"', repr(password or "R@9eDuck$!mpleM00n"))
    script = script.replace("__MODULE_NAME__", module_name or "模块")

    return script


# ========== 步骤函数生成器 ==========

def _gen_step_create(fn_name: str, pathname: str, body_sample,
                      query_fn_for_verify: Optional[str],
                      state_field: Optional[str], after_create: Optional[str]) -> str:
    """生成 step_create 函数代码。"""
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines = []
    lines.append(f'def step_create(session, state):')
    lines.append(f'    """CRUD 步骤: create"""')
    lines.append(f'    print("\\n[创建] {clean_pathname}")')
    lines.append(f'    try:')
    lines.append(f'        create_body = state["create_body"]')
    lines.append(f'        resp = {fn_name}(session, **create_body)')
    lines.append(f'        # 四层断言：HTTP状态码 + 业务成功 + 响应体 + ID提取')
    lines.append(f'        assert 200 <= resp.status < 300, f"创建失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"创建失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "创建失败: 无响应体"')
    lines.append(f'        entity = resp.json.get("entity") or resp.json.get("data")')
    lines.append(f'        assert entity, "创建失败: 响应中无 entity/data"')
    lines.append(f"        state['id'] = entity.get('id')")
    lines.append(f"        assert state['id'], '创建失败: 未返回 id'")
    lines.append(f'        # 保存名称字段供 update 步骤使用')
    lines.append(f"        for _nk in ('policyName', 'userName', 'name'):")
    lines.append(f"            if _nk in create_body:")
    lines.append(f"                state[_nk] = create_body[_nk]")
    lines.append(f'            if _nk in entity:')
    lines.append(f"                state[_nk] = entity[_nk]")
    lines.append(f"        print(f\"  ✅ 创建成功, id={{state['id']}}\")")

    # 创建后状态验证
    if after_create and query_fn_for_verify:
        lines.append(f'        # 验证创建后状态')
        lines.append(f'        resp_check = {query_fn_for_verify}(session)')
        lines.append(f'        if resp_check.success and resp_check.json:')
        lines.append(f'            check_data = resp_check.json.get("entity") or resp_check.json.get("data") or []')
        lines.append(f'            if isinstance(check_data, list):')
        lines.append(f"                item = next((i for i in check_data if i.get('id') == state['id']), None)")
        lines.append(f'                if item and item.get("{state_field}") == "{after_create}":')
        lines.append(f'                    print(f"  ✅ 状态验证: {after_create}")')

    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 创建断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _ce:')
    lines.append(f'        print(f"  ⚠️ 创建异常: {{_ce}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_update(fn_name: str, pathname: str, body_sample) -> str:
    """生成 step_update 函数代码。

    策略：忠实对应 Phase 2 update body sample 的字段键集。
    Phase 2 是页面实际抓取的，Phase 4 按 Phase 2 数据一一对应生成。
    """
    # 解析 update body sample 获取字段键
    update_keys = []
    if body_sample:
        try:
            body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
            if isinstance(body, dict):
                update_keys = list(body.keys())
        except Exception:
            pass

    lines = []
    lines.append(f'def step_update(session, state):')
    lines.append(f'    """CRUD 步骤: update"""')
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines.append(f'    print("\\n[修改] {clean_pathname}")')
    lines.append(f"    if not state['id']:")
    lines.append(f'        print("  ⚠️ 跳过[修改]: id 为空（创建未成功）")')
    lines.append(f'        return')
    lines.append(f'    try:')

    if update_keys:
        # 忠实对应 Phase 2 update body sample 的字段
        keys_repr = repr(update_keys)
        lines.append(f'        # 只发送 Phase 2 update body sample 的字段（忠实对应）')
        lines.append(f'        _update_keys = {keys_repr}')
        lines.append(f'        update_body = {{k: state["create_body"][k] for k in _update_keys if k in state.get("create_body", {{}})}}')
        lines.append(f'        update_body["id"] = state["id"]')
        lines.append(f'        update_body["description"] = f"自动修改_{{TS}}"')
    else:
        lines.append(f'        update_body = dict(state["create_body"])')
        lines.append(f'        update_body["id"] = state["id"]')
        lines.append(f'        update_body["description"] = f"自动修改_{{TS}}"')
    lines.append(f'        # 保持名称字段与创建时一致')
    lines.append(f'        for _nk in ("policyName", "userName", "name"):')
    lines.append(f'            if state.get(_nk) and _nk in update_body:')
    lines.append(f'                update_body[_nk] = state[_nk]')
    lines.append(f'        resp = {fn_name}(session, **update_body)')

    lines.append(f'        assert 200 <= resp.status < 300, f"修改失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"修改失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "修改失败: 无响应体"')
    lines.append(f'        print("  ✅ 修改成功")')
    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 修改断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _ue:')
    lines.append(f'        print(f"  ⚠️ 修改异常: {{_ue}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_delete(fn_name: str, pathname: str, body_sample,
                    query_fn_for_verify: Optional[str], query_params: dict = None) -> str:
    """生成 step_delete 函数代码。从 body_sample 驱动参数。"""
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines = []
    lines.append(f'def step_delete(session, state):')
    lines.append(f'    """CRUD 步骤: delete"""')
    lines.append(f'    print("\\n[删除] {clean_pathname}")')
    lines.append(f'    try:')
    lines.append(f"        if not state['id']:")
    lines.append(f'            print("  ⚠️ 跳过[删除]: id 为空")')
    lines.append(f'            return')

    # 从 body_sample 生成 payload 代码
    if body_sample:
        payload_code = _gen_payload_code(body_sample, "delete")
        lines.append(f'        delete_body = {payload_code}')
        lines.append(f'        resp = {fn_name}(session, id=state["id"], **delete_body)')
    else:
        lines.append(f'        resp = {fn_name}(session, id=state["id"])')

    lines.append(f'        assert 200 <= resp.status < 300, f"删除失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"删除失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "删除失败: 无响应体"')
    lines.append(f'        print("  ✅ 删除成功")')

    # 删除验证
    if query_fn_for_verify:
        lines.append(f'        # 清理验证：确认数据已删除')
        lines.append(f'        resp_check = {query_fn_for_verify}(session)')
        lines.append(f'        if resp_check.success and resp_check.json:')
        lines.append(f'            check_data = resp_check.json.get("entity") or resp_check.json.get("data") or []')
        lines.append(f'            if isinstance(check_data, list):')
        lines.append(f"                still_exists = any(i.get('id') == state['id'] for i in check_data)")
        lines.append(f'                if still_exists:')
        lines.append(f'                    print("  ⚠️ 清理验证: 数据仍存在")')
        lines.append(f'                else:')
        lines.append(f'                    print("  ✅ 清理验证: 数据已删除")')

    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 删除断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _de:')
    lines.append(f'        print(f"  ⚠️ 删除异常: {{_de}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_query(fn_name: str, pathname: str, body_sample,
                     state_field: Optional[str], after_lock: Optional[str]) -> str:
    """生成 step_query 函数代码。"""
    # 构建查询参数（过滤掉 ID 字段，保留分页参数）
    query_params = {}
    if body_sample:
        try:
            body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
            if isinstance(body, dict):
                for k, v in body.items():
                    if not re.match(r".*(id|Id|ID)$", k):
                        query_params[k] = v
        except Exception:
            pass

    lines = []
    lines.append(f'def step_query(session, state):')
    lines.append(f'    """CRUD 步骤: query"""')
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines.append(f'    print("\\n[查询] {clean_pathname}")')
    lines.append(f'    try:')
    if query_params:
        lines.append(f'        query_params = {json.dumps(query_params, ensure_ascii=False)}')
        lines.append(f'        resp = {fn_name}(session, **query_params)')
    else:
        lines.append(f'        resp = {fn_name}(session, pageNum=1, pageSize=50)')

    lines.append(f'        assert 200 <= resp.status < 300, f"查询失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"查询失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "查询失败: 无响应体"')
    lines.append(f'        data = resp.json.get("entity") or resp.json.get("data")')
    lines.append(f'        if isinstance(data, dict):')
    lines.append(f'            items = data.get("list") or data.get("records") or []')
    lines.append(f'            total = data.get("total", len(items))')
    lines.append(f'        else:')
    lines.append(f'            items = data if isinstance(data, list) else []')
    lines.append(f'            total = len(items)')
    lines.append(f'        print(f"  ✅ 查询成功: {{len(items)}} 条记录, 总计 {{total}}")')
    lines.append(f"        if state['id']:")
    lines.append(f"            found = [i for i in items if i.get('id') == state['id']]")
    lines.append(f'            if found:')
    lines.append(f"                print(f\"  ✅ 找到已创建的记录: id={{state['id']}}\")")
    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 查询断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _qe:')
    lines.append(f'        print(f"  ⚠️ 查询异常: {{_qe}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_lock(fn_name: str, pathname: str, body_sample,
                    query_fn_for_verify: Optional[str],
                    state_field: Optional[str], after_lock: Optional[str]) -> str:
    """生成 step_lock 函数代码。"""
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines = []
    lines.append(f'def step_lock(session, state):')
    lines.append(f'    """CRUD 步骤: lock"""')
    lines.append(f'    print("\\n[锁定] {clean_pathname}")')
    lines.append(f"    if not state['id']:")
    lines.append(f'        print("  ⚠️ 跳过[锁定]: id 为空")')
    lines.append(f'        return')
    lines.append(f'    try:')
    if body_sample:
        payload_code = _gen_payload_code(body_sample, "lock")
        lines.append(f'        lock_body = {payload_code}')
        lines.append(f'        resp = {fn_name}(session, id=state["id"], **lock_body)')
    else:
        lines.append(f'        resp = {fn_name}(session, id=state["id"])')
    lines.append(f'        assert 200 <= resp.status < 300, f"锁定失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"锁定失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "锁定失败: 无响应体"')
    lines.append(f'        print("  ✅ 锁定成功")')
    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 锁定断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _le:')
    lines.append(f'        print(f"  ⚠️ 锁定异常: {{_le}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_unlock(fn_name: str, pathname: str, body_sample,
                      query_fn_for_verify: Optional[str],
                      state_field: Optional[str], after_unlock: Optional[str]) -> str:
    """生成 step_unlock 函数代码。"""
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines = []
    lines.append(f'def step_unlock(session, state):')
    lines.append(f'    """CRUD 步骤: unlock"""')
    lines.append(f'    print("\\n[解锁] {clean_pathname}")')
    lines.append(f"    if not state['id']:")
    lines.append(f'        print("  ⚠️ 跳过[解锁]: id 为空")')
    lines.append(f'        return')
    lines.append(f'    try:')
    if body_sample:
        payload_code = _gen_payload_code(body_sample, "unlock")
        lines.append(f'        unlock_body = {payload_code}')
        lines.append(f'        resp = {fn_name}(session, id=state["id"], **unlock_body)')
    else:
        lines.append(f'        resp = {fn_name}(session, id=state["id"])')
    lines.append(f'        assert 200 <= resp.status < 300, f"解锁失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"解锁失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "解锁失败: 无响应体"')
    lines.append(f'        print("  ✅ 解锁成功")')
    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ 解锁断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _ue:')
    lines.append(f'        print(f"  ⚠️ 解锁异常: {{_ue}}")')
    lines.append('')
    return '\n'.join(lines)


def _gen_step_generic(step: str, fn_name: str, pathname: str, body_sample) -> str:
    """生成通用步骤函数代码。"""
    step_name_cn = {"authorize": "授权", "reset": "重置", "migrate": "迁移"}.get(step, step)
    clean_pathname = re.sub(r'[0-9a-f]{32}', '{id}', pathname)
    lines = []
    lines.append(f'def step_{step}(session, state):')
    lines.append(f'    """CRUD 步骤: {step}"""')
    lines.append(f'    print("\\n[{step_name_cn}] {clean_pathname}")')
    lines.append(f'    try:')
    if body_sample:
        payload_code = _gen_payload_code(body_sample, step)
        lines.append(f'        body = {payload_code}')
        lines.append(f'        resp = {fn_name}(session, id=state.get("id"), **body)')
    else:
        lines.append(f'        resp = {fn_name}(session)')
    lines.append(f'        assert 200 <= resp.status < 300, f"{step_name_cn}失败: HTTP {{resp.status}}"')
    lines.append(f'        assert resp.success, f"{step_name_cn}失败: 业务错误 {{resp.text[:200]}}"')
    lines.append(f'        assert resp.json, "{step_name_cn}失败: 无响应体"')
    lines.append(f'        print("  ✅ {step_name_cn}成功")')
    lines.append(f'    except AssertionError as _ae:')
    lines.append(f'        print(f"  ❌ {step_name_cn}断言失败: {{_ae}}")')
    lines.append(f'    except Exception as _e:')
    lines.append(f'        print(f"  ⚠️ {step_name_cn}异常: {{_e}}")')
    lines.append('')
    return '\n'.join(lines)


# ========== 辅助函数 ==========

def _gen_api_function(fn_name: str, step: str, path_for_url: str, method: str, query_params: dict = None) -> str:
    """生成单个 API 调用函数（使用 requests）。"""
    # 如果有 query_params，拼接到默认 URL
    full_url = path_for_url
    if query_params:
        qp_pairs = [f"{k}={v}" for k, v in query_params.items()]
        full_url = path_for_url + "?" + "&".join(qp_pairs)

    code = f'''def {fn_name}(session, url=None, **kwargs):
    """{step}: {path_for_url}"""
    if url is None:
        url = BASE_URL + "{full_url}"
    else:
        url = BASE_URL + url
    if "{{id}}" in url:
        id_val = kwargs.pop("id", None)
        if id_val:
            url = url.replace("{{id}}", str(id_val))
            # 只有 PUT/PATCH 的 body 需要 id（Phase 2 body sample 含 id 字段）
            # DELETE body 不含 id，不能多传
            if "{method}" in ("PUT", "PATCH"):
                kwargs["id"] = id_val
    try:
        req_kwargs = {{}}
        if kwargs:
            if "{method}" == "GET":
                req_kwargs["params"] = kwargs
            elif "{method}" == "DELETE" and kwargs:
                # DELETE 可能需要 body（某些 API 设计）
                req_kwargs["json"] = kwargs
            else:
                req_kwargs["json"] = kwargs

        resp = session.{method.lower()}(url, **req_kwargs)

        # 打印请求/响应详情
        try:
            _req_body = resp.request.body or ""
            if isinstance(_req_body, bytes):
                _req_body = _req_body.decode("utf-8", errors="replace")
            print(f"    >>> {{resp.request.method}} {{resp.request.url}}")
            if _req_body:
                try:
                    print(f"    body: {{json.dumps(json.loads(_req_body), ensure_ascii=False, indent=2)}}")
                except Exception:
                    print(f"    body: {{_req_body[:500]}}")
            print(f"    <<< {{resp.status_code}} {{resp.headers.get('content-type', '')}}")
            if resp.text:
                try:
                    _rb = json.loads(resp.text)
                    print(f"    success: {{_rb.get('success', '-')}}")
                    if _rb.get("errorMessage"):
                        print(f"    error: {{_rb['errorMessage']}}")
                    print(f"    body: {{json.dumps(_rb, ensure_ascii=False, indent=2)[:2000]}}")
                except Exception:
                    print(f"    body: {{resp.text[:500]}}")
        except Exception:
            pass

        return ApiResponse(resp)
    except Exception as e:
        r = ApiResponse.__new__(ApiResponse)
        r.status = -1
        r.text = str(e)
        r._json = {{"success": False}}
        return r
'''
    return code


def _gen_payload_code(body_sample, step_type: str) -> str:
    """从 Stage 2 body_sample 生成 Python dict 字面量代码。

    动态字段替换规则：
    - id/roleId/policyId 等 → state['id']
    - tenantId/adminId → state['tenantId']/state['adminId']
    - policyName/userName/name → state[_nk]（从 create 步骤传递）
    - description → "自动修改_{TS}"（仅 update 步骤）
    """
    try:
        body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
    except Exception:
        return "{}"
    if not isinstance(body, dict):
        return "{}"

    id_entity_fields = []
    tenant_fields = []
    name_fields = []
    static_fields = {}

    for k, v in body.items():
        if k.lower() in ("id", "roleid", "policyid", "userid"):
            id_entity_fields.append(k)
        elif k.lower() in ("tenantid", "adminid"):
            tenant_fields.append(k)
        elif k.lower() in ("policyname", "username", "name", "account"):
            name_fields.append(k)
        else:
            static_fields[k] = v

    # 生成 Python dict 字面量
    entries = []

    for k in id_entity_fields:
        entries.append(f"'{k}': state['id']")

    for k in tenant_fields:
        if k.lower() == "adminid":
            entries.append(f"'{k}': state['adminId']")
        else:
            # tenantId 优先从 create_body 取（保持与创建时的租户一致），
            # 避免当前登录用户属于超管租户时 API 拒绝操作
            entries.append(f"'{k}': state.get('create_body', {{}}).get('{k}', state.get('tenantId', ''))")

    for k in name_fields:
        entries.append(f"'{k}': state.get('{k}', '')")

    for k, v in static_fields.items():
        v_repr = repr(v)
        if step_type == "update" and k == "description":
            entries.append(f"'{k}': f'自动修改_{{TS}}'")
        else:
            entries.append(f"'{k}': {v_repr}")

    return '{' + ', '.join(entries) + '}'


def save_script_to_file(script: str, project_dir: str, module_name: str, version: str = ""):
    """将生成的脚本保存到文件。version 非空时写入 flows/<version>/。"""
    from . import version as _ver
    if version:
        flows_dir = _ver.flows_dir_for(project_dir, version)
    else:
        flows_dir = Path(project_dir) / "flows"
        flows_dir.mkdir(parents=True, exist_ok=True)
    output_path = flows_dir / f"{module_name}_API测试.py"
    output_path.write_text(script, encoding="utf-8")
    LOG.info(f"  脚本已生成: {output_path}")
    return str(output_path)
