"""
shared/message_capture.py — MutationObserver 消息捕获（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 统一注入逻辑，避免 Stage 1 和 Stage 2 各自写一套

调用方：
    - Stage 1: base_executor.py (操作前注入，操作后读取)
    - Stage 2: replay_engine.py (回放前注入，断言时读取)
"""

import logging
from playwright.async_api import Page

LOG = logging.getLogger(__name__)


async def install_message_capture(page: Page, pin: bool = False) -> None:
    """在页面注入 MutationObserver，捕获瞬态的 ElMessage / ElNotification 文本。

    某些操作（lock/unlock/reset/delete 等）成功后会弹出 ElMessage.success()，
    但该消息会在 ~3 秒后自动消失。等到验证操作结果时已经消失了。

    此函数在操作前注入，记录所有出现的消息文本到 window.__captured_messages。
    后续用 get_captured_messages() 读取。

    统一特性：
    - 支持 el-message / el-notification / ant-message / ant-notification
    - 异步文本延迟读取（Vue $notify 先 appendChild 空 div，后设文本）
    - 钉住通知（阻止自动移除，供 Stage 2 截图使用）★ 仅在 pin=True 时生效
    - 幂等安装（重复调用不重复注入）

    Args:
        page: Playwright Page 对象
        pin: 是否钉住通知（阻止自动消失）。Stage 1 传 False（只捕获文本），
             Stage 2 传 True（截图需要通知保持可见）
    """
    try:
        LOG.info(f"    [DIAG-install-capture] 安装消息捕获器 pin={pin}")
        await page.evaluate("""(shouldPin) => {
            // ★ 每次安装都重置捕获数组（方案 A：操作开始时清空，保证操作间隔离）
            // 上一个操作结束时可能还有残留 toast 文本，但 DOM 已消失不会重新捕获
            const oldCount = (window.__captured_messages || []).length;
            window.__captured_messages = [];
            console.log(`[MSG-CAPTURE] 清空消息数组 (之前有 ${oldCount} 条)`);

            if (!window.__pinned_notifications) {
                window.__pinned_notifications = [];
            }

            // 避免重复安装 observer
            if (window.__msg_capture_observer) {
                console.log('[MSG-CAPTURE] Observer 已存在，跳过安装');
                return;
            }
            console.log('[MSG-CAPTURE] 安装新的 MutationObserver');

            const SUCCESS_KEYWORDS = ['成功', 'success', 'Success', '完成', 'done'];
            const ERROR_KEYWORDS = ['失败', 'error', 'Error', '异常', '错误'];

            // 辅助函数：分类并存储捕获的消息
            function _pushCaptured(text, className) {
                if (!text) return;
                const isSuccess = SUCCESS_KEYWORDS.some(kw => text.includes(kw));
                const isError = ERROR_KEYWORDS.some(kw => text.includes(kw));
                const msgType = isSuccess ? 'success' : (isError ? 'error' : 'info');
                const msg = {
                    text: text.substring(0, 200),
                    type: msgType,
                    time: Date.now(),
                    timestamp: Date.now(),
                    className: className || '',
                    source: 'notification'
                };
                window.__captured_messages.push(msg);
                console.log(`[MSG-CAPTURE] 捕获通知: type=${msgType}, text="${text.substring(0, 60)}", total=${window.__captured_messages.length}`);
            }

            // 辅助函数：检查是否是 Message / Notification 组件节点
            function _isNotificationNode(node) {
                if (!node.classList) return false;
                if (node.classList.contains('el-message') ||
                    node.classList.contains('el-message--success') ||
                    node.classList.contains('el-message--error') ||
                    node.classList.contains('el-message--warning') ||
                    node.classList.contains('el-notification') ||
                    node.classList.contains('ant-message-notice') ||
                    node.classList.contains('ant-notification-notice')) {
                    return true;
                }
                if (node.querySelector && node.querySelector(
                    '.el-message, .el-notification, .ant-message-notice, .ant-notification-notice'
                )) {
                    return true;
                }
                return false;
            }

            // 辅助函数：检查是否是 Dialog 节点（只捕获文本，不钉住）
            function _isDialogNode(node) {
                if (!node.classList) return false;
                return node.classList.contains('el-dialog') ||
                       node.classList.contains('el-dialog__body') ||
                       node.classList.contains('el-message-box__message') ||
                       node.classList.contains('el-message-box');
            }

            window.__msg_capture_observer = new MutationObserver((mutations) => {
                console.log(`[MSG-CAPTURE] Observer 触发，共 ${mutations.length} 个 mutation`);
                for (const mutation of mutations) {
                    for (const node of mutation.addedNodes) {
                        if (node.nodeType !== 1) continue;
                        if (!node.classList) continue;

                        const isDialog = _isDialogNode(node);
                        const isNotification = _isNotificationNode(node);

                        // Dialog 节点：只捕获文本
                        if (isDialog && !isNotification) {
                            const text = (node.textContent || '').trim();
                            console.log(`[MSG-CAPTURE] 检测到 Dialog 节点: text="${text.substring(0, 60)}"`);
                            if (text) {
                                window.__captured_messages.push({
                                    text: text.substring(0, 200),
                                    type: 'info',
                                    time: Date.now(),
                                    timestamp: Date.now(),
                                    className: node.className,
                                    source: 'dialog'
                                });
                                console.log(`[MSG-CAPTURE] 捕获 Dialog 消息，total=${window.__captured_messages.length}`);
                            }
                            continue;
                        }

                        if (!isNotification) continue;

                        console.log(`[MSG-CAPTURE] 检测到 Notification 节点: className="${node.className}"`);
                        const text = (node.textContent || '').trim();
                        const cls = node.className || '';

                        if (text) {
                            console.log(`[MSG-CAPTURE] Notification 同步文本: "${text.substring(0, 60)}"`);
                            // 同步文本：直接存储
                            _pushCaptured(text, cls);
                        } else {
                            console.log(`[MSG-CAPTURE] Notification 异步文本，100ms 后读取`);
                            // 异步文本：Element UI $notify 先 appendChild 空 div，
                            // 后通过 Vue 响应式异步设置 message 文本。
                            // 延迟 100ms 重读。
                            const targetNode = node;
                            const targetCls = cls;
                            setTimeout(() => {
                                const delayedText = (targetNode.textContent || '').trim();
                                console.log(`[MSG-CAPTURE] Notification 延迟读取: "${delayedText.substring(0, 60)}"`);
                                _pushCaptured(delayedText, targetCls);
                            }, 100);
                        }

                        // ★ FIX-3: 仅在 shouldPin=true 时钉住通知（Stage 2 截图需要）
                        // Stage 1 传 shouldPin=false，让通知自然消失，避免干扰后续操作
                        if (shouldPin) {
                            // 钉住通知：强制可见 + 阻止自动移除
                            node.__pinned = true;

                            // 强制可见样式
                            node.style.setProperty('display', 'block', 'important');
                            node.style.setProperty('visibility', 'visible', 'important');
                            node.style.setProperty('opacity', '1', 'important');
                            node.style.setProperty('transform', 'none', 'important');
                            node.style.setProperty('transition', 'none', 'important');
                            node.style.setProperty('animation', 'none', 'important');

                            // 拦截 remove() 调用
                            const originalRemove = node.remove.bind(node);
                            node.remove = function() { /* 阻止移除 */ };
                            node.__originalRemove = originalRemove;

                            // 也拦截 parentNode.removeChild（Vue transition 可能用这个）
                            const parentNode = node.parentNode;
                            if (parentNode) {
                                const origRemoveChild = parentNode.removeChild.bind(parentNode);
                                parentNode.removeChild = function(child) {
                                    if (child === node && node.__pinned) return child;
                                    return origRemoveChild(child);
                                };
                                node.__origRemoveChild = origRemoveChild;
                                node.__pinnedParent = parentNode;
                            }

                            // 监听属性变更，覆盖任何隐藏尝试
                            const pinObserver = new MutationObserver(() => {
                                if (!node.__pinned) return;
                                node.style.setProperty('display', 'block', 'important');
                                node.style.setProperty('visibility', 'visible', 'important');
                                node.style.setProperty('opacity', '1', 'important');
                                node.style.setProperty('transform', 'none', 'important');
                                node.style.setProperty('transition', 'none', 'important');
                            });
                            pinObserver.observe(node, {
                                attributes: true,
                                attributeFilter: ['style', 'class']
                            });
                            node.__pinObserver = pinObserver;

                            // 子元素也强制可见
                            node.querySelectorAll('*').forEach(el => {
                                el.style.setProperty('visibility', 'visible', 'important');
                                el.style.setProperty('opacity', '1', 'important');
                            });

                            window.__pinned_notifications.push(node);
                        }
                    }
                }
            });

            window.__msg_capture_observer.observe(document.body, {
                childList: true,
                subtree: true
            });

            // 释放所有钉住的通知（截图后调用）
            window.__unpin_notifications = function() {
                const pinned = window.__pinned_notifications || [];
                for (const node of pinned) {
                    node.__pinned = false;
                    if (node.__originalRemove) {
                        node.remove = node.__originalRemove;
                        delete node.__originalRemove;
                    }
                    if (node.__pinnedParent && node.__origRemoveChild) {
                        node.__pinnedParent.removeChild = node.__origRemoveChild;
                        delete node.__origRemoveChild;
                        delete node.__pinnedParent;
                    }
                    if (node.__pinObserver) {
                        node.__pinObserver.disconnect();
                        delete node.__pinObserver;
                    }
                    node.style.removeProperty('display');
                    node.style.removeProperty('visibility');
                    node.style.removeProperty('opacity');
                    node.style.removeProperty('transform');
                    node.style.removeProperty('transition');
                    node.style.removeProperty('animation');
                    try { node.remove(); } catch(e) {}
                }
                window.__pinned_notifications = [];
            };
        }""", pin)
        LOG.debug(f"    ✓ MutationObserver 消息捕获已注入 (pin={pin})")
    except Exception as e:
        LOG.debug(f"    ⚠️ 消息捕获注入失败（不影响执行）: {e}")


