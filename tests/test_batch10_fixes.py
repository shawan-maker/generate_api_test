# -*- coding: utf-8 -*-
"""
test_batch10_fixes.py — 批十-A（r12 验收 P1/P2缺口①/P7 源头修复）单元测试

方案：docs/debug/eis云主机批十源头方案.md（§A 批：F1/F2/F9）

F1 STATE-CHANGE 新菜单项通道（P1 缺口①主修）
  - r12 定案：diff 条件 `text in button_states and not button_states[text]`
    机制性排除新出现菜单项——「退出运维模式」运维态首现后被首轮
    button_states.update 入册为 True，从此永无 False→True 翻转可检，
    9+ 轮级联扫描每轮读到 enabled 每轮跳过（8.7 分钟黄金窗内零收集）。
    修复：双通道 diff（newly_enabled 翻转 + newly_discovered 首现），
    新发现项检出即入历史基线（递归不重检出、稳态门 timed_out 不丢单），
    经 _register_new_buttons 单源 helper 入册后与 newly_enabled 同链执行
    （newly_discovered 排尾——B 侧先让 A 侧解锁项执行完再翻转回去）。

F2 切换对定向扫描 [PAIR-SCAN]（P1 缺口②主修）
  - r12 定案：should_scan 是 main per-op 钩子、级联递归内不触发，黄金收集
    窗内 zero should_scan；扫描被级联批推到 __vue__ 失效窗（has_visible_
    menu=False 漏收）。修复：切换对侧（is_toggle_pair_side 前缀单源）成功
    的瞬间 __vue__ 必新鲜，主循环 _record_state_transition 后、级联批前
    触发 marker 锁行定向重收集（黄金窗保证，r11 成功路径的机制化）。

F9 submitB 双修（P7 升级定案）
  - 监听判据 '/estack/api' in url 写死 estack 域，EIS API 为 /api/eis/
    → 监听器对 EIS 全盲，「捕获 0 请求」恒真。改 resource_type in
    ('xhr','fetch') + 写类方法过滤（request/response 双 handler 同口径）。
  - 重试判据收窄：真提交已生效（URL 已变 or 成功消息已捕获）时网络计数
    0 属监听窗误判，盲重试会在列表页盲点 primary 按钮产残留弹窗
    （09:28:01 链定案）。仅真静默失败（URL 未变 + 无成功消息）才允许
    JS click 重试。
"""
import inspect
import logging

import pytest

from core.discovery import const as const_mod
from core.discovery.operation_executor import crud_executor as ce
from core.discovery.operation_executor import navigation_executor as ne


def _src(fn):
    return inspect.getsource(fn)


# ============================================================
# 测试基建（同 test_batch9_fixes.py）
# ============================================================

class FakePage:
    """最小 Playwright Page 替身：仅支持 wait_for_timeout 计数。"""

    def __init__(self):
        self.sleeps = 0

    async def wait_for_timeout(self, ms):
        self.sleeps += 1


