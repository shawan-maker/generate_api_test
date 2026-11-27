# -*- coding: utf-8 -*-
"""
test_batch11_fixes.py — 批十一-A（r13 13 项回归同根源头修复）单元测试

方案：docs/debug/eis云主机批十一源头方案.md（§三 修复设计 / §四 探针结论）

F1 menuId 抓取扩源 vm.popperElm.id 兜底（P1 主修）
  - 探针三连定案：Element UI popper.js 首次 vm.show() 把菜单容器从 cell
    移动到 document.body（此后 cell 查询域恒空），vm.show() 真实生效但
    三链行内查询（menuId→行 scope→dropdownIndex）全空 → has_visible_menu
    =False → status=None → R1 整轮判降级（r13 十轮 menuId=None 7 轮 +
    pending 终局 14 根因）。vm.popperElm 是 VuePopper mixin 实例引用，
    show 前后全程恒指本行菜单（P1c 双时点取证），其 id 即全页唯一菜单
    id——消费端 getElementById 链零改动即全链复活。EIS 双形态定案：
    SEARCH-FIRST 过滤触发行重渲染后 = Element UI 原生 el-dropdown（有
    popperElm）；自研 custom-el-dropdown / estack 无 popperElm 形态
    返回 undefined 静默回落既有链（零回归）。

F3 复核门条件扩为「menuId 空」+ R1 status=None 单轮有界重读（P1 辅修）
  - r13 证伪批十-B「vue_show 路径 9 轮实证可靠」注释：十轮中 7 轮
    menuId=None（appendToBody 后 cell 域恒空），vue_show 轮与
    dispatchEvent 轮的可靠性分界实为「menuId 是否可得」。复核门从
    method 判据改为 menuId 判据——F1 扩源后 menuId 恒非空（组件异常
    除外），复核从「盲等」降级为「组件异常兜底」。
  - R1-REREAD：settle 轮询窗恰落在「行重渲染/菜单重建」瞬态时 status
    读 None 即整轮判降级（r13 型）。本轮内重扫一次 snapshot：得值即
    证明行已恢复可读，continue 回轮首重新 settle（ready/恢复转移按
    全新视图计算，不用重读局部数据拼接调度）；仍 None 按既有降级语义
    结束。有界性由既有 max_rounds 12 轮挂死保险保证。

F2（hover 与 click 路径同源）已作废：探针定案 Playwright 真 hover 对
EIS dropdown 触发器结构性死路（actionability element is not visible，
十轮+两种形态复刻均超时）——原 F2 设计前提不成立，不入本批。
"""
import inspect
import logging

import pytest

from core.discovery import const as const_mod
from core.discovery.operation_executor import crud_executor as ce


def _src(fn):
    return inspect.getsource(fn)


# ============================================================
# 测试基建（同 test_batch10_fixes.py）
# ============================================================

class FakePage:
    """最小 Playwright Page 替身：仅支持 wait_for_timeout 计数。"""

    def __init__(self):
        self.sleeps = 0

    async def wait_for_timeout(self, ms):
        self.sleeps += 1


class _ScanCapturePage:
    """evaluate 捕获替身：记录渲染 JS + 按调用序返回预设结果。"""

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
        idx = min(self._i, len(self._results) - 1)
        self._i += 1
        return self._results[idx]

    async def wait_for_timeout(self, ms):
        pass


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


