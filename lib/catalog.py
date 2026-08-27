"""
接口目录归并(IR 生成) —— 把巡游拦截到的 XHR 请求归并为统一中间表示 OpenAPI 3.1 + x-kb-* 扩展。
对应方案设计 第5节。纯函数、无浏览器依赖, 便于离线单测。
"""
import re
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qsl

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{16,}$")
NUM_RE = re.compile(r"^\d{3,}$")

# 需要脱敏的请求头/字段
SENSITIVE_KEYS = {"authorization", "cookie", "set-cookie", "token", "password", "pwd", "secret", "x-csrf-token", "sid"}
MASK = "***MASKED***"

CRUD_KEYWORDS = {
    "create": ["create", "add", "save", "新增", "创建", "adduser", "insert"],
    "list": ["list", "query", "page", "search", "查询", "列表", "getlist", "find", "tree", "all"],
    "detail": ["detail", "info", "get", "查询详情", "getbyid"],
    "update": ["update", "modify", "edit", "编辑", "set", "put"],
    "delete": ["delete", "remove", "销毁", "删除", "del"],
    # 非 CRUD 的动作型端点: 避免 login/export/json 之类被硬塞进增删改查
    "action": ["login", "logout", "export", "import", "download", "upload", "refresh",
               "check", "verify", "enable", "disable", "start", "stop", "restart",
               "reset", "bind", "unbind", "apply", "approve", "json", "send"],
}

# 短关键词歧义大(如 del 命中服务名 delphini, list 命中 black-white-list),
# 只允许"整段相等"匹配; 长关键词才允许 token/子串匹配。
_SHORT_KW = {"list", "get", "set", "add", "del", "all", "page", "json", "tree", "info", "put", "find"}


def desensitize(obj, secrets=None):
    """递归脱敏: 敏感键 + 已知密文一律打码。"""
    secrets = secrets or set()
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in SENSITIVE_KEYS:
                out[k] = MASK
            elif isinstance(v, str) and v in secrets:
                out[k] = MASK
            else:
                out[k] = desensitize(v, secrets)
        return out
    if isinstance(obj, list):
        return [desensitize(x, secrets) for x in obj]
    if isinstance(obj, str) and obj in secrets:
        return MASK
    return obj


def _is_id_segment(seg: str) -> bool:
    return bool(UUID_RE.match(seg) or HEX_RE.match(seg) or NUM_RE.match(seg))


def templatize_path(path: str) -> str:
    """把路径中的 ID 段参数化: /users/12345 -> /users/{id}。"""
    parts = path.split("/")
    out = []
    for seg in parts:
        if seg and _is_id_segment(seg):
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out)


def extract_service_resource(path: str, service_prefixes=None):
    """从 API 路径抽取 service / resource。例如 .../draco/v1/users/{id} -> ('draco','users')。"""
    service_prefixes = service_prefixes or []
    parts = [p for p in path.split("/") if p]
    service = None
    resource = None
    # 找 v1/v2 标记
    try:
        vi = next(i for i, p in enumerate(parts) if re.match(r"^v\d+$", p))
        if vi > 0:
            service = parts[vi - 1] if parts[vi - 1] in service_prefixes else service
        if vi + 1 < len(parts):
            cand = parts[vi + 1]
            resource = "{id}" if _is_id_segment(cand) else cand
    except StopIteration:
        # 无版本标记: 倒数第二段作为 resource
        if len(parts) >= 2:
            resource = "{id}" if _is_id_segment(parts[-1]) else parts[-1]
    # service 兜底: 若前缀命中
    if not service:
        for sp in service_prefixes:
            if sp in parts:
                service = sp
                break
    return service, resource


STATIC_EXT = (".mp4", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
              ".woff", ".woff2", ".ttf", ".eot", ".otf",
              ".css", ".js", ".map", ".mp3", ".wav", ".pdf", ".zip")


def is_static_asset(path: str) -> bool:
    """静态资源不是 API, 应从接口目录中剔除(如 .../image/banner/01.mp4)。"""
    p = (path or "").lower().split("?")[0]
    return p.endswith(STATIC_EXT)


