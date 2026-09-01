# 知识库（KB）体系与 Stage 1/2 数据流

**文档信息**
- 路径: `docs/design/debug/kb-system-overview.md`
- 创建日期: 2026-08-28
- 状态: **已归档**（配合 Phase 1 反馈循环实施参考）

---

## 一、KB 文件清单

项目中存在 **8 个 KB 数据文件** 和 **2 个 KB 处理模块**，分为三个层级：

```
层级 1: XPath 模板库（probe_knowledge.json）
        → 提供"如何定位元素"的模板
        → 支持 element-ui / ant-design 两套框架变体

层级 2: 系统配置库（probe_lessons_kb.json）
        → 提供"这个系统的特殊约定"
        → API 根路径、鉴权方式、字段加密、请求注入补丁

层级 3: 经验模式库（selector_patterns / operation_patterns / debug_strategies）
        → 提供"过往成功/失败的经验"
        → 选择器降级链、操作流程序列、诊断策略
```

### 文件路径

| # | 文件 | 层级 | 状态 |
|---|------|------|------|
| 1 | `module_discovery/kb/probe_knowledge.json` | L1 | ✅ 运行使用 |
| 2 | `docs/design/probe_knowledge.json` | L1 | 📋 设计文档副本（内容相同） |
| 3 | `config/probe_lessons_kb.json` | L2 | ✅ 运行使用 |
| 4 | `config/selector_patterns.json` | L3 | ✅ 运行使用 |
| 5 | `config/operation_patterns.json` | L3 | ✅ 运行使用 |
| 6 | `config/debug_strategies.json` | L3 | ✅ 运行使用 |
| 7 | `projects/estack/nav_kb.json` | 项目级 | ✅ estack 专用 |
| 8 | `config/base_nav_kb.json` | L1 | ❌ **缺失**（被 selector_patterns.json 引用但不存在） |

### KB 处理模块

| 模块 | 职责 |
|------|------|
| `module_discovery/kb_loader.py` | 加载 probe_knowledge.json，展开 XPath 模板占位符（`{label}`, `{chars_all}` 等） |
| `module_discovery/kb_merger.py` | 跨模块经验合并（去重、置信度更新、冲突检测） |

---

## 二、各 KB 文件内容结构

### 2.1 probe_knowledge.json（L1: XPath 模板库）

**核心作用**：定义"如何定位页面上的某类元素"。

**结构**：
```json
{
  "single_step": {
    "categories": {
      "button":          { "patterns": ["//button[contains(.,'{label}')]"] },
      "search-button":   { "patterns": [...] },
      "close-button":    { "patterns": [...] },
      "menu-item":       { "patterns": [...] },
      "input-generic":   { "patterns": [...] },
      "form-checkbox":   { "patterns": [...] }
    }
  },
  "multi_step": {
    "categories": {
      "el-select":  {
        "steps": {
          "expand":     { "patterns": [...] },
          "fill":       { "patterns": [...] },
          "select":     { "patterns": [...] }
        },
        "conditional_branch": { ... }
      },
      "el-cascader": { ... },
      "date-picker": { ... }
    }
  },
  "composite": {
    "categories": {
      "table-action-button": { "patterns": [...] },
      "dropdown-menu":       { "patterns": [...] }
    }
  },
  "fallback_strategies": {
    "strategies": ["playwright_role", "playwright_text", "xpath_class", "dom_text_scan"]
  }
}
```

**关键特性**：
- 每个 category 支持 `framework_variants`，可为 `ant-design` 提供不同的 XPath 模板
- 多步操作（el-select/el-cascader/date-picker）定义了分步骤模板
- 模板使用占位符：`{label}`, `{chars_all}`, `{char1}`, `{char2}`, `{option_text}`, `{field_text}`, `{last_text}`, `{idx}`
- 由 `kb_loader.py` 的 `ProbeKB` 类加载和展开

### 2.2 probe_lessons_kb.json（L2: 系统配置库）

**核心作用**：记录特定系统的技术约定，避免硬编码。

