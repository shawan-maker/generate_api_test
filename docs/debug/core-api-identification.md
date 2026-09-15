# Stage 2/3 核心 API 识别方案

## 1. 问题描述

### 1.1 当前现象

"创建用户"操作触发了 9 个 API（按时间顺序）：

```
时间窗口 replay:create 内的 API 调用：

1. GET  /current-user              ← 保活消息
2. GET  /tenants/display-by-role   ← 加载部门树（辅助）
3. GET  /tenants/users             ← 刷新列表
4. POST /policies/list             ← 加载策略（辅助）
5. GET  /password-policy           ← 加载密码策略（辅助）
6. POST /users/check               ← 验证用户名（辅助）
7. GET  /users/phone/usability     ← 验证手机号（辅助）
8. GET  /users/email/usability     ← 验证邮箱（辅助）
9. POST /users                     ← 真正创建（核心）✓
```

### 1.2 当前问题

`endpoint_classifier.py:131` 取"时间窗口内第一个非基础设施 API"：

```python
first_api = business_apis[0]
core_api_map[action] = (first_api["method"], first_api["pathname"])
```

`_is_static_or_heartbeat()` 只过滤了 #1（路径匹配 `/current-user`），#2-#9 都通过了。
结果：核心 API 被错误识别为 #2（GET /tenants/display-by-role），而不是 #9（POST /users）。

### 1.3 根因

当前过滤只有一层（静态资源 + 心跳路径匹配），无法识别：
- **辅助数据加载 API**：GET /tenants/display-by-role（为表单下拉框加载选项）
- **校验类 API**：POST /users/check（验证用户名是否可用）
- **列表刷新 API**：GET /tenants/users（操作完成后刷新列表）

这些 API 不是核心业务操作，但在时间窗口内先于核心 API 触发。

## 2. 设计原则

> Stage 2 阶段按照时间戳的方式来捕获，在 90% 的情况下是没有问题的，这个应该是主原则。
> 少数情况下，出现保活消息或者周期性消息，想办法识别，这个大方向不要变。

核心思路：**排除例外后取第一个**（时间戳优先不变，只在取"第一个"之前排除明确的非核心 API）。

## 3. 非核心 API 分类

| 类型 | 特征 | 示例 |
|------|------|------|
| **周期性消息** | 同一 API 在多个操作中反复触发 | GET /current-user（每个操作都触发） |
| **校验类 API** | 响应仅含 `{success: true}`，无实体数据 | POST /users/check, GET /phone/usability |
| **数据加载 API** | 为 UI 表单加载选项/列表 | GET /tenants/display-by-role, POST /policies/list |

### 3.1 辅助 API 的捕获不受影响

核心 API 识别只影响"哪个 API 被标记为核心"。辅助 API 的捕获由 **pre-API 机制** 负责：

```
核心 API 识别（本方案）：找主操作端点 → 影响分类和排序
辅助 API 捕获（pre-API 机制）：找依赖数据源 → 影响请求参数动态填充
```

`_identify_pre_api_candidates()` 收集所有非核心端点作为候选，`trace_pre_api_dependencies()` 通过数据反向追踪确定哪些是真正的前置依赖。两者独立运作，互不影响。

## 4. 三层过滤方案

### 4.1 第一层：频率过滤（排除周期性消息）

**原理**：周期性消息在每个操作的时间窗口内都会出现。统计同一 API 在**所有窗口**中的出现次数，超过阈值的判定为周期性消息。

```
统计方法：
  遍历 replay_windows 中每个窗口，统计每个 pathname 出现在多少个不同窗口中
  如果 window_count(pathname) > operation_count × 0.6 → 周期性消息

示例（6 个操作）：
  GET /current-user: 出现在 6/6 个窗口 → 周期性，排除 ✓
  GET /tenants/display-by-role: 出现在 1/6 个窗口 → 非周期性，保留
  POST /users: 出现在 1/6 个窗口 → 非周期性，保留
```

