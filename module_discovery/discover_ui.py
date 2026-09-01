"""
discover_ui.py — Stage 1: 前端按钮/元素探测 + 业务闭环验证
"""

import json
import logging
import time
from . import const
from .wait_helpers import (
    wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog,
    wait_for_loading_complete, wait_for_table_ready,
    wait_for_navigation_complete,
)
from .kb_loader import ProbeKB
from .form_filler import (
    FormFiller, read_form_errors, generate_fill_data, scan_form_fields,
    generate_fill_rules,
)

LOG = logging.getLogger("discover_ui")

# 全局知识库实例
_kb: ProbeKB = None


def _get_kb() -> ProbeKB:
    """获取全局知识库实例（延迟加载）"""
    global _kb
    if _kb is None:
        _kb = ProbeKB()
    return _kb


async def discover_all(page) -> dict:
    """完整探测入口：扫描按钮 + 位置分类 + 下拉菜单 + 表单字段 + 页面结构快照。

    Args:
        page: Playwright Page 对象
    """
    kb = _get_kb()
    framework = await _detect_ui_framework(page)

    result = await _discover_once(page, kb, framework)

    # 页面结构快照
    page_structure = await _snapshot_page_structure(page)
    result["page_structure"] = page_structure
    result["framework"] = framework

    if page_structure:
        LOG.info(f"  页面结构: fixed_left={page_structure.get('hasFixedLeft')}, "
                 f"fixed_right={page_structure.get('hasFixedRight')}, "
                 f"rows={page_structure.get('mainBodyRows', 0)}")

    return result


async def _discover_once(page, kb: ProbeKB, framework: str) -> dict:
    """单次探测流程（内部函数，由 discover_all 调用）

    Args:
        page: Playwright Page 对象
        kb: 知识库实例
        framework: UI 框架类型
    """
    result = {
        "toolbar_buttons": [], "row_actions": [], "dialog_buttons": [],
        "menu_items": [], "dropdowns": [], "form_fields": [], "summary": {},
    }

    # 1. CSS 地毯扫描
    candidates = await _scan_candidates(page)
    LOG.info(f"扫描到 {len(candidates)} 个候选元素")

    # 2. KB 增强扫描
    kb_extra = await _kb_enrichment_scan(page, candidates, kb, framework)
    if kb_extra:
        candidates.extend(kb_extra)
        LOG.info(f"  KB 增强扫描发现 {len(kb_extra)} 个额外元素")

    result["toolbar_buttons"] = [c for c in candidates if c.get("location") == "toolbar"]
    result["row_actions"] = [c for c in candidates if c.get("location") == "row_action"]
    result["dialog_buttons"] = [c for c in candidates if c.get("location") == "dialog"]
    result["menu_items"] = [c for c in candidates if c.get("location") == "menu"]
    LOG.info(f"  工具栏: {len(result['toolbar_buttons'])}  行操作: {len(result['row_actions'])}  "
             f"弹窗: {len(result['dialog_buttons'])}  菜单: {len(result['menu_items'])}")

    # 2.5 iframe 扫描（标准步骤）
    iframe_results = await _scan_iframes(page)
    if iframe_results:
        LOG.info(f"  iframe 扫描发现 {len(iframe_results)} 个元素")
        for iframe_btn in iframe_results:
            loc = iframe_btn.get("location", "toolbar")
            key = f"{loc}_buttons" if loc != "row_action" else "row_actions"
            if key not in result:
                key = "toolbar_buttons"
            # 去重
            if not any(b["text"] == iframe_btn["text"] for b in result[key]):
                result[key].append(iframe_btn)
                LOG.debug(f"    + iframe {key}: {iframe_btn['text']}")

    # 3. 下拉菜单发现
    dropdowns = await _discover_dropdowns(page, result["toolbar_buttons"] + result["row_actions"], kb, framework)
    result["dropdowns"] = dropdowns
    LOG.info(f"  下拉菜单子项: {len(dropdowns)}")

    # 4. 创建表单扫描（同时扫描弹窗按钮）
    has_create = any(_match_crud(b["text"]) == "create" for b in result["toolbar_buttons"])
    if has_create:
        scan_result = await _scan_create_dialog(page, kb, framework)
        result["form_fields"] = scan_result.get("form_fields", [])
        LOG.info(f"  表单字段: {len(result['form_fields'])}")
        # 合并弹窗按钮（去重）
        for btn in scan_result.get("dialog_buttons", []):
            if not any(b["text"] == btn["text"] for b in result["dialog_buttons"]):
                result["dialog_buttons"].append(btn)
        if scan_result.get("dialog_buttons"):
            LOG.info(f"  弹窗按钮: {len(result['dialog_buttons'])} (含创建弹窗内)")
        await _close_dialog(page)

    # 4.5 为所有按钮赋值 action 字段
    for key in ["toolbar_buttons", "row_actions", "dialog_buttons"]:
        for btn in result[key]:
            if "action" not in btn or not btn["action"]:
                btn["action"] = _match_crud(btn["text"])

    # 5. 动态标签构建
    result["button_labels"] = _build_button_labels(result)

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
        const all = document.querySelectorAll('{selectors}');
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


async def _discover_dropdowns(page, parent_buttons: list, kb: ProbeKB = None, framework: str = "element-ui") -> list:
    """对有下拉菜单的按钮展开扫描。

    增强：不仅检查硬编码文本，还检查 .el-dropdown 类的元素。
    """
    dropdown_items = []

    # 收集所有可能的下拉触发器
    triggers = set()
    for btn in parent_buttons:
        text = btn["text"]
        # 硬编码触发器（兜底）
        if text in ("更多", "操作", "Actions", "More", "批量操作"):
            triggers.add(text)

    # 动态发现：检查 .el-dropdown 元素（限定到表格/工具栏区域，排除导航菜单）
    parent_texts = [btn["text"] for btn in parent_buttons]
    dropdown_triggers = await page.evaluate("""(parentTexts) => {
        const results = [];
        const parentSet = new Set(parentTexts);
        const table = document.querySelector('.el-table, table, .ant-table');
        const toolbar = table?.closest('[class*="toolbar"], [class*="Toolbar"]')
                       || table?.parentElement;
        const scopes = [table, toolbar].filter(Boolean);
        scopes.forEach(scope => {
            scope.querySelectorAll('.el-dropdown, [class*="dropdown"]').forEach(el => {
                const text = (el.textContent || '').trim();
                if (text && text.length < 20 && parentSet.has(text)) {
                    results.push(text);
                }
            });
        });
        return results;
    }""", parent_texts)
    triggers.update(dropdown_triggers)

    # 展开每个触发器（优化：去重 + 快速失败）
    for text in triggers:
        clicked = await page.evaluate(f"""(text) => {{
            const table = document.querySelector('.el-table, table, .ant-table');
            const toolbar = table?.closest('[class*="toolbar"], [class*="Toolbar"]')
                           || table?.parentElement;
            const scopes = [table, toolbar].filter(Boolean);
            for (const scope of scopes) {{
                const all = scope.querySelectorAll('span, button, a, .el-dropdown');
                for (const el of all) {{
                    if ((el.textContent || '').trim() === text && el.offsetWidth > 0) {{
                        el.click(); return true;
                    }}
                }}
            }}
            return false;
        }}""", text)
        if not clicked:
            continue
        # 等待下拉菜单出现（快速超时）
        try:
            await wait_for_dropdown(page, timeout=1500)
        except Exception:
            # 菜单未出现，跳过等待消失
            await page.keyboard.press("Escape")
            continue
        items = await page.evaluate("""() => {
            const items = document.querySelectorAll('.el-dropdown-menu__item, .ant-dropdown-menu-item, [role="menuitem"]');
            return Array.from(items)
                .filter(el => {
                    if (el.offsetWidth <= 0) return false;
                    const navParent = el.closest('.el-menu, .ant-menu, .sidebar-menu, nav');
                    return !navParent;
                })
                .map(el => el.textContent.trim())
                .filter(t => t);
        }""")
        for item in items:
            # 去重：同一 parent 下不重复添加
            if not any(d["text"] == item and d["parent"] == text for d in dropdown_items):
                dropdown_items.append({
                    "text": item,
                    "parent": text,
                    "tag": "DROPDOWN_ITEM",
                    "location": "dropdown",
                    "action": _match_crud(item),
                })
        await page.keyboard.press("Escape")
        # 等待下拉菜单消失（快速超时，失败也不阻塞）
        try:
            await wait_for_dialog_dismissed(page, timeout=1000)
        except Exception:
            pass
    return dropdown_items


