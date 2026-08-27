"""
module_discovery — 模块级 API 自动化发现与测试脚本生成引擎。

给定一个功能模块的入口 URL + 登录凭证，自动完成四个阶段：
  Stage 1: 前端按钮探测 (discover_ui.py)
  Stage 2: API 捕获 (capture_apis.py)
  Stage 3: 逻辑分析 (analyze_flow.py)
  Stage 4: 脚本生成 (gen_test.py)

用法:
    python -m module_discovery.run --project ecm-compute \\
        --url "/estack/web/estack/user-center/user-manage/user" \\
        --module "用户管理" --user jcyz213-test --pass '9smkKAq@4'

产出:
    projects/<id>/kb/module_discovered/<模块名>.json   (Stage 1-2 结果)
    projects/<id>/flows/<模块名>_API测试.py              (Stage 4 生成脚本)
"""
