"""
test_batch7_fixes.py — 批七（r9 遗留源头治理）分批单元测试

G1 组（调度治理）：
- F1 克隆产物就绪等待：const.CLONE_ACTION_KEYWORDS 单源；
  _wait_clone_product_ready（行出现主信号 / 克隆中文案拒放行 / Vue 过渡
  枚举拒放行 / 超时不挂死 / 列表视图恢复 / 观测入产物）；
  form_filler.fill_edit_form 三填充点 filled_value 写回
- F2 调度统一预检门：_preflight_executable（Vue disabled prop 权威口径 /
  样式类误杀回归守卫 / 行不存在 SEARCH-FIRST 补救 / 无正向证据放行零误杀 /
  异常放行）+ _error_driven_retry 状态性首败不进第 2 轮 + 三入口源码接线
- _scan_row_state_snapshot vue_states 新口径（并行返回，既有消费点零变化）
- F3 失败二分记忆：TERMINAL_ERROR_TYPES 扩三类 + STATE_PENDING_ERROR_TEXTS
  状态性豁免 + _register_terminal_attempt 豁免逻辑 + main 循环预算接线

G2 组（提交链治理）：F4 Primary 回退弹窗内收紧 + F5 CANCEL_TEXTS 补'关闭'
+ F8 弹窗归属 token 覆盖匹配

G3 组（判定链治理）：
- F6 导航型菜单项分流：无弹窗 URL 跳转 + marker 命中 → 判导航成功不进
  EDIT-FLOW（B3 网络配置 ~4min → ~5s）；结果形态经 builder 序列化
  （click_row_more → wait_for_url → read_complete → navigate_back，
  无虚假「确定」点击步骤）
- F7 凭证回显保留：登录名/用户名类字段有非空 currentValue（弹窗回显）
  → fixed_value 保留不覆盖（B1 重置密码 dialog_still_open ×5 根治）；
  双源同步（generate_fill_value / _infer_fill_rule）+ Stage 2 回放闭环
- F9 成功信号优先：已捕获 success 通知 > DOM field 红字（B4 退出运维模式
  实证：先扫红字后读消息的早退点把业务成功判死）；推翻时裁决依据记入
  adjudication_note 流入 ui.json
"""
import inspect

import pytest

from core.discovery import const
from core.discovery.const import (
    CLONE_ACTION_KEYWORDS,
    STATE_PENDING_ERROR_TEXTS,
    TERMINAL_ERROR_TYPES,
)
from core.discovery.operation_executor import crud_executor as ce
from core.discovery.replay import form_filler as ff_mod
from core.discovery.shared import fill_helpers


# ============================================================
# 测试基建
# ============================================================

class _FakeLocator:
    """最小 Locator 替身（F4 Primary 回退测试用）。"""

    def __init__(self, page, selector, count=0, visible=False):
        self.page = page
        self.selector = selector
        self._count = count
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return self._count

    async def is_visible(self):
        return self._visible

    async def click(self, *args, **kwargs):
        self.page.locator_clicks.append(self.selector)


class FakePage:
    """最小 Playwright Page 替身。"""

    def __init__(self, evaluate_results=None):
        # evaluate(script, *args) 按调用序返回预设值（越界重复最后一个）
        self._evals = list(evaluate_results or [])
        self._eval_calls = 0
        self.sleeps = 0
        self.fills = []
        self.locator_calls = []
        self.locator_clicks = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        return _FakeLocator(self, selector, count=1, visible=True)

    async def evaluate(self, *args, **kwargs):
        if self._evals:
            idx = min(self._eval_calls, len(self._evals) - 1)
            self._eval_calls += 1
            return self._evals[idx]
        self._eval_calls += 1
        return None

    async def wait_for_timeout(self, ms):
        self.sleeps += 1

    async def fill(self, locator, value, timeout=None):
        self.fills.append((locator, value))


def make_btn(location="dropdown", text=""):
    return {"location": location, "text": text, "locator": "button:has-text('x')"}


def _src(fn):
    return inspect.getsource(fn)


# ============================================================
# F1：克隆产物就绪等待
# ============================================================

