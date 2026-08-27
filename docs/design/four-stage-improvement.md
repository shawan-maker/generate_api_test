# 四阶段发现引擎全面优化方案

## 文档信息

- 路径: `docs/design/four-stage-improvement.md`
- 日期: 2026-08-26
- 状态: 待评审

## 目标

将四阶段发现引擎（UI探测 → API捕获 → 流程分析 → 脚本生成）从"能跑通单个模块"提升到"跨模块可复用、自适应、自诊断"的自动化水平。

---

## 一、当前状态总结

### 已完成的基础修复（上一轮）
- Fix #1: `interceptor._calls` → `interceptor.calls`（根因修复）
- Fix #2: Stage 2 验证门控 + `--force-gen`
- Fix #3: 调试工件自动转储
- Fix #4: 树形选择器递归展开
- Fix #5: Stage 2 重试循环（`--max-recapture`）
- `find_data_row()` 支持多 wrapper 搜索

### 当前 Blocker
- 行操作按钮在 Element UI 固定右列中找不到（`click_row_button_v2` / `click_row_more_item` 选择器路径不对）
- `base_nav_kb.json` 已有正确的 `table-action-button` 模板（含 fixed-right fallback），但 `button_driver.py` 未使用

### 各阶段问题汇总

| 阶段 | 问题数 | 严重程度 | 核心问题 |
|------|--------|----------|----------|
| Stage 1 | 4 | 中 | 无法探测固定列中的行操作按钮 |
| Stage 2 | 7 | 高 | 行操作全部失败、操作列表硬编码、无分页处理 |
| Stage 3 | 5 | 中 | CRUD顺序纯静态、依赖分析浅、字段名不匹配bug |
| Stage 4 | 5 | 中 | 大量内联代码、无Jinja2模板、硬编码域名 |
| 跨阶段 | 4 | 高 | 经验库分散、无自诊断、无合并策略 |

---

## 二、Stage 1 优化：UI 探测增强

### 2.1 当前问题

**文件**: `module_discovery/discover_ui.py`（~200行）

| # | 问题 | 影响 |
|---|------|------|
| S1-1 | 行操作按钮只在 `.el-table__body-wrapper` 中扫描 | 固定右列的编辑/删除/更多按钮漏检 |
| S1-2 | 下拉菜单触发文本硬编码为5个（"更多"、"操作"等） | 其他触发文本被忽略 |
| S1-3 | 表单扫描只在检测到"创建"按钮时触发 | 无创建功能的模块永远无 form_fields |
| S1-4 | 无重试机制 | 页面未加载完成时结果不完整 |

### 2.2 改进方案

#### S1-1 修复：固定列行操作探测

**思路**：在 `_scan_candidates` 的 JS 中，增加对 `.el-table__fixed-right` 的扫描。

```javascript
// 当前：只扫描 .el-table__body-wrapper
const mainBody = table.querySelector('.el-table__body-wrapper');

// 改进：同时扫描 fixed-right
const fixedRight = table.querySelector('.el-table__fixed-right');
if (fixedRight) {
    const fixedRows = fixedRight.querySelectorAll('tbody tr');
    fixedRows.forEach((row, idx) => {
        row.querySelectorAll('button, .el-button, .el-dropdown, span').forEach(el => {
            // 标记来源为 fixed-right，行索引为 idx
            candidates.push({...el, source: 'fixed-right', rowIndex: idx});
        });
    });
}
```

**输出增强**：`row_actions` 中每项增加 `source` 字段（`main` / `fixed-right` / `fixed-left`），Stage 2 据此选择正确的 wrapper 进行操作。

#### S1-2 改进：动态下拉菜单发现

**当前**：硬编码 `["更多", "操作", "Actions", "More", "批量操作"]`

**改进**：
1. 从 Stage 1 扫描结果中，自动识别 `.el-dropdown` 元素（不依赖文本匹配）
2. 从 `base_nav_kb.json` 的 `composite.dropdown-menu` 模板获取触发器模式
3. 将发现的触发器文本写入 `selector_patterns.json`，后续模块复用

#### S1-3 改进：Snapshot 页面结构

**借鉴 API_create 项目**：在 Stage 1 开始时，先获取页面 DOM 骨架。

**新增**：`_snapshot_page_structure(page)` 方法

```python
async def _snapshot_page_structure(page) -> dict:
    """获取页面 DOM 骨架，识别表格结构（含固定列位置）"""
    return await page.evaluate("""
    () => {
        const table = document.querySelector('.el-table');
        if (!table) return {};
        return {
            hasFixedLeft: !!table.querySelector('.el-table__fixed-left'),
            hasFixedRight: !!table.querySelector('.el-table__fixed-right'),
            columnCount: table.querySelectorAll('thead th').length,
            operationColumnIndex: _findOperationColumn(table),
            tableWrappers: _enumerateTableWrappers(table),
        };
    }
    """)
```

**输出**：写入 `ui_result["page_structure"]`，Stage 2 据此选择正确的 wrapper。

