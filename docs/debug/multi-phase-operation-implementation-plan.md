# 多阶段操作架构实施方案 - 完整分析与验证

## 1. 方案概述

### 1.1 核心目标

支持业务操作的多阶段执行：
- **同页面多API链**：一个操作触发多个相关API调用（如重置密码需要 generate → reset）
- **跨页面流程**：一个操作跨越多个页面完成（如订单：配置页 → 确认页）

### 1.2 架构设计

在现有 manifest 结构中引入 `phases` 字段：

```json
{
  "action": "重置密码",
  "label": "重置密码",
  "phases": [
    {
      "id": "generate",
      "api": {"method": "POST", "pathname": "/password-reset/generate"},
      "body_template": {"userId": "xxx"},
      "body_field_roles": {"userId": {"role": "id_ref"}},
      "extract": [
        {"name": "generate_password", "path": "entity.password", "used_by": ["main"]}
      ]
    },
    {
      "id": "main",
      "api": {"method": "POST", "pathname": "/password-reset/reset"},
      "body_template": {"userId": "xxx", "newPassword": "yyy"},
      "body_field_roles": {
        "userId": {"role": "id_ref"},
        "newPassword": {"role": "phase_ref", "source": "generate.generate_password"}
      }
    }
  ],
  "requires": ["id"]
}
```

**向后兼容**：单API操作用 `phases: [single_phase]` 表示，运行时统一处理。

---

## 2. 识别的关键冲突与问题

### 2.1 冲突 #1: Phase B 参数清单构建会遍历所有 endpoints ⚠️ **严重**

**位置**：`analyze_flow.py:1571-1593`

**问题描述**：
```python
for category, endpoints in core_apis.items():
    for ep in endpoints:  # ← 会遍历 chain candidate
        body_sample = _parse_body(ep)
        # 构建 param_inventory...
```

当 `core_apis["重置密码"]` 包含 `[main_ep, generate_ep]` 时：
- 两个 endpoint 的 body 字段都会进入 `param_inventory`
- `field_resolutions["重置密码.userId"]` 会被 `generate_ep` 的解析结果覆盖 `main_ep` 的解析结果
- 导致字段来源解析错误

**影响范围**：Phase B 的核心匹配逻辑

**解决方案**：
```python
# 方案 A：只处理 main endpoint（推荐）
for category, endpoints in core_apis.items():
    main_ep = endpoints[0]  # 始终取第一个作为 main
    body_sample = _parse_body(main_ep)
    # 构建 param_inventory...

# 方案 B：在 key 中包含 endpoint 标识
key = f"{param['step_action']}.{ep['pathname']}.{param['field_name']}"
```

**推荐方案 A**：简单且符合语义（只有 main phase 需要 pre_api_ref）。

---

### 2.2 冲突 #2: Pass 2 保护列表缺少 phase_ref ⚠️ **严重**

**位置**：`analyze_flow.py:1488`

**问题描述**：
```python
if existing_role in ("name", "mutable", "context", "id_ref"):
    continue  # 保护这些角色不被 Phase B 覆盖
```

当前保护列表不包含 `phase_ref`。执行流程：
1. `_make_phased_step` 设置 `newPassword: {role: "phase_ref", ...}`
2. Pass 2 执行，检查 `existing_role == "phase_ref"`
3. 不在保护列表中 → 被 Phase B 的 `pre_api_ref` 或 `generate` 覆盖
4. 丢失 phase 间的数据流

**影响范围**：phases 架构的核心数据流

**解决方案**：
```python
if existing_role in ("name", "mutable", "context", "id_ref", "phase_ref"):
    continue
```

---

### 2.3 冲突 #3: _derive_dependencies 会污染 id_field_details ⚠️ **中等**

**位置**：`analyze_flow.py:364, 378`

**问题描述**：
```python
for ep in endpoints:  # ← 遍历所有 endpoints，包括 chain candidates
    # 提取 ID 字段...
    id_field_details[field_name] = {
        "source_action": action,  # ← 可能错误归因到 chain action
        "sample_value": sample_value,
        "path": field_path,
    }
```

