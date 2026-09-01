# Stage 1-2 反馈循环与质量门禁重构方案

**文档信息**
- 路径: `docs/design/debug/stage1-2-feedback-loop.md`
- 创建日期: 2026-08-28
- 状态: **方案设计中**

---

## 一、问题分析

### 1.1 当前架构缺陷

**现状**：Stage 1 和 Stage 2 是单向流水线，没有反馈机制。

```
Stage 1 (discover_all)
  ↓ validate_stage1() - 粗粒度检查（按钮数≥3, has_create/delete）
  ↓ 失败 → 等2秒重试（最多2次）
  ↓ 仍失败 → warning，继续往下

Stage 2 (capture_apis)
  ↓ 接收 ui_result，尝试操作
  ↓ click_row_button_v2 找不到按钮 → 报错
  ↓ 连续失败2次 → 触发 diagnostic_mode
  ↓ diagnostic_mode 修复 Stage 2 的选择器（selector_patterns.json）
  ↓ 重试 Stage 2 的点击（不重新运行 Stage 1）
```

**核心问题**：

1. **Stage 1 质量门禁太粗**
   - 只检查"有没有创建按钮"、"按钮数 ≥ 3"
   - 不检查"确定按钮是否存在"、"表单必填字段是否扫描到"
   - 验证失败不阻断，继续执行 Stage 2

2. **Stage 2 失败归因不清**
   - 点击失败时，无法区分是 Stage 1 没探测到（L1）还是 Stage 2 选择器错误（L2）还是时序问题（L3）
   - `diagnostic_mode` 只能修复 Stage 2 的选择器，不能修复 Stage 1 的探测遗漏

3. **没有反馈循环**
   - Stage 2 发现元素缺失时，无法反馈给 Stage 1 重新探测
   - 每次失败都从头排查，经验无法积累

4. **经验库未被充分利用**
   - `debug_strategies.json`、`selector_patterns.json`、`operation_patterns.json` 存在但未参与反馈循环
   - 修复方案无法积累复用

### 1.2 失败场景分类

| 层级 | 失败表现 | 根因 | 例子 |
|------|----------|------|------|
| **L1: 数据缺失** | ui_result 中根本没有目标元素 | Stage 1 没探测到 | 点击"确定"按钮，但 toolbar_buttons 里没有"确定" |
| **L2: 定位失败** | ui_result 有记录，但页面上找不到 | 选择器/iframe/覆盖层问题 | ui_result 记录了"确定"，但 `click_row_button_v2` 点不到 |
| **L3: 时序失败** | 元素存在但点击时机不对 | 等待不充分/动画未完成 | 弹窗还在弹出动画中就点击了 |

---

## 二、解决方案架构

### 2.1 整体设计

**核心思路**：建立 Stage 1 ↔ Stage 2 的反馈循环，通过经验库积累修复方案。

```
┌─────────────────────────────────────────────────┐
│                 反馈循环                           │
│                                                   │
│   Stage 1 ──→ Stage 2 ──→ 失败归因 ──→ 诊断策略  │
│     ↑                              ↓              │
│     └──────── 经验库 ←── 修复验证 ←┘              │
│                  ↓                                 │
│           下次优先查库                             │
└─────────────────────────────────────────────────┘
```

### 2.2 关键改进点

1. **Stage 1 验证细化**：定义 `REQUIRED_ELEMENTS`，检查必须元素是否存在
2. **Stage 2 错误分类**：区分 L1/L2/L3 三层错误
3. **反馈机制**：Stage 2 失败时反馈给 Stage 1，携带 hints 重新探测
4. **经验库集成**：诊断策略写入 `debug_strategies.json`，选择器修复写入 `selector_patterns.json`
5. **AI 辅助兜底**：最后手段使用 AI 分析，成功后固化到经验库

---

## 三、详细实施方案

### 3.1 定义必须元素（新增 `module_discovery/required_elements.py`）

