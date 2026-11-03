# Stage 2 无写 API 根因分析报告

## 问题现象

Stage 2 的 8 个操作（编辑、删除、冻结、启用、授权、设置到期时间、重置密码）只捕获到 GET API，没有写操作 API（PUT/DELETE/POST）。

## 根因结论

**根本原因：Stage 1 录制时，`系统角色` 字段有默认值导致 Stage 1 跳过了实际选择操作，只记录了 `check_default_value` 步骤；Stage 2 回放时该字段没有默认值，导致创建用户失败，测试用户未创建，后续所有操作因找不到测试数据而跳过，未触发写操作 API。**

## 根因链路

### 第一环：创建用户失败 → 测试用户未创建

#### Stage 1 录制时的行为

1. **发现 `系统角色` 字段有默认值**
   - `form_filler.py` 第 2285-2310 行：检查 el-select 是否已有默认值
   - 发现单选模式已有默认值 "普通用户"
   - `has_default.get("hasValue") = True`

2. **提前返回，跳过实际选择操作**
   - `form_filler.py` 第 2312-2331 行：
   ```python
   if has_default.get("hasValue"):
       val = has_default.get("value", "")
       LOG.info(f"    el-select {label}: 单选已有默认值 '{val}'，跳过")
       
       # 只记录检查默认值步骤
       interaction_steps.append({
           "op": "check_default_value",
           "selector": selector,
           "desc": f"检查默认值: {val}",
           "value": val
       })
       
       return True, {
           "execution_path": "has_default_value",
           "interaction_steps": interaction_steps,
           # ❌ 没有后续的选择步骤（展开下拉框、点击选项）
       }
   ```

3. **Playbook 记录不完整**
   ```json
   {
     "label": "系统角色",
     "type": "select",
     "interaction_steps": [
       {
         "op": "check_default_value",
         "desc": "检查默认值: 普通用户",
         "value": "普通用户"
       }
       // ❌ 缺少: click (展开下拉框), click (选择选项)
     ]
   }
   ```

#### Stage 2 回放时的行为

1. **执行 `check_default_value` 操作**
   - `replay_engine.py` 第 781-807 行：
   ```python
   elif op == "check_default_value":
       selector = step.get("selector")
       
       # 检查是否有默认值
       has_value = await page.evaluate("""(selector) => {
           const sel = document.querySelector(selector);
           if (!sel) return false;
           
           // 多选模式（tag）
           const tags = sel.querySelectorAll('.el-tag');
           if (tags && tags.length > 0) return true;
           
           // 单选模式
           const input = sel.querySelector('.el-input__inner');
           const selectedLabel = sel.querySelector('.el-select__selected-item');
           const value = selectedLabel?.textContent?.trim() || input?.value || '';
           return !!value;
       }""", selector)
       
       if has_value:
           LOG.info(f"    [step {i}/{len(steps)}] {desc}: 已有默认值，跳过后续步骤")
           return True  # 提前返回
       else:
           LOG.info(f"    [step {i}/{len(steps)}] {desc}: 无默认值，继续执行")
           # ❌ 但没有后续步骤可以执行！
   ```

2. **实际场景：字段没有默认值**
   - Stage 2 的环境可能与 Stage 1 不同（环境重置、数据清理）
   - `has_value = False`
   - 日志：`"无默认值，继续执行"`
   - 但 `interaction_steps` 中只有 `check_default_value`，没有后续步骤
   - 循环结束，返回 `True`（误导性的成功）

3. **表单提交失败**
   - 字段实际为空
   - 点击提交按钮后，表单验证失败
   - 错误信息：`"请选择系统角色"`

4. **日志证据**
   ```
   18:40:35 [replay_engine]     [step 1/1] 检查默认值: 普通用户: 无默认值，继续执行
   18:40:36 [replay_engine]     [interaction_steps] 系统角色: ✓ 执行成功
   18:40:53 [success_verifier] ✗ 表单发现失败 (form_validation): 请选择系统角色
   18:40:53 [capture_apis]   ⚠️ 创建用户 未检测到成功消息（assertion_failed）
   ```

### 第二环：后续操作找不到测试数据

1. **所有后续操作依赖测试用户 `AT_test_616029`**
   - 编辑、删除、冻结、启用、授权、设置到期时间、重置密码
   - 这些操作的第一步都是 `find_row`：查找包含标记 `AT_test_616029` 的数据行

2. **测试用户不存在 → 查找失败**
   - `replay_engine.py` 执行 `find_row` 步骤
   - 在表格中搜索 `AT_test_616029`
   - 未找到匹配的行
   - 抛出异常或返回失败

3. **跳过后续步骤**
   - `find_row` 失败后，后续步骤（点击、填表、提交）全部跳过
   - **没有触发任何写操作 API**
   - 只捕获到了页面加载时的 GET 请求（列表刷新）

4. **日志证据**
   ```
   18:42:09 ▶ 回放 编辑
   18:42:17 [replay_engine] [find_row] 查找数据行，标记: AT_test_616029
   18:42:17 [replay_engine] [find_row] ✗ 未找到匹配的数据行
   18:42:17 [capture_apis]   ⚠️ 编辑 执行失败 (find_row_failed)，跳过后续步骤
   
   18:44:09 ▶ 回放 删除
   18:44:17 [replay_engine] [find_row] 查找数据行，标记: AT_test_616029
   18:44:17 [replay_engine] [find_row] ✗ 未找到匹配的数据行
   18:44:17 [capture_apis]   ⚠️ 删除 执行失败 (find_row_failed)，跳过后续步骤
   
   ... (其他操作相同)
   ```

