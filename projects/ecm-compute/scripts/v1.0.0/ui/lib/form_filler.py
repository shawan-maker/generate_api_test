"""
form_filler.py - 表单填充与验证

职责：
- 扫描页面表单字段（输入框、选择框、文本域）
- 根据 label 智能填充表单
- 处理下拉框选择
- 表单提交与验证
"""

import json
import logging
import time
from playwright.async_api import Page, Locator
from typing import Dict, List, Tuple

LOG = logging.getLogger("form_filler")


class FormFiller:
    """表单填充封装类，供 capture_apis 调用"""

    def __init__(self, page: Page):
        self.page = page
        self._kb = None
        self._executor = None

    def _get_executor(self, framework="element-ui"):
        """获取 MultiStepExecutor 实例（延迟加载）"""
        if self._executor is None:
            from .kb_loader import get_kb
            self._kb = get_kb()
            self._executor = MultiStepExecutor(self.page, self._kb, framework)
        return self._executor

    async def scan_form_fields_v2(self) -> List[Dict]:
        """扫描表单字段（v2 版本，返回完整结构含 selector + kb_category）"""
        fields = await scan_form_fields(self.page)
        # 保留完整字段结构，含 kb_category
        result = []
        for f in fields:
            entry = {
                "label": f["label"],
                "type": f["type"],
                "kb_category": f.get("kb_category", ""),
                "selector": f.get("selector"),
                "inputType": f.get("inputType", "text"),
                "required": f.get("required", False),
            }
            if f.get("firstOptionText"):
                entry["firstOptionText"] = f["firstOptionText"]
            result.append(entry)
        return result

    async def fill_create_form(self, fields: List[Dict], username: str,
                                 fill_data: Dict[str, str] = None) -> int:
        """填充创建表单，返回成功填充的字段数。

        优先使用 scan 返回的精确 selector，回退到 el-form-item 结构定位。
        填充数据从 _build_fill_data() 动态构建（hardcoded + type-based fallback）。

        multi_step 类型（el-select/el-cascader/date-picker）由此方法跳过，
        由 fill_multi_step_fields() 处理。

        Args:
            fields: 表单字段列表
            username: 用户名
            fill_data: 可选的预生成填充数据（用于覆盖默认值）
        """
        from . import const

        if fill_data is None:
            fill_data = self._build_fill_data(fields, username)

        filled = 0
        for field in fields:
            label = field.get("label", "")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")
            selector = field.get("selector")

            # multi_step 类型由 MultiStepExecutor 处理，此处跳过
            if kb_category in const.MULTI_STEP_TYPES:
                continue

            # form-checkbox 由 MultiStepExecutor 处理
            if kb_category == "form-checkbox":
                continue

            # radio 类型：点击第一个选项（默认选中即可）
            if kb_category == "radio" or field_type == "radio":
                try:
                    first_text = field.get("firstOptionText", "")
                    if first_text:
                        radio_label = self.page.locator(
                            f'.el-radio-group .el-radio-button__inner:has-text("{first_text}"), '
                            f'.el-radio-group .el-radio__label:has-text("{first_text}")'
                        ).first
                        if await radio_label.count() > 0:
                            await radio_label.click()
                            filled += 1
                            LOG.info(f"    选择 radio: {label} = {first_text}")
                except Exception as e:
                    LOG.debug(f"选择 radio {label} 失败: {e}")
                continue

            # 查找匹配的填充值
            value = None
            for key, val in fill_data.items():
                if key in label or label in key:
                    value = val
                    break

            if not value:
                continue

            try:
                # 优先使用 scan 返回的精确 selector
                if selector:
                    await self.page.fill(selector, value, timeout=3000)
                    filled += 1
                    continue

                # 回退：通过 label 在 el-form-item 结构中定位 input/textarea
                if field_type == "textarea":
                    css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) textarea'
                    await self.page.fill(css, value, timeout=3000)
                else:
                    # 复合输入组检测：检查单个 form-item 内是否有多个可见 input
                    form_item_css = f'.el-form-item:has(.el-form-item__label:has-text("{label}"))'
                    form_items = await self.page.locator(form_item_css).all()

                    composite_found = False
                    for fi in form_items:
                        inputs_in_this_item = await fi.locator('input:not([readonly])').count()
                        if inputs_in_this_item >= 2:
                            # 这个 form-item 内有多个 input，是复合输入组
                            main_input = fi.locator('input:not([readonly])[placeholder*="请输入"], input:not([readonly])[placeholder*="Enter"]').first
                            if await main_input.count() > 0:
                                await main_input.fill(value, timeout=3000)
                                filled += 1
                                LOG.info(f"    填充复合输入组: {label} = {value} (多input匹配)")
                                composite_found = True
                                break

                    if not composite_found:
                        # 普通单 input 字段
                        css = f'{form_item_css} input:not([readonly])'
                        await self.page.fill(css, value, timeout=3000)
                filled += 1
            except Exception as e:
                LOG.warning(f"填充字段 {label} 失败: {e}")

        return filled

    async def fill_multi_step_fields(self, fields: List[Dict], framework: str = "element-ui") -> Tuple[int, List[Dict]]:
        """处理 multi_step 类型的表单字段。

        遍历 fields，对 kb_category 在 MULTI_STEP_TYPES 中的字段调用 MultiStepExecutor。
        优先使用 field 中的 selector（来自 Stage 1），KB XPath 作为回退。
        旧版 select_dropdowns() 作为兜底保留。

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            framework: UI 框架

        Returns:
            (成功处理的字段数, 详细信息列表)
            详细信息列表包含每个字段的 label, kb_category, selector, is_editable, option_text
        """
        from . import const

        executor = self._get_executor(framework)
        filled = 0
        details = []

        for field in fields:
            kb_cat = field.get("kb_category", "")
            if kb_cat not in const.MULTI_STEP_TYPES and kb_cat != "form-checkbox":
                continue

            label = field.get("label", "")
            selector = field.get("selector") or field.get("playwright_locator", "")

            # 传入 selector，优先使用
            success, detail = await executor.execute_with_details(kb_cat, label, option_text="", selector=selector)

            if success:
                filled += 1
                details.append({
                    "label": label,
                    "kb_category": kb_cat,
                    "selector": selector,
                    "is_editable": detail.get("is_editable", False),
                    "option_text": detail.get("option_text", ""),
                })
                LOG.info(f"    ✅ multi_step: {label} ({kb_cat})")
            elif detail.get("skipped_reason") == "no_options":
                # 非必填且无可选选项，跳过但不算失败
                details.append({
                    "label": label,
                    "kb_category": kb_cat,
                    "selector": selector,
                    "is_editable": detail.get("is_editable", False),
                    "option_text": "",
                    "skipped_reason": "no_options",
                })
                LOG.info(f"    ⏭️ multi_step 跳过(无选项): {label} ({kb_cat})")
            else:
                LOG.warning(f"    ⚠️ multi_step 失败: {label} ({kb_cat})")

        return filled, details

    async def _select_by_css_selector(self, selector: str, label: str) -> bool:
        """CSS selector 回退：直接点击 el-select input 展开 + 选择第一项。

        用于 page-nav 上下文（创建页面），KB XPath 模式无法匹配 DOM 结构时。

        Args:
            selector: CSS 选择器（来自 playbook）
            label: 字段标签（用于日志）

        Returns:
            bool: 是否成功
        """
        try:
            # 取第一个 selector（可能有多个逗号分隔的备选）
            primary_selector = selector.split(",")[0].strip()

            # 点击 el-select 内部的 input 展开下拉框
            input_selector = f"{primary_selector} input.el-input__inner"
            input_el = self.page.locator(input_selector).first
            if await input_el.count() == 0:
                # 回退：直接点击 .el-select
                input_el = self.page.locator(primary_selector).first
            if await input_el.count() == 0:
                LOG.debug(f"    CSS selector 回退: 未找到元素 {primary_selector}")
                return False

            await input_el.click(timeout=3000)
            await self.page.wait_for_timeout(500)

            # 选择第一个可见的下拉选项
            first_option = self.page.locator(
                '.el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled):visible'
            ).first
            if await first_option.count() > 0:
                await first_option.click(timeout=2000)
                LOG.debug(f"    CSS selector 回退成功: {label}")
                return True

            # 兜底：任何可见的 dropdown item
            any_option = self.page.locator(
                '.el-select-dropdown__item:visible'
            ).first
            if await any_option.count() > 0:
                await any_option.click(timeout=2000)
                LOG.debug(f"    CSS selector 回退成功(兜底): {label}")
                return True

            LOG.debug(f"    CSS selector 回退: 未找到下拉选项")
            return False
        except Exception as e:
            LOG.debug(f"    CSS selector 回退失败: {label}: {e}")
            return False

    async def fill_edit_form(self, fields: List[Dict],
                              modifications: Dict[str, str] = None) -> int:
        """填充编辑表单（只修改指定字段，不动名称/编码等标识字段）。

        与 fill_create_form 的区别：
        - 只修改 modifications 中指定的字段
        - 默认修改 描述/备注/说明 类字段
        - 跳过 名称/编码/用户名 等标识字段

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            modifications: 显式指定 {label: new_value}。
                           为 None 时自动选择可编辑字段。

        Returns:
            成功填充的字段数
        """
        from . import const

        # 标识字段：不修改
        skip_labels = {"名称", "编码", "用户名", "姓名", "账号",
                       "name", "code", "username", "account", "id"}
        # 默认可编辑字段关键词
        edit_targets = {"描述", "备注", "说明", "remark", "description",
                        "memo", "note", "comment"}

        ts = str(int(time.time()))[-6:]
        filled = 0

        for field in fields:
            label = field.get("label", "")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")
            selector = field.get("selector")

            # multi_step 类型跳过
            if kb_category in const.MULTI_STEP_TYPES:
                continue
            if kb_category == "form-checkbox":
                continue

            # 标识字段跳过
            if any(kw in label.lower() for kw in skip_labels):
                continue

            # 确定填充值
            value = None
            if modifications and label in modifications:
                value = modifications[label]
            elif not modifications:
                # 自动模式：选择可编辑字段
                if (any(kw in label.lower() for kw in edit_targets)
                        or field_type == "textarea"):
                    value = f"auto_edited_{ts}"

            if not value:
                continue

            try:
                if selector:
                    await self.page.fill(selector, "", timeout=3000)
                    await self.page.fill(selector, value, timeout=3000)
                    filled += 1
                    LOG.info(f"    编辑字段: {label} = {value}")
                else:
                    if field_type == "textarea":
                        css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) textarea'
                    else:
                        css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) input:not([readonly])'
                    await self.page.fill(css, "", timeout=3000)
                    await self.page.fill(css, value, timeout=3000)
                    filled += 1
                    LOG.info(f"    编辑字段(回退): {label} = {value}")
            except Exception as e:
                LOG.debug(f"    编辑字段 {label} 失败: {e}")

        return filled

    def _build_fill_data(self, fields: List[Dict], username: str) -> dict:
        """动态构建表单填充数据。

        策略：
        1. 通用字段映射（硬编码，覆盖常见场景）
        2. 未匹配的字段按 type/inputType 生成默认值

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            username: 用户名（用于名称/用户名类字段）

        Returns:
            {label: value} 字典
        """
        ts = str(int(time.time()))[-6:]
        import random as _rand
        _phone = f"138{_rand.randint(10000000, 99999999)}"

        # 通用字段映射（保留现有逻辑）
        fill_data = {
            # 通用字段
            "名称": username, "编码": f"code_{ts}",
            "描述": f"自动测试创建于{ts}", "备注": f"自动创建于{ts}",
            "说明": f"自动测试创建于{ts}",
            # 用户管理
            "用户名": username, "姓名": username,
            "邮箱": f"at_{ts}@test.com", "手机": _phone, "手机号": _phone, "电话": _phone,
            "密码": "Test@123456", "确认密码": "Test@123456",
            # 角色管理
            "角色名称": username, "角色编码": f"role_code_{ts}",
            "角色描述": f"自动测试角色创建于{ts}",
            # 英文字段
            "username": username, "name": username,
            "email": f"at_{ts}@test.com", "phone": _phone, "mobile": _phone, "tel": _phone,
            "password": "Test@123456", "description": f"自动测试创建于{ts}",
            "code": f"code_{ts}", "remark": f"自动创建于{ts}",
        }

        # 为未匹配的字段按 type 生成默认值
        for field in fields:
            label = field.get("label", "")
            if not label:
                continue
            # 检查是否已被覆盖
            if any(key in label or label in key for key in fill_data):
                continue
            field_type = field.get("type", "input")
            input_type = field.get("inputType", "text")
            fill_data[label] = self._default_for_type(field_type, input_type, ts, username)

        return fill_data

    @staticmethod
    def _default_for_type(field_type: str, input_type: str, ts: str, username: str) -> str:
        """根据字段类型生成合理的默认填充值。"""
        if field_type == "textarea":
            return f"自动测试创建于{ts}"
        if input_type == "number":
            return "1"
        if input_type == "email":
            return f"at_{ts}@test.com"
        if input_type == "tel":
            import random as _rand
            return f"138{_rand.randint(10000000, 99999999)}"
        if input_type == "password":
            return "Test@123456"
        if input_type == "url":
            return f"https://test-{ts}.example.com"
        # 默认文本
        return f"AT_{username}_{ts}"

    async def select_dropdowns(self) -> int:
        """选择所有下拉框（包括 el-select 和 el-tree）的第一个选项，返回成功选择的数量"""
        selected = 0
        try:
            # 查找所有 el-select
            selects = await self.page.query_selector_all('.el-select')
            for sel in selects:
                try:
                    await sel.click()
                    await self.page.wait_for_timeout(500)

                    # 选择第一个可见选项
                    options = await self.page.query_selector_all('.el-select-dropdown__item:visible')
                    if options:
                        await options[0].click()
                        selected += 1
                        await self.page.wait_for_timeout(300)
                    else:
                        await self.page.keyboard.press('Escape')
                except Exception as e:
                    LOG.warning(f"选择下拉框失败: {e}")
                    await self.page.keyboard.press('Escape')

            # 查找树形选择器（el-tree），递归展开并勾选
            tree_checked = await self._handle_tree_selectors()
            selected += tree_checked

        except Exception as e:
            LOG.warning(f"扫描下拉框失败: {e}")

        return selected

    async def _handle_tree_selectors(self) -> int:
        """处理树形选择器，展开并勾选 checkbox。

        使用 JavaScript 点击绕过 Playwright 可见性检查（树节点可能在折叠区域内）。

        Returns:
            成功勾选的 checkbox 数量
        """
        trees = await self.page.query_selector_all('.el-tree')
        if not trees:
            return 0

        checked = 0
        for tree in trees:
            try:
                # 先诊断树的状态
                tree_info = await tree.evaluate("""el => {
                    const nodes = el.querySelectorAll('.el-tree-node');
                    const expandIcons = el.querySelectorAll('.el-tree-node__expand-icon');
                    const checkboxes = el.querySelectorAll('.el-checkbox');
                    const visibleCheckboxes = Array.from(checkboxes).filter(cb => cb.offsetWidth > 0);

                    return {
                        nodeCount: nodes.length,
                        expandIconCount: expandIcons.length,
                        checkboxCount: checkboxes.length,
                        visibleCheckboxCount: visibleCheckboxes.length,
                        collapsedIcons: Array.from(expandIcons).filter(i => !i.classList.contains('expanded') && !i.classList.contains('is-leaf')).length,
                        checkedCount: Array.from(checkboxes).filter(cb => cb.classList.contains('is-checked')).length,
                        treeVisible: el.offsetWidth > 0,
                        treeRect: el.getBoundingClientRect(),
                    };
                }""")

                LOG.info(f"    树形诊断: nodes={tree_info['nodeCount']} checkboxes={tree_info['checkboxCount']} "
                        f"visible={tree_info['visibleCheckboxCount']} collapsed={tree_info['collapsedIcons']} "
                        f"checked={tree_info['checkedCount']}")

                # 如果 checkbox 不可见但有折叠的节点，先展开
                if tree_info['visibleCheckboxCount'] == 0 and tree_info['collapsedIcons'] > 0:
                    LOG.info(f"    树节点已折叠，尝试展开...")
                    expand_result = await self.page.evaluate("""el => {
                        const icons = el.querySelectorAll('.el-tree-node__expand-icon:not(.expanded):not(.is-leaf)');
                        let expanded = 0;
                        icons.forEach(icon => {
                            try {
                                icon.click();
                                expanded++;
                            } catch(e) {}
                        });
                        return { attempted: icons.length, expanded };
                    }""", tree)
                    LOG.info(f"    展开结果: {expand_result}")
                    await self.page.wait_for_timeout(500)

                # 使用 JavaScript 点击 checkbox（绕过可见性检查）
                js_click_result = await self.page.evaluate("""el => {
                    const result = { attempted: 0, clicked: 0, errors: [] };
                    const checkboxes = el.querySelectorAll('.el-checkbox__input');

                    for (const cb of checkboxes) {
                        result.attempted++;
                        if (result.clicked >= 3) break;  // 最多勾选3个

                        // 检查是否已选中
                        if (cb.closest('.el-checkbox')?.classList.contains('is-checked')) {
                            continue;
                        }

                        try {
                            // 使用 JavaScript click 绕过 Playwright 可见性检查
                            cb.click();
                            result.clicked++;
                        } catch(e) {
                            result.errors.push(e.message);
                        }
                    }
                    return result;
                }""", tree)

                checked += js_click_result['clicked']
                if js_click_result['clicked'] > 0:
                    LOG.info(f"    ✅ 通过 JS 勾选 {js_click_result['clicked']} 个 checkbox")
                if js_click_result['errors']:
                    LOG.warning(f"    JS 点击失败: {js_click_result['errors'][:3]}")

                await self.page.wait_for_timeout(300)

            except Exception as e:
                LOG.warning(f"    处理树形选择器失败: {e}")

        return checked

    async def submit_form_v2(self) -> str:
        """提交表单（v2 版本），返回提交结果。

        中文按钮文本常含空格（如 "确 定"、"保 存"），使用 JavaScript
        去掉空格后匹配，避免 Playwright has-text 匹配失败。

        返回格式: "submitted:{original_text}" 其中 original_text 是按钮原始文本（保留空格），
        供 build_playbook 生成精确的 playwright_locator。

        点击提交按钮后等待加载完成（可能触发页面刷新/跳转）。
        """
        from .wait_helpers import wait_for_loading_complete

        # JavaScript 方式：去掉空格后匹配常见提交按钮文本，返回原始文本
        try:
            clicked = await self.page.evaluate("""() => {
                const submitTexts = ['确定', '保存', '提交', '确认', '立即创建',
                    '完成', '更新', '修改', 'OK', 'Update', 'Save'];
                const buttons = Array.from(document.querySelectorAll('button'));
                // 优先找可见的按钮
                const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                for (const btn of visible) {
                    const originalText = btn.textContent.trim();
                    const normalized = originalText.replace(/\\s+/g, '');
                    if (submitTexts.some(t => normalized === t || normalized.includes(t))) {
                        btn.click();
                        return {normalized: normalized, original: originalText};
                    }
                }
                return null;
            }""")
            if clicked:
                await wait_for_loading_complete(self.page)
                # 使用原始文本（保留空格），供 build_playbook 生成精确 locator
                return f"submitted:{clicked['original']}"
        except Exception as e:
            LOG.debug(f"JavaScript 提交按钮匹配失败: {e}")

        # Playwright locator 回退
        for text in ['确定', '保存', '提交', '确认', '完成', '更新', 'OK']:
            try:
                btn = self.page.locator(f'button:has-text("{text}"):visible').first
                if await btn.count() > 0:
                    await btn.click()
                    await wait_for_loading_complete(self.page)
                    return f"submitted:{text}"
            except Exception as e:
                LOG.debug(f"尝试提交按钮 {text} 失败: {e}")
                continue

        # 最终回退: primary 按钮
        try:
            primary_btn = self.page.locator('button.el-button--primary:visible').first
            if await primary_btn.count() > 0:
                await primary_btn.click()
                await wait_for_loading_complete(self.page)
                return "submitted:primary"
        except Exception as e:
            LOG.debug(f"尝试 primary 按钮失败: {e}")

        return "not_found"


