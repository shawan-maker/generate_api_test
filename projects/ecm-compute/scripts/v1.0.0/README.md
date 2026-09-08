# API AI Test - 快速上手

> 本目录为 **独立可执行** 的测试脚本包，可拷贝到任意机器运行，不依赖框架源码。

## 目录结构

```
v1.0.0/
├── api/                          ← API 自动化测试
│   ├── lib/                      ← 运行时引擎（test_runtime, test_report, cookie_client）
│   ├── config/cookies.json       ← 登录凭据（需从浏览器导出或框架刷新）
│   └── {模块}_API测试.py          ← 测试脚本
├── ui/                           ← UI 自动化测试
│   ├── lib/                      ← 运行时引擎（replay_engine, button_driver, form_filler, cookie_client, ui_report）
│   ├── config/cookies.json       ← 登录凭据（需从浏览器导出或框架刷新）
│   ├── kb/probe_knowledge.json   ← 表单填充知识库
│   ├── {模块}_playbook.json      ← UI 操作步骤定义
│   └── {模块}.py                 ← 测试脚本
└── README.md
```

---

## API 测试

### 1. 准备

```bash
pip install requests urllib3
```

- `requests` — API 调用
- `urllib3` — 禁用 SSL 警告

### 2. 执行

```bash
cd api
python 用户管理_API测试.py                 # 运行所有步骤
python 用户管理_API测试.py create delete   # 只运行 create 和 delete
```

### 3. 报告

```
api/report/{模块}/{模块}_report_YYYYMMDD_HHMMSS.html
```

---

## UI 测试

### 1. 准备

```bash
pip install playwright
playwright install chromium
```

### 2. 执行

```bash
cd ui
python 用户管理.py                    # 运行所有操作（有界面）
python 用户管理.py create update      # 只运行 create 和 update
python 用户管理.py --headless         # 无头模式
```

### 3. 报告

```
ui/output/reports/{模块}_ui_report_YYYYMMDD_HHMMSS.html
```

---

## 通用说明

### Cookie 准备

生成脚本使用 **纯 Cookie 鉴权**，不做自动登录。需要提供 `config/cookies.json`：

1. **框架刷新**：运行框架 `--stage 1` 自动登录并保存 cookie
2. **浏览器导出**：从 DevTools → Application → Cookies 导出为 Playwright JSON 数组格式
3. **CI 定期刷新**：流水线中定期执行框架登录命令

Cookie 文件格式（Playwright JSON 数组）：
```json
[
  {
    "name": "estackToken",
    "value": "xxx...",
    "domain": "10.151.61.248",
    "path": "/",
    "httpOnly": false,
    "secure": false
  }
]
```

### Cookie 过期处理

- 如果运行时报 `❌ 鉴权失败`，说明 cookie 已过期
- 需要重新执行上述"Cookie 准备"步骤刷新 `config/cookies.json`
- 生成脚本不会自动执行登录（各项目登录方式不同，无法统一）

### 自定义登录账号

设置环境变量（仅在框架层 `--stage 1` 刷新 cookie 时使用）：
```bash
set APP_USER=your_username
set APP_PASS=your_password
```

### 拷贝到其他机器

将整个 `v1.0.0/` 目录拷贝即可，只需目标机器安装好 Python 和上述依赖，并准备好有效的 `config/cookies.json`。
