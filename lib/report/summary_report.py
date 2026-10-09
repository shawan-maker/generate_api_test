"""
summary_report.py — 汇总报告生成器

从 module_status.json + JSONL 日志 生成项目级汇总报告（API 和 UI 各一份）。

数据源：
  - module_status.json：模块列表、分组、状态、跳过原因
  - workspace/<project>/output/logs/{module}_API测试.jsonl：API 调用详情
  - workspace/<project>/output/ui_logs/{module}_UI测试.jsonl：UI 操作结果
"""

import base64
import json
import logging
from pathlib import Path
from datetime import datetime

LOG = logging.getLogger("summary_report")


def _parse_jsonl_log(log_file: Path) -> dict:
    """解析 JSONL 日志文件，提取 API 调用统计和完整数据。

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "pass_rate": str,
            "api_calls": [完整的 API 调用记录列表],
        }
    """
    if not log_file.exists():
        return None

    api_calls = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "api_call":
                        api_calls.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        LOG.warning(f"解析日志失败 {log_file}: {e}")
        return None

    if not api_calls:
        return None

    total = len(api_calls)
    passed = sum(1 for c in api_calls if c.get("assertion") == "passed")
    failed = total - passed
    pass_rate = f"{passed / total * 100:.1f}%" if total > 0 else "0%"

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": pass_rate,
        "api_calls": api_calls,
    }


def _get_method_color(method: str) -> str:
    """获取 HTTP 方法的颜色。"""
    colors = {
        "GET": "#61affe",
        "POST": "#49cc90",
        "PUT": "#fca130",
        "DELETE": "#f93e3e",
        "PATCH": "#50e3c2",
    }
    return colors.get(method, "#6b7280")


def _render_request_detail(call: dict) -> str:
    """渲染请求详情 HTML。"""
    req = call.get("request", {})
    headers = req.get("headers", {})
    body = req.get("body")

    html = '<div class="detail-section"><div class="detail-title">📤 请求详情</div>'

    # Request Headers
    if headers:
        html += '<div class="section-label">Request Headers</div>'
        html += '<table class="kv-table">'
        for k, v in headers.items():
            html += f'<tr><td class="hk">{k}</td><td class="hv">{v}</td></tr>'
        html += '</table>'

    # Request Body
    if body:
        html += '<div class="section-label">Request Body</div>'
        html += f'<pre class="json-block">{json.dumps(body, ensure_ascii=False, indent=2)}</pre>'

    html += '</div>'
    return html


def _render_response_detail(call: dict) -> str:
    """渲染响应详情 HTML。"""
    resp = call.get("response", {})
    status = resp.get("status", 0)
    body = resp.get("body")

    html = '<div class="detail-section"><div class="detail-title">📥 响应详情</div>'

    # Response Status
    html += f'<div class="section-label">Response Status</div>'
    html += f'<div class="resp-status">HTTP {status}</div>'

    # Response Body
    if body:
        html += '<div class="section-label">Response Body</div>'
        html += f'<pre class="json-block">{json.dumps(body, ensure_ascii=False, indent=2)}</pre>'

    html += '</div>'
    return html


def _render_api_calls_html(api_calls: list) -> str:
    """渲染 API 调用卡片列表（复用 test_report.py 的逻辑）。

    Args:
        api_calls: JSONL 解析出的 API 调用记录

    Returns:
        HTML 字符串，包含完整的请求卡片（含 headers、body、response）
    """
    html_parts = []

    for i, call in enumerate(api_calls, 1):
        method = call["request"]["method"]
        url = call["request"]["url"]
        status = call["response"]["status"]
        assertion = call.get("assertion", "pending")
        label = call.get("step_label", "")

        # 判断是否成功
        is_ok = assertion == "passed"
        badge_class = "b-ok" if is_ok else "b-fail"
        badge_text = "✅ 通过" if is_ok else "❌ 失败"

        # 生成卡片（复用 test_report.py 的逻辑）
        card_html = f'''    <div class="req {'ok' if is_ok else 'fail'}">
      <div class="req-head">
        <span class="req-no">{i}</span>
        <span class="method" style="background:{_get_method_color(method)}">{method}</span>
        <code class="path">{url}</code>
        <span class="badge {badge_class}">{badge_text}</span>
      </div>
      <div class="req-body">
        <div class="mark"><span class="mark-ic">{'✅' if is_ok else '❌'}</span>{label}: HTTP {status}</div>
        <details class="detail">
          <summary>查看请求/响应详情</summary>
          {_render_request_detail(call)}
          {_render_response_detail(call)}
        </details>
      </div>
    </div>
'''
        html_parts.append(card_html)

    return '\n'.join(html_parts)




