"""
shared_runner.py — 测试执行的公共逻辑

从 run_parallel.py 和 batch_runner.py 提取的公共功能：
- scan_scripts: 扫描 API/UI 测试脚本
- run_one: 执行单个测试脚本
- ensure_cookie_valid: cookie 有效性校验
- refresh_cookies: cookie 刷新

供 run_parallel.py 和 batch_runner.py 复用，避免代码重复。
"""

import os
import sys
import json
import time
import asyncio
import re
from pathlib import Path
from typing import Optional

# 确保能找到 lib
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.discovery import version as ver_mod
from core.discovery.io_helpers import load_profile as _load_profile
from core.discovery.path_mapper import get_workspace_dir
from lib.utils import safe_write

MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "2"))
COOKIE_REFRESH_INTERVAL = int(os.environ.get("COOKIE_REFRESH_INTERVAL", "1800"))  # 30分钟


def scan_scripts(scripts_dir: Path, module_filter: str = None, script_type: str = "api") -> list:
    """扫描 API/UI 测试脚本，支持 group 子目录结构。

    scripts_dir 指向版本目录（如 projects/estack/v1.0.0/），
    脚本实际在 scripts_dir/{api|ui}/ 下。

    API 结构: api/{group}/{index}_{module}_API测试.py
    UI 结构:  ui/{group}/{index}_{module}.py

    Returns:
        [(module_name, script_path, group, group_index), ...]
    """
    out = []

    # 确定目录和后缀
    type_dir = scripts_dir / script_type
    if not type_dir.exists():
        return out

    suffix_pattern = "*_API测试.py" if script_type == "api" else "*.py"

    # 扫描 group 子目录
    for group_dir in sorted(type_dir.iterdir()):
        if not group_dir.is_dir():
            continue
        if group_dir.name in ("lib", "config", "reports", "__pycache__"):
            continue
        # 扫描 group 目录下的脚本
        for s in sorted(group_dir.glob(suffix_pattern)):
            stem = s.stem
            # 解析 index 和 module name
            if script_type == "api":
                idx_match = re.match(r'^(\d{2})_(.+)_API测试$', stem)
            else:
                idx_match = re.match(r'^(\d{2})_(.+)$', stem)

            if idx_match:
                group_index = idx_match.group(1)
                name = idx_match.group(2)
            else:
                # 不符合命名规范，跳过
                continue

            if module_filter and module_filter not in name:
                continue
            out.append((name, s, group_dir.name, group_index))

    return out


def ensure_cookie_valid(project_dir: Path, workspace_dir: Path, base_url: str, login_url: str) -> bool:
    """主进程先确保 cookie 有效（失效则单线程登录一次）。返回是否可用。"""
    from lib.auth import AuthSession
    project_profile = _load_profile(project_dir)
    creds = project_profile.get("credentials", {}) or {}
    username = creds.get("username") or os.environ.get(creds.get("username_env", ""), "")
    password = creds.get("password") or os.environ.get(creds.get("password_env", ""), "")
    # 从 profile.yaml 读取 auth 配置（不再硬编码）
    auth_cfg = project_profile.get("auth", {})
    auth_cfg.setdefault("freshness_ttl_seconds", 300)
    profile = {
        "base_url": base_url,
        "login_url": login_url,
        "auth": auth_cfg,
        "captcha": project_profile.get("captcha", {}),
        "credentials": {},
        "probe_url": "/estack/api/estack/draco/v1/users/current-user",
    }
    sess = AuthSession(profile, username, password)
    ctx_dir = workspace_dir / "output" / "config"
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


async def refresh_cookies(project_dir: Path, workspace_dir: Path, base_url: str, login_url: str) -> bool:
    """刷新 cookies：检测过期后调用 auth_runner 重新登录。

    使用非 headless 模式启动浏览器滑块登录，刷新后保存新 cookie。

    Returns:
        True 如果刷新成功
    """
    from core.discovery.io_helpers import load_profile as _load_profile
    from core.discovery.auth_runner import login_with_playwright

    profile = _load_profile(project_dir)
    creds = profile.get("credentials", {}) or {}
    username = creds.get("username") or os.environ.get(creds.get("username_env", ""), "")
    password = creds.get("password") or os.environ.get(creds.get("password_env", ""), "")

    if not username or not password:
        print("  ❌ 无法刷新 cookie: 缺少登录凭据")
        return False

    try:
        from playwright.async_api import async_playwright
        from lib.browser_launcher import launch_browser

        async with async_playwright() as pw:
            browser, context, page = await launch_browser(pw, headless=False)

            try:
                ok = await login_with_playwright(page, context, login_url, username, password,
                                                 project_profile=profile)
                if not ok:
                    print("  ❌ 滑块登录失败")
                    return False

                # 保存新 cookie
                cookies = await context.cookies()
                cookie_file = workspace_dir / "output" / "config" / "cookies.json"
                cookie_file.parent.mkdir(parents=True, exist_ok=True)
                cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  ✅ 新 cookie 已保存: {cookie_file} ({len(cookies)} 条)")
                return True
            finally:
                await browser.close()
    except Exception as e:
        print(f"  ⚠️ cookie 刷新异常: {e}")
        return False


