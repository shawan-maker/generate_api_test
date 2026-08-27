"""
capture_apis.py — Stage 2: API 捕获编排层

职责：编排表单填充、按钮驱动、请求拦截、端点分类四个子模块，完成完整的 API 捕获流程。

架构：
  - request_interceptor: HTTP 请求/响应拦截与收集
  - button_driver: 按钮点击与交互驱动
  - form_filler: 表单填充与验证
  - endpoint_classifier: API 端点分类与去重
"""

import time
import json
import logging
from pathlib import Path
from .request_interceptor import RequestInterceptor
from .button_driver import ButtonDriver
from .form_filler import FormFiller
from .endpoint_classifier import EndpointClassifier
from .diagnostic_mode import DiagnosticMode
from .wait_helpers import (
    wait_for_table_ready,
    wait_for_dialog,
    wait_for_dropdown,
    wait_for_navigation_complete,
    wait_for_dialog_dismissed,
    wait_for_api_response
)

LOG = logging.getLogger("capture_apis")


async def capture_all(page, ui_result: dict, base_url: str, target_url: str,
                      project_dir: Path = None, module_name: str = "",
                      capture_all_mode: bool = False,
                      api_path_prefix: str = None) -> dict:
    """
    Stage 2 入口函数（带自动重试机制）

    Args:
        page: Playwright Page 对象
        ui_result: Stage 1 的 UI 探测结果
        base_url: 目标系统基础 URL
        target_url: 目标页面 URL
        project_dir: 项目目录（用于调试工件输出）
        module_name: 模块名称（用于调试工件命名）

    Returns:
        dict: 捕获结果
    """
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        LOG.info(f"[Stage 2] 第 {attempt}/{max_retries} 次捕获尝试: {target_url}")

        try:
            result = await _capture_once(page, ui_result, base_url, target_url, attempt,
                                         project_dir=project_dir, module_name=module_name,
                                         capture_all_mode=capture_all_mode)

            # 验证捕获结果完整性
            classified = result.get("classified", {})
            has_create = any(ep for cat, eps in classified.items()
                            if cat in ("create", "update", "delete", "detail") for ep in eps)

            if has_create or attempt == max_retries:
                if has_create:
                    LOG.info(f"[Stage 2] ✅ 第 {attempt} 次捕获成功，包含 CRUD API")
                else:
                    LOG.warning(f"[Stage 2] ⚠️ 第 {attempt} 次捕获未包含 CRUD API（已达最大重试次数）")
                    # 诊断触发器：Stage 2 验证失败且重试耗尽
                    if project_dir:
                        try:
                            diag = DiagnosticMode(page, module_name, project_dir)
                            await diag.run_strategy("stage2_validation_failed",
                                                    capture_result=result,
                                                    interceptor=interceptor if 'interceptor' in dir() else None)
                        except Exception as diag_err:
                            LOG.warning(f"    诊断模式执行失败: {diag_err}")
                return result

            # 捕获不完整，准备重试
            LOG.warning(f"[Stage 2] ⚠️ 第 {attempt} 次捕获不完整，准备重试...")
            if attempt < max_retries:
                # 重置页面状态
                await page.goto(target_url, wait_until="networkidle", timeout=60000)
                await wait_for_table_ready(page, timeout=15000)
                await page.wait_for_timeout(2000)

        except Exception as e:
            LOG.error(f"[Stage 2] ❌ 第 {attempt} 次捕获异常: {e}")
            if attempt == max_retries:
                raise
            # 重置页面状态准备重试
            try:
                await page.goto(target_url, wait_until="networkidle", timeout=60000)
                await wait_for_table_ready(page, timeout=15000)
            except Exception as reset_err:
                LOG.warning(f"页面重置失败: {reset_err}")

    # 不应该到这里
    return result


