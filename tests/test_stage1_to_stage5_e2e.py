"""
Stage 1-5 端到端测试：验证 list/query 分类修复

测试三个核心 Bug：
1. Bug 1: endpoint_classifier.py 子串匹配改为段级匹配
2. Bug 2: lib/catalog.py REST 兜底逻辑收紧
3. Bug 3: analyze_flow.py 响应体重分类误判修复
"""

import pytest
from pathlib import Path
from module_discovery.endpoint_classifier import classify_endpoint
from lib.catalog import guess_crud
from module_discovery.analyze_flow import _filter_core_apis


class TestBug1SegmentLevelMatching:
    """Bug 1: 子串匹配误判修复"""

    def test_target_not_classified_as_detail(self):
        """/target 不应被 /get 子串误判为 detail"""
        # 路径含 "get" 子串（tar-get），但完整段是 "target"
        result = classify_endpoint("GET", "/api/v1/target", [])
        assert result != "detail", f"Expected not detail, got {result}"
        # 应判为 other_get 或 support

    def test_playlist_not_classified_as_query(self):
        """/playlist 不应被 /list 子串误判为 query"""
        result = classify_endpoint("GET", "/api/v1/playlist", [])
        assert result != "query", f"Expected not query, got {result}"

    def test_preview_not_classified_as_detail(self):
        """/preview 不应被 /view 子串误判为 detail"""
        result = classify_endpoint("GET", "/api/v1/preview", [])
        assert result != "detail", f"Expected not detail, got {result}"

    def test_callback_not_classified_as_query(self):
        """/callback 不应被 /all 子串误判为 query"""
        result = classify_endpoint("GET", "/api/v1/callback", [])
        assert result != "query", f"Expected not query, got {result}"

    def test_actual_list_endpoint_still_classified(self):
        """/users/list 应正确判为 query"""
        result = classify_endpoint("GET", "/api/v1/users/list", [])
        assert result == "query", f"Expected query, got {result}"

    def test_actual_detail_endpoint_still_classified(self):
        """/users/detail 应正确判为 detail"""
        result = classify_endpoint("GET", "/api/v1/users/detail", [])
        assert result == "detail", f"Expected detail, got {result}"

    def test_batch_delete_not_classified_as_update(self):
        """/batch-delete 应正确判为 delete（段级匹配允许连字符）"""
        result = classify_endpoint("DELETE", "/api/v1/users/batch-delete", [])
        assert result == "delete", f"Expected delete, got {result}"


class TestBug2RestFallbackTightening:
    """Bug 2: REST 兜底逻辑收紧"""

    def test_get_config_not_list(self):
        """GET /config 不应被兜底判为 list（单数名词）"""
        result = guess_crud("GET", "/api/v1/config")
        assert result != "list", f"Expected not list, got {result}"

    def test_get_current_user_not_list(self):
        """GET /current-user 不应被兜底判为 list"""
        result = guess_crud("GET", "/api/v1/current-user")
        assert result != "list", f"Expected not list, got {result}"

    def test_get_users_is_list(self):
        """GET /users 应判为 list（复数名词）"""
        result = guess_crud("GET", "/api/v1/users")
        assert result == "list", f"Expected list, got {result}"

    def test_get_roles_is_list(self):
        """GET /roles 应判为 list（复数名词）"""
        result = guess_crud("GET", "/api/v1/roles")
        assert result == "list", f"Expected list, got {result}"

    def test_get_policies_is_list(self):
        """GET /policies 应判为 list（复数名词）"""
        result = guess_crud("GET", "/api/v1/policies")
        assert result == "list", f"Expected list, got {result}"

    def test_get_user_by_id_is_detail(self):
        """GET /users/{id} 应判为 detail"""
        result = guess_crud("GET", "/api/v1/users/{id}")
        assert result == "detail", f"Expected detail, got {result}"


