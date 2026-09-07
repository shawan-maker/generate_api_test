# Stage 1 探测质量改进方案

## 背景

Stage 1（`discover_ui.py`）的业务闭环验证存在三类问题：

| # | 问题 | 影响 |
|---|------|------|
| P1 | 操作成功判定标准过宽 | 迁移操作实际失败（确认按钮 disabled），但被标记为成功 |
| P2 | 不可重试的错误类型被盲目重试 | import/authorize 各重试 2 次，无策略调整 |
| P3 | 跨页面操作只"看一眼"就返回 | authorize 跳转到新页面后未继续探测 |

---

## P1：操作成功判定标准收紧

### 现状分析

**成功判定函数** `_verify_operation_success()`（`discover_ui.py:2750`）

当前对 `delete/lock/unlock/reset/authorize/migrate/import/export` 类操作：

```python
# 1. 确认弹窗已关闭？
has_pending_confirm = ...  # 检查 message-box 是否还可见
if has_pending_confirm: return False

# 2. 没有错误消息？
has_error = ...  # 检查 el-message--error 是否可见
if not has_error:
    return True  # ← 确认弹窗消失 + 没有错误 = 判定成功 ❌
```

**问题链条（migrate 案例）：**

```
_do_generic_operation()
  → 点击"批量迁移"按钮
  → _check_precondition_state() 检测到确认对话框 ✅
  → confirm_dialog() 找不到确认按钮（按钮 disabled）→ confirmed="" ❌
  → 对话框被 _close_dialog() 关闭（ESC / 点击关闭按钮）
  → read_form_errors() 返回 []（表单校验错误为空）
  → _verify_operation_success() 检查：
      弹窗已关闭 ✅ + 无错误消息 ✅ → return True ❌❌❌
```

**根本原因：**
1. `_verify_operation_success()` 对非 create/update 操作采用"无错误即成功"的宽松标准
2. `confirmed` 变量（确认按钮是否被点击）未被传递到成功判定逻辑中
3. 确认弹窗被 `_close_dialog()` 主动关闭，掩盖了"未点击确认"的事实

### 修改方案

#### 修改 1.1：`_do_generic_operation()` 增加确认状态传递

**位置：** `discover_ui.py` 第 2615-2625 行

**修改前：**
```python
success = await _verify_operation_success(page, action)
if not success:
    if not confirmed:
        return {"success": False, "error_type": "no_confirm_button", ...}
    return {"success": False, "error_type": "no_success_signal", ...}
```

**修改后：**
```python
# 如果确认按钮没被点击，直接判定失败（不管 _verify 返回什么）
if not confirmed:
    # 尝试更细致的原因分析
    return {"success": False, "error_type": "confirm_not_clicked",
            "error_text": f"确认弹窗存在但未能点击确认按钮（可能按钮 disabled 或不可见）"}

success = await _verify_operation_success(page, action, strict=True)
if not success:
    return {"success": False, "error_type": "no_success_signal", ...}
```

**关键变化：** 将 `confirmed` 检查移到 `_verify_operation_success` 之前，未点击确认直接失败，不再依赖后续的宽松判定。

#### 修改 1.2：`_verify_operation_success()` 增加严格模式

**位置：** `discover_ui.py` 第 2750-2809 行

**修改前：**
```python
async def _verify_operation_success(page, operation_type: str) -> bool:
```

**修改后：**
```python
async def _verify_operation_success(page, operation_type: str, strict: bool = False) -> bool:
```

**严格模式逻辑：**

```python
# 对于 delete/lock/unlock/reset/authorize/migrate/import/export:

if strict:
    # 严格模式：必须有正向成功信号
    # 1. 检查成功提示消息
    has_success_msg = ...  # el-message--success / ant-message-success
    if has_success_msg:
        return True

    # 2. 检查数据变化（行数变化 / 状态变化）
    data_changed = await _check_data_changed(page, operation_type)
    if data_changed:
        return True

    # 3. 两者都没有 → 失败
    return False
else:
    # 宽松模式（保持现有逻辑，用于非关键操作）
    has_pending_confirm = ...
    if has_pending_confirm: return False
    has_error = ...
    if not has_error: return True
    return False
```

#### 修改 1.3：新增 `_check_data_changed()` 辅助函数

