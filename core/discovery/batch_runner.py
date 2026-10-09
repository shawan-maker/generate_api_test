"""
batch_runner.py — 批量模块发现

从 run.py 提取的 run_all_modules 函数，处理 modules.yaml 中的多个模块。

改造：
- 按 group（一级菜单）分组，组内编号（01, 02, ...）
- 支持 skip_info（无有效业务时跳过脚本生成）
- 生成 module_status.json 记录所有模块状态
"""

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from .io_helpers import load_modules_yaml, needs_rediscovery, load_ui_result, load_capture_result
from .path_mapper import get_workspace_dir
from . import version as ver_mod

LOG = logging.getLogger("batch_runner")


async def run_all_modules(page, context, project_dir: Path, profile: dict,
                          base_url: str, login_url: str, args,
                          run_stage1, run_stage2, run_stage34,
                          run_stage5,
                          modules_override: list = None):
    """批量发现: 读取 modules.yaml（或使用传入的模块列表），按 group 分组后逐一执行。

    Args:
        page: Playwright page 对象
        context: Playwright context 对象
        project_dir: 项目目录
        profile: 项目 profile.yaml 内容
        base_url: 基础 URL
        login_url: 登录页 URL
        args: argparse 参数
        run_stage1: Stage 1 执行函数
        run_stage2: Stage 2 执行函数
        run_stage34: Stage 3+4 执行函数
        run_stage5: Stage 5 执行函数
        modules_override: 覆盖 modules.yaml 的模块列表（discover 模式传入）
    """
    if modules_override is not None:
        modules = modules_override
    else:
        modules = load_modules_yaml(project_dir)
    if not modules:
        LOG.error("没有可用的模块")
        return

    workspace_dir = get_workspace_dir(project_dir)
    version = args.version or ver_mod.resolve_version(project_dir)

    # 标签过滤
    if args.tag:
        filter_tags = set(t.strip() for t in args.tag.split(","))
        modules = [m for m in modules if filter_tags & set(m.get("tags", []))]
        LOG.info(f"标签过滤后: {len(modules)} 个模块")

    stage = args.stage  # FIX: 提前赋值，避免 NameError

    # ---- 增量检查 ----
    # 每个条目: (name, target_url, url_raw, group)
    to_discover = []
    for m in modules:
        name = m["name"]
        url_raw = m["url"]
        group = m.get("group", "未分类")
        target_url = base_url.rstrip("/") + url_raw if not url_raw.startswith("http") else url_raw
        if stage == "5":
            manifest_path = workspace_dir / "kb" / "module_discovered" / f"{name}_manifest.json"
            if manifest_path.exists():
                to_discover.append((name, target_url, url_raw, group))
            else:
                LOG.info(f"  ⏭️  跳过 [{name}]（无 manifest 文件）")
        elif needs_rediscovery(project_dir, name, target_url, force=args.force):
            to_discover.append((name, target_url, url_raw, group))
        else:
            LOG.info(f"  ⏭️  跳过 [{name}]（已有结果，使用 --force 强制重新发现）")

    if not to_discover:
        LOG.info("所有模块均已发现，无需重新执行")
        return

    # ---- 按 group 分组，组内排序并编号 ----
    grouped = defaultdict(list)
    for name, target_url, url_raw, group in to_discover:
        grouped[group].append((name, target_url, url_raw))

    # 组内按名称排序，确保编号稳定
    for group in grouped:
        grouped[group].sort(key=lambda x: x[0])

    LOG.info(f"\n{'='*60}")
    LOG.info(f"  批量发现: {len(to_discover)} / {len(modules)} 个模块（{len(grouped)} 个分组）")
    LOG.info(f"{'='*60}")

    results = []        # 最终结果列表
    all_results = []    # 所有模块状态（含跳过的）

    # ---- 按组遍历 ----
    for group in sorted(grouped.keys()):
        items = grouped[group]
        LOG.info(f"\n{'─'*60}")
        LOG.info(f"  📂 {group}（{len(items)} 个模块）")
        LOG.info(f"{'─'*60}")

        for idx, (name, target_url, url_raw) in enumerate(items, 1):
            group_index = f"{idx:02d}"

            # ---- Cookie 中期检查：每 5 个模块检查一次 ----
            if idx > 1 and idx % 5 == 0:
                LOG.info("  🔄 模块间 cookie 中期检查 ...")
                try:
                    from core.discovery.shared_runner import ensure_cookie_valid
                    if not ensure_cookie_valid(project_dir, workspace_dir, base_url, login_url):
                        LOG.warning("  ⚠️ cookie 刷新失败，继续执行（可能导致后续模块失败）")
                except Exception as e:
                    LOG.warning(f"  ⚠️ cookie 检查异常: {e}")

            LOG.info(f"\n  [{group} {group_index}] {name}")
            LOG.info(f"  URL: {url_raw}")

            result_entry = {
                "name": name,
                "group": group,
                "index": group_index,
                "status": "pending",
                "reason": None,
                "api_script": None,
                "ui_script": None,
            }

            # 计算 UI 脚本路径（用于记录到 result_entry，无论脚本是否存在）
            if group and group_index:
                ui_script_path = project_dir / version / "ui" / group / f"{group_index}_{name}.py"
            else:
                ui_script_path = project_dir / version / "ui" / f"{name}.py"
            if ui_script_path.exists():
                result_entry["ui_script"] = str(ui_script_path.relative_to(project_dir / version))

            try:
                # Stage 5 独立运行：跳过 1/2/34，直接加载 manifest 并导出
                if stage == "5":
                    manifest_path = workspace_dir / "kb" / "module_discovered" / f"{name}_manifest.json"
                    if not manifest_path.exists():
                        LOG.warning(f"  ⏭️ {name}: manifest 不存在，跳过")
                        result_entry["status"] = "no_manifest"
                        all_results.append(result_entry)
                        continue
                    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                    run_stage5(manifest, project_dir, name, version=version,
                               group=group, group_index=group_index)
                    result_entry["status"] = "ok"
                    all_results.append(result_entry)
                    results.append({"name": name, "status": "ok"})
                    continue

                # Stage 1
                if stage in ("1", "all"):
                    ui_result = await run_stage1(
                        page, project_dir, name, target_url,
                        context=context, profile=profile,
                        base_url=base_url, login_url=login_url,
                    )
                else:
                    ui_result = load_ui_result(project_dir, name)

                # Stage 2
                stage2_valid = True
                if stage in ("2", "all"):
                    capture_result, stage2_valid = await run_stage2(
                        page, project_dir, name, ui_result or {}, base_url, target_url,
                        max_recapture=args.max_recapture,
                        capture_all_mode=args.capture_all,
                        version=version,
                        api_path_prefix=profile.get("api_base", ""),
                        group=group, group_index=group_index)
                else:
                    capture_result = load_capture_result(project_dir, name)
                    stage2_valid = bool(capture_result and capture_result.get("core_api_map"))

                # Stage 3+4
                script_path = None
                manifest = None
                if stage in ("34", "4", "all"):
                    if not stage2_valid and not args.force_gen:
                        LOG.error(f"  ❌ {name}: Stage 2 验证失败，跳过脚本生成"
                                  "（使用 --force-gen 强制覆盖）")
                        result_entry["status"] = "blocked"
                        result_entry["reason"] = "Stage 2 验证失败"
                        # 清理可能残留的旧 UI 脚本（防止错误脚本被误用）
                        _cleanup_stale_ui_script(project_dir, version, group, group_index, name)
                        results.append({"name": name, "status": "blocked"})
                        all_results.append(result_entry)
                        continue
                    if not stage2_valid and args.force_gen:
                        LOG.warning(f"  ⚠️ --force-gen: 强制生成 {name} 的脚本")
                    result = run_stage34(project_dir, name, profile, target_url,
                                         version=version,
                                         group=group, group_index=group_index)
                    if result:
                        _, script_path, manifest, skip_info = result
                        if skip_info:
                            LOG.warning(f"  ⏭️ {name}: 跳过脚本生成")
                            result_entry["status"] = "skipped"
                            result_entry["reason"] = skip_info["reason"]
                            # 清理 Stage 2 已生成的 UI 脚本（无有效业务操作）
                            _cleanup_stale_ui_script(project_dir, version, group, group_index, name)
                            results.append({"name": name, "status": "skipped"})
                            all_results.append(result_entry)
                            continue
                        LOG.info(f"  ✅ {name}: 脚本已生成 → {script_path}")

                # 刷新 ui_script 字段（Stage 2 已生成，之前检查时文件尚不存在）
                if ui_script_path.exists():
                    result_entry["ui_script"] = str(ui_script_path.relative_to(project_dir / version))

                # Stage 5：导出 artifacts（默认执行）
                if manifest is None:
                    manifest_path = workspace_dir / "kb" / "module_discovered" / f"{name}_manifest.json"
                    if manifest_path.exists():
                        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

                if manifest:
                    run_stage5(manifest, project_dir, name, version=version,
                               group=group, group_index=group_index)

                result_entry["status"] = "ok"
                if script_path:
                    result_entry["api_script"] = str(Path(script_path).relative_to(project_dir / version))
                if ui_script_path.exists():
                    result_entry["ui_script"] = str(ui_script_path.relative_to(project_dir / version))
                all_results.append(result_entry)
                results.append({"name": name, "status": "ok"})

            except Exception as e:
                LOG.error(f"  ❌ {name}: 发现失败 - {e}")
                result_entry["status"] = "failed"
                result_entry["reason"] = str(e)
                all_results.append(result_entry)
                results.append({"name": name, "status": "failed", "error": str(e)})

    # ---- 合并所有模块的前置 API（项目级共享）----
    processed_names = [name for name, _, _, _ in to_discover]
    if processed_names:
        try:
            from .pre_api_merger import merge_pre_apis_across_modules
            LOG.info(f"\n{'='*60}")
            LOG.info(f"  合并前置 API（跨模块）")
            LOG.info(f"{'='*60}")
            merge_pre_apis_across_modules(project_dir, processed_names, version=version)
        except Exception as e:
            LOG.warning(f"  ⚠️ 前置 API 合并失败: {e}")

    # ---- 生成 module_status.json ----
    _save_module_status(project_dir, version, all_results)

    # ---- 汇总 ----
    ok_count = sum(1 for r in results if r["status"] == "ok")
    skip_count = sum(1 for r in results if r["status"] == "skipped")
    fail_count = sum(1 for r in results if r["status"] not in ("ok", "skipped"))
    LOG.info(f"\n{'='*60}")
    LOG.info(f"  批量发现完成: ✅ {ok_count} / ⏭️ {skip_count} / ❌ {fail_count} / 共 {len(results)}")
    LOG.info(f"{'='*60}")

    for r in all_results:
        if r["status"] == "ok":
            icon = "✅"
        elif r["status"] == "skipped":
            icon = "⏭️"
        else:
            icon = "❌"
        msg = f"  {icon} [{r['group']} {r['index']}] {r['name']}"
        if r.get("reason"):
            msg += f" — {r['reason'][:60]}"
        LOG.info(msg)