如果 `generate_ep` 的响应包含 `userId`：
- `id_field_details["userId"]` 的 `source_action` 被设置为 `"重置密码"`
- 实际上应该来自 `"创建用户"`

**影响范围**：`id_field_details` 的准确性

**解决方案**：
```python
# 只处理 main endpoint
for category, endpoints in core_apis.items():
    main_ep = endpoints[0]
    # 提取 ID 字段...
```

---

### 2.4 冲突 #4: _derive_state_rules 可能被 chain candidate 干扰 ⚠️ **低-中**

**位置**：`analyze_flow.py:497`

**问题描述**：
```python
for ep in endpoints:  # ← 遍历所有 endpoints
    # 提取 state 字段...
    if category not in state_values:
        state_values[category] = vals["value"]  # 首次写入生效
```

如果 `generate_ep` 的响应包含 `state: "GENERATED"`：
- `state_values["重置密码"]` 被设置为 `"GENERATED"`
- 断言期望值错误

**影响范围**：状态断言的准确性（但通常 main_ep 在前，首次写入保护有效）

**解决方案**：
```python
# 只处理 main endpoint
for category, endpoints in core_apis.items():
    main_ep = endpoints[0]
    # 提取 state 字段...
```

---

### 2.5 问题 #5: 运行时缺少 phase 循环 🔴 **关键缺失**

**位置**：`test_runtime.py:execute()`

**问题描述**：
当前 `execute()` 方法直接构建 URL/body 并发送请求，没有 phase 循环逻辑：
```python
def execute(self, step_def: dict) -> bool:
    url = self._build_url(step_def["api"])
    body = self._build_body(step_def)
    resp = self._send_request(method, url, body)
    # 没有 phase 遍历
```

**影响范围**：phases 架构无法执行

**解决方案**：
```python
def execute(self, step_def: dict) -> bool:
    phases = step_def.get("phases")
    if phases:
        return self._execute_phased_step(step_def, phases)
    # 原有单 API 逻辑...
```

---

### 2.6 问题 #6: 缺少 phase 级别的日志记录 🔴 **关键缺失**

**位置**：`test_runtime.py:_write_log()`

**问题描述**：
`_write_log` 没有 `phase_index` 或 `phase_name` 字段。当 phase 2/3 失败时，无法从日志中识别是哪个 phase。

**影响范围**：调试和问题定位

**解决方案**：
```python
def _write_log(self, action, label, resp, result, message="", phase_info=None):
    entry = {
        # ... 现有字段 ...
        "phase": phase_info,  # 新增
    }
```

---

### 2.7 问题 #7: 单一响应契约用于所有 phases ⚠️ **高**

**位置**：`test_runtime.py:_assert_response()`

**问题描述**：
```python
def _assert_response(self, resp: requests.Response, label: str):
    assert self.parser.is_success(resp.status_code, resp_json)
```

使用同一个 `self.parser`（从 `manifest["response_contract"]` 构建）检查所有 phases。如果某个 phase 的响应结构不同（如 `{"code": 0}` vs `{"success": true}`），断言会失败。

**影响范围**：phases 的断言逻辑

**解决方案**：
```python
# 方案 A：每个 phase 可选自定义 success_check
phase_success_check = phase.get("success_check")
if phase_success_check:
    # 使用 phase 级 success check
else:
    # 使用全局 success check

# 方案 B：phases 只检查 HTTP 状态码（简单但宽松）
assert 200 <= resp.status_code < 300
```

---

### 2.8 问题 #8: _build_body 不支持嵌套对象中的动态字段 ⚠️ **中**

**位置**：`test_runtime.py:_build_body()`

**问题描述**：
```python
for key, value in body_template.items():
    # 只处理顶层字段
    role_config = field_roles.get(key, {"role": "static"})
```