```python
async def _check_data_changed(page, operation_type: str) -> bool:
    """检查操作后是否有可观测的数据变化。

    检测信号：
    - 成功提示消息（el-message--success）
    - 表格行数变化（需要记录操作前行数）
    - 页面刷新信号（loading 指示器出现后消失）
    """
    return await page.evaluate("""() => {
        // 1. 成功消息
        const successMsg = document.querySelector(
            '.el-message--success, .el-message .el-icon-success, ' +
            '.ant-message-success, .ant-message .anticon-check-circle');
        if (successMsg && successMsg.offsetWidth > 0) return true;

        // 2. 成功通知
        const notif = document.querySelector(
            '.el-notification .el-icon-success, ' +
            '.ant-notification .anticon-check-circle');
        if (notif) return true;

        return false;
    }""")
```

### 影响评估

| 受影响场景 | 影响 | 风险 |
|-----------|------|------|
| create/update | 不受影响（独立路径） | 无 |
| delete | 需要检测到成功消息或行数变化 | **低风险**：删除操作通常有成功提示 |
| lock/unlock | 需要检测到成功消息 | **中风险**：某些系统的冻结/启用可能没有成功提示 |
| reset | 需要检测到成功消息 | **低风险**：重置密码通常有成功提示 |
| migrate/import | 判定更准确 | **正向**：之前误判为成功，现在能正确标记失败 |

**缓解措施：** 对 `strict=True` 场景，如果没检测到成功信号但有 `_close_dialog()` 关闭了弹窗，记录为 `partial`（部分成功）而非 `failed`。

---

## P2：错误类型驱动的策略分流

### 现状分析

**`_error_driven_retry()`**（`discover_ui.py:1745`）的当前流程：

```
for round in range(max_rounds):  # 最多 10 轮
    result = await operation_fn(page, context)
    if result.success: return result

    error_key = f"{error_type}|{error_field}|{error_text}"

    if error_key == last_error_key:  # 同类错误重复
        → Vision 截图分析（同状态只用 1 次）
        → 注入 vision_hints 到 context
    else:
        → _apply_fix() 修改 context

    last_error_key = error_key
```

**问题：**
- `_apply_fix()` 只处理 `form_validation`、`api_error`、`click_failed`、`no_dialog` 四类
- `env_dependency`（import 需要文件）和 `navigation_unexplored`（authorize 页面跳转）没有对应的 fix 策略
- 结果：这两类错误走 `_apply_fix()` → 什么也没改 → 原样重试 → 再次失败 → Vision 分析也没用 → 放弃

### 修改方案

#### 修改 2.1：在 `_error_driven_retry()` 开头增加错误类型预检

**位置：** `discover_ui.py` 第 1763 行（for 循环内部）

```python
for round_num in range(max_rounds):
    try:
        result = await operation_fn(page, context)
    except Exception as e:
        result = {"success": False, "error_type": "exception", "error_text": str(e)}

    if result.get("success"):
        return result

    error_type = result.get("error_type", "unknown")

    # ─── 新增：不可重试错误 → 立即返回 ───
    if error_type in TERMINAL_ERRORS:
        LOG.info(f"    [{error_type}] 不可重试，直接标记")
        return result

    # ─── 新增：特殊错误 → 转交专用处理 ───
    if error_type in DELEGATE_ERRORS:
        handler = DELEGATE_ERRORS[error_type]
        return await handler(page, context, result)

    # ─── 原有逻辑继续 ───
    error_text = result.get("error_text", "")
    ...
```

**错误分类定义：**

```python
# 不可重试：环境限制，重试多少次都没用
TERMINAL_ERRORS = {
    "env_dependency",      # 需要上传文件（import）
    "permission_denied",   # 权限不足
    "resource_not_found",  # 资源不存在
}

# 委托专用处理器
DELEGATE_ERRORS = {
    "navigation_unexplored": _handle_cross_page_operation,  # → P3
}
```

#### 修改 2.2：`_apply_fix()` 增加更多错误类型

**位置：** `discover_ui.py` 第 2944 行

```python
def _apply_fix(error_result: dict, context: dict) -> dict:
    error_type = error_result.get("error_type", "")

    # 不可重试错误：不做任何修改，让上层直接返回
    if error_type in ("env_dependency", "permission_denied", "resource_not_found"):
        return context

    # 确认按钮未找到：尝试深度 DOM 扫描
    if error_type == "confirm_not_clicked":
        context["deep_confirm_scan"] = True
        return context

    # 无成功信号：可能需要等待更长时间
    if error_type == "no_success_signal":
        context["extended_wait"] = True
        return context

    # 页面跳转未探索：转交跨页面处理器
    if error_type == "navigation_unexplored":
        context["cross_page"] = True
        return context

    # 原有逻辑...
    if error_type == "form_validation":
        ...
```

### 影响评估

