# Stage 1 Locator 传递修复总结

## 问题描述
Stage 1 探测成功后，playbook 中保存的 `playwright_locator` 不是 Stage 1 实际使用的 locator，而是重新构建的。导致 Stage 2 回放时无法匹配到正确的元素。

## 根本原因
1. **`_click_button_escalating()` 返回完整信息但未被充分利用**
   - 返回 `{"locator": "已验证的locator", "tag": "...", "text": "..."}`
   - 但调用方经常忽略 `locator` 字段，重新用 `tag + text` 构建

2. **`submit_form_v2()` 返回格式不利于传递**
   - 旧格式：`"submitted:按钮文本"`
   - 无法携带已验证的 locator 信息
   - 调用方需要用文本重新构建 locator

3. **特殊场景下 locator 丢失**
   - XPath 拆字匹配（"确 定"）：返回的 locator 是 XPath，但被重新构建为 CSS
   - JS 去空格匹配：返回的是去空格后的文本，但被重新构建为原始带空格文本
   - 对话框内的按钮：需要添加 dialog scope，但重建时可能遗漏

## 修复方案

### 1. 修改 `submit_form_v2()` 返回值 (form_filler.py:581)
**旧格式：**
```python
return "submitted:确定"
```

**新格式：**
```python
return {
    "text": "确 定",  # 原始文本（保留空格）
    "locator": "button:has-text('确定')"  # 已验证的 locator
}
```

**修改内容：**
- 返回 dict 而非字符串
- 包含原始文本和已验证的 locator
- 所有点击策略（playwright/xpath/js/force/coord）都返回已验证的 locator

### 2. 修改 `_do_create()` (discover_ui.py:2221-2292)
**旧代码：**
```python
submit_result = await form_filler.submit_form_v2()
if submit_result == "not_found":
    # ...

submit_text = submit_result.replace("submitted:", "").strip()
submit_text_normalized = " ".join(submit_text.split())
# ... 后续用 submit_text 重新构建 locator
if dialog_detected:
    submit_locator = f".el-dialog__footer button:has-text('{submit_text}')"
```

**新代码：**
```python
submit_result = await form_filler.submit_form_v2()
if not submit_result.get("locator"):
    # ...

submit_text = submit_result["text"]
submit_text_normalized = " ".join(submit_text.split())
submit_locator = submit_result["locator"]  # 直接使用已验证的 locator

# Dialog 模式下添加 scope
if dialog_detected and not submit_locator.startswith(".el-dialog"):
    submit_locator = f".el-dialog__footer {submit_locator}"
```

**关键改进：**
- 直接使用 `submit_result["locator"]`，不重新构建
- 只在必要时添加 dialog scope

### 3. 修改 `_do_edit()` (discover_ui.py:2703-2735)
**同样的修改模式：**
- 使用 `submit_result["locator"]` 而非重新构建
- 使用 `btn_click_result["locator"]` 作为 `trigger_locator_verified`

### 4. 修改 `_do_query()` (discover_ui.py:2474-2480)
**旧代码：**
```python
trigger_tag = btn_click_result.get("tag", "button")
trigger_locator = f"{trigger_tag}:has-text('{trigger_text_normalized}')"
# ...
"trigger_locator_verified": trigger_locator,
```

**新代码：**
```python
# 使用 _click_button_escalating 返回的实际 locator（已验证）
trigger_locator = btn_click_result.get("locator")
if not trigger_locator:
    trigger_tag = btn_click_result.get("tag", "button")
    trigger_locator = f"{trigger_tag}:has-text('{trigger_text_normalized}')"
# ...
"trigger_locator_verified": trigger_locator,
```

**关键改进：**
- 优先使用 `btn_click_result["locator"]`（已验证）
- 仅在 fallback 时才重新构建

### 5. 其他函数的修复
- `_do_delete()` (2890): 使用 `btn_click_result["locator"]`
- `_do_generic_operation()` (3326): 使用 `btn_click_result["locator"]`
- `_do_detail()` (2514): 使用 `btn_click_result["locator"]`

## 修复覆盖的场景

### ✅ XPath 拆字匹配
**场景：** 按钮文本 "确 定"（带空格）
**Stage 1：** XPath 匹配成功，返回 `locator: "xpath=//button[contains(.,'确') and contains(.,'定')]"`
**修复前：** 重新构建 `button:has-text('确 定')` → Stage 2 失败
**修复后：** 使用 `xpath=//button[...]` → Stage 2 成功

### ✅ JS 去空格匹配
**场景：** 按钮文本 "确 定"（带空格）
**Stage 1：** JS 去空格后匹配，返回 `locator: "button:has-text('确定')"`
**修复前：** 重新构建 `button:has-text('确 定')` → Stage 2 失败
**修复后：** 使用 `button:has-text('确定')` → Stage 2 成功

### ✅ 对话框内的提交按钮
**场景：** Dialog 内的 "确定" 按钮
**Stage 1：** 点击成功，返回 `locator: "button:has-text('确定')"`
**修复前：** 重新构建 `button:has-text('确 定')`（可能带空格） → Stage 2 失败
**修复后：** 使用 `button:has-text('确定')` + dialog scope → Stage 2 成功

### ✅ Force click / Coord click
**场景：** 需要强制点击或坐标点击
**Stage 1：** 特殊策略成功，返回对应的 locator
**修复前：** 忽略特殊策略的 locator，重新构建标准 CSS → Stage 2 失败
**修复后：** 使用特殊策略的 locator → Stage 2 成功

## 验证方法
1. 运行 Stage 1：`python -m module_discovery.run --stage 1 --project ecm-compute --module 用户管理 --headless`
2. 检查生成的 playbook.json：
   - `trigger_locator_verified` 应该是 Stage 1 实际使用的 locator
   - `submit_locator_verified` 应该是 Stage 1 实际使用的 locator
   - 不应有重新构建的 `button:has-text('...')` 格式（除非是 fallback）

## 修改文件清单
1. `module_discovery/replay/form_filler.py` - 修改 `submit_form_v2()` 返回值
2. `module_discovery/discover_ui.py` - 修改所有 `_do_*()` 函数使用已验证的 locator

## 测试建议
- 测试带空格按钮文本的场景（"确 定"、"保 存"）
- 测试 Dialog 内按钮的场景
- 测试需要 XPath/JS/Force/Coord 策略的场景
- 验证 Stage 2 回放成功率
