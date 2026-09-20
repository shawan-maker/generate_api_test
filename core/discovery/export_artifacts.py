"""
export_artifacts.py — Stage 5: 导出 artifacts

将 manifest 导出为三种格式：
  1. Postman Collection v2.1.0 JSON — 可直接导入 Postman
  2. helpers.py — 高层辅助函数（给外部测试平台用）
  3. Excel 参数文件 — 变量配置表（e_ 前缀，敏感标记）

不修改 manifest，不执行测试，仅做格式转换。
"""

import json
import uuid
import re
import logging
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("export_artifacts")


# ── Postman Pre-request Script: JS 版 helpers 函数 ─────────────────────────

_POSTMAN_HELPERS_JS = [
    "// helpers.js — 由 module_discovery Stage 5 自动导出",
    "// Postman Pre-request Script 版本的测试数据生成函数",
    "",
    "function _ts6() {",
    "    return Date.now().toString(16).slice(-6);",
    "}",
    "",
    "function gen_test_name() {",
    "    return 'AT_' + _ts6();",
    "}",
    "",
    "function gen_mutable_value() {",
    "    return 'auto_modified_' + _ts6();",
    "}",
    "",
    "function gen_email() {",
    "    return 'at_' + _ts6() + '@test.com';",
    "}",
    "",
    "function gen_phone() {",
    "    return '138' + Math.floor(Math.random() * 100000000);",
    "}",
    "",
    "function gen_text() {",
    "    return 'auto_' + _ts6();",
    "}",
    "",
    "// 表达式解析: ${function(args)} → 调用对应函数",
    "var _fnMap = {",
    "    gen_test_name: gen_test_name,",
    "    gen_mutable_value: gen_mutable_value,",
    "    gen_email: gen_email,",
    "    gen_phone: gen_phone,",
    "    gen_text: gen_text",
    "};",
    "",
    "function resolveExpr(expr) {",
    "    var m = expr.match(/^\\$\\{(\\w+)\\([^)]*\\)\\}$/);",
    "    if (!m) return expr;",
    "    var fn = _fnMap[m[1]];",
    "    return fn ? fn() : expr;",
    "}",
]


