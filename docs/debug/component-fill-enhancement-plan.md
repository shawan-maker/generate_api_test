# 组件填充增强方案 — 详细设计文档

> 日期：2026-10-14
> 范围：5 个组件模式的识别与填充增强
> 状态：待评审

---

## 一、问题全景

### 1.1 触发场景

黑白名单创建页面（page-nav 模式，URL: `/black-list/add`）包含 6 个逻辑表单字段，
当前系统扫描出 11 个物理字段，导致：

- IP 地址填充了不合法的文本值（`AT_test_xxx`）
- el-select 字段在预热阶段未填充
- 条件隐藏字段（时间段 date-picker）未被发现
- 标签多条目模式完全未识别

### 1.2 完整字段结构还原

| # | 逻辑字段 | 扫描结果 | 真实 DOM 结构 | 问题 |
|---|---------|---------|--------------|------|
| 0 | IP地址 | 字段[0] `input-generic` | 4 个 input（IP 四段） + "添加"按钮 + 标签列表 | 拆分为单个 input |
| 1 | IP地址区间 | 字段[1] `input-generic` | 8 个 input（起止 IP 各 4 段） + "添加"按钮 | 拆分为单个 input |
| 2-6 | IP地址/网段 | 字段[2] `el-select` + 字段[3-6] `input-generic` ×4 | 1 个 el-select（CIDR 0-32） + 4 个 input + "添加"按钮 | select 和 input 分离 |
| 7-10 | 授权主体 | 字段[7] `el-select` + 字段[8] `input-generic` + 字段[9-10] 重复 | 1 个 el-select（类型选择） + 1 个 el-select 树形多选 + "添加"按钮 | 多选未识别 |
| ? | 选择时间 | **未出现** | radio-group（全天/时间段）| radio 被误判为 multi-input |
| ? | 时间段1 | **未出现**（隐藏） | date-picker daterange | display:none 被跳过 |

### 1.3 问题分类

| 问题 | 涉及组件 | 核心缺失 |
|------|---------|---------|
| P1: IP 多段输入 | ip-input | 识别 + 分段填充 |
| P2: 标签多条目 | tag-multi-entry | 识别 + 添加条目流程 |
| P3: el-select 多选 | el-select-multiple | 扫描识别 + 多选策略 |
| P4: radio 条件显隐 | radio-cascade-fields | radio 误分类修复 + 条件字段发现 |
| P5: el-select 预热 | el-select (all) | 预热流程补全 |

---

## 二、现状分析（含代码定位）

### 2.1 P1: IP 多段输入 — 现状

**扫描阶段**（`form_filler.py` JS，优先级 8 "复合输入组"检测，约 line 1028）：

```javascript
// 当前逻辑：多个 input 时选"主输入"
const allInputs = Array.from(fi.querySelectorAll('input.el-input__inner'))
    .filter(inp => !inp.readOnly && inp.offsetWidth > 0);
if (allInputs.length >= 2) {
    // 选择 placeholder 含"请输入" 或 宽度最大的 input
    mainInput = allInputs.find(inp => (inp.placeholder || '').includes('请输入'))
                || allInputs.reduce((a, b) => a.offsetWidth >= b.offsetWidth ? a : b);
    // 只记录 1 个 input → 丢失其余 3/7 个 input
}
```

**问题**：4 个 IP 段 input 都没有 placeholder 且宽度相同，选择第一个 input 填充文本值。

**填充阶段**：`page.fill(selector, "AT_test_xxx")` 将文本写入第一个 IP 段 input，
Vue v-model 只更新了一段，后端校验 IP 格式失败。

### 2.2 P2: 标签多条目 — 现状

**完全未识别**。每个字段（IP地址、IP地址区间、IP地址/网段、授权主体）都有：
- "添加" 按钮（`button.el-button--text`，text="添加"）
- 标签列表（`.el-tag`）
- 提示文本（"您还可以添加N个标签"）

当前 `scan_form_fields` JS 不检测这些元素。

### 2.3 P3: el-select 多选 — 现状

**数据流断裂**：

| 层级 | 检测多选？ | 使用多选信息？ |
|------|----------|-------------|
| `scan_form_fields` JS (form_filler.py:889) | ❌ 不检测 | — |
| `navigation_executor.py` (lines 195, 600) | ✅ 检测 `.el-tag` | 写入 `field_states` |
| `playbook_builder/builder.py` (line 1459) | — | ✅ 存储 `is_multi_select` |
| `_execute_select_with_details` (line 1684) | ✅ 检测 `.el-tag` | ❌ 仅用于跳过已填充字段 |
| `replay_engine.py` | ❌ 不读取 | ❌ 不区分单选/多选 |

**关键代码**（`_execute_select_with_details`，约 line 1720）：

```python
# 多选和单选走完全相同的填充流程：
# 展开 → 可编辑检测 → 搜索/选择 → 验证
# 始终只选择 1 个选项
```

**影响**：授权管理、用户管理中的多选 el-select 只选了第一个选项。

### 2.4 P4: radio 条件显隐 — 现状

**两个子问题**：

**子问题 4a：radio 被误分类为 multi-input**

`scan_form_fields` JS 中 radio 检测（优先级 5）在 multi-input 检测（优先级 8）**之前**，
但 radio 的 `<input type="radio">` 也是 `input.el-input__inner` 的子类，
当 radio 的 `getBoundingClientRect().width > 0` 时，它被计入 `allInputs`。

对于"选择时间"字段：包含 2 个 radio input（"全天"和"时间段"），
`allInputs.length >= 2` 触发 multi-input 检测，在 radio 检测（优先级 5）之前就被处理了。

**验证方法**：需要确认 radio input 是否有 `el-input__inner` class。
→ **增加调试日志确认**（见第五节）。

**子问题 4b：条件字段未发现**

`scan_form_fields` 跳过 `getBoundingClientRect()` 为零的元素（line 951-952），
"时间段1" date-picker 因 `display:none` 被跳过。

**已有但不完整的机制**（form_filler.py line 150-161）：

```python
# radio 级联检测 — 只 log 不处理
post_fields = await scan_form_fields(self.page)
new_fields = [f for f in post_fields if f['label'] not in current_labels]
if new_fields:
    LOG.info(f"    [radio-cascade] 新增 {len(new_fields)} 个字段")
    # ❌ 新字段没有加入 fields 列表，没有生成 fill_data，没有填充
```

### 2.5 P5: el-select 预热 — 现状

**预热流程**（`button_detector.py`）：

```
Step 4: fill_create_form → 填充 input/textarea/radio（简单字段）
# ❌ 没有 Step 4.5: fill_multi_step_fields → el-select/el-cascader 等未填充
Step 5: submit_form_v2 → 提交（el-select 字段为空 → 后端校验可能失败）
```

**crud_executor._do_create 有完整的填充流程**（Step 5 fill_form + Step 6 fill_multi_step），
但预热函数只复制了部分逻辑。

---

## 三、详细修改方案

### 3.1 方案 P1: IP 多段输入组件

#### 3.1.1 识别策略

**文件**：`core/discovery/replay/form_filler.py`（`scan_form_fields` JS）

在现有优先级 8（复合输入组）**之前**插入 IP 检测：

```
优先级 7.5: IP 输入组
  检测条件（全部满足）:
    1. 单个 .el-form-item 内有 4 个或 8 个可见 input
    2. 所有 input 无 placeholder 或 placeholder 不含"请输入"
    3. 所有 input 宽度相近（最大宽度/最小宽度 < 1.5）
    4. 父容器无 .el-input-number 类（排除数字输入组）
    5. 父容器无 .el-radio-group 类（排除 radio 组）
    6. label 包含 "IP" 或 "ip" 或 "地址" 关键词（辅助确认）

  输出:
    type: 'ip-input'
    kb_category: 'ip-input'
    inputCount: 4（单IP）或 8（IP区间）
    ipVersion: 'v4' | 'v6'（IPv4 或 IPv6）
    selector: 父容器的 CSS 选择器（.el-form-item 级别）
    sub_selectors: [input1_selector, input2_selector, ...] （每个 input 的选择器）
```

#### 3.1.1.1 IPv4 / IPv6 区分策略

**问题**：IPv4 区间（8 个 input）与 IPv6 单地址（8 个 input）input 数量相同，需要区分。

**区分维度**：

| 维度 | IPv4 | IPv6 |
|------|------|------|
| **label 关键词**（最可靠） | "IP地址"/"IPv4"/"IP区间" | "IPv6"/"IPv6地址" |
| **input 数量** | 4 段（单地址）/ 8 段（区间） | 8 段（单地址）/ 16 段（区间） |
| **input 特征** | 每段 0-255，maxlength=3 | 每段 4 位十六进制，maxlength=4 |
| **placeholder** | "192" 或空 | "2001" 或含 ":" |

