# Stage 1 前端交互问题修复方案

## 问题概述

当前 Stage 1 在用户管理模块上产生空 playbook（`operations: {}`），根源是三个前端交互问题：

1. **el-select KB 流程缺陷**：系统角色选择失败，导致 create 表单验证失败
2. **空 el-select 处理不当**：部门角色无选项被标记为验证失败
3. **级联失败**：create 失败 → 无测试用户 → update/delete 全部失败

本文档提供完整修复方案，覆盖三个问题的根因、修改点、影响分析和测试验证。

---

## 问题 1：el-select KB 多步操作的两个 bug

### 1.1 根因分析

#### Bug A：`fill` 步骤只 click 不 type

**位置**：`form_filler.py:1537-1566` (`_try_step_patterns`)

**现状**：
```python
async def _try_step_patterns(self, step: dict, **placeholder_kwargs) -> bool:
    for pattern in step.get("patterns", []):
        xpath = self.kb.expand_step(pattern, ...)
        if await self._click_by_xpath(xpath):  # ← 只 click
            return True
    return False
```

**KB 定义**（`probe_knowledge.json:155-159`）：
```json
"fill": {
    "desc": "输入搜索关键词",
    "patterns": [
        "//*[contains(text(),'{label}')]/following-sibling::*[self::div or self::span]//input[@class='el-input__inner']"
    ]
}
```

**问题**：KB 的 `fill` 步骤本意是在可搜索输入框中输入 `option_text`，但 `_try_step_patterns` 只调用 `_click_by_xpath`（点击聚焦输入框），从未实际输入文本。

**日志证据**：
```
el-select 系统角色: fill result=True     ← 只 click 了，没 type
el-select 系统角色: select result=False  ← 搜索没发生，选项没被过滤
```

#### Bug B：`select` XPath 依赖 `@x-placement`

**KB 定义**（`probe_knowledge.json:160-163`）：
```json
"select": {
    "patterns": [
        "(//div[@x-placement and not(@x-placement='')]//li[contains(@class,'el-select-dropdown__item') and contains(.,'{option_text}')...])[1]"
    ]
}
```

**问题**：某些 Element UI 版本的 dropdown 容器可能使用 `data-popper-placement` 或根本没有 `x-placement` 属性，导致 XPath 匹配失败。

**当前 fallback**：`_execute_select_with_details` 在 KB XPath 失败后会调用 `_click_visible_option`（JS 遍历 `.el-select-dropdown`），但如果前面的 `fill` 步骤没有正确输入搜索文本，下拉选项没有被过滤，可能匹配到错误的选项。

### 1.2 修复方案

#### 修改点 1：为 editable select 的 fill 步骤增加 type 逻辑

**文件**：`module_discovery/form_filler.py`  
**方法**：`_execute_select_with_details` (lines 1362-1394)

**当前代码**（line 1365）：
```python
if is_editable:
    fill_result = await self._try_step_patterns(steps.get("fill", {}), label=label)
```

**修改为**：
```python
if is_editable:
    # 1. 点击输入框聚焦
    fill_result = await self._try_step_patterns(steps.get("fill", {}), label=label)
    if not fill_result:
        return False, detail
    
    # 2. 新增：输入搜索文本
    await self._type_search_text(label, option_text)
    await self.page.wait_for_timeout(500)  # 等待搜索过滤完成
```

**新增方法**：
```python
async def _type_search_text(self, label: str, text: str) -> bool:
    """在 el-select 输入框中输入搜索文本。"""
    # 方案 A：通过 label-relative XPath 定位输入框
    xpaths = [
        f"//*[contains(text(),'{label}')]/following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
        f"//label[contains(.,'{label}')]//following-sibling::*[self::div or self::span]//input[@class='el-input__inner']",
    ]
    for xpath in xpaths:
        try:
            input_el = self.page.locator(f"xpath={xpath}").first
            await input_el.fill("")  # 清空
            await input_el.type(text, delay=50)  # 逐字符输入，触发搜索
            LOG.debug(f"    已输入搜索文本: {text}")
            return True
        except Exception:
            continue
    
    # 方案 B：fallback 到 CSS selector
    try:
        # 查找最近展开的 dropdown 对应的输入框
        input_el = self.page.locator(".el-select-dropdown:not([style*='display: none']) + * input.el-input__inner").first
        await input_el.fill("")
        await input_el.type(text, delay=50)
        LOG.debug(f"    已输入搜索文本 (fallback): {text}")
        return True
    except Exception as e:
        LOG.warning(f"    无法输入搜索文本: {e}")
        return False
```

