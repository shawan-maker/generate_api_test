# Tab/Toast/Pagination 三大问题系统性分析

> 日期：2026-10-15
> 模块：黑白名单（estack/user-center/user-manage/black-list）
> 状态：分析完成，待实施

---

## 一、问题全景

黑白名单页面是典型的 **多 Tab + 各自独立 CRUD** 页面：

```
┌───────────────────────────────────────────┐
│ [黑名单]  [白名单]                         │ ← 2 个 Tab
├───────────────────────────────────────────┤
│ [新增]  [批量操作]                         │ ← 每个 Tab 各自的 toolbar
│ [搜索条件]  [查询]                         │
│ ┌─────────────────────────────────────┐   │
│ │ 表格（各有独立数据）                  │   │
│ │ 行操作：[编辑] [移除] [更多 ▾]        │   │ ← 各 Tab 各自的行操作
│ │           [暂停/启用]                │   │
│ └─────────────────────────────────────┘   │
│ ◀ 1 2 3 ▶                                 │ ← 各 Tab 各自的分页
└───────────────────────────────────────────┘
```

**三个问题的因果关系**：

```
Tab 切换不感知 ──→ 操作在错误 Tab 执行 ──→ 找不到行 ──→ 验证失败
     │                                          ↑
     └──→ 同名按钮被覆盖 ──→ 只保留最后 Tab 的按钮 ─┘
                                    ↑
分页歧义 ──→ querySelector 匹配 2 个分页 ──→ strict mode 报错 ──→ 翻页失败
                                    ↑
Toast 检测失败 ──→ 创建实际成功但标记为失败 ──→ 重试创建 ──→ 创建 2 条数据
```

---

## 二、问题 1：Tab 切换不感知

### 2.1 现状分析

**扫描阶段（element_scanner.py）— 部分感知 Tab**

| 阶段 | 代码位置 | Tab 处理方式 | 问题 |
|------|----------|-------------|------|
| Tab 检测 | `_snapshot_page_structure` L1299 | ✅ 检测所有 Tab，记录 name/locator | 无问题 |
| Tab 迭代扫描 | `discover_all` L103-134 | ✅ 逐一切换 Tab 并扫描 | 无问题 |
| toolbar_buttons 合并 | `_merge_tab_results` L151 | ❌ **同名替换**，只保留最后 Tab | 黑名单的"新增"被白名单的"新增"覆盖 |
| row_actions 合并 | `_merge_tab_results` L163 | ❌ **同名替换** + **初始 Tab 无 tab 标记** | 黑名单的行操作无 tab 字段 |
| validated_operations | `_merge_tab_results` L181 | ⚠️ 有冲突时加前缀 `tab/action`，无冲突保留原名 | 首次出现的操作（黑名单的"新增"）无 tab 前缀 |

**验证结果验证**（黑白名单_ui.json）：

```json
// toolbar_buttons — 只有白名单的按钮（黑名单的被替换掉了）
[{"text": "新增", "tab": "白名单"}, {"text": "批量操作", "tab": "白名单"}]

// row_actions — 只有黑名单的行操作（初始扫描，无 tab 标记）
[{"text": "编辑"}, {"text": "移除"}, {"text": "更多"}]  // ← 无 tab 字段！
```

**CRUD 验证阶段（crud_executor.py）— 完全不感知 Tab**

```python
# _validate_business_flow L92-96
all_buttons = (
    ui_result.get("toolbar_buttons", [])   # ← 拉平所有按钮
    + ui_result.get("row_actions", [])
    + ui_result.get("dropdowns", [])
)
# 按 action 去重，不区分 tab
# 无 tab 切换操作
# 无 tab 字段读取
```

**后果**：
1. 扫描结束后页面停留在**白名单 Tab**（最后扫描的）
2. CRUD 验证直接在白名单 Tab 上执行
3. "新增"按钮 → 白名单的"新增" → 创建了一条白名单记录
4. 然后去找 `AT_test_xxx` 行 → 在黑名单 Tab 的表格里找（因为行操作是黑名单的）→ 找不到
5. 编辑/暂停/移除 全部 `row_not_found`

**Playbook 阶段（builder.py）— CRUD 页面无 Tab 处理**

```python
# builder.py L449-471
if page_type == "read_only":
    operations = _build_read_only_playbook(ui_result, tabs)  # ✅ read_only 有 click_tab
elif page_type == "crud":
    # ❌ CRUD 路径完全不读取 tabs
```

### 2.2 解决方案：Tab-scoped CRUD Pipeline

**核心思路**：将多 Tab CRUD 页面视为"多个独立子模块"，每个 Tab 独立走完整的 CRUD 流程。

#### 方案 A：Per-Tab CRUD 验证（推荐）

