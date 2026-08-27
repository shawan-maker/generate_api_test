# API_AI_test — 无文档后台系统的 API 自动发现与测试生成框架

> 给定一个 Web 后台模块入口 URL + 登录凭证，自动完成：**按钮探测 → API 捕获 → 逻辑分析 → 脚本生成**，让没有 API 文档的后台系统无需人工抓包即可得到可运行的 API 测试脚本。

---

## 1. 架构概览

```
API_AI_test/
│
├── module_discovery/          ★ 核心：四阶段模块级发现引擎
│   ├── run.py                   CLI 入口
│   ├── discover_ui.py           Stage 1: 按钮/元素探测（含固定列扫描 + 重试）
│   ├── capture_apis.py          Stage 2: API 拦截捕获（动态操作列表）
│   ├── analyze_flow.py          Stage 3: 逻辑分析（时序+依赖排序）
│   ├── gen_test.py              Stage 4: 脚本生成（Jinja2 模板 + fallback）
│   ├── request_interceptor.py   HTTP 拦截 + KB 驱动注入（支持 --capture-all）
│   ├── button_driver.py         按钮点击驱动（KB 模板 fallback 链）
│   ├── form_filler.py           表单智能填充（type-aware 动态构建）
│   ├── endpoint_classifier.py   端点分类去重
│   ├── wait_helpers.py          事件驱动等待
│   ├── stage_validators.py      质量门禁
│   ├── diagnostic_mode.py       自诊断：失败时自动 DOM 分析 + 尝试修复
│   ├── kb_merger.py             知识库合并器（跨模块去重 + 冲突检测）
│   ├── version.py               版本管理
│   ├── run_parallel.py          并行执行
│   ├── templates/               Jinja2 模板（4 个 .j2 文件）
│   └── const.py                 常量定义
│
├── lib/                       通用库
│   ├── auth.py                  鉴权引擎（Cookie 优先 + 滑块兜底）
│   ├── api_client.py            API 客户端 + importlib 函数加载
│   ├── slider.py                滑块验证码（多算法投票）
│   ├── catalog.py               API 编目合并 → OpenAPI 3.1 IR
│   ├── resolver.py              前置依赖解析（Test Data Fabric）
│   ├── run_history.py           运行历史 + 五分法失败归因
│   ├── report_html.py           日志 → HTML 报告
│   └── utils.py                 safe_write / session_id
│
├── discovery/                 P0 全站巡游（全站 API 离线扫描）
├── generators/                代码生成器（API 层 / OpenAPI / pytest）
├── config/                    全局知识库（选择器模板 + 经验库）
├── plugins/                   pytest 插件
├── scripts/                   工具与调试脚本
├── projects/                  多项目隔离工作空间
└── tests/                     框架单元测试（106 tests）
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
       ↓
Stage 4: gen_test       → flows/<ver>/<模块>_API测试.py  可执行测试脚本
```

---

## 2. 四阶段发现引擎

### 2.1 Stage 1：按钮探测（discover_ui.py）

用 CSS 选择器地毯式扫描页面所有可见按钮/链接/菜单项：

- **按 DOM 位置分类**：工具栏 / 行操作 / 弹窗 / 菜单
- **固定列扫描**：自动检测 `.el-table__fixed-right` 和 `.el-table__fixed-left`，行操作按钮标记 `source` 字段（`main`/`fixed-right`/`fixed-left`）
- **页面结构快照**：`_snapshot_page_structure()` 在扫描前获取表格结构（是否含固定列、操作列索引、表格 wrapper 数量），Stage 2 据此选择正确的 wrapper
- **下拉菜单动态发现**：硬编码触发词（"更多"/"操作"等 5 个）+ DOM 扫描 `.el-dropdown` 元素动态补充
- **重试机制**：`discover_all(max_retries=2)`，验证不通过则等待 2 秒后重试
- 扫描「更多」「操作」等下拉菜单子项
- 点击「创建」按钮扫描表单字段（label / type / required / placeholder）
- 输出 `<模块>_ui.json`（含 `page_structure` 元数据）

### 2.2 Stage 2：API 捕获（capture_apis.py）

四个子模块协作完成 API 拦截：