async def get_captured_messages(page: Page, msg_type: str = None, clear: bool = False) -> list:
    """读取被捕获的消息。

    ★ 默认不清空（方案 A：只在下一个操作 _install_message_capture 时重置）
    操作过程中可多次读取同一 toast 信号，不丢失。

    Args:
        page: Playwright 页面对象
        msg_type: 过滤类型 ('success' / 'error' / 'info' / None=全部)
        clear: 是否清除已读取的消息（默认 False，操作间隔离由 _install_message_capture 保证）

    Returns:
        list of {"text": str, "type": str, "time": int, "source": str}
    """
    try:
        messages = await page.evaluate("""(args) => {
            const [filterType, doClear] = args;
            const msgs = window.__captured_messages || [];
            const filtered = filterType ? msgs.filter(m => m.type === filterType) : msgs;
            if (doClear) {
                window.__captured_messages = [];
            }
            return filtered;
        }""", [msg_type, clear])
        return messages or []
    except Exception as e:
        LOG.debug(f"    ⚠️ 读取捕获消息失败: {e}")
        return []


async def unpin_notifications(page: Page) -> None:
    """释放所有钉住的通知（截图后调用，让通知自然消失）。

    Args:
        page: Playwright 页面对象
    """
    try:
        await page.evaluate("""() => {
            if (window.__unpin_notifications) {
                window.__unpin_notifications();
            }
        }""")
    except Exception:
        pass