def _get_workspace_dir(project_dir: Path) -> Path:
    """获取 workspace 目录。"""
    try:
        from core.discovery.path_mapper import get_workspace_dir
        return get_workspace_dir(project_dir)
    except ImportError:
        # 兜底：framework_root / workspace / project_name
        framework_root = project_dir.parent.parent
        return framework_root / "workspace" / project_dir.name


def _get_log_dir(project_dir: Path) -> Path:
    """获取 API JSONL 日志目录。"""
    workspace = _get_workspace_dir(project_dir)
    return workspace / "output" / "logs"


def _get_ui_log_dir(project_dir: Path) -> Path:
    """获取 UI JSONL 日志目录。"""
    workspace = _get_workspace_dir(project_dir)
    return workspace / "output" / "ui_logs"


def _parse_ui_jsonl_log(log_file: Path) -> dict:
    """解析 UI JSONL 日志文件，提取操作结果统计和完整数据。

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "skipped": int,
            "pass_rate": str,
            "operations": [完整的操作记录列表],
        }
        或 None（无数据时）
    """
    if not log_file.exists():
        return None

    operations = []
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("type") == "ui_operation":
                        operations.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        LOG.warning(f"解析 UI 日志失败 {log_file}: {e}")
        return None

    if not operations:
        return None

    total = len(operations)
    passed = sum(1 for op in operations if op.get("status") in ("passed", "passed_with_note"))
    skipped = sum(1 for op in operations if op.get("status") == "skipped")
    failed = total - passed - skipped
    exec_total = passed + failed
    pass_rate = f"{passed / exec_total * 100:.1f}%" if exec_total > 0 else "N/A"

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": pass_rate,
        "operations": operations,
    }


def _load_screenshot_as_base64(screenshot_rel: str, ui_log_dir: Path) -> str:
    """从 PNG 文件加载截图并转为 base64 字符串。

    Args:
        screenshot_rel: 相对于 ui_logs 目录的路径（如 "screenshots/用户管理_1.png"）
        ui_log_dir: UI 日志目录

    Returns:
        base64 字符串，失败时返回 ""
    """
    if not screenshot_rel:
        return ""
    filepath = ui_log_dir / screenshot_rel
    if not filepath.exists():
        return ""
    try:
        png_bytes = filepath.read_bytes()
        return base64.b64encode(png_bytes).decode("ascii")
    except Exception:
        return ""


