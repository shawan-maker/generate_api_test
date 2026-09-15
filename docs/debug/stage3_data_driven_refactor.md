# Stage 3/4 纯数据驱动改造方案（终版 v3）

**日期**: 2026-09-15  
**状态**: 待实施  
**影响范围**: `endpoint_classifier.py` + `capture_apis.py` + `analyze_flow.py` + `run.py` + `stage_validators.py` + `diagnostic_mode.py` + `lib/runtime/test_runtime.py` + `tests/test_module_discovery.py`

---

## 1. 背景

### 1.1 当前问题

Stage 2 重构后，`core_api_map` 的 key 变成了中文操作名。但下游有 **24+ 处硬编码** 检查 `"create"` / `"delete"` 英文字面量，导致验证器误报 + 运行时全部步骤跳过。

### 1.2 根因

代码试图从操作名推导角色（"这是不是 create？"），但操作名由 Stage 1 UI 探测决定，每个模块不同，推导必然不准确。

### 1.3 多候选问题

当前 Stage 2 对每个操作只输出一个核心 API（tiebreaker），但部分操作需要多个 API 配合（如"重置密码"含 GET + PUT）。

### 1.4 冗余字段问题

当前 Stage 2 输出两个字段：
- `classified`（`by_category`）：完整端点数据（bodies, query_params 等）
- `core_api_map`：只有 method + pathname

**决策：彻底删除 `classified` 字段**，将完整数据合并到 `core_api_map` 数组中。理由：
- `classified` 是旧架构的遗留物（基于 HTTP 方法分类）
- `core_api_map` 数组结构已能承载完整数据，无需两个字段
- 删除后可简化 Stage 3 数据流，避免双数据源一致性问题

---

## 2. 核心设计决策

### 2.1 合并为一个字段：`core_api_map` 数组 + 完整数据

**改前**（两个字段）：
```json
{
  "by_category": {
    "创建用户": [{"method": "POST", "pathname": "/users", "bodies": [...], "query_params": {...}, ...}]
  },
  "core_api_map": {
    "创建用户": {"method": "POST", "pathname": "/users"}
  }
}
```

**改后**（一个字段）：
```json
{
  "core_api_map": {
    "创建用户": [{
      "method": "POST",
      "pathname": "/users",
      "body_field_count": 10,
      "response_has_id": true,
      "contexts": ["replay:创建用户"],
      "bodies": ["{...}"],
      "request_body_sample": "{...}",
      "query_params": {},
      "query_params_samples": []
    }],
    "重置密码": [
      {"method": "GET", "pathname": "/password-policy", "body_field_count": 0, "response_has_id": false, "bodies": [], ...},
      {"method": "PUT", "pathname": "/users/{id}/password", "body_field_count": 3, "response_has_id": false, "bodies": ["{...}"], ...}
    ]
  }
}
```

**删除 `classified`（`by_category`）字段** — 不再需要。

**理由**：
- Stage 2 只负责**缩小范围**（Layer 0-2 过滤噪音）
- Stage 3 负责**最终选取**（`_select_core_api` 按职责选 API）
- 一个字段既是候选列表又包含完整端点数据，不需要查表

### 2.2 `_select_core_api()` 候选选取

```python
def _select_core_api(candidates, purpose="step"):
    """从候选列表中选取最合适的 API。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    if purpose == "id_extract":
        # 写操作 + 响应含 ID
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH") and c["response_has_id"]]
        if writes:
            return max(writes, key=lambda c: c["body_field_count"])

    elif purpose == "step":
        # 写操作优先，字段数最多
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH", "DELETE")]
        if writes:
            return max(writes, key=lambda c: c["body_field_count"])

    elif purpose == "pre_api":
        # 有请求体的操作
        with_body = [c for c in candidates if c["body_field_count"] > 0]
        if with_body:
            return max(with_body, key=lambda c: c["body_field_count"])

    return max(candidates, key=lambda c: c["body_field_count"])
```

### 2.3 Stage 3 的三个职责

| # | 职责 | 判断依据 |
|---|------|----------|
| ① | 从候选中选取核心 API + 执行顺序 | `_select_core_api` + `operation_order` |
| ② | 找前置 API | 数据匹配（值/字段名） |
| ③ | 补充校验 API | HTTP method + 序列位置 |

---

## 3. 完整改动清单

### 3.1 `endpoint_classifier.py`（Stage 2 分类器）