#### 修改点 2：为 select 步骤增加不依赖 `@x-placement` 的备选 XPath

**文件**：`module_discovery/kb/probe_knowledge.json`  
**位置**：el-select.steps.select.patterns

**当前**：
```json
"select": {
    "desc": "选择匹配选项",
    "patterns": [
        "(//div[@x-placement and not(@x-placement='')]//li[contains(@class,'el-select-dropdown__item') and contains(.,'{option_text}') and not(ancestor-or-self::*[contains(@class,'is-hidden')]) and not(ancestor-or-self::*[contains(@style,'display: none')])])[1]"
    ]
}
```

**修改为**：
```json
"select": {
    "desc": "选择匹配选项",
    "patterns": [
        "(//div[@x-placement and not(@x-placement='')]//li[contains(@class,'el-select-dropdown__item') and contains(.,'{option_text}') and not(ancestor-or-self::*[contains(@class,'is-hidden')]) and not(ancestor-or-self::*[contains(@style,'display: none')])])[1]",
        "(//div[contains(@class,'el-select-dropdown') and not(contains(@style,'display: none'))]//li[contains(@class,'el-select-dropdown__item') and contains(.,'{option_text}') and not(ancestor-or-self::*[contains(@class,'is-hidden')]) and not(ancestor-or-self::*[contains(@style,'display: none')])])[1]"
    ]
}
```

**同样修改 `first-option`**（line 167-170）：
```json
"first-option": {
    "desc": "readonly 时选择第一个可见项（else_steps，带 hidden filter）",
    "patterns": [
        "(//div[@x-placement and not(@x-placement='')]//li[contains(@class,'el-select-dropdown__item') and not(ancestor-or-self::*[contains(@class,'is-hidden')]) and not(ancestor-or-self::*[contains(@style,'display: none')])])[1]",
        "(//div[contains(@class,'el-select-dropdown') and not(contains(@style,'display: none'))]//li[contains(@class,'el-select-dropdown__item') and not(ancestor-or-self::*[contains(@class,'is-hidden')]) and not(ancestor-or-self::*[contains(@style,'display: none')])])[1]"
    ]
}
```

### 1.3 影响分析

**正向影响**：
- el-select 可搜索场景的选项选择成功率显著提升
- 兼容不同 Element UI 版本的 dropdown 容器

**潜在风险**：
1. **`type` 延迟问题**：`delay=50` 可能在某些慢速环境中不够，需要根据实际测试调整
2. **搜索无结果**：如果 `option_text` 不匹配任何选项，输入后下拉列表可能为空，`select` 步骤仍会失败
3. **fallback CSS selector 不准确**：方案 B 的 CSS selector 可能匹配到错误的输入框

**缓解措施**：
- `_type_search_text` 返回 bool，失败时不影响后续流程（`select` 步骤会 fallback 到 `_click_visible_option`）
- 保留原有的 `_click_visible_option` JS fallback 作为最终兜底

---

## 问题 2：空 el-select 处理不当

### 2.1 根因分析

**位置**：`form_filler.py:198-208` (`fill_multi_step_fields`)

**现状**：
```python
elif detail.get("skipped_reason") == "no_options":
    details.append({
        "label": label,
        "kb_category": kb_cat,
        "selector": selector,
        "skipped_reason": "no_options",
        "option_text": ""
    })
    LOG.info(f"    ⊘ multi_step 跳过(无选项): {label} ({kb_cat})")
```

