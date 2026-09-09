# Stage 5: 前置 API 依赖链追踪与导出功能设计文档

**文档版本**: 1.0  
**创建日期**: 2026-09-08  
**状态**: 已批准，待实施

---

## 1. 背景与目标

### 1.1 问题陈述

当前四阶段发现管道存在以下问题：

1. **单级依赖追踪**：仅追踪 `create → id → 后续步骤`，无法处理多级依赖链
2. **静态值误判**：`policyIds` 等字段被标记为 `static`，但实际应从前置 API 动态获取
3. **页面加载 API 被过滤**：`/current-user`、`/policies` 等前置 API 被当作"支撑 API"过滤掉
4. **无导出能力**：生成的 Python 脚本无法导入外部测试平台

### 1.2 用户需求

- 业务 API 需要哪些参数 → 这些参数从哪些 API 获取 → 递归追溯直到鉴权
- 前置 API 逻辑集成到 Stage 2-4，不仅是 Stage 5 导出
- Stage 5 导出 Postman Collection v2.1.0、helpers.py、Excel 参数文件

### 1.3 设计目标

1. **递归依赖追踪**：自动识别前置 API 并追踪参数来源
2. **向后兼容**：现有脚本和 manifest 继续工作
3. **性能可控**：页面加载捕获额外耗时 < 3 秒
4. **导出标准化**：Postman Collection v2.1.0、Excel 参数文件、helpers.py

---

## 2. 整体架构

### 2.1 数据流设计

```
Stage 2: capture_all()
  ↓ 新增：页面加载 API 捕获
  → {classified, all_endpoints, response_samples, pre_api_candidates}

Stage 3: analyze()
  ↓ 新增：trace_pre_api_dependencies()
  → {..., pre_api_chain}
  ↓ build_manifest()
  → manifest dict with pre_apis[] section

Stage 4: generate_script(manifest)
  ↓ 运行时：TestRunner.execute_pre_apis()
  → 按依赖顺序执行前置 API，提取值到 state

Stage 5: export_artifacts()
  → Postman Collection v2.1.0 JSON
  → helpers.py (高层函数)
  → 参数 Excel (.xlsx)
```

### 2.2 模块职责

| 模块 | 职责 |
|------|------|
| `capture_apis.py` | 捕获页面加载 API，生成 `pre_api_candidates` |
| `analyze_flow.py` | 递归追踪依赖链，生成 `pre_apis[]` |
| `test_runtime.py` | 执行前置 API，提取值到 `state` |
| `export_artifacts.py` | 导出 Postman/Excel/helpers |

---

## 3. Manifest Schema 增强

### 3.1 新增 `pre_apis` 数组

```json
{
  "manifest_version": "1.1",
  "module": { ... },
  "response_contract": { ... },
  "auth_profile": { ... },
  
  "pre_apis": [
    {
      "name": "获取当前用户信息",
      "id": "current_user",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/users/current-user",
      "depends_on": [],
      "extracts": [
        {
          "name": "tenant_id",
          "path": "entity.tenantId",
          "used_by": ["*"]
        },
        {
          "name": "admin_id",
          "path": "entity.id",
          "used_by": ["create", "update", "lock", "unlock"]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "获取策略列表",
      "id": "policy_list",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/policies",
      "depends_on": [],
      "extracts": [
        {
          "name": "policy_id",
          "path": "entity.list[0].id",
          "used_by": ["create", "update"]
        }
      ]
    }
  ],
  
  "steps": [ ... ],
  "state_assertions": { ... }
}
```

### 3.2 新增 `pre_api_ref` 角色

```json
"body_field_roles": {
  "policyIds": {
    "role": "pre_api_ref",
    "source": "policy_list.policy_id",
    "is_array": true
  },
  "tenantId": {
    "role": "pre_api_ref",
    "source": "current_user.tenant_id"
  }
}
```

**角色说明**：
- `role: "pre_api_ref"` - 值来自前置 API 的提取结果
- `source: "<pre_api_id>.<extract_name>"` - 引用前置 API 的 ID 和提取字段名
- `is_array: true` - 将提取的值包装为数组（用于 `policyIds` 等字段）

---

## 4. 阶段实现详情

### 4.1 Phase A: Stage 2 页面加载 API 捕获

#### 4.1.1 改动位置

**文件**：`module_discovery/capture_apis.py`  
**函数**：`_capture_by_playbook()`  
**插入点**：interceptor 安装后、页面导航前

#### 4.1.2 新增函数

