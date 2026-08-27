"""
gen_pytest.py — 从 api_catalog.json + CRUD 编排生成 pytest 回归用例。
对应方案设计 §2.2(API层/脚本层分离) + §4.2(四步法③④) + §7(CRUD编排) + §8(真成功+状态校验)。

产出: projects/<id>/tests/<service>/test_<resource>_crud.py
每个用例: 调 api 层薄函数, 做编排+断言+状态校验。
"""
import json
import re
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]

def _render_template(proj_id, service, resource, resource_title,
                      has_create, has_list, has_detail, has_delete,
                      path_create, path_list, path_detail,
                      detail_args, delete_args):
    """渲染 CRUD test 模板。函数通过 lib.api_client.get_fn 加载, 规避连字符目录导入问题。"""
    code = r'''"""
test_{RESOURCE}_crud.py — {RESOURCE} 回归用例: 创建→查询(列表/详情)→删除→校验消失。
由 generators/gen_pytest.py 自动生成。函数通过 lib.api_client.get_fn 按
(project, service, resource, crud) 加载, 规避项目目录名含连字符的导入问题。
"""
import uuid
import pytest
from lib.api_client import get_client, get_fn

_PROJ = "{PROJ_ID}"
_SVC = "{SERVICE}"
_RES = "{RESOURCE}"

create = get_fn(_PROJ, _SVC, _RES, "create") if {HAS_CREATE} else None
list_ = get_fn(_PROJ, _SVC, _RES, "list") if {HAS_LIST} else None
detail = get_fn(_PROJ, _SVC, _RES, "detail") if {HAS_DETAIL} else None
delete = get_fn(_PROJ, _SVC, _RES, "delete") if {HAS_DELETE} else None


@pytest.fixture(scope="module")
def client():
    """模块级复用 client。鉴权 token/cookie 由 auth 注入(运行前准备)。"""
    from pathlib import Path
    proj_dir = Path(__file__).resolve().parents[2]
    return get_client(proj_dir)


class Test{RESOURCE_TITLE}CRUD:

    def test_{RESOURCE_TITLE}_create(self, client):
        """create: POST {PATH_CREATE}"""
        if create is None:
            pytest.skip("无 create 端点")
        resp = create(client, name="AT_test_" + uuid.uuid4().hex[:8])
        assert resp.success, f"创建失败: {resp.json}"
        created_id = None
        j = resp.json
        if j:
            created_id = (j.get("data", {}) or {}).get("id") or j.get("id") or (j.get("entity", {}) or {}).get("id")
        assert created_id, f"未提取到创建 ID: {resp.json}"
        pytest.created_id = created_id
        print(f"  创建 ID: {created_id}")

    def test_{RESOURCE_TITLE}_list(self, client):
        """list: {PATH_LIST}"""
        if list_ is None:
            pytest.skip("无 list 端点")
        resp = list_(client)
        assert resp.success, f"列表查询失败: {resp.json}"

    def test_{RESOURCE_TITLE}_detail(self, client):
        """detail: {PATH_DETAIL}"""
        if detail is None:
            pytest.skip("无 detail 端点")
        assert hasattr(pytest, "created_id"), "缺少 created_id"
        resp = detail(client, {DETAIL_ARGS})
        assert resp.success, f"详情查询失败: {resp.json}"
        j = resp.json
        if j:
            status_val = (j.get("data", {}) or {}).get("status") or (j.get("entity", {}) or {}).get("status") or j.get("status")
            if status_val:
                assert status_val in ("active", "available", "ACTIVE", "AVAILABLE", True), f"状态异常: {status_val}"

    def test_{RESOURCE_TITLE}_delete(self, client):
        """delete: {PATH_DETAIL}"""
        if delete is None:
            pytest.skip("无 delete 端点")
        assert hasattr(pytest, "created_id"), "缺少 created_id"
        resp = delete(client, {DELETE_ARGS})
        assert resp.success, f"删除失败: {resp.json}"
        if detail is not None:
            detail_resp = detail(client, {DETAIL_ARGS})
            if detail_resp.status == 200:
                assert not detail_resp.success, f"删除后仍能查到: {detail_resp.json}"
'''
    return (code
        .replace("{PROJ_ID}", proj_id)
        .replace("{SERVICE}", service)
        .replace("{RESOURCE}", resource)
        .replace("{RESOURCE_TITLE}", resource_title.replace("-", "_").replace(".", "_"))
        .replace("{HAS_CREATE}", str(has_create))
        .replace("{HAS_LIST}", str(has_list))
        .replace("{HAS_DETAIL}", str(has_detail))
        .replace("{HAS_DELETE}", str(has_delete))
        .replace("{PATH_CREATE}", path_create)
        .replace("{PATH_LIST}", path_list or "")
        .replace("{PATH_DETAIL}", path_detail or "")
        .replace("{DETAIL_ARGS}", detail_args)
        .replace("{DELETE_ARGS}", delete_args))


def _find_endpoints(catalog: dict, crud: str, service: str = None) -> list:
    """从 catalog 中按 CRUD 类型查找端点。"""
    results = []
    for p, methods in catalog.get("paths", {}).items():
        for method, ep in methods.items():
            if ep.get("x-kb-crud") != crud:
                continue
            if service and ep.get("x-kb-service") != service:
                continue
            results.append({
                "path": p,
                "method": method.upper(),
                "service": ep.get("x-kb-service", ""),
                "resource": ep.get("x-kb-resource", ""),
                "summary": ep.get("summary", ""),
            })
    return results


