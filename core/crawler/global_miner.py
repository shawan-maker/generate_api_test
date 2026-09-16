"""
discovery/global_miner.py —— G4 全局参数挖掘 (Global Parameter Miner)

场景 (方案设计 §7.3 G4):
  项目名 / 用户ID / 资源池名 等参数应在多处复用, 获取一次后存为全局, 全项目脚本共用,
  且最好能**自动识别**哪些参数是"全局的"。

识别思路 (四重信号):
  ① 跨模块共现  : 同一字段名出现在 >= N 个不同模块(service/resource)中 -> 候选。
  ② 取值稳定性  : 该字段在多个响应样本中取值恒定 -> 高置信"静态全局"。
  ③ 命名启发    : 字段名命中 GLOBAL_NAME_HINTS(project/user/tenant/pool/region/zone…)。
  ④ LLM 二审    : llm_review(candidates) -> 确认列表(可插拔; 默认走启发式, 留接口给 LLM)。

静态全局 vs 动态上下文:
  * globals.yaml   : 用户一次性配置的静态常量(默认项目名/Region/资源池…), 人工 review 后固化。
  * context.json   : 运行时取得并随会话变化的值(登录返回 userId/tenantId/token; 项目列表返回
                     projectId/资源池…), 自动回填, 敏感字段脱敏。

护栏: 登录/项目列表等返回值自动回填 context, 密钥类字段使用 desensitize() 脱敏后落盘。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

# 命中即视为"疑似全局"的字段名片段(小写匹配)
GLOBAL_NAME_HINTS = {
    "projectid", "project", "projectcode", "project_code",
    "tenantid", "tenant",
    "userid", "user", "username", "accountid", "account",
    "poolid", "resourcepoolid", "resourcepool", "poolname",
    "regionid", "region", "zoneid", "zone",
    "orgid", "organizationid", "organization",
    "workspaceid", "workspace", "domainid", "domain",
    "vpcid", "subnetid", "clusterid", "namespace", "namespaceid",
}
# 这些更像"认证态/会话态" -> 归为动态 context 而非静态 globals
AUTH_LIKE = {"userid", "user", "username", "accountid", "account", "tenantid",
             "tenant", "token", "accesstoken", "refreshtoken", "sessionid"}
# 分页等明显非全局字段, 直接排除
PAGINATION = {"pagenum", "pagesize", "page", "size", "offset", "limit", "sort", "order"}


# ----------------------------------------------------------------------------
# 候选发现
# ----------------------------------------------------------------------------
def _module_of(ep: dict) -> tuple:
    return (ep.get("x-kb-service", ""), ep.get("x-kb-resource", ""))


def _walk_values(obj, key: str, out: list):
    """递归收集 obj 中所有 key == key 的值。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                out.append(v)
            _walk_values(v, key, out)
    elif isinstance(obj, list):
        for it in obj:
            _walk_values(it, key, out)


def _extract_sample_values(ep: dict, key: str) -> list:
    vals = []
    for meth in ep.values() if isinstance(ep, dict) else []:
        if not isinstance(meth, dict):
            continue
        for bk in meth.get("x-kb-body-keys", []):
            if bk == key:
                vals.append("<body>")  # 仅标记出现, 不取具体值
        resp = meth.get("responses", {})
        for code, body in resp.items():
            content = (body or {}).get("content", {})
            for ct, cv in (content or {}).items():
                for ex in (cv or {}).get("examples", []):
                    rs = ex.get("response_sample")
                    if rs and isinstance(rs, str):
                        try:
                            _walk_values(json.loads(rs), key, vals)
                        except Exception:
                            pass
    return vals