```python
def _identify_pre_api_candidates(calls: list, samples: dict) -> list:
    """
    识别页面加载期间的前置 API 候选
    
    Args:
        calls: RequestInterceptor 捕获的 API 调用列表
        samples: 响应样本字典
    
    Returns:
        前置 API 候选列表，每项包含：
        - name: API 名称
        - id: API ID（用于依赖引用）
        - method: HTTP 方法
        - pathname: URL 路径
        - response_sample: 响应样本
        - extracted_fields: 可提取的字段列表
    """
    candidates = []
    
    # 过滤规则
    SUPPORTING_PATTERNS = [
        r'/current[-_]?user',
        r'/policies',
        r'/roles',
        r'/departments',
        r'/users$',
        r'/tenants$',
    ]
    
    for call in calls:
        pathname = call.get('pathname', '')
        
        # 检查是否匹配支撑 API 模式
        if not any(re.search(p, pathname) for p in SUPPORTING_PATTERNS):
            continue
        
        # 只处理 GET 请求
        if call.get('method') != 'GET':
            continue
        
        # 获取响应样本
        sample = samples.get(pathname)
        if not sample or not sample.get('response_body'):
            continue
        
        # 解析响应，提取可提取字段
        try:
            body = json.loads(sample['response_body'])
            extracted_fields = _extract_available_fields(body)
        except:
            continue
        
        # 生成 API ID（使用路径最后一段）
        api_id = pathname.rstrip('/').split('/')[-1].replace('-', '_')
        
        candidates.append({
            'name': _generate_api_name(pathname),
            'id': api_id,
            'method': 'GET',
            'pathname': pathname,
            'response_sample': sample,
            'extracted_fields': extracted_fields,
            'depends_on': []
        })
    
    return candidates


def _extract_available_fields(body: dict, prefix: str = '') -> list:
    """
    递归提取 JSON 响应中的可提取字段
    
    Args:
        body: JSON 响应体
        prefix: 路径前缀（用于递归）
    
    Returns:
        字段列表，每项包含 name 和 path
        例如：[
            {'name': 'tenant_id', 'path': 'entity.tenantId'},
            {'name': 'list_0_id', 'path': 'entity.list[0].id'}
        ]
    """
    fields = []
    
    def _walk(obj, current_path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key
                
                # 提取基本类型字段
                if isinstance(value, (str, int, float, bool)):
                    field_name = new_path.replace('.', '_').replace('[', '_').replace(']', '')
                    fields.append({
                        'name': field_name,
                        'path': new_path,
                        'type': type(value).__name__
                    })
                
                # 递归处理嵌套对象
                _walk(value, new_path)
        
        elif isinstance(obj, list):
            # 提取列表第一个元素的字段
            if len(obj) > 0:
                _walk(obj[0], f"{current_path}[0]")
    
    _walk(body, prefix)
    return fields


def _generate_api_name(pathname: str) -> str:
    """根据路径生成人类可读的 API 名称"""
    mapping = {
        'current-user': '获取当前用户信息',
        'policies': '获取策略列表',
        'roles': '获取角色列表',
        'departments': '获取部门列表',
        'users': '获取用户列表',
        'tenants': '获取租户信息',
    }
    
    last_segment = pathname.rstrip('/').split('/')[-1]
    return mapping.get(last_segment, f'前置 API: {last_segment}')
```

#### 4.1.3 改动 `_capture_by_playbook()`

```python
async def _capture_by_playbook(page, project_dir, module_name, target_url, config):
    # ... 现有代码 ...
    
    # 安装临时 interceptor 捕获页面加载 API
    page_load_interceptor = RequestInterceptor(page)
    await page_load_interceptor.install()
    
    # 导航到目标页面（触发页面加载 API）
    await page.goto(target_url)
    await page.wait_for_load_state('networkidle')
    
    # 收集页面加载 API
    page_load_calls = await page_load_interceptor.collect()
    await page_load_interceptor.uninstall()
    
    # 识别前置 API 候选
    pre_api_candidates = _identify_pre_api_candidates(
        page_load_calls,
        page_load_interceptor.response_samples
    )
    
    LOG.info(f"[Phase A] 识别到 {len(pre_api_candidates)} 个前置 API 候选")
    
    # 继续现有的 playbook 回放逻辑
    # ... 现有代码 ...
    
    # 在返回结果中包含 pre_api_candidates
    return {
        'classified': classified,
        'all_endpoints': all_endpoints,
        'response_samples': response_samples,
        'pre_api_candidates': pre_api_candidates  # 新增
    }
```

---

### 4.2 Phase B: Stage 3 依赖链追踪算法

#### 4.2.1 新增函数

**文件**：`module_discovery/analyze_flow.py`

