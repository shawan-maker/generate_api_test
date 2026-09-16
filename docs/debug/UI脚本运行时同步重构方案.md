# UI 脚本运行时同步机制重构方案

**日期**: 2026-09-16  
**状态**: 待实施  
**影响范围**: `module_discovery/generate_ui_script.py`  
**关联问题**: UI 自动化脚本"编辑"和"删除"操作失败

---

## 一、问题现象

| 操作 | Stage 2 回放 | UI 脚本运行 | 差异原因 |
|------|-------------|------------|---------|
| 编辑 | ✅ JS 回退成功 | ❌ Playwright 超时（元素不可见） | UI lib 缺少 JS 回退逻辑 |
| 删除 | ✅ JS 回退成功 | ❌ 按钮被禁用超时 | UI lib 缺少 JS 回退逻辑 |

**根因**: UI 脚本的运行时库（`ui/lib/`）与 Stage 2 运行时库（`module_discovery/replay/`）**版本严重不一致**，同步机制从未正常工作。

---

## 二、现有问题清单（6 个 Bug）

### Bug 1: 同步源路径错误（致命）

```python
# generate_ui_script.py:63
src_dir = Path(__file__).resolve().parent  # → module_discovery/
module_files = ["replay_engine.py", "button_driver.py", ...]
src = src_dir / name  # → module_discovery/replay_engine.py  ❌ 不存在
```

**实际路径**: `module_discovery/replay/replay_engine.py`

**后果**: 同步时打印 `Runtime file missing` 警告后跳过，UI lib 中保留了手动放入的旧版本文件。

**日志证据**:
```
Runtime file missing: D:\Mobile\API_AI_test\module_discovery\replay_engine.py
Runtime file missing: D:\Mobile\API_AI_test\module_discovery\button_driver.py
Runtime file missing: D:\Mobile\API_AI_test\module_discovery\form_filler.py
Runtime file missing: D:\Mobile\API_AI_test\module_discovery\wait_helpers.py
Runtime file missing: D:\Mobile\API_AI_test\module_discovery\ui_report.py
```

### Bug 2: `locator_helpers.py` 未在同步列表中

`module_discovery/replay/locator_helpers.py` 被 `replay_engine.py`、`button_driver.py`、`form_filler.py` 共 12 处引用（`from .locator_helpers import safe_css`），但从未被同步到 `ui/lib/`。

**后果**: 如果其他文件被修复同步，新代码调用 `safe_css` 时会 `ImportError`。

### Bug 3: `ui_selectors/*.json` 未同步

`const.py` 运行时加载 `Path(__file__).parent / "ui_selectors"` 目录下的 JSON 文件：
- `module_discovery/ui_selectors/element-ui.json`（4,769 B）
- `module_discovery/ui_selectors/ant-design.json`（4,746 B）

同步函数从未复制这些文件。当 `const.py` 被正确同步后，`get_ui_selectors()` 调用会 `FileNotFoundError`。

### Bug 4: 导入路径不兼容

| 源文件（`replay/`） | 导入语句 | UI lib 需要的 |
|---|---|---|
| `replay_engine.py` | `from .. import const` | `from . import const` |
| `button_driver.py` | `from .. import const` | `from . import const` |
| `form_filler.py` | `from .. import const` | `from . import const` |
| `wait_helpers.py` | `from .. import const` | `from . import const` |
| `locator_helpers.py` | `from .. import const` | `from . import const` |
| `kb_loader.py` | `from . import const` | `from . import const` ✅ |

**原因**: `replay/` 是 `module_discovery/` 的子包，用 `..` 访问父包。`ui/lib/` 是扁平结构，`const.py` 是同层兄弟。

**后果**: 直接复制会导致 `ImportError`。旧版本是手动修改过的。

### Bug 5: 同步无失败检测

同步函数对缺失文件只打印 `LOG.warning`，不抛出异常，不中断流程。CI 流水线无法检测到同步失败。

