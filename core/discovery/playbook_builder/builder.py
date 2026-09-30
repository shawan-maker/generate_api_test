"""Playbook 构建器 - 生成可执行的操作序列"""

import logging
import time
from core.discovery import const

LOG = logging.getLogger("playbook_builder")


def _classify_op_steps_type(op_data: dict) -> str:
    """根据 op_data 结构特征判断步骤构建类型（不写死任何操作名）。

    判断逻辑：
    - 有 search_input → query（搜索框触发）
    - 有 fill_data 且非空 → create/update（需要填表单）
    - 有 edit_fill_data 或 is_row_action+form_fields → update（编辑操作）
    - 有 is_delete=true → delete（确认删除）
    - 其他 → generic（通用操作）

    Args:
        op_data: 操作数据字典

    Returns:
        "create" / "update" / "query" / "delete" / "generic"
    """
    # 有搜索框 → query
    if op_data.get("search_input"):
        return "query"

    # 有表单填充数据
    fill_data = op_data.get("fill_data", {})
    if fill_data:
        # 检查是否有 marker（用于区分 create vs update）
        if op_data.get("requires_marker"):
            return "update"
        else:
            return "create"

    # 编辑操作：有 edit_fill_data 或 (is_row_action + form_fields)
    if op_data.get("edit_fill_data") or (op_data.get("is_row_action") and op_data.get("form_fields")):
        return "update"

    # 行级编辑操作：is_row_action + 其他编辑特征（submit locator/dialog 模式）
    # 覆盖场景：编辑操作有 submit_locator_verified 但 form_fields/edit_fill_data 为空
    if op_data.get("is_row_action"):
        has_submit = bool(op_data.get("submit_locator_verified") or op_data.get("submit_locator"))
        has_dialog = op_data.get("interaction_mode") == "dialog"
        if has_submit and has_dialog:
            return "update"

    # 检查是否是删除确认
    if op_data.get("is_delete") or op_data.get("confirm_dialog", {}).get("is_delete"):
        return "delete"

    # 默认走通用操作
    return "generic"