async def _scan_create_dialog(page, kb: ProbeKB = None, framework: str = "element-ui") -> dict:
    """点击创建按钮打开弹窗，扫描表单字段和弹窗按钮。

    注意：有些系统的"创建"按钮会导致页面跳转（而非弹窗），
    此时需要导航回原始页面。

    支持 iframe：遍历所有 frame 扫描表单字段。

    Returns:
        dict: {"form_fields": list, "dialog_buttons": list}
    """
    from .wait_helpers import wait_for_loading_complete

    result = {"form_fields": [], "dialog_buttons": []}
    url_before = page.url
    # 使用 ACTION_KEYWORDS 中的完整关键词列表
    create_keywords = const.ACTION_KEYWORDS.get("create", ["新增", "创建", "添加", "新建"])
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
        return result

    # 点击创建按钮后等待加载完成（可能打开弹窗/抽屉/跳转页面）
    await wait_for_loading_complete(page)

    # 统一的表单扫描 JS（与 form_filler.scan_form_fields 相同逻辑，
    # 输出含 kb_category 的统一 schema）
    _SCAN_FIELDS_JS = """() => {
        const fields = [];
        const debugInfo = [];
        const formItems = document.querySelectorAll('.el-form-item, .ant-form-item');
        formItems.forEach(fi => {
            const labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            if (!label) return;
            const r = fi.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            const required = fi.classList.contains('is-required') ||
                             fi.querySelector('[class*="required"]') !== null;

            // 复合组件检测：el-select + 独立 input（如手机号带国家编码）
            const selectEl = fi.querySelector('.el-select, .ant-select');
            const allInputs = fi.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])');
            const independentInputs = Array.from(allInputs)
                .filter(inp => !inp.readOnly && !selectEl?.contains(inp) && inp.offsetWidth > 0);

            // 调试：手机号字段
            if (label.includes('手机') || label.includes('电话') || label.includes('phone')) {
                debugInfo.push({
                    label: label,
                    hasSelect: !!selectEl,
                    allInputsCount: allInputs.length,
                    independentInputsCount: independentInputs.length,
                    independentInputs: Array.from(independentInputs).map(inp => ({
                        type: inp.type,
                        readOnly: inp.readOnly,
                        width: inp.offsetWidth,
                        inSelect: selectEl?.contains(inp),
                        placeholder: inp.placeholder
                    })),
                    allInputsInfo: Array.from(allInputs).map(inp => ({
                        type: inp.type,
                        readOnly: inp.readOnly,
                        width: inp.offsetWidth,
                        inSelect: selectEl?.contains(inp),
                        placeholder: inp.placeholder
                    }))
                });
            }

            if (selectEl && independentInputs.length > 0) {
                // 复合组件：el-select + 独立 input
                fields.push({ label: label + '(下拉)', type: 'select', kb_category: 'el-select',
                             selector: null, required });
                independentInputs.forEach(inp => {
                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: null, inputType: inp.type || 'text', required });
                });
            }
            else if (fi.querySelector('.el-cascader')) {
                fields.push({ label, type: 'cascader', kb_category: 'el-cascader',
                             selector: null, required });
            } else if (fi.querySelector('.el-date-editor, .ant-picker')) {
                fields.push({ label, type: 'date-picker', kb_category: 'date-picker',
                             selector: null, required });
            } else if (selectEl) {
                fields.push({ label, type: 'select', kb_category: 'el-select',
                             selector: null, required });
            } else if (fi.querySelector('.el-radio-group')) {
                const firstRadio = fi.querySelector('.el-radio-button__inner, .el-radio__label');
                fields.push({ label, type: 'radio', kb_category: 'radio',
                             selector: null, required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' });
            } else if (fi.querySelector('.el-checkbox') && !fi.closest('.el-table')) {
                fields.push({ label, type: 'checkbox', kb_category: 'form-checkbox',
                             selector: null, required });
            } else {
                // 通用 input / textarea
                const ta = fi.querySelector('textarea');

                // 获取所有可见的独立 input
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                )).filter(inp => !inp.readOnly && inp.offsetWidth > 0);

                if (allInputs.length >= 2) {
                    // 多个独立 input：选择主输入框
                    const mainInput = allInputs.find(inp =>
                        inp.placeholder && (inp.placeholder.includes('请输入') || inp.placeholder.includes('Enter'))
                    ) || allInputs.reduce((max, inp) => inp.offsetWidth > max.offsetWidth ? inp : max, allInputs[0]);

                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: null, inputType: mainInput.type || 'text', required });
                } else if (allInputs.length === 1) {
                    // 单个 input
                    const inp = allInputs[0];
                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: null, inputType: inp.type || 'text', required });
                } else if (ta) {
                    fields.push({ label, type: 'textarea', kb_category: 'textarea-generic',
                                 selector: null, required });
                }
            }
        });
        return {fields, debugInfo};
    }"""

    # 检测页面状态变化：弹窗 vs 页面跳转
    # 优先检查 DOM 中是否有可见弹窗（最可靠的判断）
    url_after = page.url
    dialog_check = await page.evaluate("""() => {
        const wrappers = document.querySelectorAll('.el-dialog__wrapper');
        const drawers = document.querySelectorAll('.el-drawer');
        const antModals = document.querySelectorAll('.ant-modal-wrap');

        for (const w of wrappers) {
            if (w.style.display !== 'none' && w.offsetWidth > 0) return 'dialog';
        }
        for (const d of drawers) {
            if (d.offsetWidth > 0) return 'drawer';
        }
        for (const m of antModals) {
            if (m.offsetWidth > 0) return 'dialog';
        }
        // 也检查 form 元素（可能弹窗内表单已渲染但 wrapper 检测遗漏）
        const formItems = document.querySelectorAll('.el-form-item');
        let visibleCount = 0;
        for (const fi of formItems) {
            const r = fi.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) visibleCount++;
        }
        return visibleCount >= 3 ? 'form_visible' : 'none';
    }""")
    LOG.info(f"  创建按钮后状态: URL变化={'是' if url_after != url_before else '否'}, "
             f"DOM检测={dialog_check}")

    # 判断模式：DOM 有弹窗/表单 → 弹窗模式；否则检查 URL 路径变化
    from urllib.parse import urlparse
    path_before = urlparse(url_before).path
    path_after = urlparse(url_after).path
    is_page_jump = (dialog_check == 'none' and
                    path_before != path_after and
                    any(kw in path_after for kw in ["/create", "/add", "/new"]))

    if is_page_jump:
        LOG.info(f"  创建按钮触发了页面跳转: {url_after[:80]}")
        # 等待新页面加载
        await wait_for_loading_complete(page)
        await page.wait_for_timeout(2000)
        # 在创建页扫描表单字段（支持 iframe）
        fields = await _scan_fields_in_all_frames(page, _SCAN_FIELDS_JS)
        result["form_fields"] = fields

        # 扫描创建页的操作按钮（确定/取消/保存等）
        page_buttons = await _scan_page_buttons_for_create(page)
        result["dialog_buttons"] = page_buttons
        LOG.info(f"  创建页: 表单字段={len(fields)}, 按钮={len(page_buttons)}")

        # 导航回原始页面
        LOG.info(f"  导航回原始页面: {url_before[:60]}")
        try:
            await page.goto(url_before, wait_until="load", timeout=15000)
            await wait_for_loading_complete(page)
            # 等待表格数据加载完成（tbody 中有数据行）
            for _ in range(10):  # 最多等 5 秒
                has_rows = await page.evaluate("""() => {
                    const tbody = document.querySelector('.el-table__body-wrapper tbody, table tbody');
                    return tbody && tbody.children.length > 0;
                }""")
                if has_rows:
                    LOG.debug("  表格数据已就绪")
                    break
                await page.wait_for_timeout(500)
        except Exception as e:
            LOG.warning(f"  导航回原始页面失败: {e}")
        return result

    # 弹窗模式：等待弹窗出现，使用统一 schema 扫描（支持 iframe）
    await wait_for_dialog(page, timeout=5000)
    fields = await _scan_fields_in_all_frames(page, _SCAN_FIELDS_JS)
    result["form_fields"] = fields

    # 诊断：检查弹窗 DOM 结构（帮助定位按钮未发现的问题）
    dialog_diag = await page.evaluate("""() => {
        const wrappers = document.querySelectorAll('.el-dialog__wrapper');
        const info = [];
        for (const w of wrappers) {
            if (w.style.display === 'none') continue;
            const dialog = w.querySelector('.el-dialog');
            const footer = w.querySelector('.el-dialog__footer');
            const header = w.querySelector('.el-dialog__header');
            const body = w.querySelector('.el-dialog__body');
            const allBtns = w.querySelectorAll('button');
            info.push({
                wrapperDisplay: w.style.display || '(empty)',
                wrapperOffsetW: w.offsetWidth,
                wrapperOffsetH: w.offsetHeight,
                hasDialog: !!dialog,
                dialogVisible: dialog ? (dialog.offsetWidth > 0) : false,
                hasFooter: !!footer,
                footerBtns: footer ? footer.querySelectorAll('button').length : 0,
                bodyFormItems: body ? body.querySelectorAll('.el-form-item').length : 0,
                allButtons: allBtns.length,
                btnTexts: Array.from(allBtns).slice(0, 10).map(b => ({
                    text: (b.textContent || '').trim().slice(0, 20),
                    visible: b.offsetWidth > 0,
                    inFooter: !!footer && footer.contains(b),
                }))
            });
        }
        return info;
    }""")
    LOG.info(f"  弹窗诊断: {json.dumps(dialog_diag, ensure_ascii=False)}")

    # 同时扫描弹窗内的按钮（confirm/cancel 等）
    dialog_buttons = await _scan_dialog_buttons(page)

    # 如果标准弹窗扫描未找到按钮，但检测到 form_visible，尝试扫描页面按钮作为后备
    if not dialog_buttons and dialog_check == 'form_visible':
        LOG.info("  标准弹窗容器未找到按钮，尝试扫描页面操作按钮（form_visible 模式）")
        dialog_buttons = await _scan_page_buttons_for_create(page)

    result["dialog_buttons"] = dialog_buttons

    return result


async def _scan_fields_in_all_frames(page, scan_js: str) -> list:
    """遍历所有 frame（包括 iframe）扫描表单字段。

    Args:
        page: Playwright 页面对象
        scan_js: 表单扫描的 JavaScript 代码

    Returns:
        list: 合并后的表单字段列表
    """
    all_fields = []

    def _extract_fields(result):
        """从 JS 返回值中提取字段列表。

        兼容两种返回格式：
        - 旧格式：直接返回数组 [field1, field2, ...]
        - 新格式：返回对象 {fields: [...], debugInfo: [...]}
        """
        if isinstance(result, dict):
            fields = result.get("fields", [])
            debug_info = result.get("debugInfo", [])
            if debug_info:
                LOG.info(f"    表单扫描调试信息:")
                for info in debug_info:
                    LOG.info(f"      字段 '{info.get('label')}': "
                             f"hasSelect={info.get('hasSelect')}, "
                             f"allInputs={info.get('allInputsCount')}, "
                             f"independentInputs={info.get('independentInputsCount')}")
                    for inp_info in info.get("allInputsInfo", []):
                        LOG.info(f"        input: type={inp_info.get('type')}, "
                                 f"readOnly={inp_info.get('readOnly')}, "
                                 f"width={inp_info.get('width')}, "
                                 f"inSelect={inp_info.get('inSelect')}, "
                                 f"placeholder={inp_info.get('placeholder')}")
            return fields
        elif isinstance(result, list):
            return result
        return []

    # 主页面
    try:
        result = await page.evaluate(scan_js)
        fields = _extract_fields(result)
        all_fields.extend(fields)
    except Exception as e:
        LOG.debug(f"主页面表单扫描失败: {e}")

    # iframe 遍历
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        try:
            result = await frame.evaluate(scan_js)
            fields = _extract_fields(result)
            all_fields.extend(fields)
        except Exception:
            pass

    return all_fields


async def _scan_page_buttons_for_create(page) -> list:
    """扫描创建页面/表单页面的操作按钮（确定/取消/保存等）。

    用于页面跳转模式（非弹窗）下的按钮发现。
    只匹配常见操作关键词，避免扫描到无关按钮。

    Returns:
        list: 按钮列表，每项含 location='dialog'（复用 dialog 位置标记）
    """
    try:
        elements = await page.evaluate("""() => {
            const results = [];
            // 扩展关键词：包括常见操作 + 创建相关
            const keywords = [
                '确定', '取消', '保存', '提交', '返回', '重置',
                '确认', '下一步', '上一步', '完成',
                '创建', '新建', '添加', '立即', '确认创建', '确认添加'
            ];
            const all = document.querySelectorAll('button, .el-button, [role="button"], a[href]');
            const seen = new Set();
            const allButtons = [];  // 用于调试
            for (const el of all) {
                const text = (el.textContent || '').trim();
                if (!text || text.length > 30) continue;
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                allButtons.push({text: text, tag: el.tagName, visible: r.width > 0, rect: {w: r.width, h: r.height}});
                const key = text + '|' + el.tagName;
                if (seen.has(key)) continue;
                seen.add(key);
                // 过滤：只保留文本匹配操作关键词的按钮
                // 注意：按钮文本可能有空格（如 "取 消" → "取消"），需先去除空格再匹配
                const textNormalized = text.replace(/\s+/g, '');
                const match = keywords.some(kw => textNormalized.includes(kw));
                if (!match) continue;
                // 存储时使用去除空格后的文本，确保后续 action 匹配能正常工作
                results.push({
                    text: textNormalized,
                    tag: el.tagName.toLowerCase(),
                    className: (el.className || '').slice(0, 80),
                    rect: {x: r.x, y: r.y, width: r.width, height: r.height},
                    location: 'dialog',
                    source: 'create_page_scan'
                });
            }
            return {buttons: results, allButtons: allButtons};
        }""")

        # 提取实际按钮列表和调试信息
        buttons = elements.get('buttons', []) if isinstance(elements, dict) else []
        all_buttons = elements.get('allButtons', []) if isinstance(elements, dict) else []

        if not buttons and all_buttons:
            LOG.info(f"  页面按钮扫描：找到 {len(all_buttons)} 个可见按钮，但没有匹配关键词的")
            LOG.info(f"  所有可见按钮: {all_buttons[:10]}")

        return buttons
    except Exception as e:
        LOG.debug(f"创建页按钮扫描失败: {e}")
        return []


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


