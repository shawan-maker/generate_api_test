"""
replay_engine.py — Playbook 回放引擎

从 playbook.json 回放 UI 操作步骤，支持所有步骤类型。
可独立使用，也可被 Stage 2 (capture_apis.py) 调用。

依赖：
  - button_driver.py: ButtonDriver, confirm_dialog
  - form_filler.py: apply_fill_rule, FormFiller
  - wait_helpers.py: wait_for_table_ready
  - const.py: MULTI_STEP_TYPES
  - shared/*: Stage 1/2 统一实现（marker_utils, success_verifier, switch_ops 等）
"""

import logging
import json
from typing import Optional
from .button_driver import ButtonDriver, confirm_dialog
from .form_filler import apply_fill_rule, FormFiller
from .wait_helpers import wait_for_table_ready
from .. import const
from ..shared import (
    extract_marker_from_row,
    install_message_capture,
    get_captured_messages,
    verify_operation_success,
    toggle_switch,
    select_row_checkbox,
    click_row_button,
)

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


async def _inject_notification_observer(page):
    """注入 MutationObserver 捕获并钉住瞬态消息（el-message 等 3 秒后自动消失）

    页面跳转后 JavaScript 上下文会丢失，需要重新注入。
    在 replay_from_playbook 开始和 _step_wait_for_url 页面跳转后调用。

    ★ 已委托给 shared/install_message_capture（Stage 1/2 统一实现）
    ★ Stage 2 需要钉住通知（pin=True），以便截图验证
    """
    await install_message_capture(page, pin=True)


