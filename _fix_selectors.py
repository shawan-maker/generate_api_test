"""Apply dual selector field replacements to form_filler.py"""
import re

with open('core/discovery/replay/form_filler.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    # 1. ip-input fields.push (first occurrence)
    ("""                        fields.push({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            selector: _buildComponentSelector(fi, '{form_item}', fields.length, label),
                            subSelectors: subSelectors,
                            inputCount: independentInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }});""",
     """                        fields.push(Object.assign({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            subSelectors: subSelectors,
                            inputCount: independentInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }}, _buildComponentSelector(fi, '{form_item}', fields.length, label)));"""),

    # 2. mask select
    ("""                        fields.push({{
                            label: label + '(掩码)', type: 'select', kb_category: 'el-select',
                            selector: _buildSelector(selects[0], label + '(掩码)'),
                            required,
                        }});""",
     """                        fields.push(Object.assign({{
                            label: label + '(掩码)', type: 'select', kb_category: 'el-select',
                            required,
                        }}, _buildSelector(selects[0], label + '(掩码)')));"""),

    # 3. independentInputs forEach
    ("""                independentInputs.forEach(inp => {{
                    fields.push({{ label, type: 'input', kb_category: 'input-generic',
                                 selector: _buildSelector(inp, label),
                                 inputType: inp.type || 'text', required }});
                }});""",
     """                independentInputs.forEach(inp => {{
                    fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                 inputType: inp.type || 'text', required }}, _buildSelector(inp, label)));
                }});"""),

    # 4. cascader
    ("""                fields.push({{ label, type: 'cascader', kb_category: 'el-cascader',
                             selector: _buildComponentSelector(fi, '{cascader_container}', cascaderIdx, label), required }});""",
     """                fields.push(Object.assign({{ label, type: 'cascader', kb_category: 'el-cascader',
                             required }}, _buildComponentSelector(fi, '{cascader_container}', cascaderIdx, label)));"""),

    # 5. date-picker
    ("""                fields.push({{ label, type: 'date-picker', kb_category: 'date-picker',
                             selector: _buildComponentSelector(fi, '{date_picker}', datePickerIdx, label), required }});""",
     """                fields.push(Object.assign({{ label, type: 'date-picker', kb_category: 'date-picker',
                             required }}, _buildComponentSelector(fi, '{date_picker}', datePickerIdx, label)));"""),

    # 6. multi-select with _buildSelector result
    ("""                    const selSelector = _buildSelector(sel, label + labelSuffix);

                    fields.push({{ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 selector: selSelector, required }});""",
     """                    const selSelectors = _buildSelector(sel, label + labelSuffix);

                    fields.push(Object.assign({{ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 required }}, selSelectors));"""),

    # 7. radio
    ("""                fields.push({{ label, type: 'radio', kb_category: 'radio',
                             selector: _buildComponentSelector(fi, '{radio_group}', radioIdx, label), required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' }});""",
     """                fields.push(Object.assign({{ label, type: 'radio', kb_category: 'radio',
                             required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' }}, _buildComponentSelector(fi, '{radio_group}', radioIdx, label)));"""),

    # 8. checkbox
    ("""                fields.push({{ label, type: 'checkbox', kb_category: 'form-checkbox',
                             selector: _buildComponentSelector(fi, '{checkbox_container}', checkboxIdx, label), required }});""",
     """                fields.push(Object.assign({{ label, type: 'checkbox', kb_category: 'form-checkbox',
                             required }}, _buildComponentSelector(fi, '{checkbox_container}', checkboxIdx, label)));"""),

    # 9. list-selector
    ("""                    fields.push({{
                        label, type: 'list-selector',
                        kb_category: 'list-selector',
                        selector: _buildComponentSelector(fi, listPanel ? listPanel.tagName.toLowerCase() : 'div', listIdx, label),
                        required,
                        itemCount: listItems.length
                    }});""",
     """                    fields.push(Object.assign({{
                        label, type: 'list-selector',
                        kb_category: 'list-selector',
                        required,
                        itemCount: listItems.length
                    }}, _buildComponentSelector(fi, listPanel ? listPanel.tagName.toLowerCase() : 'div', listIdx, label)));"""),

    # 10. ip-input v2 path
    ("""                        fields.push({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            selector: _buildComponentSelector(fi, '{form_item}', fields.length, label),
                            subSelectors: subSelectors,
                            inputCount: allInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }});""",
     """                        fields.push(Object.assign({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            subSelectors: subSelectors,
                            inputCount: allInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }}, _buildComponentSelector(fi, '{form_item}', fields.length, label)));"""),

    # 11. mainInput (v2)
    ("""                    fields.push({{ label, type: 'input', kb_category: 'input-generic',
                                     selector: _buildSelector(mainInput, label),
                                     inputType: mainInput.type || 'text', required }});""",
     """                    fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                     inputType: mainInput.type || 'text', required }}, _buildSelector(mainInput, label)));"""),

    # 12. inp (v2 single input)
    ("""                    fields.push({{ label, type: 'input', kb_category: 'input-generic',
                                     selector: _buildSelector(inp, label),
                                     inputType: inp.type || 'text', required }});""",
     """                    fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                     inputType: inp.type || 'text', required }}, _buildSelector(inp, label)));"""),

    # 13. textarea (v2)
    ("""                    fields.push({{ label, type: 'textarea', kb_category: 'textarea-generic',
                                 selector: _buildSelector(ta, label), required }});""",
     """                    fields.push(Object.assign({{ label, type: 'textarea', kb_category: 'textarea-generic',
                                 required }}, _buildSelector(ta, label)));"""),

    # 14. v2 input-generic (main path)
    ("""                const fieldEntry = {{
                    label, type: 'input', kb_category: 'input-generic',
                    selector: _buildSelector(inp, label),
                    inputType: isNumber ? 'number' : (inp.type || 'text'),
                    required: false,
                    visible_index: currentVisibleIndex  // ★ 在 scope 内的可见字段顺序""",
     """                const selResult = _buildSelector(inp, label);
                const fieldEntry = {{
                    label, type: 'input', kb_category: 'input-generic',
                    selector: selResult.selector,
                    playwright_locator: selResult.playwright_locator,
                    inputType: isNumber ? 'number' : (inp.type || 'text'),
                    required: false,
                    visible_index: currentVisibleIndex  // ★ 在 scope 内的可见字段顺序"""),

    # 15. el-select in v2
    ("""                        fields.push({{
                            label: label,
                            type: 'select',
                            kb_category: 'el-select',
                            selector: _buildComponentSelector(fi, '{select_container}', selectIdx, label),
                            required: fi.classList.contains('is-required') ||
                                       fi.querySelector('[class*="required"]') !== null,
                        }});""",
     """                        fields.push(Object.assign({{
                            label: label,
                            type: 'select',
                            kb_category: 'el-select',
                            required: fi.classList.contains('is-required') ||
                                       fi.querySelector('[class*="required"]') !== null,
                        }}, _buildComponentSelector(fi, '{select_container}', selectIdx, label)));"""),
]

count = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        count += 1
    else:
        print(f"WARN: Pattern {count+1} not found!")
    count += 1

with open('core/discovery/replay/form_filler.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"Applied {count} replacements")
