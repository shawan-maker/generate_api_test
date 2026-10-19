"""
base_executor.py — Shared execution utilities for Stage 1 operations.

Contains button clicking, message capture, success verification,
and other reusable execution primitives used by crud_executor and
navigation_executor.
"""

import asyncio
import json
import logging
import time
from core.discovery import const
from core.discovery.replay.wait_helpers import (
    wait_for_dropdown, wait_for_dialog_dismissed, wait_for_dialog,
    wait_for_loading_complete, wait_for_table_ready,
    wait_for_navigation_complete,
)
from core.discovery.replay.form_filler import (
    FormFiller, read_form_errors, generate_fill_data, scan_form_fields,
    generate_fill_rules,
)

LOG = logging.getLogger("base_executor")


async def cleanup_ui_overlays(page):
    """清理页面上的模态对话框等 UI 残留。

    在 Stage 1 UI 探测前调用。
    只关闭**模态**对话框（el-dialog/el-message-box/el-drawer），
    不关闭内嵌的配置面板。
    """
    try:
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(300)

        try:
            await page.evaluate("""() => {
                // 只处理有遮罩层的对话框
                const modals = document.querySelectorAll('.v-modal');
                modals.forEach(modal => {
                    const wrapper = modal.nextElementSibling;
                    if (wrapper && (wrapper.classList.contains('el-dialog__wrapper') ||
                                    wrapper.classList.contains('el-drawer__wrapper'))) {
                        const closeBtn = wrapper.querySelector('.el-dialog__headerbtn, .el-drawer__close-btn');
                        const cancelBtn = wrapper.querySelector('.el-dialog__footer button:not(.el-button--primary)');
                        if (closeBtn) closeBtn.click();
                        else if (cancelBtn) cancelBtn.click();
                    }
                });

                // 关闭 el-message-box（确认弹窗）
                document.querySelectorAll('.el-message-box__wrapper').forEach(box => {
                    if (box.style.display !== 'none') {
                        const cancelBtn = box.querySelector('.el-message-box__btns button:first-child');
                        if (cancelBtn) cancelBtn.click();
                    }
                });

                // 移除遮罩层
                document.querySelectorAll('.v-modal').forEach(el => el.remove());
            }""")
        except Exception as e:
            LOG.warning(f"关闭模态对话框失败: {e}")

        await page.wait_for_timeout(500)

    except Exception as e:
        LOG.warning(f"清理 UI 残留时出错（不影响后续流程）: {e}")


async def wait_for_spa_ready(page, max_rounds: int = 6) -> bool:
    """等待 SPA 渲染就绪（表格/创建按钮出现）。

    从 run.py 迁入。轮询检查 .el-table 和数据行/创建按钮。

    改进（方案C）：当有创建按钮但无数据行时，额外等待 3 轮（6s），
    给数据加载时间。数据行出现则立即返回；grace period 结束后
    仍无数据则返回 True（后续 pre-warm 会处理）。

    Args:
        page: Playwright Page 对象
        max_rounds: 最大轮询次数（每轮 2 秒）

    Returns:
        bool: 是否在超时前就绪
    """
    LOG.info("  等待 SPA 渲染完成...")
    grace_start = None  # grace period 起始轮次
    GRACE_ROUNDS = 3    # 额外等待轮数

    for wait_round in range(max_rounds):
        state = await page.evaluate("""() => {
            const table = document.querySelector('.el-table');
            const rows = table ? table.querySelectorAll('.el-table__body-wrapper tbody tr') : [];
            const hasCreateBtn = !!Array.from(document.querySelectorAll('button, .el-button'))
                .find(b => {
                    const r = b.getBoundingClientRect();
                    const text = (b.textContent || '').trim();
                    return r.width > 0 && r.height > 0 &&
                           (text.includes('创建') || text.includes('新建') || text.includes('新增'));
                });
            return {
                hasTable: !!table,
                rowCount: rows.length,
                hasCreateBtn: hasCreateBtn,
                url: window.location.href,
            };
        }""")
        LOG.info(f"  [{wait_round+1}/{max_rounds}] table={state['hasTable']} rows={state['rowCount']} "
                 f"createBtn={state['hasCreateBtn']} url={state['url'][:60]}")

        # 有数据行 → 立即就绪
        if state['hasTable'] and state['rowCount'] > 0:
            LOG.info("  SPA 渲染完成（有数据行）")
            return True

        # 有创建按钮但无数据行 → grace period
        if state['hasTable'] and state['hasCreateBtn']:
            if grace_start is None:
                grace_start = wait_round
                LOG.info("  有创建按钮，等待数据加载...")
            elif wait_round - grace_start >= GRACE_ROUNDS:
                LOG.info(f"  SPA 渲染完成（grace period {GRACE_ROUNDS} 轮结束，rows={state['rowCount']}）")
                return True

        # 被重定向到登录页
        if "/login" in state['url']:
            LOG.error("  被重定向到登录页，cookie 已失效")
            return False

        await page.wait_for_timeout(2000)

    LOG.warning("  SPA 渲染等待超时，继续尝试")
    return False


# ★ 统一使用 shared/marker_utils 中的实现
# 保留此函数作为兼容层，内部调用统一实现
async def _extract_fallback_marker(page) -> str | None:
    """从表格中提取第一行的名称作为 fallback marker。

    当 create 失败时，行操作可以用已存在的数据行。

    注意：此函数已迁移至 core.discovery.shared.marker_utils.extract_marker_from_row
    此处保留兼容层，内部调用统一实现。
    """
    from core.discovery.shared.marker_utils import extract_marker_from_row
    return await extract_marker_from_row(page)


