"""
capture_apis.py — Stage 2: API 捕获（Playbook 回放模式）

职责：从 Stage 1 生成的 playbook.json 回放操作流程，同时拦截网络请求。

架构：
  - request_interceptor: HTTP 请求/响应拦截与收集
  - button_driver: 按钮点击与交互驱动
  - endpoint_classifier: API 端点分类与去重

Stage 1 输出 playbook.json（包含完整的操作指令、选择器、填充规则），
Stage 2 直接执行 playbook 中的 steps，不再重新探测表单。

不重新探索、不重试、不做 UI 探测。
"""

import json
import logging
from pathlib import Path
from .request_interceptor import RequestInterceptor
from .button_driver import ButtonDriver
from .endpoint_classifier import EndpointClassifier
from .wait_helpers import (
    wait_for_table_ready,
    wait_for_dialog,
    wait_for_loading_complete,
    wait_for_api_response
)
from . import const

LOG = logging.getLogger("capture_apis")


async def capture_all(page, ui_result: dict, base_url: str, target_url: str,
                      project_dir: Path = None, module_name: str = "",
                      capture_all_mode: bool = False,
                      api_path_prefix: str = None) -> dict:
    """
    Stage 2 入口函数：从 playbook 回放操作 + 网络拦截

    Args:
        page: Playwright Page 对象
        ui_result: Stage 1 的 UI 探测结果（兼容旧版本，优先使用 playbook）
        base_url: 目标系统基础 URL
        target_url: 目标页面 URL
        project_dir: 项目目录（用于加载 playbook.json）
        module_name: 模块名称（用于定位 playbook.json）
        capture_all_mode: 是否捕获所有请求
        api_path_prefix: API 路径前缀

    Returns:
        dict: 捕获结果
    """
    LOG.info(f"[Stage 2] 开始 Playbook 回放模式捕获: {target_url}")

    try:
        # 优先从 playbook.json 加载，否则从 ui_result 构建
        playbook = _load_playbook(project_dir, module_name)
        if not playbook:
            LOG.warning("Playbook 不存在，尝试从 ui_result 构建")
            playbook = _build_playbook_from_ui_result(ui_result)

        if not playbook:
            LOG.error("无法获取 playbook，无法回放")
            return {"error": "no_playbook", "classified": {}, "stats": {}}

        result = await _capture_by_playbook(page, playbook, base_url, target_url,
                                           project_dir=project_dir, module_name=module_name,
                                           capture_all_mode=capture_all_mode,
                                           api_path_prefix=api_path_prefix)

        # 验证捕获结果完整性
        classified = result.get("classified", {})
        has_create = any(ep for cat, eps in classified.items()
                        if cat in ("create", "update", "delete", "detail") for ep in eps)

        if has_create:
            LOG.info(f"[Stage 2] ✅ 捕获成功，包含 CRUD API")
        else:
            LOG.warning(f"[Stage 2] ⚠️ 捕获未包含 CRUD API")

        return result

    except Exception as e:
        LOG.error(f"[Stage 2] ❌ 捕获异常: {e}")
        raise


def _load_playbook(project_dir: Path, module_name: str) -> dict | None:
    """从 project_dir/kb/module_discovered/{module_name}_playbook.json 加载 playbook"""
    if not project_dir or not module_name:
        return None

    playbook_path = project_dir / "kb" / "module_discovered" / f"{module_name}_playbook.json"
    if not playbook_path.exists():
        LOG.debug(f"Playbook 文件不存在: {playbook_path}")
        return None

    try:
        with open(playbook_path, 'r', encoding='utf-8') as f:
            playbook = json.load(f)
        LOG.info(f"已加载 Playbook: {playbook_path}")
        return playbook
    except Exception as e:
        LOG.warning(f"加载 Playbook 失败: {e}")
        return None


def _build_playbook_from_ui_result(ui_result: dict) -> dict | None:
    """从旧的 ui_result 构建 playbook（向后兼容）"""
    validated = ui_result.get("validated_operations", {})
    if not validated:
        return None

    # 调用 discover_ui.build_playbook
    from .discover_ui import build_playbook
    return build_playbook(ui_result)