class ScriptedScan:
    """_scan_all_button_states 序列替身（越界重复最后一个）。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self, page, created_marker=None):
        idx = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        return self.seq[idx]


def make_btn(location="dropdown", text=""):
    return {"location": location, "text": text, "locator": "button:has-text('x')"}


def patch_batch(monkeypatch, scan_seq, results_by_action):
    """_check_and_execute_newly_enabled 打桩：扫描序列 + 执行脚本。

    返回 (scan, calls)：calls 记录批循环实际执行过的 action 序。
    """
    scan = ScriptedScan(scan_seq)
    monkeypatch.setattr(ce, "_scan_all_button_states", scan)

    async def fake_settle(page, created_marker, state_ctx=None, **kw):
        return {"states": dict(scan_seq[0]), "status": "Running",
                "timed_out": False}

    monkeypatch.setattr(ce, "_wait_row_states_stable", fake_settle)

    async def fake_install(page):
        pass

    monkeypatch.setattr(ce, "_install_message_capture", fake_install)

    async def fake_preflight(page, btn, marker, snapshot=None, tab_prefix=""):
        return {"pass": True, "wait_reason": "", "detail": ""}

    monkeypatch.setattr(ce, "_preflight_executable", fake_preflight)

    calls = []

    async def fake_retry(page, fn, context):
        action = context["action"]
        script = results_by_action.get(action) or [
            {"success": False, "error_text": "无脚本"}]
        idx = calls.count(action)
        calls.append(action)
        return script[min(idx, len(script) - 1)]

    monkeypatch.setattr(ce, "_error_driven_retry", fake_retry)

    async def fake_dismiss(page):
        pass

    monkeypatch.setattr(
        "core.discovery.shared.transition_helpers._dismiss_all_overlays",
        fake_dismiss)
    return scan, calls


# ============================================================
# F1 const 单源谓词
# ============================================================

class TestBatch10F1ConstPredicates:

    def test_is_known_ui_noise_exact_match(self):
        """噪声判定精确全等：'确定'/'取消' 命中；'取消挂载' 业务项不误杀。"""
        assert const_mod.is_known_ui_noise("确定")
        assert const_mod.is_known_ui_noise("取消")
        assert const_mod.is_known_ui_noise("确认")
        assert const_mod.is_known_ui_noise("知道了")
        # 小写英文按 lower() 归一
        assert const_mod.is_known_ui_noise("OK")
        assert const_mod.is_known_ui_noise("Cancel")
        # 归一化（去空白）
        assert const_mod.is_known_ui_noise("  确定  ")
        # 前缀/包含不命中——「取消挂载」「确定挂载」类业务项不得误杀
        assert not const_mod.is_known_ui_noise("取消挂载")
        assert not const_mod.is_known_ui_noise("确定并保存")
        assert not const_mod.is_known_ui_noise("")
        assert not const_mod.is_known_ui_noise(None)

    def test_is_toggle_pair_side_prefix(self):
        """切换对侧判定：词表五对任一侧前缀命中；非切换对不命中。"""
        assert const_mod.is_toggle_pair_side("进入运维模式")
        assert const_mod.is_toggle_pair_side("退出运维模式")
        assert const_mod.is_toggle_pair_side("冻结")
        assert const_mod.is_toggle_pair_side("解冻")
        assert const_mod.is_toggle_pair_side("锁定该实例")
        assert const_mod.is_toggle_pair_side("解锁")
        assert const_mod.is_toggle_pair_side("启用用户")
        assert const_mod.is_toggle_pair_side("停用")
        assert const_mod.is_toggle_pair_side("开启")
        assert const_mod.is_toggle_pair_side("关闭防火墙")
        # 非切换对（r12 级联主体操作）不命中
        assert not const_mod.is_toggle_pair_side("重启")
        assert not const_mod.is_toggle_pair_side("开机")
        assert not const_mod.is_toggle_pair_side("关机")
        assert not const_mod.is_toggle_pair_side("暂停")
        assert not const_mod.is_toggle_pair_side("删除")
        assert not const_mod.is_toggle_pair_side("")
        assert not const_mod.is_toggle_pair_side(None)

    def test_noise_table_single_source_shape(self):
        """噪声词表形状守卫：元组 + 全小写英文 + 含 r11/r12 实证项。"""
        assert isinstance(const_mod.KNOWN_UI_NOISE, tuple)
        for w in const_mod.KNOWN_UI_NOISE:
            assert isinstance(w, str) and w == w.lower(), f"词表须全小写: {w}"
        # r11/r12 实证常驻基线项必须在册
        assert "取消" in const_mod.KNOWN_UI_NOISE
        assert "确定" in const_mod.KNOWN_UI_NOISE


# ============================================================
# F1 _register_new_buttons 单源 helper（单元）
# ============================================================

class TestBatch10F1RegisterHelper:

    def _reg(self, new_buttons, buttons_by_action, ui_result, action_roles,
             ordered_actions, deferred_destroy, executed_actions,
             tab_name="云主机", trigger_action="进入运维模式",
             tab_prefix="[t] ", delete_actions=None):
        return ce._register_new_buttons(
            new_buttons, buttons_by_action, ui_result, action_roles,
            ordered_actions, deferred_destroy, executed_actions,
            tab_name, trigger_action, tab_prefix,
            delete_actions=delete_actions,
        )

    def test_register_insert_after_trigger(self):
        """常规项：注册 buttons_by_action + 角色 + 插 trigger_action 之后。"""
        buttons, roles, ordered = {}, {}, ["创建", "进入运维模式", "重启"]
        ui_result, deferred = {"dropdowns": []}, []
        registered = self._reg(
            [{"text": "退出运维模式", "tag": "DROPDOWN_ITEM",
              "location": "dropdown",
              "selector": '.el-dropdown-menu__item:has-text("退出运维模式")'}],
            buttons, ui_result, roles, ordered, deferred, set())
        assert registered == ["退出运维模式"]
        assert "退出运维模式" in buttons
        assert buttons["退出运维模式"]["tab"] == "云主机"
        assert roles["退出运维模式"] == "generic"
        # 插到 trigger_action 紧邻之后
        assert ordered == ["创建", "进入运维模式", "退出运维模式", "重启"]
        # dropdown 项回写 ui_result.dropdowns（[WRITEBACK]，Stage 2 回放数据）
        assert any(d["text"] == "退出运维模式" for d in ui_result["dropdowns"])

    def test_register_delete_role_defers(self):
        """delete 角色：入 deferred_destroy 不入主序（批九 G1 口径）。"""
        buttons, roles, ordered = {}, {}, ["创建", "进入运维模式"]
        deferred = []
        ui_result = {"dropdowns": []}
        self._reg(
            [{"text": "删除", "tag": "DROPDOWN_ITEM",
              "location": "dropdown",
              "selector": '.el-dropdown-menu__item:has-text("删除")'}],
            buttons, ui_result, roles, ordered, deferred, set(),
            delete_actions=["删除"])
        assert roles["删除"] == "delete"
        assert deferred == ["删除"]
        assert "删除" not in ordered

    def test_register_append_when_trigger_absent(self):
        """trigger_action 不在册（锚缺失）→ append 末尾。"""
        buttons, roles, ordered = {}, {}, ["创建", "重启"]
        self._reg(
            [{"text": "退出运维模式", "location": "dropdown",
              "tag": "DROPDOWN_ITEM"}],
            buttons, {"dropdowns": []}, roles, ordered, [], set(),
            trigger_action="不存在的操作")
        assert ordered == ["创建", "重启", "退出运维模式"]

    def test_register_dedup_and_executed_skip(self):
        """已在 buttons_by_action → 跳过；已执行项仅注册册不入主序。"""
        buttons = {"已有项": make_btn()}
        roles, ordered = {}, ["进入运维模式"]
        ui_result, deferred = {"dropdowns": []}, []
        registered = self._reg(
            [{"text": "已有项", "location": "dropdown"},
             {"text": "级联已执行项", "location": "dropdown"}],
            buttons, ui_result, roles, ordered, deferred,
            executed_actions={"级联已执行项"})
        assert registered == []
        assert ordered == ["进入运维模式"], "已执行项不入主序"
        assert "级联已执行项" in buttons, "已执行项仍注册册（供扫描去重）"


# ============================================================
# F1 STATE-CHANGE 双通道（行为）
# ============================================================

class TestBatch10F1NewMenuItemChannel:

    @pytest.mark.asyncio
    async def test_new_menu_item_detected_registered_executed(
            self, monkeypatch, caplog):
        """缺口①主修行为验证：新出现菜单项首现即检测、入册、执行。

        场景对齐 r12：进入运维模式成功后，扫描首见「退出运维模式」
        （text 不在历史基线，无 False→True 翻转可检）。
        """
        caplog.set_level(logging.INFO)
        # 扫描序列：首扫见新菜单项「退出运维模式」；执行成功后递归扫空
        _, calls = patch_batch(
            monkeypatch,
            [{"退出运维模式": True}, {}],
            {"退出运维模式": [{"success": True}]})
        button_states = {"进入运维模式": True}  # STATE-INIT 正常态基线
        buttons = {"进入运维模式": make_btn()}
        ordered = ["创建", "进入运维模式"]
        roles = {"创建": "create", "进入运维模式": "generic"}
        ui_result, deferred = {"dropdowns": []}, []
        executed = {"创建", "进入运维模式"}
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, buttons, executed, None,
            "", "", ui_result, "[t] ", attempted_actions={},
            deferred_destroy=deferred,
            ordered_actions=ordered, action_roles=roles,
            delete_actions=set(), trigger_action="进入运维模式")
        # 双通道执行链：检出即执行
        assert calls == ["退出运维模式"]
        assert "退出运维模式" in results and results["退出运维模式"].get("success")
        # 单源 helper 入册三件套
        assert "退出运维模式" in buttons
        assert "退出运维模式" in roles and roles["退出运维模式"] == "generic"
        assert ordered == ["创建", "进入运维模式", "退出运维模式"]
        assert "退出运维模式" in executed
        # 检出即入历史基线（递归轮不重检出）
        assert button_states.get("退出运维模式") is True
        # 日志签名（r13 验收锚点）
        assert "[STATE-CHANGE][NEW-MENU-ITEM]" in caplog.text

    @pytest.mark.asyncio
    async def test_new_menu_item_noise_not_executed(self, monkeypatch):
        """噪声过滤：'取消'/'确定' 不进新菜单项执行通道。"""
        _, calls = patch_batch(
            monkeypatch, [{"取消": True, "确定": True}, {}], {})
        button_states = {"进入运维模式": True}
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, {}, set(), None,
            "", "", {"dropdowns": []}, "[t] ", attempted_actions={},
            deferred_destroy=[], ordered_actions=["进入运维模式"],
            action_roles={}, delete_actions=set(),
            trigger_action="进入运维模式")
        assert calls == [], "噪声按钮不得进入执行链"
        assert results == {}
        # 注：噪声项会随 button_states.update(current_states) 进历史基线
        # （恒 True 永无 False→True 翻转可检，行为无害）——契约是
        # 「不进执行通道」，不是「不入基线」。

    @pytest.mark.asyncio
    async def test_discovered_executes_after_newly_enabled(
            self, monkeypatch):
        """执行序：newly_discovered 排尾——B 侧先让 A 侧解锁项执行完，
        防提前翻转把 A 侧解锁项重新禁用（r12 退出运维模式场景序）。"""
        _, calls = patch_batch(
            monkeypatch,
            [{"重启": True, "退出运维模式": True}, {}],
            {"重启": [{"success": True}],
             "退出运维模式": [{"success": True}]})
        button_states = {"重启": False, "关机": True}
        ordered = ["创建", "关机"]
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, {}, set(), None,
            "", "", {"dropdowns": []}, "[t] ", attempted_actions={},
            deferred_destroy=[], ordered_actions=ordered,
            action_roles={}, delete_actions=set(),
            trigger_action="关机")
        assert calls == ["重启", "退出运维模式"], \
            "newly_enabled 必须先于 newly_discovered 执行"

    @pytest.mark.asyncio
    async def test_registered_before_stable_gate_timed_out(
            self, monkeypatch, caplog):
        """稳态门 timed_out 提前返回：册已入不丢单（主循环/reconcile 可见）。

        缺口④（R1 pending 无该项）的册侧根治——timed_out 本轮放弃级联
        执行，但 ordered_actions 已含新发现项，主循环后续槽位/reconcile
        均可按册执行。
        """
        caplog.set_level(logging.INFO)
        scan = ScriptedScan([{"退出运维模式": True}])
        monkeypatch.setattr(ce, "_scan_all_button_states", scan)

        async def timed_out_settle(page, created_marker, state_ctx=None, **kw):
            return {"states": {"退出运维模式": True},
                    "status": "Stopping", "timed_out": True}

        monkeypatch.setattr(ce, "_wait_row_states_stable", timed_out_settle)
        button_states = {"进入运维模式": True}
        ordered = ["创建", "进入运维模式"]
        roles = {}
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, {}, set(), None,
            "", "", {"dropdowns": []}, "[t] ", attempted_actions={},
            deferred_destroy=[], ordered_actions=ordered,
            action_roles=roles, delete_actions=set(),
            trigger_action="进入运维模式")
        assert results == {}, "timed_out 本轮放弃级联执行"
        # 册已入（不丢单）：主序已含新发现项 + 检出即入历史基线
        assert ordered == ["创建", "进入运维模式", "退出运维模式"]
        assert button_states.get("退出运维模式") is True

    @pytest.mark.asyncio
    async def test_legacy_caller_without_context_still_executes(
            self, monkeypatch):
        """旧调用方兼容：册上下文 None 时跳过注册仅执行（不炸）。"""
        _, calls = patch_batch(
            monkeypatch, [{"退出运维模式": True}, {}],
            {"退出运维模式": [{"success": True}]})
        button_states = {"进入运维模式": True}
        ordered = ["创建"]
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, {}, set(), None,
            "", "", {"dropdowns": []}, "[t] ", attempted_actions={},
            deferred_destroy=[], ordered_actions=None, action_roles=None,
            delete_actions=None, trigger_action="")
        assert calls == ["退出运维模式"], "无册上下文时执行链不受影响"
        assert "退出运维模式" not in ordered, "None 时不得碰主序"

    @pytest.mark.asyncio
    async def test_immediate_baseline_entry_prevents_rediscovery(
            self, monkeypatch):
        """检出即入基线：递归轮同项不再重检出（防重复执行）。"""
        _, calls = patch_batch(
            monkeypatch,
            # 首扫检出并执行成功；递归扫到同项（已在基线）→ 无新检出
            [{"退出运维模式": True}, {"退出运维模式": True}, {}],
            {"退出运维模式": [{"success": True}]})
        button_states = {"进入运维模式": True}
        await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, {}, set(), None,
            "", "", {"dropdowns": []}, "[t] ", attempted_actions={},
            deferred_destroy=[], ordered_actions=["进入运维模式"],
            action_roles={}, delete_actions=set(),
            trigger_action="进入运维模式")
        assert calls == ["退出运维模式"], "递归轮不得重复执行已检出项"


# ============================================================
# F2 [PAIR-SCAN] 切换对定向扫描（源码守卫）
# ============================================================

class TestBatch10F2PairScan:

    def test_pair_scan_wired_before_cascade(self):
        """[PAIR-SCAN] 接线：_record_state_transition 之后、级联批之前
        （黄金窗——切换对侧成功瞬间 __vue__ 必新鲜）。"""
        src = _src(ce._validate_single_tab_crud)
        rec_pos = src.find("_record_state_transition(")
        pair_pos = src.find("[PAIR-SCAN]")
        cascade_pos = src.find("cascade_results = await "
                               "_check_and_execute_newly_enabled(")
        assert rec_pos > 0, "转移记录锚点缺失"
        assert pair_pos > rec_pos, "[PAIR-SCAN] 须在转移记录之后"
        assert cascade_pos > pair_pos, "[PAIR-SCAN] 须在级联批之前"

    def test_pair_scan_predicate_single_source(self):
        """判定单源：const.is_toggle_pair_side（不写死运维模式文本）+ 异常
        吞掉交 should_scan/级联通道兜底（增益层不炸主循环）。"""
        src = _src(ce._validate_single_tab_crud)
        pair_block = src[src.find("★ 批十 F2"):src.find("cascade_results")]
        assert "const.is_toggle_pair_side(action)" in pair_block
        assert "_rediscover_marker_dropdowns(" in pair_block
        assert "except Exception" in pair_block, "PAIR-SCAN 异常须吞掉"
        # 不写死运维模式文本（词表单源）
        assert "is_toggle_pair_side" in _src(const_mod)

    def test_pair_scan_passes_registry_context(self):
        """级联批调用传册上下文 4 参（批十 F1 接线）——缺传则新发现项
        执行结果不入册（r12 缺口④复发）。"""
        src = _src(ce._validate_single_tab_crud)
        cascade_pos = src.find("cascade_results = await "
                               "_check_and_execute_newly_enabled(")
        block = src[cascade_pos:cascade_pos + 700]
        assert "ordered_actions=ordered_actions" in block
        assert "action_roles=action_roles" in block
        assert "delete_actions=_delete_actions" in block
        assert "trigger_action=action" in block

    def test_should_scan_uses_shared_helper(self):
        """should_scan 增量入册改调单源 helper（双通道同源）+ 传 delete_actions。"""
        src = _src(ce._validate_single_tab_crud)
        assert "_register_new_buttons(" in src
        helper_block = src[src.find("_register_new_buttons("):]
        assert "delete_actions=_delete_actions" in helper_block[:700]

    def test_state_change_channel_uses_helper(self):
        """STATE-CHANGE 新菜单项通道同样经单源 helper 入册（先于稳态门）。"""
        src = _src(ce._check_and_execute_newly_enabled)
        reg_pos = src.find("_register_new_buttons(")
        gate_pos = src.find("if created_marker:")
        assert reg_pos > 0, "新菜单项通道须调用单源入册 helper"
        assert gate_pos > reg_pos, "入册须先于稳态门（timed_out 不丢单）"


# ============================================================
# F9 submitB 监听通用化 + 重试收窄（源码守卫）
# ============================================================

class TestBatch10F9SubmitB:

    def test_monitor_generalized_no_domain_hardcode(self):
        """监听判据通用化：resource_type + 写类方法；estack 域硬编码清零。

        断言用代码形态 "'/estack/api' in url"（注释里允许引用旧字面量
        说明历史——批十注释含「原 '/estack/api' 写死」无 ` in url` 后缀）。
        """
        src = _src(ne)
        assert "'/estack/api' in url" not in src, \
            "域名硬编码代码形态必须清零（EIS 全盲根因）"
        req_block = src[src.find("async def _on_request"):src.find("async def _on_response")]
        assert "resource_type in ('xhr', 'fetch')" in req_block
        assert "method in ('POST', 'PUT', 'PATCH', 'DELETE')" in req_block
        resp_block = src[src.find("async def _on_response"):]
        resp_block = resp_block[:resp_block.find("\n\n\n")]
        assert "resource_type in ('xhr', 'fetch')" in resp_block
        assert "method in ('POST', 'PUT', 'PATCH', 'DELETE')" in resp_block

    def test_retry_narrowing_predicate(self):
        """重试判据收窄：URL 已变 or 成功消息已捕获 → 跳过 JS click 盲重试。"""
        src = _src(ne)
        assert "_skip_js_retry = bool(_url_changed or _has_success_msg)" in src
        assert "[submitB-SKIP-RETRY]" in src
        assert "if (not _skip_js_retry) and len(_captured_requests) == 0" in src
        # 成功消息判据来源：捕获消息含「成功」
        assert '_has_success_msg = any("成功" in _t for _t in _retry_msg_texts)' in src

    def test_retry_gate_after_success_evidence(self):
        """收窄门位于重试块之前（先判后重试，不许先重试后打补丁）。"""
        src = _src(ne)
        gate_pos = src.find("_skip_js_retry = bool(_url_changed or _has_success_msg)")
        retry_pos = src.find("if (not _skip_js_retry) and len(_captured_requests) == 0")
        skip_log_pos = src.find("[submitB-SKIP-RETRY]")
        assert 0 < gate_pos < retry_pos, "判据定义须先于重试块"
        assert 0 < skip_log_pos < retry_pos, "跳过日志须在重试门内先于执行"


# ============================================================
# 批十-B F3 扫描链 menuId 统一 + dispatchEvent 有界复核（行为）
# ============================================================

class _ScanCapturePage:
    """evaluate 捕获替身：记录渲染 JS + 按调用序返回预设结果。

    locator().first.hover() 恒成功（批十-B 未动 hover locator 侧，
    按方案 F4「分两步走：evaluate 侧先行」）。
    """

    def __init__(self, results):
        self._results = list(results)
        self.captured = []
        self._i = 0

    def locator(self, selector):
        return self

    @property
    def first(self):
        return self

    async def hover(self, timeout=None):
        pass

    async def evaluate(self, js, *args, **kwargs):
        self.captured.append(str(js))
        self.evaluate_args = args
        idx = min(self._i, len(self._results) - 1)
        self._i += 1
        return self._results[idx]

    async def wait_for_timeout(self, ms):
        pass


# 全成功路径 evaluate 序（批十-B 后不变：row_exists → 直查按钮 →
# SCAN-SWEEP 探测 → 展开结果（含 menuId）→ 可见性 → 菜单收集）
_B10_SUCCESS_EVALS = [
    True,                                                   # 0. row_exists
    {"buttons": [], "allTexts": ["取消", "确定"]},          # 1. 直查按钮
    0,                                                      # 2. SCAN-SWEEP
    {"expanded": True, "method": "vue_show",
     "dropdownIndex": 0, "menuId": "menu-abc"},             # 3. 展开
    True,                                                   # 4. 可见性
    {"buttons": [], "menuInfo": [], "allItems": ["退出运维模式"]},  # 5. 收集
]


class TestBatch10F3MenuIdUnified:

    @pytest.mark.asyncio
    async def test_success_path_evaluate_sequence_unchanged(self):
        """vue_show 全成功路径：evaluate 序不因 F3/F4 增加（批十一
        后复核仅限 menuId 空场景，menuId 非空全成功路径零复核开销）。"""
        page = _ScanCapturePage(_B10_SUCCESS_EVALS)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert len(page.captured) == 6, \
            f"成功路径 evaluate 数须保持 6，实际 {len(page.captured)}"
        assert "退出运维模式" in result["all_current_texts"]

    @pytest.mark.asyncio
    async def test_dispatchevent_recheck_recovers_and_proceeds(self):
        """dispatchEvent + menuId 空假展开复核：初判 False → 复核轮转
        True → 菜单收集照常执行（合成事件滞后假阴性收窄，r12 09:28 链）。
        ★ 批十一 F3 契约同步：复核门判据由 method 改为 menuId 空
        （r13 证伪「vue_show 9 轮可靠」——7/10 None；menuId 是否可得
        才是可靠性分界），本用例 menuId 置 None 对齐新判据。"""
        evals = [
            True,                                                    # 0. row_exists
            {"buttons": [], "allTexts": ["取消", "确定"]},           # 1. 直查按钮
            0,                                                       # 2. SCAN-SWEEP
            {"expanded": True, "method": "dispatchEvent",
             "dropdownIndex": 0, "menuId": None},                    # 3. 展开
            False,                                                   # 4. 初判 False
            True,                                                    # 5. 复核轮 1 转 True
            {"buttons": [], "menuInfo": [],
             "allItems": ["退出运维模式"]},                          # 6. 菜单收集
        ]
        page = _ScanCapturePage(evals)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert "退出运维模式" in result["all_current_texts"], \
            "复核转 True 后菜单收集必须执行"
        # 可见性 JS 单源复用：初判与复核轮是同一段 JS（防判据漂移）
        visible_js = page.captured[4]
        assert page.captured[5] == visible_js, "复核轮须复用同一可见性 JS"

    @pytest.mark.asyncio
    async def test_dispatchevent_recheck_bounded_at_two(self):
        """有界复核：menuId 空 + 恒 False 最多 2 轮（+ 初判共 3 次可见性
        检查），仍 False 判零收集不进菜单扫描。★ 批十一 F3 契约同步：
        menuId 置 None 对齐新判据（method 判据已证伪）。"""
        evals = list(_B10_SUCCESS_EVALS)
        evals[3] = {"expanded": True, "method": "dispatchEvent",
                    "dropdownIndex": 0, "menuId": None}
        evals[4] = False
        page = _ScanCapturePage(evals + [False, False,  # 复核 2 轮恒 False
                                         {"buttons": [], "menuInfo": [],
                                          "allItems": ["不应收集"]}])
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        # 初判 + 复核 2 = 3 次可见性检查；无第 7 次 evaluate（菜单收集不进）
        assert len(page.captured) == 7, \
            f"恒 False 须有界于 2 轮复核（共 7 evaluate），实际 {len(page.captured)}"
        assert "不应收集" not in result["all_current_texts"]

    @pytest.mark.asyncio
    async def test_vue_show_false_no_recheck(self):
        """vue_show + menuId 非空 + 初判 False 不复核：menuId 可得即
        展开真实生效（批十一 P1c 定案「menuId 是否可得」是可靠性分界，
        证伪本用例批十原注释「vue_show 路径 9 轮实证可靠」），False 即
        判零收集，无复核轮延迟、不进菜单扫描。"""
        evals = [
            True,                                                    # 0. row_exists
            {"buttons": [], "allTexts": ["取消", "确定"]},           # 1. 直查按钮
            0,                                                       # 2. SCAN-SWEEP
            {"expanded": True, "method": "vue_show",
             "dropdownIndex": 0, "menuId": "menu-abc"},             # 3. 展开
            False,                                                   # 4. 初判 False
        ]
        page = _ScanCapturePage(evals)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        # vue_show 无复核、可见性 False 不进菜单收集 → 共 5 次 evaluate
        assert len(page.captured) == 5, \
            f"vue_show False 不得触发复核（5 evaluate），实际 {len(page.captured)}"
        assert "不应收集" not in result["all_current_texts"]

    def test_expand_js_captures_menu_id(self):
        """展开 JS 抓取 menuId（与 _scan_row_state_snapshot 同源）——
        targetMenu 优先定位的锚点来源。★ 批十一 F1 契约同步：menuId
        抓取扩源 _domMenu 优先 + vm.popperElm.id 兜底（appendToBody 后
        cell 域恒空），同源形态断言对齐新字面。"""
        src = _src(ce._scan_row_after_operation)
        assert "_domMenu.id || _domMenu.getAttribute('data-popper-id')" in src, \
            "menuId 抓取须与级联链同源形态"
        assert "popperElm?.id" in src, "批十一 F1 popperElm 兜底须在位"
        assert "data-popper-id" in src
        assert src.count("menuId") >= 5, "menuId 须贯通展开/可见性/收集链"

    def test_no_full_page_menu_fallback(self):
        """删除全页回退守卫（代码形态，注释可引用历史名不误伤）：
        两扫描链不再含「捡第一个可见 menu」赋值与全页 some() 判定。"""
        for fn in (ce._scan_row_after_operation, ce._scan_row_state_snapshot):
            src = _src(fn)
            assert "targetMenu = menu;" not in src, \
                f"{fn.__name__} 仍含「捡第一个可见 menu」回退赋值"
            assert "Array.from(menus).some" not in src, \
                f"{fn.__name__} 仍含全页菜单可见性回退"

    def test_locate_chain_order_wired(self):
        """定位链序守卫：menuId → 行 scope（marker 文本）→ dropdownIndex
        （menuId 在 row-scope 之前，row-scope 在 dropdownIndex 之前）。"""
        src = _src(ce._scan_row_after_operation)
        vis_block = src[src.find("_menu_visible_js = f"):src.find("has_visible_menu = await page.evaluate")]
        mid_pos = vis_block.find("if (params.menuId)")
        row_pos = vis_block.find("if (!targetMenu) {{", mid_pos)
        idx_pos = vis_block.find("typeof params.dropdownIndex")
        assert 0 < mid_pos < row_pos < idx_pos, "定位链序必须 menuId → 行 scope → dropdownIndex"


# ============================================================
# 批十-B F4 row_selector 文本锚定（行为 + 源码守卫）
# ============================================================

class TestBatch10F4TextAnchoredRow:

    @pytest.mark.asyncio
    async def test_row_located_by_marker_text_in_js(self):
        """F4 行为验证：行定位 JS（含 .el-table__row 的段）以 markerText
        文本找行渲染——位置式 row_selector 不再进任何 evaluate JS。
        SCAN-SWEEP 弹窗探测段（.el-dialog__wrapper 计数，批九 §五）与
        行定位无关，不在 markerText 约束内，但同受位置式行查询禁令约束。"""
        page = _ScanCapturePage(_B10_SUCCESS_EVALS)
        await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        positional = ".el-table__row:nth-child(1)"
        for i, js in enumerate(page.captured):
            if ".el-table__row" in js:  # 行定位段：必须文本锚定找行
                assert "markerText" in js or "params.marker" in js, \
                    f"第 {i} 段行定位 JS 未按 marker 文本找行"
            assert f"document.querySelector('{positional}')" not in js, \
                f"第 {i} 段 JS 仍含位置式行查询"

    @pytest.mark.asyncio
    async def test_row_absent_short_circuit(self):
        """行不存在（row_exists=False）→ 空结果短路（现状行为保持）。"""
        page = _ScanCapturePage([False])
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert result == {"new_buttons": [], "disappeared_buttons": [],
                          "all_current_texts": set()}
        assert len(page.captured) == 1, "行不存在须单次探测即短路"

    def test_row_by_marker_snippet_same_domain(self):
        """_ROW_BY_MARKER_JS 与生成侧 get_row_selector_by_marker 同域：
        可见 tab-pane → .el-table__body-wrapper → 文本找行。"""
        assert hasattr(ce, "_ROW_BY_MARKER_JS")
        snippet = ce._ROW_BY_MARKER_JS
        assert ".el-tab-pane" in snippet
        assert ".el-table__body-wrapper" in snippet
        assert "markerText" in snippet
        # 生成侧同域（同 scope 形态）
        from core.discovery.shared import row_interaction as ri
        assert ".el-tab-pane" in ri._TAB_SCOPE_JS
        assert ".el-table__body-wrapper" in _src(ri.get_row_selector_by_marker)

    def test_no_positional_row_query_left_in_scan_chains(self):
        """源码守卫：两扫描链不再含位置式行查询（F4 主断言）。"""
        for fn in (ce._scan_row_after_operation, ce._scan_row_state_snapshot):
            src = _src(fn)
            assert "document.querySelector('{row_selector}')" not in src, \
                f"{fn.__name__} 仍含位置式行查询（失锚病根）"
            assert "_ROW_BY_MARKER_JS" in src, \
                f"{fn.__name__} 须消费文本锚定找行单源片段"

    def test_snapshot_locate_chain_row_scope_fallback(self):
        """_scan_row_state_snapshot 定位链：menuId → 行 scope →
        dropdownIndex（原「最后回退：第一个可见 menu」删除后行 scope 补位）。"""
        src = _src(ce._scan_row_state_snapshot)
        menuid_pos = src.find('if (menuId) {{')
        row_pos = src.find("批十 F3：行 scope 回退", menuid_pos)
        idx_pos = src.find("回退：通过 dropdown 索引定位", row_pos)
        assert 0 < menuid_pos < row_pos < idx_pos, \
            "snapshot 定位链序必须 menuId → 行 scope → dropdownIndex"


# ============================================================
# 批十-C 测试基建：reconcile settle 序列替身
# ============================================================

class SettleScript:
    """_wait_row_states_stable 序列替身（越界重复最后一个）。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self, page, created_marker, state_ctx=None,
                      target_status=None, **kw):
        idx = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        return dict(self.seq[idx])


