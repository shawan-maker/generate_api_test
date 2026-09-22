# 五阶段发现引擎实现详解

**文档版本**: v2.0  
**最后更新**: 2026-09-22  
**适用范围**: `core/discovery/` 全模块 + `lib/` 运行时

---

## 1. 总览

五阶段发现引擎的核心目标是：**给定一个 Web 后台模块的入口 URL 和登录凭证，自动完成从 UI 探测到可运行测试脚本的全流程**，无需人工抓包或编写测试代码。

### 1.1 阶段流水线

| 阶段 | 入口文件 | 输入 | 输出 | 需要浏览器 |
|------|----------|------|------|-----------|
| Stage 1 | `discover_ui.py` | 模块入口 URL | `{module}_ui.json` + `{module}_playbook.json` | 是 |
| Stage 2 | `capture_apis.py` | playbook.json | `{module}_capture.json` + UI 脚本包 | 是 |
| Stage 3 | `analyze_flow.py` | capture result | `{module}_manifest.json` | 否 |
| Stage 4 | `gen_test.py` | manifest | `{module}_API测试.py` | 否 |
| Stage 5 | `export_artifacts.py` | manifest | Postman Collection + helpers + Excel | 否 |

### 1.2 核心架构：Manifest + 运行时库

所有阶段围绕 **manifest 字典** 展开——Stage 3 构建它，Stage 4/5 消费它：

```
Stage 1 → ui_result + playbook（UI 层信息）
Stage 2 → capture result（API 层信息）
Stage 3 → manifest（将 UI + API 信息融合为完整测试清单）
Stage 4 → 薄脚本（~100 行，嵌入 manifest + 调用运行时）
Stage 5 → 外部格式导出（Postman/helpers.py/Excel）
```

运行时库 `lib/runtime/test_runtime.py` 提供所有执行逻辑（ResponseParser、StepExecutor、TestRunner），生成的脚本只是数据载体。

---

## 2. Stage 1：UI 元素探测

**入口**: `discover_ui.py` → `discover_all()` / `discover_and_validate()`  
**调用者**: `run.py` → `run_stage1()`

### 2.1 探测流程

Stage 1 的核心任务是扫描页面上所有可交互元素，分类归档，并验证业务闭环（点击创建按钮 → 弹窗出现 → 表单可填 → 提交成功）。

#### Phase A：标准探测

`discover_all()` 执行一次完整的页面扫描，流程如下：

1. **CSS 地毯扫描** — 用合并的 CSS 选择器（`BUTTON_SELECTORS`）一次 `evaluate` 扫描所有候选元素，同时按位置分类：
   - `toolbar`：工具栏按钮（表格上方）
   - `row_action`：行操作按钮（表格行内，标记 `source` 字段区分 main/fixed-left/fixed-right）
   - `dialog`：弹窗内按钮
   - `menu`：侧边菜单项

2. **KB 增强扫描** — 用知识库模板（`base_nav_kb.json`）中的 XPath 模式补充扫描，捕获 CSS 选择器遗漏的元素

3. **iframe 扫描** — 遍历所有 iframe，在其中重复按钮扫描逻辑

4. **下拉菜单发现** — 对含 `.el-dropdown` 的按钮触发点击，发现展开的子菜单项

5. **搜索输入框探测** — 两轮策略：
   - 第一轮：KB 搜索按钮模板定位搜索图标，反向推断关联输入框
   - 第二轮：CSS 选择器扫描（placeholder 关键词 + 表格上方 input）

6. **创建表单扫描** — 如果检测到创建类按钮，点击触发弹窗，扫描所有表单字段（label、type、required、placeholder），同时生成填充规则（`fill_rule`）

7. **页面结构快照** — `_snapshot_page_structure()` 检测固定列（hasFixedLeft/hasFixedRight）、列数、操作列位置等元数据

8. **动态标签构建** — `_build_button_labels()` 将按钮原文映射为标准角色（create/delete/update 等）

#### Phase B：业务闭环验证

`discover_and_validate()` 在探测基础上增加验证：对每个关键操作（创建/编辑/删除）实际执行一遍，确认按钮可点击、弹窗可弹出、表单可填写、提交可成功。

验证结果写入 `ui_result["validated_operations"]`，每个操作记录 `status`（success/failed）、`error_type`、`fill_data` 等信息。

#### Phase C/D/E：补救流程

当 Phase B 验证失败（缺少关键元素）时，`run_stage1()` 依次尝试三个补救阶段：

- **Phase C: hints 定向重探** — 将缺失元素列表作为 hints，调用 `_scan_hints()` 用更宽松的选择器重新搜索
- **Phase D: 前置操作重试** — 检查缺失元素是否需要前置操作（如弹窗未打开），重新触发前置操作后扫描
- **Phase E: Vision 截图分析** — 对页面截图，用 AI 视觉能力分析截图中的按钮/元素位置，补充到探测结果

#### Phase F：终止判定