def mine_globals(catalog: dict, *,
                 project_id: str = "",
                 llm_review: Optional[Callable[[list], list]] = None,
                 min_modules: int = 2) -> dict:
    """扫描 IR catalog, 返回候选全局参数与分类草稿。"""
    paths = catalog.get("paths", {})
    # 收集每个字段名 -> 出现的模块集合 + 取值样本
    field_modules: dict[str, set] = {}
    field_values: dict[str, list] = {}
    for p, methods in paths.items():
        for meth, ep in methods.items():
            if not isinstance(ep, dict):
                continue
            mod = _module_of(ep)
            # 请求体字段
            for bk in ep.get("x-kb-body-keys", []):
                field_modules.setdefault(bk, set()).add(mod)
            # 响应样本中的字段值(稳定性判断)
    # 二次扫描: 用已收集的字段名去响应样本里取值
    candidate_keys = set(field_modules.keys())
    for k in list(candidate_keys):
        field_values[k] = []
    for p, methods in paths.items():
        for meth, ep in methods.items():
            if not isinstance(ep, dict):
                continue
            for k in candidate_keys:
                field_values[k].extend(_extract_sample_values({meth: ep}, k))

    candidates = []
    for name, mods in field_modules.items():
        low = name.lower()
        if low in PAGINATION:
            continue
        if len(mods) < min_modules:
            continue
        values = [v for v in field_values.get(name, []) if v not in (None, "<body>")]
        unique_vals = list(dict.fromkeys(values))
        stable = len(unique_vals) == 1 and len(unique_vals) > 0
        conf = 0.5
        if low in GLOBAL_NAME_HINTS:
            conf += 0.3
        if stable:
            conf += 0.2
        conf = min(conf, 1.0)
        candidates.append({
            "field": name,
            "modules": sorted([f"{a}/{b}" for a, b in mods]),
            "module_count": len(mods),
            "sample_values": unique_vals[:5],
            "stable": stable,
            "confidence": round(conf, 2),
            "name_hint": low in GLOBAL_NAME_HINTS,
        })
    candidates.sort(key=lambda c: (-c["confidence"], -c["module_count"]))

    # ④ LLM 二审(可插拔)
    confirmed = candidates
    if llm_review is not None:
        confirmed = llm_review(candidates)

    # 分类: 静态 globals.yaml vs 动态 context.json
    globals_draft, context_backfill = [], []
    for c in confirmed:
        low = c["field"].lower()
        if low in AUTH_LIKE:
            context_backfill.append(c)
        elif c["stable"]:
            globals_draft.append(c)
        else:
            context_backfill.append(c)  # 取值会变 -> 运行时回填 context

    return {
        "project_id": project_id,
        "candidates": candidates,
        "confirmed": confirmed,
        "globals_draft": globals_draft,
        "context_backfill": context_backfill,
    }


def _likely_keys(paths) -> set:
    """返回 catalog 中曾出现过的所有 body-key 名(供样本取值扫描)。"""
    ks = set()
    for p, methods in paths.items():
        for meth, ep in methods.items():
            if isinstance(ep, dict):
                ks.update(ep.get("x-kb-body-keys", []))
    return ks


# ----------------------------------------------------------------------------
# 落盘
# ----------------------------------------------------------------------------
def _mini_yaml(data: list) -> str:
    """极简 YAML 序列化(避免强依赖 pyyaml)。"""
    lines = ["# 全局参数草稿(由 discovery/global_miner.py 生成, 待人工 review)",
             "# 静态常量: 用户一次性配置, 全项目脚本共用"]
    for c in data:
        lines.append(f"- field: {c['field']}")
        lines.append(f"  module_count: {c['module_count']}")
        lines.append(f"  confidence: {c['confidence']}")
        lines.append(f"  stable: {str(c['stable']).lower()}")
        if c.get("sample_values"):
            lines.append(f"  sample_values: {json.dumps(c['sample_values'], ensure_ascii=False)}")
        lines.append(f"  modules: {json.dumps(c['modules'], ensure_ascii=False)}")
    return "\n".join(lines) + "\n"


def write_globals_yaml(globals_draft: list, path: str) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_mini_yaml(globals_draft), encoding="utf-8")
    return str(p)


_SENSITIVE = {"token", "accesstoken", "refreshtoken", "password", "secret",
              "secretkey", "secretid", "apikey", "cookie", "authorization"}


def desensitize(key: str, value) -> object:
    """敏感字段脱敏: 保留前 2 后 2, 中间打码。"""
    if not isinstance(value, str):
        return value
    low = key.lower()
    if any(s in low for s in _SENSITIVE):
        if len(value) <= 6:
            return "***"
        return value[:2] + "***" + value[-2:]
    return value


