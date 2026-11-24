"""
crud_executor.py — CRUD operation execution for Stage 1.

Contains the main discover_and_validate entry point, business flow validation,
error-driven retry logic, and individual CRUD operation executors
(create, query, detail, edit, delete, save/confirm).
"""

import copy
import logging
import time
from core.discovery import const
from core.discovery.shared.constants import (
    NON_BUSINESS_BUTTONS, BATCH_PREFIXES, DESTROY_KEYWORDS,
    is_dropdown_trigger_text,
)
from core.discovery.replay.wait_helpers import (
    wait_for_loading_complete, wait_for_table_ready,
    wait_for_navigation_complete,
)
from core.discovery.replay.form_filler import (
    FormFiller, read_form_errors,
)
from core.discovery.shared import generate_fill_data, generate_fill_rules
from core.discovery.shared.button_clicker import click_page_button
from core.discovery.operation_executor.base_executor import (
    _install_message_capture, _get_captured_messages,
    _verify_operation_success, _check_data_changed,
    _extract_fallback_marker, _ensure_row_selected, _try_fill_empty_selects,
    _check_create_api_triggered_simple, _ensure_switch_enabled,
    _extract_business_failure, _observe_post_click, _setup_new_tab_listener,
)
from core.discovery.operation_executor.result_factory import (
    make_error, make_success, click_failed, row_not_found, no_dialog,
    submit_failed, no_success_signal, api_error, form_validation,
    no_fields, no_locator, skipped, exception,
)
from core.discovery.ui_scanner.button_detector import (
    _infer_action_role, _find_create_delete_actions,
)
from core.discovery.shared import get_row_selector_by_marker

LOG = logging.getLogger("crud_executor")


def _extract_radio_fields_from_nav(nav_info: dict) -> list:
    """从 nav_info.form_fields 中提取 radio 字段到顶层。

    Args:
        nav_info: 导航探索返回的信息字典

    Returns:
        radio_field_details 列表
    """
    radio_details = []
    form_fields = nav_info.get("form_fields", [])

    for field in form_fields:
        if field.get("kb_category") == "radio":
            radio_details.append({
                "label": field.get("label"),
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": field.get("firstOptionText", ""),
                "fill_rule": field.get("fill_rule", {}),
            })
            # 传递 cascade_actions（如 radio 触发 transfer-box）
            if "cascade_actions" in field:
                radio_details[-1]["cascade_actions"] = field["cascade_actions"]
            # ★ 透传 radio 回放元数据（interaction_steps / all_options / 显隐字段 diff）
            # 空列表 [] 也要保留（= 探测过但无变化），仅键缺失/None 时跳过
            _dst = radio_details[-1]
            for _k in ("interaction_steps", "all_options", "revealed_fields", "hidden_fields"):
                if field.get(_k) is not None:
                    _dst[_k] = field[_k]

    return radio_details


class ButtonStateTracker:
    """按钮状态追踪器

    记录所有按钮的 enabled/disabled 状态，检测状态变化，
    自动执行从 disabled → enabled 的按钮（支持级联）。
    """

    def __init__(self, tab_prefix: str = ""):
        self.tab_prefix = tab_prefix
        self.button_states = {}  # {button_text: is_enabled}
        self.executed_buttons = set()  # 已执行的按钮

    def update_states(self, buttons: list):
        """更新按钮状态

        Args:
            buttons: 按钮列表，每个按钮包含 text 和 disabled 字段
        """
        for btn in buttons:
            text = btn.get("text", "")
            disabled = btn.get("disabled", False)
            if text:
                self.button_states[text] = not disabled

    def get_newly_enabled(self) -> list:
        """获取新启用的按钮（从 disabled → enabled）

        Returns:
            list: 新启用的按钮文本列表
        """
        newly_enabled = []
        for text, is_enabled in self.button_states.items():
            if is_enabled and text not in self.executed_buttons:
                # 检查是否是新启用的（之前是 disabled 或不存在）
                # 这里简化处理：只要未执行且当前启用，就认为是新启用
                newly_enabled.append(text)
        return newly_enabled

    def mark_executed(self, button_text: str):
        """标记按钮已执行"""
        self.executed_buttons.add(button_text)