class SnapshotScript:
    """_scan_row_state_snapshot 序列替身（越界重复最后一个）。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    async def __call__(self, page, created_marker):
        idx = min(self.calls, len(self.seq) - 1)
        self.calls += 1
        return dict(self.seq[idx])


def make_btn(location="dropdown", text=""):
    return {"location": location, "text": text, "locator": "button:has-text('x')"}


def patch_reconcile(monkeypatch, settle_seq, results_by_action):
    """_state_aware_reconcile 打桩（同批十基建）。"""
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
# 批十一 F1 menuId 扩源 vm.popperElm.id 兜底（源码守卫）
# ============================================================

class TestBatch11F1PopperelmFallback:

    def test_both_expand_segments_have_popperelm_fallback(self):
        """F1 扩源落地于两条扫描链的展开段（增量扫描 + snapshot）——
        popperElm.id 兜底 + data-popper-id 次级，形态同源。"""
        inc_src = _src(ce._scan_row_after_operation)
        snap_src = _src(ce._scan_row_state_snapshot)
        for src, dom_var in ((inc_src, "_domMenu"), (snap_src, "_domMenu2")):
            assert f"{dom_var} = dropdown.querySelector('.el-dropdown-menu')" in src, \
                f"{dom_var} cell 域直查必须保留（命中轮零变化）"
            assert "popperElm?.id" in src, \
                f"{dom_var} 空时必须兜底 vm.popperElm.id（appendToBody 形态）"
            assert "popperElm?.getAttribute?.('data-popper-id')" in src, \
                f"{dom_var} 空且 id 缺失时的次级兜底必须保留"
            # 扩源必须是 ternary 兜底形态：cell 命中优先、popperElm 仅在
            # cell 域空时启用（domMenu 命中轮行为零变化）
            assert f"const menuId = {dom_var}" in src
            ternary_pos = src.find(f"const menuId = {dom_var}")
            popper_pos = src.find("popperElm?.id", ternary_pos)
            assert 0 < ternary_pos < popper_pos, \
                "menuId 定义必须先于 popperElm 兜底引用（ternary 结构）"

    def test_f1_comment_documents_appendtobody_mechanism(self):
        """F1 注释须记录 popper.js appendToBody 机制与 P1c 探针定案
        （防后人按「cell 域空=展开失败」误判回退扩源）。"""
        inc_src = _src(ce._scan_row_after_operation)
        snap_src = _src(ce._scan_row_state_snapshot)
        assert "批十一 F1" in inc_src and "批十一 F1" in snap_src
        assert "appendToBody" in inc_src, "增量段注释须记录移动机制"
        # 「无 popperElm 形态静默回落」承诺必须双侧在案（estack 零回归）
        assert inc_src.count("自研组件") >= 1
        assert snap_src.count("静默回落") >= 1

    def test_snapshot_menuid_captured_after_show(self):
        """snapshot 展开段 menuId 抓取在 vm.show() 之后——popperElm
        在 show 后必然就绪（P1c 定案：show 前亦恒指本行菜单，但 show 后
        抓取对齐「首次 show 即移动到 body」的最坏时序）。"""
        snap_src = _src(ce._scan_row_state_snapshot)
        show_pos = snap_src.find("vm.show();")
        dom_pos = snap_src.find("_domMenu2 = dropdown.querySelector")
        assert 0 < show_pos < dom_pos, \
            "snapshot 段 menuId 须在 vm.show() 之后抓取（popperElm 已就绪）"

    def test_consume_chain_getelementbyid_unchanged(self):
        """消费端定位链零改动断言：getElementById + data-popper-id
        属性选择器形态双侧在位（F1 只改抓取端、消费端链复活）。
        注：f-string 源码字面含 {{ }} 转义（inspect.getsource 原样）。"""
        inc_src = _src(ce._scan_row_after_operation)
        snap_src = _src(ce._scan_row_state_snapshot)
        # 增量链两处消费点（可见性判定 + 菜单收集）均须在位（各 1 次）
        assert inc_src.count('document.getElementById(params.menuId)') == 2
        assert inc_src.count('[data-popper-id="${{params.menuId}}"]') == 2
        # snapshot 链（局部变量 menuId 形态）
        assert 'document.getElementById(menuId)' in snap_src
        assert '[data-popper-id="${{menuId}}"]' in snap_src

    def test_no_full_page_menu_fallback_regression(self):
        """批十-B「删全页回退」不因 F1 回潮（批五 Gap B 病根禁复辟）。
        代码形态断言（注释可引用历史名不误伤，同批十守卫写法）。"""
        for fn in (ce._scan_row_after_operation, ce._scan_row_state_snapshot):
            src = _src(fn)
            assert "targetMenu = menu;" not in src, \
                f"{fn.__name__} 不得回潮「捡第一个可见 menu」回退赋值"
            assert "Array.from(menus).some" not in src, \
                f"{fn.__name__} 不得回潮全页菜单可见性回退"


# ============================================================
# 批十一 F3 复核门条件扩为「menuId 空」（行为）
# ============================================================

# 全成功路径 evaluate 序（批十一后不变）：row_exists → 直查按钮 →
# SCAN-SWEEP 探测 → 展开结果（含 menuId）→ 可见性 → 菜单收集
_B11_SUCCESS_EVALS = [
    True,                                                   # 0. row_exists
    {"buttons": [], "allTexts": ["取消", "确定"]},          # 1. 直查按钮
    0,                                                      # 2. SCAN-SWEEP
    {"expanded": True, "method": "vue_show",
     "dropdownIndex": 0, "menuId": "menu-abc"},             # 3. 展开
    True,                                                   # 4. 可见性
    {"buttons": [], "menuInfo": [], "allItems": ["退出运维模式"]},  # 5. 收集
]


class TestBatch11F3RecheckGateMenuId:

    @pytest.mark.asyncio
    async def test_menuid_empty_recheck_recovers_and_proceeds(self):
        """menuId 空复核（r13 核心场景复活路径）：初判 False → 复核轮
        转 True → 菜单收集照常执行。menuId 空 = 菜单标识不可得
        （appendToBody 窗口/组件异常），与展开 method 无关。"""
        evals = [
            True,                                                    # 0. row_exists
            {"buttons": [], "allTexts": ["取消", "确定"]},           # 1. 直查按钮
            0,                                                       # 2. SCAN-SWEEP
            {"expanded": True, "method": "vue_show",
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
            "menuId 空复核转 True 后菜单收集必须执行"
        # 可见性 JS 单源复用：初判与复核轮是同一段 JS（防判据漂移）
        visible_js = page.captured[4]
        assert page.captured[5] == visible_js, "复核轮须复用同一可见性 JS"

    @pytest.mark.asyncio
    async def test_menuid_empty_recheck_bounded_at_two(self):
        """menuId 空有界复核：恒 False 最多 2 轮（+ 初判共 3 次可见性
        检查），仍 False 判零收集不进菜单扫描。"""
        evals = list(_B11_SUCCESS_EVALS)
        evals[3] = {"expanded": True, "method": "dispatchEvent",
                    "dropdownIndex": 0, "menuId": None}
        evals[4] = False
        page = _ScanCapturePage(evals + [False, False,  # 复核 2 轮恒 False
                                         {"buttons": [], "menuInfo": [],
                                          "allItems": ["不应收集"]}])
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert len(page.captured) == 7, \
            f"恒 False 须有界于 2 轮复核（共 7 evaluate），实际 {len(page.captured)}"
        assert "不应收集" not in result["all_current_texts"]

    @pytest.mark.asyncio
    async def test_menuid_present_no_recheck_even_dispatchevent(self):
        """批十一行为变化点：menuId 可得即展开真实生效（P1c 定案
        「menuId 是否可得」是可靠性分界，非 method）——dispatchEvent 轮
        menuId 非空 + 初判 False 不复核，直接判零收集（无盲等延迟）。"""
        evals = [
            True,                                                    # 0. row_exists
            {"buttons": [], "allTexts": ["取消", "确定"]},           # 1. 直查按钮
            0,                                                       # 2. SCAN-SWEEP
            {"expanded": True, "method": "dispatchEvent",
             "dropdownIndex": 0, "menuId": "menu-abc"},              # 3. 展开
            False,                                                   # 4. 初判 False
            {"buttons": [], "menuInfo": [],
             "allItems": ["不应收集"]},                              # 5. 不应触达
        ]
        page = _ScanCapturePage(evals)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert len(page.captured) == 5, \
            f"menuId 非空不得触发复核（5 evaluate），实际 {len(page.captured)}"
        assert "不应收集" not in result["all_current_texts"]

    @pytest.mark.asyncio
    async def test_success_path_evaluate_sequence_unchanged(self):
        """vue_show + menuId 非空全成功路径：evaluate 序保持 6 次
        （F1/F3 不增加成功路径开销）。"""
        page = _ScanCapturePage(_B11_SUCCESS_EVALS)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        assert len(page.captured) == 6, \
            f"成功路径 evaluate 数须保持 6，实际 {len(page.captured)}"
        assert "退出运维模式" in result["all_current_texts"]

    def test_recheck_gate_condition_is_menuid_not_method(self):
        """源码守卫：复核门判据是 menuId 空（not expand_result.get
        ('menuId')），旧 method=='dispatchEvent' 判据不得回潮。"""
        src = _src(ce._scan_row_after_operation)
        assert "not expand_result.get('menuId')" in src
        # 旧判据形态禁复辟（method 判据已证伪：r13 vue_show 7 轮 menuId=None）
        assert "get('method') == 'dispatchEvent'" not in src
        assert "menuId 空复核" in src, "复核日志签名须体现 menuId 判据"

    def test_recheck_gate_comment_documents_r13_falsification(self):
        """源码守卫：复核门注释须记录批十-B 注释证伪与机制勘误
        （r13 7/10 None + appendToBody 定案），防按旧注释回退判据。"""
        src = _src(ce._scan_row_after_operation)
        assert "批十一 F3" in src
        assert "证伪" in src
        assert "menuId 是否可得" in src


# ============================================================
# 批十一 F3 R1 status=None 单轮有界重读（行为）
# ============================================================

class TestBatch11F3R1Reread:

    @pytest.mark.asyncio
    async def test_reread_rescues_and_business_executes(
            self, monkeypatch, caplog):
        """重读得值 → continue 回轮首重新 settle → ready 按全新视图
        计算 → 业务执行（r13 型断链修复主链路：settle 瞬态 None 轮
        不再把 pending 拖到终局）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [
            # 第 1 轮：settle 瞬态窗 status=None（states 非空垃圾面）
            {"states": {"取消": True}, "status": None,
             "qga_ready": None, "maintenance": None, "timed_out": False},
            # 第 2 轮（rescue continue 后）：settle 恢复可读
            {"states": {"开机": True}, "status": "Stopped",
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        settle, calls = patch_reconcile(
            monkeypatch, settle_seq, {"开机": [{"success": True}]})
        snapshot = SnapshotScript([
            # R1-REREAD 重读：行已恢复可读（status 得值）
            {"states": {"开机": True}, "vue_states": {},
             "status": "Stopped", "qga_ready": None, "maintenance": None},
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snapshot)

        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn()}
        state_ctx = ce._init_state_ctx()
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=state_ctx, attempted_actions={})

        assert calls == ["开机"], "rescue 后下一轮 settle 须计算出 ready 并执行"
        assert results["开机"]["success"] is True
        assert "[STATE-RECONCILE][R1-REREAD]" in caplog.text
        assert "status=None 单轮重读: status=Stopped" in caplog.text
        assert "无 ready 且状态不可读，结束（降级）" not in caplog.text
        assert snapshot.calls == 1, "重读必须单次（不进循环）"
        # settle 4 次 = rescue 轮 1 + 下一轮首 1 + 执行成功后 post 快照 1
        # （批七 F2-2 预检刷新）+ 终止轮 1（无 ready 无可恢复 → 既有结束）
        assert settle.calls == 4, \
            f"rescue 轮 + 执行轮首尾 + 终止轮共 4 次 settle，实际 {settle.calls}"

    @pytest.mark.asyncio
    async def test_reread_still_none_degrades(self, monkeypatch, caplog):
        """重读仍 None → 既有降级语义零变化（break 结束、无业务执行）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [
            {"states": {"取消": True}, "status": None,
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        _, calls = patch_reconcile(monkeypatch, settle_seq, {})
        snapshot = SnapshotScript([
            {"states": {}, "vue_states": {}, "status": None,
             "qga_ready": None, "maintenance": None},
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snapshot)

        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn()}
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=ce._init_state_ctx(), attempted_actions={})

        assert calls == [], "重读仍 None 不得执行任何业务"
        # reconcile results 只写入本轮执行过的项——降级未执行项无键
        # （调用方保留 validated 原失败值，无假成功覆盖）
        assert "开机" not in results
        assert "[STATE-RECONCILE][R1-REREAD]" in caplog.text
        assert "无 ready 且状态不可读，结束（降级）" in caplog.text
        assert snapshot.calls == 1

    @pytest.mark.asyncio
    async def test_no_reread_without_state_ctx(self, monkeypatch, caplog):
        """state_ctx 缺失时恢复转移无矩阵可用，重读无意义——不触发
        _scan_row_state_snapshot，直接降级（原语义零变化）。"""
        caplog.set_level(logging.INFO)
        settle_seq = [
            {"states": {"取消": True}, "status": None,
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        _, calls = patch_reconcile(monkeypatch, settle_seq, {})
        snapshot = SnapshotScript([
            {"states": {}, "vue_states": {}, "status": None,
             "qga_ready": None, "maintenance": None},
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snapshot)

        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn()}
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=None, attempted_actions={})

        assert calls == []
        assert snapshot.calls == 0, "state_ctx 缺失不得触发重读"
        assert "无 ready 且状态不可读，结束（降级）" in caplog.text
        assert "[R1-REREAD]" not in caplog.text

    @pytest.mark.asyncio
    async def test_reread_bounded_by_max_rounds(self, monkeypatch):
        """有界性：settle 恒 None + 重读恒得值（持续瞬态极端场景）时
        rescue-continue 受 max_rounds 挂死保险约束，不进死循环。"""
        settle_seq = [
            {"states": {"取消": True}, "status": None,
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        settle, calls = patch_reconcile(monkeypatch, settle_seq, {})
        snapshot = SnapshotScript([
            {"states": {}, "vue_states": {}, "status": "Stopped",
             "qga_ready": None, "maintenance": None},
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snapshot)

        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn()}
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=ce._init_state_ctx(), attempted_actions={},
            max_rounds=3)

        # 3 轮预算耗尽即退出：每轮恰好 1 次 settle + 1 次重读
        assert settle.calls == 3
        assert snapshot.calls == 3
        assert calls == [], "恒瞬态场景无业务可执行，不得虚耗执行"

    @pytest.mark.asyncio
    async def test_reread_rescue_not_infinite_when_settle_recovers(
            self, monkeypatch):
        """rescue 幂等收敛：settle 第 2 轮恢复可读且无 ready 无可恢复
        pending 时按既有「无可恢复 pending，结束」退出——rescue 不重写
        调度、不绕过既有终止条件。"""
        settle_seq = [
            {"states": {"取消": True}, "status": None,
             "qga_ready": None, "maintenance": None, "timed_out": False},
            # rescue 后下一轮可读，但 states 无 pending 项、无可恢复边
            {"states": {"取消": True}, "status": "Running",
             "qga_ready": None, "maintenance": None, "timed_out": False},
        ]
        settle, calls = patch_reconcile(monkeypatch, settle_seq, {})
        snapshot = SnapshotScript([
            {"states": {}, "vue_states": {}, "status": "Running",
             "qga_ready": None, "maintenance": None},
        ])
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snapshot)

        validated = {"开机": _failed_pending_result()}
        buttons = {"开机": make_btn()}
        # 空 state_ctx：无可恢复转移边（needed 空 → 既有结束路径）
        results = await ce._state_aware_reconcile(
            FakePage(), "test663637", validated, buttons, {}, set(),
            None, "", "", "[t] ",
            state_ctx=ce._init_state_ctx(), attempted_actions={})

        assert calls == [], "无可恢复 pending 时不得执行"
        assert snapshot.calls == 1
        assert "开机" not in results, "未执行项不得入 results（无假成功覆盖）"

    def test_r1_reread_wiring_guard(self):
        """源码守卫：R1-REREAD 单次调用（不在循环内）+ rescue →
        continue 回轮首 + 降级日志保持既有签名。"""
        src = _src(ce._state_aware_reconcile)
        assert "[STATE-RECONCILE][R1-REREAD]" in src
        assert "批十一 F3" in src
        # rescue → continue（回轮首重新 settle，不用局部数据拼接调度）
        reread_pos = src.find("_reread_rescued = _re_status is not None")
        continue_pos = src.find("continue", reread_pos)
        degrade_pos = src.find("无 ready 且状态不可读，结束（降级）")
        assert 0 < reread_pos < continue_pos < degrade_pos, \
            "rescue continue 必须先于降级 break"
        # 重读单次有界：调用不在 for/while 体内（块内无第二次调用）
        block = src[reread_pos:degrade_pos]
        assert block.count("_scan_row_state_snapshot") == 0, \
            "R1-REREAD 经局部变量调用（块内无裸函数名重复）"
        assert block.count("await _scan_row_state_snapshot") == 0
        # 完整块内恰好一次重读调用（在 try 内）
        full_block = src[src.find("★ 批十一 F3：status=None"):degrade_pos]
        assert full_block.count("await _scan_row_state_snapshot") == 1, \
            "重读必须恰好一次（单轮有界）"

    def test_reread_comment_documents_design_rationale(self):
        """源码守卫：R1-REREAD 注释须记录 continue 设计理由（不用重读
        局部数据拼接调度）与有界性依据（max_rounds 保险）。"""
        src = _src(ce._state_aware_reconcile)
        assert "继续" in src or "continue" in src
        assert "不用重读局部数据拼接调度" in src
        assert "12 轮" in src, "有界性依据（max_rounds 挂死保险）须在案"


# ============================================================
# 批十一 F2 勘误固化（hover 死路不作修复项）
# ============================================================

class TestBatch11F2Falsified:
    """F2（hover 与 click 路径同源）作废勘误——探针定案 Playwright
    真 hover 对 EIS dropdown 触发器结构性死路（actionability element
    is not visible），扫描路径 hover 尝试块保持现状不扩展。"""

    def test_hover_block_not_extended_by_batch11(self):
        """扫描路径 hover 尝试块无批十一改动（F2 作废、hover 维持
        批十 F4 形态——分两步走计划中的 evaluate 侧先行终点）。"""
        src = _src(ce._scan_row_after_operation)
        assert "批十一" not in src[:src.find("★ 批十 F4")], \
            "hover 段不得混入批十一改动"
