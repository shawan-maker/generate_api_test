"""
run_history.py - 历史运行结果记录与失败分类（对齐 EcsCloud run_history.js）

设计目标：
  把每次批跑结果沉淀下来，使后续运行能判断「哪些是一直失败的环境问题、
  哪些是脚本问题、哪些是产品缺陷」。

数据落盘（均在 projects/<id>/output/config/）：
  - run_history.json: 追加式运行记录数组，每条含本次全部用例结果
  - failure_ledger.json: 按用例 ID 聚合的失败台账（跨多次运行累计）
  - failure_overrides.json: 人工校正的根因（环境/脚本/产品），优先于自动正则

失败归因五分法（谁负责修）：
  - env: 环境限制（资源未开通/配额耗尽/网络/依赖前置资源缺失）→ 运维/测试侧补资源，不打扰开发
  - script: 测试脚本自身 bug（选择器/等待/流程/按钮文案错）→ 测试侧修脚本
  - product: 产品代码问题/缺陷（后端 5xx、按钮该出现却没出现、表单校验误拒、列表查询报错）
             → 被测功能本身坏了，必须提报开发，不能靠改测试脚本去"兼容"
  - flaky: 偶发/抖动（有时过有时挂）
  - unknown: 无法自动定性，需人工复核

自动分类规则（classify）：
  1) 若 failure_overrides.json 中存在该 ID → 直接用其 category（最准，产品缺陷靠人工定性）
  2) 否则按 errMsg 正则：env / product / script / flaky / unknown
     （env 优先于 product，避免"文件存储"等 env 关键词把后端 5xx 误判为脚本）

用法：
  from lib.run_history import record_run, print_summary
  record_run(summary)
  print_summary(ledger)
"""

import json
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


# ---------------- 分类正则 ----------------
ENV_RE = re.compile(
    r'配额|quota|未开通|证书配额|资源不足|欠费|余额|第二项目|跨项目|文件存储|服务未开通|'
    r'环境限制|可订购配额为\s*0|无法下单|无法构造|未开通订购',
    re.IGNORECASE
)

PRODUCT_RE = re.compile(
    r'服务端(异常|错误)|系统异常|后台报错|接口.*(报错|失败|异常)|API.*(error|fail)|'
    r'业务异常|服务内部错误|网关错误|502|503|504|panic|NullPointer|产品缺陷|代码问题|'
    r'秘密创建失败|该服务|服务故障',
    re.IGNORECASE
)

SCRIPT_RE = re.compile(
    r'Assignment to constant|重新赋值|定位失败|未出现.*按钮|Cannot read|TypeError|'
    r'ReferenceError|is not (?:a )?function|is not defined|SyntaxError|选择器|selector|'
    r'脚本错误|await.*timeout|超时.*wait|Timeout',
    re.IGNORECASE
)

FLAKY_RE = re.compile(r'偶发|偶爾|flaky|网络波动|波动', re.IGNORECASE)

OVERRIDE_EXPIRY_DAYS = 90


def load_json(file_path: Path, default: Any = None):
    """加载 JSON 文件，失败返回默认值。"""
    if default is None:
        default = {} if file_path.name.endswith('.json') and 'ledger' in file_path.name else []
    try:
        if file_path.exists():
            return json.loads(file_path.read_text('utf-8'))
        return default
    except Exception:
        return default


def load_history(project_dir: Path) -> List[Dict]:
    """加载运行历史。"""
    config_dir = project_dir / 'output' / 'config'
    return load_json(config_dir / 'run_history.json', [])


def load_ledger(project_dir: Path) -> Dict:
    """加载失败台账。"""
    config_dir = project_dir / 'output' / 'config'
    return load_json(config_dir / 'failure_ledger.json', {})


def load_overrides(project_dir: Path) -> Dict:
    """加载人工根因校正。"""
    config_dir = project_dir / 'output' / 'config'
    return load_json(config_dir / 'failure_overrides.json', {})


def classify(err_msg: str, case_id: str, overrides: Dict) -> str:
    """
    分类失败用例。

    优先级：
      1. failure_overrides.json 人工定性（最准）
      2. 正则匹配：env → product → script → flaky → unknown
    """
    if case_id in overrides and 'category' in overrides[case_id]:
        return overrides[case_id]['category']

    msg = str(err_msg or '')

    if ENV_RE.search(msg):
        return 'env'
    if PRODUCT_RE.search(msg):
        return 'product'
    if SCRIPT_RE.search(msg):
        return 'script'
    if FLAKY_RE.search(msg):
        return 'flaky'

    return 'unknown'