**判断优先级**：
1. **label 关键词**（最可靠）：含 "v6"/"IPv6" → IPv6，否则默认 IPv4
2. **input 的 placeholder/maxlength**（辅助）：如果每个 input 的 `maxlength >= 4` 或 placeholder 含 ":" → IPv6
3. **input 数量 + label 组合判断**：
   - 4 个 input + label 含 IP → IPv4 单地址
   - 8 个 input + label 含 "区间"/"range" → IPv4 区间
   - 8 个 input + label 含 "IPv6" → IPv6 单地址
   - 16 个 input → IPv6 区间

**实现**：

```javascript
// 在 IP 检测逻辑中，判断 IPv 版本
const labelLower = label.toLowerCase();
let ipVersion = 'v4';  // 默认 IPv4

// 优先级 1: label 关键词
if (labelLower.includes('ipv6') || labelLower.includes('v6')) {
    ipVersion = 'v6';
}

// 优先级 2: input 特征（辅助判断）
if (ipVersion === 'v4') {
    const firstInput = allInputs[0];
    const maxLength = firstInput.getAttribute('maxlength') || '3';
    const placeholder = firstInput.placeholder || '';
    if (parseInt(maxLength) >= 4 || placeholder.includes(':')) {
        ipVersion = 'v6';
    }
}

// 优先级 3: input 数量 + label 组合
if (ipVersion === 'v4' && allInputs.length === 8) {
    const isRange = labelLower.includes('区间') || labelLower.includes('range');
    const isV6 = labelLower.includes('ipv6') || labelLower.includes('v6');
    if (isV6) ipVersion = 'v6';
    // else: IPv4 区间
}

field.ipVersion = ipVersion;
```

**实际场景映射**：

| 字段 | input 数量 | label | 判断结果 | 填充示例 |
|------|-----------|-------|---------|---------|
| IP地址 | 4 | "IP地址" | IPv4 单地址 | 10.0.0.1 |
| IP地址区间 | 8 | "IP地址区间" | IPv4 区间 | 10.0.0.1 - 10.0.0.10 |
| IPv6地址 | 8 | "IPv6地址" | IPv6 单地址 | 2001:0db8:0000:0000:0000:0000:0000:0001 |
| IPv6区间 | 16 | "IPv6区间" | IPv6 区间 | 2001:db8::1 - 2001:db8::ff |

#### 3.1.2 填充策略

**文件**：`core/discovery/replay/form_filler.py`

新增 `MultiStepExecutor._execute_ip_input(self, label, selector, value, detail)`:

```
流程:
  1. 解析目标 IP 值
     - 4 段: "10.0.0.1" → ["10", "0", "0", "1"]
     - 8 段: "10.0.0.1-10.0.0.10" → ["10","0","0","1","10","0","0","10"]
  2. 定位父容器内所有可见 input
  3. 逐个填充:
     for i, (input_sel, value) in enumerate(zip(sub_selectors, segments)):
       page.click(input_sel)
       Ctrl+A → Backspace → type(value)
       wait(100ms)  # Vue 响应式
  4. 如果 supports_multi_entry: 点击"添加"按钮
  5. 验证: 检查标签列表是否新增
```

#### 3.1.3 测试数据生成

**文件**：`core/discovery/replay/form_filler.py`

`generate_fill_rules` 新增：

```python
if kb_category == "ip-input":
    input_count = field.get("inputCount", 4)
    if input_count == 4:
        rule = {"rule": "ip_pattern", "params": {"base": "10.0.0", "last": "1"}}
    elif input_count == 8:
        rule = {"rule": "ip_range_pattern", "params": {"base": "10.0.0", "start": "1", "end": "10"}}
```

`apply_fill_rule` 新增：

```python
if rule_type == "ip_pattern":
    base = params.get("base", "10.0.0")
    last = params.get("last", "1")
    return f"{base}.{last}"  # "10.0.0.1"

if rule_type == "ip_range_pattern":
    base = params.get("base", "10.0.0")
    start = params.get("start", "1")
    end = params.get("end", "10")
    return f"{base}.{start}-{base}.{end}"  # "10.0.0.1-10.0.0.10"
```

#### 3.1.4 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `form_filler.py` JS（scan_form_fields） | 新增 IP 检测逻辑 | ~30 |
| `form_filler.py`（MultiStepExecutor） | 新增 `_execute_ip_input` | ~40 |
| `form_filler.py`（generate_fill_rules） | 新增 `ip_pattern` / `ip_range_pattern` 规则 | ~15 |
| `form_filler.py`（apply_fill_rule） | 新增 IP 值生成 | ~15 |
| `const.py` | `MULTI_STEP_TYPES` 追加 `"ip-input"` | 1 |
| `replay_engine.py`（_step_fill_form） | `ip-input` 加入 multi_step_fields 分发 | 3 |
| `playbook_builder/builder.py` | 处理 `ip-input` 字段类型 | ~10 |

#### 3.1.5 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| IP 检测误判 | 中 | 4 个同名 input 不一定是 IP（可能是端口范围等） | 增加 label 关键词辅助确认 |
| sub_selectors 跨会话不稳定 | 中 | nth-child 选择器可能因 DOM 变化失效 | 使用 JS index fallback |
| Vue v-model 未触发 | 中 | 逐段填充后 Vue 可能不更新 | 填充后触发 input + change 事件 |
| 影响其他模块 | 低 | 仅在检测到 IP 特征时触发 | 条件严格，不会误触发 |

#### 3.1.6 确定性评估

- IP 检测条件（4/8 个 input + label 含 IP）：**95% 确定**，但需调试日志确认实际 DOM
- 填充后 Vue 更新：需调试日志确认是否需要手动触发事件
- "添加"按钮交互：需确认按钮点击后的标签生成逻辑

---

### 3.2 方案 P2: 标签多条目模式

#### 3.2.1 识别策略

**文件**：`core/discovery/replay/form_filler.py`（`scan_form_fields` JS）

在每个 `.el-form-item` 扫描时，附加检测"添加"按钮：

```javascript
// 在字段信息构建后，附加多条目检测
const addBtn = fi.querySelector('button.el-button--text')
               || Array.from(fi.querySelectorAll('button'))
                        .find(b => (b.textContent || '').trim() === '添加');

if (addBtn) {
    field.supports_multi_entry = true;
    field.add_button_selector = _buildSelector(addBtn);
    // 从提示文本提取最大条目数
    const hintText = fi.textContent.match(/您还可以添加(\d+)个标签/);
    field.max_entries = hintText ? (parseInt(hintText[1]) + currentTags) : 10;
    // 当前已有标签数
    const currentTags = fi.querySelectorAll('.el-tag').length;
    field.current_entries = currentTags;
}
```

#### 3.2.2 填充策略

**附加在现有填充流程之后**（不改变现有逻辑，追加步骤）：

```
对于 supports_multi_entry = true 的字段:
  1. 先填充字段本身的值（IP、select 等，由对应的组件填充策略处理）
  2. 点击"添加"按钮
  3. 等待标签出现（检测 .el-tag 数量增加，超时 3s）
  4. 默认只添加 1 条（预热/测试场景不需要多条）
```

**实现位置**：`form_filler.py` 的 `fill_create_form` 方法末尾，
在所有字段填充完成后，遍历有 `supports_multi_entry` 的字段执行添加。

#### 3.2.3 回放支持

**文件**：`playbook_builder/builder.py`

在 fill_form 步骤的字段信息中传递 `supports_multi_entry` 和 `add_button_selector`。

**文件**：`replay_engine.py`（`_step_fill_form`）

在单个字段填充后，检查 `supports_multi_entry`，如果为 true 则点击添加按钮。

#### 3.2.4 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `form_filler.py` JS（scan_form_fields） | 检测"添加"按钮 | ~15 |
| `form_filler.py`（fill_create_form） | 填充后点击"添加" | ~20 |
| `replay_engine.py`（_step_fill_form） | 回放时处理 multi_entry | ~15 |
| `playbook_builder/builder.py` | 传递 multi_entry 属性 | ~5 |

#### 3.2.5 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| "添加"按钮需要字段值有效 | 高 | 如果 IP 格式错误，点击"添加"不会产生标签 | 依赖 P1 先修复 IP 填充 |
| 添加后表单状态变化 | 中 | 添加标签后可能清空输入框 | 验证标签数量变化 |
| 跨会话稳定性 | 低 | "添加"按钮选择器可能变化 | 使用 text-based 选择器 |

#### 3.2.6 确定性评估

- "添加"按钮检测：**90% 确定**，需确认按钮 selector 稳定性
- 添加后标签生成：**需调试日志确认**添加按钮的行为（是否异步、是否有校验）
- 依赖 P1：**确定**，必须先正确填充 IP 才能添加标签

---

### 3.3 方案 P3: el-select 多选增强

#### 3.3.1 识别增强

**文件**：`core/discovery/replay/form_filler.py`（`scan_form_fields` JS）

在 el-select 检测处（优先级 4）增加多选识别：