如果 `passwordPolicy.userId` 需要动态替换，当前逻辑无法处理（`passwordPolicy` 整体作为 static 复制）。

**影响范围**：复杂请求体的构建

**当前状态**：重置密码场景中 `passwordPolicy` 是完整对象，不需要动态替换内部字段。此问题暂不影响，但未来可能需要支持。

**解决方案（未来扩展）**：
```python
# 递归处理嵌套对象
def _build_body_recursive(self, template, roles):
    for key, value in template.items():
        if isinstance(value, dict):
            nested_roles = roles.get(key, {})
            body[key] = self._build_body_recursive(value, nested_roles)
        else:
            # 应用 role 逻辑...
```

---

### 2.9 问题 #9: _build_url 的 {id} 替换是全局的 ⚠️ **中**

**位置**：`test_runtime.py:_build_url()`

**问题描述**：
```python
if "{id}" in pathname:
    id_val = self.state.get("id", "")
    pathname = pathname.replace("{id}", str(id_val))
```

只支持 `{id}` 占位符。如果 phase 的 pathname 需要其他动态参数（如 `/orders/{orderId}/confirm`），无法替换。

**影响范围**：URL 构建的灵活性

**当前状态**：重置密码场景不涉及此问题。未来多阶段流程可能需要。

**解决方案（未来扩展）**：
```python
# 支持任意 state key 替换
import re
placeholders = re.findall(r'\{(\w+)\}', pathname)
for key in placeholders:
    val = self.state.get(key, "")
    pathname = pathname.replace(f"{{{key}}}", str(val))
```

---

### 2.10 问题 #10: prepare_create_body 不感知 phases ⚠️ **中**

**位置**：`test_runtime.py:prepare_create_body()`

**问题描述**：
```python
def prepare_create_body(self, step_def: dict):
    body_template = step_def.get("body_template", {})
    # 只处理顶层 body_template，不处理 phases
```

如果 create 操作有 phases（如：查询配置 → 提交创建 → 确认创建），`prepare_create_body` 只处理顶层，不处理各 phase 的 body。

**影响范围**：多阶段创建操作的 body 准备

**当前状态**：重置密码场景不涉及 create 操作。

**解决方案**：
```python
def prepare_create_body(self, step_def: dict):
    phases = step_def.get("phases")
    if phases:
        # 找到 main phase 并处理其 body_template
        main_phase = next((p for p in phases if p.get("id") == "main"), phases[-1])
        body_template = main_phase.get("body_template", {})
        # 处理逻辑...
```

---

## 3. 问题影响矩阵

| 问题编号 | 严重性 | 影响范围 | 是否阻塞 P0 | 是否阻塞 P1 | 解决优先级 |
|---------|--------|---------|------------|------------|-----------|
| #1 | 严重 | Phase B 参数清单 | ✅ 是 | ✅ 是 | P0 |
| #2 | 严重 | Pass 2 保护 | ✅ 是 | ✅ 是 | P0 |
| #3 | 中等 | id_field_details | ✅ 是 | ✅ 是 | P0 |
| #4 | 低-中 | state_rules | ❌ 否 | ❌ 否 | P1 |
| #5 | 关键 | 运行时执行 | ✅ 是 | ✅ 是 | P0 |
| #6 | 关键 | 日志记录 | ✅ 是 | ✅ 是 | P0 |
| #7 | 高 | 响应断言 | ✅ 是 | ✅ 是 | P0 |
| #8 | 中 | 嵌套对象 | ❌ 否 | ❌ 否 | P2 |
| #9 | 中 | URL 替换 | ❌ 否 | ❌ 否 | P2 |
| #10 | 中 | create_body | ❌ 否 | ❌ 否 | P2 |

---

## 4. 解决方案的完整性评估

### 4.1 方案是否能完全解决问题？

**部分解决**：

✅ **能解决**：
- 同页面多API链（如重置密码的 generate → reset）
- phase 间的数据流（通过 `phase_ref` 角色）
- 向后兼容（单API操作用 `phases: [single_phase]`）

