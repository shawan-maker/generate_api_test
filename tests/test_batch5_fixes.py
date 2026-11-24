# -*- coding: utf-8 -*-
"""test_batch5_fixes.py — 批次五修复单元测试

测试目标（docs/debug/eis云主机批五修改方案.md，F1-F9 全项）：
- F1  级联仅成功入册（executed_actions.add 只在 success 分支内）
- F2  marker 锁行收集（_discover_dropdowns marker 参数 + find_data_row 搜索先行）
- F3  placeholder 确认词值生成 + apply_fill_rule placeholder_confirm 重建
- F4  逐行名称链接归并（_merge_row_link_actions）+ row_link 执行路由 +
       builder click_row_name_link 步骤 + fallback 回流拦截
- F5  _do_edit dropdown 路由（源码守卫）+ _collect_state_pending update 角色放宽
- F6  无消息 + URL 变 → navigation_url_change 成功；消息优先防误判
- F7  级联批量执行前稳态等待（源码守卫）
- F8  断言判定梯四规则（文本赢 / className 3a / 双中性等待默认成功）
       + 词表扩充 + 词表单源注入（message_capture 占位符）
- F9  dialog_context 不进判定通道 + verify 层 dialog 来源过滤

实证背景（r7 第 33 操作窗 + tmp/eis_r7_fail_evidence.json）：
- 删除失败 = 确认输入框被填随机值（placeholder 写着 "DELETE"）；
- 10 个 navigation 秒败 = 名称链接逐行条目按文本定位到他人 VM 行；
- 9 个 skipped 未进 reconcile = pending 角色只放 generic；
- 「已进入运维模式」success 样式类被文本词表判 info = 分类忽略 className。
"""
import ast
import inspect

import pytest

from core.discovery import const
from core.discovery.operation_executor import crud_executor
from core.discovery.playbook_builder import builder
from core.discovery.shared.fill_helpers import (
    generate_fill_value, apply_fill_rule,
)
from core.discovery.shared.success_verifier import verify_operation_success


# ============================================================
# 测试基建
# ============================================================

class VerifyPage:
    """verify_operation_success 最小 Page 替身。

    evaluate 按 JS 特征路由：
    - 含 window.__captured_messages → 返回 recheck_captured
      （仅中性等待复查 1 调用；策略 2.5 JS 分支只在未传 captured_messages
      时走，测试均传参避开）
    - 其余 → None（无可见消息/无结构化失败信号）
    """

    def __init__(self, url, recheck_captured=None):
        self.url = url
        self.recheck_captured = recheck_captured if recheck_captured is not None else []
        self.waits = []

    async def evaluate(self, script, *args):
        s = script if isinstance(script, str) else ""
        if "window.__captured_messages" in s:
            return self.recheck_captured
        return None

    async def wait_for_timeout(self, ms):
        self.waits.append(ms)


def _src(func) -> str:
    return inspect.getsource(func)


# ============================================================
# F1 级联仅成功入册
# ============================================================

def _ast_add_calls_in_success_branch(func_src: str) -> list:
    """AST 定位 executed_actions.add 调用及其所在 if 分支条件。

    返回 [(cond_dump, in_success_branch), ...]——in_success_branch 表示
    该 add 调用的任一祖先 If 节点条件含 success 字样。
    """
    tree = ast.parse(func_src)
    hits = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.if_stack = []

        def visit_If(self, node):
            self.if_stack.append(ast.dump(node.test))
            self.generic_visit(node)
            self.if_stack.pop()

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "executed_actions"):
                in_success = any("success" in cond for cond in self.if_stack)
                hits.append(in_success)
            self.generic_visit(node)

    Visitor().visit(tree)
    return hits


class TestF1CascadeOnlySuccessRegisters:
    """executed_actions.add 只能出现在成功判定的分支内（F1 语义分离）。"""

    def test_cascade_add_inside_success_branch(self):
        src = _src(crud_executor._check_and_execute_newly_enabled)
        hits = _ast_add_calls_in_success_branch(src)
        assert hits, "级联函数内未找到 executed_actions.add 调用"
        assert all(hits), (
            "executed_actions.add 必须全部位于 success 条件分支内——"
            "批五 F1 语义：级联失败的操作不得入册（否则被 reconcile 永久排除）"
        )


# ============================================================
# F2 marker 锁行收集（源码守卫）
# ============================================================