def patch_reconcile(monkeypatch, settle_seq, results_by_action):
    """_state_aware_reconcile 打桩：settle 序列 + 执行脚本。

    返回 (settle, calls)：calls 记录 _error_driven_retry 实际执行的
    action 序（R1 业务执行与恢复转移共用同一 retry 入口）。
    """
    settle = SettleScript(settle_seq)
    monkeypatch.setattr(ce, "_wait_row_states_stable", settle)

    async def fake_install(page):
        pass

    monkeypatch.setattr(ce, "_install_message_capture", fake_install)

    async def fake_preflight(page, btn, marker, snapshot=None, tab_prefix=""):
        return {"pass": True, "wait_reason": "", "detail": ""}

    monkeypatch.setattr(ce, "_preflight_executable", fake_preflight)

    calls = []

    async def fake_retry(page, fn, context):
        action = context["action"]
        script = results_by_action.get(action) or [{"success": False}]
        idx = calls.count(action)
        calls.append(action)
        return script[min(idx, len(script) - 1)]

    monkeypatch.setattr(ce, "_error_driven_retry", fake_retry)

    async def fake_dismiss(page):
        pass

    monkeypatch.setattr(
        "core.discovery.shared.transition_helpers._dismiss_all_overlays",
        fake_dismiss)
    return settle, calls