#### S1-4 改进：Stage 1 重试

```python
async def discover_all(page, max_retries=2):
    for attempt in range(max_retries):
        result = await _discover_once(page)
        if validate_stage1(result)[0]:
            return result
        if attempt < max_retries - 1:
            await page.wait_for_timeout(2000)
    return result
```

---

## 三、Stage 2 优化：API 捕获增强

### 3.1 当前问题

**文件**: `module_discovery/capture_apis.py`（533行）+ `button_driver.py`（~490行）+ `form_filler.py`（509行）

| # | 问题 | 影响 |
|---|------|------|
| S2-1 | 行操作列表硬编码（8个操作） | 不同模块的操作无法适配 |
| S2-2 | `click_row_button_v2` 搜索固定列选择器错误 | 所有行操作失败 |
| S2-3 | `fill_create_form` 的 fill_data 硬编码 | 新字段名无法填充 |
| S2-4 | 无分页处理 | 创建的行不在第一页时找不到 |
| S2-5 | 授权页导航URL硬编码 | 其他模块不兼容 |
| S2-6 | `close_dialog` 30秒超时 | 每次浪费30秒 |
| S2-7 | 请求拦截只匹配 `/estack/api` | 其他系统的API漏捕 |

### 3.2 改进方案

#### S2-1 修复：行操作列表从 Stage 1 动态获取

**当前**：
```python
operations = [
    ("编辑", "row:编辑", False, "编辑"),
    ("冻结", "dropdown:更多:冻结", True, "冻结"),
    # ... 硬编码8个
]
```

**改进**：从 Stage 1 的 `ui_result` 动态构建操作列表：

```python
def _build_operations_from_ui(ui_result: dict) -> list:
    """从 Stage 1 结果动态构建行操作列表"""
    operations = []

    # 1. 直接行操作按钮（编辑、删除等）
    for btn in ui_result.get("row_actions", []):
        text = btn["text"]
        source = btn.get("source", "main")
        is_dropdown = False
        operations.append((text, f"row:{text}", is_dropdown, text))

    # 2. 下拉菜单中的操作
    for dd in ui_result.get("dropdowns", []):
        parent = dd.get("parent", "更多")
        text = dd["text"]
        is_dropdown = True
        operations.append((text, f"dropdown:{parent}:{text}", is_dropdown, text))

    # 3. 按 CRUD_EXECUTION_ORDER 排序（删除排最后）
    operations.sort(key=lambda op: _crud_priority(op[0]))

    return operations
```

**回退**：如果 Stage 1 未探测到行操作（`row_actions` 为空），使用当前硬编码列表作为 fallback。

#### S2-2 修复：用 `base_nav_kb.json` 模板修复固定列

**发现**：`config/base_nav_kb.json` 的 `table-action-button` 模板已包含正确的 XPath：

```json
"table-action-button": {
    "patterns": [
        "//div[contains(@class,'el-table__fixed-right')]//tbody/tr[1]//span[contains(.,'{label}')]",
        "//div[contains(@class,'el-table__body-wrapper')]//tbody/tr[1]//span[contains(.,'{label}')]",
        "//div[contains(@class,'el-table__fixed-body-wrapper')]//tbody/tr[1]//span[contains(.,'{label}')]"
    ]
}
```

**改造 `button_driver.py`**：
- `click_row_button_v2()` 加载 `base_nav_kb.json` 模板
- 将 XPath 中的 `tr[1]` 替换为 `tr[{row_index+1}]`
- 按 fallback 顺序尝试：fixed-right → body-wrapper → fixed-body-wrapper

```python
async def click_row_button_v2(self, row: Locator, button_text: str) -> bool:
    """点击行内按钮，使用 KB 模板的 fallback 链"""
    row_index = await self._get_row_index(row)
    kb = self._load_selector_kb()
    patterns = kb.get("composite", {}).get("table-action-button", {}).get("patterns", [])

    for pattern in patterns:
        xpath = pattern.replace("tr[1]", f"tr[{row_index + 1}]").replace("{label}", button_text)
        try:
            btn = self.page.locator(f"xpath={xpath}")
            if await btn.count() > 0:
                await btn.click(timeout=3000)
                return True
        except Exception:
            continue

    return False
```

#### S2-3 改进：表单填充从 KB + 扫描结果动态构建

**当前**：`fill_create_form` 硬编码 ~20 个字段映射

**改进**：
1. 优先使用 `scan_form_fields_v2()` 返回的 label + type
2. 从 `selector_patterns.json` 的 `form_fill_patterns` 匹配填充规则
3. 从 `const.FORM_FILL_RULES` / `PLACEHOLDER_RULES` 作为 fallback
4. 未匹配的字段，根据 type 生成合理默认值（text → `AT_test_{ts}`，number → 1）

