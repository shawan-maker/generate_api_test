# 固定枚举泛化机制设计文档

**版本**: v1.0  
**日期**: 2026-09-09  
**状态**: 待讨论

## 1. 概述

### 1.1 问题背景

当前代码中存在大量固定枚举值（字段名、按钮文本、API 路径、响应结构假设等），这些值对特定模块（如用户管理、角色管理）有效，但面对新模块、新项目时会失效。

### 1.2 审计范围

- **探索文件数**: 15+ 个核心模块
- **发现硬编码值**: 200+ 处
- **涉及类别**: 9 个主要类别（A-I）

### 1.3 目标

系统性识别所有硬编码，提出数据驱动的泛化方案，确保框架适用于：
- 新模块（非用户管理/角色管理）
- 新项目（非 estack 平台）
- 新 UI 框架（非 Element UI/Ant Design）

---

## 2. 分类清单 + 泛化方案

### Category A: 响应结构假设（最高优先级）

**问题**：代码假设所有 API 响应遵循固定结构（`entity`/`data` 信封、`success`/`errorCode` 字段、`list`/`total` 分页）。

#### 2.1.1 发现清单

| 位置 | 硬编码值 | 风险 | 当前状态 |
|------|---------|------|---------|
| `test_runtime.py:47-54` | `["entity", "data", "result", "payload"]`, `"success"`, `"errorCode"`, `["list", "records", "rows", "items"]` | 中 | 已有 manifest 驱动，但 defaults 是硬编码 |
| `analyze_flow.py:83` | `body.get("entity", body)` | 高 | 未使用 response_contract |
| `analyze_flow.py:1182` | `body.get("entity", {})` + `"id"` | 高 | 未使用已发现的 envelope_keys 和 id_field |
| `analyze_flow.py:272` | `if "id" in entity` | 中 | 未使用 COMMON_ID_FIELDS |

#### 2.1.2 泛化方案

**方案 1: 修复不一致（推荐）**
- `analyze_flow.py:83,1182,272` 改用 `response_contract` 中已发现的值
- 确保所有信封提取使用统一的候选列表

**方案 2: 默认值外移**
- `test_runtime.py` 的 defaults 移到 `const.py`
- 确保与 Stage 3 发现逻辑一致

**方案 3: 响应结构发现增强**
- 在 Stage 3 增加响应结构自动发现
- 写入 manifest 的 `response_contract`

#### 2.1.3 实施复杂度

**低**（主要是代码修复，不改架构）

#### 2.1.4 验证方式

重新生成用户管理 manifest，检查 envelope_keys 是否正确使用已发现的值而非硬编码。

---

### Category B: 字段名匹配（高优先级）

**问题**：代码用固定字段名列表匹配上下文字段、ID 字段、名称字段。

#### 2.2.1 发现清单

| 位置 | 硬编码值 | 风险 | 问题 |
|------|---------|------|------|
| `analyze_flow.py:1797-1820` | `_CONTEXT_SUFFIXES = ('id', 'Id', 'tenant', 'org', 'dept', 'admin', 'role', 'policy', ...)` | **极高** | 包含业务特定术语，且重复定义 |
| `const.py:198-202` | `COMMON_ID_FIELDS = ["userId", "tenantId", "roleId", "policyId", "instanceId", ...]` | 高 | 包含云/基础设施特定术语 |
| `const.py:224-226` | `NAME_FIELD_KEYWORDS`, `MUTABLE_FIELD_KEYWORDS` | 中 | 相对通用 |
| `test_runtime.py:476` | `entity.get("policyName") or entity.get("userName") or entity.get("name")` | **极高** | 完全业务特定 |

#### 2.2.2 泛化方案

**核心问题**：当前逻辑用字段名后缀过滤（`_CONTEXT_SUFFIXES`），只有字段名匹配 `tenant`、`admin`、`role` 等后缀才尝试值匹配。这是**本末倒置**——应该直接用值去搜索前置接口响应，匹配上了自然就知道来源，不需要关心字段叫什么。

**当前逻辑（错误的）**：
```python
# 先检查字段名后缀
is_context_field = field_name.endswith('Id') or field_name.endswith('tenant')
if is_context_field:
    # 才尝试值匹配
    if field_value in pre_api_index:
        return match
```

**正确的数据驱动逻辑**：
```python
# 直接用值去前置接口响应里搜索
if field_value in pre_api_index:
    # 匹配上了，自然就知道来源
    return match
# 不需要关心字段叫什么
```

