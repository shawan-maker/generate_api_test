# Stage 3 逻辑分析缺陷修复方案

## 问题概述

用户管理模块四阶段全链路运行后，Stage 3 存在三个缺陷：

| # | 问题 | 现象 | 根因文件 |
|---|------|------|---------|
| 1 | lock 操作丢失 | Stage 1/2 都成功了 lock，但 manifest 中无 lock 步骤 | `endpoint_classifier.py` |
| 2 | 依赖注入错误 | `userId` 来源标记为 `reset` 而非 `create` | `analyze_flow.py` |
| 3 | 缺少查询验证步骤 | manifest 中无 contains_id/not_contains_id 验证 | `analyze_flow.py` |

---

## 问题 1：lock 被误分类为 update

### 1.1 现象

Stage 2 的 `api_by_button` 已正确分组：
```json
"lock": [{"pathname": "/users/{id}/suspend"}]
"update": [{"pathname": "/users/{id}"}]
```

但 Stage 3 的 `core_apis` 分类结果：
```json
"update": [
  {"pathname": "/users/{id}"},           // 正确的 update
  {"pathname": "/users/{id}/suspend"}    // ← lock 被错误归入 update
]
```

`core_apis` 中没有 `lock` 类别 → `_derive_order()` 无法排出 lock → `build_manifest()` 不生成 lock 步骤。

### 1.2 根因追踪

分类发生在 `endpoint_classifier.py:classify_endpoint()`，对 `PUT /users/{id}/suspend`：

| 步骤 | 规则 | 结果 |
|------|------|------|
| ① URL 查询/详情关键词 | `/list`, `/detail` 等 | ✗ 不匹配 |
| ② 校验类 API | `/check`, `/usability` | ✗ 不匹配 |
| ③ DELETE | `DELETE` 方法 | ✗ 是 PUT |
| ④ **按钮上下文** | `_crud_from_contexts(contexts)` | **✗ 失败（见下）** |
| ⑤ URL 写操作关键词 | `/freeze`, `/disable` 等 | ✗ URL 用 `/suspend` 不在列表中 |
| ⑥ RESTful 兜底 | `PUT → update` | ✓ **错误命中** |

**步骤④失败的根因**：

Stage 2 playbook 回放时，interceptor 记录的 context 格式为 `"replay:lock"`、`"replay:unlock"`。

`_crud_from_contexts()` 解析逻辑：
```python
parts = ctx.split(":")        # ["replay", "lock"]
action = parts[-1]            # "lock"（英文 action 名）
cat = _classify_by_text("lock")  # 在 ACTION_KEYWORDS 中查找
```

`ACTION_KEYWORDS` 的 key 是英文（`"lock"`），但**值是中文关键词列表**（`["锁定", "冻结", ...]`）。`_classify_by_text("lock")` 遍历所有 values 查找子串匹配，`"lock"` 不是任何中文关键词的子串 → 返回 `"unknown"`。

```python
# _classify_by_text 的实现
def _classify_by_text(text: str) -> str:
    for crud, keywords in const.ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:    # "锁定" in "lock" → False！
                return crud
    return "unknown"
```

### 1.3 修复方案

**文件**：`module_discovery/endpoint_classifier.py`  
**函数**：`_crud_from_contexts()` (line 39-57)

**修改内容**：增加对 `"replay:{action}"` 格式的直接匹配。

```python
def _crud_from_contexts(contexts: List[str]) -> str | None:
    priority = ["create", "delete", "update", "lock", "unlock", "reset",
                "authorize", "migrate"]
    best = None
    for ctx in contexts:
        parts = str(ctx).split(":")
        if len(parts) < 2:
            continue

        action = parts[-1].strip()

        # 新增：直接匹配 replay:{action} 格式（Stage 2 playbook 回放上下文）
        if parts[0] == "replay" and action in priority:
            if best is None or priority.index(action) < priority.index(best):
                best = action
                continue

        # 原有逻辑：中文按钮文本匹配
        cat = _classify_by_text(action)
        if cat in priority:
            if best is None or priority.index(cat) < priority.index(best):
                best = cat
    return best
```

**同时修改**：`classify_endpoint()` 步骤⑤的 URL 关键词列表增加 `/suspend`：

```python
# ⑤ URL 写操作关键词
for crud, kws in (
    ...
    ("lock", ("/lock", "/freeze", "/disable", "/suspend")),   # 新增 /suspend
    ("unlock", ("/unlock", "/enable", "/activate", "/resume")), # 新增 /resume
    ...
):
```