async def scan_form_fields(page: Page) -> List[Dict]:
    """扫描页面表单字段，按 .el-form-item 遍历，检测组件类型。

    以表单项为单位遍历，检测子元素的 DOM 类名判断组件类型。
    每个字段携带 kb_category，对应 probe_knowledge.json 的类别名。

    Returns:
        表单字段列表，每个字段包含：
        - label: 标签文本
        - type: 粗粒度类型 (input/textarea/select/cascader/date-picker/radio/checkbox)
        - kb_category: KB 类别名 (input-generic/el-select/el-cascader/...)
        - selector: CSS 选择器（仅 input/textarea 有值）
        - inputType: input type 属性（仅 input 有值）
        - required: 是否必填
        - firstOptionText: 第一个选项文本（仅 radio 有值）
    """
    script = """
    () => {
        const fields = [];

        // 以 .el-form-item / .ant-form-item 为单位遍历
        const formItems = document.querySelectorAll('.el-form-item, .ant-form-item');

        formItems.forEach(fi => {
            // 1. 提取 label
            const labelEl = fi.querySelector(
                '.el-form-item__label, .ant-form-item-label label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            if (!label) return;

            const required = fi.classList.contains('is-required') ||
                             fi.querySelector('[class*="required"]') !== null;

            // 跳过不可见的表单项
            const r = fi.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;

            // 2. 按优先级检测组件类型

            // 2.1 复合组件检测：el-select + 独立 input（如手机号带国家编码）
            const selectEl = fi.querySelector('.el-select, .ant-select');
            const independentInputs = Array.from(fi.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'))
                .filter(inp => !inp.readOnly && !selectEl?.contains(inp) && inp.offsetWidth > 0);

            if (selectEl && independentInputs.length > 0) {
                // 复合组件：先添加 select，再添加独立 input
                const selectIdx = fields.length;
                fields.push({ label: label + '(下拉)', type: 'select', kb_category: 'el-select',
                             selector: _buildComponentSelector(fi, '.el-select', selectIdx), required });
                independentInputs.forEach(inp => {
                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: _buildSelector(inp),
                                 inputType: inp.type || 'text', required });
                });
            }
            else if (fi.querySelector('.el-cascader')) {
                const cascaderIdx = fields.length;
                fields.push({ label, type: 'cascader', kb_category: 'el-cascader',
                             selector: _buildComponentSelector(fi, '.el-cascader', cascaderIdx), required });
            }
            else if (fi.querySelector('.el-date-editor, .ant-picker')) {
                const datePickerIdx = fields.length;
                fields.push({ label, type: 'date-picker', kb_category: 'date-picker',
                             selector: _buildComponentSelector(fi, '.el-date-editor, .ant-picker', datePickerIdx), required });
            }
            else if (selectEl) {
                const selectIdx = fields.length;
                fields.push({ label, type: 'select', kb_category: 'el-select',
                             selector: _buildComponentSelector(fi, '.el-select', selectIdx), required });
            }
            else if (fi.querySelector('.el-radio-group')) {
                const radioIdx = fields.length;
                const firstRadio = fi.querySelector('.el-radio-button__inner, .el-radio__label');
                fields.push({ label, type: 'radio', kb_category: 'radio',
                             selector: _buildComponentSelector(fi, '.el-radio-group', radioIdx), required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' });
            }
            else if (fi.querySelector('.el-checkbox') && !fi.closest('.el-table')) {
                const checkboxIdx = fields.length;
                fields.push({ label, type: 'checkbox', kb_category: 'form-checkbox',
                             selector: _buildComponentSelector(fi, '.el-checkbox', checkboxIdx), required });
            }
            else {
                // 通用 input / textarea
                // 检测复合输入组：多个可见 input（如手机号 = 国家编码 + 手机号）
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                )).filter(inp => !inp.readOnly && inp.offsetWidth > 0);

                const ta = fi.querySelector('textarea');

                if (allInputs.length >= 2) {
                    // 复合输入组：过滤辅助输入，选择主输入
                    const mainInput = allInputs.find(inp => {
                        // 优先选择 placeholder 包含"请输入"的
                        if (inp.placeholder && (inp.placeholder.includes('请输入') || inp.placeholder.includes('Enter'))) {
                            return true;
                        }
                        // 或选择宽度最大的
                        return false;
                    }) || allInputs.reduce((max, inp) => inp.offsetWidth > max.offsetWidth ? inp : max, allInputs[0]);

                    const ir = mainInput.getBoundingClientRect();
                    if (ir.width > 0 && ir.height > 0) {
                        fields.push({ label, type: 'input', kb_category: 'input-generic',
                                     selector: _buildSelector(mainInput),
                                     inputType: mainInput.type || 'text', required });
                    }
                } else if (allInputs.length === 1) {
                    // 单个 input
                    const inp = allInputs[0];
                    const ir = inp.getBoundingClientRect();
                    if (ir.width > 0 && ir.height > 0) {
                        fields.push({ label, type: 'input', kb_category: 'input-generic',
                                     selector: _buildSelector(inp),
                                     inputType: inp.type || 'text', required });
                    }
                } else if (ta) {
                    fields.push({ label, type: 'textarea', kb_category: 'textarea-generic',
                                 selector: _buildSelector(ta), required });
                }
            }
        });

        return fields;

        function _buildSelector(element) {
            if (element.id) return `#${element.id}`;
            if (element.name) return `[name="${element.name}"]`;

            const path = [];
            let current = element;
            while (current && current.nodeType === Node.ELEMENT_NODE) {
                let selector = current.nodeName.toLowerCase();
                if (current.id) {
                    selector = `#${current.id}`;
                    path.unshift(selector);
                    break;
                } else {
                    let sibling = current;
                    let nth = 1;
                    while (sibling.previousElementSibling) {
                        sibling = sibling.previousElementSibling;
                        if (sibling.nodeName === current.nodeName) nth++;
                    }
                    if (nth > 1 || current.nextElementSibling) {
                        selector += `:nth-of-type(${nth})`;
                    }
                }
                path.unshift(selector);
                current = current.parentElement;
            }
            return path.join(' > ');
        }

        function _buildComponentSelector(formItem, componentSelector, fieldIndex) {
            // 为复杂组件（select/cascader/date-picker/radio/checkbox）生成 CSS selector
            const component = formItem.querySelector(componentSelector);
            if (!component) return null;

            // 使用 formItem 在直接子元素中的位置定位（避免 :has() 伪类，Playwright 不支持）
            // 注意：children 只返回直接子元素，querySelectorAll 会递归搜索所有后代
            // 对于多卡片表单（.el-card 包裹），必须用 children 而非 querySelectorAll
            const directChildren = formItem.parentElement.children;
            let nth = 1;
            for (let i = 0; i < directChildren.length; i++) {
                if (directChildren[i] === formItem) {
                    nth = i + 1;
                    break;
                }
            }
            return `.el-form-item:nth-child(${nth}) ${componentSelector}`;
        }
    }
    """

    return await page.evaluate(script)


