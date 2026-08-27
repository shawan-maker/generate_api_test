# -*- coding: utf-8 -*-
"""日志 → HTML 报告转换器（可复用，Postman/Newman htmlextra 风格）。

把 module_discovery 生成的 API 测试脚本运行日志（report_*.log）解析为
自包含的单文件 HTML 报告（内联 CSS，无外部依赖），供分享/核对。
视觉参考 Postman / newman-reporter-htmlextra：
  - 顶部仪表盘：统计卡片（总用例/通过/警告/失败）+ 通过率环形图
  - 请求卡片列表：方法徽章（GET/POST/PUT/DELETE 配色）+ 路径 + 状态徽章
  - 可折叠详情（请求头/请求体/响应体分区展示，JSON 格式化）

用法（CLI）:
    python -m lib.report_html --log projects/ecm-compute/output/report_用户管理_xxx.log
    python -m lib.report_html --log xxx.log -o report.html

日志解析规则:
    1. 步骤头: 行首 `[步骤名] /api/path`（如 `[创建] /estack/api/.../users`）
    2. 步骤内状态行: `✅ ...` 成功 / `⚠️ ...` 警告 / `⏭️ ...` 跳过
    3. `>>> 请求` 和 `<<< 响应` 标记分隔请求/响应区块
    4. 区块内字段: `method:`, `url:`, `headers:`, `body:`, `status:`, `content-type:`, `success:`, `error:`
    5. 其余行为杂项细节
    6. 头部 `  XXX API 测试` 作为报告标题
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

STEP_RE = re.compile(r"^\[([^\]]+)\]\s+(\S.*)$")
HEADER_RE = re.compile(r"^\s{2}(\S.*?)\s*API\s*测试\s*$")
DONE_RE = re.compile(r"测试完成")
METHOD_RE = re.compile(r"'method'\s*:\s*'([A-Z]+)'")

METHOD_COLORS = {
    "GET": "#61affe", "POST": "#49cc90", "PUT": "#fca130",
    "DELETE": "#f93e3e", "PATCH": "#50e3c2", "HEAD": "#8a8a8a", "OPTIONS": "#8a8a8a",
}


def _infer_method(step: dict) -> str:
    """从步骤名推断 HTTP 方法。"""
    name = step.get("name", "")
    if any(k in name for k in ("创建", "新增", "注册", "锁定", "冻结", "重置", "reset", "授权", "authorize", "迁移")):
        return "POST"
    if any(k in name for k in ("修改", "编辑", "更新", "解锁", "启用")):
        return "PUT"
    if any(k in name for k in ("删除", "移除")):
        return "DELETE"
    if "查询" in name or "列表" in name or "操作" in name:
        return "GET"
    return ""


def _parse_structured_details(lines: list[str]) -> tuple[list[dict], list[str]]:
    """解析 gen_test.py 输出的结构化 >>> 请求 / <<< 响应 区块。

    一个步骤内可能有多个 API 调用，返回:
        (api_calls, misc_lines)
    其中 api_calls 是列表，每个元素:
        {"request": {"method": ..., "url": ..., "headers": {...}, "body": ...},
         "response": {"status": ..., "content_type": ..., "success": ..., "error": ..., "body": ...}}
    """
    api_calls = []
    current_api = None
    section = None  # "request" or "response" or None
    current_field = None
    current_value_lines = []
    misc_lines = []

    def flush_field():
        nonlocal current_field, current_value_lines
        if current_field and section and current_api is not None:
            val = "\n".join(current_value_lines).strip()
            current_api[section][current_field] = val
        current_field = None
        current_value_lines = []

    def flush_api():
        nonlocal current_api, section, current_field, current_value_lines
        flush_field()
        if current_api is not None:
            api_calls.append(current_api)
        current_api = None
        section = None

    for line in lines:
        stripped = line.strip()

        # 区块标记
        if stripped.startswith(">>>") and "请求" in stripped:
            flush_api()
            current_api = {"request": {}, "response": {}}
            section = "request"
            continue
        if stripped.startswith("<<<") and "响应" in stripped:
            flush_field()
            section = "response"
            continue

        # 字段行: `key: value`（可能多行，如 JSON body）
        field_match = re.match(r"^(method|url|headers|body|status|content-type|success|error):\s*(.*)",
                               stripped, re.IGNORECASE)
        if field_match and section and current_api is not None:
            flush_field()
            current_field = field_match.group(1).lower().replace("-", "_")
            current_value_lines = [field_match.group(2)]
            continue

        # 多行字段续行（缩进或 JSON 格式）
        if current_field and section and current_api is not None and (
            stripped.startswith("{") or stripped.startswith("[")
            or stripped.startswith('"') or stripped.startswith("}")
            or stripped.startswith("]") or stripped.startswith("  ")
        ):
            current_value_lines.append(stripped)
            continue

        # 不属于结构化区块的行
        flush_field()
        if stripped:
            misc_lines.append(stripped)

    flush_api()
    return api_calls, misc_lines


def parse_log(log_path: str) -> dict:
    """解析日志为结构化报告数据。"""
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    title = "API 测试报告"
    steps: list[dict] = []
    cur: dict | None = None

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        # 标题
        m = HEADER_RE.match(ln)
        if m and "SyntaxWarning" not in ln and "const " not in ln:
            title = f"{m.group(1)} API 测试报告"
            continue
        # 步骤头
        m = STEP_RE.match(ln)
        if m:
            cur = {"name": m.group(1).strip(), "api": m.group(2).strip(),
                   "marks": [], "details": []}
            steps.append(cur)
            continue
        if cur is None:
            continue
        if DONE_RE.search(ln):
            continue
        # 状态行 / 细节行
        if s.startswith("✅") or s.startswith("⚠️") or s.startswith("⏭️"):
            cur["marks"].append(s)
        else:
            cur["details"].append(ln.strip())

    # 计算每步状态、方法，并解析结构化详情
    for st in steps:
        ok = any(x.startswith("✅") for x in st["marks"])
        warn = any(x.startswith("⚠️") or x.startswith("⏭️") for x in st["marks"])
        fail = any(x.startswith("❌") for x in st["marks"])
        if fail:
            st["status"] = "fail"
        elif ok and warn:
            st["status"] = "warn"
        elif ok:
            st["status"] = "ok"
        elif warn:
            st["status"] = "warn"
        else:
            st["status"] = "info"

        # 解析结构化详情
        api_calls, misc_lines = _parse_structured_details(st["details"])
        st["api_calls"] = api_calls  # 列表，可能有多个 API 调用
        st["misc"] = misc_lines

        # 取第一个 API 调用作为主调用（用于向后兼容）
        if api_calls:
            st["req"] = api_calls[0]["request"]
            st["resp"] = api_calls[0]["response"]
        else:
            st["req"] = {}
            st["resp"] = {}

        # 方法：优先从结构化请求取，其次旧格式，最后推断
        st["method"] = (st["req"].get("method")
                        or next((METHOD_RE.search(d).group(1)
                                 for d in st["details"] if METHOD_RE.search(d)), None)
                        or _infer_method(st))

        # URL：优先从结构化请求取
        st["url"] = st["req"].get("url", st["api"])

    return {"title": title, "steps": steps, "source": str(log_path)}


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _try_format_json(s: str) -> str:
    """尝试格式化 JSON 字符串，失败则原样返回。"""
    if not s:
        return s
    try:
        parsed = json.loads(s)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return s


def _render_headers_table(headers_str: str) -> str:
    """将 headers JSON 渲染为 HTML 表格。"""
    if not headers_str:
        return ""
    try:
        headers = json.loads(headers_str)
        if not isinstance(headers, dict) or not headers:
            return ""
        rows = []
        for k, v in sorted(headers.items()):
            rows.append(f'<tr><td class="hk">{_esc(k)}</td><td class="hv">{_esc(str(v))}</td></tr>')
        return f'<table class="kv-table">{"".join(rows)}</table>'
    except (json.JSONDecodeError, TypeError):
        return f'<pre class="raw">{_esc(headers_str)}</pre>'


def _render_json_block(body_str: str) -> str:
    """渲染 JSON body 为高亮代码块。"""
    if not body_str:
        return ""
    formatted = _try_format_json(body_str)
    return f'<pre class="json-block">{_esc(formatted)}</pre>'


def render_html(data: dict) -> str:
    """渲染 Postman/Newman htmlextra 风格 HTML。"""
    steps = data["steps"]
    total = len(steps)
    n_ok = sum(1 for s in steps if s["status"] == "ok")
    n_warn = sum(1 for s in steps if s["status"] == "warn")
    n_info = sum(1 for s in steps if s["status"] == "info")
    n_fail = sum(1 for s in steps if s["status"] == "fail")
    passed = n_ok + n_warn
    pct = round(passed / total * 100) if total else 0
    donut_color = "#fca130" if n_warn else ("#49cc90" if passed else "#f93e3e")

    status_meta = {
        "ok": ("✅", "通过", "ok"),
        "warn": ("⚠️", "警告/跳过", "warn"),
        "info": ("ℹ️", "信息", "info"),
        "fail": ("❌", "失败", "fail"),
    }

    cards = []
    for i, st in enumerate(steps, 1):
        icon, label, st_cls = status_meta[st["status"]]
        method = (st.get("method") or "").upper()
        mcolor = METHOD_COLORS.get(method, "#8a8a8a")

        # 状态标记行
        marks_html = "<br>".join(
            f'<div class="mark"><span class="mark-ic">{_esc(x[:2])}</span>{_esc(x[2:])}</div>'
            for x in st["marks"]) or '<div class="mark dim">无断言/状态行</div>'

        # 结构化详情面板 - 支持多个 API 调用
        api_calls = st.get("api_calls", [])
        misc = st.get("misc", [])

        detail_parts = []

        # 渲染每个 API 调用
        for idx, api in enumerate(api_calls):
            req = api.get("request", {})
            resp = api.get("response", {})

            # 如果有多个 API，显示序号和 URL
            if len(api_calls) > 1:
                api_url = req.get("url", "未知")
                api_method = req.get("method", "?")
                detail_parts.append(f'<div class="api-call-header"><strong>API #{idx+1}:</strong> '
                                    f'<span class="method-badge">{api_method}</span> {_esc(api_url)}</div>')

            # 请求区块
            req_content = []
            if req.get("headers"):
                req_content.append(f'<div class="section-label">Request Headers</div>')
                req_content.append(_render_headers_table(req["headers"]))
            if req.get("body"):
                req_content.append(f'<div class="section-label">Request Body</div>')
                req_content.append(_render_json_block(req["body"]))
            if req_content:
                detail_parts.append(f'''<div class="detail-section">
                    <div class="detail-title">📤 请求详情</div>
                    {"".join(req_content)}
                </div>''')

            # 响应区块
            resp_content = []
            if resp.get("status"):
                resp_content.append(f'<div class="section-label">Response Status</div>')
                status_val = resp["status"]
                success_val = resp.get("success", "")
                error_val = resp.get("error", "")
                resp_content.append(f'<div class="resp-status">HTTP {status_val}'
                                    + (f' · success={success_val}' if success_val and success_val != "-" else "")
                                    + (f' · <span class="err-text">error: {error_val}</span>' if error_val else "")
                                    + '</div>')
            if resp.get("body"):
                resp_content.append(f'<div class="section-label">Response Body</div>')
                resp_content.append(_render_json_block(resp["body"]))
            if resp_content:
                detail_parts.append(f'''<div class="detail-section">
                    <div class="detail-title">📥 响应详情</div>
                    {"".join(resp_content)}
                </div>''')

        # 杂项（浏览器操作日志等）
        if misc:
            misc_html = "<br>".join(_esc(x) for x in misc[:30])
            if len(misc) > 30:
                misc_html += f"<br><em>… 其余 {len(misc) - 30} 行见原日志</em>"
            detail_parts.append(f'''<div class="detail-section">
                <div class="detail-title">📋 其他日志</div>
                <pre class="raw">{misc_html}</pre>
            </div>''')

        # 兼容旧格式（无结构化数据时显示原始 details）
        if not detail_parts and st.get("details"):
            raw_html = "<br>".join(_esc(x) for x in st["details"][:40])
            if len(st["details"]) > 40:
                raw_html += f"<br><em>… 其余 {len(st['details']) - 40} 行见原日志</em>"
            detail_parts.append(f'<pre class="raw">{raw_html}</pre>')

        if not detail_parts:
            detail_parts.append('<em class="dim">该步骤无更多请求/响应细节</em>')

        detail_count = len(st.get("details", []))
        detail_inner = "\n".join(detail_parts)

        cards.append(f"""
        <div class="req {st_cls}">
          <div class="req-head">
            <span class="req-no">{i}</span>
            <span class="method" style="background:{mcolor}">{_esc(method or 'API')}</span>
            <code class="path">{_esc(st.get('url', st['api']))}</code>
            <span class="badge b-{st_cls}">{icon} {label}</span>
          </div>
          <div class="req-body">
            <div class="marks">{marks_html}</div>
            <details class="detail"><summary>查看请求/响应详情（{detail_count} 行）</summary>
              {detail_inner}
            </details>
          </div>
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{_esc(data['title'])}</title>
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
            background: conic-gradient({donut_color} 0 {pct}%, #e5e7eb {pct}% 100%);
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
  .mark.dim {{ color: #9ca3af; }}
  .detail {{ margin-top: 8px; }}
  .detail summary {{ cursor: pointer; font-size: 12.5px; color: #6b7280; user-select: none; }}
  .detail summary:hover {{ color: #2563eb; }}
  .detail-section {{ margin-top: 12px; padding: 12px 14px; background: #f8f9fb;
                     border: 1px solid #eceff3; border-radius: 8px; }}
  .api-call-header {{ margin: 16px 0 8px 0; padding: 8px 12px; background: #eef2f7;
                      border-radius: 6px; font-size: 13px; }}
  .api-call-header:first-child {{ margin-top: 0; }}
  .method-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px;
                   font-weight: 700; color: #fff; background: #4a6da7; margin-right: 8px; }}
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
  .resp-status .err-text {{ color: #dc2626; font-weight: 500; }}
  .raw {{ margin-top: 8px; background: #f8f9fb; border: 1px solid #eceff3; border-radius: 6px;
          padding: 12px; font-size: 12px; line-height: 1.7; white-space: pre-wrap;
          word-break: break-all; color: #45546b; max-height: 260px; overflow: auto; }}
  .dim {{ color: #9ca3af; }}
  .footer {{ text-align: center; color: #9ca3af; font-size: 12px; padding: 22px 0 30px; }}
  .legend {{ text-align: center; color: #6b7280; font-size: 12px; margin-bottom: 14px; }}
</style>
</head>
<body>
<div class="header">
  <div class="wrap">
    <h1>{_esc(data['title'])}</h1>
    <div class="sub">生成时间：{_esc(data.get('generated_at', ''))} &nbsp;·&nbsp; 日志来源：<code>{_esc(data['source'])}</code></div>
  </div>
</div>

<div class="wrap">
  <div class="stats">
    <div class="stat total"><b>{total}</b><span>总请求/用例</span></div>
    <div class="stat ok"><b>{n_ok}</b><span>通过</span></div>
    <div class="stat warn"><b>{n_warn}</b><span>警告/跳过</span></div>
    <div class="stat fail"><b>{n_fail}</b><span>失败</span></div>
    <div class="stat donut-card">
      <div class="donut-wrap"><div class="donut"></div><div class="donut-val">{pct}%</div></div>
      <div class="donut-txt">通过率<br>（含警告）</div>
    </div>
  </div>

  <div class="legend">共 {total} 个 API 调用 · 通过 {passed} · 通过率 {pct}%</div>
  <div class="requests">{''.join(cards)}</div>
  <div class="footer">Postman/Newman 风格报告 · 由 lib/report_html.py 生成 · API_AI_test</div>
</div>
</body>
</html>"""


def make_report(log_path: str, out_path: str | None = None) -> str:
    """解析日志并生成 HTML，返回输出路径。"""
    data = parse_log(log_path)
    import datetime
    data["generated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if out_path is None:
        src = Path(log_path)
        reports_dir = Path(__file__).resolve().parents[1] / "output" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = str(reports_dir / (src.stem + ".html"))
    Path(out_path).write_text(render_html(data), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="日志 → HTML 报告（Postman 风格）")
    ap.add_argument("--log", required=True, help="运行日志文件 (report_*.log)")
    ap.add_argument("-o", "--out", default=None, help="输出 HTML 路径（默认与日志同名 .html）")
    args = ap.parse_args(argv)
    out = make_report(args.log, args.out)
    print(f"HTML 报告已生成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
