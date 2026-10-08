# Skill: API 自动化测试发现系统

## 元信息

- **名称**: api-ai-test
- **版本**: 2.3.0
- **用途**: 自动化发现、生成和执行 API/UI 测试
- **平台**: 通用（Claude / GPT / 其他 AI 客户端）
- **触发词**: api, test, 自动化, 发现, 生成, 执行, 测试, 用户管理, 角色管理

---

## 能力概述

给定 Web 后台模块入口 URL + 登录凭证，自动完成五阶段流水线：

| 阶段 | 说明 | 需要浏览器 |
|------|------|-----------|
| Stage 1 | UI 元素探测 + Playbook 生成 | ✅ |
| Stage 2 | API 拦截捕获 + UI 脚本生成 | ✅ |
| Stage 3 | 逻辑分析 + Manifest 构建 | ❌ |
| Stage 4 | API 测试脚本生成 + 验证运行 | ❌ |
| Stage 5 | Postman / helpers.py / Excel 导出 | ❌ |

核心能力：
1. **五阶段全流程** — UI 探测 → API 捕获 → 逻辑分析 → 脚本生成 → 导出
2. **Playbook + Manifest 双驱动** — Stage 1 生成可回放操作指令集，Stage 3 生成 API 测试清单
3. **导航自动发现** — 爬取侧边栏菜单，自动识别所有模块，批量处理
4. **增量发现** — 7 天内已发现的模块自动跳过
5. **多格式导出** — Postman Collection / helpers.py / Excel 参数文件

---

## 使用方法

### 典型工作流：新项目首次发现

```bash
# 1. 探测所有模块（爬取菜单，收集模块名称 + URL）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless

# 2. 从缓存生成 modules.yaml（供后续批量使用）
python -m core.discovery.run --project <项目名> --export-modules

# 3. 选择模块执行完整管线
python -m core.discovery.run --project <项目名> --discover-select "1,3,5" --headless
# 或选择所有模块
python -m core.discovery.run --project <项目名> --discover-select "all" --headless
```

### 单模块全流程

```bash
python -m core.discovery.run --project <项目名> \
  --module <模块名> \
  --url <模块URL路径> \
  --stage all
```

### 分阶段执行

```bash
# Stage 1: UI 探测
python -m core.discovery.run --project <项目名> --module <模块名> --url <路径> --stage 1

# Stage 2: API 捕获
python -m core.discovery.run --project <项目名> --module <模块名> --url <路径> --stage 2

# Stage 3+4: 分析 + 生成（离线，不需要浏览器）
python -m core.discovery.run --project <项目名> --module <模块名> --stage 34 --offline

# Stage 5: 导出（不需要浏览器）
python -m core.discovery.run --project <项目名> --module <模块名> --stage 5
```

### 导航发现模式（两阶段）

```bash
# 阶段 1: 探测菜单，输出模块列表
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless

# 输出 JSON 格式（适合 AI 客户端解析）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<URL>" --headless --output-format json

# 强制重新探测（忽略缓存）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<URL>" --headless --force

# 阶段 2: 从缓存选择模块执行
python -m core.discovery.run --project <项目名> --discover-select "1,3,5" --headless
python -m core.discovery.run --project <项目名> --discover-select "all" --headless

# 一体化交互模式（探测 + 选择 + 执行，一步完成）
python -m core.discovery.run --project <项目名> --discover --headless
```

### 批量处理

```bash
# 处理 modules.yaml 中的所有模块（7 天内已完成的自动跳过）
python -m core.discovery.run --project <项目名> --all-modules --headless

# 按标签过滤
python -m core.discovery.run --project <项目名> --all-modules --tag "用户中心,权限" --headless

# 强制重新发现（忽略增量缓存）
python -m core.discovery.run --project <项目名> --all-modules --force --headless
```

### 并行测试执行

```bash
# 并行运行所有已生成的测试脚本
python -m core.discovery.run_parallel --project <项目名>

# 指定版本和并发数
python -m core.discovery.run_parallel --project <项目名> --version v1.0.0 --parallel 6
```

### 运行生成的 UI 测试脚本

```bash
python projects/<项目名>/v1.0.0/ui/<模块名>.py
python projects/<项目名>/v1.0.0/ui/<模块名>.py --headless
python projects/<项目名>/v1.0.0/ui/<模块名>.py --operation "创建用户"
```

