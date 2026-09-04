"""
ui_report.py — UI 测试 HTML 报告生成器

从 UI 测试执行结果生成可视化 HTML 报告。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict


def generate_ui_report(results: List[Dict], module_name: str) -> str:
    """生成 UI 测试 HTML 报告。

    Args:
        results: 测试结果列表，每个结果包含 operation, status, duration, steps
        module_name: 模块名称

    Returns:
        报告文件路径
    """
    report_dir = Path(__file__).parent / "output" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    # 统计
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "passed")
    failed = total - passed
    pass_rate = f"{passed / total * 100:.1f}%" if total > 0 else "0%"

    # 时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = report_dir / f"{module_name}_ui_report_{timestamp}.html"

    # 生成 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{module_name} - UI 测试报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    padding: 20px;
    min-height: 100vh;
}}
.container {{
    max-width: 1200px;
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
}}
.header h1 {{ font-size: 28px; margin-bottom: 10px; }}
.header .meta {{ opacity: 0.9; font-size: 14px; }}
.summary {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 20px;
    padding: 30px;
    background: #f8f9fa;
    border-bottom: 2px solid #e9ecef;
}}
.stat {{
    background: white;
    padding: 20px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
    text-align: center;
}}
.stat-value {{
    font-size: 36px;
    font-weight: bold;
    margin-bottom: 8px;
}}
.stat-label {{
    font-size: 14px;
    color: #6c757d;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.stat.pass .stat-value {{ color: #28a745; }}
.stat.fail .stat-value {{ color: #dc3545; }}
.stat.total .stat-value {{ color: #007bff; }}
.stat.rate .stat-value {{ color: #ffc107; }}
.operations {{
    padding: 30px;
}}
.operation {{
    margin-bottom: 20px;
    border: 1px solid #e9ecef;
    border-radius: 8px;
    overflow: hidden;
}}
.operation-header {{
    padding: 16px 20px;
    background: #f8f9fa;
    border-bottom: 1px solid #e9ecef;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.operation-name {{
    font-size: 18px;
    font-weight: 600;
}}
.badge {{
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: 600;
}}
.badge-pass {{ background: #d4edda; color: #155724; }}
.badge-fail {{ background: #f8d7da; color: #721c24; }}
.operation-body {{
    padding: 20px;
}}
.step {{
    padding: 12px 16px;
    margin-bottom: 8px;
    background: #f8f9fa;
    border-radius: 6px;
    border-left: 4px solid #6c757d;
}}
.step.pass {{ border-left-color: #28a745; }}
.step.fail {{ border-left-color: #dc3545; }}
.step-header {{
    display: flex;
    justify-content: space-between;
    margin-bottom: 6px;
}}
.step-action {{
    font-weight: 600;
    color: #495057;
}}
.step-duration {{
    color: #6c757d;
    font-size: 13px;
}}
.step-detail {{
    font-size: 13px;
    color: #6c757d;
    margin-top: 4px;
}}
.error-message {{
    margin-top: 12px;
    padding: 12px;
    background: #f8d7da;
    color: #721c24;
    border-radius: 6px;
    font-family: 'Courier New', monospace;
    font-size: 13px;
    white-space: pre-wrap;
}}
.footer {{
    padding: 20px;
    text-align: center;
    color: #6c757d;
    font-size: 13px;
    background: #f8f9fa;
}}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🎨 {module_name} - UI 测试报告</h1>
        <div class="meta">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
    </div>

    <div class="summary">
        <div class="stat total">
            <div class="stat-value">{total}</div>
            <div class="stat-label">Total Operations</div>
        </div>
        <div class="stat pass">
            <div class="stat-value">{passed}</div>
            <div class="stat-label">Passed</div>
        </div>
        <div class="stat fail">
            <div class="stat-value">{failed}</div>
            <div class="stat-label">Failed</div>
        </div>
        <div class="stat rate">
            <div class="stat-value">{pass_rate}</div>
            <div class="stat-label">Pass Rate</div>
        </div>
    </div>

    <div class="operations">
"""

    for result in results:
        op_name = result.get("operation", "Unknown")
        status = result.get("status", "unknown")
        duration = result.get("duration", 0)
        steps = result.get("steps", [])
        error = result.get("error")

        badge_class = "badge-pass" if status == "passed" else "badge-fail"
        badge_text = "✅ PASSED" if status == "passed" else "❌ FAILED"

        html += f"""
        <div class="operation">
            <div class="operation-header">
                <div class="operation-name">{op_name}</div>
                <span class="badge {badge_class}">{badge_text}</span>
            </div>
            <div class="operation-body">
                <div style="margin-bottom: 12px; color: #6c757d;">
                    ⏱️ Duration: {duration:.2f}s | 📝 Steps: {len(steps)}
                </div>
"""

        for step in steps:
            step_status = step.get("status", "unknown")
            step_action = step.get("action", "")
            step_detail = step.get("detail", "")
            step_duration = step.get("duration", 0)

            step_class = "pass" if step_status == "passed" else "fail"

            html += f"""
                <div class="step {step_class}">
                    <div class="step-header">
                        <span class="step-action">{step_action}</span>
                        <span class="step-duration">{step_duration:.3f}s</span>
                    </div>
"""
            if step_detail:
                html += f'                    <div class="step-detail">{step_detail}</div>\n'
            html += "                </div>\n"

        if error:
            html += f'                <div class="error-message">{error}</div>\n'

        html += """
            </div>
        </div>
"""

    html += """
    </div>

    <div class="footer">
        Generated by API AI Test Framework - UI Test Runner
    </div>
</div>
</body>
</html>
"""

    report_file.write_text(html, encoding="utf-8")
    return str(report_file)