class TestBug3ResponseBodyReclassification:
    """Bug 3: 响应体重分类误判修复"""

    def test_data_array_not_classified_as_query(self):
        """响应含 "data": [1,2,3] 不应被判为 query（跳过 data 字段）"""
        classified = {
            "other_get": [
                {
                    "pathname": "/api/v1/config",
                    "method": "GET",
                    "contexts": [],
                    "bodies": [],
                }
            ]
        }
        response_samples = {
            "/api/v1/config": [
                {
                    "body": '{"entity": {"data": [1, 2, 3], "id": "config_001"}}'
                }
            ]
        }
        result = _filter_core_apis(classified, response_samples)
        # 应判为 execute（有 id 字段），而非 query
        assert "query" not in result or len(result.get("query", [])) == 0
        assert "execute" in result, f"Expected execute, got {result.keys()}"

    def test_list_with_dict_items_classified_as_query(self):
        """响应含 "list": [{"id":1}] 应被判为 query（典型列表结构）"""
        classified = {
            "other_get": [
                {
                    "pathname": "/api/v1/users",
                    "method": "GET",
                    "contexts": [],
                    "bodies": [],
                }
            ]
        }
        response_samples = {
            "/api/v1/users": [
                {
                    "body": '{"entity": {"list": [{"id": "user_001"}], "total": 1}}'
                }
            ]
        }
        result = _filter_core_apis(classified, response_samples)
        assert "query" in result, f"Expected query, got {result.keys()}"
        assert len(result["query"]) == 1

    def test_records_with_dict_items_classified_as_query(self):
        """响应含 "records": [{"id":1}] 应被判为 query"""
        classified = {
            "other_get": [
                {
                    "pathname": "/api/v1/roles",
                    "method": "GET",
                    "contexts": [],
                    "bodies": [],
                }
            ]
        }
        response_samples = {
            "/api/v1/roles": [
                {
                    "body": '{"entity": {"records": [{"id": "role_001"}], "total": 1}}'
                }
            ]
        }
        result = _filter_core_apis(classified, response_samples)
        assert "query" in result, f"Expected query, got {result.keys()}"

    def test_list_with_non_dict_items_not_classified_as_query(self):
        """响应含 "list": [1, 2, 3] 不应被判为 query（非典型列表结构）"""
        classified = {
            "other_get": [
                {
                    "pathname": "/api/v1/tags",
                    "method": "GET",
                    "contexts": [],
                    "bodies": [],
                }
            ]
        }
        response_samples = {
            "/api/v1/tags": [
                {
                    "body": '{"entity": {"list": [1, 2, 3], "id": "tags_001"}}'
                }
            ]
        }
        result = _filter_core_apis(classified, response_samples)
        # 应判为 execute（有 id 字段），而非 query
        assert "query" not in result or len(result.get("query", [])) == 0


class TestEndToEndClassification:
    """端到端分类测试：验证完整流程"""

    def test_user_management_apis_classification(self):
        """用户管理模块的典型 API 分类"""
        # 创建用户
        assert classify_endpoint("POST", "/api/v1/users", ["click:创建"]) == "create"

        # 列表查询
        assert classify_endpoint("GET", "/api/v1/users/list", []) == "query"

        # 详情查询
        assert classify_endpoint("GET", "/api/v1/users/{id}", []) == "detail"

        # 更新用户
        assert classify_endpoint("PUT", "/api/v1/users/{id}", ["click:编辑"]) == "update"

        # 删除用户
        assert classify_endpoint("DELETE", "/api/v1/users/{id}", []) == "delete"

        # 批量删除
        assert classify_endpoint("DELETE", "/api/v1/users/batch-delete", []) == "delete"

    def test_support_apis_not_misclassified(self):
        """支撑 API 不应被误判为 CRUD"""
        # 当前用户信息
        result = classify_endpoint("GET", "/api/v1/current-user", [])
        assert result in ("other_get", "support", "detail"), f"Got {result}"

        # 配置信息
        result = classify_endpoint("GET", "/api/v1/config", [])
        assert result in ("other_get", "support", "detail"), f"Got {result}"

        # 字典查询
        result = classify_endpoint("GET", "/api/v1/dictionary", [])
        assert result in ("other_get", "support", "detail"), f"Got {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
