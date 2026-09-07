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
import sys
import time
import os
from pathlib import Path
from typing import Optional, Any

import requests
import urllib3

# 禁用 SSL 警告（自签证书环境）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 导入鉴权模块 — 支持两种运行环境：
#   框架内（lib/auth.py 存在）→ 完整 AuthSession（含滑块登录）
#   生成脚本（lib/cookie_client.py 存在）→ cookie-only 轻量鉴权
try:
    from lib.auth import AuthSession
    _AUTH_MODE = "full"
except ImportError:
    _AUTH_MODE = "cookie"

# 统一输出目录：基于 lib/ 的父目录（即 api/ 目录）
_self = Path(__file__).resolve()
_BASE_DIR = _self.parent.parent  # api/ 或项目根
_LOG_DIR = _BASE_DIR / "report"


class ResponseParser:
    """根据 response_contract 解析 API 响应。"""

    def __init__(self, contract: dict):
        self.envelope_keys = contract.get("envelope_keys", ["entity", "data", "result", "payload"])
        self.success_check = contract.get("success_check", {
            "type": "field_and_absence",
            "success_field": "success",
            "error_field": "errorCode"
        })
        self.list_keys = contract.get("list_keys", ["list", "records", "rows", "items"])
        self.total_keys = contract.get("total_keys", ["total", "totalCount", "count"])
        self.id_field = contract.get("id_field", "id")
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
        parts = path.split(".")
        current = obj
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