### 1.4 影响分析

**正向影响**：
- `PUT /users/{id}/suspend` 正确分类为 `lock`
- `core_apis` 中出现 `lock` 类别
- `_derive_order()` 根据 `_build_dependency_graph()` 的规则（`unlock 依赖 lock`）自动将 lock 排在 unlock 之前
- manifest 生成正确的 lock 步骤

**无副作用**：
- 新增的 `parts[0] == "replay"` 条件仅在 replay 上下文时生效
- 原有的中文按钮文本匹配逻辑完全保留
- `/suspend` 关键词只用于 lock 分类，不影响其他 API

---

## 问题 2：依赖注入来源错误

### 2.1 现象

`用户管理_analysis.json` 的依赖分析：
```json
"userId": {
    "source": "reset",           // ← 错误！应该是 create
    "source_path": "entity.userId"
}
```

manifest 中 `userId` 的注入源指向 reset 操作，但实际应该从 create 响应的 `entity.id` 获取。

### 2.2 根因追踪

`_derive_dependencies()` (line 381-453) 的 ID 提取逻辑：

```python
for category, endpoints in core_apis.items():   # 遍历所有类别
    for ep in endpoints:
        # 从响应中提取 ID 字段
        found_ids = _extract_ids_recursive(body, ...)
        for field_path, field_name, sample_value in found_ids:
            if field_name not in id_field_candidates:  # ← 先到先得！
                id_field_candidates[field_name] = {
                    "source_crud": category,
                    "path": field_path,
                }
```

**问题**：`if field_name not in id_field_candidates` 是"先到先得"策略。dict 遍历顺序不确定，如果 `reset` 类别先于 `create` 被遍历：

1. 遍历 `core_apis["reset"]` → `POST /password-reset/reset` 响应包含 `entity.userId`
   → `id_field_candidates["userId"] = {"source_crud": "reset", "path": "entity.userId"}`
2. 遍历 `core_apis["create"]` → `POST /users` 响应包含 `entity.id` 和 `entity.userId`
   → `"userId" not in id_field_candidates` → **False**（已被 reset 占用）→ 跳过

### 2.3 修复方案

**文件**：`module_discovery/analyze_flow.py`  
**函数**：`_derive_dependencies()` (line 381-453)

**修改策略**：ID 字段来源优先从 `create` 操作提取。

```python
def _derive_dependencies(core_apis: dict, response_samples: dict) -> dict:
    injections = {}
    id_field_candidates = {}

    # 修改：按优先级遍历 —— create 优先，确保 ID 来源正确
    # 第一轮：只处理 create（如果存在）
    # 第二轮：处理其他类别（补充 create 未覆盖的 ID 字段）
    ordered_categories = []
    if "create" in core_apis:
        ordered_categories.append("create")
    for cat in core_apis:
        if cat != "create":
            ordered_categories.append(cat)

    for category in ordered_categories:
        endpoints = core_apis[category]
        for ep in endpoints:
            pn = ep["pathname"]
            samples = response_samples.get(pn, [])
            for s in samples[:1]:
                # ...（响应解析逻辑不变）
                found_ids = _extract_ids_recursive(body, path="", max_depth=4)
                for field_path, field_name, sample_value in found_ids:
                    if field_name not in id_field_candidates:
                        id_field_candidates[field_name] = {
                            "source_crud": category,
                            "sample_value": sample_value,
                            "path": field_path,
                        }

    # 后续 injections 逻辑不变
    # ...
```

### 2.4 影响分析

**正向影响**：
- `userId` 来源从 `reset` 修正为 `create`
- manifest 中 `userId` 的 `source` 指向 `create.id`（即 `entity.id`）
- 后续所有依赖 `userId` 的操作（reset、update、lock、unlock）使用正确的 ID

**无副作用**：
- 只是调整了遍历顺序，不影响提取逻辑本身
- 如果 create 响应中没有某个 ID 字段，仍然会从其他操作中提取
- `id_field_details` 的字段内容不变，只是 `source_crud` 更准确

---

## 问题 3：缺少查询验证步骤

### 3.1 现象

对比角色管理（正确）和用户管理（缺失验证）：

| 角色管理 | 用户管理 |
|---------|---------|
| create | create |
| ~~查询验证（创建后）~~ | ~~（缺失）~~ |
| update | reset |
| ~~查询验证（修改后）~~ | execute |
| delete | update |
| ~~查询验证（删除后）~~ | unlock |
| | delete |
| | ~~（缺失所有验证）~~ |

