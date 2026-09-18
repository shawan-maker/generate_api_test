"""
endpoint_classifier.py — API 端点分类与去重

职责：
- 根据 HTTP 方法、请求特征对 API 端点分类（行为驱动，不依赖按钮文本）
- 去重合并相同端点的多次调用
- 基于时间戳优先法确定操作核心 API
"""

import json
import logging
from collections import defaultdict
from typing import Dict, List
from . import const

LOG = logging.getLogger(__name__)


def _count_pathname_windows(all_calls: List[Dict], replay_windows: Dict) -> Dict[str, int]:
    """统计每个 pathname 出现在多少个不同时间窗口中。

    用于识别周期性消息：如果一个 API 在大部分操作窗口中都出现，
    说明它是基础设施 API（如 /current-user）。

    Args:
        all_calls: 所有捕获的 API 调用列表
        replay_windows: 每个操作的时间窗口 {action: {"start": ts, "end": ts}}

    Returns:
        {pathname: window_count}
    """
    pathname_window_count = defaultdict(int)

    for action, window in replay_windows.items():
        start, end = window["start"], window["end"]
        seen_in_window = set()

        for call in all_calls:
            ts = call.get("ts", 0)
            if start <= ts <= end:
                pathname = call.get("pathname", "")
                if pathname not in seen_in_window:
                    pathname_window_count[pathname] += 1
                    seen_in_window.add(pathname)

    return pathname_window_count


def _is_id_like_field(name: str) -> bool:
    """判断字段名是否像 ID 字段（id, userId, orderId, policy_id 等）。"""
    lower = name.lower()
    if lower in ("id", "ids"):
        return True
    if lower.endswith("id") or lower.endswith("ids"):
        return True
    if lower.endswith("_id") or lower.endswith("_ids"):
        return True
    return False


def _is_id_like_value(val: str) -> bool:
    """判断值是否像 ID 值（hex 串、长数字、UUID 等）。"""
    if not val or len(val) < 8:
        return False
    if val.isdigit() and len(val) >= 8:
        return True
    if _is_hex_like(val):
        return True
    if len(val) == 36 and val.count("-") == 4:
        return True
    return False


def _call_has_business_data(call: dict, samples: dict) -> bool:
    """检查 API 调用是否涉及业务数据（非纯基础设施/校验类）。

    三维检查：URL 路径 + query params + 响应 body。
    用于 Layer 2 过滤，替代旧版 _response_has_entity_id。

    注意：对于写操作（POST/PUT/DELETE），仅凭 URL 路径中的 ID 不足以判定，
    必须结合响应体维度，以排除校验类接口（如 /users/{id}/check）。
    """
    method = call.get("method", "").upper()
    pathname = call.get("pathname", "")

    # ── 维度 1: URL 路径中有 ID 段 ──
    # 仅对 GET 操作，URL 路径 ID 可直接判定为业务数据
    # 对写操作，需要结合响应体维度确认
    has_url_id = False
    for part in pathname.split("/"):
        if len(part) >= 20 and (part.isdigit() or _is_hex_like(part)):
            has_url_id = True
            if method == "GET":
                return True
            break

    # ── 维度 2: query params 中有 ID 类参数 ──
    # 同样仅对 GET 操作直接判定
    qp = call.get("query_params", {})
    if isinstance(qp, dict):
        for key, val in qp.items():
            if _is_id_like_field(key) and _is_id_like_value(str(val)):
                if method == "GET":
                    return True
                break

    # ── 维度 3: 响应 body 中有业务数据 ──
    # 对所有操作都必须检查响应体
    sample_list = samples.get(pathname, [])
    for sample in sample_list:
        body_text = sample.get("body", "")
        if not body_text:
            continue
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else body_text
            if not isinstance(body, dict):
                continue
            if not body.get("success", True):
                continue

            entity = None
            for key in const.ENVELOPE_KEY_CANDIDATES:
                if key in body:
                    entity = body[key]
                    break
            if entity is None:
                entity = body

            # 写操作：字符串 entity 是校验接口特征（如 /users/check 返回 UUID）
            if isinstance(entity, str) and method in ("POST", "PUT", "PATCH", "DELETE"):
                continue

            if isinstance(entity, dict):
                for field_name in entity:
                    if _is_id_like_field(field_name):
                        return True

                # 检查嵌套列表（如 entity.list, entity.rows）
                for field_value in entity.values():
                    if isinstance(field_value, list) and len(field_value) > 0:
                        first = field_value[0]
                        if isinstance(first, dict):
                            if any(_is_id_like_field(k) for k in first):
                                return True

            if isinstance(entity, list) and len(entity) > 0:
                first = entity[0]
                if isinstance(first, dict):
                    if any(_is_id_like_field(k) for k in first):
                        return True

            # entity 是 ID 类字符串（仅对 GET 有效，如 /resource/{id} 直接返回 ID）
            if isinstance(entity, str) and _is_id_like_value(entity):
                return True

            if isinstance(entity, (int, float)) and entity != 0 and not isinstance(entity, bool):
                return True

        except Exception:
            continue

    return False


