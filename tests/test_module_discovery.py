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
    EndpointClassifier, classify_endpoint, deduplicate_calls, _classify_by_text
)
from module_discovery.stage_validators import (
    validate_stage1, validate_stage2, validate_stage3, validate_stage4
)
from module_discovery.analyze_flow import _filter_by_cooccurrence


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

    def test_classify_by_text_create(self):
        """按钮文本分类：创建"""
        classifier = EndpointClassifier()
        assert classifier.classify_by_text("新增用户") == "create"
        assert classifier.classify_by_text("创建") == "create"
        assert classifier.classify_by_text("添加") == "create"

    def test_classify_by_text_delete(self):
        """按钮文本分类：删除"""
        classifier = EndpointClassifier()
        assert classifier.classify_by_text("删除") == "delete"
        assert classifier.classify_by_text("移除") == "delete"

    def test_classify_by_text_update(self):
        """按钮文本分类：修改"""
        classifier = EndpointClassifier()
        assert classifier.classify_by_text("编辑") == "update"
        assert classifier.classify_by_text("修改") == "update"

    def test_classify_by_text_unknown(self):
        """按钮文本分类：未知"""
        classifier = EndpointClassifier()
        assert classifier.classify_by_text("随便什么东西") == "unknown"

    def test_deduplicate_basic(self):
        """基本去重功能"""
        calls = [
            {"method": "POST", "pathname": "/api/users/create", "context": "click:创建"},
            {"method": "POST", "pathname": "/api/users/create", "context": "click:创建"},
            {"method": "GET", "pathname": "/api/users/list", "context": "click:查询"},
        ]
        samples = {
            "/api/users/create": [{"status": 200, "body": '{"success":true}'}],
            "/api/users/list": [{"status": 200, "body": '{"entity":{"list":[]}}'}],
        }
        result = classifier = EndpointClassifier()
        result = classifier.deduplicate(calls, samples)

        assert result["stats"]["total_calls"] == 3
        assert result["stats"]["unique_endpoints"] == 2
        assert "create" in result["classified"] or "query" in result["classified"]

    def test_deduplicate_empty(self):
        """空输入去重"""
        result = EndpointClassifier().deduplicate([], {})
        assert result["stats"]["total_calls"] == 0
        assert result["stats"]["unique_endpoints"] == 0
        assert result["classified"] == {}


# ============================================================
# classify_endpoint 函数测试
# ============================================================

class TestClassifyEndpoint:
    """测试端点分类函数"""

    def test_query_by_url(self):
        """URL 查询关键词分类"""
        assert classify_endpoint("POST", "/api/users/list", []) == "query"
        assert classify_endpoint("POST", "/api/users/page", []) == "query"
        assert classify_endpoint("POST", "/api/users/search", []) == "query"

    def test_detail_by_url(self):
        """URL 详情关键词分类"""
        assert classify_endpoint("GET", "/api/users/detail", []) == "detail"
        assert classify_endpoint("GET", "/api/users/get", []) == "detail"

    def test_support_by_url(self):
        """校验类 API 分类"""
        assert classify_endpoint("POST", "/api/users/check", ["click:创建"]) == "support"
        assert classify_endpoint("POST", "/api/users/valid", ["click:创建"]) == "support"

    def test_delete_method(self):
        """DELETE 方法分类"""
        assert classify_endpoint("DELETE", "/api/users/delete", []) == "delete"
        assert classify_endpoint("DELETE", "/api/users/unlock", []) == "unlock"

    def test_context_based_post(self):
        """基于上下文的 POST 分类"""
        assert classify_endpoint("POST", "/api/users/xxx", ["click:创建用户"]) == "create"
        assert classify_endpoint("POST", "/api/users/yyy", ["click:编辑"]) == "update"
        assert classify_endpoint("POST", "/api/users/zzz", ["click:锁定"]) == "lock"

    def test_get_ignores_context(self):
        """GET 请求不受上下文影响"""
        # GET 请求无论上下文是什么都不应该是写操作
        assert classify_endpoint("GET", "/api/menu/tree", ["click:创建用户"]) == "other_get"

    def test_url_write_keywords(self):
        """URL 写操作关键词"""
        assert classify_endpoint("POST", "/api/users/create", []) == "create"
        assert classify_endpoint("POST", "/api/users/add", []) == "create"
        assert classify_endpoint("POST", "/api/users/update", []) == "update"
        assert classify_endpoint("POST", "/api/users/lock", []) == "lock"
        assert classify_endpoint("POST", "/api/users/unlock", []) == "unlock"
        assert classify_endpoint("POST", "/api/users/reset", []) == "reset"

    def test_fallback(self):
        """兜底分类"""
        assert classify_endpoint("POST", "/api/unknown-action", []) == "other_post"
        assert classify_endpoint("GET", "/api/unknown-query", []) == "other_get"


# ============================================================
# Stage 1 门控验证测试
# ============================================================