async def _ensure_row_selected(page, marker: str | None = None) -> bool:
    """确保表格中至少有一行被勾选（toolbar 行操作需要）。

    某些系统要求先勾选 checkbox 才能点击工具栏的"锁定/解锁/重置"按钮。

    Args:
        page: Playwright Page 对象
        marker: 如果有 marker，勾选包含 marker 的行

    Returns:
        bool: 是否成功勾选
    """
    try:
        if marker:
            # 勾选包含 marker 的行
            checked = await page.evaluate(f"""(text) => {{
                const rows = document.querySelectorAll('.el-table__body tr');
                for (const row of rows) {{
                    if ((row.textContent || '').includes(text)) {{
                        const cb = row.querySelector('.el-checkbox__input, input[type="checkbox"]');
                        if (cb && cb.offsetWidth > 0) {{
                            cb.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }}""", marker)
        else:
            # 勾选第一行
            checked = await page.evaluate("""() => {
                const cb = document.querySelector(
                    '.el-table__body tr:first-child .el-checkbox__input, '
                    + '.el-table__body tr:first-child input[type="checkbox"]');
                if (cb && cb.offsetWidth > 0) {
                    cb.click();
                    return true;
                }
                return false;
            }""")
        return bool(checked)
    except Exception:
        return False


async def _try_fill_empty_selects(page, empty_field_labels: list):
    """尝试填充弹窗中的空 select 字段。

    使用 FormFiller 扫描表单 → 过滤出空 select → 调用 MultiStepExecutor 填充。
    只有所有字段都填充失败时才返回 False。

    Args:
        page: Playwright page 对象
        empty_field_labels: 空字段 label 列表（来自诊断 JS）

    Returns:
        (是否至少成功填充了一个, 填充详情列表)
        详情包含: label, kb_category, selector, is_editable, option_text
    """
    from core.discovery.replay.form_filler import FormFiller

    try:
        form_filler = FormFiller(page)

        # 扫描当前弹窗中的表单字段
        fields = await form_filler.scan_form_fields_v2()

        # 过滤出需要填充的空 select 字段
        target_fields = []
        for field in fields:
            label = field.get("label", "")
            kb_cat = field.get("kb_category", "")

            # 只处理 el-select / el-cascader 类型
            if kb_cat not in ("el-select", "el-cascader"):
                continue

            # 匹配空字段列表中的 label（去除冒号后模糊匹配）
            label_clean = label.replace("：", "").replace(":", "")
            matched = any(
                label_clean in empty_label or empty_label in label_clean
                for empty_label in empty_field_labels
            )
            if matched:
                target_fields.append(field)

        if not target_fields:
            LOG.debug(f"    未找到匹配的空 select 字段（扫描到 {len(fields)} 个字段）")
            return False, []

        LOG.info(f"    找到 {len(target_fields)} 个空 select 字段，开始填充...")

        # 逐个填充，每填一个点击该字段 label 关闭下拉面板，防止遮挡下一个 select
        # 注意：不能按 Escape，因为 el-dialog 默认 close-on-press-escape=true 会关闭整个对话框
        filled_count = 0
        details = []
        for field in target_fields:
            single_filled, single_details, _ = await form_filler.fill_multi_step_fields(
                [field], framework="element-ui"
            )
            filled_count += single_filled
            details.extend(single_details)

            # 填充后点击该字段的 label 关闭下拉面板
            label_text = field.get("label", "").replace("：", "").replace(":", "")
            try:
                await page.evaluate(f"""(labelText) => {{
                    const labels = document.querySelectorAll('.el-form-item__label');
                    for (const l of labels) {{
                        if (l.textContent.trim().includes(labelText)) {{
                            l.click();
                            return true;
                        }}
                    }}
                    return false;
                }}""", label_text)
                await page.wait_for_timeout(300)
            except Exception:
                pass

        for d in details:
            if d.get("option_text"):
                LOG.info(f"    ✓ 成功填充 {d['label']} → {d['option_text']}")
            elif d.get("skipped_reason") == "no_options":
                LOG.warning(f"    ✗ {d['label']} 无可选选项")
            else:
                LOG.warning(f"    ✗ 填充 {d['label']} 失败")

        return filled_count > 0, details

    except Exception as e:
        LOG.warning(f"    尝试填充 select 时出错: {e}")
        return False, []


async def _ensure_switch_enabled(page) -> None:
    """检测页面中的 el-switch，确保其处于开启状态。

    某些页面（如黑白名单）的状态开关控制着操作按钮的可用性：
    switch=OFF → 新增等按钮 disabled。此函数在点击操作按钮前调用，
    自动开启关闭的 switch 并处理可能的确认弹框。

    仅处理当前可见 tab-pane 中的 switch，不影响隐藏 tab。

    Args:
        page: Playwright Page 对象
    """
    try:
        switch_info = await page.evaluate("""() => {
            // 优先找当前可见 tab-pane 中的 switch
            const panes = document.querySelectorAll('.el-tab-pane');
            if (panes.length > 0) {
                for (const p of panes) {
                    if (window.getComputedStyle(p).display === 'none') continue;
                    const sw = p.querySelector('.el-switch');
                    if (sw) {
                        return {
                            found: true,
                            isChecked: sw.classList.contains('is-checked'),
                            inTabPane: true
                        };
                    }
                }
                return { found: false };
            }
            // 没有 tab-pane 时，找全局可见 switch
            const sw = document.querySelector('.el-switch');
            if (sw && sw.offsetWidth > 0) {
                return {
                    found: true,
                    isChecked: sw.classList.contains('is-checked'),
                    inTabPane: false
                };
            }
            return { found: false };
        }""")

        if not switch_info.get("found") or switch_info.get("isChecked"):
            return  # 无 switch 或已开启，跳过

        LOG.info("  检测到 switch 处于关闭状态，尝试开启...")

        # 点击开启 switch（使用 force 避免被 tooltip 遮挡）
        if switch_info.get("inTabPane"):
            sw = await page.query_selector(
                '.el-tab-pane:not([style*="display: none"]) .el-switch:not(.is-checked), '
                '.el-tab-pane .el-switch:not(.is-checked)'
            )
        else:
            sw = await page.query_selector('.el-switch:not(.is-checked)')

        if not sw:
            return

        await sw.click(force=True)
        await page.wait_for_timeout(1000)

        # 处理可能的确认弹框（关闭 switch 时可能弹出确认框）
        confirm_btn = await page.query_selector(
            '.el-message-box__wrapper:not([style*="display: none"]) '
            '.el-message-box__btns .el-button--primary'
        )
        if confirm_btn:
            await confirm_btn.click()
            await page.wait_for_timeout(1500)
            LOG.info("  switch 开启确认弹框已处理")
        else:
            LOG.info("  switch 已开启（无确认弹框）")

    except Exception as e:
        LOG.debug(f"  _ensure_switch_enabled 异常: {e}")