```python
def _build_fill_data(self, field_labels: list, username: str) -> dict:
    """动态构建填充数据"""
    ts = str(int(time.time()))[-6:]
    fill_data = {
        "名称": username, "用户名": username, "角色名称": username,
        # ... 保留现有通用映射
    }

    # 从 KB 加载模块特定的填充规则
    kb_patterns = load_selector_patterns()
    for field in field_labels:
        label = field["label"]
        if label not in fill_data:
            # 尝试 KB 规则匹配
            for rule in kb_patterns.get("form_fill_patterns", []):
                if rule["match"] in label:
                    fill_data[label] = rule["template"].format(ts=ts, username=username)
                    break
            else:
                # 按类型生成默认值
                fill_data[label] = self._default_for_type(field["type"], ts)

    return fill_data
```

#### S2-4 改进：分页感知

**当前**：`find_data_row` 只搜索当前页的行

**改进**：
1. 创建成功后，检查表格分页状态
2. 如果当前页没有匹配的行，尝试翻页查找
3. 或者：创建后通过 API 查询确认行在哪个位置

```python
async def _find_row_with_pagination(self, page, marker: str) -> Optional[Locator]:
    """带分页的行查找"""
    # 先在当前页查找
    row = await self.find_data_row(marker)
    if row:
        return row

    # 检查是否有分页
    has_pagination = await page.evaluate("""
        () => !!document.querySelector('.el-pagination')
    """)

    if not has_pagination:
        return None

    # 尝试翻页查找（最多3页）
    for page_num in range(2, 5):
        next_btn = page.locator('.el-pagination .btn-next:not(.disabled)')
        if await next_btn.count() == 0:
            break
        await next_btn.click()
        await page.wait_for_timeout(1000)
        row = await self.find_data_row(marker)
        if row:
            return row

    return None
```

#### S2-5 修复：授权页导航参数化

**当前**：硬编码 `/estack/web/estack/user-center/user-manage/user`

**改进**：从 `target_url` 推导回列表 URL：

```python
# 授权后导航回列表页
list_url = target_url  # 传入的目标页 URL 就是列表页
await page.goto(list_url, wait_until="networkidle", timeout=30000)
```

#### S2-6 修复：`close_dialog` 超时缩短

**当前**：Playwright locator 默认 30 秒超时

**改进**：
```python
async def close_dialog(page: Page) -> bool:
    try:
        # 先检查是否有可见的关闭按钮（缩短超时到 3 秒）
        close_btn = page.locator('.el-dialog__close:visible, .el-message-box__close:visible').first
        if await close_btn.count() > 0:
            await close_btn.click(timeout=3000)
            await page.wait_for_timeout(500)
            return True
        # 无可见关闭按钮，按 Escape
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False
```

#### S2-7 改进：全局请求监听

**借鉴 API_create 项目**：`page.on('request')` 捕获所有 API 请求

**改进 `request_interceptor.py`**：
- 当前：只捕获 `{base_url}/estack/api` 匹配的请求
- 改进：捕获所有 XHR/fetch 请求，分类时再过滤
- 新增 `--capture-all` 参数，默认仍使用过滤模式

```python
def _should_capture(self, url: str) -> bool:
    """判断是否应该捕获此请求"""
    if self.capture_all_mode:
        return self._is_xhr_or_fetch(url)
    # 默认模式：只捕获匹配的 API
    return f"{self.base_url}/estack/api" in url
```

---

## 四、Stage 3 优化：流程分析增强

### 4.1 当前问题

**文件**: `module_discovery/analyze_flow.py`（375行）

| # | 问题 | 影响 |
|---|------|------|
| S3-1 | CRUD 顺序纯静态（从 `CRUD_EXECUTION_ORDER` 过滤） | 无法发现模块特定的执行顺序 |
| S3-2 | 依赖分析只看顶层字段 | 嵌套依赖（`entity.data.id`）被遗漏 |
| S3-3 | 状态字段检测只查顶层 entity | 列表响应的状态字段被忽略 |
| S3-4 | `stage_validators.py` 字段名不匹配 | `validate_stage3` 永远报 order/api_mapping 缺失 |
| S3-5 | `_find_id_source` 总返回 "create" | 非创建来源的 ID 被误判 |

### 4.2 改进方案

#### S3-1 改进：执行顺序推导增强

**当前**：
```python
def _derive_order(core_apis: dict) -> list:
    return [step for step in const.CRUD_EXECUTION_ORDER if step in core_apis]
```

**改进**：结合按钮上下文推断实际顺序

```python
def _derive_order(core_apis: dict, all_endpoints: list) -> list:
    """推导执行顺序：静态优先级 + 上下文时序 + 依赖约束"""
    # 1. 静态优先级（baseline）
    static_order = [step for step in const.CRUD_EXECUTION_ORDER if step in core_apis]

    # 2. 上下文时序验证：检查按钮点击的实际顺序
    #    （interceptor 记录了时间戳，可以验证 create 确实在 update 之前）
    temporal_order = _verify_temporal_order(all_endpoints)

    # 3. 依赖约束：如果 B 的请求体引用了 A 的响应字段，A 必须在 B 之前
    dependency_order = _topological_sort_by_dependency(core_apis)

    # 4. 合并：以静态顺序为主，用依赖约束调整
    return _merge_orders(static_order, dependency_order)
```

