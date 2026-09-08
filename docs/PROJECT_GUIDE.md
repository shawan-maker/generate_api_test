# API_AI_test — 无文档后台系统的 API 自动发现与测试生成框架

> 给定一个 Web 后台模块入口 URL + 登录凭证，自动完成：**按钮探测 → API 捕获 → 逻辑分析 → 脚本生成**，让没有 API 文档的后台系统无需人工抓包即可得到可运行的 API 测试脚本。

---

## 1. 架构概览

```
API_AI_test/
│
├── module_discovery/          ★ 核心：四阶段模块级发现引擎
│   ├── run.py                   CLI 入口
│   ├── discover_ui.py           Stage 1: 按钮/元素探测（KB驱动 + 动态标签）
│   ├── capture_apis.py          Stage 2: API 拦截捕获（动态操作列表）
│   ├── analyze_flow.py          Stage 3: 逻辑分析 + Manifest 构建（build_manifest）
│   ├── gen_test.py              Stage 4: 脚本生成（Manifest 驱动薄脚本）
│   ├── request_interceptor.py   HTTP 拦截 + KB 驱动注入（支持 --capture-all）
│   ├── endpoint_classifier.py   端点分类去重
│   ├── stage_validators.py      质量门禁（含 manifest 架构检查）
│   ├── diagnostic_mode.py       自诊断：失败时自动 DOM 分析 + 尝试修复
│   ├── kb_loader.py             知识库加载器（probe_knowledge.json + XPath 模板展开）
│   ├── kb_merger.py             知识库合并器（跨模块去重 + 冲突检测）
│   ├── ai_debug_assistant.py    AI 辅助调试（LLM 分析失败原因）
│   ├── feedback_loop.py         反馈循环（Stage 1↔2 联动）
│   ├── required_elements.py     必需元素定义
│   ├── version.py               版本管理
│   ├── run_parallel.py          并行执行
│   ├── const.py                 常量定义
│   ├── kb/                      知识库文件
│   │   └── probe_knowledge.json
│   └── replay/                  UI 操作回放引擎
│       ├── replay_engine.py     Playbook 回放执行器
│       ├── button_driver.py     按钮点击驱动（KB 模板 fallback + 隐藏过滤/覆盖层定位/iframe 穿透）
│       ├── form_filler.py       表单智能填充（type-aware 动态构建 + Multi-Step 选项自动发现）
│       └── wait_helpers.py      事件驱动等待（表格就绪/弹窗/loading 完成/网络空闲）
│
├── lib/                       通用库（按功能分组）
│   ├── auth/                  鉴权相关
│   │   ├── auth.py              鉴权引擎（Cookie 优先 + 滑块兜底）
│   │   ├── slider.py            滑块验证码（多算法投票）
│   │   └── cookie_client.py     Cookie 管理客户端
│   ├── runtime/               测试运行时
│   │   ├── test_runtime.py      Manifest 驱动测试运行时（ResponseParser + StepExecutor + TestRunner）
│   │   ├── test_runner.py       测试运行器封装
│   │   └── run_history.py       运行历史记录
│   ├── report/                报告生成
│   │   ├── test_report.py       JSONL 日志 → Postman/Newman 风格 HTML 报告
│   │   ├── report_html.py       HTML 报告模板
│   │   ├── reporter.py          报告生成器
│   │   └── ui_report.py         UI 测试报告
│   ├── browser/               浏览器操作
│   │   ├── browser_ops.py       浏览器操作封装
│   │   ├── api_client.py        API 客户端 + importlib 函数加载
│   │   └── nav.py               导航辅助
│   ├── catalog.py             API 编目合并 → OpenAPI 3.1 IR
│   ├── resolver.py            前置依赖解析（Test Data Fabric）
│   ├── orphan_cleaner.py      孤立资源清理
│   ├── drift.py               漂移检测
│   └── utils.py               通用工具（safe_write / session_id）
│
├── run_suite.py               测试套件运行器（扫描所有模块，依次执行并生成汇总报告）
├── discovery/                 P0 全站巡游（全站 API 离线扫描）
├── generators/                代码生成器（API 层 / OpenAPI / pytest）
├── config/                    全局知识库（选择器模板 + 经验库）
├── plugins/                   pytest 插件
├── scripts/                   工具与调试脚本
├── projects/                  多项目隔离工作空间
└── tests/                     框架单元测试
```

### 数据流

```
入口 URL + profile.yaml
       ↓
Stage 1: discover_ui    → <模块>_ui.json          按钮/表单清单
       ↓
Stage 2: capture_apis   → <模块>.json             API 端点 + 请求/响应样本
       ↓
Stage 3: analyze_flow   → <模块>_analysis.json    CRUD 顺序 + 依赖链 + 状态断言
         build_manifest → <模块>_manifest.json    完整测试清单（含响应约定）
       ↓
Stage 4: gen_test       → scripts/<ver>/<模块>_API测试.py  薄脚本（~100 行，嵌入 manifest）
       ↓
执行脚本                  → output/logs/<模块>_API测试.jsonl  结构化 JSONL 日志
       ↓
test_report.py          → output/reports/<模块>/  Postman/Newman 风格 HTML 报告
```

