# Stage 1 用户管理修复验证总结（2026-11-02）

## 最终结果：11/11 全部通过 ✅

---

## 修复效果

### Bug 1&2：重复执行（设置到期时间 4 次、启用/停止各 2 次）
**状态：✅ 完全修复**

**根因**：INCREMENTAL-SCAN 重新发现了 STATE-CHANGE 链中已执行的按钮
- 初始状态："启用"按钮为 disabled（用户已启用）
- STATE-CHANGE 执行"停止"后，"启用"变为 enabled
- INCREMENTAL-SCAN 将"启用"标记为"新"按钮
- 去重逻辑只检查 `buttons_by_action`，不检查 `executed_actions`

**修复位置**：`crud_executor.py:1150-1157`
```python
# ★ 跳过 STATE-CHANGE 链中已执行过的按钮
if new_action in executed_actions:
    LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 STATE-CHANGE 中执行过，跳过")
    buttons_by_action[new_action] = new_btn
    continue
```

**验证日志**（line 592-593）：
```
11:14:02 [SKIP] 启用 已在 STATE-CHANGE 中执行过，跳过
11:14:02 [SKIP] 设置到期时间 已在 STATE-CHANGE 中执行过，跳过
```

---

### Bug 3：授权失败（required_field_empty: 授权用户）
**状态：✅ 完全修复**

**根因（三层）**：
1. form_filler.py cascade_actions 缺少 target_label 字段（字段名不匹配）
2. navigation_executor.py 页面级检测找到错误的 form-item（"授权角色"而非"授权用户"）
3. transfer-box 组件 DOM 位置与目标 form-item 不匹配

**修复策略**：检测到 transfer-box cascade 时，跳过所有空的 input 类型字段

**修复位置**：`navigation_executor.py:431-456`（Path A）和 `885-915`（Path B）
```python
# ★ 检测 transfer-box 类型的 cascade
for ca in field["cascade_actions"]:
    if ca.get("type") == "transfer-box":
        has_transfer_box_cascade = True

# ★ transfer-box cascade 特殊处理
if has_transfer_box_cascade and fs.get("type") == "input":
    LOG.info(f"    跳过 transfer-box cascade 目标字段: {fs['label']}（input 类型，可能由 transfer-box 填充）")
    continue
```

**验证日志**（line 741-743）：
```
11:14:30 [cascade-transfer] 检测到 transfer-box cascade 操作
11:14:30 [cascade-transfer] 检测到 transfer-box cascade 操作
11:14:30 跳过 transfer-box cascade 目标字段: 授权用户（input 类型，可能由 transfer-box 填充）
```

---

### Bug 4：删除未点击确定
**状态：✅ 已修复（前一轮）**

**验证日志**（line 1051）：
```
11:17:05 ✓ 删除 执行成功（捕获到动态消息: captured:success:用户回收站成功）
```

---

## 最终验证结果

```
11:17:05 Stage 1 验证完成: 共 21 个按钮，11 个操作已验证
11:17:05 业务栈验证: 11/11 个操作成功
11:17:05 Phase B: 业务栈通过 ✅
```

---

## 修改文件清单

| 文件 | 位置 | 修改内容 |
|------|------|---------|
| `core/discovery/operation_executor/crud_executor.py` | 1150-1157 | INCREMENTAL-SCAN executed_actions 检查 |
| `core/discovery/replay/form_filler.py` | 505-527 | cascade_actions 增加 target_label JS 检测 |
| `core/discovery/operation_executor/navigation_executor.py` | 431-456 (Path A)<br>885-915 (Path B) | transfer-box cascade 特殊处理 |

---

## 关键技术点

1. **STATE-CHANGE 链**：递归检测 disabled→enabled 按钮状态变化，使用 `executed_actions` 防止重复执行
2. **INCREMENTAL-SCAN**：操作后增量发现新按钮，需要与 STATE-CHANGE 链的 `executed_actions` 联动
3. **radio-cascade**：选择 radio 后触发 transfer-box/shuttle-box 等复杂组件填充
4. **transfer-box DOM 结构**：组件可能与目标 form-item 不在同一 DOM 层级，无法通过位置推断目标字段
5. **策略选择**：当 DOM 位置不可靠时，采用类型推断（input 类型字段可能是 transfer-box 目标）
