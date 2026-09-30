"""
test_playbook_builder.py — playbook_builder.builder 模块单元测试

测试目标：
- _classify_op_steps_type 分类逻辑全分支覆盖
- build_playbook 集成测试（meta / operations / fallback）
- _build_create_steps 创建步骤序列
- _build_delete_steps 删除步骤序列
- _build_update_steps 更新步骤序列
- _build_query_steps 查询步骤序列
- _build_generic_steps 通用操作步骤序列
- _build_page_nav_steps 跨页面操作步骤序列
"""
import pytest

from core.discovery.playbook_builder.builder import (
    _classify_op_steps_type,
    build_playbook,
    _build_create_steps,
    _build_delete_steps,
    _build_update_steps,
    _build_query_steps,
    _build_generic_steps,
    _build_page_nav_steps,
)


# ============================================================
# _classify_op_steps_type 分类逻辑测试
# ============================================================

class TestClassifyOpStepsType:
    """测试操作类型分类函数"""

    def test_create_with_fill_data(self):
        """有 fill_data 且无 edit_fill_data / is_delete → create"""
        op_data = {"fill_data": {"名称": "test_user"}}
        assert _classify_op_steps_type(op_data) == "create"

    def test_create_with_fill_data_no_marker(self):
        """有 fill_data 且 requires_marker=False → create"""
        op_data = {"fill_data": {"名称": "test"}, "requires_marker": False}
        assert _classify_op_steps_type(op_data) == "create"

    def test_update_with_edit_fill_data(self):
        """有 edit_fill_data → update"""
        op_data = {"edit_fill_data": {"名称": "edited"}}
        assert _classify_op_steps_type(op_data) == "update"

    def test_update_with_requires_marker(self):
        """有 fill_data + requires_marker → update"""
        op_data = {"fill_data": {"名称": "test"}, "requires_marker": True}
        assert _classify_op_steps_type(op_data) == "update"

    def test_update_with_row_action_and_form_fields(self):
        """is_row_action + form_fields → update"""
        op_data = {
            "is_row_action": True,
            "form_fields": [{"label": "名称", "selector": "input"}],
        }
        assert _classify_op_steps_type(op_data) == "update"

    def test_update_with_row_action_submit_and_dialog(self):
        """is_row_action + submit_locator_verified + dialog mode → update"""
        op_data = {
            "is_row_action": True,
            "submit_locator_verified": "button.submit",
            "interaction_mode": "dialog",
        }
        assert _classify_op_steps_type(op_data) == "update"

    def test_update_with_row_action_submit_locator_fallback(self):
        """is_row_action + submit_locator (非 verified) + dialog → update"""
        op_data = {
            "is_row_action": True,
            "submit_locator": "button.submit",
            "interaction_mode": "dialog",
        }
        assert _classify_op_steps_type(op_data) == "update"

    def test_delete_with_is_delete(self):
        """is_delete=True → delete"""
        op_data = {"is_delete": True}
        assert _classify_op_steps_type(op_data) == "delete"

    def test_delete_with_confirm_dialog_is_delete(self):
        """confirm_dialog.is_delete=True → delete"""
        op_data = {"confirm_dialog": {"is_delete": True}}
        assert _classify_op_steps_type(op_data) == "delete"

    def test_query_with_search_input(self):
        """有 search_input → query（优先级最高）"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "搜索"},
            "fill_data": {"名称": "test"},  # fill_data 也存在，但 search_input 优先
        }
        assert _classify_op_steps_type(op_data) == "query"

    def test_generic_empty_op_data(self):
        """空 op_data → generic"""
        assert _classify_op_steps_type({}) == "generic"

    def test_generic_minimal_op_data(self):
        """仅有不相关字段 → generic"""
        op_data = {"trigger_text": "导出", "success": True}
        assert _classify_op_steps_type(op_data) == "generic"

    def test_generic_row_action_without_form_or_dialog(self):
        """is_row_action 但无 form_fields / submit / dialog → generic"""
        op_data = {"is_row_action": True}
        assert _classify_op_steps_type(op_data) == "generic"

    def test_generic_row_action_submit_but_no_dialog(self):
        """is_row_action + submit 但 interaction_mode 不是 dialog → generic"""
        op_data = {
            "is_row_action": True,
            "submit_locator_verified": "button.ok",
            "interaction_mode": "inline",
        }
        assert _classify_op_steps_type(op_data) == "generic"

    def test_empty_fill_data_falls_through(self):
        """fill_data 为空字典 → 不算 create，继续判断"""
        op_data = {"fill_data": {}, "is_delete": True}
        assert _classify_op_steps_type(op_data) == "delete"

    def test_search_input_takes_priority_over_edit(self):
        """search_input 优先级高于 edit_fill_data"""
        op_data = {
            "search_input": {"locator": "input.search"},
            "edit_fill_data": {"名称": "x"},
        }
        assert _classify_op_steps_type(op_data) == "query"


# ============================================================
# build_playbook 集成测试
# ============================================================

class TestBuildPlaybook:
    """测试 playbook 整体构建"""

    def _make_ui_result(self, **overrides):
        """构造最小可用的 ui_result"""
        base = {
            "module_name": "用户管理",
            "target_url": "http://localhost/user",
            "base_url": "http://localhost",
            "login_url": "http://localhost/login",
            "framework": "element-ui",
            "auth_config": {"token": "abc"},
            "page_structure": {"tables": [], "forms": []},
            "validated_operations": {},
            "toolbar_buttons": [],
            "row_actions": [],
        }
        base.update(overrides)
        return base

    def test_meta_fields(self):
        """meta 包含正确的模块名、URL、版本等"""
        ui = self._make_ui_result()
        pb = build_playbook(ui)
        meta = pb["meta"]
        assert meta["module_name"] == "用户管理"
        assert meta["target_url"] == "http://localhost/user"
        assert meta["base_url"] == "http://localhost"
        assert meta["login_url"] == "http://localhost/login"
        assert meta["framework"] == "element-ui"
        assert meta["version"] == "1.0"
        assert meta["auth_config"] == {"token": "abc"}
        assert "generated_at" in meta

    def test_page_structure_passthrough(self):
        """page_structure 直接透传"""
        ps = {"tables": [{"id": "main"}], "forms": []}
        pb = build_playbook(self._make_ui_result(page_structure=ps))
        assert pb["page_structure"] == ps

    def test_empty_ui_result(self):
        """空 ui_result → 最小 playbook"""
        pb = build_playbook({})
        assert pb["meta"]["module_name"] == ""
        assert pb["meta"]["target_url"] == ""
        assert pb["operations"] == {}
        assert pb["page_structure"] == {}

    def test_single_create_operation(self):
        """单个 create 操作 → operations 中有一条记录"""
        ops = {
            "创建": {
                "success": True,
                "trigger_text": "新增",
                "fill_data": {"名称": "test_user"},
                "trigger_locator_verified": "button.create",
                "form_fields": [
                    {"label": "名称", "selector": "input.name", "type": "input", "kb_category": "input-generic"}
                ],
                "fill_rules": {"名称": {"rule": "name_pattern", "params": {"prefix": "test"}}},
                "submit_locator_verified": "button.submit",
            }
        }
        pb = build_playbook(self._make_ui_result(validated_operations=ops))
        assert "创建" in pb["operations"]
        entry = pb["operations"]["创建"]
        assert entry["role"] == "create"
        assert entry["display_name"] == "新增"
        assert entry["detection_status"] == "success"
        assert entry["replayable"] is True
        assert len(entry["steps"]) > 0

    def test_failed_operation_included(self):
        """失败操作也生成 entry（detection_status=failed）"""
        ops = {
            "删除": {
                "success": False,
                "error_type": "timeout",
                "error_text": "操作超时",
                "is_delete": True,
                "trigger_locator_verified": "button.delete",
            }
        }
        pb = build_playbook(self._make_ui_result(validated_operations=ops))
        assert "删除" in pb["operations"]
        entry = pb["operations"]["删除"]
        assert entry["detection_status"] == "failed"
        assert entry["error_type"] == "timeout"
        assert entry["error_text"] == "操作超时"

    def test_toolbar_buttons_fallback(self):
        """未在 validated_operations 中的 toolbar 按钮 → fallback 步骤"""
        toolbar = [
            {"text": "导出", "selector": "button.export"},
            {"text": "导入", "selector": "button.import"},
        ]
        pb = build_playbook(self._make_ui_result(toolbar_buttons=toolbar))
        assert "导出" in pb["operations"]
        assert "导入" in pb["operations"]
        entry = pb["operations"]["导出"]
        assert entry["role"] == "generic"
        assert entry["detection_status"] == "unvalidated"
        assert entry["replayable"] is True
        assert entry["steps"][0]["action"] == "click_button"

    def test_toolbar_fallback_skip_already_validated(self):
        """已在 validated_operations 中的按钮不会重复生成 fallback"""
        ops = {
            "导出": {
                "success": True,
                "trigger_text": "导出",
                "trigger_locator_verified": "button.export",
            }
        }
        toolbar = [{"text": "导出", "selector": "button.export"}]
        pb = build_playbook(self._make_ui_result(
            validated_operations=ops, toolbar_buttons=toolbar
        ))
        # 只有一条 "导出" 记录（来自 validated）
        assert pb["operations"]["导出"]["detection_status"] == "success"

    def test_row_actions_fallback(self):
        """row_actions 中的未验证按钮也生成 fallback"""
        row_actions = [{"text": "冻结", "selector": "button.freeze"}]
        pb = build_playbook(self._make_ui_result(row_actions=row_actions))
        assert "冻结" in pb["operations"]
        assert pb["operations"]["冻结"]["detection_status"] == "unvalidated"

    def test_toolbar_button_without_selector_uses_text_locator(self):
        """toolbar 按钮无 selector → 使用 button:has-text 构造"""
        toolbar = [{"text": "刷新"}]
        pb = build_playbook(self._make_ui_result(toolbar_buttons=toolbar))
        step = pb["operations"]["刷新"]["steps"][0]
        assert "has-text" in step["playwright_locator"]

    def test_marker_only_stored_for_create(self):
        """marker 仅在 role=create 时存储，其他操作 marker=None"""
        ops = {
            "创建": {
                "success": True,
                "trigger_text": "新增",
                "fill_data": {"名称": "test"},
                "marker": "test_marker_001",
                "trigger_locator_verified": "button.create",
                "submit_locator_verified": "button.ok",
            },
            "编辑": {
                "success": True,
                "trigger_text": "编辑",
                "edit_fill_data": {"名称": "edited"},
                "marker": "should_not_appear",
                "trigger_locator_verified": "button.edit",
                "is_row_action": True,
            },
        }
        pb = build_playbook(self._make_ui_result(validated_operations=ops))
        assert pb["operations"]["创建"]["marker"] == "test_marker_001"
        assert pb["operations"]["编辑"]["marker"] is None

    def test_page_nav_operation(self):
        """nav_info 中有 navigated_url → page-nav 模式，role=generic"""
        ops = {
            "授权": {
                "success": True,
                "trigger_text": "授权",
                "nav_info": {
                    "navigated_url": "http://localhost/auth",
                    "form_fields": [],
                    "fill_data": {},
                },
                "trigger_locator_verified": "button.auth",
            }
        }
        pb = build_playbook(self._make_ui_result(validated_operations=ops))
        entry = pb["operations"]["授权"]
        assert entry["role"] == "generic"  # page-nav 走 generic 角色
        assert "nav_info" in entry

    def test_multiple_operations(self):
        """多个不同类型的操作全部正确构建"""
        ops = {
            "创建": {
                "success": True,
                "trigger_text": "新增",
                "fill_data": {"名称": "test"},
                "trigger_locator_verified": "button.create",
                "submit_locator_verified": "button.ok",
            },
            "搜索": {
                "success": True,
                "trigger_text": "搜索",
                "search_input": {"locator": "input.search", "placeholder": "输入关键词"},
            },
            "删除": {
                "success": True,
                "trigger_text": "删除",
                "is_delete": True,
                "trigger_locator_verified": "button.delete",
                "confirmed": True,
            },
        }
        pb = build_playbook(self._make_ui_result(validated_operations=ops))
        assert len(pb["operations"]) == 3
        assert pb["operations"]["创建"]["role"] == "create"
        assert pb["operations"]["搜索"]["role"] == "query"
        assert pb["operations"]["删除"]["role"] == "delete"


# ============================================================
# _build_create_steps 创建步骤测试
# ============================================================

class TestBuildCreateSteps:
    """测试 create 操作步骤构建"""

    def test_standard_create_sequence(self):
        """标准创建：click_button → wait_for_dialog → fill_form → click_button(submit) → assert_success"""
        op_data = {
            "trigger_locator_verified": "button.create",
            "interaction_mode": "dialog",
            "dialog_locator": ".el-dialog",
            "form_fields": [
                {"label": "名称", "selector": "input.name", "type": "input", "kb_category": "input-generic"}
            ],
            "fill_rules": {"名称": {"rule": "name_pattern", "params": {"prefix": "test"}}},
            "submit_locator_verified": "button.submit",
            "submit_text": "确定",
            "success_locator": ".el-message--success",
        }
        steps = _build_create_steps(op_data)
        actions = [s["action"] for s in steps]
        assert actions == ["click_button", "wait_for_dialog", "fill_form", "click_button", "assert_success"]

    def test_create_without_trigger_locator(self):
        """无 trigger_locator → 跳过 click_button，但后续步骤仍存在"""
        op_data = {
            "submit_locator_verified": "button.ok",
            "success_locator": ".el-message--success",
        }
        steps = _build_create_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "click_button" in actions  # submit 按钮
        assert "assert_success" in actions

    def test_create_without_dialog(self):
        """无 dialog 信息 → wait_for_dialog 仍生成（使用默认 locator）"""
        op_data = {"trigger_locator_verified": "button.new"}
        steps = _build_create_steps(op_data)
        dialog_step = [s for s in steps if s["action"] == "wait_for_dialog"]
        assert len(dialog_step) == 1
        assert dialog_step[0]["playwright_locator"] == ".el-dialog__wrapper"

    def test_create_with_multi_step_fields(self):
        """multi_step_field_details → 生成含 kb_category 的字段"""
        op_data = {
            "trigger_locator_verified": "button.new",
            "form_fields": [
                {"label": "名称", "selector": "input.name", "type": "input", "kb_category": "input-generic"}
            ],
            "fill_rules": {"名称": {}},
            "multi_step_field_details": [
                {
                    "label": "角色",
                    "selector": ".role-select",
                    "kb_category": "el-select",
                    "fill_rule": {},
                    "is_editable": True,
                    "option_text": "管理员",
                }
            ],
            "submit_locator_verified": "button.ok",
        }
        steps = _build_create_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        labels = [f["label"] for f in fill_step["fields"]]
        assert "角色" in labels
        role_field = [f for f in fill_step["fields"] if f["label"] == "角色"][0]
        assert role_field["kb_category"] == "el-select"
        assert role_field["option_text"] == "管理员"

    def test_create_with_radio_field(self):
        """radio_field_details → 生成 radio 类型字段"""
        op_data = {
            "trigger_locator_verified": "button.new",
            "form_fields": [
                {"label": "名称", "selector": "input.name", "type": "input", "kb_category": "input-generic"}
            ],
            "fill_rules": {"名称": {}},
            "radio_field_details": [
                {
                    "label": "状态",
                    "option_locator": ".radio-active",
                    "fill_rule": {},
                    "option_text": "启用",
                }
            ],
            "submit_locator_verified": "button.ok",
        }
        steps = _build_create_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        radio = [f for f in fill_step["fields"] if f["label"] == "状态"][0]
        assert radio["type"] == "radio"
        assert radio["playwright_locator"] == ".radio-active"

    def test_create_marker_field_detection(self):
        """名称字段被标记为 is_marker"""
        op_data = {
            "trigger_locator_verified": "button.new",
            "form_fields": [
                {"label": "用户名称", "selector": "input.name", "type": "input", "kb_category": "input-generic"},
                {"label": "备注", "selector": "input.remark", "type": "input", "kb_category": "input-generic"},
            ],
            "fill_rules": {"用户名称": {}, "备注": {}},
            "submit_locator_verified": "button.ok",
        }
        steps = _build_create_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        name_field = [f for f in fill_step["fields"] if f["label"] == "用户名称"][0]
        remark_field = [f for f in fill_step["fields"] if f["label"] == "备注"][0]
        assert name_field.get("is_marker") is True
        assert "is_marker" not in remark_field

    def test_create_js_click_strategy(self):
        """submit_click_strategy=js → 步骤中含 click_strategy=js"""
        op_data = {
            "trigger_locator_verified": "button.new",
            "submit_locator_verified": "button.ok",
            "submit_click_strategy": "js",
        }
        steps = _build_create_steps(op_data)
        submit_step = [s for s in steps if s["action"] == "click_button" and "提交" in s.get("description", "")][0]
        assert submit_step["click_strategy"] == "js"

    def test_create_skips_select_fields_in_normal_loop(self):
        """kb_category 为 el-select / radio 等的字段在普通循环中被跳过"""
        op_data = {
            "trigger_locator_verified": "button.new",
            "form_fields": [
                {"label": "角色", "selector": ".sel", "type": "select", "kb_category": "el-select"},
                {"label": "名称", "selector": "input.n", "type": "input", "kb_category": "input-generic"},
            ],
            "fill_rules": {"角色": {}, "名称": {}},
            "submit_locator_verified": "button.ok",
        }
        steps = _build_create_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        labels = [f["label"] for f in fill_step["fields"]]
        assert "角色" not in labels  # el-select 被跳过
        assert "名称" in labels


# ============================================================
# _build_delete_steps 删除步骤测试
# ============================================================

class TestBuildDeleteSteps:
    """测试 delete 操作步骤构建"""

    def test_standard_delete_sequence(self):
        """标准删除：find_row → click_button → confirm_dialog → assert_row_disappeared"""
        op_data = {
            "trigger_locator_verified": "button.delete",
            "selectors": {"trigger": "删除"},
            "confirmed": True,
        }
        steps = _build_delete_steps(op_data)
        actions = [s["action"] for s in steps]
        assert actions[0] == "find_row"
        assert "click_button" in actions
        assert "confirm_dialog" in actions
        assert actions[-1] == "assert_row_disappeared"

    def test_delete_with_checkbox(self):
        """needs_checkbox → 插入 select_row_checkbox 步骤"""
        op_data = {
            "needs_checkbox": True,
            "checkbox_locator": ".el-checkbox",
            "trigger_locator_verified": "button.delete",
            "confirmed": True,
        }
        steps = _build_delete_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "select_row_checkbox" in actions
        checkbox_step = [s for s in steps if s["action"] == "select_row_checkbox"][0]
        assert checkbox_step["checkbox_locator"] == ".el-checkbox"

    def test_delete_without_confirm(self):
        """无 confirmed → 不生成 confirm_dialog 步骤"""
        op_data = {
            "trigger_locator_verified": "button.delete",
            "selectors": {},
        }
        steps = _build_delete_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "confirm_dialog" not in actions
        assert actions[-1] == "assert_row_disappeared"

    def test_delete_with_post_confirm_dialog_remains(self):
        """post_confirm_state.dialog_remains → 插入 close_dialog"""
        op_data = {
            "trigger_locator_verified": "button.delete",
            "confirmed": True,
            "post_confirm_state": {"dialog_remains": True},
        }
        steps = _build_delete_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "close_dialog" in actions

    def test_delete_confirm_from_selectors(self):
        """selectors.confirm 也能触发 confirm_dialog 步骤"""
        op_data = {
            "trigger_locator_verified": "button.delete",
            "selectors": {"trigger": "删除", "confirm": "确定"},
        }
        steps = _build_delete_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "confirm_dialog" in actions


# ============================================================
# _build_update_steps 更新步骤测试
# ============================================================

class TestBuildUpdateSteps:
    """测试 update 操作步骤构建"""

    def test_standard_update_sequence(self):
        """标准编辑：find_row → click_button → wait_for_dialog → fill_form → click_button(submit) → assert_success"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "interaction_mode": "dialog",
            "form_fields": [
                {"label": "名称", "selector": "input.name", "type": "input", "kb_category": "input-generic",
                 "placeholder": "请输入名称"}
            ],
            "fill_rules": {"名称": {"rule": "name_pattern"}},
            "submit_locator_verified": "button.ok",
        }
        steps = _build_update_steps(op_data)
        actions = [s["action"] for s in steps]
        assert actions[0] == "find_row"
        assert "wait_for_dialog" in actions
        assert "fill_form" in actions
        assert actions[-1] == "assert_success"

    def test_update_row_action(self):
        """is_row_action → click_row_button 而非 click_button"""
        op_data = {
            "is_row_action": True,
            "selectors": {"trigger": "编辑"},
            "button_text": "编辑",
            "trigger_locator_verified": "button.edit-row",
            "interaction_mode": "dialog",
            "submit_locator_verified": "button.ok",
        }
        steps = _build_update_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "click_row_button" in actions
        # 不应该有普通 click_button（submit 除外）
        click_btns = [s for s in steps if s["action"] == "click_button"]
        assert all("提交" in s.get("description", "") for s in click_btns)

    def test_update_multi_candidate_locators(self):
        """编辑表单字段生成多候选选择器"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "form_fields": [
                {
                    "label": "用户名称",
                    "selector": ".dialog input:nth-of-type(1)",
                    "type": "input",
                    "kb_category": "input-generic",
                    "placeholder": "请输入名称",
                    "visible_index": 0,
                }
            ],
            "fill_rules": {"用户名称": {}},
            "submit_locator_verified": "button.ok",
        }
        steps = _build_update_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        field = fill_step["fields"][0]
        assert field["locator_strategy"] == "placeholder"
        assert len(field["fallback_locators"]) >= 2  # original + label + visible_index

    def test_update_without_form_fields(self):
        """无 form_fields → 不生成 fill_form 步骤"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "submit_locator_verified": "button.ok",
        }
        steps = _build_update_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "fill_form" not in actions


