# 主管线操作指南

## 用途
运行 API 自动化发现主管线（Stage 1-5）。

## 阶段说明

### 发现流程（run.py）
- **Stage 1**：UI 元素探测（discover_ui）— 需要浏览器
- **Stage 2**：API 捕获（capture_apis）+ UI 脚本生成 — 需要浏览器
- **Stage 3+4**：流程分析 + 测试脚本生成（analyze_flow + gen_test）— 离线可运行

### 执行测试（run_parallel.py）
- **运行脚本**：批量执行 API/UI 测试脚本（支持并行）
- **Stage 5**：导出 Postman Collection / helpers.py / Excel 参数文件 — 离线可运行
- **报告生成**：自动生成汇总报告（API/UI 各一份）

## 典型工作流

### 新项目首次发现
```bash
# 1. 探测所有模块（爬取侧边栏菜单，收集模块名称+URL）
python -m core.discovery.run --project <项目名> --discover-only --home-url "<带菜单的页面URL>" --headless

# 2. 从缓存生成 modules.yaml（仅在文件不存在时生成）
python -m core.discovery.run --project <项目名> --export-modules

# 3. 选择模块执行管线
python -m core.discovery.run --project <项目名> --discover-select "1,3,5" --headless

# 或者：选择所有模块
python -m core.discovery.run --project <项目名> --discover-select "all" --headless
```

### 日常批量处理
```bash
# 批量处理所有已配置模块（读 modules.yaml，7天内已完成的自动跳过）
python -m core.discovery.run --project <项目名> --all-modules --headless

# 按标签过滤
python -m core.discovery.run --project <项目名> --all-modules --tag "访问控制" --headless

# 强制重跑（忽略增量缓存）
python -m core.discovery.run --project <项目名> --all-modules --force --headless
```

### 运行生成的测试
```bash
# 并行运行所有已生成的 API 测试脚本（默认）
python -m core.discovery.run_parallel --project <项目名>

# 并行运行所有已生成的 UI 测试脚本
python -m core.discovery.run_parallel --project <项目名> --type ui

# 指定版本和并发数
python -m core.discovery.run_parallel --project <项目名> --version v1.0.0 --parallel 6

# 只运行指定模块
python -m core.discovery.run_parallel --project <项目名> --module "用户管理"

# 无头模式运行（不打开浏览器）
python -m core.discovery.run_parallel --project <项目名> --type ui --headless

# 运行测试并导出 artifacts
python -m core.discovery.run_parallel --project <项目名> --type api --export
```

**说明：**
- 默认运行 API 测试，使用 `--type ui` 切换为 UI 测试
- 默认打开浏览器，使用 `--headless` 切换为无头模式
- 运行完成后自动生成汇总报告（API/UI 各一份）
- 汇总报告包含所有模块，按 group → module 两级结构组织

## 常用命令

### 全流程（所有阶段）
```bash
python -m core.discovery.run --project <项目名> --url "<模块URL路径>" --module "<模块名>" --user <用户名> --pass "<密码>" --headless
```

### 分阶段执行
```bash
# Stage 1：UI 探测
python -m core.discovery.run --project <项目名> --url "<模块URL>" --module "<模块名>" --stage 1

# Stage 2：API 捕获
python -m core.discovery.run --project <项目名> --url "<模块URL>" --module "<模块名>" --stage 2

# Stage 3+4：分析 + 生成脚本（离线，不需要浏览器）
python -m core.discovery.run --project <项目名> --module "<模块名>" --stage 34 --offline

# Stage 5：导出 artifacts（不需要浏览器，从磁盘加载 manifest）
python -m core.discovery.run --project <项目名> --module "<模块名>" --stage 5
```

### 导航自动发现模式
分两阶段执行，适合 AI 客户端或脚本化场景。

**阶段 1：探测菜单，输出模块列表**
```bash
# 仅探测，输出 JSON 格式（适合 AI 客户端解析）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless --output-format json

# 强制重新探测（忽略缓存）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless --force
```

