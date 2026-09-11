# Stage 1 Playbook Locator 修正方案

## 问题描述

当前 Stage 1 生成的 playbook 中记录的 locator 是"理想化"的，而非"实际操作成功"的。这导致 Stage 2 在执行时需要大量补丁逻辑来处理各种边界情况。

### 核心矛盾

| 阶段 | 实际行为 | 问题 |
|------|----------|------|
| Stage 1 探测 | 发现按钮文本 `"批 量迁移"`（有空格），用 XPath 拆字符匹配成功 | playbook 记录 `button:has-text('批量迁移')` — 理想化 locator，回放时匹配不到 |
| Stage 1 验证 create | create 失败 → fallback marker 从表格提取 | playbook 仍记录 create 的"理想步骤"，不知道这个操作实际上会失败 |
| Stage 1 验证 lock/unlock | 行操作在"更多"下拉菜单中 | playbook 可能记录了不正确的 locator |
| Stage 2 回放 | 需要重新发现按钮、处理空格、JS click 回退 | Stage 2 职责混乱，变成了"重新探测"而非"纯回放" |

### 设计原则

**Stage 2 的职责是纯回放，不应该做探测**。playbook 应该忠实记录"什么真正有效"，而不是"理论上应该能匹配"。

---

## 现状分析

### 1. Playbook Locator 写入入口清单

#### 1.1 `build_playbook` 主函数 (discover_ui.py:4638-4728)

**位置**：`module_discovery/discover_ui.py` 第 4638-4728 行

**逻辑**：
```python
for action, op_data in validated_operations.items():
    is_success = op_data.get("success", False)
    error_type = op_data.get("error_type", "")
    
    # 根据操作类型分发到不同的 step builder
    if action == "create":
        op_steps.extend(_build_create_steps(op_data))
    elif action in ("lock", "unlock", "reset", "freeze", "thaw"):
        op_steps.extend(_build_dropdown_steps(op_data))
    # ... 其他操作类型
```

**问题**：
- 无论 `is_success` 是 True 还是 False，都会生成步骤
- 失败的操作也会被写入 playbook，但 locator 是"理想化"的（探测阶段生成的，未经验证）

#### 1.2 `_build_create_steps` (discover_ui.py:4731-4843)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `click_button` (触发按钮) | `op_data.get("trigger_locator_verified") or op_data.get("trigger_locator")` | `trigger_locator` 是探测阶段生成的，未经验证；`trigger_locator_verified` 只在成功时设置 |
| `fill_form` (表单字段) | `field.get("selector")` | 来自 DOM 扫描的 `_buildSelector`，未经验证是否能成功填充 |
| `click_button` (提交按钮) | `op_data.get("submit_locator_verified") or op_data.get("submit_locator")` | 同上，`submit_locator` 未经验证 |
| `assert_success` | 硬编码 `".el-message--success"` | 假设所有系统都使用 Element UI 的成功消息，未验证 |

**关键代码**：
```python
# Line 4737-4743
trigger_locator = op_data.get("trigger_locator_verified") or op_data.get("trigger_locator")
if trigger_locator:
    steps.append({
        "action": "click_button",
        "playwright_locator": trigger_locator,
        "description": "点击创建按钮"
    })
```

#### 1.3 `_build_dropdown_steps` (discover_ui.py:4845-4890)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `find_row` | 无 locator，依赖运行时标记查找 | 正确，无需修改 |
| `click_row_more` | `op_data.get("dropdown_item_text")` | 只记录文本，不记录实际点击路径 |
| `confirm_dialog` | 无 locator | 依赖 Stage 2 运行时重新发现确认按钮 |
| `assert_success` | 硬编码 `".el-message--success"` | 未验证 |

**关键代码**：
```python
# Line 4856-4863
if op_data.get("is_dropdown_operation"):
    item_text = op_data.get("dropdown_item_text")
    if item_text:
        steps.append({
            "action": "click_row_more",
            "item_text": item_text,  # 只记录文本，不记录实际点击路径
            "description": f"点击更多菜单项: {item_text}"
        })
```

