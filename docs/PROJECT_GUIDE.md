# API_AI_test — 无文档后台系统的 API 自动发现与测试生成框架

> 给定一个 Web 后台模块入口 URL + 登录凭证，自动完成：**UI 探测 → API 捕获 → 逻辑分析 → 脚本生成 → 导出 artifacts**，让没有 API 文档的后台系统无需人工抓包即可得到可运行的 API/UI 测试脚本。

---

## 1. 项目结构

```
API_AI_test/
│
├── core/                        ★ 核心引擎
│   ├── discovery/                 五阶段模块级发现引擎（主管线）
│   │   ├── run.py                   CLI 入口
│   │   ├── io_helpers.py            I/O 辅助（profile/manifest/modules 加载）
│   │   ├── auth_runner.py           浏览器登录 + cookie 管理
│   │   ├── batch_runner.py          批量模块发现
│   │   ├── stage1_rescue.py         Stage 1 Phase C/D/E 补救子例程
│   │   ├── discover_ui.py           Stage 1: UI 元素探测（KB驱动 + 动态标签）
│   │   ├── capture_apis.py          Stage 2: API 拦截捕获（动态操作列表）
│   │   ├── analyze_flow.py          Stage 3: 逻辑分析 + Manifest 构建
│   │   ├── gen_test.py              Stage 4: API 脚本生成（Manifest 驱动薄脚本）
│   │   ├── generate_ui_script.py    Stage 2b: UI 脚本生成（Playbook → Playwright 脚本）
│   │   ├── export_artifacts.py      Stage 5: Postman/Excel/helpers 导出
│   │   ├── request_interceptor.py   HTTP 拦截 + KB 驱动注入
│   │   ├── endpoint_classifier.py   端点分类去重
│   │   ├── stage_validators.py      质量门禁（含 manifest 架构检查）
│   │   ├── diagnostic_mode.py       自诊断：失败时自动 DOM 分析 + 尝试修复
│   │   ├── kb_loader.py             知识库加载器
│   │   ├── kb_merger.py             知识库合并器
│   │   ├── ai_debug_assistant.py    AI 辅助调试（LLM 分析失败原因）
│   │   ├── feedback_loop.py         反馈循环（Stage 1↔2 联动）
│   │   ├── pre_api_merger.py        跨模块前置 API 合并
│   │   ├── required_elements.py     必需元素定义
│   │   ├── path_mapper.py           路径映射
│   │   ├── version.py               版本管理
│   │   ├── run_parallel.py          并行执行生成的测试脚本
│   │   ├── run_with_version.py      带版本号的运行入口
│   │   ├── stage2_errors.py         Stage 2 错误分类
│   │   ├── const.py                 常量定义
│   │   └── replay/                  UI 操作回放引擎
│   │       ├── replay_engine.py       Playbook 回放执行器（含通知钉住机制）
│   │       ├── button_driver.py       按钮点击驱动（KB 模板 fallback）
│   │       ├── form_filler.py         表单智能填充（type-aware 动态构建）
│   │       ├── locator_helpers.py     定位器辅助工具
│   │       └── wait_helpers.py        事件驱动等待（表格就绪/弹窗/loading）
│   │
│   ├── crawler/                   P0 爬虫子系统（独立于主管线）
│   │   ├── playwright_crawl.py      Playwright 爬虫
│   │   ├── probe_profile.py         站点探测
│   │   ├── combo_finder.py          参数组合发现
│   │   ├── entry_mapper.py          入口映射
│   │   ├── form_extractor.py        表单提取
│   │   └── global_miner.py          全局参数挖掘
│   │
│   └── generators/                代码生成器（独立于主管线）
│       ├── gen_openapi.py           生成 OpenAPI spec
│       ├── gen_pytest.py            生成 pytest 测试用例
│       ├── gen_api_layer.py         生成 API 层代码
│       └── menu_tools.py            菜单工具
│
├── lib/                         共享运行时库
│   ├── auth/                      鉴权相关
│   │   ├── auth.py                  鉴权引擎（Cookie 优先 + 滑块兜底）
│   │   ├── slider.py                滑块验证码（多算法投票）
│   │   ├── cookie_client.py         Cookie 管理客户端
│   │   └── login_tool.py            独立登录工具
│   ├── browser/                   浏览器操作
│   │   ├── api_client.py            API 客户端
│   │   ├── browser_ops.py           浏览器操作封装
│   │   └── nav.py                   导航辅助
│   ├── report/                    报告生成
│   │   ├── test_report.py           JSONL → HTML 报告
│   │   ├── report_html.py           HTML 报告模板
│   │   ├── reporter.py              报告生成器
│   │   └── ui_report.py             UI 测试报告
│   └── runtime/                   测试运行时
│       ├── test_runtime.py          Manifest 驱动测试运行时
│       ├── test_runner.py           测试运行器封装
│       ├── global_pre_apis.py       全局前置 API 执行器
│       └── run_history.py           运行历史记录
│
├── scripts/                     实用工具脚本
│   ├── _verify_auth.py            一键验证鉴权
│   ├── extract_cookies.py         交互式 cookie 提取
│   ├── identify_gap.py            滑块缺口识别（投票集成版）
│   ├── keep_alive.py              Cookie 保活守护进程
│   └── debug_tree_select.py       树形选择器调试
│
├── plugins/                     插件
│   └── pytest_api_ai_test/        pytest 插件（自动发现 *_API测试.py）
│
├── config/                      全局配置
│   ├── debug_strategies.json      调试策略
│   ├── failure_patterns.yaml      失败模式
│   ├── operation_patterns.json    操作模式
│   ├── probe_lessons_kb.json      探测经验知识库
│   └── selector_patterns.json     选择器模式
│
├── tests/                       单元测试 + 工具脚本（pytest）
│   ├── test_module_discovery.py   主管线测试
│   ├── test_stage5_verification.py Stage 5 导出测试
│   ├── test_auth.py               鉴权测试
│   ├── test_run_history.py        运行历史测试
│   ├── test_utils.py              工具函数测试
│   ├── test_pre_apis_global.py    前置 API 测试
│   └── compare_screenshots.py     截图对比工具
│
├── projects/                    项目数据（每个子目录是一个被测系统）
│   └── <project_name>/
│       ├── profile.yaml           项目配置
│       ├── modules.yaml           模块清单（批量处理用）
│       ├── .api_version           API 版本号
│       ├── kb/                    知识库
│       │   ├── api_catalog.json   API 目录
│       │   ├── menu_tree.json     菜单树
│       │   └── module_discovered/ 模块发现结果
│       │       ├── <module>_ui.json
│       │       ├── <module>_capture.json
│       │       ├── <module>_manifest.json
│       │       ├── <module>_analysis.json
│       │       └── <module>_playbook.json
│       ├── output/                运行时输出
│       │   └── config/            cookies.json 等
│       └── <version>/             版本化脚本包
│           ├── api/               API 测试脚本
│           │   ├── <module>_API测试.py
│           │   ├── helpers.py
│           │   ├── lib/           同步的运行时库
│           │   ├── config/cookies.json
│           │   └── report/        测试报告
│           ├── ui/                UI 测试脚本
│           │   ├── <module>.py
│           │   ├── <module>_data.json
│           │   ├── lib/           同步的运行时库
│           │   ├── config/cookies.json
│           │   └── output/reports/ UI 测试报告
│           ├── export/<module>/   Stage 5 导出
│           │   ├── helpers.py
│           │   └── *.postman_collection.json
│           └── login.py           登录脚本
│
├── docs/                        文档
│   ├── PROJECT_GUIDE.md           项目指南（本文档）
│   ├── debug/                     调试文档
│   └── design/                    设计文档
│
├── run_suite.py                 测试套件运行器（扫描所有模块执行并汇总报告）
├── skill.md                     AI Skill 描述文件
├── pyproject.toml               项目元数据
├── requirements.txt             Python 依赖
├── pytest.ini                   pytest 配置
└── Dockerfile                   容器化构建
```

