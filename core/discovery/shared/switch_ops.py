"""
shared/switch_ops.py — Switch 切换操作（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. JS 代码完全统一（排除行级 switch + 排除隐藏 tab-pane）

统一逻辑：
- 排除行级 switch（.closest('.el-table__row')）
- 排除隐藏 tab-pane 的 switch（display: none / offsetWidth === 0）
- 确认弹框处理（委托 dialog_helpers）
- 状态轮询验证

调用方：
    - Stage 1: crud_executor.py (_do_toggle_switch)
    - Stage 2: replay_engine.py (_step_toggle_switch)
"""

import logging
from playwright.async_api import Page
from .dialog_helpers import handle_confirm, has_visible_confirm_dialog, wait_for_confirm_box_dismissed

LOG = logging.getLogger(__name__)


async def get_switch_state(page: Page, locator: str = ".el-switch", text: str = "") -> dict:
    """获取 switch 当前状态。

    综合检测多个信号源：
    - classList.contains('is-checked')
    - .el-switch__core is-checked
    - .el-switch__action is-checked
    - Vue instance value vs activeValue
    - aria-checked

    过滤规则：
    - 排除行级 switch（.el-table__row）
    - 排除隐藏 tab-pane 中的 switch

    Args:
        page: Playwright Page 对象
        locator: switch CSS 选择器
        text: switch 文本（用于日志）

    Returns:
        dict: {
            "is_checked": bool,
            "element_found": bool,
            "filtered_count": int,
            "detail": list  # 每个 switch 的诊断信息
        }
    """
    try:
        result = await page.evaluate(f"""() => {{
            const candidates = document.querySelectorAll('{locator}');
            const all = [];
            const filtered = [];

            candidates.forEach((sw, idx) => {{
                const row = sw.closest('.el-table__row, .el-table__body tr, tr.el-table__row');
                const pane = sw.closest('.el-tab-pane');
                const paneInfo = pane ? {{
                    paneId: pane.id,
                    display: window.getComputedStyle(pane).display,
                    offsetWidth: pane.offsetWidth,
                }} : null;

                const isRow = !!row;
                const isHiddenPane = pane ? (paneInfo.display === 'none' || pane.offsetWidth === 0) : false;
                const keep = !isRow && !isHiddenPane;

                // 综合检测 isChecked 状态（多信号源）
                const selfChecked = sw.classList.contains('is-checked');
                const coreChecked = sw.querySelector('.el-switch__core')?.classList.contains('is-checked') || false;
                const actionChecked = sw.querySelector('.el-switch__action')?.classList.contains('is-checked') || false;
                const ariaChecked = sw.getAttribute('aria-checked');

                const vue = sw.__vue__;
                let vueChecked = null;
                if (vue) {{
                    const val = vue.value;
                    const activeVal = vue.activeValue !== undefined ? vue.activeValue : true;
                    vueChecked = (val === activeVal);
                }}

                const isChecked = selfChecked || coreChecked || actionChecked || vueChecked || ariaChecked === 'true';

                all.push({{
                    idx, isRow, keep,
                    paneId: paneInfo?.paneId || 'none',
                    isHiddenPane,
                    selfChecked, coreChecked, actionChecked, vueChecked, ariaChecked,
                    isChecked,
                }});

                if (keep) {{
                    filtered.push(sw);
                }}
            }});

            return {{ all, filteredCount: filtered.length }};
        }}""")

        if not result or result.get("filteredCount", 0) == 0:
            return {
                "is_checked": False,
                "element_found": False,
                "filtered_count": 0,
                "detail": result.get("all", []) if result else [],
            }

        # 取第一个 keep=True 的 switch 状态
        for item in result.get("all", []):
            if item.get("keep"):
                return {
                    "is_checked": item.get("isChecked", False),
                    "element_found": True,
                    "filtered_count": result.get("filteredCount", 0),
                    "detail": result.get("all", []),
                }

        return {
            "is_checked": False,
            "element_found": False,
            "filtered_count": result.get("filteredCount", 0),
            "detail": result.get("all", []),
        }

    except Exception as e:
        LOG.debug(f"    ⚠️ get_switch_state 异常: {e}")
        return {
            "is_checked": False,
            "element_found": False,
            "filtered_count": 0,
            "detail": [],
        }


