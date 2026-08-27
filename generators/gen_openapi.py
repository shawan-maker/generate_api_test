"""
gen_openapi.py — 从 api_catalog.json 导出为 OpenAPI 3.1 (Apifox 首选) + Postman Collection v2.1。
对应方案设计 §10 导出。
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_catalog(proj_dir: str) -> dict:
    p = Path(proj_dir) / "kb" / "api_catalog.json"
    if not p.exists():
        print(f"[export] ✗ 未找到 catalog: {p}")
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_profile(proj_dir: str) -> dict:
    p = Path(proj_dir) / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def _param_schema(param: dict) -> dict:
    """参数 → OpenAPI param object。"""
    return {
        "name": param.get("name", ""),
        "in": param.get("in", "query"),
        "schema": {"type": "string"},
        "description": f"sample: {param.get('sample_values', [])}",
    }


def _build_openapi(catalog: dict, profile: dict) -> dict:
    """从 IR -> OpenAPI 3.1。"""
    title = catalog.get("info", {}).get("title", "API")
    base_url = profile.get("base_url", "")
    api_base = profile.get("api_base", "")

    servers = []
    if base_url:
        servers.append({"url": base_url + (api_base or ""),
                        "description": profile.get("display_name", "")})

    paths = {}
    for p, methods in catalog.get("paths", {}).items():
        path_item = {}
        for method, ep in methods.items():
            # 构建 requestBody (有 body_keys 才加)
            body_keys = ep.get("x-kb-body-keys", [])
            request_body = None
            if body_keys or method in ("post", "put", "patch"):
                properties = {}
                required = []
                for bk in body_keys:
                    properties[bk] = {"type": "string", "description": ""}
                schema = {
                    "type": "object",
                    "properties": properties,
                }
                if required:
                    schema["required"] = required
                request_body = {
                    "required": True,
                    "content": {"application/json": {"schema": schema}},
                } if properties else None

            # 构建 responses
            resp_samples = {}
            for sample in ep.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("examples", []):
                sample_body = sample.get("response_sample", "")
                if sample_body:
                    try:
                        parsed = json.loads(sample_body) if isinstance(sample_body, str) else sample_body
                        resp_samples.setdefault("examples", []).append({
                            "summary": f"status={sample.get('status')}",
                            "value": parsed,
                        })
                    except Exception:
                        pass

            responses = {
                "200": {
                    "description": "Success",
                    "content": {
                        "application/json": {
                            "schema": {"type": "object"},
                            **resp_samples,
                        }
                    } if resp_samples else {}
                }
            }

            # 构建 operation
            operation = {
                "summary": ep.get("summary", f"{method} {p}"),
                "operationId": f"{method}_{p.replace('/','_').replace('{','').replace('}','')}",
                "tags": [ep.get("x-kb-service", "default")],
                "parameters": [_param_schema(param) for param in ep.get("parameters", [])],
                "responses": responses,
                # x-kb-* 扩展
                "x-kb-source": ep.get("x-kb-source"),
                "x-kb-trusted": ep.get("x-kb-trusted"),
                "x-kb-crud": ep.get("x-kb-crud"),
                "x-kb-service": ep.get("x-kb-service"),
                "x-kb-resource": ep.get("x-kb-resource"),
            }
            if request_body:
                operation["requestBody"] = request_body

            path_item[method] = operation
        paths[p] = path_item

    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": "1.0.0",
            "description": f"Exported from API_AI_test on {datetime.now(timezone.utc).isoformat()}\n"
                           f"Source: {catalog.get('x-kb-generated-at', '')}\n"
                           f"Endpoints: {catalog.get('x-kb-endpoint-count', 0)}\n"
                           "x-kb-* 扩展字段标注了来源/信任度/CRUD 分类/service 与资源归属。",
        },
        "servers": servers,
        "paths": paths,
    }


def _build_postman(catalog: dict, profile: dict) -> dict:
    """从 IR -> Postman Collection v2.1。

    包含:
      - 🔐 鉴权文件夹: 登录请求 + pre-request 自动刷新 token 脚本
      - 按 service 分组的 API 请求（URL 直接用 base_url + path，不重复 api_base）
      - 每个请求: Authorization/Estack-Language 头 + 自动断言 + 变量提取
    """
    base_url = profile.get("base_url", "")
    auth_cfg = profile.get("auth", {})
    fixed_headers = auth_cfg.get("fixed_headers", {})
    login_url = profile.get("login_url", "")

    # --- URL 构建: catalog 路径已含 api_base，只拼 base_url ---
    def _url_obj(path: str) -> dict:
        raw = f"{base_url}{path}" if base_url else path
        parts = [s for s in path.split("/") if s]
        variables = []
        resolved = []
        for seg in parts:
            if seg.startswith("{") and seg.endswith("}"):
                vname = seg.strip("{}")
                variables.append({"key": vname, "value": vname})
                resolved.append(f":{vname}")
            else:
                resolved.append(seg)
        return {
            "raw": raw,
            "protocol": base_url.split("://")[0] if "://" in base_url else "https",
            "host": base_url.split("://")[-1].split("/")[0].split(":")[0] if "://" in base_url else "",
            "path": resolved,
            "variable": variables,
        }

    # --- 公共请求头 ---
    common_headers = [
        {"key": "Content-Type", "value": "application/json"},
        {"key": "Authorization", "value": "Bearer {{token}}",
         "description": "鉴权 token（登录后自动设置，或手动填入）"},
    ]
    for hk, hv in fixed_headers.items():
        common_headers.append({"key": hk, "value": hv})

    items = []

    # ================================================================
    # 🔐 鉴权文件夹 — 登录请求 + pre-request 自动 token 刷新
    # ================================================================
    login_event = []
    if login_url:
        login_event.append({
            "listen": "test",
            "script": {
                "exec": [
                    "// 登录成功后自动提取 token 存入 collection 变量",
                    "try {",
                    "    var body = pm.response.json();",
                    "    // estack 约定: token 在 entity.accessToken 或顶层 accessToken",
                    "    var token = body.entity?.accessToken || body.accessToken",
                    "        || body.data?.accessToken || body.token;",
                    "    if (token) {",
                    "        pm.collectionVariables.set('token', token);",
                    "        console.log('✅ token 已更新: ' + token.substring(0, 20) + '...');",
                    "    } else {",
                    "        console.warn('⚠️ 响应中未找到 accessToken，请手动设置 token 变量');",
                    "    }",
                    "} catch(e) {",
                    "    console.warn('⚠️ 解析登录响应失败: ' + e.message);",
                    "}",
                ],
                "type": "text/javascript",
            },
        })

    auth_folder_items = [{
        "name": "POST 登录 (Login)",
        "request": {
            "method": "POST",
            "header": [{"key": "Content-Type", "value": "application/json"}],
            "body": {
                "mode": "raw",
                "raw": json.dumps({"username": "{{username}}", "password": "{{password}}"},
                                  ensure_ascii=False, indent=2),
                "options": {"raw": {"language": "json"}},
            },
            "url": {
                "raw": login_url or f"{base_url}/login",
                "protocol": (login_url or base_url).split("://")[0] if "://" in (login_url or base_url) else "https",
                "host": (login_url or base_url).split("://")[-1].split("/")[0].split(":")[0],
                "path": [s for s in (login_url or f"{base_url}/login").split("://")[-1].split("/", 1)[-1].split("/") if s] if "/" in (login_url or "").split("://")[-1] else [],
            },
            "description": (
                "登录接口。发送用户名密码，获取 token。\n\n"
                "**使用前**：在 Collection Variables 中填写 username 和 password。\n"
                "**运行后**：token 变量自动更新，后续请求自动携带。"
            ),
        },
        "event": login_event,
    }]

    # Pre-request script: token 过期时自动提示
    pre_request_lines = [
        "// 自动检查 token 有效性",
        "// 若 token 为空或已过期，请先运行「POST 登录」请求获取新 token",
        "var token = pm.collectionVariables.get('token');",
        "if (!token) {",
        "    console.warn('⚠️ token 未设置，请先运行登录请求');",
        "}",
    ]

    items.append({
        "name": "🔐 鉴权 / Login",
        "item": auth_folder_items,
        "event": [{
            "listen": "prerequest",
            "script": {"exec": pre_request_lines, "type": "text/javascript"},
        }],
    })

    # ================================================================
    # API 请求 — 按 service 分组
    # ================================================================
    service_items = {}  # service_name -> [items]

    for p, methods in catalog.get("paths", {}).items():
        for method, ep in methods.items():
            # 请求体
            body = None
            body_keys = ep.get("x-kb-body-keys", [])
            if body_keys and method in ("post", "put", "patch"):
                body = {
                    "mode": "raw",
                    "raw": json.dumps({k: "" for k in body_keys[:5]}, ensure_ascii=False, indent=2),
                    "options": {"raw": {"language": "json"}},
                }

            # 断言脚本
            crud = ep.get("x-kb-crud", "")
            test_script = f'''// API_AI_test — {crud} 自动断言
pm.test("Status 200", function() {{ pm.response.to.have.status(200); }});

// 真成功红线: success==true && !errorCode (estack 约定)
try {{
    var body = pm.response.json();
    var success = body.success !== undefined ? body.success : true;
    var hasError = body.errorCode !== undefined && body.errorCode !== null && body.errorCode !== "";
    pm.test("业务成功 (success==true && !errorCode)", function() {{
        pm.expect(success).to.be.true;
        pm.expect(hasError).to.be.false;
    }});
}} catch(e) {{}}

// 提取变量(供 CRUD 链传递)
try {{
    var body = pm.response.json();
    var id = body.data?.id || body.id || body.entity?.id;
    if (id) pm.collectionVariables.set("createdId", id);
}} catch(e) {{}}'''

            # Service 名称: 优先 x-kb-service → x-kb-resource → URL 第三段
            svc = ep.get("x-kb-service")
            if not svc:
                svc = ep.get("x-kb-resource")
            if not svc:
                segs = [s for s in p.split("/") if s]
                svc = segs[2] if len(segs) > 2 else "default"

            service_items.setdefault(svc, []).append({
                "name": f"{method.upper()} {p}",
                "request": {
                    "method": method.upper(),
                    "header": list(common_headers),
                    "body": body,
                    "url": _url_obj(p),
                    "description": (
                        f"CRUD: {crud}\n"
                        f"Service: {svc}\n"
                        f"Resource: {ep.get('x-kb-resource', '')}\n"
                        f"Body keys: {body_keys}"
                    ),
                },
                "event": [{
                    "listen": "test",
                    "script": {"exec": test_script.split("\n"), "type": "text/javascript"},
                }],
            })

    # 按 service 构建文件夹
    folders = [items[0]]  # 🔐 鉴权文件夹放第一个
    for svc in sorted(service_items):
        folders.append({
            "name": svc,
            "item": service_items[svc],
        })

    return {
        "info": {
            "name": catalog.get("info", {}).get("title", "API Export"),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": (
                f"Exported from API_AI_test on {datetime.now(timezone.utc).isoformat()}\n"
                f"Endpoints: {catalog.get('x-kb-endpoint-count', 0)}\n"
                f"Source: {catalog.get('x-kb-generated-at', '')}\n\n"
                "使用步骤:\n"
                "1. 在 Collection Variables 中填写 username / password\n"
                "2. 运行「🔐 鉴权 / Login」文件夹中的登录请求\n"
                "3. token 变量自动更新，后续请求自动携带 Bearer token\n"
                "4. 按需运行各 service 文件夹中的 API 请求"
            ),
        },
        "item": folders,
        "variable": [
            {"key": "token", "value": "",
             "description": "Bearer token（运行登录请求后自动设置）"},
            {"key": "username", "value": "",
             "description": "登录用户名（使用前请填写）"},
            {"key": "password", "value": "",
             "description": "登录密码（使用前请填写）"},
            {"key": "SID", "value": "bf5eadddf8eb496bb7788474f8322c6e"},
            {"key": "createdId", "value": "",
             "description": "CRUD 链传递: 创建后自动提取 id"},
        ],
    }


def export_all(proj_id: str, output_dir: str = None):
    """
    导出 OpenAPI 3.1 (YAML + JSON) + Postman Collection v2.1 + 使用说明。
    """
    proj_dir = ROOT / "projects" / proj_id
    out_dir = Path(output_dir) if output_dir else (proj_dir / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(str(proj_dir))
    if not catalog or not catalog.get("paths"):
        print(f"[export] ✗ {proj_id}: 无 catalog, 跳过")
        return

    profile = load_profile(str(proj_dir))

    # 1. OpenAPI 3.1 YAML
    oas = _build_openapi(catalog, profile)
    oas_yaml = out_dir / f"{proj_id}_openapi.yaml"
    oas_yaml.write_text(yaml.safe_dump(oas, allow_unicode=True, sort_keys=False,
                                        default_flow_style=False), encoding="utf-8")
    print(f"[export] ✅ OpenAPI 3.1 YAML: {oas_yaml}")

    # 2. OpenAPI 3.1 JSON
    oas_json = out_dir / f"{proj_id}_openapi.json"
    oas_json.write_text(json.dumps(oas, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export] ✅ OpenAPI 3.1 JSON: {oas_json}")

    # 3. Postman Collection v2.1
    pm = _build_postman(catalog, profile)
    pm_json = out_dir / f"{proj_id}_postman.json"
    pm_json.write_text(json.dumps(pm, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[export] ✅ Postman Collection: {pm_json}")

    # 4. 使用说明
    usage_guide = f"""# Postman 导入使用说明