`build_manifest()` 的 `_plan_verify_steps()` 函数有完整的验证规划逻辑，但因为 `verify_endpoints` 为空，没有生成任何验证步骤。

### 3.2 根因追踪

**`verify_endpoints` 为空的原因链**：

```
GET /tenants/users（用户列表查询）
    ↓ _filter_by_cooccurrence()
    ↓ 出现在 5/6 个按钮上下文中（83% > 60% 阈值）
    ↓ 被标记为"辅助 API"
    ↓
_filter_core_apis() 过滤掉
    ↓
core_apis 中没有用户列表查询
    ↓
build_manifest() 搜索 verify_endpoints 失败
    ↓
verify_endpoints = {}（空）
    ↓
_plan_verify_steps() 返回空列表
    ↓
无验证步骤
```

**详细分析**：

**第一步：同现过滤器误杀**

`GET /tenants/users` 在 Stage 2 回放过程中，每次操作后页面都会刷新用户列表，导致这个 API 出现在几乎所有按钮的上下文中：

```json
"contexts": ["init", "replay:create", "replay:delete", "replay:lock",
             "replay:reset", "replay:unlock", "replay:update"]
```

`_filter_by_cooccurrence()` 计算：6/7 = 86% > 60% 阈值 → 标记为辅助 API → 被 `_filter_core_apis()` 排除。

**第二步：分类不精确**

`GET /tenants/users` 的 URL 不包含 `/list`、`/page`、`/search` 等关键词，`classify_endpoint()` 将其分类为 `other_get`。

**第三步：回退搜索范围不够**

`build_manifest()` 的回退搜索只查找 `capture_result["by_category"]["query"]` 和 `["detail"]`，不查找 `["other_get"]`。而用户列表查询被分类在 `other_get` 中。

### 3.3 修复方案

需要修改两处：

#### 修改点 A：同现过滤器保护列表查询 API

**文件**：`module_discovery/analyze_flow.py`  
**函数**：`_filter_by_cooccurrence()` (line 21-61)

**策略**：如果 API 的响应是列表结构（含 `list` + `total`），即使同现频率高也不标记为辅助。

```python
def _filter_by_cooccurrence(all_endpoints: list, threshold: float = 0.6,
                            response_samples: dict = None) -> set:
    # ...（原有统计逻辑不变）

    supporting_apis = set()
    total_buttons = len(total_button_contexts)

    if total_buttons > 0:
        for pathname, contexts in api_contexts.items():
            ratio = len(contexts) / total_buttons
            if ratio >= threshold:
                # 新增：检查是否为列表查询 API（不应被过滤）
                if _is_list_query(pathname, response_samples):
                    LOG.debug(f"同现过滤保护: {pathname} 是列表查询，保留")
                    continue
                LOG.debug(f"同现过滤: {pathname} ({ratio:.2f})")
                supporting_apis.add(pathname)

    return supporting_apis


def _is_list_query(pathname: str, response_samples: dict) -> bool:
    """判断 API 是否为列表查询（响应含 list+total 结构）。"""
    if not response_samples:
        return False
    samples = response_samples.get(pathname, [])
    for s in samples[:1]:
        try:
            body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            entity = body.get("entity", body)
            if isinstance(entity, dict):
                # 同时有 list 键和 total 键 → 列表查询
                has_list = any(k in entity for k in const.LIST_KEY_CANDIDATES)
                has_total = any(k in entity for k in const.TOTAL_KEY_CANDIDATES)
                if has_list and has_total:
                    return True
        except Exception:
            pass
    return False
```

**调用处修改**（`analyze()` 函数，line 76-77）：

```python
# Step 0: 传入 response_samples 供列表查询保护使用
cooccurrence_support = _filter_by_cooccurrence(
    all_endpoints, threshold=0.6,
    response_samples=response_samples  # 新增参数
)
```

#### 修改点 B：扩大 verify endpoint 回退搜索范围

**文件**：`module_discovery/analyze_flow.py`  
**函数**：`build_manifest()` (line 1078-1108)

**策略**：如果 `query`/`detail` 分类中找不到验证端点，也搜索 `other_get`/`other_post`。

