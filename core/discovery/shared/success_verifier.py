"""
shared/success_verifier.py — 操作成功验证（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 验证标准完全一致，避免 "Stage 1 通过但 Stage 2 失败" 的不一致

统一六层策略链（mode="message" 写操作语义）：
  0.   快速检查可见通知 — 语义分类（文本词表主判定 + className 补充标记）
  0.5. 失败文本优先检测 — 通知组件内搜索"失败"
  1.   成功文本匹配 — 通知组件内搜索"成功"
  2.   CSS 选择器 + 文本语义验证
  2.5. MutationObserver 捕获 — 语义分类（dialog 正文排除在外，F9）
  3.   成功 toast 可见性轮询（异步完成的慢 toast）
  F.   主动失败检测 — error toast / form validation（结构化通道）
  兜底: 仅 PROCESSING → 成功；中性消息 → 等待复查 → 默认成功（F8 规则 3b）；
        完全无消息 → URL 变化成功（F6）/ no_signal 失败（规则 4 非查询侧）

mode="read_complete"（只读查看/查询类语义）:
  跳过成功 toast 轮询与弹窗正文判定（只读内容防误报）；
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

# ★ P0-2（C-2 修复）：JS 侧词表单源注入——全部源自 const.NOTIFICATION_*_KEYWORDS。
# 旧实现：策略 0 走 const 全量词表（classify_notification），策略 0.5/1/3/F 用 JS
# 内嵌 6 词影子词表——同一通知可能被策略 0 判 neutral、被影子词表漏判，语义漂移。
# 统一后：四策略与 classify_notification 同词表、同小写匹配、同零失败排除。
_JS_FAIL_KEYWORDS = sorted(kw.lower() for kw in const.NOTIFICATION_FAILURE_KEYWORDS)
_JS_SUCCESS_KEYWORDS = sorted(kw.lower() for kw in const.NOTIFICATION_SUCCESS_KEYWORDS)


async def verify_operation_success(
    page: Page,
    operation_type: str = "",
    mode: str = "message",
    timeout: int = 5000,
    captured_messages: list = None,
    error_type: str = "",
    error_text: str = "",
    url_before: str = "",
) -> tuple:
    """验证操作是否成功（统一六层策略链）。

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型 (create/update/delete/lock/unlock/...)
        mode: 断言模式（KB assert_mode 契约，三处 UI 环节统一语义）
            - "message": 写操作语义——判定梯见最终判定注释（批五 F8 四规则）；
              无消息信号时按 URL 变化（F6）→ 无信号失败
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
        timeout: 轮询超时（毫秒）
        captured_messages: 由 get_captured_messages() 返回的捕获消息列表
        error_type: Stage 1 记录的预期失败类型（expected_failure 模式必需）
        error_text: Stage 1 记录的预期失败文本（expected_failure 模式可选，用于复现匹配展示）
        url_before: 操作点击前的页面 URL（可选）——完全无消息信号时用于
            URL 跳转成功判定（批五 F6，navigation/详情跳转类操作）

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
    # ★ 批五 F8 规则 3b：中性消息（无成败词）——等待复查信号源
    neutral_texts = []

    # ── 策略 0: 快速检查可见通知（语义分类 + className 补充标记） ──
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
                        if (text) return {
                            text: text.substring(0, 100),
                            className: String(el.className || '')
                        };
                    }
                }
            }
            return null;
        }""")
        if quick_result:
            _quick_text = quick_result.get("text", "")
            semantic = const.classify_notification(
                _quick_text, quick_result.get("className", ""))
            if semantic == "success":
                matched_method = f"quick_visible:success:{_quick_text[:30]}"
                LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
            elif semantic == "failure":
                failure_reason = _quick_text
                failure_type = "notification"
                LOG.info(f"    ✗ 检测到失败信号: {_quick_text[:60]}")
            elif semantic == "processing":
                has_processing = True
                LOG.debug(f"    [verify_success] 检测到处理中消息: {_quick_text[:60]}")
            else:
                # 中性消息（规则 3b 信号源）：记录文本，等待复查兜底处理
                neutral_texts.append(_quick_text)
                LOG.debug(f"    [verify_success] 检测到中性消息: {_quick_text[:60]}")
    except Exception:
        pass

    # ── 策略 0.5: 失败文本优先检测（限定在通知组件范围内） ──
    # ★ P0-2: failKw 由 const.NOTIFICATION_FAILURE_KEYWORDS 注入（同策略 0 词表），
    #   小写匹配 + 零失败排除，与 classify_notification 判定口径一致
    if matched_method is None and failure_reason is None:
        try:
            failure_text = await page.evaluate("""(failKw) => {
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
                            if (!text) continue;
                            // 零失败模式排除（"0个失败"类文本不算失败信号）
                            if (/0个失败|0个错误|失败[：:]\s*0|失败数为0|无失败/.test(text)) continue;
                            const lower = text.toLowerCase();
                            if (failKw.some(kw => lower.includes(kw))) {
                                return text.substring(0, 200);
                            }
                        }
                    }
                }
                return null;
            }""", _JS_FAIL_KEYWORDS)
            if failure_text:
                failure_reason = failure_text
                failure_type = "notification"
                LOG.info(f"    ✗ 通知组件中检测到失败: {failure_text[:60]}")
        except Exception:
            pass

    # ── 策略 1: 成功文本匹配（限定在通知组件内） ──
    # ★ P0-2: successKw 由 const.NOTIFICATION_SUCCESS_KEYWORDS 注入（同策略 0 词表）
    if matched_method is None and failure_reason is None:
        try:
            success_text = await page.evaluate("""(successKw) => {
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
                            if (text && successKw.some(kw => text.toLowerCase().includes(kw))) {
                                return text.substring(0, 200);
                            }
                        }
                    }
                }
                return null;
            }""", _JS_SUCCESS_KEYWORDS)
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
                                if (text) return {
                                    text: text.substring(0, 200),
                                    className: String(el.className || '')
                                };
                            }
                        }
                        return null;
                    }""", selector)
                    if text_found:
                        break
                    await page.wait_for_timeout(300)
                if text_found:
                    _t = text_found.get("text", "")
                    semantic = const.classify_notification(
                        _t, text_found.get("className", ""))
                    if semantic == "success":
                        matched_method = f"css:{selector}:{_t[:30]}"
                        LOG.info(f"    ✓ 操作成功验证通过: {matched_method}")
                        break
                    elif semantic == "failure":
                        failure_reason = _t
                        failure_type = f"css:{selector}"
                        LOG.info(f"    ✗ CSS选择器检测到失败: {_t[:60]}")
                        break
                    elif semantic == "processing":
                        has_processing = True
                    elif semantic == "neutral":
                        neutral_texts.append(_t)
            except Exception:
                continue

    # ── 策略 2.5: MutationObserver 捕获（语义分类 + className 补充标记） ──
    if matched_method is None and failure_reason is None:
        # 优先使用传入的 captured_messages
        if captured_messages:
            # ★ F9: 过滤 dialog 来源消息（type=dialog_context / source=dialog）
            # 弹窗正文是交互内容而非操作反馈，不参与成败判定
            _notification_msgs = [m for m in captured_messages
                                   if m.get('source') != 'dialog'
                                   and m.get('type') != 'dialog_context']
            # 失败优先
            for m in _notification_msgs:
                semantic = const.classify_notification(
                    m.get('text', ''), m.get('className', ''))
                if semantic == "failure":
                    failure_reason = m.get('text', '')
                    failure_type = "captured"
                    LOG.info(f"    ✗ 捕获消息中检测到失败: {m.get('text', '')[:60]}")
                    break
                elif semantic == "processing":
                    has_processing = True
            # 成功（失败已优先处理）
            if failure_reason is None:
                for m in _notification_msgs:
                    semantic = const.classify_notification(
                        m.get('text', ''), m.get('className', ''))
                    if semantic == "success":
                        matched_method = f"captured:success:{m.get('text', '')[:40]}"
                        LOG.info(f"    ✓ 操作成功验证通过（捕获瞬态消息）: {matched_method}")
                        break
                    elif semantic == "neutral":
                        neutral_texts.append(m.get('text', ''))
        else:
            # 从 JS 上下文读取
            try:
                captured_pairs = await page.evaluate("""() => {
                    if (!window.__captured_messages) return [];
                    const now = Date.now();
                    // ★ F9: 过滤 dialog 来源消息，不参与成败判定
                    return window.__captured_messages
                        .filter(m => now - m.timestamp < 30000
                                  && m.source !== 'dialog'
                                  && m.type !== 'dialog_context')
                        .map(m => ({text: m.text, className: m.className || ''}));
                }""")
                if captured_pairs:
                    for m in captured_pairs:
                        semantic = const.classify_notification(
                            m.get('text', ''), m.get('className', ''))
                        if semantic == "failure":
                            failure_reason = m.get('text', '')
                            failure_type = "captured"
                            LOG.info(f"    ✗ 捕获消息中检测到失败: {m.get('text', '')[:60]}")
                            break
                        elif semantic == "success" and matched_method is None:
                            matched_method = f"captured:success:{m.get('text', '')[:40]}"
                            LOG.info(f"    ✓ 操作成功验证通过（捕获瞬态消息）: {matched_method}")
                        elif semantic == "processing":
                            has_processing = True
                        elif semantic == "neutral":
                            neutral_texts.append(m.get('text', ''))
            except Exception:
                pass

    # ── 策略 3: 轮询等待成功 toast ──
    # ★ 批五 F9：dialog/message-box 正文关键词扫描移出判定通道——弹窗正文是
    #   交互内容而非操作反馈（删除弹窗警示文「不可恢复」曾被当失败信号，
    #   判定碰巧对但错误文本误导诊断；滞留弹窗由 F3 dialog_still_open 提供
    #   正面失败证据）。保留成功 toast 可见性轮询（异步完成的慢 toast）。
    # ★ read_complete 跳过：只读语义无需等待写操作反馈
    if matched_method is None and failure_reason is None and mode != "read_complete":
        for _poll in range(5):
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
    # ★ 批五 F9：dialog/message-box 正文关键词扫描移出判定通道（弹窗正文是
    #   交互内容而非操作反馈）；保留 error toast 与表单校验两类结构化错误通道。
    if matched_method is None and failure_reason is None:
        try:
            failure_signals = await page.evaluate("""() => {
                const results = [];

                // 1. el-message--error / el-message--warning（★ B2: 跳过 __op_baseline 残留）
                const errEls = document.querySelectorAll('.el-message--error, .el-message--warning');
                for (const el of errEls) {
                    if (el.__op_baseline) continue;
                    if (el.offsetParent !== null) {
                        results.push({source: 'error_message', text: el.textContent.trim().substring(0, 200)});
                        break;
                    }
                }

                // 2. 表单校验错误
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

    # ★ 批五 F8 规则 3b：有中性消息（无成败词）→ 等待复查 → 仍无 → 默认成功。
    #   中性消息本身是操作产生了反馈的证据（如「已提交申请」类 info toast），
    #   异步完成的成败信号（延迟 toast）在复查窗内出现则改判。
    if neutral_texts:
        _wait_ms = const.NEUTRAL_RECHECK_WAIT_MS
        LOG.info(f"    ⏳ [neutral-wait] 仅中性消息（{neutral_texts[0][:50]}），"
                 f"等待 {_wait_ms}ms 后复查（批五规则 3）")
        await page.wait_for_timeout(_wait_ms)
        # 复查 1：捕获消息重读（等待窗内新增的成败信号；失败优先）
        try:
            _recheck_msgs = await page.evaluate("""() => {
                if (!window.__captured_messages) return [];
                const now = Date.now();
                return window.__captured_messages
                    .filter(m => now - m.timestamp < 30000
                              && m.source !== 'dialog'
                              && m.type !== 'dialog_context')
                    .map(m => ({text: m.text, className: m.className || ''}));
            }""")
            for m in _recheck_msgs:
                semantic = const.classify_notification(
                    m.get('text', ''), m.get('className', ''))
                if semantic == "failure":
                    LOG.info(f"    ✗ [neutral-wait] 复查捕获到失败信号: {m.get('text', '')[:60]}")
                    return False, {
                        "matched_method": f"neutral_wait:failure:{m.get('text', '')[:30]}",
                        "failure_reason": m.get('text', ''),
                        "failure_type": "neutral_wait",
                        "has_processing": has_processing,
                    }
            for m in _recheck_msgs:
                semantic = const.classify_notification(
                    m.get('text', ''), m.get('className', ''))
                if semantic == "success":
                    LOG.info(f"    ✓ [neutral-wait] 复查捕获到成功信号: {m.get('text', '')[:60]}")
                    return True, {
                        "matched_method": f"neutral_wait:success:{m.get('text', '')[:30]}",
                        "failure_reason": None,
                        "failure_type": None,
                        "has_processing": has_processing,
                    }
        except Exception:
            pass
        # 复查 2：可见 toast 复扫（与策略 0 同口径，含 className 补充标记）
        try:
            _recheck_visible = await page.evaluate("""() => {
                const selectors = [
                    '.el-message', '.el-message--success', '.el-message--error',
                    '.el-notification', '.el-alert', '[role="alert"]'
                ];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        if (el.__op_baseline) continue;
                        if (el.offsetParent !== null) {
                            const text = (el.textContent || '').trim();
                            if (text) return {text: text.substring(0, 100),
                                               className: String(el.className || '')};
                        }
                    }
                }
                return null;
            }""")
            if _recheck_visible:
                semantic = const.classify_notification(
                    _recheck_visible.get("text", ""),
                    _recheck_visible.get("className", ""))
                if semantic == "failure":
                    LOG.info(f"    ✗ [neutral-wait] 复查可见失败: {_recheck_visible.get('text', '')[:60]}")
                    return False, {
                        "matched_method": f"neutral_wait:visible_failure:{_recheck_visible.get('text', '')[:30]}",
                        "failure_reason": _recheck_visible.get("text", ""),
                        "failure_type": "neutral_wait",
                        "has_processing": has_processing,
                    }
                if semantic == "success":
                    LOG.info(f"    ✓ [neutral-wait] 复查可见成功: {_recheck_visible.get('text', '')[:60]}")
                    return True, {
                        "matched_method": f"neutral_wait:visible_success:{_recheck_visible.get('text', '')[:30]}",
                        "failure_reason": None,
                        "failure_type": None,
                        "has_processing": has_processing,
                    }
        except Exception:
            pass
        # 仍中性/无新信号 → 默认成功（规则 3）
        LOG.info("    ✓ [neutral-wait] 复查仍无成败信号，按规则 3 默认成功")
        return True, {
            "matched_method": "neutral_wait_default_success",
            "failure_reason": None,
            "failure_type": None,
            "has_processing": has_processing,
        }

    # ★ 批五 F6：完全无消息信号 + URL 跳转 → navigation 类成功。
    #   详情页跳转/名称链接类操作（探针 v2 实证网络配置跳 vmDetail）无 toast
    #   无弹窗——URL 变化即业务反馈。仅无消息时参考（有消息永远优先）。
    if url_before and page.url != url_before:
        LOG.info(f"    ✓ [F6] 无消息信号但 URL 已跳转: {url_before[:50]} → {page.url[:50]}")
        return True, {
            "matched_method": "navigation_url_change",
            "failure_reason": None,
            "failure_type": None,
            "has_processing": has_processing,
        }

    # 完全无信号（message 模式）——规则 4 非查询失败侧（防填非法值假成功）
    LOG.warning("    ⚠️ 未检测到明确的成功/失败信号，标记为失败")
    return False, {
        "matched_method": "none",
        "failure_reason": "未检测到明确的成功/失败信号",
        "failure_type": "no_signal",
        "has_processing": False,
    }
