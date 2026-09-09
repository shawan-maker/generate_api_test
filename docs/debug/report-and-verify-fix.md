# 修复：HTML 报告不完整 + Stage 4 校验失效

**日期**: 2026-09-08
**状态**: 待实施
**关联**: Stage 2-5 全流程验证

---

## 问题描述

### 问题 1：HTML 报告不完整

**现象**: 生成的测试报告 `用户管理_report_20260908_172919.html` 中只显示 1 个"创建用户" API 调用（HTTP 401），其余 11 个业务步骤和 3 个前置 API 均未出现。

**JSONL 日志内容**（`report/用户管理_API测试.jsonl`）：
```json
{"type": "test_start", "module": "用户管理", "total_steps": 12}
{"type": "api_call", "seq": 1, "step_action": "create", "step_label": "创建用户", "assertion": "failed", "assertion_message": "HTTP 401"}
{"type": "test_end", "total_api_calls": 1}
```

只有 3 行，缺少 11 个后续步骤和所有前置 API 调用。

### 问题 2：Stage 4 校验形同虚设

**现象**: `_run_stage4_verify()` 在脚本实际失败（所有 API 返回 401）时仍报告"✅ 脚本运行成功"，导致 Stage 5 导出在不完整的报告基础上执行。

**日志输出**：
```
[run]   运行脚本: 用户管理_API测试.py
[run]     ✅ Cookie 鉴权成功 (2 cookies, token=yes)
[run]     ❌ HTTP 401
[run]     ❌ HTTP 401
[run]     ✅ 前置 API 执行完成
[run]   ✅ 脚本运行成功          ← 误判！
[run] Stage 5: 导出 Artifacts    ← 不应执行
```

---

## 根因分析

### 根因 1：前置 API 不写 JSONL 日志

**位置**: `lib/runtime/test_runtime.py:685-757` — `execute_pre_apis()`

**现状**: 该方法在 `StepExecutor` 创建之前执行（line 868），只用 `print()` 输出到 stdout，**不写 JSONL 日志**。

```python
def execute_pre_apis(self, session):
    pre_apis = self.manifest.get("pre_apis", [])
    for pre_api in pre_apis:
        # ... 发送请求、提取字段 ...
        print(f"  → {api_name}: {method} {pathname}")  # 只输出到控制台
        # ❌ 没有写入 self.log_file
```

**影响**: 前置 API 调用在 HTML 报告中完全不可见。

### 根因 2：跳过步骤不写 JSONL 日志

**位置**: `lib/runtime/test_runtime.py:158-162` — `StepExecutor.execute()` 中的 `requires` 检查

**现状**: 当步骤的前置依赖（如 `id`）不满足时，直接 `return False`，**不写日志**。

```python
requires = step_def.get("requires", [])
for req in requires:
    if not self.state.get(req):
        print(f"  ⚠️ 跳过[{label}]: {req} 为空")
        return False  # ❌ 没有调用 self._write_log()
```

**影响**: 当 create 失败（401）导致 `id` 未提取时，后续 11 个步骤全部被跳过，但报告中看不到任何记录。

### 根因 3：脚本永不非零退出

**位置**: `lib/runtime/test_runtime.py:854-923` — `TestRunner.run()`

**现状**: 无论步骤通过还是失败，`run()` 方法末尾始终打印"✅ 测试完成"并正常退出（exit code 0）。

```python
def run(self, steps_filter=None):
    # ... 执行所有步骤 ...
    
    # line 911
    print("\n" + "=" * 60)
    print("  ✅ 测试完成")   # ← 即使全部失败也打印
    print("=" * 60)
    # ❌ 没有 sys.exit(1)
```

**影响**: 生成的脚本退出码始终为 0。

### 根因 4：Stage 4 校验只看退出码

**位置**: `module_discovery/run.py:586-644` — `_run_stage4_verify()`

**现状**: 通过 `result.returncode == 0` 判断脚本是否成功。

```python
def _run_script() -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(script)], ...)
    return result

result = _run_script()
if result.returncode == 0:       # ← 永远为 True
    LOG.info("  ✅ 脚本运行成功")
    return True
```