class TestValidateStage1:
    """测试 Stage 1 质量门控"""

    def test_valid_result(self):
        """有效的 Stage 1 结果"""
        result = {
            "toolbar_buttons": [{"text": "新增"}, {"text": "确定"}],
            "row_actions": [{"text": "编辑"}, {"text": "删除"}],
            "dialog_buttons": [{"text": "确定"}],
            "form_fields": [{"name": "name", "required": True}],
            "summary": {
                "total": 10,
                "has_create": True,
                "has_delete": True,
                "categories": {"create": 1, "delete": 1, "confirm": 2, "update": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"name": "test"}, "selectors": {"trigger": "新增"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}}
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
            "by_category": {
                "create": [{"method": "POST", "pathname": "/create"}],
                "query": [{"method": "POST", "pathname": "/list"}],
                "update": [{"method": "POST", "pathname": "/update"}],
                "delete": [{"method": "POST", "pathname": "/delete"}],
            },
            "stats": {"total_calls": 30, "unique_endpoints": 10},
            "response_samples": {"/create": [{"status": 200}]},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is True
        assert len(issues) == 0

    def test_missing_core_apis(self):
        """缺少核心 CRUD API"""
        result = {
            "by_category": {
                "query": [{"method": "POST", "pathname": "/list"}],
            },
            "stats": {"total_calls": 5, "unique_endpoints": 3},
            "response_samples": {},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is False
        assert any("缺少核心" in i for i in issues)

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

    def test_high_support_ratio(self):
        """辅助 API 比例过高"""
        result = {
            "by_category": {
                "create": [{"method": "POST", "pathname": "/create"}],
                "query": [{"method": "POST", "pathname": "/list"}],
                "update": [{"method": "POST", "pathname": "/update"}],
                "delete": [{"method": "POST", "pathname": "/delete"}],
                "support": [{"method": "GET", "pathname": f"/support{i}"} for i in range(30)],
            },
            "stats": {"total_calls": 50, "unique_endpoints": 34},
            "response_samples": {},
        }
        is_valid, issues = validate_stage2(result)
        assert is_valid is False
        assert any("辅助 API 比例过高" in i for i in issues)

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
# 同现频率过滤测试
# ============================================================

class TestCooccurrenceFilter:
    """测试同现频率过滤"""

    def test_basic_filtering(self):
        """基本过滤：出现在所有上下文中的 API 被标记为辅助"""
        endpoints = [
            {"pathname": "/api/users/create", "contexts": ["click:创建"]},
            {"pathname": "/api/menu/tree", "contexts": [
                "click:创建", "click:查询", "click:编辑", "click:删除", "click:锁定"
            ]},
            {"pathname": "/api/users/list", "contexts": ["click:查询"]},
            {"pathname": "/api/users/delete", "contexts": ["click:删除"]},
        ]
        result = _filter_by_cooccurrence(endpoints, threshold=0.6)
        assert "/api/menu/tree" in result
        assert "/api/users/create" not in result
        assert "/api/users/list" not in result
        assert "/api/users/delete" not in result

    def test_empty_endpoints(self):
        """空端点列表"""
        result = _filter_by_cooccurrence([], threshold=0.6)
        assert result == set()

    def test_no_cooccurrence(self):
        """无同现：每个 API 只出现在一个上下文中"""
        endpoints = [
            {"pathname": "/api/users/create", "contexts": ["click:创建"]},
            {"pathname": "/api/users/list", "contexts": ["click:查询"]},
            {"pathname": "/api/users/update", "contexts": ["click:编辑"]},
        ]
        result = _filter_by_cooccurrence(endpoints, threshold=0.6)
        assert len(result) == 0

    def test_high_threshold(self):
        """高阈值：只有出现在所有上下文中的才被过滤"""
        endpoints = [
            {"pathname": "/api/menu/tree", "contexts": [
                "click:创建", "click:查询", "click:编辑"
            ]},
            {"pathname": "/api/notice/count", "contexts": [
                "click:创建", "click:查询"
            ]},
        ]
        # 3 个按钮上下文，/menu/tree 出现 3/3 = 1.0, /notice/count 出现 2/3 = 0.67
        result = _filter_by_cooccurrence(endpoints, threshold=0.9)
        assert "/api/menu/tree" in result
        assert "/api/notice/count" not in result

    def test_low_threshold(self):
        """低阈值：出现在少数上下文中的也被过滤"""
        endpoints = [
            {"pathname": "/api/menu/tree", "contexts": [
                "click:创建", "click:查询"
            ]},
            {"pathname": "/api/users/create", "contexts": ["click:创建"]},
        ]
        # 2 个按钮上下文，/menu/tree 出现 2/2 = 1.0, /create 出现 1/2 = 0.5
        result = _filter_by_cooccurrence(endpoints, threshold=0.4)
        assert "/api/menu/tree" in result
        assert "/api/users/create" in result

    def test_dropdown_contexts(self):
        """下拉菜单上下文解析"""
        endpoints = [
            {"pathname": "/api/menu/tree", "contexts": [
                "click:创建", "dropdown:更多:锁定", "dropdown:更多:解锁",
                "click:查询", "click:删除"
            ]},
            {"pathname": "/api/users/lock", "contexts": ["dropdown:更多:锁定"]},
        ]
        result = _filter_by_cooccurrence(endpoints, threshold=0.6)
        assert "/api/menu/tree" in result
        assert "/api/users/lock" not in result


# ============================================================
# _classify_by_text 函数测试
# ============================================================

class TestClassifyByText:
    """测试按钮文本分类"""

    def test_create_keywords(self):
        """创建类关键词"""
        assert _classify_by_text("新增") == "create"
        assert _classify_by_text("创建用户") == "create"
        assert _classify_by_text("添加资源") == "create"
        assert _classify_by_text("新建实例") == "create"

    def test_delete_keywords(self):
        """删除类关键词"""
        assert _classify_by_text("删除") == "delete"
        assert _classify_by_text("移除") == "delete"
        assert _classify_by_text("清除") == "delete"

    def test_lock_keywords(self):
        """锁定类关键词"""
        assert _classify_by_text("锁定") == "lock"
        assert _classify_by_text("冻结") == "lock"
        assert _classify_by_text("停用") == "lock"

    def test_unlock_keywords(self):
        """解锁类关键词"""
        assert _classify_by_text("解锁") == "unlock"
        assert _classify_by_text("解冻") == "unlock"
        assert _classify_by_text("启用") == "unlock"

    def test_unknown_text(self):
        """未知文本"""
        assert _classify_by_text("随便什么") == "unknown"
        assert _classify_by_text("") == "unknown"
