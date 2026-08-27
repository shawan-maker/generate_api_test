# 模块级 API 自动化发现与测试脚本生成 · 方案设计

> **核心目标**：给定一个模块入口 URL + 登录凭证，AI 自动完成四个步骤：
> ① 前端探测（找出页面上所有功能按钮和操作元素）
> ② API 捕获（模拟点击每个按钮，拦截对应的 API 请求和响应）
> ③ 逻辑分析（从 API 信息中推导 CRUD 顺序 + 状态检查规则）
> ④ 脚本生成（输出可执行的 API 测试脚本，含状态断言）
>
> 解决的问题：**没有 API 文档的后台系统**，无需人工抓包，自动生成 API 测试脚本。

---

## 目录

1. [整体架构](#1-整体架构)
2. [模块目录与文件结构](#2-模块目录与文件结构)
3. [阶段一：前端按钮探测 —— 核心难点](#3-阶段一前端按钮探测)
4. [阶段二：API 捕获](#4-阶段二api-捕获)
5. [阶段三：逻辑顺序与关联分析 —— 核心难点](#5-阶段三逻辑顺序与关联分析)
6. [阶段四：测试脚本生成](#6-阶段四测试脚本生成)
   - 6.4 [实战驱动层经验（estack 实测，探测新模块前必读）](#64-实战驱动层经验estack-实测-2026-08-24探测新模块前必读)
7. [执行入口与用法](#7-执行入口与用法)
8. [与现有组件的集成关系](#8-与现有组件的集成关系)

---

## 1. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                      module_discovery（新增模块）               │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │  Stage 1     │    │  Stage 2     │    │  Stage 3     │       │
│  │  discover_ui │───▶│  capture_api │───▶│  analyze     │       │
│  │              │    │              │    │              │       │
│  │  · 按钮探测   │    │  · 点击按钮  │    │  · CRUD推导   │       │
│  │  · 表单字段   │    │  · 拦截XHR  │    │  · 顺序编排   │       │
│  │  · 下拉菜单   │    │  · 响应采集  │    │  · 状态推导   │       │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘       │
│         │                   │                   │                │
│         ▼                   ▼                   ▼                │
│  ┌──────────────────────────────────────────────────────┐       │
│  │              Stage 4: gen_test                        │       │
│  │  生成可执行的 API 测试脚本（含 CRUD 顺序 + 状态断言）  │       │
│  └──────────────────────────────────────────────────────┘       │
│                                                                  │
│  复用: lib/auth.py (Cookie优先+滑块兜底)                        │
│  复用: lib/slider.py (滑块识别)                                 │
│  复用: lib/api_client.py (API调用)                              │
│  复用: discovery/form_extractor.py (表单字段抽取)               │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. 模块目录与文件结构

### 2.1 目录结构

```
API_AI_test/
│
├── module_discovery/                    ←【新增】模块级发现引擎
│   ├── __init__.py
│   ├── discover_ui.py                  # Stage 1: 页面按钮/元素探测
│   ├── capture_apis.py                 # Stage 2: 点击+API拦截捕获
│   ├── analyze_flow.py                 # Stage 3: 逻辑顺序分析+状态推导
│   ├── gen_test.py                     # Stage 4: 生成可执行测试脚本
│   └── const.py                        # 按钮探测策略、CRUD关键词表等常亮
│
├── projects/<id>/
│   ├── kb/
│   │   └── module_discovered/          ←【新增】按模块存放的发现结果
│   │       ├── 用户管理.json            # 按钮清单+API映射+响应样本
│   │       └── ...
│   │
│   ├── api/                            # 【已有】API层薄函数
│   ├── flows/                          ←【新增】自动生成的测试脚本
│   │   ├── 用户管理_API测试.py
│   │   └── ...
│   └── tests/                          # 【已有】pytest测试（手动编排）
```

### 2.2 数据流转

```
入口: URL + profile.yaml
     │
     ▼
Stage 1 ──▶ buttons.json  ← 包括按钮文本、位置、下拉菜单子项
     │
     ▼
Stage 2 ──▶ module_discovered.json  ← 按钮→API映射、请求体样例、响应样本
     │
     ▼
Stage 3 ──▶ (分析结果，不单独落盘，直接输入Stage 4)
     │       ├── crud_order: [创建, 查询, 修改, 锁定, 解锁, 删除]
     │       ├── action_apis: {创建: [...], 查询: [...], ...}
     │       ├── dependency_chain: {修改依赖创建返回的id, ...}
     │       └── state_assertions: {创建后state=ENABLE, 锁定后state=LOCKED, ...}
     ▼
Stage 4 ──▶ flows/<模块名>_API测试.py  ← 可执行的Python脚本
```

---

## 3. 阶段一：前端按钮探测

这是整个流程的基石。**按钮找不全，后续 API 捕获就不完整。**

### 3.1 面临的挑战

| 挑战 | 说明 |
|------|------|
| 多种UI框架 | Element UI / Ant Design / 原生HTML / 自定义组件，选择器各不相同 |
| 按钮层级 | 表头工具栏按钮 vs 表格行内操作 vs 弹窗内按钮 vs 下拉菜单子项 |
| 不可见按钮 | 需要滚动才出现的按钮、权限控制隐藏的按钮、分页后才出现的操作 |
| 动态加载 | 点击后才出现的下拉菜单、条件显示的操作按钮 |
| 重复去重 | 相同文本的按钮在不同场景出现（"编辑"可能每行一个） |

### 3.2 多策略综合探测方案

#### 策略一：CSS 选择器地毯式扫描（通用兜底）

借鉴 EcsCloud `用户管理_按钮扫描_捕获API.js` 的 `discoverButtons()` 和 API_cases 的 `page.getByRole('button')`，合并成一个覆盖最广的选择器集合：

```python
# discover_ui.py 中的选择器集合
BUTTON_SELECTORS = """
    button, a[href],                                  /* 原生按钮+链接 */
    .el-button, .el-button--text,                     /* Element UI 按钮 */
    .el-dropdown-menu__item,                          /* Element UI 下拉菜单项 */
    .el-table__body-wrapper a,                        /* 表格内链接（编辑/删除） */
    .el-table__body-wrapper .el-button,               /* 表格内按钮 */
    .el-menu-item, .el-submenu__title,                /* 侧边栏菜单 */
    [role="button"], [role="menuitem"],               /* ARIA 角色 */
    [class*="btn"], [class*="Btn"],                   /* 包含 btn 的类名 */
    [class*="action"], [class*="Action"],              /* 包含 action 的类名 */
    .ant-btn, .ant-menu-item,                         /* Ant Design 按钮/菜单 */
    .ant-dropdown-menu-item,                          /* Ant Design 下拉菜单 */
    span[onclick],                                     /* 通过 onclick 触发】
"""
```

**关键改进**：先用 CSS 选择器扫到所有候选元素，再用 `getBoundingClientRect()` 过滤掉不可见的部分。

#### 策略二：按钮文本关键词智能识别

不只看选择器，还根据按钮文本的意义做初步分类，便于后续优先操作"功能按钮"：

```python
# const.py
ACTION_KEYWORDS = {
    'create':    ['新增', '创建', '添加', '新建', '增加', '录入', '登记',
                  '注册', '申请', '开通', '购买', '订购', '下达'],
    'delete':    ['删除', '移除', '清除', '销毁', '释放', '回收'],
    'update':    ['编辑', '修改', '更改', '更新', '变更', '设置', '配置'],
    'query':     ['搜索', '查询', '查找', '筛选', '过滤', '检索',
                  '刷新', '翻页'],
    'lock':      ['锁定', '冻结', '封禁', '暂停', '停用', '禁用',
                  '关机', '关闭'],
    'unlock':    ['解锁', '解冻', '启用', '恢复', '开机', '开启', '激活'],
    'reset':     ['重置', '重置密码', '修改密码', '初始化'],
    'export':    ['导出', '下载', '批量导出'],
    'import':    ['导入', '上传', '批量导入'],
    'batch':     ['批量', '批量操作', '批量删除'],
    'approve':   ['审批', '通过', '同意', '驳回', '拒绝'],
    'confirm':   ['确定', '确认', '提交', '保存', '完成', '下一步', '立即创建'],
}
```

#### 策略三：区分按钮层级（DOM 位置分析）

```python
def classify_button_location(page, button_element):
    """
    根据按钮在 DOM 中的位置，判断其层级：
    
    - toolbar_buttons:  表头工具栏的按钮（新增用户、批量删除等）
      判断: 按钮在表格 table 或 el-table 的上方
    
    - row_actions:     表格行内的操作（编辑、删除、更多等）
      判断: 按钮在表格的 td 或 el-table-column 内
    
    - dialog_buttons:   弹窗内的按钮（确定、取消等）
      判断: 按钮在 el-dialog 或 ant-modal 内
    
    - menu_items:      侧边栏或下拉菜单项
      判断: 按钮在 el-menu 或 el-dropdown-menu 内
    """
```

这个分类对后续的操作策略至关重要：
- `toolbar_buttons` → 直接点击（全局操作）
- `row_actions` → 先选中一条数据再点击（需要表格有数据）
- `dialog_buttons` → 弹窗关闭时扫描，不在页面级别重复点击
- `menu_items` → 如果是侧边栏菜单，说明是子页面跳转，不在此处理

#### 策略四：表格行内操作按钮的自动采样（关键）

表格行内按钮（"编辑"、"删除"）每行都有相同的文本，点击任意一行都可以。但必须**先确保表格有数据**。

```python
async def sample_row_actions(page):
    """
    如果表格为空，不需要跳过行操作按钮——创建完成后自然会有点击目标的。
    但在阶段1探测时(可能还没数据)，检测到行内按钮即可记录其存在：
    
    1. 扫描表格行中的按钮文本列表（去重）
    2. 记录每个按钮的文本和表中位置（第几列）
    3. 不点击——等到阶段2-3需要时才在有数据的行上点击
    """
```

#### 策略五：下拉菜单的递归展开

```python
async def discover_dropdowns(page, button_list):
    """
    对于每个看起来有下拉菜单的按钮（"更多"、"操作"等）：
    1. 点击它
    2. 等待下拉菜单出现
    3. 扫描 el-dropdown-menu 中的可见子项
    4. 添加到按钮列表中（标记为 dropdown_item + parent）
    5. 关闭菜单
    """
```

### 3.3 完整探测流程

```python
async def discover_all(page) -> dict:
    """
    返回完整按钮清单，结构如下：
    
    {
        "toolbar_buttons": [
            {"text": "新增用户", "tag": "BUTTON", "rect": "80x32",
             "categories": ["create"], ...},
            {"text": "批量删除", "tag": "BUTTON", "categories": ["delete", "batch"]},
        ],
        "row_actions": [
            {"text": "编辑", "is_row_action": True},
            {"text": "删除", "is_row_action": True},
            {"text": "更多", "has_dropdown": True, "children": [
                {"text": "锁定"}, {"text": "重置密码"},
            ]},
        ],
        "dialog_buttons": [...],      # 弹窗内按钮（来自弹窗扫描）
        "form_fields": [...],          # 弹窗内的表单字段
        "summary": {
            "total": 12,
            "toolbar": 2,
            "row_actions": 4,
            "has_create": True,        # 是否有创建功能
            "has_delete": True,        # 是否有删除功能
            "has_form": True,          # 是否有弹窗表单（说明需要填参数）
        }
    }
    """
```

---

## 4. 阶段二：API 捕获

### 4.1 核心机制

```python
# API 拦截器（监听 request + response）

all_calls = []  # 所有拦截的API请求
samples = {}    # 响应样本（每个path最多保存2份）

page.on("request", callback)   # 记录 method / url / body / context
page.on("response", callback)  # 记录 status / response_body
```

每个 API 被记录为一个结构：

```python
{
    "method": "POST",
    "pathname": "/estack/api/estack/draco/v1/users/create",
    "body": '{"name":"...","userName":"...","...":"..."}',
    "context": "click:新增用户",       # 标记由哪个按钮触发
    "response": {
        "status": 200,
        "body": '{"success":true,"entity":{"id":"xxx","state":"ENABLE",...}}'
    },
    "ts": 1724123456
}
```

### 4.2 按钮点击策略

```python
async def capture_by_clicking(page, buttons) -> dict:
    """
    对每个按钮做以下操作：
    
    1. toolbar_buttons（表头操作）：
       - 直接点击
       - 等待 5 秒（收集API）
       - 如果是"创建"类操作，此时弹窗 →
         a) 扫描弹窗表单字段（复用 form_extractor.extract_el_dialog_fields）
         b) 填写随机测试数据
         c) 点击提交按钮（"确定"/"保存"）
         d) 再等 3 秒（收集创建API）
         e) 关闭弹窗
       - 如果是"删除"类操作，此时有确认弹窗 →
         a) 点击"确定"确认
         b) 等 3 秒
       
    2. row_actions（行操作）：
       - 如果表格有数据：点击第一行的操作按钮
       - 如果表格无数据：跳过（等待创建完再操作）
       
    3. 下拉菜单：
       - 点击父按钮 → 展开菜单
       - 逐个点击子项
       - 关闭菜单
    
    4. 每次点击后必须关闭弹窗（close_dialog）
    """
```

### 4.3 「创建后填表」的关键设计

创建类操作需要填表才能触发真正的 API。直接点击"新增用户"按钮，如果不填写表单，只会有弹窗初始化请求，没有创建 API。

**解决方案**：在阶段一探测时，已经获取了表单字段信息。阶段二点击创建按钮后，根据字段类型自动生成测试数据：

```python
# const.py
FORM_FILL_RULES = {
    "input[type='text']":          "AT_test_" + timestamp,
    "input[type='email']":         "at_" + timestamp + "@test.com",
    "input[type='tel']":           "13800138000",
    "input[type='password']":      "Test@123456",
    "input[placeholder*='名称']":  "AT_名称_" + timestamp,
    "input[placeholder*='用户名']": "atuser_" + timestamp,
    "input[placeholder*='邮箱']":   "at_" + timestamp + "@test.com",
    "input[placeholder*='手机']":   "138" + timestamp[-8:],
    "textarea":                    "自动创建于" + timestamp,
    ".el-select":                  "[不填写，保持默认选项]",
    ".el-switch":                  "[不操作，保持默认值]",
    ".el-date-editor":             "[不操作，保持默认]",
}
```

> **重要**：当前版本**只填写文本输入类字段**。下拉选择、开关等复杂控件暂不填，提交时由后端使用默认值。后续版本可由"有效组合发现器"(combo_finder.py) 补齐。

### 4.4 去重与归类

```python
def deduplicate_and_classify(all_calls) -> dict:
    """
    1. 按 method + pathname 去重，合并 contexts（触发按钮列表）
    2. 按 URL 路径模式归类：
       - /create, /add, /save     → 创建
       - /update, /edit, /modify  → 修改
       - /delete, /remove         → 删除
       - /list, /page, /search    → 查询
       - /detail                  → 详情
       - /lock                    → 锁定
       - /unlock, /enable         → 解锁
       - /disable                 → 停用
       - /export                  → 导出
       - /import                  → 导入
       - /reset-password          → 重置密码
       - 其他 POST                → 其他操作
       - 其他 GET                 → 信息查询
    3. 保留每个端点的请求体样本（最多3份）
    4. 保留每个端点的响应样本（最多2份）
    """
```

---

## 5. 阶段三：逻辑顺序与关联分析

这是全流程中**最关键的智能部分**。在阶段二中我们拿到了几十个 API，有增删改查、有状态变更、有信息查询，如何推导出正确的执行顺序？

### 5.1 核心挑战

| 难题 | 具体表现 |
|------|----------|
| **按钮和 API 是多对多映射** | 点击"新增用户"可能触发 3 个 API（权限校验、动态字典、创建用户），要从中找出哪个是主操作 |
| **API 间有数据依赖** | 修改/删除/锁定都需要用户 id，必须等创建完拿到 id 后才能操作 |
| **不是所有 API 都要变成测试步骤** | 菜单加载、主题查询等基础设施 API 不应出现在测试脚本里 |
| **状态检查怎么自动推导** | 创建用户后应该检查状态是"激活"还是"锁定"？响应中 state 字段会有哪些合法值？ |

### 5.2 分析流水线

```python
def analyze_flow(api_map, button_map) -> FlowAnalysis:
    """
    四步流水线分析：
    
    Step 1: API角色分类     → 区分"核心CRUD API"和"辅助API"
    Step 2: 按钮-API映射    → 建立 "按钮名 → API列表" 的关联
    Step 3: CRUD顺序编排    → 推导正确的执行顺序
    Step 4: 状态断言推导    → 从响应样本自动找出状态字段+合法值
    """
```

### 5.3 Step 1：API 角色分类

识别"哪个 API 才是真正的业务操作"——这是最容易被忽略的关键问题。点击"新增用户"后产生的 API 可能包含：

```
POST /pegasi/v1/menu/tree                       ← 辅助（菜单加载）
POST /delphini/v1/dynamic-dictionary/json        ← 辅助（国际化字典）
GET  /draco/v1/tenants/users                    ← 辅助（用户列表刷新）
POST /draco/v1/users/create                     ← 核心（创建用户）✅
GET  /pegasi/v1/menu/favorite/list              ← 辅助（菜单收藏）
GET  /libra/v1/notice/new/count/all-type        ← 辅助（通知计数）
```

**分类方法**：

```python
def classify_api_role(method, pathname, body_snapshot) -> str:
    """
    三路证据综合判断：
    
    证据1：URL 关键词
      - 包含 /create → 核心-创建
      - 包含 /delete → 核心-删除
      - 包含 /update → 核心-修改
      - 包含 /list /page /search → 核心-查询
      - 包含 /detail → 核心-详情
      - 包含 /menu /notice /theme /favorite /dynamic-dictionary → 辅助
      - 包含 /users/current-user /authority/national → 辅助
      
    证据2：HTTP 方法 + 是否有 body
      - POST + 有复杂 body → 很可能是核心操作
      - GET → 大概率是辅助查询
      
    证据3：按钮上下文匹配
      - 创建按钮点击后出现的 POST /create API → 核心
      - 删除按钮点击后出现的 DELETE /POST /delete → 核心
      - 任何按钮点击都出现的相同 API → 辅助
      
    优先级：证据3 > 证据1 > 证据2
    """
```

**同现频率过滤法**（去辅助 API 的利器）：

```python
def filter_supporting_apis(all_apis) -> tuple:
    """
    统计每个 API 出现在多少个不同的按钮上下文下。
    
    如果一个 API 在所有按钮点击后都会出现，
    那它肯定是辅助 API（菜单、通知、字典……）→ 归入"辅助"类。
    
    如果一个 API 只在特定按钮点击后出现，
    那它很可能是该按钮的核心操作 → 归入"核心"类。
    """
```

### 5.4 Step 2：按钮-API 映射

在阶段一探到的按钮和阶段二捕获的 API 之间建立关联：

```
"新增用户"  →  POST /draco/v1/users/create         [创建]
"编辑"      →  POST /draco/v1/users/update         [修改]  
"锁定"      →  POST /draco/v1/users/lock           [状态变更]
"解锁"      →  POST /draco/v1/users/unlock         [状态变更]
"删除"      →  POST /draco/v1/users/delete         [删除]
"更多→重置密码" → POST /draco/v1/users/reset-password [操作]
"查询/刷新" →  POST /draco/v1/users/list           [查询]
"用户详情"  →  GET  /draco/v1/users/detail         [详情]
```

**按钮名到 CRUD 的自动映射**：

```python
def map_button_to_crud(button_text) -> str:
    """
    根据按钮文本智能匹配 CRUD 类别。
    
    规则优先级：
    1. 精确匹配预定义关键字表
    2. 包含关键字模糊匹配
    3. 分析上下文（如果按钮是"更多"的子项，其类别需结合父按钮）
    
    关键字表见 const.py 的 ACTION_KEYWORDS
    """
```

### 5.5 Step 3：CRUD 顺序编排

这是最核心的推理步骤。根据已经识别出的核心 API 集合和它们的 CRUD 类别，自动推导执行顺序：

```python
def derive_execution_order(classified_apis) -> list:
    """
    固定的优先级执行顺序（在所有模块中都适用）：
    
    ┌─────────────────────────────────────────────────┐
    │  执行顺序（有则执行，无则跳过）：                   │
    │                                                   │
    │  1. 创建 （CREATE）—— 必须先造数据                 │
    │     ↓                                             │
    │  2. 查询 （LIST） —— 验证数据出现                  │
    │     ↓                                             │
    │  3. 详情 （DETAIL） —— 查看单条数据                │
    │     ↓                                             │
    │  4. 修改 （UPDATE） —— 编辑数据                    │
    │     ↓                                             │
    │  5. 查询 （LIST） —— 验证修改生效                  │
    │     ↓                                             │
    │  6. 状态变更 1 —— 锁定（LOCK）                     │
    │     ↓                                             │
    │  7. 查询 （LIST） —— 验证状态变为 LOCKED           │
    │     ↓                                             │
    │  8. 状态变更 2 —— 解锁（UNLOCK）                   │
    │     ↓                                             │
    │  9. 查询 （LIST） —— 验证状态恢复                  │
    │     ↓                                             │
    │  10. 其他操作（重置密码、审批等）                   │
    │     ↓                                             │
    │  11. 删除 （DELETE） —— 最后清理数据               │
    │     ↓                                             │
    │  12. 查询 （LIST） —— 验证数据消失                 │
    └─────────────────────────────────────────────────┘
    
    其中第6-9步是可选的——只有存在 锁定/解锁 相关API时才加入。
    
    关键原则：
    - 创建永远是第一个
    - 删除永远是最后一个
    - 状态变更后必须跟一个查询来验证状态
    - 每个步骤产生的 id 等数据自动传递给后续步骤
    """
```

**数据依赖链的自动推导**：

```python
def derive_dependency_chain(classified_apis, response_samples) -> dict:
    """
    从响应样本中分析数据依赖关系：
    
    创建 API 的响应中会包含新创建资源的 id：
        {"success": true, "entity": {"id": "abc123", "state": "ENABLE", ...}}
    
    修改/删除/锁定 API 的请求体中会引用这个 id：
        POST /draco/v1/users/update  body: {"id": "abc123", ...}
        POST /draco/v1/users/delete  body: {"id": "abc123"}
        POST /draco/v1/users/lock    body: {"id": "abc123"}
    
    推导逻辑：
    1. 从所有 API 的请求体样本中，提取常见的"ID"字段名
       → 常见字段: id, userId, ids, resourceId, projectId, tenantId, ...
    2. 从创建 API 的响应中找到对应的值 → 这就是后续步骤的输入
    3. 自动把 id 注入到后续 API 的 body 中
    """
```

### 5.6 Step 4：状态断言自动推导

这是比 CRUD 顺序更高阶的能力——逐个检查响应样本，自动找出"状态变化"的规律：

```python
def derive_state_assertions(classified_apis, response_samples) -> dict:
    """
    从 API 响应样本中跨库分析状态字段变化：
    
    方法：
    1. 收集所有涉及"列表查询"的响应样本
    2. 从 entity.list[] 或 entity 中提取所有含有"状态"含义的字段
       → 常见字段: state, status, enabled, locked, phase, stage, runStatus, ...
    3. 对比不同操作后的状态值：
       - 创建后 → state = "ENABLE" 或 "ACTIVE" 或 "NORMAL"
       - 锁定后 → state = "LOCKED" 或 "DISABLE" 或 "FROZEN"
       - 解锁后 → state = "ENABLE"
       - 删除后 → 数据消失
    
    ★ 重点：不从单一响应判断，而是对比"创建后"和"锁定后"的响应差异。
    ★ 兜底：如果找不到明确的状态字段，退化为简单断言：
       HTTP 200 + success=true + errorCode=null
    
    产出示例：
    {
        "state_field": "state",                          # 状态字段名
        "after_create": {"expected": "ENABLE"},          # 创建后期望状态
        "after_lock": {"expected": "DISABLE_LOCKED"},     # 锁定后期望状态
        "after_unlock": {"expected": "ENABLE"},           # 解锁后期望状态
        "after_delete": {"expected": "NOT_EXIST"},        # 删除后数据消失
    }
    """
```

具体的状态字段检测算法：

```python
def find_state_field_in_response(response_body):
    """
    递归扫描响应体，找出所有可能的状态字段：
    
    候选字段名（中英文）:
      state, status, enabled, locked, frozen,
      状态, 启用, 锁定, 激活, 运行状态, 服务状态, 实例状态
    
    对每个候选字段，检查它的值是否属于以下模式：
      - 枚举值: ENABLE/DISABLE, ACTIVE/INACTIVE, 
                LOCKED/UNLOCKED, NORMAL/FROZEN,
                Running/Stopped, 运行中/已停止
      - Boolean: true/false
      - 中文字段: 正常/停用/锁定/已删除
    
    根据此分析：
    - 字段值集合有 2-4 个不同值 → 可能是状态字段 ✅
    - 字段值集合有 10+ 个不同值 → 不是状态字段 ❌（可能是名称/描述）
    - 字段值都是 true/false → 布尔状态 ✅
    - 字段名包含 状态/status/state → 高优先级 ✅
    """
```

### 5.7 整体分析流程图

```python
def analyze(api_map, button_map, response_samples) -> FlowAnalysis:
    """
    整体分析流程：
    
    输入:
      - api_map:           {"POST /draco/v1/users/create": {method, pathname, body, contexts, ...}, ...}
      - button_map:        {"新增用户": {text, category, location, ...}, ...}
      - response_samples:  {"/draco/v1/users/create": [{status, body}, ...], ...}
    
    处理步骤:
      1. filter_supporting_apis()          → 区分核心API和辅助API
      2. map_button_to_crud()              → 按钮名→CRUD类别映射
      3. associate_button_api()            → 建立按钮→核心API关联
      4. derive_execution_order()          → 编排CRUD顺序
      5. derive_dependency_chain()         → 分析数据依赖
      6. derive_state_assertions()         → 推导状态检查断言
    
    输出:
      FlowAnalysis {
          crud_order:        [创建, 查询, 修改, 锁定, 解锁, 删除]
          action_apis:       {create: api_info, list: api_info, ...}
          dependencies:      {修改→{id: "from_create"}, 删除→{id: "from_create"}}
          state_assertions:  {after_create: "ENABLE", after_lock: "LOCKED", ...}
          extra_operations:  [重置密码, 审批, ...]  # 非CRUD的额外操作
      }
    """
```

---

## 6. 阶段四：测试脚本生成

### 6.1 生成的脚本结构

```python
"""
<模块名>_API测试.py — 由 module_discovery 自动生成

执行流程:
  1. 初始化（加载鉴权、创建 API client）
  2. 创建 <资源>        ← 自动生成参数名
  3. 查询 <资源> 列表  ← 验证创建成功
  4. 修改 <资源>        ← 使用创建的 id
  5. 锁定 <资源>        ← 状态变更
  6. 查询 <资源> 列表  ← 验证状态变化
  7. 解锁 <资源>        ← 状态恢复
  8. 查询 <资源> 列表  ← 验证状态恢复
  9. 删除 <资源>        ← 清理数据
  10. 查询 <资源> 列表  ← 验证删除成功

状态断言:
  - 创建后 state 应为 "ENABLE"    （自动推导）
  - 锁定后 state 应为 "LOCKED"    （自动推导）
  - 解锁后 state 应为 "ENABLE"    （自动推导）
  - 删除后数据不应出现            （自动推导）
"""

from lib.auth import AuthSession
from lib.api_client import ApiResponse, get_client
# 也可能直接用 api 层薄函数
from projects.<id>.api.访问控制.用户管理.users import (
    draco_create_user, draco_list_user, draco_update_user,
    draco_lock_user, draco_unlock_user, draco_delete_user,
)

def test_user_lifecycle():
    # 1. 鉴权初始化
    sess = AuthSession(...)
    client = sess.get_client(...)
    if not client:
        raise RuntimeError("无法获取有效鉴权，请检查cookie或登录")
    
    # 2. AUTO: 创建（从探测数据中提取请求体参数）
    create_body = {
        "name": "AT_用户_a1b2c3",
        "userName": "atuser_a1b2c3",
        "email": "at_a1b2c3@test.com",
        "phone": "13812345678",
        "tenantId": "<auto_filled>",   # 从当前用户信息自动获取
    }
    create_resp = draco_create_user(client, **create_body)
    assert create_resp.success, f"创建失败: {create_resp.text}"
    user_id = create_resp.json["entity"]["id"]
    # 状态断言（自动推导）
    assert create_resp.json["entity"]["state"] == "ENABLE", \
        f"创建后状态异常: {create_resp.json['entity'].get('state')}"
    
    # 3. AUTO: 列表查询 — 验证新增的记录存在
    list_resp = draco_list_user(client, pageNum=1, pageSize=20)
    assert list_resp.success
    users = list_resp.json["entity"]["list"]
    assert any(u["id"] == user_id for u in users), "新创建的用户未出现在列表中"
    
    # 4. AUTO: 修改
    update_resp = draco_update_user(client, id=user_id, ...)
    assert update_resp.success
    
    # 5-6. AUTO: 锁定+验证
    lock_resp = draco_lock_user(client, id=user_id)
    assert lock_resp.success
    list_resp = draco_list_user(client, pageNum=1, pageSize=50)
    user = next(u for u in list_resp.json["entity"]["list"] if u["id"] == user_id)
    assert user["state"] == "LOCKED", f"锁定后状态应为 LOCKED，实际为 {user['state']}"
    
    # 7-8. AUTO: 解锁+验证
    unlock_resp = draco_unlock_user(client, id=user_id)
    assert unlock_resp.success
    list_resp = draco_list_user(client, ...)
    user = next(u for u in list_resp.json["entity"]["list"] if u["id"] == user_id)
    assert user["state"] == "ENABLE", f"解锁后状态应为 ENABLE"
    
    # 9-10. AUTO: 删除+验证
    delete_resp = draco_delete_user(client, id=user_id)
    assert delete_resp.success
    list_resp = draco_list_user(client, ...)
    assert not any(u["id"] == user_id for u in list_resp.json["entity"]["list"]), \
        "删除后数据仍存在"
```

### 6.2 脚本中的「自动变量传递」

```python
# 生成代码时会自动注入的变量传递逻辑：
# 
# 创建API的响应               → 提取 id, state
# 列表查询API的响应           → 验证 id 存在, 提取 state
# 修改API的请求体             → 注入 id（来自创建响应）
# 锁定/解锁API的请求体        → 注入 id（来自创建响应）
# 删除API的请求体             → 注入 id（来自创建响应）
# 最后列表查询的响应           → 验证 id 已消失
```

### 6.3 脚本元数据

生成的脚本文件头部包含元数据 JSON，方便后续分析：

```python
# @generated
# @module: 用户管理
# @project: estack
# @timestamp: 2026-08-21T10:00:00
# @endpoints: {method: "POST", path: "/draco/v1/users/create"},
#             {method: "GET",  path: "/draco/v1/users/list"},
#             {method: "POST", path: "/draco/v1/users/update"},
#             {method: "POST", path: "/draco/v1/users/lock"},
#             {method: "POST", path: "/draco/v1/users/unlock"},
#             {method: "POST", path: "/draco/v1/users/delete"}
# @state_assertions: {after_create: "ENABLE", after_lock: "LOCKED", after_unlock: "ENABLE", after_delete: "NOT_EXIST"}
```

---

### 6.4 实战驱动层经验（estack 实测 2026-08-24，探测新模块前必读）

> 本节是「用户管理」模块从「一堆查询接口」对齐到「完整业务生命周期」的真实踩坑沉淀，
> 机器可读版见 `config/probe_lessons_kb.json` v2.0（34 条反模式 + 7 个真实契约 + 分类规则）。
> 探测新模块时：**先读 KB 的 `systems.<x>` 与 `anti_patterns`，再动手。**

**① 行级操作驱动（最容易漏，也是"为什么只有查询"的根因）**

- 算出 `row_btns` 必须真正遍历：创建资源 → 回列表 → 按行 marker 重定位 → 逐一点行操作（编辑/状态/删除），否则只捕获到页面自发查询。
- 操作列在 el-table **fixed-right** 区域，主表行 DOM 内没有按钮 → 三级点击策略：行内 → fixed 同 index 行 → 页面表格区文本兜底（排除 nav）。
- estack 行内「更多」是 `el-dropdown-selfdefine` 指令：**只响应原生 `el.click()`**（dispatchEvent 无效、Playwright click 因 fixed 遮挡 30s 超时）；菜单项限定在行内 `ul.single-operation-menu`。
- 侧边栏也有「更多」（展开模块树、跳页），全局文本匹配会被劫持 → 作用域限定 + 排除 `.el-menu/sidebar`。
- **操作顺序决定能否同时捕获**：授权是整页跳转、删除会删行 → 顺序必须 `编辑→冻结→启用→锁定→解锁→重置密码→授权→(导航回列表)→删除`（授权在前、删除在后，两者才能都拿到）。
- 每次操作前重新定位行，并打印目标行文本（排查多轮操作 id 漂移）。

**② 弹窗/表单交互**

- 创建页 = 整页跳转（`/user-manage/user/create-user`）；编辑 = 弹窗；授权 = 整页跳转（`/authority-manage/add-authority?rowId=<userId>`）。
- 创建提交按钮：`//div[contains(@class,'order-submit')]//button[contains(.,'确 定')]`（文本带空格）。
- 带 countryCode 前缀的手机号 input，Playwright `fill()` 不生效 → JS 原生 `inp.value=` + `dispatchEvent(input/change)` 触发 Vue v-model。
- 弹窗「确定」用 `dlg.filter(has_text)` 可能匹配失败 → 全局最后一个「确定」原生 `el.click()`。
- 下拉「无数据」跳过不卡死；必选下拉取第一项。

**③ 前端加密与浏览器边界**

- estack 的 password/email/phone 走**前端 RSA 加密**（webpack 内封装，无独立公钥接口，纯 Python 逆向不可行）。
- 纯 httpx 重放捕获密文会因「密文对应探测时的固定明文」撞唯一性 → **创建/授权必须浏览器驱动**（`browser_create_user` / `browser_authorize_user`），其余查询/锁定/解锁/重置/删除纯 API。
- 授权真正提交 = `POST /policies/attach-to-user`（`/identity` 只是初始化动作声明）。
- 创建提交有**偶发失败**（POST /users 未发出，前端校验/加密时序竞争）→ 脚本已加「提交后检测 + 重试一次」。

**④ 分类与生成器**

- 分类规则见 KB `classify_rules`：URL 查询/详情优先 → DELETE 先查 unlock → 上下文写类别**仅对 POST/PUT/PATCH**（GET 不算写）→ URL 写关键词 → 校验类降 support → 兜底 other。
- 生成器模板一律「占位符 + 末尾 replace」；f-string 内 `{}` 必须 `{{}}`；脚本 `sys.path` 动态找根；URL 32 位 hex id 段 → `{id}` 占位；`test_data` 名称 `AT_`+TS 唯一化；reset 等需完整 body。
- 运行后日志转 HTML 报告：`python -m lib.report_html --log <report_*.log>`。

---

## 7. 执行入口与用法

### 7.1 单命令入口

```bash
# 针对某个模块做全部四个阶段
python -m module_discovery.run \
    --project ecm-compute \               # projects/<id>/
    --url "/estack/web/estack/user-center/user-manage/user" \
    --module "用户管理" \                  # 产出文件名
    --headless false                       # 是否无头浏览器

# 也可以分阶段执行（调试用）：
python -m module_discovery.run --stage 1 --project ...
python -m module_discovery.run --stage 2 --project ...
python -m module_discovery.run --stage 3 --project ...
python -m module_discovery.run --stage 4 --project ...
```

### 7.2 复用鉴权流程

直接复用 `lib/auth.py` 的 `AuthSession`：

```python
# module_discovery 中：
sess = auth.AuthSession(profile, username, password)
sess.context_path = str(out_dir / "context.json")

# 先尝试 cookie → 探活
client = sess.get_client(base_url)
if client is None:
    # cookie 失效，走滑块登录
    ok = await sess.login_with_browser(page, ctx)
    if not ok: raise RuntimeError("登录失败")
    sess.save_context()
```

---

## 8. 与现有组件的集成关系

| 现有组件 | 复用方式 | 是否修改 |
|----------|----------|----------|
| `lib/auth.py` | Cookie优先+滑块兜底鉴权 | 不修改 |
| `lib/slider.py` | 滑块缺口识别+拖动 | 不修改 |
| `lib/api_client.py` | API调用函数+ApiResponse | 不修改 |
| `lib/resolver.py` | 前置依赖解析（后续扩展） | 不修改 |
| `discovery/form_extractor.py` | 弹窗表单字段抽取 | 不修改 |
| `discovery/playwright_crawl.py` | P0全站巡游发现 | 不修改 |
| `generators/gen_api_layer.py` | 从catalog生成API层函数 | 不修改 |
| `generators/gen_pytest.py` | pytest测试生成（后续参考） | 不修改 |

**module_discovery 是全新模块**，不修改现有任何文件，只利用现有组件的输出和能力。

---

## 附录：关键算法伪代码

### A. 按钮探测全流程

```python
async def discover_buttons_comprehensive(page) -> dict:
    """全流程：选择器扫描 → 可见性过滤 → 位置分类 → 下拉展开 → 表单探测"""
    
    # 第一步：批量选择器扫描
    raw_elements = await page.evaluate(f"""() => {{
        const all = document.querySelectorAll(`{BUTTON_SELECTORS}`);
        return Array.from(all).map(el => {{
            const r = el.getBoundingClientRect();
            return {{
                text: (el.textContent || '').trim(),
                tag: el.tagName,
                className: (el.className || '').slice(0, 80),
                visible: r.width > 0 && r.height > 0,
                x: r.x, y: r.y, w: r.width, h: r.height,
                id: el.id || '',
            }};
        }});
    }}""")
    
    # 第二步：过滤可见
    visible = [e for e in raw_elements if e['visible'] and e['text']]
    
    # 第三步：去重（同文本同位置=相同按钮）
    deduped = {}
    for e in visible:
        key = f"{e['text']}|{e['tag']}"
        if key not in deduped or e['y'] < deduped[key]['y']:
            deduped[key] = e
    unique = list(deduped.values())
    
    # 第四步：位置分类
    # 用 evaluate 判断每个按钮在表格上方还是表格内
    classified = await classify_by_dom_position(page, unique)
    
    return classified
```

### B. 状态字段自动推导

```python
def auto_detect_state_field(all_responses: list) -> dict:
    """
    输入: 所有API的响应体列表
    输出: 状态字段信息和合法值
    
    步骤：
    1. 从所有响应体中找到 entity 或 data
    2. 如果是列表 (entity.list)，检查第一个元素的字段
    3. 找出值只包含 2-4 个不同值的字段 → 候选状态字段
    4. 对每个候选，判断其值集合是否符合"状态"语义
    5. 选择最匹配的作为状态字段
    """
    
    STATE_LIKE_NAMES = {
        'state', 'status', 'enabled', 'locked', 'frozen',
        '状态', '启用状态', '锁定状态', '运行状态',
        'instanceStatus', 'serviceStatus', 'runStatus',
    }
    
    STATE_LIKE_VALUES = {
        'ENABLE', 'DISABLE', 'ACTIVE', 'INACTIVE',
        'LOCKED', 'UNLOCKED', 'NORMAL', 'FROZEN',
        'CREATING', 'DELETING', 'DELETED',
        'Running', 'Stopped', '运行中', '已停止',
        '正常', '停用', '锁定', '已删除',
    }
    
    # 算法实现：
    for response_body in all_responses:
        fields = flatten_json(response_body)
        candidate_field_values = {}
        for field_path, value in fields:
            # 检查字段名是否像状态
            if any(s in field_path.lower() for s in STATE_LIKE_NAMES):
                candidate_field_values.setdefault(field_path, set()).add(value)
    
    # 选择值集合大小在 2-5 之间且值属于 STATE_LIKE_VALUES 的字段
    best = None
    for field, values in candidate_field_values.items():
        if 2 <= len(values) <= 5:
            if any(v in STATE_LIKE_VALUES for v in values):
                if best is None or len(values) < len(best['values']):
                    best = {'field': field, 'values': values}
    
    return best or {'field': None, 'values': set(), 'fallback': 'success_only'}
```

---

## 版本记录

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-08-21 | 初稿，基于 API_AI_test 架构设计模块级发现引擎 |