async def _detect_ui_framework(page) -> str:
    """检测页面使用的 UI 框架。

    通过检查页面中是否存在特定框架的类名来判断。

    Returns:
        str: "element-ui" 或 "ant-design"
    """
    try:
        framework = await page.evaluate("""() => {
            // 检查 Ant Design 特征
            if (document.querySelector('.ant-layout, .ant-btn, .ant-table')) {
                return 'ant-design';
            }
            // 检查 Element UI 特征
            if (document.querySelector('.el-table, .el-button, .el-menu')) {
                return 'element-ui';
            }
            // 默认返回 element-ui
            return 'element-ui';
        }""")
        LOG.info(f"检测到 UI 框架: {framework}")
        return framework
    except Exception as e:
        LOG.debug(f"UI 框架检测失败: {e}，默认使用 element-ui")
        return "element-ui"


async def _kb_enrichment_scan(page, existing: list, kb: ProbeKB, framework: str = "element-ui") -> list:
    """KB 引导的按钮快速定位（优化版：单次 evaluate）。

    使用 probe_knowledge.json 中的 XPath 模板进行按钮探测，
    特别是处理中文按钮文本中的空格问题（如"确 定"）。

    优化：将所有 keyword × pattern 组合合并为一次 evaluate 调用，
    避免 N×M 次 JS 注入开销。

    Args:
        page: Playwright 页面对象
        existing: 已探测到的候选元素列表
        kb: ProbeKB 知识库实例
        framework: UI 框架名称

    Returns:
        list: 通过 KB 探测到的额外元素列表
    """
    if not kb or not kb._kb_data:
        return []

    # 收集已存在按钮的文本，用于去重
    existing_texts = {item.get("text", "") for item in existing}

    # 从 ACTION_KEYWORDS 获取所有按钮关键词
    keywords = set()
    for action_keywords in const.ACTION_KEYWORDS.values():
        keywords.update(action_keywords)

    # 使用 KB 中的 button 模板进行探测
    button_patterns = kb.get_patterns("button", framework)
    if not button_patterns:
        return []

    # 构建所有 XPath 查询（Python 侧展开占位符）
    xpath_list = []
    for keyword in keywords:
        for pattern in button_patterns:
            xpath = pattern.replace("{label}", keyword)
            # 处理 {chars_all} 占位符
            if "{chars_all}" in xpath:
                chars = list(keyword)
                chars_all = " and ".join([f"contains(., '{c}')" for c in chars])
                xpath = xpath.replace("{chars_all}", chars_all)
            # 处理 {char1} 和 {char2}
            if "{char1}" in xpath and len(keyword) > 0:
                xpath = xpath.replace("{char1}", keyword[0])
            if "{char2}" in xpath and len(keyword) > 1:
                xpath = xpath.replace("{char2}", keyword[1])
            xpath_list.append(xpath)

    if not xpath_list:
        return []

    # 单次 evaluate：批量执行所有 XPath
    import json
    xpath_json = json.dumps(xpath_list)

    try:
        elements = await page.evaluate(f"""() => {{
            const xpaths = {xpath_json};
            const results = [];
            const seen = new Set();

            for (const xpath of xpaths) {{
                try {{
                    const result = document.evaluate(
                        xpath, document, null,
                        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
                    );
                    for (let i = 0; i < result.snapshotLength; i++) {{
                        const el = result.snapshotItem(i);
                        if (el && el.offsetWidth > 0 && el.offsetHeight > 0) {{
                            const text = (el.textContent || '').trim();
                            if (!text || seen.has(text)) continue;
                            seen.add(text);
                            const rect = el.getBoundingClientRect();
                            results.push({{
                                text,
                                tag: el.tagName.toLowerCase(),
                                className: el.className || '',
                                rect: {{
                                    x: rect.x, y: rect.y,
                                    width: rect.width, height: rect.height
                                }},
                                location: 'toolbar',
                                source: 'kb_scan'
                            }});
                        }}
                    }}
                }} catch (e) {{
                    // 忽略单个 XPath 错误，继续下一个
                }}
            }}
            return results;
        }}""")

        # 去重过滤
        kb_found = []
        for el in elements:
            text = el.get("text", "")
            if not text or text in existing_texts:
                continue
            existing_texts.add(text)
            kb_found.append(el)

        if kb_found:
            LOG.info(f"KB 增强扫描发现 {len(kb_found)} 个新按钮")

        return kb_found

    except Exception as e:
        LOG.warning(f"KB 批量扫描失败: {e}")
        return []


def _build_button_labels(result: dict) -> dict:
    """从已探测到的按钮构建 action -> label 映射。

    遍历所有探测到的按钮，根据按钮文本推断其对应的 action，
    构建动态标签映射，用于后续的 manifest 生成。

    Args:
        result: 探测结果字典，包含 toolbar_buttons, row_actions, dropdowns 等

    Returns:
        dict: {action: label} 映射，如 {"create": "新增", "delete": "删除"}
    """
    labels = {}

    # 收集所有按钮
    all_buttons = []
    all_buttons.extend(result.get("toolbar_buttons", []))
    all_buttons.extend(result.get("row_actions", []))
    all_buttons.extend(result.get("dropdowns", []))

    # 遍历按钮，推断 action
    for btn in all_buttons:
        text = btn.get("text", "")
        if not text:
            continue

        # 使用 ACTION_KEYWORDS 匹配 action
        for action, keywords in const.ACTION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    # 只记录第一个匹配的按钮作为该 action 的标签
                    if action not in labels:
                        labels[action] = text
                    break
            if action in labels and labels[action] == text:
                break

    return labels


async def _scan_hints(page, missing_elements: list) -> list:
    """基于反馈循环的 hints 定向扫描缺失按钮。

    对 missing_elements 中类型为 button 的元素，使用 ACTION_KEYWORDS
    中的关键词在页面中搜索可见的匹配元素。
    包括主页面和 iframe 扫描。

    Args:
        page: Playwright 页面对象
        missing_elements: 缺失元素列表，每项包含 type/action/location 等字段

    Returns:
        list: 找到的按钮元素列表
    """
    found = []
    selectors = const.BUTTON_SELECTORS_STR

    for elem in missing_elements:
        # 只处理 button 类型
        if elem.get("type") != "button":
            continue

        action = elem.get("action", "")
        keywords = const.ACTION_KEYWORDS.get(action, [])

        if not keywords:
            LOG.debug(f"  hints 扫描: action '{action}' 无关键词")
            continue

        # 尝试每个关键词
        for kw in keywords:
            try:
                elements = await page.evaluate(f"""() => {{
                    const results = [];
                    const selectors = '{selectors}';
                    const all = document.querySelectorAll(selectors);
                    for (const el of all) {{
                        const text = (el.textContent || '').trim();
                        if (text.includes('{kw}') &&
                            el.offsetWidth > 0 && el.offsetHeight > 0) {{
                            const rect = el.getBoundingClientRect();
                            const dialog = el.closest('.el-dialog, .ant-modal, .el-drawer');
                            const table = el.closest('.el-table, table, .ant-table');
                            let location = 'toolbar';
                            if (dialog && dialog.contains(el)) location = 'dialog';
                            else if (table && table.contains(el)) location = 'row_action';
                            results.push({{
                                text,
                                tag: el.tagName.toLowerCase(),
                                className: el.className || '',
                                rect: {{x: rect.x, y: rect.y, width: rect.width, height: rect.height}},
                                location,
                                source: 'hint_scan'
                            }});
                        }}
                    }}
                    return results;
                }}""")

                if elements:
                    found.extend(elements)
                    LOG.info(f"  hints 扫描: 关键词 '{kw}' 找到 {len(elements)} 个元素")
                    break  # 找到后不再尝试其他关键词

            except Exception as e:
                LOG.debug(f"  hints 扫描关键词 '{kw}' 失败: {e}")
                continue

    return found


async def _scan_iframes(page, keywords: list = None) -> list:
    """在 iframe 中扫描按钮。

    遍历所有非主 frame，扫描所有可见的按钮元素。
    如果提供 keywords 则按关键词过滤（用于 hints 扫描），
    否则扫描所有可见按钮（用于标准探测）。

    Args:
        page: Playwright Page 对象
        keywords: 搜索关键词列表（可选，为空时扫描所有按钮）

    Returns:
        list: 找到的按钮元素列表
    """
    # 如果没有 iframe，直接返回
    if len(page.frames) <= 1:
        return []

    found = []
    selectors = const.BUTTON_SELECTORS_STR

    for frame in page.frames:
        if frame == page.main_frame:
            continue

        try:
            # 扫描所有可见按钮（或按关键词过滤）
            if keywords:
                # 关键词模式：只扫描匹配关键词的按钮
                for kw in keywords:
                    elements = await frame.evaluate(f"""() => {{
                        const results = [];
                        const all = document.querySelectorAll('{selectors}');
                        for (const el of all) {{
                            const text = (el.textContent || '').trim().replace(/\\s+/g, '');
                            if (text.includes('{kw}') &&
                                el.offsetWidth > 0 && el.offsetHeight > 0) {{
                                const table = el.closest('.el-table, table, .ant-table');
                                const dialog = el.closest('.el-dialog, .ant-modal, .el-drawer');
                                let location = 'toolbar';
                                if (dialog && dialog.contains(el)) location = 'dialog';
                                else if (table && table.contains(el)) location = 'row_action';
                                results.push({{
                                    text: (el.textContent || '').trim(),
                                    tag: el.tagName.toLowerCase(),
                                    className: (el.className || '').slice(0, 80),
                                    location,
                                    source: 'iframe_scan'
                                }});
                            }}
                        }}
                        return results;
                    }}""")
                    if elements:
                        found.extend(elements)
                        LOG.debug(f"  iframe 扫描: 关键词 '{kw}' 找到 {len(elements)} 个元素")
                        break
            else:
                # 全量模式：扫描所有可见按钮
                elements = await frame.evaluate(f"""() => {{
                    const results = [];
                    const selectors = '{selectors}';
                    const all = document.querySelectorAll(selectors);
                    const seen = new Set();
                    for (const el of all) {{
                        const text = (el.textContent || '').trim();
                        if (!text || text.length > 40 || text.length < 1) continue;
                        if (el.offsetWidth <= 0 || el.offsetHeight <= 0) continue;
                        const key = text + '|' + el.tagName;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        const table = el.closest('.el-table, table, .ant-table');
                        const dialog = el.closest('.el-dialog, .ant-modal, .el-drawer');
                        let location = 'toolbar';
                        if (dialog && dialog.contains(el)) location = 'dialog';
                        else if (table && table.contains(el)) location = 'row_action';
                        results.push({{
                            text,
                            tag: el.tagName.toLowerCase(),
                            className: (el.className || '').slice(0, 80),
                            rect: Math.round(el.getBoundingClientRect().width) + 'x' + Math.round(el.getBoundingClientRect().height),
                            location,
                            source: 'iframe_scan'
                        }});
                    }}
                    return results;
                }}""")
                if elements:
                    found.extend(elements)
                    LOG.debug(f"  iframe 全量扫描找到 {len(elements)} 个元素")

        except Exception as e:
            LOG.debug(f"  iframe 扫描失败: {e}")
            continue

        # 关键词模式下找到元素就停止
        if keywords and found:
            break

    return found

    return found