def _esc(s) -> str:
    """HTML 转义。"""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _render_ui_results_html(operations: list, ui_log_dir: Path) -> str:
    """渲染 UI 操作结果 HTML 表格（与 _render_api_calls_html 对称）。

    从 JSONL 的 operations 列表生成完整的 UI 测试报告表格，
    包含操作名、耗时、结果、截图（从 PNG 加载）、步骤详情。

    Args:
        operations: JSONL 解析出的操作记录列表
        ui_log_dir: UI JSONL 日志目录（用于加载截图 PNG）

    Returns:
        HTML 字符串
    """
    rows = []
    for op in operations:
        seq = op.get("seq", "")
        display_name = _esc(op.get("display_name", ""))
        op_key = _esc(op.get("operation", ""))
        status = op.get("status", "unknown")
        duration = op.get("duration", 0)
        screenshot_rel = op.get("screenshot", "")
        steps = op.get("steps", [])
        error = op.get("error", "")
        failure_reason = op.get("failure_reason", "")
        failure_type = op.get("failure_type", "")
        note = op.get("note", "")
        expected_status = op.get("expected_status", "")

        # 操作名
        if display_name and display_name != op_key:
            operation_html = f'{display_name}<br><span class="op-key">{op_key}</span>'
        else:
            operation_html = op_key

        # 状态 badge
        if status == "passed_with_note":
            badge_cls, status_text, row_cls = "badge-warn", "通过(备注)", "warn"
        elif status == "passed":
            badge_cls, status_text, row_cls = "badge-ok", "通过", "ok"
        elif status == "failed":
            badge_cls, status_text, row_cls = "badge-fail", "失败", "fail"
        else:
            badge_cls, status_text, row_cls = "badge-skip", "跳过", "skip"

        # 截图
        if screenshot_rel:
            b64 = _load_screenshot_as_base64(screenshot_rel, ui_log_dir)
            if b64:
                screenshot_html = (
                    f'<td><a href="data:image/png;base64,{b64}" class="screenshot-link" '
                    f'onclick="showLightbox(this.href); return false;">'
                    f'<img src="data:image/png;base64,{b64}" class="screenshot-thumb"></a></td>'
                )
            else:
                screenshot_html = '<td><span class="muted">截图丢失</span></td>'
        else:
            screenshot_html = '<td><span class="muted">无截图</span></td>'

        # 步骤详情
        steps_html = ""
        if steps:
            step_rows = []
            for si, s in enumerate(steps, 1):
                s_action = _esc(s.get("action", ""))
                s_status = s.get("status", "")
                s_badge_cls = "badge-ok" if s_status == "passed" else "badge-fail" if s_status == "failed" else "badge-skip"
                s_status_text = "通过" if s_status == "passed" else "失败" if s_status == "failed" else "跳过"
                s_row_cls = ' class="fail-row"' if s_status == "failed" else ""
                s_locator = _esc(s.get("locator", ""))
                s_locator_html = f'<code class="locator">{s_locator}</code>' if s_locator else '<span class="muted">-</span>'
                s_desc = _esc(s.get("description", ""))
                step_rows.append(
                    f'<tr{s_row_cls}><td>{si}</td><td>{s_action}</td><td>{s_locator_html}</td>'
                    f'<td>{s_desc}</td><td><span class="badge {s_badge_cls}">{s_status_text}</span></td></tr>'
                )
            steps_html = (
                f'<details class="steps"><summary>执行步骤 ({len(steps)})</summary>'
                f'<div class="steps-body"><table><thead><tr><th>#</th><th>操作</th><th>Locator</th>'
                f'<th>说明</th><th>结果</th></tr></thead><tbody>{"".join(step_rows)}</tbody></table></div></details>'
            )

        # 失败/备注信息
        detail_parts = []
        if failure_reason:
            detail_parts.append(f'<div class="failure-info"><span class="failure-label">失败原因:</span> {_esc(failure_reason)}</div>')
        if failure_type:
            detail_parts.append(f'<div class="failure-info"><span class="failure-label">失败类型:</span> <code>{_esc(failure_type)}</code></div>')
        if note and not failure_reason:
            detail_parts.append(f'<div class="note-info"><span class="note-label">备注:</span> {_esc(note)}</div>')
        if expected_status == "failed":
            detail_parts.append(f'<div class="expected-info"><span class="expected-label">Stage 1 预期:</span> 失败</div>')
        if error and error != failure_reason:
            detail_parts.append(f'<div class="error-msg">{_esc(error)}</div>')
        detail_html = "\n".join(detail_parts)

        rows.append(f'''<tr class="{row_cls}">
<td>{seq}</td>
<td>{operation_html}</td>
<td>{duration:.2f}s</td>
<td><span class="badge {badge_cls}">{status_text}</span></td>
{screenshot_html}
<td>{steps_html}{detail_html}</td>
</tr>''')

    if not rows:
        return '<p class="no-data">暂无操作数据</p>'

    table_html = f'''<table>
<thead><tr><th>#</th><th>操作</th><th>耗时</th><th>结果</th><th>截图</th><th>详情</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>'''

    return table_html


