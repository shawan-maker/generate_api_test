"""
element_scanner.py — DOM element discovery and scanning functions.

Extracted from discover_ui.py. Contains functions for CSS selector scanning,
search input discovery, dropdown detection, create dialog scanning, iframe
field scanning, page structure snapshots, UI framework detection, KB-guided
discovery, hint-based re-scanning, and dialog button scanning.
"""

import json
import logging
import time
from core.discovery import const
from core.discovery.kb_loader import ProbeKB
from core.discovery.replay.wait_helpers import (
    wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog,
    wait_for_loading_complete, wait_for_table_ready,
)
from core.discovery.replay.form_filler import (
    FormFiller, read_form_errors, generate_fill_data, scan_form_fields,
    generate_fill_rules,
)

LOG = logging.getLogger("element_scanner")

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
    # Lazy imports for functions in button_detector (avoid circular imports)
    from core.discovery.ui_scanner.button_detector import (
        _classify_button_location, _close_dialog, _build_button_labels,
        _count_categories, _prewarm_table_data,
    )

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

    # 3.5 搜索输入框探测（独立于按钮扫描，复用 KB 搜索模板）
    search_inputs = await _discover_search_inputs(page, kb, framework)
    result["search_inputs"] = search_inputs
    if search_inputs:
        LOG.info(f"  搜索输入框: {len(search_inputs)} 个")
        for si in search_inputs:
            LOG.info(f"    - placeholder='{si.get('placeholder', '')}', locator={si.get('locator', '')}, "
                     f"source={si.get('source', 'css')}")

    # 4. 创建表单扫描（同时扫描弹窗按钮）
    # 通过位置判断：工具栏按钮通常是 create 类操作
    has_create = any(_classify_button_location(b) == "create_like" for b in result["toolbar_buttons"])
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

    # 4.1 数据预热：空表格时先创建数据以发现行操作按钮
    # 条件：无行操作 + 有创建按钮 + 表格无数据行
    if not result["row_actions"] and has_create:
        page_structure = await _snapshot_page_structure(page)
        if page_structure.get("mainBodyRows", 0) == 0:
            LOG.info("  表格无数据行，执行数据预热以发现行操作按钮...")
            prewarm_result = await _prewarm_table_data(
                page, result.get("form_fields", []), result["toolbar_buttons"], framework
            )
            if prewarm_result.get("success"):
                # 合并行操作（去重）
                for ra in prewarm_result.get("row_actions", []):
                    if not any(b["text"] == ra["text"] for b in result["row_actions"]):
                        result["row_actions"].append(ra)
                # 合并下拉子项（去重）
                for dd in prewarm_result.get("dropdowns", []):
                    if not any(d["text"] == dd["text"] and d["parent"] == dd["parent"]
                               for d in result["dropdowns"]):
                        result["dropdowns"].append(dd)
                # 如果 Stage 4 没扫到 form_fields，用预热时扫到的
                if not result["form_fields"] and prewarm_result.get("form_fields"):
                    result["form_fields"] = prewarm_result["form_fields"]
                    LOG.info(f"  预热补充表单字段: {len(result['form_fields'])} 个")
                LOG.info(f"  数据预热完成: {len(prewarm_result.get('row_actions', []))} 个行操作, "
                         f"{len(prewarm_result.get('dropdowns', []))} 个下拉子项")

    # 4.5 为所有按钮赋值 action 字段（使用按钮文本原文，不做分类）
    for key in ["toolbar_buttons", "row_actions", "dialog_buttons"]:
        for btn in result[key]:
            if "action" not in btn or not btn["action"]:
                btn["action"] = btn.get("text", "")

    # 5. 动态标签构建
    result["button_labels"] = _build_button_labels(result)

    result["summary"] = {
        "total_buttons": sum(len(result[k]) for k in
                            ["toolbar_buttons", "row_actions", "dialog_buttons", "menu_items", "dropdowns"]),
        "has_create": has_create,
        "has_delete": any(_classify_button_location(b) == "business"
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


async def _discover_search_inputs(page, kb: ProbeKB = None, framework: str = "element-ui") -> list:
    """探测页面搜索输入框（表格上方区域，排除弹窗/表单字段）。

    探测策略分两轮：
    - 第一轮：KB 增强 — 用 search-button 知识库的 XPath 模板定位搜索按钮/图标，
              再从其上下文推断关联的输入框
    - 第二轮：CSS 选择器扫描 — placeholder 关键词 + 搜索图标 + 表格上方 input

    排除规则：
    - 弹窗/抽屉内的输入框
    - 隐藏/禁用的元素
    - 表格行内的输入框（行内编辑）

    Returns:
        list: [{"locator": str, "placeholder": str, "has_adjacent_btn": bool,
                "source": str, "btn_xpath": str}]
    """
    results = []
    seen_locators = set()

    # ── 第一轮：KB 搜索按钮模板 → 推断关联输入框 ──
    if kb and kb._kb_data:
        search_btn_patterns = kb.get_patterns("search-button", framework)
        if search_btn_patterns:
            # 用 "搜索"/"查询" 作为 label 展开占位符
            search_labels = ["搜索", "查询", "查找"]
            xpath_list = []
            for label in search_labels:
                for pattern in search_btn_patterns:
                    xpath = pattern.replace("{label}", label)
                    if "{chars_all}" in xpath:
                        chars = list(label)
                        chars_all = " and ".join([f"contains(., '{c}')" for c in chars])
                        xpath = xpath.replace("{chars_all}", chars_all)
                    if "{char1}" in xpath and len(label) > 0:
                        xpath = xpath.replace("{char1}", label[0])
                    if "{char2}" in xpath and len(label) > 1:
                        xpath = xpath.replace("{char2}", label[1])
                    xpath_list.append(xpath)

            if xpath_list:
                import json
                xpath_json = json.dumps(xpath_list)
                try:
                    kb_results = await page.evaluate(f"""() => {{
                        const xpaths = {xpath_json};
                        const found = [];
                        const table = document.querySelector('.el-table, table, .ant-table');
                        if (!table) return found;
                        const tableRect = table.getBoundingClientRect();

                        for (const xpath of xpaths) {{
                            try {{
                                const result = document.evaluate(
                                    xpath, document, null,
                                    XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null
                                );
                                for (let i = 0; i < result.snapshotLength; i++) {{
                                    const btn = result.snapshotItem(i);
                                    if (!btn || btn.offsetWidth <= 0) continue;
                                    // 排除弹窗内
                                    if (btn.closest('.el-dialog, .el-drawer, .ant-modal')) continue;

                                    // 从搜索按钮向上查找关联输入框
                                    const container = btn.closest(
                                        '.el-form-item, .el-input-group, .ant-input-group, '
                                        + '[class*="search"], [class*="filter"], [class*="toolbar"]'
                                    ) || btn.parentElement;
                                    if (!container) continue;

                                    const input = container.querySelector(
                                        'input[type="text"], input:not([type]), .el-input__inner, .ant-input'
                                    );
                                    if (!input || input.offsetWidth <= 0) continue;

                                    // 排除表格内和表格下方
                                    if (table.contains(input)) continue;
                                    const inputRect = input.getBoundingClientRect();
                                    if (inputRect.top > tableRect.bottom) continue;

                                    const placeholder = (input.getAttribute('placeholder') || '').trim();
                                    let locator = '';
                                    if (placeholder) {{
                                        locator = 'input[placeholder="' + placeholder + '"]';
                                    }} else {{
                                        const name = input.getAttribute('name') || '';
                                        if (name) locator = 'input[name="' + name + '"]';
                                        else continue;
                                    }}

                                    found.push({{
                                        locator,
                                        placeholder,
                                        has_adjacent_btn: true,
                                        source: 'kb_search',
                                        btn_xpath: xpath,
                                        score: 15,  // KB 匹配得分高
                                    }});
                                }}
                            }} catch (e) {{
                                // 忽略单个 XPath 错误
                            }}
                        }}
                        return found;
                    }}""")
                    for item in kb_results:
                        loc = item.get("locator", "")
                        if loc and loc not in seen_locators:
                            seen_locators.add(loc)
                            results.append(item)
                except Exception as e:
                    LOG.debug(f"KB search-button 扫描失败: {e}")

    # ── 第二轮：CSS 选择器扫描（兜底） ──
    # ── 第二轮：CSS 结构特征扫描（兜底，不依赖关键词） ──
    css_raw = await page.evaluate(f"""() => {{
        const results = [];
        // 已知的 locator（KB 轮已发现的）
        const knownLocators = {json.dumps(list(seen_locators))};
        const table = document.querySelector('.el-table, table, .ant-table');
        if (!table) return results;
        const tableRect = table.getBoundingClientRect();

        // 收集所有候选 input
        const inputs = document.querySelectorAll(
            'input[type="text"], input:not([type]), .el-input input, .ant-input'
        );

        for (const el of inputs) {{
            // ── 基础排除 ──
            if (el.offsetWidth <= 0 || el.offsetHeight <= 0) continue;
            if (el.closest('.el-dialog, .el-drawer, .ant-modal, .ant-drawer')) continue;
            if (el.closest('[style*="display: none"], [style*="display:none"]')) continue;
            if (el.disabled) continue;
            if (table.contains(el)) continue;

            const rect = el.getBoundingClientRect();
            if (rect.top > tableRect.bottom) continue;

            const placeholder = (el.getAttribute('placeholder') || '').trim();

            // ── 结构特征打分（不依赖具体关键词） ──
            let score = 0;

            // 1. 有搜索图标 → 强信号
            const parentInput = el.closest('.el-input, .ant-input-wrapper, .ant-input-affix-wrapper');
            const hasSearchIcon = parentInput
                && parentInput.querySelector(
                    'i[class*="search"], svg[class*="search"], '
                    + '.el-icon-search, .anticon-search'
                );
            if (hasSearchIcon) score += 8;

            // 2. 有相邻按钮 → 强信号（搜索框+搜索按钮组合）
            let hasAdjacentBtn = false;
            const container = el.closest(
                '.el-input-group, .ant-input-group, '
                + '[class*="search"], [class*="filter"], [class*="toolbar"], '
                + '[class*="Search"], [class*="Filter"]'
            );
            if (container) {{
                const btn = container.querySelector('button, .el-button, .ant-btn');
                if (btn && btn.offsetWidth > 0) hasAdjacentBtn = true;
            }}
            // 紧邻兄弟按钮
            if (!hasAdjacentBtn && el.nextElementSibling) {{
                const sib = el.nextElementSibling;
                if (sib.tagName === 'BUTTON' || sib.classList.contains('el-button')
                    || sib.classList.contains('ant-btn')) {{
                    hasAdjacentBtn = true;
                }}
            }}
            // input 外层的父元素旁边有按钮
            if (!hasAdjacentBtn) {{
                const grandparent = el.closest('.el-input, .ant-input-wrapper')?.parentElement;
                if (grandparent) {{
                    const sibBtn = grandparent.nextElementSibling;
                    if (sibBtn && (sibBtn.tagName === 'BUTTON' || sibBtn.classList.contains('el-button'))) {{
                        hasAdjacentBtn = true;
                    }}
                }}
            }}
            if (hasAdjacentBtn) score += 6;

            // 3. 不在 form-item 中 → 中信号（搜索框通常独立于表单）
            const inFormItem = !!el.closest('.el-form-item, .ant-form-item');
            if (!inFormItem) score += 3;

            // 4. 有 placeholder → 弱信号（比空 placeholder 好）
            if (placeholder) score += 1;

            // 5. 容器 class 含搜索/过滤语义 → 中信号
            if (container) {{
                const cls = (container.className || '').toLowerCase();
                if (cls.includes('search') || cls.includes('filter')
                    || cls.includes('query') || cls.includes('toolbar')) {{
                    score += 4;
                }}
            }}

            // 需要至少满足: 搜索图标(8) 或 相邻按钮(6) 或 无formItem+placeholder(4)
            if (score < 4) continue;

            // 构建 CSS locator
            let locator = '';
            if (placeholder) {{
                locator = 'input[placeholder="' + placeholder + '"]';
            }} else if (hasSearchIcon) {{
                locator = '.el-input--prefix input';
            }} else {{
                const name = el.getAttribute('name') || '';
                if (name) {{
                    locator = 'input[name="' + name + '"]';
                }} else {{
                    continue;
                }}
            }}

            if (knownLocators.includes(locator)) continue;

            results.push({{
                locator,
                placeholder,
                has_adjacent_btn: hasAdjacentBtn,
                score,
                source: 'css',
            }});
        }}

        return results;
    }}""")
    for item in css_raw:
        loc = item.get("locator", "")
        if loc and loc not in seen_locators:
            seen_locators.add(loc)
            results.append(item)

    # 按得分排序，返回前 2 个（避免把多个表单字段误当搜索框）
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:2]


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
                    "action": item,  # 使用下拉菜单项文本作为 action
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
    from core.discovery.replay.wait_helpers import wait_for_loading_complete

    result = {"form_fields": [], "dialog_buttons": []}
    url_before = page.url

    # 使用工具栏按钮的文本作为创建按钮候选
    # 通过位置判断（toolbar 按钮通常是 create 类操作）
    toolbar_buttons = page.locator("button, .el-button").filter(has_not_text="")
    count = await toolbar_buttons.count()

    create_btn = None
    for i in range(count):
        btn = toolbar_buttons.nth(i)
        try:
            # 检查是否是工具栏按钮（位置判断）
            location = await btn.evaluate("""el => {
                const toolbar = el.closest('.el-toolbar, .toolbar, [class*="toolbar"]');
                return toolbar ? 'toolbar' : 'other';
            }""")

            if location == 'toolbar':
                # 获取按钮文本
                text = await btn.inner_text()
                text = text.strip()

                # 工具栏按钮通常是创建按钮（排除明显的非创建按钮）
                skip_keywords = ["删除", "导入", "导出", "查询", "搜索", "筛选"]
                if not any(kw in text for kw in skip_keywords):
                    create_btn = btn
                    LOG.info(f"发现工具栏按钮作为创建入口: {text}")
                    break
        except Exception as e:
            continue

    if not create_btn:
        return result

    clicked = False
    try:
        await create_btn.click()
        clicked = True
    except Exception as e:
        LOG.warning(f"点击创建按钮失败: {e}")
        return result

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
                             selector: _buildComponentSelector(fi, '.el-select'), required });
                independentInputs.forEach(inp => {
                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: _buildSelector(inp), inputType: inp.type || 'text', required });
                });
            }
            else if (fi.querySelector('.el-cascader')) {
                fields.push({ label, type: 'cascader', kb_category: 'el-cascader',
                             selector: _buildComponentSelector(fi, '.el-cascader'), required });
            } else if (fi.querySelector('.el-date-editor, .ant-picker')) {
                fields.push({ label, type: 'date-picker', kb_category: 'date-picker',
                             selector: _buildComponentSelector(fi, '.el-date-editor, .ant-picker'), required });
            } else if (selectEl) {
                fields.push({ label, type: 'select', kb_category: 'el-select',
                             selector: _buildComponentSelector(fi, '.el-select'), required });
            } else if (fi.querySelector('.el-radio-group')) {
                const firstRadio = fi.querySelector('.el-radio-button__inner, .el-radio__label');
                fields.push({ label, type: 'radio', kb_category: 'radio',
                             selector: _buildComponentSelector(fi, '.el-radio-group'), required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' });
            } else if (fi.querySelector('.el-checkbox') && !fi.closest('.el-table')) {
                fields.push({ label, type: 'checkbox', kb_category: 'form-checkbox',
                             selector: _buildComponentSelector(fi, '.el-checkbox'), required });
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
                                 selector: _buildSelector(mainInput), inputType: mainInput.type || 'text', required });
                } else if (allInputs.length === 1) {
                    // 单个 input
                    const inp = allInputs[0];
                    fields.push({ label, type: 'input', kb_category: 'input-generic',
                                 selector: _buildSelector(inp), inputType: inp.type || 'text', required });
                } else if (ta) {
                    fields.push({ label, type: 'textarea', kb_category: 'textarea-generic',
                                 selector: _buildSelector(ta), required });
                }
            }
        });

        // 辅助函数：为 input/textarea 生成 selector（基于 DOM 路径）
        function _buildSelector(el) {
            if (!el) return null;
            // 优先使用 id
            if (el.id) return `#${el.id}`;
            // 优先使用 name
            if (el.getAttribute('name')) return `[name="${el.getAttribute('name')}"]`;
            // 回退：使用 DOM 路径
            return _buildCssPath(el);
        }

        // 辅助函数：为复杂组件生成 selector（基于 label + 同级定位）
        function _buildComponentSelector(formItem, selector) {
            // 使用 formItem 在直接父元素中的位置定位（children 只返回直接子元素）
            // 注意：不能用 document.querySelectorAll 全局计数，因为 nth-of-type 是相对于直接父元素的
            const parent = formItem.parentElement;
            if (parent) {
                const directChildren = Array.from(parent.children);
                const idx = directChildren.indexOf(formItem);
                if (idx >= 0) {
                    // 使用 nth-child 而非 nth-of-type，因为我们计算的是所有直接子元素的位置
                    return `.el-form-item:nth-child(${idx + 1}) ${selector}`;
                }
            }
            // 最后回退：用组件自身属性
            const comp = formItem.querySelector(selector);
            if (comp) return _buildCssPath(comp);
            return null;
        }

        // 辅助函数：基于 DOM 路径生成 CSS selector
        function _buildCssPath(el) {
            const parts = [];
            let current = el;
            while (current && current.nodeType === 1) {
                let tag = current.tagName.toLowerCase();
                if (current.id) {
                    parts.unshift(`#${current.id}`);
                    break;
                }
                const parent = current.parentElement;
                if (parent) {
                    const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                    if (siblings.length > 1) {
                        const idx = siblings.indexOf(current) + 1;
                        tag += `:nth-of-type(${idx})`;
                    }
                }
                parts.unshift(tag);
                if (parts.length >= 5) break;  // 限制深度
                current = current.parentElement;
            }
            return parts.join(' > ');
        }

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

    # 使用 KB 中的通用按钮模板（不依赖 ACTION_KEYWORDS 关键词）
    # 通用按钮模板（如 //button[contains(.,'{label}')]）会匹配页面上所有可见按钮
    button_patterns = kb.get_patterns("button", framework)
    if not button_patterns:
        # 回退：使用通用 XPath 直接扫描所有按钮
        button_patterns = ["//button", "//a[contains(@class,'btn')]", "//*[contains(@class,'el-button')]"]

    # 构建 XPath 查询：使用通用模板（不带 {label} 占位符）扫描所有按钮
    xpath_list = []
    for pattern in button_patterns:
        # 如果模板包含占位符，跳过（这些需要具体关键词）
        if "{label}" in pattern or "{chars_all}" in pattern:
            continue
        xpath_list.append(pattern)

    # 如果所有模板都带占位符，添加兜底的通用扫描
    if not xpath_list:
        xpath_list = [
            "//button[not(ancestor::nav) and not(ancestor::*[contains(@class,'pagination')])]",
            "//a[contains(@class,'btn') or contains(@class,'button')]",
            "//*[contains(@class,'el-button') and not(ancestor::*[contains(@class,'pagination')])]",
        ]

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


