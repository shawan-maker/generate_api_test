"""
test_runtime.py — 通用测试运行时库

提供 Manifest 驱动的测试执行引擎，替代 gen_test.py 生成的硬编码逻辑。
所有模块共享同一套运行时，差异通过 manifest 数据驱动。

核心类：
  - ResponseParser: 根据 response_contract 解析 API 响应
  - StepExecutor: 执行单个 CRUD 步骤
  - TestRunner: 编排完整测试流程
"""

import json
import re
import sys
import time
import os
import uuid
import random
import importlib.util
from pathlib import Path
from typing import Optional, Any

import requests
import urllib3

# 导入常量和业务规则
# test_runtime.py 可能在两个位置运行：
#   - 框架内：lib/runtime/test_runtime.py
#   - 生成脚本：projects/.../api/lib/runtime/test_runtime.py
# 需要动态定位 core.discovery 的路径
try:
    from core.discovery import const
except ImportError:
    # 尝试从框架根目录导入
    _framework_root = Path(__file__).resolve().parent.parent.parent
    if (_framework_root / "core" / "discovery").exists():
        sys.path.insert(0, str(_framework_root))
    else:
        # 生成脚本场景：向上搜索 core/discovery
        _p = Path(__file__).resolve().parent
        while _p != _p.parent:
            if (_p / "core" / "discovery").exists():
                sys.path.insert(0, str(_p))
                break
            _p = _p.parent
    from core.discovery import const

# 禁用 SSL 警告（自签证书环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ── 表达式解析 ──────────────────────────────────────────────────────────────

_EXPR_RE = re.compile(r'^\$\{(\w+)\(([^)]*)\)\}$')


def _resolve_expression(value, helpers_ns: dict):
    """解析 ${function(args)} 表达式，返回生成值。

    支持的表达式格式:
      ${gen_test_name("userName")}  → 调用 helpers_ns["gen_test_name"]("userName")
      ${gen_mutable_value()}        → 调用 helpers_ns["gen_mutable_value"]()
      ${gen_email()}                → 调用 helpers_ns["gen_email"]()

    如果 value 不是表达式(不以 ${ 开头)，原样返回。
    如果 helpers_ns 中没有对应函数，打印警告并原样返回。

    Args:
        value: 原始值(可能是字符串表达式或普通值)
        helpers_ns: helpers 模块的函数命名空间(dict)

    Returns:
        生成的值或原始值
    """
    if not isinstance(value, str):
        return value

    m = _EXPR_RE.match(value)
    if not m:
        return value

    func_name = m.group(1)
    args_str = m.group(2).strip()

    func = helpers_ns.get(func_name)
    if not func:
        print(f"  ⚠️  helpers 中未找到函数: {func_name}，使用原值")
        return value

    # 解析参数
    if not args_str:
        # 无参数:gen_mutable_value()
        return func()

    # 解析字符串参数(支持带引号的字符串)
    args = []
    for arg in args_str.split(","):
        arg = arg.strip()
        if (arg.startswith('"') and arg.endswith('"')) or \
           (arg.startswith("'") and arg.endswith("'")):
            args.append(arg[1:-1])
        else:
            args.append(arg)

    return func(*args)