| 子模块 | 职责 |
|--------|------|
| `request_interceptor.py` | Playwright 请求/响应监听，过滤 API，收集调用记录和响应样本。支持 `--capture-all` 模式捕获全部 XHR/fetch |
| `button_driver.py` | 驱动按钮点击（KB 模板 fallback 链：`base_nav_kb.json` → 硬编码选择器）。`click_row_button_v2` 从 KB 加载 `table-action-button` XPath 模板，按 fixed-right → body-wrapper → fixed-body-wrapper 顺序尝试。`find_data_row` 支持分页感知（自动翻页最多 3 页查找目标行） |
| `form_filler.py` | 表单字段扫描与智能填充（type-aware 动态构建：text→`AT_test_{ts}`，number→1，未匹配字段按 inputType 生成默认值） |
| `endpoint_classifier.py` | 六级优先级端点分类与去重 |

**动态操作列表**：`_build_operations_from_ui(ui_result)` 从 Stage 1 结果动态构建行操作列表，按 CRUD 优先级排序。仅在动态构建为空时 fallback 到硬编码默认列表。

**KB 驱动注入**：从 `config/probe_lessons_kb.json` 加载系统级配置，monkey-patch 浏览器的 `XMLHttpRequest.send` 和 `fetch`，对匹配请求自动补全缺失字段（如 `tenantId`/`adminId`）。代码零硬编码。

**权限门禁识别**：响应体匹配 `permission_gate_keywords`（"您没有/请先授权/forbidden"），命中即标注"需高权限账号"。

**close_dialog 快速关闭**：3 秒超时检测关闭按钮 + Escape 兜底，不再使用 30 秒默认超时。

**自诊断触发**：当行查找或按钮点击连续失败时，自动触发 `diagnostic_mode.py` 进行 DOM 结构分析并尝试修复（详见 §13 自诊断机制）。

**输出**：`<模块>.json`（端点清单 + 请求/响应样本 + permission_gates）

### 2.3 Stage 3：逻辑分析（analyze_flow.py）

五步推理流水线：

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

**输出**：`<模块>_analysis.json`

### 2.4 Stage 4：脚本生成（gen_test.py）

生成完整可执行的 Python 测试脚本：

- **Jinja2 模板化**：4 个模板文件（`templates/*.j2`）+ `_render_with_jinja()` 渲染，渲染失败自动 fallback 到字符串拼接，保证 Jinja2 不可用时零影响
  - `api_function.py.j2` — 单个 API 调用函数（含结构化请求/响应日志）
  - `crud_step.py.j2` — 单个 CRUD 步骤函数定义
  - `main_function.py.j2` — STEPS 字典 + main() 主流程
  - `browser_helpers.py.j2` — 浏览器创建/授权函数（`{% raw %}` 隔离 JS 花括号）
- **浏览器操作外置**：`lib/browser_ops.py` 提供 `browser_create_resource` 和 `browser_authorize_resource` 通用函数，生成脚本只引用 import
- **Cookie domain 动态提取**：`_extract_domain(base_url)` 从 URL 解析域名，不再硬编码 IP
- **Query 函数多策略选择**：`_select_query_fn` 三级优先——列表查询（含 list/page/search）→ 详情查询（含 ID 占位）→ 任意 query
- **单步骤执行模式**：生成脚本内置 `STEPS` 字典 + `sys.argv` 解析，支持 `python script.py create query` 只执行指定步骤
- `AuthSession` 鉴权初始化（Cookie 优先 + 滑块兜底）
- 每个端点对应一个 API 函数，自动打印请求/响应摘要
- 主流程：CRUD 完整生命周期 + **四层断言**
  - HTTP 状态码
  - 业务 `success==true`
  - 关键字段非空
  - 状态值符合 CRUD 语义（如 `ENABLE`→`LOCKED`→`ENABLE`）

**输出**：`flows/<version>/<模块>_API测试.py`

### 2.5 质量门禁（stage_validators.py）

每个阶段产出后自动校验，不通过则中止：

| 阶段 | 校验项 |
|------|--------|
| Stage 1 | 按钮数量、创建/删除覆盖 |
| Stage 2 | CRUD 分类完整性、调用计数、support 比例 |
| Stage 3 | 执行顺序长度、依赖链、状态断言 |
| Stage 4 | 脚本结构（test_ 函数、断言数量、异常处理） |