#### 1.4 `_build_delete_steps` (discover_ui.py:4892-4949)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `find_row` | 无 locator | 正确 |
| `select_row_checkbox` | `op_data.get("checkbox_locator", ".el-checkbox__input")` | 默认值 `.el-checkbox__input` 可能不正确 |
| `click_button` | `trigger_locator_verified` or fallback `button:has-text('{trigger}')` | fallback 是理想化的 |
| `confirm_dialog` | 无 locator | 依赖运行时重新发现 |

#### 1.5 `_build_update_steps` (discover_ui.py:4951-5088)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `find_row` | 无 locator | 正确 |
| `click_row_button` | `op_data.get("trigger_locator_verified")` + `button_text` | `playwright_locator` 字段未使用，Stage 2 依赖 `button_text` 重新查找 |
| `click_button` (全局按钮) | `trigger_locator_verified` or fallback | fallback 是理想化的 |
| `fill_form` | 同 create | 未经验证 |
| `click_button` (提交) | 同 create | 未经验证 |

#### 1.6 `_build_query_steps` (discover_ui.py:5090-5161)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `fill_input` | `search_input.get("locator")` | 来自 DOM 扫描，未验证是否能成功输入 |
| `click_button` | `trigger_locator_verified` or fallback `button:has-text('{trigger_text}')` | fallback 是理想化的 |
| `wait_for_table_ready` | 无 locator | 正确 |

#### 1.7 `_build_generic_steps` (discover_ui.py:5163-5220)

**写入的 locator**：

| 步骤 | Locator 来源 | 问题 |
|------|-------------|------|
| `find_row` | 无 locator | 正确 |
| `click_button` | `trigger_locator_verified` or fallback | fallback 是理想化的 |
| `fill_form` | 同 create | 未经验证 |
| `click_button` (提交) | 同 create | 未经验证 |

---

### 2. Stage 1 实际点击逻辑 vs Playbook 记录

#### 2.1 `_click_button_escalating` (discover_ui.py:3431-3530)

**实际使用的策略**（5 级升级）：

```python
# Strategy 1: Playwright CSS click with safe_css
locator = safe_css(f'button:has-text("{btn_text}"), span:has-text("{btn_text}"), a:has-text("{btn_text}")')
await page.click(locator, timeout=5000)
tag_reported = "button"  # 硬编码，无论实际匹配到的是什么

# Strategy 2: XPath character-split (处理空格)
xpath = f"//button[{_chars_all(btn_text)} and {_hidden_filter()}]"
await page.click(f"xpath={xpath}", timeout=5000)
tag_reported = "button"  # 硬编码

# Strategy 3: JS whitespace-normalized click
js = """(text) => {
    const normalize = s => s.replace(/\\s+/g, '');
    const targetNorm = normalize(text);
    const candidates = document.querySelectorAll('button, span, a, .el-button');
    for (const el of candidates) {
        if (normalize(el.textContent).includes(targetNorm)) {
            el.click();
            return el.tagName.toLowerCase();  // 返回实际标签
        }
    }
    return null;
}"""
tag_reported = await page.evaluate(js, btn_text)  # 实际标签

# Strategy 4: Force click
await page.click(locator, force=True, timeout=5000)
tag_reported = "button"  # 硬编码

# Strategy 5: Coordinate click
# ... 返回实际标签
```

**问题**：
- 策略 1、2、4 硬编码 `tag="button"`，即使实际匹配到的是 `span` 或 `a`
- Playbook 记录的 `trigger_locator` 使用这个错误的 tag

#### 2.2 `confirm_dialog` (button_driver.py:806-1012)

**实际使用的策略**（3 种对话框类型 × 多级升级）：

```python
# 检测对话框类型
container_type = await _detect_dialog_container(page)  # message-box | popconfirm | generic

# 根据类型选择策略
if container_type == "message-box":
    # 策略 1: CSS primary button
    locator = ".el-message-box__btns button.el-button--primary:not(.is-disabled)"
    # 策略 2: Character-split XPath
    xpath = f"//button[{_chars_all('确定')} and {_hidden_filter()}]"
    
elif container_type == "popconfirm":
    # 策略 1: CSS primary button in action area
    locator = ".el-popconfirm__action button.el-button--primary"
    # 策略 2: Character-split XPath
    
elif container_type == "generic":
    # 策略 1: Character-split XPath (global)
    # 策略 2: CSS has-text with hidden filter
    # 策略 3: JS whitespace-normalized click
```

