"""
test_state_machine.py — 批一（状态机核心）单元测试

测试目标（docs/debug/eis云主机修改方案.md 批次一 1.1-1.5）：
- _init_state_ctx / _record_state_matrix / _record_state_transition（1.3 采集与跨轮累积）
- _collect_state_pending（1.4 pending 回收口径）
- _wait_row_states_stable（1.2 双条件稳态判定 + 超时不硬失败 + 矩阵采集）
- _state_aware_reconcile（1.4 R1 驱动性 / R2 边耗尽 / R3 业务唯一 / 降级路径）
- _wait_qga_ready（1.5 QGA 就绪轮询）
- _error_driven_retry QGA 等待分支（1.5 就绪后重试一次）
"""
import pytest

from core.discovery.operation_executor import crud_executor as ce


# ============================================================
# 测试基建：假 page / 可编程快照序列
# ============================================================

class FakePage:
    """最小 Playwright Page 替身：仅支持 wait_for_timeout 计数。"""

    def __init__(self):
        self.sleeps = 0

    async def wait_for_timeout(self, ms):
        self.sleeps += 1


def make_snapshot(states=None, status=None, qga_ready=None):
    return {
        "states": dict(states or {}),
        "status": status,
        "qga_ready": qga_ready,
    }


class ScriptedSettle:
    """按调用序返回预设 settle 结果（越界重复最后一个）。"""

    def __init__(self, settles):
        self.settles = list(settles)
        self.calls = 0

    async def __call__(self, page, created_marker, state_ctx=None, **kwargs):
        idx = min(self.calls, len(self.settles) - 1)
        self.calls += 1
        snap = self.settles[idx]
        if isinstance(snap, dict) and "timed_out" not in snap:
            snap = {**snap, "timed_out": False}
        if state_ctx is not None and snap.get("status") is not None:
            ce._record_state_matrix(state_ctx, snap["status"], snap.get("states") or {})
        return snap


def make_btn(location="dropdown", text=""):
    return {"location": location, "text": text, "locator": "button:has-text('x')"}


# ============================================================
# 1.3 采集与跨轮累积
# ============================================================

class TestInitStateCtx:

    def test_cold_start(self):
        ctx = ce._init_state_ctx(None)
        # ★ 批十 F5：种子三键（maintenance_edges 随批十新增，冷启动空清单）
        assert ctx == {"state_button_matrix": {}, "state_transitions": [],
                       "maintenance_edges": []}

    def test_valid_seed(self):
        seed = {
            "state_button_matrix": {"Running": {"关机": True, "重启": True}},
            "state_transitions": [{"action": "关机", "from": "Running", "to": "Stopped"}],
        }
        ctx = ce._init_state_ctx(seed)
        assert ctx["state_button_matrix"]["Running"]["关机"] is True
        assert ctx["state_transitions"][0]["to"] == "Stopped"

    def test_malformed_seed_filtered(self):
        seed = {
            "state_button_matrix": {"Running": "not-a-dict", "Stopped": {"开机": 1}},
            "state_transitions": ["garbage", {"action": "开机", "from": "Stopped", "to": "Running"}],
        }
        ctx = ce._init_state_ctx(seed)
        assert "Running" not in ctx["state_button_matrix"]
        assert ctx["state_button_matrix"]["Stopped"] == {"开机": 1}
        assert ctx["state_transitions"] == [{"action": "开机", "from": "Stopped", "to": "Running"}]

    def test_transition_dedup_preserve_order(self):
        seed = {
            "state_transitions": [
                {"action": "关机", "from": "Running", "to": "Stopped"},
                {"action": "关机", "from": "Running", "to": "Stopped"},  # 重复
                {"action": "开机", "from": "Stopped", "to": "Running"},
            ]
        }
        ctx = ce._init_state_ctx(seed)
        assert [(e["action"], e["from"], e["to"]) for e in ctx["state_transitions"]] == [
            ("关机", "Running", "Stopped"),
            ("开机", "Stopped", "Running"),
        ]