async def replay_from_playbook(page, steps: list, button_driver: ButtonDriver,
                               marker: str = None, interceptor=None, role: str = None) -> dict:
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
        role: 操作类型（create/query/update/delete/generic），用于控制 marker 提取逻辑

    Returns:
        dict: 执行结果，包含 marker（如果是 create 操作）
    """
    result = {"success": True}

    # ---- 空步骤标记（供调用方判断） ----
    if not steps:
        result["empty_replay"] = True
        return result

    # 注入 MutationObserver 捕获并钉住瞬态消息
    await _inject_notification_observer(page)

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

                # 诊断：检查点击前的对话框状态
                pre_click_state = await page.evaluate("""() => {
                    const dialogs = document.querySelectorAll('.el-dialog__wrapper');
                    return {
                        totalDialogs: dialogs.length,
                        visibleDialogs: Array.from(dialogs).filter(d =>
                            d.style.display !== 'none' && d.offsetWidth > 0
                        ).length,
                        dialogDetails: Array.from(dialogs).map((d, i) => ({
                            index: i,
                            display: d.style.display,
                            offsetWidth: d.offsetWidth,
                            offsetHeight: d.offsetHeight,
                            className: d.className.substring(0, 100)
                        }))
                    };
                }""")
                LOG.info(f"    [click_button] 点击前状态: {pre_click_state}")

                await _step_click_button(page, step, button_driver, ctx)

                # 诊断：检查点击后的表单状态和对话框
                # 与 Stage 1 _do_create 保持一致：等待 1500ms + loading complete
                await page.wait_for_timeout(1500)
                from .wait_helpers import wait_for_loading_complete
                await wait_for_loading_complete(page, timeout=10000)
                post_click_state = await page.evaluate("""() => {
                    const result = {
                        dialogVisible: false,
                        errors: [],
                        formValues: {},
                        submitButtonState: null
                    };

                    // 检查对话框
                    const dialog = document.querySelector('.el-dialog__wrapper:not([style*="display: none"])');
                    if (dialog) {
                        result.dialogVisible = true;

                        // 检查表单错误
                        dialog.querySelectorAll('.el-form-item').forEach(item => {
                            const error = item.querySelector('.el-form-item__error');
                            if (error) {
                                const label = item.querySelector('.el-form-item__label')?.textContent?.trim() || 'unknown';
                                result.errors.push({
                                    field: label,
                                    message: error.textContent.trim()
                                });
                            }
                        });

                        // 检查表单值
                        dialog.querySelectorAll('input, textarea').forEach(input => {
                            const formItem = input.closest('.el-form-item');
                            if (formItem) {
                                const label = formItem.querySelector('.el-form-item__label')?.textContent?.trim() || '';
                                result.formValues[label] = {
                                    value: input.value || '',
                                    type: input.type
                                };
                            }
                        });

                        // 检查提交按钮状态
                        const submitBtn = dialog.querySelector('button.el-button--primary');
                        if (submitBtn) {
                            result.submitButtonState = {
                                text: submitBtn.textContent.trim(),
                                disabled: submitBtn.disabled,
                                hasLoading: submitBtn.classList.contains('is-loading')
                            };
                        }
                    }

                    return result;
                }""")
                LOG.info(f"    [点击按钮后状态] {post_click_state}")

            elif action == "wait_for_dialog":
                await _step_wait_for_dialog(page, step, ctx)

            elif action == "fill_form":
                # ★ P1: 填充前等待抽屉/对话框完全打开（上一步通常是 click_button）
                if i > 0 and steps[i-1].get("action") == "click_button":
                    LOG.info(f"    等待抽屉/对话框完全打开...")
                    await page.wait_for_timeout(800)  # 等动画
                    # 等待可见的 overlay（抽屉或对话框）稳定
                    try:
                        await page.wait_for_function("""() => {
                            // 检查 el-drawer
                            const drawer = document.querySelector('.el-drawer:not([style*="display: none"])');
                            if (drawer && drawer.offsetWidth > 0) return true;
                            // 检查 el-dialog
                            const dialog = document.querySelector('.el-dialog__wrapper:not([style*="display: none"])');
                            if (dialog && dialog.offsetWidth > 0) return true;
                            // 检查 ant-drawer / ant-modal
                            const antDrawer = document.querySelector('.ant-drawer:not(.ant-drawer-hidden)');
                            if (antDrawer && antDrawer.offsetWidth > 0) return true;
                            const antModal = document.querySelector('.ant-modal:not(.ant-modal-hidden)');
                            if (antModal && antModal.offsetWidth > 0) return true;
                            return false;
                        }""", timeout=5000)
                        LOG.info(f"    ✓ 抽屉/对话框已打开")
                    except Exception:
                        LOG.warning(f"    ⚠️ 等待抽屉/对话框超时，继续尝试填充")

                marker_value = await _step_fill_form(page, step, button_driver, role)
                if marker_value:
                    # fill_form 提取的值始终优先（动态值 > 静态值）
                    if marker and marker != marker_value:
                        LOG.info(f"    [marker] 已更新: {marker} → {marker_value}")
                    else:
                        LOG.info(f"    ✓ marker 已从表单字段提取: {marker_value}")
                    result["marker"] = marker_value
                else:
                    # 检查是否有 is_marker 字段但未成功填充
                    has_marker_field = any(f.get("is_marker") for f in step.get("fields", []))
                    if has_marker_field:
                        LOG.warning(f"    ⚠️ 存在 is_marker 字段但填充失败，marker 保持: {result.get('marker')}")

                # 诊断：检查表单填充后的实际值
                form_state = await page.evaluate("""() => {
                    const fields = {};
                    document.querySelectorAll('.el-form-item').forEach(item => {
                        const label = item.querySelector('.el-form-item__label')?.textContent?.trim() || '';
                        const input = item.querySelector('input, textarea');
                        if (input && label) {
                            fields[label] = {
                                value: input.value || '',
                                type: input.type || 'unknown',
                                required: input.required || false
                            };
                        }
                        // 检查 select 字段
                        const select = item.querySelector('.el-select .el-select__tags');
                        if (select && label) {
                            const tags = Array.from(select.querySelectorAll('.el-tag')).map(t => t.textContent.trim());
                            fields[label] = {
                                value: tags.join(', '),
                                type: 'select',
                                tagCount: tags.length
                            };
                        }
                    });
                    return fields;
                }""")
                LOG.info(f"    [表单填充后状态] {json.dumps(form_state, ensure_ascii=False, indent=2)}")

            elif action == "find_row":
                resolved = await _step_find_row(page, step, button_driver, marker)
                if resolved and not marker:
                    # first_row 模式（含 fallback）：使用提取的 marker 供后续步骤使用
                    marker = resolved
                    result["marker"] = marker

            elif action == "click_row_button":
                ctx["url_before_click"] = page.url
                await _step_click_row_button(page, step, button_driver, marker)

            elif action == "click_row_more":
                ctx["url_before_click"] = page.url
                await _step_click_row_more(page, step, button_driver, marker)

            elif action == "confirm_dialog":
                # 在确认对话框前等待网络静默并标记提交时刻
                if interceptor:
                    await interceptor.wait_for_quiesce()
                    interceptor.mark_submit()
                await _step_confirm_dialog(page, step, result)

            elif action == "close_dialog":
                await _step_close_dialog(page, step)

            elif action == "wait_for_url":
                await _step_wait_for_url(page, step, ctx)

            elif action == "navigate_back":
                await _step_navigate_back(page, step)

            elif action == "assert_success":
                passed = await _step_assert_success(page, step)
                if not passed:
                    result["assertion_failed"] = True
                    step["success"] = False  # 同步标记步骤失败，供报告正确显示
                    # 传递失败详情到 result，供 capture_apis.py 和 generate_ui_script.py 使用
                    if step.get("failure_type"):
                        result["failure_type"] = step["failure_type"]
                    if step.get("failure_reason"):
                        result["failure_reason"] = step["failure_reason"]
                # 记录成功使用的断言方式（无论成功失败都记录）
                matched_method = step.get("matched_method")
                if matched_method:
                    result.setdefault("assert_methods", {})
                    # 用 step 的 description 或 action 作为 key
                    desc = step.get("description", action)
                    result["assert_methods"][desc] = matched_method
                    if not passed and "_dom_diagnostic" in step:
                        result["assert_methods"][f"{desc}_diagnostic"] = step["_dom_diagnostic"]

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

            elif action == "click_tab":
                await _step_click_tab(page, step)

            elif action == "toggle_switch":
                await _step_toggle_switch(page, step)

            elif action == "assert_table_has_data":
                await _step_assert_table_has_data(page)

            elif action == "assert_content_visible":
                await _step_assert_content_visible(page, step)

            elif action == "close_or_go_back":
                await _step_close_or_go_back(page, ctx)

            elif action == "wait_for_dialog_or_form":
                await _step_wait_for_dialog_or_form(page, step)

            elif action == "confirm_dialog_if_present":
                await _step_confirm_dialog_if_present(page, step, result)

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

    Stage 2 直接使用 Stage 1 提供的精确 locator 和成功策略。
    降级链：指定策略 → Playwright CSS → 空格容错 → JS 去空格点击。
    """
    locator = step.get("playwright_locator")
    btn_text = step.get("text", "")
    preferred_strategy = step.get("click_strategy")  # Stage 1 记录的成功策略

    if not locator:
        raise Exception("click_button 步骤缺少 playwright_locator（Stage 1 未提供）")

    # ★ 如果 btn_text 为空，从 locator 中提取（如 button:has-text("增加授权")）
    if not btn_text:
        import re
        text_match = re.search(r'has-text\(["\']([^"\']+)["\']\)', locator)
        if text_match:
            btn_text = text_match.group(1)
            LOG.info(f"    [click_button] 从 locator 提取文本: '{btn_text}'")

    LOG.info(f"    [click_button] text='{btn_text}', locator='{locator}', strategy={preferred_strategy}")

    # ★ DEBUG: 检查元素是否存在（特别是 el-switch）
    if 'el-switch' in locator:
        element_count = await page.locator(locator).count()
        LOG.info(f"    [SWITCH-DEBUG] locator='{locator}' 匹配元素数量: {element_count}")
        if element_count == 0:
            LOG.warning(f"    [SWITCH-DEBUG] el-switch 元素不存在！")
            # 尝试查找所有 el-switch
            all_switches = await page.locator('.el-switch').count()
            LOG.info(f"    [SWITCH-DEBUG] 页面上所有 .el-switch 数量: {all_switches}")

    # 增强：为 CSS 选择器添加隐藏过滤（防止匹配到 hidden/disabled 按钮）
    from .locator_helpers import safe_css
    ui_framework = getattr(button_driver, 'framework', 'element-ui')
    enhanced_locator = safe_css(locator, ui_framework)

    # ★ 与 Stage 1 _click_button_escalating 保持一致的按钮定位逻辑
    # Stage 1 使用 button, span, a 三种元素匹配，然后通过 JS 评估选择优先级最高的
    if btn_text:
        enhanced_locator = safe_css(
            f'button:has-text("{btn_text}"), span:has-text("{btn_text}"), '
            f'a:has-text("{btn_text}")'
        )

    # 策略 1: Playwright click（真实鼠标事件，触发 Vue/React 事件处理器）
    try:
        # ★ 第一步：JavaScript 评估，确定应该点击哪个元素（与 Stage 1 一致）
        click_result = await page.evaluate("""(btnText) => {
            const els = document.querySelectorAll('button, span, a');
            const candidates = [];
            for (const el of els) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
                if ((el.textContent || '').includes(btnText)) {
                    const isDisabled = el.disabled || el.classList.contains('is-disabled');
                    candidates.push({ el, isDisabled });
                }
            }
            // 优先选择 enabled 的 BUTTON，其次 enabled 的 A，其次 enabled 的 SPAN
            const enabledButton = candidates.find(c => c.el.tagName === 'BUTTON' && !c.isDisabled);
            const enabledLink = candidates.find(c => c.el.tagName === 'A' && !c.isDisabled);
            const enabledSpan = candidates.find(c => c.el.tagName === 'SPAN' && !c.isDisabled);
            const chosen = enabledButton || enabledLink || enabledSpan
                        || candidates.find(c => c.el.tagName === 'BUTTON')
                        || candidates.find(c => c.el.tagName === 'A')
                        || candidates.find(c => c.el.tagName === 'SPAN');
            if (chosen) {
                const el = chosen.el;
                const rect = el.getBoundingClientRect();
                return {
                    clicked: true,
                    tag: el.tagName.toLowerCase(),
                    isDisabled: chosen.isDisabled,
                    text: el.textContent.trim().substring(0, 30),
                    rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                    hasVueListeners: !!(el.__vue__ && el.__vue__._events),
                    vueComponentName: el.__vue__ ? (el.__vue__.$options.name || 'anonymous') : null
                };
            }
            return { clicked: false };
        }""", btn_text)

        if click_result.get("clicked"):
            tag = click_result.get("tag")
            is_disabled = click_result.get("isDisabled")
            rect = click_result.get("rect", {})

            if is_disabled:
                LOG.warning(f"    [click_button] 按钮 '{btn_text}' 处于禁用状态")

            # 策略 1a: Playwright CSS click（与 Stage 1 一致，优先使用）
            try:
                click_locator = page.locator(
                    safe_css(f'{tag}:has-text("{btn_text}")')
                ).first
                await click_locator.click(timeout=3000)
                LOG.info(f"    [click_button] Playwright CSS click 成功 (tag={tag}, vue={click_result.get('vueComponentName')})")
                return
            except Exception as css_err:
                LOG.warning(f"    [click_button] Playwright CSS click 失败: {str(css_err)[:100]}")

            # 策略 1b: 坐标点击（Stage 1 的最后手段）
            try:
                x = rect.get("x", 0) + rect.get("width", 0) / 2
                y = rect.get("y", 0) + rect.get("height", 0) / 2
                LOG.info(f"    [click_button] 尝试坐标点击 (x={x:.1f}, y={y:.1f})")
                await page.mouse.click(x, y)
                LOG.info(f"    [click_button] 坐标点击成功")
                return
            except Exception as coord_err:
                LOG.warning(f"    [click_button] 坐标点击也失败: {coord_err}")
                raise Exception(f"所有点击策略都失败: CSS={css_err}, Coord={coord_err}")
        else:
            LOG.info(f"    [click_button] 未找到匹配的按钮")

        # 诊断：点击前检查匹配的元素
        match_info = await page.evaluate("""(locator_text) => {
            const target = locator_text.replace(/\\s+/g, '');
            const results = [];
            const elements = document.querySelectorAll('button, span, a');
            for (const el of elements) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.disabled || el.classList.contains('is-disabled')) continue;
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target) && (el.tagName === 'BUTTON' || el.closest('button'))) {
                    results.push({
                        tag: el.tagName,
                        text: el.textContent.trim(),
                        class: el.className,
                        inDialog: !!el.closest('.el-dialog:not([style*="display: none"])'),
                        rect: el.getBoundingClientRect().toJSON()
                    });
                }
            }
            return results;
        }""", btn_text or locator.split('"')[1] if '"' in locator else "")
        LOG.info(f"    [click_button] 匹配元素: {match_info}")

        await page.click(enhanced_locator, timeout=5000)
        LOG.info(f"    [click_button] Playwright 成功")

        # 诊断：点击后检查表单状态和错误消息
        await page.wait_for_timeout(500)  # 短暂等待让表单验证完成
        _submit_check_js = const.js_normalize_in(const.SUBMIT_TEXTS, 'text')
        form_state = await page.evaluate(f"""() => {{
            // 检查表单验证错误
            const errors = [];
            document.querySelectorAll('.el-form-item__error, .el-form-item.is-error').forEach(el => {{
                if (el.offsetWidth > 0 || el.offsetHeight > 0) {{
                    errors.push(el.textContent.trim());
                }}
            }});

            // 检查对话框是否还在
            const dialogs = document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])');
            const dialogVisible = Array.from(dialogs).some(d => d.offsetWidth > 0);

            // 检查是否有提交按钮被禁用（使用统一去空格归一化匹配）
            const submitButtons = document.querySelectorAll('button.el-button--primary');
            const disabledSubmit = Array.from(submitButtons).filter(b => {{
                const text = b.textContent.trim();
                return {_submit_check_js};
            }}).map(b => ({{
                disabled: b.disabled,
                loading: b.classList.contains('is-loading'),
                text: b.textContent.trim()
            }}));

            return {{
                formErrors: errors,
                dialogVisible,
                submitButtons: disabledSubmit,
                openDialogCount: dialogs.length
            }};
        }}""")
        LOG.info(f"    [click_button] 点击后表单状态: {form_state}")

        from .wait_helpers import wait_for_loading_complete
        await wait_for_loading_complete(page, timeout=10000)
        return
    except Exception as e:
        LOG.info(f"    [click_button] Playwright 失败: {str(e)[:100]}")

    # 策略 2: 空格容错（仅针对中文 has-text 的空格变体）
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
                    LOG.info(f"    [click_button] 空格容错成功: '{original_text}' → '{normalized_text}'")
                    from .wait_helpers import wait_for_loading_complete
                    await wait_for_loading_complete(page, timeout=10000)
                    return
                except Exception:
                    pass

    # 策略 3: JS 回退（仅在 Playwright 全部失败时使用，用 dispatchEvent）
    if has_text_match:
        btn_text_for_js = has_text_match.group(1)

        clicked = await page.evaluate("""(text) => {
            const target = text.replace(/\\s+/g, '');
            const elements = document.querySelectorAll('button, span, a, .el-button');
            let candidates = [];
            for (const el of elements) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.disabled || el.classList.contains('is-disabled')) continue;
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target)) {
                    candidates.push(el);
                }
            }
            // 优先选择 BUTTON 标签
            let target_el = candidates.find(el => el.tagName === 'BUTTON') || candidates[0];
            if (target_el) {
                target_el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                return {
                    clicked: true,
                    tag: target_el.tagName,
                    text: target_el.textContent.trim()
                };
            }
            return {clicked: false};
        }""", btn_text_for_js)
        if clicked.get("clicked"):
            LOG.info(f"    [click_button] JS 回退成功: {clicked.get('tag')} '{clicked.get('text')}'")
            await page.wait_for_timeout(1000)
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
        # Dialog 模式：等待对话框或表单字段出现（兼容内联表单）
        try:
            # 先尝试等待对话框
            await page.wait_for_selector(locator, state="visible", timeout=timeout)
            await page.wait_for_timeout(1000)
        except Exception:
            # 对话框未出现，检查是否有内联表单字段
            form_check = await page.evaluate("""() => {
                const formItems = document.querySelectorAll('.el-form-item');
                const visible = Array.from(formItems).filter(f => {
                    const rect = f.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                });
                return {
                    total: formItems.length,
                    visible: visible.length,
                    hasForm: visible.length > 0
                };
            }""")

            if form_check.get("hasForm"):
                # 检测到内联表单，认为等待成功
                LOG.info(f"    [wait_for_dialog] 检测到内联表单（{form_check.get('visible')} 个可见字段），继续执行")
                await page.wait_for_timeout(1000)
            else:
                # 既没有对话框也没有表单，抛出原始异常
                raise Exception(
                    f"wait_for_dialog 超时：未找到对话框（{locator}），也未检测到内联表单"
                )
    elif interaction_mode == "page-nav":
        # Page-nav 模式：等待页面加载完成
        await page.wait_for_load_state("networkidle", timeout=10000)
        await page.wait_for_timeout(1000)
    else:
        raise Exception(f"未知的 interaction_mode: {interaction_mode}")