def _has_path_param(path: str) -> bool:
    return bool(re.search(r"\{[^}]+\}", path))


def _first_path_param(path: str):
    """提取路径模板中第一个 {param} 的参数名, 没有则返回 None。"""
    m = re.search(r"\{([^}]+)\}", path or "")
    return m.group(1) if m else None


def _fn_name(crud: str, resource: str) -> str:
    crud_map = {"create": "create", "list": "list", "detail": "get",
                 "update": "update", "delete": "delete"}
    return f"{crud_map.get(crud, crud)}_{resource.replace('-', '_')}"


def generate_tests(proj_id: str, catalog: dict = None):
    """
    为 project 生成 pytest CRUD 测试用例。
    每对 (service, resource) 生成一个测试文件。
    """
    proj_dir = ROOT / "projects" / proj_id
    test_dir = proj_dir / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)

    if catalog is None:
        cat_path = proj_dir / "kb" / "api_catalog.json"
        if not cat_path.exists():
            print(f"[gen_tests] ✗ {proj_id}: 无 catalog")
            return
        catalog = json.loads(cat_path.read_text(encoding="utf-8"))

    # 按 service/resource 分组后找每组的 CRUD 端点
    groups = {}
    for p, methods in catalog.get("paths", {}).items():
        for method, ep in methods.items():
            svc = ep.get("x-kb-service", "default") or "default"
            res = ep.get("x-kb-resource", "resource") or "resource"
            crud = ep.get("x-kb-crud", "action")
            key = f"{svc}/{res}"
            groups.setdefault(key, {
                "service": svc, "resource": res,
                "create": None, "list": None, "detail": None, "delete": None,
                "paths": [],
            })
            ep_info = {"path": p, "method": method.upper(), "crud": crud}
            groups[key]["paths"].append(ep_info)
            if crud in ("create", "list", "detail", "delete"):
                groups[key][crud] = ep_info

    generated = []
    # 加载菜单目录映射
    from generators.menu_tools import get_resource_dir
    menu_dir = get_resource_dir

    for key, g in sorted(groups.items()):
        service = g["service"]
        resource = g["resource"]
        resource_title = resource.replace("-", "_").replace(".", "_").title().replace("_", "")

        # 判断是否有完整的 CRUD 链路
        if g["create"] and (g["detail"] or g["list"]):
            # 有 create + 至少一个查询, 可生成用例
            create_ep = g["create"]
            list_ep = g["list"]
            detail_ep = g["detail"]
            delete_ep = g["delete"]

            # 决定方法名(从 api 层)
            has_create = g["create"] is not None
            has_list = g["list"] is not None
            has_detail = g["detail"] is not None
            has_delete = g["delete"] is not None

            path_create = create_ep["path"]
            path_list = list_ep["path"] if list_ep else ""
            path_detail = detail_ep["path"] if detail_ep else ""
            detail_param = _first_path_param(detail_ep["path"]) if detail_ep else None
            delete_param = _first_path_param(delete_ep["path"]) if delete_ep else None
            detail_args = f"{detail_param}=pytest.created_id" if detail_param else ""
            delete_args = f"{delete_param}=pytest.created_id" if delete_param else ""

            code = _render_template(
                proj_id,
                service,
                resource,
                resource_title,
                has_create, has_list, has_detail, has_delete,
                path_create, path_list, path_detail,
                detail_args, delete_args,
            )

            # 放入两级目录
            rel_path = menu_dir(str(proj_dir), catalog, key)  # 例如 "访问控制/用户管理"
            test_subdir = test_dir / rel_path
            test_subdir.mkdir(parents=True, exist_ok=True)
            test_file = test_subdir / f"test_{resource.replace('-', '_')}_crud.py"
            test_file.write_text(code, encoding="utf-8")
            generated.append(test_file)
            print(f"[gen_tests]   {test_file.relative_to(ROOT)}  ({resource} CRUD)")
        else:
            # 无完整 CRUD 链, 跳过(或生成单步用例)
            pass

    # 生成 conftest.py (pytest 配置)
    conftest = test_dir / "conftest.py"
    conftest.write_text('''"""
pytest conftest — 自动配置 client。鉴权 token/cookie 由 auth 注入(运行前准备)。
"""
import pytest
from lib.api_client import get_client


@pytest.fixture(scope="session")
def client():
    """session 级 client。需先准备好鉴权上下文(见 lib/auth.py)。"""
    from pathlib import Path
    proj_dir = Path(__file__).resolve().parents[2]
    return get_client(proj_dir)
''', encoding="utf-8")
    generated.append(conftest)

    print(f"[gen_tests] ✅ {proj_id}: 生成 {len(generated)} 个文件")
    return generated


def main():
    import argparse
    ap = argparse.ArgumentParser(description="生成 pytest CRUD 回归用例")
    ap.add_argument("--project", required=True, help="项目 id")
    args = ap.parse_args()
    generate_tests(args.project)


if __name__ == "__main__":
    main()
