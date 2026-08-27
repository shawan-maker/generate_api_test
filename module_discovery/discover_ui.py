"""
discover_ui.py — Stage 1: 前端按钮/元素探测
"""

import json
import logging
from . import const
from .wait_helpers import wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog

LOG = logging.getLogger("discover_ui")


async def discover_all(page, max_retries: int = 2) -> dict:
    """完整探测入口：扫描按钮 + 位置分类 + 下拉菜单 + 表单字段 + 页面结构快照。

    支持重试：页面未加载完成时结果不完整，等待后重试。
    """
    result = None
    for attempt in range(max_retries):
        result = await _discover_once(page)
        from .stage_validators import validate_stage1
        is_valid, issues = validate_stage1(result)
        if is_valid or attempt == max_retries - 1:
            if not is_valid and attempt > 0:
                LOG.warning(f"Stage 1 重试 {attempt + 1} 次后仍有问题: {issues}")
            break
        LOG.info(f"Stage 1 探测不完整 ({len(issues)} 个问题)，等待 2 秒后重试...")
        await page.wait_for_timeout(2000)

    # 页面结构快照
    page_structure = await _snapshot_page_structure(page)
    result["page_structure"] = page_structure
    if page_structure:
        LOG.info(f"  页面结构: fixed_left={page_structure.get('hasFixedLeft')}, "
                 f"fixed_right={page_structure.get('hasFixedRight')}, "
                 f"rows={page_structure.get('mainBodyRows', 0)}")

    return result


async def _discover_once(page) -> dict:
    """单次探测流程（内部函数，由 discover_all 调用）"""
    result = {
        "toolbar_buttons": [], "row_actions": [], "dialog_buttons": [],
        "menu_items": [], "dropdowns": [], "form_fields": [], "summary": {},
    }

    candidates = await _scan_candidates(page)
    LOG.info(f"扫描到 {len(candidates)} 个候选元素")

    result["toolbar_buttons"] = [c for c in candidates if c.get("location") == "toolbar"]
    result["row_actions"] = [c for c in candidates if c.get("location") == "row_action"]
    result["dialog_buttons"] = [c for c in candidates if c.get("location") == "dialog"]
    result["menu_items"] = [c for c in candidates if c.get("location") == "menu"]
    LOG.info(f"  工具栏: {len(result['toolbar_buttons'])}  行操作: {len(result['row_actions'])}  "
             f"弹窗: {len(result['dialog_buttons'])}  菜单: {len(result['menu_items'])}")

    dropdowns = await _discover_dropdowns(page, result["toolbar_buttons"] + result["row_actions"])
    result["dropdowns"] = dropdowns
    LOG.info(f"  下拉菜单子项: {len(dropdowns)}")

    has_create = any(_match_crud(b["text"]) == "create" for b in result["toolbar_buttons"])
    if has_create:
        form_fields = await _scan_create_dialog(page)
        result["form_fields"] = form_fields
        LOG.info(f"  表单字段: {len(form_fields)}")
        await _close_dialog(page)

    result["summary"] = {
        "total_buttons": sum(len(result[k]) for k in
                            ["toolbar_buttons", "row_actions", "dialog_buttons", "menu_items", "dropdowns"]),
        "has_create": has_create,
        "has_delete": any(_match_crud(b["text"]) == "delete"
                          for blist in [result["toolbar_buttons"], result["row_actions"]]
                          for b in blist),
        "has_form": len(result["form_fields"]) > 0,
        "categories": _count_categories(result),
    }
    return result


async def _scan_candidates(page) -> list:
    """CSS 选择器地毯式扫描，一次 evaluate 完成探测+位置分类+来源标记。

    增强：扫描固定列（fixed-left/fixed-right）中的按钮，标记 source 字段。
    """
    selectors = " ".join(s.strip() for s in const.BUTTON_SELECTORS.split("\n")
                         if s.strip() and not s.strip().startswith("#"))
    selectors = selectors.replace(", ", ",").replace(" ,", ",")
    raw = await page.evaluate(f"""() => {{
        const all = document.querySelectorAll('button,.el-button,a,.el-dropdown-menu__item,.el-menu-item,[role="button"],[role="menuitem"]');
        const results = [];
        const seen = new Set();
        const table = document.querySelector('.el-table, table, .ant-table');
        const dialog = document.querySelector('.el-dialog, .ant-modal, .el-drawer');
        const menu = document.querySelector('.el-menu, .ant-menu, .sidebar-menu');
        const fixedRight = table?.querySelector('.el-table__fixed-right');
        const fixedLeft = table?.querySelector('.el-table__fixed');

        all.forEach(el => {{
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            const text = (el.textContent || '').trim();
            if (!text || text.length > 40 || text.length < 1) return;
            const key = text + '|' + el.tagName;
            if (seen.has(key)) return;
            seen.add(key);
            let location = 'toolbar';
            let source = 'main';
            if (table && table.contains(el)) {{
                location = 'row_action';
                if (fixedRight && fixedRight.contains(el)) source = 'fixed-right';
                else if (fixedLeft && fixedLeft.contains(el)) source = 'fixed-left';
            }}
            else if (dialog && dialog.contains(el)) location = 'dialog';
            else if (menu && menu.contains(el)) location = 'menu';
            results.push({{
                text, tag: el.tagName, className: (el.className || '').slice(0, 60),
                rect: Math.round(r.width) + 'x' + Math.round(r.height), location, source,
            }});
        }});
        return results;
    }}""")
    return raw


