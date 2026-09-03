# Stage 1 to Stage 2 Playbook Fix (V3 - KB-Driven Replay, Corrected)

> Date: 2026-09-02 (Corrected)
> Status: Pending
> Priority: P0

## 1. Core Principle

**Stage 1**: Explore UI, validate operations, record WHAT to do.
**Stage 2**: Replay operations from playbook + intercept APIs. HOW is provided by KB.
**Playbook only records WHAT, not HOW.**

### 1.1 KB-Driven Replay (Key Design Change)

**Key Finding**: Stage 2's `MultiStepExecutor` already executes multi-step operations (el-select/el-cascader/date-picker/form-checkbox) autonomously. It only needs 3 inputs: `kb_category` + `label` + `selector`. Details like `is_editable` and `option_text` are determined at runtime by KB strategies. The values Stage 1 records for these fields are **never read by Stage 2**.

**Design Principle**: Both stages call KB's standardized multi-step strategies. Playbook only passes minimum required info:

| Operation | Playbook Only Records | Runtime Determined by KB |
|-----------|----------------------|------------------------|
| **el-select** | `kb_category`, `label`, `selector` | is_editable, option_text, fill-vs-first-option |
| **el-cascader** | `kb_category`, `label` | full path, checkbox-vs-text |
| **date-picker** | `kb_category`, `label` | today/now/month |
| **form-checkbox** | `kb_category`, `label`, `option_text` | N/A |
| **dropdown-menu** | `item_text` (+ marker at runtime) | trigger method (hover/click/Vue), button location |
| **button click** | `text` | 4-level fallback (playwright/force/js/coord) |
| **confirm dialog** | No playbook record needed | Auto-detect at runtime (MessageBox/Popconfirm/Generic) |

### 1.2 Implications

- **Remove** `is_editable`, `option_text` from playbook multi-step fields (vestigial, never consumed)
- **Replace** hardcoded `hover_dropdown` + `click_dropdown_item` steps with `click_row_more_item(row, item_text)` call
- **Replace** hardcoded confirm steps with runtime `confirm_dialog(page)` call
- **Simplify** playbook schema: each operation = sequence of "WHAT" actions, not "HOW" instructions

---

## 2. Current Gap Analysis

### 2.1 Overview

| Operation | What Stage 1 Did | What It Recorded | Gap |
|-----------|-----------------|------------------|-----|
| **create** | Click trigger, scan form, fill fields, multi-step, JS-submit | trigger_locator, submit_locator, form_fields, multi_step_field_details | Warning: submit_locator text lacks spaces |
| **update/edit** | Find row, click edit, scan form, fill edits, JS-submit | selectors.trigger, selectors.submit (text only) | Missing ALL enhanced fields, no fill_form step |
| **delete** | Check checkbox, find row, click delete, confirm dialog | selectors.trigger, selectors.confirm (nested in selectors) | Confirm gate bug + missing enhanced fields |
| **lock/unlock/reset** | Find row, row-level dropdown (hover+JS+Vue 3-tier), click item | dropdown_parent_selector=".el-dropdown" (global) | Global selector matches wrong element |
| **authorize** | Find row, click button, page navigation, navigate back | selectors.trigger, navigation_occurred | Missing navigate_back step |
| **import/migrate** | 4-level fallback toolbar button click | selectors.trigger (text only) | Missing trigger_locator |

### 2.2 Detailed Gap Analysis

#### Gap 1: Chinese Whitespace Issue

`submit_form_v2()` uses JS `.replace(/\s+/g, '')` to match buttons like "que ding" (with spaces), but returns the de-spaced text "queding". Stage 2's `button:has-text('queding')` cannot match the actual button text "que ding".

**Fix**: `submit_form_v2` should return original text (with spaces).

#### Gap 2: hover_dropdown Row-Level Positioning

Playbook records `parent_selector: ".el-dropdown"` (global). `page.query_selector(".el-dropdown")` returns the nav-bar dropdown (not visible).

**Stage 1 actually did**: `find_data_row(marker)` -> `click_row_more_item(row, btn_text)` -- row-level + KB 3-tier expansion.

**Fix**: Stage 2 should call `click_row_more_item(row, item_text)` directly. Playbook only needs `item_text`.

#### Gap 3: update Has No fill_form Step

`_do_edit()` returns missing `form_fields`, `fill_rules`, etc. `_build_update_steps()` skips the entire fill_form step.