---

## 2. 五阶段流水线

```
Stage 1 (discover_ui)    → {module}_ui.json + {module}_playbook.json
Stage 2 (capture_apis)   → {module}_capture.json + UI 脚本生成
Stage 3 (analyze_flow)   → {module}_analysis.json + {module}_manifest.json
Stage 4 (gen_test)       → {module}_API测试.py + 验证运行
Stage 5 (export)         → Postman Collection + helpers.py + Excel params
```

### Stage 1: UI 元素探测

**输入**: 模块入口 URL  
**输出**: `{module}_ui.json` + `{module}_playbook.json`

6 Phase 补救流程:
- **Phase A**: 标准探测 (discover_all)
- **Phase B**: 业务闭环验证 (_validate_business_flow)
- **Phase C**: hints 定向重探 (_scan_hints)
- **Phase D**: 前置操作检查 + 重试 (_retry_precondition)
- **Phase E**: Vision 截图分析兜底 (ai_assisted_analysis)
- **Phase F**: 终止判定 (critical 缺失 → None)

### Stage 2: API 捕获 + UI 脚本生成

**输入**: Stage 1 的 ui_result + playbook  
**输出**: `{module}_capture.json` + UI 自动化脚本

- 重放 playbook 中的所有操作
- 拦截 XHR/fetch 请求
- 按时间戳分类 API（create/update/delete/query）
- 调用 `generate_ui_script.py` 生成 Playwright UI 自动化脚本包