**问题**：
- Playbook 的 `confirm_dialog` 步骤不记录任何 locator
- Stage 2 必须重新执行整个检测流程

#### 2.3 `click_row_more_item` (button_driver.py:293-427)

**实际使用的策略**（3 级升级）：

```python
# Strategy 1: Hover + dispatch event
await more_btn.hover()
await more_btn.evaluate("""el => {
    el.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
    el.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
}""")

# Strategy 2: Click
await more_btn.click()

# Strategy 3: Vue instance direct call
await more_btn.evaluate("""el => {
    let vue = el.__vue__;
    while (vue && !vue.show) vue = vue.$parent;
    if (vue && vue.show) { vue.show(); return true; }
    return false;
}""")
```

**问题**：
- Playbook 只记录 `item_text`，不记录展开下拉菜单的策略
- Stage 2 必须重新尝试所有策略

---

### 3. Stage 2 补丁逻辑清单

#### 3.1 可删除的补丁（Stage 1 修正后可移除）

| 补丁 | 文件 | 行号 | 描述 | 依赖原因 |
|------|------|------|------|----------|
| **空格容错** | replay_engine.py | 153-176 | `_step_click_button` 中的空格归一化 fallback | Stage 1 记录的 locator 包含空格，但实际匹配时去掉了空格 |
| **Hidden filter 注入** | replay_engine.py | 145-148 | `_step_click_button` 调用 `safe_css` 添加隐藏过滤 | Stage 1 记录的 locator 不包含 `:not(.is-hidden)` 等过滤 |
| **通用 hidden filter** | locator_helpers.py | 全文 | `safe_css` / `safe_xpath` 函数 | Stage 1 所有 locator 都缺少隐藏过滤 |
| **确认按钮重新发现** | button_driver.py | 806-1012 | `confirm_dialog` 整个函数 | Stage 1 不记录确认按钮的 locator |
| **字符拆分匹配** | button_driver.py | 827-838 | `_chars_all` / `_js_chars_all` | Stage 1 记录的按钮文本包含空格 |
| **下拉菜单展开策略** | button_driver.py | 293-427 | `click_row_more_item` 的 3 级升级 | Stage 1 不记录展开策略 |

#### 3.2 不可删除的补丁（Element UI + Playwright 不兼容）

| 补丁 | 文件 | 行号 | 描述 | 原因 |
|------|------|------|------|------|
| **行按钮 JS click** | replay_engine.py | 330-368 | `_step_click_row_button` 使用 JS click | Element UI 固定列表格的 Playwright 可见性检查失败 |
| **Checkbox JS click** | replay_engine.py | 500-528 | `_step_select_row_checkbox` 使用 JS click | 同上，checkbox 在隐藏占位符中 |
| **行按钮 Playwright-to-JS fallback** | button_driver.py | 670-705 | `click_row_button` 的 fallback | 同上 |
| **下拉选项可见性预检** | button_driver.py | 743-803 | `click_dropdown_option` 的可见性检查 | 下拉菜单动画时序问题 |

#### 3.3 操作韧性补丁（非 locator 问题）

| 补丁 | 文件 | 行号 | 描述 | 原因 |
|------|------|------|------|------|
| **操作后清理** | capture_apis.py | 122-152 | `_cleanup_after_operation` | 处理操作失败后的残留对话框，不是 locator 问题 |
| **生成脚本清理** | generate_ui_script.py | 271-300 | 生成脚本中的 `_cleanup_dialogs` | 同上，是运行时韧性需求 |

---

## 修正方案

### 方案 A：Stage 1 记录"实际成功的 locator"（推荐）

#### 核心思路

Stage 1 验证操作成功后，把**实际成功的定位方式**回写到 playbook 的步骤中。

#### 修改清单

##### 1. `_do_create` 返回值修正 (discover_ui.py:2121-2335)

**当前代码**：
```python
# Line 2311-2315
return {
    "success": True,
    "trigger_locator_verified": f"{trigger_tag}:has-text('{trigger_text_normalized}')",
    "trigger_locator": trigger_locator,  # 探测阶段的，未经验证
    # ...
}
```