async def _capture_once(page, ui_result: dict, base_url: str, target_url: str,
                         attempt: int = 1, project_dir: Path = None,
                         module_name: str = "",
                         capture_all_mode: bool = False,
                         api_path_prefix: str = None) -> dict:
    """单次捕获流程（内部函数，由 capture_all 调用）"""
    # 加载知识库配置
    from .request_interceptor import load_kb
    kb_config = load_kb()

    # 初始化子模块
    interceptor = RequestInterceptor(page, base_url, target_url,
                                     capture_all_mode=capture_all_mode,
                                     api_path_prefix=api_path_prefix)
    button_driver = ButtonDriver(page)
    form_filler = FormFiller(page)
    classifier = EndpointClassifier(kb_config)

    # 1. 安装请求拦截器
    await interceptor.install()

    # 2. 导航到目标页面（仅首次）
    if attempt == 1:
        LOG.info("导航到目标页面...")
        await page.goto(target_url, wait_until="networkidle", timeout=60000)
        await wait_for_table_ready(page, timeout=15000)

    # 3. 从 UI 结果获取工具栏按钮（而非重新扫描）
    toolbar_buttons = [b["text"] for b in ui_result.get("toolbar_buttons", [])]
    LOG.info(f"从 UI 结果加载 {len(toolbar_buttons)} 个工具栏按钮: {toolbar_buttons}")

    ctx_holder = {"v": "init"}
    created_username = None

    for btn_text in toolbar_buttons:
        interceptor.set_context(f"click:{btn_text}")
        btn_type = classifier.classify_by_text(btn_text)

        clicked = await button_driver.click_by_text(btn_text)
        if not clicked:
            LOG.warning(f"无法点击按钮: {btn_text}")
            continue

        # 等待按钮点击后的响应（弹窗或 API）
        if btn_type == "create":
            await wait_for_dialog(page, timeout=8000)
        else:
            await wait_for_api_response(page, timeout=5000)

        # 处理创建流程（带重试）
        if btn_type == "create":
            created_username = f"autotest{str(int(time.time()))[-8:]}"
            create_success = await _handle_create_page_with_retry(
                page, created_username, form_filler, interceptor,
                project_dir=project_dir, module_name=module_name)

            # 创建成功后，页面可能仍在创建页（非弹窗模式），需导航回列表页
            if create_success and (target_url not in page.url or "/create" in page.url):
                LOG.info(f"    创建后导航回列表页: {target_url[:60]}")
                try:
                    await page.goto(target_url, wait_until="networkidle", timeout=30000)
                except Exception as nav_e:
                    LOG.warning(f"    导航回列表页失败: {nav_e}")

            await wait_for_table_ready(page, timeout=10000)

            # 调试：打印当前页面 URL 和表格内容
            LOG.info(f"    当前页面: {page.url[:80]}")
            try:
                rows = await page.query_selector_all('.el-table__body-wrapper tbody tr')
                LOG.info(f"    表格行数: {len(rows)}")
                for i, row in enumerate(rows[:3]):
                    text = await row.inner_text()
                    LOG.info(f"    行{i}: {text[:100]}")
            except Exception as e:
                LOG.warning(f"    读取表格失败: {e}")

            # 创建成功后，驱动行级操作（带重试）
            if create_success:
                await _capture_row_operations_with_retry(
                    page, created_username, ctx_holder,
                    interceptor, button_driver, form_filler, ui_result,
                    project_dir=project_dir, module_name=module_name)

        # 处理删除确认
        elif btn_type == "delete":
            await button_driver.confirm_message_box()
            await wait_for_dialog_dismissed(page, timeout=8000)
            await wait_for_api_response(page, url_pattern="delete", timeout=5000)

        # 其他操作关闭对话框
        else:
            await button_driver.close_all_dialogs()
            await wait_for_dialog_dismissed(page, timeout=5000)

    # 4. 展开下拉菜单
    LOG.info("展开下拉菜单...")
    await _discover_and_click_dropdowns(page, ctx_holder, interceptor, button_driver)

    # 5. 收集拦截数据
    calls, samples, gates, sid = interceptor.collect()

    # 6. 分类端点
    result = classifier.deduplicate(calls, samples)
    result["permission_gates"] = gates
    if sid:
        result["sid"] = sid

    LOG.info(f"[Stage 2] 捕获完成: {result.get('stats', {})}")

    return result