```python
def trace_pre_api_dependencies(
    steps: list,
    pre_api_candidates: list,
    context_fields: dict
) -> dict:
    """
    递归追踪参数依赖链，生成前置 API 列表
    
    Args:
        steps: 业务步骤列表
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        context_fields: 上下文字段配置
    
    Returns:
        {
            'pre_apis': [...],
            'field_resolutions': {...}
        }
    """
    
    # Phase 1: 构建参数清单
    param_inventory = []
    for step in steps:
        if step['action'] in ['create', 'update']:
            body_template = step.get('body_template', {})
            body_field_roles = step.get('body_field_roles', {})
            
            for field_name, field_value in body_template.items():
                role = body_field_roles.get(field_name, {}).get('role', 'static')
                
                # 只追踪 static 和 context 角色
                if role in ['static', 'context']:
                    param_inventory.append({
                        'step_action': step['action'],
                        'field_name': field_name,
                        'field_value': field_value,
                        'current_role': role
                    })
    
    # Phase 2: 构建前置 API 索引
    pre_api_index = {}
    for api in pre_api_candidates:
        for field in api['extracted_fields']:
            pre_api_index[field['path']] = {
                'api': api,
                'field': field
            }
    
    # Phase 3: 三策略匹配
    field_resolutions = {}
    used_apis = {}
    
    for param in param_inventory:
        resolution = _resolve_parameter_source(
            param,
            pre_api_index,
            context_fields
        )
        
        if resolution:
            key = (param['step_action'], param['field_name'])
            field_resolutions[key] = resolution
            
            # 记录使用的前置 API
            api_id = resolution['source'].split('.')[0]
            used_apis[api_id] = resolution['api']
    
    # Phase 4: 拓扑排序
    pre_apis = _topological_sort_pre_apis(used_apis)
    
    return {
        'pre_apis': pre_apis,
        'field_resolutions': field_resolutions
    }


def _resolve_parameter_source(
    param: dict,
    pre_api_index: dict,
    context_fields: dict
) -> dict | None:
    """
    使用三策略匹配解析参数来源
    
    策略 1: 精确值匹配
    策略 2: 字段名启发式
    策略 3: 列表成员检查
    """
    field_name = param['field_name']
    field_value = param['field_value']
    
    # 策略 1: 精确值匹配
    for path, info in pre_api_index.items():
        try:
            extracted_value = _extract_by_path(
                info['api']['response_sample']['response_body'],
                path
            )
            
            if str(extracted_value) == str(field_value):
                return {
                    'source': f"{info['api']['id']}.{info['field']['name']}",
                    'api': info['api'],
                    'strategy': 'exact_value_match'
                }
        except:
            continue
    
    # 策略 2: 字段名启发式
    # 例如：tenantId → 查找包含 tenantId 的前置 API
    for path, info in pre_api_index.items():
        if field_name.lower() in path.lower():
            return {
                'source': f"{info['api']['id']}.{info['field']['name']}",
                'api': info['api'],
                'strategy': 'field_name_heuristic'
            }
    
    # 策略 3: 列表成员检查
    # 检查 field_value 是否在某个列表响应中
    for path, info in pre_api_index.items():
        if '[0]' in path:
            # 这是一个列表字段
            list_path = path.split('[0]')[0]
            try:
                list_value = _extract_by_path(
                    info['api']['response_sample']['response_body'],
                    list_path
                )
                
                if isinstance(list_value, list):
                    # 检查 field_value 是否在列表中
                    for item in list_value:
                        if isinstance(item, dict):
                            item_id = item.get('id')
                            if str(item_id) == str(field_value):
                                return {
                                    'source': f"{info['api']['id']}.{info['field']['name']}",
                                    'api': info['api'],
                                    'strategy': 'list_membership',
                                    'is_array': True
                                }
            except:
                continue
    
    return None


def _topological_sort_pre_apis(used_apis: dict) -> list:
    """
    对前置 API 进行拓扑排序
    
    Args:
        used_apis: {api_id: api_info} 字典
    
    Returns:
        排序后的前置 API 列表
    """
    # 构建依赖图
    graph = {}
    for api_id, api_info in used_apis.items():
        graph[api_id] = api_info.get('depends_on', [])
    
    # 拓扑排序（Kahn 算法）
    in_degree = {node: 0 for node in graph}
    for node in graph:
        for dep in graph[node]:
            if dep in in_degree:
                in_degree[dep] += 1
    
    queue = [node for node in in_degree if in_degree[node] == 0]
    sorted_apis = []
    
    while queue:
        node = queue.pop(0)
        sorted_apis.append(used_apis[node])
        
        for dep in graph.get(node, []):
            if dep in in_degree:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)
    
    return sorted_apis
```

#### 4.2.2 改动 `build_manifest()`

```python
def build_manifest(
    classified_apis,
    response_samples,
    context_fields,
    pre_api_candidates  # 新增参数
):
    # ... 现有代码生成 steps ...
    
    # 新增：追踪前置 API 依赖
    dep_trace_result = trace_pre_api_dependencies(
        steps,
        pre_api_candidates,
        context_fields
    )
    
    pre_apis = dep_trace_result['pre_apis']
    field_resolutions = dep_trace_result['field_resolutions']
    
    # 应用 field_resolutions 到 body_field_roles
    for step in steps:
        if step['action'] in ['create', 'update']:
            for field_name in step['body_template'].keys():
                key = (step['action'], field_name)
                if key in field_resolutions:
                    resolution = field_resolutions[key]
                    step['body_field_roles'][field_name] = {
                        'role': 'pre_api_ref',
                        'source': resolution['source'],
                        'is_array': resolution.get('is_array', False)
                    }
    
    # 构建 manifest
    manifest = {
        'manifest_version': '1.1',
        'module': module_info,
        'response_contract': response_contract,
        'auth_profile': auth_profile,
        'pre_apis': pre_apis,  # 新增
        'steps': steps,
        'state_assertions': state_assertions
    }
    
    return manifest
```

---

### 4.3 Phase C: Runtime 前置 API 执行

#### 4.3.1 新增方法

**文件**：`lib/runtime/test_runtime.py`