#### S3-2 改进：深层依赖分析

**当前**：只检查 `entity.id`、`entity.userId` 等顶层字段

**改进**：递归遍历响应 JSON

```python
def _extract_ids_recursive(obj, path="", depth=0):
    """递归提取响应中的所有 ID 字段"""
    if depth > 5:
        return {}
    results = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in const.COMMON_ID_FIELDS and val:
                results[f"{path}.{key}"] = val
            elif isinstance(val, (dict, list)):
                results.update(_extract_ids_recursive(val, f"{path}.{key}", depth+1))
    elif isinstance(obj, list) and obj:
        results.update(_extract_ids_recursive(obj[0], f"{path}[0]", depth+1))
    return results
```

#### S3-3 改进：列表响应的状态字段检测

**当前**：`_find_state_value` 只检查 `isinstance(entity, dict)` 分支，列表分支是 `pass`

**改进**：
```python
elif isinstance(entity, list) and len(entity) > 0:
    first = entity[0] if isinstance(entity[0], dict) else {}
    vals = _find_state_value(first)
    if vals:
        if state_field is None:
            state_field = vals["field"]
        if vals["field"] == state_field:
            state_values[category] = vals["value"]
```

#### S3-4 修复：`stage_validators.py` 字段名不匹配

**当前 Bug**：
```python
# validate_stage3 检查的字段名
order = analysis.get("order", [])           # ❌ 实际是 "crud_order"
api_mapping = analysis.get("api_mapping", {})  # ❌ 实际是 "api_by_button"
```

**修复**：
```python
order = analysis.get("crud_order") or analysis.get("order", [])
api_mapping = analysis.get("api_by_button") or analysis.get("api_mapping", {})
```

#### S3-5 改进：ID 来源推断

**当前**：
```python
def _find_id_source(target_category, core_apis):
    if "create" in core_apis:
        return "create"
    return target_category
```

**改进**：从响应样本中实际查找哪个 CRUD 类别返回了 ID

```python
def _find_id_source(target_category, core_apis, response_samples):
    """从响应样本推断 ID 的实际来源"""
    # 优先检查 create 响应
    if "create" in core_apis:
        for ep in core_apis["create"]:
            samples = response_samples.get(ep["pathname"], [])
            for s in samples:
                body = _parse_json(s.get("body"))
                entity = body.get("entity") or body.get("data") or {}
                if isinstance(entity, dict) and "id" in entity:
                    return "create"

    # 回退：检查 query 响应
    if "query" in core_apis:
        return "query"

    return target_category
```

---

## 五、Stage 4 优化：脚本生成增强

### 5.1 当前问题

**文件**: `module_discovery/gen_test.py`（1149行）

| # | 问题 | 影响 |
|---|------|------|
| S4-1 | `browser_create_user` / `browser_authorize_user` 共 ~350 行内联 | estack 特定，不可复用 |
| S4-2 | Jinja2 模板基础设施已搭建但未使用 | 维护困难，f-string 转义易错 |
| S4-3 | Cookie domain `"10.151.37.249"` 硬编码 | 其他环境不兼容 |
| S4-4 | `query_fn_for_verify` 选择逻辑脆弱 | 所有 query 端点含 ID 时验证被跳过 |
| S4-5 | 生成脚本是单文件单体 | 无法单独运行某个步骤 |

### 5.2 改进方案

#### S4-1 改进：浏览器操作函数外置

**当前**：`browser_create_user` 和 `browser_authorize_user` 作为字符串字面量嵌入生成脚本

**改进**：提取为独立模块 `lib/browser_ops.py`

```python
# lib/browser_ops.py（新建）
def browser_create_resource(base_url, create_url, cookies_file, fill_data, submit_text="确定"):
    """通用浏览器创建资源：填表 → 提交 → 拦截响应拿 ID"""
    ...

def browser_authorize_resource(base_url, auth_url_template, cookies_file, resource_id):
    """通用浏览器授权：穿梭框选策略 → 提交"""
    ...
```

生成脚本中只引用：
```python
from lib.browser_ops import browser_create_resource, browser_authorize_resource
```

#### S4-2 改进：启用 Jinja2 模板

**当前**：`module_discovery/templates/` 目录不存在

**改进**：创建模板文件

```
module_discovery/templates/
├── script_header.py.j2      # 文件头、docstring、导入
├── api_function.py.j2       # 单个 API 调用函数
├── crud_step.py.j2          # 单个 CRUD 步骤（含断言）
├── main_function.py.j2      # main() 函数框架
└── browser_helpers.py.j2    # 浏览器辅助函数引用
```

**渐进迁移**：先模板化 API 函数定义和 CRUD 步骤（最重复的部分），保留 main() 和配置部分为代码生成。

#### S4-3 修复：Cookie domain 参数化

