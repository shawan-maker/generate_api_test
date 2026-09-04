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
from .replay_engine import replay_from_playbook
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
