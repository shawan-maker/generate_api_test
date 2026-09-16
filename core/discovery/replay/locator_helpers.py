"""
locator_helpers.py — 统一 Locator 安全包装器

所有用于定位和操作的 locator 都必须通过此模块添加隐藏过滤属性，
防止匹配到隐藏、disabled 或不可见的元素。
"""

from playwright.async_api import Page, Locator
from .. import const


def safe_css(selector: str, ui_framework: str = "element-ui") -> str:
    """为 CSS 选择器添加隐藏过滤属性。

    Args:
        selector: 原始 CSS 选择器
        ui_framework: UI 框架名称

    Returns:
        添加了隐藏过滤的 CSS 选择器
    """
    hidden_css = const.HIDDEN_FILTERS_CSS.get(
        ui_framework,
        const.HIDDEN_FILTERS_CSS['_universal']
    )
    # 如果选择器包含多个逗号分隔的选择器，为每个添加过滤
    if ',' in selector:
        parts = [part.strip() for part in selector.split(',')]
        return ', '.join(f'{part}{hidden_css}' for part in parts)
    return f'{selector}{hidden_css}'


def safe_xpath(base_xpath: str, ui_framework: str = "element-ui") -> str:
    """为 XPath 添加隐藏过滤谓词。

    Args:
        base_xpath: 原始 XPath（不含方括号）
        ui_framework: UI 框架名称

    Returns:
        添加了隐藏过滤的 XPath
    """
    hidden_filter = const.HIDDEN_FILTERS.get(
        ui_framework,
        const.HIDDEN_FILTERS['_universal']
    )
    return f'{base_xpath}[{hidden_filter}]'


async def click_safe(page: Page, selector: str, ui_framework: str = "element-ui",
                     timeout: int = 5000, force: bool = False) -> None:
    """安全点击：自动添加隐藏过滤。

    Args:
        page: Playwright Page 对象
        selector: CSS 选择器
        ui_framework: UI 框架名称
        timeout: 超时时间（ms）
        force: 是否强制点击（绕过 actionability checks）
    """
    safe_sel = safe_css(selector, ui_framework)
    await page.click(safe_sel, timeout=timeout, force=force)


async def fill_safe(page: Page, selector: str, value: str,
                    ui_framework: str = "element-ui", timeout: int = 3000) -> None:
    """安全填充：自动添加隐藏过滤。

    Args:
        page: Playwright Page 对象
        selector: CSS 选择器
        value: 填充值
        ui_framework: UI 框架名称
        timeout: 超时时间（ms）
    """
    safe_sel = safe_css(selector, ui_framework)
    await page.fill(safe_sel, value, timeout=timeout)


async def locator_safe(page: Page, selector: str,
                       ui_framework: str = "element-ui") -> Locator:
    """安全定位：自动添加隐藏过滤。

    Args:
        page: Playwright Page 对象
        selector: CSS 选择器
        ui_framework: UI 框架名称

    Returns:
        Playwright Locator 对象
    """
    safe_sel = safe_css(selector, ui_framework)
    return page.locator(safe_sel)
