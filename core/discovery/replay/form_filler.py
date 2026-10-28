"""
form_filler.py - 表单填充与验证

职责：
- 扫描页面表单字段（输入框、选择框、文本域）
- 根据 label 智能填充表单
- 处理下拉框选择
- 表单提交与验证
"""

import json
import logging
import time
from playwright.async_api import Page, Locator
from typing import Dict, List, Tuple

from .. import const
from ..shared import apply_fill_rule

LOG = logging.getLogger("form_filler")


def _log_scan_debug(raw_result: dict):
    """记录 scan_form_fields 返回的调试信息。"""
    # D1: radio 误分类
    for d in raw_result.get("_debug_multiInput", []):
        LOG.info(f"    [D1-radio] formItem='{d.get('formItemLabel')}' "
                 f"rawInputs={d.get('allRawCount')} filteredInputs={d.get('allInputsCount')} "
                 f"radioInRaw={d.get('radioInRawCount')}")
        for detail in d.get("details", []):
            LOG.info(f"    [D1-radio]   type={detail.get('type')} "
                     f"parentIsRadio={detail.get('parentIsRadio')} "
                     f"parentIsRadioBtn={detail.get('parentIsRadioBtn')}")

    # D3: IP 输入组件
    for ic in raw_result.get("_debug_ipCheck", []):
        LOG.info(f"    [D3-ip] label='{ic.get('label')}' "
                 f"inputCount={ic.get('inputCount')} "
                 f"widthRatio={ic.get('widthRatio')} "
                 f"hasIpInLabel={ic.get('hasIpInLabel')} "
                 f"hasAddBtn={ic.get('hasAddBtn')} "
                 f"tagCount={ic.get('tagCount')}")
        for sub in ic.get("subInputDetails", []):
            LOG.info(f"    [D3-ip]   input[{sub.get('idx')}]: "
                     f"type={sub.get('type')} width={sub.get('width')} "
                     f"placeholder='{sub.get('placeholder')}' readOnly={sub.get('readOnly')}")

    # D2: "添加"按钮
    for d in raw_result.get("_debug_addBtn", []):
        LOG.info(f"    [D2-addBtn] formItem='{d.get('formItemLabel')}' "
                 f"btnText='{d.get('btnText')}' visible={d.get('visible')} "
                 f"currentTags={d.get('currentTags')}")

    # D4: el-select 多选
    for d in raw_result.get("_debug_multiSelect", []):
        LOG.info(f"    [D4-multiSelect] label='{d.get('label')}' "
                 f"hasMultipleClass={d.get('hasMultipleClass')} "
                 f"hasTagsContainer={d.get('hasTagsContainer')} "
                 f"currentTagCount={d.get('currentTagCount')}")

    # D5: 复合组件结构
    for d in raw_result.get("_debug_composite", []):
        LOG.info(f"    [D5-composite] label='{d.get('label')}' "
                 f"selectCount={d.get('selectCount')} "
                 f"independentInputs={d.get('independentInputCount')} "
                 f"totalInputs={d.get('totalInputCount')} "
                 f"hasIpInLabel={d.get('hasIpInLabel')} "
                 f"isIpMaskCandidate={d.get('isIpMaskCandidate')}")
        for sub in d.get("inputDetails", []):
            LOG.info(f"    [D5-composite]   input[{sub.get('idx')}]: "
                     f"type={sub.get('type')} width={sub.get('width')} "
                     f"placeholder='{sub.get('placeholder')}' "
                     f"inInputNumber={sub.get('inInputNumber')} "
                     f"maxLength={sub.get('maxLength')}")


