"""
test_report.py — Postman/Newman 风格测试报告生成器

从 JSONL 日志文件解析 API 调用记录，生成可视化 HTML 报告。
报告输出到 output/reports/{module_name}/ 目录。
"""

import json
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "output" / "logs"
REPORT_ROOT = PROJECT_ROOT / "output" / "reports"


def parse_jsonl_log(log_file: str) -> list:
    """解析 JSONL 日志文件，提取 API 调用序列。

    Args:
        log_file: JSONL 日志文件路径

    Returns:
        API 调用记录列表
    """
    api_calls = []
    events = []

    if not Path(log_file).exists():
        return api_calls

    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("type") == "api_call":
                    api_calls.append(entry)
                else:
                    events.append(entry)
            except json.JSONDecodeError:
                continue

    return api_calls, events


def run_script_and_capture(script_path: str) -> tuple:
    """运行测试脚本并返回日志文件路径。

    Args:
        script_path: 测试脚本路径

    Returns:
        (log_file_path, module_name) 元组
    """
    script_path = Path(script_path)
    module_name = script_path.stem.replace("_API测试", "")

    # 运行脚本
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        encoding="utf-8"
    )

    # 日志文件路径
    log_file = LOG_DIR / f"{module_name}_API测试.jsonl"

    return str(log_file), module_name