所有补救阶段执行完毕后，检查缺失元素中是否有 `critical` 标记的元素。如果有，返回 `None` 终止该模块；如果只是非关键元素缺失，继续后续阶段。

### 2.2 Playbook 生成

探测完成后，`build_playbook()` 将 `ui_result` 转换为 **playbook**——一个结构化的操作指令集，Stage 2 直接回放而不需要重新探测。

Playbook 核心结构：

```json
{
  "module_name": "用户管理",
  "target_url": "https://...",
  "operations": {
    "创建用户": {
      "role": "create",
      "status": "success",
      "steps": [
        {"action": "click_button", "text": "新增", "locator": "..."},
        {"action": "fill_form", "fields": [...], "fill_rule": {...}},
        {"action": "click_button", "text": "确定", "locator": "..."},
        {"action": "assert_success", "description": "操作成功"}
      ]
    },
    "编辑": { "role": "update", "steps": [...] },
    "删除": { "role": "delete", "steps": [...] }
  }
}
```

每个 step 包含明确的 action 类型、locator、填充规则等，Stage 2 的回放引擎按序执行即可。

### 2.3 输出文件

| 文件 | 路径 | 内容 |
|------|------|------|
| `{module}_ui.json` | `kb/module_discovered/` | 完整探测结果（按钮列表、表单字段、页面结构等） |
| `{module}_playbook.json` | `kb/module_discovered/` | 结构化操作指令集（Stage 2 直接回放） |

---

## 3. Stage 2：API 捕获

**入口**: `capture_apis.py` → `capture_all()`  
**调用者**: `run.py` → `run_stage2()`

### 3.1 核心思路

Stage 2 不重新探测 UI，而是加载 Stage 1 生成的 playbook，按序回放所有操作，同时拦截浏览器发出的所有 XHR/fetch 请求。通过时间窗口将请求归属到对应操作。

### 3.2 执行流程

```
加载 playbook
    ↓
安装 RequestInterceptor（HTTP 拦截器）
    ↓
导航到目标页面（记录 init 时间窗口 → 页面初始加载的 API）
    ↓
遍历 playbook.operations：
    ├── 设置上下文标记: interceptor.set_context("replay:{action}")
    ├── 记录操作开始时间
    ├── 调用 replay_from_playbook() 回放步骤序列
    ├── 如果是 create 操作 → 提取 marker（记录标识，供后续行级操作用）
    ├── 记录操作结束时间
    └── 操作后清理残留弹窗
    ↓
按时间窗口将拦截的请求分类到各操作
    ↓
端点去重 + 分类
    ↓
验证捕获完整性（≥3 个操作有核心 API）
```

### 3.3 请求拦截器（RequestInterceptor）

`request_interceptor.py` 注册 Playwright 的 `request`/`response` 事件监听：

- **请求收集**：记录 URL、Method、Body、Headers、触发上下文（context）
- **响应收集**：记录状态码、响应 Body（截断保存为样本）
- **KB 驱动注入**：从 `probe_lessons_kb.json` 加载配置，通过 monkey-patch 浏览器的 `XMLHttpRequest.send` 和 `fetch`，对匹配 URL 正则的请求自动补全缺失字段（如 `tenantId`、`adminId`），注入值优先从 `/current-user` 端点实时获取
- **权限门禁识别**：检测响应中的权限关键词（403、"无权限"等）

### 3.4 端点分类（EndpointClassifier）

`endpoint_classifier.py` 将拦截到的请求按 pathname 去重，并标记每个端点出现的上下文（`contexts` 字段）。分类结果用于 Stage 3 的基础设施 API 识别。

### 3.5 回放引擎（replay_engine.py）

`replay_from_playbook()` 按步骤类型分派执行：

| 步骤 action | 执行逻辑 |
|-------------|----------|
| `click_button` | `ButtonDriver` 点击（KB 模板 fallback + 隐藏过滤 + 覆盖层优先 + iframe 穿透） |
| `fill_form` | `FormFiller` 按 fill_rule 填充（type-aware 动态构建值） |
| `click_row_button` | 用 marker 定位行 → 点击行内按钮 |
| `click_row_more` | 点击"更多"下拉 → 选择子项 |
| `find_row` | 在表格中搜索含 marker 的行 |
| `select_option` | `MultiStepExecutor` 处理 el-select/el-cascader |
| `assert_success` | 检测 el-message 中的成功文本 |

#### 通知钉住机制

回放开始时注入 MutationObserver，捕获 `el-message`/`el-notification` 等瞬态通知：

```javascript
// 检测到通知 DOM 插入后：
// 1. 强制 display/visibility/opacity 为可见（!important）
// 2. 拦截 node.remove() 和 parentNode.removeChild()（Vue transition 用后者）
// 3. 子 MutationObserver 监听 style/class 属性变更，覆盖任何隐藏尝试
// 4. window.__unpin_notifications() 释放所有钉住的元素
```