❌ **不能完全解决**：
- 跨页面流程（需要 Stage 1/2 的额外改造，见第 5 节）
- 嵌套对象的动态字段（问题 #8，当前场景不需要）
- 任意 URL 占位符（问题 #9，当前场景不需要）

### 4.2 是否会引入新问题？

**可能引入的新问题**：

1. **Phase B 冲突**（问题 #1）：必须修复，否则字段来源解析错误
2. **Pass 2 覆盖**（问题 #2）：必须修复，否则 phase_ref 丢失
3. **id_field_details 污染**（问题 #3）：必须修复，否则 id_ref 分类错误
4. **state_rules 污染**（问题 #4）：低概率，但建议修复
5. **断言失败**（问题 #7）：如果 phase 响应结构不同，断言会失败

### 4.3 问题之间是否存在相互影响？

**存在强耦合**：

```
问题 #1（Phase B 遍历）
  ↓ 导致
问题 #3（id_field_details 污染）
  ↓ 导致
_classify_body_fields 错误分类
  ↓ 导致
问题 #2（Pass 2 覆盖 phase_ref）

问题 #5（缺少 phase 循环）
  ↓ 导致
问题 #6（缺少 phase 日志）

问题 #7（单一响应契约）
  ↓ 可能导致
phases 执行失败（如果响应结构不同）
```

**必须按依赖顺序解决**：
1. 先解决 #1, #3（数据源层面）
2. 再解决 #2（保护层面）
3. 再解决 #5, #6, #7（运行时层面）
4. 最后解决 #4（优化层面）

---

## 5. 跨页面流程的额外问题

### 5.1 Stage 1 当前状态

**已支持**：
- `_explore_and_operate_in_new_page()` 能探索新页面
- 能识别表单、填充数据、提交

**缺失**：
- 不生成 playbook steps（只有 `nav_info` 元数据）
- `nav_info` 缺少 Playwright locator
- `sub_operations` 字段从未被填充（死代码路径）

### 5.2 Stage 2 当前状态

**已支持**：
- `_step_navigate_back()` 能返回原页面
- `wait_for_dialog` 支持 `page-nav` 模式

**缺失**：
- 没有 `_step_navigate_forward()` 前向导航
- 没有跨页面 context 标记

### 5.3 跨页面流程的解决方案

需要额外改造（作为 P3 优先级）：

**Stage 1**：
```python
# _explore_and_operate_in_new_page() 中
nav_info["sub_steps"] = [
    {"action": "fill_form", "fields": [...], "locators": [...]},
    {"action": "click_button", "playwright_locator": "..."},
    {"action": "assert_success", "playwright_locator": "..."}
]
```

**Stage 2**：
```python
async def _step_navigate_forward(page, step):
    url = step.get("url")
    await page.goto(url, wait_until="domcontentloaded")
```

**Stage 3**：
- 识别跨页面操作的多个 API（来自不同页面的 context）
- 生成包含多个 phases 的 step

---

## 6. 完整实施计划

### Phase 0: 修复 Phase B 冲突（阻塞性）

**改动文件**：`core/discovery/analyze_flow.py`

#### 改动 0.1: trace_pre_api_dependencies 只处理 main endpoint

```python
# L1571-1593
for category, endpoints in core_apis.items():
    main_ep = endpoints[0]  # ★ 只取 main endpoint
    body_sample = _parse_body(main_ep)
    if not body_sample or not isinstance(body_sample, dict):
        continue
    for field_name, field_value in body_sample.items():
        # ... 构建 param_inventory
```

#### 改动 0.2: _derive_dependencies 只处理 main endpoint

```python
# L362-393
for action in operation_order:
    endpoints = core_apis.get(action, [])
    main_ep = endpoints[0] if endpoints else None  # ★ 只取 main endpoint
    if not main_ep:
        continue
    pn = main_ep["pathname"]
    # ... 提取 ID 字段
```

