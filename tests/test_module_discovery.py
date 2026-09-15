"""
test_module_discovery.py — module_discovery 模块单元测试

测试目标：
- EndpointClassifier 类封装
- stage_validators 阶段门控验证
- analyze_flow 同现频率过滤
- endpoint_classifier 去重与分类
"""
import pytest
from unittest.mock import MagicMock

from module_discovery.endpoint_classifier import (
    EndpointClassifier, deduplicate_calls, _classify_by_behavior,
    _response_has_entity_id, _call_has_distinctive_params,
    _request_body_field_count, _count_pathname_windows,
)
from module_discovery.stage_validators import (
    validate_stage1, validate_stage2, validate_stage3, validate_stage4
)
# 导入 analyze_flow 中仍存在的函数
from module_discovery.analyze_flow import _identify_infrastructure_apis


# ============================================================
# EndpointClassifier 类封装测试
# ============================================================

class TestEndpointClassifierClass:
    """测试 EndpointClassifier 类封装"""

    def test_init_default(self):
        """默认初始化"""
        classifier = EndpointClassifier()
        assert classifier.kb_config == {}

    def test_init_with_config(self):
        """带配置初始化"""
        config = {"systems": {"estack": {"api_root": "/estack/api"}}}
        classifier = EndpointClassifier(config)
        assert classifier.kb_config == config

    def test_deduplicate_basic(self):
        """基本去重功能：通过 replay_windows 确定 core_api_map"""
        import time
        t = time.time()
        calls = [
            {"method": "POST", "pathname": "/api/users/create", "context": "replay:创建", "ts": t + 1},
            {"method": "POST", "pathname": "/api/users/create", "context": "replay:创建", "ts": t + 1.1},
            {"method": "GET", "pathname": "/api/users/list", "context": "replay:查询", "ts": t + 5},
        ]
        samples = {
            "/api/users/create": [{"status": 200, "body": '{"entity":{"id":"123"}}'}],
            "/api/users/list": [{"status": 200, "body": '{"entity":{"list":[],"total":0}}'}],
        }
        replay_windows = {
            "创建": {"start": t, "end": t + 3},
            "查询": {"start": t + 4, "end": t + 6},
        }
        classifier = EndpointClassifier()
        result = classifier.deduplicate(calls, samples, replay_windows=replay_windows)

        assert result["stats"]["total_calls"] == 3
        assert result["stats"]["unique_endpoints"] == 2
        # ★ 新设计：classified 使用操作名作为 key
        assert "创建" in result["classified"] or "查询" in result["classified"]

    def test_deduplicate_empty(self):
        """空输入去重"""
        result = EndpointClassifier().deduplicate([], {})
        assert result["stats"]["total_calls"] == 0
        assert result["stats"]["unique_endpoints"] == 0
        assert result["classified"] == {}


# ============================================================
# _classify_by_behavior 函数测试 (行为驱动分类)
# ============================================================

class TestClassifyByBehavior:
    """测试基于 HTTP 方法的行为分类（不依赖 URL 关键词或按钮文本）"""

    def test_delete_method(self):
        """DELETE 方法 → delete"""
        assert _classify_by_behavior({"method": "DELETE", "pathname": "/api/users/123"}) == "delete"
        assert _classify_by_behavior({"method": "DELETE", "pathname": "/api/users/unlock"}) == "delete"

    def test_post_with_form_data(self):
        """POST + 有表单数据 → create"""
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/users"}, has_form_data=True) == "create"

    def test_post_without_form_data(self):
        """POST + 无表单数据 → state_change (冻结/启用/审批等)"""
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/users/lock"}) == "state_change"
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/users/xxx"}) == "state_change"

    def test_put_method(self):
        """PUT 方法 → update"""
        assert _classify_by_behavior({"method": "PUT", "pathname": "/api/users/123"}) == "update"

    def test_patch_method(self):
        """PATCH 方法 → update"""
        assert _classify_by_behavior({"method": "PATCH", "pathname": "/api/users/123"}) == "update"

    def test_get_method(self):
        """GET 方法 → query"""
        assert _classify_by_behavior({"method": "GET", "pathname": "/api/users/list"}) == "query"
        assert _classify_by_behavior({"method": "GET", "pathname": "/api/unknown"}) == "query"

    def test_get_with_download(self):
        """GET + 触发下载 → export"""
        assert _classify_by_behavior({"method": "GET", "pathname": "/api/users/export"}, triggers_download=True) == "export"

    def test_post_with_download(self):
        """POST + 触发下载 → export"""
        assert _classify_by_behavior({"method": "POST", "pathname": "/api/users/export"}, triggers_download=True) == "export"

    def test_unknown_method(self):
        """未知方法 → other"""
        assert _classify_by_behavior({"method": "OPTIONS", "pathname": "/api/users"}) == "other"