**当前**：
```python
await ctx.add_cookies([{"name": c["name"], "value": c["value"],
                        "domain": "10.151.37.249", "path": "/"} for c in cookies])
```

**改进**：从 `base_url` 提取域名

```python
domain = BASE_URL.replace("https://", "").replace("http://", "").split("/")[0]
await ctx.add_cookies([{"name": c["name"], "value": c["value"],
                        "domain": domain, "path": "/"} for c in cookies])
```

#### S4-4 改进：Query 函数选择增强

**当前**：只选第一个不含 32-hex ID 的 query 端点

**改进**：
```python
def _select_query_fn(core_apis):
    """选择用于验证的 query 函数，支持多种策略"""
    # 策略1: 列表查询（POST /list 或 GET /list）
    for ep in core_apis.get("query", []):
        if "/list" in ep["pathname"] or "/page" in ep["pathname"]:
            return f"api_query_{_safe_name(ep['pathname'])}"

    # 策略2: 详情查询（GET /resource/{id}）
    for ep in core_apis.get("detail", []):
        return f"api_detail_{_safe_name(ep['pathname'])}"

    # 策略3: 任意 query 端点
    for ep in core_apis.get("query", []):
        return f"api_query_{_safe_name(ep['pathname'])}"

    return None
```

#### S4-5 改进：模块化脚本结构

**当前**：单文件，所有步骤在 `main()` 中

**改进**：生成脚本支持单步骤运行

```python
# 生成的脚本结构
STEPS = {
    "create": step_create,
    "query": step_query,
    "update": step_update,
    "delete": step_delete,
}

def main():
    if len(sys.argv) > 1:
        # 单步骤模式: python script.py create
        step_name = sys.argv[1]
        STEPS[step_name]()
    else:
        # 全量模式
        for step in CRUD_ORDER:
            STEPS[step]()
```

---

## 六、经验库体系设计

### 6.1 当前状态

经验分散在 5 处：

| 文件 | 内容 | 问题 |
|------|------|------|
| `const.py` | 硬编码常量（选择器、关键词、顺序） | 不可运行时更新 |
| `probe_lessons_kb.json` | 37条反模式 + 7条API契约 | 手动维护，无版本化 |
| `base_nav_kb.json` | XPath/CSS选择器模板 | `button_driver.py` 未使用 |
| `config/` 下的 `cookies.json` 等 | 运行时数据 | 与经验混在一起 |
| 代码注释/日志 | 隐性经验 | 不可检索 |

### 6.2 统一经验库架构

```
config/
├── selector_patterns.json      # 层1: UI元素定位模式
├── operation_patterns.json     # 层2: 操作流程模式
├── debug_strategies.json       # 层3: 诊断策略
├── base_nav_kb.json            # 已有: XPath选择器模板（保留）
└── probe_lessons_kb.json       # 已有: 反模式库（保留，逐步迁移）
```

### 6.3 层1: `selector_patterns.json` — UI 元素定位模式

```json
{
  "version": "1.0",
  "updated_by": "角色管理",
  "updated_at": "2026-08-26",
  "patterns": {
    "table_row_selectors": {
      "description": "Element UI 表格行选择器（含固定列）",
      "main": ".el-table__body-wrapper tbody tr",
      "fixed_left": ".el-table__fixed .el-table__fixed-body-wrapper tbody tr",
      "fixed_right": ".el-table__fixed-right .el-table__fixed-body-wrapper tbody tr",
      "module_source": ["用户管理", "角色管理"],
      "confidence": 0.95,
      "usage_count": 2,
      "last_verified": "2026-08-26"
    },
    "table_action_button": {
      "description": "行操作按钮定位（固定右列优先）",
      "fallback_chain": [
        ".el-table__fixed-right td:last-child",
        ".el-table__fixed-body-wrapper td:last-child",
        ".el-table__body-wrapper td:last-child"
      ],
      "xpath_templates": "@base_nav_kb.json#/composite/table-action-button",
      "module_source": ["用户管理"],
      "confidence": 0.9
    },
    "more_dropdown_trigger": {
      "description": "更多下拉菜单触发器",
      "selectors": [".el-dropdown", "[class*='dropdown']", "button:has-text('更多')"],
      "click_method": "native_el_click",
      "note": "el-dropdown-selfdefine 只响应原生 el.click()",
      "module_source": ["用户管理"],
      "confidence": 1.0
    },
    "form_fill_patterns": [
      {"match": "名称", "template": "{username}", "type": "text"},
      {"match": "编码", "template": "code_{ts}", "type": "text"},
      {"match": "邮箱", "template": "at_{ts}@test.com", "type": "email"},
      {"match": "手机", "template": "138{ts}", "type": "tel"},
      {"match": "密码", "template": "Test@123456", "type": "password"}
    ]
  }
}
```

### 6.4 层2: `operation_patterns.json` — 操作流程模式

