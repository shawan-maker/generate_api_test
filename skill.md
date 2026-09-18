# Skill: API 自动化测试发现系统

## 元信息

- **名称**: api-ai-test
- **版本**: 2.0.0
- **用途**: 自动化发现、生成和执行 API 测试
- **平台**: 通用（Claude / GPT / 其他 AI 客户端）
- **触发词**: api, test, 自动化, 发现, 生成, 执行, 测试, 用户管理, 角色管理

---

## 能力描述

本系统实现从 UI 探测到 API 测试脚本生成的完整自动化流水线，支持多模块批量处理和增量发现。

### 核心功能

1. **Stage 1-5 全流程** — 从 UI 探测到测试脚本生成的完整流水线
2. **5 角色分类系统** — name/mutable/context/generate/static 智能分类
3. **值匹配驱动** — 基于值的精确匹配 + 字段名回退匹配
4. **前置 API 追踪** — 自动识别和排序依赖的前置 API
5. **多格式导出** — Postman Collection / helpers.py / Excel 参数文件
6. **增量发现** — 7 天内已发现的模块自动跳过

---

## 使用方法

### 单模块完整流程

```bash
# Stage 1-5 全流程
python -m core.discovery.run --project <project_id> \
  --module <模块名> \
  --url <模块URL路径> \
  --stage all

# 示例：用户管理模块
python -m core.discovery.run --project ecm-compute \
  --module 用户管理 \
  --url /estack/web/estack/user-center/user-manage/user \
  --stage all
```

### 分阶段执行

```bash
# 只运行 Stage 3/4（分析和生成）
python -m core.discovery.run --project ecm-compute \
  --module 用户管理 \
  --url /estack/web/estack/user-center/user-manage/user \
  --stage 34

# 离线模式（使用已有的捕获数据）
python -m core.discovery.run --project ecm-compute \
  --module 用户管理 \
  --url /estack/web/estack/user-center/user-manage/user \
  --stage 34 --offline --headless

# Stage 5 导出
python -m core.discovery.run --project ecm-compute \
  --module 用户管理 \
  --url /estack/web/estack/user-center/user-manage/user \
  --stage 5
```

### 批量处理

```bash
# 处理 modules.yaml 中的所有模块
python -m core.discovery.run --project ecm-compute --all-modules

# 只处理特定标签的模块
python -m core.discovery.run --project ecm-compute --all-modules --tag 用户中心

# 强制重新发现（忽略增量检查）
python -m core.discovery.run --project ecm-compute --all-modules --force
```

### 执行生成的测试

```bash
# 运行 API 测试脚本
python projects/ecm-compute/v1.0.0/api/用户管理_API测试.py

# 使用 pytest 运行
python -m pytest projects/ecm-compute/v1.0.0/api/用户管理_API测试.py -v
```

---

## 5 角色分类系统

### 角色定义

| 角色 | 说明 | 示例 |
|------|------|------|
| **name** | 用户可读名称字段 | username, name, label |
| **mutable** | 可变字段（update 时修改） | description, remark, memo |
| **context** | 上下文引用（从前置 API 获取） | tenantId, policyIds |
| **generate** | 动态生成的测试值 | phone, email, uuid |
| **static** | 系统固定值 | state, isRandomPassword |

### 分类优先级

1. **Pass 1: name** — 字段名含 name/title/label + 值是短可读字符串
2. **Pass 2: mutable** — 字段名含 description/memo/remark/note/content
3. **Pass 3a: context** — 精确值匹配（在 value_index 中找到）
4. **Pass 3b: context** — 字段名回退（系统 ID 类型 + 字段名匹配）
5. **Pass 4: generate** — 值模式分析（uuid、hex_id、phone、email）
6. **Pass 5: static** — 兜底

---

## 项目结构