async def fill_form(page: Page, fields: List[Dict], data: Dict[str, str]) -> Tuple[int, int]:
    """填充表单字段。

    Args:
        page: Playwright 页面对象
        fields: 表单字段列表（来自 scan_form_fields）
        data: 填充数据 {label: value}

    Returns:
        (成功填充数, 总字段数)
    """
    filled = 0
    total = len(fields)

    for field in fields:
        label = field['label']
        if label not in data:
            continue

        value = data[label]
        selector = field['selector']
        field_type = field['type']

        try:
            if field_type == 'input' or field_type == 'textarea':
                # 清空并填充
                await page.fill(selector, '')
                await page.fill(selector, value)
                filled += 1

            elif field_type == 'select':
                # Element UI 下拉框
                await page.click(selector)
                await page.wait_for_timeout(500)

                # 选择匹配的选项
                options = await page.query_selector_all('.el-select-dropdown__item:visible')
                for opt in options:
                    text = await opt.inner_text()
                    if value in text:
                        await opt.click()
                        filled += 1
                        break

                await page.wait_for_timeout(300)

        except Exception as e:
            print(f"  ⚠️ 填充字段 {label} 失败: {e}")

    return filled, total


async def select_dropdown_option(page: Page, selector: str, value: str) -> bool:
    """选择下拉框选项。

    Args:
        page: Playwright 页面对象
        selector: 下拉框选择器
        value: 要选择的值（部分匹配）

    Returns:
        是否成功选择
    """
    try:
        # 点击下拉框
        await page.click(selector)
        await page.wait_for_timeout(500)

        # 查找并选择选项
        options = await page.query_selector_all('.el-select-dropdown__item:visible')
        for opt in options:
            text = await opt.inner_text()
            if value in text:
                await opt.click()
                await page.wait_for_timeout(300)
                return True

        # 未找到匹配选项，点击空白关闭
        await page.keyboard.press('Escape')
        return False

    except Exception as e:
        print(f"  ⚠️ 选择下拉框失败: {e}")
        return False