class TestF1CloneProduct:

    def test_clone_action_keywords_single_source(self):
        """关键词单源：克隆/clone；「复制」不收（剪贴板语义误判风险）。"""
        assert CLONE_ACTION_KEYWORDS == ('克隆', 'clone')
        assert '复制' not in CLONE_ACTION_KEYWORDS

    def test_extraction_wired_in_generic_operation(self):
        """_do_generic_operation 接线：关键词分流 + filled_value 提取 + 等待挂成功尾。"""
        src = _src(ce._do_generic_operation)
        assert "CLONE_ACTION_KEYWORDS" in src, "克隆关键词分流缺失"
        assert "filled_value" in src, "名称字段填充值提取缺失"
        assert "_wait_clone_product_ready" in src, "成功尾部就绪等待缺失"
        assert 'result["clone_product"]' in src, "产物名未落 result"

    @pytest.mark.asyncio
    async def test_wait_ready_immediate(self, monkeypatch):
        """行出现 + 无克隆中 + Vue status 稳定 → 立即放行；观测入产物。"""
        async def row_sel(page, marker):
            return "#pane1 .el-table__row:nth-child(1)"

        async def snap(page, marker):
            return {"states": {}, "vue_states": {}, "status": "Running", "qga_ready": True}

        searches = []

        async def do_search(page, kw):
            searches.append(kw)
            return True

        async def do_clear(page):
            searches.append("<clear>")
            return True

        from core.discovery.replay import button_driver
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        monkeypatch.setattr(button_driver, "search_filter_table", do_search)
        monkeypatch.setattr(button_driver, "clear_table_search", do_clear)

        page = FakePage(evaluate_results=["test599001 运行中 test999"])
        result = {}
        ok = await ce._wait_clone_product_ready(
            page, "test999", marker="marker-vm", result=result,
            timeout_s=30, interval_s=1)
        assert ok is True
        assert result["provisioning_wait"]["ready"] is True
        assert result["provisioning_wait"]["product"] == "test999"
        # 视图恢复：有 marker → 按 marker 重新过滤（最后一次搜索是 marker）
        assert searches[-1] == "marker-vm"
        assert searches[0] == "test999"

    @pytest.mark.asyncio
    async def test_wait_row_text_cloning_blocks_until_timeout(self, monkeypatch):
        """行文本含「克隆中」→ 不放行至超时；不挂死返回 False。"""
        async def row_sel(page, marker):
            return "#row1"

        from core.discovery.replay import button_driver
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        monkeypatch.setattr(button_driver, "clear_table_search", _noop_true)

        page = FakePage(evaluate_results=["test999 克隆中 test999"])
        result = {}
        ok = await ce._wait_clone_product_ready(
            page, "test999", marker="", result=result, timeout_s=0)
        assert ok is False
        assert "克隆中" in result["provisioning_wait"]["last_observation"]
        assert result["provisioning_wait"]["ready"] is False

    @pytest.mark.asyncio
    async def test_wait_vue_transient_status_blocks(self, monkeypatch):
        """Vue status 过渡枚举（Cloning/Provisioning/Creating）→ 不放行。"""
        async def row_sel(page, marker):
            return "#row1"

        async def snap(page, marker):
            return {"states": {}, "vue_states": {}, "status": "Cloning", "qga_ready": None}

        from core.discovery.replay import button_driver
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        monkeypatch.setattr(button_driver, "clear_table_search", _noop_true)

        page = FakePage(evaluate_results=["test999 test999"])  # 无克隆中文案
        ok = await ce._wait_clone_product_ready(
            page, "test999", marker="", timeout_s=0)
        assert ok is False

    @pytest.mark.asyncio
    async def test_wait_row_never_appears_timeout(self, monkeypatch):
        """产物行始终未出现 → 超时 False（不挂死）。"""
        async def row_sel(page, marker):
            return ""

        from core.discovery.replay import button_driver
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        monkeypatch.setattr(button_driver, "clear_table_search", _noop_true)

        ok = await ce._wait_clone_product_ready(FakePage(), "gone-vm", marker="", timeout_s=0)
        assert ok is False

    @pytest.mark.asyncio
    async def test_wait_status_unreadable_row_present_is_ready(self, monkeypatch):
        """status 不可读（非 dropdown command 环境）→ 行出现即就绪（P-1 定案主信号）。"""
        async def row_sel(page, marker):
            return "#row1"

        async def snap(page, marker):
            return {"states": {}, "vue_states": {}, "status": None, "qga_ready": None}

        from core.discovery.replay import button_driver
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap)
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        monkeypatch.setattr(button_driver, "clear_table_search", _noop_true)

        page = FakePage(evaluate_results=["test999 运行中"])
        ok = await ce._wait_clone_product_ready(page, "test999", marker="", timeout_s=5)
        assert ok is True

    @pytest.mark.asyncio
    async def test_fill_edit_form_writes_back_filled_value(self, monkeypatch):
        """fill_edit_form 三填充路径写回 field['filled_value']（克隆产物名数据源）。"""
        monkeypatch.setattr(fill_helpers, "generate_fill_value",
                            lambda *a, **kw: ("test599002", {"rule": "name_pattern"}))
        monkeypatch.setattr(fill_helpers, "_pair_confirmation_field_values",
                            lambda fields, planned: {})

        page = FakePage()
        filler = ff_mod.FormFiller(page)
        fields = [{
            "label": "名称", "type": "input", "inputType": "text",
            "playwright_locator": "#clone-name", "kb_category": "",
            "inputCount": 1,
        }]
        filled = await filler.fill_edit_form(fields)
        assert filled == 1
        assert fields[0]["filled_value"] == "test599002"

    def test_preflight_gate_not_in_generic_op_when_not_clone(self):
        """非克隆操作不受影响：预检门挂在调度层，_do_generic_operation 内部
        的克隆等待按 _clone_product 空值短路（源码守卫）。"""
        src = _src(ce._do_generic_operation)
        assert 'if _clone_product:' in src


async def _noop_true(*args, **kwargs):
    return True