def check_expired_overrides(project_dir: Path) -> List[Dict]:
    """检查过期的 override（超过 90 天）。"""
    overrides = load_overrides(project_dir)
    now = datetime.now()
    expired = []

    for case_id, entry in overrides.items():
        created_at = entry.get('createdAt')
        if not created_at:
            continue

        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age_days = (now - created).days
            if age_days > OVERRIDE_EXPIRY_DAYS:
                expired.append({
                    'id': case_id,
                    'category': entry.get('category', ''),
                    'ageDays': age_days,
                    'note': entry.get('note', '')[:60]
                })
        except Exception:
            continue

    if expired:
        print(f"\n⚠️  Override 过期提醒：{len(expired)} 个用例的 override 已超过 {OVERRIDE_EXPIRY_DAYS} 天")
        for e in expired:
            print(f"   - {e['id']} ({e['category']}): {e['ageDays']} 天前创建 - {e['note']}")
        print("   请检查这些用例是否已修复，如已修复请删除对应 override\n")

    return expired


def record_run(project_dir: Path, summary: Dict) -> Dict:
    """
    记录一次运行。

    Args:
        project_dir: 项目目录
        summary: 运行摘要，包含 sessionId, time, records 等

    Returns:
        {skipped: bool, reason?: str, sessionId?: str, history?: List, ledger?: Dict}
    """
    # 检查过期 override
    check_expired_overrides(project_dir)

    session_id = summary.get('sessionId', f'sess_{int(datetime.now().timestamp())}')
    time_str = summary.get('time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    records = summary.get('records', [])

    if not records:
        return {'skipped': True, 'reason': 'no records'}

    config_dir = project_dir / 'output' / 'config'
    config_dir.mkdir(parents=True, exist_ok=True)

    history_file = config_dir / 'run_history.json'
    ledger_file = config_dir / 'failure_ledger.json'

    history = load_history(project_dir)
    ledger = load_ledger(project_dir)
    overrides = load_overrides(project_dir)

    # 检查是否已记录
    if any(h.get('sessionId') == session_id for h in history):
        return {'skipped': True, 'reason': f'already recorded: {session_id}'}

    # 处理每条记录
    processed_records = []
    for r in records:
        case_id = str(r.get('id', ''))
        err_msg = r.get('errMsg', '')
        category = 'pass' if r.get('ok') else classify(err_msg, case_id, overrides)
        note = overrides.get(case_id, {}).get('note', '') if not r.get('ok') else ''

        processed_records.append({
            'id': case_id,
            'name': r.get('name', ''),
            'module': r.get('module', ''),
            'ok': r.get('ok', False),
            'errMsg': err_msg,
            'dur': r.get('durSec', r.get('dur', '')),
            'category': category,
            'note': note,
        })

    # 写入历史
    run_entry = {
        'sessionId': session_id,
        'time': time_str,
        'total': summary.get('total', len(records)),
        'passed': summary.get('passed', 0),
        'failed': summary.get('failed', 0),
        'falsePasses': summary.get('falsePasses', 0),
        'headless': summary.get('headless'),
        'records': processed_records,
    }
    history.append(run_entry)

    # 更新台账
    for r in processed_records:
        case_id = r['id']
        if not case_id:
            continue

        if case_id not in ledger:
            ledger[case_id] = {
                'id': case_id,
                'name': r['name'],
                'module': r['module'],
                'firstSeen': time_str,
                'lastRun': time_str,
                'runs': 0,
                'catCounts': {},
                'lastOk': None,
                'lastFail': None,
                'lastErr': '',
                'note': r.get('note', ''),
                'verdict': 'single-run',
                'overrideCategory': overrides.get(case_id, {}).get('category'),
            }

        e = ledger[case_id]
        if case_id in overrides:
            e['overrideCategory'] = overrides[case_id].get('category')

        e['name'] = r['name'] or e['name']
        e['module'] = r['module'] or e['module']
        e['lastRun'] = time_str
        e['runs'] += 1

        cat = 'pass' if r['ok'] else r['category']
        e['catCounts'][cat] = e['catCounts'].get(cat, 0) + 1

        if r['ok']:
            e['lastOk'] = time_str
        else:
            e['lastFail'] = time_str
            e['lastErr'] = r['errMsg']

        if r.get('note'):
            e['note'] = r['note']

    # 计算每个用例的判定
    for case_id, e in ledger.items():
        non_pass = (
            e['catCounts'].get('env', 0) +
            e['catCounts'].get('script', 0) +
            e['catCounts'].get('product', 0) +
            e['catCounts'].get('flaky', 0) +
            e['catCounts'].get('unknown', 0)
        )
        e['fails'] = non_pass

        if e['runs'] < 2:
            e['verdict'] = 'single-run'
        elif non_pass == 0:
            e['verdict'] = 'clean'
        elif non_pass == e['runs']:
            sc = e['catCounts'].get('script', 0)
            ec = e['catCounts'].get('env', 0)
            pc = e['catCounts'].get('product', 0)

            if pc >= sc and pc >= ec:
                e['verdict'] = 'product-bug'
            elif sc >= ec:
                e['verdict'] = 'script-bug'
            else:
                e['verdict'] = 'env-blocked'
        else:
            e['verdict'] = 'flaky'

    # 写入文件
    history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), 'utf-8')
    ledger_file.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), 'utf-8')

    return {'skipped': False, 'sessionId': session_id, 'history': history, 'ledger': ledger}


