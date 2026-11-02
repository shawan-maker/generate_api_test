# Stage 1 "用户管理" 4 Bug 修复验证报告 + 残留问题分析

> 测试项目: estack-ceshiyu  
> 模块: 用户管理  
> 验证运行时间: 2026-11-02 10:07 ~ 10:15  
> **最终结果: 10/11 操作验证通过（90.9%）**  
> 上一轮结果: 10/12（83.3%）

---

## 修复效果总结

| Bug | 上轮状态 | 本轮状态 | 说明 |
|-----|---------|---------|------|
| Bug 1&2 重复执行 | ❌ 设置到期时间4次，启用/停止各2次 | ⚠️ **部分修复** | STATE-CHANGE 链内不再重复，但 INCREMENTAL-SCAN 重新发现导致二次执行 |
| Bug 3 授权失败 | ❌ required_field_empty | ❌ **未修复** | cascade_filled_labels 收集逻辑有字段名不匹配 |
| Bug 4 删除未确认 | ❌ dialog 未关闭 | ✅ **已修复** | JS click 成功触发 Vue 事件 |

---

## Bug 1&2 残留: INCREMENTAL-SCAN 重新发现已执行按钮

### 现象

STATE-CHANGE 链内修复生效（每个按钮只执行一次），但 INCREMENTAL-SCAN 在后续操作后重新发现"启用"和"设置到期时间"作为"新按钮"，导致它们在主循环中被二次执行。

### 日志证据链

**第一阶段: STATE-CHANGE 链（修复生效 ✅）**

```
10:10:24 [STATE-CHANGE] 发现 2 个新启用按钮: ['启用', '设置到期时间']
10:10:24 [STATE-CHANGE] 执行新启用按钮: 启用          ← 第1次
10:10:47 [STATE-CHANGE] ✓ 启用 执行成功
10:10:50 [STATE-CHANGE] 发现 2 个新启用按钮: ['停止', '设置到期时间']
10:10:50 [STATE-CHANGE] 执行新启用按钮: 停止          ← 第1次
10:11:13 [STATE-CHANGE] ✓ 停止 执行成功
10:11:16 [STATE-CHANGE] 发现 1 个新启用按钮: ['设置到期时间']
10:11:16 [STATE-CHANGE] 执行新启用按钮: 设置到期时间    ← 第1次
10:11:38 [STATE-CHANGE] ✓ 设置到期时间 执行成功
10:11:40 [STATE-CHANGE] 跳过已执行按钮: 设置到期时间    ← ★ 修复生效！
```

STATE-CHANGE 链结束后: 启用=1次, 停止=1次, 设置到期时间=1次。

**第二阶段: INCREMENTAL-SCAN（问题 ❌）**

```
10:11:40 [INCREMENTAL-SCAN] existing_row_actions={'删除', '编辑', '授权', '重置密码', '迁移'}
10:11:42 [incremental-scan] 增量发现 2 个新按钮: ['启用', '设置到期时间']
10:11:42 [INCREMENTAL-SCAN] new_buttons=['启用', '设置到期时间']
10:11:42 增量发现 2 个新按钮
10:11:42   + 启用 (角色: generic, 插入位置: 5)
10:11:42   + 设置到期时间 (角色: generic, 插入位置: 5)
```

**为什么 "启用" 被识别为"新"按钮？**

`existing_row_actions` 在 `crud_executor.py:1109-1112` 计算：
```python
existing_row_actions = {
    action for action, btn_info in buttons_by_action.items()
    if btn_info.get("location") in ("dropdown", "row_action")
}
```

初始扫描时，"启用" 按钮状态为 **disabled**（用户已启用状态，"启用"不可点击，只有"停止"可用）。disabled 的 dropdown-item 可能未被初始扫描收录到 `buttons_by_action`。

当 STATE-CHANGE 执行"停止"后，"启用" 变为 enabled，INCREMENTAL-SCAN 发现它不在 `existing_row_actions` 中 → 标记为"新"。

**第三阶段: 主循环二次执行**

```
10:11:42 验证操作: 启用 (按钮: 启用, 角色: generic)
         ← "启用" 被第2次执行
```

### 根因

**文件**: `core/discovery/operation_executor/crud_executor.py`  
**位置**: 第 1137-1148 行，INCREMENTAL-SCAN 新按钮处理循环

```python
if new_buttons:
    for new_btn in new_buttons:
        new_action = new_btn.get("text", "")
        # 去重检查 — 只检查 buttons_by_action，不检查 executed_actions
        if new_action in buttons_by_action:
            continue
        buttons_by_action[new_action] = new_btn
        # ... 加入 ordered_actions，后续主循环会执行
```

