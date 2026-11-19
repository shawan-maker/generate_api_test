"""
shared/row_interaction.py — 行交互（定位/勾选/按钮点击）Stage 1/2 统一实现

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 行定位+重试逻辑统一，JS 代码统一

统一逻辑：
- locate_row_with_retry: 通过 marker 定位行（含重试）
- select_row_checkbox: 勾选行 checkbox（幂等）
- click_row_button: 行内按钮点击（JS click，兼容固定列）

调用方：
    - Stage 1: crud_executor.py (_do_edit, _do_delete, _ensure_row_selected)
    - Stage 1: base_executor.py (_ensure_row_selected)
    - Stage 2: replay_engine.py (_step_find_row, _step_click_row_button,
                                 _step_select_row_checkbox)
"""

import logging
from typing import Optional
from playwright.async_api import Page, ElementHandle

LOG = logging.getLogger(__name__)

# 所有表格行 JS 查询共享的作用域前缀
# 使用方式：在 JS evaluate 字符串开头嵌入，然后用 _scope 代替 document
_TAB_SCOPE_JS = """
    const _panes = document.querySelectorAll('.el-tab-pane');
    let _scope = document;
    for (const _p of _panes) {
        const _s = window.getComputedStyle(_p);
        if (_s.display !== 'none' && _s.visibility !== 'hidden') {
            _scope = _p;
            break;
        }
    }
"""


async def locate_row_with_retry(
    page: Page,
    marker: str,
    max_retries: int = 2,
) -> Optional[ElementHandle]:
    """通过 marker 文本定位表格行，含重试。

    委托 ButtonDriver.find_data_row 进行实际定位，
    在失败时等待并重试。

    Args:
        page: Playwright Page 对象
        marker: 行标识文本
        max_retries: 最大重试次数

    Returns:
        ElementHandle: 找到的行元素
        None: 未找到
    """
    if not marker:
        return None

    from core.discovery.replay.button_driver import ButtonDriver
    driver = ButtonDriver(page)

    for attempt in range(max_retries):
        row = await driver.find_data_row(marker)
        if row:
            return row

        if attempt < max_retries - 1:
            LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_retries})")
            await page.wait_for_timeout(1000)

    return None


async def select_row_checkbox(
    page: Page,
    marker: str = None,
) -> dict:
    """勾选数据行的 checkbox（JS click，兼容 Element UI 固定列）。

    幂等操作：先检查是否已勾选，已勾选则跳过。

    Element UI 固定列表格中，主体 wrapper 的 checkbox 是隐藏的占位元素，
    只有固定列 wrapper 中的 checkbox 可见。Playwright click 要求元素可见，
    因此使用 JS click 直接触发事件。

    Args:
        page: Playwright Page 对象
        marker: 行标识文本（None 则勾选第一行）

    Returns:
        dict: {
            "success": bool,
            "already_checked": bool,
        }
    """
    try:
        if marker:
            checked = await page.evaluate(f"""(text) => {{
                {_TAB_SCOPE_JS}
                const rows = _scope.querySelectorAll('.el-table__body tr');
                for (const row of rows) {{
                    if ((row.textContent || '').includes(text)) {{
                        // 检查行是否已勾选
                        const isChecked = row.querySelector('.el-checkbox__input.is-checked') ||
                                          row.querySelector('.el-checkbox__input input:checked');
                        if (isChecked) {{
                            return {{checked: true, already_checked: true}};
                        }}
                        const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                        if (cb && cb.offsetWidth > 0) {{
                            cb.click();
                            return {{checked: true, already_checked: false}};
                        }}
                    }}
                }}
                return {{checked: false, already_checked: false}};
            }}""", marker)
        else:
            # 勾选第一行
            checked = await page.evaluate(f"""() => {{
                {_TAB_SCOPE_JS}
                const firstRow = _scope.querySelector('.el-table__body tr:first-child');
                if (!firstRow) return {{checked: false, already_checked: false}};
                const isChecked = firstRow.querySelector('.el-checkbox__input.is-checked') ||
                                  firstRow.querySelector('.el-checkbox__input input:checked');
                if (isChecked) {{
                    return {{checked: true, already_checked: true}};
                }}
                const cb = firstRow.querySelector('.el-checkbox__input, input[type="checkbox"]');
                if (cb && cb.offsetWidth > 0) {{
                    cb.click();
                    return {{checked: true, already_checked: false}};
                }}
                return {{checked: false, already_checked: false}};
            }}""")

        result = {
            "success": bool(checked and checked.get('checked')),
            "already_checked": bool(checked and checked.get('already_checked')),
        }

        if result["already_checked"]:
            LOG.debug(f"    [select_row_checkbox] 行已勾选，跳过")
        elif result["success"]:
            LOG.debug(f"    [select_row_checkbox] 已勾选 marker: {marker}")
            # 等待 Vue 响应式状态更新
            await page.wait_for_timeout(500)
        else:
            LOG.warning(f"    [select_row_checkbox] 未找到含 '{marker}' 的行或无可见 checkbox")

        return result

    except Exception as e:
        LOG.debug(f"    ⚠️ select_row_checkbox 异常: {e}")
        return {"success": False, "already_checked": False}


# 行内按钮统一查找+点击过滤规则（与 button_driver.click_row_button_v2 方法3 对齐）：
# 可见性（offsetWidth/offsetHeight）→ disabled/is-disabled → 隐藏祖先。
# 防止点到 Element UI 固定列隐藏占位、互斥操作对中 offsetWidth=0 的隐藏项或禁用项。
_ROW_BUTTON_FILTER_JS = """
    if ((btn.textContent || '').trim().includes(btnText)) {
        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
        if (btn.disabled || btn.classList.contains('is-disabled')) continue;
        if (btn.closest('.is-hidden') || btn.closest('[style*="display: none"]')) continue;
        btn.click();
        return true;
    }
"""