async def _check_precondition_state(page, expected_state: dict) -> dict:
    """检查前置操作是否成功（纯 DOM，零成本）。

    检查预期的页面状态（弹窗/抽屉/页面变化）是否已出现。

    Args:
        page: Playwright Page 对象
        expected_state: 预期状态，如 {"type": "dialog", "title": "添加用户"}

    Returns:
        {"success": bool, "actual_state": str}
    """
    try:
        state = await page.evaluate("""() => {
            // 检查弹窗/抽屉
            const dialogs = document.querySelectorAll(
                '.el-dialog__wrapper, .el-drawer, .ant-modal-wrap');
            const visible = Array.from(dialogs).filter(
                d => d.style.display !== 'none' && d.offsetWidth > 0);

            let dialogTitle = '';
            let dialogType = '';
            if (visible.length > 0) {
                const d = visible[0];
                const titleEl = d.querySelector(
                    '.el-dialog__title, .el-drawer__header, .ant-modal-title');
                dialogTitle = titleEl ? titleEl.textContent.trim() : '';
                dialogType = d.classList.contains('el-drawer') ? 'drawer' : 'dialog';
            }

            // 检查确认框（MessageBox / Popconfirm）
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            const hasMsgBox = msgBox && msgBox.offsetWidth > 0;
            if (hasMsgBox && !dialogType) {
                dialogType = 'message-box';
                dialogTitle = '确认操作';
            }

            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            const hasPopconfirm = popconfirm && popconfirm.offsetWidth > 0;
            if (hasPopconfirm && !dialogType) {
                dialogType = 'popconfirm';
                dialogTitle = '确认操作';
            }

            // 检查错误提示
            const errorMsgs = [];
            document.querySelectorAll('.el-message--error, .el-notification--error').forEach(el => {
                const text = el.textContent.trim();
                if (text) errorMsgs.push(text);
            });

            // 检查加载状态
            const isLoading = !!document.querySelector(
                '.el-loading-mask:not([style*="display: none"]), .ant-spin-spinning');

            return {
                hasDialog: visible.length > 0 || hasMsgBox || hasPopconfirm,
                dialogTitle,
                dialogType,
                dialogCount: visible.length + (hasMsgBox ? 1 : 0) + (hasPopconfirm ? 1 : 0),
                errors: errorMsgs,
                isLoading,
                url: window.location.href,
            };
        }""")

        expected_type = expected_state.get("type", "dialog")
        expected_title = expected_state.get("title", "")

        # 判断是否成功
        if expected_type in ("dialog", "drawer"):
            success = state.get("hasDialog", False)
            if success and expected_title:
                # 标题模糊匹配
                actual_title = state.get("dialogTitle", "")
                success = (expected_title in actual_title) or (actual_title in expected_title)
        else:
            success = True  # 无特定期望

        actual_state = ""
        if state.get("hasDialog"):
            actual_state = f"{state['dialogType']}: {state['dialogTitle']}"
        elif state.get("errors"):
            actual_state = f"错误提示: {', '.join(state['errors'][:2])}"
        elif state.get("isLoading"):
            actual_state = "页面加载中"
        else:
            actual_state = "无变化"

        return {"success": success, "actual_state": actual_state, "raw": state}

    except Exception as e:
        LOG.debug(f"  前置操作状态检查失败: {e}")
        return {"success": False, "actual_state": f"检查失败: {e}", "raw": {}}


async def _retry_precondition(page, trigger_btn: dict, max_retries: int = 3) -> bool:
    """前置操作失败时，换方式重试点击。

    尝试 3 种点击方式：
    1. Playwright click({force: true}) — 绕过遮挡
    2. JS evaluate el.click() — 原生点击
    3. 坐标点击 page.mouse.click(x, y) — 模拟真实鼠标

    每次尝试后检查弹窗是否出现。

    Args:
        page: Playwright Page 对象
        trigger_btn: 触发按钮信息 {"text": str, "tag": str, "className": str}
        max_retries: 最大重试次数

    Returns:
        bool: 重试后弹窗是否成功出现
    """
    btn_text = trigger_btn.get("text", "")
    if not btn_text:
        return False

    LOG.info(f"  前置操作重试: 尝试点击 '{btn_text}'")

    for attempt in range(1, max_retries + 1):
        clicked = False

        try:
            if attempt == 1:
                # 方式 1: Playwright force click
                LOG.debug(f"    尝试方式 1: force click")
                selector = f"button:has-text('{btn_text}'), span:has-text('{btn_text}')"
                await page.click(selector, force=True, timeout=3000)
                clicked = True

            elif attempt == 2:
                # 方式 2: JS 原生点击
                LOG.debug(f"    尝试方式 2: JS click")
                clicked = await page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button, span, a, .el-button');
                    for (const el of all) {{
                        if ((el.textContent || '').trim() === '{btn_text}' && el.offsetWidth > 0) {{
                            el.click(); return true;
                        }}
                    }}
                    return false;
                }}""")

            elif attempt == 3:
                # 方式 3: 坐标点击
                LOG.debug(f"    尝试方式 3: 坐标点击")
                rect = await page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button, span, a, .el-button');
                    for (const el of all) {{
                        if ((el.textContent || '').trim() === '{btn_text}' && el.offsetWidth > 0) {{
                            const r = el.getBoundingClientRect();
                            return {{x: r.x + r.width/2, y: r.y + r.height/2}};
                        }}
                    }}
                    return null;
                }}""")
                if rect:
                    await page.mouse.click(rect["x"], rect["y"])
                    clicked = True

        except Exception as e:
            LOG.debug(f"    方式 {attempt} 点击失败: {e}")
            continue

        if not clicked:
            continue

        # 等待弹窗出现
        await page.wait_for_timeout(1500)

        # 检查弹窗状态
        state = await _check_precondition_state(page, {"type": "dialog"})
        if state["success"]:
            LOG.info(f"    ✅ 方式 {attempt} 成功: {state['actual_state']}")
            return True
        else:
            LOG.debug(f"    方式 {attempt} 未触发弹窗: {state['actual_state']}")

    LOG.warning(f"  ❌ 所有 {max_retries} 种点击方式均未触发弹窗")
    return False


async def _scan_dialog_buttons(page) -> list:
    """只扫描当前打开的弹窗/抽屉内的按钮（Phase D 专用）。

    不做全量 _discover_once，只定位弹窗内的可见按钮。
    用于前置操作成功后快速发现弹窗内元素（如 confirm 按钮）。

    Args:
        page: Playwright Page 对象

    Returns:
        list: 弹窗内的按钮列表，每项含 location='dialog'
    """
    try:
        # 先诊断日志：检查弹窗容器状态
        diag = await page.evaluate("""() => {
            const wrappers = document.querySelectorAll('.el-dialog__wrapper');
            const drawers = document.querySelectorAll('.el-drawer');
            return {
                wrappers: Array.from(wrappers).map(w => ({
                    display: w.style.display,
                    offsetW: w.offsetWidth,
                    offsetH: w.offsetHeight,
                    hasDialog: !!w.querySelector('.el-dialog'),
                    btnCount: w.querySelectorAll('button, .el-button').length
                })),
                drawers: Array.from(drawers).map(d => ({
                    display: d.style.display,
                    offsetW: d.offsetWidth,
                    hasClassVisible: d.classList.contains('el-drawer__open'),
                    btnCount: d.querySelectorAll('button, .el-button').length
                }))
            };
        }""")
        LOG.debug(f"  弹窗诊断: wrappers={diag['wrappers']}, drawers={diag['drawers']}")

        elements = await page.evaluate("""() => {
            const results = [];
            // 定位可见的弹窗/抽屉
            // Element UI 的 .el-dialog__wrapper 可见时：
            // - 可能没有 inline style (style.display === '')
            // - 或者有 style="display: block"
            // 隐藏时: style="display: none"
            const containers = document.querySelectorAll(
                '.el-dialog__wrapper, .el-drawer, .ant-modal-wrap');
            const visible = Array.from(containers).filter(d => {
                // 检查 inline style
                if (d.style.display === 'none') return false;
                // 检查实际尺寸（更可靠）
                if (d.offsetWidth > 0 && d.offsetHeight > 0) return true;
                // 对于 el-drawer，检查 class
                if (d.classList.contains('el-drawer') &&
                    (d.classList.contains('el-drawer__open') || d.classList.contains('is-visible'))) {
                    return true;
                }
                return false;
            });

            if (visible.length === 0) return results;

            for (const container of visible) {
                const buttons = container.querySelectorAll(
                    'button, .el-button, [role="button"], a[href]');
                const seen = new Set();
                for (const el of buttons) {
                    const text = (el.textContent || '').trim();
                    if (!text || text.length > 40 || text.length < 1) continue;
                    if (el.offsetWidth <= 0 || el.offsetHeight <= 0) continue;
                    const key = text + '|' + el.tagName;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    const rect = el.getBoundingClientRect();
                    results.push({
                        text,
                        tag: el.tagName.toLowerCase(),
                        className: (el.className || '').slice(0, 80),
                        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                        location: 'dialog',
                        source: 'dialog_scan'
                    });
                }
            }
            return results;
        }""")
        if elements:
            LOG.info(f"  弹窗扫描发现 {len(elements)} 个按钮")
        else:
            LOG.warning(f"  弹窗扫描未发现按钮 (visible containers: {len(diag['wrappers']) + len(diag['drawers'])})")
        return elements or []
    except Exception as e:
        LOG.debug(f"  弹窗扫描失败: {e}")
        return []