async def _discover_dropdowns(page, parent_buttons: list) -> list:
    """对有下拉菜单的按钮展开扫描。

    增强：不仅检查硬编码文本，还检查 .el-dropdown 类的元素。
    """
    dropdown_items = []

    # 收集所有可能的下拉触发器
    triggers = set()
    for btn in parent_buttons:
        text = btn["text"]
        # 硬编码触发器
        if text in ("更多", "操作", "Actions", "More", "批量操作"):
            triggers.add(text)

    # 动态发现：检查 .el-dropdown 元素
    dropdown_triggers = await page.evaluate("""() => {
        const results = [];
        document.querySelectorAll('.el-dropdown, [class*="dropdown"]').forEach(el => {
            const text = (el.textContent || '').trim();
            if (text && text.length < 20) {
                results.push(text);
            }
        });
        return results;
    }""")
    triggers.update(dropdown_triggers)

    # 展开每个触发器
    for text in triggers:
        clicked = await page.evaluate(f"""() => {{
            const all = document.querySelectorAll('span, button, a, .el-dropdown');
            for (const el of all) {{
                if ((el.textContent || '').trim() === '{text}' && el.offsetWidth > 0) {{
                    el.click(); return true;
                }}
            }}
            return false;
        }}""")
        if not clicked:
            continue
        # 等待下拉菜单出现
        await wait_for_dropdown(page, timeout=3000)
        items = await page.evaluate("""() => {
            const items = document.querySelectorAll('.el-dropdown-menu__item, .ant-dropdown-menu-item, [role="menuitem"]');
            return Array.from(items).filter(el => el.offsetWidth > 0).map(el => el.textContent.trim()).filter(t => t);
        }""")
        for item in items:
            dropdown_items.append({"text": item, "parent": text, "tag": "DROPDOWN_ITEM"})
        await page.keyboard.press("Escape")
        # 等待下拉菜单消失
        await wait_for_dialog_dismissed(page, timeout=3000)
    return dropdown_items