#### `deduplicate_calls()` 改动

**改前**（L305-397）：
```python
core_api_map = {}  # {action: {"method": str, "pathname": str}}
# ... Layer 0-2 过滤 ...
# tiebreaker 只取一个
best = max(candidates, key=_request_body_field_count)
core_api_map[action] = {"method": best["method"], "pathname": best["pathname"]}

# 构建 classified（完整端点数据）
classified = {}
for action_name, ep_info in core_api_map.items():
    ep_data = {method, pathname, contexts, bodies, request_body_sample, query_params, ...}
    classified.setdefault(action_name, []).append(ep_data)

return {
    "classified": classified,
    "_core_api_map": core_api_map,
    "all_endpoints": [...],
    "response_samples": samples,
    "stats": {...},
}
```

**改后**：
```python
core_api_map = {}  # {action: [{candidate_with_full_data}, ...]}  ← 值改为数组

# ... Layer 0-2 过滤不变 ...

# 收集所有候选（不再 tiebreaker 只取一个），每个候选携带完整端点数据
if not candidates:
    first_api = non_static_calls[0]
    key = f"{first_api['method']} {first_api['pathname']}"
    ep = uniq.get(key, {})
    core_api_map[action] = [_build_candidate(first_api, ep, samples)]
else:
    core_api_map[action] = [
        _build_candidate(c, uniq.get(f"{c['method']} {c['pathname']}", {}), samples)
        for c in candidates
    ]

return {
    # 删除 "classified": classified
    "_core_api_map": core_api_map,
    "all_endpoints": [...],
    "response_samples": samples,
    "stats": {...},
}
```

新增辅助函数：
```python
def _build_candidate(call: dict, uniq_ep: dict, samples: dict) -> dict:
    """构建单个候选 API 的完整数据。"""
    return {
        "method": call["method"],
        "pathname": call["pathname"],
        "body_field_count": _request_body_field_count(call),
        "response_has_id": _response_has_entity_id(call["pathname"], samples),
        "contexts": sorted(uniq_ep.get("contexts", set())),
        "bodies": uniq_ep.get("bodies", []),
        "request_body_sample": uniq_ep["bodies"][0] if uniq_ep.get("bodies") else None,
        "query_params": uniq_ep["query_params_list"][0] if len(uniq_ep.get("query_params_list", [])) == 1 else {},
        "query_params_samples": uniq_ep.get("query_params_list", []),
    }
```

#### 删除项

| # | 删除内容 | 行号 |
|---|----------|------|
| DE1 | `classified = {}` 变量及构建逻辑 | L362-387 |
| DE2 | `"classified": classified` 返回值 | L390 |

### 3.2 `capture_apis.py`（Stage 2 入口）

| 行号 | 现状 | 改为 |
|------|------|------|
| L68 | `return {"error": "no_playbook", "classified": {}, "stats": {}}` | `return {"error": "no_playbook", "core_api_map": {}, "stats": {}}` |
| L75-77 | `core_api_map = result.get("core_api_map", {})` + `operation_count` | 不变（统计 key 数） |
| L204 | `return {"error": "no_operations", "classified": {}, "stats": {}}` | `return {"error": "no_operations", "core_api_map": {}, "stats": {}}` |
| L311-313 | `core_api_map = result.pop("_core_api_map", {})`<br>`result["core_api_map"] = core_api_map` | 不变（值格式变了，提取逻辑不变） |
| L322-325 | `for action, ep_info in core_api_map.items():`<br>`    core_api_pathnames.add(ep_info.get("pathname", ""))` | `for action, candidates in core_api_map.items():`<br>`    for c in candidates:`<br>`        core_api_pathnames.add(c.get("pathname", ""))` |

### 3.3 `analyze_flow.py`（Stage 3 分析器）— 最大改动

#### 3.3.1 新增辅助函数

```python
def _select_core_api(candidates, purpose="step"):
    """从候选中选取最合适的 API。（见 2.2 节）"""
    ...

def _find_last_write_op(core_apis):
    """从 core_apis 中找最后一个写操作的操作名。"""
    for action in reversed(list(core_apis.keys())):
        for ep in core_apis[action]:
            if ep.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
                return action
    return None
```

#### 3.3.2 `analyze()` 签名修改