def _load_helpers() -> dict:
    """加载同目录的 helpers.py 作为函数命名空间。

    搜索路径:
      1. Path(__file__).parent.parent.parent / "helpers.py"  (api/helpers.py)
      2. Path(__file__).parent.parent / "helpers.py"         (lib/helpers.py)

    Returns:
        {function_name: function_object} 字典,或空字典(无 helpers.py 时)
    """
    # 尝试 api/helpers.py(脚本包根目录)
    # test_runtime.py 在 api/lib/runtime/, helpers.py 在 api/
    helpers_path = Path(__file__).resolve().parent.parent.parent / "helpers.py"
    if not helpers_path.exists():
        # 尝试 lib/helpers.py (框架内部场景)
        helpers_path = Path(__file__).resolve().parent.parent / "helpers.py"

    if not helpers_path.exists():
        return {}

    try:
        spec = importlib.util.spec_from_file_location("helpers", helpers_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 提取所有可调用的公共函数(不以 _ 开头)
        return {k: v for k, v in vars(mod).items()
                if callable(v) and not k.startswith("_")}
    except Exception as e:
        print(f"  ⚠️  加载 helpers.py 失败: {e}")
        return {}

# 导入鉴权模块 — 支持两种运行环境：
#   框架内（lib/auth.py 存在）→ 完整 AuthSession（含滑块登录）
#   生成脚本（lib/cookie_client.py 存在）→ cookie-only 轻量鉴权
try:
    from lib.auth import AuthSession
    _AUTH_MODE = "full"
except ImportError:
    _AUTH_MODE = "cookie"


class ResponseParser:
    """根据 response_contract 解析 API 响应。"""

    def __init__(self, contract: dict):
        self.envelope_keys = contract.get("envelope_keys", const.ENVELOPE_KEY_DEFAULTS)
        self.success_check = contract.get("success_check", const.SUCCESS_CHECK_DEFAULT)
        self.list_keys = contract.get("list_keys", const.LIST_KEY_DEFAULTS)
        self.total_keys = contract.get("total_keys", const.TOTAL_KEY_DEFAULTS)
        self.id_field = contract.get("id_field", const.DEFAULT_ID_FIELD)
        self._success_field = self.success_check.get("success_field", "success")
        self._error_field = self.success_check.get("error_field", "errorCode")

    def extract_entity(self, resp_json: dict) -> Optional[Any]:
        if not resp_json or not isinstance(resp_json, dict):
            return None
        for key in self.envelope_keys:
            if key in resp_json and resp_json[key] is not None:
                return resp_json[key]
        return None

    def is_success(self, status_code: int, resp_json: dict) -> bool:
        check_type = self.success_check.get("type", "field_and_absence")
        if check_type == "http_only":
            return 200 <= status_code < 300
        elif check_type == "field_and_absence":
            if status_code >= 300:
                return False
            if not resp_json or not isinstance(resp_json, dict):
                return status_code < 300
            success_field = self.success_check.get("success_field", "success")
            error_field = self.success_check.get("error_field", "errorCode")
            success_val = resp_json.get(success_field)
            error_val = resp_json.get(error_field)
            return bool(success_val) and not error_val
        else:
            return 200 <= status_code < 300

    def extract_list(self, entity: Any) -> tuple[list, int]:
        if entity is None:
            return [], 0
        if isinstance(entity, list):
            return entity, len(entity)
        if isinstance(entity, dict):
            for key in self.list_keys:
                if key in entity and isinstance(entity[key], list):
                    items = entity[key]
                    total = len(items)
                    for total_key in self.total_keys:
                        if total_key in entity:
                            total = entity[total_key]
                            break
                    return items, total
        return [], 0

    def extract_id(self, entity: Any) -> Optional[str]:
        if entity is None:
            return None
        if isinstance(entity, dict):
            id_val = entity.get(self.id_field)
            if id_val is not None:
                return str(id_val)
        return None

    def extract_by_path(self, obj: Any, path: str) -> Optional[Any]:
        if not path or obj is None:
            return None
        # 支持数组索引，如 entity[0].poolId
        import re
        parts = path.split(".")
        current = obj
        for part in parts:
            if current is None:
                return None
            # 检查是否包含数组索引，如 entity[0]
            match = re.match(r'^([^\[]+)\[(\d+)\]$', part)
            if match:
                key, index = match.groups()
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    return None
                if current is None or not isinstance(current, list):
                    return None
                idx = int(index)
                if idx >= len(current):
                    return None
                current = current[idx]
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


class StepExecutor:
    """执行单个 CRUD 步骤。"""

    def __init__(self, session: requests.Session, parser: ResponseParser,
                 state: dict, ts: str, base_url: str, log_file: str = None,
                 config: dict = None, helpers: dict = None):
        self.session = session
        self.parser = parser
        self.state = state
        self.ts = ts
        self.base_url = base_url
        self.log_file = log_file
        self.config = config or {
            "test_data": {
                "name_prefix": "AT_",
                "mutable_prefix": "Updated_",
            }
        }
        self._helpers = helpers or {}
        self._api_call_count = 0
        self._last_request = {}

    def _log_event(self, event_type: str, data: dict):
        """Write a non-API event to the log."""
        if not self.log_file:
            return
        try:
            entry = {"type": event_type}
            entry.update(data)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def execute(self, step_def: dict) -> bool:
        action = step_def.get("action", "unknown")
        label = step_def.get("label", action)

        # ★ 多阶段操作：按 phases 顺序执行
        phases = step_def.get("phases")
        if phases:
            return self._execute_phased_step(step_def, phases)

        print(f"\n[{label}] {step_def['api']['pathname']}")

        # 前置检查
        requires = step_def.get("requires", [])
        for req in requires:
            if not self.state.get(req):
                print(f"  ⚠️ 跳过[{label}]: {req} 为空")
                # 写入跳过日志
                self._write_log(action, label, None, "skipped", f"{req} 为空")
                return False

        url = None
        method = None
        body = None
        resp = None

        try:
            url = self._build_url(step_def["api"])
            body = self._build_body(step_def)
            method = step_def["api"]["method"].upper()

            # search_verify / search_not_found: 添加搜索参数
            assertion = step_def.get("assertion", "")
            if assertion in ("search_verify", "search_not_found"):
                search_param = step_def.get("search_param", "")
                if search_param:
                    create_body = self.state.get("create_body", {})
                    create_body_raw = self.state.get("create_body_raw", {})
                    # search_param_source: create body 字段名 + API URL 参数名（如 userName）
                    # search_param: endpoint_classifier 提取的参数名（可能不准确）
                    search_source = step_def.get("search_param_source", search_param)
                    # 优先用 create_body（加了时间戳前缀的实际值），fallback 到 raw
                    search_value = create_body.get(search_source) or create_body_raw.get(search_source, "")
                    if search_value:
                        # POST 列表查询：搜索参数注入到 body（而非 URL）
                        if method == "POST" and isinstance(body, dict):
                            body[search_source] = search_value
                        else:
                            url += f"&{search_source}={search_value}" if "?" in url else f"?{search_source}={search_value}"

            resp = self._send_request(method, url, body)

            # 保存当前请求信息（用于日志更新）
            self._last_request = {
                "method": method, "url": url,
                "req_headers": dict(self.session.headers),
                "req_body": body if body else None,
            }

            self._assert_response(resp, label)

            if step_def.get("extract"):
                self._extract_state(resp.json(), step_def)

            # 验证步骤：query 后的断言
            assertion_msg = self._run_post_assertion(step_def, resp)

            print(f"  ✅ {label}成功")
            if assertion_msg:
                print(f"  ✅ {assertion_msg}")

            # 写成功日志
            self._write_log(action, label, resp, "passed", assertion_msg)
            return True

        except AssertionError as e:
            print(f"  ❌ {label}断言失败: {e}")
            self._write_log(action, label, resp, "failed", str(e))
            return False
        except Exception as e:
            print(f"  ⚠️ {label}异常: {e}")
            self._write_log(action, label, resp, "error", str(e))
            return False

    def _extract_with_array_index(self, obj, path: str):
        """从嵌套对象中提取值，支持数组索引路径。

        支持路径格式:
          - "entity.id" -> obj["entity"]["id"]
          - "entity.list[0].id" -> obj["entity"]["list"][0]["id"]
        """
        if not path or obj is None:
            return None

        # 分割路径段
        segments = []
        current = ""

        i = 0
        while i < len(path):
            char = path[i]

            if char == '.':
                if current:
                    segments.append(current)
                    current = ""
            elif char == '[':
                if current:
                    segments.append(current)
                    current = ""
                # 解析数组索引
                j = i + 1
                while j < len(path) and path[j] != ']':
                    j += 1
                if j < len(path):
                    index_str = path[i+1:j]
                    segments.append(f"[{index_str}]")
                    i = j
            else:
                current += char

            i += 1

        if current:
            segments.append(current)

        # 遍历路径段提取值
        value = obj
        for segment in segments:
            if value is None:
                return None

            # 数组索引段
            if segment.startswith("[") and segment.endswith("]"):
                try:
                    index = int(segment[1:-1])
                    if isinstance(value, list) and 0 <= index < len(value):
                        value = value[index]
                    else:
                        return None
                except (ValueError, IndexError):
                    return None
            # 字典字段段
            elif isinstance(value, dict):
                value = value.get(segment)
            else:
                return None

        return value

    def _execute_phased_step(self, step_def: dict, phases: list) -> bool:
        """执行多阶段操作步骤。

        phases 按顺序执行，每个 phase 的 extract 写入 self.state，
        后续 phase 的 body 通过 phase_ref 引用前面 phase 的 extract。
        """
        action = step_def.get("action", "")
        label = step_def.get("label", action)

        # requires 检查
        requires = step_def.get("requires", [])
        for req in requires:
            if not self.state.get(req):
                print(f"  ⚠️ 跳过[{label}]: {req} 为空")
                self._write_log(action, label, None, "skipped", f"{req} 为空")
                return False

        last_resp = None
        for i, phase in enumerate(phases):
            phase_id = phase.get("id", f"phase_{i}")
            phase_label = phase.get("label", f"{label}:{phase_id}")

            print(f"\n  [{phase_label}] {phase['api']['pathname']}")

            try:
                url = self._build_url(phase["api"])
                body = self._build_body(phase)
                method = phase["api"]["method"].upper()

                resp = self._send_request(method, url, body)

                # 保存当前请求信息（用于日志更新）
                self._last_request = {
                    "method": method, "url": url,
                    "req_headers": dict(self.session.headers),
                    "req_body": body if body else None,
                }

                # phase extract → state
                extracts = phase.get("extract", [])
                if extracts:
                    resp_json = resp.json()
                    for ext in extracts:
                        name = ext.get("name", "")
                        path = ext.get("path", "")
                        value = self._extract_with_array_index(resp_json, path)
                        if value is not None:
                            self.state[name] = value
                            print(f"    ✅ 提取: {name}={str(value)[:50]}")

                last_resp = resp

                # 非最终 phase：轻量级校验（HTTP 2xx + 业务成功标记）
                if i < len(phases) - 1:
                    if not (200 <= resp.status_code < 300):
                        raise AssertionError(f"phase {phase_id} HTTP {resp.status_code}")
                    try:
                        resp_json = resp.json()
                        if isinstance(resp_json, dict):
                            success_val = resp_json.get("success", resp_json.get("code"))
                            if success_val is False or (isinstance(success_val, int) and success_val not in (0, 200)):
                                msg = resp_json.get("message", resp_json.get("msg", ""))
                                raise AssertionError(f"phase {phase_id} 业务失败: {msg}")
                    except (ValueError, AttributeError):
                        pass  # 无法解析 JSON 时不阻塞

                # 最后一个 phase（main）做断言
                if i == len(phases) - 1:
                    self._assert_response(resp, label)
                    if step_def.get("extract"):
                        self._extract_state(resp.json(), step_def)

                # phase 级日志
                self._write_log(action, label, resp, "passed", "",
                              phase_info={"index": i, "id": phase_id})

            except AssertionError as e:
                print(f"    ❌ {phase_label} 断言失败: {e}")
                self._write_log(action, label, resp, "failed", str(e),
                              phase_info={"index": i, "id": phase_id})
                return False
            except Exception as e:
                print(f"    ❌ {phase_label} 失败: {e}")
                self._write_log(action, label, resp, "failed",
                              f"phase {phase_id}: {e}",
                              phase_info={"index": i, "id": phase_id})
                return False

        print(f"  ✅ {label}成功")
        return True

    def _write_log(self, action, label, resp, result, message="", phase_info=None):
        """写一条结构化 API 调用日志（JSON Lines 格式）。"""
        if not self.log_file:
            return
        try:
            self._api_call_count += 1
            last_req = getattr(self, '_last_request', {})
            resp_json = None
            resp_headers = {}
            resp_status = 0
            if resp:
                resp_status = resp.status_code
                resp_headers = dict(resp.headers)
                try:
                    resp_json = resp.json()
                except Exception:
                    resp_json = resp.text[:2000] if resp.text else None

            entry = {
                "type": "api_call",
                "seq": self._api_call_count,
                "step_action": action,
                "step_label": label,
                "request": {
                    "method": last_req.get("method", ""),
                    "url": last_req.get("url", ""),
                    "headers": last_req.get("req_headers", {}),
                    "body": last_req.get("req_body"),
                },
                "response": {
                    "status": resp_status,
                    "headers": resp_headers,
                    "body": resp_json,
                },
                "assertion": result,
                "assertion_message": message,
                "phase": phase_info,
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _resolve_path_param(self, source: str) -> str:
        """解析 path_param 的来源，从 state 中取值。

        Stage 3 已完成关联分析，此处只做纯粹的 source → state 取值。

        Args:
            source: Stage 3 分析确定的来源路径，格式如：
                - "create.id" → self.state["id"]
                - "display_by_role.entity_0_children_0_id" → self.state["entity_0_children_0_id"]
                - "auth.xxx" → self.state["xxx"]

        Returns:
            解析后的值，找不到则返回空字符串
        """
        if not source or "." not in source:
            return ""

        parts = source.split(".", 1)
        prefix = parts[0]
        field = parts[1]

        if prefix == "create":
            # create.id → 创建的资源 ID
            return self.state.get(field, "")
        elif prefix == "auth":
            # auth.tenantId → 登录上下文
            return self.state.get(field, "")
        else:
            # pre-API: "display_by_role.entity_0_children_0_id"
            # state 中 key 不含 api_id 前缀，直接用 field 查找
            return self.state.get(field, "")

    def _build_url(self, api: dict) -> str:
        pathname = api["pathname"]

        # ★ 优先处理 path_params（Stage 3 分析的值匹配关联）
        path_params = api.get("path_params", {})
        if path_params:
            for placeholder, mapping in path_params.items():
                source = mapping.get("source", "")
                val = self._resolve_path_param(source)
                if val:
                    pathname = pathname.replace(f"{{{placeholder}}}", str(val))

        # 替换任意 {paramName} 占位符（不仅限于 {id}）— 向后兼容
        placeholders = re.findall(r'\{(\w+)\}', pathname)
        for key in placeholders:
            val = self.state.get(key, "")
            if val:
                pathname = pathname.replace(f"{{{key}}}", str(val))
            elif key == "id":
                # 兼容：id 为空时保留原行为（替换为空字符串）
                pathname = pathname.replace("{id}", "")
        url = self.base_url + pathname
        query_params = api.get("query_params", {})
        if query_params:
            resolved_params = {}
            for k, v in query_params.items():
                if isinstance(v, str) and v.startswith("$"):
                    ref = v[1:]
                    parts = None
                    if "." in ref:
                        parts = ref.split(".", 1)
                        if parts[0] == "create_body":
                            val = self.state.get("create_body", {}).get(parts[1], "")
                        else:
                            # 支持 context source 格式: $api_id.field_name
                            # 先尝试完整 key，再回退到 field_name（state 中 key 不含 api_id 前缀）
                            val = self.state.get(ref, "")
                            if not val:
                                val = self.state.get(parts[1], "")
                    else:
                        val = self.state.get(ref, "")

                    # ★ UUID 兜底：解析失败时优先使用 context 值，否则生成随机值
                    if not val:
                        # 1. 检查 state 中是否有同名 context 值可用
                        ctx_val = ""
                        if parts and len(parts) > 1:
                            ctx_val = self.state.get(parts[1], "")
                            # 如果 parts[1] 是提取路径名（如 entity_list_0_tenantId），
                            # 尝试提取基础字段名（如 tenantId）
                            if not ctx_val:
                                # 从 "entity_list_0_tenantId" 提取 "tenantId"
                                base_field = parts[1].split("_")[-1] if "_" in parts[1] else parts[1]
                                if base_field != parts[1]:
                                    ctx_val = self.state.get(base_field, "")
                        else:
                            ctx_val = self.state.get(ref, "")

                        if ctx_val:
                            val = ctx_val
                        elif k.lower().endswith("id"):
                            # 2. 生成并缓存（同一字段名复用同一 UUID）
                            val = uuid.uuid4().hex
                            self.state[ref] = val
                            if parts and len(parts) > 1:
                                self.state[parts[1]] = val
                            print(f"  ⚠️ query param {k}: 解析为空，生成 hex_id: {val[:20]}")
                        else:
                            val = ""
                    resolved_params[k] = val
                else:
                    resolved_params[k] = v
            if resolved_params:
                query_str = "&".join(f"{k}={v}" for k, v in resolved_params.items())
                url = url + "?" + query_str
        return url

    def _build_body(self, step_def: dict):
        """构建请求体，根据 5 角色体系动态替换字段值。"""
        body_template = step_def.get("body_template", {})
        field_roles = step_def.get("body_field_roles", {})
        if not body_template:
            return {}

        # 数组格式请求体（如 batch delete ["id1", "id2"]）
        if isinstance(body_template, list):
            arr_role = field_roles.get("__array_items__", {})
            if arr_role.get("role") == "context":
                source = arr_role.get("source", "create.id")
                # 从 context 解析 ID
                if "." in source:
                    api_id, field_name = source.split(".", 1)
                    if api_id == "create":
                        actual_id = self.state.get("id", "")
                    else:
                        actual_id = self.state.get(field_name, "")
                else:
                    actual_id = self.state.get("id", "")
                return [actual_id] * len(body_template)
            return list(body_template)

        body = {}
        for key, value in body_template.items():
            role_config = field_roles.get(key, {"role": "static"})
            role = role_config.get("role", "static")

            if role == "name":
                # name 角色：复用 create 步骤生成的名称（保证编辑等步骤使用同一名称）
                create_body = self.state.get("create_body", {})
                body[key] = create_body.get(key, value) if isinstance(create_body, dict) else value

            elif role == "mutable":
                # mutable 角色：解析 ${...} 表达式
                body[key] = _resolve_expression(value, self._helpers)

            elif role == "context":
                # context 角色：从 state 中解析值
                source = role_config.get("source", f"context.{key}")
                is_array = role_config.get("is_array", False)
                # 模板值是数组时自动保持数组类型
                if isinstance(value, list):
                    is_array = True
                resolved = self._resolve_context_value(source, key, value, is_array, role_config)
                body[key] = resolved

            elif role == "generate":
                # generate 角色：解析 ${...} 表达式
                body[key] = _resolve_expression(value, self._helpers)

            else:
                # static 角色：原样保留，嵌套 dict 递归处理
                if isinstance(value, dict):
                    prefix = f"{key}."
                    nested_roles = {
                        rk[len(prefix):]: rv
                        for rk, rv in field_roles.items()
                        if rk.startswith(prefix)
                    }
                    if nested_roles:
                        body[key] = self._build_body_nested(value, nested_roles)
                    else:
                        body[key] = value
                else:
                    body[key] = value

        return body

    def _resolve_context_value(self, source: str, field_name: str, default_value, is_array: bool,
                                role_config: dict = None):
        """解析 context 角色的值。

        Args:
            source: 来源路径，格式如 "auth.tenantId"、"create.id"、"api_id.field_name"
            field_name: 字段名（用于兜底查找）
            default_value: 默认值
            is_array: 是否数组类型
            role_config: 完整的角色配置（可能包含 state_key）

        Returns:
            解析后的值
        """
        resolved = None

        # ★ 如果有 state_key，优先使用（嵌套 static 提升场景）
        if role_config and role_config.get("state_key"):
            state_key = role_config["state_key"]
            resolved = self.state.get(state_key)
            if resolved is not None:
                # is_array 时确保值是数组
                if is_array and not isinstance(resolved, list):
                    resolved = [resolved] if resolved else default_value
                return resolved

        if source.startswith("auth."):
            # auth.xxx → 从 state 直接查找
            field = source.split(".", 1)[1]
            resolved = self.state.get(field)
        elif source.startswith("create."):
            # create.xxx → 从 state 直接查找（id_producer 提取的 ID）
            field = source.split(".", 1)[1]
            if field == "id":
                resolved = self.state.get("id")
            else:
                resolved = self.state.get(field)
        elif source.startswith("create_body."):
            # create_body.xxx → 从 create_body 中查找
            field = source.split(".", 1)[1]
            resolved = self.state.get("create_body", {}).get(field, default_value)
        elif "." in source:
            # api_id.field_name → 从 state 查找 field_name
            parts = source.split(".", 1)
            field_name_part = parts[1]
            resolved = self.state.get(field_name_part)
        else:
            # 无点号，直接用 source 查找
            resolved = self.state.get(source)

        # UUID 兜底：解析失败时使用默认值（而非生成随机 UUID）
        # body_template 中 context 字段值已被 Stage 3 清理为 None，
        # 所以 default_value 通常是 None。这确保运行时不会使用错误的过期数据。
        if resolved is None:
            if field_name.lower().endswith("id"):
                print(f"  ⚠️ {field_name}: context 解析失败 (source={source}), "
                      f"请检查前置 API 是否成功执行")
            resolved = default_value

        # is_array 时确保值是数组
        if is_array and not isinstance(resolved, list):
            resolved = [resolved] if resolved else default_value

        return resolved

    def _build_body_nested(self, template, field_roles):
        """递归处理嵌套对象中的动态字段。

        支持 5 角色体系（name/mutable/context/generate/static）。
        """
        result = {}
        for key, value in template.items():
            role_config = field_roles.get(key, {"role": "static"})
            role = role_config.get("role", "static")

            # name 角色
            if role == "name":
                result[key] = _resolve_expression(value, self._helpers)

            # mutable 角色
            elif role == "mutable":
                result[key] = _resolve_expression(value, self._helpers)

            # context 角色
            elif role == "context":
                source = role_config.get("source", f"context.{key}")
                is_array = role_config.get("is_array", False)
                resolved = self._resolve_context_value(source, key, value, is_array, role_config)
                result[key] = resolved

            # generate 角色
            elif role == "generate":
                # generate 角色：解析 ${...} 表达式
                result[key] = _resolve_expression(value, self._helpers)

            # static 角色（默认）
            else:
                if isinstance(value, dict):
                    prefix = f"{key}."
                    nested_roles = {
                        rk[len(prefix):]: rv
                        for rk, rv in field_roles.items()
                        if rk.startswith(prefix)
                    }
                    result[key] = self._build_body_nested(value, nested_roles) if nested_roles else value
                else:
                    result[key] = value

        return result

    def _send_request(self, method: str, url: str, body: dict,
                      step_action: str = "", step_label: str = "") -> requests.Response:
        # 打印请求信息（控制台）
        print(f"    >>> {method} {url}")
        if body:
            try:
                print(f"    body: {json.dumps(body, ensure_ascii=False, indent=2)}")
            except Exception:
                print(f"    body: {str(body)[:500]}")

        # 记录请求头（发请求前快照）
        req_headers = dict(self.session.headers)

        # 发送请求
        if method == "GET":
            resp = self.session.get(url, params=body if body else None)
        elif method == "DELETE":
            if body:
                resp = self.session.delete(url, json=body)
            else:
                resp = self.session.delete(url)
        elif method == "PUT":
            resp = self.session.put(url, json=body)
        elif method == "PATCH":
            resp = self.session.patch(url, json=body)
        else:
            resp = self.session.post(url, json=body)

        # 解析响应体
        resp_json = None
        if resp.text:
            try:
                resp_json = resp.json()
            except Exception:
                pass

        # 打印响应信息（控制台）
        print(f"    <<< {resp.status_code} {resp.headers.get('content-type', '')}")
        if resp_json is not None:
            success_val = resp_json.get(self.parser._success_field, "-")
            print(f"    {self.parser._success_field}: {success_val}")
            error_val = resp_json.get(self.parser._error_field)
            if error_val:
                print(f"    {self.parser._error_field}: {error_val}")
            print(f"    body: {json.dumps(resp_json, ensure_ascii=False, indent=2)[:2000]}")
        elif resp.text:
            print(f"    body: {resp.text[:500]}")

        return resp

    def _assert_response(self, resp: requests.Response, label: str):
        assert 200 <= resp.status_code < 300, f"HTTP {resp.status_code}"
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = None
        assert self.parser.is_success(resp.status_code, resp_json), \
            f"业务错误 {resp.text[:200]}"
        assert resp_json is not None, "无响应体"

    def _run_post_assertion(self, step_def: dict, resp: requests.Response) -> str:
        """执行步骤的后续断言（query 验证等）。返回断言消息或空字符串。"""
        assertion = step_def.get("assertion")
        if not assertion:
            return ""

        try:
            resp_json = resp.json()
        except Exception:
            return ""

        entity = self.parser.extract_entity(resp_json)
        target_id = self.state.get("id", "")

        # 当无 id_producer（纯查询模块）时，验证步骤只需确认 API 返回成功即可
        if not target_id and assertion in ("search_verify", "contains_id", "not_contains_id", "search_not_found"):
            if isinstance(entity, dict):
                items, total = self.parser.extract_list(entity)
                return f"验证通过: 响应成功 (共 {total} 条记录)"
            elif isinstance(entity, list):
                return f"验证通过: 响应成功 (共 {len(entity)} 条记录)"
            return f"验证通过: API 返回成功"

        if assertion == "contains_id":
            # 验证列表中是否包含刚创建的 ID
            if isinstance(entity, dict):
                items, total = self.parser.extract_list(entity)
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in items if isinstance(item, dict)
                )
                if found:
                    return f"验证通过: 列表中找到 ID={target_id} (共 {total} 条)"
                else:
                    raise AssertionError(f"列表中未找到 ID={target_id}")
            elif isinstance(entity, list):
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in entity if isinstance(item, dict)
                )
                if found:
                    return f"验证通过: 列表中找到 ID={target_id}"
                else:
                    raise AssertionError(f"列表中未找到 ID={target_id}")

        elif assertion == "not_contains_id":
            # 验证列表中不再包含已删除的 ID
            if isinstance(entity, dict):
                items, total = self.parser.extract_list(entity)
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in items if isinstance(item, dict)
                )
                if not found:
                    return f"验证通过: ID={target_id} 已从列表移除 (剩余 {total} 条)"
                else:
                    raise AssertionError(f"列表中仍存在 ID={target_id}")
            elif isinstance(entity, list):
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in entity if isinstance(item, dict)
                )
                if not found:
                    return f"验证通过: ID={target_id} 已从列表移除"
                else:
                    raise AssertionError(f"列表中仍存在 ID={target_id}")

        elif assertion == "field_changed":
            # 从 body_field_roles 中查找 role="name" 的字段，而非硬编码 policyName/userName
            if isinstance(entity, dict):
                field_roles = step_def.get("body_field_roles", {})
                name_fields = [
                    field_name for field_name, role_info in field_roles.items()
                    if isinstance(role_info, dict) and role_info.get("role") == "name"
                ]

                # 按优先级尝试：先找到的字段优先
                name_val = None
                for field_name in name_fields:
                    val = entity.get(field_name)
                    if val is not None:
                        name_val = val
                        break

                # 降级：如果 body_field_roles 中没有 name 字段，尝试通用字段
                if name_val is None:
                    name_val = entity.get("name") or entity.get("title") or entity.get("label")

                mutable_prefix = self.config.get("test_data", {}).get("mutable_prefix", "Updated_")
                if name_val and mutable_prefix in str(name_val):
                    return f"验证通过: 名称已更新为 {name_val}"

        elif assertion == "search_verify":
            # Same as contains_id - search by name and verify ID exists
            if isinstance(entity, dict):
                items, total = self.parser.extract_list(entity)
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in items if isinstance(item, dict)
                )
                if found:
                    return f"验证通过: 搜索中找到 ID={target_id} (共 {total} 条)"
                else:
                    raise AssertionError(f"搜索结果中未找到 ID={target_id}")
            elif isinstance(entity, list):
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in entity if isinstance(item, dict)
                )
                if found:
                    return f"验证通过: 搜索中找到 ID={target_id}"
                else:
                    raise AssertionError(f"搜索结果中未找到 ID={target_id}")

        elif assertion == "search_not_found":
            # Same as not_contains_id - search by name and verify ID doesn't exist
            if isinstance(entity, dict):
                items, total = self.parser.extract_list(entity)
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in items if isinstance(item, dict)
                )
                if not found:
                    return f"验证通过: 搜索中未找到 ID={target_id} (剩余 {total} 条)"
                else:
                    raise AssertionError(f"搜索结果中仍存在 ID={target_id}")
            elif isinstance(entity, list):
                found = any(
                    str(item.get(self.parser.id_field)) == str(target_id)
                    for item in entity if isinstance(item, dict)
                )
                if not found:
                    return f"验证通过: 搜索中未找到 ID={target_id}"
                else:
                    raise AssertionError(f"搜索结果中仍存在 ID={target_id}")

        return ""

    def _extract_state(self, resp_json: dict, step_def: dict):
        entity = self.parser.extract_entity(resp_json)
        if entity is None:
            return

        # 使用 step 的 extract 配置提取所有字段（包括 ID）
        extract_config = step_def.get("extract", {})
        if extract_config and isinstance(extract_config, dict):
            for key, path in extract_config.items():
                if key == "names" or not isinstance(path, str):
                    continue
                val = self.parser.extract_by_path(resp_json, path)
                if val:
                    self.state[key] = str(val)
                    if key == "id":
                        print(f"  ✅ 提取 ID: {val}")

        # 降级：使用默认的 ID 提取逻辑
        if "id" not in self.state:
            id_val = self.parser.extract_id(entity)
            if not id_val and isinstance(entity, dict):
                # 尝试从列表响应中提取第一个 item 的 ID（用于查询操作）
                items, _ = self.parser.extract_list(entity)
                if items and len(items) > 0 and isinstance(items[0], dict):
                    id_val = self.parser.extract_id(items[0])
            if id_val:
                self.state["id"] = id_val
                print(f"  ✅ 提取 ID: {id_val}")

        field_roles = step_def.get("body_field_roles", {})
        create_body = self.state.get("create_body", {})
        for key, role_config in field_roles.items():
            if role_config.get("role") == "name":
                if key in entity:
                    self.state[key] = entity[key]
                elif key in create_body:
                    self.state[key] = create_body[key]