async def _click_button_escalating(page, btn_text: str) -> dict:
    """降级策略点击按钮：Playwright CSS → XPath 拆字符 → JS 去空格 → force → 坐标。

    优先使用 CSS has-text 子串匹配；若失败则使用 XPath 拆字符匹配
    (处理 "确 定" 等带空格的按钮文本)；再失败则 JS 去空格匹配。

    Returns:
        dict: {"clicked": bool, "strategy": str, "tag": str, "text": str}
        - clicked: 是否成功点击
        - strategy: 使用的策略 (playwright/xpath/js/force/coord)
        - tag: 成功点击的元素标签
        - text: 按钮原始文本
    """
    result = {"clicked": False, "strategy": "", "tag": "", "text": btn_text,
              "click_strategy": "", "actual_text": btn_text, "locator": ""}
    if not btn_text:
        return result

    from core.discovery.replay.locator_helpers import safe_css
    hidden_css = const.HIDDEN_FILTERS_CSS.get('element-ui', const.HIDDEN_FILTERS_CSS['_universal'])

    # 策略 1: Playwright 原生点击（CSS has-text，带隐藏过滤）
    try:
        enhanced = safe_css(
            f'button:has-text("{btn_text}"), span:has-text("{btn_text}"), '
            f'a:has-text("{btn_text}")'
        )
        locator = page.locator(enhanced).first
        if await locator.count() > 0:
            # 获取实际匹配的元素标签（按优先级：button > a > span）
            # 避免 span 在 DOM 中排在 button 之前时被优先选中
            # 同时记录 disabled 状态，优先选择 enabled 元素，但不静默降级
            actual_info = await page.evaluate(f"""() => {{
                const els = document.querySelectorAll('button, span, a');
                const candidates = [];
                for (const el of els) {{
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
                    const tabPane = el.closest('.el-tab-pane');
                    if (tabPane && window.getComputedStyle(tabPane).display === 'none') continue;
                    if ((el.textContent || '').includes({json.dumps(btn_text)})) {{
                        const isDisabled = el.disabled || el.classList.contains('is-disabled');
                        candidates.push({{ el, isDisabled }});
                    }}
                }}
                // 优先选择 enabled 的 BUTTON，其次 enabled 的 A，其次 enabled 的 SPAN
                // 如果都没有 enabled，才考虑 disabled 元素（仍然按 button > a > span 优先级）
                const enabledButton = candidates.find(c => c.el.tagName === 'BUTTON' && !c.isDisabled);
                const enabledLink = candidates.find(c => c.el.tagName === 'A' && !c.isDisabled);
                const enabledSpan = candidates.find(c => c.el.tagName === 'SPAN' && !c.isDisabled);

                const chosen = enabledButton || enabledLink || enabledSpan
                            || candidates.find(c => c.el.tagName === 'BUTTON')
                            || candidates.find(c => c.el.tagName === 'A')
                            || candidates.find(c => c.el.tagName === 'SPAN');

                return chosen ? {{
                    tag: chosen.el.tagName.toLowerCase(),
                    isDisabled: chosen.isDisabled
                }} : {{ tag: 'button', isDisabled: false }};
            }}""")
            actual_tag = actual_info["tag"]
            is_disabled = actual_info["isDisabled"]

            if is_disabled:
                LOG.warning(f"  按钮 '{btn_text}' 处于禁用状态，点击可能无效")
            # 点击时优先点击 button 元素（使用精确的 tag 定位器而非通用 .first）
            click_locator = page.locator(
                safe_css(f'{actual_tag}:has-text("{btn_text}")')
            ).first
            await click_locator.click(timeout=3000)
            verified_locator = f'{actual_tag}:has-text("{btn_text}")'
            result.update({"clicked": True, "strategy": "playwright", "tag": actual_tag,
                           "click_strategy": "playwright", "actual_text": btn_text,
                           "locator": verified_locator})
            return result
    except Exception:
        pass

    # 策略 2: XPath 拆字符匹配 (处理 "确 定" 等带空格的按钮文本)
    try:
        hidden_filter = const.HIDDEN_FILTERS.get('element-ui', const.HIDDEN_FILTERS['_universal'])
        chars_match = " and ".join(f"contains(.,'{c}')" for c in btn_text)
        xpath = f"//button[{chars_match} and {hidden_filter}]"
        xpath_btn = page.locator(f"xpath={xpath}").first
        if await xpath_btn.count() > 0:
            # 获取实际文本（只取直接文本节点，可能有空格）
            actual_text = await page.evaluate(f"""() => {{
                const btns = document.querySelectorAll('button');
                for (const btn of btns) {{
                    let directText = '';
                    for (const node of btn.childNodes) {{
                        if (node.nodeType === Node.TEXT_NODE) {{
                            directText += node.textContent.trim();
                        }}
                    }}
                    const text = directText || btn.textContent.trim();
                    if ({' and '.join(f"text.includes('{c}')" for c in btn_text)}) {{
                        return text;
                    }}
                }}
                return {json.dumps(btn_text)};
            }}""")
            await xpath_btn.click(timeout=3000)
            result.update({"clicked": True, "strategy": "xpath", "tag": "button",
                           "click_strategy": "xpath", "actual_text": actual_text,
                           "locator": f"xpath={xpath}"})
            return result
    except Exception:
        pass

    # 策略 3: JS 去空格匹配 (strip all whitespace then compare)
    try:
        clicked_info = await page.evaluate("""(text) => {
            const target = text.replace(/\\s+/g, '');
            const elements = document.querySelectorAll('button, span, a, .el-button');
            for (const el of elements) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.disabled || el.classList.contains('is-disabled')) continue;
                if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
                const tabPane = el.closest('.el-tab-pane');
                if (tabPane && window.getComputedStyle(tabPane).display === 'none') continue;
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target)) {
                    el.click();
                    // 只提取直接文本节点
                    let directText = '';
                    for (const node of el.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            directText += node.textContent.trim();
                        }
                    }
                    const actualText = directText || el.textContent.trim();
                    return {clicked: true, tag: el.tagName.toLowerCase(), text: actualText};
                }
            }
            return null;
        }""", btn_text)
        if clicked_info:
            actual_tag = clicked_info["tag"]
            actual_text = clicked_info.get("text", btn_text).strip()
            # 保留原始 DOM 文本（含空格），Stage 2 回放时精确匹配
            verified_locator = f'{actual_tag}:has-text("{actual_text}")'
            result.update({"clicked": True, "strategy": "js", "tag": actual_tag,
                           "click_strategy": "js", "actual_text": actual_text,
                           "locator": verified_locator})
            return result
    except Exception:
        pass

    # 策略 4: force click（CSS，带隐藏过滤）
    try:
        enhanced = safe_css(
            f'button:has-text("{btn_text}"), span:has-text("{btn_text}")'
        )
        # 获取实际匹配的元素标签和文本（只取直接文本节点）
        match_info = await page.evaluate(f"""() => {{
            const els = document.querySelectorAll('button, span, a');
            for (const el of els) {{
                if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
                const tabPane = el.closest('.el-tab-pane');
                if (tabPane && window.getComputedStyle(tabPane).display === 'none') continue;
                if ((el.textContent || '').includes({json.dumps(btn_text)})) {{
                    let directText = '';
                    for (const node of el.childNodes) {{
                        if (node.nodeType === Node.TEXT_NODE) {{
                            directText += node.textContent.trim();
                        }}
                    }}
                    const text = directText || el.textContent.trim();
                    return {{tag: el.tagName.toLowerCase(), text: text}};
                }}
            }}
            return {{tag: 'button', text: {json.dumps(btn_text)}}};
        }}""")
        await page.click(enhanced, force=True, timeout=3000)
        actual_tag = match_info.get("tag", "button")
        actual_text = match_info.get("text", btn_text).strip()
        verified_locator = f'{actual_tag}:has-text("{actual_text}")'
        result.update({"clicked": True, "strategy": "force", "tag": actual_tag,
                       "click_strategy": "force", "actual_text": actual_text,
                       "locator": verified_locator})
        return result
    except Exception:
        pass

    # 策略 5: 坐标点击（JS 去空格 + 坐标）
    try:
        rect = await page.evaluate("""(text) => {
            const target = text.replace(/\\s+/g, '');
            const elements = document.querySelectorAll('button, span, a, .el-button');
            for (const el of elements) {
                if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                if (el.disabled || el.classList.contains('is-disabled')) continue;
                if (el.closest('.is-hidden') || el.closest('[style*="display: none"]')) continue;
                const tabPane = el.closest('.el-tab-pane');
                if (tabPane && window.getComputedStyle(tabPane).display === 'none') continue;
                const elText = el.textContent.trim().replace(/\\s+/g, '');
                if (elText.includes(target)) {
                    const r = el.getBoundingClientRect();
                    // 只提取直接文本节点
                    let directText = '';
                    for (const node of el.childNodes) {
                        if (node.nodeType === Node.TEXT_NODE) {
                            directText += node.textContent.trim();
                        }
                    }
                    const actualText = directText || el.textContent.trim();
                    return {x: r.x + r.width / 2, y: r.y + r.height / 2, tag: el.tagName.toLowerCase(), text: actualText};
                }
            }
            return null;
        }""", btn_text)
        if rect:
            await page.mouse.click(rect["x"], rect["y"])
            actual_tag = rect["tag"]
            actual_text = rect.get("text", btn_text).strip()
            # 保留原始 DOM 文本（含空格），Stage 2 回放时精确匹配
            verified_locator = f'{actual_tag}:has-text("{actual_text}")'
            result.update({"clicked": True, "strategy": "coord", "tag": actual_tag,
                           "click_strategy": "coord", "actual_text": actual_text,
                           "locator": verified_locator})
            return result
    except Exception:
        pass

    return result


