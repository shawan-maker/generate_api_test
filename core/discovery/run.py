"""
run.py — module_discovery 执行入口。

用法:
    python -m module_discovery.run --project ecm-compute \\
        --url "/estack/web/estack/user-center/user-manage/user" \\
        --module "用户管理" --user jcyz213-test --pass '9smkKAq@4'

    # 分阶段执行:
    python -m module_discovery.run --stage 1 --project ecm-compute ...
    python -m module_discovery.run --stage 2 --project ecm-compute ...
    python -m module_discovery.run --stage 34 --project ecm-compute ...
    python -m module_discovery.run --stage 4 --project ecm-compute ...
"""

import sys
import os
import json
import time
import asyncio
import logging
import argparse
from datetime import datetime
from pathlib import Path

# 确保能找到 lib
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.discovery.discover_ui import (
    discover_and_validate, discover_all, cleanup_ui_overlays, wait_for_spa_ready,
)
from core.discovery.capture_apis import capture_all
from core.discovery.analyze_flow import analyze, build_manifest
from core.discovery.gen_test import generate_script, save_script_to_file
from core.discovery import version as ver_mod
from core.discovery.stage_validators import validate_stage1, validate_stage2, validate_stage3, validate_stage4
from core.discovery.io_helpers import (
    load_profile as _load_profile,
    load_ui_result as _load_ui_result,
    load_capture_result as _load_capture_result,
    save_json as _save_json,
    needs_rediscovery as _needs_rediscovery,
)
from core.discovery.auth_runner import login_with_playwright as _login_with_playwright
from core.discovery.stage1_rescue import hints_rescan as _hints_rescan
from core.discovery.stage1_rescue import precondition_retry as _precondition_retry
from core.discovery.stage1_rescue import vision_rescue as _vision_rescue
from core.discovery.batch_runner import run_all_modules
from core.discovery.path_mapper import get_workspace_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("run")


def _normalize_url(url: str, base_url: str) -> str:
    """规范化 URL 路径，修复 MSYS2/Git Bash 路径转换问题。

    MSYS2/Git Bash 会将 --url /estack/web/... 自动转换为
    D:/Program Files (x86)/Git/estack/web/...，导致拼接异常。

    检测并修复此类被破坏的路径。
    """
    if url.startswith("http"):
        return url

    # MSYS2 路径转换修复：检测是否包含 Windows 驱动器盘符（如 D:/）
    import re
    msys_match = re.match(r'^[A-Za-z]:[/\\].*?[/\\](estack|api|web)(/.+)$', url)
    if msys_match:
        # 提取真实路径部分（从已知前缀开始）
        url = "/" + msys_match.group(1) + msys_match.group(2)
        LOG.debug(f"  URL 路径修复（MSYS2 转换）: {url}")

    return base_url.rstrip("/") + url


def parse_args():
    ap = argparse.ArgumentParser(description="模块级 API 自动化发现")
    ap.add_argument("--project", required=True, help="项目 ID (projects/<id>/)")
    ap.add_argument("--url", default=None, help="模块入口 URL 路径")
    ap.add_argument("--module", default=None, help="模块名称（中文，如'用户管理'）")
    ap.add_argument("--user", default=None, help="登录用户名")
    ap.add_argument("--pass", dest="password", default=None, help="登录密码")
    ap.add_argument("--headless", action="store_true", default=False,
                    help="无头模式（默认 False）")
    ap.add_argument("--offline", action="store_true",
                    help="离线模式：从已有 discovered.json 重新分析+生成，不启动浏览器")
    ap.add_argument("--stage", type=str, default="all",
                    choices=["1", "2", "34", "4", "5", "all"],
                    help="执行阶段: 1=探测, 2=捕获, 34=分析+生成, 5=导出, all=全部")
    ap.add_argument("--version", type=str, default=None,
                    help="脚本版本号（如 v2.1.0）。默认读 .api_version / API_VERSION 环境变量 / v1.0.0")
    # ---- 批量发现 ----
    ap.add_argument("--discover", action="store_true", default=False,
                    help="自动发现模式：从主页面发现所有模块并批量处理")
    ap.add_argument("--discover-only", action="store_true", default=False,
                    help="仅执行导航发现，输出模块列表（JSON）后退出，不执行管线")
    ap.add_argument("--discover-select", type=str, default=None,
                    help="从缓存加载发现结果，按编号选择模块执行（如 '1,3,5' 或 'all'）")
    ap.add_argument("--home-url", type=str, default=None,
                    help="主页面 URL（discover 模式使用，避免交互式输入）")
    ap.add_argument("--output-format", type=str, default="text",
                    choices=["text", "json"],
                    help="--discover-only 的输出格式（默认 text，AI 客户端用 json）")
    ap.add_argument("--all-modules", action="store_true", default=False,
                    help="批量发现: 读取 projects/<project>/modules.yaml 中的模块清单逐一发现")
    ap.add_argument("--force", action="store_true", default=False,
                    help="强制重新发现所有模块（忽略增量检查）")
    ap.add_argument("--tag", default=None,
                    help="仅发现指定标签的模块（逗号分隔）")
    ap.add_argument("--force-gen", action="store_true", default=False,
                    help="即使 Stage 2 验证失败也强制生成脚本")
    ap.add_argument("--max-recapture", type=int, default=0,
                    help="Stage 2 验证失败时的最大重试捕获次数（默认0禁用，重跑对 playbook 问题无用）")
    ap.add_argument("--capture-all", action="store_true", default=False,
                    help="捕获所有 XHR/fetch 请求（不限于 /estack/api）")
    ap.add_argument("--export", action="store_true", default=False,
                    help="Stage 5: 导出 Postman Collection / helpers.py / Excel 参数文件")
    ap.add_argument("--no-run", action="store_true", default=False,
                    help="跳过脚本生成后的自动运行验证（默认自动运行 API+UI 脚本并生成报告）")
    return ap.parse_args()


