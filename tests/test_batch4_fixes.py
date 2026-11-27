# -*- coding: utf-8 -*-
"""
test_batch4_fixes.py — 批次四修复单元测试

测试目标（docs/debug/eis云主机修改方案.md 批次四探针定案后的四处修复）：
- 4.2-A  const.starts_with_negative_prefix（否定前缀判定）
- 4.2-B  submit_form_v2 点击后负向前缀后验（点中「放弃更新」不返回成功）
- 4.2-C  _detect_submit_button_text 构造级回归守卫（负向排除 + 两层匹配）
- 4.1-A  _buildSelector 两副本（form_filler/element_scanner）构造级守卫
         （裸 #id 唯一性校验 + 弹窗容器限定）
- 批一    crud_executor L518 f-string 注释花括号 NameError 回归守卫
- 批三3.3 fill_edit_form 确认字段配对挂载（编辑逐字段路径此前漏挂）

实证背景（tmp/eis_probe_batch4b.json）：
- EIS 创建云主机弹窗是 el-drawer（关闭后 DOM 残留），与克隆 el-dialog 共用
  password-module 组件同 id → page.fill 裸 #id 命中隐藏残留实例 3s 超时。
- EIS 更新密钥对弹窗「放弃更新」enabled 在前、「更新并重启云主机」disabled 在后，
  KB chars-contains XPath 把「更新」解析到「放弃更新」→ 静默关闭零 API → no_signal 假失败。
- 克隆弹窗「登录密码」与「确认密码」为两个独立注册 input，逐字段各生成随机密码
  → 「两次输入密码不一致」校验拒绝。
"""
import re

import pytest

from core.discovery import const
from core.discovery.replay.form_filler import FormFiller


# ============================================================
# 测试基建
# ============================================================

class FakePage:
    """最小 Playwright Page 替身：记录 fill 调用；evaluate 记录 JS 返回 None。"""

    def __init__(self):
        self.fills = []          # [(selector, value), ...]
        self.eval_scripts = []   # [js, ...]
        self.locators = []

    async def fill(self, selector, value, timeout=None):
        self.fills.append((selector, value))

    async def evaluate(self, script, *args):
        self.eval_scripts.append(script)
        return None

    def locator(self, selector):
        loc = _FakeLocator()
        self.locators.append((selector, loc))
        return loc


class _FakeLocator:

    async def count(self):
        return 0

    async def is_visible(self):
        return False

    @property
    def first(self):
        return self


# _detect_submit_button_text 是 async 方法——替身必须可 await
# （同步 lambda 被 await → TypeError: object str can't be used in 'await' expression）
async def _fake_detect_empty(self, scope_selector=""):
    return ""


async def _fake_detect_found(self, scope_selector=""):
    return "确 定"


def make_clone_dialog_fields():
    """复刻 EIS 克隆弹窗扫描产物（probe batch4b PA2 实证形态）。

    复合父项（composite-input, inputCount=2）+ 两个密码子 input 并存注册。
    """
    return [
        {"label": "名称", "type": "input", "kb_category": "input-generic",
         "inputType": "text", "inputCount": 1,
         "selector": "#name-input", "playwright_locator": "#name-input",
         "required": True},
        {"label": "登录名", "type": "input", "kb_category": "input-generic",
         "inputType": "text", "inputCount": 1,
         "selector": "#login-input", "playwright_locator": "#login-input",
         "required": True},
        {"label": "登录密码", "type": "composite-input",
         "kb_category": "composite-input", "inputCount": 2,
         "selector": None, "required": True},
        {"label": "登录密码", "type": "input", "kb_category": "input-generic",
         "inputType": "password", "inputCount": 1,
         "selector": "#password-module-new", "playwright_locator": "#password-module-new",
         "required": True},
        {"label": "确认密码", "type": "input", "kb_category": "input-generic",
         "inputType": "password", "inputCount": 1,
         "selector": "#password-module-confirm", "playwright_locator": "#password-module-confirm",
         "required": True},
    ]


def _fills_for(page, selector):
    return [v for sel, v in page.fills if sel.startswith(selector)]


# ============================================================
# 4.2-A 否定前缀判定
# ============================================================

