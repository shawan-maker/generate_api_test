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
import re
from pathlib import Path
from .request_interceptor import RequestInterceptor
from .replay.button_driver import ButtonDriver
from .endpoint_classifier import EndpointClassifier
from .replay.replay_engine import replay_from_playbook
from .replay.wait_helpers import (
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


async def _cleanup_after_operation(page, ui_framework: str = "element-ui"):
    """操作后清理：关闭残留弹窗，确保回到列表页状态。

    Stage 2 回放时，失败操作（如 import 打开上传对话框）可能留下未关闭的弹窗，
    阻挡后续操作的点击。此函数在每个操作回放后调用。
    """
    from . import const

    # 1. 按 Escape 尝试关闭
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)

    # 2. JS 清理：关闭所有可见的对话框/抽屉/消息框
    try:
        selectors = const.get_ui_selectors(ui_framework)
        cleanup_scripts = selectors.get("cleanup", {})

        # 执行各类型的清理脚本
        for script_key in ["dialog_close_js", "messagebox_cancel_js", "drawer_close_js"]:
            script = cleanup_scripts.get(script_key)
            if script:
                await page.evaluate(f"() => {{ {script} }}")
    except Exception as e:
        LOG.debug(f"UI 选择器清理失败: {e}")

    await page.wait_for_timeout(500)

    # 3. 再次按 Escape（兜底）
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)


async def _capture_by_playbook(page, playbook: dict, base_url: str, target_url: str,
                               project_dir: Path = None, module_name: str = "",
                               capture_all_mode: bool = False,
                               api_path_prefix: str = None,
                               ui_framework: str = "element-ui") -> dict:
    """通过回放 playbook 来捕获 API"""
    # 加载知识库配置
    from .request_interceptor import load_kb
    kb_config = load_kb()

    # 初始化子模块
    interceptor = RequestInterceptor(page, base_url, target_url,
                                     capture_all_mode=capture_all_mode,
                                     api_path_prefix=api_path_prefix)
    button_driver = ButtonDriver(page, ui_framework)
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

        # 操作间清理：关闭残留弹窗，确保回到列表页
        try:
            await _cleanup_after_operation(page, ui_framework)
        except Exception as e:
            LOG.debug(f"  操作间清理异常（不影响后续）: {e}")

        continue

    # 5. 收集拦截数据
    calls, samples, gates, sid = interceptor.collect()

    # 6. 分类端点
    result = classifier.deduplicate(calls, samples)
    result["permission_gates"] = gates
    if sid:
        result["sid"] = sid

    LOG.info(f"[Stage 2] Playbook 回放完成: {result.get('stats', {})}")

    # 新增：识别前置 API 候选（Phase A）
    # 排除业务操作端点，保留其余端点作为前置 API 候选
    business_pathnames = set()
    for cat in ("create", "update", "delete", "execute",
                "lock", "unlock", "reset", "import", "export"):
        for ep in result.get("classified", {}).get(cat, []):
            business_pathnames.add(ep.get("pathname", ""))
    # 对 query/detail 类别：排除"模块自身资源"的查询，保留"辅助资源"查询
    # 判断依据：query 端点路径是否包含 create/delete 端点的资源路径段
    # 例如：create=/users → query=/tenants/users 包含 users → 排除（主查询）
    #       而 /policies/list 不包含 users → 保留（前置 API 候选）
    create_paths = [ep["pathname"] for ep in result.get("classified", {}).get("create", [])]
    for ep in result.get("classified", {}).get("query", []):
        p = ep.get("pathname", "")
        is_main_query = False
        for cp in create_paths:
            # 提取 create 路径的最后一段资源名
            resource_seg = cp.rstrip("/").split("/")[-1] if cp else ""
            if resource_seg and resource_seg in p:
                is_main_query = True
                break
        # detail 类别也类似排除
        if is_main_query:
            business_pathnames.add(p)
    for ep in result.get("classified", {}).get("detail", []):
        p = ep.get("pathname", "")
        for cp in create_paths:
            resource_seg = cp.rstrip("/").split("/")[-1] if cp else ""
            if resource_seg and resource_seg in p:
                business_pathnames.add(p)
                break
    pre_api_candidates = _identify_pre_api_candidates(calls, samples, business_pathnames)
    result["pre_api_candidates"] = pre_api_candidates
    LOG.info(f"[Phase A] 识别到 {len(pre_api_candidates)} 个前置 API 候选")

    return result


# ========== Phase A: 前置 API 候选识别 ==========