async def _handle_create_page_with_retry(page, created_username: str,
                                         form_filler: FormFiller,
                                         interceptor: RequestInterceptor,
                                         max_form_retries: int = 2,
                                         project_dir: Path = None,
                                         module_name: str = "") -> bool:
    """处理创建类操作（带重试机制）。

    如果首次提交后未检测到创建 API 响应（说明表单填充不完整），
    则自动重试：清空表单→重新扫描→补充填充→再次提交。

    Returns:
        bool: 创建是否成功（检测到了 POST 创建请求）
    """
    await wait_for_dialog(page, timeout=8000)
    create_url_before = page.url
    LOG.info(f"    创建页面: {create_url_before[:80]}")

    for form_attempt in range(1, max_form_retries + 1):
        # 使用 FormFiller 扫描和填充表单
        field_labels = await form_filler.scan_form_fields_v2()
        LOG.info(f"    [表单尝试 {form_attempt}/{max_form_retries}] "
                f"扫描到 {len(field_labels)} 个字段: {[f['label'] for f in field_labels[:10]]}")

        # 填充表单
        filled_count = await form_filler.fill_create_form(field_labels, created_username)
        LOG.info(f"    已填充 {filled_count}/{len(field_labels)} 个字段")

        # 选择下拉框
        select_count = await form_filler.select_dropdowns()
        LOG.info(f"    已选择 {select_count} 个下拉框")

        # ★ 在提交前记录拦截器当前请求数（修复时序 bug：
        #   submit_form_v2 内部 click+wait 期间 POST 已发出并被拦截，
        #   若提交后再取 initial_count，POST 已计入列表，检测永远失败）
        calls_before_submit = len(interceptor.calls)

        # 提交表单
        submitted = await form_filler.submit_form_v2()
        LOG.info(f"    提交结果: {submitted}")

        # 检查是否触发了创建 API
        create_detected = await _check_create_api_triggered(
            page, interceptor, calls_before=calls_before_submit)

        if create_detected:
            LOG.info(f"    ✅ 创建 API 已触发（第 {form_attempt} 次尝试）")
            return True

        # 首次尝试失败，准备重试
        if form_attempt < max_form_retries:
            LOG.warning(f"    ⚠️ 第 {form_attempt} 次提交未触发创建 API，准备重试...")

            # 检查表单校验错误
            errors = await _get_form_errors(page)
            if errors:
                LOG.warning(f"    表单校验错误: {errors}")

            # 如果页面还在表单上（未跳转），清空后重试
            if create_url_before in page.url or "/create" in page.url:
                # 尝试重新填充未填充的字段
                await page.wait_for_timeout(1000)
                # 不关闭对话框，直接在现有表单上重试
            else:
                # 页面跳转了但没检测到 API，可能需要导航回创建页
                LOG.warning(f"    页面已跳转到: {page.url[:60]}")
                try:
                    await page.go_back()
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    LOG.warning(f"    导航回创建页失败: {e}")

    LOG.error(f"    ❌ 创建失败：{max_form_retries} 次尝试均未触发创建 API")

    # 自动转储调试工件
    if project_dir:
        debug_dir = project_dir / "output" / "debug" / module_name
        errors = await _get_form_errors(page)
        await _dump_debug_artifacts(page, debug_dir, module_name, {
            "call_count": len(interceptor.calls),
            "post_detected": False,
            "form_errors": errors,
        })

    return False