# ============================================================
# _build_query_steps 查询步骤测试
# ============================================================

class TestBuildQuerySteps:
    """测试 query 操作步骤构建"""

    def test_query_with_search_input_and_button(self):
        """搜索框 + 按钮触发 → fill_input + click_button + wait_for_table_ready"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "请输入关键词"},
            "trigger_locator_verified": "button.search",
            "trigger_mode": "button",
        }
        steps = _build_query_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "fill_input" in actions
        assert "click_button" in actions
        assert actions[-1] == "wait_for_table_ready"

    def test_query_with_search_input_and_enter(self):
        """搜索框 + 回车触发 → fill_input + press_key + wait_for_table_ready"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "搜索"},
            "trigger_mode": "enter",
        }
        steps = _build_query_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "fill_input" in actions
        assert "press_key" in actions
        assert actions[-1] == "wait_for_table_ready"

    def test_query_input_locator_triggers_enter(self):
        """trigger_locator 是 input 类型 → 用回车替代"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "搜索"},
            "trigger_locator_verified": "input[type='text']",
            "trigger_mode": "button",
        }
        steps = _build_query_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "press_key" in actions
        # 不应该有 click_button
        assert "click_button" not in actions

    def test_query_without_search_input(self):
        """无 search_input → 只有 click_button + wait_for_table_ready"""
        op_data = {
            "trigger_locator_verified": "button.query",
        }
        steps = _build_query_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "fill_input" not in actions
        assert "click_button" in actions
        assert actions[-1] == "wait_for_table_ready"

    def test_query_auto_trigger_mode(self):
        """trigger_mode=auto → 不生成触发步骤（自动触发）"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "搜索"},
            "trigger_locator_verified": "button.search",
            "trigger_mode": "auto",
        }
        steps = _build_query_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "fill_input" in actions
        assert "click_button" not in actions
        assert actions[-1] == "wait_for_table_ready"

    def test_query_fill_input_has_marker_placeholder(self):
        """搜索输入步骤包含 {marker_name} 占位符"""
        op_data = {
            "search_input": {"locator": "input.search", "placeholder": "名称"},
            "trigger_mode": "enter",
        }
        steps = _build_query_steps(op_data)
        fill = [s for s in steps if s["action"] == "fill_input"][0]
        assert fill["value"] == "{marker_name}"