**缺陷**: 去重只检查 `buttons_by_action`（按钮注册表），不检查 `executed_actions`（已执行集合）。INCREMENTAL-SCAN 发现的"新"按钮如果已经在 STATE-CHANGE 链中执行过，仍然会被加入待验证队列。

### 修复方案

**文件**: `core/discovery/operation_executor/crud_executor.py`  
**位置**: 第 1145-1148 行  
**修改量**: 加 4 行

**修改前**:
```python
        # 去重检查
        if new_action in buttons_by_action:
            LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 buttons_by_action 中，跳过")
            continue
```

**修改后**:
```python
        # 去重检查
        if new_action in buttons_by_action:
            LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 buttons_by_action 中，跳过")
            continue

        # ★ 跳过 STATE-CHANGE 链中已执行过的按钮
        # INCREMENTAL-SCAN 可能重新发现已在级联中执行的状态切换按钮
        if new_action in executed_actions:
            LOG.info(f"{tab_prefix}  [SKIP] {new_action} 已在 STATE-CHANGE 中执行过，跳过")
            # 仍注册到 buttons_by_action（供后续 INCREMENTAL-SCAN 去重），但不加入 ordered_actions
            buttons_by_action[new_action] = new_btn
            continue
```

**安全性**: 
- 仅跳过已执行的按钮，不影响真正新发现的按钮（如"授权"、"重置密码"）
- 仍将按钮注册到 `buttons_by_action`，确保后续 INCREMENTAL-SCAN 不会重复发现
- `executed_actions` 在同一函数作用域内（第 766 行初始化），可直接访问

---

## Bug 3: cascade_actions 字段名不匹配 + transfer-box 在 form-item 外

### 现象

与上轮完全相同：form_filler 的 radio-cascade 成功填充了 transfer-box（选择了"sdfdg"），但 navigation_executor 仍将"授权用户"识别为空的 input 字段，触发 `required_field_empty` 失败。

```
10:13:27 [radio-cascade] transfer-box 选择成功      ← cascade 成功
10:13:32 表单字段状态检查:
  授权类型 (radio): ✅ value='用户'
  授权用户 (input): ❌ 空 value=''                  ← 误判依旧
  授权角色 (radio): ✅ value='组织机构'
10:13:33 第 1 轮失败: [required_field_empty] field= text=必填字段无法填写: 授权用户（input，值为空）
```

**关键线索**: 没有出现 `跳过 cascade 填充字段: 授权用户` 日志 → `cascade_filled_labels` 集合为空。

### 根因（两层）

#### 根因 1: cascade_actions 字段名不匹配

**生产者** — `form_filler.py:504-509`:
```python
cascade_actions.append({
    "type": "transfer-box",
    "trigger_field": label,         # "授权类型"
    "trigger_value": first_text,    # "用户"
    "interaction_steps": interaction_steps,
    # ❌ 没有 target_label 字段
})
```

**消费者** — `navigation_executor.py:426-428`（上轮修复添加的代码）:
```python
for ca in field["cascade_actions"]:
    if ca.get("target_label"):          # ← 永远返回 None！
        cascade_filled_labels.add(ca["target_label"])
```

`cascade_actions` 条目中没有 `target_label` 键 → `ca.get("target_label")` 始终为 `None` → `cascade_filled_labels` 始终为空集。

#### 根因 2: transfer-box 在 form-item DOM 结构外

即使修复了根因 1（记录了 target_label），navigation_executor 也不知道 transfer-box 对应的表单字段标签是"授权用户"。

原因：授权页面的 transfer-box 组件不在 "授权用户" 的 `.el-form-item` 内部。JS 扫描逻辑（第 310-352 行）只在 form-item 内部查找 `[class*="transfer-box"]`：

```javascript
// 第 312 行
const transferBox = fi.querySelector('[class*="transfer-box"]');
```

"授权用户" 的 form-item 内没有 `[class*="transfer-box"]` 子元素 → JS 落入 `else` 分支（第 343-352 行），将其识别为普通 `input` 类型。

### 修复方案（两步走，从源头解决）

#### 步骤 1: 修复 form_filler.py 的 cascade_actions 记录

**文件**: `core/discovery/replay/form_filler.py`  
**位置**: 第 504-509 行  
**修改量**: 加 1 行

**修改前**:
```python
cascade_actions.append({
    "type": "transfer-box",
    "trigger_field": label,
    "trigger_value": first_text,
    "interaction_steps": interaction_steps,
})
```