**注意**：这里统计的是"出现在多少个不同窗口中"，不是"总调用次数"。因为同一个 API 在一个窗口内可能被调用多次（如轮询），但只算一个窗口。

### 4.2 第二层：响应特征过滤（排除校验类 API）

**原理**：校验类 API 的响应仅含成功标志，不包含业务实体数据。

```
判断方法：
  解析响应 JSON → 提取实体（尝试信封键 entity/data/result/...）
  如果实体是 dict 且包含 id 字段 → 包含业务数据，保留
  否则 → 无业务数据，排除

示例：
  POST /users/check → {"success": true} → entity=None → 排除 ✓
  GET /phone/usability → {"success": true, "entity": {"available": true}}
    → entity 是 dict 但无 id → 排除 ✓
  POST /users → {"success": true, "entity": {"id": "abc123", "name": "..."}}
    → entity 有 id → 保留 ✓
```

### 4.3 第三层：时间戳优先（取第一个）

经过前两层过滤后，剩下的第一个 API 就是核心 API。

```
示例（create 窗口，过滤后）：
  #2 GET  /tenants/display-by-role  ← 如果 Layer 2 通过（响应含 id），它仍是第一个
  #9 POST /users                    ← 核心 API

  问题：如果 #2 也通过了 Layer 2，取第一个仍然是 #2，不是 #9
```

### 4.4 ⚠️ 已知局限：数据加载 API 可能通过第二层

**问题**：某些数据加载 API 的响应也包含实体 id：

```
GET /tenants/display-by-role 响应：
  {"entity": {"id": "tenant-001", "name": "...", "children": [...]}}
  → entity 是 dict，有 id → Layer 2 通过 → 取第一个 = GET /tenants/display-by-role ✗
```

**这不是校验类 API，而是数据加载 API**。Layer 2 设计目标是排除校验类（响应无业务数据），对数据加载 API 可能无效。

### 4.5 补充层：多候选 tiebreaker（当 Layer 1+2 不够时）

如果同一窗口内有多个 API 通过了 Layer 1+2，使用 tiebreaker 策略：

```
tiebreaker 规则：
  1. 计算每个候选的请求体字段数（POST/PUT/PATCH 有 body，GET 无 body = 0 字段）
  2. 请求体字段数最多的 = 核心 API（核心操作发送完整实体，辅助 POST 只发送少量校验字段）

示例：
  GET /tenants/display-by-role → 请求体字段数 = 0（GET 无 body）
  POST /users → 请求体字段数 = 12（完整用户对象）
  → POST /users 胜出 ✓

适用场景：
  - 项目全用 POST：POST /check（2 字段） vs POST /create（12 字段）→ 取字段多的 ✓
  - 标准 RESTful：GET /options（0 字段） vs POST /users（12 字段）→ 取字段多的 ✓

兜底：
  - 如果 tiebreaker 也无法区分（请求体字段数相同），取第一个（时间戳优先）
```

## 5. 完整算法

```python
def identify_core_api_for_window(action, window_calls, all_calls, 
                                   replay_windows, response_samples):
    """
    三层过滤 + tiebreaker 识别核心 API
    """
    
    # ── 预计算：每个 pathname 出现在多少个不同窗口中 ──
    pathname_window_count = defaultdict(int)
    for act, window in replay_windows.items():
        seen_in_window = set()
        for call in all_calls:
            if window["start"] <= call.get("ts", 0) <= window["end"]:
                pn = call["pathname"]
                if pn not in seen_in_window:
                    pathname_window_count[pn] += 1
                    seen_in_window.add(pn)
    
    operation_count = len(replay_windows)
    frequency_threshold = max(2, int(operation_count * 0.6))
    
    # ── 过滤当前窗口内的 API ──
    candidates = []
    for call in window_calls:
        pathname = call["pathname"]
        
        # Layer 0: 排除静态资源和心跳（已有逻辑）
        if _is_static_or_heartbeat(call):
            continue
        
        # Layer 1: 排除周期性消息
        if pathname_window_count.get(pathname, 0) > frequency_threshold:
            continue
        
        # Layer 2: 排除响应无业务数据的 API
        if not _response_has_entity_id(pathname, response_samples):
            continue
        
        candidates.append(call)
    
    # ── 从候选中选择 ──
    if not candidates:
        # 兜底：所有都被过滤了，取第一个非静态的
        return next((c for c in window_calls if not _is_static_or_heartbeat(c)), None)
    
    if len(candidates) == 1:
        return candidates[0]
    
    # ── tiebreaker：请求体字段数最多的胜出 ──
    def body_field_count(call):
        bodies = call.get("bodies", [])
        if not bodies:
            return 0
        try:
            body = json.loads(bodies[0]) if isinstance(bodies[0], str) else bodies[0]
            return len(body) if isinstance(body, dict) else 0
        except:
            return 0
    
    candidates.sort(key=body_field_count, reverse=True)
    return candidates[0]
```