def generate_api_summary(project_dir: Path, version: str) -> str:
    """生成 API 汇总报告。

    数据来源：
      - module_status.json：模块状态
      - JSONL 日志：API 调用详情（每个模块的请求/响应/断言）

    Args:
        project_dir: 项目目录
        version: 版本号

    Returns:
        报告文件路径
    """
    status_path = project_dir / version / "module_status.json"
    if not status_path.exists():
        LOG.warning(f"module_status.json 不存在: {status_path}")
        return ""

    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    modules = status_data.get("modules", [])
    summary = status_data.get("summary", {})

    # 按 group 分组
    grouped_modules = {}
    for m in modules:
        g = m.get("group", "未分类")
        if g not in grouped_modules:
            grouped_modules[g] = []
        grouped_modules[g].append(m)

    # 解析 JSONL 日志（每个有 API 脚本的模块）
    log_dir = _get_log_dir(project_dir)
    module_logs = {}
    for m in modules:
        name = m.get("name", "")
        log_file = log_dir / f"{name}_API测试.jsonl"
        log_data = _parse_jsonl_log(log_file)
        if log_data:
            module_logs[name] = log_data

    # 全局 API 统计
    global_total = sum(d["total"] for d in module_logs.values())
    global_passed = sum(d["passed"] for d in module_logs.values())
    global_failed = sum(d["failed"] for d in module_logs.values())
    global_pass_rate = f"{global_passed / global_total * 100:.1f}%" if global_total > 0 else "N/A"

    # 模块级统计
    total = summary.get("total", len(modules))
    ok = summary.get("ok", 0)
    skipped = summary.get("skipped", 0)
    failed = summary.get("failed", 0)
    module_pass_rate = f"{ok / total * 100:.1f}%" if total > 0 else "0%"

    # 生成 HTML
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_name = f"API汇总报告_{timestamp}.html"
    report_dir = project_dir / version / "api" / "reports" / "_summary"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{project_dir.name} API 汇总报告</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "SF Pro Text", "Helvetica Neue", "Microsoft YaHei", sans-serif;
         background: #f4f5f7; color: #1f2937; line-height: 1.6; }}
  .header {{ background: linear-gradient(135deg, #1f2937 0%, #111827 100%); color: #fff;
             padding: 30px 32px 26px; }}
  .header h1 {{ font-size: 24px; font-weight: 700; }}
  .header .sub {{ color: #9ca3af; font-size: 13px; margin-top: 8px; }}
  .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px; }}
  .overview {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
               gap: 16px; margin-bottom: 32px; }}
  .stat {{ background: #fff; border-radius: 10px; padding: 20px; text-align: center;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); border-top: 3px solid #e5e7eb; }}
  .stat.ok {{ border-top-color: #49cc90; }}
  .stat.skip {{ border-top-color: #fca130; }}
  .stat.fail {{ border-top-color: #f93e3e; }}
  .stat.info {{ border-top-color: #61affe; }}
  .stat b {{ display: block; font-size: 32px; margin-bottom: 4px; }}
  .stat.ok b {{ color: #1a7f37; }}
  .stat.skip b {{ color: #b45309; }}
  .stat.fail b {{ color: #dc2626; }}
  .stat.info b {{ color: #2563eb; }}
  .stat span {{ font-size: 12px; color: #6b7280; }}
  .section-title {{ font-size: 18px; font-weight: 700; color: #1f2937; margin: 32px 0 16px;
                    padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }}
  .group {{ background: #fff; border-radius: 10px; margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden; }}
  .group-head {{ background: #f8f9fb; padding: 14px 20px; border-bottom: 1px solid #e5e7eb;
                 display: flex; justify-content: space-between; align-items: center; }}
  .group-head h2 {{ font-size: 16px; font-weight: 600; color: #1f2937; }}
  .group-head .count {{ font-size: 13px; color: #6b7280; }}
  .module-section {{ padding: 20px; border-bottom: 1px solid #f3f4f6; }}
  .module-section:last-child {{ border-bottom: none; }}
  .module-title {{ font-size: 15px; font-weight: 600; color: #1f2937; margin-bottom: 12px; }}
  .module-stats {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
  .module-stats span {{ font-size: 12px; color: #6b7280; background: #f8f9fb; padding: 4px 10px;
                        border-radius: 4px; }}
  .module-stats span b {{ color: #1f2937; }}
  .requests {{ margin-top: 16px; }}
  .req {{ background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 12px;
          box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .req-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
  .req-no {{ background: #f3f4f6; color: #6b7280; width: 24px; height: 24px; border-radius: 50%;
             display: inline-flex; align-items: center; justify-content: center; font-size: 12px; }}
  .method {{ display: inline-block; padding: 4px 8px; border-radius: 4px; color: #fff;
             font-size: 11px; font-weight: 600; }}
  .path {{ color: #374151; font-family: monospace; font-size: 12px; flex: 1; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .b-ok {{ background: #d1fae5; color: #065f46; }}
  .b-fail {{ background: #fee2e2; color: #991b1b; }}
  .req-body {{ margin-top: 8px; }}
  .mark {{ color: #6b7280; font-size: 13px; }}
  .mark-ic {{ margin-right: 4px; }}
  .detail {{ margin-top: 8px; }}
  .detail summary {{ color: #2563eb; cursor: pointer; font-size: 12px; }}
  .detail-section {{ background: #f9fafb; padding: 12px; border-radius: 4px; margin-top: 8px; }}
  .detail-title {{ font-weight: 600; color: #374151; margin-bottom: 8px; font-size: 13px; }}
  .section-label {{ color: #6b7280; font-size: 12px; margin-top: 8px; margin-bottom: 4px;
                    text-transform: uppercase; letter-spacing: .5px; }}
  .kv-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .kv-table td {{ padding: 4px 8px; border-bottom: 1px solid #e5e7eb; }}
  .kv-table .hk {{ color: #6b7280; font-weight: 500; width: 120px; }}
  .kv-table .hv {{ color: #374151; font-family: monospace; word-break: break-all; }}
  .json-block {{ background: #1f2937; color: #f3f4f6; padding: 12px; border-radius: 4px;
                 font-size: 11px; overflow-x: auto; max-height: 300px; white-space: pre-wrap;
                 word-break: break-all; font-family: monospace; }}
  .resp-status {{ color: #374151; font-size: 13px; }}
  .footer {{ text-align: center; color: #9ca3af; font-size: 12px; padding: 30px 0; }}
  .no-data {{ color: #9ca3af; font-style: italic; padding: 8px 0; }}
</style>
</head>
<body>
<div class="header">
  <h1>{project_dir.name} API 汇总报告</h1>
  <div class="sub">生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · 版本：{version}</div>
</div>
<div class="wrap">
  <div class="section-title">模块概览</div>
  <div class="overview">
    <div class="stat"><b>{total}</b><span>总模块数</span></div>
    <div class="stat ok"><b>{ok}</b><span>成功</span></div>
    <div class="stat skip"><b>{skipped}</b><span>跳过</span></div>
    <div class="stat fail"><b>{failed}</b><span>失败/阻塞</span></div>
    <div class="stat ok"><b>{module_pass_rate}</b><span>模块通过率</span></div>
  </div>

  <div class="section-title">API 调用统计</div>
  <div class="overview">
    <div class="stat info"><b>{global_total}</b><span>总请求数</span></div>
    <div class="stat ok"><b>{global_passed}</b><span>通过</span></div>
    <div class="stat fail"><b>{global_failed}</b><span>失败</span></div>
    <div class="stat info"><b>{global_pass_rate}</b><span>请求通过率</span></div>
  </div>
"""

    # 按 group 输出模块
    for group_name in sorted(grouped_modules.keys()):
        html += f"""  <div class="group">
    <div class="group-head">
      <h2>📂 {group_name}</h2>
    </div>
"""

        for module in grouped_modules[group_name]:
            module_name = module["name"]
            module_status = module["status"]
            reason = module.get("reason", "")

            # 读取该模块的 JSONL 日志
            log_data = module_logs.get(module_name)

            if log_data:
                api_calls = log_data.get("api_calls", [])
                calls_html = _render_api_calls_html(api_calls)

                html += f"""    <div class="module-section" id="module-{module_name}">
      <h3 class="module-title">{module_name}</h3>
      <div class="module-stats">
        <span>总请求: <b>{log_data["total"]}</b></span>
        <span>通过: <b>{log_data["passed"]}</b></span>
        <span>失败: <b>{log_data["failed"]}</b></span>
        <span>通过率: <b>{log_data["pass_rate"]}</b></span>
      </div>
      <div class="requests">
        {calls_html}
      </div>
    </div>
"""
            else:
                # 无日志数据
                html += f"""    <div class="module-section">
      <h3 class="module-title">{module_name}</h3>
      <p class="no-data">暂无测试数据{f'（{reason}）' if reason else ''}</p>
    </div>
"""

        html += """  </div>
"""

    html += f"""</div>
<div class="footer">API_AI_test 汇总报告 · {project_dir.name} · {version}</div>
</body>
</html>"""

    report_path.write_text(html, encoding="utf-8")
    LOG.info(f"  📊 API 汇总报告已生成: {report_path}")
    return str(report_path)


def generate_ui_summary(project_dir: Path, version: str) -> str:
    """生成 UI 汇总报告。

    数据来源：
      - workspace/<project>/output/ui_logs/{module}_UI测试.jsonl：UI 操作结果

    Args:
        project_dir: 项目目录
        version: 版本号

    Returns:
        报告文件路径
    """
    status_path = project_dir / version / "module_status.json"
    if not status_path.exists():
        LOG.warning(f"module_status.json 不存在: {status_path}")
        return ""

    status_data = json.loads(status_path.read_text(encoding="utf-8"))
    modules = status_data.get("modules", [])
    summary = status_data.get("summary", {})

    # 按 group 分组
    grouped_modules = {}
    for m in modules:
        g = m.get("group", "未分类")
        if g not in grouped_modules:
            grouped_modules[g] = []
        grouped_modules[g].append(m)

    # 扫描 UI JSONL 日志（与 API 报告一致）
    ui_log_dir = _get_ui_log_dir(project_dir)
    module_ui_logs = {}
    for m in modules:
        name = m.get("name", "")
        jsonl_file = ui_log_dir / f"{name}_UI测试.jsonl"
        log_data = _parse_ui_jsonl_log(jsonl_file)
        if log_data:
            module_ui_logs[name] = log_data

    # 统计
    total = summary.get("total", len(modules))
    ui_script_count = sum(1 for m in modules if m.get("ui_script"))
    ui_report_count = len(module_ui_logs)

    # 全局 UI 统计（从 JSONL）
    global_total = sum(d["total"] for d in module_ui_logs.values())
    global_passed = sum(d["passed"] for d in module_ui_logs.values())
    global_failed = sum(d["failed"] for d in module_ui_logs.values())
    global_pass_rate = f"{global_passed / global_total * 100:.1f}%" if global_total > 0 else "N/A"

    # 生成 HTML
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_name = f"UI汇总报告_{timestamp}.html"
    report_dir = project_dir / version / "ui" / "reports" / "_summary"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{project_dir.name} UI 汇总报告</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "SF Pro Text", "Helvetica Neue", "Microsoft YaHei", sans-serif;
         background: #f4f5f7; color: #1f2937; line-height: 1.6; }}
  .header {{ background: linear-gradient(135deg, #1f2937 0%, #111827 100%); color: #fff;
             padding: 30px 32px 26px; }}
  .header h1 {{ font-size: 24px; font-weight: 700; }}
  .header .sub {{ color: #9ca3af; font-size: 13px; margin-top: 8px; }}
  .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px 20px; }}
  .overview {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
               gap: 16px; margin-bottom: 32px; }}
  .stat {{ background: #fff; border-radius: 10px; padding: 20px; text-align: center;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); border-top: 3px solid #e5e7eb; }}
  .stat.ok {{ border-top-color: #49cc90; }}
  .stat.skip {{ border-top-color: #fca130; }}
  .stat.fail {{ border-top-color: #f93e3e; }}
  .stat.info {{ border-top-color: #61affe; }}
  .stat b {{ display: block; font-size: 32px; margin-bottom: 4px; }}
  .stat.ok b {{ color: #1a7f37; }}
  .stat.skip b {{ color: #b45309; }}
  .stat.fail b {{ color: #dc2626; }}
  .stat.info b {{ color: #2563eb; }}
  .stat span {{ font-size: 12px; color: #6b7280; }}
  .section-title {{ font-size: 18px; font-weight: 700; color: #1f2937; margin: 32px 0 16px;
                    padding-bottom: 8px; border-bottom: 2px solid #e5e7eb; }}
  .group {{ background: #fff; border-radius: 10px; margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,.06); overflow: hidden; }}
  .group-head {{ background: #f8f9fb; padding: 14px 20px; border-bottom: 1px solid #e5e7eb;
                 display: flex; justify-content: space-between; align-items: center; }}
  .group-head h2 {{ font-size: 16px; font-weight: 600; color: #1f2937; }}
  .module-section {{ padding: 20px; border-bottom: 1px solid #f3f4f6; }}
  .module-section:last-child {{ border-bottom: none; }}
  .module-title {{ font-size: 15px; font-weight: 600; color: #1f2937; margin-bottom: 12px; }}
  .module-stats {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
  .module-stats span {{ font-size: 12px; color: #6b7280; background: #f8f9fb; padding: 4px 10px;
                        border-radius: 4px; }}
  .module-stats span b {{ color: #1f2937; }}
  .ui-report-content {{ margin-top: 12px; }}
  .ui-report-content table {{ width: 100%; border-collapse: collapse; font-size: 12px;
                               background: #fff; border-radius: 8px; overflow: hidden;
                               box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
  .ui-report-content th {{ background: #f9fafb; padding: 8px 10px; text-align: left;
                            border-bottom: 2px solid #e5e7eb; white-space: nowrap; }}
  .ui-report-content td {{ padding: 7px 10px; border-bottom: 1px solid #f3f4f6;
                            vertical-align: top; word-break: break-all; }}
  .ui-report-content tr.ok {{ background: #f0fdf4; }}
  .ui-report-content tr.fail {{ background: #fef2f2; }}
  .ui-report-content tr.warn {{ background: #fffbeb; }}
  .ui-report-content tr.skip {{ background: #f9fafb; }}
  .ui-report-content .badge {{ display: inline-block; padding: 1px 9px; border-radius: 10px;
                                font-size: 11px; font-weight: 600; color: #fff; }}
  .ui-report-content .badge.ok {{ background: #10b981; }}
  .ui-report-content .badge.fail {{ background: #ef4444; }}
  .ui-report-content .badge.skip {{ background: #6b7280; }}
  .ui-report-content .badge-warn {{ background: #f39c12; color: #fff; padding: 2px 8px;
                                     border-radius: 8px; font-size: 11px; font-weight: 600; }}
  .ui-report-content .badge-ok {{ background: #d5f5e3; color: #27ae60; padding: 2px 8px;
                                   border-radius: 8px; font-size: 11px; font-weight: 600; }}
  .ui-report-content .badge-fail {{ background: #fadbd8; color: #e74c3c; padding: 2px 8px;
                                     border-radius: 8px; font-size: 11px; font-weight: 600; }}
  .ui-report-content .badge-skip {{ background: #f2f3f4; color: #95a5a6; padding: 2px 8px;
                                     border-radius: 8px; font-size: 11px; font-weight: 600; }}
  .ui-report-content .op-key {{ font-size: 10px; color: #95a5a6; font-weight: 400; }}
  .ui-report-content .screenshot-thumb {{ max-width: 80px; max-height: 60px;
                                           border: 1px solid #d1d5db; border-radius: 4px;
                                           cursor: pointer; }}
  .ui-report-content .screenshot-link {{ display: inline-block; }}
  .ui-report-content .muted {{ color: #95a5a6; font-size: 11px; }}
  .ui-report-content .locator {{ background: #f8f9fa; padding: 2px 6px; border-radius: 4px;
                                  font-size: 11px; color: #495057; border: 1px solid #dee2e6;
                                  font-family: Consolas,Monaco,monospace; display: inline-block;
                                  max-width: 300px; overflow: hidden; text-overflow: ellipsis;
                                  white-space: nowrap; }}
  .ui-report-content .steps {{ margin-top: 4px; }}
  .ui-report-content .steps > summary {{ cursor: pointer; font-size: 12px; color: #2563eb;
                                          font-weight: 600; }}
  .ui-report-content .steps-body {{ padding: 4px 0; }}
  .ui-report-content .steps table {{ width: 100%; border-collapse: collapse; font-size: 12px;
                                      margin-top: 4px; box-shadow: none; border-radius: 0; }}
  .ui-report-content .steps th {{ background: #f8f9fa; padding: 5px 7px; text-align: left;
                                   border-bottom: 2px solid #dee2e6; white-space: nowrap; }}
  .ui-report-content .steps td {{ padding: 4px 7px; border-bottom: 1px solid #eee;
                                   vertical-align: top; word-break: break-all; }}
  .ui-report-content .steps tr.fail-row {{ background: #fef0f0; }}
  .ui-report-content .error-msg {{ color: #e74c3c; font-size: 11px; margin-top: 4px;
                                    white-space: pre-wrap; }}
  .ui-report-content .failure-info {{ color: #c0392b; font-size: 11px; margin-top: 4px; }}
  .ui-report-content .failure-label {{ font-weight: 600; }}
  .ui-report-content .note-info {{ color: #f39c12; font-size: 11px; margin-top: 4px; }}
  .ui-report-content .note-label {{ font-weight: 600; }}
  .ui-report-content .expected-info {{ color: #7f8c8d; font-size: 11px; margin-top: 4px; }}
  .ui-report-content .expected-label {{ font-weight: 600; }}
  .lightbox {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
               background: rgba(0,0,0,.85); z-index: 9999; align-items: center;
               justify-content: center; cursor: zoom-out; }}
  .lightbox.show {{ display: flex; }}
  .lightbox img {{ max-width: 92vw; max-height: 92vh; border: 2px solid #fff;
                   box-shadow: 0 8px 32px rgba(0,0,0,.5); }}
  .footer {{ text-align: center; color: #9ca3af; font-size: 12px; padding: 30px 0; }}
  .no-data {{ color: #9ca3af; font-style: italic; padding: 8px 0; }}
</style>
</head>
<body>
<div class="header">
  <h1>{project_dir.name} UI 汇总报告</h1>
  <div class="sub">生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} · 版本：{version}</div>
</div>
<div class="wrap">
  <div class="section-title">模块概览</div>
  <div class="overview">
    <div class="stat"><b>{total}</b><span>总模块数</span></div>
    <div class="stat info"><b>{ui_script_count}</b><span>UI 脚本数</span></div>
    <div class="stat info"><b>{ui_report_count}</b><span>已生成报告</span></div>
  </div>

  <div class="section-title">UI 操作统计</div>
  <div class="overview">
    <div class="stat info"><b>{global_total}</b><span>总操作数</span></div>
    <div class="stat ok"><b>{global_passed}</b><span>通过</span></div>
    <div class="stat fail"><b>{global_failed}</b><span>失败</span></div>
    <div class="stat info"><b>{global_pass_rate}</b><span>操作通过率</span></div>
  </div>
"""

    # 按 group 输出模块
    for group_name in sorted(grouped_modules.keys()):
        html += f"""  <div class="group">
    <div class="group-head">
      <h2>📂 {group_name}</h2>
    </div>
"""

        for module in grouped_modules[group_name]:
            module_name = module["name"]

            # 从 JSONL 读取
            ui_log_data = module_ui_logs.get(module_name)

            if ui_log_data:
                # JSONL 数据源（与 API 报告一致）
                operations = ui_log_data.get("operations", [])
                results_html = _render_ui_results_html(operations, ui_log_dir)

                html += f"""    <div class="module-section" id="ui-module-{module_name}">
      <h3 class="module-title">{module_name}</h3>
      <div class="module-stats">
        <span>总操作: <b>{ui_log_data["total"]}</b></span>
        <span>通过: <b>{ui_log_data["passed"]}</b></span>
        <span>失败: <b>{ui_log_data["failed"]}</b></span>
        <span>跳过: <b>{ui_log_data["skipped"]}</b></span>
        <span>通过率: <b>{ui_log_data["pass_rate"]}</b></span>
      </div>
      <div class="ui-report-content">
        {results_html}
      </div>
    </div>
"""
            else:
                # 无报告
                reason = module.get("reason", "")
                html += f"""    <div class="module-section">
      <h3 class="module-title">{module_name}</h3>
      <p class="no-data">暂无 UI 测试报告{f'（{reason}）' if reason else ''}</p>
    </div>
"""

        html += """  </div>
"""

    html += f"""</div>
<div class="lightbox" id="lightbox" onclick="hideLightbox()">
  <img id="lightbox-img" src="" alt="截图">
</div>
<script>
function showLightbox(src) {{
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('show');
}}
function hideLightbox() {{
  document.getElementById('lightbox').classList.remove('show');
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') hideLightbox();
}});
</script>
<div class="footer">API_AI_test 汇总报告 · {project_dir.name} · {version}</div>
</body>
</html>"""

    report_path.write_text(html, encoding="utf-8")
    LOG.info(f"  📊 UI 汇总报告已生成: {report_path}")
    return str(report_path)


def generate_all_summaries(project_dir: Path, version: str):
    """生成所有汇总报告（API + UI）。

    Args:
        project_dir: 项目目录
        version: 版本号
    """
    LOG.info(f"\n{'='*60}")
    LOG.info(f"  生成汇总报告")
    LOG.info(f"{'='*60}")

    api_report = generate_api_summary(project_dir, version)
    ui_report = generate_ui_summary(project_dir, version)

    return api_report, ui_report