```python
class TestRunner:
    def execute_pre_apis(self, session: requests.Session):
        """
        按依赖顺序执行前置 API，提取值到 state
        
        Args:
            session: 已认证的 requests.Session
        """
        pre_apis = self.manifest.get('pre_apis', [])
        
        if not pre_apis:
            # 向后兼容：回退到旧逻辑
            self.fetch_context(session)
            return
        
        LOG.info(f"[Pre-APIs] 执行 {len(pre_apis)} 个前置 API")
        
        for pre_api in pre_apis:
            api_id = pre_api['id']
            api_name = pre_api['name']
            
            # 检查依赖
            depends_on = pre_api.get('depends_on', [])
            missing_deps = [dep for dep in depends_on if dep not in self.state]
            if missing_deps:
                LOG.warning(f"[Pre-APIs] 跳过 {api_name}，缺少依赖: {missing_deps}")
                continue
            
            # 构建请求
            url = self.base_url + pre_api['pathname']
            method = pre_api.get('method', 'GET')
            
            # 执行请求
            try:
                if method == 'GET':
                    resp = session.get(url, timeout=10)
                else:
                    body = pre_api.get('body_template', {})
                    resp = session.post(url, json=body, timeout=10)
                
                resp.raise_for_status()
                response_body = resp.json()
                
                LOG.info(f"[Pre-APIs] {api_name} 执行成功")
                
                # 提取字段到 state
                for extract in pre_api.get('extracts', []):
                    extract_name = extract['name']
                    extract_path = extract['path']
                    
                    value = self._extract_with_array_index(
                        response_body,
                        extract_path
                    )
                    
                    if value is not None:
                        self.state[extract_name] = value
                        LOG.info(f"  - {extract_name}: {value}")
                    else:
                        LOG.warning(f"  - {extract_name}: 提取失败")
                
            except Exception as e:
                LOG.error(f"[Pre-APIs] {api_name} 执行失败: {e}")
                raise
    
    def _extract_with_array_index(self, obj: dict, path: str):
        """
        支持数组索引的路径提取
        
        Args:
            obj: JSON 对象
            path: 路径，例如 "entity.list[0].id"
        
        Returns:
            提取的值，或 None
        """
        import re
        
        # 分割路径段
        parts = []
        current = ''
        
        for char in path:
            if char in '.[':
                if current:
                    parts.append(current)
                    current = ''
                if char == '[':
                    parts.append('[')
            elif char == ']':
                if current:
                    parts.append(current)
                    current = ''
                parts.append(']')
            else:
                current += char
        
        if current:
            parts.append(current)
        
        # 遍历提取
        value = obj
        i = 0
        while i < len(parts):
            part = parts[i]
            
            if part == '[':
                # 下一个部分是索引
                i += 1
                if i < len(parts):
                    try:
                        index = int(parts[i])
                        value = value[index]
                    except (ValueError, IndexError, TypeError):
                        return None
                i += 1
                # 跳过 ']'
                if i < len(parts) and parts[i] == ']':
                    i += 1
            else:
                # 普通字段访问
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None
                i += 1
            
            if value is None:
                return None
        
        return value
```

#### 4.3.2 改动 `_build_body()`

```python
def _build_body(self, step_def):
    body_template = step_def.get('body_template', {})
    body_field_roles = step_def.get('body_field_roles', {})
    
    body = {}
    
    for field_name, field_value in body_template.items():
        role_info = body_field_roles.get(field_name, {})
        role = role_info.get('role', 'static')
        
        if role == 'static':
            body[field_name] = field_value
        
        elif role == 'context':
            source = role_info.get('source', field_name)
            body[field_name] = self.state.get(source, field_value)
        
        elif role == 'id_ref':
            body[field_name] = self.state.get('id', field_value)
        
        elif role == 'name':
            body[field_name] = self.state.get('create_body', {}).get(
                field_name, field_value
            )
        
        elif role == 'mutable':
            body[field_name] = f"auto_modified_{self.state.get('timestamp', '')}"
        
        elif role == 'pre_api_ref':
            # 新增：前置 API 引用
            source = role_info.get('source', '')
            # source 格式: "api_id.extract_name"
            parts = source.split('.')
            extract_name = parts[-1] if parts else source
            
            value = self.state.get(extract_name, field_value)
            
            # 处理 is_array
            if role_info.get('is_array', False):
                body[field_name] = [value] if not isinstance(value, list) else value
            else:
                body[field_name] = value
        
        else:
            body[field_name] = field_value
    
    return body
```

---

### 4.4 Phase D: Stage 5 导出模块

#### 4.4.1 新增文件

**文件**：`module_discovery/export_artifacts.py`