---

## 2. 四阶段发现引擎

### 2.1 Stage 1：按钮探测（discover_ui.py）

`discover_all(page, max_retries=2)` 执行发现 → 产出两阶段流水线，输出 `<模块>_ui.json`。

**UI 框架检测**：流程开始时 `_detect_ui_framework()` 通过 DOM 类名检测 `element-ui` 或 `ant-design`，结果贯穿后续所有步骤（KB 模板变体选择、CSS 选择器、表单类型识别）。

**发现阶段（步骤 1-4）**：

| 步骤 | 方法 | 作用 | 依赖 |
|------|------|------|------|
| 1. CSS 地毯扫描 | `_scan_candidates()` | 用 20+ CSS 选择器一次 `evaluate` 扫描所有可见元素，按 DOM 位置分类（工具栏/行操作/弹窗/菜单），行操作按钮标记 `source`（`main`/`fixed-right`/`fixed-left`），按 `text+tagName` 去重 | 无 |
| 2. KB 增强扫描 | `_kb_enrichment_scan()` | 用 `probe_knowledge.json` 的 XPath 模板 + `ACTION_KEYWORDS` 关键词查找 CSS 漏掉的按钮（特别是中文空格问题：`"确 定"` → XPath `contains(.,'确') and contains(.,'定')` 仍可命中），与步骤 1 去重后合并 | 步骤 1 |
| 3. 下拉菜单发现 | `_discover_dropdowns()` | 展开下拉触发器（硬编码 5 个 + DOM `.el-dropdown` 动态补充）读取子项文本——子项是点击后动态渲染的，CSS 和 KB 都看不到 | 步骤 1-2 |
| 4. 创建表单扫描 | `_scan_create_dialog()` | 点击创建按钮后扫描表单字段，支持弹窗模式和页面跳转模式，遍历主页面 + 所有 iframe，输出统一 schema `{label, type, kb_category, required}` | 步骤 1-2 |

**产出阶段**：

| 步骤 | 方法 | 作用 | 依赖 |
|------|------|------|------|
| 5. 标签构建 | `_build_button_labels()` | 汇总步骤 1-3 的所有按钮，构建 `{action: label}` 映射（如 `{"create": "新增"}`），供 manifest 生成使用 | 步骤 1-3 |
| 6. 质量门禁 + 结构快照 | `validate_stage1()` + `_snapshot_page_structure()` | 验证按钮数量/创建删除覆盖，不通过等待 2 秒重试（最多 2 次）；获取表格 DOM 骨架供 Stage 2 使用 | 步骤 1-5 |

**页面结构快照字段**（`_snapshot_page_structure()` 输出）：

| 字段 | 含义 | Stage 2 用途 |
|------|------|-------------|
| `hasFixedLeft` / `hasFixedRight` | 是否有固定列 | 决定搜索哪个 wrapper |
| `mainBodyRows` / `fixedRightRows` | 各 wrapper 行数 | 判断行数据在哪个区域 |
| `operationColumnIndex` | 操作列索引 | 定位行操作按钮所在列 |
| `operationColumnLocation` | `last` / `first` / `middle` | 选择 fallback 链起点 |
| `tableWrappers` | 所有 wrapper 的 class + 行数 | `find_data_row()` 多 wrapper 搜索 |

**输出**：`<模块>_ui.json`（含 `page_structure`、`framework`、`button_labels` 元数据）

### 2.2 Stage 2：API 捕获（capture_apis.py）

四个子模块协作完成 API 拦截：

| 子模块 | 职责 |
|--------|------|
| `request_interceptor.py` | Playwright 请求/响应监听，过滤 API，收集调用记录和响应样本。支持 `--capture-all` 模式捕获全部 XHR/fetch |
| `replay/button_driver.py` | 驱动按钮点击（KB 模板 fallback 链 + **Locator 增强**：隐藏过滤器 `HIDDEN_FILTERS` 排除不可见/禁用元素、覆盖层定位 `OVERLAY_SELECTORS` 优先在弹窗/抽屉内寻找、iframe 自动穿透） |
| `replay/form_filler.py` | 表单字段扫描与智能填充（type-aware 动态构建 + **Multi-Step 自动发现**：el-select 展开后自动读取首个可见选项、el-cascader 逐级发现+选择，最大深度 10 级） |
| `endpoint_classifier.py` | 六级优先级端点分类与去重 |