**方案 1: 删除字段名过滤，纯值驱动匹配（推荐）**

**步骤**:
1. **删除 `_CONTEXT_SUFFIXES` 门卫逻辑**
2. **策略 1（精确值匹配）**：直接用 `field_value_str` 在 `pre_api_index` 中搜索，不做字段名过滤
3. **策略 2（字段名启发式）**：保留，但作为降级方案（值匹配失败时）
4. **策略 3（列表成员检查）**：保留

**代码修改示意**：
```python
# 策略 1: 精确值匹配（最高优先级，不再检查字段名）
if field_value_str in pre_api_index:
    info = pre_api_index[field_value_str]
    api_id = info['api']['id']
    field_name_in_api = info['field']['name']
    return {
        'source': f"{api_id}.{field_name_in_api}",
        'api': info['api'],
        'strategy': 'exact_value_match',
        'is_array': is_array
    }

# 策略 2: 字段名启发式（降级方案）
# 仅当值匹配失败时，尝试字段名匹配
for value_str, info in pre_api_index.items():
    api_field_name = info['field']['name']
    if field_name.lower() == api_field_name.lower():
        return {'source': f"{api_id}.{api_field_name}", ...}
```

**优势**：
- **完全数据驱动**：值本身就是最好的标识，不需要猜测字段名规则
- **自动发现**：前置接口返回什么值，业务接口用什么值，匹配上了就知道依赖关系
- **零配置**：不需要维护后缀列表，适用于任何项目、任何字段命名

**方案 2: `COMMON_ID_FIELDS` 精简**
- 只保留通用 ID 字段（`id`, `ids`）
- 其他字段通过策略 1 自动发现

**方案 3: `field_changed` 断言泛化**
- `test_runtime.py:476` 改为从 manifest 的 `body_field_roles` 中查找 `role="name"` 的字段
- 不再硬编码 `policyName`/`userName`

#### 2.2.3 实施复杂度

**低**（删除过滤逻辑，简化代码）

#### 2.2.4 验证方式

重新生成用户管理 manifest，检查 `tenantId`、`adminId`、`policyIds` 是否正确解析为 `pre_api_ref`，即使字段名不包含 `tenant`、`admin`、`policy` 后缀。

---

### Category C: API 路径和 URL 模式（高优先级）

**问题**：代码假设特定 API 路径模式（`/estack/api`, `/users/current-user`, `/policies`）。

#### 2.3.1 发现清单

| 位置 | 硬编码值 | 风险 | 问题 |
|------|---------|------|------|
| `analyze_flow.py:1089-1098` | `"estack" in url`, `"/estack/api/estack/draco/v1/users/current-user"`, `tenantId`/`adminId` 默认值 | **极高** | estack 特定 |
| `auth.py:231` | `"/estack/api/estack/draco/v1/users/current-user"` | **极高** | 默认 probe URL |
| `request_interceptor.py:153` | `"/estack/api"` | 高 | 默认 API 前缀 |
| `run.py:263` | `f"{base_url}/estack/web/estack/login"` | 高 | 默认登录 URL |
| `const.py:148-153` | `SUPPORTING_API_KEYWORDS = ["/menu/", "/theme", "/current-user", "/role/", "/permission/"]` | 中 | 部分通用，部分 estack 特定 |

#### 2.3.2 泛化方案

**方案 1: 删除 estack 检测逻辑（推荐）**
- `analyze_flow.py:1089` 的 `"estack" in url` 检查删除
- `probe_url`, `context_fields` 改为 `profile.yaml` 必填字段，无默认值

**方案 2: API 前缀配置化**
- `profile.yaml` 增加 `api_path_prefix` 字段

**方案 3: SUPPORTING_API_KEYWORDS 拆分**
- 通用部分（`/menu/`, `/theme`, `/dictionary`）保留
- estack 特定部分（`/current-user`, `/role/`, `/permission/`）移到项目配置

**配置示例**:
```yaml
# profile.yaml
api:
  path_prefix: "/estack/api"
  supporting_keywords:
    - "/current-user"
    - "/role/"
    - "/permission/"

context:
  probe_url: "/estack/api/estack/draco/v1/users/current-user"
  fields:
    tenantId:
      path: "entity.tenantId"
    adminId:
      path: "entity.id"
```

#### 2.3.3 实施复杂度

**中**（需要修改 profile.yaml schema，但逻辑改动小）

#### 2.3.4 验证方式

创建新项目 profile.yaml，确保无默认值也能工作。

---

### Category D: UI 组件选择器（中优先级）

