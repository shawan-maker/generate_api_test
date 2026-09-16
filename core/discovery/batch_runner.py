"""
batch_runner.py — 批量模块发现

从 run.py 提取的 run_all_modules 函数，处理 modules.yaml 中的多个模块。
"""

import json
import logging
from pathlib import Path

from .io_helpers import load_modules_yaml, needs_rediscovery, load_ui_result, load_capture_result
from .path_mapper import get_workspace_dir
from . import version as ver_mod

LOG = logging.getLogger("batch_runner")


async def run_all_modules(page, context, project_dir: Path, profile: dict,
                          base_url: str, login_url: str, args,
                          run_stage1, run_stage2, run_stage34,
                          _run_stage4_verify, run_stage5):
    """批量发现: 读取 modules.yaml，逐一执行指定 stage。

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
        _run_stage4_verify: Stage 4 验证函数
        run_stage5: Stage 5 执行函数
    """
    modules = load_modules_yaml(project_dir)
    if not modules:
        LOG.error("没有可用的模块")
        return

    workspace_dir = get_workspace_dir(project_dir)

    # 标签过滤
    if args.tag:
        filter_tags = set(t.strip() for t in args.tag.split(","))
        modules = [m for m in modules if filter_tags & set(m.get("tags", []))]
        LOG.info(f"标签过滤后: {len(modules)} 个模块")

    stage = args.stage  # FIX: 提前赋值，避免 NameError

    # 增量检查（Stage 5 不需要增量过滤，所有有 manifest 的模块都要处理）
    to_discover = []
    for m in modules:
        name = m["name"]
        url_raw = m["url"]
        target_url = base_url.rstrip("/") + url_raw if not url_raw.startswith("http") else url_raw
        if stage == "5":
            # Stage 5: 只要有 manifest 就处理
            manifest_path = workspace_dir / "kb" / "module_discovered" / f"{name}_manifest.json"
            if manifest_path.exists():
                to_discover.append((name, target_url, url_raw))
            else:
                LOG.info(f"  ⏭️  跳过 [{name}]（无 manifest 文件）")
        elif needs_rediscovery(project_dir, name, target_url, force=args.force):
            to_discover.append((name, target_url, url_raw))
        else:
            LOG.info(f"  ⏭️  跳过 [{name}]（已有结果，使用 --force 强制重新发现）")

    if not to_discover:
        LOG.info("所有模块均已发现，无需重新执行")
        return

    LOG.info(f"\n{'='*60}")
    LOG.info(f"  批量发现: {len(to_discover)} / {len(modules)} 个模块")
    LOG.info(f"{'='*60}")

    results = []

    for idx, (name, target_url, url_raw) in enumerate(to_discover, 1):
        LOG.info(f"\n{'='*60}")
        LOG.info(f"  [{idx}/{len(to_discover)}] {name}")
        LOG.info(f"  URL: {url_raw}")
        LOG.info(f"{'='*60}")

        try:
            # Stage 5 独立运行：跳过 1/2/34，直接加载 manifest 并导出
            if stage == "5":
                manifest_path = workspace_dir / "kb" / "module_discovered" / f"{name}_manifest.json"
                if not manifest_path.exists():
                    LOG.warning(f"  ⏭️ {name}: manifest 不存在，跳过")
                    results.append({"name": name, "status": "no_manifest"})
                    continue
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                run_stage5(manifest, project_dir, name,
                           version=args.version or ver_mod.resolve_version(project_dir))
                results.append({"name": name, "status": "ok"})
                continue

            # Stage 1
            if stage in ("1", "all"):
                ui_result = await run_stage1(page, project_dir, name, target_url)
            else:
                ui_result = load_ui_result(project_dir, name)

            # Stage 2
            stage2_valid = True
            if stage in ("2", "all"):
                capture_result, stage2_valid = await run_stage2(
                    page, project_dir, name, ui_result or {}, base_url, target_url,
                    max_recapture=args.max_recapture,
                    capture_all_mode=args.capture_all,
                    version=args.version or ver_mod.resolve_version(project_dir),
                    api_path_prefix=profile.get("api_base", ""))
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
                    results.append({"name": name, "status": "blocked"})
                    continue
                if not stage2_valid and args.force_gen:
                    LOG.warning(f"  ⚠️ --force-gen: 强制生成 {name} 的脚本")
                result = run_stage34(project_dir, name, profile, target_url,
                                     version=args.version or ver_mod.resolve_version(project_dir))
                if result:
                    _, script_path, manifest = result
                    LOG.info(f"  ✅ {name}: 脚本已生成 → {script_path}")

            # Stage 4 验证：运行脚本并检查结果
            script_ok = False
            if script_path and (stage == "all" or args.export):
                LOG.info(f"\n  Stage 4 验证: 运行 {name} 的 API 测试脚本")
                script_ok = await _run_stage4_verify(
                    str(script_path), project_dir, profile, login_url,
                    username=(args.user or ""), password=(args.password or ""),
                    headless=args.headless
                )
                if not script_ok:
                    LOG.warning(f"  ⚠️ {name}: 脚本运行失败，跳过 Stage 5 导出")
                    results.append({"name": name, "status": "script_failed"})
                    continue

            # Stage 5：导出 artifacts（仅在脚本成功时）
            if (stage == "all" or args.export) and manifest and (script_ok or not script_path):
                run_stage5(manifest, project_dir, name,
                           version=args.version or ver_mod.resolve_version(project_dir))

            results.append({"name": name, "status": "ok"})
        except Exception as e:
            LOG.error(f"  ❌ {name}: 发现失败 - {e}")
            results.append({"name": name, "status": "failed", "error": str(e)})

    # 合并所有模块的前置 API（项目级共享）
    processed_names = [name for name, _, _ in to_discover]
    if processed_names:
        try:
            from .pre_api_merger import merge_pre_apis_across_modules
            version = args.version or ver_mod.resolve_version(project_dir)
            LOG.info(f"\n{'='*60}")
            LOG.info(f"  合并前置 API（跨模块）")
            LOG.info(f"{'='*60}")
            merge_pre_apis_across_modules(project_dir, processed_names, version=version)
        except Exception as e:
            LOG.warning(f"  ⚠️ 前置 API 合并失败: {e}")

    # 汇总
    ok_count = sum(1 for r in results if r["status"] == "ok")
    fail_count = len(results) - ok_count
    LOG.info(f"\n{'='*60}")
    LOG.info(f"  批量发现完成: ✅ {ok_count} / ❌ {fail_count} / 共 {len(results)}")
    LOG.info(f"{'='*60}")

    for r in results:
        icon = "✅" if r["status"] == "ok" else "❌"
        msg = f"  {icon} {r['name']}"
        if r.get("error"):
            msg += f" — {r['error'][:80]}"
        LOG.info(msg)
