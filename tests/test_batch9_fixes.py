# -*- coding: utf-8 -*-
"""
test_batch9_fixes.py — 批九-A（r11 验收 P-2/P-1/P-5 源头修复）单元测试

方案：docs/debug/eis云主机批九源头方案.md（§一 P-2 / §二 P-1 / §四 P-5）

F1 轮内定向消费（P-2 Gap A 主修）
  - r11 实证：14:13:00 暂停 成功翻转 Running→Paused 后，轮首 stale ready
    继续消费 硬重启（新旧矩阵行均可用）→ Paused 窗口被同轮摧毁，
    恢复 终身未执行。修复：成功项 post-settle 记边后重算调度序，
    本次翻转解锁的 pending 绝对优先（窗口优先序）。

F2 过渡态再稳态（P-2 Gap B 主修）
  - r11 实证：14:14:21 settle 读到 Stopping（>10s 过渡态）→ 恢复 的
    需求态 Paused 无 Stopping 出边 → reconcile 整体 break，行 15s 后
    回 Running（差 15s 即可再驱动一轮完整恢复）。修复：「无出边 +
    pending 未清 + status 从未作为稳态入矩阵」→ 有界再 settle 重评。

F3 转移边质量门（P-2 Gap C 主修）
  - r11 实证：网络配置 Stopping→Stopped 观察巧合边（导航型操作执行时
    恰逢状态漂移）被 R1 两次消费各耗 ~30s 空转。修复：记录门 + 消费门
    双道同判据单源（_is_nav_without_form）。

F4 语义目标态等待（P-2 F1 第 5 点）
  - post-settle 优先等待已知转移边的语义 to 态出现，慢翻转/过渡态
    场景不再依赖「值两次相同」碰运气；首跑无边回落双条件（行为一致）。

G1 收尾删除（P-1 主修）——删除项不入主循环序，deferred_destroy 清单
  在 final reconcile + clear_table_search 之后统一经既有删除链执行
  （「删除最后」由结构保证而非排序技巧）。

G2 增益层 defer（P-1）——级联批（含递归批）与 R1（ready 直执行 +
  恢复驱动）遇 destroy 类不执行不入册，defer 收尾统一执行。

P-5 矩阵全态零观测诚实短路——收尾仍未回收且矩阵全态零观测的 pending
  给 skipped_state_window 终态，不沿用 click_failed 判失败。
"""
import inspect
import logging

import pytest

from core.discovery.operation_executor import crud_executor as ce
from core.discovery.shared.button_clicker import is_destroy_text


def _src(fn):
    return inspect.getsource(fn)


# ============================================================
# 测试基建
# ============================================================

class FakePage:
    """最小 Playwright Page 替身：仅支持 wait_for_timeout 计数。"""

    def __init__(self):
        self.sleeps = 0

    async def wait_for_timeout(self, ms):
        self.sleeps += 1


def make_snapshot(states=None, status=None, qga_ready=None, vue_states=None):
    return {
        "states": dict(states or {}),
        "status": status,
        "qga_ready": qga_ready,
        "vue_states": dict(vue_states or {}),
    }


class SettleStub:
    """settle 替身：按调用序返回预设快照（越界重复最后一个）。

    collect_matrix=False 时不采集矩阵——F2 过渡态场景专用：过渡态若被
    采入矩阵，F2「status not in matrix（从未作为稳态被观测）」判据失效
    （真实 _wait_row_states_stable 只在双条件稳态/F4 目标命中时采集）。
    """

    def __init__(self, settles, collect_matrix=True):
        self.settles = list(settles)
        self.calls = 0
        self.collect_matrix = collect_matrix

    async def __call__(self, page, created_marker, state_ctx=None, **kwargs):
        idx = min(self.calls, len(self.settles) - 1)
        self.calls += 1
        snap = self.settles[idx]
        if isinstance(snap, dict) and "timed_out" not in snap:
            snap = {**snap, "timed_out": False}
        if (self.collect_matrix and state_ctx is not None
                and snap.get("status") is not None):
            ce._record_state_matrix(
                state_ctx, snap["status"], snap.get("states") or {})
        return snap