**影响**: 校验永远通过，Stage 5 导出不受脚本实际结果影响。

---

## 修改方案

### 修改 1：TestRunner.run() 失败时非零退出

**文件**: `lib/runtime/test_runtime.py`（line ~854-923）

**改动**: 在 `run()` 方法中统计失败步骤数，失败时调用 `sys.exit(1)`。

```python
def run(self, steps_filter: Optional[list] = None):
    print("=" * 60)
    print(f"  {self.module_name} API 测试")
    print("=" * 60)

    session = self.setup_auth()
    if session is None:
        return

    # 三路分支：共享上下文 > 模块级前置 API > fetch_context
    if self.shared_context:
        self._inject_shared_context()
    else:
        self.execute_pre_apis(session)

    steps = self.manifest.get("steps", [])
    create_step = next((s for s in steps if s.get("action") == "create"), None)
    if create_step:
        self.prepare_create_body(create_step)

    if steps_filter:
        steps_to_run = [s for s in steps if s.get("action") in steps_filter]
    else:
        steps_to_run = steps

    print(f"\n🚀 执行步骤: {', '.join(s['action'] for s in steps_to_run)} "
          f"(共 {len(steps_to_run)}/{len(steps)})")

    executor = StepExecutor(session, self.parser, self.state, self.ts,
                            self.base_url, log_file=self.log_file)

    executor._log_event("test_start", {
        "module": self.module_name,
        "base_url": self.base_url,
        "target_url": self.manifest.get("module", {}).get("target_url", ""),
        "total_steps": len(steps_to_run),
    })

    # ---- 改动：统计失败数 ----
    failed_count = 0

    try:
        for step_def in steps_to_run:
            print(f"\n{'=' * 60}")
            print(f"步骤: {step_def['action']}")
            print("=" * 60)
            ok = executor.execute(step_def)
            if not ok:
                failed_count += 1          # ← 新增
    except Exception as e:
        print(f"\n❌ 主流程异常: {e}")
        import traceback
        traceback.print_exc()

    executor._log_event("test_end", {
        "total_api_calls": executor._api_call_count,
    })

    # ---- 改动：输出摘要 + 非零退出 ----
    total = len(steps_to_run)
    passed = total - failed_count

    print("\n" + "=" * 60)
    print(f"  📊 结果: {passed}/{total} 通过, {failed_count} 失败")
    print("=" * 60)
    print(f"  📋 日志已写入: {self.log_file}")

    # 自动生成 HTML 报告
    try:
        from lib.report.test_report import parse_jsonl_log, generate_postman_report
        api_calls, events = parse_jsonl_log(self.log_file)
        if api_calls:
            report_path = generate_postman_report(api_calls, events, self.module_name)
            print(f"  📊 报告已生成: {report_path}")
    except Exception as e:
        print(f"  ⚠️ 报告生成失败: {e}")

    if failed_count > 0:
        sys.exit(1)                        # ← 新增：非零退出
```

### 修改 2：execute_pre_apis() 写 JSONL 日志

**文件**: `lib/runtime/test_runtime.py`（line ~685-757）

**改动**: 重构 `execute_pre_apis()`，每个前置 API 调用后写入 JSONL `api_call` 条目。

