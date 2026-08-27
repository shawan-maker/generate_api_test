# 角色管理 API 测试 - 经验总结

**测试时间**: 2026-08-26  
**测试模块**: 角色管理 (Role Management)  
**目标URL**: `/estack/web/estack/user-center/user-manage/role`  
**API基础路径**: `/estack/api/estack/draco/v1/policies`

## 执行结果

✅ **全部通过**: 创建 → 查询 → 详情 → 更新 → 删除 → 验证删除

测试脚本: `projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py`  
测试报告: `output/reports/report_角色管理.html`

---

## 关键发现与教训

### 1. 凭据配置陷阱：空 credentials 对象导致自动登录失败

**问题**:  
初始脚本使用 `"credentials": {}`，导致 `AuthSession` 无法识别传入的 username/password，即使显式传递也无效。

**根因**:  
```python
# 错误配置
profile = {
    "credentials": {},  # 空对象！
    ...
}
sess = AuthSession(profile, username, password)  # 传入的凭据被忽略
```

**正确做法**:
```python
# 正确配置 - 在 profile 中包含凭据
profile = {
    "credentials": {
        "username": username,
        "password": password,
    },
    ...
}
```

**验证**:  
- 空 credentials 对象 → 自动登录失败，返回 `None`
- 包含凭据的 credentials 对象 → 自动登录成功

**适用场景**:  
所有需要鉴权的 API 测试脚本。

---

### 2. Cookie 过期处理：TTL 机制与自动刷新

**机制**:  
- Cookie 有效期由 `meta.json` 中的 `savedAt` 时间戳控制
- TTL = 300 秒（5 分钟），超过后需要刷新
- 刷新方式：重新执行滑块登录

**问题现象**:
```
[auth] 探活: HTTP 401
[auth] cookie 失效且无可用凭据
❌ 鉴权失败
```

**解决方案**:
```python
# 在 profile 中配置凭据，启用自动登录回退
"credentials": {
    "username": "estack-yy",
    "password": "R@9eDuck$!mpleM00n",
}
```

**最佳实践**:
- 测试前检查 `output/config/meta.json` 的 `savedAt` 时间
- 如果超过 5 分钟，手动刷新或确保 credentials 配置正确
- 生产环境：使用环境变量或密钥管理服务

---

### 3. 超管租户限制：无法在超管组织下创建角色

**问题**:  
使用当前用户的 `tenantId`（超管组织）创建角色时返回错误：
```
无法操作超管组织
```

**根因**:  
系统禁止在超管组织（Super Management Department）下创建自定义角色。

**解决方案**:
```python
# 获取可用的非超管租户
resp = client.get(f"{BASE_URL}/estack/api/estack/draco/v1/tenants")
tenants = resp.json().get('entity', {}).get('list', [])
tenant_id = tenants[0].get('id')  # 使用第一个普通租户
```

**验证**:
- 超管 tenantId: `cec63451f8bf4ceebb9ada0b87d829bf` → 创建失败
- 普通租户 ID: `f592689dad3744979b11faf199a93303` → 创建成功

**适用场景**:  
所有涉及租户隔离的操作（用户、角色、资源等）。

---

### 4. policyDocument 格式要求：JSON 字符串 vs 对象

**问题**:  
尝试将 `policyDocument` 作为 JSON 对象传递时失败：
```python
# 错误 - 对象格式
"policyDocument": {
    "version": "v1",
    "statement": [...]
}
# 错误: 参数格式或者参数类型非法
```

**正确格式**:
```python
# 正确 - JSON 字符串
"policyDocument": json.dumps({
    "version": "v1",
    "statement": [{
        "effect": "ALLOW",
        "action": ["USER:ListUsers", "USER:ListRoles"],
        "resource": ["*"]
    }]
})
```

**字段命名规范**:
- ❌ `policyText` - 错误字段名
- ❌ `document` - 错误字段名
- ✅ `policyDocument` - 正确字段名

**适用场景**:  
所有涉及策略文档的 API（角色、权限、策略等）。

---

### 5. 策略文档语法：小写 vs 大写

**问题**:  
使用大写关键字（如 AWS IAM 风格）时失败：
```python
# 错误 - AWS IAM 风格
{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Action": ["ecs:Describe*"],
        "Resource": ["*"]
    }]
}
# 错误: 权限策略格式错误
```

**正确语法**:
```python
# 正确 - 小写关键字
{
    "version": "v1",
    "statement": [{
        "effect": "ALLOW",
        "action": ["USER:ListUsers"],
        "resource": ["*"]
    }]
}
```

**关键字对照表**:
| AWS IAM | estack |
|---------|--------|
| `Version` | `version` |
| `Statement` | `statement` |
| `Effect` | `effect` |
| `Allow/Deny` | `ALLOW/DENY` |
| `Action` | `action` |
| `Resource` | `resource` |

**验证方法**:  
查询现有角色详情，参考其 `policyDocument` 格式。

---

### 6. tenantId 参数要求：GET vs POST/PUT/DELETE

**发现**:  
不同 HTTP 方法对 `tenantId` 的要求不同：

| 操作 | 方法 | tenantId 传递方式 |
|------|------|------------------|
| 列表查询 | POST | Body JSON |
| 详情查询 | GET | Query 参数 |
| 创建角色 | POST | Body JSON（可选） |
| 更新角色 | PUT | Body JSON |
| 删除角色 | DELETE | Query 参数 |