# ============================================================
# F2：调度统一预检门
# ============================================================

class TestF2Preflight:

    def _patch_row(self, monkeypatch, selector="#row1"):
        async def row_sel(page, marker):
            return selector
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)

    @pytest.mark.asyncio
    async def test_pass_when_no_marker(self):
        """无 marker（无行依赖）→ 直接放行。"""
        out = await ce._preflight_executable(FakePage(), make_btn(), "")
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_block_disabled_vue_prop(self, monkeypatch):
        """Vue disabled prop 权威口径禁用 → 拦截（wait_reason=disabled）。"""
        self._patch_row(monkeypatch)
        snap = {"states": {"重启": False}, "vue_states": {"重启": False}}
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="重启"), "vm-1", snapshot=snap)
        assert out["pass"] is False
        assert out["wait_reason"] == "disabled"
        assert "按钮被禁用" in out["detail"]

    @pytest.mark.asyncio
    async def test_pass_enabled(self, monkeypatch):
        self._patch_row(monkeypatch)
        snap = {"states": {"重启": True}, "vue_states": {"重启": True}}
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="重启"), "vm-1", snapshot=snap)
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_class_disabled_but_vue_enabled_passes(self, monkeypatch):
        """★ r5 回归守卫：可操作互斥项带 cmss-disabled 样式类但 Vue prop 可点
        → 预检（vue_states 权威口径）必须放行，不得按样式类误杀。"""
        self._patch_row(monkeypatch)
        snap = {"states": {"进入运维模式": False},      # 旧口径（含样式类）判禁用
                "vue_states": {"进入运维模式": True}}   # 权威口径可点
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="进入运维模式"), "vm-1", snapshot=snap)
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_missing_text_no_evidence_passes(self, monkeypatch):
        """vue_states 无该按钮文本（DOM 口径漂移）→ 无正向证据放行零误杀。"""
        self._patch_row(monkeypatch)
        snap = {"states": {"别的操作": True}, "vue_states": {"别的操作": True}}
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="不存在于快照"), "vm-1", snapshot=snap)
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_text_normalization_whitespace(self, monkeypatch):
        """文本归一：收集名与 DOM 文本空格差异不漏判。"""
        self._patch_row(monkeypatch)
        snap = {"states": {}, "vue_states": {"更新密钥对": False}}
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="更新 密钥对"), "vm-1", snapshot=snap)
        assert out["pass"] is False
        assert out["wait_reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_row_not_found_after_search_rescue_fails(self, monkeypatch):
        """行不可见 + SEARCH-FIRST 补救仍不可见 → 拦截 row_not_found。"""
        async def row_sel(page, marker):
            return ""
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        from core.discovery.replay import button_driver
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="重启"), "vm-1")
        assert out["pass"] is False
        assert out["wait_reason"] == "row_not_found"

    @pytest.mark.asyncio
    async def test_search_rescue_makes_row_visible_then_pass(self, monkeypatch):
        """行初始不可见 → SEARCH-FIRST 过滤后可见 → 放行。"""
        seq = ["", "#row1"]

        async def row_sel(page, marker):
            return seq.pop(0) if seq else "#row1"
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        from core.discovery.replay import button_driver
        monkeypatch.setattr(button_driver, "search_filter_table", _noop_true)
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="重启"), "vm-1",
            snapshot={"states": {}, "vue_states": {"重启": True}})
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_toolbar_location_skips_disabled_check(self, monkeypatch):
        """toolbar 位置仅行存在检查，不做行内菜单禁用扫描（不同 DOM 无意义）。"""
        self._patch_row(monkeypatch)

        async def snap_fail(page, marker):
            raise AssertionError("toolbar 预检不应触发行内菜单扫描")
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap_fail)
        out = await ce._preflight_executable(
            FakePage(), make_btn(location="toolbar", text="批量启用"), "vm-1")
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_scan_exception_passes(self, monkeypatch):
        """禁用扫描异常 → 放行执行（预检失败安全侧=放行）。"""
        self._patch_row(monkeypatch)

        async def snap_fail(page, marker):
            raise RuntimeError("dom gone")
        monkeypatch.setattr(ce, "_scan_row_state_snapshot", snap_fail)
        out = await ce._preflight_executable(
            FakePage(), make_btn(text="重启"), "vm-1")
        assert out["pass"] is True

    @pytest.mark.asyncio
    async def test_f2_3_state_first_fail_no_second_round(self, monkeypatch):
        """状态性首败（skipped/row_not_found/按钮被禁用）不进第 2 轮完整流程。"""
        for err in (
            {"success": False, "error_type": "skipped", "error_text": "按钮被禁用: 重启"},
            {"success": False, "error_type": "row_not_found", "error_text": "未找到数据行"},
            {"success": False, "error_type": "click_failed", "error_text": "xxx 按钮被禁用 yyy"},
        ):
            calls = []

            async def op(page, context, _err=err):
                calls.append(1)
                return dict(_err)

            async def no_vision(page, context):
                return {}

            async def page_key(page):
                return "s1"

            monkeypatch.setattr(ce, "_vision_analysis", no_vision)
            monkeypatch.setattr(ce, "_get_page_state_key", page_key)
            out = await ce._error_driven_retry(FakePage(), op, {"marker": "vm-1"})
            assert len(calls) == 1, f"状态性首败应只执行 1 轮: {err['error_type']}"
            assert out["error_type"] == err["error_type"]

    @pytest.mark.asyncio
    async def test_non_state_failure_still_retries_round2(self, monkeypatch):
        """非状态性失败（click_failed 无禁用文本）仍走第 2 轮（现状保留）。"""
        calls = []

        async def op(page, context):
            calls.append(1)
            return {"success": False, "error_type": "click_failed",
                    "error_text": "坐标漂移点击失败"}

        async def no_vision(page, context):
            return {}

        async def page_key(page):
            return "s1"

        monkeypatch.setattr(ce, "_vision_analysis", no_vision)
        monkeypatch.setattr(ce, "_get_page_state_key", page_key)
        await ce._error_driven_retry(FakePage(), op, {"marker": "vm-1"})
        assert len(calls) >= 2

    def test_wiring_three_entries(self):
        """三入口接线源码守卫：main 循环（预算 + 预检）/ 批 / reconcile R1。"""
        main_src = _src(ce._validate_single_tab_crud)
        assert "_attempted_exhausted(action, attempted_actions)" in main_src, \
            "F3-3 main 循环预算接线缺失"
        assert "_preflight_executable(" in main_src, "F2-1 main 循环预检门缺失"
        assert "_preflight_executable(" in _src(ce._check_and_execute_newly_enabled), \
            "F2-2 STATE-CHANGE 批预检缺失"
        assert "_preflight_executable(" in _src(ce._state_aware_reconcile), \
            "F2-2 RECONCILE R1 预检缺失"
        # 批内快照缓存 + 成功失效
        batch_src = _src(ce._check_and_execute_newly_enabled)
        assert "_batch_snap" in batch_src
        rec_src = _src(ce._state_aware_reconcile)
        assert "_r1_snap" in rec_src


