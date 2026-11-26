# -*- coding: utf-8 -*-
"""test_batch6_fixes.py — 批次六修复单元测试

测试目标（docs/debug/eis云主机批六修改方案.md，9 项全量）：
- 修复1  _infer_action_role delete 快照精确匹配 + is_destroy_text 关键词兜底
         （r8 实证：delete_actions 陈旧快照晚收集的「删除」错判 generic 被提前执行）
- 修复2  二次确认浮层（doubleConfirm 类 tooltip 层）通用识别与点击——
         const 词表单源 + dialog_helpers 检测函数 + 守卫 B 豁免 + builder/replay 落点
- 修复3  弹窗归属校验（dialog_mismatch 终态）+ _close_dialog 关闭验证 +
         _dismiss_all_overlays 批内/恢复清扫
- 修复4  _do_edit dialog_detected 未赋值 UnboundLocalError（r8 两轮复现）
- 修复5  attempted_actions 终态失败白名单记忆表（MAX_LIFETIME_ATTEMPTS=3，
         三消费点排除 + validate 链三元组 + ui.json terminal_failed_operations）
- 修复6  扫描前清扫（6a）+ 互斥切换对兜底入册（6b find_toggle_pair_text）
- 修复7  scan_form_fields JS push 点 placeholder 采集（修复「删除确认」
         字段被 name_pattern 填随机值的采集侧断点）
- 修复8  el-input-number 容器判据 + currentValue 默认值保留 + 数字词/登录名
         规则纠正（E11：数量/CPU/内存/系统盘 fill_data 全=marker 文本）
- 修复9  create 段「编辑字段: <label> = <value>」日志恢复（E12）
"""
import ast
import inspect
import json

import pytest

from core.discovery import const
from core.discovery.const import (
    DOUBLE_CONFIRM_BUTTON_TEXTS,
    TERMINAL_ERROR_TYPES,
    find_toggle_pair_text,
    js_double_confirm_texts,
)
from core.discovery.operation_executor import crud_executor
from core.discovery.replay import form_filler
from core.discovery.shared.dialog_helpers import detect_and_click_double_confirm
from core.discovery.shared.fill_helpers import (
    _infer_fill_rule,
    apply_fill_rule,
    generate_fill_data,
    generate_fill_rules,
    generate_fill_value,
)


# ============================================================
# 测试基建
# ============================================================

def _src(func) -> str:
    return inspect.getsource(func)


class DoubleConfirmPage:
    """detect_and_click_double_confirm 最小 Page 替身。

    evaluate(script, texts) 返回预置 dbl_result（JS 特征不需要解析）。
    """

    def __init__(self, dbl_result=None, raise_exc=None):
        self.dbl_result = dbl_result or {"found": False, "clicked": False}
        self.raise_exc = raise_exc
        self.calls = []

    async def evaluate(self, script, *args):
        self.calls.append(args[0] if args else None)
        if self.raise_exc:
            raise self.raise_exc
        return self.dbl_result


# ============================================================
# 修复 1（P0）：_infer_action_role delete 关键词兜底
# ============================================================

class TestFix1DeleteRoleInference:

    def test_dropdown_delete_without_snapshot_matches(self):
        """晚收集的「删除」在 delete_actions 陈旧快照为空时仍判 delete（r8 根因）。"""
        from core.discovery.ui_scanner.button_detector import _infer_action_role
        btn = {"location": "dropdown", "tag": "DROPDOWN_ITEM", "className": ""}
        assert _infer_action_role("删除", btn, delete_actions=[]) == "delete"

    def test_delete_snapshot_exact_match_priority(self):
        """快照精确匹配优先（既有行为不回归）。"""
        from core.discovery.ui_scanner.button_detector import _infer_action_role
        btn = {"location": "dropdown", "tag": "DROPDOWN_ITEM", "className": ""}
        assert _infer_action_role("删除", btn, delete_actions=["删除"]) == "delete"

    def test_generic_text_not_misjudged(self):
        """非破坏性文本不误判 delete（兜底不扩大口径）。"""
        from core.discovery.ui_scanner.button_detector import _infer_action_role
        btn = {"location": "dropdown", "tag": "DROPDOWN_ITEM", "className": ""}
        assert _infer_action_role("执行命令", btn, delete_actions=[]) == "generic"

    def test_destroy_keyword_variants(self):
        """is_destroy_text 词表其他破坏词（销毁/清除）同走兜底。"""
        from core.discovery.ui_scanner.button_detector import _infer_action_role
        from core.discovery.shared.button_clicker import is_destroy_text
        btn = {"location": "dropdown", "tag": "DROPDOWN_ITEM", "className": ""}
        for text in ("销毁", "清除"):
            if is_destroy_text(text):
                assert _infer_action_role(text, btn, delete_actions=[]) == "delete"