def _request_body_field_count(call: Dict) -> int:
    """计算请求体字段数（用于 tiebreaker）。

    当多个 API 通过 Layer 1+2 过滤时，请求体字段数最多的通常是核心 API：
    - 核心 API：发送完整实体（10+ 字段）
    - 辅助 POST：发送少量校验字段（1-3 字段）
    - GET 请求：无请求体（0 字段）

    Args:
        call: API 调用信息 {method, body, ...}

    Returns:
        请求体字段数，GET 请求返回 0
    """
    method = call.get("method", "").upper()
    if method == "GET":
        return 0

    body_text = call.get("body", "")
    if not body_text:
        return 0

    try:
        body = json.loads(body_text) if isinstance(body_text, str) else body_text
        if isinstance(body, dict):
            return len(body)
    except Exception:
        pass

    return 0


def _build_candidate(call: dict, uniq_ep: dict, samples: dict) -> dict:
    """构建单个候选 API 的完整数据。

    用于 core_api_map 数组结构，每个候选包含完整的端点信息。

    Args:
        call: API 调用信息 {method, pathname, body, ...}
        uniq_ep: 去重后的端点数据 {contexts, bodies, query_params_list, ...}
        samples: 响应样本字典

    Returns:
        候选 API 完整数据字典
    """
    candidate = {
        "method": call["method"],
        "pathname": call["pathname"],
        "body_field_count": _request_body_field_count(call),
        "response_has_id": _call_has_business_data(call, samples),
        "contexts": sorted(uniq_ep.get("contexts", set())),
        "bodies": uniq_ep.get("bodies", []),
        "request_body_sample": uniq_ep["bodies"][0] if uniq_ep.get("bodies") else None,
        "query_params": uniq_ep["query_params_list"][0] if len(uniq_ep.get("query_params_list", [])) == 1 else {},
        "query_params_samples": uniq_ep.get("query_params_list", []),
    }

    # 提取搜索参数名（Stage 3 用于判断 search_verify vs contains_id）
    search_param = _extract_search_param(candidate)
    if search_param:
        candidate["search_param"] = search_param

    return candidate


def _call_has_distinctive_params(call: Dict, all_calls: List[Dict]) -> bool:
    """检查该调用是否携带同路径其他调用没有的独有参数。

    用于 Layer 1 的例外判断：如果一个高频路径的某次调用携带了搜索参数
    （其他调用没有的非分页参数），说明是有意触发的业务请求，不应被频率排除。

    例如：
    - GET /tenants/users?tenantId=xxx&pageNum=1&pageSize=10（页面自动刷新，无独有参数）
    - GET /tenants/users?tenantId=xxx&pageNum=1&pageSize=10&name=AT_test（搜索，有独有参数 name）

    Args:
        call: 当前 API 调用信息
        all_calls: 所有捕获的 API 调用列表

    Returns:
        True 表示该调用有独有参数（应保留为候选）
    """
    pathname = call.get("pathname", "")
    call_params = call.get("query_params", {})
    if not call_params or not isinstance(call_params, dict):
        return False

    call_keys = set(call_params.keys())

    # 收集同路径其他调用的参数键
    other_keys = set()
    for other in all_calls:
        if other is call:
            continue
        if other.get("pathname") != pathname:
            continue
        other_qp = other.get("query_params", {})
        if other_qp and isinstance(other_qp, dict):
            other_keys.update(other_qp.keys())

    # 找出当前调用独有的参数键
    distinctive_keys = call_keys - other_keys
    if not distinctive_keys:
        return False

    # 排除分页参数后，是否还有独有参数
    distinctive_non_pagination = distinctive_keys - _PAGINATION_PARAMS
    return len(distinctive_non_pagination) > 0