**动态操作列表**：`_build_operations_from_ui(ui_result)` 从 Stage 1 结果动态构建行操作列表，按 CRUD 优先级排序。仅在动态构建为空时 fallback 到硬编码默认列表。

**Locator 增强系统**：按钮点击操作（`click_row_button_v2`、`click_row_more_item`、`confirm_dialog` 等）集成三大增强机制：
- **隐藏过滤器**：`const.HIDDEN_FILTERS` 定义三套 XPath 过滤谓词（`element-ui` / `ant-design` / `_universal`），通过 `_append_hidden_filter()` 自动追加到按钮 XPath，排除 `is-hidden`、`display: none`、`disabled`、`is-disabled` 等不可见/禁用元素
- **覆盖层定位**：`detect_active_overlay_js()` 检测当前活跃的弹窗/抽屉/消息框，`apply_overlay_scope()` 为 XPath 追加覆盖层容器前缀（如 `//div[contains(@class,'el-dialog')]`），优先在弹窗内寻找按钮，失败时降级到全局定位
- **iframe 穿透**：`_try_click_frame()` 遍历 `page.frames` 在主框架和所有 iframe 中查找目标元素，支持 Element UI 弹窗内容渲染在 iframe 中的场景

**Multi-Step 选项自动发现**：`replay/form_filler.py` 的 `MultiStepExecutor` 在填充 el-select / el-cascader 时自动发现选项：
- `_execute_select()`：展开下拉框后调用 `_discover_first_option()` 读取首个可见选项文本，通过 `option_text` 参数传递给 KB 模板
- `_execute_cascader()`：当 `options` 为 None 时调用 `_cascader_discover_and_select()`，逐级展开并选择第一个有子级的项，到叶子级时选择，最大深度 10 级
- `_read_cascader_current_items()`：读取级联选择器当前面板的菜单项（Element UI: `.el-cascader-menu li[role="menuitem"]`，Ant Design: `.ant-cascader-menu .ant-cascader-menu-item`）

**事件驱动等待**：所有按钮点击后调用 `replay/wait_helpers.wait_for_loading_complete()` 替代固定 `wait_for_timeout(3000~5000)`，流程：等待浏览器加载状态 → 等待网络空闲（短超时容错） → 等待 7 种 loading 元素消失（`el-loading-mask`、`el-loading-text`、`ng-show loading`、`el-loading-spinner`、`ant-btn-loading`、`ant-btn-loading-icon`、`ant-spin-spinning`） → 稳定等待 1 秒。

**KB 驱动注入**：从 `config/probe_lessons_kb.json` 加载系统级配置，monkey-patch 浏览器的 `XMLHttpRequest.send` 和 `fetch`，对匹配请求自动补全缺失字段（如 `tenantId`/`adminId`）。代码零硬编码。

**权限门禁识别**：响应体匹配 `permission_gate_keywords`（"您没有/请先授权/forbidden"），命中即标注"需高权限账号"。

**close_dialog 快速关闭**：3 秒超时检测关闭按钮 + Escape 兜底，不再使用 30 秒默认超时。

**自诊断触发**：当行查找或按钮点击连续失败时，自动触发 `diagnostic_mode.py` 进行 DOM 结构分析并尝试修复（详见 §13 自诊断机制）。

**输出**：`<模块>.json`（端点清单 + 请求/响应样本 + permission_gates）

### 2.3 Stage 3：逻辑分析（analyze_flow.py）

五步推理流水线 + Manifest 构建：

1. **共现过滤** — 出现在 60%+ 按钮上下文的 API → 辅助 API
2. **核心过滤** — 移除 menu/theme/dictionary/current-user 等基础设施 API
3. **按钮-API 映射** — 按 contexts 建立"按钮名→核心 API"反向映射
4. **CRUD 顺序编排（三层策略）**：
   - 静态优先级基础顺序（`CRUD_EXECUTION_ORDER`）
   - 时序验证：从 interceptor 时间戳推导实际点击顺序（`_derive_temporal_order`）
   - 依赖约束拓扑排序：B 的请求体引用 A 的响应字段时，A 必须在 B 之前（`_topological_sort`）
   - 合并策略：以静态顺序为主，用依赖约束调整
5. **数据依赖链（深层分析）**：`_extract_ids_recursive` 递归遍历响应 JSON（最大深度 4 层），提取所有 ID 字段及其完整路径（如 `entity.data.userId`）。从创建响应提取 ID 注入后续请求；对比状态字段推导断言
6. **状态字段检测增强**：同时支持 dict 和 list 类型的 entity 响应，从列表响应首条记录中提取状态字段
7. **Manifest 构建**（`build_manifest()`）：将分析结果 + 响应约定发现合并为完整测试清单
   - `_discover_response_contract()`: 自动发现响应信封键、成功判断逻辑、列表/总数键、ID 字段
   - `_classify_body_fields()`: 为每个请求体字段标注角色（id_ref/context/name/mutable/static）
   - 自动插入验证步骤：在 create/update/delete 后插入查询验证（contains_id/not_contains_id 断言）