# ============================================================
# _scan_row_state_snapshot：vue_states 新口径
# ============================================================

class TestVueStatesSnapshot:

    @pytest.mark.asyncio
    async def test_full_scan_returns_vue_states(self, monkeypatch):
        """完整扫描（行按钮 + dropdown 菜单）返回 vue_states 并行口径。"""
        async def row_sel(page, marker):
            return "#row1"

        part1 = {"states": {"开关机": True},
                 "vueStates": {"开关机": True}}
        dropdown_expand = {"expanded": True, "menuId": "menu-1", "dropdownIndex": 0}
        dropdown_states = {"states": {"重启": True, "更新密钥对": False},
                           "vueStates": {"重启": True, "更新密钥对": False},
                           "rowStatus": "Stopped", "qgaReady": False}
        page = FakePage(evaluate_results=[part1, dropdown_expand, dropdown_states])
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        out = await ce._scan_row_state_snapshot(page, "vm-1")
        assert out["vue_states"] == {"开关机": True, "重启": True, "更新密钥对": False}
        assert out["states"] == {"开关机": True, "重启": True, "更新密钥对": False}
        assert out["status"] == "Stopped"
        assert out["qga_ready"] is False

    @pytest.mark.asyncio
    async def test_no_dropdown_returns_part1_vue_states(self, monkeypatch):
        """无 dropdown（非 command 环境）→ vue_states 仍返回行按钮部分。"""
        async def row_sel(page, marker):
            return "#row1"

        part1 = {"states": {"按钮A": True}, "vueStates": {"按钮A": True}}
        page = FakePage(evaluate_results=[part1, {"error": "no_dropdown"}])
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        out = await ce._scan_row_state_snapshot(page, "vm-1")
        assert out["vue_states"] == {"按钮A": True}
        assert out["status"] is None

    @pytest.mark.asyncio
    async def test_row_missing_returns_empty_vue_states(self, monkeypatch):
        async def row_sel(page, marker):
            return ""
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        out = await ce._scan_row_state_snapshot(FakePage(), "vm-1")
        assert out == {"states": {}, "vue_states": {}, "status": None, "qga_ready": None}

    @pytest.mark.asyncio
    async def test_existing_consumers_unchanged(self, monkeypatch):
        """既有消费点零变化：_scan_all_button_states 只投影 states；
        _read_row_status 只取 status/qga。"""
        async def row_sel(page, marker):
            return "#row1"

        part1 = {"states": {"A": True}, "vueStates": {"A": True}}
        dropdown_expand = {"expanded": True, "menuId": None, "dropdownIndex": 0}
        dropdown_states = {"states": {"B": False},
                           "vueStates": {"B": True},
                           "rowStatus": "Running", "qgaReady": True}
        page = FakePage(evaluate_results=[part1, dropdown_expand, dropdown_states])
        monkeypatch.setattr(ce, "get_row_selector_by_marker", row_sel)
        states = await ce._scan_all_button_states(page, "vm-1")
        assert states == {"A": True, "B": False}  # states 口径（含样式类）不变
        # 生产语义：两个投影各自独立扫描（无共享缓存）→ 新替身重喂预设
        page2 = FakePage(evaluate_results=[part1, dropdown_expand, dropdown_states])
        rd = await ce._read_row_status(page2, "vm-1")
        assert rd == {"status": "Running", "qga_ready": True}


