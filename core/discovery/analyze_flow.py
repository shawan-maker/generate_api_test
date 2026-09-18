"""
analyze_flow.py — Stage 3: 逻辑顺序与关联分析

核心推理流水线：
  Step 0: 基础设施 API 识别  → 识别 init 阶段的辅助 API
  Step 1: 构建核心 API 列表  → 从 core_api_map 提取业务 API
  Step 2: 按钮-API 关联     → 建立 "按钮文本 → 核心 API" 映射
  Step 3: CRUD 顺序编排     → 推导正确的执行顺序
  Step 4: 数据依赖链分析    → 从响应中提取 id，注入后续请求
  Step 5: 状态断言自动推导  → 跨响应对比找出状态变化规律
"""

import re
import json
import logging
from typing import Optional
from collections import defaultdict
from . import const

LOG = logging.getLogger("analyze_flow")


def _identify_infrastructure_apis(all_endpoints: list, threshold: float = 0.6) -> set:
    """统计每个 GET 端点出现在多少不同操作 context 中。

    如果一个 GET 端点出现在 ≥threshold 的不同操作中 → 基础设施 API。
    例如 /current-user 在 create/update/delete/migrate 中都出现 → 基础设施。
    而 /display-unit-tree 只在 migrate 中出现 → 业务 API。

    额外规则：如果一个端点只在 init 上下文中出现（无任何 replay: 上下文），
    也视为基础设施 API（页面初始化加载的资源）。

    操作总数 ≤ 2 时降级为保守策略：只排除已知的全局基础设施路径
    （/current-user, /access-log 等），避免频率统计不可靠。

    Args:
        all_endpoints: 所有去重端点列表（含 method/context 字段）
        threshold: 出现比例阈值（默认 60%）

    Returns:
        set: 基础设施 API 的 pathname 集合
    """
    path_contexts = defaultdict(set)
    all_contexts = set()
    init_only_paths = set()  # 只在 init 上下文出现的端点

    for ep in all_endpoints:
        pathname = ep.get("pathname", "")
        contexts = ep.get("contexts", [])

        has_replay = False
        for ctx in contexts:
            if ctx.startswith("replay:"):
                has_replay = True
                action = ctx.split(":", 1)[1]
                path_contexts[pathname].add(action)
                all_contexts.add(action)

        # 记录只在 init 上下文出现的端点
        if not has_replay:
            init_only_paths.add(pathname)

    # 规则 1：只在 init 上下文出现 → 基础设施 API
    infra = set(init_only_paths)
    if init_only_paths:
        LOG.debug(f"  基础设施 API（init-only）: {init_only_paths}")

    # 规则 2：频率统计（出现在多个 replay 操作中）
    total = len(all_contexts)
    if total == 0:
        return infra

    # 降级策略：操作总数 ≤ 2 时，只排除已知的全局基础设施路径（从 KB 读取）
    from .kb_loader import get_kb
    kb = get_kb()
    generic_infra = kb.get_infrastructure_patterns()
    if not generic_infra:
        # 兜底：最通用的基础设施模式（不依赖特定项目）
        generic_infra = ["/current-user", "/access-log", "/system-theme"]
    if total <= 2:
        for path in path_contexts:
            if any(pattern in path for pattern in generic_infra):
                infra.add(path)
        LOG.debug(f"  基础设施 API 降级识别（total={total}）: {infra}")
        return infra

    for path, contexts in path_contexts.items():
        ratio = len(contexts) / total
        if ratio >= threshold:
            infra.add(path)
            LOG.debug(f"  基础设施 API: {path} 出现在 {len(contexts)}/{total} ({ratio:.2f}) 个操作中")

    return infra


def _is_list_query(pathname: str, response_samples: dict) -> bool:
    """判断 API 是否为列表查询（响应含 list+total 结构）。

    列表查询 API 即使出现在多个按钮上下文中也不应被同现过滤器排除，
    因为页面操作后刷新列表是常见行为。
    """
    if not response_samples:
        return False
    samples = response_samples.get(pathname, [])
    for s in samples[:1]:
        try:
            body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            # 尝试所有候选信封键，而非硬编码 "entity"
            entity = body
            for ek in const.ENVELOPE_KEY_CANDIDATES:
                if ek in body and isinstance(body[ek], (dict, list)):
                    entity = body[ek]
                    break

            # Case 1: entity 是列表（如 display-unit-tree）
            if isinstance(entity, list) and len(entity) > 0:
                return True

            # Case 2: entity.list + entity.total（如 tenants/users）
            if isinstance(entity, dict):
                has_list = any(k in entity for k in const.LIST_KEY_CANDIDATES)
                has_total = any(k in entity for k in const.TOTAL_KEY_CANDIDATES)
                if has_list and has_total:
                    return True
        except Exception:
            pass
    return False


def analyze(core_api_map, all_endpoints, response_samples, ui_result,
            pre_api_candidates=None, profile=None,
            operation_order=None) -> dict:
    """
    完整分析流程入口。

    从 core_api_map 构建 core_apis，不再使用旧的 HTTP 方法分类逻辑。

    Args:
        core_api_map: Stage 2 操作→核心API映射（核心输入）
        all_endpoints: 所有去重端点列表
        response_samples: 响应样本 {pathname: [{status, body}, ...]}
        ui_result: discover_ui 的输出
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        profile: profile.yaml 配置字典
        operation_order: Stage 2 保存的操作顺序
    """
    LOG.info("开始逻辑分析...")

    # Step 0: 基础设施 API 识别
    infra_apis = _identify_infrastructure_apis(all_endpoints, threshold=0.6)
    if infra_apis:
        LOG.info(f"  Step 0: 基础设施 API 识别: {len(infra_apis)} 个")

    # Step 1: 构建 core_apis
    core_apis = _build_core_apis_from_core_api_map(core_api_map, infra_apis)
    LOG.info(f"  Step 1 (core_api_map): {list(core_apis.keys())}")

    # Step 2: 按钮-API 关联
    api_by_button = _map_buttons_to_apis(core_apis, all_endpoints, ui_result)
    LOG.info(f"  Step 2: 按钮→API 映射: {len(api_by_button)} 条")

    # Step 3: 执行顺序（直接使用 Stage 2 的 operation_order）
    execution_order = _derive_order(core_apis, operation_order)
    LOG.info(f"  Step 3: 执行顺序: {execution_order}")

    # Step 4: 数据依赖链分析（数据驱动：找 ID 生产者）
    dep_chain = _derive_dependencies(core_apis, response_samples, execution_order)
    LOG.info(f"  Step 4: 依赖字段: {list(dep_chain.get('injections', {}).keys())}")
    id_producer = dep_chain.get("id_producer")
    LOG.info(f"  Step 4: ID 生产者: {id_producer or '未找到'}")

    # Step 5: 状态断言推导（数据驱动：用操作名作 key）
    state_rules = _derive_state_rules(core_apis, response_samples, id_producer)
    LOG.info(f"  Step 5: 状态断言字段: {state_rules.get('state_field') or 'success_only'}")

    # Step 6: 前置 API 依赖链追踪（Phase B）
    pre_api_candidates = pre_api_candidates or []
    pre_api_result = None
    if pre_api_candidates:
        pre_api_result = trace_pre_api_dependencies(
            core_apis, response_samples, pre_api_candidates,
            context_fields={}, id_field_details=dep_chain.get("id_field_details", {}),
            id_producer=id_producer
        )
        LOG.info(f"  Step 6: 前置 API 追踪: {len(pre_api_result.get('pre_apis', []))} 个前置 API")

    return {
        "crud_order": execution_order,
        "core_apis": core_apis,
        "api_by_button": api_by_button,
        "dependencies": dep_chain,
        "state_assertions": state_rules,
        "pre_api_chain": pre_api_result,
        "core_api_map": core_api_map or {},
    }