**阶段 2：从缓存选择模块，执行管线**
```bash
# 选择指定模块执行（编号来自阶段 1 的输出）
python -m core.discovery.run --project <项目名> --discover-select "1,3,5" --headless

# 选择所有模块执行
python -m core.discovery.run --project <项目名> --discover-select "all" --headless
```

## 参数说明

### run.py（发现流程）

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
| --export-modules | 否 | 从导航发现缓存生成 modules.yaml |
| --force | 否 | 强制重新发现 |
| --tag | 否 | 按标签过滤模块（逗号分隔） |
| --version | 否 | 脚本版本号（默认 v1.0.0） |
| --force-gen | 否 | Stage 2 验证失败仍强制生成脚本 |
| --max-recapture | 否 | Stage 2 最大重试捕获次数 |
| --capture-all | 否 | 捕获所有 XHR/fetch（不限于 /estack/api） |
| --export | 否 | Stage 4 验证通过后导出 artifacts |
| --discover | 否 | 一体化发现模式（探测 + 交互选择 + 执行） |
| --discover-only | 否 | 仅探测菜单，输出模块列表后退出 |
| --discover-select | 否 | 从缓存选择模块执行（如 "1,3,5" 或 "all"） |
| --home-url | 否 | 导航发现的主页面 URL（避免交互式输入） |
| --output-format | 否 | 输出格式：text/json（默认 text） |
| --no-run | 否 | 跳过脚本生成后的自动运行验证 |

### run_parallel.py（执行测试）

| 参数 | 必填 | 说明 |
|------|------|------|
| --project | ✅ | 项目 ID（projects/ 下的目录名） |
| --type | 否 | 脚本类型：api（默认）或 ui |
| --parallel | 否 | 并发数（默认 1，顺序执行） |
| --module | 否 | 只运行指定模块（模糊匹配文件名） |
| --version | 否 | 脚本版本号（默认 v1.0.0） |
| --headless | 否 | 无头模式（默认打开浏览器） |
| --export | 否 | 运行完成后执行 Stage 5 导出 artifacts |
| --scripts-dir | 否 | 自定义脚本目录（默认 projects/<project>/v1.0.0/） |

## ⚠️ AI 行为约束（必须遵守）

1. 执行 `--discover-only` 后，**必须**将模块列表展示给用户
2. **等待用户选择**要执行的模块（输入编号如 "1,3,5" 或 "all"）
3. **禁止**自动拼接 `--all-modules` 或 `--discover-select "all"`
4. 用户明确说"全部重新扫描"/"重新扫描所有模块"时，可使用 `--all-modules --force`

## 报告生成

### 汇总报告
- **API 汇总报告**：包含所有模块的 API 测试结果，按 group → module 两级结构组织
- **UI 汇总报告**：包含所有模块的 UI 测试结果，按 group → module 两级结构组织
- **报告位置**：`projects/<项目名>/v1.0.0/reports/_summary/`
- **生成时机**：`run_parallel.py` 运行完成后自动生成

### 单模块报告
- 每个模块脚本运行时也会生成独立报告
- **API 报告位置**：`workspace/<项目名>/output/logs/{module}_API测试.jsonl`
- **UI 报告位置**：`projects/<项目名>/v1.0.0/ui/reports/{module}/`

### 调试单个模块
用户可以直接运行单个模块脚本进行调试：
```bash
# 运行单个 API 测试脚本
python projects/<项目名>/v1.0.0/api/{group}/{index}_{module}_API测试.py

# 运行单个 UI 测试脚本（打开浏览器）
python projects/<项目名>/v1.0.0/ui/{group}/{index}_{module}.py

# 运行单个 UI 测试脚本（无头模式）
python projects/<项目名>/v1.0.0/ui/{group}/{index}_{module}.py --headless
```

## 注意事项

- `run.py` 负责发现流程（Stage 1-4），生成测试脚本
- `run_parallel.py` 负责执行测试（运行脚本 + 生成报告 + 可选 Stage 5 导出）
- 增量发现：7 天内已发现的模块自动跳过（除非 --force）
- 生成的脚本包在 `projects/<项目名>/v1.0.0/` 下，可拷贝独立运行
- 运行脚本时会自动检测并刷新过期的 cookie（如果配置了登录凭据）
