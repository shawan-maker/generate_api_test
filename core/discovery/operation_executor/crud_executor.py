"""
crud_executor.py — CRUD operation execution for Stage 1.

Contains the main discover_and_validate entry point, business flow validation,
error-driven retry logic, and individual CRUD operation executors
(create, query, detail, edit, delete, save/confirm).
"""

import json
import logging
import time
from core.discovery import const
from core.discovery.replay.wait_helpers import (
    wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog,
    wait_for_loading_complete, wait_for_table_ready,
    wait_for_navigation_complete,
)
from core.discovery.replay.form_filler import (
    FormFiller, read_form_errors, generate_fill_data, scan_form_fields,
    generate_fill_rules,
)
from core.discovery.operation_executor.base_executor import (
    _click_button_escalating, _install_message_capture, _get_captured_messages,
    _verify_operation_success, _check_data_changed, _ensure_on_list_page,
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

LOG = logging.getLogger("crud_executor")


async def _get_row_selector_by_marker(page, marker: str) -> str:
    """根据 marker 文本获取所在行的 CSS 选择器。

    Args:
        page: Playwright Page 对象
        marker: 行标识文本

    Returns:
        str: 行的 CSS 选择器，如 ".el-table__body-wrapper .el-table__row:nth-child(1)"
             如果未找到，返回空字符串
    """
    if not marker:
        return ""

    try:
        row_info = await page.evaluate(f"""() => {{
            const rows = document.querySelectorAll('.el-table__body-wrapper .el-table__row');
            for (let i = 0; i < rows.length; i++) {{
                const row = rows[i];
                const cells = row.querySelectorAll('.cell');
                for (const cell of cells) {{
                    const text = cell.textContent.trim();
                    if (text.includes('{marker}')) {{
                        return {{
                            found: true,
                            rowIndex: i,
                            selector: `.el-table__body-wrapper .el-table__row:nth-child(${{i + 1}})`
                        }};
                    }}
                }}
            }}
            return {{ found: false }};
        }}""")

        if row_info.get("found"):
            return row_info.get("selector", "")
        return ""

    except Exception as e:
        LOG.debug(f"  [get-row-selector] 失败: {e}")
        return ""


async def _scan_row_after_operation(page, row_selector: str, marker: str, existing_actions: set) -> list:
    """操作执行后，增量扫描当前行，发现新出现的按钮。

    用于捕获状态依赖的按钮，如：
    - "暂停"操作成功后，"启用"按钮出现
    - "冻结"操作成功后，"解冻"按钮出现

    Args:
        page: Playwright Page 对象
        row_selector: 行的 CSS 选择器
        marker: 行标识（用于验证行仍然存在）
        existing_actions: 已知的操作按钮文本集合（用于去重）

    Returns:
        list: 新发现的按钮列表，格式同 element_scanner 的按钮格式
    """
    try:
        # 1. 验证行仍然存在
        row_exists = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            return row ? true : false;
        }}""")

        if not row_exists:
            LOG.debug(f"  [incremental-scan] 行不存在（可能已删除），跳过扫描")
            return []

        # 2. 扫描当前行的所有操作按钮（直接可见的 .el-button）
        new_buttons = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return [];

            const buttons = [];
            const existingTexts = {list(existing_actions)};

            // 扫描直接可见的按钮（.el-button）
            const directBtns = row.querySelectorAll('.cell .el-button');
            directBtns.forEach(btn => {{
                const text = btn.textContent.trim();
                if (text && !existingTexts.includes(text)) {{
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
            }});

            return buttons;
        }}""")

        # 3. 过滤掉不可见的按钮
        visible_buttons = [
            btn for btn in new_buttons
            if btn.get("rect", {}).get("width", 0) > 0
        ]

        # 4. ★ FIX-2: 扫描 dropdown 菜单（Element UI 将菜单 teleport 到 body）
        #    先 hover 触发展开，再查询 document 级别的菜单项
        dropdown_items = await page.evaluate(f"""() => {{
            const row = document.querySelector('{row_selector}');
            if (!row) return {{ hasDropdown: false }};

            const dropdown = row.querySelector('.el-dropdown');
            if (!dropdown) return {{ hasDropdown: false }};

            // 触发 hover 展开菜单
            dropdown.dispatchEvent(new MouseEvent('mouseenter', {{ bubbles: false }}));
            return {{ hasDropdown: true }};
        }}""")

        if dropdown_items.get("hasDropdown"):
            # 等待菜单动画完成
            await page.wait_for_timeout(600)

            # 在 document.body 下查找可见的 dropdown-menu（teleport 渲染）
            dropdown_buttons = await page.evaluate(f"""() => {{
                const existingTexts = {list(existing_actions)};
                const buttons = [];

                // 查找所有可见的 dropdown-menu（Element UI teleport 到 body）
                const menus = document.querySelectorAll('.el-dropdown-menu');
                for (const menu of menus) {{
                    if (menu.offsetWidth === 0 || menu.offsetHeight === 0) continue;

                    menu.querySelectorAll('.el-dropdown-menu__item').forEach(item => {{
                        const text = item.textContent.trim();
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

                return buttons;
            }}""")

            visible_buttons.extend(dropdown_buttons)

        if visible_buttons:
            LOG.info(f"  [incremental-scan] 发现 {len(visible_buttons)} 个新按钮: "
                     f"{[b['text'] for b in visible_buttons]}")

        return visible_buttons

    except Exception as e:
        LOG.warning(f"  [incremental-scan] 扫描失败: {e}")
        return []


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
    from core.discovery.ui_scanner.element_scanner import discover_all
    ui_result = await discover_all(page)

    # Phase B: 业务闭环验证
    validated = await _validate_business_flow(page, ui_result, username)
    if validated is None:
        # 关键操作验证失败
        return None

    ui_result["validated_operations"] = validated
    return ui_result