```python
def execute_pre_apis(self, session: requests.Session):
    """执行前置 API 链并写入 JSONL 日志。"""
    pre_apis = self.manifest.get("pre_apis", [])
    if not pre_apis:
        self._fallback_fetch_context(session)
        return

    print(f"  📋 执行 {len(pre_apis)} 个前置 API")

    for pre_api in pre_apis:
        api_id = pre_api.get("id", "unknown")
        api_name = pre_api.get("name", api_id)
        pathname = pre_api.get("pathname", "")
        method = pre_api.get("method", "GET")
        extracts = pre_api.get("extracts", [])

        if not pathname:
            print(f"  ⚠️ 跳过 {api_name}: 缺少 pathname")
            continue

        url = self.base_url + pathname
        print(f"  → {api_name}: {method} {pathname}")

        resp = None
        result = "passed"
        message = ""
        extracted = []

        try:
            if method.upper() == "GET":
                resp = session.get(url, timeout=10)
            else:
                body = pre_api.get("body_template", {})
                resp = session.request(method, url, json=body, timeout=10)

            if resp.status_code >= 300:
                result = "failed"
                message = f"HTTP {resp.status_code}"
            else:
                try:
                    resp_json = resp.json()
                except Exception:
                    result = "error"
                    message = "响应不是 JSON"
                    resp_json = None

                if resp_json:
                    for extract in extracts:
                        field_name = extract.get("name", "")
                        field_path = extract.get("path", "")
                        if not field_name or not field_path:
                            continue
                        value = self._extract_with_array_index(resp_json, field_path)
                        if value is not None:
                            self.state[field_name] = value
                            extracted.append(f"{field_name}={value}")
                        else:
                            print(f"    ⚠️ 无法提取 {field_name} (path: {field_path})")

                    if extracted:
                        message = f"提取: {', '.join(extracted)}"

        except Exception as e:
            result = "error"
            message = str(e)

        # ✅ 新增：写入 JSONL 日志
        self._log_pre_api_call(api_name, method, url, resp, result, message)

        if result == "passed":
            print(f"    ✅ {message}")
        else:
            print(f"    ❌ {message}")

    print(f"  ✅ 前置 API 执行完成")


def _log_pre_api_call(self, label: str, method: str, url: str,
                       resp, result: str, message: str):
    """将前置 API 调用写入 JSONL 日志。"""
    if not self.log_file:
        return
    try:
        resp_json = None
        resp_headers = {}
        resp_status = 0
        if resp:
            resp_status = resp.status_code
            resp_headers = dict(resp.headers)
            try:
                resp_json = resp.json()
            except Exception:
                resp_json = resp.text[:2000] if resp.text else None

        entry = {
            "type": "api_call",
            "seq": 0,
            "step_action": "pre_api",
            "step_label": f"[前置] {label}",
            "request": {
                "method": method,
                "url": url,
                "headers": {},
                "body": None,
            },
            "response": {
                "status": resp_status,
                "headers": resp_headers,
                "body": resp_json,
            },
            "assertion": result,
            "assertion_message": message,
        }
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
```

### 修改 3：跳过步骤写 JSONL 日志

**文件**: `lib/runtime/test_runtime.py` — `StepExecutor.execute()`（line ~158-162）

**改动**: 在 `requires` 检查失败时写入跳过日志。

```python
def execute(self, step_def: dict) -> bool:
    action = step_def.get("action", "unknown")
    label = step_def.get("label", action)

    print(f"\n[{label}] {step_def['api']['pathname']}")

    # 前置检查
    requires = step_def.get("requires", [])
    for req in requires:
        if not self.state.get(req):
            print(f"  ⚠️ 跳过[{label}]: {req} 为空")
            # ✅ 新增：写入跳过日志
            self._write_log(action, label, None, "skipped", f"{req} 为空")
            return False
    # ... 后续逻辑不变 ...
```

### 修改 4：HTML 报告支持 skipped 状态

**文件**: `lib/report/test_report.py`

**改动**: 在统计和渲染中添加 `skipped` 状态支持。

```python
# 统计（~line 94）
passed = sum(1 for c in api_calls if c["assertion"] == "passed")
failed = sum(1 for c in api_calls if c["assertion"] == "failed")
skipped = sum(1 for c in api_calls if c["assertion"] == "skipped")  # 新增
error = sum(1 for c in api_calls if c["assertion"] == "error")

# 新增 skipped 统计卡片
# <div class="stat skip"><b>{skipped}</b><span>跳过</span></div>

# 新增 badge 样式
# .b-skip { background: #f3f4f6; color: #6b7280; border: 1px dashed #d1d5db; }

# 渲染 skipped 步骤时使用灰色/虚线样式
if assertion == "skipped":
    badge_class = "b-skip"
    badge_text = "跳过"
    req_class = "skip"
```

### 修改 5：Stage 4 校验增强

**文件**: `module_discovery/run.py` — `_run_stage4_verify()`（line ~586）

**改动**: 双重检测——退出码 + 输出中的失败标记。

