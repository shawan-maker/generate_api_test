# 主管线操作指南

## 用途
运行 API 自动化发现主管线（Stage 1-5）。

## 阶段说明
- **Stage 1**：UI 元素探测（discover_ui）— 需要浏览器
- **Stage 2**：API 捕获（capture_apis）— 需要浏览器
- **Stage 3+4**：流程分析 + 测试脚本生成（analyze_flow + gen_test）— 离线可运行
- **Stage 5**：导出 Postman Collection / helpers.py / Excel 参数文件 — 离线可运行

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

### 批量处理所有模块
```bash
# 处理 modules.yaml 中的所有模块
python -m core.discovery.run --project <项目名> --all-modules --user <用户名> --pass "<密码>" --headless

# 批量 + 指定阶段
python -m core.discovery.run --project <项目名> --all-modules --stage 34

# 按标签过滤
python -m core.discovery.run --project <项目名> --all-modules --tag "用户,权限"

# 强制重新发现（忽略增量缓存）
python -m core.discovery.run --project <项目名> --all-modules --force
```

### 导航自动发现模式（Discover）
分两阶段执行，适合 AI 客户端或脚本化场景。

**阶段 1：探测菜单，输出模块列表**
```bash
# 仅探测，输出 JSON 格式（适合 AI 客户端解析）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless --output-format json

# 仅探测，输出文本格式（适合人工查看）
python -m core.discovery.run --project <项目名> --discover-only \
  --home-url "<带菜单的页面URL>" --headless

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

# 选择 + 指定阶段
python -m core.discovery.run --project <项目名> --discover-select "1,2,3" --stage 34
```

**一体化交互模式**
```bash
# 完整流程：探测 → 交互选择 → 执行（适合人工操作）
python -m core.discovery.run --project <项目名> --discover --headless
```

### 并行测试执行
```bash
# 并行运行所有已生成的测试脚本
python -m core.discovery.run_parallel --project <项目名>

# 指定版本和并发数
python -m core.discovery.run_parallel --project <项目名> --version v1.0.0 --parallel 6

# 只运行指定模块
python -m core.discovery.run_parallel --project <项目名> --module "用户管理"
```

### 测试套件运行
```bash
python run_suite.py --project <项目名>
```

## 参数说明

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
| --discover | 否 | 一体化发现模式（探测 + 交互选择 + 执行） |
| --discover-only | 否 | 仅探测菜单，输出模块列表后退出 |
| --discover-select | 否 | 从缓存选择模块执行（如 "1,3,5" 或 "all"） |
| --home-url | 否 | 导航发现的主页面 URL（避免交互式输入） |
| --output-format | 否 | 输出格式：text/json（默认 text） |

## 辅助工具

```bash
# 独立登录工具（获取 cookie）
python -m lib.auth.login_tool --project <项目名>

# P0 爬虫（发现入口点）
python -m core.crawler.playwright_crawl --project <项目名>
```

## 注意事项

- Stage 5 可独立运行，无需浏览器，直接从磁盘加载 manifest 导出
- `--stage all` 会执行全部阶段（含 Stage 5 导出）
- 增量发现：7 天内已发现的模块自动跳过（除非 --force）
- 生成的脚本包在 `projects/<项目名>/scripts/v1.0.0/` 下，可拷贝独立运行