```javascript
// 现有: 检测到 .el-select
const selectEl = fi.querySelector('.el-select, .ant-select');
if (selectEl) {
    // 新增: 多选检测
    const isMulti = selectEl.classList.contains('el-select--multiple')
                    || selectEl.querySelector('.el-select__tags') !== null
                    || selectEl.querySelector('input[multiple]') !== null;

    fields.push({
        label, type: 'select', kb_category: 'el-select',
        selector: _buildComponentSelector(fi, selectEl, selectIdx),
        required,
        is_multi_select: isMulti,  // 新增字段
    });
}
```

#### 3.3.2 填充策略增强

**文件**：`core/discovery/replay/form_filler.py`

修改 `_execute_select_with_details`，在发现选项后根据 `is_multi_select` 决定行为：

```python
# 当前逻辑（单选）:
#   选择 1 个选项 → 下拉框自动关闭

# 新增逻辑（多选）:
if is_multi_select:
    # 1. 选择第一个选项（点击 checkbox 或文本）
    # 2. 不关闭下拉框
    # 3. 如果 fill_rule 指定了 count > 1，继续选择更多选项
    # 4. 最后点击其他区域或按 ESC 关闭下拉框
    target_count = field.get("select_count", 1)  # 默认选 1 个（保守策略）
    for i in range(target_count):
        option = visible_options[i]
        await _click_option(option)
        await page.wait_for_timeout(200)
    # 关闭下拉
    await page.keyboard.press("Escape")
else:
    # 单选：现有逻辑不变
```

#### 3.3.3 数据传递链路修复

**当前断裂点**：

```
scan_form_fields → 不输出 is_multi_select ← 修复点 1
    ↓
playbook_builder → 从 field_states 读取 isMultiSelect
    ↓（但 field_states 只在 page-nav 操作时有数据）
playbook JSON → is_multi_select
    ↓
replay_engine → 不读取 is_multi_select ← 修复点 2
```

**修复点 1**：`scan_form_fields` JS 输出 `is_multi_select`（见 3.3.1）

**修复点 2**：`replay_engine.py` `_step_fill_form` 传递 `is_multi_select` 给 `MultiStepExecutor`

```python
# _step_fill_form 中 multi_step_fields 收集时
for field in fields:
    if kb_category in MULTI_STEP_TYPES:
        # 新增: 传递 is_multi_select
        multi_step_fields.append(field)  # field 已包含 is_multi_select
```

`MultiStepExecutor._execute_select_with_details` 已能读取 `is_multi_select`，
只需确认参数传递链路完整。

#### 3.3.4 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `form_filler.py` JS（scan_form_fields） | el-select 多选检测 | ~10 |
| `form_filler.py`（_execute_select_with_details） | 多选填充策略 | ~30 |
| `replay_engine.py`（_step_fill_form） | 传递 is_multi_select | ~3 |
| `playbook_builder/builder.py` | 从 scan 结果读取 is_multi_select | ~5 |

#### 3.3.5 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| 多选后下拉框未关闭 | 中 | 多选模式下选择后下拉框不自动关闭 | 显式 ESC 关闭 |
| 影响已有单选逻辑 | 低 | 只在 `is_multi_select=True` 时走新路径 | 条件分支隔离 |
| 多选选项数量不确定 | 低 | 不同场景需要选不同数量的选项 | 默认选 1 个（保守），fill_rule 可配置 |
| tree-select 多选 | 中 | 树形多选可能需要展开节点 | 先处理普通多选，tree-select 多选后续增强 |

#### 3.3.6 确定性评估

- 多选检测（`el-select--multiple` class）：**95% 确定**
- 多选填充策略（选择 N 个 + ESC 关闭）：**85% 确定**，需调试确认 checkbox 点击行为
- 数据传递链路：scan → builder → replay：**90% 确定**，需验证字段属性透传

---

### 3.4 方案 P4: radio 条件显隐

#### 3.4.1 子问题 4a: radio 误分类修复

**文件**：`core/discovery/replay/form_filler.py`（`scan_form_fields` JS）

**当前优先级冲突**：

```
优先级 5: radio 检测 — fi.querySelector('.el-radio-group')
优先级 8: 复合输入组 — allInputs.length >= 2
```

**问题**：`el-radio-group` 内的 `<input type="radio">` 也被 `querySelectorAll('input.el-input__inner')` 匹配到。
需要先确认：radio input 是否有 `el-input__inner` class？

**需调试日志确认**（见第五节 D1）。

**如果确认 radio input 被误匹配**：

在 allInputs 过滤中排除 radio input：

```javascript
const allInputs = Array.from(fi.querySelectorAll('input.el-input__inner'))
    .filter(inp => !inp.readOnly
                   && inp.offsetWidth > 0
                   && inp.type !== 'radio'      // 新增：排除 radio
                   && inp.type !== 'checkbox'); // 新增：排除 checkbox
```

#### 3.4.2 子问题 4b: 条件字段发现

**文件**：`core/discovery/replay/form_filler.py`（`fill_create_form` 方法）

当前 radio 级联检测（line 150-161）只 log 不处理。修改为：

```python
# 在 radio 字段填充后（切换了 radio 选项）
# 1. 等待 DOM 更新
await page.wait_for_timeout(500)

# 2. 重新扫描表单字段
post_fields = await scan_form_fields(self.page)
current_labels = {f['label'] for f in fields}
new_fields = [f for f in post_fields if f['label'] not in current_labels]

if new_fields:
    LOG.info(f"    [radio-cascade] 新增 {len(new_fields)} 个条件字段")
    # 3. 将新字段加入 fields 列表
    fields.extend(new_fields)
    # 4. 为新字段生成 fill_data
    new_fill_rules = generate_fill_rules(new_fields)
    new_fill_data = generate_fill_data(new_fields, username)
    fill_data.update(new_fill_data)
    # 5. 填充新字段
    # ... 调用 fill_create_form 的填充逻辑 ...
```

#### 3.4.3 回放支持

**文件**：`replay_engine.py`（`_step_fill_form`）

当前 radio 回放后只检测 transfer-box 级联（line 847-879）。
需要增加通用的条件字段发现：

```python
# radio 填充后，重新扫描并填充新出现的字段
if kb_category == "radio":
    await page.wait_for_timeout(500)
    # 重新扫描
    new_fields = await _scan_new_fields(page, existing_labels)
    if new_fields:
        # 填充新字段
        for nf in new_fields:
            if nf.get("kb_category") in MULTI_STEP_TYPES:
                multi_step_fields.append(nf)
            else:
                simple_fields.append(nf)
        # 递归调用填充逻辑
```

#### 3.4.4 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `form_filler.py` JS（scan_form_fields） | allInputs 排除 radio/checkbox | ~5 |
| `form_filler.py`（fill_create_form） | radio 级联字段处理 | ~30 |
| `replay_engine.py`（_step_fill_form） | radio 级联回放 | ~25 |
| `playbook_builder/builder.py` | 条件字段标记 | ~10 |

#### 3.4.5 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| radio 切换后 DOM 不稳定 | 中 | 新字段可能出现延迟 | wait_for_timeout + 重试 |
| 条件字段填充可能触发更多级联 | 低 | 嵌套条件字段 | 限制级联深度（最多 1 层） |
| allInputs 过滤影响其他检测 | 中 | 排除 radio/checkbox 后，某些依赖 allInputs 计数的逻辑可能变化 | 只在 multi-input 检测中排除 |

#### 3.4.6 确定性评估

- radio 误分类原因：**60% 确定**是 allInputs 匹配了 radio input，**需调试日志确认**（D1）
- 条件字段发现后的填充：**85% 确定**，需确认新字段的类型分布
- 回放时的级联处理：**70% 确定**，需确认 radio 步骤在 playbook 中的顺序

---

### 3.5 方案 P5: el-select 预热填充

#### 3.5.1 修改方案

**文件**：`core/discovery/ui_scanner/button_detector.py`

在预热的 Step 4（fill_create_form）之后增加 Step 4.5：

```python
# Step 4.5: 填充多步字段（el-select / el-cascader / date-picker 等）
from core.discovery import const as _const
multi_step_fields = [
    f for f in form_fields
    if f.get("kb_category") in _const.MULTI_STEP_TYPES
    or f.get("kb_category") == "form-checkbox"
]
if multi_step_fields:
    LOG.info(f"    预热: 填充 {len(multi_step_fields)} 个多步字段")
    filler_ms = FormFiller(page)
    ms_filled, _, close_actions = await filler_ms.fill_multi_step_fields(
        multi_step_fields, framework
    )
    filled_count += ms_filled
    LOG.info(f"    预热: 多步字段填充结果: {ms_filled}/{len(multi_step_fields)}")
    # 关闭残留下拉框
    if close_actions:
        for action in close_actions:
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(200)
            except Exception:
                pass
```

#### 3.5.2 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `button_detector.py` | 预热 Step 4.5 | ~20 |

#### 3.5.3 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| el-select 填充失败阻塞预热 | 低 | fill_multi_step_fields 有超时和错误处理 | 已有容错机制 |
| 下拉框残留影响后续操作 | 低 | 添加 ESC 关闭逻辑 | close_actions 处理 |
| 影响预热性能 | 低 | 多步字段填充增加几秒 | 可接受 |

