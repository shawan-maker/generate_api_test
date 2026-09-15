# Stage 2/3 架构重构方案：从 HTTP 方法分类到操作窗口驱动

## 1. 问题回顾

### 1.1 当前架构的核心问题

Stage 2 在捕获阶段做了一件多余且有害的事：**按 HTTP 方法将所有端点分类到固定类别**（query/create/update/delete）。

```python
# endpoint_classifier.py 当前逻辑
if method == "GET":
    return "query"  # 所有 GET 请求 → query 垃圾桶
```

**后果**：
- `query` 类别成为垃圾桶：12 个 GET 端点全部归入（主列表查询、配置加载、校验查询、心跳）
- Stage 3 无法区分"主列表查询"和"数据加载 API"
- 验证端点选择错误：字母排序后 `/display-unit-tree` 排在 `/tenants/users` 前面
- 核心 API 标记失效：`/tenants/users` 因高频出现被 Layer 1 过滤排除

### 1.2 根本原因

分类这个动作本身是多余的。Stage 1 已经知道了业务操作（创建、编辑、删除、搜索...），Stage 2 按操作顺序回放，每个操作产生一个时间窗口。**时间窗口本身就定义了"哪些 API 属于哪个操作"**，不需要再按 HTTP 方法重新分类。

## 2. 设计原则

### 2.1 核心思想

```
Stage 1: 发现业务操作（创建、编辑、删除、搜索...）

Stage 2: 按操作顺序回放，每个操作产生时间窗口
  → 输出：{操作名: 该窗口内的核心 API}
  → 不再按 HTTP 方法分类

Stage 3: 逐操作处理
  → 对每个操作：
    1. 直接使用 Stage 2 标记的核心 API
    2. 识别前置 API（多操作共享的数据加载）
    3. 识别验证 API（操作后的查询验证）
  → 按 Stage 2 操作顺序组织测试步骤

Stage 4: 组装测试脚本
```

### 2.2 关键设计决策

**决策 1：验证端点的来源**
- 优先：从"搜索"操作的核心 API 获取（如果 Stage 1 有搜索操作）
- 次选：从 `init` 窗口的核心 API 获取（如果模块没有搜索操作）

**决策 2：前置 API 识别**
- 不再依赖 `classified` 类别
- 直接按 HTTP 方法过滤：`method == "GET"` 且不在 `core_api_map` 中的端点 → pre-API 候选

**决策 3：完全移除 `classified`/`by_category`**
- Stage 2 输出 `core_api_map` 替代 `classified`
- Stage 3 不再需要 `_reclassify_all_endpoints()` 和 `_filter_core_apis()`
- 简化数据流，减少概念混乱

## 3. 数据流对比

### 3.1 当前架构

```
Stage 2 输出:
{
  "classified": {
    "query": [GET /users, GET /roles, GET /password-policy, ...],  ← 垃圾桶
    "create": [POST /users],
    "update": [PUT /users/{id}],
    "delete": [DELETE /users/{id}]
  },
  "operation_order": ["创建", "搜索", "编辑", ...],
  "all_calls": [...]
}

Stage 3 处理:
1. _reclassify_all_endpoints() → 重新分类（复杂逻辑）
2. _filter_core_apis() → 从每个类别选核心 API（query 类别豁免所有过滤）
3. 取 eps[0] → 字母排序选错（/display-unit-tree vs /tenants/users）
```

### 3.2 新架构

```
Stage 2 输出:
{
  "core_api_map": {
    "创建": {"method": "POST", "pathname": "/users"},
    "搜索": {"method": "GET", "pathname": "/tenants/users"},
    "编辑": {"method": "PUT", "pathname": "/users/{id}"},
    "删除": {"method": "DELETE", "pathname": "/users/{id}"},
    "冻结": {"method": "POST", "pathname": "/users/{id}/suspend"},
    "init": {"method": "GET", "pathname": "/roles"}  ← 初始加载窗口
  },
  "operation_order": ["创建", "搜索", "编辑", ...],
  "all_calls": [...]
}

Stage 3 处理:
1. 直接使用 core_api_map[action] → 无需再选
2. 验证端点：优先用 "搜索" 操作，次选用 "init" 操作
3. 前置 API：所有 GET 调用 - core_api_map 中的 GET → pre-API 候选
```