# ============================================================
# Stage 1 门控验证测试
# ============================================================

class TestValidateStage1:
    """测试 Stage 1 质量门控"""

    def test_valid_result(self):
        """有效的 Stage 1 结果"""
        result = {
            "toolbar_buttons": [{"text": "新增", "action": "create"}, {"text": "确定", "action": "confirm"}],
            "row_actions": [{"text": "编辑", "action": "update"}, {"text": "删除", "action": "delete"}],
            "dialog_buttons": [{"text": "确定", "action": "confirm"}],
            "form_fields": [{"name": "name", "required": True}],
            "summary": {
                "total": 10,
                "has_create": True,
                "has_delete": True,
                "categories": {"create": 1, "delete": 1, "confirm": 2, "update": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"name": "test"}, "selectors": {"trigger": "新增"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}},
                "update": {"success": True, "fill_data": {"name": "test"}, "selectors": {"trigger": "编辑"}}
            }
        }
        is_valid, issues, missing = validate_stage1(result)
        assert is_valid is True
        assert len(issues) == 0

    def test_empty_buttons(self):
        """按钮列表为空"""
        result = {
            "toolbar_buttons": [],
            "row_actions": [],
            "summary": {"total": 0}
        }
        is_valid, issues, missing = validate_stage1(result)
        assert is_valid is False
        assert any("工具栏按钮" in i or "行操作按钮" in i for i in issues)

    def test_too_few_buttons(self):
        """按钮数量过少"""
        result = {
            "toolbar_buttons": [{"text": "新增"}],
            "row_actions": [],
            "summary": {"total": 2}
        }
        is_valid, issues, missing = validate_stage1(result)
        assert is_valid is False
        assert any("按钮总数过少" in i for i in issues)

    def test_missing_create(self):
        """缺少创建功能"""
        result = {
            "toolbar_buttons": [{"text": "查询"}],
            "row_actions": [{"text": "删除"}],
            "summary": {"total": 5, "has_create": False}
        }
        is_valid, issues, missing = validate_stage1(result)
        assert is_valid is False
        assert any("创建" in i for i in issues)

    def test_not_dict(self):
        """输入不是字典"""
        is_valid, issues, missing = validate_stage1("not a dict")
        assert is_valid is False
        assert "不是字典" in issues[0]

    def test_too_many_buttons(self):
        """按钮数量过多"""
        result = {
            "toolbar_buttons": [{"text": f"btn{i}"} for i in range(50)],
            "row_actions": [{"text": f"row{i}"} for i in range(60)],
            "summary": {"total": 110}
        }
        is_valid, issues, missing = validate_stage1(result)
        assert is_valid is False
        assert any("按钮总数过多" in i for i in issues)


# ============================================================
# Stage 2 门控验证测试
# ============================================================

