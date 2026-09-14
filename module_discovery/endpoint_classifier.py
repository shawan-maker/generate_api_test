"""
endpoint_classifier.py — API 端点分类与去重

职责：
- 根据 HTTP 方法、请求特征对 API 端点分类（行为驱动，不依赖按钮文本）
- 去重合并相同端点的多次调用
- 基于时间戳优先法确定操作核心 API
"""

import logging
from typing import Dict, List
from . import const

LOG = logging.getLogger(__name__)


class EndpointClassifier:
    """端点分类器封装类，供 capture_apis 调用"""

    def __init__(self, kb_config: dict = None):
        self.kb_config = kb_config or {}

    def deduplicate(self, all_calls: List[Dict], samples: Dict,
                    replay_windows: Dict = None) -> Dict:
        """去重合并并分类端点"""
        return deduplicate_calls(all_calls, samples, replay_windows=replay_windows)


def _classify_by_behavior(call: dict, has_form_data: bool = False,
                         triggers_download: bool = False) -> str:
    """基于 API 行为分类（不看操作名，只看 HTTP 行为）。

    Args:
        call: API 调用信息 {method, pathname, ...}
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
        elif has_form_data:
            return "create"
        else:
            # 无表单的 POST = 状态变更（冻结/启用/审批/发布/重置密码...）
            return "state_change"

    if method in ("PUT", "PATCH"):
        return "update"

    if method == "GET":
        if triggers_download:
            return "export"
        else:
            return "query"

    return "other"


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

    # ★ 基于时间窗口构建 core_api_map
    core_api_map = {}  # {action: (method, pathname)}
    if replay_windows:
        for action, window in replay_windows.items():
            start, end = window["start"], window["end"]
            # 筛选时间窗口内的 API 调用，按时间排序
            window_calls = sorted(
                [c for c in all_calls if start <= c.get("ts", 0) <= end],
                key=lambda c: c.get("ts", 0)
            )
            # 第一个业务 API 就是核心 API（排除静态资源和心跳）
            for call in window_calls:
                if not _is_static_or_heartbeat(call):
                    core_api_map[action] = (call["method"], call["pathname"])
                    break

    # 构建反向索引：(method, pathname) → set of actions
    endpoint_actions = {}  # {(method, pathname): set(actions)}
    for action, ep_key in core_api_map.items():
        endpoint_actions.setdefault(ep_key, set()).add(action)

    # 分类端点
    classified = {}
    for ep in uniq.values():
        ep_key = (ep["method"], ep["pathname"])
        actions = endpoint_actions.get(ep_key, set())

        # ★ 分类逻辑：优先使用 core_api_map（时间戳优先法）
        if "query" in actions:
            # 如果该端点是 query 操作的核心 API，优先归为 query
            cat = "query"
        elif actions:
            # 取第一个非 query 操作作为类别
            cat = next((a for a in actions if a != "query"), list(actions)[0])
        else:
            # 兜底：基于 HTTP 方法的行为分类（不依赖文本匹配）
            cat = _classify_by_behavior(ep, has_form_data=bool(ep.get("bodies")))

        ep_data = {
            "method": ep["method"], "pathname": ep["pathname"],
            "contexts": sorted(ep["contexts"]),
            "bodies": ep["bodies"],
            "request_body_sample": ep["bodies"][0] if ep.get("bodies") else None,
            "query_params": ep["query_params_list"][0] if len(ep["query_params_list"]) == 1 else {},
            "query_params_samples": ep["query_params_list"],
        }
        # query 类端点：自动提取搜索参数名
        if cat == "query":
            search_param = _extract_search_param(ep_data)
            if search_param:
                ep_data["search_param"] = search_param
        classified.setdefault(cat, []).append(ep_data)

    return {
        "classified": classified,
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

