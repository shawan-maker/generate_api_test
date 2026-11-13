"""
button_clicker.py — 统一按钮点击（Stage 1/2 共用）

按按钮类型分层，不按业务或阶段区分：
- click_page_button:  普通按钮（工具栏、提交、触发按钮等）
- click_more_menu:    下拉菜单项（"更多"展开后的子菜单）

降级链统一为：
1. scrollIntoView + 按钮存在性检查
2. KB XPath 模板遍历（Playwright 原生 click）
3. Playwright CSS click
4. JS btn.click()（最终降级）
"""

import logging
import re

from playwright.async_api import Page

from .. import const
from ..kb_loader import get_kb, apply_overlay_scope, detect_active_overlay_js, get_overlay_prefix

LOG = logging.getLogger(__name__)


# ============================================================
# 内部工具函数
# ============================================================

async def _try_click_frame_with_wait(page: Page, frame, xpath: str, timeout: int = 2000) -> bool:
    """在指定 frame 中尝试 XPath 点击，支持 Playwright 原生 click。"""
    try:
        el = frame.locator(f"xpath={xpath}")
        if await el.count() > 0:
            await el.click(timeout=timeout)
            from ..replay.wait_helpers import wait_for_loading_complete
            await wait_for_loading_complete(page)
            return True
    except Exception:
        pass
    return False


async def _click_by_xpath_with_wait(page: Page, xpath: str, timeout: int = 2000) -> bool:
    """通过 XPath 点击元素，支持主页面 + iframe 遍历。"""
    # 主页面
    if await _try_click_frame_with_wait(page, page, xpath, timeout):
        return True

    # iframe 遍历
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if await _try_click_frame_with_wait(page, frame, xpath, timeout):
            return True

    return False


async def _detect_active_overlay(page: Page, framework: str = "element-ui") -> str:
    """检测当前页面上活跃的覆盖层（弹窗/抽屉），返回 XPath 前缀。"""
    js = detect_active_overlay_js(framework)
    try:
        overlay_type = await page.evaluate(js)
        return get_overlay_prefix(overlay_type, framework)
    except Exception:
        return ""


# ============================================================
# 普通按钮点击（工具栏、提交、触发按钮等）
# ============================================================