async def submit_form(page: Page, button_text: str = "提交") -> bool:
    """提交表单。

    Args:
        page: Playwright 页面对象
        button_text: 提交按钮文本

    Returns:
        是否成功点击提交按钮
    """
    try:
        # 查找提交按钮
        buttons = await page.query_selector_all('button:visible')
        for btn in buttons:
            text = await btn.inner_text()
            if button_text in text:
                await btn.click()
                await page.wait_for_timeout(1000)
                return True

        # 尝试常见提交按钮文本
        for text in ['确定', '保存', '提交', '确认']:
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible')
            if buttons:
                await buttons[0].click()
                await page.wait_for_timeout(1000)
                return True

        return False

    except Exception as e:
        print(f"  ⚠️ 提交表单失败: {e}")
        return False


async def read_form_errors(page: Page) -> List[Dict]:
    """获取表单验证错误信息（结构化，含字段定位）。

    与旧版 get_form_errors() 的区别：
    - 返回 dict 列表，含 field_label + error_text + severity
    - 尝试将错误信息关联到具体的表单字段
    - 支持 Element UI 和 Ant Design

    Returns:
        [{"field_label": "用户名", "error_text": "用户名已存在", "severity": "field"}, ...]
    """
    script = """
    () => {
        const errors = [];

        // Element UI: 字段级错误
        document.querySelectorAll('.el-form-item').forEach(fi => {
            const errorEl = fi.querySelector('.el-form-item__error');
            if (!errorEl) return;
            const text = errorEl.textContent.trim();
            if (!text) return;
            const labelEl = fi.querySelector('.el-form-item__label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            errors.push({
                field_label: label,
                error_text: text,
                severity: 'field',
                source: 'element-ui'
            });
        });

        // Ant Design: 字段级错误
        document.querySelectorAll('.ant-form-item').forEach(fi => {
            const errorEl = fi.querySelector('.ant-form-item-explain-error, .ant-form-item-explain');
            if (!errorEl) return;
            const text = errorEl.textContent.trim();
            if (!text) return;
            const labelEl = fi.querySelector('.ant-form-item-label label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            errors.push({
                field_label: label,
                error_text: text,
                severity: 'field',
                source: 'ant-design'
            });
        });

        // 全局错误消息（El-Message / El-Notification / Ant-Message）
        const globalSelectors = [
            '.el-message--error', '.el-notification__content',
            '.ant-message-error', '.ant-notification-notice'
        ];
        document.querySelectorAll(globalSelectors.join(',')).forEach(el => {
            const text = el.textContent.trim();
            if (!text) return;
            errors.push({
                field_label: '',
                error_text: text,
                severity: 'global',
                source: 'message'
            });
        });

        return errors;
    }
    """

    try:
        return await page.evaluate(script)
    except Exception as e:
        LOG.debug(f"读取表单错误失败: {e}")
        return []


async def get_form_errors(page: Page) -> List[str]:
    """获取表单验证错误信息（纯文本列表，向后兼容）。

    Returns:
        错误信息列表
    """
    errors = await read_form_errors(page)
    return [e["error_text"] for e in errors if e.get("error_text")]


def generate_fill_rules(fields: List[Dict]) -> dict:
    """根据表单字段生成填充规则（而非具体值）。

    Stage 1 保存"规则"，Stage 2 根据规则生成"新值"，避免值重复。

    规则类型：
    - username_pattern: 生成唯一用户名
    - email_pattern: 生成测试邮箱
    - phone_pattern: 生成手机号
    - password_fixed: 固定测试密码
    - code_pattern: 生成编码
    - description_pattern: 生成描述
    - name_pattern: 生成名称
    - fixed_value: 固定值（不需要变化的字段）

    Args:
        fields: scan_form_fields_v2() 返回的字段列表

    Returns:
        {label: {"rule": str, "params": dict}} 字典
    """
    rules = {}

    for field in fields:
        label = field.get("label", "")
        if not label:
            continue

        field_type = field.get("type", "input")
        input_type = field.get("inputType", "text")
        required = field.get("required", False)

        # 根据字段标签和类型推断规则
        rule = _infer_fill_rule(label, field_type, input_type, required)
        rules[label] = rule

    return rules