# ============================================================
# 从 run.py 迁入：UI 清理与 SPA 就绪等待
# ============================================================

async def cleanup_ui_overlays(page):
    """清理页面上的模态对话框等 UI 残留。

    在 Stage 1 UI 探测前调用。
    只关闭**模态**对话框（el-dialog/el-message-box/el-drawer），
    不关闭内嵌的配置面板。
    """
    try:
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(300)

        try:
            await page.evaluate("""() => {
                // 只处理有遮罩层的对话框
                const modals = document.querySelectorAll('.v-modal');
                modals.forEach(modal => {
                    const wrapper = modal.nextElementSibling;
                    if (wrapper && (wrapper.classList.contains('el-dialog__wrapper') ||
                                    wrapper.classList.contains('el-drawer__wrapper'))) {
                        const closeBtn = wrapper.querySelector('.el-dialog__headerbtn, .el-drawer__close-btn');
                        const cancelBtn = wrapper.querySelector('.el-dialog__footer button:not(.el-button--primary)');
                        if (closeBtn) closeBtn.click();
                        else if (cancelBtn) cancelBtn.click();
                    }
                });

                // 关闭 el-message-box（确认弹窗）
                document.querySelectorAll('.el-message-box__wrapper').forEach(box => {
                    if (box.style.display !== 'none') {
                        const cancelBtn = box.querySelector('.el-message-box__btns button:first-child');
                        if (cancelBtn) cancelBtn.click();
                    }
                });

                // 移除遮罩层
                document.querySelectorAll('.v-modal').forEach(el => el.remove());
            }""")
        except Exception as e:
            LOG.warning(f"关闭模态对话框失败: {e}")

        await page.wait_for_timeout(500)

    except Exception as e:
        LOG.warning(f"清理 UI 残留时出错（不影响后续流程）: {e}")


async def wait_for_spa_ready(page, max_rounds: int = 6) -> bool:
    """等待 SPA 渲染就绪（表格/创建按钮出现）。

    从 run.py 迁入。轮询检查 .el-table 和数据行/创建按钮。

    Args:
        page: Playwright Page 对象
        max_rounds: 最大轮询次数（每轮 2 秒）

    Returns:
        bool: 是否在超时前就绪
    """
    LOG.info("  等待 SPA 渲染完成...")
    for wait_round in range(max_rounds):
        state = await page.evaluate("""() => {
            const table = document.querySelector('.el-table');
            const rows = table ? table.querySelectorAll('.el-table__body-wrapper tbody tr') : [];
            const hasCreateBtn = !!Array.from(document.querySelectorAll('button, .el-button'))
                .find(b => {
                    const r = b.getBoundingClientRect();
                    const text = (b.textContent || '').trim();
                    return r.width > 0 && r.height > 0 &&
                           (text.includes('创建') || text.includes('新建') || text.includes('新增'));
                });
            return {
                hasTable: !!table,
                rowCount: rows.length,
                hasCreateBtn: hasCreateBtn,
                url: window.location.href,
            };
        }""")
        LOG.info(f"  [{wait_round+1}/{max_rounds}] table={state['hasTable']} rows={state['rowCount']} "
                 f"createBtn={state['hasCreateBtn']} url={state['url'][:60]}")

        if state['hasTable'] and (state['rowCount'] > 0 or state['hasCreateBtn']):
            LOG.info("  SPA 渲染完成")
            return True

        # 被重定向到登录页
        if "/login" in state['url']:
            LOG.error("  被重定向到登录页，cookie 已失效")
            return False

        await page.wait_for_timeout(2000)

    LOG.warning("  SPA 渲染等待超时，继续尝试")
    return False


# ============================================================
# Stage 1 业务闭环验证
# ============================================================

async def discover_and_validate(page, username: str = "test") -> dict:
    """Stage 1 入口：探测 + 业务闭环验证。

    流程：
    1. discover_all() — 探测所有按钮/字段
    2. _validate_business_flow() — 按 CRUD 顺序逐一验证每个按钮
    3. 返回增强后的 ui_result（含 validated_operations）

    Args:
        page: Playwright Page 对象
        username: 用于生成测试数据的用户名

    Returns:
        dict: 增强后的 ui_result，含 validated_operations 字段。
              如果关键操作验证失败，返回 None。
    """
    # Phase A: 标准探测
    ui_result = await discover_all(page)

    # Phase B: 业务闭环验证
    validated = await _validate_business_flow(page, ui_result, username)
    if validated is None:
        # 关键操作验证失败
        return None

    ui_result["validated_operations"] = validated
    return ui_result


async def _validate_business_flow(page, ui_result: dict, username: str) -> dict:
    """按 CRUD_EXECUTION_ORDER 逐一验证按钮的业务闭环。

    每个按钮执行完整流程：点击 → 填表(如适用) → 提交 → 验证成功。
    验证失败的按钮进入错误驱动重试循环。

    Args:
        page: Playwright Page 对象
        ui_result: discover_all() 返回的探测结果
        username: 用户名

    Returns:
        dict: {action: {"success": bool, "fill_data": dict, "selectors": dict, ...}}
              如果关键操作全部失败，返回 None。
    """
    validated = {}
    form_filler = FormFiller(page)
    framework = ui_result.get("framework", "element-ui")

    # 收集所有按钮，按 action 分组（包括 dropdowns）
    all_buttons = (
        ui_result.get("toolbar_buttons", [])
        + ui_result.get("row_actions", [])
        + ui_result.get("dropdowns", [])  # ← 补上 dropdowns 验证缺口
    )
    buttons_by_action = {}
    _DROPDOWN_TRIGGER_TEXTS = {"更多", "操作", "Actions", "More", "批量操作"}
    for btn in all_buttons:
        # 跳过下拉菜单触发器（如"更多"），它们只是展开菜单，不是实际操作
        if btn.get("text") in _DROPDOWN_TRIGGER_TEXTS and btn.get("tag") != "DROPDOWN_ITEM":
            continue
        action = btn.get("action", "") or _match_crud(btn.get("text", ""))
        if action and action not in buttons_by_action:
            buttons_by_action[action] = btn

    # 表单字段（从探测结果获取）
    form_fields = ui_result.get("form_fields", [])

    # 记录创建的数据标识（供后续操作复用）
    created_marker = None

    for action in const.CRUD_EXECUTION_ORDER:
        btn = buttons_by_action.get(action)
        if not btn:
            LOG.debug(f"  跳过 {action}: 未找到对应按钮")
            continue

        btn_text = btn.get("text", "")
        LOG.info(f"  验证操作: {action} (按钮: {btn_text})")

        # 分发到对应的验证函数
        if action == "create":
            result = await _error_driven_retry(
                page, _do_create, {
                    "btn": btn, "fields": form_fields, "username": username,
                    "form_filler": form_filler, "framework": framework,
                }
            )
            if result and result.get("success"):
                created_marker = result.get("marker")
                validated[action] = result
            else:
                # Fix 2: create 失败时从表格提取 fallback marker
                fallback = await _extract_fallback_marker(page)
                if fallback:
                    created_marker = fallback
                    LOG.info(f"  create 失败，使用 fallback marker: {fallback}")

        elif action == "query":
            result = await _error_driven_retry(
                page, _do_query, {"btn": btn}
            )
            if result and result.get("success"):
                validated[action] = result

        elif action == "detail":
            result = await _error_driven_retry(
                page, _do_detail, {
                    "btn": btn, "marker": created_marker,
                }
            )
            if result and result.get("success"):
                validated[action] = result

        elif action == "update":
            result = await _error_driven_retry(
                page, _do_edit, {
                    "btn": btn, "marker": created_marker,
                    "form_filler": form_filler, "framework": framework,
                }
            )
            if result and result.get("success"):
                validated[action] = result

        elif action == "delete":
            result = await _error_driven_retry(
                page, _do_delete, {
                    "btn": btn, "marker": created_marker,
                }
            )
            if result and result.get("success"):
                validated[action] = result
                created_marker = None  # 已删除

        elif action in ("lock", "unlock", "reset", "authorize", "migrate",
                        "export", "import", "batch", "approve", "execute"):
            # Fix 3: toolbar 行操作前先确保有行被勾选
            btn_location = btn.get("location", "toolbar")
            if btn_location == "toolbar" and action in ("lock", "unlock", "reset"):
                selected = await _ensure_row_selected(page, created_marker)
                if selected:
                    LOG.debug(f"    已勾选表格行供 toolbar 操作使用")

            result = await _error_driven_retry(
                page, _do_generic_operation, {
                    "btn": btn, "action": action, "marker": created_marker,
                }
            )
            if result and result.get("success"):
                validated[action] = result

        # 操作后确保回到列表页
        await _ensure_on_list_page(page)

    # 判断是否有关键操作失败
    critical_actions = {"create", "delete"}
    failed_critical = [a for a in critical_actions
                       if a in buttons_by_action and a not in validated]
    if failed_critical:
        LOG.error(f"  Stage 1 关键操作验证失败: {failed_critical}")
        return None

    LOG.info(f"  Stage 1 验证完成: {len(validated)}/{len(buttons_by_action)} 个操作通过")
    return validated


# ============================================================
# 错误驱动重试循环
# ============================================================

async def _error_driven_retry(page, operation_fn, context: dict) -> dict:
    """错误驱动重试循环。

    执行操作 → 检查结果 → 失败时读取错误信息 → 针对性修改 → 重试。
    同页面状态 Vision 截图只分析 1 次。

    Args:
        page: Playwright Page 对象
        operation_fn: 执行操作的异步函数
        context: 操作上下文

    Returns:
        dict: 操作结果（含 success, fill_data 等）或 None
    """
    last_error_key = None
    vision_used_for_state = set()  # 已截图的页面状态 key
    max_rounds = 10  # 安全上限

    for round_num in range(max_rounds):
        try:
            result = await operation_fn(page, context)
        except Exception as e:
            LOG.warning(f"    操作异常: {e}")
            result = {"success": False, "error_type": "exception",
                      "error_text": str(e)}

        if result.get("success"):
            if round_num > 0:
                LOG.info(f"    第 {round_num + 1} 轮尝试成功")
            return result

        # 提取错误信息
        error_type = result.get("error_type", "unknown")
        error_text = result.get("error_text", "")
        error_field = result.get("error_field", "")
        current_error_key = f"{error_type}|{error_field}|{error_text}"

        LOG.info(f"    第 {round_num + 1} 轮失败: [{error_type}] {error_text[:80]}")

        if current_error_key == last_error_key:
            # 卡住了：错误信息无变化
            page_state_key = await _get_page_state_key(page)
            if page_state_key not in vision_used_for_state:
                # Vision 截图分析（同页面状态只 1 次）
                LOG.info(f"    错误无变化，调用 Vision 分析...")
                vision_hints = await _vision_analysis(page, context)
                vision_used_for_state.add(page_state_key)
                if vision_hints:
                    context["vision_hints"] = vision_hints
                    last_error_key = None  # 重置，让下一轮视为"新"信息
                    continue
            # Vision 也用过了或无新 insights
            LOG.warning(f"    所有补救策略已尝试，操作 {context.get('btn', {}).get('text', '?')} 验证失败")
            return None
        else:
            last_error_key = current_error_key

        # 基于错误信息做针对性修改
        context = _apply_fix(result, context)

    LOG.warning(f"    超过最大重试次数 ({max_rounds})")
    return None