async def run_one(script_path: Path, module_name: str, runs_dir: Path,
                  session_id: str, version: str, project_dir: Path,
                  workspace_dir: Path, base_url: str, login_url: str,
                  script_type: str = "api", headless: bool = False) -> dict:
    """运行单个模块脚本，失败时重试（最多 MAX_RETRIES 次），重试前刷新 cookie。

    Args:
        script_path: 脚本文件路径
        module_name: 模块名称
        runs_dir: 运行结果目录
        session_id: 会话 ID
        version: 版本
        project_dir: 项目目录
        workspace_dir: workspace 目录
        base_url: 基础 URL
        login_url: 登录页 URL
        script_type: "api" 或 "ui"
        headless: 是否无头模式运行

    Returns:
        执行结果字典
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["AUTO_LOGIN"] = "0"          # Worker 只读 cookie，不自动登录
    env["API_VERSION"] = version or ""

    last_result = None
    for attempt in range(1, MAX_RETRIES + 2):  # 1次正常 + MAX_RETRIES次重试
        t0 = time.time()
        result = {"module": module_name, "script": str(script_path), "ok": False,
                  "returncode": -1, "durationSec": 0, "output": "", "attempt": attempt}

        # 每次运行前检查 cookie 是否过期
        if attempt == 1:  # 只在第一次运行时检查
            cookie_file = workspace_dir / "output" / "config" / "cookies.json"
            if cookie_file.exists():
                try:
                    with open(cookie_file, 'r', encoding='utf-8') as f:
                        cookies_data = json.load(f)
                    # 检查 cookies 是否过期（简单检查时间戳）
                    current_time = int(time.time())
                    is_expired = False
                    for cookie in cookies_data:
                        if 'expires' in cookie and cookie['expires'] > 0:
                            if cookie['expires'] < current_time:
                                is_expired = True
                                break

                    if is_expired:
                        print(f"⏰ [{module_name}] Cookie 已过期，刷新中...")
                        if not await refresh_cookies(project_dir, workspace_dir, base_url, login_url):
                            print(f"  ⚠️ cookie 刷新失败，使用现有 cookie")
                except Exception as e:
                    print(f"  ⚠️ 检查 cookie 失败：{e}")

        try:
            # UI 脚本需要 --headless 参数（如果指定），API 脚本不需要
            cmd = [sys.executable, str(script_path)]
            if script_type == "ui" and headless:
                cmd.append("--headless")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
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
        session_dir = runs_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        safe_write(
            session_dir / f"{module_name}.json",
            json.dumps(result, ensure_ascii=False, indent=2),
        )

        if result["ok"]:
            last_result = result
            break
        else:
            last_result = result
            if attempt <= MAX_RETRIES:
                print(f"  ⚠️ {module_name} 失败（attempt {attempt}/{MAX_RETRIES + 1}），刷新 cookie 后重试...")
                await refresh_cookies(project_dir, workspace_dir, base_url, login_url)

    return last_result


def generate_summary_report(project_dir: Path, version: str, script_type: str) -> Optional[str]:
    """生成汇总报告（API 或 UI）。

    Args:
        project_dir: 项目目录
        version: 版本
        script_type: "api" 或 "ui"

    Returns:
        报告文件路径，失败返回 None
    """
    try:
        if script_type == "api":
            from lib.report.summary_report import generate_api_summary
            return generate_api_summary(project_dir, version)
        elif script_type == "ui":
            from lib.report.summary_report import generate_ui_summary
            return generate_ui_summary(project_dir, version)
    except Exception as e:
        print(f"\n⚠️  生成汇总报告失败: {e}")
        return None