async def _check_create_api_triggered(page, interceptor: RequestInterceptor,
                                      timeout: int = 8000,
                                      calls_before: int = 0) -> bool:
    """检查是否触发了创建 API 请求。

    通过检查拦截器中是否有新的 POST 请求来判断。

    Args:
        calls_before: 提交表单前的拦截器请求数，用于正确检测新增的 POST 请求。
                      默认为 0（兼容旧调用）。
    """
    start = time.time()
    initial_count = calls_before if calls_before else len(interceptor.calls)

    while time.time() - start < timeout / 1000:
        current_count = len(interceptor.calls)
        if current_count > initial_count:
            # 有新的请求，检查是否是创建类 API
            for call in interceptor.calls[initial_count:]:
                method = call.get("method", "")
                path = call.get("pathname", "")
                # 创建类 API 通常是 POST（但不是所有 POST 都是创建）
                if method == "POST" and not any(kw in path for kw in
                    ("/list", "/page", "/search", "/query", "/menu", "/theme",
                     "/favorite", "/dictionary", "/check", "/validate")):
                    LOG.info(f"    → 检测到创建请求: {method} {path[:60]}")
                    return True
        await page.wait_for_timeout(500)

    return False


async def _get_form_errors(page) -> list:
    """获取当前页面上的表单校验错误信息。"""
    try:
        return await page.evaluate("""
            () => {
                const errors = [];
                document.querySelectorAll('.el-form-item__error').forEach(el => {
                    const text = el.textContent.trim();
                    if (text) errors.push(text);
                });
                document.querySelectorAll('.el-message--error').forEach(el => {
                    const text = el.textContent.trim();
                    if (text) errors.push(text);
                });
                return errors;
            }
        """)
    except Exception:
        return []