**修正**：
```python
# 只保留已验证的 locator，删除未经验证的
return {
    "success": True,
    "trigger_locator_verified": f"{trigger_tag}:has-text('{trigger_text_normalized}')",
    # 删除 "trigger_locator" 字段
    "submit_locator_verified": submit_locator,  # 实际点击成功的
    # 删除 "submit_locator" 字段
    "form_fields_verified": verified_fields,  # 实际填充成功的字段
    # 删除 "form_fields" 字段
    # ...
}
```

**影响范围**：
- `_build_create_steps` 需要修改，不再 fallback 到未经验证的 locator
- 如果 `trigger_locator_verified` 不存在，说明操作失败，不应生成步骤

##### 2. `_build_create_steps` 修正 (discover_ui.py:4731-4843)

**当前代码**：
```python
# Line 4737
trigger_locator = op_data.get("trigger_locator_verified") or op_data.get("trigger_locator")
```

**修正**：
```python
trigger_locator = op_data.get("trigger_locator_verified")
if not trigger_locator:
    # 操作失败，不生成步骤
    return []
```

**同理修改**：
- Line 4767-4770: `form_fields` → `form_fields_verified`
- Line 4824: `submit_locator` → `submit_locator_verified`

##### 3. `_click_button_escalating` 返回值修正 (discover_ui.py:3431-3530)

**当前代码**：
```python
# Strategy 1
tag_reported = "button"  # 硬编码

# Strategy 2
tag_reported = "button"  # 硬编码
```

**修正**：
```python
# Strategy 1
# 实际匹配到的元素标签
matched_tag = await page.evaluate("""(locator) => {
    const el = document.querySelector(locator);
    return el ? el.tagName.toLowerCase() : null;
}""", locator)
tag_reported = matched_tag or "button"

# Strategy 2
# XPath 匹配的也需要获取实际标签
matched_tag = await page.evaluate("""(xpath) => {
    const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
    return result.singleNodeValue ? result.singleNodeValue.tagName.toLowerCase() : null;
}""", xpath)
tag_reported = matched_tag or "button"
```

##### 4. `confirm_dialog` 步骤增强 (discover_ui.py:4868-4871, 4929-4932, 5191-5194)

**当前代码**：
```python
steps.append({
    "action": "confirm_dialog",
    "description": "点击确认对话框"
})
```

**修正**：
```python
# 在 _do_generic_operation 等函数中记录确认按钮的实际 locator
confirm_result = await confirm_dialog(page)
if confirm_result:
    steps.append({
        "action": "confirm_dialog",
        "confirm_locator_verified": confirm_result.get("locator"),  # 实际点击成功的 locator
        "container_type_verified": confirm_result.get("container_type"),  # message-box | popconfirm | generic
        "description": "点击确认对话框"
    })
```

**需要修改 `confirm_dialog` 函数**（button_driver.py:806-1012）：
```python
# 返回实际使用的 locator 和容器类型
return {
    "success": True,
    "locator": actual_locator,  # 实际点击成功的
    "container_type": container_type,  # message-box | popconfirm | generic
    "strategy": strategy_used,  # css-primary | xpath-charsplit | js-normalized
}
```

##### 5. `_build_dropdown_steps` 增强 (discover_ui.py:4845-4890)

**当前代码**：
```python
steps.append({
    "action": "click_row_more",
    "item_text": item_text,
    "description": f"点击更多菜单项: {item_text}"
})
```

**修正**：
```python
# 在 _do_generic_operation 中记录展开策略
expand_result = await click_row_more_item(row, item_text)
if expand_result:
    steps.append({
        "action": "click_row_more",
        "item_text": item_text,
        "expand_strategy_verified": expand_result.get("strategy"),  # hover | click | vue-instance
        "more_button_locator_verified": expand_result.get("more_button_locator"),  # "更多"按钮的 locator
        "description": f"点击更多菜单项: {item_text}"
    })
```

**需要修改 `click_row_more_item` 函数**（button_driver.py:293-427）：
```python
# 返回实际使用的策略
return {
    "success": True,
    "strategy": strategy_used,  # hover | click | vue-instance
    "more_button_locator": more_btn_locator,  # "更多"按钮的 locator
}
```

##### 6. 表单字段验证增强

**当前问题**：`form_fields` 来自 DOM 扫描，未验证是否能成功填充。

**修正**：在 `_do_create` / `_do_edit` 中，记录实际填充成功的字段：

