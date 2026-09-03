# Stage 3/4 质量修复方案：从 Stage 2 数据到 Stage 4 脚本的全链路修正

## 背景

Stage 2 成功捕获 22 个 API 端点，但 Stage 3 筛选和 Stage 4 生成存在多个质量问题：

| # | 问题 | 根因 | 影响 |
|---|------|------|------|
| 1 | query API 被错误映射为 policies/list | Stage 1 未为 query 生成专用步骤，Stage 2 未触发用户列表查询 | 脚本无法验证 CRUD 闭环 |
| 2 | update API 指向 suspend（冻结） | Stage 1 的 `_do_edit` 未返回 `trigger_locator_verified`，Stage 2 回放失败 | 真正的编辑 API 未捕获 |
| 3 | delete body_template 为空 | `_parse_body` 不支持数组格式请求体 | delete 操作无法注入 ID |
| 4 | execute 步骤包含辅助 API | 同现过滤阈值不够，辅助 API 未被过滤 | 脚本包含无意义的步骤 |
| 5 | Stage 4 验证器误报 | 验证器期望 `def test_` 函数，新架构用 manifest 模式 | 每次运行都输出错误日志 |

---

## 修改方案

### 修复 1：Stage 1 为 query 操作生成专用 playbook 步骤

**问题**：`_do_query` 返回信息极简（仅 `{"success": True, "selectors": {"trigger": btn_text}}`），`build_playbook` 没有 `_build_query_steps`，fallback 到 `_build_generic_steps` 生成了不完整的步骤。

**修改文件**：`module_discovery/discover_ui.py`

**方案**：

#### 1.1 增强 `_do_query` 返回值（~行 2007-2042）

```python
# 当前
result = {"success": True, "selectors": {"trigger": btn_text}}

# 修改后
result = {
    "success": True,
    "selectors": {"trigger": btn_text},
    "trigger_locator_verified": btn_click_result.get("tag", "button") 
                                + ":has-text('" + " ".join(btn_text.split()) + "')",
    "has_table": has_table,
    "row_count": row_count,  # 新增：查询前的行数
}
```

需要记录 `_click_button_escalating` 的结果（当前代码在检查 `clicked` 后丢弃了 `btn_click_result`），以及查询前后的表格行数。

#### 1.2 新增 `_build_query_steps` 函数（~行 3200 附近）

```python
def _build_query_steps(op_data: dict) -> list:
    """构建 query 操作步骤"""
    steps = []
    
    trigger_locator = op_data.get("trigger_locator_verified") 
                      or f"button:has-text('{op_data.get('selectors', {}).get('trigger', '')}')"
    steps.append({
        "action": "click_button",
        "playwright_locator": trigger_locator,
        "description": "点击查询按钮"
    })
    
    # 等待表格刷新（不检查成功消息）
    steps.append({
        "action": "wait_for_table_ready",
        "description": "等待表格数据刷新"
    })
    
    return steps
```

#### 1.3 在 `build_playbook` 中添加 query 分支（~行 3196）

```python
elif action == "query":
    op_steps.extend(_build_query_steps(op_data))
```

**验证标准**：
- playbook 的 `operations.query` 包含 `click_button` + `wait_for_table_ready` 两个步骤
- Stage 2 回放 query 时能触发用户列表查询 API
- Stage 3 正确识别 query API（用户列表端点而非策略列表端点）

---

### 修复 2：Stage 1 的 `_do_edit` 返回完整的按钮 locator

**问题**：Stage 2 回放 update 时失败（`编辑按钮不可见`）。根据设计原则，**Stage 2 只执行不探测**，所以修改方向是让 Stage 1 提供足够精确的信息。

**根因分析**：
- Stage 1 的 `_do_edit` 通过 `click_row_button_v2` 成功点击了编辑按钮（使用了 JS click 绕过可见性检查）
- 但 `_do_edit` 没有将实际使用的 locator 记录到返回值中
- `build_playbook` 的 `_build_update_steps` 因此缺少 `trigger_locator_verified`
- Stage 2 的 `_step_click_row_button` 使用简单的 CSS selector + `is_visible()` 检查，无法绕过可见性问题

