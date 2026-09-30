"""
value_chain.py — 三原则值追溯引擎与依赖推导

核心功能：
  - build_value_chain: 构建全量值关联链（三原则驱动）
  - _derive_dependencies: 从 ValueChain 中提取数据依赖关系
  - _derive_state_rules: 从响应样本中推导状态断言规则
  - _discover_response_contract: 从响应样本自动发现完整的响应约定
"""

import json
import logging
from typing import Optional
from collections import defaultdict
from core.discovery import const

LOG = logging.getLogger("value_chain")


# ========== ValueChain: 三原则值匹配引擎 ==========


def _compute_static_segments(all_calls: list) -> set:
    """从 all_calls 统计 URL 路径中的静态段（数据驱动，不硬编码）。

    原理：静态段（框架前缀如 v1/api、资源名如 groups/users）在多数请求中重复出现，
    而动态 ID 每次请求都不同，不可能高频出现。

    Args:
        all_calls: 完整调用序列

    Returns:
        set: 静态路径段集合
    """
    # 通用框架保留词（兜底，calls 为空时至少能过滤版本号）
    universal_static = {"api", "v1", "v2", "v3", "v4"}

    if not all_calls:
        return universal_static

    # 统计每个路径段出现在多少个不同的 pathname 中
    segment_pathname_count = defaultdict(set)
    for call in all_calls:
        pathname = call.get("pathname", "")
        segments = pathname.strip("/").split("/")
        for seg in segments:
            if seg:
                segment_pathname_count[seg].add(pathname)

    # 出现在 ≥50% 不同 pathname 中的段 → 静态段
    total_pathnames = len(set(c.get("pathname", "") for c in all_calls))
    threshold = max(2, total_pathnames * 0.5)

    static = set(universal_static)
    for seg, pathnames in segment_pathname_count.items():
        if len(pathnames) >= threshold:
            static.add(seg)

    LOG.debug(f"  [ValueChain] 静态路径段（数据驱动，共 {len(static)} 个）: "
              f"{sorted(static)[:15]}...")
    return static


def build_value_chain(core_apis: dict, response_samples: dict,
                      all_calls: list) -> dict:
    """
    构建全量值关联链（三原则驱动）。

    对每个操作的请求（URL路径段 + query参数 + body字段），
    找出其中的动态值，通过三原则逐级追溯到源头：
      原则1：根据值匹配 — 匹配前面所有接口响应中值相同的字段
      原则2：时间最近原则 — 多个响应有相同值时取时间最近的
      原则3：多级匹配 — 如果那个响应的请求中也有动态值，继续递归追溯

    Args:
        core_apis: 核心 API 字典 {操作名: [端点数据]}
        response_samples: 响应样本 {pathname: [{status, body, ts, context, method}, ...]}
        all_calls: 完整调用序列 [{method, pathname, body, context, ts, query_params}, ...]

    Returns:
        {
            "chains": {"<action>.<field>": {...}, ...},
            "id_producer": "创建用户组" | None,
            "id_field_details": {...},
            "injections": {...},
        }
    """
    LOG.info("  [ValueChain] 构建全量值关联链...")

    # 1. 构建全局时间线：所有响应按时间排序
    all_responses = _flatten_responses(response_samples)
    all_responses.sort(key=lambda r: r.get("ts", 0))

    # 2. 构建全局请求时间线
    all_requests = sorted(all_calls, key=lambda c: c.get("ts", 0)) if all_calls else []

    # 3. 数据驱动：从 all_calls 统计静态路径段（替代硬编码）
    static_segments = _compute_static_segments(all_calls)

    # 4. 对每个操作，提取请求中的动态值并追溯
    chains = {}

    for action, endpoints in core_apis.items():
        if not endpoints:
            continue
        ep = endpoints[0]
        body_sample = _parse_body(ep)
        pathname = ep.get("pathname", "")
        query_params = ep.get("query_params", {}) or {}

        # 确定该操作的请求时间（用于"只看之前的响应"）
        op_ts = _get_operation_ts(action, all_requests, response_samples)

        # 从请求中提取动态值
        dynamic_values = _extract_dynamic_values(
            body=body_sample,
            query_params=query_params,
            pathname=pathname,
            static_segments=static_segments,
        )

        # 对每个动态值执行三原则追溯
        for field_name, value_str in dynamic_values.items():
            chain_key = f"{action}.{field_name}"
            trace_path = _trace_value(
                value_str=value_str,
                before_ts=op_ts,
                all_responses=all_responses,
                all_requests=all_requests,
                depth=0,
                max_depth=6,
                visited=set(),
                static_segments=static_segments,
            )

            if trace_path:
                ultimate = _get_ultimate_source(trace_path)
                chains[chain_key] = {
                    "value": value_str,
                    "field_name": field_name,
                    "action": action,
                    "trace_path": trace_path,
                    "ultimate_source": ultimate,
                }

    # 4. 从 chains 中提取 id_producer 和 id_field_details
    id_producer, id_field_details, injections = _extract_id_info_from_chains(
        chains, core_apis, response_samples
    )

    LOG.info(f"  [ValueChain] {len(chains)} 条关联链, "
             f"ID生产者={id_producer or '无'}, "
             f"ID字段={list(id_field_details.keys())}")

    return {
        "chains": chains,
        "id_producer": id_producer,
        "id_field_details": id_field_details,
        "injections": injections,
    }