## 4. 场景分析

### 4.1 场景 1：有搜索操作的模块（用户管理）

```
Stage 1: [创建用户, 搜索, 编辑, 删除, 冻结, ...]

Stage 2:
  init 窗口: GET /tenants/users(核心) + GET /password-policy(配置)
  创建用户窗口: POST /users(核心)
  搜索窗口: GET /tenants/users?name=xxx(核心)  ← 有搜索参数，不被 Layer 1 过滤
  编辑窗口: PUT /users/{id}(核心)
  删除窗口: DELETE /users/{id}(核心)
  冻结窗口: POST /users/{id}/suspend(核心)

core_api_map:
  "创建用户": POST /users
  "搜索": GET /tenants/users  ← 验证端点来源（优先级 1）
  "编辑": PUT /users/{id}
  "删除": DELETE /users/{id}
  "冻结": POST /users/{id}/suspend
  "init": GET /tenants/users  ← 验证端点候选（优先级 2）

Stage 3:
  创建后验证 → 使用 "搜索" 操作的 GET /tenants/users
  编辑后验证 → 使用 "搜索" 操作的 GET /tenants/users
  删除后验证 → 使用 "搜索" 操作的 GET /tenants/users
```

### 4.2 场景 2：没有搜索操作的模块（角色管理）

```
Stage 1: [创建角色, 编辑, 删除]  ← 没有搜索

Stage 2:
  init 窗口: GET /roles(核心) + GET /permissions(配置)
  创建角色窗口: POST /roles(核心)
  编辑窗口: PUT /roles/{id}(核心)
  删除窗口: DELETE /roles/{id}(核心)

core_api_map:
  "创建角色": POST /roles
  "编辑": PUT /roles/{id}
  "删除": DELETE /roles/{id}
  "init": GET /roles  ← 验证端点来源（无搜索操作，只能用 init）

Stage 3:
  创建后验证 → 使用 "init" 操作的 GET /roles
  编辑后验证 → 使用 "init" 操作的 GET /roles
  删除后验证 → 使用 "init" 操作的 GET /roles
```

### 4.3 场景 3：状态变更操作（冻结/启用）

```
Stage 1: [创建, 冻结, 启用, 删除]

Stage 2:
  init 窗口: GET /users(核心)
  创建窗口: POST /users(核心)
  冻结窗口: POST /users/{id}/suspend(核心)
  启用窗口: POST /users/{id}/resume(核心)
  删除窗口: DELETE /users/{id}(核心)

core_api_map:
  "创建": POST /users
  "冻结": POST /users/{id}/suspend
  "启用": POST /users/{id}/resume
  "删除": DELETE /users/{id}
  "init": GET /users

Stage 3:
  创建后验证 → 使用 "init" 操作的 GET /users
  冻结后验证 → 使用 "init" 操作的 GET /users（检查 state 字段）
  启用后验证 → 使用 "init" 操作的 GET /users（检查 state 字段）
  删除后验证 → 使用 "init" 操作的 GET /users（检查 NOT_EXIST）
```

### 4.4 场景 4：操作失败未捕获 API

```
Stage 1: [创建, 编辑, 删除]

Stage 2:
  init 窗口: GET /users(核心)
  创建窗口: POST /users(核心)
  编辑窗口: [无 API 调用]  ← 操作失败
  删除窗口: DELETE /users/{id}(核心)

core_api_map:
  "创建": POST /users
  "删除": DELETE /users/{id}
  "init": GET /users
  ← "编辑" 不在 core_api_map 中

Stage 3:
  遍历 operation_order: ["创建", "编辑", "删除"]
  "创建" → 有核心 API → 生成步骤
  "编辑" → core_api_map 中无此操作 → 跳过
  "删除" → 有核心 API → 生成步骤
```

## 5. 详细实现方案

### 5.1 Stage 2 修改：`capture_apis.py`

#### 修改 1：`_capture_by_playbook()` 增加 `init` 窗口

