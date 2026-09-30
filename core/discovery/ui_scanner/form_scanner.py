"""
form_scanner.py — Form field scanning.

Extracted from discover_ui.py. Contains functions for deep scanning of
individual form items and resolving label-based selectors for form inputs.
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

LOG = logging.getLogger("form_scanner")

# 全局知识库实例
_kb: ProbeKB = None


async def _deep_scan_form_item(page, field_label: str) -> dict:
    """深度扫描指定 form-item，识别复合组件结构。

    当 fill_overrides 需要精确填充时调用，用于处理复合组件
    （如手机号 = el-select 国家编码 + input 手机号）。

    Args:
        page: Playwright Page 对象
        field_label: 字段标签文本

    Returns:
        {
            "found": bool,
            "is_composite": bool,
            "elements": [{"type": "input/select", "selector": "...", "index": N}, ...]
        }
    """
    try:
        result = await page.evaluate(f"""(label) => {{
            const formItems = document.querySelectorAll('.el-form-item');
            for (const fi of formItems) {{
                const labelEl = fi.querySelector('.el-form-item__label');
                if (!labelEl) continue;
                const labelText = labelEl.textContent.trim().replace(/[：:]/g, '');
                if (labelText !== label) continue;

                // 找到目标 form-item，分析其结构
                const selectEl = fi.querySelector('.el-select, .ant-select');
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                ));

                const elements = [];
                const independentInputs = [];

                // 辅助函数：基于 DOM 路径生成 selector
                function buildSelector(el) {{
                    if (el.id) return `#${{el.id}}`;
                    if (el.getAttribute('name')) return `[name="${{el.getAttribute('name')}}"]`;
                    const path = [];
                    let current = el;
                    while (current && current.nodeType === 1) {{
                        let tag = current.tagName.toLowerCase();
                        if (current.id) {{
                            path.unshift(`#${{current.id}}`);
                            break;
                        }}
                        const parent = current.parentElement;
                        if (parent) {{
                            const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                            if (siblings.length > 1) {{
                                const idx = siblings.indexOf(current) + 1;
                                tag += `:nth-of-type(${{idx}})`;
                            }}
                        }}
                        path.unshift(tag);
                        if (path.length >= 5) break;
                        current = current.parentElement;
                    }}
                    return path.join(' > ');
                }}

                if (selectEl) {{
                    // 有 select 组件
                    elements.push({{ type: 'select', index: 0, selector: buildSelector(selectEl) }});

                    // 查找不在 select 内部的独立 input
                    allInputs.forEach((inp, idx) => {{
                        if (!selectEl.contains(inp) && !inp.readOnly && inp.offsetWidth > 0) {{
                            independentInputs.push(inp);
                            elements.push({{ type: 'input', index: elements.length, selector: buildSelector(inp) }});
                        }}
                    }});

                    return {{
                        found: true,
                        is_composite: independentInputs.length > 0,
                        elements: elements
                    }};
                }} else {{
                    // 没有 select，只有普通 input
                    allInputs.forEach((inp, idx) => {{
                        if (!inp.readOnly && inp.offsetWidth > 0) {{
                            elements.push({{ type: 'input', index: elements.length, selector: buildSelector(inp) }});
                        }}
                    }});

                    return {{
                        found: true,
                        is_composite: false,
                        elements: elements
                    }};
                }}
            }}
            return {{ found: false, is_composite: false, elements: [] }};
        }}""", field_label)
        return result
    except Exception as e:
        LOG.warning(f"    深度扫描 {field_label} 失败: {e}")
        return {"found": False, "is_composite": False, "elements": []}


async def _resolve_label_selector(page, label: str) -> str | None:
    """根据 label 文本找到对应 input，返回基于 DOM 路径的 selector（无 :has() 伪类）。"""
    try:
        return await page.evaluate(f"""(label) => {{
            const formItems = document.querySelectorAll('.el-form-item, .ant-form-item');
            for (const fi of formItems) {{
                const labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
                if (!labelEl) continue;
                const labelText = labelEl.textContent.trim().replace(/[：:]/g, '');
                if (labelText !== label) continue;

                // 找到目标 form-item，获取可见 input
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                )).filter(inp => !inp.readOnly && inp.offsetWidth > 0);

                const target = allInputs.length > 0 ? allInputs[allInputs.length - 1] : fi.querySelector('input, textarea');
                if (!target) return null;

                // 基于 DOM 路径生成 selector
                const parts = [];
                let current = target;
                while (current && current.nodeType === 1) {{
                    let tag = current.tagName.toLowerCase();
                    if (current.id) {{
                        parts.unshift(`#${{current.id}}`);
                        break;
                    }}
                    const parent = current.parentElement;
                    if (parent) {{
                        const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
                        if (siblings.length > 1) {{
                            const idx = siblings.indexOf(current) + 1;
                            tag += `:nth-of-type(${{idx}})`;
                        }}
                    }}
                    parts.unshift(tag);
                    if (parts.length >= 5) break;
                    current = current.parentElement;
                }}
                return parts.join(' > ');
            }}
            return null;
        }}""", label)
    except Exception as e:
        LOG.debug(f"    _resolve_label_selector({label}) 失败: {e}")
        return None
