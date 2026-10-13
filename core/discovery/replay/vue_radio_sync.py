"""
vue_radio_sync.py — Element UI Radio Vue v-model 同步工具

Element UI + Vue 2 的 radio 组件在 Playwright click 后可能不触发 Vue v-model 更新。
本模块提供统一的强制同步逻辑，供 Stage 1（form_filler）和 Stage 2（replay_engine）共享。

同步策略：
1. Playwright click radio wrapper
2. 检查 Vue v-model 状态（诊断用）
3. 通过 __vue__ API 强制更新 v-model：
   - set value / $emit input / handleChange / $forceUpdate
   - 向上遍历 $parent 链找到页面组件，更新 form model
   - $nextTick 清除表单验证状态
"""

import logging

LOG = logging.getLogger(__name__)

# ============================================================
# JavaScript 片段
# ============================================================

# 检查 radio 点击后 Vue v-model 是否已更新
_VUE_CHECK_JS = """(text) => {
    const radios = document.querySelectorAll('.el-radio, .el-radio-button');
    for (const radio of radios) {
        if (radio.textContent.includes(text)) {
            const input = radio.querySelector('input[type="radio"]');
            const isRadioBtn = radio.classList.contains('el-radio-button');
            return {
                inputChecked: input ? input.checked : false,
                hasIsChecked: radio.classList.contains('is-checked'),
                hasIsActive: radio.classList.contains('is-active'),
                isRadioButton: isRadioBtn,
                radioClass: radio.className,
                inputValue: input ? input.value : null
            };
        }
    }
    return null;
}"""

