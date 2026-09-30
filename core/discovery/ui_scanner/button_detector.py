"""
button_detector.py — Button classification & utilities.

Extracted from discover_ui.py. Contains functions for dialog management,
button location classification, category counting, button label building,
precondition state checking, precondition retry logic, table data prewarming,
action role inference, and create/delete action detection.
"""

import json
import logging
import time
from core.discovery import const
from core.discovery.kb_loader import ProbeKB
from core.discovery.replay.wait_helpers import (
    wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog,
    wait_for_loading_complete, wait_for_table_ready,
)
from core.discovery.replay.form_filler import (
    FormFiller, read_form_errors, generate_fill_data, scan_form_fields,
    generate_fill_rules,
)

LOG = logging.getLogger("button_detector")

# 全局知识库实例
_kb: ProbeKB = None


async def _prewarm_table_data(page, form_fields: list, toolbar_buttons: list,
                               framework: str = "element-ui") -> dict:
    """数据预热：在空表格时创建临时数据，使行操作按钮渲染。

    触发条件：row_actions 为空 AND 有创建按钮 AND 表格行数为 0

    流程：
    1. 找到创建按钮（toolbar_buttons 中 create_like 类型的按钮）
    2. 点击创建按钮 → 打开弹窗
    3. 如果 form_fields 为空，自己扫描表单字段
    4. 生成 fill_rules + fill_data
    5. 填充表单
    6. 提交表单
    7. 等待表格刷新（rows > 0）
    8. 重新扫描行操作按钮
    9. 不执行清理（数据留给后续 Stage 2 使用）

    Args:
        page: Playwright Page 对象
        form_fields: Stage 4 已扫描的表单字段（可能为空）
        toolbar_buttons: 工具栏按钮列表
        framework: UI 框架类型

    Returns:
        {"row_actions": [...], "dropdowns": [...], "form_fields": [...],
         "created_marker": str|None, "success": bool}
    """
    # Lazy imports for cross-module dependencies
    from core.discovery.operation_executor.base_executor import _click_button_escalating
    from core.discovery.ui_scanner.element_scanner import (
        _get_kb, _scan_candidates, _discover_dropdowns, _snapshot_page_structure,
    )

    result = {"row_actions": [], "dropdowns": [], "form_fields": [],
              "created_marker": None, "success": False}

    try:
        # Step 1: 找到创建按钮
        create_btn = None
        for btn in toolbar_buttons:
            if _classify_button_location(btn) == "create_like":
                create_btn = btn
                break

        if not create_btn:
            LOG.warning("    预热: 未找到创建按钮，跳过数据预热")
            return result

        btn_text = create_btn.get("text", "")
        LOG.info(f"    预热: 点击创建按钮 '{btn_text}'")

        # Step 2: 点击创建按钮
        click_result = await _click_button_escalating(page, btn_text)
        if not click_result.get("success"):
            LOG.warning(f"    预热: 点击创建按钮失败")
            return result

        await page.wait_for_timeout(1000)
        from core.discovery.replay.wait_helpers import wait_for_loading_complete
        await wait_for_loading_complete(page)

        # Step 2.5: 如果 form_fields 为空，自己扫描
        if not form_fields:
            LOG.info("    预热: form_fields 为空，扫描表单字段...")
            form_fields = await scan_form_fields_v2(page)
            LOG.info(f"    预热: 扫描到 {len(form_fields)} 个表单字段")

        result["form_fields"] = form_fields

        if not form_fields:
            LOG.warning("    预热: 未扫描到表单字段，跳过")
            await _close_dialog(page)
            return result

        # Step 3: 生成填充数据
        fill_rules = generate_fill_rules(form_fields)
        fill_data = generate_fill_data(form_fields, username="AT_prewarm")
        LOG.info(f"    预热: 生成 {len(fill_data)} 个字段填充数据")

        # Step 4: 填充表单
        filler = form_filler.FormFiller(page)
        fill_result = await filler.fill_create_form(form_fields, "AT_prewarm", fill_data)
        # fill_result 现在是 dict: {"filled": int, "cascade_actions": list}
        filled_count = fill_result["filled"] if isinstance(fill_result, dict) else fill_result
        if filled_count == 0 and form_fields:
            LOG.warning(f"    预热: 表单填充失败: 0 个字段被填充")
            await _close_dialog(page)
            return result

        # Step 5: 提交表单
        submit_result = await filler.submit_form_v2()
        if not submit_result:
            LOG.warning("    预热: 表单提交失败")
            await _close_dialog(page)
            return result
        LOG.info(f"    预热: 表单已提交 (策略: {submit_result.get('click_strategy', '')})")

        # Step 6: 等待表格刷新
        await page.wait_for_timeout(2000)
        try:
            await wait_for_table_ready(page, timeout=10000)
        except Exception:
            await page.wait_for_timeout(3000)

        # Step 7: 检查数据行
        page_structure = await _snapshot_page_structure(page)
        row_count = page_structure.get("mainBodyRows", 0)
        LOG.info(f"    预热: 表格行数 = {row_count}")

        if row_count > 0:
            # Step 8: 重新扫描行操作
            new_candidates = await _scan_candidates(page)
            new_row_actions = [c for c in new_candidates if c.get("location") == "row_action"]
            result["row_actions"] = new_row_actions
            result["success"] = True
            # 使用模糊匹配查找名称类字段作为 marker
            _marker_keywords = ["名称", "用户名", "name", "username"]
            _prewarm_marker = None
            for label, value in fill_data.items():
                if any(kw in label.lower() for kw in _marker_keywords):
                    _prewarm_marker = value
                    break
            result["created_marker"] = _prewarm_marker or "AT_prewarm"
            LOG.info(f"    预热成功: 发现 {len(new_row_actions)} 个行操作按钮")

            # 重新扫描下拉菜单（行操作中可能有"更多"按钮）
            if new_row_actions:
                kb = _get_kb()
                new_dropdowns = await _discover_dropdowns(
                    page, toolbar_buttons + new_row_actions, kb, framework
                )
                result["dropdowns"] = new_dropdowns
                if new_dropdowns:
                    LOG.info(f"    预热: 发现 {len(new_dropdowns)} 个下拉子项")
        else:
            LOG.warning("    预热: 创建后表格仍无数据行")

        return result

    except Exception as e:
        LOG.warning(f"    预热异常: {e}")
        await _close_dialog(page)
        return result