def export_postman_collection(manifest: dict, output_path: Path):
    """导出 Postman Collection v2.1.0 JSON。

    动态字段（name/mutable/generate）通过 Pre-request Script 自动赋值，
    无需手动设置环境变量。

    Args:
        manifest: 完整 manifest 字典
        output_path: 输出文件路径 (.postman_collection.json)
    """
    module_name = manifest.get("module", {}).get("name", "module")
    base_url = manifest.get("module", {}).get("base_url", "")

    collection = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": f"{module_name} API 测试",
            "description": f"由 module_discovery Stage 5 自动导出",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
        },
        "variable": _build_postman_variables(manifest),
        "item": []
    }

    # 00_前置操作 文件夹
    pre_apis = manifest.get("pre_apis", [])
    if pre_apis:
        pre_folder = {
            "name": "00_前置操作",
            "item": []
        }
        for pre_api in pre_apis:
            item = _build_postman_request_item(pre_api, base_url, is_pre_api=True)
            pre_folder["item"].append(item)
        collection["item"].append(pre_folder)

    # 01_业务操作 文件夹
    steps = manifest.get("steps", [])
    if steps:
        biz_folder = {
            "name": "01_业务操作",
            "item": []
        }
        for i, step in enumerate(steps, 1):
            # 分支：单 API step vs phases step
            phases = step.get("phases")
            if phases:
                # phases step：展开为多个 Postman request item
                for j, phase in enumerate(phases):
                    # 构建 phase 级的 step-like dict（供 _build_postman_request_item 使用）
                    phase_step = {
                        "action": step.get("action", ""),
                        "label": phase.get("label", phase.get("id", "")),
                        "api": phase.get("api", {}),
                        "body_template": phase.get("body_template", {}),
                        "body_field_roles": phase.get("body_field_roles", {}),
                        "extract": phase.get("extract", []),
                    }
                    item = _build_postman_request_item(phase_step, base_url, step_index=i)
                    # 重命名为 "序号.子序号_标签"
                    item["name"] = f"{i:02d}.{j+1}_{phase.get('label', phase.get('id', ''))}"
                    biz_folder["item"].append(item)
            else:
                # 单 API step（原有逻辑不变）
                item = _build_postman_request_item(step, base_url, step_index=i)
                biz_folder["item"].append(item)
        collection["item"].append(biz_folder)

    # Collection-level Pre-request Script: 定义 JS 版的 helpers 函数
    collection["event"] = [{
        "listen": "prerequest",
        "script": {
            "type": "text/javascript",
            "exec": _POSTMAN_HELPERS_JS
        }
    }]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(collection, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    LOG.info(f"Postman Collection 已导出: {output_path} ({len(pre_apis)} 前置 + {len(steps)} 业务)")


def _build_postman_variables(manifest: dict) -> list:
    """构建 Postman Collection 级变量。"""
    variables = []
    base_url = manifest.get("module", {}).get("base_url", "")
    auth_profile = manifest.get("auth_profile", {})

    variables.append({"key": "base_url", "value": base_url, "type": "string"})

    # 从 auth_profile 提取固定 header
    fixed_headers = auth_profile.get("fixed_headers", {})
    for hk, hv in fixed_headers.items():
        variables.append({"key": hk, "value": hv, "type": "string"})

    # 前置 API 提取的变量（运行时由 test script 设置）
    for pre_api in manifest.get("pre_apis", []):
        for extract in pre_api.get("extracts", []):
            variables.append({
                "key": extract["name"],
                "value": "",
                "type": "string",
                "description": f"由 {pre_api.get('name', pre_api.get('id', ''))} 提取"
            })

    return variables


def _build_postman_request_item(step_def: dict, base_url: str,
                                is_pre_api: bool = False,
                                step_index: int = 0) -> dict:
    """构建单个 Postman 请求项。"""
    api = step_def.get("api", {})
    method = api.get("method", "GET")
    pathname = api.get("pathname", "")
    label = step_def.get("label", step_def.get("action", "unknown"))
    body_template = step_def.get("body_template", {})
    field_roles = step_def.get("body_field_roles", {})

    # 名称
    name = f"{step_index:02d}_{label}" if step_index else label

    # URL: {{base_url}} + pathname，path_params 用 {{variable}} 替换
    path_params = api.get("path_params", {})
    if path_params:
        for placeholder, mapping in path_params.items():
            source = mapping.get("source", "")
            if "." in source:
                var_name = source.split(".", 1)[1]
            else:
                var_name = source if source else placeholder
            # create.id → id
            if var_name == "create.id":
                var_name = "id"
            pathname = pathname.replace(f"{{{placeholder}}}", "{{" + var_name + "}}")

    raw_url = "{{base_url}}" + pathname

    # Headers
    headers = [
        {"key": "Content-Type", "value": "application/json"},
        {"key": "Authorization", "value": "Bearer {{token}}"},
    ]
    auth_profile_extra = step_def.get("_auth_fixed_headers", {})
    for hk, hv in auth_profile_extra.items():
        headers.append({"key": hk, "value": hv})

    # Test script and pre-request script containers
    events = []

    # Body
    body = None
    if body_template:
        body_raw, pre_vars = _resolve_postman_body(body_template, field_roles)
        body = {
            "mode": "raw",
            "raw": json.dumps(body_raw, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}}
        }
        # Pre-request Script: 为动态字段设置环境变量
        if pre_vars:
            pre_lines = []
            for var_name, expr in pre_vars:
                pre_lines.append(f"pm.variables.set('{var_name}', resolveExpr('{expr}'));")
            events.append({
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": pre_lines
                }
            })

    # Test script: 提取响应值到环境变量
    extracts = step_def.get("extract", {}) or step_def.get("extracts", [])
    if isinstance(extracts, dict) and extracts:
        test_lines = _build_postman_test_script(extracts, step_def.get("action", ""))
        if test_lines:
            events.append({
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": test_lines
                }
            })
    elif isinstance(extracts, list) and extracts:
        test_lines = _build_postman_test_script_from_list(extracts)
        if test_lines:
            events.append({
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": test_lines
                }
            })

    item = {
        "name": name,
        "request": {
            "method": method,
            "header": headers,
            "url": {"raw": raw_url},
        },
        "response": []
    }
    if body:
        item["request"]["body"] = body
    if events:
        item["event"] = events

    return item