def _extract_entity(body: dict) -> dict | None:
    """从响应体中提取实体数据（尝试常见信封键）。"""
    for key in const.ENVELOPE_KEY_CANDIDATES:
        if key in body and isinstance(body[key], dict):
            return body[key]
    # 如果没有信封键，返回 body 本身（如果包含 id）
    if "id" in body:
        return body
    return None


class EndpointClassifier:
    """端点分类器封装类，供 capture_apis 调用"""

    def __init__(self, kb_config: dict = None):
        self.kb_config = kb_config or {}

    def deduplicate(self, all_calls: List[Dict], samples: Dict,
                    replay_windows: Dict = None) -> Dict:
        """去重合并并分类端点"""
        return deduplicate_calls(all_calls, samples, replay_windows=replay_windows)


def _is_hex_like(s: str) -> bool:
    """检查字符串是否看起来像 hex ID（32字符hex或类似格式）。"""
    if not s or len(s) < 20:
        return False
    # 检查是否全是十六进制字符
    return all(c in '0123456789abcdefABCDEF' for c in s)


def _classify_by_behavior(call: dict, has_form_data: bool = False,
                         triggers_download: bool = False) -> str:
    """基于 API 行为分类（不看操作名，只看 HTTP 行为）。

    Args:
        call: API 调用信息 {method, pathname, bodies, ...}
        has_form_data: 是否携带表单数据
        triggers_download: 是否触发文件下载

    Returns:
        分类结果：create/update/delete/query/state_change/export/other
    """
    method = call.get("method", "").upper()

    # HTTP method 是主要分类依据
    if method == "DELETE":
        return "delete"

    if method == "POST":
        if triggers_download:
            return "export"

        # ★ 结构特征区分 create vs state_change
        pathname = call.get("pathname", "")
        path_parts = [p for p in pathname.split("/") if p]

        # 检查路径中是否包含 ID 段（32字符hex或长数字串）
        has_id_in_middle = False
        for i, part in enumerate(path_parts):
            # ID 特征：长度 >= 20 且是数字或hex
            if len(part) >= 20 and (part.isdigit() or _is_hex_like(part)):
                # 如果 ID 不在最后一段 → 中间有 ID → 状态变更
                if i < len(path_parts) - 1:
                    has_id_in_middle = True
                    break

        if has_id_in_middle:
            # POST + 中间有ID + 子路径 → state_change（如 /users/{id}/lock）
            return "state_change"
        elif has_form_data:
            # POST + 简单路径 + 有表单 → create
            return "create"
        else:
            # POST + 无表单 → state_change
            return "state_change"

    if method in ("PUT", "PATCH"):
        return "update"

    if method == "GET":
        if triggers_download:
            return "export"
        else:
            return "query"

    return "other"


def _score_core_candidate(call: dict, uniq: dict) -> int:
    """对 core API 候选综合评分（越高越好）。

    评分维度：
    - 方法优先级：写操作 > GET（业务操作核心应该是写操作）
    - 跨上下文惩罚：出现在越多操作中，越不可能是当前操作的核心 API
    - 请求体字段数（保留原有逻辑）
    """
    score = 0
    method = call.get("method", "").upper()
    pathname = call.get("pathname", "")

    # 1. 方法优先级
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        score += 100
    elif method == "GET":
        score += 10

    # 2. 跨上下文惩罚
    key = f"{method} {pathname}"
    ep = uniq.get(key, {})
    context_count = len(ep.get("contexts", set()))
    score -= context_count * 20

    # 3. 请求体字段数
    score += _request_body_field_count(call) * 2

    return score


