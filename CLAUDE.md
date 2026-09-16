# API_AI_test — 项目说明

## 架构概览

```
API_AI_test/
├── module_discovery/          # 主管线（Stage 1-5 发现引擎）
│   ├── run.py                 # CLI 入口（~940 行）
│   ├── io_helpers.py          # I/O 辅助（profile/manifest/modules 加载）
│   ├── auth_runner.py         # 浏览器登录 + cookie 管理
│   ├── batch_runner.py        # 批量模块发现
│   ├── stage1_rescue.py       # Stage 1 Phase C/D/E 补救
│   ├── discover_ui.py         # Stage 1: UI 探测
│   ├── capture_apis.py        # Stage 2: API 捕获
│   ├── analyze_flow.py        # Stage 3: 流程分析
│   ├── gen_test.py            # Stage 4: API 脚本生成
│   ├── generate_ui_script.py  # Stage 2b: UI 脚本生成
│   ├── export_artifacts.py    # Stage 5: Postman/Excel/helpers 导出
│   ├── feedback_loop.py       # 阶段间反馈循环
│   ├── pre_api_merger.py      # 跨模块前置 API 合并
│   ├── stage_validators.py    # 阶段验证器
│   ├── replay/                # 浏览器回放引擎
│   └── kb/                    # 静态知识库（UI selectors 等）
├── discovery/                 # P0 爬虫子系统（独立于主管线）
├── generators/                # 代码生成器（OpenAPI/pytest/API层）
├── lib/                       # 共享运行时库
│   ├── auth/                  # 认证（cookie_client, login_tool, slider）
│   ├── browser/               # 浏览器操作
│   ├── report/                # 报告生成
│   └── runtime/               # 测试运行时（test_runtime, global_pre_apis）
├── scripts/                   # 实用工具脚本
├── plugins/                   # pytest 插件
├── projects/                  # 项目数据（每个子目录是一个被测系统）
├── tests/                     # 单元测试
├── config/                    # 全局配置
└── docs/                      # 文档
```

## 主管线入口

```bash
# 主管线（Stage 1-5）
python -m module_discovery.run --project <id> --stage <1|2|34|5|all>

# 批量处理
python -m module_discovery.run --project <id> --all-modules --stage <stage>

# 并行测试执行
python -m module_discovery.run_parallel --project <id>

# 测试套件运行
python run_suite.py --project <id>

# 独立登录工具
python -m lib.auth.login_tool --project <id>

# P0 爬虫
python -m discovery.playwright_crawl --project <id>
```

## 输出规范

- **运行时输出** 统一在 `projects/<project>/output/`（logs、config、debug）
- **生成脚本** 在 `projects/<project>/scripts/<version>/`（api/ 和 ui/ 子目录）
- **KB 中间产物** 在 `projects/<project>/kb/module_discovered/`（manifest、capture、playbook）
- **Stage 5 导出** 在 `projects/<project>/output/api/<module>/<version>/`（Postman、Excel、helpers）
- 根目录不产生任何运行时输出

## 阶段流水线

```
Stage 1 (discover_ui)    → {module}_ui.json + {module}_playbook.json
Stage 2 (capture_apis)  → {module}.json (capture) + UI 脚本生成
Stage 3 (analyze_flow)  → {module}_analysis.json + {module}_manifest.json
Stage 4 (gen_test)      → {module}_API测试.py + 验证运行
Stage 5 (export)        → Postman Collection + helpers.py + Excel params
```

## 关键设计

- **Cookie-only 认证**：生成的脚本不包含登录逻辑，仅依赖 cookies.json
- **Manifest 驱动**：Stage 3-5 都基于 manifest 字典，Stage 5 可独立运行
- **增量发现**：7 天内已发现的模块自动跳过（除非 --force）
- **运行时同步**：gen_test.py / generate_ui_script.py 自动同步 lib/ 到脚本包

## 测试

```bash
# 运行全部单元测试
python -m pytest tests/ -q

# 跳过已知失败的测试（pre-existing）
python -m pytest tests/ --ignore=tests/test_feedback_loop.py --ignore=tests/test_phase1_required_elements.py -q
```