def _build_core_apis_from_core_api_map(core_api_map: dict, infra_apis: set) -> dict:
    """从 core_api_map 构建 core_apis。

    core_api_map 值已改为数组格式，每项包含完整端点数据，
    不需要再查 classified_apis 表。
    """
    core_apis = {}

    for action_name, candidates in core_api_map.items():
        if action_name == "init":
            continue
        if not candidates:
            continue

        # 从候选中选取核心 API
        selected = _select_core_api(candidates, "step")
        if not selected:
            continue

        target_cat = action_name
        if target_cat not in core_apis:
            core_apis[target_cat] = []

        core_apis[target_cat].append(selected)

        # ★ 保留其他写操作候选（操作链）
        # 过滤条件：
        #   1. 是写操作（POST/PUT/PATCH/DELETE）且有 body
        #   2. pathname 不以 /list, /search, /query 结尾（这些是伪写操作=查询）
        #   3. 与核心 API 共享资源路径前缀（排除不同资源的 API）
        core_pathname = selected.get("pathname", "")
        core_resource = "/".join(core_pathname.rstrip("/").split("/")[:-1])  # 如 /estack/api/estack/draco/v1/users
        query_suffixes = ("/list", "/search", "/query", "/check", "/validate", "/generate")

        others = []
        for c in candidates:
            if c is selected:
                continue
            if c.get("method", "").upper() not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            if c.get("body_field_count", 0) <= 0:
                continue
            # 排除伪写操作（pathname 以 /list 等结尾 = 实际是查询）
            c_pathname = c.get("pathname", "")
            if any(c_pathname.endswith(s) for s in query_suffixes):
                LOG.debug(f"  操作链过滤: {action_name} 跳过 {c_pathname}（伪写操作：以 {c_pathname.split('/')[-1]} 结尾）")
                continue
            # 排除不同资源路径的 API
            c_resource = "/".join(c_pathname.rstrip("/").split("/")[:-1])
            if core_resource and c_resource and core_resource != c_resource:
                LOG.debug(f"  操作链过滤: {action_name} 跳过 {c_pathname}（资源路径 {c_resource} != {core_resource}）")
                continue
            others.append(c)

        for other in others:
            core_apis[target_cat].append(other)
            LOG.info(f"  操作链: {action_name} 追加 "
                     f"{other['method']} {other['pathname'].split('/')[-1]}")

    return core_apis


def _map_buttons_to_apis(core_apis: dict, all_endpoints: list,
                         ui_result: dict) -> dict:
    """
    Step 2: 构建 按钮文本 → 核心API 的映射。

    方法：
    - 从 ui_result 中收集所有按钮文本
    - 对每个核心 API，查看其 contexts 中包含了哪些按钮点击
    - 建立反向映射
    """
    # 收集所有按钮文本
    button_texts = set()
    for key in ("toolbar_buttons", "row_actions", "dropdowns"):
        for btn in ui_result.get(key, []):
            button_texts.add(btn["text"])
            if btn.get("parent"):
                button_texts.add(f"{btn['parent']}→{btn['text']}")

    # 建立 context → button 映射
    mapping = {}
    for category, endpoints in core_apis.items():
        for ep in endpoints:
            for ctx in ep.get("contexts", []):
                # ctx 格式: "click:新增用户" 或 "dropdown:更多:锁定"
                parts = ctx.split(":")
                if len(parts) >= 2:
                    btn_text = parts[1]
                    if btn_text not in mapping:
                        mapping[btn_text] = []
                    mapping[btn_text].append({
                        "category": category,
                        "method": ep["method"],
                        "pathname": ep["pathname"],
                        "body_sample": ep.get("request_body_sample"),
                        "query_params": ep.get("query_params", {}),
                    })

    return mapping


def _derive_order(core_apis: dict, operation_order: list = None) -> list:
    """
    确定执行顺序：直接使用 Stage 2 的 operation_order，不重新推导。

    Stage 2 已按 playbook 回放顺序保存了操作序列，
    该顺序由用户在 Stage 1 中定义，是正确的业务顺序。

    Args:
        core_apis: 核心 API 字典
        operation_order: Stage 2 保存的操作顺序

    Returns:
        执行顺序列表
    """
    if operation_order:
        # 过滤掉 core_apis 中不存在的操作
        filtered = [op for op in operation_order if op in core_apis]
        # 补充 core_apis 中有但 operation_order 中没有的（兜底）
        for op in core_apis:
            if op not in filtered:
                filtered.append(op)
        return filtered
    else:
        # 旧数据兼容：无 operation_order 时使用字典键顺序
        return list(core_apis.keys())