class TestStartsWithNegativePrefix:

    def test_negative_prefixes(self):
        assert const.starts_with_negative_prefix("放弃更新") is True
        assert const.starts_with_negative_prefix("放 弃 更 新") is True
        assert const.starts_with_negative_prefix("取消并返回") is True
        assert const.starts_with_negative_prefix("Close Dialog") is False  # 英文大小写敏感（同既有归一化口径）

    def test_submit_texts_never_negative(self):
        for text in const.SUBMIT_TEXTS:
            assert const.starts_with_negative_prefix(text) is False, text

    def test_boundary_inputs(self):
        assert const.starts_with_negative_prefix("") is False
        assert const.starts_with_negative_prefix(None) is False
        assert const.starts_with_negative_prefix("更新并重启云主机") is False


# ============================================================
# 批三 3.3 fill_edit_form 配对挂载（两阶段）
# ============================================================

class TestFillEditFormPairing:

    @pytest.mark.asyncio
    async def test_confirm_password_copies_source_value(self):
        """「确认密码」必须复用「登录密码」的值（配对挂载实证）。"""
        page = FakePage()
        ff = FormFiller(page)
        fields = make_clone_dialog_fields()

        filled = await ff.fill_edit_form(fields, None)

        assert filled >= 3  # 名称 + 登录名 + 两个密码
        pw_new = _fills_for(page, "#password-module-new")
        pw_confirm = _fills_for(page, "#password-module-confirm")
        assert pw_new and pw_confirm, "两个密码子字段都应被填充"
        assert pw_new[-1] == pw_confirm[-1], (
            f"配对失效: 登录密码={pw_new[-1]!r} vs 确认密码={pw_confirm[-1]!r}"
        )

    @pytest.mark.asyncio
    async def test_composite_parent_still_skipped(self):
        """复合父项（composite-input / inputCount>=2）仍跳过——4.1 不经此路径。"""
        page = FakePage()
        ff = FormFiller(page)
        fields = make_clone_dialog_fields()

        await ff.fill_edit_form(fields, None)
        # 复合父项无 selector，本就不可填；断言子项填充手势 == 每子项恰好
        # fill("") + fill(value) 两次调用（两阶段重构不改变填充手势），且两个
        # 密码子项最终值一致（配对挂载）
        pw_new = _fills_for(page, "#password-module-new")
        pw_confirm = _fills_for(page, "#password-module-confirm")
        assert pw_new == ["", pw_new[-1]] and len(pw_new) == 2
        assert pw_confirm == ["", pw_new[-1]] and len(pw_confirm) == 2
        assert len(_fills_for(page, "#name-input")) == 2
        assert len(_fills_for(page, "#login-input")) == 2

    @pytest.mark.asyncio
    async def test_modifications_explicit_values_preserved(self):
        """modifications 显式指定字段照旧优先，未指定字段不填充（行为不回归）。"""
        page = FakePage()
        ff = FormFiller(page)
        fields = make_clone_dialog_fields()

        filled = await ff.fill_edit_form(fields, {"名称": "fix-clone-01"})

        assert filled == 1
        # 手势固定两步: fill("") 清空 + fill(value)
        assert _fills_for(page, "#name-input") == ["", "fix-clone-01"]
        assert _fills_for(page, "#password-module-new") == []
        assert _fills_for(page, "#login-input") == []

    @pytest.mark.asyncio
    async def test_fill_rule_written_back_for_generated_values(self):
        """D3: generate_fill_value 产出的 fill_rule 仍写回 field dict（playbook 依赖）。"""
        page = FakePage()
        ff = FormFiller(page)
        fields = make_clone_dialog_fields()

        await ff.fill_edit_form(fields, None)
        pw_child = [f for f in fields
                    if f.get("label") == "登录密码" and f.get("inputType") == "password"]
        assert pw_child, "密码子字段应在 fields 中"
        rule = pw_child[0].get("_fill_rule")
        assert rule and rule.get("rule") == "password_pattern", rule

    @pytest.mark.asyncio
    async def test_identifier_field_skipped(self):
        """标识字段（账号类）仍跳过，防 marker 失效（行为不回归）。"""
        page = FakePage()
        ff = FormFiller(page)
        fields = make_clone_dialog_fields()
        fields.append({"label": "登录账号", "type": "input", "kb_category": "input-generic",
                       "inputType": "text", "inputCount": 1,
                       "selector": "#account-input", "playwright_locator": "#account-input"})

        await ff.fill_edit_form(fields, None)
        assert _fills_for(page, "#account-input") == []


# ============================================================
# 4.2-B submit_form_v2 点击后负向前缀后验
# ============================================================

