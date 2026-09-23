#!/usr/bin/env python3
"""
run_ui_suite.py — UI 自动化测试套件运行器

扫描所有模块的 UI 测试脚本，依次执行并生成一份汇总 HTML 报告。
汇总报告输出到 output/ui_report/suite_report_{timestamp}.html

用法:
  python run_ui_suite.py --project ecm-compute
  python run_ui_suite.py --project ecm-compute --modules 用户管理 角色管理
  python run_ui_suite.py --project ecm-compute --version v1.0.0
"""

import sys
import io
import glob
import argparse
import subprocess
import json
import re
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def find_all_ui_scripts(project_name: str = None, version: str = None) -> list:
    """查找所有模块的 UI 测试脚本。

    Args:
        project_name: 项目名称（可选），不指定则扫描所有项目
        version: 版本号（可选），不指定则使用最新版本

    Returns:
        [(script_path, module_name, project_name), ...] 列表
    """
    scripts = []

    if project_name:
        # 查找项目目录
        project_dir = PROJECT_ROOT / "projects" / project_name
        if not project_dir.exists():
            print(f"❌ 项目目录不存在: {project_dir}")
            return []

        # 确定版本
        if version:
            version_dir = project_dir / version
        else:
            # 查找最新的 v* 目录
            version_dirs = sorted([d for d in project_dir.glob("v*") if d.is_dir()])
            if not version_dirs:
                print(f"⚠️ 未找到版本目录: {project_dir}")
                return []
            version_dir = version_dirs[-1]

        # 扫描 ui/ 目录
        ui_dir = version_dir / "ui"
        if not ui_dir.exists():
            print(f"⚠️ UI 目录不存在: {ui_dir}")
            return []

        for script_path in sorted(ui_dir.glob("*.py")):
            # 排除 lib/ 目录下的文件
            if script_path.parent.name == "lib":
                continue
            # 排除 __pycache__ 等
            if script_path.name.startswith("_"):
                continue
            # 排除 config/ 等目录（虽然它们不会有 .py 文件）
            if script_path.parent.name not in ["ui", version_dir.name]:
                continue

            module_name = script_path.stem
            scripts.append((str(script_path), module_name, project_name))
    else:
        # 扫描所有项目
        for project_dir in sorted((PROJECT_ROOT / "projects").glob("*")):
            if not project_dir.is_dir():
                continue
            scripts.extend(find_all_ui_scripts(project_dir.name, version))

    return scripts


def run_ui_script(script_path: str) -> dict:
    """运行单个 UI 测试脚本。

    Args:
        script_path: 脚本路径

    Returns:
        执行结果 dict
    """
    print(f"  ▶ 执行: {Path(script_path).name}")

    try:
        result = subprocess.run(
            [sys.executable, script_path, "--headless"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300  # 5分钟超时
        )

        # 解析输出，提取测试结果
        output = result.stdout
        lines = output.split("\n")

        # 查找测试结果行
        # 格式: "✅ 测试完成" 或 "❌ 测试失败"
        # 格式: "X/Y 通过"
        passed = 0
        failed = 0
        total = 0

        for line in lines:
            # 匹配 "X/Y 通过" 格式
            match = re.search(r"(\d+)/(\d+)\s+通过", line)
            if match:
                passed = int(match.group(1))
                total = int(match.group(2))
                failed = total - passed
                break

        # 如果没找到，尝试从输出判断
        if total == 0:
            # 检查是否有 "测试完成" 字样
            if "测试完成" in output:
                total = 1
                passed = 1 if result.returncode == 0 else 0
                failed = 0 if passed else 1
            else:
                total = 1
                passed = 0
                failed = 1

        success_rate = f"{(passed / total * 100):.1f}%" if total > 0 else "0%"

        return {
            "status": "success",
            "returncode": result.returncode,
            "passed": passed,
            "failed": failed,
            "total": total,
            "success_rate": success_rate,
            "output": output,
            "stderr": result.stderr,
        }

    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "returncode": -1,
            "passed": 0,
            "failed": 1,
            "total": 1,
            "success_rate": "0%",
            "output": "执行超时（300秒）",
            "stderr": "",
        }
    except Exception as e:
        return {
            "status": "error",
            "returncode": -1,
            "passed": 0,
            "failed": 1,
            "total": 1,
            "success_rate": "0%",
            "output": f"执行异常: {e}",
            "stderr": "",
        }