## 6. Stage 3 顺序一致性

### 6.1 当前问题

Stage 3 的 `_derive_order()` 会重新推断执行顺序：

```
Stage 1: playbook 定义操作顺序 → [create, query, update, delete, lock, unlock]
Stage 2: 按 playbook 顺序回放 → replay_windows 保持此顺序
Stage 3: _derive_order() 重新推断 → 可能打乱顺序 ✗
```

`_derive_temporal_order()` 使用 endpoint 在 `all_endpoints` 中的索引位置推断时序，
但 `all_endpoints` 的顺序取决于 `deduplicate_calls()` 中 dict 的插入顺序，
不保证与 playbook 操作顺序一致。

### 6.2 修复方案

**Stage 2 保存操作顺序**：在 `_capture_by_playbook()` 中，`replay_windows` 的 key 顺序就是 playbook 操作顺序。将其显式保存为 `operation_order`。

**Stage 3 读取操作顺序**：`analyze()` 接受 `operation_order` 参数，`_derive_order()` 直接返回该顺序，不再重新推断。

### 6.3 数据流

```
Stage 2 (capture_apis.py):
  operation_order = list(replay_windows.keys())  # [create, query, update, delete, ...]
  → 保存到 capture_result.json

Stage 3 (analyze_flow.py):
  operation_order = capture_result.get("operation_order", [])
  → _derive_order() 直接返回 operation_order（过滤掉不存在的类别）
```

## 7. 影响范围分析

### 7.1 修改影响

| 修改点 | 影响 | 风险 |
|--------|------|------|
| `deduplicate_calls()` 三层过滤 | core_api_map 更准确 → 分类排序正确 | 低（兜底逻辑保持原行为） |
| 保存 `operation_order` | Stage 2 输出新增字段 | 无（向后兼容，旧数据无此字段） |
| `_derive_order()` 直接读取 | Stage 3 不再重新排序 | 低（顺序与 Stage 1/2 一致） |

### 7.2 不会引入的问题

| 关注点 | 分析 |
|--------|------|
| pre-API 机制是否受影响 | ❌ 不受影响。pre-API 候选收集独立于 core_api_map |
| 同现过滤是否受影响 | ❌ 不受影响。同现过滤在 Stage 3 独立运行 |
| 分类逻辑是否受影响 | ❌ 不受影响。`_classify_by_behavior()` 基于 HTTP method，不依赖 core_api_map |
| 响应约定发现是否受影响 | ❌ 不受影响。信封键/ID字段发现遍历所有响应样本 |

### 7.3 潜在风险

| 风险 | 概率 | 缓解措施 |
|------|------|----------|
| tiebreaker 误判（辅助 POST 字段多于核心 POST） | 极低 | 辅助 POST 通常 1-3 个字段，核心 POST 通常 5+ 个字段 |
| Layer 2 误杀（核心 API 响应无 entity id） | 低 | 兜底逻辑：全部被过滤时取第一个非静态 API |
| 旧 capture 数据无 operation_order | 中 | `_derive_order()` 回退到当前的时序推断逻辑 |
| frequency_threshold 不合理 | 低 | 默认 60% 操作数，最小值 2，可根据实际调整 |