class TestValidateStage2:
    """测试 Stage 2 质量门控"""

    def test_valid_result(self):
        """有效的 Stage 2 结果"""
        result = {
            "core_api_map": {
                "创建": {"method": "POST", "pathname": "/users"},
                "查询": {"method": "GET", "pathname": "/users"},
                "编辑": {"method": "PUT", "pathname": "/users/123"},
                "删除": {"method": "DELETE", "pathname": "/users/123"},
            },
            "stats": {"total_calls": 30, "unique_endpoints": 10},
            "response_samples": {"/users": [{"status": 200}]},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is True
        assert len(issues) == 0

    def test_missing_core_apis(self):
        """缺少核心 CRUD API"""
        result = {
            "core_api_map": {
                "查询": {"method": "POST", "pathname": "/list"},
            },
            "stats": {"total_calls": 5, "unique_endpoints": 3},
            "response_samples": {},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is False
        assert any("端点过少" in i or "core_api_map" in i for i in issues)

    def test_too_few_calls(self):
        """API 调用过少"""
        result = {
            "by_category": {
                "create": [{"method": "POST", "pathname": "/create"}],
                "query": [{"method": "POST", "pathname": "/list"}],
                "update": [{"method": "POST", "pathname": "/update"}],
                "delete": [{"method": "POST", "pathname": "/delete"}],
            },
            "stats": {"total_calls": 2, "unique_endpoints": 1},
            "response_samples": {},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is False
        assert any("调用过少" in i for i in issues)

    def test_not_dict(self):
        """输入不是字典"""
        is_valid, issues = validate_stage2([])
        assert is_valid is False


# ============================================================
# Stage 3 门控验证测试
# ============================================================

class TestValidateStage3:
    """测试 Stage 3 质量门控"""

    def test_valid_result(self):
        """有效的 Stage 3 结果"""
        result = {
            "order": [
                {"name": "create"},
                {"name": "query"},
                {"name": "update"},
                {"name": "delete"},
            ],
            "dependencies": {"id": "from_create"},
            "state_assertions": {"state_field": "state"},
            "api_mapping": {"create": "/api/create"},
        }
        is_valid, issues = validate_stage3(result)
        assert is_valid is True
        assert len(issues) == 0

    def test_empty_order(self):
        """执行顺序为空"""
        result = {
            "order": [],
            "dependencies": {},
            "state_assertions": {},
            "api_mapping": {},
        }
        is_valid, issues = validate_stage3(result)
        assert is_valid is False
        assert any("执行顺序为空" in i for i in issues)

    def test_short_order(self):
        """执行顺序过短"""
        result = {
            "order": [{"name": "query"}],
            "dependencies": {},
            "state_assertions": {},
            "api_mapping": {},
        }
        is_valid, issues = validate_stage3(result)
        assert is_valid is False
        assert any("过短" in i for i in issues)

    def test_missing_create_step(self):
        """缺少创建步骤"""
        result = {
            "order": [
                {"name": "query"},
                {"name": "update"},
                {"name": "delete"},
            ],
            "dependencies": {},
            "state_assertions": {},
            "api_mapping": {},
        }
        is_valid, issues = validate_stage3(result)
        assert is_valid is False
        assert any("创建" in i for i in issues)

    def test_empty_dependencies(self):
        """依赖关系为空"""
        result = {
            "order": [
                {"name": "create"},
                {"name": "query"},
                {"name": "delete"},
            ],
            "dependencies": {},
            "state_assertions": {"state_field": "state"},
            "api_mapping": {"create": "/api/create"},
        }
        is_valid, issues = validate_stage3(result)
        assert is_valid is False
        assert any("依赖" in i for i in issues)


# ============================================================
# Stage 4 门控验证测试
# ============================================================

class TestValidateStage4:
    """测试 Stage 4 质量门控"""

    def test_valid_script(self):
        """有效的生成脚本"""
        script = """
MANIFEST = {
    "module": "用户管理",
    "target_url": "/user-manage",
    "response_contract": {
        "create": {"path": "data.id", "assert_not_none": True},
        "list": {"path": "data.records", "assert_is_list": True}
    },
    "body_field_roles": {
        "username": "identity",
        "password": "credential",
        "role": "enum"
    },
    "steps": [
        {"crud": "create", "button": "新增"},
        {"crud": "list", "button": "查询"},
        {"crud": "update", "button": "编辑"},
        {"crud": "delete", "button": "删除"}
    ]
}

from lib.test_runtime import TestRunner

class TestUserLifecycle(TestRunner):
    def test_create_user(self):
        resp = self.run_step("create")
        assert resp.status == 200
        assert resp.success

    def test_list_user(self):
        resp = self.run_step("list")
        assert resp.status == 200

    def test_update_user(self):
        resp = self.run_step("update")
        assert resp.status == 200

    def test_delete_user(self):
        resp = self.run_step("delete")
        assert resp.status == 200

def browser_create_user():
    pass

assert resp.status == 200
assert resp.success
assert entity['state'] == 'ENABLE'
assert entity['id']
assert 'name' in entity

except AssertionError as e:
    print(e)
except Exception as e:
    print(e)
""" + "\n" * 60  # 确保行数在 50-500 之间

        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is True
        assert len(issues) == 0

    def test_empty_script(self):
        """空脚本"""
        is_valid, issues = validate_stage4("", "test.py")
        assert is_valid is False

    def test_missing_test_function(self):
        """旧架构缺少 test_ 函数应报错"""
        # 旧架构脚本（无 MANIFEST），缺少 test_ 函数
        script = """
def main():
    pass

def browser_create_user():
    pass
""" + "\n" * 60
        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is False
        assert any("test_" in i for i in issues)

    def test_manifest_pure_mode_valid(self):
        """manifest 纯模式（无 test_ 函数）应通过验证"""
        script = """
MANIFEST = {
    "module": "用户管理",
    "response_contract": {"envelope_keys": ["entity"]},
    "body_field_roles": {"userId": "id_ref"},
    "steps": [
        {"action": "create", "api": {"method": "POST", "pathname": "/users"}},
        {"action": "query", "api": {"method": "GET", "pathname": "/users"}},
        {"action": "update", "api": {"method": "PUT", "pathname": "/users/1"}},
        {"action": "delete", "api": {"method": "DELETE", "pathname": "/users/1"}}
    ]
}
from lib.test_runtime import TestRunner

runner = TestRunner(MANIFEST)
runner.run()
""" + "\n" * 60
        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is True
        assert len(issues) == 0

    def test_manifest_pure_mode_missing_steps(self):
        """manifest 纯模式缺少步骤应报错"""
        script = """
MANIFEST = {
    "module": "用户管理",
    "steps": []
}
from lib.test_runtime import TestRunner

def main():
    pass
""" + "\n" * 60
        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is False
        assert any("步骤过少" in i for i in issues)

    def test_few_assertions(self):
        """断言过少"""
        script = """
MANIFEST = {
    "module": "用户管理",
    "steps": [{"crud": "create"}]
}
from lib.test_runtime import TestRunner

class TestUser(TestRunner):
    def test_create(self):
        resp = self.run_step("create")

def browser_create_user():
    pass
""" + "\n" * 60
        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is False
        assert any("断言" in i for i in issues)

    def test_short_script(self):
        """脚本过短"""
        script = """
def test_lifecycle():
    pass
def browser_create_user():
    pass
assert True
assert True
assert True
assert True
assert True
except AssertionError: pass
except Exception: pass
# 创建 查询 修改 删除 锁定 解锁
"""
        is_valid, issues = validate_stage4(script, "test.py")
        assert is_valid is False
        assert any("过短" in i for i in issues)


# ============================================================
# 基础设施 API 识别测试
# ============================================================

class TestInfrastructureAPIs:
    """测试基础设施 API 识别"""

    def test_infra_only_contexts(self):
        """只在 init 上下文中出现的 API 被标记为基础设施"""
        endpoints = [
            {"pathname": "/api/users/create", "contexts": ["replay:create"]},
            {"pathname": "/api/current-user", "contexts": ["init"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/current-user" in result
        assert "/api/users/create" not in result

    def test_high_frequency_infra(self):
        """高频出现的 API 被标记为基础设施"""
        endpoints = [
            {"pathname": "/api/users/create", "contexts": ["replay:create"]},
            {"pathname": "/api/menu/tree", "contexts": [
                "replay:create", "replay:query", "replay:edit", "replay:delete"
            ]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/menu/tree" in result
        assert "/api/users/create" not in result

    def test_empty_endpoints(self):
        """空端点列表"""
        result = _identify_infrastructure_apis([], threshold=0.6)
        assert result == set()


# ============================================================
# _response_has_entity_id 响应特征检测测试
# ============================================================

class TestResponseHasEntityId:
    """测试响应是否包含实体 ID（Layer 2 过滤核心）"""

    def test_entity_with_id(self):
        """标准响应：entity 包含 id 字段"""
        samples = {
            "/api/users": [{"status": 200, "body": '{"entity":{"id":"abc123","name":"test"}}'}],
        }
        assert _response_has_entity_id("/api/users", samples) is True

    def test_entity_without_id(self):
        """校验类响应：entity 无 id 字段"""
        samples = {
            "/api/users/check": [{"status": 200, "body": '{"entity":{"available":true}}'}],
        }
        assert _response_has_entity_id("/api/users/check", samples) is False

    def test_entity_list_with_id(self):
        """列表响应：entity.list[0].id（核心修复场景）"""
        samples = {
            "/api/tenants/users": [{
                "status": 200,
                "body": '{"entity":{"list":[{"id":"u1","userName":"test"}],"total":10,"pageNum":1}}',
            }],
        }
        assert _response_has_entity_id("/api/tenants/users", samples) is True

    def test_entity_rows_with_id(self):
        """列表响应：entity.rows[0].id（变体）"""
        samples = {
            "/api/users": [{
                "status": 200,
                "body": '{"entity":{"rows":[{"id":"u1","name":"test"}],"total":5}}',
            }],
        }
        assert _response_has_entity_id("/api/users", samples) is True

    def test_entity_list_empty(self):
        """列表响应：entity.list 为空数组（无数据时）"""
        samples = {
            "/api/users": [{
                "status": 200,
                "body": '{"entity":{"list":[],"total":0}}',
            }],
        }
        assert _response_has_entity_id("/api/users", samples) is False

    def test_no_entity_key(self):
        """响应无信封键，直接检查 body"""
        samples = {
            "/api/users": [{"status": 200, "body": '{"id":"abc123","name":"test"}'}],
        }
        assert _response_has_entity_id("/api/users", samples) is True

    def test_success_only(self):
        """仅含 success 标志的校验响应"""
        samples = {
            "/api/users/check": [{"status": 200, "body": '{"success":true}'}],
        }
        assert _response_has_entity_id("/api/users/check", samples) is False

    def test_empty_samples(self):
        """空响应样本"""
        assert _response_has_entity_id("/api/users", {}) is False
        assert _response_has_entity_id("/api/users", {"/api/users": []}) is False

    def test_invalid_json(self):
        """无效 JSON 响应"""
        samples = {
            "/api/users": [{"status": 200, "body": "not json"}],
        }
        assert _response_has_entity_id("/api/users", samples) is False


# ============================================================
# _call_has_distinctive_params 独有参数检测测试
# ============================================================

class TestCallHasDistinctiveParams:
    """测试调用是否携带同路径其他调用没有的独有参数"""

    def test_with_search_param(self):
        """携带搜索参数（核心修复场景）"""
        call = {
            "method": "GET", "pathname": "/api/tenants/users",
            "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10", "name": "AT_test"},
        }
        all_calls = [
            call,
            # 同路径的其他调用（页面自动刷新，无搜索参数）
            {"method": "GET", "pathname": "/api/tenants/users",
             "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10"}},
            {"method": "GET", "pathname": "/api/tenants/users",
             "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10"}},
        ]
        assert _call_has_distinctive_params(call, all_calls) is True

    def test_without_distinctive_params(self):
        """无独有参数（页面自动刷新调用）"""
        call = {
            "method": "GET", "pathname": "/api/tenants/users",
            "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10"},
        }
        all_calls = [
            call,
            {"method": "GET", "pathname": "/api/tenants/users",
             "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10"}},
        ]
        assert _call_has_distinctive_params(call, all_calls) is False

    def test_only_pagination_params(self):
        """独有参数仅是分页参数（不算独有）"""
        call = {
            "method": "GET", "pathname": "/api/users",
            "query_params": {"pageNum": "2", "pageSize": "20"},
        }
        all_calls = [
            call,
            {"method": "GET", "pathname": "/api/users",
             "query_params": {"pageNum": "1", "pageSize": "10"}},
        ]
        assert _call_has_distinctive_params(call, all_calls) is False

    def test_no_query_params(self):
        """无查询参数的调用"""
        call = {"method": "GET", "pathname": "/api/users", "query_params": {}}
        all_calls = [call]
        assert _call_has_distinctive_params(call, all_calls) is False

    def test_none_query_params(self):
        """query_params 为 None"""
        call = {"method": "GET", "pathname": "/api/users"}
        all_calls = [call]
        assert _call_has_distinctive_params(call, all_calls) is False

    def test_different_pathnames(self):
        """不同路径的调用不影响判断"""
        call = {
            "method": "GET", "pathname": "/api/users",
            "query_params": {"name": "test"},
        }
        all_calls = [
            call,
            # 不同路径，参数不参与比较
            {"method": "GET", "pathname": "/api/roles",
             "query_params": {"name": "test"}},
        ]
        assert _call_has_distinctive_params(call, all_calls) is True


# ============================================================
# _request_body_field_count 请求体字段数测试
# ============================================================

class TestRequestBodyFieldCount:
    """测试请求体字段数计算（tiebreaker 用）"""

    def test_post_with_body(self):
        """POST 请求有请求体"""
        call = {"method": "POST", "body": '{"userName":"test","password":"123","email":"t@t.com"}'}
        assert _request_body_field_count(call) == 3

    def test_get_no_body(self):
        """GET 请求无请求体"""
        call = {"method": "GET", "pathname": "/api/users"}
        assert _request_body_field_count(call) == 0

    def test_empty_body(self):
        """空请求体"""
        call = {"method": "POST", "body": ""}
        assert _request_body_field_count(call) == 0

    def test_invalid_json(self):
        """无效 JSON 请求体"""
        call = {"method": "POST", "body": "not json"}
        assert _request_body_field_count(call) == 0

    def test_array_body(self):
        """数组请求体（如批量删除）"""
        call = {"method": "DELETE", "body": '["id1","id2","id3"]'}
        assert _request_body_field_count(call) == 0  # 不是 dict


# ============================================================
# 三层过滤完整场景测试
# ============================================================

class TestThreeLayerFiltering:
    """测试三层过滤 + tiebreaker 的完整场景"""

    def _make_calls(self, specs):
        """辅助：从 (method, pathname, ts, query_params) 构建 calls"""
        return [
            {"method": m, "pathname": p, "ts": ts, "query_params": qp or {}, "context": f"replay:{ctx}"}
            for m, p, ts, qp, ctx in specs
        ]

    def test_create_window_post_wins_over_gets(self):
        """create 窗口：POST /users（核心）胜过 GET 辅助 API"""
        all_calls = [
            {"method": "GET", "pathname": "/current-user", "ts": 1.0,
             "query_params": {}, "context": "replay:create"},
            {"method": "GET", "pathname": "/tenants/display-by-role", "ts": 1.1,
             "query_params": {"tenantId": "t1"}, "context": "replay:create"},
            {"method": "GET", "pathname": "/password-policy", "ts": 1.2,
             "query_params": {}, "context": "replay:create"},
            {"method": "POST", "pathname": "/users", "ts": 1.3,
             "query_params": {}, "context": "replay:create",
             "body": '{"userName":"test","password":"123","email":"t@t.com","name":"test"}'},
        ]
        samples = {
            "/current-user": [{"status": 200, "body": '{"entity":{"id":"u1"}}'}],
            "/tenants/display-by-role": [{"status": 200, "body": '{"entity":{"id":"t1"}}'}],
            "/password-policy": [{"status": 200, "body": '{"entity":{"minLength":8}}'}],
            "/users": [{"status": 200, "body": '{"entity":{"id":"new-id"}}'}],
        }
        windows = {"create": {"start": 0.5, "end": 2.0}}
        result = deduplicate_calls(all_calls, samples, replay_windows=windows)
        # POST /users 应通过 tiebreaker 胜出（请求体字段数最多）
        create_eps = result["classified"].get("create", [])
        assert len(create_eps) > 0
        assert create_eps[0]["pathname"] == "/users"

    def test_query_window_search_api_preserved(self):
        """query 窗口：搜索 API 不被 Layer 1 频率排除（核心修复场景）"""
        # 模拟 10 个操作窗口，/tenants/users 在 8 个窗口出现
        windows = {}
        all_calls = []
        actions = ["create", "query", "update", "delete", "lock", "unlock",
                    "reset", "migrate", "authorize", "import"]
        for i, action in enumerate(actions):
            start = float(i * 10)
            end = start + 5.0
            windows[action] = {"start": start, "end": end}
            # /tenants/users 在每个窗口都出现（页面自动刷新）
            all_calls.append({
                "method": "GET", "pathname": "/api/tenants/users",
                "ts": start + 0.5,
                "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10"},
                "context": f"replay:{action}",
            })

        # query 窗口额外触发搜索 API（带搜索参数）
        query_start = windows["query"]["start"]
        all_calls.append({
            "method": "GET", "pathname": "/api/tenants/users",
            "ts": query_start + 1.0,
            "query_params": {"tenantId": "t1", "pageNum": "1", "pageSize": "10", "name": "AT_test"},
            "context": "replay:query",
        })

        samples = {
            "/api/tenants/users": [{
                "status": 200,
                "body": '{"entity":{"list":[{"id":"u1"}],"total":10}}',
            }],
        }
        result = deduplicate_calls(all_calls, samples, replay_windows=windows)
        # query 类别应非空（搜索 API 被正确识别）
        query_eps = result["classified"].get("query", [])
        assert len(query_eps) > 0
        assert query_eps[0]["pathname"] == "/api/tenants/users"

    def test_fallback_when_all_filtered(self):
        """兜底：所有 API 都被过滤时取第一个非静态"""
        all_calls = self._make_calls([
            ("GET", "/current-user", 1.0, {}, "create"),
            ("POST", "/users/check", 1.1, {}, "create"),
        ])
        samples = {
            "/current-user": [{"status": 200, "body": '{"entity":{"id":"u1"}}'}],
            "/users/check": [{"status": 200, "body": '{"success":true}'}],
        }
        windows = {"create": {"start": 0.5, "end": 2.0}}
        result = deduplicate_calls(all_calls, samples, replay_windows=windows)
        # /current-user 是静态排除，/users/check 无 entity id
        # 兜底应取第一个非静态的
        assert result["stats"]["unique_endpoints"] == 2

    def test_count_pathname_windows(self):
        """统计 pathname 在各窗口的出现次数"""
        calls = [
            {"pathname": "/api/users", "ts": 1.0},
            {"pathname": "/api/users", "ts": 1.5},
            {"pathname": "/api/users", "ts": 11.0},
            {"pathname": "/api/roles", "ts": 1.2},
        ]
        windows = {
            "create": {"start": 0.5, "end": 2.0},
            "query": {"start": 10.0, "end": 12.0},
        }
        counts = _count_pathname_windows(calls, windows)
        assert counts["/api/users"] == 2  # 出现在 create 和 query 两个窗口
        assert counts["/api/roles"] == 1  # 只出现在 create 窗口


# ============================================================
# StepExecutor config 参数测试
# ============================================================

class TestStepExecutorConfig:
    """测试 StepExecutor.config 属性（修复 AttributeError）"""

    def test_config_default(self):
        """默认 config 包含 test_data"""
        from lib.runtime.test_runtime import StepExecutor, ResponseParser
        parser = ResponseParser({})
        executor = StepExecutor(
            session=None, parser=parser, state={}, ts="123456",
            base_url="http://test"
        )
        assert executor.config is not None
        assert "test_data" in executor.config
        assert executor.config["test_data"]["name_prefix"] == "AT_"
        assert executor.config["test_data"]["mutable_prefix"] == "Updated_"

    def test_config_custom(self):
        """自定义 config 覆盖默认值"""
        from lib.runtime.test_runtime import StepExecutor, ResponseParser
        parser = ResponseParser({})
        custom_config = {
            "test_data": {
                "name_prefix": "TEST_",
                "mutable_prefix": "MOD_",
            }
        }
        executor = StepExecutor(
            session=None, parser=parser, state={}, ts="123456",
            base_url="http://test", config=custom_config
        )
        assert executor.config["test_data"]["name_prefix"] == "TEST_"
        assert executor.config["test_data"]["mutable_prefix"] == "MOD_"

    def test_config_none_fallback(self):
        """config=None 时使用默认值"""
        from lib.runtime.test_runtime import StepExecutor, ResponseParser
        parser = ResponseParser({})
        executor = StepExecutor(
            session=None, parser=parser, state={}, ts="123456",
            base_url="http://test", config=None
        )
        assert executor.config["test_data"]["name_prefix"] == "AT_"