```python
def _run_script() -> subprocess.CompletedProcess:
    LOG.info(f"  运行脚本: {script.name}")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(script_dir), timeout=120,
    )
    for line in result.stdout.splitlines():
        if any(k in line for k in ("✅", "❌", "⚠️", "HTTP", "401", "Token", "Cookie", "assertion")):
            LOG.info(f"    {line.strip()}")
    return result

# ---- 改动：增强校验 ----
def _is_script_success(result: subprocess.CompletedProcess) -> bool:
    """判断脚本是否真正成功（不仅看退出码）。"""
    if result.returncode != 0:
        return False
    output = result.stdout + result.stderr
    # 检测认证失败（不可恢复）
    if "❌ 鉴权失败" in output or "❌ Cookie 鉴权失败" in output:
        return False
    # 检测所有业务 API 都失败
    fail_count = output.count("❌")
    pass_count = output.count("✅")
    if fail_count > 0 and pass_count <= 2:
        # 只有鉴权成功和前置 API 的 ✅，业务步骤全部 ❌
        return False
    return True

result = _run_script()

if _is_script_success(result):
    LOG.info("  ✅ 脚本运行成功")
    return True

if not _is_token_expired(result):
    LOG.warning(f"  ❌ 脚本运行失败 (非 Token 问题，退出码={result.returncode})")
    if result.stderr.strip():
        LOG.warning(f"    stderr: {result.stderr[:500]}")
    return False
# ... Token 刷新逻辑不变 ...
```

---

## 关键文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `lib/runtime/test_runtime.py` | 修改 | run() 非零退出；execute_pre_apis() 写 JSONL；skipped 写 JSONL |
| `lib/report/test_report.py` | 修改 | 支持 skipped 状态统计和渲染 |
| `module_discovery/run.py` | 修改 | _run_stage4_verify() 增强校验 |
| `module_discovery/gen_test.py` | 同步 | 确保 test_runtime.py 变更同步到 api/lib/ |

---

## 实施步骤

1. **修改 `lib/runtime/test_runtime.py`**
   - 修改 1: `run()` 添加 failed_count 统计和 sys.exit(1)
   - 修改 2: 重构 `execute_pre_apis()` + 新增 `_log_pre_api_call()`
   - 修改 3: `execute()` 中 requires 检查失败时写日志

2. **修改 `lib/report/test_report.py`**
   - 修改 4: 添加 skipped 状态统计和渲染样式

3. **修改 `module_discovery/run.py`**
   - 修改 5: 新增 `_is_script_success()` 替换简单的 returncode 检查

4. **同步 runtime lib**
   - 运行 `--stage 34 --offline` 自动同步 test_runtime.py 到 api/lib/

---

## 验证方式

### 1. 单元测试
```bash
python -m pytest tests/ -v --ignore=tests/test_feedback_loop.py --ignore=tests/test_run_history.py
```
预期：161 passed

### 2. Stage 4 校验测试（Token 过期场景）
```bash
# 清除 cookie 使 Token 过期
MSYS_NO_PATHCONV=1 python -m module_discovery.run \
  --project ecm-compute --stage 34 \
  --url "https://10.151.61.248/estack/web/estack/user-center/user-manage/user" \
  --module "用户管理" --export --offline
```
预期：
- 如果 cookie 有效：Stage 4 校验通过 → Stage 5 导出
- 如果 cookie 过期（401）：Stage 4 校验失败 → Stage 5 被跳过，日志输出 "❌ 脚本运行失败"

### 3. 报告完整性验证
运行脚本后检查 HTML 报告：
- 应包含 3 个 `[前置]` 条目（list, current_user, display_by_role）
- 应包含 12 个业务步骤（即使部分被标记为 skipped）
- 统计卡片应显示通过/失败/跳过分类数量

### 4. JSONL 日志验证
检查 `report/用户管理_API测试.jsonl`：
- 应包含 `step_action: "pre_api"` 的条目
- 应包含 `assertion: "skipped"` 的条目（如果有步骤被跳过）
- 总条目数应 ≥ 步骤总数（前置 + 业务）