def _select_core_api(candidates, purpose="step"):
    """从候选列表中选取最合适的 API。

    Args:
        candidates: 候选 API 列表，每项包含 method, pathname, body_field_count, response_has_id 等
        purpose: 选取目的 - "step"(写操作优先), "id_extract"(写操作+响应含ID), "pre_api"(有请求体)

    Returns:
        选中的候选 dict，或 None
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    if purpose == "id_extract":
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH") and c.get("response_has_id")]
        if writes:
            return max(writes, key=lambda c: c.get("body_field_count", 0))

    elif purpose == "step":
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH", "DELETE")]
        if writes:
            return max(writes, key=lambda c: c.get("body_field_count", 0))

    elif purpose == "pre_api":
        with_body = [c for c in candidates if c.get("body_field_count", 0) > 0]
        if with_body:
            return max(with_body, key=lambda c: c.get("body_field_count", 0))

    return max(candidates, key=lambda c: c.get("body_field_count", 0))


def _find_last_write_op(core_apis):
    """从 core_apis 中找最后一个写操作的操作名。"""
    for action in reversed(list(core_apis.keys())):
        for ep in core_apis[action]:
            if ep.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
                return action
    return None


def _derive_dependencies(core_apis: dict, response_samples: dict, operation_order: list) -> dict:
    """
    Step 4: 从响应样本中分析数据依赖关系。

    核心逻辑：
    1. 按 operation_order 顺序扫描响应，找到第一个返回 ID 的操作（ID 生产者）
    2. 检查后续操作的请求体/URL 中是否引用该 ID
    3. 返回 id_producer（操作名）供后续步骤使用

    Args:
        core_apis: 核心 API 字典 {操作名: [端点数据]}
        response_samples: 响应样本 {pathname: [{status, body}, ...]}
        operation_order: 执行顺序列表

    Returns:
        dict with:
        - id_producer: 产生 ID 的操作名（如"创建用户"）
        - id_field_details: ID 字段详情 {字段名: {source_action, sample_value, path}}
        - injections: ID 引用模式 {字段名: {source, source_path}}
    """
    id_producer = None  # 产生 ID 的操作名
    id_field_details = {}  # {字段名: {source_action, sample_value, path}}
    injections = {}

    # 1. 按 operation_order 顺序找 ID 生产者（数据驱动，不依赖操作名）
    for action in operation_order:
        endpoints = core_apis.get(action, [])
        if not endpoints:
            continue
        ep = endpoints[0]  # 只处理 main endpoint，避免 chain candidate 污染
        pn = ep["pathname"]
        samples = response_samples.get(pn, [])
        for s in samples[:1]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
            except Exception as e:
                LOG.debug(f"解析响应样本失败 ({pn}): {e}")
                continue

            # 递归提取 ID 字段
            found_ids = _extract_ids_recursive(body, path="", max_depth=4)

            for field_path, field_name, sample_value in found_ids:
                if field_name not in id_field_details:
                    id_field_details[field_name] = {
                        "source_action": action,
                        "sample_value": sample_value,
                        "path": field_path,
                    }
                    LOG.debug(f"  发现 ID 字段: {field_path} = {sample_value} (来自 {action})")

                    # 第一个找到 ID 的操作即为 ID 生产者
                    if id_producer is None:
                        id_producer = action
                        LOG.debug(f"  ID 生产者: {action}")

        # 如果已找到 ID 生产者，停止扫描
        if id_producer:
            break

    # 2. 检查后续操作是否引用了这些 ID 字段
    for action, endpoints in core_apis.items():
        if action == id_producer:
            continue  # 跳过 ID 生产者本身

        if not endpoints:
            continue
        ep = endpoints[0]  # 只处理 main endpoint，避免 chain candidate 污染
        body_sample = ep.get("request_body_sample") or (
            ep.get("bodies")[0] if ep.get("bodies") else None)
        if not body_sample:
            continue
        try:
            req_body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
        except Exception as e:
            LOG.debug(f"解析请求体 JSON 失败: {e}")
            continue

        if isinstance(req_body, list):
            # 数组格式（如 batch delete ["id1", "id2"]）
            if req_body and isinstance(req_body[0], str) and len(req_body[0]) >= 8:
                injections["__array_items__"] = {
                    "source": id_producer or "unknown",
                    "source_path": "entity.id",
                }
            continue

        if not isinstance(req_body, dict):
            continue

        # 检查请求体中是否包含 ID 字段
        for fname in const.COMMON_ID_FIELDS:
            if fname in req_body and fname not in injections:
                injections[fname] = {
                    "source": id_field_details.get(fname, {}).get(
                        "source_action",
                        id_producer or "unknown"),
                    "source_path": id_field_details.get(fname, {}).get("path", fname),
                }

    result = {
        "id_producer": id_producer,
        "id_field_details": id_field_details,
        "injections": injections,
    }
    return result


def _extract_ids_recursive(obj, path: str = "", max_depth: int = 4, _depth: int = 0):
    """
    递归遍历 JSON 对象，提取所有 ID 字段。

    Args:
        obj: JSON 对象（dict/list/primitive）
        path: 当前路径（如 "entity.data"）
        max_depth: 最大递归深度
        _depth: 当前深度（内部使用）

    Returns:
        list of tuples: (full_path, field_name, sample_value)
        例如: [("entity.id", "id", "12345"), ("entity.data.userId", "userId", "67890")]
    """
    if _depth >= max_depth:
        return []

    results = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key

            # 检查是否是 ID 字段
            if key in const.COMMON_ID_FIELDS:
                if isinstance(value, (str, int)) and value:
                    results.append((current_path, key, str(value)))

            # 递归进入子对象
            if isinstance(value, (dict, list)):
                results.extend(_extract_ids_recursive(value, current_path, max_depth, _depth + 1))

    elif isinstance(obj, list):
        # 只检查列表的第一个元素（避免重复）
        if len(obj) > 0:
            first = obj[0]
            if isinstance(first, (dict, list)):
                results.extend(_extract_ids_recursive(first, f"{path}[0]", max_depth, _depth + 1))

    return results



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


# ========== Manifest 构建（Phase 2: 泛化架构）==========

# 步骤标签映射 — 仅保留最通用的 HTTP 行为标签
# 不再硬编码中文业务操作名（如"创建"、"锁定"）
# 实际标签优先从 ui_result.button_labels 获取（按钮文本即标签）


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


def _discover_id_field(response_samples: dict, envelope_keys: list) -> str:
    """从创建类响应中发现 ID 字段名。

    Args:
        response_samples: 响应样本
        envelope_keys: 已发现的信封键

    Returns:
        ID 字段名（默认从 const.DEFAULT_ID_FIELD）
    """
    # 优先使用 COMMON_ID_FIELDS 中的顺序
    id_hits = {}

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue

            # 在信封内查找 ID 字段
            entity = None
            for ek in envelope_keys:
                if ek in body:
                    entity = body[ek]
                    break
            if not isinstance(entity, dict):
                continue

            for fname in const.COMMON_ID_FIELDS:
                if fname in entity:
                    val = entity[fname]
                    if isinstance(val, (str, int)) and val:
                        id_hits[fname] = id_hits.get(fname, 0) + 1

    if id_hits:
        # 优先选 const.DEFAULT_ID_FIELD，其次选出现最多的
        if const.DEFAULT_ID_FIELD in id_hits:
            return const.DEFAULT_ID_FIELD
        return max(id_hits, key=id_hits.get)

    return const.DEFAULT_ID_FIELD


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
    id_field = _discover_id_field(response_samples, envelope_keys)

    return {
        "envelope_keys": envelope_keys,
        "success_check": success_check,
        "list_keys": list_keys,
        "total_keys": total_keys,
        "id_field": id_field,
    }


def _classify_body_fields(body_sample: dict, id_field_details: dict,
                          create_body_sample: dict = None,
                          context_field_names: set = None) -> dict:
    """为请求体中每个字段标注角色（role）。

    替代 gen_test.py 中 _gen_payload_code() 的硬编码字段分类。

    角色说明：
    - "id_ref":    ID 引用字段，运行时从 state['id'] 获取
    - "context":   上下文字段（tenantId 等），从 state 或 create_body 获取
    - "name":      名称字段，运行时添加 AT_{TS}_ 前缀
    - "mutable":   可变字段（description 等），运行时生成新值
    - "static":    静态字段，直接复制 body_template 的值

    Args:
        body_sample: 请求体样本 dict
        id_field_details: Stage 3 分析的 ID 字段详情
                          {field_name: {source_crud, sample_value, path}}
        create_body_sample: create 步骤的 body 样本（用于判断 context 字段）
        context_field_names: auth_profile.context_fields 中声明的字段名集合

    Returns:
        {field_name: {"role": ..., "source": ...}} 字典
    """
    if not body_sample:
        return {}

    context_field_names = context_field_names or set()

    id_field_names = set()
    if id_field_details:
        id_field_names = set(id_field_details.keys())
    id_field_names.add(const.DEFAULT_ID_FIELD)  # 始终包含默认 ID 字段

    # context 字段优先级高于 id_ref：在 context_fields 中声明的字段，
    # 即使名称出现在 id_field_details 中，也标记为 context
    effective_id_fields = id_field_names - context_field_names

    create_keys = set()
    if create_body_sample and isinstance(create_body_sample, dict):
        create_keys = set(create_body_sample.keys())

    roles = {}
    for key, value in body_sample.items():
        key_lower = key.lower()

        # 0. 上下文字段优先：在 auth_profile.context_fields 中声明
        #    但需要值匹配验证：如果当前值与 create body 中的值不同，可能是不同语义
        if key in context_field_names:
            create_value = (create_body_sample or {}).get(key)
            if create_value is not None and value != create_value:
                # 值不同 → 虽然字段名匹配 context_fields，但语义不同（如迁移的目标 tenantId）
                roles[key] = {"role": "static"}
                LOG.debug(f"    _classify_body_fields: {key} 值与 create body 不同 "
                          f"({value} vs {create_value})，降级为 static")
            else:
                roles[key] = {"role": "context", "source": f"context.{key}"}
            continue

        # 1. ID 引用字段：在 id_field_details 中出现（排除 context 字段），或字段名匹配 ID 模式
        if key in effective_id_fields:
            roles[key] = {"role": "id_ref"}
            continue

        # 判断辅助标志
        is_name_like = any(kw in key_lower for kw in const.NAME_FIELD_KEYWORDS)
        is_mutable_like = any(kw in key_lower for kw in const.MUTABLE_FIELD_KEYWORDS)
        ends_with_id = (key_lower.endswith("id") and key_lower != "id")
        ends_with_ids = (key_lower.endswith("ids") and key_lower != "ids")

        # 2. ID 引用字段（补充）：以 Id/Ids 结尾、不在 create body 中、非名称类
        #    例: roleId, policyId, userId, userIds（在 delete/update body 中出现但不在 create body 中）
        if (ends_with_id or ends_with_ids) and key not in create_keys and not is_name_like:
            roles[key] = {"role": "id_ref"}
            continue

        # 3. 上下文字段：以 Id 结尾且在 create body 中出现的字段
        #    例: tenantId, adminId（从创建时传递的上下文）
        #    但需要值匹配验证：如果当前值与 create body 中的值不同，不是 context
        if ends_with_id and key in create_keys and not is_name_like:
            create_value = (create_body_sample or {}).get(key)
            if create_value is not None and value != create_value:
                # 值不同 → 不是上下文引用，保持 static（如迁移的 tenantId 是目标部门）
                roles[key] = {"role": "static"}
                LOG.debug(f"    _classify_body_fields: {key} 值与 create body 不同 "
                          f"({value} vs {create_value})，保持 static")
            else:
                roles[key] = {"role": "context", "source": f"create_body.{key}"}
            continue

        # 4. 名称字段：字段名含 name/title/label 且值是短可读字符串
        if is_name_like and isinstance(value, str) and 1 < len(value) < 50:
            roles[key] = {"role": "name"}
            continue

        # 4. 可变字段：description 类字段
        if is_mutable_like:
            roles[key] = {"role": "mutable"}
            continue

        # 5. 静态字段：其他所有
        roles[key] = {"role": "static"}

    # 降级分析：对 static 字段的长字符串做值模式分析
    # 识别加密值、邮箱、手机号等用户输入字段
    # 跳过 ID 类字段（以 Id/Ids 结尾）— 它们是系统引用，不应被降级
    for key, role_info in roles.items():
        if role_info.get("role") == "static":
            key_lower = key.lower()
            if key_lower.endswith("id") or key_lower.endswith("ids"):
                continue  # ID 字段保持 static
            value = body_sample[key]
            if isinstance(value, str) and len(value) > 20:
                pattern = _analyze_value_pattern(key, value)
                if pattern and pattern["role"] == "test_value":
                    roles[key] = {
                        "role": "test_value",
                        "value_pattern": pattern["value_pattern"],
                    }
                    LOG.debug(f"    _classify_body_fields: {key} 降级为 test_value/{pattern['value_pattern']}")

    return roles


def _build_auth_profile(profile: dict) -> dict:
    """从项目 profile dict 构建 manifest 中的 auth_profile。

    Args:
        profile: 项目配置 dict（来自 project.yaml 或 run.py 传入）

    Returns:
        auth_profile dict
    """
    auth_cfg = profile.get("auth", {})
    captcha_cfg = profile.get("captcha", {})
    creds_cfg = profile.get("credentials", {}) or {}

    # 获取 probe_url 和 context_fields（必须从 profile 读取，无业务特定默认值）
    probe_url = profile.get("probe_url", auth_cfg.get("probe_url", ""))
    context_fields = profile.get("context_fields", {})

    if not probe_url:
        LOG.warning("profile.yaml 缺少 probe_url，auth 探活将不可用")
    if not context_fields:
        LOG.warning("profile.yaml 缺少 context_fields，上下文注入将不可用")

    return {
        "header_name": auth_cfg.get("header_name", "Authorization"),
        "header_prefix": auth_cfg.get("header_prefix", "Bearer "),
        "freshness_ttl_seconds": auth_cfg.get("freshness_ttl_seconds", 300),
        "fixed_headers": auth_cfg.get("fixed_headers", {}),
        "probe_url": probe_url,
        "context_fields": context_fields,
        "token_key": auth_cfg.get("token_key", ""),
        "token_storage": auth_cfg.get("token_storage", "localStorage"),
        "cookie_token_key": auth_cfg.get("cookie_token_key", ""),
        "captcha": {
            "auth_button_text": captcha_cfg.get("auth_button_text",
                                                 profile.get("captcha_auth_button", "")),
            "login_button_text": captcha_cfg.get("login_button_text",
                                                  profile.get("captcha_login_button", "")),
        },
        "credentials_env": {
            "username": profile.get("username_env", "APP_USER"),
            "password": profile.get("password_env", "APP_PASS"),
        },
        "credentials_default": {
            "username": creds_cfg.get("username", ""),
            "password": creds_cfg.get("password", ""),
        },
    }


def build_manifest(analysis: dict, capture_result: dict,
                   profile: dict, module_name: str, target_url: str,
                   ui_result: dict = None, infra_apis: set = None) -> dict:
    """构建完整测试清单（manifest）。

    将 Stage 3 分析结果 + 响应约定发现 + 项目配置 → 结构化的 manifest dict。

    Args:
        analysis: Stage 3 analyze() 的输出
        capture_result: Stage 2 捕获结果
        profile: 项目配置 dict
        module_name: 模块名称
        target_url: 目标页面 URL
        ui_result: Stage 1 UI 探测结果（可选，用于动态标签提取）
        infra_apis: 基础设施 API 路径集合（可选，用于过滤辅助 API）

    Returns:
        完整的 manifest dict
    """
    response_samples = capture_result.get("response_samples", {})
    core_apis = analysis.get("core_apis", {})
    crud_order = analysis.get("crud_order", [])
    infra_apis = infra_apis or set()

    # 获取 id_producer 和 last_write_op（用于替代硬编码的 "create"/"delete"）
    id_producer = analysis.get("dependencies", {}).get("id_producer")
    last_write_op = _find_last_write_op(core_apis)

    # 1. 发现响应约定
    response_contract = _discover_response_contract(response_samples)

    # 2. 构建 auth_profile
    auth_profile = _build_auth_profile(profile)

    # 3. 获取 create body 样本（用于字段分类）
    create_body_sample = None
    if id_producer and id_producer in core_apis:
        create_body_sample = _parse_body(core_apis[id_producer][0])

    # 4. 构建 steps 列表
    id_field_details = analysis.get("dependencies", {}).get("id_field_details", {})

    # 5. 获取 context_fields 字段名（用于优先标记 context 角色）
    context_fields = auth_profile.get("context_fields", {})
    context_field_names = set(context_fields.keys())

    # 获取可用的验证端点（query / detail）
    # 优先从 core_apis 获取；如果 query/detail 被同现过滤器误杀，
    # 则从 capture_result 原始数据中回退查找
    #
    # 关键校验：验证端点的响应样本必须包含 entity ID，否则 contains_id 断言无意义
    create_id_sample = None
    if create_body_sample and isinstance(create_body_sample, dict):
        # 从 create 响应样本中提取 ID（用于验证 verify endpoint 是否返回同类数据）
        create_eps = core_apis.get(id_producer, [])
        if create_eps:
            create_resp_samples = response_samples.get(create_eps[0]["pathname"], [])
            for s in create_resp_samples:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
                    # 使用 response_contract 发现的信封键和 ID 字段，而非硬编码
                    envelope_keys = response_contract.get("envelope_keys", const.ENVELOPE_KEY_CANDIDATES)
                    id_field = response_contract.get("id_field", const.DEFAULT_ID_FIELD)
                    entity = body
                    for ek in envelope_keys:
                        if ek in body and isinstance(body[ek], dict):
                            entity = body[ek]
                            break
                    if isinstance(entity, dict) and id_field in entity:
                        create_id_sample = entity[id_field]
                        break
                except Exception:
                    pass

    def _endpoint_returns_entity_id(ep) -> bool:
        """检查端点的响应样本是否包含 create 提取的 entity ID。"""
        if not create_id_sample:
            return True  # 无法校验时默认通过
        samples = response_samples.get(ep["pathname"], [])
        for s in samples:
            try:
                body_text = s["body"] if isinstance(s["body"], str) else json.dumps(s["body"])
                if create_id_sample in body_text:
                    return True
            except Exception:
                pass
        return False

    verify_endpoints = {}
    # ★ 使用 core_api_map 选择验证端点（语义优先级 > 字母排序）
    core_api_map = analysis.get("core_api_map", {})

    # 验证端点选择优先级：搜索操作 > init 操作 > 列表查询兜底
    verify_ep = None
    verify_source = None  # 记录来源用于日志

    # 优先级 1：从"搜索"操作的核心 API 获取
    for search_action in ("搜索", "query", "search"):
        if search_action in core_api_map:
            candidates = core_api_map[search_action]
            full_ep = _select_core_api(candidates, "step")
            if full_ep and _endpoint_returns_entity_id(full_ep):
                verify_ep = full_ep
                verify_source = f"搜索操作 ({search_action})"
                break

    # 优先级 2：从 init 操作获取（导航阶段的列表查询）
    if verify_ep is None and "init" in core_api_map:
        candidates = core_api_map["init"]
        full_ep = _select_core_api(candidates, "step")
        if full_ep and _endpoint_returns_entity_id(full_ep):
            verify_ep = full_ep
            verify_source = "init 操作（页面初始加载）"

    # 优先级 3：兜底 — 从所有 GET 端点中找列表查询
    if verify_ep is None:
        for ep in capture_result.get("all_endpoints", []):
            if ep.get("method") == "GET":
                pn = ep.get("pathname", "")
                # Try to find in core_api_map candidates
                for action, candidates in core_api_map.items():
                    for c in candidates:
                        if c.get("method") == ep.get("method") and c.get("pathname") == pn:
                            full_ep = c
                            if full_ep and _is_list_query(pn, response_samples) and _endpoint_returns_entity_id(full_ep):
                                verify_ep = full_ep
                                verify_source = f"列表查询兜底 ({pn[:50]})"
                                break
                    if verify_ep:
                        break
                if verify_ep:
                    break

    if verify_ep:
        verify_endpoints["query"] = {
            "ep": verify_ep,
            "body_sample": _parse_body(verify_ep),
        }
        LOG.info(f"  验证端点来源: {verify_source} → {verify_ep['pathname'][:60]}")
    else:
        LOG.warning("  验证端点未找到，写操作后将无自动验证")


    def _resolve_search_param_source(search_param: str, query_ep: dict,
                                     create_body: dict, resp_samples: dict) -> str:
        """分析 search_param 值在 create body 中的来源字段。

        通过对比搜索流量的参数值和 create body 字段值，确定映射关系。
        例如：search_param="name" 的值实际来自 create_body["userName"]。

        Args:
            search_param: 搜索参数名（如 "name"）
            query_ep: query 端点数据
            create_body: create 请求体样本
            resp_samples: 响应样本字典

        Returns:
            create body 中的源字段名，找不到则返回空字符串
        """
        if not create_body or not isinstance(create_body, dict):
            return ""

        # 从捕获的搜索流量中获取 search_param 的实际值
        query_params_samples = query_ep.get("query_params_samples", [])
        search_value = None
        for sample in query_params_samples:
            if isinstance(sample, dict) and search_param in sample:
                search_value = sample[search_param]
                break

        if not search_value:
            return ""

        # 对比 create body 字段值，找出匹配项
        for field_name, field_value in create_body.items():
            if isinstance(field_value, str) and field_value == search_value:
                return field_name

        # 如果精确匹配失败，尝试部分匹配（如搜索值包含在字段值中）
        for field_name, field_value in create_body.items():
            if isinstance(field_value, str) and search_value in field_value:
                return field_name

        return ""

    steps = []

    # 提前获取 field_resolutions（用于 verify 步骤 query_params 生成）
    pre_api_chain = analysis.get("pre_api_chain")
    field_resolutions = {}
    if pre_api_chain and pre_api_chain.get("pre_apis"):
        field_resolutions = pre_api_chain.get("field_resolutions", {})

    def _get_create_pre_api_ref_source(field_name):
        """检查 create 步骤中指定字段是否有 pre_api_ref 解析，返回其 source 或 None。

        用于 verify 步骤的 query_params 生成：如果 create body 中某字段使用了
        pre_api_ref（如 tenantId ← display_by_role.entity_0_id），verify 步骤
        的 query_params 也应引用相同来源，确保 tenantId 一致。
        """
        key = f"{id_producer}.{field_name}"
        if key in field_resolutions:
            return field_resolutions[key].get("source", "")
        return None

    def _make_step(action, ep, body_sample, assertion=None):
        """构建单个步骤 dict。"""
        # 处理数组格式请求体（如 batch delete ["id1", "id2"]）
        if isinstance(body_sample, list):
            field_roles = {
                "__array_items__": {"role": "id_ref", "source": f"{id_producer}.id"}
            }
        else:
            field_roles = _classify_body_fields(
                body_sample, id_field_details, create_body_sample, context_field_names
            )

        extract = None
        if action == id_producer:
            envelope_keys = response_contract["envelope_keys"]
            id_field = response_contract["id_field"]
            extract = {
                "id": f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field,
                "names": [k for k, v in field_roles.items() if v.get("role") == "name"],
            }

        pathname = re.sub(r"[0-9a-f]{20,}", "{id}", ep["pathname"])

        step = {
            "action": action,
            "label": (ui_result or {}).get("button_labels", {}).get(action)
                     or action,
            "api": {
                "method": ep["method"],
                "pathname": pathname,
                "query_params": ep.get("query_params", {}),
            },
            "body_template": body_sample if isinstance(body_sample, list) else (body_sample or {}),
            "body_field_roles": field_roles,
            "requires": [] if action == id_producer else ["id"],
        }
        if extract:
            step["extract"] = extract
        if assertion:
            step["assertion"] = assertion
        return step

    def _make_phased_step(action, eps):
        """构建带 phases 的多阶段步骤。

        eps[0] = 核心 API（body_field_count 最大）
        eps[1:] = 前置 phase（按 body_field_count 降序，少的先执行）

        执行顺序：eps[-1] → ... → eps[1] → eps[0]
        （最少的先执行 = 查询/准备 → 最多的后执行 = 提交）
        """
        # 核心 API（最后执行）
        core_ep = eps[0]
        core_body = _parse_body(core_ep)
        core_roles = _classify_body_fields(
            core_body, id_field_details, create_body_sample, context_field_names
        )

        # 前置 phases（反转，最少的先执行）
        prepare_eps = list(reversed(eps[1:]))
        phases = []
        all_extracts = {}  # {field_name: "phase_id.state_key"}

        for pep in prepare_eps:
            phase_id = pep["pathname"].split("/")[-1]  # 如 "generate"
            pbody = _parse_body(pep)
            proles = _classify_body_fields(
                pbody, id_field_details, create_body_sample, context_field_names
            )

            # 推导 extract：从响应样本中提取与核心 body 同名字段
            extracts = _infer_phase_extracts(pep, core_body, all_extracts)

            phases.append({
                "id": phase_id,
                "label": f"{action}({phase_id})",
                "api": {
                    "method": pep["method"],
                    "pathname": re.sub(r"[0-9a-f]{20,}", "{id}", pep["pathname"]),
                    "query_params": pep.get("query_params", {}),
                },
                "body_template": pbody or {},
                "body_field_roles": proles,
                "extract": extracts,
            })

        # 核心 phase（最后执行）
        core_pathname = re.sub(r"[0-9a-f]{20,}", "{id}", core_ep["pathname"])
        phases.append({
            "id": "main",
            "label": action,
            "api": {
                "method": core_ep["method"],
                "pathname": core_pathname,
                "query_params": core_ep.get("query_params", {}),
            },
            "body_template": core_body if isinstance(core_body, list) else (core_body or {}),
            "body_field_roles": core_roles,
        })

        # 更新核心 phase 的 field_roles：引用前置 phase 的 extract
        _apply_phase_refs(core_roles, all_extracts)

        # 后处理：服务端特殊验证逻辑
        # 当 isRandomPassword=1 时，服务端仍会验证 newPassword 格式（如 RSA 加密），
        # 但忽略实际值。因此 newPassword 应保持原始 RSA 模板值（static），
        # 不替换为 phase_ref 提取的明文或 generate 的 UUID。
        if isinstance(core_body, dict):
            is_random = core_body.get("isRandomPassword")
            if is_random and "newPassword" in core_roles:
                if core_roles["newPassword"].get("role") == "phase_ref":
                    # 恢复为 static，使用原始 RSA 模板值
                    core_roles["newPassword"] = {"role": "static"}
                    LOG.info("  操作链: newPassword 恢复为 static（isRandomPassword=1 时服务端验证格式但忽略值）")

        # extract（如果 action == id_producer）
        extract = None
        if action == id_producer:
            envelope_keys = response_contract["envelope_keys"]
            id_field = response_contract["id_field"]
            extract = {
                "id": f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field,
                "names": [k for k, v in core_roles.items() if v.get("role") == "name"],
            }

        step = {
            "action": action,
            "label": (ui_result or {}).get("button_labels", {}).get(action) or action,
            "phases": phases,
            "requires": [] if action == id_producer else ["id"],
        }
        if extract:
            step["extract"] = extract
        return step

    def _infer_phase_extracts(phase_ep, core_body, all_extracts):
        """推导 phase 的 extract 映射。

        策略：检查 phase 响应中的字段，如果字段名在核心 body 中出现，
        建立 extract 映射，供核心 phase 引用。
        支持后缀模糊匹配（如 response 中 password → request 中 newPassword）。
        """
        pathname = phase_ep["pathname"]
        samples = response_samples.get(pathname, [])
        if not samples:
            return []

        try:
            resp_body = json.loads(samples[0].get("body", "{}")) if isinstance(
                samples[0].get("body", "{}"), str) else samples[0].get("body", {})
        except Exception:
            return []

        entity = None
        for key in const.ENVELOPE_KEY_CANDIDATES:
            if key in resp_body and isinstance(resp_body[key], dict):
                entity = resp_body[key]
                break
        if entity is None:
            entity = resp_body
        if not isinstance(entity, dict):
            return []

        phase_id = pathname.split("/")[-1]
        extracts = []

        # 构建核心 body 字段的后缀索引（用于模糊匹配）
        core_body_lower = {}
        if isinstance(core_body, dict):
            for cb_field in core_body:
                core_body_lower[cb_field.lower()] = cb_field

        for field_name, value in entity.items():
            if value is None:
                continue

            matched_core_field = None

            # 1. 精确匹配：字段名在核心 body 中直接出现
            if isinstance(core_body, dict) and field_name in core_body:
                matched_core_field = field_name
            else:
                # 2. 后缀模糊匹配：response 字段名是 request 字段名的后缀
                #    如 response "password" → request "newPassword"
                fn_lower = field_name.lower()
                for cb_lower, cb_field in core_body_lower.items():
                    if cb_lower != fn_lower and cb_lower.endswith(fn_lower):
                        # 确认前缀部分合理（new, updated, old 等）
                        prefix = cb_lower[:len(cb_lower) - len(fn_lower)]
                        if prefix in ("new", "updated", "old", "target", "final"):
                            matched_core_field = cb_field
                            LOG.info(f"  操作链 extract 模糊匹配: {field_name} → {cb_field}")
                            break

            if matched_core_field:
                state_key = f"{phase_id}_{field_name}"
                extracts.append({
                    "name": state_key,
                    "path": f"entity.{field_name}",
                })
                all_extracts[matched_core_field] = f"{phase_id}.{state_key}"

        return extracts

    def _apply_phase_refs(core_roles, all_extracts):
        """将核心 phase 的 field_roles 中匹配前置 phase extract 的字段
        标记为 phase_ref。
        """
        for field_name in core_roles:
            if field_name in all_extracts:
                existing_role = core_roles[field_name].get("role", "")
                # 只覆盖 static 和 generate，不覆盖 id_ref/name/mutable/context
                if existing_role in ("static", "generate", "test_value"):
                    core_roles[field_name] = {
                        "role": "phase_ref",
                        "source": all_extracts[field_name],
                    }

    def _make_verify_step(verify_action, label, assertion):
        """构建验证步骤（基于可用的验证端点）。"""
        if verify_action not in verify_endpoints:
            return None

        ep = verify_endpoints[verify_action]["ep"]
        body_sample = verify_endpoints[verify_action]["body_sample"]

        field_roles = _classify_body_fields(
            body_sample, id_field_details, create_body_sample, context_field_names
        )
        pathname = re.sub(r"[0-9a-f]{20,}", "{id}", ep["pathname"])

        # 构建 query_params：从 samples 提取固定参数，映射到 state 引用
        query_params = {}
        samples = ep.get("query_params_samples", [])
        if samples and isinstance(samples[0], dict):
            # 取参数最多的样本作为基准
            best_sample = max(samples, key=lambda s: len(s) if isinstance(s, dict) else 0)
            # 排除搜索参数（search_param 会在运行时动态添加）
            search_param_name = ep.get("search_param", "")
            for pk, pv in best_sample.items():
                if pk == search_param_name:
                    continue
                # 优先检查 create step 中同名字段是否为 pre_api_ref
                # 如果 create 使用了 pre_api_ref 来源，verify 步骤也应使用相同来源
                create_ref_source = _get_create_pre_api_ref_source(pk)
                if create_ref_source:
                    query_params[pk] = f"${create_ref_source}"
                elif pk in context_field_names:
                    query_params[pk] = f"${pk}"
                else:
                    # 保持原始值（如 pageNum=1, pageSize=10）
                    query_params[pk] = pv

        step = {
            "action": ep["method"].lower(),
            "label": label,
            "api": {
                "method": ep["method"],
                "pathname": pathname,
                "query_params": query_params if query_params else ep.get("query_params", {}),
            },
            "body_template": body_sample or {},
            "body_field_roles": field_roles,
            "requires": ["id"],
            "assertion": assertion,
        }
        # search_verify / search_not_found 需要携带搜索参数名
        if assertion in ("search_verify", "search_not_found"):
            search_param = ep.get("search_param", "")
            if search_param:
                step["search_param"] = search_param
                # 分析 search_param 值在 create body 中的来源字段
                # 从捕获的搜索流量中获取参数值，匹配 create body 字段
                search_param_source = _resolve_search_param_source(
                    search_param, ep, create_body_sample, response_samples
                )
                if search_param_source:
                    step["search_param_source"] = search_param_source
        return step

    def _plan_verify_steps(action):
        """根据操作类型和可用端点，自动规划验证步骤。

        逻辑完全数据驱动，不依赖任何特定模块：
        - 写操作后，如果有可用的查询端点就插入验证
        - 断言类型由响应结构决定（列表→contains/not_contains，详情→field_changed）

        Args:
            action: 当前 CRUD 操作类型

        Returns:
            [(verify_action, label, assertion), ...] 验证步骤规划列表
        """
        plans = []
        action_label = (ui_result or {}).get("button_labels", {}).get(action) \
                       or action

        # 写操作后验证：基于 HTTP method 判断，不硬编码操作名
        # 检查该 action 对应的端点是否为写操作
        is_write_op = False
        if action in core_apis:
            for ep in core_apis[action]:
                if ep.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
                    is_write_op = True
                    break

        if is_write_op:
            # 优先用 query（列表验证），其次 detail（详情验证）
            if "query" in verify_endpoints:
                query_ep = verify_endpoints["query"]["ep"]
                search_param = query_ep.get("search_param", "")

                # 有 search_param → 使用 search_verify/search_not_found
                if search_param:
                    if action == last_write_op:
                        plans.append(("query", f"搜索验证（删除后）", "search_not_found"))
                    else:
                        plans.append(("query", f"搜索验证（{action_label}后）", "search_verify"))
                # 无 search_param → 使用 contains_id/not_contains_id
                elif action == last_write_op:
                    plans.append(("query", f"查询验证（删除后）", "not_contains_id"))
                elif action == id_producer:
                    plans.append(("query", f"查询验证（创建后）", "contains_id"))
                else:
                    plans.append(("query", f"查询验证（{action_label}后）", "contains_id"))
            elif "detail" in verify_endpoints:
                # 非 create/delete 的写操作可以用 detail 验证
                if action != id_producer and action != last_write_op:
                    plans.append(("detail", f"详情验证（{action_label}后）", "field_changed"))

        return plans

    # 追踪哪些 step/action 使用了 phases（用于 Pass 2 保护 phase_ref 角色）
    phase_ref_fields = {}  # {action: set(field_names)}

    for action in crud_order:
        eps = core_apis.get(action, [])
        if not eps:
            continue

        # 分支：单 API vs 多 API 操作链
        if len(eps) == 1:
            # 单 API 操作（向后兼容）
            ep = eps[0]
            body_sample = _parse_body(ep)

            # 主步骤
            steps.append(_make_step(action, ep, body_sample))
        else:
            # 多 API 操作链：生成带 phases 的 step
            phased_step = _make_phased_step(action, eps)
            steps.append(phased_step)

            # 提取步骤中的 phase_ref 字段（用于后续 Phase B 保护）
            phases = phased_step.get("phases", [])
            for phase in phases:
                if phase.get("id") == "main":
                    field_roles = phase.get("body_field_roles", {})
                    for field_name, role_config in field_roles.items():
                        if role_config.get("role") == "phase_ref":
                            if action not in phase_ref_fields:
                                phase_ref_fields[action] = set()
                            phase_ref_fields[action].add(field_name)

        # 根据可用端点自动规划验证步骤
        for verify_action, label, assertion in _plan_verify_steps(action):
            verify = _make_verify_step(verify_action, label, assertion)
            if verify:
                steps.append(verify)

    # 5. 组装 manifest（增强版：包含前置 API）
    manifest_version = "1.0"
    pre_apis_list = []
    # pre_api_chain 和 field_resolutions 已在前面获取

    if pre_api_chain and pre_api_chain.get("pre_apis"):
        manifest_version = "1.1"

        # 构建 pre_apis 数组（带 extracts 信息）
        for api_info in pre_api_chain["pre_apis"]:
            pre_api_entry = {
                "name": api_info.get("name", ""),
                "id": api_info.get("id", ""),
                "method": api_info.get("method", "GET"),
                "pathname": api_info.get("pathname", ""),
                "depends_on": api_info.get("depends_on", []),
                "extracts": [],
                "body_template": {},
                "query_params": {}
            }

            # 从 field_resolutions 找出哪些字段是从这个 API 提取的
            for key, resolution in field_resolutions.items():
                # key 格式: "action.field"
                step_action, field_name = key.split('.', 1)
                source = resolution.get("source", "")
                if source.startswith(api_info["id"] + "."):
                    extract_name = source.split(".", 1)[1]
                    # 查找对应的 extracted_fields 获取 path
                    for ef in api_info.get("extracted_fields", []):
                        if ef["name"] == extract_name:
                            # 避免重复
                            if not any(e["name"] == extract_name for e in pre_api_entry["extracts"]):
                                # 收集 used_by 信息
                                used_by = []
                                for k, res in field_resolutions.items():
                                    if res.get("source") == source:
                                        action, _ = k.split('.', 1)
                                        used_by.append(action)

                                pre_api_entry["extracts"].append({
                                    "name": extract_name,
                                    "path": ef["path"],
                                    "used_by": used_by if used_by else ["*"]
                                })
                            break

            pre_apis_list.append(pre_api_entry)

        # 补充 context_fields 提取：确保 query_params 引用的字段被提取
        if context_fields:
            for ctx_name, ctx_config in context_fields.items():
                ctx_path = ctx_config.get("path", "")
                if not ctx_path:
                    continue
                # 检查是否已有此字段提取
                already_extracted = False
                for pre_api in pre_apis_list:
                    if any(e.get("name") == ctx_name for e in pre_api.get("extracts", [])):
                        already_extracted = True
                        break
                if already_extracted:
                    continue
                # 找到能提供此字段的 pre_api（验证完整路径是否存在于响应中）
                for pre_api in pre_apis_list:
                    api_id = pre_api.get("id", "")
                    # 查找对应的 pre_api_candidates 获取响应样本
                    for candidate in capture_result.get("pre_api_candidates", []):
                        if candidate.get("id") == api_id:
                            body_text = candidate.get("response_sample", {}).get("response_body", "")
                            if body_text:
                                try:
                                    body = json.loads(body_text) if isinstance(body_text, str) else body_text
                                    # 验证完整路径是否存在（不仅是前缀）
                                    test_value = _extract_by_path_generic(body, ctx_path)
                                    if test_value is not None:
                                        pre_api["extracts"].append({
                                            "name": ctx_name,
                                            "path": ctx_path,
                                            "used_by": ["query"]
                                        })
                                        LOG.info(f"  补充 context_field 提取: {ctx_name} -> {ctx_path} (from {api_id})")
                                        already_extracted = True
                                except Exception:
                                    pass
                            break
                    if already_extracted:
                        break

        # 应用 field_resolutions 到 steps 的 body_field_roles
        for step in steps:
            action = step.get("action", "")

            # 收集需要处理的 body_field_roles：单 API step + phases step 的 main phase
            roles_targets = []
            step_field_roles = step.get("body_field_roles", {})
            if step_field_roles:
                roles_targets.append(step_field_roles)

            # phases step：处理每个 phase 的 body_field_roles
            for phase in step.get("phases", []):
                phase_roles = phase.get("body_field_roles", {})
                if phase_roles:
                    roles_targets.append(phase_roles)

            for field_roles in roles_targets:
                for field_name in list(field_roles.keys()):
                    key = f"{action}.{field_name}"
                    if key in field_resolutions:
                        # 保护 name/mutable/id_ref/phase_ref/static/test_value 字段不被 Phase B 覆盖
                        existing_role = field_roles[field_name].get("role", "")
                        if existing_role in ("name", "mutable", "context", "id_ref", "phase_ref", "static", "test_value"):
                            continue
                        resolution = field_resolutions[key]
                        strategy = resolution.get("strategy", "")

                        if strategy == "generate_random":
                            field_roles[field_name] = {
                                "role": "generate",
                                "value_type": resolution.get("value_type", "hex_id"),
                            }
                        elif strategy == "value_pattern_analysis":
                            field_roles[field_name] = {
                                "role": "test_value",
                                "value_pattern": resolution["value_pattern"],
                            }
                        else:
                            # pre_api_ref 来源
                            field_roles[field_name] = {
                                "role": "pre_api_ref",
                                "source": resolution["source"],
                            }
                            if resolution.get("is_array"):
                                field_roles[field_name]["is_array"] = True

    manifest = {
        "manifest_version": manifest_version,
        "module": {
            "name": module_name,
            "base_url": profile.get("base_url", ""),
            "login_url": profile.get("login_url", ""),
            "target_url": target_url,
        },
        "response_contract": response_contract,
        "auth_profile": auth_profile,
        "steps": steps,
        "state_assertions": analysis.get("state_assertions", {}),
    }

    # 添加 pre_apis（如果有）
    if pre_apis_list:
        manifest["pre_apis"] = pre_apis_list
        # v1.2: 添加 pre_api_refs 列表（仅包含 ID，用于项目级共享）
        manifest["pre_api_refs"] = [api["id"] for api in pre_apis_list]
        manifest["manifest_version"] = "1.2"

    LOG.info(f"  Manifest 构建完成: {len(steps)} 个步骤, "
             f"信封键={response_contract['envelope_keys'][:3]}, "
             f"ID字段={response_contract['id_field']}, "
             f"前置API={len(pre_apis_list)} 个")

    return manifest


# ========== Phase B: 前置 API 依赖链追踪 ==========

def trace_pre_api_dependencies(core_apis: dict, response_samples: dict,
                               pre_api_candidates: list, context_fields: dict,
                               id_field_details: dict,
                               id_producer: str = None) -> dict:
    """
    递归追踪参数依赖链，生成前置 API 列表。

    分析业务 API 的请求体参数，识别哪些参数应从前置 API 动态获取。
    使用三策略匹配算法：精确值匹配 → 字段名启发式 → 列表成员检查。

    Args:
        core_apis: 核心 CRUD API 字典 {category: [endpoints]}
        response_samples: 响应样本字典
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        context_fields: 上下文字段配置（预留参数）
        id_field_details: ID 字段详情

    Returns:
        {
            'pre_apis': [...],  # 排序后的前置 API 列表
            'field_resolutions': {...}  # 字段解析映射
        }
    """
    LOG.info(f"[Phase B] 开始前置 API 依赖链追踪...")

    # Phase 1: 构建参数清单（从业务 API 的请求体中提取）
    param_inventory = []
    for category, endpoints in core_apis.items():
        if not endpoints:
            continue
        ep = endpoints[0]  # 只处理 main endpoint，避免 chain candidate 污染
        body_sample = _parse_body(ep)
        if not body_sample or not isinstance(body_sample, dict):
            continue
        for field_name, field_value in body_sample.items():
            # 追踪标量值和列表值
            if isinstance(field_value, list):
                # 列表值：保留整个列表，_resolve_parameter_source 会处理
                if field_value:  # 非空列表
                    param_inventory.append({
                        'step_action': category,
                        'field_name': field_name,
                        'field_value': field_value,
                        'pathname': ep.get('pathname', '')
                    })
            elif isinstance(field_value, (str, int, float, bool)):
                param_inventory.append({
                    'step_action': category,
                    'field_name': field_name,
                    'field_value': field_value,
                    'pathname': ep.get('pathname', '')
                })

    LOG.info(f"  Phase 1: 收集到 {len(param_inventory)} 个参数")

    # Phase 2: 构建前置 API 索引（扁平化所有前置 API 响应，同值多源）
    pre_api_index = {}  # {value_str: [source_entries]}
    for api in pre_api_candidates:
        body_text = api.get('response_sample', {}).get('response_body', '')
        if not body_text:
            continue
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else body_text
        except Exception:
            continue

        # 提取所有可提取字段
        for field_info in api.get('extracted_fields', []):
            path = field_info.get('path', '')
            value = _extract_by_path_generic(body, path)
            if value is not None:
                key = str(value)
                entry = {
                    'api': api,
                    'field': field_info,
                    'value': value,
                    'value_type': _classify_value_type(value),
                    'path_depth': path.count('.') + path.count('['),
                }
                if key not in pre_api_index:
                    pre_api_index[key] = []
                pre_api_index[key].append(entry)

    LOG.info(f"  Phase 2: 前置 API 索引包含 {len(pre_api_index)} 个值")

    # Phase 3: 三策略匹配 + 值模式分析降级
    field_resolutions = {}
    used_apis = {}  # {api_id: api_info}

    for param in param_inventory:
        resolution = _resolve_parameter_source(param, pre_api_index)
        if resolution:
            key = f"{param['step_action']}.{param['field_name']}"
            field_resolutions[key] = resolution
            strategy = resolution.get("strategy", "")
            # generate_random 不加入 used_apis（不依赖任何前置 API）
            if strategy != "generate_random":
                api_id = resolution['source'].split('.')[0]
                if api_id not in used_apis:
                    used_apis[api_id] = resolution['api']
        else:
            # 非系统 ID 类型值 → 原有值模式分析降级逻辑
            if id_producer and param['step_action'] == id_producer:
                pattern = _analyze_value_pattern(param['field_name'], param['field_value'])
                if pattern and pattern['role'] == 'test_value':
                    key = f"{param['step_action']}.{param['field_name']}"
                    field_resolutions[key] = {
                        'source': '',
                        'strategy': 'value_pattern_analysis',
                        'value_pattern': pattern['value_pattern'],
                    }
                    LOG.debug(f"    值模式降级: {param['field_name']} → "
                              f"test_value/{pattern['value_pattern']}")

    LOG.info(f"  Phase 3: 解析了 {len(field_resolutions)} 个字段来源")

    # Phase 4: 拓扑排序（处理前置 API 之间的依赖）
    pre_apis = _topological_sort_pre_apis(used_apis)

    # Phase 5: 添加 context_fields 提取（确保 query_params 引用的字段被提取）
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
            path_prefix = path.split('.')[0]  # 例如 "entity"
            target_api = None
            for pre_api in pre_apis:
                # 检查该 pre_api 的响应是否包含此路径前缀
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
                # 添加提取字段
                if 'extracts' not in target_api:
                    target_api['extracts'] = []
                target_api['extracts'].append({
                    'name': field_name,
                    'path': path
                })
                LOG.info(f"  Phase 5: 添加 context_field 提取: {field_name} -> {path} (from {target_api.get('id')})")

    return {
        'pre_apis': pre_apis,
        'field_resolutions': field_resolutions
    }


def _analyze_value_pattern(field_name: str, value) -> dict:
    """根据值的特征推断字段角色（方案 B 降级：值模式分析）。

    主要数据驱动（看值本身的特征），对加密/不可读值用字段名辅助判断。
    用于区分「系统引用字段」和「用户输入字段」。

    Args:
        field_name: 字段名（加密值时用作辅助信号）
        value: 字段值

    Returns:
        {"role": "test_value"|"static", "value_pattern": "..."} 或 None
    """
    if not isinstance(value, str) or not value:
        return None

    fn_lower = field_name.lower()

    # 1. 长十六进制串（≥32 字符）→ hash/加密值，不可重放
    if re.fullmatch(r'[0-9a-fA-F]{32,}', value):
        return {"role": "test_value", "value_pattern": "hex_hash"}

    # 2. 长 Base64 串（≥50 字符，含 +/=）→ 加密值，不可重放
    #    对加密值用字段名辅助判断应生成什么类型的测试值
    if len(value) >= 50 and re.search(r'[+/=]', value) and re.fullmatch(r'[A-Za-z0-9+/=]+', value):
        # 字段名含敏感信息（phone/email/password）→ 标记为 static，复用原始加密值
        # 原因：服务端要求加密后的值，我们无法生成有效的加密值（不知道公钥）
        if any(kw in fn_lower for kw in ('phone', 'mobile', 'cell', 'email', 'mail', 'password', 'passwd', 'pwd')):
            return {"role": "static", "value_pattern": "encrypted_sensitive"}
        return {"role": "test_value", "value_pattern": "base64_encrypted"}

    # 3. 短可读字符串 — 进一步分析子模式
    if len(value) < 80:
        # 3a. 含 @ → 邮箱
        if '@' in value and '.' in value.split('@')[-1]:
            return {"role": "test_value", "value_pattern": "email"}

        # 3b. 纯数字且长度 8-15 → 手机号
        if re.fullmatch(r'\+?\d{8,15}', value):
            return {"role": "test_value", "value_pattern": "phone"}

        # 3c. 短可读字符串（含字母，长度 2-50）→ 名称/文本
        if 2 <= len(value) <= 50 and re.search(r'[a-zA-Z一-鿿]', value):
            return {"role": "test_value", "value_pattern": "text"}

    # 4. 短固定格式值（如 "+86", "1", "ACTIVE"）→ 静态
    if len(value) <= 10:
        return {"role": "static"}

    return None


def _classify_value_type(value) -> str:
    """根据值本身特征分类（不看参数名，只看值的特征）。

    Returns:
        "hex_id"     — 32字符十六进制串 (如 "42ffdba38c58484f9be2bc1adf1672e6")
        "uuid"       — 标准 UUID 格式 (如 "550e8400-e29b-41d4-a716-446655440000")
        "numeric_id" — 8位以上纯数字
        "boolean"    — 布尔值
        "number"     — 普通数字
        "string"     — 其他字符串
    """
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if not isinstance(value, str) or not value:
        return "string"
    if re.fullmatch(r'[0-9a-fA-F]{32,}', value):
        return "hex_id"
    if len(value) == 36 and value.count('-') == 4 and re.fullmatch(r'[0-9a-fA-F\-]+', value):
        return "uuid"
    if value.isdigit() and len(value) >= 8:
        return "numeric_id"
    return "string"


def _is_system_id_type(value_type: str) -> bool:
    """判断值类型是否属于"系统 ID 类"——必须从前置 API 获取或动态生成。"""
    return value_type in ("hex_id", "uuid", "numeric_id")


def _pick_best_source(entries: list, request_field_name: str) -> dict:
    """从同一值的多个前置 API 来源中选择最可靠的。

    评分规则（不依赖参数名，纯粹基于来源质量）：
    1. 路径深度浅优先（entity.tenantId 优于 entity.list[0].tenantId）
    2. 字段名叶子节点匹配加分（仅作 tiebreaker）
    """
    def score(entry):
        s = 0
        # 路径越浅越可靠（直接字段 > 列表项字段）
        s -= entry['path_depth'] * 10
        # 字段名叶子节点匹配加分（仅作 tiebreaker）
        leaf = entry['field']['name'].rsplit('_', 1)[-1].lower()
        if leaf == request_field_name.lower():
            s += 5
        return s

    return max(entries, key=score)


def _resolve_parameter_source(param: dict, pre_api_index: dict) -> dict:
    """
    值类型驱动的字段来源解析。

    核心原则：根据值的类型和值本身匹配，不依赖参数名。
    - 系统 ID 类型（hex_id/uuid/numeric_id）必须从前置 API 获取或动态生成
    - 非系统 ID 类型（短字符串、布尔值等）不尝试匹配前置 API

    策略 1: 精确值匹配 + 多源选择（路径浅优先）
    策略 2: 列表成员检查
    策略 3: generate 兜底（系统 ID 无来源时生成随机值）

    Args:
        param: 参数信息 {step_action, field_name, field_value}
        pre_api_index: 前置 API 索引 {value_str: [source_entries]}

    Returns:
        解析结果 {source, api, strategy} 或 None
    """
    field_name = param['field_name']
    field_value = param['field_value']

    # 处理列表值：如果值是一个列表，检查第一个元素
    is_array = False
    if isinstance(field_value, list):
        if not field_value:
            return None
        field_value = field_value[0]
        is_array = True

    field_value_str = str(field_value)
    value_type = _classify_value_type(field_value)

    # ── 值类型预过滤：非系统 ID 类型不匹配前置 API ──
    if not _is_system_id_type(value_type):
        return None

    # ── 策略 1: 精确值匹配 + 多源选择 ──
    if field_value_str in pre_api_index:
        entries = pre_api_index[field_value_str]
        best = _pick_best_source(entries, field_name) if len(entries) > 1 else entries[0]
        api_id = best['api']['id']
        field_name_in_api = best['field']['name']
        return {
            'source': f"{api_id}.{field_name_in_api}",
            'api': best['api'],
            'strategy': 'exact_value_match',
            'is_array': is_array
        }

    # ── 策略 2: 列表成员检查（参数值是否在某个列表响应中）──
    seen_apis = set()
    for value_str, entries in pre_api_index.items():
        for entry in entries:
            field_path = entry['field']['path']
            if '[0]' not in field_path:
                continue
            api = entry['api']
            api_key = api.get('id', '') + field_path
            if api_key in seen_apis:
                continue
            seen_apis.add(api_key)

            list_path = field_path.split('[0]')[0]
            body_text = api.get('response_sample', {}).get('response_body', '')
            if not body_text:
                continue
            try:
                body = json.loads(body_text) if isinstance(body_text, str) else body_text
                list_value = _extract_by_path_generic(body, list_path)
                if isinstance(list_value, list):
                    for item in list_value:
                        if isinstance(item, dict):
                            item_id = item.get('id')
                            if str(item_id) == field_value_str:
                                api_id = api['id']
                                field_name_in_api = entry['field']['name']
                                return {
                                    'source': f"{api_id}.{field_name_in_api}",
                                    'api': api,
                                    'strategy': 'list_membership',
                                    'is_array': True
                                }
            except Exception:
                continue

    # ── 策略 3: 无匹配但值是系统 ID → 标记运行时生成 ──
    return {
        'source': '',
        'strategy': 'generate_random',
        'value_type': value_type,
    }


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


def _extract_by_path_generic(obj: dict, path: str):
    """
    通用的路径提取函数（支持数组索引）。

    Args:
        obj: JSON 对象
        path: 路径字符串，例如 "entity.list[0].id"

    Returns:
        提取的值，或 None
    """
    if not path or obj is None:
        return None

    # 分割路径段
    parts = []
    current = ''
    for char in path:
        if char in '.[':
            if current:
                parts.append(current)
                current = ''
            if char == '[':
                parts.append('[')
        elif char == ']':
            if current:
                parts.append(current)
                current = ''
            parts.append(']')
        else:
            current += char
    if current:
        parts.append(current)

    # 遍历提取
    value = obj
    i = 0
    while i < len(parts):
        part = parts[i]

        if part == '[':
            # 下一个部分是索引
            i += 1
            if i < len(parts):
                try:
                    index = int(parts[i])
                    value = value[index]
                except (ValueError, IndexError, TypeError):
                    return None
            i += 1
            # 跳过 ']'
            if i < len(parts) and parts[i] == ']':
                i += 1
        else:
            # 普通字段访问
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            i += 1

        if value is None:
            return None

    return value