class TestSubmitFormV2NegativePostValidation:

    @pytest.mark.asyncio
    async def test_negative_click_not_returned_as_success(self, monkeypatch):
        """点击命中「放弃更新」→ 不得当作提交成功返回（第 6 轮误点复现防御）。"""
        page = FakePage()
        ff = FormFiller(page)
        calls = []

        async def fake_click(page_arg, text, **kwargs):
            calls.append(text)
            if text == "更新":
                # KB chars-contains 误命中否定按钮（第 6 轮实证形态）
                return {"clicked": True, "strategy": "xpath", "tag": "button",
                        "text": "放弃更新", "locator": "button:has-text(\"放弃更新\")",
                        "xpath_index": 0, "clean_text": "更新"}
            return {"clicked": False}

        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", fake_click)
        monkeypatch.setattr(FormFiller, "_detect_submit_button_text",
                            _fake_detect_empty)

        result = await ff.submit_form_v2()

        assert result.get("locator") == "" and result.get("text") == "", (
            f"否定按钮点击不得返回成功: {result}"
        )
        assert "更新" in calls, "否定后验后应继续尝试其余提交文本"
        assert len(calls) == len(const.SUBMIT_TEXTS), "应遍历全部提交文本"

    @pytest.mark.asyncio
    async def test_negative_click_then_next_text_succeeds(self, monkeypatch):
        """否定命中后续行：下一提交文本命中真按钮 → 正常返回成功。"""
        page = FakePage()
        ff = FormFiller(page)

        async def fake_click(page_arg, text, **kwargs):
            if text == const.SUBMIT_TEXTS[0]:
                return {"clicked": True, "strategy": "xpath", "tag": "button",
                        "text": "放弃更新", "locator": "button:has-text(\"放弃更新\")",
                        "xpath_index": 0, "clean_text": text}
            if text == const.SUBMIT_TEXTS[1]:
                return {"clicked": True, "strategy": "xpath", "tag": "button",
                        "text": text, "locator": f"button:has-text(\"{text}\")",
                        "xpath_index": 1, "clean_text": text}
            return {"clicked": False}

        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", fake_click)
        monkeypatch.setattr(FormFiller, "_detect_submit_button_text",
                            _fake_detect_empty)

        result = await ff.submit_form_v2()

        assert result.get("text") == const.SUBMIT_TEXTS[1]
        assert result.get("locator"), "第二个提交文本应作为成功返回"

    @pytest.mark.asyncio
    async def test_normal_click_still_returns_success(self, monkeypatch):
        """常规提交按钮命中 → 行为不变（回归守卫）。"""
        page = FakePage()
        ff = FormFiller(page)

        async def fake_click(page_arg, text, **kwargs):
            return {"clicked": True, "strategy": "xpath", "tag": "button",
                    "text": "确 定", "locator": "button:has-text(\"确 定\")",
                    "xpath_index": 0, "clean_text": "确定"}

        monkeypatch.setattr(
            "core.discovery.shared.button_clicker.click_page_button", fake_click)
        monkeypatch.setattr(FormFiller, "_detect_submit_button_text",
                            _fake_detect_found)

        result = await ff.submit_form_v2()
        assert result.get("text") == "确 定"
        assert result.get("locator") == "button:has-text(\"确 定\")"


# ============================================================
# 4.2-C _detect_submit_button_text 构造级回归守卫
# ============================================================

class TestDetectSubmitButtonTextConstruction:

    @pytest.mark.asyncio
    async def test_js_contains_negative_exclusion_and_two_tiers(self):
        """构造的 JS 必须含否定前缀排除 + 全等/前缀两层匹配（f-string 转义回归守卫）。"""
        page = FakePage()
        ff = FormFiller(page)

        await ff._detect_submit_button_text("")

        assert page.eval_scripts, "应调用 page.evaluate"
        js = page.eval_scripts[0]
        # 否定前缀列表已注入
        assert "放弃" in js and "取消" in js
        assert "NEG.some(w => norm.startsWith(w))" in js
        # 层 1: 全等命中
        assert "SUBMITS.includes(norm)" in js
        # 层 2: 前缀命中返回完整按钮文本（「更新并重启云主机」startsWith「更新」）
        assert "SUBMITS.some(t => norm.startsWith(t) && norm.length > t.length)" in js


# ============================================================
# 4.1-A _buildSelector 两副本构造级回归守卫
# ============================================================

