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
    _check_create_api_triggered_simple,
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


async def _validate_business_flow(page, ui_result: dict, username: str) -> dict:
    """按动态顺序逐一验证按钮的业务闭环。

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
    _NON_BUSINESS_BUTTONS = {"GO", "Go", "go"}  # 分页跳转等非业务按钮

    # 收集行级操作文本，用于去重批量操作
    _row_action_texts = {
        btn.get("text", "") for btn in all_buttons
        if btn.get("location") in ("row_action", "dropdown")
        and btn.get("text") not in _DROPDOWN_TRIGGER_TEXTS  # 排除下拉触发器
    }
    _BATCH_PREFIXES = ["批量", "batch", "bulk"]
    _DESTROY_KEYWORDS = ["删除", "delete", "remove", "清空", "clear"]

    # 检查是否有行级删除操作（用于语义级去重）
    has_row_destroy = any(
        any(kw in ra_text.lower() for kw in _DESTROY_KEYWORDS)
        for ra_text in _row_action_texts
    )

    # 非业务元素的 className 关键词（面包屑、分页等 DOM 噪音）
    _NAVIGATION_CLASS_KEYWORDS = {"breadcrumb", "pagination"}

    for btn in all_buttons:
        # 跳过下拉菜单触发器（如"更多"），它们只是展开菜单，不是实际操作
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

        # 使用按钮文本作为 action（不再调用 _match_crud）
        action = btn.get("action", "") or btn.get("text", "")
        if action:
            existing = buttons_by_action.get(action)
            # 优先选择 BUTTON 元素，避免被 DIV 包装器覆盖
            # 扫描顺序可能先遇到 DIV.btn-group-left，后遇到真正的 BUTTON.el-button--primary
            if not existing or (existing.get("tag") != "BUTTON" and btn.get("tag") == "BUTTON"):
                buttons_by_action[action] = btn

    # 自动推断 query 操作：如果没有 query 按钮但探测到搜索输入框
    if "query" not in buttons_by_action and ui_result.get("search_inputs"):
        search_input = ui_result["search_inputs"][0]  # 使用得分最高的搜索框
        LOG.info(f"  自动推断 query 操作: 使用搜索输入框 '{search_input.get('placeholder', '')}'")
        # 创建合成的 query 按钮记录
        buttons_by_action["query"] = {
            "text": f"搜索:{search_input.get('placeholder', '输入框')}",
            "action": "query",
            "tag": "INPUT",
            "location": "toolbar",
            "is_search_input": True,
            "search_input": search_input,
        }

    # 表单字段（从探测结果获取）
    form_fields = ui_result.get("form_fields", [])

    # 记录创建的数据标识（供后续操作复用）
    created_marker = None

    # 动态确定操作顺序：
    # 结构性判断哪些操作需要先有数据（编辑/删除类需 marker），哪些不需要
    # create 优先 → query 紧随其后（验证搜索功能）→ 其他操作按发现顺序 → delete 最后
    _create_action, _delete_actions = _find_create_delete_actions(buttons_by_action)
    _query_action = "query" if "query" in buttons_by_action else None

    # marker fallback: 无 create 操作但表格有数据时，从第一行提取标识供 row_action 使用
    _pre_marker = None
    if not _create_action:
        try:
            _pre_marker = await page.evaluate("""() => {
                // 从主表格（非 fixed 列）第一行提取第一个有意义的文本
                const rows = document.querySelectorAll(
                    '.el-table__body-wrapper .el-table__row');
                if (rows.length === 0) return null;
                const cells = rows[0].querySelectorAll('.cell');
                for (const cell of cells) {
                    const text = cell.textContent.trim();
                    // 跳过空值、纯数字、过短/过长的文本
                    if (!text || text.length < 2 || text.length > 50) continue;
                    if (/^\\d+$/.test(text)) continue;
                    // 跳过 checkbox/icon 列
                    if (cell.querySelector('.el-checkbox, .el-icon, i[class*="icon"]')) continue;
                    return text;
                }
                return null;
            }""")
            if _pre_marker:
                created_marker = _pre_marker
                LOG.info(f"  无 create 操作，从表格首行提取 marker: '{_pre_marker}'")
        except Exception as e:
            LOG.debug(f"  marker fallback 失败: {e}")

    ordered_actions = []
    if _create_action:
        ordered_actions.append(_create_action)
    if _query_action and _query_action != _create_action:
        ordered_actions.append(_query_action)
    for action in buttons_by_action:
        if action != _create_action and action != _query_action and action not in _delete_actions:
            ordered_actions.append(action)
    ordered_actions.extend(_delete_actions)

    # 记录每个 action 的语义角色（create/update/delete/generic/query）
    action_roles = {}
    for action in buttons_by_action:
        btn = buttons_by_action[action]
        action_roles[action] = _infer_action_role(action, btn, delete_actions=_delete_actions)

    LOG.debug(f"  操作分发: { {a: action_roles[a] for a in ordered_actions} }")

    for action in ordered_actions:
        btn = buttons_by_action.get(action)
        if not btn:
            LOG.debug(f"  跳过 {action}: 未找到对应按钮")
            continue

        btn_text = btn.get("text", "")
        btn_location = btn.get("location", "toolbar")
        role = action_roles.get(action, "generic")
        LOG.info(f"  验证操作: {action} (按钮: {btn_text}, 角色: {role})")

        # 基于角色分发到对应验证函数（不依赖硬编码操作名）
        if role == "create":
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
                # create 失败时从表格提取 fallback marker
                fallback = await _extract_fallback_marker(page)
                if fallback:
                    created_marker = fallback
                    LOG.info(f"  create 失败，使用 fallback marker: {fallback}")
                if result:
                    validated[action] = result

        elif role == "query":
            if btn.get("is_search_input"):
                result = await _do_query_via_search_input(page, btn, marker=created_marker)
                if result and result.get("success"):
                    validated[action] = result
            else:
                result = await _error_driven_retry(
                    page, _do_query, {"btn": btn, "marker": created_marker}
                )
                if result and result.get("success"):
                    validated[action] = result

        elif role == "detail":
            result = await _error_driven_retry(
                page, _do_detail, {
                    "btn": btn, "marker": created_marker,
                }
            )
            if result and result.get("success"):
                validated[action] = result

        elif role == "update":
            # 只有 row_action / dropdown 的编辑走专有路径
            # toolbar 的编辑操作走 generic
            if btn_location in ("row_action", "dropdown"):
                if not created_marker:
                    LOG.warning(f"  {action} 跳过: create 失败且无可用 marker")
                    validated[action] = skipped("create 失败且无可用 marker")
                else:
                    result = await _error_driven_retry(
                        page, _do_edit, {
                            "btn": btn, "marker": created_marker,
                            "form_filler": form_filler, "framework": framework,
                        }
                    )
                    if result and result.get("success"):
                        validated[action] = result
                    elif result:
                        validated[action] = result
            else:
                # toolbar 编辑走 generic
                result = await _error_driven_retry(
                    page, _do_generic_operation, {
                        "btn": btn, "action": action, "marker": created_marker,
                        "form_filler": form_filler,
                    }
                )
                if result and result.get("success"):
                    validated[action] = result
                elif result:
                    validated[action] = result

        elif role == "delete":
            # 只有 row_action 的删除走专有路径（直接点击行内删除按钮）
            # toolbar 和 dropdown 的删除走 generic（需要 checkbox 勾选或两步点击）
            if btn_location == "row_action":
                if not created_marker:
                    LOG.warning(f"  {action} 跳过: create 失败且无可用 marker")
                    validated[action] = skipped("create 失败且无可用 marker")
                else:
                    result = await _error_driven_retry(
                        page, _do_delete, {
                            "btn": btn, "marker": created_marker,
                        }
                    )
                    if result and result.get("success"):
                        validated[action] = result
                        created_marker = None  # 已删除
                    elif result:
                        validated[action] = result
            else:
                # toolbar 批量删除或 dropdown 删除走 generic
                if btn_location == "toolbar" and created_marker:
                    selected = await _ensure_row_selected(page, created_marker)
                    if selected:
                        LOG.debug(f"    已勾选表格行供批量删除使用")

                result = await _error_driven_retry(
                    page, _do_generic_operation, {
                        "btn": btn, "action": action, "marker": created_marker,
                        "form_filler": form_filler,
                    }
                )
                if result and result.get("success"):
                    validated[action] = result
                    created_marker = None  # 已删除
                elif result:
                    validated[action] = result

        else:
            # generic 角色：所有其他操作统一走通用路径
            # 不写死操作名白名单，任何未被上述角色匹配的操作都走这里
            if btn_location == "toolbar" and created_marker:
                selected = await _ensure_row_selected(page, created_marker)
                if selected:
                    LOG.debug(f"    已勾选表格行供 toolbar 操作使用")

            result = await _error_driven_retry(
                page, _do_generic_operation, {
                    "btn": btn, "action": action, "marker": created_marker,
                    "form_filler": form_filler,
                }
            )
            if result and result.get("success"):
                validated[action] = result
            elif result:
                validated[action] = result

        # 操作后确保回到列表页
        try:
            await _ensure_on_list_page(page)
        except Exception as e:
            LOG.error(f"    _ensure_on_list_page 异常: {e}")
            # 浏览器可能已关闭，停止后续操作验证
            if "has been closed" in str(e):
                LOG.error(f"    浏览器已关闭，停止后续操作验证")
                break

    # 判断是否有关键操作失败（create-like 和 delete-like 角色）
    # 不写死具体操作名，检查是否有任一 create/delete 角色的操作成功验证
    has_create_role = any(r == "create" for r in action_roles.values())
    has_delete_role = any(r == "delete" for r in action_roles.values())

    create_verified = any(
        action_roles.get(a) == "create" and validated.get(a, {}).get("success")
        for a in validated
    )
    delete_verified = any(
        action_roles.get(a) == "delete" and validated.get(a, {}).get("success")
        for a in validated
    )

    failed_critical = []
    if has_create_role and not create_verified:
        failed_critical.append("create-like")
    if has_delete_role and not delete_verified:
        failed_critical.append("delete-like")

    if failed_critical:
        LOG.error(f"  Stage 1 关键操作验证失败: {failed_critical}")
        return None

    LOG.info(f"  Stage 1 验证完成: {len(validated)}/{len(buttons_by_action)} 个操作通过")
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
    btn_click_result = await _click_button_escalating(page, btn_text)
    if not btn_click_result["clicked"]:
        return click_failed(f"无法点击按钮: {btn_text}",
                           trigger_text=btn_text,
                           trigger_locator_verified=None)

    # 2. 等待弹窗/页面就绪
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
            success = await _verify_operation_success(page, "create")
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
            multi_step_field_details.append({
                "label": field.get("label"),
                "kb_category": kb_cat,
                "selector": field.get("selector"),
                "fill_rule": field.get("fill_rule", {}),
                "is_editable": False,
                "option_text": "",
            })

    # 5. 填充表单（传入预生成的 fill_data）
    fill_result = await form_filler.fill_create_form(fields, username, fill_data)
    filled = fill_result["filled"] if isinstance(fill_result, dict) else fill_result
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
    ms_filled, ms_details, _ = await form_filler.fill_multi_step_fields(fields, framework)

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
                break

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
                return api_error(err_text,
                                trigger_text=btn_text,
                                trigger_locator_verified=btn_click_result.get("locator"))

    # 10. 验证成功（弹窗关闭 / 成功提示）
    success = await _verify_operation_success(page, "create")
    if not success:
        return no_success_signal("提交后未检测到成功信号",
                                trigger_text=btn_text,
                                trigger_locator_verified=btn_click_result.get("locator"))

    # 10.5. API 触发检测（额外确认，从 Stage 2 移入）
    api_triggered = await _check_create_api_triggered_simple(page)
    if api_triggered:
        LOG.info(f"    API 触发确认: POST 请求已检测到")

    # 11. 记录创建的标识数据（使用模糊匹配查找名称类字段）
    _marker_keywords = ["名称", "用户名", "name", "username"]
    marker = None
    for label, value in fill_data.items():
        if any(kw in label.lower() for kw in _marker_keywords):
            marker = value
            break
    if not marker:
        marker = username

    # 确保回到列表页
    await _ensure_on_list_page(page)

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
    for field in fields:
        if field.get("kb_category") == "radio":
            radio_field_details.append({
                "label": field.get("label"),
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": "",
                "fill_rule": field.get("fill_rule", {}),
            })

    return {
        "success": True,
        "fill_data": fill_data,
        "fill_rules": fill_rules,
        "form_fields": fields,
        "marker": marker,
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
        input_el = page.locator(locator).first
        if await input_el.count() == 0:
            return make_error("locator_failed", f"搜索输入框未找到: {locator}")

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
    except Exception:
        pass

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
    if btn_location == "row_action" and marker:
        from core.discovery.replay.button_driver import ButtonDriver
        driver = ButtonDriver(page)
        row = await driver.find_data_row(marker)
        if not row:
            return row_not_found(f"未找到数据行: {marker}")
        clicked = await driver.click_row_button_v2(row, btn_text)
        row_selector = btn_text
    else:
        btn_click_result = await _click_button_escalating(page, btn_text)
        clicked = btn_click_result["clicked"]

    if not clicked:
        return click_failed(f"无法点击详情按钮: {btn_text}")

    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 验证详情打开（弹窗或页面跳转）
    from core.discovery.ui_scanner.button_detector import _check_precondition_state, _close_dialog
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

    _log.debug(f"    [_do_edit] START: btn_text='{btn_text}', marker='{marker}', location='{btn_location}'")

    # 找到行并点击编辑（带重试）
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
        return click_failed(f"无法点击编辑按钮: {btn_text}",
                           trigger_text=btn_text,
                           trigger_locator_verified=None)

    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 验证编辑弹窗打开
    from core.discovery.ui_scanner.button_detector import _check_precondition_state, _close_dialog
    state = await _check_precondition_state(page, {"type": "dialog"})
    if not state["success"]:
        return no_dialog("点击编辑后未出现编辑弹窗",
                        trigger_text=btn_text,
                        trigger_locator_verified=btn_click_result.get("locator"))

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

    success = await _verify_operation_success(page, "update")
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
    for field in fields:
        kb_cat = field.get("kb_category", "")
        # edit 操作不修改 multi_step 字段，不收集（避免 playbook 包含 Stage 1 未执行的步骤）
        if kb_cat == "radio":
            radio_field_details.append({
                "label": field.get("label"),
                "kb_category": "radio",
                "option_locator": field.get("selector"),
                "option_text": "",
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
    }


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
    await _install_message_capture(page)

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
        row_selector = f"{btn.get('parent', '更多')} > {btn_text}"
    else:
        # toolbar 按钮：先勾选 checkbox（如果需要）
        if btn_location == "toolbar" and marker:
            await _ensure_row_selected(page, marker)
        btn_click_result = await _click_button_escalating(page, btn_text)
        clicked = btn_click_result["clicked"]
        dropdown_click_result = None

    if not clicked:
        return click_failed(f"无法点击 {action} 按钮: {btn_text}",
                           trigger_text=trigger_text_normalized,
                           trigger_locator_verified=btn_click_result.get("locator") if btn_click_result else None)

    await page.wait_for_timeout(1500)
    await wait_for_loading_complete(page)

    # 导航保护：检查是否触发了页面跳转
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
        await _navigate_back_to_list(page, url_before)

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
            confirmed = await confirm_dialog(page)
            LOG.info(f"    [DIAG-generic-path1] confirm_dialog 返回: '{confirmed}'")
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
        confirmed = await confirm_dialog(page)
        LOG.info(f"    [DIAG-generic-path3] confirm_dialog 返回: '{confirmed}'")
        if confirmed:
            LOG.info(f"    已点击确认按钮: {confirmed}")
        else:
            LOG.warning(f"    确认框存在但未找到确认按钮")
        await wait_for_loading_complete(page)
        await page.wait_for_timeout(1000)

    else:
        # ── 无弹窗：可能是即时操作（状态切换、toast 反馈等） ──
        LOG.debug(f"    未检测到弹窗 (state: {state.get('actual_state', 'none')})")
        # 先检查是否已经产生了成功信号（toast/数据变化）
        _instant_msgs = await _get_captured_messages(page)
        _instant_success = await _verify_operation_success(
            page, action, strict=True, captured_messages=_instant_msgs)
        if _instant_success:
            LOG.info(f"    即时操作成功（无弹框）")
            return {"success": True, "trigger_text": trigger_text_normalized,
                    "selectors": {"trigger": btn_text}}

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
    success = await _verify_operation_success(page, "save", strict=True)

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
