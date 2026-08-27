"""离线验证: 通过 lib.api_client.get_fn 加载每个 api 模块的函数, 确认两级目录结构下
导入无误、函数名与生成器命名一致。不发起任何网络请求。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import api_client as ac  # noqa: E402


def load_catalog(proj):
    p = ROOT / "projects" / proj / "kb" / "api_catalog.json"
    return json.loads(p.read_text(encoding="utf-8"))


def verify_project(proj):
    print(f"\n========== {proj} ==========")
    catalog = load_catalog(proj)
    # 收集 (service, resource, crud) 三元组
    triples = set()
    for path_tmpl, methods in catalog["paths"].items():
        for method, ep in methods.items():
            svc = ep.get("x-kb-service") or "default"
            res = ep.get("x-kb-resource") or "resource"
            crud = ep.get("x-kb-crud", "action")
            triples.add((svc, res, crud))

    ok = 0
    err = 0
    errors = []
    for svc, res, crud in sorted(triples):
        try:
            fn = ac.get_fn(proj, svc, res, crud)
            ok += 1
        except Exception as e:
            err += 1
            errors.append((svc, res, crud, repr(e)))

    print(f"get_fn 成功 {ok} / 失败 {err} (共 {len(triples)} 个端点函数)")
    for svc, res, crud, e in errors[:20]:
        print(f"  ✗ {svc}/{res}/{crud}: {e}")

    # 验证 _client.json 可读 + get_client 可用
    try:
        proj_dir = ROOT / "projects" / proj
        c = ac.get_client(proj_dir)
        print(f"get_client OK -> base_url={c.base_url}")
    except Exception as e:
        print(f"  ✗ get_client 失败: {e}")

    return err


if __name__ == "__main__":
    total_err = 0
    for proj in ("estack", "ecm-compute"):
        total_err += verify_project(proj)
    print("\n========== 结论 ==========")
    if total_err == 0:
        print("✅ 全部 api 模块可被 importlib 正确加载, 函数名匹配, _client.json 可读。")
    else:
        print(f"❌ 有 {total_err} 个端点函数加载失败, 见上。")
    sys.exit(1 if total_err else 0)