class TestRecordStateMatrix:

    def test_new_buttons_merged_and_latest_wins(self):
        ctx = ce._init_state_ctx()
        ce._record_state_matrix(ctx, "Running", {"关机": True, "重启": True})
        ce._record_state_matrix(ctx, "Running", {"关机": False, "标签管理": True})
        assert ctx["state_button_matrix"]["Running"] == {
            "关机": False, "重启": True, "标签管理": True,
        }

    def test_guards(self):
        ctx = ce._init_state_ctx()
        ce._record_state_matrix(ctx, None, {"a": True})       # status 不可读
        ce._record_state_matrix(ctx, "Running", {})            # states 为空
        ce._record_state_matrix(None, "Running", {"a": True})  # 无 ctx
        assert ctx["state_button_matrix"] == {}


class TestRecordStateTransition:

    def test_record_and_dedup(self):
        ctx = ce._init_state_ctx()
        ce._record_state_transition(ctx, "关机", "Running", "Stopped")
        ce._record_state_transition(ctx, "关机", "Running", "Stopped")  # 重复
        # ★ 批十 F7：记边即带 validated 标志（记边前提＝该 action 刚执行
        #   成功；消费端以此做存量伪边首碰拦截）
        assert ctx["state_transitions"] == [
            {"action": "关机", "from": "Running", "to": "Stopped",
             "validated": True}
        ]

    def test_guards(self):
        ctx = ce._init_state_ctx()
        ce._record_state_transition(ctx, "X", None, "Stopped")   # from 不可读
        ce._record_state_transition(ctx, "X", "Running", None)   # to 不可读
        ce._record_state_transition(ctx, "X", "Running", "Running")  # 两端相同
        ce._record_state_transition(None, "X", "Running", "Stopped")  # 无 ctx
        assert ctx["state_transitions"] == []


# ============================================================
# 1.4 pending 回收口径
# ============================================================

class TestCollectStatePending:

    def _validated(self, **kwargs):
        return {
            "success": kwargs.get("success", False),
            "error_type": kwargs.get("error_type", ""),
            "error_text": kwargs.get("error_text", ""),
        }

    def _setup(self, monkeypatch):
        buttons = {"重启": make_btn(), "编辑": make_btn(), "新增": make_btn(location="toolbar")}
        monkeypatch.setattr(ce, "_infer_action_role", lambda *a, **k: "generic")
        return buttons

    def test_collects_state_blocked_row_generic(self, monkeypatch):
        buttons = self._setup(monkeypatch)
        validated = {"重启": self._validated(error_type="skipped", error_text="按钮被禁用")}
        pending = ce._collect_state_pending(validated, buttons, {}, set())
        assert pending == ["重启"]

    def test_click_failed_signature(self, monkeypatch):
        buttons = self._setup(monkeypatch)
        validated = {"重启": self._validated(error_type="click_failed")}
        assert ce._collect_state_pending(validated, buttons, {}, set()) == ["重启"]

    def test_excludes_non_state_signatures(self, monkeypatch):
        buttons = self._setup(monkeypatch)
        validated = {
            "重启": self._validated(error_type="no_signal"),
            "重启2": self._validated(error_type="skipped", error_text="其他原因"),
        }
        assert ce._collect_state_pending(validated, buttons, {}, set()) == []

    def test_excludes_success_executed_toolbar(self, monkeypatch):
        buttons = self._setup(monkeypatch)
        validated = {
            "重启": self._validated(success=True),                       # 已成功
            "编辑": self._validated(error_type="click_failed"),          # 已以业务身份成功过
            "新增": self._validated(error_type="click_failed"),          # toolbar 位置
        }
        executed = {"编辑": True}
        assert ce._collect_state_pending(validated, buttons, {}, executed) == []

    def test_collects_update_role_row_pending(self, monkeypatch):
        """批五 F5-2：update 角色行级操作（click_failed）也进 pending。

        r7 实证 修改Hostname（update 角色 dropdown 位置）skipped 后未进
        pending、reconcile 永无恢复机会——角色条件放宽到 {generic, update}，
        位置约束（toolbar 不回收）不变。
        """
        buttons = self._setup(monkeypatch)
        validated = {"编辑": self._validated(error_type="click_failed")}
        assert ce._collect_state_pending(
            validated, buttons, {"编辑": "update"}, set()) == ["编辑"]