```python
verify_endpoints = {}
for verify_action in ["query", "detail"]:
    # ...（原有的 core_apis 搜索逻辑不变）

    if selected:
        continue

    # 回退 1：从 capture_result 的 query/detail 分类搜索（原有逻辑）
    raw_classified = capture_result.get("by_category", {})
    raw_eps = raw_classified.get(verify_action, [])
    for ep in raw_eps:
        # ...（原有过滤逻辑）

    # 新增回退 2：从 other_get/other_post 中搜索列表查询端点
    if verify_action not in verify_endpoints:
        for fallback_cat in ("other_get", "other_post"):
            fallback_eps = raw_classified.get(fallback_cat, [])
            for ep in fallback_eps:
                p = ep["pathname"].lower()
                if any(kw in p for kw in const.SUPPORTING_API_KEYWORDS):
                    continue
                if _endpoint_returns_entity_id(ep):
                    verify_endpoints[verify_action] = {
                        "ep": ep,
                        "body_sample": _parse_body(ep),
                    }
                    LOG.info(f"  验证端点回退2: {verify_action} ← "
                             f"{ep['pathname'][:60]} (from {fallback_cat})")
                    break
            if verify_action in verify_endpoints:
                break
```

### 3.4 影响分析

**正向影响**：
- `GET /tenants/users` 不再被同现过滤器误杀（响应含 list+total 结构）
- 即使被误杀，回退搜索也能从 `other_get` 中找到它
- `verify_endpoints["query"]` 被正确设置
- `_plan_verify_steps()` 在每个写操作后插入 `contains_id`/`not_contains_id` 验证步骤
- manifest 步骤变为：create → 验证 → reset → update → 验证 → lock → 验证 → unlock → 验证 → delete → 验证

**潜在副作用**：

| 风险 | 说明 | 缓解 |
|------|------|------|
| 列表查询保护误放 | 某些辅助 API 也有 list+total 结构 | 只保护响应含 list+total **且** entity 是 dict 的 API |
| other_get 回退噪声 | other_get 中可能有非列表 API | `_endpoint_returns_entity_id()` 二次过滤 |
| 验证端点不匹配 | 回退找到的端点可能返回不同实体的列表 | `_endpoint_returns_entity_id()` 检查 create ID 是否出现在响应中 |

---

## 三个修复的执行顺序

```
问题1 (lock 分类) → 确保 core_apis 包含 lock 类别
    ↓
问题2 (依赖注入) → 确保 userId 来源指向 create
    ↓
问题3 (验证步骤) → 确保每个写操作后有验证步骤
```

三个修复相互独立，可以按任意顺序实施。但建议按 1→2→3 顺序，因为：
- 问题 1 确保 lock 步骤出现在 manifest 中
- 问题 2 确保 lock 步骤使用正确的 ID
- 问题 3 确保 lock 步骤后有验证

---

## 修复后的预期 manifest steps

```
 1. create       — POST /users                    — 创建用户
 2. post(验证)   — POST /policies/list             — 查询验证（创建后）contains_id
 3. reset        — POST /password-reset/reset      — 重置密码
 4. update       — PUT /users/{id}                 — 编辑
 5. post(验证)   — POST /policies/list             — 查询验证（编辑后）contains_id
 6. lock         — PUT /users/{id}/suspend         — 冻结
 7. post(验证)   — POST /policies/list             — 查询验证（冻结后）contains_id
 8. unlock       — PUT /users/{id}/enable          — 启用
 9. post(验证)   — POST /policies/list             — 查询验证（启用后）contains_id
10. delete       — DELETE /users/batch/delete      — 批量删除
11. post(验证)   — POST /policies/list             — 查询验证（删除后）not_contains_id
```

注意：验证端点最终使用哪个 API 取决于 `_endpoint_returns_entity_id()` 的校验结果。如果 `POST /policies/list` 不含用户 ID，会回退到 `GET /tenants/users`。

---

## 关键文件清单

| 文件 | 修改函数 | 修改类型 |
|------|---------|---------|
| `endpoint_classifier.py` | `_crud_from_contexts()` | 增加 replay context 直接匹配 |
| `endpoint_classifier.py` | `classify_endpoint()` 步骤⑤ | 增加 `/suspend`、`/resume` 关键词 |
| `analyze_flow.py` | `_derive_dependencies()` | create 优先遍历 |
| `analyze_flow.py` | `_filter_by_cooccurrence()` | 列表查询保护 |
| `analyze_flow.py` | `analyze()` | 传递 response_samples 给同现过滤 |
| `analyze_flow.py` | `build_manifest()` | 扩大 verify endpoint 回退范围 |
