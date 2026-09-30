"""
pre_api_tracer.py — Pre-API dependency chain tracing.

Traces the dependency chain for pre-APIs: identifies which pre-APIs are needed
to supply context values for core CRUD operations, resolves chained dependencies
(pre-APIs that themselves depend on other pre-APIs), and produces a topologically
sorted execution order.

Extracted from analyze_flow.py to keep Stage 3 modular.
"""

import json
import logging

from core.discovery import const
from core.discovery.stage3.field_classifier import (
    ValueIndex,
    build_value_index,
    classify_fields_recursive,
    is_noise_value,
    _get_nested_value,
)
from core.discovery.stage3.value_chain import _parse_body

LOG = logging.getLogger("pre_api_tracer")


def trace_pre_api_dependencies(core_apis: dict, response_samples: dict,
                               pre_api_candidates: list, context_fields: dict,
                               id_field_details: dict,
                               id_producer: str = None,
                               value_index: 'ValueIndex' = None) -> dict:
    """
    前置 API 依赖链追踪（v2.0 使用 5 角色分类引擎）。

    使用新的值索引驱动的分类系统，一次完成字段分类和前置 API 识别。

    Args:
        core_apis: 核心 CRUD API 字典 {category: [endpoints]}
        response_samples: 响应样本字典
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        context_fields: 上下文字段配置
        id_field_details: ID 字段详情
        id_producer: ID 生产者操作名
        value_index: 外部传入的值索引（与 build_manifest 共享同一份）

    Returns:
        {
            'pre_apis': [...],  # 排序后的前置 API 列表
            'field_resolutions': {...}  # 字段解析映射（用于 query_params）
        }
    """
    LOG.info(f"[Phase B] 开始前置 API 依赖链追踪 (v2.0)...")

    # 1. 获取 create body 样本和响应
    create_body_sample = None
    create_response_sample = None
    if id_producer and id_producer in core_apis:
        create_ep = core_apis[id_producer][0]
        create_body_sample = _parse_body(create_ep)
        # 获取 create 响应
        create_pn = create_ep.get("pathname", "")
        create_resp_samples = response_samples.get(create_pn, [])
        # ★ 按 context 筛选创建响应样本
        op_context = f"replay:{id_producer}"
        matched_samples = [s for s in create_resp_samples if s.get("context") == op_context]
        if not matched_samples:
            matched_samples = create_resp_samples
        if matched_samples:
            try:
                body_text = matched_samples[0].get("body", "{}")
                create_response_sample = json.loads(body_text) if isinstance(body_text, str) else body_text
            except Exception:
                pass

    # 2. 使用外部传入的 value_index（与 build_manifest 共享），或内部构建（向后兼容）
    if value_index is None:
        value_index = build_value_index(
            pre_api_candidates=pre_api_candidates,
            create_body_sample=create_body_sample,
            create_response_sample=create_response_sample,
            context_fields=context_fields,
        )

    # 3. 对每个操作进行分类，收集 used_apis
    used_apis = {}
    field_resolutions = {}
    for action, endpoints in core_apis.items():
        if not endpoints:
            continue
        ep = endpoints[0]
        body_sample = _parse_body(ep)
        if not body_sample or not isinstance(body_sample, dict):
            continue

        # 使用新的 5 角色分类（递归，含嵌套 dict 字段）
        # 对 id_producer 操作排除 create_body（与 build_manifest 一致）
        exclude = {"create_body"} if action == id_producer else None
        field_roles = classify_fields_recursive(
            body_sample, value_index, create_body_sample, current_action=action,
            exclude_sources=exclude
        )

        # 收集 context 来源的上前 API
        for field_name, role_info in field_roles.items():
            if role_info.get("role") == "context":
                source = role_info.get("source", "")
                if "." in source:
                    api_id = source.split(".", 1)[0]
                    # 跳过 create_body 和 create（不是真正的前置 API）
                    if api_id not in ("create_body", "create"):
                        # 优先从 value_index 获取 api_info
                        nested_val = _get_nested_value(body_sample, field_name)
                        entry = value_index.lookup(nested_val if nested_val is not None else "")
                        if entry and entry.get("source_api") == api_id:
                            api_info = entry.get("api_info")
                            if api_info:
                                used_apis[api_id] = api_info
                        else:
                            # 回退：直接从 pre_api_candidates 按 api_id 查找
                            for candidate in pre_api_candidates:
                                if candidate.get("id") == api_id:
                                    used_apis[api_id] = candidate
                                    break

        # 链式依赖追踪
        resolve_chain(
            body_sample, field_roles, value_index, used_apis,
            current_action=action
        )

        # 构建 field_resolutions（用于 verify 步骤的 query_params）
        for field_name, role_info in field_roles.items():
            if role_info.get("role") == "context":
                source = role_info.get("source", "")
                if source and not source.startswith(("create_body.", "create.")):
                    key = f"{action}.{field_name}"
                    field_resolutions[key] = {
                        "source": source,
                        "strategy": "exact_value_match",
                    }

    LOG.info(f"  收集到 {len(used_apis)} 个前置 API")

    # 4. 拓扑排序
    pre_apis = _topological_sort_pre_apis(used_apis)

    # 5. 添加 context_fields 提取（确保 query_params 引用的字段被提取）
    if context_fields:
        for field_name, field_config in context_fields.items():
            path = field_config.get('path', '')
            if not path:
                continue

            # 检查是否已有字段提取
            already_extracted = False
            for pre_api in pre_apis:
                for extract in pre_api.get('extracts', []):
                    if extract.get('name') == field_name or extract.get('path') == path:
                        already_extracted = True
                        break
                if already_extracted:
                    break

            if already_extracted:
                continue

            # 找到能提供此字段的 pre_api（根据 path 前缀匹配）
            path_prefix = path.split('.')[0]
            target_api = None
            for pre_api in pre_apis:
                body_text = pre_api.get('response_sample', {}).get('response_body', '')
                if body_text:
                    try:
                        body = json.loads(body_text) if isinstance(body_text, str) else body_text
                        if path_prefix in body:
                            target_api = pre_api
                            break
                    except Exception:
                        continue

            if target_api:
                if 'extracts' not in target_api:
                    target_api['extracts'] = []
                target_api['extracts'].append({
                    'name': field_name,
                    'path': path
                })
                LOG.info(f"  添加 context_field 提取: {field_name} -> {path} (from {target_api.get('id')})")

    return {
        'pre_apis': pre_apis,
        'field_resolutions': field_resolutions
    }