class FormFiller:
    """表单填充封装类，供 capture_apis 调用"""

    def __init__(self, page: Page, ui_framework: str = "element-ui"):
        self.page = page
        self._kb = None
        self._executor = None
        self.framework = ui_framework
        # 加载 UI 选择器配置
        try:
            self.selectors = const.get_ui_selectors(ui_framework)
        except FileNotFoundError:
            # 回退到 element-ui
            self.selectors = const.get_ui_selectors("element-ui")

    def _get_executor(self, framework="element-ui"):
        """获取 MultiStepExecutor 实例（延迟加载）"""
        if self._executor is None:
            from ..kb_loader import get_kb
            self._kb = get_kb()
            self._executor = MultiStepExecutor(self.page, self._kb, framework)
        return self._executor

    async def scan_form_fields_v2(self, scope_selector: str = "") -> List[Dict]:
        """扫描表单字段（v2 版本，返回完整结构含 selector + kb_category）

        Args:
            scope_selector: 可选，限定搜索范围的 CSS 选择器（如 '.el-drawer:not([style*="display: none"])'）
        """
        raw_result = await scan_form_fields(self.page, scope_selector=scope_selector)
        # 兼容新的返回格式（包含调试信息）
        if isinstance(raw_result, dict):
            fields = raw_result.get('fields', [])
            _log_scan_debug(raw_result)
        else:
            fields = raw_result
        # 保留完整字段结构，含 kb_category
        result = []
        for f in fields:
            entry = {
                "label": f["label"],
                "type": f["type"],
                "kb_category": f.get("kb_category", ""),
                "selector": f.get("selector"),
                "playwright_locator": f.get("playwright_locator"),
                "inputType": f.get("inputType", "text"),
                "required": f.get("required", False),
            }
            if f.get("firstOptionText"):
                entry["firstOptionText"] = f["firstOptionText"]
            # ★ 透传 placeholder、thead_label、visible_index，用于跨会话稳定定位
            if f.get("placeholder"):
                entry["placeholder"] = f["placeholder"]
            if f.get("thead_label"):
                entry["thead_label"] = f["thead_label"]
            if f.get("visible_index") is not None:
                entry["visible_index"] = f["visible_index"]
            if f.get("is_fallback_label"):
                entry["is_fallback_label"] = True
            # ★ IP 多段输入专用字段
            if f.get("subSelectors"):
                entry["subSelectors"] = f["subSelectors"]
            if f.get("inputCount"):
                entry["inputCount"] = f["inputCount"]
            if f.get("ipVersion"):
                entry["ipVersion"] = f["ipVersion"]
            result.append(entry)
        return result

    async def _detect_cascade_debug(self, trigger_label: str, trigger_type: str,
                                     pre_fields: List[Dict]):
        """[D6] 级联检测调试：pre-scan → 等待 → post-scan，对比前后字段差异。

        关键：pre-scan 和 post-scan 都使用 scan_form_fields()，确保 selector 格式一致。
        pre_fields 参数仅用于日志参考（显示当前已知的多步字段），不作为比较基准。
        """
        # pre-scan：填充后的当前状态（与 post-scan 用同一函数，selector 格式一致）
        try:
            pre_scan = await scan_form_fields(self.page)
            pre_raw = pre_scan.get('fields', []) if isinstance(pre_scan, dict) else pre_scan
        except Exception as e:
            LOG.warning(f"    [D6-cascade] pre-scan 失败: {e}")
            return

        LOG.info(f"    [D6-cascade] pre-scan: {len(pre_raw)} 个字段, 等待 2s 检测级联渲染 "
                 f"(trigger={trigger_label}, type={trigger_type})")
        await self.page.wait_for_timeout(2000)

        try:
            post_scan = await scan_form_fields(self.page)
            post_raw = post_scan.get('fields', []) if isinstance(post_scan, dict) else post_scan
        except Exception as e:
            LOG.warning(f"    [D6-cascade] post-scan 失败: {e}")
            return

        # 用 selector 作为字段唯一标识（两次扫描格式一致）
        pre_selectors = {f.get('selector') for f in pre_raw if f.get('selector')}
        post_selectors = {f.get('selector') for f in post_raw if f.get('selector')}

        # 1. 检测全新 selector（级联渲染出的新字段）
        new_selectors = post_selectors - pre_selectors
        if new_selectors:
            new_field_list = [f for f in post_raw if f.get('selector') in new_selectors]
            for nf in new_field_list:
                LOG.warning(f"    [D6-cascade] 🆕 新字段: label='{nf.get('label')}' "
                           f"kb_category={nf.get('kb_category')} selector={nf.get('selector')}")

        # 2. 检测消失的 selector（级联隐藏了字段）
        gone_selectors = pre_selectors - post_selectors
        if gone_selectors:
            gone_list = [f for f in pre_raw if f.get('selector') in gone_selectors]
            for gf in gone_list:
                LOG.warning(f"    [D6-cascade] ❌ 消失: label='{gf.get('label')}' "
                           f"kb_category={gf.get('kb_category')}")

        # 3. 检测同 label 多字段（级联 select 等）
        from collections import defaultdict
        post_by_label = defaultdict(list)
        for f in post_raw:
            if f.get('selector'):
                post_by_label[f.get('label')].append(f)

        for lbl, fields_list in post_by_label.items():
            if len(fields_list) > 1:
                new_in_label = [f for f in fields_list if f.get('selector') in new_selectors]
                if new_in_label:
                    LOG.warning(f"    [D6-cascade] 🔗 同 label 多字段: label='{lbl}' "
                               f"共 {len(fields_list)} 个, 新增 {len(new_in_label)} 个")
                    for ni in new_in_label:
                        LOG.warning(f"    [D6-cascade]   新增: kb_category={ni.get('kb_category')} "
                                   f"selector={ni.get('selector')}")

        # 4. 检测同 label el-select 多实例
        same_label_selects = [f for f in post_raw
                             if f.get('label') == trigger_label
                             and f.get('kb_category') == 'el-select']
        if len(same_label_selects) > 1:
            LOG.warning(f"    [D6-cascade] 📋 同名 el-select: {trigger_label} "
                       f"共 {len(same_label_selects)} 个")
            for i, s in enumerate(same_label_selects):
                LOG.warning(f"    [D6-cascade]   select[{i}]: selector={s.get('selector')} "
                           f"required={s.get('required')}")

        if not new_selectors and not gone_selectors:
            LOG.info(f"    [D6-cascade] 无级联效果 (pre={len(pre_raw)} post={len(post_raw)})")

    async def _detect_and_fill_cascade(self, trigger_label: str, trigger_type: str,
                                        filled_selectors: set,
                                        depth: int = 0, max_depth: int = 3) -> Dict:
        """[Phase 2a] 级联检测 + 自动填充：pre-scan → 等待 → post-scan → 填充新字段。

        与 ``_detect_cascade_debug`` 的区别：
        - 检测到新字段后自动填充（multi_step 用 executor，简单字段用 fill）
        - 递归检测（新字段可能触发二级级联），最多 ``max_depth`` 层
        - 用 ``filled_selectors`` 防止循环级联（A→B→A）

        Args:
            trigger_label: 触发级联的字段 label
            trigger_type: 触发字段的 kb_category
            filled_selectors: 已填充字段的 selector 集合（防循环）
            depth: 当前递归深度
            max_depth: 最大递归深度

        Returns:
            {"cascade_fields": list, "cascade_actions": list, "cascade_filled": int}
        """
        from .. import const

        if depth >= max_depth:
            LOG.info(f"    [cascade-fill] 达到最大递归深度 {max_depth}，停止")
            return {"cascade_fields": [], "cascade_actions": [], "cascade_filled": 0}

        # 1. Pre/post scan
        try:
            pre_scan = await scan_form_fields(self.page)
            pre_raw = pre_scan.get('fields', []) if isinstance(pre_scan, dict) else pre_scan
        except Exception as e:
            LOG.warning(f"    [cascade-fill] pre-scan 失败: {e}")
            return {"cascade_fields": [], "cascade_actions": [], "cascade_filled": 0}

        LOG.info(f"    [cascade-fill] depth={depth} pre-scan: {len(pre_raw)} 个字段, "
                 f"等待 2s 检测级联 (trigger={trigger_label})")
        await self.page.wait_for_timeout(2000)

        try:
            post_scan = await scan_form_fields(self.page)
            post_raw = post_scan.get('fields', []) if isinstance(post_scan, dict) else post_scan
        except Exception as e:
            LOG.warning(f"    [cascade-fill] post-scan 失败: {e}")
            return {"cascade_fields": [], "cascade_actions": [], "cascade_filled": 0}

        # 2. 计算新增字段（selector + label 双重去重）
        pre_selectors = {f.get('selector') for f in pre_raw if f.get('selector')}
        post_selectors = {f.get('selector') for f in post_raw if f.get('selector')}
        new_selectors = post_selectors - pre_selectors - filled_selectors

        # ★ label 去重：selector 可能因 DOM 微变（nth-of-type 索引偏移）而不同，
        # 但 label 文本是语义标识，不随 DOM 结构变化。
        # 如果 post 中某字段的 label 在 pre 中已存在，不算新字段。
        pre_labels = {f.get('label', '') for f in pre_raw if f.get('label')}

        new_fields = [f for f in post_raw
                      if f.get('selector') and f.get('selector') in new_selectors
                      and f.get('label', '') not in pre_labels]

        if not new_fields:
            LOG.info(f"    [cascade-fill] 无级联新字段 (pre={len(pre_raw)} post={len(post_raw)})")
            return {"cascade_fields": [], "cascade_actions": [], "cascade_filled": 0}

        LOG.info(f"    [cascade-fill] 🆕 检测到 {len(new_fields)} 个级联新字段: "
                 f"{[f.get('label', '?') for f in new_fields]}")

        # 3. 填充新字段
        executor = self._get_executor("element-ui")
        cascade_actions = []
        cascade_filled = 0

        for field in new_fields:
            label = field.get('label', '')
            kb_cat = field.get('kb_category', '')
            # ★ 优先使用 playwright_locator（更稳定），回退到 selector
            selector = field.get('playwright_locator') or field.get('selector', '')

            # 防循环
            if selector in filled_selectors:
                LOG.info(f"    [cascade-fill] 跳过已填充字段: {label}")
                continue

            action_entry = {
                "type": "cascade-fill",
                "trigger_field": trigger_label,
                "cascade_field": label,
                "cascade_category": kb_cat,
                "selector": selector,
                "success": False,
            }

            if kb_cat in const.MULTI_STEP_TYPES or kb_cat == "form-checkbox":
                # 多步字段：用 MultiStepExecutor 填充
                success, detail = await executor.execute_with_details(
                    kb_cat, label, option_text="", selector=selector,
                    playwright_locator=field.get('playwright_locator')
                )
                action_entry["success"] = success
                action_entry["option_text"] = detail.get("option_text", "")
                if success:
                    cascade_filled += 1
                    filled_selectors.add(selector)
                    LOG.info(f"    [cascade-fill] ✅ {label} ({kb_cat}) → {detail.get('option_text', '')}")

                    # el-select 下拉框关闭
                    if kb_cat == "el-select":
                        await self.page.wait_for_timeout(300)
                        dropdown_visible = await self.page.evaluate("""() => {
                            const dropdown = document.querySelector('[x-placement]');
                            if (!dropdown) return false;
                            const style = window.getComputedStyle(dropdown);
                            return style.display !== 'none' && style.visibility !== 'hidden';
                        }""")
                        if dropdown_visible:
                            await self.page.keyboard.press("Escape")
                else:
                    LOG.warning(f"    [cascade-fill] ⚠️ {label} ({kb_cat}) 填充失败")

            elif kb_cat in ("input-generic", "textarea-generic"):
                # 简单输入：生成随机值并填充
                try:
                    el = self.page.locator(selector).first
                    if await el.count() > 0:
                        # ★ 安全护栏：如果字段已有值，不覆盖（防止 selector 误判覆盖正确数据）
                        current_value = await el.input_value()
                        if current_value and current_value.strip():
                            LOG.info(f"    [cascade-fill] 跳过已有值字段: {label} = '{current_value[:20]}'")
                            continue

                        ts = str(int(time.time()))[-6:]
                        value = f"cascade_{ts}"
                        await el.fill(value)
                        action_entry["success"] = True
                        action_entry["fill_value"] = value
                        cascade_filled += 1
                        filled_selectors.add(selector)
                        LOG.info(f"    [cascade-fill] ✅ {label} (input) → {value}")
                except Exception as e:
                    LOG.warning(f"    [cascade-fill] ⚠️ {label} (input) 填充失败: {e}")
            else:
                LOG.info(f"    [cascade-fill] 跳过不支持的类型: {label} ({kb_cat})")

            cascade_actions.append(action_entry)

        # 4. 递归检测（新字段可能触发二级级联）
        if cascade_filled > 0 and depth < max_depth - 1:
            last_filled = new_fields[-1]
            sub_result = await self._detect_and_fill_cascade(
                trigger_label=last_filled.get('label', ''),
                trigger_type=last_filled.get('kb_category', ''),
                filled_selectors=filled_selectors,
                depth=depth + 1,
                max_depth=max_depth,
            )
            cascade_actions.extend(sub_result.get("cascade_actions", []))
            cascade_filled += sub_result.get("cascade_filled", 0)

        return {
            "cascade_fields": new_fields,
            "cascade_actions": cascade_actions,
            "cascade_filled": cascade_filled,
        }

    async def fill_create_form(self, fields: List[Dict], username: str,
                                 fill_data: Dict[str, str] = None) -> Dict:
        """填充创建表单，返回填充结果。

        优先使用 scan 返回的精确 selector，回退到 el-form-item 结构定位。
        填充数据从 _build_fill_data() 动态构建（hardcoded + type-based fallback）。

        multi_step 类型（el-select/el-cascader/date-picker）由此方法跳过，
        由 fill_multi_step_fields() 处理。

        Args:
            fields: 表单字段列表
            username: 用户名
            fill_data: 可选的预生成填充数据（用于覆盖默认值）

        Returns:
            Dict: {
                "filled": int,  # 成功填充的字段数
                "cascade_actions": List[Dict],  # radio 触发的级联操作
                "select_close_dropdown": List[Dict],  # el-select 下拉框关闭操作
            }
        """
        from .. import const

        if fill_data is None:
            fill_data = self._build_fill_data(fields, username)

        filled = 0
        cascade_actions = []  # ★ 记录 radio 触发的级联操作（如 transfer-box）
        radio_selections = {}  # ★ 记录 radio 实际选择的值（供 playbook 使用）
        select_close_dropdown = []  # ★ 记录 el-select 下拉框关闭操作
        for field in fields:
            label = field.get("label", "")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")
            # ★ 优先使用 playwright_locator（更稳定），回退到 selector
            selector = field.get("playwright_locator") or field.get("selector")

            # multi_step 类型由 MultiStepExecutor 处理，此处跳过
            if kb_category in const.MULTI_STEP_TYPES:
                continue

            # form-checkbox 由 MultiStepExecutor 处理
            if kb_category == "form-checkbox":
                continue

            # radio 类型：点击第一个选项（默认选中即可）
            # 注意：Element UI + Vue 需要正确的事件序列才能更新 v-model
            # 策略：Playwright click → 检查 Vue 状态 → 如失败则用 Vue 2 内部 API 强制更新
            if kb_category == "radio" or field_type == "radio":
                try:
                    first_text = field.get("firstOptionText", "")
                    if first_text:
                        from .locator_helpers import safe_css
                        import json as _json

                        # 第一次尝试：Playwright click wrapper
                        radio_wrapper = self.page.locator(
                            safe_css(
                                f'.el-radio-group .el-radio:has-text("{first_text}"), '
                                f'.el-radio-group .el-radio-button:has-text("{first_text}")'
                            )
                        ).first

                        if await radio_wrapper.count() > 0:
                            await radio_wrapper.click()
                            await self.page.wait_for_timeout(300)

                            # 通过共享工具强制同步 Vue v-model
                            from .vue_radio_sync import sync_radio_vue_model
                            vue_update_result = await sync_radio_vue_model(self.page, first_text)

                            filled += 1
                            radio_selections[label] = first_text  # ★ 记录实际选择的值
                            LOG.info(f"    选择 radio: {label} = {first_text}")

                            # radio 级联：检测动态组件并自动处理
                            await self.page.wait_for_timeout(1000)  # 等待动态组件渲染

                            # 检测新增 form fields
                            try:
                                post_scan_result = await scan_form_fields(self.page)
                                # 兼容新的返回格式（包含调试信息）
                                post_fields = post_scan_result.get('fields', []) if isinstance(post_scan_result, dict) else post_scan_result
                                current_labels = {f['label'] for f in fields}
                                new_fields = [f for f in post_fields if f['label'] not in current_labels]
                                if new_fields:
                                    LOG.info(f"    [radio-cascade] 新增 {len(new_fields)} 个字段")
                            except Exception:
                                pass

                            # 检测 transfer-box 是否需要选择（如"授权"选择"组织架构"后）
                            # 如果右侧面板为空（无选择），则执行选择
                            try:
                                right_panel_status = await self.page.evaluate("""() => {
                                    const rightPanel = document.querySelector('[class*="transfer-box-right"]');
                                    if (!rightPanel) return { exists: false, hasSelection: false };
                                    const r = rightPanel.getBoundingClientRect();
                                    if (r.width <= 0 || r.height <= 0) return { exists: false, hasSelection: false };

                                    const text = rightPanel.textContent || '';
                                    const hasNoData = text.includes('暂无数据');
                                    const hasSelection = !hasNoData && (text.includes('已选择') || text.includes('已选项'));

                                    return { exists: true, hasSelection };
                                }""")

                                if right_panel_status.get('exists') and not right_panel_status.get('hasSelection'):
                                    LOG.info(f"    [radio-cascade] transfer-box 需要选择")
                                    executor = self._get_executor()
                                    # transfer-box 不需要 selector，使用 None
                                    success, detail = await executor.execute_with_details(
                                        "transfer-box", label, "", None, None
                                    )
                                    if success:
                                        LOG.info(f"    [radio-cascade] transfer-box 选择成功")
                                        # ★ 记录 cascade 操作，供 Stage 2 回放
                                        # selected_items 应包含实际选中的项（option_text）
                                        selected_text = detail.get("option_text", "")
                                        cascade_actions.append({
                                            "type": "transfer-box",
                                            "trigger_field": label,
                                            "trigger_value": first_text,
                                            "selected_items": [selected_text] if selected_text else [],
                                        })
                                    else:
                                        LOG.warning(f"    [radio-cascade] transfer-box 选择失败")
                            except Exception:
                                pass

                        else:
                            # 回退：直接点击 label
                            radio_label = self.page.locator(
                                safe_css(
                                    f'.el-radio-group .el-radio-button__inner:has-text("{first_text}"), '
                                    f'.el-radio-group .el-radio__label:has-text("{first_text}")'
                                )
                            ).first
                            if await radio_label.count() > 0:
                                await radio_label.click()
                                filled += 1
                                radio_selections[label] = first_text  # ★ 记录实际选择的值
                                LOG.info(f"    选择 radio: {label} = {first_text} (label fallback)")

                except Exception as e:
                    LOG.error(f"选择 radio {label} 失败: {e}", exc_info=True)
                continue

            # 查找匹配的填充值
            value = None
            for key, val in fill_data.items():
                if key in label or label in key:
                    value = val
                    break

            if not value:
                continue

            try:
                from .locator_helpers import safe_css
                # 优先使用 scan 返回的精确 selector
                if selector:
                    enhanced = safe_css(selector)
                    await self.page.fill(enhanced, value, timeout=3000)
                    filled += 1
                    continue

                # 回退：通过 label 在 el-form-item 结构中定位 input/textarea
                if field_type == "textarea":
                    css = f'.el-form-item:has(.el-form-item__label:has-text("{label}")) textarea'
                    enhanced = safe_css(css)
                    await self.page.fill(enhanced, value, timeout=3000)
                else:
                    # 复合输入组检测：检查单个 form-item 内是否有多个可见 input
                    form_item_css = f'.el-form-item:has(.el-form-item__label:has-text("{label}"))'
                    enhanced_form_css = safe_css(form_item_css)
                    form_items = await self.page.locator(enhanced_form_css).all()

                    composite_found = False
                    for fi in form_items:
                        inputs_in_this_item = await fi.locator('input:not([readonly])').count()
                        if inputs_in_this_item >= 2:
                            # 这个 form-item 内有多个 input，是复合输入组
                            main_input = fi.locator('input:not([readonly])[placeholder*="请输入"], input:not([readonly])[placeholder*="Enter"]').first
                            if await main_input.count() > 0:
                                await main_input.fill(value, timeout=3000)
                                filled += 1
                                LOG.info(f"    填充复合输入组: {label} = {value} (多input匹配)")
                                composite_found = True
                                break

                    if not composite_found:
                        # 普通单 input 字段
                        css = f'{form_item_css} input:not([readonly])'
                        enhanced = safe_css(css)
                        await self.page.fill(enhanced, value, timeout=3000)
                filled += 1
            except Exception as e:
                LOG.warning(f"填充字段 {label} 失败: {e}")

        return {
            "filled": filled,
            "cascade_actions": cascade_actions,
            "radio_selections": radio_selections,  # ★ 记录 radio 实际选择的值
        }

    async def fill_multi_step_fields(self, fields: List[Dict], framework: str = "element-ui",
                                      max_rounds: int = 5) -> Tuple[int, List[Dict], List[Dict]]:
        """处理 multi_step 类型的表单字段（N 轮重试 + 级联感知）。

        遍历 fields，对 kb_category 在 MULTI_STEP_TYPES 中的字段调用 MultiStepExecutor。
        优先使用 field 中的 selector（来自 Stage 1），KB XPath 作为回退。

        N 轮重试策略（Phase 1b）：
        - 每轮尝试填充所有 pending 字段
        - no_options 的字段延后到下一轮重试（上游填充后下游选项可能加载完成）
        - 最多 max_rounds 轮，提前退出条件：无 pending 字段 或 连续两轮无进展
        - 每轮成功后执行 [D6] 级联检测（日志记录）

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            framework: UI 框架
            max_rounds: 最大重试轮数（默认 5，覆盖 A→B→C→D→E 深度级联）

        Returns:
            (成功处理的字段数, 详细信息列表, 下拉框关闭操作列表)
        """
        from .. import const

        executor = self._get_executor(framework)
        filled = 0
        details = []
        close_dropdown_actions = []

        # 构建待填充队列（仅 multi_step 类型）
        pending = []
        for field in fields:
            kb_cat = field.get("kb_category", "")
            if kb_cat not in const.MULTI_STEP_TYPES and kb_cat != "form-checkbox":
                continue
            pending.append(field)

        if not pending:
            return filled, details, close_dropdown_actions

        LOG.info(f"    [multi-step] 开始 N 轮填充: {len(pending)} 个字段, max_rounds={max_rounds}")

        prev_pending_count = -1  # 用于检测无进展
        filled_selectors = set()  # 已填充字段的 selector 集合（Phase 2a：防循环）
        all_cascade_actions = []  # Phase 2a：收集所有级联填充记录

        for round_num in range(1, max_rounds + 1):
            if not pending:
                break

            # 无进展检测：如果本轮 pending 数量与上轮相同，说明卡住了
            if len(pending) == prev_pending_count:
                LOG.info(f"    [multi-step] Round {round_num}: 连续无进展（{len(pending)} 个 pending），停止重试")
                break
            prev_pending_count = len(pending)

            LOG.info(f"    [multi-step] Round {round_num}/{max_rounds}: {len(pending)} 个待填充")
            still_pending = []
            round_filled = 0

            for field in pending:
                kb_cat = field.get("kb_category", "")
                label = field.get("label", "")
                # 分别获取两种选择器
                selector = field.get("selector")  # 标准 CSS，用于 evaluate
                playwright_locator = field.get("playwright_locator")  # Playwright 语法

                # 为 ip-input 传递额外参数
                extra_kwargs = {}
                if kb_cat == "ip-input":
                    extra_kwargs = {
                        "sub_selectors": field.get("subSelectors", []),
                        "input_count": field.get("inputCount", 4),
                        "ip_version": field.get("ipVersion", "v4"),
                    }

                success, detail = await executor.execute_with_details(
                    kb_cat, label, option_text="", selector=selector,
                    playwright_locator=playwright_locator, **extra_kwargs
                )

                if success:
                    filled += 1
                    round_filled += 1
                    filled_selectors.add(selector)
                    detail_entry = {
                        "label": label,
                        "kb_category": kb_cat,
                        "selector": selector,
                        "playwright_locator": field.get("playwright_locator"),
                        "is_editable": detail.get("is_editable", False),
                        "option_text": detail.get("option_text", ""),
                        "round": round_num,
                    }
                    # ★ Phase 4a 补漏：复制 select_sub_type（el-select 子类型）
                    if "select_sub_type" in detail:
                        detail_entry["select_sub_type"] = detail["select_sub_type"]
                    details.append(detail_entry)
                    LOG.info(f"    ✅ multi_step R{round_num}: {label} ({kb_cat}) → {detail.get('option_text', '')}")

                    # ★ el-select 选择后检查下拉框状态，如仍展开则关闭并记录
                    if kb_cat == "el-select":
                        await self.page.wait_for_timeout(300)
                        dropdown_visible = await self.page.evaluate("""() => {
                            const dropdown = document.querySelector('[x-placement]');
                            if (!dropdown) return false;
                            const style = window.getComputedStyle(dropdown);
                            return style.display !== 'none' && style.visibility !== 'hidden';
                        }""")
                        if dropdown_visible:
                            LOG.info(f"    [el-select] 下拉框仍展开，发送 ESC 关闭: {label}")
                            await self.page.keyboard.press("Escape")
                            close_dropdown_actions.append({
                                "field_label": label,
                                "action": "press_escape",
                                "round": round_num,
                            })

                    # ★ [Phase 2a] 级联检测 + 自动填充
                    if kb_cat in ("el-select", "radio"):
                        cascade_result = await self._detect_and_fill_cascade(
                            label, kb_cat, filled_selectors, depth=0, max_depth=3
                        )
                        cascade_filled = cascade_result.get("cascade_filled", 0)
                        if cascade_filled > 0:
                            filled += cascade_filled
                            round_filled += cascade_filled
                            all_cascade_actions.extend(cascade_result.get("cascade_actions", []))
                            # 将级联产生的 multi_step 字段加入 pending（下一轮处理）
                            for cf in cascade_result.get("cascade_fields", []):
                                cf_cat = cf.get("kb_category", "")
                                if cf_cat in const.MULTI_STEP_TYPES or cf_cat == "form-checkbox":
                                    cf_selector = cf.get("selector", "")
                                    if cf_selector not in filled_selectors:
                                        still_pending.append(cf)
                                        LOG.info(f"    [cascade] 级联字段加入 pending: {cf.get('label')} ({cf_cat})")

                elif detail.get("skipped_reason") == "no_options":
                    still_pending.append(field)
                    if round_num == 1:
                        LOG.info(f"    ⏭️ multi_step R{round_num} 跳过(无选项): {label} ({kb_cat})")
                    # 非首轮仍 no_options 时不输出日志（减少噪音，最终汇总）
                else:
                    # 其他失败（非 no_options）：记录但不再重试
                    details.append({
                        "label": label,
                        "kb_category": kb_cat,
                        "selector": selector,
                        "playwright_locator": field.get("playwright_locator"),
                        "is_editable": detail.get("is_editable", False),
                        "option_text": "",
                        "skipped_reason": detail.get("skipped_reason", "unknown"),
                        "round": round_num,
                    })
                    LOG.warning(f"    ⚠️ multi_step R{round_num} 失败: {label} ({kb_cat})")

            if round_filled > 0:
                LOG.info(f"    [multi-step] Round {round_num} 完成: 填充 {round_filled} 个, "
                         f"剩余 {len(still_pending)} 个待重试")

            pending = still_pending

            # 有 pending 字段时等待后再试
            if pending and round_num < max_rounds:
                LOG.info(f"    [multi-step] 等待 1s 后重试 {len(pending)} 个字段...")
                await self.page.wait_for_timeout(1000)

        # 最终：将始终 no_options 的字段记录到 details
        for field in pending:
            label = field.get("label", "")
            kb_cat = field.get("kb_category", "")
            selector = field.get("selector") or field.get("playwright_locator", "")
            details.append({
                "label": label,
                "kb_category": kb_cat,
                "selector": selector,
                "is_editable": False,
                "option_text": "",
                "skipped_reason": "no_options",
            })
        if pending:
            LOG.warning(f"    [multi-step] {len(pending)} 个字段在 {max_rounds} 轮后仍无选项: "
                        f"{[f.get('label', '?') for f in pending]}")

        return filled, details, close_dropdown_actions

    async def _select_by_css_selector(self, selector: str, label: str) -> bool:
        """CSS selector 回退：直接点击 el-select input 展开 + 选择第一项。

        用于 page-nav 上下文（创建页面），KB XPath 模式无法匹配 DOM 结构时。

        Args:
            selector: CSS 选择器（来自 playbook）
            label: 字段标签（用于日志）

        Returns:
            bool: 是否成功
        """
        try:
            from .locator_helpers import safe_css

            # 取第一个 selector（可能有多个逗号分隔的备选）
            primary_selector = selector.split(",")[0].strip()

            # 点击 el-select 内部的 input 展开下拉框
            input_selector = f"{primary_selector} input.el-input__inner"
            input_el = self.page.locator(safe_css(input_selector)).first
            if await input_el.count() == 0:
                # 回退：直接点击 .el-select
                input_el = self.page.locator(safe_css(primary_selector)).first
            if await input_el.count() == 0:
                LOG.debug(f"    CSS selector 回退: 未找到元素 {primary_selector}")
                return False

            await input_el.click(timeout=3000)
            await self.page.wait_for_timeout(500)

            # 选择第一个可见的下拉选项
            first_option = self.page.locator(
                safe_css('.el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled):visible')
            ).first
            if await first_option.count() > 0:
                await first_option.click(timeout=2000)
                LOG.debug(f"    CSS selector 回退成功: {label}")
                return True

            # 兜底：任何可见的 dropdown item
            any_option = self.page.locator(
                safe_css('.el-select-dropdown__item:visible')
            ).first
            if await any_option.count() > 0:
                await any_option.click(timeout=2000)
                LOG.debug(f"    CSS selector 回退成功(兜底): {label}")
                return True

            LOG.debug(f"    CSS selector 回退: 未找到下拉选项")
            return False
        except Exception as e:
            LOG.debug(f"    CSS selector 回退失败: {label}: {e}")
            return False

    async def fill_edit_form(self, fields: List[Dict],
                              modifications: Dict[str, str] = None) -> int:
        """填充编辑表单（只修改指定字段，不动名称/编码等标识字段）。

        与 fill_create_form 的区别：
        - 只修改 modifications 中指定的字段
        - 默认修改所有可编辑的 input/textarea 字段
        - 跳过 标识字段（账号/用户名等）和复杂组件（select/radio/checkbox 等）

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            modifications: 显式指定 {label: new_value}。
                           为 None 时自动选择可编辑字段。

        Returns:
            成功填充的字段数
        """
        from .. import const
        from core.discovery.shared.fill_helpers import generate_fill_value

        # 标识字段：只跳过账号/用户名等定位字段，避免 marker 失效
        def _is_identifier(label: str, field: dict) -> bool:
            label_lower = label.lower()
            if field.get("readonly") or field.get("disabled"):
                return True
            # 只跳过真正的定位字段
            if any(kw in label_lower for kw in ["账号", "username", "account"]):
                return True
            return False

        ts = str(int(time.time()))[-6:]
        filled = 0

        for field in fields:
            label = field.get("label", "")
            field_type = field.get("type", "input")
            kb_category = field.get("kb_category", "")
            # ★ 优先使用 playwright_locator（更稳定），回退到 selector
            selector = field.get("playwright_locator") or field.get("selector")

            # 标识字段跳过
            if _is_identifier(label, field):
                continue

            # 复杂组件跳过（select/radio/checkbox/list 等不直接编辑）
            if kb_category in const.MULTI_STEP_TYPES or kb_category in ("radio", "form-checkbox", "list-selector"):
                continue

            # 多输入组件跳过（如手机号：国家码+号码，编辑时无法正确处理复合输入）
            if field.get("inputCount", 1) >= 2:
                continue

            # 只处理 input 和 textarea（扫描器已过滤 readonly/disabled）
            if field_type not in ("input", "textarea"):
                continue

            # 确定填充值
            value = None
            if modifications and label in modifications:
                value = modifications[label]
            elif not modifications:
                # generate_fill_value 返回 (value, fill_rule) tuple
                # Stage 1 直接用生成的值，apply_fill_rule 是 Stage 2 回放用的
                value, _fill_rule = generate_fill_value(
                    field_type, field.get("inputType", "text"),
                    username="test", label=label
                )
                if not value:
                    value = f"auto_edited_{ts}"

            if not value:
                continue

            try:
                from .locator_helpers import safe_css
                input_type = field.get("inputType", "text")

                if selector:
                    enhanced = safe_css(selector)
                    if input_type == "number":
                        # el-input-number：点击聚焦 → 清空 → 键入数值 → Tab 触发 blur
                        await self.page.click(enhanced, timeout=3000)
                        await self.page.keyboard.press("Control+A")
                        await self.page.keyboard.press("Backspace")
                        await self.page.keyboard.type(str(value), delay=50)
                        await self.page.keyboard.press("Tab")
                        filled += 1
                        LOG.info(f"    编辑字段(number): {label} = {value}")
                    else:
                        await self.page.fill(enhanced, "", timeout=3000)
                        await self.page.fill(enhanced, value, timeout=3000)
                        filled += 1
                        LOG.info(f"    编辑字段: {label} = {value}")
                else:
                    # 从选择器注册表获取 form item 选择器
                    form_item = self.selectors["form"]["item"]
                    form_label = self.selectors["form"]["label"]
                    if field_type == "textarea":
                        css = f'{form_item}:has({form_label}:has-text("{label}")) textarea'
                    else:
                        css = f'{form_item}:has({form_label}:has-text("{label}")) input:not([readonly])'
                    enhanced = safe_css(css)
                    await self.page.fill(enhanced, "", timeout=3000)
                    await self.page.fill(enhanced, value, timeout=3000)
                    filled += 1
                    LOG.info(f"    编辑字段(回退): {label} = {value}")
            except Exception as e:
                LOG.debug(f"    编辑字段 {label} 失败: {e}")

        return filled

    def _build_fill_data(self, fields: List[Dict], username: str) -> dict:
        """动态构建表单填充数据。

        策略：
        1. 基于字段类型和通用模式生成值（不硬编码模块特定字段）
        2. 从 profile 读取测试数据配置（如有）

        Args:
            fields: scan_form_fields_v2() 返回的字段列表
            username: 用户名（用于名称/用户名类字段）

        Returns:
            {label: value} 字典
        """
        ts = str(int(time.time()))[-6:]
        import random as _rand
        from .. import const

        # 从 profile 读取测试数据配置（如果有）
        # 默认值仅为兜底，实际应从 profile 配置
        _phone = f"138{_rand.randint(10000000, 99999999)}"
        _email = f"at_{ts}@test.com"
        _password = "Test@123456"

        fill_data = {}

        # 为所有字段按类型生成默认值
        for field in fields:
            label = field.get("label", "")
            if not label:
                continue
            field_type = field.get("type", "input")
            input_type = field.get("inputType", "text")
            fill_data[label] = self._default_for_type(field_type, input_type, ts, username)

        return fill_data

    @staticmethod
    def _default_for_type(field_type: str, input_type: str, ts: str, username: str) -> str:
        """根据字段类型生成合理的默认填充值。"""
        import random as _rand
        if field_type == "textarea":
            return f"auto_test_{ts}"
        if input_type == "number":
            return str(_rand.randint(1, 100))
        if input_type == "email":
            return f"at_{ts}@test.com"
        if input_type == "tel":
            return f"138{_rand.randint(10000000, 99999999)}"
        if input_type == "password":
            return f"Test@{_rand.randint(100000, 999999)}"
        if input_type == "url":
            return f"https://test-{ts}.example.com"
        # 默认文本
        return f"AT_{username}_{ts}"

    async def select_dropdowns(self) -> int:
        """选择所有下拉框（包括 el-select 和 el-tree）的第一个选项，返回成功选择的数量"""
        selected = 0
        try:
            # 从选择器注册表获取 select 选择器
            select_container = self.selectors["select"]["container"]
            select_item = self.selectors["select"]["item"]

            # 查找所有 select
            selects = await self.page.query_selector_all(select_container)
            for sel in selects:
                try:
                    await sel.click()
                    await self.page.wait_for_timeout(500)

                    # 选择第一个可见选项
                    options = await self.page.query_selector_all(f'{select_item}:visible')
                    if options:
                        await options[0].click()
                        selected += 1
                        await self.page.wait_for_timeout(300)
                    else:
                        await self.page.keyboard.press('Escape')
                except Exception as e:
                    LOG.warning(f"选择下拉框失败: {e}")
                    await self.page.keyboard.press('Escape')

            # 查找树形选择器（el-tree），递归展开并勾选
            tree_checked = await self._handle_tree_selectors()
            selected += tree_checked

        except Exception as e:
            LOG.warning(f"扫描下拉框失败: {e}")

        return selected

    async def _handle_tree_selectors(self) -> int:
        """处理树形选择器，展开并勾选 checkbox。

        使用 JavaScript 点击绕过 Playwright 可见性检查（树节点可能在折叠区域内）。

        Returns:
            成功勾选的 checkbox 数量
        """
        # 从选择器注册表获取 tree 选择器
        tree_container = self.selectors["tree"]["container"]
        tree_node = self.selectors["tree"]["node"]
        expand_icon = self.selectors["tree"]["expand_icon"]
        checkbox = self.selectors["tree"]["checkbox"]
        checkbox_input = self.selectors["tree"]["checkbox_input"]

        trees = await self.page.query_selector_all(tree_container)
        if not trees:
            return 0

        checked = 0
        for tree in trees:
            try:
                # 先诊断树的状态
                tree_info = await tree.evaluate(f"""el => {{
                    const nodes = el.querySelectorAll('{tree_node}');
                    const expandIcons = el.querySelectorAll('{expand_icon}');
                    const checkboxes = el.querySelectorAll('{checkbox}');
                    const visibleCheckboxes = Array.from(checkboxes).filter(cb => cb.offsetWidth > 0);

                    return {{
                        nodeCount: nodes.length,
                        expandIconCount: expandIcons.length,
                        checkboxCount: checkboxes.length,
                        visibleCheckboxCount: visibleCheckboxes.length,
                        collapsedIcons: Array.from(expandIcons).filter(i => !i.classList.contains('expanded') && !i.classList.contains('is-leaf')).length,
                        checkedCount: Array.from(checkboxes).filter(cb => cb.classList.contains('is-checked')).length,
                        treeVisible: el.offsetWidth > 0,
                        treeRect: el.getBoundingClientRect(),
                    }};
                }}""")

                LOG.info(f"    树形诊断: nodes={tree_info['nodeCount']} checkboxes={tree_info['checkboxCount']} "
                        f"visible={tree_info['visibleCheckboxCount']} collapsed={tree_info['collapsedIcons']} "
                        f"checked={tree_info['checkedCount']}")

                # 如果 checkbox 不可见但有折叠的节点，先展开
                if tree_info['visibleCheckboxCount'] == 0 and tree_info['collapsedIcons'] > 0:
                    LOG.info(f"    树节点已折叠，尝试展开...")
                    expand_result = await self.page.evaluate(f"""el => {{
                        const icons = el.querySelectorAll('{expand_icon}:not(.expanded):not(.is-leaf)');
                        let expanded = 0;
                        icons.forEach(icon => {{
                            try {{
                                icon.click();
                                expanded++;
                            }} catch(e) {{}}
                        }});
                        return {{ attempted: icons.length, expanded }};
                    }}""", tree)
                    LOG.info(f"    展开结果: {expand_result}")
                    await self.page.wait_for_timeout(500)

                # 使用 JavaScript 点击 checkbox（绕过可见性检查）
                js_click_result = await self.page.evaluate(f"""el => {{
                    const result = {{ attempted: 0, clicked: 0, errors: [] }};
                    const checkboxes = el.querySelectorAll('{checkbox_input}');

                    for (const cb of checkboxes) {{
                        result.attempted++;
                        if (result.clicked >= 3) break;  // 最多勾选3个

                        // 检查是否已选中
                        if (cb.closest('{checkbox}')?.classList.contains('is-checked')) {{
                            continue;
                        }}

                        try {{
                            // 使用 JavaScript click 绕过 Playwright 可见性检查
                            cb.click();
                            result.clicked++;
                        }} catch(e) {{
                            result.errors.push(e.message);
                        }}
                    }}
                    return result;
                }}""", tree)

                checked += js_click_result['clicked']
                if js_click_result['clicked'] > 0:
                    LOG.info(f"    ✅ 通过 JS 勾选 {js_click_result['clicked']} 个 checkbox")
                if js_click_result['errors']:
                    LOG.warning(f"    JS 点击失败: {js_click_result['errors'][:3]}")

                await self.page.wait_for_timeout(300)

            except Exception as e:
                LOG.warning(f"    处理树形选择器失败: {e}")

        return checked

    async def submit_form_v2(self, scope_selector: str = "") -> dict:
        """提交表单（v2 版本），返回已验证的 locator、原始文本和成功策略。

        中文按钮文本常含空格（如 "确 定"、"保 存"），使用 JavaScript
        去掉空格后匹配，避免 Playwright has-text 匹配失败。

        Args:
            scope_selector: 可选，限定搜索范围的 CSS 选择器（如 '.el-drawer:not([style*="display: none"])'）

        Returns:
            dict: {"text": str, "locator": str, "click_strategy": str}
            - text: 按钮原始文本（保留空格），如 "确 定"
            - locator: 已验证的 playwright_locator，如 "button:has-text('确 定')"
            - click_strategy: 成功策略 "js" / "playwright"
        """
        from .wait_helpers import wait_for_loading_complete

        # JavaScript 方式：统一去空格归一化匹配提交按钮文本，返回原始文本 + 标签
        try:
            # 如果有 scope_selector，先在限定范围内搜索
            scope_js = f"document.querySelector('{scope_selector}')" if scope_selector else "document"
            clicked = await self.page.evaluate(f"""(() => {{
                const scope = {scope_js};

                // 优先搜索 button 元素，排除标题元素
                const allButtons = scope ? Array.from(scope.querySelectorAll('button, span, a')) : Array.from(document.querySelectorAll('button, span, a'));
                const visible = allButtons.filter(b => {{
                    if (b.offsetWidth <= 0 || b.offsetHeight <= 0) return false;
                    if (b.disabled || b.classList.contains('is-disabled')) return false;
                    if (b.closest('.is-hidden') || b.closest('[style*="display: none"]')) return false;
                    if (b.getAttribute('role') === 'heading') return false;
                    if (/^H[1-6]$/.test(b.tagName)) return false;
                    return true;
                }});

                // 按优先级排序：button > a > span
                visible.sort((a, b) => {{
                    const priority = {{ BUTTON: 1, A: 2, SPAN: 3 }};
                    return (priority[a.tagName] || 99) - (priority[b.tagName] || 99);
                }});

                for (const btn of visible) {{
                    const originalText = btn.textContent.trim();
                    // 统一去空格归一化匹配（长度限制 < 10 避免误中长文本）
                    if (originalText.replace(/\\s+/g, '').length < 10 && {const.js_normalize_in(const.SUBMIT_TEXTS, 'originalText')}) {{
                        btn.click();
                        return {{original: originalText, tag: btn.tagName.toLowerCase()}};
                    }}
                }}
                return null;
            }})()""")
            if clicked:
                await wait_for_loading_complete(self.page)
                original = clicked['original']
                tag = clicked['tag']
                verified_locator = f"{tag}:has-text(\"{original}\")"
                return {"text": original, "locator": verified_locator, "click_strategy": "js"}
        except Exception as e:
            LOG.debug(f"JavaScript 提交按钮匹配失败: {e}")

        # Playwright locator 回退（带隐藏过滤，使用 SUBMIT_TEXTS 列表）
        from .locator_helpers import safe_css
        for text in const.SUBMIT_TEXTS:
            try:
                enhanced = safe_css(f'button:has-text("{text}"):visible')
                btn = self.page.locator(enhanced).first
                if await btn.count() > 0:
                    actual_text = await self.page.evaluate(f"""() => {{
                        const allBtns = Array.from(document.querySelectorAll('button'));
                        for (const b of allBtns) {{
                            if (b.offsetWidth > 0 && b.offsetHeight > 0 && !b.disabled) {{
                                const txt = b.textContent.trim();
                                if (txt.includes("{text}")) {{
                                    return txt;
                                }}
                            }}
                        }}
                        return "{text}";
                    }}""")
                    await btn.click()
                    await wait_for_loading_complete(self.page)
                    verified_locator = f"button:has-text(\"{actual_text}\")"
                    return {"text": actual_text, "locator": verified_locator, "click_strategy": "playwright"}
            except Exception as e:
                LOG.debug(f"尝试提交按钮 {text} 失败: {e}")
                continue

        # 最终回退: primary 按钮（带隐藏过滤）
        try:
            enhanced_primary = safe_css('button.el-button--primary:visible')
            primary_btn = self.page.locator(enhanced_primary).first
            if await primary_btn.count() > 0:
                actual_text = await self.page.evaluate("""() => {
                    const btns = Array.from(document.querySelectorAll('button.el-button--primary'));
                    for (const b of btns) {
                        if (b.offsetWidth > 0 && b.offsetHeight > 0 && !b.disabled) {
                            return b.textContent.trim();
                        }
                    }
                    return "primary";
                }""")
                await primary_btn.click()
                await wait_for_loading_complete(self.page)
                return {"text": actual_text, "locator": "button.el-button--primary", "click_strategy": "playwright"}
        except Exception as e:
            LOG.debug(f"尝试 primary 按钮失败: {e}")

        return {"text": "", "locator": ""}