**修改文件**：`module_discovery/discover_ui.py`

**方案**：

#### 2.1 增强 `_do_edit` 返回值（~行 2167-2280）

```python
# 当前：_do_edit 调用 click_row_button_v2 后只检查 clicked 结果
# 修改后：记录实际点击成功的按钮 locator

result = {
    "success": True,
    "selectors": {"trigger": btn_text, "row_selector": row_selector},
    "form_fields": edit_fields,
    "fill_rules": fill_rules,
    "fill_data": fill_data,
    # 新增：记录精确的行级按钮 locator
    "trigger_locator_verified": f"button:has-text('{' '.join(btn_text.split())}')",
    "is_row_action": True,  # 标记为行级操作
}
```

#### 2.2 确保 `_build_update_steps` 传递精确 locator（~行 3441）

```python
def _build_update_steps(op_data: dict) -> list:
    steps = []
    
    steps.append({"action": "find_row", "description": "定位目标数据行"})
    
    # 使用 Stage 1 提供的精确 locator
    if op_data.get("is_row_action"):
        trigger_locator = op_data.get("trigger_locator_verified")
        if trigger_locator:
            steps.append({
                "action": "click_row_button",
                "button_text": op_data.get("selectors", {}).get("trigger", ""),
                "playwright_locator": trigger_locator,  # 新增
                "description": f"点击行内编辑按钮"
            })
    # ... 后续步骤不变
```

#### 2.3 Stage 2 的 `_step_click_row_button` 使用 JS click（已修复）

> 注意：已在本次会话中修复（方案 A），使用 `page.evaluate` JS click 替代 Playwright click。
> 这属于 Stage 2 的**执行策略**优化（不改变 Stage 2 的纯执行原则），因为 JS click 是已知的 Element UI 固定列表格的兼容方案。

**验证标准**：
- playbook 的 `operations.update.steps` 中 `click_row_button` 包含 `playwright_locator`
- Stage 2 回放 update 时能成功点击编辑按钮
- Stage 3 正确捕获编辑 API（PUT /users/{id}）而非 suspend API

---

### 修复 3：`_parse_body` 支持数组格式请求体

**问题**：delete 的 body_template 为空，因为 `_parse_body` 遇到 JSON 数组时返回 `{}`。

**修改文件**：`module_discovery/analyze_flow.py`

**方案**：

#### 3.1 修改 `_parse_body`（~行 597-619）

```python
def _parse_body(ep_or_sample) -> dict | list:
    """解析请求体，支持 dict 和 array 格式"""
    # ... 前置逻辑不变 ...
    try:
        body = json.loads(sample) if isinstance(sample, str) else sample
        # 支持 dict 和 list
        if isinstance(body, (dict, list)):
            return body
        return {}
    except Exception:
        return {}
```

#### 3.2 修改 `_make_step` 处理数组 body（~行 1040-1074）

```python
def _make_step(action, ep, body_sample):
    # ...
    if isinstance(body_sample, list):
        # 数组格式：如 batch delete ["id1", "id2"]
        step["body_template"] = body_sample
        step["body_field_roles"] = {
            "__array_items__": {"role": "id_ref", "source": "create.id"}
        }
    else:
        step["body_template"] = body_sample or {}
        step["body_field_roles"] = _classify_body_fields(body_sample, ...)
```

#### 3.3 修改 `_derive_dependencies` 处理数组请求体（~行 424）

```python
# 当前
if not isinstance(req_body, dict):
    continue

# 修改后
if isinstance(req_body, list):
    # 数组格式：检查元素是否为 ID 字符串
    if req_body and isinstance(req_body[0], str) and len(req_body[0]) >= 8:
        # 可能是 ID 数组，记录注入点
        injections[field_name] = {"source": "create", "source_path": "entity.id"}
    continue
elif not isinstance(req_body, dict):
    continue
```

**验证标准**：
- manifest 的 delete step 的 `body_template` 为 `["{id}"]` 格式
- Stage 4 生成的脚本能正确注入 create 返回的 ID 到 delete 请求体

---

### 修复 4：Stage 3 增强 API 分类准确性

