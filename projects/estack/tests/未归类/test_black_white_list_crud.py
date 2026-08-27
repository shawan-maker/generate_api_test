"""
test_black-white-list_crud.py — black-white-list 回归用例: 创建→查询(列表/详情)→删除→校验消失。
由 generators/gen_pytest.py 自动生成。函数通过 lib.api_client.get_fn 按
(project, service, resource, crud) 加载, 规避项目目录名含连字符的导入问题。
"""
import uuid
import pytest
from lib.api_client import get_client, get_fn

_PROJ = "estack"
_SVC = "draco"
_RES = "black-white-list"

create = get_fn(_PROJ, _SVC, _RES, "create") if True else None
list_ = get_fn(_PROJ, _SVC, _RES, "list") if False else None
detail = get_fn(_PROJ, _SVC, _RES, "detail") if True else None
delete = get_fn(_PROJ, _SVC, _RES, "delete") if False else None


@pytest.fixture(scope="module")
def client():
    """模块级复用 client。鉴权 token/cookie 由 auth 注入(运行前准备)。"""
    from pathlib import Path
    proj_dir = Path(__file__).resolve().parents[2]
    return get_client(proj_dir)


class TestBlackWhiteListCRUD:

    def test_BlackWhiteList_create(self, client):
        """create: POST /estack/api/estack/draco/v1/black-white-list"""
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

    def test_BlackWhiteList_list(self, client):
        """list: """
        if list_ is None:
            pytest.skip("无 list 端点")
        resp = list_(client)
        assert resp.success, f"列表查询失败: {resp.json}"

    def test_BlackWhiteList_detail(self, client):
        """detail: /estack/api/estack/draco/v1/black-white-list/system-config"""
        if detail is None:
            pytest.skip("无 detail 端点")
        assert hasattr(pytest, "created_id"), "缺少 created_id"
        resp = detail(client, )
        assert resp.success, f"详情查询失败: {resp.json}"
        j = resp.json
        if j:
            status_val = (j.get("data", {}) or {}).get("status") or (j.get("entity", {}) or {}).get("status") or j.get("status")
            if status_val:
                assert status_val in ("active", "available", "ACTIVE", "AVAILABLE", True), f"状态异常: {status_val}"

    def test_BlackWhiteList_delete(self, client):
        """delete: /estack/api/estack/draco/v1/black-white-list/system-config"""
        if delete is None:
            pytest.skip("无 delete 端点")
        assert hasattr(pytest, "created_id"), "缺少 created_id"
        resp = delete(client, )
        assert resp.success, f"删除失败: {resp.json}"
        if detail is not None:
            detail_resp = detail(client, )
            if detail_resp.status == 200:
                assert not detail_resp.success, f"删除后仍能查到: {detail_resp.json}"