```python
async def _capture_by_playbook(page, playbook, base_url, target_url, ...):
    # ... 现有逻辑 ...

    replay_windows = {}
    
    # ★ 新增：记录 init 窗口（导航到目标页面时的加载阶段）
    import time
    init_start = time.time()
    await page.goto(target_url, wait_until="networkidle", timeout=60000)
    await wait_for_table_ready(page, timeout=15000)
    init_end = time.time()
    replay_windows["init"] = {"start": init_start, "end": init_end}
    
    # 回放操作（现有逻辑）
    for action in operations:
        op_start = time.time()
        # ... 执行操作 ...
        op_end = time.time()
        replay_windows[action] = {"start": op_start, "end": op_end}
    
    # 收集拦截数据
    calls, samples, gates, sid, submit_marks = interceptor.collect()
    
    # 分类端点 — 传入 replay_windows
    result = classifier.deduplicate(calls, samples, replay_windows=replay_windows)
    
    # ★ 修改：保存 core_api_map（从 classifier 输出中获取）
    result["core_api_map"] = result.get("_core_api_map", {})
    result["operation_order"] = [op for op in replay_windows.keys() if op != "init"]
    
    return result
```

#### 修改 2：移除 `pre_api_candidates` 的业务路径排除逻辑

```python
# 当前逻辑（删除）
business_pathnames = set()
for cat, eps in result.get("classified", {}).items():
    if cat not in NON_BUSINESS_CATS:
        for ep in eps:
            business_pathnames.add(ep.get("pathname", ""))
# ... 复杂的排除逻辑 ...

# 新逻辑（简化）
core_api_pathnames = set()
for action, ep_info in result.get("core_api_map", {}).items():
    core_api_pathnames.add(ep_info.get("pathname", ""))

pre_api_candidates = _identify_pre_api_candidates(calls, samples, core_api_pathnames)
```

### 5.2 Stage 2 修改：`endpoint_classifier.py`

#### 修改 1：`deduplicate_calls()` 输出 `core_api_map`

```python
def deduplicate_calls(all_calls, samples, replay_windows=None):
    # ... 现有去重逻辑 ...
    
    # ★ 修改：构建 core_api_map（三层过滤）
    core_api_map = {}
    if replay_windows:
        pathname_window_count = _count_pathname_windows(all_calls, replay_windows)
        operation_count = len(replay_windows)
        frequency_threshold = max(2, int(operation_count * 0.6))
        
        for action, window in replay_windows.items():
            start, end = window["start"], window["end"]
            window_calls = sorted(
                [c for c in all_calls if start <= c.get("ts", 0) <= end],
                key=lambda c: c.get("ts", 0)
            )
            
            non_static_calls = [
                call for call in window_calls
                if not _is_static_or_heartbeat(call)
            ]
            
            if not non_static_calls:
                continue
            
            # Layer 1 + Layer 2 过滤（现有逻辑，含独有参数例外）
            candidates = []
            for call in non_static_calls:
                pathname = call.get("pathname", "")
                
                # Layer 1: 频率排除 + 独有参数例外
                if pathname_window_count.get(pathname, 0) > frequency_threshold:
                    if not _call_has_distinctive_params(call, all_calls):
                        continue
                
                # Layer 2: 响应特征排除
                if not _response_has_entity_id(pathname, samples):
                    continue
                
                candidates.append(call)
            
            # 选择核心 API
            if not candidates:
                first_api = non_static_calls[0]
                core_api_map[action] = {
                    "method": first_api["method"],
                    "pathname": first_api["pathname"]
                }
            else:
                best = max(candidates, key=_request_body_field_count)
                core_api_map[action] = {
                    "method": best["method"],
                    "pathname": best["pathname"]
                }
    
    # ★ 修改：返回 core_api_map，不再返回 classified
    return {
        "_core_api_map": core_api_map,  # 内部字段，由 capture_apis 提取
        "all_endpoints": [...],
        "response_samples": samples,
        "stats": {...}
    }
```

#### 修改 2：删除 `_classify_by_behavior()` 和分类逻辑