**结构**：
```json
{
  "systems": {
    "estack_draco": {
      "api_root": "/estack/api",
      "auth": { "type": "cookie", "cookie_name": "accessToken" },
      "field_encryption": { "method": "RSA", "fields": ["password"] },
      "ui_table": { "fixed_columns": ["right"], "row_operation_location": "fixed-right" },
      "inject": { "auto_fill": ["tenantId", "adminId"] },
      "permission_gate_keywords": ["您没有", "请先授权", "forbidden"]
    }
  },
  "classify_rules": { "priority": [...] },
  "anti_patterns": [ { "pattern": "...", "severity": "high", "fix": "..." } ],
  "success_playbook": { "steps": [...] },
  "captured_contracts": { "create_user": {...}, "delete_user": {...} }
}
```

**关键内容**：
- `systems.estack_draco.inject`：请求注入补丁配置（自动补 tenantId/adminId）
- `anti_patterns`：30+ 条踩坑记录（Playwright 协程未 await、固定列遮挡等）
- `classify_rules`：7 级优先级端点分类规则
- `captured_contracts`：7 个已验证的 API 契约样本

### 2.3 selector_patterns.json（L3: 选择器经验库）

**核心作用**：记录"哪类元素用什么选择器能找到"，支持降级链。

**结构**：
```json
{
  "patterns": {
    "table_row_selectors": {
      "selectors": ["main", "fixed_left", "fixed_right"],
      "confidence": 0.95,
      "usage_count": 12
    },
    "table_action_button": {
      "fallback_chain": ["fixed_right", "fixed_body_wrapper", "body_wrapper"],
      "xpath_templates": "@base_nav_kb.json#/composite/table-action-button"
    },
    "more_dropdown_trigger": {
      "selectors": [".el-dropdown", "[class*='dropdown']", "text '更多'"],
      "click_method": "native_click"
    },
    "form_fill_patterns": [
      { "match": "名称", "template": "AT_test_{ts}", "type": "text" },
      { "match": "邮箱", "template": "at_{ts}@test.com", "type": "email" }
    ]
  }
}
```

### 2.4 operation_patterns.json（L3: 操作流程库）

**核心作用**：记录"某类操作的完整步骤序列"。

**结构**：
```json
{
  "operations": {
    "create_user_estack": {
      "steps": ["click_toolbar", "fill_form", "select_dropdowns", "submit"],
      "expected_apis": ["POST /users", "GET /users"],
      "confidence": 0.95
    },
    "row_operations_estack": {
      "direct_buttons": ["edit", "lock", "unlock"],
      "dropdown_trigger": "更多",
      "dropdown_items": ["authorize", "reset_password", "delete"],
      "special_handling": {
        "删除": "需确认弹窗",
        "授权": "整页跳转",
        "重置密码": "需填密码表单"
      },
      "operation_order": ["edit", "lock", "unlock", "reset_password", "authorize", "delete"]
    }
  }
}
```

### 2.5 debug_strategies.json（L3: 诊断策略库）

**核心作用**：定义"失败时如何诊断和修复"。

**结构**：
```json
{
  "strategies": {
    "row_not_found": {
      "trigger": { "consecutive_failures": 3 },
      "steps": ["screenshot", "dump_table_structure", "test_selectors", "check_pagination"],
      "auto_fix": true,
      "fix_target": "selector_patterns.json"
    },
    "button_click_failed": {
      "trigger": { "consecutive_failures": 2 },
      "steps": ["screenshot", "dump_row_cells", "check_fixed_right", "test_xpath_templates"],
      "auto_fix": true,
      "fix_target": "selector_patterns.json"
    },
    "stage2_validation_failed": {
      "trigger": { "retry_exhausted": true },
      "steps": ["dump_capture_result", "analyze_missing_apis", "compare_with_operation_patterns"],
      "auto_fix": false
    }
  }
}
```

### 2.6 base_nav_kb.json（缺失）

**问题**：`selector_patterns.json` 中的 `table_action_button.xpath_templates` 引用了 `@base_nav_kb.json#/composite/table-action-button`，但该文件不存在。

**影响**：当前 `button_driver.py` 直接使用 `probe_knowledge.json` 中的 `table-action-button` 模板，`base_nav_kb.json` 的引用未生效。这是一个待修复的依赖问题。