def _failed_pending_result():
    """pending 回收口径的失败签名（skipped/按钮被禁用）。"""
    return {"success": False, "error_type": "skipped",
            "error_text": "按钮被禁用"}


# ============================================================
# 批十-C F5 运维正交维度恢复边（三键持久化链 + MAINT-FIRST 消费）
# ============================================================

class TestBatch10F5MaintenanceDimension:

    def test_snapshot_reads_maintenance_channel(self):
        """F5-1 数据通道：snapshot JS 读 command.row.maintenance，
        全返回分支带 maintenance 键（非展开/异常分支 None 降级）。"""
        src = _src(ce._scan_row_state_snapshot)
        assert "row.maintenance !== undefined" in src
        assert "maintenance = (row.maintenance === true)" in src
        assert "rowStatus, qgaReady, maintenance}}" in src
        assert '"maintenance": dropdown_states.get("maintenance")' in src
        assert src.count('"maintenance": None') == 2, \
            "非展开分支与异常分支必须显式 None（键契约一致）"

    @pytest.mark.asyncio
    async def test_settle_propagates_maintenance_all_paths(
            self, monkeypatch):
        """F5-1 透传：稳态返回经 {**cur} 展开携带 maintenance；首扫空
        早退分支显式 maintenance=None（MAINT-FIRST 读值契约一致）。"""
        snap = {"states": {"关机": True}, "vue_states": {},
                "status": "Running", "qga_ready": None, "maintenance": True}

        async def fake_snap(page, created_marker):
            return dict(snap)

        monkeypatch.setattr(ce, "_scan_row_state_snapshot", fake_snap)
        out = await ce._wait_row_states_stable(FakePage(), "test663637")
        assert out["maintenance"] is True
        assert out["timed_out"] is False

        async def fake_snap_empty(page, created_marker):
            return {"states": {}, "vue_states": {}, "status": None,
                    "qga_ready": None, "maintenance": None}

        monkeypatch.setattr(ce, "_scan_row_state_snapshot", fake_snap_empty)
        out = await ce._wait_row_states_stable(FakePage(), "test663637")
        assert out["maintenance"] is None

    def test_settle_early_return_contract(self):
        """settle 早退分支契约守卫：显式 maintenance 键 + docstring 记录。"""
        src = _src(ce._wait_row_states_stable)
        assert '"maintenance": None, "timed_out": False}' in src
        assert '"maintenance": bool|None' in src, "docstring 须记录新键"

    def test_init_state_ctx_maintenance_seed_dedupe(self):
        """F5-3 种子装载：畸形过滤 + action 去重保序 + source 缺省 seed。"""
        ctx = ce._init_state_ctx({"maintenance_edges": [
            {"action": "退出运维模式", "source": "pair_scan"},
            {"action": "退出运维模式", "source": "dup"},  # 同 action 去重
            {"maintenance": True},                          # 无 action 过滤
            "解冻",                                        # 非 dict 过滤
        ]})
        assert ctx["maintenance_edges"] == [
            {"action": "退出运维模式", "maintenance": True,
             "source": "pair_scan"}]
        ctx2 = ce._init_state_ctx({"maintenance_edges": [
            {"action": "退出运维模式"}, {"action": "解冻"}]})
        assert ctx2["maintenance_edges"] == [
            {"action": "退出运维模式", "maintenance": True, "source": "seed"},
            {"action": "解冻", "maintenance": True, "source": "seed"}]

    def test_pair_scan_registers_maintenance_edge(self):
        """F5-2 采集接线：PAIR-SCAN 定向重收集新增项入
        maintenance_edges（state_ctx 缺失静默跳过 + action 去重）。"""
        src = _src(ce._validate_single_tab_crud)
        pair_pos = src.find("★ 批十 F2")
        maint_pos = src.find("[MAINT-EDGE]")
        cascade_pos = src.find("cascade_results = await ")
        assert 0 < pair_pos < maint_pos < cascade_pos, \
            "MAINT-EDGE 采集须在 PAIR-SCAN 块内、级联批之前"
        block = src[pair_pos:maint_pos + 400]
        assert "if state_ctx:" in block, "state_ctx 缺失时静默跳过（不炸）"
        assert '"maintenance_edges", []' in block
        assert '"source": "pair_scan"' in block
        assert 'any(e.get("action") == _bside' in block, "action 去重防重复登记"

    def test_ui_result_writeback_and_run_seed_wired(self):
        """F5-5 三键持久化闭环：crud 写回 ui_result + run.py 种子装载
        + 异常退出兜底注入（批一 1.3 三键口径对称扩展）。"""
        src = _src(ce._validate_business_flow)
        assert ('ui_result["maintenance_edges"] = '
                'state_ctx.get("maintenance_edges", [])') in src
        import io
        import os
        run_py = os.path.normpath(os.path.join(
            os.path.dirname(ce.__file__), "..", "run.py"))
        run_src = io.open(run_py, encoding="utf-8").read()
        assert ('seed["maintenance_edges"] = '
                'old["maintenance_edges"]') in run_src, \
            "_load_state_seed 须装载 maintenance_edges（跨轮种子）"
        assert 'if "maintenance_edges" not in ui_result:' in run_src, \
            "异常退出兜底须保留旧轮种子（防断代）"
        assert ('ui_result["maintenance_edges"] = '
                'state_seed.get("maintenance_edges", [])') in run_src

    @pytest.mark.asyncio
    async def test_maint_first_reorders_to_head(self, monkeypatch, caplog):
        """F5-4 消费：maintenance=True 可读且 ready 含已登记运维边项 →
        排队首（r12 定证：不退出运维模式则 8 项运维锁死+删除收尾全灭）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [{
            "states": {"退出运维模式": True, "重启": True},
            "status": "Running", "qga_ready": None,
            "maintenance": True, "timed_out": False,
        }]
        _, calls = patch_reconcile(
            monkeypatch, settle_seq,
            {"退出运维模式": [{"success": True}], "重启": [{"success": True}]})
        validated = {"退出运维模式": _failed_pending_result(),
                     "重启": _failed_pending_result()}
        buttons = {"退出运维模式": make_btn(), "重启": make_btn()}
        state_ctx = ce._init_state_ctx({"maintenance_edges": [
            {"action": "退出运维模式", "source": "pair_scan"}]})
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})
        assert calls[0] == "退出运维模式", "运维维度恢复项必须排队首"
        assert calls == ["退出运维模式", "重启"]
        assert "[STATE-RECONCILE][MAINT-FIRST]" in caplog.text
        assert results["退出运维模式"]["success"] is True
        assert results["重启"]["success"] is True

    @pytest.mark.asyncio
    async def test_maint_first_no_reorder_when_flag_unreadable(
            self, monkeypatch, caplog):
        """maintenance 不可读（None）→ 零重排零日志（行为不变）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [{
            "states": {"重启": True}, "status": "Running",
            "qga_ready": None, "maintenance": None, "timed_out": False,
        }]
        _, calls = patch_reconcile(
            monkeypatch, settle_seq, {"重启": [{"success": True}]})
        validated = {"重启": _failed_pending_result()}
        buttons = {"重启": make_btn()}
        state_ctx = ce._init_state_ctx({"maintenance_edges": [
            {"action": "退出运维模式", "source": "pair_scan"}]})
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})
        assert calls == ["重启"]
        assert "[STATE-RECONCILE][MAINT-FIRST]" not in caplog.text

    @pytest.mark.asyncio
    async def test_maint_first_no_reorder_when_item_unregistered(
            self, monkeypatch, caplog):
        """maintenance=True 但 ready 项未登记运维边 → 零重排
        （重排仅限已登记项，不猜测其他项语义）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [{
            "states": {"重启": True, "关机": True}, "status": "Running",
            "qga_ready": None, "maintenance": True, "timed_out": False,
        }]
        _, calls = patch_reconcile(
            monkeypatch, settle_seq,
            {"重启": [{"success": True}], "关机": [{"success": True}]})
        validated = {"重启": _failed_pending_result(),
                     "关机": _failed_pending_result()}
        buttons = {"重启": make_btn(), "关机": make_btn()}
        state_ctx = ce._init_state_ctx()  # 无登记运维边
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})
        assert set(calls) == {"重启", "关机"}  # set 迭代序不定，不锁顺序
        assert "[STATE-RECONCILE][MAINT-FIRST]" not in caplog.text

    def test_maint_first_wired_in_reconcile(self):
        """源码守卫：MAINT-FIRST 位于 ready 计算后、F10 日志前。"""
        src = _src(ce._state_aware_reconcile)
        assert 'settle.get("maintenance") is True' in src
        assert "[STATE-RECONCILE][MAINT-FIRST]" in src
        ready_pos = src.find("ready = [a for a in pending")
        maint_pos = src.find("[STATE-RECONCILE][MAINT-FIRST]")
        log_pos = src.find("[STATE-RECONCILE][R1-READY-DECISION]")
        assert 0 < ready_pos < maint_pos < log_pos, \
            "MAINT-FIRST 须在 ready 计算后、F10 可观测日志前"


# ============================================================
# 批十-C F6 TRANSIENT-WAIT 判据数据驱动扩展（r12 P2 主修）
# ============================================================

class TestBatch10F6TransientWait:

    @pytest.mark.asyncio
    async def test_transient_wait_fires_when_in_matrix_but_no_out_edges(
            self, monkeypatch, caplog):
        """r12 P2 场景闭合：Stopping 已入已观测矩阵（批九 F2 条件②
        永不触发）但无出边记录 → F6 扩展判据仍进入有界再稳态，行回
        Running 后解锁 关机 业务执行（r11 差 15s 即可恢复的实证机制化）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [
            {"states": {"关机": False}, "status": "Stopping",
             "qga_ready": None, "maintenance": None, "timed_out": False},
            {"states": {"关机": True}, "status": "Running",
             "qga_ready": None, "maintenance": None, "timed_out": False},
            {"states": {"关机": False}, "status": "Stopped",
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        _, calls = patch_reconcile(
            monkeypatch, settle_seq, {"关机": [{"success": True}]})
        validated = {"关机": _failed_pending_result()}
        buttons = {"关机": make_btn()}
        state_ctx = ce._init_state_ctx({
            # Stopping 已入矩阵：旧判据「status not in matrix」为 False
            "state_button_matrix": {"Stopping": {"关机": False},
                                    "Running": {"关机": True}},
            # 无任何 from=Stopping 的出边 → _no_out_edges=True
            "state_transitions": [
                {"action": "关机", "from": "Running", "to": "Stopped",
                 "validated": True}],
        })
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})
        assert "[TRANSIENT-WAIT]" in caplog.text
        assert "状态 Stopping" in caplog.text, "须点名 Stopping 无出边过渡态"
        assert calls == ["关机"], "再稳态解锁后必须执行关机业务"
        assert results["关机"]["success"] is True

    def test_transient_wait_guard_shapes(self):
        """源码守卫：判据「未入矩阵 OR 该态无出边」+ 有界 2 次。"""
        src = _src(ce._state_aware_reconcile)
        assert "_no_out_edges = not any(" in src
        assert "(status not in matrix or _no_out_edges)" in src
        assert "transient_waits < 2" in src
        assert "e.get(\"from\") == status" in src, \
            "出边判定须按 from==当前态过滤（数据驱动）"