# ============================================================
# 各操作的执行函数
# ============================================================

async def _do_create(page, context: dict) -> dict:
    """执行创建操作：点击创建按钮 → 填表 → 提交 → 验证。"""
    btn = context["btn"]
    fields = context.get("fields", [])
    form_filler = context["form_filler"]
    framework = context.get("framework", "element-ui")
    username = context.get("username", "test")

    btn_text = btn.get("text", "")

    # 1. 点击创建按钮
    clicked = await _click_button_escalating(page, btn_text)
    if not clicked:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击按钮: {btn_text}"}

    # 2. 等待弹窗/页面就绪
    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 3. 扫描表单字段（如果探测阶段没扫到或需要刷新）
    if not fields:
        fields = await form_filler.scan_form_fields_v2()
    if not fields:
        await _close_dialog(page)
        return {"success": False, "error_type": "no_fields",
                "error_text": "未扫描到表单字段"}

    # 4. 生成填充规则和数据
    fill_rules = generate_fill_rules(fields)
    fill_data = generate_fill_data(fields, username)
    # 应用之前的修复
    if context.get("fill_overrides"):
        fill_data.update(context["fill_overrides"])

    # 5. 填充表单（传入预生成的 fill_data）
    filled = await form_filler.fill_create_form(fields, username, fill_data)
    # 重新扫描以获取最新字段状态
    if context.get("fill_overrides"):
        for label, value in context["fill_overrides"].items():
            try:
                # 深度扫描：识别复合组件结构，精确定位目标 input
                deep_info = await _deep_scan_form_item(page, label)
                if deep_info.get("is_composite"):
                    # 复合组件：取最后一个可见 input（避开 el-select 内部的）
                    input_count = sum(1 for e in deep_info["elements"] if e["type"] == "input")
                    if input_count > 0:
                        # 使用 nth-of-type 选择最后一个 input
                        css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) input:not([readonly]):nth-of-type({input_count})'
                        await page.fill(css, value, timeout=3000)
                        LOG.info(f"    复合组件填充: {label} → {value[:20]}...")
                        continue

                # 非复合组件：使用通用选择器
                css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) input:not([readonly])'
                await page.fill(css, value, timeout=3000)
            except Exception as e:
                LOG.debug(f"    填充 override {label} 失败: {e}")

    # 6. 处理多步组件
    ms_filled = await form_filler.fill_multi_step_fields(fields, framework)
    if ms_filled == 0:
        # 兜底：尝试 select_dropdowns()
        select_count = await form_filler.select_dropdowns()
        if select_count > 0:
            LOG.info(f"    兜底 select_dropdowns: 选择了 {select_count} 个下拉框")

    # 7. 提交
    submit_result = await form_filler.submit_form_v2()
    if submit_result == "not_found":
        await _close_dialog(page)
        return {"success": False, "error_type": "submit_failed",
                "error_text": "未找到提交按钮"}

    # 8. 等待结果
    await page.wait_for_timeout(2000)

    # 9. 检查表单校验错误
    errors = await read_form_errors(page)
    if errors:
        field_errors = [e for e in errors if e.get("severity") == "field"]
        if field_errors:
            first_err = field_errors[0]
            return {"success": False, "error_type": "form_validation",
                    "error_field": first_err.get("field_label", ""),
                    "error_text": first_err.get("error_text", "")}
        global_errors = [e for e in errors if e.get("severity") == "global"]
        if global_errors:
            err_text = global_errors[0].get("error_text", "")
            # 过滤掉成功消息（"成功" 不应被视为错误）
            if "成功" in err_text:
                LOG.info(f"    检测到成功消息: {err_text}，忽略")
            else:
                return {"success": False, "error_type": "api_error",
                        "error_text": err_text}

    # 10. 验证成功（弹窗关闭 / 成功提示）
    success = await _verify_operation_success(page, "create")
    if not success:
        return {"success": False, "error_type": "no_success_signal",
                "error_text": "提交后未检测到成功信号"}

    # 10.5. API 触发检测（额外确认，从 Stage 2 移入）
    api_triggered = await _check_create_api_triggered_simple(page)
    if api_triggered:
        LOG.info(f"    API 触发确认: POST 请求已检测到")

    # 11. 记录创建的标识数据
    marker = fill_data.get("名称") or fill_data.get("用户名") or fill_data.get("name") or username

    # 确保回到列表页
    await _ensure_on_list_page(page)

    return {
        "success": True,
        "fill_data": fill_data,
        "fill_rules": fill_rules,
        "form_fields": fields,  # 添加扫描到的表单字段信息
        "marker": marker,
        "selectors": {
            "trigger": btn_text,
            "submit": submit_result.replace("submitted:", ""),
        },
    }


async def _do_query(page, context: dict) -> dict:
    """执行查询操作：输入搜索条件 → 点击搜索 → 验证列表刷新。"""
    btn = context["btn"]
    btn_text = btn.get("text", "")

    # 尝试在搜索框输入
    try:
        search_input = page.locator(
            '.el-input input[placeholder*="搜索"], '
            '.el-input input[placeholder*="请输入"], '
            '.el-input input[placeholder*="关键词"]'
        ).first
        if await search_input.count() > 0:
            await search_input.fill("test")
            await page.wait_for_timeout(500)
    except Exception:
        pass

    # 点击搜索按钮
    clicked = await _click_button_escalating(page, btn_text)
    if not clicked:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击搜索按钮: {btn_text}"}

    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 验证列表刷新（简单检查：表格仍存在）
    has_table = await page.evaluate(
        "() => !!document.querySelector('.el-table__body-wrapper tbody tr')"
    )

    result = {"success": True, "selectors": {"trigger": btn_text}}
    if has_table:
        result["has_data"] = True
    return result


async def _do_detail(page, context: dict) -> dict:
    """执行查看详情操作：点击详情 → 验证打开 → 关闭。"""
    btn = context["btn"]
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # 如果是行操作，先找到对应行
    row_selector = None
    if btn_location == "row_action" and marker:
        from .button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return {"success": False, "error_type": "row_not_found",
                    "error_text": f"未找到数据行: {marker}"}
        clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    else:
        clicked = await _click_button_escalating(page, btn_text)

    if not clicked:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击详情按钮: {btn_text}"}

    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 验证详情打开（弹窗或页面跳转）
    state = await _check_precondition_state(page, {"type": "dialog"})
    url_changed = "/detail" in page.url or "/view" in page.url

    if state["success"] or url_changed:
        # 关闭详情
        await _close_dialog(page)
        if url_changed:
            await page.go_back()
            await wait_for_navigation_complete(page)
        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector
        return {"success": True, "selectors": selectors}

    return {"success": False, "error_type": "no_dialog",
            "error_text": "点击详情后未出现详情页/弹窗"}


async def _extract_fallback_marker(page) -> str | None:
    """从表格中提取第一行的名称作为 fallback marker。

    当 create 失败时，行操作可以用已存在的数据行。
    """
    try:
        marker = await page.evaluate("""() => {
            // 从表格第一行提取名称类文本（通常是第一列或第二列）
            const table = document.querySelector('.el-table__body, table tbody');
            if (!table) return null;
            const rows = table.querySelectorAll('tr');
            if (rows.length === 0) return null;
            const cells = rows[0].querySelectorAll('td .cell, td');
            for (const cell of cells) {
                const text = (cell.textContent || '').trim();
                // 跳过 checkbox 列、序号列、操作列
                if (!text || text.length > 30 || text.length < 1) continue;
                if (/^\\d+$/.test(text)) continue;
                if (cell.querySelector('.el-checkbox, .el-radio')) continue;
                if (cell.querySelector('button, .el-button')) continue;
                return text;
            }
            return null;
        }""")
        return marker
    except Exception:
        return None


async def _ensure_row_selected(page, marker: str | None = None) -> bool:
    """确保表格中至少有一行被勾选（toolbar 行操作需要）。

    某些系统要求先勾选 checkbox 才能点击工具栏的"锁定/解锁/重置"按钮。

    Args:
        page: Playwright Page 对象
        marker: 如果有 marker，勾选包含 marker 的行

    Returns:
        bool: 是否成功勾选
    """
    try:
        if marker:
            # 勾选包含 marker 的行
            checked = await page.evaluate(f"""(text) => {{
                const rows = document.querySelectorAll('.el-table__body tr');
                for (const row of rows) {{
                    if ((row.textContent || '').includes(text)) {{
                        const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                        if (cb && cb.offsetWidth > 0) {{
                            cb.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }}""", marker)
        else:
            # 勾选第一行
            checked = await page.evaluate("""() => {
                const cb = document.querySelector(
                    '.el-table__body tr:first-child .el-checkbox__input, '
                    + '.el-table__body tr:first-child input[type="checkbox"]');
                if (cb && cb.offsetWidth > 0) {
                    cb.click();
                    return true;
                }
                return false;
            }""")
        return bool(checked)
    except Exception:
        return False


async def _do_edit(page, context: dict) -> dict:
    """执行编辑操作：点击编辑 → 修改字段 → 提交 → 验证。"""
    btn = context["btn"]
    marker = context.get("marker")
    form_filler = context["form_filler"]
    framework = context.get("framework", "element-ui")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # 找到行并点击编辑（带重试）
    max_locate_retries = 2
    row_selector = None
    for attempt in range(max_locate_retries):
        if btn_location == "row_action" and marker:
            from .button_driver import ButtonDriver
            driver = ButtonDriver(page)
            row = await driver.find_data_row(marker)
            if not row:
                if attempt < max_locate_retries - 1:
                    LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_locate_retries})")
                    await page.wait_for_timeout(1000)
                    continue
                return {"success": False, "error_type": "row_not_found",
                        "error_text": f"未找到数据行: {marker}"}
            clicked = await driver.click_row_button_v2(row, btn_text)
            row_selector = btn_text
        else:
            clicked = await _click_button_escalating(page, btn_text)

        if clicked:
            break
        elif attempt < max_locate_retries - 1:
            LOG.debug(f"    点击失败，重试 ({attempt + 1}/{max_locate_retries})")
            await page.wait_for_timeout(500)
    else:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击编辑按钮: {btn_text}"}

    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 验证编辑弹窗打开
    state = await _check_precondition_state(page, {"type": "dialog"})
    if not state["success"]:
        return {"success": False, "error_type": "no_dialog",
                "error_text": "点击编辑后未出现编辑弹窗"}

    # 扫描字段并修改
    fields = await form_filler.scan_form_fields_v2()
    modifications = context.get("edit_overrides")
    filled = await form_filler.fill_edit_form(fields, modifications)
    if filled == 0:
        await _close_dialog(page)
        return {"success": False, "error_type": "no_editable_fields",
                "error_text": "未找到可修改的字段"}

    # 提交
    submit_result = await form_filler.submit_form_v2()
    if submit_result == "not_found":
        await _close_dialog(page)
        return {"success": False, "error_type": "submit_failed",
                "error_text": "未找到提交按钮"}

    await page.wait_for_timeout(2000)

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        field_errors = [e for e in errors if e.get("severity") == "field"]
        if field_errors:
            return {"success": False, "error_type": "form_validation",
                    "error_field": field_errors[0].get("field_label", ""),
                    "error_text": field_errors[0].get("error_text", "")}

    success = await _verify_operation_success(page, "update")
    if not success:
        await _close_dialog(page)
        return {"success": False, "error_type": "no_success_signal",
                "error_text": "编辑提交后未检测到成功信号"}

    # 构建 selectors
    selectors = {"trigger": btn_text, "submit": submit_result.replace("submitted:", "")}
    if row_selector:
        selectors["row_selector"] = row_selector

    return {"success": True, "edit_fill_data": {"modified_fields": filled}, "selectors": selectors}