---

## 3. 鉴权机制

### 3.1 AuthSession（lib/auth.py）

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
COOKIE_DIR=projects/ecm-compute/output/config AUTO_LOGIN=1 \
    python projects/ecm-compute/flows/v1.0.0/用户管理_API测试.py
```

### 4.5 生成 HTML 报告

```bash
# 运行脚本，日志落盘
python projects/ecm-compute/flows/v1.0.0/用户管理_API测试.py \
    > projects/ecm-compute/output/logs/report_用户管理_$(date +%Y%m%d_%H%M%S).log 2>&1

# 日志 → HTML（Postman htmlextra 风格：仪表盘 + 环形图 + 请求卡片）
python -m lib.report_html --log projects/ecm-compute/output/logs/report_用户管理_xxx.log
```

### 4.6 CLI 参数速查

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

### 4.7 单步骤调试模式

生成的测试脚本支持单步骤执行，方便调试特定 CRUD 操作：

```bash
# 只执行创建步骤
python projects/ecm-compute/flows/v1.0.0/用户管理_API测试.py create

# 执行创建+查询
python projects/ecm-compute/flows/v1.0.0/用户管理_API测试.py create query

# 不传参数则执行全部步骤
python projects/ecm-compute/flows/v1.0.0/用户管理_API测试.py
```

脚本内置 `STEPS` 字典映射步骤名到函数，`sys.argv` 解析决定执行哪些步骤。

---

## 5. 版本管理与并行执行

### 5.1 版本隔离

```
projects/ecm-compute/
├── .api_version            # 当前版本（v1.0.0）
└── flows/
    ├── v1.0.0/用户管理_API测试.py
    ├── v2.1.0/用户管理_API测试.py   # 升级后重跑，不覆盖 v1.0.0
    └── v3.1.0/...
```

版本号来源优先级：`API_VERSION` 环境变量 > `.api_version` 文件 > 默认 `v1.0.0`。

```bash
# 指定版本运行
python -m module_discovery.run --version v2.1.0 --project ecm-compute --module "用户管理" --stage all

