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
from pathlib import Path

# 确保能找到 lib
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module_discovery.discover_ui import (
    discover_and_validate, discover_all, cleanup_ui_overlays, wait_for_spa_ready,
)
from module_discovery.capture_apis import capture_all
from module_discovery.analyze_flow import analyze, build_manifest
from module_discovery.gen_test import generate_script, save_script_to_file
from module_discovery import version as ver_mod
from module_discovery.stage_validators import validate_stage1, validate_stage2, validate_stage3, validate_stage4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOG = logging.getLogger("run")


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
                    choices=["1", "2", "34", "4", "all"],
                    help="执行阶段: 1=探测, 2=捕获, 34=分析, 4=生成, all=全部")
    ap.add_argument("--version", type=str, default=None,
                    help="脚本版本号（如 v2.1.0）。默认读 .api_version / API_VERSION 环境变量 / v1.0.0")
    # ---- 批量发现 ----
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
    return ap.parse_args()


def _load_profile(project_dir: Path) -> dict:
    """加载项目的 profile.yaml。"""
    import yaml
    p = project_dir / "profile.yaml"
    if p.exists():
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {}


def _load_ui_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 UI 探测结果。"""
    p = project_dir / "kb" / "module_discovered" / f"{module_name}_ui.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _load_capture_result(project_dir: Path, module_name: str) -> dict:
    """加载已保存的 API 捕获结果。"""
    # 兼容新旧文件名：{module}.json（旧）和 {module}_capture.json（Stage 2 重构后）
    for suffix in ("_capture.json", ".json"):
        p = project_dir / "kb" / "module_discovered" / f"{module_name}{suffix}"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save_json(data: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_modules_yaml(project_dir: Path) -> list:
    """加载 modules.yaml 模块清单。返回模块列表，按 priority 排序。"""
    modules_file = project_dir / "modules.yaml"
    if not modules_file.exists():
        LOG.error(f"模块清单不存在: {modules_file}")
        LOG.info("请创建 modules.yaml，格式参考 projects/estack/modules.yaml")
        return []

    try:
        import yaml
        data = yaml.safe_load(modules_file.read_text(encoding="utf-8")) or {}
        modules = data.get("modules", [])

        # 过滤 enabled=false 的模块
        modules = [m for m in modules if m.get("enabled", True)]

        # 按 priority 排序（默认 100）
        modules.sort(key=lambda m: m.get("priority", 100))

        LOG.info(f"已加载 {len(modules)} 个模块（来自 {modules_file}）")
        return modules
    except Exception as e:
        LOG.error(f"加载 modules.yaml 失败: {e}")
        return []


def _needs_rediscovery(project_dir: Path, module_name: str, module_url: str, force: bool = False) -> bool:
    """检查模块是否需要重新发现（增量逻辑）。

    Returns:
        True = 需要重新发现
        False = 可以跳过（已有结果且 URL 未变更）
    """
    if force:
        return True

    discovery_file = project_dir / "kb" / "module_discovered" / f"{module_name}.json"
    if not discovery_file.exists():
        return True

    try:
        data = json.loads(discovery_file.read_text(encoding="utf-8"))
        old_url = data.get("target_url", "")

        # URL 变更则重新发现
        if old_url != module_url:
            LOG.info(f"  URL 已变更: {old_url} → {module_url}")
            return True

        # 检查文件 mtime（超过 7 天则重新发现）
        import os
        mtime = os.path.getmtime(discovery_file)
        age_days = (time.time() - mtime) / 86400
        if age_days > 7:
            LOG.info(f"  发现结果已过期（{age_days:.1f} 天前）")
            return True

        return False
    except Exception as e:
        LOG.warning(f"  检查发现结果失败: {e}")
        return True


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
    out_path = project_dir / "kb" / "module_discovered" / f"{module_name}_ui.json"
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
    login_url = profile.get("login_url", f"{base_url}/estack/web/estack/login")
    ui_result["login_url"] = login_url

    # 注入 auth_config（供 UI 脚本生成使用）
    profile_auth = profile.get("auth", {})
    ui_result["auth_config"] = {
        "token_key": profile_auth.get("token_key", "estackToken"),
        "token_storage": profile_auth.get("token_storage", "localStorage"),
        "cookie_token_key": profile_auth.get("cookie_token_key", "accessToken"),
    }

    # 生成并保存 playbook
    from .discover_ui import build_playbook
    playbook = build_playbook(ui_result)
    playbook_path = project_dir / "kb" / "module_discovered" / f"{module_name}_playbook.json"
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


async def _hints_rescan(page, ui_result: dict, missing: list) -> dict:
    """Phase C: hints 定向重探。

    用 missing_elements 作为 hints 调用 _scan_hints，
    将找到的元素合并回 ui_result。
    """
    from .discover_ui import _scan_hints
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase C: hints 定向重探 ({len(missing)} 个缺失元素)")

    found = await _scan_hints(page, missing)
    if found:
        LOG.info(f"  Phase C: 找到 {len(found)} 个元素")
        ui_result = patch_ui_result(ui_result, {"elements": found})
    else:
        LOG.info("  Phase C: 未找到额外元素")

    return ui_result


async def _precondition_retry(page, ui_result: dict, missing: list) -> dict:
    """Phase D: 检查前置操作，换方式重试点击，重新扫描弹窗内元素。"""
    from .discover_ui import (
        _check_precondition_state, _retry_precondition,
        _scan_dialog_buttons,
    )
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase D: 前置操作重试 ({len(missing)} 个缺失元素)")

    added_any = False
    for elem in missing:
        if elem.get("type") != "button":
            continue
        action = elem.get("action", "")
        # 只对需要前置操作（弹窗内按钮）的元素重试
        expected_location = elem.get("location", [])
        if "dialog" not in expected_location:
            continue

        # 找到对应的触发按钮（toolbar 中同 action 的按钮）
        trigger_btn = None
        for btn in ui_result.get("toolbar_buttons", []):
            if btn.get("action") == action:
                trigger_btn = btn
                break
        if not trigger_btn:
            for btn in ui_result.get("row_actions", []):
                if btn.get("action") == action:
                    trigger_btn = btn
                    break
        if not trigger_btn:
            continue

        # 检查前置操作状态
        expected_title = ""
        for ff in ui_result.get("form_fields", []):
            if ff.get("name"):
                expected_title = ff.get("dialog_title", "")
                break

        state = await _check_precondition_state(page, {
            "type": "dialog", "title": expected_title
        })

        if not state["success"]:
            # 前置操作未成功，尝试重试点击
            retry_ok = await _retry_precondition(page, trigger_btn)
            if retry_ok:
                # 重试成功，扫描弹窗内按钮
                dialog_btns = await _scan_dialog_buttons(page)
                if dialog_btns:
                    ui_result = patch_ui_result(ui_result, {"elements": dialog_btns})
                    added_any = True
                    LOG.info(f"  Phase D: 重试成功，新增 {len(dialog_btns)} 个弹窗按钮")
        else:
            # 前置操作已成功（弹窗已打开），直接扫描弹窗
            dialog_btns = await _scan_dialog_buttons(page)
            if dialog_btns:
                ui_result = patch_ui_result(ui_result, {"elements": dialog_btns})
                added_any = True
                LOG.info(f"  Phase D: 弹窗已打开，新增 {len(dialog_btns)} 个弹窗按钮")

    if not added_any:
        LOG.info("  Phase D: 未找到额外元素")

    return ui_result


async def _vision_rescue(page, ui_result: dict, missing: list) -> dict:
    """Phase E: Vision 截图分析兜底。"""
    from .ai_debug_assistant import ai_assisted_analysis
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase E: Vision 截图分析 ({len(missing)} 个缺失元素)")

    try:
        result = await ai_assisted_analysis(page, missing, expected_context=None)

        if result.get("found"):
            ui_result = patch_ui_result(ui_result, result)
            LOG.info(f"  Phase E: Vision 找到 {len(result.get('elements', []))} 个元素")
        else:
            diagnosis = result.get("diagnosis", "unknown")
            source = result.get("source", "unknown")
            LOG.info(f"  Phase E: Vision 未找到元素 (source={source}, diagnosis={diagnosis})")

    except Exception as e:
        LOG.warning(f"  Phase E: Vision 分析异常: {e}")

    return ui_result


async def run_stage2(page, project_dir: Path, module_name: str,
                     ui_result: dict, base_url: str, target_url: str,
                     max_recapture: int = 2,
                     capture_all_mode: bool = False,
                     version: str = "") -> tuple:
    """Stage 2: API 捕获。

    Stage 1 已验证所有操作可成功，Stage 2 只做 API 捕获。
    保留重试机制以应对瞬时网络问题。

    Returns:
        (full_result, is_valid) 元组
    """
    LOG.info("=" * 50)
    LOG.info("Stage 2: API 捕获")
    LOG.info("=" * 50)

    max_recapture = min(max_recapture, 5)

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
            capture_all_mode=capture_all_mode
        )

        is_valid, issues = validate_stage2(api_capture)

        # 构建结果（无论验证是否通过）
        full_result = {
            "capture_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target_url": target_url,
            "stats": api_capture.get("stats", {}),
            "by_category": api_capture.get("classified", {}),
            "all_endpoints": api_capture.get("all_endpoints", []),
            "response_samples": api_capture.get("response_samples", {}),
        }
        out_path = project_dir / "kb" / "module_discovered" / f"{module_name}.json"
        _save_json(full_result, out_path)

        if is_valid:
            LOG.info("Stage 2 验证通过")
            LOG.info(f"结果已保存: {out_path}")
            stats = full_result["stats"]
            LOG.info(f"  总拦截: {stats.get('total_calls', 0)} 条")
            LOG.info(f"  唯一端点: {stats.get('unique_endpoints', 0)} 个")
            for cat, eps in sorted(api_capture.get("classified", {}).items()):
                LOG.info(f"    {cat}: {len(eps)} 个")

            # 生成 UI 自动化脚本
            from .generate_ui_script import generate_ui_script
            playbook_path = project_dir / "kb" / "module_discovered" / f"{module_name}_playbook.json"
            if playbook_path.exists():
                try:
                    with open(playbook_path, 'r', encoding='utf-8') as f:
                        playbook_data = json.load(f)
                    ver = version or ver_mod.resolve_version(project_dir)
                    script_path, data_path = generate_ui_script(playbook_data, module_name, project_dir, version=ver)
                    LOG.info(f"  UI 脚本已生成: {script_path}")
                    LOG.info(f"  UI 数据已生成: {data_path}")
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

    capture_result = _load_capture_result(project_dir, module_name)
    ui_result = _load_ui_result(project_dir, module_name)

    if not capture_result:
        LOG.error(f"未找到捕获结果: kb/module_discovered/{module_name}.json")
        LOG.error("请先执行 Stage 2")
        return None

    classified = capture_result.get("by_category", {})
    all_endpoints = capture_result.get("all_endpoints", [])
    response_samples = capture_result.get("response_samples", {})

    # Stage 3: 分析
    flow = analyze(classified, all_endpoints, response_samples, ui_result)

    # 阶段门控验证
    is_valid, issues = validate_stage3(flow)
    if not is_valid:
        LOG.error(f"Stage 3 质量门控失败，发现 {len(issues)} 个问题:")
        for issue in issues:
            LOG.error(f"  - {issue}")
        LOG.error("建议：检查分类结果是否准确、是否存在非标准 API 模式")
        LOG.warning("继续执行后续阶段，但结果可能不完整")

    # 保存分析结果
    analysis_path = project_dir / "kb" / "module_discovered" / f"{module_name}_analysis.json"
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
    manifest_path = project_dir / "kb" / "module_discovered" / f"{module_name}_manifest.json"
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
        script, str(project_dir), module_name, version=version,
    )

    return flow, script_path


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

    profile = _load_profile(project_dir)
    base_url = profile.get("base_url")
    if not base_url:
        LOG.error("profile.yaml 缺少 base_url 配置")
        sys.exit(1)
    login_url = profile.get("login_url")
    if not login_url:
        LOG.error("profile.yaml 缺少 login_url 配置")
        sys.exit(1)

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

            # ---- 鉴权（批量模式统一登录一次）----
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
                await browser.close()
                return

            # 尝试从 output/config 加载已有 cookie
            cookie_file = project_dir / "output" / "config" / "cookies.json"
            logged_in = False
            if cookie_file.exists():
                try:
                    cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                    if cookies:
                        await context.add_cookies(cookies)
                        try:
                            await page.goto(base_url, wait_until="load", timeout=30000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(5000)
                        if "/login" not in page.url:
                            LOG.info("  ✅ Cookie 有效，直接进入系统")
                            logged_in = True
                except Exception:
                    pass

            if not logged_in:
                LOG.info("  执行滑块登录...")
                ok = await _login_with_playwright(page, context, login_url, username, password,
                                                  project_profile=profile)
                if not ok:
                    LOG.error("❌ 登录失败")
                    await browser.close()
                    return
                LOG.info("  ✅ 登录成功")
                # 保存 cookie
                cookies = await context.cookies()
                cookie_file.parent.mkdir(parents=True, exist_ok=True)
                cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

            # 执行批量发现
            await run_all_modules(page, context, project_dir, profile, base_url, login_url, args)

            if not args.headless:
                LOG.info("\n⚠️ 浏览器保持打开，手动关闭后按 Ctrl+C 退出。")
        return

    # 单模块模式（原有逻辑）
    if not args.url or not args.module:
        LOG.error("单模块模式需要 --url 和 --module 参数")
        sys.exit(1)

    target_url = base_url.rstrip("/") + args.url if not args.url.startswith("http") else args.url

    # 离线模式: 只做 Stage 3+4
    if args.offline:
        run_stage34(project_dir, args.module, profile, target_url,
                    version=args.version or ver_mod.resolve_version(project_dir))
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

        # ---- 鉴权 ----
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
            await browser.close()
            return

        # 尝试从 output/config 加载已有 cookie
        cookie_file = project_dir / "output" / "config" / "cookies.json"
        logged_in = False
        if cookie_file.exists():
            try:
                cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                if cookies:
                    await context.add_cookies(cookies)
                    # 注入 token 到 localStorage（cookie 有效时也需要）
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
            except Exception:
                pass

        if not logged_in:
            LOG.info("  执行滑块登录...")
            ok = await _login_with_playwright(page, context, login_url, username, password,
                                              project_profile=profile)
            if not ok:
                LOG.error("❌ 登录失败")
                await browser.close()
                return
            LOG.info("  ✅ 登录成功")
            # 保存 cookie
            cookies = await context.cookies()
            cookie_file.parent.mkdir(parents=True, exist_ok=True)
            cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

            # 从 cookie 提取 token，用 addInitScript 注入到 localStorage（所有后续页面）
            token_key = profile.get("auth", {}).get("token_key", "accessToken")
            token_cookie = next((c["value"] for c in cookies if c["name"] == token_key), None)
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

        # ---- 各阶段执行 ----
        stage = args.stage

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
                version=args.version or ver_mod.resolve_version(project_dir))
        else:
            capture_result = _load_capture_result(project_dir, args.module)
            stage2_valid = bool(capture_result and capture_result.get("by_category"))

        LOG.info("\n⚠️ 浏览器保持打开供检查确认。")

        # Stage 3+4（可以离线执行）
        if stage in ("34", "4", "all"):
            if not stage2_valid and not args.force_gen:
                LOG.error("❌ Stage 2 验证失败，拒绝生成脚本（使用 --force-gen 强制覆盖）")
                LOG.error("   请先修复捕获问题或检查调试工件: "
                          f"{project_dir / 'output' / 'debug' / args.module}")
            else:
                if not stage2_valid and args.force_gen:
                    LOG.warning("⚠️ --force-gen: 强制生成脚本（Stage 2 验证未通过）")
                result = run_stage34(project_dir, args.module, profile, target_url,
                                     version=args.version or ver_mod.resolve_version(project_dir))
                if result:
                    _, script_path = result
                    LOG.info(f"\n✅ 全部完成! 测试脚本: {script_path}")

        if not args.headless:
            LOG.info("\n⚠️ 浏览器保持打开，手动关闭后按 Ctrl+C 退出。")


async def run_all_modules(page, context, project_dir: Path, profile: dict,
                          base_url: str, login_url: str, args):
    """批量发现: 读取 modules.yaml，逐一执行指定 stage。"""
    modules = _load_modules_yaml(project_dir)
    if not modules:
        LOG.error("没有可用的模块")
        return

    # 标签过滤
    if args.tag:
        filter_tags = set(t.strip() for t in args.tag.split(","))
        modules = [m for m in modules if filter_tags & set(m.get("tags", []))]
        LOG.info(f"标签过滤后: {len(modules)} 个模块")

    # 增量检查
    to_discover = []
    for m in modules:
        name = m["name"]
        url_raw = m["url"]
        target_url = base_url.rstrip("/") + url_raw if not url_raw.startswith("http") else url_raw
        if _needs_rediscovery(project_dir, name, target_url, force=args.force):
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
    stage = args.stage

    for idx, (name, target_url, url_raw) in enumerate(to_discover, 1):
        LOG.info(f"\n{'='*60}")
        LOG.info(f"  [{idx}/{len(to_discover)}] {name}")
        LOG.info(f"  URL: {url_raw}")
        LOG.info(f"{'='*60}")

        try:
            # Stage 1
            if stage in ("1", "all"):
                ui_result = await run_stage1(page, project_dir, name, target_url)
            else:
                ui_result = _load_ui_result(project_dir, name)

            # Stage 2
            stage2_valid = True
            if stage in ("2", "all"):
                capture_result, stage2_valid = await run_stage2(
                    page, project_dir, name, ui_result or {}, base_url, target_url,
                    max_recapture=args.max_recapture,
                    capture_all_mode=args.capture_all,
                    version=args.version or ver_mod.resolve_version(project_dir))
            else:
                capture_result = _load_capture_result(project_dir, name)
                stage2_valid = bool(capture_result and capture_result.get("by_category"))

            # Stage 3+4
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
                    _, script_path = result
                    LOG.info(f"  ✅ {name}: 脚本已生成 → {script_path}")

            results.append({"name": name, "status": "ok"})
        except Exception as e:
            LOG.error(f"  ❌ {name}: 发现失败 - {e}")
            results.append({"name": name, "status": "failed", "error": str(e)})

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


# ==================== 登录函数 ====================

async def _login_with_playwright(page, context, login_url, username, password,
                                 project_profile: dict = None):
    """
    登录：cookie 优先 → 失效后走滑块自动登录（复用 lib/auth.py）。
    从 project_profile 读取 auth/captcha 配置，不硬编码。
    """
    from lib import auth

    p = project_profile or {}
    profile = {
        "login_url": login_url,
        "auth": p.get("auth", {}),
        "captcha": p.get("captcha", {}),
    }

    sess = auth.AuthSession(profile, username, password)
    LOG.info("  执行滑块登录（复用 login_with_browser）...")
    ok = await sess.login_with_browser(page, context)
    if not ok:
        return False

    # 保存上下文
    import os as _os
    cookie_dir = Path(_os.environ.get("COOKIE_DIR", ""))
    if cookie_dir.exists():
        sess.context_path = str(cookie_dir / "context.json")
        sess.save_context(cookies=await context.cookies())

    return True


if __name__ == "__main__":
    import os
    asyncio.run(main())