def _resolve_postman_body(body_template, field_roles: dict):
    """将 body_template 中的动态字段替换为 Postman {{variable}} 引用。

    对于 name/mutable/generate 角色，返回 (body, pre_vars) 元组：
    - body: 包含 {{variable}} 占位符的 body
    - pre_vars: [(var_name, expression), ...] 用于 pre-request script

    支持 5 角色体系（name/mutable/context/generate/static）。
    """
    if isinstance(body_template, list):
        arr_role = field_roles.get("__array_items__", {})
        if arr_role.get("role") == "context":
            return ["{{id}}"], []
        return body_template, []

    result = {}
    pre_vars = []  # [(var_name, expression), ...]

    for key, value in body_template.items():
        role_config = field_roles.get(key, {"role": "static"})
        role = role_config.get("role", "static")

        # name 角色
        if role == "name":
            var_name = f"name_{key}"
            # 检查 value 是否是 ${...} 表达式
            if isinstance(value, str) and value.startswith("${"):
                result[key] = "{{" + var_name + "}}"
                pre_vars.append((var_name, value))
            else:
                result[key] = "{{" + var_name + "}}"

        # mutable 角色
        elif role == "mutable":
            var_name = f"mutable_{key}"
            # 检查 value 是否是 ${...} 表达式
            if isinstance(value, str) and value.startswith("${"):
                result[key] = "{{" + var_name + "}}"
                pre_vars.append((var_name, value))
            else:
                result[key] = "{{" + var_name + "}}"

        # context 角色
        elif role == "context":
            source = role_config.get("source", f"context.{key}")
            # source 格式: "api_id.field_name" → 取 field_name
            if "." in source:
                var_name = source.split(".", 1)[1]
            else:
                var_name = source
            # 特殊处理：create.id → id
            if var_name == "create.id":
                var_name = "id"
            result[key] = "{{" + var_name + "}}"

        # generate 角色
        elif role == "generate":
            pattern = role_config.get("pattern", "text")
            var_name = f"gen_{key}_{pattern}"
            # 检查 value 是否是 ${...} 表达式
            if isinstance(value, str) and value.startswith("${"):
                result[key] = "{{" + var_name + "}}"
                pre_vars.append((var_name, value))
            else:
                result[key] = "{{" + var_name + "}}"

        # static 角色（默认）
        else:
            # 嵌套 dict：递归检查是否有提升的 context 字段
            if isinstance(value, dict):
                prefix = f"{key}."
                nested_roles = {
                    rk[len(prefix):]: rv
                    for rk, rv in field_roles.items()
                    if rk.startswith(prefix)
                }
                if nested_roles:
                    nested_result, nested_pre_vars = _resolve_postman_body(value, nested_roles)
                    result[key] = nested_result
                    pre_vars.extend(nested_pre_vars)
                else:
                    result[key] = value
            else:
                result[key] = value

    return result, pre_vars