def _action_segments(path: str) -> list:
    """
    取路径的"动作段"(最后 1~2 个有意义段), 用于关键词判定。
    刻意剔除: api_base 段、service 名、版本号(v1/inner)、路径参数({id}),
    否则服务名 delphini 会因含 'del' 被误判为 delete。
    """
    segs = [s for s in path.split("/") if s]
    segs = [s for s in segs if not re.fullmatch(r"v\d+|inner|\{[^}]+\}", s, re.I)]
    return [s.lower() for s in segs[-2:]]


def _kw_hit(kind: str, segs: list) -> bool:
    """在动作段上匹配关键词: 短词要求整段相等, 长词允许 token/子串。"""
    for seg in segs:
        tokens = re.split(r"[-_.]", seg)
        for kw in CRUD_KEYWORDS.get(kind, []):
            if kw in _SHORT_KW:
                if seg == kw:                    # 整段相等: /accesskey/list
                    return True
            else:
                if kw in seg or kw in tokens:    # 长词: /accesskey/create, /xxx-remove
                    return True
    return False


def guess_crud(method: str, path: str, overrides: dict = None) -> str:
    """
    双路判定 CRUD(见方案设计 第7节): 人工覆盖 > 关键词(动作段) > REST 结构。
    overrides: {"POST /a/b/c": "list"} 或 {"/a/b/c": "list"}, 来自 kb/crud_overrides.json,
               优先级最高(对应 EcsCloud failure_overrides 的设计)。
    """
    m = method.upper()
    if overrides:
        for key in (f"{m} {path}", path):
            if key in overrides:
                return overrides[key]

    segs = _action_segments(path)

    # --- 第 1 路: 关键词(只看动作段), 优先级 action > delete > update > create > list ---
    if _kw_hit("action", segs):
        return "action"
    if _kw_hit("delete", segs):
        return "delete"
    if _kw_hit("update", segs):
        return "update"
    if _kw_hit("create", segs):
        return "create"
    if _kw_hit("detail", segs):
        return "detail"
    if _kw_hit("list", segs):
        return "list"

    # --- 第 2 路: REST 结构兜底 ---
    if m == "DELETE":
        return "delete"
    if m in ("PUT", "PATCH"):
        return "update"
    if m == "POST":
        return "create"      # 国内系统 RPC 风格全 POST, 新增居多
    if m == "GET":
        # GET /res/{id} -> detail; GET /res -> list
        return "detail" if re.search(r"\{[^}]+\}/?$", path) else "list"
    return "action"


def _truncate(obj, max_len=4000):
    s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    if len(s) > max_len:
        return s[:max_len] + " …[truncated]"
    return s