**问题**：代码假设使用 Element UI 或 Ant Design，CSS 选择器硬编码。

#### 2.4.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `const.py:10-32` | `BUTTON_SELECTORS`（20+ 个 Element UI/Ant Design 选择器） | 高 |
| `button_driver.py`, `form_filler.py`, `wait_helpers.py` | `.el-table__*`, `.el-dialog__*`, `.el-form-item`, `.ant-modal-*` | 高 |
| `capture_apis.py:133-145` | `.el-dialog__wrapper`, `.el-message-box__wrapper` | 高 |

#### 2.4.2 泛化方案

**方案: UI 选择器注册表（推荐）**

**架构设计**:
```
module_discovery/
├── ui_selectors/
│   ├── element-ui.json      # Element UI 选择器配置
│   ├── ant-design.json      # Ant Design 选择器配置
│   └── custom.json          # 自定义框架选择器
└── const.py                 # 从注册表动态加载
```

**配置文件示例**:
```json
{
  "framework": "element-ui",
  "button_selectors": [
    "button",
    ".el-button",
    ".el-button--text",
    ".el-dropdown-menu__item"
  ],
  "table_selectors": {
    "body_row": ".el-table__body-wrapper tbody tr",
    "fixed_row": ".el-table__fixed .el-table__fixed-body-wrapper tbody tr"
  },
  "dialog_selectors": {
    "wrapper": ".el-dialog__wrapper",
    "close": ".el-dialog__close"
  }
}
```

**加载机制**:
```python
# const.py
def load_ui_selectors(framework: str) -> dict:
    """从 ui_selectors/ 目录加载指定框架的选择器配置"""
    config_path = Path(__file__).parent / "ui_selectors" / f"{framework}.json"
    if not config_path.exists():
        raise ValueError(f"UI framework '{framework}' not supported")
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)
```

**profile.yaml 配置**:
```yaml
ui_framework: element-ui  # 或 ant-design, custom
```

#### 2.4.3 实施复杂度

**高**（需要重构 replay 模块，但架构更清晰）

#### 2.4.4 验证方式

在 Element UI 项目上运行 Stage 1-2，验证选择器加载正确。

---

### Category E: 按钮文本和 CRUD 关键词（中优先级）

**问题**：代码用固定中文关键词映射按钮文本到 CRUD 操作。

#### 2.5.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `const.py:40-64` | `ACTION_KEYWORDS`（17 个 CRUD 类别，每个 5-15 个中文关键词） | 中 |
| `const.py:158-173` | `API_CRUD_KEYWORDS`（URL 路径关键词） | 中 |
| `form_filler.py:557-580` | 提交按钮文本列表（`确定`, `保存`, `提交`, `OK`） | 中 |
| `button_driver.py:777-837` | 确认对话框按钮文本（`确定`, `确认`, `是`, `OK`） | 中 |

#### 2.5.2 泛化方案

**方案 1: 关键词扩展机制（推荐）**
- `const.py` 保留通用核心关键词
- `profile.yaml` 增加 `custom_action_keywords` 字段，项目级扩展

**配置示例**:
```yaml
# profile.yaml
action_keywords:
  create:
    - "新增"
    - "创建"
    - "添加"
    # 项目特定关键词
    - "申请"
    - "开通"
  delete:
    - "删除"
    - "移除"
    # 项目特定关键词
    - "释放"
    - "回收"
```

**方案 2: AI 辅助分类**
- 对无法匹配的按钮文本，调用 LLM 判断 CRUD 类别
- 已在 `endpoint_classifier.py` 中有类似逻辑

**方案 3: 提交按钮文本动态化**
- 改为从 UI 探测结果中读取（`ui_result.confirm_button_text`）

#### 2.5.3 实施复杂度

**低-中**（主要是配置扩展，不改核心逻辑）

---

### Category F: 认证和会话（中优先级）

**问题**：代码假设特定认证模式（cookie 名、header 名、token 存储位置）。

#### 2.6.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `auth.py:163,167`, `cookie_client.py:20,43,64` | `"accessToken"`, `"estackToken"`, `"Authorization"` | 高 |
| `cookie_client.py:125,198` | `"estackToken"` (localStorage key) | 高 |
| `auth.py:385-388` | `"点击完成认证"`, `"登录"`, `"/portal"`, `"pictures-verification"` | 高 |
| `auth.py:428-429` | `input[placeholder="用户名"]`, `input[placeholder="登录密码"]` | 高 |

#### 2.6.2 泛化方案