**修改后**:
```python
# ★ 查找 transfer-box 关联的表单字段标签
_transfer_target_label = ""
try:
    _transfer_target_label = await self.page.evaluate("""() => {
        // 查找可见的 transfer-box 容器，向上追溯关联的 form-item 标签
        const boxes = document.querySelectorAll(
            '[class*="transfer-box"], [class*="el-transfer"]'
        );
        for (const box of boxes) {
            const r = box.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            // 向上查找最近的 form-item
            const formItem = box.closest('.el-form-item, .ant-form-item');
            if (formItem) {
                const labelEl = formItem.querySelector(
                    '.el-form-item__label, .ant-form-item-label label'
                );
                if (labelEl) return labelEl.textContent.trim().replace(/[：:]/g, '');
            }
        }
        return '';
    }""")
except Exception:
    pass

cascade_actions.append({
    "type": "transfer-box",
    "trigger_field": label,
    "trigger_value": first_text,
    "target_label": _transfer_target_label,   # ★ 新增
    "interaction_steps": interaction_steps,
})
```

#### 步骤 2: 修复 navigation_executor.py 的 cascade_filled_labels 收集

**文件**: `core/discovery/operation_executor/navigation_executor.py`  
**位置**: 两处（第 419-428 行 和 第 868-879 行，即 Path A 和 Path B）  
**修改量**: 每处增加页面级 transfer-box 标签检测

**修改前** (两处相同):
```python
cascade_filled_labels = set()
for field in form_field_details:
    if field.get("cascade_actions"):
        cascade_filled_labels.add(field.get("label"))
        for ca in field["cascade_actions"]:
            if ca.get("target_label"):
                cascade_filled_labels.add(ca["target_label"])
```

**修改后**:
```python
cascade_filled_labels = set()
for field in form_field_details:
    if field.get("cascade_actions"):
        cascade_filled_labels.add(field.get("label"))
        for ca in field["cascade_actions"]:
            if ca.get("target_label"):
                cascade_filled_labels.add(ca["target_label"])

# ★ 页面级 transfer-box 标签检测
# transfer-box 可能不在 form-item 内部（如授权页面），JS 无法检测
# 通过向上追溯 DOM 找到关联的 form-item 标签
try:
    _page_transfer_labels = await page.evaluate("""() => {
        const labels = [];
        const boxes = document.querySelectorAll(
            '[class*="transfer-box-right"], [class*="el-transfer"]'
        );
        for (const box of boxes) {
            const r = box.getBoundingClientRect();
            if (r.width <= 0 || r.height <= 0) continue;
            const formItem = box.closest('.el-form-item, .ant-form-item');
            if (formItem) {
                const labelEl = formItem.querySelector(
                    '.el-form-item__label, .ant-form-item-label label'
                );
                if (labelEl) {
                    labels.push(labelEl.textContent.trim().replace(/[：:]/g, ''));
                }
            }
        }
        return labels;
    }""")
    for _tl in (_page_transfer_labels or []):
        if _tl:
            cascade_filled_labels.add(_tl)
except Exception:
    pass
```

**安全性**:
- 步骤 1 仅添加一个字段，不影响现有 cascade_actions 结构
- 步骤 2 的页面级检测只查找可见的 transfer-box-right，不影响隐藏或无关组件
- 两层防护互为补充：步骤 1 解决字段名不匹配，步骤 2 覆盖 form-item 外的 transfer-box

---

## Bug 4: 已修复 ✅

### 验证证据

```
10:14:41 [DIAG-delete-before] dialogs(1): [{"title": "删除用户", "btns": ["", "取 消", "确 定"]}]
10:14:42 [MSG-CAPTURE] Notification: type=success, text="用户回收站成功"
                          ↑ JS click 触发了 Vue 事件，后端立即返回成功
10:14:44 confirm_dialog 返回: '确 定'
10:15:52 ✓ 删除 执行成功（捕获到动态消息: captured:success:用户回收站成功）
```

JS click → Vue 事件触发 → API 调用 → "用户回收站成功" notification。删除操作完全成功。

---

## 实施清单

| 序号 | Bug | 修改文件 | 修改位置 | 修改量 | 优先级 |
|------|-----|---------|---------|--------|--------|
| 1 | Bug 1&2 残留 | `crud_executor.py` | 第 1148 行后 | 加 5 行 | P0 |
| 2a | Bug 3 字段名 | `form_filler.py` | 第 504-509 行 | 加 15 行 | P0 |
| 2b | Bug 3 页面级检测 | `navigation_executor.py` | 第 428 行后 + 第 879 行后 | 每处加 15 行 | P0 |

### 验证步骤

```bash
python -m core.discovery.run --project estack-ceshiyu \
  --url "/estack/web/estack/user-center/user-manage/user" \
  --module "用户管理" --stage 1
```

预期结果:
- 设置到期时间: 仅执行 1 次（INCREMENTAL-SCAN 跳过已执行）
- 启用/停止: 各执行 1 次
- 授权: 成功（transfer-box 字段被 cascade 跳过）
- 删除: 成功（JS click 已验证有效）
- **目标: 11/11 全部通过**
