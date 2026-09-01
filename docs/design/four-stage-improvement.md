# 四阶段发现引擎全面优化方案

## 文档信息

- 路径: `docs/design/four-stage-improvement.md`
- 创建日期: 2026-08-26
- 最后更新: 2026-08-28
- 状态: **已实施完成**

## 目标

将四阶段发现引擎（UI探测 → API捕获 → 流程分析 → 脚本生成）从"能跑通单个模块"提升到"跨模块可复用、自适应、自诊断"的自动化水平。

---

## 一、当前状态总结

### 已完成的基础修复（上一轮）
- ✅ Fix #1: `interceptor._calls` → `interceptor.calls`（根因修复）
- ✅ Fix #2: Stage 2 验证门控 + `--force-gen`
- ✅ Fix #3: 调试工件自动转储
- ✅ Fix #4: 树形选择器递归展开
- ✅ Fix #5: Stage 2 重试循环（`--max-recapture`）
- ✅ `find_data_row()` 支持多 wrapper 搜索

### Manifest + Runtime Library 架构（2026-08-28 完成）

**核心改造**：将硬编码逻辑从生成脚本中分离，通过 manifest JSON 驱动运行时执行。

| 组件 | 状态 | 说明 |
|------|------|------|
| `lib/test_runtime.py` | ✅ 完成 | 通用运行时（ResponseParser + StepExecutor + TestRunner） |
| `analyze_flow.build_manifest()` | ✅ 完成 | Manifest 构建 + 响应约定自动发现 |
| `gen_test.py` 薄脚本模式 | ✅ 完成 | 从 940 行 → 107 行（-89%），旧代码完全移除 |
| `lib/test_report.py` | ✅ 完成 | JSONL 日志 → Postman/Newman 风格 HTML 报告 |
| `run_suite.py` | ✅ 完成 | 测试套件运行器（扫描所有模块，依次执行并生成汇总报告） |
| `stage_validators.py` | ✅ 完成 | 适配 manifest 架构检查 |

**架构对比**：
- **旧架构**: 527 行硬编码脚本 (fat script) — 已全部删除
- **新架构**: ~107 行 manifest 驱动脚本 (thin script)

### 各阶段问题状态

| 阶段 | 原始问题数 | 已解决 | 状态 |
|------|-----------|--------|------|
| Stage 1 | 4 | 4 | ✅ 完成 |
| Stage 2 | 12 | 12 | ✅ 完成 |
| Stage 3 | 5 | 5 | ✅ 完成 |
| Stage 4 | 5 | 5 | ✅ 完成 |
| 跨阶段 | 4 | 4 | ✅ 完成 |

---

## 二、Stage 1 优化：UI 探测增强 ✅ 已完成

### 2.1 实施内容

| # | 问题 | 解决方案 | 状态 |
|---|------|----------|------|
| S1-1 | 行操作按钮只在 `.el-table__body-wrapper` 中扫描 | `_snapshot_page_structure()` 检测固定列，`source` 字段标记来源 | ✅ |
| S1-2 | 下拉菜单触发文本硬编码 | 动态发现 `.el-dropdown` 元素 + `base_nav_kb.json` 模板补充 | ✅ |
| S1-3 | 表单扫描只在检测到"创建"按钮时触发 | 保留，符合实际需求 | ✅ |
| S1-4 | 无重试机制 | `discover_all(max_retries=2)` 验证不通过等待重试 | ✅ |

### 2.2 输出格式

`<模块>_ui.json` 包含 `page_structure` 元数据：
- `hasFixedLeft` / `hasFixedRight`: 固定列检测结果
- `columnCount`: 表格列数
- `operationColumnIndex`: 操作列索引
- `tableWrappers`: 表格 wrapper 数量

行操作按钮标记 `source` 字段（`main`/`fixed-right`/`fixed-left`），Stage 2 据此选择正确的 wrapper。

---

## 三、Stage 2 优化：API 捕获增强 ✅ 已完成

### 3.1 实施内容