async def _scan_row_after_operation(page, row_selector: str, marker: str, existing_actions: set) -> dict:
    """操作执行后，增量扫描当前行，发现新出现的按钮和文本变化的按钮。

    用于捕获状态依赖的按钮，如：
    - "暂停"操作成功后，"启用"按钮出现
    - "冻结"操作成功后，"解冻"按钮出现
    - "锁定"按钮文本变为"解锁"（状态切换按钮对）

    Args:
        page: Playwright Page 对象
        row_selector: 行的 CSS 选择器
        marker: 行标识（用于验证行仍然存在）
        existing_actions: 已知的操作按钮文本集合（用于去重）

    Returns:
        dict: {
            'new_buttons': list,  # 新发现的按钮
            'disappeared_buttons': list,  # 消失的按钮（可能是状态切换）
            'all_current_texts': set  # 当前所有按钮文本（用于诊断）
        }
    """
    result = {'new_buttons': [], 'disappeared_buttons': [], 'all_current_texts': set()}
    try:
        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] 开始扫描: row_selector={row_selector}, marker={marker}")

        # 1. 验证行仍然存在
        row_exists = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            return row ? true : false;
        }}""")

        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] row_exists={row_exists}")

        if not row_exists:
            LOG.debug(f"  [incremental-scan] 行不存在（可能已删除），跳过扫描")
            return result

        # 2. 扫描当前行的所有操作按钮（直接可见的 .el-button）
        scan_result = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return {{ buttons: [], allTexts: [] }};

            const buttons = [];
            const allTexts = [];
            const existingTexts = {list(existing_actions)};

            // 扫描直接可见的按钮（.el-button）
            const directBtns = row.querySelectorAll('.cell .el-button');
            directBtns.forEach(btn => {{
                const text = btn.textContent.trim();
                if (text) {{
                    allTexts.push(text);
                    if (!existingTexts.includes(text)) {{
                        const rect = btn.getBoundingClientRect();
                        buttons.push({{
                            text: text,
                            tag: 'BUTTON',
                            location: 'row_action',
                            selector: `button:has-text("${{text}}")`,
                            rect: {{ width: rect.width, height: rect.height }},
                            is_new: true,
                        }});
                    }}
                }}
            }});

            return {{ buttons, allTexts }};
        }}""")

        new_buttons = scan_result.get('buttons', [])
        all_current_texts = set(scan_result.get('allTexts', []))

        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] direct_buttons_found={[b.get('text') for b in new_buttons]}")

        # 3. 过滤掉不可见的按钮
        visible_buttons = [
            btn for btn in new_buttons
            if btn.get("rect", {}).get("width", 0) > 0
        ]

        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] visible_buttons={[b.get('text') for b in visible_buttons]}")

        # 4. ★ FIX-2: 扫描 dropdown 菜单（Element UI 将菜单 teleport 到 body）
        #    使用 Vue 实例的 show() 方法展开菜单（与 click_row_more_item 相同方式）
        dropdown_locator_str = f"{row_selector} .el-dropdown"

        expand_result = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return {{ expanded: false, reason: 'no_row' }};

            const dropdown = row.querySelector('.el-dropdown');
            if (!dropdown) return {{ expanded: false, reason: 'no_dropdown' }};

            // 记录 dropdown 索引，用于后续精确定位 menu
            const dropdownIndex = Array.from(document.querySelectorAll('.el-dropdown')).indexOf(dropdown);

            // 方法 1: 调用 Vue 实例的 show/handleMouseEnter
            if (dropdown.__vue__) {{
                const vm = dropdown.__vue__;
                if (typeof vm.show === 'function') {{
                    vm.show();
                    return {{ expanded: true, method: 'vue_show', dropdownIndex }};
                }}
                if (typeof vm.handleMouseEnter === 'function') {{
                    vm.handleMouseEnter();
                    return {{ expanded: true, method: 'vue_handleMouseEnter', dropdownIndex }};
                }}
            }}

            // 方法 2: 派发 mouseenter + mouseover 事件
            dropdown.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: false }}));
            dropdown.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
            return {{ expanded: true, method: 'dispatchEvent', dropdownIndex }};
        }}""")

        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] dropdown_expand={expand_result}")

        # 等待菜单动画完成（增加等待时间，确保 Vue 响应式更新完成）
        await page.wait_for_timeout(1500)

        dropdown_index = expand_result.get('dropdownIndex')

        # 检查是否有可见的 dropdown 菜单（只检查目标行的 menu）
        has_visible_menu = await page.evaluate(f"""(dropdownIndex) => {{
            // 通过 dropdown 索引定位关联的 menu
            const dropdowns = document.querySelectorAll('.el-dropdown');
            if (typeof dropdownIndex === 'number' && dropdownIndex < dropdowns.length) {{
                const dropdown = dropdowns[dropdownIndex];
                const menu = dropdown.querySelector('.el-dropdown-menu')
                    || dropdown.parentElement?.querySelector('.el-dropdown-menu');
                if (menu && menu.offsetWidth > 0 && menu.offsetHeight > 0) return true;
            }}
            // 回退：检查任何可见 menu
            const menus = document.querySelectorAll('.el-dropdown-menu');
            return Array.from(menus).some(m => m.offsetWidth > 0 && m.offsetHeight > 0);
        }}""", dropdown_index)

        LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] has_visible_menu={has_visible_menu}")

        if has_visible_menu:
            # ★ 只扫描目标行的 dropdown menu（避免混入其他行的按钮）
            dropdown_buttons = await page.evaluate(f"""(dropdownIndex) => {{
                const existingTexts = {list(existing_actions)};
                const buttons = [];
                const allItems = [];

                // 通过 dropdown 索引定位目标 menu
                let targetMenu = null;
                const dropdowns = document.querySelectorAll('.el-dropdown');
                if (typeof dropdownIndex === 'number' && dropdownIndex < dropdowns.length) {{
                    const dropdown = dropdowns[dropdownIndex];
                    targetMenu = dropdown.querySelector('.el-dropdown-menu')
                        || dropdown.parentElement?.querySelector('.el-dropdown-menu');
                }}

                // 回退：找第一个可见的 menu
                if (!targetMenu) {{
                    const menus = document.querySelectorAll('.el-dropdown-menu');
                    for (const menu of menus) {{
                        if (menu.offsetWidth > 0 && menu.offsetHeight > 0) {{
                            targetMenu = menu;
                            break;
                        }}
                    }}
                }}

                // 收集所有 menu 信息（诊断用）
                const menuInfo = [];
                document.querySelectorAll('.el-dropdown-menu').forEach((menu, idx) => {{
                    const items = [];
                    menu.querySelectorAll('.el-dropdown-menu__item').forEach(item => {{
                        items.push(item.textContent.trim());
                    }});
                    menuInfo.push({{
                        idx: idx,
                        visible: menu.offsetWidth > 0 && menu.offsetHeight > 0,
                        width: menu.offsetWidth,
                        height: menu.offsetHeight,
                        items: items
                    }});
                }});

                if (targetMenu) {{
                    targetMenu.querySelectorAll('.el-dropdown-menu__item').forEach(item => {{
                        const text = item.textContent.trim();
                        allItems.push(text);
                        // ★ 可见性过滤（fix7）：互斥操作对（启用/暂停）DOM 同时渲染但仅
                        // 当前行状态可用的一项可见（offsetWidth>0）。隐藏项（offW=0）不
                        // 可点击——入册会导致"先点隐藏的启用→弹框/状态错配→no_signal
                        // 假失败"（fix6 黑名单/启用回归实证）。等真实状态翻转后增量扫描
                        // 再次触发，隐藏项变可见再入册，顺序自然正确。
                        if (item.offsetWidth <= 0) return;
                        // ★ disabled 判定：有 Vue 实例以 disabled prop 为准（fix6 探针
                        // 实证：可操作的互斥项也带 cmss-disabled 样式类，按 class 过滤
                        // 会误杀唯一可用项）；无 Vue 实例保留 class 兜底。
                        if (item.__vue__) {{
                            if (item.__vue__.disabled === true) return;
                        }} else if (item.classList.contains('cmss-disabled') || item.classList.contains('is-disabled')) return;
                        if (text && !existingTexts.includes(text)) {{
                            buttons.push({{
                                text: text,
                                tag: 'DROPDOWN_ITEM',
                                location: 'dropdown',
                                selector: `.el-dropdown-menu__item:has-text("${{text}}")`,
                                is_new: true,
                            }});
                        }}
                    }});
                }}

                return {{ buttons: buttons, menuInfo: menuInfo, existingTexts: existingTexts, allItems: allItems }};
            }}""", dropdown_index)

            LOG.info(f"  [INCREMENTAL-SCAN-DEBUG] dropdown_scan_result={dropdown_buttons}")

            if isinstance(dropdown_buttons, dict):
                visible_buttons.extend(dropdown_buttons.get('buttons', []))
                # ★ 问题二：收集 dropdown 中的当前按钮文本
                all_current_texts.update(dropdown_buttons.get('allItems', []))
            else:
                visible_buttons.extend(dropdown_buttons)

        # ★ 问题二：检测消失的按钮（可能是状态切换导致的文本变化）
        # 例如：执行"禁用"后，"禁用"按钮消失，"启用"按钮出现
        # 例如：执行"锁定"后，"锁定"按钮消失，"解锁"按钮出现
        disappeared_buttons = []
        if existing_actions and all_current_texts:
            # 直接比较前后差异，找出消失的按钮
            disappeared_buttons = [text for text in existing_actions if text not in all_current_texts]

            if disappeared_buttons:
                LOG.info(f"  [INCREMENTAL-SCAN] 检测到按钮文本变化:")
                LOG.info(f"    消失的按钮: {disappeared_buttons}")
                LOG.info(f"    新出现的按钮: {[b.get('text') for b in visible_buttons] if visible_buttons else []}")
                LOG.info(f"    当前所有按钮: {all_current_texts}")

        result['new_buttons'] = visible_buttons
        result['disappeared_buttons'] = disappeared_buttons
        result['all_current_texts'] = all_current_texts

        if visible_buttons:
            LOG.info(f"  [incremental-scan] 发现 {len(visible_buttons)} 个新按钮: "
                     f"{[b['text'] for b in visible_buttons]}")

        return result

    except Exception as e:
        LOG.warning(f"  [incremental-scan] 扫描失败: {e}")
        return {'new_buttons': [], 'disappeared_buttons': [], 'all_current_texts': set()}


async def discover_and_validate(page, username: str = "test",
                                state_seed: dict = None) -> dict:
    """Stage 1 入口：探测 + 业务闭环验证。

    流程：
    1. discover_all() — 探测所有按钮/字段
    2. _validate_business_flow() — 按 CRUD 顺序逐一验证每个按钮
    3. 返回增强后的 ui_result（含 validated_operations）

    Args:
        page: Playwright Page 对象
        username: 用于生成测试数据的用户名
        state_seed: 旧 ui.json 的状态矩阵/转移边种子（批一 1.3 跨轮累积；
            None=冷启动）

    Returns:
        dict: 增强后的 ui_result，含 validated_operations 字段。
              如果关键操作验证失败，返回 None。
    """
    # Phase A: 标准探测
    from core.discovery.ui_scanner.element_scanner import discover_all
    ui_result = await discover_all(page)

    # Phase B: 业务闭环验证
    validated, execution_order = await _validate_business_flow(
        page, ui_result, username, state_seed=state_seed)
    if validated is None:
        # 关键操作验证失败
        return None

    ui_result["validated_operations"] = validated
    ui_result["execution_order"] = execution_order

    # 清理空 tab 字段（不污染输出）
    for op_name, op_data in validated.items():
        if isinstance(op_data, dict) and "tab" in op_data and not op_data["tab"]:
            del op_data["tab"]

    return ui_result


def _merge_validated_result(validated: dict, key: str, result) -> object:
    """validated 写入收口：已有 success 结果的条目拒绝被失败结果覆盖。

    背景（EIS 第 6 轮实证）：级联（STATE-CHANGE）先行成功的操作写入
    validated 后，主循环按收集序到达同一操作时重新执行，撞上状态翻转
    后的禁用态，失败结果把先前的成功结果覆盖——终态 5/36 中 6 个本应
    success 的操作被抹成 skipped，产物损失。

    语义：
    - 已有 success=True 且新结果非 success → 拒绝写入（保留成功），打日志
    - 其余情况（首次写入 / 成功覆盖失败 / 失败覆盖失败）→ 正常写入

    Args:
        validated: 操作结果字典
        key: 操作名
        result: 待写入结果

    Returns:
        实际生效的结果（被拒绝时返回已存的成功结果）
    """
    existing = validated.get(key)
    if (isinstance(existing, dict) and existing.get("success")
            and isinstance(result, dict) and not result.get("success")):
        LOG.info(f"    [MERGE-GUARD] '{key}' 已有成功结果，拒绝失败结果覆盖 "
                 f"({result.get('error_type', '?')}: {(result.get('error_text') or '')[:60]})")
        return existing
    validated[key] = result
    return result


async def _scan_row_state_snapshot(page, created_marker: str) -> dict:
    """扫描当前行按钮状态 + 读行权威状态（批一 1.1/1.3 基座）。

    状态读取路径：展开目标行 dropdown 菜单 → 从菜单项 Vue 实例读
    command.row.status（同时顺带 qgaReady）。不依赖状态列位置（不硬编码
    列索引），走的是 Element UI dropdown command 模式的通用数据通道；
    estack 行数据同构（第 4 轮 estack 启用操作日志同样有 command.row），
    读不到时 status=None 静默降级，不破坏任何现有环境。

    Args:
        page: Playwright Page
        created_marker: 行标识

    Returns:
        dict: {"states": {button_text: is_enabled},
               "status": str|None, "qga_ready": bool|None}
    """
    row_selector = await get_row_selector_by_marker(page, created_marker)
    if not row_selector:
        return {"states": {}, "status": None, "qga_ready": None}

    try:
        result = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return {{}};

            const states = {{}};

            // 1. 行内直接按钮
            row.querySelectorAll('.cell .el-button').forEach(btn => {{
                const text = btn.textContent.trim();
                if (text) {{
                    const disabled = btn.disabled
                        || btn.classList.contains('cmss-disabled')
                        || btn.classList.contains('is-disabled');
                    states[text] = !disabled;
                }}
            }});

            return states;
        }}""")

        # 2. 展开 dropdown 并扫描内部按钮（顺带读行权威状态，批一 1.1）
        dropdown_expand = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return {{ error: 'no_row' }};

            const dropdown = row.querySelector('.el-dropdown');
            if (!dropdown) return {{ error: 'no_dropdown' }};

            const vm = dropdown.__vue__;
            if (vm && typeof vm.show === 'function') {{
                vm.show();
            }} else {{
                dropdown.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: false }}));
                dropdown.dispatchEvent(new MouseEvent('mouseover', {{ bubbles: true }}));
            }}

            // 获取此 dropdown 关联的 menu 元素标识
            const menu = dropdown.querySelector('.el-dropdown-menu')
                || dropdown.parentElement?.querySelector('.el-dropdown-menu');
            const menuId = menu ? menu.id || menu.getAttribute('data-popper-id') : null;
            const dropdownIndex = Array.from(document.querySelectorAll('.el-dropdown')).indexOf(dropdown);

            return {{ expanded: true, menuId, dropdownIndex }};
        }}""")

        if dropdown_expand and dropdown_expand.get('expanded'):
            await page.wait_for_timeout(800)

            # 扫描可见的 dropdown menu（只扫描目标行的 menu）
            menu_id = dropdown_expand.get('menuId')
            dropdown_index = dropdown_expand.get('dropdownIndex')
            dropdown_states = await page.evaluate(f"""(params) => {{
                const menuId = params.menuId;
                const dropdownIndex = params.dropdownIndex;
                const states = {{}};
                let targetMenu = null;

                // 优先通过 menuId 定位
                if (menuId) {{
                    targetMenu = document.getElementById(menuId)
                        || document.querySelector(`[data-popper-id="${{menuId}}"]`);
                }}

                // 回退：通过 dropdown 索引定位
                if (!targetMenu && typeof dropdownIndex === 'number') {{
                    const dropdowns = document.querySelectorAll('.el-dropdown');
                    if (dropdownIndex < dropdowns.length) {{
                        const dropdown = dropdowns[dropdownIndex];
                        targetMenu = dropdown.querySelector('.el-dropdown-menu')
                            || dropdown.parentElement?.querySelector('.el-dropdown-menu');
                    }}
                }}

                // 最后回退：找第一个可见的 menu
                if (!targetMenu) {{
                    const menus = document.querySelectorAll('.el-dropdown-menu');
                    for (const menu of menus) {{
                        if (menu.offsetWidth > 0 && menu.offsetHeight > 0) {{
                            targetMenu = menu;
                            break;
                        }}
                    }}
                }}

                let rowStatus = null;
                let qgaReady = null;
                if (targetMenu) {{
                    targetMenu.querySelectorAll('.el-dropdown-menu__item').forEach(item => {{
                        const text = item.textContent.trim();
                        if (text) {{
                            const disabled = item.classList.contains('cmss-disabled')
                                || item.classList.contains('is-disabled')
                                || (item.__vue__ && item.__vue__.disabled === true);
                            states[text] = !disabled;
                        }}

                        // ★ 批一 1.1：菜单项 Vue command.row → 行权威状态
                        //   command 形态兼容：{{row: {{...}}}} / 直接行对象 / vm.row
                        const vm = item.__vue__;
                        if (!vm) return;
                        let row = null;
                        const cmd = vm.command;
                        if (cmd && typeof cmd === 'object') {{
                            if (cmd.row && typeof cmd.row === 'object') row = cmd.row;
                            else if (cmd.status !== undefined || cmd.qgaReady !== undefined) row = cmd;
                        }}
                        if (!row && vm.row && typeof vm.row === 'object') row = vm.row;
                        if (!row) return;
                        if (rowStatus === null && row.status !== undefined && row.status !== null) {{
                            rowStatus = String(row.status);
                        }}
                        if (qgaReady === null && row.qgaReady !== undefined) {{
                            qgaReady = (row.qgaReady === true);
                        }}
                    }});
                }}

                return {{states, rowStatus, qgaReady}};
            }}""", {"menuId": menu_id, "dropdownIndex": dropdown_index})

            result.update(dropdown_states.get("states") or {})
            return {
                "states": result,
                "status": dropdown_states.get("rowStatus"),
                "qga_ready": dropdown_states.get("qgaReady"),
            }

        return {"states": result, "status": None, "qga_ready": None}

    except Exception as e:
        LOG.warning(f"  [scan_button_states] 扫描失败: {e}")
        return {"states": {}, "status": None, "qga_ready": None}


async def _scan_all_button_states(page, created_marker: str) -> dict:
    """扫描当前行所有按钮的 enabled/disabled 状态（含 dropdown 内的按钮）。

    （批一 1.1 重构后为 _scan_row_state_snapshot 的 states 投影，保持既有
    签名与返回形态，STATE-INIT / 级联等既有消费点零改动复用。）

    Returns:
        dict: {button_text: is_enabled}
    """
    snapshot = await _scan_row_state_snapshot(page, created_marker)
    return snapshot.get("states") or {}


async def _read_row_status(page, created_marker: str) -> dict:
    """行状态权威读取 helper（批一 1.1）。

    展开目标行 dropdown 菜单 → 从菜单项 Vue 实例读 command.row.status，
    同时顺带读 qgaReady。读不到（非 dropdown command 环境 / 行不可达）
    → status=None 静默降级，不硬失败。

    Returns:
        dict: {"status": str|None, "qga_ready": bool|None}
    """
    snapshot = await _scan_row_state_snapshot(page, created_marker)
    return {
        "status": snapshot.get("status"),
        "qga_ready": snapshot.get("qga_ready"),
    }


async def _wait_qga_ready(page, created_marker: str,
                          timeout_ms: int = 60000,
                          poll_interval_ms: int = 5000) -> bool:
    """轮询行级 qgaReady 直到就绪（批一 1.5）。

    上限 timeout_ms；qga_ready 不可读（None）时立即放弃——不做全局硬等
    （无 QGA 的镜像会挂死整轮）。

    Returns:
        bool: True=已就绪；False=超时或不可读
    """
    waited = 0
    while waited <= timeout_ms:
        snapshot = await _scan_row_state_snapshot(page, created_marker)
        qga = snapshot.get("qga_ready")
        if qga is True:
            return True
        if qga is None:
            return False
        await page.wait_for_timeout(poll_interval_ms)
        waited += poll_interval_ms
    return False


# ============================================================
# 状态机采集（批一 1.3）：状态×按钮矩阵 + 转移边（跨轮累积落盘 ui.json）
# ============================================================

def _init_state_ctx(state_seed: dict = None) -> dict:
    """初始化状态采集上下文（批一 1.3）：合并旧 ui.json 种子（跨轮累积）。

    转移边同三元组 (action, from, to) 去重保序；矩阵逐状态逐按钮并入。
    种子缺字段/畸形条目过滤，保证冷启动（无旧产物）零异常。
    """
    seed = state_seed or {}
    matrix = {}
    for status, btns in (seed.get("state_button_matrix") or {}).items():
        if isinstance(btns, dict):
            matrix[status] = dict(btns)
    transitions = []
    seen = set()
    for edge in (seed.get("state_transitions") or []):
        if not isinstance(edge, dict):
            continue
        key = (edge.get("action"), edge.get("from"), edge.get("to"))
        if key in seen or not all(key):
            continue
        seen.add(key)
        transitions.append({"action": key[0], "from": key[1], "to": key[2]})
    return {"state_button_matrix": matrix, "state_transitions": transitions}


def _record_state_matrix(state_ctx: dict, status, states: dict) -> None:
    """状态×按钮矩阵采集：settle 点观测 (status, {按钮: enabled}) 并入。

    同状态重复观测按按钮覆盖（最新为准），新按钮并入（union 语义）。
    status 不可读（None）或 states 为空时不采集——矩阵以稳态点为权威。
    """
    if not state_ctx or not status or not isinstance(states, dict) or not states:
        return
    matrix = state_ctx.setdefault("state_button_matrix", {})
    row = matrix.setdefault(status, {})
    row.update(states)


def _record_state_transition(state_ctx: dict, action, from_status, to_status) -> None:
    """转移边采集：{action, from, to} 同三元组去重保序。

    任一端不可读（None）或两端相同（非状态翻转）时不记录。
    """
    if (not state_ctx or not action or not from_status or not to_status
            or from_status == to_status):
        return
    edges = state_ctx.setdefault("state_transitions", [])
    key = (action, from_status, to_status)
    for e in edges:
        if (e.get("action"), e.get("from"), e.get("to")) == key:
            return
    edges.append({"action": action, "from": from_status, "to": to_status})


_ROW_LINK_ACTION = "查看详情（名称链接）"


def _merge_row_link_actions(buttons_by_tab: dict, ui_result: dict) -> int:
    """★ 批五 F4-1：逐行名称链接归并为单条语义操作（r7 实证 10 条 → 1 条）。

    现状根因（证据 E）：存量 VM 名称单元格链接被逐行收成 N 个操作（每条
    label=各 VM 名，location=row_action，tag=SPAN）；执行时按「文本全局
    唯一直点」或「marker 行内找该文本」都指向别人的 VM 行——多用户环境
    必然 not_found/click_failed 秒败，且操作数随存量 VM 数膨胀。

    归并判据（纯结构性，与 _infer_action_role 判 navigation 同一特征）：
    - location=row_action 且 tag 非 BUTTON/DROPDOWN_ITEM
    - 非下拉触发器（is_dropdown_trigger_text 排除「更多」）
    - 同 tab 内 ≥2 条且文本互不相同（逐行形态；同文本链接按钮按 action
      去重天然只收 1 条，不触发归并）

    归并动作：逐行条目从 buttons_by_tab[tab] 与 ui_result["row_actions"]
    双侧移除，注册单条语义操作「查看详情（名称链接）」（row_link=True，
    row_link_class=多数派 className 供执行/回放 class 定位，row_link_samples
    落产物供可观测）。builder fallback 通道按 row_link 标记跳过（防逐行
    条目从 row_actions 回流 playbook）。

    Returns:
        int: 被归并的逐行条目总数（0=无名称链接形态，零变化幂等）
    """
    merged_total = 0
    merged_texts = set()
    merged_btn_template = None
    for tab_name, tab_buttons in buttons_by_tab.items():
        link_entries = [
            (action, b) for action, b in tab_buttons.items()
            if b.get("location") == "row_action"
            and b.get("tag") not in ("BUTTON", "DROPDOWN_ITEM")
            and not is_dropdown_trigger_text(b.get("text"))
        ]
        if len(link_entries) < 2:
            continue
        texts = [b.get("text", "") for _, b in link_entries]
        if len(set(texts)) != len(texts):
            continue  # 同文本多行（非名称链接形态），保守不归并
        # 多数派 className（探测期采集，执行/回放按 class 定位名称单元格）
        cls_counter = {}
        for _, b in link_entries:
            cls = (b.get("className") or "").strip()
            cls_counter[cls] = cls_counter.get(cls, 0) + 1
        link_class = max(cls_counter, key=lambda k: cls_counter[k]) if cls_counter else ""
        for action, _b in link_entries:
            del tab_buttons[action]
            merged_texts.add(action)
        merged_btn = {
            "text": _ROW_LINK_ACTION,
            "tag": link_entries[0][1].get("tag", "SPAN"),
            "className": link_class,
            "location": "row_action",
            "action": _ROW_LINK_ACTION,
            "tab": tab_name,
            "source": "row_link_merge",
            "row_link": True,
            "row_link_class": link_class,
            "row_link_samples": texts,
        }
        tab_buttons[_ROW_LINK_ACTION] = merged_btn
        merged_btn_template = merged_btn_template or dict(merged_btn)
        merged_total += len(link_entries)
        LOG.info(f"  [F4-ROW-LINK] tab '{tab_name}': 归并 {len(link_entries)} 条逐行名称链接"
                 f" → 单条 '{_ROW_LINK_ACTION}' (class='{link_class}')")
    if not merged_total:
        return 0
    # ui_result["row_actions"] 同步：移除被归并条目 + 插入合成单条（幂等防重）
    if isinstance(ui_result.get("row_actions"), list):
        kept = []
        for b in ui_result["row_actions"]:
            _bt = b.get("text", "")
            if (b.get("location") == "row_action"
                    and b.get("tag") not in ("BUTTON", "DROPDOWN_ITEM")
                    and not is_dropdown_trigger_text(_bt)
                    and _bt in merged_texts):
                continue
            kept.append(b)
        if not any(b.get("row_link") for b in kept) and merged_btn_template:
            kept.append(merged_btn_template)
        ui_result["row_actions"] = kept
    return merged_total


def _collect_state_pending(validated: dict, buttons_by_action: dict,
                           action_roles: dict, executed_actions: set) -> list:
    """回收状态受阻 pending（批一 1.4 reconcile 候选）。

    条件：已尝试未成功 + generic/update 角色（批五 F5-2 放宽）+ 行级位置 +
    未以业务身份成功过（executed_actions）+ 失败签名属状态受阻（禁用跳过 /
    过渡窗口 click_failed）。与既有 _retry_state_blocked_ops 候选口径一致。
    """
    pending = []
    for action, res in validated.items():
        if not isinstance(res, dict) or res.get("success"):
            continue
        if action in executed_actions:
            continue
        if action not in buttons_by_action:
            continue
        # ★ 批五 F5-2：角色放宽到 {generic, update}——update 角色操作（如
        #   EIS 修改Hostname）同样受行状态门控（disabled/过渡窗），此前仅放
        #   generic 致 update 角色永无 reconcile 恢复机会（r7 实证 修改Hostname
        #   skipped 后未进 pending）。位置约束（下一行）不变：仅行级位置可回收，
        #   toolbar 角色 update 仍不回收（既有语义保留）。
        if action_roles.get(action, "generic") not in ("generic", "update"):
            continue
        btn = buttons_by_action.get(action) or {}
        if btn.get("location") not in ("row_action", "dropdown"):
            continue
        error_type = res.get("error_type") or ""
        error_text = res.get("error_text") or ""
        state_blocked = (
            (error_type == "skipped" and "按钮被禁用" in error_text)
            or error_type == "click_failed"
        )
        if not state_blocked:
            continue
        pending.append(action)
    return pending


async def _check_and_execute_newly_enabled(
    page,
    created_marker: str,
    button_states: dict,
    buttons_by_action: dict,
    executed_actions: set,
    form_filler,
    tab_name: str,
    tab_locator: str,
    ui_result: dict,
    tab_prefix: str,
    max_depth: int = 5,
) -> dict:
    """检查并执行新启用的按钮（通用级联处理）。

    扫描当前行所有按钮状态，对比历史记录，发现从 disabled → enabled 的按钮立即执行。
    支持递归处理级联（A 执行后启用 B，B 执行后启用 C）。

    Args:
        page: Playwright Page
        created_marker: 行标识
        button_states: 历史状态 {button_text: is_enabled}
        buttons_by_action: 按钮字典
        executed_actions: 已执行的按钮集合
        form_filler: 表单填充器
        tab_name: Tab 名称
        tab_locator: Tab 定位器
        ui_result: UI 结果
        tab_prefix: 日志前缀
        max_depth: 最大递归深度

    Returns:
        dict: 执行结果 {action: result}
    """
    if max_depth <= 0:
        LOG.warning(f"{tab_prefix}[STATE-CHANGE] 达到最大递归深度，停止")
        return {}

    results = {}

    # 扫描当前状态
    current_states = await _scan_all_button_states(page, created_marker)
    if not current_states:
        return results

    # 找出新启用的按钮（之前 disabled，现在 enabled）
    newly_enabled = []
    for text, is_enabled in current_states.items():
        if is_enabled and text in button_states and not button_states[text]:
            # 从 disabled → enabled
            if text not in executed_actions:
                newly_enabled.append(text)

    if not newly_enabled:
        # 更新状态记录
        button_states.update(current_states)
        return results

    LOG.info(f"{tab_prefix}[STATE-CHANGE] 发现 {len(newly_enabled)} 个新启用按钮: {newly_enabled}")

    # ★ 批五 F7：级联批量执行前稳态等待——主循环状态翻转后行处过渡窗
    # （关机✓→Stopping），过渡窗内对 newly_enabled 连续执行必连败（r7b
    # 证据 I：10 项级联连败）。等双条件稳态（status 连续两次相同 + 菜单
    # enabled 集两次一致）再批量执行；timed_out 不硬失败——本轮放弃级联
    # 执行（defer：不执行任何项，button_states 不更新保持翻转前基线，
    # 后续成功操作的级联扫描可重新检测到这批翻转，交 reconcile 的 settle
    # 逻辑兜底）。
    if created_marker:
        try:
            stable = await _wait_row_states_stable(page, created_marker)
        except Exception as _we:
            # 级联是增益层：等待异常不应炸主循环（page 关闭类异常除外——
            # 后续操作自会暴露），降级为不等待直接执行（行为回退现状）
            LOG.warning(f"{tab_prefix}[STATE-CHANGE] 稳态等待异常（照常执行级联）: {_we}")
            stable = {"timed_out": False}
        if stable.get("timed_out"):
            LOG.warning(f"{tab_prefix}[STATE-CHANGE] 行状态等待稳态超时，"
                        f"本轮放弃级联执行（defer reconcile）: {newly_enabled}")
            return results
        LOG.info(f"{tab_prefix}[STATE-CHANGE] 行已稳态"
                 f"（status={stable.get('status')}），开始批量执行")

    # 逐一执行
    for text in newly_enabled:
        # ★ 防止递归中已执行的按钮被重复执行
        if text in executed_actions:
            LOG.info(f"{tab_prefix}[STATE-CHANGE] 跳过已执行按钮: {text}")
            continue
        LOG.info(f"{tab_prefix}[STATE-CHANGE] 执行新启用按钮: {text}")

        # ★ P0-A: 级联操作同样在开始时重装捕获器，隔离主操作消息残留
        await _install_message_capture(page)

        # 构建按钮信息
        btn_info = buttons_by_action.get(text, {
            "text": text,
            "tag": "DROPDOWN_ITEM",
            "location": "dropdown",
            "selector": f'.el-dropdown-menu__item:has-text("{text}")',
            "tab": tab_name,
        })

        # 执行
        result = await _error_driven_retry(
            page, _do_generic_operation, {
                "btn": btn_info, "action": text, "marker": created_marker,
                "form_filler": form_filler,
                "tab_name": tab_name, "tab_locator": tab_locator,
            }
        )

        results[text] = result
        # ★ 批五 F1：仅业务成功占用「业务身份」入册——_collect_state_pending
        # 把 executed_actions 视作已业务成功排除（L724），失败也入册会把
        # 级联失败的操作永久排除在 reconcile 恢复之外（r7b 时间线+创建镜像
        # 反证定案：级联失败但主循环槽位在后的操作最终成功，证明缺口专杀
        # 「槽位已过+reconcile 被排除」的操作）。失败项由 validated 中的
        # state_blocked 失败签名经 pending 回收（L735 口径不变）。
        if result and result.get("success"):
            executed_actions.add(text)

        if result and result.get("success"):
            LOG.info(f"{tab_prefix}[STATE-CHANGE] ✓ {text} 执行成功")

            # 递归检查级联
            await page.wait_for_timeout(1500)
            sub_results = await _check_and_execute_newly_enabled(
                page, created_marker, button_states, buttons_by_action,
                executed_actions, form_filler, tab_name, tab_locator,
                ui_result, tab_prefix, max_depth - 1
            )
            results.update(sub_results)
        else:
            error_text = result.get("error_text", "") if result else "未知错误"
            LOG.warning(f"{tab_prefix}[STATE-CHANGE] ✗ {text} 执行失败: {error_text}")

    # 更新状态记录
    button_states.update(current_states)

    return results


async def _wait_row_states_stable(page, created_marker: str,
                                  max_wait_ms: int = 180000,
                                  poll_interval_ms: int = 5000,
                                  state_ctx: dict = None) -> dict:
    """等待行状态稳定（批一 1.2 重写：双条件 + 权威 status）。

    判定（不预设「过渡态特征」——「含 ing/中」特征枚举必错（Running 含
    ing、运行中含「中」），改用「值不再变化」本身作为翻转完成的证明）：
      - status 连续两次读值相同（间隔 poll_interval_ms）且
      - 菜单 enabled 集合连续两次一致
      双条件同时满足 = 稳态。status 不可读时退回「菜单集合两次一致」
      单条件判定（现状行为，estack 等非 command 环境不降级）。
    上限 180s（分钟级翻转覆盖：EIS 开机实测 ~18s，重装类更长留余量）。
    超时不硬失败：记录 state_wait_timeout 返回最后一次观测（重试链兜底）。
    稳态时顺带采集状态×按钮矩阵（批一 1.3，state_ctx 提供时）。

    Args:
        page: Playwright Page
        created_marker: 行标识
        max_wait_ms: 最长等待上限（毫秒），超时返回最后一次观测
        poll_interval_ms: 轮询间隔（毫秒）
        state_ctx: 状态采集上下文（批一 1.3；None 时仅判定不采集）

    Returns:
        dict: {"states": {button_text: is_enabled},
               "status": str|None, "qga_ready": bool|None, "timed_out": bool}
    """
    prev = await _scan_row_state_snapshot(page, created_marker)
    if not prev.get("states"):
        # 首扫为空：行可能在重渲染中，短暂等待后复扫一次确认
        await page.wait_for_timeout(poll_interval_ms)
        prev = await _scan_row_state_snapshot(page, created_marker)
        if not prev.get("states"):
            return {"states": {}, "status": None, "qga_ready": None, "timed_out": False}

    waited = 0
    while waited < max_wait_ms:
        await page.wait_for_timeout(poll_interval_ms)
        cur = await _scan_row_state_snapshot(page, created_marker)
        waited += poll_interval_ms
        states_equal = bool(cur.get("states")) and cur.get("states") == prev.get("states")
        prev_status = prev.get("status")
        cur_status = cur.get("status")
        # status 可读时双条件；不可读时退回菜单集合单条件（现状行为）
        if prev_status is not None and cur_status is not None:
            stable = states_equal and cur_status == prev_status
        else:
            stable = states_equal
        if stable:
            _record_state_matrix(state_ctx,
                                 cur_status if cur_status is not None else prev_status,
                                 cur.get("states") or {})
            return {**cur, "timed_out": False}
        prev = cur

    LOG.warning(f"  [STATE-WAIT] 行状态 {max_wait_ms // 1000}s 未收敛"
                f"（state_wait_timeout），继续（重试链兜底）")
    return {**prev, "timed_out": True}


async def _state_aware_reconcile(
    page,
    created_marker: str,
    validated: dict,
    buttons_by_action: dict,
    action_roles: dict,
    executed_actions: set,
    form_filler,
    tab_name: str,
    tab_locator: str,
    tab_prefix: str,
    trigger_action: str = "",
    state_ctx: dict = None,
    max_rounds: int = 12,
) -> dict:
    """状态感知调度器（批一 1.4，循环式替代既有递归重试）。

    背景：行级操作依赖目标行生命周期状态（运行/关机/冻结等），主循环按
    收集序执行时受阻（禁用 skipped / 过渡窗口 click_failed）。既有
    _retry_state_blocked_ops 只回收「当前已启用」的操作——无状态语义，
    Stopped 态 10 个禁用操作永久挂空（EIS 第 6 轮实证）。

    本调度器消费批一 1.1/1.3 产出的三类权威数据：
      - status：行权威状态（Vue command.row.status，settle 时读取）
      - state_button_matrix：状态×按钮可用矩阵（settle 点采集）
      - state_transitions：已知转移边 {action, from, to}（跨轮累积）

    每轮 reconcile（循环，无递归）：
      1. settle（1.2 等待稳态）→ 读 status + 扫菜单 → 采集进 matrix
      2. R1 直接执行：pending 中「当前已启用」的 → 逐个以业务身份执行；
         执行后再 settle（该操作可能翻转状态）→ 回到 1
      3. R1 恢复转移：无 ready 时，找「blocked pending 的需求状态」
         （matrix 显示该 pending 在状态 S 可用、当前状态不可用）+
         「已知转移边 from=当前状态 to=S」→ 以恢复身份执行转移操作
         （intent=state_restore, triggered_by=首个需求操作，
         build_playbook Cascade-Order 已消费该语义）→ 到达后逐个
         尝试解锁的 pending：至少一个成功 → 边保持可用；全部失败 →
         R2 该边标记耗尽（本轮不再走）
      4. 无 ready、无可用恢复边 → 结束

    R1-R4 规则（已与项目所有者对齐）：
      R1 驱动性：任何转移必须有驱动力（业务身份=待验证；恢复身份=有
        pending 需要）——乒乓根治点：既已验证又无 pending 需求的转移
        直接拒绝。
      R2 边耗尽：恢复转移到达后解锁 pending 全部失败 → 边本轮封禁，
        防矩阵误判反复横跳；转移操作自身失败同样封禁该边。
      R3 业务唯一：每操作业务身份每次 reconcile 调用至多一次。
      R4 恢复不限次：恢复身份跨调用不限（关机/硬关机各把状态打到
        Stopped，开机两次恢复均合法，各有新 pending 驱动）；已验证过的
        操作可作为恢复手段再次执行。

    终止性：正常推演下每轮至少消费一个 pending（业务执行）或一条边
    （耗尽/已走），单调递减自然终止；max_rounds 仅作挂死保险（防状态
    翻转卡死导致 status 轮询 180s×N 无限挂），非业务覆盖上限。

    status 不可读（非 dropdown command 环境）→ 自动降级为仅 R1 直接
    执行（与既有 _retry_state_blocked_ops 行为等价，estack 零降级）。

    Returns:
        dict: {action: result}，result 附内部字段 _reconcile_meta
        （triggered_by/intent，调用方记录执行顺序后移除）；
        row_state_before/after 已随 result 携带（批一 1.3 落 validated）。
    """
    if not created_marker:
        return {}

    pending = set(_collect_state_pending(
        validated, buttons_by_action, action_roles, executed_actions))
    if not pending:
        return {}

    results = {}
    exhausted_edges = set()   # R2：本轮封禁边 (action, from, to)
    business_executed = set()  # R3：业务身份已执行（本次 reconcile 调用内）
    recovery_done = set()      # 本次调用已走过的边（含转移失败）
    watched_edges = {}         # R2 判定：edge_key -> 解锁 pending 成败追踪

    LOG.info(f"{tab_prefix}[STATE-RECONCILE] 启动: pending={sorted(pending)}"
             f"（触发: {trigger_action or '主循环收尾'}）")

    for round_num in range(1, max_rounds + 1):
        # 1. settle：等待稳态（1.2）+ 采集矩阵（1.3）
        settle = await _wait_row_states_stable(page, created_marker, state_ctx=state_ctx)
        states = settle.get("states") or {}
        status = settle.get("status")
        if not states:
            break

        # 2. R1 直接执行：pending 中当前已启用且业务身份未消费的
        ready = [a for a in pending if states.get(a) and a not in business_executed]
        if ready:
            for action in ready:
                btn = buttons_by_action.get(action)
                if not btn:
                    pending.discard(action)
                    continue
                LOG.info(f"{tab_prefix}[STATE-RECONCILE] R1 业务执行: {action}"
                         f"（当前状态: {status}）")
                # ★ P0-A: 每次执行同样重装捕获器，隔离前序操作消息残留
                await _install_message_capture(page)
                result = await _error_driven_retry(
                    page, _do_generic_operation, {
                        "btn": btn, "action": action, "marker": created_marker,
                        "form_filler": form_filler,
                        "tab_name": tab_name, "tab_locator": tab_locator,
                    }
                )
                result = result or {}
                business_executed.add(action)  # R3：业务身份已消费
                pending.discard(action)

                success = bool(result and result.get("success"))
                if status is not None:
                    result["row_state_before"] = status
                if success:
                    executed_actions.add(action)
                    LOG.info(f"{tab_prefix}[STATE-RECONCILE] ✓ {action} 业务成功")
                    # 执行可能再次翻转行状态 → settle 后记转移边（1.3）
                    post = await _wait_row_states_stable(
                        page, created_marker, state_ctx=state_ctx)
                    if post.get("status") is not None:
                        result["row_state_after"] = post.get("status")
                    _record_state_transition(state_ctx, action, status, post.get("status"))
                else:
                    err = result.get("error_text", "") or "未知错误"
                    LOG.info(f"{tab_prefix}[STATE-RECONCILE] ✗ {action} 业务失败: {err}")
                # 成败均入 results（对齐旧 STATE-RETRY 契约：调用方 merge
                # 更新 validated 最新结果——失败结果保留最新错误证据）
                results[action] = {**result, "_reconcile_meta": {
                    "triggered_by": trigger_action,
                    "intent": "business_validate",
                }}

                # R2 判定：watched 边的解锁 pending 成败追踪
                for key in list(watched_edges):
                    watch = watched_edges[key]
                    if action not in watch["unlocked"]:
                        continue
                    watch["attempted"].add(action)
                    if success:
                        watch["succeeded"] = True
                    if watch["succeeded"] or watch["attempted"] >= watch["unlocked"]:
                        if not watch["succeeded"] and watch["attempted"] >= watch["unlocked"]:
                            exhausted_edges.add(key)
                            LOG.info(f"{tab_prefix}[STATE-RECONCILE] R2 边耗尽: {key}")
                        del watched_edges[key]
            # 执行后再 settle（可能翻转状态）→ 回到 1
            continue

        # 3. R1 恢复转移（status 可读 + 有矩阵数据才走；不可读 → 降级结束）
        if status is None or not state_ctx:
            LOG.info(f"{tab_prefix}[STATE-RECONCILE] 无 ready 且状态不可读，结束（降级）")
            break

        matrix = state_ctx.get("state_button_matrix") or {}
        cur_enabled = matrix.get(status) or {}
        # blocked pending 的需求状态（matrix 显示可用、当前不可用）
        needed = {}
        for action in pending:
            if cur_enabled.get(action):
                continue
            need_states = [s for s, btns in matrix.items()
                           if s != status and btns.get(action)]
            if need_states:
                needed[action] = need_states
        if not needed:
            LOG.info(f"{tab_prefix}[STATE-RECONCILE] 无 ready 且无可恢复 pending，结束")
            break

        # 找可用转移边：from=当前状态、to∈需求状态、未封禁未走过、操作当前可点
        candidate = None
        for edge in (state_ctx.get("state_transitions") or []):
            key = (edge.get("action"), edge.get("from"), edge.get("to"))
            if (key in exhausted_edges
                    or key in recovery_done
                    or edge.get("from") != status
                    or edge.get("action") not in buttons_by_action
                    or not states.get(edge.get("action"))):
                continue
            if any(edge.get("to") in ns for ns in needed.values()):
                candidate = (edge, key)
                break
        if not candidate:
            LOG.info(f"{tab_prefix}[STATE-RECONCILE] 无可用恢复边，结束")
            break

        edge, key = candidate
        first_needing = next(a for a, ns in needed.items() if edge["to"] in ns)
        restore_action = edge["action"]
        btn = buttons_by_action.get(restore_action)
        LOG.info(f"{tab_prefix}[STATE-RECONCILE] R1 恢复转移: {restore_action}"
                 f"（{edge['from']} → {edge['to']}，驱动: {first_needing}）")
        await _install_message_capture(page)
        result = await _error_driven_retry(
            page, _do_generic_operation, {
                "btn": btn, "action": restore_action, "marker": created_marker,
                "form_filler": form_filler,
                "tab_name": tab_name, "tab_locator": tab_locator,
            }
        )
        result = result or {}
        recovery_done.add(key)
        if status is not None:
            result["row_state_before"] = status
        if result.get("success"):
            executed_actions.add(restore_action)
            post = await _wait_row_states_stable(
                page, created_marker, state_ctx=state_ctx)
            if post.get("status") is not None:
                result["row_state_after"] = post.get("status")
            _record_state_transition(state_ctx, restore_action, status, post.get("status"))
            LOG.info(f"{tab_prefix}[STATE-RECONCILE] ✓ 恢复转移成功:"
                     f" {status} → {post.get('status')}")
            # R2 追踪：到达后解锁的 pending 全部失败 → 该边耗尽
            unlocked = {a for a, ns in needed.items() if edge["to"] in ns}
            watched_edges[key] = {"unlocked": unlocked, "attempted": set(), "succeeded": False}
        else:
            # 转移操作自身失败 → R2 边封禁（防同边反复撞）
            exhausted_edges.add(key)
            err = result.get("error_text", "") or "未知错误"
            LOG.info(f"{tab_prefix}[STATE-RECONCILE] ✗ 恢复转移失败: {err}（边封禁）")
        # 成败均入 results（调用方 merge 更新 validated 最新结果）
        results[restore_action] = {**result, "_reconcile_meta": {
            "triggered_by": first_needing, "intent": "state_restore",
        }}
        # 回到 1：settle 后重扫 ready

    LOG.info(f"{tab_prefix}[STATE-RECONCILE] 结束: 本轮成功 {sum(1 for r in results.values() if r.get('success'))}"
             f" 个操作，剩余 pending {len(pending)} 个")
    return results


async def _rediscover_marker_dropdowns(
    page, ui_result: dict, framework: str, created_marker: str,
    buttons_by_action: dict, action_roles: dict, ordered_actions: list,
    tab_name: str, delete_actions: list, current_action: str, tab_prefix: str = "",
) -> list:
    """★ 批五 F2-1：create 成功后 marker 锁行重收集下拉菜单项并入册主循环。

    根因（v5 真实收集路径复现定案）：Phase A discover_all 在 create 之前运行，
    全页收集展开的是第一个可见 dd——多用户环境下是他人 VM 行，marker 行
    轮不到；第一行非 Running 态时需开机操作全 disabled 被旧过滤吞掉
    （r7b 缺失 4 项 4/4 全缺吻合）；互斥对文本取自错误行状态。create 成功
    后 marker 行已存在，按 marker 锁行重收集（hover 手势全量渲染，EIS 22 项）。

    新增项注册进 buttons_by_action + action_roles + ordered_actions +
    ui_result.dropdowns（供主循环尝试与 Stage 2 回放），插入位置与增量扫描
    同口径：delete 类追加末尾，其余插到当前操作（create）之后。
    锁行失败由 _discover_dropdowns 内部回落旧全页收集（收集到的既有项自动去重）。
    """
    from core.discovery.ui_scanner.element_scanner import _discover_dropdowns
    # 触发器判定需要 parent_buttons 文本集合（row_actions 含「更多」类触发器）
    parents = (
        ui_result.get("row_actions", [])
        + ui_result.get("toolbar_buttons", [])
    )
    try:
        items = await _discover_dropdowns(
            page, parents, None, framework, marker=created_marker)
    except Exception as e:
        LOG.warning(f"{tab_prefix}[F2-RECOLLECT] marker 锁行重收集异常: {e}")
        return []

    new_actions = []
    for d in items:
        text = d.get("text", "")
        if not text or text in buttons_by_action:
            continue  # 已在册（Phase A 收集/级联入册）自动去重
        btn = {
            "text": text,
            "tag": "DROPDOWN_ITEM",
            "location": "dropdown",
            "parent": d.get("parent", "更多"),
            "action": text,
            "tab": tab_name,
            "source": "marker_recollect",
        }
        buttons_by_action[text] = btn
        role = _infer_action_role(text, btn, delete_actions=delete_actions)
        action_roles[text] = role
        # 位置：delete 类放最后，其余插到当前操作（create）之后（与增量扫描同口径）
        if role == "delete":
            ordered_actions.append(text)
            insert_pos = len(ordered_actions) - 1
        else:
            try:
                current_idx = ordered_actions.index(current_action)
                ordered_actions.insert(current_idx + 1, text)
                insert_pos = current_idx + 1
            except ValueError:
                ordered_actions.append(text)
                insert_pos = len(ordered_actions) - 1
        # 回写 ui_result.dropdowns 供 Stage 2 回放（三阶段一致性）
        dd_list = ui_result.setdefault("dropdowns", [])
        if not any(x.get("text") == text and x.get("tab") == tab_name
                   for x in dd_list):
            dd_list.append({**btn, "tag": "DROPDOWN_ITEM"})
        new_actions.append(text)
        LOG.info(f"{tab_prefix}[F2-RECOLLECT] + {text} (角色: {role}, "
                 f"插入位置: {insert_pos})")
    if new_actions:
        LOG.info(f"{tab_prefix}[F2-RECOLLECT] marker 行重收集新增 "
                 f"{len(new_actions)} 项: {new_actions}")
    return new_actions


async def _validate_single_tab_crud(
    page,
    tab_buttons: dict,
    ui_result: dict,
    username: str,
    tab_name: str = "",
    tab_locator: str = "",
    state_ctx: dict = None,
) -> tuple:
    """在单个 Tab 上下文中执行完整 CRUD 验证。

    Args:
        page: Playwright Page 对象
        tab_buttons: {action: btn} 字典，该 Tab 下的所有按钮
        ui_result: discover_all() 返回的探测结果
        username: 用户名
        tab_name: Tab 名称（用于日志）
        state_ctx: 状态采集上下文（批一 1.3；跨 Tab 共享同一引用，
            settle 点观测逐轮累积，最终随 ui_result 落盘 ui.json）

    Returns:
        dict: {action: {"success": bool, "fill_data": dict, "selectors": dict, ...}}
    """
    validated = {}
    form_filler = FormFiller(page)
    framework = ui_result.get("framework", "element-ui")
    buttons_by_action = tab_buttons

    # ★ 使用 shared/constants 统一常量（消除重复定义）
    from core.discovery.shared.constants import (
        NON_BUSINESS_BUTTONS, BATCH_PREFIXES, DESTROY_KEYWORDS
    )
    _NON_BUSINESS_BUTTONS = NON_BUSINESS_BUTTONS

    # 收集行级操作文本，用于去重批量操作
    # ★ 触发器判定用包含式 helper（「更多操作」组合文本命中，精确匹配漏判实证）
    _row_action_texts = {
        btn.get("text", "") for btn in buttons_by_action.values()
        if btn.get("location") in ("row_action", "dropdown")
        and not is_dropdown_trigger_text(btn.get("text"))
    }
    _BATCH_PREFIXES = BATCH_PREFIXES
    _DESTROY_KEYWORDS = DESTROY_KEYWORDS

    # 自动推断 query 操作：如果没有 query 按钮但探测到搜索输入框
    if "query" not in buttons_by_action:
        # ★ 按 Tab 过滤 search_inputs（多 Tab 页面需要区分）
        all_search_inputs = ui_result.get("search_inputs", [])
        tab_search_inputs = [
            si for si in all_search_inputs
            if si.get("tab", "") == tab_name
        ]

        if tab_search_inputs:
            search_input = tab_search_inputs[0]
            LOG.info(f"  自动推断 query 操作: 使用搜索输入框 '{search_input.get('placeholder', '')}' (tab={tab_name})")
            buttons_by_action["query"] = {
                "text": f"搜索:{search_input.get('placeholder', '输入框')}",
                "action": "query",
                "tag": "INPUT",
                "location": "toolbar",
                "is_search_input": True,
                "search_input": search_input,
            }
        else:
            LOG.debug(f"  未找到 {tab_name} 的搜索输入框，跳过 query 推断")

    # 表单字段（从探测结果获取）
    form_fields = ui_result.get("form_fields", [])

    # 记录创建的数据标识（供后续操作复用）
    created_marker = None

    # 动态确定操作顺序
    _create_action, _delete_actions = _find_create_delete_actions(buttons_by_action)
    _query_action = "query" if "query" in buttons_by_action else None

    # marker fallback: 无 create 操作但表格有数据时，从第一行提取标识
    # ★ 使用 shared/marker_utils（Stage 1/2 统一实现）
    _pre_marker = None
    if not _create_action:
        from core.discovery.shared.marker_utils import extract_marker_from_row
        _pre_marker = await extract_marker_from_row(page)
        if _pre_marker:
            created_marker = _pre_marker
            LOG.info(f"  无 create 操作，从表格首行提取 marker: '{_pre_marker}'")

    # ★ Switch 分类：toolbar/form 级 vs 行级
    switches = ui_result.get("switches", [])
    tab_switches = [sw for sw in switches if sw.get("tab") == tab_name]
    toolbar_switches = [sw for sw in tab_switches if sw.get("location") in ("toolbar", "form")]
    row_switches = [sw for sw in tab_switches if sw.get("location") == "row"]

    def _sw_action_key(sw):
        """为 switch 生成唯一的 action key"""
        return f"switch:{sw.get('text', '状态')}"

    # 将 switch 注册到 buttons_by_action，使其参与统一调度
    for sw in toolbar_switches + row_switches:
        key = _sw_action_key(sw)
        buttons_by_action[key] = {**sw, "action": key, "_is_switch": True}

    tab_prefix = f"[{tab_name}] " if tab_name else ""
    if tab_switches:
        LOG.info(f"{tab_prefix}检测到 {len(tab_switches)} 个 switch "
                 f"(toolbar/form={len(toolbar_switches)}, row={len(row_switches)})")

    # ★ 构建 ordered_actions（按优先级排列）
    ordered_actions = []

    # 1. Toolbar/Form switch 最先（功能前置条件：如"开启黑名单"）
    for sw in toolbar_switches:
        ordered_actions.append(_sw_action_key(sw))

    # 2. 新增（create）
    if _create_action:
        ordered_actions.append(_create_action)

    # 3. query
    if _query_action and _query_action != _create_action:
        ordered_actions.append(_query_action)

    # 4. 其他操作（编辑等）
    for action in buttons_by_action:
        if (action not in ordered_actions
                and action not in _delete_actions
                and not buttons_by_action[action].get("_is_switch")):
            ordered_actions.append(action)

    # 5. Row switch（行内开关，新增之后的普通业务操作）
    for sw in row_switches:
        key = _sw_action_key(sw)
        if key not in ordered_actions:
            ordered_actions.append(key)

    # 6. 删除最后
    ordered_actions.extend(_delete_actions)

    # ★ 初始化按钮状态追踪（通用方案，替代硬编码的 STATE_TOGGLE_BUTTONS）
    # 记录所有按钮的初始 enabled/disabled 状态，用于检测状态变化
    button_states = {}  # {button_text: is_enabled}
    executed_actions = set()  # 已执行的按钮集合

    # ★ 如果 created_marker 已存在（来自 pre_marker），立即预扫描初始状态
    if created_marker:
        await page.wait_for_timeout(500)
        button_states = await _scan_all_button_states(page, created_marker)
        LOG.info(f"{tab_prefix}[STATE-INIT] 预扫描初始按钮状态: {button_states}")

    # ★ 初始化执行顺序追踪（记录实际执行的操作序列，包括级联操作）
    execution_order = []

    def _record_execution(action_name: str, phase: str, role: str,
                         triggered_by: str = "", partial_success: bool = False):
        """记录执行顺序，自动去重 cascade 和 main 阶段的重复操作。

        当同一操作既作为 cascade（状态恢复）又作为 main（业务验证）执行时，
        会合并为一条记录，用 intent 字段区分意图。
        """
        # 查找是否已存在同名 cascade 记录
        existing_cascade = next(
            (e for e in execution_order
             if e["action"] == action_name and e.get("phase") == "cascade"),
            None
        )

        if phase == "cascade" and existing_cascade:
            # 已存在 cascade 记录，更新
            existing_cascade["triggered_by"] = triggered_by
            existing_cascade["role"] = role
            existing_cascade["intent"] = "state_restore"
        elif phase == "main" and existing_cascade:
            # main 阶段遇到已有 cascade 的操作，升级为主操作
            existing_cascade["phase"] = "main"
            existing_cascade["role"] = role
            existing_cascade["intent"] = "business_validate"
            # 移除 triggered_by（不再是纯 cascade）
            existing_cascade.pop("triggered_by", None)
            if partial_success:
                existing_cascade["partial_success"] = True
        else:
            # 新增记录
            entry = {
                "action": action_name,
                "phase": phase,
                "role": role,
            }
            if phase == "cascade" and triggered_by:
                entry["triggered_by"] = triggered_by
                entry["intent"] = "state_restore"
            else:
                entry["intent"] = "business_validate"
            if partial_success:
                entry["partial_success"] = True
            execution_order.append(entry)

    # 记录每个 action 的语义角色
    action_roles = {}
    for action in buttons_by_action:
        btn = buttons_by_action[action]
        if btn.get("_is_switch"):
            action_roles[action] = "switch"
        else:
            action_roles[action] = _infer_action_role(action, btn, delete_actions=_delete_actions)

    LOG.debug(f"{tab_prefix}操作分发: { {a: action_roles[a] for a in ordered_actions} }")

    for action in ordered_actions:
        btn = buttons_by_action.get(action)
        if not btn:
            LOG.debug(f"{tab_prefix}跳过 {action}: 未找到对应按钮")
            continue

        # ★ 已成功过的操作不重复执行：级联（STATE-CHANGE）先行成功的
        # 已入册操作，主循环到达时跳过——重复执行既浪费探测时间又可能
        # 再次翻转行状态（级联双执行实证：热迁移/创建快照/创建镜像等
        # 在 create 后级联已成功，主循环再次执行属无谓重复业务动作）。
        # 对主循环自身成功的操作无影响（成功结果在自身迭代内才写入）。
        _prior_result = validated.get(action)
        if isinstance(_prior_result, dict) and _prior_result.get("success"):
            LOG.info(f"{tab_prefix}跳过 {action}: 已在级联/状态重试中成功，不重复执行")
            continue

        # ★ 批量操作动态跳过（fix7）：清单已有单操作「XX」时跳过「批量XX」。
        #   用户规则：有启用则不需要批量启用；删除/批量删除、暂停/批量暂停同理。
        #   既有静态规则（_validate_business_flow，扫描时刻判定）在行级互斥项
        #   （启用/暂停）依赖行数据/状态时漏拦——表空时静态收集不到单操作
        #   （fix6 白名单/批量启用 click_failed 实证）。此处执行时兜底：
        #   增量扫描入册的单操作此时已在 buttons_by_action。
        #   前缀与 shared/constants.BATCH_PREFIXES 对齐（此处避免函数级 import 开销用字面量）。
        _skip_batch = False
        for _bp in ("批量", "batch", "bulk"):
            if action.startswith(_bp):
                _core = action[len(_bp):]
                if _core and any(a == _core for a in buttons_by_action):
                    LOG.info(f"{tab_prefix}跳过批量操作: '{action}'（清单已有单操作 '{_core}'，按规则跳过）")
                    _skip_batch = True
                    break
        if _skip_batch:
            continue

        btn_text = btn.get("text", "")
        btn_location = btn.get("location", "toolbar")
        role = action_roles.get(action, "generic")
        LOG.info(f"{tab_prefix}验证操作: {action} (按钮: {btn_text}, 角色: {role})")

        # ★ 批一 1.3：行级操作执行前读行权威状态（row_state_before）。
        # 执行成功后 settle 读 row_state_after，两端可读且不同 → 记转移边
        # {action, from, to}（跨轮累积落盘 ui.json）。status 不可读时为 None，
        # 后续记录全部静默跳过（estack 等非 command 环境零影响）。
        _row_state_before = None
        if (created_marker
                and btn_location in ("row_action", "dropdown")
                and role in ("generic", "update", "delete")
                and not btn.get("_is_switch")):
            _pre_snapshot = await _scan_row_state_snapshot(page, created_marker)
            _row_state_before = _pre_snapshot.get("status")

        # ★ P0-A: 每个操作开始时重装消息捕获器（清空 captured 数组 + 重打 __op_baseline 基线）。
        # 此前只在 Tab 切换时装一次（_validate_business_flow），同 Tab 内前序操作的通知
        # （3s TTL 后 DOM 消失躲过 B2 基线，但 __captured_messages 数组永久残留）会污染
        # 本操作的成败判定——实证：黑白名单新增被 10s 前 switch 的「切换黑白名单成功」
        # 残留通知误判 success（真实反馈是 info 级「请选择组织」）。
        # install 幂等（observer 复用，仅重复清空数组 + 基线打标），无重复安装副作用。
        await _install_message_capture(page)

        # ★ Switch 操作：统一调度
        if btn.get("_is_switch"):
            LOG.info(f"{tab_prefix}[SWITCH-DEBUG] 检测到 switch 操作: text='{btn.get('text')}', locator='{btn.get('locator')}', is_checked={btn.get('is_checked')}")

            # ★ 修改点 3：toggle 前用 JS 验证实际状态（诊断用）
            try:
                _pre_toggle_diag = await page.evaluate(f"""(locator) => {{
                    const switches = document.querySelectorAll(locator);
                    const visible = Array.from(switches).filter(s => s.offsetWidth > 0);
                    if (visible.length === 0) return {{ error: 'no visible switch', locator: locator }};
                    const sw = visible[0];
                    return {{
                        locator: locator,
                        selfChecked: sw.classList.contains('is-checked'),
                        ariaChecked: sw.getAttribute('aria-checked'),
                        paneId: sw.closest('.el-tab-pane')?.id || 'none'
                    }};
                }}""", btn.get('locator'))
                LOG.info(f"{tab_prefix}[SWITCH-PRE-TOGGLE] 实际 DOM 状态: {_pre_toggle_diag}")
            except Exception as e:
                LOG.warning(f"{tab_prefix}[SWITCH-PRE-TOGGLE] 诊断失败: {e}")

            result = await _do_toggle_switch(page, {"switch_info": btn})
            LOG.info(f"{tab_prefix}[SWITCH-DEBUG] toggle 结果: success={result.get('success') if result else None}, before={result.get('before_checked') if result else None}, after={result.get('after_checked') if result else None}")

            # ★ 修改点 3：toggle 后验证实际状态变化
            try:
                _post_toggle_diag = await page.evaluate(f"""(locator) => {{
                    const switches = document.querySelectorAll(locator);
                    const visible = Array.from(switches).filter(s => s.offsetWidth > 0);
                    if (visible.length === 0) return {{ error: 'no visible switch', locator: locator }};
                    const sw = visible[0];
                    return {{
                        locator: locator,
                        selfChecked: sw.classList.contains('is-checked'),
                        ariaChecked: sw.getAttribute('aria-checked'),
                        paneId: sw.closest('.el-tab-pane')?.id || 'none'
                    }};
                }}""", btn.get('locator'))
                LOG.info(f"{tab_prefix}[SWITCH-POST-TOGGLE] 实际 DOM 状态: {_post_toggle_diag}")

                # 验证状态是否真的变化
                _pre_checked = _pre_toggle_diag.get('selfChecked')
                _post_checked = _post_toggle_diag.get('selfChecked')
                if _pre_checked == _post_checked and _pre_checked is not None:
                    LOG.warning(f"{tab_prefix}[SWITCH-WARN] ⚠️ switch 状态未变化！pre={_pre_checked}, post={_post_checked}")
            except Exception as e:
                LOG.warning(f"{tab_prefix}[SWITCH-POST-TOGGLE] 诊断失败: {e}")

            if result and result.get("success"):
                # ★ 传递 _is_switch 标记到 validated，供 builder 识别
                validated[action] = {**result, "_is_switch": True, "locator": btn.get("locator"), "text": btn.get("text"), "is_checked": btn.get("is_checked")}
                LOG.info(f"{tab_prefix}{action} 操作成功")
            else:
                error_text = result.get("error_text", "") if result else "未知错误"
                LOG.warning(f"{tab_prefix}{action} 操作失败: {error_text}")
                _merge_validated_result(validated, action, {**(result or make_error("switch_failed", error_text)), "_is_switch": True, "locator": btn.get("locator"), "text": btn.get("text"), "is_checked": btn.get("is_checked")})
            # switch 状态变化后，等待页面稳定
            await page.wait_for_timeout(500)
            continue

        # 基于角色分发到对应验证函数
        if role == "create":
            result = await _error_driven_retry(
                page, _do_create, {
                    "btn": btn, "fields": form_fields, "username": username,
                    "form_filler": form_filler, "framework": framework,
                    "tab_name": tab_name, "tab_locator": tab_locator,
                }
            )
            if result and result.get("success"):
                created_marker = result.get("marker")
                if not created_marker:  # 成功但没取到 marker，fallback 取第一行
                    created_marker = await _extract_fallback_marker(page)
                    if created_marker:
                        LOG.info(f"{tab_prefix}create 成功但 marker 为空，使用 fallback marker: {created_marker}")
                validated[action] = result
                # ★ 记录执行顺序
                _record_execution(action, "main", "create")

                # ★ 预扫描按钮状态：create 成功后立即记录初始状态
                # 用于后续检测状态变化（如：冻结→启用、禁用→启用）
                if created_marker:
                    await page.wait_for_timeout(500)  # 等待 Vue 渲染
                    button_states = await _scan_all_button_states(page, created_marker)
                    LOG.info(f"{tab_prefix}[STATE-INIT] 预扫描按钮状态: {button_states}")
                    # ★ 批五 F2-1：marker 锁行重收集——Phase A 全页收集展开的是
                    # 第一个可见 dd（他人 VM 行），marker 行轮不到且 disabled 项
                    # 被旧口径吞掉（v5 定案）。create 成功后按 marker 锁行重收集
                    # 补齐真实菜单全量项（EIS 22 项）并入册主循环。
                    await _rediscover_marker_dropdowns(
                        page, ui_result, framework, created_marker,
                        buttons_by_action, action_roles, ordered_actions,
                        tab_name, _delete_actions, action, tab_prefix,
                    )
            else:
                fallback = await _extract_fallback_marker(page)
                if fallback:
                    created_marker = fallback
                    LOG.info(f"{tab_prefix}create 失败，使用 fallback marker: {fallback}")
                # ★ Fix 5: 即使 result 为 None/空也要记录
                if result:
                    _merge_validated_result(validated, action, result)
                else:
                    _merge_validated_result(validated, action, {
                        "success": False,
                        "error_type": "create_failed",
                        "error_text": "Create operation returned no result",
                        "trigger_text": btn.get("text", "") if isinstance(btn, dict) else str(btn),
                        "trigger_locator_verified": btn.get("locator", "") if isinstance(btn, dict) else "",
                        "fill_data": {},
                        "fill_rules": {},
                        "form_fields": form_fields,
                    })

        elif role == "query":
            LOG.info(f"{tab_prefix}[QUERY-DEBUG] 开始验证 query 操作: btn_text='{btn.get('text')}', is_search_input={btn.get('is_search_input')}, marker={created_marker}")
            if btn.get("is_search_input"):
                result = await _do_query_via_search_input(page, btn, marker=created_marker)
                LOG.info(f"{tab_prefix}[QUERY-DEBUG] search_input 结果: success={result.get('success') if result else None}, error={result.get('error') if result else 'None'}")
                if result and result.get("success"):
                    validated[action] = result
                    # ★ 记录执行顺序
                    _record_execution(action, "main", "query")
                else:
                    LOG.warning(f"{tab_prefix}[QUERY-DEBUG] query 验证失败（search_input）: {result}")
            else:
                result = await _error_driven_retry(
                    page, _do_query, {"btn": btn, "marker": created_marker}
                )
                LOG.info(f"{tab_prefix}[QUERY-DEBUG] button 结果: success={result.get('success') if result else None}, error={result.get('error') if result else 'None'}")
                if result and result.get("success"):
                    validated[action] = result
                    # ★ 记录执行顺序
                    _record_execution(action, "main", "query")
                else:
                    LOG.warning(f"{tab_prefix}[QUERY-DEBUG] query 验证失败（button）: {result}")

        elif role == "detail":
            result = await _error_driven_retry(
                page, _do_detail, {
                    "btn": btn, "marker": created_marker,
                    "tab_name": tab_name, "tab_locator": tab_locator,
                }
            )
            if result and result.get("success"):
                validated[action] = result
                # ★ 记录执行顺序
                _record_execution(action, "main", "detail")

        elif role == "update":
            if btn_location in ("row_action", "dropdown"):
                if not created_marker:
                    LOG.warning(f"{tab_prefix}{action} 跳过: create 失败且无可用 marker")
                    # ★ Fix 6: 补充上下文信息
                    skip_result = skipped("create 失败且无可用 marker")
                    skip_result["trigger_text"] = btn_text
                    skip_result["btn_location"] = btn_location
                    skip_result["skip_reason"] = "no_marker_available"
                    validated[action] = skip_result
                else:
                    result = await _error_driven_retry(
                        page, _do_edit, {
                            "btn": btn, "marker": created_marker,
                            "form_filler": form_filler, "framework": framework,
                            "tab_name": tab_name, "tab_locator": tab_locator,
                        }
                    )
                    if result and result.get("success"):
                        validated[action] = result
                        # ★ 记录执行顺序
                        _record_execution(action, "main", "update")
                    elif result:
                        _merge_validated_result(validated, action, result)
            else:
                result = await _error_driven_retry(
                    page, _do_generic_operation, {
                        "btn": btn, "action": action, "marker": created_marker,
                        "form_filler": form_filler,
                        "tab_name": tab_name, "tab_locator": tab_locator,
                    }
                )
                if result and result.get("success"):
                    validated[action] = result
                    # ★ 记录执行顺序
                    _record_execution(action, "main", "update")
                elif result:
                    _merge_validated_result(validated, action, result)

        elif role == "delete":
            if btn_location == "row_action":
                if not created_marker:
                    LOG.warning(f"{tab_prefix}{action} 跳过: create 失败且无可用 marker")
                    # ★ Fix 6: 补充上下文信息
                    skip_result = skipped("create 失败且无可用 marker")
                    skip_result["trigger_text"] = btn_text
                    skip_result["btn_location"] = btn_location
                    skip_result["skip_reason"] = "no_marker_available"
                    validated[action] = skip_result
                else:
                    result = await _error_driven_retry(
                        page, _do_delete, {
                            "btn": btn, "marker": created_marker,
                        }
                    )
                    if result and result.get("success"):
                        validated[action] = result
                        created_marker = None
                        # ★ 记录执行顺序
                        _record_execution(action, "main", "delete")
                    elif result:
                        _merge_validated_result(validated, action, result)
            else:
                if btn_location == "toolbar" and created_marker:
                    selected = await _ensure_row_selected(page, created_marker)
                    if selected:
                        LOG.debug(f"{tab_prefix}已勾选表格行供批量删除使用")

                result = await _error_driven_retry(
                    page, _do_generic_operation, {
                        "btn": btn, "action": action, "marker": created_marker,
                        "form_filler": form_filler,
                        "tab_name": tab_name, "tab_locator": tab_locator,
                    }
                )
                if result and result.get("success"):
                    validated[action] = result
                    created_marker = None
                    # ★ 记录执行顺序
                    _record_execution(action, "main", "delete")
                elif result:
                    _merge_validated_result(validated, action, result)

        elif role == "navigation":
            # ★ 方案批三 3.1：navigation 角色专用截流——验证目标是「跳转发生」
            # （URL 变化或新标签页），不再落入 generic 的表单探索路径（第 6 轮
            # 实证：9 个名称链接操作在目标详情页伪表单上空转 38 分钟）。
            # 秒过即回列表；navigation 不改变行状态，无级联/增量语义。
            result = await _do_navigation_operation(page, {
                "btn": btn, "action": action, "marker": created_marker,
                "tab_name": tab_name, "tab_locator": tab_locator,
            })
            if result and result.get("success"):
                validated[action] = result
                # ★ 记录执行顺序
                _record_execution(action, "main", "navigation")
            elif result:
                _merge_validated_result(validated, action, result)
            continue

        else:
            # generic 角色
            if btn_location == "toolbar" and created_marker:
                selected = await _ensure_row_selected(page, created_marker)
                if selected:
                    LOG.debug(f"{tab_prefix}已勾选表格行供 toolbar 操作使用")

            result = await _error_driven_retry(
                page, _do_generic_operation, {
                    "btn": btn, "action": action, "marker": created_marker,
                    "form_filler": form_filler,
                    "tab_name": tab_name, "tab_locator": tab_locator,
                }
            )
            if result and result.get("success"):
                validated[action] = result
                executed_actions.add(action)
                # ★ 记录执行顺序
                _record_execution(action, "main", "generic")
            elif result and result.get("nav_info"):
                # ★ 部分成功的操作（如授权）：有 nav_info 但 success=False
                # 记录到 validated 但标记为 partial_success
                result["partial_success"] = True
                _merge_validated_result(validated, action, result)
                executed_actions.add(action)
                _record_execution(action, "main", "generic", partial_success=True)
            elif result:
                # ★ 失败结果也写入 validated，与 create/update/delete 角色统一
                # Stage 1 记录实际操作，成功/失败不影响操作记录
                _merge_validated_result(validated, action, result)

        # ★ 通用状态变化检测与级联执行
        # 操作成功后：等待行状态稳定（批一 1.2 双条件 settle，顺带采集矩阵）
        # → 级联执行新启用按钮 → 状态感知调度（批一 1.4 reconcile：
        # 直接执行 ready pending + 有驱动力的恢复转移，替代旧 STATE-RETRY）
        if result and result.get("success") and created_marker:
            settle = await _wait_row_states_stable(page, created_marker, state_ctx=state_ctx)

            # ★ 批一 1.3：row_state_before/after 落 validated + 转移边采集
            # （result 与 validated[action] 同引用，字段直接随产物落盘；
            #   任一端不可读或两端相同 → _record_state_transition 静默跳过）
            if _row_state_before is not None:
                result["row_state_before"] = _row_state_before
            if settle.get("status") is not None:
                result["row_state_after"] = settle["status"]
            _record_state_transition(state_ctx, action, _row_state_before, settle.get("status"))

            cascade_results = await _check_and_execute_newly_enabled(
                page, created_marker, button_states, buttons_by_action,
                executed_actions, form_filler, tab_name, tab_locator, ui_result, tab_prefix
            )

            # 将级联执行的结果写入 validated，并记录执行顺序
            for cascade_action, cascade_result in cascade_results.items():
                if cascade_result:
                    _merge_validated_result(validated, cascade_action, cascade_result)
                    # ★ 记录级联操作的执行顺序
                    _record_execution(cascade_action, "cascade", action_roles.get(cascade_action, "generic"), triggered_by=action)

            # ★ 批一 1.4：状态感知调度（替代 _retry_state_blocked_ops）——
            # 直接执行 pending 中当前已启用的 + 有 pending 驱动的恢复转移
            # （R1 驱动性根治乒乓；settle/矩阵/转移边由 reconcile 内部自理）
            reconcile_results = await _state_aware_reconcile(
                page, created_marker, validated, buttons_by_action, action_roles,
                executed_actions, form_filler, tab_name, tab_locator, tab_prefix,
                trigger_action=action, state_ctx=state_ctx,
            )
            for rc_action, rc_result in reconcile_results.items():
                if not rc_result:
                    continue
                rc_meta = rc_result.pop("_reconcile_meta", None) or {}
                _merge_validated_result(validated, rc_action, rc_result)
                if rc_result.get("success"):
                    _record_execution(rc_action, "cascade",
                                      action_roles.get(rc_action, "generic"),
                                      triggered_by=rc_meta.get("triggered_by") or action)

        # ★ 增量发现：操作成功后，扫描当前行发现新出现的按钮
        # 适用场景：暂停→启用、禁用→启用 等状态依赖的操作对
        if result and result.get("success"):
            should_scan = (
                role in ("generic", "update", "delete")  # 状态变更类操作
                and btn_location in ("row_action", "dropdown")  # 行级操作
                and created_marker  # 有明确的行标识
            )

            LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] 操作成功，检查是否触发增量扫描: "
                     f"role={role}, btn_location={btn_location}, created_marker={created_marker}, "
                     f"should_scan={should_scan}")

            if should_scan:
                # 获取当前行的选择器（基于 marker）
                row_selector = await get_row_selector_by_marker(page, created_marker)
                LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] row_selector={row_selector}")

                if row_selector:
                    # ★ 只传递行级按钮（dropdown/row_action），排除工具栏按钮
                    existing_row_actions = {
                        action for action, btn_info in buttons_by_action.items()
                        if btn_info.get("location") in ("dropdown", "row_action")
                    }
                    LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] buttons_by_action 完整内容:")
                    for _act, _info in buttons_by_action.items():
                        LOG.info(f"  {_act}: location={_info.get('location')}, source={_info.get('source')}, "
                                 f"tag={_info.get('tag')}, selector={_info.get('selector', '')[:80]}")
                    LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] existing_row_actions={existing_row_actions}")

                    scan_result = await _scan_row_after_operation(
                        page, row_selector, created_marker, existing_row_actions
                    )

                    new_buttons = scan_result.get('new_buttons', [])
                    disappeared_buttons = scan_result.get('disappeared_buttons', [])

                    LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] new_buttons={[b.get('text') for b in new_buttons] if new_buttons else []}")

                    # ★ 问题二：记录消失的按钮（状态切换的旧状态）
                    if disappeared_buttons:
                        LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] disappeared_buttons={disappeared_buttons}")
                        LOG.info(f"{tab_prefix}[INCREMENTAL-SCAN] 检测到状态切换按钮对:")
                        for old_text in disappeared_buttons:
                            # 查找对应的新按钮（通常是反义词）
                            # 例如：禁用→启用，锁定→解锁，冻结→解冻
                            LOG.info(f"  {old_text} → 已切换为新状态")

                    if new_buttons:
                        LOG.info(f"{tab_prefix}增量发现 {len(new_buttons)} 个新按钮")

                        # 将新按钮加入待验证队列
                        for new_btn in new_buttons:
                            new_btn["tab"] = tab_name  # 标记 tab
                            new_action = new_btn.get("text", "")

                            # 去重检查
                            if new_action in buttons_by_action:
                                LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 buttons_by_action 中，跳过")
                                continue

                            # ★ 跳过 STATE-CHANGE 链中已执行过的按钮
                            # INCREMENTAL-SCAN 可能重新发现已在级联中执行的状态切换按钮
                            # （初始 disabled 的按钮在 STATE-CHANGE 后变为 enabled，被识别为"新"）
                            if new_action in executed_actions:
                                LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 STATE-CHANGE 中执行过，跳过")
                                # 仍注册到 buttons_by_action（供后续 INCREMENTAL-SCAN 去重），但不加入 ordered_actions
                                buttons_by_action[new_action] = new_btn
                                continue

                            buttons_by_action[new_action] = new_btn

                            # ★ 回写到 ui_result["dropdowns"]：确保增量发现的下拉菜单项
                            # 写入 _ui.json，供 Stage 2 回放时使用
                            if new_btn.get("location") == "dropdown":
                                dropdowns_list = ui_result.setdefault("dropdowns", [])
                                if not any(d.get("text") == new_action and d.get("tab") == tab_name
                                           for d in dropdowns_list):
                                    dropdowns_list.append({
                                        "text": new_action,
                                        "parent": new_btn.get("parent", "更多"),
                                        "tag": "DROPDOWN_ITEM",
                                        "location": "dropdown",
                                        "action": new_action,
                                        "tab": tab_name,
                                    })
                                    LOG.info(f"{tab_prefix}  [WRITEBACK] {new_action} → ui_result.dropdowns")

                            # 推断角色
                            new_role = _infer_action_role(new_action, new_btn, delete_actions=_delete_actions)
                            action_roles[new_action] = new_role

                            # 插入到 ordered_actions 的合适位置
                            # delete 类操作放最后，其他操作放当前位置后面
                            if new_role == "delete":
                                ordered_actions.append(new_action)
                                insert_pos = len(ordered_actions) - 1
                            else:
                                # 在当前 action 后面插入
                                try:
                                    current_idx = ordered_actions.index(action)
                                    ordered_actions.insert(current_idx + 1, new_action)
                                    insert_pos = current_idx + 1
                                except ValueError:
                                    ordered_actions.append(new_action)
                                    insert_pos = len(ordered_actions) - 1

                            LOG.info(f"{tab_prefix}  + {new_action} (角色: {new_role}, 插入位置: {insert_pos}, "
                                     f"ordered_actions={ordered_actions})")

        # 操作后确保回到列表页（统一使用 shared/transition_to_next）
        try:
            from ..shared.transition_helpers import transition_to_next
            await transition_to_next(page, tab_name=tab_name, tab_locator=tab_locator, wait_table=False)
        except Exception as e:
            LOG.error(f"{tab_prefix}transition_to_next 异常: {e}")
            if "has been closed" in str(e):
                LOG.error(f"{tab_prefix}浏览器已关闭，停止后续操作验证")
                break

    # ★ 主循环收尾：行状态可能在最后一批操作后再次翻转，最后跑一轮
    # 状态感知调度（EIS 实证：关机后 Stopped 态解锁的行级操作在主循环
    # 内全部撞禁用/过渡窗口，无后续成功操作触发时在此兜底回收；
    # 批一 1.4 后 reconcile 还可走有驱动力的恢复转移解锁 Stopped 态
    # 依赖的操作——关机→开机边由本轮转移边采集产出）
    if created_marker:
        final_reconcile = await _state_aware_reconcile(
            page, created_marker, validated, buttons_by_action, action_roles,
            executed_actions, form_filler, tab_name, tab_locator, tab_prefix,
            trigger_action="", state_ctx=state_ctx,
        )
        for rc_action, rc_result in final_reconcile.items():
            if not rc_result:
                continue
            rc_meta = rc_result.pop("_reconcile_meta", None) or {}
            _merge_validated_result(validated, rc_action, rc_result)
            if rc_result.get("success"):
                _record_execution(rc_action, "cascade",
                                  action_roles.get(rc_action, "generic"),
                                  triggered_by=rc_meta.get("triggered_by") or "")

        # ★ 批五 F2 自清洁：清空搜索框恢复列表——搜索先行留下的过滤态在
        # Tab 收尾必须恢复，防污染下轮探测与共享环境（幂等，无搜索框 no-op）。
        try:
            from core.discovery.replay.button_driver import clear_table_search
            await clear_table_search(page)
        except Exception as _ce:
            LOG.debug(f"{tab_prefix}清空搜索异常（不阻塞收尾）: {_ce}")

    # ★ 记录未验证的 toolbar 按钮
    validated_actions = set(validated.keys())
    toolbar_buttons = ui_result.get("toolbar_buttons", [])
    for btn in toolbar_buttons:
        btn_text = btn.get("text", "")
        if btn_text and btn_text not in validated_actions:
            execution_order.append({
                "action": btn_text,
                "phase": "main",
                "role": "unvalidated",
                "reason": "toolbar_button_not_validated",
                "trigger_text": btn_text,
                "trigger_locator": btn.get("locator", ""),
            })

    return validated, execution_order


async def _validate_business_flow(page, ui_result: dict, username: str,
                                  state_seed: dict = None) -> tuple:
    """按动态顺序逐一验证按钮的业务闭环。

    支持多 Tab 页面：检测到多个 tab 时，逐一切换 tab 并独立验证每个 tab 的 CRUD 操作。
    操作顺序：create 优先（创建测试数据），delete 最后（清理），其他操作按发现顺序。
    每个按钮执行完整流程：点击 → 填表(如适用) → 提交 → 验证成功。
    验证失败的按钮进入错误驱动重试循环。

    Args:
        page: Playwright Page 对象
        ui_result: discover_all() 返回的探测结果
        username: 用户名
        state_seed: 旧 ui.json 的状态矩阵/转移边种子（批一 1.3 跨轮累积）

    Returns:
        dict: {action: {"success": bool, "fill_data": dict, "selectors": dict, ...}}
              如果关键操作全部失败，返回 None。
    """
    # ★ 批一 1.3：状态采集上下文（跨轮累积——合并旧 ui.json 种子，本轮
    # settle 点观测逐轮补全；引用挂 ui_result 顶层随产物落盘 ui.json，
    # 供 reconcile（1.4）恢复转移消费 + Stage 2 后续扩展）
    state_ctx = _init_state_ctx(state_seed)
    ui_result["state_button_matrix"] = state_ctx["state_button_matrix"]
    ui_result["state_transitions"] = state_ctx["state_transitions"]
    if state_ctx["state_transitions"]:
        LOG.info(f"  [STATE-CTX] 载入跨轮转移边 {len(state_ctx['state_transitions'])} 条"
                 f" / 矩阵状态 {len(state_ctx['state_button_matrix'])} 个")

    # [TAB-STATE-DIAG] 诊断：记录当前 Tab 状态
    try:
        page_structure = ui_result.get("page_structure", {})
        tabs_info = page_structure.get("tabs", [])
        if tabs_info:
            LOG.info(f"    [TAB-STATE-DIAG] 页面有 {len(tabs_info)} 个 Tab:")
            for t in tabs_info:
                active_str = "✓ ACTIVE" if t.get("active") else "✗"
                LOG.info(f"    [TAB-STATE-DIAG]   {active_str} [{t.get('index')}] "
                         f"'{t.get('name')}' locator='{t.get('locator', '')}'")

        # 检查当前实际激活的 Tab（通过 pane 可见性判断）
        current_tab = await page.evaluate("""() => {
            const panes = document.querySelectorAll('.el-tab-pane');
            const result = {
                paneCount: panes.length,
                visiblePanes: [],
                headerPaneMismatch: false
            };

            // 收集所有可见的 pane
            panes.forEach((pane, i) => {
                const style = window.getComputedStyle(pane);
                if (style.display !== 'none' && style.visibility !== 'hidden') {
                    result.visiblePanes.push({
                        index: i,
                        id: pane.id || '',
                        name: pane.getAttribute('aria-label') || pane.querySelector('.el-tab-pane__title')?.textContent || ''
                    });
                }
            });

            // 检查 header 和 pane 的激活状态是否一致
            const activeHeaders = document.querySelectorAll('.el-tabs__item.is-active');
            const activePanes = result.visiblePanes.length;

            // 如果 header 数量和可见 pane 数量不匹配，说明状态不同步
            if (activeHeaders.length !== activePanes) {
                result.headerPaneMismatch = true;
                result.activeHeaderCount = activeHeaders.length;
                result.activeHeaderTexts = Array.from(activeHeaders).map(h => h.textContent.trim());
            }

            return result;
        }""")

        if current_tab.get('paneCount', 0) == 0:
            LOG.info(f"    [TAB-STATE-DIAG] 页面无 tab-pane 结构")
        else:
            visible_count = len(current_tab.get('visiblePanes', []))
            LOG.info(f"    [TAB-STATE-DIAG] 共 {current_tab.get('paneCount')} 个 pane, {visible_count} 个可见")
            for vp in current_tab.get('visiblePanes', []):
                LOG.info(f"    [TAB-STATE-DIAG]   可见 pane[{vp.get('index')}] id='{vp.get('id', '')}' name='{vp.get('name', '')}'")

            if current_tab.get('headerPaneMismatch'):
                LOG.warning(f"    [TAB-STATE-DIAG] ⚠️ Header-Pane 状态不同步！")
                LOG.warning(f"    [TAB-STATE-DIAG]   active headers: {current_tab.get('activeHeaderCount')} 个 {current_tab.get('activeHeaderTexts', [])}")
                LOG.warning(f"    [TAB-STATE-DIAG]   visible panes: {visible_count} 个")
    except Exception as e:
        LOG.warning(f"    [TAB-STATE-DIAG] 诊断失败: {e}")

    # [Toast Fix A] 注入 MutationObserver 捕获瞬态通知（el-message / el-notification）
    # Stage 1 之前没有注入，导致 __captured_messages 始终为空
    await _install_message_capture(page)

    # 收集所有按钮（包括 dropdowns）
    all_buttons = (
        ui_result.get("toolbar_buttons", [])
        + ui_result.get("row_actions", [])
        + ui_result.get("dropdowns", [])
    )

    # ★ 使用 shared/constants 统一常量（消除重复定义）
    from core.discovery.shared.constants import (
        NON_BUSINESS_BUTTONS, BATCH_PREFIXES,
        DESTROY_KEYWORDS, NAVIGATION_CLASS_KEYWORDS
    )
    _NON_BUSINESS_BUTTONS = NON_BUSINESS_BUTTONS
    _DESTROY_KEYWORDS = DESTROY_KEYWORDS
    _NAVIGATION_CLASS_KEYWORDS = NAVIGATION_CLASS_KEYWORDS
    _BATCH_PREFIXES = BATCH_PREFIXES

    # 收集行级操作文本，用于去重批量操作
    # ★ 触发器判定用包含式 helper（「更多操作」组合文本命中，精确匹配漏判实证）
    _row_action_texts = {
        btn.get("text", "") for btn in all_buttons
        if btn.get("location") in ("row_action", "dropdown")
        and not is_dropdown_trigger_text(btn.get("text"))
    }
    has_row_destroy = any(
        any(kw in ra_text.lower() for kw in _DESTROY_KEYWORDS)
        for ra_text in _row_action_texts
    )

    # 按 Tab 分组按钮，过滤非业务按钮
    buttons_by_tab = {}  # {tab_name: {action: btn}}

    for btn in all_buttons:
        # 跳过下拉菜单触发器（包含式判定：「更多操作」等组合文本同样命中）
        if is_dropdown_trigger_text(btn.get("text")) and btn.get("tag") != "DROPDOWN_ITEM":
            continue

        # 跳过分页跳转等非业务按钮
        if btn.get("text") in _NON_BUSINESS_BUTTONS:
            continue

        # 跳过面包屑、分页等非业务 DOM 元素
        btn_cls = (btn.get("className") or "").lower()
        if any(kw in btn_cls for kw in _NAVIGATION_CLASS_KEYWORDS):
            LOG.debug(f"  跳过非业务元素: '{btn.get('text', '')}' (cls={btn_cls[:40]})")
            continue

        # 跳过导航跳转按钮
        btn_text_val = btn.get("text", "")
        if any(kw in btn_text_val for kw in ("前往", "跳转至")):
            LOG.info(f"  跳过导航按钮: '{btn_text_val}'")
            continue

        # 去重：如果 toolbar/dropdown 按钮是"批量 X"，且有对应的行级操作"X"，则跳过
        if btn.get("location") in ("toolbar", "dropdown"):
            text = btn.get("text", "")
            skip = False
            for prefix in _BATCH_PREFIXES:
                if text.startswith(prefix):
                    core_text = text[len(prefix):]
                    if core_text in _row_action_texts:
                        LOG.debug(f"  跳过批量操作 '{text}'（有行级对应 '{core_text}'）")
                        skip = True
                        break
            # 语义级去重：如果有行级删除操作，跳过 toolbar 删除
            if not skip:
                is_toolbar_destroy = any(kw in text.lower() for kw in _DESTROY_KEYWORDS)
                if is_toolbar_destroy and has_row_destroy:
                    LOG.info(f"  跳过 toolbar 删除 '{text}'（有行级删除操作可用）")
                    skip = True
            if skip:
                continue

        # 使用按钮文本作为 action
        action = btn.get("action", "") or btn.get("text", "")
        if not action:
            continue

        # 获取 tab 标记
        tab_name = btn.get("tab", "")

        # 按 tab 分组
        if tab_name not in buttons_by_tab:
            buttons_by_tab[tab_name] = {}

        # 优先选择 BUTTON 元素
        existing = buttons_by_tab[tab_name].get(action)
        if not existing or (existing.get("tag") != "BUTTON" and btn.get("tag") == "BUTTON"):
            buttons_by_tab[tab_name][action] = btn

    # ★ 批五 F4-1：逐行名称链接归并（buttons_by_tab 构建后、Tab 验证前）——
    #   逐行 SPAN 名称链接 → 单条「查看详情（名称链接）」row_link 语义操作；
    #   无名称链接形态的模块（estack 等 BUTTON 型行操作）零变化幂等。
    _merge_row_link_actions(buttons_by_tab, ui_result)

    # 按 Tab 独立验证
    validated = {}
    all_execution_order = []  # ★ 聚合所有 tab 的执行顺序
    tabs_info = page_structure.get("tabs", [])

    for tab_name, tab_buttons in buttons_by_tab.items():
        # 切换到该 Tab（如果有多个 Tab）
        if tab_name and len(tabs_info) > 1:
            tab_info = next((t for t in tabs_info if t.get("name") == tab_name), None)
            # ★ Tab 切换：locator 优先，无 id 的 tab 用文本定位
            # （与回放端 _step_click_tab / shared/tab_helpers 同源的文本策略）
            _tab_click_locator = (tab_info.get("locator", "") if tab_info else "")
            if not _tab_click_locator and tab_name:
                _tab_click_locator = (
                    f'[role="tab"]:has-text("{tab_name}"), '
                    f'.el-tabs__item:has-text("{tab_name}"), '
                    f'.ant-tabs-tab:has-text("{tab_name}")'
                )
            if _tab_click_locator:
                LOG.info(f"  📑 切换到 Tab: {tab_name}")
                try:
                    await page.click(_tab_click_locator, timeout=5000)
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    LOG.warning(f"  ⚠️ Tab 切换失败: {e}")
                    continue

            # ★ DIAG: 切换 Tab 后检查该 Tab 的 Switch 状态
            try:
                _switch_diag = await page.evaluate("""(tabName) => {
                    const panes = document.querySelectorAll('.el-tab-pane');
                    let activePane = null;
                    for (const p of panes) {
                        const style = window.getComputedStyle(p);
                        if (style.display !== 'none' && p.offsetWidth > 0) {
                            activePane = p;
                            break;
                        }
                    }
                    if (!activePane) return {error: 'no visible pane'};

                    const switches = activePane.querySelectorAll('.el-switch');
                    const switchInfo = Array.from(switches).map((sw, i) => ({
                        index: i,
                        isChecked: sw.classList.contains('is-checked'),
                        ariaChecked: sw.querySelector('input')?.getAttribute('aria-checked'),
                        text: sw.closest('.el-form-item')?.querySelector('.el-form-item__label')?.textContent?.trim()
                              || sw.closest('.toolbar')?.textContent?.trim()?.substring(0, 30)
                              || `switch-${i}`,
                        inRow: !!sw.closest('.el-table__row'),
                    }));

                    return {
                        paneId: activePane.id || '',
                        paneName: activePane.getAttribute('aria-label') || '',
                        switchCount: switches.length,
                        switches: switchInfo,
                    };
                }""", tab_name)
                LOG.info(f"    [TAB-SWITCH-DIAG] Tab '{tab_name}' 切换后 Switch 状态: {_switch_diag}")
            except Exception as e:
                LOG.warning(f"    [TAB-SWITCH-DIAG] 诊断失败: {e}")

        # 在该 Tab 上下文中执行完整 CRUD 验证
        tab_validated, tab_exec_order = await _validate_single_tab_crud(
            page, tab_buttons, ui_result, username, tab_name,
            tab_locator=tab_info.get("locator", "") if tab_name and tab_info else "",
            state_ctx=state_ctx,
        )

        # 聚合 execution_order
        all_execution_order.extend(tab_exec_order)

        # 合并结果（多 Tab 时加前缀）
        for action, result in tab_validated.items():
            result["tab"] = tab_name
            if tab_name and len(tabs_info) > 1:
                # 多 Tab 页面：操作名加 tab 前缀（避免冲突）
                validated[f"{tab_name}/{action}"] = result
            else:
                # 单 Tab 页面：保持原名
                validated[action] = result

    # 判断是否有关键操作失败（create-like 和 delete-like 角色）
    # 需要从 buttons_by_tab 重建 action_roles
    all_buttons_flat = {}
    for tab_buttons in buttons_by_tab.values():
        all_buttons_flat.update(tab_buttons)

    action_roles = {}
    for action, btn in all_buttons_flat.items():
        _create_action, _delete_actions = _find_create_delete_actions(all_buttons_flat)
        action_roles[action] = _infer_action_role(action, btn, delete_actions=_delete_actions)

    has_create_role = any(r == "create" for r in action_roles.values())
    has_delete_role = any(r == "delete" for r in action_roles.values())

    # 检查 validated 中的 key（可能带 tab 前缀）
    create_verified = any(
        validated.get(k, {}).get("success")
        for k in validated
        if action_roles.get(k.split("/")[-1]) == "create"
    )
    delete_verified = any(
        validated.get(k, {}).get("success")
        for k in validated
        if action_roles.get(k.split("/")[-1]) == "delete"
    )

    failed_critical = []
    if has_create_role and not create_verified:
        failed_critical.append("create-like")
    if has_delete_role and not delete_verified:
        failed_critical.append("delete-like")

    if failed_critical:
        LOG.error(f"  Stage 1 关键操作验证失败: {failed_critical}")
        LOG.info(f"  返回 {len(validated)} 个已验证操作（含失败操作）供 Stage 2 使用")
        return validated, all_execution_order

    LOG.info(f"  Stage 1 验证完成: {len(validated)} 个操作通过")
    return validated, all_execution_order




# ============================================================
# 错误驱动重试循环
# ============================================================

# P2: 不可重试错误 — 环境限制，重试多少次都没用
TERMINAL_ERRORS = {
    "env_dependency",           # 需要上传文件（import）
    "permission_denied",        # 权限不足
    "resource_not_found",       # 资源不存在
    "required_field_empty",     # 必填字段为空（无选项数据，重试无法自愈）
    "confirm_button_disabled",  # 确认按钮被禁用
    "api_error",                # 后端返回明确业务错误，重试无意义
}

# P2: 委托专用处理器
DELEGATE_ERRORS = {
    "navigation_unexplored": "_handle_cross_page_operation",  # → P3
    "cross_page_partial": "_handle_cross_page_operation",
}


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
    qga_wait_done = False  # ★ 批一 1.5：QGA 就绪等待每次调用只做一次
    max_rounds = 10  # 安全上限
    first_ms_details = None  # ★ 保存第一次 attempt 的 multi_step_field_details（从空状态记录，最完整）
    first_round_result = None  # ★ 保存第 1 轮完整失败结果（信息丰度回退用）

    for round_num in range(max_rounds):
        try:
            result = await operation_fn(page, context)
        except Exception as e:
            LOG.warning(f"    操作异常: {e}")
            result = exception(str(e))

        # ★ 保存第一次 attempt 的 multi_step_field_details 与完整结果
        # 第一次从空表单开始，记录的 interaction_steps 最完整
        # 后续 attempt 因表单状态残留（multi-select 有值、cascader 有预选），步骤可能不完整
        if round_num == 0:
            first_round_result = copy.deepcopy(result)
            if result.get("multi_step_field_details"):
                first_ms_details = copy.deepcopy(result["multi_step_field_details"])
                LOG.debug(f"    [_error_driven_retry] 保存第 1 轮的 multi_step_field_details ({len(first_ms_details)} 个字段)")

        if result.get("success"):
            if round_num > 0:
                LOG.info(f"    第 {round_num + 1} 轮尝试成功")
            # ★ 成功时，用第一次的 multi_step_field_details 替换（第一次从空状态记录，步骤最完整）
            if first_ms_details and round_num > 0:
                result["multi_step_field_details"] = first_ms_details
                LOG.info(f"    [_error_driven_retry] 使用第 1 轮的 multi_step_field_details 替换（共 {len(first_ms_details)} 个字段）")
            # ★ O1: 将按钮状态注入到返回结果
            if context.get("_button_state"):
                result["button_state"] = context["_button_state"]
            # ★ O2: 将 Tab 信息注入到返回结果
            if context.get("tab_name"):
                result["tab_info"] = {
                    "tab_name": context.get("tab_name"),
                    "tab_locator": context.get("tab_locator"),
                }
            # ★ O3: 将分页查找轨迹注入到返回结果
            if context.get("_pagination_trace"):
                result["pagination_trace"] = context["_pagination_trace"]
            return result

        # 提取错误信息
        error_type = result.get("error_type", "unknown")
        error_text = result.get("error_text", "")
        error_field = result.get("error_field", "")
        current_error_key = f"{error_type}|{error_field}|{error_text}"

        LOG.info(f"    第 {round_num + 1} 轮失败: [{error_type}] field={error_field} text={error_text[:120]}"
                 f" is_terminal={error_type in TERMINAL_ERRORS}")

        # ★ 批一 1.5：QGA 就绪等待——失败签名含 QGA（如「QGA 未就绪」）→
        # 轮询行级 qgaReady（Vue command.row，_read_row_status 顺带读取）
        # 上限 60s，就绪后进入下一轮重试（即「重试一次」，qga_wait_done
        # 保证每次调用只等一次）。不做全局硬等（无 QGA 镜像会挂死整轮）；
        # qga 不可读时立即放弃。置于 TERMINAL 检查之前：QGA 类业务错误
        # 可能被归为 api_error 终态，先等后判不破坏终态防死循环语义
        # （等待一次性有界，若再次失败仍按终态正常返回）。
        if (not qga_wait_done
                and context.get("marker")
                and "qga" in (error_text or "").lower()):
            qga_wait_done = True
            qga_ok = await _wait_qga_ready(page, context.get("marker"), timeout_ms=60000)
            if qga_ok:
                LOG.info("    [QGA-WAIT] QGA 已就绪，重试操作")
                last_error_key = None
                continue
            LOG.warning("    [QGA-WAIT] QGA 等待超时或不可读，按常规错误处理")

        # P2: 不可重试错误 → 立即返回
        if error_type in TERMINAL_ERRORS:
            LOG.info(f"    [{error_type}] 不可重试，直接标记")
            return result

        # P2: 委托专用处理器（如跨页面操作）
        if error_type in DELEGATE_ERRORS:
            handler_name = DELEGATE_ERRORS[error_type]
            # Lazy import for cross-module handler
            from core.discovery.operation_executor.navigation_executor import _handle_cross_page_operation
            handler = _handle_cross_page_operation
            if handler and callable(handler):
                LOG.info(f"    [{error_type}] 委托给 {handler_name} 处理")
                return await handler(page, context, result)
            else:
                LOG.warning(f"    [{error_type}] 处理器 {handler_name} 未找到，标记失败")
                return result

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
            # Vision 也用过了或无新 insights — 返回最后一次结果（保留 error_type/selectors）
            LOG.warning(f"    所有补救策略已尝试，操作 {context.get('btn', {}).get('text', '?')} 验证失败")
            # ★ 信息丰度回退：末轮结果可能因页面状态损坏（如 page-nav 表单页未回退，
            # 第 2/3 轮 click_failed 连按钮都没点到）丢失全部表单记录；
            # 第 1 轮（从空表单开始）记录最完整（form_fields + multi_step_field_details
            # + trigger_locator_verified + 业务错误证据）。若末轮信息缺失而首轮有，
            # 返回首轮完整失败结果——builder 依赖这些结构字段生成 create 步骤与
            # expected_failure 断言，丢失会导致 playbook 只剩 1 步 click_tab
            # （实证：黑白名单新增 form_validation「请选择组织」被末轮 click_failed 覆盖）。
            if first_round_result:
                _first_richer = (
                    (not result.get("form_fields") and bool(first_round_result.get("form_fields")))
                    or (not result.get("multi_step_field_details")
                        and bool(first_round_result.get("multi_step_field_details")))
                    or (not result.get("trigger_locator_verified")
                        and bool(first_round_result.get("trigger_locator_verified")))
                )
                if _first_richer:
                    LOG.info(f"    [_error_driven_retry] 末轮结果信息不全，回退返回第 1 轮完整失败结果 "
                             f"(error_type={first_round_result.get('error_type')})")
                    return first_round_result
            return result
        else:
            last_error_key = current_error_key

        # 基于错误信息做针对性修改
        context = _apply_fix(result, context)

    LOG.warning(f"    超过最大重试次数 ({max_rounds})")
    return result


# ============================================================
# marker 提取辅助函数
# ============================================================

def _determine_marker_with_source(fields: list, fill_data: dict) -> tuple:
    """取表单中第一个 input 类型字段的值作为 marker。

    选择逻辑：
      - 遍历 fields，找到第一个 type=="input" 的字段
      - 从 fill_data 中获取该字段的值
      - 无 input 字段 → (None, None)

    Returns:
        (marker_value, marker_source)
        marker_source = {"type": "input", "field_label": str}
        或 (None, None)
    """
    for f in (fields or []):
        if f.get("type") == "input":
            label = f.get("label", "")
            value = (fill_data or {}).get(label, "")
            if value:
                return value, {"type": "input", "field_label": label}
    return None, None


# ============================================================
# 各操作的执行函数
# ============================================================

def _build_create_error_result(
    error_type: str, error_text: str,
    btn_text: str, btn_click_result: dict,
    fill_data: dict, fill_rules: dict, fields: list,
    submit_result: dict, multi_step_field_details: list,
    username: str,
    radio_field_details: list = None,
    cascade_actions: list = None,
) -> dict:
    """构建 create 操作的失败结果，保留表单数据供 playbook 生成完整步骤。

    当表单已提交但业务失败时（如权限不足、重复数据等），需要保留完整的表单填写
    和提交信息，以便 builder.py 生成完整的 playbook 步骤（包括表单填写和提交）。
    """
    from .result_factory import make_error

    # ★ NoneType 防御：跳过提交路径（必填多步字段填充失败）传 submit_result=None——
    # 此前直接 .get() 抛 AttributeError，exception 掩盖真实错误类型
    # （multi_step_required_failed），Stage 2 无法针对性复现填充失败场景
    submit_result = submit_result or {}

    # 归一化文本
    trigger_text = " ".join(btn_text.split())
    submit_text = submit_result.get("text", "")
    submit_text_normalized = " ".join(submit_text.split())

    # 使用 submit_form_v2 返回的已验证 locator
    submit_locator = submit_result.get("locator", "")
    trigger_locator = btn_click_result.get("locator")

    if not trigger_locator:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text)
        trigger_locator = f"{trigger_tag}:has-text('{actual_text}')"

    # 构建结果（包含表单数据，供 playbook 使用）
    result = make_error(error_type, error_text,
                       trigger_text=trigger_text,
                       trigger_locator_verified=trigger_locator)
    result["success"] = False
    result["form_submitted"] = True  # 标记表单已提交
    result["fill_data"] = fill_data
    result["fill_rules"] = fill_rules
    result["form_fields"] = fields
    result["multi_step_field_details"] = multi_step_field_details
    result["radio_field_details"] = radio_field_details  # ★ 错误路径也需记录 radio 详情
    result["cascade_actions"] = cascade_actions  # ★ 错误路径也需记录级联操作（如 transfer-box）
    result["trigger_xpath_index"] = btn_click_result.get("xpath_index", -1)
    result["trigger_clean_text"] = btn_click_result.get("clean_text", "")
    result["submit_locator"] = submit_locator
    result["submit_locator_verified"] = submit_locator
    result["submit_text"] = submit_text_normalized
    result["submit_click_strategy"] = submit_result.get("click_strategy", "playwright")
    result["submit_xpath_index"] = submit_result.get("xpath_index", -1)
    result["submit_clean_text"] = submit_result.get("clean_text", "")
    result["dialog_detected"] = True
    result["interaction_mode"] = "dialog"
    result["selectors"] = {
        "trigger": btn_text,
        "submit": submit_text,
    }

    # 提取 marker 及来源
    marker, marker_source = _determine_marker_with_source(fields, fill_data)
    result["marker"] = marker
    result["marker_source"] = marker_source

    return result


async def _do_create(page, context: dict) -> dict:
    """执行创建操作：点击创建按钮 → 填表 → 提交 → 验证。"""
    btn = context["btn"]
    fields = context.get("fields", [])
    form_filler = context["form_filler"]
    framework = context.get("framework", "element-ui")
    username = context.get("username", "test")

    btn_text = btn.get("text", "")

    # 0. 前置条件：确保 switch 开启（某些页面的 switch 控制按钮可用性）
    await _ensure_switch_enabled(page)

    # 1. 点击创建按钮（注册新标签页监听）
    url_before = page.url
    new_tab_event, new_tab_ref, cleanup_tab_listener = _setup_new_tab_listener(page)

    btn_click_result = await click_page_button(page, btn_text)
    if not btn_click_result["clicked"]:
        cleanup_tab_listener()
        return click_failed(f"无法点击按钮: {btn_text}",
                           trigger_text=btn_text,
                           trigger_locator_verified=None)

    # 2. 统一后点击检测（Phase 1a：新标签页 + 页面跳转）
    obs = await _observe_post_click(
        page, url_before, timeout_ms=3000,
        new_page_event=new_tab_event, new_page_ref=new_tab_ref
    )
    cleanup_tab_listener()

    # 2.5 确定提交按钮的搜索范围（避免误点击反馈弹窗内的按钮）
    from core.discovery.operation_executor.base_executor import _determine_submit_scope
    scope_selector = await _determine_submit_scope(page, obs)
    LOG.info(f"    [scope] scenario={obs['scenario']}, container_type={obs.get('container_type', '')}, scope_selector={scope_selector[:80] if scope_selector else '(empty)'}")

    # 2a. 新标签页场景
    if obs["scenario"] == "new_tab":
        new_page = obs["new_page"]
        LOG.info(f"    [new-tab] create 在新标签页打开: {new_page.url[:60]}")
        from core.discovery.operation_executor.navigation_executor import (
            _explore_and_operate_in_new_page,
        )
        nav_info = await _explore_and_operate_in_new_page(
            page=new_page, action="create", new_url=new_page.url,
            depth=0, max_depth=3,
        )
        try:
            await new_page.close()
        except Exception:
            pass
        submit_result = nav_info.get("submit_result", {})
        if submit_result and submit_result.get("success"):
            return {
                "success": True,
                "nav_info": nav_info,
                "trigger_text": btn_text,
                "fill_data": nav_info.get("fill_data", {}),
                "selectors": {"trigger": btn_text},
                "trigger_locator_verified": btn_click_result.get("locator"),
                "interaction_mode": "new_tab",
            }
        return make_error("new_tab_create_failed",
                         f"新标签页创建操作未完成: {nav_info.get('error_type', '')}",
                         trigger_text=btn_text,
                         nav_info=nav_info,
                         trigger_locator_verified=btn_click_result.get("locator"))

    # 2b. 页面跳转场景（如 /create, /add）— 让现有流程继续
    if obs["scenario"] == "page_nav":
        LOG.info(f"    [page-nav] create 触发页面跳转: {url_before[:40]} → {page.url[:40]}")
        # 不 return，让后续的 scan_form_fields_v2 在新页面上执行

    # 2c. 等待页面就绪（如果 _observe_post_click 已等待过则跳过）
    if obs["scenario"] == "none":
        await page.wait_for_timeout(1500)
        await wait_for_loading_complete(page)

    # 3. 扫描表单字段（如果探测阶段没扫到或需要刷新）
    if not fields:
        fields = await form_filler.scan_form_fields_v2()
        context["fields"] = fields  # 更新 context，供 _apply_fix() 使用
    if not fields:
        # 无表单字段的特殊情况：可能是简单确认对话框（如 AccessKey 创建）
        # 尝试直接点击确认按钮
        from core.discovery.replay.button_driver import confirm_dialog
        confirmed = await confirm_dialog(page)
        if confirmed:
            await page.wait_for_timeout(1500)
            captured_msgs = await _get_captured_messages(page)
            success, _detail = await _verify_operation_success(page, "create", captured_messages=captured_msgs,
                                                              url_before=url_before)
            if success:
                LOG.info(f"    无表单字段的确认对话框操作成功")
                # 构建 trigger_locator_verified（与正常 _do_create 路径一致）
                trigger_locator_verified = btn_click_result.get("locator")
                if not trigger_locator_verified:
                    trigger_tag = btn_click_result.get("tag", "button")
                    actual_text = btn_click_result.get("actual_text", btn_text)
                    trigger_locator_verified = f"{trigger_tag}:has-text('{actual_text}')"
                return {
                    "success": True,
                    "confirmed": confirmed,
                    "trigger_text": btn_text,
                    "fill_data": {},
                    "selectors": {"trigger": btn_text, "confirm": confirmed},
                    "trigger_locator_verified": trigger_locator_verified,
                }
        from core.discovery.ui_scanner.button_detector import _close_dialog
        await _close_dialog(page)
        return no_fields("未扫描到表单字段且确认对话框处理失败",
                        trigger_text=btn_text,
                        trigger_locator_verified=btn_click_result.get("locator"))

    # 4. 生成填充规则和数据
    fill_rules = generate_fill_rules(fields)
    fill_data = generate_fill_data(fields, username)
    # 应用之前的修复
    if context.get("fill_overrides"):
        fill_data.update(context["fill_overrides"])

    # 4.5 初始化 multi_step_field_details（供 step 6 更新）
    from core.discovery import const as _const
    multi_step_field_details = []
    for field in fields:
        kb_cat = field.get("kb_category", "")
        if kb_cat in _const.MULTI_STEP_TYPES or kb_cat == "form-checkbox":
            entry = {
                "label": field.get("label"),
                "kb_category": kb_cat,
                "selector": field.get("selector"),
                "playwright_locator": field.get("playwright_locator"),
                "fill_rule": fill_rules.get(field.get("label", ""), {}),
                "is_editable": False,
            }
            # ★ ip-input: 显式标记为"复合输入字段"（使用 sub_selectors，不需要 locator）
            if kb_cat == "ip-input":
                entry["requires_locator"] = False  # 明确标记不需要 playwright_locator
                entry["sub_selectors"] = field.get("subSelectors", [])
                entry["input_count"] = field.get("inputCount", 4)
                entry["ip_version"] = field.get("ipVersion", "v4")
            # ★ composite-input: 显式标记为"复合输入字段"（使用 sub_components，不需要 locator）
            if kb_cat == "composite-input":
                entry["requires_locator"] = False
                entry["sub_components"] = field.get("subComponents", [])
            multi_step_field_details.append(entry)

    # 5. 填充表单（传入预生成的 fill_data）
    fill_result = await form_filler.fill_create_form(fields, username, fill_data)
    filled = fill_result["filled"] if isinstance(fill_result, dict) else fill_result
    # ★ 提取 radio 和 cascade 信息，供错误路径也能记录
    radio_selections = fill_result.get("radio_selections", {}) if isinstance(fill_result, dict) else {}
    cascade_actions = fill_result.get("cascade_actions", []) if isinstance(fill_result, dict) else []
    # 构建 radio_field_details（供错误路径使用）
    radio_field_details = []
    for field in fields:
        if field.get("kb_category") == "radio":
            field_label = field.get("label")
            actual_option_text = radio_selections.get(field_label, "")
            radio_field_details.append({
                "label": field_label,
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": actual_option_text,
                "fill_rule": field.get("fill_rule", {}),
            })
    # ★ R3: 合并 FormFiller 记录的 radio 详情（interaction_steps / all_options / 显隐字段 diff）
    _radio_details_from_fill = fill_result.get("radio_field_details", []) if isinstance(fill_result, dict) else []
    if _radio_details_from_fill:
        _radio_by_label = {rd.get("label"): rd for rd in _radio_details_from_fill}
        for rd_entry in radio_field_details:
            _src = _radio_by_label.get(rd_entry.get("label"))
            if _src:
                if _src.get("interaction_steps"):
                    rd_entry["interaction_steps"] = _src["interaction_steps"]
                if _src.get("all_options"):
                    rd_entry["all_options"] = _src["all_options"]
                # ★ 显隐输入项 diff（泛化能力）: 空列表 [] 也要保留（= 探测过但无变化）
                if _src.get("revealed_fields") is not None:
                    rd_entry["revealed_fields"] = _src["revealed_fields"]
                if _src.get("hidden_fields") is not None:
                    rd_entry["hidden_fields"] = _src["hidden_fields"]
    # 重新扫描以获取最新字段状态
    if context.get("fill_overrides"):
        for label, value in context["fill_overrides"].items():
            try:
                # 深度扫描：识别复合组件结构，精确定位目标 input
                from core.discovery.ui_scanner.form_scanner import _deep_scan_form_item, _resolve_label_selector
                deep_info = await _deep_scan_form_item(page, label)
                if deep_info.get("found"):
                    # 复合组件：取最后一个可见 input（避开 el-select 内部的）
                    input_elements = [e for e in deep_info.get("elements", []) if e["type"] == "input"]
                    if input_elements:
                        # 优先使用深度扫描返回的 selector
                        target = input_elements[-1]
                        selector = target.get("selector")
                        if selector:
                            await page.fill(selector, value, timeout=3000)
                            LOG.info(f"    复合组件填充 (via deep selector): {label} → {value[:20]}...")
                            continue
                # 回退：按位置索引在同级中定位 form-item
                css = await _resolve_label_selector(page, label)
                if css:
                    await page.fill(css, value, timeout=3000)
            except Exception as e:
                LOG.debug(f"    填充 override {label} 失败: {e}")

    # 6. 处理多步组件
    ms_filled, ms_details, ms_cascade_actions = await form_filler.fill_multi_step_fields(fields, framework)

    # ★ Phase 4b: 合并级联 actions（radio + multi_step）
    if ms_cascade_actions:
        cascade_actions.extend(ms_cascade_actions)
        LOG.info(f"    [cascade] 合并 multi_step 级联: {len(ms_cascade_actions)} 个")

    # 跟踪因无选项被跳过的字段
    skipped_no_options = [d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"]
    if skipped_no_options:
        LOG.info(f"    以下字段因无选项被跳过: {skipped_no_options}")

    # 更新 multi_step_field_details 中的实际执行结果
    # 过滤掉 Stage 1 跳过的字段（如 no_options），只保留实际填充成功的
    # 确保 playbook 不包含 Stage 1 未完成的步骤
    for detail in ms_details:
        label = detail["label"]
        kb_cat = detail.get("kb_category", "")
        if detail.get("skipped_reason"):
            # 从 multi_step_field_details 中移除跳过的字段
            multi_step_field_details = [
                f for f in multi_step_field_details
                if not (f["label"] == label and f.get("kb_category", "") == kb_cat)
            ]
            continue
        for ms_field in multi_step_field_details:
            if ms_field["label"] == label and ms_field.get("kb_category", "") == kb_cat:
                ms_field["is_editable"] = detail.get("is_editable", False)
                # 移除 option_text（Stage 2 不再依赖文本回放）
                if "select_sub_type" in detail:
                    ms_field["select_sub_type"] = detail["select_sub_type"]
                # 传递 interaction_steps（Stage 2 按步骤回放）
                if "interaction_steps" in detail:
                    ms_field["interaction_steps"] = detail["interaction_steps"]
                if detail.get("playwright_locator"):
                    ms_field["playwright_locator"] = detail["playwright_locator"]
                break

    # ✅ P6: 检查必填多步字段是否填充成功
    required_failed = []
    for detail in ms_details:
        label = detail.get("label", "")
        # 查找对应的字段定义，检查是否必填
        field_def = next((f for f in fields if f.get("label") == label), None)
        is_required = field_def and field_def.get("required", False)
        # 检查是否填充成功（is_editable 或 interaction_steps 非空，且没有 skipped_reason）
        is_success = (detail.get("is_editable", False) or detail.get("interaction_steps")) \
                     and not detail.get("skipped_reason")
        # ★ F4: 「无选项」（no_options）的必填字段不再预先阻断提交。
        # 依据：is-required 是 form-item 级信号，复合 form-item 内的非主
        # select 会被过宽继承 required；且「标了 required 但环境无选项」时，
        # 应让真实表单校验做最终裁决——非必填则提交成功创建记录（marker 出现，
        # create 成功）；真必填则表单校验拦截、marker 自然缺失，create 如实
        # 判失败（三阶段一致性：探测记录的是真实行为而非预判）。其他失败原因
        # （expand_failed/verify_failed 等真实交互故障）保持阻断，避免提交
        # 注定失败的表单。
        if is_required and not is_success and detail.get("skipped_reason") != "no_options":
            required_failed.append(label)
        elif is_required and detail.get("skipped_reason") == "no_options":
            LOG.info(f"    必填多步字段 {label} 无选项，放行提交由表单校验终裁")

    # 也检查未被 fill_multi_step_fields 处理的必填字段
    handled_labels = {d.get("label") for d in ms_details}
    for f in fields:
        if f.get("kb_category") in const.MULTI_STEP_TYPES or f.get("kb_category") == "form-checkbox":
            if f.get("label") not in handled_labels and f.get("required", False):
                required_failed.append(f.get("label"))

    if required_failed:
        LOG.warning(f"    必填多步字段填充失败: {required_failed}，跳过提交")
        from core.discovery.ui_scanner.button_detector import _close_dialog
        await _close_dialog(page)
        return _build_create_error_result(
            "multi_step_required_failed",
            f"必填多步字段填充失败: {', '.join(required_failed)}",
            btn_text, btn_click_result,
            fill_data, fill_rules, fields, None,
            multi_step_field_details, username,
            radio_field_details=radio_field_details,
            cascade_actions=cascade_actions
        )

    if ms_filled == 0:
        # 兜底：尝试 select_dropdowns()
        select_count = await form_filler.select_dropdowns()
        if select_count > 0:
            LOG.info(f"    兜底 select_dropdowns: 选择了 {select_count} 个下拉框")

    # 7. 提交
    submit_result = await form_filler.submit_form_v2(scope_selector=scope_selector)
    # ★ 批五 F3：提交守卫终态优先——confirm_word_missing（按钮 disabled 未点击）/
    #   dialog_still_open（点击后同指纹弹窗滞留）均为「提交未生效」终态，
    #   不进结果验证、不回退（守卫返回 locator 为空/滞留语义）。
    if submit_result.get("submit_blocked"):
        LOG.warning(f"    [F3] 提交守卫拦截: {submit_result['submit_blocked']}"
                    f" —— {submit_result.get('blocked_reason', '')}")
        from core.discovery.ui_scanner.button_detector import _close_dialog
        await _close_dialog(page)
        return _build_create_error_result(
            submit_result["submit_blocked"],
            submit_result.get("blocked_reason") or "提交被守卫拦截（未生效）",
            btn_text, btn_click_result,
            fill_data, fill_rules, fields, submit_result,
            multi_step_field_details, username,
            radio_field_details=radio_field_details,
            cascade_actions=cascade_actions
        )
    if not submit_result.get("locator"):
        from core.discovery.ui_scanner.button_detector import _close_dialog
        await _close_dialog(page)
        return submit_failed("未找到提交按钮",
                            trigger_text=btn_text,
                            trigger_locator_verified=btn_click_result.get("locator"))

    # 8. 等待结果
    await page.wait_for_timeout(2000)

    # 9. 检查表单校验错误
    errors = await read_form_errors(page)
    LOG.info(f"    [DEBUG] 提交后 read_form_errors 返回 {len(errors or [])} 条: {errors}")
    if errors:
        field_errors = [e for e in errors if e.get("severity") == "field"]

        # 过滤掉因无选项被跳过的字段的验证错误
        if skipped_no_options:
            field_errors = [e for e in field_errors
                          if e.get("field_label") not in skipped_no_options]
            if not field_errors:
                LOG.info(f"    所有字段验证错误均来自无选项字段，已忽略")

        if field_errors:
            first_err = field_errors[0]
            # ★ O5: 记录表单快照，供 Stage 2 复现错误场景
            try:
                form_snapshot = await form_filler.scan_form_fields_v2()
                snapshot_data = [
                    {"label": f["label"], "value": f.get("value", ""), "kb_category": f.get("kb_category", "")}
                    for f in form_snapshot
                ]
            except Exception as snap_err:
                LOG.warning(f"    表单快照捕获失败: {snap_err}")
                snapshot_data = []
            # ★ Fix 3: 使用 _build_create_error_result 保留完整表单数据
            result = _build_create_error_result(
                "form_validation",
                first_err.get("error_text", ""),
                btn_text, btn_click_result,
                fill_data, fill_rules, fields, submit_result,
                multi_step_field_details, username,
                radio_field_details=radio_field_details,
                cascade_actions=cascade_actions
            )
            result["error_field"] = first_err.get("field_label", "")
            result["form_snapshot_on_error"] = snapshot_data
            return result
        global_errors = [e for e in errors if e.get("severity") == "global"]
        if global_errors:
            err_text = global_errors[0].get("error_text", "")
            # 过滤掉成功消息（"成功" 不应被视为错误）
            if "成功" in err_text:
                LOG.info(f"    检测到成功消息: {err_text}，忽略")
            else:
                # 业务失败但表单已提交，保留表单数据供 playbook 生成完整步骤
                return _build_create_error_result(
                    "api_error", err_text, btn_text, btn_click_result,
                    fill_data, fill_rules, fields, submit_result,
                    multi_step_field_details, username,
                    radio_field_details=radio_field_details,
                    cascade_actions=cascade_actions
                )

    # 10. 验证成功（弹窗关闭 / 成功提示）
    captured_msgs = await _get_captured_messages(page)
    LOG.info(f"    [DEBUG] captured_messages: {captured_msgs}")
    success, _detail = await _verify_operation_success(page, "create", captured_messages=captured_msgs,
                                                      url_before=url_before)
    LOG.info(f"    [DEBUG] _verify_operation_success: {success}, detail={_detail}")
    if not success:
        # 二次检测：提取明确的业务失败信息
        failure_info = await _extract_business_failure(page)
        LOG.info(f"    [DEBUG] _extract_business_failure: {failure_info}")
        if failure_info:
            # 业务失败但表单已提交，保留表单数据供 playbook 生成完整步骤
            return _build_create_error_result(
                "api_error", failure_info["error_text"], btn_text, btn_click_result,
                fill_data, fill_rules, fields, submit_result,
                multi_step_field_details, username,
                radio_field_details=radio_field_details,
                cascade_actions=cascade_actions
            )
        # ★ P2-A: 无明确失败关键词时，从本操作捕获的 info 级 toast 中提取
        # 前端校验拦截提示（如「请选择组织」——el-message--info 全局 toast，
        # 非 el-form-item 内联校验，read_form_errors 检不到、failKw 也不含此类文本，
        # 此前会落入 no_success_signal 丢失业务证据）。归为 form_validation，
        # 与用户管理「请选择部门」（read_form_errors 路径）同构，衔接 D 轮
        # expected_failure 契约（回放时按 error_text 匹配复现实证）。
        # 「请」开头 = 前端指令性校验提示的稳定特征；P0-A 修复后 captured_msgs
        # 已按操作隔离，无跨操作残留；排除 source=dialog（确认弹框正文非失败证据）。
        validation_hint = None
        for msg in reversed(captured_msgs or []):
            text = (msg.get("text") or "").strip()
            if (msg.get("type") == "info" and msg.get("source") != "dialog"
                    and text.startswith("请")):
                validation_hint = text
                break
        if validation_hint:
            LOG.info(f"    [P2-A] 捕获前端校验拦截提示: {validation_hint}")
            return _build_create_error_result(
                "form_validation", validation_hint, btn_text, btn_click_result,
                fill_data, fill_rules, fields, submit_result,
                multi_step_field_details, username,
                radio_field_details=radio_field_details,
                cascade_actions=cascade_actions
            )
        return _build_create_error_result(
            "no_success_signal", "提交后未检测到成功信号", btn_text, btn_click_result,
            fill_data, fill_rules, fields, submit_result,
            multi_step_field_details, username,
            radio_field_details=radio_field_details,
            cascade_actions=cascade_actions
        )

    # 10.5. API 触发检测（额外确认，从 Stage 2 移入）
    api_triggered = await _check_create_api_triggered_simple(page)
    if api_triggered:
        LOG.info(f"    API 触发确认: POST 请求已检测到")

    # 11. 记录创建的标识数据及其来源
    marker, marker_source = _determine_marker_with_source(fields, fill_data)
    if marker:
        LOG.info(f"    marker: {marker} (来源: {marker_source['type']}/{marker_source['field_label']})")
    else:
        LOG.info(f"    marker: None (无 input 类型字段)")

    # 确保回到列表页（统一使用 shared/transition_to_next）
    from ..shared.transition_helpers import transition_to_next
    await transition_to_next(page, tab_name=context.get("tab_name", ""), tab_locator=context.get("tab_locator", ""), wait_table=False)

    # 使用 submit_form_v2 返回的已验证 locator（Stage 1 探测成功的那个）
    submit_text = submit_result["text"]
    submit_text_normalized = " ".join(submit_text.split())
    submit_locator = submit_result["locator"]
    trigger_text_normalized = " ".join(btn_text.split())

    # 使用 click_page_button 返回的实际 locator（已验证）
    trigger_locator = btn_click_result.get("locator")
    if not trigger_locator:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
        trigger_locator = f"{trigger_tag}:has-text('{actual_text}')"

    dialog_detected = await page.evaluate("""() => {
        const dialog = document.querySelector('.el-dialog__wrapper:not([style*="display: none"]), .el-dialog:not([style*="display: none"])');
        return dialog && dialog.offsetWidth > 0;
    }""")

    # Dialog 模式下，为已验证的 locator 添加 dialog 范围限定（防止匹配到外部同名按钮）
    if dialog_detected and not submit_locator.startswith(".el-dialog"):
        submit_locator = f".el-dialog__footer {submit_locator}"

    # Phase 1.1: 收集 radio 字段详情
    radio_field_details = []
    # 从 fill_result 获取 radio 实际选择的值和 cascade 操作
    radio_selections = fill_result.get("radio_selections", {}) if isinstance(fill_result, dict) else {}
    cascade_actions = fill_result.get("cascade_actions", []) if isinstance(fill_result, dict) else []

    for field in fields:
        if field.get("kb_category") == "radio":
            field_label = field.get("label")
            actual_option_text = radio_selections.get(field_label, "")
            radio_field_details.append({
                "label": field_label,
                "kb_category": "radio",
                "option_locator": field.get("playwright_locator") or field.get("selector"),
                "option_text": actual_option_text,
                "fill_rule": field.get("fill_rule", {}),
            })
    # ★ R3: 合并 FormFiller 记录的 radio 详情（interaction_steps / all_options / 显隐字段 diff）
    _radio_details_from_fill = fill_result.get("radio_field_details", []) if isinstance(fill_result, dict) else []
    if _radio_details_from_fill:
        _radio_by_label = {rd.get("label"): rd for rd in _radio_details_from_fill}
        for rd_entry in radio_field_details:
            _src = _radio_by_label.get(rd_entry.get("label"))
            if _src:
                if _src.get("interaction_steps"):
                    rd_entry["interaction_steps"] = _src["interaction_steps"]
                if _src.get("all_options"):
                    rd_entry["all_options"] = _src["all_options"]
                # ★ 显隐输入项 diff（泛化能力）: 空列表 [] 也要保留（= 探测过但无变化）
                if _src.get("revealed_fields") is not None:
                    rd_entry["revealed_fields"] = _src["revealed_fields"]
                if _src.get("hidden_fields") is not None:
                    rd_entry["hidden_fields"] = _src["hidden_fields"]

    return {
        "success": True,
        "fill_data": fill_data,
        "fill_rules": fill_rules,
        "form_fields": fields,
        "marker": marker,
        "marker_source": marker_source,  # ★ 记录 marker 来源字段
        "selectors": {
            "trigger": btn_text,
            "submit": submit_text,
        },
        # Playbook 增强字段
        "trigger_locator": trigger_locator,
        "trigger_locator_verified": trigger_locator,  # Phase 1.1: 已验证的 locator
        "trigger_text": trigger_text_normalized,
        "trigger_xpath_index": btn_click_result.get("xpath_index", -1),
        "trigger_clean_text": btn_click_result.get("clean_text", ""),
        "submit_locator": submit_locator,
        "submit_locator_verified": submit_locator,  # Phase 1.1: 已验证的 locator
        "submit_text": submit_text_normalized,
        "submit_click_strategy": submit_result.get("click_strategy", "playwright"),
        "submit_xpath_index": submit_result.get("xpath_index", -1),
        "submit_clean_text": submit_result.get("clean_text", ""),
        "dialog_detected": bool(dialog_detected),
        "interaction_mode": "dialog" if dialog_detected else "page-nav",  # Phase 1.1
        "dialog_locator": ".el-dialog__wrapper",
        "success_detected": True,
        "success_locator": ".el-message--success",
        "multi_step_field_details": multi_step_field_details,  # Phase 1.1
        "radio_field_details": radio_field_details,  # Phase 1.1
        "cascade_actions": cascade_actions,  # ★ 记录 radio 触发的级联操作（如 transfer-box）
    }


async def _do_query_via_search_input(page, btn: dict, marker: str = None) -> dict:
    """执行查询操作：通过搜索输入框触发（无搜索按钮）。

    Args:
        page: Playwright Page 对象
        btn: 合成的按钮字典，含 search_input 信息
        marker: 搜索文本，使用实际创建的完整名称（如 AT_test_647215）
    """
    search_input_info = btn.get("search_input", {})
    locator = search_input_info.get("locator", "")
    placeholder = search_input_info.get("placeholder", "")

    if not locator:
        return no_locator("搜索输入框无有效 locator")

    # 使用 marker（完整名称）作为搜索文本，若无则回退到 "test"
    search_text = marker or "test"

    # 填写搜索框
    try:
        # ★ 多 tab 页面可能有多个同名搜索框（每个 tab 一个），优先选择可见的
        visible_el = page.locator(f"{locator}:visible").first
        if await visible_el.count() > 0:
            input_el = visible_el
            LOG.info(f"    [query] 找到可见搜索框")
        else:
            # fallback: 取第一个（可能不可见）
            input_el = page.locator(locator).first
            total_count = await page.locator(locator).count()
            if total_count == 0:
                return make_error("locator_failed", f"搜索输入框未找到: {locator}")
            LOG.info(f"    [query] 搜索框不可见 ({total_count} 个匹配，尝试等待)...")
            await input_el.wait_for(state='visible', timeout=5000)

        await input_el.click()
        await input_el.fill(search_text)
        await page.wait_for_timeout(500)
    except Exception as e:
        return make_error("fill_failed", f"填写搜索框失败: {e}")

    # 触发搜索：根据是否有相邻按钮决定触发方式
    has_adjacent_btn = search_input_info.get("has_adjacent_btn", False)
    trigger_mode = "auto"

    if has_adjacent_btn:
        # 尝试点击相邻按钮
        try:
            # 尝试多种定位方式
            btn_locators = [
                page.locator(f"{locator} ~ button").first,
                page.locator(f"{locator} + .el-button").first,
                page.locator(f'{locator}').locator('xpath=../following-sibling::button').first,
            ]
            clicked = False
            for btn_loc in btn_locators:
                if await btn_loc.count() > 0 and await btn_loc.is_visible():
                    await btn_loc.click()
                    clicked = True
                    trigger_mode = "button"
                    break
            if not clicked:
                # 回退到回车
                await page.keyboard.press("Enter")
                trigger_mode = "enter"
        except Exception:
            await page.keyboard.press("Enter")
            trigger_mode = "enter"
    else:
        # 无相邻按钮，使用回车触发
        await page.keyboard.press("Enter")
        trigger_mode = "enter"

    # 等待表格刷新
    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 验证列表仍存在
    has_table = await page.evaluate(
        "() => !!document.querySelector('.el-table__body-wrapper tbody tr')"
    )

    result = {
        "success": True,
        "selectors": {"trigger": f"搜索输入:{placeholder}"},
        "trigger_locator_verified": locator,
        "search_input": {
            "locator": locator,
            "placeholder": placeholder,
        },
        "trigger_mode": trigger_mode,
        "has_table": has_table,
    }
    LOG.info(f"  搜索输入框验证成功: trigger_mode={trigger_mode}, has_table={has_table}")
    return result


async def _do_query(page, context: dict) -> dict:
    """执行查询操作：输入搜索条件 → 点击搜索 → 验证列表刷新。"""
    btn = context["btn"]
    btn_text = btn.get("text", "")
    marker = context.get("marker")  # 使用完整名称作为搜索文本
    search_text = marker or "test"
    LOG.info(f"  [query] 使用搜索文本: '{search_text}' (marker={marker})")

    # 尝试在搜索框输入（结构特征探测，不依赖关键词）
    search_input_locator = None
    search_input_placeholder = None
    try:
        search_input = page.locator(
            '.el-input--prefix input, '
            '.el-input input[placeholder], '
            '.ant-input-affix-wrapper input'
        ).first
        if await search_input.count() > 0:
            # 排除弹窗内的
            in_dialog = await search_input.evaluate(
                "el => !!el.closest('.el-dialog, .el-drawer, .ant-modal')"
            )
            if not in_dialog:
                search_input_locator = await search_input.evaluate(
                    "el => el.getAttribute('placeholder') ? "
                    "'input[placeholder=\"' + el.getAttribute('placeholder') + '\"]' : ''"
                )
                search_input_placeholder = await search_input.evaluate(
                    "el => el.getAttribute('placeholder') || ''"
                )
                await search_input.fill(search_text)
                LOG.info(f"  [query] 已填充搜索框: '{search_text}'")
                await page.wait_for_timeout(500)
    except Exception as e:
        LOG.warning(f"  [query] 搜索框填充异常: {e}")
        import traceback
        LOG.debug(f"  [query] 异常堆栈:\n{traceback.format_exc()}")

    # 点击搜索按钮
    btn_click_result = await click_page_button(page, btn_text)
    if not btn_click_result["clicked"]:
        return click_failed(f"无法点击搜索按钮: {btn_text}")

    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 验证列表刷新（简单检查：表格仍存在）
    has_table = await page.evaluate(
        "() => !!document.querySelector('.el-table__body-wrapper tbody tr')"
    )

    trigger_text_normalized = " ".join(btn_text.split())
    # 使用 click_page_button 返回的已验证 locator（不重建）
    trigger_locator = btn_click_result.get("locator")
    if not trigger_locator:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
        trigger_locator = f"{trigger_tag}:has-text('{actual_text}')"

    result = {
        "success": True,
        "selectors": {"trigger": btn_text},
        "trigger_text": trigger_text_normalized,
        "trigger_locator_verified": trigger_locator,
        "has_table": has_table,
    }

    # 记录搜索输入框信息（供 playbook 生成使用）
    if search_input_locator:
        result["search_input"] = {
            "locator": search_input_locator,
            "placeholder": search_input_placeholder or "",
        }
        result["trigger_mode"] = "button"

    return result


async def _do_detail(page, context: dict) -> dict:
    """执行查看详情操作：点击详情 → 验证打开 → 关闭。"""
    btn = context["btn"]
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # 如果是行操作，先找到对应行
    row_selector = None
    url_before = page.url

    # Phase 3: 注册新标签页监听器
    new_tab_event, new_tab_ref, cleanup_tab_listener = _setup_new_tab_listener(page)

    if btn_location == "row_action" and marker:
        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            cleanup_tab_listener()
            return row_not_found(f"未找到数据行: {marker}")
        clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    else:
        btn_click_result = await click_page_button(page, btn_text)
        clicked = btn_click_result["clicked"]

    if not clicked:
        cleanup_tab_listener()
        return click_failed(f"无法点击详情按钮: {btn_text}")

    # Phase 3: 使用统一后点击检测
    obs = await _observe_post_click(
        page, url_before, timeout_ms=3000,
        new_page_event=new_tab_event, new_page_ref=new_tab_ref
    )
    cleanup_tab_listener()

    from core.discovery.ui_scanner.button_detector import _close_dialog

    selectors = {"trigger": btn_text}
    if row_selector:
        selectors["row_selector"] = row_selector

    # 分支处理不同场景
    if obs["scenario"] == "new_tab":
        # 新标签页打开详情
        new_page = obs["new_page"]
        LOG.info(f"    [detail] 新标签页打开: {new_page.url[:60]}")
        try:
            await new_page.close()
        except Exception:
            pass
        return {"success": True, "selectors": selectors, "interaction_mode": "new_tab"}

    elif obs["scenario"] == "page_nav":
        # 页面跳转模式
        LOG.info(f"    [detail] 页面跳转: {url_before[:40]} → {page.url[:40]}")
        await page.go_back()
        await wait_for_navigation_complete(page)
        # ★ 导航后恢复 Tab（tab_locator 为空时按文本回退，shared 统一实现）
        _tab_name = context.get("tab_name", "")
        if _tab_name:
            from core.discovery.shared.tab_helpers import restore_active_tab
            await restore_active_tab(page, _tab_name, context.get("tab_locator", ""))
        return {"success": True, "selectors": selectors, "interaction_mode": "page_nav"}

    elif obs["scenario"] == "dialog":
        # 弹窗模式
        LOG.info(f"    [detail] 弹窗打开: {obs['actual_state']}")
        await _close_dialog(page)
        return {"success": True, "selectors": selectors, "interaction_mode": "dialog"}

    else:
        # 未检测到详情打开
        return no_dialog("点击详情后未出现详情页/弹窗")


async def _do_edit(page, context: dict) -> dict:
    """执行编辑操作：点击编辑 → 修改字段 → 提交 → 验证。"""
    import logging
    _log = logging.getLogger("crud_executor")
    btn = context["btn"]
    marker = context.get("marker")
    form_filler = context["form_filler"]
    framework = context.get("framework", "element-ui")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")
    _tab_name = context.get("tab_name", "")
    _tab_locator = context.get("tab_locator", "")

    _log.debug(f"    [_do_edit] START: btn_text='{btn_text}', marker='{marker}', location='{btn_location}'")

    # 记录点击前的 URL
    url_before_click = page.url
    _log.info(f"    [EDIT-DEBUG] 点击前 URL: {url_before_click}")

    # Phase 3: 注册新标签页监听器
    new_tab_event, new_tab_ref, cleanup_tab_listener = _setup_new_tab_listener(page)

    # ★ 批五 F5-1：dropdown 编辑入口无 marker → 显式 skipped（对齐 generic
    #   dropdown 路径 no_marker_for_dropdown 语义；不再落入 else 全局点击空耗）
    if btn_location == "dropdown" and not marker:
        cleanup_tab_listener()
        _log.warning(f"    {btn_text} 跳过: dropdown 操作需要 marker")
        result = skipped("dropdown 操作需要 marker",
                        trigger_text=btn_text,
                        trigger_locator_verified=None)
        result["btn_location"] = "dropdown"
        result["skip_reason"] = "no_marker_for_dropdown"
        return result

    # 找到行并点击编辑（带重试）
    max_locate_retries = 2
    row_selector = None
    btn_click_result = {"clicked": False, "strategy": "", "tag": "button", "text": btn_text}
    for attempt in range(max_locate_retries):
        _log.info(f"    [EDIT-DEBUG] attempt={attempt + 1}/{max_locate_retries}, btn_location={btn_location}, marker={marker}")
        if btn_location == "row_action" and marker:
            from core.discovery.replay.button_driver import ButtonDriver
            driver = ButtonDriver(page)
            _log.info(f"    [EDIT-DEBUG] 开始定位数据行: marker='{marker}'")
            row = await driver.find_data_row(marker)
            if not row:
                _log.warning(f"    [EDIT-DEBUG] 行定位失败: marker='{marker}' 未找到")
                if attempt < max_locate_retries - 1:
                    LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_locate_retries})")
                    await page.wait_for_timeout(1000)
                    continue
                cleanup_tab_listener()
                return row_not_found(f"未找到数据行: {marker}",
                                    trigger_text=btn_text,
                                    trigger_locator_verified=None)
            _log.info(f"    [EDIT-DEBUG] 行定位成功，点击行内按钮: btn_text='{btn_text}'")
            clicked = await driver.click_row_button_v2(row, btn_text)
            _log.info(f"    [EDIT-DEBUG] click_row_button_v2 返回: clicked={clicked}")
            row_selector = btn_text
        elif btn_location == "dropdown" and marker:
            # ★ 批五 F5-1：dropdown 位置的编辑入口（EIS 修改Hostname）——
            #   此前落入 else 全局 click_page_button 必败（页面无该文本按钮）。
            #   走 marker 行「更多」菜单点击该项（click_row_more_item 内含
            #   Vue disabled 检测）；disabled 时直接 skipped 交状态机恢复
            #   （对齐 generic dropdown 路径 L4719 同款语义），不空耗重试。
            from core.discovery.replay.button_driver import ButtonDriver
            driver = ButtonDriver(page)
            _log.info(f"    [EDIT-DEBUG] dropdown 编辑入口: marker='{marker}', item='{btn_text}'")
            row = await driver.find_data_row(marker)
            if not row:
                if attempt < max_locate_retries - 1:
                    LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_locate_retries})")
                    await page.wait_for_timeout(1000)
                    continue
                cleanup_tab_listener()
                return row_not_found(f"未找到数据行: {marker}",
                                    trigger_text=btn_text,
                                    trigger_locator_verified=None)
            _edit_dd_result = await driver.click_row_more_item(row, btn_text)
            _log.info(f"    [EDIT-DEBUG] click_row_more_item 返回: {_edit_dd_result}")
            if _edit_dd_result.get("reason") == "button-disabled":
                cleanup_tab_listener()
                LOG.info(f"    跳过 {btn_text} 操作: 按钮被禁用 (cmss-disabled/is-disabled)")
                return skipped(f"按钮被禁用: {btn_text}",
                              trigger_text=btn_text,
                              trigger_locator_verified=None)
            clicked = _edit_dd_result.get("clicked", False)
            row_selector = f"{btn.get('parent', '更多')} > {btn_text}"
        else:
            _log.info(f"    [EDIT-DEBUG] 使用 toolbar 按钮点击方式")
            btn_click_result = await click_page_button(page, btn_text)
            clicked = btn_click_result["clicked"]

        if clicked:
            break
        elif attempt < max_locate_retries - 1:
            LOG.debug(f"    点击失败，重试 ({attempt + 1}/{max_locate_retries})")
            await page.wait_for_timeout(500)
    else:
        cleanup_tab_listener()
        return click_failed(f"无法点击编辑按钮: {btn_text}",
                           trigger_text=btn_text,
                           trigger_locator_verified=None)

    # Phase 3: 使用统一后点击检测
    _log.info(f"    [EDIT-DEBUG] 开始 _observe_post_click (timeout_ms=3000)")
    _log.info(f"    [EDIT-DEBUG] 点击前 URL: {url_before_click}")
    obs = await _observe_post_click(
        page, url_before_click, timeout_ms=3000,
        new_page_event=new_tab_event, new_page_ref=new_tab_ref
    )
    cleanup_tab_listener()

    url_after_click = page.url
    _log.info(f"    [EDIT-DEBUG] 点击后 URL: {url_after_click}")
    _log.info(f"    [EDIT-DEBUG] observe scenario: {obs['scenario']}")
    _log.info(f"    [EDIT-DEBUG] URL 变化: {url_before_click != url_after_click}")
    if url_before_click != url_after_click:
        _log.info(f"    [EDIT-DEBUG] URL diff:")
        _log.info(f"      before: {url_before_click}")
        _log.info(f"      after:  {url_after_click}")

    # [DEBUG] 检查是否有 order-submit 区域（底部固定操作栏）
    order_submit_info = await page.evaluate("""() => {
        // 查找 order-submit 或类似的操作栏
        const selectors = [
            '.order-submit',
            '.order-submit.clearfix',
            '.main-wrapper .order-submit',
            '[class*="order-submit"]',
            '[class*="submit-bar"]',
            '[class*="action-bar"]',
            '.fixed-bottom',
            '.sticky-bottom'
        ];

        let found = null;
        for (const selector of selectors) {
            const el = document.querySelector(selector);
            if (el) {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                found = {
                    selector: selector,
                    tagName: el.tagName,
                    className: el.className,
                    rect: {
                        top: rect.top,
                        bottom: rect.bottom,
                        left: rect.left,
                        right: rect.right,
                        width: rect.width,
                        height: rect.height
                    },
                    style: {
                        position: style.position,
                        display: style.display,
                        visibility: style.visibility,
                        opacity: style.opacity,
                        zIndex: style.zIndex
                    },
                    innerHTML: el.innerHTML.substring(0, 200)
                };
                break;
            }
        }

        // 检查视口高度
        const viewportHeight = window.innerHeight;

        // 查找所有 fixed/sticky 定位的元素
        const fixedElements = [];
        document.querySelectorAll('*').forEach(el => {
            const style = window.getComputedStyle(el);
            if (style.position === 'fixed' || style.position === 'sticky') {
                const rect = el.getBoundingClientRect();
                if (rect.height > 0 && rect.width > 0) {
                    fixedElements.push({
                        tagName: el.tagName,
                        className: el.className.substring(0, 100),
                        position: style.position,
                        rect: { top: rect.top, bottom: rect.bottom, height: rect.height }
                    });
                }
            }
        });

        return {
            found: found,
            viewportHeight: viewportHeight,
            fixedElementsCount: fixedElements.length,
            fixedElements: fixedElements.slice(0, 5)  // 只返回前5个
        };
    }""")

    _log.info(f"    [EDIT-DEBUG] order-submit 区域: {order_submit_info.get('found') is not None}")
    if order_submit_info.get('found'):
        _log.info(f"    [EDIT-DEBUG]   selector: {order_submit_info['found']['selector']}")
        _log.info(f"    [EDIT-DEBUG]   className: {order_submit_info['found']['className']}")
        _log.info(f"    [EDIT-DEBUG]   rect: {order_submit_info['found']['rect']}")
        _log.info(f"    [EDIT-DEBUG]   style: {order_submit_info['found']['style']}")
    _log.info(f"    [EDIT-DEBUG] 视口高度: {order_submit_info.get('viewportHeight')}")
    _log.info(f"    [EDIT-DEBUG] fixed/sticky 元素数量: {order_submit_info.get('fixedElementsCount')}")
    if order_submit_info.get('fixedElements'):
        for i, fe in enumerate(order_submit_info['fixedElements']):
            _log.info(f"    [EDIT-DEBUG]   fixed[{i}]: {fe}")

    from core.discovery.ui_scanner.button_detector import _close_dialog

    # Phase 3: 分支处理不同场景
    if obs["scenario"] == "new_tab":
        # 新标签页编辑模式
        new_page = obs["new_page"]
        _log.info(f"    [EDIT-DEBUG] 新标签页打开: {new_page.url[:60]}")
        from core.discovery.operation_executor.navigation_executor import (
            _explore_and_operate_in_new_page,
        )
        nav_info = await _explore_and_operate_in_new_page(
            page=new_page, action="edit", new_url=new_page.url, depth=0, max_depth=3
        )
        try:
            await new_page.close()
        except Exception:
            pass

        nav_submit = nav_info.get("submit_result", {})
        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector

        if nav_submit.get("success"):
            _log.info(f"    [EDIT-DEBUG] 新标签页编辑成功")
            return make_success(
                fill_data=nav_info.get("fill_data", {}),
                selectors=selectors,
                nav_info=nav_info,
                trigger_text=btn_text,
            )
        else:
            error_text = nav_info.get("error_text", "") or "新标签页编辑未检测到成功信号"
            _log.warning(f"    [EDIT-DEBUG] 新标签页编辑失败: {error_text}")
            return make_error("new_tab_edit_failed", error_text,
                             trigger_text=btn_text,
                             trigger_locator_verified=btn_click_result.get("locator"),
                             trigger_xpath_index=btn_click_result.get("xpath_index", -1),
                             trigger_clean_text=btn_click_result.get("clean_text", ""),
                             nav_info=nav_info)

    elif obs["scenario"] == "page_nav":
        # ── 页面跳转编辑模式 ──
        _log.info(f"    [EDIT-DEBUG] 检测到页面跳转，走跨页面编辑流程")
        from core.discovery.operation_executor.navigation_executor import (
            _explore_and_operate_in_new_page, _navigate_back_to_list,
        )
        nav_info = await _explore_and_operate_in_new_page(
            page=page, action="edit", new_url=url_after_click, depth=0, max_depth=3
        )
        await _navigate_back_to_list(page, url_before_click, tab_name=_tab_name, tab_locator=_tab_locator)

        nav_submit = nav_info.get("submit_result", {})
        if nav_submit.get("success"):
            _log.info(f"    [EDIT-DEBUG] 跨页面编辑成功")
            selectors = {"trigger": btn_text}
            if row_selector:
                selectors["row_selector"] = row_selector
            return make_success(
                fill_data=nav_info.get("fill_data", {}),
                selectors=selectors,
                nav_info=nav_info,
                trigger_text=btn_text,
            )
        else:
            error_text = nav_info.get("error_text", "") or "跨页面编辑未检测到成功信号"
            _log.warning(f"    [EDIT-DEBUG] 跨页面编辑失败: {error_text}")
            return make_error("cross_page_edit_failed", error_text,
                              trigger_text=btn_text,
                              trigger_locator_verified=btn_click_result.get("locator"),
                              nav_info=nav_info)

    elif obs["scenario"] != "dialog":
        return no_dialog("点击编辑后未出现编辑弹窗",
                        trigger_text=btn_text,
                        trigger_locator_verified=btn_click_result.get("locator"),
                        trigger_xpath_index=btn_click_result.get("xpath_index", -1),
                        trigger_clean_text=btn_click_result.get("clean_text", ""))

    # ── 弹窗编辑模式 (obs["scenario"] == "dialog") ──
    # 扫描字段并修改
    try:
        fields = await form_filler.scan_form_fields_v2()
        modifications = context.get("edit_overrides")
        filled = await form_filler.fill_edit_form(fields, modifications)
        if filled == 0:
            await _close_dialog(page)
            return {
                "success": False,
                "error_type": "no_editable_fields",
                "error_text": "未找到可修改的字段",
                "trigger_text": btn_text,
                "trigger_locator_verified": btn_click_result.get("locator"),
                "form_fields": fields,
                "filled_count": filled,
                "interaction_mode": "dialog",
                "dialog_locator": ".el-dialog__wrapper",
            }

        # 提交
        submit_result = await form_filler.submit_form_v2()
        # ★ 批五 F3：守卫终态优先（按钮 disabled / 弹窗滞留 = 提交未生效）
        if submit_result.get("submit_blocked"):
            await _close_dialog(page)
            return {
                "success": False,
                "error_type": submit_result["submit_blocked"],
                "error_text": submit_result.get("blocked_reason") or "提交被守卫拦截（未生效）",
                "trigger_text": btn_text,
                "trigger_locator_verified": btn_click_result.get("locator"),
                "form_fields": fields,
                "filled_count": filled,
                "dialog_detected": dialog_detected,
                "interaction_mode": "dialog",
                "dialog_locator": ".el-dialog__wrapper",
            }
        if not submit_result.get("locator"):
            await _close_dialog(page)
            return {
                "success": False,
                "error_type": "submit_failed",
                "error_text": "未找到提交按钮",
                "trigger_text": btn_text,
                "trigger_locator_verified": btn_click_result.get("locator"),
                "form_fields": fields,
                "filled_count": filled,
                "dialog_detected": dialog_detected,
                "interaction_mode": "dialog",
                "dialog_locator": ".el-dialog__wrapper",
            }

        await page.wait_for_timeout(2000)

        # 检查错误
        errors = await read_form_errors(page)
        if errors:
            field_errors = [e for e in errors if e.get("severity") == "field"]
            if field_errors:
                # ★ O5: 记录表单快照，供 Stage 2 复现错误场景
                try:
                    form_snapshot = await form_filler.scan_form_fields_v2()
                    snapshot_data = [
                        {"label": f["label"], "value": f.get("value", ""), "kb_category": f.get("kb_category", "")}
                        for f in form_snapshot
                    ]
                except Exception as snap_err:
                    _log.warning(f"    表单快照捕获失败: {snap_err}")
                    snapshot_data = []
                await _close_dialog(page)  # ★ 关闭对话框，避免阻塞重试时的按钮点击
                result = form_validation(field_errors[0].get("error_text", ""),
                                      error_field=field_errors[0].get("field_label", ""),
                                      trigger_text=btn_text,
                                      trigger_locator_verified=btn_click_result.get("locator"))
                result["form_snapshot_on_error"] = snapshot_data
                result["form_fields"] = fields
                result["filled_count"] = filled
                result["submit_result"] = submit_result
                result["interaction_mode"] = "dialog"
                result["dialog_locator"] = ".el-dialog__wrapper"
                return result

        captured_msgs = await _get_captured_messages(page)
        success, _detail = await _verify_operation_success(page, "update", captured_messages=captured_msgs,
                                                          url_before=url_before_click)
        if not success:
            await _close_dialog(page)
            return {
                "success": False,
                "error_type": "no_success_signal",
                "error_text": "编辑提交后未检测到成功信号",
                "trigger_text": btn_text,
                "trigger_locator_verified": btn_click_result.get("locator"),
                "form_fields": fields,
                "filled_count": filled,
                "submit_result": submit_result,
                "interaction_mode": "dialog",
                "dialog_locator": ".el-dialog__wrapper",
            }
    except Exception as e:
        # ★ 异常时先关闭对话框，再传播错误，避免对话框遮挡重试时的按钮点击
        _log.warning(f"    [_do_edit] 弹窗编辑异常: {e}，关闭对话框")
        await _close_dialog(page)
        raise

    # 使用 submit_form_v2 返回的已验证 locator
    submit_text = submit_result["text"]
    submit_text_normalized = " ".join(submit_text.split())
    submit_locator = submit_result["locator"]

    # 构建 selectors
    selectors = {"trigger": btn_text, "submit": submit_text}
    if row_selector:
        selectors["row_selector"] = row_selector

    # Phase 1: 增强字段（与 _do_create 对齐，供 build_playbook 使用）
    trigger_text_normalized = " ".join(btn_text.split())

    import json as _json
    _js_code = """() => {
        const dialog = document.querySelector('.el-dialog__wrapper:not([style*="display: none"]), .el-dialog:not([style*="display: none"])');
        return dialog && dialog.offsetWidth > 0;
    }"""
    LOG.debug(f"    [_do_edit] evaluate JS: {_js_code[:200]}...")
    try:
        dialog_detected = await page.evaluate(_js_code)
    except Exception as e:
        LOG.error(f"    [_do_edit] evaluate FAILED: {e}")
        dialog_detected = False

    # Dialog 模式下，为已验证的 locator 添加 dialog 范围限定
    if dialog_detected and not submit_locator.startswith(".el-dialog"):
        submit_locator = f".el-dialog__footer {submit_locator}"

    # 使用 click_page_button 返回的实际 locator（已验证）
    trigger_locator = btn_click_result.get("locator")
    if not trigger_locator:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
        trigger_locator = f"{trigger_tag}:has-text('{actual_text}')"

    # Phase 1: 记录是否为行级操作（编辑按钮在行内，Stage 2 需先定位行再点击）
    is_row_action = btn_location == "row_action" and row_selector is not None

    # 收集 multi_step 和 radio 字段详情
    # 注意：fill_edit_form 跳过了 multi_step 类型字段（不做修改），
    # 因此 multi_step_field_details 必须为空，不传递给 playbook
    from core.discovery import const as _const
    multi_step_field_details = []
    radio_field_details = []
    # 注意：fill_edit_form 返回 int，不包含 radio_selections
    # 编辑操作通常不需要重新选择 radio，保持 option_text 为空
    for field in fields:
        kb_cat = field.get("kb_category", "")
        # edit 操作不修改 multi_step 字段，不收集（避免 playbook 包含 Stage 1 未执行的步骤）
        if kb_cat == "radio":
            radio_field_details.append({
                "label": field.get("label"),
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": "",  # 编辑操作保持为空
                "fill_rule": field.get("fill_rule", {}),
            })

    return {
        "success": True,
        "edit_fill_data": {"modified_fields": filled},
        "selectors": selectors,
        # Phase 1: 增强字段
        "is_row_action": is_row_action,
        "requires_marker": True,  # 编辑操作需要先定位数据行
        "button_text": btn_text,
        "trigger_text": trigger_text_normalized,
        "form_fields": fields,
        "fill_rules": generate_fill_rules(fields),
        "trigger_locator_verified": trigger_locator,
        "trigger_xpath_index": btn_click_result.get("xpath_index", -1),
        "trigger_clean_text": btn_click_result.get("clean_text", ""),
        "submit_locator_verified": submit_locator,
        "submit_text": submit_text_normalized,
        "submit_xpath_index": submit_result.get("xpath_index", -1),
        "submit_clean_text": submit_result.get("clean_text", ""),
        "interaction_mode": "dialog" if dialog_detected else "page-nav",
        "dialog_locator": ".el-dialog__wrapper",
        "success_locator": ".el-message--success",
        "multi_step_field_details": multi_step_field_details,
        "radio_field_details": radio_field_details,
        "cascade_actions": [],  # 编辑操作不收集 cascade（fill_edit_form 返回 int）
    }


async def _do_toggle_switch(page, context: dict) -> dict:
    """执行 switch 切换操作（★ P0-1 执行体收敛为单源：shared/switch_ops.toggle_switch）。

    Stage 1 编排层职责：
    - 组装 switch_info（text/locator）
    - 调用共享 toggle_switch（状态检测/JS 点击/确认弹框/状态轮询全部单源）
    - 包装 Stage 1 结果结构（make_error 错误类型映射、playbook 结果构造）

    旧实现的内联 JS（_get_switch_state / 点击 JS / 确认弹框处理 / 状态轮询）
    与 shared/switch_ops 逐行雷同（审计 C-1 双实现），已删除；
    内联版独有诊断能力（点击后即时状态、弹框详尽诊断、switch 关键词校验、
    失败错误消息查询）已并入 switch_ops 单源。

    Args:
        page: Playwright Page 对象
        context: 包含 switch_info 字典

    Returns:
        dict: 操作结果，包含 success, before_state, after_state, trigger_text 等
    """
    switch_info = context["switch_info"]
    sw_text = switch_info.get("text", "Switch")
    # 空 locator 回退全局 .el-switch（switch_ops 按 label 文本锚点精确匹配）
    sw_locator = switch_info.get("locator", "") or ".el-switch"

    from core.discovery.shared.switch_ops import toggle_switch

    try:
        success, detail = await toggle_switch(page, locator=sw_locator, text=sw_text)

        if not success:
            error = detail.get("error", "")
            # make_error 编排：按失败形态映射 Stage 1 错误类型
            # （与旧内联版错误类型口径一致：点击类失败 / 状态未变化）
            if "无法点击" in error or "未找到" in error:
                return make_error("switch_click_failed",
                                  error or f"无法点击 toolbar switch: {sw_text}",
                                  trigger_text=sw_text)
            return make_error("switch_state_unchanged",
                              error or f"switch 状态未变化: {sw_text}",
                              trigger_text=sw_text)

        before_state_str = detail.get("before_state", "UNKNOWN")
        after_state_str = detail.get("after_state", "UNKNOWN")
        success_msg = detail.get("success_message")

        LOG.info(f"    [switch] 切换成功: {before_state_str} → {after_state_str}"
                 + (f" (消息: {success_msg})" if success_msg else ""))

        return {
            "success": True,
            "before_state": before_state_str,
            "after_state": after_state_str,
            "trigger_text": sw_text,
            "selectors": {"trigger": sw_locator},
            "trigger_locator_verified": sw_locator,
            "confirm_handled": detail.get("confirm_handled", False),
            "success_message": success_msg,
            "is_switch": True,
            "fill_data": {},
        }

    except Exception as e:
        LOG.error(f"    [switch] 异常: {e}")
        return exception(f"switch 操作异常: {e}", trigger_text=sw_text)


async def _do_delete(page, context: dict) -> dict:
    """执行删除操作：点击删除 → 确认 → 验证行消失。

    支持两种场景：
    - row_action: 行内"删除"按钮，直接点击该行
    - toolbar: 工具栏"批量删除"按钮，需要先勾选 checkbox
    """
    btn = context["btn"]
    LOG.info(f"  [ROUTE] → _do_delete 被调用, btn_text='{btn.get('text', '')}', location='{btn.get('location', '')}'")
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")

    # ★ 批五 F6：记录点击前 URL（无消息信号时 URL 跳转判成功的接线输入）
    url_before = page.url

    # toolbar 批量删除：先勾选 checkbox
    if btn_location == "toolbar":
        selected = await _ensure_row_selected(page, marker)
        if selected:
            LOG.debug(f"    已勾选表格行供批量删除使用")
        await page.wait_for_timeout(500)

    # 安装消息捕获（在点击按钮前，防止瞬态 toast 消失）
    await _install_message_capture(page)

    # 找到行并点击删除（带重试）
    max_locate_retries = 2
    row_selector = None
    btn_click_result = {"clicked": False, "strategy": "", "tag": "button", "text": btn_text}
    for attempt in range(max_locate_retries):
        if btn_location == "row_action" and marker:
            from core.discovery.replay.button_driver import ButtonDriver
            driver = ButtonDriver(page)
            row = await driver.find_data_row(marker)
            if not row:
                if attempt < max_locate_retries - 1:
                    LOG.debug(f"    行定位失败，等待后重试 ({attempt + 1}/{max_locate_retries})")
                    await page.wait_for_timeout(1000)
                    continue
                return row_not_found(f"未找到数据行: {marker}",
                                    trigger_text=btn_text,
                                    trigger_locator_verified=None)
            clicked = await driver.click_row_button_v2(row, btn_text)
            row_selector = btn_text
        else:
            btn_click_result = await click_page_button(page, btn_text)
            clicked = btn_click_result["clicked"]

        if clicked:
            break
        elif attempt < max_locate_retries - 1:
            LOG.debug(f"    点击失败，重试 ({attempt + 1}/{max_locate_retries})")
            await page.wait_for_timeout(500)
    else:
        return click_failed(f"无法点击删除按钮: {btn_text}",
                           trigger_text=btn_text,
                           trigger_locator_verified=None)

    await page.wait_for_timeout(1000)

    # === DIAGNOSTIC: 点击删除按钮后的 DOM 状态 ===
    diag_before = await page.evaluate("""() => {
        const result = {
            messageBox: null,
            popconfirm: null,
            dialogs: [],
            allBtns: []
        };

        // 检查 message-box
        const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
        if (msgBox && msgBox.offsetWidth > 0) {
            const btns = msgBox.querySelectorAll('button');
            result.messageBox = {
                visible: true,
                title: (msgBox.querySelector('.el-message-box__title') || {}).textContent || '',
                message: (msgBox.querySelector('.el-message-box__message') || {}).textContent || '',
                btnCount: btns.length,
                btns: Array.from(btns).map(b => ({
                    text: b.textContent.trim(),
                    cls: b.className,
                    disabled: b.disabled,
                    visible: b.offsetWidth > 0
                }))
            };
        }

        // 检查 popconfirm
        const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
        if (popconfirm && popconfirm.offsetWidth > 0) {
            const btns = popconfirm.querySelectorAll('button');
            result.popconfirm = {
                visible: true,
                btnCount: btns.length,
                btns: Array.from(btns).map(b => ({
                    text: b.textContent.trim(),
                    cls: b.className,
                    disabled: b.disabled
                }))
            };
        }

        // 检查其他弹窗
        document.querySelectorAll('.el-dialog__wrapper, .el-drawer').forEach(d => {
            const r = d.getBoundingClientRect();
            if (r.width > 0 && r.height > 0 && d.style.display !== 'none') {
                const btns = d.querySelectorAll('button');
                result.dialogs.push({
                    cls: d.className.substring(0, 60),
                    title: (d.querySelector('.el-dialog__title, .el-drawer__header') || {}).textContent || '',
                    btnCount: btns.length,
                    btns: Array.from(btns).slice(0, 5).map(b => b.textContent.trim())
                });
            }
        });

        // 所有可见按钮（用于对比）
        document.querySelectorAll('button').forEach((btn, i) => {
            const r = btn.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;
            const text = (btn.textContent || '').trim();
            if (['确定', '确认', '取消', '是', '否', 'OK', 'Cancel', 'Yes', 'No'].some(t => text.includes(t))) {
                result.allBtns.push({
                    idx: i, text: text.substring(0, 20),
                    cls: (btn.className || '').substring(0, 60),
                    w: Math.round(r.width), h: Math.round(r.height),
                    disabled: btn.disabled
                });
            }
        });

        return result;
    }""")
    import json as _json3
    LOG.info(f"    [DIAG-delete-before] messageBox: {_json3.dumps(diag_before.get('messageBox'), ensure_ascii=False)}")
    LOG.info(f"    [DIAG-delete-before] popconfirm: {_json3.dumps(diag_before.get('popconfirm'), ensure_ascii=False)}")
    _dlg_del = [{"title": d.get("title","")[:20], "btns": d.get("btns",[])[:3]} for d in diag_before.get("dialogs", [])]
    LOG.info(f"    [DIAG-delete-before] dialogs({len(diag_before.get('dialogs', []))}): {_json3.dumps(_dlg_del, ensure_ascii=False)}")
    _btns_del = [{"text": b.get("text",""), "cls": b.get("cls","")[:30]} for b in diag_before.get("allBtns", [])]
    LOG.info(f"    [DIAG-delete-before] allBtns({len(diag_before.get('allBtns', []))}): {_json3.dumps(_btns_del, ensure_ascii=False)}")
    # === END DIAGNOSTIC ===

    # 确认删除弹窗（支持 el-message-box 和 el-popconfirm）
    from core.discovery.replay.button_driver import confirm_dialog
    # ★ O6: 记录确认对话框文本
    dialog_text = await page.evaluate("""() => {
        const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
        if (msgBox) {
            const msg = msgBox.querySelector('.el-message-box__message');
            return msg ? msg.textContent.trim() : '';
        }
        const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
        if (popconfirm) {
            const msg = popconfirm.querySelector('.el-popconfirm__main');
            return msg ? msg.textContent.trim() : '';
        }
        return '';
    }""")
    confirmed = await confirm_dialog(page)
    LOG.info(f"    confirm_dialog 返回: '{confirmed}'")

    # === DIAGNOSTIC: confirm_dialog 调用后的 DOM 状态 ===
    diag_after = await page.evaluate("""() => {
        const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
        const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
        return {
            messageBoxStillVisible: msgBox && msgBox.offsetWidth > 0,
            popconfirmStillVisible: popconfirm && popconfirm.offsetWidth > 0,
            messageBoxDisplay: msgBox ? msgBox.style.display : 'not-found',
            popconfirmDisplay: popconfirm ? popconfirm.style.display : 'not-found'
        };
    }""")
    LOG.info(f"    [DIAG-delete-after] messageBoxStillVisible: {diag_after.get('messageBoxStillVisible')}, "
             f"popconfirmStillVisible: {diag_after.get('popconfirmStillVisible')}")
    # === END DIAGNOSTIC ===

    if not confirmed:
        return make_error("no_confirm_button", "未找到删除确认按钮",
                         trigger_text=btn_text,
                         trigger_locator_verified=btn_click_result.get("locator"))

    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 直接验证行是否消失（最可靠的删除成功信号）
    row_disappeared = False
    if marker:
        await wait_for_table_ready(page, timeout=5000)
        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        remaining_row = await driver.find_data_row(marker)
        row_disappeared = remaining_row is None
        if row_disappeared:
            LOG.debug(f"    ✓ 删除验证通过: 数据行已消失 ({marker})")
        else:
            LOG.warning(f"    ⚠️ 删除后数据行仍存在: {marker}")

    # 检查确认后的页面状态（是否仍有残留对话框）
    post_state = await page.evaluate("""() => {
        const dialogs = document.querySelectorAll(
            '.el-dialog__wrapper:not([style*="display: none"]), ' +
            '.el-drawer:not([style*="display: none"]), ' +
            '.ant-modal-wrap:not([style*="display: none"])');
        const hasOpenDialog = Array.from(dialogs).some(d => d.offsetWidth > 0);
        const hasForm = document.querySelector(
            '.el-dialog__wrapper:not([style*="display: none"]) form, ' +
            '.el-dialog__wrapper:not([style*="display: none"]) .el-form') !== null;
        return { dialog_remains: hasOpenDialog, has_form_in_dialog: hasForm };
    }""")

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        global_errors = [e for e in errors if e.get("severity") == "global"]
        if global_errors:
            err_text = global_errors[0].get("error_text", "")
            # 过滤掉成功消息（"成功" 不应被视为错误）
            if "成功" in err_text:
                LOG.info(f"    检测到成功消息: {err_text}，忽略")
            else:
                return api_error(err_text,
                                trigger_text=btn_text,
                                trigger_locator_verified=btn_click_result.get("locator"))

    # 读取捕获的消息
    captured_msgs = await _get_captured_messages(page)

    # 验证成功（成功提示或行消失）
    success, _detail = await _verify_operation_success(page, "delete", captured_messages=captured_msgs,
                                                      url_before=url_before)

    # 行消失是最可靠的的成功信号，覆盖 toast 检测
    if not success and row_disappeared:
        success = True
        LOG.info(f"    删除成功（通过行消失验证）")

    # 构建 selectors
    selectors = {"trigger": btn_text, "confirm": confirmed}
    if row_selector:
        selectors["row_selector"] = row_selector

    # Phase 1: 增强字段
    trigger_text_normalized = " ".join(btn_text.split())
    # 使用 click_page_button 返回的实际 locator（已验证）
    trigger_locator_verified = btn_click_result.get("locator")
    if not trigger_locator_verified:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
        trigger_locator_verified = f"{trigger_tag}:has-text('{actual_text}')"

    # 如果是 toolbar 删除，标记需要先勾选 checkbox
    needs_checkbox = btn_location == "toolbar"
    checkbox_locator = ".el-checkbox__input" if needs_checkbox else None

    if success:
        result = {
            "success": True,
            "selectors": selectors,
            "confirmed": confirmed,
            "is_delete": True,
            "is_row_action": btn_location == "row_action",
            "btn_location": btn_location,
            "requires_marker": True,
            "marker": marker,
            "trigger_text": trigger_text_normalized,
            "trigger_locator_verified": trigger_locator_verified,
            "success_locator": ".el-message--success",
            "needs_checkbox": needs_checkbox,
            "checkbox_locator": checkbox_locator,
            "post_confirm_state": {
                "dialog_remains": post_state["dialog_remains"],
                "has_form_in_dialog": post_state["has_form_in_dialog"],
            },
            "confirm_dialog": {
                "confirmed": confirmed,
                "button_text": confirmed,
                "dialog_text": dialog_text,
            },
        }
    else:
        # ★ 失败时也保留所有交互数据，确保 playbook 可以生成回放步骤
        result = {
            "success": False,
            "error_type": "no_success_signal",
            "error_text": "删除后未检测到成功信号",
            "selectors": selectors,
            "confirmed": confirmed,
            "is_delete": True,
            "is_row_action": btn_location == "row_action",
            "btn_location": btn_location,
            "requires_marker": True,
            "marker": marker,
            "trigger_text": trigger_text_normalized,
            "trigger_locator_verified": trigger_locator_verified,
            "success_locator": ".el-message--success",
            "needs_checkbox": needs_checkbox,
            "checkbox_locator": checkbox_locator,
            "post_confirm_state": {
                "dialog_remains": post_state["dialog_remains"],
                "has_form_in_dialog": post_state["has_form_in_dialog"],
            },
            "confirm_dialog": {
                "confirmed": confirmed,
                "button_text": confirmed,
                "dialog_text": dialog_text,
            },
        }

    return result


async def _do_navigation_operation(page, context: dict) -> dict:
    """navigation 角色专用验证：点击 → URL 变化或新标签页 = 成功。

    语义依据（方案批三 3.1）：navigation 类操作（名称链接等非 BUTTON/
    DROPDOWN_ITEM 元素，_infer_action_role 纯结构性判定）的业务语义就是
    「跳转」，验证目标是导航本身发生，而非目标页面的表单流程。此前该角色
    落入 generic 分支 → _explore_and_operate_in_new_page 把目标页伪表单
    字段当业务表单填写提交（第 6 轮实证：9 个名称链接操作空转 38 分钟）。

    流程：点击（复用行级/toolbar 点击降级链）→ _observe_post_click →
    scenario 为 new_tab/page_nav 即成功并恢复现场（新标签页关闭/同 tab
    跳转回列表）；其余场景按实际反馈记录失败。不进入错误驱动重试、
    不等待弹窗、不探索目标页面表单。
    """
    from core.discovery.replay.button_driver import ButtonDriver
    from core.discovery.shared.button_clicker import (
        try_click_unique_global_button, is_destroy_text,
    )
    from core.discovery.operation_executor.navigation_executor import (
        _navigate_back_to_list,
    )
    from core.discovery.ui_scanner.button_detector import _close_dialog

    btn = context["btn"]
    action = context["action"]
    marker = context.get("marker")
    tab_name = context.get("tab_name", "")
    tab_locator = context.get("tab_locator", "")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")
    trigger_text = " ".join(btn_text.split())

    url_before = page.url
    LOG.info(f"  [NAVIGATION] 验证跳转操作: '{btn_text}' (location={btn_location})")

    # 注册新标签页监听（与 generic 同构：URL 变化 / 新 tab 双通道检测）
    new_tab_event, new_tab_ref, cleanup_tab_listener = _setup_new_tab_listener(page)

    # ── 点击（复用 generic 的行级/toolbar 点击降级链） ──
    btn_click_result = {}
    clicked = False
    _row_link = bool(btn.get("row_link"))
    _row_link_class = btn.get("row_link_class", "") or ""
    if btn_location == "row_action" and _row_link:
        # ★ 批五 F4-2：归并后的名称链接语义操作——文本是语义名（页面上不
        #   存在），不能全局/行内按文本点击；按 row_link_class 点 marker 行
        #   名称单元格链接（三阶段一致：marker 行 + class 定位）。名称链接
        #   的行归属即 marker 行本身，不做 first_row 降级。
        if not marker:
            cleanup_tab_listener()
            LOG.warning(f"  [NAVIGATION] row_link 操作 '{btn_text}' 跳过: 需要 marker")
            return {
                "success": False,
                "error_type": "skipped",
                "error_text": "名称链接操作需要 marker",
                "trigger_text": trigger_text,
                "btn_location": btn_location,
            }
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            cleanup_tab_listener()
            return {
                "success": False,
                "error_type": "row_not_found",
                "error_text": f"未找到数据行: {marker}",
                "trigger_text": trigger_text,
                "btn_location": btn_location,
            }
        clicked = await driver.click_row_name_link(row, _row_link_class)
        if clicked:
            _rl_tokens = [t for t in _row_link_class.split() if t]
            btn_click_result = {
                "clicked": True,
                "tag": btn.get("tag", "span"),
                "text": "row_name_link",
                "locator": ("." + ".".join(_rl_tokens)) if _rl_tokens else "",
            }
    elif btn_location == "row_action":
        # 名称链接类：文本全局唯一直接点击 → marker 行内定位 → first_row 兜底
        # （非破坏性降级语义与 generic/Stage 2 _step_find_row 对齐）
        _direct = await try_click_unique_global_button(page, btn_text)
        if _direct.get("clicked"):
            clicked = True
            btn_click_result = _direct
        else:
            driver = ButtonDriver(page)
            row = await driver.find_data_row(marker) if marker else None
            if not row and not is_destroy_text(btn_text):
                row = await driver.find_first_data_row()
            if not row:
                cleanup_tab_listener()
                return {
                    "success": False,
                    "error_type": "row_not_found",
                    "error_text": f"未找到数据行: {marker}",
                    "trigger_text": trigger_text,
                    "btn_location": btn_location,
                }
            clicked = await driver.click_row_button_v2(row, btn_text)
    else:
        # toolbar/其他位置链接：全局文本点击（click_page_button 含 a/span:has-text）
        btn_click_result = await click_page_button(page, btn_text)
        clicked = bool(btn_click_result.get("clicked"))

    if not clicked:
        cleanup_tab_listener()
        return {
            "success": False,
            "error_type": "click_failed",
            "error_text": f"无法点击 navigation 按钮: {btn_text}",
            "trigger_text": trigger_text,
            "btn_location": btn_location,
        }

    # 已验证 locator（与 generic 路径同构，供 playbook 序列化）
    trigger_locator_verified = btn_click_result.get("locator")
    if not trigger_locator_verified:
        _trigger_tag = btn_click_result.get("tag", "a")
        _actual_text = btn_click_result.get("actual_text", trigger_text)
        trigger_locator_verified = f"{_trigger_tag}:has-text('{_actual_text}')"

    # ── 后点击观察（含 ~2s 场景等待 + loading 收敛） ──
    obs = await _observe_post_click(
        page, url_before, timeout_ms=3000,
        new_page_event=new_tab_event, new_page_ref=new_tab_ref
    )
    cleanup_tab_listener()
    scenario = obs.get("scenario", "none")

    if scenario in ("new_tab", "page_nav"):
        nav_info = {"scenario": scenario, "navigated_url": "", "page_title": ""}
        if scenario == "new_tab":
            new_page = obs.get("new_page")
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            nav_info["navigated_url"] = new_page.url
            try:
                nav_info["page_title"] = await new_page.title()
            except Exception:
                pass
            # 导航已验证，关闭新标签页保持会话干净（与 generic new_tab 路径一致）
            try:
                await new_page.close()
            except Exception:
                pass
        else:
            nav_info["navigated_url"] = page.url
            try:
                nav_info["page_title"] = await page.title()
            except Exception:
                pass
            # 同 tab 跳转：回列表页继续后续操作（统一收口 navigation_executor）
            await _navigate_back_to_list(page, url_before, tab_name=tab_name, tab_locator=tab_locator)

        LOG.info(f"  [NAVIGATION] ✓ 跳转验证成功: '{btn_text}' → {nav_info['navigated_url'][:60]}")
        result = {
            "success": True,
            "action": action,
            "trigger_text": trigger_text,
            "trigger_locator_verified": trigger_locator_verified,
            "btn_location": btn_location,
            "interaction_mode": "navigation",
            "nav_info": nav_info,
        }
        # ★ 批五 F4-2：row_link 操作落 op_data 标记 + selectors——builder
        #   据此生成 find_row + click_row_name_link 步骤（三阶段一致），
        #   trigger_locator 仅作调试信息（builder row_link 分支优先短路）。
        if _row_link:
            result["row_link"] = True
            result["row_link_class"] = _row_link_class
            result["selectors"] = {
                "trigger": trigger_text,
                "row_selector": "row_name_link",
                "row_link_class": _row_link_class,
            }
        return result

    # ── 未发生导航：按实际反馈记录失败（不重试、不等弹窗） ──
    # dialog 场景属意外反馈（导航类不应弹窗），关闭弹窗恢复现场防污染后续操作
    if scenario == "dialog":
        try:
            await _close_dialog(page)
        except Exception as _cd_e:
            LOG.debug(f"  [NAVIGATION] 关闭意外弹窗失败: {_cd_e}")
    detail = f"scenario={scenario}"
    if obs.get("toast_text"):
        detail += f", toast='{obs.get('toast_text')}'"
    LOG.info(f"  [NAVIGATION] ✗ 未检测到跳转: '{btn_text}' ({detail})")
    return {
        "success": False,
        "error_type": "no_navigation_signal",
        "error_text": f"点击后未发生页面跳转（{detail}）",
        "trigger_text": trigger_text,
        "trigger_locator_verified": trigger_locator_verified,
        "btn_location": btn_location,
        "nav_info": {"scenario": scenario},
    }


async def _required_selects_still_empty(page, scope_q: str) -> list:
    """★ 4.2 提交前守卫探测：scope 容器内仍为空的必填 select 的 label 列表。

    判据（探针 4B 原型 tmp/eis_probe_batch4b.json _JS_REQUIRED_EMPTY 实证）：
    - 空判据与 pre_fill_diag 同源（无 tag / 无 input 值 / 无已选文本）
    - is-required 收紧：仅 el-form-item 带 is-required 的计入——非必填空
      select 不阻断提交（与既有 pre-fill 兜底口径一致，防误伤）
    - disabled select 跳过（级联自动填充态/禁用态不可填，不构成中止证据）

    Returns:
        [label, ...] 仍为空的必填 select label 列表（容器不可见/无命中返回 []）
    """
    return await page.evaluate(f"""(() => {{
        const container = document.querySelector('{scope_q}');
        if (!container || container.offsetWidth === 0) return [];
        const out = [];
        container.querySelectorAll('.el-select').forEach(sel => {{
            const innerInput = sel.querySelector('.el-input__inner');
            if (innerInput && innerInput.disabled) return;
            const tags = sel.querySelectorAll('.el-tag');
            const hasTags = tags && tags.length > 0;
            const hasValue = innerInput && innerInput.value;
            const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
            const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
            if (!hasTags && !hasValue && !hasSelectedText) {{
                const fi = sel.closest('.el-form-item');
                if (!fi || !(fi.className || '').includes('is-required')) return;
                const label = fi.querySelector('.el-form-item__label');
                if (label) out.push(label.textContent.trim());
            }}
        }});
        return out;
    }})()""")


async def _do_generic_operation(page, context: dict) -> dict:
    """执行通用操作（lock/unlock/reset/authorize 等）。"""
    # Lazy imports to avoid circular dependencies
    from core.discovery.operation_executor.base_executor import (
        _install_message_capture, _ensure_row_selected,
        _try_fill_empty_selects, _get_captured_messages, _verify_operation_success,
    )
    from core.discovery.operation_executor.navigation_executor import (
        _explore_and_operate_in_new_page, _navigate_back_to_list,
    )
    from core.discovery.ui_scanner.button_detector import (
        _close_dialog, _check_precondition_state,
    )

    btn = context["btn"]
    action = context["action"]
    _tab_name = context.get("tab_name", "")
    _tab_locator = context.get("tab_locator", "")
    LOG.info(f"  [ROUTE] → _do_generic_operation 被调用, action='{action}', btn_text='{btn.get('text', '')}', location='{btn.get('location', '')}'")
    marker = context.get("marker")
    btn_text = btn.get("text", "")
    btn_location = btn.get("location", "toolbar")
    # 提前归一化，确保所有 return 路径都携带 trigger_text
    trigger_text_normalized = " ".join(btn_text.split())

    # 收集弹窗中填充的 select 字段详情（用于 playbook 序列化）
    dialog_fill_details = []
    # 收集表单字段详情和提交按钮信息（用于 playbook 序列化）
    edit_field_details = []
    radio_field_details_local = []
    submit_result = {}
    # 初始化上下文变量（确保在任何失败路径都能访问）
    _verified_trigger_locator = None
    _verified_dropdown_item_text = None
    _needs_checkbox = False
    _checkbox_locator = None
    confirmed = ""
    fields = []
    filled = 0
    actual_state = ""

    def _fail(error_type: str, error_text: str, **kwargs):
        """统一的失败结果生成器，自动合并所有已收集的交互数据"""
        result = {
            "success": False,
            "error_type": error_type,
            "error_text": error_text,
            "failure_type": error_type,
            "failure_reason": error_text,
            "trigger_text": trigger_text_normalized,
            "action": action,
        }
        # 合并 kwargs
        result.update(kwargs)
        # 自动添加已收集的交互数据（如果存在且非空）
        if dialog_fill_details:
            result["dialog_fills"] = dialog_fill_details
        if edit_field_details:
            result["edit_field_details"] = edit_field_details
        if radio_field_details_local:
            result["radio_field_details"] = radio_field_details_local
        if submit_result:
            result["submit_result"] = submit_result
        if confirmed:
            result["confirmed"] = confirmed
            result["confirm_dialog"] = {
                "confirmed": confirmed,
                "button_text": confirmed,
            }
        if _verified_trigger_locator:
            result["trigger_locator_verified"] = _verified_trigger_locator
        if _verified_dropdown_item_text:
            result["dropdown_item_text_verified"] = _verified_dropdown_item_text
        if _needs_checkbox:
            result["needs_checkbox"] = True
            result["checkbox_locator"] = _checkbox_locator or ".el-checkbox__input"
        if fields:
            result["form_fields"] = fields
        if filled > 0:
            result["filled_count"] = filled
        # 添加对话框相关字段
        if actual_state and any(actual_state.startswith(prefix) for prefix in ["dialog:", "drawer:"]):
            result["dialog_detected"] = True
            result["interaction_mode"] = "dialog"
            result["dialog_locator"] = ".el-dialog__wrapper"
        return result

    # 记录点击前的 URL（用于导航保护）
    url_before = page.url

    # 安装消息捕获（在点击按钮之前，防止瞬态 toast 消失）
    LOG.info(f"  [DIAG-generic-start] 开始 _do_generic_operation，安装消息捕获器")
    await _install_message_capture(page)

    # 立即检查消息数组状态
    _initial_msgs = await page.evaluate("() => (window.__captured_messages || []).length")
    LOG.info(f"  [DIAG-after-install] 安装后消息数组长度: {_initial_msgs}")

    # ★ 添加浏览器 console 监听器，捕获 [MSG-CAPTURE] 日志（幂等）
    if not getattr(page, "_msg_capture_console_listener_added", False):
        def _on_console_msg(msg):
            text = msg.text
            if "[MSG-CAPTURE]" in text:
                LOG.info(f"    [BROWSER-CONSOLE] {text}")
        page.on("console", _on_console_msg)
        page._msg_capture_console_listener_added = True
        LOG.info(f"  [DIAG-console] 已添加浏览器 console 监听器")

    # 注册新标签页监听器（Phase 1a：统一后点击检测）
    new_tab_event, new_tab_ref, cleanup_tab_listener = _setup_new_tab_listener(page)

    # ★ 修改点 4：Generic 操作前检查当前 tab 的 switch 状态
    # 如果 toolbar/form 级 switch 是 OFF，自动开启（避免按钮被 cmss-disabled）
    try:
        _switch_pre_check = await page.evaluate("""() => {
            // 查找当前可见 tab-pane 内的 toolbar/form 级 switch
            const visiblePanes = Array.from(document.querySelectorAll('.el-tab-pane'))
                .filter(p => p.offsetWidth > 0 && window.getComputedStyle(p).display !== 'none');

            const results = [];

            for (const pane of visiblePanes) {
                const switches = pane.querySelectorAll('.el-switch');
                for (const sw of switches) {
                    // 排除行级 switch
                    if (sw.closest('.el-table__row, .el-table__body tr')) continue;

                    const isChecked = sw.classList.contains('is-checked');
                    const paneId = pane.id || 'none';

                    results.push({
                        paneId: paneId,
                        isChecked: isChecked,
                        locator: `#${paneId} .el-switch`,
                        selfChecked: sw.classList.contains('is-checked')
                    });

                    // 如果 switch 是 OFF，记录需要开启
                    if (!isChecked) {
                        results[results.length - 1].needsToggle = true;
                    }
                }
            }

            return results;
        }""")

        if _switch_pre_check:
            for sw_info in _switch_pre_check:
                # ★ 增强诊断：始终打印完整 switch 状态（含 Vue 内部值）
                LOG.info(f"  [GENERIC-SWITCH-PRECHECK] {sw_info['paneId']}: "
                         f"selfChecked={sw_info.get('selfChecked')}, "
                         f"vueValue={sw_info.get('vueValue')}, "
                         f"vueActiveValue={sw_info.get('vueActiveValue')}, "
                         f"isChecked={sw_info.get('isChecked')}")

                if sw_info.get('needsToggle'):
                    LOG.info(f"  [GENERIC-SWITCH-PRECHECK] 检测到 {sw_info['paneId']} 的 switch 为 OFF，尝试开启")

                    # 复用 _ensure_switch_enabled 逻辑
                    from core.discovery.shared.switch_ops import get_switch_state, toggle_switch

                    locator = sw_info['locator']
                    state = await get_switch_state(page, locator)

                    if state['element_found'] and not state['is_checked']:
                        result = await toggle_switch(page, locator)
                        if result.get('success'):
                            LOG.info(f"  [GENERIC-SWITCH-PRECHECK] ✓ 已开启 {sw_info['paneId']} 的 switch")
                            await page.wait_for_timeout(500)
                        else:
                            LOG.warning(f"  [GENERIC-SWITCH-PRECHECK] ✗ 开启失败: {result.get('error_text')}")
    except Exception as e:
        LOG.warning(f"  [GENERIC-SWITCH-PRECHECK] 预检失败: {e}")

    # ★ O1: 记录按钮点击前的状态（用于 playbook 生成 wait_for_button_enabled 步骤）
    context["_button_state"] = None
    context["_pagination_trace"] = None
    if marker:
        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        # ★ O3: 收集分页查找轨迹
        if hasattr(driver, '_last_pagination_trace'):
            context["_pagination_trace"] = driver._last_pagination_trace
        if row:
            context["_button_state"] = await page.evaluate(f"""async (arg) => {{
                const row = arg.row;
                const btnText = arg.btnText;
                const btns = row.querySelectorAll('button');
                for (const btn of btns) {{
                    const txt = btn.textContent.trim();
                    if (txt === btnText || txt.replace(/\\s+/g, '') === btnText.replace(/\\s+/g, '')) {{
                        return {{
                            found: true,
                            isDisabled: btn.disabled || btn.classList.contains('is-disabled'),
                            isVisible: btn.offsetWidth > 0 && btn.offsetHeight > 0,
                            text: txt,
                        }};
                    }}
                }}
                // dropdown item
                const moreBtn = row.querySelector('.el-dropdown-link');
                if (moreBtn) {{
                    moreBtn.click();
                    await new Promise(r => setTimeout(r, 300));
                    const items = document.querySelectorAll('.el-dropdown-menu__item');
                    for (const item of items) {{
                        const txt = item.textContent.trim();
                        if (txt === btnText || txt.replace(/\\s+/g, '') === btnText.replace(/\\s+/g, '')) {{
                            return {{
                                found: true,
                                isDisabled: item.classList.contains('is-disabled'),
                                isVisible: item.offsetWidth > 0,
                                text: txt,
                                tagName: 'DROPDOWN_ITEM',
                            }};
                        }}
                    }}
                }}
                return {{ found: false }};
            }}""", {"row": row, "btnText": btn_text})
            LOG.debug(f"  [O1] 按钮状态: {context.get('_button_state')}")

    # 找到行并点击
    row_selector = None
    btn_click_result = {}  # 默认初始化，row_action/dropdown 分支不设置此变量
    dropdown_click_result = None  # 防御：row_action 分支 clicked=False 时 L3701 引用会 NameError
    if btn_location == "row_action" and marker:
        from core.discovery.replay.button_driver import ButtonDriver
        from core.discovery.shared.button_clicker import (
            try_click_unique_global_button, is_destroy_text,
        )
        driver = ButtonDriver(page)
        clicked = False
        # ★ [EIS-Navi] 行级名称链接类按钮（文本全局唯一，如表格名称列实例名链接）：
        # 按钮文本本身即行归属，直接按文本全局点击——与 Stage 2 回放 click_button
        # 全局文本定位、builder fallback locator（button/a/span:has-text）同构，
        # 不再依赖 marker 行快照（共享环境行被他人删除时行内定位必然连锁失败）。
        # 门控在 try_click_unique_global_button 单源内：非破坏性 + 全局可见候选==1；
        # 每行同名按钮（estack 编辑/删除 count>1）自然跳过走下方 marker 原路径，零变化。
        _direct = await try_click_unique_global_button(page, btn_text)
        if _direct.get("clicked"):
            clicked = True
            btn_click_result = _direct  # 已验证 locator 供 trigger_locator_verified 落盘
        if not clicked:
            row = await driver.find_data_row(marker)
            if not row and not is_destroy_text(btn_text):
                # ★ 对齐 Stage 2 _step_find_row 降级语义：marker 快照行不可用
                # （共享环境行被删等）→ first_row 兜底，行级操作语义对任意行仍成立；
                # 破坏性文本不降级——对随机行执行删除类操作不可接受，保留 row_not_found
                row = await driver.find_first_data_row()
                if row:
                    LOG.warning(
                        f"    marker 行 '{marker}' 不可用，row_action 降级 first_row: '{btn_text}'")
            if not row:
                return _fail("row_not_found", f"未找到数据行: {marker}")
            clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    elif btn_location == "dropdown":
        # 两步点击：先找到行 → 展开"更多" → 点击子项
        if not marker:
            LOG.warning(f"  {action} 跳过: dropdown 操作需要 marker")
            # ★ Fix 7: 补充上下文信息
            result = skipped("dropdown 操作需要 marker",
                          trigger_text=trigger_text_normalized,
                          trigger_locator_verified=None)
            result["btn_location"] = "dropdown"
            result["dropdown_parent"] = btn.get("parent", "更多")
            result["skip_reason"] = "no_marker_for_dropdown"
            return result

        from core.discovery.replay.button_driver import ButtonDriver
        from core.discovery.shared.button_clicker import is_destroy_text
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            if not is_destroy_text(btn_text):
                # ★ 对齐 Stage 2 _step_find_row 降级语义：marker 快照行不可用 →
                # first_row 兜底（非破坏性下拉项，如 开机/关机/重启 状态类操作）；
                # 破坏性下拉项（删除/移除）保留 row_not_found 安全失败
                row = await driver.find_first_data_row()
                if row:
                    LOG.warning(
                        f"    marker 行 '{marker}' 不可用，dropdown 降级 first_row: '{btn_text}'")
            if not row:
                return _fail("row_not_found", f"未找到数据行: {marker}")
        dropdown_click_result = await driver.click_row_more_item(row, btn_text)
        clicked = dropdown_click_result.get("clicked", False)
        LOG.info(f"    [DEBUG-dropdown] click_row_more_item 返回: {dropdown_click_result}")

        # ★ 诊断：检查点击后 500ms 内下拉菜单是否关闭 + 是否有 API 请求
        await page.wait_for_timeout(500)
        _post_click_diag = await page.evaluate("""(btnText) => {
            // 检查下拉菜单是否仍然可见
            const poppers = document.querySelectorAll('.el-dropdown-menu');
            const stillVisible = Array.from(poppers).some(p => p.offsetWidth > 0 && p.offsetHeight > 0);

            // 检查父 el-dropdown 的 Vue 实例是否有 command 事件监听
            const dropdowns = document.querySelectorAll('.el-dropdown');
            const commandListeners = [];
            dropdowns.forEach(dd => {
                if (dd.__vue__) {
                    const vm = dd.__vue__;
                    commandListeners.push({
                        hasCommand: !!vm.$listeners?.command,
                        hasHandleCommand: typeof vm.handleCommand === 'function',
                        listeners: Object.keys(vm.$listeners || {})
                    });
                }
            });

            // 检查最近的网络请求
            const entries = performance.getEntriesByType('resource');
            const recentXhr = entries
                .filter(e => (e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch')
                         && !e.name.includes('menu')
                         && !e.name.includes('favorite')
                         && !e.name.includes('access-log')
                         && !e.name.includes('notice')
                         && !e.name.includes('authority'))
                .slice(-3)
                .map(e => ({ url: e.name.substring(0, 80), duration: Math.round(e.duration) }));

            return {
                dropdownStillVisible: stillVisible,
                commandListeners: commandListeners,
                recentXhr: recentXhr
            };
        }""", btn_text)
        LOG.info(f"    [DIAG-dropdown-click] 点击后诊断: {_post_click_diag}")

        row_selector = f"{btn.get('parent', '更多')} > {btn_text}"
    else:
        # toolbar 按钮：checkbox 已由调用方 (_validate_business_flow) 勾选，
        # 此处不再重复 toggle，避免双重点击导致取消勾选
        btn_click_result = await click_page_button(page, btn_text)
        clicked = btn_click_result["clicked"]
        dropdown_click_result = None

    if not clicked:
        cleanup_tab_listener()
        # ★ 检查是否因为按钮被禁用而跳过（cmss-disabled / is-disabled）
        _disable_reason = (dropdown_click_result or {}).get("reason", "")
        if _disable_reason == "button-disabled":
            LOG.info(f"    跳过 {action} 操作: 按钮被禁用 (cmss-disabled/is-disabled)")
            return skipped(f"按钮被禁用: {action}",
                          trigger_text=trigger_text_normalized)
        return _fail("click_failed", f"无法点击 {action} 按钮: {btn_text}")

    # ★ 计算已验证的触发器信息（供后续所有 return 路径使用）
    # 按钮已被成功点击 → locator 已验证，无论操作结果成功还是失败都应保留
    _verified_trigger_locator = None
    _verified_dropdown_item_text = None
    _verified_expand_strategy = None
    if btn_location == "dropdown":
        _verified_dropdown_item_text = dropdown_click_result.get("actual_text", btn_text)
        _verified_expand_strategy = dropdown_click_result.get("expand_strategy", "hover")
    else:
        _verified_trigger_locator = btn_click_result.get("locator")
        if not _verified_trigger_locator and btn_click_result:
            _trigger_tag = btn_click_result.get("tag", "button")
            _actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
            _verified_trigger_locator = f"{_trigger_tag}:has-text('{_actual_text}')"

    # ★ 计算 needs_checkbox（toolbar 非 primary 按钮需要先选中行）
    _needs_checkbox = False
    _checkbox_locator = ".el-checkbox__input"
    if btn_location == "toolbar":
        _btn_class = btn.get("className", "") or ""
        _is_primary = "primary" in _btn_class.lower()
        if not _is_primary:
            _needs_checkbox = True

    # ── Phase 1a：统一后点击检测 ──
    # 使用 _observe_post_click 检测新标签页场景（现有代码未处理的新能力）
    # 对于 page_nav/dialog/toast 场景，保持现有检测逻辑以确保向后兼容
    obs = await _observe_post_click(
        page, url_before, timeout_ms=3000,
        new_page_event=new_tab_event, new_page_ref=new_tab_ref
    )
    cleanup_tab_listener()

    # DIAG: _observe_post_click 后检查消息数组
    _msgs_after_observe = await page.evaluate("() => (window.__captured_messages || []).length")
    LOG.info(f"    [DIAG-after-observe] _observe_post_click 后，消息数组长度: {_msgs_after_observe}")

    # ── 新标签页场景（Phase 1a 新增能力） ──
    if obs["scenario"] == "new_tab":
        new_page = obs["new_page"]
        LOG.info(f"    [new-tab] 检测到新标签页，开始探索: {new_page.url[:60]}")

        nav_info = await _explore_and_operate_in_new_page(
            page=new_page,
            action=action,
            new_url=new_page.url,
            depth=0,
            max_depth=3
        )

        # ★ 提取 radio 字段到操作结果顶层
        radio_field_details = _extract_radio_fields_from_nav(nav_info)

        # 关闭新标签页
        try:
            await new_page.close()
        except Exception:
            pass

        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector

        submit_result = nav_info.get("submit_result", {})
        if submit_result and submit_result.get("success"):
            return {
                "success": True,
                "nav_info": nav_info,
                "trigger_text": trigger_text_normalized,
                "selectors": selectors,
                "fill_data": nav_info.get("fill_data", {}),
                "submit_result": submit_result,
                "interaction_mode": "new_tab",
                "radio_field_details": radio_field_details,
            }
        else:
            error_type = nav_info.get("error_type", "cross_page_no_submit")
            error_text = nav_info.get("error_text", "新标签页操作未完成")
            return _fail(error_type, error_text,
                        nav_info=nav_info,
                        selectors=selectors,
                        radio_field_details=radio_field_details)

    # ── 现有检测逻辑（向后兼容） ──
    # 注意：_observe_post_click 已做了 wait + loading_complete，
    # 但 page_nav 分支仍需要原始的 URL 对比
    url_after = page.url
    if url_before != url_after:
        LOG.info(f"    检测到页面跳转: {url_before[:40]} → {url_after[:40]}")

        # 使用递归探索函数，探测并执行新页面的操作
        nav_info = await _explore_and_operate_in_new_page(
            page=page,
            action=action,
            new_url=url_after,
            depth=0,
            max_depth=3
        )

        # ★ 提取 radio 字段到操作结果顶层
        radio_field_details = _extract_radio_fields_from_nav(nav_info)

        # 导航回原始页面
        await _navigate_back_to_list(page, url_before, tab_name=_tab_name, tab_locator=_tab_locator)

        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector

        # 根据 nav_info 判断跨页面操作是否成功（新格式：submit_result）
        submit_result = nav_info.get("submit_result", {})
        if submit_result:
            # 有提交操作执行
            if submit_result.get("success"):
                # 提交成功
                return {
                    "success": True,
                    "nav_info": nav_info,
                    "trigger_text": trigger_text_normalized,
                    "selectors": selectors,
                    "fill_data": nav_info.get("fill_data", {}),
                    "submit_result": submit_result,
                    "radio_field_details": radio_field_details,
                }
            else:
                # 提交失败
                return _fail("cross_page_submit_failed",
                            f"跨页面操作提交失败: {submit_result.get('button_text', 'unknown')}",
                            nav_info=nav_info,
                            selectors=selectors,
                            submit_result=submit_result,
                            radio_field_details=radio_field_details)
        else:
            # 没有执行提交操作，检查是否有错误
            error_type = nav_info.get("error_type", "")
            error_text = nav_info.get("error_text", "")

            if error_type:
                # 有明确错误
                return _fail(error_type, error_text,
                            nav_info=nav_info,
                            selectors=selectors,
                            radio_field_details=radio_field_details)
            else:
                # 没有错误但没有提交操作（可能是页面没有表单或关键按钮）
                return _fail("cross_page_no_submit", "跨页面操作后未执行提交操作",
                            nav_info=nav_info,
                            selectors=selectors,
                            radio_field_details=radio_field_details)

    await wait_for_loading_complete(page)

    # 特殊处理上传操作 - 检测文件上传对话框（结构性判断，不依赖操作名）
    has_upload_dialog = await page.evaluate("""() => {
        const fileInput = document.querySelector('input[type="file"]');
        const uploadComponent = document.querySelector('.el-upload, .ant-upload');
        const uploadDialog = document.querySelector('.el-dialog, .ant-modal');

        if (fileInput && fileInput.offsetParent !== null) {
            return true;
        }
        if (uploadComponent && uploadComponent.offsetParent !== null) {
            return true;
        }
        if (uploadDialog && uploadDialog.offsetParent !== null) {
            const dialogUpload = uploadDialog.querySelector('.el-upload, .ant-upload, input[type="file"]');
            if (dialogUpload) return true;
        }
        return false;
    }""")

    if has_upload_dialog:
        LOG.info(f"    检测到文件上传对话框（{action} 操作），无法自动完成")
        await _close_dialog(page)
        selectors = {"trigger": btn_text}
        if row_selector:
            selectors["row_selector"] = row_selector
        # 文件上传操作无法自动完成（需要文件），标记为失败
        # ★ 包含已验证的触发器信息，供 playbook builder 生成步骤
        result = make_error("env_dependency", "文件上传操作无法自动完成",
                         trigger_text=trigger_text_normalized,
                         trigger_locator_verified=_verified_trigger_locator,
                         dropdown_item_text_verified=_verified_dropdown_item_text,
                         expand_strategy_verified=_verified_expand_strategy,
                         needs_checkbox=_needs_checkbox,
                         checkbox_locator=_checkbox_locator,
                         selectors=selectors)
        # ★ O7: 记录文件上传信息
        result["upload_info"] = {
            "file_input_locator": "input[type='file']",
            "file_format": "xlsx",  # 或 csv
            "upload_strategy": "set_input_files",
        }
        return result

    # ============================================================
    # 弹窗检测 + 按容器类型分发处理
    # drawer/dialog → 表单模式（扫描字段 → 填充 → 提交）
    # message-box/popconfirm → 确认框模式（填充 select → 点确认）
    # 无弹窗 → 即时操作模式（检查 toast/数据变化）
    # ============================================================
    # DIAG: 在调用 _check_precondition_state 前，先检查消息数组状态
    _msgs_before_dialog = await page.evaluate("() => (window.__captured_messages || []).length")
    LOG.info(f"    [DIAG-before-dialog-check] 弹窗检测前，消息数组长度: {_msgs_before_dialog}")

    # DIAG: 在调用 _check_precondition_state 前，先 dump 当前 DOM 弹窗状态
    _pre_diag = await page.evaluate("""() => {
        const result = { msgBox: false, popconfirm: false, dialog: false, drawer: false };
        const mb = document.querySelector('.el-message-box__wrapper');
        if (mb && mb.offsetWidth > 0) result.msgBox = { visible: true, display: mb.style.display, cls: (mb.className || '').substring(0, 60) };
        const pc = document.querySelector('.el-popconfirm');
        if (pc && pc.offsetWidth > 0) result.popconfirm = { visible: true, cls: (pc.className || '').substring(0, 60) };
        const dlgs = document.querySelectorAll('.el-dialog__wrapper');
        for (const d of dlgs) {
            if (d.offsetWidth > 0 && d.style.display !== 'none') { result.dialog = { visible: true, display: d.style.display }; break; }
        }
        const drs = document.querySelectorAll('.el-drawer');
        for (const d of drs) {
            if (d.offsetWidth > 0 && d.style.display !== 'none') { result.drawer = { visible: true, display: d.style.display }; break; }
        }
        return result;
    }""")
    import json as _json7
    LOG.info(f"    [DIAG-generic-precheck] DOM弹窗状态: {_json7.dumps(_pre_diag, ensure_ascii=False)}")

    state = await _check_precondition_state(page, {"type": "dialog"})
    actual_state = state.get("actual_state", "") if state["success"] else ""
    LOG.info(f"    [DIAG-generic-precheck] _check_precondition_state → success={state['success']}, actual_state='{actual_state}'")

    # DIAG: 弹窗检测后，检查消息数组
    _msgs_after_dialog_check = await page.evaluate("() => (window.__captured_messages || []).length")
    LOG.info(f"    [DIAG-after-dialog-check] 弹窗检测后，消息数组长度: {_msgs_after_dialog_check}")
    # actual_state 可能是 "drawer: 标题" 或 "dialog: 标题"，提取类型部分
    _container_type = actual_state.split(":")[0].strip() if actual_state else ""
    confirmed = ""

    if _container_type in ("drawer", "dialog"):
        # ── 表单容器（drawer / dialog）：扫描字段 → 填充 → 提交 ──
        LOG.info(f"    检测到表单容器: {actual_state}")
        form_filler = context.get("form_filler")

        # 先填充容器中的空 select 字段
        _sel_q = '.el-drawer:not([style*="display: none"])' if _container_type == 'drawer' else '.el-dialog__wrapper:not([style*="display: none"])'
        pre_fill_diag = await page.evaluate(f"""() => {{
            const container = document.querySelector('{_sel_q}');
            if (!container || container.offsetWidth === 0) return {{ empty_selects: [] }};
            const emptySelects = [];
            container.querySelectorAll('.el-select').forEach(sel => {{
                const innerInput = sel.querySelector('.el-input__inner');
                if (innerInput && innerInput.disabled) return;
                const tags = sel.querySelectorAll('.el-tag');
                const hasTags = tags && tags.length > 0;
                const hasValue = innerInput && innerInput.value;
                const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                if (!hasTags && !hasValue && !hasSelectedText) {{
                    const label = sel.closest('.el-form-item')
                        ?.querySelector('.el-form-item__label')
                        ?.textContent?.trim();
                    if (label) emptySelects.push(label);
                }}
            }});
            return {{ empty_selects: emptySelects }};
        }}""")

        empty_sels = pre_fill_diag.get("empty_selects", [])
        if empty_sels:
            LOG.info(f"    {actual_state} 中有 {len(empty_sels)} 个空 select，先填充: {empty_sels}")
            _, fill_details = await _try_fill_empty_selects(page, empty_sels)
            dialog_fill_details.extend(fill_details)
            await page.wait_for_timeout(500)

        # 检查容器中是否有表单（form / .el-form / .el-form-item 都算）
        _form_q = '.el-drawer:not([style*="display: none"])' if _container_type == 'drawer' else '.el-dialog__wrapper:not([style*="display: none"])'
        has_form = await page.evaluate(f"""() => {{
            const container = document.querySelector('{_form_q}');
            if (!container || container.offsetWidth === 0) return false;
            return !!(container.querySelector('form') || container.querySelector('.el-form') || container.querySelector('.el-form-item'));
        }}""")

        if not has_form:
            # 无表单的容器（纯展示或简单确认）→ 填充空 select + 回退到确认框处理
            LOG.info(f"    {actual_state} 中无表单，回退到确认框处理")

            # 确认前先填充空 select 字段（对齐旧代码行为，迁移等操作需要）
            pre_fill_diag = await page.evaluate(f"""(() => {{
                const container = document.querySelector('{_sel_q}');
                if (!container || container.offsetWidth === 0) return {{ empty_selects: [] }};
                const emptySelects = [];
                container.querySelectorAll('.el-select').forEach(sel => {{
                    const innerInput = sel.querySelector('.el-input__inner');
                    if (innerInput && innerInput.disabled) return;
                    const tags = sel.querySelectorAll('.el-tag');
                    const hasTags = tags && tags.length > 0;
                    const hasValue = innerInput && innerInput.value;
                    const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                    const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                    if (!hasTags && !hasValue && !hasSelectedText) {{
                        const label = sel.closest('.el-form-item')
                            ?.querySelector('.el-form-item__label')
                            ?.textContent?.trim();
                        if (label) emptySelects.push(label);
                    }}
                }});
                return {{ empty_selects: emptySelects }};
            }})()""")

            empty_sels = pre_fill_diag.get("empty_selects", [])
            if empty_sels:
                LOG.info(f"    确认前有 {len(empty_sels)} 个空 select 字段，先填充: {empty_sels}")
                _, fill_details = await _try_fill_empty_selects(page, empty_sels)
                dialog_fill_details.extend(fill_details)
                await page.wait_for_timeout(500)

            from core.discovery.replay.button_driver import confirm_dialog
            LOG.info(f"    [DIAG-generic-path1] drawer/dialog 无表单，调用 confirm_dialog...")
            _msgs_before_confirm = await page.evaluate("() => (window.__captured_messages || []).length")
            LOG.info(f"    [DIAG-before-confirm] 调用 confirm_dialog 前，消息数组长度: {_msgs_before_confirm}")
            confirmed = await confirm_dialog(page)
            LOG.info(f"    [DIAG-generic-path1] confirm_dialog 返回: '{confirmed}'")
            _msgs_after_confirm = await page.evaluate("() => (window.__captured_messages || []).length")
            LOG.info(f"    [DIAG-after-confirm] confirm_dialog 后，消息数组长度: {_msgs_after_confirm}")
            if confirmed:
                # 验证返回的文本是否真的是确认按钮文本（去空格后匹配 CONFIRM_TEXTS）
                confirmed_normalized = confirmed.replace(" ", "")
                is_real_confirm = confirmed_normalized in const.CONFIRM_TEXTS_SET
                if is_real_confirm:
                    LOG.info(f"    已点击确认按钮: {confirmed}")
                else:
                    # confirm_dialog 返回了非确认按钮文本（可能是弹窗标题或内容文本）
                    # 说明弹窗内没有确认按钮 → 只读查看型弹窗
                    # ★ 统一判定器：关闭前以 read_complete 模式验证（无失败信号即成功）。
                    #   read_complete 不扫弹窗全文（防只读内容误报），但保留 error toast /
                    #   表单校验两类结构化错误通道（真实加载错误仍判失败）
                    LOG.info(f"    confirm_dialog 返回了非确认文本: '{confirmed[:40]}'（只读查看型弹窗）")
                    _rd_msgs = await _get_captured_messages(page)
                    _rd_ok, _rd_detail = await _verify_operation_success(
                        page, action, mode="read_complete", captured_messages=_rd_msgs)
                    if _rd_ok:
                        closed = await confirm_dialog(page, confirm=False)
                        if not closed:
                            await _close_dialog(page)
                        return {
                            "success": True,
                            "trigger_text": trigger_text_normalized,
                            "selectors": {"trigger": btn_text},
                            "interaction_mode": "readonly_dialog",
                            "readonly_dialog": True,
                            "matched_method": _rd_detail.get("matched_method"),
                            "trigger_locator_verified": _verified_trigger_locator,
                            "dialog_locator": _sel_q,
                            "container_type": _container_type,
                        }
                    else:
                        # 判定器检出失败信号 → 关闭弹窗清理现场后如实判失败
                        await confirm_dialog(page, confirm=False)
                        return _fail(
                            "readonly_dialog_error",
                            f"{action} 只读弹窗操作检出失败信号: "
                            f"{_rd_detail.get('failure_reason', 'unknown')}",
                            trigger_locator_verified=_verified_trigger_locator,
                        )
            else:
                # ★ 修复: 无确认按钮时，尝试关闭弹窗（取消/关闭按钮）
                # 只读查看型 dialog（如"导入历史"）只有取消按钮，关闭即为成功
                LOG.info(f"    {actual_state} 中未找到确认按钮，尝试关闭弹窗...")
                closed = await confirm_dialog(page, confirm=False)
                if closed:
                    LOG.info(f"    已点击取消/关闭按钮: '{closed}'（只读查看型弹窗）")
                else:
                    await _close_dialog(page)
                    return _fail("no_confirm_button", f"{action} {actual_state} 中未找到确认按钮")
            await wait_for_loading_complete(page)
            await page.wait_for_timeout(1000)
        elif form_filler:
            # 有表单 → 扫描字段 → 填充 → 提交（限定到 drawer/dialog 容器内）
            LOG.info(f"    {actual_state} 表单处理: 扫描字段 (scope={_sel_q})...")
            fields = await form_filler.scan_form_fields_v2(scope_selector=_sel_q)
            LOG.info(f"    扫描到 {len(fields)} 个字段: {[f.get('label', '?') for f in fields]}")
            filled = await form_filler.fill_edit_form(fields, context.get("edit_overrides"))
            LOG.info(f"    填充了 {filled} 个字段")

            # 收集填充的字段详情（用于 playbook 生成）
            edit_field_details = []
            radio_field_details_local = []
            for field in fields:
                input_type = field.get("inputType", "text")
                kb_category = field.get("kb_category", "")
                # 收集 input 类型字段（包括 number、text 等）
                if field.get("type") == "input" and field.get("selector"):
                    detail = {
                        "label": field.get("label"),
                        "selector": field.get("selector"),
                        "playwright_locator": field.get("playwright_locator"),
                        "type": "input",
                        "inputType": input_type,
                    }
                    # ★ 透传 placeholder、thead_label、visible_index，用于跨会话稳定定位
                    if field.get("placeholder"):
                        detail["placeholder"] = field["placeholder"]
                    if field.get("thead_label"):
                        detail["thead_label"] = field["thead_label"]
                    if field.get("visible_index") is not None:
                        detail["visible_index"] = field["visible_index"]
                    # ★ D3: 收集 fill_rule（从 form_filler 写回的 _fill_rule）
                    if field.get("_fill_rule"):
                        detail["fill_rule"] = field["_fill_rule"]
                    edit_field_details.append(detail)
                # ★ 提取 radio 字段到顶层
                elif kb_category == "radio":
                    radio_detail = {
                        "label": field.get("label"),
                        "kb_category": "radio",
                        "option_locator": field.get("selector"),
                        "option_text": field.get("firstOptionText", ""),
                        "fill_rule": field.get("fill_rule", {}),
                    }
                    if "cascade_actions" in field:
                        radio_detail["cascade_actions"] = field["cascade_actions"]
                    radio_field_details_local.append(radio_detail)

            if filled == 0:
                LOG.info(f"    无可填字段，尝试直接提交")

            # ★ 4.2 必填空 select 提交前中止: pre-fill 尝试后仍有 is-required 且为空的
            # select（实证: 更新密钥对「密钥对」无可选项恒空 → 提交按钮 disabled →
            # 无提交路径）→ 提交必被表单校验拒绝，点击只会产生静默失败（no_signal
            # 假象）或否定前缀按钮误点风险。落 required_field_empty 终态（与既有
            # 提交后兜底 L5473 同型），不空转重试。
            try:
                _req_empty = await _required_selects_still_empty(page, _sel_q)
            except Exception as _guard_e:
                LOG.debug(f"    [4.2-guard] 守卫探测异常（放行提交）: {_guard_e}")
                _req_empty = []
            if _req_empty:
                LOG.warning(f"    [4.2-guard] 提交中止: 必填下拉字段仍为空: {_req_empty}")
                await _close_dialog(page)
                return _fail(
                    "required_field_empty",
                    f"{action} 弹窗中必填下拉字段仍为空且无法自动填充: {', '.join(_req_empty)}",
                    error_field=_req_empty[0],
                )

            submit_result = await form_filler.submit_form_v2(scope_selector=_sel_q)
            LOG.debug(f"    submit_form_v2 返回: {submit_result}")
            # ★ 批五 F3：守卫终态优先——按钮 disabled 未点击 / 点击后弹窗滞留，
            #   均为提交未生效的终态，不回退 confirm_dialog（同弹窗同批按钮，
            #   回退无意义），直接落失败。
            if submit_result.get("submit_blocked"):
                LOG.warning(f"    [F3] {action} 提交守卫拦截: "
                            f"{submit_result['submit_blocked']} —— "
                            f"{submit_result.get('blocked_reason', '')}")
                await _close_dialog(page)
                return _fail(
                    submit_result["submit_blocked"],
                    f"{action} 提交未生效: {submit_result.get('blocked_reason', '')}",
                )
            if not submit_result.get("locator"):
                LOG.info(f"    submit_form_v2 未找到提交按钮，回退到 confirm_dialog")
                from core.discovery.replay.button_driver import confirm_dialog
                LOG.info(f"    [DIAG-generic-path2] submit_form_v2 回退，调用 confirm_dialog...")
                confirmed = await confirm_dialog(page)
                LOG.info(f"    [DIAG-generic-path2] confirm_dialog 返回: '{confirmed}'")
                if not confirmed:
                    await _close_dialog(page)
                    return _fail("no_confirm_button", f"{action} {actual_state} 中未找到提交/确认按钮")
            await page.wait_for_timeout(2000)
            await wait_for_loading_complete(page)

            # 检查表单是否仍在显示
            drawer_still_open = await page.evaluate("""() => {
                const drawer = document.querySelector('.el-drawer');
                return drawer && drawer.offsetWidth > 0;
            }""")
            LOG.debug(f"    提交后 drawer 是否仍打开: {drawer_still_open}")

            # 检查表单错误
            errors = await read_form_errors(page)
            if errors:
                field_errors = [e for e in errors if e.get("severity") == "field"]
                if field_errors:
                    await _close_dialog(page)
                    return _fail("form_validation", field_errors[0].get("error_text", ""),
                                error_field=field_errors[0].get("field_label", ""))
            # 表单已提交，confirmed 留空，直接进入成功验证
        else:
            # 有表单但无 form_filler → 回退到填充空 select + 确认（对齐旧代码行为）
            LOG.warning(f"    {actual_state} 有表单但无 form_filler，回退到 _try_fill_empty_selects + confirm_dialog")

            pre_fill_diag = await page.evaluate(f"""(() => {{
                const container = document.querySelector('{_sel_q}');
                if (!container || container.offsetWidth === 0) return {{ empty_selects: [] }};
                const emptySelects = [];
                container.querySelectorAll('.el-select').forEach(sel => {{
                    const innerInput = sel.querySelector('.el-input__inner');
                    if (innerInput && innerInput.disabled) return;
                    const tags = sel.querySelectorAll('.el-tag');
                    const hasTags = tags && tags.length > 0;
                    const hasValue = innerInput && innerInput.value;
                    const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                    const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                    if (!hasTags && !hasValue && !hasSelectedText) {{
                        const label = sel.closest('.el-form-item')
                            ?.querySelector('.el-form-item__label')
                            ?.textContent?.trim();
                        if (label) emptySelects.push(label);
                    }}
                }});
                return {{ empty_selects: emptySelects }};
            }})()""")

            empty_sels = pre_fill_diag.get("empty_selects", [])
            if empty_sels:
                LOG.info(f"    确认前有 {len(empty_sels)} 个空 select 字段，先填充: {empty_sels}")
                _, fill_details = await _try_fill_empty_selects(page, empty_sels)
                dialog_fill_details.extend(fill_details)
                await page.wait_for_timeout(500)

            from core.discovery.replay.button_driver import confirm_dialog
            confirmed = await confirm_dialog(page)
            if confirmed:
                LOG.info(f"    已点击确认按钮: {confirmed}")
            else:
                LOG.warning(f"    {actual_state} 中未找到确认按钮")
                await _close_dialog(page)
                return _fail("no_confirm_button", f"{action} {actual_state} 中未找到确认按钮")
            await wait_for_loading_complete(page)
            await page.wait_for_timeout(1000)

    elif _container_type in ("message-box", "popconfirm"):
        # ── 简单确认框：填充空 select → 点确认 ──
        LOG.info(f"    检测到确认框: {actual_state}")

        # 填充空 select 字段
        pre_fill_diag = await page.evaluate("""() => {
            const containers = [];
            document.querySelectorAll(
                '.el-dialog__wrapper:not([style*="display: none"]), '
                + '.el-message-box__wrapper:not([style*="display: none"])'
            ).forEach(d => {
                if (d.offsetWidth > 0) containers.push(d);
            });
            if (containers.length === 0) return { empty_selects: [] };
            const emptySelects = [];
            for (const el of containers) {
                el.querySelectorAll('.el-select').forEach(sel => {
                    const innerInput = sel.querySelector('.el-input__inner');
                    if (innerInput && innerInput.disabled) return;
                    const tags = sel.querySelectorAll('.el-tag');
                    const hasTags = tags && tags.length > 0;
                    const hasValue = innerInput && innerInput.value;
                    const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                    const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                    if (!hasTags && !hasValue && !hasSelectedText) {
                        const label = sel.closest('.el-form-item')
                            ?.querySelector('.el-form-item__label')
                            ?.textContent?.trim();
                        if (label) emptySelects.push(label);
                    }
                });
            }
            return { empty_selects: emptySelects };
        }""")

        empty_sels = pre_fill_diag.get("empty_selects", [])
        if empty_sels:
            LOG.info(f"    确认前有 {len(empty_sels)} 个空 select 字段，先填充: {empty_sels}")
            _, fill_details = await _try_fill_empty_selects(page, empty_sels)
            dialog_fill_details.extend(fill_details)
            await page.wait_for_timeout(500)

        from core.discovery.replay.button_driver import confirm_dialog
        LOG.info(f"    [DIAG-generic-path3] 确认框模式，调用 confirm_dialog...")
        _msgs_before_confirm = await page.evaluate("() => (window.__captured_messages || []).length")
        LOG.info(f"    [DIAG-path3-before-confirm] 调用 confirm_dialog 前，消息数组长度: {_msgs_before_confirm}")
        confirmed = await confirm_dialog(page)
        LOG.info(f"    [DIAG-generic-path3] confirm_dialog 返回: '{confirmed}'")
        _msgs_after_confirm = await page.evaluate("() => (window.__captured_messages || []).length")
        LOG.info(f"    [DIAG-path3-after-confirm] confirm_dialog 后，消息数组长度: {_msgs_after_confirm}")
        if confirmed:
            LOG.info(f"    已点击确认按钮: {confirmed}")
        else:
            LOG.warning(f"    确认框存在但未找到确认按钮")
        await wait_for_loading_complete(page)
        await page.wait_for_timeout(1000)

        # ── Phase 2b: 链式确认（确认→表单） ──
        # 确认框关闭后可能弹出新的表单弹窗，需要检测并处理
        chain_obs = await _observe_post_click(page, page.url, timeout_ms=2000)
        if chain_obs["scenario"] == "dialog" and chain_obs["container_type"] in ("drawer", "dialog"):
            LOG.info(f"    [chain-confirm] 确认后弹出新表单: {chain_obs['actual_state']}")
            chain_container_type = chain_obs["container_type"]
            chain_sel_q = '.el-drawer:not([style*="display: none"])' if chain_container_type == 'drawer' else '.el-dialog__wrapper:not([style*="display: none"])'

            # 填充新弹窗中的空 select
            chain_pre_fill = await page.evaluate(f"""() => {{
                const container = document.querySelector('{chain_sel_q}');
                if (!container || container.offsetWidth === 0) return {{ empty_selects: [] }};
                const emptySelects = [];
                container.querySelectorAll('.el-select').forEach(sel => {{
                    const innerInput = sel.querySelector('.el-input__inner');
                    if (innerInput && innerInput.disabled) return;
                    const tags = sel.querySelectorAll('.el-tag');
                    const hasTags = tags && tags.length > 0;
                    const hasValue = innerInput && innerInput.value;
                    const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                    const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                    if (!hasTags && !hasValue && !hasSelectedText) {{
                        const label = sel.closest('.el-form-item')
                            ?.querySelector('.el-form-item__label')
                            ?.textContent?.trim();
                        if (label) emptySelects.push(label);
                    }}
                }});
                return {{ empty_selects: empty_selects }};
            }}""")

            chain_empty_sels = chain_pre_fill.get("empty_selects", [])
            if chain_empty_sels:
                LOG.info(f"    [chain-confirm] 新弹窗有 {len(chain_empty_sels)} 个空 select，先填充: {chain_empty_sels}")
                _, chain_fill_details = await _try_fill_empty_selects(page, chain_empty_sels)
                dialog_fill_details.extend(chain_fill_details)
                await page.wait_for_timeout(500)

            # 检查是否有表单
            chain_has_form = chain_obs.get("has_form", False)
            if chain_has_form and form_filler:
                LOG.info(f"    [chain-confirm] 扫描新弹窗表单字段...")
                chain_fields = await form_filler.scan_form_fields_v2(scope_selector=chain_sel_q)
                LOG.info(f"    [chain-confirm] 扫描到 {len(chain_fields)} 个字段")

                if chain_fields:
                    chain_filled = await form_filler.fill_edit_form(chain_fields, context.get("edit_overrides"))
                    LOG.info(f"    [chain-confirm] 填充了 {chain_filled} 个字段")

                    chain_submit = await form_filler.submit_form_v2(scope_selector=chain_sel_q)
                    # ★ 批五 F3：守卫终态——不回退 confirm_dialog（同弹窗同批按钮）
                    if chain_submit.get("submit_blocked"):
                        LOG.warning(f"    [chain-confirm] 提交守卫拦截: "
                                    f"{chain_submit['submit_blocked']} —— "
                                    f"{chain_submit.get('blocked_reason', '')}")
                    elif not chain_submit.get("locator"):
                        LOG.info(f"    [chain-confirm] 未找到提交按钮，回退到 confirm_dialog")
                        chain_confirmed = await confirm_dialog(page)
                        if chain_confirmed:
                            LOG.info(f"    [chain-confirm] 链式确认按钮: {chain_confirmed}")
                    await page.wait_for_timeout(2000)
                    await wait_for_loading_complete(page)
            else:
                # 无表单，尝试直接确认
                chain_confirmed = await confirm_dialog(page)
                if chain_confirmed:
                    LOG.info(f"    [chain-confirm] 链式确认按钮: {chain_confirmed}")
                await wait_for_loading_complete(page)
                await page.wait_for_timeout(1000)

    else:
        # ── 无弹窗：可能是即时操作（状态切换、toast 反馈等） ──
        LOG.debug(f"    未检测到弹窗 (state: {state.get('actual_state', 'none')})")
        _msgs_no_dialog = await page.evaluate("() => (window.__captured_messages || []).length")
        LOG.info(f"    [DIAG-no-dialog] 无弹窗路径，消息数组长度: {_msgs_no_dialog}")

        # ★ [DIAG] 全面 DOM 诊断：检查点击后页面实际状态
        _dom_diag = await page.evaluate("""() => {
            const result = {
                // 1. 所有可见的 message/notification/dialog 元素
                messages: [],
                notifications: [],
                dialogs: [],
                messageBoxes: [],
                popconfirms: [],
                // 2. body 直接子元素中的弹出层
                bodyPopups: [],
                // 3. 最近的网络请求
                recentRequests: [],
                // 4. 按钮所在行的状态
                targetRow: null
            };

            // 检查 el-message
            document.querySelectorAll('.el-message, .el-message--success, .el-message--error, .el-message--warning, .el-message--info').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.messages.push({
                    text: (el.textContent || '').trim().substring(0, 100),
                    cls: el.className,
                    visible: rect.width > 0 && rect.height > 0,
                    rect: {top: rect.top, left: rect.left, width: rect.width, height: rect.height}
                });
            });

            // 检查 el-notification（含 alert 类型）
            document.querySelectorAll('.el-notification, [class*="el-notification"], [class*="alert"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.notifications.push({
                    text: (el.textContent || '').trim().substring(0, 100),
                    cls: el.className,
                    visible: rect.width > 0 && rect.height > 0,
                    rect: {top: Math.round(rect.top), left: Math.round(rect.left), width: Math.round(rect.width), height: Math.round(rect.height)}
                });
            });

            // 检查 el-dialog
            document.querySelectorAll('.el-dialog__wrapper, .el-dialog').forEach(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                result.dialogs.push({
                    text: (el.textContent || '').trim().substring(0, 100),
                    cls: el.className,
                    visible: rect.width > 0 && rect.height > 0,
                    display: style.display,
                    visibility: style.visibility
                });
            });

            // 检查 el-message-box
            document.querySelectorAll('.el-message-box__wrapper, .el-message-box').forEach(el => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                result.messageBoxes.push({
                    text: (el.textContent || '').trim().substring(0, 100),
                    cls: el.className,
                    visible: rect.width > 0 && rect.height > 0,
                    display: style.display,
                    visibility: style.visibility
                });
            });

            // 检查 el-popconfirm
            document.querySelectorAll('.el-popconfirm, [class*="popconfirm"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                result.popconfirms.push({
                    text: (el.textContent || '').trim().substring(0, 100),
                    cls: el.className,
                    visible: rect.width > 0 && rect.height > 0
                });
            });

            // 检查 body 直接子元素中的弹出层（z-index 高的元素）
            document.body.querySelectorAll(':scope > *').forEach(el => {
                const style = window.getComputedStyle(el);
                const zIndex = parseInt(style.zIndex) || 0;
                if (zIndex > 100) {
                    const rect = el.getBoundingClientRect();
                    result.bodyPopups.push({
                        tag: el.tagName,
                        cls: el.className.substring(0, 50),
                        zIndex: zIndex,
                        visible: rect.width > 0 && rect.height > 0,
                        text: (el.textContent || '').trim().substring(0, 50)
                    });
                }
            });

            // 最近的网络请求（performance API）
            const entries = performance.getEntriesByType('resource');
            const xhrEntries = entries.filter(e => e.initiatorType === 'xmlhttprequest' || e.initiatorType === 'fetch');
            result.recentRequests = xhrEntries.slice(-5).map(e => ({
                url: e.name.substring(0, 80),
                duration: Math.round(e.duration)
            }));

            return result;
        }""")
        LOG.info(f"    [DIAG-DOM] 点击后 DOM 状态: {_dom_diag}")

        # ★ 优先检查 toast（页面通知是最准确的成功信号）
        # 大多数操作（如黑白名单的暂停/启用/删除）都有明确的成功 toast，
        # 直接根据 toast 判断成功，不需要通过按钮状态推断。
        _instant_msgs = await _get_captured_messages(page)

        # [DIAG] 诊断 __captured_messages 状态
        _diag_cap = await page.evaluate("""() => ({
            hasObserver: !!window.__msg_capture_observer,
            observerConnected: window.__msg_capture_observer ?
                (typeof window.__msg_capture_observer.takeRecords === 'function') : false,
            msgs: window.__captured_messages || [],
            msgCount: (window.__captured_messages || []).length
        })""")
        LOG.info(f"    [DIAG-toast] __captured_messages 状态: {_diag_cap}")

        if _instant_msgs:
            LOG.info(f"    [toast-first] 检测到 {len(_instant_msgs)} 条捕获消息")
            _instant_success, _ = await _verify_operation_success(
                page, action, captured_messages=_instant_msgs)
            if _instant_success:
                LOG.info(f"    [toast-first] 检测到成功 toast，直接标记成功")
                return {"success": True, "trigger_text": trigger_text_normalized,
                        "selectors": {"trigger": btn_text}}

        # ★ toast 轮询：等待 API 响应完成后的成功 toast
        # 某些操作（如黑白名单暂停/启用）API 响应较慢，toast 在 observe 窗口后才出现。
        # 轮询只检查 toast/DOM 通知，不依赖按钮状态推断。
        _poll_success = False
        _poll_toast_text = ""
        for _poll_round in range(6):  # 6 轮 × 2s = 最多 12s
            await page.wait_for_timeout(2000)

            # 检查 1: __captured_messages（MutationObserver 捕获）
            _poll_msgs = await _get_captured_messages(page)
            if _poll_msgs:
                LOG.info(f"    [toast-poll] 第 {_poll_round+1} 轮：captured {len(_poll_msgs)} 条: "
                         f"{[m.get('text','')[:40] for m in _poll_msgs]}")
                from core.discovery import const as _pc
                for m in _poll_msgs:
                    if _pc.classify_notification(m.get('text', '')) == "success":
                        _poll_toast_text = m.get('text', '')
                        _poll_success = True
                        break
                if _poll_success:
                    break
                # 检查失败信号
                for m in _poll_msgs:
                    if _pc.classify_notification(m.get('text', '')) == "failure":
                        LOG.warning(f"    [toast-poll] 第 {_poll_round+1} 轮：检测到失败 toast: {m.get('text','')[:60]}")
                        return _fail("operation_failed",
                                    f"{action} 操作失败: {m.get('text', '')}")

            # 检查 2: 直接查询 DOM 中当前可见的通知（兜底，防止 MutationObserver 遗漏）
            _dom_toast = await page.evaluate("""() => {
                const selectors = [
                    '.el-message--success', '.el-message', '.el-notification',
                    '.el-alert', '[role="alert"]'
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (el.offsetWidth > 0 && el.offsetHeight > 0) {
                            const text = (el.textContent || '').trim();
                            if (text) return { text: text.substring(0, 200), cls: el.className };
                        }
                    }
                }
                return null;
            }""")
            if _dom_toast:
                LOG.info(f"    [toast-poll] 第 {_poll_round+1} 轮：DOM 可见通知: {_dom_toast}")
                from core.discovery import const as _pc2
                if _pc2.classify_notification(_dom_toast.get('text', '')) == "success":
                    _poll_toast_text = _dom_toast.get('text', '')
                    _poll_success = True
                    break
            else:
                LOG.debug(f"    [toast-poll] 第 {_poll_round+1} 轮：无可见 toast，继续等待...")

        if _poll_success:
            LOG.info(f"    [toast-poll] 检测到成功 toast: '{_poll_toast_text[:60]}'")
            return {"success": True, "trigger_text": trigger_text_normalized,
                    "selectors": {"trigger": btn_text},
                    "toast_signal": _poll_toast_text}

        # 12s 后仍无 toast，返回失败以触发重试
        LOG.warning(f"    [toast-poll] 12s 后仍未检测到成功 toast")

    # P1: 确认按钮未点击（仅对确认框路径生效，表单路径和即时操作已提前处理）
    if not confirmed and actual_state in ("message-box", "popconfirm"):
        # 诊断失败原因：检查所有类型的弹窗（含 drawer），找出真正原因
        _confirm_check_js = const.js_normalize_in(const.CONFIRM_TEXTS, "txt")
        _diag_js = """() => {
            const containers = [];

            // 1. el-message-box
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            if (msgBox && msgBox.offsetWidth > 0) containers.push({type: 'message-box', el: msgBox});

            // 2. el-popconfirm
            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            if (popconfirm && popconfirm.offsetWidth > 0) containers.push({type: 'popconfirm', el: popconfirm});

            // 3. el-dialog
            document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])').forEach(d => {
                if (d.offsetWidth > 0) containers.push({type: 'dialog', el: d});
            });

            // 4. el-drawer
            document.querySelectorAll('.el-drawer:not([style*="display: none"])').forEach(d => {
                if (d.offsetWidth > 0) containers.push({type: 'drawer', el: d});
            });

            if (containers.length === 0) {
                return { has_dialog: false };
            }

            for (const c of containers) {
                const el = c.el;
                const result = { has_dialog: true, dialog_type: c.type };

                // drawer/dialog 有表单 → 标记为表单类型
                if (c.type === 'drawer' || c.type === 'dialog') {
                    const hasForm = !!(el.querySelector('form') || el.querySelector('.el-form'));
                    if (hasForm) {
                        result.is_form_container = true;
                        return result;
                    }
                }

                // 检查空的 select 字段
                const emptySelects = [];
                el.querySelectorAll('.el-select').forEach(sel => {
                    const input = sel.querySelector('.el-input__inner');
                    const tags = sel.querySelectorAll('.el-tag');
                    const hasTags = tags && tags.length > 0;
                    const hasValue = input && input.value;
                    const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                    const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                    const isEmpty = !hasTags && !hasValue && !hasSelectedText;
                    if (isEmpty) {
                        const label = sel.closest('.el-form-item')?.querySelector('.el-form-item__label')?.textContent?.trim();
                        emptySelects.push(label || '未知字段');
                    }
                });

                const allBtns = el.querySelectorAll('button');
                const btnInfos = [];
                allBtns.forEach(btn => {
                    const txt = (btn.textContent || '').trim();
                    btnInfos.push({
                        text: txt,
                        disabled: btn.disabled,
                        visible: btn.offsetWidth > 0
                    });
                });

                let confirmBtn = null;
                allBtns.forEach(btn => {
                    const txt = (btn.textContent || '').trim();
                    if (CONFIRM_TEXTS_PLACEHOLDER && !confirmBtn) confirmBtn = btn;
                });

                result.buttons = btnInfos;
                result.confirm_found = !!confirmBtn;
                result.confirm_disabled = confirmBtn ? confirmBtn.disabled : null;
                result.empty_selects = emptySelects;

                if (emptySelects.length > 0 || (confirmBtn && confirmBtn.disabled)) {
                    result.has_actionable_issue = true;
                    return result;
                }

                result.has_actionable_issue = false;
                return result;
            }

            return { has_dialog: false };
        }""".replace("CONFIRM_TEXTS_PLACEHOLDER", _confirm_check_js)

        diag = await page.evaluate(_diag_js)

        LOG.debug(f"    弹窗诊断: {diag}")

        if diag.get("has_dialog"):
            empty_fields = diag.get("empty_selects", [])
            if empty_fields:
                LOG.info(f"    检测到 {len(empty_fields)} 个空 select 字段，尝试自动填充: {empty_fields}")
                fill_success, fill_details_2 = await _try_fill_empty_selects(page, empty_fields)
                if fill_success:
                    LOG.info("    空 select 字段填充成功，点击确认按钮")
                    dialog_fill_details.extend(fill_details_2)
                    await page.wait_for_timeout(1000)
                    from core.discovery.replay.button_driver import confirm_dialog
                    confirmed = await confirm_dialog(page)
                    if confirmed:
                        LOG.info(f"    重新点击确认按钮: {confirmed}")
                        await wait_for_loading_complete(page)
                        await page.wait_for_timeout(1000)
                    else:
                        LOG.warning(f"    确认弹窗存在但未找到确认按钮")
                        await _close_dialog(page)
                        return _fail("no_confirm_button", f"{action} 弹窗中未找到确认按钮")
                else:
                    LOG.warning("    空 select 字段填充失败")
                    await _close_dialog(page)
                    return _fail("required_field_empty", f"弹窗中必填字段为空且无法自动填充: {', '.join(empty_fields)}")
            elif diag.get("confirm_disabled"):
                await _close_dialog(page)
                return _fail("confirm_button_disabled", f"确认按钮被禁用，无法执行 {action} 操作")
            else:
                btn_list = [b.get("text", "?") for b in diag.get("buttons", [])]
                await _close_dialog(page)
                return _fail("no_confirm_button", f"{action} 弹窗中未找到可点击的确认按钮（弹窗按钮: {btn_list}）")
        else:
            # 无任何弹窗且即时操作也未成功
            return _fail("no_confirm_button", f"{action} 操作未检测到弹窗或成功信号")


    # 检查确认后的页面状态（是否仍有残留对话框）
    post_state = await page.evaluate("""() => {
        const dialogs = document.querySelectorAll(
            '.el-dialog__wrapper:not([style*="display: none"]), ' +
            '.el-drawer:not([style*="display: none"]), ' +
            '.ant-modal-wrap:not([style*="display: none"])');
        const hasOpenDialog = Array.from(dialogs).some(d => d.offsetWidth > 0);
        const hasForm = document.querySelector(
            '.el-dialog__wrapper:not([style*="display: none"]) form, ' +
            '.el-dialog__wrapper:not([style*="display: none"]) .el-form') !== null;
        return { dialog_remains: hasOpenDialog, has_form_in_dialog: hasForm };
    }""")

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        # 过滤掉 global 类型的成功消息
        filtered_errors = []
        for e in errors:
            if e.get("severity") == "global" and "成功" in e.get("error_text", ""):
                LOG.info(f"    检测到成功消息: {e.get('error_text')}，忽略")
                continue
            filtered_errors.append(e)

        if filtered_errors:
            first = filtered_errors[0]
            return _fail(first.get("severity", "unknown"), first.get("error_text", ""))

    # 读取捕获的消息（操作后可能已消失）
    captured_msgs = await _get_captured_messages(page)

    # [DIAG] 诊断：打印捕获到的所有消息
    LOG.info(f"    [DIAG-captured-msgs] 共捕获 {len(captured_msgs)} 条消息:")
    for i, msg in enumerate(captured_msgs):
        LOG.info(f"      [{i}] text='{msg.get('text', '')[:80]}', type={msg.get('type')}, source={msg.get('source')}")

    # 验证操作是否真的成功（需要正向成功信号）
    # ★ 批五 F6：透传 url_before——row_link/网络配置类跳转操作无消息时
    #   由 shared 判定梯 navigation_url_change 层判成功（有消息永远优先）
    success, detail = await _verify_operation_success(page, action, captured_messages=captured_msgs,
                                                     url_before=url_before)

    # ★ 统一判定逻辑：无论 failure_type 是什么，未检测到成功信号即标记为失败
    # query/detail 操作有独立函数（_do_query/_do_detail），不走此路径，不受影响
    if not success:
        failure_type = detail.get("failure_type", "no_signal")
        failure_reason = detail.get("failure_reason", "未检测到成功信号")
        LOG.warning(f"    [operation_failed] {action} 操作未成功 ({failure_type}): {failure_reason[:80]}")
        # 关闭可能残留的弹窗/抽屉
        await _close_dialog(page)
        return _fail("operation_failed", f"{action} 操作未成功: {failure_reason}")

    # 构建 selectors
    selectors = {"trigger": btn_text}
    if row_selector:
        selectors["row_selector"] = row_selector
    if confirmed:
        selectors["confirm"] = confirmed

    # Playbook 增强字段
    result = {"success": True, "selectors": selectors, "confirmed": confirmed, "trigger_text": trigger_text_normalized, "marker": marker}

    # ★ O6: 记录确认对话框信息
    if confirmed:
        result["confirm_dialog"] = {
            "confirmed": True,
            "button_text": confirmed,
        }

    # 记录弹窗中填充的 select 字段详情（供 playbook 生成 fill_form 步骤）
    if dialog_fill_details:
        result["dialog_fills"] = dialog_fill_details

    # 记录表单字段详情和提交按钮信息（供 playbook 生成 fill_form 和 click_button 步骤）
    if edit_field_details:
        result["edit_field_details"] = edit_field_details
    if submit_result and submit_result.get("locator"):
        result["submit_result"] = submit_result

    # 记录确认后的对话框状态（供 playbook 生成 close_dialog 步骤）
    result["post_confirm_state"] = {
        "dialog_remains": post_state["dialog_remains"],
        "has_form_in_dialog": post_state["has_form_in_dialog"],
    }

    # success 为 True 才会执行到这里（not success 已在上方 return _fail）

    # 记录 trigger_locator_verified（使用 click_page_button 返回的实际 locator）
    if btn_location not in ("dropdown",):
        verified_locator = btn_click_result.get("locator")
        if not verified_locator:
            trigger_tag = btn_click_result.get("tag", "button")
            actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
            verified_locator = f"{trigger_tag}:has-text('{actual_text}')"
        result["trigger_locator_verified"] = verified_locator

    # 记录是否需要先勾选 checkbox（toolbar 按钮需要先选中行）
    # 修复：只有批量操作按钮（非 primary）才需要 checkbox，创建按钮（primary）不需要
    if btn_location == "toolbar":
        btn_class = btn.get("className", "") or ""
        is_primary = "primary" in btn_class.lower()
        if not is_primary:
            # 非 primary 的 toolbar 按钮是批量操作，需要先选中行
            result["needs_checkbox"] = True
            result["checkbox_locator"] = ".el-checkbox__input"

    # 记录是否为 dropdown 操作
    if btn_location == "dropdown":
        result["is_dropdown_operation"] = True
        result["dropdown_parent_selector"] = ".el-dropdown"
        # 使用实际点击的文本（含空格），而非输入参数
        actual_text = dropdown_click_result.get("actual_text", btn_text)
        result["dropdown_item_locator"] = f".el-dropdown-menu__item:has-text('{actual_text}')"
        result["dropdown_item_text"] = actual_text
        result["dropdown_trigger_method"] = "hover"
        result["checkbox_locator"] = ".el-checkbox__input"
        # 记录展开策略（来自 click_row_more_item）
        if dropdown_click_result:
            result["expand_strategy_verified"] = dropdown_click_result.get("expand_strategy", "hover")
            actual_item = dropdown_click_result.get("actual_item_text", btn_text)
            result["dropdown_item_text_verified"] = actual_item

    # 记录确认按钮实际文本（供 playbook 生成精确 locator）
    if confirmed:
        result["confirm_button_text_verified"] = confirmed

    # ★ 提取 radio 字段到操作结果顶层
    if radio_field_details_local:
        result["radio_field_details"] = radio_field_details_local

    return result


async def _do_save_or_confirm(page, context: dict) -> dict:
    """执行保存或确认操作。

    Args:
        page: Playwright Page 对象
        context: 包含 btn 的上下文

    Returns:
        dict: 操作结果
    """
    btn = context["btn"]
    btn_text = btn.get("text", "")

    # 点击保存/确认按钮
    btn_click_result = await click_page_button(page, btn_text)
    if not btn_click_result["clicked"]:
        return click_failed(f"无法点击按钮: {btn_text}")

    await page.wait_for_timeout(1000)
    await wait_for_loading_complete(page)

    # 检查是否有确认弹窗
    from core.discovery.ui_scanner.button_detector import _check_precondition_state
    state = await _check_precondition_state(page, {"type": "dialog"})
    if state["success"]:
        from core.discovery.replay.button_driver import confirm_dialog
        confirmed = await confirm_dialog(page)
        if confirmed:
            LOG.info(f"    已点击确认按钮: {confirmed}")
        await page.wait_for_timeout(1000)

    # 验证成功
    captured_msgs = await _get_captured_messages(page)
    success, _detail = await _verify_operation_success(page, "save", captured_messages=captured_msgs)

    if success:
        return make_success(selectors={"trigger": btn_text}, error_type="", error_text="")
    return no_success_signal("保存/确认操作后未检测到成功信号", selectors={"trigger": btn_text})


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
            # ★ 检查是否为非必填字段：非必填字段验证失败时清空该字段跳过
            fields = context.get("fields", [])
            field_def = next((f for f in fields if f.get("label") == error_field), None)
            is_required = field_def.get("required", True) if field_def else True

            if not is_required:
                # 非必填字段验证失败：清空值跳过
                LOG.info(f"    [apply_fix] 非必填字段 '{error_field}' 验证失败，清空值跳过")
                overrides[error_field] = ""
            elif "已存在" in error_text or "重复" in error_text or "duplicate" in error_text.lower():
                # 唯一性冲突：加时间戳后缀
                # ★ F5: 补救值用最保守字符集（纯小写字母+数字）——云平台类系统
                # 的标识符校验普遍遵循 k8s 规范（小写字母/数字/中划线），下划线
                # 几乎总被拒（如 EIS 名称「由小写字母、数字以及中划线组成」）。
                ts = str(int(time.time()))[-6:]
                overrides[error_field] = f"at{ts}"
            elif "格式" in error_text or "format" in error_text.lower():
                # 格式错误：尝试常见格式
                overrides[error_field] = _guess_format(error_field, error_text)
            elif "不能为空" in error_text or "必填" in error_text or "required" in error_text.lower():
                # 必填字段：填入默认值（★ F5 同上：保守字符集）
                overrides[error_field] = f"auto{str(int(time.time()))[-4:]}"
            else:
                # 其他校验错误：按字段类型生成合理值
                ts_val = str(int(time.time()))[-6:]
                field_lower = error_field.lower()
                if any(kw in field_lower for kw in ("手机", "电话", "phone", "tel", "mobile")):
                    import random as _rand
                    overrides[error_field] = f"138{_rand.randint(10000000, 99999999)}"
                elif any(kw in field_lower for kw in ("邮箱", "email")):
                    overrides[error_field] = f"at{ts_val}@test.com"
                elif any(kw in field_lower for kw in ("密码", "password")):
                    overrides[error_field] = "Test@123456"
                else:
                    # ★ F5: fix{ts}——纯小写字母+数字，对任何标识符/名称类
                    # 校验都安全（fix_404317 的下划线曾被 EIS 校验拒绝）
                    overrides[error_field] = f"fix{ts_val}"

    elif error_type == "api_error":
        # 接口错误：分析错误文本
        if "不能为空" in error_text or "必填" in error_text:
            # 某个字段缺失：尝试填充
            overrides = context.setdefault("fill_overrides", {})
            # 尝试从错误文本中提取字段名
            for label in _extract_field_names(error_text):
                # ★ F5: 补救值保守字符集（纯小写字母+数字，见 form_validation 分支注记）
                overrides[label] = f"auto{str(int(time.time()))[-4:]}"

    elif error_type == "click_failed":
        # 点击失败：已在 click_page_button 中尝试过所有策略
        # 标记为不可恢复
        pass

    elif error_type == "no_dialog":
        # 弹窗未出现：可能需要先执行前置操作
        pass

    return context


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
        from core.discovery.ai_debug_assistant import ai_assisted_analysis

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

    # 名称类标识符保守字符集（与 F5 补救值一致：小写字母+数字）
    return f"at{ts}"


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