def _get_ultimate_source(trace_path: list) -> dict:
    """从追溯链中提取最终来源（递归进入 sub_traces）。"""
    if not trace_path:
        return {}
    node = trace_path[-1]
    while node.get("sub_traces"):
        sub = node["sub_traces"]
        sub_path = sub.get("trace_path", [])
        if not sub_path:
            break
        node = sub_path[-1]
    return {
        "step": node.get("step", ""),
        "pathname": node.get("pathname", ""),
        "response_path": node.get("response_path", ""),
    }


def _flatten_responses(response_samples: dict) -> list:
    """将 {pathname: [sample, ...]} 展平为带 pathname 的列表。"""
    flat = []
    for pathname, samples in response_samples.items():
        for s in samples:
            flat.append({
                "pathname": pathname,
                "status": s.get("status"),
                "body": s.get("body", ""),
                "ts": s.get("ts", 0),
                "context": s.get("context", ""),
                "method": s.get("method", ""),
            })
    return flat


def _get_operation_ts(action: str, all_requests: list, response_samples: dict) -> float:
    """获取操作的近似时间戳。"""
    op_context = f"replay:{action}"
    for req in all_requests:
        if req.get("context") == op_context:
            return req.get("ts", float("inf"))
    for pathname, samples in response_samples.items():
        for s in samples:
            if s.get("context") == op_context:
                return s.get("ts", float("inf"))
    return float("inf")


def _trace_value(value_str: str, before_ts: float,
                 all_responses: list, all_requests: list,
                 depth: int = 0, max_depth: int = 6,
                 visited: set = None,
                 static_segments: set = None) -> list:
    """
    递归追溯一个值的来源（三原则核心）。

    原则1：根据值匹配 — 在前面所有响应中搜索该值
    原则2：时间最近 — 取时间最接近 before_ts 的响应（路径短优先）
    原则3：多级匹配 — 检查该响应对应的请求中是否有动态值，递归追溯

    Returns:
        trace_path: [{step, pathname, response_path, ts, sub_traces?}, ...]
    """
    if visited is None:
        visited = set()

    if depth >= max_depth or value_str in visited:
        return []

    visited.add(value_str)

    # 原则1：在前面所有响应中搜索该值
    candidates = []
    for resp in all_responses:
        if resp.get("ts", 0) >= before_ts:
            break
        paths = _find_value_in_response(resp["body"], value_str)
        if paths:
            candidates.append({
                "step": resp.get("context", "").replace("replay:", ""),
                "pathname": resp["pathname"],
                "paths": paths,
                "ts": resp.get("ts", 0),
                "method": resp.get("method", ""),
            })

    if not candidates:
        return []

    # 原则2：时间最早（首次产生者）+ 路径最短
    # 追溯到值的首次产生源，而非最近出现的位置
    candidates.sort(key=lambda c: c["ts"])
    best_candidate = candidates[0]
    best_path = min(best_candidate["paths"], key=len)

    trace_entry = {
        "step": best_candidate["step"],
        "pathname": best_candidate["pathname"],
        "response_path": best_path,
        "ts": best_candidate["ts"],
    }

    # 原则3：检查该响应对应的请求中是否有动态值
    matching_request = _find_request_for_response(
        resp_pathname=best_candidate["pathname"],
        resp_ts=best_candidate["ts"],
        all_requests=all_requests,
    )

    if matching_request:
        req_dynamic = _extract_dynamic_values(
            body=_parse_request_body(matching_request.get("body", "")),
            query_params=matching_request.get("query_params", {}),
            pathname=matching_request.get("pathname", ""),
            static_segments=static_segments,
        )

        for dv_name, dv_value in req_dynamic.items():
            if dv_value != value_str and _looks_like_identifier(dv_value):
                sub_trace = _trace_value(
                    value_str=dv_value,
                    before_ts=matching_request.get("ts", before_ts),
                    all_responses=all_responses,
                    all_requests=all_requests,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited=visited.copy(),
                    static_segments=static_segments,
                )
                if sub_trace:
                    trace_entry["sub_traces"] = {
                        "request_field": dv_name,
                        "request_value": dv_value,
                        "trace_path": sub_trace,
                    }
                    break

    return [trace_entry]