释放在 `_cleanup_dialogs()` 的 Phase 0 执行（操作间清理时），确保截图时通知仍然可见。

#### 按钮点击驱动（button_driver.py）

`click_row_button_v2()` 集成三层增强：

1. **隐藏过滤** — 三套 XPath 谓词排除 `display:none`、`is-hidden`、`visibility:hidden` 的元素
2. **覆盖层优先** — `detect_active_overlay_js()` 检测当前活跃的弹窗/抽屉，优先在其中查找按钮
3. **iframe 穿透** — 遍历 `page.frames` 在主框架和所有 iframe 中查找

#### 表单填充（form_filler.py）

`MultiStepExecutor` 处理复杂表单组件：

| 组件 | 发现方式 |
|------|----------|
| el-select | 展开后 `_discover_first_option()` 读取首个可见选项 |
| el-cascader | `_cascader_discover_and_select()` 逐级展开（最大深度 10 级） |
| el-date-picker | 选择当前日期 |
| 普通 input | 按 type 生成：text → `AT_test_{ts}`，number → 1 |

### 3.6 重试与验证

`run_stage2()` 包含重试机制（`--max-recapture`），验证失败时重新导航到目标页面并重放。验证标准：`core_api_map` 中至少 3 个操作有核心 API。

### 3.7 输出文件

| 文件 | 路径 | 内容 |
|------|------|------|
| `{module}_capture.json` | `kb/module_discovered/` | 捕获结果（core_api_map、all_endpoints、response_samples、operation_order） |
| UI 脚本包 | `{version}/ui/` | 由 `generate_ui_script.py` 生成（薄脚本 + 同步的 lib/ + playbook） |

### 3.8 UI 脚本生成（generate_ui_script.py）

Stage 2 同时调用 `generate_ui_script.py` 生成 UI 自动化测试脚本包：

- 薄主脚本（`{module}.py`）：加载 playbook + 调用回放引擎
- 同步运行时 lib/：从 `core/discovery/replay/` 复制回放引擎相关文件，转换导入路径
- 数据文件（`{module}_data.json`）：提取测试数据
- Playbook 文件（`{module}_playbook.json`）：直接复制

---

## 4. Stage 3：逻辑分析

**入口**: `analyze_flow.py` → `analyze()` + `build_manifest()`  
**调用者**: `run.py` → `run_stage34()`

### 4.1 分析流水线

Stage 3 是纯离线分析（不需要浏览器），从 Stage 2 的 capture result 推导出完整的测试执行清单。

```
analyze() 七步流水线：

Step 0: 基础设施 API 识别
    ↓
Step 1: 构建核心 API 列表（从 core_api_map 提取）
    ↓
Step 2: 按钮-API 关联（建立 "按钮文本 → 核心API" 映射）
    ↓
Step 3: 执行顺序编排（直接使用 Stage 2 的 operation_order）
    ↓
Step 4: 数据依赖链分析（ID 生产者识别 + ID 字段追踪）
    ↓
Step 5: 状态断言推导（跨响应对比找状态变化规律）
    ↓
Step 6: 构建 value_index（前置 API 和上下文字段的值索引）
    ↓
Step 7: 前置 API 依赖链追踪
```

### 4.2 各步骤详解

#### Step 0：基础设施 API 识别

`_identify_infrastructure_apis()` 通过频率统计识别非业务 API：

- **规则 1**：只在 `init` 上下文出现的端点 → 基础设施 API（页面初始化加载的资源）
- **规则 2**：出现在 ≥60% 不同操作上下文中的 GET 端点 → 基础设施 API（如 `/current-user` 在每个操作后都被前端调用）
- **降级策略**：操作总数 ≤2 时频率统计不可靠，改为匹配 KB 中的已知基础设施路径

#### Step 3：执行顺序

直接使用 Stage 2 的 `operation_order`（即 Stage 1 定义的 playbook 回放顺序）。这个顺序已经考虑了业务依赖（先创建再编辑再删除），无需重新推导。

#### Step 4：数据依赖链分析

`_derive_dependencies()` 的核心逻辑：

1. 按执行顺序扫描响应样本，找到第一个返回 ID 的操作（**ID 生产者**）
2. `_extract_ids_recursive()` 递归遍历响应 JSON（最大深度 4 层），提取所有疑似 ID 字段
3. 检查后续操作的请求体中是否引用了这些 ID 字段（匹配 `COMMON_ID_FIELDS` 列表）
4. 输出 `injections` 映射：`{字段名: {source, source_path}}`

#### Step 5：状态断言推导

`_derive_state_rules()` 分析操作执行前后数据的状态变化：

- 找到列表查询 API（响应含 `list + total` 结构）
- 对比操作前后的查询结果，推导状态字段和变化规律
- 输出断言规则（如：删除后数据不应出现在列表中）

### 4.3 Manifest 构建

`build_manifest()` 将分析结果融合为完整的测试清单：