class TestRunner:
    """编排完整测试流程。"""

    def __init__(self, manifest: dict, shared_context: dict = None):
        self.manifest = manifest
        self.module_name = manifest.get("module", {}).get("name", "模块")
        self.base_url = manifest.get("module", {}).get("base_url", "")
        self.parser = ResponseParser(manifest.get("response_contract", {}))
        self.state = {}
        self.shared_context = shared_context or {}
        self.ts = format(int(time.time() * 1000), "x")[-6:]

        # 加载 helpers.py 数据生成函数（与 Stage 5 导出的 helpers.py 共享）
        self._helpers = _load_helpers()

        # 动态计算输出目录（避免框架内 import 时在根目录创建 report/）
        # 生成脚本场景：api/lib/runtime/test_runtime.py → parent.parent.parent = api/
        # 框架内场景：lib/runtime/test_runtime.py → 使用 workspace/ 避免污染项目根
        _self = Path(__file__).resolve()
        _base = _self.parent.parent.parent
        if _self.parent.parent.name == "lib" and _base.name == "api":
            # 生成脚本：api/lib/runtime/test_runtime.py
            self._base_dir = _base  # api/（config/ 在此目录下）
            # JSONL 日志输出到 workspace/<project>/output/logs/（不污染 projects/）
            # 向上找 projects/ 目录，其 parent 是框架根
            project_name = _base.parent.parent.name  # api → version → project_name
            framework_root = None
            for p in _base.parents:
                if p.name == "projects":
                    framework_root = p.parent
                    break
            if framework_root is None:
                framework_root = _self.parent.parent.parent.parent  # 兜底
            self._log_dir = framework_root / "workspace" / project_name / "output" / "logs"
            self._log_dir.mkdir(parents=True, exist_ok=True)
        else:
            # 框架内 import：使用 workspace/<project>/output/
            # 从环境变量或当前工作目录推断项目名
            import os
            project_name = os.environ.get("PROJECT_NAME")
            if not project_name:
                # 尝试从当前工作目录推断
                cwd = Path.cwd()
                if "projects" in cwd.parts:
                    idx = cwd.parts.index("projects")
                    if idx + 1 < len(cwd.parts):
                        project_name = cwd.parts[idx + 1]
            if not project_name:
                project_name = "_default"
            self._base_dir = _base.parent / "workspace" / project_name / "output"
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._log_dir = self._base_dir

        # 测试配置（名称前缀、可变前缀等）
        self.config = manifest.get("config", {
            "test_data": {
                "name_prefix": "AT_",
                "mutable_prefix": "Updated_",
            }
        })

        # 设置日志文件
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = str(self._log_dir / f"{self.module_name}_API测试.jsonl")
        # 清空旧日志
        try:
            open(self.log_file, "w", encoding="utf-8").close()
        except Exception:
            pass

    def setup_auth(self) -> Optional[requests.Session]:
        """设置鉴权 — 支持两种模式:
        - full: 使用 AuthSession（含滑块登录回退）
        - cookie: 仅使用 cookie_client（生成脚本专用）
        """
        auth_profile = self.manifest.get("auth_profile", {})
        creds_env = auth_profile.get("credentials_env", {})
        creds_default = auth_profile.get("credentials_default", {})

        # Cookie-only 模式（生成脚本）
        if _AUTH_MODE == "cookie":
            from lib.cookie_client import require_auth
            config_dir = self._base_dir / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            auth_config = {
                "header_name": auth_profile.get("header_name", "Authorization"),
                "header_prefix": auth_profile.get("header_prefix", "Bearer "),
                "fixed_headers": auth_profile.get("fixed_headers", {}),
                "cookie_token_key": auth_profile.get("cookie_token_key", ""),
            }

            session, cookies = require_auth(config_dir, auth_config)
            print("  ✅ Cookie 鉴权成功")
            return session

        # Full auth 模式（框架内）
        profile = {
            "login_url": self.manifest.get("module", {}).get("login_url", ""),
            "auth": {
                "header_name": auth_profile.get("header_name", "Authorization"),
                "header_prefix": auth_profile.get("header_prefix", "Bearer "),
                "freshness_ttl_seconds": auth_profile.get("freshness_ttl_seconds", 300),
                "fixed_headers": auth_profile.get("fixed_headers", {}),
            },
            "captcha": auth_profile.get("captcha", {}),
            "probe_url": auth_profile.get("probe_url", ""),
            "credentials": {
                "username_env": creds_env.get("username", "APP_USER"),
                "password_env": creds_env.get("password", "APP_PASS"),
                "username": creds_default.get("username", ""),
                "password": creds_default.get("password", ""),
            },
        }

        username = os.environ.get(creds_env.get("username", "APP_USER")) or \
                   creds_default.get("username", "")
        password = os.environ.get(creds_env.get("password", "APP_PASS")) or \
                   creds_default.get("password", "")

        sess = AuthSession(profile, username, password)

        ctx_dir = self._base_dir / "config"
        ctx_dir.mkdir(parents=True, exist_ok=True)
        sess.set_context_path(str(ctx_dir / "context.json"))

        session = requests.Session()
        session.verify = False

        fixed_headers = auth_profile.get("fixed_headers", {})
        fixed_headers["Content-Type"] = "application/json"
        session.headers.update(fixed_headers)

        sess.load_context()
        if sess.token and sess.probe_online(self.base_url):
            session.headers[auth_profile.get("header_name", "Authorization")] = \
                f"{auth_profile.get('header_prefix', 'Bearer ')}{sess.token}"
            for c in sess._cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
            print("  ✅ 使用已有鉴权")
            return session

        if os.environ.get("AUTO_LOGIN", "1") != "0":
            client = sess.ensure_client(self.base_url)
            if client and sess.token:
                session.headers[auth_profile.get("header_name", "Authorization")] = \
                    f"{auth_profile.get('header_prefix', 'Bearer ')}{sess.token}"
                for c in sess._cookies:
                    session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
                print("  ✅ 自动登录成功")
                return session

        print("❌ 无法获取有效鉴权")
        print("   请设置 AUTO_LOGIN=1 或先运行发现流程刷新 cookie")
        return None

    def fetch_context(self, session: requests.Session):
        """从 probe_url 获取上下文字段（tenantId, adminId 等）。

        如果 probe_url 配置了但请求失败，则退出测试（这些字段通常是必需的）。
        """
        auth_profile = self.manifest.get("auth_profile", {})
        probe_url = auth_profile.get("probe_url", "")
        context_fields = auth_profile.get("context_fields", {})
        if not probe_url:
            return

        url = self.base_url + probe_url
        print(f"  🔍 获取上下文: {url}")

        try:
            resp = session.get(url, timeout=10)
        except Exception as e:
            print(f"  ❌ 无法连接 probe_url: {e}")
            print(f"     Cookie/Token 可能已过期，请刷新")
            sys.exit(1)

        if resp.status_code >= 300:
            print(f"  ❌ probe_url 返回 HTTP {resp.status_code}")
            print(f"     Cookie/Token 可能已过期，请刷新")
            sys.exit(1)

        try:
            resp_json = resp.json()
        except Exception:
            print(f"  ❌ probe_url 响应不是 JSON")
            sys.exit(1)

        extracted = []
        for field, config in context_fields.items():
            path = config.get("path", "")
            value = self.parser.extract_by_path(resp_json, path)
            if value is not None:
                self.state[field] = value
                extracted.append(f"{field}={value}")
            else:
                print(f"  ⚠️ 无法提取 {field} (path: {path})")

        if extracted:
            print(f"  ✅ 上下文已加载: {', '.join(extracted)}")
        else:
            print(f"  ⚠️ 未提取到任何上下文字段")

    def execute_pre_apis(self, session: requests.Session):
        """执行前置 API 链，提取字段到 state，并写入 JSONL 日志。"""
        pre_apis = self.manifest.get("pre_apis", [])

        # 向后兼容：如果没有 pre_apis，使用旧的 fetch_context
        if not pre_apis:
            self.fetch_context(session)
            return

        print(f"\n📡 执行前置 API 链 ({len(pre_apis)} 个)")

        for pre_api in pre_apis:
            api_id = pre_api.get("id", "unknown")
            api_name = pre_api.get("name", api_id)
            pathname = pre_api.get("pathname", "")
            method = pre_api.get("method", "GET")
            extracts = pre_api.get("extracts", [])

            if not pathname:
                print(f"  ⚠️ 跳过 {api_name}: 缺少 pathname")
                continue

            url = self.base_url + pathname
            print(f"  → {api_name}: {method} {pathname}")

            resp = None
            result = "passed"
            message = ""
            extracted = []

            try:
                # 根据方法类型发送请求
                if method.upper() == "GET":
                    resp = session.get(url, timeout=10)
                else:
                    body = pre_api.get("body_template", {})
                    resp = session.request(method, url, json=body, timeout=10)

                if resp.status_code >= 300:
                    result = "failed"
                    message = f"HTTP {resp.status_code}"
                else:
                    try:
                        resp_json = resp.json()
                    except Exception:
                        result = "error"
                        message = "响应不是 JSON"
                        resp_json = None

                    if resp_json:
                        # 提取字段
                        for extract in extracts:
                            field_name = extract.get("name", "")
                            field_path = extract.get("path", "")

                            if not field_name or not field_path:
                                continue

                            # 支持数组索引路径 (如 entity.list[0].id)
                            value = self._extract_with_array_index(resp_json, field_path)

                            if value is not None:
                                self.state[field_name] = value
                                extracted.append(f"{field_name}={value}")
                            else:
                                print(f"    ⚠️ 无法提取 {field_name} (path: {field_path})")

                        if extracted:
                            message = f"提取: {', '.join(extracted)}"

            except Exception as e:
                result = "error"
                message = str(e)

            # 写入 JSONL 日志
            self._log_pre_api_call(api_name, method, url, resp, result, message)

            if result == "passed":
                print(f"    ✅ {message}")
            else:
                print(f"    ❌ {message}")

        print(f"  ✅ 前置 API 执行完成")

    def _log_pre_api_call(self, label: str, method: str, url: str,
                          resp, result: str, message: str):
        """将前置 API 调用写入 JSONL 日志。"""
        if not self.log_file:
            return
        try:
            resp_json = None
            resp_headers = {}
            resp_status = 0
            if resp:
                resp_status = resp.status_code
                resp_headers = dict(resp.headers)
                try:
                    resp_json = resp.json()
                except Exception:
                    resp_json = resp.text[:2000] if resp.text else None

            entry = {
                "type": "api_call",
                "seq": 0,  # 前置 API 使用 seq=0，业务步骤从 1 开始
                "step_action": "pre_api",
                "step_label": f"[前置] {label}",
                "request": {
                    "method": method,
                    "url": url,
                    "headers": {},
                    "body": None,
                },
                "response": {
                    "status": resp_status,
                    "headers": resp_headers,
                    "body": resp_json,
                },
                "assertion": result,
                "assertion_message": message,
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _extract_with_array_index(self, obj: Any, path: str) -> Optional[Any]:
        """从嵌套对象中提取值，支持数组索引路径。

        支持路径格式:
          - "entity.id" -> obj["entity"]["id"]
          - "entity.list[0].id" -> obj["entity"]["list"][0]["id"]

        Args:
            obj: 嵌套字典/列表对象
            path: 点分隔路径，可包含数组索引 [n]

        Returns:
            提取的值，失败返回 None
        """
        if not path or obj is None:
            return None

        # 分割路径段
        segments = []
        current = ""

        i = 0
        while i < len(path):
            char = path[i]

            if char == '.':
                if current:
                    segments.append(current)
                    current = ""
            elif char == '[':
                if current:
                    segments.append(current)
                    current = ""
                # 解析数组索引
                j = i + 1
                while j < len(path) and path[j] != ']':
                    j += 1
                if j < len(path):
                    index_str = path[i+1:j]
                    segments.append(f"[{index_str}]")
                    i = j
            else:
                current += char

            i += 1

        if current:
            segments.append(current)

        # 遍历路径段提取值
        value = obj
        for segment in segments:
            if value is None:
                return None

            # 数组索引段
            if segment.startswith("[") and segment.endswith("]"):
                try:
                    index = int(segment[1:-1])
                    if isinstance(value, list) and 0 <= index < len(value):
                        value = value[index]
                    else:
                        return None
                except (ValueError, IndexError):
                    return None
            # 字典字段段
            elif isinstance(value, dict):
                value = value.get(segment)
            else:
                return None

        return value

    def prepare_create_body(self, step_def: dict):
        # 分支：单 API step vs phases step
        phases = step_def.get("phases")
        if phases:
            # phases step：从 main phase（最后一个）获取 body 配置
            main_phase = phases[-1]
            body_template = main_phase.get("body_template", {})
            field_roles = main_phase.get("body_field_roles", {})
        else:
            # 单 API step（原有逻辑）
            body_template = step_def.get("body_template", {})
            field_roles = step_def.get("body_field_roles", {})

        create_body = {}
        create_body_raw = {}  # 保存原始值（用于搜索验证）

        for key, value in body_template.items():
            role_config = field_roles.get(key, {"role": "static"})
            role = role_config.get("role", "static")

            # 保存原始值
            create_body_raw[key] = value

            if role == "name":
                # name 角色：解析 ${...} 表达式
                create_body[key] = _resolve_expression(value, self._helpers)
            elif role == "generate":
                # generate 角色：解析 ${...} 表达式
                create_body[key] = _resolve_expression(value, self._helpers)
            elif role == "context":
                # context 角色：从 state 中解析实际值（前置 API 已执行完毕）
                # 这样后续步骤通过 create_body.xxx 引用时能拿到正确的值
                source = role_config.get("source", f"context.{key}")
                if "." in source:
                    parts = source.split(".", 1)
                    resolved = self.state.get(parts[1], value)
                else:
                    resolved = self.state.get(source, value)
                create_body[key] = resolved
            else:
                create_body[key] = value

        self.state["create_body"] = create_body
        self.state["create_body_raw"] = create_body_raw  # 供搜索验证使用

    def _inject_shared_context(self):
        """将共享上下文值注入到 self.state"""
        for key, value in self.shared_context.items():
            self.state[key] = value
        print(f"  ✅ 已注入共享上下文: {list(self.shared_context.keys())}")

    def run(self, steps_filter: Optional[list] = None):
        print("=" * 60)
        print(f"  {self.module_name} API 测试")
        print("=" * 60)

        session = self.setup_auth()
        if session is None:
            return

        # 三路分支：共享上下文 > 模块级前置 API > fetch_context
        if self.shared_context:
            self._inject_shared_context()
        else:
            # 执行前置 API 链（如果有）或回退到 fetch_context
            self.execute_pre_apis(session)

        steps = self.manifest.get("steps", [])
        create_step = next((s for s in steps if s.get("extract")), None)
        if create_step:
            self.prepare_create_body(create_step)

        if steps_filter:
            steps_to_run = [s for s in steps if s.get("action") in steps_filter]
        else:
            steps_to_run = steps

        print(f"\n🚀 执行步骤: {', '.join(s['action'] for s in steps_to_run)} "
              f"(共 {len(steps_to_run)}/{len(steps)})")

        executor = StepExecutor(session, self.parser, self.state, self.ts,
                                self.base_url, log_file=self.log_file,
                                config=self.config, helpers=self._helpers)

        # 写入测试开始事件
        executor._log_event("test_start", {
            "module": self.module_name,
            "base_url": self.base_url,
            "target_url": self.manifest.get("module", {}).get("target_url", ""),
            "total_steps": len(steps_to_run),
        })

        try:
            failed_count = 0
            for step_def in steps_to_run:
                print(f"\n{'=' * 60}")
                print(f"步骤: {step_def['action']}")
                print("=" * 60)
                ok = executor.execute(step_def)
                if not ok:
                    failed_count += 1
        except Exception as e:
            print(f"\n❌ 主流程异常: {e}")
            import traceback
            traceback.print_exc()
            failed_count = len(steps_to_run)

        # 写入测试完成事件
        executor._log_event("test_end", {
            "total_api_calls": executor._api_call_count,
        })

        # 输出摘要
        total = len(steps_to_run)
        passed = total - failed_count

        print("\n" + "=" * 60)
        print(f"  📊 结果: {passed}/{total} 通过, {failed_count} 失败")
        print("=" * 60)
        print(f"  📋 日志已写入: {self.log_file}")

        # 自动生成 HTML 报告
        try:
            from lib.test_report import parse_jsonl_log, generate_postman_report
            api_calls, events = parse_jsonl_log(self.log_file)
            if api_calls:
                report_path = generate_postman_report(api_calls, events, self.module_name)
                print(f"  📊 报告已生成: {report_path}")
        except Exception as e:
            print(f"  ⚠️ 报告生成失败: {e}")

        # 失败时非零退出，供 Stage 4 校验检测
        if failed_count > 0:
            sys.exit(1)