class TestF2MarkerLockedCollection:

    def test_discover_dropdowns_has_marker_param(self):
        from core.discovery.ui_scanner.element_scanner import _discover_dropdowns
        sig = inspect.signature(_discover_dropdowns)
        assert "marker" in sig.parameters, (
            "_discover_dropdowns 必须支持 marker 锁行参数（F2-1：create 成功后"
            "按 marker 行重收集，防多用户环境收集到他人 VM 行）"
        )

    def test_recollect_passes_created_marker(self):
        src = _src(crud_executor._rediscover_marker_dropdowns)
        assert "marker=created_marker" in src, (
            "marker 锁行重收集调用必须传 created_marker（F2-1）"
        )

    def test_find_data_row_search_first(self):
        from core.discovery.replay.button_driver import ButtonDriver
        src = _src(ButtonDriver.find_data_row)
        assert "search_filter_table" in src, (
            "find_data_row 必须搜索先行（F2-3：多用户环境行序漂移，"
            "先按 marker 搜索过滤再定位）"
        )
        assert (src.index("search_filter_table")
                < src.index("_find_row_on_current_page")), (
            "搜索过滤必须先于全页扫描定位（F2-3 主路径序）"
        )


# ============================================================
# F3 placeholder 确认词
# ============================================================

class TestF3PlaceholderConfirm:

    def test_value_generation_quoted_word(self):
        """placeholder 含引号包裹确认词 → 填该词（r7b 实证：填 DELETE 而非随机值）。"""
        value, rule = generate_fill_value(
            "input", "text", label="删除确认",
            placeholder='请输入 "DELETE" 确认删除')
        assert value == "DELETE"
        assert rule["rule"] == "placeholder_confirm"
        assert "DELETE" in rule["params"]["placeholder_hint"]

    def test_value_generation_chinese_quotes(self):
        """中文引号包裹确认词同样命中（const 单源正则兼容）。"""
        value, _ = generate_fill_value(
            "input", "text", label="删除确认",
            placeholder="请输入 “DELETE” 确认删除")
        assert value == "DELETE"

    def test_value_generation_no_quote_unchanged(self):
        """无引号包裹词的 placeholder 不触发确认词规则（防误伤普通字段）。"""
        value, rule = generate_fill_value(
            "input", "text", label="名称", placeholder="请输入名称")
        assert rule["rule"] != "placeholder_confirm"
        assert value != "DELETE"

    def test_apply_fill_rule_rebuilds_from_hint(self):
        """Stage 2 回放：placeholder_confirm 规则从 hint 重建同一词（三阶段一致）。"""
        rule = {
            "rule": "placeholder_confirm",
            "params": {"placeholder_hint": '请输入 "DELETE" 确认删除'},
        }
        # 契约：apply_fill_rule(rule, timestamp=None)，无 label/fields 形参
        value = apply_fill_rule(rule)
        assert value == "DELETE"
        # hint 丢失时返回空串由调用方兜底（不再落 name_pattern 随机值）
        assert apply_fill_rule({"rule": "placeholder_confirm", "params": {}}) == ""

    def test_const_regex_single_source(self):
        """const 单源正则存在且可提取。"""
        assert const.extract_placeholder_confirm_word('输入 "YES"') == "YES"
        # 契约：未命中返回空串（docstring 明示），非 None
        assert const.extract_placeholder_confirm_word("无引号") == ""
        assert const.extract_placeholder_confirm_word("") == ""


# ============================================================
# F4 逐行名称链接归并
# ============================================================

def _make_span_btn(text, tab="云主机", cls="table-span-btn"):
    return {
        "text": text, "tag": "SPAN", "className": cls,
        "location": "row_action", "action": text, "tab": tab,
        "source": "main",
    }


