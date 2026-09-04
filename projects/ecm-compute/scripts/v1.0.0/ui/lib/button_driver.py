"""
button_driver.py - 按钮点击与交互驱动

职责：
- 扫描页面按钮（工具栏、行操作、下拉菜单）
- 智能点击按钮（处理遮挡、动画、fixed 列等复杂场景）
- 处理下拉菜单展开与选择
- 处理确认弹窗
"""

import logging
from playwright.async_api import Page, Locator
from typing import List, Dict, Optional

LOG = logging.getLogger("button_driver")

# KB 加载通过 kb_loader.ProbeKB 统一管理


class ButtonDriver:
    """按钮驱动封装类，供 capture_apis 调用"""

    def __init__(self, page: Page):
        self.page = page
        self.framework = "element-ui"  # 默认，可由外部设置

    async def scan_toolbar_buttons(self) -> List[str]:
        """扫描工具栏按钮文本列表"""
        result = await scan_buttons(self.page)
        return result.get('toolbar', [])

    async def click_by_text(self, text: str, max_retries: int = 3) -> bool:
        """按文本点击按钮"""
        return await click_button(self.page, text, max_retries)

    async def find_data_row(self, marker: str) -> Optional[Locator]:
        """查找包含指定 marker 文本的表格行（支持分页感知）

        Element UI 固定列表格会把同一行复制到多个 wrapper 中，
        但只有主体 wrapper 包含完整的列（含操作按钮列）。
        策略：先在当前页查找，找不到则尝试翻页（最多3页）。
        """
        # 先在当前页查找
        row = await self._find_row_on_current_page(marker)
        if row:
            return row

        # 当前页找不到，尝试翻页查找
        return await self._find_row_with_pagination(marker)

    async def _find_row_on_current_page(self, marker: str) -> Optional[Locator]:
        """在当前页面查找行（不含分页）"""
        try:
            MAIN_SEL = '.el-table__body-wrapper tbody tr'
            FIXED_SELS = [
                '.el-table__fixed .el-table__fixed-body-wrapper tbody tr',
                '.el-table__fixed-right .el-table__fixed-body-wrapper tbody tr',
            ]

            for attempt in range(3):
                # 1. 优先在主体 wrapper 中查找
                main_rows = await self.page.query_selector_all(MAIN_SEL)
                for row in main_rows:
                    text = await row.inner_text()
                    if marker in text:
                        LOG.info(f"找到数据行(主体): {text[:100]}")
                        return row

                # 2. 主体中没找到，检查固定列 wrapper
                for fix_sel in FIXED_SELS:
                    fixed_rows = await self.page.query_selector_all(fix_sel)
                    for idx, row in enumerate(fixed_rows):
                        text = await row.inner_text()
                        if marker in text:
                            LOG.info(f"在固定列找到行(索引{idx})，回主体取对应行")
                            if idx < len(main_rows):
                                return main_rows[idx]
                            else:
                                LOG.warning(f"行索引{idx}超出主体行数{len(main_rows)}")

                # 未找到，等待后重试
                if attempt < 2:
                    LOG.debug(f"未找到包含 '{marker}' 的行，等待 1 秒后重试...")
                    await self.page.wait_for_timeout(1000)

            return None

        except Exception as e:
            LOG.warning(f"当前页查找数据行失败: {e}")
            return None

    async def _find_row_with_pagination(self, marker: str, max_pages: int = 3) -> Optional[Locator]:
        """带分页的行查找：当前页找不到时尝试翻页（最多 max_pages 页）"""
        try:
            # 检查是否有分页组件
            has_pagination = await self.page.evaluate("""
                () => !!document.querySelector('.el-pagination')
            """)

            if not has_pagination:
                LOG.debug("无分页组件，跳过翻页查找")
                return None

            # 获取当前页码
            current_page = await self.page.evaluate("""
                () => {
                    const activeBtn = document.querySelector('.el-pagination .number.active, .el-pagination .btn-quicknext + .number');
                    return activeBtn ? parseInt(activeBtn.textContent) : 1;
                }
            """)
            LOG.info(f"当前页未找到 '{marker}'，当前页码: {current_page}，尝试翻页...")

            # 尝试翻页查找（最多 max_pages 页）
            for page_num in range(current_page + 1, current_page + max_pages + 1):
                # 点击下一页
                next_btn = self.page.locator('.el-pagination .btn-next:not(.disabled)')
                if await next_btn.count() == 0:
                    LOG.debug("已到达最后一页，停止翻页")
                    break

                await next_btn.click()
                await self.page.wait_for_timeout(1500)  # 等待数据加载

                # 在新页查找
                row = await self._find_row_on_current_page(marker)
                if row:
                    LOG.info(f"在第 {page_num} 页找到包含 '{marker}' 的行")
                    return row

            LOG.warning(f"翻页 {max_pages} 页后仍未找到包含 '{marker}' 的行")
            return None

        except Exception as e:
            LOG.warning(f"分页查找失败: {e}")
            return None

    def _load_kb_templates(self) -> dict:
        """加载 KB 模板（统一使用 ProbeKB）"""
        try:
            from .kb_loader import get_kb
            kb = get_kb()
            return kb._kb_data or {}
        except Exception as e:
            LOG.warning(f"加载 KB 模板失败: {e}")
            return {}

    async def _get_row_index(self, row: Locator) -> int:
        """获取行在表格中的索引（0-based）"""
        try:
            return await row.evaluate(
                'el => Array.from(el.parentElement.children).indexOf(el)')
        except Exception:
            return -1

    async def _detect_active_overlay(self) -> str:
        """检测当前页面上活跃的覆盖层（弹窗/抽屉）。

        Returns:
            str: 覆盖层的 XPath 前缀，无活跃覆盖层时返回空串
        """
        from .kb_loader import detect_active_overlay_js, get_overlay_prefix

        js = detect_active_overlay_js(self.framework)
        try:
            overlay_type = await self.page.evaluate(js)
            return get_overlay_prefix(overlay_type, self.framework)
        except Exception:
            return ""

    async def _click_by_xpath_with_wait(self, xpath: str, timeout: int = 2000) -> bool:
        """通过 XPath 点击元素，支持 iframe 遍历，点击成功后等待加载完成。

        适用于按钮类型的点击（可能触发弹窗、抽屉、页面跳转）。
        """
        # 主页面尝试
        if await self._try_click_frame_with_wait(self.page, xpath, timeout):
            return True

        # iframe 遍历
        for frame in self.page.frames:
            if frame == self.page.main_frame:
                continue
            if await self._try_click_frame_with_wait(frame, xpath, timeout):
                return True

        return False

    async def _try_click_frame_with_wait(self, frame, xpath: str, timeout: int = 2000) -> bool:
        """在指定 frame 中尝试点击，点击成功后等待加载完成。"""
        try:
            el = frame.locator(f"xpath={xpath}")
            if await el.count() > 0:
                await el.click(timeout=timeout)
                # 按钮点击成功后等待加载完成
                from .wait_helpers import wait_for_loading_complete
                await wait_for_loading_complete(self.page)
                return True
        except Exception:
            pass
        return False

    async def click_row_button_v2(self, row: Locator, button_text: str) -> bool:
        """点击行内按钮（v2 版本，使用 KB XPath 模板）

        使用 probe_knowledge.json 的 table-action-button 模板的 fallback 链：
        1. fixed-right 列
        2. body-wrapper 主体
        3. fixed-body-wrapper

        支持：
        - 隐藏元素过滤（display:none, is-hidden）
        - 覆盖层前缀定位（弹窗/抽屉内的按钮）
        - 点击后等待加载完成
        """
        from .kb_loader import get_kb, apply_overlay_scope

        row_index = await self._get_row_index(row)
        if row_index < 0:
            LOG.warning("无法获取行索引，使用默认值 0")
            row_index = 0

        # 加载 KB 模板
        kb = get_kb()
        patterns = kb.get_patterns("table-action-button", framework=self.framework)

        # 检测活跃覆盖层
        overlay_prefix = await self._detect_active_overlay()

        # 按 fallback 顺序尝试
        for pattern in patterns:
            xpath = kb.expand_step(
                pattern,
                label=button_text,
                idx=row_index + 1,
                framework=self.framework,
                apply_hidden=True
            )

            # 如果有活跃覆盖层，尝试覆盖层内定位
            if overlay_prefix:
                scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
                if await self._click_by_xpath_with_wait(scoped_xpath):
                    return True

            # 全局定位
            if await self._click_by_xpath_with_wait(xpath):
                return True

        # 最终 fallback: 直接在行内用 JavaScript 查找并点击按钮
        LOG.debug(f"XPath 全部失败，尝试 JavaScript fallback")
        try:
            clicked = await row.evaluate(f"""(row) => {{
                const buttons = row.querySelectorAll('button, .el-button, a, span');
                for (const btn of buttons) {{
                    const text = btn.textContent.trim();
                    if (text.includes('{button_text}')) {{
                        btn.click();
                        return true;
                    }}
                }}
                return false;
            }}""")
            if clicked:
                LOG.debug(f"JavaScript fallback 成功点击按钮 '{button_text}'")
                from .wait_helpers import wait_for_loading_complete
                await wait_for_loading_complete(self.page)
                return True
        except Exception as e:
            LOG.debug(f"JavaScript fallback 失败: {e}")

        LOG.warning(f"未找到按钮 '{button_text}' (行索引={row_index})")
        return False

    async def click_row_more_item(self, row: Locator, item_text: str) -> bool:
        """点击行内"更多"下拉菜单项

        使用 KB dropdown-menu 模板的 click-more 步骤 fallback 链。

        支持：
        - 隐藏元素过滤
        - 覆盖层前缀定位
        - 点击后等待加载完成
        """
        from .kb_loader import get_kb, apply_overlay_scope

        row_index = await self._get_row_index(row)
        if row_index < 0:
            row_index = 0

        kb = get_kb()
        more_patterns = kb.get_patterns("dropdown-menu", framework=self.framework)

        # 检测活跃覆盖层
        overlay_prefix = await self._detect_active_overlay()

        # 查找并点击"更多"按钮
        more_btn = None
        for pattern in more_patterns:
            xpath = kb.expand_step(
                pattern,
                label="更多",
                idx=row_index + 1,
                framework=self.framework,
                apply_hidden=True
            )

            if overlay_prefix:
                scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
                try:
                    btn = self.page.locator(f"xpath={scoped_xpath}")
                    if await btn.count() > 0:
                        more_btn = btn
                        break
                except Exception:
                    pass

            try:
                btn = self.page.locator(f"xpath={xpath}")
                if await btn.count() > 0:
                    more_btn = btn
                    break
            except Exception:
                continue

        if not more_btn:
            LOG.warning(f"未找到行内更多按钮 (行索引={row_index})")
            return False

        # 展开下拉菜单（Element UI 默认 hover 触发，click 兜底）
        # Element UI 的 mouseenter 监听器绑定在 .el-dropdown 父元素上，
        # 仅 hover 内部 <span> 可能无法触发 Vue 的事件处理器。
        expanded = False
        try:
            await more_btn.hover()
            await self.page.wait_for_timeout(300)
            # 显式向 .el-dropdown 父元素派发 mouseenter 事件
            await self.page.evaluate("""(el) => {
                const dropdown = el.closest('.el-dropdown');
                if (dropdown) {
                    dropdown.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false}));
                    dropdown.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                }
                // 也触发 .el-dropdown__caret-button（如果有）
                const caret = el.closest('.el-dropdown__caret-button') || el.closest('.el-button-group');
                if (caret) {
                    caret.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false}));
                }
            }""", await more_btn.element_handle())
            await self.page.wait_for_timeout(600)
            # 检查菜单是否展开（子项可见）
            expanded = await self.page.evaluate("""() => {
                const items = document.querySelectorAll('.el-dropdown-menu__item');
                return Array.from(items).some(el => el.offsetWidth > 0);
            }""")
            if expanded:
                LOG.debug(f"    ✓ hover 展开成功 (行索引={row_index})")
        except Exception as e:
            LOG.debug(f"hover + dispatch on more button failed: {e}")

        if not expanded:
            # hover 未展开，尝试 click（某些 dropdown 配置 trigger="click"）
            LOG.debug(f"    ✗ hover 未展开，尝试 click (行索引={row_index})")
            try:
                await more_btn.click()
                await self.page.wait_for_timeout(600)
                expanded = await self.page.evaluate("""() => {
                    const items = document.querySelectorAll('.el-dropdown-menu__item');
                    return Array.from(items).some(el => el.offsetWidth > 0);
                }""")
                if expanded:
                    LOG.debug(f"    ✓ click 展开成功 (行索引={row_index})")
            except Exception as e:
                LOG.debug(f"click on more button failed: {e}")

        if not expanded:
            LOG.debug(f"    ✗ click 未展开，尝试 Vue 实例 (行索引={row_index})")
            # 最后兜底：JS 直接调用 Vue 实例的 show/handleMouseEnter
            try:
                expanded = await self.page.evaluate("""(el) => {
                    const dropdown = el.closest('.el-dropdown');
                    if (dropdown && dropdown.__vue__) {
                        const vm = dropdown.__vue__;
                        if (typeof vm.show === 'function') vm.show();
                        else if (typeof vm.handleMouseEnter === 'function') vm.handleMouseEnter();
                        return true;
                    }
                    // 尝试向上查找 el-dropdown 组件
                    const parent = el.closest('[class*="dropdown"]');
                    if (parent && parent.__vue__) {
                        const vm = parent.__vue__;
                        if (typeof vm.show === 'function') { vm.show(); return true; }
                    }
                    return false;
                }""", await more_btn.element_handle())
                if expanded:
                    LOG.debug(f"    ✓ Vue 实例展开成功 (行索引={row_index})")
                    await self.page.wait_for_timeout(600)
                else:
                    LOG.debug(f"    ✗ Vue 实例未找到 (行索引={row_index})")
            except Exception as e:
                LOG.debug(f"Vue instance show() fallback failed: {e}")

        if not expanded:
            LOG.warning(f"    ✗ 所有展开策略均失败 (行索引={row_index})")

        # 查找并点击菜单项（按钮类型，需要等待加载）
        clicked = await click_dropdown_option(self.page, item_text)
        return clicked

    async def confirm_message_box(self) -> str:
        """确认 Element UI MessageBox，返回点击的按钮文本"""
        return await confirm_dialog(self.page, confirm=True)

    async def close_all_dialogs(self) -> bool:
        """关闭所有弹窗"""
        return await close_dialog(self.page)

    async def has_non_nav_button(self, text: str) -> bool:
        """检查是否存在非导航按钮（排除侧边栏）"""
        try:
            result = await self.page.evaluate(f"""
                () => {{
                    const buttons = Array.from(document.querySelectorAll('button, .el-button'));
                    return buttons.some(btn => {{
                        const txt = btn.textContent.trim();
                        if (!txt.includes('{text}')) return false;
                        // 排除侧边栏/菜单
                        const parent = btn.closest('.el-menu, .sidebar, nav');
                        return !parent;
                    }});
                }}
            """)
            return result
        except Exception as e:
            LOG.warning(f"检查按钮存在性失败: {e}")
            return False

    async def get_dropdown_items(self) -> List[str]:
        """获取当前展开的下拉菜单项"""
        try:
            items = await self.page.evaluate("""
                () => {
                    const menuItems = document.querySelectorAll('.el-dropdown-menu__item:visible');
                    return Array.from(menuItems).map(item => item.textContent.trim()).filter(t => t);
                }
            """)
            return items
        except Exception as e:
            LOG.warning(f"获取下拉菜单项失败: {e}")
            return []

    async def after_row_op(self, form_filler: 'FormFiller'):
        """行操作后处理（关闭弹窗、处理表单等）"""
        # 检查是否有弹窗或表单
        await self.page.wait_for_timeout(1000)

        # 尝试关闭弹窗
        closed = await close_dialog(self.page)
        if closed:
            return

        # 检查是否有确认框
        confirmed = await confirm_dialog(self.page, confirm=True)
        if confirmed:
            return

        # 其他情况等待一下
        await self.page.wait_for_timeout(500)