def resolve_chain(body_sample: dict, field_roles: dict,
                  value_index: ValueIndex, used_apis: dict,
                  current_action: str = "",
                  seen_apis: set = None, depth: int = 0,
                  max_depth: int = 5):
    """链式依赖追踪：确保 context 来源的前置 API 本身参数也被追踪。

    对每个 role=context 的字段，检查其来源 API 是否有自身的 query_params/body 参数
    需要从前置 API 获取。递归追踪直到无参数或达到最大深度。

    Args:
        body_sample: 当前操作的请求体
        field_roles: classify_fields 的输出（会被原地修改以更新 source）
        value_index: 全局值索引
        used_apis: 已使用的前置 API 集合（会被原地修改）
        current_action: 当前操作名
        seen_apis: 当前链中已访问的 API（防止循环）
        depth: 当前递归深度
        max_depth: 最大链深度
    """
    if seen_apis is None:
        seen_apis = set()
    if depth >= max_depth:
        LOG.warning(f"  [resolve_chain] 达到最大深度 {max_depth}，停止")
        return

    for key, role_info in field_roles.items():
        if role_info.get("role") != "context":
            continue
        source = role_info.get("source", "")
        if "." not in source:
            continue
        api_id = source.split(".", 1)[0]
        # 跳过 create_body 和 create（不是真正的前置 API）
        if api_id in ("create_body", "create"):
            continue
        if api_id in seen_apis:
            continue

        # 找到来源 API 的信息
        nested_val = _get_nested_value(body_sample, key)
        entry = value_index.lookup(
            nested_val if nested_val is not None else "",
            request_field_name=key,
            current_action=current_action,
        )
        if not entry or entry.get("source_api") != api_id:
            continue

        api_info = entry.get("api_info")
        if not api_info:
            continue

        # 记录使用的前置 API
        used_apis[api_id] = api_info
        new_seen = seen_apis | {api_id}

        # 检查来源 API 本身的 query_params 是否需要追踪
        source_api_params = api_info.get("query_params", {})
        if not source_api_params:
            # 从 query_params_samples 获取
            qps = api_info.get("query_params_samples", [])
            if qps and isinstance(qps, list) and len(qps) > 0:
                source_api_params = qps[0] if isinstance(qps[0], dict) else {}

        for param_name, param_value in source_api_params.items():
            if is_noise_value(param_value):
                continue
            sub_match = value_index.lookup(
                param_value, request_field_name=param_name,
                current_action=current_action,
            )
            if sub_match and sub_match.get("source_api") != api_id:
                sub_api_id = sub_match["source_api"]
                if sub_api_id not in ("create_body", "create") and sub_api_id not in new_seen:
                    used_apis[sub_api_id] = sub_match.get("api_info")
                    LOG.debug(f"    [resolve_chain] {api_id}.{param_name} ← {sub_match['source_path']}")


def _topological_sort_pre_apis(used_apis: dict) -> list:
    """
    对前置 API 进行拓扑排序。

    Args:
        used_apis: {api_id: api_info} 字典

    Returns:
        排序后的前置 API 列表
    """
    # 构建依赖图
    graph = {}
    for api_id, api_info in used_apis.items():
        graph[api_id] = api_info.get('depends_on', [])

    # 拓扑排序（Kahn 算法）
    in_degree = {node: 0 for node in graph}
    for node in graph:
        for dep in graph[node]:
            if dep in in_degree:
                in_degree[dep] += 1

    queue = [node for node in in_degree if in_degree[node] == 0]
    sorted_apis = []

    while queue:
        node = queue.pop(0)
        sorted_apis.append(used_apis[node])

        for dep in graph.get(node, []):
            if dep in in_degree:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

    return sorted_apis