```python
import json
import uuid
from pathlib import Path
from typing import Dict, List, Any
import openpyxl


def export_postman_collection(manifest: dict, output_path: Path):
    """
    导出 Postman Collection v2.1.0
    
    Args:
        manifest: 完整 manifest 字典
        output_path: 输出文件路径
    """
    collection = {
        'info': {
            '_postman_id': str(uuid.uuid4()),
            'name': manifest['module']['name'],
            'schema': 'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'
        },
        'item': []
    }
    
    # 前置 API 文件夹
    if manifest.get('pre_apis'):
        pre_api_folder = {
            'name': '00_前置操作',
            'item': []
        }
        
        for pre_api in manifest['pre_apis']:
            request_item = _build_postman_request(
                pre_api,
                manifest['module']['base_url'],
                is_pre_api=True
            )
            pre_api_folder['item'].append(request_item)
        
        collection['item'].append(pre_api_folder)
    
    # 业务操作文件夹
    business_folder = {
        'name': '01_业务操作',
        'item': []
    }
    
    for i, step in enumerate(manifest.get('steps', []), 1):
        request_item = _build_postman_request(
            step,
            manifest['module']['base_url'],
            is_pre_api=False,
            step_index=i
        )
        business_folder['item'].append(request_item)
    
    collection['item'].append(business_folder)
    
    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)


def _build_postman_request(
    api_def: dict,
    base_url: str,
    is_pre_api: bool,
    step_index: int = 0
) -> dict:
    """构建 Postman 请求项"""
    
    # 构建 URL
    pathname = api_def.get('pathname', '')
    url_parts = pathname.split('/')
    
    url = {
        'raw': f"{{{{base_url}}}}{pathname}",
        'host': ['{{base_url}}'],
        'path': [p for p in url_parts if p]
    }
    
    # 构建 Headers
    headers = [
        {
            'key': 'Content-Type',
            'value': 'application/json',
            'type': 'text'
        },
        {
            'key': 'Authorization',
            'value': '{{auth_header}}',
            'type': 'text'
        }
    ]
    
    # 构建 Body
    body = None
    body_template = api_def.get('body_template', {})
    body_field_roles = api_def.get('body_field_roles', {})
    
    if body_template:
        body_raw = {}
        
        for field_name, field_value in body_template.items():
            role_info = body_field_roles.get(field_name, {})
            role = role_info.get('role', 'static')
            
            if role in ['context', 'pre_api_ref']:
                source = role_info.get('source', field_name)
                # 转换为 {{variable}} 格式
                var_name = source.replace('.', '_')
                body_raw[field_name] = f"{{{{{var_name}}}}}"
            elif role == 'id_ref':
                body_raw[field_name] = '{{id}}'
            elif role == 'name':
                body_raw[field_name] = f"{{{{create_body_{field_name}}}}}"
            elif role == 'mutable':
                body_raw[field_name] = '{{mutable_value}}'
            else:
                body_raw[field_name] = field_value
        
        body = {
            'mode': 'raw',
            'raw': json.dumps(body_raw, ensure_ascii=False, indent=2)
        }
    
    # 构建 Test 脚本（提取响应值）
    event = []
    
    extracts = api_def.get('extracts', [])
    if extracts:
        test_script_lines = [
            'pm.test("提取响应值", function () {',
            '    var jsonData = pm.response.json();',
            '    pm.expect(pm.response.code).to.be.oneOf([200, 201]);',
            ''
        ]
        
        for extract in extracts:
            extract_name = extract['name']
            extract_path = extract['path']
            
            # 转换路径为 JavaScript 访问
            js_path = extract_path.replace('.', '.').replace('[', '[').replace(']', ']')
            test_script_lines.append(
                f'    var {extract_name} = jsonData{js_path};'
            )
            test_script_lines.append(
                f'    pm.environment.set("{extract_name}", {extract_name});'
            )
        
        test_script_lines.append('});')
        
        event.append({
            'listen': 'test',
            'script': {
                'exec': test_script_lines,
                'type': 'text/javascript'
            }
        })
    
    # 构建名称
    name = api_def.get('name', api_def.get('label', 'Unnamed'))
    if not is_pre_api and step_index > 0:
        name = f"{step_index:02d}_{name}"
    
    return {
        'name': name,
        'request': {
            'method': api_def.get('method', 'GET'),
            'header': headers,
            'body': body,
            'url': url
        },
        'response': [],
        'event': event
    }


def export_helpers(manifest: dict, output_path: Path):
    """
    导出 helpers.py（高层函数）
    
    Args:
        manifest: 完整 manifest 字典
        output_path: 输出文件路径
    """
    helpers_code = '''"""
Helper functions for API testing
Generated by Stage 5
"""

import json
import time
import random
from pathlib import Path


def get_token_by_cookie(cookie_path: str, token_key: str = "accessToken") -> str:
    """
    从 Cookie 文件提取 Token
    
    Args:
        cookie_path: Cookie 文件路径
        token_key: Token 字段名
    
    Returns:
        Token 字符串
    """
    with open(cookie_path, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    
    for cookie in cookies:
        if cookie['name'] == token_key:
            return cookie['value']
    
    raise ValueError(f"Cookie '{token_key}' not found")


def gen_timestamp() -> str:
    """生成时间戳（毫秒级）"""
    return str(int(time.time() * 1000))


def gen_unique_name(prefix: str, ts: str, original: str) -> str:
    """
    生成唯一名称
    
    Args:
        prefix: 前缀
        ts: 时间戳
        original: 原始名称
    
    Returns:
        唯一名称，例如 "AT_test_1234567890"
    """
    return f"{prefix}_{ts}_{original}"


def gen_mutable_value(ts: str) -> str:
    """
    生成可修改字段值
    
    Args:
        ts: 时间戳
    
    Returns:
        可修改值，例如 "auto_modified_1234567890"
    """
    return f"auto_modified_{ts}"
'''
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(helpers_code)


def export_excel_params(manifest: dict, output_path: Path):
    """
    导出 Excel 参数文件
    
    Args:
        manifest: 完整 manifest 字典
        output_path: 输出文件路径
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '变量配置'
    
    # 写入表头
    headers = ['变量名称', '是否敏感类型', '变量值', '备注']
    ws.append(headers)
    
    # 收集全局参数
    global_params = _collect_global_params(manifest)
    
    # 写入参数
    for param in global_params:
        ws.append([
            param['name'],
            '是' if param.get('sensitive', False) else '否',
            param['value'],
            param.get('description', '')
        ])
    
    # 保存文件
    wb.save(output_path)


def _collect_global_params(manifest: dict) -> List[Dict[str, Any]]:
    """收集全局参数（在多个步骤中使用的参数）"""
    params = []
    
    # 基础参数
    params.extend([
        {
            'name': 'e_base_url',
            'value': manifest['module']['base_url'],
            'sensitive': False,
            'description': 'API 基础地址'
        },
        {
            'name': 'e_username',
            'value': '${username}',
            'sensitive': False,
            'description': '登录用户名'
        },
        {
            'name': 'e_password',
            'value': '${password}',
            'sensitive': True,
            'description': '登录密码'
        },
        {
            'name': 'e_auth_header',
            'value': '${get_token_by_cookie("cookies.json", "accessToken")}',
            'sensitive': True,
            'description': '认证 Header'
        }
    ])
    
    # 前置 API 提取的参数
    for pre_api in manifest.get('pre_apis', []):
        for extract in pre_api.get('extracts', []):
            params.append({
                'name': f"e_{extract['name']}",
                'value': f"${{{extract['name']}}}",
                'sensitive': False,
                'description': f"从 {pre_api['name']} 提取"
            })
    
    return params
```