```python
# 删除以下函数和逻辑：
# - _classify_by_behavior()
# - classified.setdefault(cat, []).append(ep_data)
# - eps.sort(key=lambda ep: (0 if ep["_is_core_api"] else 1, ep["pathname"]))
```

### 5.3 Stage 3 修改：`analyze_flow.py`

#### 修改 1：`analyze()` 签名变更

```python
def analyze(core_api_map: dict, all_endpoints: list,
            response_samples: dict, ui_result: dict,
            capture_result: dict = None, operation_order: list = None):
    """
    :param core_api_map: {操作名: {"method": "POST", "pathname": "/users"}}
    :param all_endpoints: 所有去重端点列表
    :param response_samples: 响应样本字典
    :param ui_result: Stage 1 UI 探测结果
    :param capture_result: Stage 2 完整捕获结果
    :param operation_order: Stage 2 操作顺序（不含 init）
    """
```

#### 修改 2：删除 `_reclassify_all_endpoints()` 和 `_filter_core_apis()`

```python
# 删除这两个函数，不再需要重新分类和过滤
```

#### 修改 3：简化 `analyze()` 主逻辑

```python
def analyze(...):
    # Step 1: 直接使用 core_api_map（无需再选）
    # core_api_map 已经是 {操作名: 核心API}，直接使用
    
    # Step 2: 识别基础设施 API（现有逻辑）
    infra_apis = _identify_infrastructure_apis(all_endpoints, response_samples)
    
    # Step 3: 同现频率过滤（现有逻辑，用于标记辅助 API）
    cooccurrence_support = _filter_by_cooccurrence(all_endpoints, threshold=0.6)
    
    # Step 4: 推导执行顺序（优先用 operation_order）
    crud_order = _derive_order(core_api_map, all_endpoints, operation_order)
    
    # Step 5: 推导依赖关系（现有逻辑）
    dependencies = _derive_dependencies(core_api_map, all_endpoints, response_samples)
    
    # Step 6: 状态断言（现有逻辑）
    state_assertions = _discover_state_assertions(core_api_map, response_samples)
    
    # Step 7: 前置 API 依赖链追踪（现有逻辑）
    pre_api_chain = trace_pre_api_dependencies(
        core_api_map, all_endpoints, response_samples, capture_result
    )
    
    return {
        "crud_order": crud_order,
        "dependencies": dependencies,
        "state_assertions": state_assertions,
        "pre_api_chain": pre_api_chain,
        # 不再返回 reclassified_apis 和 core_apis
    }
```

#### 修改 4：`build_manifest()` 验证端点选择逻辑

```python
def build_manifest(analysis, capture_result, profile, module_name, target_url, ui_result=None):
    core_api_map = analysis.get("core_api_map", {})
    crud_order = analysis.get("crud_order", [])
    
    # ★ 新逻辑：选择验证端点
    verify_endpoint = None
    
    # 优先级 1：从 "搜索" 操作获取
    if "搜索" in core_api_map or "query" in core_api_map:
        search_action = "搜索" if "搜索" in core_api_map else "query"
        verify_endpoint = core_api_map[search_action]
        LOG.info(f"验证端点来源: 搜索操作 → {verify_endpoint['pathname']}")
    
    # 优先级 2：从 "init" 操作获取
    elif "init" in core_api_map:
        verify_endpoint = core_api_map["init"]
        LOG.info(f"验证端点来源: init 操作 → {verify_endpoint['pathname']}")
    
    # 优先级 3：从所有 GET 端点中找列表查询（兜底）
    else:
        for ep in all_endpoints:
            if ep["method"] == "GET":
                if _is_list_query(ep["pathname"], response_samples):
                    verify_endpoint = ep
                    LOG.info(f"验证端点来源: 列表查询端点 → {ep['pathname']}")
                    break
    
    # 构建步骤（现有逻辑，但验证端点来源改为 verify_endpoint）
    steps = []
    for action in crud_order:
        if action not in core_api_map:
            continue
        
        ep_info = core_api_map[action]
        ep = _find_endpoint_by_info(all_endpoints, ep_info)
        body_sample = _parse_body(ep)
        
        # 主步骤
        steps.append(_make_step(action, ep, body_sample))
        
        # 验证步骤（使用 verify_endpoint）
        if verify_endpoint:
            for verify_action, label, assertion in _plan_verify_steps(action):
                verify = _make_verify_step(verify_action, label, assertion, verify_endpoint)
                if verify:
                    steps.append(verify)
    
    # ... 后续逻辑不变 ...
```

