# Skill: API 自动化发现主管线

## 元信息

- **名称**: pipeline
- **版本**: 1.0.0
- **用途**: 运行 API 自动化发现主管线（Stage 1-5）
- **平台**: 通用（Claude / GPT / 其他 AI 客户端）
- **触发词**: pipeline, 主管线, 发现流程, stage, 导出, 批量

---

## 能力描述

当用户要求执行 API 自动化发现流程时，根据提供的参数调用相应的 Python 命令。

### 支持的操作

1. **全流程执行** — 单模块 Stage 1-5
2. **分阶段执行** — 只运行指定阶段
3. **批量处理** — 处理 modules.yaml 中的所有模块
4. **离线分析** — 跳过浏览器阶段
5. **导出 artifacts** — Stage 5 独立导出

---

## 阶段说明

| 阶段 | 名称 | 说明 | 需要浏览器 |
|------|------|------|-----------|
| Stage 1 | UI 探测 | 发现页面按钮/表单 | ✅ |
| Stage 2 | API 捕获 | 拦截操作对应的 API | ✅ |
| Stage 3+4 | 分析+生成 | 构建 manifest + 生成测试脚本 | ❌ |
| Stage 5 | 导出 | Postman/Excel/helpers | ❌ |

---

## 命令模板

### 1. 全流程（所有阶段）

```bash
python -m module_discovery.run \
    --project {{project}} \
    --url "{{url}}" \
    --module "{{module}}" \
    --user {{user}} \
    --pass "{{password}}" \
    --headless
```

**参数说明**:
- `{{project}}` — 项目 ID（projects/ 下的目录名）
- `{{url}}` — 模块入口 URL 路径
- `{{module}}` — 模块名称（中文）
- `{{user}}` — 登录用户名
- `{{password}}` — 登录密码

### 2. 分阶段执行

```bash
# Stage 1: UI 探测
python -m module_discovery.run --project {{project}} --url "{{url}}" --module "{{module}}" --stage 1

# Stage 2: API 捕获
python -m module_discovery.run --project {{project}} --url "{{url}}" --module "{{module}}" --stage 2

# Stage 3+4: 分析 + 生成脚本（离线）
python -m module_discovery.run --project {{project}} --module "{{module}}" --stage 34 --offline

# Stage 5: 导出 artifacts（离线）
python -m module_discovery.run --project {{project}} --module "{{module}}" --stage 5
```

### 3. 批量处理所有模块

```bash
# 处理 modules.yaml 中的所有模块
python -m module_discovery.run --project {{project}} --all-modules --user {{user}} --pass "{{password}}" --headless

# 批量 + 指定阶段
python -m module_discovery.run --project {{project}} --all-modules --stage 34

# 按标签过滤
python -m module_discovery.run --project {{project}} --all-modules --tag "用户,权限"

# 强制重新发现
python -m module_discovery.run --project {{project}} --all-modules --force
```

### 4. 并行测试执行

```bash
# 并行运行所有已生成的测试脚本
python -m module_discovery.run_parallel --project {{project}}

# 指定版本和并发数
python -m module_discovery.run_parallel --project {{project}} --version v1.0.0 --parallel 6

# 只运行指定模块
python -m module_discovery.run_parallel --project {{project}} --module "{{module}}"
```

### 5. 测试套件运行

```bash
python run_suite.py --project {{project}}
```

### 6. 独立登录工具

```bash
python -m lib.auth.login_tool --project {{project}}
```

---

## 参数参考

| 参数 | 必填 | 说明 |
|------|------|------|
| --project | ✅ | 项目 ID（projects/ 下的目录名） |
| --url | Stage 1-2 | 模块入口 URL 路径 |
| --module | 单模块模式 | 模块名称（中文，如"用户管理"） |
| --user | 在线模式 | 登录用户名 |
| --pass | 在线模式 | 登录密码 |
| --stage | 否 | 1/2/34/4/5/all（默认 all） |
| --headless | 否 | 无头浏览器模式 |
| --offline | 否 | 离线模式（跳过 Stage 1-2） |
| --all-modules | 否 | 批量处理 modules.yaml 中的模块 |
| --force | 否 | 强制重新发现 |
| --tag | 否 | 按标签过滤模块（逗号分隔） |
| --version | 否 | 脚本版本号（默认 v1.0.0） |
| --force-gen | 否 | Stage 2 验证失败仍强制生成脚本 |
| --max-recapture | 否 | Stage 2 最大重试捕获次数 |
| --capture-all | 否 | 捕获所有 XHR/fetch（不限于 /estack/api） |
| --export | 否 | Stage 4 验证通过后导出 artifacts |

---

## 项目结构

```
projects/{{project}}/
├── profile.yaml          # 项目配置
├── modules.yaml          # 模块清单（批量处理用）
├── kb/
│   └── module_discovered/
│       ├── {{module}}_ui.json
│       ├── {{module}}_capture.json
│       ├── {{module}}_manifest.json
│       └── {{module}}_API测试.py
└── output/
    ├── logs/
    └── api/{{module}}/{{version}}/
        ├── {{module}}.postman_collection.json
        ├── helpers.py
        └── {{module}}_params.xlsx
```

---

## 注意事项

1. **Stage 5 可独立运行** — 无需浏览器，从磁盘加载 manifest 导出
2. **`--stage all`** — 执行全部阶段（含 Stage 5 导出）
3. **`--stage 34 --offline`** — 离线重新分析生成（不需要浏览器）
4. **增量发现** — 7 天内已发现的模块自动跳过（除非 --force）
5. **生成脚本包** — 在 `projects/{{project}}/scripts/v1.0.0/` 下，可拷贝独立运行

---

## 示例对话

**用户**: 运行用户管理模块的全流程

**AI**: 
```bash
python -m module_discovery.run --project ecm-compute --url "/estack/web/estack/user-center/user-manage/user" --module "用户管理" --user admin --pass "password123" --headless
```

**用户**: 导出用户管理的 Postman Collection

**AI**:
```bash
python -m module_discovery.run --project ecm-compute --module "用户管理" --stage 5
```

**用户**: 批量处理所有模块

**AI**:
```bash
python -m module_discovery.run --project ecm-compute --all-modules --user admin --pass "password123" --headless
```

---

## 辅助工具

```bash
# 独立登录工具（获取 cookie）
python -m lib.auth.login_tool --project {{project}}

# P0 爬虫（发现入口点）
python -m discovery.playwright_crawl --project {{project}}
```

---

## 文档链接

- 完整项目指南: `docs/PROJECT_GUIDE.md`
- 项目说明: `CLAUDE.md`