#### 3.5.4 确定性评估

- 方案可行性：**95% 确定**，fill_multi_step_fields 是已验证的成熟函数
- 填充效果：**85% 确定**，取决于 el-select 的选项是否已加载

---

## 四、实施顺序与依赖关系

```
✅ Phase 1（已完成，2026-10-14）:
  ├── ✅ P5: el-select 预热填充（已实现，3/4 成功）
  ├── ✅ P4a: radio 误分类修复（调试日志已添加，待确认）
  └── ✅ 调试日志 D1-D4 已添加

Phase 2（P0 优先级，立即实施）:
  ├── 🔥 P1: IP 多段输入（~115 行，阻塞项，必须最先解决）
  └── P3: el-select 多选增强（~48 行，黑白名单不需要，其他模块可能需要）

Phase 3（依赖 Phase 2）:
  ├── P2: 标签多条目（~55 行，依赖 P1 IP 填充）
  └── P4b: 条件字段发现（~70 行，独立功能）
```

**Phase 1 完成情况**：
- ✅ P5 已实现：预热时调用 fill_multi_step_fields，3/4 el-select 成功填充
- ✅ 调试日志已添加：D1-D4 收集到完整数据
- ⚠️ P4a 待确认：日志显示 radio input 不在标准 el-radio-group 内（非标准结构）

**Phase 2 预期效果（当前阻塞项）**：
- 🔥 P1 解决 IP 输入 → IP 地址填充合法值（如 10.0.0.1），**创建成功**
- P3 解决多选 select → 黑白名单不需要（日志显示都是单选），其他模块可能需要

**Phase 3 预期效果**：
- P2 解决标签添加 → IP 条目被正确添加（依赖 P1 先填充合法 IP）
- P4b 解决条件字段 → "时间段" date-picker 可被发现和填充

---

## 五、调试日志方案（不确定项）

### D1: radio 误分类确认

**文件**：`form_filler.py`（`scan_form_fields` JS）

在 allInputs 收集后，添加诊断信息：

```javascript
// 在 allInputs 过滤后（约 line 1028）
const allInputDetails = allInputs.map(inp => ({
    type: inp.type,           // "text", "radio", "checkbox" 等
    className: inp.className.substring(0, 60),
    hasRadioClass: inp.classList.contains('el-radio__input') || false,
    parentIsRadio: inp.closest('.el-radio-group') !== null,
    placeholder: inp.placeholder || '',
    width: inp.offsetWidth,
}));
result._debug_multiInput = {
    formItemLabel: label,
    allInputCount: allInputs.length,
    allInputDetails: allInputDetails,
};
```

**Python 侧读取**：

```python
# scan_form_fields 返回后
debug_mi = result.get("_debug_multiInput")
if debug_mi and debug_mi.get("allInputCount", 0) >= 2:
    LOG.info(f"    [调试] multi-input 字段 '{debug_mi.get('formItemLabel')}': "
             f"{debug_mi.get('allInputCount')} 个 input")
    for d in debug_mi.get("allInputDetails", []):
        LOG.info(f"    [调试]   type={d.get('type')}, "
                 f"parentIsRadio={d.get('parentIsRadio')}, "
                 f"class={d.get('className', '')[:40]}")
```

**期望输出**：确认"选择时间"字段的 input type 是否为 "radio"，
以及 `parentIsRadio` 是否为 true。

**如果确认**：修复方案 4a 中的 allInputs 过滤即可。
**如果不确认**：需要进一步分析 radio 组件的 DOM 结构。

### D2: "添加"按钮行为确认

**文件**：`form_filler.py`（`scan_form_fields` JS）

```javascript
// 在 .el-form-item 扫描循环内
const addBtn = fi.querySelector('button.el-button--text');
const tagCount = fi.querySelectorAll('.el-tag').length;
if (addBtn) {
    field._debug_addBtn = {
        text: addBtn.textContent.trim(),
        visible: addBtn.offsetWidth > 0,
        currentTags: tagCount,
    };
}
```

**Python 侧读取**：

```python
for f in form_fields:
    dbg = f.get("_debug_addBtn")
    if dbg:
        LOG.info(f"    [调试] 字段 '{f.get('label')}' 有添加按钮: "
                 f"text='{dbg.get('text')}', "
                 f"visible={dbg.get('visible')}, "
                 f"currentTags={dbg.get('currentTags')}")
```

**期望输出**：确认哪些字段有"添加"按钮，按钮是否可见。

### D3: IP 输入组件 DOM 结构确认

**文件**：`form_filler.py`（`scan_form_fields` JS）

在 multi-input 检测处，记录 IP 相关字段的结构：

```javascript
if (allInputs.length >= 2) {
    const widths = allInputs.map(inp => inp.offsetWidth);
    const maxW = Math.max(...widths);
    const minW = Math.min(...widths);
    result._debug_ipCheck = result._debug_ipCheck || [];
    result._debug_ipCheck.push({
        label: label,
        inputCount: allInputs.length,
        widthRatio: minW > 0 ? (maxW / minW).toFixed(2) : 'inf',
        hasIpInLabel: /ip|地址/i.test(label),
        parentClass: fi.querySelector('[class*="ip"]')?.className || 'none',
        hasAddBtn: !!fi.querySelector('button.el-button--text'),
    });
}
```

**Python 侧读取**：

```python
ip_checks = scan_result.get("_debug_ipCheck", [])
for ic in ip_checks:
    LOG.info(f"    [调试] IP 检测: label='{ic.get('label')}', "
             f"inputCount={ic.get('inputCount')}, "
             f"widthRatio={ic.get('widthRatio')}, "
             f"hasIpInLabel={ic.get('hasIpInLabel')}, "
             f"parentClass={ic.get('parentClass')}")
```

**期望输出**：确认 IP 字段的 input 数量、宽度比例、label 关键词。

### D4: el-select 多选确认

**文件**：`form_filler.py`（`scan_form_fields` JS）

```javascript
if (selectEl) {
    const isMulti = selectEl.classList.contains('el-select--multiple')
                    || selectEl.querySelector('.el-select__tags') !== null;
    if (isMulti) {
        field._debug_multiSelect = {
            hasMultipleClass: selectEl.classList.contains('el-select--multiple'),
            hasTagsContainer: selectEl.querySelector('.el-select__tags') !== null,
            currentTagCount: (selectEl.querySelectorAll('.el-tag') || []).length,
        };
    }
}
```

**Python 侧读取**：

```python
for f in form_fields:
    dbg = f.get("_debug_multiSelect")
    if dbg:
        LOG.info(f"    [调试] 多选字段 '{f.get('label')}': "
                 f"hasMultipleClass={dbg.get('hasMultipleClass')}, "
                 f"hasTagsContainer={dbg.get('hasTagsContainer')}, "
                 f"currentTagCount={dbg.get('currentTagCount')}")
```

**期望输出**：确认哪些 el-select 是多选，多选检测方式是否可靠。

---

## 六、验证计划

### Phase 1 验证

```bash
# 1. 单元测试
python -m pytest tests/ -q

# 2. 黑白名单 Stage 1（有头模式，观察日志）
python -m core.discovery.run --project estack --module "黑白名单" \
  --url "/estack/web/estack/user-center/user-manage/black-list" --stage 1

# 验证点:
# - [ ] P5: 预热日志出现 "多步字段填充: N/M"
# - [ ] P4a: "选择时间" 被识别为 radio（不再误判为 multi-input）
# - [ ] D1: 调试日志确认 radio input 的 type 和 parentIsRadio
# - [ ] D2/D3/D4: 收集 IP/添加按钮/多选的调试数据
```

### Phase 2 验证

```bash
# 验证点:
# - [ ] P1: IP 字段填充了合法 IP（如 "10.0.0.1"）
# - [ ] P3: 多选 el-select 选了至少 1 个选项
# - [ ] 预热提交后表单无校验错误
# - [ ] 预热后表格行数 > 0（数据创建成功）
```

### Phase 3 验证

```bash
# 验证点:
# - [ ] P2: IP 条目被成功添加为标签
# - [ ] P4b: "时间段" date-picker 被发现并填充
# - [ ] 黑白名单完整 CRUD 操作可用
```

---

## 七、实际运行日志分析（2026-10-14）

### 7.1 运行环境
- 模块：黑白名单（black-list）
- 日期：2026-10-14 15:29-15:31
- 阶段：Stage 1（UI 探测 + 预热填充）

### 7.2 调试日志关键发现

#### D1-radio：Radio 字段检测
```
[D1-radio] formItem='选择时间' rawInputs=2 filteredInputs=0 radioInRaw=2
[D1-radio]   type=radio parentIsRadio=False parentIsRadioBtn=False
[D1-radio]   type=radio parentIsRadio=False parentIsRadioBtn=False
```