# 通过 __vue__ API 强制更新 radio v-model
# 包含：直接赋值、$emit、handleChange、$forceUpdate、parent chain form model 更新、验证清除
_VUE_FORCE_UPDATE_JS = """async (text) => {
    const result = { found: false, methods: [] };
    const radios = document.querySelectorAll('.el-radio, .el-radio-button');
    for (const radio of radios) {
        if (radio.textContent.includes(text)) {
            const input = radio.querySelector('input[type="radio"]');
            if (!input) { result.error = 'no input'; return result; }

            result.found = true;
            result.inputValue = input.value;

            // 找到 radio-group 父元素
            const radioGroup = radio.closest('.el-radio-group');
            if (!radioGroup) { result.error = 'no radioGroup'; return result; }

            // 获取 Vue 实例
            const vueGroup = radioGroup.__vue__;
            if (!vueGroup) { result.error = 'no __vue__'; return result; }

            // 记录更新前的值
            result.beforeValue = vueGroup.value;

            const targetValue = input.value;

            // 方法 1: 直接设置 value 属性
            vueGroup.value = targetValue;
            result.methods.push('set_value');

            // 方法 2: $emit input 事件
            vueGroup.$emit('input', targetValue);
            result.methods.push('emit_input');

            // 方法 3: 触发 radio 组件的 handleChange
            const radioVue = radio.__vue__;
            if (radioVue && typeof radioVue.handleChange === 'function') {
                radioVue.handleChange();
                result.methods.push('handleChange');
            }

            // 方法 4: $forceUpdate
            vueGroup.$forceUpdate();
            result.methods.push('forceUpdate');

            // 记录更新后的值
            result.afterValue = vueGroup.value;

            // 向上查找 form-item 的 Vue 实例，触发 validate
            const formItem = radioGroup.closest('.el-form-item');
            if (formItem && formItem.__vue__) {
                formItem.__vue__.$emit('el.form.change', targetValue);
                result.methods.push('form_change');
            }

            // 向上遍历 $parent 链，找到页面组件的 form model
            let current = vueGroup.$parent;
            let depth = 0;
            result.parentChain = [];

            const propAttr = formItem ? formItem.getAttribute('prop') : null;
            result.formItemProp = propAttr;

            while (current && depth < 10) {
                const componentName = current.$options && current.$options.name ? current.$options.name : 'anonymous';
                const dataKeys = current.$data ? Object.keys(current.$data) : [];
                result.parentChain.push({
                    name: componentName,
                    dataKeys: dataKeys.slice(0, 10),
                    depth: depth
                });

                // 跳过 Element UI 内部组件
                if (componentName && componentName.startsWith('El')) {
                    current = current.$parent;
                    depth++;
                    continue;
                }

                // 查找包含表单数据的组件
                const hasFormData = dataKeys.some(k =>
                    k.toLowerCase().includes('form') ||
                    k.toLowerCase().includes('model') ||
                    k.toLowerCase().includes('data')
                );

                if (hasFormData && dataKeys.length > 2) {
                    result.pageComponent = componentName;
                    result.pageComponentKeys = dataKeys;

                    const formFields = ['form', 'formData', 'model', 'formModel', 'ruleForm', 'data'];
                    for (const field of formFields) {
                        if (current.$data && current.$data[field]) {
                            const formObj = current.$data[field];
                            if (typeof formObj === 'object' && formObj !== null) {
                                result[field + '_keys'] = Object.keys(formObj);

                                // 策略 1: 如果 formItem 有 prop 属性，直接匹配
                                if (propAttr && propAttr in formObj) {
                                    const oldValue = formObj[propAttr];
                                    formObj[propAttr] = targetValue;
                                    result.updatedField = field + '.' + propAttr;
                                    result.oldValue = oldValue;
                                    result.newValue = targetValue;
                                    result.matchStrategy = 'prop_exact';
                                    result.methods.push('page_form_update');
                                    break;
                                }

                                // 策略 2: 用 radio 选项文本匹配字段名
                                const labelLower = (text || '').toLowerCase();
                                const formKeys = Object.keys(formObj);
                                let matched = false;

                                for (const key of formKeys) {
                                    const keyLower = key.toLowerCase();
                                    if (keyLower === labelLower ||
                                        keyLower.includes(labelLower) ||
                                        (labelLower.length > 2 && labelLower.includes(keyLower))) {
                                        const oldValue = formObj[key];
                                        formObj[key] = targetValue;
                                        result.updatedField = field + '.' + key;
                                        result.oldValue = oldValue;
                                        result.newValue = targetValue;
                                        result.matchStrategy = 'label_fuzzy';
                                        result.methods.push('page_form_update');
                                        matched = true;
                                        break;
                                    }
                                }

                                if (matched) break;
                            }
                        }
                    }

                    // 使用 Vue.$nextTick 确保在响应式更新后清除验证
                    await new Promise((resolve) => {
                        vueGroup.$nextTick(() => {
                            const allFormItems = document.querySelectorAll('.el-form-item');
                            allFormItems.forEach(fi => {
                                if (fi.__vue__) {
                                    fi.__vue__.validateState = 'success';
                                    fi.__vue__.validateMessage = '';
                                }
                            });
                            result.methods.push('nextTick_formItems_clear');

                            // 向上找 ElForm 并清除验证
                            let searchVue = vueGroup.$parent;
                            let sd = 0;
                            while (searchVue && sd < 15) {
                                if (searchVue.$options && searchVue.$options.name === 'ElForm') {
                                    searchVue.validateOnRuleChange = false;
                                    if (typeof searchVue.clearValidate === 'function') {
                                        searchVue.clearValidate();
                                        result.methods.push('nextTick_form_clearValidate');
                                    }
                                    break;
                                }
                                searchVue = searchVue.$parent;
                                sd++;
                            }
                            resolve();
                        });
                    });

                    break;
                }

                current = current.$parent;
                depth++;
            }

            break;
        }
    }
    return result;
}"""


# ============================================================
# Python API
# ============================================================

async def sync_radio_vue_model(page, option_text: str) -> dict:
    """同步 Element UI radio 的 Vue v-model 状态。

    Element UI + Vue 2 环境下，Playwright click 后 DOM class 可能更新
    但 Vue v-model 不一定同步。此函数通过 __vue__ 内部 API 强制更新。

    Args:
        page: Playwright Page 对象
        option_text: radio 选项的文本内容

    Returns:
        dict: Vue 更新结果，包含:
            - found (bool): 是否找到目标 radio
            - methods (list): 执行的更新方法
            - beforeValue / afterValue: 更新前后的 v-model 值
            - error (str, optional): 错误信息
    """
    if not option_text:
        return {"found": False, "methods": [], "error": "empty option_text"}

    try:
        vue_check = await page.evaluate(_VUE_CHECK_JS, option_text)
        LOG.info(f"      [Radio] Vue 点击后状态: {vue_check}")

        vue_update_result = await page.evaluate(_VUE_FORCE_UPDATE_JS, option_text)
        LOG.info(f"      [Radio] Vue v-model 更新结果: {vue_update_result}")

        return vue_update_result
    except Exception as e:
        LOG.warning(f"      [Radio] Vue v-model 同步失败: {e}")
        return {"found": False, "methods": [], "error": str(e)}