#### Gap 4: Confirm Gate Bug (Cross-Cutting)

All `_build_*_steps` check `op_data.get("confirm")` but Stage 1 stores confirm inside `selectors["confirm"]` (nested). Result: confirm steps are **never generated** in any playbook.

#### Gap 5: _do_delete Missing Enhanced Fields

No `trigger_locator_verified`, `confirm_dialog_type`, `confirm_locator`, `checkbox_locator`.

#### Gap 6: Toolbar Buttons Missing trigger_locator

`_click_button_escalating` returns only `bool`, not the successful strategy.

#### Gap 7: authorize Missing navigate_back

Stage 1 auto-navigates back after page jump, but playbook has no `navigate_back` step.

#### Gap 8: _step_click_button JS Fallback Only Searches button Tag

Stage 1 searches `button, span, a, .el-button`. Stage 2 JS fallback only searches `button`.

---

## 3. Fix Plan

### 3.1 Modification List

| # | File | Function | Change | Gap |
|---|------|----------|--------|-----|
| 1 | `discover_ui.py` | `_do_edit` | Add form_fields, fill_rules, submit_locator_verified, interaction_mode, dialog_locator, success_locator | 3 |
| 2 | `discover_ui.py` | `_do_generic_operation` | Add trigger_locator_verified for toolbar, `confirmed` top-level field, navigate_back_url | 4,6,7 |
| 3 | `discover_ui.py` | `_do_delete` | Add trigger_locator_verified, confirmed, confirm_dialog_type, confirm_locator, checkbox_locator | 4,5 |
| 4 | `discover_ui.py` | `_click_button_escalating` | Return dict with strategy/tag/text instead of bool | 6 |
| 5 | `discover_ui.py` | `_build_update_steps` | Align with `_build_create_steps` | 3 |
| 6 | `discover_ui.py` | `_build_dropdown_steps` | Use `click_row_more` step; confirm gate: only generate when `confirmed=True` | 2,4 |
| 7 | `discover_ui.py` | `_build_delete_steps` | Use enhanced fields; confirm gate: only generate when `confirmed=True` | 4,5 |
| 8 | `discover_ui.py` | `_build_generic_steps` | Add navigate_back; confirm gate: only generate when `confirmed=True` | 4,7 |
| 9 | `capture_apis.py` | `_step_click_row_more` (new) | Call `button_driver.click_row_more_item(row, item_text)` | 2 |
| 10 | `capture_apis.py` | `_step_confirm_dialog` (simplified) | Call `button_driver.confirm_dialog(page)` instead of hardcoded locator | 4 |
| 11 | `capture_apis.py` | `_step_click_button` | **删除** JS fallback，直接用 Stage 1 精确 locator | 8 |
| 12 | `capture_apis.py` | `_step_navigate_back` (new) | Navigate back to URL | 7 |
| 13 | `capture_apis.py` | `_step_hover_dropdown` | Remove (replaced by `_step_click_row_more`) | 2 |
| 14 | `capture_apis.py` | `_step_click_dropdown_item` | Remove (replaced by `_step_click_row_more`) | 2 |
| 15 | `form_filler.py` | `submit_form_v2` | Return original text (with spaces) | 1 |

### 3.2 Detailed Design

#### 3.2.1 Simplified Playbook Step Types

**Before** (current playbook for lock operation):
```json
{
  "steps": [
    {"action": "find_row"},
    {"action": "hover_dropdown", "parent_selector": ".el-dropdown", "trigger_method": "hover"},
    {"action": "click_dropdown_item", "playwright_locator": ".el-dropdown-menu__item:has-text('freeze')"},
    {"action": "click_confirm_dialog", "confirm_locator": ".el-message-box__btns button:has-text('OK')"},
    {"action": "assert_success", "playwright_locator": ".el-message--success"}
  ]
}
```

**After** (KB-driven, simplified):
```json
{
  "steps": [
    {"action": "find_row"},
    {"action": "click_row_more", "item_text": "freeze"},
    {"action": "confirm_dialog"},
    {"action": "assert_success"}
  ]
}
```

**Key differences**:
- `hover_dropdown` + `click_dropdown_item` -> `click_row_more` (single step, KB-driven)
- `click_confirm_dialog` with hardcoded locator -> `confirm_dialog` (auto-detect, KB-driven)
- No `parent_selector`, `trigger_method`, `confirm_locator` needed

