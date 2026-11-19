"""
locator_helpers.py — 统一 Locator 安全包装器

所有用于定位和操作的 locator 都必须通过此模块添加隐藏过滤属性，
防止匹配到隐藏、disabled 或不可见的元素。
"""

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