# ============================================================
# 1.2 双条件稳态判定
# ============================================================

class TestWaitRowStatesStable:

    def _patch_snapshots(self, monkeypatch, snapshots):
        seq = ScriptedSnapshots(snapshots)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", seq)
        return seq

    @pytest.mark.asyncio
    async def test_stable_when_status_and_menu_both_equal(self, monkeypatch):
        snap = make_snapshot({"关机": True}, "Running")
        seq = self._patch_snapshots(monkeypatch, [snap, snap, snap])
        page = FakePage()
        ctx = ce._init_state_ctx()
        result = await ce._wait_row_states_stable(page, "m", state_ctx=ctx,
                                                   max_wait_ms=20000, poll_interval_ms=100)
        assert result["timed_out"] is False
        assert result["status"] == "Running"
        assert result["states"] == {"关机": True}
        # settle 点采集矩阵（1.3）
        assert ctx["state_button_matrix"]["Running"] == {"关机": True}

    @pytest.mark.asyncio
    async def test_menu_stable_but_status_flipping_not_stable(self, monkeypatch):
        # 菜单集合不变但 status 变化（过渡态）→ 不稳，直到 status 连续两次相同
        menu = {"关机": True, "重启": True}
        seq = self._patch_snapshots(monkeypatch, [
            make_snapshot(menu, "Running"),
            make_snapshot(menu, "Stopping"),   # status 变化 → 不稳
            make_snapshot(menu, "Stopped"),    # status 又变 → 不稳
            make_snapshot(menu, "Stopped"),     # 连续两次相同 → 稳
        ])
        page = FakePage()
        result = await ce._wait_row_states_stable(page, "m", max_wait_ms=60000, poll_interval_ms=100)
        assert result["timed_out"] is False
        assert result["status"] == "Stopped"
        assert page.sleeps >= 3

    @pytest.mark.asyncio
    async def test_status_unreadable_falls_back_to_menu_condition(self, monkeypatch):
        menu = {"冻结": True}
        seq = self._patch_snapshots(monkeypatch, [
            make_snapshot(menu, None),
            make_snapshot({"冻结": False}, None),   # 菜单变化 → 不稳
            make_snapshot(menu, None),              # 菜单两次一致 → 稳（estack 降级路径）
        ])
        result = await ce._wait_row_states_stable(FakePage(), "m", max_wait_ms=60000, poll_interval_ms=100)
        assert result["timed_out"] is False
        assert result["states"] == menu

    @pytest.mark.asyncio
    async def test_timeout_not_hard_fail(self, monkeypatch):
        # status/菜单永不重复 → 超时，timed_out=True 返回最后观测（不抛异常）
        seq = AlternatingSnapshots([
            make_snapshot({"关机": True}, "Running"),
            make_snapshot({"关机": False}, "Stopping"),
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", seq)
        result = await ce._wait_row_states_stable(FakePage(), "m", max_wait_ms=250, poll_interval_ms=100)
        assert result["timed_out"] is True
        assert result["states"]  # 返回最后一次观测

    @pytest.mark.asyncio
    async def test_row_unreachable_returns_empty(self, monkeypatch):
        seq = self._patch_snapshots(monkeypatch, [make_snapshot(), make_snapshot()])
        result = await ce._wait_row_states_stable(FakePage(), "m", poll_interval_ms=100)
        assert result["states"] == {}
        assert result["status"] is None


class ScriptedSnapshots:
    """按调用序返回预设快照（越界重复最后一个）。"""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    async def __call__(self, page, created_marker):
        idx = min(self.calls, len(self.snapshots) - 1)
        self.calls += 1
        return self.snapshots[idx]


class AlternatingSnapshots:
    """按调用序循环返回快照（永不收敛，用于超时路径）。"""

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    async def __call__(self, page, created_marker):
        snap = self.snapshots[self.calls % len(self.snapshots)]
        self.calls += 1
        return snap


# ============================================================
# 1.4 状态感知调度器
# ============================================================

class TestStateAwareReconcile:

    def _patch_common(self, monkeypatch, settles, results_by_action):
        """打桩 settle / 执行链。results_by_action: {action: [result1, result2...]}"""
        settle_seq = ScriptedSettle(settles)
        monkeypatch.setattr(ce, "_wait_row_states_stable", settle_seq)

        # 批七 F2 预检①（marker 行存在）需要真 page；本组 page=None 替身
        # 聚焦 R1/R2/R3 调度语义（预检门契约由 test_batch7_fixes.py 专项覆盖）
        async def fake_row_sel(page, marker):
            return "#row-stub"

        monkeypatch.setattr(ce, "get_row_selector_by_marker", fake_row_sel)

        async def fake_install(page):
            pass

        monkeypatch.setattr(ce, "_install_message_capture", fake_install)

        calls = []

        async def fake_retry(page, fn, context):
            action = context["action"]
            script = results_by_action.get(action) or [{"success": False, "error_text": "无脚本"}]
            idx = calls.count(action)
            calls.append(action)
            return script[min(idx, len(script) - 1)]

        monkeypatch.setattr(ce, "_error_driven_retry", fake_retry)
        return settle_seq, calls

    def _reconcile_args(self, validated, buttons, roles=None, executed=None, state_ctx=None):
        return dict(
            page=None, created_marker="vm-001",
            validated=validated, buttons_by_action=buttons,
            action_roles=roles or {}, executed_actions=executed if executed is not None else set(),
            form_filler=None, tab_name="", tab_locator="",
            tab_prefix="[t] ", trigger_action="关机",
            state_ctx=state_ctx if state_ctx is not None else ce._init_state_ctx(),
        )

    @pytest.mark.asyncio
    async def test_no_pending_returns_empty(self, monkeypatch):
        self._patch_common(monkeypatch, [make_snapshot({"关机": True}, "Running")], {})
        validated = {"关机": {"success": True}}
        buttons = {"关机": make_btn()}
        assert await ce._state_aware_reconcile(**self._reconcile_args(validated, buttons)) == {}

    @pytest.mark.asyncio
    async def test_degraded_ready_execution_without_status(self, monkeypatch):
        """status 不可读 → 降级为仅「当前已启用」直接执行（estack 零降级等价）。"""
        settles = [make_snapshot({"编辑": True, "重启": False}, None)]
        self._patch_common(monkeypatch, settles, {"编辑": [{"success": True, "marker": "vm-001"}]})
        validated = {"编辑": {"success": False, "error_type": "click_failed"}}
        buttons = {"编辑": make_btn(), "重启": make_btn()}
        executed = set()
        ctx = ce._init_state_ctx()
        results = await ce._state_aware_reconcile(**self._reconcile_args(
            validated, buttons, executed=executed, state_ctx=ctx))
        assert "编辑" in results
        assert results["编辑"]["success"] is True
        assert results["编辑"]["_reconcile_meta"]["intent"] == "business_validate"
        assert "编辑" in executed
        # status 不可读 → 无转移边、无矩阵
        assert ctx["state_transitions"] == []
        assert ctx["state_button_matrix"] == {}

    @pytest.mark.asyncio
    async def test_recovery_transfer_with_drive(self, monkeypatch):
        """R1 恢复转移：blocked pending 需求驱动 + 已知边 → 转移后解锁业务执行。"""
        settles = [
            make_snapshot({"开机": True, "重启": False}, "Stopped"),   # 第 1 轮 settle
            make_snapshot({"开机": False, "重启": True}, "Running"),   # 开机成功后 post-settle
            make_snapshot({"开机": False, "重启": True}, "Running"),   # 第 2 轮 settle
            make_snapshot({"开机": False, "重启": False}, "Running"),  # 重启成功后 post-settle
        ]
        self._patch_common(monkeypatch, settles, {
            "开机": [{"success": True}],
            "重启": [{"success": True}],
        })
        validated = {"重启": {"success": False, "error_type": "skipped", "error_text": "按钮被禁用"}}
        buttons = {"开机": make_btn(), "重启": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"重启": True, "开机": False},
                "Stopped": {"开机": True, "重启": False},
            },
            # ★ 批十 F7：真边种子带 validated 标志（无标志边被首碰拦截）
            "state_transitions": [{"action": "开机", "from": "Stopped",
                                   "to": "Running", "validated": True}],
        })
        executed = set()
        results = await ce._state_aware_reconcile(**self._reconcile_args(
            validated, buttons, executed=executed, state_ctx=ctx))
        # 恢复转移 + 解锁业务执行
        assert results["开机"]["_reconcile_meta"] == {"triggered_by": "重启", "intent": "state_restore"}
        assert results["开机"]["success"] is True
        assert results["重启"]["_reconcile_meta"]["intent"] == "business_validate"
        assert results["重启"]["success"] is True
        assert {"开机", "重启"} <= executed
        # 转移边：开机 Stopped→Running（本调用观测）
        assert {"action": "开机", "from": "Stopped", "to": "Running",
                "validated": True} in ctx["state_transitions"]

    @pytest.mark.asyncio
    async def test_r2_edge_exhausted_after_unlocked_all_fail(self, monkeypatch):
        """R2 边耗尽：恢复到达后解锁 pending 全失败 → 同边不再走（循环终止）。"""
        settles = [
            make_snapshot({"开机": True, "重启": False}, "Stopped"),
            make_snapshot({"开机": False, "重启": True}, "Running"),
            make_snapshot({"开机": False, "重启": True}, "Running"),
            make_snapshot({"开机": False, "重启": True}, "Running"),
        ]
        self._patch_common(monkeypatch, settles, {
            "开机": [{"success": True}],
            "重启": [{"success": False, "error_type": "click_failed", "error_text": "过渡窗口"}],
        })
        validated = {"重启": {"success": False, "error_type": "skipped", "error_text": "按钮被禁用"}}
        buttons = {"开机": make_btn(), "重启": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {
                "Running": {"重启": True},
                "Stopped": {"开机": True, "重启": False},
            },
            # ★ 批十 F7：真边种子带 validated 标志（无标志边被首碰拦截）
            "state_transitions": [{"action": "开机", "from": "Stopped",
                                   "to": "Running", "validated": True}],
        })
        executed = set()
        results = await ce._state_aware_reconcile(**self._reconcile_args(
            validated, buttons, executed=executed, state_ctx=ctx))
        assert results["开机"]["success"] is True          # 恢复转移执行了
        assert results["重启"]["success"] is False          # 解锁 pending 失败被记录
        # "重启" 无业务成功、不 executed（R2 全失败语义）
        assert "重启" not in executed

    @pytest.mark.asyncio
    async def test_r3_business_identity_once_per_call(self, monkeypatch):
        """R3 业务唯一：失败后同一调用内不再业务执行（避免无进展循环）。"""
        settles = [make_snapshot({"编辑": True}, "Running")]  # 恒可点
        _, calls = self._patch_common(monkeypatch, settles, {
            "编辑": [{"success": False, "error_type": "click_failed"}],
        })
        validated = {"编辑": {"success": False, "error_type": "click_failed"}}
        buttons = {"编辑": make_btn()}
        results = await ce._state_aware_reconcile(**self._reconcile_args(validated, buttons))
        assert calls.count("编辑") == 1
        assert results["编辑"]["success"] is False

    @pytest.mark.asyncio
    async def test_no_gratuitous_transition_without_pending_need(self, monkeypatch):
        """R1 驱动性：pending 在当前状态已可用（矩阵显示）→ 不走恢复转移。"""
        settles = [make_snapshot({"开机": True, "编辑": False}, "Stopped")]
        _, calls = self._patch_common(monkeypatch, settles, {})
        validated = {"编辑": {"success": False, "error_type": "skipped", "error_text": "按钮被禁用"}}
        buttons = {"开机": make_btn(), "编辑": make_btn()}
        ctx = ce._init_state_ctx({
            "state_button_matrix": {"Stopped": {"编辑": True}},   # 矩阵说 Stopped 可用
            # ★ 批十 F7：真边种子带 validated——F7 首碰拦截不适用（真边），
            #   无驱动（无 needed）依然零转移（R1 驱动性守卫更强口径）
            "state_transitions": [{"action": "开机", "from": "Stopped",
                                   "to": "Running", "validated": True}],
        })
        results = await ce._state_aware_reconcile(**self._reconcile_args(validated, buttons, state_ctx=ctx))
        # 菜单显示编辑禁用 → 不进 ready；矩阵说当前可用 → 不构成 needed → 结束，无任何执行
        assert calls == []
        assert results == {}