async def _fill_ip_input(page, sub_selectors: list, option_text: str, label: str) -> bool:
    """填充 IP 地址输入框（支持 IPv4 和 IPv6）

    Args:
        page: Playwright Page 对象
        sub_selectors: IP 输入框的子选择器列表（通常是 4 个或 8 个 input）
        option_text: IP 地址值（如 "10.0.0.1", "10.0.0.1/24", "10.0.0.1-10.0.0.10"）
        label: 字段标签（用于日志）

    Returns:
        bool: 是否填充成功
    """
    import re

    if not sub_selectors or not option_text:
        return False

    # 判断输入框数量：4 个是 IPv4，8 个是 IPv6
    input_count = len(sub_selectors)

    # 解析 IP 值
    # 支持格式：
    # - "10.0.0.1" (单 IP)
    # - "10.0.0.1/24" (带掩码)
    # - "10.0.0.1-10.0.0.10" (范围)
    # - "fe80::1" (IPv6)

    parts = []

    # IPv6 检测：包含 ":"
    if ":" in option_text:
        # IPv6 处理：按 ":" 分割，填充到 8 个输入框
        # 处理 "::" 缩写（展开为完整的 8 段）
        ipv6_parts = option_text.replace("::", ":").split(":")
        ipv6_parts = [p if p else "0" for p in ipv6_parts]
        # 补齐到 8 段
        while len(ipv6_parts) < 8:
            ipv6_parts.insert(1, "0")  # 在 :: 位置插入 0
        parts = ipv6_parts[:8]
    else:
        # IPv4 处理
        if "-" in option_text:
            # 范围格式：10.0.0.1-10.0.0.10 → 只取第一部分
            option_text = option_text.split("-")[0]
        elif "/" in option_text:
            # 掩码格式：10.0.0.1/24 → 只取 IP 部分
            option_text = option_text.split("/")[0]

        # 分割 IP 段
        parts = option_text.split(".")
        # 补齐到 4 段
        while len(parts) < 4:
            parts.append("0")
        parts = parts[:4]

    # 填充每个输入框
    for i, selector in enumerate(sub_selectors[:input_count]):
        if i < len(parts):
            value = parts[i]
        else:
            value = "0"

        try:
            await page.fill(selector, value)
            LOG.debug(f"    [ip-input] {label}[{i}]: {value}")
        except Exception as e:
            LOG.warning(f"    [ip-input] {label}[{i}]: 填充失败 - {e}")
            return False

    return True