async def _capture_by_playbook(page, playbook: dict, base_url: str, target_url: str,
                               project_dir: Path = None, module_name: str = "",
                               capture_all_mode: bool = False,
                               api_path_prefix: str = None) -> dict:
    """通过回放 playbook 来捕获 API"""
    # 加载知识库配置
    from .request_interceptor import load_kb
    kb_config = load_kb()

    # 初始化子模块
    interceptor = RequestInterceptor(page, base_url, target_url,
                                     capture_all_mode=capture_all_mode,
                                     api_path_prefix=api_path_prefix)
    button_driver = ButtonDriver(page)
    classifier = EndpointClassifier(kb_config)

    # 1. 安装请求拦截器
    await interceptor.install()

    # 2. 导航到目标页面
    LOG.info("导航到目标页面...")
    await page.goto(target_url, wait_until="networkidle", timeout=60000)
    await wait_for_table_ready(page, timeout=15000)

    # 3. 从 playbook.operations 获取操作序列
    operations = playbook.get("operations", {})
    if not operations:
        LOG.error("Playbook 中无操作定义，无法回放")
        return {"error": "no_operations", "classified": {}, "stats": {}}

    LOG.info(f"Playbook 操作序列: {list(operations.keys())}")

    # 4. 按 CRUD 执行顺序回放
    created_marker = None

    for action in const.CRUD_EXECUTION_ORDER:
        op = operations.get(action)
        if not op:
            LOG.debug(f"跳过 {action}: Playbook 中无定义")
            continue

        op_status = op.get("status", "success")
        if op_status == "failed":
            LOG.info(f"▶ 回放 {action} (Stage 1 标记为失败: {op.get('error_type', 'unknown')})")
        else:
            LOG.info(f"▶ 回放 {action}")
        interceptor.set_context(f"replay:{action}")

        try:
            # 执行 playbook 中的步骤序列
            steps = op.get("steps", [])
            result = await replay_from_playbook(page, steps, button_driver, created_marker)

            # 如果是 create 操作且成功，记录 marker
            if action == "create" and result.get("marker"):
                created_marker = result["marker"]
                LOG.info(f"  创建成功，marker: {created_marker}")
                await wait_for_table_ready(page, timeout=10000)

            await page.wait_for_timeout(1000)

        except Exception as e:
            LOG.warning(f"  ⚠️ 回放 {action} 失败: {e}")
            continue

    # 5. 收集拦截数据
    calls, samples, gates, sid = interceptor.collect()

    # 6. 分类端点
    result = classifier.deduplicate(calls, samples)
    result["permission_gates"] = gates
    if sid:
        result["sid"] = sid

    LOG.info(f"[Stage 2] Playbook 回放完成: {result.get('stats', {})}")

    return result