async def _close_dialog(page):
    """关闭弹窗（尝试点击关闭按钮和按 ESC）。

    如果页面已导航回（非弹窗模式），跳过此步骤。
    """
    # 检查是否有可见的对话框
    has_dialog = await page.evaluate("""() => {
        const wrappers = document.querySelectorAll('.el-dialog__wrapper, .ant-modal-wrap, .el-drawer');
        return Array.from(wrappers).some(w => w.style.display !== 'none' && w.offsetWidth > 0);
    }""")
    if not has_dialog:
        return  # 非弹窗模式，已在 _scan_create_dialog 中导航回

    try:
        await page.evaluate("""() => {
            const btns = document.querySelectorAll('.el-dialog__headerbtn, .el-drawer__close-btn, .ant-modal-close, [class*="close"]');
            for (const b of btns) { if (b.offsetWidth > 0) { b.click(); return; } }
        }""")
        await page.wait_for_timeout(500)
        await page.keyboard.press("Escape")
    except Exception as e:
        LOG.debug(f"关闭弹窗失败: {e}")


def _classify_button_location(btn: dict) -> str:
    """基于按钮 DOM 位置推断其行为类型（结构性，不依赖文本）。

    用于 Stage 1 内部逻辑（如决定是否扫描创建弹窗），不改变按钮的 action 字段。
    按钮的 action 字段始终使用按钮文本原文。

    Returns:
        "create_like" / "query_like" / "navigation" / "business"
    """
    location = btn.get("location", "toolbar")
    tag = btn.get("tag", "")

    # 导航/菜单类 → 不是业务操作
    if location == "menu":
        return "navigation"

    # 工具栏按钮（页面顶部）→ 可能是 create 或 query
    if location == "toolbar":
        # 有 icon 特征或 class 包含 search/query → query_like
        cls = (btn.get("className") or "").lower()
        if "search" in cls or "query" in cls or "filter" in cls:
            return "query_like"
        # 其他工具栏按钮 → 可能是 create 或 generic
        return "create_like"

    # 行操作按钮 → 业务操作
    if location == "row_action":
        return "business"

    # 下拉菜单项 → 业务操作
    if location in ("dropdown", "dropdown_item"):
        return "business"

    return "business"