#### 改动 0.3: _derive_state_rules 只处理 main endpoint

```python
# L496-525
for category, endpoints in core_apis.items():
    main_ep = endpoints[0] if endpoints else None  # ★ 只取 main endpoint
    if not main_ep:
        continue
    pn = main_ep["pathname"]
    # ... 提取 state 字段
```

---

### Phase 1: 同页面多API链支持

**改动文件**：
- `core/discovery/analyze_flow.py`
- `lib/runtime/test_runtime.py`

#### 改动 1.1: _build_core_apis_from_core_api_map 保留所有写候选

```python
# L198-223
def _build_core_apis_from_core_api_map(core_api_map: dict, infra_apis: set) -> dict:
    core_apis = {}
    for action_name, candidates in core_api_map.items():
        if action_name == "init" or not candidates:
            continue

        selected = _select_core_api(candidates, "step")
        if not selected:
            continue

        core_apis[action_name] = [selected]

        # ★ 保留其他写操作候选（操作链）
        others = [
            c for c in candidates
            if c is not selected
            and c.get("method", "").upper() in ("POST", "PUT", "PATCH", "DELETE")
            and c.get("body_field_count", 0) > 0
        ]
        for other in others:
            core_apis[action_name].append(other)
            LOG.info(f"  操作链: {action_name} 追加 "
                     f"{other['method']} {other['pathname'].split('/')[-1]}")

    return core_apis
```

#### 改动 1.2: build_manifest 生成 phases 结构

```python
# L1372-1387
for action in crud_order:
    eps = core_apis.get(action, [])
    if not eps:
        continue

    if len(eps) == 1:
        # 单 API 操作（向后兼容）
        ep = eps[0]
        body_sample = _parse_body(ep)
        steps.append(_make_step(action, ep, body_sample))
    else:
        # ★ 多 API 操作链：生成 phases
        step = _make_phased_step(action, eps)
        steps.append(step)

    # 验证步骤（保持不变）
    for verify_action, label, assertion in _plan_verify_steps(action):
        verify = _make_verify_step(verify_action, label, assertion)
        if verify:
            steps.append(verify)
```

#### 改动 1.3: 新增 _make_phased_step 函数

```python
def _make_phased_step(action, eps):
    """构建多阶段步骤。
    
    eps[0] = main API（最后执行）
    eps[1:] = prepare APIs（按 body_field_count 降序执行）
    """
    main_ep = eps[0]
    prepare_eps = list(reversed(eps[1:]))  # 少的先执行
    
    phases = []
    all_extracts = {}  # {field_name: "phase_id.field_name"}
    
    # 构建 prepare phases
    for pep in prepare_eps:
        phase_id = pep["pathname"].split("/")[-1]
        pbody = _parse_body(pep)
        proles = _classify_body_fields(
            pbody, id_field_details, create_body_sample, context_field_names
        )
        
        # 推导 extract
        extracts = _infer_phase_extracts(pep, main_ep, all_extracts)
        
        phases.append({
            "id": phase_id,
            "label": f"{action}({phase_id})",
            "api": {
                "method": pep["method"],
                "pathname": re.sub(r"[0-9a-f]{20,}", "{id}", pep["pathname"]),
                "query_params": pep.get("query_params", {}),
            },
            "body_template": pbody or {},
            "body_field_roles": proles,
            "extract": extracts,
        })
    
    # 构建 main phase
    main_body = _parse_body(main_ep)
    main_roles = _classify_body_fields(
        main_body, id_field_details, create_body_sample, context_field_names
    )
    
    # 应用 phase_ref
    _apply_phase_refs(main_roles, all_extracts)
    
    phases.append({
        "id": "main",
        "label": action,
        "api": {
            "method": main_ep["method"],
            "pathname": re.sub(r"[0-9a-f]{20,}", "{id}", main_ep["pathname"]),
            "query_params": main_ep.get("query_params", {}),
        },
        "body_template": main_body if isinstance(main_body, list) else (main_body or {}),
        "body_field_roles": main_roles,
    })
    
    # extract（如果 action == id_producer）
    extract = None
    if action == id_producer:
        envelope_keys = response_contract["envelope_keys"]
        id_field = response_contract["id_field"]
        extract = {
            "id": f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field,
            "names": [k for k, v in main_roles.items() if v.get("role") == "name"],
        }
    
    step = {
        "action": action,
        "label": (ui_result or {}).get("button_labels", {}).get(action) or action,
        "phases": phases,
        "requires": [] if action == id_producer else ["id"],
    }
    if extract:
        step["extract"] = extract
    return step
```

