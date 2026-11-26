# -*- coding: utf-8 -*-
"""
test_batch8_fixes.py — 批八（r10 验收 P1/P2 源头修复）单元测试

修复1（P2 增量扫描展开可靠性）：_scan_row_after_operation 真实 hover 先行展开
  - r10 定案：F6 导航往返（go_back）后页面重渲染窗口内行 .el-dropdown 的
    __vue__ 不可用，JS 展开链退化 dispatchEvent 且菜单不开（has_visible_menu=
    False），r10 两次扫描（11:34:08 / 11:47:10）一无所获，「退出运维模式」
    终身未入册（11:49:33 菜单枚举证实已在 DOM）；r9 同结构扫描三次全部
    vue_show 成功（16:56:25 捕获 new_buttons=['退出运维模式']）。
  - hover 与点击路径 click_row_more_item 展开策略同源（同窗口全程实证
    可用 expand_strategy='hover'）；hover 先行打开菜单，原 JS 链保持原序
    兜底（vue_show 幂等、合成 mouseenter 不收起已开菜单）；hover 失败不
    阻断扫描。

修复2（P1 守卫 B 有界轮询）：_dialog_still_open_check 500ms×6 轮询替代
  一次快照
  - r10 定案：重装操作系统/创建快照 3/3 假失败——提交点击后业务通知在
    判定后 ~1s 到达（11:36:52 判败 → 11:36:53「操作系统已重装」）、弹窗
    随即关闭；r9 同链 500-600ms 内关弹窗静默放行（重装走二次确认通道）。
    一次 500ms 快照对 EIS 后端处理延迟过紧（批五 F3 设计弱点，r10 运维
    模式慢环境放大成 6 假败，非批七代码回归——守卫 B 本体与三调用点
    批七未动已 diff 实证）。
  - 轮询至弹窗指纹变化（实际关闭）即提前放行：快路径成本不变；真失败
    仅多 ~2.5s 判定延迟；二次确认层每轮检测、层出现即点击且单次（防重复
    提交）。
"""
import inspect

import pytest

from core.discovery.operation_executor import crud_executor as ce
from core.discovery.replay import form_filler as ff_mod


def _src(fn):
    return inspect.getsource(fn)


# ============================================================
# 测试基建
# ============================================================

class _ScanHoverLocator:
    """扫描 hover 替身：记录 hover 到 page 事件流，可配置失败。"""

    def __init__(self, page, selector, fail=False):
        self._page = page
        self._selector = selector
        self._fail = fail

    @property
    def first(self):
        return self

    async def hover(self, timeout=None):
        if self._fail:
            raise RuntimeError("hover target detached (mock)")
        self._page.events.append(("hover", self._selector))


class ScanFakePage:
    """_scan_row_after_operation 替身：evaluate 序列 + hover/evaluate 事件流。"""

    def __init__(self, evaluate_results=None, hover_fail=False):
        self._evals = list(evaluate_results or [])
        self._eval_calls = 0
        self.events = []          # 有序事件流：('hover', selector) / ('evaluate', i)
        self._hover_fail = hover_fail

    def locator(self, selector):
        return _ScanHoverLocator(self, selector, fail=self._hover_fail)

    async def evaluate(self, *args, **kwargs):
        self.events.append(("evaluate", self._eval_calls))
        if self._evals:
            idx = min(self._eval_calls, len(self._evals) - 1)
            value = self._evals[idx]
        else:
            value = None
        self._eval_calls += 1
        return value

    async def wait_for_timeout(self, ms):
        pass


class GuardFakePage:
    """守卫 B 替身：只数 wait_for_timeout 轮次。"""

    def __init__(self):
        self.sleeps = 0

    async def wait_for_timeout(self, ms):
        self.sleeps += 1


# _scan_row_after_operation 的 evaluate 调用序（行存在 → 直查按钮 → 展开结果
# → 菜单可见 → 菜单按钮收集），全成功路径预设：
_SCAN_EVALS = [
    True,                                                  # 1. row_exists
    {"buttons": [], "allTexts": ["取消", "确定"]},          # 2. 直查按钮
    {"expanded": True, "method": "vue_show", "dropdownIndex": 2},  # 3. 展开结果
    True,                                                  # 4. has_visible_menu
    {"buttons": [], "menuInfo": [], "allItems": ["关机"]},  # 5. 菜单收集
]


