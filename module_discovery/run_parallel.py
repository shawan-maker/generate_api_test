"""
run_parallel.py — 分模块并行执行生成的 API 测试脚本（复用 EcsCloud run_all24_parallel.js 思路）。

设计（与 EcsCloud 对齐）：
  * 按 flows/<version>/ 下「每个模块一个脚本」作为最小执行单元（模块级并行，组内即单脚本）。
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
"""
import os
import sys
import json
import time
import asyncio
import argparse
import shutil
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module_discovery import version as ver_mod
from lib.utils import safe_write, generate_session_id

MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "5"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
COOKIE_REFRESH_INTERVAL = int(os.environ.get("COOKIE_REFRESH_INTERVAL", "1800"))  # 30分钟


def parse_args():
    ap = argparse.ArgumentParser(description="分模块并行执行 API 测试脚本")
    ap.add_argument("--project", required=True, help="项目 ID (projects/<id>/)")
    ap.add_argument("--version", default=None, help="脚本版本（默认读 .api_version）")
    ap.add_argument("--parallel", type=int, default=4, help="并发 Worker 数（默认 4）")
    ap.add_argument("--module", default=None, help="只运行指定模块（模糊匹配文件名）")
    ap.add_argument("--flows-dir", default=None, help="覆盖 flows 目录（绝对/相对）")
    return ap.parse_args()


def _load_profile(project_dir: Path) -> dict:
    try:
        import yaml
    except Exception:
        return {}
    p = project_dir / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def ensure_cookie_valid(project_dir: Path, base_url: str, login_url: str) -> bool:
    """主进程先确保 cookie 有效（失效则单线程登录一次）。返回是否可用。"""
    from lib.auth import AuthSession
    creds = _load_profile(project_dir).get("credentials", {}) or {}
    username = creds.get("username") or os.environ.get(creds.get("username_env", ""), "")
    password = creds.get("password") or os.environ.get(creds.get("password_env", ""), "")
    profile = {
        "base_url": base_url,
        "login_url": login_url,
        "auth": {"header_name": "Authorization", "header_prefix": "Bearer ",
                 "freshness_ttl_seconds": 300, "fixed_headers": {"Estack-Language": "zh-CN"}},
        "captcha": {"auth_button_text": "点击完成认证", "login_button_text": "登录"},
        "credentials": {},
        "probe_url": "/estack/api/estack/draco/v1/users/current-user",
    }
    sess = AuthSession(profile, username, password)
    ctx_dir = project_dir / "output" / "config"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    sess.context_path = str(ctx_dir / "context.json")
    client = sess.ensure_client(base_url)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
        return True
    return False


def scan_scripts(flows_dir: Path, module_filter: str = None) -> list:
    scripts = sorted(flows_dir.glob("*_API测试.py"))
    out = []
    for s in scripts:
        name = s.stem.replace("_API测试", "")
        if module_filter and module_filter not in name:
            continue
        out.append((name, s))
    return out


def write_result(runs_dir: Path, session_id: str, module_name: str, result: dict):
    """将单个模块结果写入 runs/<sessionId>/<module>.json。"""
    session_dir = runs_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    safe_write(
        session_dir / f"{module_name}.json",
        json.dumps(result, ensure_ascii=False, indent=2),
    )


def write_latest(runs_dir: Path, session_id: str):
    """写 runs/latest 指针指向本次 session。"""
    safe_write(runs_dir / "latest", session_id)


def read_session_results(runs_dir: Path, session_id: str) -> dict:
    """从 session 目录读取所有结果。"""
    session_dir = runs_dir / session_id
    if not session_dir.exists():
        return {}
    results = {}
    for f in session_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results[f.stem] = data
        except Exception:
            pass
    return results


def clean_old_sessions(runs_dir: Path, max_sessions: int = MAX_SESSIONS):
    """保留最近 max_sessions 次运行，删除更早的 session 目录。"""
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