| # | 问题 | 解决方案 | 状态 |
|---|------|----------|------|
| S2-1 | 行操作列表硬编码（8个操作） | `_build_operations_from_ui(ui_result)` 动态构建 | ✅ |
| S2-2 | `click_row_button_v2` 搜索固定列选择器错误 | KB 模板 fallback 链：fixed-right → body-wrapper → fixed-body-wrapper | ✅ |
| S2-3 | `fill_create_form` 的 fill_data 硬编码 | type-aware 动态构建：text→`AT_test_{ts}`，number→1 | ✅ |
| S2-4 | 无分页处理 | `find_data_row` 支持分页感知（自动翻页最多 3 页） | ✅ |
| S2-5 | 授权页导航URL硬编码 | 从 `target_url` 推导回列表 URL | ✅ |
| S2-6 | `close_dialog` 30秒超时 | 3 秒超时检测关闭按钮 + Escape 兜底 | ✅ |
| S2-7 | 请求拦截只匹配 `/estack/api` | `--capture-all` 参数，默认仍使用过滤模式 | ✅ |
| S2-8 | 按钮点击误命中隐藏/禁用元素 | `const.HIDDEN_FILTERS` 三套 XPath 过滤谓词（element-ui/ant-design/universal），`_append_hidden_filter()` 自动处理三种 XPath 模式 | ✅ |
| S2-9 | 弹窗内按钮与主页面同名按钮冲突 | `detect_active_overlay_js()` + `apply_overlay_scope()` 优先在弹窗/抽屉容器内定位，失败降级全局 | ✅ |
| S2-10 | iframe 内按钮不可达 | `_try_click_frame()` 遍历 `page.frames` 在主框架和所有 iframe 中查找 | ✅ |
| S2-11 | 按钮点击后固定等待 3-5 秒 | `wait_for_loading_complete()` 事件驱动等待 7 种 loading 元素消失 | ✅ |
| S2-12 | el-select/el-cascader 无法选择具体选项 | `_discover_first_option()` 自动读取首个可见选项 + `_cascader_discover_and_select()` 逐级发现 | ✅ |

### 3.2 KB 驱动注入

从 `config/probe_lessons_kb.json` 加载系统级配置，monkey-patch 浏览器的 `XMLHttpRequest.send` 和 `fetch`，对匹配请求自动补全缺失字段（如 `tenantId`/`adminId`）。代码零硬编码。

### 3.3 自诊断触发

当行查找或按钮点击连续失败时，自动触发 `diagnostic_mode.py` 进行 DOM 结构分析并尝试修复（详见 §七 自诊断机制）。

---

## 四、Stage 3 优化：流程分析增强 ✅ 已完成

### 4.1 实施内容

| # | 问题 | 解决方案 | 状态 |
|---|------|----------|------|
| S3-1 | CRUD 顺序纯静态 | 三层策略：静态优先级 + 时序验证 + 依赖约束拓扑排序 | ✅ |
| S3-2 | 依赖分析只看顶层字段 | `_extract_ids_recursive` 递归遍历响应 JSON（最大深度 4 层） | ✅ |
| S3-3 | 状态字段检测只查顶层 entity | 同时支持 dict 和 list 类型的 entity 响应 | ✅ |
| S3-4 | `stage_validators.py` 字段名不匹配 | 修复为 `crud_order` / `api_by_button` | ✅ |
| S3-5 | `_find_id_source` 总返回 "create" | 从响应样本中实际查找 ID 来源 | ✅ |

### 4.2 Manifest 构建（新增）

`build_manifest()` 将分析结果 + 响应约定发现合并为完整测试清单：

```python
manifest = build_manifest(flow, capture_result, profile, module_name, target_url)
```

**自动发现机制**：
- `_discover_response_contract()`: 响应信封键、成功判断逻辑、列表/总数键、ID 字段
- `_classify_body_fields()`: 为每个请求体字段标注角色（id_ref/context/name/mutable/static）
- 自动插入验证步骤：在 create/update/delete 后插入查询验证（contains_id/not_contains_id 断言）

**输出**：`<模块>_manifest.json`（含 response_contract、auth_profile、steps、state_assertions）

---

## 五、Stage 4 优化：脚本生成增强 ✅ 已完成

### 5.1 实施内容

| # | 问题 | 解决方案 | 状态 |
|---|------|----------|------|
| S4-1 | 浏览器操作内联 350 行 | 不再需要，manifest 架构下脚本只有 ~100 行 | ✅ |
| S4-2 | Jinja2 模板未使用 | 不再需要，薄脚本直接拼接 manifest JSON | ✅ |
| S4-3 | Cookie domain 硬编码 | 运行时从 base_url 动态提取 | ✅ |
| S4-4 | Query 函数选择逻辑脆弱 | 自动插入验证步骤，无需手动选择 query 函数 | ✅ |
| S4-5 | 生成脚本是单文件单体 | 薄脚本 + 共享运行时，支持单步骤执行 | ✅ |

