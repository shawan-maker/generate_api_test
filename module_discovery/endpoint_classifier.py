"""
endpoint_classifier.py — API 端点分类与去重

职责：
- 根据 HTTP 方法、URL 路径、触发上下文对 API 端点分类
- 去重合并相同端点的多次调用
- 识别 CRUD 类别（create/query/update/delete/lock/unlock/reset/authorize）
"""

import re
from typing import Dict, List, Set
from . import const


class EndpointClassifier:
    """端点分类器封装类，供 capture_apis 调用"""

    def __init__(self, kb_config: dict = None):
        self.kb_config = kb_config or {}

    def classify_by_text(self, text: str) -> str:
        """根据按钮文本分类 CRUD 类别"""
        return _classify_by_text(text)

    def deduplicate(self, all_calls: List[Dict], samples: Dict) -> Dict:
        """去重合并并分类端点"""
        return deduplicate_calls(all_calls, samples)


def _classify_by_text(text: str) -> str:
    """根据按钮文本分类 CRUD 类别。"""
    for crud, keywords in const.ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return crud
    return "unknown"


def _crud_from_contexts(contexts: List[str]) -> str | None:
    """从 API 的触发上下文(按钮文本)推导 CRUD 类别，作为写操作判定的强信号。

    例: 'click:创建用户' / 'row:编辑' / 'dropdown:更多:冻结'
        → 取末段动作文本，经 ACTION_KEYWORDS 分类。
    支持 Stage 2 playbook 回放上下文格式 'replay:lock' / 'replay:unlock' 等，
    此时 action 为英文 action 名，直接匹配 priority 列表而不走中文关键词查找。
    仅返回"写操作类"类别(create/delete/update/lock/unlock/reset/authorize/migrate)；
    查询/详情等不在此返回（避免把创建上下文里的列表查询误标为 create）。
    """
    priority = ["create", "delete", "update", "lock", "unlock", "reset",
                "authorize", "migrate"]
    best = None
    for ctx in contexts:
        parts = str(ctx).split(":")
        if len(parts) < 2:
            continue

        action = parts[-1].strip()

        # 直接匹配 replay:{action} 格式（Stage 2 playbook 回放上下文）
        if parts[0] == "replay" and action in priority:
            if best is None or priority.index(action) < priority.index(best):
                best = action
                continue

        # 原有逻辑：中文按钮文本匹配
        cat = _classify_by_text(action)
        if cat in priority:
            if best is None or priority.index(cat) < priority.index(best):
                best = cat
    return best


