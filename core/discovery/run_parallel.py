"""
run_parallel.py — 分模块并行执行生成的 API/UI 测试脚本（复用 EcsCloud run_all24_parallel.js 思路）。

设计（与 EcsCloud 对齐）：
  * 按 scripts/<version>/{api|ui}/ 下「每个模块一个脚本」作为最小执行单元（模块级并行，组内即单脚本）。
  * 主进程先确保 cookie 有效（失效则单线程滑块登录一次），再 fan-out 给 Worker 子进程。
  * 每个 Worker 是独立子进程，并发数受 --parallel 控制；Worker 设 AUTO_LOGIN=0（只读 cookie，
    避免多进程并发抢登覆盖同一份 cookies.json）。
  * 各 Worker 结果按模块名聚合，输出并行汇总 JSON + 控制台摘要。

新增（对齐 EcsCloud）：
  * 唯一 sessionId：每次运行生成唯一 ID，结果写入 runs/<sessionId>/<module>.json
  * 旧 session 清理：保留最近 MAX_SESSIONS 次运行，自动删除更早的目录
  * 结果持久化：进程崩溃不丢结果（每个模块完成后立即写入）
  * Worker 重试：失败时最多重试 MAX_RETRIES 次，重试前检查 cookie 有效性
  * Cookie 中期刷新：长运行期间定期检查 cookie 有效期，过期前主动刷新

用法：
  python -m module_discovery.run_parallel --project ecm-compute
  python -m module_discovery.run_parallel --project ecm-compute --version v2.1.0 --parallel 6
  python -m module_discovery.run_parallel --project ecm-compute --module 用户管理
  python -m module_discovery.run_parallel --project ecm-compute --type ui --parallel 3
  python -m module_discovery.run_parallel --project ecm-compute --type api --export
"""
import os
import sys
import json
import time
import asyncio
import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.discovery import version as ver_mod
from core.discovery.io_helpers import load_profile as _load_profile
from core.discovery.path_mapper import get_workspace_dir
from core.discovery.shared_runner import (
    scan_scripts,
    ensure_cookie_valid,
    refresh_cookies,
    run_one,
    generate_summary_report,
)
from lib.utils import safe_write, generate_session_id

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "5"))
COOKIE_REFRESH_INTERVAL = int(os.environ.get("COOKIE_REFRESH_INTERVAL", "1800"))  # 30分钟


def parse_args():
    ap = argparse.ArgumentParser(description="分模块并行执行 API/UI 测试脚本")
    ap.add_argument("--project", required=True, help="项目 ID (projects/<id>/)")
    ap.add_argument("--version", default=None, help="脚本版本（默认读 .api_version）")
    ap.add_argument("--parallel", type=int, default=1, help="并发 Worker 数（默认 1，顺序执行）")
    ap.add_argument("--module", default=None, help="只运行指定模块（模糊匹配文件名）")
    ap.add_argument("--scripts-dir", default=None, help="覆盖 scripts 目录（绝对/相对）")
    ap.add_argument("--type", choices=["api", "ui"], default="api", help="脚本类型：api 或 ui（默认 api）")
    ap.add_argument("--headless", action="store_true", default=False, help="无头模式运行（默认打开浏览器）")
    ap.add_argument("--export", action="store_true", default=False, help="执行 Stage 5 导出 artifacts")
    return ap.parse_args()


def write_latest(runs_dir: Path, session_id: str):
    """写 runs/latest 指针指向本次 session。"""
    safe_write(runs_dir / "latest", session_id)


def clean_old_sessions(runs_dir: Path, max_sessions: int = MAX_SESSIONS):
    """保留最近 max_sessions 次运行，删除更早的 session 目录。"""
    import re
    if not runs_dir.exists():
        return
    session_dirs = []
    session_re = re.compile(r"^\d{8}T\d{6}_[a-z0-9]{4}$")
    for d in runs_dir.iterdir():
        if d.is_dir() and session_re.match(d.name):
            session_dirs.append(d)
    session_dirs.sort(key=lambda d: d.name, reverse=True)
    if len(session_dirs) <= max_sessions:
        return
    for d in session_dirs[max_sessions:]:
        try:
            shutil.rmtree(d)
            print(f"[清理] 已删除旧 session: {d.name}")
        except Exception as e:
            print(f"[清理] 删除失败 {d.name}: {e}")