```python
# 改前：
def analyze(classified_apis, all_endpoints, response_samples, ui_result,
            pre_api_candidates=None, profile=None,
            operation_order=None, core_api_map=None) -> dict:

# 改后：删除 classified_apis 参数
def analyze(core_api_map, all_endpoints, response_samples, ui_result,
            pre_api_candidates=None, profile=None,
            operation_order=None) -> dict:
```

内部调用变化：
```python
# 改前：
core_apis = _build_core_apis_from_core_api_map(core_api_map, classified_apis, infra_apis)

# 改后：core_api_map 已包含完整数据，不需要 classified_apis
core_apis = _build_core_apis_from_core_api_map(core_api_map, infra_apis)
```

#### 3.3.3 `_build_core_apis_from_core_api_map()` — 简化

```python
# 改前：从 classified_apis 查表获取完整数据
def _build_core_apis_from_core_api_map(core_api_map, classified_apis, infra_apis):
    ep_lookup = {}
    for cat, eps in classified_apis.items():
        for ep in eps:
            ep_lookup[(ep["method"], ep["pathname"])] = ep
    for action_name, ep_info in core_api_map.items():
        ep_data = ep_lookup.get((ep_info["method"], ep_info["pathname"]))
        ...

# 改后：core_api_map 数组中已有完整数据，直接取
def _build_core_apis_from_core_api_map(core_api_map, infra_apis):
    core_apis = {}
    for action_name, candidates in core_api_map.items():
        if action_name == "init":
            continue
        selected = _select_core_api(candidates, "step")
        if not selected:
            continue
        # selected 已包含 bodies, query_params 等完整数据
        core_apis.setdefault(action_name, []).append(selected)
    return core_apis
```

#### 3.3.4 `_derive_order()` — 已简化（当前已完成）

不变。

#### 3.3.5 `_derive_dependencies()` — 微调

当前已实现的版本已包含 `id_producer`。微调：传入 `core_api_map`（数组）用于 `_select_core_api`。

```python
def _derive_dependencies(core_apis, response_samples, operation_order, core_api_map):
    # ... 遍历 operation_order，用 _select_core_api(candidates, "id_extract") 选取 ...
```

#### 3.3.6 `_derive_state_rules()` — 已重写（需确认）

当前已完成重写，使用 `id_producer` 参数。确认 `values_by_action` 改名。

#### 3.3.7 `build_manifest()` — 消除所有 `"create"` / `"delete"` 硬编码

| 原行号 | 现状 | 改为 |
|--------|------|------|
| L1103 | `core_api_map = analysis.get("core_api_map", {})` | 不变 |
| L1104-1105 | `reclassified = analysis.get("reclassified_apis", {})`<br>`raw_classified = reclassified if reclassified else capture_result.get("by_category", {})` | **删除** — 不再需要 `by_category` 查表 |
| L1113-1116 | `if search_action in core_api_map:`<br>`    ep_info = core_api_map[search_action]`<br>`    full_ep = _find_endpoint_by_method_pathname(raw_classified, ...)` | `if search_action in core_api_map:`<br>`    candidates = core_api_map[search_action]`<br>`    full_ep = _select_core_api(candidates, "step")` |
| L1124-1127 | 同上，`"init"` | 同上 |
| L1138 | `_find_endpoint_by_method_pathname(raw_classified, ...)` | `full_ep = _select_core_api(candidates, "step")` |
| L1255-1257 | `if "create" in core_apis:`<br>`    create_body_sample = _parse_body(core_apis["create"][0])` | `if id_producer and id_producer in core_apis:`<br>`    create_body_sample = _parse_body(core_apis[id_producer][0])` |
| L1274 | `create_eps = core_apis.get("create", [])` | `create_eps = core_apis.get(id_producer, [])` |
| L1413-1423 | `key = f"create.{field_name}"` | `key = f"{id_producer}.{field_name}"` |
| L1438 | `if action == "create":` | `if action == id_producer:` |
| L1459 | `"requires": ["id"] if action != "create" else []` | `"requires": [] if action == id_producer else ["id"]` |
| L1563/1568/1570 | `if action == "delete":` / `elif action == "create":` | 用 `is_last_write_op` + `id_producer` |
| L1576 | `if action not in ("create", "delete"):` | `if action != id_producer and not is_last_write_op:` |

#### 3.3.8 `_find_endpoint_by_method_pathname()` — 删除

这个函数专门从 `classified`（`by_category`）查表，`classified` 删除后不再需要。

**被删除**（L560-579，~20 行）