### Bug 6: `cookies.json` 不更新

```python
# generate_ui_script.py:121
if not dst_cookies.exists():  # 只在目标不存在时复制
```

Cookie 过期后刷新的新 cookie 不会同步到 UI 脚本的 `config/` 目录。

---

## 三、文件版本差异量化

| 文件 | 源版本大小 | UI lib 大小 | 差异 | 缺失更新天数 |
|------|-----------|------------|------|-------------|
| `replay_engine.py` | 33,952 B（824 行） | 20,489 B（536 行） | **-13.5 KB** | 9 天 |
| `button_driver.py` | 47,536 B | 36,070 B | **-11.5 KB** | 6 天 |
| `form_filler.py` | 99,986 B | 88,293 B | **-11.7 KB** | 10 天 |
| `wait_helpers.py` | 7,157 B | 6,574 B | **-583 B** | 5 天 |
| `locator_helpers.py` | 2,861 B | **不存在** | **完全缺失** | — |
| `ui_selectors/*.json` | 9,515 B | **不存在** | **完全缺失** | — |

---

## 四、完整修改方案

### 4.1 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `module_discovery/generate_ui_script.py` | **重写** `_sync_ui_runtime_lib()` | 修复全部 6 个 Bug |

**仅需修改 1 个文件**。

### 4.2 重写后的 `_sync_ui_runtime_lib()` 设计

```
输入: ui_dir（目标目录，如 projects/ecm-compute/scripts/v1.0.0/ui/）

源目录定义:
  replay_dir = module_discovery/replay/     ← 回放引擎源码
  md_dir     = module_discovery/            ← const.py, kb_loader.py, ui_report.py
  lib_dir    = lib/                         ← cookie_client.py
  kb_dir     = module_discovery/kb/         ← probe_knowledge.json
  selectors_dir = module_discovery/ui_selectors/ ← *.json

目标目录:
  ui/lib/                                   ← 所有 Python 模块（flat 结构）
  ui/lib/kb/                                ← KB 数据文件
  ui/lib/ui_selectors/                      ← UI 选择器 JSON
  ui/config/                                ← cookies.json

同步规则:
  1. 遍历 replay_dir/*.py → 复制到 ui/lib/（跳过 __init__.py）
  2. 复制 md_dir/{const, kb_loader, ui_report}.py → ui/lib/
  3. 复制 lib_dir/cookie_client.py → ui/lib/
  4. 复制 kb_dir/probe_knowledge.json → ui/lib/kb/
  5. 复制 selectors_dir/*.json → ui/lib/ui_selectors/
  6. 复制 cookies.json → ui/config/（每次都覆盖）

导入转换规则（仅对 replay/ 下的文件）:
  "from .. import const"         → "from . import const"
  "from ..kb_loader import"      → "from .kb_loader import"
  "from .. import "              → "from . import "        （兜底）
  "from . import "               → 保持不变                （兄弟引用）
  "from .locator_helpers import" → 保持不变

同步后验证:
  python -c "import sys; sys.path.insert(0,'ui/'); from lib import replay_engine"
  如果 ImportError → 抛异常终止
```

### 4.3 伪代码