```python
"""
required_elements.py — 定义各操作流程的必须元素

Stage 1 验证时检查这些元素是否存在于 ui_result 中。
"""

REQUIRED_ELEMENTS = {
    "create_flow": [
        {
            "type": "button",
            "action": "create",
            "desc": "创建/新增按钮",
            "location": ["toolbar", "row_action"],
            "critical": True,  # 缺失则阻断
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确定/提交按钮（表单提交）",
            "location": ["dialog", "toolbar"],
            "critical": True,
        },
        {
            "type": "input",
            "required": True,
            "desc": "必填输入项（至少1个）",
            "min_count": 1,
            "critical": False,  # 缺失但可降级（使用默认值）
        },
    ],
    
    "delete_flow": [
        {
            "type": "button",
            "action": "delete",
            "desc": "删除按钮",
            "location": ["row_action", "toolbar"],
            "critical": True,
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确认删除按钮",
            "location": ["dialog", "toolbar"],
            "critical": True,
        },
    ],
    
    "update_flow": [
        {
            "type": "button",
            "action": "update",
            "desc": "编辑按钮",
            "location": ["row_action"],
            "critical": True,
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确定按钮",
            "location": ["dialog"],
            "critical": True,
        },
    ],
    
    "query_flow": [
        {
            "type": "button",
            "action": "query",
            "desc": "查询/搜索按钮",
            "location": ["toolbar"],
            "critical": False,  # 可降级（直接调 API）
        },
    ],
}


def get_required_elements(flow_name: str) -> list:
    """获取指定流程的必须元素列表。
    
    Args:
        flow_name: 流程名称（create_flow/delete_flow/update_flow/query_flow）
    
    Returns:
        必须元素列表
    """
    return REQUIRED_ELEMENTS.get(flow_name, [])


def check_required_elements(ui_result: dict, flow_name: str) -> tuple:
    """检查 ui_result 是否包含必须元素。
    
    Args:
        ui_result: Stage 1 输出
        flow_name: 流程名称
    
    Returns:
        (all_present, missing_elements)
        - all_present: 是否全部存在
        - missing_elements: 缺失的元素列表（含 critical 标记）
    """
    required = get_required_elements(flow_name)
    missing = []
    
    for req in required:
        found = False
        
        if req["type"] == "button":
            # 检查按钮
            locations = req.get("location", [])
            for loc in locations:
                buttons = ui_result.get(f"{loc}_buttons", []) + ui_result.get("dropdowns", [])
                for btn in buttons:
                    action = btn.get("action") or _infer_action(btn.get("text", ""))
                    if action == req["action"]:
                        found = True
                        break
                if found:
                    break
        
        elif req["type"] == "input":
            # 检查输入字段
            fields = ui_result.get("form_fields", [])
            min_count = req.get("min_count", 1)
            if req.get("required"):
                matching = [f for f in fields if f.get("required")]
                found = len(matching) >= min_count
            else:
                found = len(fields) >= min_count
        
        if not found:
            missing.append(req)
    
    all_present = len(missing) == 0
    return all_present, missing


def _infer_action(button_text: str) -> str:
    """从按钮文本推断 action。"""
    from . import const
    for action, keywords in const.ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in button_text:
                return action
    return "unknown"
```

### 3.2 重构 `validate_stage1`（修改 `stage_validators.py`）

```python
def validate_stage1(ui_result: dict, required_flows: list = None) -> tuple:
    """验证 Stage 1 UI 探测结果质量。
    
    Args:
        ui_result: Stage 1 输出
        required_flows: 需要验证的流程列表（如 ["create_flow", "delete_flow"]）
                       默认验证所有 CRUD 流程
    
    Returns:
        (is_valid, issues, missing_elements)
    """
    issues = []
    
    # 原有检查（保留）
    # ... 按钮总数、has_create/delete 等 ...
    
    # 新增：检查必须元素
    if required_flows is None:
        required_flows = ["create_flow", "delete_flow", "update_flow", "query_flow"]
    
    all_missing = []
    for flow in required_flows:
        present, missing = check_required_elements(ui_result, flow)
        if not present:
            all_missing.extend(missing)
            for m in missing:
                if m.get("critical"):
                    issues.append(f"[{flow}] 缺少必须元素: {m['desc']} (critical)")
                else:
                    issues.append(f"[{flow}] 缺少元素: {m['desc']} (可降级)")
    
    # 判断是否通过（只检查 critical 元素）
    critical_missing = [m for m in all_missing if m.get("critical")]
    is_valid = len(critical_missing) == 0 and len(issues) == 0
    
    return is_valid, issues, all_missing
```

### 3.3 Stage 2 错误分类（新增异常类）

