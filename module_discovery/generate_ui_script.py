"""
generate_ui_script.py — UI 自动化脚本生成器

从 Stage 1 生成的 playbook.json 生成独立的 Playwright UI 自动化脚本包。

生成的脚本包特点：
- 包含完整回放引擎 (lib/)，从 module_discovery/ 复制
- 纯 Playwright，不依赖项目内部库
- 支持命令行参数（--headless, --data, --operation）
- 分离数据文件（{module_name}_data.json）和 playbook（{module_name}_playbook.json）
"""

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
import time
from pathlib import Path
from playwright.async_api import async_playwright

# ==================== Bootstrap ====================

_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))

from lib.replay_engine import replay_from_playbook
from lib.button_driver import ButtonDriver
from lib.cookie_client import load_cookies, get_token, apply_to_playwright_context, inject_token_to_storage

# ==================== 配置 ====================

CONFIG = {{
    "base_url": "{base_url}",
    "login_url": "{login_url}",
    "target_url": "{target_url}",
    "headless": False,
    "slow_mo": 100,
    # 鉴权配置
    "token_key": "{token_key}",
    "token_storage": "{token_storage}",
    "cookie_token_key": "{cookie_token_key}",
}}

AVAILABLE_OPERATIONS = {repr(op_names)}

# ==================== Cookie 鉴权 ====================