async def _step_fill_form(page, step: dict, button_driver: ButtonDriver, role: str = None) -> str | None:
    """步骤：填充表单字段（纯执行，不做推断）

    Args:
        page: Playwright Page 对象
        step: 步骤定义
        button_driver: ButtonDriver 实例
        role: 操作类型（create/query/update/delete/generic），只有 create 操作才提取 marker
    """
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
        field_type = field.get("type", "input")

        # ★ 方案 A：Stage 1 显式标记不需要 locator 的字段（如 ip-input 使用 sub_selectors）
        requires_locator = field.get("requires_locator", True)

        # 1. 优先识别 multi-step 类型
        if kb_category in const.MULTI_STEP_TYPES or kb_category == "form-checkbox":
            multi_step_fields.append(field)
            continue

        # 2. 对于非 multi-step 字段，检查 locator（除非显式标记不需要）
        if requires_locator and not locator:
            LOG.warning(f"    字段 {label} 缺少 locator，跳过")
            continue

        # 3. 如果没有 kb_category 但是普通 input 类型，当作简单字段处理
        if not kb_category and field_type == "input":
            simple_fields.append(field)
            continue

        # 4. Stage 1 必须提供 kb_category，不做推断（除了普通 input）
        if not kb_category:
            LOG.warning(f"    字段 {label} 缺少 kb_category（Stage 1 未提供），跳过")
            continue

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
        input_type = field.get("inputType", "text")

        # Radio 字段：Stage 1 已提供精确的 option_locator
        # 处理逻辑与 Stage 1 fill_create_form() 保持一致（含 Vue v-model 同步）
        if kb_category == "radio":
            try:
                enhanced = safe_css(locator, ui_framework) if locator else locator
                await page.click(enhanced, timeout=3000)
                LOG.info(f"      [Radio] 点击完成: {label}")

                # 等待 Vue 响应
                await page.wait_for_timeout(300)

                # 通过共享工具强制同步 Vue v-model
                # 兼容两种字段名：firstOptionText（原始扫描）和 option_text（playbook 生成）
                first_text = field.get("firstOptionText", "") or field.get("option_text", "")
                if first_text:
                    from .vue_radio_sync import sync_radio_vue_model
                    vue_update_result = await sync_radio_vue_model(page, first_text)

                # ★ 处理 cascade_actions（如 radio 触发 transfer-box 选择）
                cascade_actions = field.get("cascade_actions", [])
                if cascade_actions:
                    LOG.info(f"      [Radio] 检测到 {len(cascade_actions)} 个 cascade 操作")
                    for ca in cascade_actions:
                        if ca.get("type") == "transfer-box":
                            trigger_value = ca.get("trigger_value", "")
                            # 只有当选择的值匹配 trigger_value 时才执行 cascade
                            if not trigger_value or trigger_value == first_text:
                                LOG.info(f"      [Radio] 执行 cascade: transfer-box (trigger: {trigger_value})")
                                await page.wait_for_timeout(500)  # 等待动态组件渲染

                                from .form_filler import MultiStepExecutor
                                executor = MultiStepExecutor(page, None, ui_framework)
                                success, detail = await executor.execute_with_details(
                                    "transfer-box", label, "", None, ""
                                )
                                if success:
                                    LOG.info(f"      [Radio] cascade 成功: {detail.get('selected_items', [])}")
                                    # ★ 只有 create 操作才从 cascade 提取 marker
                                    if role == "create":
                                        selected_items = detail.get("selected_items", [])
                                        if selected_items and selected_items[0] and not marker_value:
                                            marker_value = selected_items[0]
                                            LOG.info(f"      [Radio] marker 已从 cascade 提取: {marker_value}")
                                        elif not marker_value:
                                            # fallback: 使用 trigger_value
                                            if trigger_value:
                                                marker_value = trigger_value
                                                LOG.info(f"      [Radio] marker 已从 cascade trigger 提取: {marker_value}")
                                else:
                                    LOG.warning(f"      [Radio] cascade 失败")

                filled_count += 1
                LOG.info(f"      [Radio] 处理完成: {label}")
            except Exception as e:
                LOG.error(f"      [Radio] 处理失败: {label}: {e}", exc_info=True)
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
            # ★ P0: 多候选选择器依次尝试（placeholder → label → original）
            # 构造候选列表：primary locator + fallback_locators
            fallback_list = field.get("fallback_locators", [])
            if isinstance(fallback_list, list):
                candidate_selectors = [locator] + [
                    fb.get("selector") if isinstance(fb, dict) else fb
                    for fb in fallback_list
                ]
            else:
                # 兼容旧 playbook：fallback_locator 是字符串
                old_fallback = field.get("fallback_locator", "")
                candidate_selectors = [locator]
                if old_fallback and old_fallback != locator:
                    candidate_selectors.append(old_fallback)

            # 去重（保持顺序）
            seen = set()
            candidates = []
            for c in candidate_selectors:
                if c and c not in seen:
                    seen.add(c)
                    candidates.append(c)

            fill_success = False
            used_selector = None

            for cand_idx, cand_sel in enumerate(candidates):
                if not cand_sel:
                    continue
                try:
                    # ★ 特殊处理：__js_index__ 使用 JS 定位（跨会话最稳定）
                    is_js_index = cand_sel.startswith("__js_index__:")
                    if is_js_index:
                        target_index = int(cand_sel.split(":")[1])
                        # 在可见的 drawer/dialog 内找第 N 个可见 input
                        actual_value = await page.evaluate("""(targetIdx) => {
                            // 找可见的 overlay（drawer 或 dialog）
                            const overlays = [
                                ...document.querySelectorAll('.el-drawer:not([style*="display: none"])'),
                                ...document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])'),
                                ...document.querySelectorAll('.ant-drawer:not(.ant-drawer-hidden)'),
                                ...document.querySelectorAll('.ant-modal:not(.ant-modal-hidden)')
                            ].filter(el => el.offsetWidth > 0 && el.offsetHeight > 0);

                            const scope = overlays[0] || document.body;
                            const allInputs = scope.querySelectorAll('input.el-input__inner');
                            let visibleIdx = 0;
                            for (const inp of allInputs) {
                                const r = inp.getBoundingClientRect();
                                if (r.width <= 0 || r.height <= 0) continue;
                                if (inp.readOnly || inp.disabled) continue;
                                if (visibleIdx === targetIdx) {
                                    // 找到目标，填充并返回值
                                    inp.focus();
                                    inp.select();
                                    return inp.value || '';
                                }
                                visibleIdx++;
                            }
                            return null;
                        }""", target_index)

                        if actual_value is not None:
                            # JS 定位成功，现在用 keyboard 填充
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Backspace")
                            await page.keyboard.type(str(value), delay=50)
                            await page.keyboard.press("Tab")
                            LOG.info(f"      填充(js_index={target_index}): {label} = {value}")

                            # 验证
                            await page.wait_for_timeout(200)
                            verify_value = await page.evaluate("""(targetIdx) => {
                                const overlays = [
                                    ...document.querySelectorAll('.el-drawer:not([style*="display: none"])'),
                                    ...document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])'),
                                    ...document.querySelectorAll('.ant-drawer:not(.ant-drawer-hidden)'),
                                    ...document.querySelectorAll('.ant-modal:not(.ant-modal-hidden)')
                                ].filter(el => el.offsetWidth > 0 && el.offsetHeight > 0);
                                const scope = overlays[0] || document.body;
                                const allInputs = scope.querySelectorAll('input.el-input__inner');
                                let visibleIdx = 0;
                                for (const inp of allInputs) {
                                    const r = inp.getBoundingClientRect();
                                    if (r.width <= 0 || r.height <= 0) continue;
                                    if (inp.readOnly || inp.disabled) continue;
                                    if (visibleIdx === targetIdx) return inp.value;
                                    visibleIdx++;
                                }
                                return null;
                            }""", target_index)

                            if verify_value and str(verify_value).strip():
                                fill_success = True
                                LOG.info(f"      ✓ 验证通过: {label} = {verify_value} (候选{cand_idx}/js_index)")
                                break
                            else:
                                LOG.warning(f"      ⚠️ 候选{cand_idx}(js_index) 验证失败")
                                continue
                        else:
                            LOG.warning(f"      ⚠️ 候选{cand_idx}(js_index={target_index}) 定位失败")
                            continue

                    # 常规 CSS 选择器
                    enhanced = safe_css(cand_sel, ui_framework)

                    # Number 类型字段：使用 click + keyboard 方式（el-input-number 组件需要）
                    if input_type == "number":
                        await page.click(enhanced, timeout=3000)
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                        await page.keyboard.type(str(value), delay=50)
                        await page.keyboard.press("Tab")
                        LOG.info(f"      填充(number)[候选{cand_idx}]: {label} = {value}")
                    else:
                        await page.fill(enhanced, str(value), timeout=3000)
                        LOG.info(f"      填充[候选{cand_idx}]: {label} = {value}")

                    # ★ 填充后验证 — 检查值是否真的写入了目标字段
                    await page.wait_for_timeout(200)  # 等待 Vue 响应式更新
                    actual_value = await page.evaluate("""(sel) => {
                        try {
                            const el = document.querySelector(sel);
                            if (el && el.value) return el.value;
                        } catch(e) {}
                        return null;
                    }""", cand_sel)

                    if actual_value and str(actual_value).strip():
                        fill_success = True
                        used_selector = cand_sel
                        LOG.info(f"      ✓ 验证通过: {label} = {actual_value} (候选{cand_idx})")
                        break
                    else:
                        LOG.warning(f"      ⚠️ 候选{cand_idx} 验证失败（值为空），尝试下一个")
                        continue
                except Exception as cand_err:
                    LOG.warning(f"      ⚠️ 候选{cand_idx} 失败: {cand_err}")
                    continue

            if fill_success:
                filled_count += 1
                # ★ 只有 create 操作才提取 marker
                if role == "create" and is_marker and not marker_value:
                    marker_value = value
            else:
                LOG.warning(f"      ❌ 字段 {label} 所有候选选择器均失败")
                if role == "create" and is_marker:
                    LOG.warning(f"      ⚠️ marker 字段 {label} 填充失败，marker 未更新")
        except Exception as e:
            LOG.warning(f"      ❌ 填充失败: {label}: {e}")

    # --- 2. 处理 multi_step 字段 (el-select/el-cascader/date-picker/form-checkbox) ---
    if multi_step_fields:
        ff = FormFiller(page)
        framework = step.get("framework", "element-ui")

        # ★ 按 sub_type 分组：特殊处理的 el-select vs 通用字段
        special_selects = []  # select-remote / select-tree
        normal_fields = []    # 其他所有 multi_step 字段
        for f in multi_step_fields:
            kb_cat = f.get("kb_category", "")
            # ★ ip-input: 特殊处理（使用 sub_selectors）
            if kb_cat == "ip-input":
                sub_selectors = f.get("sub_selectors", [])
                option_text = f.get("option_text", "")
                label = f.get("label", "")
                if sub_selectors and option_text:
                    try:
                        success = await _fill_ip_input(page, sub_selectors, option_text, label)
                        if success:
                            filled_count += 1
                            LOG.info(f"    [ip-input] {label}: 填充成功 = {option_text}")
                        else:
                            LOG.warning(f"    [ip-input] {label}: 填充失败")
                    except Exception as e:
                        LOG.warning(f"    [ip-input] {label}: 异常: {e}")
            elif (kb_cat == "el-select"
                and f.get("select_sub_type") in ("select-remote", "select-tree")):
                special_selects.append(f)
            else:
                normal_fields.append(f)

        # 2a. 通用字段：走原有 fill_multi_step_fields
        if normal_fields:
            ms_filled, _, close_dropdown_actions = await ff.fill_multi_step_fields(normal_fields, framework)
            filled_count += ms_filled
            LOG.debug(f"    multi_step 完成: {ms_filled}/{len(normal_fields)}")
            if close_dropdown_actions:
                LOG.info(f"    multi_step 检测到 {len(close_dropdown_actions)} 个下拉框关闭操作")

        # 2b. 特殊类型 el-select：逐个调用子类型专用方法
        for f in special_selects:
            selector = f.get("playwright_locator", "")
            sub_type = f.get("select_sub_type", "")
            label = f.get("label", "")
            if not selector:
                LOG.warning(f"    [sub-type] {label}: 缺少 selector，跳过")
                continue
            try:
                success, detail = await ff.execute_select_by_sub_type(selector, sub_type, label)
                if success:
                    filled_count += 1
                    LOG.info(f"    [sub-type] {label}: {sub_type} 填充成功")
                    # 更新 option_text 供后续 marker 提取使用
                    f["option_text"] = detail.get("option_text", "")
                else:
                    LOG.warning(f"    [sub-type] {label}: {sub_type} 填充失败: {detail}")
            except Exception as e:
                LOG.warning(f"    [sub-type] {label}: {sub_type} 异常: {e}")

        # ★ Phase 4b: 处理 el-select 的 cascade_actions（覆盖所有 multi_step_fields）
        for ms_field in multi_step_fields:
            if ms_field.get("kb_category") == "el-select":
                cascade_actions = ms_field.get("cascade_actions", [])
                if cascade_actions:
                    field_label = ms_field.get("label", "")
                    LOG.info(f"    [el-select] {field_label} 检测到 {len(cascade_actions)} 个 cascade 操作")
                    for ca in cascade_actions:
                        ca_type = ca.get("type", "")
                        if ca_type == "transfer-box":
                            trigger_value = ca.get("trigger_value", "")
                            selected_items = ca.get("selected_items", [])
                            LOG.info(f"    [el-select] 执行 cascade: transfer-box (trigger: {trigger_value})")
                            await page.wait_for_timeout(500)

                            from .form_filler import MultiStepExecutor
                            executor = MultiStepExecutor(page, None, ui_framework)
                            success, detail = await executor.execute_with_details(
                                "transfer-box", field_label, "", None, ""
                            )
                            if success:
                                LOG.info(f"    [el-select] cascade 成功: {detail}")
                                if role == "create" and not marker_value and selected_items:
                                    marker_value = selected_items[0]
                                    LOG.info(f"    ✓ marker 已从 el-select cascade 提取: {marker_value}")
                            else:
                                LOG.warning(f"    [el-select] cascade 失败")

        # ★ 只有 create 操作才从 multi_step 字段提取 marker
        if role == "create" and not marker_value:
            for ms_field in multi_step_fields:
                if ms_field.get("is_marker") and ms_field.get("option_text"):
                    marker_value = ms_field["option_text"]
                    LOG.info(f"    ✓ marker 已从 multi_step 字段提取: {marker_value}")
                    break

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