# ============================================================
# 1.5 QGA 就绪等待
# ============================================================

class TestWaitQgaReady:

    @pytest.mark.asyncio
    async def test_qga_true_returns_true(self, monkeypatch):
        async def snap(page, marker):
            return make_snapshot({}, "Running", True)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        assert await ce._wait_qga_ready(FakePage(), "m") is True

    @pytest.mark.asyncio
    async def test_qga_unreadable_returns_false_immediately(self, monkeypatch):
        """qga 不可读 → 立即放弃（不做全局硬等，无 QGA 镜像不挂死）。"""
        async def snap(page, marker):
            return make_snapshot({}, "Running", None)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        page = FakePage()
        assert await ce._wait_qga_ready(page, "m", timeout_ms=60000) is False
        assert page.sleeps == 0

    @pytest.mark.asyncio
    async def test_qga_false_then_true(self, monkeypatch):
        seq = ScriptedSnapshots([
            make_snapshot({}, "Running", False),
            make_snapshot({}, "Running", False),
            make_snapshot({}, "Running", True),
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", seq)
        assert await ce._wait_qga_ready(FakePage(), "m", timeout_ms=60000) is True
        assert seq.calls == 3

    @pytest.mark.asyncio
    async def test_qga_timeout(self, monkeypatch):
        async def snap(page, marker):
            return make_snapshot({}, "Running", False)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        assert await ce._wait_qga_ready(FakePage(), "m", timeout_ms=100, poll_interval_ms=50) is False


class TestErrorDrivenRetryQgaWait:
    """1.5：_error_driven_retry QGA 等待分支（就绪后重试一次）。"""

    @pytest.mark.asyncio
    async def test_qga_failure_waits_then_retries(self, monkeypatch):
        attempts = []

        async def operation_fn(page, context):
            attempts.append(1)
            if len(attempts) == 1:
                return {"success": False, "error_type": "no_signal",
                        "error_text": "QGA 未就绪，请稍后重试"}
            return {"success": True, "marker": "vm-001"}

        qga_calls = []

        async def fake_qga_wait(page, marker, timeout_ms=60000, poll_interval_ms=5000):
            qga_calls.append((marker, timeout_ms))
            return True

        monkeypatch.setattr(ce, "_wait_qga_ready", fake_qga_wait)
        result = await ce._error_driven_retry(
            FakePage(), operation_fn, {"marker": "vm-001", "btn": {"text": "修改Hostname"}})
        assert result["success"] is True
        assert len(attempts) == 2          # 失败 1 次 + QGA 就绪后重试 1 次
        assert qga_calls == [("vm-001", 60000)]

    @pytest.mark.asyncio
    async def test_non_qga_error_no_wait(self, monkeypatch):
        async def operation_fn(page, context):
            return {"success": True, "marker": "m"}

        async def fake_qga_wait(page, marker, **kwargs):
            raise AssertionError("非 QGA 错误不应触发等待")

        monkeypatch.setattr(ce, "_wait_qga_ready", fake_qga_wait)
        result = await ce._error_driven_retry(
            FakePage(), operation_fn, {"marker": "m", "btn": {"text": "x"}})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_no_marker_no_qga_wait(self, monkeypatch):
        async def operation_fn(page, context):
            # error_text 含 QGA 但无 marker：走终态错误（api_error），
            # 保证断言聚焦「QGA 等待未被触发」这一件事本身
            return {"success": False, "error_type": "api_error", "error_text": "QGA 未就绪"}

        async def fake_qga_wait(page, marker, **kwargs):
            raise AssertionError("无 marker 不应触发 QGA 等待")

        monkeypatch.setattr(ce, "_wait_qga_ready", fake_qga_wait)
        result = await ce._error_driven_retry(FakePage(), operation_fn, {"btn": {"text": "x"}})
        assert result["error_type"] == "api_error"