**分析**：
- "选择时间"字段包含 2 个 `type=radio` 的 input
- 但这些 radio input **不在** `.el-radio-group` 容器内（`parentIsRadio=False`）
- 也不在 `.el-radio-button` 内（`parentIsRadioBtn=False`）
- 说明这个页面使用了**自定义 radio 结构**，而非标准 Element UI radio-group

**结论**：P4a 修复方案（allInputs 过滤 radio）是必要的，但需要额外处理非标准 radio 结构。

#### D3-ip：IP 输入组件结构
```
[D3-ip] label='IP地址' inputCount=4 widthRatio=1.00 hasIpInLabel=True hasAddBtn=True tagCount=0
[D3-ip]   input[0]: type=text width=55 placeholder='' readOnly=False
[D3-ip]   input[1]: type=text width=55 placeholder='' readOnly=False
[D3-ip]   input[2]: type=text width=55 placeholder='' readOnly=False
[D3-ip]   input[3]: type=text width=55 placeholder='' readOnly=False

[D3-ip] label='IP地址区间' inputCount=8 widthRatio=1.00 hasIpInLabel=True hasAddBtn=True tagCount=0
[D3-ip]   input[0-3]: 第一段 IP（4 个 input）
[D3-ip]   input[4-7]: 第二段 IP（4 个 input）
```

**分析**：
- "IP地址"字段：**4 个 input**，每个宽度 55px，宽度比例 1.00（完全相同）
- "IP地址区间"字段：**8 个 input**，同样宽度 55px
- 所有 input 都是 `type=text`，`readOnly=False`，无 placeholder
- label 中包含 "IP" 关键词（`hasIpInLabel=True`）
- 有添加按钮（`hasAddBtn=True`），但标签数为 0（`tagCount=0`）

**IPv4/IPv6 区分分析**：

| 字段 | input 数量 | label | 判断 | 说明 |
|------|-----------|-------|------|------|
| IP地址 | 4 | "IP地址" | **IPv4 单地址** | 4 段 + label 无 "v6" |
| IP地址区间 | 8 | "IP地址区间" | **IPv4 区间** | 8 段 + label 含 "区间" 且无 "v6" |

**关键发现**：
- 当前页面的 8 个 input 是 **两个 IPv4 地址**（起 IP + 止 IP），不是 IPv6
- IPv6 单地址虽然也是 8 段，但 label 会包含 "IPv6" 或 "v6" 关键词
- 区分策略：label 关键词（最可靠）> input 特征（maxlength/placeholder）> 数量组合

**判断逻辑**：
```javascript
// 优先级 1: label 关键词
if (label.includes('IPv6') || label.includes('v6')) → IPv6
else → IPv4

// 优先级 2: input 特征（辅助）
if (input.maxlength >= 4 || placeholder.includes(':')) → IPv6

// 优先级 3: 数量 + label 组合
4 个 input → IPv4 单地址
8 个 input + label 含 "区间" → IPv4 区间
8 个 input + label 含 "IPv6" → IPv6 单地址
```

**填充示例**：
- IPv4 单地址（4 段）：`10.0.0.1` → 分别填入 `["10", "0", "0", "1"]`
- IPv4 区间（8 段）：`10.0.0.1-10.0.0.10` → 分别填入 `["10", "0", "0", "1", "10", "0", "0", "10"]`
- IPv6 单地址（8 段）：`2001:db8::1` → 展开为 `["2001", "0db8", "0000", "0000", "0000", "0000", "0000", "0001"]`
- IPv6 区间（16 段）：`2001:db8::1 - 2001:db8::ff` → 分别展开为 16 段

**结论**：P1 修复方案（IP 多段输入）的检测条件完全匹配：
- ✅ 4/8 个 input
- ✅ 宽度比例 1.00
- ✅ label 含 "IP"
- ✅ 需要分段填充
- ✅ IPv4/IPv6 可通过 label 关键词区分

#### D2-addBtn：添加按钮行为
```
[D2-addBtn] formItem='IP地址' btnText='' visible=False currentTags=0
[D2-addBtn] formItem='IP地址区间' btnText='' visible=False currentTags=0
```

**分析**：
- 添加按钮存在，但 `btnText=''`（文本为空）且 `visible=False`
- 当前标签数为 0（`currentTags=0`）
- 说明按钮可能需要先填充 IP 值后才变为可见

**结论**：P2 修复方案（标签多条目）需要先实现 P1（正确填充 IP），然后按钮才可能变为可用状态。

#### D4-multiSelect：el-select 多选
```
（日志中未输出 D4，说明扫描的 4 个 el-select 都不是多选）
```

**分析**：
- 4 个 el-select 字段：IP地址/网段(CIDR)、授权主体(类型)、IP地址/网段、授权主体
- 均未触发 D4 日志，说明这些 select 都是**单选**，不是多选

**结论**：P3 修复方案（el-select 多选）在黑白名单模块不适用，但其他模块可能需要。

### 7.3 预热填充结果

#### 普通字段填充（fill_create_form）
```
预热: 表单填充结果: 7/11 个字段
```

**分析**：
- 11 个字段中，7 个普通字段被填充（input-generic 类型）
- 4 个 el-select 字段被跳过（由 fill_multi_step_fields 处理）

#### 多步字段填充（fill_multi_step_fields）
```
预热: 多步字段填充结果: 3/4
el-select IP地址/网段(CIDR): selector 展开成功
⚠️ select 选项验证失败: IP地址/网段(CIDR) 为空
⚠️ multi_step 失败: IP地址/网段(CIDR) (el-select)
✅ multi_step: 授权主体(类型) (el-select)
✅ multi_step: IP地址/网段 (el-select)
✅ multi_step: 授权主体 (el-select)
```

**分析**：
- P5 修复方案生效！多步字段填充被调用
- 4 个 el-select 中，3 个成功填充，1 个失败
- 失败的 "IP地址/网段(CIDR)" 选项为空（可能是级联选择，依赖前一个字段）

**结论**：P5 修复成功，但级联 el-select 需要进一步处理（可能需要在 P3 中增加级联逻辑）。

#### 表单提交结果
```
预热: 表单已提交 (策略: js)
预热: 表格行数 = 0
预热: 创建后表格仍无数据行
```

**分析**：
- 表单提交成功（策略：js）
- 但表格行数仍为 0，说明**创建失败**
- 根因：IP 地址字段填充了无效的文本值（如 "AT_test_963087"），后端校验失败

**结论**：必须先实现 P1（IP 多段输入填充合法 IP 值），预热才能创建成功。

### 7.4 总结

| 问题 | 日志证据 | 修复方案 | 优先级 |
|------|---------|---------|--------|
| IP 填充非法值 | D3-ip 显示 4/8 个 input，当前填充文本 | P1: IP 多段输入 ✅ | **已完成** |
| Radio 结构非标准 | D1-radio 显示 radio 不在 el-radio-group 内 | P4a: allInputs 过滤 radio | **P0** |
| 多步字段未填充 | 日志显示 3/4 el-select 填充成功 | P5: el-select 预热填充 ✅ | **已完成** |
| 添加按钮未激活 | D2-addBtn 显示按钮不可见 | P2: 标签多条目（依赖 P1） | P1 |
| 级联 el-select 失败 | IP地址/网段(CIDR) 选项为空 | P3: 级联逻辑增强 | P2 |

---

### 7.5 P1 实施完成验证（2026-10-14）

#### 7.5.1 实施内容

**JS 扫描逻辑**（`form_filler.py:1106-1228`）：
- 新增优先级 7.5：IP 输入组检测
- 检测条件：4/8/16 个 input + 宽度相近 + label 含 IP 关键词
- IPv4/IPv6 区分：优先看 label（"IPv6"/"v6"），其次看 maxlength/placeholder
- 输出：`subSelectors`（每个 input 的 selector）、`inputCount`、`ipVersion`

**IP 填充执行器**（`form_filler.py:2440-2613`）：
- `_execute_ip_input_with_details`：分段填充 IP
- `_parse_ip_segments`：解析 IP 值为各段
- `_expand_ipv6`：展开 IPv6 缩写（:: → 完整 8 段）
- 逐段填充：点击 → 清空 → 输入 → 触发 input/change 事件

**填充规则**（`form_filler.py:1583-1701`）：
- 新增 4 种 IP 规则：`ip_v4_pattern`、`ip_v4_range_pattern`、`ip_v6_pattern`、`ip_v6_range_pattern`
- `generate_fill_rules`：根据 inputCount 和 ipVersion 选择规则
- `apply_fill_rule`：生成具体 IP 值（如 `10.0.0.1`、`10.0.0.1-10.0.0.10`）

**常量定义**（`const.py:140`）：
- `MULTI_STEP_TYPES` 新增 `"ip-input"`

**字段透传**（`form_filler.py:98-122`）：
- `scan_form_fields_v2` 透传 `subSelectors`、`inputCount`、`ipVersion`

**关键修复**：IP 输入组件检测中的 `el-input-number` 误判问题
- 根因：IP 地址字段使用了 `el-input-number` 组件，导致被误判为数字输入
- 修复：区分"单个数字输入"vs"多个数字输入组成的 IP 段"
- 代码：`form_filler.py:1163-1165`