#### 修改 5：`_identify_pre_api_candidates()` 简化

```python
def _identify_pre_api_candidates(calls, samples, core_api_pathnames):
    """
    识别前置 API 候选。
    
    逻辑：所有 GET 调用 - core_api_map 中的 GET → pre-API 候选
    """
    candidates = []
    seen_pathnames = set()
    
    for call in calls:
        pathname = call.get("pathname", "")
        method = call.get("method", "")
        
        # 只处理 GET 请求
        if method != "GET":
            continue
        
        # 排除核心 API
        if pathname in core_api_pathnames:
            continue
        
        # 去重
        if pathname in seen_pathnames:
            continue
        
        # ... 现有逻辑：提取字段、生成 API ID ...
        
        candidates.append({
            "name": api_name,
            "id": api_id,
            "method": method,
            "pathname": pathname,
            "response_sample": {"response_body": body_text},
            "extracted_fields": extracted_fields,
            "depends_on": []
        })
        
        seen_pathnames.add(pathname)
    
    return candidates
```

### 5.4 Stage 3 修改：`run.py`

#### 修改：传递 `core_api_map` 替代 `classified`

```python
def run_stage34(project_dir, module_name, profile, target_url,
                capture_result, ui_result):
    # 从 capture_result 读取 core_api_map
    core_api_map = capture_result.get("core_api_map", {})
    operation_order = capture_result.get("operation_order", [])
    
    # 调用 analyze()
    analysis = analyze_flow.analyze(
        core_api_map,  # ← 新参数
        capture_result.get("all_endpoints", []),
        capture_result.get("response_samples", {}),
        ui_result,
        capture_result=capture_result,
        operation_order=operation_order
    )
    
    # ... 后续逻辑不变 ...
```

## 6. 涉及文件清单

| 文件 | 修改类型 | 修改内容 |
|------|----------|----------|
| `capture_apis.py` | 修改 | 增加 init 窗口、输出 core_api_map、简化 pre-API 候选识别 |
| `endpoint_classifier.py` | 重构 | 删除分类逻辑、输出 core_api_map、删除 classified |
| `analyze_flow.py` | 重构 | 删除 reclassify/filter 函数、简化 analyze()、修改验证端点选择逻辑 |
| `run.py` | 修改 | 传递 core_api_map 替代 classified |
| `tests/test_module_discovery.py` | 修改 | 更新测试用例，移除 classified 相关测试 |

## 7. 验证方案

### 7.1 单元测试

```python
# 测试 1: core_api_map 构建
def test_core_api_map_construction():
    """测试三层过滤正确构建 core_api_map"""
    calls = [...]
    samples = {...}
    windows = {
        "init": {"start": 0, "end": 5},
        "创建": {"start": 10, "end": 15},
        "搜索": {"start": 20, "end": 25},
    }
    result = deduplicate_calls(calls, samples, replay_windows=windows)
    core_api_map = result["_core_api_map"]
    
    assert core_api_map["init"]["pathname"] == "/roles"
    assert core_api_map["创建"]["pathname"] == "/users"
    assert core_api_map["搜索"]["pathname"] == "/tenants/users"

# 测试 2: 验证端点选择优先级
def test_verify_endpoint_priority():
    """测试验证端点选择：搜索 > init > 列表查询"""
    core_api_map = {
        "创建": {"method": "POST", "pathname": "/users"},
        "搜索": {"method": "GET", "pathname": "/tenants/users"},
        "init": {"method": "GET", "pathname": "/roles"}
    }
    verify_ep = select_verify_endpoint(core_api_map, all_endpoints)
    assert verify_ep["pathname"] == "/tenants/users"  # 搜索优先
```

### 7.2 集成测试