def _count_categories(result: dict) -> dict:
    """统计各位置按钮数量（使用按钮文本作为类别标识）。"""
    counts = {}
    for key in ("toolbar_buttons", "row_actions", "dropdowns"):
        for btn in result.get(key, []):
            # 用按钮文本作为类别标识（不再做文本→CRUD 映射）
            cat = btn.get("text", "unknown")
            counts[cat] = counts.get(cat, 0) + 1
    return counts


def _build_button_labels(result: dict) -> dict:
    """从已探测到的按钮构建 action -> label 映射。

    按钮文本即操作名，直接构建 {text: text} 映射。

    Args:
        result: 探测结果字典，包含 toolbar_buttons, row_actions, dropdowns 等

    Returns:
        dict: {action: label} 映射
    """
    labels = {}

    # 收集所有按钮
    all_buttons = []
    all_buttons.extend(result.get("toolbar_buttons", []))
    all_buttons.extend(result.get("row_actions", []))
    all_buttons.extend(result.get("dropdowns", []))

    # 遍历按钮，直接使用按钮文本作为 action 和 label
    for btn in all_buttons:
        text = btn.get("text", "")
        if not text:
            continue

        # action 就是按钮文本，label 也是按钮文本
        action = btn.get("action", text)
        labels[action] = text

    return labels


async def _check_precondition_state(page, expected_state: dict) -> dict:
    """检查前置操作是否成功（纯 DOM，零成本）。

    检查预期的页面状态（弹窗/抽屉/页面变化）是否已出现。

    Args:
        page: Playwright Page 对象
        expected_state: 预期状态，如 {"type": "dialog", "title": "添加用户"}

    Returns:
        {"success": bool, "actual_state": str}
    """
    try:
        state = await page.evaluate("""() => {
            // 检查弹窗/抽屉（增强过滤：加 height 检查 + 内部容器检查）
            const dialogs = document.querySelectorAll(
                '.el-dialog__wrapper, .el-drawer, .ant-modal-wrap');
            const visible = Array.from(dialogs).filter(d => {
                if (d.style.display === 'none') return false;
                if (d.offsetWidth <= 0 || d.offsetHeight <= 0) return false;
                if (d.classList.contains('is-hidden')) return false;
                // 检查内部容器本体是否可见
                const inner = d.querySelector('.el-dialog, .el-drawer, .ant-modal');
                if (inner && (inner.offsetHeight <= 0 || inner.offsetWidth <= 0)) return false;
                return true;
            });

            // 检查确认框（MessageBox / Popconfirm）
            const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
            const hasMsgBox = msgBox && msgBox.offsetWidth > 0;

            const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
            const hasPopconfirm = popconfirm && popconfirm.offsetWidth > 0;

            // 优先级：message-box > popconfirm > dialog/drawer
            // message-box 和 popconfirm 是全局模态层，应优先处理
            let dialogTitle = '';
            let dialogType = '';
            if (hasMsgBox) {
                dialogType = 'message-box';
                dialogTitle = '确认操作';
            } else if (hasPopconfirm) {
                dialogType = 'popconfirm';
                dialogTitle = '确认操作';
            } else if (visible.length > 0) {
                const d = visible[0];
                const titleEl = d.querySelector(
                    '.el-dialog__title, .el-drawer__header, .ant-modal-title');
                dialogTitle = titleEl ? titleEl.textContent.trim() : '';
                dialogType = d.classList.contains('el-drawer') ? 'drawer' : 'dialog';
            }

            // 检查错误提示
            const errorMsgs = [];
            document.querySelectorAll('.el-message--error, .el-notification--error').forEach(el => {
                const text = el.textContent.trim();
                if (text) errorMsgs.push(text);
            });

            // 检查加载状态
            const isLoading = !!document.querySelector(
                '.el-loading-mask:not([style*="display: none"]), .ant-spin-spinning');

            return {
                hasDialog: visible.length > 0 || hasMsgBox || hasPopconfirm,
                dialogTitle,
                dialogType,
                dialogCount: visible.length + (hasMsgBox ? 1 : 0) + (hasPopconfirm ? 1 : 0),
                errors: errorMsgs,
                isLoading,
                url: window.location.href,
            };
        }""")

        expected_type = expected_state.get("type", "dialog")
        expected_title = expected_state.get("title", "")

        # 判断是否成功
        if expected_type in ("dialog", "drawer"):
            success = state.get("hasDialog", False)
            if success and expected_title:
                # 标题模糊匹配
                actual_title = state.get("dialogTitle", "")
                success = (expected_title in actual_title) or (actual_title in expected_title)
        else:
            success = True  # 无特定期望

        actual_state = ""
        if state.get("hasDialog"):
            actual_state = f"{state['dialogType']}: {state['dialogTitle']}"
        elif state.get("errors"):
            actual_state = f"错误提示: {', '.join(state['errors'][:2])}"
        elif state.get("isLoading"):
            actual_state = "页面加载中"
        else:
            actual_state = "无变化"

        return {"success": success, "actual_state": actual_state, "raw": state}

    except Exception as e:
        LOG.debug(f"  前置操作状态检查失败: {e}")
        return {"success": False, "actual_state": f"检查失败: {e}", "raw": {}}


