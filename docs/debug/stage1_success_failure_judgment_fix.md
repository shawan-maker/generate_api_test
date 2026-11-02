# Stage 1 用户管理 — 成功/失败判断逻辑修复

## 修复日期：2026-11-02

---

## 问题背景

Stage 1 用户管理模块运行后，出现两个误判：

1. **导入操作**：表单字段全部填充失败（0/3），但被标记为成功
2. **迁移操作**：dialog 弹出"批量用户迁移失败"文本，但被标记为成功

---

## 根因分析

### 端到端调用链

```
_do_generic_operation(action)
│
├─ Path 1: new_tab → _explore_and_operate_in_new_page → return nav_info
├─ Path 2: page_nav URL 跳转 → _explore_and_operate_in_new_page → return nav_info
├─ Path 3: 文件上传检测 → return make_error("env_dependency")
├─ Path 4: drawer/dialog 有表单 → scan → fill → submit → fallthrough
├─ Path 5: drawer/dialog 无表单 → confirm → fallthrough
├─ Path 6: message-box/popconfirm → confirm → fallthrough
├─ Path 7: 无 dialog → toast 轮询 → fallthrough
│
└─ 统一验证点 (line 4632)
    └→ _verify_operation_success() → (bool, detail)
    └→ ❌ 原代码只取 bool，丢弃 detail
    └→ ❌ if not success → 无条件覆盖为 True
```

### Bug A：导入误判为成功

**路径**：Path 2 (page_nav URL 跳转)

```
crud_executor.py:3765 → URL 跳转检测
  → navigation_executor.py:643 → _explore_and_operate_in_new_page
    → scan_form_fields_v2() → 返回 0 个字段（导入页面的字段是文件上传）
    → line 650: if not form_field_details → True
      → ❌ 直接标记为"浏览页面"成功
      → 没有检测文件上传组件
```

**根因**：`navigation_executor.py:650` 把"有文件上传组件但无表单字段"误判为浏览页面。

### Bug B：迁移误判为成功

**路径**：Path 4 (dialog 有表单) → 统一验证点

```
crud_executor.py:4632 → _verify_operation_success()
  → success_verifier.py 策略 3 检测到 dialog 中的"失败"关键词
    → return (False, {failure_type: "dialog", failure_reason: "批量用户迁移失败..."})

base_executor.py:857 → _verify_operation_success wrapper
  → success, detail = await verify_operation_success(...)
  → return success  ← ❌ 只返回 bool，丢弃 detail

crud_executor.py:4637
  → if not success:
    → LOG.warning("[no_success_signal] ...仍标记为成功")
    → ❌ 无条件覆盖 success=False → True
```

**根因**：两层缺陷叠加：
1. `base_executor.py:863` 丢弃 `detail`（包含 `failure_type="dialog"`）
2. `crud_executor.py:4637` 不区分"明确失败"和"无信号"，一律覆盖为成功

---

## 修复方案

### 修改 1：base_executor.py — 返回完整 detail

```python
# 修改前 (line 842-863):
async def _verify_operation_success(...) -> bool:
    success, detail = await verify_operation_success(...)
    return success  # ← 丢弃 detail

# 修改后:
async def _verify_operation_success(...) -> tuple:
    """返回 (success: bool, detail: dict)"""
    return await verify_operation_success(...)
```

### 修改 2：crud_executor.py 统一判定点 (line 4632-4653)

```python
# 修改前:
success = await _verify_operation_success(...)
if not success:
    LOG.warning("[no_success_signal] ...仍标记为成功")
    await _close_dialog(page)
# result = {"success": True, ...}  ← 无条件成功

# 修改后:
success, detail = await _verify_operation_success(...)
if not success:
    failure_type = detail.get("failure_type", "no_signal")
    failure_reason = detail.get("failure_reason", "")
    if failure_type != "no_signal":
        # 明确失败 → 返回失败
        LOG.warning(f"[operation_failed] {action}: {failure_reason[:80]}")
        await _close_dialog(page)
        return make_error("operation_failed", ...)
    else:
        # 无信号 → 标记成功
        LOG.info(f"[no_signal] {action} 无明确信号，标记为成功")
        await _close_dialog(page)
# result = {"success": True, ...}
```

### 修改 3：navigation_executor.py 文件上传检测 (line 650)

```python
# 修改前:
if not form_field_details:
    nav_info["submit_result"] = {"success": True, ...}  # 浏览页面
    return nav_info

# 修改后:
if not form_field_details:
    has_upload = await page.evaluate("""() => {
        const fileInput = document.querySelector('input[type="file"]');
        const uploadDiv = document.querySelector('.el-upload, .ant-upload, .upload-area');
        return !!(fileInput || uploadDiv);
    }""")
    if has_upload:
        nav_info["error_type"] = "env_dependency"
        nav_info["error_text"] = "文件上传操作需要外部文件，无法自动完成"
        return nav_info
    nav_info["submit_result"] = {"success": True, ...}
    return nav_info
```