```python
"""
module_discovery/stage2_errors.py — Stage 2 错误分类
"""

class Stage2Error(Exception):
    """Stage 2 操作失败基类"""
    pass

class Stage1MissingError(Stage2Error):
    """L1: Stage 1 没探测到必须元素"""
    def __init__(self, element_type: str, action: str, context: str, expected_location: list = None):
        self.element_type = element_type  # "button" / "input"
        self.action = action              # "confirm" / "create"
        self.context = context            # "提交创建表单"
        self.expected_location = expected_location or []
        super().__init__(f"Stage 1 未探测到: {element_type}[{action}] in {context}")

class Stage2LocatorError(Stage2Error):
    """L2: Stage 2 选择器/定位失败"""
    def __init__(self, element_desc: str, selector: str, tried_iframes: bool = False):
        self.element_desc = element_desc
        self.selector = selector
        self.tried_iframes = tried_iframes
        super().__init__(f"定位失败: {element_desc} (selector={selector})")

class Stage2TimingError(Stage2Error):
    """L3: Stage 2 时序失败"""
    def __init__(self, element_desc: str, wait_strategy: str):
        self.element_desc = element_desc
        self.wait_strategy = wait_strategy
        super().__init__(f"时序失败: {element_desc} (wait={wait_strategy})")
```

### 3.4 Stage 2 操作前检查（修改 `capture_apis.py`）

```python
async def _click_button_with_check(page, ui_result, action: str, context: str, kb, framework):
    """点击按钮前先检查 ui_result 是否有记录。
    
    Raises:
        Stage1MissingError: ui_result 中没有该按钮
        Stage2LocatorError: 按钮存在但点击失败
    """
    # 检查 ui_result
    found = False
    for loc in ["toolbar_buttons", "row_actions", "dialog_buttons", "dropdowns"]:
        buttons = ui_result.get(loc, [])
        for btn in buttons:
            btn_action = btn.get("action") or _infer_action(btn.get("text", ""))
            if btn_action == action:
                found = True
                break
        if found:
            break
    
    if not found:
        # L1: Stage 1 没探测到
        raise Stage1MissingError(
            element_type="button",
            action=action,
            context=context,
            expected_location=["toolbar", "dialog"]
        )
    
    # 尝试点击
    clicked = await click_row_button_v2(page, action=action, kb=kb, framework=framework)
    if not clicked:
        # L2: 定位失败
        raise Stage2LocatorError(
            element_desc=f"按钮[{action}]",
            selector=f"action={action}",
            tried_iframes=True
        )
    
    return clicked
```

### 3.5 反馈循环机制（修改 `capture_apis.py` 主流程）

```python
async def capture_apis(page, ui_result, config, max_stage2_retries: int = 3):
    """API 捕获主流程，支持反馈循环。
    
    流程：
    1. 尝试执行操作
    2. 失败时分类错误（L1/L2/L3）
    3. L1: 反馈给 Stage 1，携带 hints 重新探测
    4. L2/L3: 触发 diagnostic_mode 修复
    5. 重试操作（最多 max_stage2_retries 次）
    6. 修复方案写入经验库
    """
    from .discover_ui import discover_all
    from .required_elements import get_required_elements
    
    current_ui_result = ui_result
    
    for attempt in range(max_stage2_retries):
        try:
            result = await _capture_once(page, current_ui_result, config)
            
            # 成功 → 写入经验库（如果之前有修复）
            if attempt > 0:
                await _save_fix_to_experience_library(config.last_fix)
            
            return result
        
        except Stage1MissingError as e:
            # L1: Stage 1 遗漏 → 反馈给 Stage 1
            
            # 1. 查经验库匹配策略
            strategy = await _match_debug_strategy(e)
            
            if strategy:
                # 2. 按策略诊断
                fix_result = await _execute_debug_strategy(page, strategy, e)
                
                if fix_result["found"]:
                    # 3. 找到元素 → 更新 ui_result → 写入经验库
                    current_ui_result = await _patch_ui_result(current_ui_result, fix_result)
                    await _update_experience_library(strategy, fix_result)
                    continue  # 重试 Stage 2
                else:
                    # 4. 策略失败 → AI 辅助分析
                    ai_result = await _ai_assisted_analysis(page, e)
                    if ai_result["found"]:
                        current_ui_result = await _patch_ui_result(current_ui_result, ai_result)
                        await _save_ai_fix_to_experience_library(e, ai_result)
                        continue
                    else:
                        LOG.error(f"AI 辅助也未能找到: {e}")
                        raise
            else:
                # 没有匹配策略 → 直接 AI 辅助
                ai_result = await _ai_assisted_analysis(page, e)
                if ai_result["found"]:
                    current_ui_result = await _patch_ui_result(current_ui_result, ai_result)
                    await _save_ai_fix_to_experience_library(e, ai_result)
                    continue
                else:
                    raise
        
        except Stage2LocatorError as e:
            # L2: 定位失败 → diagnostic_mode 修复选择器
            fix_result = await diagnostic_mode.run(page, strategy="button_click_failed", error=e)
            if fix_result["fixed"]:
                await _update_selector_patterns(fix_result)
                continue  # 重试 Stage 2
            else:
                raise
        
        except Stage2TimingError as e:
            # L3: 时序失败 → 增加等待
            await page.wait_for_timeout(2000)  # 临时方案
            # TODO: 根据 e.wait_strategy 选择更智能的等待
            continue
    
    # 重试耗尽
    raise Stage2Error(f"Stage 2 重试 {max_stage2_retries} 次后仍失败")
```