async def _dump_debug_artifacts(page, debug_dir: Path, module_name: str, info: dict):
    """捕获失败时自动转储调试工件（截图、表单状态、拦截器状态）。

    Args:
        page: Playwright Page 对象
        debug_dir: 调试工件输出目录
        module_name: 模块名称
        info: 诊断信息字典
    """
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")

        # 1. 截图
        screenshot_path = debug_dir / f"capture_fail_{module_name}_{ts}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)
        LOG.warning(f"    📸 截图已保存: {screenshot_path}")

        # 2. 提取表单字段状态
        form_state = await page.evaluate("""
            () => {
                const fields = [];
                document.querySelectorAll('.el-form-item').forEach((item, idx) => {
                    const labelEl = item.querySelector('.el-form-item__label');
                    const inputEl = item.querySelector('input, textarea');
                    const errorEl = item.querySelector('.el-form-item__error');
                    fields.push({
                        index: idx,
                        label: labelEl ? labelEl.textContent.trim() : '',
                        value: inputEl ? inputEl.value : '',
                        error: errorEl ? errorEl.textContent.trim() : '',
                        required: item.classList.contains('is-required'),
                        type: item.querySelector('.el-tree') ? 'tree' :
                              item.querySelector('.el-select') ? 'select' :
                              item.querySelector('textarea') ? 'textarea' : 'input',
                    });
                });
                return fields;
            }
        """)

        # 3. 保存表单状态
        form_state_path = debug_dir / f"form_state_{module_name}_{ts}.json"
        form_state_path.write_text(
            json.dumps(form_state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        LOG.warning(f"    📝 表单状态已保存: {form_state_path}")

        # 4. 保存拦截器状态
        interceptor_state_path = debug_dir / f"interceptor_state_{module_name}_{ts}.json"
        interceptor_state = {
            "call_count": info.get("call_count", 0),
            "post_detected": info.get("post_detected", False),
            "form_errors": info.get("form_errors", []),
            "timestamp": ts,
        }
        interceptor_state_path.write_text(
            json.dumps(interceptor_state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        LOG.warning(f"    🔍 拦截器状态已保存: {interceptor_state_path}")

        LOG.warning(f"    ✅ 调试工件已保存到: {debug_dir}")

    except Exception as e:
        LOG.error(f"    ❌ 转储调试工件失败: {e}")


def _build_operations_from_ui(ui_result: dict) -> list:
    """从 Stage 1 的 ui_result 动态构建行操作列表。

    如果 ui_result 为空或没有行操作信息，fallback 到硬编码列表。

    Returns:
        list of tuples: (op_label, ctx, is_dropdown, item_text)
    """
    # Fallback 硬编码列表
    DEFAULT_OPERATIONS = [
        ("编辑", "row:编辑", False, "编辑"),
        ("冻结", "dropdown:更多:冻结", True, "冻结"),
        ("启用", "dropdown:更多:启用", True, "启用"),
        ("锁定", "dropdown:更多:锁定", True, "锁定"),
        ("解锁", "dropdown:更多:解锁", True, "解锁"),
        ("重置密码", "dropdown:更多:重置密码", True, "重置密码"),
        ("授权", "dropdown:更多:授权", True, "授权"),
        ("删除", "row:删除", False, "删除"),
    ]

    if not ui_result:
        return DEFAULT_OPERATIONS

    operations = []
    row_actions = ui_result.get("row_actions", [])
    dropdowns = ui_result.get("dropdowns", [])

    # 1. 从 row_actions 提取直接按钮（非 dropdown 触发器）
    dropdown_triggers = {"更多", "操作", "Actions", "More", "批量操作"}
    for btn in row_actions:
        text = btn.get("text", "")
        if not text or text in dropdown_triggers:
            continue
        operations.append((text, f"row:{text}", False, text))

    # 2. 从 dropdowns 提取下拉菜单项
    for dd in dropdowns:
        text = dd.get("text", "")
        parent = dd.get("parent", "更多")
        if not text:
            continue
        operations.append((text, f"dropdown:{parent}:{text}", True, text))

    # 3. 如果动态构建为空，使用 fallback
    if not operations:
        LOG.debug("ui_result 未提供行操作信息，使用默认列表")
        return DEFAULT_OPERATIONS

    # 4. 按 CRUD 优先级排序（删除排最后）
    def _crud_priority(op_label: str) -> int:
        label_lower = op_label.lower()
        if "删除" in label_lower or "delete" in label_lower:
            return 100  # 删除排最后
        if "编辑" in label_lower or "edit" in label_lower:
            return 1
        if "查看" in label_lower or "view" in label_lower:
            return 2
        if "授权" in label_lower or "auth" in label_lower:
            return 50
        return 10

    operations.sort(key=lambda op: _crud_priority(op[0]))
    return operations


async def _capture_row_operations_with_retry(page, marker: str, ctx_holder: dict,
                                             interceptor: RequestInterceptor,
                                             button_driver: ButtonDriver,
                                             form_filler: FormFiller,
                                             ui_result: dict = None,
                                             max_row_retries: int = 2,
                                             project_dir: Path = None,
                                             module_name: str = ""):
    """创建成功后，对新建行逐一驱动业务操作（带重试机制）。

    行操作列表从 Stage 1 的 ui_result 动态构建（fallback 到硬编码列表）。
    """
    LOG.info(f"  🎯 定位新建行: {marker}")

    operations = _build_operations_from_ui(ui_result)
    LOG.info(f"  行操作列表: {[op[0] for op in operations]}")

    consecutive_row_failures = 0
    consecutive_click_failures = 0

    for op_label, ctx, is_dropdown, item in operations:
        row = await button_driver.find_data_row(marker)
        if row is None:
            LOG.warning(f"  ⚠️ 未找到行({marker})，跳过 [{op_label}]")
            consecutive_row_failures += 1
            # 诊断触发器：行查找连续失败 2 次
            if consecutive_row_failures >= 2 and project_dir:
                try:
                    diag = DiagnosticMode(page, module_name, project_dir)
                    await diag.run_strategy("row_not_found", marker=marker)
                    LOG.info(f"    📊 已触发 row_not_found 诊断")
                except Exception as diag_err:
                    LOG.warning(f"    诊断模式执行失败: {diag_err}")
                consecutive_row_failures = 0  # 重置计数器
            continue

        # 重置行查找失败计数
        consecutive_row_failures = 0

        # 打印目标行文本
        try:
            row_txt = (await row.inner_text()).replace("\n", " | ")[:60]
            LOG.info(f"    [目标行] {row_txt}")
        except Exception as e:
            LOG.debug(f"获取目标行文本失败: {e}")

        interceptor.set_context(ctx)
        ctx_holder["v"] = ctx

        # 点击操作按钮（带重试）
        ok = False
        for attempt in range(1, max_row_retries + 1):
            if is_dropdown:
                ok = await button_driver.click_row_more_item(row, item)
            else:
                ok = await button_driver.click_row_button_v2(row, item)

            if ok:
                LOG.info(f"    → {op_label}（第 {attempt} 次尝试）")
                consecutive_click_failures = 0  # 重置点击失败计数
                break
            elif attempt < max_row_retries:
                LOG.warning(f"    ⚠️ [{op_label}] 第 {attempt} 次点击失败，重试...")
                await page.wait_for_timeout(1000)
                # 重新定位行
                row = await button_driver.find_data_row(marker)
                if row is None:
                    break

        if not ok:
            LOG.warning(f"  ⚠️ 未触发 [{op_label}]（{max_row_retries} 次尝试均失败）")
            consecutive_click_failures += 1
            # 诊断触发器：按钮点击连续失败 2 次
            if consecutive_click_failures >= 2 and project_dir:
                try:
                    diag = DiagnosticMode(page, module_name, project_dir)
                    await diag.run_strategy("button_click_failed", row=row)
                    LOG.info(f"    📊 已触发 button_click_failed 诊断")
                except Exception as diag_err:
                    LOG.warning(f"    诊断模式执行失败: {diag_err}")
                consecutive_click_failures = 0  # 重置计数器
            continue

        # 重置点击失败计数
        consecutive_click_failures = 0

        # 处理特殊操作
        if op_label == "删除":
            await button_driver.confirm_message_box()
            await wait_for_dialog_dismissed(page, timeout=8000)
            await wait_for_api_response(page, url_pattern="delete", timeout=5000)
        elif op_label == "授权":
            await wait_for_navigation_complete(page, timeout=10000)
            try:
                if "add-authority" in page.url or "/authority" in page.url:
                    # 导航回目标列表页（使用传入的 target_url，不硬编码）
                    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    await wait_for_table_ready(page, timeout=15000)
                    LOG.info("    已从授权页导航回列表页")
            except Exception as e:
                LOG.warning(f"授权页导航回列表失败: {e}")
        elif _is_edit_operation(op_label):
            # 编辑操作：等待弹窗/编辑页出现，修改表单，提交保存
            await _handle_edit_operation(page, form_filler, interceptor)
        else:
            await button_driver.after_row_op(form_filler)

    LOG.info("  ✅ 行级业务操作驱动完成")


def _is_edit_operation(op_label: str) -> bool:
    """判断是否为编辑类操作"""
    edit_keywords = ["编辑", "修改", "edit", "update", "变更"]
    return any(kw in op_label.lower() for kw in edit_keywords)


async def _handle_edit_operation(page, form_filler, interceptor) -> bool:
    """处理编辑操作：等待编辑弹窗/页面出现，修改字段，提交保存。

    Returns:
        bool: 编辑是否成功（检测到 PUT/PATCH 请求）
    """
    from .button_driver import close_dialog

    LOG.info("    等待编辑表单出现...")
    # 等待弹窗或导航完成
    try:
        await wait_for_dialog(page, timeout=6000)
    except Exception:
        pass
    await page.wait_for_timeout(1000)

    # 诊断：编辑页面的结构
    edit_page_info = await page.evaluate("""() => {
        const dialogs = document.querySelectorAll('.el-dialog__wrapper, .el-drawer');
        const visibleDialogs = Array.from(dialogs).filter(d => d.style.display !== 'none');

        // 全局可见按钮
        const buttons = Array.from(document.querySelectorAll('button, .el-button'))
            .filter(b => b.offsetWidth > 0 && b.offsetHeight > 0)
            .map(b => ({
                text: b.textContent.trim(),
                classes: b.className.slice(0,60),
                primary: b.classList.contains('el-button--primary')
            }))
            .filter(b => b.text && b.text.length < 20);

        // 对话框内部按钮（包括不可见的）
        const dialogButtons = [];
        visibleDialogs.forEach(dlg => {
            const allBtns = dlg.querySelectorAll('button, .el-button');
            allBtns.forEach(b => {
                dialogButtons.push({
                    text: b.textContent.trim(),
                    visible: b.offsetWidth > 0 && b.offsetHeight > 0,
                    display: window.getComputedStyle(b).display,
                    parentDisplay: b.parentElement ? window.getComputedStyle(b.parentElement).display : 'n/a',
                    grandParentDisplay: b.parentElement && b.parentElement.parentElement ?
                        window.getComputedStyle(b.parentElement.parentElement).display : 'n/a',
                    classes: b.className.slice(0,60),
                    primary: b.classList.contains('el-button--primary'),
                });
            });
        });

        // 对话框内部结构
        const dialogStructure = visibleDialogs.map(dlg => ({
            type: dlg.classList.contains('el-drawer') ? 'drawer' : 'dialog',
            innerHTML: dlg.innerHTML.substring(0, 500),
            childCount: dlg.children.length,
        }));

        return {
            dialogCount: visibleDialogs.length,
            dialogTypes: visibleDialogs.map(d => d.classList.contains('el-drawer') ? 'drawer' : 'dialog'),
            allButtons: buttons.slice(0, 20),
            dialogButtons: dialogButtons.slice(0, 20),
            dialogStructure: dialogStructure,
            hasForm: !!document.querySelector('.el-form'),
            url: window.location.href,
        };
    }""")
    LOG.info(f"    编辑页诊断: dialogs={edit_page_info['dialogCount']} "
             f"types={edit_page_info['dialogTypes']}")

    # 扫描编辑表单字段
    LOG.info("    扫描编辑表单字段...")
    fields = await form_filler.scan_form_fields_v2()
    if not fields:
        LOG.warning("    未找到编辑表单字段，关闭弹窗")
        await close_dialog(page)
        return False

    LOG.info(f"    找到 {len(fields)} 个字段: {[f['label'] for f in fields[:8]]}")

    # 修改描述/备注类文本字段（不修改名称字段，避免 marker 失效）
    import time as _time
    ts = str(int(_time.time()))[-6:]
    modified = False
    edit_targets = ["描述", "备注", "说明", "remark", "description", "memo", "note"]
    skip_targets = ["名称", "编码", "name", "code"]

    for field in fields:
        field_type = field.get("type", "input")
        if field_type not in ("input", "textarea"):
            continue
        label = field.get("label", "")
        selector = field.get("selector")
        input_type = field.get("inputType", "text")
        if input_type not in ("text", ""):
            continue

        # 跳过名称/编码字段
        if any(kw in label for kw in skip_targets):
            continue

        is_editable = (any(kw in label.lower() for kw in edit_targets)
                      or field_type == "textarea")

        if is_editable and selector:
            try:
                new_val = f"auto_edited_{ts}"
                await page.fill(selector, "", timeout=3000)
                await page.fill(selector, new_val, timeout=3000)
                LOG.info(f"    已修改字段 '{label}' → '{new_val}'")
                modified = True
                break
            except Exception as e:
                LOG.debug(f"    修改字段 '{label}' 失败: {e}")
                continue

    if not modified:
        LOG.warning("    未修改任何字段，关闭弹窗")
        await close_dialog(page)
        return False

    # 记录提交前请求数
    calls_before = len(interceptor.calls) if interceptor else 0

    # 提交编辑表单
    LOG.info("    提交编辑表单...")

    # 使用 JavaScript 点击对话框内的保存按钮（容忍空格，如 "确 定"）
    submitted = await page.evaluate("""() => {
        const submitTexts = ['保存', '确定', '确认', '更新', '提交', 'OK'];
        const dialogs = document.querySelectorAll('.el-dialog__wrapper, .el-drawer');
        const visibleDialogs = Array.from(dialogs).filter(d => d.style.display !== 'none');

        for (const dlg of visibleDialogs) {
            const buttons = dlg.querySelectorAll('button');
            for (const btn of buttons) {
                const text = btn.textContent.trim().replace(/\\s+/g, '');
                if (submitTexts.some(t => text === t) && btn.offsetWidth > 0) {
                    btn.click();
                    return text;
                }
            }
        }
        return null;
    }""")

    if submitted:
        LOG.info(f"    已点击对话框内按钮: {submitted}")
        await page.wait_for_timeout(2000)
    else:
        # 回退到 submit_form_v2
        result = await form_filler.submit_form_v2()
        LOG.info(f"    提交结果: {result}")
        submitted = result != "not_found"

    if submitted:
        # 等待 update API 或页面导航完成
        LOG.info("    等待 update API 响应...")
        await page.wait_for_timeout(3000)

    # 调试: 打印提交后的所有新请求
    if interceptor:
        new_calls = interceptor.calls[calls_before:]
        LOG.info(f"    编辑后新增 {len(new_calls)} 个请求:")
        for i, call in enumerate(new_calls):
            method = call.get("method", "").upper()
            path = call.get("pathname", "")
            status = call.get("status", "")
            LOG.info(f"      [{i}] {method} {path[:80]} (status={status})")

    # 检查是否检测到 PUT/PATCH 请求
    list_patterns = ["/list", "/page", "/search", "/query", "/menu", "/theme",
                    "/dictionary", "/check", "/validate", "/current-user"]
    if interceptor:
        new_calls = interceptor.calls[calls_before:]
        for call in new_calls:
            method = call.get("method", "").upper()
            path = call.get("pathname", "")
            if any(skip in path for skip in list_patterns):
                continue
            if method in ("PUT", "PATCH"):
                LOG.info(f"    ✅ 检测到 update 请求: {method} {path[:60]}")
                return True
            if method == "POST":
                LOG.info(f"    ✅ 检测到 update 请求(POST): {method} {path[:60]}")
                return True

    LOG.warning("    未检测到 update API 请求")
    # 关闭弹窗
    await close_dialog(page)
    return False


async def _discover_and_click_dropdowns(page, ctx_holder: dict,
                                        interceptor: RequestInterceptor,
                                        button_driver: ButtonDriver):
    """展开并点击工具栏级下拉菜单"""
    for menu_text in ("更多", "操作", "Actions"):
        has_btn = await button_driver.has_non_nav_button(menu_text)
        if not has_btn:
            continue

        url_before = page.url
        await button_driver.click_by_text(menu_text)
        await wait_for_dropdown(page, timeout=5000)

        # 检查是否跳转到导航
        if page.url != url_before:
            LOG.warning(f"    ⚠️ {menu_text} 触发了页面跳转，回退")
            try:
                await page.go_back()
                await wait_for_navigation_complete(page, timeout=10000)
            except Exception as e:
                LOG.warning(f"回退导航失败: {e}")
            continue

        # 获取下拉项
        items = await button_driver.get_dropdown_items()
        LOG.info(f"    {menu_text} → {items}")

        for item in items:
            ctx_holder["v"] = f"dropdown:{menu_text}:{item}"
            interceptor.set_context(ctx_holder["v"])

            await button_driver.click_by_text(item)
            await wait_for_api_response(page, timeout=5000)
            await button_driver.close_all_dialogs()
            await wait_for_dialog_dismissed(page, timeout=5000)

            # 重新展开菜单
            await button_driver.click_by_text(menu_text)
            await wait_for_dropdown(page, timeout=5000)

        # 关闭菜单
        try:
            await page.keyboard.press("Escape")
        except Exception as e:
            LOG.debug(f"关闭菜单失败: {e}")
        await page.wait_for_timeout(500)