# ============================================================
# F3：失败二分 + 记忆全阶段贯穿
# ============================================================

class TestF3Memory:

    def test_whitelist_expanded_nine_types(self):
        """F3-1：白名单六类 → 九类（dialog_still_open/readonly_dialog_error/api_error）。"""
        assert TERMINAL_ERROR_TYPES == frozenset({
            'required_field_empty', 'form_validation', 'no_confirm_button',
            'confirm_word_missing', 'no_fields', 'dialog_mismatch',
            'dialog_still_open', 'readonly_dialog_error', 'api_error',
        })

    def test_state_pending_error_texts_single_source(self):
        """F3-2 豁免表单源五词（与方案 §F3-2 逐字一致）。"""
        assert STATE_PENDING_ERROR_TEXTS == ('qga 未就绪', '克隆中', '请等待', '未就绪', '正在')

    def test_exempt_state_pending_not_registered(self):
        """状态性失败（QGA 未就绪/克隆中/正在/请等待）不入册，保留状态机窗口。"""
        cases = [
            {"success": False, "error_type": "api_error", "error_text": "QGA 未就绪，请稍后"},
            {"success": False, "error_type": "api_error", "error_text": "克隆中, 请等待克隆完成"},
            {"success": False, "error_type": "dialog_still_open", "error_text": "xxx 正在执行"},
            {"success": False, "error_type": "api_error", "error_text": "资源未就绪"},
        ]
        attempted = {}
        for res in cases:
            assert not ce._register_terminal_attempt("重启", res, attempted)
        assert attempted == {}

    def test_terminal_api_error_registered(self):
        """非状态性 api_error（配额/权限类）正常入册。"""
        attempted = {}
        assert ce._register_terminal_attempt(
            "克隆", {"success": False, "error_type": "api_error",
                     "error_text": "磁盘配额不足，无法创建云主机"}, attempted)
        assert attempted["克隆"] == 1

    def test_dialog_still_open_registered(self):
        """F3-1 核心：dialog_still_open（提交未生效）入册——换状态必同败。"""
        attempted = {}
        assert ce._register_terminal_attempt(
            "重置密码",
            {"success": False, "error_type": "dialog_still_open",
             "error_text": "提交未生效: 登录名与云主机不符"}, attempted)
        assert attempted["重置密码"] == 1

    def test_readonly_dialog_error_registered(self):
        attempted = {}
        assert ce._register_terminal_attempt(
            "执行命令",
            {"success": False, "error_type": "readonly_dialog_error",
             "error_text": "只读弹窗检出失败信号"}, attempted)
        assert attempted["执行命令"] == 1

    def test_state_blocked_types_still_not_registered(self):
        """click_failed/skipped/no_signal 仍不入册（状态机域自管理，批六口径保留）。"""
        attempted = {}
        for et in ("click_failed", "skipped", "no_signal"):
            assert not ce._register_terminal_attempt(
                "开机", {"success": False, "error_type": et,
                         "error_text": "过渡窗口"}, attempted)
        assert attempted == {}

    def test_lowercase_matching_for_qga(self):
        """英文关键字 lower 匹配（'QGA 未就绪' 与 'qga 未就绪' 同豁免）。"""
        attempted = {}
        for text in ("QGA 未就绪", "qga 未就绪", "Qga 未就绪"):
            assert not ce._register_terminal_attempt(
                "重装操作系统",
                {"success": False, "error_type": "api_error", "error_text": text},
                attempted)
        assert attempted == {}


# ============================================================
# G2：F4 Primary 回退 scope 收紧 / F5 关闭词表 / F8 弹窗归属 token 覆盖
# ============================================================