async def scan_buttons(page: Page) -> Dict[str, List[str]]:
    """扫描页面按钮（工具栏、行操作、下拉菜单）。

    Returns:
        {
            'toolbar': ['创建', '查询', '导出'],
            'row_actions': ['编辑', '删除', '更多'],
            'dropdowns': ['更多']
        }
    """
    script = """
    () => {
        const result = {
            toolbar: [],
            row_actions: [],
            dropdowns: []
        };

        // 扫描工具栏按钮（表格上方）
        const toolbar = document.querySelector('.toolbar, .el-form--inline, [class*="toolbar"]');
        if (toolbar) {
            const buttons = toolbar.querySelectorAll('button, .el-button');
            buttons.forEach(btn => {
                const text = btn.textContent.trim();
                if (text && !result.toolbar.includes(text)) {
                    result.toolbar.push(text);
                }
            });
        }

        // 扫描行操作按钮（表格操作列）
        const table = document.querySelector('.el-table, table');
        if (table) {
            // 查找操作列（通常是最后一列或含 "操作" 文本的列）
            const headerCells = table.querySelectorAll('th, .el-table__header-wrapper th');
            let operationColIndex = -1;

            headerCells.forEach((cell, idx) => {
                const text = cell.textContent.trim();
                if (text.includes('操作') || text.includes('Action')) {
                    operationColIndex = idx;
                }
            });

            // 如果找到操作列，扫描该列的按钮
            if (operationColIndex >= 0) {
                const rows = table.querySelectorAll('tbody tr, .el-table__body-wrapper tr');
                if (rows.length > 0) {
                    const firstRow = rows[0];
                    const cells = firstRow.querySelectorAll('td');
                    if (cells[operationColIndex]) {
                        const buttons = cells[operationColIndex].querySelectorAll('button, .el-button, a');
                        buttons.forEach(btn => {
                            const text = btn.textContent.trim();
                            if (text && !result.row_actions.includes(text)) {
                                result.row_actions.push(text);
                            }
                        });
                    }
                }
            }
        }

        // 扫描下拉菜单触发器
        const dropdownTriggers = document.querySelectorAll('.el-dropdown, [class*="dropdown"]');
        dropdownTriggers.forEach(trigger => {
            const text = trigger.textContent.trim();
            if (text && !result.dropdowns.includes(text)) {
                result.dropdowns.push(text);
            }
        });

        return result;
    }
    """

    return await page.evaluate(script)