def generate_suite_report(all_results: list, project_name: str = None) -> str:
    """生成汇总 HTML 报告。

    Args:
        all_results: 各模块的测试结果列表
        project_name: 项目名称（用于报告标题）

    Returns:
        报告文件路径
    """
    # 确定输出目录
    if project_name:
        project_dir = PROJECT_ROOT / "projects" / project_name
        report_root = project_dir / "output" / "ui_report"
    else:
        report_root = PROJECT_ROOT / "output" / "ui_report"

    report_root.mkdir(parents=True, exist_ok=True)

    total_modules = len(all_results)
    total_passed = sum(r["passed"] for r in all_results)
    total_failed = sum(r["failed"] for r in all_results)
    total_tests = sum(r["total"] for r in all_results)
    overall_rate = f"{total_passed / total_tests * 100:.1f}%" if total_tests > 0 else "N/A"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    title_suffix = f" — {project_name}" if project_name else ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>UI Test Suite Report{title_suffix}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 40px 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1100px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header .meta {{ opacity: 0.9; font-size: 14px; }}
        .summary {{
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 16px;
            padding: 24px 30px;
            background: #f8f9fa;
            border-bottom: 2px solid #e9ecef;
        }}
        .stat-card {{
            text-align: center;
            padding: 16px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .stat-card .value {{
            font-size: 32px;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .stat-card .label {{
            font-size: 12px;
            color: #6c757d;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .stat-card.green .value {{ color: #28a745; }}
        .stat-card.red .value {{ color: #dc3545; }}
        .stat-card.blue .value {{ color: #007bff; }}

        .module-overview {{
            padding: 24px 30px;
            border-bottom: 2px solid #e9ecef;
        }}
        .module-overview h2 {{
            font-size: 20px;
            margin-bottom: 16px;
        }}
        .module-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .module-table th, .module-table td {{
            padding: 10px 14px;
            text-align: left;
            border-bottom: 1px solid #e9ecef;
            font-size: 14px;
        }}
        .module-table th {{
            background: #f8f9fa;
            font-weight: 600;
            color: #495057;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }}
        .badge-pass {{ background: #d4edda; color: #155724; }}
        .badge-fail {{ background: #f8d7da; color: #721c24; }}
        .badge-error {{ background: #fff3cd; color: #856404; }}

        .module-section {{
            padding: 24px 30px;
            border-bottom: 1px solid #e9ecef;
        }}
        .module-section:last-of-type {{ border-bottom: none; }}
        .module-section h2 {{
            font-size: 20px;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .output-box {{
            margin-top: 8px;
            padding: 12px;
            background: #1e1e1e;
            color: #d4d4d4;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            line-height: 1.5;
            white-space: pre-wrap;
            overflow-x: auto;
            max-height: 400px;
            overflow-y: auto;
        }}
        .footer {{
            padding: 16px;
            text-align: center;
            background: #f8f9fa;
            color: #6c757d;
            font-size: 12px;
        }}
        .progress-bar {{
            height: 8px;
            background: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 4px;
        }}
        .progress-bar .fill {{
            height: 100%;
            border-radius: 4px;
        }}
        .progress-bar .fill.green {{ background: #28a745; }}
        .progress-bar .fill.red {{ background: #dc3545; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>UI Test Suite Report{title_suffix}</h1>
            <div class="meta">{now}</div>
        </div>

        <div class="summary">
            <div class="stat-card blue">
                <div class="value">{total_modules}</div>
                <div class="label">Modules</div>
            </div>
            <div class="stat-card">
                <div class="value">{total_tests}</div>
                <div class="label">Total Tests</div>
            </div>
            <div class="stat-card green">
                <div class="value">{total_passed}</div>
                <div class="label">Passed</div>
            </div>
            <div class="stat-card red">
                <div class="value">{total_failed}</div>
                <div class="label">Failed</div>
            </div>
            <div class="stat-card">
                <div class="value">{overall_rate}</div>
                <div class="label">Success Rate</div>
            </div>
        </div>

        <div class="module-overview">
            <h2>Module Overview</h2>
            <table class="module-table">
                <thead>
                    <tr>
                        <th>Project</th>
                        <th>Module</th>
                        <th>Status</th>
                        <th>Passed</th>
                        <th>Failed</th>
                        <th>Rate</th>
                        <th>Progress</th>
                    </tr>
                </thead>
                <tbody>
"""

    for r in all_results:
        if r["status"] == "error":
            badge = '<span class="badge badge-error">ERROR</span>'
        elif r["failed"] == 0:
            badge = '<span class="badge badge-pass">PASS</span>'
        else:
            badge = '<span class="badge badge-fail">FAIL</span>'

        pct = int(r["passed"] / r["total"] * 100) if r["total"] > 0 else 0
        bar_color = "green" if r["failed"] == 0 and r["status"] == "success" else "red"

        html += f"""
                    <tr>
                        <td>{r['project']}</td>
                        <td><strong>{r['module']}</strong></td>
                        <td>{badge}</td>
                        <td>{r['passed']}</td>
                        <td>{r['failed']}</td>
                        <td>{r['success_rate']}</td>
                        <td>
                            <div class="progress-bar">
                                <div class="fill {bar_color}" style="width: {pct}%"></div>
                            </div>
                        </td>
                    </tr>
"""

    html += """
                </tbody>
            </table>
        </div>
"""

    # Module detail sections
    for r in all_results:
        if r["status"] == "error":
            html += f"""
        <div class="module-section">
            <h2>{r['project']}/{r['module']} <span class="badge badge-error">ERROR</span></h2>
            <p style="color: #dc3545;">{r.get('output', 'Unknown error')}</p>
        </div>
"""
            continue

        if r["failed"] == 0:
            badge = '<span class="badge badge-pass">PASS</span>'
        else:
            badge = '<span class="badge badge-fail">FAIL</span>'

        html += f"""
        <div class="module-section">
            <h2>{r['project']}/{r['module']} {badge} <small style="font-size:14px;color:#6c757d;">({r['passed']}/{r['total']}, {r['success_rate']})</small></h2>
            <div class="output-box">{r['output']}</div>
        </div>
"""

    html += """
        <div class="footer">
            <p>Generated by UI Test Suite Runner</p>
        </div>
    </div>
</body>
</html>"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_root / f"suite_report_{timestamp}.html"
    report_path.write_text(html, encoding="utf-8")

    return str(report_path)


def run_suite(project_name: str = None, modules: list = None, version: str = None):
    """运行测试套件，生成一份汇总报告。

    Args:
        project_name: 项目名称（可选），不指定则运行所有项目
        modules: 模块名称列表（可选），不指定则运行所有模块
        version: 版本号（可选）
    """
    scripts = find_all_ui_scripts(project_name, version)

    if not scripts:
        print("No UI test scripts found")
        return

    if modules:
        scripts = [(p, m, proj) for p, m, proj in scripts if m in modules]

    if not scripts:
        print("No matching modules found")
        return

    print(f"Found {len(scripts)} UI module(s) to test")
    print("=" * 60)

    all_results = []

    for script_path, module_name, project in scripts:
        print(f"\n[{project}/{module_name}] Running UI tests...")
        print("-" * 60)

        result = run_ui_script(script_path)
        result["module"] = module_name
        result["project"] = project
        all_results.append(result)

        if result["status"] == "success":
            icon = "PASS" if result["failed"] == 0 else "FAIL"
            print(f"  [{icon}] {result['passed']}/{result['total']} ({result['success_rate']})")
        else:
            print(f"  [ERROR] {result.get('output', 'unknown')}")

    # 生成汇总报告
    report_path = generate_suite_report(all_results, project_name)

    # 打印汇总
    print("\n" + "=" * 60)
    print("Suite Summary")
    print("=" * 60)

    total_modules = len(all_results)
    total_passed = sum(r["passed"] for r in all_results)
    total_failed = sum(r["failed"] for r in all_results)
    total_tests = sum(r["total"] for r in all_results)
    rate = f"{total_passed / total_tests * 100:.1f}%" if total_tests > 0 else "N/A"

    print(f"Modules: {total_modules}")
    print(f"Tests:   {total_passed}/{total_tests} passed ({rate})")
    print(f"Report:  {report_path}")
    print()

    for r in all_results:
        icon = "PASS" if r["status"] == "success" and r["failed"] == 0 else "FAIL"
        if r["status"] == "success":
            print(f"  [{icon}] {r['project']}/{r['module']}: {r['passed']}/{r['total']} ({r['success_rate']})")
        else:
            print(f"  [ERR] {r['project']}/{r['module']}: {r.get('output', 'unknown')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run UI test suite for all modules")
    parser.add_argument("--project", type=str,
                        help="Project name (optional, runs all projects if not specified)")
    parser.add_argument("--modules", type=str, nargs="+",
                        help="Module names to run (optional, runs all modules if not specified)")
    parser.add_argument("--version", type=str,
                        help="Version (optional, uses latest if not specified)")

    args = parser.parse_args()
    run_suite(args.project, args.modules, args.version)