def _find_value_in_response(body_text: str, target_value: str) -> list:
    """在一个响应 body 中查找目标值的所有路径。"""
    if not body_text:
        return []
    try:
        body = json.loads(body_text) if isinstance(body_text, str) else body_text
    except Exception:
        return []

    paths = []
    _search_value_recursive(body, str(target_value), "", paths, max_depth=5)
    return paths


def _search_value_recursive(obj, target_str: str, path: str,
                            results: list, max_depth: int, _depth: int = 0):
    """递归搜索 JSON 中的目标值，收集所有匹配路径。"""
    if _depth >= max_depth:
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            if isinstance(value, (str, int, float)) and value:
                if str(value) == target_str:
                    results.append(current_path)
            elif isinstance(value, dict):
                _search_value_recursive(value, target_str, current_path,
                                        results, max_depth, _depth + 1)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        _search_value_recursive(item, target_str,
                                                f"{current_path}[{i}]",
                                                results, max_depth, _depth + 1)
                    elif isinstance(item, (str, int)) and str(item) == target_str:
                        results.append(f"{current_path}[{i}]")


def _extract_dynamic_values(body=None, query_params: dict = None,
                            pathname: str = "",
                            static_segments: set = None) -> dict:
    """
    从请求中提取所有动态值（ID 类字段）。

    排除已知的非动态字段（分页、固定值等），只保留看起来像标识符的值。

    Args:
        body: 请求体（dict 或 list）
        query_params: URL 查询参数
        pathname: URL 路径
        static_segments: 静态路径段集合（数据驱动，由 _compute_static_segments 生成）
    """
    values = {}

    if isinstance(body, dict):
        for k, v in body.items():
            if k in const.EXTRACT_EXCLUDE_KEYS:
                continue
            if isinstance(v, str) and _looks_like_identifier(v):
                values[k] = v
            elif isinstance(v, list) and v and isinstance(v[0], str):
                if _looks_like_identifier(v[0]):
                    values[k] = v[0]
    elif isinstance(body, list) and body:
        if isinstance(body[0], str) and _looks_like_identifier(body[0]):
            values["_array_item"] = body[0]

    for k, v in (query_params or {}).items():
        if k in const.EXTRACT_EXCLUDE_KEYS:
            continue
        if isinstance(v, str) and _looks_like_identifier(v):
            values[f"_query_{k}"] = v

    # 使用数据驱动的静态段集合（由 _compute_static_segments 生成）
    # 若未传入（向后兼容），回退到通用框架保留词
    if static_segments is None:
        static_segments = {"api", "v1", "v2", "v3", "v4"}
    segments = pathname.strip("/").split("/")
    for seg in segments:
        if seg and seg not in static_segments and _looks_like_identifier(seg):
            values[f"_path_{seg[:8]}"] = seg

    return values