### 5.2 新架构：Manifest + Runtime Library

**生成的脚本结构**（~100 行）：
```python
"""
角色管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-08-28 11:30:32
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/role
执行流程:
  1. 创建
  2. 查询验证（创建后）
  3. 修改
  4. 查询验证（修改后）
  5. 删除
  6. 查询验证（删除后）
"""

import sys, json
from pathlib import Path

# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）
_root = Path(__file__).resolve()
while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

from lib.test_runtime import TestRunner

MANIFEST = { ... }  # 嵌入的完整测试清单

if __name__ == "__main__":
    runner = TestRunner(MANIFEST)
    steps = sys.argv[1:] if len(sys.argv) > 1 else None
    runner.run(steps_filter=steps)
```

### 5.3 运行时引擎（lib/test_runtime.py）

| 类 | 职责 |
|---|---|
| `ResponseParser` | 根据 `response_contract` 解析 API 响应 |
| `StepExecutor` | 执行单个 CRUD 步骤 + 写 JSONL 日志 |
| `TestRunner` | 编排完整测试流程 |

**Body 字段角色系统**：
- `id_ref` → `state['id']`（从 create 响应提取）
- `context` → `state[key]` 或 `state['create_body'][key]`（如 tenantId）
- `name` → `AT_{TS}_` 前缀 + 原始值
- `mutable` → `"自动修改_{TS}"`
- `static` → 直接复制 body_template 值

### 5.4 JSONL 日志输出

每次 API 调用写入一条结构化日志（`output/logs/<模块>_API测试.jsonl`）：

```json
{
  "type": "api_call",
  "seq": 1,
  "step_action": "create",
  "step_label": "创建",
  "request": {
    "method": "POST",
    "url": "https://10.151.61.248/estack/api/estack/draco/v1/policies",
    "headers": { "Authorization": "Bearer xxx", ... },
    "body": { "tenantId": "...", "policyName": "AT_xxx_autotest" }
  },
  "response": {
    "status": 200,
    "headers": { "content-type": "application/json" },
    "body": { "success": true, "entity": { "id": "xxx", ... } }
  },
  "assertion": "passed",
  "assertion_message": ""
}
```

### 5.5 报告生成（lib/test_report.py）

从 JSONL 日志生成 Postman/Newman 风格 HTML 报告：

```bash
python lib/test_report.py projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py
```

**报告内容**：
- 仪表盘：总请求数、通过/失败数、通过率环形图
- 请求卡片：每个 API 调用一张卡片，含方法标签（彩色）、URL、状态徽章
- 请求/响应详情：可折叠的 Request Headers、Request Body、Response Status、Response Body
- 验证标记：每个步骤的断言结果和消息

**输出**：`output/reports/<模块>/<模块>_report_<timestamp>.html`

### 5.6 测试套件运行器（run_suite.py）

扫描所有模块的测试脚本，依次执行并生成一份汇总 HTML 报告：

```bash
python run_suite.py                                    # 运行所有项目所有模块
python run_suite.py --project ecm-compute              # 运行指定项目
python run_suite.py --project ecm-compute --modules 角色管理 用户管理  # 指定模块
```

**输出**：`output/reports/suite_report_<timestamp>.html`

---

## 六、经验库体系设计 ✅ 已完成

### 6.1 三层架构

```
config/
├── base_nav_kb.json            # XPath/CSS 选择器模板（已有）
├── probe_lessons_kb.json       # 反模式库（已有）
├── selector_patterns.json      # 层1: UI 元素定位模式
├── operation_patterns.json     # 层2: 操作流程模式
└── debug_strategies.json       # 层3: 诊断策略
```

### 6.2 合并策略（kb_merger.py）

`KBMerger` 类处理跨模块经验合并：

| 规则 | 行为 |
|------|------|
| pattern_key 去重 | 同一 key 的模式合并，不覆盖 |
| module_source 合并 | set union，保留所有来源模块 |
| confidence 取最大值 | 高置信度优先 |
| selectors 合并 | 去重后保留顺序 |
| 冲突检测 | 同一 key 来自不同模块且 selector 不一致 → 标记 `conflict: true` |

### 6.3 版本化与清理