### 3.6 经验库集成（修改 `debug_strategies.json` 结构）

```json
{
  "version": "2.0",
  "strategies": [
    {
      "id": "STG1-001",
      "name": "确定按钮遗漏-弹窗内",
      "trigger": {
        "stage": 2,
        "error_type": "L1",
        "element_type": "button",
        "element_action": "confirm",
        "context_pattern": "提交.*表单"
      },
      "steps": [
        {
          "action": "check_dialog",
          "desc": "检查是否有弹窗/抽屉未展开",
          "js": "document.querySelectorAll('.el-dialog, .el-drawer').length"
        },
        {
          "action": "expand_xpath_search",
          "desc": "在弹窗容器内搜索确定按钮",
          "xpath": "//div[contains(@class,'el-dialog')]//button[contains(.,'确')]"
        },
        {
          "action": "iframe_scan",
          "desc": "遍历 iframe 查找",
          "scope": "all_frames"
        }
      ],
      "outcome": {
        "fix_target": "stage1",
        "patch_location": "dialog_buttons"
      },
      "metadata": {
        "accumulated_from": ["角色管理", "2026-08-28"],
        "success_count": 3,
        "last_used": "2026-08-28",
        "deprecated": false
      }
    }
  ]
}
```

### 3.7 AI 辅助分析（新增 `ai_debug_assistant.py`）