def _find_request_for_response(resp_pathname: str, resp_ts: float,
                               all_requests: list) -> dict | None:
    """找到产生指定响应的请求（同一 pathname + 时间最接近且在响应之前）。"""
    best = None
    best_ts_diff = float("inf")

    for req in all_requests:
        req_ts = req.get("ts", 0)
        if req_ts >= resp_ts:
            continue
        if req.get("pathname") != resp_pathname:
            continue
        ts_diff = resp_ts - req_ts
        if ts_diff < best_ts_diff:
            best_ts_diff = ts_diff
            best = req

    return best


def _parse_request_body(body_text) -> dict | list | None:
    """解析请求体 JSON。"""
    if not body_text:
        return None
    if isinstance(body_text, (dict, list)):
        return body_text
    try:
        return json.loads(body_text)
    except Exception:
        return None


def _extract_id_info_from_chains(chains: dict, core_apis: dict, response_samples: dict = None) -> tuple:
    """
    从 ValueChain 中提取 id_producer、id_field_details、injections。

    核心原则：使用 trace_path[0]（值本身的即时来源），而非 ultimate_source
    （ultimate_source 递归跟踪 sub_traces，会追溯到请求中其他值的来源如 tenantId→init）。

    优先级：
    1. 识别 POST 操作作为 id_producer（创建操作）
    2. 在其响应字段中，优先选择名称为 'id' 或 'xxxId' 的字段
    注：上下文参数（如 tenantId）来自 pre-API，trace_path[0].step 不在 core_apis 中，
       由 L843 的 source_step not in core_apis 自动过滤，无需额外排除。

    Args:
        chains: ValueChain 追溯结果
        core_apis: 核心 API 字典
        response_samples: 响应样本（用于动态计算回退路径）
    """
    id_producer = None
    id_field_details = {}
    injections = {}

    # 计算动态回退路径（替代硬编码 "entity.id"）
    default_extract_path = f"entity.{const.DEFAULT_ID_FIELD}"  # 兜底
    if response_samples:
        envelope_keys = _discover_envelope_keys(response_samples)
        top_key = envelope_keys[0] if envelope_keys else "entity"
        default_extract_path = f"{top_key}.{const.DEFAULT_ID_FIELD}"

    # 策略 1：从 trace_path[0] 中识别 id_producer（值本身的即时来源）
    post_candidates = []
    for chain_key, chain_info in chains.items():
        tp = chain_info.get("trace_path", [])
        if not tp:
            continue
        first_node = tp[0]  # ★ 值本身被找到的位置（非 sub_traces 最深节点）
        source_step = first_node.get("step", "")
        source_path = first_node.get("response_path", "")
        field_name = source_path.split(".")[-1] if source_path else ""

        # 只考虑来自核心操作的响应（排除 init/前置API）
        if source_step not in core_apis:
            continue
        # 排除包含 "list" 的路径（列表查询响应中的值不是该操作产生的）
        if "list" in source_path:
            continue
        # 检查该操作是否为 POST（创建操作）
        ep = core_apis[source_step][0]
        if ep.get("method", "").upper() != "POST":
            continue
        # 优先选择名称包含 'id' 的字段
        if field_name and ("id" in field_name.lower()):
            post_candidates.append((source_step, field_name, chain_info, source_path))

    # 如果有多个候选，优先选择字段名为 'id' 的
    if post_candidates:
        post_candidates.sort(key=lambda x: (
            0 if x[1].lower() == "id" else
            1 if x[1].lower().endswith("id") else
            2
        ))
        best_action, best_field, best_chain, best_path = post_candidates[0]
        id_producer = best_action

        for action, field_name, chain_info, source_path in post_candidates:
            if action == id_producer and field_name not in id_field_details:
                id_field_details[field_name] = {
                    "source_action": id_producer,
                    "sample_value": chain_info["value"],
                    "path": source_path,
                }
    else:
        # 策略 2：退回到统计 trace_path[0] 引用次数的逻辑
        source_counts = {}
        for chain_key, chain_info in chains.items():
            tp = chain_info.get("trace_path", [])
            if not tp:
                continue
            first_node = tp[0]
            source_step = first_node.get("step", "")
            source_path = first_node.get("response_path", "")

            if source_step in core_apis and "list" not in source_path:
                source_counts[source_step] = source_counts.get(source_step, 0) + 1

        if source_counts:
            id_producer = max(source_counts, key=source_counts.get)

        if id_producer:
            for chain_key, chain_info in chains.items():
                tp = chain_info.get("trace_path", [])
                if not tp:
                    continue
                first_node = tp[0]
                if first_node.get("step") == id_producer:
                    response_path = first_node.get("response_path", "")
                    field_name = response_path.split(".")[-1] if response_path else ""
                    if field_name and field_name not in id_field_details:
                        id_field_details[field_name] = {
                            "source_action": id_producer,
                            "sample_value": chain_info["value"],
                            "path": response_path,
                        }

    for chain_key, chain_info in chains.items():
        action = chain_info.get("action", "")
        field_name = chain_info.get("field_name", "")

        if action == id_producer:
            continue

        # ★ 使用 trace_path[0]（值的即时来源），而非 ultimate_source
        tp = chain_info.get("trace_path", [])
        if tp:
            first_node = tp[0]
            if first_node.get("step") == id_producer:
                injections[field_name] = {
                    "source": id_producer,
                    "source_path": first_node.get("response_path", field_name),
                }
            elif field_name == "_array_item":
                injections["__array_items__"] = {
                    "source": id_producer or "unknown",
                    "source_path": first_node.get("response_path", default_extract_path),
                }

    return id_producer, id_field_details, injections


