"""
discovery/combo_finder.py —— G2 有效组合发现器 (Valid-Combination Finder)

场景 (方案设计 §7.3 G2):
  某"新建"模块的表单有多项输入, 每项是一个下拉框(枚举), 各有 3-4 个选项,
  但后端规格/限制导致 **只有 1-2 种组合能新增成功**。
  本模块自动找出"能成功的组合", 落地到 kb/param_constraints.json,
  供 G3 resolver 在组装 body 时规避非法组合、供 flows 选参。

算法 (四步走):
  ① 种子(seed):      从已知成功样本或各字段 default/首个选项出发。
  ② 错误码引导剪枝:  probe 失败时, 解析 errorCode/errorMessage 指向的字段,
                     仅在该字段上换选项, 其余保持不动 —— 把失败局部化。
  ③ 正交两两播种:     对每对字段枚举其选项组合(其余固定为基线),
                     覆盖全部两两交互, 不退化成全笛卡尔积。
  ④ 缓存:            每次 probe 结果写入 param_constraints.json 的 triedCombos,
                     重跑跳过已试, 并累积 validCombos / incompatibilities。

护栏 (destructive_probe):
  优先走"校验接口"(validate_fn / 独立的 check 端点, 非破坏性);
  若无校验接口, 则走 create→delete(创建成功后立即清理), 并打标 destructive_probe=true。

设计为数据源无关:
  - 表单字段枚举来自 form_extractor 的 function_surface.json, 或手写的
    kb/form_specs/<service>__<resource>.json; 缺省时由调用方显式传入 FormSpec。
  - transport 是一个可注入的探针函数 probe(combo)->ProbeResult, 便于离线 dry-run。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------
@dataclass
class FieldSpec:
    """一个枚举表单字段。"""
    name: str
    options: list               # 可选项列表
    required: bool = True
    default: Optional[object] = None

    def baseline(self):
        if self.default is not None:
            return self.default
        return self.options[0] if self.options else None


@dataclass
class ProbeResult:
    """一次组合探测的结果。"""
    success: bool
    error_code: Optional[str] = None
    error_msg: Optional[str] = None
    hint_field: Optional[str] = None   # 错误指向的字段(用于剪枝)
    raw: Optional[dict] = None


# transport: probe(combo: dict) -> ProbeResult
Transport = Callable[[dict], ProbeResult]


# ----------------------------------------------------------------------------
# 组合发现器
# ----------------------------------------------------------------------------
class ComboFinder:
    def __init__(
        self,
        fields: list[FieldSpec],
        transport: Transport,
        *,
        label: str = "",
        destructive: bool = False,
        cache_path: Optional[str] = None,
        max_probes: int = 400,
        log_fn=print,
    ):
        self.fields = fields
        self.fname = {f.name: f for f in fields}
        self.transport = transport
        self.label = label or ",".join(f.name for f in fields)
        self.destructive = destructive
        self.cache_path = Path(cache_path) if cache_path else None
        self.max_probes = max_probes
        self.log = log_fn
        self._tried: dict[str, dict] = {}     # combo_key -> result dict
        self._probes = 0

    # ---- 工具 ----
    def _key(self, combo: dict) -> str:
        return "|".join(f"{f.name}={combo.get(f.name)!r}" for f in self.fields)

    def _probe(self, combo: dict) -> ProbeResult:
        k = self._key(combo)
        if k in self._tried:
            t = self._tried[k]
            return ProbeResult(t["success"], t.get("error_code"), t.get("error_msg"),
                               t.get("hint_field"), t.get("raw"))
        if self._probes >= self.max_probes:
            raise RuntimeError(f"已达 max_probes={self.max_probes}, 停止以避免无限探测")
        self._probes += 1
        r = self.transport(combo)
        self._tried[k] = {
            "combo": {f.name: combo.get(f.name) for f in self.fields},
            "success": r.success,
            "error_code": r.error_code,
            "error_msg": r.error_msg,
            "hint_field": r.hint_field,
            "raw": r.raw,
        }
        return r

    @staticmethod
    def _infer_hint(msg: Optional[str], fields) -> Optional[str]:
        """从错误文本中启发式定位字段名(离线/live 通用)。"""
        if not msg:
            return None
        low = msg.lower()
        for f in fields:
            if f.name.lower() in low:
                return f.name
        return None

    def _seed(self) -> dict:
        return {f.name: f.baseline() for f in self.fields}

    # ---- 主流程 ----
    def search(self) -> dict:
        self.log(f"[combo_finder] 开始搜索有效组合: {self.label} "
                 f"({len(self.fields)} 字段, destructive={self.destructive})")
        # ① 种子
        seed = self._seed()
        known_good = None
        r0 = self._probe(seed)
        if r0.success:
            known_good = dict(seed)
            self.log(f"[combo_finder] 种子即成功: {seed}")
        else:
            # ② 错误码引导剪枝: 逐字段定位可行值
            known_good = self._greedy_fix(dict(seed), r0)
        if known_good is None:
            self.log("[combo_finder] ⚠️ 未找到任何成功组合 (可能规格极严或字段枚举不全)")
        else:
            self.log(f"[combo_finder] 找到一个成功基线: {known_good}")

        # ③ 正交两两播种
        self._pairwise_sweep(baseline=known_good or seed)

        # ④ 由缓存推导 validCombos / incompatibilities
        out = self._derive()
        out["destructive_probe"] = self.destructive
        out["label"] = self.label
        out["tried_count"] = len(self._tried)
        if self.cache_path:
            self._save(out)
        return out

    def _greedy_fix(self, combo: dict, last: ProbeResult) -> Optional[dict]:
        """沿失败方向逐字段换选项, 直到成功或穷尽。返回成功组合或 None。"""
        for _ in range(len(self.fields) + 2):
            if last.success:
                return combo
            hint = last.hint_field or self._infer_hint(last.error_msg, self.fields)
            if hint is None:
                # 无提示: 顺序尝试每个字段的每个选项
                for f in self.fields:
                    for opt in f.options:
                        if combo.get(f.name) == opt:
                            continue
                        trial = dict(combo); trial[f.name] = opt
                        if self._probe(trial).success:
                            return trial
                return None
            f = self.fname.get(hint)
            if f is None:
                return None
            # 在该字段上换选项, 其余保持
            for opt in f.options:
                if combo.get(f.name) == opt:
                    continue
                trial = dict(combo); trial[f.name] = opt
                res = self._probe(trial)
                if res.success:
                    return trial
                last = res
            # 该字段所有候选都失败 -> 死路
            return None
        return None

    def _pairwise_sweep(self, baseline: dict):
        """对每对字段枚举选项组合(其余固定为 baseline), 探测并记录。"""
        n = len(self.fields)
        for i in range(n):
            for j in range(i + 1, n):
                fi, fj = self.fields[i], self.fields[j]
                for vi in fi.options:
                    for vj in fj.options:
                        combo = dict(baseline)
                        combo[fi.name] = vi
                        combo[fj.name] = vj
                        self._probe(combo)

    def _derive(self) -> dict:
        tried = self._tried
        # 1. 收集成功组合
        valid_combos = []
        for t in tried.values():
            if t["success"]:
                valid_combos.append({
                    "fields": t["combo"],
                    "note": "discovered valid combination",
                })
        # 2. 每个字段的"可行取值集合": 出现在 >=1 个成功组合中的值
        feasible: dict[str, set] = {f.name: set() for f in self.fields}
        for t in tried.values():
            if t["success"]:
                for nm, val in t["combo"].items():
                    feasible.setdefault(nm, set()).add(val)
        # 3. 全局无效取值: 从未出现在任何成功组合中 -> 告知 G3 直接规避
        invalid_values = []
        for f in self.fields:
            for opt in f.options:
                if opt not in feasible.get(f.name, set()):
                    invalid_values.append({"field": f.name, "value": opt})
        # 4. 真·不兼容(交互效应): 两值各自都可行, 但该组合在所有完整组合中从未成功
        #    关键: 必须看"完整组合"层面 —— 同一对 (va,vb) 可能在一个上下文成功、
        #          另一个上下文失败(如 flavor-b+postpaid 在 region=r1 成功、r2 失败),
        #          那种情况不能误判为不兼容, 只有"该对值从未在任何完整组合成功"才算。
        incompat = []
        seen = set()
        pair_ever_valid = {}   # (na, va, nb, vb) -> 是否曾在某成功组合中同时出现
        for t in tried.values():
            if not t["success"]:
                continue
            c = t["combo"]
            names = list(c.keys())
            for a in range(len(names)):
                for b in range(a + 1, len(names)):
                    na, nb = names[a], names[b]
                    pair_ever_valid[(na, c[na], nb, c[nb])] = True
        for t in tried.values():
            if t["success"]:
                continue
            c = t["combo"]
            names = list(c.keys())
            for a in range(len(names)):
                for b in range(a + 1, len(names)):
                    na, nb = names[a], names[b]
                    va, vb = c[na], c[nb]
                    # 该 (va,vb) 是否曾在某成功组合中同时出现? 出现过则不是不兼容
                    if pair_ever_valid.get((na, va, nb, vb)):
                        continue
                    # 两值各自都可行 -> 这才是真正的组合冲突
                    if va in feasible.get(na, set()) and vb in feasible.get(nb, set()):
                        ik = (na, str(va), nb, str(vb))
                        if ik in seen:
                            continue
                        seen.add(ik)
                        incompat.append({
                            "fieldA": na, "valueA": va,
                            "fieldB": nb, "valueB": vb,
                            "reason": "两值各自可行但该组合从未成功(交互冲突)",
                        })
        return {
            "validCombos": valid_combos,
            "incompatibilities": incompat,
            "invalidValues": invalid_values,
            "triedCombos": list(tried.values()),
        }

    def _save(self, out: dict):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "resource": self.label,
            "validCombos": out["validCombos"],
            "incompatibilities": out["incompatibilities"],
            "triedCombos": out["triedCombos"],
            "destructive_probe": self.destructive,
        }
        self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"[combo_finder] 已写入: {self.cache_path}")


# ----------------------------------------------------------------------------
# 离线自测 (mock transport 藏一组有效组合, 验证算法能发现)
# ----------------------------------------------------------------------------
def _mock_rule(combo: dict) -> ProbeResult:
    """
    隐藏规则(含真·交互效应):
      * billing 必须 postpaid ( univariate 约束)
      * flavor 必须 ∈ {flavor-a, flavor-b} ( univariate 约束)
      * 但 flavor-b 与 region=r2 互斥(仅在该组合下失败 -> 真·不兼容)
    """
    billing = combo.get("billing")
    flavor = combo.get("flavor")
    region = combo.get("region")
    if billing != "postpaid":
        return ProbeResult(False, "E_BILLING", "billing must be postpaid", "billing")
    if flavor == "flavor-c":
        return ProbeResult(False, "E_FLAVOR", "flavor-c unsupported", "flavor")
    if flavor == "flavor-b" and region == "r2":
        return ProbeResult(False, "E_CONFLICT", "flavor-b not allowed with region r2", "region")
    return ProbeResult(True, None, None, None, {"id": "mock-123"})


def _self_test():
    fields = [
        FieldSpec("flavor", ["flavor-a", "flavor-b", "flavor-c"]),
        FieldSpec("billing", ["prepaid", "postpaid"]),
        FieldSpec("region", ["r1", "r2"]),
    ]
    cf = ComboFinder(fields, _mock_rule, label="add-instance", destructive=False, max_probes=500)
    out = cf.search()
    valid = out["validCombos"]
    incompat = {(i["fieldA"], i["valueA"], i["fieldB"], i["valueB"]) for i in out["incompatibilities"]}
    invalid = {(iv["field"], iv["value"]) for iv in out["invalidValues"]}
    print("\n===== G2 combo_finder 自测 =====")
    print(f"成功组合数: {len(valid)}  | 不兼容对数: {len(incompat)}  | 全局无效值: {len(invalid)}")
    # 断言: 3 个有效组合 (postpaid × {a,b} × {r1} 再扣除 b+r2)
    assert len(valid) == 3, f"期望 3 个有效组合, 实际 {len(valid)}"
    assert any(v["fields"]["flavor"] == "flavor-b" and v["fields"]["region"] == "r1" for v in valid)
    # 真·不兼容: flavor-b 与 region=r2 (两者各自可行但组合不行)
    assert ("flavor", "flavor-b", "region", "r2") in incompat, "应标记 flavor-b×region-r2 真·不兼容"
    # 全局无效值应被识别(不误报为 pairwise 不兼容)
    assert ("billing", "prepaid") in invalid, "应识别 prepaid 为全局无效值"
    assert ("flavor", "flavor-c") in invalid, "应识别 flavor-c 为全局无效值"
    # prepaid / flavor-c 应被识别为"全局无效值", 而不应出现在 pairwise 不兼容里
    incompat_flat = {(i["fieldA"], i["valueA"]) for i in out["incompatibilities"]} | \
                    {(i["fieldB"], i["valueB"]) for i in out["incompatibilities"]}
    assert ("billing", "prepaid") not in incompat_flat, "prepaid 不应误报为 pairwise 不兼容"
    assert ("flavor", "flavor-c") not in incompat_flat, "flavor-c 不应误报为 pairwise 不兼容"
    print("✅ G2 自测通过: 发现 3 个有效组合 + 1 个真·交互不兼容 + 2 个全局无效值")


if __name__ == "__main__":
    _self_test()