```
API_AI_test/
├── core/
│   ├── discovery/          # 主管线引擎（Stage 1-5）
│   │   ├── run.py         # CLI 入口
│   │   ├── analyze_flow.py # Stage 3: 流程分析（5 角色分类）
│   │   ├── gen_test.py    # Stage 4: 测试脚本生成
│   │   └── export_artifacts.py # Stage 5: 导出
│   └── generators/        # 代码生成器
├── lib/
│   ├── runtime/           # 运行时库
│   │   └── test_runtime.py # 5 角色运行时处理
│   └── auth/              # 认证模块
├── projects/              # 项目数据（每个子目录是一个被测系统）
│   └── <project_id>/
│       ├── modules.yaml   # 模块清单
│       ├── profile.yaml   # 项目配置
│       └── v1.0.0/        # 版本化输出
│           ├── api/       # 生成的 API 测试脚本
│           └── export/    # Stage 5 导出文件
└── workspace/             # 中间产物（不上传 git）
    └── <project_id>/
        └── kb/module_discovered/ # KB 中间数据
```

---

## 关键特性

### 1. 值匹配驱动

- **精确值匹配**：在前置 API 响应中查找相同的值
- **字段名回退**：对系统 ID 类型（hex_id、uuid、alphanumeric ID），按字段名在前置 API 中查找
- **归一化匹配**：`tenant_id` 和 `tenantId` 可以正确匹配

### 2. 前置 API 自动追踪

- 自动识别业务操作依赖的前置 API
- 拓扑排序确保执行顺序正确
- 链式依赖追踪（前置 API 的参数也可能需要其他前置 API）

### 3. 增量发现

- 7 天内已发现的模块自动跳过（除非 `--force`）
- 基于 `kb/module_discovered/<name>.json` 的 mtime 检查
- 支持 `--force` 强制重新发现

### 4. 多格式导出

- **Postman Collection** — 可直接导入 Postman
- **helpers.py** — 高层辅助函数（供外部测试平台使用）
- **Excel 参数文件** — 变量配置表（e_ 前缀，敏感标记）

---

## 配置说明

### modules.yaml

```yaml
modules:
  - name: 用户管理
    url: /estack/web/estack/user-center/user-manage/user
    enabled: true
    tags: [用户中心, 基础]
    priority: 1

  - name: 角色管理
    url: /estack/web/estack/user-center/user-manage/role
    enabled: true
    tags: [用户中心, 权限]
    priority: 2
```

### profile.yaml

```yaml
base_url: https://example.com
login_url: https://example.com/login
auth:
  cookie_token_key: accessToken
  header_name: Authorization
  header_prefix: "Bearer "
```

---

## 常见问题

### Q: 认证失败怎么办？

A: 重新登录生成 cookies.json：
```bash
python -m lib.auth.login_tool --project ecm-compute
```

### Q: 如何跳过浏览器阶段？

A: 使用 `--offline` 参数（需要有已捕获的数据）：
```bash
python -m core.discovery.run --project ecm-compute --module 用户管理 --stage 34 --offline
```

### Q: 如何查看详细的日志？

A: 所有日志输出到 `projects/<project>/output/logs/` 目录

### Q: 生成的脚本在哪里？

A: `projects/<project_id>/v1.0.0/api/<模块名>_API测试.py`

---

## 技术栈

- **Python 3.8+**
- **Playwright** — UI 探测和 API 捕获
- **pytest** — 测试执行框架
- **openpyxl** — Excel 导出

---

## 更新日志

### v2.0.0 (2026-09-18)

- ✅ 9 角色简化为 5 角色（name/mutable/context/generate/static）
- ✅ 值匹配驱动的分类系统
- ✅ 字段名回退匹配（支持系统 ID 类型）
- ✅ 通配符路径支持（`[*]`）
- ✅ 归一化字段名匹配（tenant_id == tenantId）

### v1.0.0 (2026-09-01)

- 初始版本：Stage 1-5 完整流水线
- KB 驱动优化
- Playbook 架构