```python
verified_fields = []
for field in form_fields:
    selector = field.get("selector")
    fill_result = await _fill_field(page, selector, fill_rule)
    if fill_result.get("success"):
        verified_fields.append({
            **field,
            "selector_verified": selector,  # 实际填充成功的 selector
            "fill_strategy": fill_result.get("strategy"),  # playwright-fill | js-fill | multi-step
        })

return {
    "success": True,
    "form_fields_verified": verified_fields,
    # 删除 "form_fields"
}
```

##### 7. `build_playbook` 主函数修正 (discover_ui.py:4638-4728)

**当前代码**：
```python
for action, op_data in validated_operations.items():
    is_success = op_data.get("success", False)
    error_type = op_data.get("error_type", "")
    
    # 无论成功失败都生成步骤
    if action == "create":
        op_steps.extend(_build_create_steps(op_data))
```

**修正**：
```python
for action, op_data in validated_operations.items():
    is_success = op_data.get("success", False)
    error_type = op_data.get("error_type", "")
    
    # 只生成成功操作的步骤
    if not is_success:
        continue
    
    if action == "create":
        op_steps.extend(_build_create_steps(op_data))
```

**影响**：
- 失败的操作不会被写入 playbook
- Stage 2 不需要处理失败操作的步骤

##### 8. 所有 `_build_*_steps` 函数统一修正

**统一模式**：
```python
# 删除 fallback 到未经验证的 locator
# 当前：
trigger_locator = op_data.get("trigger_locator_verified") or op_data.get("trigger_locator")

# 修正：
trigger_locator = op_data.get("trigger_locator_verified")
if not trigger_locator:
    return []  # 操作失败，不生成步骤
```

**需要修改的函数**：
- `_build_create_steps` (line 4737, 4824)
- `_build_delete_steps` (line 4915-4917)
- `_build_update_steps` (line 4979-4981)
- `_build_query_steps` (line 5113, 5139-5152)
- `_build_generic_steps` (line 5173-5180)

---

### 方案 B：Stage 1 的 `validated_operations` 标记更精确的 status

#### 核心思路

增加更细粒度的状态，比如 `partial`（create 表单能填但提交失败，后续操作用了 fallback marker）。Stage 2 根据这个状态决定是否跳过某些步骤。

#### 修改清单

##### 1. 扩展 status 枚举

```python
# 当前：success | failed
# 修正：success | partial | failed

# partial 的含义：
# - create 表单能填但提交失败
# - 后续操作用了 fallback marker
# - 某些步骤成功，某些失败
```

##### 2. `_do_create` 返回 partial 状态

```python
if submit_failed:
    return {
        "success": False,
        "status": "partial",  # 部分成功
        "marker": extracted_marker,  # 从表单中提取的 marker
        "trigger_locator_verified": trigger_locator,
        "form_fields_verified": verified_fields,
        "error_type": "submit_failed",
    }
```

##### 3. Stage 2 根据 status 跳过步骤

```python
# capture_apis.py
if op.get("status") == "partial":
    # 跳过 assert_success 步骤
    # 但仍然执行其他步骤（用于捕获 API）
    skip_assert = True
```

#### 评估

**优点**：
- Stage 1 仍然记录"理想化"的 locator，但标记哪些步骤成功
- Stage 2 可以根据标记跳过失败的步骤

**缺点**：
- Stage 2 仍然需要处理"理想化"的 locator，补丁逻辑无法完全删除
- 增加了 Stage 2 的复杂度

**结论**：不推荐，因为无法解决 Stage 2 补丁逻辑混乱的问题。

---

## 推荐方案：方案 A

### 理由

1. **Stage 2 不需要理解 fallback 逻辑** — 它只需要执行正确的 locator
2. **Playbook 就是 Stage 1 的"操作说明书"** — 说明书应该记录真正有效的方法
3. **不增加 Stage 2 的职责边界** — Stage 2 仍然是纯回放
4. **可以删除 Stage 2 的补丁逻辑** — 简化代码，减少维护成本

---

## 实施计划

### Phase 1: 修正 Stage 1 返回值（3-4 小时）

#### 1.1 修正 `_click_button_escalating` 返回值

**文件**：`module_discovery/discover_ui.py`
**行号**：3431-3530

**修改内容**：
- 策略 1、2、4 返回实际匹配到的标签，而不是硬编码 `"button"`
- 返回结构：`{"success": True, "tag": "span", "locator": "...", "strategy": "css"}`