def summarize(ledger: Dict) -> Dict:
    """汇总失败台账。"""
    out = {
        'envBlocked': [],
        'scriptBug': [],
        'productBug': [],
        'flaky': [],
        'clean': [],
        'single': [],
    }

    for case_id, e in ledger.items():
        oc = e.get('overrideCategory')
        verdict = e.get('verdict', 'single-run')

        if verdict == 'clean':
            out['clean'].append(e)
        elif verdict == 'flaky':
            out['flaky'].append(e)
        elif verdict == 'env-blocked' or (verdict == 'single-run' and oc == 'env'):
            out['envBlocked'].append(e)
        elif verdict == 'script-bug' or (verdict == 'single-run' and oc == 'script'):
            out['scriptBug'].append(e)
        elif verdict == 'product-bug' or (verdict == 'single-run' and oc == 'product'):
            out['productBug'].append(e)
        else:
            out['single'].append(e)

    def sort_key(e):
        return (-e.get('runs', 0), -e.get('fails', 0))

    for key in out:
        out[key].sort(key=sort_key)

    return out


def print_summary(project_dir: Path, ledger: Optional[Dict] = None):
    """打印失败台账汇总。"""
    if ledger is None:
        ledger = load_ledger(project_dir)

    s = summarize(ledger)

    def line(e):
        note = f" — {e['note']}" if e.get('note') else ''
        return f"  [{e['id']}] {e['name']}  (运行{e['runs']}次/失败{e.get('fails', 0)}次){note}"

    print("\n═══════ 失败台账汇总 ═══════")

    print(f"🌐 持久性环境问题（{len(s['envBlocked'])}）：")
    for e in s['envBlocked']:
        print(line(e))

    print(f"🐞 持久性脚本问题（{len(s['scriptBug'])}）：")
    for e in s['scriptBug']:
        print(line(e))

    print(f"🐛 产品代码问题/缺陷（{len(s['productBug'])}）：")
    for e in s['productBug']:
        print(line(e))

    print(f"⚠️  偶发/抖动（{len(s['flaky'])}）：")
    for e in s['flaky']:
        print(line(e))

    print(f"❓ 单次运行尚不能定性（{len(s['single'])}）：")
    for e in s['single']:
        print(line(e))

    print(f"✅ 已稳定通过（{len(s['clean'])}）")

    print("════════════════════════════\n")

    return s