# 注意：不再使用硬编码模式匹配前置 API！
# 正确做法：Stage 2 捕获所有 GET API 作为候选，Stage 3 通过数据反向追踪决定哪些是前置 API。
# 这样任何项目的任何 API 都能被正确识别，无需维护路径模式列表。

_PRE_API_PATTERNS = []  # 保留变量但清空，避免未来误用


def _identify_pre_api_candidates(calls: list, samples: dict,
                                  classified_pathnames: set = None) -> list:
    """
    收集非业务 API 作为前置 API 候选。

    排除已分类为核心 CRUD 的 API，将剩余 API（有有效 JSON 响应的）
    都作为候选传给 Stage 3，由 trace_pre_api_dependencies() 根据业务请求
    数据反向追踪来决定哪些是真正的前置 API。

    Args:
        calls: RequestInterceptor 捕获的 API 调用列表
        samples: 响应样本字典 {pathname: [{status, body}, ...]}
        classified_pathnames: 已分类为核心 CRUD API 的路径集合（排除用）

    Returns:
        前置 API 候选列表
    """
    classified_pathnames = classified_pathnames or set()
    candidates = []
    seen_pathnames = set()

    for call in calls:
        pathname = call.get('pathname', '')

        # 去重：同一路径只处理一次
        if pathname in seen_pathnames:
            continue

        # 排除已分类为核心 CRUD 的业务 API
        if pathname in classified_pathnames:
            continue

        # 获取响应样本
        sample_list = samples.get(pathname, [])
        if not sample_list:
            continue

        sample = sample_list[0]
        body_text = sample.get('body', '')
        if not body_text:
            continue

        # 解析响应，提取可提取字段
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else body_text
            if not isinstance(body, dict):
                continue
            extracted_fields = _extract_available_fields(body)
        except Exception as e:
            LOG.debug(f"  [Phase A] {pathname} JSON 解析失败: {e}")
            continue

        # 跳过没有有效字段的 API
        if not extracted_fields:
            continue

        # 生成 API ID（使用路径最后一段，转换连字符为下划线）
        last_segment = pathname.rstrip('/').split('/')[-1]
        api_id = last_segment.replace('-', '_').replace(' ', '_')

        # 生成人类可读名称
        api_name = _generate_api_name(pathname)

        candidates.append({
            'name': api_name,
            'id': api_id,
            'method': call.get('method', 'GET'),
            'pathname': pathname,
            'response_sample': {'response_body': body_text},
            'extracted_fields': extracted_fields,
            'depends_on': []
        })

        seen_pathnames.add(pathname)
        LOG.debug(f"  [Phase A] 收集候选: {api_name} ({pathname})")

    LOG.info(f"  [Phase A] 共收集 {len(candidates)} 个 GET API 候选，将由 Stage 3 数据追踪筛选")
    return candidates


def _extract_available_fields(body: dict, prefix: str = '') -> list:
    """
    递归提取 JSON 响应中的可提取字段。

    Args:
        body: JSON 响应体
        prefix: 路径前缀（用于递归）

    Returns:
        字段列表，每项包含 name 和 path
        例如：[
            {'name': 'entity_tenantId', 'path': 'entity.tenantId'},
            {'name': 'entity_list_0_id', 'path': 'entity.list[0].id'}
        ]
    """
    fields = []

    def _walk(obj, current_path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key

                # 提取基本类型字段（字符串、整数、布尔等）
                if isinstance(value, (str, int, float, bool)) and value is not None:
                    # 生成字段名：将路径中的特殊字符替换为下划线
                    field_name = new_path.replace('.', '_').replace('[', '_').replace(']', '')
                    fields.append({
                        'name': field_name,
                        'path': new_path,
                        'type': type(value).__name__
                    })

                # 递归处理嵌套对象
                if isinstance(value, (dict, list)):
                    _walk(value, new_path)

        elif isinstance(obj, list):
            # 提取列表第一个元素的字段（用于列表响应）
            if len(obj) > 0:
                _walk(obj[0], f"{current_path}[0]")

    _walk(body, prefix)
    return fields


def _generate_api_name(pathname: str) -> str:
    """根据路径生成人类可读的 API 名称"""
    mapping = {
        'current-user': '获取当前用户信息',
        'policies': '获取策略列表',
        'roles': '获取角色列表',
        'departments': '获取部门列表',
        'users': '获取用户列表',
        'tenants': '获取租户信息',
        'organizations': '获取组织列表',
        'dict': '获取字典',
        'config': '获取配置',
    }

    last_segment = pathname.rstrip('/').split('/')[-1]
    return mapping.get(last_segment, f'前置 API: {last_segment}')