#### 7.5.2 验证结果

**测试通过**：
```bash
675 passed in 5.81s ✅
```

**黑白名单 Stage 1 验证**（2026-10-14 17:00-17:17）：

```
IP 填充: IP地址 = 10.0.0.1 (version=v4, inputs=4) ✅
IP 填充: IP地址区间 = 10.0.0.1-10.0.0.10 (version=v4, inputs=8) ✅
```

**乐观策略验证**：
```
17:16:56 [base_executor]     ⚠️ 未检测到明确的成功/失败信号，标记为失败（可能是 toast 过快消失或后端无反馈）
17:16:56 [crud_executor]     第 1 次失败: [no_success_signal] 提交后未检测到成功信号
```

**多步字段填充**：
```
✅ multi_step: IP地址 (ip-input)
✅ multi_step: IP地址区间 (ip-input)
⚠️ multi_step 失败: IP地址/掩码(黑白) (el-select)  # 级联依赖问题，与 P1 无关
✅ multi_step: 授权类型(黑白) (el-select)
⚠️ multi_step 失败: 授权主体(黑白) (el-select)    # 级联依赖问题，与 P1 无关
✅ multi_step: IP地址/掩码 (el-select)
✅ multi_step: 授权类型 (el-select)
```

**最终结果**：
```
Stage 1 关键操作验证失败: ['create-like']
业务堆栈验证: 1/2 操作成功
  - 新增: 失败 (no_success_signal)
  - 查询: 成功
```

**结论**：
- ✅ P1 IP 多段输入修复成功，IP 字段填充了合法值
- ✅ 乐观策略移除成功，"无信号"不再误判为成功
- ⚠️ 新增操作失败，根因是多步字段（授权主体）填充失败后仍提交表单

---

### 7.6 乐观策略移除（2026-10-14）

#### 7.6.1 问题定义

**用户明确的成功判定标准**：
- 有 toast/dialog + "成功"描述 → 成功
- 有 toast/dialog + "失败"描述 → 失败
- "xx中"等待过期 → 可以标记为成功
- **没有 toast/dialog → 不应该标记为成功**

**原实现问题**：
`base_executor.py:702-832` 的 `_verify_operation_success` 函数在检测不到任何信号时，走"乐观策略"直接返回 `True`：

```python
# 5. 乐观策略：没有检测到任何错误信号 → 视为成功
LOG.info("    未检测到错误信号，判定为成功")
return True
```

这导致 IP 字段填充非法值 `AT_test_965723` 后仍被标记为成功，严重干扰分析。

#### 7.6.2 修复方案

移除乐观策略，改为返回 `False`：

```python
# 5. 无信号：没有检测到明确的成功/失败信号 → 标记为失败
# 避免"乐观策略"导致的误判（如填充非法值后仍标记为成功）
LOG.warning("    ⚠️ 未检测到明确的成功/失败信号，标记为失败（可能是 toast 过快消失或后端无反馈）")
return False
```

**修改文件**：
- `core/discovery/operation_executor/base_executor.py:829-832`

**下游影响**：
- 9 个调用方（`crud_executor.py`、`navigation_executor.py`）已正确处理 `False`
- 返回 `error_type="no_success_signal"`，明确表达"无信号"状态
- `builder.py` 已正确处理 `success=False` + `error_type="no_success_signal"`

#### 7.6.3 验证结果

```
17:16:56 [base_executor]     ⚠️ 未检测到明确的成功/失败信号，标记为失败（可能是 toast 过快消失或后端无反馈）
17:16:56 [crud_executor]     第 1 次失败: [no_success_signal] 提交后未检测到成功信号
```

**结论**：乐观策略移除后，系统不再误判"无信号"为成功，准确识别出表单提交失败的问题。

---

## 八、下一阶段实施方案

### 8.1 P6: 多步字段填充失败后仍提交表单（**P0 优先级**）

#### 8.1.1 问题定义

**日志证据**（2026-10-14 17:16:43-17:16:56）：

```
17:16:43 [form_filler]     ⚠️ multi_step 失败: IP地址/掩码(黑白) (el-select)
17:16:48 [form_filler]     ⚠️ multi_step 失败: 授权主体(黑白) (el-select)
17:16:48 [button_detector]     预热: 多步字段填充结果: 5/6
17:16:48 [button_detector]     预热: 表单已提交 (策略: js)
17:16:56 [base_executor]     ⚠️ 未检测到明确的成功/失败信号，标记为失败
```

**根因**：
- `button_detector.py` 预热流程中，多步字段填充部分失败（5/6 成功）
- **当前逻辑：无论成功失败，都继续提交表单**
- 导致必填字段（授权主体）为空时仍提交，表单验证失败
- 没有检测到明确的错误信号（toast 可能过快消失或后端无反馈）

**影响**：
- Stage 1 验证失败，错误地认为"新增操作无法执行"
- 实际是预热阶段的多步字段填充失败导致的

#### 8.1.2 修复方案

**修改文件**：`core/discovery/ui_scanner/button_detector.py`

**修改位置**：预热流程 Step 4.5（多步字段填充）之后

**当前逻辑**（约 line 280-295）：

```python
# Step 4.5: 填充多步字段
if multi_step_fields:
    ms_filled, details, close_actions = await filler.fill_multi_step_fields(
        multi_step_fields, framework
    )
    filled_count += ms_filled
    LOG.info(f"    预热: 多步字段填充结果: {ms_filled}/{len(multi_step_fields)}")
    # 关闭残留下拉框
    for action in close_actions:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)

# Step 5: 提交表单（无论多步字段是否全部成功）
await filler.submit_form_v2()
```

**修复逻辑**：

```python
# Step 4.5: 填充多步字段
if multi_step_fields:
    ms_filled, details, close_actions = await filler.fill_multi_step_fields(
        multi_step_fields, framework
    )
    filled_count += ms_filled
    LOG.info(f"    预热: 多步字段填充结果: {ms_filled}/{len(multi_step_fields)}")
    # 关闭残留下拉框
    for action in close_actions:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
    
    # ✅ 新增：检查必填字段是否都填充成功
    required_failed = []
    for d in details:
        field_label = d.get("label", "")
        is_required = any(
            f.get("label") == field_label and f.get("required", False)
            for f in multi_step_fields
        )
        if is_required and not d.get("is_editable", False):
            required_failed.append(field_label)
    
    if required_failed:
        LOG.warning(f"    预热: 必填多步字段填充失败: {required_failed}，跳过提交")
        return result  # 跳过提交，返回预热结果
```

#### 8.1.3 同步修复 `_do_create`

**修改文件**：`core/discovery/operation_executor/crud_executor.py`

**修改位置**：`_do_create` 函数中多步字段填充之后（约 line 745-760）

**当前逻辑**：

```python
# Step 6: 填充多步字段
ms_filled, ms_details, close_actions = await filler.fill_multi_step_fields(
    multi_step_field_details, framework
)

# Step 7: 提交表单（无论多步字段是否全部成功）
submit_result = await filler.submit_form_v2()
```

**修复逻辑**：

```python
# Step 6: 填充多步字段
ms_filled, ms_details, close_actions = await filler.fill_multi_step_fields(
    multi_step_field_details, framework
)

# ✅ 新增：检查必填字段是否都填充成功
required_failed = []
for d in ms_details:
    field_label = d.get("label", "")
    is_required = any(
        f.get("label") == field_label and f.get("required", False)
        for f in multi_step_field_details
    )
    if is_required and not d.get("is_editable", False):
        required_failed.append(field_label)

if required_failed:
    LOG.warning(f"    必填多步字段填充失败: {required_failed}，跳过提交")
    return _build_create_error_result(
        "multi_step_required_failed",
        f"必填多步字段填充失败: {', '.join(required_failed)}",
        trigger_text=btn_text,
        trigger_locator_verified=btn_click_result.get("locator"),
    )

# Step 7: 提交表单
submit_result = await filler.submit_form_v2()
```

#### 8.1.4 涉及文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| `button_detector.py` | 预热流程检查必填字段 | ~15 |
| `crud_executor.py` | `_do_create` 检查必填字段 | ~20 |

#### 8.1.5 风险评估

| 风险 | 等级 | 说明 | 缓解措施 |
|------|------|------|---------|
| 误判必填字段 | 低 | 依赖 `required` 标记准确性 | 检查 `form_fields` 中的 `required` 字段 |
| 影响非必填字段 | 低 | 只检查 `required=True` 的字段 | 非必填字段失败不阻止提交 |
| 预热阶段跳过提交 | 低 | 预热失败不影响 Stage 1 验证 | Stage 1 验证使用 `_do_create`，独立于预热 |

#### 8.1.6 确定性评估

- 必填字段检测：依赖 `form_fields` 中的 `required` 标记，**90% 确定**
- 多步字段失败后的错误处理：**95% 确定**，已有 `_build_create_error_result` 函数
- 预热阶段跳过提交：**95% 确定**，不影响 Stage 1 验证

