"""
form_filler.py - 表单填充与验证

职责：
- 扫描页面表单字段（输入框、选择框、文本域）
- 根据 label 智能填充表单
- 处理下拉框选择
- 表单提交与验证
"""

import logging
import time
from playwright.async_api import Page, Locator
from typing import Dict, List, Tuple

LOG = logging.getLogger("form_filler")


class FormFiller:
    """表单填充封装类，供 capture_apis 调用"""

    def __init__(self, page: Page):
        self.page = page

    async def scan_form_fields_v2(self) -> List[Dict]:
        """扫描表单字段（v2 版本，返回完整结构含 selector）"""
        fields = await scan_form_fields(self.page)
        # 保留 label、type、selector 供 fill_create_form 使用
        return [{"label": f["label"], "type": f["type"],
                 "selector": f.get("selector"), "inputType": f.get("inputType", "text")}
                for f in fields]

    async def fill_create_form(self, fields: List[Dict], username: str) -> int:
        """填充创建表单，返回成功填充的字段数。

        优先使用 scan 返回的精确 selector，回退到 el-form-item 结构定位。
        填充数据从 _build_fill_data() 动态构建（hardcoded + type-based fallback）。
        """
        fill_data = self._build_fill_data(fields, username)

        filled = 0
        for field in fields:
            label = field.get("label", "")
            field_type = field.get("type", "input")
            selector = field.get("selector")

            # radio 类型：点击第一个选项（默认选中即可）
            if field_type == "radio":
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
                if field_type == "select":
                    # select 由 select_dropdowns() 处理，此处跳过
                    continue

                if field_type == "radio":
                    # radio button：点击第一个选项（或根据 label 匹配默认选项）
                    first_text = field.get("firstOptionText", "")
                    if first_text:
                        clicked = await self.page.evaluate(f"""() => {{
                            const labels = document.querySelectorAll('.el-radio-button__inner, .el-radio__label');
                            for (const lbl of labels) {{
                                if (lbl.textContent.trim() === '{first_text}') {{
                                    lbl.click();
                                    return true;
                                }}
                            }}
                            return false;
                        }}""")
                        if clicked:
                            filled += 1
                    continue

                # 优先使用 scan 返回的精确 selector
                if selector:
                    await self.page.fill(selector, value, timeout=3000)
                    filled += 1
                    continue

                # 回退：通过 label 在 el-form-item 结构中定位 input/textarea
                if field_type == "textarea":
                    css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) textarea'
                else:
                    css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) input:not([readonly])'
                await self.page.fill(css, value, timeout=3000)
                filled += 1
            except Exception as e:
                LOG.warning(f"填充字段 {label} 失败: {e}")

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

        # 通用字段映射（保留现有逻辑）
        fill_data = {
            # 通用字段
            "名称": username, "编码": f"code_{ts}",
            "描述": f"自动测试创建于{ts}", "备注": f"自动创建于{ts}",
            "说明": f"自动测试创建于{ts}",
            # 用户管理
            "用户名": username, "姓名": username,
            "邮箱": f"at_{ts}@test.com", "手机": f"138{ts[:8]}",
            "密码": "Test@123456", "确认密码": "Test@123456",
            # 角色管理
            "角色名称": username, "角色编码": f"role_code_{ts}",
            "角色描述": f"自动测试角色创建于{ts}",
            # 英文字段
            "username": username, "name": username,
            "email": f"at_{ts}@test.com", "phone": f"138{ts[:8]}",
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
            return f"138{ts[:8]}"
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
        """
        # JavaScript 方式：去掉空格后匹配常见提交按钮文本
        try:
            clicked = await self.page.evaluate("""() => {
                const submitTexts = ['确定', '保存', '提交', '确认', '立即创建',
                    '完成', '更新', '修改', 'OK', 'Update', 'Save'];
                const buttons = Array.from(document.querySelectorAll('button'));
                // 优先找可见的按钮
                const visible = buttons.filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                for (const btn of visible) {
                    const text = btn.textContent.trim().replace(/\\s+/g, '');
                    if (submitTexts.some(t => text === t || text.includes(t))) {
                        btn.click();
                        return text;
                    }
                }
                return null;
            }""")
            if clicked:
                await self.page.wait_for_timeout(1000)
                return f"submitted:{clicked}"
        except Exception as e:
            LOG.debug(f"JavaScript 提交按钮匹配失败: {e}")

        # Playwright locator 回退
        for text in ['确定', '保存', '提交', '确认', '完成', '更新', 'OK']:
            try:
                btn = self.page.locator(f'button:has-text("{text}"):visible').first
                if await btn.count() > 0:
                    await btn.click()
                    await self.page.wait_for_timeout(1000)
                    return f"submitted:{text}"
            except Exception as e:
                LOG.debug(f"尝试提交按钮 {text} 失败: {e}")
                continue

        # 最终回退: primary 按钮
        try:
            primary_btn = self.page.locator('button.el-button--primary:visible').first
            if await primary_btn.count() > 0:
                await primary_btn.click()
                await self.page.wait_for_timeout(1000)
                return "submitted:primary"
        except Exception as e:
            LOG.debug(f"尝试 primary 按钮失败: {e}")

        return "not_found"


async def scan_form_fields(page: Page) -> List[Dict]:
    """扫描页面表单字段（输入框、选择框、文本域）。

    Returns:
        表单字段列表，每个字段包含：
        - label: 标签文本
        - selector: 选择器
        - type: 字段类型 (input/select/textarea)
        - required: 是否必填
    """
    script = """
    () => {
        const fields = [];

        // 扫描输入框（排除 radio/checkbox/readonly/hidden）
        const inputs = document.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])');
        inputs.forEach(inp => {
            // 跳过 readonly 输入框（如 tree-select 的搜索框）
            if (inp.readOnly) return;
            // 跳过不可见的输入框
            const r = inp.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            // 跳过 el-select 内部的输入框（由 select_dropdowns 处理）
            if (inp.closest('.el-select')) return;

            const label = _findLabel(inp);
            if (!label) return;

            fields.push({
                label: label,
                selector: _buildSelector(inp),
                type: 'input',
                inputType: inp.type || 'text',
                required: inp.required || inp.closest('.required, [class*="required"], .is-required') !== null
            });
        });

        // 扫描文本域
        const textareas = document.querySelectorAll('textarea');
        textareas.forEach(ta => {
            const label = _findLabel(ta);
            if (!label) return;

            fields.push({
                label: label,
                selector: _buildSelector(ta),
                type: 'textarea',
                required: ta.required || ta.closest('.required, [class*="required"], .is-required') !== null
            });
        });

        // 扫描选择框（Element UI）
        const selects = document.querySelectorAll('.el-select');
        selects.forEach(sel => {
            const label = _findLabel(sel);
            if (!label) return;

            fields.push({
                label: label,
                selector: _buildSelector(sel),
                type: 'select',
                required: sel.closest('.required, [class*="required"], .is-required') !== null
            });
        });

        // 扫描 radio button 组（el-radio-group），返回第一个选项的 label 供点击
        const radioGroups = document.querySelectorAll('.el-radio-group');
        radioGroups.forEach(group => {
            const label = _findLabel(group);
            if (!label) return;
            // 获取第一个 radio 选项的文本
            const firstRadio = group.querySelector('.el-radio-button__inner, .el-radio__label');
            if (!firstRadio) return;

            fields.push({
                label: label,
                selector: null,  // radio 不用 fill，用 click
                type: 'radio',
                inputType: 'radio',
                firstOptionText: firstRadio.textContent.trim(),
                required: group.closest('.is-required') !== null
            });
        });

        return fields;

        function _findLabel(element) {
            // 1. 查找最近的 el-form-item
            const formItem = element.closest('.el-form-item');
            if (formItem) {
                const labelEl = formItem.querySelector('.el-form-item__label');
                if (labelEl) {
                    return labelEl.textContent.trim().replace(/[：:]/g, '');
                }
            }

            // 2. 查找 label 标签
            if (element.id) {
                const label = document.querySelector(`label[for="${element.id}"]`);
                if (label) return label.textContent.trim().replace(/[：:]/g, '');
            }

            // 3. 查找父元素中的 label
            const parent = element.parentElement;
            if (parent) {
                const label = parent.querySelector('label');
                if (label) return label.textContent.trim().replace(/[：:]/g, '');
            }

            return null;
        }

        function _buildSelector(element) {
            if (element.id) return `#${element.id}`;
            if (element.name) return `[name="${element.name}"]`;

            // 使用 xpath
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


async def get_form_errors(page: Page) -> List[str]:
    """获取表单验证错误信息。

    Returns:
        错误信息列表
    """
    script = """
    () => {
        const errors = [];

        // Element UI 错误提示
        const errorEls = document.querySelectorAll('.el-form-item__error, .el-message--error');
        errorEls.forEach(el => {
            const text = el.textContent.trim();
            if (text) errors.push(text);
        });

        return errors;
    }
    """

    return await page.evaluate(script)