**问题**：
1. `fill_multi_step_fields` 正确处理了 `no_options` 情况（不计数为失败，记录到 details）
2. 但 `_do_create` 在 line 1880 调用后，继续执行 `submit_form_v2`（line 1898）
3. 如果该 el-select 是必填字段，表单验证会报错 `[form_validation] 请选择部门角色`
4. `_error_driven_retry` 将此视为普通验证错误，尝试重试 10 次，每次都失败

**日志证据**：
```
部门角色: 下拉展开成功(400x39)，但 _discover_first_option 返回空
⊘ multi_step 跳过(无选项): 部门角色 (el-select)
第 1 次失败: [form_validation] 请选择部门角色
第 2 次失败: [form_validation] 请选择部门角色
...
```

### 2.2 修复方案

#### 修改点 1：在 `_do_create` 中识别并跳过 `no_options` 字段

**文件**：`module_discovery/discover_ui.py`  
**方法**：`_do_create` (lines 1879-1930)

**当前代码**（line 1880）：
```python
ms_filled, ms_details = await form_filler.fill_multi_step_fields(fields, framework)
```

**修改为**：
```python
ms_filled, ms_details = await form_filler.fill_multi_step_fields(fields, framework)

# 新增：记录哪些字段因 no_options 被跳过
skipped_no_options = [d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"]
if skipped_no_options:
    LOG.info(f"    以下字段因无选项被跳过: {skipped_no_options}")
```

#### 修改点 2：在表单验证错误处理中过滤 `no_options` 字段

**文件**：`module_discovery/discover_ui.py`  
**方法**：`_do_create` (lines 1907-1915)

**当前代码**：
```python
errors = await read_form_errors(page)
if errors:
    field_errors = [e for e in errors if e.get("severity") == "field"]
    if field_errors:
        return {
            "success": False,
            "error_type": "form_validation",
            "error_field": field_errors[0].get("field"),
            "error_text": field_errors[0].get("text"),
        }
```

**修改为**：
```python
errors = await read_form_errors(page)
if errors:
    field_errors = [e for e in errors if e.get("severity") == "field"]
    
    # 新增：过滤掉 no_options 字段的验证错误
    if field_errors and skipped_no_options:
        field_errors = [
            e for e in field_errors 
            if e.get("field") not in skipped_no_options
        ]
        if not field_errors:
            LOG.info("    表单验证错误均来自 no_options 字段，已忽略")
    
    if field_errors:
        return {
            "success": False,
            "error_type": "form_validation",
            "error_field": field_errors[0].get("field"),
            "error_text": field_errors[0].get("text"),
        }
```

### 2.3 影响分析

**正向影响**：
- 空 el-select 字段不再导致 create 操作失败
- 减少无意义的重试（10 次 → 0 次）

**潜在风险**：
1. **字段名匹配不精确**：`read_form_errors` 返回的 `field` 可能是字段 label 或 input name，与 `ms_details` 中的 `label` 可能不完全匹配
2. **误过滤真正的必填字段**：如果字段确实有选项但未被选择，会被错误忽略

**缓解措施**：
- 使用模糊匹配（`in` 而非 `==`）：`if e.get("field") not in skipped_no_options`
- 只在 `skipped_no_options` 非空时启用过滤
- 保留日志记录，便于调试

**替代方案**：
- 在 `read_form_errors` 返回时，增加 `field_type` 信息（如 "select"），只对 select 类型字段应用过滤
- 但这需要修改 `read_form_errors` 的实现，改动范围更大

---

## 问题 3：级联失败（create → update/delete）

### 3.1 根因分析

**位置**：`discover_ui.py:1614-1644` (`_validate_business_flow`)

**现状**：
```python
created_marker = None  # line 1614

# create 失败时（line 1638-1641）
if not created_marker:
    created_marker = await self._extract_fallback_marker(page)

# update/delete 使用 created_marker（line 1662, 1674）
if action == "update":
    result = await _error_driven_retry(
        lambda p, c: self._do_edit(p, c, created_marker), ...
    )
```

**问题**：
1. create 失败 → `created_marker` 为 None
2. `_extract_fallback_marker` 尝试从现有表格数据中提取第一行的名称作为 marker
3. 但如果表格为空或 fallback 提取失败，`created_marker` 仍为 None
4. `_do_edit` 和 `_do_delete` 使用 `marker=None`，导致无法定位数据行