def load_crud_overrides(kb_dir) -> dict:
    """读 kb/crud_overrides.json(人工校正层, 优先级最高)。不存在则返回 {}。"""
    from pathlib import Path
    p = Path(kb_dir) / "crud_overrides.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("overrides", data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def merge_captures(captures: list, profile: dict = None, crud_overrides: dict = None) -> dict:
    """
    captures: 每个元素 {
      method, url, path, query(dict), request_headers, request_body,
      status, response_body, source, ts
    }
    返回归并后的端点字典, key = METHOD path_template。
    """
    prefixes = (profile or {}).get("service_prefixes", [])
    groups = {}
    for c in captures:
        method = (c.get("method") or "GET").upper()
        path = c.get("path") or urlsplit(c.get("url", "")).path
        if is_static_asset(path):
            continue          # 静态资源(图片/视频/字体/地图)不是 API, 不入目录
        tmpl = templatize_path(path)
        key = f"{method} {tmpl}"
        g = groups.setdefault(key, {
            "method": method, "path_template": tmpl, "count": 0,
            "query_params": {}, "path_params": [], "body_keys": set(),
            "samples": [], "statuses": set(), "source": c.get("source", "traffic"),
        })
        g["count"] += 1
        g["statuses"].add(c.get("status"))
        for k, v in (c.get("query") or {}).items():
            g["query_params"].setdefault(k, set()).add(str(v))
        # path params
        for seg in path.split("/"):
            if seg and _is_id_segment(seg) and "{id}" not in g["path_params"]:
                g["path_params"].append("{id}")
        # body keys
        rb = c.get("request_body")
        if isinstance(rb, dict):
            for k in rb.keys():
                g["body_keys"].add(k)
        # sample (保留最近一条脱敏样本)
        if len(g["samples"]) < 3:
            g["samples"].append({
                "url": c.get("url"),
                "query": c.get("query"),
                "request_body": desensitize(rb),
                "response_sample": _truncate(desensitize(c.get("response_body"))),
                "status": c.get("status"),
            })
    # 收尾: set->list, 推断 service/resource/crud
    out = {}
    for key, g in groups.items():
        service, resource = extract_service_resource(g["path_template"], prefixes)
        out[key] = {
            "method": g["method"],
            "path_template": g["path_template"],
            "count": g["count"],
            "statuses": sorted(g["statuses"]),
            "source": g["source"],
            "x-kb-trusted": False,
            "x-kb-source": g["source"],
            "x-kb-crud": guess_crud(g["method"], g["path_template"], crud_overrides),
            "x-kb-service": service,
            "x-kb-resource": resource,
            "parameters": [
                {"in": "query", "name": k, "sample_values": sorted(v)[:5]} for k, v in g["query_params"].items()
            ] + [
                {"in": "path", "name": "id"} for _ in g["path_params"]
            ],
            "body_keys": sorted(g["body_keys"]),
            "samples": g["samples"],
        }
    return out


def build_api_catalog(merged: dict, profile: dict = None) -> dict:
    """把归并结果包成 OpenAPI 3.1 + x-kb-* 的 IR。"""
    paths = {}
    for key, ep in merged.items():
        p = ep["path_template"]
        paths.setdefault(p, {})[ep["method"].lower()] = {
            "summary": f"{ep['x-kb-crud']}:{ep['x-kb-resource'] or p}",
            "x-kb-source": ep["x-kb-source"],
            "x-kb-trusted": ep["x-kb-trusted"],
            "x-kb-crud": ep["x-kb-crud"],
            "x-kb-service": ep["x-kb-service"],
            "x-kb-resource": ep["x-kb-resource"],
            "parameters": ep["parameters"],
            "x-kb-body-keys": ep["body_keys"],
            "responses": {
                "200": {
                    "description": f"observed status(es): {ep['statuses']}",
                    "content": {"application/json": {"examples": ep["samples"]}},
                }
            },
        }
    title = (profile or {}).get("display_name") or (profile or {}).get("name") or "API"
    return {
        "openapi": "3.1.0",
        "info": {"title": f"{title} — discovered API catalog", "version": "0.1.0-p0"},
        "x-kb-generated-at": datetime.now(timezone.utc).isoformat(),
        "x-kb-endpoint-count": len(merged),
        "paths": paths,
    }


def har_to_captures(har_path: str) -> list:
    """解析 HAR 文件为内部 capture 列表(仅取 XHR/fetch)。"""
    with open(har_path, "r", encoding="utf-8") as f:
        har = json.load(f)
    out = []
    for entry in har.get("log", {}).get("entries", []):
        req = entry.get("request", {})
        resp = entry.get("response", {})
        url = req.get("url", "")
        method = req.get("method", "GET")
        q = {}
        for qp in req.get("queryString", []):
            q[qp.get("name")] = qp.get("value")
        rb = None
        post = req.get("postData", {})
        if post.get("text"):
            try:
                rb = json.loads(post["text"])
            except Exception:
                rb = post["text"]
        rbody = None
        content = resp.get("content", {})
        if content.get("text"):
            try:
                rbody = json.loads(content["text"])
            except Exception:
                rbody = content["text"]
        out.append({
            "method": method, "url": url, "path": urlsplit(url).path, "query": q,
            "request_headers": {h["name"]: h["value"] for h in req.get("headers", [])},
            "request_body": rb, "status": resp.get("status"), "response_body": rbody,
            "source": "har", "ts": entry.get("startedDateTime"),
        })
    return out