#### 4.4.2 依赖配置

**文件**：`requirements.txt`

```txt
openpyxl>=3.1.0
```

---

### 4.5 Phase E: 管道编排

#### 4.5.1 改动 `run.py`

```python
def run_pipeline(project_config):
    # ... 现有代码 ...
    
    # Stage 2: 捕获 API
    capture_result = await capture_apis(project_config)
    
    # 传递 pre_api_candidates 到 Stage 3
    pre_api_candidates = capture_result.get('pre_api_candidates', [])
    
    # Stage 3: 分析流程
    analysis_result = analyze_flow(
        capture_result,
        pre_api_candidates=pre_api_candidates  # 新增参数
    )
    
    # Stage 4: 生成脚本
    manifest = analysis_result['manifest']
    script_path = generate_script(manifest, project_config)
    
    # Stage 4.5: 自动运行验证
    verify_result = await run_stage4_verify(script_path)
    if not verify_result['success']:
        LOG.error("Stage 4 验证失败")
        return
    
    # Stage 5: 导出 artifacts（如果启用）
    if project_config.get('stage5_export', False):
        await run_stage5(manifest, project_config)


async def run_stage4_verify(script_path: Path) -> dict:
    """
    自动运行 Stage 4 生成的脚本并验证
    
    Args:
        script_path: 生成的脚本路径
    
    Returns:
        验证结果字典
    """
    import subprocess
    
    LOG.info(f"[Stage 4.5] 自动运行脚本: {script_path}")
    
    try:
        result = subprocess.run(
            ['python', str(script_path)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=script_path.parent
        )
        
        # 解析 JSONL 报告
        report_path = script_path.parent / 'report' / f"{script_path.stem}.jsonl"
        if not report_path.exists():
            return {'success': False, 'error': '报告文件未生成'}
        
        with open(report_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        total = len(lines)
        passed = sum(1 for line in lines if '"status": "passed"' in line)
        
        pass_rate = passed / total if total > 0 else 0
        
        LOG.info(f"[Stage 4.5] 通过率: {passed}/{total} ({pass_rate:.1%})")
        
        return {
            'success': pass_rate >= 0.8,
            'pass_rate': pass_rate,
            'passed': passed,
            'total': total
        }
    
    except subprocess.TimeoutExpired:
        LOG.error("[Stage 4.5] 脚本执行超时")
        return {'success': False, 'error': '执行超时'}
    except Exception as e:
        LOG.error(f"[Stage 4.5] 脚本执行失败: {e}")
        return {'success': False, 'error': str(e)}


async def run_stage5(manifest: dict, project_config: dict):
    """
    Stage 5: 导出 artifacts
    
    Args:
        manifest: 完整 manifest 字典
        project_config: 项目配置
    """
    from module_discovery.export_artifacts import (
        export_postman_collection,
        export_helpers,
        export_excel_params
    )
    
    output_dir = Path(project_config['output_dir'])
    module_name = manifest['module']['name']
    
    LOG.info(f"[Stage 5] 导出 artifacts 到 {output_dir}")
    
    # 导出 Postman Collection
    postman_path = output_dir / f"{module_name}.postman_collection.json"
    export_postman_collection(manifest, postman_path)
    LOG.info(f"[Stage 5] Postman Collection: {postman_path}")
    
    # 导出 helpers.py
    helpers_path = output_dir / 'helpers.py'
    export_helpers(manifest, helpers_path)
    LOG.info(f"[Stage 5] Helpers: {helpers_path}")
    
    # 导出 Excel 参数
    excel_path = output_dir / f"{module_name}_参数.xlsx"
    export_excel_params(manifest, excel_path)
    LOG.info(f"[Stage 5] Excel 参数: {excel_path}")
```