```python
def _sync_ui_runtime_lib(ui_dir: Path):
    """从 Stage 2 运行时同步所有文件到 ui/lib/。"""
    src_dir = Path(__file__).resolve().parent       # module_discovery/
    replay_dir = src_dir / "replay"                  # module_discovery/replay/
    lib_dir = src_dir.parent / "lib"                 # lib/
    dst_lib = ui_dir / "lib"
    dst_lib.mkdir(parents=True, exist_ok=True)

    synced = []
    
    # ---- 1. replay/*.py → ui/lib/ ----
    for src_file in sorted(replay_dir.glob("*.py")):
        if src_file.name == "__init__.py":
            continue
        dst_file = dst_lib / src_file.name
        content = src_file.read_text(encoding="utf-8")
        content = _transform_imports(content)     # from .. → from .
        _write_if_changed(dst_file, content)
        synced.append(src_file.name)
    
    # ---- 2. module_discovery 顶层模块 → ui/lib/ ----
    for name in ("const.py", "kb_loader.py", "ui_report.py"):
        src_file = src_dir / name
        if src_file.exists():
            dst_file = dst_lib / name
            _write_if_changed(dst_file, src_file.read_text(encoding="utf-8"))
            synced.append(name)
    
    # ---- 3. lib/cookie_client.py → ui/lib/ ----
    cookie_src = lib_dir / "cookie_client.py"
    if cookie_src.exists():
        _write_if_changed(dst_lib / "cookie_client.py",
                          cookie_src.read_text(encoding="utf-8"))
        synced.append("cookie_client.py")
    
    # ---- 4. kb/probe_knowledge.json → ui/lib/kb/ ----
    kb_src = src_dir / "kb" / "probe_knowledge.json"
    if kb_src.exists():
        dst_kb = dst_lib / "kb"
        dst_kb.mkdir(exist_ok=True)
        _write_if_changed(dst_kb / "probe_knowledge.json",
                          kb_src.read_text(encoding="utf-8"))
    
    # ---- 5. ui_selectors/*.json → ui/lib/ui_selectors/ ----
    selectors_src = src_dir / "ui_selectors"
    if selectors_src.exists():
        dst_selectors = dst_lib / "ui_selectors"
        dst_selectors.mkdir(exist_ok=True)
        for json_file in selectors_src.glob("*.json"):
            _write_if_changed(dst_selectors / json_file.name,
                              json_file.read_text(encoding="utf-8"))
    
    # ---- 6. cookies.json → ui/config/（每次覆盖）----
    _sync_cookies(ui_dir)
    
    # ---- 7. 生成 __init__.py ----
    init_file = dst_lib / "__init__.py"
    init_content = '"""UI automation runtime library — synced from module_discovery."""\n'
    _write_if_changed(init_file, init_content)
    
    # ---- 8. 同步后验证 ----
    _verify_sync(dst_lib, synced)
    
    LOG.info(f"  UI 运行时同步完成: {len(synced)} 个文件")


def _transform_imports(content: str) -> str:
    """将 replay/ 子包的父级导入转换为 flat 包导入。"""
    lines = content.split("\n")
    result = []
    for line in lines:
        stripped = line.lstrip()
        # from .. import const → from . import const
        if stripped.startswith("from .. import "):
            line = line.replace("from .. import ", "from . import ", 1)
        # from ..kb_loader import → from .kb_loader import
        elif stripped.startswith("from .."):
            line = line.replace("from ..", "from .", 1)
        result.append(line)
    return "\n".join(result)


def _write_if_changed(dst: Path, content: str):
    """仅在内容变化时写入，避免不必要的文件更新。"""
    if dst.exists() and dst.read_text(encoding="utf-8") == content:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8")


def _verify_sync(dst_lib: Path, synced: list):
    """验证同步后的模块能正确导入。"""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, str({str(dst_lib.parent)!r})); "
         f"from lib import replay_engine; from lib import button_driver; "
         f"from lib import form_filler; from lib import wait_helpers"],
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        LOG.error(f"  ❌ 同步验证失败: {result.stderr.strip()}")
        raise RuntimeError(f"UI runtime sync verification failed: {result.stderr}")
```

---

## 五、老旧逻辑删除方案

### 5.1 删除内容

以下代码在重写后不再需要，应**完整删除**：