async def replay_from_playbook(page, steps: list, button_driver: ButtonDriver, marker: str = None) -> dict:
    """执行 playbook 中的步骤序列（泛化版本）

    自适应两种交互模式：
    - dialog 模式：点击按钮 → 弹出对话框 → 填表 → 提交
    - page-nav 模式：点击按钮 → 页面跳转 → 填表 → 提交 → 返回列表

    Args:
        page: Playwright Page 对象
        steps: 步骤列表（来自 playbook.operations[action].steps）
        button_driver: ButtonDriver 实例
        marker: 已创建的记录标识（用于行级操作）

    Returns:
        dict: 执行结果，包含 marker（如果是 create 操作）
    """
    result = {"success": True}

    # 交互状态上下文
    ctx = {
        "url_before_click": page.url,
        "original_url": page.url,
        "interaction_mode": None,  # "dialog" | "page-nav"
    }

    for i, step in enumerate(steps):
        action = step.get("action")
        LOG.debug(f"    执行步骤 {i+1}/{len(steps)}: {action}")

        try:
            if action == "click_button":
                ctx["url_before_click"] = page.url
                await _step_click_button(page, step, button_driver, ctx)

            elif action == "wait_for_dialog":
                await _step_wait_for_dialog(page, step, ctx)

            elif action == "fill_form":
                marker_value = await _step_fill_form(page, step, button_driver)
                if marker_value:
                    result["marker"] = marker_value

            elif action == "find_row":
                await _step_find_row(page, step, button_driver, marker)

            elif action == "click_row_button":
                await _step_click_row_button(page, step, button_driver, marker)

            elif action == "click_row_more":
                await _step_click_row_more(page, step, button_driver, marker)

            elif action == "confirm_dialog":
                await _step_confirm_dialog(page, step)

            elif action == "navigate_back":
                await _step_navigate_back(page, step)

            elif action == "assert_success":
                await _step_assert_success(page, step)

            elif action == "assert_row_disappeared":
                await _step_assert_row_disappeared(page, step, button_driver, marker)

            elif action == "select_row_checkbox":
                await _step_select_row_checkbox(page, step, button_driver, marker)

            # Legacy step types (backward compat — will be removed)
            elif action == "hover_dropdown":
                LOG.warning("    hover_dropdown 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_hover_dropdown_legacy(page, step, button_driver)

            elif action == "click_dropdown_item":
                LOG.warning("    click_dropdown_item 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_click_dropdown_item_legacy(page, step)

            elif action == "click_confirm_dialog":
                LOG.warning("    click_confirm_dialog 已废弃，请重新运行 Stage 1 生成新 playbook")
                await _step_click_confirm_dialog_legacy(page, step)

            else:
                LOG.warning(f"    未知步骤类型: {action}")

            # 步骤间等待
            await page.wait_for_timeout(500)

        except Exception as e:
            LOG.warning(f"    步骤 {i+1} 执行失败: {e}")
            raise

    return result


# ============================================================
# 步骤执行器
# ============================================================

async def _step_click_button(page, step: dict, button_driver: ButtonDriver, ctx: dict):
    """步骤：点击按钮（纯执行，不做探测）

    Stage 2 直接使用 Stage 1 提供的精确 locator，失败即报错。
    不再做任何 JS 去空格回退或通用选择器遍历。

    轻量级空格容错：如果 locator 包含中文且 has-text 失败，尝试去空格后的版本。
    """
    locator = step.get("playwright_locator")

    if not locator:
        raise Exception("click_button 步骤缺少 playwright_locator（Stage 1 未提供）")

    try:
        # 直接使用 Stage 1 提供的已验证 locator
        await page.click(locator, timeout=5000)
    except Exception as e:
        # 轻量级空格容错：仅针对中文 has-text 的空格变体
        import re
        has_text_match = re.search(r"has-text\(['\"](.+?)['\"]\)", locator)
        if has_text_match:
            original_text = has_text_match.group(1)
            # 检查是否包含中文字符
            if any('一' <= c <= '鿿' for c in original_text):
                # 尝试去空格后的版本
                normalized_text = original_text.replace(' ', '')
                if normalized_text != original_text:
                    normalized_locator = locator.replace(
                        f"has-text('{original_text}')",
                        f"has-text('{normalized_text}')"
                    ).replace(
                        f'has-text("{original_text}")',
                        f'has-text("{normalized_text}")'
                    )
                    try:
                        await page.click(normalized_locator, timeout=3000)
                        LOG.debug(f"    空格容错成功: '{original_text}' → '{normalized_text}'")
                        return
                    except Exception:
                        pass
        # 容错失败，抛出原始异常
        raise


async def _step_wait_for_dialog(page, step: dict, ctx: dict):
    """步骤：等待表单环境就绪（纯执行，不做探测）"""
    locator = step.get("playwright_locator")
    interaction_mode = step.get("interaction_mode", "dialog")
    timeout = step.get("timeout_ms", 5000)

    if not locator:
        raise Exception("wait_for_dialog 步骤缺少 playwright_locator（Stage 1 未提供）")

    ctx["interaction_mode"] = interaction_mode

    if interaction_mode == "dialog":
        # Dialog 模式：等待对话框出现
        await page.wait_for_selector(locator, state="visible", timeout=timeout)
        await page.wait_for_timeout(1000)
    elif interaction_mode == "page-nav":
        # Page-nav 模式：等待页面加载完成
        await page.wait_for_load_state("networkidle", timeout=10000)
        await page.wait_for_timeout(1000)
    else:
        raise Exception(f"未知的 interaction_mode: {interaction_mode}")


async def _step_fill_form(page, step: dict, button_driver: ButtonDriver) -> str | None:
    """步骤：填充表单字段（纯执行，不做推断）"""
    from .form_filler import apply_fill_rule, FormFiller
    from . import const

    fields = step.get("fields", [])
    filled_count = 0
    marker_value = None

    # 收集 multi_step 字段，统一用 FormFiller 处理
    multi_step_fields = []
    simple_fields = []

    for field in fields:
        label = field.get("label")
        kb_category = field.get("kb_category", "")
        locator = field.get("playwright_locator")

        if not locator:
            LOG.warning(f"    字段 {label} 缺少 locator，跳过")
            continue

        # Stage 1 必须提供 kb_category，不做推断
        if not kb_category:
            LOG.warning(f"    字段 {label} 缺少 kb_category（Stage 1 未提供），跳过")
            continue

        if kb_category in const.MULTI_STEP_TYPES or kb_category == "form-checkbox":
            # 多步字段，后续统一处理
            multi_step_fields.append(field)
        else:
            simple_fields.append(field)

    # --- 1. 处理普通字段 (input/textarea/radio) ---
    for field in simple_fields:
        label = field.get("label")
        locator = field.get("playwright_locator")
        fill_rule = field.get("fill_rule")
        is_marker = field.get("is_marker", False)
        kb_category = field.get("kb_category", "")

        # Radio 字段：Stage 1 已提供精确的 option_locator
        if kb_category == "radio":
            try:
                await page.click(locator, timeout=3000)
                filled_count += 1
                LOG.debug(f"      选择 radio: {label}")
            except Exception as e:
                LOG.debug(f"      radio 失败: {label}: {e}")
            continue

        # 普通 input/textarea：生成填充值 + page.fill()
        if fill_rule:
            value = apply_fill_rule(fill_rule)
        else:
            LOG.warning(f"    字段 {label} 缺少 fill_rule，跳过")
            continue

        if value is None:
            continue

        try:
            await page.fill(locator, str(value), timeout=3000)
            filled_count += 1
            LOG.debug(f"      填充: {label} = {value}")
            if is_marker and not marker_value:
                marker_value = value
        except Exception as e:
            LOG.debug(f"      填充失败: {label}: {e}")

    # --- 2. 处理 multi_step 字段 (el-select/el-cascader/date-picker/form-checkbox) ---
    if multi_step_fields:
        ff = FormFiller(page)
        framework = step.get("framework", "element-ui")
        ms_filled, _ = await ff.fill_multi_step_fields(multi_step_fields, framework)
        filled_count += ms_filled
        LOG.debug(f"    multi_step 完成: {ms_filled}/{len(multi_step_fields)}")

    LOG.info(f"    已填充 {filled_count}/{len(fields)} 个字段")
    return marker_value


async def _step_click_row_more(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：点击行级"更多"菜单项（KB 驱动的 row-level 操作）

    调用 button_driver.click_row_more_item 执行：
    1. find_data_row(marker) 定位行
    2. click_row_more_item(row, item_text) 展开下拉并点击
    """
    item_text = step.get("item_text")
    if not item_text:
        raise Exception("click_row_more 步骤缺少 item_text（Stage 1 未提供）")
    if not marker:
        raise Exception("click_row_more 步骤需要 marker 但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")

    clicked = await button_driver.click_row_more_item(row, item_text)
    if not clicked:
        raise Exception(f"未找到菜单项: {item_text}")


async def _step_navigate_back(page, step: dict):
    """步骤：导航回指定 URL（用于 authorize 等页面跳转操作）"""
    url = step.get("url")
    if not url:
        raise Exception("navigate_back 步骤缺少 url（Stage 1 未提供）")

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    # 等待表格重新加载
    from .wait_helpers import wait_for_table_ready
    await wait_for_table_ready(page, timeout=15000)


async def _step_find_row(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：定位数据行"""
    if not marker:
        raise Exception("find_row 步骤需要 marker，但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")


async def _step_click_row_button(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：在数据行内点击按钮

    先通过 marker 定位数据行，再在行内查找并点击指定文本的按钮。
    避免全局匹配到不可见的同名按钮。
    """
    button_text = step.get("button_text")
    if not button_text:
        raise Exception("click_row_button 步骤缺少 button_text（Stage 1 未提供）")
    if not marker:
        raise Exception("click_row_button 步骤需要 marker 但无可用 marker")

    row = await button_driver.find_data_row(marker)
    if not row:
        raise Exception(f"未找到数据行: {marker}")

    # 在行内查找按钮
    btn = await row.query_selector(f'button:has-text("{button_text}")')
    if not btn:
        raise Exception(f"在数据行内未找到按钮: {button_text}")

    # 检查按钮是否可见
    is_visible = await btn.is_visible()
    if not is_visible:
        raise Exception(f"数据行内的按钮不可见: {button_text}")

    await btn.click()
    LOG.info(f"点击行内按钮: {button_text}")


async def _step_confirm_dialog(page, step: dict):
    """步骤：确认对话框（简化版，自动检测）

    调用 button_driver.confirm_dialog 自动检测并点击确认按钮。
    支持 MessageBox/Popconfirm/Generic 三种类型。
    """
    from .button_driver import confirm_dialog
    confirmed = await confirm_dialog(page)
    if not confirmed:
        LOG.warning("    未检测到确认对话框或确认按钮")


# ============================================================
# Legacy 步骤执行器（向后兼容，将在后续版本移除）
# ============================================================

async def _step_hover_dropdown_legacy(page, step: dict, button_driver: ButtonDriver):
    """步骤：展开下拉菜单（纯执行，不做回退）"""
    parent_selector = step.get("parent_selector")
    trigger_method = step.get("trigger_method", "hover")
    wait_ms = step.get("wait_ms", 600)

    if not parent_selector:
        raise Exception("hover_dropdown 步骤缺少 parent_selector（Stage 1 未提供）")

    # 找到"更多"按钮
    more_btn = await page.query_selector(parent_selector)
    if not more_btn:
        raise Exception(f"未找到 dropdown 触发器: {parent_selector}")

    # 使用 Stage 1 验证的触发方式
    if trigger_method == "hover":
        await more_btn.hover()
    else:
        await more_btn.click()

    await page.wait_for_timeout(wait_ms)

    # 不再做 DOM 展开验证或 hover/click 回退


async def _step_click_dropdown_item_legacy(page, step: dict):
    """步骤：点击下拉菜单项"""
    locator = step.get("playwright_locator")
    if not locator:
        raise Exception("click_dropdown_item 步骤缺少 locator")

    await page.click(locator, timeout=3000)


async def _step_click_confirm_dialog_legacy(page, step: dict):
    """步骤：点击确认对话框（纯执行，不做回退）"""
    confirm_locator = step.get("confirm_locator")

    if not confirm_locator:
        raise Exception("click_confirm_dialog 步骤缺少 confirm_locator（Stage 1 未提供）")

    # 直接使用 Stage 1 提供的已验证 locator
    await page.wait_for_selector(confirm_locator, state="visible", timeout=3000)
    await page.click(confirm_locator)

    # 不再调用通用的 confirm_dialog() 回退


async def _step_assert_success(page, step: dict):
    """步骤：验证操作成功（纯执行，无默认值）"""
    locator = step.get("playwright_locator")

    if not locator:
        LOG.warning(f"    assert_success 步骤缺少 locator（Stage 1 未提供），跳过验证")
        return

    try:
        await page.wait_for_selector(locator, state="visible", timeout=5000)
    except Exception:
        LOG.warning(f"    未检测到成功消息: {locator}")


async def _step_assert_row_disappeared(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：验证数据行已消失"""
    if not marker:
        return

    # 等待表格刷新
    await wait_for_table_ready(page, timeout=5000)

    # 检查行是否还在
    row = await button_driver.find_data_row(marker)
    if row:
        LOG.warning(f"    数据行仍然存在: {marker}")


async def _step_select_row_checkbox(page, step: dict, button_driver: ButtonDriver, marker: str):
    """步骤：勾选数据行的 checkbox（JS click，兼容 Element UI 固定列）

    Element UI 固定列表格中，主体 wrapper 的 checkbox 是隐藏的占位元素，
    只有固定列 wrapper 中的 checkbox 可见。Playwright click 要求元素可见，
    因此使用 JS click 直接触发事件（与 Stage 1 _ensure_row_selected 一致）。
    """
    if not marker:
        raise Exception("select_row_checkbox 步骤需要 marker")

    # 使用 JS click：遍历所有 .el-table__body 行，找到含 marker 的行后点击 checkbox
    checked = await page.evaluate("""(text) => {
        const rows = document.querySelectorAll('.el-table__body tr');
        for (const row of rows) {
            if ((row.textContent || '').includes(text)) {
                const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                if (cb) {
                    cb.click();
                    return true;
                }
            }
        }
        return false;
    }""", marker)

    if not checked:
        raise Exception(f"未找到含 '{marker}' 的行或无 checkbox 可点击")

    await page.wait_for_timeout(300)