# ============================================================
# _observe_post_click — 统一的按钮点击后场景检测
# ============================================================

async def _observe_post_click(page, url_before: str, timeout_ms: int = 3000,
                               new_page_event: asyncio.Event = None,
                               new_page_ref: list = None) -> dict:
    """统一观察按钮点击后发生了什么，返回结构化的场景描述。

    检测优先级：新标签页 → 页面跳转 → 弹窗/抽屉/确认框 → Toast → 无变化

    用法示例::

        # 在 click 前注册新标签页监听
        new_page_event = asyncio.Event()
        new_page_ref = [None]
        def on_page(p):
            new_page_ref[0] = p
            new_page_event.set()
        page.context.on("page", on_page)

        url_before = page.url
        await click_button(...)

        obs = await _observe_post_click(page, url_before,
                                         new_page_event=new_page_event,
                                         new_page_ref=new_page_ref)

    Args:
        page: Playwright Page 对象
        url_before: 点击前的 URL（用于检测页面跳转）
        timeout_ms: 最大等待时间（毫秒），默认 3000
        new_page_event: asyncio.Event，在 ``context.on("page")`` 回调中 set
        new_page_ref: ``[Page]`` 列表，在回调中赋值新 Page

    Returns:
        dict with keys:
        - scenario: ``"new_tab"`` | ``"page_nav"`` | ``"dialog"`` | ``"toast"`` | ``"none"``
        - container_type: ``"el-dialog"`` | ``"el-drawer"`` | ``"el-message-box"`` | ``"el-popconfirm"`` | ``""``
        - container_title: 弹窗标题
        - has_form: 弹窗/页面内是否有表单
        - url_changed: bool
        - new_page: 新标签页的 Page 对象（如有）
        - toast_text: toast 消息文本
        - toast_type: ``"success"`` | ``"error"`` | ``"warning"`` | ``""``
        - dialog_fill_needed: 弹窗中是否有未填充的 el-select
        - actual_state: 来自 ``_check_precondition_state`` 的原始状态字符串
    """
    from core.discovery.ui_scanner.button_detector import _check_precondition_state

    # 初始化默认返回
    result = {
        "scenario": "none",
        "container_type": "",
        "container_title": "",
        "has_form": False,
        "url_changed": False,
        "new_page": None,
        "toast_text": "",
        "toast_type": "",
        "dialog_fill_needed": False,
        "actual_state": "",
    }

    # ── ① 等待场景出现 ──
    # 分两阶段等待：先短暂等待给快速反应场景（dialog/toast），
    # 再检查 loading 完成后的最终状态
    wait_s = min(timeout_ms / 1000.0, 3.0)

    # 如果有新标签页事件，先等它（最多 1.5s，不浪费时间在非新标签场景）
    if new_page_event is not None:
        try:
            await asyncio.wait_for(new_page_event.wait(), timeout=1.5)
        except (asyncio.TimeoutError, Exception):
            pass

    # 检查新标签页
    if new_page_ref and new_page_ref[0] is not None:
        new_pg = new_page_ref[0]
        try:
            await new_pg.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        LOG.info(f"    [observe] 检测到新标签页: {new_pg.url[:80]}")
        result["scenario"] = "new_tab"
        result["new_page"] = new_pg
        return result

    # 标准等待（给 dialog/toast/page-nav 渲染时间）
    await page.wait_for_timeout(min(1500, int(wait_s * 1000)))
    try:
        await wait_for_loading_complete(page)
    except Exception:
        pass

    # ── ② 页面跳转？ ──
    url_after = page.url
    if url_before and url_before != url_after:
        has_form = await _check_page_has_form(page)
        LOG.info(f"    [observe] 检测到页面跳转: {url_before[:40]} → {url_after[:40]}, has_form={has_form}")
        result["scenario"] = "page_nav"
        result["url_changed"] = True
        result["has_form"] = has_form
        return result

    # ── ③ 弹窗/抽屉/确认框？ ──
    state = await _check_precondition_state(page, {"type": "dialog"})
    if state.get("success"):
        actual_state = state.get("actual_state", "")
        container_type = actual_state.split(":")[0].strip() if actual_state else ""
        container_title = actual_state.split(":", 1)[1].strip() if ":" in actual_state else ""

        has_form = False
        dialog_fill_needed = False
        if container_type in ("drawer", "dialog"):
            has_form = await _check_dialog_has_form(page, container_type)
            dialog_fill_needed = await _check_dialog_has_empty_selects(page, container_type)

        LOG.info(f"    [observe] 检测到弹窗: type={container_type}, title='{container_title}', "
                 f"has_form={has_form}, fill_needed={dialog_fill_needed}")
        result["scenario"] = "dialog"
        result["container_type"] = container_type
        result["container_title"] = container_title
        result["has_form"] = has_form
        result["dialog_fill_needed"] = dialog_fill_needed
        result["actual_state"] = actual_state
        return result

    # ── ④ Toast/Message？ ──
    messages = await _get_captured_messages(page)
    if messages:
        first_msg = messages[0]
        msg_text = first_msg.get("text", "")
        msg_type = first_msg.get("type", "")
        LOG.info(f"    [observe] 检测到 Toast: type={msg_type}, text='{msg_text[:60]}'")
        result["scenario"] = "toast"
        result["toast_text"] = msg_text
        result["toast_type"] = msg_type
        # 返回所有捕获的消息供后续分析
        result["all_messages"] = messages
        return result

    # ── ⑤ 无变化 ──
    LOG.info(f"    [observe] 未检测到明显变化（可能是即时操作或需要更长等待时间）")
    return result