**方案 1: 认证配置全面外移（推荐）**
- `profile.yaml` 的 `auth` 部分已经包含大部分配置
- 删除 `"accessToken"`, `"estackToken"` 等默认值，改为必填

**方案 2: 登录流程配置化**
- `profile.yaml` 增加 `login_flow` 配置块
- 包含按钮文本、表单选择器、成功 URL 关键词等

**配置示例**:
```yaml
# profile.yaml
auth:
  type: bearer
  token_key: estackToken           # 必填
  token_storage: localStorage      # 必填
  cookie_token_key: accessToken    # 必填
  header_name: Authorization
  header_prefix: "Bearer "

login_flow:
  button_text: "登录"
  username_selector: 'input[placeholder="用户名"]'
  password_selector: 'input[placeholder="登录密码"]'
  success_url_keyword: "/portal"
  captcha:
    type: slider
    selector: "#slideVerify"
    auth_button_text: "点击完成认证"
    material_url_regex: "pictures-verification|images/checkcap/code"
```

**方案 3: 多认证模式支持**
- 当前只支持 cookie-based
- 未来可能需要 OAuth2/OIDC/SAML
- 抽象 `AuthProvider` 接口，不同实现（CookieAuth, OAuth2Auth）

#### 2.6.3 实施复杂度

**中**（需要重构 auth 模块，但 profile.yaml 已有基础）

---

### Category G: 测试数据生成（低优先级）

**问题**：代码用固定规则生成测试数据（密码、邮箱、手机号）。

#### 2.7.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `form_filler.py:372,409,1049,1065` | `"Test@123456"` | 中 |
| `form_filler.py:1041` | `"test.com"` (邮箱域名) | 低 |
| `form_filler.py:1045` | `"138"` (手机号前缀) | 低 |
| `test_runtime.py:908-912` | `"AT_"` 前缀 | 低 |

#### 2.7.2 泛化方案

**方案: 测试数据配置化（推荐）**

**配置示例**:
```yaml
# profile.yaml
test_data:
  password: "Test@123456"
  email_domain: "test.com"
  phone_prefix: "138"
  name_prefix: "AT_"
```

#### 2.7.3 实施复杂度

**低**（纯配置扩展）

---

### Category H: CRUD 执行顺序和依赖（低优先级）

**问题**：代码假设固定 CRUD 执行顺序和依赖关系。

#### 2.8.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `const.py:67-81` | `CRUD_EXECUTION_ORDER`（13 个类别固定顺序） | 低 |
| `analyze_flow.py:438-453` | 依赖图规则（`query/update/lock` depend on `create`） | 低 |
| `analyze_flow.py:1439` | 写操作列表 `("create", "update", "delete", "lock", "unlock", "reset")` | 低 |

#### 2.8.2 泛化方案

**方案: 依赖规则配置化（可选）**
- `profile.yaml` 增加 `crud_dependencies` 配置块
- 允许项目级覆盖依赖关系
- **当前保持硬编码**：这些规则相对通用，暂不需要泛化

#### 2.8.3 实施复杂度

**低**（当前可不改动）

---

### Category I: 错误分类和失败检测（低优先级）

**问题**：代码用关键词匹配分类测试失败原因。

#### 2.9.1 发现清单

| 位置 | 硬编码值 | 风险 |
|------|---------|------|
| `run_history.py:40-60` | `ENV_RE`, `PRODUCT_RE`, `SCRIPT_RE`, `FLAKY_RE`（中文/英文关键词正则） | 中 |
| `run.py:630-631` | `"HTTP 401"`, `"Token 已过期"` | 低 |

#### 2.9.2 泛化方案

**方案 1: 错误分类配置化（推荐）**
- 创建 `config/failure_patterns.yaml` 文件
- 包含各类失败模式的正则

**配置示例**:
```yaml
# config/failure_patterns.yaml
env:
  - '配额|quota|未开通|证书配额|资源不足|欠费|余额'
  - '第二项目|跨项目|文件存储|服务未开通|环境限制'
product:
  - '服务端(异常|错误)|系统异常|后台报错'
  - '接口.*(报错|失败|异常)|API.*(error|fail)'
script:
  - 'Assignment to constant|重新赋值|定位失败'
  - 'TypeError|ReferenceError|SyntaxError'
flaky:
  - '偶发|偶爾|flaky|网络波动|波动'
```

**方案 2: AI 辅助分类**
- 对无法匹配的错误信息，调用 LLM 判断失败类别

#### 2.9.3 实施复杂度