**输出**：
- `<模块>_analysis.json` — 逻辑分析结果
- `<模块>_manifest.json` — 完整测试清单（含 response_contract、auth_profile、steps、state_assertions）

### 2.4 Stage 4：脚本生成（gen_test.py）

**Manifest 驱动架构**：生成的脚本是薄脚本（~100 行），只包含：
- 嵌入的 MANIFEST JSON 数据（含 response_contract、auth_profile、steps 定义、state_assertions）
- `from lib.runtime.test_runtime import TestRunner` 引用
- `main()` 入口：创建 TestRunner 实例并执行

所有执行逻辑由 `lib/runtime/test_runtime.py` 提供，脚本本身不包含硬编码的业务逻辑。

**生成流程**：
1. `run.py` 调用 `analyze_flow.build_manifest()` 构建完整测试清单
2. `gen_test.generate_script(manifest, module_name)` 生成嵌入 manifest 的薄脚本
3. `save_script_to_file()` 保存到 `scripts/<version>/` 目录

**输出**：`scripts/<version>/<模块>_API测试.py`

### 2.5 测试运行时（lib/runtime/test_runtime.py）

Manifest 驱动的通用测试执行引擎，替代旧的硬编码脚本逻辑：

| 类 | 职责 |
|---|---|
| `ResponseParser` | 根据 `response_contract` 解析 API 响应：信封键提取、成功判断、列表/总数提取、ID 提取 |
| `StepExecutor` | 执行单个 CRUD 步骤：构建 URL/body → 发请求 → 三层断言 → 提取状态 → 写 JSONL 日志 |
| `TestRunner` | 编排完整测试流程：鉴权 → 上下文获取 → 按步骤执行 → 支持 `steps_filter` 单步执行 |

**Body 字段角色系统**：每个请求体字段按 role 解析：
- `id_ref` → `state['id']`（从 create 响应提取）
- `context` → `state[key]` 或 `state['create_body'][key]`（如 tenantId）
- `name` → `AT_{TS}_` 前缀 + 原始值
- `mutable` → `"自动修改_{TS}"`
- `static` → 直接复制 body_template 值

**JSONL 日志输出**：每次 API 调用写入一条结构化日志（`output/logs/<模块>_API测试.jsonl`），包含完整请求头/体、响应头/体、断言结果。

### 2.6 报告生成（lib/report/test_report.py）

从 JSONL 日志生成 Postman/Newman 风格 HTML 报告：

- **仪表盘**：总请求数、通过/失败数、通过率环形图
- **请求卡片**：每个 API 调用一张卡片，含方法标签（彩色）、URL、状态徽章
- **请求/响应详情**：可折叠的 Request Headers、Request Body、Response Status、Response Body
- **验证标记**：每个步骤的断言结果和消息

**用法**：
```bash
# 运行脚本 + 生成报告（一体化）
python lib/report/test_report.py projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py

# 报告输出到
output/reports/<模块>/<模块>_report_<timestamp>.html
```

### 2.7 测试套件运行器（run_suite.py）

扫描所有模块的测试脚本，依次执行并生成一份汇总 HTML 报告：

```bash
# 运行所有项目所有模块
python run_suite.py

# 运行指定项目
python run_suite.py --project ecm-compute

# 运行指定模块
python run_suite.py --project ecm-compute --modules 角色管理 用户管理
```

**输出**：`output/reports/suite_report_<timestamp>.html`

### 2.8 质量门禁（stage_validators.py）

每个阶段产出后自动校验，不通过则中止：

| 阶段 | 校验项 |
|------|--------|
| Stage 1 | 按钮数量、创建/删除覆盖 |
| Stage 2 | CRUD 分类完整性、调用计数、support 比例 |
| Stage 3 | 执行顺序长度、依赖链、状态断言 |
| Stage 4 | manifest 结构（response_contract、steps、body_field_roles） |

---

## 3. 鉴权机制

### 3.1 AuthSession（lib/auth/auth.py）

**设计原则**：Cookie 为唯一可信源，对齐 EcsCloud `session.js`。

**核心方法 `ensure_client(base_url)`**：

```
load cookies.json
    ↓
新鲜度检查（TTL=30min，meta.json 记录 savedAt）
    ↓ 新鲜                    ↓ 过期
  直接返回 client        probe_online (current-user 探活)
                              ↓ 成功              ↓ 失败
                         返回 client         login_with_browser (滑块登录)
                                                  ↓ 成功
                                            回写 cookies.json → 返回 client
```