**修改点 1**：`_merge_tab_results` — 不再同名替换，改为按 tab 分组

```python
# 修改前：同名替换
existing_list[existing_idx] = btn  # ← 覆盖

# 修改后：追加 + tab 标记
btn["tab"] = tab_name  # 所有元素统一标记 tab
existing_list.append(btn)  # ← 追加不替换
```

**修改点 2**：`_validate_business_flow` — 按 tab 分组验证

```python
# 伪代码
tabs = page_structure.get("tabs", [])
if len(tabs) > 1:
    for tab in tabs:
        # 1. 切换到该 Tab
        await page.click(tab["locator"])
        await page.wait_for_timeout(2000)
        
        # 2. 筛选该 Tab 的按钮
        tab_buttons = [b for b in all_buttons if b.get("tab") == tab["name"]]
        
        # 3. 在该 Tab 上下文中执行完整 CRUD 验证
        tab_result = await _validate_single_tab_crud(tab_buttons, tab)
        
        # 4. 合并结果（加 tab 前缀）
        for action, result in tab_result.items():
            all_results[f"{tab['name']}/{action}"] = result
```

**修改点 3**：Playbook builder — CRUD 页面添加 `click_tab` 前置步骤

```python
# 每个操作前插入 tab 切换步骤
if tab_name:
    steps.insert(0, {
        "action": "click_tab",
        "tab_name": tab_name,
        "playwright_locator": tab_locator
    })
```

**修改点 4**：button_driver.py — 分页和行查找限定到可见 Tab

```python
# 行查找：限定到可见的 tab-pane 内
# 修改前
rows = await self.page.query_selector_all('.el-table__body-wrapper tbody tr')

# 修改后
rows = await self.page.query_selector_all(
    '.el-tab-pane:not([style*="display: none"]) .el-table__body-wrapper tbody tr, '
    '.el-table__body-wrapper tbody tr'  # 无 tab 时回退
)
```

#### 方案 B：最小修改（不推荐，但风险最低）

只在 CRUD 验证前切换回第一个 Tab（active=true 的那个），不做 per-tab 分组：

```python
# 验证前切回初始 Tab
if tabs:
    first_tab = next((t for t in tabs if t.get("active")), tabs[0])
    await page.click(first_tab["locator"])
```

**缺点**：只能验证一个 Tab 的 CRUD，另一个 Tab 完全跳过。

#### 推荐方案 A 的理由

1. 两个 Tab 虽然按钮名称相同，但数据独立，应分别验证
2. 生成的 playbook 应包含两个 Tab 各自的操作
3. Stage 2 回放时需要正确切换 Tab

### 2.3 需要新增的调试日志

在实施前需要确认：

| 日志点 | 位置 | 目的 |
|--------|------|------|
| Tab 切换后的 DOM 状态 | `_validate_business_flow` 入口 | 确认当前在哪个 Tab |
| 每个 Tab 的按钮列表 | `buttons_by_action` 构建后 | 确认按钮是否正确分组 |
| 行查找时的 tab-pane 可见性 | `button_driver.find_data_row` | 确认查找范围 |

---

## 三、问题 2：Toast 检测失败

### 3.1 现状分析

**当前检测链路**：

```
Step 0: captured_messages (MutationObserver 实时捕获)
    ↓ 无匹配
Step 1: DOM 查询 .el-message--success / .el-notification .el-icon-success
    ↓ 无匹配
Step 1.5: 5 秒轮询 .el-message-box / .el-dialog 文本匹配
    ↓ 无匹配
Step 4: 错误状态检测 .el-message--error / .el-form-item__error
    ↓ 无匹配
Step 5: 无信号 → 返回 False ← 这里失败了
```

**检测覆盖的组件类型**：

| 组件 | 选择器 | 覆盖状态 |
|------|--------|---------|
| Element UI `$message`（页面顶部居中） | `.el-message--success` | ✅ |
| Element UI `$notify`（右上角通知） | `.el-notification .el-icon-success` | ⚠️ 只检测 icon |
| Element UI `$alert`（弹窗） | `.el-message-box__wrapper` | ✅ |
| Ant Design `$message` | `.ant-message-success` | ✅ |
| 自定义 toast | 无 | ❌ |

**可能的失败原因**：

1. **通知类型不匹配**：右上角的提示可能是 Element UI `$notify`，但选择器只检测了 `.el-icon-success`，如果该通知没有使用 icon（比如纯文本 "新增成功"），就匹配不到
2. **动画过渡期**：toast 有进出动画（fade-in/fade-out），`offsetWidth > 0` 可能在动画开始瞬间为 true，但代码在 toast 出现之前就检测完毕了
3. **teleport/portal 渲染**：某些 UI 框架将通知渲染到 `<body>` 级别，但 MutationObserver 的 `childList` 监听可能只挂在 `#app` 上
4. **检测时机太晚**：提交表单后，代码先做了其他操作（等待弹窗关闭、检查 loading），toast 在这个间隙出现并消失了