```python
manifest = build_manifest(flow, capture_result, profile, module_name, target_url)
```

**自动发现机制**（在 build_manifest 中执行）：

| 函数 | 职责 |
|------|------|
| `_discover_response_contract()` | 从响应样本中发现信封键（entity/data/rows）、成功判断逻辑（success/code）、列表键（list/records）、总数键（total/totalCount）、ID 字段 |
| `_classify_body_fields()` | 为每个请求体字段标注角色：`id_ref`（ID 引用）/ `context`（上下文）/ `name`（名称字段）/ `mutable`（可变字段）/ `static`（静态字段） |

**自动插入验证步骤**：在 create/update/delete 后自动插入查询验证步骤（`contains_id` / `not_contains_id` 断言），确保操作真正生效。

### 4.4 Manifest 结构

```json
{
  "module": {"name": "用户管理", "target_url": "...", "base_url": "..."},
  "auth_profile": {"header_name": "Authorization", "fixed_headers": {...}},
  "response_contract": {
    "envelope_key": "entity",
    "success_check": {"field": "success", "value": true},
    "list_key": "list",
    "total_key": "total",
    "id_field": "id"
  },
  "steps": [
    {
      "action": "create",
      "label": "创建用户",
      "api": {"method": "POST", "pathname": "/estack/api/.../users", ...},
      "body_template": {"tenantId": "...", "userName": "...", ...},
      "body_field_roles": {
        "tenantId": {"role": "context"},
        "userName": {"role": "name"},
        "phone": {"role": "mutable"}
      },
      "extract": {"id": "entity.id"}
    },
    {
      "action": "query_verify",
      "label": "查询验证（创建后）",
      "api": {"method": "POST", "pathname": ".../list"},
      "assertion": {"type": "contains_id", "field": "id"}
    }
    // ... 更多步骤
  ],
  "pre_apis": [...],
  "state_assertions": {"after_delete": "NOT_EXIST"}
}
```

### 4.5 输出文件

| 文件 | 路径 | 内容 |
|------|------|------|
| `{module}_analysis.json` | `kb/module_discovered/` | 分析原始结果（crud_order、dependencies、state_assertions） |
| `{module}_manifest.json` | `kb/module_discovered/` | 完整测试清单（Stage 4/5 的直接输入） |

---

## 5. Stage 4：脚本生成

**入口**: `gen_test.py` → `generate_script()` + `save_script_to_file()`  
**调用者**: `run.py` → `run_stage34()`

### 5.1 薄脚本生成

`generate_manifest_script()` 生成约 100 行的 Python 脚本，结构极其简单：

```python
"""
用户管理_API测试.py — 自动生成 (manifest 模式)
执行流程:
  1. 创建用户
  2. 查询验证（创建后）
  3. 编辑
  ...
"""
import sys, json
from pathlib import Path

# Bootstrap: 找到同目录的 lib/
_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))
from lib.runtime.test_runtime import TestRunner

MANIFEST = { ... }  # 嵌入的完整测试清单（JSON → Python 字面量）

# 全局前置 API 支持
SHARED_CONTEXT = {}
_shared_ctx_file = Path(__file__).resolve().parent / ".shared_context.json"
if _shared_ctx_file.exists():
    from lib.runtime.global_pre_apis import GlobalPreApiExecutor
    SHARED_CONTEXT = GlobalPreApiExecutor.load_context(_shared_ctx_file)

if __name__ == "__main__":
    runner = TestRunner(MANIFEST, shared_context=SHARED_CONTEXT)
    steps = sys.argv[1:] if len(sys.argv) > 1 else None
    runner.run(steps_filter=steps)
```

脚本只是 manifest 数据的载体 + 运行时入口，所有执行逻辑由 `lib/runtime/test_runtime.py` 提供。

### 5.2 运行时同步

`_sync_runtime_lib()` 将项目根 `lib/` 下的运行时文件复制到脚本包的 `lib/`，同步时进行导入路径转换：

**同步映射表**（只同步 API 测试真正需要的文件）：

| 源文件 | 目标位置 |
|--------|----------|
| `lib/runtime/test_runtime.py` | `api/lib/runtime/test_runtime.py` |
| `lib/runtime/global_pre_apis.py` | `api/lib/runtime/global_pre_apis.py` |
| `lib/auth/cookie_client.py` | `api/lib/cookie_client.py` |
| `lib/report/test_report.py` | `api/lib/test_report.py` |

同步后自动清理旧版残留文件（已废弃的子包和重复文件）。

### 5.3 运行时执行（test_runtime.py）

`TestRunner` 按 manifest 中的 steps 顺序执行，每个 step 的核心流程：

```
1. 构建请求 URL（base_url + pathname + path_params 替换）
2. 按 body_field_roles 填充请求体
3. 发送 HTTP 请求
4. 解析响应（ResponseParser）
5. 提取变量（extract 配置 → state 字典）
6. 执行断言（assertion 配置）
7. 写入 JSONL 日志
```