**日志证据**：
```
Stage 1 关键操作验证失败: ['create', 'delete']
验证操作: update → [no_dialog] 点击编辑后未出现编辑表单
验证操作: delete → [api_error] 批量删除用户失败
```

### 3.2 修复方案

#### 修改点 1：增强 `_extract_fallback_marker` 的鲁棒性

**文件**：`module_discovery/discover_ui.py`  
**方法**：`_extract_fallback_marker` (需确认当前实现)

**假设当前实现**：
```python
async def _extract_fallback_marker(self, page) -> str:
    # 尝试提取第一行的名称字段
    try:
        first_row = page.locator(".el-table__body tr").first
        name_cell = first_row.locator("td:nth-child(2)").first  # 假设名称在第二列
        return await name_cell.text_content()
    except:
        return None
```

**修改为**：
```python
async def _extract_fallback_marker(self, page) -> str:
    """从现有表格数据中提取 marker（用于 create 失败时的 fallback）。"""
    try:
        # 等待表格加载
        await page.wait_for_selector(".el-table__body", timeout=3000)
        
        # 尝试多种列索引（名称可能在第 2、3、4 列）
        for col_idx in [2, 3, 4, 1]:
            try:
                first_row = page.locator(".el-table__body tr").first
                cell = first_row.locator(f"td:nth-child({col_idx})").first
                text = await cell.text_content()
                if text and len(text) > 2 and not text.startswith("操作"):
                    LOG.debug(f"    Fallback marker: {text} (col {col_idx})")
                    return text.strip()
            except:
                continue
        
        # 最后的 fallback：提取任意非空单元格
        cells = page.locator(".el-table__body td")
        count = await cells.count()
        for i in range(min(count, 10)):  # 最多尝试 10 个单元格
            try:
                text = await cells.nth(i).text_content()
                if text and len(text) > 2 and not text.startswith("操作"):
                    LOG.debug(f"    Fallback marker: {text} (cell {i})")
                    return text.strip()
            except:
                continue
        
        return None
    except Exception as e:
        LOG.warning(f"    Fallback marker 提取失败: {e}")
        return None
```

#### 修改点 2：当 marker 为 None 时，update/delete 标记为 "skipped" 而非 "failed"

**文件**：`module_discovery/discover_ui.py`  
**方法**：`_validate_business_flow` (lines 1662-1686)

**当前代码**（line 1662）：
```python
if action == "update":
    result = await _error_driven_retry(
        lambda p, c: self._do_edit(p, c, created_marker), ...
    )
```

**修改为**：
```python
if action == "update":
    if not created_marker:
        LOG.warning("    update 跳过：无可用 marker（create 失败且 fallback 失败）")
        validated[action] = {
            "success": False,
            "error_type": "skipped",
            "error_text": "无可用 marker",
        }
    else:
        result = await _error_driven_retry(
            lambda p, c: self._do_edit(p, c, created_marker), ...
        )
        if result:
            validated[action] = result
```

**同样修改 delete**（line 1674）：
```python
if action == "delete":
    if not created_marker:
        LOG.warning("    delete 跳过：无可用 marker")
        validated[action] = {
            "success": False,
            "error_type": "skipped",
            "error_text": "无可用 marker",
        }
    else:
        result = await _error_driven_retry(
            lambda p, c: self._do_delete(p, c, created_marker), ...
        )
        if result:
            validated[action] = result
```

### 3.3 影响分析

**正向影响**：
- update/delete 不再因 marker 缺失而进入无意义的重试循环
- playbook 中可以记录 "skipped" 状态，便于后续分析

**潜在风险**：
1. **`_extract_fallback_marker` 提取到错误数据**：可能提取到操作列的文本或其他非名称字段
2. **update/delete 被错误跳过**：如果 create 实际成功但 `created_marker` 未被正确设置

**缓解措施**：
- `_extract_fallback_marker` 增加多个列索引尝试和文本长度/内容过滤
- 在 `build_playbook` 中，`skipped` 状态的操作不生成步骤，但保留元数据
- 保留日志记录，便于调试