## 证据汇总

### 证据 1：Playbook 中 `系统角色` 的 interaction_steps 不完整

```json
{
  "label": "系统角色",
  "type": "select",
  "kb_category": "el-select",
  "interaction_steps": [
    {
      "op": "check_default_value",
      "desc": "检查默认值: 普通用户",
      "value": "普通用户"
    }
  ]
  // ❌ 缺少展开下拉框和选择选项的步骤
}
```

### 证据 2：Stage 2 日志显示创建用户失败

```
18:40:53 [success_verifier] ✗ 表单发现失败 (form_validation): 请选择系统角色
```

### 证据 3：后续操作因 find_row 失败而跳过

```
18:42:17 [replay_engine] [find_row] ✗ 未找到匹配的数据行
18:42:17 [capture_apis]   ⚠️ 编辑 执行失败 (find_row_failed)
```

### 证据 4：代码逻辑确认提前返回

`form_filler.py` 第 2312-2331 行：
- 当检测到默认值时，只记录 `check_default_value` 步骤
- 立即 `return True`，不记录后续选择步骤

## 解决方案

### 方案 A：Stage 1 始终记录完整操作（推荐）

**修改 `form_filler.py` 第 2312 行的逻辑**：
- 即使字段有默认值，也记录完整的选择步骤（展开下拉框、点击选项）
- 确保 Stage 2 可以完全重放 Stage 1 的操作
- 不依赖环境一致性假设

**优点**：
- 最简单、最稳健
- 确保 Playbook 包含完整的操作步骤
- 不依赖 Stage 2 环境是否与 Stage 1 一致

**缺点**：
- 录制时间略增加（多执行几步操作）
- Playbook 文件略大

### 方案 B：Stage 2 增加 fallback 机制

**修改 `replay_engine.py` 第 803-807 行**：
- 当 `check_default_value` 返回 `False` 且没有后续步骤时，自动执行选择操作
- 需要在 `check_default_value` 操作中增加 fallback 逻辑

**优点**：
- 不需要重新录制 Stage 1
- 可以修复现有的 Playbook

**缺点**：
- 增加 replay_engine 的复杂度
- 需要在回放时动态生成选择步骤（可能不准确）

### 方案 C：Playbook 增加环境校验

**在 Playbook 开头增加环境校验步骤**：
- 检查关键字段（如 `系统角色`）的当前状态
- 如果状态不符合预期，执行完整的选择操作

**优点**：
- 可以动态适应不同环境
- 提高 Playbook 的鲁棒性

**缺点**：
- 需要修改 Playbook 生成和执行逻辑
- 增加复杂度

### 推荐方案：A

**理由**：
1. **简单直接**：修改 Stage 1 的录制逻辑，确保记录完整操作
2. **稳健可靠**：不依赖环境一致性假设，Stage 2 可以完全重放
3. **符合原则**：Stage 1 记录了什么 → Stage 2 就回放什么
4. **易于维护**：不需要在 Stage 2 增加复杂的 fallback 逻辑

## 实施建议

### 短期（立即）

1. **修改 `form_filler.py`**：
   - 第 2312 行：移除提前返回逻辑
   - 即使有默认值，也继续执行展开和选择操作
   - 记录完整步骤到 `interaction_steps`

2. **重新录制 Stage 1**：
   - 运行 `python -m core.discovery.run --project estack-ceshiyu --module "用户管理" --stage 1`
   - 验证 Playbook 中 `系统角色` 的 `interaction_steps` 包含完整步骤

3. **重新运行 Stage 2**：
   - 运行 `python -m core.discovery.run --project estack-ceshiyu --module "用户管理" --stage 2`
   - 验证创建用户成功，后续操作可以捕获写操作 API

### 中期（1-2 周）

1. **增加 Playbook 验证机制**：
   - Stage 2 执行前，检查 Playbook 的完整性
   - 对于关键字段（如 `系统角色`），确保 `interaction_steps` 包含选择步骤

2. **增加 Stage 2 错误诊断**：
   - 当 `find_row` 失败时，明确提示"测试数据未创建"
   - 提供建议：检查创建用户操作是否成功

### 长期（1 个月）

1. **Playbook 环境无关性**：
   - 研究如何让 Playbook 不依赖 Stage 1 录制时的环境状态
   - 增加动态适应机制

2. **Stage 1 录制策略优化**：
   - 对于有默认值的字段，提供配置选项（跳过/强制选择）
   - 允许用户自定义录制行为

## 结论

**根本原因**：Stage 1 录制时，`系统角色` 字段有默认值导致 Stage 1 跳过了实际选择操作，只记录了 `check_default_value` 步骤；Stage 2 回放时该字段没有默认值，导致创建用户失败，测试用户未创建，后续所有操作因找不到测试数据而跳过，未触发写操作 API。

**解决方案**：修改 `form_filler.py`，即使字段有默认值，也记录完整的选择步骤，确保 Stage 2 可以完全重放 Stage 1 的操作。

**预期效果**：重新录制后，创建用户操作成功，测试用户被创建，后续 8 个操作可以正常执行并捕获写操作 API。