### 3.2 凭据四级优先级

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | CLI `--user/--pass` 或函数参数 | 临时覆盖 |
| 2 | 环境变量 `AUTO_LOGIN_USER/PASS` | CI/CD |
| 3 | `auth.json`（`.gitignore` 排除） | 独立凭据文件 |
| 4 | `profile.yaml` credentials | 项目级配置 |

### 3.3 滑块登录

Playwright 打开登录页 → 填写凭证 → 点击认证 → 捕获验证码素材 → OpenCV 多算法投票识别缺口（10+ 算法，含边缘/颜色/梯度/饱和度）→ 计算拖拽距离 → 模拟人类拖拽（缓动+过冲+Y轴抖动）→ 最多 4 次重试。

### 3.4 并行安全

`AUTO_LOGIN=0` 时禁用自动登录。并行 Worker 只读 Cookie，避免多进程抢登覆盖同一份 `cookies.json`。

---

## 4. 运行方法

### 4.1 完整流程（一键发现）

```bash
cd D:\Mobile\API_AI_test

python -m module_discovery.run \
    --project ecm-compute \
    --url "/estack/web/estack/user-center/user-manage/user" \
    --module "用户管理" \
    --stage all
```

### 4.2 分阶段执行

```bash
# 仅 Stage 1（探测按钮）
python -m module_discovery.run --stage 1 --project ecm-compute --url "..." --module "用户管理"

# 仅 Stage 2（捕获 API，需先有 ui.json）
python -m module_discovery.run --stage 2 --project ecm-compute --url "..." --module "用户管理"

# Stage 3+4（离线分析 + 生成）
python -m module_discovery.run --stage 34 --project ecm-compute --module "用户管理"

# 仅 Stage 4（离线生成，需先有 analysis.json）
python -m module_discovery.run --stage 4 --project ecm-compute --module "用户管理"

# 离线模式（从已有数据重新分析+生成）
python -m module_discovery.run --offline --project ecm-compute --module "用户管理"
```

### 4.3 批量发现（多模块）

```bash
# 读 modules.yaml，发现所有已启用模块
python -m module_discovery.run --project ecm-compute --all-modules --stage all

# 按标签过滤
python -m module_discovery.run --project ecm-compute --all-modules --tag p0
```

`modules.yaml` 示例：
```yaml
modules:
  - name: 用户管理
    url: /estack/web/estack/user-center/user-manage/user
    enabled: true
    priority: p0
    tags: [core, iam]
```

### 4.4 运行生成的测试脚本

```bash
python projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py
```

脚本自动完成：鉴权 → 上下文获取 → create → 查询验证 → update → 查询验证 → delete → 查询验证。
日志自动写入 `output/logs/角色管理_API测试.jsonl`。

### 4.5 生成 HTML 报告

```bash
# 运行脚本 + 生成报告（一体化）
python lib/report/test_report.py projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py

# 报告输出到
output/reports/<模块>/<模块>_report_<timestamp>.html
```

### 4.6 运行测试套件（全模块汇总）

```bash
# 运行所有项目所有模块
python run_suite.py

# 运行指定项目所有模块
python run_suite.py --project ecm-compute

# 运行指定模块
python run_suite.py --project ecm-compute --modules 角色管理 用户管理
```

**汇总报告**：`output/reports/suite_report_<timestamp>.html`

### 4.7 CLI 参数速查

| 参数 | 说明 |
|------|------|
| `--project` | 项目 ID（对应 `projects/<id>`） |
| `--url` | 模块入口 URL 路径 |
| `--module` | 模块中文名 |
| `--stage` | 执行阶段：`1` / `2` / `34` / `4` / `all` |
| `--version` | 版本号（如 `v1.0.0`） |
| `--headless` | 无头模式 |
| `--offline` | 离线模式 |
| `--user` / `--pass` | 覆盖登录凭据 |
| `--all-modules` | 批量发现 |
| `--force` | 强制重跑（忽略增量缓存） |
| `--tag` | 按标签过滤模块 |
| `--capture-all` | 捕获所有 XHR/fetch 请求（默认只捕获匹配的 API） |
| `--max-recapture` | Stage 2 验证失败时的最大重试次数 |

### 4.8 单步骤调试模式

生成的测试脚本支持单步骤执行，方便调试特定 CRUD 操作：

```bash
# 只执行创建步骤
python projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py create

# 执行创建+删除
python projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py create delete

# 不传参数则执行全部步骤
python projects/ecm-compute/scripts/v1.0.0/角色管理_API测试.py
```

---

## 5. 版本管理与并行执行

### 5.1 版本隔离

```
projects/ecm-compute/
├── .api_version            # 当前版本（v1.0.0）
└── scripts/
    ├── v1.0.0/角色管理_API测试.py
    ├── v2.1.0/角色管理_API测试.py   # 升级后重跑，不覆盖 v1.0.0
    └── v3.1.0/...
```

