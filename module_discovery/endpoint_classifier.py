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
    仅返回"写操作类"类别(create/delete/update/lock/unlock/reset/authorize/migrate)；
    查询/详情等不在此返回（避免把创建上下文里的列表查询误标为 create）。
    """
    priority = ["create", "delete", "update", "lock", "unlock", "reset",
                "authorize", "migrate"]
    best = None
    for ctx in contexts:
        parts = str(ctx).split(":")
        action = parts[-1].strip() if len(parts) >= 2 else str(ctx).strip()
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
           ⑦ 兜底 other_post / other_get
    """
    p = pathname.lower()

    # ① URL 查询/详情关键词（高优先）
    for crud, kws in (("query", ("/list", "/page", "/search", "/query", "/find", "/all", "/select")),
                      ("detail", ("/detail", "/get", "/info", "/view"))):
        if any(k in p for k in kws):
            return crud

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

    # ⑤ URL 写操作关键词
    for crud, kws in (("create", ("/create", "/add", "/save", "/register", "/apply")),
                      ("update", ("/update", "/edit", "/modify", "/change")),
                      ("lock", ("/lock", "/freeze", "/disable")),
                      ("unlock", ("/unlock", "/enable", "/activate")),
                      ("reset", ("/reset", "/reset-password")),
                      ("delete", ("/delete", "/remove", "/destroy"))):
        if any(k in p for k in kws):
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

    # ⑦ 兜底
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
        classified.setdefault(cat, []).append({
            "method": ep["method"], "pathname": ep["pathname"],
            "contexts": sorted(ep["contexts"]),
            "bodies": ep["bodies"],
            "request_body_sample": ep["bodies"][0] if ep.get("bodies") else None,
            "query_params": ep["query_params_list"][0] if len(ep["query_params_list"]) == 1 else {},
            "query_params_samples": ep["query_params_list"],
        })

    return {
        "classified": classified,
        "all_endpoints": [{"method": e["method"], "pathname": e["pathname"],
                           "contexts": sorted(e["contexts"]), "bodies": e["bodies"]}
                          for e in uniq.values()],
        "response_samples": samples,
        "stats": {"total_calls": len(all_calls), "unique_endpoints": len(uniq)},
    }
