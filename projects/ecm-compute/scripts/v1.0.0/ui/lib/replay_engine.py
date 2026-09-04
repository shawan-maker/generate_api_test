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
from . import const

LOG = logging.getLogger(__name__)


async def replay_from_playbook(page, steps: list, button_driver: ButtonDriver, marker: str = None) -> dict:
    """执行 playbook 中的步骤序列（泛化版本）

    自适应两种交互模式：
    - dialog 模式：点击按钮 → 弹出对话框 → 填表 → 提交
    - page-nav 模式：点击按钮 → 页面跳转 → 填表 → 提交 → 返回列表

    Args:
        page: Playwright Page 对象
        steps: 步骤列表（来自 playbook.operations[action].steps）
        button_driver: ButtonDriver 实例
        marker: 已创建的记录标识（用于行级操作）

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

            # Legacy step types (backward compat — will be removed)
            elif action == "hover_dropdown":
                LOG.warning("    hover_dropdown 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_hover_dropdown_legacy(page, step, button_driver)

            elif action == "click_dropdown_item":
                LOG.warning("    click_dropdown_item 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_click_dropdown_item_legacy(page, step)

            elif action == "click_confirm_dialog":
                LOG.warning("    click_confirm_dialog 已废弃，请重新运行 Stage 1 生成新 playbook")
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

    Stage 2 直接使用 Stage 1 提供的精确 locator，失败即报错。
    不再做任何 JS 去空格回退或通用选择器遍历。

    轻量级空格容错：如果 locator 包含中文且 has-text 失败，尝试去空格后的版本。
    """
    locator = step.get("playwright_locator")

    if not locator:
        raise Exception("click_button 步骤缺少 playwright_locator（Stage 1 未提供）")

    try:
        # 直接使用 Stage 1 提供的已验证 locator
        await page.click(locator, timeout=5000)
    except Exception as e:
        # 轻量级空格容错：仅针对中文 has-text 的空格变体
        import re
        has_text_match = re.search(r"has-text\(['\"](.+?)['\"]\)", locator)
        if has_text_match:
            original_text = has_text_match.group(1)
            # 检查是否包含中文字符
            if any('一' <= c <= '鿿' for c in original_text):
                # 尝试去空格后的版本
                normalized_text = original_text.replace(' ', '')
                if normalized_text != original_text:
                    normalized_locator = locator.replace(
                        f"has-text('{original_text}')",
                        f"has-text('{normalized_text}')"
                    ).replace(
                        f'has-text("{original_text}")',
                        f'has-text("{normalized_text}")'
                    )
                    try:
                        await page.click(normalized_locator, timeout=3000)
                        LOG.debug(f"    空格容错成功: '{original_text}' → '{normalized_text}'")
                        return
                    except Exception:
                        pass
        # 容错失败，抛出原始异常
        raise


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
    for field in simple_fields:
        label = field.get("label")
        locator = field.get("playwright_locator")
        fill_rule = field.get("fill_rule")
        is_marker = field.get("is_marker", False)
        kb_category = field.get("kb_category", "")

        # Radio 字段：Stage 1 已提供精确的 option_locator
        if kb_category == "radio":
            try:
                await page.click(locator, timeout=3000)
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
            await page.fill(locator, str(value), timeout=3000)
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

    clicked = await button_driver.click_row_more_item(row, item_text)
    if not clicked:
        raise Exception(f"未找到菜单项: {item_text}")


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

    LOG.info(f"点击行内按钮: {button_text}")


async def _step_confirm_dialog(page, step: dict):
    """步骤：确认对话框（纯执行，不做探测）

    调用 button_driver.confirm_dialog 点击确认按钮。
    支持 MessageBox/Popconfirm/Generic 三种类型。
    """
    from .button_driver import confirm_dialog
    confirmed = await confirm_dialog(page)
    if not confirmed:
        LOG.warning("    未检测到确认对话框或确认按钮")


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

    await page.click(locator, timeout=3000)


async def _step_click_confirm_dialog_legacy(page, step: dict):
    """步骤：点击确认对话框（纯执行，不做回退）"""
    confirm_locator = step.get("confirm_locator")

    if not confirm_locator:
        raise Exception("click_confirm_dialog 步骤缺少 confirm_locator（Stage 1 未提供）")

    # 直接使用 Stage 1 提供的已验证 locator
    await page.wait_for_selector(confirm_locator, state="visible", timeout=3000)
    await page.click(confirm_locator)

    # 不再调用通用的 confirm_dialog() 回退


async def _step_assert_success(page, step: dict):
    """步骤：验证操作成功（纯执行，无默认值）"""
    locator = step.get("playwright_locator")

    if not locator:
        LOG.warning(f"    assert_success 步骤缺少 locator（Stage 1 未提供），跳过验证")
        return

    try:
        await page.wait_for_selector(locator, state="visible", timeout=5000)
    except Exception:
        LOG.warning(f"    未检测到成功消息: {locator}")


async def _step_assert_row_disappeared(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：验证数据行已消失"""
    if not marker:
        return

    # 等待表格刷新
    await wait_for_table_ready(page, timeout=5000)

    # 检查行是否还在
    row = await button_driver.find_data_row(marker)
    if row:
        LOG.warning(f"    数据行仍然存在: {marker}")


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
    """
    if not marker:
        raise Exception("select_row_checkbox 步骤需要 marker")

    # 使用 JS click：遍历所有 .el-table__body 行，找到含 marker 的行后点击 checkbox
    checked = await page.evaluate("""(text) => {
        const rows = document.querySelectorAll('.el-table__body tr');
        for (const row of rows) {
            if ((row.textContent || '').includes(text)) {
                const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                if (cb) {
                    cb.click();
                    return true;
                }
            }
        }
        return false;
    }""", marker)

    if not checked:
        raise Exception(f"未找到含 '{marker}' 的行或无 checkbox 可点击")

    await page.wait_for_timeout(300)