---

## 问题 4：下拉菜单按钮（lock/unlock/reset）

### 4.1 根因分析

**位置**：`discover_ui.py:2429-2595` (`_do_generic_operation`)

**现状**：
```python
# line 2442-2460
if btn_location == "row_action" and marker:
    # 直接点击行内按钮
    clicked = await driver.click_row_button_v2(row, btn_text)

elif btn_location == "dropdown" and marker:
    # 点击"更多"菜单内的按钮
    clicked = await driver.click_row_more_item(row, btn_text)

else:
    # 工具栏按钮
    clicked = await _click_button_escalating(page, btn_text)
```

**问题**：
1. 按钮发现阶段（`_discover_dropdowns`）正确识别了 dropdown 内的按钮并设置 `location="dropdown"`
2. `_do_generic_operation` 正确分支到 `btn_location == "dropdown"` 路径
3. 但 `button_driver.click_row_more_item` 的 3 层展开策略（hover → click → Vue instance）在当前环境中都失败了

**日志证据**：
```
验证操作: lock (按钮: 冻结)
  第 1 次失败: [click_failed] 无法点击 lock 按钮: 冻结
  第 2 次失败: [click_failed] 无法点击 lock 按钮: 冻结
```

**可能原因**：
1. `marker` 为 None（create 失败导致），`find_data_row(None)` 失败
2. dropdown 展开成功，但 `click_dropdown_option` 找不到匹配的 item_text
3. hover/click/Vue instance 都未能成功展开 dropdown

### 4.2 修复方案

#### 修改点 1：当 marker 为 None 时，dropdown 按钮也标记为 "skipped"

**文件**：`module_discovery/discover_ui.py`  
**方法**：`_do_generic_operation` (lines 2442-2460)

**当前代码**：
```python
if btn_location == "dropdown" and marker:
    driver = ButtonDriver(page)
    row = await driver.find_data_row(marker)
    clicked = await driver.click_row_more_item(row, btn_text)
```

**修改为**：
```python
if btn_location == "dropdown":
    if not marker:
        LOG.warning(f"    dropdown 按钮跳过：无可用 marker")
        return {
            "success": False,
            "error_type": "skipped",
            "error_text": "无可用 marker",
        }
    
    driver = ButtonDriver(page)
    row = await driver.find_data_row(marker)
    if not row:
        return {
            "success": False,
            "error_type": "click_failed",
            "error_text": f"未找到数据行: {marker}",
        }
    clicked = await driver.click_row_more_item(row, btn_text)
```

#### 修改点 2：增强 `click_row_more_item` 的日志记录

**文件**：`module_discovery/button_driver.py`  
**方法**：`click_row_more_item` (lines 274-396)

**当前代码**（line 332-354，Tier 1 hover）：
```python
await more_btn.hover()
await self.page.wait_for_timeout(300)
# dispatch mouseenter...
expanded = await self.page.evaluate(...)
if expanded:
    LOG.debug("    ✓ hover 展开成功")
```

**修改为**：
```python
await more_btn.hover()
await self.page.wait_for_timeout(300)
# dispatch mouseenter...
expanded = await self.page.evaluate(...)
if expanded:
    LOG.debug("    ✓ hover 展开成功")
else:
    LOG.debug("    ✗ hover 未展开，尝试 click")
```

**同样为 Tier 2、Tier 3 增加日志**。

### 4.3 影响分析

**正向影响**：
- dropdown 按钮在 marker 缺失时不会进入无意义的重试
- 增强日志便于诊断 dropdown 展开失败的原因

**潜在风险**：
1. **dropdown 按钮被错误跳过**：如果 marker 实际存在但未被正确传递
2. **日志过多**：每层 fallback 都记录日志可能产生噪音

**缓解措施**：
- 使用 `LOG.debug` 而非 `LOG.info`
- 保留 `error_type: "skipped"` 以便在 playbook 中区分

---

## 问题间的相互影响分析

### 1. 问题 1（el-select bug）→ 问题 2（空 el-select）