#### 1.2 修正 `confirm_dialog` 返回值

**文件**：`module_discovery/replay/button_driver.py`
**行号**：806-1012

**修改内容**：
- 返回实际使用的 locator 和容器类型
- 返回结构：`{"success": True, "locator": "...", "container_type": "message-box", "strategy": "css-primary"}`

#### 1.3 修正 `click_row_more_item` 返回值

**文件**：`module_discovery/replay/button_driver.py`
**行号**：293-427

**修改内容**：
- 返回实际使用的展开策略
- 返回结构：`{"success": True, "strategy": "hover", "more_button_locator": "..."}`

#### 1.4 修正 `_do_create` / `_do_edit` 返回值

**文件**：`module_discovery/discover_ui.py`
**行号**：2121-2335, 2620-2781

**修改内容**：
- 只返回 `_verified` 字段，删除未经验证的字段
- 返回结构：`{"success": True, "trigger_locator_verified": "...", "submit_locator_verified": "...", "form_fields_verified": [...]}`

#### 1.5 修正 `_do_generic_operation` 返回值

**文件**：`module_discovery/discover_ui.py`
**行号**：2912-3335

**修改内容**：
- 记录确认按钮的实际 locator
- 记录展开下拉菜单的策略

### Phase 2: 修正 Playbook 构建函数（2-3 小时）

#### 2.1 修正 `build_playbook` 主函数

**文件**：`module_discovery/discover_ui.py`
**行号**：4638-4728

**修改内容**：
- 只生成成功操作的步骤（`if not is_success: continue`）

#### 2.2 修正所有 `_build_*_steps` 函数

**文件**：`module_discovery/discover_ui.py`
**行号**：4731-5220

**修改内容**：
- 删除 fallback 到未经验证的 locator
- 如果 `_verified` 字段不存在，返回空步骤列表
- 使用 `_verified` 字段替代原字段

**具体修改**：
- `_build_create_steps`: line 4737, 4767, 4824
- `_build_dropdown_steps`: line 4856-4863（增强，记录展开策略）, line 4868-4871（增强，记录确认按钮 locator）
- `_build_delete_steps`: line 4915-4917, line 4929-4932（增强）
- `_build_update_steps`: line 4969-4981, line 5074
- `_build_query_steps`: line 5113, 5139-5152
- `_build_generic_steps`: line 5173-5220

### Phase 3: 删除 Stage 2 补丁逻辑（1-2 小时）

#### 3.1 删除空格容错

**文件**：`module_discovery/replay/replay_engine.py`
**行号**：153-176

**修改内容**：
- 删除 `_step_click_button` 中的空格归一化 fallback
- 原因：Stage 1 记录的 locator 已经是归一化后的

#### 3.2 删除 hidden filter 注入

**文件**：`module_discovery/replay/replay_engine.py`
**行号**：145-148

**修改内容**：
- 删除 `_step_click_button` 中的 `safe_css` 调用
- 原因：Stage 1 记录的 locator 已经包含 hidden filter

**文件**：`module_discovery/replay/locator_helpers.py`
**修改内容**：
- 简化 `safe_css` / `safe_xpath`，不再添加 hidden filter
- 或者删除这两个函数（如果不再需要）

#### 3.3 简化 `confirm_dialog`

**文件**：`module_discovery/replay/button_driver.py`
**行号**：806-1012

**修改内容**：
- 如果 playbook 提供了 `confirm_locator_verified`，直接使用，不再重新发现
- 保留原有的重新发现逻辑作为 fallback（用于向后兼容旧的 playbook）

#### 3.4 简化 `click_row_more_item`

**文件**：`module_discovery/replay/button_driver.py`
**行号**：293-427

**修改内容**：
- 如果 playbook 提供了 `expand_strategy_verified`，直接使用，不再尝试所有策略
- 保留原有的多策略尝试作为 fallback

### Phase 4: 测试验证（2-3 小时）

#### 4.1 Stage 1 测试

**测试用例**：
1. 运行 Stage 1，生成 playbook
2. 检查 playbook 中的 locator 是否包含空格（应该不包含）
3. 检查 playbook 中的 `confirm_dialog` 步骤是否包含 `confirm_locator_verified`
4. 检查 playbook 中的 `click_row_more` 步骤是否包含 `expand_strategy_verified`
5. 检查失败的操作是否被排除在 playbook 之外

