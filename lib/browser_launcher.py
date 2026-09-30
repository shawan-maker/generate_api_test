"""统一的 Playwright 浏览器启动器。

所有调用处使用 `launch_browser()` 创建浏览器/上下文/页面，避免在多处硬编码
viewport、locale、user-agent 和 chrome args。

典型用法::

    from playwright.async_api import async_playwright
    from lib.browser_launcher import launch_browser

    async with async_playwright() as pw:
        browser, context, page = await launch_browser(pw, headless=False)
        try:
            ...  # 业务逻辑
        finally:
            await browser.close()
"""

from __future__ import annotations

from typing import Tuple

# ---------- 默认配置 ----------
DEFAULT_VIEWPORT = {"width": 1600, "height": 1000}
DEFAULT_LOCALE = "zh-CN"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)
# 通用 chrome args：忽略证书、关闭安全策略（测试环境）、禁用自动化标记
DEFAULT_CHROME_ARGS = (
    "--ignore-certificate-errors",
    "--disable-web-security",
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
)


async def launch_browser(
    pw,
    *,
    headless: bool = False,
    viewport: dict = None,
    locale: str = DEFAULT_LOCALE,
    user_agent: str = DEFAULT_USER_AGENT,
    chrome_args: tuple = DEFAULT_CHROME_ARGS,
    slow_mo: int = 0,
    ignore_https_errors: bool = True,
) -> Tuple[object, object, object]:
    """启动 Chromium 浏览器并创建标准 context + page。

    Args:
        pw: async_playwright() 上下文（必须已处于 `async with` 中）。
        headless: 是否无头模式。
        viewport: 浏览器视口尺寸，默认 1600x1000。
        locale: 浏览器 locale，默认 zh-CN。
        user_agent: 浏览器 UA。
        chrome_args: 传递给 Chromium 的参数列表。
        slow_mo: 操作间隔（毫秒），调试用。
        ignore_https_errors: 是否忽略 HTTPS 证书错误。

    Returns:
        (browser, context, page) 三元组。调用方负责在退出时 `await browser.close()`
        （或依赖外层 async_playwright 上下文自动清理）。
    """
    browser = await pw.chromium.launch(
        headless=headless,
        args=list(chrome_args),
        slow_mo=slow_mo,
    )
    context = await browser.new_context(
        viewport=viewport or DEFAULT_VIEWPORT,
        locale=locale,
        user_agent=user_agent,
        ignore_https_errors=ignore_https_errors,
    )
    page = await context.new_page()
    return browser, context, page