def _build_postman_test_script(extracts: dict, action: str) -> list:
    """构建 Postman test script（从 extract dict 格式）。"""
    lines = [
        "var jsonData = pm.response.json();",
        "pm.test('Status OK', function() {",
        "    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
        "});",
        ""
    ]

    # 提取 id
    id_path = extracts.get("id", "")
    if id_path:
        js_path = _json_path_to_js(id_path)
        lines.append(f"var createdId = jsonData{js_path};")
        lines.append(f"if (createdId) pm.environment.set('id', createdId);")
        lines.append("")

    # 提取 names
    names = extracts.get("names", [])
    for name_key in names:
        lines.append(f"var nameVal = jsonData.entity && jsonData.entity.{name_key};")
        lines.append(f"if (nameVal) pm.environment.set('name_{name_key}', nameVal);")

    return lines


def _build_postman_test_script_from_list(extracts: list) -> list:
    """构建 Postman test script（从 extract list 格式）。"""
    lines = [
        "var jsonData = pm.response.json();",
        "pm.test('Status OK', function() {",
        "    pm.expect(pm.response.code).to.be.oneOf([200, 201]);",
        "});",
        ""
    ]
    for ext in extracts:
        name = ext.get("name", "")
        path = ext.get("path", "")
        if name and path:
            js_path = _json_path_to_js(path)
            lines.append(f"var {name} = jsonData{js_path};")
            lines.append(f"if ({name} !== undefined) pm.environment.set('{name}', {name});")
    return lines


def _json_path_to_js(path: str) -> str:
    """将 entity.id 或 entity.list[0].id 转为 JS 访问路径。"""
    parts = []
    current = ""
    for char in path:
        if char == '.':
            if current:
                parts.append(f".{current}")
                current = ""
        elif char == '[':
            if current:
                parts.append(f".{current}")
                current = ""
            parts.append("[")
        elif char == ']':
            if current:
                parts.append(current)
                current = ""
            parts.append("]")
        else:
            current += char
    if current:
        parts.append(f".{current}")
    return "".join(parts)