```json
{
  "version": "1.0",
  "operations": {
    "create_role_estack": {
      "description": "estack 角色创建流程",
      "module_pattern": "角色.*管理|role.*manage",
      "steps": [
        {"action": "click_toolbar", "button": "创建角色"},
        {"action": "fill_form", "fields": ["角色名称", "角色编码", "角色描述"]},
        {"action": "select_tree", "selector": ".el-tree", "strategy": "select_root"},
        {"action": "submit", "button": "确定"}
      ],
      "expected_apis": ["POST /policies", "GET /policies/list"],
      "module_source": "角色管理",
      "success_count": 1,
      "last_verified": "2026-08-26"
    },
    "row_operations_estack": {
      "description": "estack 行操作通用流程",
      "direct_buttons": ["编辑", "删除"],
      "dropdown_trigger": "更多",
      "dropdown_items": ["冻结", "启用", "锁定", "解锁", "重置密码", "授权"],
      "special_handling": {
        "删除": "confirm_message_box",
        "授权": "full_page_jump"
      },
      "module_source": ["用户管理", "角色管理"],
      "confidence": 0.9
    }
  }
}
```

### 6.5 层3: `debug_strategies.json` — 诊断策略

```json
{
  "version": "1.0",
  "strategies": {
    "row_not_found": {
      "description": "表格行查找失败时的诊断流程",
      "trigger": "find_data_row returns None after 3 retries",
      "steps": [
        {"action": "screenshot", "scope": "full_page"},
        {"action": "dump_table_structure", "include": ["wrappers", "fixed_columns", "row_count"]},
        {"action": "test_selectors", "selectors_from": "selector_patterns.json#table_row_selectors"},
        {"action": "search_marker_in_page", "method": "textContent"},
        {"action": "check_pagination", "include": ["current_page", "total_pages"]}
      ],
      "auto_fix": true,
      "fix_target": "selector_patterns.json"
    },
    "button_click_failed": {
      "description": "按钮点击失败时的诊断流程",
      "trigger": "click_row_button_v2 returns False after 2 retries",
      "steps": [
        {"action": "screenshot", "scope": "row_area"},
        {"action": "dump_row_cells", "include": ["text", "buttons", "dropdowns"]},
        {"action": "check_fixed_right", "selectors_from": "selector_patterns.json#table_action_button"},
        {"action": "dump_fixed_right_dom", "depth": 3}
      ],
      "auto_fix": true,
      "fix_target": "selector_patterns.json"
    },
    "create_failed": {
      "description": "创建操作失败时的诊断流程",
      "trigger": "_handle_create_page_with_retry exhausts retries",
      "steps": [
        {"action": "screenshot", "scope": "full_page"},
        {"action": "dump_form_state", "include": ["labels", "values", "errors", "required"]},
        {"action": "dump_interceptor_state", "include": ["call_count", "last_calls"]},
        {"action": "check_form_validation_errors", "selector": ".el-form-item__error"}
      ],
      "auto_fix": false,
      "output_to": "output/debug/{module}/"
    }
  }
}
```

### 6.6 合并策略

**规则**：
1. **按 `pattern_key` 去重**：同一 key 的模式合并，不覆盖
2. **`module_source` 合并**：set union，保留所有来源
3. **`confidence` 取最大值**：高置信度优先
4. **`selectors` 合并**：去重后保留顺序
5. **冲突检测**：同一 key 的 selectors 来自不同模块且不一致 → 标记 `conflict: true`，输出待审核列表

**实现**：`module_discovery/kb_merger.py`

```python
class KBMerger:
    def merge_pattern(self, new: dict, existing: dict) -> dict:
        key = new["key"]
        if key not in existing:
            return new
        old = existing[key]
        return {
            **old,
            "module_source": list(set(old.get("module_source", []) + new.get("module_source", []))),
            "confidence": max(old.get("confidence", 0), new.get("confidence", 0)),
            "usage_count": old.get("usage_count", 0) + new.get("usage_count", 1),
            "selectors": self._merge_lists(old.get("selectors", []), new.get("selectors", [])),
            "last_verified": max(old.get("last_verified", ""), new.get("last_verified", "")),
        }

    def detect_conflicts(self, patterns: list) -> list:
        """检测冲突模式"""
        conflicts = []
        grouped = defaultdict(list)
        for p in patterns:
            grouped[p["key"]].append(p)
        for key, group in grouped.items():
            if len(group) > 1:
                selectors = [json.dumps(p.get("selectors"), sort_keys=True) for p in group]
                if len(set(selectors)) > 1:
                    conflicts.append({"key": key, "patterns": group})
        return conflicts
```

### 6.7 版本化与清理

- 每次更新 `selector_patterns.json` 时 `version` 自增
- `changelog` 记录修改（模块来源、修改内容、时间戳）
- `usage_count < 3` 且 `confidence < 0.5` 的模式标记 `deprecated`
- `last_verified` 超过 30 天的模式标记 `needs_verification`

---

## 七、自诊断机制

### 7.1 触发条件（代码固化）

在 `capture_apis.py` 的关键位置嵌入触发器：