**问题**：
- `POST /draco/v1/policies/list`（策略列表）被错误分类为 query
- `GET /draco/v1/password-policy`（密码策略）被错误分类为 execute

**根因**：`_filter_core_apis` 的 Phase B（other_post/other_get 重分类）存在两个问题：
1. 基于响应结构（有无 list 键）分类，而非基于 API 路径语义
2. `target_cat` 在同一类别的所有端点间共享，导致后续端点被错误归类

**修改文件**：`module_discovery/analyze_flow.py`

**方案**：

#### 4.1 为每个端点独立分类（修复 target_cat 共享问题）

```python
# 当前：target_cat 在循环外声明，所有端点共享
for cat in ("other_post", "other_get"):
    target_cat = None  # ← 问题：共享
    for ep in core[cat]:
        ...
        target_cat = "query"  # ← 影响后续所有端点

# 修改后：每个端点独立决定目标分类
for cat in ("other_post", "other_get"):
    reclassified = defaultdict(list)
    for ep in core[cat]:
        ep_cat = None  # 独立变量
        ...
        if has_list_key:
            ep_cat = "query"
        elif "id" in entity:
            ep_cat = "execute"
        reclassified[ep_cat or "execute"].append(ep)
    
    for dest_cat, eps in reclassified.items():
        core[dest_cat] = core.get(dest_cat, []) + eps
    del core[cat]
```

#### 4.2 增加路径语义过滤（在 SUPPORTING_API_KEYWORDS 基础上扩展）

```python
# const.py 新增
# 与业务模块无关的通用 API 路径特征
INFRA_API_PATH_PATTERNS = [
    "/policies/",      # 策略/角色管理（非用户管理核心）
    "/password-policy", # 密码策略（辅助查询）
    "/dictionary",     # 字典
    "/authority/",     # 权限
]
```

```python
# analyze_flow.py _filter_core_apis Phase B 中新增
for ep in core[cat]:
    pn = ep["pathname"]
    # 路径语义过滤：如果 API 路径与当前模块无关，跳过
    if any(pat in pn for pat in const.INFRA_API_PATH_PATTERNS):
        LOG.debug(f"路径语义过滤: {pn} 被判定为基础设施 API")
        continue
    # ... 原有的响应结构分析 ...
```

#### 4.3 query API 的路径匹配验证

当 `_filter_core_apis` 将某个 API 分类为 `query` 时，验证其路径是否包含模块相关的关键词：

```python
# 在 Phase B 的 query 分类后
if ep_cat == "query":
    # 验证：query API 路径应包含模块名相关的关键词
    module_hint = _extract_module_hint(ui_result)  # 从 ui_result 提取模块名
    if module_hint and not _path_matches_module(pn, module_hint):
        LOG.info(f"  query API {pn} 路径不匹配模块 '{module_hint}'，降级为 execute")
        ep_cat = "execute"
```

**验证标准**：
- `POST /policies/list` 不再被分类为 query
- `GET /password-policy` 不再作为独立 execute 步骤
- 真正的用户列表查询 API（`GET /tenants/users`）被正确分类为 query

---

### 修复 5：Stage 4 验证器适配 manifest 架构

**问题**：`validate_stage4` 的两个检查与 manifest 架构不兼容：
- 检查 6（行 329）：要求 `def test_` 函数 — manifest 模式无此函数
- 检查 7（行 333-335）：要求 3+ 个 `assert` 语句 — manifest 模式无内联 assert

**修改文件**：`module_discovery/stage_validators.py`

**方案**：

#### 5.1 条件化检查 6 和 7

```python
def validate_stage4(script_content: str, script_path: str) -> Tuple[bool, List[str]]:
    # ... 检查 1-5 不变 ...
    
    is_manifest_mode = "MANIFEST" in script_content and "TestRunner" in script_content
    
    if not is_manifest_mode:
        # 检查 6: test_ 函数（仅旧架构需要）
        if "def test_" not in script_content:
            issues.append("脚本内未找到 test_ 函数")
        
        # 检查 7: assert 语句数量（仅旧架构需要）
        assert_count = script_content.count("assert ")
        if len(script_lines) >= 10 and assert_count < 3:
            issues.append(f"断言过少 ({assert_count} 个)")
    else:
        # manifest 模式的专用检查
        # 检查 6': steps 数量
        steps_count = script_content.count('"action":')
        if steps_count < 3:
            issues.append(f"manifest 步骤过少 ({steps_count} 个)")
        
        # 检查 7': 包含 create 和 delete 步骤
        if '"action": "create"' not in script_content:
            issues.append("manifest 缺少 create 步骤")
        if '"action": "delete"' not in script_content:
            issues.append("manifest 缺少 delete 步骤")
```

