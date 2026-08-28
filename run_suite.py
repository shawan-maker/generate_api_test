#!/usr/bin/env python3
"""
run_suite.py — 测试套件运行器

扫描所有模块的测试脚本，依次执行并生成一份汇总 HTML 报告。
汇总报告输出到 output/reports/suite_report_{timestamp}.html
"""

import sys
import io
import glob
import re
import argparse
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 确保能找到 lib
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.test_report import run_script_and_capture

# 统一报告输出目录
REPORT_ROOT = PROJECT_ROOT / "output" / "reports"


def find_all_test_scripts(project_name: str = None) -> list:
    """查找所有模块的测试脚本。

    Args:
        project_name: 项目名称（可选），不指定则扫描所有项目

    Returns:
        [(script_path, module_name, project_name), ...] 列表
    """
    scripts = []

    if project_name:
        pattern = PROJECT_ROOT / "projects" / project_name / "flows" / "*" / "*_API测试.py"
    else:
        pattern = PROJECT_ROOT / "projects" / "*" / "flows" / "*" / "*_API测试.py"

    for script_path in sorted(glob.glob(str(pattern))):
        path = Path(script_path)
        module_name = path.stem.replace("_API测试", "")
        # 从路径提取项目名
        project = path.parts[path.parts.index("projects") + 1] if "projects" in path.parts else "unknown"
        scripts.append((str(path), module_name, project))

    return scripts


def run_suite(project_name: str = None, modules: list = None):
    """运行测试套件，生成一份汇总报告。

    Args:
        project_name: 项目名称（可选），不指定则运行所有项目
        modules: 模块名称列表（可选），不指定则运行所有模块
    """
    scripts = find_all_test_scripts(project_name)

    if not scripts:
        print("No test scripts found")
        return

    if modules:
        scripts = [(p, m, proj) for p, m, proj in scripts if m in modules]

    if not scripts:
        print("No matching modules found")
        return

    print(f"Found {len(scripts)} module(s) to test")
    print("=" * 60)

    all_results = []

    for script_path, module_name, project in scripts:
        print(f"\n[{project}/{module_name}] Running tests...")
        print("-" * 60)

        try:
            result = run_script_and_capture(script_path)
            all_results.append({
                "module": module_name,
                "project": project,
                "status": "success",
                "steps": result["steps"],
                "summary": result["summary"],
            })
            s = result["summary"]
            icon = "PASS" if s["failed"] == 0 else "FAIL"
            print(f"  [{icon}] {s['passed']}/{s['total']} ({s['success_rate']})")
        except Exception as e:
            all_results.append({
                "module": module_name,
                "project": project,
                "status": "error",
                "error": str(e),
                "steps": [],
                "summary": {"total": 0, "passed": 0, "failed": 0, "success_rate": "0%"},
            })
            print(f"  [ERROR] {e}")

    # 生成汇总报告
    report_path = generate_suite_report(all_results, project_name)

    # 打印汇总
    print("\n" + "=" * 60)
    print("Suite Summary")
    print("=" * 60)

    total_modules = len(all_results)
    total_passed = sum(r["summary"]["passed"] for r in all_results)
    total_failed = sum(r["summary"]["failed"] for r in all_results)
    total_steps = sum(r["summary"]["total"] for r in all_results)
    rate = f"{total_passed / total_steps * 100:.1f}%" if total_steps > 0 else "N/A"

    print(f"Modules: {total_modules}")
    print(f"Steps:   {total_passed}/{total_steps} passed ({rate})")
    print(f"Report:  {report_path}")
    print()

    for r in all_results:
        icon = "PASS" if r["status"] == "success" and r["summary"]["failed"] == 0 else "FAIL"
        if r["status"] == "success":
            s = r["summary"]
            print(f"  [{icon}] {r['project']}/{r['module']}: {s['passed']}/{s['total']} ({s['success_rate']})")
        else:
            print(f"  [ERR] {r['project']}/{r['module']}: {r.get('error', 'unknown')}")