async def click_button(page: Page, text: str, max_retries: int = 3) -> bool:
    """智能点击按钮（处理遮挡、动画、fixed 列等复杂场景）。

    策略：
    1. 优先使用 Playwright click（自动等待可见性）
    2. 如果被遮挡，尝试 scrollIntoView + force click
    3. 如果仍在 fixed 列，使用 JavaScript click

    点击成功后等待加载完成（可能触发弹窗/抽屉/页面跳转）。

    Args:
        page: Playwright 页面对象
        text: 按钮文本
        max_retries: 最大重试次数

    Returns:
        是否成功点击
    """
    from .wait_helpers import wait_for_loading_complete

    for attempt in range(max_retries):
        try:
            # 策略 1: Playwright click（自动等待可见性）
            button = page.locator(f'button:has-text("{text}"), .el-button:has-text("{text}")').first
            if await button.count() > 0:
                await button.click(timeout=3000)
                await wait_for_loading_complete(page)
                return True

            # 策略 2: 查找并滚动到可见
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible, .el-button:has-text("{text}"):visible')
            if buttons:
                await buttons[0].scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await buttons[0].click()
                await wait_for_loading_complete(page)
                return True

            # 策略 3: JavaScript click（绕过遮挡）
            clicked = await page.evaluate(f"""
                () => {{
                    const buttons = Array.from(document.querySelectorAll('button, .el-button'));
                    const target = buttons.find(btn => btn.textContent.trim().includes('{text}'));
                    if (target) {{
                        target.click();
                        return true;
                    }}
                    return false;
                }}
            """)
            if clicked:
                await wait_for_loading_complete(page)
                return True

        except Exception as e:
            if attempt == 0:
                # 首次失败时诊断
                diag = await page.evaluate(f"""() => {{
                    const all = Array.from(document.querySelectorAll('button, .el-button'));
                    const matches = all.filter(b => b.textContent.trim().includes('{text}'));
                    return {{
                        totalButtons: all.length,
                        matchingText: matches.length,
                        matches: matches.slice(0, 5).map(b => ({{
                            text: b.textContent.trim().slice(0, 30),
                            visible: b.offsetWidth > 0 && b.offsetHeight > 0,
                            tag: b.tagName,
                            rect: {{w: b.offsetWidth, h: b.offsetHeight, x: b.getBoundingClientRect().x, y: b.getBoundingClientRect().y}},
                        }}))
                    }};
                }}""")
                LOG.info(f"  click_button 诊断 '{text}': {diag}")
            if attempt < max_retries - 1:
                await page.wait_for_timeout(500)
            continue

    return False