async def _do_delete(page, context: dict) -> dict:
    """执行删除操作：点击删除 → 确认 → 验证行消失。

    支持两种场景：
    - row_action: 行内"删除"按钮，直接点击该行
    - toolbar: 工具栏"批量删除"按钮，需要先勾选 checkbox
    """
    btn = context["btn"]
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # toolbar 批量删除：先勾选 checkbox
    if btn_location == "toolbar":
        selected = await _ensure_row_selected(page, marker)
        if selected:
            LOG.debug(f"    已勾选表格行供批量删除使用")
        await page.wait_for_timeout(500)

    # 找到行并点击删除（带重试）
    max_locate_retries = 2
    row_selector = None
    for attempt in range(max_locate_retries):
        if btn_location == "row_action" and marker:
            from .button_driver import ButtonDriver
            driver = ButtonDriver(page)
            row = await driver.find_data_row(marker)
            if not row:
                if attempt < max_locate_retries - 1:
                    LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_locate_retries})")
                    await page.wait_for_timeout(1000)
                    continue
                return {"success": False, "error_type": "row_not_found",
                        "error_text": f"未找到数据行: {marker}"}
            clicked = await driver.click_row_button_v2(row, btn_text)
            row_selector = btn_text
        else:
            clicked = await _click_button_escalating(page, btn_text)

        if clicked:
            break
        elif attempt < max_locate_retries - 1:
            LOG.debug(f"    点击失败，重试 ({attempt + 1}/{max_locate_retries})")
            await page.wait_for_timeout(500)
    else:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击删除按钮: {btn_text}"}

    await page.wait_for_timeout(1000)

    # 确认删除弹窗（支持 el-message-box 和 el-popconfirm）
    from .button_driver import confirm_dialog
    confirmed = await confirm_dialog(page)
    if not confirmed:
        return {"success": False, "error_type": "no_confirm_button",
                "error_text": "未找到删除确认按钮"}

    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        global_errors = [e for e in errors if e.get("severity") == "global"]
        if global_errors:
            return {"success": False, "error_type": "api_error",
                    "error_text": global_errors[0].get("error_text", "")}

    # 验证成功（成功提示或行消失）
    success = await _verify_operation_success(page, "delete")

    # 构建 selectors
    selectors = {"trigger": btn_text, "confirm": confirmed}
    if row_selector:
        selectors["row_selector"] = row_selector

    return {"success": True, "selectors": selectors} if success else {
        "success": False, "error_type": "no_success_signal",
        "error_text": "删除后未检测到成功信号"
    }


async def _do_generic_operation(page, context: dict) -> dict:
    """执行通用操作（lock/unlock/reset/authorize 等）。"""
    btn = context["btn"]
    action = context["action"]
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # 记录点击前的 URL（用于导航保护）
    url_before = page.url

    # 找到行并点击
    row_selector = None
    if btn_location == "row_action" and marker:
        from .button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return {"success": False, "error_type": "row_not_found",
                    "error_text": f"未找到数据行: {marker}"}
        clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    elif btn_location == "dropdown" and marker:
        # 两步点击：先找到行 → 展开"更多" → 点击子项
        from .button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return {"success": False, "error_type": "row_not_found",
                    "error_text": f"未找到数据行: {marker}"}
        clicked = await driver.click_row_more_item(row, btn_text)
        row_selector = f"{btn.get('parent', '更多')} > {btn_text}"
    else:
        clicked = await _click_button_escalating(page, btn_text)

    if not clicked:
        return {"success": False, "error_type": "click_failed",
                "error_text": f"无法点击 {action} 按钮: {btn_text}"}

    await page.wait_for_timeout(1500)

    # 导航保护：检查是否触发了页面跳转（从 Stage 2 移入）
    url_after = page.url
    if url_before != url_after:
        LOG.info(f"    检测到页面跳转: {url_before[:40]} → {url_after[:40]}")
        # 如果是跳转类操作（如 authorize），等待加载完成后返回
        await wait_for_loading_complete(page)
        # 尝试导航回原始页面
        try:
            await page.goto(url_before, wait_until="domcontentloaded", timeout=30000)
            await wait_for_table_ready(page, timeout=15000)
            LOG.info(f"    已导航回原始页面")
        except Exception as e:
            LOG.warning(f"    导航回原始页面失败: {e}")
        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector
        return {"success": True, "navigation_occurred": True, "selectors": selectors}

    await wait_for_loading_complete(page)

    # Fix 4: 特殊处理 import 操作 - 检测文件上传对话框
    if action == "import":
        has_upload_dialog = await page.evaluate("""() => {
            // 检测文件上传对话框
            const fileInput = document.querySelector('input[type="file"]');
            const uploadComponent = document.querySelector('.el-upload, .ant-upload');
            const uploadDialog = document.querySelector('.el-dialog, .ant-modal');

            if (fileInput && fileInput.offsetParent !== null) {
                return true;
            }
            if (uploadComponent && uploadComponent.offsetParent !== null) {
                return true;
            }
            // 检查对话框中是否包含上传组件
            if (uploadDialog && uploadDialog.offsetParent !== null) {
                const dialogUpload = uploadDialog.querySelector('.el-upload, .ant-upload, input[type="file"]');
                if (dialogUpload) return true;
            }
            return false;
        }""")

        if has_upload_dialog:
            LOG.info(f"    检测到文件上传对话框（{action} 操作）")
            # 关闭对话框
            await _close_dialog(page)
            selectors = {"trigger": btn_text}
            if row_selector:
                selectors["row_selector"] = row_selector
            return {"success": True, "selectors": selectors}

    # 如果有确认弹窗，点击确认
    state = await _check_precondition_state(page, {"type": "dialog"})
    confirmed = ""
    if state["success"]:
        LOG.info(f"    检测到确认弹窗: {state.get('actual_state', 'dialog')}")
        from .button_driver import confirm_dialog
        confirmed = await confirm_dialog(page)
        if confirmed:
            LOG.info(f"    已点击确认按钮: {confirmed}")
        else:
            LOG.warning(f"    确认弹窗存在但未找到确认按钮")
        await wait_for_loading_complete(page)
        await page.wait_for_timeout(1000)
    else:
        LOG.debug(f"    未检测到确认弹窗 (state: {state.get('actual_state', 'none')})")

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        first = errors[0]
        return {"success": False, "error_type": first.get("severity", "unknown"),
                "error_text": first.get("error_text", "")}

    # 验证操作是否真的成功（不能只靠"没错误=成功"）
    success = await _verify_operation_success(page, action)
    if not success:
        # 如果确认框没被点击（可能 _check_precondition_state 漏检），返回失败
        if not confirmed:
            return {"success": False, "error_type": "no_confirm_button",
                    "error_text": f"未检测到 {action} 确认弹窗或操作成功信号"}
        # 确认框点了但没有成功信号
        return {"success": False, "error_type": "no_success_signal",
                "error_text": f"{action} 操作后未检测到成功信号"}

    # 构建 selectors
    selectors = {"trigger": btn_text}
    if row_selector:
        selectors["row_selector"] = row_selector
    if confirmed:
        selectors["confirm"] = confirmed

    return {"success": True, "selectors": selectors}


# ============================================================
# 辅助函数
# ============================================================

async def _click_button_escalating(page, btn_text: str) -> bool:
    """降级策略点击按钮：Playwright → force → JS → 坐标。"""
    if not btn_text:
        return False

    # 策略 1: Playwright 原生点击
    try:
        locator = page.locator(
            f'button:has-text("{btn_text}"), span:has-text("{btn_text}"), '
            f'a:has-text("{btn_text}")'
        ).first
        if await locator.count() > 0:
            await locator.click(timeout=3000)
            return True
    except Exception:
        pass

    # 策略 2: force click
    try:
        await page.click(
            f'button:has-text("{btn_text}"), span:has-text("{btn_text}")',
            force=True, timeout=3000
        )
        return True
    except Exception:
        pass

    # 策略 3: JS click
    try:
        clicked = await page.evaluate(f"""(text) => {{
            const elements = document.querySelectorAll('button, span, a, .el-button');
            for (const el of elements) {{
                if (el.textContent.trim().includes(text) && el.offsetWidth > 0) {{
                    el.click();
                    return true;
                }}
            }}
            return false;
        }}""", btn_text)
        if clicked:
            return True
    except Exception:
        pass

    # 策略 4: 坐标点击
    try:
        rect = await page.evaluate(f"""(text) => {{
            const elements = document.querySelectorAll('button, span, a, .el-button');
            for (const el of elements) {{
                if (el.textContent.trim().includes(text) && el.offsetWidth > 0) {{
                    const r = el.getBoundingClientRect();
                    return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
                }}
            }}
            return null;
        }}""", btn_text)
        if rect:
            await page.mouse.click(rect["x"], rect["y"])
            return True
    except Exception:
        pass

    return False


