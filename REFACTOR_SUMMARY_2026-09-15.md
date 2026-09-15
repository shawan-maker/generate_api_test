# Stage 2-4 数据驱动重构 - 完成总结

**日期**: 2026-09-15  
**状态**: ✅ 已完成  
**影响范围**: Stage 2-4 API 发现管道

---

## 概述

成功将 Stage 2-4 管道从**硬编码中文操作名**迁移到**数据驱动动态查找**架构，消除了对特定操作名（如 `"create"`, `"delete"`）的依赖。

---

## 核心变更

### 1. 数据结构演进

#### 旧设计（已废弃）
```python
# Stage 2 输出
{
    "classified": {
        "create": [{"method": "POST", "pathname": "/users", ...}],
        "query": [{"method": "GET", "pathname": "/users", ...}],
        "delete": [{"method": "DELETE", "pathname": "/users/{id}", ...}]
    },
    "core_api_map": {
        "create": {"method": "POST", "pathname": "/users"},
        "delete": {"method": "DELETE", "pathname": "/users/{id}"}
    }
}
```

**问题**:
- 硬编码中文操作名 `"create"`, `"delete"`, `"query"`
- 两个独立结构（`classified` + `core_api_map`）存储重复信息
- Stage 3 必须识别特定操作名才能提取 ID

#### 新设计（已实现）
```python
# Stage 2 输出
{
    "core_api_map": {
        "创建用户": [
            {
                "method": "POST",
                "pathname": "/users",
                "contexts": ["init"],
                "bodies": [...],
                "response_has_id": true,
                ...
            }
        ],
        "查询用户": [...],
        "编辑用户": [...],
        "删除用户": [...]
    },
    "operation_order": ["init", "创建用户", "查询用户", "编辑用户", "删除用户"]
}
```

**优势**:
- ✅ 使用 UI 发现的操作名（中文），无需硬编码映射
- ✅ 单一数据源（`core_api_map`），消除冗余
- ✅ 候选数组支持多 API 场景（如重置密码的 GET+PUT）
- ✅ Stage 3 动态识别 ID 生产者和最终写入操作

---

### 2. 文件级变更清单

#### Phase 1: `module_discovery/endpoint_classifier.py`
- ✅ 移除 `classified` 返回字段
- ✅ 返回公共 `core_api_map`（数组格式）
- ✅ 添加 `_build_candidate()` 构建完整候选数据
- ✅ 按 `body_field_count` 降序排列候选

#### Phase 2: `module_discovery/capture_apis.py`
- ✅ 更新错误返回为 `"core_api_map": {}`
- ✅ 移除 `_core_api_map` 转换逻辑（已直接使用公共字段）

#### Phase 3: `module_discovery/analyze_flow.py` (核心重构)
- ✅ `analyze()` 签名：移除 `classified` 参数
- ✅ 添加 `_select_core_api()` 动态选择器
- ✅ 添加 `_find_last_write_op()` 查找最终写入操作
- ✅ 重写 `_build_core_apis_from_core_api_map()` 适配数组格式
- ✅ 删除 `_STEP_LABELS` 硬编码映射
- ✅ 删除 `_find_endpoint_by_method_pathname()` 辅助函数
- ✅ 更新 `build_manifest()` 使用 `id_producer` 和 `last_write_op`

#### Phase 4: `module_discovery/run.py`
- ✅ 移除 `by_category` 字段
- ✅ 日志输出改用 `core_api_map`
- ✅ `analyze()` 调用改用 `core_api_map` 参数
- ✅ 更新 Stage 2 验证检查点

#### Phase 5: `module_discovery/stage_validators.py`
- ✅ `validate_stage2()` 验证数组格式
- ✅ `validate_stage3()` 移除硬编码操作名检查
- ✅ `validate_stage4()` 检查 `"extract":` 和 `"not_contains_id"` 替代硬编码

#### Phase 6: `module_discovery/diagnostic_mode.py`
- ✅ 所有 `classified` 引用改为 `core_api_map`

#### Phase 7: `lib/runtime/test_runtime.py`
- ✅ 第 250 行：`if step_def.get("extract"):` 替代 `if action == "create":`
- ✅ 第 1025 行：`s.get("extract")` 替代 `s.get("action") == "create"`

#### Phase 8: `tests/test_module_discovery.py`
- ✅ 所有测试数据从 `classified` 迁移到 `core_api_map`
- ✅ 所有测试数据从字典格式改为数组格式
- ✅ 变量名更新：`create_eps` → `create_candidates`, `query_eps` → `query_candidates`