def backfill_context(session_values: dict, path: str, *, mask: bool = True) -> dict:
    """把登录/项目列表等返回值写回 context.json(脱敏); 与已有内容合并。"""
    p = Path(path)
    ctx = {}
    if p.exists():
        try:
            ctx = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            ctx = {}
    for k, v in session_values.items():
        ctx[k] = desensitize(k, v) if mask else v
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx


# ----------------------------------------------------------------------------
# 离线自测
# ----------------------------------------------------------------------------
def _synthetic_catalog() -> dict:
    def ep(svc, res, body_keys, sample_value=None):
        e = {"x-kb-service": svc, "x-kb-resource": res, "x-kb-body-keys": body_keys,
             "responses": {}}
        if sample_value is not None:
            e["responses"] = {"200": {"content": {"application/json": {
                "examples": [{"response_sample": json.dumps(sample_value)}]}}}}
        return e
    return {"paths": {
        "/a": {"post": ep("draco", "project", ["projectId", "pageNum"],
                          {"projectId": "PROJ-1", "name": "x"})},
        "/b": {"get": ep("pegasi", "vm", ["projectId", "regionId"],
                         {"projectId": "PROJ-1", "id": "v1"})},
        "/c": {"post": ep("karma", "disk", ["projectId", "size"],
                          {"projectId": "PROJ-1"})},
        "/u": {"get": ep("draco", "user", ["userId", "userName"],
                         {"userId": "U-100", "userName": "alice"})},
        "/p": {"get": ep("draco", "policy", ["userId", "type"],
                         {"userId": "U-100"})},
        "/r": {"get": ep("sona", "req", ["requestId"],
                         {"requestId": "R-111"})},
        "/r2": {"get": ep("sona", "req2", ["requestId"],
                          {"requestId": "R-999"})},
    }}


def _self_test():
    cat = _synthetic_catalog()
    res = mine_globals(cat, project_id="estack", min_modules=2)
    cand = {c["field"]: c for c in res["candidates"]}
    print("\n===== G4 global_miner 自测 =====")
    for c in res["candidates"]:
        print(f"  {c['field']:10s} mods={c['module_count']} stable={c['stable']} "
              f"conf={c['confidence']} -> {'globals' if c in res['globals_draft'] else 'context'}")
    # 断言
    assert "projectId" in cand, "projectId 应被识别为候选"
    assert cand["projectId"]["stable"] is True, "projectId 取值应稳定"
    assert cand["projectId"] in res["globals_draft"], "projectId 应进 globals.yaml 草稿(静态)"
    assert "userId" in cand, "userId 应被识别"
    assert cand["userId"] in res["context_backfill"], "userId 应进 context(认证态)"
    assert "requestId" in cand, "requestId 跨两模块应被识别"
    assert cand["requestId"] not in res["globals_draft"], "requestId 取值变化不应进静态 globals"
    assert "pageNum" not in cand, "分页字段应被排除"

    # 落盘 + 脱敏验证
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    yp = write_globals_yaml(res["globals_draft"], str(tmp / "globals.yaml"))
    cp = backfill_context({"userId": "U-100", "token": "abcdefghijklmnop"},
                          str(tmp / "context.json"))
    assert cp["token"] == "ab***op", f"token 应脱敏, 实际 {cp['token']}"
    print(f"✅ G4 自测通过: 候选={len(res['candidates'])}, 静态={len(res['globals_draft'])}, "
          f"动态={len(res['context_backfill'])}; 已写 {yp}")

    # 顺带在真实 estack catalog 上跑一遍(不硬断言, 仅确认不崩溃且能产出)
    try:
        real = json.load(open("projects/estack/kb/api_catalog.json", encoding="utf-8"))
        r2 = mine_globals(real, project_id="estack")
        print(f"[真实 catalog] 候选全局参数: "
              f"{[c['field'] for c in r2['candidates'][:8]]}")
    except Exception as e:
        print(f"[真实 catalog] 扫描跳过: {e}")


if __name__ == "__main__":
    _self_test()