class TestF4PrimaryScope:

    @pytest.mark.asyncio
    async def test_no_active_dialog_disables_fallback(self, monkeypatch):
        """无活跃弹窗 → Primary 回退禁用（r9 A3 全页误点防回归），不触碰 locator。"""
        async def no_fp(self):
            return ""

        async def no_guard(self, scope_selector=""):
            return None

        async def no_detect(self, scope_selector=""):
            return ""

        async def not_clicked(page, text, **kwargs):
            return {"clicked": False}

        monkeypatch.setattr(ff_mod.FormFiller, "_active_dialog_fingerprint", no_fp)
        monkeypatch.setattr(ff_mod.FormFiller, "_submit_button_disabled_guard", no_guard)
        monkeypatch.setattr(ff_mod.FormFiller, "_detect_submit_button_text", no_detect)
        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", not_clicked)

        page = FakePage()
        ff = ff_mod.FormFiller(page)
        result = await ff.submit_form_v2()
        assert result.get("text") == "" and result.get("locator") == ""
        assert result.get("primary_fallback_disabled") == "no_active_dialog"
        assert page.locator_calls == [], "回退禁用时不得触碰 page.locator"

    @pytest.mark.asyncio
    async def test_fallback_scoped_to_active_dialog(self, monkeypatch):
        """有活跃弹窗 → 回退 locator/JS 都限定弹窗容器内（A3 误点实证形态）。"""
        async def fp(self):
            return "dialog|更新密钥对"

        async def no_guard(self, scope_selector=""):
            return None

        async def no_detect(self, scope_selector=""):
            return ""

        async def not_clicked(page, text, **kwargs):
            return {"clicked": False}

        async def passthrough_check(self, ret, fp_before):
            return ret

        async def no_wait(page):
            pass

        monkeypatch.setattr(ff_mod.FormFiller, "_active_dialog_fingerprint", fp)
        monkeypatch.setattr(ff_mod.FormFiller, "_submit_button_disabled_guard", no_guard)
        monkeypatch.setattr(ff_mod.FormFiller, "_detect_submit_button_text", no_detect)
        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", not_clicked)
        monkeypatch.setattr(ff_mod.FormFiller, "_dialog_still_open_check", passthrough_check)
        monkeypatch.setattr(
            "core.discovery.replay.wait_helpers.wait_for_loading_complete", no_wait)

        page = FakePage(evaluate_results=["更新并重启云主机"])
        ff = ff_mod.FormFiller(page)
        result = await ff.submit_form_v2()
        assert page.locator_calls, "应走 Primary 回退"
        assert ".el-dialog__wrapper:visible button.el-button--primary" in page.locator_calls[0]
        assert page.locator_clicks, "弹窗内 primary 应被点击"
        assert result.get("text") == "更新并重启云主机"

    @pytest.mark.asyncio
    async def test_negative_prefix_primary_skipped(self, monkeypatch):
        """弹窗内 primary 命中否定前缀（'放弃更新'）→ 跳过不点击。"""
        async def fp(self):
            return "dialog|更新密钥对"

        async def no_guard(self, scope_selector=""):
            return None

        async def no_detect(self, scope_selector=""):
            return ""

        async def not_clicked(page, text, **kwargs):
            return {"clicked": False}

        monkeypatch.setattr(ff_mod.FormFiller, "_active_dialog_fingerprint", fp)
        monkeypatch.setattr(ff_mod.FormFiller, "_submit_button_disabled_guard", no_guard)
        monkeypatch.setattr(ff_mod.FormFiller, "_detect_submit_button_text", no_detect)
        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", not_clicked)

        page = FakePage(evaluate_results=["放弃更新"])
        ff = ff_mod.FormFiller(page)
        result = await ff.submit_form_v2()
        assert result.get("text") == ""
        assert page.locator_clicks == [], "否定前缀按钮不得被点击"


class TestF5CancelTexts:

    def test_close_added_to_cancel_texts(self):
        """'关闭' 入 CANCEL_TEXTS（r9 A4：执行命令弹窗 footer 只有"关闭"）。"""
        assert "关闭" in const.CANCEL_TEXTS

    def test_semantic_separation_kept(self):
        """'关闭' 不进提交/确认词表（关闭≠提交语义）。"""
        assert "关闭" not in const.SUBMIT_TEXTS
        assert "关闭" not in const.CONFIRM_TEXTS

    def test_wired_into_close_chain(self):
        """只读弹窗主链 confirm_dialog(confirm=False) 消费 CANCEL_TEXTS（单源）。"""
        from core.discovery.replay.button_driver import confirm_dialog
        src = _src(confirm_dialog)
        assert "CANCEL_TEXTS" in src


class TestF8TitleClaimsAction:

    def test_continuous_substring_baseline_kept(self):
        """①连续子串：批六 r8 实证 4 例全命中形态零收窄。"""
        assert ce._title_claims_action("删除", "删除云主机test569722")
        assert ce._title_claims_action("更新密钥对", "更新密钥对")
        assert ce._title_claims_action("修改Hostname", "修改Hostname")
        assert ce._title_claims_action("重装操作系统", "重装操作系统-云主机-test569722")

    def test_subject_word_insertion_now_claims(self):
        """r9 B2 定案形态：'创建镜像'弹窗 title='创建云主机镜像' → 判归属成立。"""
        assert ce._title_claims_action("创建镜像", "创建云主机镜像")
        assert ce._title_claims_action("删除镜像", "删除云主机镜像")

    def test_two_char_token_coverage(self):
        """③2字滑块全命中：容忍非白名单词插入（'修改密码'对'修改管理员密码'）。"""
        assert ce._title_claims_action("修改密码", "修改管理员密码")

    def test_zero_overlap_still_mismatch(self):
        """F8.3 防误认领边界：完全不重叠仍判 mismatch（残留弹窗不被误领）。"""
        assert not ce._title_claims_action("删除", "创建云主机镜像")
        assert not ce._title_claims_action("创建镜像", "创建云主机")

    def test_empty_inputs_safe(self):
        assert not ce._title_claims_action("", "任意标题")
        assert not ce._title_claims_action("任意操作", "")

    def test_subject_words_single_source(self):
        """主体词白名单单源；长词在前（'云主机'先于'主机'剔除）。"""
        assert const.DIALOG_TITLE_SUBJECT_WORDS == ("云主机", "主机", "实例", "虚拟机")
        assert (const.DIALOG_TITLE_SUBJECT_WORDS.index("云主机")
                < const.DIALOG_TITLE_SUBJECT_WORDS.index("主机"))

    def test_wired_into_3a_block(self):
        """3a 主判据与残留隐藏名单均走 _title_claims_action（判据单源防漂移）。"""
        src = _src(ce._do_generic_operation)
        assert "_title_claims_action" in src
        assert "_hide_idx" in src