def _run_stage5(manifest: dict, project_dir: Path, module_name: str,
                version: str = None, group: str = None, group_index: str = None):
    """Stage 5 导出回调（供 run_all_modules 调用）"""
    from core.discovery.export_artifacts import export_all
    export_all(manifest, project_dir, module_name, version=version,
               group=group, group_index=group_index)


async def main_async():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project
    if not project_dir.exists():
        print(f"❌ 项目目录不存在: {project_dir}")
        sys.exit(1)

    workspace_dir = get_workspace_dir(project_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    profile = _load_profile(project_dir)
    base_url = profile.get("base_url", "https://10.151.37.249")
    login_url = profile.get("login_url", f"{base_url}/estack/web/estack/login")

    version = args.version or ver_mod.resolve_version(project_dir)
    if args.scripts_dir:
        scripts_dir = Path(args.scripts_dir)
    else:
        scripts_dir = ver_mod.scripts_dir_for(project_dir, version)
    if not scripts_dir.exists():
        print(f"❌ scripts 目录不存在: {scripts_dir}")
        sys.exit(1)

    # Session ID
    session_id = generate_session_id()
    runs_dir = workspace_dir / "output" / "config" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 主进程预校验 cookie ----
    print("=" * 60)
    print(f"  分模块并行执行（版本 {version}，session: {session_id}）")
    print("=" * 60)
    print("▶ 预校验 cookie 有效性 ...")
    if args.type == "ui":
        # UI 脚本只需 cookie 文件存在，Playwright 会注入
        cookie_file = workspace_dir / "output" / "config" / "cookies.json"
        if not cookie_file.exists() or not json.loads(cookie_file.read_text("utf-8")):
            print("❌ cookies.json 不存在或为空（请先运行发现流程获取 cookie）")
            sys.exit(2)
        cookie_count = len(json.loads(cookie_file.read_text("utf-8")))
        print(f"✅ cookie 文件存在（{cookie_count} 条），开始执行 UI 脚本\n")
    else:
        if not ensure_cookie_valid(project_dir, workspace_dir, base_url, login_url):
            print("❌ cookie 无效且无法自动登录（请先运行发现流程或设置 AUTO_LOGIN=1 + 凭据）")
            sys.exit(2)
        print("✅ cookie 有效，开始并行执行\n")

    # ---- 2. 扫描脚本 ----
    scripts = scan_scripts(scripts_dir, args.module, args.type)
    if not scripts:
        print(f"⚠️ 未找到可执行的 {args.type.upper()} 测试脚本（{scripts_dir}）")
        sys.exit(0)
    print(f"共 {len(scripts)} 个模块脚本，并行 Worker = {args.parallel}\n")

    # ---- 3. 并行执行（带重试 + cookie 中期刷新）----
    sem = asyncio.Semaphore(max(1, args.parallel))
    start_time = time.time()
    last_cookie_refresh = start_time

    async def _run_with_sem(name, path, group, group_index):
        nonlocal last_cookie_refresh
        async with sem:
            # Cookie 中期刷新：每 COOKIE_REFRESH_INTERVAL 秒检查一次
            now = time.time()
            if now - last_cookie_refresh > COOKIE_REFRESH_INTERVAL:
                print(f"⏰ Cookie 中期刷新（已运行 {int(now - start_time)}s）...")
                try:
                    if not ensure_cookie_valid(project_dir, workspace_dir, base_url, login_url):
                        print("  ⚠️ cookie 刷新失败，继续使用现有 cookie")
                except Exception as e:
                    print(f"  ⚠️ cookie 刷新异常: {e}")
                last_cookie_refresh = now
            return await run_one(path, name, runs_dir, session_id, version,
                                 project_dir, workspace_dir, base_url, login_url,
                                 script_type=args.type, headless=args.headless)

    tasks = [_run_with_sem(name, path, group, group_index) for name, path, group, group_index in scripts]
    results = await asyncio.gather(*tasks)

    # ---- 4. 聚合 ----
    records = []
    total_retries = 0
    for (name, s, group, group_index), r in zip(scripts, results):
        retries = r.get("attempt", 1) - 1
        total_retries += retries
        status = "✅" if r["ok"] else "❌"
        retry_tag = f" (重试{retries}次)" if retries else ""
        # 显示 group 信息
        group_prefix = f"[{group} {group_index}] " if group and group_index else ""
        print(f"{status} {group_prefix}{name}{retry_tag} ({r['durationSec']}s)")
        if not r["ok"]:
            # 打印尾部错误便于定位
            tail = "\n".join(r["output"].strip().splitlines()[-15:])
            print("   └─ " + tail.replace("\n", "\n      "))
        records.append({"module": name, "group": group, "index": group_index, "script": str(s), **r})

    passed = sum(1 for r in records if r["ok"])
    failed = len(records) - passed
    total_dur = round(sum(r["durationSec"] for r in records), 1)
    summary = {
        "sessionId": session_id,
        "version": version,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parallel": args.parallel,
        "total": len(records),
        "passed": passed,
        "failed": failed,
        "retries": total_retries,
        "totalDurationSec": total_dur,
        "records": records,
    }

    # ---- 5. 写汇总 JSON ----
    out_dir = workspace_dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"parallel_{version}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    safe_write(out_file, json.dumps(summary, ensure_ascii=False, indent=2))

    # ---- 6. 写 runs/latest 指针 ----
    write_latest(runs_dir, session_id)

    # ---- 7. 清理旧 session ----
    clean_old_sessions(runs_dir)

    # ---- 8. 记录到失败归因台账 ----
    try:
        from lib.run_history import record_run
        run_summary = {
            "sessionId": session_id,
            "time": summary["time"],
            "total": summary["total"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "falsePasses": 0,
            "headless": True,
            "records": [
                {
                    "id": r["module"],
                    "name": r["module"],
                    "module": version,
                    "ok": r["ok"],
                    "errMsg": r.get("output", "")[:500] if not r["ok"] else "",
                    "durSec": r["durationSec"],
                }
                for r in records
            ],
        }
        record_run(project_dir, run_summary)
        print("\n✅ 已记录到失败归因台账")
    except Exception as e:
        print(f"\n⚠️  记录失败归因台账失败: {e}")

    # ---- 9. 执行 Stage 5 导出（如果指定 --export）----
    if args.export and args.type == "api":
        print("\n▶ 执行 Stage 5: 导出 artifacts ...")
        try:
            from core.discovery.batch_runner import run_all_modules
            # Stage 5 只需要 manifest 路径，不需要 page/context
            await run_all_modules(
                page=None,
                context=None,
                project_dir=project_dir,
                profile=profile,
                base_url=base_url,
                login_url=login_url,
                args=args,
                run_stage1=None,
                run_stage2=None,
                run_stage34=None,
                _run_stage4_verify=None,
                run_stage5=_run_stage5,
                modules_override=None,
                _run_ui_script=None,
            )
            print("✅ Stage 5 导出完成")
        except Exception as e:
            print(f"⚠️  Stage 5 导出失败: {e}")

    # ---- 10. 生成汇总报告 ----
    report_path = generate_summary_report(project_dir, version, args.type)
    if report_path:
        print(f"\n✅ {args.type.upper()} 汇总报告已生成: {report_path}")

    print("\n" + "=" * 60)
    print(f"  并行执行完成：通过 {passed} / 失败 {failed} / 共 {len(records)}")
    print(f"  总耗时 {total_dur}s（串行预计约 {round(total_dur * args.parallel, 1)}s）")
    print(f"  结果持久化: {runs_dir / session_id}")
    print(f"  汇总: {out_file}")
    print("=" * 60)

    if failed:
        print(f"\n需关注：")
        for r in records:
            if not r["ok"]:
                err_tail = r["output"].strip().splitlines()[-1][:120] if r["output"].strip() else ""
                print(f"  - [失败] {r['module']} :: {err_tail}")

    sys.exit(1 if failed else 0)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