---

## 三、KB 在 Stage 1/2 中的使用时机

### 3.1 Stage 1: 按钮探测（discover_ui.py）

```
discover_all(page, max_retries=2)
│
├─ 步骤 1: CSS 地毯扫描
│   └─ 使用 const.BUTTON_SELECTORS（硬编码，不依赖 KB）
│
├─ 步骤 2: KB 增强扫描 ← probe_knowledge.json
│   └─ kb.get_patterns("button", framework) 获取 XPath 模板
│   └─ 用模板 + ACTION_KEYWORDS 做 XPath 查找，补充 CSS 遗漏
│
├─ 步骤 3: 下拉菜单发现
│   └─ 硬编码触发词 + DOM .el-dropdown 扫描（不依赖 KB）
│
├─ 步骤 4: 创建表单扫描
│   └─ const.ELEMENT_TYPE_MAP 将表单元素映射到 KB category
│      如 "select" → "el-select"，"cascader" → "el-cascader"
│   └─ 输出 form_fields 时带上 kb_category 字段
│      Stage 2 据此调用 kb.get_steps(kb_category) 获取多步模板
│
└─ 步骤 5: 标签构建
    └─ const.ACTION_KEYWORDS 匹配构建 {action: label} 映射
```

**Stage 1 使用的 KB**：
| KB 文件 | 使用时机 | 用途 |
|---------|----------|------|
| `probe_knowledge.json` | 步骤 2 | XPath 模板增强扫描 |
| `const.ACTION_KEYWORDS` | 步骤 2, 5 | 按钮关键词匹配 |
| `const.ELEMENT_TYPE_MAP` | 步骤 4 | 表单元素类型 → KB category 映射 |
| `const.BUTTON_SELECTORS` | 步骤 1 | CSS 选择器集合（硬编码） |

### 3.2 Stage 2: API 捕获（capture_apis.py）

```
capture_apis(page, ui_result, config)
│
├─ 子模块: request_interceptor.py
│   └─ probe_lessons_kb.json → systems.estack_draco.inject
│      在浏览器内注入请求补丁（自动补 tenantId/adminId）
│
├─ 子模块: button_driver.py
│   ├─ selector_patterns.json → table_row_selectors
│   │  find_data_row() 使用三级选择器定位表格行
│   │
│   ├─ probe_knowledge.json → table-action-button
│   │  click_row_button_v2() 使用 fallback_chain 定位行操作按钮
│   │
│   └─ selector_patterns.json → more_dropdown_trigger
│      expand_dropdown_menu() 使用选择器触发下拉菜单
│
├─ 子模块: form_filler.py
│   ├─ probe_knowledge.json → multi_step (el-select/el-cascader/date-picker)
│   │  MultiStepExecutor.execute() 获取多步操作模板
│   │  expand_step() 展开占位符 + 追加隐藏过滤器
│   │
│   ├─ selector_patterns.json → form_fill_patterns
│   │  fill_create_form() 根据字段标签匹配填充模板
│   │
│   └─ kb_loader.py → _append_hidden_filter / apply_overlay_scope
│      Locator 增强：隐藏过滤器 + 覆盖层定位 + iframe 穿透
│
├─ 子模块: endpoint_classifier.py
│   └─ probe_lessons_kb.json → classify_rules
│      7 级优先级端点分类
│
└─ 诊断模式: diagnostic_mode.py
    └─ debug_strategies.json
       失败时加载诊断策略，执行诊断步骤，尝试自动修复
       修复结果通过 kb_merger.py 回写到 selector_patterns.json
```

**Stage 2 使用的 KB**：
| KB 文件 | 使用时机 | 用途 |
|---------|----------|------|
| `probe_knowledge.json` | button_driver / form_filler | XPath 模板（按钮定位 + 多步组件交互） |
| `probe_lessons_kb.json` | request_interceptor / endpoint_classifier | 系统配置（注入补丁 + 分类规则） |
| `selector_patterns.json` | button_driver / form_filler | 选择器降级链 + 表单填充模板 |
| `operation_patterns.json` | capture_apis 主流程 | 操作执行顺序 + 特殊处理 |
| `debug_strategies.json` | diagnostic_mode | 诊断策略（失败时触发） |

