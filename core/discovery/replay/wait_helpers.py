"""
wait_helpers.py — 事件驱动等待辅助函数

替代固定 wait_for_timeout，用事件驱动等待提高稳定性和速度。
"""

import logging
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
from .. import const

LOG = logging.getLogger("wait_helpers")


async def wait_for_table_ready(page: Page, timeout: int = 10000, ui_framework: str = "element-ui") -> bool:
    """等待表格加载就绪（loading mask 消失 + 有数据行）。

    替代: wait_for_timeout(3000~5000) after navigation
    """
    selectors = const.get_ui_selectors(ui_framework)

    try:
        # 等待 loading mask 消失
        loading_mask = selectors["loading"]["mask"]
        await page.wait_for_selector(
            loading_mask, state='hidden', timeout=timeout
        )
    except PlaywrightTimeout:
        pass  # 可能没有 loading mask

    try:
        # 等待表格行出现
        table_row = selectors["table"]["body_row"]
        await page.wait_for_selector(
            table_row, state='visible', timeout=timeout
        )
        return True
    except PlaywrightTimeout:
        LOG.warning("等待表格就绪超时")
        await page.wait_for_timeout(1000)  # 兜底
        return False


async def wait_for_dialog(page: Page, timeout: int = 8000, ui_framework: str = "element-ui") -> bool:
    """等待弹窗出现（dialog / message-box）。

    替代: wait_for_timeout(1000~2000) after button click
    """
    selectors = const.get_ui_selectors(ui_framework)

    try:
        dialog_sel = selectors["dialog"]["visible_dialog"]
        messagebox_sel = selectors["message_box"]["visible_selector"]
        combined = f"{dialog_sel}, {messagebox_sel}"

        await page.wait_for_selector(
            combined,
            state='visible', timeout=timeout
        )
        await page.wait_for_timeout(300)  # 等弹窗动画完成
        return True
    except PlaywrightTimeout:
        LOG.warning("等待弹窗出现超时")
        return False


async def wait_for_dropdown(page: Page, timeout: int = 5000, ui_framework: str = "element-ui") -> bool:
    """等待下拉菜单展开。

    替代: wait_for_timeout(800~1000) after dropdown click
    """
    selectors = const.get_ui_selectors(ui_framework)

    try:
        dropdown_menu = selectors["dropdown"]["menu_visible"]
        select_dropdown = selectors["select"]["dropdown_visible"]
        combined = f"{dropdown_menu}, {select_dropdown}"

        await page.wait_for_selector(
            combined,
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


async def wait_for_dialog_dismissed(page: Page, timeout: int = 8000, ui_framework: str = "element-ui") -> bool:
    """等待弹窗关闭（dialog 消失或隐藏）。

    替代: wait_for_timeout(500~1000) after close/confirm
    """
    selectors = const.get_ui_selectors(ui_framework)

    try:
        dialog_sel = selectors["dialog"]["visible_dialog"]
        messagebox_sel = selectors["message_box"]["visible_selector"]
        combined = f"{dialog_sel}, {messagebox_sel}"

        await page.wait_for_selector(
            combined,
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


async def wait_for_loading_complete(page: Page, timeout: int = 15000) -> bool:
    """等待页面加载完成（浏览器状态 + 网络空闲 + loading 元素消失）。

    参考 ecsCloud1/module_keywords.py:wait_for_loading_complete

    流程：
    1. 等待浏览器加载状态（覆盖 Tab 转圈阶段）
    2. 等待网络空闲（SPA 兜底，短超时容错）
    3. 等待 8 种 loading 元素消失（el-loading-mask, el-loading-text, ng-show loading,
       el-loading-spinner, ant-btn-loading, ant-btn-loading-icon, ant-spin-spinning）
    4. 稳定等待（1 秒）

    Args:
        page: Playwright Page 对象
        timeout: 单个等待阶段的最大超时时间（ms）

    Returns:
        bool: 是否成功完成所有等待
    """
    try:
        # 1. 等待浏览器加载状态（覆盖 Tab 转圈阶段）
        await page.wait_for_load_state('load', timeout=timeout)

        # 2. 等待网络空闲（SPA 兜底，短超时容错）
        try:
            await page.wait_for_load_state('networkidle', timeout=min(3000, timeout))
        except PlaywrightTimeout:
            pass  # 网络空闲超时不阻断

        # 3. 等待 8 种 loading 元素消失
        loading_selectors = [
            '//div[contains(@class, "el-loading-mask")]',
            '//p[@class="el-loading-text"]',
            '//div[@ng-show="loading" and not(contains(@class, "ng-hide"))]',
            '//div[@class="el-loading-spinner"]/p[@class="el-loading-text"]',
            '//button[contains(@class, "ant-btn-loading")]',
            '//button//span[contains(@class, "ant-btn-loading-icon")]',
            '//div[contains(@class, "ant-spin-spinning")]',
        ]

        for selector in loading_selectors:
            try:
                await page.wait_for_selector(
                    selector, state='hidden', timeout=timeout
                )
            except PlaywrightTimeout:
                pass  # 元素不存在或已隐藏，继续
            except Exception:
                pass  # 其他异常也忽略

        # 4. 稳定等待
        await page.wait_for_timeout(1000)

        return True
    except Exception as e:
        LOG.warning(f"等待加载完成失败: {e}")
        return False