# 版本入口转发器
python -m module_discovery.run_with_version v2.1.0 --project ecm-compute ...
```

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

**operation_patterns.json** — 操作流程模式库：

```json
{
  "create_role_estack": {
    "module_pattern": "角色.*管理|role.*manage",
    "steps": [
      {"action": "click_toolbar", "button": "创建角色"},
      {"action": "fill_form", "fields": ["角色名称", "角色编码", "角色描述"]},
      {"action": "select_tree", "selector": ".el-tree", "strategy": "select_root"},
      {"action": "submit", "button": "确定"}
    ],
    "expected_apis": ["POST /policies", "GET /policies/list"],
    "success_count": 1,
    "last_verified": "2026-08-26"
  },
  "row_operations_estack": {
    "direct_buttons": ["编辑", "删除"],
    "dropdown_trigger": "更多",
    "dropdown_items": ["冻结", "启用", "锁定", "解锁", "重置密码", "授权"],
    "special_handling": {
      "删除": "confirm_message_box",
      "授权": "full_page_jump"
    }
  }
}
```

**debug_strategies.json** — 诊断策略库：

```json
{
  "row_not_found": {
    "trigger": "find_data_row returns None after 3 retries",
    "steps": [
      {"action": "screenshot", "scope": "full_page"},
      {"action": "dump_table_structure"},
      {"action": "test_selectors", "selectors_from": "selector_patterns.json"},
      {"action": "search_marker_in_page", "method": "textContent"},
      {"action": "check_pagination"}
    ],
    "auto_fix": true,
    "fix_target": "selector_patterns.json"
  },
  "button_click_failed": {
    "trigger": "click_row_button_v2 returns False after 2 retries",
    "steps": [
      {"action": "screenshot", "scope": "row_area"},
      {"action": "dump_row_cells"},
      {"action": "check_fixed_right"}
    ],
    "auto_fix": true
  }
}
```

### 8.2 项目级知识库（projects/\<id>/kb/）

| 文件 | 内容 |
|------|------|
| `api_catalog.json` | 统一 API 编目（OpenAPI 3.1 + x-kb-* 扩展） |
| `menu_tree.json` | 菜单树结构 |
| `module_discovered/<模块>_ui.json` | Stage 1 按钮探测结果 |
| `module_discovered/<模块>.json` | Stage 2 API 捕获结果 |
| `module_discovered/<模块>_analysis.json` | Stage 3 分析结果 |
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
├── flows/                    # 版本化测试脚本
│   └── v1.0.0/
│       └── 用户管理_API测试.py
│
├── kb/                       # 知识库
│   ├── api_catalog.json
│   ├── menu_tree.json
│   └── module_discovered/    # 各阶段发现产物
│
├── output/
│   ├── config/               # cookies.json / meta.json / auth.json / run_history
│   ├── debug/                # 调试截图和抓包
│   ├── logs/                 # 日志文件
│   └── reports/              # HTML 报告
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
| `httpx>=0.27` | HTTP 客户端 |
| `pyyaml>=6.0` | profile.yaml 解析 |
| `jsonpath-ng>=1.6` | JSON 路径查询（依赖解析） |

可选开发依赖：`pytest>=7.0`, `pytest-cov`, `flake8`, `jinja2`

### Docker

```bash
docker build -t api-ai-test .
docker run api-ai-test   # 默认运行 pytest tests/ -v
```

### 单元测试

```bash
python -m pytest tests/ -v     # 106 个测试
```

---

## 12. 关键设计决策

| 决策 | 理由 |
|------|------|
| Cookie 为唯一鉴权可信源 | localStorage 在 estack 中多为空；Cookie 纯净数组可直接 addCookies/httpx |
| KB 驱动注入而非代码硬编码 | 多项目复用，新项目只需更新 KB 配置 |
| importlib 文件路径加载 API 函数 | 项目目录名含连字符（如 `ecm-compute`），不能作为 Python 包名 |
| 浏览器驱动创建/授权 | 前端 RSA 加密 + 穿梭框交互，纯 HTTP 无法复现 |
| 四层断言而非仅检查 success | 验证 HTTP 状态 + 业务成功 + 数据完整性 + 状态语义 |
| Worker AUTO_LOGIN=0 | 避免多进程并发抢登覆盖同一份 cookies.json |
| 版本化 flows 目录 | 不同版本脚本互不影响，重跑不覆盖历史 |
| Jinja2 模板 + fallback 字符串拼接 | 模板化提高可维护性，Jinja2 不可用时自动降级到字符串拼接，零影响 |
| 浏览器操作外置到 lib/browser_ops.py | 通用函数（`browser_create_resource`/`browser_authorize_resource`）与生成脚本解耦 |
| KB 模板 fallback 链定位按钮 | `base_nav_kb.json` XPath 模板按 fixed-right → body-wrapper 顺序尝试，适配 Element UI 固定列 |
| 三层执行顺序推导（静态+时序+依赖） | 静态优先级保证 baseline，时序验证发现实际顺序，拓扑排序保证依赖约束 |
| 深层 ID 递归提取（最大 4 层） | 嵌套响应中的 ID 字段（如 `entity.data.userId`）不被遗漏 |
| 自诊断模式嵌入捕获流程 | 连续失败时自动触发 DOM 分析 + 选择器修复，减少人工介入 |
| 统一经验库三层架构 | selector_patterns（定位）→ operation_patterns（流程）→ debug_strategies（诊断），跨模块复用 |
| KBMerger 合并策略 | 按 pattern_key 去重、module_source 取并集、confidence 取最大值、冲突标记待审核 |

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

## 15. 设计文档索引

| 文档 | 路径 |
|------|------|
| 总体架构设计 | `docs/design/方案设计.md` |
| 四阶段发现引擎全面优化方案 | `docs/design/four-stage-improvement.md` |
| 四阶段流水线详细设计 | `docs/design/方案设计_模块级API发现.md` |
| 鉴权与自动重登机制 | `docs/design/登录鉴权与自动重登机制.md` |
| 用户管理探测踩坑记录 | `docs/design/经验总结_用户管理API探测踩坑与成功范式.md` |
| 目录结构说明 | `docs/design/项目结构.md` |
