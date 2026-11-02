# Stage 1 "用户管理" 4 个 Bug 的诊断与修复方案

> 测试项目: estack-ceshiyu (console-estack-ceshiyu.cmecloud.cn)  
> 模块: 用户管理  
> 运行时间: 2026-11-02 09:31 ~ 09:40  
> 最终结果: 10/12 操作验证成功，2 个失败（授权、删除）

---

## 目录

1. [Bug 1&2: 设置到期时间执行 4 次，启用/停止各 2 次](#bug-12-设置到期时间执行-4-次启用停止各-2-次)
2. [Bug 3: 授权操作失败](#bug-3-授权操作失败)
3. [Bug 4: 删除用户未点击确定](#bug-4-删除用户未点击确定)
4. [实施清单](#实施清单)

---

## Bug 1&2: 设置到期时间执行 4 次，启用/停止各 2 次

### 现象

| 时间 | 操作 | 触发方式 | 第几次 |
|------|------|----------|--------|
| 09:34:01 | 启用 | STATE-CHANGE | 第1次 |
| 09:34:27 | 停止 | STATE-CHANGE | 第1次 |
| 09:34:53 | 设置到期时间 | STATE-CHANGE 递归 | 第1次 |
| 09:35:18 | 启用 | STATE-CHANGE 递归 | 第2次 |
| 09:35:42 | 设置到期时间 | STATE-CHANGE 递归 | 第2次 |
| 09:36:06 | 启用 | STATE-CHANGE 递归 | 第3次（边界停止） |
| 09:36:29 | 设置到期时间 | INCREMENTAL-SCAN 新按钮 | 第3次 |
| 09:36:55 | 停止 | 主循环验证 | 第2次 |

### 根因

**文件**: `core/discovery/operation_executor/crud_executor.py`  
**函数**: `_check_and_execute_newly_enabled` (第 518-621 行)  
**核心缺陷**: 循环体内缺少 `executed_actions` 重检查

`newly_enabled` 列表在循环开始前一次性计算完毕（第 564-569 行），但循环体内执行按钮后递归调用自身，递归中可能已经执行了列表中的后续按钮。回到上层后，**没有重新检查 `executed_actions`**，导致重复执行。

**问题代码** (第 563-613 行):

```python
# newly_enabled 在这里一次性计算
newly_enabled = []
for text, is_enabled in current_states.items():
    if is_enabled and text in button_states and not button_states[text]:
        if text not in executed_actions:
            newly_enabled.append(text)

# 循环体中没有重新检查 executed_actions ← BUG
for text in newly_enabled:
    LOG.info(f"{tab_prefix}[STATE-CHANGE] 执行新启用按钮: {text}")
    # ... 执行按钮 ...
    executed_actions.add(text)
    if result and result.get("success"):
        # 递归中可能已执行了 newly_enabled 中的后续按钮
        sub_results = await _check_and_execute_newly_enabled(...)
        results.update(sub_results)
```

**执行链追踪**:

```
depth=5: newly_enabled = ['启用', '设置到期时间']
  → 执行 '启用' → executed_actions.add('启用')
    → 递归 depth=4: newly_enabled = ['停止', '设置到期时间']
      → 执行 '停止' → executed_actions.add('停止')
        → 递归 depth=3: newly_enabled = ['设置到期时间']
          → 执行 '设置到期时间' → executed_actions.add('设置到期时间')
      → depth=4 循环继续: text='设置到期时间'
        → ❌ 没检查 executed_actions → 直接执行 (重复!)
    → depth=5 循环继续: text='设置到期时间'
      → ❌ 没检查 executed_actions → 直接执行 (重复!)
```

### 修复方案

**修改文件**: `core/discovery/operation_executor/crud_executor.py`  
**修改位置**: 第 579 行，`for text in newly_enabled:` 循环体开头  
**修改量**: 加 3 行

**修改前**:
```python
    # 逐一执行
    for text in newly_enabled:
        LOG.info(f"{tab_prefix}[STATE-CHANGE] 执行新启用按钮: {text}")
```

**修改后**:
```python
    # 逐一执行
    for text in newly_enabled:
        # ★ 防止递归中已执行的按钮被重复执行
        if text in executed_actions:
            LOG.info(f"{tab_prefix}[STATE-CHANGE] 跳过已执行按钮: {text}")
            continue
        LOG.info(f"{tab_prefix}[STATE-CHANGE] 执行新启用按钮: {text}")
```

**安全性**: 此修改不影响首次执行的按钮（它们不在 `executed_actions` 中），仅阻止递归链中的重复执行。

---

## Bug 3: 授权操作失败

### 现象

点击"授权"后页面跳转到授权页面，表单字段通过 radio-cascade 成功填充，但 navigation_executor 误判"授权用户"字段为空（必填失败），直接返回错误，没有走到点击"确定"步骤。

```
09:38:14 [form_filler] 选择 radio: 授权类型 = 用户
09:38:15 [form_filler] [radio-cascade] transfer-box 需要选择
09:38:15 [form_filler] [transfer-box] ✓ 找到穿梭框更新，点击成功  ← 填充成功
09:38:20 [navigation_executor] 表单字段状态检查:
  授权类型 (radio): ✅ value='用户'
  授权用户 (input): ❌ 空 value=''     ← 误判！
  授权角色 (radio): ✅ value='组织机构'
09:38:21 [required_field_empty] → is_terminal=True → 直接报错
```

### 根因

**文件**: `core/discovery/operation_executor/navigation_executor.py`  
**函数**: `discover_all` 中的字段状态检查 JS（第 247-356 行）

**信息断层**: form_filler 的 radio-cascade 机制成功填充了"授权用户"字段（通过 transfer-box 穿梭框），但 navigation_executor 的字段状态检查不知道 cascade 已处理。它通过 JS 重新检查 DOM，将"授权用户"识别为普通 `input` 类型，检查 `inputEl.value` 为空。

**问题代码** (第 343-351 行):

```javascript
} else {
    // 普通 input / textarea — "授权用户"落入此分支
    const inputEl = fi.querySelector('input:not([type="hidden"]), textarea');
    const value = inputEl ? inputEl.value.trim() : '';
    results.push({
        label, type: 'input', value: value,
        isDisabled: inputEl ? inputEl.disabled : false,
        hasValue: value.length > 0    // ← value 为空，hasValue=false
    });
}
```

"授权用户"的 form-item 内部没有直接的 `[class*="transfer-box"]` 元素（穿梭框是 radio-cascade 动态关联的），所以 JS 检查走到了 `else` 分支，将其当作普通 input。

**必填字段检查逻辑** (第 419-437 行):

```python
# 必填字段为空且非 disabled → 标记失败
for fs in field_states:
    if not fs["hasValue"]:
        if fs["isDisabled"]:
            continue
        if fs["label"] in no_options_labels:
            continue
        # ← 缺少: cascade 填充字段的跳过逻辑
        unfilled_required.append(f"{fs['label']}（{fs['type']}，值为空）")

if unfilled_required:
    nav_info["error_type"] = "required_field_empty"  # ← is_terminal，直接失败
```

同样的 bug 在第 858-874 行（Path B）也存在。

### 修复方案

**修改文件**: `core/discovery/operation_executor/navigation_executor.py`  
**修改位置**: 两处（第 416-431 行 和 第 855-868 行）  
**修改量**: 每处加 5-6 行

**思路**: form_filler 的 `fill_form_fields` 返回的 `cascade_actions` 记录了哪些字段触发了 cascade 填充。这些字段的实际值不是通过标准 DOM input 体现的，而是通过穿梭框等复杂组件。在必填检查时，这些字段应被视为"已由 cascade 处理"而跳过。

**修改前** (第 416-431 行):
```python
            # 收集 no_options 的字段标签
            no_options_labels = {d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"}

            # 必填字段为空且非 disabled → 标记失败
            for fs in field_states:
                if not fs["hasValue"]:
                    if fs["isDisabled"]:
                        LOG.info(f"    跳过 disabled 必填字段: {fs['label']}")
                        continue
                    if fs["label"] in no_options_labels:
                        LOG.info(f"    跳过 no_options 必填字段: {fs['label']}")
                        continue
                    unfilled_required.append(f"{fs['label']}（{fs['type']}，值为空）")
```

**修改后**:
```python
            # 收集 no_options 的字段标签
            no_options_labels = {d["label"] for d in ms_details if d.get("skipped_reason") == "no_options"}

            # ★ 收集 cascade 填充的字段标签（radio-cascade 触发的穿梭框等组件）
            # 这些字段的值不在标准 DOM input 中体现，不应参与必填检查
            cascade_filled_labels = set()
            for field in form_field_details:
                if field.get("cascade_actions"):
                    cascade_filled_labels.add(field.get("label"))
                # 也收集有 radio cascade 触发但未记录到 cascade_actions 的情况
                if field.get("kb_category") == "radio" and field.get("cascade_actions"):
                    for ca in field["cascade_actions"]:
                        if ca.get("action_type") == "fill_transfer":
                            cascade_filled_labels.add(ca.get("target_label", ""))

            # 必填字段为空且非 disabled → 标记失败
            for fs in field_states:
                if not fs["hasValue"]:
                    if fs["isDisabled"]:
                        LOG.info(f"    跳过 disabled 必填字段: {fs['label']}")
                        continue
                    if fs["label"] in no_options_labels:
                        LOG.info(f"    跳过 no_options 必填字段: {fs['label']}")
                        continue
                    if fs["label"] in cascade_filled_labels:
                        LOG.info(f"    跳过 cascade 填充字段: {fs['label']}（已由 radio-cascade 处理）")
                        continue
                    unfilled_required.append(f"{fs['label']}（{fs['type']}，值为空）")
```

**同样修改第 855-868 行**（Path B 中的相同逻辑），增加 `cascade_filled_labels` 收集与跳过。

**安全性**: 仅跳过 cascade 已处理的字段，不影响其他字段的必填检查。`cascade_filled_labels` 集合基于 form_filler 的实际填充记录构建，不会误跳过未填充的字段。

---

## Bug 4: 删除用户未点击确定

### 现象

删除操作弹出确认 dialog（type=dialog, title="删除用户"），但 confirm_dialog 点击"确定"后行未消失，删除未生效。

```
09:39:26 [DIAG-delete-before] dialogs(1): [{"title": "删除用户", "btns": ["", "取 消", "确 定"]}]
09:39:26 [DIAG-delete-before] allBtns(0): []   ← 全局按钮扫描没找到"确 定"
09:39:29 confirm_dialog 返回: '确 定'          ← 返回了按钮文本
09:39:29 [DIAG-delete-after] messageBoxStillVisible: None  ← 检查的是 message-box
09:39:44 [no_success_signal] 删除 操作未检测到成功信号
```

### 根因

**文件**: `core/discovery/replay/button_driver.py`  
**函数**: `confirm_dialog` (第 1394-1564 行)

**问题 1 — 按钮定位**: 删除确认 dialog 是 generic-dialog 类型（`el-dialog__wrapper`），不是 message-box。`confirm_dialog` 的 generic-dialog 分支首先尝试 `.el-button--primary`（第 1494 行），如果匹配到了其他按钮（如 dialog 中嵌套的其他 primary 按钮），就会点击错误的元素。

**问题 2 — Playwright click 不触发 Vue 事件**: `button.click()` (第 1503 行) 使用 Playwright 的点击模拟，在某些 Element UI 场景下可能不能正确触发 Vue 的 `@click` 事件处理器。代码已有 JS click 兜底（第 1532-1558 行），但只在 Playwright click 的 locator 找不到按钮时才执行。当 locator 找到了按钮但点击没有触发 Vue 事件时，不会走到 JS 兜底。

**问题 3 — DIAG-delete-after 只检查 message-box**: `_do_delete` (第 3284-3293 行) 的诊断代码只检查 `messageBoxStillVisible` 和 `popconfirmStillVisible`，不检查 generic dialog 是否仍然可见。所以即使 dialog 没关闭也不会被检测到。

**代码证据** (第 1491-1509 行):

```python
else:  # generic-dialog
    dialog_scope = ".el-dialog__wrapper:not([style*='display: none'])"
    if confirm:
        button = page.locator(f'{dialog_scope} button.el-button--primary').first
        # ↑ 可能匹配到 dialog 中其他 primary 按钮
        if await button.count() == 0:
            button = page.locator(f"xpath=...").first

if button and await button.count() > 0:
    button_text = await button.inner_text()
    try:
        await button.click(timeout=5000)  # ← Playwright click 可能不触发 Vue 事件
    except Exception as click_err:
        if "intercepts pointer events" in str(click_err):
            await button.evaluate("el => el.click()")  # JS 兜底，仅在遮挡时触发
        else:
            raise
    return button_text  # ← 返回了按钮文本，但 Vue 事件可能没触发
```

### 修复方案

**修改文件**: `core/discovery/replay/button_driver.py`  
**修改位置**: `confirm_dialog` 函数的 generic-dialog 分支 (第 1491-1509 行)  
**修改量**: 改 ~15 行

**思路**: 对于 generic-dialog 类型，**直接优先使用 JS click** 而非 Playwright click。JS click (`element.click()`) 直接调用 DOM API，能可靠触发 Vue 的 `@click` 事件处理器，不受 Playwright 点击模拟的遮挡/动画/时序问题影响。

**修改前** (第 1491-1498 行):
```python
        else:  # generic-dialog — 限定在可见对话框容器内查找
            dialog_scope = ".el-dialog__wrapper:not([style*='display: none']), .ant-modal-wrap:not([style*='display: none'])"
            if confirm:
                button = page.locator(f'{dialog_scope} button.el-button--primary').first
                if await button.count() == 0:
                    button = page.locator(f"xpath={dialog_scope}//button[{confirm_xpath_pred} and {hidden_filter}]").first
            else:
                button = page.locator(f"xpath={dialog_scope}//button[{cancel_xpath_pred} and {hidden_filter}]").first
```

**修改后**:
```python
        else:  # generic-dialog — 优先使用 JS click（可靠触发 Vue 事件）
            target_texts = const.CONFIRM_TEXTS if confirm else const.CANCEL_TEXTS
            target_json = json.dumps(target_texts, ensure_ascii=False)
            js_clicked = await page.evaluate(f"""() => {{
                const targetTexts = {target_json};
                const dialogs = document.querySelectorAll(
                    '.el-dialog__wrapper:not([style*="display: none"]), ' +
                    '.ant-modal-wrap:not([style*="display: none"])'
                );
                for (const dialog of dialogs) {{
                    if (dialog.offsetWidth <= 0 || dialog.offsetHeight <= 0) continue;
                    const buttons = dialog.querySelectorAll('button');
                    for (const btn of buttons) {{
                        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                        if (btn.disabled || btn.classList.contains('is-disabled')) continue;
                        const txt = (btn.textContent || '').trim().replace(/\\s+/g, '');
                        if (targetTexts.includes(txt)) {{
                            btn.click();
                            return (btn.textContent || '').trim();
                        }}
                    }}
                }}
                return null;
            }}""")
            if js_clicked:
                await wait_for_loading_complete(page)
                return js_clicked
            # JS click 未命中，回退到 Playwright locator（兼容非 Vue 框架）
            dialog_scope = ".el-dialog__wrapper:not([style*='display: none']), .ant-modal-wrap:not([style*='display: none'])"
            if confirm:
                button = page.locator(f"xpath={dialog_scope}//button[{confirm_xpath_pred} and {hidden_filter}]").first
            else:
                button = page.locator(f"xpath={dialog_scope}//button[{cancel_xpath_pred} and {hidden_filter}]").first
```

**关键变化**:
1. generic-dialog 分支先用 JS `btn.click()` 在可见 dialog 内精确匹配确认/取消按钮
2. 只在可见（`offsetWidth > 0`）、非 disabled 的按钮中查找
3. 通过 `targetTexts.includes(txt)` 匹配（已去空格归一化），与现有 CONFIRM_TEXTS 保持一致
4. JS click 未命中时才回退到 Playwright locator（作为兼容兜底）

**安全性**: 只影响 generic-dialog 类型的确认操作。message-box 和 popconfirm 分支保持不变（它们的 Playwright click 已被验证可靠）。JS click 是业界公认的 Vue/React 组件点击触发方式，不会引入新问题。

---

## 实施清单

### 优先级排序

| 序号 | Bug | 影响 | 修改复杂度 | 优先级 |
|------|-----|------|-----------|--------|
| 1 | Bug 1&2 STATE-CHANGE 重复执行 | 浪费时间，可能产生脏数据 | 3 行 | P0 |
| 2 | Bug 4 删除确认点击 | 删除操作失败 | 15 行 | P0 |
| 3 | Bug 3 授权字段误判 | 授权操作失败 | 10 行 × 2 处 | P1 |

### 实施步骤

#### Step 1: 修复 STATE-CHANGE 重复执行

```
文件: core/discovery/operation_executor/crud_executor.py
位置: 第 579 行
操作: 在 for 循环体开头加 executed_actions 检查
```

#### Step 2: 修复 generic-dialog 确认按钮

```
文件: core/discovery/replay/button_driver.py
位置: 第 1491-1498 行
操作: generic-dialog 分支优先使用 JS click
```

#### Step 3: 修复 cascade 字段必填检查

```
文件: core/discovery/operation_executor/navigation_executor.py
位置: 第 416-431 行 (Path A) 和 第 855-868 行 (Path B)
操作: 收集 cascade_filled_labels，在必填检查中跳过
```

#### Step 4: 验证

```bash
python -m core.discovery.run --project estack-ceshiyu \
  --url "/estack/web/estack/user-center/user-manage/user" \
  --module "用户管理" --stage 1
```

预期结果:
- 设置到期时间: 执行 1 次（而非 4 次）
- 启用/停止: 各执行 1 次（而非 2 次）
- 授权: 成功（transfer-box 被 cascade 正确填充，不误判为空）
- 删除: 成功（JS click 触发 Vue 事件，dialog 正确关闭）

### 涉及文件清单

| 文件 | 修改行数 | 修改类型 |
|------|---------|---------|
| `core/discovery/operation_executor/crud_executor.py` | 579 行加 3 行 | 新增逻辑 |
| `core/discovery/replay/button_driver.py` | 1491-1498 行改 ~15 行 | 重构分支 |
| `core/discovery/operation_executor/navigation_executor.py` | 416 行加 10 行, 855 行加 10 行 | 新增逻辑 × 2 处 |

### 风险评估

- **Bug 1&2 修复**: 仅加 3 行防御性检查，不影响首次执行路径，**风险极低**
- **Bug 4 修复**: 改变 generic-dialog 的按钮点击方式，但保留了 Playwright 回退路径，**风险低**
- **Bug 3 修复**: 仅在 cascade 已处理的情况下跳过必填检查，基于 form_filler 的实际记录而非猜测，**风险低**