#### 3.3.9 `trace_pre_api_dependencies()` — 移除类别名过滤

| 原行号 | 现状 | 改为 |
|--------|------|------|
| L1774-1776 | `if category not in ('create', 'update', 'delete'):`<br>`    continue` | 删除此过滤 |
| L1846 | `if param['step_action'] == 'create':` | `if param['step_action'] == id_producer:` |

#### 3.3.10 删除项

| # | 函数/代码块 | 行号 | 理由 |
|---|-------------|------|------|
| D1 | `_derive_temporal_order()` | L401-446 (~47 行) | 已被 `operation_order` 替代 |
| D2 | `_merge_orders()` | L449-471 (~23 行) | 不再有多个顺序源 |
| D3 | `_build_dependency_graph()` | L474-508 (~35 行) | 不再手动推导依赖图 |
| D4 | `_topological_sort()` | L511-544 (~34 行) | 同上 |
| D5 | `_STEP_LABELS = {}` | L820 | 空字典，dead code |
| D6 | `_VERIFY_ONLY` 过滤 | L322-324 | `operation_order` 已包含正确操作 |
| D7 | `_find_endpoint_by_method_pathname()` | L560-579 (~20 行) | `classified` 删除后不需要查表 |
| D8 | `_derive_state_rules` 中的硬编码块 | L717-732 (~15 行) | 用 `id_producer` / `last_write_op` 替代 |

**总计删除：~205 行**

### 3.4 `run.py`（运行入口）

| 行号 | 现状 | 改为 |
|------|------|------|
| L471 | `"by_category": api_capture.get("classified", {})` | **删除此行** |
| L487 | `for cat, eps in sorted(api_capture.get("classified", {}).items()):` | `for cat, candidates in sorted(api_capture.get("core_api_map", {}).items()):` |
| L488 | `LOG.info(f"    {cat}: {len(eps)} 个")` | `LOG.info(f"    {cat}: {len(candidates)} 个候选")` |
| L536 | `classified = capture_result.get("by_category", {})` | `core_api_map = capture_result.get("core_api_map", {})` |
| L545 | `flow = analyze(classified, all_endpoints, ...)` | `flow = analyze(core_api_map, all_endpoints, ..., core_api_map=core_api_map)` — 合并为一个参数 |
| L1044 | `stage2_valid = bool(capture_result and capture_result.get("by_category"))` | `stage2_valid = bool(capture_result and capture_result.get("core_api_map"))` |
| L1164 | 同上 | 同上 |

### 3.5 `stage_validators.py`（阶段验证器）

#### `validate_stage2()` — 适配数组结构

| 行号 | 现状 | 改为 |
|------|------|------|
| L137-140 | `core_api_map = capture_result.get("core_api_map", {})`<br>`if not core_api_map:` | 不变（dict 非空检查） |
| L142-155 | `for action, ep_info in core_api_map.items():`<br>`    if not isinstance(ep_info, dict):`<br>`        issues.append(...)` | `for action, candidates in core_api_map.items():`<br>`    if not isinstance(candidates, list) or not candidates:`<br>`        issues.append(f"core_api_map['{action}'] 格式错误：应为非空数组")`<br>`        continue`<br>`    for ep in candidates:`<br>`        if not isinstance(ep, dict):`<br>`            issues.append(f"core_api_map['{action}'] 候选格式错误")`<br>`            break`<br>`        if "method" not in ep or "pathname" not in ep:`<br>`            issues.append(f"core_api_map['{action}'] 候选缺少 method 或 pathname")`<br>`            break`<br>`    else:`<br>`        valid_endpoints += 1` |

#### `validate_stage3()` — 移除操作名硬编码

| 行号 | 现状 | 改为 |
|------|------|------|
| L218 | `any("create" in name.lower() for name in step_names)` | 检查 `crud_order` 非空即可 |
| L221 | `any("delete" in name.lower() for name in step_names)` | 同上 |

#### `validate_stage4()` — 移除操作名硬编码

| 行号 | 现状 | 改为 |
|------|------|------|
| L323 | `'"action": "create"' not in script_content` | `'"extract":' not in script_content` |
| L325 | `'"action": "delete"' not in script_content` | `'"not_contains_id"' not in script_content` |

### 3.6 `diagnostic_mode.py`（诊断模式）

