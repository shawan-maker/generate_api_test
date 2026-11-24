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
    FormFiller, read_form_errors, scan_form_fields,
)
from core.discovery.shared import generate_fill_data, generate_fill_rules

LOG = logging.getLogger("element_scanner")

# 全局知识库实例
_kb: ProbeKB = None


def _get_kb() -> ProbeKB:
    """获取全局知识库实例（延迟加载）"""
    global _kb
    if _kb is None:
        _kb = ProbeKB()
    return _kb


# 导航/面包屑等非业务元素的 className 关键词
_NAVIGATION_CLASS_PATTERNS = {
    "breadcrumb",    # el-breadcrumb, ant-breadcrumb
    "pagination",    # el-pagination, ant-pagination
    "page-header",   # 页头标题
    "nav-link",      # 导航链接
    "nav-item",      # 导航项
}


def _is_navigation_element(btn: dict) -> bool:
    """判断是否为导航/面包屑等非业务元素（应排除出 toolbar_buttons）。

    规则：
    - className 包含导航类关键词（breadcrumb, pagination 等）
    - toolbar 位置的 SPAN 标签（通常是面包屑文本，不是可交互按钮）

    Args:
        btn: 按钮候选元素字典

    Returns:
        True 表示是导航元素，应被过滤
    """
    cls = (btn.get("className") or "").lower()
    tag = btn.get("tag", "")

    # 面包屑/分页等 class 关键词
    if any(kw in cls for kw in _NAVIGATION_CLASS_PATTERNS):
        return True

    # 非 BUTTON/A 标签 + toolbar 位置 → 大概率是导航文本（如面包屑 SPAN）
    if tag not in ("BUTTON", "A") and btn.get("location") == "toolbar":
        return True

    return False


async def discover_all(page, explore_mode: str = "") -> dict:
    """完整探测入口：扫描按钮 + 位置分类 + 下拉菜单 + 表单字段 + 页面结构快照。

    支持多 tab 页面：检测到多个 tab 时，逐一切换并扫描每个 tab 下的元素，
    合并到统一结果中（标记 tab 来源）。

    Args:
        page: Playwright Page 对象
        explore_mode: 调用语境（默认空 = 列表页全量探索）。
            "form_page" = 调用方已在表单页（如 navigation_executor 的跨页面
            编辑/新增探索）——此语境下跳过 4.2 form_page 交互扫描段：
            该段设计场景是「列表页点编辑类按钮展开表单」，在表单页误用会
            探索性点击表单内功能按钮（如黑白名单编辑页的「添加」IP 行），
            且段尾点「取消」在表单页语义 = 放弃编辑退出页面，把探索器
            带离目标表单页（fix4 实证：提交前 URL 已回列表页、全链空转）。
    """
    kb = _get_kb()
    framework = await _detect_ui_framework(page)

    # ★ 页面结构快照（含 tab 检测）—— 必须在 _discover_once 之前
    # _discover_once 含预热（prewarm），预热提交表单可能导致页面跳转（page-nav 模式），
    # 跳转后 tab 元素消失，导致 tab 检测失败。
    original_url = page.url
    page_structure = await _snapshot_page_structure(page)

    result = await _discover_once(page, kb, framework, explore_mode)

    result["page_structure"] = page_structure
    result["framework"] = framework

    if page_structure:
        LOG.info(f"  页面结构: fixed_left={page_structure.get('hasFixedLeft')}, "
                 f"fixed_right={page_structure.get('hasFixedRight')}, "
                 f"rows={page_structure.get('mainBodyRows', 0)}")

    # ---- 补打初始 Tab 的标记 ----
    tabs = page_structure.get("tabs", [])
    if tabs:
        # 找到活跃 Tab（active=true），如果没有则用第一个
        active_tab = next((t for t in tabs if t.get("active")), tabs[0])
        active_tab_name = active_tab.get("name", "")

        # 为初始扫描的所有按钮类型补打 tab 标记（统一标记策略）
        _btn_types_to_mark = [
            "toolbar_buttons", "row_actions", "dropdowns",
            "dialog_buttons", "search_inputs", "menu_items",
            "switches",  # ★ 包含 switch 开关（如"开启黑名单"）
        ]
        for btn_type in _btn_types_to_mark:
            for item in result.get(btn_type, []):
                item["tab"] = active_tab_name
        for vo_data in result.get("validated_operations", {}).values():
            vo_data["tab"] = active_tab_name

    # ---- Tab 迭代扫描：检测到多个 tab 时，逐一扫描 ----
    if len(tabs) > 1:
        tab_names = [t["name"] for t in tabs]
        LOG.info(f"  📑 检测到 {len(tabs)} 个 tab: {tab_names}")
        for tab in tabs:
            if tab.get("active"):
                continue  # 当前 tab 已扫描
            tab_name = tab["name"]
            LOG.info(f"  📑 切换到 tab: {tab_name}")
            try:
                # 切换前清理可能的弹窗残留
                from core.discovery.ui_scanner.button_detector import _close_dialog
                await _close_dialog(page)

                # 点击 tab
                tab_locator = tab.get("locator", "")
                if tab_locator:
                    await page.click(tab_locator, timeout=5000)
                else:
                    await page.click(
                        f'[role="tab"]:has-text("{tab_name}"), .el-tabs__item:has-text("{tab_name}")',
                        timeout=5000,
                    )
                await page.wait_for_timeout(2000)  # 等待 tab 切换和数据加载

                # 在该 tab 下扫描（explore_mode 透传：form_page 语境下同样跳过 4.2 探索性点击）
                tab_result = await _discover_once(page, kb, framework, explore_mode)
                _merge_tab_results(result, tab_result, tab_name)
            except Exception as e:
                LOG.warning(f"  ⚠️ tab [{tab_name}] 扫描失败: {e}")

    return result


def _merge_tab_results(base: dict, tab_result: dict, tab_name: str):
    """将 tab 扫描结果合并到主结果中，标记 tab 来源。

    追加策略：所有按钮直接追加，不替换同名按钮。每个按钮标记 tab 字段，
    供下游代码（crud_executor、builder）区分不同 tab 的独立操作。

    Args:
        base: 主结果字典（会被原地修改）
        tab_result: 当前 tab 的扫描结果
        tab_name: tab 名称
    """
    # toolbar_buttons 合并：去重后追加（同一 Tab 内不重复，跨 Tab 的同名按钮保留）
    for btn in tab_result.get("toolbar_buttons", []):
        btn["tab"] = tab_name
        btn_list = base.setdefault("toolbar_buttons", [])
        # 去重：同一 tab + 同一文本 = 同一个按钮
        if not any(b.get("tab") == tab_name and b.get("text") == btn.get("text") for b in btn_list):
            btn_list.append(btn)

    # row_actions 合并：去重后追加（同理）
    for ra in tab_result.get("row_actions", []):
        ra["tab"] = tab_name
        ra_list = base.setdefault("row_actions", [])
        if not any(b.get("tab") == tab_name and b.get("text") == ra.get("text") for b in ra_list):
            ra_list.append(ra)

    # form_actions 合并（不标记 tab，表单按钮通常跨 tab 共享）
    for fa in tab_result.get("form_actions", []):
        if not any(b["text"] == fa["text"] for b in base.setdefault("form_actions", [])):
            base["form_actions"].append(fa)

    # validated_operations 合并（加 tab 前缀避免重名）
    for action, op_data in tab_result.get("validated_operations", {}).items():
        op_data["tab"] = tab_name
        existing = base.setdefault("validated_operations", {})
        if action in existing:
            keyed_name = f"{tab_name}/{action}"
        else:
            keyed_name = action
        existing[keyed_name] = op_data

    # search_inputs 合并
    for si in tab_result.get("search_inputs", []):
        si["tab"] = tab_name
        base.setdefault("search_inputs", []).append(si)

    # dropdowns 合并
    for dd in tab_result.get("dropdowns", []):
        dd["tab"] = tab_name
        base.setdefault("dropdowns", []).append(dd)

    # dialog_buttons 合并
    for db in tab_result.get("dialog_buttons", []):
        db["tab"] = tab_name
        base.setdefault("dialog_buttons", []).append(db)

    # switches 合并：去重后追加（同 toolbar_buttons 策略）
    for sw in tab_result.get("switches", []):
        sw["tab"] = tab_name
        sw_list = base.setdefault("switches", [])
        if not any(b.get("tab") == tab_name and b.get("text") == sw.get("text") for b in sw_list):
            sw_list.append(sw)


