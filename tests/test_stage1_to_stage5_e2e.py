"""
Stage 1-5 端到端测试：验证行为驱动分类与响应体重分类

测试核心功能：
1. 行为驱动分类（HTTP method + 请求特征，不依赖 URL 关键词）
2. lib/catalog.py REST 兜底逻辑
3. analyze_flow.py 响应体重分类
"""

import pytest
from pathlib import Path
from module_discovery.endpoint_classifier import _classify_by_behavior
from lib.catalog import guess_crud
from module_discovery.analyze_flow import _filter_core_apis


class TestBehaviorDrivenClassification:
    """验证行为驱动分类：基于 HTTP method，不依赖 URL 关键词"""

    def test_get_classified_as_query(self):
        """GET 方法 → query"""
        result = _classify_by_behavior({"method": "GET", "pathname": "/api/v1/users/list"})
        assert result == "query", f"Expected query, got {result}"

    def test_post_with_form_classified_as_create(self):
        """POST + form data → create"""
        result = _classify_by_behavior({"method": "POST", "pathname": "/api/v1/users"}, has_form_data=True)
        assert result == "create", f"Expected create, got {result}"

    def test_post_without_form_classified_as_state_change(self):
        """POST + no form → state_change"""
        result = _classify_by_behavior({"method": "POST", "pathname": "/api/v1/users/lock"})
        assert result == "state_change", f"Expected state_change, got {result}"

    def test_put_classified_as_update(self):
        """PUT → update"""
        result = _classify_by_behavior({"method": "PUT", "pathname": "/api/v1/users/123"})
        assert result == "update", f"Expected update, got {result}"

    def test_delete_classified_as_delete(self):
        """DELETE → delete"""
        result = _classify_by_behavior({"method": "DELETE", "pathname": "/api/v1/users/123"})
        assert result == "delete", f"Expected delete, got {result}"

    def test_get_with_download_classified_as_export(self):
        """GET + download → export"""
        result = _classify_by_behavior({"method": "GET", "pathname": "/api/v1/users/export"}, triggers_download=True)
        assert result == "export", f"Expected export, got {result}"


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
            "query": [
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
            "query": [
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
    """端到端分类测试：验证行为驱动分类在完整流程中的表现"""

    def test_user_management_apis_classification(self):
        """用户管理模块的典型 API 分类（行为驱动）"""
        # 创建用户
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/v1/users"}, has_form_data=True) == "create"

        # 列表查询
        assert _classify_by_behavior({"method": "GET", "pathname": "/api/v1/users/list"}) == "query"

        # 更新用户
        assert _classify_by_behavior({"method": "PUT", "pathname": "/api/v1/users/123"}) == "update"

        # 删除用户
        assert _classify_by_behavior({"method": "DELETE", "pathname": "/api/v1/users/123"}) == "delete"

        # 批量删除
        assert _classify_by_behavior({"method": "DELETE", "pathname": "/api/v1/users/batch-delete"}) == "delete"

        # 状态变更（锁定）
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/v1/users/lock"}) == "state_change"

    def test_support_apis_not_misclassified(self):
        """支撑 API 不应被误判为 CRUD（行为驱动：GET = query）"""
        # 当前用户信息
        result = _classify_by_behavior({"method": "GET", "pathname": "/api/v1/current-user"})
        assert result == "query", f"Got {result}"

        # 配置信息
        result = _classify_by_behavior({"method": "GET", "pathname": "/api/v1/config"})
        assert result == "query", f"Got {result}"

        # 字典查询
        result = _classify_by_behavior({"method": "GET", "pathname": "/api/v1/dictionary"})
        assert result == "query", f"Got {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