#### 4.2 Stage 2 测试

**测试用例**：
1. 使用新的 playbook 运行 Stage 2
2. 验证 Stage 2 不再触发空格容错逻辑
3. 验证 Stage 2 不再触发 hidden filter 注入
4. 验证 Stage 2 不再重新发现确认按钮
5. 验证 Stage 2 能够成功回放所有操作

#### 4.3 回归测试

**测试用例**：
1. 使用旧的 playbook 运行 Stage 2（向后兼容）
2. 验证 Stage 2 的 fallback 逻辑仍然有效
3. 验证其他模块的 playbook 仍然可以正常回放

---

## 风险评估

### 1. 向后兼容性风险

**风险**：旧的 playbook 不包含 `_verified` 字段，Stage 2 可能无法回放。

**缓解措施**：
- Stage 2 的补丁逻辑保留为 fallback，不立即删除
- 在 Phase 3 中，先简化补丁逻辑，保留 fallback
- 在 Phase 4 中，验证旧的 playbook 仍然可以回放

### 2. Stage 1 失败率上升风险

**风险**：如果 Stage 1 的验证逻辑有 bug，可能导致更多操作被标记为失败，playbook 中没有步骤。

**缓解措施**：
- 在 Phase 1 中，仔细测试 `_click_button_escalating` / `confirm_dialog` / `click_row_more_item` 的返回值
- 在 Phase 4 中，验证 Stage 1 的成功率没有下降

### 3. 交叉影响风险

**风险**：修改 `_click_button_escalating` 的返回值可能影响其他调用者。

**缓解措施**：
- 搜索所有调用 `_click_button_escalating` 的地方，验证它们能够处理新的返回值
- 在 Phase 1 中，逐个修改并测试

### 4. 表单字段验证风险

**风险**：`form_fields_verified` 可能漏掉某些字段，导致表单填充不完整。

**缓解措施**：
- 在 Phase 1.4 中，仔细验证所有字段都被记录
- 在 Phase 4 中，验证表单填充的成功率

---

## 交叉影响分析

### 1. 修改点之间的依赖关系

```
_click_button_escalating (Phase 1.1)
    ↓
_do_create / _do_edit (Phase 1.4)
    ↓
_build_create_steps / _build_update_steps (Phase 2.2)
    ↓
Stage 2 空格容错删除 (Phase 3.1)

confirm_dialog (Phase 1.2)
    ↓
_do_generic_operation (Phase 1.5)
    ↓
_build_dropdown_steps / _build_delete_steps (Phase 2.2)
    ↓
Stage 2 confirm_dialog 简化 (Phase 3.3)

click_row_more_item (Phase 1.3)
    ↓
_do_generic_operation (Phase 1.5)
    ↓
_build_dropdown_steps (Phase 2.2)
    ↓
Stage 2 click_row_more_item 简化 (Phase 3.4)
```

### 2. 修改顺序建议

1. **Phase 1.1 → 1.4 → 1.5**：先修正按钮点击，再修正操作返回值
2. **Phase 1.2 → 1.5**：修正确认对话框，再修正操作返回值
3. **Phase 1.3 → 1.5**：修正下拉菜单，再修正操作返回值
4. **Phase 2.1 → 2.2**：先修正主函数，再修正子函数
5. **Phase 3.1 → 3.2 → 3.3 → 3.4**：按依赖顺序删除补丁

### 3. 可能的遗漏点

#### 3.1 `_do_query` 返回值

**文件**：`module_discovery/discover_ui.py`
**行号**：2423-2489

**检查**：`_do_query` 是否也使用了 `_click_button_escalating`？如果是，需要同步修正。

#### 3.2 `_do_delete` 返回值

**文件**：`module_discovery/discover_ui.py`
**行号**：2784-2909

**检查**：`_do_delete` 是否也使用了 `confirm_dialog`？如果是，需要同步修正。

#### 3.3 其他操作类型

**检查**：除了 create/update/delete/query/lock/unlock/reset，还有哪些操作类型需要修正？

**答案**：
- `import` / `export` / `authorize` / `migrate` / `detail` 等都走 `_do_generic_operation`，已经在 Phase 1.5 中覆盖