**影响**：如果问题 1 未修复，el-select 的 `fill` 步骤不输入文本，`select` 步骤失败，即使字段有选项也会被标记为失败（非 `no_options`）。

**修复顺序**：**必须先修复问题 1**，否则问题 2 的修复只覆盖了"无选项"场景，无法解决"有选项但选择失败"场景。

### 2. 问题 1 + 问题 2 → 问题 3（级联失败）

**影响**：
- 如果 create 表单中有 el-select 字段（如系统角色），问题 1 导致选择失败 → create 失败
- 如果 create 表单中有空 el-select 字段（如部门角色），问题 2 导致验证错误 → create 失败
- create 失败 → 无 `created_marker` → update/delete 失败（问题 3）

**修复顺序**：修复问题 1 和问题 2 后，create 成功率提升，问题 3 的触发概率降低。但问题 3 的修复（fallback marker + skipped 状态）仍然必要，因为 create 可能因其他原因失败（如 API 错误）。

### 3. 问题 3 → 问题 4（dropdown 按钮）

**影响**：lock/unlock/reset 按钮在 dropdown 内，需要 `marker` 来定位数据行。如果 create 失败且 fallback marker 失败，dropdown 按钮也会失败。

**修复顺序**：修复问题 3 后，dropdown 按钮在无 marker 时会被标记为 "skipped" 而非进入重试循环。

---

## 实施计划

### Phase 1：修复 el-select KB 流程（问题 1）

1. **修改 `form_filler.py`**：
   - 新增 `_type_search_text` 方法
   - 修改 `_execute_select_with_details` 的 editable 分支，调用 `_type_search_text`

2. **修改 `probe_knowledge.json`**：
   - 为 `el-select.steps.select.patterns` 增加不依赖 `@x-placement` 的备选 XPath
   - 为 `el-select.steps.first-option.patterns` 增加同样的备选 XPath

3. **测试验证**：
   - 在用户管理模块上运行 Stage 1，验证系统角色选择成功
   - 检查日志中 `fill result=True` 后是否出现 `已输入搜索文本`

### Phase 2：修复空 el-select 处理（问题 2）

1. **修改 `discover_ui.py`**：
   - 在 `_do_create` 中记录 `skipped_no_options` 列表
   - 在表单验证错误处理中过滤 `skipped_no_options` 字段的错误

2. **测试验证**：
   - 在用户管理模块上运行 Stage 1，验证部门角色无选项时 create 成功
   - 检查日志中是否出现 `以下字段因无选项被跳过: ['部门角色']`

### Phase 3：修复级联失败（问题 3）

1. **修改 `discover_ui.py`**：
   - 增强 `_extract_fallback_marker` 的鲁棒性（多列尝试 + 内容过滤）
   - 在 `_validate_business_flow` 中，当 `marker` 为 None 时将 update/delete 标记为 "skipped"

2. **测试验证**：
   - 模拟 create 失败场景，验证 update/delete 被标记为 "skipped"
   - 检查 playbook 中 update/delete 的 `status: "failed"` 和 `error_type: "skipped"`

### Phase 4：修复 dropdown 按钮（问题 4）

1. **修改 `discover_ui.py`**：
   - 在 `_do_generic_operation` 中，当 `btn_location == "dropdown"` 且 `marker` 为 None 时返回 "skipped"

2. **修改 `button_driver.py`**：
   - 在 `click_row_more_item` 的 3 层 fallback 中增加 debug 日志

3. **测试验证**：
   - 在用户管理模块上运行 Stage 1，验证 lock/unlock/reset 被标记为 "skipped"（而非 "click_failed"）
   - 检查日志中是否出现 `dropdown 按钮跳过：无可用 marker`

### Phase 5：端到端验证

1. **运行 Stage 1**：
   ```bash
   python -m module_discovery.run --project ecm-compute --module "用户管理" --stage 1
   ```
   - 预期：playbook 包含 create（成功）、update（成功或 skipped）、delete（成功或 skipped）、lock/unlock/reset（skipped）

2. **运行 Stage 2**：
   ```bash
   python -m module_discovery.run --project ecm-compute --module "用户管理" --stage 2
   ```
   - 预期：`用户管理.json` 包含 create/update/delete/reset/enable API

