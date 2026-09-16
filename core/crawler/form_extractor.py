"""
P2 功能面探测 —— Element UI 表单字段结构抽取 (form_extractor)
对应方案设计 §4.2 四步法中的第①步与第②步。

用途:
  点击"新建"按钮后, 弹窗中的 el-form-item 字段(label/placeholder/必填星号/控件类型/枚举选项)
  被抽取为 function_surface.json, 侧面补充参数语义(中文名/必填性/枚举)。
  同时在下拉框加载时捕获 XHR 请求(前置依赖清单, 见 §7.3 G3)。

设计: 纯 evaluate 无浏览器重启, 作为 playwright_crawl.py 巡游中"新建"按钮流程的内嵌步骤。
"""
import json
import logging
from pathlib import Path

LOG = logging.getLogger("form_extractor")


async def extract_el_dialog_fields(page, captures: list, module_label: str) -> dict:
    """
    在新建弹窗可见时, 扫描 el-form-item 列表, 返回字段结构。
    同时检测下拉框加载时的 XHR 请求(前置接口)。
    """
    fields = await page.evaluate("""() => {
        // 查找可见的 el-dialog
        const dialogs = document.querySelectorAll('.el-dialog');
        const visibleDialog = Array.from(dialogs).find(d => {
            const style = window.getComputedStyle(d);
            return style.display !== 'none' && style.visibility !== 'hidden' && d.offsetWidth > 0;
        });
        if (!visibleDialog) return { ok: false, reason: 'no_visible_dialog' };

        const result = { ok: true, dialog_title: '', fields: [] };

        // 对话框标题
        const titleEl = visibleDialog.querySelector('.el-dialog__title, .el-dialog__header');
        if (titleEl) result.dialog_title = (titleEl.textContent || '').trim();

        // 扫描 el-form-item
        const formItems = visibleDialog.querySelectorAll('.el-form-item');
        formItems.forEach(item => {
            const labelEl = item.querySelector('.el-form-item__label');
            const label = labelEl ? (labelEl.textContent || '').trim() : '';

            // 必填标记(el-form-item 有 is-required class / label 前有红色 *)
            const isRequired = item.classList.contains('is-required')
                || (labelEl && labelEl.querySelector('.is-required, [style*=\"color: red\"], [style*=\"color:#FF\"]'));

            // 控件类型: input / select / switch / radio / checkbox / date-picker / cascader
            const input = item.querySelector('input:not([type=\"hidden\"])');
            const select = item.querySelector('.el-select');
            const textarea = item.querySelector('textarea');
            const switchEl = item.querySelector('.el-switch');
            const radioGroup = item.querySelector('.el-radio-group');
            const checkGroup = item.querySelector('.el-checkbox-group');
            const datePicker = item.querySelector('.el-date-editor');
            const cascader = item.querySelector('.el-cascader');

            let fieldType = 'text';
            let placeholder = '';
            let options = [];

            if (select) {
                fieldType = 'select';
                // 下拉选项(el-option)
                const opts = select.querySelectorAll('.el-select-dropdown__item, el-option');
                // 注: el-option 选项在首次打开前可能尚未渲染; 先取已渲染的
                opts.forEach(o => {
                    const txt = (o.textContent || '').trim();
                    if (txt) options.push(txt);
                });
                // 如果选项列表为空, 从 el-select 的 placeholder 获取提示
                const selInput = select.querySelector('input');
                if (selInput) placeholder = selInput.placeholder || '';
            } else if (input) {
                fieldType = input.type || 'text';
                placeholder = input.placeholder || '';
            } else if (textarea) {
                fieldType = 'textarea';
                placeholder = textarea.placeholder || '';
            } else if (switchEl) {
                fieldType = 'switch';
            } else if (radioGroup) {
                fieldType = 'radio';
                radioGroup.querySelectorAll('.el-radio, .el-radio__label').forEach(r => {
                    const txt = (r.textContent || '').trim();
                    if (txt) options.push(txt);
                });
            } else if (checkGroup) {
                fieldType = 'checkbox';
                checkGroup.querySelectorAll('.el-checkbox, .el-checkbox__label').forEach(c => {
                    const txt = (c.textContent || '').trim();
                    if (txt) options.push(txt);
                });
            } else if (datePicker) {
                fieldType = 'date';
                const dpInput = datePicker.querySelector('input');
                if (dpInput) placeholder = dpInput.placeholder || '';
            } else if (cascader) {
                fieldType = 'cascader';
                const caInput = cascader.querySelector('input');
                if (caInput) placeholder = caInput.placeholder || '';
            }

            const field = { label, fieldType, isRequired, placeholder };
            if (options.length > 0) field.options = options;
            result.fields.push(field);
        });

        // 检测是否有下拉框正在加载中(未渲染选项)
        const selectsWithoutOptions = visibleDialog.querySelectorAll('.el-select:not(:has(.el-select-dropdown__item))');
        result.selects_loading = selectsWithoutOptions.length;

        return result;
    }""")

    LOG.info(f"  表单弹窗: title={fields.get('dialog_title','')}, "
             f"fields={len(fields.get('fields',[]))}, "
             f"selects_loading={fields.get('selects_loading',0)}")

    if fields.get("ok") and fields.get("fields"):
        # 写入 function_surface.json (增量追加)
        surface_entry = {
            "module": module_label,
            "dialog_title": fields.get("dialog_title", ""),
            "fields": fields["fields"],
            "selects_loading": fields.get("selects_loading", 0),
        }
    else:
        LOG.warning(f"  表单字段抽取失败: {fields.get('reason','')}")

    return fields
