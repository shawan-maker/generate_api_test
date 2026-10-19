"""
shared/dialog_helpers.py — 弹框检测/关闭/确认（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 统一弹框检测标准，避免 Stage 1 认为无弹窗而 Stage 2 认为有弹窗

调用方：
    - Stage 1: base_executor.py (cleanup_ui_overlays, _ensure_switch_enabled)
    - Stage 2: replay_engine.py (_step_confirm_dialog, _step_confirm_dialog_if_present,
                                 _step_close_or_go_back, _step_wait_for_dialog_or_form)
"""

import logging
from playwright.async_api import Page

LOG = logging.getLogger(__name__)


async def detect_overlay(page: Page) -> dict | None:
    """检测当前可见的弹框/遮罩层，返回类型和元素信息。

    检测范围：
    - el-dialog（对话框）
    - el-drawer（抽屉）
    - el-message-box（确认弹框）
    - el-popconfirm（气泡确认框）
    - ant-modal（Ant Design 模态框）
    - ant-drawer（Ant Design 抽屉）

    Args:
        page: Playwright Page 对象

    Returns:
        dict: {"type": str, "title": str, "visible": bool}
        None: 无可见弹框
    """
    try:
        result = await page.evaluate("""() => {
            // el-dialog
            const dialogs = document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])');
            for (const d of dialogs) {
                if (d.offsetWidth > 0) {
                    const title = (d.querySelector('.el-dialog__title') || {}).textContent || '';
                    return { type: 'el-dialog', title: title.trim(), visible: true };
                }
            }

            // el-drawer
            const drawers = document.querySelectorAll('.el-drawer:not([style*="display: none"])');
            for (const d of drawers) {
                if (d.offsetWidth > 0) {
                    const title = (d.querySelector('.el-drawer__header') || {}).textContent || '';
                    return { type: 'el-drawer', title: title.trim(), visible: true };
                }
            }

            // el-message-box
            const boxes = document.querySelectorAll('.el-message-box__wrapper:not([style*="display: none"])');
            for (const b of boxes) {
                if (b.offsetWidth > 0) {
                    const title = (b.querySelector('.el-message-box__title') || {}).textContent || '';
                    return { type: 'el-message-box', title: title.trim(), visible: true };
                }
            }

            // el-popconfirm
            const popconfirms = document.querySelectorAll('.el-popconfirm:not([style*="display: none"])');
            for (const p of popconfirms) {
                if (p.offsetWidth > 0) {
                    return { type: 'el-popconfirm', title: '', visible: true };
                }
            }

            // ant-modal
            const antModals = document.querySelectorAll('.ant-modal:not(.ant-modal-hidden)');
            for (const m of antModals) {
                if (m.offsetWidth > 0) {
                    const title = (m.querySelector('.ant-modal-title') || {}).textContent || '';
                    return { type: 'ant-modal', title: title.trim(), visible: true };
                }
            }

            // ant-drawer
            const antDrawers = document.querySelectorAll('.ant-drawer:not(.ant-drawer-hidden)');
            for (const d of antDrawers) {
                if (d.offsetWidth > 0) {
                    const title = (d.querySelector('.ant-drawer-title') || {}).textContent || '';
                    return { type: 'ant-drawer', title: title.trim(), visible: true };
                }
            }

            return null;
        }""")
        return result
    except Exception as e:
        LOG.debug(f"    ⚠️ 弹框检测失败: {e}")
        return None


async def has_visible_confirm_dialog(page: Page) -> bool:
    """快速检查是否有可见的确认弹框（MessageBox / Popconfirm）。

    Args:
        page: Playwright Page 对象

    Returns:
        bool: 是否有可见的确认弹框
    """
    try:
        has_dialog = await page.evaluate("""() => {
            const wrappers = document.querySelectorAll(
                '.el-message-box__wrapper:not([style*="display: none"]), '
                + '.el-popconfirm:not([style*="display: none"])'
            );
            return Array.from(wrappers).some(w => w.offsetWidth > 0);
        }""")
        return bool(has_dialog)
    except Exception:
        return False