class TestF4MergeRowLinkActions:
    """_merge_row_link_actions 归并判据（纯函数，r7 实证 10 条 → 1 条）。"""

    def test_merge_basic(self):
        buttons_by_tab = {"云主机": {
            "wt-a": _make_span_btn("wt-a"),
            "wt-b": _make_span_btn("wt-b"),
            "wt-c": _make_span_btn("wt-c"),
            "编辑": {"text": "编辑", "tag": "BUTTON", "className": "el-button",
                    "location": "row_action", "action": "编辑", "tab": "云主机"},
        }}
        ui_result = {"row_actions": [
            _make_span_btn("wt-a"), _make_span_btn("wt-b"), _make_span_btn("wt-c"),
            {"text": "更多", "tag": "SPAN", "className": "el-dropdown-link",
             "location": "row_action", "action": "更多", "tab": "云主机"},
            {"text": "编辑", "tag": "BUTTON", "className": "el-button",
             "location": "row_action", "action": "编辑", "tab": "云主机"},
        ]}
        n = crud_executor._merge_row_link_actions(buttons_by_tab, ui_result)
        assert n == 3
        merged = buttons_by_tab["云主机"]["查看详情（名称链接）"]
        assert merged["row_link"] is True
        assert merged["row_link_class"] == "table-span-btn"
        assert merged["location"] == "row_action"
        assert sorted(merged["row_link_samples"]) == ["wt-a", "wt-b", "wt-c"]
        # BUTTON 行操作与「更多」触发器不动
        assert "编辑" in buttons_by_tab["云主机"]
        # ui_result 同步：逐行条目移除，合成单条入册，「更多」保留
        ra_texts = [b["text"] for b in ui_result["row_actions"]]
        assert "wt-a" not in ra_texts and "wt-b" not in ra_texts
        assert "查看详情（名称链接）" in ra_texts
        assert "更多" in ra_texts and "编辑" in ra_texts

    def test_merge_zero_change_for_button_rows(self):
        """estack 形态（BUTTON 型行操作）零变化（幂等防回归）。"""
        buttons_by_tab = {"用户管理": {
            "编辑": {"text": "编辑", "tag": "BUTTON", "location": "row_action",
                    "action": "编辑", "tab": "用户管理"},
            "删除": {"text": "删除", "tag": "BUTTON", "location": "row_action",
                    "action": "删除", "tab": "用户管理"},
        }}
        ui_result = {"row_actions": [
            {"text": "编辑", "tag": "BUTTON", "location": "row_action"},
            {"text": "删除", "tag": "BUTTON", "location": "row_action"},
        ]}
        before = {k: dict(v) for k, v in buttons_by_tab["用户管理"].items()}
        n = crud_executor._merge_row_link_actions(buttons_by_tab, ui_result)
        assert n == 0
        assert buttons_by_tab["用户管理"] == before

    def test_merge_single_link_not_merged(self):
        """单条 SPAN 链接（每行同文本链接按钮按 action 去重后的形态）不归并。"""
        buttons_by_tab = {"云主机": {"详情": _make_span_btn("详情")}}
        ui_result = {"row_actions": [_make_span_btn("详情")]}
        n = crud_executor._merge_row_link_actions(buttons_by_tab, ui_result)
        assert n == 0
        assert "详情" in buttons_by_tab["云主机"]

    def test_merge_same_text_rows_not_merged(self):
        """同文本多条（非逐行名称形态）保守不归并。"""
        buttons_by_tab = {"云主机": {"详情": _make_span_btn("详情")}}
        ui_result = {"row_actions": [_make_span_btn("详情"), _make_span_btn("详情")]}
        n = crud_executor._merge_row_link_actions(buttons_by_tab, ui_result)
        assert n == 0

    def test_merge_toolbar_untouched(self):
        """toolbar 通用按钮永不参与归并（归并判据限 row_action 位置）。"""
        buttons_by_tab = {"云主机": {
            "wt-a": _make_span_btn("wt-a"),
            "wt-b": _make_span_btn("wt-b"),
            "批量操作": {"text": "批量操作", "tag": "BUTTON",
                       "location": "toolbar", "action": "批量操作",
                       "tab": "云主机"},
        }}
        ui_result = {"row_actions": [
            _make_span_btn("wt-a"), _make_span_btn("wt-b"),
        ], "toolbar_buttons": [
            {"text": "批量操作", "tag": "BUTTON", "location": "toolbar"},
        ]}
        crud_executor._merge_row_link_actions(buttons_by_tab, ui_result)
        assert "批量操作" in buttons_by_tab["云主机"]