async def _check_page_has_form(page) -> bool:
    """检查当前页面是否包含表单元素。"""
    try:
        return await page.evaluate("""() => {
            return !!(
                document.querySelector('form') ||
                document.querySelector('.el-form') ||
                document.querySelector('.el-form-item') ||
                document.querySelector('.ant-form') ||
                document.querySelector('.ant-form-item')
            );
        }""")
    except Exception:
        return False


async def _check_dialog_has_form(page, container_type: str) -> bool:
    """检查弹窗/抽屉中是否包含表单元素。"""
    if container_type == "drawer":
        scope = '.el-drawer:not([style*="display: none"])'
    else:
        scope = '.el-dialog__wrapper:not([style*="display: none"])'
    try:
        return await page.evaluate(f"""() => {{
            const container = document.querySelector('{scope}');
            if (!container || container.offsetWidth === 0) return false;
            return !!(
                container.querySelector('form') ||
                container.querySelector('.el-form') ||
                container.querySelector('.el-form-item') ||
                container.querySelector('.ant-form') ||
                container.querySelector('.ant-form-item')
            );
        }}""")
    except Exception:
        return False


async def _check_dialog_has_empty_selects(page, container_type: str) -> bool:
    """检查弹窗/抽屉中是否有未填充的 el-select。"""
    if container_type == "drawer":
        scope = '.el-drawer:not([style*="display: none"])'
    else:
        scope = '.el-dialog__wrapper:not([style*="display: none"])'
    try:
        return await page.evaluate(f"""() => {{
            const container = document.querySelector('{scope}');
            if (!container || container.offsetWidth === 0) return false;
            const selects = container.querySelectorAll('.el-select');
            for (const sel of selects) {{
                const innerInput = sel.querySelector('.el-input__inner');
                if (innerInput && innerInput.disabled) continue;
                const tags = sel.querySelectorAll('.el-tag');
                const hasTags = tags && tags.length > 0;
                const hasValue = innerInput && innerInput.value;
                const selectedLabel = sel.querySelector('.el-select__selected-item, .el-select__tags-text');
                const hasSelectedText = selectedLabel && selectedLabel.textContent.trim().length > 0;
                if (!hasTags && !hasValue && !hasSelectedText) return true;
            }}
            return false;
        }}""")
    except Exception:
        return False