## 8. 实施方案

### Step 1: 修改 `endpoint_classifier.py`

1. `deduplicate_calls()` 签名新增 `response_samples` 参数（当前已通过 `samples` 传入）
2. 新增 `_count_pathname_windows()` — 统计 pathname 在各窗口的出现次数
3. 新增 `_response_has_entity_id()` — 检查响应是否包含实体 id
4. 新增 `_request_body_field_count()` — 计算请求体字段数（tiebreaker）
5. 修改 core_api_map 构建逻辑：三层过滤 + tiebreaker

### Step 2: 修改 `capture_apis.py`

1. `_capture_by_playbook()` 保存 `operation_order = list(replay_windows.keys())`
2. 将 `operation_order` 添加到 `result` dict

### Step 3: 修改 `analyze_flow.py`

1. `analyze()` 新增 `operation_order` 参数
2. `_derive_order()` 优先使用 `operation_order`，不存在时回退到时序推断

### Step 4: 修改 `run.py`

1. `run_stage34()` 从 capture_result 中读取 `operation_order` 传给 `analyze()`

### Step 5: 修改 `const.py`

1. 如需新增常量（如频率阈值），在此定义

## 9. 验证方案

### 9.1 单元测试

```python
# 测试三层过滤
def test_three_layer_filter():
    # 模拟 create 窗口的 9 个 API 调用
    window_calls = [
        {"method": "GET", "pathname": "/current-user", "ts": 1.0},
        {"method": "GET", "pathname": "/tenants/display-by-role", "ts": 1.1},
        {"method": "GET", "pathname": "/tenants/users", "ts": 1.2},
        {"method": "POST", "pathname": "/policies/list", "ts": 1.3},
        {"method": "GET", "pathname": "/password-policy", "ts": 1.4},
        {"method": "POST", "pathname": "/users/check", "ts": 1.5},
        {"method": "GET", "pathname": "/users/phone/usability", "ts": 1.6},
        {"method": "GET", "pathname": "/users/email/usability", "ts": 1.7},
        {"method": "POST", "pathname": "/users", "ts": 1.8, "bodies": ['{"name":"test",...}']},
    ]
    
    # 模拟响应
    response_samples = {
        "/current-user": [{"body": '{"entity":{"id":"u1"}}'}],
        "/tenants/display-by-role": [{"body": '{"entity":{"id":"t1","name":"..."}}'}],
        "/tenants/users": [{"body": '{"entity":{"list":[...],"total":10}}'}],
        "/policies/list": [{"body": '{"entity":{"list":[...],"total":5}}'}],
        "/password-policy": [{"body": '{"entity":{"minLength":8}}'}],
        "/users/check": [{"body": '{"success":true}'}],
        "/users/phone/usability": [{"body": '{"success":true,"entity":{"available":true}}'}],
        "/users/email/usability": [{"body": '{"success":true,"entity":{"available":true}}'}],
        "/users": [{"body": '{"entity":{"id":"new-user-id","name":"test"}}'}],
    }
    
    # 预期结果：POST /users
    result = identify_core_api_for_window("create", window_calls, ...)
    assert result["pathname"] == "/users"
```

### 9.2 集成测试

```bash
# 完整流水线测试
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python -m module_discovery.run \
  --project ecm-compute \
  --url "/estack/web/estack/user-center/user-manage/user" \
  --module "用户管理" \
  --stage all --export --headless

# 验证点：
# 1. Stage 2 输出包含 operation_order 字段
# 2. by_category.create[0].pathname == "/users"（核心 API 正确）
# 3. Stage 3 crud_order == operation_order（顺序一致）
# 4. Stage 4 脚本执行成功
# 5. Stage 5 导出正常
```

### 9.3 回归验证

- 确认 pre_api_candidates 数量不变（pre-API 机制不受影响）
- 确认 query 类别非空（GET /tenants/users 仍被正确分类为 query）
- 确认旧 capture 数据（无 operation_order 字段）能正常加载