#### Body 字段角色解析

`StepExecutor` 按 role 填充每个请求体字段：

| Role | 规则 | 示例 |
|------|------|------|
| `id_ref` | 从 state 字典读取（上一个操作的 extract 结果） | `"roleId": state["id"]` |
| `context` | 从 state 或 shared_context 读取 | `"tenantId": shared_ctx["tenantId"]` |
| `name` | `AT_{timestamp}_` 前缀 + 原始值 | `"AT_20260922_role1"` |
| `mutable` | `"自动修改_{timestamp}"` | `"自动修改_20260922"` |
| `static` | 直接复制 body_template 值 | `"type": 1` |

#### 响应解析（ResponseParser）

自动发现响应信封结构：
- 扫描 `ENVELOPE_KEY_CANDIDATES`（entity/data/result/rows 等）找到数据载体
- 根据 `response_contract` 判断请求是否成功
- 按 `extract` 配置提取变量到 state 字典

#### JSONL 日志

每次 API 调用写入一条结构化日志到 `output/logs/{module}_API测试.jsonl`：

```json
{
  "type": "api_call",
  "seq": 1,
  "step_action": "create",
  "step_label": "创建用户",
  "request": {"method": "POST", "url": "...", "headers": {...}, "body": {...}},
  "response": {"status": 200, "headers": {...}, "body": {...}},
  "assertion": "passed",
  "assertion_message": ""
}
```

### 5.4 输出文件

| 文件 | 路径 | 内容 |
|------|------|------|
| `{module}_API测试.py` | `{version}/api/` | 薄脚本（manifest + 运行时入口） |
| `helpers.py` | `{version}/api/` | 测试数据生成函数集 |
| `lib/` | `{version}/api/lib/` | 同步的运行时库（导入路径已转换） |
| `config/cookies.json` | `{version}/api/config/` | Cookie 文件 |

---

## 6. Stage 5：导出 Artifacts

**入口**: `export_artifacts.py`  
**调用者**: `run.py` → `run_stage5()`

### 6.1 三种导出格式

Stage 5 不修改 manifest、不执行测试，仅做格式转换。可独立于 Stage 4 运行（从磁盘加载 manifest）。

#### Postman Collection v2.1.0

`export_postman_collection()` 生成可直接导入 Postman 的 JSON 文件：

- **Collection 级变量**：`base_url`、认证 header、前置 API 提取的变量
- **文件夹结构**：`00_前置操作/`（pre_apis）+ `01_业务操作/`（steps）
- **Pre-request Script**：JS 版 helpers 函数（`gen_test_name()`、`gen_email()` 等），动态字段通过 `pm.variables.set()` 自动赋值
- **Test Script**：从响应中提取变量到环境变量（`pm.environment.set()`）
- **phases 展开**：多阶段 step 展开为多个 Postman request item

#### helpers.py

`export_helpers()` 生成 Python 测试数据生成函数集（给外部测试平台用）：

```python
def gen_test_name(prefix="AT"):
    return f"{prefix}_{_ts6()}"

def gen_mutable_value():
    return f"auto_modified_{_ts6()}"

def gen_email():
    return f"at_{_ts6()}@test.com"
```

#### Excel 参数文件

`export_excel_params()` 生成变量配置表：
- 所有 body 字段以 `e_` 前缀命名（如 `e_userName`）
- 标记敏感字段（password、secret 等）
- 标注字段角色（name/mutable/static/id_ref/context）

### 6.2 输出文件

| 文件 | 路径 |
|------|------|
| `{module}.postman_collection.json` | `{version}/export/{module}/` |
| `helpers.py` | `{version}/export/{module}/` |
| `{module}_params.xlsx` | `{version}/export/{module}/` |

---

## 7. 跨阶段机制

### 7.1 反馈循环（feedback_loop.py）

Stage 1 ↔ Stage 2 之间的联动：

- **策略匹配** — `match_debug_strategy()` 根据错误类型从 `debug_strategies.json` 加载诊断策略
- **结果合并** — `patch_ui_result()` 将修复后的元素合并回 ui_result（去重）
- **经验保存** — 成功修复写入 `selector_patterns.json`（跨模块复用）

### 7.2 自诊断（diagnostic_mode.py）

`DiagnosticMode` 类在关键失败点自动触发：

| 触发条件 | 策略 |
|----------|------|
| `find_data_row` 连续 3 次失败 | `row_not_found` — 表格结构 dump + 选择器测试 |
| `click_row_button` 连续 2 次失败 | `button_click_failed` — 按钮可见性 + 隐藏检查 |
| Stage 2 验证失败且重试耗尽 | `stage2_validation_failed` — 全面诊断 |

诊断输出到 `output/debug/{module}/diagnostic_{timestamp}/`，包含截图、DOM 结构、选择器命中率等。如果 `auto_fix=true` 且发现新选择器，自动更新 `selector_patterns.json`。

### 7.3 知识库体系