async def toggle_switch(
    page: Page,
    locator: str = ".el-switch",
    text: str = "",
    expect_confirm: bool = False,
) -> tuple:
    """切换 el-switch 开关。

    统一实现：
    1. 诊断当前状态
    2. JS 点击（排除行级 + 排除隐藏 tab-pane）
    3. 处理可能的确认弹框
    4. 轮询等待状态变化
    5. 验证结果

    Args:
        page: Playwright Page 对象
        locator: switch CSS 选择器
        text: switch 文本（用于日志）
        expect_confirm: 是否预期弹确认框

    Returns:
        (success: bool, detail: dict)
        detail 包含:
        - before_state: str  "ON" / "OFF"
        - after_state: str   "ON" / "OFF"
        - confirm_handled: bool  是否处理了确认弹框
        - success_message: str   成功消息（如有）
        - error: str             错误信息（如有）
    """
    sw_text = text or "Switch"

    try:
        # 1. 诊断当前状态
        before_state = await get_switch_state(page, locator, text)
        is_checked_before = before_state.get("is_checked", False)
        before_state_str = "ON" if is_checked_before else "OFF"

        LOG.info(f"    [switch] 切换 '{sw_text}': {before_state_str} → {'OFF' if is_checked_before else 'ON'}")

        if not before_state.get("element_found"):
            return False, {
                "before_state": before_state_str,
                "after_state": before_state_str,
                "confirm_handled": False,
                "error": "未找到 toolbar 级 switch",
            }

        # 2. JS 点击（排除行级 + 排除隐藏 tab-pane）
        click_result = await page.evaluate(f"""() => {{
            const candidates = document.querySelectorAll('{locator}');
            const toolbarSwitches = Array.from(candidates).filter(sw => {{
                if (sw.closest('.el-table__row, .el-table__body tr, tr.el-table__row')) return false;
                const pane = sw.closest('.el-tab-pane');
                if (pane) {{
                    const style = window.getComputedStyle(pane);
                    if (style.display === 'none' || pane.offsetWidth === 0) return false;
                }}
                return true;
            }});

            if (toolbarSwitches.length === 0) {{
                return {{ clicked: false, reason: 'no_toolbar_switch', total: candidates.length }};
            }}

            const sw = toolbarSwitches[0];
            sw.click();
            return {{ clicked: true, total: candidates.length, filtered: toolbarSwitches.length }};
        }}""")

        if not click_result.get("clicked"):
            reason = click_result.get("reason", "unknown")
            LOG.warning(f"    [switch] 点击失败: {reason}")
            return False, {
                "before_state": before_state_str,
                "after_state": before_state_str,
                "confirm_handled": False,
                "error": f"无法点击 toolbar switch: {reason}",
            }

        LOG.info(f"    [switch] 点击成功 (total={click_result.get('total')}, filtered={click_result.get('filtered')})")

        # 3. 处理确认弹框
        confirm_handled = False
        await page.wait_for_timeout(500)

        if await has_visible_confirm_dialog(page):
            # 诊断弹框内容
            box_message = await page.evaluate("""() => {
                const box = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
                if (!box) return '';
                const msg = box.querySelector('.el-message-box__message');
                return msg ? msg.textContent.trim() : '';
            }""")
            LOG.info(f"    [switch] 检测到确认弹框，消息: '{box_message}'")

            # 点击确认按钮
            confirm_btn = await page.query_selector('.el-message-box__btns .el-button--primary')
            if confirm_btn:
                await confirm_btn.click()
                LOG.info(f"    [switch] 已点击确认按钮")
                confirm_handled = True

                # 等待弹框消失
                await wait_for_confirm_box_dismissed(page, max_wait=10)
            else:
                # 备选按钮
                for btn_selector in ['.el-message-box__btns button:last-child',
                                     '.el-message-box__btns button:first-child']:
                    btn = await page.query_selector(btn_selector)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await wait_for_confirm_box_dismissed(page, max_wait=10)
                        confirm_handled = True
                        LOG.info(f"    [switch] 已点击确认按钮 (fallback)")
                        break
        else:
            LOG.debug(f"    [switch] 无确认弹框")

        # 4. 轮询等待 switch 状态变化（最多 5 秒）
        status_changed = False
        after_checked = is_checked_before

        for attempt in range(10):
            current_state = await get_switch_state(page, locator, text)
            current_checked = current_state.get("is_checked", False)

            if not current_state.get("element_found"):
                LOG.debug(f"    [switch] 轮询[{attempt}]: switch 元素未找到")
                await page.wait_for_timeout(500)
                continue

            if current_checked != is_checked_before:
                status_changed = True
                after_checked = current_checked
                LOG.info(f"    [switch] ✓ 状态已变化: {before_state_str} → "
                         f"{'ON' if current_checked else 'OFF'} (等待 {(attempt + 1) * 500}ms)")
                break
            else:
                LOG.info(f"    [switch] 轮询 [{attempt+1}/10]: 状态未变 ({'ON' if current_checked else 'OFF'}), "
                         f"已等待 {(attempt + 1) * 500}ms")

            await page.wait_for_timeout(500)

        after_state_str = "ON" if after_checked else "OFF"

        # 5. 验证结果
        if not status_changed:
            # 状态未变化 — 诊断
            box_still_exists = await page.evaluate("""() => {
                const boxes = document.querySelectorAll('.el-message-box__wrapper');
                return Array.from(boxes).filter(b => b.offsetWidth > 0 && b.offsetHeight > 0).length;
            }""")
            LOG.warning(f"    [switch] ❌ 5 秒后状态仍未变化 (仍有 {box_still_exists} 个可见弹框)")

            return False, {
                "before_state": before_state_str,
                "after_state": after_state_str,
                "confirm_handled": confirm_handled,
                "error": f"switch 状态未变化: {sw_text}",
            }

        # 6. 检查成功消息（可选）
        from .message_capture import get_captured_messages
        captured_msgs = await get_captured_messages(page, clear=True)
        success_msg = None
        for msg in captured_msgs:
            msg_text = msg.get("text", "")
            if "成功" in msg_text or "success" in msg_text.lower():
                success_msg = msg_text
                break

        LOG.info(f"    [switch] 切换成功: {before_state_str} → {after_state_str}"
                 + (f" (消息: {success_msg})" if success_msg else ""))

        return True, {
            "before_state": before_state_str,
            "after_state": after_state_str,
            "confirm_handled": confirm_handled,
            "success_message": success_msg,
        }

    except Exception as e:
        LOG.error(f"    [switch] 异常: {e}")
        return False, {
            "before_state": "UNKNOWN",
            "after_state": "UNKNOWN",
            "confirm_handled": False,
            "error": str(e),
        }