# ============================================================
# 批十-C F7 边候选 validated 角色门（r12 P3 首碰拦截）
# ============================================================

class TestBatch10F7EdgeValidatedGate:

    def test_record_state_transition_stamps_validated_true(self):
        """记边即带执行证据标志（记边前提＝该 action 刚执行成功）。"""
        ctx = ce._init_state_ctx()
        ce._record_state_transition(ctx, "关机", "Running", "Stopped")
        assert ctx["state_transitions"] == [
            {"action": "关机", "from": "Running", "to": "Stopped",
             "validated": True}]
        # 同三元组去重（标志不产生第二条边）
        ce._record_state_transition(ctx, "关机", "Running", "Stopped")
        assert len(ctx["state_transitions"]) == 1

    def test_init_state_ctx_preserves_validated_flag(self):
        """种子跨轮保留标志：有标志保序保留；无标志存量旧边保持无标志
        （消费端首碰拦截语义）。"""
        ctx = ce._init_state_ctx({"state_transitions": [
            {"action": "关机", "from": "Running", "to": "Stopped",
             "validated": True},
            {"action": "网络配置", "from": "Running", "to": "Stopped"},
        ]})
        assert ctx["state_transitions"][0]["validated"] is True
        assert "validated" not in ctx["state_transitions"][1]

    @pytest.mark.asyncio
    async def test_reconcile_first_touch_intercepts_pseudo_edge(
            self, monkeypatch, caplog):
        """r12 P3 场景：存量伪边（网络配置 Stopping→Stopped 观察巧合）
        首次消费时无 validated 执行记录可查（批九 F3 消费门漏拦）——
        F7 边自带标志门在候选循环首碰拦截，真边（关机）照常驱动恢复。"""
        caplog.set_level(logging.INFO)
        settle_seq = [
            {"states": {"网络配置": True, "关机": True, "开机": False},
             "status": "Running", "qga_ready": None,
             "maintenance": None, "timed_out": False},
            {"states": {"开机": True, "关机": False},
             "status": "Stopped", "qga_ready": None,
             "maintenance": None, "timed_out": False},
        ]
        _, calls = patch_reconcile(
            monkeypatch, settle_seq,
            {"关机": [{"success": True}], "开机": [{"success": True}]})
        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn(), "关机": make_btn(),
                   "网络配置": make_btn()}
        state_ctx = ce._init_state_ctx({
            "state_button_matrix": {"Running": {"开机": False},
                                    "Stopped": {"开机": True}},
            # 伪边在前（无标志存量形态）；真边在后
            "state_transitions": [
                {"action": "网络配置", "from": "Running", "to": "Stopped"},
                {"action": "关机", "from": "Running", "to": "Stopped",
                 "validated": True}],
        })
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})
        assert "网络配置" not in calls, "伪边必须首碰拦截（不被消费）"
        assert calls == ["关机", "开机"], "真边关机驱动恢复后执行开机"
        assert "[EDGE-SKIP-UNVALIDATED]" in caplog.text
        assert "网络配置" in caplog.text
        assert results["关机"]["_reconcile_meta"]["intent"] == "state_restore"

    def test_unvalidated_gate_position(self):
        """源码守卫：F7 门在批九 F3 消费门之后、key 去重/状态匹配之前
        （首碰即拦，不进候选判定）。"""
        src = _src(ce._state_aware_reconcile)
        nav_pos = src.find("[STATE-RECONCILE][EDGE-SKIP-NAV]")
        gate_pos = src.find("[EDGE-SKIP-UNVALIDATED]")
        assert 0 < nav_pos < gate_pos
        assert src.find('edge.get("from") != status', gate_pos) > gate_pos, \
            "F7 门须在候选状态匹配检查之前"