---

### 3. 关键技术实现

#### 3.1 动态 ID 生产者识别

```python
# analyze_flow.py - _build_manifest()
id_producer = next(
    (op for op in operation_order 
     if core_api_map.get(op, [{}])[0].get("response_has_id")),
    None
)

# 在步骤定义中
if op == id_producer:
    step_def["extract"] = f"{op}_id"
    step_def["verify"] = [{"contains_id": f"{op}_id"}]
```

**效果**: 自动识别第一个返回 ID 的操作，无需硬编码 `"create"`。

#### 3.2 动态最终写入操作识别

```python
# analyze_flow.py - _find_last_write_op()
def _find_last_write_op(operation_order, core_api_map):
    for op in reversed(operation_order):
        candidates = core_api_map.get(op, [])
        if candidates:
            method = candidates[0].get("method", "").upper()
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                return op
    return None

# 在步骤定义中
if op == last_write_op:
    step_def["verify"] = [{"not_contains_id": id_producer_var}]
```

**效果**: 自动识别最后一个写入操作（通常是删除），无需硬编码 `"delete"`。

#### 3.3 候选数组排序

```python
# endpoint_classifier.py - deduplicate_calls()
candidates.sort(key=_request_body_field_count, reverse=True)
core_api_map[action] = [
    _build_candidate(c, ...) for c in candidates
]
```

**效果**: 最佳候选（字段最多）排在首位，支持多 API 场景的智能选择。

---

## 验证结果

### 单元测试
```
tests/test_module_discovery.py ............... 65 passed in 0.16s
```

### 全局搜索验证
- ✅ `classified` 字段引用：0 处
- ✅ `by_category` 字段引用：0 处
- ✅ `action == "create"` 硬编码：0 处（核心模块）
- ✅ `action == "delete"` 硬编码：0 处（核心模块）
- ✅ `step_def.get("action")` 硬编码：0 处
- ✅ `_core_api_map` 内部字段：0 处

---

## 架构收益

### 1. 语言无关性
- **旧**: 必须使用英文操作名 `"create"`, `"delete"`
- **新**: 支持任意语言的操作名（中文、日文、法文等）

### 2. 多 API 支持
- **旧**: 每个操作只能映射一个 API
- **新**: 支持多候选 API（如重置密码的 GET+PUT）

### 3. 智能选择
- **旧**: 硬编码逻辑选择 API
- **新**: 根据目的（id_extract/step/query/verify）动态选择最佳候选

### 4. 数据驱动验证
- **旧**: 验证器检查特定操作名
- **新**: 验证器检查数据结构（`extract` 字段、`not_contains_id` 规则）

### 5. 可维护性
- **旧**: 添加新操作需修改多处硬编码
- **新**: 添加新操作只需在 `operation_order` 中追加

---

## 兼容性说明

### 向后兼容
- ✅ Stage 2 输出格式变更，但 Stage 3-4 已同步适配
- ✅ Playbook 格式无变化（仍使用中文操作名）
- ✅ Manifest 格式增强（添加 `extract` 字段）

### 破坏性变更
- ⚠️ 旧版 Stage 2 输出（含 `classified` 字段）不再支持
- ⚠️ 需要重新运行 Stage 2 生成新格式输出

---

## 后续优化建议

1. **多候选 API 场景测试**
   - 测试重置密码（GET+PUT）等多 API 操作
   - 验证 `_select_core_api()` 在不同目的下的选择逻辑

2. **错误处理增强**
   - 当 `id_producer` 未找到时提供明确错误信息
   - 当 `last_write_op` 为空时的降级策略

3. **性能优化**
   - 候选数组排序可缓存（避免重复计算）
   - `_select_core_api()` 可添加记忆化

4. **文档更新**
   - 更新 `docs/architecture/stage_pipeline.md` 反映新数据结构
   - 添加数据驱动架构设计文档

---

## 总结

本次重构成功将 Stage 2-4 管道从**硬编码驱动**迁移到**数据驱动**架构，实现了：

- ✅ 消除所有硬编码中文操作名
- ✅ 支持任意语言的操作名
- ✅ 支持多候选 API 场景
- ✅ 动态识别 ID 生产者和最终写入操作
- ✅ 65 个单元测试全部通过

代码质量显著提升，为后续多语言支持和复杂场景扩展奠定基础。
