"""
generate_ui_script.py — UI 自动化脚本生成器

从 Stage 1 生成的 playbook.json 生成独立的 Playwright UI 自动化脚本包。

生成的脚本包特点：
- 包含完整回放引擎 (lib/)，从 module_discovery/ 复制
- 纯 Playwright，不依赖项目内部库
- 支持命令行参数（--headless, --data, --operation）
- 分离数据文件（{module_name}_data.json）和 playbook（{module_name}_playbook.json）
"""

import base64
import json
import logging
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("generate_ui_script")


def generate_ui_script(playbook: dict, module_name: str, project_dir: Path, version: str = "v1.0.0") -> tuple[Path, Path]:
    """生成 UI 自动化脚本包和数据文件

    Args:
        playbook: Stage 1 生成的 playbook 数据
        module_name: 模块名称
        project_dir: 项目目录
        version: 版本号

    Returns:
        (script_path, data_path)
    """
    # 1. 确定输出目录
    ui_dir = project_dir / "scripts" / version / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)

    # 2. 同步运行时 lib/
    _sync_ui_runtime_lib(ui_dir)

    # 3. 生成数据文件
    data = _extract_test_data(playbook)
    data_path = ui_dir / f"{module_name}_data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info(f"数据文件已生成: {data_path}")

    # 4. 输出 playbook 为独立 JSON 文件
    playbook_path = ui_dir / f"{module_name}_playbook.json"
    playbook_path.write_text(json.dumps(playbook, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info(f"Playbook 已生成: {playbook_path}")

    # 5. 生成薄主脚本
    script_content = _render_script(playbook, module_name, data_path.name, playbook_path.name, version)
    script_path = ui_dir / f"{module_name}.py"
    script_path.write_text(script_content, encoding="utf-8")
    LOG.info(f"UI 脚本已生成: {script_path}")

    return script_path, data_path


def _sync_ui_runtime_lib(ui_dir: Path):
    """将 module_discovery/ 下的运行时文件复制到 ui/lib/。"""
    src_dir = Path(__file__).resolve().parent  # module_discovery/
    lib_dir = src_dir.parent / "lib"  # lib/
    dst_lib = ui_dir / "lib"
    dst_lib.mkdir(parents=True, exist_ok=True)

    # Python modules from module_discovery/
    module_files = [
        "__init__.py",
        "replay_engine.py",
        "button_driver.py",
        "form_filler.py",
        "wait_helpers.py",
        "kb_loader.py",
        "const.py",
        "ui_report.py",
    ]
    for name in module_files:
        src = src_dir / name
        dst = dst_lib / name
        if name == "__init__.py" and not src.exists():
            dst.write_text('"""UI automation runtime library."""\n', encoding="utf-8")
            continue
        if not src.exists():
            LOG.warning(f"  Runtime file missing: {src}")
            continue
        src_content = src.read_text(encoding="utf-8")
        if dst.exists() and dst.read_text(encoding="utf-8") == src_content:
            continue
        dst.write_text(src_content, encoding="utf-8")
        LOG.info(f"  UI runtime sync: {name}")

    # cookie_client.py from lib/
    cookie_src = lib_dir / "cookie_client.py"
    cookie_dst = dst_lib / "cookie_client.py"
    if cookie_src.exists():
        src_content = cookie_src.read_text(encoding="utf-8")
        if not cookie_dst.exists() or cookie_dst.read_text(encoding="utf-8") != src_content:
            cookie_dst.write_text(src_content, encoding="utf-8")
            LOG.info("  UI runtime sync: cookie_client.py")
    else:
        LOG.warning(f"  Runtime file missing: {cookie_src}")

    # KB data
    kb_src = src_dir / "kb" / "probe_knowledge.json"
    kb_dst = dst_lib / "kb" / "probe_knowledge.json"
    kb_dst.parent.mkdir(parents=True, exist_ok=True)
    if kb_src.exists():
        src_content = kb_src.read_text(encoding="utf-8")
        if not kb_dst.exists() or kb_dst.read_text(encoding="utf-8") != src_content:
            kb_dst.write_text(src_content, encoding="utf-8")
            LOG.info("  UI runtime sync: kb/probe_knowledge.json")

    # cookies.json -> config/
    config_dst = ui_dir / "config"
    config_dst.mkdir(parents=True, exist_ok=True)
    cookies_src = src_dir.parent / "cookies.json"  # project root
    if cookies_src.exists():
        dst_cookies = config_dst / "cookies.json"
        if not dst_cookies.exists():
            dst_cookies.write_text(cookies_src.read_text(encoding="utf-8"), encoding="utf-8")
            LOG.info("  UI runtime sync: config/cookies.json")


def _extract_test_data(playbook: dict) -> dict:
    """从 playbook 中提取测试数据"""
    data = {}

    create_op = playbook.get("operations", {}).get("create", {})
    if create_op:
        create_data = {}
        for step in create_op.get("steps", []):
            if step.get("action") == "fill_form":
                for field in step.get("fields", []):
                    label = field.get("label")
                    if not label:
                        continue
                    if "密码" in label or "password" in label.lower():
                        create_data[label] = "Test@123456"
                    elif "手机" in label or "phone" in label.lower():
                        create_data[label] = "13800138000"
                    elif "邮箱" in label or "email" in label.lower():
                        create_data[label] = "test@example.com"
                    elif "描述" in label or "备注" in label or "description" in label.lower():
                        create_data[label] = "UI 自动化测试数据"
                    elif "名称" in label or "name" in label.lower():
                        create_data[label] = None  # 动态
                    elif "编码" in label or "code" in label.lower():
                        create_data[label] = None  # 动态
                    else:
                        create_data[label] = f"test_{label}"
        data["create"] = create_data

    update_op = playbook.get("operations", {}).get("update", {})
    if update_op:
        data["update"] = {"描述": "auto_edited", "备注": "UI 自动化更新测试"}

    return data


def _render_script(playbook: dict, module_name: str, data_filename: str, playbook_filename: str, version: str) -> str:
    """渲染薄主脚本"""
    meta = playbook.get("meta", {})
    target_url = meta.get("target_url", "")
    base_url = meta.get("base_url", "")
    if not base_url and target_url:
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

    login_url = meta.get("login_url", "")
    if not login_url and base_url:
        login_url = f"{base_url}/estack/web/estack/login"

    # 提取鉴权配置
    auth_config = meta.get("auth_config", {})
    token_key = auth_config.get("token_key", "estackToken")
    token_storage = auth_config.get("token_storage", "localStorage")
    cookie_token_key = auth_config.get("cookie_token_key", "accessToken")

    # 提取操作名列表
    operations = playbook.get("operations", {})
    op_names = list(operations.keys())

    # 提取 Stage 1 标记的操作状态（用于跳过失败操作）
    op_status = {}
    for name, op in operations.items():
        if op.get("status") == "failed":
            op_status[name] = {
                "status": "failed",
                "error_type": op.get("error_type", "unknown"),
                "error_text": op.get("error_text", ""),
            }
    operation_status_json = json.dumps(op_status, ensure_ascii=False, indent=4)

    return f'''#!/usr/bin/env python3
"""
{module_name} - UI 自动化测试脚本

生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
生成工具: API AI Test Framework - Stage 2
版本: {version}

用法:
    python {module_name}.py                    # 运行所有操作
    python {module_name}.py create update      # 只运行 create 和 update
    python {module_name}.py --headless         # 无头模式
    python {module_name}.py --data custom.json # 使用自定义数据文件

依赖:
    pip install playwright
    playwright install chromium
"""

import json
import os
import sys
import io
import asyncio
import argparse
import base64
import time
from pathlib import Path
from playwright.async_api import async_playwright

# ==================== Bootstrap ====================

_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))

from lib.replay_engine import replay_from_playbook
from lib.button_driver import ButtonDriver

# ==================== 配置 ====================

CONFIG = {{
    "base_url": "{base_url}",
    "login_url": "{login_url}",
    "target_url": "{target_url}",
    "headless": False,
    "slow_mo": 100,
    # 鉴权配置（cookie_client 统一使用）
    "auth_config": {{
        "token_key": "{token_key}",
        "token_storage": "{token_storage}",
        "cookie_token_key": "{cookie_token_key}",
    }},
}}

AVAILABLE_OPERATIONS = {repr(op_names)}

# ==================== Cookie 鉴权 ====================

async def require_cookie_auth(context, page):
    """使用 cookie_client 统一鉴权，失败则报错退出。"""
    auth_cfg = CONFIG.get("auth_config", {{}})
    full_auth_cfg = {{**auth_cfg, "login_url": CONFIG["login_url"], "target_url": CONFIG["target_url"]}}

    from lib.cookie_client import require_auth_for_ui, apply_auth_to_playwright

    cookies, token = await require_auth_for_ui(
        config_dir=Path(__file__).parent / "config",
        auth_config=full_auth_cfg,
    )

    await apply_auth_to_playwright(context, page, cookies, token, full_auth_cfg)

# ==================== 操作执行 ====================

async def _cleanup_dialogs(page):
    """关闭操作间可能残留的对话框/弹窗，防止阻塞后续操作。"""
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
    except Exception:
        pass
    # Element UI / Ant Design / 通用关闭按钮
    close_selectors = [
        ".el-dialog__close",
        ".el-message-box__btns button:not(.el-button--primary)",
        ".el-drawer__close-btn",
        ".ant-modal-close",
        ".el-dialog__headerbtn",
    ]
    for sel in close_selectors:
        try:
            els = await page.query_selector_all(sel)
            for el in els:
                if await el.is_visible():
                    await el.click()
                    await page.wait_for_timeout(200)
        except Exception:
            pass
    # 最终兜底 Escape
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
    except Exception:
        pass

# 操作失败原因（由 Stage 1 标记）
_OPERATION_STATUS = {operation_status_json}

async def run_operation(page, operation_name, operations_data, marker=None):
    """使用回放引擎执行单个操作"""
    op = operations_data.get(operation_name)
    if not op:
        print(f"⚠️ 操作不存在: {{operation_name}}")
        return marker, {{"operation": operation_name, "status": "failed", "error": "操作不存在", "steps": []}}

    # 跳过 Stage 1 标记为失败的操作
    st = _OPERATION_STATUS.get(operation_name, {{}})
    if st.get("status") == "failed":
        reason = st.get("error_text", "Stage 1 标记为失败")
        err_type = st.get("error_type", "unknown")
        display_name = op.get("display_name", op.get("description", operation_name))
        print(f"\\n⏭ 跳过: {{display_name}} ({{err_type}}: {{reason}})")
        return marker, {{
            "operation": operation_name,
            "display_name": display_name,
            "status": "skipped",
            "error": f"[{{err_type}}] {{reason}}",
            "steps": [],
            "duration": 0,
        }}

    print(f"\\n▶ 执行: {{op.get('display_name', op.get('description', operation_name))}}")

    steps = op.get("steps", [])
    button_driver = ButtonDriver(page)
    op_start = time.time()

    try:
        result = await replay_from_playbook(page, steps, button_driver, marker)
        duration = time.time() - op_start

        new_marker = result.get("marker", marker)
        # 截图（成功）
        screenshot = None
        try:
            raw = await page.screenshot(type="png")
            screenshot = base64.b64encode(raw).decode("ascii")
        except Exception:
            pass

        # 构建详细步骤信息（含 locator 和测试数据）
        detailed_steps = []
        for s in steps:
            step_info = {{
                "action": s.get("action", ""),
                "status": "passed",
                "locator": s.get("playwright_locator", ""),
                "description": s.get("description", ""),
            }}
            # 填充表单的字段数据
            if s.get("action") == "fill_form" and "fields" in s:
                fields_summary = []
                for field in s["fields"]:
                    field_info = {{
                        "label": field.get("label", ""),
                        "locator": field.get("playwright_locator", ""),
                        "type": field.get("type", ""),
                    }}
                    # 测试数据
                    fill_rule = field.get("fill_rule", {{}})
                    if fill_rule:
                        params = fill_rule.get("params", {{}})
                        if "value" in params:
                            field_info["test_data"] = params["value"]
                        elif "prefix" in params:
                            field_info["test_data"] = f"{{params['prefix']}}*"
                        else:
                            field_info["test_data"] = str(fill_rule.get("rule", ""))
                    elif field.get("option_text"):
                        field_info["test_data"] = field["option_text"]
                    fields_summary.append(field_info)
                step_info["fields"] = fields_summary

            detailed_steps.append(step_info)

        op_result = {{
            "operation": operation_name,
            "display_name": op.get("display_name", operation_name),
            "status": "passed",
            "steps": detailed_steps,
            "duration": duration,
            "screenshot": screenshot,
        }}
        print(f"  ✅ 操作成功 (耗时 {{duration:.2f}}s)")

        # 操作成功后清理残留弹窗
        await _cleanup_dialogs(page)
        return new_marker, op_result

    except Exception as e:
        duration = time.time() - op_start
        # 截图（失败）
        screenshot = None
        try:
            raw = await page.screenshot(type="png")
            screenshot = base64.b64encode(raw).decode("ascii")
        except Exception:
            pass
        op_result = {{
            "operation": operation_name,
            "display_name": op.get("display_name", operation_name),
            "status": "failed",
            "error": str(e),
            "steps": [],
            "duration": duration,
            "screenshot": screenshot,
        }}
        print(f"  ❌ 操作失败: {{e}}")

        # 失败后也尝试清理弹窗
        await _cleanup_dialogs(page)
        return marker, op_result

# ==================== 报告 ====================

from lib.ui_report import generate_ui_report as generate_html_report

# ==================== 主程序 ====================

async def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="{module_name} UI 自动化测试脚本")
    parser.add_argument("operations", nargs="*", help="要执行的操作列表")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument("--data", help="数据文件路径")
    args = parser.parse_args()

    # 加载 playbook
    playbook_path = Path(__file__).parent / "{playbook_filename}"
    if not playbook_path.exists():
        print(f"❌ Playbook 文件不存在: {{playbook_path}}")
        return
    with open(playbook_path, "r", encoding="utf-8") as f:
        playbook = json.load(f)

    operations_data = playbook.get("operations", {{}})

    # 确定要执行的操作
    ops = args.operations if args.operations else AVAILABLE_OPERATIONS

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=args.headless or CONFIG["headless"],
            slow_mo=CONFIG["slow_mo"]
        )
        context = await browser.new_context(
            viewport={{"width": 1920, "height": 1080}},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # Cookie 鉴权（不做登录）
        print("Cookie 鉴权...")
        await require_cookie_auth(context, page)

        print(f"导航到: {{CONFIG['target_url']}}")
        await page.goto(CONFIG["target_url"], wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 等待表格渲染完成（与 Stage 1 发现环境一致）
        from lib.wait_helpers import wait_for_table_ready
        try:
            await wait_for_table_ready(page, timeout=15000)
            print("  ✅ 表格已就绪")
        except Exception as e:
            print(f"  \\u26a0\\ufe0f \\u7b49\\u5f85\\u8868\\u683c\\u8d85\\u65f6: {{e}}")

        marker = None
        results = []
        for op_name in ops:
            marker, op_result = await run_operation(page, op_name, operations_data, marker)
            results.append(op_result)

        report_path = generate_html_report(results, "{module_name}")
        print(f"\\n{{'='*60}}")
        print(f"  ✅ 测试完成")
        print(f"{{'='*60}}")
        print(f"  📊 报告: {{report_path}}")

        if not (args.headless or CONFIG["headless"]):
            input("\\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    from datetime import datetime
    asyncio.run(main())
'''