def _looks_like_identifier(value) -> bool:
    """判断值是否看起来像标识符（ID 类字段）。"""
    if not isinstance(value, (str, int)):
        return False

    value_str = str(value)

    if len(value_str) < 6:
        return False
    if value_str.isdigit() and len(value_str) < 8:
        return False
    if value_str.lower() in ("true", "false", "null", "0", "1"):
        return False
    if value_str.startswith(("http://", "https://", "/")):
        return False

    has_letter = any(c.isalpha() for c in value_str)
    has_digit = any(c.isdigit() for c in value_str)
    if has_letter and has_digit and len(value_str) >= 8:
        return True

    if value_str.isdigit() and len(value_str) >= 16:
        return True

    return False


def _find_last_write_op(core_apis):
    """从 core_apis 中找最后一个写操作的操作名。"""
    from core.discovery.stage3.api_classifier import _find_last_write_op as _impl
    return _impl(core_apis)


# ========== 依赖推导与状态规则 ==========


def _derive_dependencies(core_apis: dict, response_samples: dict,
                         operation_order: list,
                         value_chain: dict = None) -> dict:
    """
    Step 4: 从 ValueChain 中提取数据依赖关系。

    ValueChain 已由 build_value_chain 完成三原则追溯，
    这里只做结果转换。

    Args:
        core_apis: 核心 API 字典
        response_samples: 响应样本（向后兼容用）
        operation_order: 执行顺序列表
        value_chain: build_value_chain 的输出（核心输入）

    Returns:
        dict with:
        - id_producer: 产生 ID 的操作名
        - id_field_details: ID 字段详情
        - injections: ID 引用模式
    """
    if value_chain:
        return {
            "id_producer": value_chain.get("id_producer"),
            "id_field_details": value_chain.get("id_field_details", {}),
            "injections": value_chain.get("injections", {}),
            "value_chain": value_chain,
        }

    # 向后兼容：无 ValueChain 时返回空结果
    LOG.warning("  [Step 4] 无 ValueChain，返回空依赖")
    return {
        "id_producer": None,
        "id_field_details": {},
        "injections": {},
    }