## 导入步骤

1. 打开 Postman → File → Import
2. 选择 `{proj_id}_postman.json`
3. 点击 Import

## 配置鉴权

导入后在 Collection Variables 中填写：

| 变量 | 说明 |
|------|------|
| `username` | 登录用户名 |
| `password` | 登录密码 |
| `token` | 留空（运行登录请求后自动设置） |

## 使用流程

1. 先运行「🔐 鉴权 / Login」文件夹中的登录请求
2. token 变量自动更新（Test 脚本提取 accessToken）
3. 后续所有 API 请求自动携带 `Authorization: Bearer {{token}}`
4. 每个请求附带自动断言（Status 200 + 业务 success 检查 + 变量提取）

## 文件清单

| 文件 | 格式 | 说明 |
|------|------|------|
| `{proj_id}_postman.json` | Postman Collection v2.1 | 直接导入 Postman |
| `{proj_id}_openapi.yaml` | OpenAPI 3.1 YAML | 导入 Apifox / Swagger |
| `{proj_id}_openapi.json` | OpenAPI 3.1 JSON | 导入其他工具 |
"""
    guide_path = out_dir / "postman_import_guide.md"
    guide_path.write_text(usage_guide, encoding="utf-8")
    print(f"[export] ✅ 使用说明: {guide_path}")

    print(f"[export] ✅ {proj_id}: 导出 {4} 个文件到 {out_dir}")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="导出 OpenAPI + Postman")
    ap.add_argument("--project", required=True, help="项目 id (projects/<id>/)")
    ap.add_argument("--output", help="输出目录(默认 projects/<id>/exports/)")
    args = ap.parse_args()
    export_all(args.project, args.output)


if __name__ == "__main__":
    main()
