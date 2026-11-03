"""Stage 1/2 统一的操作间页面衔接逻辑

在每个业务操作完成后，将页面恢复到初始列表状态：
1. 关闭所有弹窗（dialog / drawer / message-box / notification）
2. 导航回目标 URL（如果有）
3. 恢复 Tab（如果指定）
4. 等待表格就绪

此模块统一了 Stage 1 的 _ensure_on_list_page 和 Stage 2 的 _cleanup_after_operation，
确保两个阶段的行为一致。
"""
import logging
from typing import Optional

from playwright.async_api import Page

LOG = logging.getLogger(__name__)


async def _dismiss_all_overlays(page: Page, ui_framework: str = "element-ui") -> bool:
    """关闭所有可见的弹窗/遮罩层。

    策略：
    1. Escape × 2（处理嵌套弹窗）
    2. JS 关闭：点击关闭按钮 / 取消按钮
    3. JS 强制隐藏：对仍然可见的弹窗设 display=none（兜底）
    4. 验证：确认没有可见弹窗

    Returns:
        True 如果关闭了弹窗，False 如果本来就没有弹窗
    """
    try:
        # Step 1: Escape × 2（处理嵌套弹窗）
        for _ in range(2):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)

        # Step 2: JS 关闭弹窗（点击关闭按钮 / 取消按钮）
        closed_count = await page.evaluate("""() => {
            let count = 0;

            // Element UI Dialog: 点击关闭按钮
            const dialogs = document.querySelectorAll('.el-dialog__wrapper');
            dialogs.forEach(wrapper => {
                if (window.getComputedStyle(wrapper).display !== 'none') {
                    const closeBtn = wrapper.querySelector('.el-dialog__headerbtn');
                    if (closeBtn && closeBtn.offsetWidth > 0) {
                        closeBtn.click();
                        count++;
                    }
                }
            });

            // Element UI Drawer: 点击关闭按钮
            const drawers = document.querySelectorAll('.el-drawer');
            drawers.forEach(drawer => {
                if (window.getComputedStyle(drawer).display !== 'none') {
                    const closeBtn = drawer.querySelector('.el-drawer__close-btn');
                    if (closeBtn && closeBtn.offsetWidth > 0) {
                        closeBtn.click();
                        count++;
                    }
                }
            });

            // Element UI MessageBox: 点击取消按钮
            const msgBoxes = document.querySelectorAll('.el-message-box__wrapper');
            msgBoxes.forEach(box => {
                if (window.getComputedStyle(box).display !== 'none') {
                    const cancelBtn = box.querySelector('.el-message-box__btns button:first-child');
                    if (cancelBtn && cancelBtn.offsetWidth > 0) {
                        cancelBtn.click();
                        count++;
                    }
                }
            });

            return count;
        }""")

        if closed_count > 0:
            LOG.debug(f"  [transition] 关闭了 {closed_count} 个弹窗")
            await page.wait_for_timeout(500)

        # Step 3: JS 强制隐藏仍然可见的弹窗（兜底）
        force_hidden_count = await page.evaluate("""() => {
            let count = 0;
            const selectors = [
                '.el-dialog__wrapper',
                '.el-message-box__wrapper',
                '.el-drawer',
                '.el-message',
                '.el-notification'
            ];
            for (const sel of selectors) {
                const elements = document.querySelectorAll(sel);
                for (const el of elements) {
                    if (window.getComputedStyle(el).display !== 'none') {
                        el.style.display = 'none';
                        count++;
                    }
                }
            }
            return count;
        }""")

        if force_hidden_count > 0:
            LOG.info(f"  [transition] 强制隐藏了 {force_hidden_count} 个残留弹窗")

        # Step 4: 验证没有可见弹窗
        remaining = await page.evaluate("""() => {
            const selectors = [
                '.el-dialog__wrapper',
                '.el-message-box__wrapper',
                '.el-drawer'
            ];
            let count = 0;
            for (const sel of selectors) {
                const elements = document.querySelectorAll(sel);
                for (const el of elements) {
                    if (window.getComputedStyle(el).display !== 'none') {
                        count++;
                    }
                }
            }
            return count;
        }""")

        if remaining > 0:
            LOG.warning(f"  [transition] 仍有 {remaining} 个弹窗未关闭")

        return closed_count > 0 or force_hidden_count > 0

    except Exception as e:
        LOG.debug(f"  [transition] 关闭弹窗失败: {e}")
        return False