def _infer_fill_rule(label: str, field_type: str, input_type: str, required: bool) -> dict:
    """推断字段的填充规则。"""
    label_lower = label.lower()

    # 用户名相关
    if any(kw in label_lower for kw in ["用户名", "账号", "username", "account"]):
        return {"rule": "username_pattern", "params": {"prefix": "autotest"}}

    # 名称相关（非用户名）
    if any(kw in label_lower for kw in ["名称", "name", "姓名"]) and "user" not in label_lower:
        return {"rule": "name_pattern", "params": {"prefix": "test"}}

    # 邮箱
    if any(kw in label_lower for kw in ["邮箱", "email", "邮件"]):
        return {"rule": "email_pattern", "params": {"domain": "test.com"}}

    # 手机号
    if any(kw in label_lower for kw in ["手机", "电话", "phone", "mobile", "tel"]):
        return {"rule": "phone_pattern", "params": {"prefix": "138"}}

    # 密码
    if any(kw in label_lower for kw in ["密码", "password", "pwd"]):
        return {"rule": "password_fixed", "params": {"value": "Test@123456"}}

    # 编码
    if any(kw in label_lower for kw in ["编码", "code", "编号"]):
        return {"rule": "code_pattern", "params": {"prefix": "code"}}

    # 描述/备注
    if any(kw in label_lower for kw in ["描述", "备注", "说明", "description", "remark", "memo"]):
        return {"rule": "description_pattern", "params": {"prefix": "auto"}}

    # 根据 inputType 推断
    if input_type == "email":
        return {"rule": "email_pattern", "params": {"domain": "test.com"}}
    if input_type == "tel":
        return {"rule": "phone_pattern", "params": {"prefix": "138"}}
    if input_type == "password":
        return {"rule": "password_fixed", "params": {"value": "Test@123456"}}
    if input_type == "number":
        return {"rule": "number_pattern", "params": {"min": 1, "max": 100}}

    # 默认规则
    if field_type == "textarea":
        return {"rule": "description_pattern", "params": {"prefix": "auto"}}

    return {"rule": "name_pattern", "params": {"prefix": "test"}}


def apply_fill_rule(rule: dict, timestamp: str = None) -> str:
    """根据规则生成具体的填充值。

    Args:
        rule: {"rule": str, "params": dict} 格式的规则
        timestamp: 时间戳（可选，用于生成唯一值）

    Returns:
        生成的具体值
    """
    if not timestamp:
        timestamp = str(int(time.time()))[-6:]

    rule_type = rule.get("rule", "name_pattern")
    params = rule.get("params", {})

    if rule_type == "username_pattern":
        prefix = params.get("prefix", "autotest")
        return f"{prefix}{timestamp}"

    if rule_type == "email_pattern":
        domain = params.get("domain", "test.com")
        return f"at_{timestamp}@{domain}"

    if rule_type == "phone_pattern":
        prefix = params.get("prefix", "138")
        import random
        return f"{prefix}{random.randint(10000000, 99999999)}"

    if rule_type == "password_fixed":
        return params.get("value", "Test@123456")

    if rule_type == "code_pattern":
        prefix = params.get("prefix", "code")
        return f"{prefix}_{timestamp}"

    if rule_type == "description_pattern":
        prefix = params.get("prefix", "auto")
        return f"{prefix}_desc_{timestamp}"

    if rule_type == "name_pattern":
        prefix = params.get("prefix", "test")
        return f"{prefix}_{timestamp}"

    if rule_type == "number_pattern":
        min_val = params.get("min", 1)
        max_val = params.get("max", 100)
        # 使用时间戳后几位作为数字
        num = int(timestamp) % (max_val - min_val + 1) + min_val
        return str(num)

    if rule_type == "fixed_value":
        return params.get("value", "")

    # 未知规则，返回默认值
    return f"test_{timestamp}"


def generate_fill_data(fields: List[Dict], username: str = "test") -> dict:
    """根据表单字段生成填充数据（独立函数，供 Stage 1/2 直接调用）。

    从 FormFiller._build_fill_data 抽取的公开版本，无需实例化 FormFiller。

    策略：
    1. 通用字段映射（硬编码，覆盖常见场景）
    2. 未匹配的字段按 type/inputType 生成默认值

    Args:
        fields: scan_form_fields_v2() 返回的字段列表
        username: 用户名（用于名称/用户名类字段）

    Returns:
        {label: value} 字典
    """
    import random
    ts = str(int(time.time()))[-6:]

    # 生成 11 位手机号
    phone = f"138{random.randint(10000000, 99999999)}"

    # 通用字段映射
    fill_data = {
        # 通用字段
        "名称": username, "编码": f"code_{ts}",
        "描述": f"自动测试创建于{ts}", "备注": f"自动创建于{ts}",
        "说明": f"自动测试创建于{ts}",
        # 用户管理
        "用户名": username, "姓名": username,
        "邮箱": f"at_{ts}@test.com", "手机": phone,
        "密码": "Test@123456", "确认密码": "Test@123456",
        # 角色管理
        "角色名称": username, "角色编码": f"role_code_{ts}",
        "角色描述": f"自动测试角色创建于{ts}",
        # 英文字段
        "username": username, "name": username,
        "email": f"at_{ts}@test.com", "phone": phone,
        "password": "Test@123456", "description": f"自动测试创建于{ts}",
        "code": f"code_{ts}", "remark": f"自动创建于{ts}",
    }

    # 为未匹配的字段按 type 生成默认值
    for field in fields:
        label = field.get("label", "")
        if not label:
            continue
        # 检查是否已被覆盖
        if any(key in label or label in key for key in fill_data):
            continue
        field_type = field.get("type", "input")
        input_type = field.get("inputType", "text")
        fill_data[label] = FormFiller._default_for_type(field_type, input_type, ts, username)

    return fill_data