async def scan_form_fields(page: Page, ui_framework: str = "element-ui", scope_selector: str = "") -> List[Dict]:
    """扫描页面表单字段，按 form-item 遍历，检测组件类型。

    以表单项为单位遍历，检测子元素的 DOM 类名判断组件类型。
    每个字段携带 kb_category，对应 probe_knowledge.json 的类别名。

    Args:
        page: Playwright 页面对象
        ui_framework: UI 框架名称

    Returns:
        表单字段列表，每个字段包含：
        - label: 标签文本
        - type: 粗粒度类型 (input/textarea/select/cascader/date-picker/radio/checkbox)
        - kb_category: KB 类别名 (input-generic/el-select/el-cascader/...)
        - selector: CSS 选择器（仅 input/textarea 有值）
        - inputType: input type 属性（仅 input 有值）
        - required: 是否必填
        - firstOptionText: 第一个选项文本（仅 radio 有值）
    """
    # 从选择器注册表注入 JS
    selectors = const.get_ui_selectors(ui_framework)
    form_item = selectors["form"]["item"]
    form_label = selectors["form"]["label"]
    select_container = selectors["select"]["container"]
    cascader_container = selectors["cascader"]["container"]
    date_picker = selectors["date_picker"]["editor"]
    radio_group = selectors["radio"]["group"]
    radio_button = selectors["radio"]["button_inner"]
    radio_label = selectors["radio"]["label"]
    checkbox_container = selectors["checkbox"]["container"]
    table_container = selectors["table"]["container"]

    # 如果有 scope_selector，限定搜索范围到指定容器
    _scope_js = f"document.querySelector('{scope_selector}')" if scope_selector else "document"

    script = f"""
    () => {{
        const FORM_ITEM_SEL = '{form_item}';
        const fields = [];
        const _debug_ipCheck = [];
        const _debug_multiInput = [];
        const _debug_addBtn = [];
        const _debug_multiSelect = [];
        const _debug_composite = [];

        // 以 form-item 为单位遍历（支持 scope 限定）
        const scope = {_scope_js};
        const formItems = scope ? scope.querySelectorAll('{form_item}') : document.querySelectorAll('{form_item}');

        formItems.forEach(fi => {{
            // 1. 提取 label（优先 .el-form-item__label，fallback 到 input placeholder）
            const labelEl = fi.querySelector('{form_label}');
            let label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            if (!label) {{
                // fallback: 从 input/textarea 的 placeholder 获取
                const inp = fi.querySelector('input:not([type="hidden"]), textarea');
                if (inp && inp.placeholder) {{
                    label = inp.placeholder.replace(/^[请输入]*[：:]?/, '').trim();
                }}
            }}
            if (!label) return;

            const required = fi.classList.contains('is-required') ||
                             fi.querySelector('[class*="required"]') !== null;

            // 跳过不可见的表单项
            const r = fi.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;

            // 2. 按优先级检测组件类型

            // 2.1 复合组件检测（增强版）：find ALL selects, filter internal inputs
            const selects = Array.from(fi.querySelectorAll('{select_container}'));
            const selectEl = selects[0] || null;  // 兼容 2.4 单 select 路径
            const independentInputs = Array.from(fi.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'))
                .filter(inp => !inp.readOnly && inp.offsetWidth > 0
                    && !selects.some(s => s.contains(inp)));

            if (selects.length > 0 && independentInputs.length > 0) {{
                // [D5] 复合组件结构诊断
                const allRawInputs = Array.from(fi.querySelectorAll('input:not([type="hidden"])'))
                    .filter(inp => inp.offsetWidth > 0);
                const labelLower = label.toLowerCase();
                _debug_composite.push({{
                    label: label,
                    selectCount: selects.length,
                    independentInputCount: independentInputs.length,
                    totalInputCount: allRawInputs.length,
                    hasIpInLabel: /ip|ipv4|ipv6|地址|网段/i.test(label),
                    isIpMaskCandidate: (independentInputs.length === 4 || independentInputs.length === 8 || independentInputs.length === 16),
                    inputDetails: independentInputs.map((inp, idx) => ({{
                        idx: idx,
                        type: inp.type,
                        width: inp.offsetWidth,
                        placeholder: inp.placeholder,
                        readOnly: inp.readOnly,
                        inInputNumber: !!inp.closest('.el-input-number'),
                        maxLength: inp.getAttribute('maxlength') || '',
                    }})),
                }});

                // ★ 模式匹配：IP+掩码复合组件
                const hasIpInLabel = /ip|ipv4|ipv6|地址|网段/i.test(label);
                const isIpMaskCandidate = (independentInputs.length === 4 || independentInputs.length === 8 || independentInputs.length === 16)
                    && hasIpInLabel && selects.length === 1;

                if (isIpMaskCandidate) {{
                    // IP+掩码：4/8/16 个 IP 段 + 1 个掩码选择框
                    const widths = independentInputs.map(inp => inp.offsetWidth);
                    const maxW = Math.max(...widths);
                    const minW = Math.min(...widths);
                    const widthRatio = minW > 0 ? (maxW / minW) : 999;

                    if (widthRatio < 1.5) {{
                        const ipVersion = /ipv6|v6/i.test(label) ? 'v6' : 'v4';
                        const subSelectors = independentInputs.map(inp => _buildSelector(inp, label).selector);

                        fields.push(Object.assign({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            subSelectors: subSelectors,
                            inputCount: independentInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }}, _buildComponentSelector(fi, '{form_item}', fields.length, label)));
                        fields.push(Object.assign({{
                            label: label + '(掩码)', type: 'select', kb_category: 'el-select',
                            required,
                        }}, _buildSelector(selects[0], label + '(掩码)')));
                        return;
                    }}
                }}

                // 通用模式：逐个输出 select，再输出独立 input
                selects.forEach((sel, idx) => {{
                    const isMulti = sel.classList.contains('el-select--multiple')
                        || sel.querySelector('.el-select__tags') !== null
                        || sel.querySelector('input[multiple]') !== null;
                    const labelSuffix = selects.length > 1 ? '(' + (idx + 1) + ')' : '(下拉)';

                    // ★ 统一使用 _buildSelector 生成双定位器（selector + playwright_locator）
                    const selResult = _buildSelector(sel, label + labelSuffix);

                    fields.push(Object.assign({{ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 required }}, selResult));
                    if (isMulti) {{
                        _debug_multiSelect.push({{
                            label: label + labelSuffix,
                            hasMultipleClass: sel.classList.contains('el-select--multiple'),
                            hasTagsContainer: sel.querySelector('.el-select__tags') !== null,
                            currentTagCount: (sel.querySelectorAll('.el-tag') || []).length,
                        }});
                    }}
                }});
                independentInputs.forEach(inp => {{
                    fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                 inputType: inp.type || 'text', required }}, _buildSelector(inp, label)));
                }});
            }}
            else if (fi.querySelector('{cascader_container}')) {{
                const cascaderIdx = fields.length;
                fields.push(Object.assign({{ label, type: 'cascader', kb_category: 'el-cascader',
                             required }}, _buildComponentSelector(fi, '{cascader_container}', cascaderIdx, label)));
            }}
            else if (fi.querySelector('{date_picker}')) {{
                const datePickerIdx = fields.length;
                fields.push(Object.assign({{ label, type: 'date-picker', kb_category: 'date-picker',
                             required }}, _buildComponentSelector(fi, '{date_picker}', datePickerIdx, label)));
            }}
            else if (selects.length > 0) {{
                // 2.4 增强：处理 form-item 内的多个 select（如授权主体：类型选择 + 实体选择）
                selects.forEach((sel, idx) => {{
                    const labelSuffix = selects.length > 1 ? '(' + (idx + 1) + ')' : '';
                    // 使用 _buildSelector 生成唯一 CSS 路径（区分同 form-item 内的多个 select）
                    const selSelectors = _buildSelector(sel, label + labelSuffix);

                    fields.push(Object.assign({{ label: label + labelSuffix, type: 'select', kb_category: 'el-select',
                                 required }}, selSelectors));
                    // [D4] el-select 多选调试
                    const isMulti = sel.classList.contains('el-select--multiple')
                        || sel.querySelector('.el-select__tags') !== null
                        || sel.querySelector('input[multiple]') !== null;
                    if (isMulti) {{
                        _debug_multiSelect.push({{
                            label: label + labelSuffix,
                            hasMultipleClass: sel.classList.contains('el-select--multiple'),
                            hasTagsContainer: sel.querySelector('.el-select__tags') !== null,
                            currentTagCount: (sel.querySelectorAll('.el-tag') || []).length,
                        }});
                    }}
                }});
            }}
            else if (fi.querySelector('{radio_group}')) {{
                const radioIdx = fields.length;
                const firstRadio = fi.querySelector('{radio_button}, {radio_label}');
                fields.push(Object.assign({{ label, type: 'radio', kb_category: 'radio',
                             required,
                             firstOptionText: firstRadio ? firstRadio.textContent.trim() : '' }}, _buildComponentSelector(fi, '{radio_group}', radioIdx, label)));
            }}
            else if (fi.querySelector('{checkbox_container}') && !fi.closest('{table_container}')) {{
                const checkboxIdx = fields.length;
                fields.push(Object.assign({{ label, type: 'checkbox', kb_category: 'form-checkbox',
                             required }}, _buildComponentSelector(fi, '{checkbox_container}', checkboxIdx, label)));
            }}
            else {{
                // 检测列表选择器（穿梭框 / transfer-like 组件）
                // 特征：搜索 input + 可点击列表项（>=3 个选项）
                const listPanel = fi.querySelector(
                    '.el-transfer, .el-transfer-panel, ' +
                    '[class*="transfer"], [class*="picker-panel"], ' +
                    '[class*="select-panel"], [class*="list-panel"]'
                );
                const listItems = fi.querySelectorAll(
                    'li[class*="item"], .el-checkbox-group .el-checkbox, ' +
                    '[class*="option-item"], [class*="list-item"]'
                );
                const searchInput = fi.querySelector('input[placeholder]');

                if ((listPanel || listItems.length >= 3) && searchInput) {{
                    const listIdx = fields.length;
                    fields.push(Object.assign({{
                        label, type: 'list-selector',
                        kb_category: 'list-selector',
                        required,
                        itemCount: listItems.length
                    }}, _buildComponentSelector(fi, listPanel ? listPanel.tagName.toLowerCase() : 'div', listIdx, label)));
                }}
                // 通用 input / textarea
                else {{
                // 检测复合输入组：多个可见 input（如手机号 = 国家编码 + 手机号）
                const allInputs = Array.from(fi.querySelectorAll(
                    'input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'
                )).filter(inp => !inp.readOnly && inp.offsetWidth > 0);

                // [D1] 调试: 检查是否有 radio input 被误匹配到 allInputs
                const _allRawInputs = Array.from(fi.querySelectorAll('input:not([type="hidden"])'));
                const _radioInAllRaw = _allRawInputs.filter(inp => inp.type === 'radio' || inp.closest('.el-radio-group'));
                if (_radioInAllRaw.length > 0) {{
                    _debug_multiInput.push({{
                        formItemLabel: label,
                        allRawCount: _allRawInputs.length,
                        allInputsCount: allInputs.length,
                        radioInRawCount: _radioInAllRaw.length,
                        details: _radioInAllRaw.map(inp => ({{
                            type: inp.type,
                            className: (inp.className || '').substring(0, 60),
                            parentIsRadio: inp.closest('.el-radio-group') !== null,
                            parentIsRadioBtn: inp.closest('.el-radio-button') !== null,
                        }})),
                    }});
                }}

                // [D3] 调试: IP 输入组件 DOM 结构确认
                if (allInputs.length >= 2) {{
                    const widths = allInputs.map(inp => inp.offsetWidth);
                    const maxW = Math.max(...widths);
                    const minW = Math.min(...widths);
                    _debug_ipCheck.push({{
                        label: label,
                        inputCount: allInputs.length,
                        widthRatio: minW > 0 ? (maxW / minW).toFixed(2) : 'inf',
                        hasIpInLabel: /ip|地址|IP/i.test(label),
                        hasElInputNumber: !!fi.querySelector('.el-input-number'),
                        hasRadioGroup: !!fi.querySelector('{radio_group}'),
                        hasAddBtn: !!fi.querySelector('button.el-button--text'),
                        tagCount: fi.querySelectorAll('.el-tag').length,
                        subInputDetails: allInputs.map((inp, i) => ({{
                            idx: i,
                            type: inp.type || 'text',
                            width: inp.offsetWidth,
                            placeholder: inp.placeholder || '',
                            readOnly: inp.readOnly,
                        }})),
                    }});
                }}

                const ta = fi.querySelector('textarea');

                // 7.5 IP 输入组检测（在复合输入组之前）
                // 检测条件：4 或 8 个 input + 宽度相近 + label 含 IP
                const labelLower = label.toLowerCase();
                const isIpLabel = /ip|ipv4|ipv6|地址/.test(labelLower);
                // ★ 区分真正的数字输入 vs IP 多段输入：
                // - 真正的 el-input-number：1 个 input 在 .el-input-number 内
                // - IP 多段输入：4/8/16 个 input，每个都在 .el-input-number 内（用于限制 0-255）
                const allInInputNumber = allInputs.length > 0 && allInputs.every(inp => !!inp.closest('.el-input-number'));
                const isIpMultiSegment = allInputs.length === 4 || allInputs.length === 8 || allInputs.length === 16;
                // 只有当"不是 IP 多段数量"且"所有 input 都在 el-input-number 内"时，才排除
                const hasIpInputNumber = !isIpMultiSegment && allInInputNumber;
                const hasIpRadioGroup = !!fi.querySelector('{radio_group}');

                if (!hasIpInputNumber && !hasIpRadioGroup && isIpLabel &&
                    (allInputs.length === 4 || allInputs.length === 8 || allInputs.length === 16)) {{
                    // 检查宽度是否相近（排除不同宽度的 input 组合）
                    const widths = allInputs.map(inp => inp.offsetWidth);
                    const maxW = Math.max(...widths);
                    const minW = Math.min(...widths);
                    const widthRatio = minW > 0 ? (maxW / minW) : 999;

                    if (widthRatio < 1.5) {{
                        // 判断 IPv4/IPv6
                        let ipVersion = 'v4';
                        if (labelLower.includes('ipv6') || labelLower.includes('v6')) {{
                            ipVersion = 'v6';
                        }} else if (allInputs.length > 0) {{
                            // 辅助判断：maxlength >= 4 或 placeholder 含 ":"
                            const firstInput = allInputs[0];
                            const maxLength = parseInt(firstInput.getAttribute('maxlength') || '3');
                            const placeholder = firstInput.placeholder || '';
                            if (maxLength >= 4 || placeholder.includes(':')) {{
                                ipVersion = 'v6';
                            }}
                        }}

                        // 构建每个 input 的 selector
                        const subSelectors = allInputs.map((inp, idx) => {{
                            const path = [];
                            let current = inp;
                            while (current && current.nodeType === Node.ELEMENT_NODE) {{
                                let selector = current.nodeName.toLowerCase();
                                if (current.id) {{
                                    selector = `#${{current.id}}`;
                                    path.unshift(selector);
                                    break;
                                }} else {{
                                    let sibling = current;
                                    let nth = 1;
                                    while (sibling.previousElementSibling) {{
                                        sibling = sibling.previousElementSibling;
                                        if (sibling.nodeName === current.nodeName) nth++;
                                    }}
                                    if (nth > 1 || current.nextElementSibling) {{
                                        selector += `:nth-of-type(${{nth}})`;
                                    }}
                                }}
                                path.unshift(selector);
                                current = current.parentElement;
                            }}
                            return path.join(' > ');
                        }});

                        fields.push(Object.assign({{
                            label, type: 'ip-input', kb_category: 'ip-input',
                            subSelectors: subSelectors,
                            inputCount: allInputs.length,
                            ipVersion: ipVersion,
                            required,
                        }}, _buildComponentSelector(fi, '{form_item}', fields.length, label)));
                        return; // 已识别为 IP，跳过后续检测
                    }}
                }}

                if (allInputs.length >= 2) {{
                    // 复合输入组：过滤辅助输入，选择主输入
                    const mainInput = allInputs.find(inp => {{
                        // 优先选择 placeholder 包含"请输入"的
                        if (inp.placeholder && (inp.placeholder.includes('请输入') || inp.placeholder.includes('Enter'))) {{
                            return true;
                        }}
                        // 或选择宽度最大的
                        return false;
                    }}) || allInputs.reduce((max, inp) => inp.offsetWidth > max.offsetWidth ? inp : max, allInputs[0]);

                    const ir = mainInput.getBoundingClientRect();
                    if (ir.width > 0 && ir.height > 0) {{
                        fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                     inputType: mainInput.type || 'text', required,
                                     inputCount: allInputs.length }}, _buildSelector(mainInput, label)));
                    }}
                }} else if (allInputs.length === 1) {{
                    // 单个 input
                    const inp = allInputs[0];
                    const ir = inp.getBoundingClientRect();
                    if (ir.width > 0 && ir.height > 0) {{
                        fields.push(Object.assign({{ label, type: 'input', kb_category: 'input-generic',
                                     inputType: inp.type || 'text', required }}, _buildSelector(inp, label)));
                    }}
                }} else if (ta) {{
                    fields.push(Object.assign({{ label, type: 'textarea', kb_category: 'textarea-generic',
                                 required }}, _buildSelector(ta, label)));
                }}
                }}

                // [D2] 调试: "添加"按钮行为确认
                const _addBtn = fi.querySelector('button.el-button--text');
                const _tagCount = fi.querySelectorAll('.el-tag').length;
                if (_addBtn) {{
                    _debug_addBtn.push({{
                        formItemLabel: label,
                        btnText: _addBtn.textContent.trim(),
                        visible: _addBtn.offsetWidth > 0,
                        currentTags: _tagCount,
                    }});
                }}
            }}
        }});

        // Fallback: 如果 .el-form-item 扫描到 0 个字段，直接搜索 scope 内所有可见 input
        if (fields.length === 0 && scope) {{
            const directInputs = scope.querySelectorAll('input.el-input__inner');
            let visibleIndex = 0;  // ★ 可见字段的顺序索引（跨会话稳定）
            directInputs.forEach((inp, idx) => {{
                const ir = inp.getBoundingClientRect();
                if (ir.width <= 0 || ir.height <= 0) return;
                if (inp.readOnly || inp.disabled) return;

                const currentVisibleIndex = visibleIndex;
                visibleIndex++;

                // ★ 保存 placeholder 作为独立的字段识别特征（跨会话稳定）
                const rawPlaceholder = inp.placeholder || '';
                const placeholder = rawPlaceholder.replace(/^[请输入]*[：:]?\s*/, '').trim();

                // 1) 优先从 .el-form-item__label 获取 label（标准结构）
                let label = '';
                const formItem = inp.closest('.el-form-item');
                if (formItem) {{
                    const labelEl = formItem.querySelector('.el-form-item__label');
                    if (labelEl) label = labelEl.textContent.trim().replace(/[：:]/g, '');
                }}

                // 2) 表格内字段：优先用 thead 列名（最稳定、最有意义）
                let theadLabel = '';
                const td = inp.closest('td');
                if (td) {{
                    const table = td.closest('table');
                    if (table) {{
                        const cellIndex = td.cellIndex;
                        const th = table.querySelector('thead tr th:nth-child(' + (cellIndex + 1) + ')');
                        if (th) theadLabel = th.textContent.trim();
                    }}
                }}
                if (theadLabel && !label) label = theadLabel;

                // 3) 仍无 label 才用 placeholder（去掉"请输入"前缀后的干净文本）
                if (!label && placeholder) label = placeholder;

                // 4) 最后兜底：空字符串 + 标记
                let isFallbackLabel = false;
                if (!label) {{
                    label = '';
                    isFallbackLabel = true;
                }}

                // 检测 el-input-number 父容器
                const isNumber = !!inp.closest('.el-input-number');

                // ★ 返回完整字段信息，含多种定位特征用于跨会话定位
                const selResult = _buildSelector(inp, label);
                const fieldEntry = {{
                    label, type: 'input', kb_category: 'input-generic',
                    selector: selResult.selector,
                    playwright_locator: selResult.playwright_locator,
                    inputType: isNumber ? 'number' : (inp.type || 'text'),
                    required: false,
                    visible_index: currentVisibleIndex  // ★ 在 scope 内的可见字段顺序
                }};
                if (isFallbackLabel) fieldEntry.is_fallback_label = true;
                if (rawPlaceholder) fieldEntry.placeholder = rawPlaceholder;
                if (theadLabel) fieldEntry.thead_label = theadLabel;
                fields.push(fieldEntry);
            }});
        }}

        // Fallback 2: 检查是否有被遗漏的 el-select（同一 form-item 内有多个组件时可能发生）
        // 复用上面已声明的 formItems 变量，不重复声明
        formItems.forEach(fi => {{
            const r = fi.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) return;

            const labelEl = fi.querySelector('{form_label}');
            let label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            if (!label) return;

            // 检查该 form-item 内的所有 el-select
            const selects = fi.querySelectorAll('{select_container}');
            selects.forEach((sel, selIdx) => {{
                const selR = sel.getBoundingClientRect();
                if (selR.width <= 0 || selR.height <= 0) return;

                // 检查该 el-select 是否已被 fields 覆盖（通过 label + type 匹配）
                const alreadyCovered = fields.some(f =>
                    f.label === label && f.type === 'select' && f.kb_category === 'el-select'
                );
                if (!alreadyCovered) {{
                    const selectIdx = fields.length;
                    const compSel = _buildComponentSelector(fi, '{select_container}', selectIdx, label);
                    if (compSel && compSel.selector) {{
                        fields.push(Object.assign({{
                            label: label,
                            type: 'select',
                            kb_category: 'el-select',
                            required: fi.classList.contains('is-required') ||
                                       fi.querySelector('[class*="required"]') !== null,
                        }}, compSel));
                    }}
                }}
            }});
        }});

        return {{
            fields: fields,
            _debug_ipCheck: _debug_ipCheck,
            _debug_multiInput: _debug_multiInput,
            _debug_addBtn: _debug_addBtn,
            _debug_multiSelect: _debug_multiSelect,
            _debug_composite: _debug_composite,
        }};

        function _buildSelector(element, labelText) {{
            // 优先级 1: #id（最稳定，两个上下文都支持）
            if (element.id) return {{ selector: `#${{element.id}}`, playwright_locator: `#${{element.id}}` }};
            // 优先级 2: [name] 属性（两个上下文都支持）
            if (element.name) return {{ selector: `[name="${{element.name}}"]`, playwright_locator: `[name="${{element.name}}"]` }};

            // 优先级 3a: DOM path（标准 CSS，evaluate 安全）
            const path = [];
            let current = element;
            while (current && current.nodeType === Node.ELEMENT_NODE) {{
                let selector = current.nodeName.toLowerCase();
                if (current.id) {{
                    selector = `#${{current.id}}`;
                    path.unshift(selector);
                    break;
                }} else {{
                    let sibling = current;
                    let nth = 1;
                    while (sibling.previousElementSibling) {{
                        sibling = sibling.previousElementSibling;
                        if (sibling.nodeName === current.nodeName) nth++;
                    }}
                    if (nth > 1 || current.nextElementSibling) {{
                        selector += `:nth-of-type(${{nth}})`;
                    }}
                }}
                path.unshift(selector);
                current = current.parentElement;
            }}
            const domPath = path.join(' > ');

            // 优先级 3b: Label-based（Playwright 语法，跨会话稳定）
            let playwrightLocator = null;
            if (labelText && labelText.length > 0) {{
                const escaped = labelText.replace(/"/g, '\\"');
                playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${{escaped}}")) input`;
            }}

            return {{ selector: domPath, playwright_locator: playwrightLocator }};
        }}

        function _buildComponentSelector(formItem, componentSelector, fieldIndex, labelText) {{
            // 为复杂组件（select/cascader/date-picker/radio/checkbox）生成 CSS selector
            const component = formItem.querySelector(componentSelector);
            if (!component) return {{ selector: null, playwright_locator: null }};

            // 优先级 1: 使用 formItem 在直接子元素中的位置定位（标准 CSS，evaluate 安全）
            // 注意：children 只返回直接子元素，querySelectorAll 会递归搜索所有后代
            // 对于多卡片表单（.el-card 包裹），必须用 children 而非 querySelectorAll
            const directChildren = formItem.parentElement.children;
            let nth = 1;
            for (let i = 0; i < directChildren.length; i++) {{
                if (directChildren[i] === formItem) {{
                    nth = i + 1;
                    break;
                }}
            }}
            const domPath = FORM_ITEM_SEL + ':nth-child(' + nth + ') ' + componentSelector;

            // 优先级 2: Label-based（Playwright 语法，跨会话稳定）
            let playwrightLocator = null;
            if (labelText && labelText.length > 0) {{
                const escaped = labelText.replace(/"/g, '\\"');
                playwrightLocator = `.el-form-item:has(.el-form-item__label:has-text("${{escaped}}")) ${{componentSelector}}`;
            }}

            return {{ selector: domPath, playwright_locator: playwrightLocator }};
        }}
    }}
    """

    return await page.evaluate(script)