版本号来源优先级：`API_VERSION` 环境变量 > `.api_version` 文件 > 默认 `v1.0.0`。

### 5.2 分模块并行（run_parallel.py）

```bash
# 并行跑所有模块脚本（默认 4 并发）
python -m module_discovery.run_parallel --project ecm-compute

# 6 并发 + 指定版本
python -m module_discovery.run_parallel --project ecm-compute --version v1.0.0 --parallel 6
```

**架构**：主进程预校验 Cookie（失效单线程登录一次）→ Worker 子进程 `AUTO_LOGIN=0` → 每模块完成后持久化结果 → 失败重试 + Cookie 中期刷新（每 30 分钟）→ 旧 Session 自动清理（保留最近 5 次）→ 失败归因台账集成。

---

## 6. 全站巡游（P0）

`discovery/` 实现全站级 API 离线扫描：

| 模块 | 功能 |
|------|------|
| `playwright_crawl.py` | 全站爬取：登录→菜单遍历→XHR 拦截→合并 |
| `probe_profile.py` | 零登录 profile 探测：前端框架指纹、API 文档端点扫描 |
| `form_extractor.py` | el-dialog 表单字段提取 |
| `entry_mapper.py` | URL 路径段→业务模块映射 |
| `combo_finder.py` | G2 有效参数组合发现（种子→错误码引导→正交扫描） |
| `global_miner.py` | G4 全局参数挖掘（跨模块共现 + 值稳定性） |

```bash
python run_p0.py --project ecm-compute              # P0 全站巡游
python run_p0.py --project ecm-compute --remerge     # 离线重合并
```

---

## 7. 代码生成器

`generators/` 从知识库产出多种格式：

| 生成器 | 输出 |
|--------|------|
| `gen_api_layer.py` | `api/<group>/<label>/<resource>.py` 薄函数 + `_client.json` |
| `gen_openapi.py` | OpenAPI 3.1 YAML/JSON + Postman Collection v2.1 |
| `gen_pytest.py` | `tests/<group>/test_<resource>_crud.py` pytest 测试 |
| `menu_tools.py` | 菜单树→两级中文目录映射 |

---

## 8. 知识库体系

### 8.1 全局知识库（config/）

**base_nav_kb.json**（v2.0）— XPath/CSS 选择器模板库：
- 单步：按钮/搜索/下载/菜单/Tab/复选框/输入框/创建入口/提交按钮
- 多步：el-select 级联/el-cascader/日期选择器
- 组合：表格行操作/下拉菜单/区域链接
- 断言：成功/错误 Toast/首行内容/字段值
- 兜底策略：playwright_role/text/xpath_class/dom_text_scan

**probe_lessons_kb.json**（v2.0）— 机器可读经验库：

| 区块 | 内容 |
|------|------|
| `systems` | 系统级配置（API 根路径、鉴权约定、字段加密、注入规则、已捕获契约） |
| `classify_rules` | 七级优先级端点分类规则 |
| `anti_patterns` | 30+ 反模式记录（严重度 + 来源 + 修复方案） |
| `success_playbook` | 标准六步发现流程 |
| `permission_gate_keywords` | 权限门禁关键词列表 |

**selector_patterns.json** — UI 元素定位模式库：

```json
{
  "table_row_selectors": {
    "main": ".el-table__body-wrapper tbody tr",
    "fixed_right": ".el-table__fixed-right .el-table__fixed-body-wrapper tbody tr",
    "module_source": ["用户管理", "角色管理"],
    "confidence": 0.95,
    "usage_count": 2,
    "last_verified": "2026-08-26"
  },
  "table_action_button": {
    "fallback_chain": [
      ".el-table__fixed-right td:last-child",
      ".el-table__fixed-body-wrapper td:last-child",
      ".el-table__body-wrapper td:last-child"
    ],
    "xpath_templates": "@base_nav_kb.json#/composite/table-action-button"
  },
  "form_fill_patterns": [
    {"match": "名称", "template": "{username}", "type": "text"},
    {"match": "邮箱", "template": "at_{ts}@test.com", "type": "email"},
    {"match": "手机", "template": "138{ts}", "type": "tel"}
  ]
}
```

**operation_patterns.json** — 操作流程模式库

**debug_strategies.json** — 诊断策略库

### 8.2 项目级知识库（projects/\<id>/kb/）

| 文件 | 内容 |
|------|------|
| `api_catalog.json` | 统一 API 编目（OpenAPI 3.1 + x-kb-* 扩展） |
| `menu_tree.json` | 菜单树结构 |
| `module_discovered/<模块>_ui.json` | Stage 1 按钮探测结果 |
| `module_discovered/<模块>.json` | Stage 2 API 捕获结果 |
| `module_discovered/<模块>_analysis.json` | Stage 3 分析结果 |
| `module_discovered/<模块>_manifest.json` | Stage 3 Manifest（测试清单） |
| `crud_overrides.json` | 手动 CRUD 分类覆盖 |

