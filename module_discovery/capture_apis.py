"""
capture_apis.py — Stage 2: API 捕获（回放模式）

职责：回放 Stage 1 已验证的操作流程，同时拦截网络请求。

架构：
  - request_interceptor: HTTP 请求/响应拦截与收集
  - button_driver: 按钮点击与交互驱动
  - endpoint_classifier: API 端点分类与去重

Stage 1 已验证所有操作可成功（validated_operations），Stage 2 只需：
  1. 按 CRUD 顺序回放每个操作
  2. 用 Stage 1 的 fill_data 填充表单
  3. 全程开启网络拦截器捕获 API

不重新探索、不重试、不做 UI 探测。
"""

import time
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
    Stage 2 入口函数：回放 Stage 1 已验证操作 + 网络拦截

    Args:
        page: Playwright Page 对象
        ui_result: Stage 1 的 UI 探测结果（含 validated_operations）
        base_url: 目标系统基础 URL
        target_url: 目标页面 URL
        project_dir: 项目目录（用于调试工件输出）
        module_name: 模块名称（用于调试工件命名）
        capture_all_mode: 是否捕获所有请求
        api_path_prefix: API 路径前缀

    Returns:
        dict: 捕获结果
    """
    LOG.info(f"[Stage 2] 开始回放模式捕获: {target_url}")

    try:
        result = await _capture_by_replay(page, ui_result, base_url, target_url,
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


async def _capture_by_replay(page, ui_result: dict, base_url: str, target_url: str,
                              project_dir: Path = None, module_name: str = "",
                              capture_all_mode: bool = False,
                              api_path_prefix: str = None) -> dict:
    """通过回放 Stage 1 已验证操作来捕获 API"""
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

    # 3. 从 validated_operations 获取已验证的操作序列
    validated = ui_result.get("validated_operations", {})
    if not validated:
        LOG.error("validated_operations 为空，无法回放")
        return {"error": "no_validated_operations", "classified": {}, "stats": {}}

    LOG.info(f"已验证操作序列: {list(validated.keys())}")

    # 4. 按 CRUD 执行顺序回放
    created_marker = None

    for action in const.CRUD_EXECUTION_ORDER:
        op_data = validated.get(action)
        if not op_data:
            LOG.debug(f"跳过 {action}: 无验证数据")
            continue

        LOG.info(f"▶ 回放 {action}")
        interceptor.set_context(f"replay:{action}")

        try:
            if action == "create":
                # 创建操作：使用 Stage 1 的 fill_data
                marker = await _replay_create(page, op_data, button_driver)
                if marker:
                    created_marker = marker
                    LOG.info(f"  创建成功，marker: {marker}")
                    await wait_for_table_ready(page, timeout=10000)

            elif action in ("query", "detail", "update", "delete",
                           "lock", "unlock", "reset", "authorize",
                           "migrate", "export", "import", "batch",
                           "approve", "execute"):
                # 行级操作：需要 created_marker
                if created_marker:
                    await _replay_row_operation(page, action, op_data, created_marker, button_driver)
                else:
                    LOG.debug(f"  跳过 {action}: 无 created_marker")

            else:
                # 其他操作（如工具栏按钮）
                await _replay_toolbar_operation(page, action, op_data, button_driver)

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

    LOG.info(f"[Stage 2] 回放完成: {result.get('stats', {})}")

    return result


async def _replay_create(page, op_data: dict, button_driver: ButtonDriver) -> str | None:
    """回放创建操作，返回创建的 marker"""
    selectors = op_data.get("selectors", {})
    fill_rules = op_data.get("fill_rules", {})
    fill_data = op_data.get("fill_data", {})  # 兜底
    form_fields = op_data.get("form_fields", [])  # Stage 1 扫描的表单字段
    trigger_selector = selectors.get("trigger")
    submit_selector = selectors.get("submit")

    if not trigger_selector:
        LOG.warning("  无 trigger selector")
        return None

    if not submit_selector:
        LOG.warning("  无 submit selector，跳过回放")
        return None

    if not form_fields:
        LOG.warning("  无 form_fields，跳过回放")
        return None

    # 1. 点击触发按钮
    LOG.debug(f"  点击触发: {trigger_selector}")
    clicked = await button_driver.click_by_text(trigger_selector)
    if not clicked:
        LOG.warning(f"  无法点击: {trigger_selector}")
        return None

    # 2. 等待表单出现
    await wait_for_dialog(page, timeout=8000)
    await page.wait_for_timeout(1000)

    # 3. 使用 Stage 1 的 form_fields 和 fill_rules 生成新值并填充
    from .form_filler import apply_fill_rule
    filled_count = 0
    marker_value = None

    for field in form_fields:
        field_name = field.get("name")
        selector = field.get("selector")

        if not field_name or not selector:
            continue

        # 优先使用 fill_rules 生成新值
        rule = fill_rules.get(field_name)
        if rule:
            value = apply_fill_rule(rule)
        else:
            # 兜底：使用 Stage 1 的 fill_data
            value = fill_data.get(field_name)

        if value is None:
            continue

        try:
            await page.fill(selector, str(value), timeout=3000)
            filled_count += 1
            LOG.debug(f"    填充: {field_name} = {value}")

            # 记录 marker（通常是 name/username 字段）
            if not marker_value and field_name in ["name", "username", "名称", "用户名"]:
                marker_value = value
        except Exception as e:
            LOG.debug(f"    填充失败: {field_name}: {e}")

    LOG.info(f"  已填充 {filled_count}/{len(form_fields)} 个字段")

    # 4. 点击提交按钮（使用 Stage 1 记录的 selector）
    LOG.debug(f"  点击提交: {submit_selector}")
    submitted = await button_driver.click_by_text(submit_selector)

    if not submitted:
        LOG.warning("  无法提交")
        return None

    # 5. 等待 API 响应
    await wait_for_api_response(page, timeout=10000)
    await wait_for_loading_complete(page)

    # 6. 返回 marker（在填充时已记录）
    return marker_value


async def _replay_row_operation(page, action: str, op_data: dict,
                                 marker: str, button_driver: ButtonDriver):
    """回放行级操作（query/update/delete 等）"""
    selectors = op_data.get("selectors", {})
    row_selector = selectors.get("row_selector")

    if not row_selector:
        LOG.debug(f"  {action}: 无 row_selector")
        return

    # 1. 定位到目标行
    row = await button_driver.find_data_row(marker)
    if not row:
        LOG.warning(f"  {action}: 无法定位行 {marker}")
        return

    # 2. 点击行操作按钮
    LOG.debug(f"  {action}: 点击 {row_selector}")
    clicked = await button_driver.click_row_button_v2(row, row_selector)
    if not clicked:
        LOG.warning(f"  {action}: 无法点击 {row_selector}")
        return

    # 3. 等待 API 响应
    await wait_for_api_response(page, timeout=8000)
    await page.wait_for_timeout(500)

    # 4. 处理弹窗（如确认删除）- 使用 Stage 1 记录的确认按钮
    if action == "delete":
        confirm_selector = selectors.get("confirm")
        if confirm_selector:
            LOG.debug(f"  确认删除: {confirm_selector}")
            confirmed = await button_driver.click_by_text(confirm_selector)
            if confirmed:
                await wait_for_api_response(page, timeout=8000)
        else:
            LOG.warning(f"  {action}: 无 confirm_selector，跳过确认")

    LOG.info(f"  {action}: 完成")


async def _replay_toolbar_operation(page, action: str, op_data: dict,
                                     button_driver: ButtonDriver):
    """回放工具栏操作"""
    selectors = op_data.get("selectors", {})
    trigger_selector = selectors.get("trigger")

    if not trigger_selector:
        LOG.debug(f"  {action}: 无 trigger selector")
        return

    # 1. 点击触发按钮
    LOG.debug(f"  {action}: 点击 {trigger_selector}")
    clicked = await button_driver.click_by_text(trigger_selector)
    if not clicked:
        LOG.warning(f"  {action}: 无法点击 {trigger_selector}")
        return

    # 2. 等待 API 响应
    await wait_for_api_response(page, timeout=8000)
    await page.wait_for_timeout(500)

    LOG.info(f"  {action}: 完成")
