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


async def detect_and_click_double_confirm(page: Page, log=None) -> dict:
    """★ 批六修复2：检测提交后二次确认浮层并点击其确认按钮（通用结构判据）。

    背景（r8 定案）：两步提交的二次确认层（EIS doubleConfirm 实证
    div[role="tooltip"] + #doubleConfirm-confirm）不在弹窗容器白名单——
    守卫 B 把「主弹窗等二次确认」误判为 dialog_still_open，重装操作系统
    3 次尝试全被误杀（主提交实际已生效）。

    结构判据（零硬编码单点形态）：
    - 候选层 = [role="tooltip"] / [role="alertdialog"] / .el-popper /
      [role="dialog"] 中可见（offsetWidth/Height > 0）的层；
    - 排除主弹窗容器内嵌层（el-dialog/message-box/drawer wrapper 内），
      避免把主弹窗自己的确认按钮当二次确认；
    - 排除下拉类 popper（el-dropdown-menu / el-select-dropdown /
      el-picker-panel / el-cascader-panel / el-table）——行级 hover 菜单
      泄漏与 select 展开态不误伤；
    - 层内可见可点击（DOM disabled + Vue prop disabled 权威口径）按钮，
      去空格小写文本 ∈ DOUBLE_CONFIRM_BUTTON_TEXTS（const 单源词表）。

    单源声明：Stage 1 form_filler 守卫 B 豁免通道与 crud_executor
    chain-confirm 提交后段共用本函数；勿在别处复制。

    Args:
        page: Playwright Page 对象
        log: 可选 logger（默认模块 LOG）

    Returns:
        dict: {"found": bool, "clicked": bool, "layer_tag": str, "button_text": str}
    """
    lg = log or LOG
    try:
        from core.discovery.const import DOUBLE_CONFIRM_BUTTON_TEXTS
        dbl = await page.evaluate("""(confirmTexts) => {
            const candidates = document.querySelectorAll(
                '[role="tooltip"], [role="alertdialog"], .el-popper, [role="dialog"]');
            for (const layer of candidates) {
                if (layer.offsetWidth <= 0 || layer.offsetHeight <= 0) continue;
                if (layer.style.display === 'none') continue;
                // 排除主弹窗容器本身与内嵌层
                if (layer.closest('.el-dialog__wrapper, .el-message-box__wrapper,'
                    + ' .el-drawer, .ant-modal-wrap')) continue;
                if (layer.classList.contains('el-dialog')
                    || layer.classList.contains('el-message-box')) continue;
                // 排除下拉类 popper（行级菜单泄漏 / select 展开 / 日期面板）
                if (layer.querySelector('.el-dropdown-menu, .el-select-dropdown,'
                    + ' .el-picker-panel, .el-cascader-panel, .el-table')) continue;
                const btns = layer.querySelectorAll('button, .el-button, [role="button"]');
                for (const b of btns) {
                    if (b.offsetWidth <= 0 || b.offsetHeight <= 0) continue;
                    if (b.disabled) continue;
                    const vue = b.__vue__;
                    if (vue && vue.disabled === true) continue;  // Vue prop 权威口径
                    const t = (b.textContent || '').replace(/\\s+/g, '').toLowerCase();
                    if (confirmTexts.indexOf(t) !== -1) {
                        b.click();
                        return {found: true, clicked: true,
                                layer_tag: layer.tagName + (layer.getAttribute('role')
                                ? '[role=' + layer.getAttribute('role') + ']' : ''),
                                button_text: (b.textContent || '').trim()};
                    }
                }
            }
            return {found: false, clicked: false};
        }""", [t.lower() for t in DOUBLE_CONFIRM_BUTTON_TEXTS])
        if dbl and dbl.get("clicked"):
            lg.info(f"    [批六修复2] 二次确认层已点击确认: "
                    f"{dbl.get('layer_tag', '')} / '{dbl.get('button_text', '')}'")
        return dbl or {"found": False, "clicked": False}
    except Exception as e:
        lg.debug(f"    [批六修复2] 二次确认层检测异常（忽略）: {e}")
        return {"found": False, "clicked": False}