| 位置 | 删除内容 | 原因 |
|------|----------|------|
| `_sync_ui_runtime_lib()` 原实现（第 61–124 行） | 整个函数体 | 路径错误、文件列表硬编码、缺少导入转换 |
| 原 `module_files` 列表 | `["replay_engine.py", "button_driver.py", ...]` | 改为自动发现 `replay/*.py` |
| 原 `if not src.exists(): LOG.warning(...)` | 缺失文件静默跳过逻辑 | 改为缺失时报错 |
| 原 `cookies.json` 仅首次复制逻辑 | `if not dst_cookies.exists()` | 改为每次覆盖 |

### 5.2 保留内容

| 内容 | 保留原因 |
|------|----------|
| `generate_ui_script()` 主函数 | 流程不变，只改同步子步骤 |
| `_extract_test_data()` | 数据提取逻辑不变 |
| `_render_script()` | 脚本模板渲染不变 |
| UI 报告生成逻辑（`ui_report.py`） | 报告样式不变 |

### 5.3 旧文件清理

同步机制修复后，`ui/lib/` 中的旧文件会被自动覆盖。无需手动删除。但建议清理：

```
projects/ecm-compute/scripts/v1.0.0/ui/lib/output/    ← 旧版报告输出目录残留
```

---

## 六、同步后文件结构

```
scripts/v1.0.0/ui/
├── 用户管理.py                      ← 主脚本（不变）
├── 用户管理_playbook.json           ← Playbook 数据（不变）
├── 用户管理_data.json               ← 测试数据（不变）
├── config/
│   └── cookies.json                 ← 每次覆盖同步
├── lib/
│   ├── __init__.py                  ← 自动生成 stub
│   ├── replay_engine.py             ← 从 replay/ 复制 + 导入转换
│   ├── button_driver.py             ← 从 replay/ 复制 + 导入转换
│   ├── form_filler.py               ← 从 replay/ 复制 + 导入转换
│   ├── wait_helpers.py              ← 从 replay/ 复制 + 导入转换
│   ├── locator_helpers.py           ← 从 replay/ 复制 + 导入转换（新增）
│   ├── const.py                     ← 从 module_discovery/ 直接复制
│   ├── kb_loader.py                 ← 从 module_discovery/ 直接复制
│   ├── ui_report.py                 ← 从 module_discovery/ 直接复制
│   ├── cookie_client.py             ← 从 lib/ 直接复制
│   ├── kb/
│   │   └── probe_knowledge.json     ← KB 数据文件
│   └── ui_selectors/
│       ├── element-ui.json          ← UI 选择器（新增）
│       └── ant-design.json          ← UI 选择器（新增）
└── output/
    └── reports/
        └── 用户管理_ui_report_*.html ← 报告输出（样式不变）
```

---

## 七、验证清单

### 7.1 同步机制验证

| 序号 | 验证项 | 验证方法 | 预期结果 |
|------|--------|----------|----------|
| 1 | 文件完整性 | 对比 `replay/*.py` 与 `ui/lib/*.py` 行数 | 一致（导入转换后） |
| 2 | `locator_helpers.py` 存在 | `ls ui/lib/locator_helpers.py` | 文件存在 |
| 3 | `ui_selectors/*.json` 存在 | `ls ui/lib/ui_selectors/` | 2 个 JSON 文件 |
| 4 | 导入转换 | `grep "from \.\." ui/lib/*.py` | 0 匹配（无父级导入） |
| 5 | 导入验证 | `python -c "from lib import replay_engine"` | 无 ImportError |
| 6 | 同步日志 | 检查 Stage 2 输出 | 无 `Runtime file missing` 警告 |

### 7.2 UI 脚本运行验证

| 序号 | 验证项 | 验证方法 | 预期结果 |
|------|--------|----------|----------|
| 7 | 编辑操作 | 运行 UI 脚本 `--headless` | ✅ 成功（JS 回退生效） |
| 8 | 删除操作 | 运行 UI 脚本 `--headless` | ✅ 成功（JS 回退生效） |
| 9 | 报告生成 | 检查 HTML 报告文件 | 样式与之前一致 |
| 10 | 独立运行 | 拷贝 `ui/` 到临时目录运行 | 正常运行，无路径依赖 |