### 3.2 解决方案：调试日志先行

**第一步**：添加 Toast DOM 诊断日志（不改变检测逻辑，只记录）

```python
# 在 _verify_operation_success 的 Step 0 之前添加
toast_diag = await page.evaluate("""() => {
    const result = { messages: [], notifications: [], allToasts: [] };
    
    // 扫描所有可能的 toast/notification 容器
    document.querySelectorAll('.el-message, .el-notification, .el-message-box, [class*="toast"], [class*="notice"]').forEach(el => {
        result.allToasts.push({
            tag: el.tagName,
            className: el.className.substring(0, 200),
            text: el.textContent.trim().substring(0, 100),
            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
            style_display: window.getComputedStyle(el).display,
            style_visibility: window.getComputedStyle(el).visibility,
            style_opacity: window.getComputedStyle(el).opacity,
            rect: el.getBoundingClientRect().toJSON()
        });
    });
    
    // 扫描 MutationObserver 捕获到的消息
    result.capturedCount = (window.__captured_messages || []).length;
    result.capturedMessages = (window.__captured_messages || []).slice(-5);
    
    // 扫描 body 直接子元素中可能的 toast 容器
    for (const child of document.body.children) {
        if (child.id === 'app') continue;
        if (child.className && (child.className.includes('message') || child.className.includes('notification') || child.className.includes('toast'))) {
            result.allToasts.push({
                tag: child.tagName,
                id: child.id,
                className: child.className.substring(0, 200),
                text: child.textContent.trim().substring(0, 100),
                isBodyChild: true
            });
        }
    }
    
    return result;
}""")
LOG.info(f"    [TOAST-DIAG] allToasts={len(toast_diag.get('allToasts', []))} "
         f"captured={toast_diag.get('capturedCount', 0)}")
for t in toast_diag.get('allToasts', []):
    LOG.info(f"    [TOAST-DIAG]   class='{t.get('className', '')[:60]}' "
             f"text='{t.get('text', '')[:40]}' visible={t.get('visible')}")
for m in toast_diag.get('capturedMessages', []):
    LOG.info(f"    [TOAST-DIAG]   captured: type={m.get('type')} text='{m.get('text', '')[:40]}'")
```

**第二步**：根据诊断结果扩展选择器

可能需要的额外选择器（根据诊断结果选择）：

```javascript
// 更宽泛的 $notify 检测
'.el-notification'  // 不再限制必须有 .el-icon-success

// 检测 notification 的文本内容
const notifs = document.querySelectorAll('.el-notification');
for (const n of notifs) {
    const text = n.querySelector('.el-notification__content')?.textContent || '';
    if (/成功/.test(text)) return true;
}

// 检测 body 级别的 toast 容器
const bodyToasts = document.body.querySelectorAll('[class*="message"], [class*="notification"]');
```

**第三步**：增加检测等待时间

```python
# 当前：5 次轮询 × 1s = 5s
# 建议：10 次轮询 × 500ms = 5s（更频繁的检测间隔）
for _poll in range(10):  # 增加轮询次数
    # ... 检测逻辑
    await page.wait_for_timeout(500)  # 缩短间隔
```

### 3.3 优先级

1. **P0**：添加 TOAST-DIAG 诊断日志，确认实际的 DOM 结构
2. **P1**：根据诊断结果扩展选择器
3. **P2**：优化检测间隔

---

## 四、问题 3：分页按钮歧义

### 4.1 现状分析

**错误日志**：
```
locator(".el-pagination .btn-next:not(.disabled)") resolved to 2 elements:
    1) .white-list-manage-container > .table-content > .pagination > ... > .btn-next
    2) .white-list-manage-container > .table-content > .pagination > ... > .btn-next
```

**根因**：`button_driver.py` 中的分页选择器是全局的，不区分 Tab：

```python
# button_driver.py L160
next_btn_sel = self.selectors["pagination"]["next_button"]
# = ".el-pagination .btn-next:not(.disabled)"  ← 匹配到 2 个
next_btn = self.page.locator(next_btn_sel)  # ← strict mode violation
```

**影响范围**：

| 函数 | 选择器 | 问题 |
|------|--------|------|
| `_find_row_with_pagination` L137 | `.el-pagination` | `querySelector` 返回第一个（可能是隐藏 Tab 的） |
| `_find_row_with_pagination` L160 | `.el-pagination .btn-next:not(.disabled)` | 匹配 2 个，strict mode 报错 |
| `_find_row_on_current_page` L98 | `.el-table__body-wrapper tbody tr` | 匹配所有 Tab 的行 |
| `find_first_data_row` L68 | `.el-table__body-wrapper tbody tr` | 同上 |