async def js_click_row_button(
    page: Page,
    row,
    btn_text: str,
    marker: str = "",
) -> bool:
    """行内按钮 JS click 兜底（Stage 1/2 单源实现，消除 C-6 双实现漂移）。

    合并自两处原有 JS btn.click() 兜底：
    - 本模块 click_row_button 的 JS 降级（原 marker 分支 + else 分支——
      else 分支 arguments[0].querySelectorAll 对字符串调用必抛 TypeError，随合并修复）
    - button_driver.click_row_button_v2 方法 3（行作用域 + 可见性/disabled 过滤）

    两种形态（按 marker 有无互斥选择）：
    - marker 形态：在可见 tab 作用域内按 marker 文本找行，再在行内找按钮
      （表格重渲染致 row 句柄失效时的稳健路径）
    - row 形态：直接在给定行元素（ElementHandle/Locator）内找按钮

    Args:
        page: Playwright Page 对象
        row: 行元素（ElementHandle/Locator）；marker 形态下可为 None
        btn_text: 按钮文本
        marker: 可选，行标识文本（提供时优先 marker 形态）

    Returns:
        bool: 是否成功点击（JS 执行异常按失败返回，不抛出）
    """
    if not btn_text:
        return False

    try:
        if marker:
            clicked = await page.evaluate(f"""(args) => {{
                const [btnText, markerText] = args;
                {_TAB_SCOPE_JS}
                const rows = _scope.querySelectorAll('.el-table__body tr');
                for (const row of rows) {{
                    if ((row.textContent || '').includes(markerText)) {{
                        const buttons = row.querySelectorAll('button, .el-button, a, span');
                        for (const btn of buttons) {{{_ROW_BUTTON_FILTER_JS}
                        }}
                    }}
                }}
                return false;
            }}""", [btn_text, marker])
        else:
            if row is None:
                return False
            clicked = await row.evaluate(f"""(rowEl, btnText) => {{
                const buttons = rowEl.querySelectorAll('button, .el-button, a, span');
                for (const btn of buttons) {{{_ROW_BUTTON_FILTER_JS}
                }}
                return false;
            }}""", btn_text)
        return bool(clicked)
    except Exception as e:
        LOG.debug(f"js_click_row_button 异常: {e}")
        return False


async def click_row_button(
    page: Page,
    row: ElementHandle,
    btn_text: str,
    marker: str = "",
) -> bool:
    """点击行内指定文本的按钮（JS click，兼容 Element UI 固定列）。

    优先使用 ButtonDriver.click_row_button_v2（已有完善实现），
    失败时降级为 JS click。

    Args:
        page: Playwright Page 对象
        row: 行 ElementHandle
        btn_text: 按钮文本
        marker: marker 文本（JS 降级时使用）

    Returns:
        bool: 是否成功点击
    """
    # 优先使用 ButtonDriver.click_row_button_v2
    from core.discovery.replay.button_driver import ButtonDriver
    driver = ButtonDriver(page)

    try:
        clicked = await driver.click_row_button_v2(row, btn_text)
        if clicked:
            LOG.debug(f"    [click_row_button] ButtonDriver 成功: {btn_text}")
            from core.discovery.replay.wait_helpers import wait_for_loading_complete
            await wait_for_loading_complete(page, timeout=10000)
            return True
    except Exception as e:
        LOG.warning(f"click_row_button_v2 异常，尝试 JS 降级: {e}")

    # 降级：JS click（兼容 Element UI 固定列）——单源实现 js_click_row_button
    clicked = await js_click_row_button(page, row, btn_text, marker=marker)

    if clicked:
        LOG.debug(f"    [click_row_button] JS fallback 成功: {btn_text}")
        from core.discovery.replay.wait_helpers import wait_for_loading_complete
        await wait_for_loading_complete(page, timeout=10000)
        return True

    LOG.warning(f"    [click_row_button] 未找到行内按钮: {btn_text}")
    return False


async def get_row_selector_by_marker(page: Page, marker: str) -> str:
    """根据 marker 文本获取所在行的 CSS 选择器（限定在可见 tab-pane 内查找）。

    Args:
        page: Playwright Page 对象
        marker: 行标识文本

    Returns:
        str: 行的 CSS 选择器，如 "#pane1 .el-table__body-wrapper .el-table__row:nth-child(1)"
             如果未找到，返回空字符串
    """
    if not marker:
        return ""

    try:
        row_info = await page.evaluate(f"""(text) => {{
            {_TAB_SCOPE_JS}
            const table = _scope !== document
                ? _scope.querySelector('.el-table__body-wrapper')
                : document.querySelector('.el-table__body-wrapper');
            if (!table) return {{ found: false }};

            const rows = table.querySelectorAll('.el-table__row');
            for (let i = 0; i < rows.length; i++) {{
                const rowText = rows[i].textContent || '';
                if (rowText.includes(text)) {{
                    const paneId = (_scope !== document && _scope.id) ? _scope.id : null;
                    const selector = paneId
                        ? `#${{paneId}} .el-table__body-wrapper .el-table__row:nth-child(${{i + 1}})`
                        : `.el-table__body-wrapper .el-table__row:nth-child(${{i + 1}})`;
                    return {{ found: true, selector: selector }};
                }}
            }}
            return {{ found: false }};
        }}""", marker)
        if row_info and row_info.get("found"):
            return row_info.get("selector", "")
        return ""
    except Exception:
        return ""