| 行号 | 现状 | 改为 |
|------|------|------|
| L802 | `classified = capture_result.get("classified", {})` | `core_api_map = capture_result.get("core_api_map", {})` |
| L807 | `if category in classified and classified[category]:` | `if category in core_api_map and core_api_map[category]:` |

### 3.7 `lib/runtime/test_runtime.py`（运行时）

| 行号 | 现状 | 改为 |
|------|------|------|
| L250 | `if action == "create":` → `_extract_state()` | `if step_def.get("extract"):` → `_extract_state()` |
| L1025 | `s.get("action") == "create"` → find create_step | `s.get("extract")` → 找第一个有 extract 的步骤 |

### 3.8 `tests/test_module_discovery.py`（单元测试）

| 行号 | 现状 | 改为 |
|------|------|------|
| L44 | `"""基本去重功能：通过 replay_windows 确定 core_api_map"""` | 注释更新 |
| L214-230 | `"core_api_map": {"create": {"method": "POST", ...}}` | `"core_api_map": {"create": [{"method": "POST", ..., "body_field_count": 5, "response_has_id": true}]}` |
| L238 | `assert any("core_api_map" in i for i in issues)` | 不变 |

---

## 4. 修改后的数据流

```
Stage 2 输出 (core_api_map 数组 + 完整数据):
  "core_api_map": {
    "创建用户": [{
      method: POST, pathname: /users,
      body_field_count: 10, response_has_id: true,
      bodies: ["{userName:...}"], request_body_sample: "{...}",
      query_params: {}, query_params_samples: [],
      contexts: ["replay:创建用户"]
    }],
    "重置密码": [
      {method: GET, pathname: /password-policy, body_field_count: 0, response_has_id: false, ...},
      {method: PUT, pathname: /users/{id}/password, body_field_count: 3, response_has_id: false, ...}
    ],
    "删除": [{method: DELETE, pathname: /users/{id}, body_field_count: 2, response_has_id: true, ...}],
  }
  "operation_order": ["创建用户", "query", "编辑", "冻结", "重置密码", "删除"]
  # 不再输出 "by_category"

Stage 3 处理:
  ① _build_core_apis:
     → "创建用户": _select_core_api(1 候选, "step") → POST /users（含完整 bodies 等数据）
     → "重置密码": _select_core_api(2 候选, "step") → PUT /users/{id}/password（写操作优先）

  ② _derive_dependencies:
     → "创建用户": _select_core_api(candidates, "id_extract") → POST /users → entity.id → id_producer
     → "重置密码": PUT 无 response_has_id → 跳过

  ③ build_manifest:
     → "创建用户": is_id_producer → extract: {id: "entity.id"}, requires: []
     → "重置密码": 步骤用 PUT（_select_core_api 已选）
     → "删除": is_last_write_op → assertion: "not_contains_id"

Runtime 执行:
  → 找到 extract 的步骤 → "创建用户" → prepare_create_body + _extract_state
  → 后续步骤 requires: ["id"] → 正常执行
```

---

## 5. 边界情况分析

### 5.1 单候选操作（大多数情况）

`_select_core_api()` 直接返回唯一候选。行为与当前一致。

### 5.2 多候选操作

`_select_core_api(candidates, "step")` 选写操作中字段数最多的。

### 5.3 所有候选都无 `response_has_id`

`_select_core_api(candidates, "id_extract")` fallback 到 `"step"` 逻辑。若响应无 ID → `id_producer = None`，所有步骤 `requires: []`。

### 5.4 操作无候选（Layer 0-2 全过滤）

`core_api_map[action]` 取 `non_static_calls[0]` 作为兜底候选。行为与当前一致。

### 5.5 `operation_order` 为空

Fallback 到 `list(core_apis.keys())`。

### 5.6 `_get_create_pre_api_ref_source` 的 key 格式

改为 `f"{id_producer}.{field_name}"`。`trace_pre_api_dependencies` 使用 `param['step_action']`（操作名）作 key 前缀 → 一致。

### 5.7 `create_body` state key

`test_runtime.py` 中 `self.state["create_body"]` 由 `prepare_create_body()` 写入 → 通过 `extract` 检测触发 → 不需要改名。

### 5.8 `diagnostic_mode.py` 的 `_analyze_missing_apis`

改为从 `core_api_map` 查找类别。`core_api_map` 的 key 是操作名（中文），而 `expected_categories` 可能是英文名（如 `"create"`）。这不影响——该函数检查类别是否存在，如果类别名不匹配就报告缺失，行为合理。