# ============================================================
# _build_generic_steps 通用操作步骤测试
# ============================================================

class TestBuildGenericSteps:
    """测试通用操作步骤构建"""

    def test_generic_with_trigger_locator(self):
        """有 trigger_locator → click_button"""
        op_data = {
            "selectors": {"trigger": "导出"},
            "trigger_locator_verified": "button.export",
            "confirmed": True,
        }
        steps = _build_generic_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "click_button" in actions
        assert "confirm_dialog" in actions

    def test_generic_with_needs_checkbox(self):
        """needs_checkbox → find_row + select_row_checkbox"""
        op_data = {
            "needs_checkbox": True,
            "checkbox_locator": ".el-checkbox",
            "selectors": {"trigger": "批量删除"},
            "trigger_locator_verified": "button.batch-delete",
            "confirmed": True,
        }
        steps = _build_generic_steps(op_data)
        actions = [s["action"] for s in steps]
        assert actions[0] == "find_row"
        assert "select_row_checkbox" in actions

    def test_generic_with_row_selector(self):
        """selectors.row_selector → find_row"""
        op_data = {
            "selectors": {"trigger": "授权", "row_selector": "tr:has-text('admin')"},
            "trigger_locator_verified": "button.auth",
        }
        steps = _build_generic_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "find_row" in actions

    def test_generic_with_dropdown_item(self):
        """dropdown_item_text → click_row_more"""
        op_data = {
            "selectors": {},
            "dropdown_item_text_verified": "冻结",
            "expand_strategy_verified": "hover",
        }
        steps = _build_generic_steps(op_data)
        more_step = [s for s in steps if s["action"] == "click_row_more"][0]
        assert more_step["item_text"] == "冻结"
        assert more_step["expand_strategy"] == "hover"

    def test_generic_with_dialog_fills(self):
        """dialog_fills → fill_form 步骤"""
        op_data = {
            "selectors": {"trigger": "分配"},
            "trigger_locator_verified": "button.assign",
            "dialog_fills": [
                {
                    "label": "角色",
                    "selector": ".role-select",
                    "option_text": "管理员",
                    "kb_category": "el-select",
                    "is_editable": True,
                }
            ],
            "confirmed": True,
        }
        steps = _build_generic_steps(op_data)
        fill_steps = [s for s in steps if s["action"] == "fill_form"]
        assert len(fill_steps) == 1
        assert fill_steps[0]["fields"][0]["label"] == "角色"

    def test_generic_with_edit_field_details(self):
        """edit_field_details → fill_form 带多候选选择器"""
        op_data = {
            "selectors": {"trigger": "调整"},
            "trigger_locator_verified": "button.adjust",
            "edit_field_details": [
                {
                    "label": "数量",
                    "inputType": "number",
                    "placeholder": "请输入数量",
                    "selector": ".qty-input",
                    "visible_index": 2,
                }
            ],
            "confirmed": True,
        }
        steps = _build_generic_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        field = fill_step["fields"][0]
        assert field["inputType"] == "number"
        assert field["fill_rule"]["rule"] == "number_pattern"
        assert field["locator_strategy"] == "placeholder"
        assert len(field["fallback_locators"]) >= 1

    def test_generic_with_navigate_back(self):
        """navigate_back_url → navigate_back 步骤"""
        op_data = {
            "selectors": {"trigger": "查看"},
            "trigger_locator_verified": "button.view",
            "navigate_back_url": "http://localhost/list",
            "confirmed": True,
        }
        steps = _build_generic_steps(op_data)
        nav_step = [s for s in steps if s["action"] == "navigate_back"]
        assert len(nav_step) == 1
        assert nav_step[0]["url"] == "http://localhost/list"

    def test_generic_no_ui_action_no_assert(self):
        """无实际 UI 操作 → 不生成 assert_success"""
        op_data = {
            "selectors": {},
        }
        steps = _build_generic_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "assert_success" not in actions

    def test_generic_with_submit_result(self):
        """submit_result.locator → click_button(提交)"""
        op_data = {
            "selectors": {"trigger": "配置"},
            "trigger_locator_verified": "button.config",
            "submit_result": {"locator": "button.confirm-config", "text": "确认"},
        }
        steps = _build_generic_steps(op_data)
        click_steps = [s for s in steps if s["action"] == "click_button"]
        assert len(click_steps) >= 2  # trigger + submit

    def test_generic_post_confirm_dialog_remains(self):
        """post_confirm_state.dialog_remains → close_dialog"""
        op_data = {
            "selectors": {"trigger": "冻结"},
            "trigger_locator_verified": "button.freeze",
            "confirmed": True,
            "post_confirm_state": {"dialog_remains": True},
        }
        steps = _build_generic_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "close_dialog" in actions