```python
"""
ai_debug_assistant.py — AI 辅助调试（最后手段）

当经验库无匹配策略时，使用 AI 分析失败原因并生成修复方案。
"""

async def _ai_assisted_analysis(page, error: Stage1MissingError) -> dict:
    """AI 辅助分析缺失元素。
    
    流程：
    1. 截图 + DOM dump → 发送给 AI
    2. AI 输出：候选选择器列表 + 置信度
    3. 验证：逐个尝试候选选择器
    4. 成功 → 返回修复方案
    5. 失败 → 返回空结果
    
    Returns:
        {
            "found": bool,
            "element": {...},  # 找到的元素
            "selector": str,   # 成功的定位方式
            "confidence": float
        }
    """
    # 1. 截图 + DOM dump
    screenshot = await page.screenshot()
    dom_dump = await page.evaluate("""() => {
        const buttons = document.querySelectorAll('button, a, span[onclick]');
        return Array.from(buttons).map(b => ({
            tag: b.tagName,
            text: b.textContent.trim(),
            class: b.className,
            visible: b.offsetWidth > 0
        })).filter(b => b.text);
    }""")
    
    # 2. 构造 AI prompt
    prompt = f"""
    我需要找到页面上的 {error.element_type}[{error.action}]，用于 {error.context}。
    但 Stage 1 探测失败，没有在 ui_result 中找到这个元素。
    
    当前页面 DOM 中的按钮列表：
    {json.dumps(dom_dump, indent=2, ensure_ascii=False)}
    
    请分析：
    1. 哪个元素最可能是目标？（根据文本、class、上下文）
    2. 给出 3 个候选选择器（XPath 优先），按置信度排序
    3. 说明为什么 Stage 1 可能漏掉了这个元素
    
    输出格式：
    ```json
    {{
        "candidates": [
            {{"selector": "...", "confidence": 0.9, "reason": "..."}},
            ...
        ],
        "miss_reason": "..."
    }}
    ```
    """
    
    # 3. 调用 AI（需要集成 LLM API）
    ai_response = await _call_llm(prompt, screenshot)
    
    # 4. 验证候选选择器
    candidates = ai_response.get("candidates", [])
    for candidate in candidates:
        selector = candidate["selector"]
        
        # 尝试定位
        element = await _try_selector(page, selector)
        if element:
            return {
                "found": True,
                "element": element,
                "selector": selector,
                "confidence": candidate["confidence"],
                "miss_reason": ai_response.get("miss_reason")
            }
    
    return {"found": False}


async def _save_ai_fix_to_experience_library(error: Stage1MissingError, ai_result: dict):
    """将 AI 修复方案写入经验库。"""
    strategy = {
        "id": f"AI-{int(time.time())}",
        "name": f"AI修复-{error.action}-{error.context}",
        "trigger": {
            "stage": 2,
            "error_type": "L1",
            "element_type": error.element_type,
            "element_action": error.action,
            "context_pattern": error.context
        },
        "steps": [
            {
                "action": "use_selector",
                "desc": "使用 AI 发现的选择器",
                "selector": ai_result["selector"]
            }
        ],
        "outcome": {
            "fix_target": "stage1",
            "patch_location": "toolbar_buttons"  # 或根据 error.expected_location 推断
        },
        "metadata": {
            "accumulated_from": ["AI", datetime.now().isoformat()],
            "success_count": 1,
            "last_used": datetime.now().isoformat(),
            "deprecated": False,
            "source": "ai_assisted"
        }
    }
    
    # 写入 debug_strategies.json
    await _append_to_debug_strategies(strategy)
```

---

## 四、验证方案

### 4.1 单元测试

```python
# tests/test_required_elements.py

def test_check_required_elements_all_present():
    """测试所有必须元素都存在"""
    ui_result = {
        "toolbar_buttons": [{"text": "创建", "action": "create"}],
        "dialog_buttons": [{"text": "确定", "action": "confirm"}],
        "form_fields": [{"label": "名称", "required": True}]
    }
    
    present, missing = check_required_elements(ui_result, "create_flow")
    assert present is True
    assert len(missing) == 0


def test_check_required_elements_critical_missing():
    """测试缺少 critical 元素"""
    ui_result = {
        "toolbar_buttons": [{"text": "创建", "action": "create"}],
        "dialog_buttons": [],  # 缺少确定按钮
        "form_fields": [{"label": "名称", "required": True}]
    }
    
    present, missing = check_required_elements(ui_result, "create_flow")
    assert present is False
    assert len(missing) == 1
    assert missing[0]["action"] == "confirm"
    assert missing[0]["critical"] is True


def test_check_required_elements_non_critical_missing():
    """测试缺少 non-critical 元素"""
    ui_result = {
        "toolbar_buttons": [{"text": "创建", "action": "create"}],
        "dialog_buttons": [{"text": "确定", "action": "confirm"}],
        "form_fields": []  # 缺少必填字段（non-critical）
    }
    
    present, missing = check_required_elements(ui_result, "create_flow")
    assert present is True  # non-critical 不影响
    assert len(missing) == 1
    assert missing[0]["critical"] is False
```

### 4.2 集成测试

```python
# tests/test_stage1_stage2_feedback.py

async def test_stage1_missing_error_feedback():
    """测试 Stage 1 遗漏时的反馈循环"""
    # 模拟 Stage 1 输出（缺少确定按钮）
    ui_result = {
        "toolbar_buttons": [{"text": "创建", "action": "create"}],
        "dialog_buttons": [],  # 缺少
        "form_fields": [{"label": "名称", "required": True}]
    }
    
    # 模拟 Stage 2 操作
    with pytest.raises(Stage1MissingError) as exc_info:
        await _click_button_with_check(page, ui_result, "confirm", "提交表单", kb, framework)
    
    assert exc_info.value.action == "confirm"
    assert exc_info.value.context == "提交表单"


async def test_feedback_loop_with_strategy():
    """测试反馈循环匹配到策略并修复"""
    # 准备经验库策略
    strategy = {
        "id": "TEST-001",
        "trigger": {
            "error_type": "L1",
            "element_action": "confirm"
        },
        "steps": [...]
    }
    await _save_debug_strategy(strategy)
    
    # 模拟 Stage 2 失败
    ui_result = {...}  # 缺少确定按钮
    
    # 执行 capture_apis（应该触发反馈循环）
    # 验证：
    # 1. 匹配到策略
    # 2. 执行策略找到元素
    # 3. 更新 ui_result
    # 4. 重试成功
    result = await capture_apis(page, ui_result, config, max_stage2_retries=2)
    assert result is not None
```