def deduplicate_calls(all_calls: List[Dict], samples: Dict,
                      replay_windows: Dict = None) -> Dict:
    """去重合并相同端点的多次调用。

    Args:
        all_calls: 所有捕获的 API 调用列表
        samples: 响应样本 {pathname: [{status, body}, ...]}
        replay_windows: 每个操作的时间窗口 {action: {"start": ts, "end": ts}}

    Returns:
        {classified, all_endpoints, response_samples, stats}
    """
    # 去重合并
    uniq = {}
    for api in all_calls:
        key = api["method"] + " " + api["pathname"]
        if key not in uniq:
            uniq[key] = {"method": api["method"], "pathname": api["pathname"],
                         "contexts": set(), "bodies": [], "query_params_list": []}
        uniq[key]["contexts"].add(api.get("context", "?"))
        if api.get("body") and api["body"] not in uniq[key]["bodies"]:
            uniq[key]["bodies"].append(api["body"])
        qp = api.get("query_params", {})
        if qp and qp not in uniq[key]["query_params_list"]:
            uniq[key]["query_params_list"].append(qp)

    # ★ 基于时间窗口构建 core_api_map（数组格式，每项包含完整端点数据）
    # 三层过滤：频率排除 → 响应特征排除 → 时间戳优先（取第一个）
    # tiebreaker：多个候选时，请求体字段数最多的胜出
    core_api_map = {}  # {action: [{candidate_with_full_data}, ...]}
    if replay_windows:
        # 预计算：每个 pathname 出现在多少个不同窗口中（Layer 1 用）
        pathname_window_count = _count_pathname_windows(all_calls, replay_windows)
        operation_count = len(replay_windows)
        frequency_threshold = max(2, int(operation_count * 0.6))

        for action, window in replay_windows.items():
            start, end = window["start"], window["end"]
            # 筛选时间窗口内的 API 调用，按时间排序
            window_calls = sorted(
                [c for c in all_calls if start <= c.get("ts", 0) <= end],
                key=lambda c: c.get("ts", 0)
            )

            # Layer 0: 排除静态资源和心跳
            non_static_calls = [
                call for call in window_calls
                if not _is_static_or_heartbeat(call)
            ]

            if not non_static_calls:
                continue

            # Layer 1 + Layer 2 过滤，收集候选
            candidates = []
            for call in non_static_calls:
                pathname = call.get("pathname", "")

                # Layer 1: 排除周期性消息（出现在大部分窗口中）
                if pathname_window_count.get(pathname, 0) > frequency_threshold:
                    # 例外：如果该调用携带其他同路径调用没有的独有参数（搜索参数），
                    # 说明是有意触发的业务请求，不受频率限制
                    if not _call_has_distinctive_params(call, all_calls):
                        continue

                # Layer 2: 排除响应无业务数据的 API（校验类）
                if not _call_has_business_data(call, samples):
                    continue

                candidates.append(call)

            # 从候选中选择，构建完整数据数组
            if not candidates:
                # 兜底：所有都被过滤了，取第一个非静态的
                first_api = non_static_calls[0]
                key = f"{first_api['method']} {first_api['pathname']}"
                ep = uniq.get(key, {})
                core_api_map[action] = [_build_candidate(first_api, ep, samples)]
            else:
                # 收集所有候选，按综合评分降序排列（最佳候选在前）
                candidates.sort(key=lambda c: _score_core_candidate(c, uniq), reverse=True)
                core_api_map[action] = [
                    _build_candidate(c, uniq.get(f"{c['method']} {c['pathname']}", {}), samples)
                    for c in candidates
                ]
                # 操作链识别：多个写操作 = 操作链，记录日志
                write_candidates = [
                    c for c in candidates
                    if c.get("method", "").upper() in ("POST", "PUT", "PATCH", "DELETE")
                ]
                if len(write_candidates) >= 2:
                    chain_names = [c['pathname'].split('/')[-1] for c in write_candidates]
                    LOG.info(f"  {action}: 识别到 {len(write_candidates)} 步操作链: {chain_names}")
                if len(candidates) > 1:
                    best = candidates[0]
                    LOG.debug(f"  core_api tiebreaker: {action} → "
                              f"{best['method']} {best['pathname']} "
                              f"(评分={_score_core_candidate(best, uniq)}, 共{len(candidates)}个候选)")

    # 后校验：非 query 操作必须有写操作 API（POST/PUT/PATCH/DELETE）
    for action, candidates in list(core_api_map.items()):
        if action in ("init", "query"):
            continue
        has_write = any(
            c.get("method", "").upper() in ("POST", "PUT", "PATCH", "DELETE")
            for c in candidates
        )
        if not has_write:
            LOG.warning(f"  ⚠️ {action} 只有 GET 请求，无写操作 API，标记为 capture_incomplete")
            core_api_map[action] = []

    return {
        "core_api_map": core_api_map,
        "all_endpoints": [{"method": e["method"], "pathname": e["pathname"],
                           "contexts": sorted(e["contexts"]), "bodies": e["bodies"]}
                          for e in uniq.values()],
        "response_samples": samples,
        "stats": {"total_calls": len(all_calls), "unique_endpoints": len(uniq)},
    }