class TestBuildSelectorIdUniqueness:

    def _extract_form_filler_js(self):
        src = open(r"D:\Mobile\API_AI_test\core\discovery\replay\form_filler.py",
                   encoding="utf-8").read()
        m = re.search(
            r'function _buildSelector\(element, labelText\) \{\{(.*?)\n        \}\}',
            src, re.S)
        assert m, "form_filler._buildSelector 段未找到"
        ns = {}
        exec(compile("fn_src = f" + repr(m.group(0)), "<test>", "exec"), ns)
        return ns["fn_src"]

    def test_form_filler_bare_id_uniqueness_and_container_scope(self):
        """f-string 干跑求值成功 + 唯一性校验/容器限定结构在场。"""
        js = self._extract_form_filler_js()
        assert "document.querySelectorAll(bareId).length === 1" in js
        assert "DIALOG_CONTAINERS" in js
        assert "`.${hit} ${bareId}`" in js
        assert ":not([style*=\"display: none\"]) ${bareId}" in js
        # id 冲突且容器限定失败 → 必须落回优先级 2/3（不能无声返回裸 id）
        assert "// id 冲突且容器限定仍不唯一" in js

    def test_element_scanner_copy_same_semantics(self):
        """element_scanner 副本（单花括号纯 JS）同源同款语义。"""
        src = open(r"D:\Mobile\API_AI_test\core\discovery\ui_scanner\element_scanner.py",
                   encoding="utf-8").read()
        start = src.find("function _buildSelector(element, labelText) {")
        end = src.find("function _buildComponentSelector", start)
        assert start > 0 and end > start
        seg = src[start:end]
        assert "document.querySelectorAll(bareId).length === 1" in seg
        assert "DIALOG_CONTAINERS" in seg
        assert "`.${hit} ${bareId}`" in seg
        assert ":not([style*=\"display: none\"]) ${bareId}" in seg


# ============================================================
# 4.2-D 必填空 select 提交前中止（_required_selects_still_empty 守卫）
# ============================================================

class TestRequiredSelectsGuard:
    """提交前守卫: pre-fill 尝试后仍有 is-required 空 select → required_field_empty 终态。

    实证（PB2 + 第 6 轮）: 更新密钥对「密钥对」el-select 无可选项恒空 →
    提交按钮 disabled → 提交点击只得静默关闭（no_signal）或否定按钮误点。
    守卫把这类操作转为可诊断的 required_field_empty（既有终态类型，
    与提交后兜底分支同型，不空转重试）。
    """

    CE_PATH = r"D:\Mobile\API_AI_test\core\discovery\operation_executor\crud_executor.py"

    def _guard_src(self):
        src = open(self.CE_PATH, encoding="utf-8").read()
        start = src.find("async def _required_selects_still_empty")
        end = src.find("async def _do_generic_operation", start)
        assert start > 0 and end > start, "_required_selects_still_empty 未找到"
        return src[start:end]

    def test_guard_js_builds_and_has_criteria(self):
        """守卫 f-string 干跑求值成功（防 NameError 同型回归）+ 三项判据在场。"""
        seg = self._guard_src()
        m = re.search(r"page\.evaluate\((f\"\"\".*?\"\"\")\)", seg, re.S)
        assert m, "守卫 f-string 未找到"
        ns = {"scope_q": ".el-dialog__wrapper"}
        exec(compile("js = " + m.group(1), "<test>", "exec"), ns)
        js = ns["js"]
        # scope 注入成功
        assert "document.querySelector('.el-dialog__wrapper')" in js
        # is-required 收紧（非必填空 select 不阻断）
        assert "is-required" in js
        # disabled select 跳过（级联自动填充态不构成中止证据）
        assert "innerInput.disabled" in js
        # 空判据三通道与 pre_fill_diag 同源
        assert "el-tag" in js and "el-input__inner" in js
        assert "el-select__selected-item" in js

    def test_guard_mounted_before_submit_in_generic_path(self):
        """守卫必须挂在 submit_form_v2 之前且落 required_field_empty 终态。"""
        src = open(self.CE_PATH, encoding="utf-8").read()
        guard_pos = src.find("_required_selects_still_empty(page, _sel_q)")
        submit_pos = src.find("submit_form_v2(scope_selector=_sel_q)")
        assert 0 < guard_pos < submit_pos, "守卫须在提交点击之前"
        seg = src[guard_pos:submit_pos]
        assert "required_field_empty" in seg, "中止须落既有终态类型"
        # 守卫探测异常须放行提交（best-effort，不阻断主流程）
        assert "放行提交" in seg

    @pytest.mark.asyncio
    async def test_guard_helper_passthrough(self):
        """helper 直接调用: scope 注入 JS + evaluate 结果原样返回。"""
        from core.discovery.operation_executor import crud_executor as ce

        class _GuardPage:
            def __init__(self, result):
                self.result = result
                self.scripts = []

            async def evaluate(self, script, *args):
                self.scripts.append(script)
                return self.result

        page = _GuardPage(["密钥对"])
        out = await ce._required_selects_still_empty(
            page, '.el-dialog__wrapper:not([style*="display: none"])')
        assert out == ["密钥对"]
        assert "is-required" in page.scripts[0]
        assert ".el-dialog__wrapper:not([style*=\"display: none\"])" in page.scripts[0]