3. **运行 Stage 3+4**：
   ```bash
   python -m module_discovery.run --project ecm-compute --module "用户管理" --stage 34 --offline
   ```
   - 预期：生成 manifest 和测试脚本

4. **运行测试脚本**：
   ```bash
   python projects/ecm-compute/scripts/v1.0.0/api/用户管理_API测试.py
   ```
   - 预期：7/7 步骤通过

---

## 潜在副作用与缓解措施

### 1. `_type_search_text` 的延迟问题

**风险**：`delay=50` 可能在慢速环境中不够，搜索过滤未完成就开始选择。

**缓解**：
- 在 `type` 后增加 `wait_for_timeout(500)`
- 如果 `select` 步骤失败，增加重试逻辑（重新 type + 更长延迟）

### 2. 备选 XPath 匹配到错误选项

**风险**：不依赖 `@x-placement` 的 XPath 可能匹配到其他 dropdown 的选项。

**缓解**：
- 保留原有的 `@x-placement` XPath 作为第一优先
- 备选 XPath 增加 `contains(@style,'display: none')` 过滤
- 依赖 `_click_visible_option` JS fallback 作为最终兜底

### 3. `skipped_no_options` 字段名匹配不精确

**风险**：`read_form_errors` 返回的 `field` 可能是 input name（如 `departmentRoleId`），与 `ms_details` 中的 `label`（如 `部门角色`）不匹配。

**缓解**：
- 使用模糊匹配：`if any(s in e.get("field", "") for s in skipped_no_options)`
- 或者在 `fill_multi_step_fields` 中同时记录 `label` 和 `selector`，用 selector 匹配

### 4. fallback marker 提取到错误数据

**风险**：`_extract_fallback_marker` 可能提取到操作列的文本（如 "编辑"、"删除"）。

**缓解**：
- 过滤掉以 "操作"、"编辑"、"删除" 开头的文本
- 优先提取第 2、3 列（通常是名称字段）

### 5. dropdown 按钮被错误跳过

**风险**：如果 `marker` 实际存在但因传递错误导致为 None，dropdown 按钮会被跳过。

**缓解**：
- 在 `_do_generic_operation` 入口增加参数校验日志
- 保留 `error_type: "skipped"` 以便在 playbook 中区分

---

## 测试用例

### 单元测试

1. **test_type_search_text**：验证 `_type_search_text` 能正确输入文本
2. **test_select_xpath_fallback**：验证备选 XPath 能匹配到选项
3. **test_skipped_no_options_filter**：验证表单验证错误过滤逻辑
4. **test_extract_fallback_marker**：验证多列尝试和内容过滤
5. **test_dropdown_button_skipped**：验证 marker 为 None 时返回 "skipped"

### 集成测试

1. **用户管理模块端到端**：Stage 1 → 2 → 3 → 4 → 运行脚本
2. **角色管理模块端到端**：验证修复不影响其他模块
3. **模拟 create 失败**：验证 fallback marker 和 skipped 状态

---

## 总结

本文档提出的修复方案覆盖了 Stage 1 的三个核心问题：

1. **el-select KB 流程缺陷**：增加 `type` 逻辑 + 备选 XPath
2. **空 el-select 处理不当**：过滤 `no_options` 字段的验证错误
3. **级联失败**：增强 fallback marker + skipped 状态

修复后的预期效果：
- create 成功率显著提升（el-select 字段正确处理）
- update/delete 在无 marker 时被标记为 "skipped"（而非进入重试循环）
- dropdown 按钮在无 marker 时被标记为 "skipped"
- playbook 包含更多有效操作，Stage 2 能捕获更多 API

**实施优先级**：问题 1 > 问题 2 > 问题 3 > 问题 4

**预计工作量**：
- Phase 1（问题 1）：2 小时
- Phase 2（问题 2）：1 小时
- Phase 3（问题 3）：1.5 小时
- Phase 4（问题 4）：0.5 小时
- Phase 5（端到端验证）：1 小时

**总计**：约 6 小时