async def click_page_button(
    page: Page,
    btn_text: str,
    scope_selector: str = "",
    ui_framework: str = "element-ui",
    xpath_index: int = -1,
    clean_text: str = "",
) -> dict:
    """统一普通按钮点击（Stage 1/2 共用）。

    降级链：
    1. JS 评估确认按钮存在 + scrollIntoView
    2. KB XPath 模板遍历（Playwright 原生 click，自动滚动）
       - 如果提供 xpath_index，优先使用记录的 pattern
    3. Playwright CSS click
    4. JS btn.click()（最终降级）

    Args:
        page: Playwright Page
        btn_text: 按钮文本（可能含空格，如 "确 定"）
        scope_selector: 可选，限定搜索范围的 CSS 选择器
        ui_framework: UI 框架名称

    Returns:
        {
            "clicked": bool,
            "strategy": str,  # "xpath" | "playwright" | "js" | ""
            "tag": str,
            "text": str,
            "locator": str,
            "xpath_index": int,  # KB pattern 索引（strategy == "xpath" 时有效）
            "clean_text": str,   # 去空格后的文本（KB XPath 用）
        }
    """
    result = {
        "clicked": False,
        "strategy": "",
        "tag": "",
        "text": btn_text,
        "locator": "",
        "xpath_index": -1,
        "clean_text": "",
    }

    if not btn_text:
        return result

    # ── Step 1: JS 评估确认按钮存在 + scrollIntoView ──
    # 如果有 scope_selector，限定搜索范围
    scope_js = f"Array.from(document.querySelectorAll('{scope_selector}'))" if scope_selector else "null"
    gather_js = (
        "Array.from(new Set(scopes.flatMap(s => Array.from(s.querySelectorAll('button, span, a')))))"
        if scope_selector
        else "Array.from(document.querySelectorAll('button, span, a'))"
    )

    btn_info = await page.evaluate(f"""function(btnText) {{
        const scopes = {scope_js};
        const els = {gather_js};
        const target = btnText.replace(/\\s+/g, '');
        const candidates = [];
        for (const el of els) {{
            if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
            if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
            if (el.disabled || el.classList.contains('is-disabled')) continue;
            const elText = el.textContent.trim().replace(/\\s+/g, '');
            if (elText.includes(target)) {{
                candidates.push(el);
            }}
        }}
        // 优先级: button > a > span
        candidates.sort((a, b) => {{
            const p = {{ BUTTON: 1, A: 2, SPAN: 3 }};
            return (p[a.tagName] || 99) - (p[b.tagName] || 99);
        }});
        if (candidates.length > 0) {{
            const chosen = candidates[0];
            chosen.scrollIntoView({{ block: 'center', behavior: 'instant' }});
            return {{
                exists: true,
                tag: chosen.tagName.toLowerCase(),
                originalText: chosen.textContent.trim(),
            }};
        }}
        return {{ exists: false }};
    }}""", btn_text)

    if not btn_info.get("exists"):
        LOG.debug(f"  [click_page_button] 未找到按钮: '{btn_text}'")
        return result

    LOG.debug(f"  [click_page_button] 按钮已找到: tag={btn_info.get('tag')}, "
              f"text='{btn_info.get('originalText', '')[:30]}', 已 scrollIntoView")

    await page.wait_for_timeout(200)  # 等待滚动完成

    # ── Step 2: KB XPath 模板遍历（Playwright 原生 click） ──
    kb = get_kb()
    patterns = kb.get_patterns("button", framework=ui_framework)

    # 空格过滤：如果未传入 clean_text，则计算
    if not clean_text:
        clean_text = re.sub(r'\s+', '', btn_text)

    # 检测活跃覆盖层
    overlay_prefix = await _detect_active_overlay(page, ui_framework)

    # 如果传入了 xpath_index（Stage 2 回放），优先尝试记录的 pattern
    if xpath_index >= 0 and xpath_index < len(patterns):
        LOG.info(f"  [click_page_button] 优先使用记录的 XPath pattern #{xpath_index}")
        pattern = patterns[xpath_index]
        xpath = kb.expand_step(
            pattern, label=clean_text,
            framework=ui_framework,
            apply_hidden=True,
        )

        # overlay scope 优先
        if overlay_prefix:
            scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
            if await _click_by_xpath_with_wait(page, scoped_xpath, timeout=2000):
                LOG.info(f"  [click_page_button] KB XPath (overlay, recorded) 成功: '{btn_text}' (pattern #{xpath_index})")
                verified_locator = f"{btn_info.get('tag', 'button')}:has-text(\"{btn_info.get('originalText', btn_text)}\")"
                result.update({
                    "clicked": True, "strategy": "xpath",
                    "tag": btn_info.get("tag", "button"),
                    "text": btn_info.get("originalText", btn_text),
                    "locator": verified_locator,
                    "xpath_index": xpath_index,
                    "clean_text": clean_text,
                })
                return result

        # 全局
        if await _click_by_xpath_with_wait(page, xpath, timeout=2000):
            LOG.info(f"  [click_page_button] KB XPath (recorded) 成功: '{btn_text}' (pattern #{xpath_index})")
            verified_locator = f"{btn_info.get('tag', 'button')}:has-text(\"{btn_info.get('originalText', btn_text)}\")"
            result.update({
                "clicked": True, "strategy": "xpath",
                "tag": btn_info.get("tag", "button"),
                "text": btn_info.get("originalText", btn_text),
                "locator": verified_locator,
                "xpath_index": xpath_index,
                "clean_text": clean_text,
            })
            return result

        LOG.warning(f"  [click_page_button] 记录的 XPath pattern #{xpath_index} 失败，降级到全量遍历")

    # 全量遍历
    for i, pattern in enumerate(patterns):
        xpath = kb.expand_step(
            pattern, label=clean_text,
            framework=ui_framework,
            apply_hidden=True,
        )

        # 动态 timeout：第一个 pattern 2000ms，后续 500ms
        timeout = 2000 if i == 0 else 500

        # overlay scope 优先
        if overlay_prefix:
            scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
            if await _click_by_xpath_with_wait(page, scoped_xpath, timeout=timeout):
                LOG.info(f"  [click_page_button] KB XPath (overlay) 成功: '{btn_text}' (pattern #{i})")
                verified_locator = f"{btn_info.get('tag', 'button')}:has-text(\"{btn_info.get('originalText', btn_text)}\")"
                result.update({
                    "clicked": True, "strategy": "xpath",
                    "tag": btn_info.get("tag", "button"),
                    "text": btn_info.get("originalText", btn_text),
                    "locator": verified_locator,
                    "xpath_index": i,
                    "clean_text": clean_text,
                })
                return result

        # 全局
        if await _click_by_xpath_with_wait(page, xpath, timeout=timeout):
            LOG.info(f"  [click_page_button] KB XPath 成功: '{btn_text}' (pattern #{i})")
            verified_locator = f"{btn_info.get('tag', 'button')}:has-text(\"{btn_info.get('originalText', btn_text)}\")"
            result.update({
                "clicked": True, "strategy": "xpath",
                "tag": btn_info.get("tag", "button"),
                "text": btn_info.get("originalText", btn_text),
                "locator": verified_locator,
                "xpath_index": i,
                "clean_text": clean_text,
            })
            return result

    # ── Step 3: Playwright CSS click（scrollIntoView 后应成功） ──
    try:
        from ..replay.locator_helpers import safe_css
        tag = btn_info.get("tag", "button")
        click_locator = page.locator(
            safe_css(f'{tag}:has-text("{btn_text}")', ui_framework)
        ).first
        await click_locator.click(timeout=2000)
        LOG.info(f"  [click_page_button] Playwright CSS click 成功: '{btn_text}'")
        verified_locator = f'{tag}:has-text("{btn_info.get("originalText", btn_text)}")'
        result.update({
            "clicked": True, "strategy": "playwright",
            "tag": tag,
            "text": btn_info.get("originalText", btn_text),
            "locator": verified_locator,
        })
        return result
    except Exception as e:
        LOG.debug(f"  [click_page_button] Playwright CSS click 失败: {str(e)[:100]}")

    # ── Step 4: JS btn.click()（最终降级） ──
    clicked = await page.evaluate("""(btnText) => {
        const target = btnText.replace(/\\s+/g, '');
        const els = document.querySelectorAll('button, span, a');
        const candidates = [];
        for (const el of els) {
            if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
            if (el.disabled || el.classList.contains('is-disabled')) continue;
            if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
            const elText = el.textContent.trim().replace(/\\s+/g, '');
            if (elText.includes(target)) {
                candidates.push(el);
            }
        }
        // 优先 BUTTON
        candidates.sort((a, b) => {
            const p = { BUTTON: 1, A: 2, SPAN: 3 };
            return (p[a.tagName] || 99) - (p[b.tagName] || 99);
        });
        if (candidates.length > 0) {
            const el = candidates[0];
            el.click();
            return { clicked: true, tag: el.tagName.toLowerCase(), text: el.textContent.trim() };
        }
        return { clicked: false };
    }""", btn_text)

    if clicked.get("clicked"):
        LOG.info(f"  [click_page_button] JS btn.click() 降级成功: '{btn_text}'")
        from ..replay.wait_helpers import wait_for_loading_complete
        await wait_for_loading_complete(page)
        tag = clicked.get("tag", "button")
        actual_text = clicked.get("text", btn_text)
        result.update({
            "clicked": True, "strategy": "js",
            "tag": tag,
            "text": actual_text,
            "locator": f'{tag}:has-text("{actual_text}")',
        })
        return result

    LOG.warning(f"  [click_page_button] 所有策略均未命中: '{btn_text}'")
    return result


# ============================================================
# 下拉菜单点击（"更多"展开后的子菜单项）
# ============================================================

async def click_more_menu(
    page: Page,
    row,
    item_text: str,
    ui_framework: str = "element-ui",
) -> dict:
    """点击下拉菜单项（委托 button_driver.click_row_more_item）。

    Args:
        page: Playwright Page
        row: 行 ElementHandle/Locator
        item_text: 菜单项文本
        ui_framework: UI 框架名称

    Returns:
        {"clicked": bool, "strategy": str, "text": str}
    """
    from ..replay.button_driver import ButtonDriver
    driver = ButtonDriver(page)

    click_result = await driver.click_row_more_item(row, item_text)

    if isinstance(click_result, dict):
        return {
            "clicked": click_result.get("clicked", False),
            "strategy": "kb_xpath",
            "text": item_text,
        }
    else:
        return {
            "clicked": bool(click_result),
            "strategy": "kb_xpath",
            "text": item_text,
        }