#### 3.2.2 New `_step_click_row_more` (Gap 2)

```python
async def _step_click_row_more(page, step, button_driver, marker):
    """Expand row dropdown and click item. Delegates to button_driver.click_row_more_item."""
    item_text = step.get("item_text")
    if not item_text:
        raise Exception("click_row_more step missing item_text")
    if not marker:
        raise Exception("click_row_more needs marker but none available")
    
    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"Row not found: {marker}")
    
    clicked = await button_driver.click_row_more_item(row, item_text)
    if not clicked:
        raise Exception(f"Failed to click dropdown item: {item_text}")
```

#### 3.2.3 Simplified `_step_confirm_dialog` (Gap 4)

```python
async def _step_confirm_dialog(page, step):
    """Auto-detect and click confirm dialog. Delegates to button_driver.confirm_dialog."""
    from .button_driver import confirm_dialog
    confirmed = await confirm_dialog(page)
    if not confirmed:
        LOG.warning("Confirm dialog step but no confirm button found")
```

This replaces the old approach where the playbook had to specify the exact confirm_locator. Now `confirm_dialog()` auto-detects MessageBox/Popconfirm/Generic at runtime.

#### 3.2.4 `_do_edit` Enhancement (Gap 3)

Add to `_do_edit` return value (similar to `_do_create`):
```python
return {
    "success": True,
    "selectors": {...},
    "edit_fill_data": edit_fill_data,
    # New enhanced fields:
    "trigger_locator_verified": trigger_locator,
    "submit_locator_verified": submit_locator,
    "submit_text": submit_text_normalized,
    "interaction_mode": "dialog" if dialog_detected else "page-nav",
    "dialog_locator": ".el-dialog__wrapper",
    "success_locator": ".el-message--success",
    "form_fields": fields,
    "fill_rules": {},
    "multi_step_field_details": [],
    "radio_field_details": [],
}
```

#### 3.2.5 `_do_generic_operation` Enhancement (Gap 4, 6, 7)

1. Toolbar buttons: record `trigger_locator_verified`
2. Confirm: add top-level `"confirmed": confirmed`
3. Navigation: add `"navigation_occurred": True, "navigate_back_url": url_before`

#### 3.2.6 `_do_delete` Enhancement (Gap 4, 5)

Add: `trigger_locator_verified`, `confirmed`, `confirm_dialog_type`, `confirm_locator`, `checkbox_locator`, `success_locator`.

#### 3.2.7 `_click_button_escalating` Return Dict (Gap 6)

Change from returning `bool` to returning `dict`:
```python
return {"clicked": True, "strategy": "js", "tag": "button"}
```

All callers must be updated: `_do_generic_operation`, `_do_delete`, `_do_detail`, `_do_query`.

#### 3.2.8 Confirm Gate Fix in All Builders (Gap 4)

```python
# Old (broken):
if op_data.get("confirm"):

# New: Stage 1 已记录 confirmed=True/False，只在确认弹出时才生成步骤
confirmed = op_data.get("confirmed") or op_data.get("selectors", {}).get("confirm")
if confirmed:
```

Stage 1 在探测时已经明确知道操作是否弹出了确认框（`confirmed` 字段）。只在 `confirmed=True` 时生成 `confirm_dialog` 步骤，Stage 2 直接执行，不做"可能出现"的猜测。**不生成无意义的 confirm 步骤让 Stage 2 去探测**。

#### 3.2.9 `_step_navigate_back` (Gap 7)

```python
async def _step_navigate_back(page, step):
    url = step.get("url")
    if not url:
        return
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await wait_for_table_ready(page, timeout=15000)
```

#### 3.2.10 `_step_click_button` JS Fallback 删除 (Gap 8)

**删除** Stage 2 中的 JS fallback 逻辑。按原则，Stage 2 不应做任何探测。Stage 1 的 `_click_button_escalating` 已返回精确的 `{tag, text, strategy}`，`build_playbook` 据此生成精确 `playwright_locator`，Stage 2 直接点击即可。

```python
# Old Stage 2 code (to be deleted):
clicked = await page.evaluate("""(targetText) => {
    const buttons = Array.from(document.querySelectorAll('button'));
    ...
}""", text)

# New: 直接使用 Stage 1 的精确 locator，失败即报错
await page.click(locator, timeout=5000)
```