#### 4.5.2 项目配置

**文件**：`projects/ecm-compute/config.yaml`

```yaml
stage5_export: true  # 启用 Stage 5 导出
```

---

## 5. 示例：用户管理模块

### 5.1 前置 API 识别

**页面加载期间捕获的 API**：
1. `GET /estack/api/estack/draco/v1/users/current-user`
   - 响应：`{"entity": {"id": "xxx", "tenantId": "yyy", ...}}`
   - 提取字段：`tenant_id`, `admin_id`

2. `GET /estack/api/estack/draco/v1/policies`
   - 响应：`{"entity": {"list": [{"id": "zzz", ...}, ...]}}`
   - 提取字段：`policy_id` (list[0].id)

### 5.2 依赖链追踪

**业务步骤 `create`**：
```json
{
  "body_template": {
    "tenantId": "yyy",
    "adminId": "xxx",
    "policyIds": ["zzz"],
    "userName": "AT_test_123"
  },
  "body_field_roles": {
    "tenantId": {"role": "static"},
    "adminId": {"role": "static"},
    "policyIds": {"role": "static"},
    "userName": {"role": "name"}
  }
}
```

**追踪结果**：
- `tenantId: "yyy"` → 精确匹配 `current-user.entity.tenantId`
- `adminId: "xxx"` → 精确匹配 `current-user.entity.id`
- `policyIds: ["zzz"]` → 列表成员匹配 `policies.entity.list[0].id`

**更新后的 `body_field_roles`**：
```json
{
  "tenantId": {
    "role": "pre_api_ref",
    "source": "current_user.tenant_id"
  },
  "adminId": {
    "role": "pre_api_ref",
    "source": "current_user.admin_id"
  },
  "policyIds": {
    "role": "pre_api_ref",
    "source": "policies.policy_id",
    "is_array": true
  },
  "userName": {
    "role": "name"
  }
}
```

### 5.3 运行时执行流程

```
1. setup_auth() → 认证
2. execute_pre_apis()
   - GET /current-user → state['tenant_id'] = 'yyy', state['admin_id'] = 'xxx'
   - GET /policies → state['policy_id'] = 'zzz'
3. prepare_create_body() → state['create_body'] = {...}
4. execute_step('create')
   - _build_body() 解析 pre_api_ref:
     - tenantId → state['tenant_id'] = 'yyy'
     - adminId → state['admin_id'] = 'xxx'
     - policyIds → [state['policy_id']] = ['zzz']
   - POST /users → state['id'] = 'new_user_id'
5. execute_step('query') → GET /users?name=...
6. execute_step('update') → PUT /users/{id}
...
```

---

## 6. 验证策略

### 6.1 单元测试

```python
def test_trace_pre_api_dependencies():
    """测试依赖链追踪算法"""
    steps = [
        {
            'action': 'create',
            'body_template': {
                'tenantId': 'test_tenant',
                'policyIds': ['policy_123']
            },
            'body_field_roles': {
                'tenantId': {'role': 'static'},
                'policyIds': {'role': 'static'}
            }
        }
    ]
    
    pre_api_candidates = [
        {
            'id': 'current_user',
            'name': '获取当前用户',
            'pathname': '/users/current-user',
            'response_sample': {
                'response_body': json.dumps({
                    'entity': {'tenantId': 'test_tenant', 'id': 'admin_123'}
                })
            },
            'extracted_fields': [
                {'name': 'tenant_id', 'path': 'entity.tenantId'},
                {'name': 'admin_id', 'path': 'entity.id'}
            ]
        },
        {
            'id': 'policies',
            'name': '获取策略列表',
            'pathname': '/policies',
            'response_sample': {
                'response_body': json.dumps({
                    'entity': {'list': [{'id': 'policy_123'}]}
                })
            },
            'extracted_fields': [
                {'name': 'policy_id', 'path': 'entity.list[0].id'}
            ]
        }
    ]
    
    result = trace_pre_api_dependencies(steps, pre_api_candidates, {})
    
    # 验证 pre_apis
    assert len(result['pre_apis']) == 2
    assert result['pre_apis'][0]['id'] == 'current_user'
    assert result['pre_apis'][1]['id'] == 'policies'
    
    # 验证 field_resolutions
    assert ('create', 'tenantId') in result['field_resolutions']
    assert result['field_resolutions'][('create', 'tenantId')]['source'] == 'current_user.tenant_id'
    
    assert ('create', 'policyIds') in result['field_resolutions']
    assert result['field_resolutions'][('create', 'policyIds')]['source'] == 'policies.policy_id'
    assert result['field_resolutions'][('create', 'policyIds')]['is_array'] == True


def test_extract_with_array_index():
    """测试数组索引提取"""
    runner = TestRunner({})
    
    obj = {
        'entity': {
            'list': [
                {'id': 'first'},
                {'id': 'second'}
            ]
        }
    }
    
    assert runner._extract_with_array_index(obj, 'entity.list[0].id') == 'first'
    assert runner._extract_with_array_index(obj, 'entity.list[1].id') == 'second'
    assert runner._extract_with_array_index(obj, 'entity.list[2].id') is None
```