def _setup_new_tab_listener(page) -> tuple:
    """在按钮点击前注册新标签页监听器。

    Returns:
        (event, ref, cleanup) — asyncio.Event, [Page] ref, 清理函数
        使用方式::

            event, ref, cleanup = _setup_new_tab_listener(page)
            await click_button(...)
            obs = await _observe_post_click(page, url_before,
                                             new_page_event=event, new_page_ref=ref)
            cleanup()  # 移除监听器
    """
    event = asyncio.Event()
    ref = [None]

    def on_page(p):
        ref[0] = p
        event.set()

    page.context.on("page", on_page)

    def cleanup():
        try:
            page.context.remove_listener("page", on_page)
        except Exception:
            pass

    return event, ref, cleanup


async def _install_message_capture(page) -> None:
    """在页面注入 MutationObserver，捕获瞬态的 ElMessage / ElNotification 文本。

    某些操作（lock/unlock/reset 等）成功后会弹出 ElMessage.success()，
    但该消息会在 ~3 秒后自动消失。等到 _verify_operation_success 检查时已经消失了。

    此函数在操作前注入，记录所有出现的消息文本到 window.__captured_messages。
    后续用 _get_captured_messages() 读取。
    """
    await page.evaluate("""() => {
        // 初始化捕获数组（如果还没有）
        if (!window.__captured_messages) {
            window.__captured_messages = [];
        }

        // 避免重复安装 observer
        if (window.__msg_capture_observer) return;

        const SUCCESS_KEYWORDS = ['成功', 'success', 'Success', '完成', 'done'];
        const ERROR_KEYWORDS = ['失败', 'error', 'Error', '异常', '错误'];

        // 辅助函数：分类并存储捕获的消息
        function _pushCaptured(text) {
            if (!text) return;
            const isSuccess = SUCCESS_KEYWORDS.some(kw => text.includes(kw));
            const isError = ERROR_KEYWORDS.some(kw => text.includes(kw));
            window.__captured_messages.push({
                text: text.substring(0, 200),
                type: isSuccess ? 'success' : (isError ? 'error' : 'info'),
                time: Date.now()
            });
        }

        // 辅助函数：检查是否是 Element UI Message / Notification
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
            // 也检查子节点是否包含消息组件
            if (node.querySelector && node.querySelector(
                '.el-message, .el-notification, .ant-message-notice, .ant-notification-notice'
            )) {
                return true;
            }
            return false;
        }

        window.__msg_capture_observer = new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1) continue;

                    if (!_isNotificationNode(node)) continue;

                    const text = (node.textContent || '').trim();
                    if (text) {
                        // 同步文本：直接存储
                        _pushCaptured(text);
                    } else {
                        // 异步文本：Element UI $notify 先 appendChild 空 div，
                        // 后通过 Vue 响应式异步设置 message 文本。
                        // 延迟 100ms 重读（Vue nextTick 通常 <16ms，
                        // 但留足余量避免边界情况）。
                        const targetNode = node;
                        setTimeout(() => {
                            const delayedText = (targetNode.textContent || '').trim();
                            _pushCaptured(delayedText);
                        }, 100);
                    }
                }
            }
        });

        window.__msg_capture_observer.observe(document.body, {
            childList: true,
            subtree: true
        });
    }""")


async def _get_captured_messages(page, msg_type: str = None) -> list:
    """读取被捕获的消息，并清除捕获数组。

    Args:
        page: Playwright 页面对象
        msg_type: 过滤类型 ('success' / 'error' / None=全部)

    Returns:
        list of {"text": str, "type": str, "time": int}
    """
    messages = await page.evaluate("""(filterType) => {
        const msgs = window.__captured_messages || [];
        const filtered = filterType ? msgs.filter(m => m.type === filterType) : msgs;
        // 清空已捕获的消息
        window.__captured_messages = [];
        return filtered;
    }""", msg_type)
    return messages or []


