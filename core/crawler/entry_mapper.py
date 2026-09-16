"""
entry_mapper.py — 入口 URL ↔ 业务模块映射器。

给定一个入口 URL（例如用户登录后所在的功能页），推断它对应什么业务模块，
从而生成正确的 CRUD 脚本。

核心逻辑:
  1. 从 URL 路径段提取关键词（如 user-center/user-manage/user → ["user","manage","center"]）
  2. 用关键词匹配 catalog 中的 service/resource/endpoint
  3. 综合评分选最佳匹配

从方案出发: 用户在 "用户管理" 页 -> 期望的 CRUD 脚本是用户的增删改查,
而不是 AccessKey 的。
"""
import json
import re
from pathlib import Path
from typing import Optional


def parse_url_segments(url: str) -> list:
    """从 URL 中提取有意义的路径段（去重、去静态词）。"""
    # 去掉协议和域名
    path = re.sub(r'^https?://[^/]+', '', url).split('?')[0].rstrip('/')
    segs = [s.lower() for s in path.split('/') if s]
    # 去停用词
    stopwords = {'static', 'web', 'api', 'v1', 'v2', 'v3', 'list', 'page', 'index', 'html'}
    return [s for s in segs if s not in stopwords and len(s) > 1]


# ---- 路径段 → 业务关键词映射 ----
_URL_TO_BIZ = {
    # estack 用户管理
    "user": "users", "user-center": "users", "user-manage": "users",
    # 用户组
    "group": "groups",
    # 角色
    "role": "roles",
    # 权限
    "authority": "authority", "auth": "authority",
    # 项目
    "project": "projects", "project-center": "projects",
    # 配额
    "quota": "quotas",
    # 容量
    "capacity": "capacity",
    # 日志
    "log": "logs",
    # AccessKey
    "accesskey": "accesskey", "ak": "accesskey",
    # 菜单
    "menu": "menu",
    # 云主机
    "vm": "ecs", "compute": "compute", "ecs": "ecs",
    # 镜像
    "image": "ims", "ims": "ims",
    # 网络
    "vpc": "vpc", "network": "network", "eip": "eip",
    # 存储
    "volume": "volume", "ebs": "volume", "disk": "volume",
    # 备份
    "backup": "backup", "cbs": "backup",
    # 安全组
    "security": "security_group", "sg": "security_group",
}


def guess_business_from_url(url: str, catalog: dict = None, verbose: bool = False) -> dict:
    """
    从入口 URL 推断业务模块。
    返回: {business_name, resource_key, score, matched_segments, missing_crud}
    """
    segs = parse_url_segments(url)
    if verbose:
        print(f"URL 路径段: {segs}")

    # 1. 匹配 URL_TO_BIZ
    matched_biz = set()
    for seg in segs:
        if seg in _URL_TO_BIZ:
            matched_biz.add(_URL_TO_BIZ[seg])

    if verbose:
        print(f"匹配的业务: {matched_biz}")

    # 2. 用 catalog 交叉验证
    catalog_hits = {}
    if catalog:
        for p, methods in catalog.get("paths", {}).items():
            for method, ep in methods.items():
                svc = (ep.get("x-kb-service") or "").lower()
                res = (ep.get("x-kb-resource") or "").lower()
                # 检查是否与 URL 段匹配
                for seg in segs:
                    if seg in svc or seg in res:
                        key = f"{svc}/{res}"
                        catalog_hits.setdefault(key, {"count": 0, "segments": set(), "cruds": set()})
                        catalog_hits[key]["count"] += 1
                        catalog_hits[key]["segments"].add(seg)
                        catalog_hits[key]["cruds"].add(ep.get("x-kb-crud", ""))

    if verbose and catalog_hits:
        print("catalog 命中:")
        for k, v in sorted(catalog_hits.items(), key=lambda x: -x[1]["count"])[:5]:
            print(f"  {k:30} count={v['count']} cruds={v['cruds']} segs={v['segments']}")

    # 3. 综合评分选最佳
    candidates = []
    # 来自 URL_TO_BIZ 的候选
    for biz in matched_biz:
        score = 100 - len(biz) * 2  # 短词优先
        candidates.append({"business": biz, "score": score, "source": "url_kb", "segments": list(matched_biz)})

    # 来自 catalog 的候选
    for key, v in catalog_hits.items():
        svc, res = key.split("/") if "/" in key else (key, key)
        biz = res or svc
        score = v["count"] * 10 + len(v["segments"]) * 5
        candidates.append({
            "business": biz,
            "resource_key": key,
            "score": score,
            "source": "catalog",
            "segments": list(v["segments"]),
            "cruds": list(v["cruds"]),
        })

    # 去重、排序
    seen = set()
    deduped = []
    for c in sorted(candidates, key=lambda x: -x["score"]):
        if c["business"] not in seen:
            seen.add(c["business"])
            deduped.append(c)

    best = deduped[0] if deduped else {"business": "unknown", "score": 0, "source": "none", "segments": segs}

    # 4. 检查 CRUD 完整性（无论 best 来自 url_kb 还是 catalog）
    missing_crud = []
    has_crud = {"create": False, "list": False, "detail": False, "delete": False}
    if catalog:
        biz_name = best.get("business", "")
        for p, methods in catalog.get("paths", {}).items():
            for method, ep in methods.items():
                res = (ep.get("x-kb-resource") or "").lower()
                svc = (ep.get("x-kb-service") or "").lower()
                if res == biz_name or res.replace("-", "") == biz_name or svc == biz_name:
                    has_crud[ep.get("x-kb-crud", "")] = True
        for crud_type in ["create", "list", "detail", "delete"]:
            if not has_crud.get(crud_type):
                missing_crud.append(crud_type)
        best["has_crud"] = has_crud

    best["missing_crud"] = missing_crud

    return best


def deduce_entry_point(url: str) -> str:
    """
    简化接口: 只返回业务名。
    如 "https://.../user-center/user-manage/user" → "users"
    """
    result = guess_business_from_url(url)
    return result.get("business", "unknown")


if __name__ == "__main__":
    # 测试
    urls = [
        "https://console-estack-syyhb.cmecloud.cn/estack/web/estack/user-center/user-manage/user",
        "https://console-estack-syyhb.cmecloud.cn/estack/web/estack/accesskey",
        "https://10.151.37.249/estack/web/ecm-compute-static/vm/list?productType=vm",
        "https://console-estack-syyhb.cmecloud.cn/estack/web/estack/role-manage/role",
    ]
    catalog = json.load(open(Path(__file__).resolve().parents[1] / "projects" / "estack" / "kb" / "api_catalog.json"))

    for url in urls:
        print(f"\n{'='*60}")
        print(f"URL: {url}")
        result = guess_business_from_url(url, catalog, verbose=True)
        print(f"结果: business={result.get('business')} score={result.get('score')} "
              f"missing_crud={result.get('missing_crud')}")