def _make_guard_ff(monkeypatch, fp_values, dbl_values=None):
    """构造守卫 B 测试 FormFiller。

    fp_values: 循环内 _active_dialog_fingerprint 按调用序（每轮一次）返回值
                （末位重复）；fp_before 由测试另行直传，不在序列内。
    dbl_values: _detect_and_click_double_confirm 按调用序返回值（末位重复），
                None=恒未命中。
    """
    page = GuardFakePage()
    ff = ff_mod.FormFiller(page)
    fp_seq = list(fp_values)
    dbl_seq = list(dbl_values or [{"found": False, "clicked": False}])

    async def fake_fp(self):
        v = fp_seq.pop(0) if len(fp_seq) > 1 else fp_seq[0]
        _make_guard_ff.fp_calls[0] += 1
        return v

    async def fake_dbl(self):
        v = dbl_seq.pop(0) if len(dbl_seq) > 1 else dbl_seq[0]
        _make_guard_ff.dbl_calls[0] += 1
        return v

    _make_guard_ff.fp_calls = [0]
    _make_guard_ff.dbl_calls = [0]

    async def no_wait(page):
        pass

    monkeypatch.setattr(ff_mod.FormFiller, "_active_dialog_fingerprint", fake_fp)
    monkeypatch.setattr(ff_mod.FormFiller, "_detect_and_click_double_confirm", fake_dbl)
    monkeypatch.setattr(
        "core.discovery.replay.wait_helpers.wait_for_loading_complete", no_wait)
    return ff, page


# ============================================================
# 修复1：增量扫描真实 hover 先行展开（P2 源头）
# ============================================================

class TestBatch8Fix1ScanHoverFirst:

    @pytest.mark.asyncio
    async def test_hover_precedes_js_expansion(self):
        """hover 必须发生在 JS 展开链（expand_result evaluate）之前。"""
        page = ScanFakePage(evaluate_results=_SCAN_EVALS)
        await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        hovers = [e for e in page.events if e[0] == "hover"]
        assert hovers, "扫描未做真实 hover 展开先行"
        assert hovers[0][1] == ".el-table__row:nth-child(1) .el-dropdown", \
            "hover 必须锚定行内 .el-dropdown（与点击路径同源策略）"
        # hover 事件必须先于第 3 次 evaluate（展开结果采集）
        hover_idx = page.events.index(hovers[0])
        expand_idx = [i for i, e in enumerate(page.events)
                      if e[0] == "evaluate" and e[1] == 2][0]
        assert hover_idx < expand_idx, "hover 先行被破坏（JS 链先跑）"

    @pytest.mark.asyncio
    async def test_hover_failure_non_blocking(self):
        """hover 抛异常（元素重渲染脱靶）→ 扫描不炸、JS 兜底链照常执行。"""
        page = ScanFakePage(evaluate_results=_SCAN_EVALS, hover_fail=True)
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(1)", "test663637", {"关机"})
        # 展开链 evaluate（第 3 次）仍被调用
        assert any(e == ("evaluate", 2) for e in page.events), \
            "hover 失败后 JS 兜底展开链未执行"
        assert result is not None

    @pytest.mark.asyncio
    async def test_hover_absent_row_survives(self):
        """行不存在 → 扫描幂等返回空结果（既有前置行为不回归）。"""
        page = ScanFakePage(evaluate_results=[False])
        result = await ce._scan_row_after_operation(
            page, ".el-table__row:nth-child(9)", "nope", set())
        assert result == {"new_buttons": [], "disappeared_buttons": [],
                          "all_current_texts": set()}
        assert not [e for e in page.events if e[0] == "hover"], \
            "行不存在时不得 hover"

    def test_hover_wired_source(self):
        """源码守卫：hover 先行存在且 try 包裹（失败不阻断）。"""
        src = _src(ce._scan_row_after_operation)
        assert "_dd_loc.hover(timeout=2000)" in src, "真实 hover 先行未接线"
        assert "真实 hover 展开失败（JS 兜底链继续）" in src, \
            "hover 失败必须走 JS 兜底链而非中断"
        # hover 块在 JS 展开链 evaluate 之前
        assert src.index("_dd_loc.hover") < src.index("expand_result = await page.evaluate")


# ============================================================
# 修复2：守卫 B 有界轮询（P1 源头）
# ============================================================