def _cleanup_stale_ui_script(project_dir: Path, version: str, group: str,
                             group_index: str, module_name: str):
    """清理残留的 UI 脚本（当模块被 blocked/skipped 时调用）。"""
    if group and group != "未分类":
        ui_script = project_dir / version / "ui" / group / f"{group_index}_{module_name}.py"
    else:
        ui_script = project_dir / version / "ui" / f"{module_name}.py"
    if ui_script.exists():
        ui_script.unlink()
        LOG.info(f"  🗑️ 已清理残留 UI 脚本: {ui_script}")


def _save_module_status(project_dir: Path, version: str, results: list):
    """生成 module_status.json，记录所有模块的执行状态。"""
    from lib.utils import safe_write_json

    # 按 group 聚合统计
    groups = {}
    for r in results:
        g = r["group"]
        if g not in groups:
            groups[g] = {"total": 0, "ok": 0, "skipped": 0, "failed": 0}
        groups[g]["total"] += 1
        status = r["status"]
        if status == "ok":
            groups[g]["ok"] += 1
        elif status == "skipped":
            groups[g]["skipped"] += 1
        else:
            groups[g]["failed"] += 1

    total = len(results)
    ok_count = sum(1 for r in results if r["status"] == "ok")
    skip_count = sum(1 for r in results if r["status"] == "skipped")
    fail_count = total - ok_count - skip_count

    data = {
        "project": project_dir.name,
        "version": version,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": {
            "total": total,
            "ok": ok_count,
            "skipped": skip_count,
            "failed": fail_count,
        },
        "groups": groups,
        "modules": results,
    }

    status_path = project_dir / version / "module_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(status_path, data, ensure_ascii=False, indent=2)
    LOG.info(f"\n  📋 模块状态已保存: {status_path}")
