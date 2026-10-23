# 多 Tab Switch 操作修复总结

## 问题背景

黑白名单模块包含两个 Tab（黑名单/白名单），每个 Tab 都有 switch:状态操作。在 API 测试生成过程中发现了以下问题：

## 发现的问题

### Fix 5: Switch query_params 丢失
**现象**: switch:状态 API 调用失败，报错 "Required request parameter 'bwlType' for method parameter type BwlType is not present"

**根因**: 
- Stage 2 捕获数据中，`requests[].query_params` 包含 `{"bwlType": "BLACK"}`
- 但 manifest 生成时使用 `ep.get("query_params", {})` 获取的是 ep 级别的空对象 `{}`
- 导致生成的测试脚本中 query_params 为空

**修复方案**:
```python
# manifest_builder.py: _make_step 函数
# 优先使用 per-request 级别的 query_params
per_request_qp = _get_query_params_for_action(ep, action)
step_query_params = per_request_qp if per_request_qp else ep.get("query_params", {})
```

### Fix 6: 多 Tab 验证步骤字段分类错误
**现象**: 
1. 白名单验证步骤使用 `type=BLACK`（应该用 `WHITE`）
2. switch:状态 操作后生成了验证步骤，尝试搜索不存在的 ID

**根因**:
1. `_make_verify_step` 中的 `body_sample` 来自验证端点的全局 body，未区分 Tab
2. 字段分类器将 tab-discriminator 字段（如 `type`）分类为 `context`，引用全局 `create_body.type`
3. `_plan_verify_steps` 为 switch 操作规划了验证步骤，但 switch 只是切换配置，不创建/修改特定记录

**修复方案**:

1. **提取 tab-discriminator 归一化函数**:
```python
def _normalize_tab_discriminator_fields(field_roles, body_sample, 
                                       action_create_body_sample,
                                       is_switch=False):
    """将 tab-discriminator 字段从 context 改为 static"""
    for field_name, role_info in list(field_roles.items()):
        if role_info.get("role") != "context":
            continue
        
        actual_value = body_sample.get(field_name)
        if actual_value is None:
            continue
        
        should_normalize = False
        
        if is_switch:
            # switch: 所有 context 字段都不可用
            should_normalize = True
        elif action_create_body_sample:
            # 检查是否与当前 Tab 的 create body 值匹配
            create_value = action_create_body_sample.get(field_name)
            if create_value is not None and create_value == actual_value:
                should_normalize = True
        
        if should_normalize:
            field_roles[field_name] = {"role": "static"}
```

2. **精确匹配验证步骤的 body**:
```python
# _make_verify_step 中
precise_body = _get_body_for_action(ep, action) if action else None
if precise_body is not None:
    body_sample = precise_body
```

3. **修复 tab_create_bodies 填充**:
```python
# 为 create-like 操作填充 tab_create_bodies
precise_body = _get_body_for_action(ep, action)
action_create_body = precise_body if precise_body is not None else body_sample

tab_prefix = _get_tab_prefix(action)
if tab_prefix:
    tab_create_bodies[tab_prefix] = action_create_body
```

4. **跳过 switch 操作的验证步骤**:
```python
def _plan_verify_steps(action):
    # Switch 操作不规划验证步骤
    if _is_switch_action(action):
        return []
    # ... 其他验证规划逻辑
```

## 修改文件

- `core/discovery/stage3/manifest_builder.py`
  - `_make_step`: 使用 per-request query_params
  - `_make_verify_step`: 精确匹配 body + tab-discriminator 归一化
  - `_plan_verify_steps`: 跳过 switch 操作
  - 主循环: 正确填充 tab_create_bodies

## 验证结果

- 单元测试: 658/658 通过 ✓
- 黑白名单 API 测试: 22/22 通过 ✓
  - 黑名单: switch:状态, 新增, 搜索验证, 编辑, 暂停, 启用, 移除 (11 步骤)
  - 白名单: switch:状态, 新增, 搜索验证, 编辑, 暂停, 启用, 移除 (11 步骤)

## 泛化设计

修复采用泛化设计，不针对黑白名单模块硬编码：
1. `_normalize_tab_discriminator_fields` 适用于所有多 Tab 模块
2. `_get_body_for_action` 通过 context 精确匹配，适用于所有需要区分 Tab 的场景
3. `_is_switch_action` 通过 action name 模式匹配，适用于所有 switch 操作
4. 单 Tab 模块不受影响（`tab_prefix` 为 None，不触发多 Tab 逻辑）