- 每次更新 `selector_patterns.json` 时 `version` 自增
- `changelog` 记录修改（模块来源、修改内容、时间戳）
- `usage_count < 3` 且 `confidence < 0.5` 的模式标记 `deprecated`
- `last_verified` 超过 30 天的模式标记 `needs_verification`

---

## 七、自诊断机制 ✅ 已完成

### 7.1 触发条件

`capture_apis.py` 在关键失败点自动触发诊断：

| 触发条件 | 诊断策略 | 场景 |
|----------|----------|------|
| `find_data_row` 连续 3 次失败 | `row_not_found` | 表格行查找失败 |
| `click_row_button_v2` 连续 2 次失败 | `button_click_failed` | 行操作按钮点击失败 |
| Stage 2 验证失败且重试耗尽 | `stage2_validation_failed` | CRUD 完整性不达标 |

### 7.2 诊断流程（diagnostic_mode.py）

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

### 7.3 诊断输出

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

## 八、验证结果

### 端到端验证（2026-08-28）

```bash
# Stage 3+4 离线重新生成
MSYS_NO_PATHCONV=1 python -m module_discovery.run --project ecm-compute \
    --url "/estack/web/estack/user-center/user-manage/role" \
    --module "角色管理" --stage 34 --offline --headless

# 运行生成的脚本
python "projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py"

# 生成 HTML 报告
python lib/test_report.py "projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py"
```

**结果**：
- ✅ Stage 3 验证通过: 3 个分类, 3 个映射
- ✅ Stage 4 验证通过: 373 行 manifest 驱动脚本
- ✅ 脚本执行: 6/6 步骤全部通过（create → 查询验证 → update → 查询验证 → delete → 查询验证）
- ✅ 报告生成: `output/reports/角色管理/角色管理_report_20260828_113241.html`

### 关键验证点

| 验证项 | 结果 |
|--------|------|
| Phase 3 根据 Phase 2 结果输出运行逻辑 | ✅ 正确：crud_order、body_field_roles、自动插入验证步骤 |
| Phase 4 生成脚本运行后生成 HTML 报告 | ✅ 正确：路径 `output/reports/<模块>/`，内容含完整请求/响应详情 |
| JSONL 日志结构完整性 | ✅ 包含 type、seq、step_action、request、response、assertion |
| 报告仪表盘数据准确性 | ✅ 6 总请求、6 通过、0 失败、100% 通过率 |

---

## 九、已解决的问题汇总（28 项）

### 基础设施问题 (1-4)
1. ✅ 拦截器 `_calls` vs `calls` 属性名
2. ✅ `validate_stage2` key 不匹配 (`classified` vs `by_category`)
3. ✅ 表单提交时序 (`calls_before_submit` 提前取值)
4. ✅ 树形选择器递归展开 (`_handle_tree_selectors`)

### 流程控制问题 (5-9)
5. ✅ Stage 2 验证门控 (失败阻止生成，`--force-gen` 覆盖)
6. ✅ Stage 2 重试机制 (`--max-recapture`)
7. ✅ 调试工件自动转储 (`_dump_debug_artifacts`)
8. ✅ `find_data_row()` 固定列支持
9. ✅ 行操作按钮定位 (Element UI 固定列)

### 捕获与生成问题 (10-14)
10. ✅ 编辑 API 捕获 (表单填充 + PUT/PATCH 触发)
11. ✅ `_find_id_source` 硬编码 → `id_field_candidates` + `source_crud`
12. ✅ Jinja2 模板化 (4 个模板集成) → 后改为 manifest 架构
13. ✅ 单步骤执行模式 (`STEPS` 字典 + `sys.argv`)
14. ✅ URL 查询参数捕获 (`urlparse` + `query_params` 字段)

### Manifest 架构问题 (15-18)
15. ✅ `roleId` 字段分类错误 (static → id_ref)
16. ✅ `test_runtime.py` 硬编码路径 (`projects/ecm-compute`)
17. ✅ `probe_url` 和 `context_fields` 为空 (estack 默认值)
18. ✅ JSON null → Python None 转换

### 硬编码清理 (19-23)
19. ✅ Phase 2-4 所有发现常量集中到 const.py
20. ✅ gen_test.py 旧架构代码完全删除 (940→107 行)
21. ✅ test_runtime.py 泛化默认值 + 移除 estack 前缀 env var
22. ✅ run.py 移除硬编码 URL + 登录配置数据化
23. ✅ stage_validators.py 适配新架构