async def _navigate_to_list(page: Page, target_url: str = "", ui_framework: str = "element-ui") -> bool:
    """导航回列表页。

    优化策略：如果已在目标页，先尝试等待表格就绪；
    只有超时或不在目标页时才做全量 page.goto()。

    Args:
        page: Playwright Page
        target_url: 目标 URL（Stage 2 必须传，Stage 1 可选）
        ui_framework: UI 框架

    Returns:
        True 导航成功，False 无需导航或导航失败
    """
    try:
        if target_url:
            current_url = page.url
            # 已在目标页 → 先尝试等待表格，避免全量刷新
            if current_url.startswith(target_url):
                LOG.debug(f"  [transition] 已在目标页，尝试等待表格")
                from core.discovery.replay.wait_helpers import wait_for_table_ready
                table_ok = await wait_for_table_ready(page, timeout=5000, ui_framework=ui_framework)
                if table_ok:
                    LOG.debug(f"  [transition] 表格已就绪，跳过 page.goto")
                    return False  # 无需导航
                else:
                    LOG.info(f"  [transition] 表格未就绪（5s），执行 page.goto 刷新")
                    await page.goto(target_url, wait_until="networkidle", timeout=60000)
                    return True
            else:
                # 不在目标页 → 必须导航
                LOG.info(f"  [transition] 当前页不匹配，导航到: {target_url}")
                await page.goto(target_url, wait_until="networkidle", timeout=60000)
                return True
        else:
            # Stage 1 模式：轻量回退
            url = page.url
            if any(kw in url for kw in ("/create", "/add", "/edit", "/detail")):
                LOG.info(f"  [transition] 检测到子页面，执行 go_back()")
                from core.discovery.replay.wait_helpers import wait_for_navigation_complete
                await page.go_back()
                await wait_for_navigation_complete(page)
                return True
            else:
                LOG.debug(f"  [transition] 已在列表页，跳过回退")
                return False

    except Exception as e:
        LOG.warning(f"  [transition] 导航失败: {e}")
        return False


async def transition_to_next(
    page: Page,
    target_url: str = "",
    tab_name: str = "",
    tab_locator: str = "",
    ui_framework: str = "element-ui",
    wait_table: bool = True,
    table_timeout: int = 15000,
) -> bool:
    """操作完成后，将页面恢复到初始列表状态。

    统一 Stage 1/2 的衔接逻辑，执行顺序：
    1. 关闭所有弹窗（dialog / drawer / message-box / notification）
    2. 导航回目标 URL（如果有）
    3. 恢复 Tab（如果指定）
    4. 等待表格就绪

    Args:
        page: Playwright Page
        target_url: 目标列表页 URL（Stage 2 必须传，Stage 1 可选）
        tab_name: 目标 Tab 名称（多 Tab 页面）
        tab_locator: 目标 Tab 的 locator
        ui_framework: UI 框架（element-ui / ant-design）
        wait_table: 是否等待表格就绪
        table_timeout: 表格等待超时（ms）

    Returns:
        True 成功恢复，False 部分步骤失败但不影响后续
    """
    all_ok = True

    # Step 1: 关闭所有弹窗（必须先于导航，弹窗可能阻止 page.goto）
    await _dismiss_all_overlays(page, ui_framework)

    # Step 2: 导航回列表页（已在目标页且表格就绪则跳过）
    try:
        navigated = await _navigate_to_list(page, target_url, ui_framework)
        # 如果没导航且也没传 target_url，仍需等待表格
        if not navigated and not target_url and wait_table:
            # Stage 1 go_back 模式
            pass
    except Exception as e:
        LOG.warning(f"  [transition] 导航异常: {e}")
        all_ok = False

    # Step 3: 恢复 Tab
    if tab_name and tab_locator:
        try:
            from core.discovery.shared.tab_helpers import restore_active_tab
            await restore_active_tab(page, tab_name, tab_locator)
        except Exception as e:
            LOG.debug(f"  [transition] Tab 恢复失败: {e}")
            all_ok = False

    # Step 4: 等待表格就绪
    if wait_table:
        try:
            from core.discovery.replay.wait_helpers import wait_for_table_ready
            table_ok = await wait_for_table_ready(page, timeout=table_timeout, ui_framework=ui_framework)
            if not table_ok:
                LOG.warning(f"  [transition] 表格未就绪（超时 {table_timeout}ms）")
                all_ok = False
        except Exception as e:
            LOG.warning(f"  [transition] 等待表格失败: {e}")
            all_ok = False

    return all_ok
