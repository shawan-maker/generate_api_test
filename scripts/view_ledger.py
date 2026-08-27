"""
查看失败归因台账

用法：
  python scripts/view_ledger.py ecm-compute
  python scripts/view_ledger.py ecm-compute --html
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/view_ledger.py <project_dir> [--html]")
        sys.exit(1)

    project_dir = ROOT / "projects" / sys.argv[1]
    if not project_dir.exists():
        print(f"❌ 项目目录不存在: {project_dir}")
        sys.exit(1)

    from lib.run_history import print_summary, render_ledger_html

    # 生成 HTML 报告
    if "--html" in sys.argv:
        html = render_ledger_html(project_dir)
        output_file = Path(__file__).resolve().parents[1] / "output" / "reports" / "failure_ledger.html"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(html, "utf-8")
        print(f"✅ HTML 报告已生成: {output_file}")
    else:
        # 打印汇总
        print_summary(project_dir)


if __name__ == "__main__":
    main()