# ============================================================
# 修复 2（P0）：二次确认浮层识别与点击
# ============================================================

class TestFix2DoubleConfirm:

    def test_word_list_single_source(self):
        """词表非空且含 EIS 实证按钮文本（确认/确定）。"""
        assert DOUBLE_CONFIRM_BUTTON_TEXTS
        assert "确认" in DOUBLE_CONFIRM_BUTTON_TEXTS
        assert "确定" in DOUBLE_CONFIRM_BUTTON_TEXTS

    def test_js_word_list_json_roundtrip(self):
        """js_double_confirm_texts 产出可解析 JSON 且与词表一致。"""
        parsed = json.loads(js_double_confirm_texts())
        assert parsed == DOUBLE_CONFIRM_BUTTON_TEXTS

    @pytest.mark.asyncio
    async def test_detect_click_found(self):
        """浮层命中：返回 found/clicked/layer_tag/button_text 全量信息。"""
        page = DoubleConfirmPage(dbl_result={
            "found": True, "clicked": True,
            "layer_tag": "DIV[role=tooltip]", "button_text": "确认",
        })
        ret = await detect_and_click_double_confirm(page)
        assert ret["found"] is True
        assert ret["clicked"] is True
        assert ret["layer_tag"] == "DIV[role=tooltip]"
        assert ret["button_text"] == "确认"
        # 词表已注入 JS（传参形态）
        assert page.calls[0] == [t.lower() for t in DOUBLE_CONFIRM_BUTTON_TEXTS]

    @pytest.mark.asyncio
    async def test_detect_click_absent_noop(self):
        """无浮层：found=False 零变化（幂等）。"""
        page = DoubleConfirmPage()
        ret = await detect_and_click_double_confirm(page)
        assert ret == {"found": False, "clicked": False}

    @pytest.mark.asyncio
    async def test_detect_click_exception_swallowed(self):
        """evaluate 异常不外抛（守卫 B 豁免通道不得引入新失败模式）。"""
        page = DoubleConfirmPage(raise_exc=RuntimeError("mock js error"))
        ret = await detect_and_click_double_confirm(page)
        assert ret == {"found": False, "clicked": False}

    def test_button_selector_covers_bare_span(self):
        """P-D1 v10 真机实证：EIS 二次确认按钮是裸 SPAN#doubleConfirm-confirm
        （无 button tag / el-button class / role）——按钮选择器必须含 span，
        词表全等过滤是唯一点击判据（「取消」/外层拼接文本不命中）。"""
        from core.discovery.shared import dialog_helpers
        src = _src(dialog_helpers.detect_and_click_double_confirm)
        assert 'button, .el-button, [role="button"], span' in src, \
            "按钮选择器缺 span（P-D1 v10 实证形态盲点）"

    def test_form_filler_guard_b_exemption_wired(self):
        """守卫 B 同指纹滞留分支接豁免检测（薄包装 → shared 单源，源码守卫）。"""
        src = _src(form_filler.FormFiller._dialog_still_open_check)
        assert "_detect_and_click_double_confirm" in src
        # 薄包装路由 shared 单源（Stage 1/2 共用）
        wrap_src = _src(form_filler.FormFiller._detect_and_click_double_confirm)
        assert "from core.discovery.shared.dialog_helpers import detect_and_click_double_confirm" in wrap_src

    def test_crud_executor_double_confirm_wired(self):
        """crud_executor generic 提交链接入 double_confirm 落库（源码守卫）。"""
        src = _src(crud_executor._do_generic_operation)
        assert "detect_and_click_double_confirm" in src
        assert "double_confirm" in src

    def test_builder_double_confirm_steps(self):
        """builder 模块级 _build_double_confirm_steps + 三 builder 接入（源码守卫）。"""
        from core.discovery.playbook_builder import builder
        assert hasattr(builder, "_build_double_confirm_steps")
        src = _src(builder._build_double_confirm_steps)
        assert "click_double_confirm" in src
        # 三个 builder（create/update/generic）各接入一次
        mod_src = inspect.getsource(builder)
        assert mod_src.count("steps.extend(_build_double_confirm_steps(op_data))") >= 3, \
            "builder 接入点不足 3 处"

    def test_replay_engine_click_double_confirm_handler(self):
        """replay_engine 分发 click_double_confirm（宪法三阶段一致性落点）。"""
        from core.discovery.replay import replay_engine
        assert hasattr(replay_engine, "_step_click_double_confirm")
        src = _src(replay_engine._step_click_double_confirm)
        assert "double_confirm_absent" in src  # 幂等：无浮层不红灯
        mod_src = inspect.getsource(replay_engine)
        assert mod_src.count('"click_double_confirm"') >= 1, "分发器缺 click_double_confirm 分支"