def _is_static_or_heartbeat(call: dict) -> bool:
    """排除非业务 API（静态资源、心跳、基础设施 GET）。"""
    pn = call.get("pathname", "")

    # 静态资源
    if any(pn.endswith(ext) for ext in const.SKIP_STATIC_EXTENSIONS):
        return True

    # 心跳/主题等基础设施 GET（从 KB 读取，不硬编码）
    if call["method"] == "GET":
        # 从 KB 读取基础设施 API 模式
        from .kb_loader import get_kb
        kb = get_kb()
        infra_patterns = kb.get_infrastructure_patterns()
        if any(pattern in pn for pattern in infra_patterns):
            return True

        # 兜底：通用基础设施模式（不依赖特定项目）
        generic_infra_patterns = [
            "/current-user", "/access-log", "/system-theme",
            "/favorite", "/menu/tree", "/notice/"
        ]
        if any(pattern in pn for pattern in generic_infra_patterns):
            return True

    return False


# 分页参数（排除后剩余的才可能是搜索参数）
_PAGINATION_PARAMS = {
    "pageNum", "pageSize", "page", "size", "limit", "offset",
    "current", "total", "currentPage", "pageNo", "page_num",
    "page_size", "per_page", "rows", "start", "end",
    "sortField", "sortOrder", "sort_field", "sort_order",
    "orderBy", "order", "order_by",
}


def _extract_search_param(ep_data: dict) -> str:
    """从 query 端点的请求参数中提取搜索参数名。

    策略（纯结构排除法，不依赖语义关键词）：
    1. 从 KB 读取分页参数集，排除后剩余的非空参数即候选
    2. 候选按值特征排序：
       - 短字符串（2-30字符）优先（像用户输入的搜索文本）
       - 纯数字排除（像 ID 或状态码）
    3. 多个候选时取第一个（通常 API 设计只有一个搜索参数）

    如果排除分页后无剩余参数，返回空字符串（该端点不支持搜索）。

    Returns:
        搜索参数名（str），找不到返回空字符串
    """
    from .kb_loader import get_kb
    kb = get_kb()
    pagination_params = kb.get_pagination_params()

    def _score_candidate(k: str, v) -> int:
        """对候选参数打分（越高越好）。"""
        if v is None or v == "":
            return -1
        v_str = str(v)
        # 纯数字（像 ID/状态码/分页）→ 排除
        if v_str.isdigit():
            return -1
        # 短字符串（2-30字符）→ 像搜索输入
        if 2 <= len(v_str) <= 30:
            return 10
        # 中等长度 → 仍可用
        if len(v_str) <= 100:
            return 5
        return 1

    def _pick_from_params(params_list):
        # 按非分页参数数量降序排序，优先检查参数最多的样本（通常是搜索请求）
        def count_non_pagination(params):
            if not isinstance(params, dict):
                return 0
            return sum(1 for k in params.keys() if k not in pagination_params)

        sorted_params = sorted(params_list, key=count_non_pagination, reverse=True)

        for params in sorted_params:
            if not isinstance(params, dict):
                continue
            candidates = []
            for k, v in params.items():
                if k in pagination_params:
                    continue
                score = _score_candidate(k, v)
                if score > 0:
                    candidates.append((k, score))
            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                return candidates[0][0]
        return ""

    # 检查 GET query params
    all_params = ep_data.get("query_params_samples", [])
    if not all_params and ep_data.get("query_params"):
        all_params = [ep_data["query_params"]]
    result = _pick_from_params(all_params)
    if result:
        return result

    # 检查 POST body（POST-based list 端点）
    bodies = ep_data.get("bodies", [])
    result = _pick_from_params(bodies)
    if result:
        return result

    return ""