---

## 6. 实施步骤

### Phase 1: `endpoint_classifier.py`
1. 新增 `_build_candidate()` 辅助函数
2. `deduplicate_calls()` 中 `core_api_map` 值改为数组 + 完整数据
3. **删除** `classified` 变量和构建逻辑
4. **删除** `"classified"` 返回值

### Phase 2: `capture_apis.py`
1. 错误返回改为 `"core_api_map": {}`
2. `core_api_pathnames` 构建适配数组遍历

### Phase 3: `analyze_flow.py`
1. `analyze()` 签名：删除 `classified_apis` 参数，`core_api_map` 成为唯一输入
2. 新增 `_select_core_api()` + `_find_last_write_op()`
3. 重写 `_build_core_apis_from_core_api_map()` — 从数组直接取数据
4. `_derive_dependencies()` 微调加入 `_select_core_api`
5. `_derive_state_rules()` 确认完成
6. `build_manifest()` 消除所有硬编码 + 删除 `raw_classified` / `_find_endpoint_by_method_pathname` 调用
7. `trace_pre_api_dependencies()` 移除类别名过滤
8. **删除** `_derive_temporal_order`, `_merge_orders`, `_build_dependency_graph`, `_topological_sort`, `_STEP_LABELS`, `_VERIFY_ONLY`, `_find_endpoint_by_method_pathname`

### Phase 4: `run.py`
1. 删除 `by_category` 行
2. `run_stage34()` 中用 `core_api_map` 替代 `classified`
3. `stage2_valid` 检查改为 `core_api_map`
4. 日志输出适配数组

### Phase 5: `stage_validators.py`
1. `validate_stage2` 适配数组结构
2. `validate_stage3` 移除操作名检查
3. `validate_stage4` 改为检查 `extract` / `not_contains_id`

### Phase 6: `diagnostic_mode.py`
1. `classified` → `core_api_map`

### Phase 7: `lib/runtime/test_runtime.py`
1. L250: `action == "create"` → `step_def.get("extract")`
2. L1025: `action == "create"` → `step_def.get("extract")`

### Phase 8: `tests/test_module_discovery.py`
1. 测试数据 `core_api_map` 格式改为数组
2. 断言逻辑更新

### Phase 9: 验证
1. 运行单元测试
2. 重跑 Stage 2 生成新格式数据
3. 运行 Stage 3+4 offline
4. 运行生成的脚本

---

## 7. 改动量统计

| 文件 | 新增 | 删除 | 重写 | 修改 |
|------|------|------|------|------|
| `endpoint_classifier.py` | ~20 行 (`_build_candidate`) | ~30 行 (`classified` 构建) | 0 | ~15 行 |
| `capture_apis.py` | 0 | 0 | 0 | ~8 行 |
| `analyze_flow.py` | ~50 行 (2 辅助函数) | ~205 行 (7 函数 + 代码块) | ~200 行 (6 函数) | ~40 行 |
| `run.py` | 0 | ~3 行 | 0 | ~10 行 |
| `stage_validators.py` | 0 | 0 | 0 | ~20 行 |
| `diagnostic_mode.py` | 0 | 0 | 0 | ~3 行 |
| `lib/runtime/test_runtime.py` | 0 | 0 | 0 | ~4 行 |
| `tests/test_module_discovery.py` | 0 | 0 | 0 | ~10 行 |
| **总计** | **~70 行** | **~238 行** | **~200 行** | **~110 行** |

净减少 ~120 行代码。

---

## 8. 旧代码彻底清理清单

本章节汇总所有需要删除或替换的旧代码，确保实施时不遗漏。

### 8.1 `endpoint_classifier.py` 清理项

| # | 位置 | 删除内容 | 原因 |
|---|------|----------|------|
| 1 | L362-387 | `classified = {}` 变量及其构建逻辑（整个 for 循环） | `classified` 字段不再需要，数据合并到 `core_api_map` 数组 |
| 2 | L390 | 返回值中的 `"classified": classified` 行 | 同上 |

### 8.2 `analyze_flow.py` 清理项