---

## 9. 运维工具

### 鉴权验证

```bash
python -m scripts._verify_auth --project ecm-compute
python -m scripts._verify_auth --project ecm-compute --force-probe
```

四步验证：读凭据配置 → 构建 AuthSession → 探活验证 → Cookie 状态检查。

### Cookie 保活

```bash
python scripts/keep_alive.py --project ecm-compute           # 默认 15 分钟
python scripts/keep_alive.py --project ecm-compute --interval 10
```

定时探活 + 单实例锁 + 崩溃自愈 + 看门狗超时检测。

### 失败台账

```bash
python scripts/view_ledger.py ecm-compute           # 控制台查看
python scripts/view_ledger.py ecm-compute --html     # HTML 报告（含趋势折线图）
```

五分法自动归因：

| 分类 | 含义 |
|------|------|
| `env` | 环境限制（配额/未开通/网络） |
| `script` | 测试脚本 bug |
| `product` | 产品代码缺陷（5xx/按钮缺失） |
| `flaky` | 偶发抖动 |
| `unknown` | 无法自动定性 |

### 导入验证

```bash
python scripts/verify_imports.py    # 验证所有 API 模块可被 importlib 正确加载
```

---

## 10. 项目数据布局

每个项目（`projects/<id>/`）的标准结构：

```
projects/<id>/
├── profile.yaml              # 系统配置（base_url/鉴权/验证码/凭据/菜单）
├── modules.yaml              # 模块发现清单
├── .api_version              # 当前版本标记
├── nav_kb.json               # 项目级导航知识库
│
├── api/                      # 生成的 API 薄函数（两级中文目录）
│   ├── _client.json          #   项目常量（base_url/auth_headers）
│   ├── 访问控制/用户管理/users.py
│   └── 未归类/...
│
├── captures/                 # 原始抓包数据
├── exports/                  # OpenAPI 3.1 + Postman Collection
│
├── scripts/                    # 版本化测试脚本
│   └── v1.0.0/
│       └── 角色管理_API测试.py
│
├── kb/                       # 知识库
│   ├── api_catalog.json
│   ├── menu_tree.json
│   └── module_discovered/    # 各阶段发现产物
│       ├── <模块>_ui.json         # Stage 1
│       ├── <模块>.json            # Stage 2
│       ├── <模块>_analysis.json   # Stage 3 分析
│       └── <模块>_manifest.json   # Stage 3 Manifest
│
├── output/
│   ├── config/               # cookies.json / context.json
│   ├── debug/                # 调试截图和抓包
│   ├── logs/                 # JSONL 结构化日志
│   └── reports/              # HTML 报告（Postman 风格 + 套件汇总）
│       └── <模块>/
│           └── <模块>_report_<timestamp>.html
│
└── tests/                    # 生成的 pytest 测试（两级目录镜像 api/）
```

---

## 11. 环境与依赖

### 运行环境

```
Python:  >=3.9（推荐 3.11+）
Node.js: 22.x（部分 JS 脚本参考用）
```

### 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

### 依赖清单

| 包 | 用途 |
|---|---|
| `playwright>=1.44` | 浏览器自动化 |
| `opencv-python-headless>=4.9` | 滑块验证码缺口识别 |
| `requests>=2.32` | HTTP 客户端（测试运行时） |
| `pyyaml>=6.0` | profile.yaml 解析 |
| `jsonpath-ng>=1.6` | JSON 路径查询（依赖解析） |

可选开发依赖：`pytest>=7.0`, `pytest-cov`, `flake8`

### 单元测试

```bash
python -m pytest tests/ -v     # 122 个测试
```

---

## 12. 关键设计决策