#### 改动 1.4: 新增 _infer_phase_extracts 辅助函数

```python
def _infer_phase_extracts(phase_ep, main_ep, all_extracts):
    """推导 phase 的 extract 映射。
    
    策略：检查 phase 响应中的字段，如果字段名在 main body 中出现，
    建立 extract 映射。
    """
    from . import const
    pathname = phase_ep["pathname"]
    samples = response_samples.get(pathname, [])
    if not samples:
        return []
    
    try:
        resp_body = json.loads(samples[0].get("body", "{}"))
    except Exception:
        return []
    
    entity = None
    for key in const.ENVELOPE_KEY_CANDIDATES:
        if key in resp_body and isinstance(resp_body[key], dict):
            entity = resp_body[key]
            break
    if entity is None:
        entity = resp_body
    if not isinstance(entity, dict):
        return []
    
    main_body = _parse_body(main_ep)
    if not isinstance(main_body, dict):
        return []
    
    phase_id = pathname.split("/")[-1]
    extracts = []
    for field_name, value in entity.items():
        if value is None:
            continue
        # 字段名在 main body 中出现 → 建立映射
        if field_name in main_body:
            state_key = f"{phase_id}_{field_name}"
            extracts.append({
                "name": state_key,
                "path": f"entity.{field_name}",
                "used_by": ["main"],
            })
            all_extracts[field_name] = f"{phase_id}.{state_key}"
    
    return extracts
```

#### 改动 1.5: 新增 _apply_phase_refs 辅助函数

```python
def _apply_phase_refs(field_roles, all_extracts):
    """将 main phase 的字段引用前置 phase 的 extract。"""
    for field_name, source in all_extracts.items():
        if field_name in field_roles:
            existing_role = field_roles[field_name].get("role", "")
            # 只覆盖 static/generate/test_value，不覆盖 id_ref/name/mutable/context
            if existing_role in ("static", "generate", "test_value"):
                field_roles[field_name] = {
                    "role": "phase_ref",
                    "source": source,
                }
```

#### 改动 1.6: Pass 2 保护 phase_ref

```python
# L1488
if existing_role in ("name", "mutable", "context", "id_ref", "phase_ref"):
    continue
```

#### 改动 1.7: Runtime 支持 phases 执行

```python
# test_runtime.py:execute()
def execute(self, step_def: dict) -> bool:
    action = step_def.get("action", "unknown")
    label = step_def.get("label", action)
    
    # ★ 多阶段操作
    phases = step_def.get("phases")
    if phases:
        return self._execute_phased_step(step_def, phases)
    
    # 原有单 API 逻辑...
```

#### 改动 1.8: 新增 _execute_phased_step 方法