| # | 位置 | 删除内容 | 原因 |
|---|------|----------|------|
| 1 | L401-446 | 整个 `_derive_temporal_order()` 函数（~47 行） | 已被 `operation_order` 替代，不再需要时序推导 |
| 2 | L449-471 | 整个 `_merge_orders()` 函数（~23 行） | 不再有多个顺序源需要合并 |
| 3 | L474-508 | 整个 `_build_dependency_graph()` 函数（~35 行） | 不再手动推导依赖图，顺序由 `operation_order` 决定 |
| 4 | L511-544 | 整个 `_topological_sort()` 函数（~34 行） | 同上 |
| 5 | L560-579 | 整个 `_find_endpoint_by_method_pathname()` 函数（~20 行） | `classified` 删除后不再需要从 `by_category` 查表 |
| 6 | L717-732 | `_derive_state_rules()` 中的硬编码块：`if "create" in state_values` 等（~15 行） | 用 `id_producer` / `last_write_op` 替代 |
| 7 | L820 | `_STEP_LABELS = {}` 空字典定义 | Dead code，从未使用 |
| 8 | L322-324 | `_VERIFY_ONLY = {"query", "detail", "execute"}` 及 `available -= _VERIFY_ONLY` | `operation_order` 已包含正确操作，不需要过滤 |
| 9 | L1104-1105 | `reclassified = analysis.get("reclassified_apis", {})` 和 `raw_classified = ...` | 不再需要 `by_category` 查表 |
| 10 | L130-134 | `analyze()` 函数签名中的 `classified_apis` 参数 | 改为只接收 `core_api_map` |

### 8.3 `run.py` 清理项

| # | 位置 | 删除内容 | 原因 |
|---|------|----------|------|
| 1 | L471 | `"by_category": api_capture.get("classified", {})` 行 | `by_category` 字段不再输出 |

### 8.4 `capture_apis.py` 清理项

| # | 位置 | 删除/修改内容 | 原因 |
|---|------|---------------|------|
| 1 | L68 | `return {"error": "no_playbook", "classified": {}, "stats": {}}` → 改为 `"core_api_map": {}` | 统一错误返回格式 |
| 2 | L204 | `return {"error": "no_operations", "classified": {}, "stats": {}}` → 改为 `"core_api_map": {}` | 同上 |

### 8.5 全局搜索验证清单

实施完成后，需要全局搜索确认以下关键字已完全清除：

| 搜索关键字 | 预期结果 | 检查命令 |
|-----------|----------|----------|
| `"classified"` | 仅出现在注释或文档中 | `grep -r '"classified"' module_discovery/` |
| `"by_category"` | 仅出现在注释或文档中 | `grep -r '"by_category"' module_discovery/` |
| `action == "create"` | 应该为 0 匹配 | `grep -r 'action == "create"' module_discovery/ lib/runtime/` |
| `action == "delete"` | 应该为 0 匹配 | `grep -r 'action == "delete"' module_discovery/ lib/runtime/` |
| `in core_apis and core_apis["create"]` | 应该为 0 匹配 | `grep -r 'core_apis\["create"\]' module_discovery/` |
| `core_apis.get("create"` | 应该为 0 匹配 | `grep -r 'core_apis\.get("create"' module_discovery/` |
| `core_apis.get("delete"` | 应该为 0 匹配 | `grep -r 'core_apis\.get("delete"' module_discovery/` |
| `"create" in core_apis` | 应该为 0 匹配 | `grep -r '"create" in core_apis' module_discovery/` |
| `"delete" in core_apis` | 应该为 0 匹配 | `grep -r '"delete" in core_apis' module_discovery/` |
| `classified_apis` | 仅出现在 `analyze()` 旧签名注释中 | `grep -r 'classified_apis' module_discovery/` |
| `raw_classified` | 应该为 0 匹配 | `grep -r 'raw_classified' module_discovery/` |
| `reclassified` | 应该为 0 匹配 | `grep -r 'reclassified' module_discovery/` |

### 8.6 实施顺序建议

为避免遗漏，建议按以下顺序执行清理：

1. **Phase 1-2**: 修改 `endpoint_classifier.py` 和 `capture_apis.py`，删除 `classified` 相关代码
2. **Phase 3**: 修改 `analyze_flow.py`，删除所有旧函数和硬编码
3. **Phase 4-6**: 修改 `run.py`、`stage_validators.py`、`diagnostic_mode.py`
4. **Phase 7**: 修改 `test_runtime.py`
5. **Phase 8**: 更新测试用例
6. **验证**: 运行上述全局搜索命令，确认所有关键字已清除
7. **测试**: 运行单元测试和端到端测试