async def _discover_once(page, kb: ProbeKB, framework: str, explore_mode: str = "") -> dict:
    """单次探测流程（内部函数，由 discover_all 调用）

    Args:
        page: Playwright Page 对象
        kb: 知识库实例
        framework: UI 框架类型
        explore_mode: 调用语境（见 discover_all docstring；form_page 时跳过 4.2 段）
    """
    # Lazy imports for functions in button_detector (avoid circular imports)
    from core.discovery.ui_scanner.button_detector import (
        _classify_button_location, _close_dialog, _build_button_labels,
        _count_categories, _prewarm_table_data,
    )

    result = {
        "toolbar_buttons": [], "row_actions": [], "dialog_buttons": [],
        "form_actions": [], "menu_items": [], "dropdowns": [], "form_fields": [], "summary": {},
    }

    # 1. CSS 地毯扫描
    candidates = await _scan_candidates(page)
    LOG.info(f"扫描到 {len(candidates)} 个候选元素")

    # 2. KB 增强扫描
    kb_extra = await _kb_enrichment_scan(page, candidates, kb, framework)
    if kb_extra:
        candidates.extend(kb_extra)
        LOG.info(f"  KB 增强扫描发现 {len(kb_extra)} 个额外元素")

    # 过滤 toolbar_buttons：排除面包屑/导航等非业务元素
    result["toolbar_buttons"] = [
        c for c in candidates
        if c.get("location") == "toolbar" and not _is_navigation_element(c)
    ]
    result["row_actions"] = [c for c in candidates if c.get("location") == "row_action"]
    result["dialog_buttons"] = [c for c in candidates if c.get("location") == "dialog"]
    result["form_actions"] = [c for c in candidates if c.get("location") == "form_action"]
    result["menu_items"] = [c for c in candidates if c.get("location") == "menu"]

    # 统计被过滤的导航元素
    _filtered_nav = [
        c for c in candidates
        if c.get("location") == "toolbar" and _is_navigation_element(c)
    ]
    if _filtered_nav:
        LOG.debug(f"  过滤导航元素: {[_f.get('text', '') for _f in _filtered_nav]}")

    LOG.info(f"  工具栏: {len(result['toolbar_buttons'])}  行操作: {len(result['row_actions'])}  "
             f"弹窗: {len(result['dialog_buttons'])}  表单: {len(result['form_actions'])}  "
             f"菜单: {len(result['menu_items'])}")

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

    # 3.6 Switch 开关发现（el-switch 元素，如"开启黑名单"）
    switches = await _discover_switches(page)
    result["switches"] = switches
    if switches:
        LOG.info(f"  Switch 开关: {len(switches)} 个")
        for sw in switches:
            state = "ON" if sw.get("is_checked") else "OFF"
            tab_info = f" (tab: {sw.get('tab')})" if sw.get('tab') else ""
            LOG.info(f"    - '{sw.get('text', '')}' [{state}]{tab_info}")

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

    # 3.8 主从布局检测：有表格 + 无 create_like 按钮 → 尝试激活树节点后重新扫描
    has_create = any(_classify_button_location(b) == "create_like" for b in result["toolbar_buttons"])
    _page_struct_check = await _snapshot_page_structure(page)
    _has_table = _page_struct_check.get("mainBodyRows", -1) >= 0 or _page_struct_check.get("tables", 0) > 0
    if _has_table and not has_create:
        precondition = await _try_activate_master_detail_tree(page)
        if precondition:
            result["preconditions"] = [precondition]
            # 重新扫描 toolbar + row_actions
            _rescan = await _scan_candidates(page)
            _new_toolbar = [
                c for c in _rescan
                if c.get("location") == "toolbar" and not _is_navigation_element(c)
            ]
            _new_rows = [c for c in _rescan if c.get("location") == "row_action"]
            # 合并新发现的按钮（去重）
            for btn in _new_toolbar:
                if not any(b["text"] == btn["text"] for b in result["toolbar_buttons"]):
                    result["toolbar_buttons"].append(btn)
            for ra in _new_rows:
                if not any(b["text"] == ra["text"] for b in result["row_actions"]):
                    result["row_actions"].append(ra)
            # 重新判断 has_create
            has_create = any(_classify_button_location(b) == "create_like" for b in result["toolbar_buttons"])
            LOG.info(f"  重新扫描: toolbar={len(result['toolbar_buttons'])} row_actions={len(result['row_actions'])} "
                     f"has_create={has_create}")

    # 4.1 数据预热：空表格时先创建数据以发现行操作按钮
    # 条件：无行操作 + 有创建按钮（无论表格是否有数据）
    # 修复：即使表格有数据，如果行操作未被发现，也应执行预热
    if not result["row_actions"] and has_create:
        page_structure = await _snapshot_page_structure(page)
        main_body_rows = page_structure.get("mainBodyRows", 0)

        if main_body_rows == 0:
            LOG.info("  表格无数据行，执行数据预热以发现行操作按钮...")
        else:
            LOG.info(f"  表格有 {main_body_rows} 行数据但行操作未被发现，执行数据预热...")

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

    # 4.2 form_page 交互扫描：点击编辑类按钮后扫描完整的表单交互状态
    # 条件：有 form_action 按钮 + 不是 CRUD 页面（无 create_like 按钮）
    # ★ explore_mode="form_page" 时整段跳过：本段设计场景是「列表页」——点编辑类
    # 按钮展开表单→扫字段→点取消恢复。在已是表单页的探索语境（跨页面编辑/新增）中
    # 误用会：①探索性点击表单内功能按钮（如编辑页「添加」IP 行，产生脏数据）；
    # ②段尾点「取消」= 表单页语义的放弃编辑退出，把探索器带离目标页面
    # （fix4 实证：提交前 URL 已回列表页，后续 fill/submit 全部空转失败）。
    if result["form_actions"] and not has_create and explore_mode != "form_page":
        _SUBMIT_KEYWORDS = ["保存", "提交", "确定", "确认", "save", "submit", "confirm"]

        # 找出编辑类按钮（非提交类）
        edit_btns = [
            btn for btn in result["form_actions"]
            if not any(kw in btn.get("text", "").lower() for kw in _SUBMIT_KEYWORDS)
        ]

        if edit_btns:
            edit_btn = edit_btns[0]
            btn_text = edit_btn.get("text", "")
            LOG.info(f"  form_page 交互扫描: 点击 '{btn_text}' 以发现表单字段和隐藏按钮...")

            try:
                from core.discovery.shared.button_clicker import click_page_button
                click_result = await click_page_button(page, btn_text)

                if click_result.get("clicked"):
                    await page.wait_for_timeout(1000)
                    from core.discovery.replay.wait_helpers import wait_for_loading_complete
                    await wait_for_loading_complete(page, timeout=5000)

                    # 1. 扫描表单字段（使用 form_filler 的公共方法）
                    if not result["form_fields"]:
                        from core.discovery.replay.form_filler import scan_form_fields
                        _scan_raw = await scan_form_fields(page, framework)
                        # ★ scan_form_fields 实际返回 {fields: [...], _debug_*: ...}
                        # 包装 dict（form_filler 内部 6 处调用点均做 isinstance(dict)
                        # → .get('fields') 解包兼容，此处对齐）。此前直接把 dict 赋给
                        # form_fields：len(dict)=key 数 误报"6 个字段"，下游
                        # navigation_executor [FIX] 诊断块对 dict 做 [:5] 切片崩溃
                        # （unhashable type: 'slice'），探索中断后 nav_info["form_fields"]
                        # 保留 dict，builder 迭代出 str 键 → str.get 崩掉整个 Stage 1。
                        form_fields = (_scan_raw.get("fields", [])
                                       if isinstance(_scan_raw, dict) else _scan_raw)
                        if form_fields:
                            result["form_fields"] = form_fields
                            LOG.info(f"    表单字段: {len(form_fields)} 个")

                    # 2. 重新扫描按钮
                    new_candidates = await _scan_candidates(page)
                    existing_texts = {btn.get("text", "") for btn in result["form_actions"]}

                    # 找出新出现的 form_action 或 dialog 按钮
                    for c in new_candidates:
                        if c.get("location") in ("form_action", "dialog"):
                            text = c.get("text", "")
                            if text and text not in existing_texts:
                                result["form_actions"].append(c)
                                existing_texts.add(text)
                                LOG.info(f"    发现新按钮: {c.get('location')} '{text}'")

                    # 3. 恢复初始状态：点击"取消"或按 Escape
                    cancel_clicked = await page.evaluate("""() => {
                        const all = document.querySelectorAll('button, .el-button, .ant-btn');
                        for (const el of all) {
                            const text = (el.textContent || '').trim();
                            if (['取消', '关闭', 'cancel', 'close'].some(kw =>
                                text.toLowerCase().includes(kw)) && el.offsetWidth > 0) {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }""")

                    if cancel_clicked:
                        await page.wait_for_timeout(500)
                    else:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(300)

                    LOG.info(f"  form_page 交互扫描完成: "
                             f"{len(result['form_actions'])} 个按钮, "
                             f"{len(result['form_fields'])} 个字段")
                else:
                    LOG.warning(f"  form_page 交互扫描: 点击 '{btn_text}' 失败")

            except Exception as e:
                LOG.warning(f"  form_page 交互扫描异常: {e}")
                # 尝试恢复
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)

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


