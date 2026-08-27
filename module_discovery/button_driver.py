"""
button_driver.py - 按钮点击与交互驱动

职责：
- 扫描页面按钮（工具栏、行操作、下拉菜单）
- 智能点击按钮（处理遮挡、动画、fixed 列等复杂场景）
- 处理下拉菜单展开与选择
- 处理确认弹窗
"""

import json
import logging
from pathlib import Path
from playwright.async_api import Page, Locator
from typing import List, Dict, Optional

LOG = logging.getLogger("button_driver")

# KB 路径（相对项目根目录）
_KB_PATH = Path(__file__).parent.parent / "config" / "base_nav_kb.json"


class ButtonDriver:
    """按钮驱动封装类，供 capture_apis 调用"""

    def __init__(self, page: Page):
        self.page = page

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
        """加载 base_nav_kb.json 模板"""
        try:
            if _KB_PATH.exists():
                return json.loads(_KB_PATH.read_text(encoding='utf-8'))
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

    async def click_row_button_v2(self, row: Locator, button_text: str) -> bool:
        """点击行内按钮（v2 版本，使用 KB XPath 模板）

        使用 base_nav_kb.json 的 table-action-button 模板的 fallback 链：
        1. fixed-right 列
        2. body-wrapper 主体
        3. fixed-body-wrapper
        """
        row_index = await self._get_row_index(row)
        if row_index < 0:
            LOG.warning("无法获取行索引，使用默认值 0")
            row_index = 0

        # 加载 KB 模板
        kb = self._load_kb_templates()
        patterns = kb.get("composite", {}).get("table-action-button", {}).get("patterns", [])

        if not patterns:
            # KB 未加载，使用硬编码 fallback
            patterns = [
                "//div[contains(@class,'el-table__fixed-right')]//tbody/tr[{idx}]//span[contains(.,'{label}')]",
                "//div[contains(@class,'el-table__body-wrapper')]//tbody/tr[{idx}]//span[contains(.,'{label}')]",
                "//div[contains(@class,'el-table__fixed-body-wrapper')]//tbody/tr[{idx}]//span[contains(.,'{label}')]",
            ]

        # 按 fallback 顺序尝试
        for pattern in patterns:
            xpath = (pattern
                    .replace("tr[1]", f"tr[{row_index + 1}]")
                    .replace("{idx}", str(row_index + 1))
                    .replace("{label}", button_text))
            try:
                btn = self.page.locator(f"xpath={xpath}")
                if await btn.count() > 0:
                    LOG.debug(f"找到按钮 '{button_text}' via XPath: {xpath[:80]}")
                    try:
                        await btn.click(timeout=3000)
                        await self.page.wait_for_timeout(500)
                        return True
                    except Exception as e:
                        LOG.debug(f"Playwright click failed, trying JS click: {e}")
                        await btn.evaluate('el => el.click()')
                        await self.page.wait_for_timeout(500)
                        return True
            except Exception as e:
                LOG.debug(f"XPath failed: {xpath[:60]}... error: {e}")
                continue

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
                await self.page.wait_for_timeout(500)
                return True
        except Exception as e:
            LOG.debug(f"JavaScript fallback 失败: {e}")

        LOG.warning(f"未找到按钮 '{button_text}' (行索引={row_index})")
        return False

    async def click_row_more_item(self, row: Locator, item_text: str) -> bool:
        """点击行内"更多"下拉菜单项

        使用 KB dropdown-menu 模板的 click-more 步骤 fallback 链。
        """
        row_index = await self._get_row_index(row)
        if row_index < 0:
            row_index = 0

        # 加载 KB dropdown-menu 模板的 click-more 步骤
        kb = self._load_kb_templates()
        more_patterns = (kb.get("composite", {}).get("dropdown-menu", {})
                         .get("steps", {}).get("click-more", {}).get("patterns", []))

        if not more_patterns:
            # KB 未加载，使用硬编码 fallback
            more_patterns = [
                "//div[contains(@class,'el-table__fixed-right')]//tbody/tr[{idx}]//span[contains(.,'更多')]",
                "//div[contains(@class,'el-table__body-wrapper')]//tbody/tr[{idx}]//span[contains(.,'更多')]",
                "//div[contains(@class,'el-table__fixed-body-wrapper')]//tbody/tr[{idx}]//span[contains(.,'更多')]",
            ]

        # 查找并点击"更多"按钮
        more_btn = None
        for pattern in more_patterns:
            xpath = (pattern
                    .replace("tr[1]", f"tr[{row_index + 1}]")
                    .replace("{idx}", str(row_index + 1)))
            try:
                btn = self.page.locator(f"xpath={xpath}")
                if await btn.count() > 0:
                    more_btn = btn
                    LOG.debug(f"找到更多按钮 via XPath: {xpath[:80]}")
                    break
            except Exception as e:
                LOG.debug(f"XPath failed for more button: {e}")
                continue

        if not more_btn:
            LOG.warning(f"未找到行内更多按钮 (行索引={row_index})")
            return False

        # 点击展开
        try:
            await more_btn.click()
        except Exception as e:
            LOG.debug(f"Playwright click on more button failed, trying JS click: {e}")
            await more_btn.evaluate('el => el.click()')

        await self.page.wait_for_timeout(800)

        # 查找并点击菜单项
        clicked = await click_dropdown_option(self.page, item_text)
        return clicked

    async def confirm_message_box(self) -> bool:
        """确认 Element UI MessageBox"""
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

    Args:
        page: Playwright 页面对象
        text: 按钮文本
        max_retries: 最大重试次数

    Returns:
        是否成功点击
    """
    for attempt in range(max_retries):
        try:
            # 策略 1: Playwright click（自动等待可见性）
            button = page.locator(f'button:has-text("{text}"), .el-button:has-text("{text}")').first
            if await button.count() > 0:
                await button.click(timeout=3000)
                await page.wait_for_timeout(500)
                return True

            # 策略 2: 查找并滚动到可见
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible, .el-button:has-text("{text}"):visible')
            if buttons:
                await buttons[0].scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await buttons[0].click()
                await page.wait_for_timeout(500)
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
                await page.wait_for_timeout(500)
                return True

        except Exception as e:
            if attempt < max_retries - 1:
                await page.wait_for_timeout(500)
            continue

    return False


async def click_row_button(page: Page, row_index: int, button_text: str) -> bool:
    """点击表格行操作按钮。

    Args:
        page: Playwright 页面对象
        row_index: 行索引（从 0 开始）
        button_text: 按钮文本

    Returns:
        是否成功点击
    """
    try:
        # 查找表格行
        rows = await page.query_selector_all('tbody tr, .el-table__body-wrapper tr')
        if row_index >= len(rows):
            return False

        row = rows[row_index]

        # 查找操作列按钮
        cells = await row.query_selector_all('td')
        for cell in cells:
            buttons = await cell.query_selector_all('button, .el-button, a')
            for btn in buttons:
                text = await btn.inner_text()
                if button_text in text:
                    # 尝试点击
                    try:
                        await btn.click()
                        await page.wait_for_timeout(500)
                        return True
                    except Exception as e:
                        # 被遮挡时使用 JavaScript click
                        LOG.debug(f"Playwright click failed, trying JS click: {e}")
                        await btn.evaluate('el => el.click()')
                        await page.wait_for_timeout(500)
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

    Args:
        page: Playwright 页面对象
        option_text: 选项文本

    Returns:
        是否成功点击
    """
    try:
        # 查找可见的下拉菜单项
        option = page.locator(f'.el-dropdown-menu__item:has-text("{option_text}"), .el-select-dropdown__item:has-text("{option_text}")').first
        if await option.count() > 0:
            await option.click()
            await page.wait_for_timeout(500)
            return True

        # JavaScript click
        clicked = await page.evaluate(f"""
            () => {{
                const items = Array.from(document.querySelectorAll('.el-dropdown-menu__item, .el-select-dropdown__item'));
                const target = items.find(item => item.textContent.trim().includes('{option_text}'));
                if (target) {{
                    target.click();
                    return true;
                }}
                return false;
            }}
        """)

        if clicked:
            await page.wait_for_timeout(500)
            return True

        return False

    except Exception as e:
        LOG.warning(f"点击下拉选项失败: {e}")
        return False