#### 8.1.7 验证计划

```bash
# 1. 单元测试
python -m pytest tests/ -q

# 2. 黑白名单 Stage 1（有头模式，观察日志）
python -m core.discovery.run --project estack --module "黑白名单" \
  --url "/estack/web/estack/user-center/user-manage/black-list" --stage 1

# 验证点:
# - [ ] 多步字段填充失败时，日志输出 "必填多步字段填充失败: [...]"
# - [ ] 表单未提交（预热阶段）
# - [ ] Stage 1 验证失败原因明确：multi_step_required_failed
```

---

### 8.2 优先级更新

| 方案 | 工作量 | 风险 | 确定性 | 优先级 | 状态 |
|------|--------|------|--------|--------|------|
| P5: el-select 预热 | 20 行 | 极低 | 95% | Phase 1 | ✅ 已完成 |
| P1: IP 多段输入 | 115 行 | 中 | 85% | Phase 2 | ✅ 已完成 |
| P6: 多步字段失败后仍提交 | 35 行 | 低 | 95% | **Phase 2.5** | ⏳ 待实施 |
| P4a: radio 误分类 | 5 行 | 低 | 60% | Phase 1 | ⏳ 待实施 |
| P3: el-select 多选 | 48 行 | 中 | 85% | Phase 2 | ⏳ 待实施 |
| P2: 标签多条目 | 55 行 | 中 | 80% | Phase 3 | ⏳ 待实施 |
| P4b: 条件字段发现 | 70 行 | 中 | 75% | Phase 3 | ⏳ 待实施 |
| **Phase 1: 复合组件检测增强** | 50 行 | 中 | 90% | **立即实施** | ⏳ 待实施 |
| **Phase 2: el-select 类型分类** | 80 行 | 高 | 70% | Phase 1 后 | ⏳ 待实施 |
| **Phase 3: 泛化级联检测** | 30 行 | 低 | 85% | Phase 1 后 | ⏳ 待实施 |

**建议**：
1. **立即实施（P0 优先级）**：Phase 1 复合组件检测增强 → 解决子网掩码 + 授权主体问题
2. **Phase 2.5（待实施）**：P6 多步字段失败后仍提交 → 修复预热阶段的表单提交逻辑
3. **Phase 2（待实施）**：el-select 类型分类 → 区分单选/多选/穿梭框/树形选择
4. **Phase 1（待实施）**：P4a radio 误分类 → 修复 radio 字段检测
5. **Phase 3（待实施）**：P2 标签多条目、P4b 条件字段发现、Phase 3 泛化级联检测

---

## 九、总结

**已完成**：
- ✅ P5: el-select 预热填充（2026-10-09）
- ✅ P1: IP 多段输入（2026-10-14）
- ✅ 乐观策略移除（2026-10-14）
- ✅ D5/D6: 复合组件 + 级联检测调试日志（2026-10-14）

**待实施**：
- ⏳ **Phase 1: 复合组件检测增强**（**P0 优先级**，解决子网掩码 + 授权主体问题）
- ⏳ **Phase 2: el-select 类型分类**（单选/多选/穿梭框/树形选择）
- ⏳ **Phase 3: 泛化级联检测**
- ⏳ P6: 多步字段失败后仍提交
- ⏳ P4a: radio 误分类修复
- ⏳ P2: 标签多条目
- ⏳ P4b: 条件字段发现

**下一步**：实施 Phase 1，增强 2.1 优先级的复合组件检测逻辑（解决子网掩码和授权主体问题）

---

## 十、D5/D6 调试日志验证结论

> 日期：2026-10-14
> 状态：✅ 验证完成（D5/D6 调试日志已添加并运行 Stage 1）

### 10.1 D5 — 复合组件结构验证

#### 10.1.1 子网掩码字段（IP地址/网段）✅ 确认

```
[D5-composite] label='IP地址/网段' selectCount=1 independentInputs=4 totalInputs=5
hasIpInLabel=True isIpMaskCandidate=True
  input[0]: type=text width=55 inInputNumber=True
  input[1]: type=text width=55 inInputNumber=True
  input[2]: type=text width=55 inInputNumber=True
  input[3]: type=text width=55 inInputNumber=True
```

**确认结构**：1 个 select（掩码选择框）+ 4 个 input（IP 段，都在 el-input-number 内）

**检测条件命中**：`isIpMaskCandidate=True`，说明泛化检测逻辑可以识别这个模式

**当前扫描结果**（11 个字段中的 5 个）：
```
字段[2] IP地址/网段(下拉) → el-select  ❌ 掩码选择框，验证失败"为空"
字段[3] IP地址/网段      → input-generic  (IP 段 1)
字段[4] IP地址/网段      → input-generic  (IP 段 2)
字段[5] IP地址/网段      → input-generic  (IP 段 3)
字段[6] IP地址/网段      → input-generic  (IP 段 4)
```

**问题根因**：优先级 2.1 的复合检测逻辑只是简单拆分为 select + inputs，没有识别 IP+掩码的语义关联

#### 10.1.2 授权主体字段 ✅ 确认

```
[D5-composite] label='授权主体' selectCount=2 independentInputs=1 totalInputs=3
  input[0]: type=text width=258 inInputNumber=False
```

**确认结构**：**2 个 select** + 1 个 input（width=258，不在 el-input-number 内）

**关键发现**：
- `selectCount=2`：同一个 form-item 内有 2 个 el-select
- `independentInputs=1`：1 个独立 input（width=258）
- 这个 input 很可能是第二个 select 内部的**搜索框**（`.el-select__input`），而不是独立字段

**当前扫描结果**（11 个字段中的 3 个）：
```
字段[7]  授权主体(下拉) → el-select  ✅ 类型选择器，选择'组织'
字段[8]  授权主体      → input-generic  (第二个 select 的搜索框) ❌ 误判
字段[10] 授权主体      → el-select  (来自另一个 tab 的表单)
```

**问题根因**：优先级 2.1 使用 `fi.querySelector('.el-select')` 只找到**第一个 select**，第二个 select 的搜索框被误判为独立 input

### 10.2 D6 — 级联检测验证

修复后的 D6 逻辑（pre-scan → 等待 → post-scan）显示：

```
[D6-cascade] pre-scan: 11 个字段, 等待 2s 检测级联渲染 (trigger=授权主体(下拉), type=el-select)
[D6-cascade] 无级联效果 (pre=11 post=11)
```

**所有 3 次级联检测都显示"无级联效果"**：
1. 选择"授权主体(下拉)"后：pre=11 post=11
2. 选择"IP地址/网段"后：pre=11 post=11
3. 选择"授权主体"后：pre=11 post=11

**关键结论**：**不存在级联渲染**！两个 select 始终存在于 DOM 中，不是条件渲染的。问题纯粹是**扫描检测错误**，不是级联填充问题。

### 10.3 核心问题总结

| 问题 | 本质 | 当前状态 |
|------|------|---------|
| 子网掩码字段 | 2.1 优先级没有识别 IP+掩码复合模式 | 已确认 `isIpMaskCandidate=True` |
| 授权主体字段 | 2.1 优先级只找第一个 select，第二个 select 的搜索框被误判 | 已确认 `selectCount=2` |
| 级联渲染 | 不存在！两个 select 始终在 DOM 中 | ✅ 已排除 |

**下一步**：实施泛化方案，增强 2.1 优先级的复合组件检测逻辑

---

## 十一、泛化方案：复合组件检测 + el-select 类型分类

> 日期：2026-10-14
> 状态：待实施

### 11.1 问题泛化

**问题 1（子网掩码）的本质**：当一个 `form-item` 包含 select + 多个 input 时，当前 2.1 优先级只是粗暴地拆分为独立字段，丢失了组件间的语义关联。子网掩码只是这个通用问题的一个特例——通用问题是**复合组件模式识别缺失**。

**问题 2（授权主体）的本质**：当一个 form-item 包含多个 select 时，当前 2.1 优先级只检测第一个 select，其他 select 的内部组件（如搜索框）被误判为独立字段。通用问题是**多 select 场景检测不完整**。

**el-select 类型分类缺失**：当前所有 el-select 都标记为 `kb_category: 'el-select'`，没有区分单选/多选/穿梭框/树形选择等不同类型，导致填充策略无法精确匹配。

### 11.2 方案一：JS 扫描侧 — 复合组件通用模式识别

当前 2.1 只做"select + input → 拆分"。改为：**收集 form-item 内的完整组件清单**，按组件组合模式分发：