# ============================================================
# G3：F6 导航型菜单项分流 / F7 凭证回显保留 / F9 成功信号优先
# ============================================================

class TestF6NavigationMenuDeduction:

    def test_wired_before_edit_flow_exploration(self):
        """F6 分流必须先于 page_nav 分支的 EDIT-FLOW 探索调用（不进探索=治本）。"""
        src = _src(ce._do_generic_operation)
        f6_pos = src.find("_f6_has_dialog")
        assert f6_pos > 0, "F6 分流缺失"
        calls = [m.start() for m in __import__("re").finditer(
            r"_explore_and_operate_in_new_page\(", src)]
        assert len(calls) >= 2, "page_nav 分支的探索调用缺失"
        # 第 1 个调用是 new_tab 分支（page=new_page），第 2 个是 page_nav 分支
        assert f6_pos < calls[1], "F6 分流必须先于 EDIT-FLOW 探索"

    def test_conditions_double_gate(self):
        """双条件保守边界：marker 命中目标 URL + 无弹窗弹出（防误判删除后跳列表等）。"""
        src = _src(ce._do_generic_operation)
        assert "if marker and marker in url_after:" in src
        assert "if not _f6_has_dialog:" in src
        # 弹窗可见性检查覆盖四形态容器（与 F4 对齐）
        for kw in (".el-dialog__wrapper", ".el-drawer", ".el-message-box", ".ant-modal-wrap"):
            assert kw in src

    def test_result_shape_page_nav(self):
        """F6 结果形态：nav_info.navigated_url → builder 走 page-nav 序列化；
        无表单无提交（submit_result.button_text 置空抑制虚假「确定」点击）。"""
        src = _src(ce._do_generic_operation)
        assert '"navigated_url": url_after' in src
        assert '"form_fields": []' in src
        assert '"submit_result": {"button_text": ""}' in src
        assert '"interaction_mode": "page-nav"' in src

    def test_navigate_back_kept_and_dropdown_locator_excluded(self):
        """跳转后导航回列表（会话一致）；dropdown 类不落 raw locator（回放不可靠，
        对齐本函数尾部既有先例），builder 走 dropdown_item/click_row_more 分支。"""
        src = _src(ce._do_generic_operation)
        assert "_navigate_back_to_list(page, url_before" in src
        assert 'if btn_location not in ("dropdown",) and _verified_trigger_locator:' in src
        assert "_verified_dropdown_item_text" in src

    def test_page_nav_steps_serialization_shape(self):
        """行为测试：F6 结果形态经 _build_page_nav_steps → find_row →
        click_row_more → wait_for_url → assert read_complete → navigate_back，
        不生成虚假「确定」点击步骤。"""
        from core.discovery.playbook_builder.builder import _build_page_nav_steps
        op_data = {
            "trigger_text": "网络配置",
            "selectors": {"trigger": "网络配置", "row_selector": "更多 > 网络配置"},
            "nav_info": {
                "navigated_url": "https://eis.example/vmDetail/test595047",
                "form_fields": [],
                "submit_result": {"button_text": ""},
            },
            "dropdown_item_text_verified": "网络配置",
            "interaction_mode": "page-nav",
            "navigate_back_url": "https://eis.example/vm/list",
        }
        steps = _build_page_nav_steps(op_data)
        actions = [s["action"] for s in steps]
        assert "find_row" in actions
        assert "click_row_more" in actions
        wait_step = next(s for s in steps if s["action"] == "wait_for_url")
        assert "vmDetail" in wait_step["url_pattern"]
        assert_step = next(s for s in steps if s["action"] == "assert_success")
        assert assert_step["assert_mode"] == "read_complete"
        assert steps[-1]["action"] == "navigate_back"
        # 无提交动作 → 无「确定」点击步骤（旧默认会生成 button_text='确定'）
        assert not any(s["action"] == "click_button" and s.get("text") == "确定"
                       for s in steps), "导航型操作不得生成虚假提交点击步骤"