async def confirm_dialog(page: Page, confirm: bool = True) -> bool:
    """处理确认弹窗（Element UI MessageBox）。

    Args:
        page: Playwright 页面对象
        confirm: True 点击确认，False 点击取消

    Returns:
        是否成功处理
    """
    try:
        # 等待弹窗出现
        await page.wait_for_timeout(500)

        # 查找弹窗按钮
        if confirm:
            button = page.locator('.el-message-box__btns button.el-button--primary, .el-message-box__btns button:has-text("确定")').first
        else:
            button = page.locator('.el-message-box__btns button:has-text("取消")').first

        if await button.count() > 0:
            await button.click()
            await page.wait_for_timeout(800)
            return True

        # 尝试常见确认按钮文本
        for text in ['确定', '确认', '是', 'OK', 'Yes']:
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible')
            if buttons:
                await buttons[0].click()
                await page.wait_for_timeout(800)
                return True

        return False

    except Exception as e:
        LOG.warning(f"处理确认弹窗失败: {e}")
        return False


async def close_dialog(page: Page) -> bool:
    """关闭弹窗（点击右上角关闭按钮或按 Escape）。

    先用 3 秒检查可见关闭按钮，无则按 Escape。
    """
    try:
        # 检查是否有可见的关闭按钮（3秒超时）
        close_btn = page.locator('.el-dialog__close:visible, .el-message-box__close:visible').first
        if await close_btn.count() > 0:
            await close_btn.click(timeout=3000)
            await page.wait_for_timeout(500)
            return True

        # 无可见关闭按钮，按 Escape
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(500)
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