# ============================================================
# 批一：crud_executor L518 f-string 注释花括号 NameError 回归守卫
# ============================================================

class TestScanRowStateSnapshotFString:

    def test_snapshot_js_builds_without_nameerror(self):
        """(params) 形态 f-string 必须全部可构造（花括号已转义）。

        回归实证: 未转义时 f-string 求值抛 NameError，被 except 吞掉后
        _scan_row_state_snapshot 恒返回空快照 → 批一状态机 R1-R4 整链路死代码。
        批十-B 后 (params) 块增至 3 个（可见性判定/菜单收集/状态快照），
        守卫升级为全块逐一构造（新增块同受花括号转义守护）；状态快照块
        （rowStatus/qgaReady）保留原断言。
        """
        from core.discovery.operation_executor import crud_executor as ce
        src = open(r"D:\Mobile\API_AI_test\core\discovery\operation_executor\crud_executor.py",
                   encoding="utf-8").read()
        blocks = re.findall(r'f"""\(params\) => \{\{.*?"""', src, re.S)
        assert blocks, "(params) 形态 f-string 块未找到"
        assert len(blocks) >= 3, \
            f"(params) 块须含可见性/收集/状态快照 3 块，实际 {len(blocks)}"
        ns = {"menuId": None, "dropdownIndex": None,
              "_ROW_BY_MARKER_JS": ce._ROW_BY_MARKER_JS,
              "existing_actions": set()}
        snapshot_js = None
        for blk in blocks:
            ns_i = dict(ns)
            exec(compile("js = " + blk, "<test>", "exec"), ns_i)
            js_i = ns_i["js"]
            if "rowStatus" in js_i and "qgaReady" in js_i:
                snapshot_js = js_i
        assert snapshot_js is not None, "状态快照 f-string（rowStatus/qgaReady）未找到"
        assert "{row: {...}}" in snapshot_js, "注释花括号渲染丢失"

    def test_no_unescaped_js_template_interpolations(self):
        """crud_executor 全文件 f-string 不含未转义的 JS 模板插值（'row'/'...'）。"""
        import ast
        src = open(r"D:\Mobile\API_AI_test\core\discovery\operation_executor\crud_executor.py",
                   encoding="utf-8").read()
        tree = ast.parse(src)
        suspects = []
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                for v in node.values:
                    if isinstance(v, ast.FormattedValue):
                        seg = ast.get_source_segment(src, v.value) or ""
                        if seg.strip() in ("row", "..."):
                            suspects.append((node.lineno, seg))
        assert not suspects, f"未转义插值回归: {suspects}"


# ============================================================
# 第 7 轮增量：el-select 优先选项（form_preferences.select_preferred_options）
# ============================================================