**低**（配置扩展）

---

## 3. 优先级排序

| 优先级 | Category | 影响范围 | 实施复杂度 | 建议时间 |
|--------|----------|---------|-----------|---------|
| **P0** | A: 响应结构假设 | 所有模块 | 低 | 1 天 |
| **P0** | B: 字段名匹配 | 所有模块 | 中 | 2 天 |
| **P1** | C: API 路径和 URL | 所有新项目 | 中 | 2 天 |
| **P1** | D: UI 组件选择器 | 非 Element/Ant 项目 | 高 | 3 天 |
| **P2** | E: 按钮文本和 CRUD | 新模块扩展 | 低-中 | 1 天 |
| **P2** | F: 认证和会话 | 非 estack 项目 | 中 | 2 天 |
| **P3** | G: 测试数据生成 | 密码策略严格的项目 | 低 | 0.5 天 |
| **P3** | H: CRUD 执行顺序 | 特殊业务流程 | 低 | 0.5 天 |
| **P3** | I: 错误分类 | 新错误模式 | 低 | 0.5 天 |

**总计**: 约 12 天

---

## 4. 实施建议

### Phase 1: 快速修复（P0，3 天）

**目标**: 消除最明显的业务特定硬编码，确保新模块可用。

1. **修复响应结构不一致**（Category A）
   - `analyze_flow.py:83,1182,272` 改用 `response_contract`
   - 验证：重新生成用户管理 manifest，检查 envelope_keys 是否正确使用

2. **重构 `_CONTEXT_SUFFIXES`**（Category B）
   - 删除业务特定后缀，保留通用后缀
   - 改为从 `profile.yaml` 读取
   - 验证：重新生成用户管理 manifest，检查 pre_api_ref 是否正确

3. **泛化 `field_changed` 断言**（Category B）
   - `test_runtime.py:476` 改为从 manifest 读取 name 字段
   - 验证：运行用户管理测试，检查 update 验证是否通过

### Phase 2: 配置化改造（P1，5 天）

**目标**: 支持非 estack 项目和非 Element UI 项目。

4. **删除 estack 特定默认值**（Category C）
   - `probe_url`, `context_fields` 改为必填
   - 删除 `"estack" in url` 检测逻辑
   - 验证：创建新项目 profile.yaml，确保无默认值也能工作

5. **UI 选择器注册表**（Category D）
   - 创建 `module_discovery/ui_selectors/` 目录
   - 为 Element UI 和 Ant Design 创建配置文件
   - 重构 replay 模块，接受 selector config
   - 验证：在 Element UI 项目上运行 Stage 1-2

6. **按钮文本扩展机制**（Category E）
   - `profile.yaml` 增加 `custom_action_keywords`
   - 验证：添加自定义关键词，检查按钮分类是否正确

### Phase 3: 认证和测试数据（P2-P3，4 天）

**目标**: 支持多种认证模式和测试数据策略。

7. **认证配置全面外移**（Category F）
   - 删除 `"accessToken"`, `"estackToken"` 默认值
   - `profile.yaml` 增加 `login_flow` 配置块
   - 验证：在非 estack 项目上运行 Stage 1

8. **测试数据配置化**（Category G）
   - `profile.yaml` 增加 `test_data` 配置块
   - 验证：修改密码策略，检查生成数据是否符合

9. **错误分类配置化**（Category I）
   - 创建 `config/failure_patterns.yaml`
   - 验证：添加新错误模式，检查分类是否正确

---

## 5. 关键文件清单

| 文件 | 改动类型 | 涉及 Category |
|------|----------|--------------|
| `module_discovery/analyze_flow.py` | 修改 | A, B, C |
| `module_discovery/const.py` | 修改 | B, D, E |
| `module_discovery/run.py` | 修改 | C |
| `lib/runtime/test_runtime.py` | 修改 | A, B |
| `lib/auth/auth.py` | 修改 | F |
| `lib/auth/cookie_client.py` | 修改 | F |
| `module_discovery/replay/button_driver.py` | 修改 | D |
| `module_discovery/replay/form_filler.py` | 修改 | D, E, G |
| `module_discovery/replay/wait_helpers.py` | 修改 | D |
| `module_discovery/capture_apis.py` | 修改 | D |
| `module_discovery/ui_selectors/element-ui.json` | **新增** | D |
| `module_discovery/ui_selectors/ant-design.json` | **新增** | D |
| `config/failure_patterns.yaml` | **新增** | I |
| `projects/*/profile.yaml` | 修改 | C, F, G |