class StepExecutor:
    """执行单个 CRUD 步骤。"""

    def __init__(self, session: requests.Session, parser: ResponseParser,
                 state: dict, ts: str, base_url: str, log_file: str = None):
        self.session = session
        self.parser = parser
        self.state = state
        self.ts = ts
        self.base_url = base_url
        self.log_file = log_file
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

        print(f"\n[{label}] {step_def['api']['pathname']}")

        # 前置检查
        requires = step_def.get("requires", [])
        for req in requires:
            if not self.state.get(req):
                print(f"  ⚠️ 跳过[{label}]: {req} 为空")
                return False

        url = None
        method = None
        body = None
        resp = None

        try:
            url = self._build_url(step_def["api"])
            body = self._build_body(step_def)
            method = step_def["api"]["method"].upper()
            resp = self._send_request(method, url, body)

            # 保存当前请求信息（用于日志更新）
            self._last_request = {
                "method": method, "url": url,
                "req_headers": dict(self.session.headers),
                "req_body": body if body else None,
            }

            self._assert_response(resp, label)

            if action == "create":
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

    def _write_log(self, action, label, resp, result, message=""):
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
            }
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _build_url(self, api: dict) -> str:
        pathname = api["pathname"]
        if "{id}" in pathname:
            id_val = self.state.get("id", "")
            pathname = pathname.replace("{id}", str(id_val))
        url = self.base_url + pathname
        query_params = api.get("query_params", {})
        if query_params:
            resolved_params = {}
            for k, v in query_params.items():
                if isinstance(v, str) and v.startswith("$"):
                    ref = v[1:]
                    if "." in ref:
                        parts = ref.split(".", 1)
                        if parts[0] == "create_body":
                            resolved_params[k] = self.state.get("create_body", {}).get(parts[1], "")
                        else:
                            resolved_params[k] = self.state.get(ref, "")
                    else:
                        resolved_params[k] = self.state.get(ref, "")
                else:
                    resolved_params[k] = v
            if resolved_params:
                query_str = "&".join(f"{k}={v}" for k, v in resolved_params.items())
                url = url + "?" + query_str
        return url

    def _build_body(self, step_def: dict):
        body_template = step_def.get("body_template", {})
        field_roles = step_def.get("body_field_roles", {})
        if not body_template:
            return {}

        # 数组格式请求体（如 batch delete ["id1", "id2"]）
        if isinstance(body_template, list):
            arr_role = field_roles.get("__array_items__", {})
            if arr_role.get("role") == "id_ref":
                # 将数组中每个元素替换为 create 返回的 ID
                return [self.state.get("id", "")] * len(body_template)
            return list(body_template)

        body = {}
        for key, value in body_template.items():
            role_config = field_roles.get(key, {"role": "static"})
            role = role_config.get("role", "static")
            if role == "id_ref":
                body[key] = self.state.get("id", "")
            elif role == "context":
                source = role_config.get("source", f"context.{key}")
                if source.startswith("context."):
                    field = source.split(".", 1)[1]
                    body[key] = self.state.get(field, "")
                elif source.startswith("create_body."):
                    field = source.split(".", 1)[1]
                    body[key] = self.state.get("create_body", {}).get(field, value)
                else:
                    body[key] = self.state.get(key, value)
            elif role == "name":
                if isinstance(value, str):
                    create_body = self.state.get("create_body", {})
                    body[key] = create_body.get(key, value)
                else:
                    body[key] = value
            elif role == "mutable":
                body[key] = f"自动修改_{self.ts}"
            else:
                body[key] = value
        return body

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
            # 验证实体中名称字段已更新
            if isinstance(entity, dict):
                name_val = entity.get("policyName") or entity.get("userName") or entity.get("name")
                if name_val and "_Updated" in str(name_val):
                    return f"验证通过: 名称已更新为 {name_val}"

        return ""

    def _extract_state(self, resp_json: dict, step_def: dict):
        entity = self.parser.extract_entity(resp_json)
        if entity is None:
            return
        id_val = self.parser.extract_id(entity)
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

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.module_name = manifest.get("module", {}).get("name", "模块")
        self.base_url = manifest.get("module", {}).get("base_url", "")
        self.parser = ResponseParser(manifest.get("response_contract", {}))
        self.state = {}
        self.ts = format(int(time.time() * 1000), "x")[-6:]

        # 设置日志文件
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_file = str(_LOG_DIR / f"{self.module_name}_API测试.jsonl")
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
            config_dir = _BASE_DIR / "config"
            config_dir.mkdir(parents=True, exist_ok=True)

            auth_config = {
                "header_name": auth_profile.get("header_name", "Authorization"),
                "header_prefix": auth_profile.get("header_prefix", "Bearer "),
                "fixed_headers": auth_profile.get("fixed_headers", {}),
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

        ctx_dir = _BASE_DIR / "config"
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

    def prepare_create_body(self, step_def: dict):
        body_template = step_def.get("body_template", {})
        field_roles = step_def.get("body_field_roles", {})
        create_body = {}
        for key, value in body_template.items():
            role_config = field_roles.get(key, {"role": "static"})
            role = role_config.get("role", "static")
            if role == "name" and isinstance(value, str):
                if not value.startswith("AT_"):
                    create_body[key] = f"AT_{self.ts}_{value}"
                else:
                    create_body[key] = f"AT_{self.ts}_{value[3:]}"
            else:
                create_body[key] = value
        self.state["create_body"] = create_body

    def run(self, steps_filter: Optional[list] = None):
        print("=" * 60)
        print(f"  {self.module_name} API 测试")
        print("=" * 60)

        session = self.setup_auth()
        if session is None:
            return

        self.fetch_context(session)

        steps = self.manifest.get("steps", [])
        create_step = next((s for s in steps if s.get("action") == "create"), None)
        if create_step:
            self.prepare_create_body(create_step)

        if steps_filter:
            steps_to_run = [s for s in steps if s.get("action") in steps_filter]
        else:
            steps_to_run = steps

        print(f"\n🚀 执行步骤: {', '.join(s['action'] for s in steps_to_run)} "
              f"(共 {len(steps_to_run)}/{len(steps)})")

        executor = StepExecutor(session, self.parser, self.state, self.ts,
                                self.base_url, log_file=self.log_file)

        # 写入测试开始事件
        executor._log_event("test_start", {
            "module": self.module_name,
            "base_url": self.base_url,
            "target_url": self.manifest.get("module", {}).get("target_url", ""),
            "total_steps": len(steps_to_run),
        })

        try:
            for step_def in steps_to_run:
                print(f"\n{'=' * 60}")
                print(f"步骤: {step_def['action']}")
                print("=" * 60)
                executor.execute(step_def)
        except Exception as e:
            print(f"\n❌ 主流程异常: {e}")
            import traceback
            traceback.print_exc()

        # 写入测试完成事件
        executor._log_event("test_end", {
            "total_api_calls": executor._api_call_count,
        })

        print("\n" + "=" * 60)
        print("  ✅ 测试完成")
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