async def _try_activate_master_detail_tree(page) -> dict:
    """检测主从布局（左侧树形 + 右侧操作区域），自动选中第一个树节点。

    触发条件（由调用方判断）：页面存在表格 + 无 create_like 按钮。

    流程：
    1. 检测树形结构是否存在
    2. 检查是否已有选中状态（避免重复操作）
    3. 点击第一个可见的树节点
    4. 等待右侧内容刷新
    5. 返回 precondition 记录（供 playbook 和 Stage 2 使用）

    Returns:
        precondition dict 或 None（未检测到树形结构）
        {
            "type": "click_first_visible",
            "selector": "[role='treeitem']",
            "text_matched": "总部",
            "description": "选择部门节点以激活操作区域",
            "wait_ms": 1500
        }
    """
    try:
        # Step 1: 检测树形结构
        info = await page.evaluate("""
        () => {
            // 判断节点是否是叶子节点（无子节点）
            function _isLeafNode(sel, node) {
                // Element UI / Ant Design 常见叶子节点标记
                const cls = (node.className || '').toString();

                // 显式标记为叶子
                if (cls.includes('is-leaf') || cls.includes('tree-leaf')) {
                    return true;
                }

                // 检查是否有子节点容器
                // Element UI: .el-tree-node__children
                // Ant Design: .ant-tree-treenode 内的 .ant-tree-indent 或子 .ant-tree-treenode
                const childrenContainer = node.querySelector(':scope > .el-tree-node__children, :scope > .ant-tree-list');
                if (childrenContainer) {
                    return false;  // 有子节点容器，不是叶子
                }

                // 检查 aria-expanded 属性（展开的节点不是叶子）
                const ariaExpanded = node.getAttribute('aria-expanded');
                if (ariaExpanded === 'true' || ariaExpanded === 'false') {
                    return false;  // 有展开属性，是父节点
                }

                // 检查是否有展开/折叠图标
                const switcherIcon = node.querySelector('.el-tree-node__expand-icon:not(.is-leaf), .ant-tree-switcher:not(.ant-tree-switcher-noop)');
                if (switcherIcon) {
                    return false;  // 有展开图标，是父节点
                }

                // 默认为叶子节点
                return true;
            }

            const treeSelectors = [
                '[role="treeitem"]',
                '.el-tree-node',
                '.ant-tree-treenode',
                '.custom-tree-node',
                '[class*="tree-node"]'
            ];

            for (const sel of treeSelectors) {
                const nodes = document.querySelectorAll(sel);
                if (nodes.length > 0) {
                    // 检查是否已有选中状态（且选中的是叶子节点）
                    const selectedSels = [
                        sel + '.is-current',
                        sel + '[aria-selected="true"]',
                        sel + '.active',
                        sel + '.selected'
                    ];
                    let hasSelected = false;
                    let selectedText = null;
                    for (const ssel of selectedSels) {
                        const selectedNode = document.querySelector(ssel);
                        if (selectedNode) {
                            // 检查选中的是否是叶子节点
                            const isLeaf = _isLeafNode(sel, selectedNode);
                            if (isLeaf) {
                                hasSelected = true;
                                selectedText = selectedNode.textContent.trim().substring(0, 60);
                            }
                            break;
                        }
                    }

                    // 找第一个可见的**叶子节点**
                    let firstLeaf = null;
                    for (const node of nodes) {
                        const rect = node.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            const isLeaf = _isLeafNode(sel, node);
                            if (isLeaf) {
                                firstLeaf = node.textContent.trim().substring(0, 60);
                                break;
                            }
                        }
                    }

                    return {
                        found: true,
                        selector: sel,
                        nodeCount: nodes.length,
                        hasSelected: hasSelected,
                        selectedText: selectedText,
                        firstLeaf: firstLeaf
                    };
                }
            }
            return {found: false};
        }
        """)

        if not info.get("found"):
            LOG.info("  🔍 主从布局检测: 无树形结构")
            return None

        tree_sel = info.get("selector", "")
        first_leaf = info.get("firstLeaf", "")
        LOG.info(f"  🔍 主从布局检测: 发现树形结构 ({tree_sel}, {info.get('nodeCount', 0)} 个节点)")
        LOG.info(f"     首个可见叶子节点: '{first_leaf}'")

        # Step 2: 如果已有选中状态（且选中的是叶子节点），记录但不点击
        if info.get("hasSelected"):
            selected_text = info.get("selectedText", "")
            LOG.info(f"     已有选中状态: '{selected_text}'")
            return {
                "type": "click_first_leaf",
                "selector": tree_sel,
                "text_matched": selected_text or first_leaf,
                "description": "选择树节点以激活操作区域",
                "wait_ms": 1500
            }

        # Step 3: 点击第一个可见的叶子节点
        LOG.info(f"  💡 自动选中第一个叶子节点以激活操作区域...")
        clicked = await page.evaluate(f"""
        () => {{
            function _isLeafNode(sel, node) {{
                const cls = (node.className || '').toString();
                if (cls.includes('is-leaf') || cls.includes('tree-leaf')) {{
                    return true;
                }}
                const childrenContainer = node.querySelector(':scope > .el-tree-node__children, :scope > .ant-tree-list');
                if (childrenContainer) {{
                    return false;
                }}
                const ariaExpanded = node.getAttribute('aria-expanded');
                if (ariaExpanded === 'true' || ariaExpanded === 'false') {{
                    return false;
                }}
                const switcherIcon = node.querySelector('.el-tree-node__expand-icon:not(.is-leaf), .ant-tree-switcher:not(.ant-tree-switcher-noop)');
                if (switcherIcon) {{
                    return false;
                }}
                return true;
            }}

            const nodes = document.querySelectorAll('{tree_sel}');
            for (const node of nodes) {{
                const rect = node.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {{
                    const isLeaf = _isLeafNode('{tree_sel}', node);
                    if (isLeaf) {{
                        node.click();
                        return node.textContent.trim().substring(0, 60);
                    }}
                }}
            }}
            return null;
        }}
        """)

        if not clicked:
            LOG.warning(f"     未能点击叶子节点")
            return None

        LOG.info(f"     已点击: '{clicked}'")

        # Step 4: 等待右侧内容刷新
        await page.wait_for_timeout(1500)

        # Step 5: 返回 precondition 记录
        return {
            "type": "click_first_leaf",
            "selector": tree_sel,
            "text_matched": clicked,
            "description": "选择树节点以激活操作区域",
            "wait_ms": 1500
        }

    except Exception as e:
        LOG.warning(f"  主从布局检测失败: {e}")
        return None


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
        // ★ 查找当前可见的表格（多 Tab 页面有多个 table，需要选激活 Tab 的）
        const allTables = document.querySelectorAll('.el-table, table, .ant-table');
        let table = null;
        for (const t of allTables) {{
            if (t.offsetWidth > 0 && t.offsetHeight > 0) {{
                table = t;
                break;
            }}
        }}
        if (!table && allTables.length > 0) {{
            table = allTables[0];  // fallback: 没有可见的用第一个
        }}
        const menu = document.querySelector('.el-menu, .ant-menu, .sidebar-menu');
        const fixedRight = table?.querySelector('.el-table__fixed-right');
        const fixedLeft = table?.querySelector('.el-table__fixed');

        // ★ 查找可见的弹窗/抽屉（排除 display:none 的隐藏弹窗）
        const dialogWrappers = document.querySelectorAll(
            '.el-dialog__wrapper, .ant-modal-wrap, .el-drawer__wrapper, [role="dialog"]'
        );
        let visibleDialog = null;
        for (const w of dialogWrappers) {{
            if (w.style.display !== 'none' && w.offsetWidth > 0) {{
                visibleDialog = w.querySelector('.el-dialog, .ant-modal, .el-drawer') || w;
                break;
            }}
        }}

        // ★ 查找页面内表单（非弹窗内的 el-form）
        const allForms = document.querySelectorAll('.el-form, .ant-form, form');
        let pageForm = null;
        for (const f of allForms) {{
            if (f.offsetWidth > 0 && !visibleDialog?.contains(f)) {{
                pageForm = f;
                break;
            }}
        }}

        all.forEach(el => {{
            const r = el.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            const text = (el.textContent || '').trim();
            if (!text || text.length > 40 || text.length < 1) return;
            const key = text + '|' + el.tagName;
            if (seen.has(key)) return;

            // ★ 跳过隐藏弹窗内的按钮（display:none 的弹窗内按钮不应参与扫描）
            const wrapper = el.closest('.el-dialog__wrapper, .ant-modal-wrap, .el-drawer__wrapper');
            if (wrapper && wrapper.style.display === 'none') return;

            // ★ 跳过非标准浮层内的按钮（cookie偏好/公告/通知等非业务组件）
            const nonBizOverlay = el.closest(
                '[class*="cookie"], [class*="consent"], [class*="banner"], ' +
                '[class*="notice-bar"], [class*="announcement"], [class*="notification-bar"], ' +
                '[class*="toast-"], [class*="snackbar"], [class*="alert-bar"]'
            );
            if (nonBizOverlay) return;

            seen.add(key);
            let location = 'toolbar';
            let source = 'main';
            if (table && table.contains(el)) {{
                location = 'row_action';
                if (fixedRight && fixedRight.contains(el)) source = 'fixed-right';
                else if (fixedLeft && fixedLeft.contains(el)) source = 'fixed-left';
            }}
            else if (visibleDialog && visibleDialog.contains(el)) location = 'dialog';
            else if (menu && menu.contains(el)) location = 'menu';
            else if (pageForm && pageForm.contains(el) && !table?.contains(el)) location = 'form_action';
            results.push({{
                text, tag: el.tagName, className: (el.className || '').slice(0, 60),
                rect: Math.round(r.width) + 'x' + Math.round(r.height), location, source,
            }});
        }});
        return results;
    }}""")
    return raw


async def _discover_switches(page) -> list:
    """扫描页面中的 el-switch 元素，记录其状态和上下文。

    扫描策略：
    - 查找所有 .el-switch 元素
    - 获取相邻的标签文本（优先 form-item label，其次 sibling 文本）
    - 记录当前状态（is-checked 类名）
    - 记录所属 tab-pane（如果在 tab 内）

    Args:
        page: Playwright Page 对象

    Returns:
        list: switch 信息列表，每个元素包含：
            - text: switch 旁的标签文本（如"开启黑名单"）
            - locator: CSS 选择器
            - is_checked: 当前是否开启
            - tab: 所属 tab 名称（如果在 tab-pane 内）
            - location: 位置类型（toolbar/form）
    """
    try:
        raw_switches = await page.evaluate("""() => {
            const switches = document.querySelectorAll('.el-switch');
            const results = [];

            switches.forEach((sw, idx) => {
                // 跳过不可见的 switch
                const rect = sw.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;

                // 检查是否可见（不在 display:none 的父元素内）
                const style = window.getComputedStyle(sw);
                if (style.display === 'none' || style.visibility === 'hidden') return;
                const parentHidden = sw.closest('[style*="display: none"], .el-tab-pane[style*="display: none"]');
                if (parentHidden) return;

                // 获取标签文本：优先 form-item__label，其次相邻文本
                let labelText = '';
                const formItem = sw.closest('.el-form-item');
                if (formItem) {
                    const label = formItem.querySelector('.el-form-item__label');
                    if (label) {
                        labelText = label.textContent.trim();
                    }
                }
                // 如果没有 label，尝试从相邻元素获取
                if (!labelText) {
                    const prev = sw.previousElementSibling;
                    if (prev && prev.textContent) {
                        labelText = prev.textContent.trim();
                    }
                }
                // 最后尝试从父元素的文本内容推断
                if (!labelText) {
                    const parent = sw.parentElement;
                    if (parent) {
                        const parentText = parent.textContent.trim();
                        // 提取 switch 前的文本
                        const swIdx = parent.innerHTML.indexOf(sw.outerHTML);
                        if (swIdx > 0) {
                            const beforeText = parent.textContent.substring(0, swIdx).trim();
                            if (beforeText) labelText = beforeText;
                        }
                    }
                }

                // 如果没有文本，使用默认名称
                if (!labelText) {
                    labelText = 'Switch ' + (idx + 1);
                }

                // 检查当前状态（优先使用 Vue 实例值，fallback 到 classList）
                let isChecked = false;
                const vue = sw.__vue__;
                if (vue && typeof vue.value !== 'undefined') {
                    // Vue 实例的 value 是实际状态
                    isChecked = vue.value === (vue.activeValue !== undefined ? vue.activeValue : true);
                } else {
                    // fallback: 检查自身和子元素的 is-checked
                    isChecked = sw.classList.contains('is-checked')
                        || !!sw.querySelector('.is-checked')
                        || sw.querySelector('.el-switch__core')?.classList.contains('is-checked');
                }

                // 检查是否在 tab-pane 内
                const tabPane = sw.closest('.el-tab-pane');
                let tabName = '';
                if (tabPane) {
                    // 尝试从 tab-pane 的 id 或 aria-label 获取名称
                    tabName = tabPane.getAttribute('aria-label') || '';
                    if (!tabName && tabPane.id) {
                        // pane-xxx → xxx
                        tabName = tabPane.id.replace('pane-', '');
                    }
                }

                // ★ 判断位置类型：优先检查是否在表格行内
                const tableRow = sw.closest('.el-table__row, .el-table__body tr, tr.el-table__row');
                const isInTableRow = !!tableRow;

                let location;
                if (isInTableRow) {
                    location = 'row';  // 表格行内的 switch，不参与业务操作验证
                } else if (formItem) {
                    location = 'form';  // 表单内的 switch
                } else {
                    location = 'toolbar';  // 工具栏区域的 switch
                }

                // ★ 构建唯一 selector（根据位置类型生成不同精度的选择器）
                let selector = '';

                if (location === 'row') {
                    // 行级 switch：不需要精确 selector（不参与验证）
                    selector = '.el-table__row .el-switch';
                } else if (location === 'toolbar' || location === 'form') {
                    // toolbar/form 级 switch：使用 tab pane ID 精确限定
                    // ★ 修复：避免使用 :not(.el-table__row .el-switch)（CSS :not() 不支持复杂选择器）
                    // 改为只使用 paneId 限定，switch_ops.py 的 JS 过滤会排除行级 switch
                    if (tabPane) {
                        const paneId = tabPane.id || '';
                        if (paneId) {
                            // 简化选择器：只限定 tab pane，switch_ops.py 会做行级过滤
                            selector = `#${paneId} .el-switch`;
                        }
                    }
                }

                // ★ 无 paneId 的 form/toolbar switch 不落盘 locator：
                // 禁全局序号定位（nth-of-type 是父内序号、多匹配、点错风险）。
                // 执行侧 switch_ops 按 label 文本运行时锚定（文本 + 相对路径）。


                results.push({
                    text: labelText,
                    locator: selector,
                    is_checked: isChecked,
                    tab: tabName,
                    location: location,
                    is_switch: true,  // 标记为 switch 类型
                    tag: 'SWITCH',
                });
            });

            return results;
        }""")

        LOG.debug(f"  _discover_switches: 发现 {len(raw_switches)} 个 switch")
        return raw_switches or []

    except Exception as e:
        LOG.warning(f"  _discover_switches 异常: {e}")
        return []


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
                        // ★ 查找当前可见的表格（多 Tab 页面有多个 table，需要选激活 Tab 的）
                        const allTables = document.querySelectorAll('.el-table, table, .ant-table');
                        let table = null;
                        for (const t of allTables) {{
                            if (t.offsetWidth > 0 && t.offsetHeight > 0) {{
                                table = t;
                                break;
                            }}
                        }}
                        if (!table && allTables.length > 0) {{
                            table = allTables[0];  // fallback: 没有可见的用第一个
                        }}
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
        // ★ 查找当前可见的表格（多 Tab 页面有多个 table，需要选激活 Tab 的）
        const allTables = document.querySelectorAll('.el-table, table, .ant-table');
        let table = null;
        for (const t of allTables) {{
            if (t.offsetWidth > 0 && t.offsetHeight > 0) {{
                table = t;
                break;
            }}
        }}
        if (!table && allTables.length > 0) {{
            table = allTables[0];  // fallback: 没有可见的用第一个
        }}
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
    # ★ 触发器判定改用 shared 单源包含式 helper（原硬编码精确匹配清单消除）：
    #   「更多操作」组合文本含「更多」即命中（EIS），既有精确文本行为不变（estack）
    from core.discovery.shared.constants import is_dropdown_trigger_text
    triggers = set()
    for btn in parent_buttons:
        text = btn["text"]
        # 硬编码触发器（兜底）
        if is_dropdown_trigger_text(text):
            triggers.add(text)

    # 动态发现：检查 .el-dropdown 元素（限定到表格/工具栏区域，排除导航菜单）
    parent_texts = [btn["text"] for btn in parent_buttons]
    dropdown_triggers = await page.evaluate("""(parentTexts) => {
        const results = [];
        const parentSet = new Set(parentTexts);
        // ★ 查找当前可见的表格（多 Tab 页面有多个 table，需要选激活 Tab 的）
        const allTables = document.querySelectorAll('.el-table, table, .ant-table');
        let table = null;
        for (const t of allTables) {
            if (t.offsetWidth > 0 && t.offsetHeight > 0) {
                table = t;
                break;
            }
        }
        if (!table && allTables.length > 0) {
            table = allTables[0];  // fallback
        }
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
        # --- 手势 1：DOM click（既有行为，兼容 click 触发的下拉，如工具栏批量操作）---
        clicked = await page.evaluate(f"""(text) => {{
            // ★ 查找当前可见的表格
            const allTables = document.querySelectorAll('.el-table, table, .ant-table');
            let table = null;
            for (const t of allTables) {{
                if (t.offsetWidth > 0 && t.offsetHeight > 0) {{
                    table = t;
                    break;
                }}
            }}
            if (!table && allTables.length > 0) {{
                table = allTables[0];
            }}
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

        # --- 收集可见菜单项（已有逻辑：可见 + 非 disabled + 非导航区）---
        _collect_js = """() => {
            const items = document.querySelectorAll('.el-dropdown-menu__item, .ant-dropdown-menu-item, [role="menuitem"]');
            return Array.from(items)
                .filter(el => {
                    if (el.offsetWidth <= 0) return false;
                    // ★ 过滤 disabled 项：有 Vue 实例时以 disabled prop 为准。
                    //   实证（黑白名单探针）：可操作的互斥项（行级'暂停'，offsetWidth=46 可见
                    //   且 fix5 真实点击成功）也带 cmss-disabled 样式类——按 class 过滤会把
                    //   唯一可用操作误杀，收集恒为 0；互斥不可用项由可见性（offW=0）表达。
                    //   无 Vue 实例（非 Vue 页面）保留 class 兜底，不丢旧行为。
                    if (el.__vue__) {
                        if (el.__vue__.disabled === true) return false;
                    } else if (el.classList.contains('cmss-disabled') || el.classList.contains('is-disabled')) {
                        return false;
                    }
                    const navParent = el.closest('.el-menu, .ant-menu, .sidebar-menu, nav');
                    return !navParent;
                })
                .map(el => el.textContent.trim())
                .filter(t => t);
        }"""

        items = []
        if clicked:
            try:
                await wait_for_dropdown(page, timeout=1500)
            except Exception:
                pass  # 菜单未出现 → items 为空，走 vue 兜底手势
            items = await page.evaluate(_collect_js)

        # --- 手势 2：Vue show() 兜底（hover 触发的行级下拉，如黑白名单「更多」）---
        # DOM click 对 hover 触发的 el-dropdown 不展开菜单（菜单项全部不可见 → 0 收集）。
        # 调触发器自身的 Vue 实例 show()/handleMouseEnter()，与 crud_executor 增量扫描
        # 同手法（fix3 实证：dropdown_expand={'method': 'vue_show'} 后 menuInfo 可见子项
        # 才出现）。
        # ★ 匹配分两遍：①精确 ===（旧行为）②前缀 startsWith 兜底——菜单渲染在 dd 内部
        #   时（黑白名单行级 dd textContent='更多 启用 暂停'，fix6 探针实证），=== 匹配
        #   不到任何 dd；前缀遍可识别此类触发器。
        # ★ 只收 visible 子项：互斥操作对（启用/暂停）DOM 同时渲染但仅当前行状态
        # 可用的一项可见，收 visible 保证第一个 generic op 点的是可用的那个。
        if not items:
            expand_result = await page.evaluate("""(triggerText) => {
                const candidates = Array.from(document.querySelectorAll('.el-dropdown'));
                const tryMatch = (matchFn) => {
                    for (const dd of candidates) {
                        if (dd.offsetWidth <= 0 || dd.offsetHeight <= 0) continue;
                        // 必须是同名触发器本身（防误展开其他下拉）
                        if (!matchFn((dd.textContent || '').trim())) continue;
                        // 排除导航区下拉
                        if (dd.closest('.el-menu, .ant-menu, nav, .sidebar-menu')) continue;
                        if (dd.__vue__) {
                            const vm = dd.__vue__;
                            if (typeof vm.show === 'function') {
                                vm.show();
                                return { expanded: true, method: 'vue_show' };
                            }
                            if (typeof vm.handleMouseEnter === 'function') {
                                vm.handleMouseEnter();
                                return { expanded: true, method: 'vue_handleMouseEnter' };
                            }
                        }
                        dd.dispatchEvent(new MouseEvent('mouseenter', { bubbles: false }));
                        dd.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                        return { expanded: true, method: 'dispatchEvent' };
                    }
                    return null;
                };
                // 第一遍：精确匹配（旧行为，不回归）
                const exact = tryMatch(t => t === triggerText);
                if (exact) return exact;
                // 第二遍：前缀匹配兜底（菜单内置渲染的 dd，textContent 含菜单项文本）
                const prefix = tryMatch(t => t.startsWith(triggerText));
                return prefix || { expanded: false, reason: 'no_visible_trigger' };
            }""", text)
            if expand_result.get("expanded"):
                LOG.info(f"  [dropdown] '{text}' DOM click 未展开菜单，Vue 兜底展开"
                         f" (method={expand_result.get('method')})")
                await page.wait_for_timeout(1200)  # 等 Vue 响应式更新 + 菜单动画
                items = await page.evaluate(_collect_js)
            else:
                LOG.debug(f"  [dropdown] '{text}' 无可见触发器，跳过")
                await page.keyboard.press("Escape")
                continue

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
        # 收起：优先 Vue hide()（vue_show 展开的菜单 ESC 可能不关），再 Escape
        await page.evaluate("""() => {
            document.querySelectorAll('.el-dropdown').forEach(dd => {
                if (dd.__vue__ && typeof dd.__vue__.hide === 'function') {
                    try { dd.__vue__.hide(); } catch (e) {}
                }
            });
        }""")
        await page.keyboard.press("Escape")
        # 等待下拉菜单消失（快速超时，失败也不阻塞）
        try:
            await wait_for_dialog_dismissed(page, timeout=1000)
        except Exception:
            pass
        # ★ 兜底收起（fix6 探针实证）：teleport 到 body 的菜单 popper 可见性与
        # dd vm.visible 解耦——假目标 dd.show() 可经共享 popper 复活行级菜单，
        # vm.hide()/Escape 均关不掉，dispatch mouseleave/mouseout 可可靠关闭；
        # 无残留时为 no-op，不影响旧行为。
        leaked = await page.evaluate("""() => Array.from(
            document.querySelectorAll('.el-dropdown-menu'))
            .filter(m => m.offsetWidth > 0 && m.offsetHeight > 0).length""")
        if leaked:
            await page.evaluate("""() => {
                document.querySelectorAll('.el-dropdown').forEach(dd => {
                    dd.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false }));
                    dd.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }));
                });
            }""")
            await page.wait_for_timeout(400)
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
            // ★ P0-3 对齐：selects 全数组口径（与 form_filler.scan_form_fields 同源）——
            // 独立 input 的归属判定须排除【所有】select 内部 input，而非仅第一个。
            const selects = Array.from(fi.querySelectorAll('.el-select, .ant-select'));
            // ★ F1: select 级可见性过滤——form-item 可见不代表其内部 select 可见
            //（预渲染隐藏槽位模板：如「增加一块普通网卡」的隐藏网卡行内 select）。
            // 隐藏 select 不入册（不推字段、不继承 required）；independentInputs
            // 的归属判定仍用全量 selects（隐藏 select 内的 input 同样不算独立 input）。
            const visibleSelects = selects.filter(s => s.offsetWidth > 0 && s.offsetHeight > 0);
            const selectEl = selects[0] || null;  // 兼容旧单 select 引用（下方 debug 信息）
            const allInputs = fi.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])');
            const independentInputs = Array.from(allInputs)
                .filter(inp => !inp.readOnly && inp.offsetWidth > 0
                    && !selects.some(s => s.contains(inp)));

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
                // 复合组件：el-select(s) + 独立 input
                // ★ P0-3 对齐：select 逐个推（单 select 加 (下拉) 后缀、多 select 加 (N)
                // 后缀），统一走 _buildSelector 产「语义锚+唯一性验证」的 selector；
                // 合成后缀字段不生成 label locator（DOM label 无后缀文本，:has-text
                // 不命中，P1-A 豁免）。
                visibleSelects.forEach((sel, idx) => {
                    const labelSuffix = visibleSelects.length > 1 ? '(' + (idx + 1) + ')' : '(下拉)';
                    fields.push(Object.assign({ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 required }, _buildSelector(sel, label + labelSuffix)));
                });
                independentInputs.forEach(inp => {
                    fields.push(Object.assign({ label, type: 'input', kb_category: 'input-generic',
                                 inputType: inp.type || 'text', required }, _buildSelector(inp, label)));
                });
            }
            else if (fi.querySelector('.el-cascader')) {
                fields.push(Object.assign({ label, type: 'cascader', kb_category: 'el-cascader',
                             required }, _buildComponentSelector(fi, '.el-cascader', fields.length, label)));
            } else if (fi.querySelector('.el-date-editor, .ant-picker')) {
                fields.push(Object.assign({ label, type: 'date-picker', kb_category: 'date-picker',
                             required }, _buildComponentSelector(fi, '.el-date-editor, .ant-picker', fields.length, label)));
            } else if (visibleSelects.length > 0) {
                // ★ P0-3 对齐：form-item 内多个 select（如授权主体：类型选择 + 实体选择）
                // 逐个输出（单 select 不加后缀、多 select 加 (N) 后缀），统一走 _buildSelector
                // 产「语义锚+唯一性验证」的 selector——多卡片布局下位置锚必然撞车。
                // ★ F1: 只推可见 select（隐藏槽位模板不入册；selects 全量仅作归属判定）。
                visibleSelects.forEach((sel, idx) => {
                    const labelSuffix = visibleSelects.length > 1 ? '(' + (idx + 1) + ')' : '';
                    fields.push(Object.assign({ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 required }, _buildSelector(sel, label + labelSuffix)));
                });
            } else if (fi.querySelector('.el-radio-group')) {
                const firstRadio = fi.querySelector('.el-radio-button__inner, .el-radio__label');
                fields.push(Object.assign({ label, type: 'radio', kb_category: 'radio',
                             required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' },
                             _buildComponentSelector(fi, '.el-radio-group', fields.length, label)));
            } else if (fi.querySelector('.el-checkbox') && !fi.closest('.el-table')) {
                fields.push(Object.assign({ label, type: 'checkbox', kb_category: 'form-checkbox',
                             required }, _buildComponentSelector(fi, '.el-checkbox', fields.length, label)));
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

                    fields.push(Object.assign({ label, type: 'input', kb_category: 'input-generic',
                                 inputType: mainInput.type || 'text', required }, _buildSelector(mainInput, label)));
                } else if (allInputs.length === 1) {
                    // 单个 input
                    const inp = allInputs[0];
                    fields.push(Object.assign({ label, type: 'input', kb_category: 'input-generic',
                                 inputType: inp.type || 'text', required }, _buildSelector(inp, label)));
                } else if (ta) {
                    fields.push(Object.assign({ label, type: 'textarea', kb_category: 'textarea-generic',
                                 required }, _buildSelector(ta, label)));
                }
            }
        });

        // 辅助函数：生成双定位器（P0-3 对齐版，与 form_filler.scan_form_fields 同源）
        // selector = 「语义容器锚+文档内唯一性验证+上爬补锚」的 CSS DOM path；
        // playwright_locator = label 锚（按元素类型分支，合成后缀豁免）。
        function _buildSelector(element, labelText) {
            if (!element) return { selector: null, playwright_locator: null };
            // 优先级 1: #id（最稳定）——仅文档内唯一时直用；id 冲突（实证: 关闭后
            // 残留挂载的 dialog/drawer 与当前表单共用组件 id）→ 限定到弹窗容器
            // 重试唯一性（与 form_filler._buildSelector 同源同款）。
            if (element.id) {
                const bareId = `#${element.id}`;
                if (document.querySelectorAll(bareId).length === 1) {
                    return { selector: bareId, playwright_locator: bareId };
                }
                const DIALOG_CONTAINERS = ['el-dialog__wrapper', 'el-drawer__wrapper', 'el-drawer', 'el-dialog'];
                let scopeNode = element.parentElement;
                while (scopeNode && scopeNode.nodeType === Node.ELEMENT_NODE) {
                    const cls = scopeNode.classList;
                    if (cls) {
                        const hit = DIALOG_CONTAINERS.find(c => cls.contains(c));
                        if (hit) {
                            const plain = `.${hit} ${bareId}`;
                            if (document.querySelectorAll(plain).length === 1) {
                                return { selector: plain, playwright_locator: plain };
                            }
                            const filtered = `.${hit}:not([style*="display: none"]) ${bareId}`;
                            if (document.querySelectorAll(filtered).length === 1) {
                                return { selector: filtered, playwright_locator: filtered };
                            }
                        }
                    }
                    scopeNode = scopeNode.parentElement;
                }
                // id 冲突且容器限定仍不唯一 → 继续走优先级 2/3
            }
            // 优先级 2: [name] 属性
            if (element.name) return { selector: `[name="${element.name}"]`, playwright_locator: `[name="${element.name}"]` };

            // 优先级 3a: DOM path——语义容器优先必达（P0-3 第二案）：
            // 遇 el-form-item 等语义容器以其为根，无条件带位置锚；锚+下段路径经
            // 文档内唯一性验证通过才截断，不唯一则上爬逐层补锚（深度上限 12 仅作
            // 保险丝）。多卡片布局（el-card 逐卡重置 nth-child）下位置锚必然撞车，
            // 唯一性验证是唯一可靠截断依据。
            const SEMANTIC_CONTAINERS = ['el-form-item', 'el-tab-pane', 'el-dialog', 'el-drawer', 'el-table__row'];
            const path = [];
            let current = element;
            let depth = 0;
            let anchorMode = false;
            while (current && current.nodeType === 1 && depth < 12) {
                if (current.id) {
                    path.unshift(`#${current.id}`);
                    break;
                }
                let sibling = current;
                let nth = 1;
                while (sibling.previousElementSibling) {
                    sibling = sibling.previousElementSibling;
                    if (sibling.nodeName === current.nodeName) nth++;
                }
                const suffix = path.length ? ' > ' + path.join(' > ') : '';
                const containerCls = SEMANTIC_CONTAINERS.find(sc => current.classList.contains(sc));
                if (containerCls) {
                    anchorMode = true;
                    const anchor = `.${containerCls}:nth-of-type(${nth})`;
                    path.unshift(anchor);
                    if (document.querySelectorAll(anchor + suffix).length === 1) {
                        break;
                    }
                    current = current.parentElement;
                    depth++;
                    continue;
                }
                // 下段层（首个语义容器之前）: 原省略优化构建，不做唯一性验证
                if (!anchorMode) {
                    let plain = current.nodeName.toLowerCase();
                    if (nth > 1 || current.nextElementSibling) {
                        plain += `:nth-of-type(${nth})`;
                    }
                    path.unshift(plain);
                    current = current.parentElement;
                    depth++;
                    continue;
                }
                // 上爬层（语义锚不唯一后）: 累计路径唯一即截断；省略版不唯一时
                // 强制带 nth-of-type 重试，仍不唯一才继续上爬。
                let selectorSeg = current.nodeName.toLowerCase();
                if (nth > 1 || current.nextElementSibling) {
                    selectorSeg += `:nth-of-type(${nth})`;
                }
                if (document.querySelectorAll(selectorSeg + suffix).length === 1) {
                    path.unshift(selectorSeg);
                    break;
                }
                const forced = current.nodeName.toLowerCase() + `:nth-of-type(${nth})`;
                path.unshift(forced);
                if (document.querySelectorAll(forced + suffix).length === 1) {
                    break;
                }
                current = current.parentElement;
                depth++;
            }
            const domPath = path.join(' > ');

            // 优先级 3b: Label-based（Playwright 语法，跨会话稳定）
            // 合成后缀（"(N)"/"(下拉)"/"(掩码)"）是扫描器加的标记，DOM label 无
            // 此文本，:has-text 永不命中——豁免不生成，强制走 selector（P1-A）。
            // 按元素类型分支（诊断实证）：
            // - select 组件 → 组件级 .el-select（label 锚唯一性最好，避免 select 内
            //   readonly input 多命中）；
            // - textarea → textarea；
            // - 普通 input → input:not([readonly])（复合 form-item 内排除 select 的
            //   readonly input，防 strict violation 多命中）。
            let playwrightLocator = null;
            if (labelText && labelText.length > 0
                    && !/\((掩码|下拉|\d+)\)$/.test(labelText.trim())) {
                const escaped = labelText.replace(/"/g, '\\"');
                if (element.classList && (element.classList.contains('el-select') || element.classList.contains('ant-select'))) {
                    playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${escaped}")) .el-select`;
                } else if (element.tagName === 'TEXTAREA') {
                    playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${escaped}")) textarea`;
                } else {
                    playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${escaped}")) input:not([readonly])`;
                }
            }

            return { selector: domPath, playwright_locator: playwrightLocator };
        }

        // 辅助函数：为复杂组件生成双定位器（cascader/date-picker/radio/checkbox）
        function _buildComponentSelector(formItem, componentSelector, fieldIndex, labelText) {
            const component = formItem.querySelector(componentSelector);
            if (!component) return { selector: null, playwright_locator: null };

            // selector: formItem 在直接父元素中的位置定位（标准 CSS，evaluate 安全）
            // 注意：children 只返回直接子元素；多卡片表单下位置锚可能不唯一——
            // 此时由 playwright_locator（label 锚）优先兜底（消费侧约定）。
            const directChildren = formItem.parentElement.children;
            let nth = 1;
            for (let i = 0; i < directChildren.length; i++) {
                if (directChildren[i] === formItem) {
                    nth = i + 1;
                    break;
                }
            }
            const domPath = '.el-form-item:nth-child(' + nth + ') ' + componentSelector;

            // playwright_locator: label 锚
            let playwrightLocator = null;
            if (labelText && labelText.length > 0) {
                const escaped = labelText.replace(/"/g, '\\"');
                playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${escaped}")) ${componentSelector}`;
            }

            return { selector: domPath, playwright_locator: playwrightLocator };
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
    """获取页面 DOM 骨架，识别表格结构（含固定列位置、操作列索引）和 tab 结构。

    输出供 Stage 2 选择正确的 wrapper 进行行操作。
    新增: tab 检测（el-tabs/ant-tabs），用于多 tab 页面的迭代扫描。
    """
    try:
        result = await page.evaluate("""() => {
            const result = {};

            // ---- Tab 检测调试 ----
            const tabDebug = {
                elTabsHeader: !!document.querySelector('.el-tabs__header'),
                elTabsNavWrap: !!document.querySelector('.el-tabs__nav-wrap'),
                elTabs: !!document.querySelector('.el-tabs'),
                antTabsNav: !!document.querySelector('.ant-tabs-nav'),
                roleTablist: !!document.querySelector('[role="tablist"]'),
                elTabsItems: document.querySelectorAll('.el-tabs__item').length,
                roleTab: document.querySelectorAll('[role="tab"]').length,
            };
            result._tabDebug = tabDebug;

            // ---- Tab 检测 ----
            const tablist = document.querySelector(
                '.el-tabs__header, .ant-tabs-nav, [role="tablist"]'
            );
            if (tablist) {
                const tabs = tablist.querySelectorAll(
                    '[role="tab"], .el-tabs__item, .ant-tabs-tab'
                );
                result.tabs = Array.from(tabs).map((t, i) => ({
                    name: t.textContent.trim(),
                    active: t.classList.contains('is-active')
                        || t.classList.contains('ant-tabs-tab-active')
                        || t.getAttribute('aria-selected') === 'true',
                    index: i,
                    id: t.id || '',
                    // 无 id 不落盘 locator（禁 nth-child 编号定位）：
                    // 执行/回放侧统一按 tab_name 文本定位（tab_helpers / _step_click_tab）
                    locator: t.id ? '#' + t.id : '',
                }));
            }

            // ---- 表格结构 ----
            const table = document.querySelector('.el-table');
            if (table) {
                let operationColumnIndex = -1;
                const headerCells = table.querySelectorAll('.el-table__header-wrapper th');
                headerCells.forEach((cell, idx) => {
                    const text = cell.textContent.trim();
                    if (text.includes('操作') || text.includes('Action')) {
                        operationColumnIndex = idx;
                    }
                });

                const wrappers = [];
                table.querySelectorAll('[class*="wrapper"]').forEach(w => {
                    wrappers.push({
                        cls: w.className.slice(0, 80),
                        rowCount: w.querySelectorAll('tbody tr').length,
                    });
                });

                result.hasFixedLeft = !!table.querySelector('.el-table__fixed');
                result.hasFixedRight = !!table.querySelector('.el-table__fixed-right');
                result.mainBodyRows = table.querySelectorAll('.el-table__body-wrapper tbody tr').length;
                result.fixedRightRows = table.querySelectorAll('.el-table__fixed-right .el-table__fixed-body-wrapper tbody tr').length;
                result.fixedLeftRows = table.querySelectorAll('.el-table__fixed .el-table__fixed-body-wrapper tbody tr').length;
                result.columnCount = headerCells.length;
                result.operationColumnIndex = operationColumnIndex;
                result.operationColumnLocation = operationColumnIndex === headerCells.length - 1 ? 'last' :
                    operationColumnIndex === 0 ? 'first' : operationColumnIndex >= 0 ? 'middle' : 'none';
                result.tableWrappers = wrappers.slice(0, 10);
            }

            return result;
        }""")
        # 输出 tab 调试信息
        tab_debug = result.get("_tabDebug", {})
        if tab_debug:
            LOG.info(f"  [Tab调试] elTabsHeader={tab_debug.get('elTabsHeader')}, "
                     f"elTabsNavWrap={tab_debug.get('elTabsNavWrap')}, "
                     f"elTabs={tab_debug.get('elTabs')}, "
                     f"roleTablist={tab_debug.get('roleTablist')}, "
                     f"elTabsItems={tab_debug.get('elTabsItems')}, "
                     f"roleTab={tab_debug.get('roleTab')}")
        if not result.get("tabs"):
            LOG.warning(f"  [Tab调试] tablist 选择器未匹配，无法检测 tab")
        return result if result else {}
    except Exception as e:
        LOG.warning(f"页面结构快照失败: {e}")
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
