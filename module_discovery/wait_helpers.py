"""
wait_helpers.py — 事件驱动等待辅助函数

替代固定 wait_for_timeout，用事件驱动等待提高稳定性和速度。
"""

import logging
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

LOG = logging.getLogger("wait_helpers")


async def wait_for_table_ready(page: Page, timeout: int = 10000) -> bool:
    """等待表格加载就绪（loading mask 消失 + 有数据行）。

    替代: wait_for_timeout(3000~5000) after navigation
    """
    try:
        # 等待 loading mask 消失
        await page.wait_for_selector(
            '.el-loading-mask', state='hidden', timeout=timeout
        )
    except PlaywrightTimeout:
        pass  # 可能没有 loading mask

    try:
        # 等待表格行出现
        await page.wait_for_selector(
            '.el-table__body-wrapper tbody tr', state='visible', timeout=timeout
        )
        return True
    except PlaywrightTimeout:
        LOG.warning("等待表格就绪超时")
        await page.wait_for_timeout(1000)  # 兜底
        return False


async def wait_for_dialog(page: Page, timeout: int = 8000) -> bool:
    """等待弹窗出现（el-dialog / el-message-box）。

    替代: wait_for_timeout(1000~2000) after button click
    """
    try:
        await page.wait_for_selector(
            '.el-dialog:visible, .el-message-box:visible, .el-dialog__wrapper[style*=""] .el-dialog',
            state='visible', timeout=timeout
        )
        await page.wait_for_timeout(300)  # 等弹窗动画完成
        return True
    except PlaywrightTimeout:
        LOG.warning("等待弹窗出现超时")
        return False


async def wait_for_dropdown(page: Page, timeout: int = 5000) -> bool:
    """等待下拉菜单展开。

    替代: wait_for_timeout(800~1000) after dropdown click
    """
    try:
        await page.wait_for_selector(
            '.el-dropdown-menu:not([style*="display: none"]), '
            '.el-select-dropdown:not([style*="display: none"])',
            state='visible', timeout=timeout
        )
        await page.wait_for_timeout(200)  # 等菜单动画
        return True
    except PlaywrightTimeout:
        LOG.warning("等待下拉菜单超时")
        return False


async def wait_for_navigation_complete(page: Page, timeout: int = 15000) -> bool:
    """等待页面导航完成（networkidle + DOM 稳定）。

    替代: wait_for_timeout(3000~5000) after page.goto
    """
    try:
        await page.wait_for_load_state('networkidle', timeout=timeout)
        await page.wait_for_timeout(500)  # 等最后一波渲染
        return True
    except PlaywrightTimeout:
        LOG.warning("等待导航完成超时")
        return False


async def wait_for_dialog_dismissed(page: Page, timeout: int = 8000) -> bool:
    """等待弹窗关闭（dialog 消失或隐藏）。

    替代: wait_for_timeout(500~1000) after close/confirm
    """
    try:
        await page.wait_for_selector(
            '.el-dialog:visible, .el-message-box:visible',
            state='hidden', timeout=timeout
        )
        return True
    except PlaywrightTimeout:
        LOG.warning("等待弹窗关闭超时")
        await page.keyboard.press('Escape')
        return False


async def wait_for_api_response(page: Page, url_pattern: str = "",
                                timeout: int = 10000) -> bool:
    """等待特定 API 响应返回。

    替代: wait_for_timeout(2000~5000) after action that triggers API
    """
    try:
        if url_pattern:
            async with page.expect_response(
                lambda resp: url_pattern in resp.url and resp.status < 500,
                timeout=timeout
            ):
                pass  # 只是等待响应，不需要 context manager 的返回值
            await page.wait_for_timeout(300)  # 等前端处理响应
            return True
        else:
            # 等待网络稳定
            await page.wait_for_load_state('networkidle', timeout=timeout)
            return True
    except PlaywrightTimeout:
        LOG.warning(f"等待 API 响应超时: {url_pattern}")
        return False