class TestF4RowLinkExecutionRoute:
    """row_link 执行路由与步骤生成（三阶段一致）。"""

    def test_navigation_op_has_row_link_branch(self):
        src = _src(crud_executor._do_navigation_operation)
        assert 'btn.get("row_link")' in src, (
            "navigation 执行必须有 row_link 专属分支（语义操作名页面上不存在，"
            "不能按文本点击）"
        )
        assert "click_row_name_link" in src, "row_link 分支必须走行内名称链接点击"

    def test_button_driver_has_name_link_helper(self):
        from core.discovery.replay.button_driver import ButtonDriver
        assert hasattr(ButtonDriver, "click_row_name_link"), (
            "ButtonDriver 必须提供 click_row_name_link 助手（F4-3）"
        )
        sig = inspect.signature(ButtonDriver.click_row_name_link)
        assert "link_class" in sig.parameters

    def test_build_page_nav_steps_row_link(self):
        """builder 生成 find_row → click_row_name_link → wait_for_url 序列。"""
        op_data = {
            "success": True,
            "row_link": True,
            "row_link_class": "table-span-btn",
            "trigger_text": "查看详情（名称链接）",
            "selectors": {
                "trigger": "查看详情（名称链接）",
                "row_selector": "row_name_link",
                "row_link_class": "table-span-btn",
            },
            "nav_info": {"scenario": "page_nav", "navigated_url":
                         "https://console.example.com/vmDetail?id=1"},
        }
        steps = builder._build_page_nav_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "click_row_name_link" in actions, (
            "row_link 操作必须生成 click_row_name_link 步骤（不能按文本生成 "
            "click_button/click_row_button——语义名页面上不存在）"
        )
        i = actions.index("click_row_name_link")
        assert steps[i]["link_class"] == "table-span-btn"
        assert "find_row" in actions[:i], "行定位必须先于名称链接点击"
        assert "wait_for_url" in actions, "跳转类操作必须有 URL 等待验证"

    def test_builder_fallback_skips_row_link(self):
        """builder fallback 通道拦截 row_link 合成条目（防逐行条目回流 playbook）。"""
        src = _src(builder.build_playbook)
        assert 'btn.get("row_link")' in src, (
            "fallback 循环必须按 row_link 标记跳过——否则合成条目被生成"
            "click_button 文本步骤（必然失败）"
        )

    def test_replay_engine_has_name_link_step(self):
        from core.discovery.replay.replay_engine import (
            _step_click_row_name_link,
        )
        src = _src(_step_click_row_name_link)
        assert "click_row_name_link" in src
        # 分发链注册
        engine_src = _src(
            __import__("core.discovery.replay.replay_engine",
                       fromlist=["replay_from_playbook"]).replay_from_playbook
        ) if False else None
        import core.discovery.replay.replay_engine as re_mod
        full_src = inspect.getsource(re_mod)
        assert 'action == "click_row_name_link"' in full_src, (
            "replay 分发链必须注册 click_row_name_link 步骤类型"
        )


# ============================================================
# F5 _do_edit dropdown 路由 + pending 角色放宽
# ============================================================

class TestF5EditDropdownRoute:

    def test_edit_has_dropdown_branch(self):
        src = _src(crud_executor._do_edit)
        assert 'elif btn_location == "dropdown" and marker:' in src, (
            "_do_edit 必须有 dropdown 分支（EIS 修改Hostname 此前落入 else "
            "全局 click_page_button 必败）"
        )
        assert "click_row_more_item" in src, "dropdown 分支必须走「更多」菜单点击"
        assert "button-disabled" in src, "dropdown 分支必须处理 disabled → skipped"

    def test_edit_dropdown_no_marker_skip(self):
        src = _src(crud_executor._do_edit)
        assert 'btn_location == "dropdown" and not marker' in src, (
            "dropdown 编辑无 marker 必须显式 skipped（不落全局点击空耗）"
        )


class TestF5PendingUpdateRole:

    def _pending(self, roles, location="dropdown", error_type="skipped",
                 error_text="按钮被禁用: 修改Hostname"):
        validated = {"修改Hostname": {
            "success": False, "error_type": error_type, "error_text": error_text,
        }}
        buttons = {"修改Hostname": {
            "text": "修改Hostname", "location": location,
        }}
        executed = set()
        return crud_executor._collect_state_pending(
            validated, buttons, roles, executed)

    def test_update_role_row_level_recycled(self):
        """F5-2：update 角色 + 行级位置 + 状态受阻签名 → 进 reconcile pending。"""
        pending = self._pending({"修改Hostname": "update"})
        assert "修改Hostname" in pending, (
            "update 角色的行级操作必须能进 pending（r7 实证修改Hostname "
            "skipped 后被 generic-only 条件吞掉，永无 reconcile 恢复机会）"
        )

    def test_update_role_toolbar_not_recycled(self):
        """toolbar 位置的 update 操作不回收（位置约束保留）。"""
        pending = self._pending({"修改Hostname": "update"}, location="toolbar")
        assert "修改Hostname" not in pending

    def test_generic_role_unchanged(self):
        """generic 角色既有行为不变。"""
        pending = self._pending({"重装操作系统": "generic"},
                                error_text="按钮被禁用: 重装操作系统")
        assert pending

    def test_update_role_non_state_blocked_not_recycled(self):
        """非状态受阻签名（如 form_validation）不回收（既有口径）。"""
        pending = self._pending({"修改Hostname": "update"},
                                error_type="form_validation", error_text="校验失败")
        assert "修改Hostname" not in pending

    def test_update_role_click_failed_recycled(self):
        """过渡窗口 click_failed 属状态受阻签名 → 进 pending。"""
        pending = self._pending({"修改Hostname": "update"},
                                error_type="click_failed", error_text="过渡窗口失败")
        assert "修改Hostname" in pending