```
config/
├── base_nav_kb.json         XPath/CSS 选择器模板（UI 框架适配）
├── probe_lessons_kb.json    反模式库 + 系统配置（KB 驱动注入）
├── selector_patterns.json   层1: UI 元素定位模式（跨模块积累）
├── operation_patterns.json  层2: 操作流程模式
└── debug_strategies.json    层3: 诊断策略
```

`kb_merger.py` 的 `KBMerger` 处理跨模块经验合并：pattern_key 去重、module_source 合并、confidence 取最大值、selectors 合并、冲突检测。

### 7.4 事件驱动等待（wait_helpers.py）

替代固定 `wait_for_timeout`，通过事件驱动提高稳定性和速度：

| 函数 | 等待目标 |
|------|----------|
| `wait_for_table_ready()` | loading mask 消失 + 表格行出现 |
| `wait_for_dialog()` | 弹窗/消息框出现 |
| `wait_for_dropdown()` | 下拉菜单展开 |
| `wait_for_loading_complete()` | 浏览器 load + networkidle + 7 种 loading 元素消失 |
| `wait_for_navigation_complete()` | 页面跳转完成 |
| `wait_for_dialog_dismissed()` | 弹窗关闭 |
| `wait_for_api_response()` | 指定 API 响应返回 |

### 7.5 前置 API 合并（pre_api_merger.py）

跨模块共享前置 API：当多个模块依赖相同的前置数据（如获取租户 ID、组织树），合并为全局前置 API，避免重复执行。执行结果保存到 `.shared_context.json`，所有脚本启动时加载。

---

## 8. 流程图

### 8.1 五阶段总流程

```
                    ┌─────────────────────────────────────────────┐
                    │              CLI 入口 (run.py)              │
                    │  --project --module --stage --url ...       │
                    └──────────────┬──────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────────────┐
                    │         登录 + Cookie 获取                  │
                    │  auth_runner.py → cookies.json              │
                    └──────────────┬──────────────────────────────┘
                                   │
         ┌─────────────────────────▼─────────────────────────┐
         │              Stage 1: UI 探测                      │
         │                                                   │
         │  Phase A: discover_all()                          │
         │    CSS扫描 → KB增强 → iframe → 下拉菜单           │
         │    → 搜索框 → 创建表单 → 页面结构快照              │
         │                                                   │
         │  Phase B: discover_and_validate()                 │
         │    实际执行创建/编辑/删除，验证业务闭环              │
         │                                                   │
         │  Phase C/D/E: 补救（验证失败时）                   │
         │    hints重探 → 前置操作重试 → Vision截图分析        │
         │                                                   │
         │  Phase F: 终止判定                                 │
         │    critical缺失 → 终止 / 仅非关键 → 继续           │
         │                                                   │
         │  输出: {module}_ui.json + {module}_playbook.json  │
         └─────────────────────────┬─────────────────────────┘
                                   │
         ┌─────────────────────────▼─────────────────────────┐
         │              Stage 2: API 捕获                     │
         │                                                   │
         │  加载 playbook → 安装拦截器 → 导航(init)           │
         │  → 逐操作回放 replay_from_playbook()              │
         │  → 时间窗口分类 API → 端点去重                    │
         │  → 验证(≥3操作有核心API)                          │
         │  → 调用 generate_ui_script.py 生成 UI 脚本包      │
         │                                                   │
         │  输出: {module}_capture.json + UI 脚本包          │
         └─────────────────────────┬─────────────────────────┘
                                   │
         ┌─────────────────────────▼─────────────────────────┐
         │         Stage 3: 逻辑分析（离线，无需浏览器）       │
         │                                                   │
         │  analyze() 七步流水线:                             │
         │  Step 0: 基础设施 API 识别（频率统计）             │
         │  Step 1: 核心 API 列表构建                        │
         │  Step 2: 按钮-API 关联                            │
         │  Step 3: 执行顺序（沿用 Stage 2 operation_order） │
         │  Step 4: 数据依赖链（ID 生产者 + ID 字段追踪）     │
         │  Step 5: 状态断言推导                             │
         │  Step 6-7: value_index + 前置 API 追踪            │
         │                                                   │
         │  build_manifest():                                │
         │  响应信封发现 + 字段角色分类 + 自动插入验证步骤    │
         │                                                   │
         │  输出: {module}_manifest.json                     │
         └─────────────────────────┬─────────────────────────┘
                                   │
         ┌─────────────────────────▼─────────────────────────┐
         │         Stage 4: 脚本生成（离线）                  │
         │                                                   │
         │  generate_manifest_script():                      │
         │  manifest JSON → 薄脚本(~100行)                   │
         │                                                   │
         │  _sync_runtime_lib():                             │
         │  lib/ → api/lib/ + 导入路径转换                   │
         │                                                   │
         │  输出: {module}_API测试.py + helpers.py + lib/    │
         └─────────────────────────┬─────────────────────────┘
                                   │
         ┌─────────────────────────▼─────────────────────────┐
         │         Stage 5: 导出（离线）                      │
         │                                                   │
         │  Postman Collection v2.1.0 (含 Pre/Test Script)   │
         │  helpers.py (测试数据生成函数集)                   │
         │  Excel 参数文件 (e_ 前缀变量表)                   │
         │                                                   │
         │  输出: export/{module}/                           │
         └───────────────────────────────────────────────────┘
```