async def _verify_operation_success(page, operation_type: str) -> bool:
    """验证操作是否成功（弹窗关闭 / 成功提示 / 列表变化）。

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型 (create/update/delete/lock/unlock/...)

    Returns:
        bool: 是否检测到成功信号
    """
    # 1. 检查成功提示 (Element UI / Ant Design)
    has_success = await page.evaluate("""() => {
        // Element UI success message
        const successMsg = document.querySelector('.el-message--success, .el-message .el-icon-success');
        if (successMsg && successMsg.offsetWidth > 0) return true;

        // Ant Design success message
        const antMsg = document.querySelector('.ant-message-success, .ant-message .anticon-check-circle');
        if (antMsg && antMsg.offsetWidth > 0) return true;

        // Notification success
        const notif = document.querySelector('.el-notification .el-icon-success, .ant-notification .anticon-check-circle');
        if (notif) return true;

        return false;
    }""")
    if has_success:
        return True

    # 2. 对于 create/update: 检查弹窗是否关闭
    if operation_type in ("create", "update"):
        state = await _check_precondition_state(page, {"type": "dialog"})
        if not state["success"]:
            # 弹窗已关闭 = 成功
            return True

    # 3. 对于 delete/lock/unlock/reset/authorize/migrate/import/export: 检查无错误提示 + 确认弹窗已关闭
    if operation_type in ("delete", "lock", "unlock", "reset", "authorize",
                          "migrate", "import", "export", "batch", "approve"):
        # 确认弹窗/MessageBox 已关闭（说明确认流程走完了）
        has_pending_confirm = await page.evaluate("""() => {
            // el-message-box 仍可见
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            if (msgBox && msgBox.offsetWidth > 0) return true;
            // el-popconfirm 仍可见
            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            if (popconfirm && popconfirm.offsetWidth > 0) return true;
            return false;
        }""")
        if has_pending_confirm:
            return False  # 确认弹窗仍在 → 操作未完成

        has_error = await page.evaluate("""() => {
            const errMsg = document.querySelector('.el-message--error, .ant-message-error, .el-message--warning');
            return errMsg && errMsg.offsetWidth > 0;
        }""")
        if not has_error:
            return True

    return False


async def _ensure_on_list_page(page):
    """确保当前在列表页（如果不在，导航回去）。"""
    url = page.url
    # 检查是否在创建/编辑页面
    if any(kw in url for kw in ("/create", "/add", "/edit", "/detail")):
        # 回退到上一页
        await page.go_back()
        await wait_for_navigation_complete(page)

    # 关闭可能残留的弹窗
    await _close_dialog(page)


async def _get_page_state_key(page) -> str:
    """计算页面状态 key（URL + 弹窗标题 hash），用于 Vision 缓存。"""
    import hashlib
    url = page.url
    try:
        dialog_titles = await page.evaluate("""() => {
            const dialogs = document.querySelectorAll(
                '.el-dialog__wrapper, .el-drawer, .ant-modal-wrap');
            const visible = Array.from(dialogs).filter(
                d => d.style.display !== 'none' && d.offsetWidth > 0);
            return visible.map(d => {
                const title = d.querySelector(
                    '.el-dialog__title, .el-drawer__header, .ant-modal-title');
                return title ? title.textContent.trim() : '';
            }).filter(t => t);
        }""")
    except Exception:
        dialog_titles = []

    state_str = f"{url}||{','.join(sorted(dialog_titles))}"
    state_hash = hashlib.md5(state_str.encode()).hexdigest()[:12]
    return f"{url[:80]}_{state_hash}"


async def _vision_analysis(page, context: dict) -> dict:
    """调用 Vision API 分析当前页面状态。

    同页面状态只截图分析 1 次（由 ai_debug_assistant 内部缓存）。

    Returns:
        dict: Vision 分析结果，含诊断建议
    """
    try:
        from .ai_debug_assistant import ai_assisted_analysis

        btn = context.get("btn", {})
        missing = [{
            "type": "button",
            "action": btn.get("action", ""),
            "text": btn.get("text", ""),
        }]

        result = await ai_assisted_analysis(
            page,
            missing_elements=missing,
            expected_context=f"预期操作: {btn.get('text', '?')} 应成功执行"
        )

        return result if result.get("found") else None
    except Exception as e:
        LOG.debug(f"  Vision 分析失败: {e}")
        return None


def _apply_fix(error_result: dict, context: dict) -> dict:
    """根据错误信息修改上下文数据。

    Args:
        error_result: 操作失败的结果 dict
        context: 当前操作上下文

    Returns:
        修改后的 context
    """
    error_type = error_result.get("error_type", "")
    error_field = error_result.get("error_field", "")
    error_text = error_result.get("error_text", "")

    if error_type == "form_validation":
        # 表单校验错误：修改对应字段的值
        overrides = context.setdefault("fill_overrides", {})
        if error_field:
            if "已存在" in error_text or "重复" in error_text or "duplicate" in error_text.lower():
                # 唯一性冲突：加时间戳后缀
                ts = str(int(time.time()))[-6:]
                overrides[error_field] = f"AT_{ts}"
            elif "格式" in error_text or "format" in error_text.lower():
                # 格式错误：尝试常见格式
                overrides[error_field] = _guess_format(error_field, error_text)
            elif "不能为空" in error_text or "必填" in error_text or "required" in error_text.lower():
                # 必填字段：填入默认值
                overrides[error_field] = f"auto_{str(int(time.time()))[-4:]}"
            else:
                # 其他校验错误：按字段类型生成合理值
                ts_val = str(int(time.time()))[-6:]
                field_lower = error_field.lower()
                if any(kw in field_lower for kw in ("手机", "电话", "phone", "tel", "mobile")):
                    import random as _rand
                    overrides[error_field] = f"138{_rand.randint(10000000, 99999999)}"
                elif any(kw in field_lower for kw in ("邮箱", "email")):
                    overrides[error_field] = f"at_{ts_val}@test.com"
                elif any(kw in field_lower for kw in ("密码", "password")):
                    overrides[error_field] = "Test@123456"
                else:
                    overrides[error_field] = f"fix_{ts_val}"

    elif error_type == "api_error":
        # 接口错误：分析错误文本
        if "不能为空" in error_text or "必填" in error_text:
            # 某个字段缺失：尝试填充
            overrides = context.setdefault("fill_overrides", {})
            # 尝试从错误文本中提取字段名
            for label in _extract_field_names(error_text):
                overrides[label] = f"auto_{str(int(time.time()))[-4:]}"

    elif error_type == "click_failed":
        # 点击失败：已在 _click_button_escalating 中尝试过所有策略
        # 标记为不可恢复
        pass

    elif error_type == "no_dialog":
        # 弹窗未出现：可能需要先执行前置操作
        pass

    return context


async def _deep_scan_form_item(page, field_label: str) -> dict:
    """深度扫描指定 form-item，识别复合组件结构。

    当 fill_overrides 需要精确填充时调用，用于处理复合组件
    （如手机号 = el-select 国家编码 + input 手机号）。

    Args:
        page: Playwright Page 对象
        field_label: 字段标签文本

    Returns:
        {
            "found": bool,
            "is_composite": bool,
            "elements": [{"type": "input/select", "selector": "...", "index": N}, ...]
        }
    """
    try:
        result = await page.evaluate(f"""(label) => {{
            const formItems = document.querySelectorAll('.el-form-item');
            for (const fi of formItems) {{
                const labelEl = fi.querySelector('.el-form-item__label');
                if (!labelEl) continue;
                const labelText = labelEl.textContent.trim().replace(/[：:]/g, '');
                if (labelText !== label) continue;

                // 找到目标 form-item，分析其结构
                const selectEl = fi.querySelector('.el-select, .ant-select');
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                ));

                const elements = [];
                const independentInputs = [];

                if (selectEl) {{
                    // 有 select 组件
                    elements.push({{ type: 'select', index: 0 }});

                    // 查找不在 select 内部的独立 input
                    allInputs.forEach((inp, idx) => {{
                        if (!selectEl.contains(inp) && !inp.readOnly && inp.offsetWidth > 0) {{
                            independentInputs.push(inp);
                            elements.push({{ type: 'input', index: elements.length }});
                        }}
                    }});

                    return {{
                        found: true,
                        is_composite: independentInputs.length > 0,
                        elements: elements
                    }};
                }} else {{
                    // 没有 select，只有普通 input
                    allInputs.forEach((inp, idx) => {{
                        if (!inp.readOnly && inp.offsetWidth > 0) {{
                            elements.push({{ type: 'input', index: elements.length }});
                        }}
                    }});

                    return {{
                        found: true,
                        is_composite: false,
                        elements: elements
                    }};
                }}
            }}
            return {{ found: false, is_composite: false, elements: [] }};
        }}""", field_label)
        return result
    except Exception as e:
        LOG.warning(f"    深度扫描 {field_label} 失败: {e}")
        return {"found": False, "is_composite": False, "elements": []}


def _guess_format(field_label: str, error_text: str) -> str:
    """根据字段标签和错误文本猜测正确的数据格式。"""
    label_lower = field_label.lower()
    ts = str(int(time.time()))[-6:]

    if any(kw in label_lower for kw in ("邮箱", "email")):
        return f"at_{ts}@test.com"
    if any(kw in label_lower for kw in ("手机", "电话", "phone", "tel")):
        import random as _rand
        return f"138{_rand.randint(10000000, 99999999)}"
    if any(kw in label_lower for kw in ("密码", "password")):
        return "Test@123#$"
    if any(kw in label_lower for kw in ("url", "网址", "链接")):
        return f"https://test-{ts}.example.com"
    if any(kw in label_lower for kw in ("编码", "code")):
        return f"code_{ts}"

    return f"AT_{ts}"


async def _check_create_api_triggered_simple(page, timeout: int = 3000) -> bool:
    """简化版 API 触发检测（从 Stage 2 移入）。

    通过检查最近的网络请求判断是否有 POST 创建请求。
    作为 _verify_operation_success 的补充确认，非必需。

    Args:
        page: Playwright Page 对象
        timeout: 检测超时（毫秒），默认 3 秒（比 Stage 2 的 8 秒更短）

    Returns:
        bool: 是否检测到创建 API 请求
    """
    try:
        # 使用 Performance API 检查最近的 XHR 请求
        has_post = await page.evaluate("""() => {
            const entries = performance.getEntriesByType('resource');
            const recent = entries.slice(-20); // 最近 20 个资源请求
            for (const entry of recent) {
                if (entry.initiatorType === 'xmlhttprequest' || entry.initiatorType === 'fetch') {
                    const url = entry.name.toLowerCase();
                    // 排除基础设施 API
                    if (url.includes('/list') || url.includes('/page') ||
                        url.includes('/search') || url.includes('/query') ||
                        url.includes('/menu') || url.includes('/theme')) {
                        continue;
                    }
                    // 检测到可能的创建 API
                    if (url.includes('/create') || url.includes('/add') ||
                        url.includes('/save') || url.includes('/register')) {
                        return true;
                    }
                }
            }
            return false;
        }""")
        return has_post
    except Exception as e:
        LOG.debug(f"    API 触发检测失败: {e}")
        return False


def _extract_field_names(error_text: str) -> list:
    """从错误文本中提取字段名。

    例如："'用户名'不能为空" → ["用户名"]
    """
    import re
    # 匹配引号中的字段名
    names = re.findall(r"['"'"'"「」【】]([^'"'"'"「」【】]+)['"'"'"「」【】]", error_text)
    if names:
        return names

    # 匹配 "XXX不能为空" / "XXX is required" 模式
    match = re.match(r"^(.+?)(?:不能为空|is required|必填)", error_text)
    if match:
        return [match.group(1).strip()]

    return []