| 场景 | 变化 | 风险 |
|------|------|------|
| import | 不再重试，直接标记 `skipped` | **正向**：节省 2 轮无意义重试 |
| authorize | 不再盲目重试，转交跨页面处理 | **正向**：触发 P3 的递归探测 |
| create/update | 不受影响（`form_validation` 路径不变） | 无 |
| delete | 不受影响 | 无 |
| lock/unlock/reset | 不受影响 | 无 |

---

## P3：跨页面操作递归探测

### 现状分析

**`_explore_navigated_page()`**（`discover_ui.py:2829`）的当前实现：

```python
async def _explore_navigated_page(page, action, new_url):
    nav_info = {"navigated_url": new_url, "buttons": [], ...}

    # 只收集元信息
    page_info = await page.evaluate("""() => {
        const buttons = Array.from(document.querySelectorAll('button'))...
        return { title, buttons, hasForm };
    }""")
    nav_info["buttons"] = page_info["buttons"]

    # 然后直接导航回原页面
    await page.goto(url_before, ...)

    return nav_info
```

**问题：** 只做了"看一眼"就返回。没有：
- 扫描新页面的按钮/表单
- 在新页面执行任何操作
- 将新页面的操作结果作为子步骤

### 修改方案

#### 修改 3.1：重写 `_explore_navigated_page()` → `_explore_and_operate_in_new_page()`

**核心思路：** 复用 `discover_all()` 探测新页面 → 识别可执行操作 → 逐一执行 → 返回原页面

```python
async def _explore_and_operate_in_new_page(page, action, new_url,
                                            depth=0, max_depth=3) -> dict:
    """探索导航后的新页面，探测并执行操作。

    Args:
        page: Playwright Page 对象
        action: 触发跳转的操作类型
        new_url: 新页面 URL
        depth: 当前递归深度（0 = 首次进入）
        max_depth: 最大递归深度（默认 3 层）

    Returns:
        dict: {
            "navigated_url": str,
            "page_title": str,
            "sub_operations": [
                {"action": "create", "success": True, "steps": [...]},
                {"action": "update", "success": False, "error": "..."},
            ],
            "terminal_action": str | None,  # 最终完成的操作
        }
    """
    nav_info = {
        "navigated_url": new_url,
        "depth": depth,
        "page_title": "",
        "sub_operations": [],
    }

    # 1. 等待页面加载
    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(1000)

    # 2. 递归探测新页面（复用 discover_all）
    sub_result = await discover_all(page)
    nav_info["page_title"] = await page.title()

    # 3. 从探测结果中识别可执行操作
    sub_buttons = sub_result.get("toolbar_buttons", []) + sub_result.get("row_actions", [])
    sub_fields = sub_result.get("form_fields", [])

    # 4. 逐一尝试执行操作
    form_filler = FormFiller(page)
    for btn in sub_buttons:
        btn_action = btn.get("action", "") or _match_crud(btn.get("text", ""))
        if not btn_action:
            continue

        # 根据按钮类型分发
        if btn_action == "create" and sub_fields:
            result = await _do_create(page, {
                "btn": btn, "fields": sub_fields,
                "username": "auto", "form_filler": form_filler,
                "framework": sub_result.get("framework", "element-ui"),
            })
        elif btn_action in ("save", "submit", "confirm"):
            result = await _do_save_or_confirm(page, {"btn": btn})
        else:
            result = await _do_generic_operation(page, {
                "btn": btn, "action": btn_action, "marker": None,
            })

        nav_info["sub_operations"].append({
            "action": btn_action,
            "button_text": btn.get("text", ""),
            "success": result.get("success", False),
            "error_type": result.get("error_type", ""),
            "selectors": result.get("selectors", {}),
        })

        # 检查是否又触发了页面跳转（递归）
        if page.url != new_url and depth < max_depth:
            LOG.info(f"    子操作触发再次跳转: {new_url} → {page.url}")
            sub_nav = await _explore_and_operate_in_new_page(
                page, btn_action, page.url, depth=depth + 1, max_depth=max_depth
            )
            nav_info["sub_operations"].append({
                "action": f"navigate_{btn_action}",
                "sub_page": sub_nav,
            })
            # 递归返回后，继续当前页面的剩余操作

    return nav_info
```

#### 修改 3.2：`_do_generic_operation()` 的导航检测调用新函数

**位置：** `discover_ui.py` 第 2525-2544 行

**修改前：**
```python
if url_before != url_after:
    nav_info = await _explore_navigated_page(page, action, url_after)
    await page.goto(url_before, ...)
    return {"success": False, "error_type": "navigation_unexplored",
            "nav_info": nav_info, ...}
```