### 8.2 Stage 1 六阶段补救流程

```
discover_all() ──────────────────────────────────────┐
    │                                                │
    ▼                                                │
discover_and_validate()                              │
    │                                                │
    ├── 验证通过 ──→ 保存 ui.json + playbook.json    │
    │                                                │
    └── 验证失败                                     │
        │                                            │
        ▼                                            │
    Phase B: validate_stage1()                       │
        │                                            │
        ├── 通过 ──→ 保存                            │
        │                                            │
        └── 失败（missing 列表）                      │
            │                                        │
            ▼                                        │
        Phase C: hints 定向重探                       │
            _scan_hints(missing)                     │
            patch_ui_result()                        │
            │                                        │
            ├── 通过 ──→ 保存                        │
            │                                        │
            └── 仍失败                                │
                │                                    │
                ▼                                    │
            Phase D: 前置操作重试                     │
                _retry_precondition()                │
                _scan_dialog_buttons()               │
                │                                    │
                ├── 通过 ──→ 保存                    │
                │                                    │
                └── 仍失败                            │
                    │                                │
                    ▼                                │
                Phase E: Vision 截图分析              │
                    AI 分析截图 → 补充元素            │
                    │                                │
                    ├── 通过 ──→ 保存                │
                    │                                │
                    └── 仍失败                        │
                        │                            │
                        ▼                            │
                    Phase F: 终止判定                 │
                        │                            │
                        ├── critical缺失 → None (终止)
                        │                            │
                        └── 仅非关键 → 继续 Stage 2  │
```

### 8.3 Stage 2 回放与 API 分类

```
加载 playbook.json
    │
    ▼
安装 RequestInterceptor
    ├── Playwright request/response 事件监听
    ├── KB 驱动注入 (monkey-patch XHR/fetch)
    └── Console 消息监听
    │
    ▼
导航到目标页面
    interceptor.set_context("init")
    记录 init 时间窗口 [t0, t1]
    │
    ▼
┌─── 遍历 playbook.operations ──────────────────────┐
│                                                    │
│  action = "创建用户"                                │
│  interceptor.set_context("replay:创建用户")         │
│  t_start = now()                                   │
│                                                    │
│  replay_from_playbook():                           │
│    ├── click_button → ButtonDriver (KB fallback)   │
│    ├── fill_form → FormFiller (type-aware)         │
│    ├── click_button (确定) → confirm_dialog()      │
│    │     └── 截图（通知已被 MutationObserver 钉住） │
│    └── assert_success → 检测成功消息               │
│                                                    │
│  提取 marker（如果是 create 操作）                   │
│  t_end = now()                                     │
│  记录 replay 时间窗口 [t_start, t_end]              │
│                                                    │
│  _cleanup_after_operation()                        │
│    └── 关闭残留弹窗 + Escape 兜底                   │
│                                                    │
└────────────────────────────────────────────────────┘
    │
    ▼
按时间窗口分类拦截到的请求
    ├── init 窗口 → 页面初始加载 API
    ├── replay:创建用户 窗口 → 创建相关 API
    ├── replay:编辑 窗口 → 编辑相关 API
    └── ...
    │
    ▼
EndpointClassifier 去重 + 标记 contexts
    │
    ▼
验证: core_api_map 中 ≥3 操作有核心 API
    │
    ├── 通过 → 保存 capture.json + 生成 UI 脚本包
    │
    └── 失败 → 重试（--max-recapture）
```

### 8.4 Stage 3 分析流水线

```
输入: capture.json (core_api_map + all_endpoints + response_samples)
    │
    ▼
Step 0: _identify_infrastructure_apis()
    │  规则1: init-only 端点 → 基础设施
    │  规则2: 出现在 ≥60% 操作中的 GET → 基础设施
    │  降级: 操作≤2 时匹配 KB 已知路径
    ▼
Step 1: _build_core_apis_from_core_api_map()
    │  排除 init + 基础设施
    │  选取核心 API (写操作优先, body 字段数最多)
    │  保留操作链候选 (同资源路径的写操作)
    ▼
Step 2: _map_buttons_to_apis()
    │  按钮文本 → 核心 API 映射
    │  通过 context 字段关联
    ▼
Step 3: _derive_order()
    │  直接使用 Stage 2 的 operation_order
    ▼
Step 4: _derive_dependencies()
    │  扫描响应 → 找 ID 生产者 (第一个返回 ID 的操作)
    │  _extract_ids_recursive() 深度 4 层
    │  检查后续操作请求体 → 找 ID 引用
    ▼
Step 5: _derive_state_rules()
    │  找列表查询 API (list + total)
    │  对比操作前后查询结果 → 状态断言
    ▼
Step 6: build_value_index()
    │  合并 pre_api_candidates + create_body + context_fields
    ▼
Step 7: trace_pre_api_dependencies()
    │  追踪前置 API 依赖链
    ▼
build_manifest()
    │  _discover_response_contract() → 信封/成功判断/列表键
    │  _classify_body_fields() → 字段角色分类
    │  自动插入验证步骤 (create/edit/delete 后)
    ▼
输出: manifest.json
```