#### 3.4 表单字段的其他来源

**检查**：除了 `_do_create` / `_do_edit`，还有哪些地方生成 `form_fields`？

**答案**：
- `_do_generic_operation` 也会生成 `form_fields`，需要在 Phase 1.5 中同步修正

---

## 实施检查清单

### Phase 1 检查清单

- [ ] 1.1 `_click_button_escalating` 返回实际标签
- [ ] 1.2 `confirm_dialog` 返回实际 locator 和容器类型
- [ ] 1.3 `click_row_more_item` 返回展开策略
- [ ] 1.4 `_do_create` / `_do_edit` 只返回 `_verified` 字段
- [ ] 1.5 `_do_generic_operation` 记录确认按钮和展开策略

### Phase 2 检查清单

- [ ] 2.1 `build_playbook` 只生成成功操作的步骤
- [ ] 2.2 `_build_create_steps` 删除 fallback
- [ ] 2.2 `_build_dropdown_steps` 增强，记录展开策略和确认按钮
- [ ] 2.2 `_build_delete_steps` 删除 fallback，增强确认按钮
- [ ] 2.2 `_build_update_steps` 删除 fallback
- [ ] 2.2 `_build_query_steps` 删除 fallback
- [ ] 2.2 `_build_generic_steps` 删除 fallback

### Phase 3 检查清单

- [ ] 3.1 删除 `_step_click_button` 空格容错
- [ ] 3.2 删除 `_step_click_button` hidden filter 注入
- [ ] 3.2 简化 `safe_css` / `safe_xpath`
- [ ] 3.3 简化 `confirm_dialog`，保留 fallback
- [ ] 3.4 简化 `click_row_more_item`，保留 fallback

### Phase 4 检查清单

- [ ] 4.1 Stage 1 生成的 playbook 不包含空格
- [ ] 4.1 Stage 1 生成的 playbook 包含 `_verified` 字段
- [ ] 4.1 Stage 1 失败操作被排除
- [ ] 4.2 Stage 2 不再触发空格容错
- [ ] 4.2 Stage 2 不再触发 hidden filter 注入
- [ ] 4.2 Stage 2 能够成功回放
- [ ] 4.3 旧的 playbook 仍然可以回放（向后兼容）
- [ ] 4.3 其他模块的 playbook 仍然可以回放

---

## 附录：代码位置索引

### discover_ui.py

- `_click_button_escalating`: 3431-3530
- `_do_create`: 2121-2335
- `_do_edit`: 2620-2781
- `_do_delete`: 2784-2909
- `_do_query`: 2423-2489
- `_do_generic_operation`: 2912-3335
- `build_playbook`: 4638-4728
- `_build_create_steps`: 4731-4843
- `_build_dropdown_steps`: 4845-4890
- `_build_delete_steps`: 4892-4949
- `_build_update_steps`: 4951-5088
- `_build_query_steps`: 5090-5161
- `_build_generic_steps`: 5163-5220

### button_driver.py

- `click_row_more_item`: 293-427
- `confirm_dialog`: 806-1012
- `click_row_button`: 670-705
- `click_dropdown_option`: 743-803

### replay_engine.py

- `_step_click_button`: 131-177
- `_step_click_row_button`: 330-368
- `_step_select_row_checkbox`: 500-528
- `_step_confirm_dialog`: 371-393

### locator_helpers.py

- `safe_css`: 全文
- `safe_xpath`: 全文

---

## 总结

本方案通过修正 Stage 1 的返回值和 playbook 构建逻辑，确保 playbook 记录的是"实际操作成功的 locator"，而不是"理想化的 locator"。这样可以删除 Stage 2 的大量补丁逻辑，简化代码，减少维护成本。

**核心修改**：
1. Stage 1 的 `_do_*` 函数只返回 `_verified` 字段
2. Stage 1 的 `_build_*_steps` 函数只使用 `_verified` 字段，删除 fallback
3. Stage 1 的 `build_playbook` 只生成成功操作的步骤
4. Stage 2 的补丁逻辑可以简化或删除

**预期效果**：
- Stage 2 不再需要空格容错、hidden filter 注入等补丁
- Stage 2 的职责更加清晰：纯回放，不探测
- 代码更简洁，维护成本更低