### 4.3 端到端测试

```bash
# 使用真实的 ecm-compute 项目测试

# 1. 运行 Stage 1-2（角色管理）
python -m module_discovery.run --project ecm-compute \
    --module 角色管理 --stage 12

# 2. 检查日志输出：
#    - 是否有 "Stage 1 验证失败" 警告
#    - 是否有 "Stage 1 遗漏" 错误
#    - 是否有 "反馈循环" 日志
#    - 是否有 "经验库更新" 日志

# 3. 检查经验库文件：
#    - debug_strategies.json 是否新增条目
#    - selector_patterns.json 是否更新

# 4. 运行生成的脚本验证
python projects/ecm-compute/flows/v1.0.0/角色管理_API测试.py
```

---

## 五、Gap 分析

### 5.1 遗漏检查

| 检查项 | 是否覆盖 | 说明 |
|--------|----------|------|
| Stage 1 遗漏必须元素 | ✅ | `REQUIRED_ELEMENTS` + `check_required_elements` |
| Stage 2 选择器失败 | ✅ | `diagnostic_mode` 已有 |
| Stage 2 时序失败 | ✅ | `Stage2TimingError` + 增加等待 |
| 反馈循环 | ✅ | `capture_apis` 捕获 L1 错误并反馈 |
| 经验库积累 | ✅ | `_save_fix_to_experience_library` |
| AI 辅助兜底 | ✅ | `_ai_assisted_analysis` |
| iframe/覆盖层 | ✅ | Stage 2 已有（`click_row_button_v2`） |
| 多步组件（el-select/cascader） | ⚠️ 部分 | 当前方案只覆盖按钮，多步组件的"选项文本"缺失未处理 |

### 5.2 未覆盖场景

**场景 1：多步组件选项文本缺失**

```
Stage 1 探测到 el-select 字段（"状态"），但没记录选项文本。
Stage 2 执行 _execute_select 时，option_text=""，只能选第一项。
这不是 Stage 1 遗漏，而是 Stage 1 没采集足够信息。
```

**解决方案**：在 `REQUIRED_ELEMENTS` 中增加对多步组件的检查：

```python
"create_flow": [
    # ... 按钮检查 ...
    {
        "type": "multi_step_field",
        "kb_category": ["el-select", "el-cascader"],
        "desc": "多步组件字段",
        "require_options": False,  # 不强制要求选项文本（Stage 2 会自动发现）
        "critical": False,
    },
]
```

**场景 2：表单字段 required 标记错误**

```
某些系统的表单字段没有 .is-required class，但实际是必填的。
Stage 1 探测为 required=False，Stage 2 不填值，提交失败。
```

**解决方案**：在 `diagnostic_mode` 中增加"表单提交失败"策略，分析响应错误信息，推断缺失的必填字段。

---

## 六、风险评估

### 6.1 是否会引入新问题？

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| **反馈循环死循环** | 中 | 高 | `max_stage2_retries=3` 限制重试次数 |
| **经验库策略过时** | 中 | 中 | 定期标记 `deprecated`，优先使用 `success_count` 高的策略 |
| **AI 辅助成本高** | 低 | 中 | AI 作为最后手段，优先查经验库 |
| **REQUIRED_ELEMENTS 过严** | 中 | 高 | `critical` 标记区分，non-critical 缺失不阻断 |
| **Stage 2 重试耗时** | 低 | 中 | 日志记录重试次数，监控性能 |

### 6.2 是否影响正常逻辑？