def _derive_state_rules(core_apis: dict, response_samples: dict, id_producer: str = None) -> dict:
    """
    Step 5: 从响应样本中推导状态断言规则。

    跨 CRUD 类别对比状态字段的值变化规律：
    - create 后的 state 值 → "after_create"
    - lock 后的 state 值 → "after_lock"
    - unlock 后的 state 值 → "after_unlock"
    """
    state_field = None
    state_values = {}  # {crud_category: state_value}

    for category, endpoints in core_apis.items():
        if not endpoints:
            continue
        ep = endpoints[0]  # 只处理 main endpoint，避免 chain candidate 污染
        pn = ep["pathname"]
        samples = response_samples.get(pn, [])
        for s in samples[:1]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
            except Exception as e:
                LOG.debug(f"解析状态断言响应样本失败 ({pn}): {e}")
                continue
            entity = _extract_entity_with_fallback(body)
            if isinstance(entity, dict):
                vals = _find_state_value(entity)
                if vals:
                    if state_field is None:
                        state_field = vals["field"]
                    if vals["field"] == state_field:
                        if category not in state_values:
                            state_values[category] = vals["value"]
            elif isinstance(entity, list) and len(entity) > 0:
                # 实体是列表，检查第一个元素的状态
                first = entity[0]
                if isinstance(first, dict):
                    vals = _find_state_value(first)
                    if vals:
                        if state_field is None:
                            state_field = vals["field"]
                        if vals["field"] == state_field:
                            if category not in state_values:
                                state_values[category] = vals["value"]

    # 组合状态规则
    rules = {
        "state_field": state_field,
        "values_by_crud": state_values,
    }

    # 如果有 create 和 lock 状态值，推导完整的断言逻辑
    if id_producer and id_producer in state_values:
        rules["after_create"] = state_values[id_producer]
    # Find delete-like operation for after_delete assertion
    last_write_op = _find_last_write_op(core_apis)
    if last_write_op:
        for ep in core_apis.get(last_write_op, []):
            if ep.get("method") == "DELETE":
                rules["after_delete"] = "NOT_EXIST"
                break

    return rules


def _find_state_value(entity: dict) -> Optional[dict]:
    """在实体字典中查找状态字段。

    使用通用状态字段名（state/status/enabled/locked 等），
    不依赖特定语言的状态值列表。
    """
    # 通用状态字段名（英文为主，覆盖大多数 API 设计）
    _GENERIC_STATE_FIELDS = [
        "state", "status", "enabled", "locked", "frozen",
        "active", "disabled", "phase", "stage", "lifecycle",
        "instanceStatus", "serviceStatus", "runStatus",
    ]

    # TODO: 未来可从 KB 读取扩展的状态字段名
    # from .kb_loader import get_kb
    # kb = get_kb()
    # kb_state_fields = kb.get_state_field_names()
    # all_fields = kb_state_fields or _GENERIC_STATE_FIELDS
    all_fields = _GENERIC_STATE_FIELDS

    for fname in all_fields:
        if fname in entity:
            val = entity[fname]
            if isinstance(val, str) and val:
                return {"field": fname, "value": val}
            if isinstance(val, bool):
                return {"field": fname, "value": val}
    return None


def _extract_entity_with_fallback(body: dict, fallback_keys: list = None) -> any:
    """从响应体中提取实体数据（带信封键回退）。

    优先使用发现的信封键，找不到时回退到 const.ENVELOPE_KEY_DEFAULTS。

    Args:
        body: 响应体 dict
        fallback_keys: 回退的信封键列表

    Returns:
        提取的实体数据，或空 dict/list
    """
    if not isinstance(body, dict):
        return {}

    # 优先使用发现的信封键
    keys_to_try = fallback_keys or const.ENVELOPE_KEY_DEFAULTS

    for key in keys_to_try:
        if key in body and body[key] is not None:
            return body[key]

    # 都找不到，返回空
    return {}


# ========== 通用解析工具 ==========


def _parse_body(ep_or_sample) -> dict | list:
    """从 endpoint dict 或 body sample 解析出 body dict 或 list。

    Args:
        ep_or_sample: endpoint dict（含 request_body_sample/bodies）或直接的 body sample

    Returns:
        解析后的 dict 或 list，失败返回 {}
    """
    sample = None
    if isinstance(ep_or_sample, dict):
        sample = ep_or_sample.get("request_body_sample") or (
            (ep_or_sample.get("bodies") or [None])[0])
    else:
        sample = ep_or_sample

    if not sample:
        return {}
    try:
        body = json.loads(sample) if isinstance(sample, str) else sample
        # 支持 dict 和 list
        if isinstance(body, (dict, list)):
            return body
        return {}
    except Exception:
        return {}


# ========== 响应约定发现 ==========