def generate_postman_report(api_calls: list, events: list, module_name: str) -> str:
    """生成 Postman/Newman 风格 HTML 报告。

    Args:
        api_calls: API 调用记录列表
        events: 事件记录列表
        module_name: 模块名称

    Returns:
        报告文件路径
    """
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    report_dir = REPORT_ROOT / module_name
    report_dir.mkdir(parents=True, exist_ok=True)

    # 统计
    total = len(api_calls)
    passed = sum(1 for c in api_calls if c.get("assertion") == "passed")
    failed = total - passed
    pass_rate = f"{passed / total * 100:.1f}%" if total > 0 else "0%"

    # 时间戳
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = report_dir / f"{module_name}_report_{timestamp}.html"

    # 生成 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{module_name} API 测试报告</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: "SF Pro Text", "Helvetica Neue", "Microsoft YaHei", "PingFang SC", sans-serif;
         background: #f4f5f7; color: #1f2937; }}
  .header {{ background: linear-gradient(135deg, #1f2937 0%, #111827 100%); color: #fff;
             padding: 26px 32px 22px; }}
  .header .wrap {{ max-width: 1080px; margin: 0 auto; }}
  .header h1 {{ font-size: 21px; font-weight: 700; letter-spacing: .3px; }}
  .header .sub {{ color: #9ca3af; font-size: 12.5px; margin-top: 6px; line-height: 1.7; }}
  .header .sub code {{ color: #d1d5db; background: rgba(255,255,255,.08); padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 0 20px; }}
  .stats {{ display: flex; gap: 16px; margin: -26px auto 22px; position: relative; z-index: 2; flex-wrap: wrap; }}
  .stat {{ flex: 1; min-width: 150px; background: #fff; border-radius: 10px; padding: 16px 18px;
           box-shadow: 0 1px 3px rgba(0,0,0,.08); border-top: 3px solid #e5e7eb; }}
  .stat.ok {{ border-top-color: #49cc90; }} .stat.warn {{ border-top-color: #fca130; }}
  .stat.fail {{ border-top-color: #f93e3e; }} .stat.total {{ border-top-color: #61affe; }}
  .stat b {{ font-size: 26px; display: block; line-height: 1.2; }}
  .stat span {{ font-size: 12px; color: #6b7280; }}
  .stat.ok b {{ color: #1a7f37; }} .stat.warn b {{ color: #b45309; }}
  .stat.fail b {{ color: #dc2626; }} .stat.total b {{ color: #2563eb; }}
  .donut-card {{ display: flex; align-items: center; gap: 16px; }}
  .donut {{ width: 76px; height: 76px; border-radius: 50%; flex: none;
            background: conic-gradient(#49cc90 0 {pass_rate}, #e5e7eb {pass_rate} 100%);
            display: grid; place-items: center; }}
  .donut::before {{ content: ''; width: 56px; height: 56px; border-radius: 50%; background: #fff; }}
  .donut-val {{ position: absolute; font-size: 17px; font-weight: 700; }}
  .donut-wrap {{ position: relative; }}
  .donut-txt {{ font-size: 12px; color: #6b7280; line-height: 1.6; }}
  .requests {{ margin-top: 4px; }}
  .req {{ background: #fff; border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.07);
          margin-bottom: 12px; overflow: hidden; }}
  .req-head {{ display: flex; align-items: center; gap: 12px; padding: 13px 18px; flex-wrap: wrap;
               border-bottom: 1px solid #f0f1f3; }}
  .req-no {{ width: 24px; height: 24px; border-radius: 50%; background: #eef2f7; color: #4a6da7;
             display: inline-flex; align-items: center; justify-content: center; font-size: 12px;
             font-weight: 700; flex: none; }}
  .method {{ color: #fff; font-size: 11px; font-weight: 700; letter-spacing: .5px; padding: 3px 10px;
             border-radius: 4px; flex: none; }}
  .path {{ font-family: "SF Mono", Consolas, monospace; font-size: 12.5px; color: #374151;
           word-break: break-all; flex: 1; min-width: 180px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: 12px;
            font-weight: 600; flex: none; }}
  .b-ok {{ background: #e8f7ee; color: #1a7f37; }}
  .b-warn {{ background: #fff4e5; color: #b45309; }}
  .b-info {{ background: #eef2f7; color: #4a6da7; }}
  .b-fail {{ background: #fdeaea; color: #dc2626; }}
  .req-body {{ padding: 12px 18px 14px; }}
  .mark {{ font-size: 13px; line-height: 1.9; color: #374151; }}
  .mark .mark-ic {{ margin-right: 8px; }}
  .detail {{ margin-top: 8px; }}
  .detail summary {{ cursor: pointer; font-size: 12.5px; color: #6b7280; user-select: none; }}
  .detail summary:hover {{ color: #2563eb; }}
  .detail-section {{ margin-top: 12px; padding: 12px 14px; background: #f8f9fb;
                     border: 1px solid #eceff3; border-radius: 8px; }}
  .detail-title {{ font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 8px; }}
  .section-label {{ font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase;
                    letter-spacing: .5px; margin: 10px 0 4px; padding-bottom: 4px;
                    border-bottom: 1px solid #e5e7eb; }}
  .section-label:first-child {{ margin-top: 0; }}
  .kv-table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin: 4px 0; }}
  .kv-table td {{ padding: 4px 8px; border-bottom: 1px solid #eee; vertical-align: top; }}
  .kv-table .hk {{ font-weight: 600; color: #4a6da7; white-space: nowrap; width: 160px; }}
  .kv-table .hv {{ color: #374151; word-break: break-all; font-family: "SF Mono", Consolas, monospace;
                   font-size: 11.5px; }}
  .json-block {{ margin: 4px 0; background: #1e293b; color: #e2e8f0; border-radius: 6px;
                 padding: 12px; font-size: 12px; line-height: 1.6; white-space: pre-wrap;
                 word-break: break-all; max-height: 320px; overflow: auto;
                 font-family: "SF Mono", Consolas, monospace; }}
  .resp-status {{ font-size: 13px; font-weight: 600; color: #374151; padding: 4px 0; }}
  .footer {{ text-align: center; color: #9ca3af; font-size: 12px; padding: 22px 0 30px; }}
  .legend {{ text-align: center; color: #6b7280; font-size: 12px; margin-bottom: 14px; }}
</style>
</head>
<body>
<div class="header">
  <div class="wrap">
    <h1>{module_name} API 测试报告</h1>
    <div class="sub">生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &nbsp;·&nbsp; 日志来源：<code>output/logs/{module_name}_API测试.jsonl</code></div>
  </div>
</div>

<div class="wrap">
  <div class="stats">
    <div class="stat total"><b>{total}</b><span>总请求数</span></div>
    <div class="stat ok"><b>{passed}</b><span>通过</span></div>
    <div class="stat fail"><b>{failed}</b><span>失败</span></div>
    <div class="stat donut-card">
      <div class="donut-wrap"><div class="donut"></div><div class="donut-val">{pass_rate}</div></div>
      <div class="donut-txt">通过率</div>
    </div>
  </div>

  <div class="legend">共 {total} 个 API 调用 · 通过 {passed} · 通过率 {pass_rate}</div>
  <div class="requests">
"""

    # 生成每个 API 调用的卡片
    method_colors = {
        "GET": "#61affe",
        "POST": "#49cc90",
        "PUT": "#fca130",
        "DELETE": "#f93e3e",
        "PATCH": "#50e3c2",
    }

    for i, call in enumerate(api_calls, 1):
        method = call["request"]["method"]
        url = call["request"]["url"]
        status = call["response"]["status"]
        assertion = call.get("assertion", "pending")
        label = call.get("step_label", "")

        # 判断是否成功
        is_ok = assertion == "passed" or (status >= 200 and status < 300)
        badge_class = "b-ok" if is_ok else "b-fail"
        badge_text = "✅ 通过" if is_ok else "❌ 失败"

        method_color = method_colors.get(method, "#6b7280")

        html += f"""    <div class="req {'ok' if is_ok else 'fail'}">
      <div class="req-head">
        <span class="req-no">{i}</span>
        <span class="method" style="background:{method_color}">{method}</span>
        <code class="path">{url}</code>
        <span class="badge {badge_class}">{badge_text}</span>
      </div>
      <div class="req-body">
        <div class="marks"><div class="mark"><span class="mark-ic">{'✅' if is_ok else '❌'}</span>{label}: HTTP {status}</div></div>
        <details class="detail"><summary>查看请求/响应详情</summary>
          <div class="detail-section">
            <div class="detail-title">📤 请求详情</div>
            <div class="section-label">Request Headers</div>
            <table class="kv-table">
"""

        # 请求头
        req_headers = call["request"].get("headers", {})
        for k, v in req_headers.items():
            html += f'              <tr><td class="hk">{k}</td><td class="hv">{v}</td></tr>\n'

        html += """            </table>
"""

        # 请求体
        req_body = call["request"].get("body")
        if req_body:
            html += '            <div class="section-label">Request Body</div>\n'
            html += f'            <pre class="json-block">{json.dumps(req_body, ensure_ascii=False, indent=2)}</pre>\n'

        html += """          </div>
<div class="detail-section">
            <div class="detail-title">📥 响应详情</div>
"""

        # 响应状态
        html += f'            <div class="section-label">Response Status</div>\n'
        html += f'            <div class="resp-status">HTTP {status}</div>\n'

        # 响应体
        resp_body = call["response"].get("body")
        if resp_body:
            html += '            <div class="section-label">Response Body</div>\n'
            html += f'            <pre class="json-block">{json.dumps(resp_body, ensure_ascii=False, indent=2)}</pre>\n'

        html += """          </div>
        </details>
      </div>
    </div>
"""

    html += f"""  </div>
  <div class="footer">Postman/Newman 风格报告 · 由 lib/test_report.py 生成 · API_AI_test</div>
</div>
</body>
</html>"""

    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html)

    return str(report_file)


def main():
    """主入口：运行脚本并生成报告。"""
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    if len(sys.argv) < 2:
        print("用法: python test_report.py <script_path>")
        print("示例: python test_report.py projects/ecm-compute/scripts/v1.0.0/api/角色管理_API测试.py")
        sys.exit(1)

    script_path = sys.argv[1]

    # 运行脚本并获取日志路径
    log_file, module_name = run_script_and_capture(script_path)

    # 解析日志
    api_calls, events = parse_jsonl_log(log_file)

    if not api_calls:
        print(f"❌ 未找到 API 调用记录: {log_file}")
        sys.exit(1)

    # 生成报告
    report_file = generate_postman_report(api_calls, events, module_name)

    print(f"✅ 报告已生成: {report_file}")
    print(f"   模块: {module_name}")
    print(f"   API 调用数: {len(api_calls)}")


if __name__ == "__main__":
    main()