### Stage 3: 逻辑分析

**输入**: Stage 2 的 capture result  
**输出**: `{module}_manifest.json`

- 构建 manifest 字典（包含所有步骤、依赖、断言）
- 追踪前置 API 依赖链
- 识别 infrastructure APIs（菜单/权限/配置）
- 生成 body_field_roles（name/mutable/id_ref/context）

### Stage 4: 脚本生成

**输入**: Stage 3 的 manifest  
**输出**: `{module}_API测试.py`

- 生成薄脚本（~50 行）
- 所有执行逻辑由 `lib/runtime/test_runtime.py` 提供
- 同步运行时库到脚本包 `lib/`

### Stage 5: 导出 artifacts

**输入**: Stage 3/4 的 manifest  
**输出**: Postman Collection + helpers.py + Excel params

- **Postman Collection v2.1.0**: 可直接导入 Postman
- **helpers.py**: 高层辅助函数（给外部测试平台用）
- **Excel 参数文件**: 变量配置表（e_ 前缀，敏感标记）

---

## 3. 使用方法

### 3.1 单模块全流程

```bash
python -m core.discovery.run \
    --project ecm-compute \
    --url "/estack/web/estack/user-center/user-manage/user" \
    --module "用户管理" \
    --user admin --pass "password123" \
    --headless
```

### 3.2 分阶段执行

```bash
# Stage 1: UI 探测
python -m core.discovery.run --project ecm-compute --url "/path" --module "用户管理" --stage 1

# Stage 2: API 捕获
python -m core.discovery.run --project ecm-compute --url "/path" --module "用户管理" --stage 2

# Stage 3+4: 分析 + 生成（离线，不需要浏览器）
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 34 --offline

# Stage 4: 仅生成脚本（从已有 manifest）
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 4

# Stage 5: 导出 artifacts（不需要浏览器）
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 5
```

### 3.3 批量处理所有模块

```bash
# 处理 modules.yaml 中的所有模块
python -m core.discovery.run --project ecm-compute --all-modules --headless

# 按标签过滤
python -m core.discovery.run --project ecm-compute --all-modules --tag "用户,权限"

# 强制重新发现（忽略增量缓存）
python -m core.discovery.run --project ecm-compute --all-modules --force
```

### 3.4 并行测试执行

