"""UI 测试报告生成模块"""
import base64
from datetime import datetime


def _to_base64_str(data):
    """将截图数据统一转为 base64 字符串。

    支持输入：bytes / bytearray / str（已是 base64）
    """
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return base64.b64encode(bytes(data)).decode("ascii")
    # 已经是字符串
    return str(data)


def generate_ui_report(results, module_name):
    """生成 UI 测试报告（HTML 格式）

    Args:
        results: 测试结果列表，每项包含:
            - operation: 操作标识 (create/update/...)
            - display_name: 业务名称 (创建用户/编辑/...)
            - status: 状态 (passed/failed/skipped)
            - duration: 耗时(秒)
            - steps: 步骤列表 (可选)
            - error: 错误信息 (可选)
            - screenshot: 截图数据 (bytes 或 base64 str)
        module_name: 模块名称

    Returns:
        报告文件路径
    """
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "passed")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = total - passed - skipped
    exec_total = passed + failed
    pass_rate = f"{(passed / exec_total * 100) if exec_total > 0 else 0:.1f}%"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_ok = failed == 0 and exec_total > 0
    icon = "✅" if all_ok else "❌"

    # HTML 转义
    def esc(s):
        if s is None:
            return ""
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    # 构建步骤详情
    def build_steps_html(steps):
        if not steps:
            return ""
        rows = []
        for i, s in enumerate(steps, 1):
            action = esc(s.get("action", ""))
            status = s.get("status", "")
            badge_cls = "badge-ok" if status == "passed" else "badge-fail" if status == "failed" else "badge-skip"
            status_text = "通过" if status == "passed" else "失败" if status == "failed" else "跳过"
            row_cls = ' class="fail-row"' if status == "failed" else ""

            # Locator
            locator = esc(s.get("locator", ""))
            locator_html = f'<code class="locator">{locator}</code>' if locator else '<span class="muted">-</span>'

            # 描述
            description = esc(s.get("description", ""))

            # 测试数据
            fields = s.get("fields", [])
            if fields:
                # 表单字段列表
                field_rows = []
                for f in fields:
                    label = esc(f.get("label", ""))
                    f_locator = esc(f.get("locator", ""))
                    f_type = esc(f.get("type", ""))
                    test_data = esc(f.get("test_data", ""))
                    field_rows.append(
                        f'<tr><td>{label}</td><td><code class="locator">{f_locator}</code></td>'
                        f'<td>{f_type}</td><td>{test_data}</td></tr>'
                    )
                fields_html = (
                    f'<details class="fields-detail"><summary>表单字段 ({len(fields)})</summary>'
                    f'<table class="fields-table"><thead><tr><th>标签</th><th>Locator</th>'
                    f'<th>类型</th><th>测试数据</th></tr></thead><tbody>{"".join(field_rows)}</tbody></table></details>'
                )
            else:
                fields_html = ""

            rows.append(
                f'<tr{row_cls}><td>{i}</td><td>{action}</td><td>{locator_html}</td>'
                f'<td>{description}{fields_html}</td><td><span class="badge {badge_cls}">{status_text}</span></td></tr>'
            )
        return (
            f'<details class="steps"><summary>执行步骤 ({len(steps)})</summary>'
            f'<div class="steps-body"><table><thead><tr><th>#</th><th>操作</th><th>Locator</th>'
            f'<th>说明</th><th>结果</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>'
        )

    # 构建截图单元格
    def build_screenshot_cell(r):
        raw = r.get("screenshot")
        if not raw:
            return '<td><span class="muted">无截图</span></td>'
        b64 = _to_base64_str(raw)
        return (
            '<td><a href="data:image/png;base64,' + b64 + '" class="screenshot-link" '
            'onclick="showLightbox(this.href); return false;">'
            '<img src="data:image/png;base64,' + b64 + '" class="screenshot-thumb"></a></td>'
        )

    # 构建结果行
    def build_result_row(i, r):
        # 操作列：优先显示业务名称，括号内显示技术标识
        display_name = esc(r.get("display_name", ""))
        op_key = esc(r.get("operation", ""))
        if display_name and display_name != op_key:
            operation = f'{display_name}<br><span class="op-key">{op_key}</span>'
        else:
            operation = op_key

        status = r.get("status", "unknown")
        duration = r.get("duration", 0)
        steps = r.get("steps", [])
        error = r.get("error", "")

        badge_cls = "badge-ok" if status == "passed" else "badge-fail" if status == "failed" else "badge-skip"
        status_text = "通过" if status == "passed" else "失败" if status == "failed" else "跳过"
        row_cls = "ok" if status == "passed" else "fail" if status == "failed" else "skip"

        steps_html = build_steps_html(steps)
        screenshot_html = build_screenshot_cell(r)
        error_html = f'<div class="error-msg">{esc(error)}</div>' if error else ""

        return f'''<tr class="{row_cls}">
<td>{i}</td>
<td>{operation}</td>
<td>{duration:.2f}s</td>
<td><span class="badge {badge_cls}">{status_text}</span></td>
{screenshot_html}
<td>{steps_html}{error_html}</td>
</tr>'''

    rows_html = "\n".join(build_result_row(i, r) for i, r in enumerate(results, 1))

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{esc(module_name)} · UI 测试报告</title>
<style>
*{{box-sizing:border-box;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
body{{background:#f5f6fa;color:#2c3e50;padding:20px 28px;line-height:1.55}}
.header{{background:linear-gradient(135deg,#2c3e50,#3498db);color:#fff;padding:20px 26px;border-radius:8px;margin-bottom:16px}}
.header h1{{font-size:20px;margin-bottom:6px}}
.header p{{opacity:.85;font-size:13px}}
.summary{{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}}
.card{{background:#fff;padding:12px 20px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08);text-align:center;min-width:96px}}
.card .num{{font-size:24px;font-weight:700}}
.card .label{{font-size:12px;color:#7f8c8d}}
.card.ok .num{{color:#27ae60}}
.card.fail .num{{color:#e74c3c}}
.card.warn .num{{color:#f39c12}}
.card.total .num{{color:#3498db}}
table{{width:100%;border-collapse:collapse;font-size:12px;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
th{{background:#f8f9fa;padding:8px 10px;text-align:left;border-bottom:2px solid #dee2e6;white-space:nowrap}}
td{{padding:7px 10px;border-bottom:1px solid #eee;vertical-align:top;word-break:break-all}}
tr.ok{{background:#f4fdf7}}
tr.fail{{background:#fef0f0}}
tr.skip{{background:#f8f9fa}}
.badge{{display:inline-block;padding:1px 9px;border-radius:10px;font-size:11px;font-weight:600;color:#fff}}
.badge.ok{{background:#27ae60}}
.badge.fail{{background:#e74c3c}}
.badge.skip{{background:#95a5a6}}
.badge-ok{{background:#d5f5e3;color:#27ae60;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600}}
.badge-fail{{background:#fadbd8;color:#e74c3c;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600}}
.badge-skip{{background:#f2f3f4;color:#95a5a6;padding:2px 8px;border-radius:8px;font-size:11px;font-weight:600}}
.op-key{{font-size:10px;color:#95a5a6;font-weight:400}}
.screenshot-thumb{{max-width:80px;max-height:60px;border:1px solid #ddd;border-radius:4px;cursor:pointer}}
.screenshot-link{{display:inline-block}}
.muted{{color:#95a5a6;font-size:11px}}
.locator{{background:#f8f9fa;padding:2px 6px;border-radius:4px;font-size:11px;color:#495057;border:1px solid #dee2e6;font-family:Consolas,Monaco,monospace;display:inline-block;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.steps{{margin-top:4px}}
.steps>summary{{cursor:pointer;font-size:12px;color:#2980b9;font-weight:600}}
.steps-body{{padding:4px 0}}
.steps table{{width:100%;border-collapse:collapse;font-size:12px;margin-top:4px;box-shadow:none;border-radius:0}}
.steps th{{background:#f8f9fa;padding:5px 7px;text-align:left;border-bottom:2px solid #dee2e6;white-space:nowrap}}
.steps td{{padding:4px 7px;border-bottom:1px solid #eee;vertical-align:top;word-break:break-all}}
.steps tr.fail-row{{background:#fef0f0}}
.fields-detail{{margin-top:4px}}
.fields-detail>summary{{cursor:pointer;font-size:11px;color:#7f8c8d;font-weight:500}}
.fields-table{{width:100%;border-collapse:collapse;font-size:11px;margin-top:4px;box-shadow:none;border-radius:0}}
.fields-table th{{background:#f8f9fa;padding:4px 6px;text-align:left;border-bottom:1px solid #dee2e6;font-weight:600;white-space:nowrap}}
.fields-table td{{padding:3px 6px;border-bottom:1px solid #eee;vertical-align:top;word-break:break-all}}
.fields-table .locator{{max-width:200px}}
.error-msg{{color:#e74c3c;font-size:11px;margin-top:4px;white-space:pre-wrap}}
.lightbox{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.85);z-index:9999;align-items:center;justify-content:center;cursor:zoom-out}}
.lightbox.show{{display:flex}}
.lightbox img{{max-width:92vw;max-height:92vh;border:2px solid #fff;box-shadow:0 8px 32px rgba(0,0,0,.5)}}
</style>
</head>
<body>
<div class="header">
<h1>{icon} {esc(module_name)} · UI 测试报告</h1>
<p>执行时间: {now_str} | 实际执行: {exec_total}/{total} (跳过 {skipped}) | 耗时: {sum(r.get('duration', 0) for r in results):.1f}s</p>
</div>
<div class="summary">
<div class="card total"><div class="num">{total}</div><div class="label">总数</div></div>
<div class="card ok"><div class="num">{passed}</div><div class="label">通过</div></div>
<div class="card fail"><div class="num">{failed}</div><div class="label">失败</div></div>
<div class="card warn"><div class="num">{skipped}</div><div class="label">跳过</div></div>
<div class="card warn"><div class="num">{pass_rate}</div><div class="label">通过率</div></div>
</div>
<table>
<thead><tr><th>#</th><th>操作</th><th>耗时</th><th>结果</th><th>截图</th><th>详情</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<p style="margin-top:12px;font-size:12px;color:#95a5a6">单文件自包含报告：结果截图（base64，点击可放大）与操作过程均已内嵌，可直接邮件分享。</p>
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
</body>
</html>'''

    from pathlib import Path
    # ui_report.py 在 lib/ 目录下，报告应该输出到父目录的 output/reports/
    script_dir = Path(__file__).parent.parent  # ui/ 目录
    report_dir = script_dir / "output" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = report_dir / f"{module_name}_ui_report_{ts}.html"
    report_file.write_text(html, encoding="utf-8")

    return str(report_file)