# ============================================================
# _build_page_nav_steps 跨页面操作步骤测试
# ============================================================

class TestBuildPageNavSteps:
    """测试跨页面操作步骤构建"""

    def test_page_nav_basic_sequence(self):
        """基本跨页面：click_button → wait_for_url → click_button(提交) → assert_success → navigate_back"""
        op_data = {
            "selectors": {"trigger": "授权"},
            "trigger_locator_verified": "button.auth",
            "nav_info": {
                "navigated_url": "http://localhost/auth?tab=role",
                "form_fields": [],
                "fill_data": {},
                "submit_result": {"button_text": "保存"},
            },
            "navigate_back_url": "http://localhost/users",
        }
        steps = _build_page_nav_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "click_button" in actions
        assert "wait_for_url" in actions
        assert "assert_success" in actions
        assert "navigate_back" in actions

    def test_page_nav_with_checkbox(self):
        """needs_checkbox → find_row + select_row_checkbox"""
        op_data = {
            "needs_checkbox": True,
            "checkbox_locator": ".cb",
            "selectors": {"trigger": "分配"},
            "trigger_locator_verified": "button.assign",
            "nav_info": {
                "navigated_url": "http://localhost/assign",
                "submit_result": {"button_text": "确定"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        actions = [s["action"] for s in steps]
        assert actions[0] == "find_row"
        assert "select_row_checkbox" in actions

    def test_page_nav_with_row_selector_and_trigger(self):
        """row_selector + trigger → click_row_button"""
        op_data = {
            "selectors": {"trigger": "添加用户", "row_selector": "tr:has-text('admin')"},
            "nav_info": {
                "navigated_url": "http://localhost/add-user",
                "submit_result": {"button_text": "确定"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        row_btn = [s for s in steps if s["action"] == "click_row_button"]
        assert len(row_btn) == 1
        assert row_btn[0]["button_text"] == "添加用户"

    def test_page_nav_with_dropdown_in_row_selector(self):
        """row_selector 含 '>' → click_row_more"""
        op_data = {
            "selectors": {"trigger": "授权", "row_selector": "更多 > 授权"},
            "nav_info": {
                "navigated_url": "http://localhost/auth",
                "submit_result": {"button_text": "保存"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        more_step = [s for s in steps if s["action"] == "click_row_more"]
        assert len(more_step) == 1
        assert more_step[0]["item_text"] == "授权"

    def test_page_nav_with_form_fields(self):
        """nav_info 有 form_fields → fill_form 步骤"""
        op_data = {
            "selectors": {"trigger": "配置"},
            "trigger_locator_verified": "button.config",
            "nav_info": {
                "navigated_url": "http://localhost/config",
                "form_fields": [
                    {"label": "超时时间", "selector": "input.timeout", "kb_category": "input-generic", "type": "input"},
                ],
                "fill_data": {"超时时间": "3600"},
                "field_states": [],
                "submit_result": {"button_text": "保存"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"]
        assert len(fill_step) == 1
        assert fill_step[0]["fields"][0]["label"] == "超时时间"

    def test_page_nav_select_field(self):
        """nav_info 中 el-select 字段正确构建"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "nav_info": {
                "navigated_url": "http://localhost/edit",
                "form_fields": [
                    {"label": "角色", "selector": ".role-sel", "kb_category": "el-select", "type": "select"},
                ],
                "fill_data": {},
                "field_states": [
                    {"label": "角色", "isDisabled": False, "isMultiSelect": True},
                ],
                "submit_result": {"button_text": "确定"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        field = fill_step["fields"][0]
        assert field["type"] == "select"
        assert field["is_multi_select"] is True

    def test_page_nav_disabled_field_skipped(self):
        """field_states 标记 isDisabled 的字段被跳过"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "nav_info": {
                "navigated_url": "http://localhost/edit",
                "form_fields": [
                    {"label": "ID", "selector": "input.id", "kb_category": "input-generic", "type": "input"},
                    {"label": "名称", "selector": "input.name", "kb_category": "input-generic", "type": "input"},
                ],
                "fill_data": {"名称": "test"},
                "field_states": [
                    {"label": "ID", "isDisabled": True},
                    {"label": "名称", "isDisabled": False},
                ],
                "submit_result": {"button_text": "保存"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        labels = [f["label"] for f in fill_step["fields"]]
        assert "ID" not in labels
        assert "名称" in labels

    def test_page_nav_wait_for_url_strips_query(self):
        """wait_for_url 的 url_pattern 去掉查询参数"""
        op_data = {
            "selectors": {"trigger": "查看"},
            "trigger_locator_verified": "button.view",
            "nav_info": {
                "navigated_url": "http://localhost/detail?id=123&tab=info",
                "submit_result": {"button_text": "返回"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        wait_step = [s for s in steps if s["action"] == "wait_for_url"][0]
        assert wait_step["url_pattern"] == "http://localhost/detail"

    def test_page_nav_fallback_trigger_text(self):
        """无 trigger_locator 也无 row_selector → 使用 trigger 文本构造 locator"""
        op_data = {
            "selectors": {"trigger": "配置管理"},
            "nav_info": {
                "navigated_url": "http://localhost/config",
                "submit_result": {"button_text": "保存"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        click_step = [s for s in steps if s["action"] == "click_button"][0]
        assert "配置管理" in click_step["playwright_locator"]

    def test_page_nav_radio_field(self):
        """nav_info 中 radio 字段正确构建"""
        op_data = {
            "selectors": {"trigger": "编辑"},
            "trigger_locator_verified": "button.edit",
            "nav_info": {
                "navigated_url": "http://localhost/edit",
                "form_fields": [
                    {"label": "状态", "selector": ".radio-on", "kb_category": "radio", "type": "radio",
                     "firstOptionText": "启用"},
                ],
                "fill_data": {},
                "field_states": [
                    {"label": "状态", "isDisabled": False, "value": "启用"},
                ],
                "submit_result": {"button_text": "保存"},
            },
        }
        steps = _build_page_nav_steps(op_data)
        fill_step = [s for s in steps if s["action"] == "fill_form"][0]
        radio = fill_step["fields"][0]
        assert radio["type"] == "radio"
        assert radio["option_text"] == "启用"
        assert radio["firstOptionText"] == "启用"