```bash
# 并行运行所有已生成的测试脚本
python -m core.discovery.run_parallel --project ecm-compute

# 指定版本和并发数
python -m core.discovery.run_parallel --project ecm-compute --version v1.0.0 --parallel 6

# 只运行指定模块
python -m core.discovery.run_parallel --project ecm-compute --module "用户管理"
```

### 3.5 测试套件运行

```bash
python run_suite.py --project ecm-compute
```

扫描所有模块的 API 和 UI 测试脚本，依次执行并生成汇总 HTML 报告。

### 3.6 独立登录工具

```bash
# 获取 fresh cookies（从项目目录执行）
cd projects/ecm-compute
PYTHONPATH=../.. python -m lib.auth.login_tool --output v1.0.0/ui/config/cookies.json --headless
```

### 3.7 P0 爬虫（发现入口点）

```bash
python -m core.crawler.playwright_crawl --project ecm-compute
```

### 3.8 直接运行生成的 UI 测试脚本

```bash
# UI 测试脚本包含完整的回放引擎，可独立运行
python projects/ecm-compute/v1.0.0/ui/用户管理.py

# 无头模式
python projects/ecm-compute/v1.0.0/ui/用户管理.py --headless

# 指定操作
python projects/ecm-compute/v1.0.0/ui/用户管理.py --operation "创建用户"
```

---

## 4. 关键设计

### 4.1 Cookie-only 认证

生成的脚本**不包含登录逻辑**，仅依赖 `cookies.json`：
- 运行前通过 `login_tool.py` 获取 cookie
- 或手动编辑 `config/cookies.json`（Playwright cookie 格式）
- 脚本运行时从 `config/cookies.json` 读取 token 注入浏览器

### 4.2 Manifest 驱动

Stage 3-5 都基于 manifest 字典：
- Stage 3 生成 manifest
- Stage 4 根据 manifest 生成 API 脚本
- Stage 5 根据 manifest 导出 artifacts
- Stage 5 可独立运行（从磁盘加载 manifest）

### 4.3 增量发现

7 天内已发现的模块自动跳过（除非 `--force`）：
- 检查 `kb/module_discovered/{module}_capture.json` 的 mtime
- 超过 7 天则重新发现
- URL 变更也会触发重新发现

### 4.4 运行时同步

`gen_test.py` 和 `generate_ui_script.py` 自动同步运行时库到脚本包：
- API 脚本: `projects/<project>/<version>/api/lib/`
- UI 脚本: `projects/<project>/<version>/ui/lib/`
- 同步时自动转换导入路径（`from .. import const` → `from . import const`）
- 脚本包可拷贝到其他地方独立运行

### 4.5 UI 回放引擎 — 通知钉住机制

UI 自动化截图需要捕获 `el-message` / `el-notification` 等瞬态通知：

- **问题**：通知出现后 ~3 秒自动消失，截图时已不可见
- **方案**：MutationObserver 在 replay 开始时注入，检测到通知后钉住元素
  - 强制 `display/visibility/opacity` 为可见
  - 拦截 `node.remove()` 和 `parentNode.removeChild()`
  - 子 MutationObserver 监听属性变更，覆盖任何隐藏尝试
- **释放**：`_cleanup_dialogs()` 在操作间清理时调用 `window.__unpin_notifications()` 释放
- **截图时机**：在 `wait_for_loading_complete()` 之后、`+500ms` 等待通知出现后截图

---

## 5. 输出规范

### 5.1 运行时输出

统一在 `projects/<project>/output/`：
- `config/` — cookies.json 等运行时配置
- `logs/` — pipeline 日志

**根目录不产生任何运行时输出**。

### 5.2 版本化脚本包

在 `projects/<project>/<version>/`：
- `api/` — API 测试脚本 + lib/ + config/ + report/
- `ui/` — UI 测试脚本 + lib/ + config/ + output/reports/
- `export/<module>/` — Stage 5 导出（Postman/helpers）

### 5.3 KB 中间产物