async def require_cookie_auth(context, page):
    """Cookie-only 鉴权，失败则报错退出。"""
    cookies_path = Path(__file__).parent / "config" / "cookies.json"
    cookies, token = load_cookies(cookies_path)

    if not cookies:
        print("=" * 60)
        print("  ❌ 鉴权失败: 未找到 cookies.json")
        print("=" * 60)
        print(f"  期望文件: {{cookies_path}}")
        print("  解决方法: 运行 --stage 1 或 --stage 2 刷新 cookie")
        sys.exit(1)

    # 注入 cookie
    await apply_to_playwright_context(context, cookies)
    print(f"  ✅ 已注入 {{len(cookies)}} 个 cookies")

    # 导航到登录页以设置 origin
    await page.goto(CONFIG["login_url"], wait_until="domcontentloaded", timeout=30000)
    await page.wait_for_timeout(500)

    # 注入 token 到 storage
    if token:
        await inject_token_to_storage(page, token, CONFIG["token_storage"], CONFIG["token_key"])
        print(f"  ✅ 已注入 {{CONFIG['token_key']}} 到 {{CONFIG['token_storage']}}")
    else:
        print(f"  ⚠️ 未找到 token (cookie_token_key={{CONFIG['cookie_token_key']}})")

    # 导航到目标页验证
    target_url = CONFIG.get("target_url", CONFIG["login_url"])
    await page.goto(target_url, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(1000)

    if "login" in page.url:
        print("=" * 60)
        print("  ❌ Cookie 已过期或无效")
        print("=" * 60)
        print("  解决方法: 运行 --stage 1 或 --stage 2 刷新 cookie")
        sys.exit(1)

    print("  ✅ Cookie 鉴权成功")

# ==================== 操作执行 ====================

async def run_operation(page, operation_name, operations_data, marker=None):
    """使用回放引擎执行单个操作"""
    op = operations_data.get(operation_name)
    if not op:
        print(f"⚠️ 操作不存在: {{operation_name}}")
        return marker, {{"operation": operation_name, "status": "failed", "error": "操作不存在", "steps": []}}

    print(f"\\n▶ 执行: {{op.get('description', operation_name)}}")

    steps = op.get("steps", [])
    button_driver = ButtonDriver(page)
    op_start = time.time()

    try:
        result = await replay_from_playbook(page, steps, button_driver, marker)
        duration = time.time() - op_start

        new_marker = result.get("marker", marker)
        op_result = {{
            "operation": operation_name,
            "status": "passed",
            "steps": [{{"action": s.get("action"), "status": "passed"}} for s in steps],
            "duration": duration,
        }}
        print(f"  ✅ 操作成功 (耗时 {{duration:.2f}}s)")
        return new_marker, op_result

    except Exception as e:
        duration = time.time() - op_start
        op_result = {{
            "operation": operation_name,
            "status": "failed",
            "error": str(e),
            "steps": [],
            "duration": duration,
        }}
        print(f"  ❌ 操作失败: {{e}}")
        return marker, op_result

# ==================== 报告 ====================

_REPORT_CSS = (
    "*{{margin:0;padding:0;box-sizing:border-box}}"
    "body{{font-family:-apple-system,'Segoe UI',Roboto,'Microsoft YaHei',sans-serif;"
    "background:#f5f6fa;color:#2c3e50;padding:20px 28px;line-height:1.6}}"
    ".header{{background:linear-gradient(135deg,#2c3e50,#3498db);color:#fff;"
    "padding:22px 28px;border-radius:8px;margin-bottom:18px}}"
    ".header h1{{font-size:20px;margin-bottom:6px}}"
    ".header p{{opacity:.88;font-size:13px}}"
    ".summary{{display:flex;gap:12px;margin-bottom:18px;flex-wrap:wrap}}"
    ".card{{background:#fff;padding:12px 20px;border-radius:8px;"
    "box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center;min-width:100px}}"
    ".card .num{{font-size:24px;font-weight:700}}"
    ".card .label{{font-size:12px;color:#7f8c8d}}"
    ".card.ok .num{{color:#27ae60}}"
    ".card.fail .num{{color:#e74c3c}}"
    ".card.warn .num{{color:#f39c12}}"
    "details{{background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);"
    "margin-bottom:14px;overflow:hidden}}"
    "summary{{padding:12px 18px;font-size:14px;font-weight:600;cursor:pointer;"
    "background:#ecf0f1;list-style:none;display:flex;gap:8px;align-items:center}}"
    "summary::-webkit-details-marker{{display:none}}"
    "summary::before{{content:'▶';font-size:10px;transition:.2s}}"
    "details[open]>summary::before{{transform:rotate(90deg)}}"
    ".body{{padding:14px 18px}}"
    "table{{width:100%;border-collapse:collapse;font-size:12px}}"
    "th{{background:#f8f9fa;padding:6px 8px;text-align:left;"
    "border-bottom:2px solid #dee2e6;white-space:nowrap}}"
    "td{{padding:5px 8px;border-bottom:1px solid #eee;vertical-align:top;word-break:break-all}}"
    "tr.fail-row{{background:#fef0f0}}"
    ".badge{{display:inline-block;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600}}"
    ".badge-ok{{background:#d5f5e3;color:#27ae60}}"
    ".badge-fail{{background:#fadbd8;color:#e74c3c}}"
    ".badge-warn{{background:#fef9e7;color:#f39c12}}"
    ".badge-skip{{background:#f2f3f4;color:#95a5a6}}"
    ".err{{background:#fef0f0;border-left:4px solid #e74c3c;padding:10px 14px;"
    "border-radius:4px;color:#c0392b;white-space:pre-wrap;font-size:13px;margin-bottom:14px}}"
    ".oknote{{background:#eafaf1;border-left:4px solid #27ae60;padding:10px 14px;"
    "border-radius:4px;color:#1e7e44;font-size:13px;margin-bottom:14px;white-space:pre-wrap}}"
    ".oknote b{{color:#155d33}}"
)

def generate_html_report(results, module_name):
    """生成 HTML 测试报告（风格与 EcsCloud 统一）"""
    from datetime import datetime as _dt
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = total - passed
    pass_rate = f"{{(passed / total * 100) if total > 0 else 0:.1f}}%" if total else "0.0%"
    now_str = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    all_ok = failed == 0 and total > 0

    icon = "✅" if all_ok else "❌"

    # --- 操作明细行 ---
    rows_html = ""
    for i, r in enumerate(results, 1):
        st = r.get("status", "unknown")
        dur = r.get("duration", 0)
        op_name = r.get("operation", "?")
        err_msg = r.get("error", "")
        cls = "badge-ok" if st == "passed" else "badge-fail"
        label = "通过" if st == "passed" else "失败"
        row_cls = ' class="fail-row"' if st == "failed" else ""
        rows_html += (
            f"<tr{{row_cls}}>"
            f"<td>{{i}}</td>"
            f"<td>{{op_name}}</td>"
            f"<td>{{dur:.2f}}s</td>"
            f'<td><span class="badge {{cls}}">{{label}}</span></td>'
            f"<td>{{err_msg}}</td>"
            f"</tr>"
        )

    # --- 组装 HTML（用 + 拼接避免 f-string 嵌套 triple-quote 冲突） ---
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{{module_name}} · UI 测试报告</title>"
        f"<style>{{_REPORT_CSS}}</style></head><body>"
        f'<div class="header"><h1>{{icon}} {{module_name}} · UI 测试报告</h1>'
        f"<p>执行时间: {{now_str}} | 耗时: {{sum(r.get('duration',0) for r in results):.1f}}s</p></div>"
        '<div class="summary">'
        f'<div class="card"><div class="num">{{total}}</div><div class="stat-label">总操作</div></div>'
        f'<div class="card ok"><div class="num">{{passed}}</div><div class="stat-label">通过</div></div>'
        f'<div class="card fail"><div class="num">{{failed}}</div><div class="stat-label">失败</div></div>'
        f'<div class="card warn"><div class="num">{{pass_rate}}</div><div class="stat-label">通过率</div></div>'
        "</div>"
    )

    if failed > 0:
        err_ops = [r["operation"] for r in results if r["status"] == "failed"]
        html += f'<div class="err">失败操作: {{", ".join(err_ops)}}</div>'

    if all_ok:
        html += '<div class="oknote">✅ <b>全部操作执行成功</b>。以下为各操作的执行明细。</div>'

    html += (
        '<details open><summary>操作执行明细</summary><div class="body"><table>'
        "<thead><tr><th>#</th><th>操作</th><th>耗时</th><th>结果</th><th>错误信息</th></tr></thead>"
        f"<tbody>{{rows_html}}</tbody></table></div></details>"
        "</body></html>"
    )

    report_dir = Path(__file__).parent / "output" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{{module_name}}_ui_report_{{ts}}.html"
    report_path.write_text(html, encoding="utf-8")
    return str(report_path)

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
