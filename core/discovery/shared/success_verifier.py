"""
shared/success_verifier.py — 操作成功验证（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 验证标准完全一致，避免 "Stage 1 通过但 Stage 2 失败" 的不一致

统一六层策略链：
  0.   快速检查可见通知 — 语义分类
  0.5. 失败文本优先检测 — 通知组件内搜索"失败"
  1.   成功文本匹配 — 通知组件内搜索"成功"
  2.   CSS 选择器 + 文本语义验证
  2.5. MutationObserver 捕获 — 语义分类
  F.   主动失败检测 — dialog / error message / form validation
  兜底: 仅 PROCESSING → 成功；无信号 → 失败

调用方：
    - Stage 1: base_executor.py (_verify_operation_success)
    - Stage 2: replay_engine.py (_step_assert_success)
"""

import logging
from playwright.async_api import Page
from .. import const

LOG = logging.getLogger(__name__)


async def verify_operation_success(
    page: Page,
    operation_type: str = "",
    strict: bool = False,
    timeout: int = 5000,
    captured_messages: list = None,
) -> tuple:
    """验证操作是否成功（统一六层策略链）。

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型 (create/update/delete/lock/unlock/...)
        strict: 严格模式（需要正向成功信号，而非"无错误即成功"）
        timeout: 轮询超时（毫秒）
        captured_messages: 由 get_captured_messages() 返回的捕获消息列表

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
                const el = document.querySelector(sel);
                if (el && el.offsetParent !== null) {
                    const text = (el.textContent || '').trim();
                    if (text) return text.substring(0, 100);
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
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        const text = (el.textContent || '').trim();
                        if (text && failKw.some(kw => text.includes(kw))) {
                            return text.substring(0, 200);
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
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetParent !== null) {
                        const text = (el.textContent || '').trim();
                        if (text && successKw.some(kw => text.includes(kw))) {
                            return text.substring(0, 200);
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
                el = await page.wait_for_selector(selector, state="visible", timeout=2000)
                if el:
                    text = await el.text_content()
                    text = (text or "").strip()
                    semantic = const.classify_notification(text)
                    if semantic == "success":
                        matched_method = f"css:{selector}:{text[:30]}"
                        LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
                        break
                    elif semantic == "failure":
                        failure_reason = text
                        failure_type = f"css:{selector}"
                        LOG.info(f"    ✗ CSS选择器检测到失败: {text[:60]}")
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
    if matched_method is None and failure_reason is None:
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
                const el = document.querySelector('.el-message--success, .el-message .el-icon-success');
                return el && el.offsetWidth > 0;
            }""")
            if has_success_toast:
                matched_method = "toast:success_toast"
                break
            await page.wait_for_timeout(1000)

    # ── 策略 F: 主动失败检测 ──
    if matched_method is None and failure_reason is None:
        try:
            failure_signals = await page.evaluate("""() => {
                const failKw = ['失败', '错误', '异常', 'error', '无权', '权限不足'];
                const results = [];

                // 1. el-dialog 中的失败文本
                document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])')
                    .forEach(d => {
                        if (d.offsetWidth === 0) return;
                        const text = (d.textContent || '').trim();
                        if (failKw.some(kw => text.includes(kw))) {
                            results.push({source: 'dialog', text: text.substring(0, 200)});
                        }
                    });

                // 2. el-message-box 中的失败文本
                document.querySelectorAll('.el-message-box__wrapper:not([style*="display: none"])')
                    .forEach(d => {
                        if (d.offsetWidth === 0) return;
                        const text = (d.textContent || '').trim();
                        if (failKw.some(kw => text.includes(kw))) {
                            results.push({source: 'message_box', text: text.substring(0, 200)});
                        }
                    });

                // 3. el-message--error / el-message--warning
                const errMsg = document.querySelector('.el-message--error, .el-message--warning');
                if (errMsg && errMsg.offsetParent !== null) {
                    results.push({source: 'error_message', text: errMsg.textContent.trim().substring(0, 200)});
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
            }""")
            if failure_signals:
                failure_reason = failure_signals[0]["text"]
                failure_type = failure_signals[0]["source"]
                LOG.info(f"    ✗ 主动检测发现失败 ({failure_signals[0]['source']}): {failure_reason[:60]}")
        except Exception:
            pass

    # ── 最终判定 ──
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

    # 完全无信号
    LOG.warning("    ⚠️ 未检测到明确的成功/失败信号，标记为失败")
    return False, {
        "matched_method": "none",
        "failure_reason": "未检测到明确的成功/失败信号",
        "failure_type": "no_signal",
        "has_processing": False,
    }