在 `projects/<project>/kb/module_discovered/`：
- `{module}_ui.json` — Stage 1 UI 探测结果
- `{module}_playbook.json` — Stage 1 执行剧本
- `{module}_capture.json` — Stage 2 API 捕获结果
- `{module}_manifest.json` — Stage 3 manifest
- `{module}_analysis.json` — Stage 3 分析结果

---

## 6. 项目配置

### 6.1 profile.yaml

```yaml
base_url: "https://10.151.61.248"
login_url: "https://10.151.61.248/estack/web/estack/login"
api_base: "/estack/api"
probe_url: "/estack/web/estack/user-center/user-manage/user"

auth:
  header_name: "Authorization"
  header_prefix: "Bearer "
  token_key: "estackToken"
  token_storage: "localStorage"
  cookie_token_key: "accessToken"

captcha:
  type: "slider"
  bg_selector: ".slide_bg"
  gap_selector: ".slide_gap"

credentials:
  username_env: "APP_USER"
  password_env: "APP_PASS"
```

### 6.2 modules.yaml

```yaml
modules:
  - name: "用户管理"
    url: "/estack/web/estack/user-center/user-manage/user"
    enabled: true
    priority: 1
    tags: ["用户中心", "基础"]

  - name: "角色管理"
    url: "/estack/web/estack/user-center/role-manage/role"
    enabled: true
    priority: 2
    tags: ["用户中心", "权限"]
```

---

## 7. 测试

```bash
# 运行全部单元测试
python -m pytest tests/ -q

# 运行指定测试文件
python -m pytest tests/test_module_discovery.py -v

# 运行指定测试函数
python -m pytest tests/test_stage5_verification.py::test_export_artifacts -v
```

---

## 8. 常见问题

### Q: Stage 2 验证失败怎么办？

使用 `--force-gen` 强制生成脚本：
```bash
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 34 --force-gen
```

或使用 `--max-recapture` 增加重试次数：
```bash
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 2 --max-recapture 3
```

### Q: 如何跳过浏览器阶段？

使用 `--offline` 或 `--stage 34`：
```bash
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 34 --offline
```

### Q: Cookie 过期了怎么办？

```bash
cd projects/ecm-compute
PYTHONPATH=../.. python -m lib.auth.login_tool --output v1.0.0/ui/config/cookies.json --headless
```

### Q: 生成的脚本如何独立运行？

脚本包在 `projects/<project>/<version>/` 下：
```bash
# API 测试
cd projects/ecm-compute/v1.0.0/api
python 用户管理_API测试.py

# UI 测试
cd projects/ecm-compute/v1.0.0/ui
python 用户管理.py
```

前提是 `config/cookies.json` 中有有效的 cookie。

### Q: 如何导出 Postman Collection？

使用 `--stage 5`：
```bash
python -m core.discovery.run --project ecm-compute --module "用户管理" --stage 5
```

输出在 `projects/ecm-compute/v1.0.0/export/用户管理/`。

### Q: UI 截图截不到通知提示？

回放引擎内置通知钉住机制（MutationObserver），在 `replay_from_playbook()` 开始时自动注入。确保使用最新的 `core/discovery/replay/replay_engine.py` 和 `button_driver.py`。

---

## 9. 技术栈

- **Python 3.9+**
- **Playwright** — 浏览器自动化
- **pytest** — 测试框架
- **httpx** — HTTP 客户端
- **opencv-python-headless** — 滑块验证码缺口识别
- **pyyaml** — 配置文件解析
- **jsonpath-ng** — JSON 路径查询
- **openpyxl** — Excel 导出（可选）

---

## 10. 相关文档

| 文档 | 路径 | 说明 |
|------|------|------|
| 五阶段引擎改进 | `docs/design/stage-improvement.md` | 五阶段实现思路 + 流程图 |
| AI Skill 描述 | `skill.md` | AI 客户端集成的 Skill 配置 |
| 主管线操作指南 | `.claude/skills/pipeline.md` | Claude Code 内部操作指南 |