### 4.2 解决方案：Tab-scoped 选择器

**方案 A（推荐）**：限定到可见 tab-pane

```python
# 辅助函数：获取可见 tab-pane 的范围限定前缀
async def _get_visible_scope(self) -> str:
    """获取当前可见 tab-pane 的 CSS 选择器前缀"""
    scope = await self.page.evaluate("""() => {
        const panes = document.querySelectorAll('.el-tab-pane');
        if (panes.length === 0) return '';  // 无 tab，不限定
        
        for (const pane of panes) {
            const style = window.getComputedStyle(pane);
            if (style.display !== 'none' && style.visibility !== 'hidden') {
                // 尝试用 id 或 nth-child 构建选择器
                if (pane.id) return '#' + pane.id;
                const parent = pane.parentElement;
                const idx = Array.from(parent.children).indexOf(pane);
                return pane.tagName.toLowerCase() + ':nth-child(' + (idx + 1) + ')';
            }
        }
        return '';
    }""")
    return scope

# 使用示例：
# 行查找
visible_scope = await self._get_visible_scope()
selector = f'{visible_scope} .el-table__body-wrapper tbody tr' if visible_scope else '.el-table__body-wrapper tbody tr'
rows = await self.page.query_selector_all(selector)

# 分页
selector = f'{visible_scope} .el-pagination' if visible_scope else '.el-pagination'
```

**方案 B**：Playwright locator 的 `filter` 可见性检查

```python
# 只匹配可见的分页组件
pagination = self.page.locator('.el-pagination').filter(has=self.page.locator(':visible'))
# 或使用 nth(0) 限定第一个可见的
next_btn = self.page.locator('.el-pagination:visible .btn-next:not(.disabled)').first
```

**方案 C**：在 JS 层做可见性过滤

```javascript
// 在 evaluate 中限定到可见 tab-pane
const panes = document.querySelectorAll('.el-tab-pane');
let scope = document;  // 默认全页面
for (const pane of panes) {
    if (window.getComputedStyle(pane).display !== 'none') {
        scope = pane;
        break;
    }
}
// 在 scope 范围内查找
const rows = scope.querySelectorAll('.el-table__body-wrapper tbody tr');
const pagination = scope.querySelector('.el-pagination');
```

### 4.3 推荐实施顺序

1. **Phase 1**：`button_driver.py` 的行查找和分页查找添加 tab-pane 可见性限定（方案 A）
2. **Phase 2**：`_find_row_on_current_page` 和 `find_first_data_row` 同步修改
3. **Phase 3**：将可见 scope 提取为公共方法，统一调用

---

## 五、整体实施路线图

```
Phase 1: 诊断（不改逻辑，只加日志）
  ├── 1a: 添加 TOAST-DIAG 诊断日志
  ├── 1b: 添加 TAB-STATE 诊断日志（CRUD 验证入口记录当前 Tab）
  └── 1c: 运行 Stage 1，收集诊断数据

Phase 2: Toast 修复（基于诊断数据）
  ├── 2a: 扩展 toast 选择器
  ├── 2b: 优化检测间隔
  └── 2c: 验证创建操作能正确检测成功

Phase 3: Tab-scoped 操作（核心改造）
  ├── 3a: _merge_tab_results 不再同名替换
  ├── 3b: 初始 Tab 扫描结果也标记 tab 字段
  ├── 3c: _validate_business_flow 按 Tab 分组验证
  └── 3d: Playbook builder CRUD 页面添加 click_tab 步骤

Phase 4: 分页/行查找限定
  ├── 4a: button_driver 添加 tab-pane 可见性限定
  ├── 4b: 行查找限定到可见 Tab 的表格
  └── 4c: 分页操作限定到可见 Tab 的分页组件

Phase 5: 集成验证
  ├── 5a: 黑白名单模块 Stage 1 全流程验证
  ├── 5b: 用户管理模块回归测试（单 Tab，确认不受影响）
  └── 5c: 单元测试补充
```

---

## 六、风险与约束

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 单 Tab 页面受影响 | 无 Tab 页面添加了不必要的 scope 限定 | 所有限定逻辑在无 Tab 时回退到全页面 |
| per-Tab CRUD 验证时间翻倍 | 2 个 Tab → 验证时间 ×2 | 可配置是否启用 per-tab 验证 |
| Toast 选择器扩展引入误判 | 匹配到非 toast 元素 | 保持"无信号=失败"原则，只扩展成功检测 |
| 同名按钮不再替换 | toolbar_buttons 列表变长 | 下游代码需要正确处理 tab 字段 |