class TestSelectPreferredOptions:
    """命名空间等 el-select 单选字段优先选项机制。

    实证（第 7 轮 Stage 1）: 创建云主机表单默认命名空间 wmk2 内存配额不足 →
    form_validation 终态失败。机制: profile.yaml form_preferences.
    select_preferred_options 注入 MultiStepExecutor 类属性，label 命中时
    优先选择期望选项（覆盖表单默认值/first-option），未命中回退默认行为。
    Stage 1 记录真实选择序列，Stage 2 回放同构（三阶段一致）。
    """

    FF_PATH = r"D:\Mobile\API_AI_test\core\discovery\replay\form_filler.py"
    RUN_PATH = r"D:\Mobile\API_AI_test\core\discovery\run.py"
    PROFILE_PATH = r"D:\Mobile\API_AI_test\projects\eis\profile.yaml"

    def test_setter_cleans_and_overrides(self):
        """setter: 非法条目清洗 + 覆盖式幂等 + 类属性全局生效。"""
        from core.discovery.replay.form_filler import MultiStepExecutor
        try:
            MultiStepExecutor.set_select_preferred_options({
                "命名空间": ["test-smoke"],
                "bad_list": [],              # 空列表剔除
                "bad_value": "not-a-list",   # 非列表剔除
                "ok_tuple": ("a", "b"),      # tuple 接受
            })
            got = MultiStepExecutor._preferred_select_options
            assert got == {"命名空间": ["test-smoke"], "ok_tuple": ["a", "b"]}
            # 覆盖式幂等: 二次注入整体替换
            MultiStepExecutor.set_select_preferred_options({"命名空间": ["x"]})
            assert MultiStepExecutor._preferred_select_options == {"命名空间": ["x"]}
            # 非法类型整体忽略
            MultiStepExecutor.set_select_preferred_options(None)
            assert MultiStepExecutor._preferred_select_options == {}
        finally:
            MultiStepExecutor.set_select_preferred_options(None)

    def test_override_mounted_after_option_discovery(self):
        """Step 4.5 覆盖逻辑必须挂在 first-option 发现之后、detail 构造之前。"""
        src = open(self.FF_PATH, encoding="utf-8").read()
        seg_pos = src.find("Step 4.5: 优先选项覆盖")
        assert seg_pos > 0, "优先选项覆盖段未找到"
        disc_pos = src.find("retry discovery option_text")
        detail_pos = src.find('"is_editable": is_editable', seg_pos)
        assert 0 < disc_pos < seg_pos, "覆盖须在选项发现之后"
        assert detail_pos > seg_pos, "覆盖须在 detail 构造之前"
        seg = src[seg_pos:detail_pos]
        # 命中时覆盖 option_text；未命中保持默认行为
        assert "option_text = found_pref" in seg
        assert "保持" in seg and "不在" in seg

    @pytest.mark.asyncio
    async def test_find_preferred_option_passthrough(self):
        """helper: JS 扫描口径（可见面板+可见选项+disabled 跳过+精确匹配）+ 结果透传。"""
        from core.discovery.replay.form_filler import MultiStepExecutor

        class _Page:
            def __init__(self, result):
                self.result, self.scripts = result, []

            async def evaluate(self, script, *args):
                self.scripts.append((script, args))
                return self.result

        ex = MultiStepExecutor(_Page("test-smoke"), None, "element-ui")
        assert await ex._find_preferred_option(["a", "test-smoke"]) == "test-smoke"
        js, args = ex.page.scripts[0]
        assert "is-disabled" in js and "offsetWidth" in js
        assert "preferred.includes(text)" in js
        assert args == (["a", "test-smoke"],)
        # 未命中与异常路径
        assert await MultiStepExecutor(_Page(""), None)._find_preferred_option(["x"]) == ""

        class _Boom:
            async def evaluate(self, script, *args):
                raise RuntimeError("boom")

        assert await MultiStepExecutor(_Boom(), None)._find_preferred_option(["x"]) == ""
        # ant-design 不支持（配置消费场景为 Element UI）
        assert await MultiStepExecutor(_Page("x"), None, "ant-design")._find_preferred_option(["x"]) == ""

    def test_run_py_injects_profile_preferences(self):
        """run.py main 入口: profile form_preferences 注入 MultiStepExecutor。"""
        src = open(self.RUN_PATH, encoding="utf-8").read()
        imp_pos = src.find("from core.discovery.replay.form_filler import MultiStepExecutor")
        call_pos = src.find("MultiStepExecutor.set_select_preferred_options(_pref_opts)")
        assert imp_pos > 0 and call_pos > 0
        # 注入点向前 1500 字符内须有 profile 加载与配置键读取（注入紧随 main 的 profile 加载）
        seg = src[max(0, call_pos - 1500):call_pos]
        assert "profile = _load_profile(project_dir)" in seg, "注入须在 profile 加载之后"
        assert "form_preferences" in seg and "select_preferred_options" in seg

    def test_profile_yaml_has_namespace_preference(self):
        """EIS profile.yaml: form_preferences.select_preferred_options 可解析且指向 test-smoke。"""
        import yaml
        cfg = yaml.safe_load(open(self.PROFILE_PATH, encoding="utf-8").read())
        prefs = (cfg.get("form_preferences") or {}).get("select_preferred_options") or {}
        assert prefs.get("命名空间") == ["test-smoke"]