class TestBatch8Fix2GuardBPolling:

    @pytest.mark.asyncio
    async def test_fast_close_round1_pass(self, monkeypatch):
        """弹窗首轮内关闭 → 放行，且只睡 1 轮（快路径成本不变）。"""
        ff, page = _make_guard_ff(monkeypatch, [""])
        ret = await ff._dialog_still_open_check({}, "dialog|重装操作系统")
        assert ret["submit_blocked"] is None
        assert ret["blocked_reason"] == ""
        assert page.sleeps == 1

    @pytest.mark.asyncio
    async def test_slow_close_passes(self, monkeypatch):
        """r10 假败实证形态：弹窗在 ~1s（第 2 轮）才关 → 必须放行。

        重装 #1：11:36:52 判定时弹窗仍开、11:36:53 通知到达弹窗关闭——
        旧单快照在此判 dialog_still_open 假失败（6 假败之一）。
        """
        ff, page = _make_guard_ff(
            monkeypatch, ["dialog|重装操作系统", ""])
        ret = await ff._dialog_still_open_check({}, "dialog|重装操作系统")
        assert ret["submit_blocked"] is None, "第 2 轮关闭的慢弹窗被误判滞留"
        assert page.sleeps == 2

    @pytest.mark.asyncio
    async def test_never_closes_bounded_judgement(self, monkeypatch):
        """真失败：弹窗恒开 → 6 轮有界后判 dialog_still_open（不无限等）。"""
        ff, page = _make_guard_ff(monkeypatch, ["dialog|创建快照"])
        ret = await ff._dialog_still_open_check({}, "dialog|创建快照")
        assert ret["submit_blocked"] == "dialog_still_open"
        assert "提交未生效" in ret["blocked_reason"]
        assert page.sleeps == 6, "轮询必须有界（500ms×6）"

    @pytest.mark.asyncio
    async def test_double_confirm_single_click_then_close(self, monkeypatch):
        """二次确认层出现即点击且仅一次；弹窗随后关闭 → 放行并记录层信息。"""
        ff, page = _make_guard_ff(
            monkeypatch,
            fp_values=["dialog|重置密码", ""],
            dbl_values=[{"found": True, "clicked": True,
                         "layer_tag": "DIV[role=tooltip]", "button_text": "确认"},
                        {"found": False, "clicked": False}])
        ret = await ff._dialog_still_open_check({}, "dialog|重置密码")
        assert ret["submit_blocked"] is None
        assert ret["double_confirm_clicked"] is True
        assert ret["double_confirm_layer_tag"] == "DIV[role=tooltip]"
        assert _make_guard_ff.dbl_calls[0] == 1, \
            "二次确认层被重复点击（防重复提交被破坏）"

    @pytest.mark.asyncio
    async def test_double_confirm_late_layer_caught(self, monkeypatch):
        """层晚到（第 1 轮无、第 2 轮出现）→ 每轮检测兜住，不漏层。"""
        ff, page = _make_guard_ff(
            monkeypatch,
            fp_values=["dialog|删除", "dialog|删除", ""],
            dbl_values=[{"found": False, "clicked": False},
                        {"found": True, "clicked": True,
                         "layer_tag": "DIV[role=tooltip]", "button_text": "确认"},
                        {"found": False, "clicked": False}])
        ret = await ff._dialog_still_open_check({}, "dialog|删除")
        assert ret["submit_blocked"] is None
        assert ret["double_confirm_clicked"] is True

    @pytest.mark.asyncio
    async def test_double_confirm_still_open_judged(self, monkeypatch):
        """二次确认已点但弹窗 6 轮仍滞留 → 维持 dialog_still_open 判定。"""
        ff, page = _make_guard_ff(
            monkeypatch,
            fp_values=["dialog|删除"],
            dbl_values=[{"found": True, "clicked": True,
                         "layer_tag": "DIV[role=tooltip]", "button_text": "确认"}])
        ret = await ff._dialog_still_open_check({}, "dialog|删除")
        assert ret["submit_blocked"] == "dialog_still_open"
        assert ret["double_confirm_clicked"] is True
        assert _make_guard_ff.dbl_calls[0] == 1, "层已处理后不得重复点击"

    @pytest.mark.asyncio
    async def test_no_fp_before_passthrough(self, monkeypatch):
        """点击前无弹窗（toolbar 即时提交类）→ 不轮询直接放行（既有语义）。"""
        ff, page = _make_guard_ff(monkeypatch, [""])
        ret = await ff._dialog_still_open_check({}, "")
        assert ret["submit_blocked"] is None
        assert page.sleeps == 0

    @pytest.mark.asyncio
    async def test_fingerprint_exception_releases(self, monkeypatch):
        """指纹采集异常 → 放行（守卫不得引入新失败模式）。"""
        page = GuardFakePage()

        async def boom(self):
            raise RuntimeError("evaluate failed")

        async def no_wait(p):
            pass

        monkeypatch.setattr(ff_mod.FormFiller, "_active_dialog_fingerprint", boom)
        monkeypatch.setattr(
            "core.discovery.replay.wait_helpers.wait_for_loading_complete", no_wait)
        ff = ff_mod.FormFiller(page)
        ret = await ff._dialog_still_open_check({}, "dialog|X")
        assert ret["submit_blocked"] is None

    def test_polling_wired_source(self):
        """源码守卫：有界轮询结构 + 单次二次确认防重 + 提前退出。"""
        src = _src(ff_mod.FormFiller._dialog_still_open_check)
        assert "for _attempt in range(6)" in src, "500ms×6 有界轮询未接线"
        assert "dbl_handled" in src, "二次确认单次点击防重未接线"
        assert src.index("if fp_after != fp_before:\n                    break") < \
            src.index("dbl = await self._detect_and_click_double_confirm()"), \
            "指纹变化必须先于层检测退出（防残留层误点）"