async def _step_wait_for_url(page, step: dict, ctx: dict):
    """步骤：等待页面 URL 发生变化（用于 page-nav 操作）

    前置步骤（click_button/click_row_button/click_row_more）已触发页面跳转，
    此步骤只需确认 URL 已变化且页面开始加载。

    Args:
        page: Playwright 页面对象
        step: 步骤定义
        ctx: 交互状态上下文，包含 url_before_click
    """
    initial_url = ctx.get("url_before_click", page.url)

    # 最多等待 15 秒
    for _ in range(150):
        if page.url != initial_url:
            # URL 已变化，等待页面基本加载
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(500)
            LOG.info(f"    页面已跳转: {page.url[:80]}")
            # 页面跳转后 JavaScript 上下文丢失，需要重新注入通知捕获
            await _inject_notification_observer(page)
            return
        await page.wait_for_timeout(100)

    raise Exception(f"等待页面跳转超时，URL 未变化: {page.url}")


async def _step_navigate_back(page, step: dict):
    """步骤：导航回指定 URL（用于 authorize 等页面跳转操作）"""
    url = step.get("url")
    if not url:
        raise Exception("navigate_back 步骤缺少 url（Stage 1 未提供）")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # 等待表格重新加载
    await wait_for_table_ready(page, timeout=15000)