### 6.2 集成测试

```python
@pytest.mark.asyncio
async def test_end_to_end_user_management():
    """端到端测试：用户管理模块"""
    project_config = {
        'project_dir': 'projects/ecm-compute',
        'module_name': '用户管理',
        'stage5_export': True
    }
    
    # 运行完整管道
    result = await run_pipeline(project_config)
    
    # 验证 Stage 2
    assert 'pre_api_candidates' in result['capture_result']
    assert len(result['capture_result']['pre_api_candidates']) >= 2
    
    # 验证 Stage 3
    manifest = result['manifest']
    assert 'pre_apis' in manifest
    assert len(manifest['pre_apis']) >= 2
    
    # 验证 pre_api_ref 角色
    create_step = next(s for s in manifest['steps'] if s['action'] == 'create')
    assert 'tenantId' in create_step['body_field_roles']
    assert create_step['body_field_roles']['tenantId']['role'] == 'pre_api_ref'
    
    # 验证 Stage 4.5
    assert result['verify_result']['success'] == True
    assert result['verify_result']['pass_rate'] >= 0.8
    
    # 验证 Stage 5
    output_dir = Path(project_config['project_dir']) / 'output'
    assert (output_dir / '用户管理.postman_collection.json').exists()
    assert (output_dir / 'helpers.py').exists()
    assert (output_dir / '用户管理_参数.xlsx').exists()
```

### 6.3 向后兼容测试

```python
def test_backward_compatibility():
    """测试无 pre_apis 的 manifest 仍能正常工作"""
    old_manifest = {
        'manifest_version': '1.0',
        'module': {'name': 'Test'},
        'response_contract': {...},
        'auth_profile': {
            'probe_url': '/current-user',
            'context_fields': {'tenantId': {'path': 'entity.tenantId'}}
        },
        'steps': [...],
        'state_assertions': {...}
        # 注意：没有 pre_apis 字段
    }
    
    runner = TestRunner(old_manifest)
    
    # execute_pre_apis() 应该回退到 fetch_context()
    session = requests.Session()
    runner.execute_pre_apis(session)
    
    # 验证 context_fields 仍然被提取
    assert 'tenantId' in runner.state
```

---

## 7. 风险与缓解

### 7.1 性能风险

**风险**：页面加载捕获增加额外耗时  
**缓解**：复用现有页面导航逻辑，额外耗时 < 3 秒  
**验证**：在用户管理模块上测量实际耗时

### 7.2 误判风险

**风险**：三策略匹配可能产生误判  
**缓解**：
- 优先使用精确值匹配
- 字段名启发式仅作为备选
- 无法追踪的字段保持 `static` 角色
**验证**：单元测试覆盖各种匹配场景

### 7.3 向后兼容风险

**风险**：旧 manifest 无法在新 runtime 上运行  
**缓解**：
- `execute_pre_apis()` 检测 `pre_apis` 字段
- 无 `pre_apis` 时回退到 `fetch_context()`
**验证**：运行所有现有生成的脚本

### 7.4 数组路径风险

**风险**：`extract_by_path()` 不支持数组索引  
**缓解**：新增 `_extract_with_array_index()` 方法  
**验证**：单元测试覆盖各种路径格式

---

## 8. 关键文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `module_discovery/capture_apis.py` | 新增函数 | 页面加载 API 捕获 |
| `module_discovery/analyze_flow.py` | 新增函数 + 改动 | 依赖链追踪算法 |
| `lib/runtime/test_runtime.py` | 新增方法 + 改动 | 前置 API 执行 |
| `module_discovery/gen_test.py` | 无改动 | 传递增强 manifest |
| `module_discovery/export_artifacts.py` | 新文件 | Stage 5 导出 |
| `module_discovery/run.py` | 新增函数 | Stage 5 编排 |
| `module_discovery/const.py` | 新增常量 | `pre_api_ref` 角色 |
| `requirements.txt` | 新增依赖 | `openpyxl>=3.1.0` |

---

## 9. 实施顺序

1. **Phase A** (1天)：Stage 2 页面加载捕获
2. **Phase B** (2天)：Stage 3 依赖链追踪算法
3. **Phase C** (1天)：Runtime 前置 API 执行
4. **Phase D** (2天)：Stage 5 导出模块
5. **Phase E** (1天)：管道编排 + 验证

**总计**：7天

---

## 10. 后续步骤

1. 按 Phase A-E 顺序实施
2. 每个 Phase 完成后运行用户管理模块端到端测试验证
3. 验证 Postman Collection 可成功导入 Postman
4. 验证 Excel 参数文件与参考格式一致

---

**文档结束**
