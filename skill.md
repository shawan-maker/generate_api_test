# Skill: API 自动化测试发现系统

## 元信息

- **名称**: api-ai-test
- **版本**: 2.1.0
- **用途**: 自动化发现、生成和执行 API 测试
- **平台**: 通用（Claude / GPT / 其他 AI 客户端）
- **触发词**: api, test, 自动化, 发现, 生成, 执行, 测试, 用户管理, 角色管理

---

## 能力描述

本系统实现从 UI 探测到 API 测试脚本生成的完整自动化流水线，支持多模块批量处理和增量发现。

### 核心功能

1. **Stage 1-5 全流程** — 从 UI 探测到测试脚本生成的完整流水线
2. **Playbook 架构** — Stage 1 生成可执行操作指令集，Stage 2 直接回放
3. **5 角色分类系统** — name/mutable/context/generate/static 智能分类
4. **值匹配驱动** — 基于值的精确匹配，context 靠值识别
5. **前置 API 追踪** — 自动识别和排序依赖的前置 API
6. **多格式导出** — Postman Collection / helpers.py / Excel 参数文件
7. **增量发现** — 7 天内已发现的模块自动跳过

---

## 使用方法

### 单模块完整流程

```bash
python -m core.discovery.run --project <project_id> \
  --module <模块名> \
  --url <模块URL路径> \
  --stage all
```

### 分阶段执行

```bash
# Stage 1: UI 探测
python -m core.discovery.run --project ecm-compute --module 用户管理 --url /path --stage 1

# Stage 2: API 捕获
python -m core.discovery.run --project ecm-compute --module 用户管理 --url /path --stage 2

# Stage 3+4: 分析 + 生成（离线）
python -m core.discovery.run --project ecm-compute --module 用户管理 --stage 34 --offline

# Stage 5: 导出
python -m core.discovery.run --project ecm-compute --module 用户管理 --stage 5
```

### 批量处理

```bash
# 处理 modules.yaml 中的所有模块
python -m core.discovery.run --project ecm-compute --all-modules

# 按标签过滤
python -m core.discovery.run --project ecm-compute --all-modules --tag 用户中心

# 强制重新发现
python -m core.discovery.run --project ecm-compute --all-modules --force
```

### 导航发现模式

```bash
# 探测菜单，输出模块列表
python -m core.discovery.run --project ecm-compute --discover-only --home-url "<URL>" --headless

# 从缓存选择模块执行
python -m core.discovery.run --project ecm-compute --discover-select "1,3,5" --headless

# 一体化交互模式
python -m core.discovery.run --project ecm-compute --discover --headless
```

### 并行测试执行

```bash
python -m core.discovery.run_parallel --project ecm-compute
python -m core.discovery.run_parallel --project ecm-compute --version v1.0.0 --parallel 6
```

---

## 5 角色分类系统

### 角色定义

| 角色 | 说明 | 示例 |
|------|------|------|
| **name** | 用户可读名称字段 | username, name, label |
| **mutable** | 可变字段（update 时修改） | description, remark, memo |
| **context** | 上下文引用（从前置 API 获取） | tenantId, policyIds |
| **generate** | 动态生成的测试值 | phone, email, uuid |
| **static** | 系统固定值 | state, isRandomPassword |

### 分类优先级

1. **Pass 1: name** — 字段名含 name/title/label + 值是短可读字符串
2. **Pass 2: mutable** — 字段名含 description/memo/remark/note/content
3. **Pass 3: context** — 精确值匹配（在 value_index 中找到相同值）
4. **Pass 4: generate** — 值模式分析（uuid、hex_id、phone、email）
5. **Pass 5: static** — 兜底

---

## 项目结构