### 修改 4：所有调用方同步为 tuple 解包

| 文件 | 行号 | 修改 |
|------|------|------|
| crud_executor.py | 1805 | `success, _detail = await _verify_operation_success(...)` |
| crud_executor.py | 2040 | `success, _detail = await _verify_operation_success(...)` |
| crud_executor.py | 2688 | `success, _detail = await _verify_operation_success(...)` |
| crud_executor.py | 3363 | `success, _detail = await _verify_operation_success(...)` |
| crud_executor.py | 4386 | `_instant_success, _ = await _verify_operation_success(...)` |
| crud_executor.py | 4632 | `success, detail = await _verify_operation_success(...)` |
| crud_executor.py | 4771 | `success, _detail = await _verify_operation_success(...)` |
| navigation_executor.py | 618 | `success, _detail = await _verify_operation_success(...)` |
| navigation_executor.py | 1311 | `success, _detail = await _verify_operation_success(...)` |

---

## 判定规则定义

### 优先级

```
失败信号 > 成功信号 > 处理中 > 无信号
```

### 各 failure_type 的处理

| failure_type | 判定 | 理由 |
|---|---|---|
| `notification` | ❌ 失败 | toast 中有失败关键词 |
| `css:...` | ❌ 失败 | CSS 选择器检测到失败 |
| `captured` | ❌ 失败 | MutationObserver 捕获到失败 |
| `dialog` | ❌ 失败 | dialog 中有失败文本 |
| `form_validation` | ❌ 失败 | 表单校验错误 |
| `backend` | ❌ 失败 | 其他失败 |
| `no_signal` | ✅ 成功 | 按钮已点击/表单已提交，无报错即成功 |

---

## 全路径覆盖验证

| 路径 | 场景 | 修改后行为 |
|------|------|-----------|
| Path 1: new_tab | 成功 | ✅ 不变 |
| Path 1: new_tab | 失败 | ✅ 不变 |
| Path 2: page_nav | 导入（文件上传） | ✅ **修复 Bug-A** → env_dependency |
| Path 2: page_nav | 正常跨页面表单 | ✅ 不变 |
| Path 2: page_nav | 纯展示页面 | ✅ 不变 |
| Path 3: upload | 文件上传弹窗 | ✅ 不变 |
| Path 4: dialog+form | 迁移失败 | ✅ **修复 Bug-B** → operation_failed |
| Path 4: dialog+form | 重置密码成功 | ✅ 不变 |
| Path 5: dialog无form | 确认操作成功 | ✅ 不变 |
| Path 5: dialog无form | 确认操作失败 | ✅ 新增保护 |
| Path 6: message-box | 确认框操作成功 | ✅ 不变 |
| Path 6: message-box | 确认框操作失败 | ✅ 新增保护 |
| Path 7: 无dialog | toast 成功 | ✅ 不变 |
| Path 7: 无dialog | toast 失败 | ✅ 不变 |
| Path 7: 无dialog | 无信号 | ✅ 不变 |

---

## 测试结果

```
675 passed, 0 failed
```

---

## 修改文件清单

| 文件 | 修改点 | 改动量 |
|------|--------|--------|
| `base_executor.py:842-863` | 返回 tuple | 3 行 |
| `crud_executor.py:1805` | tuple 解包 | 1 行 |
| `crud_executor.py:2040` | tuple 解包 | 1 行 |
| `crud_executor.py:2688` | tuple 解包 | 1 行 |
| `crud_executor.py:3363` | tuple 解包 | 1 行 |
| `crud_executor.py:4386` | tuple 解包 | 1 行 |
| `crud_executor.py:4632-4653` | 统一判定逻辑 | ~15 行 |
| `crud_executor.py:4771` | tuple 解包 | 1 行 |
| `navigation_executor.py:618` | tuple 解包 | 1 行 |
| `navigation_executor.py:650` | 文件上传检测 | ~15 行 |
| `navigation_executor.py:1311` | tuple 解包 | 1 行 |

**Stage 2 (`replay_engine.py`)**：已确认 `_step_assert_success` 已正确使用 `(success, detail)` tuple，无需修改。

---

## 预期效果

修复后重新运行 Stage 1 用户管理：

| 操作 | 修复前 | 修复后 |
|------|--------|--------|
| 导入 | ❌ 误判成功 | ✅ env_dependency 失败 |
| 迁移 | ❌ 误判成功 | ✅ operation_failed 失败 |
| 其他操作 | ✅ 正确 | ✅ 不变 |