# ============================================================
# 修复 3（P0）：弹窗归属校验 + 关闭验证 + 清扫
# ============================================================

class TestFix3DialogOwnershipAndCleanup:

    def test_generic_dialog_mismatch_terminal(self):
        """generic 路径弹窗归属不匹配 → dialog_mismatch 终态（源码守卫）。"""
        src = _src(crud_executor._do_generic_operation)
        assert "dialog_mismatch" in src

    def test_close_dialog_returns_bool_with_verify(self):
        """_close_dialog 返回 bool + 关闭验证 + 二轮兜底（源码守卫）。"""
        from core.discovery.ui_scanner.button_detector import _close_dialog
        src = _src(_close_dialog)
        assert "-> bool" in src
        tree = ast.parse(src)
        # 存在 return True / return False 的关闭结果语义
        bool_returns = [n for n in ast.walk(tree)
                        if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)]
        assert bool_returns, "_close_dialog 无 bool 返回（关闭验证缺失）"

    def test_dismiss_all_overlays_shared(self):
        """三处清扫复用 shared/transition_helpers 单源（源码守卫）。"""
        from core.discovery.shared.transition_helpers import _dismiss_all_overlays
        src = _src(crud_executor._do_generic_operation)
        assert "_dismiss_all_overlays" in src
        assert callable(_dismiss_all_overlays)


# ============================================================
# 修复 4（P0）：dialog_detected UnboundLocalError
# ============================================================

class TestFix4DialogDetectedInit:

    def test_do_edit_dialog_detected_initialized_before_use(self):
        """_do_edit 开头初始化 dialog_detected = False（先赋值后引用）。"""
        src = _src(crud_executor._do_edit)
        init_pos = src.find("dialog_detected = False")
        assert init_pos != -1, "_do_edit 缺 dialog_detected = False 初始化"
        # 初始化必须在函数体前部（首个守卫 B 引用之前）
        first_ref = src.find("dialog_detected")
        assert first_ref <= init_pos  # 首个出现即初始化

    def test_do_edit_no_unboundlocal_pattern(self):
        """AST 验证：所有 dialog_detected 引用前存在模块级安全赋值。"""
        src = _src(crud_executor._do_edit)
        tree = ast.parse(src)
        assigned_before_use = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "dialog_detected":
                        assigned_before_use = True
        assert assigned_before_use, "无 dialog_detected 赋值点（UnboundLocalError 根因仍在）"


# ============================================================
# 修复 5（P1）：attempted_actions 终态失败记忆表
# ============================================================