def _generate_trend_chart(history: List) -> str:
    """生成通过率趋势折线图 SVG。

    Args:
        history: 历史运行记录列表

    Returns:
        SVG HTML 字符串
    """
    if not history or len(history) < 2:
        return '<div style="color: #888; padding: 20px;">历史数据不足，无法生成趋势图（至少需要 2 次运行）</div>'

    # 按时间排序（从旧到新）
    sorted_history = sorted(history, key=lambda x: x.get('time', ''))
    runs = list(range(1, len(sorted_history) + 1))

    # 计算每次运行的通过率
    pass_rates = []
    for h in sorted_history:
        total = h.get('total', 0)
        passed = h.get('passed', 0)
        rate = (passed / total * 100) if total > 0 else 0
        pass_rates.append(round(rate, 1))

    # SVG 尺寸
    width = 800
    height = 300
    padding = 60
    chart_width = width - 2 * padding
    chart_height = height - 2 * padding

    # 计算坐标点
    max_runs = len(runs)
    x_scale = chart_width / (max_runs - 1) if max_runs > 1 else chart_width
    y_scale = chart_height / 100

    points = []
    for i, (run, rate) in enumerate(zip(runs, pass_rates)):
        x = padding + i * x_scale
        y = height - padding - rate * y_scale
        points.append((x, y))

    # 生成折线路径
    path_d = 'M ' + ' L '.join(f'{x},{y}' for x, y in points)

    # 生成数据点
    circles = ''.join(
        f'<circle cx="{x}" cy="{y}" r="4" fill="#4a90d9" stroke="#fff" stroke-width="2">'
        f'<title>Run {run}: {rate}%</title></circle>'
        for (run, rate), (x, y) in zip(zip(runs, pass_rates), points)
    )

    # 生成 X 轴标签（每隔几个显示一个）
    step = max(1, len(runs) // 10)
    x_labels = ''.join(
        f'<text x="{padding + i * x_scale}" y="{height - padding + 20}" '
        f'text-anchor="middle" font-size="11" fill="#666">{run}</text>'
        for i, run in enumerate(runs) if i % step == 0
    )

    # 生成 Y 轴标签
    y_labels = ''.join(
        f'<text x="{padding - 10}" y="{height - padding - i * y_scale}" '
        f'text-anchor="end" font-size="11" fill="#666">{i}%</text>'
        for i in [0, 25, 50, 75, 100]
    )

    # 生成网格线
    grid_lines = ''.join(
        f'<line x1="{padding}" y1="{height - padding - i * y_scale}" '
        f'x2="{width - padding}" y2="{height - padding - i * y_scale}" '
        f'stroke="#e0e0e0" stroke-width="1"/>'
        for i in [0, 25, 50, 75, 100]
    )

    svg = f'''
    <div style="background: #fff; padding: 20px; margin: 20px 0; box-shadow: 0 1px 3px rgba(0,0,0,.08);">
      <h2 style="font-size: 16px; margin: 0 0 16px; padding-left: 8px; border-left: 4px solid #4a90d9;">
        📈 通过率趋势
      </h2>
      <svg width="{width}" height="{height}" style="max-width: 100%;">
        <!-- 网格线 -->
        {grid_lines}

        <!-- 坐标轴 -->
        <line x1="{padding}" y1="{height - padding}" x2="{width - padding}" y2="{height - padding}"
              stroke="#333" stroke-width="2"/>
        <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height - padding}"
              stroke="#333" stroke-width="2"/>

        <!-- X 轴标签 -->
        {x_labels}
        <text x="{width / 2}" y="{height - 10}" text-anchor="middle" font-size="12" fill="#666">运行次数</text>

        <!-- Y 轴标签 -->
        {y_labels}
        <text x="20" y="{height / 2}" text-anchor="middle" font-size="12" fill="#666"
              transform="rotate(-90 20 {height / 2})">通过率 (%)</text>

        <!-- 折线 -->
        <path d="{path_d}" fill="none" stroke="#4a90d9" stroke-width="2.5"/>

        <!-- 数据点 -->
        {circles}
      </svg>
      <div style="text-align: center; color: #888; font-size: 12px; margin-top: 8px;">
        共 {len(history)} 次运行 · 最新通过率: {pass_rates[-1]}%
      </div>
    </div>
    '''
    return svg


def render_ledger_html(project_dir: Path, ledger: Optional[Dict] = None, history: Optional[List] = None) -> str:
    """渲染失败台账 HTML 报告。"""
    if ledger is None:
        ledger = load_ledger(project_dir)
    if history is None:
        history = load_history(project_dir)

    s = summarize(ledger)

    def esc(text):
        return str(text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    def row_html(e):
        cats = ' / '.join(f"{k}:{v}" for k, v in e.get('catCounts', {}).items())
        tag = '<span class="tag">单次·人工定性</span> ' if e.get('runs', 0) < 2 else ''
        note = f'<div class="note">📌 {esc(e.get("note"))}</div>' if e.get('note') else ''
        err = f'<div class="err">{esc(e.get("lastErr", "")[:300])}</div>' if e.get('lastErr') else ''

        return f"""<tr>
    <td class="id">{esc(e.get('id'))}</td>
    <td>{tag}{esc(e.get('name'))}</td>
    <td class="mod">{esc(e.get('module'))}</td>
    <td class="num">{e.get('runs', 0)}</td>
    <td class="num">{e.get('fails', 0)}</td>
    <td class="num">{esc(cats)}</td>
    <td>{note}{err}</td>
  </tr>"""

    run_rows = ''.join(
        f"""<tr>
    <td>{esc(r.get('sessionId'))}</td>
    <td>{esc(r.get('time'))}</td>
    <td class="num">{r.get('total', 0)}</td>
    <td class="num ok">{r.get('passed', 0)}</td>
    <td class="num bad">{r.get('failed', 0)}</td>
    <td class="num">{r.get('falsePasses', 0)}</td>
  </tr>"""
        for r in reversed(history)
    )

    def section(title, items, css_class):
        rows = ''.join(row_html(e) for e in items) or '<tr><td colspan="7">无</td></tr>'
        return f"""
<h2 class="{css_class}">{title} <span class="count">{len(items)}</span></h2>
<table>
  <thead>
    <tr>
      <th>用例ID</th><th>名称</th><th>模块</th><th>运行</th><th>失败</th><th>分类计数</th><th>根因/末次错误</th>
    </tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""

    clean_rows = ''.join(row_html(e) for e in s['clean'][:60]) or '<tr><td colspan="7">无</td></tr>'
    if len(s['clean']) > 60:
        clean_rows += f'<tr><td colspan="7">… 共 {len(s["clean"])} 条</td></tr>'

    # 生成趋势图
    trend_chart = _generate_trend_chart(history) if history else ''

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>失败台账 · 环境 vs 脚本 vs 产品缺陷</title>
<style>
  body {{
    font-family: -apple-system, 'Segoe UI', Roboto, 'Microsoft YaHei', sans-serif;
    margin: 24px;
    color: #222;
    background: #fafafa;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 24px 0 8px; padding-left: 8px; border-left: 4px solid #4a90d9; }}
  .sub {{ color: #888; font-size: 12px; margin-bottom: 8px; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    background: #fff;
    font-size: 13px;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  th, td {{
    border: 1px solid #e3e3e3;
    padding: 7px 9px;
    text-align: left;
    vertical-align: top;
  }}
  th {{ background: #f0f3f7; font-weight: 600; }}
  .num {{ text-align: center; white-space: nowrap; }}
  .id {{ font-family: monospace; color: #555; }}
  .mod {{ color: #777; font-size: 12px; }}
  .ok {{ color: #2e9b4f; }}
  .bad {{ color: #d24b4b; }}
  .note {{ color: #b8860b; font-size: 12px; margin-top: 3px; }}
  .err {{ color: #a33; font-size: 12px; margin-top: 3px; white-space: pre-wrap; }}
  .tag {{
    display: inline-block;
    background: #fff3cd;
    color: #8a6d00;
    border: 1px solid #ffe08a;
    border-radius: 9px;
    padding: 0 6px;
    font-size: 11px;
    margin-right: 4px;
  }}
  .env h2 {{ border-color: #d24b4b; }}
  .script h2 {{ border-color: #e09a1a; }}
  .product h2 {{ border-color: #c0392b; }}
  .flaky h2 {{ border-color: #9b7bd9; }}
  .clean h2 {{ border-color: #2e9b4f; }}
  .count {{
    display: inline-block;
    background: #eee;
    border-radius: 10px;
    padding: 1px 8px;
    font-size: 12px;
    margin-left: 6px;
  }}
</style>
</head>
<body>
<h1>失败台账 · 环境限制 vs 脚本问题 vs 产品缺陷</h1>
<div class="sub">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ｜ 数据：output/config/run_history.json + failure_ledger.json</div>

{trend_chart}

{section('🌐 持久性环境问题', s['envBlocked'], 'env')}
{section('🐞 持久性脚本问题', s['scriptBug'], 'script')}
{section('🐛 产品代码问题 / 缺陷（须提报开发）', s['productBug'], 'product')}
{section('⚠️ 偶发/抖动（有时过有时挂）', s['flaky'], 'flaky')}
{section('❓ 单次运行（尚不能定性）', s['single'], '')}

<h2 class="clean">✅ 已稳定通过 <span class="count">{len(s['clean'])}</span></h2>
<table>
  <thead>
    <tr>
      <th>用例ID</th><th>名称</th><th>模块</th><th>运行</th><th>失败</th><th>分类计数</th><th>备注</th>
    </tr>
  </thead>
  <tbody>{clean_rows}</tbody>
</table>

<h2>📜 历史运行记录（{len(history)} 次）</h2>
<table>
  <thead>
    <tr><th>Session</th><th>时间</th><th>总数</th><th>通过</th><th>失败</th><th>假通过</th></tr>
  </thead>
  <tbody>{run_rows}</tbody>
</table>
</body>
</html>"""

    return html


if __name__ == '__main__':
    # 命令行入口：生成 HTML 报告
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("Usage: python -m lib.run_history <project_dir> [output.html]")
        sys.exit(1)

    project_dir = Path(sys.argv[1])
    output_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parents[1] / 'output' / 'reports' / 'failure_ledger.html'

    html = render_ledger_html(project_dir)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html, 'utf-8')

    print(f"✅ 失败台账 HTML 报告已生成: {output_file}")

    # 同时打印汇总
    print_summary(project_dir)
