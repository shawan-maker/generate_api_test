"""
shared/success_verifier.py — 操作成功验证（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 验证标准完全一致，避免 "Stage 1 通过但 Stage 2 失败" 的不一致

统一六层策略链（mode="message" 写操作语义）：
  0.   快速检查可见通知 — 语义分类
  0.5. 失败文本优先检测 — 通知组件内搜索"失败"
  1.   成功文本匹配 — 通知组件内搜索"成功"
  2.   CSS 选择器 + 文本语义验证
  2.5. MutationObserver 捕获 — 语义分类
  F.   主动失败检测 — dialog / error message / form validation
  兜底: 仅 PROCESSING → 成功；无信号 → 失败

mode="read_complete"（只读查看/查询类语义）:
  策略 3 与 F 的弹窗全文扫描跳过（只读内容防误报）；
  保留通知组件错误检测与表单校验（不吞错）；
  无失败信号 → 成功 (read_complete:no_failure)

调用方：
    - Stage 1: base_executor.py (_verify_operation_success)
    - Stage 2 / UI 脚本: replay_engine.py (_step_assert_success)
    - Stage 1 readonly 分支: crud_executor.py (统一入口，mode="read_complete")
"""

import logging
from playwright.async_api import Page
from .. import const

LOG = logging.getLogger(__name__)