#### 3.2.11 `submit_form_v2` Return Original Text (Gap 1)

Return `{normalized: "queding", original: "que ding"}` from JS, use original for locator.

---

## 4. Impact Analysis

### 4.1 `_click_button_escalating` Return Type Change

| Caller | Current Usage | Change |
|--------|-------------|--------|
| `_do_generic_operation` toolbar branch | `clicked = await ...` | `result = await ...; clicked = result.get("clicked")` |
| `_do_delete` | Same | Same |
| `_do_detail` | Same | Same |
| `_do_query` | Same | Same |

### 4.2 Removed Step Types

- `hover_dropdown` -> replaced by `click_row_more`
- `click_dropdown_item` -> replaced by `click_row_more`
- `click_confirm_dialog` (with locator) -> replaced by `confirm_dialog` (auto-detect)

### 4.3 New Step Types

- `click_row_more`: `{action: "click_row_more", item_text: "freeze"}`
- `confirm_dialog`: `{action: "confirm_dialog"}`
- `navigate_back`: `{action: "navigate_back", url: "..."}`

---

## 5. Implementation Order

### Phase 1: Stage 1 Output Enhancement
1. `_click_button_escalating` return dict
2. Update all callers
3. `_do_edit` add enhanced fields
4. `_do_generic_operation` add trigger_locator_verified, confirmed, navigate_back_url
5. `_do_delete` add enhanced fields
6. `submit_form_v2` return original text

### Phase 2: build_playbook Enhancement
7. `_build_update_steps` align with `_build_create_steps`
8. `_build_dropdown_steps` use `click_row_more` step + fix confirm gate
9. `_build_delete_steps` use enhanced fields + fix confirm gate
10. `_build_generic_steps` add navigate_back + fix confirm gate

### Phase 3: Stage 2 Executor Adaptation
11. New `_step_click_row_more` (calls `button_driver.click_row_more_item`)
12. Simplified `_step_confirm_dialog` (calls `button_driver.confirm_dialog`)
13. New `_step_navigate_back`
14. `_step_click_button` 删除 JS fallback，直接用 Stage 1 精确 locator
15. Remove `_step_hover_dropdown` and `_step_click_dropdown_item`
16. `replay_from_playbook` pass marker to `click_row_more`

### Phase 4: Verification
17. Re-run Stage 1 to generate new playbook
18. Check playbook JSON structure
19. Run Stage 2 replay verification
20. Run full E2E pipeline
21. Run unit tests (no regression)

---

## 6. Expected Results

### Before (Stage 2 log)
```
> replay create
    filled 8/9 fields
    step 4 failed: button:has-text('OK') timeout
> replay update
    find_row needs marker, none available
> replay lock
    ElementHandle.hover: .el-dropdown not visible (30s timeout)
> replay import
    button:has-text('batch import') timeout
```

### After (expected Stage 2 log)
```
> replay create
    click button: create user
    filled 9/9 fields
    click submit
    create success, marker: autotest330256
> replay update
    find row: autotest330256
    click edit button
    fill edit form: 3 fields modified
    click submit
> replay lock
    find row: autotest330256
    click row more: freeze
    confirm dialog: OK
> replay import
    click button: batch import
    detected file upload dialog
```

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_click_button_escalating` return type change breaks callers | High | Update all callers in Phase 1 |
| Old playbooks incompatible with new Stage 2 | Low | Stage 2 detects missing fields and prompts re-run Stage 1 |
| `confirm_dialog()` auto-detect may have false positives | Medium | Keep `_check_precondition_state` gate before calling |
| `submit_form_v2` original text affects other callers | Low | Check all callers, only used for locator construction |

---

## 8. Key Files

| File | Change Type | Description |
|------|-------------|-------------|
| `module_discovery/discover_ui.py` | Modify | `_do_edit`, `_do_generic_operation`, `_do_delete`, `_click_button_escalating`, `_build_*_steps` |
| `module_discovery/capture_apis.py` | Modify | New `click_row_more`, simplified `confirm_dialog`, `navigate_back`; remove `hover_dropdown`, `click_dropdown_item` |
| `module_discovery/form_filler.py` | Modify | `submit_form_v2` return original text |
| `module_discovery/button_driver.py` | No change | Already provides `click_row_more_item` and `confirm_dialog` |
