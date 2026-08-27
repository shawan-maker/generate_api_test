"""
gen_api_layer.py — 从 api_catalog.json (OpenAPI 3.1 IR) 生成 projects/<id>/api/ 薄函数。
对应方案设计 §2.2 API 层 / 脚本层分离 + §5 生成器。

产出: projects/<id>/api/_client.py + api/<service>/<resource>.py
每个函数: 只拼请求、发请求、返回响应对象, 不含断言、不含编排。
"""
import json
import re
import sys
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def load_catalog(proj_dir: str) -> dict:
    p = Path(proj_dir) / "kb" / "api_catalog.json"
    if not p.exists():
        print(f"[gen_api] ✗ 未找到 catalog: {p}")
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _safe_fn_name(method: str, crud: str, resource: str, service: str, path: str) -> str:
    """生成安全的 Python 函数名: create_user / list_role_policies"""
    parts = []
    if crud:
        crud_map = {"create": "create", "list": "list", "detail": "get",
                     "update": "update", "delete": "delete", "action": "exec"}
        parts.append(crud_map.get(crud, crud))
    # 注意: 必须优先用 resource; 仅当 resource 为空才回退到 path 末段。
    # 下面这种写法等价于 (resource or path[-1]) if path else "endpoint",
    # 在 path 为空时会错误回退到 "endpoint", 因此显式分支处理。
    if resource:
        base = resource
    elif path:
        base = path.strip("/").split("/")[-1]
    else:
        base = "endpoint"
    base = re.sub(r"\{[^}]+\}", "", base).strip("-").replace("-", "_").replace(".", "_")
    if not base:
        base = "resource"
    parts.append(base)
    if service:
        parts.insert(0, service)
    return "_".join(parts)


def _path_to_python_params(path_template: str) -> list:
    """从路径模板提取路径参数。{id} -> path_params: str。"""
    params = []
    for m in re.finditer(r"\{([^}]+)\}", path_template):
        pname = m.group(1).lstrip("{").rstrip("}")
        params.append(pname)
    return params


def _gen_client_json(proj_dir: str, base_url: str, api_base: str,
                      header_name: str, header_prefix: str, fixed_headers: dict) -> str:
    """生成 api/_client.json — 项目级 httpx 客户端常量(按路径读取, 不靠 import)。"""
    cfg = {
        "base_url": base_url,
        "api_base": api_base,
        "header_name": header_name,
        "header_prefix": header_prefix,
        "fixed_headers": fixed_headers or {},
    }
    return json.dumps(cfg, indent=2, ensure_ascii=False)


def _gen_resource_funcs(service: str, resource: str, paths: dict) -> str:
    """
    为指定 service/resource 生成 api/ 薄函数。
    函数签名: def create_user(c: httpx.Client, *, name: str, **extra) -> ApiResponse:
    注意: 所有额外参数用 **extra 收, 不限制 body 结构(保持通用)。
    """
    lines = []
    lines.append(f"# ---- {service}/{resource} ----")

    for path_tmpl, methods in paths.items():
        for method_lower, ep in methods.items():
            method = method_lower.upper()
            crud = ep.get("x-kb-crud", "action")
            fn_name = _safe_fn_name(method, crud, resource, service, path_tmpl)

            path_params = _path_to_python_params(path_tmpl)
            body_keys = ep.get("x-kb-body-keys", [])
            body_keys_sample = body_keys[:5]  # 最多 5 个

            # 函数签名
            sig_parts = ["c"]
            for pp in path_params:
                sig_parts.append(f"{pp}: str")
            if method in ("POST", "PUT", "PATCH"):
                sig_parts.append("**extra")

            doc_lines = []
            doc_lines.append(f'    """{crud}: {path_tmpl}')
            if body_keys_sample:
                doc_lines.append(f'    body_keys: {json.dumps(body_keys_sample)}')
            doc_lines.append('    """')

            # 函数体
            body_lines = []
            # URL 路径参数格式化
            if path_params:
                url_fmt = path_tmpl
                for pp in path_params:
                    url_fmt = url_fmt.replace("{" + pp + "}", "{" + f"{pp}" + "}")
                body_lines.append(f'    url = f"{url_fmt}"')
            else:
                body_lines.append(f'    url = "{path_tmpl}"')

            if method in ("POST", "PUT", "PATCH"):
                body_lines.append("    return ApiResponse(c.request(")
                body_lines.append(f'        "{method}", url, json=extra or None))')
            else:
                body_lines.append("    return ApiResponse(c.request(")
                body_lines.append(f'        "{method}", url))')

            # 组装
            full_sig = ', '.join(sig_parts)
            doc = '\n'.join(doc_lines)
            body = '\n'.join(body_lines)
            lines.append(f'\n\ndef {fn_name}({full_sig}):')
            lines.append(doc)
            lines.append(body)

    return '\n'.join(lines)