async def run_stage1(page, project_dir: Path, module_name: str, target_url: str):
    """Stage 1: 前端按钮探测 + 业务闭环验证。

    流程（6 Phase）：
      Phase A: 标准探测 (discover_all)
      Phase B: 业务闭环验证 (_validate_business_flow)
      Phase C: hints 定向重探 (_scan_hints)
      Phase D: 前置操作检查 + 重试 (_retry_precondition)
      Phase E: Vision 截图分析兜底 (ai_assisted_analysis)
      Phase F: 终止判定 (critical 缺失 → None)

    Returns:
        dict: ui_result（含 validated_operations）或 None（关键验证失败）
    """
    LOG.info("=" * 50)
    LOG.info("Stage 1: 前端按钮探测 + 业务闭环验证")
    LOG.info("=" * 50)

    # 导航到目标页
    await page.goto(target_url, wait_until="load", timeout=45000)

    # 等待 SPA 渲染就绪
    ready = await wait_for_spa_ready(page)
    if not ready:
        LOG.warning("SPA 渲染未完全就绪，继续尝试")

    # 清理 UI 残留
    await cleanup_ui_overlays(page)

    # Phase A+B: 探测 + 业务闭环验证（一体化）
    ui_result = await discover_and_validate(page)

    if ui_result is None:
        # 关键操作验证失败 — 尝试 Phase C/D/E 补救
        LOG.warning("  Phase A+B 失败，尝试后续阶段补救")
        # discover_and_validate 返回 None 时，ui_result 不可用
        # 需要重新做基础探测来拿到初始结果
        ui_result = await discover_all(page)

    # Phase B: 质量门控验证（required_elements）
    is_valid, issues, missing = validate_stage1(ui_result, required_flows=None)

    if is_valid:
        LOG.info("  Phase B: 质量门控通过 ✅")
    else:
        LOG.warning(f"  Phase B: 质量门控失败，{len(issues)} 个问题，{len(missing)} 个缺失元素")

        # Phase C: hints 定向重探
        if missing:
            ui_result = await _hints_rescan(page, ui_result, missing)
            is_valid, issues, missing = validate_stage1(ui_result, required_flows=None)

        # Phase D: 前置操作检查 + 重试
        if not is_valid and missing:
            ui_result = await _precondition_retry(page, ui_result, missing)
            is_valid, issues, missing = validate_stage1(ui_result, required_flows=None)

        # Phase E: Vision 截图分析兜底
        if not is_valid and missing:
            ui_result = await _vision_rescue(page, ui_result, missing)
            is_valid, issues, missing = validate_stage1(ui_result, required_flows=None)

        # Phase F: 终止判定
        if not is_valid:
            critical = [m for m in missing if m.get("critical")]
            if critical:
                LOG.error(f"  Phase F: ❌ {len(critical)} 个关键元素缺失，终止")
                for m in critical:
                    LOG.error(f"    - {m['desc']}")
                return None
            else:
                LOG.warning(f"  Phase F: 仅非关键元素缺失（{len(missing)} 个），继续")

    # 保存
    ws_dir = get_workspace_dir(project_dir)
    ws_dir.mkdir(parents=True, exist_ok=True)
    out_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_ui.json"
    _save_json(ui_result, out_path)
    LOG.info(f"结果已保存: {out_path}")

    # 注入 meta 信息到 ui_result（供 build_playbook 使用）
    ui_result["module_name"] = module_name
    ui_result["target_url"] = target_url
    # 从 target_url 推导 base_url
    from urllib.parse import urlparse
    parsed_url = urlparse(target_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    ui_result["base_url"] = base_url
    # 从 profile 加载 login_url
    profile = _load_profile(project_dir)
    login_url = profile.get("login_url", "")
    ui_result["login_url"] = login_url

    # 注入 auth_config（供 UI 脚本生成使用）
    profile_auth = profile.get("auth", {})
    ui_result["auth_config"] = {
        "token_key": profile_auth.get("token_key", ""),
        "token_storage": profile_auth.get("token_storage", "localStorage"),
        "cookie_token_key": profile_auth.get("cookie_token_key", ""),
    }

    # 生成并保存 playbook
    from core.discovery.discover_ui import build_playbook
    playbook = build_playbook(ui_result)
    playbook_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_playbook.json"
    _save_json(playbook, playbook_path)
    LOG.info(f"Playbook 已保存: {playbook_path}")

    # 打印摘要
    s = ui_result.get("summary", {})
    LOG.info(f"  按钮总数: {s.get('total_buttons', 0)}")
    LOG.info(f"  有创建按钮: {s.get('has_create')}")
    LOG.info(f"  有删除按钮: {s.get('has_delete')}")
    LOG.info(f"  表单字段数: {s.get('has_form')}")
    LOG.info(f"  CRUD 覆盖: {json.dumps(s.get('categories', {}), ensure_ascii=False)}")

    # 打印验证结果
    validated = ui_result.get("validated_operations", {})
    if validated:
        LOG.info(f"  已验证操作: {list(validated.keys())}")
        for action, info in validated.items():
            fill_count = len(info.get("fill_data", {}))
            LOG.info(f"    {action}: fill_data={fill_count} fields")

    return ui_result


async def run_stage2(page, project_dir: Path, module_name: str,
                     ui_result: dict, base_url: str, target_url: str,
                     max_recapture: int = 2,
                     capture_all_mode: bool = False,
                     version: str = "",
                     api_path_prefix: str = None) -> tuple:
    """Stage 2: API 捕获。

    Stage 1 已验证所有操作可成功，Stage 2 只做 API 捕获。
    保留重试机制以应对瞬时网络问题。

    Args:
        api_path_prefix: API 路径前缀，从 profile.yaml 的 api_base 读取。
                         若为 None 则自动从 profile 加载。

    Returns:
        (full_result, is_valid) 元组
    """
    LOG.info("=" * 50)
    LOG.info("Stage 2: API 捕获")
    LOG.info("=" * 50)

    # 自动从 profile 加载 api_path_prefix（若未显式传入）
    if api_path_prefix is None:
        profile = _load_profile(project_dir)
        api_path_prefix = profile.get("api_base", "")

    max_recapture = min(max_recapture, 5)
    ws_dir = get_workspace_dir(project_dir)
    ws_dir.mkdir(parents=True, exist_ok=True)

    for capture_attempt in range(max_recapture + 1):
        if capture_attempt > 0:
            LOG.info(f"\nStage 2 重试 #{capture_attempt}/{max_recapture}")
            try:
                await page.goto(target_url, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(2000)
            except Exception as e:
                LOG.warning(f"页面重置失败: {e}")

        # 确保在目标页面
        if not page.url or target_url not in page.url:
            await page.goto(target_url, wait_until="load", timeout=45000)
            await page.wait_for_timeout(5000)

        api_capture = await capture_all(
            page, ui_result, base_url, target_url,
            project_dir=project_dir, module_name=module_name,
            capture_all_mode=capture_all_mode,
            api_path_prefix=api_path_prefix
        )

        is_valid, issues = validate_stage2(api_capture)

        # 构建结果（无论验证是否通过）
        full_result = {
            "capture_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target_url": target_url,
            "stats": api_capture.get("stats", {}),
            "core_api_map": api_capture.get("core_api_map", {}),  # ★ 操作→候选数组映射
            "all_endpoints": api_capture.get("all_endpoints", []),
            "response_samples": api_capture.get("response_samples", {}),
            "pre_api_candidates": api_capture.get("pre_api_candidates", []),
            "operation_order": api_capture.get("operation_order", []),
        }
        out_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_capture.json"
        _save_json(full_result, out_path)

        if is_valid:
            LOG.info("Stage 2 验证通过")
            LOG.info(f"结果已保存: {out_path}")
            stats = full_result["stats"]
            LOG.info(f"  总拦截: {stats.get('total_calls', 0)} 条")
            LOG.info(f"  唯一端点: {stats.get('unique_endpoints', 0)} 个")
            for action, candidates in sorted(api_capture.get("core_api_map", {}).items()):
                LOG.info(f"    {action}: {len(candidates)} 个候选")

            # 生成 UI 自动化脚本
            from .generate_ui_script import generate_ui_script
            playbook_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_playbook.json"
            if playbook_path.exists():
                try:
                    with open(playbook_path, 'r', encoding='utf-8') as f:
                        playbook_data = json.load(f)
                    ver = version or ver_mod.resolve_version(project_dir)
                    script_path = generate_ui_script(playbook_data, module_name, project_dir, version=ver)
                    LOG.info(f"  UI 脚本已生成: {script_path}")
                except Exception as e:
                    LOG.warning(f"  UI 脚本生成失败: {e}")
            else:
                LOG.warning(f"  Playbook 文件不存在，跳过 UI 脚本生成: {playbook_path}")

            return full_result, True

        # 验证失败
        LOG.error(f"Stage 2 质量门控失败，发现 {len(issues)} 个问题:")
        for issue in issues:
            LOG.error(f"  - {issue}")

        if capture_attempt == max_recapture:
            LOG.error(f"Stage 2 已达最大重试次数 ({max_recapture})")
            LOG.info(f"结果已保存（不完整）: {out_path}")
            return full_result, False

    return None, False


def run_stage34(project_dir: Path, module_name: str,
                profile: dict, target_url: str, version: str = ""):
    """Stage 3 (分析) + Stage 4 (生成)。"""
    LOG.info("=" * 50)
    LOG.info("Stage 3 & 4: 逻辑分析 + 脚本生成")
    LOG.info("=" * 50)

    ws_dir = get_workspace_dir(project_dir)
    capture_result = _load_capture_result(project_dir, module_name)
    ui_result = _load_ui_result(project_dir, module_name)

    if not capture_result:
        LOG.error(f"未找到捕获结果: kb/module_discovered/{module_name}.json")
        LOG.error("请先执行 Stage 2")
        return None

    all_endpoints = capture_result.get("all_endpoints", [])
    response_samples = capture_result.get("response_samples", {})
    pre_api_candidates = capture_result.get("pre_api_candidates", [])
    # ★ 从 Stage 2 读取操作→核心API映射 + 操作顺序
    core_api_map = capture_result.get("core_api_map", {})
    operation_order = capture_result.get("operation_order", [])

    # Stage 3: 分析（传入 core_api_map + 操作顺序）
    flow = analyze(core_api_map, all_endpoints, response_samples, ui_result,
                   pre_api_candidates=pre_api_candidates, profile=profile,
                   operation_order=operation_order)

    # 阶段门控验证
    is_valid, issues = validate_stage3(flow)
    if not is_valid:
        LOG.error(f"Stage 3 质量门控失败，发现 {len(issues)} 个问题:")
        for issue in issues:
            LOG.error(f"  - {issue}")
        LOG.error("建议：检查分类结果是否准确、是否存在非标准 API 模式")
        LOG.warning("继续执行后续阶段，但结果可能不完整")

    # 保存分析结果
    analysis_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_analysis.json"
    _save_json(flow, analysis_path)
    LOG.info(f"分析结果已保存: {analysis_path}")

    # 打印分析摘要
    LOG.info(f"  执行顺序: {flow.get('crud_order', [])}")
    deps = flow.get("dependencies", {})
    if deps.get("injections"):
        LOG.info(f"  依赖注入: {list(deps['injections'].keys())}")
    sa = flow.get("state_assertions", {})
    if sa.get("state_field"):
        LOG.info(f"  状态字段: {sa['state_field']} → {sa.get('values_by_crud', {})}")

    # Stage 4: 生成脚本
    base_url = profile.get("base_url", "")
    login_url = profile.get("login_url", "")
    _creds = profile.get("credentials", {}) or {}
    version = version or ver_mod.resolve_version(project_dir)

    # 构建 manifest（泛化架构）
    manifest = build_manifest(flow, capture_result, profile,
                              module_name, target_url, ui_result)
    # 保存 manifest
    manifest_path = ws_dir / "kb" / "module_discovered" / f"{module_name}_manifest.json"
    _save_json(manifest, manifest_path)
    LOG.info(f"  Manifest 已保存: {manifest_path}")

    script = generate_script(manifest, module_name)

    # 阶段门控验证
    is_valid, issues = validate_stage4(script, None)
    if not is_valid:
        LOG.error(f"Stage 4 质量门控失败，发现 {len(issues)} 个问题:")
        for issue in issues:
            LOG.error(f"  - {issue}")
        LOG.warning("生成的脚本可能不完整或存在质量问题")

    script_path = save_script_to_file(
        script, str(project_dir), module_name, version=version, manifest=manifest
    )

    return flow, script_path, manifest


async def _run_stage4_verify(script_path: str, project_dir: Path, profile: dict,
                             login_url: str, username: str, password: str,
                             headless: bool = True) -> bool:
    """运行 Stage 4 生成的脚本，Token 过期时自动刷新 cookie 并重试。

    流程：
    1. 运行脚本，检查退出码
    2. 如果失败且检测到 Token 过期（HTTP 401），启动浏览器滑块登录刷新 cookie
    3. 将新 cookie 复制到脚本的 config/cookies.json
    4. 重新运行脚本

    Returns:
        True 如果脚本运行成功，False 如果失败
    """
    import subprocess

    script = Path(script_path)
    script_dir = script.parent

    # 脚本的 config 目录（cookie 存放位置）
    script_config = script_dir / "config"
    script_config.mkdir(parents=True, exist_ok=True)

    # 框架级的 cookie 目录
    framework_config = get_workspace_dir(project_dir) / "output" / "config"
    framework_config.mkdir(parents=True, exist_ok=True)

    # 预复制: 框架 cookies → 脚本 config（始终用最新 cookie 覆盖，避免使用过期旧 cookie）
    fw_cookie = framework_config / "cookies.json"
    sc_cookie = script_config / "cookies.json"
    if fw_cookie.exists():
        import shutil
        shutil.copy2(str(fw_cookie), str(sc_cookie))
        LOG.info(f"  Cookie 同步: {fw_cookie.name} → {script_config}")

    def _run_script() -> subprocess.CompletedProcess:
        """运行脚本并返回结果。"""
        LOG.info(f"  运行脚本: {script.name}")
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(script_dir), timeout=120,
        )
        # 打印脚本输出（关键行）
        for line in result.stdout.splitlines():
            if any(k in line for k in ("✅", "❌", "⚠️", "HTTP", "401", "Token", "Cookie", "assertion")):
                LOG.info(f"    {line.strip()}")
        return result

    def _is_token_expired(result: subprocess.CompletedProcess) -> bool:
        """检测脚本输出是否表明 Token 过期。"""
        output = result.stdout + result.stderr
        return any(k in output for k in ("HTTP 401", "401", "Token 已过期", "probe_url 返回 HTTP 401",
                                         "Cookie/Token 可能已过期"))

    # 第一次运行
    result = _run_script()

    # 即使退出码为 0，也要检查输出中的失败标记
    if result.returncode == 0:
        output = result.stdout + result.stderr
        # 检查是否有失败标记（但不包括 Token 过期相关的 401）
        if "❌" in output:
            # 统计失败次数
            fail_count = output.count("❌")
            # 检查是否是业务 API 失败（非 Token 过期）
            if "HTTP 401" in output or "HTTP 403" in output or "HTTP 5" in output:
                LOG.warning(f"  ⚠️ 脚本退出码为 0，但检测到 {fail_count} 个失败标记")
                LOG.warning(f"  ❌ 脚本运行失败 (业务 API 调用失败)")
                return False
        LOG.info("  ✅ 脚本运行成功")
        return True

    if not _is_token_expired(result):
        LOG.warning(f"  ❌ 脚本运行失败 (非 Token 问题，退出码={result.returncode})")
        if result.stderr.strip():
            LOG.warning(f"    stderr: {result.stderr[:500]}")
        return False

    # Token 过期 → 自动刷新
    LOG.info("  🔄 检测到 Token 过期，启动浏览器刷新 cookie...")

    if not username or not password:
        creds = profile.get("credentials", {}) or {}
        username = username or creds.get("username") or os.environ.get(creds.get("username_env", ""), "")
        password = password or creds.get("password") or os.environ.get(creds.get("password_env", ""), "")

    if not username or not password:
        LOG.error("  ❌ 无法自动刷新: 缺少登录凭据 (--user/--pass 或 profile.yaml)")
        return False

    # 启动浏览器滑块登录
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--ignore-certificate-errors", "--disable-web-security",
                  "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        try:
            ok = await _login_with_playwright(page, context, login_url, username, password,
                                              project_profile=profile)
            if not ok:
                LOG.error("  ❌ 滑块登录失败")
                return False

            # 保存新 cookie 到框架目录
            cookies = await context.cookies()
            framework_cookie = framework_config / "cookies.json"
            framework_cookie.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            LOG.info(f"  ✅ 新 cookie 已保存: {framework_cookie} ({len(cookies)} 条)")

            # 同步到脚本的 config/cookies.json
            script_cookie = script_config / "cookies.json"
            script_cookie.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
            LOG.info(f"  ✅ 已同步到脚本目录: {script_cookie}")

        finally:
            await browser.close()

    # 第二次运行（用新 cookie）
    LOG.info("  🔄 使用新 cookie 重新运行...")
    result = _run_script()

    if result.returncode == 0:
        # 同样检查输出中的失败标记
        output = result.stdout + result.stderr
        if "❌" in output:
            if "HTTP 401" in output or "HTTP 403" in output or "HTTP 5" in output:
                LOG.warning(f"  ⚠️ 脚本退出码为 0，但检测到业务 API 调用失败")
                LOG.error(f"  ❌ 脚本运行失败（刷新 Token 后业务 API 仍失败）")
                return False
        LOG.info("  ✅ 脚本运行成功（Token 已刷新）")
        return True

    LOG.error(f"  ❌ 脚本运行失败（即使刷新 Token 后仍失败，退出码={result.returncode})")
    if result.stderr.strip():
        LOG.error(f"    stderr: {result.stderr[:500]}")
    return False


async def _run_ui_script(ui_script_path: str, headless: bool = True) -> bool:
    """运行 UI 测试脚本并生成报告。

    Args:
        ui_script_path: UI 脚本路径
        headless: 是否使用无头模式

    Returns:
        True 如果脚本运行成功，False 如果失败
    """
    import subprocess

    script = Path(ui_script_path)
    if not script.exists():
        LOG.warning(f"  ⚠️ UI 脚本不存在: {script.name}")
        return False

    script_dir = script.parent

    LOG.info(f"  运行 UI 脚本: {script.name}")

    # 构建命令
    cmd = [sys.executable, str(script)]
    if headless:
        cmd.append("--headless")

    result = subprocess.run(
        cmd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(script_dir), timeout=180,
    )

    # 打印脚本输出（关键行）
    for line in result.stdout.splitlines():
        if any(k in line for k in ("✅", "❌", "⚠️", "报告", "report", "通过", "失败", "完成")):
            LOG.info(f"    {line.strip()}")

    if result.returncode == 0:
        output = result.stdout + result.stderr
        # 检查是否有失败标记
        fail_count = output.count("❌")
        pass_count = output.count("✅")

        if fail_count > 0 and pass_count == 0:
            LOG.warning(f"  ⚠️ UI 脚本退出码为 0，但检测到 {fail_count} 个失败标记")
            return False

        LOG.info("  ✅ UI 脚本运行成功")
        return True

    LOG.warning(f"  ⚠️ UI 脚本运行失败（退出码={result.returncode}）")
    if result.stderr.strip():
        LOG.warning(f"    stderr: {result.stderr[:300]}")
    return False


def run_stage5(manifest: dict, project_dir: Path, module_name: str, version: str = ""):
    """Stage 5: 导出 Postman Collection / helpers.py / Excel 参数文件。

    Args:
        manifest: 完整的 manifest 字典（包含 pre_apis 等增强字段）
        project_dir: 项目目录
        module_name: 模块名称
        version: 版本号
    """
    LOG.info("=" * 50)
    LOG.info("Stage 5: 导出 Artifacts (Postman / helpers / Excel)")
    LOG.info("=" * 50)

    try:
        from .export_artifacts import (
            export_postman_collection,
            export_helpers,
            export_excel_params
        )
    except ImportError as e:
        LOG.error(f"无法导入 export_artifacts 模块: {e}")
        return

    # 导出目录 — 输出到 projects/<project>/<version>/export/<module>
    version = version or ver_mod.resolve_version(project_dir)
    export_dir = project_dir / version / "export" / module_name
    export_dir.mkdir(parents=True, exist_ok=True)

    # 1. Postman Collection
    postman_path = export_dir / f"{module_name}.postman_collection.json"
    try:
        export_postman_collection(manifest, postman_path)
        LOG.info(f"  ✅ Postman Collection: {postman_path}")
    except Exception as e:
        LOG.error(f"  ❌ Postman Collection 导出失败: {e}")

    # 2. helpers.py
    helpers_path = export_dir / "helpers.py"
    try:
        export_helpers(manifest, helpers_path)
        LOG.info(f"  ✅ helpers.py: {helpers_path}")
    except Exception as e:
        LOG.error(f"  ❌ helpers.py 导出失败: {e}")

    # 3. Excel 参数文件
    excel_path = export_dir / f"{module_name}_params.xlsx"
    try:
        export_excel_params(manifest, excel_path)
        LOG.info(f"  ✅ Excel 参数文件: {excel_path}")
    except Exception as e:
        LOG.error(f"  ❌ Excel 参数文件导出失败: {e}")

    LOG.info(f"Stage 5 导出完成: {export_dir}")

    # 4. 自动生成 README.md（如果不存在）
    version_dir = project_dir / version
    readme_path = version_dir / "README.md"
    if not readme_path.exists():
        _generate_readme(version_dir, project_dir.name, version)


def _generate_readme(version_dir: Path, project_name: str, version: str):
    """在版本目录下生成 README.md 操作手册。

    Args:
        version_dir: 版本目录（如 projects/ecm-compute/v1.0.0）
        project_name: 项目名称
        version: 版本号
    """
    readme_path = version_dir / "README.md"

    # 扫描实际存在的脚本文件
    api_dir = version_dir / "api"
    ui_dir = version_dir / "ui"
    export_dir = version_dir / "export"

    api_scripts = []
    ui_scripts = []
    if api_dir.exists():
        api_scripts = [f.name for f in api_dir.glob("*_API测试.py")]
    if ui_dir.exists():
        ui_scripts = [f.name for f in ui_dir.glob("*.py") if not f.name.startswith("_")]

    api_script_list = "\n".join(f"  - `{s}`" for s in api_scripts) if api_scripts else "  - (暂无)"
    ui_script_list = "\n".join(f"  - `{s}`" for s in ui_scripts) if ui_scripts else "  - (暂无)"

    readme_content = f"""# {project_name} - 自动化测试脚本包

> 版本: {version}
> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 说明: 本目录包含完整的 API + UI 自动化测试脚本，可直接拷贝到其他机器运行。

## 📦 目录结构

```
{version}/
├── api/                    # API 自动化测试
│   ├── config/            # 配置文件（cookie 等）
│   ├── lib/               # 运行时库
│   ├── helpers.py         # 辅助函数
│   └── *_API测试.py       # API 测试脚本
├── ui/                     # UI 自动化测试
│   ├── config/            # 配置文件（cookie 等）
│   ├── lib/               # 运行时库
│   └── *.py               # UI 测试脚本
├── export/                 # 导出的测试资产
│   └── <模块>/
│       ├── *.postman_collection.json
│       ├── helpers.py
│       └── *_params.xlsx
└── README.md              # 本文件
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 安装 Python 3.8+
python --version

# 安装依赖
pip install requests playwright openpyxl
playwright install chromium
```

### 2. 配置认证

**方式 A：使用现有 Cookie（推荐）**

1. 将已登录的浏览器 cookie 导出为 JSON 格式
2. 保存到以下位置：
   - API 测试: `api/config/cookies.json`
   - UI 测试: `ui/config/cookies.json`

**方式 B：从原始目录同步**

如果从生成脚本的原始目录拷贝，cookie 已自动同步到 `api/config/` 和 `ui/config/`。

### 3. 运行测试

#### API 测试

```bash
# 进入 API 目录
cd api

# 运行单个模块测试
python 角色管理_API测试.py

# 运行所有 API 测试
for f in *_API测试.py; do python "$f"; done
```

**报告生成位置**: `api/reports/`

#### UI 测试

```bash
# 进入 UI 目录
cd ui

# 运行单个模块测试（有界面）
python 角色管理.py

# 运行单个模块测试（无头模式）
python 角色管理.py --headless

# 运行所有 UI 测试
for f in *.py; do [[ "$f" != "__"* ]] && python "$f" --headless; done
```

**报告生成位置**: `ui/reports/`

#### 批量运行

```bash
# 返回上级目录（包含 run_suite.py 的目录）
cd ..

# 运行所有 API 测试
python run_suite.py --project {project_name} --version {version} --type api

# 运行所有 UI 测试
python run_suite.py --project {project_name} --version {version} --type ui

# 运行所有测试（API + UI）
python run_suite.py --project {project_name} --version {version} --type all
```

## 📋 已生成的测试脚本

### API 测试脚本
{api_script_list}

### UI 测试脚本
{ui_script_list}

## 🔧 常见问题

### Q1: 运行时提示 "Cookie 过期" 或 "401 Unauthorized"

**原因**: Cookie 已过期

**解决方法**:
1. 重新登录系统，获取新的 cookie
2. 更新 `api/config/cookies.json` 和 `ui/config/cookies.json`
3. 或者从原始目录重新拷贝整个 `{version}/` 目录

### Q2: UI 测试找不到元素

**原因**: 页面结构可能已变更

**解决方法**:
1. 检查目标系统是否更新过界面
2. 联系开发人员确认元素定位器是否需要更新
3. 重新运行发现流程生成新的脚本

### Q3: 如何添加新模块的测试？

**方法**: 在原始目录重新运行发现流程：

```bash
# 在原始目录（包含 core/discovery/run.py 的目录）
python -m core.discovery.run --project {project_name} --module "新模块名称" --url "/path/to/module"

# 然后将生成的脚本拷贝到本目录
```

### Q4: 如何修改测试数据？

**方法**: 编辑脚本中的 `test_data` 部分：

```python
# 打开 *_API测试.py 或 *.py
# 找到 test_data 字典
test_data = {{
    "角色名称": "AT_test_xxx",  # 修改这里
    "描述": "测试角色",          # 修改这里
}}
```

## 📊 测试报告

- **API 报告**: `api/reports/<模块>/` - Postman 风格 HTML 报告
- **UI 报告**: `ui/reports/` - 包含截图的 HTML 报告
- **原始日志**: 生成在原始目录的 `projects/{project_name}/output/` 下

## 🔗 相关资源

- **Postman Collection**: `export/<模块>/*.postman_collection.json`
  - 可导入 Postman 进行手动测试
- **Excel 参数文件**: `export/<模块>/*_params.xlsx`
  - 包含所有测试数据，可用于数据驱动测试
- **Helpers 函数**: `api/helpers.py`
  - 包含数据生成辅助函数，可在其他脚本中复用

## 📝 注意事项

1. **Cookie 有效期**: Cookie 通常有时效，过期后需要重新获取
2. **环境一致性**: 确保测试环境的接口地址与生产环境一致
3. **数据清理**: 测试产生的数据（如创建的角色）需要在测试后手动清理，或运行删除脚本
4. **并发问题**: 避免多人同时运行相同模块的测试，可能产生数据冲突

## 🆘 技术支持

如遇到问题，请联系：
- 开发人员: [填写开发人员联系方式]
- 测试框架维护: [填写框架维护者联系方式]

---

*本文档由自动化测试框架自动生成*
"""

    readme_path.write_text(readme_content, encoding="utf-8")
    LOG.info(f"✅ README.md 已生成: {readme_path}")


async def _ensure_login(page, context, profile, login_url, target_url, args, workspace_dir):
    """公共登录逻辑：尝试 cookie 复用，失败则完整登录。

    Args:
        page: Playwright page
        context: Playwright context
        profile: 项目 profile
        login_url: 登录页 URL
        target_url: 验证 cookie 有效性用的目标 URL
        args: argparse 参数
        workspace_dir: workspace 目录

    Returns:
        bool: 是否登录成功
    """
    LOG.info("登录...")
    creds = profile.get("credentials", {}) or {}
    username = (args.user
                or creds.get("username")
                or os.environ.get(creds.get("username_env", ""), "")
                or "")
    password = (args.password
                or creds.get("password")
                or os.environ.get(creds.get("password_env", ""), "")
                or "")

    if not username or not password:
        LOG.error("缺少登录凭据，请通过 --user/--pass 提供")
        return False

    # 尝试从 workspace 加载已有 cookie
    cookie_file = workspace_dir / "output" / "config" / "cookies.json"
    logged_in = False
    if cookie_file.exists():
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            if cookies:
                await context.add_cookies(cookies)
                # 注入 token 到 localStorage
                token_key = profile.get("auth", {}).get("token_key", "accessToken")
                token_cookie = next((c["value"] for c in cookies if c["name"] == token_key), None)
                if token_cookie:
                    await context.add_init_script(f"""
                        localStorage.setItem('{token_key}', '{token_cookie}');
                    """)
                try:
                    await page.goto(target_url, wait_until="load", timeout=30000)
                except Exception:
                    pass
                await page.wait_for_timeout(5000)
                if "/login" not in page.url:
                    LOG.info("  ✅ Cookie 有效，直接进入目标页")
                    logged_in = True
                    # Cookie 路径：刷新页面确保 SPA 组件完整渲染（解决 DOM 未渲染问题）
                    try:
                        await page.reload(wait_until="networkidle", timeout=30000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    # 重新保存 cookie（页面加载可能刷新了 cookie）
                    fresh_cookies = await context.cookies()
                    cookie_file.parent.mkdir(parents=True, exist_ok=True)
                    cookie_file.write_text(json.dumps(fresh_cookies, ensure_ascii=False, indent=2), encoding="utf-8")
                else:
                    LOG.info("  ⚠️ Cookie 已过期，将执行完整登录")
        except Exception:
            pass

    if not logged_in:
        LOG.info("  执行滑块登录...")
        ok = await _login_with_playwright(page, context, login_url, username, password,
                                          project_profile=profile)
        if not ok:
            LOG.error("❌ 登录失败")
            return False
        LOG.info("  ✅ 登录成功")

        # 保存凭据到全局凭据库
        from core.discovery.io_helpers import save_credentials
        credentials_to_save = {
            "username": username,
            "password": password,
            "base_url": profile.get("base_url", ""),
            "login_url": login_url,
        }
        if "auth" in profile:
            credentials_to_save["auth"] = profile["auth"]
        save_credentials(args.project, credentials_to_save)

        # 保存 cookie
        token_key = profile.get("auth", {}).get("token_key", "accessToken")
        cookies = await context.cookies()
        token_cookie = next((c["value"] for c in cookies if c["name"] == token_key), None)

        # 如果 cookie 中没有 token，从 localStorage 获取并添加为 cookie
        if not token_cookie:
            try:
                ls_token = await page.evaluate(f"() => localStorage.getItem('{token_key}')")
                if ls_token:
                    base_url = profile.get("base_url", "")
                    # 提取 domain（去掉协议和端口）
                    import re as _re
                    domain_match = _re.search(r'https?://([^/:]+)', base_url)
                    domain = domain_match.group(1) if domain_match else ""
                    if domain:
                        token_cookie_obj = {
                            "name": token_key,
                            "value": ls_token,
                            "domain": domain,
                            "path": "/",
                            "expires": -1,
                            "httpOnly": False,
                            "secure": False,
                            "sameSite": "Lax",
                        }
                        await context.add_cookies([token_cookie_obj])
                        cookies = await context.cookies()
                        token_cookie = ls_token
                        LOG.info(f"  ✅ 已从 localStorage 注入 {token_key} 到 cookie")
            except Exception as e:
                LOG.warning(f"  ⚠️ 从 localStorage 获取 token 失败: {e}")

        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

        # 用 addInitScript 注入 token 到 localStorage（所有后续页面）
        if token_cookie:
            await context.add_init_script(f"""
                localStorage.setItem('{token_key}', '{token_cookie}');
            """)
            LOG.info("  ✅ 已通过 addInitScript 注入 token 到 localStorage")

        # 跳转到目标 URL
        try:
            await page.goto(target_url, wait_until="load", timeout=45000)
        except Exception:
            pass
        await page.wait_for_timeout(5000)

    return True


async def _run_discover_mode(project_dir: Path, profile: dict, base_url: str, login_url: str, args):
    """自动发现模式：从主页面发现所有模块并批量处理。

    流程：
      1. 交互式收集参数（主页面 URL、版本号）
      2. 登录系统
      3. 导航自动发现（展开菜单 → 提取模块 URL）
      4. 与已有脚本对比
      5. 交互式选择要处理的模块
      6. 批量执行 Stage 1-5
    """
    from core.discovery.discover_navigation import (
        discover_modules,
        compare_with_existing,
        interactive_select,
    )

    LOG.info("")
    LOG.info("=" * 70)
    LOG.info("Phase 0: 导航自动发现模式")
    LOG.info("=" * 70)

    # 1. 交互式收集参数
    # 主页面 URL（默认使用 login_url 去掉 /login，即应用主页）
    app_home_url = login_url.replace("/login", "")
    default_home_url = profile.get("home_url") or app_home_url
    try:
        home_url_input = input(f"主页面 URL [默认 {default_home_url}]: ").strip()
        home_url = home_url_input if home_url_input else default_home_url
    except (EOFError, KeyboardInterrupt):
        home_url = default_home_url
        print()

    LOG.info(f"使用主页面: {home_url}")

    # 版本号（已在 main() 中处理，这里只是确认）
    version = args.version or ver_mod.resolve_version(project_dir)
    LOG.info(f"使用版本: {version}")

    # 2. 启动浏览器并登录
    if args.offline:
        LOG.error("离线模式不支持 --discover，请使用在线模式")
        sys.exit(1)

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            args=["--ignore-certificate-errors", "--disable-web-security",
                  "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # 复用公共登录逻辑
        workspace_dir = get_workspace_dir(project_dir)
        login_ok = await _ensure_login(page, context, profile, login_url, home_url, args, workspace_dir)
        if not login_ok:
            await browser.close()
            return

        # 3. 导航自动发现
        LOG.info("")
        LOG.info("Phase 0.5: 导航自动发现")
        LOG.info("-" * 70)

        # 记录登录后的实际页面 URL（这是真正的门户页面）
        landing_url = page.url
        LOG.info(f"  登录后着陆页: {landing_url}")

        # 如果用户指定了不同的 home_url 且不是着陆页，才导航过去
        if home_url and home_url != landing_url and not landing_url.endswith("/portal"):
            LOG.info(f"  导航到指定主页: {home_url}")
            await page.goto(home_url, wait_until="load", timeout=45000)
            await page.wait_for_timeout(3000)
        else:
            # 使用着陆页，只需等待渲染
            await page.wait_for_timeout(3000)

        # 调用导航发现
        discovered = await discover_modules(
            page, context, base_url, login_url, profile, project_dir,
            force=args.force
        )

        if not discovered:
            LOG.error("❌ 未发现任何有效模块")
            LOG.info("  可能原因：")
            LOG.info("  1. 当前页面不包含侧边栏菜单")
            LOG.info("  2. SPA 未完全加载")
            LOG.info("  3. 登录凭据已过期")
            LOG.info("")

            # 交互式提示用户输入正确的URL
            try:
                LOG.info("请输入一个包含侧边栏菜单的页面URL（例如用户管理或角色管理页面）：")
                new_url = input(f"URL [{home_url}]: ").strip()
                if new_url:
                    home_url = new_url
                    LOG.info(f"导航到: {home_url}")
                    await page.goto(home_url, wait_until="load", timeout=45000)
                    await page.wait_for_timeout(3000)

                    # 重新尝试发现
                    discovered = await discover_modules(
                        page, context, base_url, login_url, profile, project_dir,
                        force=args.force
                    )
            except (EOFError, KeyboardInterrupt):
                pass

            if not discovered:
                LOG.error("仍未发现有效模块，退出")
                await browser.close()
                return

        # 4. 与已有脚本对比
        version_dir = project_dir / version
        modules_with_status = compare_with_existing(discovered, version_dir)

        new_count = sum(1 for m in modules_with_status if not m.get("has_script"))
        existing_count = sum(1 for m in modules_with_status if m.get("has_script"))
        LOG.info(f"  发现 {len(modules_with_status)} 个模块: {new_count} 个新模块, {existing_count} 个已有脚本")

        # 5. 交互式选择
        selected = interactive_select(modules_with_status)

        if not selected:
            LOG.info("未选择任何模块，退出")
            await browser.close()
            return

        LOG.info(f"  选择了 {len(selected)} 个模块进行处理")

        # 6. 批量执行（复用已有的 run_all_modules 管线）
        LOG.info("")
        LOG.info("Phase 1-5: 批量执行")
        LOG.info("-" * 70)

        # 格式适配：discover 输出 {"label": ...} → run_all_modules 期望 {"name": ...}
        modules_for_pipeline = [
            {"name": m["label"], "url": m["url"]}
            for m in selected
        ]

        await run_all_modules(
            page, context, project_dir, profile, base_url, login_url, args,
            run_stage1=run_stage1,
            run_stage2=run_stage2,
            run_stage34=run_stage34,
            _run_stage4_verify=_run_stage4_verify,
            run_stage5=run_stage5,
            modules_override=modules_for_pipeline,
            _run_ui_script=_run_ui_script
        )

        await browser.close()

    LOG.info("")
    LOG.info("✅ 自动发现模式完成")


def _resolve_home_url(profile, login_url, args) -> str:
    """解析主页面 URL：优先 --home-url 参数，其次 profile 配置，最后推导。"""
    if args.home_url:
        return args.home_url
    default = profile.get("home_url") or login_url.replace("/login", "")
    try:
        home_url_input = input(f"主页面 URL [默认 {default}]: ").strip()
        return home_url_input if home_url_input else default
    except (EOFError, KeyboardInterrupt):
        print()
        return default


async def _discover_and_cache(project_dir: Path, profile: dict, base_url: str,
                              login_url: str, args) -> bool:
    """阶段 1：登录 → 爬取菜单 → 对比已有脚本 → 输出列表 → 保存缓存。

    供 --discover-only 使用，也供 --discover 模式复用。

    Returns:
        (bool, list): (是否成功, 带对比状态的模块列表)
    """
    from core.discovery.discover_navigation import (
        discover_modules,
        compare_with_existing,
    )
    from core.discovery.nav_discovery import save_discovery_result

    home_url = _resolve_home_url(profile, login_url, args)
    version = args.version or ver_mod.resolve_version(project_dir)

    LOG.info("")
    LOG.info("=" * 70)
    LOG.info("Phase 0: 导航发现（仅探测）")
    LOG.info("=" * 70)
    LOG.info(f"  主页面: {home_url}")
    LOG.info(f"  版本: {version}")

    if args.offline:
        LOG.error("离线模式不支持 --discover-only")
        return False, []

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            args=["--ignore-certificate-errors", "--disable-web-security",
                  "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        workspace_dir = get_workspace_dir(project_dir)
        login_ok = await _ensure_login(page, context, profile, login_url, home_url, args, workspace_dir)
        if not login_ok:
            await browser.close()
            return False, []

        # 导航到主页面
        landing_url = page.url
        LOG.info(f"  登录后着陆页: {landing_url}")

        if home_url != landing_url and not landing_url.endswith("/portal"):
            LOG.info(f"  导航到指定主页: {home_url}")
            await page.goto(home_url, wait_until="load", timeout=45000)
            await page.wait_for_timeout(3000)
        else:
            await page.wait_for_timeout(3000)

        # 爬取菜单
        discovered = await discover_modules(
            page, context, base_url, login_url, profile, project_dir,
            force=args.force
        )

        if not discovered:
            LOG.error("❌ 未发现任何模块")
            LOG.info("  请通过 --home-url 提供包含侧边栏菜单的页面 URL")
            await browser.close()
            return False, []

        await browser.close()

    # 与已有脚本对比
    version_dir = project_dir / version
    modules_with_status = compare_with_existing(discovered, version_dir)

    new_count = sum(1 for m in modules_with_status if not m.get("has_script"))
    existing_count = sum(1 for m in modules_with_status if m.get("has_script"))

    # 输出结果
    output_format = getattr(args, "output_format", "text") or "text"

    if output_format == "json":
        from core.discovery.discover_navigation import format_discovery_result
        result_json = format_discovery_result(modules_with_status)
        print(result_json)
    else:
        _print_discovery_summary(modules_with_status, new_count, existing_count)

    LOG.info(f"  发现 {len(modules_with_status)} 个模块: {new_count} 个新模块, {existing_count} 个已有脚本")
    LOG.info(f"  缓存文件: {workspace_dir / 'kb' / 'navigation_discovered.json'}")

    return True, modules_with_status


def _print_discovery_summary(modules_with_status: list, new_count: int, existing_count: int):
    """打印发现结果摘要（text 格式）。"""
    print()
    print("=" * 70)
    print(f"  发现 {len(modules_with_status)} 个模块: {new_count} 个新模块, {existing_count} 个已有脚本")
    print("=" * 70)
    print(f"  {'编号':<6} {'模块名称':<20} {'分组':<20} {'状态'}")
    print("  " + "-" * 64)

    for i, m in enumerate(modules_with_status, 1):
        label = m["label"]
        group = m.get("group", "")
        status = "✅ 已有" if m.get("has_script") else "🆕 新模块"
        print(f"  [{i:>2}]  {label:<18} {group:<18} {status}")

    print()
    print("  使用以下命令执行选定模块的管线：")
    print("  python -m core.discovery.run --project <项目名> --discover-select \"1,3,5\"")
    print("  python -m core.discovery.run --project <项目名> --discover-select \"all\"")
    print()


async def _execute_selected_modules(project_dir: Path, profile: dict,
                                     base_url: str, login_url: str,
                                     selected_ids, args):
    """阶段 2：从缓存加载 → 按编号筛选 → 启动浏览器 → 执行管线。

    Args:
        selected_ids: 选中的模块编号列表（如 [1, 3, 5]），None 表示全部
    """
    from core.discovery.discover_navigation import compare_with_existing
    from core.discovery.nav_discovery import load_discovery_result

    workspace_dir = get_workspace_dir(project_dir)
    version = args.version or ver_mod.resolve_version(project_dir)

    # 1. 从缓存加载
    cached = load_discovery_result(workspace_dir, max_age_days=7)
    if not cached:
        LOG.error("❌ 缓存不存在或已过期，请先运行 --discover-only")
        sys.exit(1)

    # 对比已有脚本
    version_dir = project_dir / version
    modules_with_status = compare_with_existing(cached, version_dir)

    # 2. 按编号筛选
    if selected_ids is None:
        selected = list(modules_with_status)
        LOG.info(f"选择了全部 {len(selected)} 个模块")
    else:
        selected = []
        for idx in selected_ids:
            if 1 <= idx <= len(modules_with_status):
                selected.append(modules_with_status[idx - 1])
            else:
                LOG.warning(f"  编号 {idx} 超出范围（1-{len(modules_with_status)}），已忽略")

        if not selected:
            LOG.error("❌ 未选择任何有效模块")
            sys.exit(1)

        LOG.info(f"选择了 {len(selected)} 个模块:")
        for m in selected:
            LOG.info(f"  - {m['label']} ({m.get('group', '')})")

    # 3. 启动浏览器 + 登录 + 执行
    home_url = _resolve_home_url(profile, login_url, args)

    if args.offline:
        # 离线模式不需要浏览器，直接执行 Stage 34
        LOG.info("离线模式：跳过 Stage 1-2，仅执行 Stage 34")
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await _run_discovered_pipeline(page, context, project_dir, profile,
                                            base_url, login_url, selected, args)
            await browser.close()
        return

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            args=["--ignore-certificate-errors", "--disable-web-security",
                  "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        login_ok = await _ensure_login(page, context, profile, login_url, home_url, args, workspace_dir)
        if not login_ok:
            await browser.close()
            return

        await _run_discovered_pipeline(page, context, project_dir, profile,
                                        base_url, login_url, selected, args)
        await browser.close()

    LOG.info("")
    LOG.info("✅ 选择执行完成")


async def _run_discovered_pipeline(page, context, project_dir, profile,
                                    base_url, login_url, selected, args):
    """从选中的模块列表执行 Stage 1-5 管线。"""
    LOG.info("")
    LOG.info("Phase 1-5: 批量执行")
    LOG.info("-" * 70)

    modules_for_pipeline = [
        {"name": m["label"], "url": m["url"]}
        for m in selected
    ]

    await run_all_modules(
        page, context, project_dir, profile, base_url, login_url, args,
        run_stage1=run_stage1,
        run_stage2=run_stage2,
        run_stage34=run_stage34,
        _run_stage4_verify=_run_stage4_verify,
        run_stage5=run_stage5,
        modules_override=modules_for_pipeline,
        _run_ui_script=_run_ui_script
    )


async def main():
    # Windows GBK 编码兼容：日志中的 emoji/中文不崩溃
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    args = parse_args()
    project_dir = ROOT / "projects" / args.project
    if not project_dir.exists():
        LOG.error(f"项目目录不存在: {project_dir}")
        sys.exit(1)

    # 交互式版本提示（仅在未通过 --version 指定时）
    if args.version is None:
        default_ver = ver_mod.resolve_version(project_dir)
        existing_versions = ver_mod.available_versions(project_dir)
        if existing_versions:
            LOG.info(f"已有版本: {', '.join(existing_versions)}")
        try:
            user_input = input(f"请输入版本号 [默认 {default_ver}]: ").strip()
            args.version = user_input if user_input else default_ver
        except (EOFError, KeyboardInterrupt):
            # 非交互模式或用户中断，使用默认值
            args.version = default_ver
        LOG.info(f"使用版本: {args.version}")

    workspace_dir = get_workspace_dir(project_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    profile = _load_profile(project_dir)
    base_url = profile.get("base_url")
    if not base_url:
        LOG.error("profile.yaml 缺少 base_url 配置")
        sys.exit(1)
    login_url = profile.get("login_url")
    if not login_url:
        LOG.error("profile.yaml 缺少 login_url 配置")
        sys.exit(1)

    # 自动发现模式（--discover）
    if args.discover:
        await _run_discover_mode(project_dir, profile, base_url, login_url, args)
        return

    # 仅发现模式（--discover-only）
    if args.discover_only:
        await _discover_and_cache(project_dir, profile, base_url, login_url, args)
        return

    # 选择执行模式（--discover-select）
    if args.discover_select:
        # 解析选择：'1,3,5' → [1,3,5]，'all' → None
        if args.discover_select.strip().lower() == "all":
            selected_ids = None
        else:
            try:
                selected_ids = [int(x.strip()) for x in args.discover_select.split(",")]
            except ValueError:
                LOG.error(f"❌ --discover-select 格式错误: '{args.discover_select}'")
                LOG.error("   请使用逗号分隔的数字，如 '1,3,5'，或 'all'")
                sys.exit(1)

        await _execute_selected_modules(project_dir, profile, base_url,
                                         login_url, selected_ids, args)
        return

    # 批量发现模式
    if args.all_modules:
        # 离线模式不支持批量发现（需要浏览器）
        if args.offline:
            LOG.error("离线模式不支持 --all-modules，请使用在线模式")
            sys.exit(1)

        # 批量模式不需要 --url 和 --module
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=args.headless,
                args=["--ignore-certificate-errors", "--disable-web-security",
                      "--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1600, "height": 1000},
                locale="zh-CN",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            page = await context.new_page()

            # ---- 鉴权（统一登录）----
            login_ok = await _ensure_login(page, context, profile, login_url, base_url, args, workspace_dir)
            if not login_ok:
                await browser.close()
                return

            # 执行批量发现
            await run_all_modules(
                page, context, project_dir, profile, base_url, login_url, args,
                run_stage1=run_stage1,
                run_stage2=run_stage2,
                run_stage34=run_stage34,
                _run_stage4_verify=_run_stage4_verify,
                run_stage5=run_stage5
            )

        return

    # Stage 5 独立运行（无需 --url，仅需 --module）
    if args.stage == "5":
        if not args.module:
            LOG.error("Stage 5 需要 --module 参数")
            sys.exit(1)
        version = args.version or ver_mod.resolve_version(project_dir)
        manifest_path = workspace_dir / "kb" / "module_discovered" / f"{args.module}_manifest.json"
        if not manifest_path.exists():
            LOG.error(f"❌ manifest 不存在: {manifest_path}")
            LOG.error("   请先运行 --stage 34 生成 manifest")
            sys.exit(1)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        LOG.info(f"已从文件加载 manifest: {manifest_path}")
        run_stage5(manifest, project_dir, args.module, version=version)
        return

    # 单模块模式（原有逻辑）
    if not args.url or not args.module:
        LOG.error("单模块模式需要 --url 和 --module 参数")
        sys.exit(1)

    target_url = _normalize_url(args.url, base_url)

    # 离线模式: Stage 3+4 → 运行脚本 → Stage 5（如果脚本成功）
    if args.offline:
        result = run_stage34(project_dir, args.module, profile, target_url,
                    version=args.version or ver_mod.resolve_version(project_dir))
        if result:
            flow, script_path, manifest = result

            # 运行生成的脚本（如果失败且 Token 过期，自动刷新）
            script_ok = await _run_stage4_verify(
                script_path, project_dir, profile, login_url,
                username=(args.user or ""), password=(args.password or ""),
                headless=args.headless
            )

            # 只有脚本成功才执行 Stage 5
            if script_ok and args.export:
                run_stage5(manifest, project_dir, args.module,
                           version=args.version or ver_mod.resolve_version(project_dir))
            elif not script_ok:
                LOG.warning("⚠️ 脚本运行失败，跳过 Stage 5 导出")
        return

    # 在线模式: 启动浏览器
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=args.headless,
            args=["--ignore-certificate-errors", "--disable-web-security",
                  "--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # ---- 鉴权（统一登录）----
        login_ok = await _ensure_login(page, context, profile, login_url, target_url, args, workspace_dir)
        if not login_ok:
            await browser.close()
            return

        # ---- 各阶段执行 ----
        stage = args.stage

        # Stage 5 独立运行：跳过 1/2/34，直接加载 manifest 并导出
        if stage == "5":
            version = args.version or ver_mod.resolve_version(project_dir)
            manifest_path = workspace_dir / "kb" / "module_discovered" / f"{args.module}_manifest.json"
            if not manifest_path.exists():
                LOG.error(f"❌ manifest 不存在: {manifest_path}")
                LOG.error("   请先运行 --stage 34 生成 manifest")
                return
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            LOG.info(f"已从文件加载 manifest: {manifest_path}")
            run_stage5(manifest, project_dir, args.module, version=version)
            return

        # Stage 1
        if stage in ("1", "all"):
            ui_result = await run_stage1(page, project_dir, args.module, target_url)
        else:
            ui_result = _load_ui_result(project_dir, args.module)

        if not ui_result:
            LOG.warning("未找到 UI 探测结果，将使用空结果")

        # Stage 2
        stage2_valid = True
        if stage in ("2", "all"):
            capture_result, stage2_valid = await run_stage2(
                page, project_dir, args.module, ui_result or {}, base_url, target_url,
                max_recapture=args.max_recapture,
                capture_all_mode=args.capture_all,
                version=args.version or ver_mod.resolve_version(project_dir),
                api_path_prefix=profile.get("api_base", ""))
        else:
            capture_result = _load_capture_result(project_dir, args.module)
            stage2_valid = bool(capture_result and capture_result.get("core_api_map"))

        # Stage 3+4（可以离线执行）
        manifest = None
        script_path = None
        if stage in ("34", "4", "all"):
            if not stage2_valid and not args.force_gen:
                LOG.error("❌ Stage 2 验证失败，拒绝生成脚本（使用 --force-gen 强制覆盖）")
                LOG.error("   请先修复捕获问题或检查调试工件: "
                          f"{workspace_dir / 'output' / 'debug' / args.module}")
            else:
                if not stage2_valid and args.force_gen:
                    LOG.warning("⚠️ --force-gen: 强制生成脚本（Stage 2 验证未通过）")
                result = run_stage34(project_dir, args.module, profile, target_url,
                                     version=args.version or ver_mod.resolve_version(project_dir))
                if result:
                    _, script_path, manifest = result
                    LOG.info(f"\n✅ Stage 3+4 完成! 测试脚本: {script_path}")

        # Stage 4.5: 自动运行验证 + 生成报告（默认执行，--no-run 跳过）
        script_ok = False
        ui_script_ok = False
        if not args.no_run:
            LOG.info("\n" + "=" * 60)
            LOG.info("Stage 4.5: 自动运行验证 + 生成报告")
            LOG.info("=" * 60)

            # 4.5.1: 运行 API 脚本
            if script_path:
                script_ok = await _run_stage4_verify(
                    str(script_path), project_dir, profile, login_url,
                    username=(args.user or ""), password=(args.password or ""),
                    headless=args.headless
                )
                if not script_ok:
                    LOG.warning("⚠️ API 脚本运行失败，但仍继续后续阶段")

            # 4.5.2: 运行 UI 脚本
            version = args.version or ver_mod.resolve_version(project_dir)
            ui_script_path = project_dir / version / "ui" / f"{args.module}.py"
            if ui_script_path.exists():
                ui_script_ok = await _run_ui_script(str(ui_script_path), headless=args.headless)
                if not ui_script_ok:
                    LOG.warning("⚠️ UI 脚本运行失败，但仍继续后续阶段")
            else:
                LOG.info(f"  ⏭️  UI 脚本不存在: {ui_script_path.name}")

        # Stage 5（导出 artifacts）- 默认执行
        if manifest is None:
            # 尝试从文件加载 manifest
            version = args.version or ver_mod.resolve_version(project_dir)
            manifest_path = workspace_dir / "kb" / "module_discovered" / f"{args.module}_manifest.json"
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                LOG.info(f"已从文件加载 manifest: {manifest_path}")

        if manifest:
            run_stage5(
                manifest=manifest,
                project_dir=project_dir,
                module_name=args.module,
                version=args.version or ver_mod.resolve_version(project_dir)
            )


if __name__ == "__main__":
    import os
    asyncio.run(main())