async def dismiss_overlay(page: Page) -> bool:
    """关闭当前可见的弹框/遮罩层。

    策略：
    1. 按 Escape
    2. 点击关闭按钮（.el-dialog__headerbtn / .el-drawer__close-btn）
    3. 点击取消按钮
    4. 移除遮罩层

    Args:
        page: Playwright Page 对象

    Returns:
        bool: 是否成功关闭了弹框
    """
    try:
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(300)

        closed = await page.evaluate("""() => {
            let closed = false;

            // 只处理有遮罩层的对话框
            const modals = document.querySelectorAll('.v-modal');
            modals.forEach(modal => {
                const wrapper = modal.nextElementSibling;
                if (wrapper && (wrapper.classList.contains('el-dialog__wrapper') ||
                                wrapper.classList.contains('el-drawer__wrapper'))) {
                    const closeBtn = wrapper.querySelector('.el-dialog__headerbtn, .el-drawer__close-btn');
                    const cancelBtn = wrapper.querySelector('.el-dialog__footer button:not(.el-button--primary)');
                    if (closeBtn) { closeBtn.click(); closed = true; }
                    else if (cancelBtn) { cancelBtn.click(); closed = true; }
                }
            });

            // 关闭 el-message-box（确认弹窗）
            document.querySelectorAll('.el-message-box__wrapper').forEach(box => {
                if (box.style.display !== 'none') {
                    const cancelBtn = box.querySelector('.el-message-box__btns button:first-child');
                    if (cancelBtn) { cancelBtn.click(); closed = true; }
                }
            });

            // 移除遮罩层
            document.querySelectorAll('.v-modal').forEach(el => el.remove());

            return closed;
        }""")

        await page.wait_for_timeout(500)
        return bool(closed)

    except Exception as e:
        LOG.debug(f"    ⚠️ 关闭弹框失败: {e}")
        return False


async def handle_confirm(page: Page, timeout: int = 3000) -> str | None:
    """处理确认弹框（MessageBox/Popconfirm/通用对话框）。

    轮询等待弹框出现，然后点击确认按钮。

    Args:
        page: Playwright Page 对象
        timeout: 等待弹框出现的超时时间（毫秒）

    Returns:
        str: 确认按钮文本（成功时）
        None: 未找到弹框或点击失败
    """
    # 委托给 button_driver.confirm_dialog（已有的统一实现）
    # 这里只作为 shared 层的入口，实际逻辑在 button_driver 中
    from core.discovery.replay.button_driver import confirm_dialog
    return await confirm_dialog(page)


async def confirm_if_present(page: Page) -> bool:
    """如果存在确认弹框则处理，不存在则静默跳过。

    Args:
        page: Playwright Page 对象

    Returns:
        bool: 是否处理了确认弹框
    """
    has_dialog = await has_visible_confirm_dialog(page)
    if not has_dialog:
        return False

    result = await handle_confirm(page)
    return result is not None


async def wait_for_confirm_box_dismissed(page: Page, max_wait: int = 10) -> bool:
    """等待确认弹框消失。

    Args:
        page: Playwright Page 对象
        max_wait: 最大等待轮数（每轮 300ms）

    Returns:
        bool: 弹框是否已消失
    """
    for _ in range(max_wait):
        box_still_visible = await page.evaluate("""() => {
            const boxes = document.querySelectorAll('.el-message-box__wrapper');
            for (const b of boxes) {
                if (b.offsetWidth > 0 && b.offsetHeight > 0
                    && !b.getAttribute('style')?.includes('display: none')) {
                    return true;
                }
            }
            return false;
        }""")
        if not box_still_visible:
            return True
        await page.wait_for_timeout(300)
    return False