def _discover_envelope_keys(response_samples: dict) -> list:
    """从响应样本自动发现信封键。

    统计各候选键在实际响应中的出现频率，按频率排序返回。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        按出现频率排序的信封键列表
    """
    hit_count = {k: 0 for k in const.ENVELOPE_KEY_CANDIDATES}
    total = 0

    for pathname, samples in response_samples.items():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            total += 1
            for key in const.ENVELOPE_KEY_CANDIDATES:
                if key in body:
                    hit_count[key] += 1

    if total == 0:
        return const.ENVELOPE_KEY_DEFAULTS

    # 按命中数降序排列，只保留有命中的
    found = [(k, c) for k, c in hit_count.items() if c > 0]
    found.sort(key=lambda x: -x[1])

    if found:
        return [k for k, _ in found]
    return const.ENVELOPE_KEY_DEFAULTS


def _discover_success_check(response_samples: dict) -> dict:
    """从响应样本自动发现成功判断模式。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        success_check 配置 dict
    """
    has_success = 0
    has_error_code = 0
    total = 0

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            total += 1
            for f in const.SUCCESS_FIELD_CANDIDATES:
                if f in body:
                    has_success += 1
                    break
            for f in const.ERROR_FIELD_CANDIDATES:
                if f in body:
                    has_error_code += 1
                    break

    if total > 0 and has_success / total > 0.3:
        # 找到 success 字段 → field_and_absence 模式
        # 确定具体的 success_field 和 error_field
        success_field = "success"
        error_field = None
        for samples in response_samples.values():
            for s in samples[:1]:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
                except Exception:
                    continue
                if isinstance(body, dict):
                    for f in const.SUCCESS_FIELD_CANDIDATES:
                        if f in body:
                            success_field = f
                            break
                    for f in const.ERROR_FIELD_CANDIDATES:
                        if f in body:
                            error_field = f
                            break
                    break
            if success_field != "success" or error_field:
                break

        return {
            "type": "field_and_absence",
            "success_field": success_field,
            "error_field": error_field,
        }

    # 未找到明确的 success 字段 → 仅 HTTP 状态码
    return {"type": "http_only"}


def _discover_list_structure(response_samples: dict, envelope_keys: list) -> tuple:
    """从查询类响应中发现列表结构。

    Args:
        response_samples: 响应样本
        envelope_keys: 已发现的信封键

    Returns:
        (list_keys, total_keys) 元组
    """
    list_hits = {k: 0 for k in const.LIST_KEY_CANDIDATES}
    total_hits = {k: 0 for k in const.TOTAL_KEY_CANDIDATES}

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue

            # 在信封内查找列表结构
            entity = None
            for ek in envelope_keys:
                if ek in body:
                    entity = body[ek]
                    break
            if not isinstance(entity, dict):
                continue

            for k in const.LIST_KEY_CANDIDATES:
                if k in entity and isinstance(entity[k], list):
                    list_hits[k] += 1
            for k in const.TOTAL_KEY_CANDIDATES:
                if k in entity and isinstance(entity[k], (int, float)):
                    total_hits[k] += 1

    # 按命中数排序，保留有命中的
    found_list = [k for k, c in sorted(list_hits.items(), key=lambda x: -x[1]) if c > 0]
    found_total = [k for k, c in sorted(total_hits.items(), key=lambda x: -x[1]) if c > 0]

    return (
        found_list or const.LIST_KEY_DEFAULTS,
        found_total or const.TOTAL_KEY_DEFAULTS,
    )


def _discover_response_contract(response_samples: dict) -> dict:
    """从响应样本自动发现完整的响应约定。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        response_contract dict
    """
    envelope_keys = _discover_envelope_keys(response_samples)
    success_check = _discover_success_check(response_samples)
    list_keys, total_keys = _discover_list_structure(response_samples, envelope_keys)
    # id_field 默认为 const.DEFAULT_ID_FIELD，后续会被 id_field_details 覆盖
    id_field = const.DEFAULT_ID_FIELD

    return {
        "envelope_keys": envelope_keys,
        "success_check": success_check,
        "list_keys": list_keys,
        "total_keys": total_keys,
        "id_field": id_field,
    }