async def _step_find_row(page, step: dict, button_driver: ButtonDriver, marker: str) -> Optional[str]:
    """步骤：定位数据行

    Returns:
        resolved_marker: 提取的 marker 文本（用于 first_row 模式），或 None
    """
    mode = step.get("mode", "by_marker")

    if mode == "first_row":
        # 无 marker 模式：直接取第一行
        row = await button_driver.find_first_data_row()
        if not row:
            raise Exception("表格无数据行")
        # ★ 使用统一方法从第一行提取 marker（与 Stage 1 行为一致）
        resolved = await extract_marker_from_row(button_driver.page)
        if not resolved:
            # fallback: 如果统一方法提取失败，使用原始文本
            text = await row.inner_text()
            resolved = text.strip()[:50]
            LOG.warning(f"    ⚠️ extract_marker_from_row 失败，使用 fallback: {resolved}")
        else:
            LOG.info(f"    find_row (first_row): 提取 marker = {resolved}")
        return resolved
    else:
        # 有 marker 模式：按 marker 搜索
        if not marker:
            # 运行时 fallback：marker 不可用时降级为 first_row
            LOG.warning("    ⚠️ find_row mode=by_marker 但 marker 为 None，降级为 first_row")
            row = await button_driver.find_first_data_row()
            if not row:
                raise Exception("表格无数据行（marker 不可用且无数据行）")
            # ★ 使用统一方法从第一行提取 marker（与 Stage 1 行为一致）
            resolved = await extract_marker_from_row(button_driver.page)
            if not resolved:
                text = await row.inner_text()
                resolved = text.strip()[:50]
                LOG.warning(f"    ⚠️ extract_marker_from_row 失败，使用 fallback: {resolved}")
            else:
                LOG.info(f"    find_row (fallback→first_row): 提取 marker = {resolved}")
            return resolved
        row = await button_driver.find_data_row(marker)
        if not row:
            raise Exception(f"未找到数据行: {marker}")
        return None