async def _retry_precondition(page, trigger_btn: dict, max_retries: int = 3) -> bool:
    """前置操作失败时，换方式重试点击。

    尝试 3 种点击方式：
    1. Playwright click({force: true}) — 绕过遮挡
    2. JS evaluate el.click() — 原生点击
    3. 坐标点击 page.mouse.click(x, y) — 模拟真实鼠标

    每次尝试后检查弹窗是否出现。

    Args:
        page: Playwright Page 对象
        trigger_btn: 触发按钮信息 {"text": str, "tag": str, "className": str}
        max_retries: 最大重试次数

    Returns:
        bool: 重试后弹窗是否成功出现
    """
    btn_text = trigger_btn.get("text", "")
    if not btn_text:
        return False

    LOG.info(f"  前置操作重试: 尝试点击 '{btn_text}'")

    for attempt in range(1, max_retries + 1):
        clicked = False

        try:
            if attempt == 1:
                # 方式 1: Playwright force click（带隐藏过滤）
                LOG.debug(f"    尝试方式 1: force click")
                from core.discovery.replay.locator_helpers import safe_css
                selector = safe_css(f"button:has-text('{btn_text}'), span:has-text('{btn_text}')")
                await page.click(selector, force=True, timeout=3000)
                clicked = True

            elif attempt == 2:
                # 方式 2: JS 原生点击（带隐藏/disabled 检查）
                LOG.debug(f"    尝试方式 2: JS click")
                clicked = await page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button, span, a, .el-button');
                    for (const el of all) {{
                        if ((el.textContent || '').trim() === '{btn_text}' && el.offsetWidth > 0
                            && !el.disabled && !el.classList.contains('is-disabled')
                            && !el.closest('.is-hidden') && !el.closest('[style*="display: none"]')) {{
                            el.click(); return true;
                        }}
                    }}
                    return false;
                }}""")

            elif attempt == 3:
                # 方式 3: 坐标点击（带隐藏/disabled 检查）
                LOG.debug(f"    尝试方式 3: 坐标点击")
                rect = await page.evaluate(f"""() => {{
                    const all = document.querySelectorAll('button, span, a, .el-button');
                    for (const el of all) {{
                        if ((el.textContent || '').trim() === '{btn_text}' && el.offsetWidth > 0
                            && !el.disabled && !el.classList.contains('is-disabled')
                            && !el.closest('.is-hidden') && !el.closest('[style*="display: none"]')) {{
                            const r = el.getBoundingClientRect();
                            return {{x: r.x + r.width/2, y: r.y + r.height/2}};
                        }}
                    }}
                    return null;
                }}""")
                if rect:
                    await page.mouse.click(rect["x"], rect["y"])
                    clicked = True

        except Exception as e:
            LOG.debug(f"    方式 {attempt} 点击失败: {e}")
            continue

        if not clicked:
            continue

        # 等待弹窗出现
        await page.wait_for_timeout(1500)

        # 检查弹窗状态
        state = await _check_precondition_state(page, {"type": "dialog"})
        if state["success"]:
            LOG.info(f"    ✅ 方式 {attempt} 成功: {state['actual_state']}")
            return True
        else:
            LOG.debug(f"    方式 {attempt} 未触发弹窗: {state['actual_state']}")

    LOG.warning(f"  ❌ 所有 {max_retries} 种点击方式均未触发弹窗")
    return False


def _infer_action_role(action: str, btn: dict, delete_actions: list = None) -> str:
    """基于按钮 DOM 位置和特征推断语义角色（纯结构性，不写死任何文本）。

    规则：
    - 搜索输入框 → query
    - 非 BUTTON/DROPDOWN_ITEM 标签 → navigation（面包屑/导航/分页）
    - toolbar BUTTON + primary 样式 → create（主操作按钮，触发弹窗创建流程）
    - toolbar BUTTON + 非 primary → generic（批量操作/辅助按钮）
    - row_action / dropdown + 破坏性关键词 → delete（行内删除，走专有路径）
    - row_action / dropdown + 编辑关键词 → update（行内编辑，走专有路径）
    - row_action / dropdown + 其他 → generic（行内操作，走通用路径）

    Args:
        action: 按钮文本（作为 action 标识）
        btn: 按钮元数据字典
        delete_actions: 破坏性操作列表（来自 _find_create_delete_actions）

    Returns:
        "create" / "query" / "detail" / "delete" / "update" / "navigation" / "generic"
    """
    # 搜索输入框 → query
    if btn.get("is_search_input"):
        return "query"

    location = btn.get("location", "toolbar")
    tag = btn.get("tag", "")
    cls = (btn.get("className") or "").lower()

    # 非 BUTTON/DROPDOWN_ITEM 标签不参与业务验证
    if tag not in ("BUTTON", "DROPDOWN_ITEM"):
        return "navigation"

    # 有 href 的链接 → detail/navigation
    if tag == "A" and btn.get("href"):
        return "detail"

    # toolbar BUTTON：区分主操作按钮和辅助按钮
    if location == "toolbar":
        # primary 按钮 = 主操作（创建/新增）→ create 角色
        if "primary" in cls:
            return "create"
        # 非 primary 的 toolbar 按钮 = 批量操作/辅助功能 → generic
        return "generic"

    # row_action / dropdown：区分删除/编辑/其他
    action_lower = action.lower()

    # 破坏性操作 → delete（使用传入的 delete_actions 列表，避免重复定义关键词）
    if delete_actions and action in delete_actions:
        return "delete"

    # 编辑类操作 → update
    _update_keywords = ["编辑", "修改", "edit", "update", "更改"]
    if any(kw in action_lower for kw in _update_keywords):
        return "update"

    return "generic"


def _find_create_delete_actions(buttons_by_action: dict) -> tuple:
    """基于 DOM 位置找出 create-like 和所有 delete-like 操作。

    纯结构性判断：
    - create: toolbar 中第一个 primary BUTTON（主操作按钮）
    - delete: 所有破坏性操作（基于按钮文本关键词匹配，不写死具体操作名）

    Args:
        buttons_by_action: {action: btn_dict} 映射

    Returns:
        (create_action, delete_actions) — create 名称或 None，delete 名称列表
    """
    create_action = None
    delete_actions = []

    # create: toolbar 中第一个 primary BUTTON
    for action, btn in buttons_by_action.items():
        if btn.get("location") == "toolbar" and btn.get("tag") == "BUTTON":
            cls = (btn.get("className") or "").lower()
            if "primary" in cls:
                create_action = action
                break

    # delete: 基于文本关键词识别破坏性操作（泛化：不写死具体操作名）
    _destroy_keywords = ["删除", "delete", "remove", "清空", "clear"]
    for action in buttons_by_action:
        action_lower = action.lower()
        if any(kw in action_lower for kw in _destroy_keywords):
            delete_actions.append(action)

    return create_action, delete_actions


# Re-export _scan_dialog_buttons from element_scanner for convenience
# (function is defined in element_scanner but frequently imported from this module)
from core.discovery.ui_scanner.element_scanner import _scan_dialog_buttons  # noqa: E402, F811