async def click_row_button(page: Page, row_index: int, button_text: str) -> bool:
    """点击表格行操作按钮。

    点击成功后等待加载完成。
    """
    from .wait_helpers import wait_for_loading_complete

    try:
        rows = await page.query_selector_all('tbody tr, .el-table__body-wrapper tr')
        if row_index >= len(rows):
            return False

        row = rows[row_index]
        cells = await row.query_selector_all('td')
        for cell in cells:
            buttons = await cell.query_selector_all('button, .el-button, a')
            for btn in buttons:
                text = await btn.inner_text()
                if button_text in text:
                    try:
                        await btn.click()
                        await wait_for_loading_complete(page)
                        return True
                    except Exception as e:
                        LOG.debug(f"Playwright click failed, trying JS click: {e}")
                        await btn.evaluate('el => el.click()')
                        await wait_for_loading_complete(page)
                        return True

        return False
    except Exception as e:
        LOG.warning(f"点击行按钮失败: {e}")
        return False


async def expand_dropdown(page: Page, trigger_text: str) -> List[str]:
    """展开下拉菜单并获取选项列表。

    Args:
        page: Playwright 页面对象
        trigger_text: 下拉触发器文本

    Returns:
        选项文本列表
    """
    try:
        # 点击触发器
        await click_button(page, trigger_text)
        await page.wait_for_timeout(800)

        # 获取下拉选项
        options = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.el-dropdown-menu__item:visible, .el-select-dropdown__item:visible');
                return Array.from(items).map(item => item.textContent.trim()).filter(text => text);
            }
        """)

        return options

    except Exception as e:
        LOG.warning(f"展开下拉菜单失败: {e}")
        return []


async def click_dropdown_option(page: Page, option_text: str) -> bool:
    """点击下拉菜单选项。

    点击成功后等待加载完成（可能触发后续操作）。
    快速路径：先检查可见性，不可见时直接走 JS click，避免 30s 超时。
    """
    from .wait_helpers import wait_for_loading_complete

    try:
        # 快速检查：元素是否存在且可见
        visible = await page.evaluate(f"""() => {{
            const items = Array.from(document.querySelectorAll(
                '.el-dropdown-menu__item, .el-select-dropdown__item'));
            const target = items.find(
                item => item.textContent.trim().includes('{option_text}'));
            if (!target) return 'not_found';
            if (target.offsetWidth > 0 && target.offsetHeight > 0) return 'visible';
            return 'hidden';
        }}""")

        if visible == 'not_found':
            return False

        if visible == 'visible':
            # 可见 → Playwright click（快速成功）
            option = page.locator(
                f'.el-dropdown-menu__item:has-text("{option_text}"), '
                f'.el-select-dropdown__item:has-text("{option_text}")'
            ).first
            if await option.count() > 0:
                await option.click(timeout=3000)
                await wait_for_loading_complete(page)
                return True

        # 不可见或 Playwright click 失败 → JS click（强制触发）
        clicked = await page.evaluate(f"""
            () => {{
                const items = Array.from(document.querySelectorAll(
                    '.el-dropdown-menu__item, .el-select-dropdown__item'));
                const target = items.find(
                    item => item.textContent.trim().includes('{option_text}'));
                if (target) {{
                    target.click();
                    return true;
                }}
                return false;
            }}
        """)

        if clicked:
            await wait_for_loading_complete(page)
            return True

        return False

    except Exception as e:
        LOG.warning(f"点击下拉选项失败: {e}")
        return False


async def confirm_dialog(page: Page, confirm: bool = True) -> str:
    """处理确认弹窗（Element UI MessageBox / Popconfirm / 通用模态框）。

    支持三种确认 UI：
    1. el-message-box: MessageBox.confirm()
    2. el-popconfirm: <el-popconfirm> 气泡确认框
    3. 通用模态框: 任何包含确认按钮的可见对话框

    Args:
        page: Playwright 页面对象
        confirm: True 点击确认，False 点击取消

    Returns:
        点击的按钮文本（如 "确定"、"确认"），失败返回空字符串
    """
    from .wait_helpers import wait_for_loading_complete

    try:
        # 等待确认框出现（最多 2 秒）
        for _ in range(4):
            # 检查各种确认框容器
            has_confirm = await page.evaluate("""() => {
                // 1. el-message-box
                const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
                if (msgBox && msgBox.offsetWidth > 0) return 'message-box';

                // 2. el-popconfirm
                const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
                if (popconfirm && popconfirm.offsetWidth > 0) return 'popconfirm';

                // 3. 通用对话框（包含确认按钮）
                const dialogs = document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"]), .ant-modal-wrap:not([style*="display: none"])');
                for (const dialog of dialogs) {
                    if (dialog.offsetWidth > 0) {
                        const btns = dialog.querySelectorAll('button');
                        for (const btn of btns) {
                            const text = btn.textContent.trim();
                            if (['确定', '确认', '是', 'OK', 'Yes'].includes(text)) {
                                return 'generic-dialog';
                            }
                        }
                    }
                }
                return null;
            }""")

            if has_confirm:
                break
            await page.wait_for_timeout(500)

        if not has_confirm:
            LOG.debug("未检测到确认框容器")
            return ""

        LOG.debug(f"检测到确认框类型: {has_confirm}")

        # 根据容器类型定位按钮
        if has_confirm == 'message-box':
            if confirm:
                button = page.locator('.el-message-box__btns button.el-button--primary, .el-message-box__btns button:has-text("确定")').first
            else:
                button = page.locator('.el-message-box__btns button:has-text("取消")').first

        elif has_confirm == 'popconfirm':
            if confirm:
                # el-popconfirm 的确认按钮在 .el-popconfirm__action 内
                button = page.locator('.el-popconfirm__action button.el-button--primary, .el-popconfirm__action button:has-text("确定")').first
            else:
                button = page.locator('.el-popconfirm__action button:has-text("取消")').first

        else:  # generic-dialog
            if confirm:
                button = page.locator('button:has-text("确定"), button:has-text("确认"), button:has-text("是"), button:has-text("OK"), button:has-text("Yes")').first
            else:
                button = page.locator('button:has-text("取消"), button:has-text("Cancel"), button:has-text("否")').first

        if await button.count() > 0:
            button_text = await button.inner_text()
            await button.click()
            await wait_for_loading_complete(page)
            return button_text.strip()

        # 兜底：尝试常见确认按钮文本
        for text in ['确定', '确认', '是', 'OK', 'Yes']:
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible')
            if buttons:
                await buttons[0].click()
                await wait_for_loading_complete(page)
                return text

        return ""

    except Exception as e:
        LOG.warning(f"处理确认弹窗失败: {e}")
        return ""


async def close_dialog(page: Page) -> bool:
    """关闭弹窗（点击右上角关闭按钮或按 Escape）。

    点击成功后等待加载完成（可能触发页面刷新）。

    先用 3 秒检查可见关闭按钮，无则按 Escape。
    """
    from .wait_helpers import wait_for_loading_complete

    try:
        # 检查是否有可见的关闭按钮（3秒超时）
        close_btn = page.locator('.el-dialog__close:visible, .el-message-box__close:visible').first
        if await close_btn.count() > 0:
            await close_btn.click(timeout=3000)
            await wait_for_loading_complete(page)
            return True

        # 无可见关闭按钮，按 Escape
        await page.keyboard.press('Escape')
        await wait_for_loading_complete(page)
        return True

    except Exception as e:
        LOG.debug(f"关闭弹窗: {e}")
        return False


async def wait_for_table_load(page: Page, timeout: int = 10000) -> bool:
    """等待表格加载完成。

    Args:
        page: Playwright 页面对象
        timeout: 超时时间（毫秒）

    Returns:
        是否加载成功
    """
    try:
        # 等待加载动画消失
        await page.wait_for_selector('.el-loading-mask', state='hidden', timeout=timeout)
        await page.wait_for_timeout(500)
        return True
    except Exception as e:
        # 没有加载动画或超时
        LOG.debug(f"等待表格加载: {e}")
        return False
