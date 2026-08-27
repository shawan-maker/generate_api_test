"""
lib/resolver.py —— G3 前置依赖解析 / Test Data Fabric

场景 (方案设计 §7.3 G3):
  "添加云主机" 表单近 20 项, 但底层 API 需要 10+ 前置接口的取值(子网/镜像/规格/项目/资源池…)
  才能成功。逐字段手填既慢又易错。

设计:
  * 把"目标 create 端点的参数"声明为一组依赖槽 DepSpec。
  * resolver.provide(deps, ctx) 按依赖图逐个取值:
      - literal/const : 直接来自 ctx 的 {{var}} 或字面量;
      - list          : 调 api 层(经 get_fn)拿到枚举/列表响应, 用 jsonpath 抽取取值池,
                        若本槽是受 G2 组合约束的枚举字段, 则套用 param_constraints 排除非法值;
  * 前置结果**不落独立脚本**, 而是作为"取值节点"就地被消费, 直接并入目标 body。
  * 抽取到的值回写 ctx, 供后续 dep 用 {{var}} 引用(依赖图传递)。

与 G2 的衔接:
  Resolver 在 list 槽命中枚举字段时, 读取 kb/param_constraints.json 的
  invalidValues / incompatibilities, 自动剔除"全局无效值"与"与已选值冲突的组合值"。

离线可用: call_api 可注入(mock), 不依赖真实服务器; jsonpath 抽取优先用 jsonpath_ng,
  缺失时回退到内置 `$..key` 简易抽取器(覆盖绝大多数列表响应)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from jsonpath_ng import parse as _jsonpath_parse
    _HAS_JSONPATH = True
except Exception:
    _HAS_JSONPATH = False


# ----------------------------------------------------------------------------
# 依赖槽声明
# ----------------------------------------------------------------------------
@dataclass
class DepSpec:
    """目标 create body 的一个参数槽。"""
    name: str                     # 目标字段名
    kind: str = "literal"         # literal | const | list
    value: Any = None             # literal/const 用
    # list 类型: 通过 api 层取取值池
    service: str = ""
    resource: str = ""
    crud: str = "list"
    jsonpath: str = "$..id"       # 从响应中抽取取值
    pick: str = "first"           # first | random | all
    filter: Optional[dict] = None # 抽取前对响应条目做等值过滤 {field: value}
    combo_field: Optional[str] = None  # 若为受 G2 约束的枚举字段, 填字段名以套用约束


# call_api(project_id, service, resource, crud, client, **kwargs) -> ApiResponse
CallApi = Callable[[str, str, str, str, Any], Any]


# ----------------------------------------------------------------------------
# 简易 jsonpath 抽取(回退)
# ----------------------------------------------------------------------------
def _simple_extract(path: str, obj: Any) -> list:
    """支持 `$..key`(递归收集所有 key 的值) 与 `$`(整体)。"""
    key = None
    if path.startswith("$.."):
        key = path[3:]
    if key is None:
        if path.strip() in ("$", "$."):
            return [obj]
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == key:
                    out.append(v)
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)
    walk(obj)
    return out


def _extract(path: str, obj: Any) -> list:
    if _HAS_JSONPATH:
        try:
            return [m.value for m in _jsonpath_parse(path).find(obj)]
        except Exception:
            pass
    return _simple_extract(path, obj)


# ----------------------------------------------------------------------------
# Resolver
# ----------------------------------------------------------------------------
class Resolver:
    def __init__(
        self,
        project_id: str,
        *,
        client=None,
        client_factory: Optional[Callable[[], Any]] = None,
        call_api: Optional[CallApi] = None,
        ctx: Optional[dict] = None,
        constraints_dir: Optional[str] = None,
        log_fn=print,
    ):
        self.project_id = project_id
        self.client = client
        self.client_factory = client_factory
        self.call_api = call_api
        self.ctx = dict(ctx or {})
        self.constraints_dir = Path(constraints_dir) if constraints_dir else None
        self.log = log_fn
        self._constraints_cache: dict = {}

    # ---- 模板变量( {{var}} ) ----
    @staticmethod
    def _render(value, ctx: dict):
        if isinstance(value, str) and "{{" in value:
            for k, v in ctx.items():
                value = value.replace("{{%s}}" % k, str(v))
        return value

    # ---- G2 组合约束加载 ----
    def _load_constraints(self, resource: str) -> dict:
        if self.constraints_dir is None:
            return {}
        p = Path(self.constraints_dir) / f"{resource}.json"
        if not p.exists():
            return {}
        if str(p) not in self._constraints_cache:
            self._constraints_cache[str(p)] = json.loads(p.read_text(encoding="utf-8"))
        return self._constraints_cache[str(p)]

    def _filter_by_constraints(self, combo_field: str, candidates: list, resource: str) -> list:
        cons = self._load_constraints(resource)
        if not cons:
            return candidates
        invalid = {iv["value"] for iv in cons.get("invalidValues", [])
                   if iv["field"] == combo_field}
        incompat = cons.get("incompatibilities", [])
        kept = []
        for cand in candidates:
            if cand in invalid:
                self.log(f"[resolver]  排除全局无效值 {combo_field}={cand}")
                continue
            drop = False
            for inc in incompat:
                # inc 形如 (fieldA,valueA,fieldB,valueB); 若本槽是其中一侧且对侧取值已定且冲突 -> 排除
                if inc["fieldA"] == combo_field and inc["valueA"] == cand:
                    other = inc["fieldB"]
                    if self.ctx.get(other) == inc["valueB"]:
                        drop = True
                        break
                if inc["fieldB"] == combo_field and inc["valueB"] == cand:
                    other = inc["fieldA"]
                    if self.ctx.get(other) == inc["valueA"]:
                        drop = True
                        break
            if drop:
                self.log(f"[resolver]  排除冲突组合 {combo_field}={cand} "
                         f"(与 {other}={self.ctx.get(other)} 不兼容)")
                continue
            kept.append(cand)
        return kept

    # ---- 取值池 ----
    def _fetch_pool(self, dep: DepSpec) -> list:
        if self.call_api is None:
            raise RuntimeError("未注入 call_api (live 模式需提供 get_fn 包装)")
        client = self.client or (self.client_factory() if self.client_factory else None)
        resp = self.call_api(self.project_id, dep.service, dep.resource, dep.crud, client)
        body = resp.json if hasattr(resp, "json") else resp
        items = body
        # 常见响应包裹: entity.list / data.list / data / list
        if isinstance(body, dict):
            for k in ("list", "data", "entity", "records", "items"):
                v = body.get(k)
                if isinstance(v, list):
                    items = v
                    break
            else:
                items = [body]
        if not isinstance(items, list):
            items = [items]
        # 预过滤
        if dep.filter:
            items = [it for it in items if isinstance(it, dict)
                     and all(it.get(fk) == fv for fk, fv in dep.filter.items())]
        vals = []
        for it in items:
            got = _extract(dep.jsonpath, it)
            vals.extend(got)
        # 去重保序
        seen = set(); uniq = []
        for v in vals:
            if v not in seen:
                seen.add(v); uniq.append(v)
        return uniq

    def _pick(self, vals: list, pick: str):
        if not vals:
            return None
        if pick == "random":
            import random
            return random.choice(vals)
        if pick == "all":
            return vals
        return vals[0]

    # ---- 主入口 ----
    def provide(self, deps: list[DepSpec], ctx: Optional[dict] = None) -> dict:
        if ctx is not None:
            self.ctx.update(ctx)
        body = {}
        for dep in deps:
            if dep.kind in ("literal", "const"):
                body[dep.name] = self._render(dep.value, self.ctx)
                self.ctx[dep.name] = body[dep.name]
                self.log(f"[resolver] {dep.name} = {body[dep.name]!r} (literal)")
            elif dep.kind == "list":
                # 上游已在 ctx 中解析过的值直接透传, 不重复拉取(依赖图传递)
                if self.ctx.get(dep.name) is not None:
                    v = self.ctx[dep.name]
                    body[dep.name] = v
                    self.log(f"[resolver] {dep.name} = {v!r} (ctx 透传)")
                else:
                    pool = self._fetch_pool(dep)
                    if dep.combo_field:
                        pool = self._filter_by_constraints(dep.combo_field, pool, dep.resource)
                    chosen = self._pick(pool, dep.pick)
                    body[dep.name] = chosen
                    if chosen is not None:
                        self.ctx[dep.name] = chosen
                    self.log(f"[resolver] {dep.name} = {chosen!r} (pool size={len(pool)})")
            else:
                raise ValueError(f"未知 dep.kind: {dep.kind}")
        return body


# ----------------------------------------------------------------------------
# 离线自测
# ----------------------------------------------------------------------------
def _mock_call_api(project_id, service, resource, crud, client):
    """返回带 .json 属性的假响应。"""
    class _R:
        def __init__(self, data): self._d = data
        @property
        def json(self): return self._d
        @property
        def success(self): return True
    pools = {
        ("vpc", "vpc"): {"list": [{"id": "vpc-1"}, {"id": "vpc-2"}]},
        ("net", "subnet"): {"list": [{"id": "sub-1"}, {"id": "sub-2"}]},
        ("img", "image"): {"list": [{"id": "img-1"}]},
        ("flavor", "flavor"): {"list": [{"id": "flavor-a"}, {"id": "flavor-b"}]},
        ("region", "region"): {"list": [{"id": "r1"}, {"id": "r2"}]},
    }
    return _R(pools[(service, resource)])


def _write_test_constraints(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "resource": "addhost",
        "validCombos": [],
        "incompatibilities": [
            {"fieldA": "flavor", "valueA": "flavor-b", "fieldB": "region", "valueB": "r2",
             "reason": "两值各自可行但该组合从未成功(交互冲突)"}
        ],
        "invalidValues": [],
        "triedCombos": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def _self_test():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    _write_test_constraints(tmp / "addhost.json")

    # 依赖图: 添加云主机需要的前置取值
    deps = [
        DepSpec("projectId", kind="literal", value="{{projectId}}"),
        DepSpec("vpcId", kind="list", service="vpc", resource="vpc", crud="list", jsonpath="$..id"),
        DepSpec("subnetId", kind="list", service="net", resource="subnet", crud="list", jsonpath="$..id"),
        DepSpec("imageId", kind="list", service="img", resource="image", crud="list", jsonpath="$..id"),
        DepSpec("flavor", kind="list", service="flavor", resource="flavor", crud="list",
                jsonpath="$..id", combo_field="flavor"),
        DepSpec("region", kind="list", service="region", resource="region", crud="list",
                jsonpath="$..id", combo_field="region"),
    ]
    print("\n===== G3 resolver 自测 (空 ctx) =====")
    r1 = Resolver("estack", call_api=_mock_call_api, constraints_dir=str(tmp),
                  ctx={"projectId": "P-001"}, log_fn=print)
    body1 = r1.provide(deps)
    assert body1["projectId"] == "P-001", body1
    assert body1["vpcId"] == "vpc-1", body1
    assert body1["subnetId"] == "sub-1", body1
    assert body1["imageId"] == "img-1", body1
    assert body1["flavor"] == "flavor-a", body1
    # flavor=flavor-a 时不与任何 region 冲突 -> 取首个 r1
    assert body1["region"] == "r1", body1

    print("\n===== G3 resolver 自测 (ctx 预置 flavor=flavor-b, 验证约束拦截) =====")
    r2 = Resolver("estack", call_api=_mock_call_api, constraints_dir=str(tmp),
                  ctx={"projectId": "P-002", "flavor": "flavor-b"}, log_fn=print)
    body2 = r2.provide(deps)
    # flavor 已是 flavor-b, region 候选 [r1,r2] 中 r2 与 flavor-b 冲突 -> 必须排除, 取 r1
    assert body2["flavor"] == "flavor-b", body2
    assert body2["region"] == "r1", f"约束应排除 r2, 实际 {body2}"
    print("✅ G3 自测通过: 前置取值并装 body, 且 G2 组合约束成功拦截冲突 region=r2")


if __name__ == "__main__":
    _self_test()
