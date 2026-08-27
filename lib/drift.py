"""
drift.py — 契约漂移检测。对同一项目的两次发现结果进行 diff，生成 drift_report.json。
对应方案设计 §11.1。
"""
import json
from pathlib import Path
from datetime import datetime


def diff_catalogs(old: dict, new: dict, proj_id: str = "") -> dict:
    """
    比较两个 api_catalog.json 的 paths，检测新增、下线、变更的端点。
    返回 drift_report dict。
    """
    old_paths = old.get("paths", {})
    new_paths = new.get("paths", {})

    old_keys = set()
    new_keys = set()
    for p, methods in old_paths.items():
        for m in methods:
            old_keys.add(f"{m.upper()} {p}")
    for p, methods in new_paths.items():
        for m in methods:
            new_keys.add(f"{m.upper()} {p}")

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    common = sorted(old_keys & new_keys)

    # 对共同的端点检查参数/body_keys/statuses 变化
    changed = []
    for key in common:
        method, path = key.split(" ", 1)
        old_ep = old_paths.get(path, {}).get(method.lower(), {})
        new_ep = new_paths.get(path, {}).get(method.lower(), {})

        changes = []
        old_params = set(p.get("name", "") for p in old_ep.get("parameters", []))
        new_params = set(p.get("name", "") for p in new_ep.get("parameters", []))
        if old_params != new_params:
            changes.append(f"params: {old_params - new_params} removed, {new_params - old_params} added")

        old_body = set(old_ep.get("x-kb-body-keys", []))
        new_body = set(new_ep.get("x-kb-body-keys", []))
        if old_body != new_body:
            changes.append(f"body_keys: {old_body - new_body} removed, {new_body - old_body} added")

        old_crud = old_ep.get("x-kb-crud")
        new_crud = new_ep.get("x-kb-crud")
        if old_crud != new_crud:
            changes.append(f"crud: {old_crud} -> {new_crud}")

        if changes:
            changed.append({"key": key, "changes": changes})

    report = {
        "project": proj_id,
        "generated_at": datetime.utcnow().isoformat(),
        "summary": {
            "old_endpoints": len(old_keys),
            "new_endpoints": len(new_keys),
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
        },
        "added": added,
        "removed": removed,
        "changed": changed,
    }
    return report


def diff(proj_id: str, old_catalog: str = None, new_catalog: str = None):
    """
    对指定项目的两次 catalog 快照做 diff。
    old/new 为 catalog 文件路径, 默认从 kb/snapshots/ 取最近的两次。
    """
    proj_dir = Path.cwd() / "projects" / proj_id
    snap_dir = proj_dir / "kb" / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    if old_catalog and new_catalog:
        old = json.loads(Path(old_catalog).read_text(encoding="utf-8"))
        new = json.loads(Path(new_catalog).read_text(encoding="utf-8"))
    else:
        # 从 snapshots/ 取最新两个快照
        snaps = sorted(snap_dir.glob("*.json"))
        if len(snaps) < 2:
            print(f"[drift] ✗ {proj_id}: 快照不足 2 个 ({len(snaps)})")
            return None
        old = json.loads(snaps[-2].read_text(encoding="utf-8"))
        new = json.loads(snaps[-1].read_text(encoding="utf-8"))

    report = diff_catalogs(old, new, proj_id)

    out_path = proj_dir / "output" / "config" / "drift_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[drift] {proj_id}: +{report['summary']['added']} / -{report['summary']['removed']} / "
          f"~{report['summary']['changed']} (已写入 {out_path})")
    return report