def export_helpers(manifest: dict, output_path: Path):
    """导出 helpers.py 数据生成函数集。

    这是唯一的测试数据生成函数源，被以下组件共享：
      - Stage 4 生成的 Python 测试脚本（test_runtime.py 导入调用）
      - Stage 5 导出的 Postman Collection（Pre-request Script）
      - Stage 5 导出的 Excel 参数表

    函数列表：
      get_token_by_cookie  — Cookie Token 提取
      gen_timestamp        — 毫秒时间戳
      gen_test_name        — 唯一测试名称（name 角色）
      gen_mutable_value    — 可变字段值（mutable 角色）
      gen_email            — 测试邮箱（generate/email 模式）
      gen_phone            — 测试手机号（generate/phone 模式）
      gen_uuid             — UUID 字符串（generate/uuid 模式）
      gen_hex_id           — 32 位十六进制 ID（generate/hex_id 模式）
      gen_random_int       — 8 位随机整数（generate/random_int 模式）
      gen_text             — 通用文本（generate/text 模式）
    """
    auth_profile = manifest.get("auth_profile", {})
    cookie_token_key = auth_profile.get("cookie_token_key", "accessToken")

    helpers_code = '''"""
helpers.py — 由 module_discovery Stage 5 自动导出

测试数据生成函数集，供 API 测试脚本、Postman Pre-request Script 共享。
所有生成函数以当前时间戳为种子，保证每次运行数据唯一。
"""

import json
import time
import random
import uuid as _uuid_mod
from pathlib import Path


# ────────────────── 认证 ──────────────────

def get_token_by_cookie(cookie_path: str, token_key: str = "{cookie_token_key}") -> str:
    """
    从 Cookie 文件提取 Token。

    Args:
        cookie_path: Cookie JSON 文件路径
        token_key: Cookie 中的 Token 字段名

    Returns:
        Token 字符串

    Raises:
        FileNotFoundError: cookie_path 不存在
        ValueError: 未找到指定 token_key
    """
    path = Path(cookie_path)
    if not path.exists():
        raise FileNotFoundError(f"Cookie 文件不存在: {{cookie_path}}")

    with open(path, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    for cookie in cookies:
        if cookie.get("name") == token_key:
            value = cookie.get("value", "")
            if value:
                return value

    raise ValueError(f"Cookie 中未找到 token_key={{token_key}}")


# ────────────────── 基础工具 ──────────────────

def gen_timestamp() -> str:
    """生成当前时间戳（毫秒级），例如 '1725780000123'。"""
    return str(int(time.time() * 1000))


def _ts6() -> str:
    """生成 6 位十六进制时间戳后缀（用于构造唯一值）。"""
    return format(int(time.time() * 1000), "x")[-6:]


# ────────────────── 数据生成函数 ──────────────────

def gen_test_name(field_name: str = "") -> str:
    """
    生成唯一测试名称（用于 name 角色字段，如 userName、name）。

    Args:
        field_name: 字段名（仅用于可读性，不影响生成值）

    Returns:
        例如 'AT_a3f2c1'
    """
    return f"AT_{{_ts6()}}"


def gen_mutable_value() -> str:
    """
    生成可变字段值（用于 mutable 角色字段，如 description）。

    Returns:
        例如 'auto_modified_a3f2c1'
    """
    return f"auto_modified_{{_ts6()}}"


def gen_email() -> str:
    """
    生成测试邮箱地址。

    Returns:
        例如 'at_a3f2c1@test.com'
    """
    return f"at_{{_ts6()}}@test.com"


def gen_phone() -> str:
    """
    生成测试手机号（11 位中国大陆号码）。

    Returns:
        例如 '1380a3f2c1' → 注意：ts6 含字母，此处用随机数字替代
    """
    return f"138{{random.randint(10000000, 99999999)}}"


def gen_uuid() -> str:
    """
    生成 UUID v4 字符串。

    Returns:
        例如 '550e8400-e29b-41d4-a716-446655440000'
    """
    return str(_uuid_mod.uuid4())


def gen_hex_id() -> str:
    """
    生成 32 位十六进制 ID（无连字符的 UUID）。

    Returns:
        例如 '550e8400e29b41d4a716446655440000'
    """
    return _uuid_mod.uuid4().hex


def gen_random_int() -> str:
    """
    生成 8 位随机整数。

    Returns:
        例如 '47291830'
    """
    return str(random.randint(10000000, 99999999))


def gen_text() -> str:
    """
    生成通用文本值。

    Returns:
        例如 'auto_a3f2c1'
    """
    return f"auto_{{_ts6()}}"
'''.format(cookie_token_key=cookie_token_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(helpers_code, encoding="utf-8")
    LOG.info(f"helpers.py 已导出: {output_path}")


def export_excel_params(manifest: dict, output_path: Path):
    """导出 Excel 参数文件。

    格式：单 sheet "变量配置"，列：变量名称 | 是否敏感类型 | 变量值 | 备注
    变量名使用 e_ 前缀，敏感字段标记为 "是"。
    """
    try:
        import openpyxl
    except ImportError:
        LOG.warning("openpyxl 未安装，跳过 Excel 导出。pip install openpyxl")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "变量配置"

    # 表头
    ws.append(["变量名称", "是否敏感类型", "变量值", "备注"])

    # 表头样式
    from openpyxl.styles import Font, Alignment
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    # 收集参数
    params = _collect_excel_params(manifest)

    for param in params:
        ws.append([
            param["name"],
            "是" if param.get("sensitive") else "否",
            param["value"],
            param.get("note", "")
        ])

    # 列宽
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 15
    ws.column_dimensions["C"].width = 50
    ws.column_dimensions["D"].width = 40

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    LOG.info(f"Excel 参数文件已导出: {output_path} ({len(params)} 个参数)")


def _collect_excel_params(manifest: dict) -> list:
    """收集导出到 Excel 的参数列表。

    规则：
    - 基础连接信息（base_url, login_url）
    - 认证信息（username, password, token）
    - 前置 API 提取的上下文字段
    - 业务步骤中出现的 name/mutable 字段
    """
    params = []
    module = manifest.get("module", {})
    auth_profile = manifest.get("auth_profile", {})

    # 基础参数
    params.append({
        "name": "e_base_url",
        "value": module.get("base_url", ""),
        "sensitive": False,
        "note": "API 基础地址"
    })
    params.append({
        "name": "e_login_url",
        "value": module.get("login_url", ""),
        "sensitive": False,
        "note": "登录页地址"
    })

    # 认证参数
    creds_env = auth_profile.get("credentials_env", {})
    params.append({
        "name": "e_username",
        "value": "${" + creds_env.get("username", "APP_USER") + "}",
        "sensitive": False,
        "note": f"环境变量: {creds_env.get('username', 'APP_USER')}"
    })
    params.append({
        "name": "e_password",
        "value": "${" + creds_env.get("password", "APP_PASS") + "}",
        "sensitive": True,
        "note": f"环境变量: {creds_env.get('password', 'APP_PASS')}"
    })
    params.append({
        "name": "e_token",
        "value": "${get_token_by_cookie('cookies.json')}",
        "sensitive": True,
        "note": f"从 Cookie 提取 (key={auth_profile.get('cookie_token_key', 'accessToken')})"
    })

    # 前置 API 提取的变量
    for pre_api in manifest.get("pre_apis", []):
        for extract in pre_api.get("extracts", []):
            params.append({
                "name": f"e_{extract['name']}",
                "value": f"${{{extract['name']}}}",
                "sensitive": False,
                "note": f"由 {pre_api.get('name', pre_api.get('id', ''))} 提取 (path: {extract.get('path', '')})"
            })

    # 业务步骤中的 name 字段
    seen_names = set()
    for step in manifest.get("steps", []):
        # 分支：单 API step vs phases step
        phases = step.get("phases")
        if phases:
            # phases step：从所有 phases 中收集字段
            roles_list = [phase.get("body_field_roles", {}) for phase in phases]
        else:
            # 单 API step（原有逻辑）
            roles_list = [step.get("body_field_roles", {})]

        for body_roles in roles_list:
            for field_name, role_config in body_roles.items():
                if role_config.get("role") == "name" and field_name not in seen_names:
                    seen_names.add(field_name)
                    params.append({
                        "name": f"e_name_{field_name}",
                        "value": "${gen_test_name('" + field_name + "')}",
                        "sensitive": False,
                        "note": f"步骤 {step.get('label', step.get('action', ''))} 的名称字段"
                    })
                elif role_config.get("role") == "mutable" and field_name not in seen_names:
                    seen_names.add(field_name)
                    params.append({
                        "name": f"e_mutable_{field_name}",
                        "value": "${gen_mutable_value()}",
                        "sensitive": False,
                        "note": f"步骤 {step.get('label', step.get('action', ''))} 的可变字段"
                    })

    return params


def export_all(manifest: dict, output_dir: Path, module_name: str = None):
    """导出所有 artifacts（便捷入口）。

    Args:
        manifest: 完整 manifest 字典
        output_dir: 输出目录
        module_name: 模块名（默认从 manifest 取）
    """
    if not module_name:
        module_name = manifest.get("module", {}).get("name", "module")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Postman Collection
    postman_path = output_dir / f"{module_name}.postman_collection.json"
    export_postman_collection(manifest, postman_path)

    # 2. helpers.py
    helpers_path = output_dir / "helpers.py"
    export_helpers(manifest, helpers_path)

    # 3. Excel 参数文件
    excel_path = output_dir / f"{module_name}_params.xlsx"
    export_excel_params(manifest, excel_path)

    return {
        "postman": str(postman_path),
        "helpers": str(helpers_path),
        "excel": str(excel_path),
    }