```python
def _execute_phased_step(self, step_def: dict, phases: list) -> bool:
    """执行多阶段操作步骤。"""
    action = step_def.get("action", "")
    label = step_def.get("label", action)
    
    # requires 检查
    requires = step_def.get("requires", [])
    for req in requires:
        if not self.state.get(req):
            print(f"  ⚠️ 跳过[{label}]: {req} 为空")
            self._write_log(action, label, None, "skipped", f"{req} 为空")
            return False
    
    last_resp = None
    for i, phase in enumerate(phases):
        phase_id = phase.get("id", f"phase_{i}")
        phase_label = phase.get("label", f"{label}:{phase_id}")
        
        print(f"\n  [{phase_label}] {phase['api']['pathname']}")
        
        try:
            url = self._build_url(phase["api"])
            body = self._build_body(phase)
            method = phase["api"]["method"].upper()
            
            resp = self._send_request(method, url, body)
            
            # phase extract → state
            extracts = phase.get("extract", [])
            if extracts:
                resp_json = resp.json()
                for ext in extracts:
                    name = ext.get("name", "")
                    path = ext.get("path", "")
                    value = self._extract_with_array_index(resp_json, path)
                    if value is not None:
                        self.state[name] = value
                        print(f"    ✅ 提取: {name}={str(value)[:50]}")
            
            last_resp = resp
            
            # 最后一个 phase（main）做断言
            if i == len(phases) - 1:
                self._assert_response(resp, label)
                if step_def.get("extract"):
                    self._extract_state(resp.json(), step_def)
            
            # phase 级日志
            self._write_log(action, label, resp, "passed", "", 
                          phase_info={"index": i, "id": phase_id})
            
        except Exception as e:
            print(f"    ❌ {phase_label} 失败: {e}")
            self._write_log(action, label, None, "failed",
                          f"phase {phase_id}: {e}",
                          phase_info={"index": i, "id": phase_id})
            return False
    
    print(f"  ✅ {label}成功")
    return True
```

#### 改动 1.9: _build_body 支持 phase_ref 角色

```python
# _build_body() 中添加
elif role == "phase_ref":
    source = role_config.get("source", "")
    # source 格式: "phase_id.state_key"
    if "." in source:
        state_key = source.split(".", 1)[1]
        resolved = self.state.get(state_key)
    else:
        resolved = self.state.get(source)
    
    if resolved is None:
        # 兜底：和 pre_api_ref 相同的 UUID 生成逻辑
        vtype = role_config.get("value_type", "hex_id")
        if vtype == "hex_id":
            resolved = uuid.uuid4().hex
        elif vtype == "uuid":
            resolved = str(uuid.uuid4())
        else:
            resolved = value
        print(f"    ⚠️ {key}: phase 提取失败，生成 {vtype}: {str(resolved)[:20]}")
    
    body[key] = resolved
```

#### 改动 1.10: _write_log 支持 phase_info

```python
def _write_log(self, action, label, resp, result, message="", phase_info=None):
    # ... 现有逻辑 ...
    entry = {
        # ... 现有字段 ...
        "phase": phase_info,  # 新增
    }
```

---

### Phase 2: gen_test / export 适配（可选）

**改动文件**：
- `core/discovery/gen_test.py`
- `core/discovery/export_artifacts.py`

#### 改动 2.1: gen_test 展开 phases

```python
# 在生成脚本时，检测 phases 字段
if "phases" in step:
    # 展开 phases 为多个 API 调用
    for phase in step["phases"]:
        # 生成 phase 级代码
```

#### 改动 2.2: export_artifacts 展开 phases

```python
# 在生成 Postman Collection 时，展开 phases
if "phases" in step:
    for phase in step["phases"]:
        # 添加 phase 级 request
```

---

### Phase 3: 跨页面流程支持（未来）

**改动文件**：
- `core/discovery/discover_ui.py`
- `core/discovery/replay/replay_engine.py`
- `core/discovery/capture_apis.py`

#### 改动 3.1: Stage 1 生成跨页面 steps

```python
# _explore_and_operate_in_new_page() 中
nav_info["sub_steps"] = [
    {"action": "fill_form", "fields": [...], "locators": [...]},
    {"action": "click_button", "playwright_locator": "..."},
]
```

#### 改动 3.2: Stage 2 支持前向导航

```python
async def _step_navigate_forward(page, step):
    url = step.get("url")
    await page.goto(url, wait_until="domcontentloaded")
```

#### 改动 3.3: Stage 3 识别跨页面 API