### 7.3 回归验证

| 序号 | 验证项 | 验证方法 | 预期结果 |
|------|--------|----------|----------|
| 11 | 单元测试 | `pytest tests/test_module_discovery.py` | 全部通过 |
| 12 | Stage 2 在线运行 | `--stage 2 --headless` | 验证通过 |
| 13 | Stage 3+4 离线 | `--offline` | 验证通过 |
| 14 | API 脚本运行 | 运行 API 测试脚本 | 结果不变 |

---

## 八、四项保证逐项确认

### 保证 1: 后续 UI 脚本自动生成正确

| 检查点 | 状态 | 说明 |
|--------|------|------|
| Stage 2 完成后触发 UI 脚本生成 | ✅ 已有 | `run.py:489` 在 Stage 2 验证通过后调用 |
| 同步函数被调用 | ✅ 已有 | `generate_ui_script()` 步骤 2 |
| 所有 replay 文件被正确复制 | ✅ 修复后 | 自动发现 `replay/*.py`，路径正确 |
| 导入路径自动转换 | ✅ 新增 | `_transform_imports()` |
| 同步后自动验证 | ✅ 新增 | `_verify_sync()` 导入检查 |

### 保证 2: 同步机制后续自动生效

| 检查点 | 状态 | 说明 |
|--------|------|------|
| 每次 Stage 2 完成都触发同步 | ✅ 已有 | `run_stage2()` 验证通过后调用 |
| 增量更新（仅写变化的文件） | ✅ 新增 | `_write_if_changed()` |
| 新增 replay 模块自动发现 | ✅ 修复后 | `replay_dir.glob("*.py")` 自动发现 |
| 同步失败时抛异常 | ✅ 修复后 | 不再静默跳过 |

### 保证 3: 现有 UI 报告样式不变、结构保留

| 检查点 | 状态 | 说明 |
|--------|------|------|
| `ui_report.py` 被同步 | ✅ 修复后 | 从 `module_discovery/` 复制 |
| 报告输出路径 | ✅ 不变 | `Path(__file__).parent.parent / "output" / "reports"` |
| 报告模板 | ✅ 不变 | `_render_script()` 和 `ui_report.py` 不修改 |
| 截图功能 | ✅ 不变 | Playwright screenshot API 不受影响 |

### 保证 4: UI 脚本能拷贝到其他机器独立运行

| 检查点 | 状态 | 说明 |
|--------|------|------|
| 所有依赖在 `lib/` 内 | ✅ | flat 结构，`from . import` |
| 数据文件（KB/选择器）在 `lib/` 内 | ✅ 修复后 | `kb/` + `ui_selectors/` 子目录 |
| 不依赖 `module_discovery/` 路径 | ✅ | 无 `from ..` 导入 |
| 不依赖项目根目录 | ✅ | 仅依赖 `config/cookies.json`（相对路径） |
| 外部依赖仅 playwright + stdlib | ✅ | `cookie_client.py` 的 requests 是 lazy import |

---

## 九、潜在风险与应对

| 风险 | 概率 | 影响 | 应对 |
|------|------|------|------|
| `_transform_imports` 遗漏新模式 | 低 | `ImportError` | 用正则 `from \.\.` 全局扫描验证 |
| `replay/` 新增非 `.py` 文件 | 低 | 缺失数据文件 | 当前 `replay/` 下无数据文件，未来如有需扩展 |
| `const.py` 的 `ui_selectors/` 路径在 `ui/lib/` 下解析不同 | 无 | — | `_UI_SELECTORS_DIR = Path(__file__).parent / "ui_selectors"` 在 flat 结构下解析为 `ui/lib/ui_selectors/`，正确 |
| `kb_loader.py` 的 KB 路径在 `ui/lib/` 下解析不同 | 低 | `FileNotFoundError` | `kb_loader.py` 从 `module_discovery/kb/` 加载，同步后从 `ui/lib/kb/` 加载，路径一致（`Path(__file__).parent / "kb"`） |
| 同步验证子进程启动慢 | 低 | 增加 ~1s 延迟 | 可接受，仅在生成时执行一次 |

