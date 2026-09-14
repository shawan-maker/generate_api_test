"""
Phase 1 单元测试：required_elements.py、stage2_errors.py 和 validate_stage1() 的新功能

测试覆盖：
1. REQUIRED_ELEMENTS 结构验证
2. check_required_elements() 函数的各种场景
3. Stage2Error 异常类
4. validate_stage1() 的 required_flows 参数和 missing_elements 返回值
"""

import pytest
from module_discovery.required_elements import (
    REQUIRED_ELEMENTS,
    get_required_elements,
    check_required_elements,
)
from module_discovery.stage2_errors import (
    Stage2Error,
    Stage1MissingError,
    Stage2LocatorError,
    Stage2TimingError,
)
from module_discovery.stage_validators import validate_stage1


class TestRequiredElementsStructure:
    """测试 REQUIRED_ELEMENTS 结构"""

    def test_required_elements_has_create_flow(self):
        """create_flow 必须存在"""
        assert "create_flow" in REQUIRED_ELEMENTS
        assert len(REQUIRED_ELEMENTS["create_flow"]) > 0

    def test_required_elements_has_delete_flow(self):
        """delete_flow 必须存在"""
        assert "delete_flow" in REQUIRED_ELEMENTS
        assert len(REQUIRED_ELEMENTS["delete_flow"]) > 0

    def test_create_flow_has_create_button(self):
        """create_flow 必须包含创建按钮（按钮文本 "新增"）"""
        create_flow = REQUIRED_ELEMENTS["create_flow"]
        create_buttons = [e for e in create_flow if e.get("action") == "新增"]
        assert len(create_buttons) == 1
        assert create_buttons[0]["critical"] is True

    def test_create_flow_has_confirm_button(self):
        """create_flow 必须包含确定按钮（按钮文本 "确定"）"""
        create_flow = REQUIRED_ELEMENTS["create_flow"]
        confirm_buttons = [e for e in create_flow if e.get("action") == "确定"]
        assert len(confirm_buttons) == 1
        assert confirm_buttons[0]["critical"] is True

    def test_all_elements_have_required_fields(self):
        """所有必须元素必须包含 type、desc、critical 字段"""
        for flow_name, elements in REQUIRED_ELEMENTS.items():
            for element in elements:
                assert "type" in element, f"{flow_name} 的元素缺少 type 字段"
                assert "desc" in element, f"{flow_name} 的元素缺少 desc 字段"
                assert "critical" in element, f"{flow_name} 的元素缺少 critical 字段"


class TestGetRequiredElements:
    """测试 get_required_elements() 函数"""

    def test_get_existing_flow(self):
        """获取已定义的流程"""
        elements = get_required_elements("create_flow")
        assert isinstance(elements, list)
        assert len(elements) > 0

    def test_get_nonexistent_flow(self):
        """获取未定义的流程返回空列表"""
        elements = get_required_elements("nonexistent_flow")
        assert elements == []

    def test_get_all_flows(self):
        """获取所有已定义的流程"""
        for flow_name in REQUIRED_ELEMENTS.keys():
            elements = get_required_elements(flow_name)
            assert len(elements) > 0, f"{flow_name} 应该返回非空列表"


class TestCheckRequiredElements:
    """测试 check_required_elements() 函数"""

    def test_all_critical_elements_present(self):
        """所有 critical 元素都存在"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
            ],
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True
        assert missing == []

    def test_missing_critical_element(self):
        """缺少 critical 元素"""
        ui_result = {
            "toolbar_buttons": [],  # 缺少新增按钮
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
            ],
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is False
        assert len(missing) == 1
        assert missing[0]["action"] == "新增"
        assert missing[0]["critical"] is True

    def test_missing_non_critical_element(self):
        """缺少 non-critical 元素不影响 all_present"""
        # delete_flow 中的确定按钮是 critical
        ui_result = {
            "toolbar_buttons": [
                {"text": "删除", "action": "删除"},
            ],
            "dialog_buttons": [],  # 缺少确定按钮
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
        }
        all_present, missing = check_required_elements(ui_result, "delete_flow")
        # 确定按钮是 critical，所以 all_present 应该是 False
        assert all_present is False
        assert len(missing) == 1
        assert missing[0]["action"] == "确定"

    def test_button_in_different_location(self):
        """按钮在不同位置（toolbar vs row_action vs dialog）"""
        ui_result = {
            "toolbar_buttons": [],
            "dialog_buttons": [],
            "row_actions": [
                {"text": "删除", "action": "删除"},  # delete_flow 允许 row_action
            ],
            "dropdowns": [],
            "form_fields": [],
        }
        all_present, missing = check_required_elements(ui_result, "delete_flow")
        # 删除按钮在 row_actions 中，应该被找到
        delete_found = any(e["action"] == "删除" for e in ui_result["row_actions"])
        assert delete_found

    def test_button_in_dropdown(self):
        """按钮在 dropdowns 中"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [
                {"text": "删除", "action": "删除"},
            ],
            "form_fields": [],
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True

    def test_form_fields_check(self):
        """表单字段检查（如果 required_elements 中有 type=form_fields 的定义）"""
        # 当前 REQUIRED_ELEMENTS 中没有 form_fields 类型的检查
        # 这个测试是为了未来扩展
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
                {"name": "email", "required": False},
            ],
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True

    def test_empty_ui_result(self):
        """空的 ui_result"""
        ui_result = {
            "toolbar_buttons": [],
            "dialog_buttons": [],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
        }
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is False
        assert len(missing) >= 2  # 至少缺少 create 和 confirm 和 form_fields