```python
# 在 deduplicate_calls 中，跨页面操作的 API 来自不同 context
# 需要合并到同一个 action 下
```

---

## 7. 验证计划

### 7.1 Phase 0 验证（Phase B 冲突修复）

**验证步骤**：
1. 运行 Stage 345（不启用 phases）
2. 检查 manifest 中 `重置密码` 是否仍为单 API step
3. 运行测试脚本，确认 14/15 通过（重置密码仍失败）

**预期结果**：现有逻辑不受影响

### 7.2 Phase 1 验证（同页面多API链）

**验证步骤**：
1. 运行 Stage 345（启用 phases）
2. 检查 manifest 中 `重置密码` 是否有 `phases` 数组
3. 检查 `generate` phase 是否有 `extract`
4. 检查 `main` phase 的 `newPassword` 是否为 `phase_ref`
5. 运行测试脚本
6. 检查运行时输出是否有 `[重置密码(generate)]` 和 `[重置密码]` 子步骤
7. 确认 15/15 通过

**预期结果**：
- manifest 包含 phases 结构
- 运行时按顺序执行 generate → main
- newPassword 来自 generate 的返回
- 所有 15 个步骤通过

### 7.3 回归测试

**验证步骤**：
1. 运行所有单元测试：`python -m pytest tests/ -q`
2. 运行用户管理模块全量测试
3. 运行角色管理模块测试（如果有）
4. 确认无新增失败

**预期结果**：
- 单元测试全部通过
- 现有模块测试无回归

---

## 8. 风险评估

### 8.1 高风险项

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Phase B 冲突未修复导致字段来源错误 | 高 | 严重 | Phase 0 优先修复 |
| Pass 2 覆盖 phase_ref | 高 | 严重 | 加入保护列表 |
| phase 响应结构不同导致断言失败 | 中 | 高 | 支持 phase 级 success_check |

### 8.2 中风险项

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| phase 提取失败 | 低 | 中 | UUID 兜底 |
| 向后兼容性问题 | 低 | 中 | 单API操作用 `phases: [single_phase]` |

### 8.3 低风险项

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 嵌套对象动态字段 | 极低 | 低 | 当前场景不需要 |
| 任意 URL 占位符 | 极低 | 低 | 当前场景不需要 |

---

## 9. 实施顺序建议

### 9.1 立即实施（P0）

**Phase 0 + Phase 1**：
1. 修复 Phase B 冲突（改动 0.1-0.3）
2. 实现 phases 结构（改动 1.1-1.10）
3. 验证重置密码场景

**预计工作量**：2-3 天

### 9.2 后续实施（P1）

**Phase 2**：
1. gen_test 适配 phases
2. export_artifacts 适配 phases

**预计工作量**：1 天

### 9.3 未来实施（P2/P3）

**Phase 3**：
1. 跨页面流程支持
2. 嵌套对象动态字段
3. 任意 URL 占位符

**预计工作量**：5-7 天

---

## 10. 总结

### 10.1 方案完整性

✅ **能解决**：同页面多API链（如重置密码）  
❌ **不能完全解决**：跨页面流程（需要额外改造）

### 10.2 新引入问题

⚠️ **必须解决**：Phase B 冲突、Pass 2 覆盖、id_field_details 污染  
⚠️ **建议解决**：state_rules 污染、phase 级日志、phase 级断言

### 10.3 问题相互影响

存在强耦合，必须按依赖顺序解决：
1. 数据源层面（Phase B、_derive_dependencies、_derive_state_rules）
2. 保护层面（Pass 2）
3. 运行时层面（execute、_build_body、_write_log）

### 10.4 对正常逻辑的影响

✅ **向后兼容**：单API操作用 `phases: [single_phase]` 表示  
✅ **现有测试**：不影响现有模块测试  
⚠️ **需要回归**：修复 Phase B 冲突后需要回归测试

---

**文档版本**：v1.0  
**创建时间**：2026-09-17  
**状态**：待审批