---

## 四、KB 处理模块

### 4.1 kb_loader.py（ProbeKB 类）

**职责**：加载 `probe_knowledge.json`，展开 XPath 模板占位符。

**核心方法**：

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `get_patterns(category, framework)` | "button", "element-ui" | XPath 模板列表 | 按 single_step/composite/multi_step 查找 |
| `expand(category, label, framework)` | "button", "确定" | 展开后的 XPath 列表 | 替换 `{label}`, `{chars_all}` 等占位符 |
| `get_steps(category, framework)` | "el-select", "element-ui" | 步骤结构 dict | 多步操作的分步模板 |
| `expand_step(pattern, ...)` | 模板 + 参数 | 展开后的单步 XPath | 支持所有占位符 + `apply_hidden=True` 追加隐藏过滤器 |
| `get_conditional_branch(category, framework)` | "el-cascader" | 条件分支配置 | 级联选择器最后一级判断逻辑 |
| `get_fallback_strategies()` | — | 兜底策略列表 | KB 查找失败时的降级方案 |
| `detect_framework_sync(html)` | HTML 字符串 | "element-ui" / "ant-design" | 通过 DOM 类名检测 UI 框架 |

**工具函数**（模块级）：

| 函数 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `_append_hidden_filter(xpath, filter)` | XPath + 过滤条件 | 追加过滤器后的 XPath | 处理三种 XPath 模式 |
| `apply_overlay_scope(xpath, prefix)` | XPath + 覆盖层前缀 | 追加前缀后的 XPath | 弹窗/抽屉内定位 |
| `detect_active_overlay_js(framework)` | 框架名 | JS 脚本字符串 | 检测当前活跃的覆盖层 |
| `get_overlay_prefix(overlay_type, framework)` | 覆盖层类型 | XPath 前缀 | 获取覆盖层的 XPath 前缀 |

### 4.2 kb_merger.py（KBMerger 类）

**职责**：跨模块经验合并，将新发现的模式写入经验库。

**核心方法**：

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `merge_selector_pattern(key, new_pattern)` | 模式 key + 新选择器 | 合并后的模式 | 去重、更新置信度和使用次数 |
| `merge_operation_pattern(key, new_op)` | 操作 key + 新操作 | 合并后的操作 | 累加成功/失败计数，更新置信度 |
| `detect_conflicts(key, new_selectors)` | 模式 key + 新选择器 | 冲突列表 | 同名不同选择器 / 同选择器不同名 |
| `cleanup_deprecated_patterns(days, min_usage, min_conf)` | 阈值参数 | 清理的模式列表 | 清理过期和低质量模式 |

---

## 五、KB 数据流全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                        KB 数据流全景图                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐                                           │
│  │ probe_knowledge  │◄──── kb_loader.py (ProbeKB)              │
│  │   .json (L1)     │         │                                 │
│  └──────────────────┘         │ get_patterns() / expand()      │
│                               │ get_steps() / expand_step()    │
│                               ▼                                 │
│  ┌──────────────────┐   ┌─────────────┐   ┌──────────────────┐ │
│  │ probe_lessons_kb │   │  Stage 1    │   │   Stage 2        │ │
│  │   .json (L2)     │──▶│  discover   │──▶│   capture        │ │
│  │                  │   │  _ui.py     │   │   _apis.py       │ │
│  └──────────────────┘   └─────────────┘   └──────────────────┘ │
│         │                       │                  │            │
│         │ inject config         │ kb_category      │            │
│         │ classify_rules        │ (from const)     │            │
│         ▼                       ▼                  ▼            │
│  ┌──────────────────┐   ┌─────────────┐   ┌──────────────────┐ │
│  │ request          │   │ form_filler │   │ button_driver    │ │
│  │ _interceptor.py  │   │ .py         │   │ .py              │ │
│  └──────────────────┘   └─────────────┘   └──────────────────┘ │
│                                                                 │
│  ┌──────────────────┐   ┌─────────────┐   ┌──────────────────┐ │
│  │ selector         │──▶│  button     │   │  endpoint        │ │
│  │ _patterns (L3)   │   │  _driver.py │   │  _classifier.py  │ │
│  └──────────────────┘   └─────────────┘   └──────────────────┘ │
│         ▲                       │                  │            │
│         │                       │ 失败时           │            │
│         │                       ▼                  │            │
│         │              ┌─────────────────┐         │            │
│         │              │ diagnostic_mode │         │            │
│         │              │ .py             │         │            │
│         │              └─────────────────┘         │            │
│         │                       │                  │            │
│         │              ┌─────────────────┐         │            │
│         └──────────────│ kb_merger.py    │◄────────┘            │
│                        │ (回写经验库)     │                      │
│                        └─────────────────┘                      │
│                                                                 │
│  ┌──────────────────┐                                           │
│  │ operation        │◄──── capture_apis.py 主流程               │
│  │ _patterns (L3)   │      读取操作顺序和特殊处理               │
│  └──────────────────┘                                           │
│                                                                 │
│  ┌──────────────────┐                                           │
│  │ debug_strategies │◄──── diagnostic_mode.py                   │
│  │ .json (L3)       │      失败时加载诊断策略                   │
│  └──────────────────┘                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 六、KB 体系当前问题