# ============================================================
# 批十-C F8 装配序切换对侧排尾（r12 P5）
# ============================================================

class TestBatch10F8ToggleSideAssembly:

    def test_split_toggle_side_actions_pure(self):
        """纯函数分离：非切换对在前、切换对侧排尾，保序；判定单源
        const.is_toggle_pair_side（不写死运维模式文本）。"""
        normal, toggle = ce._split_toggle_side_actions(
            ["创建", "进入运维模式", "查询", "冻结", "编辑"], {})
        assert normal == ["创建", "查询", "编辑"]
        assert toggle == ["进入运维模式", "冻结"]
        assert ce._split_toggle_side_actions(None, {}) == ([], [])
        assert ce._split_toggle_side_actions([], {}) == ([], [])

    def test_assembly_order_normal_toggle_rowswitch(self):
        """装配接线守卫：step 4 消费分离函数——非切换对 → 切换对侧 →
        row switch 依旧最尾（r12 P5：切换对翻转干扰半径收窄到收尾段）。"""
        src = _src(ce._validate_single_tab_crud)
        split_pos = src.find("_split_toggle_side_actions(")
        normal_pos = src.find("ordered_actions.extend(_normal_others)")
        toggle_pos = src.find("ordered_actions.extend(_toggle_sides)")
        row_pos = src.find("# 5. Row switch")
        log_pos = src.find("[ASSEMBLE-F8]")
        assert 0 < split_pos < normal_pos < toggle_pos < row_pos, \
            "装配序必须 非切换对 → 切换对侧 → row switch"
        assert 0 < log_pos < toggle_pos