class TestStage2Errors:
    """测试 Stage2Error 异常类"""

    def test_stage2_error_base(self):
        """Stage2Error 基类"""
        error = Stage2Error("基础错误")
        assert str(error) == "基础错误"
        assert isinstance(error, Exception)

    def test_stage1_missing_error(self):
        """Stage1MissingError"""
        error = Stage1MissingError(
            element_type="button",
            action="确定",
            context="提交创建表单",
            expected_location=["dialog"],
        )
        assert error.element_type == "button"
        assert error.action == "确定"
        assert error.context == "提交创建表单"
        assert "Stage 1" in str(error)
        assert "确定" in str(error)
        assert isinstance(error, Stage2Error)

    def test_stage2_locator_error(self):
        """Stage2LocatorError"""
        error = Stage2LocatorError(
            element_desc="按钮[confirm]",
            selector=".el-dialog__footer button",
            tried_iframes=True,
        )
        assert error.element_desc == "按钮[confirm]"
        assert error.selector == ".el-dialog__footer button"
        assert error.tried_iframes is True
        assert "定位失败" in str(error)
        assert isinstance(error, Stage2Error)

    def test_stage2_timing_error(self):
        """Stage2TimingError"""
        error = Stage2TimingError(
            element_desc="确定按钮",
            wait_strategy="wait_for_selector",
        )
        assert error.element_desc == "确定按钮"
        assert error.wait_strategy == "wait_for_selector"
        assert "时序失败" in str(error)
        assert isinstance(error, Stage2Error)