class TestF7CredentialEcho:

    def test_echo_login_name_preserved(self):
        """B1：重置密码弹窗打开时登录名回显 'root' → 保留原值不覆盖
        （fixed_value 三阶段一致：Stage 1 填 root / 落盘规则 / Stage 2 重建 root）。"""
        value, rule = fill_helpers.generate_fill_value(
            "input", "text", "test", "登录名", current_value="root")
        assert value == "root"
        assert rule == {"rule": "fixed_value", "params": {"value": "root"}}

    def test_echo_username_label_preserved(self):
        """'用户名' 同判据（词表单源 _USERNAME_LABEL_KEYWORDS）。"""
        value, rule = fill_helpers.generate_fill_value(
            "input", "text", "test", "用户名", current_value="admin")
        assert value == "admin"
        assert rule["rule"] == "fixed_value"

    def test_no_echo_falls_back_username_pattern(self):
        """无回显（current_value 空）→ 既有 username_pattern 语义不变（零回归）。"""
        value, rule = fill_helpers.generate_fill_value(
            "input", "text", "test", "登录名", current_value="")
        assert rule == {"rule": "username_pattern", "params": {"prefix": "test"}}
        assert value.startswith("test")

    def test_whitespace_only_echo_treated_as_empty(self):
        value, rule = fill_helpers.generate_fill_value(
            "input", "text", "test", "登录名", current_value="   ")
        assert rule["rule"] == "username_pattern"

    def test_infer_fill_rule_synced(self):
        """双源同步：_infer_fill_rule（generate_fill_rules 路径）与
        generate_fill_value 同判据同策略（防 Stage 1 值与落盘规则漂移）。"""
        rule = fill_helpers._infer_fill_rule(
            "登录名", "input", "text", True, current_value="root")
        assert rule == {"rule": "fixed_value", "params": {"value": "root"}}
        rule2 = fill_helpers._infer_fill_rule(
            "登录名", "input", "text", True, current_value="")
        assert rule2 == {"rule": "username_pattern", "params": {"prefix": "test"}}

    def test_apply_fill_rule_fixed_value_roundtrip(self):
        """Stage 2 回放闭环：fixed_value 规则重建同值。"""
        assert fill_helpers.apply_fill_rule(
            {"rule": "fixed_value", "params": {"value": "root"}}) == "root"

    @pytest.mark.asyncio
    async def test_edit_fill_path_uses_echo_value(self):
        """端到端：fill_edit_form 对回显登录名填回显值（不再随机覆盖），
        filled_value 写回字段（F1 通道同源）。"""
        filler = ff_mod.FormFiller(FakePage())
        fields = [{
            "label": "登录名", "type": "input", "inputType": "text",
            "kb_category": "input-generic", "currentValue": "root",
            "selector": ".el-dialog__wrapper .el-input__inner",
        }]
        page = filler.page
        filled = await filler.fill_edit_form(fields, modifications=None)
        assert filled == 1
        values = [v for _, v in page.fills]
        assert "root" in values, "回显登录名必须被保留填充"
        assert not any(v.startswith("test") and v[4:].isdigit() for v in values), \
            "不得生成随机用户名覆盖回显凭证"
        assert fields[0].get("filled_value") == "root"


class TestF9SuccessPriority:

    def test_success_message_read_before_red_text(self):
        """F9 裁决序：先读捕获消息（success 判定）后扫 field 红字——
        B4 推翻根因是先扫红字的早退点（read_form_errors 全页扫描不区分
        红字归属，残留弹窗红字在成功验证前把操作判死）。"""
        src = _src(ce._do_generic_operation)
        f9_start = src.find("批七 F9（B4）")
        assert f9_start > 0, "F9 注释块缺失"
        seg = src[f9_start:]
        captured_pos = seg.find("captured_msgs = await _get_captured_messages(page)")
        errors_pos = seg.find("errors = await read_form_errors(page)")
        assert 0 < captured_pos < errors_pos, "F9 段内必须先读捕获消息后扫红字"

    def test_success_overrides_red_text_gate(self):
        """有 success 语义消息 → 红字不参与裁决（if 分支跳过）；
        无 success 消息 → 既有红字失败语义不变（error_type=severity，
        error-driven retry 依赖）。"""
        src = _src(ce._do_generic_operation)
        assert "if _has_success_msg:" in src
        assert 'return _fail(first.get("severity"' in src
        assert "const.classify_notification" in src, "复用单源分类器（不新增词表）"

    def test_no_stale_success_risk(self):
        """P0-A 保障：每操作开始重装捕获器清空数组（上一操作残留 success
        不会误推翻本轮红字失败）——_do_generic_operation 调用点在位。"""
        src = _src(ce._do_generic_operation)
        assert "await _install_message_capture(page)" in src

    def test_adjudication_note_flows_into_result(self):
        """推翻发生时裁决依据记入 result['adjudication_note']（流入 ui.json，
        供 r10 验收与人工复核；互斥对菜单项消失仅佐证不做判定依据）。"""
        src = _src(ce._do_generic_operation)
        assert '"adjudication_note"' in src
        assert "被已捕获 success 通知推翻" in src

    def test_verifier_strategy_f_already_gated(self):
        """定案记录：B4 实际推翻点在 crud_executor 早退而非 success_verifier
        ——verifier 策略 F（field 红字扫描）本就有 matched_method None 门，
        已捕获 success 后不会扫红字（方案实施位置修正的回归守卫）。"""
        from core.discovery.shared import success_verifier as sv
        src = _src(sv.verify_operation_success)
        assert "if matched_method is None and failure_reason is None:" in src