```python
# 触发条件1: 行查找连续失败
if not row and consecutive_row_failures >= 2:
    await _trigger_diagnostic(page, marker, "row_not_found")

# 触发条件2: 按钮点击连续失败
if not click_ok and consecutive_click_failures >= 2:
    await _trigger_diagnostic(page, marker, "button_click_failed")

# 触发条件3: Stage 2 验证失败且重试耗尽
if not stage2_valid and recapture_count >= max_recapture:
    await _trigger_diagnostic(page, marker, "stage2_validation_failed")
```

### 7.2 诊断模式实现

**新建**：`module_discovery/diagnostic_mode.py`

```python
class DiagnosticMode:
    """自诊断模式：失败时自动分析 DOM 结构并尝试修复"""

    def __init__(self, page, module_name, debug_dir):
        self.page = page
        self.module_name = module_name
        self.debug_dir = debug_dir / f"diagnostic_{int(time.time())}"
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    async def run_strategy(self, strategy_name: str, **kwargs) -> dict:
        """执行诊断策略"""
        strategies = load_debug_strategies()
        strategy = strategies.get(strategy_name)
        if not strategy:
            LOG.warning(f"未知诊断策略: {strategy_name}")
            return {}

        results = {}
        for step in strategy["steps"]:
            action = step["action"]
            if action == "screenshot":
                results["screenshot"] = await self._screenshot(step.get("scope"))
            elif action == "dump_table_structure":
                results["table_structure"] = await self._dump_table_structure()
            elif action == "test_selectors":
                results["selector_tests"] = await self._test_selectors(step.get("selectors_from"))
            elif action == "search_marker_in_page":
                results["marker_locations"] = await self._search_marker(kwargs.get("marker"))
            elif action == "dump_row_cells":
                results["row_cells"] = await self._dump_row_cells(kwargs.get("row"))
            elif action == "check_pagination":
                results["pagination"] = await self._check_pagination()

        # 保存诊断结果
        self._save_results(results)

        # 尝试自动修复
        if strategy.get("auto_fix"):
            fix = self._attempt_auto_fix(results, strategy.get("fix_target"))
            results["fix_applied"] = fix

        return results
```

### 7.3 诊断输出格式

```
output/debug/{module}/diagnostic_{timestamp}/
├── screenshot.png              # 页面截图
├── table_structure.json        # 表格 DOM 结构
├── selector_tests.json         # 选择器命中率测试
├── marker_locations.json       # marker 文本位置
├── pagination.json             # 分页状态
└── diagnosis_summary.json      # 诊断结论 + 修复建议/已应用的修复
```

### 7.4 自动修复流程

```
诊断 → 发现新选择器 → 更新 selector_patterns.json → 重新尝试操作
                          ↓
                    合并策略检查冲突
                          ↓
                    成功 → 继续管道
                    失败 → 记录到 diagnosis_summary.json，人工介入
```

---

## 八、跨阶段改进

### 8.1 Stage 3 字段名 Bug 修复（立即修复）

**文件**: `module_discovery/stage_validators.py`

```python
# validate_stage3 中的字段名修正
order = analysis.get("crud_order") or analysis.get("order", [])
api_mapping = analysis.get("api_by_button") or analysis.get("api_mapping", {})
```

### 8.2 `const.py` 中未使用的常量

- `FORM_FILL_RULES` 和 `PLACEHOLDER_RULES`：被 `form_filler.py` 的硬编码 `fill_data` 替代
- `STATE_LIKE_VALUES`：定义但从未在 `analyze_flow.py` 中引用

**处理**：
- 将 `FORM_FILL_RULES` 迁移到 `selector_patterns.json` 的 `form_fill_patterns`
- 在 `_derive_state_rules` 中使用 `STATE_LIKE_VALUES` 验证状态值

### 8.3 数据清理

Stage 2 每次运行创建 autotest 角色但未清理（失败时）。

**改进**：
1. 在 `capture_all` 结束时，如果删除操作未执行，通过 API 清理创建的资源
2. 在 `run.py` 启动时，检查并清理残留的 autotest 资源

---

## 九、实施优先级与排期

### Phase 1: 紧急修复（1-2天）

| 优先级 | 改动 | 文件 | 理由 |
|--------|------|------|------|
| P0 | S2-2: 用 KB 模板修复固定列按钮 | `button_driver.py` | 当前 blocker |
| P0 | S3-4: 修复字段名不匹配 | `stage_validators.py` | 一行修复，影响验证 |
| P1 | S2-6: close_dialog 超时缩短 | `button_driver.py` | 每次节省 30 秒 |
| P1 | S2-5: 授权页导航参数化 | `capture_apis.py` | 跨模块兼容 |

### Phase 2: Stage 1+2 增强（2-3天）