**验证标准**：
- manifest 模式的脚本通过 Stage 4 验证（无误报）
- 旧架构脚本仍被正确验证（兼容性保持）

---

## 修改优先级

| 优先级 | 修复项 | 改动量 | 预期效果 |
|--------|--------|--------|---------|
| **P0** | 修复 2：编辑按钮 locator | ~30 行 | Stage 2 能回放 update，捕获真正的编辑 API |
| **P0** | 修复 3：数组 body 支持 | ~20 行 | delete 操作能注入 ID |
| **P1** | 修复 1：query 专用步骤 | ~40 行 | Stage 2 触发用户列表查询，Stage 3 正确分类 |
| **P1** | 修复 4：API 分类增强 | ~50 行 | 消除辅助 API 的误分类 |
| **P2** | 修复 5：验证器适配 | ~15 行 | 消除误报日志 |

## 遗漏检查

### 可能遗漏的点

1. **`_do_query` 的表格行数记录**：query 操作可能不改变行数，assert 不应检查行数变化。`_build_query_steps` 的 `wait_for_table_ready` 只需等待表格加载完成，不需要 assert 行数。

2. **编辑按钮在固定列中的 locator**：Element UI 固定列表格中，编辑按钮可能在 `.el-table__fixed-right` 的 wrapper 中。Stage 1 通过 JS click 绕过了这个问题，但 playbook 中记录的 `button:has-text('编辑')` 在 Stage 2 中可能匹配到固定列中的隐藏占位按钮。

   → **解决**：在 `_step_click_row_button` 中使用 JS click（已修复），与 Stage 1 的 `_ensure_row_selected` 保持一致。这属于执行策略优化，不违反"Stage 2 纯执行"原则。

3. **delete 的 ID 注入时机**：manifest 的 delete step 的 `body_template` 是数组格式 `["{id}"]`，`test_runtime.py` 需要知道如何替换数组中的 ID。检查 `TestRunner` 是否支持数组 body 的 ID 注入。

4. **query API 的响应结构差异**：用户列表查询（`GET /tenants/users`）的响应是分页结构 `{"entity": {"total": N, "list": [...]}}`  而非简单数组。Stage 3 的 `_plan_verify_steps` 在生成 `contains_id` 断言时需要知道在 `entity.list` 中查找。当前代码已处理此情况（通过 `LIST_KEY_CANDIDATES` 配置）。

5. **`POST /policies/list` 的双重身份**：这个 API 在创建用户时被调用（获取角色列表），但它也出现在 query 验证步骤中（因为 Stage 2 没有捕获真正的用户列表查询 API）。修复 1 和 4 同时解决此问题。

### 端到端验证场景

修复完成后，预期的数据流：

```
Stage 1 → playbook:
  create: click_button + wait_dialog + fill_form + submit + assert
  query:  click_button + wait_table_ready           ← 修复 1
  update: find_row + click_row_button(locator) + ... ← 修复 2
  delete: find_row + select_checkbox + click + ...

Stage 2 → capture:
  create: POST /users ✅
  query:  GET /tenants/users ✅                      ← 修复 1 效果
  update: PUT /users/{id} ✅                         ← 修复 2 效果
  delete: DELETE /users/batch/delete (body: [id]) ✅  ← 修复 3 效果

Stage 3 → analysis:
  query: GET /tenants/users ✅                       ← 修复 4 效果
  policies/list: 被过滤（辅助 API）✅                ← 修复 4 效果

Stage 4 → script:
  通过验证，无误报 ✅                                 ← 修复 5 效果
  12 步骤，含 create/query/update/delete CRUD 闭环
```