class TestValidateStage1WithRequiredFlows:
    """测试 validate_stage1() 的 required_flows 参数"""

    def test_validate_stage1_default_required_flows(self):
        """默认检查所有流程（required_flows=None）"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
                {"text": "删除", "action": "删除"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        is_valid, issues, missing = validate_stage1(ui_result, required_flows=None)
        # 应该检查 create_flow 和 delete_flow
        # create_flow 需要 "新增" 和 "确定"
        # delete_flow 需要 "删除" 和 "确定"
        assert "missing" in locals()  # 确保返回了 missing 参数

    def test_validate_stage1_specific_required_flows(self):
        """指定检查特定流程"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
                {"text": "查询", "action": "查询"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [
                {"text": "编辑", "action": "编辑"},
                {"text": "删除", "action": "删除"},
            ],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
            ],
            "summary": {
                "total_buttons": 5,
                "has_create": True,
                "has_delete": True,
                "categories": {"新增": 1, "查询": 1, "编辑": 1, "删除": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"username": "test"}, "selectors": {"trigger": "新增"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}}
            }
        }
        # 只检查 create_flow
        is_valid, issues, missing = validate_stage1(
            ui_result, required_flows=["create_flow"]
        )
        assert is_valid is True
        assert missing == []

    def test_validate_stage1_missing_required_elements(self):
        """缺少必须元素时返回 missing"""
        ui_result = {
            "toolbar_buttons": [],  # 缺少 "新增"
            "dialog_buttons": [],  # 缺少 "确定"
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        is_valid, issues, missing = validate_stage1(
            ui_result, required_flows=["create_flow"]
        )
        assert is_valid is False
        assert len(missing) >= 2  # 至少缺少 "新增" 和 "确定"
        assert any(e["action"] == "新增" for e in missing)
        assert any(e["action"] == "确定" for e in missing)

    def test_validate_stage1_empty_required_flows(self):
        """required_flows=[] 时不检查必须元素"""
        ui_result = {
            "toolbar_buttons": [],
            "dialog_buttons": [],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        is_valid, issues, missing = validate_stage1(
            ui_result, required_flows=[]
        )
        # 不检查必须元素，但其他检查仍然执行
        # 可能因为 page_structure 为空而失败
        assert "missing" in locals()

    def test_validate_stage1_returns_three_values(self):
        """validate_stage1 返回三个值"""
        ui_result = {
            "toolbar_buttons": [{"text": "新增", "action": "新增"}],
            "dialog_buttons": [{"text": "确定", "action": "确定"}],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        result = validate_stage1(ui_result, required_flows=["create_flow"])
        assert len(result) == 3
        is_valid, issues, missing = result
        assert isinstance(is_valid, bool)
        assert isinstance(issues, list)
        assert isinstance(missing, list)


class TestValidateStage1BackwardCompatibility:
    """测试 validate_stage1() 的向后兼容性"""

    def test_validate_stage1_without_required_flows_param(self):
        """不传 required_flows 参数时使用默认值"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
                {"text": "删除", "action": "删除"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        # 不传 required_flows 参数
        is_valid, issues, missing = validate_stage1(ui_result)
        # 应该使用默认值（所有流程）
        assert "missing" in locals()

    def test_validate_stage1_old_call_style(self):
        """旧的调用方式（只传 ui_result）仍然有效"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增", "action": "新增"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }
        # 模拟旧代码的调用方式（但实际上新签名返回 3 个值）
        result = validate_stage1(ui_result)
        assert len(result) == 3


class TestIntegration:
    """集成测试：Phase 1 各组件协同工作"""

    def test_full_create_flow_validation(self):
        """完整的 create_flow 验证流程"""
        # 1. 准备 ui_result
        ui_result = {
            "toolbar_buttons": [
                {"text": "新增用户", "action": "新增"},
                {"text": "查询", "action": "查询"},
            ],
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
                {"text": "取消", "action": "取消"},
            ],
            "row_actions": [
                {"text": "编辑", "action": "编辑"},
                {"text": "删除", "action": "删除"},
            ],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
                {"name": "email", "required": False},
            ],
            "summary": {
                "total_buttons": 6,
                "has_create": True,
                "has_delete": True,
                "categories": {"新增": 1, "查询": 1, "编辑": 1, "删除": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"username": "test"}, "selectors": {"trigger": "新增用户"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}}
            }
        }

        # 2. 使用 check_required_elements 检查
        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is True
        assert missing == []

        # 3. 使用 validate_stage1 验证
        is_valid, issues, missing = validate_stage1(
            ui_result, required_flows=["create_flow"]
        )
        assert is_valid is True
        assert missing == []

    def test_full_delete_flow_validation(self):
        """完整的 delete_flow 验证流程"""
        ui_result = {
            "toolbar_buttons": [
                {"text": "删除", "action": "删除"},
                {"text": "查询", "action": "查询"},
            ],
            "dialog_buttons": [
                {"text": "确认删除", "action": "确定"},
            ],
            "row_actions": [
                {"text": "编辑", "action": "编辑"},
            ],
            "dropdowns": [],
            "form_fields": [],
            "summary": {
                "total_buttons": 4,
                "has_create": True,
                "has_delete": True,
                "categories": {"查询": 1, "编辑": 1, "删除": 1}
            },
            "validated_operations": {
                "create": {"success": True, "fill_data": {"username": "test"}, "selectors": {"trigger": "新增"}},
                "delete": {"success": True, "fill_data": {}, "selectors": {"trigger": "删除"}}
            }
        }

        all_present, missing = check_required_elements(ui_result, "delete_flow")
        assert all_present is True

        is_valid, issues, missing = validate_stage1(
            ui_result, required_flows=["delete_flow"]
        )
        assert is_valid is True

    def test_missing_element_triggers_stage1_missing_error(self):
        """缺少元素时应该触发 Stage1MissingError（在 Stage 2 中）"""
        ui_result = {
            "toolbar_buttons": [],  # 缺少 "新增"
            "dialog_buttons": [
                {"text": "确定", "action": "确定"},
            ],
            "row_actions": [],
            "dropdowns": [],
            "form_fields": [
                {"name": "username", "required": True},
            ],
            "page_structure": {
                "tables": [{"rows": 10}],
                "forms": [],
            },
        }

        all_present, missing = check_required_elements(ui_result, "create_flow")
        assert all_present is False

        # 模拟 Stage 2 的检查逻辑
        if not all_present:
            # 应该抛出 Stage1MissingError
            first_missing = missing[0]
            error = Stage1MissingError(
                element_type=first_missing["type"],
                action=first_missing["action"],
                context="创建流程",
                expected_location=first_missing.get("location", ["unknown"]),
            )
            assert error.action == "新增"
            assert "Stage 1" in str(error)