async def _validate_single_tab_crud(
    page,
    tab_buttons: dict,
    ui_result: dict,
    username: str,
    tab_name: str = "",
    tab_locator: str = "",
) -> dict:
    """在单个 Tab 上下文中执行完整 CRUD 验证。

    Args:
        page: Playwright Page 对象
        tab_buttons: {action: btn} 字典，该 Tab 下的所有按钮
        ui_result: discover_all() 返回的探测结果
        username: 用户名
        tab_name: Tab 名称（用于日志）

    Returns:
        dict: {action: {"success": bool, "fill_data": dict, "selectors": dict, ...}}
    """
    validated = {}
    form_filler = FormFiller(page)
    framework = ui_result.get("framework", "element-ui")
    buttons_by_action = tab_buttons

    # ★ 使用 shared/constants 统一常量（消除重复定义）
    from core.discovery.shared.constants import (
        DROPDOWN_TRIGGER_TEXTS, NON_BUSINESS_BUTTONS, BATCH_PREFIXES, DESTROY_KEYWORDS
    )
    _DROPDOWN_TRIGGER_TEXTS = DROPDOWN_TRIGGER_TEXTS
    _NON_BUSINESS_BUTTONS = NON_BUSINESS_BUTTONS

    # 收集行级操作文本，用于去重批量操作
    _row_action_texts = {
        btn.get("text", "") for btn in buttons_by_action.values()
        if btn.get("location") in ("row_action", "dropdown")
        and btn.get("text") not in _DROPDOWN_TRIGGER_TEXTS
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

        btn_text = btn.get("text", "")
        btn_location = btn.get("location", "toolbar")
        role = action_roles.get(action, "generic")
        LOG.info(f"{tab_prefix}验证操作: {action} (按钮: {btn_text}, 角色: {role})")

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
                validated[action] = {**(result or make_error("switch_failed", error_text)), "_is_switch": True, "locator": btn.get("locator"), "text": btn.get("text"), "is_checked": btn.get("is_checked")}
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
            else:
                fallback = await _extract_fallback_marker(page)
                if fallback:
                    created_marker = fallback
                    LOG.info(f"{tab_prefix}create 失败，使用 fallback marker: {fallback}")
                if result:
                    validated[action] = result

        elif role == "query":
            LOG.info(f"{tab_prefix}[QUERY-DEBUG] 开始验证 query 操作: btn_text='{btn.get('text')}', is_search_input={btn.get('is_search_input')}, marker={created_marker}")
            if btn.get("is_search_input"):
                result = await _do_query_via_search_input(page, btn, marker=created_marker)
                LOG.info(f"{tab_prefix}[QUERY-DEBUG] search_input 结果: success={result.get('success') if result else None}, error={result.get('error') if result else 'None'}")
                if result and result.get("success"):
                    validated[action] = result
                else:
                    LOG.warning(f"{tab_prefix}[QUERY-DEBUG] query 验证失败（search_input）: {result}")
            else:
                result = await _error_driven_retry(
                    page, _do_query, {"btn": btn, "marker": created_marker}
                )
                LOG.info(f"{tab_prefix}[QUERY-DEBUG] button 结果: success={result.get('success') if result else None}, error={result.get('error') if result else 'None'}")
                if result and result.get("success"):
                    validated[action] = result
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

        elif role == "update":
            if btn_location in ("row_action", "dropdown"):
                if not created_marker:
                    LOG.warning(f"{tab_prefix}{action} 跳过: create 失败且无可用 marker")
                    validated[action] = skipped("create 失败且无可用 marker")
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
                    elif result:
                        validated[action] = result
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
                elif result:
                    validated[action] = result

        elif role == "delete":
            if btn_location == "row_action":
                if not created_marker:
                    LOG.warning(f"{tab_prefix}{action} 跳过: create 失败且无可用 marker")
                    validated[action] = skipped("create 失败且无可用 marker")
                else:
                    result = await _error_driven_retry(
                        page, _do_delete, {
                            "btn": btn, "marker": created_marker,
                        }
                    )
                    if result and result.get("success"):
                        validated[action] = result
                        created_marker = None
                    elif result:
                        validated[action] = result
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
                elif result:
                    validated[action] = result

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
            elif result:
                validated[action] = result

        # ★ 增量发现：操作成功后，扫描当前行发现新出现的按钮
        # 适用场景：暂停→启用、禁用→启用 等状态依赖的操作对
        if result and result.get("success"):
            should_scan = (
                role in ("generic", "update", "delete")  # 状态变更类操作
                and btn_location in ("row_action", "dropdown")  # 行级操作
                and created_marker  # 有明确的行标识
            )

            if should_scan:
                # 获取当前行的选择器（基于 marker）
                row_selector = await _get_row_selector_by_marker(page, created_marker)
                if row_selector:
                    existing_actions = set(buttons_by_action.keys())
                    new_buttons = await _scan_row_after_operation(
                        page, row_selector, created_marker, existing_actions
                    )

                    if new_buttons:
                        LOG.info(f"{tab_prefix}增量发现 {len(new_buttons)} 个新按钮")

                        # 将新按钮加入待验证队列
                        for new_btn in new_buttons:
                            new_btn["tab"] = tab_name  # 标记 tab
                            new_action = new_btn.get("text", "")

                            # 去重检查
                            if new_action in buttons_by_action:
                                continue

                            buttons_by_action[new_action] = new_btn

                            # 推断角色
                            new_role = _infer_action_role(new_action, new_btn, delete_actions=_delete_actions)
                            action_roles[new_action] = new_role

                            # 插入到 ordered_actions 的合适位置
                            # delete 类操作放最后，其他操作放当前位置后面
                            if new_role == "delete":
                                ordered_actions.append(new_action)
                            else:
                                # 在当前 action 后面插入
                                try:
                                    current_idx = ordered_actions.index(action)
                                    ordered_actions.insert(current_idx + 1, new_action)
                                except ValueError:
                                    ordered_actions.append(new_action)

                            LOG.info(f"{tab_prefix}  + {new_action} (角色: {new_role})")

        # 操作后确保回到列表页
        try:
            await _ensure_on_list_page(page, tab_name, tab_locator)
        except Exception as e:
            LOG.error(f"{tab_prefix}_ensure_on_list_page 异常: {e}")
            if "has been closed" in str(e):
                LOG.error(f"{tab_prefix}浏览器已关闭，停止后续操作验证")
                break

    return validated


async def _validate_business_flow(page, ui_result: dict, username: str) -> dict:
    """按动态顺序逐一验证按钮的业务闭环。

    支持多 Tab 页面：检测到多个 tab 时，逐一切换 tab 并独立验证每个 tab 的 CRUD 操作。
    操作顺序：create 优先（创建测试数据），delete 最后（清理），其他操作按发现顺序。
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
        DROPDOWN_TRIGGER_TEXTS, NON_BUSINESS_BUTTONS, BATCH_PREFIXES,
        DESTROY_KEYWORDS, NAVIGATION_CLASS_KEYWORDS
    )
    _DROPDOWN_TRIGGER_TEXTS = DROPDOWN_TRIGGER_TEXTS
    _NON_BUSINESS_BUTTONS = NON_BUSINESS_BUTTONS
    _DESTROY_KEYWORDS = DESTROY_KEYWORDS
    _NAVIGATION_CLASS_KEYWORDS = NAVIGATION_CLASS_KEYWORDS
    _BATCH_PREFIXES = BATCH_PREFIXES

    # 收集行级操作文本，用于去重批量操作
    _row_action_texts = {
        btn.get("text", "") for btn in all_buttons
        if btn.get("location") in ("row_action", "dropdown")
        and btn.get("text") not in _DROPDOWN_TRIGGER_TEXTS
    }
    has_row_destroy = any(
        any(kw in ra_text.lower() for kw in _DESTROY_KEYWORDS)
        for ra_text in _row_action_texts
    )

    # 按 Tab 分组按钮，过滤非业务按钮
    buttons_by_tab = {}  # {tab_name: {action: btn}}

    for btn in all_buttons:
        # 跳过下拉菜单触发器
        if btn.get("text") in _DROPDOWN_TRIGGER_TEXTS and btn.get("tag") != "DROPDOWN_ITEM":
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

        # 去重：如果 toolbar 按钮是"批量 X"，且有对应的行级操作"X"，则跳过
        if btn.get("location") == "toolbar":
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

    # 按 Tab 独立验证
    validated = {}
    tabs_info = page_structure.get("tabs", [])

    for tab_name, tab_buttons in buttons_by_tab.items():
        # 切换到该 Tab（如果有多个 Tab）
        if tab_name and len(tabs_info) > 1:
            tab_info = next((t for t in tabs_info if t.get("name") == tab_name), None)
            if tab_info and tab_info.get("locator"):
                LOG.info(f"  📑 切换到 Tab: {tab_name}")
                try:
                    await page.click(tab_info["locator"], timeout=5000)
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
        tab_result = await _validate_single_tab_crud(
            page, tab_buttons, ui_result, username, tab_name,
            tab_locator=tab_info.get("locator", "") if tab_name and tab_info else "",
        )

        # 合并结果（多 Tab 时加前缀）
        for action, result in tab_result.items():
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
        return validated

    LOG.info(f"  Stage 1 验证完成: {len(validated)} 个操作通过")
    return validated




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
    max_rounds = 10  # 安全上限

    for round_num in range(max_rounds):
        try:
            result = await operation_fn(page, context)
        except Exception as e:
            LOG.warning(f"    操作异常: {e}")
            result = exception(str(e))

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
    result["submit_locator"] = submit_locator
    result["submit_locator_verified"] = submit_locator
    result["submit_text"] = submit_text_normalized
    result["submit_click_strategy"] = submit_result.get("click_strategy", "playwright")
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

    btn_click_result = await _click_button_escalating(page, btn_text)
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
    if not fields:
        # 无表单字段的特殊情况：可能是简单确认对话框（如 AccessKey 创建）
        # 尝试直接点击确认按钮
        from core.discovery.replay.button_driver import confirm_dialog
        confirmed = await confirm_dialog(page)
        if confirmed:
            await page.wait_for_timeout(1500)
            captured_msgs = await _get_captured_messages(page)
            success = await _verify_operation_success(page, "create", captured_messages=captured_msgs)
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
                "fill_rule": field.get("fill_rule", {}),
                "is_editable": False,
                "option_text": "",
            }
            # ★ ip-input: 显式标记为"复合输入字段"（使用 sub_selectors，不需要 locator）
            if kb_cat == "ip-input":
                entry["requires_locator"] = False  # 明确标记不需要 playwright_locator
                entry["sub_selectors"] = field.get("subSelectors", [])
                entry["input_count"] = field.get("inputCount", 4)
                entry["ip_version"] = field.get("ipVersion", "v4")
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
        if detail.get("skipped_reason"):
            # 从 multi_step_field_details 中移除跳过的字段
            multi_step_field_details = [
                f for f in multi_step_field_details if f["label"] != label
            ]
            continue
        for ms_field in multi_step_field_details:
            if ms_field["label"] == label:
                ms_field["is_editable"] = detail.get("is_editable", False)
                ms_field["option_text"] = detail.get("option_text", "")
                # ★ Phase 4a: 传递 el-select 子类型
                if "select_sub_type" in detail:
                    ms_field["select_sub_type"] = detail["select_sub_type"]
                break

    # ✅ P6: 检查必填多步字段是否填充成功
    required_failed = []
    for detail in ms_details:
        label = detail.get("label", "")
        # 查找对应的字段定义，检查是否必填
        field_def = next((f for f in fields if f.get("label") == label), None)
        is_required = field_def and field_def.get("required", False)
        # 检查是否填充成功（is_editable 或 option_text，且没有 skipped_reason）
        is_success = (detail.get("is_editable", False) or detail.get("option_text", "")) \
                     and not detail.get("skipped_reason")
        if is_required and not is_success:
            required_failed.append(label)

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
    submit_result = await form_filler.submit_form_v2()
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
            return form_validation(first_err.get("error_text", ""),
                                  error_field=first_err.get("field_label", ""),
                                  trigger_text=btn_text,
                                  trigger_locator_verified=btn_click_result.get("locator"))
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
    success = await _verify_operation_success(page, "create", captured_messages=captured_msgs)
    if not success:
        # 二次检测：提取明确的业务失败信息
        failure_info = await _extract_business_failure(page)
        if failure_info:
            # 业务失败但表单已提交，保留表单数据供 playbook 生成完整步骤
            return _build_create_error_result(
                "api_error", failure_info["error_text"], btn_text, btn_click_result,
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

    # 确保回到列表页
    await _ensure_on_list_page(page, context.get("tab_name", ""), context.get("tab_locator", ""))

    # 使用 submit_form_v2 返回的已验证 locator（Stage 1 探测成功的那个）
    submit_text = submit_result["text"]
    submit_text_normalized = " ".join(submit_text.split())
    submit_locator = submit_result["locator"]
    trigger_text_normalized = " ".join(btn_text.split())

    # 使用 _click_button_escalating 返回的实际 locator（已验证）
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
            # 使用实际选择的值（从 fill_result.radio_selections 获取）
            actual_option_text = radio_selections.get(field_label, "")
            radio_field_details.append({
                "label": field_label,
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": actual_option_text,  # ★ 使用实际选择的值
                "fill_rule": field.get("fill_rule", {}),
            })

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
        "submit_locator": submit_locator,
        "submit_locator_verified": submit_locator,  # Phase 1.1: 已验证的 locator
        "submit_text": submit_text_normalized,
        "submit_click_strategy": submit_result.get("click_strategy", "playwright"),
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
    btn_click_result = await _click_button_escalating(page, btn_text)
    if not btn_click_result["clicked"]:
        return click_failed(f"无法点击搜索按钮: {btn_text}")

    await wait_for_loading_complete(page)
    await page.wait_for_timeout(1000)

    # 验证列表刷新（简单检查：表格仍存在）
    has_table = await page.evaluate(
        "() => !!document.querySelector('.el-table__body-wrapper tbody tr')"
    )

    trigger_text_normalized = " ".join(btn_text.split())
    # 使用 _click_button_escalating 返回的已验证 locator（不重建）
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
        btn_click_result = await _click_button_escalating(page, btn_text)
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
        # ★ 导航后恢复 Tab
        _tab_name = context.get("tab_name", "")
        _tab_locator = context.get("tab_locator", "")
        if _tab_name and _tab_locator:
            from core.discovery.operation_executor.base_executor import _restore_active_tab
            await _restore_active_tab(page, _tab_name, _tab_locator)
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
        else:
            _log.info(f"    [EDIT-DEBUG] 使用 toolbar 按钮点击方式")
            btn_click_result = await _click_button_escalating(page, btn_text)
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
                        trigger_locator_verified=btn_click_result.get("locator"))

    # ── 弹窗编辑模式 (obs["scenario"] == "dialog") ──
    # 扫描字段并修改
    fields = await form_filler.scan_form_fields_v2()
    modifications = context.get("edit_overrides")
    filled = await form_filler.fill_edit_form(fields, modifications)
    if filled == 0:
        await _close_dialog(page)
        return make_error("no_editable_fields", "未找到可修改的字段",
                         trigger_text=btn_text,
                         trigger_locator_verified=btn_click_result.get("locator"))

    # 提交
    submit_result = await form_filler.submit_form_v2()
    if not submit_result.get("locator"):
        await _close_dialog(page)
        return submit_failed("未找到提交按钮",
                            trigger_text=btn_text,
                            trigger_locator_verified=btn_click_result.get("locator"))

    await page.wait_for_timeout(2000)

    # 检查错误
    errors = await read_form_errors(page)
    if errors:
        field_errors = [e for e in errors if e.get("severity") == "field"]
        if field_errors:
            return form_validation(field_errors[0].get("error_text", ""),
                                  error_field=field_errors[0].get("field_label", ""),
                                  trigger_text=btn_text,
                                  trigger_locator_verified=btn_click_result.get("locator"))

    captured_msgs = await _get_captured_messages(page)
    success = await _verify_operation_success(page, "update", captured_messages=captured_msgs)
    if not success:
        await _close_dialog(page)
        return no_success_signal("编辑提交后未检测到成功信号",
                                trigger_text=btn_text,
                                trigger_locator_verified=btn_click_result.get("locator"))

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

    # 使用 _click_button_escalating 返回的实际 locator（已验证）
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
        "submit_locator_verified": submit_locator,
        "submit_text": submit_text_normalized,
        "interaction_mode": "dialog" if dialog_detected else "page-nav",
        "dialog_locator": ".el-dialog__wrapper",
        "success_locator": ".el-message--success",
        "multi_step_field_details": multi_step_field_details,
        "radio_field_details": radio_field_details,
        "cascade_actions": [],  # 编辑操作不收集 cascade（fill_edit_form 返回 int）
    }


async def _do_toggle_switch(page, context: dict) -> dict:
    """执行 switch 切换操作：点击 switch → 处理确认弹框 → 验证状态变化。

    Switch 操作被视为独立的业务操作，记录到 validated_operations 中。

    Args:
        page: Playwright Page 对象
        context: 包含 switch_info 字典

    Returns:
        dict: 操作结果，包含 success, before_state, after_state, trigger_text 等
    """
    switch_info = context["switch_info"]
    sw_text = switch_info.get("text", "Switch")
    sw_locator = switch_info.get("locator", ".el-switch")
    before_checked = switch_info.get("is_checked", False)

    LOG.info(f"    [switch] 切换 '{sw_text}': {'ON' if before_checked else 'OFF'} → {'OFF' if before_checked else 'ON'}")

    try:
        # 1. 记录点击前的 URL（用于检测页面跳转）
        url_before = page.url

        # ★ 综合检测 switch 状态（多信号源）
        async def _get_switch_state(locator):
            """综合检测 switch 状态，返回 (isChecked, detail_dict)"""
            result = await page.evaluate(f"""() => {{
                const candidates = document.querySelectorAll('{locator}');
                const all = [];
                const filtered = [];

                candidates.forEach((sw, idx) => {{
                    const row = sw.closest('.el-table__row, .el-table__body tr, tr.el-table__row');
                    const pane = sw.closest('.el-tab-pane');
                    const paneInfo = pane ? {{
                        paneId: pane.id,
                        display: window.getComputedStyle(pane).display,
                        visibility: window.getComputedStyle(pane).visibility,
                        offsetWidth: pane.offsetWidth,
                        offsetHeight: pane.offsetHeight,
                    }} : null;

                    const isRow = !!row;
                    const isHiddenPane = pane ? (paneInfo.display === 'none' || pane.offsetWidth === 0) : false;
                    const keep = !isRow && !isHiddenPane;

                    // 综合检测 isChecked 状态（多信号源）
                    const vue = sw.__vue__;
                    const selfChecked = sw.classList.contains('is-checked');
                    const coreChecked = sw.querySelector('.el-switch__core')?.classList.contains('is-checked') || false;
                    const actionChecked = sw.querySelector('.el-switch__action')?.classList.contains('is-checked') || false;
                    const ariaChecked = sw.getAttribute('aria-checked');
                    let vueChecked = null;
                    if (vue) {{
                        const val = vue.value;
                        const activeVal = vue.activeValue !== undefined ? vue.activeValue : true;
                        vueChecked = (val === activeVal);
                    }}

                    // 综合判断：任一信号为 true 则视为 checked
                    const isChecked = selfChecked || coreChecked || actionChecked || vueChecked || ariaChecked === 'true';

                    all.push({{
                        idx, isRow, keep,
                        paneId: paneInfo?.paneId || 'none',
                        isHiddenPane,
                        selfChecked, coreChecked, actionChecked, vueChecked, ariaChecked,
                        isChecked,
                        vueValue: vue?.value,
                        vueActiveValue: vue?.activeValue,
                    }});

                    if (keep) {{
                        filtered.push(sw);
                    }}
                }});

                return {{ all, filteredCount: filtered.length }};
            }}""")

            if not result or result.get("filteredCount", 0) == 0:
                return False, result

            # 取第一个 keep=True 的 switch 状态
            for item in result.get("all", []):
                if item.get("keep"):
                    return item.get("isChecked", False), result

            return False, result

        diag_before = None
        is_checked_before, diag_before = await _get_switch_state(sw_locator)
        LOG.info(f"    [switch] 点击前诊断: checked={is_checked_before}, detail={diag_before.get('all', []) if diag_before else []}")

        # 2. 点击 switch
        clicked = False
        try:
            # ★ 使用 JS 点击，排除表格行内和不可见 tab-pane 的 switch
            click_result = await page.evaluate(f"""() => {{
                // 查找所有匹配的 switch
                const candidates = document.querySelectorAll('{sw_locator}');
                // 过滤：排除行级 switch + 排除不可见 tab-pane 的 switch
                const toolbarSwitches = Array.from(candidates).filter(sw => {{
                    // 排除行级 switch
                    if (sw.closest('.el-table__row, .el-table__body tr, tr.el-table__row')) return false;
                    // ★ 排除不在当前可见 tab-pane 内的 switch
                    const pane = sw.closest('.el-tab-pane');
                    if (pane) {{
                        const style = window.getComputedStyle(pane);
                        if (style.display === 'none' || pane.offsetWidth === 0) return false;
                    }}
                    return true;
                }});

                if (toolbarSwitches.length === 0) {{
                    return {{ clicked: false, reason: 'no_toolbar_switch' }};
                }}

                // 点击第一个 toolbar 级的 switch
                const sw = toolbarSwitches[0];
                sw.click();
                return {{ clicked: true, total: candidates.length, filtered: toolbarSwitches.length }};
            }}""")

            if click_result.get("clicked"):
                clicked = True
                LOG.debug(f"    [switch] 点击成功: {sw_locator} "
                          f"(toolbar级, total={click_result.get('total')}, "
                          f"filtered={click_result.get('filtered')})")

                # ★ 记录点击后的状态（立即检测）
                await page.wait_for_timeout(100)  # 等待 Vue 响应
                is_checked_after_click, _ = await _get_switch_state(sw_locator)
                LOG.info(f"    [switch] 点击后即时状态: checked={is_checked_after_click}")
            else:
                reason = click_result.get("reason", "unknown")
                LOG.warning(f"    [switch] 点击失败: {reason}")
                return make_error("switch_click_failed", f"无法点击 toolbar switch: {sw_text} ({reason})",
                                trigger_text=sw_text)
        except Exception as e:
            LOG.warning(f"    [switch] 点击失败: {e}")
            return make_error("switch_click_failed", f"无法点击 switch: {sw_text}",
                            trigger_text=sw_text)

        if not clicked:
            return click_failed(f"无法点击 switch: {sw_text}", trigger_text=sw_text)

        # 3. 等待可能的确认弹框
        await page.wait_for_timeout(500)

        # 检查是否出现确认弹框（el-message-box 或 el-dialog）
        confirm_handled = False
        try:
            # ★ 增强诊断：记录弹框详细状态
            box_diag = await page.evaluate("""() => {
                const wrappers = document.querySelectorAll('.el-message-box__wrapper');
                const result = [];
                wrappers.forEach((w, i) => {
                    const style = w.getAttribute('style') || '';
                    const display = window.getComputedStyle(w).display;
                    const visible = w.offsetWidth > 0 && w.offsetHeight > 0;
                    const title = w.querySelector('.el-message-box__title')?.textContent?.trim() || '';
                    const message = w.querySelector('.el-message-box__message')?.textContent?.trim()?.substring(0, 50) || '';
                    const btns = Array.from(w.querySelectorAll('.el-message-box__btns button')).map(b => ({
                        text: b.textContent.trim(),
                        disabled: b.disabled,
                        primary: b.classList.contains('el-button--primary')
                    }));
                    result.push({
                        index: i,
                        display,
                        visible,
                        styleDisplay: style.includes('display: none'),
                        title,
                        message,
                        buttons: btns
                    });
                });
                return result;
            }""")
            if box_diag:
                for bd in box_diag:
                    # ★ 使用 INFO 级别输出弹框消息内容（便于调试）
                    LOG.info(f"    [switch] 弹框[{bd.get('index')}]: "
                             f"visible={bd.get('visible')}, "
                             f"title='{bd.get('title')}', "
                             f"message='{bd.get('message')}'")

            # 查找可见的确认弹框
            confirm_box = await page.query_selector('.el-message-box__wrapper:not([style*="display: none"])')
            if not confirm_box:
                # 备选：检查是否有可见的弹框（通过尺寸判断）
                for bd in (box_diag or []):
                    if bd.get("visible"):
                        LOG.debug(f"    [switch] 备选弹框检测: 使用 visible box[{bd.get('index')}]")
                        confirm_box = True  # 标记有弹框
                        break

            if confirm_box:
                # ★ 获取弹框消息内容，检查是否是 switch 的确认弹框
                box_message = await page.evaluate("""() => {
                    const box = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
                    if (!box) return '';
                    const msg = box.querySelector('.el-message-box__message');
                    return msg ? msg.textContent.trim() : '';
                }""")

                LOG.info(f"    [switch] 检测到确认弹框，消息: '{box_message}'")

                # ★ 检查弹框消息是否包含 switch 相关关键词
                switch_keywords = ["开关", "switch", "状态", sw_text]
                is_switch_confirm = any(kw in box_message.lower() for kw in [k.lower() for k in switch_keywords])

                if not is_switch_confirm and box_message:
                    LOG.warning(f"    [switch] ⚠️ 弹框消息不包含 switch 关键词 {switch_keywords}，可能是其他操作的弹框")

                # 点击确认按钮（优先 primary 按钮）
                confirm_btn = await page.query_selector('.el-message-box__btns .el-button--primary')
                if confirm_btn:
                    await confirm_btn.click()
                    LOG.info(f"    [switch] 已点击确认按钮 (primary)")

                    # ★ 等待弹框真正消失（轮询，而非固定等待）
                    for _wait in range(10):  # 最多 3 秒
                        box_still_visible = await page.evaluate("""() => {
                            const boxes = document.querySelectorAll('.el-message-box__wrapper');
                            for (const b of boxes) {
                                if (b.offsetWidth > 0 && b.offsetHeight > 0
                                    && !b.getAttribute('style')?.includes('display: none')) {
                                    return true;
                                }
                            }
                            return false;
                        }""")
                        if not box_still_visible:
                            LOG.debug(f"    [switch] 弹框已消失 (等待 {_wait * 300}ms)")
                            break
                        await page.wait_for_timeout(300)
                    else:
                        LOG.warning(f"    [switch] 弹框 3 秒后仍未消失")

                    confirm_handled = True
                else:
                    # 尝试其他确认按钮
                    for btn_selector in ['.el-message-box__btns button:last-child',
                                         '.el-message-box__btns button:first-child']:
                        btn = await page.query_selector(btn_selector)
                        if btn and await btn.is_visible():
                            btn_text_diag = await btn.text_content()
                            LOG.debug(f"    [switch] 备选确认按钮: '{btn_text_diag}'")
                            await btn.click()
                            # 等待弹框消失
                            for _wait in range(10):
                                box_still_visible = await page.evaluate("""() => {
                                    const boxes = document.querySelectorAll('.el-message-box__wrapper');
                                    for (const b of boxes) {
                                        if (b.offsetWidth > 0 && b.offsetHeight > 0) return true;
                                    }
                                    return false;
                                }""")
                                if not box_still_visible:
                                    break
                                await page.wait_for_timeout(300)
                            confirm_handled = True
                            LOG.info(f"    [switch] 已点击确认按钮 (fallback)")
                            break
        except Exception as e:
            LOG.debug(f"    [switch] 确认弹框处理异常: {e}")

        # 4. ★ 轮询等待 switch 状态变化（最多 5 秒）
        # 使用综合检测方法，替代原来的单一 classList 检查
        status_changed = False
        after_checked = is_checked_before

        for attempt in range(10):  # 10 × 500ms = 5 秒
            current_checked, diag_poll = await _get_switch_state(sw_locator)

            # 检查 switch 是否还存在
            if diag_poll and diag_poll.get("filteredCount", 0) == 0:
                LOG.debug(f"    [switch] 轮询[{attempt}]: switch 元素未找到")
                await page.wait_for_timeout(500)
                continue

            if current_checked != is_checked_before:
                status_changed = True
                after_checked = current_checked
                # ★ 状态变化时使用 INFO 级别
                LOG.info(f"    [switch] ✓ 状态已变化: {'ON' if is_checked_before else 'OFF'} → "
                         f"{'ON' if current_checked else 'OFF'} (等待 {(attempt + 1) * 500}ms)")
                break
            else:
                # ★ 每次轮询都输出详细日志（INFO 级别）
                LOG.info(f"    [switch] 轮询 [{attempt+1}/10]: 状态未变 ({'ON' if current_checked else 'OFF'}), "
                         f"已等待 {(attempt + 1) * 500}ms")

            await page.wait_for_timeout(500)

        # 5. 验证状态变化
        if is_checked_before == after_checked:
            LOG.warning(f"    [switch] ❌ 5 秒后状态仍未变化")

            # ★ 输出详细诊断信息
            box_still_exists = await page.evaluate("""() => {
                const boxes = document.querySelectorAll('.el-message-box__wrapper');
                const visible = Array.from(boxes).filter(b => b.offsetWidth > 0 && b.offsetHeight > 0);
                return visible.length;
            }""")
            LOG.warning(f"    [switch] 诊断: 仍有 {box_still_exists} 个可见弹框")

            # 检查是否有错误消息
            error_msgs = await page.evaluate("""() => {
                const msgs = document.querySelectorAll('.el-message--error, .el-notification--error');
                return Array.from(msgs).map(m => m.textContent.trim()).filter(t => t);
            }""")
            if error_msgs:
                LOG.warning(f"    [switch] 诊断: 发现错误消息: {error_msgs}")

            return make_error("switch_state_unchanged",
                            f"switch 状态未变化: {sw_text}",
                            trigger_text=sw_text)

        # 7. 检查成功消息（可选）
        captured_msgs = await _get_captured_messages(page)
        success_msg = None
        for msg in captured_msgs:
            msg_text = msg.get("text", "")
            if "成功" in msg_text or "success" in msg_text.lower():
                success_msg = msg_text
                break

        # 8. 构建成功结果
        before_state_str = "ON" if is_checked_before else "OFF"
        after_state_str = "ON" if after_checked else "OFF"

        LOG.info(f"    [switch] 切换成功: {before_state_str} → {after_state_str}"
                + (f" (消息: {success_msg})" if success_msg else ""))

        return {
            "success": True,
            "before_state": before_state_str,
            "after_state": after_state_str,
            "trigger_text": sw_text,
            "selectors": {"trigger": sw_locator},
            "trigger_locator_verified": sw_locator,
            "confirm_handled": confirm_handled,
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
            btn_click_result = await _click_button_escalating(page, btn_text)
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
    success = await _verify_operation_success(page, "delete", captured_messages=captured_msgs)

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
    # 使用 _click_button_escalating 返回的实际 locator（已验证）
    trigger_locator_verified = btn_click_result.get("locator")
    if not trigger_locator_verified:
        trigger_tag = btn_click_result.get("tag", "button")
        actual_text = btn_click_result.get("actual_text", trigger_text_normalized)
        trigger_locator_verified = f"{trigger_tag}:has-text('{actual_text}')"

    # 如果是 toolbar 删除，标记需要先勾选 checkbox
    needs_checkbox = btn_location == "toolbar"
    checkbox_locator = ".el-checkbox__input" if needs_checkbox else None

    result = {
        "success": True,
        "selectors": selectors,
        "confirmed": confirmed,
        "is_delete": True,
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
    } if success else no_success_signal("删除后未检测到成功信号",
                                        trigger_text=trigger_text_normalized)

    return result


async def _do_generic_operation(page, context: dict) -> dict:
    """执行通用操作（lock/unlock/reset/authorize 等）。"""
    # Lazy imports to avoid circular dependencies
    from core.discovery.operation_executor.base_executor import (
        _install_message_capture, _ensure_row_selected, _click_button_escalating,
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
    submit_result = {}

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

    # 找到行并点击
    row_selector = None
    btn_click_result = {}  # 默认初始化，row_action/dropdown 分支不设置此变量
    if btn_location == "row_action" and marker:
        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return row_not_found(f"未找到数据行: {marker}",
                                trigger_text=trigger_text_normalized,
                                trigger_locator_verified=None)
        clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    elif btn_location == "dropdown":
        # 两步点击：先找到行 → 展开"更多" → 点击子项
        if not marker:
            LOG.warning(f"  {action} 跳过: dropdown 操作需要 marker")
            return skipped("dropdown 操作需要 marker",
                          trigger_text=trigger_text_normalized,
                          trigger_locator_verified=None)

        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return row_not_found(f"未找到数据行: {marker}",
                                trigger_text=trigger_text_normalized,
                                trigger_locator_verified=None)
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
        btn_click_result = await _click_button_escalating(page, btn_text)
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
        return click_failed(f"无法点击 {action} 按钮: {btn_text}",
                           trigger_text=trigger_text_normalized,
                           trigger_locator_verified=btn_click_result.get("locator") if btn_click_result else None)

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
            }
        else:
            error_type = nav_info.get("error_type", "cross_page_no_submit")
            error_text = nav_info.get("error_text", "新标签页操作未完成")
            return make_error(error_type, error_text,
                             trigger_text=trigger_text_normalized,
                             nav_info=nav_info,
                             selectors=selectors)

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
                    "submit_result": submit_result
                }
            else:
                # 提交失败
                return make_error("cross_page_submit_failed",
                                 f"跨页面操作提交失败: {submit_result.get('button_text', 'unknown')}",
                                 nav_info=nav_info,
                                 selectors=selectors,
                                 submit_result=submit_result)
        else:
            # 没有执行提交操作，检查是否有错误
            error_type = nav_info.get("error_type", "")
            error_text = nav_info.get("error_text", "")

            if error_type:
                # 有明确错误
                return make_error(error_type, error_text,
                                 trigger_text=trigger_text_normalized,
                                 nav_info=nav_info,
                                 selectors=selectors)
            else:
                # 没有错误但没有提交操作（可能是页面没有表单或关键按钮）
                return make_error("cross_page_no_submit", "跨页面操作后未执行提交操作",
                                 trigger_text=trigger_text_normalized,
                                 nav_info=nav_info,
                                 selectors=selectors)

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
        return make_error("env_dependency", "文件上传操作无法自动完成",
                         trigger_text=trigger_text_normalized,
                         selectors=selectors)

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
                LOG.info(f"    已点击确认按钮: {confirmed}")
            else:
                LOG.warning(f"    {actual_state} 中未找到确认按钮")
                await _close_dialog(page)
                return make_error("no_confirm_button", f"{action} {actual_state} 中未找到确认按钮",
                                 trigger_text=trigger_text_normalized)
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
            for field in fields:
                input_type = field.get("inputType", "text")
                # 收集 input 类型字段（包括 number、text 等）
                if field.get("type") == "input" and field.get("selector"):
                    detail = {
                        "label": field.get("label"),
                        "selector": field.get("selector"),
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
                    edit_field_details.append(detail)

            if filled == 0:
                LOG.info(f"    无可填字段，尝试直接提交")

            submit_result = await form_filler.submit_form_v2(scope_selector=_sel_q)
            LOG.debug(f"    submit_form_v2 返回: {submit_result}")
            if not submit_result.get("locator"):
                LOG.info(f"    submit_form_v2 未找到提交按钮，回退到 confirm_dialog")
                from core.discovery.replay.button_driver import confirm_dialog
                LOG.info(f"    [DIAG-generic-path2] submit_form_v2 回退，调用 confirm_dialog...")
                confirmed = await confirm_dialog(page)
                LOG.info(f"    [DIAG-generic-path2] confirm_dialog 返回: '{confirmed}'")
                if not confirmed:
                    await _close_dialog(page)
                    return make_error("no_confirm_button", f"{action} {actual_state} 中未找到提交/确认按钮",
                                     trigger_text=trigger_text_normalized)
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
                    return form_validation(field_errors[0].get("error_text", ""),
                                          error_field=field_errors[0].get("field_label", ""),
                                          trigger_text=trigger_text_normalized)
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
                return make_error("no_confirm_button", f"{action} {actual_state} 中未找到确认按钮",
                                 trigger_text=trigger_text_normalized)
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
                    if not chain_submit.get("locator"):
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
            _instant_success = await _verify_operation_success(
                page, action, strict=True, captured_messages=_instant_msgs)
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
                        return make_error("operation_failed",
                                        f"{action} 操作失败: {m.get('text', '')}",
                                        trigger_text=trigger_text_normalized)

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
                        return make_error("no_confirm_button", f"{action} 弹窗中未找到确认按钮",
                                         trigger_text=trigger_text_normalized)
                else:
                    LOG.warning("    空 select 字段填充失败")
                    await _close_dialog(page)
                    return make_error("required_field_empty", f"弹窗中必填字段为空且无法自动填充: {', '.join(empty_fields)}",
                                     trigger_text=trigger_text_normalized)
            elif diag.get("confirm_disabled"):
                await _close_dialog(page)
                return make_error("confirm_button_disabled", f"确认按钮被禁用，无法执行 {action} 操作",
                                 trigger_text=trigger_text_normalized)
            else:
                btn_list = [b.get("text", "?") for b in diag.get("buttons", [])]
                await _close_dialog(page)
                return make_error("no_confirm_button", f"{action} 弹窗中未找到可点击的确认按钮（弹窗按钮: {btn_list}）",
                                 trigger_text=trigger_text_normalized)
        else:
            # 无任何弹窗且即时操作也未成功
            return make_error("no_confirm_button", f"{action} 操作未检测到弹窗或成功信号",
                             trigger_text=trigger_text_normalized)


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
            return make_error(first.get("severity", "unknown"), first.get("error_text", ""),
                             trigger_text=trigger_text_normalized)

    # 读取捕获的消息（操作后可能已消失）
    captured_msgs = await _get_captured_messages(page)

    # [DIAG] 诊断：打印捕获到的所有消息
    LOG.info(f"    [DIAG-captured-msgs] 共捕获 {len(captured_msgs)} 条消息:")
    for i, msg in enumerate(captured_msgs):
        LOG.info(f"      [{i}] text='{msg.get('text', '')[:80]}', type={msg.get('type')}, source={msg.get('source')}")

    # 验证操作是否真的成功（严格模式：需要正向成功信号）
    success = await _verify_operation_success(page, action, strict=True, captured_messages=captured_msgs)
    if not success:
        # 失败前关闭可能残留的弹窗/抽屉，避免遮挡后续重试
        await _close_dialog(page)
        return no_success_signal(f"{action} 操作后未检测到成功信号（成功提示/数据变化）",
                                trigger_text=trigger_text_normalized)

    # 构建 selectors
    selectors = {"trigger": btn_text}
    if row_selector:
        selectors["row_selector"] = row_selector
    if confirmed:
        selectors["confirm"] = confirmed

    # Playbook 增强字段
    result = {"success": True, "selectors": selectors, "confirmed": confirmed, "trigger_text": trigger_text_normalized, "marker": marker}

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

    # 记录 trigger_locator_verified（使用 _click_button_escalating 返回的实际 locator）
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
    btn_click_result = await _click_button_escalating(page, btn_text)
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
    success = await _verify_operation_success(page, "save", strict=True, captured_messages=captured_msgs)

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

    return f"AT_{ts}"


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