**修改后：**
```python
if url_before != url_after:
    # 递归探测并执行新页面的操作
    nav_info = await _explore_and_operate_in_new_page(
        page, action, url_after, depth=0, max_depth=3
    )

    # 根据子操作结果判定成功/失败
    sub_ops = nav_info.get("sub_operations", [])
    all_success = all(op.get("success", False) for op in sub_ops) if sub_ops else False

    if all_success and sub_ops:
        # 所有子操作成功 → 标记为成功
        await _navigate_back_to_list(page, url_before)
        return {"success": True, "nav_info": nav_info,
                "selectors": {"trigger": btn_text},
                "sub_operations": sub_ops}
    else:
        # 有子操作失败 → 标记为失败，但记录已探索的步骤
        await _navigate_back_to_list(page, url_before)
        return {"success": False, "error_type": "cross_page_partial",
                "error_text": f"跨页面操作部分失败: "
                              f"{sum(1 for o in sub_ops if not o.get('success'))}/{len(sub_ops)} 失败",
                "nav_info": nav_info,
                "sub_operations": sub_ops,
                "selectors": {"trigger": btn_text}}
```

#### 修改 3.3：导航回列表页的安全函数

```python
async def _navigate_back_to_list(page, original_url: str):
    """安全导航回原始列表页。

    优先使用 page.goto()，失败则尝试 page.go_back()。
    """
    try:
        await page.goto(original_url, wait_until="domcontentloaded", timeout=30000)
        await wait_for_table_ready(page, timeout=15000)
    except Exception:
        try:
            await page.go_back()
            await wait_for_navigation_complete(page)
        except Exception as e:
            LOG.warning(f"    导航回原始页面失败: {e}")
```

### 递归深度控制

| 层级 | 说明 | 示例 |
|------|------|------|
| depth=0 | 原始列表页点击触发跳转 | 点击"授权" |
| depth=1 | 新页面探测并执行 | 授权页面：勾选权限→保存 |
| depth=2 | 子操作又触发跳转 | 保存后弹出"确认生效"对话框 |
| depth=3 | 最大深度，不再递归 | 直接记录信息并返回 |

**关键约束：**
- `max_depth=3`：最多 3 层嵌套
- 每层递归有独立超时（15s 页面加载 + 操作超时）
- 递归过程中记录所有步骤，确保 playbook 生成有据可查
- 如果某层递归耗时超过 60s，强制中断并返回已有结果

### 影响评估

| 场景 | 变化 | 风险 |
|------|------|------|
| authorize（授权） | 完整探索授权页面 | **正向** |
| detail（查看详情） | 如果查看详情触发跳转，也会递归探测 | **低风险**：详情页通常只有"返回"按钮 |
| create/update | 不受影响（弹窗模式，不触发导航） | 无 |
| delete | 不受影响 | 无 |
| 任意操作触发意外跳转 | 也会被递归探测 | **中风险**：可能增加探测耗时 |

**缓解措施：**
- 递归总时间上限 60s，超时强制返回
- 同一 URL 不重复探测（URL 去重）
- 递归深度限制 3 层

---

## 问题间的交互影响

### 影响链 1：P2 → P3

`navigation_unexplored` 错误在 P2 中不再盲目重试，而是委托给 P3 的跨页面处理器。

```
_error_driven_retry()
  → _do_generic_operation()
    → 检测到页面跳转
    → 调用 _explore_and_operate_in_new_page()  ← P3
    → 返回跨页面操作结果
  → 根据结果判定 success/failure
  → 如果成功 → 不再重试
  → 如果失败 → 标记为 cross_page_partial，不再盲目重试
```

**结论：** P2 和 P3 需要同时实施。如果只实施 P2 不实施 P3，`navigation_unexplored` 会被标记为终端错误直接返回，authorize 操作将永远无法验证。

### 影响链 2：P1 → P3

P3 的递归探测中，每一层子操作也需要使用 P1 的严格成功判定。

```
_explore_and_operate_in_new_page()
  → 对每个子按钮调用 _do_generic_operation() / _do_create()
    → _verify_operation_success(page, action, strict=True)  ← P1
    → 子操作的成功/失败判定同样严格
```

**结论：** P1 的 `strict` 模式需要在 P3 的递归路径中也生效。

### 影响链 3：P1 → 现有操作

P1 的严格模式可能影响现有正常工作的操作（lock/unlock/reset）。

**风险场景：** 某些系统的冻结/启用操作成功后没有明确的成功提示消息，只有关闭确认弹窗。