---

## ⚠️ AI 行为约束（必须遵守）

1. 执行 `--discover-only` 后，**必须**将模块列表展示给用户
2. **等待用户选择**要执行的模块（输入编号如 `"1,3,5"` 或 `"all"`）
3. **禁止**自动拼接 `--all-modules` 或 `--discover-select "all"`
4. 用户明确说"全部重新扫描"/"重新扫描所有模块"时，可使用 `--all-modules --force`

---

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--project` | ✅ | 项目 ID（projects/ 下的目录名） |
| `--module` | 单模块模式 | 模块名称（中文，如"用户管理"） |
| `--url` | Stage 1-2 | 模块入口 URL 路径 |
| `--user` / `--pass` | 在线模式 | 登录用户名 / 密码 |
| `--stage` | 否 | `1` / `2` / `34` / `4` / `5` / `all`（默认 `all`） |
| `--headless` | 否 | 无头浏览器模式 |
| `--offline` | 否 | 离线模式（跳过 Stage 1-2，从缓存加载） |
| `--force` | 否 | 强制重新发现（忽略 7 天增量缓存） |
| `--force-gen` | 否 | Stage 2 验证失败仍强制生成脚本 |
| `--all-modules` | 否 | 批量处理 `modules.yaml` 中所有模块 |
| `--tag` | 否 | 按标签过滤模块（逗号分隔） |
| `--version` | 否 | 脚本版本号（默认 `v1.0.0`） |
| `--max-recapture` | 否 | Stage 2 最大重试捕获次数 |
| `--capture-all` | 否 | 捕获所有 XHR/fetch（不限于配置的 api 路径前缀） |
| `--export` | 否 | Stage 4 验证通过后自动导出 artifacts |
| `--no-run` | 否 | 跳过脚本生成后的自动运行验证 |
| **导航发现相关** | | |
| `--discover` | 否 | 一体化发现模式（探测 + 交互选择 + 执行） |
| `--discover-only` | 否 | 仅探测菜单，输出模块列表后退出 |
| `--discover-select` | 否 | 从缓存选择模块执行（如 `"1,3,5"` 或 `"all"`） |
| `--home-url` | 否 | 导航发现的主页面 URL（避免交互式输入） |
| `--output-format` | 否 | 输出格式：`text` / `json`（默认 `text`，AI 客户端用 `json`） |
| `--export-modules` | 否 | 从导航发现缓存生成 `modules.yaml` |

---

## 输出位置

| 产物 | 路径 |
|------|------|
| API 测试脚本 | `projects/<项目名>/<版本>/api/<模块名>_API测试.py` |
| UI 测试脚本 | `projects/<项目名>/<版本>/ui/<模块名>.py` |
| Stage 5 导出 | `projects/<项目名>/<版本>/export/<模块名>/` |
| 运行历史 & 报告 | `projects/<项目名>/<版本>/api/report/` |
| 中间数据 (KB) | `workspace/<项目名>/kb/module_discovered/` |

---

## 常见问题

### Q: 认证失败怎么办？
重新运行 Stage 1-2 会自动登录并刷新 cookie。或手动编辑 `config/cookies.json`。

### Q: 如何跳过浏览器阶段？
使用 `--stage 34 --offline`（需已有缓存数据）。

### Q: Stage 2 验证失败怎么办？
使用 `--force-gen` 强制生成脚本，或 `--max-recapture` 增加重试次数。

### Q: 生成的脚本如何独立运行？
脚本包在 `projects/<项目名>/<版本>/` 下，需确保 `config/cookies.json` 有效：
```bash
cd projects/<项目名>/<版本>/api && python <模块名>_API测试.py
```

### Q: 如何导出 Postman Collection？
使用 `--stage 5`，输出在 `projects/<项目名>/<版本>/export/<模块名>/`。

### Q: 如何查看日志？
日志输出到 `projects/<项目名>/output/logs/`。

---

## 技术栈

Python 3.9+ / Playwright / pytest / httpx / opencv-python-headless / pyyaml / jsonpath-ng / openpyxl

---

## 相关文档

- 项目实现详解：`docs/PROJECT_GUIDE.md`
- 五阶段引擎设计：`docs/design/stage-improvement.md`
- Claude Code 操作指南：`.claude/commands/api-ai-test.md`