### Locator 增强 (24-28)
24. ✅ 按钮点击误命中隐藏/禁用元素 (`HIDDEN_FILTERS` + `_append_hidden_filter()` 三套 XPath 过滤谓词)
25. ✅ 弹窗内按钮与主页面同名按钮冲突 (`detect_active_overlay_js()` + `apply_overlay_scope()` 覆盖层优先定位)
26. ✅ iframe 内按钮不可达 (`_try_click_frame()` 遍历主框架和所有 iframe)
27. ✅ 按钮点击后固定等待 3-5 秒 (`wait_for_loading_complete()` 事件驱动等待 7 种 loading 元素)
28. ✅ el-select/el-cascader 无法选择具体选项 (`_discover_first_option()` + `_cascader_discover_and_select()` 自动发现)

---

## 十、关键文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `lib/test_runtime.py` | **新建** | 通用运行时（ResponseParser + StepExecutor + TestRunner） |
| `lib/test_report.py` | **新建** | JSONL 日志 → Postman/Newman 风格 HTML 报告 |
| `module_discovery/analyze_flow.py` | **新增函数** | `build_manifest()` + 4 个 discover 函数 + `_classify_body_fields()` |
| `module_discovery/gen_test.py` | **重写** | 仅 manifest 模式（107 行），旧代码完全删除 |
| `module_discovery/run.py` | **改造** | `run_stage34()` 调用 `build_manifest()` 并传给 `generate_script()` |
| `module_discovery/stage_validators.py` | **适配** | 新架构检查项 |
| `module_discovery/const.py` | **新增** | 集中管理所有发现常量 + `HIDDEN_FILTERS` / `OVERLAY_SELECTORS` |
| `module_discovery/kb_loader.py` | **新增函数** | `_append_hidden_filter()` / `apply_overlay_scope()` / `detect_active_overlay_js()` / `get_overlay_prefix()` / `expand_step(apply_hidden=True)` |
| `module_discovery/button_driver.py` | **增强** | `click_row_button_v2` / `click_row_more_item` 集成隐藏过滤 + 覆盖层定位 + iframe 穿透 |
| `module_discovery/form_filler.py` | **增强** | `MultiStepExecutor._discover_first_option()` / `_cascader_discover_and_select()` / `_read_cascader_current_items()` 自动发现选项 |
| `module_discovery/wait_helpers.py` | **新增函数** | `wait_for_loading_complete()` 事件驱动等待（7 种 loading 元素） |
| `module_discovery/diagnostic_mode.py` | **新建** | 自诊断模式 |
| `module_discovery/kb_merger.py` | **新建** | 知识库合并器 |
| `run_suite.py` | **新建** | 测试套件运行器 |
| `config/selector_patterns.json` | **新建** | UI 元素定位模式 |
| `config/operation_patterns.json` | **新建** | 操作流程模式 |
| `config/debug_strategies.json` | **新建** | 诊断策略 |

---

## 十一、运行命令参考

### 完整流程
```bash
python -m module_discovery.run --project ecm-compute --module 角色管理 --stage all
```

### 分阶段运行
```bash
python -m module_discovery.run --project ecm-compute --module 角色管理 --stage 2  # 仅捕获
python -m module_discovery.run --project ecm-compute --module 角色管理 --stage 34  # 分析+生成
python -m module_discovery.run --project ecm-compute --module 角色管理 --stage 4   # 仅生成
```

### 执行生成的脚本
```bash
python projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py
python projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py create update
```

### 生成 HTML 报告
```bash
python lib/test_report.py projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py
```

### 运行测试套件
```bash
python run_suite.py                                    # 运行所有项目所有模块
python run_suite.py --project ecm-compute              # 运行指定项目
python run_suite.py --project ecm-compute --modules 角色管理 用户管理  # 指定模块
```

---

## 十二、下一步方向

### 高优先级
1. 验证其他模块（用户管理、权限管理）
2. 添加 manifest schema 验证
3. 为 `test_runtime.py` 编写单元测试

### 中优先级
4. 文档化 manifest 结构和使用方法
5. 添加 manifest 编辑工具（手动调整字段分类）
6. 支持更复杂的响应模式（分页、嵌套 ID）

### 低优先级
7. 支持异步测试
8. 支持性能测试模式