# ============================================================
# F6 URL 跳转判成功（navigation_url_change 层）
# ============================================================

class TestF6UrlChangeSuccess:

    @pytest.mark.asyncio
    async def test_no_message_url_changed_success(self):
        """无消息 + URL 变 → navigation_url_change 成功。"""
        page = VerifyPage(url="https://console.example.com/vmDetail?id=1")
        ok, detail = await verify_operation_success(
            page, "查看详情（名称链接）", mode="message",
            captured_messages=[], url_before="https://console.example.com/list")
        assert ok is True
        assert detail["matched_method"] == "navigation_url_change"

    @pytest.mark.asyncio
    async def test_no_message_url_unchanged_no_signal(self):
        """无消息 + URL 不变 + 非查询 → no_signal 失败（规则 4 保留）。"""
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "任意操作", mode="message",
            captured_messages=[], url_before="https://console.example.com/list")
        assert ok is False
        assert detail["failure_type"] == "no_signal"

    @pytest.mark.asyncio
    async def test_failure_message_wins_over_url_change(self):
        """失败消息 + URL 变 → 失败（消息永远优先于 URL 判定）。"""
        page = VerifyPage(url="https://console.example.com/vmDetail?id=1")
        ok, detail = await verify_operation_success(
            page, "任意操作", mode="message",
            captured_messages=[{"text": "操作失败：资源不足", "className": ""}],
            url_before="https://console.example.com/list")
        assert ok is False
        assert detail["failure_type"] == "captured"

    @pytest.mark.asyncio
    async def test_read_complete_unchanged(self):
        """read_complete 无失败 → 成功（查询类既有口径不受 F6 影响）。"""
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "查询", mode="read_complete",
            captured_messages=[], url_before="https://console.example.com/list")
        assert ok is True
        assert detail["matched_method"] == "read_complete:no_failure"


# ============================================================
# F7 级联前稳态等待（源码守卫）
# ============================================================

class TestF7StableStateWait:

    def test_cascade_waits_row_states_stable(self):
        src = _src(crud_executor._check_and_execute_newly_enabled)
        assert "_wait_row_states_stable" in src, (
            "级联批量执行前必须调用 _wait_row_states_stable"
            "（r7 证据 I：关机✓后 Stopping 过渡窗 10 项连败）"
        )
        assert "timed_out" in src, (
            "稳态等待 timed_out 必须放弃本轮级联执行（defer 给 reconcile）"
        )


# ============================================================
# F8 断言判定梯四规则
# ============================================================

class TestF8ClassifyLadder:

    def test_rule12_text_failure_wins_over_success_class(self):
        """规则 2：文本失败词 + className success → 失败（文本赢）。"""
        assert const.classify_notification("操作失败", "el-message--success") == "failure"

    def test_rule12_text_success_wins_over_error_class(self):
        """规则 1：文本成功词 + className error → 成功（文本赢）。"""
        assert const.classify_notification("操作成功", "el-message--error") == "success"

    def test_rule3a_neutral_text_success_class(self):
        """规则 3a：文本中性 + className success → 成功（「已进入运维模式」用例）。"""
        assert const.classify_notification(
            "已进入运维模式", "el-message--success") == "success"

    def test_rule3a_neutral_text_error_class(self):
        """规则 3a：文本中性 + className error → 失败。"""
        assert const.classify_notification(
            "任务队列已受理", "el-message--error") == "failure"

    def test_rule3b_double_neutral(self):
        """双中性 → neutral（由 verify 层等待复查后默认成功）。"""
        assert const.classify_notification("已受理，请稍候查看", "") == "neutral"

    def test_keyword_expansion(self):
        """完成态措辞入表（F8 词表扩充）。"""
        for kw in ("已进入", "已退出", "已开启", "已关闭", "已停止",
                   "已启动", "已切换", "已挂载", "已卸载", "已绑定",
                   "已解绑", "已下发"):
            assert kw in const.NOTIFICATION_SUCCESS_KEYWORDS, f"词表缺完成态措辞: {kw}"

    def test_wordlist_single_source_injection(self):
        """词表单源：message_capture JS 由占位符构建时注入（不双源漂移）。"""
        import core.discovery.shared.message_capture as mc
        src = _src(mc)
        assert ("__SUCCESS_KEYWORDS_JSON__" in src
                or "NOTIFICATION_SUCCESS_KEYWORDS" in src), (
            "message_capture 的 JS 成功词表必须由 const 单源注入"
            "（此前 JS 硬编码 5 词 vs const 12 词语义漂移）"
        )