```
API_AI_test/
├── core/
│   └── discovery/              # 主管线引擎（Stage 1-5）
│       ├── run.py              # CLI 入口
│       ├── discover_ui.py      # Stage 1: UI 探测 + Playbook 生成
│       ├── capture_apis.py     # Stage 2: API 捕获 + 回放
│       ├── analyze_flow.py     # Stage 3: 逻辑分析 + Manifest 构建
│       ├── gen_test.py         # Stage 4: API 脚本生成
│       ├── generate_ui_script.py # Stage 2b: UI 脚本生成
│       ├── export_artifacts.py # Stage 5: 导出
│       ├── stage1_rescue.py    # Stage 1 Phase C/D/E 补救
│       ├── feedback_loop.py    # Stage 1↔2 反馈循环
│       ├── request_interceptor.py # HTTP 拦截 + KB 驱动注入
│       ├── endpoint_classifier.py # 端点分类去重
│       ├── stage_validators.py # 质量门禁
│       ├── pre_api_merger.py   # 跨模块前置 API 合并
│       ├── nav_discovery.py    # 导航菜单爬取
│       ├── discover_navigation.py # 导航发现编排
│       ├── run_parallel.py     # 并行测试执行
│       └── replay/             # UI 操作回放引擎
│           ├── replay_engine.py  # Playbook 回放执行器
│           ├── button_driver.py  # 按钮点击驱动
│           ├── form_filler.py    # 表单智能填充
│           ├── locator_helpers.py # 定位器辅助
│           └── wait_helpers.py   # 事件驱动等待
├── lib/
│   ├── auth/                   # 认证模块
│   │   ├── auth.py             # 鉴权引擎
│   │   ├── slider.py           # 滑块验证码
│   │   └── cookie_client.py    # Cookie 管理客户端
│   ├── runtime/                # 测试运行时
│   │   ├── test_runtime.py     # Manifest 驱动运行时
│   │   ├── global_pre_apis.py  # 全局前置 API 执行器
│   │   ├── run_history.py      # 运行历史记录
│   │   └── path_utils.py       # 路径工具
│   ├── report/                 # 报告生成
│   │   ├── test_report.py      # JSONL → HTML 报告
│   │   └── ui_report.py        # UI 测试报告
│   └── utils.py                # 工具函数
├── config/                     # 全局配置
│   ├── credentials.yaml        # 全局凭据库
│   ├── debug_strategies.json   # 调试策略
│   ├── failure_patterns.yaml   # 失败模式
│   └── probe_lessons_kb.json   # 探测经验知识库
├── projects/                   # 项目数据
│   └── <project_id>/
│       ├── modules.yaml        # 模块清单
│       ├── profile.yaml        # 项目配置
│       └── v1.0.0/             # 版本化输出
│           ├── api/            # API 测试脚本 + lib/
│           ├── ui/             # UI 测试脚本 + lib/
│           └── export/         # Stage 5 导出文件
└── workspace/                  # 中间产物
    └── <project_id>/
        └── kb/module_discovered/ # KB 中间数据
```

---

## 关键特性

### 1. Playbook 架构

Stage 1 生成 playbook（结构化操作指令集），Stage 2 直接回放：

```json
{
  "operations": {
    "创建用户": {
      "role": "create",
      "detection_status": "success",
      "steps": [
        {"action": "click_button", "playwright_locator": "..."},
        {"action": "fill_form", "fields": [...], "fill_rule": {...}},
        {"action": "click_button", "text": "确定"}
      ]
    }
  }
}
```

失败操作也会生成步骤（至少包含 click_button），确保 Stage 2 能回放并捕获 API 响应。

### 2. 值匹配驱动

- **精确值匹配**：在前置 API 响应中查找相同的值
- **纯值匹配**：context 完全靠值相等识别，不依赖字段名匹配
- **URL 路径参数**：pathname 中的动态参数通过值匹配确定来源

### 3. 前置 API 自动追踪

- 自动识别业务操作依赖的前置 API
- 拓扑排序确保执行顺序正确
- 链式依赖追踪（前置 API 的参数也可能需要其他前置 API）

### 4. 增量发现

- 7 天内已发现的模块自动跳过（除非 `--force`）
- 基于 `kb/module_discovered/{module}_manifest.json` 的 mtime 检查
- URL 变更也会触发重新发现

### 5. 多格式导出

- **Postman Collection** — 可直接导入 Postman
- **helpers.py** — 高层辅助函数（供外部测试平台使用）
- **Excel 参数文件** — 变量配置表（e_ 前缀，敏感标记）

---

## 配置说明

### modules.yaml

```yaml
modules:
  - name: 用户管理
    url: /estack/web/estack/user-center/user-manage/user
    enabled: true
    tags: [用户中心, 基础]
    priority: 1

  - name: 角色管理
    url: /estack/web/estack/user-center/user-manage/role
    enabled: true
    tags: [用户中心, 权限]
    priority: 2
```

### profile.yaml

```yaml
base_url: https://example.com
login_url: https://example.com/login
auth:
  cookie_token_key: accessToken
  header_name: Authorization
  header_prefix: "Bearer "
```

---

## 常见问题

### Q: 认证失败怎么办？

A: 重新运行 Stage 1-2 时会自动登录并刷新 cookie。

### Q: 如何跳过浏览器阶段？

A: 使用 `--offline` 参数（需要有已捕获的数据）：
```bash
python -m core.discovery.run --project ecm-compute --module 用户管理 --stage 34 --offline
```

### Q: 如何查看详细的日志？

A: 所有日志输出到 `projects/<project>/output/logs/` 目录

### Q: 生成的脚本在哪里？

A: `projects/<project_id>/v1.0.0/api/<模块名>_API测试.py`

---

## 技术栈

- **Python 3.9+**
- **Playwright** — UI 探测和 API 捕获
- **pytest** — 测试执行框架
- **httpx** — HTTP 客户端
- **opencv-python-headless** — 滑块验证码缺口识别
- **pyyaml** — 配置文件解析
- **jsonpath-ng** — JSON 路径查询
- **openpyxl** — Excel 导出（可选）