class MultiStepExecutor:
    """多步交互执行器：根据 KB multi_step 模板驱动复杂组件交互。

    支持 el-select、el-cascader、date-picker、form-checkbox 四种组件类型。
    每种组件有专属的执行序列，从 KB 读取步骤模板并按序执行。
    """

    def __init__(self, page: Page, kb, framework: str = "element-ui"):
        """初始化执行器。

        Args:
            page: Playwright Page 对象
            kb: ProbeKB 知识库实例
            framework: UI 框架 ("element-ui" 或 "ant-design")
        """
        self.page = page
        self.kb = kb
        self.framework = framework

    async def execute(self, kb_category: str, label: str,
                     option_text: str = "", options: list = None,
                     selector: str = "") -> bool:
        """根据 kb_category 分发到对应的执行方法。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader", "date-picker"）
            label: 字段标签（用于定位组件）
            option_text: 要选择的选项文本
            options: 级联选择的多级选项列表（如 ["uiautotest", "uiautotest"]）
            selector: CSS selector（来自 playbook，直接定位元素）

        Returns:
            bool: 是否成功完成交互
        """
        handlers = {
            "el-select": self._execute_select,
            "el-cascader": self._execute_cascader,
            "date-picker": self._execute_date_picker,
            "form-checkbox": self._execute_form_checkbox,
        }
        handler = handlers.get(kb_category)
        if not handler:
            LOG.warning(f"MultiStepExecutor: 无处理器 {kb_category}")
            return False

        try:
            return await handler(label, option_text, options, selector)
        except Exception as e:
            LOG.warning(f"MultiStepExecutor: {kb_category}({label}) 失败: {e}")
            return False

    async def execute_with_details(self, kb_category: str, label: str,
                                   option_text: str = "", options: list = None,
                                   selector: str = "") -> Tuple[bool, Dict]:
        """根据 kb_category 分发到对应的执行方法，并返回详细执行信息。

        与 execute() 类似，但返回元组 (成功标志, 详情字典)。
        详情字典包含 is_editable 和 option_text 等运行时信息。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader", "date-picker"）
            label: 字段标签（用于定位组件）
            option_text: 要选择的选项文本
            options: 级联选择的多级选项列表（如 ["uiautotest", "uiautotest"]）
            selector: CSS selector（来自 playbook，直接定位元素）

        Returns:
            Tuple[bool, Dict]: (是否成功完成交互, 详情字典)
        """
        handlers = {
            "el-select": self._execute_select_with_details,
            "el-cascader": self._execute_cascader,  # 暂未实现详情版本
            "date-picker": self._execute_date_picker,  # 暂未实现详情版本
            "form-checkbox": self._execute_form_checkbox,  # 暂未实现详情版本
        }
        handler = handlers.get(kb_category)
        if not handler:
            LOG.warning(f"MultiStepExecutor: 无处理器 {kb_category}")
            return False, {}

        try:
            if kb_category == "el-select":
                return await handler(label, option_text, options, selector)
            else:
                # 其他类型暂时只返回成功标志，详情为空字典
                success = await handler(label, option_text, options, selector)
                return success, {}
        except Exception as e:
            LOG.warning(f"MultiStepExecutor: {kb_category}({label}) 失败: {e}")
            return False, {}

    async def _execute_select_with_details(self, label: str, option_text: str,
                                           _options, selector: str = "") -> Tuple[bool, Dict]:
        """el-select 多步交互，并返回详细的执行信息。

        Returns:
            Tuple[bool, Dict]: (是否成功, {"is_editable": bool, "option_text": str})
        """
        steps = self.kb.get_steps("el-select", self.framework)
        if not steps:
            return False, {}

        # Step 1: expand（必须使用 selector，不做 KB XPath 回退）
        if not selector:
            LOG.warning(f"    el-select {label} 缺少 selector（Stage 1 未提供）")
            return False, {}

        expanded = await self._expand_by_selector(selector, label)
        if not expanded:
            LOG.debug(f"    el-select {label}: expand failed")
            return False, {}
        await self.page.wait_for_timeout(500)

        # Step 2: editable-check
        editable_patterns = steps.get("editable-check", {}).get("patterns", [])
        is_editable = await self._check_element_visible(editable_patterns, label=label)
        LOG.debug(f"    el-select {label}: is_editable={is_editable}")

        # Step 3: 等待选项加载
        await self._wait_for_options_loaded()

        # Step 4: 自动发现选项
        if not option_text:
            option_text = await self._discover_first_option()
            LOG.debug(f"    el-select {label}: first discovery option_text={repr(option_text)}")
            # 重试一次：Element UI 下拉面板可能需要额外渲染时间
            if not option_text:
                await self.page.wait_for_timeout(500)
                option_text = await self._discover_first_option(timeout=2000)
                LOG.debug(f"    el-select {label}: retry discovery option_text={repr(option_text)}")

        detail = {
            "is_editable": is_editable,
            "option_text": option_text or "",
        }

        if not option_text:
            # 记录详细的下拉框状态以便调试
            dd_state = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                const result = [];
                for (const dd of dds) {
                    const items = dd.querySelectorAll('.el-select-dropdown__item');
                    const visibleItems = Array.from(items).filter(it =>
                        it.offsetWidth > 0 && it.offsetHeight > 0
                    );
                    result.push({
                        visible: dd.offsetWidth > 0 && dd.offsetHeight > 0,
                        display: window.getComputedStyle(dd).display,
                        totalItems: items.length,
                        visibleItems: visibleItems.length,
                        firstItemText: items.length > 0 ? items[0].textContent.trim() : null,
                        firstVisibleText: visibleItems.length > 0 ? visibleItems[0].textContent.trim() : null
                    });
                }
                return result;
            }""")
            # 判断是否真的无选项（而非加载慢）
            has_visible_dropdown = any(d.get('visible') for d in dd_state)
            has_any_visible_items = any(
                d.get('visible') and d.get('totalItems', 0) > 0 for d in dd_state
            )
            if has_visible_dropdown and not has_any_visible_items:
                LOG.info(f"    el-select {label}: 下拉框已展开但无选项，跳过（非必填或暂无可选项）")
                detail["skipped_reason"] = "no_options"
                return False, detail
            LOG.warning(f"    el-select {label} 未发现可选项，下拉框状态: {json.dumps(dd_state, ensure_ascii=False)}")
            return False, detail

        # Step 5: 根据是否可编辑选择不同策略
        LOG.info(f"    el-select {label}: proceeding with is_editable={is_editable}, option_text='{option_text}'")
        if is_editable:
            # 可编辑：先输入搜索，再选择
            filled = await self._try_step_patterns(steps.get("fill", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: fill result={filled}")
            if not filled:
                return False, detail

            # 关键修复：fill 只 click 了输入框，需要实际输入搜索文本
            typed = await self._type_search_text(label, option_text, selector)
            LOG.info(f"    el-select {label}: type search result={typed}")
            await self.page.wait_for_timeout(500)  # 等待搜索过滤完成

            # 检查 fill 后下拉是否仍然展开
            post_fill = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                const visible = Array.from(dds).filter(dd => dd.offsetWidth > 0 && dd.offsetHeight > 0);
                return visible.length;
            }""")
            LOG.info(f"    el-select {label}: visible dropdowns after fill={post_fill}")

            # 如果 fill 导致下拉关闭，重新展开
            if post_fill == 0:
                LOG.info(f"    el-select {label}: fill closed dropdown, re-expanding")
                re_expanded = await self._expand_by_selector(selector, label)
                if re_expanded:
                    await self.page.wait_for_timeout(500)
                    # 等待选项加载
                    await self._wait_for_options_loaded()

            selected = await self._try_step_patterns(
                steps.get("select", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: select result={selected}")
            # KB XPath 失败时回退：直接点击可见选项
            if not selected:
                selected = await self._click_visible_option(option_text)
                LOG.info(f"    el-select {label}: direct click result={selected}")
            return selected, detail
        else:
            # 不可编辑：等待选项加载，再选择第一个选项
            await self._wait_for_options_loaded()
            selected = await self._try_step_patterns(
                steps.get("first-option", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: first-option result={selected}")
            # KB XPath 失败时回退：直接点击可见选项
            if not selected:
                selected = await self._click_visible_option(option_text)
                LOG.info(f"    el-select {label}: direct click result={selected}")
            return selected, detail

    async def _execute_select(self, label: str, option_text: str, _options, selector: str = "") -> bool:
        """el-select 多步交互：使用 Stage 1 提供的 selector 展开"""
        steps = self.kb.get_steps("el-select", self.framework)
        if not steps:
            return False

        # Step 1: expand（必须使用 selector，不做 KB XPath 回退）
        if not selector:
            LOG.warning(f"    el-select {label} 缺少 selector（Stage 1 未提供）")
            return False

        expanded = await self._expand_by_selector(selector, label)
        if not expanded:
            return False
        await self.page.wait_for_timeout(500)

        # Step 1.5: 自动发现选项（新增）
        if not option_text:
            option_text = await self._discover_first_option()

        # Step 2: editable-check — 判断是否可搜索
        editable_patterns = steps.get("editable-check", {}).get("patterns", [])
        is_editable = await self._check_element_visible(editable_patterns, label=label)

        if is_editable and option_text:
            # 可搜索：输入关键词 → 选择匹配项
            await self._try_step_patterns(steps.get("fill", {}), label=label)
            await self.page.wait_for_timeout(300)
            selected = await self._try_step_patterns(
                steps.get("select", {}), label=label, option_text=option_text)
            return selected
        else:
            # 不可搜索或无指定选项：选第一项
            selected = await self._try_step_patterns(
                steps.get("first-option", {}), label=label)
            return selected

    async def _expand_by_selector(self, selector: str, label: str) -> bool:
        """用 CSS selector 点击展开 el-select 下拉框。

        selector 来自 Stage 1 playbook，可能是 .el-select 或 input 的选择器。
        """
        try:
            # 取第一个 selector（可能有逗号分隔的备选）
            primary = selector.split(",")[0].strip()

            # 尝试点击 .el-select 的 input 区域来展开
            el = self.page.locator(primary).first
            el_count = await el.count()
            if el_count == 0:
                LOG.info(f"    el-select {label}: selector 未匹配 ({primary})")
                return False

            await el.click(timeout=3000)
            LOG.info(f"    el-select {label}: selector 展开成功 ({primary})")
            return True
        except Exception as e:
            LOG.info(f"    el-select {label}: selector 展开失败: {e}")
            return False

    async def _execute_cascader(self, label: str, _option_text, options: list, selector: str = "") -> bool:
        """el-cascader 多步交互：expand → expand-level(逐级) → conditional-branch → select-last"""
        steps = self.kb.get_steps("el-cascader", self.framework)
        if not steps:
            return False

        # Step 1: expand
        expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
        if not expanded:
            return False
        await self.page.wait_for_timeout(500)

        # Step 2: 根据是否提供 options 选择不同模式
        if options:
            # 外部提供了完整路径：按路径逐级展开+选择
            return await self._cascader_select_by_path(steps, label, options)
        else:
            # 无路径：逐级发现+选择
            return await self._cascader_discover_and_select(steps, label)

    async def _execute_date_picker(self, label: str, option_text: str, _options, selector: str = "") -> bool:
        """date-picker 多步交互：expand → select-today/select-now"""
        steps = self.kb.get_steps("date-picker", self.framework)
        if not steps:
            return False

        # Step 1: expand
        expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
        if not expanded:
            return False
        await self.page.wait_for_timeout(500)

        # Step 2: 选择今天（默认策略）
        selected = await self._try_step_patterns(steps.get("select-today", {}), label=label)
        if not selected:
            # 回退：选择此刻
            selected = await self._try_step_patterns(steps.get("select-now", {}), label=label)
        return selected

    async def _execute_form_checkbox(self, label: str, option_text: str, _options, selector: str = "") -> bool:
        """form-checkbox：勾选指定选项"""
        patterns = self.kb.get_patterns("form-checkbox", self.framework)
        for p in patterns:
            xpath = self.kb.expand_step(
                p, field_text=label, option_text=option_text,
                framework=self.framework, apply_hidden=True
            )
            if await self._click_by_xpath(xpath):
                return True

        # 兜底：尝试 fallback_strategies
        return await self._try_fallback(label)

    # ---- 内部工具方法 ----

    async def _detect_active_overlay(self) -> str:
        """检测当前页面上活跃的覆盖层（弹窗/抽屉）。

        Returns:
            str: 覆盖层的 XPath 前缀，无活跃覆盖层时返回空串
        """
        from .kb_loader import detect_active_overlay_js, get_overlay_prefix

        js = detect_active_overlay_js(self.framework)
        try:
            overlay_type = await self.page.evaluate(js)
            return get_overlay_prefix(overlay_type, self.framework)
        except Exception:
            return ""

    async def _try_step_patterns(self, step: dict, **placeholder_kwargs) -> bool:
        """尝试一个步骤的所有 pattern，任一成功即返回 True。"""
        # 检测活跃覆盖层（每次调用时检测，因为页面状态可能变化）
        overlay_prefix = await self._detect_active_overlay()

        for pattern in step.get("patterns", []):
            xpath = self.kb.expand_step(
                pattern,
                framework=self.framework,
                apply_hidden=True,
                **placeholder_kwargs
            )

            # Debug: log the generated XPath
            LOG.debug(f"    XPath pattern: {xpath[:120]}...")

            # 如果有活跃覆盖层，先尝试在覆盖层内定位
            if overlay_prefix:
                from .kb_loader import apply_overlay_scope
                scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
                if await self._click_by_xpath(scoped_xpath):
                    return True

            # 回退：在全局范围定位
            if await self._click_by_xpath(xpath):
                LOG.debug(f"    ✓ XPath matched")
                return True

        LOG.debug(f"    ✗ All XPath patterns failed")
        return False

    async def _click_by_xpath(self, xpath: str, timeout: int = 2000) -> bool:
        """通过 XPath 点击元素，支持 iframe 遍历。

        注意：不等待加载完成，适用于输入型组件（下拉框、日期选择器等）的展开操作。
        按钮类型的点击请在 button_driver 中手动调用 wait_for_loading_complete。
        """
        # 主页面尝试
        if await self._try_click_frame(self.page, xpath, timeout):
            return True

        # iframe 遍历
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            if await self._try_click_frame(frame, xpath, timeout):
                return True

        return False

    async def _try_click_frame(self, frame, xpath: str, timeout: int = 2000) -> bool:
        """在指定 frame 中尝试点击（不等待加载）。"""
        try:
            el = frame.locator(f"xpath={xpath}")
            if await el.count() > 0:
                await el.click(timeout=timeout)
                return True
        except Exception:
            pass
        return False

    async def _check_element_visible(self, patterns: list, timeout: int = 500,
                                     **placeholder_kwargs) -> bool:
        """检查任一 pattern 匹配的元素是否可见。"""
        for p in patterns:
            xpath = self.kb.expand_step(p, **placeholder_kwargs)
            if await self._check_element_visible_raw(xpath, timeout):
                return True
        return False

    async def _check_element_visible_raw(self, xpath: str, timeout: int = 500) -> bool:
        """检查 XPath 匹配的元素是否可见。"""
        try:
            el = self.page.locator(f"xpath={xpath}")
            return await el.count() > 0
        except Exception:
            return False

    async def _try_fallback(self, label: str) -> bool:
        """当 KB 标准模板全部失败时，尝试 fallback_strategies。"""
        strategies = self.kb.get_fallback_strategies()
        for strategy in strategies:
            for pattern in strategy.get("patterns", []):
                selector = self.kb.expand_step(pattern, label=label)
                try:
                    # Playwright 原生选择器（role=, text=, xpath=）
                    el = self.page.locator(selector)
                    if await el.count() > 0:
                        await el.click(timeout=2000)
                        LOG.info(f"    fallback 成功: {strategy['name']} → {label}")
                        return True
                except Exception:
                    continue
        return False

    # ---- 选项自动发现 ----

    async def _discover_first_option(self, timeout: int = 3000) -> str:
        """展开下拉框后，读取第一个可见选项的文本。

        支持 Element UI 和 Ant Design 两种框架。
        Element UI 的下拉面板会被 teleport 到 <body>，需要等待异步渲染。

        Args:
            timeout: 等待下拉面板出现的超时时间（毫秒）

        Returns:
            第一个可见选项的文本，无选项时返回空串
        """
        if self.framework == "ant-design":
            # 等待 Ant Design 下拉选项出现
            try:
                await self.page.wait_for_selector(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    '.ant-select-item-option:not(.ant-select-item-option-disabled)',
                    state='visible',
                    timeout=timeout
                )
            except Exception:
                pass  # 继续尝试读取，可能已经出现

            js = """() => {
                const item = document.querySelector(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    + '.ant-select-item-option:not(.ant-select-item-option-disabled)');
                return item ? item.textContent.trim() : '';
            }"""
        else:
            # 等待 Element UI 下拉选项出现（teleport 到 <body>）
            try:
                await self.page.wait_for_selector(
                    '.el-select-dropdown .el-select-dropdown__item:not(.is-disabled)',
                    state='visible',
                    timeout=timeout
                )
            except Exception:
                pass  # 继续尝试读取，可能已经出现

            js = """() => {
                const result = {found: false, text: '', debug: {dropdowns: []}};
                const dropdowns = document.querySelectorAll('.el-select-dropdown');
                for (const dd of dropdowns) {
                    const ddInfo = {
                        visible: dd.offsetWidth > 0 && dd.offsetHeight > 0,
                        width: dd.offsetWidth,
                        height: dd.offsetHeight,
                        items: []
                    };
                    const items = dd.querySelectorAll('.el-select-dropdown__item:not(.is-disabled)');
                    for (const item of items) {
                        const itemInfo = {
                            text: item.textContent.trim(),
                            visible: item.offsetWidth > 0 && item.offsetHeight > 0
                        };
                        ddInfo.items.push(itemInfo);
                        if (!result.found && item.offsetWidth > 0 && item.offsetHeight > 0) {
                            result.found = true;
                            result.text = item.textContent.trim();
                        }
                    }
                    result.debug.dropdowns.push(ddInfo);
                }
                return result;
            }"""
        try:
            result = await self.page.evaluate(js)
            if isinstance(result, dict):
                if result.get('found'):
                    LOG.info(f"    _discover_first_option: found option '{result['text']}'")
                    return result['text']
                else:
                    LOG.info(f"    _discover_first_option: no visible option, debug={result.get('debug')}")
                    return ""
            else:
                return result if result else ""
        except Exception as e:
            LOG.info(f"    _discover_first_option exception: {e}")
            return ""

    async def _type_search_text(self, label: str, text: str, selector: str = "") -> bool:
        """在 el-select 输入框中输入搜索文本。

        Args:
            label: 字段标签
            text: 要输入的搜索文本
            selector: el-select 的 CSS selector（用于 fallback）

        Returns:
            是否成功输入
        """
        # 方案 A：通过 label-relative XPath 定位输入框
        xpaths = [
            f"//*[contains(text(),'{label}')]/following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
            f"//label[contains(.,'{label}')]//following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
        ]
        for xpath in xpaths:
            try:
                input_el = self.page.locator(f"xpath={xpath}").first
                # 检查是否为 readonly
                is_readonly = await input_el.get_attribute("readonly")
                if is_readonly is not None:
                    LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                    return False
                await input_el.fill("")  # 清空
                await input_el.type(text, delay=50)  # 逐字符输入，触发搜索
                LOG.debug(f"    已输入搜索文本: {text}")
                return True
            except Exception:
                continue

        # 方案 B：fallback 到 selector 对应的输入框
        if selector:
            try:
                # selector 是 el-select 容器，需要找到其中的 input
                input_selector = f"{selector} input.el-input__inner"
                input_el = self.page.locator(input_selector).first
                # 检查是否为 readonly
                is_readonly = await input_el.get_attribute("readonly")
                if is_readonly is not None:
                    LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                    return False
                await input_el.fill("")
                await input_el.type(text, delay=50)
                LOG.debug(f"    已输入搜索文本 (selector fallback): {text}")
                return True
            except Exception as e:
                LOG.warning(f"    无法输入搜索文本 (selector): {e}")

        # 方案 C：fallback 到最近展开的 dropdown 对应的输入框
        try:
            input_el = self.page.locator(".el-select-dropdown:not([style*='display: none']) + * input.el-input__inner").first
            # 检查是否为 readonly
            is_readonly = await input_el.get_attribute("readonly")
            if is_readonly is not None:
                LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                return False
            await input_el.fill("")
            await input_el.type(text, delay=50)
            LOG.debug(f"    已输入搜索文本 (CSS fallback): {text}")
            return True
        except Exception as e:
            LOG.warning(f"    无法输入搜索文本: {e}")
            return False

    async def _click_visible_option(self, option_text: str = "") -> bool:
        """直接点击可见的下拉选项（KB XPath 失败时的回退）。

        Args:
            option_text: 要选择的选项文本，为空则选择第一个可见选项

        Returns:
            是否成功点击
        """
        try:
            if option_text:
                # 点击指定文本的选项
                clicked = await self.page.evaluate("""(text) => {
                    const dds = document.querySelectorAll('.el-select-dropdown');
                    for (const dd of dds) {
                        if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;
                        const items = dd.querySelectorAll('.el-select-dropdown__item');
                        for (const item of items) {
                            if (item.textContent.trim() === text) {
                                item.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""", option_text)
            else:
                # 点击第一个可见选项
                clicked = await self.page.evaluate("""() => {
                    const dds = document.querySelectorAll('.el-select-dropdown');
                    for (const dd of dds) {
                        if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;
                        const items = dd.querySelectorAll('.el-select-dropdown__item:not(.is-disabled)');
                        for (const item of items) {
                            if (item.offsetWidth > 0 && item.offsetHeight > 0) {
                                item.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""")
            return bool(clicked)
        except Exception as e:
            LOG.debug(f"    _click_visible_option exception: {e}")
            return False

    async def _wait_for_options_loaded(self) -> bool:
        """等待下拉选项加载完成（最多 4 秒）。

        Returns:
            bool: 是否成功加载选项
        """
        for i in range(8):  # 8 * 500ms = 4 秒
            state = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                for (const dd of dds) {
                    if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;
                    const items = dd.querySelectorAll('.el-select-dropdown__item');
                    return {
                        found: true,
                        visible: true,
                        itemCount: items.length,
                        firstItemText: items.length > 0 ? items[0].textContent.trim() : null
                    };
                }
                return {found: false, visible: false, itemCount: 0, firstItemText: null};
            }""")
            if state['found'] and state['itemCount'] > 0:
                LOG.debug(f"    选项加载完成: {state['itemCount']} 个选项, 第一个: '{state['firstItemText']}'")
                return True
            LOG.debug(f"    等待选项加载... 第 {i+1}/8 次检查, 当前状态: {state}")
            await self.page.wait_for_timeout(500)
        LOG.warning(f"    等待选项加载超时 (4 秒)")
        return False

    async def _read_cascader_current_items(self) -> list:
        """读取级联选择器当前（最后）面板的菜单项。

        Returns:
            [{"text": str, "hasChildren": bool}] 列表
        """
        if self.framework == "ant-design":
            js = """() => {
                const menus = document.querySelectorAll('.ant-cascader-menu');
                if (!menus.length) return [];
                const last = menus[menus.length - 1];
                const items = last.querySelectorAll(
                    '.ant-cascader-menu-item:not(.ant-cascader-menu-item-disabled)');
                return Array.from(items).map(item => ({
                    text: item.textContent.trim(),
                    hasChildren: item.querySelector(
                        '.ant-cascader-menu-item-expand-icon, '
                        + '[class*="arrow"], [class*="right"]') !== null
                })).filter(i => i.text);
            }"""
        else:
            js = """() => {
                const menus = document.querySelectorAll('.el-cascader-menu');
                if (!menus.length) return [];
                const last = menus[menus.length - 1];
                const items = last.querySelectorAll('li[role="menuitem"]');
                return Array.from(items).map(item => ({
                    text: (item.querySelector('span') || item).textContent.trim(),
                    hasChildren: item.querySelector(
                        '.el-icon-arrow-right, '
                        + 'i[class*="arrow-right"], '
                        + '[class*="postfix"]') !== null
                })).filter(i => i.text);
            }"""
        try:
            return await self.page.evaluate(js)
        except Exception:
            return []

    async def _cascader_select_by_path(self, steps: dict, label: str, options: list) -> bool:
        """已知路径：逐级展开中间级，最后一级条件分支选择。"""
        for i, opt in enumerate(options[:-1]):
            level_expanded = await self._try_step_patterns(
                steps.get("expand-level", {}), label=label, option_text=opt)
            if not level_expanded:
                LOG.warning(f"级联第 {i+1} 级 '{opt}' 展开失败")
                return False
            await self.page.wait_for_timeout(300)

        last_option = options[-1]
        branch = self.kb.get_conditional_branch("el-cascader", self.framework)

        if branch:
            condition_xpath = self.kb.expand_step(
                branch.get("condition_locator", ""), label=label, last_text=last_option)
            has_checkbox = await self._check_element_visible_raw(
                condition_xpath, timeout=branch.get("timeout", 500))
            target_step = (branch.get("if_visible") if has_checkbox
                           else branch.get("if_not_visible"))
        else:
            target_step = "select-last-text"

        return await self._try_step_patterns(
            steps.get(target_step, {}), label=label, option_text=last_option)

    async def _cascader_discover_and_select(self, steps: dict, label: str) -> bool:
        """逐级发现+选择：每级选第一个有子级的项展开，到叶子级时选择。

        策略：
        - 每级读取当前面板的菜单项
        - 如果有子级指示（箭头图标），点击第一项展开下一级
        - 如果无子级（叶子节点），直接点击选择
        - 最大深度 10 级保护
        """
        max_depth = 10

        for depth in range(max_depth):
            items = await self._read_cascader_current_items()
            if not items:
                LOG.warning(f"级联第 {depth+1} 级无菜单项")
                return False

            first_item = items[0]
            if first_item.get("hasChildren"):
                # 有子级：点击展开下一级
                expanded = await self._try_step_patterns(
                    steps.get("expand-level", {}),
                    label=label, option_text=first_item["text"])
                if not expanded:
                    LOG.warning(f"级联第 {depth+1} 级 '{first_item['text']}' 展开失败")
                    return False
                await self.page.wait_for_timeout(300)
            else:
                # 叶子节点：直接选择
                branch = self.kb.get_conditional_branch("el-cascader", self.framework)
                if branch:
                    condition_xpath = self.kb.expand_step(
                        branch.get("condition_locator", ""),
                        label=label, last_text=first_item["text"])
                    has_checkbox = await self._check_element_visible_raw(
                        condition_xpath, timeout=branch.get("timeout", 500))
                    target_step = (branch.get("if_visible") if has_checkbox
                                   else branch.get("if_not_visible"))
                else:
                    target_step = "select-last-text"

                selected = await self._try_step_patterns(
                    steps.get(target_step, {}),
                    label=label, option_text=first_item["text"])
                if selected:
                    LOG.info(f"    级联自动发现: 选择了 '{first_item['text']}' (depth={depth+1})")
                return selected

        LOG.warning("级联超过最大深度，中止")
        return False

    async def discover_options(self, kb_category: str, label: str) -> dict:
        """发现组件的可用选项（不选择，仅读取）。

        供外部调用方显式发现选项。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader"）
            label: 字段标签

        Returns:
            el-select:  {"options": ["选项1"]}
            el-cascader: {"path": ["第一级", "第二级", ...]}
            其他:        {}
        """
        if kb_category == "el-select":
            steps = self.kb.get_steps("el-select", self.framework)
            if not steps:
                return {}
            expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
            if not expanded:
                return {}
            await self.page.wait_for_timeout(500)
            text = await self._discover_first_option()
            await self.page.keyboard.press("Escape")
            return {"options": [text]} if text else {}

        elif kb_category == "el-cascader":
            steps = self.kb.get_steps("el-cascader", self.framework)
            if not steps:
                return {}
            expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
            if not expanded:
                return {}
            await self.page.wait_for_timeout(500)
            path = []
            for depth in range(10):
                items = await self._read_cascader_current_items()
                if not items:
                    break
                path.append(items[0]["text"])
                if not items[0]["hasChildren"]:
                    break
                await self._try_step_patterns(
                    steps.get("expand-level", {}),
                    label=label, option_text=items[0]["text"])
                await self.page.wait_for_timeout(300)
            await self.page.keyboard.press("Escape")
            return {"path": path}

        return {}