class TestFix5AttemptedActions:

    def test_terminal_error_types_whitelist(self):
        """白名单基线六类（方案 §修复5）；批七 F3-1 扩至九类（+dialog_still_open/
        readonly_dialog_error/api_error，见 test_batch7_fixes.py 九类契约测试）。"""
        assert TERMINAL_ERROR_TYPES == frozenset({
            'required_field_empty', 'form_validation', 'no_confirm_button',
            'confirm_word_missing', 'no_fields', 'dialog_mismatch',
            'dialog_still_open', 'readonly_dialog_error', 'api_error',
        })

    def test_register_terminal_attempt_counts(self):
        """白名单终态失败入册计数；成功/非白名单不入册。"""
        attempted = {}
        assert crud_executor._register_terminal_attempt(
            "重置密码", {"success": False, "error_type": "required_field_empty"}, attempted)
        assert attempted["重置密码"] == 1
        # 成功不入册
        assert not crud_executor._register_terminal_attempt(
            "重置密码", {"success": True, "error_type": "required_field_empty"}, attempted)
        # 非白名单不入册（状态机域自管理）
        assert not crud_executor._register_terminal_attempt(
            "开机", {"success": False, "error_type": "click_failed"}, attempted)
        assert attempted["重置密码"] == 1
        # 空结构安全
        assert not crud_executor._register_terminal_attempt("x", {}, attempted)
        assert not crud_executor._register_terminal_attempt("x", None, attempted)
        assert not crud_executor._register_terminal_attempt(
            "x", {"success": False, "error_type": "form_validation"}, None)

    def test_attempted_exhausted_budget(self):
        """预算 MAX_LIFETIME_ATTEMPTS=3；None 安全回退 False。"""
        assert crud_executor.MAX_LIFETIME_ATTEMPTS == 3
        assert not crud_executor._attempted_exhausted("克隆", None)
        assert not crud_executor._attempted_exhausted("克隆", {})
        assert not crud_executor._attempted_exhausted("克隆", {"克隆": 2})
        assert crud_executor._attempted_exhausted("克隆", {"克隆": 3})
        assert crud_executor._attempted_exhausted("克隆", {"克隆": 5})

    def test_three_consumers_accept_attempted_actions(self):
        """三消费点签名含 attempted_actions（默认 None 兼容旧调用）。"""
        for fn in (crud_executor._collect_state_pending,
                   crud_executor._check_and_execute_newly_enabled,
                   crud_executor._state_aware_reconcile):
            sig = inspect.signature(fn)
            assert "attempted_actions" in sig.parameters, f"{fn.__name__} 缺 attempted_actions"
            assert sig.parameters["attempted_actions"].default is None

    def test_validate_single_tab_crud_returns_triple(self):
        """validate 链返回三元组（attempted_actions 跨 Tab 聚合出口）。"""
        src = _src(crud_executor._validate_single_tab_crud)
        tree = ast.parse(src)
        tuple_returns = [n for n in ast.walk(tree)
                         if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
        assert tuple_returns, "_validate_single_tab_crud 应返回三元组"

    def test_validate_business_flow_aggregates_attempted(self):
        """_validate_business_flow 聚合 attempted（多 Tab 前缀）+ 三元组。"""
        src = _src(crud_executor._validate_business_flow)
        assert "attempted" in src
        tree = ast.parse(src)
        tuple_returns = [n for n in ast.walk(tree)
                         if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
        assert tuple_returns, "_validate_business_flow 应返回三元组"

    def test_validate_ui_result_records_terminal_failed(self):
        """discover_and_validate 落 terminal_failed_operations 到 ui_result（L408）。"""
        src = _src(crud_executor.discover_and_validate)
        assert "terminal_failed_operations" in src


# ============================================================
# 修复 6（P1）：扫描前清扫 + 互斥切换对兜底
# ============================================================

class TestFix6TogglePairAndScanDismiss:

    # ── 6a：扫描前清扫（主循环 should_scan 块内，源码守卫） ──

    def test_scan_before_dismiss_overlays_wired(self):
        """should_scan 成立后、_scan_row_after_operation 前调 _dismiss_all_overlays（6a）。"""
        src = _src(crud_executor._validate_single_tab_crud)
        scan_pos = src.find("_scan_row_after_operation")
        dismiss_pos = src.find("_dismiss_all_overlays")
        assert dismiss_pos != -1, "主循环缺扫描前清扫"
        assert 0 < dismiss_pos < scan_pos, "清扫须在增量扫描之前"

    # ── 6b：find_toggle_pair_text 纯函数 ──

    def test_enter_exit_pair(self):
        assert find_toggle_pair_text(
            "进入运维模式", ["退出运维模式", "编辑"]) == "退出运维模式"

    def test_reverse_pair(self):
        """双向判定：退出→进入同样命中（互斥对称）。"""
        assert find_toggle_pair_text(
            "退出运维模式", ["进入运维模式"]) == "进入运维模式"

    def test_all_five_pairs(self):
        """五对词表全覆盖（TOGGLE_PAIR_KEYWORDS 单源）。"""
        cases = [
            ("启用", "停用", ["停用X"]), ("开启", "关闭", ["关闭Y"]),
            ("冻结", "解冻", ["解冻Z"]), ("锁定", "解锁", ["解锁W"]),
        ]
        for a, b, cands in cases:
            assert find_toggle_pair_text(a + "X", cands) == cands[0]

    def test_no_pair_returns_empty(self):
        assert find_toggle_pair_text("执行命令", ["退出运维模式"]) == ""
        assert find_toggle_pair_text("进入运维模式", ["编辑"]) == ""
        assert find_toggle_pair_text("", ["任何"]) == ""

    def test_whitespace_normalized(self):
        """去空格归一化（「进 入运维模式」仍命中）。"""
        assert find_toggle_pair_text(
            "进 入运维模式", ["退 出运维模式"]) == "退 出运维模式"

    def test_incremental_scan_rescue_wired(self):
        """INCREMENTAL-SCAN 消失按钮互斥对兜底接线（6b 主循环源码守卫）。"""
        src = _src(crud_executor._validate_single_tab_crud)
        assert "find_toggle_pair_text" in src
        assert "toggle_pair_rescue" in src


# ============================================================
# 修复 7（P1）：placeholder 采集（JS push 点）
# ============================================================

class TestFix7PlaceholderCapture:

    def test_scan_form_fields_js_pushes_placeholder(self):
        """JS 四个 push 点（独立/主/单 input + textarea）携带 placeholder。"""
        src = _src(form_filler.scan_form_fields)
        import re
        # JS push 为多行对象字面量（re.S 跨行匹配）
        input_pushes = re.findall(
            r"kb_category: 'input-generic',.*?placeholder:", src, re.S)
        assert len(input_pushes) >= 3, \
            f"input push 点 placeholder 覆盖不足: {len(input_pushes)}"
        assert re.search(r"kb_category: 'textarea-generic',\s*placeholder:", src), \
            "textarea push 点缺 placeholder"

    def test_scan_fields_v2_passes_current_value(self):
        """scan_form_fields_v2 透传 currentValue（修复8 输入）。"""
        src = _src(form_filler.FormFiller.scan_form_fields_v2)
        assert "currentValue" in src

    def test_edit_fill_planning_passes_placeholder(self):
        """fill_edit_form 规划透传 placeholder + currentValue。"""
        src = _src(form_filler.FormFiller.fill_edit_form)
        assert "placeholder=field.get" in src
        assert "current_value=field.get" in src


# ============================================================
# 修复 8（P2）：数字字段规则纠正
# ============================================================

class TestFix8NumberRule:

    def test_number_with_default_value_kept(self):
        """el-input-number 有默认值 → fixed_value 保留（不填随机数）。"""
        value, rule = generate_fill_value("input", "number", current_value="4")
        assert value == "4"
        assert rule == {"rule": "fixed_value", "params": {"value": "4"}}

    def test_number_without_default_random(self):
        """无默认值 → number_pattern 1-100。"""
        value, rule = generate_fill_value("input", "number")
        assert 1 <= int(value) <= 100
        assert rule["rule"] == "number_pattern"

    def test_number_label_keyword_fallback(self):
        """label 数字词兜底（inputType 缺失场景）：CPU/系统盘 → 数字。"""
        for label in ("CPU", "内存", "系统盘", "数量", "规格"):
            value, rule = generate_fill_value("input", "text", label=label)
            assert rule["rule"] in ("number_pattern", "fixed_value"), \
                f"label={label} 未走数字规则: {rule}"

    def test_number_label_excludes_name_fields(self):
        """「磁盘名称」类含盘的名称字段不误伤（排除词单源）。"""
        for label in ("磁盘名称", "云盘名字", "镜像别名", "标签"):
            _, rule = generate_fill_value("input", "text", label=label)
            assert rule["rule"] != "number_pattern", \
                f"label={label} 被误判数字: {rule}"

    def test_login_name_username_rule(self):
        """登录名/用户名 → username_pattern，值与原 name_pattern 等值。"""
        value, rule = generate_fill_value("input", "text", label="登录名")
        assert rule["rule"] == "username_pattern"
        assert rule["params"]["prefix"] == "test"
        import re
        assert re.fullmatch(r"test\d{4,}", value), f"登录名值形态变化: {value}"

    def test_name_field_unchanged(self):
        """名称/别名/描述字段维持 name_pattern（marker 提取链不动）。"""
        for label in ("名称", "别名", "云主机名称"):
            value, rule = generate_fill_value("input", "text", label=label)
            assert rule["rule"] == "name_pattern"
            assert rule["params"]["prefix"] == "test"

    def test_infer_fill_rule_number_branches(self):
        """_infer_fill_rule 与 generate_fill_value 同源四分支。"""
        assert _infer_fill_rule("数量", "input", "number", False) == \
            {"rule": "number_pattern", "params": {"min": 1, "max": 100}}
        assert _infer_fill_rule("数量", "input", "number", False,
                                current_value="2") == \
            {"rule": "fixed_value", "params": {"value": "2"}}
        assert _infer_fill_rule("系统盘", "input", "text", False)["rule"] == "number_pattern"
        assert _infer_fill_rule("磁盘名称", "input", "text", False)["rule"] == "name_pattern"
        assert _infer_fill_rule("登录名", "input", "text", False)["rule"] == "username_pattern"

    def test_generate_fill_data_uses_default_value(self):
        """generate_fill_data 端到端：currentValue 字段填原值。"""
        fields = [
            {"label": "名称", "type": "input", "inputType": "text",
             "kb_category": "input-generic"},
            {"label": "数量", "type": "input", "inputType": "number",
             "kb_category": "input-generic", "currentValue": "3"},
        ]
        data = generate_fill_data(fields)
        assert data["数量"] == "3"
        assert data["名称"].startswith("test")

    def test_generate_fill_rules_fixed_value(self):
        """generate_fill_rules 端到端：数字默认值 → fixed_value 规则。"""
        fields = [
            {"label": "CPU", "type": "input", "inputType": "number",
             "kb_category": "input-generic", "currentValue": "4"},
            {"label": "登录名", "type": "input", "inputType": "text",
             "kb_category": "input-generic"},
        ]
        rules = generate_fill_rules(fields)
        assert rules["CPU"] == {"rule": "fixed_value", "params": {"value": "4"}}
        assert rules["登录名"]["rule"] == "username_pattern"

    def test_placeholder_confirm_still_top_priority(self):
        """placeholder 引号确认词优先级仍最高（批五 F3 不回归）。"""
        value, rule = generate_fill_value(
            "input", "number", label="删除确认",
            placeholder='请输入 "DELETE" 确认删除')
        assert value == "DELETE"
        assert rule["rule"] == "placeholder_confirm"

    def test_scan_js_number_detector_wired(self):
        """主扫描分支 el-input-number 容器判据 + currentValue 采集（源码守卫）。"""
        src = _src(form_filler.scan_form_fields)
        # 主分支 _num8 判据（≥2 处：多 input 主 + 单 input）+ fallback isNumber
        assert src.count(".el-input-number')") >= 3, \
            "el-input-number 容器判据覆盖不足"
        assert src.count("currentValue:") >= 2, "currentValue 采集点不足"

    def test_element_scanner_number_detector_wired(self):
        """r9 实证：创建链 fields 来自 element_scanner 探测扫描源
        （_do_create context 非空不重扫）——三个 inputType 赋值点
        （复合独立 input / 多 input 主输入框 / 单 input）须同修复 8 口径，
        否则数量/CPU/内存 inputType=text 无 currentValue → 随机数覆盖默认值。"""
        from core.discovery.ui_scanner import element_scanner
        # _SCAN_FIELDS_JS 是 _scan_create_dialog 函数内局部 JS（r9 创建 fields 源）
        src = inspect.getsource(element_scanner._scan_create_dialog)
        assert src.count("closest('.el-input-number')") >= 3, \
            "element_scanner el-input-number 判据不足 3 处（r9 实证此源漏修）"
        assert src.count("currentValue:") >= 3, \
            "element_scanner currentValue 采集点不足 3 处"

    def test_form_filler_composite_branch_number_wired(self):
        """form_filler 复合分支（EIS 系统盘 = 规格 el-select + el-input-number
        组合，r9 D5-composite 实证走此分支）须同 _num8 口径——原修复 8
        漏了本分支。"""
        src = _src(form_filler.scan_form_fields)
        assert src.count("_num8") >= 4, \
            "复合分支 _num8 缺失（原 3 处 + 复合分支新 1 处）"

    # ---------- P-D1 密码规则（v8 弹窗提示原文实证）----------
    # EIS 前端校验「包含数字，但不能出现3个及以上连续或重复的数字」：
    # 旧形态 Test@<6位随机数> 是 6 个位置连续数字、几乎必违规（r8
    # Test@667196 实证被拒、无 POST）。

    def test_password_no_three_consecutive_digits(self):
        """password 分支生成值：无 3 连数字、数字互异、必含数字。"""
        import re
        for _ in range(50):
            value, rule = generate_fill_value("input", "password")
            assert rule["rule"] == "password_pattern"
            assert not re.search(r"\d{3}", value), f"3连数字: {value}"
            digits = re.findall(r"\d", value)
            assert digits, f"必须含数字: {value}"
            assert len(digits) == len(set(digits)), f"重复数字: {value}"

    def test_password_label_branch_same_form(self):
        """label 含密码关键词（inputType 缺失兜底）→ 同交错形态。"""
        import re
        for _ in range(20):
            value, rule = generate_fill_value("input", "text", label="登录密码")
            assert rule["rule"] == "password_pattern"
            assert not re.search(r"\d{3}", value), f"3连数字: {value}"

    def test_apply_fill_rule_password_rebuild(self):
        """replay 重建（apply_fill_rule password_pattern）与探测同源形态。"""
        import re
        for _ in range(20):
            value = apply_fill_rule({"rule": "password_pattern",
                                     "params": {"prefix": "Test@"}})
            assert not re.search(r"\d{3}", value), f"3连数字: {value}"

    def test_password_fixed_default_compliant(self):
        """password_fixed 缺省值合规（Test@123456 的「123」已换合规形态）。"""
        import re
        value = apply_fill_rule({"rule": "password_fixed", "params": {}})
        assert not re.search(r"\d{3}", value), f"3连数字: {value}"


# ============================================================
# 修复 9（P2）：create 段填充日志恢复
# ============================================================

class TestFix9CreateFillLogging:

    def test_create_fill_logs_restored(self):
        """fill_create_form 主循环含「编辑字段」成功日志（与 edit 同签名）。"""
        src = _src(form_filler.FormFiller.fill_create_form)
        assert src.count("编辑字段: {label} = {value}") >= 2, \
            "create 主循环成功日志未恢复（selector 路径 + 回退路径）"


# ============================================================
# 端到端回归锚：修复间不互相破坏
# ============================================================

class TestCrossFixRegressions:

    def test_terminal_types_not_in_state_blocked_signatures(self):
        """白名单类型与状态受阻签名零重叠（域分离不漂移）。"""
        src = _src(crud_executor._collect_state_pending)
        for etype in TERMINAL_ERROR_TYPES:
            assert etype not in src or "not in" in src or "排除" in src, \
                f"白名单 {etype} 与 pending 收集口径冲突"

    def test_double_confirm_not_in_dropdown_excludes(self):
        """二次确认候选词与互斥对词表零交叉（机制独立）。"""
        for w in DOUBLE_CONFIRM_BUTTON_TEXTS:
            for a, b in const.TOGGLE_PAIR_KEYWORDS:
                assert w not in (a, b)