def generate_suite_report(all_results: list, project_name: str = None) -> str:
    """生成汇总 HTML 报告。

    Args:
        all_results: 各模块的测试结果列表
        project_name: 项目名称（用于报告标题）

    Returns:
        报告文件路径
    """
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    total_modules = len(all_results)
    total_passed = sum(r["summary"]["passed"] for r in all_results)
    total_failed = sum(r["summary"]["failed"] for r in all_results)
    total_steps = sum(r["summary"]["total"] for r in all_results)
    overall_rate = f"{total_passed / total_steps * 100:.1f}%" if total_steps > 0 else "N/A"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    title_suffix = f" — {project_name}" if project_name else ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>API Test Suite Report{title_suffix}</title>
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

        /* Module overview table */
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

        /* Module detail sections */
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
        .step {{
            margin-bottom: 12px;
            border: 1px solid #e9ecef;
            border-radius: 8px;
            overflow: hidden;
        }}
        .step-header {{
            padding: 10px 16px;
            background: #f8f9fa;
            border-bottom: 1px solid #e9ecef;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 14px;
        }}
        .step-name {{ font-weight: 600; }}
        .step-details {{ padding: 12px 16px; font-size: 13px; }}
        .detail-row {{
            display: flex;
            padding: 4px 0;
            border-bottom: 1px solid #f5f5f5;
        }}
        .detail-row:last-child {{ border-bottom: none; }}
        .detail-label {{
            font-weight: 600;
            width: 100px;
            color: #6c757d;
        }}
        .detail-value {{
            flex: 1;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            word-break: break-all;
        }}
        .step-output {{
            margin-top: 8px;
            padding: 10px;
            background: #1e1e1e;
            color: #d4d4d4;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 11px;
            line-height: 1.5;
            white-space: pre-wrap;
            overflow-x: auto;
            max-height: 300px;
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
            <h1>API Test Suite Report{title_suffix}</h1>
            <div class="meta">{now}</div>
        </div>

        <div class="summary">
            <div class="stat-card blue">
                <div class="value">{total_modules}</div>
                <div class="label">Modules</div>
            </div>
            <div class="stat-card">
                <div class="value">{total_steps}</div>
                <div class="label">Total Steps</div>
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
        s = r["summary"]
        if r["status"] == "error":
            badge = '<span class="badge badge-error">ERROR</span>'
        elif s["failed"] == 0:
            badge = '<span class="badge badge-pass">PASS</span>'
        else:
            badge = '<span class="badge badge-fail">FAIL</span>'

        pct = int(s["passed"] / s["total"] * 100) if s["total"] > 0 else 0
        bar_color = "green" if s["failed"] == 0 and r["status"] == "success" else "red"

        html += f"""
                    <tr>
                        <td>{r['project']}</td>
                        <td><strong>{r['module']}</strong></td>
                        <td>{badge}</td>
                        <td>{s['passed']}</td>
                        <td>{s['failed']}</td>
                        <td>{s['success_rate']}</td>
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
            <p style="color: #dc3545;">{r.get('error', 'Unknown error')}</p>
        </div>
"""
            continue

        s = r["summary"]
        if s["failed"] == 0:
            badge = '<span class="badge badge-pass">PASS</span>'
        else:
            badge = '<span class="badge badge-fail">FAIL</span>'

        html += f"""
        <div class="module-section">
            <h2>{r['project']}/{r['module']} {badge} <small style="font-size:14px;color:#6c757d;">({s['passed']}/{s['total']}, {s['success_rate']})</small></h2>
"""

        for step in r["steps"]:
            status_class = "badge-pass" if step["status"] == "passed" else "badge-fail"
            status_text = "PASS" if step["status"] == "passed" else "FAIL"

            html += f"""
            <div class="step">
                <div class="step-header">
                    <span class="step-name">{step['name'].upper()}</span>
                    <span class="badge {status_class}">{status_text}</span>
                </div>
                <div class="step-details">
                    <div class="detail-row">
                        <div class="detail-label">API:</div>
                        <div class="detail-value">{step['api_path']}</div>
                    </div>
"""
            if step.get("http_status"):
                html += f"""
                    <div class="detail-row">
                        <div class="detail-label">HTTP:</div>
                        <div class="detail-value">{step['http_status']}</div>
                    </div>
"""
            if step.get("extracted_id"):
                html += f"""
                    <div class="detail-row">
                        <div class="detail-label">ID:</div>
                        <div class="detail-value">{step['extracted_id']}</div>
                    </div>
"""
            html += f"""
                    <div class="step-output">{step['output']}</div>
                </div>
            </div>
"""

        html += """
        </div>
"""

    html += """
        <div class="footer">
            <p>Generated by Manifest + Runtime Library Architecture</p>
        </div>
    </div>
</body>
</html>"""

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_ROOT / f"suite_report_{timestamp}.html"
    report_path.write_text(html, encoding="utf-8")

    return str(report_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run test suite for all modules")
    parser.add_argument("--project", type=str,
                        help="Project name (optional, runs all projects if not specified)")
    parser.add_argument("--modules", type=str, nargs="+",
                        help="Module names to run (optional, runs all modules if not specified)")

    args = parser.parse_args()
    run_suite(args.project, args.modules)