| 优先级 | 改动 | 文件 |
|--------|------|------|
| P1 | S1-1: 固定列行操作探测 | `discover_ui.py` |
| P1 | S1-3: Snapshot 页面结构 | `discover_ui.py` |
| P1 | S2-1: 行操作列表动态化 | `capture_apis.py` |
| P2 | S2-3: 表单填充动态化 | `form_filler.py` |
| P2 | S2-4: 分页感知 | `button_driver.py` |
| P2 | S2-7: 全局请求监听 | `request_interceptor.py` |

### Phase 3: Stage 3+4 增强（2-3天）

| 优先级 | 改动 | 文件 |
|--------|------|------|
| P1 | S3-1: 执行顺序推导增强 | `analyze_flow.py` |
| P1 | S3-2: 深层依赖分析 | `analyze_flow.py` |
| P2 | S3-3: 列表响应状态检测 | `analyze_flow.py` |
| P2 | S4-1: 浏览器操作函数外置 | `gen_test.py` + `lib/browser_ops.py` |
| P2 | S4-3: Cookie domain 参数化 | `gen_test.py` |
| P3 | S4-2: Jinja2 模板化 | `gen_test.py` + `templates/` |
| P3 | S4-4: Query 函数选择增强 | `gen_test.py` |
| P3 | S4-5: 模块化脚本结构 | `gen_test.py` |

### Phase 4: 经验库 + 自诊断（3-4天）

| 优先级 | 改动 | 文件 |
|--------|------|------|
| P1 | 创建 `selector_patterns.json` | `config/` |
| P1 | 创建 `operation_patterns.json` | `config/` |
| P1 | 创建 `debug_strategies.json` | `config/` |
| P2 | 实现 `diagnostic_mode.py` | `module_discovery/` |
| P2 | 实现 `kb_merger.py` | `module_discovery/` |
| P2 | 嵌入诊断触发器 | `capture_apis.py` |
| P3 | 版本化 + 清理机制 | `kb_merger.py` |

---

## 十、验证方案

### Phase 1 验证
```bash
# 角色管理 Stage 2：行操作应成功
MSYS_NO_PATHCONV=1 python -m module_discovery.run --project ecm-compute \
    --url "/estack/web/estack/user-center/user-manage/role" \
    --module "角色管理" --stage 2 --force --headless
# 预期：不再出现 "未找到行内更多按钮"
```

### Phase 2 验证
```bash
# Stage 1 输出应包含 page_structure 和 fixed-right 行操作
# _ui.json 中 row_actions 应包含 source: "fixed-right"
```

### Phase 3 验证
```bash
# Stage 3 分析结果应包含完整的 crud_order 和 dependencies
# Stage 4 生成的脚本应能独立运行
```

### Phase 4 验证
```bash
# 人为制造选择器错误 → 诊断模式自动触发 → 自动修复 → 管道继续
# 多模块运行后 → selector_patterns.json 正确合并 → 无冲突
```

### 端到端验证
```bash
# 完整四阶段运行
MSYS_NO_PATHCONV=1 python -m module_discovery.run --project ecm-compute \
    --url "/estack/web/estack/user-center/user-manage/role" \
    --module "角色管理" --stage all --force --headless
# 预期：生成包含完整 CRUD 步骤的测试脚本
```

---

## 十一、关键文件清单

| 文件 | 改动类型 | Phase |
|------|----------|-------|
| `module_discovery/button_driver.py` | 改造 | 1 |
| `module_discovery/stage_validators.py` | 修复 | 1 |
| `module_discovery/capture_apis.py` | 改造 | 1,2,4 |
| `module_discovery/discover_ui.py` | 改造 | 2 |
| `module_discovery/form_filler.py` | 改造 | 2 |
| `module_discovery/request_interceptor.py` | 改造 | 2 |
| `module_discovery/analyze_flow.py` | 改造 | 3 |
| `module_discovery/gen_test.py` | 改造 | 3 |
| `module_discovery/diagnostic_mode.py` | 新建 | 4 |
| `module_discovery/kb_merger.py` | 新建 | 4 |
| `config/selector_patterns.json` | 新建 | 4 |
| `config/operation_patterns.json` | 新建 | 4 |
| `config/debug_strategies.json` | 新建 | 4 |
| `lib/browser_ops.py` | 新建 | 3 |
| `module_discovery/templates/*.j2` | 新建 | 3 |

---

## 十二、待讨论问题

1. **经验库存储格式**：JSON vs YAML vs SQLite？当前方案用 JSON，但如果经验条目超过 100 条，JSON 的合并和查询效率可能不够。
2. **诊断模式的自动化程度**：是完全自动修复（更新选择器后自动重试），还是诊断后暂停等待人工确认？
3. **Stage 4 的 `browser_create_user`**：是提取为 `lib/browser_ops.py` 通用函数，还是保留内联但用 Jinja2 模板化？
4. **经验库的初始数据**：是从现有的 `probe_lessons_kb.json` + `base_nav_kb.json` 自动迁移，还是手动重建？
5. **跨项目复用**：经验库是项目级（`projects/ecm-compute/config/`）还是全局级（`config/`）？当前方案是全局级。