async def _verify_operation_success(page, operation_type: str, strict: bool = False,
                                     captured_messages: list = None) -> bool:
    """验证操作是否成功（弹窗关闭 / 成功提示 / 列表变化 / 捕获的消息）。

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型 (create/update/delete/lock/unlock/...)
        strict: 严格模式（需要正向成功信号，而非"无错误即成功"）
        captured_messages: 由 _get_captured_messages() 返回的捕获消息列表

    Returns:
        bool: 是否检测到成功信号
    """
    # [TOAST-DIAG] 诊断：扫描所有可能的 toast/notification DOM 元素
    try:
        toast_diag = await page.evaluate("""() => {
            const result = { allToasts: [], capturedCount: 0, capturedMessages: [] };

            // 1. 扫描所有可能的 toast/notification 容器
            const selectors = [
                '.el-message', '.el-message--success', '.el-message--error', '.el-message--warning',
                '.el-notification', '.el-notification__content', '.el-notification__title',
                '.el-message-box', '.el-message-box__wrapper',
                '[class*="toast"]', '[class*="notice"]', '[class*="message"]'
            ];
            const seen = new Set();
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    const key = el.tagName + ':' + el.className.substring(0, 50);
                    if (seen.has(key)) return;
                    seen.add(key);
                    result.allToasts.push({
                        tag: el.tagName,
                        className: el.className.substring(0, 200),
                        text: el.textContent.trim().substring(0, 100),
                        visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                        style_display: window.getComputedStyle(el).display,
                        style_visibility: window.getComputedStyle(el).visibility,
                        style_opacity: window.getComputedStyle(el).opacity,
                        rect: {
                            top: Math.round(el.getBoundingClientRect().top),
                            left: Math.round(el.getBoundingClientRect().left),
                            width: Math.round(el.getBoundingClientRect().width),
                            height: Math.round(el.getBoundingClientRect().height)
                        }
                    });
                });
            });

            // 2. 扫描 MutationObserver 捕获到的消息
            result.capturedCount = (window.__captured_messages || []).length;
            result.capturedMessages = (window.__captured_messages || []).slice(-5).map(m => ({
                text: m.text,
                type: m.type,
                time: m.time
            }));

            // 3. 扫描 body 直接子元素中可能的 toast 容器
            for (const child of document.body.children) {
                if (child.id === 'app') continue;
                // 处理 SVG 元素的 className（可能是 SVGAnimatedString）
                const cls = typeof child.className === 'string' ? child.className : (child.className?.baseVal || '');
                if (cls && (cls.includes('message') || cls.includes('notification') || cls.includes('toast'))) {
                    result.allToasts.push({
                        tag: child.tagName,
                        id: child.id,
                        className: cls.substring(0, 200),
                        text: child.textContent.trim().substring(0, 100),
                        isBodyChild: true,
                        visible: child.offsetWidth > 0
                    });
                }
            }

            return result;
        }""")
        LOG.info(f"    [TOAST-DIAG] found {len(toast_diag.get('allToasts', []))} toast elements, "
                 f"captured {toast_diag.get('capturedCount', 0)} messages")
        for t in toast_diag.get('allToasts', [])[:10]:  # 最多输出 10 个
            visible_str = "✓" if t.get('visible') else "✗"
            body_str = " [body]" if t.get('isBodyChild') else ""
            LOG.info(f"    [TOAST-DIAG]   {visible_str} {t.get('tag')} class='{t.get('className', '')[:60]}' "
                     f"text='{t.get('text', '')[:40]}'{body_str} "
                     f"rect={t.get('rect', {})}")
        for m in toast_diag.get('capturedMessages', []):
            LOG.info(f"    [TOAST-DIAG]   captured: type={m.get('type')} text='{m.get('text', '')[:40]}' "
                     f"time={m.get('time')}")
    except Exception as e:
        LOG.warning(f"    [TOAST-DIAG] 诊断失败: {e}")

    # 0. 检查捕获的消息（使用语义分类器）
    has_processing = False
    if captured_messages:
        _all_texts = [m.get('text', '') for m in captured_messages]

        # 语义分类每条消息，失败优先于成功
        for t in _all_texts:
            semantic = const.classify_notification(t)
            if semantic == "failure":
                LOG.info(f"    捕获到错误消息: {t[:60]}")
                return False
            elif semantic == "processing":
                has_processing = True

        # 第二轮：检查成功（失败已在第一轮优先处理）
        for t in _all_texts:
            semantic = const.classify_notification(t)
            if semantic == "success":
                LOG.info(f"    捕获到成功消息: {t[:60]}")
                return True

    # 1. 检查成功提示 (Element UI / Ant Design) — 正向信号，两种模式都接受
    has_success = await page.evaluate("""() => {
        // Element UI success message
        const successMsg = document.querySelector('.el-message--success, .el-message .el-icon-success');
        if (successMsg && successMsg.offsetWidth > 0) return true;

        // Ant Design success message
        const antMsg = document.querySelector('.ant-message-success, .ant-message .anticon-check-circle');
        if (antMsg && antMsg.offsetWidth > 0) return true;

        // Notification success (icon-based)
        const notif = document.querySelector('.el-notification .el-icon-success, .ant-notification .anticon-check-circle');
        if (notif) return true;

        // [Toast Fix B] Notification success (text-based, no icon)
        // Element UI $notify 可能没有 icon，只有文本
        const successKw = ['成功', '完成', '已保存', '已添加', '已删除', '已更新'];
        const notifications = document.querySelectorAll('.el-notification, .ant-notification-notice');
        for (const n of notifications) {
            if (n.offsetWidth > 0 && n.offsetHeight > 0) {
                const text = (n.querySelector('.el-notification__content, .ant-notification-notice-message') || n).textContent.trim();
                if (successKw.some(kw => text.includes(kw))) {
                    return true;
                }
            }
        }

        return false;
    }""")
    if has_success:
        return True

    # 1.5 轮询等待 el-dialog / el-message-box 中的成功/错误消息（给服务端响应时间）
    for _poll in range(5):  # 最多轮询 5 次，每次 1s，共 5s
        dialog_result = await page.evaluate("""() => {
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

                // ★ 检测"0个失败"等否定模式（如"2个成功，0个失败"）
                const zeroFailurePattern = /0个失败|0个错误|失败[：:]\s*0|失败数为0|无失败/;
                const hasZeroFailure = zeroFailurePattern.test(text);

                // 失败优先：如果同时包含成功和失败关键词（混合结果），判定为失败
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
                LOG.info(f"    弹窗中检测到成功: {dialog_result['text'][:80]}")
                return True
            else:
                LOG.info(f"    弹窗中检测到错误: {dialog_result['text'][:80]}")
                return False

        # 成功 toast 出现 → 立即跳出，不浪费轮询时间
        has_success_toast = await page.evaluate("""() => {
            const el = document.querySelector('.el-message--success, .el-message .el-icon-success');
            return el && el.offsetWidth > 0;
        }""")
        if has_success_toast:
            break
        await page.wait_for_timeout(1000)

    # 4. 兜底：检查是否有明确的错误状态
    # 策略：只检查明确的错误信号，没有错误就认为成功（乐观策略）
    has_explicit_error = await page.evaluate("""() => {
        // 明确的错误消息（红色/橙色提示）
        const errMsg = document.querySelector(
            '.el-message--error, .ant-message-error, .el-message--warning');
        if (errMsg && errMsg.offsetWidth > 0) return true;

        // 表单验证错误（红色文字提示）
        const formErrors = document.querySelectorAll('.el-form-item__error');
        for (const err of formErrors) {
            if (err.offsetWidth > 0) return true;
        }

        return false;
    }""")

    if has_explicit_error:
        LOG.info("    检测到明确的错误状态")
        return False

    # 5. 无信号：没有检测到明确的成功/失败信号 → 标记为失败
    # 避免"乐观策略"导致的误判（如填充非法值后仍标记为成功）
    LOG.warning("    ⚠️ 未检测到明确的成功/失败信号，标记为失败（可能是 toast 过快消失或后端无反馈）")
    return False


async def _check_data_changed(page, operation_type: str) -> bool:
    """检查操作后是否有可观测的数据变化。

    检测信号：
    - 表格行数变化（与操作前对比）
    - 行状态变化（如 state 字段从 ENABLE → DISABLE）
    - 成功通知（某些系统用 notification 而非 message）

    Args:
        page: Playwright Page 对象
        operation_type: 操作类型

    Returns:
        bool: 是否检测到数据变化
    """
    return await page.evaluate("""() => {
        // 1. 成功通知（el-notification / ant-notification）
        const notif = document.querySelector(
            '.el-notification .el-icon-success, ' +
            '.ant-notification .anticon-check-circle, ' +
            '.el-notification__content:has(.el-icon-success)');
        if (notif && notif.offsetWidth > 0) return true;

        // 2. 成功提示文本（某些系统用普通 message 显示"操作成功"）
        const msgs = document.querySelectorAll('.el-message, .ant-message-notice');
        for (const msg of msgs) {
            if (msg.offsetWidth > 0) {
                const text = msg.textContent || '';
                if (text.includes('成功') || text.includes('success') || text.includes('Success')) {
                    return true;
                }
            }
        }

        // 3. 表格行高亮/动画（某些系统在操作成功后会高亮受影响的行）
        const highlightedRow = document.querySelector(
            '.el-table__row.el-table__row--striped.current-row, ' +
            '.el-table__row.success-highlight, ' +
            'tr.highlight-success');
        if (highlightedRow && highlightedRow.offsetWidth > 0) return true;

        return false;
    }""")


async def _extract_business_failure(page) -> dict | None:
    """检测页面中是否存在明确的业务失败信息。

    在 _verify_operation_success 返回 False 后调用，用于提取具体的错误信息。
    与 _verify_operation_success 的区别：本函数只检测明确的失败关键词，
    不检测"无成功信号"的情况（可能是 UI 延迟）。

    Args:
        page: Playwright Page 对象

    Returns:
        {"error_text": "..."} 如果检测到明确失败
        None 如果没有检测到失败（可能是 UI 延迟）
    """
    try:
        result = await page.evaluate("""() => {
            // 检测明确的失败关键词
            const failKw = ['失败', '错误', '异常', 'error', '无权', '权限不足',
                            '已存在', '重复', 'duplicate', '不能为空', '必填'];

            // 检查弹窗和错误提示容器
            const containers = [
                ...document.querySelectorAll('.el-message-box__wrapper:not([style*="display: none"])'),
                ...document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"])'),
                ...document.querySelectorAll('.el-message--error, .el-message--warning')
            ];

            for (const container of containers) {
                if (container.offsetWidth === 0) continue;
                const text = container.textContent.trim();
                if (!text) continue;

                // 检查是否包含失败关键词
                const hasFailure = failKw.some(kw => text.includes(kw));
                if (hasFailure) {
                    return { error_text: text.substring(0, 200) };
                }
            }

            return null;
        }""")
        return result
    except Exception as e:
        LOG.debug(f"    _extract_business_failure: {e}")
        return None


async def _ensure_on_list_page(page):
    """确保当前在列表页（如果不在，导航回去）。"""
    try:
        url = page.url
        # 检查是否在创建/编辑页面
        if any(kw in url for kw in ("/create", "/add", "/edit", "/detail")):
            # 回退到上一页
            await page.go_back()
            await wait_for_navigation_complete(page)

        # 关闭可能残留的弹窗
        from core.discovery.ui_scanner.button_detector import _close_dialog
        await _close_dialog(page)
    except Exception as e:
        # 浏览器可能已关闭（如 Target page, context or browser has been closed）
        LOG.debug(f"    _ensure_on_list_page: {e}")


async def _check_create_api_triggered_simple(page, timeout: int = 3000) -> bool:
    """简化版 API 触发检测（从 Stage 2 移入）。

    通过检查最近的网络请求判断是否有 POST 创建请求。
    作为 _verify_operation_success 的补充确认，非必需。

    Args:
        page: Playwright Page 对象
        timeout: 检测超时（毫秒），默认 3 秒（比 Stage 2 的 8 秒更短）

    Returns:
        bool: 是否检测到创建 API 请求
    """
    try:
        # 使用 Performance API 检查最近的 XHR 请求
        has_post = await page.evaluate("""() => {
            const entries = performance.getEntriesByType('resource');
            const recent = entries.slice(-20); // 最近 20 个资源请求
            for (const entry of recent) {
                if (entry.initiatorType === 'xmlhttprequest' || entry.initiatorType === 'fetch') {
                    const url = entry.name.toLowerCase();
                    // 排除基础设施 API
                    if (url.includes('/list') || url.includes('/page') ||
                        url.includes('/search') || url.includes('/query') ||
                        url.includes('/menu') || url.includes('/theme')) {
                        continue;
                    }
                    // 检测到可能的创建 API
                    if (url.includes('/create') || url.includes('/add') ||
                        url.includes('/save') || url.includes('/register')) {
                        return true;
                    }
                }
            }
            return false;
        }""")
        return has_post
    except Exception as e:
        LOG.debug(f"    API 触发检测失败: {e}")
        return False