### 8.5 运行时执行流程（TestRunner）

```
TestRunner(MANIFEST, shared_context)
    │
    ▼
初始化 AuthSession
    │  从 cookies.json 加载 Cookie
    │  提取 token → 构建 Authorization header
    ▼
┌─── 遍历 manifest.steps ───────────────────────────┐
│                                                    │
│  step = {action, api, body_template, ...}          │
│                                                    │
│  StepExecutor.execute_step(step, state):           │
│    │                                               │
│    ├── 1. 构建 URL                                 │
│    │      base_url + pathname                      │
│    │      path_params 替换 → state 变量            │
│    │                                               │
│    ├── 2. 填充请求体                                │
│    │      遍历 body_field_roles:                   │
│    │        id_ref → state[key]                    │
│    │        context → state/create_body/shared_ctx │
│    │        name → "AT_{ts}_" + 原始值             │
│    │        mutable → "自动修改_{ts}"              │
│    │        static → 复制 body_template 值         │
│    │                                               │
│    ├── 3. 发送 HTTP 请求                           │
│    │      httpx.AsyncClient.request()              │
│    │                                               │
│    ├── 4. ResponseParser 解析响应                  │
│    │      发现信封键 (entity/data/rows)            │
│    │      判断成功 (success==true / code==200)     │
│    │                                               │
│    ├── 5. 提取变量 → state 字典                    │
│    │      extract: {id: "entity.id"}               │
│    │      state["id"] = response["entity"]["id"]   │
│    │                                               │
│    ├── 6. 执行断言                                 │
│    │      contains_id / not_contains_id / eq       │
│    │                                               │
│    └── 7. 写入 JSONL 日志                          │
│           output/logs/{module}_API测试.jsonl       │
│                                                    │
└────────────────────────────────────────────────────┘
    │
    ▼
生成 HTML 报告 (JSONL → Postman 风格)
    │
    ▼
输出运行结果摘要
```

---

## 9. 关键文件索引

| 文件 | 职责 |
|------|------|
| `core/discovery/run.py` | CLI 入口，阶段编排 |
| `core/discovery/discover_ui.py` | Stage 1 探测 + 验证 + Playbook 生成 |
| `core/discovery/stage1_rescue.py` | Stage 1 Phase C/D/E 补救子例程 |
| `core/discovery/capture_apis.py` | Stage 2 Playbook 回放 + API 拦截 |
| `core/discovery/request_interceptor.py` | HTTP 拦截 + KB 驱动注入 |
| `core/discovery/endpoint_classifier.py` | 端点分类去重 |
| `core/discovery/replay/replay_engine.py` | Playbook 回放引擎（含通知钉住） |
| `core/discovery/replay/button_driver.py` | 按钮点击驱动（隐藏过滤 + 覆盖层 + iframe） |
| `core/discovery/replay/form_filler.py` | 表单智能填充（MultiStepExecutor） |
| `core/discovery/replay/wait_helpers.py` | 事件驱动等待 |
| `core/discovery/analyze_flow.py` | Stage 3 逻辑分析 + Manifest 构建 |
| `core/discovery/gen_test.py` | Stage 4 薄脚本生成 + 运行时同步 |
| `core/discovery/generate_ui_script.py` | Stage 2b UI 脚本生成 |
| `core/discovery/export_artifacts.py` | Stage 5 导出（Postman/helpers/Excel） |
| `core/discovery/feedback_loop.py` | Stage 1↔2 反馈循环 |
| `core/discovery/diagnostic_mode.py` | 自诊断模式 |
| `core/discovery/kb_loader.py` | 知识库加载器 |
| `core/discovery/kb_merger.py` | 知识库跨模块合并 |
| `core/discovery/pre_api_merger.py` | 跨模块前置 API 合并 |
| `core/discovery/const.py` | 常量定义（选择器、字段名、UI 框架配置） |
| `lib/runtime/test_runtime.py` | 测试运行时（ResponseParser + StepExecutor + TestRunner） |
| `lib/runtime/global_pre_apis.py` | 全局前置 API 执行器 |
| `lib/auth/cookie_client.py` | Cookie 管理客户端 |
| `lib/report/test_report.py` | JSONL → HTML 报告 |

---

**文档版本**: v2.0  
**最后更新**: 2026-09-22