def _gen_init() -> str:
    """生成 api/__init__.py — 通过 lib.api_client 暴露取数入口, 不依赖包名导入。"""
    return '''"""
API 层包 — 薄调用函数集合(两级目录组织)。

重要: 取函数请用 lib.api_client.get_fn(project_id, service, resource, crud),
不要 `import projects.<id>.api...` —— 项目目录名可能含连字符(如 ecm-compute),
会导致包名导入失败。get_fn 走文件路径加载, 对含连字符的项目同样适用。
"""
from lib.api_client import get_fn, get_client, ApiResponse

__all__ = ["get_fn", "get_client", "ApiResponse"]
'''


def generate_api_layer(proj_id: str, catalog: dict = None, force: bool = False):
    """
    从 api_catalog.json -> projects/<id>/api/<一级目录>/<二级目录>/<resource>.py
    两级目录来自 menu_tree.json (一级=分组, 二级=功能项)。
    """
    proj_dir = ROOT / "projects" / proj_id
    api_dir = proj_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)

    if catalog is None:
        catalog = load_catalog(str(proj_dir))
    if not catalog or not catalog.get("paths"):
        print(f"[gen_api] ✗ {proj_id}: catalog 为空, 跳过")
        return

    # 加载菜单目录映射
    from generators.menu_tools import get_resource_dir
    menu_dir = get_resource_dir

    # 按 service 分组
    service_groups = {}
    for path_tmpl, methods in catalog["paths"].items():
        for method_lower, ep in methods.items():
            service = ep.get("x-kb-service") or "default"
            resource = ep.get("x-kb-resource") or "resource"
            key = f"{service}/{resource}"
            service_groups.setdefault(key, {"service": service, "resource": resource, "paths": {}})
            service_groups[key]["paths"].setdefault(path_tmpl, {})[method_lower] = ep

    # 生成每个 service/resource 的模块（放入两级目录）
    generated_files = []
    for key, group in sorted(service_groups.items()):
        service = group["service"]
        resource = group["resource"]
        module_name = resource.replace("-", "_").replace(".", "_") or "resource"

        # 确定两级目录路径
        rel_path = menu_dir(str(proj_dir), catalog, key)  # 例如 "访问控制/用户管理"
        mod_dir = api_dir / rel_path
        mod_dir.mkdir(parents=True, exist_ok=True)
        mod_file = mod_dir / f"{module_name}.py"
        if mod_file.exists() and not force:
            continue  # 已有文件不覆盖(保留人工修改)

        code = _gen_resource_funcs(service, resource, group["paths"])
        # 统一从框架层 lib.api_client 导入, 规避项目目录名含连字符导致的包名导入失败
        mod_file.write_text(f'''"""
API 层: {service}/{resource} — 薄调用函数, 无断言。
自动生成自 api_catalog.json, 请勿手动修改。
"""
import httpx
from lib.api_client import ApiResponse, get_client

{code}
''', encoding="utf-8")
        generated_files.append(mod_file)
        print(f"[gen_api]   {mod_file.relative_to(ROOT)}")

        # 两级目录: 在 二级(label) 与 一级(group) 目录各放一个 __init__.py (空包)
        label_dir = mod_dir
        group_dir = mod_dir.parent
        if group_dir != api_dir and not (group_dir / "__init__.py").exists():
            (group_dir / "__init__.py").write_text(
                '"""API 一级目录包(自动生成)。"""\n', encoding="utf-8")
        if not (label_dir / "__init__.py").exists():
            (label_dir / "__init__.py").write_text(
                '"""API 二级目录包(自动生成)。"""\n', encoding="utf-8")

    # 生成 _client.json (项目专属常量, 按路径读取)
    client_file = api_dir / "_client.json"
    if not client_file.exists() or force:
        # 从 profile.yaml 读 base_url/api_base 等
        profile = _load_profile(proj_dir)
        client_code = _gen_client_json(
            str(proj_dir),
            profile.get("base_url", ""),
            profile.get("api_base", ""),
            (profile.get("auth") or {}).get("header_name", "Authorization"),
            (profile.get("auth") or {}).get("header_prefix", "Bearer "),
            (profile.get("auth") or {}).get("fixed_headers", {}),
        )
        client_file.write_text(client_code, encoding="utf-8")
        generated_files.append(client_file)
        print(f"[gen_api]   {client_file.relative_to(ROOT)}")
        # 清理旧版 _client.py (若有)
        old_py = api_dir / "_client.py"
        if old_py.exists():
            old_py.unlink()
            print(f"[gen_api]   (已删除旧版) {old_py.relative_to(ROOT)}")

    # 生成 __init__.py (api 包入口, 暴露 get_fn)
    init_file = api_dir / "__init__.py"
    init_file.write_text(_gen_init(), encoding="utf-8")
    generated_files.append(init_file)

    print(f"[gen_api] ✅ {proj_id}: 生成 {len(generated_files)} 个文件 ({len(service_groups)} 模块)")
    return generated_files


def _load_profile(proj_dir: Path) -> dict:
    """读取项目 profile.yaml。"""
    import yaml
    p = proj_dir / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="从 api_catalog.json 生成 API 层")
    ap.add_argument("--project", required=True, help="项目 id (projects/<id>/)")
    ap.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = ap.parse_args()
    generate_api_layer(args.project, force=args.force)


if __name__ == "__main__":
    main()