**缓解措施：**
- `strict` 模式同时检查两种信号：成功提示 **或** 数据变化
- 对于 lock/unlock：检查操作后行状态是否变化（如 state 字段从 ENABLE → DISABLE）
- 如果两种信号都没有但确认按钮被点击了，标记为 `partial` 而非 `failed`

---

## 遗漏检查

### 4.1 `_do_delete()` 是否受影响？

`_do_delete()` 有独立的确认/验证逻辑（检查行消失），不经过 `_verify_operation_success()`。

**结论：** P1 修改不影响 delete 路径。

### 4.2 `_do_create()` / `_do_edit()` 是否受影响？

create/update 有独立的 `_verify_operation_success()` 路径（检查弹窗关闭），不走 generic 路径。

**结论：** P1 的 `strict` 参数不影响 create/update。但如果未来统一使用 `strict`，需要注意弹窗关闭 ≠ 成功（可能有后端错误弹窗延迟出现）。

### 4.3 `_ensure_on_list_page()` 在跨页面操作后的行为

`_validate_business_flow()` 在每个操作后调用 `_ensure_on_list_page()`（1718 行）。跨页面操作完成后需要确保已经导航回列表页。

**当前代码：**
```python
# 操作后确保回到列表页
try:
    await _ensure_on_list_page(page)
except Exception as e:
    LOG.error(f"    _ensure_on_list_page 异常: {e}")
```

**风险：** P3 的 `_navigate_back_to_list()` 已经导航回原页面，`_ensure_on_list_page()` 应该能正常工作。但如果跨页面操作中途失败（如超时），页面可能停留在中间状态。

**缓解措施：** P3 的 `_explore_and_operate_in_new_page()` 必须确保无论成功失败都导航回原页面（使用 try/finally）。

### 4.4 `_close_dialog()` 在严格模式下的时序问题

当前流程：
1. `_check_precondition_state()` 检测到确认弹窗
2. `confirm_dialog()` 尝试点击确认（可能失败）
3. `_close_dialog()` 关闭弹窗
4. `_verify_operation_success()` 检查状态

**问题：** 步骤 3 的 `_close_dialog()` 在步骤 4 之前执行，会掩盖弹窗未正确关闭的事实。

**修改：** 在 P1 方案中，如果 `confirmed` 为空（确认按钮未被点击），不调用 `_close_dialog()`，直接返回失败。让调用方（`_error_driven_retry`）决定是否关闭。

### 4.5 Playbook 生成的影响

`build_playbook()`（`discover_ui.py`）从 validated 结果生成 playbook 步骤。P3 引入了 `sub_operations` 嵌套结构，需要确保 playbook 生成器能处理。

**修改点：** `build_playbook()` 需要增加对 `nav_info.sub_operations` 的展平处理，将跨页面操作的子步骤转为 playbook steps。

### 4.6 Stage 2 的影响

Stage 2（`generate_ui_script.py`）从 playbook 生成 UI 脚本。如果 playbook 包含跨页面操作，生成的脚本需要能在回放时处理页面跳转。

**影响：** `replay_engine.py` 的 `replay_from_playbook()` 需要能处理 `navigate` 类型的 step。这是 Stage 2 的修改范围，不在本方案内，但需要在 playbook 中标记清楚。

---

## 实施顺序

| 顺序 | 方案 | 修改文件 | 预估工作量 |
|------|------|----------|-----------|
| 1 | P1：成功判定收紧 | `discover_ui.py` | 1-2h |
| 2 | P2：错误类型分流 | `discover_ui.py` | 1h |
| 3 | P3：跨页面递归 | `discover_ui.py` | 3-4h |

P1 和 P2 可以独立实施和测试。P3 依赖 P2（需要 P2 的 `DELEGATE_ERRORS` 转交）。

---

## 测试验证

### P1 验证
```bash
python -m module_discovery.run --project ecm-compute --stage 1 \
  --module "用户管理" --url "https://10.151.61.248/estack/web/estack/user-center/user-manage/user"

# 检查 migrate 操作是否被标记为 failed（而非 passed）
grep -A5 "验证操作: migrate" output/*.log
```

### P2 验证
```bash
# 检查 import 是否直接标记 skipped（不重试）
# 检查 authorize 是否触发跨页面处理
grep -E "(env_dependency|不可重试|跨页面)" output/*.log
```

### P3 验证
```bash
# 检查 authorize 操作是否探索了授权页面
grep -E "(子操作|sub_operations|新页面)" output/*.log

# 检查 playbook 是否包含跨页面步骤
python -c "import json; d=json.load(open('kb/module_discovered/用户管理_playbook.json'));
print([op for op in d['operations'].get('authorize', {}).get('steps', []) if 'navigate' in str(op)])"
```