**示例**:
```python
# GET - 使用 query 参数
resp = client.get(url, params={"tenantId": tenant_id})

# POST/PUT - 使用 body
resp = client.post(url, json={..., "tenantId": tenant_id})

# DELETE - 使用 query 参数
resp = client.delete(url, params={"tenantId": tenant_id})
```

**适用场景**:  
所有带租户隔离的 API。

---

### 7. 探测优先原则：先验证再编码

**教训**:  
初始测试脚本基于假设编写，导致多个错误。

**正确流程**:
1. **手动探测** API，验证端点和参数
2. **捕获真实请求/响应**，参考现有数据
3. **编写测试脚本**，基于实际行为
4. **逐步验证**，每个步骤单独测试

**工具**:
```bash
# 使用 httpx 直接探测
python << 'PYEOF'
import httpx
import json
from pathlib import Path

# 加载 cookies
cookies = json.loads(Path('output/config/cookies.json').read_text())
token = next(c['value'] for c in cookies if c['name'] == 'accessToken')

# 创建 client
client = httpx.Client(
    base_url='https://10.151.37.249',
    headers={'Authorization': f'Bearer {token}'}
)

# 探测 API
resp = client.get('/estack/api/estack/draco/v1/tenants')
print(json.dumps(resp.json(), indent=2, ensure_ascii=False))
PYEOF
```

**最佳实践**:
- 不要假设 API 行为
- 参考现有数据（如查询现有角色的 policyDocument）
- 先验证单个操作，再组合成完整流程

---

## 优化建议

### 对现有流程的改进

1. **凭据管理标准化**
   - 在 `get_auth_client()` 中统一处理 credentials
   - 添加环境变量支持：`ESTACK_USER`, `ESTACK_PASS`
   - 文档化凭据配置的三种方式

2. **Cookie 刷新机制**
   - 在测试脚本开头检查 cookie 新鲜度
   - 提供手动刷新命令：`python scripts/refresh_cookies.py`
   - 在 CI/CD 中自动化刷新流程

3. **租户选择策略**
   - 创建 `get_test_tenant()` 工具函数
   - 自动选择非超管租户
   - 支持多租户测试场景

4. **错误处理增强**
   - 捕获常见错误（超管限制、格式错误等）
   - 提供友好的错误提示
   - 自动重试机制（针对临时失败）

5. **文档自动化**
   - 从测试脚本生成 API 文档
   - 记录请求/响应示例
   - 自动生成字段说明

### 对 module_discovery 工具的改进

1. **Stage 2 API 捕获增强**
   - 修复 toolbar 扫描逻辑（使用 UI 结果而非重新扫描）
   - 改进表单填充器（处理复杂表单）
   - 增加重试机制（针对网络波动）

2. **策略文档识别**
   - 添加 policyDocument 格式检测
   - 自动识别 JSON 字符串 vs 对象
   - 提供格式修正建议

3. **租户隔离感知**
   - 在 Stage 3 分析阶段识别租户限制
   - 自动选择测试租户
   - 生成多租户测试用例

---

## 技术细节

### API 端点清单

| 操作 | 方法 | 端点 | 关键参数 |
|------|------|------|----------|
| 列表查询 | POST | `/policies/list` | `tenantId`, `pageNum`, `pageSize` |
| 详情查询 | GET | `/policies/{id}` | `tenantId` (query) |
| 创建角色 | POST | `/policies` | `policyName`, `policyDocument`, `tenantId` |
| 更新角色 | PUT | `/policies/{id}` | `policyName`, `description`, `policyDocument`, `tenantId` |
| 删除角色 | DELETE | `/policies/{id}` | `tenantId` (query) |

### 请求/响应示例

**创建角色**:
```json
// Request
POST /estack/api/estack/draco/v1/policies
{
  "policyName": "AT_Role_a2f1e9",
  "policyType": "CUSTOM",
  "policyCategory": "ORGANIZATION",
  "description": "自动测试角色",
  "policyDocument": "{\"version\":\"v1\",\"statement\":[{\"effect\":\"ALLOW\",\"action\":[\"USER:ListUsers\",\"USER:ListRoles\"],\"resource\":[\"*\"]}]}",
  "tenantId": "f592689dad3744979b11faf199a93303"
}

// Response
{
  "success": true,
  "entity": {
    "id": "20a7ba32bafb4ca88a4aa55d2c9ada49",
    "policyName": "AT_Role_a2f1e9",
    "policyType": "CUSTOM",
    "createdAt": "2026-08-26T10:15:30",
    ...
  }
}
```

### 状态断言

- **创建后**: 角色应出现在列表中
- **更新后**: 角色名称/描述应变化
- **删除后**: 角色不应出现在列表中
- **详情查询**: 返回完整的 policyDocument

---

## 结论

角色管理 API 测试成功完成，验证了完整的 CRUD 流程。

**关键收获**:
1. 凭据配置必须显式包含在 profile.credentials 中
2. 超管租户有操作限制，需要使用普通租户
3. policyDocument 必须是 JSON 字符串，使用小写关键字
4. 不同 HTTP 方法对参数传递方式有不同要求
5. 探测优先原则可以避免大量假设错误

**下一步**:
- 将经验应用到其他模块（部门管理、租户管理等）
- 优化 module_discovery 工具链
- 建立标准化的 API 测试模板
