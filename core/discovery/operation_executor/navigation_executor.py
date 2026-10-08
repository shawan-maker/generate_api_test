"""
navigation_executor.py — Cross-page navigation for Stage 1 operations.

Contains functions for exploring navigated pages, handling cross-page
operations, and navigating back to the list page.
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
    _verify_operation_success,
)
from core.discovery.operation_executor.result_factory import (
    make_error, make_success, click_failed, row_not_found, no_dialog,
    submit_failed, no_success_signal, api_error, form_validation,
    no_fields, no_locator, skipped, exception,
)

LOG = logging.getLogger("navigation_executor")


async def _explore_and_operate_in_new_page(page, action: str, new_url: str, depth: int = 0, max_depth: int = 3) -> dict:
    """探索导航后的新页面，探测表单并完成操作。

    核心逻辑：
    1. 探测页面，提取表单字段
    2. 如果有表单：填写字段 → 点击提交按钮 → 验证结果
    3. 如果没有表单：点击关键操作按钮（保存/确认）→ 验证结果
    4. 忽略菜单栏按钮（menu_items）

    Args:
        page: Playwright Page 对象
        action: 触发跳转的操作类型（如 'authorize'）
        new_url: 新页面 URL
        depth: 当前递归深度（0 = 首次进入）
        max_depth: 最大递归深度（默认 3 层）

    Returns:
        dict: {
            "navigated_url": str,
            "page_title": str,
            "has_form": bool,
            "form_fields": list,
            "fill_data": dict,
            "submit_result": {...},
            "error_type": str,
            "error_text": str,
        }
    """
    nav_info = {
        "navigated_url": new_url,
        "depth": depth,
        "page_title": "",
        "has_form": False,
        "form_fields": [],
        "fill_data": {},
        "submit_result": {},
        "error_type": "",
        "error_text": "",
    }

    try:
        # 1. 等待页面加载
        await page.wait_for_load_state("networkidle", timeout=15000)
        await page.wait_for_timeout(1500)

        # 重新安装消息捕获（页面导航后 JS hook 丢失）
        await _install_message_capture(page)
        # DIAG: 验证消息捕获是否安装成功
        _cap_check = await page.evaluate("() => typeof window.__captured_messages !== 'undefined'")
        LOG.info(f"    [DIAG-msg-capture-install] 消息捕获安装结果: hasCapture={_cap_check}")

        # 2. 探测页面（提取表单字段，忽略菜单栏）
        from core.discovery.ui_scanner.element_scanner import discover_all
        page_result = await discover_all(page)
        nav_info["page_title"] = await page.title()

        # 只提取表单字段和工具栏/对话框按钮（忽略菜单栏）
        form_fields = page_result.get("form_fields", [])
        toolbar_buttons = page_result.get("toolbar_buttons", [])
        dialog_buttons = page_result.get("dialog_buttons", [])

        nav_info["has_form"] = len(form_fields) > 0
        nav_info["form_fields"] = form_fields

        LOG.info(f"    新页面 (depth={depth}): title='{nav_info['page_title']}', "
                f"form_fields={len(form_fields)}, toolbar_buttons={len(toolbar_buttons)}, "
                f"dialog_buttons={len(dialog_buttons)}")

        # 3. 如果达到最大深度，停止
        if depth >= max_depth:
            LOG.warning(f"    达到最大递归深度 {max_depth}，停止探索")
            nav_info["error_type"] = "max_depth_reached"
            nav_info["error_text"] = f"达到最大深度 {max_depth}"
            return nav_info

        # 4. 如果有表单：填写并提交
        if form_fields:
            LOG.info(f"    发现表单，开始填写...")
            form_filler = FormFiller(page)

            # 扫描表单字段详情
            form_field_details = await form_filler.scan_form_fields_v2()

            # 生成填充数据并填写表单（使用标准 API）
            fill_data = generate_fill_data(form_field_details, username="AT_test_auto")
            nav_info["fill_data"] = fill_data

            # 填写表单（普通字段 + 多步组件）
            fill_result = await form_filler.fill_create_form(form_field_details, "AT_test_auto", fill_data)
            filled_count = fill_result["filled"] if isinstance(fill_result, dict) else fill_result
            cascade_actions = fill_result.get("cascade_actions", []) if isinstance(fill_result, dict) else []

            # 处理 el-select 等多步组件
            ms_filled, ms_details, close_dropdown_actions = await form_filler.fill_multi_step_fields(form_field_details, "element-ui")
            filled_count += ms_filled

            LOG.info(f"    已填写 {filled_count} 个字段（含 {ms_filled} 个多步组件）")
            if cascade_actions:
                LOG.info(f"    发现 {len(cascade_actions)} 个 cascade 操作")
            if close_dropdown_actions:
                LOG.info(f"    发现 {len(close_dropdown_actions)} 个下拉框关闭操作")

            # ★ 将 cascade_actions 附加到对应的 radio 字段
            if cascade_actions:
                for field in form_field_details:
                    if field.get("kb_category") == "radio":
                        matching_cascades = [c for c in cascade_actions
                                             if c["trigger_value"] == field.get("firstOptionText")]
                        if matching_cascades:
                            field["cascade_actions"] = matching_cascades

            # ★ 将 close_dropdown_actions 附加到对应的 el-select 字段
            if close_dropdown_actions:
                for field in form_field_details:
                    if field.get("kb_category") == "el-select":
                        matching_closes = [c for c in close_dropdown_actions
                                           if c["field_label"] == field.get("label")]
                        if matching_closes:
                            field["close_dropdown"] = matching_closes

            # 检查必填字段是否填写成功
            # 核心逻辑：fill 完后重新读 DOM，检查必填字段是否已有值（包括默认值）
            unfilled_required = []
            field_states = await page.evaluate("""() => {
                const results = [];
                const formItems = document.querySelectorAll('.el-form-item, .ant-form-item');
                formItems.forEach((fi, idx) => {
                    const labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
                    const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
                    if (!label) return;

                    const required = fi.classList.contains('is-required') ||
                                     fi.querySelector('[class*="required"]') !== null;
                    if (!required) return;

                    const r = fi.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return;

                    // 检测组件类型和当前值
                    const selectEl = fi.querySelector('.el-select, .ant-select');
                    const radioGroup = fi.querySelector('.el-radio-group, .ant-radio-group');
                    const checkboxGroup = fi.querySelector('.el-checkbox-group, .ant-checkbox-group');

                    if (selectEl) {
                        // el-select: 检查 input 中显示的选中值
                        const inputEl = selectEl.querySelector('.el-input__inner, input');
                        const selectedText = inputEl ? inputEl.value.trim() : '';
                        // 多选模式：检查 el-tag（标签式选中项）
                        const tags = selectEl.querySelectorAll('.el-tag, .el-select__tags-text');
                        const tagTexts = Array.from(tags).map(t => t.textContent.trim()).filter(Boolean);
                        // 单选模式：检查 .el-select__selected-item
                        const selectedItem = selectEl.querySelector('.el-select__selected-item');
                        const selectedItemText = selectedItem ? selectedItem.textContent.trim() : '';
                        // 综合判定：input.value 或 tags 或 selected-item 任一非空即有值
                        const finalValue = selectedText || tagTexts.join(', ') || selectedItemText;
                        const isDisabled = selectEl.classList.contains('is-disabled') ||
                                           selectEl.querySelector('.is-disabled') !== null ||
                                           (inputEl && inputEl.disabled);
                        results.push({
                            label, type: 'select', value: finalValue,
                            isDisabled: isDisabled, hasValue: finalValue.length > 0,
                            isMultiSelect: tagTexts.length > 0
                        });
                    } else if (radioGroup) {
                        // 支持 el-radio (is-checked) 和 el-radio-button (is-active)
                        const checkedRadio = radioGroup.querySelector(
                            '.el-radio__input.is-checked + .el-radio__label, ' +
                            '.el-radio-button.is-active .el-radio-button__inner, ' +
                            '.ant-radio-wrapper-checked .ant-radio + span, ' +
                            'input[type="radio"]:checked + span'
                        );
                        const checkedText = checkedRadio ? checkedRadio.textContent.trim() : '';
                        results.push({
                            label, type: 'radio', value: checkedText,
                            isDisabled: false, hasValue: checkedText.length > 0
                        });
                    } else if (checkboxGroup) {
                        const checkedBoxes = checkboxGroup.querySelectorAll(
                            '.el-checkbox__input.is-checked + .el-checkbox__label, ' +
                            '.ant-checkbox-wrapper-checked'
                        );
                        const checkedTexts = Array.from(checkedBoxes).map(cb => cb.textContent.trim());
                        results.push({
                            label, type: 'checkbox', value: checkedTexts.join(', '),
                            isDisabled: false, hasValue: checkedTexts.length > 0
                        });
                    } else {
                        // 检查自定义穿梭框 (transfer-box)
                        const transferBox = fi.querySelector('[class*="transfer-box"]');
                        if (transferBox) {
                            // 在 transferBox 内查找右侧面板
                            let rightPanel = transferBox.querySelector('[class*="transfer-box-right"]');
                            // 如果 transferBox 实际匹配到的是 transfer-box-left（因为 left 也含 "transfer-box"），
                            // rightPanel 会为 null — 此时回退到 form-item 或页面级别查找
                            if (!rightPanel) {
                                rightPanel = fi.querySelector('[class*="transfer-box-right"]');
                            }
                            if (!rightPanel) {
                                rightPanel = document.querySelector('[class*="transfer-box-right"]');
                            }
                            const rightText = rightPanel ? rightPanel.textContent.trim() : '';
                            // 使用简单检测：右侧面板非空且不含 "0个" 即有选中项
                            const hasSelection = rightText.length > 0 && !rightText.includes('0个');
                            // 提取字符编码用于诊断
                            const charCodes = [];
                            for (let i = 0; i < Math.min(rightText.length, 30); i++) {
                                charCodes.push(rightText.charCodeAt(i));
                            }
                            results.push({
                                label, type: 'transfer', value: rightText.substring(0, 100),
                                isDisabled: false, hasValue: hasSelection,
                                _diag: rightPanel ? 'found' : 'NOT_FOUND',
                                _diagBoxCls: (transferBox.className || '').substring(0, 40),
                                _diagRightText: rightText.substring(0, 80),
                                _diagChildCount: rightPanel ? rightPanel.children.length : -1,
                                _diagHasSelection: hasSelection,
                                _diagTextLen: rightText.length,
                                _diagCharCodes: charCodes
                            });
                        } else {
                            // 普通 input / textarea
                            const inputEl = fi.querySelector('input:not([type="hidden"]), textarea');
                            const value = inputEl ? inputEl.value.trim() : '';
                            const isDisabled = inputEl ? inputEl.disabled : false;
                            results.push({
                                label, type: 'input', value: value,
                                isDisabled: isDisabled, hasValue: value.length > 0
                            });
                        }
                    }
                });
                return results;
            }""")

            nav_info["field_states"] = field_states
            LOG.info(f"    表单字段状态检查: [P4-V2-CODE]")
            LOG.info(f"    [P4-RAW] field_states keys: {[list(fs.keys()) for fs in field_states]}")
            for fs in field_states:
                status = "✅" if fs["hasValue"] else ("⏭️ disabled" if fs["isDisabled"] else "❌ 空")
                diag_info = ""
                if fs.get('_diag'):
                    diag_info = (f" _diag={fs.get('_diag', '')} _diagBoxCls={fs.get('_diagBoxCls', '')} "
                                 f"_diagHasSelection={fs.get('_diagHasSelection', 'N/A')} "
                                 f"_diagTextLen={fs.get('_diagTextLen', 'N/A')} "
                                 f"_diagCharCodes={fs.get('_diagCharCodes', 'N/A')}")
                LOG.info(f"      {fs['label']} ({fs['type']}): {status} value='{fs['value']}'{diag_info}")
                # RAW dump for transfer fields to debug missing keys
                if fs.get('type') == 'transfer':
                    LOG.info(f"      [RAW-transfer] full dict: {fs}")

            # === DIAG-transfer-field: 独立检测 transfer-box 状态 ===
            _any_transfer_empty = any(fs.get("type") == "transfer" and not fs.get("hasValue") for fs in field_states)
            if _any_transfer_empty:
                _transfer_diag = await page.evaluate("""() => {
                    const result = { formItems: [], standaloneTransfer: null };
                    // 检查每个 form-item 的内容
                    document.querySelectorAll('.el-form-item, .ant-form-item').forEach((fi, idx) => {
                        const labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
                        const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
                        const r = fi.getBoundingClientRect();
                        const required = fi.classList.contains('is-required') ||
                                         fi.querySelector('[class*="required"]') !== null;
                        const transferBox = fi.querySelector('[class*="transfer-box"]');
                        const transferBoxRight = fi.querySelector('[class*="transfer-box-right"]');
                        result.formItems.push({
                            idx, label, required,
                            visible: r.width > 0 && r.height > 0,
                            w: Math.round(r.width), h: Math.round(r.height),
                            hasTransferBox: !!transferBox,
                            hasTransferBoxRight: !!transferBoxRight,
                            rightText: transferBoxRight ? transferBoxRight.textContent.trim().substring(0, 80) : 'NOT_FOUND',
                            rightClass: transferBoxRight ? (transferBoxRight.className || '').substring(0, 60) : 'NOT_FOUND',
                            innerHTML: fi.innerHTML.substring(0, 200)
                        });
                    });
                    // 独立查找页面中的 transfer-box（不在 form-item 内）
                    const stTransfer = document.querySelector('[class*="transfer-box-right"]');
                    if (stTransfer) {
                        const parentForm = stTransfer.closest('.el-form-item, .ant-form-item');
                        result.standaloneTransfer = {
                            text: stTransfer.textContent.trim().substring(0, 80),
                            cls: (stTransfer.className || '').substring(0, 60),
                            inFormItem: !!parentForm,
                            parentTag: stTransfer.parentElement ? stTransfer.parentElement.tagName : 'none',
                            parentCls: stTransfer.parentElement ? (stTransfer.parentElement.className || '').substring(0, 60) : ''
                        };
                    }
                    return result;
                }""")
                import json as _json_td
                LOG.info(f"    [DIAG-transfer-field] {_json_td.dumps(_transfer_diag, ensure_ascii=False)}")

            # 收集 no_options 的字段标签（两轮都无选项 → 不计入必填失败）
            no_options_labels = {d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"}

            # 必填字段为空且非 disabled → 标记失败
            for fs in field_states:
                if not fs["hasValue"]:
                    if fs["isDisabled"]:
                        # disabled 的字段不需要填，跳过
                        LOG.info(f"    跳过 disabled 必填字段: {fs['label']}（有默认值或不可编辑）")
                        continue
                    if fs["label"] in no_options_labels:
                        # no_options 的字段不计入必填失败（API 可能无数据）
                        LOG.info(f"    跳过 no_options 必填字段: {fs['label']}（无可选数据）")
                        continue
                    # 非 disabled 且为空 → 真的填不上
                    unfilled_required.append(f"{fs['label']}（{fs['type']}，值为空）")

            if unfilled_required:
                LOG.warning(f"    必填字段未填写: {', '.join(unfilled_required)}")
                nav_info["error_type"] = "required_field_empty"
                nav_info["error_text"] = f"必填字段无法填写: {', '.join(unfilled_required)}"
                return nav_info

            LOG.info(f"    表单已填写，查找提交按钮...")

            # 查找提交按钮（优先级：确定 > 保存 > 提交）
            submit_btn = None
            for btn in toolbar_buttons + dialog_buttons:
                btn_text = btn.get("text", "")
                if const.normalize_button_text(btn_text) in const.SUBMIT_TEXTS_SET:
                    submit_btn = btn
                    break

            if not submit_btn:
                # === DIAGNOSTIC: dump 所有可见按钮和容器 ===
                _submit_check_js = const.js_normalize_in(const.SUBMIT_TEXTS, 'text')
                diag = await page.evaluate(f"""() => {{
                    const result = {{ allBtns: [], dialogs: [], submitLikeBtns: [] }};
                    document.querySelectorAll('button').forEach((btn, i) => {{
                        const r = btn.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) return;
                        const text = (btn.textContent || '').trim();
                        const parent = btn.closest('.el-dialog, .el-drawer, .el-message-box, .el-form, section');
                        result.allBtns.push({{
                            idx: i, text: text.substring(0, 30),
                            cls: (btn.className || '').substring(0, 60),
                            w: Math.round(r.width), h: Math.round(r.height),
                            parentTag: parent ? parent.tagName : 'none',
                            parentCls: parent ? (parent.className || '').substring(0, 60) : '',
                            disabled: btn.disabled
                        }});
                    }});
                    document.querySelectorAll('.el-dialog__wrapper, .el-drawer, .ant-modal-wrap').forEach(d => {{
                        const r = d.getBoundingClientRect();
                        result.dialogs.push({{
                            cls: (d.className || '').substring(0, 60),
                            visible: r.width > 0 && r.height > 0 && d.style.display !== 'none',
                            display: d.style.display
                        }});
                    }});
                    document.querySelectorAll('button').forEach(btn => {{
                        const r = btn.getBoundingClientRect();
                        if (r.width <= 0 || r.height <= 0) return;
                        const text = (btn.textContent || '').trim();
                        if ({_submit_check_js}) {{
                            result.submitLikeBtns.push({{
                                text: text, cls: (btn.className || '').substring(0, 60),
                                closest: btn.closest('.el-dialog, .el-drawer, .el-form-item') ? 'in-container' : 'standalone',
                                parentHTML: btn.parentElement ? btn.parentElement.outerHTML.substring(0, 150) : ''
                            }});
                        }}
                    }});
                    return result;
                }}""")
                import json as _json
                LOG.warning(f"    [DIAG-submit-PathA] toolbar_buttons={len(toolbar_buttons)}, dialog_buttons={len(dialog_buttons)}")
                _btns_a = [{"t": b.get("text","")[:15], "c": b.get("cls","")[:30], "p": b.get("parentTag","")} for b in diag.get("allBtns", [])]
                LOG.warning(f"    [DIAG-submit-PathA] 页面所有可见按钮({len(diag.get('allBtns', []))}): {_json.dumps(_btns_a, ensure_ascii=False)}")
                _sub_a = [{"t": b.get("text","")[:15], "cl": b.get("closest",""), "ph": b.get("parentHTML","")[:80]} for b in diag.get("submitLikeBtns", [])]
                LOG.warning(f"    [DIAG-submit-PathA] submitLikeBtns({len(diag.get('submitLikeBtns', []))}): {_json.dumps(_sub_a, ensure_ascii=False)}")
                _dlg_a = [{"c": d.get("cls","")[:40], "v": d.get("visible")} for d in diag.get("dialogs", [])]
                LOG.warning(f"    [DIAG-submit-PathA] dialogs({len(diag.get('dialogs', []))}): {_json.dumps(_dlg_a, ensure_ascii=False)}")
                # === END DIAGNOSTIC ===
                nav_info["error_type"] = "no_submit_button"
                nav_info["error_text"] = "未找到提交按钮"
                return nav_info
            btn_click_result = await _click_button_escalating(page, btn_text)

            if not btn_click_result["clicked"]:
                nav_info["error_type"] = "click_submit_failed"
                nav_info["error_text"] = f"无法点击提交按钮: {btn_text}"
                return nav_info

            # 等待前端验证完成（包括表单错误提示）
            await page.wait_for_timeout(1500)
            await wait_for_loading_complete(page)

            # DIAG: 提交后的 DOM 状态诊断
            _post_submit_diag = await page.evaluate("""() => {
                const result = {
                    messages: [],
                    notifications: [],
                    dialogs: [],
                    url: location.href
                };
                // 检查 el-message
                document.querySelectorAll('.el-message').forEach(m => {
                    if (m.offsetWidth > 0) {
                        result.messages.push({
                            text: (m.textContent || '').trim().substring(0, 50),
                            cls: (m.className || '').substring(0, 60)
                        });
                    }
                });
                // 检查 el-notification
                document.querySelectorAll('.el-notification').forEach(n => {
                    if (n.offsetWidth > 0) {
                        result.notifications.push({
                            text: (n.textContent || '').trim().substring(0, 50),
                            cls: (n.className || '').substring(0, 60)
                        });
                    }
                });
                // 检查弹窗
                document.querySelectorAll('.el-dialog__wrapper, .el-message-box__wrapper').forEach(d => {
                    if (d.offsetWidth > 0 && d.style.display !== 'none') {
                        result.dialogs.push({
                            cls: (d.className || '').substring(0, 60),
                            title: (d.querySelector('.el-dialog__title, .el-message-box__title') || {}).textContent || ''
                        });
                    }
                });
                return result;
            }""")
            import json as _json_submit
            LOG.info(f"    [DIAG-submit-after] 提交后状态: {_json_submit.dumps(_post_submit_diag, ensure_ascii=False)}")

            # 检查表单错误提示（前端验证失败）
            form_errors = await read_form_errors(page)
            if form_errors:
                error_texts = [err.get("text", "") for err in form_errors if err.get("text")]
                if error_texts:
                    LOG.warning(f"    表单验证失败: {', '.join(error_texts)}")
                    nav_info["error_type"] = "required_field_empty"
                    nav_info["error_text"] = f"表单验证失败: {', '.join(error_texts)}"
                    nav_info["form_errors"] = form_errors
                    return nav_info

            # 检查是否有确认对话框
            from core.discovery.ui_scanner.button_detector import _check_precondition_state
            state = await _check_precondition_state(page, {"type": "dialog"})
            if state["success"]:
                from core.discovery.replay.button_driver import confirm_dialog
                confirmed = await confirm_dialog(page)
                if confirmed:
                    LOG.info(f"    已点击确认按钮: {confirmed}")
                await page.wait_for_timeout(1000)

            # 验证提交是否成功（使用捕获的消息）
            captured_msgs = await _get_captured_messages(page)
            success = await _verify_operation_success(page, "submit", strict=True,
                                                       captured_messages=captured_msgs)
            nav_info["submit_result"] = {
                "success": success,
                "button_text": btn_text
            }

            if not success:
                nav_info["error_type"] = "submit_failed"
                nav_info["error_text"] = "提交后未检测到成功信号"

            return nav_info

        # 5. 使用与主页面相同的探测逻辑扫描所有表单字段（包括交互组件）
        LOG.info(f"    扫描所有表单字段（包括交互组件）...")
        form_filler = FormFiller(page)

        # 使用 scan_form_fields_v2 扫描所有表单字段（包括复选框、树形选择、穿梭框等）
        form_field_details = await form_filler.scan_form_fields_v2()

        if not form_field_details:
            # 确实没有任何可填写的字段，视为浏览/展示页面
            LOG.info(f"    无任何表单字段，视为浏览页面（操作成功）")
            nav_info["submit_result"] = {
                "success": True,
                "button_text": "",
                "action": "browse",
                "note": "页面跳转成功，无可填写字段（展示页面）"
            }
            return nav_info

        LOG.info(f"    扫描到 {len(form_field_details)} 个表单字段")

        # 6. 生成填充数据并填写表单
        fill_data = generate_fill_data(form_field_details, username="AT_test_auto")
        nav_info["fill_data"] = fill_data

        # 填写表单（普通字段 + 多步组件）
        fill_result = await form_filler.fill_create_form(form_field_details, "AT_test_auto", fill_data)
        filled_count = fill_result["filled"] if isinstance(fill_result, dict) else fill_result
        cascade_actions = fill_result.get("cascade_actions", []) if isinstance(fill_result, dict) else []

        # 处理 el-select 等多步组件
        ms_filled, ms_details, close_dropdown_actions = await form_filler.fill_multi_step_fields(form_field_details, "element-ui")
        filled_count += ms_filled

        LOG.info(f"    已填写 {filled_count} 个字段（含 {ms_filled} 个多步组件）")
        if cascade_actions:
            LOG.info(f"    发现 {len(cascade_actions)} 个 cascade 操作")
        if close_dropdown_actions:
            LOG.info(f"    发现 {len(close_dropdown_actions)} 个下拉框关闭操作")

        # ★ 将 cascade_actions 附加到对应的 radio 字段
        if cascade_actions:
            for field in form_field_details:
                if field.get("kb_category") == "radio":
                    matching_cascades = [c for c in cascade_actions
                                         if c["trigger_value"] == field.get("firstOptionText")]
                    if matching_cascades:
                        field["cascade_actions"] = matching_cascades

        # ★ 将 close_dropdown_actions 附加到对应的 el-select 字段
        if close_dropdown_actions:
            for field in form_field_details:
                if field.get("kb_category") == "el-select":
                    matching_closes = [c for c in close_dropdown_actions
                                       if c["field_label"] == field.get("label")]
                    if matching_closes:
                        field["close_dropdown"] = matching_closes

        # ★ 保存 form_field_details 到 nav_info（含 selector + kb_category，供 playbook 生成使用）
        nav_info["form_fields"] = form_field_details

        # 7. 检查必填字段是否填写成功（与主页面相同的逻辑）
        unfilled_required = []
        field_states = await page.evaluate("""() => {
            const results = [];
            const formItems = document.querySelectorAll('.el-form-item, .ant-form-item');
            formItems.forEach((fi, idx) => {
                const labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
                const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
                if (!label) return;

                const required = fi.classList.contains('is-required') ||
                                 fi.querySelector('[class*="required"]') !== null;
                if (!required) return;

                const r = fi.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;

                // 检测组件类型和当前值
                const selectEl = fi.querySelector('.el-select, .ant-select');
                const radioGroup = fi.querySelector('.el-radio-group, .ant-radio-group');
                const checkboxGroup = fi.querySelector('.el-checkbox-group, .ant-checkbox-group');
                const treeSelect = fi.querySelector('.el-tree-select, .ant-tree-select');
                const transfer = fi.querySelector('.el-transfer, .ant-transfer, [class*="transfer-box"]');

                if (selectEl) {
                    const inputEl = selectEl.querySelector('.el-input__inner, input');
                    const selectedText = inputEl ? inputEl.value.trim() : '';
                    // 多选模式：检查 el-tag（标签式选中项）
                    const tags = selectEl.querySelectorAll('.el-tag, .el-select__tags-text');
                    const tagTexts = Array.from(tags).map(t => t.textContent.trim()).filter(Boolean);
                    // 单选模式：检查 .el-select__selected-item
                    const selectedItem = selectEl.querySelector('.el-select__selected-item');
                    const selectedItemText = selectedItem ? selectedItem.textContent.trim() : '';
                    // 综合判定：input.value 或 tags 或 selected-item 任一非空即有值
                    const finalValue = selectedText || tagTexts.join(', ') || selectedItemText;
                    const isDisabled = selectEl.classList.contains('is-disabled') ||
                                       selectEl.querySelector('.is-disabled') !== null ||
                                       (inputEl && inputEl.disabled);
                    results.push({
                        label, type: 'select', value: finalValue,
                        isDisabled: isDisabled, hasValue: finalValue.length > 0,
                        isMultiSelect: tagTexts.length > 0
                    });
                } else if (treeSelect) {
                    const inputEl = treeSelect.querySelector('.el-input__inner, input');
                    const selectedText = inputEl ? inputEl.value.trim() : '';
                    const isDisabled = treeSelect.classList.contains('is-disabled');
                    results.push({
                        label, type: 'tree-select', value: selectedText,
                        isDisabled: isDisabled, hasValue: selectedText.length > 0
                    });
                } else if (radioGroup) {
                    // 支持 el-radio (is-checked) 和 el-radio-button (is-active)
                    const checkedRadio = radioGroup.querySelector(
                        '.el-radio__input.is-checked + .el-radio__label, ' +
                        '.el-radio-button.is-active .el-radio-button__inner, ' +
                        '.ant-radio-wrapper-checked .ant-radio + span, ' +
                        'input[type="radio"]:checked + span'
                    );
                    const checkedText = checkedRadio ? checkedRadio.textContent.trim() : '';
                    results.push({
                        label, type: 'radio', value: checkedText,
                        isDisabled: false, hasValue: checkedText.length > 0
                    });
                } else if (checkboxGroup) {
                    const checkedBoxes = checkboxGroup.querySelectorAll(
                        '.el-checkbox__input.is-checked + .el-checkbox__label, ' +
                        '.ant-checkbox-wrapper-checked'
                    );
                    const checkedTexts = Array.from(checkedBoxes).map(cb => cb.textContent.trim());
                    results.push({
                        label, type: 'checkbox', value: checkedTexts.join(', '),
                        isDisabled: false, hasValue: checkedTexts.length > 0
                    });
                } else if (transfer) {
                    // 穿梭框：检查右侧是否有选中项
                    // 标准 el-transfer / ant-transfer
                    const rightList = transfer.querySelector('.el-transfer__button + .el-transfer-panel, .ant-transfer-list:last-child');
                    const hasStandardItems = rightList && rightList.querySelectorAll('li').length > 0;
                    // 自定义 transfer-box：检查右侧面板
                    let customRight = transfer.querySelector('[class*="transfer-box-right"]');
                    if (!customRight) customRight = fi.querySelector('[class*="transfer-box-right"]');
                    if (!customRight) customRight = document.querySelector('[class*="transfer-box-right"]');
                    const customRightText = customRight ? customRight.textContent.trim() : '';
                    // 使用简单检测：右侧面板非空且不含 "0个" 即有选中项
                    const hasCustomItems = customRightText.length > 0 && !customRightText.includes('0个');
                    const hasItems = hasStandardItems || hasCustomItems;
                    const isDisabled = transfer.classList.contains('is-disabled');
                    const displayValue = hasCustomItems ? customRightText.substring(0, 100) : (hasItems ? 'has-items' : '');
                    results.push({
                        label, type: 'transfer', value: displayValue,
                        isDisabled: isDisabled, hasValue: hasItems,
                        _diag: customRight ? 'found' : 'NOT_FOUND',
                        _diagBoxCls: (transfer.className || '').substring(0, 40),
                        _diagRightText: customRightText.substring(0, 80)
                    });
                } else {
                    // 普通 input / textarea
                    const inputEl = fi.querySelector('input:not([type="hidden"]), textarea');
                    const value = inputEl ? inputEl.value.trim() : '';
                    const isDisabled = inputEl ? inputEl.disabled : false;
                    results.push({
                        label, type: 'input', value: value,
                        isDisabled: isDisabled, hasValue: value.length > 0
                    });
                }
            });
            return results;
        }""")

        nav_info["field_states"] = field_states
        LOG.info(f"    表单字段状态检查:")
        for fs in field_states:
            status = "✅" if fs["hasValue"] else ("⏭️ disabled" if fs["isDisabled"] else "❌ 空")
            diag_info = f" _diag={fs.get('_diag', '')} _diagBoxCls={fs.get('_diagBoxCls', '')} _diagRightText={fs.get('_diagRightText', '')}" if fs.get('_diag') else ""
            LOG.info(f"      {fs['label']} ({fs['type']}): {status} value='{fs['value']}'{diag_info}")
            # Radio 字段诊断：输出 DOM 结构
            if fs.get('type') == 'radio' and not fs.get('hasValue'):
                LOG.info(f"      [DIAG-radio] label='{fs['label']}' value='{fs['value']}' hasValue={fs['hasValue']}")
                # 获取该字段的 DOM 结构
                try:
                    radio_diag = await page.evaluate(f"""() => {{
                        const labels = document.querySelectorAll('.el-form-item__label');
                        for (const lbl of labels) {{
                            if (lbl.textContent.trim() === '{fs['label']}') {{
                                const formItem = lbl.closest('.el-form-item');
                                const radioGroup = formItem ? formItem.querySelector('.el-radio-group') : null;
                                if (!radioGroup) return {{ error: 'no radio-group' }};

                                const radios = radioGroup.querySelectorAll('.el-radio, .el-radio-button');
                                const result = {{ radioCount: radios.length, items: [] }};

                                radios.forEach((r, i) => {{
                                    const input = r.querySelector('input[type="radio"]');
                                    const isRadioButton = r.classList.contains('el-radio-button');
                                    const hasActive = r.classList.contains('is-active');
                                    const hasChecked = r.classList.contains('is-checked');
                                    const innerEl = r.querySelector('.el-radio__label, .el-radio-button__inner');
                                    const text = innerEl ? innerEl.textContent.trim() : '';

                                    result.items.push({{
                                        idx: i,
                                        type: isRadioButton ? 'button' : 'radio',
                                        text: text,
                                        checked: input ? input.checked : false,
                                        hasActive: hasActive,
                                        hasChecked: hasChecked,
                                        className: r.className
                                    }});
                                }});

                                return result;
                            }}
                        }}
                        return {{ error: 'label not found' }};
                    }}""")
                    LOG.info(f"      [DIAG-radio] DOM structure: {radio_diag}")
                except Exception as e:
                    LOG.info(f"      [DIAG-radio] error: {e}")

        # 收集 no_options 的字段标签（两轮都无选项 → 不计入必填失败）
        no_options_labels = {d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"}

        # 必填字段为空且非 disabled → 标记失败
        for fs in field_states:
            if not fs["hasValue"]:
                if fs["isDisabled"]:
                    LOG.info(f"    跳过 disabled 必填字段: {fs['label']}（有默认值或不可编辑）")
                    continue
                if fs["label"] in no_options_labels:
                    # no_options 的字段不计入必填失败（API 可能无数据）
                    LOG.info(f"    跳过 no_options 必填字段: {fs['label']}（无可选数据）")
                    continue
                unfilled_required.append(f"{fs['label']}（{fs['type']}，值为空）")

        if unfilled_required:
            LOG.warning(f"    必填字段未填写: {', '.join(unfilled_required)}")
            nav_info["error_type"] = "required_field_empty"
            nav_info["error_text"] = f"必填字段无法填写: {', '.join(unfilled_required)}"
            return nav_info

        LOG.info(f"    表单已填写，查找提交按钮...")

        # 8. 查找提交按钮（优先级：确定 > 保存 > 提交）
        submit_btn = None
        for btn in toolbar_buttons + dialog_buttons:
            btn_text = btn.get("text", "")
            if const.normalize_button_text(btn_text) in const.SUBMIT_TEXTS_SET:
                submit_btn = btn
                break

        if not submit_btn:
            # === DIAGNOSTIC: dump 所有可见按钮和容器 ===
            _submit_check_js = const.js_normalize_in(const.SUBMIT_TEXTS, 'text')
            diag = await page.evaluate(f"""() => {{
                const result = {{ allBtns: [], dialogs: [], submitLikeBtns: [] }};
                // 所有可见按钮
                document.querySelectorAll('button').forEach((btn, i) => {{
                    const r = btn.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return;
                    const text = (btn.textContent || '').trim();
                    const parent = btn.closest('.el-dialog, .el-drawer, .el-message-box, .el-form, section');
                    result.allBtns.push({{
                        idx: i, text: text.substring(0, 30),
                        cls: (btn.className || '').substring(0, 60),
                        w: Math.round(r.width), h: Math.round(r.height),
                        parentTag: parent ? parent.tagName : 'none',
                        parentCls: parent ? (parent.className || '').substring(0, 60) : '',
                        disabled: btn.disabled
                    }});
                }});
                // 检查弹窗/抽屉
                document.querySelectorAll('.el-dialog__wrapper, .el-drawer, .ant-modal-wrap').forEach(d => {{
                    const r = d.getBoundingClientRect();
                    result.dialogs.push({{
                        cls: (d.className || '').substring(0, 60),
                        visible: r.width > 0 && r.height > 0 && d.style.display !== 'none',
                        display: d.style.display
                    }});
                }});
                document.querySelectorAll('button').forEach(btn => {{
                    const r = btn.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return;
                    const text = (btn.textContent || '').trim();
                    if ({_submit_check_js}) {{
                        result.submitLikeBtns.push({{
                            text: text, cls: (btn.className || '').substring(0, 60),
                            closest: btn.closest('.el-dialog, .el-drawer, .el-form-item') ? 'in-container' : 'standalone',
                            parentHTML: btn.parentElement ? btn.parentElement.outerHTML.substring(0, 150) : ''
                        }});
                    }}
                }});
                return result;
            }}""")
            import json as _json2
            LOG.warning(f"    [DIAG-submit] toolbar_buttons={len(toolbar_buttons)}, dialog_buttons={len(dialog_buttons)}")
            _btns_b = [{"t": b.get("text","")[:15], "c": b.get("cls","")[:30], "p": b.get("parentTag","")} for b in diag.get("allBtns", [])]
            LOG.warning(f"    [DIAG-submit] 页面所有可见按钮({len(diag.get('allBtns', []))}): {_json2.dumps(_btns_b, ensure_ascii=False)}")
            _sub_b = [{"t": b.get("text","")[:15], "cl": b.get("closest",""), "ph": b.get("parentHTML","")[:80]} for b in diag.get("submitLikeBtns", [])]
            LOG.warning(f"    [DIAG-submit] submitLikeBtns({len(diag.get('submitLikeBtns', []))}): {_json2.dumps(_sub_b, ensure_ascii=False)}")
            _dlg_b = [{"c": d.get("cls","")[:40], "v": d.get("visible")} for d in diag.get("dialogs", [])]
            LOG.warning(f"    [DIAG-submit] dialogs({len(diag.get('dialogs', []))}): {_json2.dumps(_dlg_b, ensure_ascii=False)}")
            # === END DIAGNOSTIC ===
            nav_info["error_type"] = "no_submit_button"
            nav_info["error_text"] = "未找到提交按钮"
            return nav_info

        # === DIAG-P5-3: 提交前检查消息捕获是否存活 + 按钮状态 ===
        import json as _json_p5
        _btn_text_clean = btn_text.replace(chr(32), '').replace(' ', '') if btn_text else ''
        _btn_text_json = _json_p5.dumps(_btn_text_clean)
        _capture_alive = await page.evaluate(f"""() => {{
            const btns = document.querySelectorAll('button');
            let targetBtn = null;
            for (const b of btns) {{
                const t = (b.textContent || '').trim().replace(/\\s+/g, '');
                if (t.includes({_btn_text_json})) {{
                    targetBtn = b;
                    break;
                }}
            }}
            return {{
                hasCapture: typeof window.__captured_messages !== 'undefined',
                msgCount: typeof window.__captured_messages !== 'undefined'
                    ? window.__captured_messages.length : -1,
                url: location.href,
                btnDisabled: targetBtn ? targetBtn.disabled : 'not-found',
                btnCls: targetBtn ? (targetBtn.className || '').substring(0, 60) : 'not-found',
                btnVisible: targetBtn ? targetBtn.offsetWidth > 0 : false
            }};
        }}""")
        LOG.info(f"    [DIAG-submitB-pre] 提交前状态: {_json_p5.dumps(_capture_alive, ensure_ascii=False)}")

        # === DIAG-network: 安装网络请求拦截器，检测提交是否触发 API 调用 ===
        _captured_requests = []

        async def _on_request(request):
            url = request.url
            method = request.method
            if '/estack/api' in url and method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                _captured_requests.append({
                    'url': url,
                    'method': method,
                    'post_data': (request.post_data or '')[:200]
                })

        async def _on_response(response):
            url = response.url
            if '/estack/api' in url:
                _captured_requests.append({
                    'url': url,
                    'method': response.request.method,
                    'status': response.status
                })

        page.on('request', _on_request)
        page.on('response', _on_response)

        # === DIAG-submitB-vue: 点击前检查 Vue 实例和事件绑定 ===
        _vue_diag_pre = await page.evaluate(f"""() => {{
            const btns = document.querySelectorAll('button');
            let targetBtn = null;
            for (const b of btns) {{
                const t = (b.textContent || '').trim().replace(/\\s+/g, '');
                if (t.includes({_btn_text_json})) {{
                    targetBtn = b;
                    break;
                }}
            }}
            if (!targetBtn) return {{ found: false }};
            // 检查 Vue 实例
            const vueInstance = targetBtn.__vue__ || targetBtn.__vue_app__;
            // 检查 onclick 事件
            const hasOnClick = !!targetBtn.onclick;
            // 检查父元素的 Vue 实例
            const parentVue = targetBtn.parentElement ? (targetBtn.parentElement.__vue__ || targetBtn.parentElement.__vue_app__) : null;
            return {{
                found: true,
                hasVueInstance: !!vueInstance,
                hasParentVue: !!parentVue,
                hasOnClick: hasOnClick,
                tagName: targetBtn.tagName,
                className: (targetBtn.className || '').substring(0, 60),
                disabled: targetBtn.disabled,
                visible: targetBtn.offsetWidth > 0 && targetBtn.offsetHeight > 0
            }};
        }}""")
        LOG.info(f"    [DIAG-submitB-vue] 点击前 Vue 状态: {_json_p5.dumps(_vue_diag_pre, ensure_ascii=False)}")

        btn_click_result = await _click_button_escalating(page, btn_text)

        # === DIAG-P5-1: 点击结果详情 ===
        LOG.info(f"    [DIAG-submitB-click] 点击结果: clicked={btn_click_result.get('clicked')}, "
                 f"strategy='{btn_click_result.get('strategy')}', "
                 f"actual_text='{btn_click_result.get('actual_text')}', "
                 f"locator='{btn_click_result.get('locator', '')[:60]}'")
        LOG.info(f"    [DIAG-submitB-click] 点击后 URL: {page.url}")

        # === DIAG-P5-form-errors: 点击后立即检查表单验证错误 ===
        await page.wait_for_timeout(500)
        _form_errors_immediate = await page.evaluate("""() => {
            const errors = [];
            document.querySelectorAll('.el-form-item__error').forEach(el => {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    const formItem = el.closest('.el-form-item');
                    const label = formItem ? (formItem.querySelector('.el-form-item__label') || {}).textContent : '';
                    errors.push({
                        label: (label || '').trim().replace(/[：:]/g, ''),
                        text: el.textContent.trim().substring(0, 80),
                        visible: true
                    });
                }
            });
            // 也检查按钮状态
            const btns = document.querySelectorAll('button');
            const btnStates = [];
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (t.includes('确') && t.includes('定')) {
                    btnStates.push({
                        text: t,
                        disabled: b.disabled,
                        isDisabledClass: b.classList.contains('is-disabled'),
                        cls: (b.className || '').substring(0, 60)
                    });
                }
            }
            return { errors, btnStates };
        }""")
        LOG.info(f"    [DIAG-submitB-form-errors] 点击后表单错误: {_json_p5.dumps(_form_errors_immediate, ensure_ascii=False)}")

        if not btn_click_result["clicked"]:
            nav_info["error_type"] = "click_submit_failed"
            nav_info["error_text"] = f"无法点击提交按钮: {btn_text}"
            return nav_info

        # === DIAG-P5-instant: 点击后 200ms 检查瞬态 toast（可能在 wait 期间消失）===
        await page.wait_for_timeout(200)
        _instant_toast = await page.evaluate("""() => {
            const result = { messages: [], notifications: [], captureMsgs: [] };
            document.querySelectorAll('.el-message').forEach(m => {
                const r = m.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    result.messages.push({
                        text: m.textContent.trim().substring(0, 50),
                        cls: m.className.substring(0, 60),
                        opacity: window.getComputedStyle(m).opacity
                    });
                }
            });
            document.querySelectorAll('.el-notification').forEach(n => {
                const r = n.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) {
                    result.notifications.push({
                        text: n.textContent.trim().substring(0, 50),
                        cls: n.className.substring(0, 60)
                    });
                }
            });
            // 检查消息捕获
            if (typeof window.__captured_messages !== 'undefined') {
                result.captureMsgs = window.__captured_messages.map(m => ({
                    text: (m.text || m.message || '').substring(0, 50),
                    type: m.type || 'unknown'
                }));
            }
            return result;
        }""")
        LOG.info(f"    [DIAG-submitB-instant] 200ms 瞬态: {_json_p5.dumps(_instant_toast, ensure_ascii=False)}")

        # 等待前端验证完成（包括表单错误提示）
        await page.wait_for_timeout(1300)  # 已等 200ms，再等 1300ms 凑满 1500ms
        await wait_for_loading_complete(page)

        # === DIAG-P5-4: 提交后 DOM 弹窗/消息状态 ===
        _post_diag = await page.evaluate("""() => {
            const result = { messages: [], notifications: [], url: location.href, pageChanged: false };
            document.querySelectorAll('.el-message').forEach(m => {
                if (m.offsetWidth > 0) result.messages.push({
                    text: m.textContent.trim().substring(0, 50),
                    cls: m.className.substring(0, 60)
                });
            });
            document.querySelectorAll('.el-notification').forEach(n => {
                if (n.offsetWidth > 0) result.notifications.push({
                    text: n.textContent.trim().substring(0, 50),
                    cls: n.className.substring(0, 60)
                });
            });
            return result;
        }""")
        LOG.info(f"    [DIAG-submitB-after] 提交后 DOM: {_json_p5.dumps(_post_diag, ensure_ascii=False)}")

        # === DIAG-network: 输出拦截到的网络请求 ===
        try:
            page.remove_listener('request', _on_request)
            page.remove_listener('response', _on_response)
        except Exception:
            pass
        LOG.info(f"    [DIAG-submitB-network] 捕获到 {len(_captured_requests)} 个 API 请求:")
        for req in _captured_requests:
            if 'status' in req:
                LOG.info(f"      响应: {req['method']} {req['url'][-80:]} → {req['status']}")
            else:
                LOG.info(f"      请求: {req['method']} {req['url'][-80:]} post='{req.get('post_data', '')[:100]}'")

        # === 如果 Playwright click 未触发 API 请求，尝试 JS click 备选 ===
        if len(_captured_requests) == 0 and btn_click_result.get("clicked"):
            LOG.info(f"    [DIAG-submitB-js-retry] Playwright click 未触发 API，尝试 JS click...")
            # 重新安装网络监听
            _captured_requests.clear()
            page.on('request', _on_request)
            page.on('response', _on_response)

            # JS click
            _js_click_result = await page.evaluate(f"""() => {{
                const btns = document.querySelectorAll('button');
                for (const b of btns) {{
                    const t = (b.textContent || '').trim().replace(/\\s+/g, '');
                    if (t.includes({_btn_text_json})) {{
                        b.click();
                        return {{ clicked: true, tag: b.tagName, cls: (b.className || '').substring(0, 60) }};
                    }}
                }}
                return {{ clicked: false }};
            }}""")
            LOG.info(f"    [DIAG-submitB-js-retry] JS click 结果: {_json_p5.dumps(_js_click_result, ensure_ascii=False)}")

            # 等待 API 响应
            await page.wait_for_timeout(2000)
            await wait_for_loading_complete(page)

            # 检查网络请求
            try:
                page.remove_listener('request', _on_request)
                page.remove_listener('response', _on_response)
            except Exception:
                pass
            LOG.info(f"    [DIAG-submitB-js-retry] JS click 后捕获 {len(_captured_requests)} 个 API 请求:")
            for req in _captured_requests:
                if 'status' in req:
                    LOG.info(f"      响应: {req['method']} {req['url'][-80:]} → {req['status']}")
                else:
                    LOG.info(f"      请求: {req['method']} {req['url'][-80:]} post='{req.get('post_data', '')[:100]}'")

            # 更新 captured_msgs
            if len(_captured_requests) > 0:
                captured_msgs = await _get_captured_messages(page)
                LOG.info(f"    [DIAG-submitB-js-retry] JS click 后 captured_msgs: {[m.get('text','')[:30] for m in captured_msgs]}")

        # 检查表单错误提示（前端验证失败）
        form_errors = await read_form_errors(page)
        if form_errors:
            error_texts = [err.get("text", "") for err in form_errors if err.get("text")]
            if error_texts:
                LOG.warning(f"    表单验证失败: {', '.join(error_texts)}")
                nav_info["error_type"] = "form_validation_failed"
                nav_info["error_text"] = f"表单验证失败: {', '.join(error_texts)}"
                nav_info["form_errors"] = form_errors
                return nav_info

        # 检查是否有确认对话框
        from core.discovery.ui_scanner.button_detector import _check_precondition_state
        state = await _check_precondition_state(page, {"type": "dialog"})
        if state["success"]:
            from core.discovery.replay.button_driver import confirm_dialog
            confirmed = await confirm_dialog(page)
            if confirmed:
                LOG.info(f"    已点击确认按钮: {confirmed}")
            await page.wait_for_timeout(1000)

        # 10. 验证提交是否成功（使用捕获的消息）
        captured_msgs = await _get_captured_messages(page)
        # === DIAG-P5-2: captured_msgs 内容 ===
        LOG.info(f"    [DIAG-submitB-verify] captured_msgs({len(captured_msgs)}): "
                 f"{[m.get('text','')[:30] for m in captured_msgs]}")
        success = await _verify_operation_success(page, "submit", strict=True,
                                                   captured_messages=captured_msgs)
        # === DIAG-P5-2: verify 结果 ===
        LOG.info(f"    [DIAG-submitB-verify] verify结果: success={success}, strict=True")
        nav_info["submit_result"] = {
            "success": success,
            "button_text": btn_text
        }

        if not success:
            nav_info["error_type"] = "no_success_signal"
            nav_info["error_text"] = "提交后未检测到成功提示"

        return nav_info

    except Exception as e:
        error_str = str(e)
        LOG.warning(f"    探索新页面失败: {error_str[:80]}")

        # 检测是否是因为打开新标签页导致 page 对象失效
        if "Target page" in error_str or "has been closed" in error_str:
            LOG.warning(f"    页面已关闭（可能打开了新标签页）")
            nav_info["error_type"] = "page_closed_new_tab"
            nav_info["error_text"] = "操作打开了新标签页，当前 page 对象已失效"
        else:
            nav_info["error_type"] = "exception"
            nav_info["error_text"] = error_str[:200]

        return nav_info


async def _handle_cross_page_operation(page, context: dict, result: dict) -> dict:
    """处理跨页面操作的委托处理器。

    当 _do_generic_operation 检测到页面跳转并返回 navigation_unexplored 时，
    _error_driven_retry 会委托此函数处理。

    Args:
        page: Playwright Page 对象
        context: 操作上下文
        result: _do_generic_operation 返回的结果（包含 nav_info）

    Returns:
        dict: 最终操作结果
    """
    nav_info = result.get("nav_info", {})
    btn = context.get("btn", {})
    btn_text = btn.get("text", "")

    # 从 nav_info 中提取子操作结果
    sub_ops = nav_info.get("sub_operations", [])

    if not sub_ops:
        # 没有子操作，标记为部分失败
        return make_error("cross_page_no_operations", "跨页面操作未找到可执行的子操作",
                         nav_info=nav_info,
                         selectors={"trigger": btn_text})

    # 统计子操作成功率
    success_count = sum(1 for op in sub_ops if op.get("success", False))
    total_count = len(sub_ops)

    if success_count == total_count:
        # 所有子操作都成功
        LOG.info(f"    跨页面操作全部成功: {success_count}/{total_count}")
        return make_success(nav_info=nav_info,
                           sub_operations=sub_ops,
                           selectors={"trigger": btn_text})
    elif success_count > 0:
        # 部分成功
        LOG.warning(f"    跨页面操作部分成功: {success_count}/{total_count}")
        return make_error("cross_page_partial", f"跨页面操作部分失败: {success_count}/{total_count} 成功",
                         nav_info=nav_info,
                         sub_operations=sub_ops,
                         selectors={"trigger": btn_text})
    else:
        # 全部失败
        LOG.error(f"    跨页面操作全部失败: 0/{total_count}")
        return make_error("cross_page_failed", f"跨页面操作全部失败: 0/{total_count}",
                         nav_info=nav_info,
                         sub_operations=sub_ops,
                         selectors={"trigger": btn_text})


async def _navigate_back_to_list(page, original_url: str):
    """安全导航回原始列表页。

    优先使用 page.goto()，失败则尝试 page.go_back()。

    Args:
        page: Playwright Page 对象
        original_url: 原始列表页 URL
    """
    try:
        await page.goto(original_url, wait_until="domcontentloaded", timeout=30000)
        await wait_for_table_ready(page, timeout=15000)
        LOG.info(f"    已导航回原始页面: {original_url[:60]}")
    except Exception as e1:
        LOG.warning(f"    page.goto 失败: {e1}，尝试 page.go_back()")
        try:
            await page.go_back()
            await wait_for_navigation_complete(page)
            await wait_for_table_ready(page, timeout=15000)
            LOG.info(f"    已通过 go_back 返回")
        except Exception as e2:
            LOG.error(f"    导航回原始页面失败: {e2}")