class ScriptedScan:
    """_scan_all_button_states / _scan_row_state_snapshot 序列替身。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self, page, created_marker=None):
        idx = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        return self.seq[idx]


def make_btn(location="dropdown", text=""):
    return {"location": location, "text": text, "locator": "button:has-text('x')"}


def _fail_result(error_type="click_failed"):
    return {
        "success": False,
        "error_type": error_type,
        "error_text": "按钮被禁用" if error_type == "skipped" else "过渡窗口点击失败",
    }


class ReconcileHarness:
    """_state_aware_reconcile 测试夹具：settle / 执行链 / 预检全打桩。

    calls 记录 (action, settle_calls)——settle_calls 为该项执行时刻已发生
    的 settle 调用数，供 F1「出列后下轮重评」的轮次对账。
    """

    def __init__(self, monkeypatch, settles, results_by_action,
                 collect_matrix=True):
        self.settle = SettleStub(settles, collect_matrix=collect_matrix)
        monkeypatch.setattr(ce, "_wait_row_states_stable", self.settle)

        async def fake_row_sel(page, marker):
            return "#row-stub"

        monkeypatch.setattr(ce, "get_row_selector_by_marker", fake_row_sel)

        async def fake_install(page):
            pass

        monkeypatch.setattr(ce, "_install_message_capture", fake_install)

        self.calls = []

        async def fake_retry(page, fn, context):
            action = context["action"]
            script = results_by_action.get(action) or [
                {"success": False, "error_text": "无脚本"}]
            idx = self.calls.count(action)
            self.calls.append((action, self.settle.calls))
            return script[min(idx, len(script) - 1)]

        monkeypatch.setattr(ce, "_error_driven_retry", fake_retry)

    def args(self, validated, buttons, roles=None, executed=None, state_ctx=None):
        return dict(
            page=None, created_marker="vm-001",
            validated=validated, buttons_by_action=buttons,
            action_roles=roles or {},
            executed_actions=executed if executed is not None else set(),
            form_filler=None, tab_name="", tab_locator="",
            tab_prefix="[t] ", trigger_action="关机",
            state_ctx=state_ctx if state_ctx is not None else ce._init_state_ctx(),
        )


# ============================================================
# F1 轮内定向消费（P-2 Gap A）
# ============================================================

class TestBatch9F1WindowUnlocked:

    @pytest.mark.asyncio
    async def test_unlocked_pending_consumed_before_stale_ready(
            self, monkeypatch, caplog):
        """r11 14:13 场景复刻：暂停 翻转 Running→Paused 后，新解锁的
        恢复 必须先于旧 ready 残留的 硬重启 消费（窗口优先序）。"""
        caplog.set_level(logging.INFO)
        settles = [
            # R1#1 settle：暂停/硬重启 可用（轮首快照），恢复 不可用
            make_snapshot({"暂停": True, "硬重启": True, "恢复": False}, "Running"),
            # 暂停 成功后 post-settle（F4：恢复 边首跑未知，回落双条件）
            make_snapshot({"恢复": True, "硬重启": True, "暂停": False}, "Paused"),
            # 恢复 成功后 post-settle：回 Running
            make_snapshot({"硬重启": True, "恢复": False, "暂停": True}, "Running"),
        ]
        h = ReconcileHarness(monkeypatch, settles, {
            "暂停": [{"success": True}],
            "恢复": [{"success": True}],
            "硬重启": [{"success": True}],
        })
        validated = {
            "暂停": _fail_result(),
            "恢复": _fail_result(),
            "硬重启": _fail_result(),
        }
        buttons = {"暂停": make_btn(), "恢复": make_btn(), "硬重启": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"暂停": True, "硬重启": True, "恢复": False},
                "Paused": {"恢复": True, "硬重启": True, "暂停": False},
            },
        })
        executed = set()
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, executed=executed, state_ctx=ctx))
        # 轮首 ready（暂停/硬重启 两项）内部序来自 pending set 哈希迭代
        # （既有行为，非 F1 规格）。F1 保证的窗口优先序：无论轮首谁先
        # 执行，翻转解锁的 恢复 必须立即抢占消费（第二个执行），不得
        # 让位于另一项旧 ready 残留（方案 §1.6「下一个执行的是 恢复」）。
        seq = [a for a, _ in h.calls]
        assert len(seq) == 3 and set(seq) == {"暂停", "恢复", "硬重启"}
        assert seq[1] == "恢复", \
            "翻转解锁的 恢复 必须立即抢占消费（窗口优先），不得让位旧 ready 残留"
        assert {"暂停", "恢复", "硬重启"} <= executed
        assert "[WINDOW-UNLOCKED]" in caplog.text
        # 恢复 边对两序均确定：Paused 态执行 → post-settle 回 Running
        assert {"action": "恢复", "from": "Paused", "to": "Running"} \
            in ctx["state_transitions"]
        # 暂停 Running→Paused 边仅在 暂停 先行序下成立（另一序下 暂停
        # post-settle 越界重复尾快照 Running→Running，两端相同静默跳过）
        if seq[0] == "暂停":
            assert {"action": "暂停", "from": "Running", "to": "Paused"} \
                in ctx["state_transitions"]

    @pytest.mark.asyncio
    async def test_stale_ready_dropped_when_not_executable_in_post_state(
            self, monkeypatch, caplog):
        """翻转后旧 ready 残留在 post 态不可用 → 当轮出列（不得轮内
        消费），交后续 settle/窗口重评后才执行——pending 不丢。

        轮首两项 ready 内部序哈希随机（set 迭代）：暂停 先行 → 硬重启
        出列、轮间 settle 4 重评；硬重启 先行 → 暂停 出列、下一次窗口
        解锁（settle 3）重评。两序下「出列不丢、后续重评」不变量一致。
        """
        caplog.set_level(logging.INFO)
        settles = [
            # R1#1 settle
            make_snapshot({"暂停": True, "硬重启": True, "恢复": False}, "Running"),
            # 暂停 post：Paused 态 硬重启 不可用 → F1 出列
            make_snapshot({"恢复": True, "硬重启": False, "暂停": False}, "Paused"),
            # 恢复 post：回 Running（重启中窗口，全不可用）
            make_snapshot({"恢复": False, "硬重启": False, "暂停": True}, "Running"),
            # R1#2 settle：硬重启 重新可用
            make_snapshot({"硬重启": True, "暂停": False, "恢复": False}, "Running"),
            # 硬重启 post
            make_snapshot({"硬重启": False, "暂停": False, "恢复": False}, "Running"),
        ]
        h = ReconcileHarness(monkeypatch, settles, {
            "暂停": [{"success": True}],
            "恢复": [{"success": True}],
            "硬重启": [{"success": True}],
        })
        validated = {
            "暂停": _fail_result(),
            "恢复": _fail_result(),
            "硬重启": _fail_result(),
        }
        buttons = {"暂停": make_btn(), "恢复": make_btn(), "硬重启": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"暂停": True, "硬重启": True, "恢复": False},
                "Paused": {"恢复": True, "硬重启": False, "暂停": False},
            },
        })
        executed = set()
        await ce._state_aware_reconcile(**h.args(
            validated, buttons, executed=executed, state_ctx=ctx))
        seq = [a for a, _ in h.calls]
        assert len(seq) == 3 and set(seq) == {"暂停", "恢复", "硬重启"}
        assert seq[1] == "恢复", "窗口优先：解锁项立即抢占消费"
        # 出列语义：post 态不可用的旧 ready 残留当轮出列，不得在翻转轮
        # 内消费——其执行只能发生在后续 settle/窗口重评之后（settle
        # 调用数 > 2：轮首 settle 与首个 post-settle 之后）
        other = "硬重启" if seq[0] == "暂停" else "暂停"
        third = next(c for c in h.calls if c[0] == other)
        assert third[1] > 2, \
            f"被出列项 {other} 不得在翻转轮内消费（实际 settle 序 {third[1]}）"
        assert "旧 ready 出列" in caplog.text
        assert {"暂停", "恢复", "硬重启"} <= executed

    def test_f1_wired_source(self):
        """源码守卫：动态队列 + 窗口解锁重算结构在 reconcile 内。"""
        src = _src(ce._state_aware_reconcile)
        assert "while qi < len(queue):" in src, "F1 轮内动态队列未接线"
        assert "[WINDOW-UNLOCKED]" in src
        # 解锁项计算含「post 态可用 ∧ 轮首不可用」判据（本次翻转解锁）
        assert "post_states.get(a)" in src
        assert "not states.get(a)" in src


# ============================================================
# F2 过渡态再稳态（P-2 Gap B）
# ============================================================

class TestBatch9F2TransientWait:

    @pytest.mark.asyncio
    async def test_transient_wait_recovers_after_transition(
            self, monkeypatch, caplog):
        """r11 14:14 场景复刻：Stopping 无出边不再整体 break——两次
        TRANSIENT-WAIT 后行回 Running，恢复转移（暂停 Running→Paused）
        驱动 恢复 业务执行闭环。"""
        caplog.set_level(logging.INFO)
        settles = [
            # R1#1：Stopping 过渡态（不采矩阵）
            make_snapshot({"暂停": False, "恢复": False}, "Stopping"),
            # R1#2：仍在 Stopping
            make_snapshot({"暂停": False, "恢复": False}, "Stopping"),
            # R1#3：回 Running，暂停 可用
            make_snapshot({"暂停": True, "恢复": False}, "Running"),
            # 暂停 恢复转移 post（F4 target=Paused 命中）
            make_snapshot({"恢复": True, "暂停": False}, "Paused"),
            # R1#4：Paused，恢复 可用
            make_snapshot({"恢复": True, "暂停": False}, "Paused"),
            # 恢复 业务成功 post（F4 target=Running）
            make_snapshot({"暂停": True, "恢复": False}, "Running"),
            # R1#5：pending 空，结束
            make_snapshot({"暂停": False, "恢复": False}, "Running"),
        ]
        h = ReconcileHarness(monkeypatch, settles, {
            "暂停": [{"success": True}],
            "恢复": [{"success": True}],
        }, collect_matrix=False)
        validated = {"恢复": _fail_result()}
        buttons = {"暂停": make_btn(), "恢复": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"暂停": True, "恢复": False},
                "Paused": {"恢复": True, "暂停": False},
            },
            "state_transitions": [
                {"action": "暂停", "from": "Running", "to": "Paused"},
                {"action": "恢复", "from": "Paused", "to": "Running"},
            ],
        })
        executed = set()
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, executed=executed, state_ctx=ctx))
        assert results["恢复"]["success"] is True
        assert results["暂停"]["_reconcile_meta"]["intent"] == "state_restore"
        assert [a for a, _ in h.calls] == ["暂停", "恢复"]
        assert caplog.text.count("[TRANSIENT-WAIT]") == 2

    @pytest.mark.asyncio
    async def test_transient_wait_bounded_at_two(self, monkeypatch, caplog):
        """Stopping 恒定不消化 → 2 次上限耗尽后按现状 break（不无限等）。"""
        caplog.set_level(logging.INFO)
        settles = [make_snapshot({"恢复": False}, "Stopping")]
        h = ReconcileHarness(monkeypatch, settles, {}, collect_matrix=False)
        validated = {"恢复": _fail_result()}
        buttons = {"恢复": make_btn()}
        ctx = ce._init_state_ctx({
            # Stopping 无行无边、无出边可消化
            "state_button_matrix": {"Running": {"恢复": True}},
        })
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, state_ctx=ctx))
        assert results == {}
        assert h.settle.calls == 3, \
            "TRANSIENT-WAIT 后须再 settle 重评（第 3 次确认后 break）"
        assert caplog.text.count("[TRANSIENT-WAIT]") == 2
        assert "无可用恢复边，结束" in caplog.text

    @pytest.mark.asyncio
    async def test_no_transient_wait_when_status_observed(self, monkeypatch, caplog):
        """status 是已观测稳态（在矩阵中）但无出边 → 不 TRANSIENT，直接
        结束（F2 只对「从未作为稳态入矩阵」的过渡态生效）。"""
        caplog.set_level(logging.INFO)
        settles = [make_snapshot({"恢复": False}, "Paused")]
        h = ReconcileHarness(monkeypatch, settles, {})
        validated = {"恢复": _fail_result()}
        buttons = {"恢复": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"恢复": True},
                "Paused": {"恢复": False},
            },
        })
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, state_ctx=ctx))
        assert results == {}
        assert "[TRANSIENT-WAIT]" not in caplog.text
        assert h.settle.calls == 1

    def test_f2_wired_source(self):
        """源码守卫：F2 分支位于「无可用恢复边」break 之前、判据数据驱动。"""
        src = _src(ce._state_aware_reconcile)
        assert "[TRANSIENT-WAIT]" in src
        assert "status not in matrix" in src, \
            "过渡态判据必须是「未入已观测矩阵」（数据驱动，不预设文本特征）"
        assert "transient_waits < 2" in src, "TRANSIENT-WAIT 必须有界（上限 2 次）"
        # F2 分支先于 break（否则永不生效）
        assert src.index("[TRANSIENT-WAIT]") < src.index("无可用恢复边，结束")


# ============================================================
# F3 转移边质量门（P-2 Gap C）
# ============================================================

class TestBatch9F3EdgeQualityGate:

    def test_is_nav_without_form_branches(self):
        """单源判据分支：非 dict / 非 page-nav / 有表单字段 → 放行；
        page-nav 无表单 → 拦截。"""
        assert ce._is_nav_without_form(None) is False
        assert ce._is_nav_without_form("page-nav") is False
        assert ce._is_nav_without_form({"interaction_mode": "dialog"}) is False
        assert ce._is_nav_without_form(
            {"interaction_mode": "page-nav",
             "nav_info": {"form_fields": [{"label": "规格"}]}}) is False
        assert ce._is_nav_without_form(
            {"interaction_mode": "page-nav", "nav_info": {"form_fields": []}}) is True
        assert ce._is_nav_without_form(
            {"interaction_mode": "page-nav", "nav_info": {}}) is True

    def test_record_gate_blocks_nav_edge(self, caplog):
        """记录门：page-nav 无表单的执行结果不记边（观察巧合不入册）。"""
        caplog.set_level(logging.INFO)
        ctx = ce._init_state_ctx()
        result = {"interaction_mode": "page-nav", "nav_info": {"form_fields": []}}
        ce._record_state_transition(ctx, "网络配置", "Stopping", "Stopped",
                                    result=result)
        assert ctx["state_transitions"] == []
        assert "[EDGE-SKIP-NAV]" in caplog.text

    def test_record_gate_keeps_real_edges(self):
        """dialog 确认类与 result=None（旧调用兼容）照常记边——真边零损失。"""
        ctx1 = ce._init_state_ctx()
        ce._record_state_transition(ctx1, "关机", "Running", "Stopped",
                                     result={"interaction_mode": "dialog"})
        assert {"action": "关机", "from": "Running", "to": "Stopped"} \
            in ctx1["state_transitions"]
        ctx2 = ce._init_state_ctx()
        ce._record_state_transition(ctx2, "开机", "Stopped", "Running",
                                    result=None)
        assert {"action": "开机", "from": "Stopped", "to": "Running"} \
            in ctx2["state_transitions"]

    @pytest.mark.asyncio
    async def test_consume_gate_skips_stale_pseudo_edge(self, monkeypatch, caplog):
        """消费门：R1 边候选循环跳过存量伪边（网络配置 观察巧合边），
        不驱动无意义恢复转移。"""
        caplog.set_level(logging.INFO)
        settles = [make_snapshot({"网络配置": True, "恢复": False}, "Running")]
        h = ReconcileHarness(monkeypatch, settles, {})
        validated = {
            "恢复": _fail_result(),
            # 存量伪边的来源操作：page-nav 无表单结果
            "网络配置": {"interaction_mode": "page-nav",
                         "nav_info": {"form_fields": []}},
        }
        buttons = {"网络配置": make_btn(), "恢复": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"网络配置": True, "恢复": False},
                "Stopped": {"恢复": True},
            },
            "state_transitions": [
                {"action": "网络配置", "from": "Running", "to": "Stopped"},
            ],
        })
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, state_ctx=ctx))
        assert [a for a, _ in h.calls] == [], "伪边不得驱动恢复转移"
        assert "[EDGE-SKIP-NAV]" in caplog.text
        assert results == {}

    @pytest.mark.asyncio
    async def test_consume_gate_keeps_dialog_edges(self, monkeypatch):
        """对照：dialog 确认类边不受消费门误杀，恢复转移照常驱动。"""
        settles = [
            # R1#1：Running，恢复 不可用
            make_snapshot({"网络配置": True, "恢复": False}, "Running"),
            # 网络配置 恢复转移 post（F4 target=Stopped）
            make_snapshot({"恢复": True, "网络配置": False}, "Stopped"),
            # R1#2：Stopped，恢复 可用
            make_snapshot({"恢复": True}, "Stopped"),
            # 恢复 业务成功 post（恢复 边未知，回落双条件）
            make_snapshot({"恢复": False}, "Running"),
            # R1#3：结束轮
            make_snapshot({}, "Running"),
        ]
        h = ReconcileHarness(monkeypatch, settles, {
            "网络配置": [{"success": True}],
            "恢复": [{"success": True}],
        })
        validated = {
            "恢复": _fail_result(),
            "网络配置": {"interaction_mode": "dialog"},
        }
        buttons = {"网络配置": make_btn(), "恢复": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"网络配置": True, "恢复": False},
                "Stopped": {"恢复": True},
            },
            "state_transitions": [
                {"action": "网络配置", "from": "Running", "to": "Stopped"},
            ],
        })
        executed = set()
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, executed=executed, state_ctx=ctx))
        assert [a for a, _ in h.calls] == ["网络配置", "恢复"]
        assert results["网络配置"]["_reconcile_meta"]["intent"] == "state_restore"
        assert results["恢复"]["success"] is True

    def test_f3_wired_source(self):
        """源码守卫：记录门与消费门同判据单源。"""
        src_record = _src(ce._record_state_transition)
        assert "_is_nav_without_form(result)" in src_record
        src_reconcile = _src(ce._state_aware_reconcile)
        assert '_is_nav_without_form(validated.get(edge.get("action")))' \
            in src_reconcile


# ============================================================
# F4 语义目标态等待（P-2 F1 第 5 点）
# ============================================================

class TestBatch9F4SemanticTargetWait:

    def test_semantic_target_for_known_edge(self):
        """语义目标态单源查找：已知边 (action, from) → to；无边/空参 → None。"""
        ctx = ce._init_state_ctx({
            "state_transitions": [
                {"action": "开机", "from": "Stopped", "to": "Running"},
            ],
        })
        assert ce._semantic_target_for(ctx, "开机", "Stopped") == "Running"
        assert ce._semantic_target_for(ctx, "暂停", "Running") is None
        assert ce._semantic_target_for(None, "开机", "Stopped") is None
        assert ce._semantic_target_for(ctx, "", "Stopped") is None
        assert ce._semantic_target_for(ctx, "开机", "") is None

    @pytest.mark.asyncio
    async def test_wait_stable_target_hit_first_scan(self, monkeypatch):
        """初扫已命中语义目标态 → 立即返回（零轮询成本）。"""
        page = FakePage()
        snap = make_snapshot({"关机": True}, "Paused")

        async def fake_scan(p, marker):
            return snap

        monkeypatch.setattr(ce, "_scan_row_state_snapshot", fake_scan)
        out = await ce._wait_row_states_stable(
            page, "vm-001", target_status="Paused")
        assert out["status"] == "Paused"
        assert out["timed_out"] is False
        assert page.sleeps == 0, "初扫已命中目标态不得再轮询"

    @pytest.mark.asyncio
    async def test_wait_stable_target_hit_during_poll(self, monkeypatch):
        """轮询中命中语义目标态 → 命中即返回 + 矩阵采集（语义稳态）。"""
        page = FakePage()
        seq = [
            make_snapshot({"关机": True}, "Running"),
            make_snapshot({"关机": True}, "Stopping"),   # 过渡读数（目标未中）
            make_snapshot({"关机": False}, "Paused"),    # 目标命中
        ]
        scan = ScriptedScan(seq)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", scan)
        ctx = ce._init_state_ctx()
        out = await ce._wait_row_states_stable(
            page, "vm-001", state_ctx=ctx, target_status="Paused")
        assert out["status"] == "Paused"
        assert out["timed_out"] is False
        assert page.sleeps == 2
        # F4 目标命中时矩阵照常采集（Paused 行）
        assert ctx["state_button_matrix"].get("Paused", {}).get("关机") is False

    @pytest.mark.asyncio
    async def test_wait_stable_target_miss_falls_back_dual_condition(
            self, monkeypatch):
        """目标态未出现 → 回落双条件口径（两次相同即稳态返回，不无限等）。"""
        page = FakePage()
        seq = [
            make_snapshot({"关机": True}, "Running"),
            make_snapshot({"关机": True}, "Running"),
            make_snapshot({"关机": True}, "Running"),
        ]
        scan = ScriptedScan(seq)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", scan)
        out = await ce._wait_row_states_stable(
            page, "vm-001", target_status="Paused")
        assert out["status"] == "Running"
        assert out["timed_out"] is False
        assert page.sleeps == 1, "双条件稳态（两次相同）先于目标态兜底"

    @pytest.mark.asyncio
    async def test_wait_stable_without_target_unchanged(self, monkeypatch):
        """target_status=None（既有调用方）→ 纯双条件行为零变化。"""
        page = FakePage()
        seq = [
            make_snapshot({"关机": True}, "Running"),
            make_snapshot({"关机": False}, "Running"),   # states 变化 → 不稳
            make_snapshot({"关机": False}, "Running"),  # 两次相同 → 稳态
        ]
        scan = ScriptedScan(seq)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", scan)
        out = await ce._wait_row_states_stable(page, "vm-001")
        assert out["status"] == "Running"
        assert out["states"]["关机"] is False
        assert out["timed_out"] is False

    def test_f4_wired_source(self):
        """源码守卫：reconcile 两处 post-settle 均接语义目标态。"""
        src = _src(ce._state_aware_reconcile)
        assert "target_status=_semantic_target_for(" in src, \
            "R1 业务执行 post-settle 未接语义目标态"
        assert 'target_status=edge.get("to")' in src, \
            "恢复转移 post-settle 未接语义目标态（edge.to 恒已知）"
        src_wait = _src(ce._wait_row_states_stable)
        assert "target_status" in src_wait


# ============================================================
# G2 增益层 defer（P-1：级联批 / R1）
# ============================================================

class TestBatch9G2CascadeDefer:

    def _patch_batch(self, monkeypatch, scan_seq, results_by_action):
        """_check_and_execute_newly_enabled 打桩：扫描序列 + 执行脚本。"""
        scan = ScriptedScan(scan_seq)
        monkeypatch.setattr(ce, "_scan_all_button_states", scan)

        async def fake_settle(page, created_marker, state_ctx=None, **kw):
            return {"states": {"重启": True, "删除": True},
                    "status": "Running", "timed_out": False}

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

    @pytest.mark.asyncio
    async def test_cascade_defers_destroy(self, monkeypatch, caplog):
        """批循环 defer：newly_enabled=['重启','删除'] → 执行 重启、
        defer 删除（不入 executed_actions、不执行）。"""
        caplog.set_level(logging.INFO)
        # 首扫两项新启用；重启 成功后递归批扫描无新启用（结束递归）
        _, calls = self._patch_batch(
            monkeypatch, [{"重启": True, "删除": True}, {}],
            {"重启": [{"success": True}]})
        deferred = []
        executed = set()
        button_states = {"重启": False, "删除": False}
        buttons = {"重启": make_btn(), "删除": make_btn()}
        results = await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, buttons, executed, None,
            "", "", {}, "[t] ", attempted_actions={}, deferred_destroy=deferred)
        assert calls == ["重启"], "destroy 类必须在批循环入口 defer，不执行"
        assert deferred == ["删除"]
        assert "删除" not in executed
        assert "重启" in executed
        assert "[DESTROY-DEFER]" in caplog.text
        assert "删除" not in results

    @pytest.mark.asyncio
    async def test_recursive_batch_same_guard(self, monkeypatch):
        """递归批同守卫：递归扫描新发现的 删除 同样 defer（不批内执行）。"""
        # 首扫 重启 新启用 → 执行成功 → 递归批扫出 删除 新启用
        _, calls = self._patch_batch(
            monkeypatch, [{"重启": True}, {"删除": True}, {}],
            {"重启": [{"success": True}]})
        deferred = []
        executed = set()
        button_states = {"重启": False, "删除": False}
        buttons = {"重启": make_btn(), "删除": make_btn()}
        await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, buttons, executed, None,
            "", "", {}, "[t] ", attempted_actions={}, deferred_destroy=deferred)
        assert calls == ["重启"], "递归批内 destroy 类同样必须 defer"
        assert deferred == ["删除"]
        assert "删除" not in executed

    @pytest.mark.asyncio
    async def test_defer_idempotent_dedup(self, monkeypatch):
        """幂等去重：删除 已在 deferred_destroy → 不重复 append。"""
        self._patch_batch(monkeypatch, [{"删除": True}], {})
        deferred = ["删除"]
        executed = set()
        button_states = {"删除": False}
        buttons = {"删除": make_btn()}
        await ce._check_and_execute_newly_enabled(
            FakePage(), "vm-001", button_states, buttons, executed, None,
            "", "", {}, "[t] ", attempted_actions={}, deferred_destroy=deferred)
        assert deferred == ["删除"]

    @pytest.mark.asyncio
    async def test_r1_ready_filters_destroy(self, monkeypatch, caplog):
        """R1 ready 过滤：删除 当前已启用也不直接执行（G2 第一道）。"""
        caplog.set_level(logging.INFO)
        settles = [make_snapshot({"删除": True}, "Running")]
        h = ReconcileHarness(monkeypatch, settles, {"删除": [{"success": True}]})
        validated = {"删除": _fail_result()}
        buttons = {"删除": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {"Running": {"删除": True}},
        })
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, state_ctx=ctx))
        assert [a for a, _ in h.calls] == [], "R1 不得直接执行删除"
        assert results == {}

    @pytest.mark.asyncio
    async def test_r1_recovery_not_driven_for_destroy(self, monkeypatch):
        """R1 恢复驱动过滤：删除 在其他态可用也不为其跑恢复转移
        （为删除翻转状态 = 无意义状态翻转）。"""
        settles = [make_snapshot({"删除": False, "开机": True}, "Running")]
        h = ReconcileHarness(monkeypatch, settles, {})
        validated = {"删除": _fail_result()}
        buttons = {"删除": make_btn(), "开机": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"开机": True},
                "Stopped": {"删除": True},
            },
            "state_transitions": [
                {"action": "开机", "from": "Running", "to": "Stopped"},
            ],
        })
        results = await ce._state_aware_reconcile(**h.args(
            validated, buttons, state_ctx=ctx))
        assert [a for a, _ in h.calls] == [], "不得为删除跑恢复转移"
        assert results == {}

    def test_g2_wired_source(self):
        """源码守卫：批循环 + R1 ready + R1 needed 三处 destroy 过滤接线。"""
        src_batch = _src(ce._check_and_execute_newly_enabled)
        assert "if is_destroy_text(text):" in src_batch
        assert "[DESTROY-DEFER]" in src_batch
        assert "deferred_destroy=deferred_destroy" in src_batch, \
            "递归批必须传递 deferred_destroy"
        src_recon = _src(ce._state_aware_reconcile)
        # ready 过滤（直接执行路径）
        assert "and not is_destroy_text(a)]" in src_recon
        # needed 过滤（恢复驱动路径）
        assert "if is_destroy_text(action):" in src_recon


# ============================================================
# G1 收尾删除路径（P-1 主修，源码守卫）
# ============================================================

class TestBatch9G1FinalDestroyPath:

    def test_assembly_defers_delete_actions(self):
        """装配层：删除项不入 ordered_actions（末位排序技巧移除）。"""
        src = _src(ce._validate_single_tab_crud)
        assert "ordered_actions.extend(_delete_actions)" not in src, \
            "删除不得再入主循环序（改由收尾结构保证最后执行）"
        assert "deferred_destroy = list(_delete_actions)" in src
        # 其他操作装配时仍排除删除项
        assert "action not in _delete_actions" in src

    def test_final_destroy_after_reconcile_and_clear_search(self):
        """收尾阶段时点：final reconcile → clear_table_search → 收尾删除
        → 未验证 toolbar 记录（删除为最后一个执行记录）。"""
        src = _src(ce._validate_single_tab_crud)
        reconcile_pos = src.rfind("_state_aware_reconcile")   # final reconcile
        clear_pos = src.find("await clear_table_search")      # 收尾清搜索
        final_pos = src.find("[DESTROY-FINAL] 收尾删除清单")
        assert final_pos > clear_pos > reconcile_pos, \
            "收尾删除必须在 final reconcile 与清搜索之后（结构保证删除最后）"
        unval_pos = src.find("记录未验证的 toolbar 按钮")
        assert final_pos < unval_pos, \
            "收尾删除须在未验证 toolbar 记录之前（执行记录末位语义）"
        # 执行记录与行终结
        assert '_record_execution(del_action, "main", "delete")' in src
        assert "已有成功结果，跳过" in src, "幂等防御（既有成功结果不重执行）"
        final_block = src[src.find("★ 批九 G1：收尾删除"):]
        assert "created_marker = None" in final_block, \
            "删除成功后必须置空 marker（行生命周期终结）"

    def test_recollect_and_incremental_route_delete_to_deferred(self):
        """marker 重收集与主循环增量入册：delete 角色改入 deferred_destroy。"""
        src_rediscover = _src(ce._rediscover_marker_dropdowns)
        assert "deferred_destroy.append(text)" in src_rediscover
        assert "insert_pos = -1" in src_rediscover
        assert "deferred_destroy: list" in src_rediscover, \
            "重收集签名须接受 deferred_destroy 清单"
        src_main = _src(ce._validate_single_tab_crud)
        assert "if new_action not in deferred_destroy:" in src_main
        assert "deferred_destroy.append(new_action)" in src_main
        # 主循环两处调用方传参：marker 重收集 + 级联批（批九 G2 修复——
        # 批调用漏传会让批内 defer 的删除项整轮丢失）
        assert src_main.count("deferred_destroy=deferred_destroy") >= 2

    def test_final_destroy_reuses_existing_chain(self):
        """收尾删除复用既有删除链（_do_delete / _error_driven_retry /
        _merge_validated_result），不新增第二套执行逻辑。"""
        src = _src(ce._validate_single_tab_crud)
        final_block = src[src.find("★ 批九 G1：收尾删除"):]
        assert "_do_delete" in final_block
        assert "_error_driven_retry" in final_block
        assert "_merge_validated_result" in final_block
        # 无 marker 的行级删除诚实 skip（不盲执行）
        assert "no_marker_available" in final_block


# ============================================================
# P-5 矩阵全态零观测诚实短路（源码守卫）
# ============================================================

class TestBatch9P5HonestShortcircuit:

    def test_p5_shortcircuit_wired(self):
        src = _src(ce._validate_single_tab_crud)
        assert "[P5-SHORTCIRCUIT]" in src
        assert "state_window_unobserved" in src
        # 矩阵为空不短路（无状态采集环境零变化——estack 类）
        assert "if _matrix_all:" in src
        # 有已观测启用态 → 状态机仍有路径，不短路
        assert "any(row.get(pend_action) for row in _matrix_all.values())" in src
        # destroy 项交收尾删除执行，不短路
        assert "if is_destroy_text(pend_action):" in src
        # 短路发生在 final reconcile 之后（收尾时点）
        assert src.find("[P5-SHORTCIRCUIT]") > src.rfind("_state_aware_reconcile")
        # skipped 终态口径（非 click_failed 判失败）
        assert '"error_type": "skipped"' in src

    def test_p5_merge_not_overwrite_success(self):
        """P-5 短路经 _merge_validated_result（防成功被失败覆盖既有语义）。"""
        src = _src(ce._validate_single_tab_crud)
        p5_block = src[src.find("★ 批九 P-5"):src.find("★ 批五 F2 自清洁")]
        assert "_merge_validated_result(validated, pend_action, _sc)" in p5_block