async def fill_form(page: Page, fields: List[Dict], data: Dict[str, str]) -> Tuple[int, int]:
    """填充表单字段。

    Args:
        page: Playwright 页面对象
        fields: 表单字段列表（来自 scan_form_fields）
        data: 填充数据 {label: value}

    Returns:
        (成功填充数, 总字段数)
    """
    filled = 0
    total = len(fields)
    from .locator_helpers import safe_css

    for field in fields:
        label = field['label']
        if label not in data:
            continue

        value = data[label]
        # ★ 优先使用 playwright_locator（更稳定），回退到 selector
        selector = field.get('playwright_locator') or field['selector']
        field_type = field['type']

        try:
            enhanced = safe_css(selector)
            if field_type == 'input' or field_type == 'textarea':
                # 清空并填充
                await page.fill(enhanced, '')
                await page.fill(enhanced, value)
                filled += 1

            elif field_type == 'select':
                # Element UI 下拉框
                await page.click(enhanced)
                await page.wait_for_timeout(500)

                # 选择匹配的选项（带隐藏过滤）
                hidden_css = const.HIDDEN_FILTERS_CSS.get('element-ui', const.HIDDEN_FILTERS_CSS['_universal'])
                options = await page.query_selector_all(f'.el-select-dropdown__item:visible{hidden_css}')
                for opt in options:
                    text = await opt.inner_text()
                    if value in text:
                        await opt.click()
                        filled += 1
                        break

                await page.wait_for_timeout(300)

        except Exception as e:
            print(f"  ⚠️ 填充字段 {label} 失败: {e}")

    return filled, total


async def select_dropdown_option(page: Page, selector: str, value: str) -> bool:
    """选择下拉框选项。

    Args:
        page: Playwright 页面对象
        selector: 下拉框选择器
        value: 要选择的值（部分匹配）

    Returns:
        是否成功选择
    """
    try:
        from .locator_helpers import safe_css
        enhanced = safe_css(selector)
        # 点击下拉框
        await page.click(enhanced)
        await page.wait_for_timeout(500)

        # 查找并选择选项（带隐藏过滤）
        hidden_css = const.HIDDEN_FILTERS_CSS.get('element-ui', const.HIDDEN_FILTERS_CSS['_universal'])
        options = await page.query_selector_all(f'.el-select-dropdown__item:visible{hidden_css}')
        for opt in options:
            text = await opt.inner_text()
            if value in text:
                await opt.click()
                await page.wait_for_timeout(300)
                return True

        # 未找到匹配选项，点击空白关闭
        await page.keyboard.press('Escape')
        return False

    except Exception as e:
        print(f"  ⚠️ 选择下拉框失败: {e}")
        return False


async def read_form_errors(page: Page) -> List[Dict]:
    """获取表单验证错误信息（结构化，含字段定位）。

    与旧版 get_form_errors() 的区别：
    - 返回 dict 列表，含 field_label + error_text + severity
    - 尝试将错误信息关联到具体的表单字段
    - 支持 Element UI 和 Ant Design

    Returns:
        [{"field_label": "用户名", "error_text": "用户名已存在", "severity": "field"}, ...]
    """
    script = """
    () => {
        const errors = [];

        // Element UI: 字段级错误
        document.querySelectorAll('.el-form-item').forEach(fi => {
            const errorEl = fi.querySelector('.el-form-item__error');
            if (!errorEl) return;
            const text = errorEl.textContent.trim();
            if (!text) return;
            const labelEl = fi.querySelector('.el-form-item__label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            errors.push({
                field_label: label,
                error_text: text,
                severity: 'field',
                source: 'element-ui'
            });
        });

        // Ant Design: 字段级错误
        document.querySelectorAll('.ant-form-item').forEach(fi => {
            const errorEl = fi.querySelector('.ant-form-item-explain-error, .ant-form-item-explain');
            if (!errorEl) return;
            const text = errorEl.textContent.trim();
            if (!text) return;
            const labelEl = fi.querySelector('.ant-form-item-label label');
            const label = labelEl ? labelEl.textContent.trim().replace(/[：:]/g, '') : '';
            errors.push({
                field_label: label,
                error_text: text,
                severity: 'field',
                source: 'ant-design'
            });
        });

        // 全局错误消息（El-Message / El-Notification / Ant-Message）
        const globalSelectors = [
            '.el-message--error', '.el-notification--error .el-notification__content',
            '.ant-message-error', '.ant-notification-notice-error'
        ];
        document.querySelectorAll(globalSelectors.join(',')).forEach(el => {
            const text = el.textContent.trim();
            if (!text) return;
            errors.push({
                field_label: '',
                error_text: text,
                severity: 'global',
                source: 'message'
            });
        });

        return errors;
    }
    """

    try:
        return await page.evaluate(script)
    except Exception as e:
        LOG.debug(f"读取表单错误失败: {e}")
        return []


async def get_form_errors(page: Page) -> List[str]:
    """获取表单验证错误信息（纯文本列表，向后兼容）。

    Returns:
        错误信息列表
    """
    errors = await read_form_errors(page)
    return [e["error_text"] for e in errors if e.get("error_text")]


