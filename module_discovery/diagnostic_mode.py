"""
diagnostic_mode.py — 自诊断模式

职责：
- 在 Stage 2 失败时自动触发诊断流程
- 截图、DOM dump、选择器测试、分页检查
- 尝试自动修复 selector_patterns.json
- 输出诊断报告到 output/debug/{module}/diagnostic_{timestamp}/
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from playwright.async_api import Page

LOG = logging.getLogger("diagnostic_mode")


class DiagnosticMode:
    """自诊断模式：失败时自动分析 DOM 结构并尝试修复"""

    def __init__(self, page: Page, module_name: str, project_dir: Path,
                 selector_patterns_path: str = "config/selector_patterns.json"):
        self.page = page
        self.module_name = module_name
        self.project_dir = project_dir
        self.selector_patterns_path = selector_patterns_path
        self.timestamp = None
        self.debug_dir = None
        self.results = {}

    async def run_strategy(self, strategy_name: str, **kwargs) -> Dict[str, Any]:
        """执行诊断策略

        Args:
            strategy_name: 策略名称（如 "row_not_found", "button_click_failed"）
            **kwargs: 策略参数（如 marker, row 等）

        Returns:
            诊断结果字典
        """
        # 设置输出目录
        import time
        self.timestamp = int(time.time())
        self.debug_dir = self.project_dir / "output" / "debug" / self.module_name / f"diagnostic_{self.timestamp}"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

        LOG.info(f"[诊断模式] 触发策略: {strategy_name}")
        LOG.info(f"[诊断模式] 输出目录: {self.debug_dir}")

        # 加载策略配置
        strategy_config = self._load_strategy_config(strategy_name)
        if not strategy_config:
            LOG.warning(f"[诊断模式] 未找到策略配置: {strategy_name}")
            return {"error": f"Strategy not found: {strategy_name}"}

        # 执行诊断步骤
        steps = strategy_config.get("steps", [])
        results = {}

        for step in steps:
            action = step.get("action")
            output_file = step.get("output", "unknown.json")
            description = step.get("description", "")

            LOG.info(f"[诊断模式] 执行步骤: {action} - {description}")

            try:
                if action == "screenshot":
                    result = await self._take_screenshot(step.get("scope", "full_page"), output_file)
                elif action == "dump_table_structure":
                    result = await self._dump_table_structure(step.get("include", []), output_file)
                elif action == "test_selectors":
                    result = await self._test_selectors(step.get("selectors_from", ""), output_file)
                elif action == "search_marker_in_page":
                    marker = kwargs.get("marker", "")
                    result = await self._search_marker(marker, step.get("method", "textContent"), output_file)
                elif action == "check_pagination":
                    result = await self._check_pagination(step.get("include", []), output_file)
                elif action == "dump_row_cells":
                    row = kwargs.get("row")
                    result = await self._dump_row_cells(row, step.get("include", []), output_file)
                elif action == "check_fixed_right":
                    result = await self._check_fixed_right(step.get("selectors_from", ""), output_file)
                elif action == "dump_fixed_right_dom":
                    result = await self._dump_fixed_right_dom(step.get("depth", 3), output_file)
                elif action == "test_xpath_templates":
                    result = await self._test_xpath_templates(step.get("templates_from", ""), output_file)
                elif action == "dump_form_state":
                    result = await self._dump_form_state(step.get("include", []), output_file)
                elif action == "dump_interceptor_state":
                    result = await self._dump_interceptor_state(kwargs.get("interceptor"), step.get("include", []), output_file)
                elif action == "check_form_validation_errors":
                    result = await self._check_form_validation_errors(step.get("selector", ".el-form-item__error"), output_file)
                elif action == "check_dialog_state":
                    result = await self._check_dialog_state(step.get("include", []), output_file)
                elif action == "test_dropdown_selectors":
                    result = await self._test_dropdown_selectors(step.get("selectors_from", ""), output_file)
                elif action == "check_el_dropdown_class":
                    result = await self._check_el_dropdown_class(output_file)
                elif action == "try_native_click":
                    result = await self._try_native_click(step.get("method", "el.click()"), output_file)
                elif action == "dump_capture_result":
                    result = self._dump_capture_result(kwargs.get("capture_result"), step.get("include", []), output_file)
                elif action == "analyze_missing_apis":
                    result = self._analyze_missing_apis(kwargs.get("capture_result"), step.get("expected_categories", []), output_file)
                elif action == "check_interceptor_logs":
                    result = await self._check_interceptor_logs(kwargs.get("interceptor"), output_file)
                elif action == "compare_with_operation_patterns":
                    result = await self._compare_with_operation_patterns(step.get("patterns_from", ""), output_file)
                else:
                    LOG.warning(f"[诊断模式] 未知动作: {action}")
                    result = {"error": f"Unknown action: {action}"}

                results[action] = result
                LOG.info(f"[诊断模式] 步骤完成: {action}")

            except Exception as e:
                LOG.error(f"[诊断模式] 步骤失败: {action} - {e}")
                results[action] = {"error": str(e)}

        # 尝试自动修复
        auto_fix = strategy_config.get("auto_fix", False)
        fix_target = strategy_config.get("fix_target", "")

        if auto_fix and fix_target:
            LOG.info(f"[诊断模式] 尝试自动修复: {fix_target}")
            fix_result = self._attempt_auto_fix(results, fix_target)
            results["auto_fix"] = fix_result

        # 保存诊断报告
        self._save_diagnostic_report(results, strategy_name)

        LOG.info(f"[诊断模式] 诊断完成，共执行 {len(results)} 个步骤")
        return results

    def _load_strategy_config(self, strategy_name: str) -> Optional[Dict]:
        """加载策略配置"""
        config_path = self.project_dir / "config" / "debug_strategies.json"
        if not config_path.exists():
            LOG.warning(f"[诊断模式] 策略配置文件不存在: {config_path}")
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            strategies = config.get("strategies", {})
            return strategies.get(strategy_name)

        except Exception as e:
            LOG.error(f"[诊断模式] 加载策略配置失败: {e}")
            return None

    async def _take_screenshot(self, scope: str, output_file: str) -> Dict:
        """截取页面截图"""
        screenshot_path = self.debug_dir / output_file.replace(".json", ".png")

        try:
            if scope == "full_page":
                await self.page.screenshot(path=str(screenshot_path), full_page=True)
            elif scope == "row_area":
                # 尝试截取表格区域
                table = await self.page.query_selector(".el-table")
                if table:
                    await table.screenshot(path=str(screenshot_path))
                else:
                    await self.page.screenshot(path=str(screenshot_path), full_page=True)
            else:
                await self.page.screenshot(path=str(screenshot_path), full_page=True)

            return {"success": True, "path": str(screenshot_path)}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dump_table_structure(self, include: List[str], output_file: str) -> Dict:
        """提取表格 DOM 结构"""
        try:
            structure = await self.page.evaluate("""
                () => {
                    const table = document.querySelector('.el-table');
                    if (!table) return { error: 'No .el-table found' };

                    const result = {
                        wrappers: [],
                        fixed_columns: { left: false, right: false },
                        row_count: 0,
                        column_headers: []
                    };

                    // 检查 wrappers
                    const wrappers = table.querySelectorAll('[class*="wrapper"]');
                    wrappers.forEach(w => {
                        result.wrappers.push({
                            class: w.className,
                            row_count: w.querySelectorAll('tr').length
                        });
                    });

                    // 检查固定列
                    result.fixed_columns.left = !!table.querySelector('.el-table__fixed-left, .el-table__fixed');
                    result.fixed_columns.right = !!table.querySelector('.el-table__fixed-right');

                    // 统计行数
                    const rows = table.querySelectorAll('tbody tr');
                    result.row_count = rows.length;

                    // 提取列头
                    const headers = table.querySelectorAll('thead th');
                    headers.forEach(th => {
                        result.column_headers.push(th.textContent.trim());
                    });

                    return result;
                }
            """)

            self._save_json(output_file, structure)
            return {"success": True, "structure": structure}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _test_selectors(self, selectors_from: str, output_file: str) -> Dict:
        """测试选择器命中率"""
        try:
            # 加载选择器配置
            selector_config = self._load_selector_patterns()
            if not selector_config:
                return {"success": False, "error": "Failed to load selector_patterns.json"}

            # 解析 selectors_from（如 "selector_patterns.json#table_row_selectors"）
            pattern_key = selectors_from.split("#")[-1]
            pattern = selector_config.get("patterns", {}).get(pattern_key, {})

            if not pattern:
                return {"success": False, "error": f"Pattern not found: {pattern_key}"}

            # 测试每个选择器
            test_results = []
            selectors = pattern.get("selectors", [])

            for selector_info in selectors:
                selector = selector_info.get("selector", "")
                name = selector_info.get("name", "unknown")

                try:
                    count = await self.page.eval_on_selector_all(selector, "els => els.length")
                    test_results.append({
                        "name": name,
                        "selector": selector,
                        "count": count,
                        "success": count > 0
                    })
                except Exception as e:
                    test_results.append({
                        "name": name,
                        "selector": selector,
                        "error": str(e),
                        "success": False
                    })

            self._save_json(output_file, test_results)
            return {"success": True, "results": test_results}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _search_marker(self, marker: str, method: str, output_file: str) -> Dict:
        """在页面中搜索 marker 文本"""
        if not marker:
            return {"success": False, "error": "No marker provided"}

        try:
            locations = await self.page.evaluate(f"""
                (marker) => {{
                    const results = [];
                    const walker = document.createTreeWalker(
                        document.body,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );

                    let node;
                    while (node = walker.nextNode()) {{
                        if (node.textContent.includes(marker)) {{
                            const element = node.parentElement;
                            results.push({{
                                text: node.textContent.substring(0, 100),
                                tag: element.tagName,
                                class: element.className,
                                id: element.id
                            }});
                        }}
                    }}

                    return results;
                }}
            """, marker)

            self._save_json(output_file, locations)
            return {"success": True, "count": len(locations), "locations": locations}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_pagination(self, include: List[str], output_file: str) -> Dict:
        """检查分页状态"""
        try:
            pagination = await self.page.evaluate("""
                () => {
                    const pagination = document.querySelector('.el-pagination');
                    if (!pagination) return { exists: false };

                    const result = { exists: true };

                    // 当前页
                    const active = pagination.querySelector('.el-pager .number.active');
                    result.current_page = active ? parseInt(active.textContent) : null;

                    // 总页数
                    const pages = pagination.querySelectorAll('.el-pager .number');
                    result.total_pages = pages.length;

                    // 每页大小
                    const sizeSelector = pagination.querySelector('.el-pagination__sizes');
                    if (sizeSelector) {
                        const selected = sizeSelector.querySelector('.el-select .el-input__inner');
                        result.page_size = selected ? selected.value : null;
                    }

                    // 总条数
                    const total = pagination.querySelector('.el-pagination__total');
                    result.total_count = total ? total.textContent : null;

                    return result;
                }
            """)

            self._save_json(output_file, pagination)
            return {"success": True, "pagination": pagination}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dump_row_cells(self, row, include: List[str], output_file: str) -> Dict:
        """提取行的单元格内容"""
        if not row:
            return {"success": False, "error": "No row provided"}

        try:
            cells = await row.evaluate("""
                (row) => {
                    const cells = row.querySelectorAll('td');
                    const result = [];

                    cells.forEach((cell, index) => {
                        const cellInfo = {
                            index: index,
                            text: cell.textContent.trim()
                        };

                        // 提取按钮
                        const buttons = cell.querySelectorAll('button');
                        if (buttons.length > 0) {
                            cellInfo.buttons = Array.from(buttons).map(b => b.textContent.trim());
                        }

                        // 提取下拉
                        const dropdowns = cell.querySelectorAll('.el-dropdown');
                        if (dropdowns.length > 0) {
                            cellInfo.dropdowns = Array.from(dropdowns).map(d => d.textContent.trim());
                        }

                        // 提取链接
                        const links = cell.querySelectorAll('a');
                        if (links.length > 0) {
                            cellInfo.links = Array.from(links).map(a => ({
                                text: a.textContent.trim(),
                                href: a.href
                            }));
                        }

                        result.push(cellInfo);
                    });

                    return result;
                }
            """)

            self._save_json(output_file, cells)
            return {"success": True, "cells": cells}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_fixed_right(self, selectors_from: str, output_file: str) -> Dict:
        """检查固定右列是否存在目标按钮"""
        try:
            check_result = await self.page.evaluate("""
                () => {
                    const fixedRight = document.querySelector('.el-table__fixed-right');
                    if (!fixedRight) return { exists: false };

                    const result = {
                        exists: true,
                        buttons: [],
                        dropdowns: []
                    };

                    // 提取按钮
                    const buttons = fixedRight.querySelectorAll('button');
                    buttons.forEach(b => {
                        result.buttons.push({
                            text: b.textContent.trim(),
                            class: b.className,
                            disabled: b.disabled
                        });
                    });

                    // 提取下拉
                    const dropdowns = fixedRight.querySelectorAll('.el-dropdown');
                    dropdowns.forEach(d => {
                        result.dropdowns.push({
                            text: d.textContent.trim(),
                            class: d.className
                        });
                    });

                    return result;
                }
            """)

            self._save_json(output_file, check_result)
            return {"success": True, "check": check_result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dump_fixed_right_dom(self, depth: int, output_file: str) -> Dict:
        """提取固定右列的 DOM 结构"""
        try:
            dom = await self.page.evaluate(f"""
                (depth) => {{
                    const fixedRight = document.querySelector('.el-table__fixed-right');
                    if (!fixedRight) return {{ error: 'No fixed-right found' }};

                    function extractDom(element, currentDepth) {{
                        if (currentDepth >= depth) return null;

                        const result = {{
                            tag: element.tagName,
                            class: element.className,
                            id: element.id,
                            children: []
                        }};

                        // 提取文本（如果是叶子节点）
                        if (element.children.length === 0) {{
                            result.text = element.textContent.trim().substring(0, 50);
                        }}

                        // 递归提取子元素
                        Array.from(element.children).forEach(child => {{
                            const childDom = extractDom(child, currentDepth + 1);
                            if (childDom) result.children.push(childDom);
                        }});

                        return result;
                    }}

                    return extractDom(fixedRight, 0);
                }}
            """, depth)

            self._save_json(output_file, dom)
            return {"success": True, "dom": dom}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _test_xpath_templates(self, templates_from: str, output_file: str) -> Dict:
        """测试 XPath 模板命中率"""
        try:
            # 加载选择器配置
            selector_config = self._load_selector_patterns()
            if not selector_config:
                return {"success": False, "error": "Failed to load selector_patterns.json"}

            # 解析 templates_from
            pattern_key = templates_from.split("#")[-1]
            pattern = selector_config.get("patterns", {}).get(pattern_key, {})

            if not pattern:
                return {"success": False, "error": f"Pattern not found: {pattern_key}"}

            # 测试每个 XPath 模板
            test_results = []
            fallback_chain = pattern.get("fallback_chain", [])

            for template_info in fallback_chain:
                strategy = template_info.get("strategy", "unknown")
                xpath = template_info.get("xpath", "")

                # 替换占位符
                xpath_test = xpath.replace("{row_index}", "1").replace("{label}", "")

                try:
                    count = await self.page.evaluate(f"""
                        () => {{
                            const result = document.evaluate(
                                `{xpath_test}`,
                                document,
                                null,
                                XPathResult.ORDERED_NODE_SNAPSHOT_TYPE,
                                null
                            );
                            return result.snapshotLength;
                        }}
                    """)

                    test_results.append({
                        "strategy": strategy,
                        "xpath": xpath,
                        "count": count,
                        "success": count > 0
                    })
                except Exception as e:
                    test_results.append({
                        "strategy": strategy,
                        "xpath": xpath,
                        "error": str(e),
                        "success": False
                    })

            self._save_json(output_file, test_results)
            return {"success": True, "results": test_results}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dump_form_state(self, include: List[str], output_file: str) -> Dict:
        """提取表单字段状态"""
        try:
            form_state = await self.page.evaluate("""
                () => {
                    const formItems = document.querySelectorAll('.el-form-item');
                    const result = [];

                    formItems.forEach(item => {
                        const label = item.querySelector('.el-form-item__label');
                        const input = item.querySelector('input, textarea');
                        const select = item.querySelector('.el-select');
                        const error = item.querySelector('.el-form-item__error');

                        const fieldInfo = {
                            label: label ? label.textContent.trim() : '',
                            required: item.classList.contains('is-required')
                        };

                        if (input) {
                            fieldInfo.type = 'input';
                            fieldInfo.input_type = input.type;
                            fieldInfo.value = input.value;
                            fieldInfo.placeholder = input.placeholder;
                        } else if (select) {
                            fieldInfo.type = 'select';
                            const selected = select.querySelector('.el-select__tags-text, .el-input__inner');
                            fieldInfo.value = selected ? selected.textContent.trim() : '';
                        }

                        if (error) {
                            fieldInfo.error = error.textContent.trim();
                        }

                        result.push(fieldInfo);
                    });

                    return result;
                }
            """)

            self._save_json(output_file, form_state)
            return {"success": True, "form_state": form_state}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _dump_interceptor_state(self, interceptor, include: List[str], output_file: str) -> Dict:
        """提取拦截器状态"""
        if not interceptor:
            return {"success": False, "error": "No interceptor provided"}

        try:
            state = {
                "call_count": len(interceptor.calls),
                "post_count": sum(1 for c in interceptor.calls if c.get("method") == "POST"),
                "last_calls": interceptor.calls[-10:] if interceptor.calls else [],
                "error_responses": []
            }

            # 提取错误响应
            for sample_list in interceptor.response_samples.values():
                for sample in sample_list:
                    if sample.get("status", 200) >= 400:
                        state["error_responses"].append(sample)

            self._save_json(output_file, state)
            return {"success": True, "state": state}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_form_validation_errors(self, selector: str, output_file: str) -> Dict:
        """检查表单校验错误"""
        try:
            errors = await self.page.evaluate(f"""
                () => {{
                    const errorElements = document.querySelectorAll('{selector}');
                    const result = [];

                    errorElements.forEach(el => {{
                        const formItem = el.closest('.el-form-item');
                        const label = formItem ? formItem.querySelector('.el-form-item__label') : null;

                        result.push({{
                            label: label ? label.textContent.trim() : '',
                            error: el.textContent.trim()
                        }});
                    }});

                    return result;
                }}
            """)

            self._save_json(output_file, errors)
            return {"success": True, "errors": errors, "count": len(errors)}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_dialog_state(self, include: List[str], output_file: str) -> Dict:
        """检查对话框状态"""
        try:
            dialog_state = await self.page.evaluate("""
                () => {
                    const dialog = document.querySelector('.el-dialog__wrapper[style*="display: none"]') ?
                        null : document.querySelector('.el-dialog__wrapper');

                    if (!dialog) return { is_open: false };

                    const result = { is_open: true };

                    // 标题
                    const title = dialog.querySelector('.el-dialog__title');
                    result.title = title ? title.textContent.trim() : '';

                    // 按钮
                    const buttons = dialog.querySelectorAll('.el-dialog__footer button');
                    result.buttons = Array.from(buttons).map(b => b.textContent.trim());

                    return result;
                }
            """)

            self._save_json(output_file, dialog_state)
            return {"success": True, "dialog_state": dialog_state}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _test_dropdown_selectors(self, selectors_from: str, output_file: str) -> Dict:
        """测试下拉触发器选择器"""
        try:
            selector_config = self._load_selector_patterns()
            if not selector_config:
                return {"success": False, "error": "Failed to load selector_patterns.json"}

            pattern_key = selectors_from.split("#")[-1]
            pattern = selector_config.get("patterns", {}).get(pattern_key, {})

            if not pattern:
                return {"success": False, "error": f"Pattern not found: {pattern_key}"}

            test_results = []
            selectors = pattern.get("selectors", [])

            for selector_info in selectors:
                selector = selector_info.get("selector", "")
                selector_type = selector_info.get("type", "css")

                try:
                    if selector_type == "css":
                        count = await self.page.eval_on_selector_all(selector, "els => els.length")
                    elif selector_type == "text":
                        count = await self.page.evaluate(f"""
                            () => {{
                                const buttons = document.querySelectorAll('button');
                                return Array.from(buttons).filter(b => b.textContent.includes('更多')).length;
                            }}
                        """)
                    else:
                        count = 0

                    test_results.append({
                        "type": selector_type,
                        "selector": selector,
                        "count": count,
                        "success": count > 0
                    })
                except Exception as e:
                    test_results.append({
                        "type": selector_type,
                        "selector": selector,
                        "error": str(e),
                        "success": False
                    })

            self._save_json(output_file, test_results)
            return {"success": True, "results": test_results}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_el_dropdown_class(self, output_file: str) -> Dict:
        """检查 el-dropdown 类名"""
        try:
            check_result = await self.page.evaluate("""
                () => {
                    const dropdowns = document.querySelectorAll('.el-dropdown');
                    const result = [];

                    dropdowns.forEach(d => {
                        result.push({
                            class: d.className,
                            text: d.textContent.trim(),
                            has_trigger: d.getAttribute('trigger') || 'hover',
                            parent_class: d.parentElement ? d.parentElement.className : ''
                        });
                    });

                    return result;
                }
            """)

            self._save_json(output_file, check_result)
            return {"success": True, "dropdowns": check_result, "count": len(check_result)}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _try_native_click(self, method: str, output_file: str) -> Dict:
        """尝试原生 click 方法"""
        try:
            click_result = await self.page.evaluate("""
                () => {
                    const dropdown = document.querySelector('.el-dropdown');
                    if (!dropdown) return { success: false, error: 'No .el-dropdown found' };

                    // 尝试原生 click
                    try {
                        dropdown.click();
                        return { success: true, method: 'el.click()' };
                    } catch (e) {
                        return { success: false, error: e.message };
                    }
                }
            """)

            self._save_json(output_file, click_result)
            return {"success": True, "result": click_result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _dump_capture_result(self, capture_result, include: List[str], output_file: str) -> Dict:
        """提取捕获结果"""
        if not capture_result:
            return {"success": False, "error": "No capture_result provided"}

        try:
            result = {}
            for key in include:
                if key in capture_result:
                    result[key] = capture_result[key]

            self._save_json(output_file, result)
            return {"success": True, "result": result}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _analyze_missing_apis(self, capture_result, expected_categories: List[str], output_file: str) -> Dict:
        """分析缺失的 API 类别"""
        if not capture_result:
            return {"success": False, "error": "No capture_result provided"}

        try:
            classified = capture_result.get("classified", {})
            missing = []
            found = []

            for category in expected_categories:
                if category in classified and classified[category]:
                    found.append(category)
                else:
                    missing.append(category)

            analysis = {
                "expected": expected_categories,
                "found": found,
                "missing": missing,
                "coverage": f"{len(found)}/{len(expected_categories)}"
            }

            self._save_json(output_file, analysis)
            return {"success": True, "analysis": analysis}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _check_interceptor_logs(self, interceptor, output_file: str) -> Dict:
        """检查拦截器日志"""
        if not interceptor:
            return {"success": False, "error": "No interceptor provided"}

        try:
            logs = {
                "total_calls": len(interceptor.calls),
                "calls_by_method": {},
                "calls_by_status": {},
                "sample_calls": interceptor.calls[:20]
            }

            # 按方法统计
            for call in interceptor.calls:
                method = call.get("method", "UNKNOWN")
                logs["calls_by_method"][method] = logs["calls_by_method"].get(method, 0) + 1

            # 按状态统计（从 response_samples）
            for path, samples in interceptor.response_samples.items():
                for sample in samples:
                    status = sample.get("status", 200)
                    status_group = f"{status // 100}xx"
                    logs["calls_by_status"][status_group] = logs["calls_by_status"].get(status_group, 0) + 1

            self._save_json(output_file, logs)
            return {"success": True, "logs": logs}

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _compare_with_operation_patterns(self, patterns_from: str, output_file: str) -> Dict:
        """与已知操作流程模式对比"""
        try:
            # 加载操作模式配置
            patterns_path = self.project_dir / "config" / "operation_patterns.json"
            if not patterns_path.exists():
                return {"success": False, "error": "operation_patterns.json not found"}

            with open(patterns_path, "r", encoding="utf-8") as f:
                patterns = json.load(f)

            # 提取当前页面特征
            page_features = await self.page.evaluate("""
                () => {
                    const features = {
                        has_form: !!document.querySelector('.el-form'),
                        has_table: !!document.querySelector('.el-table'),
                        has_dialog: !!document.querySelector('.el-dialog__wrapper:not([style*="display: none"])'),
                        toolbar_buttons: [],
                        row_buttons: []
                    };

                    // 工具栏按钮
                    const toolbar = document.querySelectorAll('.el-table__header-wrapper button, .el-toolbar button');
                    toolbar.forEach(b => features.toolbar_buttons.push(b.textContent.trim()));

                    // 行内按钮
                    const rows = document.querySelectorAll('.el-table__body-wrapper tbody tr');
                    if (rows.length > 0) {
                        const buttons = rows[0].querySelectorAll('button');
                        buttons.forEach(b => features.row_buttons.push(b.textContent.trim()));
                    }

                    return features;
                }
            """)

            comparison = {
                "page_features": page_features,
                "known_patterns": list(patterns.get("operations", {}).keys()),
                "matches": []
            }

            # 简单匹配逻辑
            for pattern_name, pattern_info in patterns.get("operations", {}).items():
                expected_buttons = pattern_info.get("expected_apis", [])
                # 这里可以添加更复杂的匹配逻辑
                comparison["matches"].append({
                    "pattern": pattern_name,
                    "confidence": 0.5  # 占位符
                })

            self._save_json(output_file, comparison)
            return {"success": True, "comparison": comparison}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _attempt_auto_fix(self, results: Dict, fix_target: str) -> Dict:
        """尝试自动修复"""
        try:
            # 解析 fix_target（如 "selector_patterns.json#table_row_selectors"）
            pattern_key = fix_target.split("#")[-1]

            # 从诊断结果中提取新发现的选择器
            new_selectors = []

            # 检查 selector_tests 结果
            selector_tests = results.get("test_selectors", {}).get("results", [])
            for test in selector_tests:
                if test.get("success"):
                    new_selectors.append({
                        "name": test.get("name"),
                        "selector": test.get("selector"),
                        "source": "diagnostic_mode",
                        "confidence": 0.8
                    })

            if not new_selectors:
                return {"success": False, "reason": "No new selectors found"}

            # 加载现有选择器配置
            selector_config = self._load_selector_patterns()
            if not selector_config:
                return {"success": False, "reason": "Failed to load selector_patterns.json"}

            # 更新配置
            pattern = selector_config.get("patterns", {}).get(pattern_key, {})
            if not pattern:
                return {"success": False, "reason": f"Pattern not found: {pattern_key}"}

            # 合并新选择器
            existing_selectors = pattern.get("selectors", [])
            existing_names = {s.get("name") for s in existing_selectors}

            added_count = 0
            for new_sel in new_selectors:
                if new_sel.get("name") not in existing_names:
                    existing_selectors.append(new_sel)
                    added_count += 1

            if added_count == 0:
                return {"success": False, "reason": "No new selectors to add"}

            # 更新模块来源
            module_sources = set(pattern.get("module_source", []))
            module_sources.add(self.module_name)
            pattern["module_source"] = list(module_sources)

            # 更新时间戳
            import time
            pattern["last_verified"] = time.strftime("%Y-%m-%d")
            pattern["usage_count"] = pattern.get("usage_count", 0) + 1

            # 保存更新后的配置
            selector_config["patterns"][pattern_key] = pattern
            self._save_selector_patterns(selector_config)

            return {
                "success": True,
                "added_selectors": added_count,
                "updated_pattern": pattern_key
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _load_selector_patterns(self) -> Optional[Dict]:
        """加载 selector_patterns.json"""
        config_path = self.project_dir / self.selector_patterns_path
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_selector_patterns(self, config: Dict):
        """保存 selector_patterns.json"""
        config_path = self.project_dir / self.selector_patterns_path
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def _save_json(self, filename: str, data: Any):
        """保存 JSON 文件"""
        output_path = self.debug_dir / filename
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _save_diagnostic_report(self, results: Dict, strategy_name: str):
        """保存诊断报告"""
        report = {
            "strategy": strategy_name,
            "module": self.module_name,
            "timestamp": self.timestamp,
            "steps_executed": len(results),
            "results": results
        }

        report_path = self.debug_dir / "diagnostic_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        LOG.info(f"[诊断模式] 报告已保存: {report_path}")