---

## 十、实施步骤

```
Step 1: 重写 _sync_ui_runtime_lib()
         - 修改文件: generate_ui_script.py
         - 新增: _transform_imports(), _write_if_changed(), _verify_sync()
         - 删除: 旧的 module_files 硬编码列表、静默跳过逻辑

Step 2: 运行单元测试
         - python -m pytest tests/test_module_discovery.py -q

Step 3: 重跑 Stage 2（在线）
         - python -m module_discovery.run --stage 2 --headless
         - 检查日志: 无 "Runtime file missing" 警告
         - 检查 ui/lib/: 所有文件已更新

Step 4: 运行 UI 脚本验证
         - python 用户管理.py --headless
         - 检查: 编辑 ✅、删除 ✅
         - 检查: HTML 报告生成正常

Step 5: 独立运行验证
         - 拷贝 ui/ 目录到临时位置
         - 运行 python 用户管理.py --headless
         - 确认无路径依赖

Step 6: 回归验证
         - Stage 3+4 离线运行正常
         - API 脚本运行结果不变
```

---

## 附录 A: 导入转换规则完整表

| 原始导入 | 转换后 | 出现位置 |
|----------|--------|----------|
| `from .. import const` | `from . import const` | replay_engine:6, button_driver:7, form_filler:8, wait_helpers:4, locator_helpers:3 |
| `from ..kb_loader import get_kb` | `from .kb_loader import get_kb` | button_driver:153/228/310, form_filler:40 |
| `from ..kb_loader import detect_active_overlay_js` | `from .kb_loader import detect_active_overlay_js` | button_driver:174 |
| `from ..kb_loader import get_overlay_prefix` | `from .kb_loader import get_overlay_prefix` | button_driver:174 |
| `from ..kb_loader import apply_overlay_scope` | `from .kb_loader import apply_overlay_scope` | button_driver:228/310 |
| `from . import const` | 不变 | kb_loader.py |
| `from .button_driver import ...` | 不变 | replay_engine.py（兄弟引用） |
| `from .form_filler import ...` | 不变 | replay_engine.py（兄弟引用） |
| `from .wait_helpers import ...` | 不变 | replay_engine.py（兄弟引用） |
| `from .locator_helpers import ...` | 不变 | replay_engine/button_driver/form_filler（兄弟引用） |

## 附录 B: `kb_loader.py` 路径兼容性验证

```python
# kb_loader.py 中的 KB 加载路径
KB_PATH = Path(__file__).parent / "kb" / "probe_knowledge.json"
```

| 运行环境 | `__file__` 解析 | KB 路径 | 是否存在 |
|----------|-----------------|---------|----------|
| Stage 2（module_discovery/） | `module_discovery/kb_loader.py` | `module_discovery/kb/probe_knowledge.json` | ✅ |
| UI 脚本（ui/lib/） | `ui/lib/kb_loader.py` | `ui/lib/kb/probe_knowledge.json` | ✅（同步后） |

## 附录 C: `const.py` 路径兼容性验证

```python
# const.py 中的 UI 选择器加载路径
_UI_SELECTORS_DIR = _Path(__file__).parent / "ui_selectors"
```

| 运行环境 | `__file__` 解析 | 选择器路径 | 是否存在 |
|----------|-----------------|-----------|----------|
| Stage 2（module_discovery/） | `module_discovery/const.py` | `module_discovery/ui_selectors/*.json` | ✅ |
| UI 脚本（ui/lib/） | `ui/lib/const.py` | `ui/lib/ui_selectors/*.json` | ✅（同步后） |