### 6.1 已知问题

| # | 问题 | 影响 | 严重度 |
|---|------|------|--------|
| 1 | `base_nav_kb.json` 缺失 | `selector_patterns.json` 中的引用无效，`button_driver.py` 直接使用 `probe_knowledge.json` 绕过 | 中 |
| 2 | `probe_knowledge.json` 存在两份 | `module_discovery/kb/` 和 `docs/design/` 各一份，需保持同步 | 低 |
| 3 | `selector_patterns.json` 与 `probe_knowledge.json` 职责重叠 | `table_action_button` 在两个文件中都有定义，优先级不清 | 中 |
| 4 | `debug_strategies.json` 未参与反馈循环 | 诊断策略只读不写，修复结果未回写 | 高 |
| 5 | `operation_patterns.json` 未动态更新 | 只有 estack 的硬编码操作，新模块的操作序列未写入 | 中 |

### 6.2 与反馈循环（Phase 1）的关系

Phase 1 的反馈循环需要在以下位置与 KB 体系集成：

| 反馈循环步骤 | 涉及的 KB | 操作 |
|-------------|----------|------|
| Stage 2 L1 错误归因 | `debug_strategies.json` | 读取诊断策略 |
| 执行诊断步骤 | `probe_knowledge.json` | 使用 XPath 模板重新探测 |
| 修复验证成功 | `selector_patterns.json` | 通过 `kb_merger.py` 回写新选择器 |
| 修复验证成功 | `debug_strategies.json` | 更新策略的 `success_count` 和 `last_used` |
| AI 辅助分析成功 | `debug_strategies.json` | 写入新策略条目 |
| Stage 2 L2 错误 | `selector_patterns.json` | 更新选择器降级链 |

---

## 七、总结

### KB 体系的三个层级

| 层级 | 文件 | 职责 | 更新频率 |
|------|------|------|----------|
| L1: XPath 模板 | `probe_knowledge.json` | 定义"如何定位元素" | 低频（框架级模板稳定） |
| L2: 系统配置 | `probe_lessons_kb.json` | 定义"这个系统的特殊约定" | 中频（每接入新系统更新一次） |
| L3: 经验模式 | `selector/operation/debug` | 记录"过往成功/失败的经验" | 高频（每次探测后可能更新） |

### KB 在 Stage 1/2 中的角色

- **Stage 1**：主要使用 L1（XPath 模板增强扫描），少量使用 L3（表单元素类型映射）
- **Stage 2**：全面使用 L1/L2/L3（按钮定位、系统配置、选择器降级、操作顺序、诊断策略）
- **反馈循环**（待实现）：Stage 2 失败时读取 L3 诊断策略，修复后回写 L3 经验库

### 待修复

1. `base_nav_kb.json` 缺失 → 要么创建，要么清理 `selector_patterns.json` 中的引用
2. `debug_strategies.json` 需要支持动态写入（当前只读）
3. `probe_knowledge.json` 和 `selector_patterns.json` 的职责边界需要明确