```bash
# 测试 1: 有搜索操作的模块（用户管理）
python -m module_discovery.run --project ecm-compute \
  --url "/estack/web/estack/user-center/user-manage/user" \
  --module "用户管理" --stage all --export --headless

# 验证点：
# - Stage 2 输出 core_api_map 包含 "init" 和 "搜索"
# - Stage 3 验证端点使用 "搜索" 操作的 /tenants/users
# - Stage 4 脚本执行成功

# 测试 2: 无搜索操作的模块（角色管理）
python -m module_discovery.run --project ecm-compute \
  --url "/estack/web/estack/user-center/user-manage/role" \
  --module "角色管理" --stage all --export --headless

# 验证点：
# - Stage 2 输出 core_api_map 包含 "init"
# - Stage 3 验证端点使用 "init" 操作的 /roles
# - Stage 4 脚本执行成功
```

### 7.3 回归验证

```bash
# 运行所有现有测试
pytest tests/test_module_discovery.py -v

# 验证点：
# - 移除 classified 相关测试后，新测试全部通过
# - 不影响 pre-API 机制、同现过滤、响应约定发现
```

## 8. 实施步骤

### Phase 1: Stage 2 输出 core_api_map（1-2 小时）

1. `endpoint_classifier.py`: 修改 `deduplicate_calls()` 输出 `_core_api_map`
2. `capture_apis.py`: 增加 init 窗口、提取 `core_api_map` 到输出
3. 单元测试：验证 core_api_map 构建正确

### Phase 2: Stage 3 使用 core_api_map（2-3 小时）

1. `analyze_flow.py`: 修改 `analyze()` 签名、删除 reclassify/filter 函数
2. `analyze_flow.py`: 修改 `build_manifest()` 验证端点选择逻辑
3. `analyze_flow.py`: 简化 `_identify_pre_api_candidates()`
4. 单元测试：验证 analyze() 输出正确

### Phase 3: 集成测试（1-2 小时）

1. 运行用户管理模块端到端测试
2. 运行角色管理模块端到端测试
3. 修复发现的问题

### Phase 4: 清理和文档（1 小时）

1. 删除 `classified` 相关代码和测试
2. 更新 PROJECT_GUIDE.md 数据流图
3. 更新 four-stage-improvement.md 架构说明

## 9. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| init 窗口捕获不完整 | 中 | 验证端点缺失 | 兜底逻辑：从所有 GET 端点中找列表查询 |
| core_api_map 构建错误 | 低 | 核心 API 选错 | 三层过滤已验证，独有参数例外已测试 |
| 旧 capture 数据不兼容 | 高 | 无法加载 | 检查 `core_api_map` 是否存在，不存在则回退到旧逻辑 |
| 改动范围大引入 bug | 中 | 功能回归 | 分 Phase 实施，每个 Phase 独立测试 |

## 10. 向后兼容

### 旧 capture 数据处理

```python
def analyze(core_api_map=None, classified=None, ...):
    # 兼容旧数据：如果没有 core_api_map，从 classified 构建
    if core_api_map is None and classified is not None:
        core_api_map = _build_core_api_map_from_classified(classified)
        LOG.warning("使用旧版 classified 数据构建 core_api_map")
```

### 渐进式迁移

- Phase 1: Stage 2 同时输出 `core_api_map` 和 `classified`
- Phase 2: Stage 3 优先使用 `core_api_map`，`classified` 作为兜底
- Phase 3: 完全移除 `classified`

## 11. 总结

### 核心改进

1. **消除分类混乱**：不再按 HTTP 方法分类，直接按操作窗口组织
2. **简化数据流**：Stage 2 输出 core_api_map，Stage 3 直接使用
3. **验证端点明确**：优先用搜索操作，次选用 init 操作
4. **前置 API 识别简化**：直接按 HTTP 方法过滤，不依赖分类

### 预期收益

- 代码复杂度降低 40%（删除 reclassify/filter 函数）
- 验证端点选择准确率提升（从字母排序改为语义优先级）
- 数据流更清晰（减少 2 层中间转换）
- 测试覆盖率提升（针对 core_api_map 构建和验证端点选择）

### 实施周期

- Phase 1-2: 3-5 小时
- Phase 3-4: 2-3 小时
- 总计: 5-8 小时
