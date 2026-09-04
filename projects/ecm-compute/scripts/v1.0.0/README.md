# API AI Test - 快速上手

> 本目录为 **独立可执行** 的测试脚本包，可拷贝到任意机器运行，不依赖框架源码。

## 目录结构

```
v1.0.0/
├── api/                          ← API 自动化测试
│   ├── lib/                      ← 运行时引擎（test_runtime, auth, slider）
│   ├── config/                   ← cookies（自动生成，可删除后重新登录）
│   └── {模块}_API测试.py          ← 测试脚本
├── ui/                           ← UI 自动化测试
│   ├── lib/                      ← 运行时引擎（replay_engine, button_driver, form_filler）
│   ├── config/cookies.json       ← 登录凭据（自动生成）
│   ├── kb/probe_knowledge.json   ← 表单填充知识库
│   ├── {模块}_playbook.json      ← UI 操作步骤定义
│   ├── {模块}_data.json          ← 测试数据
│   └── {模块}.py                 ← 测试脚本
└── README.md
```

---

## API 测试

### 1. 准备

```bash
pip install requests httpx urllib3 opencv-python numpy playwright
playwright install chromium
```

- `requests` — API 调用
- `httpx` — 滑块登录鉴权
- `urllib3` — 禁用 SSL 警告
- `opencv-python` + `numpy` — 滑块缺口识别（cookie 有效时不需要）
- `playwright` — 滑块验证自动登录（cookie 有效时不需要）

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

### 自定义登录账号

设置环境变量：
```bash
set APP_USER=your_username
set APP_PASS=your_password
```

### Cookie 自动刷新

- 首次运行或 cookie 过期时，脚本会自动执行滑块登录并保存 cookie 到 `config/`
- 后续运行优先使用已有 cookie，无需重复登录
- 如需强制重新登录，删除 `config/cookies.json` 即可

### 拷贝到其他机器

将整个 `v1.0.0/` 目录拷贝即可，只需目标机器安装好 Python 和上述依赖。