class TestF8NeutralWaitDefault:
    """规则 3b：双中性 → 等待复查 → 仍无 → 默认成功。"""

    @pytest.mark.asyncio
    async def test_neutral_wait_default_success(self, monkeypatch):
        monkeypatch.setattr(const, "NEUTRAL_RECHECK_WAIT_MS", 0)
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "进入运维模式", mode="message",
            captured_messages=[{"text": "已受理，请稍候查看", "className": ""}],
            url_before="https://console.example.com/list")
        assert ok is True
        assert detail["matched_method"] == "neutral_wait_default_success"
        assert 0 in page.waits, "等待窗必须实际等待（NEUTRAL_RECHECK_WAIT_MS）"

    @pytest.mark.asyncio
    async def test_neutral_wait_recheck_failure_wins(self, monkeypatch):
        """复查窗内出现失败消息 → 按失败判（迟到错误不被默认成功掩盖）。"""
        monkeypatch.setattr(const, "NEUTRAL_RECHECK_WAIT_MS", 0)
        page = VerifyPage(
            url="https://console.example.com/list",
            recheck_captured=[{"text": "进入运维模式失败：资源不足",
                               "className": ""}])
        ok, detail = await verify_operation_success(
            page, "进入运维模式", mode="message",
            captured_messages=[{"text": "已受理，请稍候查看", "className": ""}],
            url_before="https://console.example.com/list")
        assert ok is False
        assert detail["failure_type"] == "neutral_wait"

    @pytest.mark.asyncio
    async def test_processing_only_success(self, monkeypatch):
        """仅处理中消息 → processing_only 成功（既有口径保留）。"""
        monkeypatch.setattr(const, "NEUTRAL_RECHECK_WAIT_MS", 0)
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "批量操作", mode="message",
            captured_messages=[{"text": "正在处理，请稍候", "className": ""}],
            url_before="https://console.example.com/list")
        assert ok is True
        assert detail["matched_method"] == "processing_only"


# ============================================================
# F9 dialog_context 不进判定通道
# ============================================================

class TestF9DialogContextExcluded:

    @pytest.mark.asyncio
    async def test_dialog_context_not_failure_signal(self):
        """弹窗正文警示文（type=dialog_context）不判失败——按规则 4 no_signal。"""
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "删除", mode="message",
            captured_messages=[{
                "text": "删除后其关联的快照、备份资源也将被同步删除，数据无法恢复",
                "type": "dialog_context", "source": "dialog",
            }],
            url_before="https://console.example.com/list")
        assert ok is False
        assert detail["failure_type"] == "no_signal", (
            "dialog_context 不能进入判定通道——若此处报 failure_type=captured "
            "则 F9 过滤失效，前序弹窗滞留会污染后续操作判定（证据 D 第三环）"
        )

    @pytest.mark.asyncio
    async def test_real_toast_still_judged(self):
        """对照组：同文本真 toast（非 dialog 来源）仍正常判失败。"""
        page = VerifyPage(url="https://console.example.com/list")
        ok, detail = await verify_operation_success(
            page, "删除", mode="message",
            captured_messages=[{
                "text": "删除失败：数据无法恢复",
                "type": "error", "source": "toast",
            }],
            url_before="https://console.example.com/list")
        assert ok is False
        assert detail["failure_type"] == "captured"

    def test_message_capture_marks_dialog_context(self):
        """message_capture JS 对 dialog 来源消息标 type='dialog_context'。"""
        import core.discovery.shared.message_capture as mc
        src = _src(mc)
        assert "dialog_context" in src, (
            "message_capture 必须把 dialog 来源消息标为 dialog_context"
            "（与 toast 判定通道分离）"
        )