def classify_endpoint(method: str, pathname: str, contexts: List[str]) -> str:
    """综合分类端点（修复：纯 URL 关键词错配 estack 扁平资源风格）。

    优先级: ① URL 查询/详情关键词(高优先，防止被 create 上下文误标)
           ② 写方法专属关键词(lock/unlock/reset 等路径语义，先于 DELETE 兜底)
           ③ DELETE → delete（仅真正的删除接口；unlock 已在②处理）
           ④ 按钮上下文的写操作类别(仅对 POST/PUT/DELETE 生效——GET 请求
              (下拉数据源/校验)无论上下文是什么都绝不可能是 create/update/lock 等写操作)
           ⑤ URL 写操作关键词
           ⑥ RESTful 资源路径兜底（POST=创建, PUT=更新, DELETE=删除）
           ⑦ 上下文推断（replay:query 等）
           ⑧ 兜底 other_post / other_get
    """
    p = pathname.lower()

    # ① URL 查询/详情关键词（高优先）
    # 修复: 从子串匹配改为段级匹配，避免 /target 被 /get 误判、
    # /playlist 被 /list 误判、/preview 被 /view 误判
    _path_segs = [s for s in p.split("/") if s]
    _last2 = _path_segs[-2:] if len(_path_segs) >= 2 else _path_segs

    _query_segs = {"list", "page", "search", "query", "find", "all", "select"}
    _detail_segs = {"detail", "get", "info", "view"}

    for seg in _last2:
        if seg in _query_segs:
            return "query"
        if seg in _detail_segs:
            return "detail"

    # ①b RESTful 详情兜底：GET /resources/{id} 或 GET /resources/32hex
    if method == "GET" and re.search(r"/\{[^}]+\}$", p):
        return "detail"
    if method == "GET" and re.search(r"/[0-9a-f]{20,}$", p):
        return "detail"

    # ② 校验类 API（账号/手机号/邮箱校验等）永远不是写操作，即使 POST+create 上下文
    if any(k in p for k in ("/check", "/usability", "/valid", "/exist", "/unique")):
        return "support" if method == "POST" else "other_get"

    # ③ 写方法专属语义（先于 DELETE 兜底，避免 unlock 被误判 delete）
    if method == "DELETE" and "/unlock" in p:
        return "unlock"
    if method == "DELETE":
        return "delete"

    # ④ 按钮上下文的写操作类别：仅对写方法生效（GET 不可能是写操作）
    if method in ("POST", "PUT", "PATCH"):
        ctx_crud = _crud_from_contexts(contexts)
        if ctx_crud in ("create", "delete", "update", "lock", "unlock", "reset", "authorize", "migrate"):
            return ctx_crud

    # ⑤ URL 写操作关键词（段级匹配，避免子串误判）
    _write_map = {
        "create": {"create", "add", "save", "register", "apply"},
        "update": {"update", "edit", "modify", "change"},
        "lock": {"lock", "freeze", "disable", "suspend"},
        "unlock": {"unlock", "enable", "activate", "resume"},
        "reset": {"reset", "reset-password", "password-reset"},
        "delete": {"delete", "remove", "destroy"},
    }
    # 段级匹配：关键词需完整出现在某个路径段中（允许连字符复合段如 batch-delete）
    for seg in _last2:
        seg_tokens = set(re.split(r"[-_.]", seg))
        for crud, kws in _write_map.items():
            if seg in kws or seg_tokens & kws:
                return crud

    # ⑥ RESTful 资源路径兜底：POST /resources = 创建，PUT /resources/{id} = 更新
    # 识别模式：URL 末段是资源名（非 ID），且无明确 action 路径
    if method == "POST" and not any(k in p for k in ("/list", "/page", "/search", "/check")):
        # 排除已知的基础设施 API（菜单、主题、字典等）
        if not any(k in p for k in ("/menu/", "/theme", "/favorite", "/dynamic-dictionary",
                                     "/notice/", "/access-log", "/system-theme")):
            # 末段看起来像动作（含连字符/下划线/已知动词）→ 不兜底 create
            last_seg = p.rstrip("/").rsplit("/", 1)[-1]
            if "-" not in last_seg and "_" not in last_seg and last_seg.isalpha():
                return "create"
    if method == "PUT":
        return "update"
    if method == "PATCH":
        return "update"

    # ⑦ 上下文推断（replay:query 等）
    # 判断逻辑：
    #   - 列表查询 API（/tenants/users）只在 CRUD 操作中触发：
    #     create, update, delete, lock, unlock + query → 6 个 replay 上下文
    #   - 工具类 API（current-user）在每个操作回放时都会被调用，
    #     额外出现在 import, migrate, reset 等"基础设施"操作中
    # 策略：有 replay:query 且不在 import/migrate/reset 中出现 → 列表查询 API
    if method == "GET" and "replay:query" in contexts:
        infra_ctxs = {"replay:import", "replay:migrate", "replay:reset"}
        if not any(c in contexts for c in infra_ctxs):
            return "query"

    # ⑧ 兜底
    if method == "POST":
        return "other_post"
    if method == "GET":
        return "other_get"
    return "other"


def deduplicate_calls(all_calls: List[Dict], samples: Dict) -> Dict:
    """去重合并相同端点的多次调用。

    Args:
        all_calls: 所有捕获的 API 调用列表
        samples: 响应样本 {pathname: [{status, body}, ...]}

    Returns:
        {classified, all_endpoints, response_samples, stats}
    """
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

    classified = {}
    for ep in uniq.values():
        cat = classify_endpoint(ep["method"], ep["pathname"], list(ep["contexts"]))
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