async def verify_operation_success(
    page: Page,
    operation_type: str = "",
    mode: str = "message",
    strict: bool = False,
    timeout: int = 5000,
    captured_messages: list = None,
    error_type: str = "",
    error_text: str = "",
) -> tuple:
    """验证操作是否成功（统一六层策略链）。

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型 (create/update/delete/lock/unlock/...)
        mode: 断言模式（KB assert_mode 契约，三处 UI 环节统一语义）
            - "message": 写操作语义——需要正向成功信号；无信号判失败（现状行为）
            - "read_complete": 只读查看/查询类语义——交互序列完成即成功；
              跳过弹窗全文扫描（防只读内容误报），保留结构化错误通道（不吞错）；
              无失败信号判成功
            - "expected_failure"（★ D 轮新增）: Stage 1 标记失败的写操作语义——
              回放目标是复现该失败作为实证，而非寻找成功信号：
              · 捕获失败信号 → 预期失败复现实证通过（success=True，
                matched_method="expected_failure:reproduced:<捕获文本>"）
              · 捕获成功信号 → 预期失败未复现=意外通过（success=False，
                failure_type="unexpected_pass"，人工确认信号）
              · 无任何信号 → 按 error_type 分流：form_validation → 意外通过；
                其余（env_dependency 等，业务动作未被录制、回放无法感知环境墙）
                → 声明盲区（success=True，matched_method="expected_failure:blind_spot:<type>"，
                交由报告层如实标注"业务未闭环"，不计红线、不谎报成功）
        strict: 严格模式（需要正向成功信号，而非"无错误即成功"）
        timeout: 轮询超时（毫秒）
        captured_messages: 由 get_captured_messages() 返回的捕获消息列表
        error_type: Stage 1 记录的预期失败类型（expected_failure 模式必需）
        error_text: Stage 1 记录的预期失败文本（expected_failure 模式可选，用于复现匹配展示）

    Returns:
        (success: bool, detail: dict)
        detail 包含:
        - matched_method: str  匹配的方法标识
        - failure_reason: str  失败原因（如有）
        - failure_type: str    失败类型（如有）
        - has_processing: bool 是否只检测到处理中消息
    """
    matched_method = None
    failure_reason = None
    failure_type = None
    has_processing = False

    # ── 策略 0: 快速检查可见通知（语义分类） ──
    try:
        quick_result = await page.evaluate("""() => {
            const selectors = [
                '.el-message', '.el-message--success', '.el-message--error',
                '.el-notification', '.el-alert',
                '[role="alert"]'
            ];
            for (const sel of selectors) {
                // ★ B2: 遍历全部节点，跳过 __op_baseline（上一操作残留）
                const els = document.querySelectorAll(sel);
                for (const el of els) {
                    if (el.__op_baseline) continue;
                    if (el.offsetParent !== null) {
                        const text = (el.textContent || '').trim();
                        if (text) return text.substring(0, 100);
                    }
                }
            }
            return null;
        }""")
        if quick_result:
            semantic = const.classify_notification(quick_result)
            if semantic == "success":
                matched_method = f"quick_visible:success:{quick_result[:30]}"
                LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
            elif semantic == "failure":
                failure_reason = quick_result
                failure_type = "notification"
                LOG.info(f"    ✗ 检测到失败信号: {quick_result[:60]}")
            elif semantic == "processing":
                has_processing = True
                LOG.debug(f"    [verify_success] 检测到处理中消息: {quick_result[:60]}")
    except Exception:
        pass

    # ── 策略 0.5: 失败文本优先检测（限定在通知组件范围内） ──
    if matched_method is None and failure_reason is None:
        try:
            failure_text = await page.evaluate("""() => {
                const failKw = ['失败', '错误', '异常', 'error', '无权', '权限不足'];
                const selectors = [
                    '.el-message', '.el-message--error', '.el-message--warning',
                    '.el-notification', '.el-alert',
                    '[role="alert"]'
                ];
                // ★ B2: 遍历全部节点，跳过 __op_baseline（上一操作残留）
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (el.__op_baseline) continue;
                        if (el.offsetParent !== null) {
                            const text = (el.textContent || '').trim();
                            if (text && failKw.some(kw => text.includes(kw))) {
                                return text.substring(0, 200);
                            }
                        }
                    }
                }
                return null;
            }""")
            if failure_text:
                failure_reason = failure_text
                failure_type = "notification"
                LOG.info(f"    ✗ 通知组件中检测到失败: {failure_text[:60]}")
        except Exception:
            pass

    # ── 策略 1: 成功文本匹配（限定在通知组件内） ──
    if matched_method is None and failure_reason is None:
        try:
            success_text = await page.evaluate("""() => {
                const successKw = ['成功', '完成', '已保存', '已添加', '已删除', '已更新'];
                const selectors = [
                    '.el-message', '.el-message--success',
                    '.el-notification', '.el-alert',
                    '[role="alert"]'
                ];
                // ★ B2: 遍历全部节点，跳过 __op_baseline（上一操作残留）
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (el.__op_baseline) continue;
                        if (el.offsetParent !== null) {
                            const text = (el.textContent || '').trim();
                            if (text && successKw.some(kw => text.includes(kw))) {
                                return text.substring(0, 200);
                            }
                        }
                    }
                }
                return null;
            }""")
            if success_text:
                matched_method = f"notification_text:成功:{success_text[:30]}"
                LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
        except Exception:
            pass

    # ── 策略 2: CSS 选择器 + 文本语义验证 ──
    if matched_method is None and failure_reason is None:
        css_selectors = [
            ".el-message",
            ".el-notification",
            ".el-alert",
            "[role='alert']",
        ]
        for selector in css_selectors:
            try:
                # ★ B2: 轮询等待非基线通知（保留原 wait_for_selector 的 ~2s 等待语义，
                # 但跳过 __op_baseline 残留节点——page.wait_for_selector 无法表达该过滤，
                # 会让钉住的上一操作残留 toast 直接命中）
                text_found = None
                for _attempt in range(7):  # ~2s @300ms
                    text_found = await page.evaluate("""(sel) => {
                        const els = document.querySelectorAll(sel);
                        for (const el of els) {
                            if (el.__op_baseline) continue;
                            if (el.offsetParent !== null) {
                                const text = (el.textContent || '').trim();
                                if (text) return text.substring(0, 200);
                            }
                        }
                        return null;
                    }""", selector)
                    if text_found:
                        break
                    await page.wait_for_timeout(300)
                if text_found:
                    semantic = const.classify_notification(text_found)
                    if semantic == "success":
                        matched_method = f"css:{selector}:{text_found[:30]}"
                        LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
                        break
                    elif semantic == "failure":
                        failure_reason = text_found
                        failure_type = f"css:{selector}"
                        LOG.info(f"    ✗ CSS选择器检测到失败: {text_found[:60]}")
                        break
                    elif semantic == "processing":
                        has_processing = True
            except Exception:
                continue

    # ── 策略 2.5: MutationObserver 捕获（语义分类） ──
    if matched_method is None and failure_reason is None:
        # 优先使用传入的 captured_messages
        if captured_messages:
            # ★ 修复：过滤掉 dialog 来源的消息，只检查通知类消息
            # 确认框文本（如"确定开启黑名单功能，开启后将禁止..."）不应被当作失败信号
            _notification_msgs = [m for m in captured_messages if m.get('source') != 'dialog']
            _all_texts = [m.get('text', '') for m in _notification_msgs]
            # 失败优先
            for t in _all_texts:
                semantic = const.classify_notification(t)
                if semantic == "failure":
                    failure_reason = t
                    failure_type = "captured"
                    LOG.info(f"    ✗ 捕获消息中检测到失败: {t[:60]}")
                    break
                elif semantic == "processing":
                    has_processing = True
            # 成功（失败已优先处理）
            if failure_reason is None:
                for t in _all_texts:
                    semantic = const.classify_notification(t)
                    if semantic == "success":
                        matched_method = f"captured:success:{t[:40]}"
                        LOG.info(f"    ✓ 操作成功验证通过（捕获瞬态消息）: {matched_method}")
                        break
        else:
            # 从 JS 上下文读取
            try:
                captured_texts = await page.evaluate("""() => {
                    if (!window.__captured_messages) return [];
                    const now = Date.now();
                    // ★ 修复：过滤掉 dialog 来源的消息
                    return window.__captured_messages
                        .filter(m => now - m.timestamp < 30000 && m.source !== 'dialog')
                        .map(m => m.text);
                }""")
                if captured_texts:
                    for text in captured_texts:
                        semantic = const.classify_notification(text)
                        if semantic == "failure":
                            failure_reason = text
                            failure_type = "captured"
                            LOG.info(f"    ✗ 捕获消息中检测到失败: {text[:60]}")
                            break
                        elif semantic == "success" and matched_method is None:
                            matched_method = f"captured:success:{text[:40]}"
                            LOG.info(f"    ✓ 操作成功验证通过（捕获瞬态消息）: {matched_method}")
                        elif semantic == "processing":
                            has_processing = True
            except Exception:
                pass

    # ── 策略 3: 轮询等待 dialog/message-box 中的成功/错误消息 ──
    # ★ read_complete 跳过：弹窗全文扫描是"提交反馈弹窗"语义，
    #   只读弹窗内容（历史记录/业务详情）天然可能含失败关键词，不跳过必误报
    if matched_method is None and failure_reason is None and mode != "read_complete":
        for _poll in range(5):
            dialog_result = await page.evaluate(r"""() => {
                const successKw = ['成功', '完成', '已保存', '已添加', '已删除', '已更新'];
                const errKw = ['失败', '错误', '异常', 'error', '无权', '权限不足'];

                const containers = [
                    ...document.querySelectorAll('.el-message-box__wrapper:not([style*="display: none"])'),
                    ...document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])')
                ];

                for (const container of containers) {
                    if (container.offsetWidth === 0) continue;
                    const content = container.querySelector(
                        '.el-message-box__message, .el-message-box__content, .el-dialog'
                    );
                    if (!content) continue;
                    const text = content.textContent.trim();
                    if (!text) continue;

                    const zeroFailurePattern = /0个失败|0个错误|失败[：:]\s*0|失败数为0|无失败/;
                    const hasZeroFailure = zeroFailurePattern.test(text);

                    const hasSuccess = successKw.some(kw => text.includes(kw));
                    const hasError = errKw.some(kw => text.includes(kw));
                    if (hasError && !hasZeroFailure) {
                        return { type: 'error', text: text.substring(0, 200) };
                    }
                    if (hasSuccess) {
                        return { type: 'success', text: text.substring(0, 200) };
                    }
                }
                return null;
            }""")
            if dialog_result:
                if dialog_result.get('type') == 'success':
                    matched_method = f"dialog:success:{dialog_result['text'][:30]}"
                    LOG.info(f"    ✓ 弹窗中检测到成功: {dialog_result['text'][:80]}")
                    break
                else:
                    failure_reason = dialog_result.get('text', '')
                    failure_type = "dialog"
                    LOG.info(f"    ✗ 弹窗中检测到错误: {dialog_result['text'][:80]}")
                    break

            # 成功 toast 出现 → 立即跳出
            has_success_toast = await page.evaluate("""() => {
                // ★ B2: 遍历全部节点，跳过 __op_baseline（上一操作残留）
                const els = document.querySelectorAll('.el-message--success, .el-message .el-icon-success');
                for (const el of els) {
                    if (el.__op_baseline) continue;
                    if (el.offsetWidth > 0) return true;
                }
                return false;
            }""")
            if has_success_toast:
                matched_method = "toast:success_toast"
                break
            await page.wait_for_timeout(1000)

    # ── 策略 F: 主动失败检测 ──
    # ★ read_complete 模式：跳过 dialog/message-box 全文扫描（只读内容防误报），
    #   保留 error toast / 表单校验两类结构化错误通道（不吞真实错误）
    if matched_method is None and failure_reason is None:
        try:
            failure_signals = await page.evaluate("""(skipDialogScan) => {
                const failKw = ['失败', '错误', '异常', 'error', '无权', '权限不足'];
                const results = [];

                // 1. el-dialog 中的失败文本（read_complete 跳过——只读弹窗内容非错误反馈）
                if (!skipDialogScan) {
                    document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])')
                        .forEach(d => {
                            if (d.offsetWidth === 0) return;
                            const text = (d.textContent || '').trim();
                            if (failKw.some(kw => text.includes(kw))) {
                                results.push({source: 'dialog', text: text.substring(0, 200)});
                            }
                        });
                }

                // 2. el-message-box 中的失败文本（read_complete 跳过，同上）
                if (!skipDialogScan) {
                    document.querySelectorAll('.el-message-box__wrapper:not([style*="display: none"])')
                        .forEach(d => {
                            if (d.offsetWidth === 0) return;
                            const text = (d.textContent || '').trim();
                            if (failKw.some(kw => text.includes(kw))) {
                                results.push({source: 'message_box', text: text.substring(0, 200)});
                            }
                        });
                }

                // 3. el-message--error / el-message--warning（★ B2: 跳过 __op_baseline 残留）
                const errEls = document.querySelectorAll('.el-message--error, .el-message--warning');
                for (const el of errEls) {
                    if (el.__op_baseline) continue;
                    if (el.offsetParent !== null) {
                        results.push({source: 'error_message', text: el.textContent.trim().substring(0, 200)});
                        break;
                    }
                }

                // 4. 表单校验错误
                const formErrors = document.querySelectorAll('.el-form-item__error');
                const errorTexts = [];
                formErrors.forEach(e => {
                    if (e.offsetWidth > 0) {
                        errorTexts.push(e.textContent.trim());
                    }
                });
                if (errorTexts.length > 0) {
                    results.push({source: 'form_validation', text: errorTexts.join('; ')});
                }

                return results.length > 0 ? results : null;
            }""", mode == "read_complete")
            if failure_signals:
                failure_reason = failure_signals[0]["text"]
                failure_type = failure_signals[0]["source"]
                LOG.info(f"    ✗ 主动检测发现失败 ({failure_signals[0]['source']}): {failure_reason[:60]}")
        except Exception:
            pass

    # ── 最终判定 ──
    # ★ D 轮 expected_failure 模式：预期失败的复现实证定性
    #   （策略链 0~F 与 message 模式完全一致——写操作语境需要全量失败信号通道；
    #    仅最终定性反转：失败信号=复现实证，成功信号=意外通过）
    if mode == "expected_failure":
        if failure_reason:
            # 失败信号捕获 → 预期失败复现实证通过
            LOG.info(f"    ⛔ [expected_failure] 预期失败复现实证通过: {failure_reason[:60]}"
                     f"（期望 {error_type}: {error_text or '未记录'}）")
            return True, {
                "matched_method": f"expected_failure:reproduced:{failure_reason[:40]}",
                "failure_reason": None,
                "failure_type": None,
                "has_processing": has_processing,
            }
        if matched_method:
            # 成功信号 → 预期失败未复现（意外通过，人工确认信号）
            LOG.warning(f"    ⚠️ [expected_failure] 预期失败未复现——检测到成功信号: {matched_method}")
            return False, {
                "matched_method": "expected_failure:not_reproduced",
                "failure_reason": f"预期失败未复现——检测到成功信号"
                                  f"（期望 {error_type}: {error_text or '未记录'}）",
                "failure_type": "unexpected_pass",
                "has_processing": has_processing,
            }
        # 无任何信号 → 按 error_type 分流（与 CI 门禁口径对齐：
        # 「意外通过」检测仅 form_validation 启用，其余类型如实标注盲区）
        if error_type == "form_validation":
            LOG.warning("    ⚠️ [expected_failure] 预期失败未复现（回放无失败信号）"
                        "——环境可能已变化，需人工确认")
            return False, {
                "matched_method": "expected_failure:not_reproduced",
                "failure_reason": f"预期失败未复现——回放未捕获任何失败信号"
                                  f"（期望 {error_type}: {error_text or '未记录'}），环境可能已变化",
                "failure_type": "unexpected_pass",
                "has_processing": has_processing,
            }
        # env_dependency 等：业务动作未被录制（如上传墙），回放只重演交互骨架，
        # 永远无法感知墙是否消失——如实标注盲区，交由报告层按"业务未闭环"呈现
        LOG.info(f"    ⛔ [expected_failure] 回放无失败信号——{error_type or 'unknown'} "
                 f"属声明盲区（业务动作未被录制，无法证伪），按业务未闭环处理")
        return True, {
            "matched_method": f"expected_failure:blind_spot:{error_type or 'unknown'}",
            "failure_reason": None,
            "failure_type": None,
            "has_processing": has_processing,
        }

    if matched_method:
        return True, {
            "matched_method": matched_method,
            "failure_reason": None,
            "failure_type": None,
            "has_processing": has_processing,
        }

    if failure_reason:
        return False, {
            "matched_method": "failure_detected",
            "failure_reason": failure_reason,
            "failure_type": failure_type or "backend",
            "has_processing": has_processing,
        }

    # 兜底
    if has_processing:
        LOG.info(f"    ✓ 仅检测到处理中消息，标记为成功")
        return True, {
            "matched_method": "processing_only",
            "failure_reason": None,
            "failure_type": None,
            "has_processing": True,
        }

    # ★ read_complete（只读查看/查询类）: 交互序列已完成且无失败信号 → 成功
    # （结构化错误通道已在上游检测：error toast / 表单校验；弹窗全文已跳过）
    if mode == "read_complete":
        LOG.info("    ✓ [read_complete] 只读交互完成且无失败信号，标记为成功")
        return True, {
            "matched_method": "read_complete:no_failure",
            "failure_reason": None,
            "failure_type": None,
            "has_processing": False,
        }

    # 完全无信号（message 模式）
    LOG.warning("    ⚠️ 未检测到明确的成功/失败信号，标记为失败")
    return False, {
        "matched_method": "none",
        "failure_reason": "未检测到明确的成功/失败信号",
        "failure_type": "no_signal",
        "has_processing": False,
    }