class MultiStepExecutor:
    """多步交互执行器：根据 KB multi_step 模板驱动复杂组件交互。

    支持 el-select、el-cascader、date-picker、form-checkbox 四种组件类型。
    每种组件有专属的执行序列，从 KB 读取步骤模板并按序执行。
    """

    def __init__(self, page: Page, kb, framework: str = "element-ui"):
        """初始化执行器。

        Args:
            page: Playwright Page 对象
            kb: ProbeKB 知识库实例
            framework: UI 框架 ("element-ui" 或 "ant-design")
        """
        self.page = page
        self.kb = kb
        self.framework = framework

    async def execute(self, kb_category: str, label: str,
                     option_text: str = "", options: list = None,
                     selector: str = "") -> bool:
        """根据 kb_category 分发到对应的执行方法。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader", "date-picker"）
            label: 字段标签（用于定位组件）
            option_text: 要选择的选项文本
            options: 级联选择的多级选项列表（如 ["uiautotest", "uiautotest"]）
            selector: CSS selector（来自 playbook，直接定位元素）

        Returns:
            bool: 是否成功完成交互
        """
        handlers = {
            "el-select": self._execute_select,
            "el-cascader": self._execute_cascader,
            "date-picker": self._execute_date_picker,
            "form-checkbox": self._execute_form_checkbox,
            "list-selector": self._execute_list_selector,
            "transfer-box": self._execute_transfer_box,
        }
        handler = handlers.get(kb_category)
        if not handler:
            LOG.warning(f"MultiStepExecutor: 无处理器 {kb_category}")
            return False

        try:
            return await handler(label, option_text, options, selector)
        except Exception as e:
            LOG.warning(f"MultiStepExecutor: {kb_category}({label}) 失败: {e}")
            return False

    async def execute_with_details(self, kb_category: str, label: str,
                                   option_text: str = "", options: list = None,
                                   selector: str = "", playwright_locator: str = None,
                                   **kwargs) -> Tuple[bool, Dict]:
        """根据 kb_category 分发到对应的执行方法，并返回详细执行信息。

        与 execute() 类似，但返回元组 (成功标志, 详情字典)。
        详情字典包含 is_editable 和 option_text 等运行时信息。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader", "date-picker", "ip-input"）
            label: 字段标签（用于定位组件）
            option_text: 要选择的选项文本
            options: 级联选择的多级选项列表（如 ["uiautotest", "uiautotest"]）
            selector: CSS selector（用于 evaluate 上下文，标准 CSS）
            playwright_locator: Playwright locator（用于 Playwright API，可含 :has-text() 等扩展语法）
            **kwargs: 额外参数（如 ip-input 需要的 sub_selectors, input_count, ip_version）

        Returns:
            Tuple[bool, Dict]: (是否成功完成交互, 详情字典)
        """
        handlers = {
            "el-select": self._execute_select_with_details,
            "el-cascader": self._execute_cascader_with_details,
            "date-picker": self._execute_date_picker_with_details,
            "form-checkbox": self._execute_form_checkbox_with_details,
            "list-selector": self._execute_list_selector_with_details,
            "transfer-box": self._execute_transfer_box,
            "ip-input": self._execute_ip_input_with_details,
        }
        handler = handlers.get(kb_category)
        if not handler:
            LOG.warning(f"MultiStepExecutor: 无处理器 {kb_category}")
            return False, {}

        try:
            if kb_category == "ip-input":
                return await handler(label, kwargs)
            else:
                # 所有处理器统一接收双选择器
                return await handler(label, option_text, options, selector, playwright_locator)
        except Exception as e:
            LOG.warning(f"MultiStepExecutor: {kb_category}({label}) 失败: {e}")
            return False, {}

    async def _execute_select_with_details(self, label: str, option_text: str,
                                           _options, selector: str = "", playwright_locator: str = None) -> Tuple[bool, Dict]:
        """el-select 多步交互，并返回详细的执行信息。

        Returns:
            Tuple[bool, Dict]: (是否成功, {"is_editable": bool, "option_text": str})
        """
        steps = self.kb.get_steps("el-select", self.framework)
        select_sub_type = "select-single"  # 默认值，后续检测后覆盖
        # ★ 定义默认的 selection_strategy，所有返回路径都会包含
        selection_strategy = {
            "method": "first_available",
            "skip_rules": [{"condition": "is-disabled", "reason": "skip_disabled_options"}]
        }
        if not steps:
            return False, {}

        # Step 1: expand（必须使用 selector，不做 KB XPath 回退）
        if not selector:
            LOG.warning(f"    el-select {label} 缺少 selector（Stage 1 未提供）")
            return False, {}

        # Step 0: 检查下拉框是否已有默认选中值（避免重复点击导致取消选中）
        if not option_text:
            # 仅自动发现模式（option_text 为空）时检查，用户明确指定值时跳过
            has_default = await self.page.evaluate("""(selector) => {
                const sel = document.querySelector(selector);
                if (!sel) return {hasValue: false};

                // 多选模式（tag）
                const tags = sel.querySelectorAll('.el-tag');
                if (tags && tags.length > 0) {
                    return {
                        hasValue: true,
                        mode: 'multiple',
                        values: Array.from(tags).map(t =>
                            (t.querySelector('.el-select__tags-text') || t).textContent.trim()
                        )
                    };
                }

                // 单选模式
                const input = sel.querySelector('.el-input__inner');
                const selectedLabel = sel.querySelector('.el-select__selected-item');
                const value = selectedLabel?.textContent?.trim() || input?.value || '';
                if (value) {
                    return {hasValue: true, mode: 'single', value: value};
                }

                return {hasValue: false};
            }""", selector)

            if has_default.get("hasValue"):
                mode = has_default.get("mode", "single")
                if mode == "multiple":
                    vals = has_default.get("values", [])
                    LOG.info(f"    el-select {label}: 多选已有默认值 {vals}，跳过")
                    return True, {"is_editable": False, "option_text": ", ".join(vals), "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}
                else:
                    val = has_default.get("value", "")
                    LOG.info(f"    el-select {label}: 单选已有默认值 '{val}'，跳过")
                    return True, {"is_editable": False, "option_text": val, "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}

        expanded = await self._expand_by_selector(selector, label, playwright_locator)
        if not expanded:
            LOG.debug(f"    el-select {label}: expand failed")
            return False, {"selection_strategy": selection_strategy}
        await self.page.wait_for_timeout(500)

        # Step 2: editable-check
        editable_patterns = steps.get("editable-check", {}).get("patterns", [])
        is_editable = await self._check_element_visible(editable_patterns, label=label)
        LOG.debug(f"    el-select {label}: is_editable={is_editable}")

        # Step 2.5: Phase 2c - el-select 子类型检测
        # 在下拉框展开后检测子类型（single/multi/searchable/remote/group/tree）
        sub_type_info = None
        try:
            sub_type_info = await self.page.evaluate("""(selector) => {
                const sel = document.querySelector(selector);
                if (!sel) return null;
                const isMulti = sel.classList.contains('el-select--multiple') || sel.querySelector('.el-select__tags');
                const input = sel.querySelector('input.el-input__inner');
                const isFilterable = input && !input.readOnly;
                const vue = sel.__vue__;
                const isRemote = vue && vue.remote === true;
                const dds = document.querySelectorAll('.el-select-dropdown');
                let hasTree = false, hasGroup = false;
                for (const dd of dds) {
                    if (dd.offsetWidth > 0 && dd.offsetHeight > 0) {
                        if (dd.querySelector('.el-tree')) hasTree = true;
                        if (dd.querySelector('.el-select-group__wrap')) hasGroup = true;
                    }
                }
                return { isMulti: !!isMulti, isFilterable: !!isFilterable, isRemote: !!isRemote, hasTree, hasGroup };
            }""", selector)
        except Exception as e:
            LOG.debug(f"    el-select {label}: sub-type detection failed: {e}")

        if sub_type_info:
            if sub_type_info.get("hasTree"):
                select_sub_type = "select-tree"
            elif sub_type_info.get("isMulti"):
                select_sub_type = "select-multi"
            elif sub_type_info.get("isRemote"):
                select_sub_type = "select-remote"
            elif sub_type_info.get("isFilterable"):
                select_sub_type = "select-searchable"
            elif sub_type_info.get("hasGroup"):
                select_sub_type = "select-group"

            LOG.info(f"    el-select {label}: sub_type={select_sub_type}")

            # ★ FIX: tree-select 路由到专用处理器
            # 通用选择路径使用 .el-select-dropdown__item，但 tree-select 的选项是
            # .custom-tree-node / .el-tree-node__content，选择器不匹配
            if select_sub_type == "select-tree":
                LOG.info(f"    [FIX] el-select {label}: 检测到 tree-select，路由到 _fill_tree_select 专用处理器")
                return await self._fill_tree_select(selector, label, playwright_locator)

        # Step 3: 等待选项加载
        await self._wait_for_options_loaded()

        # Step 4: 自动发现选项
        if not option_text:
            option_text = await self._discover_first_option()
            LOG.debug(f"    el-select {label}: first discovery option_text={repr(option_text)}")
            # 重试一次：Element UI 下拉面板可能需要额外渲染时间
            if not option_text:
                await self.page.wait_for_timeout(500)
                option_text = await self._discover_first_option(timeout=2000)
                LOG.debug(f"    el-select {label}: retry discovery option_text={repr(option_text)}")

        detail = {
            "is_editable": is_editable,
            "option_text": option_text or "",
            "select_sub_type": select_sub_type if sub_type_info else "select-single",
            "selection_strategy": selection_strategy,
        }

        if not option_text:
            # 记录详细的下拉框状态以便调试
            dd_state = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                const result = [];
                for (const dd of dds) {
                    const items = dd.querySelectorAll('.el-select-dropdown__item');
                    const visibleItems = Array.from(items).filter(it =>
                        it.offsetWidth > 0 && it.offsetHeight > 0
                    );
                    result.push({
                        visible: dd.offsetWidth > 0 && dd.offsetHeight > 0,
                        display: window.getComputedStyle(dd).display,
                        totalItems: items.length,
                        visibleItems: visibleItems.length,
                        firstItemText: items.length > 0 ? items[0].textContent.trim() : null,
                        firstVisibleText: visibleItems.length > 0 ? visibleItems[0].textContent.trim() : null
                    });
                }
                return result;
            }""")
            # 判断是否真的无选项（而非加载慢）
            has_visible_dropdown = any(d.get('visible') for d in dd_state)
            has_any_visible_items = any(
                d.get('visible') and d.get('totalItems', 0) > 0 for d in dd_state
            )
            if has_visible_dropdown and not has_any_visible_items:
                LOG.info(f"    el-select {label}: 下拉框已展开但无选项，跳过（非必填或暂无可选项）")
                detail["skipped_reason"] = "no_options"
                detail["selection_strategy"] = selection_strategy
                return False, detail
            LOG.warning(f"    el-select {label} 未发现可选项，下拉框状态: {json.dumps(dd_state, ensure_ascii=False)}")
            detail["selection_strategy"] = selection_strategy
            return False, detail

        # Step 5: 根据是否可编辑选择不同策略
        LOG.info(f"    el-select {label}: proceeding with is_editable={is_editable}, option_text='{option_text}'")

        # Tree-select 特殊处理：跳过搜索过滤，直接选择第一个可见选项
        # 原因：tree-select 的搜索过滤逻辑与标准 el-select 不兼容，会导致过滤失败
        if is_editable:
            is_tree_select = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                for (const dd of dds) {
                    if (dd.offsetWidth > 0 && dd.offsetHeight > 0) {
                        return !!dd.querySelector('.el-tree');
                    }
                }
                return false;
            }""")

            if is_tree_select:
                LOG.info(f"    el-select {label}: 检测到 tree-select，跳过搜索过滤，直接选择第一个选项")
                is_editable = False  # 强制走 first-option 逻辑
                option_text = ""     # 清空 option_text，让它自动选择第一个

        if is_editable:
            # 可编辑：先输入搜索，再选择
            filled = await self._try_step_patterns(steps.get("fill", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: fill result={filled}")
            if not filled:
                return False, detail

            # 关键修复：fill 只 click 了输入框，需要实际输入搜索文本
            typed = await self._type_search_text(label, option_text, selector)
            LOG.info(f"    el-select {label}: type search result={typed}")
            await self.page.wait_for_timeout(500)  # 等待搜索过滤完成

            # 检查 fill 后下拉是否仍然展开
            post_fill = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                const visible = Array.from(dds).filter(dd => dd.offsetWidth > 0 && dd.offsetHeight > 0);
                return visible.length;
            }""")
            LOG.info(f"    el-select {label}: visible dropdowns after fill={post_fill}")

            # 如果 fill 导致下拉关闭，重新展开
            if post_fill == 0:
                LOG.info(f"    el-select {label}: fill closed dropdown, re-expanding")
                re_expanded = await self._expand_by_selector(selector, label, playwright_locator)
                if re_expanded:
                    await self.page.wait_for_timeout(500)
                    # 等待选项加载
                    await self._wait_for_options_loaded()

            selected = await self._try_step_patterns(
                steps.get("select", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: select result={selected}")
            # KB XPath 失败时回退：直接点击可见选项
            if not selected:
                selected = await self._click_visible_option(option_text)
                LOG.info(f"    el-select {label}: direct click result={selected}")
            # Post-fill verification
            await self.page.wait_for_timeout(500)
            verify = await self._verify_select_filled(label, selector)
            if not verify.get("filled"):
                debug_info = verify.get("debug", {})
                LOG.warning(f"    ⚠️ select 填充后验证失败: {label} 仍为空 "
                           f"debug={debug_info}")
                return False, {"is_editable": is_editable, "option_text": option_text, "verify_failed": True, "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}
            LOG.debug(f"    ✓ select 填充验证通过: {label} = {verify['displayed_value'][:30]}")
            return selected, {"is_editable": is_editable, "option_text": verify.get("displayed_value", option_text), "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}
        else:
            # 不可编辑：等待选项加载，再选择第一个选项
            await self._wait_for_options_loaded()
            selected = await self._try_step_patterns(
                steps.get("first-option", {}), label=label, option_text=option_text)
            LOG.info(f"    el-select {label}: first-option result={selected}")
            # KB XPath 失败时回退：直接点击可见选项
            if not selected:
                selected = await self._click_visible_option(option_text)
                LOG.info(f"    el-select {label}: direct click result={selected}")
            # Post-fill verification
            await self.page.wait_for_timeout(500)
            verify = await self._verify_select_filled(label, selector)
            if not verify.get("filled"):
                debug_info = verify.get("debug", {})
                LOG.warning(f"    ⚠️ select 填充后验证失败: {label} 仍为空 "
                           f"debug={debug_info}")
                return False, {"is_editable": is_editable, "option_text": option_text, "verify_failed": True, "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}
            LOG.debug(f"    ✓ select 填充验证通过: {label} = {verify['displayed_value'][:30]}")
            return selected, {"is_editable": is_editable, "option_text": verify.get("displayed_value", option_text), "select_sub_type": select_sub_type, "selection_strategy": selection_strategy}

    async def _fill_remote_select(self, selector: str, label: str, playwright_locator: str = None) -> tuple:
        """远程搜索 el-select：展开后等待远程加载完成，选第一个

        策略：展开 → 等待 option 出现 → 选第一个
        """
        LOG.info(f"    [select-remote] {label}: 展开下拉框")

        # 1. 展开下拉框
        expanded = await self._expand_by_selector(selector, label, playwright_locator)
        if not expanded:
            return False, {"skipped_reason": "expand_failed"}

        # 2. 等待远程 API 返回（关键差异：通用逻辑不等待）
        LOG.info(f"    [select-remote] {label}: 等待远程选项加载...")
        try:
            await self.page.wait_for_selector(
                '.el-select-dropdown:not([style*="display: none"]) .el-select-dropdown__item:not(.is-disabled)',
                state='visible',
                timeout=5000
            )
            LOG.info(f"    [select-remote] {label}: 远程选项已加载")
        except Exception as e:
            LOG.warning(f"    [select-remote] {label}: 远程选项未加载 (timeout): {e}")
            return False, {"skipped_reason": "remote_load_timeout"}

        # 3. 选第一个可见选项（与通用逻辑相同）
        await self.page.wait_for_timeout(300)
        option_text = await self._discover_first_option()
        if not option_text:
            LOG.warning(f"    [select-remote] {label}: 未发现可选项")
            return False, {"skipped_reason": "no_options"}

        selected = await self._click_visible_option(option_text)
        LOG.info(f"    [select-remote] {label}: 选择结果={selected}, option='{option_text}'")

        # 4. 验证
        await self.page.wait_for_timeout(500)
        verify = await self._verify_select_filled(label, selector)
        if not verify.get("filled"):
            LOG.warning(f"    [select-remote] {label}: 填充后验证失败")
            return False, {"option_text": option_text, "verify_failed": True}

        return selected, {"option_text": verify.get("displayed_value", option_text)}

    async def _fill_tree_select(self, selector: str, label: str, playwright_locator: str = None) -> tuple:
        """树形 el-select：诊断并选择树节点

        步骤：先详细诊断 Vue 组件结构和事件链，再选择正确的操作方式。

        Args:
            selector: CSS 选择器（用于 page.evaluate）
            label: 字段标签
            playwright_locator: Playwright 定位器（用于 page.click/locator，可选）

        Returns:
            tuple: (success: bool, detail: dict)
                detail 包含 "option_text"（选中的节点文本）
        """
        LOG.info(f"    [select-tree] {label}: 开始诊断")

        # 1. 详细诊断：el-select Vue 实例 + 页面所有 el-tree 的事件绑定
        diag = await self.page.evaluate("""(selector) => {
            const result = {};

            // 1a. el-select Vue 实例分析
            const selectEl = document.querySelector(selector);
            if (selectEl && selectEl.__vue__) {
                const v = selectEl.__vue__;
                result.select = {
                    cls: selectEl.className,
                    // v-model 值
                    value: v.value,
                    selectedLabel: v.selectedLabel,
                    // 可用方法
                    methods: Object.keys(v).filter(k => typeof v[k] === 'function').filter(k => !k.startsWith('_') && !k.startsWith('$')).slice(0, 20),
                    // 事件监听
                    events: Object.keys(v.$listeners || {}),
                    // 内部状态
                    visible: v.visible,
                    // 关联的 popper
                    popperEl: v.popperEl ? v.popperEl.className : null,
                    // 检查是否有 tree 相关的 prop 或 ref
                    treeRef: v.$refs.tree ? 'exists' : null,
                    treeRefMethods: v.$refs.tree ? Object.keys(v.$refs.tree).filter(k => typeof v.$refs.tree[k] === 'function').slice(0, 10) : null,
                };
            } else {
                result.select = { found: false };
            }

            // 1b. 页面所有 el-tree 分析
            const trees = Array.from(document.querySelectorAll('.el-tree'));
            result.trees = trees.map((t, idx) => {
                const visible = t.offsetWidth > 0 && t.offsetHeight > 0;
                const inDisabled = !!t.closest('.is-disabled');
                const inDropdown = !!t.closest('.el-select-dropdown');
                const vue = t.__vue__;

                // 查找父组件链（检查是否有 el-select 祖先）
                let parentChain = [];
                let p = t.parentElement;
                for (let i = 0; i < 8 && p; i++) {
                    let desc = p.tagName.toLowerCase();
                    if (p.className && typeof p.className === 'string') {
                        desc += '.' + p.className.substring(0, 50);
                    }
                    if (p.__vue__) desc += '[vue]';
                    parentChain.push(desc);
                    p = p.parentElement;
                }

                return {
                    idx,
                    visible,
                    inDisabled,
                    inDropdown,
                    nodeCount: t.querySelectorAll('.custom-tree-node').length,
                    parentClasses: t.parentElement ? t.parentElement.className.substring(0, 80) : '',
                    // Vue 事件
                    vueListeners: vue ? Object.keys(vue.$listeners || {}) : [],
                    vueEvents: vue && vue._events ? Object.keys(vue._events) : [],
                    hasNodeClick: vue ? typeof vue.handleNodeClick === 'function' : false,
                    hasSetCurrentKey: vue ? typeof vue.setCurrentKey === 'function' : false,
                    parentChain: parentChain.slice(0, 5),
                };
            });

            return result;
        }""", selector)

        LOG.info(f"    [select-tree] {label}: el-select 诊断: {diag.get('select')}")
        for ti in diag.get('trees', []):
            LOG.info(f"    [select-tree] {label}: tree[{ti['idx']}]: visible={ti['visible']}, disabled={ti['inDisabled']}, "
                     f"inDropdown={ti['inDropdown']}, nodes={ti['nodeCount']}, parent={ti['parentClasses'][:60]}, "
                     f"listeners={ti.get('vueListeners')}, events={ti.get('vueEvents', [])[:10]}")

        # 2. 通过 el-select 的 handleNodeClick 事件回调设置值
        # handleNodeClick 在 $listeners 中（父组件传入的事件回调）
        has_handleNodeClick = 'handleNodeClick' in diag.get('select', {}).get('events', [])
        if has_handleNodeClick:
            LOG.info(f"    [select-tree] {label}: 通过 handleNodeClick 事件回调设置值")
            set_result = await self.page.evaluate("""(selector) => {
                const selectEl = document.querySelector(selector);
                if (!selectEl || !selectEl.__vue__) return { success: false, reason: 'no_select_vue' };
                const sv = selectEl.__vue__;

                // ★ 修复：强制优先选择 dropdown 内的树（el-select-dropdown 弹出层）
                // 页面上可能有多个 el-tree（如组织架构面板），必须选择正确的下拉树
                const allTrees = Array.from(document.querySelectorAll('.el-tree'));

                // 优先级：1) inDropdown + visible（不管 disabled）  2) visible only
                // 注意：inDropdown 的树可能在 disabled 容器内，但节点本身可交互
                const visibleTree = allTrees.find(t => {
                    if (t.offsetWidth <= 0 || t.offsetHeight <= 0) return false;
                    // 优先选择 dropdown 内的树（即使父容器 disabled）
                    if (t.closest('.el-select-dropdown')) return true;
                    return false;
                }) || allTrees.find(t => {
                    // Fallback: 任何可见的树（不在 disabled 容器内）
                    if (t.offsetWidth <= 0 || t.offsetHeight <= 0) return false;
                    if (t.closest('.is-disabled')) return false;
                    return true;
                });
                if (!visibleTree) return { success: false, reason: 'no_visible_tree' };

                // ★ 获取当前已选中的显示值（用于跳过"选到相同值"的情况）
                const currentDisplayValue = (() => {
                    const inputEl = selectEl.querySelector('input.el-input__inner');
                    if (inputEl && inputEl.value) return inputEl.value.trim();
                    const tagEl = selectEl.querySelector('.el-select__tags .el-tag');
                    if (tagEl) return tagEl.textContent.trim().replace('×', '').trim();
                    return '';
                })();

                const spans = visibleTree.querySelectorAll('.custom-tree-node span');
                let nodeData = null;
                let text = '';

                for (const span of spans) {
                    const content = span.closest('.el-tree-node__content');
                    if (!content) continue;
                    const cb = content.querySelector('.el-checkbox');
                    if (cb && cb.classList.contains('is-checked')) continue;
                    // ★ 跳过 is-current 节点（树组件自身标记的当前节点）
                    if (content.classList.contains('is-current')) continue;
                    // ★ 跳过与当前已选中值相同的节点
                    const spanText = span.textContent.trim();
                    if (currentDisplayValue && spanText === currentDisplayValue) continue;
                    const treeNodeEl = content.closest('.el-tree-node');
                    if (treeNodeEl && treeNodeEl.__vue__) {
                        nodeData = treeNodeEl.__vue__.node.data;
                        text = spanText;
                        break;
                    }
                }

                if (!nodeData && spans.length > 0) {
                    // fallback: 尝试第一个有 vue 数据的节点
                    for (const span of spans) {
                        const content = span.closest('.el-tree-node__content');
                        const treeNodeEl = content ? content.closest('.el-tree-node') : null;
                        if (treeNodeEl && treeNodeEl.__vue__) {
                            nodeData = treeNodeEl.__vue__.node.data;
                            text = span.textContent.trim();
                            break;
                        }
                    }
                }

                if (!nodeData) return { success: false, reason: 'no_node_data' };

                // 调用 $listeners 中的 handleNodeClick 回调
                const listener = sv.$listeners && sv.$listeners.handleNodeClick;
                if (listener) {
                    // $listeners 中的回调可能是函数或函数数组
                    if (typeof listener === 'function') {
                        listener(nodeData);
                    } else if (Array.isArray(listener)) {
                        listener.forEach(fn => { if (typeof fn === 'function') fn(nodeData); });
                    }
                    return { success: true, text, dataKeys: Object.keys(nodeData).slice(0, 8) };
                }

                // 也尝试用 $emit
                sv.$emit('handleNodeClick', nodeData);
                return { success: true, text, via: 'emit', dataKeys: Object.keys(nodeData).slice(0, 8) };
            }""", selector)
            LOG.info(f"    [select-tree] {label}: handleNodeClick 结果: {set_result}")

            if set_result.get('success'):
                await self.page.wait_for_timeout(800)
                verify = await self._verify_select_filled(label, selector)
                if verify.get("filled"):
                    return True, {
                        "option_text": verify.get("displayed_value", set_result['text']),
                        "select_sub_type": "select-tree",
                        "selection_strategy": {
                            "method": "handleNodeClick",
                            "skip_rules": [
                                {"condition": "is-checked", "reason": "avoid_already_selected"},
                                {"condition": "is-current", "reason": "avoid_current_selection"},
                                {"condition": "text_match_current", "reason": "avoid_same_value"}
                            ]
                        }
                    }
                else:
                    LOG.warning(f"    [select-tree] {label}: handleNodeClick 后验证失败, displayed='{verify.get('displayed_value', '')}'")

        # 3. Fallback: 直接 Playwright click
        LOG.info(f"    [select-tree] {label}: 尝试 Playwright click")
        node_info = await self.page.evaluate("""(selector) => {
            const allTrees = Array.from(document.querySelectorAll('.el-tree'));

            // ★ 修复：强制优先选择 dropdown 内的树（与 handleNodeClick 路径一致）
            const visibleTree = allTrees.find(t => {
                if (t.offsetWidth <= 0 || t.offsetHeight <= 0) return false;
                // 优先选择 dropdown 内的树（即使父容器 disabled）
                if (t.closest('.el-select-dropdown')) return true;
                return false;
            }) || allTrees.find(t => {
                // Fallback: 任何可见的树（不在 disabled 容器内）
                if (t.offsetWidth <= 0 || t.offsetHeight <= 0) return false;
                if (t.closest('.is-disabled')) return false;
                return true;
            });
            if (!visibleTree) return { found: false, reason: 'no_visible_tree' };

            // ★ 获取当前已选中的显示值
            const selectEl = document.querySelector(selector);
            const currentDisplayValue = (() => {
                if (!selectEl) return '';
                const inputEl = selectEl.querySelector('input.el-input__inner');
                if (inputEl && inputEl.value) return inputEl.value.trim();
                const tagEl = selectEl.querySelector('.el-select__tags .el-tag');
                if (tagEl) return tagEl.textContent.trim().replace('×', '').trim();
                return '';
            })();

            const spans = visibleTree.querySelectorAll('.custom-tree-node span');
            for (const span of spans) {
                const content = span.closest('.el-tree-node__content');
                if (!content) continue;
                const cb = content.querySelector('.el-checkbox');
                if (cb && cb.classList.contains('is-checked')) continue;
                // ★ 跳过 is-current 节点
                if (content.classList.contains('is-current')) continue;
                // ★ 跳过与当前已选中值相同的节点
                const spanText = span.textContent.trim();
                if (currentDisplayValue && spanText === currentDisplayValue) continue;
                return { found: true, text: spanText, selector: '.custom-tree-node span' };
            }
            return { found: false, reason: 'all_selected' };
        }""", selector)

        if node_info.get('found'):
            locator = self.page.locator(f"{node_info['selector']}:visible").first
            if await locator.count() > 0:
                await locator.click(force=True, timeout=5000)
                LOG.info(f"    [select-tree] {label}: click '{node_info['text']}'")
                await self.page.wait_for_timeout(800)

        verify = await self._verify_select_filled(label, selector)
        if not verify.get("filled"):
            return False, {
                "skipped_reason": "verify_failed",
                "option_text": node_info.get('text', ''),
                "select_sub_type": "select-tree",
                "selection_strategy": {
                    "method": "playwright_click",
                    "skip_rules": [
                        {"condition": "is-checked", "reason": "avoid_already_selected"},
                        {"condition": "is-current", "reason": "avoid_current_selection"},
                        {"condition": "text_match_current", "reason": "avoid_same_value"}
                    ]
                }
            }

        return True, {
            "option_text": verify.get("displayed_value", node_info.get('text', '')),
            "select_sub_type": "select-tree",
            "selection_strategy": {
                "method": "playwright_click",
                "skip_rules": [
                    {"condition": "is-checked", "reason": "avoid_already_selected"},
                    {"condition": "is-current", "reason": "avoid_current_selection"},
                    {"condition": "text_match_current", "reason": "avoid_same_value"}
                ]
            }
        }

    async def execute_select_by_sub_type(self, selector: str, sub_type: str, label: str, playwright_locator: str = None) -> tuple:
        """根据子类型分发到不同处理方法

        Args:
            selector: CSS 选择器
            sub_type: 子类型（select-remote/select-tree/select-single 等）
            label: 字段标签
            playwright_locator: Playwright 定位器（可选）

        Returns:
            (success, detail)
        """
        if sub_type == "select-remote":
            return await self._fill_remote_select(selector, label, playwright_locator)
        elif sub_type == "select-tree":
            return await self._fill_tree_select(selector, label, playwright_locator)
        else:
            # select-single/multi/searchable/group：走现有通用逻辑
            return await self._execute_select_with_details(selector, label)

    async def _expand_by_selector(self, selector: str, label: str, playwright_locator: str = None) -> bool:
        """用 CSS selector 点击展开 el-select 下拉框。

        selector 来自 Stage 1 playbook，可能是 .el-select 或 input 的选择器。
        playwright_locator: Playwright 语法定位器（优先使用）
        """
        try:
            # 优先使用 playwright_locator（更稳定）
            effective_locator = playwright_locator if playwright_locator else selector
            # 取第一个 locator（可能有逗号分隔的备选）
            primary = effective_locator.split(",")[0].strip()

            # 尝试点击 .el-select 的 input 区域来展开
            el = self.page.locator(primary).first
            el_count = await el.count()
            if el_count == 0:
                LOG.info(f"    el-select {label}: locator 未匹配 ({primary})")
                return False

            await el.click(timeout=3000)
            LOG.info(f"    el-select {label}: locator 展开成功 ({primary})")
            return True
        except Exception as e:
            LOG.info(f"    el-select {label}: locator 展开失败: {e}")
            return False

    async def _execute_cascader_with_details(self, label: str, _option_text, options: list, selector: str = "", playwright_locator: str = None) -> tuple:
        """el-cascader 多步交互，返回详细执行信息。

        Returns:
            tuple: (success: bool, detail: dict)
        """
        steps = self.kb.get_steps("el-cascader", self.framework)
        if not steps:
            return False, {"skipped_reason": "no_steps", "kb_category": "el-cascader"}

        # Step 1: expand
        expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
        if not expanded:
            return False, {"skipped_reason": "expand_failed", "kb_category": "el-cascader"}
        await self.page.wait_for_timeout(500)

        # Step 2: 根据是否提供 options 选择不同模式
        if options:
            success = await self._cascader_select_by_path(steps, label, options)
            if success:
                return True, {
                    "option_text": " > ".join(options),
                    "selected_path": options,
                    "kb_category": "el-cascader",
                }
            return False, {"skipped_reason": "path_select_failed", "kb_category": "el-cascader"}
        else:
            success = await self._cascader_discover_and_select(steps, label)
            if success:
                # 读取实际选择的路径
                selected_path = await self._read_cascader_selected_path(selector)
                return True, {
                    "option_text": " > ".join(selected_path) if selected_path else "",
                    "selected_path": selected_path,
                    "kb_category": "el-cascader",
                }
            return False, {"skipped_reason": "discover_select_failed", "kb_category": "el-cascader"}

    async def _read_cascader_selected_path(self, selector: str = "") -> list:
        """读取 cascader 当前选中的路径文本。"""
        try:
            if selector:
                result = await self.page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return [];
                    const input = el.querySelector('input');
                    if (input && input.value) return input.value.split(' / ');
                    const tags = el.querySelectorAll('.el-tag');
                    if (tags.length > 0) return [tags[0].textContent.trim().replace('×', '').trim()];
                    return [];
                }""", selector)
                return result if isinstance(result, list) else []
            return []
        except Exception:
            return []

    async def _execute_date_picker_with_details(self, label: str, option_text: str, _options, selector: str = "", playwright_locator: str = None) -> tuple:
        """date-picker 多步交互，返回详细执行信息。

        Returns:
            tuple: (success: bool, detail: dict)
        """
        steps = self.kb.get_steps("date-picker", self.framework)
        if not steps:
            return False, {"skipped_reason": "no_steps", "kb_category": "date-picker"}

        # Step 1: expand
        expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
        if not expanded:
            return False, {"skipped_reason": "expand_failed", "kb_category": "date-picker"}
        await self.page.wait_for_timeout(500)

        # Step 2: 选择今天（默认策略）
        strategy = "today"
        selected = await self._try_step_patterns(steps.get("select-today", {}), label=label)
        if not selected:
            # 回退：选择此刻
            strategy = "now"
            selected = await self._try_step_patterns(steps.get("select-now", {}), label=label)

        if selected:
            # 读取实际选中的值
            actual_value = ""
            if selector:
                try:
                    actual_value = await self.page.evaluate("""(sel) => {
                        const input = document.querySelector(sel + ' input');
                        return input ? input.value : '';
                    }""", selector)
                except Exception:
                    pass
            return True, {
                "option_text": actual_value or strategy,
                "selection_strategy": {"method": f"click_{strategy}"},
                "kb_category": "date-picker",
            }
        return False, {"skipped_reason": "select_failed", "kb_category": "date-picker"}

    async def _execute_form_checkbox_with_details(self, label: str, option_text: str, _options, selector: str = "", playwright_locator: str = None) -> tuple:
        """form-checkbox：勾选指定选项，返回详细执行信息。

        Returns:
            tuple: (success: bool, detail: dict)
        """
        patterns = self.kb.get_patterns("form-checkbox", self.framework)
        for p in patterns:
            xpath = self.kb.expand_step(
                p, field_text=label, option_text=option_text,
                framework=self.framework, apply_hidden=True
            )
            if await self._click_by_xpath(xpath):
                return True, {
                    "option_text": option_text,
                    "checked_options": [option_text],
                    "kb_category": "form-checkbox",
                }

        # 兜底：尝试 fallback_strategies
        success = await self._try_fallback(label)
        if success:
            return True, {
                "option_text": option_text,
                "checked_options": [option_text],
                "kb_category": "form-checkbox",
            }
        return False, {"skipped_reason": "no_checkbox_clicked", "kb_category": "form-checkbox"}

    async def _execute_list_selector(self, label: str, option_text: str, _options, selector: str = "") -> bool:
        """list-selector：在列表选择器中选择一个选项（穿梭框/transfer-like 组件）

        交互流程：
        1. 找到搜索框（可选）
        2. 找到第一个可点击的列表项
        3. 点击该项（可能是 checkbox 或 li）
        4. 验证"已选择"面板中是否出现该项

        Args:
            label: 字段标签
            option_text: 要选择的选项文本（为空则选择第一个）
            selector: 组件容器 selector
            _options: 未使用

        Returns:
            bool: 是否成功选择
        """
        success, _ = await self._execute_list_selector_with_details(label, option_text, _options, selector)
        return success

    async def _execute_list_selector_with_details(self, label: str, option_text: str, _options, selector: str = "", playwright_locator: str = None) -> Tuple[bool, Dict]:
        """list-selector 多步交互，并返回详细的执行信息。

        Returns:
            Tuple[bool, Dict]: (是否成功, {"option_text": str, "selected_count": int})
        """
        # 1. 定位组件容器
        container = None
        if selector:
            try:
                container = await self.page.query_selector(selector)
            except Exception:
                pass

        # === DIAGNOSTIC: dump 页面中的列表/选择相关 DOM 结构 ===
        diag = await self.page.evaluate("""() => {
            const result = { checkboxes: [], listItems: [], allVisibleBtns: [], transferPanels: [], customLists: [] };

            // 1. 所有 checkbox
            document.querySelectorAll('.el-checkbox').forEach((cb, i) => {
                const r = cb.getBoundingClientRect();
                const parent = cb.closest('.el-form-item, .el-dialog, .el-drawer, section');
                const parentLabel = parent ? (parent.querySelector('.el-form-item__label, .el-dialog__title') || {}).textContent : '';
                result.checkboxes.push({
                    idx: i, visible: r.width > 0 && r.height > 0,
                    w: Math.round(r.width), h: Math.round(r.height),
                    checked: cb.classList.contains('is-checked'),
                    text: (cb.textContent || '').trim().substring(0, 50),
                    parentTag: parent ? parent.tagName : 'none',
                    parentLabel: (parentLabel || '').trim().substring(0, 30),
                    parentClass: parent ? (parent.className || '').substring(0, 60) : ''
                });
            });

            // 2. 所有 li
            document.querySelectorAll('li').forEach((li, i) => {
                const r = li.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                const parent = li.closest('.el-form-item, .el-dialog, .el-drawer, section, .el-table');
                result.listItems.push({
                    idx: i, w: Math.round(r.width), h: Math.round(r.height),
                    text: (li.textContent || '').trim().substring(0, 50),
                    cls: (li.className || '').substring(0, 60),
                    parentTag: parent ? parent.tagName : 'none',
                    parentClass: parent ? (parent.className || '').substring(0, 60) : ''
                });
            });

            // 3. transfer-panel / picker-panel
            document.querySelectorAll('[class*="transfer"], [class*="picker-panel"], [class*="select-panel"], [class*="list-panel"]').forEach((el, i) => {
                const r = el.getBoundingClientRect();
                result.transferPanels.push({
                    idx: i, visible: r.width > 0 && r.height > 0,
                    w: Math.round(r.width), h: Math.round(r.height),
                    cls: (el.className || '').substring(0, 80),
                    childCount: el.children.length,
                    innerHTML: el.innerHTML.substring(0, 200)
                });
            });

            // 4. 所有可见按钮（含文本）
            document.querySelectorAll('button').forEach((btn, i) => {
                const r = btn.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return;
                result.allVisibleBtns.push({
                    text: (btn.textContent || '').trim().substring(0, 30),
                    cls: (btn.className || '').substring(0, 60),
                    visible: true
                });
            });

            return result;
        }""")
        import json
        LOG.info(f"    [DIAG-list-selector] selector='{selector}'")
        _cb_summary = [{"v": c.get("visible"), "c": c.get("checked"), "t": c.get("text","")[:20], "p": c.get("parentLabel","")[:15]} for c in diag.get("checkboxes", [])[:10]]
        LOG.info(f"    [DIAG] checkboxes({len(diag.get('checkboxes', []))}): {json.dumps(_cb_summary, ensure_ascii=False)}")
        _li_summary = [{"t": li.get("text","")[:20], "c": li.get("cls","")[:30]} for li in diag.get("listItems", [])[:10]]
        LOG.info(f"    [DIAG] listItems({len(diag.get('listItems', []))}): {json.dumps(_li_summary, ensure_ascii=False)}")
        _tp_summary = [{"c": p.get("cls","")[:40], "v": p.get("visible"), "ch": p.get("childCount")} for p in diag.get("transferPanels", [])]
        LOG.info(f"    [DIAG] transferPanels({len(diag.get('transferPanels', []))}): {json.dumps(_tp_summary, ensure_ascii=False)}")
        _btn_summary = [b.get("text","")[:15] for b in diag.get("allVisibleBtns", [])]
        LOG.info(f"    [DIAG] visibleBtns({len(diag.get('allVisibleBtns', []))}): {json.dumps(_btn_summary, ensure_ascii=False)}")
        # === END DIAGNOSTIC ===

        # 2. 在容器内或页面内搜索列表项
        # 优先查找 checkbox 列表项（el-checkbox-group 内的 el-checkbox）
        # 其次查找普通 li 列表项
        search_scope = container if container else self.page

        # 尝试找到第一个未勾选的 checkbox
        checkbox = None
        if container:
            checkbox = await container.query_selector(
                '.el-checkbox-group .el-checkbox:not(.is-checked), '
                '.el-checkbox:not(.is-checked)'
            )
        if not checkbox:
            # 尝试页面级别
            checkbox = await self.page.query_selector(
                '.el-checkbox-group .el-checkbox:not(.is-checked)'
            )

        if checkbox:
            # 点击 checkbox 的 label 区域（更稳定）
            label_el = await checkbox.query_selector('.el-checkbox__label')
            if label_el:
                await label_el.click()
            else:
                await checkbox.click()
            await self.page.wait_for_timeout(500)

            # 获取选中的选项文本
            selected_text = await self.page.evaluate("""() => {
                const checked = document.querySelectorAll('.el-checkbox.is-checked .el-checkbox__label');
                return Array.from(checked).map(el => el.textContent.trim()).join(', ');
            }""")

            return True, {"option_text": selected_text, "selected_count": 1}

        # 回退 1：尝试普通 li 列表项（增加可见性校验，避免匹配不可见的菜单项）
        list_item = None
        if container:
            list_item = await container.query_selector('li:not(.is-selected):not(.is-disabled)')
        if not list_item:
            list_item = await self.page.query_selector(
                'li[class*="item"]:not(.is-selected):not(.is-disabled)'
            )

        if list_item:
            # 可见性校验：确保元素真正可见且在视口内
            is_visible = await list_item.evaluate('el => el.offsetWidth > 0 && el.offsetHeight > 0 && el.getBoundingClientRect().top >= 0 && el.getBoundingClientRect().top < window.innerHeight')
            if is_visible:
                await list_item.click()
                await self.page.wait_for_timeout(500)
                item_text = await list_item.inner_text()
                return True, {"option_text": item_text.strip(), "selected_count": 1}
            else:
                LOG.info(f"    li 元素不可见（可能在视口外），跳过")
                list_item = None

        # 注意：transfer-box 已独立为 _execute_transfer_box 处理器，不在此处处理

        LOG.warning(f"    list-selector {label}: 未找到可点击的列表项（checkbox/li 均无匹配）")
        return False, {"skipped_reason": "no_clickable_items"}

    async def _execute_ip_input_with_details(self, label: str, kwargs: dict) -> Tuple[bool, Dict]:
        """IP 多段输入组件处理器。

        将 IP 地址分段填充到多个 input 中，支持 IPv4/IPv6。

        Args:
            label: 字段标签
            kwargs: 额外参数
                - sub_selectors: 每个 input 的 CSS selector 列表
                - input_count: input 数量（4=IPv4 单地址，8=IPv4 区间或 IPv6 单地址，16=IPv6 区间）
                - ip_version: IPv 版本 ('v4' 或 'v6')
                - fill_rule: 填充规则（用于生成 IP 值）

        Returns:
            Tuple[bool, Dict]: (是否成功, {"option_text": str, "input_count": int})
        """
        sub_selectors = kwargs.get("sub_selectors", [])
        input_count = kwargs.get("input_count", 4)
        ip_version = kwargs.get("ip_version", "v4")
        fill_rule = kwargs.get("fill_rule")

        # 1. 根据 fill_rule 生成 IP 值
        if fill_rule:
            ip_value = apply_fill_rule(fill_rule)
        else:
            # 使用默认 IP 值
            if ip_version == "v4":
                if input_count == 4:
                    ip_value = "10.0.0.1"
                elif input_count == 8:
                    ip_value = "10.0.0.1-10.0.0.10"
                else:
                    ip_value = "10.0.0.1"
            else:  # v6
                if input_count == 8:
                    ip_value = "2001:0db8:0000:0000:0000:0000:0000:0001"
                elif input_count == 16:
                    ip_value = "2001:0db8:0000:0000:0000:0000:0000:0001-2001:0db8:0000:0000:0000:0000:0000:000a"
                else:
                    ip_value = "2001:0db8:0000:0000:0000:0000:0000:0001"

        LOG.info(f"    IP 填充: {label} = {ip_value} (version={ip_version}, inputs={input_count})")

        # 2. 解析 IP 值为各段
        segments = self._parse_ip_segments(ip_value, input_count, ip_version)
        if not segments:
            LOG.warning(f"    IP 解析失败: {ip_value}")
            return False, {"skipped_reason": "ip_parse_failed"}

        if len(segments) != input_count:
            LOG.warning(f"    IP 段数不匹配: 解析出 {len(segments)} 段，需要 {input_count} 段")
            return False, {"skipped_reason": "segment_count_mismatch"}

        # 3. 逐个填充 input
        filled_count = 0
        for i, (selector, value) in enumerate(zip(sub_selectors, segments)):
            try:
                # 点击 input 获取焦点
                await self.page.click(selector, timeout=2000)
                await self.page.wait_for_timeout(50)

                # 清空并填充
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
                await self.page.keyboard.type(value, delay=30)

                # 触发 input 事件以更新 Vue 响应式
                await self.page.evaluate(f"""() => {{
                    const input = document.querySelector('{selector}');
                    if (input) {{
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}
                }}""")

                await self.page.wait_for_timeout(100)
                filled_count += 1
                LOG.debug(f"      IP input[{i}] = {value}")

            except Exception as e:
                LOG.warning(f"    IP input[{i}] 填充失败: {selector} - {e}")
                # 继续填充其他段，不中断

        if filled_count == 0:
            return False, {"skipped_reason": "all_inputs_failed"}

        LOG.info(f"    ✅ IP 填充成功: {filled_count}/{input_count} 段")
        return True, {"option_text": ip_value, "input_count": filled_count}

    def _parse_ip_segments(self, ip_value: str, input_count: int, ip_version: str) -> list:
        """解析 IP 值为各段列表。

        Args:
            ip_value: IP 值字符串（如 "10.0.0.1" 或 "10.0.0.1-10.0.0.10"）
            input_count: 期望的段数
            ip_version: IPv 版本 ('v4' 或 'v6')

        Returns:
            list: 各段字符串列表
        """
        if not ip_value:
            return []

        # IPv4 处理
        if ip_version == "v4":
            if input_count == 4:
                # 单地址: "10.0.0.1" → ["10", "0", "0", "1"]
                parts = ip_value.split(".")
                return parts[:4] if len(parts) >= 4 else []
            elif input_count == 8:
                # 区间: "10.0.0.1-10.0.0.10" → ["10","0","0","1","10","0","0","10"]
                if "-" in ip_value:
                    start, end = ip_value.split("-", 1)
                    start_parts = start.strip().split(".")
                    end_parts = end.strip().split(".")
                    if len(start_parts) == 4 and len(end_parts) == 4:
                        return start_parts + end_parts
                return []

        # IPv6 处理
        elif ip_version == "v6":
            if input_count == 8:
                # 单地址: "2001:db8::1" → ["2001","0db8","0000","0000","0000","0000","0000","0001"]
                expanded = self._expand_ipv6(ip_value)
                return expanded.split(":") if expanded else []
            elif input_count == 16:
                # 区间
                if "-" in ip_value:
                    start, end = ip_value.split("-", 1)
                    start_expanded = self._expand_ipv6(start.strip())
                    end_expanded = self._expand_ipv6(end.strip())
                    if start_expanded and end_expanded:
                        return start_expanded.split(":") + end_expanded.split(":")
                return []

        return []

    def _expand_ipv6(self, addr: str) -> str:
        """将 IPv6 地址展开为完整 8 段形式。

        Args:
            addr: IPv6 地址（可含 :: 缩写）

        Returns:
            str: 完整展开的地址（如 "2001:0db8:0000:0000:0000:0000:0000:0001"）
        """
        import re

        if not addr or ":" not in addr:
            return ""

        # 处理 :: 缩写
        if "::" in addr:
            prefix, suffix = addr.split("::", 1)
            prefix_parts = prefix.split(":") if prefix else []
            suffix_parts = suffix.split(":") if suffix else []
            missing = 8 - len(prefix_parts) - len(suffix_parts)
            parts = prefix_parts + ["0"] * missing + suffix_parts
        else:
            parts = addr.split(":")

        # 补齐到 8 段
        while len(parts) < 8:
            parts.append("0")

        # 每段补齐到 4 位
        expanded = ":".join(p.zfill(4) for p in parts)
        return expanded

    async def _execute_transfer_box(self, label: str, option_text: str = "",
                                    _options=None, selector: str = "",
                                    playwright_locator: str = None) -> Tuple[bool, Dict]:
        """transfer-box 穿梭框专用处理器

        处理 el-table 结构的穿梭框（tbody/tr/span），不包含 checkbox 或 li 的 fallback。

        Args:
            label: 字段标签
            option_text: 要选择的选项文本（为空则选择第一个）
            selector: 组件容器 selector（未使用）
            _options: 未使用
            playwright_locator: Playwright locator（未使用，保持接口一致）

        Returns:
            Tuple[bool, Dict]: (是否成功, {"option_text": str, "selected_count": int})
        """
        import json

        # 定位左侧面板
        transfer_left = await self.page.query_selector(
            '[class*="transfer-box-left"], [class*="transfer-left"], '
            '.el-transfer-panel:first-child, [class*="transfer"] .panel:first-child'
        )

        if not transfer_left:
            LOG.warning(f"    transfer-box {label}: 未找到左侧面板")
            return False, {"skipped_reason": "no_transfer_box_found"}

        LOG.info(f"    检测到自定义穿梭框左侧面板，搜索可点击元素...")

        # === STEP 1: 优先使用 xpath 点击（用户验证的路径）===
        # //tbody//tr[@class="el-table__row"]//span[1]
        LOG.info(f"    [transfer-box] 尝试 xpath 点击: //tbody//tr[@class='el-table__row']//span[1]")
        xpath_click_result = await self.page.evaluate("""(container) => {
            const xpath = '//tbody//tr[@class="el-table__row"]//span[1]';
            const result = document.evaluate(xpath, container, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
            const span = result.singleNodeValue;

            if (span) {
                const text = (span.textContent || '').trim();
                const r = span.getBoundingClientRect();

                // === 点击前诊断信息 START ===
                const debugInfo = {
                    // 1. 元素基本属性
                    text: text,
                    tag: span.tagName,
                    cls: (span.className || '').substring(0, 50),

                    // 2. 位置和尺寸
                    rect: {
                        x: Math.round(r.x),
                        y: Math.round(r.y),
                        width: Math.round(r.width),
                        height: Math.round(r.height)
                    },

                    // 3. 可见性检查
                    visibility: {
                        isVisible: r.width > 0 && r.height > 0,
                        isDisplayed: window.getComputedStyle(span).display !== 'none',
                        opacity: window.getComputedStyle(span).opacity,
                        pointerEvents: window.getComputedStyle(span).pointerEvents
                    },

                    // 4. 事件监听器检查
                    eventListeners: {
                        hasClickHandler: !!span.onclick,
                        hasVueComponent: !!span.__vue__,
                        parentHasClickHandler: span.parentElement ? !!span.parentElement.onclick : false,
                        closestTableRow: !!span.closest('tr'),
                        closestTableCell: !!span.closest('td')
                    },

                    // 5. 父元素链（向上 5 层）
                    parentChain: (() => {
                        const chain = [];
                        let el = span;
                        for (let i = 0; i < 5 && el; i++) {
                            chain.push({
                                tag: el.tagName,
                                cls: (el.className || '').substring(0, 30),
                                hasClickHandler: !!el.onclick
                            });
                            el = el.parentElement;
                        }
                        return chain;
                    })(),

                    // 6. 遮挡检查（检查中心点被哪个元素遮挡）
                    obstruction: (() => {
                        const centerX = r.x + r.width / 2;
                        const centerY = r.y + r.height / 2;
                        const topElement = document.elementFromPoint(centerX, centerY);
                        return {
                            isTopElement: topElement === span,
                            topElementTag: topElement ? topElement.tagName : null,
                            topElementCls: topElement ? (topElement.className || '').substring(0, 30) : null,
                            topElementText: topElement ? (topElement.textContent || '').trim().substring(0, 30) : null
                        };
                    })(),

                    // 7. 禁用状态
                    disabled: {
                        isDisabled: span.hasAttribute('disabled'),
                        hasDisabledClass: span.classList.contains('is-disabled'),
                        isReadOnly: span.hasAttribute('readonly')
                    },

                    // 8. Vue 组件信息（如果有）
                    vueInfo: (() => {
                        if (!span.__vue__) return null;
                        const vm = span.__vue__;
                        return {
                            componentName: vm.$options.name || vm.$options._componentTag || 'unknown',
                            hasClickEvent: !!vm.$listeners?.click,
                            parentComponentName: vm.$parent?.$options?.name || 'unknown'
                        };
                    })()
                };
                // === 点击前诊断信息 END ===

                console.log('[transfer-box] 点击前诊断:', JSON.stringify(debugInfo, null, 2));

                // 执行点击
                span.click();

                return {
                    clicked: true,
                    ...debugInfo
                };
            }
            return { clicked: false, reason: 'xpath not found' };
        }""", transfer_left)
        LOG.info(f"    [transfer-box] xpath 点击结果: {json.dumps(xpath_click_result, ensure_ascii=False)}")

        if xpath_click_result.get('clicked'):
            target_text = xpath_click_result.get('text', '')

            # === 输出点击前的诊断信息 ===
            LOG.info(f"    [transfer-box] === 点击诊断信息 ===")
            LOG.info(f"    [transfer-box] 元素文本: '{xpath_click_result.get('text')}'")
            LOG.info(f"    [transfer-box] 位置: {xpath_click_result.get('rect')}")
            LOG.info(f"    [transfer-box] 可见性: {json.dumps(xpath_click_result.get('visibility'), ensure_ascii=False)}")
            LOG.info(f"    [transfer-box] 事件监听: {json.dumps(xpath_click_result.get('eventListeners'), ensure_ascii=False)}")
            LOG.info(f"    [transfer-box] 父元素链: {json.dumps(xpath_click_result.get('parentChain'), ensure_ascii=False)}")
            LOG.info(f"    [transfer-box] 遮挡检查: {json.dumps(xpath_click_result.get('obstruction'), ensure_ascii=False)}")
            LOG.info(f"    [transfer-box] 禁用状态: {json.dumps(xpath_click_result.get('disabled'), ensure_ascii=False)}")
            if xpath_click_result.get('vueInfo'):
                LOG.info(f"    [transfer-box] Vue 信息: {json.dumps(xpath_click_result.get('vueInfo'), ensure_ascii=False)}")
            LOG.info(f"    [transfer-box] === 诊断信息结束 ===")

            # === 多次检查右侧面板（Vue 可能需要时间更新）===
            right_panel_updated = False
            for attempt in range(3):
                await self.page.wait_for_timeout(300)  # 每次等待 300ms

                right_panel = await self.page.evaluate("""() => {
                    const right = document.querySelector('[class*="transfer-box-right"]');
                    if (!right) return { found: false };

                    const items = right.querySelectorAll('div, span, p, li');
                    const texts = [];
                    for (const el of items) {
                        const t = (el.textContent || '').trim();
                        if (t && t.length < 80) texts.push(t);
                    }

                    return {
                        found: true,
                        itemCount: items.length,
                        texts: texts.slice(0, 10),
                        rect: (() => {
                            const r = right.getBoundingClientRect();
                            return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
                        })()
                    };
                }""")

                LOG.info(f"    [transfer-box] 右侧面板检查 (attempt {attempt + 1}/3): itemCount={right_panel.get('itemCount', 0)}, texts={right_panel.get('texts', [])[:5]}")

                # 如果右侧面板有内容，说明点击成功
                if right_panel.get('itemCount', 0) > 0:
                    LOG.info(f"    [transfer-box] ✓ 右侧面板已更新，点击成功")
                    right_panel_updated = True
                    break

            if not right_panel_updated:
                LOG.warning(f"    [transfer-box] ⚠ 右侧面板未更新，可能点击未生效")
                LOG.warning(f"    [transfer-box] 建议检查：1) 是否需要点击父元素(tr/td) 2) 是否需要用 Playwright click 3) 是否需要触发 Vue 事件")

            return True, {"option_text": target_text, "selected_count": 1 if right_panel_updated else 0}

        # === STEP 2: xpath 失败时，回退到 querySelectorAll + 筛选 ===
        LOG.info(f"    [transfer-box] xpath 未找到，回退到 querySelectorAll 方式")
        LOG.info(f"    [transfer-box] xpath 失败原因: {xpath_click_result.get('reason', 'unknown')}")

        # 在左侧面板内找到可见、可点击的叶子元素
        click_result = await self.page.evaluate("""(container) => {
            const items = container.querySelectorAll('div, span, p, label, a');
            const candidates = [];
            for (const el of items) {
                const r = el.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) continue;
                if (r.height > 60) continue;
                const text = (el.textContent || '').trim();
                if (!text || text.length > 80) continue;
                if (el.tagName === 'INPUT' || el.tagName === 'BUTTON') continue;
                if (el.closest('[class*="header"], [class*="search"], [class*="filter"], [class*="title"]')) continue;
                if (el.children.length <= 2) {
                    candidates.push({
                        text,
                        tag: el.tagName,
                        cls: (el.className || '').substring(0, 60),
                        w: Math.round(r.width),
                        h: Math.round(r.height),
                        childCount: el.children.length,
                        isLeaf: el.children.length === 0,
                        hasAlphanumeric: /[a-zA-Z0-9]/.test(text)
                    });
                }
            }
            return candidates;
        }""", transfer_left)

        if click_result and len(click_result) > 0:
            LOG.info(f"    穿梭框左侧面板找到 {len(click_result)} 个候选项: {[c['text'][:20] for c in click_result[:5]]}")
            # === DIAG-STEP2: 输出每个候选项的详细信息 ===
            for i, c in enumerate(click_result[:10]):
                LOG.info(f"    [DIAG-STEP2] candidate[{i}]: text='{c['text']}', tag={c.get('tag')}, "
                         f"isLeaf={c.get('isLeaf')}, hasAlphanumeric={c.get('hasAlphanumeric')}, "
                         f"cls='{c.get('cls', '')[:40]}'")
            # 结构性筛选：叶子元素 + 非标题容器
            # ★ 修改：移除 hasAlphanumeric 要求，支持纯中文文本（如"普通用户"）
            valid_candidates = [
                c for c in click_result
                if c.get('isLeaf')                           # 必须是叶子元素
                and len(c.get('text', '')) > 2               # 文本长度 > 2
                and 'box-left-text' not in c.get('cls', '')  # 排除标题容器
                and 'text-po' not in c.get('cls', '')        # 排除标题子容器
                and c.get('text', '') not in ['全选', '可选项']  # 排除常见 header 文本
            ]
            LOG.info(f"    [DIAG-STEP2] 筛选后有效候选: {len(valid_candidates)} 个")
            if valid_candidates:
                target_text = valid_candidates[0]["text"]
                LOG.info(f"    穿梭框候选项筛选: 选择 '{target_text}' (从 {len(valid_candidates)} 个有效候选中)")
            else:
                target_text = click_result[0]["text"]
                LOG.warning(f"    穿梭框候选项筛选: 未找到有效候选项，使用默认 '{target_text}'")
            # 用文本精确点击目标元素
            import json as _json_ls
            _escaped_text = _json_ls.dumps(target_text)
            LOG.info(f"    [DIAG-STEP3] 准备点击文本: '{target_text}'")
            clicked = await self.page.evaluate(f"""(container) => {{
                const targetText = {_escaped_text};
                const items = container.querySelectorAll('div, span, p, label, a');
                let foundEl = null;
                for (const el of items) {{
                    if ((el.textContent || '').trim() === targetText) {{
                        foundEl = el;
                        break;
                    }}
                }}
                if (foundEl) {{
                    const r = foundEl.getBoundingClientRect();
                    foundEl.click();
                    return {{
                        clicked: true,
                        tag: foundEl.tagName,
                        cls: (foundEl.className || '').substring(0, 50),
                        w: Math.round(r.width), h: Math.round(r.height)
                    }};
                }}
                return {{ clicked: false, reason: 'not found' }};
            }}""", transfer_left)
            LOG.info(f"    [DIAG-STEP3] 点击结果: {json.dumps(clicked, ensure_ascii=False)}")

            if clicked:
                await self.page.wait_for_timeout(500)
                LOG.info(f"    穿梭框点击成功: '{target_text}'")

                # === DIAG: 检查右侧面板是否更新 ===
                right_panel = await self.page.evaluate("""() => {
                    const right = document.querySelector('[class*="transfer-box-right"]');
                    if (!right) return { found: false };
                    const items = right.querySelectorAll('div, span, p');
                    const texts = [];
                    for (const el of items) {
                        const t = (el.textContent || '').trim();
                        if (t && t.length < 80) texts.push(t);
                    }
                    return { found: true, itemCount: items.length, texts: texts.slice(0, 10) };
                }""")
                LOG.info(f"    [DIAG-transfer] 右侧面板: {json.dumps(right_panel, ensure_ascii=False)}")

                # === DIAG: 如果右侧面板为空，dump 左侧 DOM 树 ===
                if right_panel.get("itemCount", 0) == 0:
                    deep_diag = await self.page.evaluate("""() => {
                        const left = document.querySelector('[class*="transfer-box-left"]');
                        if (!left) return { found: false };
                        function dumpTree(el, depth) {
                            if (depth > 3) return null;
                            const r = el.getBoundingClientRect();
                            if (r.width <= 0 || r.height <= 0) return null;
                            const result = {
                                tag: el.tagName,
                                cls: (el.className || '').substring(0, 40),
                                text: (el.childNodes.length === 1 && el.childNodes[0].nodeType === 3)
                                    ? el.textContent.trim().substring(0, 30) : '',
                                w: Math.round(r.width), h: Math.round(r.height),
                                children: []
                            };
                            for (const child of el.children) {
                                const c = dumpTree(child, depth + 1);
                                if (c) result.children.push(c);
                            }
                            return result;
                        }
                        return { found: true, tree: dumpTree(left, 0) };
                    }""")
                    LOG.info(f"    [DIAG-transfer] 左侧 DOM 树: {json.dumps(deep_diag, ensure_ascii=False)}")

                return True, {"option_text": target_text, "selected_count": 1}
            else:
                LOG.warning(f"    穿梭框 JS click 失败: '{target_text}'")

        else:
            LOG.warning(f"    穿梭框左侧面板内未找到可点击元素")

        LOG.warning(f"    transfer-box {label}: 未找到可点击元素")
        return False, {"skipped_reason": "no_clickable_items"}

    # ---- 内部工具方法 ----

    async def _verify_select_filled(self, label: str, selector: str = "") -> dict:
        """验证 select 字段填充后是否有值。

        支持单选（input.value）、多选 tag 模式（.el-tag）、tree-select（.el-select__selected-item）。
        优先使用 selector 直接定位，回退到 label 搜索。

        Returns:
            dict: {"filled": bool, "displayed_value": str, "is_empty": bool}
        """
        js = """([label, selector]) => {
            let sel = null;

            // 优先使用 selector 直接定位
            if (selector) {
                try {
                    const el = document.querySelector(selector);
                    if (el) {
                        // selector 可能指向 .el-select 本身或其子元素
                        sel = el.classList.contains('el-select') ? el : el.closest('.el-select');
                        if (!sel) sel = el.querySelector('.el-select');
                    }
                } catch (e) {}
            }

            // 回退：通过 label 搜索
            if (!sel) {
                // 去除后缀如 "(掩码)", "(下拉)", "(1)", "(2)"
                const baseLabel = label.replace(/\\([\\u4e00-\\u9fa5\\d]+\\)$/, '').trim();
                const formItems = document.querySelectorAll('.el-form-item');
                for (const fi of formItems) {
                    const labelEl = fi.querySelector('.el-form-item__label');
                    if (!labelEl) continue;
                    const labelText = labelEl.textContent.trim();
                    if (labelText.includes(baseLabel) || baseLabel.includes(labelText)) {
                        sel = fi.querySelector('.el-select');
                        if (sel) break;
                    }
                }
            }

            if (!sel) return {filled: false, reason: 'no_select_found',
                debug: {selectorFound: !!document.querySelector(selector), selector: selector}};

            const isTreeSelect = sel.classList.contains('tree-select');
            const tags = sel.querySelectorAll('.el-tag');
            const input = sel.querySelector('.el-input__inner');
            const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
            const tagsContainer = sel.querySelector('.el-select__tags');

            const hasTags = tags.length > 0;
            const hasValue = input && input.value.trim().length > 0;
            const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;

            // tree-select 特殊检测：多种策略
            let hasTreeSelected = false;
            let treeSelectedText = '';
            if (isTreeSelect) {
                // 策略1: 检查 .el-select__tags 内的文本（排除 input 元素）
                if (tagsContainer) {
                    const treeChildren = Array.from(tagsContainer.children)
                        .filter(c => c.tagName !== 'INPUT' && !c.classList.contains('el-input'));
                    if (treeChildren.length > 0) {
                        const text = treeChildren.map(c => c.textContent.trim()).filter(t => t).join(', ');
                        if (text) {
                            hasTreeSelected = true;
                            treeSelectedText = text;
                        }
                    }
                }

                // 策略2: 检查 .el-tree-node.is-current（当前选中节点）
                // 注意：Element Plus 的 tree 下拉面板 teleport 到 <body>，不在 .el-select 内部
                if (!hasTreeSelected) {
                    const currentNode = document.querySelector('.el-tree-node.is-current');
                    if (currentNode) {
                        const label = currentNode.querySelector('.el-tree-node__label');
                        if (label) {
                            const text = label.textContent.trim();
                            if (text) {
                                hasTreeSelected = true;
                                treeSelectedText = text;
                            }
                        }
                    }
                }

                // 策略3: 检查 Vue model 值
                if (!hasTreeSelected && sel.__vue__) {
                    const vue = sel.__vue__;
                    const modelValue = vue.modelValue || vue.value || vue.selectedLabel;
                    if (modelValue !== null && modelValue !== undefined && String(modelValue).trim()) {
                        hasTreeSelected = true;
                        treeSelectedText = String(modelValue);
                    }
                }

                // 策略3.5: 通过 Vue $refs.tree 获取当前选中节点的标签文本
                // 比策略4更可靠：直接从树组件读取，不依赖 placeholder
                if (!hasTreeSelected && sel.__vue__) {
                    const v = sel.__vue__;
                    if (v.$refs && v.$refs.tree) {
                        const tree = v.$refs.tree;
                        let nodeText = '';
                        // 尝试 getCurrentNode（单选树）
                        if (typeof tree.getCurrentNode === 'function') {
                            try {
                                const cur = tree.getCurrentNode();
                                if (cur && cur.label) nodeText = cur.label;
                                else if (cur && typeof cur === 'object') {
                                    nodeText = cur.name || cur.text || cur.title || '';
                                }
                            } catch(e) {}
                        }
                        // 尝试 getCheckedNodes（复选树）
                        if (!nodeText && typeof tree.getCheckedNodes === 'function') {
                            try {
                                const checked = tree.getCheckedNodes(false, true);
                                if (checked && checked.length > 0) {
                                    nodeText = checked.map(n => n.label || n.name || '').filter(t => t).join(', ');
                                }
                            } catch(e) {}
                        }
                        if (nodeText) {
                            hasTreeSelected = true;
                            treeSelectedText = nodeText;
                        }
                    }
                    // 补充: 从 input.value 读取（tree-select 选中后 input 可能显示节点文本）
                    if (!hasTreeSelected && input && input.value && input.value.trim()
                        && input.value.trim() !== '请选择') {
                        hasTreeSelected = true;
                        treeSelectedText = input.value.trim();
                    }
                }

                // 策略4: 检查 input 的 placeholder（兜底检测）
                // tree-select 选中后 placeholder 变为空字符串（默认值是"请选择"）
                // 空字符串说明已选中，非"请选择"的文本也说明已选中
                if (!hasTreeSelected && input) {
                    const ph = input.placeholder;
                    if (ph !== null && ph !== undefined) {
                        if (ph === '' || (!ph.includes('请选择') && !ph.includes('选择'))) {
                            hasTreeSelected = true;
                            treeSelectedText = ph || '';
                        }
                    }
                }
            }

            const displayed = hasTags
                ? Array.from(tags).map(t => t.textContent.trim()).join(', ')
                : hasTreeSelected ? treeSelectedText
                : (input ? input.value : '') || (selectedLabel ? selectedLabel.textContent.trim() : '');

            const filled = hasTags || hasValue || hasSelectedText || hasTreeSelected;

            return {
                filled: filled,
                displayed_value: displayed,
                is_empty: !filled,
                debug: {
                    hasTags, hasValue, hasSelectedText, hasTreeSelected,
                    tagCount: tags.length,
                    inputValue: input ? input.value : null,
                    inputPlaceholder: input ? input.placeholder : null,
                    hasTagsContainer: !!tagsContainer,
                    tagsContainerChildren: tagsContainer ? tagsContainer.children.length : 0,
                    isMultiSelect: sel.classList.contains('el-select--multiple'),
                    isTreeSelect: isTreeSelect,
                    selClassName: sel.className
                }
            };
        }"""
        try:
            return await self.page.evaluate(js, [label, selector])
        except Exception as e:
            LOG.debug(f"    _verify_select_filled exception: {e}")
            return {"filled": False, "reason": "js_exception"}

    async def _detect_active_overlay(self) -> str:
        """检测当前页面上活跃的覆盖层（弹窗/抽屉）。

        Returns:
            str: 覆盖层的 XPath 前缀，无活跃覆盖层时返回空串
        """
        from ..kb_loader import detect_active_overlay_js, get_overlay_prefix

        js = detect_active_overlay_js(self.framework)
        try:
            overlay_type = await self.page.evaluate(js)
            return get_overlay_prefix(overlay_type, self.framework)
        except Exception:
            return ""

    async def _try_step_patterns(self, step: dict, **placeholder_kwargs) -> bool:
        """尝试一个步骤的所有 pattern，任一成功即返回 True。"""
        # 检测活跃覆盖层（每次调用时检测，因为页面状态可能变化）
        overlay_prefix = await self._detect_active_overlay()

        for pattern in step.get("patterns", []):
            xpath = self.kb.expand_step(
                pattern,
                framework=self.framework,
                apply_hidden=True,
                **placeholder_kwargs
            )

            # Debug: log the generated XPath
            LOG.debug(f"    XPath pattern: {xpath[:120]}...")

            # 如果有活跃覆盖层，先尝试在覆盖层内定位
            if overlay_prefix:
                from ..kb_loader import apply_overlay_scope
                scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
                if await self._click_by_xpath(scoped_xpath):
                    return True

            # 回退：在全局范围定位
            if await self._click_by_xpath(xpath):
                LOG.debug(f"    ✓ XPath matched")
                return True

        LOG.debug(f"    ✗ All XPath patterns failed")
        return False

    async def _click_by_xpath(self, xpath: str, timeout: int = 2000) -> bool:
        """通过 XPath 点击元素，支持 iframe 遍历。

        注意：不等待加载完成，适用于输入型组件（下拉框、日期选择器等）的展开操作。
        按钮类型的点击请在 button_driver 中手动调用 wait_for_loading_complete。
        """
        # 主页面尝试
        if await self._try_click_frame(self.page, xpath, timeout):
            return True

        # iframe 遍历
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            if await self._try_click_frame(frame, xpath, timeout):
                return True

        return False

    async def _try_click_frame(self, frame, xpath: str, timeout: int = 2000) -> bool:
        """在指定 frame 中尝试点击（不等待加载）。"""
        try:
            el = frame.locator(f"xpath={xpath}")
            if await el.count() > 0:
                await el.click(timeout=timeout)
                return True
        except Exception:
            pass
        return False

    async def _check_element_visible(self, patterns: list, timeout: int = 500,
                                     **placeholder_kwargs) -> bool:
        """检查任一 pattern 匹配的元素是否可见。"""
        for p in patterns:
            xpath = self.kb.expand_step(p, **placeholder_kwargs)
            if await self._check_element_visible_raw(xpath, timeout):
                return True
        return False

    async def _check_element_visible_raw(self, xpath: str, timeout: int = 500) -> bool:
        """检查 XPath 匹配的元素是否可见。"""
        try:
            el = self.page.locator(f"xpath={xpath}")
            return await el.count() > 0
        except Exception:
            return False

    async def _try_fallback(self, label: str) -> bool:
        """当 KB 标准模板全部失败时，尝试 fallback_strategies。"""
        strategies = self.kb.get_fallback_strategies()
        for strategy in strategies:
            for pattern in strategy.get("patterns", []):
                selector = self.kb.expand_step(pattern, label=label)
                try:
                    # Playwright 原生选择器（role=, text=, xpath=）
                    el = self.page.locator(selector)
                    if await el.count() > 0:
                        await el.click(timeout=2000)
                        LOG.info(f"    fallback 成功: {strategy['name']} → {label}")
                        return True
                except Exception:
                    continue
        return False

    # ---- 选项自动发现 ----

    async def _discover_first_option(self, timeout: int = 3000) -> str:
        """展开下拉框后，读取第一个可见选项的文本。

        支持 Element UI 和 Ant Design 两种框架。
        Element UI 的下拉面板会被 teleport 到 <body>，需要等待异步渲染。

        Args:
            timeout: 等待下拉面板出现的超时时间（毫秒）

        Returns:
            第一个可见选项的文本，无选项时返回空串
        """
        if self.framework == "ant-design":
            # 等待 Ant Design 下拉选项出现
            try:
                await self.page.wait_for_selector(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    '.ant-select-item-option:not(.ant-select-item-option-disabled)',
                    state='visible',
                    timeout=timeout
                )
            except Exception:
                pass  # 继续尝试读取，可能已经出现

            js = """() => {
                const item = document.querySelector(
                    '.ant-select-dropdown:not(.ant-select-dropdown-hidden) '
                    + '.ant-select-item-option:not(.ant-select-item-option-disabled)');
                return item ? item.textContent.trim() : '';
            }"""
        else:
            # 等待 Element UI 下拉选项出现（teleport 到 <body>）
            # 支持标准 el-select 和 tree-select（含 custom-tree-node）
            try:
                await self.page.wait_for_selector(
                    '.el-select-dropdown .el-select-dropdown__item:not(.is-disabled), '
                    '.el-select-dropdown .el-tree-node__content, '
                    '.el-select-dropdown .custom-tree-node',
                    state='visible',
                    timeout=timeout
                )
            except Exception:
                pass  # 继续尝试读取，可能已经出现

            js = """() => {
                const result = {found: false, text: '', debug: {dropdowns: [], treeSelect: false}};
                const dropdowns = document.querySelectorAll('.el-select-dropdown');
                for (const dd of dropdowns) {
                    const ddInfo = {
                        visible: dd.offsetWidth > 0 && dd.offsetHeight > 0,
                        width: dd.offsetWidth,
                        height: dd.offsetHeight,
                        items: [],
                        isTree: false
                    };

                    // 先检查是否是 tree-select（检查 el-tree 结构）
                    const tree = dd.querySelector('.el-tree');
                    if (tree) {
                        ddInfo.isTree = true;
                        result.treeSelect = true;
                        // 优先查找 custom-tree-node（叶子节点），再回退到 el-tree-node__content（父节点）
                        const leafNodes = dd.querySelectorAll('.custom-tree-node');
                        const parentNodes = dd.querySelectorAll('.el-tree-node__content:not(.is-current)');
                        const allNodes = leafNodes.length > 0 ? leafNodes : parentNodes;
                        for (const node of allNodes) {
                            const text = node.textContent.trim();
                            const visible = node.offsetWidth > 0 && node.offsetHeight > 0;
                            ddInfo.items.push({text, visible});
                            if (!result.found && visible && text) {
                                result.found = true;
                                result.text = text;
                            }
                        }
                    } else {
                        // 标准 el-select
                        const items = dd.querySelectorAll('.el-select-dropdown__item:not(.is-disabled)');
                        for (const item of items) {
                            const itemInfo = {
                                text: item.textContent.trim(),
                                visible: item.offsetWidth > 0 && item.offsetHeight > 0
                            };
                            ddInfo.items.push(itemInfo);
                            if (!result.found && itemInfo.visible) {
                                result.found = true;
                                result.text = item.textContent.trim();
                            }
                        }
                    }
                    result.debug.dropdowns.push(ddInfo);
                }
                return result;
            }"""
        try:
            result = await self.page.evaluate(js)
            if isinstance(result, dict):
                if result.get('found'):
                    LOG.info(f"    _discover_first_option: found option '{result['text']}'")
                    return result['text']
                else:
                    LOG.info(f"    _discover_first_option: no visible option, debug={result.get('debug')}")
                    return ""
            else:
                return result if result else ""
        except Exception as e:
            LOG.info(f"    _discover_first_option exception: {e}")
            return ""

    async def _type_search_text(self, label: str, text: str, selector: str = "") -> bool:
        """在 el-select 输入框中输入搜索文本。

        Args:
            label: 字段标签
            text: 要输入的搜索文本
            selector: el-select 的 CSS selector（用于 fallback）

        Returns:
            是否成功输入
        """
        # 方案 A：通过 label-relative XPath 定位输入框
        xpaths = [
            f"//*[contains(text(),'{label}')]/following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
            f"//label[contains(.,'{label}')]//following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
        ]
        for xpath in xpaths:
            try:
                input_el = self.page.locator(f"xpath={xpath}").first
                # 检查是否为 readonly
                is_readonly = await input_el.get_attribute("readonly")
                if is_readonly is not None:
                    LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                    return False
                await input_el.fill("")  # 清空
                await input_el.type(text, delay=50)  # 逐字符输入，触发搜索
                LOG.debug(f"    已输入搜索文本: {text}")
                return True
            except Exception:
                continue

        # 方案 B：fallback 到 selector 对应的输入框
        if selector:
            try:
                # selector 是 el-select 容器，需要找到其中的 input
                input_selector = f"{selector} input.el-input__inner"
                input_el = self.page.locator(input_selector).first
                # 检查是否为 readonly
                is_readonly = await input_el.get_attribute("readonly")
                if is_readonly is not None:
                    LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                    return False
                await input_el.fill("")
                await input_el.type(text, delay=50)
                LOG.debug(f"    已输入搜索文本 (selector fallback): {text}")
                return True
            except Exception as e:
                LOG.warning(f"    无法输入搜索文本 (selector): {e}")

        # 方案 C：fallback 到最近展开的 dropdown 对应的输入框
        try:
            input_el = self.page.locator(".el-select-dropdown:not([style*='display: none']) + * input.el-input__inner").first
            # 检查是否为 readonly
            is_readonly = await input_el.get_attribute("readonly")
            if is_readonly is not None:
                LOG.debug(f"    输入框为 readonly，跳过输入搜索文本")
                return False
            await input_el.fill("")
            await input_el.type(text, delay=50)
            LOG.debug(f"    已输入搜索文本 (CSS fallback): {text}")
            return True
        except Exception as e:
            LOG.warning(f"    无法输入搜索文本: {e}")
            return False

    async def _click_visible_option(self, option_text: str = "") -> bool:
        """直接点击可见的下拉选项（KB XPath 失败时的回退）。

        支持标准 el-select 和 tree-select（含 .el-tree 结构）。

        Args:
            option_text: 要选择的选项文本，为空则选择第一个可见选项

        Returns:
            是否成功点击
        """
        try:
            if option_text:
                # 点击指定文本的选项（支持标准 item 和 tree node）
                clicked = await self.page.evaluate("""(text) => {
                    const dds = document.querySelectorAll('.el-select-dropdown');
                    for (const dd of dds) {
                        if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;

                        // 检查是否是 tree-select
                        const tree = dd.querySelector('.el-tree');
                        if (tree) {
                            // 优先查找 custom-tree-node（叶子节点）
                            const leafNodes = tree.querySelectorAll('.custom-tree-node');
                            if (leafNodes.length > 0) {
                                for (const node of leafNodes) {
                                    if (node.textContent.trim() === text) {
                                        // 优先点击勾选框：checkbox 在 .el-tree-node__content 级别，
                                        // 不在 .custom-tree-node 内部，需要向上查找
                                        const treeContent = node.closest('.el-tree-node__content');
                                        const checkbox = treeContent ? treeContent.querySelector('.el-checkbox') : null;
                                        if (checkbox) {
                                            checkbox.click();
                                        } else {
                                            node.click();
                                        }
                                        return true;
                                    }
                                }
                            }
                            // 回退到 el-tree-node__label
                            const labels = tree.querySelectorAll('.el-tree-node__label');
                            for (const label of labels) {
                                if (label.textContent.trim() === text) {
                                    // 优先点击勾选框
                                    const treeNode = label.closest('.el-tree-node');
                                    const checkbox = treeNode ? treeNode.querySelector('.el-checkbox') : null;
                                    if (checkbox) {
                                        checkbox.click();
                                    } else {
                                        label.click();
                                    }
                                    return true;
                                }
                            }
                        }

                        // 标准 el-select
                        const items = dd.querySelectorAll('.el-select-dropdown__item');
                        for (const item of items) {
                            if (item.textContent.trim() === text) {
                                item.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""", option_text)
            # 多选 select 选择后下拉框不自动关闭，需要按 ESC 关闭让 tags 渲染
            if clicked:
                await self.page.wait_for_timeout(200)
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(300)
            else:
                # 点击第一个可见选项（支持标准 item 和 tree node）
                clicked = await self.page.evaluate("""() => {
                    const dds = document.querySelectorAll('.el-select-dropdown');
                    for (const dd of dds) {
                        if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;

                        // 检查是否是 tree-select
                        const tree = dd.querySelector('.el-tree');
                        if (tree) {
                            // 优先查找 custom-tree-node（叶子节点）
                            const leafNodes = tree.querySelectorAll('.custom-tree-node');
                            if (leafNodes.length > 0) {
                                for (const node of leafNodes) {
                                    if (node.offsetWidth > 0 && node.offsetHeight > 0) {
                                        // 优先点击勾选框：checkbox 在 .el-tree-node__content 级别，
                                        // 不在 .custom-tree-node 内部，需要向上查找
                                        const treeContent = node.closest('.el-tree-node__content');
                                        const checkbox = treeContent ? treeContent.querySelector('.el-checkbox') : null;
                                        if (checkbox) {
                                            checkbox.click();
                                        } else {
                                            node.click();
                                        }
                                        return true;
                                    }
                                }
                            }
                            // 尝试展开第一个未展开的树节点
                            const expandIcons = tree.querySelectorAll('.el-tree-node__expand-icon:not(.is-leaf)');
                            for (const icon of expandIcons) {
                                if (icon.offsetWidth > 0 && icon.offsetHeight > 0) {
                                    icon.click();
                                    return false; // 已展开，返回 false 让外层重试
                                }
                            }
                            // 回退到 el-tree-node__label
                            const labels = tree.querySelectorAll('.el-tree-node__label');
                            for (const label of labels) {
                                if (label.offsetWidth > 0 && label.offsetHeight > 0) {
                                    // 优先点击勾选框
                                    const treeNode = label.closest('.el-tree-node');
                                    const checkbox = treeNode ? treeNode.querySelector('.el-checkbox') : null;
                                    if (checkbox) {
                                        checkbox.click();
                                    } else {
                                        label.click();
                                    }
                                    return true;
                                }
                            }
                        }

                        // 标准 el-select
                        const items = dd.querySelectorAll('.el-select-dropdown__item:not(.is-disabled)');
                        for (const item of items) {
                            if (item.offsetWidth > 0 && item.offsetHeight > 0) {
                                item.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }""")
            # 多选 select 选择后下拉框不自动关闭，需要按 ESC 关闭让 tags 渲染
            if clicked:
                await self.page.wait_for_timeout(200)
                await self.page.keyboard.press("Escape")
                await self.page.wait_for_timeout(300)
            return bool(clicked)
        except Exception as e:
            LOG.debug(f"    _click_visible_option exception: {e}")
            return False

    async def _wait_for_options_loaded(self) -> bool:
        """等待下拉选项加载完成（最多 4 秒）。

        同时支持标准 el-select 和 tree-select（含 .el-tree 结构）。

        Returns:
            bool: 是否成功加载选项
        """
        for i in range(8):  # 8 * 500ms = 4 秒
            state = await self.page.evaluate("""() => {
                const dds = document.querySelectorAll('.el-select-dropdown');
                for (const dd of dds) {
                    if (dd.offsetWidth === 0 || dd.offsetHeight === 0) continue;

                    // 检查是否是 tree-select
                    const tree = dd.querySelector('.el-tree');
                    if (tree) {
                        const treeNodes = tree.querySelectorAll('.el-tree-node');
                        const emptyText = tree.querySelector('.el-tree__empty-text');
                        const isEmpty = emptyText && emptyText.offsetWidth > 0;
                        return {
                            found: true,
                            visible: true,
                            itemCount: treeNodes.length,
                            isTree: true,
                            isEmpty: isEmpty,
                            emptyText: emptyText ? emptyText.textContent.trim() : null,
                            firstItemText: treeNodes.length > 0
                                ? (treeNodes[0].querySelector('.el-tree-node__label')
                                    || treeNodes[0]).textContent.trim()
                                : null
                        };
                    }

                    // 标准 el-select
                    const items = dd.querySelectorAll('.el-select-dropdown__item');
                    return {
                        found: true,
                        visible: true,
                        itemCount: items.length,
                        isTree: false,
                        isEmpty: false,
                        firstItemText: items.length > 0 ? items[0].textContent.trim() : null
                    };
                }
                return {found: false, visible: false, itemCount: 0, isTree: false, isEmpty: false, firstItemText: null};
            }""")
            if state['found']:
                if state['isTree']:
                    if state['itemCount'] > 0:
                        LOG.debug(f"    tree-select 加载完成: {state['itemCount']} 个节点, 第一个: '{state['firstItemText']}'")
                        return True
                    if state['isEmpty']:
                        LOG.info(f"    tree-select 为空: {state['emptyText']}")
                        return False
                else:
                    if state['itemCount'] > 0:
                        LOG.debug(f"    选项加载完成: {state['itemCount']} 个选项, 第一个: '{state['firstItemText']}'")
                        return True
            LOG.debug(f"    等待选项加载... 第 {i+1}/8 次检查, 当前状态: {state}")
            await self.page.wait_for_timeout(500)
        LOG.warning(f"    等待选项加载超时 (4 秒)")
        return False

    async def _read_cascader_current_items(self) -> list:
        """读取级联选择器当前（最后）面板的菜单项。

        Returns:
            [{"text": str, "hasChildren": bool}] 列表
        """
        if self.framework == "ant-design":
            js = """() => {
                const menus = document.querySelectorAll('.ant-cascader-menu');
                if (!menus.length) return [];
                const last = menus[menus.length - 1];
                const items = last.querySelectorAll(
                    '.ant-cascader-menu-item:not(.ant-cascader-menu-item-disabled)');
                return Array.from(items).map(item => ({
                    text: item.textContent.trim(),
                    hasChildren: item.querySelector(
                        '.ant-cascader-menu-item-expand-icon, '
                        + '[class*="arrow"], [class*="right"]') !== null
                })).filter(i => i.text);
            }"""
        else:
            js = """() => {
                const menus = document.querySelectorAll('.el-cascader-menu');
                if (!menus.length) return [];
                const last = menus[menus.length - 1];
                const items = last.querySelectorAll('li[role="menuitem"]');
                return Array.from(items).map(item => ({
                    text: (item.querySelector('span') || item).textContent.trim(),
                    hasChildren: item.querySelector(
                        '.el-icon-arrow-right, '
                        + 'i[class*="arrow-right"], '
                        + '[class*="postfix"]') !== null
                })).filter(i => i.text);
            }"""
        try:
            return await self.page.evaluate(js)
        except Exception:
            return []

    async def _cascader_select_by_path(self, steps: dict, label: str, options: list) -> bool:
        """已知路径：逐级展开中间级，最后一级条件分支选择。"""
        for i, opt in enumerate(options[:-1]):
            level_expanded = await self._try_step_patterns(
                steps.get("expand-level", {}), label=label, option_text=opt)
            if not level_expanded:
                LOG.warning(f"级联第 {i+1} 级 '{opt}' 展开失败")
                return False
            await self.page.wait_for_timeout(300)

        last_option = options[-1]
        branch = self.kb.get_conditional_branch("el-cascader", self.framework)

        if branch:
            condition_xpath = self.kb.expand_step(
                branch.get("condition_locator", ""), label=label, last_text=last_option)
            has_checkbox = await self._check_element_visible_raw(
                condition_xpath, timeout=branch.get("timeout", 500))
            target_step = (branch.get("if_visible") if has_checkbox
                           else branch.get("if_not_visible"))
        else:
            target_step = "select-last-text"

        return await self._try_step_patterns(
            steps.get(target_step, {}), label=label, option_text=last_option)

    async def _cascader_discover_and_select(self, steps: dict, label: str) -> bool:
        """逐级发现+选择：每级选第一个有子级的项展开，到叶子级时选择。

        策略：
        - 每级读取当前面板的菜单项
        - 如果有子级指示（箭头图标），点击第一项展开下一级
        - 如果无子级（叶子节点），直接点击选择
        - 最大深度 10 级保护
        """
        max_depth = 10

        for depth in range(max_depth):
            items = await self._read_cascader_current_items()
            if not items:
                LOG.warning(f"级联第 {depth+1} 级无菜单项")
                return False

            first_item = items[0]
            if first_item.get("hasChildren"):
                # 有子级：点击展开下一级
                expanded = await self._try_step_patterns(
                    steps.get("expand-level", {}),
                    label=label, option_text=first_item["text"])
                if not expanded:
                    LOG.warning(f"级联第 {depth+1} 级 '{first_item['text']}' 展开失败")
                    return False
                await self.page.wait_for_timeout(300)
            else:
                # 叶子节点：直接选择
                branch = self.kb.get_conditional_branch("el-cascader", self.framework)
                if branch:
                    condition_xpath = self.kb.expand_step(
                        branch.get("condition_locator", ""),
                        label=label, last_text=first_item["text"])
                    has_checkbox = await self._check_element_visible_raw(
                        condition_xpath, timeout=branch.get("timeout", 500))
                    target_step = (branch.get("if_visible") if has_checkbox
                                   else branch.get("if_not_visible"))
                else:
                    target_step = "select-last-text"

                selected = await self._try_step_patterns(
                    steps.get(target_step, {}),
                    label=label, option_text=first_item["text"])
                if selected:
                    LOG.info(f"    级联自动发现: 选择了 '{first_item['text']}' (depth={depth+1})")
                return selected

        LOG.warning("级联超过最大深度，中止")
        return False

    async def discover_options(self, kb_category: str, label: str) -> dict:
        """发现组件的可用选项（不选择，仅读取）。

        供外部调用方显式发现选项。

        Args:
            kb_category: KB 类别名（如 "el-select", "el-cascader"）
            label: 字段标签

        Returns:
            el-select:  {"options": ["选项1"]}
            el-cascader: {"path": ["第一级", "第二级", ...]}
            其他:        {}
        """
        if kb_category == "el-select":
            steps = self.kb.get_steps("el-select", self.framework)
            if not steps:
                return {}
            expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
            if not expanded:
                return {}
            await self.page.wait_for_timeout(500)
            text = await self._discover_first_option()
            await self.page.keyboard.press("Escape")
            return {"options": [text]} if text else {}

        elif kb_category == "el-cascader":
            steps = self.kb.get_steps("el-cascader", self.framework)
            if not steps:
                return {}
            expanded = await self._try_step_patterns(steps.get("expand", {}), label=label)
            if not expanded:
                return {}
            await self.page.wait_for_timeout(500)
            path = []
            for depth in range(10):
                items = await self._read_cascader_current_items()
                if not items:
                    break
                path.append(items[0]["text"])
                if not items[0]["hasChildren"]:
                    break
                await self._try_step_patterns(
                    steps.get("expand-level", {}),
                    label=label, option_text=items[0]["text"])
                await self.page.wait_for_timeout(300)
            await self.page.keyboard.press("Escape")
            return {"path": path}

        return {}