---

## 6. 风险和缓解

| 风险 | 缓解措施 |
|------|---------|
| 删除默认值导致现有项目失败 | 所有 profile.yaml 同步更新，添加必填字段 |
| UI 选择器重构导致 replay 失败 | 先在 Element UI 项目验证，再推广到其他框架 |
| 配置复杂度增加 | 提供 `profile.example.yaml` 模板，包含详细注释 |
| 向后兼容问题 | manifest_version 升级到 1.3，旧版本仍支持 |

---

## 7. 验证策略

每个 Phase 完成后，运行以下验证：

1. **单元测试**: `python -m pytest tests/ --ignore=tests/test_feedback_loop.py --ignore=tests/test_run_history.py -v`
2. **用户管理端到端**: `python -m module_discovery.run --project ecm-compute --stage 34 --module 用户管理 --url "..." --offline`
3. **新项目验证**（Phase 2 后）: 创建一个非 estack 项目的 profile.yaml，运行 Stage 1-4

---

## 8. 讨论要点

### 8.1 优先级确认

- P0（A/B）是否必须立即实施？
- P1（C/D）是否可以延后？
- 哪些 Category 对当前项目最关键？

### 8.2 配置复杂度

- 增加配置项是否会导致使用门槛提高？
- 是否需要提供 `profile.example.yaml` 模板？
- 是否需要配置验证工具？

### 8.3 向后兼容

- 是否需要支持旧版 profile.yaml？
- manifest_version 升级到 1.3 是否有风险？

### 8.4 实施范围

- 是否一次性实施所有 Category？
- 还是分阶段实施，每阶段验证后再继续？

---

## 9. 下一步

1. 团队讨论本文档，确认优先级和实施范围
2. 根据讨论结果调整实施计划
3. 开始 Phase 1 实施（如确认 P0）
4. 每个 Phase 完成后验证，根据结果调整后续 Phase

---

## 附录 A: 完整硬编码清单

详见三个 Explore agent 的完整输出（已保存在对话历史中）。

## 附录 B: profile.yaml 完整配置示例

```yaml
# profile.example.yaml — 完整配置示例

name: my-project
display_name: "My Project"
ui_framework: element-ui  # element-ui | ant-design | custom

# ===== 入口与 API 命名空间 =====
base_url: "https://example.com"
web_entry: "/web/dashboard"
login_url: "https://example.com/login"
api_base: "/api"
api_base_full: "https://example.com/api"

# ===== 鉴权 =====
auth:
  type: bearer
  token_key: accessToken           # 必填
  token_storage: localStorage      # 必填
  cookie_token_key: accessToken    # 必填
  header_name: Authorization
  header_prefix: "Bearer "
  freshness_ttl_seconds: 1800
  fixed_headers:
    Language: "zh-CN"

# ===== 登录流程 =====
login_flow:
  button_text: "登录"
  username_selector: 'input[name="username"]'
  password_selector: 'input[name="password"]'
  success_url_keyword: "/dashboard"
  captcha:
    type: slider
    selector: "#captcha"
    auth_button_text: "完成验证"
    material_url_regex: "captcha-material"

# ===== 上下文配置 =====
context:
  probe_url: "/api/v1/users/current"
  fields:
    tenantId:
      path: "entity.tenantId"
    userId:
      path: "entity.id"

# ===== 业务服务前缀 =====
service_prefixes:
  - user
  - order
  - product

# ===== 入口页配置 =====
menu:
  menu_landing: "/web/dashboard"
  api_endpoints: []
  dom_menu_selector: ".menu, .submenu, .menu-item"

# ===== 凭据 =====
credentials:
  username_env: MY_PROJECT_USER
  password_env: MY_PROJECT_PASS
  username: "test_user"
  password: "Test@123456"
  role: "管理员"

# ===== 安全护栏 =====
guardrails:
  resource_prefix: "AT_"
  allow_write: true
  danger_blacklist:
    - "*delete*"
    - "*remove*"

# ===== 自定义配置 =====
context_field_suffixes:
  generic: ['id', 'Id', 'code', 'Code', 'key', 'Key']
  project: ['tenant', 'org', 'dept']

action_keywords:
  create:
    - "新增"
    - "创建"
  delete:
    - "删除"
    - "移除"

test_data:
  password: "Test@123456"
  email_domain: "test.com"
  phone_prefix: "138"
  name_prefix: "AT_"

api:
  path_prefix: "/api"
  supporting_keywords:
    - "/menu/"
    - "/theme"
```
