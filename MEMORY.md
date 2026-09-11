# 项目记忆

## Stage 1 Locator 传递修复 (2026-09-10)

Stage 1 探测成功后，playbook 中保存的 locator 必须是 Stage 1 实际使用的已验证 locator，而不是重新构建的。

**问题**：XPath 拆字匹配、JS 去空格匹配、对话框内按钮等特殊场景下，重新构建的 locator 与 Stage 1 实际使用的不一致，导致 Stage 2 回放失败。

**修复**：
- `submit_form_v2()` 返回 dict `{"text": ..., "locator": ...}` 而非字符串
- 所有 `_do_*()` 函数直接使用 `submit_result["locator"]` 和 `btn_click_result["locator"]`
- 详细文档：`module_discovery/STAGE1_LOCATOR_FIX_SUMMARY.md`

## Stage 2 Delete 操作修复 (2026-09-10)

**问题**：Stage 2 回放 delete 操作时，`confirm_dialog` 失败"确认按钮未找到或点击失败"。

**根因**：
1. `_do_delete` 只依赖 `_verify_operation_success` 检查 toast，未验证行是否真正消失
2. 批量删除需要先勾选 checkbox，但 Stage 1 `_ensure_row_selected` 和 Stage 2 `_step_select_row_checkbox` 的 JS 逻辑不一致（后者缺少 `offsetWidth > 0` 检查）
3. `_build_delete_steps` 未生成 `assert_row_disappeared` 步骤

**修复**：
- `_do_delete`: confirm_dialog 后直接检查行是否消失，作为最可靠的成功信号
- `_step_select_row_checkbox`: 增加 `offsetWidth > 0` 检查 + 幂等操作（已勾选则跳过，避免 toggle 取消勾选）
- `_build_delete_steps`: 生成 `assert_row_disappeared` 步骤
- `_step_click_button`: 增强诊断日志（按钮 disabled 状态检测）
- `_step_confirm_dialog`: 清理调试日志
- `capture_apis.py`: 增加页面崩溃恢复机制（`Page crashed` → 重新加载页面）
- `button_driver.py`: `close_dialog` 增加崩溃检测，避免在已崩溃页面上继续操作

**关键教训**：
- 前一个操作（如 migrate）勾选 checkbox 后，状态可能残留到下一个操作
- 必须检查 checkbox 是否已勾选，避免 toggle 取消勾选导致按钮 disabled
- 页面崩溃时需要恢复机制，不能假设页面始终可用