def build_playbook(ui_result: dict) -> dict:
    """从 Stage 1 的 ui_result 构建完整的 playbook.json

    Playbook 是一个可执行的 UI 自动化操作手册，包含：
    - meta: 模块元信息（URL、框架等）
    - page_structure: 页面结构信息
    - operations: 所有验证通过的操作步骤（含 locator、fill_rules 等）

    Args:
        ui_result: Stage 1 的 ui_result 字典

    Returns:
        dict: 完整的 playbook 结构
    """
    # 提取基础元信息
    meta = {
        "module_name": ui_result.get("module_name", ""),
        "target_url": ui_result.get("target_url", ""),
        "base_url": ui_result.get("base_url", ""),
        "login_url": ui_result.get("login_url", ""),
        "framework": ui_result.get("framework", "element-ui"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "1.0",
        "auth_config": ui_result.get("auth_config", {}),
    }

    # 提取页面结构
    page_structure = ui_result.get("page_structure", {})

    # 构建操作列表
    validated_operations = ui_result.get("validated_operations", {})
    operations = {}

    for action, op_data in validated_operations.items():
        is_success = op_data.get("success", False)
        error_type = op_data.get("error_type", "")

        # 所有操作都生成步骤（无论成功失败），确保 Stage 2 能回放并捕获 API
        # 失败操作（如后端业务失败）仍需捕获 API 响应并生成脚本
        op_steps = []
        steps_type = "unknown"

        # ★ 跨页面操作检测：nav_info 中有 navigated_url → page-nav 模式
        # 不论成功失败，只要有导航信息就视为 page-nav
        nav_info = op_data.get("nav_info")
        is_page_nav = (nav_info
                       and nav_info.get("navigated_url"))
        if is_page_nav:
            op_data["interaction_mode"] = "page-nav"
            if not op_data.get("navigate_back_url"):
                op_data["navigate_back_url"] = ui_result.get("target_url", "")

        # 基于 op_data 结构特征分发步骤构建（不写死任何操作名）
        if is_page_nav:
            steps_type = "generic"  # page-nav 走通用角色
            op_steps.extend(_build_page_nav_steps(op_data))
        else:
            steps_type = _classify_op_steps_type(op_data)
            if steps_type == "create":
                op_steps.extend(_build_create_steps(op_data))
            elif steps_type == "query":
                op_steps.extend(_build_query_steps(op_data))
            elif steps_type == "update":
                op_steps.extend(_build_update_steps(op_data))
            elif steps_type == "delete":
                op_steps.extend(_build_delete_steps(op_data))
            else:
                # 通用操作（含导入、授权、冻结等）
                op_steps.extend(_build_generic_steps(op_data))

        if op_steps or not is_success:
            # 使用 trigger_text 作为业务名称（按钮原文即操作名）
            display_name = op_data.get("trigger_text") or action
            # ★ 只有 create 操作存储 marker（自身生成的值）
            # 其他操作的 marker 从上游 create 传播，不硬编码到 playbook
            marker = op_data.get("marker") if steps_type == "create" else None
            # replayable: 有步骤的操作都可回放（无论成功失败）
            # 失败操作仍需回放以捕获 API 并在报告中展示实际结果
            replayable = bool(op_steps)
            entry = {
                "display_name": display_name,
                "description": op_data.get("description", action),
                "role": steps_type,
                "steps": op_steps,
                "marker": marker,
                "detection_status": "success" if is_success else "failed",
                "replayable": replayable,
            }
            if not is_success and error_type:
                entry["error_type"] = error_type
                entry["error_text"] = op_data.get("error_text", "")
            # 导航类操作记录探索信息（无论成功失败）
            nav_info = op_data.get("nav_info")
            if nav_info:
                entry["nav_info"] = nav_info
            operations[action] = entry

    # ★ Unvalidated buttons fallback: 扫描 toolbar_buttons/row_actions 中
    # 未在 validated_operations 中出现的按钮，为它们生成最基本的 click 步骤
    # 这确保 rescue 阶段发现的元素也能进入 playbook
    validated_trigger_texts = {
        op_data.get("trigger_text", "").strip()
        for op_data in validated_operations.values()
    }
    fallback_count = 0

    for btn_list_key in ("toolbar_buttons", "row_actions"):
        for btn in ui_result.get(btn_list_key, []):
            btn_text = btn.get("text", "").strip()
            if not btn_text or btn_text in validated_trigger_texts:
                continue
            # 跳过已作为 action key 存在的按钮
            action_key = btn_text
            if action_key in operations:
                continue
            # 生成最基本的 click 步骤
            btn_locator = btn.get("selector", "")
            if not btn_locator:
                btn_locator = f"button:has-text('{btn_text}')"
            fallback_steps = [{
                "action": "click_button",
                "playwright_locator": btn_locator,
                "description": f"点击 {btn_text} 按钮（未验证）"
            }]
            operations[action_key] = {
                "display_name": btn_text,
                "description": f"未验证操作: {btn_text}",
                "role": "generic",
                "steps": fallback_steps,
                "marker": None,
                "detection_status": "unvalidated",
                "replayable": True,
            }
            validated_trigger_texts.add(btn_text)
            fallback_count += 1

    if fallback_count > 0:
        LOG.info(f"  Playbook: 添加 {fallback_count} 个未验证按钮的 fallback 步骤")

    return {
        "meta": meta,
        "page_structure": page_structure,
        "operations": operations
    }


def _build_create_steps(op_data: dict) -> list:
    """构建 create 操作步骤"""
    steps = []

    # Step 1: 点击创建按钮（只使用已验证的 locator）
    trigger_locator = op_data.get("trigger_locator_verified")
    if trigger_locator:
        steps.append({
            "action": "click_button",
            "playwright_locator": trigger_locator,
            "description": "点击创建按钮"
        })

    # Step 2: 等待对话框（添加 interaction_mode）
    interaction_mode = op_data.get("interaction_mode", "dialog")
    dialog_locator = op_data.get("dialog_locator", ".el-dialog__wrapper")
    steps.append({
        "action": "wait_for_dialog",
        "playwright_locator": dialog_locator,
        "interaction_mode": interaction_mode,
        "description": "等待创建对话框"
    })

    # Step 3: 填充表单
    form_fields = op_data.get("form_fields", [])
    fill_rules = op_data.get("fill_rules", {})
    multi_step_details = op_data.get("multi_step_field_details", [])
    radio_details = op_data.get("radio_field_details", [])

    if form_fields and fill_rules:
        fields_info = []

        # 处理普通字段（input/textarea）—— 跳过 multi_step 和 radio（单独处理）
        for field in form_fields:
            label = field.get("label", "")
            selector = field.get("selector")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")

            if not selector:
                continue

            # 跳过 multi_step 和 radio（单独处理）
            if kb_category in ("el-select", "el-cascader", "date-picker", "form-checkbox", "radio"):
                continue

            rule = fill_rules.get(label, {})
            field_info = {
                "label": label,
                "playwright_locator": selector,
                "type": field_type,
                "kb_category": kb_category,
                "fill_rule": rule
            }

            # 标记 marker 字段：label 包含关键字 + 仅限文本输入类型（排除 select/checkbox/radio）
            _marker_kw = ("名称", "账号", "name", "username")
            if field_type in ("input", "textarea") and any(kw in label.lower() for kw in _marker_kw):
                field_info["is_marker"] = True

            fields_info.append(field_info)

        # 添加 multi_step 字段（带完整属性）
        for ms_field in multi_step_details:
            fields_info.append({
                "label": ms_field["label"],
                "playwright_locator": ms_field.get("selector", ""),
                "type": ms_field["kb_category"].replace("el-", "") if ms_field["kb_category"].startswith("el-") else ms_field["kb_category"],
                "kb_category": ms_field["kb_category"],
                "fill_rule": ms_field.get("fill_rule", {}),
                "is_editable": ms_field.get("is_editable", False),
                "option_text": ms_field.get("option_text", ""),
            })

        # 添加 radio 字段（带已验证的选项 locator）
        for radio_field in radio_details:
            fields_info.append({
                "label": radio_field["label"],
                "playwright_locator": radio_field.get("option_locator", ""),
                "type": "radio",
                "kb_category": "radio",
                "fill_rule": radio_field.get("fill_rule", {}),
                "option_text": radio_field.get("option_text", ""),
            })

        if fields_info:
            steps.append({
                "action": "fill_form",
                "fields": fields_info,
                "description": "填充表单字段"
            })

    # Step 4: 点击提交按钮（使用已验证的 locator）
    submit_locator = op_data.get("submit_locator_verified") or op_data.get("submit_locator")
    submit_text = op_data.get("submit_text") or op_data.get("selectors", {}).get("submit")
    submit_strategy = op_data.get("submit_click_strategy", "playwright")  # 默认 playwright
    if submit_locator:
        step_entry = {
            "action": "click_button",
            "playwright_locator": submit_locator,
            "text": submit_text,
            "description": "点击提交按钮"
        }
        # 如果 Stage 1 使用 JS 点击成功，记录策略供 Stage 2 优先使用
        if submit_strategy == "js":
            step_entry["click_strategy"] = "js"
        steps.append(step_entry)

    # Step 5: 验证成功
    success_locator = op_data.get("success_locator", ".el-message--success")
    steps.append({
        "action": "assert_success",
        "playwright_locator": success_locator,
        "description": "验证创建成功"
    })

    return steps


def _build_delete_steps(op_data: dict) -> list:
    """构建删除操作步骤"""
    steps = []

    # Step 1: 定位数据行
    steps.append({
        "action": "find_row",
        "description": "定位目标数据行"
    })

    # Step 2: 勾选 checkbox（如果需要）
    needs_checkbox = op_data.get("needs_checkbox", False)
    if needs_checkbox:
        checkbox_locator = op_data.get("checkbox_locator", ".el-checkbox__input")
        steps.append({
            "action": "select_row_checkbox",
            "checkbox_locator": checkbox_locator,
            "description": "勾选行 checkbox"
        })

    # Step 3: 点击删除按钮（只使用已验证的 locator）
    selectors = op_data.get("selectors", {})
    trigger = selectors.get("trigger")
    trigger_locator = op_data.get("trigger_locator_verified")
    if trigger_locator:
        steps.append({
            "action": "click_button",
            "text": trigger,
            "playwright_locator": trigger_locator,
            "description": "点击删除按钮"
        })

    # Step 4: 处理确认对话框（仅当 Stage 1 记录 confirmed=True 时生成）
    confirmed = op_data.get("confirmed") or op_data.get("selectors", {}).get("confirm")
    if confirmed:
        steps.append({
            "action": "confirm_dialog",
            "description": "点击确认对话框"
        })

    # Step 4.5: 如果确认后对话框仍在，插入关闭步骤
    post_state = op_data.get("post_confirm_state", {})
    if post_state.get("dialog_remains"):
        steps.append({
            "action": "close_dialog",
            "description": "关闭残留对话框"
        })

    # Step 5: 验证行消失
    steps.append({
        "action": "assert_row_disappeared",
        "description": "验证数据行已消失"
    })

    return steps


def _build_update_steps(op_data: dict) -> list:
    """构建更新操作步骤"""
    steps = []

    # Step 1: 定位数据行
    steps.append({
        "action": "find_row",
        "description": "定位目标数据行"
    })

    # Step 2: 点击编辑按钮
    selectors = op_data.get("selectors", {})
    trigger = selectors.get("trigger")
    is_row_action = op_data.get("is_row_action", False)

    if is_row_action:
        # 行级操作：先定位行，再在行内点击按钮
        button_text = op_data.get("button_text") or trigger
        trigger_locator = op_data.get("trigger_locator_verified")
        if button_text:
            steps.append({
                "action": "click_row_button",
                "button_text": button_text,
                "playwright_locator": trigger_locator,
                "description": f"点击行内{trigger}按钮"
            })
    else:
        # 全局按钮（只使用已验证的 locator）
        trigger_locator = op_data.get("trigger_locator_verified")
        if trigger_locator:
            steps.append({
                "action": "click_button",
                "text": trigger,
                "playwright_locator": trigger_locator,
                "description": "点击编辑按钮"
            })

    # Step 3: 等待对话框（添加 interaction_mode）
    interaction_mode = op_data.get("interaction_mode", "dialog")
    dialog_locator = op_data.get("dialog_locator", ".el-dialog__wrapper")
    steps.append({
        "action": "wait_for_dialog",
        "playwright_locator": dialog_locator,
        "interaction_mode": interaction_mode,
        "description": "等待编辑对话框"
    })

    # Step 4: 填充表单
    form_fields = op_data.get("form_fields", [])
    fill_rules = op_data.get("fill_rules", {})
    multi_step_details = op_data.get("multi_step_field_details", [])
    radio_details = op_data.get("radio_field_details", [])

    if form_fields and fill_rules:
        fields_info = []

        # 处理普通字段（input/textarea）—— 跳过 multi_step 和 radio
        for field in form_fields:
            label = field.get("label", "")
            selector = field.get("selector")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")
            input_type = field.get("inputType", "text")
            placeholder = field.get("placeholder", "")
            visible_index = field.get("visible_index")

            if not selector:
                continue

            # 跳过 multi_step 和 radio（单独处理）
            if kb_category in ("el-select", "el-cascader", "date-picker", "form-checkbox", "radio"):
                continue

            rule = fill_rules.get(label, {})

            # ★ 生成多候选选择器（与 _build_generic_steps 一致，跨会话稳定）
            candidates = []

            # 候选 1: 基于 placeholder（最稳定，跨会话完全不变）
            if placeholder:
                escaped_ph = placeholder.replace('"', '\\"')
                candidates.append({
                    "strategy": "placeholder",
                    "selector": f'input[placeholder="{escaped_ph}"]',
                })

            # 候选 2: Stage 1 的原始绝对路径选择器（特定于编辑 dialog 的 DOM 位置）
            if selector:
                candidates.append({
                    "strategy": "original",
                    "selector": selector,
                })

            # 候选 3: 基于 label 文本（限定到可见 dialog/drawer，避免匹配多个弹窗中的同名字段）
            if label and not label.startswith("字段"):
                escaped_label = label.replace('"', '\\"')
                candidates.append({
                    "strategy": "label",
                    "selector": (
                        f'.el-dialog__wrapper:not([style*="display: none"]) '
                        f'.el-form-item:has(.el-form-item__label:has-text("{escaped_label}")) input, '
                        f'.el-drawer:not([style*="display: none"]) '
                        f'.el-form-item:has(.el-form-item__label:has-text("{escaped_label}")) input'
                    ),
                })

            # 候选 4: 基于 visible_index（JS 定位，跨会话最稳定）
            if visible_index is not None:
                candidates.append({
                    "strategy": "visible_index",
                    "selector": f"__js_index__:{visible_index}",
                })

            # 默认主选择器：第一个候选；fallbacks：其余候选
            primary = candidates[0] if candidates else {"strategy": "none", "selector": ""}
            fallbacks = candidates[1:] if len(candidates) > 1 else []

            field_info = {
                "label": label,
                "playwright_locator": primary.get("selector", ""),
                "locator_strategy": primary.get("strategy", ""),
                "fallback_locators": fallbacks,
                "type": field_type,
                "inputType": input_type,
                "kb_category": kb_category,
                "fill_rule": rule
            }

            # 标记 marker 字段：label 包含关键字 + 仅限文本输入类型（排除 select/checkbox/radio）
            _marker_kw = ("名称", "账号", "name", "username")
            if field_type in ("input", "textarea") and any(kw in label.lower() for kw in _marker_kw):
                field_info["is_marker"] = True

            fields_info.append(field_info)

        # 添加 multi_step 字段（带完整属性）
        for ms_field in multi_step_details:
            fields_info.append({
                "label": ms_field["label"],
                "playwright_locator": ms_field.get("selector", ""),
                "type": ms_field["kb_category"].replace("el-", "") if ms_field["kb_category"].startswith("el-") else ms_field["kb_category"],
                "kb_category": ms_field["kb_category"],
                "fill_rule": ms_field.get("fill_rule", {}),
                "is_editable": ms_field.get("is_editable", False),
                "option_text": ms_field.get("option_text", ""),
            })

        # 添加 radio 字段（带已验证的选项 locator）
        for radio_field in radio_details:
            fields_info.append({
                "label": radio_field["label"],
                "playwright_locator": radio_field.get("option_locator", ""),
                "type": "radio",
                "kb_category": "radio",
                "fill_rule": radio_field.get("fill_rule", {}),
                "option_text": radio_field.get("option_text", ""),
            })

        if fields_info:
            steps.append({
                "action": "fill_form",
                "fields": fields_info,
                "description": "填充编辑表单"
            })

    # Step 5: 点击提交（使用已验证的 locator）
    submit_locator = op_data.get("submit_locator_verified") or op_data.get("submit_locator")
    submit_text = op_data.get("submit_text") or op_data.get("selectors", {}).get("submit")
    if submit_locator:
        steps.append({
            "action": "click_button",
            "playwright_locator": submit_locator,
            "text": submit_text,
            "description": "点击提交按钮"
        })

    # Step 6: 验证成功
    success_locator = op_data.get("success_locator", ".el-message--success")
    steps.append({
        "action": "assert_success",
        "playwright_locator": success_locator,
        "description": "验证更新成功"
    })

    return steps


def _build_query_steps(op_data: dict) -> list:
    """构建 query 操作步骤

    支持两种模式：
    1. 搜索按钮模式：click_button + wait_for_table_ready
    2. 搜索输入框模式：fill_input + (click_button/press_key) + wait_for_table_ready
    """
    steps = []

    # 搜索输入框步骤（如果有）
    search_input = op_data.get("search_input")
    if search_input:
        locator = search_input.get("locator", "")
        placeholder = search_input.get("placeholder", "")
        if locator:
            steps.append({
                "action": "fill_input",
                "playwright_locator": locator,
                "value": "{marker_name}",  # 运行时替换为创建的名称
                "description": f"搜索框输入: {placeholder}" if placeholder else "搜索框输入关键词"
            })

    # 触发步骤：按钮/回车/自动
    trigger_locator = op_data.get("trigger_locator_verified")
    trigger_mode = op_data.get("trigger_mode", "button")

    if trigger_locator and trigger_mode != "auto":
        if trigger_locator.startswith("input["):
            # locator 是输入框，不是按钮 → 统一用回车触发
            # （即使 trigger_mode="button" 表示有相邻按钮，但我们没存其 locator）
            steps.append({
                "action": "press_key",
                "key": "Enter",
                "description": "回车触发搜索"
            })
        else:
            steps.append({
                "action": "click_button",
                "playwright_locator": trigger_locator,
                "description": "点击查询按钮"
            })
    elif trigger_mode == "enter":
        steps.append({
            "action": "press_key",
            "key": "Enter",
            "description": "回车触发搜索"
        })
    elif not search_input:
        # 传统模式（无搜索输入框信息，只使用已验证的 locator）
        if trigger_locator:
            steps.append({
                "action": "click_button",
                "playwright_locator": trigger_locator,
                "description": "点击查询按钮"
            })

    # 等待表格刷新（不检查成功消息）
    steps.append({
        "action": "wait_for_table_ready",
        "description": "等待表格数据刷新"
    })

    return steps


def _build_generic_steps(op_data: dict) -> list:
    """构建通用操作步骤"""
    steps = []

    selectors = op_data.get("selectors", {})

    # 如果需要先勾选 checkbox（toolbar 操作），先定位行
    needs_checkbox = op_data.get("needs_checkbox", False)
    if needs_checkbox:
        steps.append({
            "action": "find_row",
            "description": "定位目标数据行"
        })
        checkbox_locator = op_data.get("checkbox_locator", ".el-checkbox__input")
        steps.append({
            "action": "select_row_checkbox",
            "checkbox_locator": checkbox_locator,
            "description": "勾选行 checkbox"
        })
    # 如果有行选择器（row_action 操作），先定位行
    elif selectors.get("row_selector"):
        steps.append({
            "action": "find_row",
            "description": "定位目标数据行"
        })

    # 点击触发按钮（只使用已验证的 locator）
    trigger = selectors.get("trigger")
    trigger_locator = op_data.get("trigger_locator_verified")
    dropdown_item_text = op_data.get("dropdown_item_text_verified")

    if trigger_locator:
        # 普通按钮点击
        steps.append({
            "action": "click_button",
            "text": trigger,
            "playwright_locator": trigger_locator,
            "description": "点击操作按钮"
        })
    elif dropdown_item_text:
        # dropdown 子项点击（如"更多 > 冻结"）
        steps.append({
            "action": "click_row_more",
            "item_text": dropdown_item_text,
            "expand_strategy": op_data.get("expand_strategy_verified", "click"),
            "description": f"点击下拉菜单项: {dropdown_item_text}"
        })

    # 如果 Stage 1 在弹窗中填充了 select 字段，生成 fill_form 步骤（在 confirm 之前执行）
    dialog_fills = op_data.get("dialog_fills", [])
    if dialog_fills:
        fill_fields = []
        for d in dialog_fills:
            if d.get("option_text") and d.get("selector"):
                fill_fields.append({
                    "label": d.get("label", ""),
                    "playwright_locator": d["selector"],
                    "type": "select",
                    "kb_category": d.get("kb_category", "el-select"),
                    "fill_rule": {},
                    "is_editable": d.get("is_editable", False),
                    "option_text": d["option_text"],
                })
        if fill_fields:
            steps.append({
                "action": "fill_form",
                "fields": fill_fields,
                "description": "填充弹窗中的下拉选择字段"
            })

    # 处理表单字段（input 类型，如 number、text 等）
    edit_fields = op_data.get("edit_field_details", [])
    if edit_fields:
        fill_fields = []
        for field in edit_fields:
            input_type = field.get("inputType", "text")
            label = field.get("label", "")
            placeholder = field.get("placeholder", "")
            thead_label = field.get("thead_label", "")
            visible_index = field.get("visible_index")

            # 根据 inputType 生成合适的 fill_rule
            if input_type == "number":
                fill_rule = {
                    "rule": "number_pattern",
                    "params": {"min": 100000, "max": 999999}
                }
            else:
                fill_rule = {"rule": "name_pattern", "params": {"prefix": "test"}}

            # ★ 生成多个候选选择器（按优先级排列），跨会话稳定
            # 问题：绝对路径 nth-of-type 选择器跨会话不稳定
            # 方案：生成多个基于语义特征的候选，由 replay 层依次尝试
            candidates = []

            # 候选 1: 基于 placeholder（最稳定，跨会话完全不变）
            if placeholder:
                escaped_ph = placeholder.replace('"', '\\"')
                candidates.append({
                    "strategy": "placeholder",
                    "selector": f'input[placeholder="{escaped_ph}"]',
                })

            # 候选 2: Stage 1 的原始绝对路径选择器（特定于当前 dialog 的 DOM 位置）
            original_selector = field.get("selector", "")
            if original_selector:
                candidates.append({
                    "strategy": "original",
                    "selector": original_selector,
                })

            # 候选 3: 基于 label 文本（限定到可见 dialog/drawer，避免匹配多个弹窗中的同名字段）
            if label and not label.startswith("字段"):
                escaped_label = label.replace('"', '\\"')
                candidates.append({
                    "strategy": "label",
                    "selector": (
                        f'.el-dialog__wrapper:not([style*="display: none"]) '
                        f'.el-form-item:has(.el-form-item__label:has-text("{escaped_label}")) input, '
                        f'.el-drawer:not([style*="display: none"]) '
                        f'.el-form-item:has(.el-form-item__label:has-text("{escaped_label}")) input'
                    ),
                })

            # 候选 4: 基于 visible_index（JS 定位，跨会话最稳定）
            # 在 scope（drawer/dialog）内找第 N 个可见 input
            if visible_index is not None:
                candidates.append({
                    "strategy": "visible_index",
                    "selector": f"__js_index__:{visible_index}",  # 特殊标记，replay 层识别并用 JS 定位
                })

            # 默认主选择器：第一个候选；fallbacks：其余候选
            primary = candidates[0] if candidates else {"strategy": "none", "selector": ""}
            fallbacks = candidates[1:] if len(candidates) > 1 else []

            fill_fields.append({
                "label": label,
                "playwright_locator": primary.get("selector", ""),
                "locator_strategy": primary.get("strategy", ""),
                "fallback_locators": fallbacks,  # ★ 多个候选选择器
                "type": field.get("type", "input"),
                "inputType": input_type,
                "fill_rule": fill_rule,
            })
        if fill_fields:
            steps.append({
                "action": "fill_form",
                "fields": fill_fields,
                "description": "填充表单字段"
            })

    # 处理表单提交按钮
    submit_result = op_data.get("submit_result", {})
    if submit_result.get("locator"):
        steps.append({
            "action": "click_button",
            "text": submit_result.get("text", "提交"),
            "playwright_locator": submit_result.get("locator"),
            "description": f"点击{submit_result.get('text', '提交')}按钮"
        })

    # 处理确认对话框（仅当 Stage 1 记录 confirmed=True 时生成）
    confirmed = op_data.get("confirmed") or op_data.get("selectors", {}).get("confirm")
    if confirmed:
        steps.append({
            "action": "confirm_dialog",
            "description": "点击确认对话框"
        })

    # 如果确认后对话框仍在，插入关闭步骤
    post_state = op_data.get("post_confirm_state", {})
    if post_state.get("dialog_remains"):
        steps.append({
            "action": "close_dialog",
            "description": "关闭残留对话框"
        })

    # 如果有导航返回，添加 navigate_back 步骤
    navigate_back_url = op_data.get("navigate_back_url")
    if navigate_back_url:
        steps.append({
            "action": "navigate_back",
            "url": navigate_back_url,
            "description": "导航回原页面"
        })

    # 验证成功 — 仅在有实际 UI 操作时生成（导航操作不弹成功消息）
    has_ui_action = bool(trigger_locator or dropdown_item_text or confirmed
                         or needs_checkbox or selectors.get("row_selector") or dialog_fills)
    if has_ui_action:
        # 使用多选择器兼容不同 UI 框架的成功提示：
        # - .el-message--success: Element UI 标准成功消息
        # - .el-notification__content:has-text('成功'): Element UI 通知组件
        # - [role='alert']:has-text('成功'): ARIA 标准通知
        success_locator = op_data.get("success_locator",
            ".el-message--success, .el-notification__content:has-text('成功'), [role='alert']:has-text('成功')")
        steps.append({
            "action": "assert_success",
            "playwright_locator": success_locator,
            "description": "验证操作成功"
        })

    return steps


def _build_page_nav_steps(op_data: dict) -> list:
    """构建跨页面操作步骤（page-nav 模式）

    跨页面操作的数据存储在 nav_info 中：
    - nav_info.form_fields: 表单字段定义
    - nav_info.fill_data: 填充的测试数据
    - nav_info.submit_result: 提交结果（button_text, success）
    - nav_info.navigated_url: 跳转后的 URL

    步骤序列：click_button → fill_form → click_button(提交) → assert_success → navigate_back
    """
    steps = []
    nav_info = op_data.get("nav_info", {})
    selectors = op_data.get("selectors", {})

    # Step 1: 定位数据行（如果是行级操作）
    needs_checkbox = op_data.get("needs_checkbox", False)
    if needs_checkbox:
        steps.append({
            "action": "find_row",
            "description": "定位目标数据行"
        })
        checkbox_locator = op_data.get("checkbox_locator", ".el-checkbox__input")
        steps.append({
            "action": "select_row_checkbox",
            "checkbox_locator": checkbox_locator,
            "description": "勾选行 checkbox"
        })
    elif selectors.get("row_selector"):
        steps.append({
            "action": "find_row",
            "description": "定位目标数据行"
        })

    # Step 2: 点击触发按钮（跳转到新页面）
    trigger = selectors.get("trigger")
    trigger_locator = op_data.get("trigger_locator_verified")
    dropdown_item_text = op_data.get("dropdown_item_text_verified")
    row_selector = selectors.get("row_selector")

    if trigger_locator:
        steps.append({
            "action": "click_button",
            "text": trigger,
            "playwright_locator": trigger_locator,
            "description": "点击操作按钮（页面跳转）"
        })
    elif row_selector and trigger:
        # 行级操作：先定位行，再点击行内按钮
        if ">" in row_selector:
            # 下拉菜单项（如 "更多 > 授权"）
            item_text = row_selector.split(">")[-1].strip()
            steps.append({
                "action": "click_row_more",
                "item_text": item_text,
                "expand_strategy": op_data.get("expand_strategy_verified", "click"),
                "description": f"点击下拉菜单项: {item_text}"
            })
        else:
            # 行内按钮（如 "添加用户"）
            steps.append({
                "action": "click_row_button",
                "button_text": trigger,
                "playwright_locator": f"button:has-text('{trigger}')",
                "description": f"点击行内{trigger}按钮"
            })
    elif dropdown_item_text:
        steps.append({
            "action": "click_row_more",
            "item_text": dropdown_item_text,
            "expand_strategy": op_data.get("expand_strategy_verified", "click"),
            "description": f"点击下拉菜单项: {dropdown_item_text}"
        })
    elif trigger:
        # Fallback: 使用 trigger 文本构造 locator
        escaped_trigger = trigger.replace('"', '\\"')
        steps.append({
            "action": "click_button",
            "text": trigger,
            "playwright_locator": f"button:has-text(\"{escaped_trigger}\")",
            "description": f"点击{trigger}按钮（页面跳转）"
        })

    # Step 3: 等待新页面加载（使用 URL 变化检测）
    navigated_url = nav_info.get("navigated_url", "")
    if navigated_url:
        steps.append({
            "action": "wait_for_url",
            "url_pattern": navigated_url.split("?")[0],  # 匹配路径段
            "description": "等待页面跳转完成"
        })

    # Step 4: 填充表单（从 nav_info 提取）
    form_fields = nav_info.get("form_fields", [])
    fill_data = nav_info.get("fill_data", {})
    field_states = nav_info.get("field_states", [])

    if form_fields or field_states:
        fill_fields = []

        # ★ 构建 label → field_states 映射（用于获取 isDisabled/isMultiSelect/value 元数据）
        field_states_by_label = {}
        for fs in field_states:
            lbl = fs.get("label", "")
            if lbl:
                field_states_by_label[lbl] = fs

        # ★ 以 form_fields 为主数据源（含 selector + kb_category，来自 scan_form_fields_v2）
        # field_states 仅补充元数据（isDisabled, isMultiSelect, value）
        processed_labels = set()

        for field in form_fields:
            label = field.get("label", "")
            selector = field.get("selector", "")
            kb_category = field.get("kb_category", "")
            field_type = field.get("type", "input")

            if not label or not selector:
                continue

            # 从 field_states 获取元数据
            fs = field_states_by_label.get(label, {})
            is_disabled = fs.get("isDisabled", False)
            if is_disabled:
                continue

            processed_labels.add(label)

            # 根据 kb_category 构建字段条目（使用 scan_form_fields_v2 的真实类别）
            if kb_category == "el-select":
                select_field = {
                    "label": label,
                    "type": "select",
                    "kb_category": "el-select",
                    "playwright_locator": selector,
                    "fill_rule": {},
                    "is_editable": True,
                    "is_multi_select": fs.get("isMultiSelect", False),
                }
                # ★ 传递 close_dropdown（el-select 选择后下拉框仍展开，需发送 ESC 关闭）
                if "close_dropdown" in field:
                    select_field["close_dropdown"] = field["close_dropdown"]
                fill_fields.append(select_field)
            elif kb_category == "radio":
                radio_field = {
                    "label": label,
                    "type": "radio",
                    "kb_category": "radio",
                    "playwright_locator": selector,
                    "fill_rule": {},
                    "option_text": fs.get("value", ""),
                    # ★ 保留 firstOptionText（来自 scan_form_fields_v2），供 Stage 2 Vue v-model 同步使用
                    "firstOptionText": field.get("firstOptionText", ""),
                }
                # ★ 传递 cascade_actions（如 radio 触发 transfer-box 选择）
                if "cascade_actions" in field:
                    radio_field["cascade_actions"] = field["cascade_actions"]
                fill_fields.append(radio_field)
            elif kb_category in ("list-selector", "el-cascader", "date-picker", "form-checkbox"):
                # 多步组件：kb_category 已在 MULTI_STEP_TYPES 中，replay_engine 正确路由
                fill_fields.append({
                    "label": label,
                    "type": field_type,
                    "kb_category": kb_category,
                    "playwright_locator": selector,
                    "fill_rule": {},
                })
            elif kb_category in ("input-generic", "textarea-generic"):
                # 普通输入字段
                fill_data_value = nav_info.get("fill_data", {}).get(label, "")
                fill_fields.append({
                    "label": label,
                    "type": field_type,
                    "kb_category": kb_category,
                    "playwright_locator": selector,
                    "fill_rule": {"rule": "fixed_value", "params": {"value": fill_data_value}} if fill_data_value else {},
                })
            else:
                # 其他类型：直接使用
                fill_fields.append({
                    "label": label,
                    "type": field_type,
                    "kb_category": kb_category,
                    "playwright_locator": selector,
                    "fill_rule": {},
                })

        # 补充 field_states 中有但 form_fields 中没有的字段（旧 playbook 兼容）
        for fs in field_states:
            label = fs.get("label", "")
            if not label or label in processed_labels:
                continue
            is_disabled = fs.get("isDisabled", False)
            if is_disabled:
                continue
            # 无 selector 的字段无法生成可执行步骤，跳过
            LOG.debug(f"    field_states 字段 {label} 无对应 form_fields 条目，跳过")

        if fill_fields:
            steps.append({
                "action": "fill_form",
                "fields": fill_fields,
                "description": "填充表单字段"
            })

    # Step 5: 点击提交按钮（无论成功失败都生成，Stage 2 需要捕获 API）
    submit_result = nav_info.get("submit_result", {})
    submit_text = submit_result.get("button_text", "确定")
    if submit_text:
        steps.append({
            "action": "click_button",
            "text": submit_text,
            "playwright_locator": f"button:has-text(\"{submit_text}\")",
            "description": f"点击{submit_text}按钮"
        })

    # Step 6: 验证成功（无论成功失败都生成，assert_success 会做语义判断）
    success_locator = op_data.get("success_locator",
        ".el-message--success, .el-notification__content:has-text('成功'), [role='alert']:has-text('成功')")
    steps.append({
        "action": "assert_success",
        "playwright_locator": success_locator,
        "description": "验证操作成功"
    })

    # Step 7: 导航回原页面
    navigate_back_url = op_data.get("navigate_back_url")
    if navigate_back_url:
        steps.append({
            "action": "navigate_back",
            "url": navigate_back_url,
            "description": "导航回原页面"
        })

    return steps