async def _scan_create_dialog(page) -> list:
    """点击创建按钮打开弹窗，扫描表单字段。

    注意：有些系统的"创建"按钮会导致页面跳转（而非弹窗），
    此时需要导航回原始页面。
    """
    url_before = page.url
    create_keywords = ["新增", "创建", "添加", "新建"]
    clicked = False
    for kw in create_keywords:
        clicked = await page.evaluate(f"""() => {{
            const all = document.querySelectorAll('span, button, a');
            for (const el of all) {{
                if ((el.textContent || '').trim().includes('{kw}') && el.offsetWidth > 0) {{
                    el.click(); return true;
                }}
            }}
            return false;
        }}""")
        if clicked:
            break
    if not clicked:
        return []

    await page.wait_for_timeout(2000)

    # 检测是否发生了页面跳转（非弹窗模式）
    url_after = page.url
    if url_after != url_before and "/create" in url_after:
        LOG.info(f"  创建按钮触发了页面跳转: {url_after[:60]}")
        # 在创建页扫描表单字段
        fields = await page.evaluate("""() => {
            const inps = document.querySelectorAll('.el-form-item input, .el-form-item textarea, .el-form-item .el-select .el-input__inner');
            return Array.from(inps).map(i => {
                let label = '';
                const fi = i.closest('.el-form-item');
                if (fi) {
                    const lbl = fi.querySelector('.el-form-item__label');
                    if (lbl) label = lbl.textContent.trim();
                }
                return { label: label || i.placeholder || '', placeholder: i.placeholder || '',
                         type: i.type || 'text', required: !!(i.required || (fi && fi.classList.contains('is-required'))),
                         tag: i.tagName };
            });
        }""")
        # 导航回原始页面
        LOG.info(f"  导航回原始页面: {url_before[:60]}")
        try:
            await page.goto(url_before, wait_until="load", timeout=15000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            LOG.warning(f"  导航回原始页面失败: {e}")
        return fields

    # 弹窗模式：等待弹窗出现
    await wait_for_dialog(page, timeout=5000)
    fields = await page.evaluate("""() => {
        const dlg = document.querySelector('.el-dialog, .ant-modal');
        if (!dlg) return [];
        const inps = dlg.querySelectorAll('input, textarea, .el-select .el-input__inner');
        return Array.from(inps).map(i => {
            let label = '';
            const fi = i.closest('.el-form-item, .ant-form-item');
            if (fi) {
                const lbl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
                if (lbl) label = lbl.textContent.trim();
            }
            return { label: label || i.placeholder || '', placeholder: i.placeholder || '',
                     type: i.type || 'text', required: !!(i.required || (fi && fi.classList.contains('is-required'))),
                     tag: i.tagName };
        });
    }""")
    return fields


async def _close_dialog(page):
    """关闭弹窗（尝试点击关闭按钮和按 ESC）。

    如果页面已导航回（非弹窗模式），跳过此步骤。
    """
    # 检查是否有可见的对话框
    has_dialog = await page.evaluate("""() => {
        const wrappers = document.querySelectorAll('.el-dialog__wrapper, .ant-modal-wrap, .el-drawer');
        return Array.from(wrappers).some(w => w.style.display !== 'none' && w.offsetWidth > 0);
    }""")
    if not has_dialog:
        return  # 非弹窗模式，已在 _scan_create_dialog 中导航回

    try:
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('.el-dialog__headerbtn, .el-drawer__close-btn, .ant-modal-close, [class*="close"]');
            for (const b of btns) { if (b.offsetWidth > 0) { b.click(); return; } }
        }""")
        await page.wait_for_timeout(500)
        await page.keyboard.press("Escape")
    except Exception as e:
        LOG.debug(f"关闭弹窗失败: {e}")


def _match_crud(text: str) -> str:
    for crud, keywords in const.ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return crud
    return "unknown"


def _count_categories(result: dict) -> dict:
    counts = {}
    for key in ("toolbar_buttons", "row_actions", "dropdowns"):
        for btn in result.get(key, []):
            cat = _match_crud(btn["text"])
            counts[cat] = counts.get(cat, 0) + 1
    return counts


async def _snapshot_page_structure(page) -> dict:
    """获取页面 DOM 骨架，识别表格结构（含固定列位置、操作列索引）。

    输出供 Stage 2 选择正确的 wrapper 进行行操作。
    """
    try:
        return await page.evaluate("""() => {
            const table = document.querySelector('.el-table');
            if (!table) return {};

            // 查找操作列索引
            let operationColumnIndex = -1;
            const headerCells = table.querySelectorAll('.el-table__header-wrapper th');
            headerCells.forEach((cell, idx) => {
                const text = cell.textContent.trim();
                if (text.includes('操作') || text.includes('Action')) {
                    operationColumnIndex = idx;
                }
            });

            // 枚举表格 wrapper
            const wrappers = [];
            table.querySelectorAll('[class*="wrapper"]').forEach(w => {
                wrappers.push({
                    cls: w.className.slice(0, 80),
                    rowCount: w.querySelectorAll('tbody tr').length,
                });
            });

            return {
                hasFixedLeft: !!table.querySelector('.el-table__fixed'),
                hasFixedRight: !!table.querySelector('.el-table__fixed-right'),
                mainBodyRows: table.querySelectorAll('.el-table__body-wrapper tbody tr').length,
                fixedRightRows: table.querySelectorAll('.el-table__fixed-right .el-table__fixed-body-wrapper tbody tr').length,
                fixedLeftRows: table.querySelectorAll('.el-table__fixed .el-table__fixed-body-wrapper tbody tr').length,
                columnCount: headerCells.length,
                operationColumnIndex: operationColumnIndex,
                operationColumnLocation: operationColumnIndex === headerCells.length - 1 ? 'last' :
                    operationColumnIndex === 0 ? 'first' : operationColumnIndex >= 0 ? 'middle' : 'none',
                tableWrappers: wrappers.slice(0, 10),
            };
        }""")
    except Exception as e:
        LOG.debug(f"页面结构快照失败: {e}")
        return {}
