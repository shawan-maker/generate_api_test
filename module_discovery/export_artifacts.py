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


def export_postman_collection(manifest: dict, output_path: Path):
    """导出 Postman Collection v2.1.0 JSON。

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
            item = _build_postman_request_item(step, base_url, step_index=i)
            biz_folder["item"].append(item)
        collection["item"].append(biz_folder)

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

    # URL: {{base_url}} + pathname，路径参数用 {{id}}
    raw_url = "{{base_url}}" + pathname

    # Headers
    headers = [
        {"key": "Content-Type", "value": "application/json"},
        {"key": "Authorization", "value": "Bearer {{token}}"},
    ]
    auth_profile_extra = step_def.get("_auth_fixed_headers", {})
    for hk, hv in auth_profile_extra.items():
        headers.append({"key": hk, "value": hv})

    # Body
    body = None
    if body_template:
        body_raw = _resolve_postman_body(body_template, field_roles)
        body = {
            "mode": "raw",
            "raw": json.dumps(body_raw, ensure_ascii=False, indent=2),
            "options": {"raw": {"language": "json"}}
        }

    # Test script: 提取响应值到环境变量
    events = []
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
    """将 body_template 中的动态字段替换为 Postman {{variable}} 引用。"""
    if isinstance(body_template, list):
        arr_role = field_roles.get("__array_items__", {})
        if arr_role.get("role") == "id_ref":
            return ["{{id}}"]
        return body_template

    result = {}
    for key, value in body_template.items():
        role_config = field_roles.get(key, {"role": "static"})
        role = role_config.get("role", "static")

        if role == "id_ref":
            result[key] = "{{id}}"
        elif role == "context":
            source = role_config.get("source", f"context.{key}")
            var_name = source.replace(".", "_").replace("context_", "")
            result[key] = "{{" + var_name + "}}"
        elif role == "pre_api_ref":
            source = role_config.get("source", key)
            # source 格式: "api_id.field_name" → 取 field_name
            if "." in source:
                var_name = source.split(".", 1)[1]
            else:
                var_name = source
            result[key] = "{{" + var_name + "}}"
        elif role == "name":
            result[key] = "{{name_" + key + "}}"
        elif role == "mutable":
            result[key] = "{{mutable_" + key + "}}"
        else:
            result[key] = value
    return result


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
    """导出 helpers.py 高层辅助函数。

    仅导出外部测试平台需要的公共函数，不导出内部实现。
    """
    auth_profile = manifest.get("auth_profile", {})
    cookie_token_key = auth_profile.get("cookie_token_key", "accessToken")
    header_name = auth_profile.get("header_name", "Authorization")
    header_prefix = auth_profile.get("header_prefix", "Bearer ")

    helpers_code = f'''"""
helpers.py — 由 module_discovery Stage 5 自动导出

高层辅助函数，供外部测试平台调用。
内部实现（extract_by_path、build_auth_header 等）不导出。
"""

import json
import time
import random
from pathlib import Path


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


def gen_timestamp() -> str:
    """
    生成当前时间戳（毫秒级）。

    Returns:
        时间戳字符串，例如 "1725780000123"
    """
    return str(int(time.time() * 1000))


def gen_unique_name(prefix: str, ts: str, original: str) -> str:
    """
    生成唯一名称（用于 create 步骤的 name 字段）。

    Args:
        prefix: 前缀，例如 "AT"
        ts: 时间戳字符串
        original: 原始名称

    Returns:
        格式化名称，例如 "AT_1725780000123_test_user"
    """
    return f"{{prefix}}_{{ts}}_{{original}}"


def gen_mutable_value(ts: str) -> str:
    """
    生成可变字段值（用于 update 步骤的 description 等字段）。

    Args:
        ts: 时间戳字符串

    Returns:
        格式化值，例如 "自动修改_1725780000123"
    """
    return f"自动修改_{{ts}}"
'''

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
        body_roles = step.get("body_field_roles", {})
        for field_name, role_config in body_roles.items():
            if role_config.get("role") == "name" and field_name not in seen_names:
                seen_names.add(field_name)
                params.append({
                    "name": f"e_name_{field_name}",
                    "value": "${gen_unique_name('AT', gen_timestamp(), '...')}",
                    "sensitive": False,
                    "note": f"步骤 {step.get('label', step.get('action', ''))} 的名称字段"
                })
            elif role_config.get("role") == "mutable" and field_name not in seen_names:
                seen_names.add(field_name)
                params.append({
                    "name": f"e_mutable_{field_name}",
                    "value": "${gen_mutable_value(gen_timestamp())}",
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