async def _step_click_row_button(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：在数据行内点击按钮（JS click，兼容 Element UI 固定列）

    ★ 已委托给 shared/click_row_button（Stage 1/2 统一实现）
    先通过 marker 定位数据行，再在行内查找并点击指定文本的按钮。
    """
    button_text = step.get("button_text")
    if not button_text:
        raise Exception("click_row_button 步骤缺少 button_text（Stage 1 未提供）")
    if not marker:
        raise Exception("click_row_button 步骤需要 marker 但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")

    # ★ 使用共享实现
    clicked = await click_row_button(page, row, button_text)
    if not clicked:
        raise Exception(f"未找到行内按钮: {button_text}")

    LOG.debug(f"点击行内按钮: {button_text}")


async def _step_confirm_dialog(page, step: dict, result: dict = None):
    """步骤：确认对话框（纯执行，不做探测）

    调用 button_driver.confirm_dialog 点击确认按钮。
    支持 MessageBox/Popconfirm/Generic 三种类型。
    """
    from .button_driver import confirm_dialog, close_dialog
    from .wait_helpers import wait_for_loading_complete

    LOG.debug(f"    [confirm_dialog] 点击确认按钮")

    confirmed = await confirm_dialog(page, screenshot_holder=result)

    if not confirmed:
        raise Exception("确认按钮未找到或点击失败")

    LOG.debug(f"    [confirm_dialog] 成功: confirmed='{confirmed}'")

    # 兜底截图：如果 confirm_dialog 内部没有截图，这里补一张
    if result is not None and "success_screenshot" not in result:
        try:
            import base64
            raw = await page.screenshot(type="png")
            result["success_screenshot"] = base64.b64encode(raw).decode("ascii")
            LOG.debug(f"    [confirm_dialog] 兜底截图已保存")
        except Exception as e:
            LOG.debug(f"    [confirm_dialog] 兜底截图失败: {e}")

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



    # 不再调用通用的 confirm_dialog() 回退


async def _step_assert_success(page, step: dict) -> bool:
    """步骤：验证操作成功（软断言，失败仅标记不阻断）

    ★ 已委托给 shared/verify_operation_success（Stage 1/2 统一实现）
    六层语义检测策略依次尝试：
      0.   快速检查可见通知 — 语义分类（防止短暂消息消失）
      0.5. 失败文本优先检测 — 通知组件内搜索"失败"
      1.   成功文本匹配 — 通知组件内搜索"成功"（降低优先级）
      2.   CSS 选择器 + 文本语义验证
      2.5. MutationObserver 捕获 — 语义分类（不再 fallback 到任意消息）
      F.   主动失败检测 — el-dialog / el-message--error / 表单校验错误
      兜底: 仅 PROCESSING → 标记成功；完全无信号 → 标记失败

    Returns:
        True 表示检测到成功提示，False 表示检测到失败或未检测到
    """
    operation_type = step.get("operation_type", "")
    success, detail = await verify_operation_success(page, operation_type=operation_type)

    # 更新 step 中的诊断信息
    if detail.get("matched_method"):
        step["matched_method"] = detail["matched_method"]
    if detail.get("failure_reason"):
        step["failure_reason"] = detail["failure_reason"]
    if detail.get("failure_type"):
        step["failure_type"] = detail["failure_type"]

    await _take_assert_screenshot(page, step)
    return success



async def _take_assert_screenshot(page, step: dict):
    """截图：无论成功/失败都截取，确保报告有图。

    在 assert_success 的最终判定后调用，捕获当前页面状态。
    """
    try:
        import base64
        raw = await page.screenshot(type="png")
        step["success_screenshot"] = base64.b64encode(raw).decode("ascii")
        LOG.debug(f"    [assert_screenshot] saved: {len(raw)} bytes")
    except Exception as e:
        LOG.debug(f"    [assert_screenshot] failed: {e}")


async def _diagnose_success_dom(page) -> list:
    """诊断模式：扫描 DOM 中所有可能的成功提示元素。

    Returns:
        候选元素列表 [{selector, text, className, tag}]
    """
    try:
        result = await page.evaluate("""() => {
            const candidates = [];
            const keywords = ['成功', 'success', 'completed', 'saved', '完成'];

            // 扫描所有可见元素
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_ELEMENT, null
            );
            while (walker.nextNode()) {
                const el = walker.currentNode;
                if (el.offsetParent === null) continue;  // 跳过不可见元素

                const text = (el.textContent || '').trim();
                const cls = el.className || '';
                const matched = keywords.some(kw =>
                    text.toLowerCase().includes(kw.toLowerCase())
                );
                if (!matched) continue;

                // 构建推荐选择器
                let selector = el.tagName.toLowerCase();
                if (cls && typeof cls === 'string') {
                    selector += '.' + cls.trim().split(/\\s+/).slice(0, 2).join('.');
                }
                if (el.id) selector = '#' + el.id;

                candidates.push({
                    tag: el.tagName.toLowerCase(),
                    className: (typeof cls === 'string') ? cls : '',
                    selector: selector,
                    text: text.substring(0, 100),
                    role: el.getAttribute('role') || ''
                });
            }
            // 去重（同 selector 只保留第一个）
            const seen = new Set();
            return candidates.filter(c => {
                if (seen.has(c.selector)) return false;
                seen.add(c.selector);
                return true;
            });
        }""")
        return result or []
    except Exception as e:
        LOG.debug(f"    DOM 诊断异常: {e}")
        return []


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


async def _step_click_tab(page, step: dict):
    """步骤：切换到指定 tab。

    使用 playwright_locator（优先）或 tab_name（回退）定位 tab 元素。
    """
    tab_name = step.get("tab_name", "")
    locator = step.get("playwright_locator", "")

    try:
        if locator:
            await page.click(locator, timeout=5000)
        elif tab_name:
            await page.click(
                f'[role="tab"]:has-text("{tab_name}"), '
                f'.el-tabs__item:has-text("{tab_name}"), '
                f'.ant-tabs-tab:has-text("{tab_name}")',
                timeout=5000,
            )
        else:
            LOG.warning("    click_tab: 无 tab_name 和 playwright_locator")
            return
        await page.wait_for_timeout(1500)  # 等待 tab panel 切换和数据加载
        LOG.debug(f"    ✓ 已切换到 tab: {tab_name}")
    except Exception as e:
        LOG.warning(f"    ⚠️ tab 切换失败 ({tab_name}): {e}")
        raise


async def _step_toggle_switch(page, step: dict):
    """步骤：切换 el-switch 开关。

    ★ 已委托给 shared/toggle_switch（Stage 1/2 统一实现）
    直接使用 Stage 1 记录的 locator，统一处理确认弹框和状态验证。
    """
    sw_locator = step.get("playwright_locator", ".el-switch")
    sw_text = step.get("text", "状态")
    expect_confirm = step.get("expect_confirm", False)

    # ★ 使用共享实现
    success, detail = await toggle_switch(
        page,
        locator=sw_locator,
        text=sw_text,
        expect_confirm=expect_confirm,
    )

    if not success:
        error_msg = detail.get("error", "未知错误")
        raise Exception(f"switch 切换失败 ({sw_text}): {error_msg}")

    LOG.info(f"    ✓ switch '{sw_text}' 切换完成: {detail.get('before_state')} → {detail.get('after_state')}")


async def _step_assert_table_has_data(page):
    """步骤：断言表格有数据行。"""
    has_data = await page.evaluate("""() => {
        const rows = document.querySelectorAll(
            '.el-table__body-wrapper tbody tr:not(.el-table__empty-text)'
        );
        return rows.length > 0;
    }""")
    if not has_data:
        raise AssertionError("表格无数据")
    LOG.debug("    ✓ 表格有数据")


async def _step_assert_content_visible(page, step: dict):
    """步骤：断言页面/弹窗中有可见内容（用于详情页验证）。"""
    visible = await page.evaluate("""() => {
        // 检查对话框中有可见文本内容
        const dialog = document.querySelector(
            '.el-dialog__wrapper:not([style*="display: none"]), '
            + '.ant-modal-wrap:not([style*="display: none"]), '
            + '.el-drawer:not([style*="display: none"])'
        );
        if (dialog) {
            const body = dialog.querySelector('.el-dialog__body, .ant-modal-body, .el-drawer__body');
            return body ? body.textContent.trim().length > 10 : false;
        }
        // 检查主区域有内容
        const main = document.querySelector('.app-main, .main-content, #app');
        return main ? main.textContent.trim().length > 10 : false;
    }""")
    if not visible:
        raise AssertionError("页面无可见内容")
    LOG.debug("    ✓ 页面有可见内容")
    await _take_assert_screenshot(page, step)


async def _step_close_or_go_back(page, ctx: dict):
    """步骤：关闭当前弹窗或导航回原页面。"""
    # 先尝试关闭弹窗
    has_dialog = await page.evaluate("""() => {
        const wrappers = document.querySelectorAll(
            '.el-dialog__wrapper, .ant-modal-wrap, .el-drawer'
        );
        return Array.from(wrappers).some(
            w => w.style.display !== 'none' && w.offsetWidth > 0
        );
    }""")
    if has_dialog:
        try:
            await page.evaluate("""() => {
                const btns = document.querySelectorAll(
                    '.el-dialog__headerbtn, .el-drawer__close-btn, .ant-modal-close'
                );
                for (const b of btns) { if (b.offsetWidth > 0) { b.click(); return; } }
            }""")
            await page.wait_for_timeout(500)
            LOG.debug("    ✓ 已关闭弹窗")
            return
        except Exception:
            pass
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
        LOG.debug("    ✓ 已按 ESC 关闭")
        return

    # 无弹窗：检查是否导航到了新页面，如果是则导航回
    original_url = ctx.get("original_url", "")
    if original_url and page.url != original_url:
        await page.goto(original_url, wait_until="load", timeout=30000)
        await page.wait_for_timeout(1000)
        LOG.debug("    ✓ 已导航回原页面")


async def _step_wait_for_dialog_or_form(page, step: dict):
    """步骤：等待弹窗或页面级表单就绪（软等待，不强制要求弹窗出现）。

    用于 form_page 类型的操作：点击按钮后可能弹出对话框，也可能只是让页面表单变为可编辑状态。
    两种情况都接受，不做失败判定。
    """
    timeout = step.get("timeout_ms", 5000)

    # 等待一段时间，检查是否有弹窗/抽屉出现
    has_overlay = await page.evaluate(f"""() => {{
        // 检查 el-dialog / el-drawer / ant-modal
        const overlays = document.querySelectorAll(
            '.el-dialog__wrapper:not([style*="display: none"]), '
            + '.el-drawer:not([style*="display: none"]), '
            + '.ant-modal:not(.ant-modal-hidden), '
            + '.ant-drawer:not(.ant-drawer-hidden)'
        );
        return Array.from(overlays).some(o => o.offsetWidth > 0);
    }}""")

    if has_overlay:
        LOG.debug("    ✓ 检测到弹窗/抽屉，等待稳定")
        await page.wait_for_timeout(1000)
        return

    # 无弹窗：等待页面级表单变化（如 disabled 变为 enabled）
    try:
        await page.wait_for_function("""() => {
            // 检查是否有可编辑的 input（非 disabled）
            const inputs = document.querySelectorAll('input:not([disabled]), textarea:not([disabled])');
            return inputs.length > 0;
        }""", timeout=timeout)
        LOG.debug("    ✓ 检测到可编辑表单")
    except Exception:
        # 超时也不报错 — 可能页面变化较慢或按钮本身不触发变化
        LOG.debug(f"    ⚠️ wait_for_dialog_or_form 超时（{timeout}ms），继续执行")


async def _step_confirm_dialog_if_present(page, step: dict, result: dict = None):
    """步骤：如果有确认弹窗则点击确认（软步骤，无弹窗则跳过）。

    用于 form_page 操作：有些操作会弹出确认框（如"确认保存？"），有些不会。
    不报错，无弹窗时静默跳过。
    """
    from .button_driver import confirm_dialog

    # 检查是否有可见的确认弹窗
    has_confirm_dialog = await page.evaluate("""() => {
        // MessageBox / Popconfirm / 通用对话框
        const wrappers = document.querySelectorAll(
            '.el-message-box__wrapper:not([style*="display: none"]), '
            + '.el-popconfirm:not([style*="display: none"])'
        );
        return Array.from(wrappers).some(w => w.offsetWidth > 0);
    }""")

    if not has_confirm_dialog:
        LOG.debug("    ✓ 无确认弹窗，跳过")
        return

    # 有确认弹窗，尝试点击确认
    try:
        confirmed = await confirm_dialog(page, screenshot_holder=result)
        if confirmed:
            LOG.debug(f"    ✓ 已点击确认按钮: '{confirmed}'")
            await page.wait_for_timeout(1000)
        else:
            LOG.debug("    ⚠️ 确认弹窗存在但未能点击确认按钮")
    except Exception as e:
        LOG.debug(f"    ⚠️ 确认弹窗点击失败: {e}")


async def _step_select_row_checkbox(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：勾选数据行的 checkbox（JS click，兼容 Element UI 固定列）

    ★ 已委托给 shared/select_row_checkbox（Stage 1/2 统一实现）
    幂等操作：先检查是否已勾选，已勾选则跳过。
    """
    if not marker:
        raise Exception("select_row_checkbox 步骤需要 marker")

    # ★ 使用共享实现
    result = await select_row_checkbox(page, marker)
    if not result.get("success"):
        raise Exception(f"未找到含 '{marker}' 的行或无可见 checkbox")

    if result.get("already_checked"):
        LOG.debug(f"    [select_row_checkbox] 行已勾选，跳过")
    else:
        LOG.debug(f"    [select_row_checkbox] 已勾选 marker: {marker}")


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
        await page.fill(enhanced, value, timeout=5000)
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