async def _scan_hints(page, missing_elements: list) -> list:
    """基于反馈循环的 hints 定向扫描缺失按钮。

    对 missing_elements 中类型为 button 的元素，使用按钮文本（action）
    在页面中搜索可见的匹配元素。
    包括主页面和 iframe 扫描。

    Args:
        page: Playwright 页面对象
        missing_elements: 缺失元素列表，每项含 type/action/location 等字段

    Returns:
        list: 找到的按钮元素列表
    """
    found = []
    selectors = const.BUTTON_SELECTORS_STR

    for elem in missing_elements:
        # 只处理 button 类型
        if elem.get("type") != "button":
            continue

        # 使用按钮文本（action）作为搜索关键词
        action = elem.get("action", "")
        if not action:
            LOG.debug(f"  hints 扫描: action 为空")
            continue

        # 尝试搜索按钮文本
        try:
            elements = await page.evaluate(f"""() => {{
                const results = [];
                const selectors = '{selectors}';
                const all = document.querySelectorAll(selectors);
                for (const el of all) {{
                    const text = (el.textContent || '').trim();
                    if (text.includes('{action}') &&
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
                LOG.info(f"  hints 扫描: 按钮文本 '{action}' 找到 {len(elements)} 个元素")

        except Exception as e:
            LOG.debug(f"  hints 扫描按钮文本 '{action}' 失败: {e}")
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