```javascript
// 2.1 通用复合组件检测
const selects = Array.from(fi.querySelectorAll('.el-select'));
const cascaders = Array.from(fi.querySelectorAll('.el-cascader'));
const datePickers = Array.from(fi.querySelectorAll('[class*="date-picker"]'));

// 过滤掉所有 select/cascader/date-picker 内部的 input（如搜索框）
const allInputs = Array.from(fi.querySelectorAll('input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"])'))
    .filter(inp => {
        if (inp.readOnly || inp.offsetWidth === 0) return false;
        // 排除所有复合组件内部的 input
        return !selects.some(s => s.contains(inp))
            && !cascaders.some(c => c.contains(inp))
            && !datePickers.some(d => d.contains(inp));
    });

// 组件清单
const componentInventory = {
    selectCount: selects.length,
    cascaderCount: cascaders.length,
    datePickerCount: datePickers.length,
    inputCount: allInputs.length,
};

// 模式匹配表（通用，可扩展）
const pattern = matchCompositePattern(componentInventory, label);
switch (pattern) {
    case 'ip-with-mask':    // 1 select + 4/8/16 inputs + IP label
        fields.push({ type: 'ip-input-with-mask', ... });
        break;
    case 'multi-select-composite':  // N selects + M inputs
        // 逐个添加 select，排除内部搜索框
        selects.forEach((selectEl, idx) => {
            fields.push({ type: classifySelect(selectEl), ... });
        });
        break;
    case 'single-select-input':  // 1 select + 1 input（原 2.1 行为）
        fields.push({ type: 'select', kb_category: 'el-select', ... });
        fields.push({ type: 'input', kb_category: 'input-generic', ... });
        break;
    ...
}
```

**关键改进**：
1. 使用 `querySelectorAll` 找所有 select，而非 `querySelector` 只找第一个
2. 过滤掉所有 select/cascader/date-picker 内部的 input（如搜索框）
3. 模式匹配表可扩展，新增模式只需加一行

### 11.3 方案二：el-select 类型分类

#### 11.3.1 扩展 kb_category 分类

当前：所有 el-select 都标记为 `kb_category: 'el-select'`

改为：根据 select 的实际特征分类

| 当前 kb_category | 新 kb_category | 识别特征 | 填充策略 |
|------------------|----------------|---------|---------|
| `el-select` | `el-select-single` | 单选，无 `el-select--multiple` class | 选择第一个选项或默认值 |
| `el-select` | `el-select-multiple` | 多选，有 `el-select--multiple` class 或 `.el-select__tags` 容器 | 选择多个选项，生成逗号分隔值 |
| `el-select` | `el-transfer` | 穿梭框，有 `.el-transfer` 或左右面板结构 | 从左面板选择项移动到右面板 |
| `el-select` | `el-tree-select` | 树形选择，有 `.el-tree` 或层级结构 | 展开树节点，选择叶子节点 |

#### 11.3.2 JS 扫描侧分类逻辑

```javascript
function classifySelect(selectEl) {
    // 1. 检查是否为穿梭框（el-transfer）
    if (selectEl.classList.contains('el-transfer') ||
        selectEl.querySelector('.el-transfer__panel')) {
        return { kb_category: 'el-transfer', type: 'transfer' };
    }
    
    // 2. 检查是否为树形选择（el-tree-select）
    if (selectEl.classList.contains('el-tree-select') ||
        selectEl.querySelector('.el-tree')) {
        return { kb_category: 'el-tree-select', type: 'tree-select' };
    }
    
    // 3. 检查是否为多选（el-select-multiple）
    if (selectEl.classList.contains('el-select--multiple') ||
        selectEl.querySelector('.el-select__tags')) {
        return { kb_category: 'el-select-multiple', type: 'select-multiple' };
    }
    
    // 4. 默认为单选（el-select-single）
    return { kb_category: 'el-select-single', type: 'select-single' };
}

// 在 2.4 优先级中使用
selects.forEach((selectEl, idx) => {
    const classification = classifySelect(selectEl);
    const labelSuffix = selects.length > 1 ? `(${idx + 1})` : '';
    fields.push({
        label: label + labelSuffix,
        type: classification.type,
        kb_category: classification.kb_category,
        selector: _buildComponentSelector(fi, '.el-select', idx),
        required,
    });
});
```

#### 11.3.3 Python 侧填充策略适配

```python
# fill_multi_step_fields 中
if kb_cat == 'el-select-single':
    # 单选：选择第一个选项或默认值
    success, detail = await executor.execute_with_details(...)
elif kb_cat == 'el-select-multiple':
    # 多选：选择多个选项
    success, detail = await executor.execute_with_details(...)
elif kb_cat == 'el-transfer':
    # 穿梭框：从左面板选择项移动到右面板
    success, detail = await executor.execute_with_details(...)
elif kb_cat == 'el-tree-select':
    # 树形选择：展开树节点，选择叶子节点
    success, detail = await executor.execute_with_details(...)
```

### 11.4 方案三：多 select 场景处理

#### 11.4.1 检测 form-item 内的所有 select

```javascript
const selects = Array.from(fi.querySelectorAll('.el-select'));

if (selects.length > 0) {
    // 逐个分类并添加
    selects.forEach((selectEl, idx) => {
        const classification = classifySelect(selectEl);
        
        // 多个 select 时添加序号后缀
        const labelSuffix = selects.length > 1 ? `(${idx + 1})` : '';
        
        fields.push({
            label: label + labelSuffix,
            type: classification.type,
            kb_category: classification.kb_category,
            selector: _buildComponentSelector(fi, '.el-select', idx),
            required,
        });
    });
    
    // 检查是否有独立 input（排除所有 select 内部的搜索框）
    const independentInputs = Array.from(fi.querySelectorAll('input:...'))
        .filter(inp => !selects.some(s => s.contains(inp)) && ...);
    
    independentInputs.forEach(inp => {
        fields.push({ type: 'input', kb_category: 'input-generic', ... });
    });
}
```

#### 11.4.2 授权主体字段处理

```javascript
// 授权主体：2 个 el-select
[D5-composite] label='授权主体' selectCount=2 independentInputs=1 totalInputs=3

// 改进后的扫描结果
字段[7]  授权主体(1) → el-select-single  (类型选择器)
字段[8]  授权主体(2) → el-select-single  (实体选择器)
// 字段[8] 的搜索框被过滤，不再误判为独立 input
```

### 11.5 实施计划

#### Phase 1：复合组件检测增强（P7 + P8）

**目标**：解决子网掩码和授权主体问题

**修改文件**：`core/discovery/replay/form_filler.py`

**修改点**：
1. 优先级 2.1：使用 `querySelectorAll` 找所有 select
2. 优先级 2.1：过滤掉所有 select 内部的 input（搜索框）
3. 优先级 2.1：添加 `matchCompositePattern` 模式匹配函数
4. 优先级 2.1：新增 `ip-with-mask` 模式处理

**验证**：重跑 Stage 1，确认子网掩码和授权主体字段正确识别

#### Phase 2：el-select 类型分类（P9）

**目标**：区分单选/多选/穿梭框/树形选择

**修改文件**：
- `core/discovery/replay/form_filler.py`（JS 扫描侧）
- `core/discovery/replay/multi_step_executor.py`（Python 填充侧）

**修改点**：
1. JS 侧：添加 `classifySelect` 函数
2. JS 侧：在 2.4 优先级中使用分类逻辑
3. Python 侧：在 `fill_multi_step_fields` 中适配不同 kb_category
4. `const.py`：更新 `MULTI_STEP_TYPES` 列表

**验证**：
- 单选：选择第一个选项
- 多选：选择多个选项
- 穿梭框：从左面板移动到右面板
- 树形选择：展开并选择叶子节点

#### Phase 3：泛化级联检测（P10）

**目标**：统一处理 radio/select 的级联依赖

**修改文件**：`core/discovery/replay/form_filler.py`

**修改点**：
1. 提取通用的 `_detect_cascade` 函数（复用 D6 逻辑）
2. 在 `fill_multi_step_fields` 中对所有可交互组件调用级联检测
3. 自动填充级联渲染出的新字段

**验证**：确认级联字段自动填充

### 11.6 优先级与依赖

| 阶段 | 任务 | 优先级 | 复杂度 | 依赖 |
|------|------|--------|--------|------|
| Phase 1 | 复合组件检测增强 | P1 | 中 | 无 |
| Phase 2 | el-select 类型分类 | P2 | 高 | Phase 1 |
| Phase 3 | 泛化级联检测 | P3 | 低 | Phase 1 |

**建议实施顺序**：
1. **Phase 1**（立即实施）：解决子网掩码和授权主体问题
2. **Phase 2**（后续实施）：完善 el-select 类型分类
3. **Phase 3**（可选）：泛化级联检测

### 11.7 总结

**D5/D6 验证结论**：
- ✅ 子网掩码字段：确认 `isIpMaskCandidate=True`，需要增强 2.1 检测逻辑
- ✅ 授权主体字段：确认 `selectCount=2`，需要使用 `querySelectorAll` 找所有 select
- ✅ 级联渲染：不存在！问题纯粹是扫描检测错误

**泛化方案**：
- 方案一：复合组件通用模式识别（解决子网掩码问题）
- 方案二：el-select 类型分类（完善选择框处理）
- 方案三：多 select 场景处理（解决授权主体问题）

**下一步**：实施 Phase 1，增强 2.1 优先级的复合组件检测逻辑