# ============================================================
# 批十-C F10 R1 ready 判定可观测性（r12 P4 疑点定案日志）
# ============================================================

class TestBatch10F10ReadyObservability:

    @pytest.mark.asyncio
    async def test_ready_decision_logs_reasons(self, monkeypatch, caplog):
        """无 ready 轮次逐项原因枚举 + 基础上下文（r13 真机按此日志
        定案 P4 是否需要行为修复）。

        可达原因：deferred_destroy（删除类）/ state_disabled（当前态禁用，
        含预算未耗尽的过渡窗失败项）。attempted_exhausted /
        business_executed / ready_other 为防御性枚举分支——attempted
        预算耗尽项在 _collect_state_pending 收集时即被过滤（批六
        修复5）、业务执行路径恒配 pending.discard，当前接线不可经
        行为触发，形状契约由 test_ready_decision_guard_shapes 守卫。
        """
        caplog.set_level(logging.INFO)
        settle_seq = [{
            "states": {"删除": False, "挂载": False, "重启": False},
            "status": "Running", "qga_ready": None,
            "maintenance": None, "timed_out": False,
        }]
        _, calls = patch_reconcile(monkeypatch, settle_seq, {})
        validated = {
            "删除": _failed_pending_result(),
            # 预算 2/3（未耗尽可回收）但当前态禁用 → state_disabled
            "重启": _failed_pending_result(),
            "挂载": _failed_pending_result(),
        }
        buttons = {"删除": make_btn(), "重启": make_btn(),
                   "挂载": make_btn()}
        state_ctx = ce._init_state_ctx()
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={"重启": 2})
        assert calls == [], "无 ready 轮次不得执行任何操作"
        assert "[STATE-RECONCILE][R1-READY-DECISION]" in caplog.text
        assert "pending 3 项均不 ready" in caplog.text
        assert "删除=deferred_destroy" in caplog.text
        assert "重启=state_disabled" in caplog.text
        assert "挂载=state_disabled" in caplog.text
        assert "status=Running" in caplog.text
        assert "timed_out=False" in caplog.text

    def test_ready_decision_guard_shapes(self):
        """源码守卫：原因枚举五分支 + 基础上下文字段。"""
        src = _src(ce._state_aware_reconcile)
        assert "[STATE-RECONCILE][R1-READY-DECISION]" in src
        for reason in ("=deferred_destroy", "=business_executed",
                       "=attempted_exhausted", "=ready_other",
                       "=state_disabled"):
            assert reason in src, f"原因枚举缺 {reason} 分支"
        assert "timed_out={settle.get('timed_out')}" in src