| 决策 | 理由 |
|------|------|
| Cookie 为唯一鉴权可信源 | localStorage 在 estack 中多为空；Cookie 纯净数组可直接 addCookies/httpx |
| KB 驱动注入而非代码硬编码 | 多项目复用，新项目只需更新 KB 配置 |
| importlib 文件路径加载 API 函数 | 项目目录名含连字符（如 `ecm-compute`），不能作为 Python 包名 |
| Manifest + Runtime Library 架构 | 生成薄脚本（~100 行），所有执行逻辑在共享运行时中，修改逻辑不需重新生成脚本 |
| Body 字段角色系统 | 将请求体字段按语义分类（id_ref/context/name/mutable/static），运行时自动解析 |
| 自动插入验证步骤 | Stage 3 在 CRUD 操作后自动插入查询验证（contains_id/not_contains_id），无需手动定义 |
| JSONL 结构化日志 | 每次 API 调用记录完整请求/响应详情，支持 Postman 风格报告生成 |
| 三层执行顺序推导（静态+时序+依赖） | 静态优先级保证 baseline，时序验证发现实际顺序，拓扑排序保证依赖约束 |
| 深层 ID 递归提取（最大 4 层） | 嵌套响应中的 ID 字段（如 `entity.data.userId`）不被遗漏 |
| 自诊断模式嵌入捕获流程 | 连续失败时自动触发 DOM 分析 + 选择器修复，减少人工介入 |
| KB 模板 fallback 链定位按钮 | `base_nav_kb.json` XPath 模板按 fixed-right → body-wrapper 顺序尝试，适配 Element UI 固定列 |
| Worker AUTO_LOGIN=0 | 避免多进程并发抢登覆盖同一份 cookies.json |
| 版本化 scripts 目录 | 不同版本脚本互不影响，重跑不覆盖历史 |
| 隐藏过滤器仅作用于按钮点击 | 表单输入字段需要填充不可见元素（如隐藏域），过滤会导致填值失败 |
| 覆盖层定位优先 + 全局降级 | 弹窗内按钮优先在弹窗容器内查找，失败时降级到全局避免误报 |
| Multi-Step 选项内嵌自动发现 | 展开面板后直接读取选项文本，避免"打开→读取→关闭→重新打开→选择"的额外开销 |
| 事件驱动等待替代固定等待 | `wait_for_loading_complete` 监听 7 种 loading 元素消失，比固定 `wait_for_timeout(3000~5000)` 更快更稳定 |
| lib/ 按功能分组 | 将通用库按鉴权/运行时/报告/浏览器分组，提高可维护性 |
| module_discovery/replay/ 独立子模块 | UI 操作回放引擎独立封装，便于复用和测试 |

---

## 13. 自诊断机制

### 13.1 触发条件

`capture_apis.py` 在关键失败点自动触发诊断：

| 触发条件 | 诊断策略 | 场景 |
|----------|----------|------|
| `find_data_row` 连续 3 次失败 | `row_not_found` | 表格行查找失败 |
| `click_row_button_v2` 连续 2 次失败 | `button_click_failed` | 行操作按钮点击失败 |
| Stage 2 验证失败且重试耗尽 | `stage2_validation_failed` | CRUD 完整性不达标 |

### 13.2 诊断流程（diagnostic_mode.py）

```
触发诊断 → 执行策略步骤
  ├─ screenshot       → 页面截图
  ├─ dump_table_structure → 表格 DOM 结构（wrappers/fixed_columns/row_count）
  ├─ test_selectors   → 从 selector_patterns.json 加载选择器逐一测试命中率
  ├─ search_marker    → page.textContent 查找 marker 在页面中的位置
  └─ check_pagination → 当前页码/总页数/是否有分页组件
        ↓
auto_fix=true → 发现新选择器 → 更新 selector_patterns.json → KBMerger 合并 → 重新尝试
auto_fix=false → 输出 diagnosis_summary.json → 人工介入
```

### 13.3 诊断输出

```
output/debug/{module}/diagnostic_{timestamp}/
├── screenshot.png              # 页面截图
├── table_structure.json        # 表格 DOM 结构
├── selector_tests.json         # 选择器命中率
├── marker_locations.json       # marker 文本位置
├── pagination.json             # 分页状态
└── diagnosis_summary.json      # 诊断结论 + 修复建议/已应用的修复
```

---

## 14. 经验库体系

### 14.1 三层架构

```
config/
├── base_nav_kb.json            # XPath/CSS 选择器模板（已有）
├── probe_lessons_kb.json       # 反模式库（已有）
├── selector_patterns.json      # 层1: UI 元素定位模式
├── operation_patterns.json     # 层2: 操作流程模式
└── debug_strategies.json       # 层3: 诊断策略
```

### 14.2 合并策略（kb_merger.py）

`KBMerger` 类处理跨模块经验合并：

| 规则 | 行为 |
|------|------|
| pattern_key 去重 | 同一 key 的模式合并，不覆盖 |
| module_source 合并 | set union，保留所有来源模块 |
| confidence 取最大值 | 高置信度优先 |
| selectors 合并 | 去重后保留顺序 |
| 冲突检测 | 同一 key 来自不同模块且 selector 不一致 → 标记 `conflict: true`，输出待审核列表 |

### 14.3 版本化与清理

- 每次更新 `selector_patterns.json` 时 `version` 自增
- `changelog` 记录修改（模块来源、修改内容、时间戳）
- `usage_count < 3` 且 `confidence < 0.5` 的模式标记 `deprecated`
- `last_verified` 超过 30 天的模式标记 `needs_verification`

---

**文档版本**: v1.0  
**最后更新**: 2026-09-07  
**维护者**: API_AI_test 项目组