async def run_one(script_path: Path, module_name: str, runs_dir: Path,
                  session_id: str, version: str, project_dir: Path,
                  base_url: str, login_url: str):
    """运行单个模块脚本，失败时重试（最多 MAX_RETRIES 次），重试前刷新 cookie。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["AUTO_LOGIN"] = "0"          # Worker 只读 cookie，不自动登录
    env["API_VERSION"] = version or ""

    last_result = None
    for attempt in range(1, MAX_RETRIES + 2):  # 1次正常 + MAX_RETRIES次重试
        t0 = time.time()
        result = {"module": module_name, "script": str(script_path), "ok": False,
                  "returncode": -1, "durationSec": 0, "output": "", "attempt": attempt}
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script_path),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env=env, cwd=str(ROOT),
            )
            stdout, _ = await proc.communicate()
            dur = round(time.time() - t0, 1)
            text = stdout.decode("utf-8", errors="replace") if stdout else ""
            ok = proc.returncode == 0
            result = {"module": module_name, "script": str(script_path), "ok": ok,
                      "returncode": proc.returncode, "durationSec": dur, "output": text,
                      "attempt": attempt}
        except Exception as e:
            dur = round(time.time() - t0, 1)
            result = {"module": module_name, "script": str(script_path), "ok": False,
                      "returncode": -1, "durationSec": dur, "output": f"启动失败: {e}",
                      "attempt": attempt}

        # 立即持久化（进程崩溃不丢结果）
        write_result(runs_dir, session_id, module_name, result)
        last_result = result

        # 成功或已达重试上限，返回
        if result["ok"] or attempt > MAX_RETRIES:
            return result

        # 失败且还有重试机会：刷新 cookie 后再试
        print(f"  ⚠️ {module_name} 失败（attempt {attempt}/{MAX_RETRIES + 1}），刷新 cookie 后重试...")
        try:
            if not ensure_cookie_valid(project_dir, base_url, login_url):
                print(f"  ❌ cookie 刷新失败，跳过重试")
                return result
        except Exception as e:
            print(f"  ⚠️ cookie 刷新异常: {e}")
            return result

    return last_result


async def main_async():
    args = parse_args()
    project_dir = ROOT / "projects" / args.project
    if not project_dir.exists():
        print(f"❌ 项目目录不存在: {project_dir}")
        sys.exit(1)

    profile = _load_profile(project_dir)
    base_url = profile.get("base_url", "https://10.151.37.249")
    login_url = profile.get("login_url", f"{base_url}/estack/web/estack/login")

    version = args.version or ver_mod.resolve_version(project_dir)
    if args.flows_dir:
        flows_dir = Path(args.flows_dir)
    else:
        flows_dir = ver_mod.flows_dir_for(project_dir, version)
    if not flows_dir.exists():
        print(f"❌ flows 目录不存在: {flows_dir}")
        sys.exit(1)

    # Session ID
    session_id = generate_session_id()
    runs_dir = project_dir / "output" / "config" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 主进程预校验 cookie ----
    print("=" * 60)
    print(f"  分模块并行执行（版本 {version}，session: {session_id}）")
    print("=" * 60)
    print("▶ 预校验 cookie 有效性 ...")
    if not ensure_cookie_valid(project_dir, base_url, login_url):
        print("❌ cookie 无效且无法自动登录（请先运行发现流程或设置 AUTO_LOGIN=1 + 凭据）")
        sys.exit(2)
    print("✅ cookie 有效，开始并行执行\n")

    # ---- 2. 扫描脚本 ----
    scripts = scan_scripts(flows_dir, args.module)
    if not scripts:
        print(f"⚠️ 未找到可执行的测试脚本（{flows_dir}）")
        sys.exit(0)
    print(f"共 {len(scripts)} 个模块脚本，并行 Worker = {args.parallel}\n")

    # ---- 3. 并行执行（带重试 + cookie 中期刷新）----
    sem = asyncio.Semaphore(max(1, args.parallel))
    start_time = time.time()
    last_cookie_refresh = start_time

    async def _run_with_sem(name, path):
        nonlocal last_cookie_refresh
        async with sem:
            # Cookie 中期刷新：每 COOKIE_REFRESH_INTERVAL 秒检查一次
            now = time.time()
            if now - last_cookie_refresh > COOKIE_REFRESH_INTERVAL:
                print(f"⏰ Cookie 中期刷新（已运行 {int(now - start_time)}s）...")
                try:
                    if not ensure_cookie_valid(project_dir, base_url, login_url):
                        print("  ⚠️ cookie 刷新失败，继续使用现有 cookie")
                except Exception as e:
                    print(f"  ⚠️ cookie 刷新异常: {e}")
                last_cookie_refresh = now
            return await run_one(path, name, runs_dir, session_id, version,
                                 project_dir, base_url, login_url)

    tasks = [_run_with_sem(name, path) for name, path in scripts]
    results = await asyncio.gather(*tasks)

    # ---- 4. 聚合 ----
    records = []
    total_retries = 0
    for (name, s), r in zip(scripts, results):
        retries = r.get("attempt", 1) - 1
        total_retries += retries
        status = "✅" if r["ok"] else "❌"
        retry_tag = f" (重试{retries}次)" if retries else ""
        print(f"{status} {name}{retry_tag} ({r['durationSec']}s)")
        if not r["ok"]:
            # 打印尾部错误便于定位
            tail = "\n".join(r["output"].strip().splitlines()[-15:])
            print("   └─ " + tail.replace("\n", "\n      "))
        records.append({"module": name, "script": str(s), **r})

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
    out_dir = Path(__file__).resolve().parents[1] / "output" / "reports"
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