| 场景 | 影响 | 说明 |
|------|------|------|
| Stage 1 探测完整 | ✅ 无影响 | `validate_stage1` 通过，直接进入 Stage 2 |
| Stage 2 一次成功 | ✅ 无影响 | 不触发反馈循环 |
| Stage 1 遗漏但 Stage 2 不需要 | ✅ 无影响 | `REQUIRED_ELEMENTS` 只检查 critical 元素 |
| Stage 2 失败但无匹配策略 | ⚠️ 降级 | 走 AI 辅助，或抛出异常（与当前行为一致） |

### 6.3 相互影响分析

| 问题 A | 问题 B | 影响 | 说明 |
|--------|--------|------|------|
| REQUIRED_ELEMENTS 过严 | 反馈循环频繁触发 | 负面 | 缓解：`critical` 标记 + non-critical 不阻断 |
| 经验库策略过时 | AI 辅助频繁调用 | 负面 | 缓解：定期清理 deprecated 策略 |
| Stage 1 重试次数增加 | 总耗时增加 | 负面 | 缓解：`max_stage1_retries=2` 不变，只在 Stage 2 反馈时重试 |
| AI 修复写入经验库 | 策略数量膨胀 | 负面 | 缓解：AI 修复标记 `source: ai_assisted`，定期人工审核 |

---

## 七、实施计划

### 7.1 阶段划分

**Phase 1：基础设施（2-3 天）**
- [ ] 新增 `required_elements.py`
- [ ] 新增 `stage2_errors.py`
- [ ] 修改 `stage_validators.py`（`validate_stage1` 增加 `required_flows` 参数）
- [ ] 更新 `debug_strategies.json` 结构

**Phase 2：反馈循环（3-4 天）**
- [ ] 修改 `capture_apis.py`（错误分类 + 反馈机制）
- [ ] 新增 `_match_debug_strategy`（匹配经验库策略）
- [ ] 新增 `_execute_debug_strategy`（执行策略步骤）
- [ ] 新增 `_patch_ui_result`（更新 ui_result）

**Phase 3：AI 辅助（2-3 天）**
- [ ] 新增 `ai_debug_assistant.py`
- [ ] 集成 LLM API（OpenAI / Claude）
- [ ] 实现 `_ai_assisted_analysis`
- [ ] 实现 `_save_ai_fix_to_experience_library`

**Phase 4：测试与验证（2 天）**
- [ ] 单元测试
- [ ] 集成测试
- [ ] 端到端测试（ecm-compute）
- [ ] 性能监控

### 7.2 优先级

| 优先级 | 任务 | 原因 |
|--------|------|------|
| P0 | `required_elements.py` + `stage2_errors.py` | 基础设施，必须先行 |
| P0 | 修改 `validate_stage1` | 核心改进 |
| P1 | 反馈循环（Phase 2） | 核心价值 |
| P2 | AI 辅助（Phase 3） | 兜底方案，可后补 |

---

## 八、总结

### 8.1 解决的问题

1. ✅ Stage 1 质量门禁细化（检查必须元素）
2. ✅ Stage 2 错误归因清晰（L1/L2/L3 分类）
3. ✅ 反馈循环建立（Stage 2 → Stage 1）
4. ✅ 经验库积累复用（修复方案写入 `debug_strategies.json`）
5. ✅ AI 辅助兜底（最后手段）

### 8.2 未完全解决的问题

1. ⚠️ 多步组件选项文本缺失（部分覆盖）
2. ⚠️ 表单字段 required 标记错误（需要 diagnostic_mode 增强）

### 8.3 下一步方向

1. 实施 Phase 1-2，验证核心机制
2. 收集真实项目的失败案例，丰富经验库
3. 监控反馈循环触发频率，调整 `REQUIRED_ELEMENTS` 的 `critical` 标记
4. 考虑引入机器学习，自动从历史数据中学习 `REQUIRED_ELEMENTS`

---

**附录：相关文件清单**

- 新增：`module_discovery/required_elements.py`
- 新增：`module_discovery/stage2_errors.py`
- 新增：`module_discovery/ai_debug_assistant.py`
- 修改：`module_discovery/stage_validators.py`
- 修改：`module_discovery/capture_apis.py`
- 修改：`config/debug_strategies.json`（结构升级）
- 新增：`tests/test_required_elements.py`
- 新增：`tests/test_stage1_stage2_feedback.py`
