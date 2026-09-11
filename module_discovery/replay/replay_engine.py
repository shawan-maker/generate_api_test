"""
replay_engine.py — Playbook 回放引擎

从 playbook.json 回放 UI 操作步骤，支持所有步骤类型。
可独立使用，也可被 Stage 2 (capture_apis.py) 调用。

依赖：
  - button_driver.py: ButtonDriver, confirm_dialog
  - form_filler.py: apply_fill_rule, FormFiller
  - wait_helpers.py: wait_for_table_ready
  - const.py: MULTI_STEP_TYPES
"""

import logging
from .button_driver import ButtonDriver, confirm_dialog
from .form_filler import apply_fill_rule, FormFiller
from .wait_helpers import wait_for_table_ready
from .. import const

LOG = logging.getLogger(__name__)


async def _is_page_crashed(page) -> bool:
    """检测页面是否已崩溃"""
    try:
        await page.evaluate("() => true")
        return False
    except Exception as e:
        if "crashed" in str(e).lower():
            return True
        # 其他异常也视为页面不可用
        return True


async def replay_from_playbook(page, steps: list, button_driver: ButtonDriver,
                               marker: str = None, interceptor=None) -> dict:
    """执行 playbook 中的步骤序列（泛化版本）

    自适应两种交互模式：
    - dialog 模式：点击按钮 → 弹出对话框 → 填表 → 提交
    - page-nav 模式：点击按钮 → 页面跳转 → 填表 → 提交 → 返回列表

    Args:
        page: Playwright Page 对象
        steps: 步骤列表（来自 playbook.operations[action].steps）
        button_driver: ButtonDriver 实例
        marker: 已创建的记录标识（用于行级操作）
        interceptor: RequestInterceptor 实例（可选，用于标记提交时间戳）

    Returns:
        dict: 执行结果，包含 marker（如果是 create 操作）
    """
    result = {"success": True}

    # 交互状态上下文
    ctx = {
        "url_before_click": page.url,
        "original_url": page.url,
        "interaction_mode": None,  # "dialog" | "page-nav"
    }

    for i, step in enumerate(steps):
        action = step.get("action")
        LOG.debug(f"    执行步骤 {i+1}/{len(steps)}: {action}")

        # 每步执行前检查页面是否崩溃
        if await _is_page_crashed(page):
            LOG.error(f"    ⚠️ 页面已崩溃，无法继续执行步骤 {i+1}")
            raise Exception("页面已崩溃")

        try:
            if action == "click_button":
                ctx["url_before_click"] = page.url
                await _step_click_button(page, step, button_driver, ctx)

            elif action == "wait_for_dialog":
                await _step_wait_for_dialog(page, step, ctx)

            elif action == "fill_form":
                marker_value = await _step_fill_form(page, step, button_driver)
                if marker_value:
                    result["marker"] = marker_value

            elif action == "find_row":
                await _step_find_row(page, step, button_driver, marker)

            elif action == "click_row_button":
                await _step_click_row_button(page, step, button_driver, marker)

            elif action == "click_row_more":
                await _step_click_row_more(page, step, button_driver, marker)

            elif action == "confirm_dialog":
                # 在确认对话框前等待网络静默并标记提交时刻
                if interceptor:
                    await interceptor.wait_for_quiesce()
                    interceptor.mark_submit()
                await _step_confirm_dialog(page, step)

            elif action == "close_dialog":
                await _step_close_dialog(page, step)

            elif action == "navigate_back":
                await _step_navigate_back(page, step)

            elif action == "assert_success":
                await _step_assert_success(page, step)

            elif action == "assert_row_disappeared":
                await _step_assert_row_disappeared(page, step, button_driver, marker)

            elif action == "select_row_checkbox":
                await _step_select_row_checkbox(page, step, button_driver, marker)

            elif action == "wait_for_table_ready":
                await _step_wait_for_table_ready(page, step)

            elif action == "fill_input":
                await _step_fill_input(page, step, marker)

            elif action == "press_key":
                await _step_press_key(page, step)

            # Legacy step types (backward compat — will be removed)
            elif action == "hover_dropdown":
                LOG.warning("    hover_dropdown 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_hover_dropdown_legacy(page, step, button_driver)

            elif action == "click_dropdown_item":
                LOG.warning("    click_dropdown_item 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_click_dropdown_item_legacy(page, step)

            elif action == "click_confirm_dialog":
                LOG.warning("    click_confirm_dialog 已废弃，请重新运行 Stage 1 生成新 playbook")
                # 在确认对话框前等待网络静默并标记提交时刻
                if interceptor:
                    await interceptor.wait_for_quiesce()
                    interceptor.mark_submit()
                await _step_click_confirm_dialog_legacy(page, step)

            else:
                LOG.warning(f"    未知步骤类型: {action}")

            # 步骤间等待
            await page.wait_for_timeout(500)

        except Exception as e:
            LOG.warning(f"    步骤 {i+1} 执行失败: {e}")
            raise

    return result


# ============================================================
# 步骤执行器
# ============================================================

async def _step_click_button(page, step: dict, button_driver: ButtonDriver, ctx: dict):
    """步骤：点击按钮（纯执行，不做探测）

    Stage 2 直接使用 Stage 1 提供的精确 locator。
    降级链：Playwright CSS → 空格容错 → JS 去空格点击（对齐 Stage 1 的 _click_button_escalating）。
    """
    locator = step.get("playwright_locator")
    btn_text = step.get("text", "")

    if not locator:
        raise Exception("click_button 步骤缺少 playwright_locator（Stage 1 未提供）")

    LOG.debug(f"    [click_button] text='{btn_text}', locator='{locator}'")

    # 增强：为 CSS 选择器添加隐藏过滤（防止匹配到 hidden/disabled 按钮）
    from .locator_helpers import safe_css
    ui_framework = getattr(button_driver, 'framework', 'element-ui')
    enhanced_locator = safe_css(locator, ui_framework)

    try:
        await page.click(enhanced_locator, timeout=5000)
        LOG.debug(f"    [click_button] 成功")
        from .wait_helpers import wait_for_loading_complete
        await wait_for_loading_complete(page, timeout=10000)
        return
    except Exception:
        pass

    # 轻量级空格容错：仅针对中文 has-text 的空格变体
    import re
    has_text_match = re.search(r"has-text\(['\"](.+?)['\"]\)", enhanced_locator)
    if has_text_match:
        original_text = has_text_match.group(1)
        if any('一' <= c <= '鿿' for c in original_text):
            normalized_text = original_text.replace(' ', '')
            if normalized_text != original_text:
                normalized_locator = enhanced_locator.replace(
                    f"has-text('{original_text}')",
                    f"has-text('{normalized_text}')"
                ).replace(
                    f'has-text("{original_text}")',
                    f'has-text("{normalized_text}")'
                )
                try:
                    await page.click(normalized_locator, timeout=3000)
                    LOG.debug(f"    [click_button] 空格容错成功: '{original_text}' → '{normalized_text}'")
                    from .wait_helpers import wait_for_loading_complete
                    await wait_for_loading_complete(page, timeout=10000)
                    return
                except Exception:
                    pass

    # JS 回退：对齐 Stage 1 _click_button_escalating 的 JS 去空格策略
    if has_text_match:
        btn_text = has_text_match.group(1)

        # 检查按钮状态（诊断信息，帮助定位为何 Playwright click 失败）
        btn_state = await page.evaluate("""(text) => {
            const target = text.replace(/\\s+/g, '');
            const results = [];
            const elements = document.querySelectorAll('button');
            for (const el of elements) {
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target)) {
                    results.push({
                        text: el.textContent.trim(),
                        disabled: el.disabled || el.classList.contains('is-disabled'),
                        visible: el.offsetWidth > 0 && el.offsetHeight > 0
                    });
                }
            }
            return results;
        }""", btn_text)
        if btn_state:
            LOG.debug(f"    [click_button] JS回退: {len(btn_state)}个匹配按钮 {btn_state}")

        clicked = await page.evaluate("""(text) => {
            const target = text.replace(/\\s+/g, '');
            const elements = document.querySelectorAll('button, span, a, .el-button');
            for (const el of elements) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.disabled || el.classList.contains('is-disabled')) continue;
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target)) {
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                }
            }
            return false;
        }""", btn_text)
        if clicked:
            LOG.debug(f"    [click_button] JS回退成功: '{btn_text}'")
            await page.wait_for_timeout(500)
            from .wait_helpers import wait_for_loading_complete
            await wait_for_loading_complete(page, timeout=10000)
            return

    raise Exception(f"click_button 失败: 所有策略均未命中 locator={locator}")


async def _step_wait_for_dialog(page, step: dict, ctx: dict):
    """步骤：等待表单环境就绪（纯执行，不做探测）"""
    locator = step.get("playwright_locator")
    interaction_mode = step.get("interaction_mode", "dialog")
    timeout = step.get("timeout_ms", 5000)

    if not locator:
        raise Exception("wait_for_dialog 步骤缺少 playwright_locator（Stage 1 未提供）")

    ctx["interaction_mode"] = interaction_mode

    if interaction_mode == "dialog":
        # Dialog 模式：等待对话框出现
        await page.wait_for_selector(locator, state="visible", timeout=timeout)
        await page.wait_for_timeout(1000)
    elif interaction_mode == "page-nav":
        # Page-nav 模式：等待页面加载完成
        await page.wait_for_load_state("networkidle", timeout=10000)
        await page.wait_for_timeout(1000)
    else:
        raise Exception(f"未知的 interaction_mode: {interaction_mode}")


async def _step_fill_form(page, step: dict, button_driver: ButtonDriver) -> str | None:
    """步骤：填充表单字段（纯执行，不做推断）"""
    fields = step.get("fields", [])
    filled_count = 0
    marker_value = None

    # 收集 multi_step 字段，统一用 FormFiller 处理
    multi_step_fields = []
    simple_fields = []

    for field in fields:
        label = field.get("label")
        kb_category = field.get("kb_category", "")
        locator = field.get("playwright_locator")

        if not locator:
            LOG.warning(f"    字段 {label} 缺少 locator，跳过")
            continue

        # Stage 1 必须提供 kb_category，不做推断
        if not kb_category:
            LOG.warning(f"    字段 {label} 缺少 kb_category（Stage 1 未提供），跳过")
            continue

        if kb_category in const.MULTI_STEP_TYPES or kb_category == "form-checkbox":
            # 多步字段，后续统一处理
            multi_step_fields.append(field)
        else:
            simple_fields.append(field)

    # --- 1. 处理普通字段 (input/textarea/radio) ---
    from .locator_helpers import safe_css
    ui_framework = step.get("framework", "element-ui")

    for field in simple_fields:
        label = field.get("label")
        locator = field.get("playwright_locator")
        fill_rule = field.get("fill_rule")
        is_marker = field.get("is_marker", False)
        kb_category = field.get("kb_category", "")

        # Radio 字段：Stage 1 已提供精确的 option_locator
        if kb_category == "radio":
            try:
                enhanced = safe_css(locator, ui_framework) if locator else locator
                await page.click(enhanced, timeout=3000)
                filled_count += 1
                LOG.debug(f"      选择 radio: {label}")
            except Exception as e:
                LOG.debug(f"      radio 失败: {label}: {e}")
            continue

        # 普通 input/textarea：生成填充值 + page.fill()
        if fill_rule:
            value = apply_fill_rule(fill_rule)
        else:
            LOG.warning(f"    字段 {label} 缺少 fill_rule，跳过")
            continue

        if value is None:
            continue

        try:
            enhanced = safe_css(locator, ui_framework) if locator else locator
            await page.fill(enhanced, str(value), timeout=3000)
            filled_count += 1
            LOG.debug(f"      填充: {label} = {value}")
            if is_marker and not marker_value:
                marker_value = value
        except Exception as e:
            LOG.debug(f"      填充失败: {label}: {e}")

    # --- 2. 处理 multi_step 字段 (el-select/el-cascader/date-picker/form-checkbox) ---
    if multi_step_fields:
        ff = FormFiller(page)
        framework = step.get("framework", "element-ui")
        ms_filled, _ = await ff.fill_multi_step_fields(multi_step_fields, framework)
        filled_count += ms_filled
        LOG.debug(f"    multi_step 完成: {ms_filled}/{len(multi_step_fields)}")

    LOG.info(f"    已填充 {filled_count}/{len(fields)} 个字段")
    return marker_value


async def _step_click_row_more(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：点击行级"更多"菜单项（KB 驱动的 row-level 操作）

    调用 button_driver.click_row_more_item 执行：
    1. find_data_row(marker) 定位行
    2. click_row_more_item(row, item_text) 展开下拉并点击
    """
    item_text = step.get("item_text")
    if not item_text:
        raise Exception("click_row_more 步骤缺少 item_text（Stage 1 未提供）")
    if not marker:
        raise Exception("click_row_more 步骤需要 marker 但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")

    click_result = await button_driver.click_row_more_item(row, item_text)
    # 兼容新返回类型（dict）和旧返回类型（bool）
    if isinstance(click_result, dict):
        if not click_result.get("clicked", False):
            raise Exception(f"未找到菜单项: {item_text}")
    elif not click_result:
        raise Exception(f"未找到菜单项: {item_text}")

    # 等待菜单项触发的 API 请求完成
    from .wait_helpers import wait_for_loading_complete
    await wait_for_loading_complete(page, timeout=10000)


async def _step_navigate_back(page, step: dict):
    """步骤：导航回指定 URL（用于 authorize 等页面跳转操作）"""
    url = step.get("url")
    if not url:
        raise Exception("navigate_back 步骤缺少 url（Stage 1 未提供）")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # 等待表格重新加载
    await wait_for_table_ready(page, timeout=15000)


async def _step_find_row(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：定位数据行"""
    if not marker:
        raise Exception("find_row 步骤需要 marker，但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")


async def _step_click_row_button(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：在数据行内点击按钮（JS click，兼容 Element UI 固定列）

    先通过 marker 定位数据行，再在行内查找并点击指定文本的按钮。
    Element UI 固定列表格中，按钮可能在固定列 wrapper 中，Playwright click
    要求元素可见，因此使用 JS click 直接触发事件。
    """
    button_text = step.get("button_text")
    if not button_text:
        raise Exception("click_row_button 步骤缺少 button_text（Stage 1 未提供）")
    if not marker:
        raise Exception("click_row_button 步骤需要 marker 但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")

    # 使用 JS click：兼容 Element UI 固定列
    clicked = await page.evaluate("""(args) => {
        const [text, marker] = args;
        const rows = document.querySelectorAll('.el-table__body tr');
        for (const row of rows) {
            if ((row.textContent || '').includes(marker)) {
                const buttons = row.querySelectorAll('button');
                for (const btn of buttons) {
                    if ((btn.textContent || '').trim().includes(text)) {
                        btn.click();
                        return true;
                    }
                }
            }
        }
        return false;
    }""", [button_text, marker])

    if not clicked:
        raise Exception(f"未找到行内按钮: {button_text}")

    LOG.debug(f"点击行内按钮: {button_text}")
    # 等待行内按钮触发的 API 请求完成
    from .wait_helpers import wait_for_loading_complete
    await wait_for_loading_complete(page, timeout=10000)


async def _step_confirm_dialog(page, step: dict):
    """步骤：确认对话框（纯执行，不做探测）

    调用 button_driver.confirm_dialog 点击确认按钮。
    支持 MessageBox/Popconfirm/Generic 三种类型。
    """
    from .button_driver import confirm_dialog, close_dialog
    from .wait_helpers import wait_for_loading_complete

    LOG.debug(f"    [confirm_dialog] 点击确认按钮")

    confirmed = await confirm_dialog(page)

    if not confirmed:
        raise Exception("确认按钮未找到或点击失败")

    LOG.debug(f"    [confirm_dialog] 成功: confirmed='{confirmed}'")

    # 等待确认操作触发的 API 请求完成（如删除、创建等）
    await wait_for_loading_complete(page, timeout=10000)

    # 验证 dialog 消失
    await page.wait_for_timeout(1000)
    dialog_remains = await page.evaluate("""() => {
        const dialogs = document.querySelectorAll(
            '.el-dialog__wrapper:not([style*="display: none"]), '
            + '.el-message-box__wrapper:not([style*="display: none"])');
        return Array.from(dialogs).some(d => d.offsetWidth > 0);
    }""")
    if dialog_remains:
        LOG.warning("    [confirm_dialog] 确认后 dialog 仍存在，尝试再次关闭")
        await close_dialog(page)


async def _step_close_dialog(page, step: dict):
    """步骤：关闭残留对话框（纯执行，不做探测）

    调用 button_driver.close_dialog 关闭当前可见的对话框/抽屉。
    通常在确认操作后，如果对话框未自动关闭时使用。
    """
    from .button_driver import close_dialog
    closed = await close_dialog(page)
    if not closed:
        LOG.debug("    无残留对话框需要关闭")


# ============================================================
# Legacy 步骤执行器（向后兼容，将在后续版本移除）
# ============================================================

async def _step_hover_dropdown_legacy(page, step: dict, button_driver: ButtonDriver):
    """步骤：展开下拉菜单（纯执行，不做回退）"""
    parent_selector = step.get("parent_selector")
    trigger_method = step.get("trigger_method", "hover")
    wait_ms = step.get("wait_ms", 600)

    if not parent_selector:
        raise Exception("hover_dropdown 步骤缺少 parent_selector（Stage 1 未提供）")

    # 找到"更多"按钮
    more_btn = await page.query_selector(parent_selector)
    if not more_btn:
        raise Exception(f"未找到 dropdown 触发器: {parent_selector}")

    # 使用 Stage 1 验证的触发方式
    if trigger_method == "hover":
        await more_btn.hover()
    else:
        await more_btn.click()

    await page.wait_for_timeout(wait_ms)

    # 不再做 DOM 展开验证或 hover/click 回退


async def _step_click_dropdown_item_legacy(page, step: dict):
    """步骤：点击下拉菜单项"""
    locator = step.get("playwright_locator")
    if not locator:
        raise Exception("click_dropdown_item 步骤缺少 locator")

    from .locator_helpers import safe_css
    from .wait_helpers import wait_for_loading_complete
    enhanced = safe_css(locator)
    await page.click(enhanced, timeout=3000)
    await wait_for_loading_complete(page, timeout=10000)


async def _step_click_confirm_dialog_legacy(page, step: dict):
    """步骤：点击确认对话框（纯执行，不做回退）"""
    confirm_locator = step.get("confirm_locator")

    if not confirm_locator:
        raise Exception("click_confirm_dialog 步骤缺少 confirm_locator（Stage 1 未提供）")

    # 直接使用 Stage 1 提供的已验证 locator（带隐藏过滤）
    from .locator_helpers import safe_css
    from .wait_helpers import wait_for_loading_complete
    enhanced = safe_css(confirm_locator)
    await page.wait_for_selector(enhanced, state="visible", timeout=3000)
    await page.click(enhanced)
    await wait_for_loading_complete(page, timeout=10000)

    # 不再调用通用的 confirm_dialog() 回退


async def _step_assert_success(page, step: dict):
    """步骤：验证操作成功（软断言，失败仅 warning 不阻断）"""
    locator = step.get("playwright_locator")

    if not locator:
        LOG.warning(f"    assert_success 步骤缺少 locator（Stage 1 未提供），跳过验证")
        return

    try:
        await page.wait_for_selector(locator, state="visible", timeout=5000)
        LOG.debug(f"    ✓ 操作成功验证通过: {locator}")
    except Exception as e:
        # 软断言：断言失败不阻断后续步骤（marker 提取等）
        # 操作可能已成功（API 已触发），仅未检测到成功消息
        LOG.warning(f"    ⚠️ assert_success 超时（不影响操作）: {locator} - {str(e)[:100]}")
        # 不抛出异常，允许后续步骤继续执行


async def _step_assert_row_disappeared(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：验证数据行已消失"""
    if not marker:
        return

    # 等待表格刷新
    await wait_for_table_ready(page, timeout=5000)

    # 检查行是否还在
    row = await button_driver.find_data_row(marker)
    if row:
        raise Exception(f"删除验证失败: 数据行仍存在: {marker}")
    LOG.debug(f"    ✓ 数据行已消失验证通过: {marker}")


async def _step_wait_for_table_ready(page, step: dict):
    """步骤：等待表格数据刷新"""
    timeout = step.get("timeout_ms", 10000)
    await wait_for_table_ready(page, timeout=timeout)
    LOG.debug("    表格数据已刷新")


async def _step_select_row_checkbox(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：勾选数据行的 checkbox（JS click，兼容 Element UI 固定列）

    Element UI 固定列表格中，主体 wrapper 的 checkbox 是隐藏的占位元素，
    只有固定列 wrapper 中的 checkbox 可见。Playwright click 要求元素可见，
    因此使用 JS click 直接触发事件（与 Stage 1 _ensure_row_selected 一致）。

    重要：先检查 checkbox 是否已勾选，已勾选则跳过（避免 toggle 取消勾选）。
    """
    if not marker:
        raise Exception("select_row_checkbox 步骤需要 marker")

    # 先检查 checkbox 状态 + 点击（幂等操作：已勾选则跳过）
    # 对齐 Stage 1 _ensure_row_selected：必须检查 offsetWidth > 0
    checked = await page.evaluate("""(text) => {
        const rows = document.querySelectorAll('.el-table__body tr');

        for (const row of rows) {
            if ((row.textContent || '').includes(text)) {
                // 检查行是否已勾选（Element UI checkbox 选中状态）
                const isChecked = row.querySelector('.el-checkbox__input.is-checked') ||
                                  row.querySelector('.el-checkbox__input input:checked');
                if (isChecked) {
                    return {checked: true, already_checked: true};
                }

                const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                if (cb && cb.offsetWidth > 0) {
                    cb.click();
                    return {checked: true, already_checked: false};
                }
            }
        }
        return {checked: false};
    }""", marker)

    if not checked or not checked.get('checked'):
        raise Exception(f"未找到含 '{marker}' 的行或无可见 checkbox")

    if checked.get('already_checked'):
        LOG.debug(f"    [select_row_checkbox] 行已勾选，跳过")
        return

    LOG.debug(f"    [select_row_checkbox] 已勾选 marker: {marker}")

    # 等待 Vue 响应式状态更新（checkbox → 按钮启用需要时间）
    await page.wait_for_timeout(500)


async def _step_fill_input(page, step: dict, marker: str):
    """步骤：填充搜索输入框

    支持 {marker_name} 占位符替换为实际的 marker 值。
    """
    locator = step.get("playwright_locator")
    value_template = step.get("value", "")

    if not locator:
        raise Exception("fill_input 步骤缺少 playwright_locator（Stage 1 未提供）")

    # 替换占位符
    value = value_template.replace("{marker_name}", marker or "")

    if not value:
        LOG.warning("    fill_input: 值为空，跳过")
        return

    try:
        from .locator_helpers import safe_css
        enhanced = safe_css(locator)
        await page.fill(enhanced, value, timeout=3000)
        LOG.info(f"    搜索框已填充: {value}")
    except Exception as e:
        LOG.warning(f"    fill_input 失败: {e}")
        raise


async def _step_press_key(page, step: dict):
    """步骤：按键操作（如回车触发搜索）"""
    key = step.get("key")
    if not key:
        raise Exception("press_key 步骤缺少 key（Stage 1 未提供）")

    await page.keyboard.press(key)
    LOG.info(f"    按键: {key}")
