"""
button_driver.py - 按钮点击与交互驱动

职责：
- 扫描页面按钮（工具栏、行操作、下拉菜单）
- 智能点击按钮（处理遮挡、动画、fixed 列等复杂场景）
- 处理下拉菜单展开与选择
- 处理确认弹窗
"""

import logging
import json
from playwright.async_api import Page, Locator
from typing import List, Dict, Optional

from .. import const
from ..shared.row_interaction import _TAB_SCOPE_JS, js_click_row_button

LOG = logging.getLogger("button_driver")

# KB 加载通过 kb_loader.ProbeKB 统一管理

# ★ 批五 F2-3：搜索框 placeholder 识别关键词（v3 探针定案口径）。
#   EIS 实证 placeholder='先选择属性项，再选择属性值或输入关键字搜索' 同时
#   含三关键词；estack 无匹配关键词时搜索先行自动 no-op → 原扫描路径零回归。
_SEARCH_PLACEHOLDER_KEYWORDS = ("搜索", "关键字", "属性值")


async def _find_search_input(page) -> Optional[object]:
    """★ 批五 F2-3：按 placeholder 关键词定位页面级搜索框（排除弹窗内输入）。

    Returns:
        ElementHandle | None：可见且不在弹窗/抽屉内的第一个匹配输入框。
    """
    try:
        handle = await page.evaluate_handle("""(kws) => {
            const inputs = document.querySelectorAll('input[placeholder]');
            for (const inp of inputs) {
                if (inp.offsetWidth <= 0 || inp.offsetHeight <= 0) continue;
                // 排除弹窗/抽屉/消息框内输入（防误把 dialog 表单字段当搜索框）
                if (inp.closest('.el-dialog__wrapper, .el-drawer, .el-message-box__wrapper, .ant-modal-wrap')) continue;
                const ph = inp.getAttribute('placeholder') || '';
                if (kws.some(k => ph.includes(k))) return inp;
            }
            return null;
        }""", list(_SEARCH_PLACEHOLDER_KEYWORDS))
        return handle.as_element()
    except Exception as e:
        LOG.debug(f"  [SEARCH-FIRST] 搜索框定位失败: {e}")
        return None


async def search_filter_table(page, keyword: str) -> bool:
    """★ 批五 F2-3：搜索先行过滤表格（v3 探针定案链路，Stage 1/2 单源共享）。

    多用户环境列表行序漂移（r7 实证第一行是谁的 VM 完全随机），行定位必须
    先搜索过滤再查找，不能依赖行扫描巧合命中 marker 行。

    链路（v3 成功实证；v5 失败教训：JS setter 无焦点 + keyboard Enter 落
    body 无效）：
      placeholder 关键词定位搜索框 → Playwright click 真实聚焦 →
      （复合框场景）属性下拉选「名称」类选项 → fill(keyword) →
      在 input 上 press("Enter") → 等表格刷新。

    幂等短路：搜索框已含同 keyword 时跳过重放（find_data_row 每操作多次
    调用，省 2.5s/次）。

    Returns:
        True=搜索已生效（或已处于同 keyword 过滤态）；
        False=无搜索框/未生效（调用方回落原扫描路径）。
    """
    el = await _find_search_input(page)
    if el is None:
        return False
    try:
        # 幂等短路：输入框已是同 keyword → 表格已处于过滤态，无需重放
        try:
            cur = await el.input_value()
        except Exception:
            cur = None
        if cur == keyword and keyword:
            return True
        await el.click()  # 真实点击聚焦（v5 教训：JS 设值无焦点无效）
        await page.wait_for_timeout(800)
        # 复合框属性下拉（EIS 实证形态）：点击后展开 el-select-dropdown，
        # 可见选项含「名称」→ 选之（无下拉/无匹配项 → 直接输关键字）
        chosen = await page.evaluate("""() => {
            const dd = Array.from(document.querySelectorAll('.el-select-dropdown'))
                .filter(d => d.offsetWidth > 0);
            if (!dd.length) return null;
            const last = dd[dd.length - 1];
            const items = Array.from(last.querySelectorAll('.el-select-dropdown__item'))
                .filter(i => i.offsetWidth > 0);
            const hit = items.find(i => ((i.textContent || '').trim()).includes('名称')) || null;
            if (hit) { hit.click(); return (hit.textContent || '').trim(); }
            return null;
        }""")
        if chosen:
            LOG.info(f"  [SEARCH-FIRST] 属性下拉已选: '{chosen}'")
            await page.wait_for_timeout(800)
        await el.fill(keyword)
        await page.wait_for_timeout(500)
        await el.press("Enter")  # 在 input 上按 Enter（v5 教训：keyboard.press 落 body 无效）
        await page.wait_for_timeout(2500)
        LOG.info(f"  [SEARCH-FIRST] 已按 '{keyword}' 过滤表格")
        return True
    except Exception as e:
        LOG.debug(f"  [SEARCH-FIRST] 搜索未生效（回落原扫描路径）: {e}")
        return False


async def clear_table_search(page) -> bool:
    """★ 批五 F2 自清洁：清空页面搜索框并回车恢复列表（幂等 no-op 安全重跑）。

    模块收尾调用（crud_executor Tab 末尾）——搜索先行留下的过滤态在轮次
    结束后必须恢复，防污染下轮探测与共享环境。
    """
    el = await _find_search_input(page)
    if el is None:
        return False
    try:
        try:
            cur = await el.input_value()
        except Exception:
            cur = None
        if not cur:
            return False  # 已是空 → 幂等 no-op
        await el.click()
        await el.fill("")
        await page.wait_for_timeout(300)
        await el.press("Enter")
        await page.wait_for_timeout(1500)
        LOG.info("  [SEARCH-FIRST] 已清空搜索，恢复列表")
        return True
    except Exception as e:
        LOG.debug(f"  [SEARCH-FIRST] 清空搜索失败: {e}")
        return False


class ButtonDriver:
    """按钮驱动封装类，供 capture_apis 调用"""

    def __init__(self, page: Page, ui_framework: str = "element-ui"):
        self.page = page
        self.framework = ui_framework
        # 加载 UI 选择器配置
        try:
            self.selectors = const.get_ui_selectors(ui_framework)
        except FileNotFoundError:
            # 回退到 element-ui
            self.selectors = const.get_ui_selectors("element-ui")

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

        ★ 批五 F2-3：搜索先行——多用户环境行序漂移，先按 marker 搜索过滤
        表格再定位（v3 定案链路 search_filter_table）。搜索未生效/无搜索框
        → 走原扫描+翻页路径（完整保留，条件化主路径而非兜底补丁）。
        删除验证（_do_delete 行消失检查）复用本路径自动获得
        「搜索 marker → 0 行 = 删除成功」口径（F2-4）。
        """
        # ★ 搜索先行（主路径）：过滤到 marker 行再定位。
        # 搜索已生效时本路径为终局——过滤态下 5 次重试未命中即判定行不存在
        # （删除验证 0 行口径依赖此快速返回，不再重复全扫+翻页）。
        if marker:
            searched = await search_filter_table(self.page, marker)
            if searched:
                row = await self._find_row_on_current_page(marker)
                if row:
                    return row
                LOG.debug(f"搜索过滤态下未找到 '{marker}'，判定行不存在")
                return None

        # 原路径：当前页查找 → 翻页查找
        row = await self._find_row_on_current_page(marker)
        if row:
            return row

        # 当前页找不到，尝试翻页查找
        return await self._find_row_with_pagination(marker)

    async def find_first_data_row(self) -> Optional[Locator]:
        """定位表格第一行数据（无需 marker，限定在可见 tab-pane 内查找）

        用于无 marker 场景（表单无 input 字段时），直接取第一行进行操作。
        """
        try:
            # ★ 等待可见 tab-pane 内的表格主体行出现（限定作用域，避免匹配隐藏 Tab）
            import time as _t
            _deadline = _t.time() + 10  # 10s timeout
            _found = False
            while _t.time() < _deadline:
                _found = await self.page.evaluate(f"""() => {{
                    {_TAB_SCOPE_JS}
                    return !!_scope.querySelector('.el-table__body-wrapper tbody tr');
                }}""")
                if _found:
                    break
                await self.page.wait_for_timeout(300)
            if not _found:
                return None
            # 在可见 tab-pane 内查找第一行，获取带 pane scope 的选择器
            row_info = await self.page.evaluate(f"""() => {{
                {_TAB_SCOPE_JS}
                const row = _scope.querySelector('.el-table__body-wrapper tbody tr');
                if (!row) return null;
                const allRows = _scope.querySelectorAll('.el-table__body-wrapper tbody tr');
                const idx = Array.from(allRows).indexOf(row);
                const paneId = (_scope !== document && _scope.id) ? _scope.id : null;
                return {{
                    selector: paneId
                        ? `#${{paneId}} .el-table__body-wrapper tbody tr:nth-child(${{idx + 1}})`
                        : `.el-table__body-wrapper tbody tr:nth-child(${{idx + 1}})`,
                    paneId: paneId
                }};
            }}""")
            if row_info and row_info.get("selector"):
                return self.page.locator(row_info["selector"]).first
        except Exception:
            pass
        return None

    async def _find_row_on_current_page(self, marker: str) -> Optional[Locator]:
        """在当前页面查找行（含加载等待 + 扩展重试）"""
        try:
            # [TAB-SCOPE-DIAG] 诊断：记录行查找时的 tab-pane 可见性
            try:
                scope_diag = await self.page.evaluate("""() => {
                    const panes = document.querySelectorAll('.el-tab-pane');
                    if (panes.length === 0) return { noTabPane: true };
                    const result = { panes: [], tables: [], paginations: [] };
                    panes.forEach((pane, i) => {
                        const style = window.getComputedStyle(pane);
                        result.panes.push({
                            index: i,
                            id: pane.id || '',
                            visible: style.display !== 'none' && style.visibility !== 'hidden',
                            display: style.display,
                            tableCount: pane.querySelectorAll('.el-table').length,
                            rowCount: pane.querySelectorAll('.el-table__body-wrapper tbody tr').length,
                            paginationCount: pane.querySelectorAll('.el-pagination').length
                        });
                    });
                    // 全页面统计
                    result.tables = Array.from(document.querySelectorAll('.el-table')).map(t => {
                        const pane = t.closest('.el-tab-pane');
                        return {
                            inTabPane: !!pane,
                            paneId: pane ? pane.id : '',
                            rowCount: t.querySelectorAll('.el-table__body-wrapper tbody tr').length
                        };
                    });
                    result.paginations = Array.from(document.querySelectorAll('.el-pagination')).map(p => {
                        const pane = p.closest('.el-tab-pane');
                        return {
                            inTabPane: !!pane,
                            paneId: pane ? pane.id : '',
                            visible: p.offsetWidth > 0
                        };
                    });
                    return result;
                }""")
                if scope_diag.get('noTabPane'):
                    LOG.debug(f"    [TAB-SCOPE-DIAG] 无 tab-pane 结构")
                else:
                    LOG.info(f"    [TAB-SCOPE-DIAG] {len(scope_diag.get('panes', []))} 个 tab-pane, "
                             f"{len(scope_diag.get('tables', []))} 个表格, "
                             f"{len(scope_diag.get('paginations', []))} 个分页组件")
                    for p in scope_diag.get('panes', []):
                        vis = "✓" if p.get('visible') else "✗"
                        LOG.info(f"    [TAB-SCOPE-DIAG]   {vis} pane[{p.get('index')}] id='{p.get('id', '')}' "
                                 f"tables={p.get('tableCount')} rows={p.get('rowCount')} "
                                 f"paginations={p.get('paginationCount')}")
                    for t in scope_diag.get('tables', []):
                        LOG.info(f"    [TAB-SCOPE-DIAG]   table: inTabPane={t.get('inTabPane')} "
                                 f"paneId='{t.get('paneId', '')}' rows={t.get('rowCount')}")
                    for pg in scope_diag.get('paginations', []):
                        vis = "✓" if pg.get('visible') else "✗"
                        LOG.info(f"    [TAB-SCOPE-DIAG]   pagination: {vis} inTabPane={pg.get('inTabPane')} "
                                 f"paneId='{pg.get('paneId', '')}'")
            except Exception as e:
                LOG.debug(f"    [TAB-SCOPE-DIAG] 诊断失败: {e}")

            MAIN_SEL = self.selectors["table"]["body_row"]
            FIXED_SELS = [
                self.selectors["table"]["fixed_row"],
                self.selectors["table"]["fixed_right_row"],
            ]

            # 先等待 loading mask 消失（最多 5s，不阻断）
            try:
                await self.page.wait_for_selector(
                    '.el-loading-mask', state='hidden', timeout=5000
                )
            except Exception:
                pass  # 可能没有 loading mask

            for attempt in range(5):  # 3 → 5
                # 1. 优先在主体 wrapper 中查找
                main_rows = await self.page.query_selector_all(MAIN_SEL)

                # ★ 过滤：只保留可见 tab-pane 内的行（排除隐藏 tab 的行）
                visible_rows = []
                for row in main_rows:
                    in_visible_pane = await self.page.evaluate("""(el) => {
                        const pane = el.closest('.el-tab-pane');
                        if (!pane) return true;  // 不在 tab-pane 内，保留
                        const style = window.getComputedStyle(pane);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    }""", row)
                    if in_visible_pane:
                        visible_rows.append(row)

                if len(visible_rows) < len(main_rows):
                    LOG.debug(f"    [TAB-SCOPE-FILTER] 过滤隐藏 tab 行: {len(main_rows)} → {len(visible_rows)}")

                for row in visible_rows:
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
                            if idx < len(visible_rows):
                                return visible_rows[idx]
                            else:
                                LOG.warning(f"行索引{idx}超出主体行数{len(visible_rows)}")

                # 未找到，渐进式等待后重试
                if attempt < 4:
                    wait_ms = 1500 if attempt < 2 else 2000
                    LOG.debug(
                        f"未找到包含 '{marker}' 的行，"
                        f"等待 {wait_ms}ms 后重试 (attempt {attempt+1}/5)..."
                    )
                    await self.page.wait_for_timeout(wait_ms)

            LOG.debug(f"当前页 5 次尝试后仍未找到 '{marker}'")
            return None

        except Exception as e:
            LOG.warning(f"当前页查找数据行失败: {e}")
            return None

    async def _find_row_with_pagination(self, marker: str, max_pages: int = 3) -> Optional[Locator]:
        """带分页的行查找：当前页找不到时尝试翻页（最多 max_pages 页）"""
        self._last_pagination_trace = None  # ★ O3: 记录分页查找轨迹
        try:
            # [PAGINATION-DIAG] 诊断：记录分页组件数量和可见性
            try:
                pg_diag = await self.page.evaluate("""() => {
                    const pgs = document.querySelectorAll('.el-pagination');
                    return Array.from(pgs).map((pg, i) => {
                        const pane = pg.closest('.el-tab-pane');
                        const nextBtn = pg.querySelector('.btn-next');
                        return {
                            index: i,
                            inTabPane: !!pane,
                            paneId: pane ? pane.id : '',
                            visible: pg.offsetWidth > 0,
                            hasNextBtn: !!nextBtn,
                            nextBtnDisabled: nextBtn ? nextBtn.disabled : null,
                            nextBtnVisible: nextBtn ? nextBtn.offsetWidth > 0 : false
                        };
                    });
                }""")
                LOG.info(f"    [PAGINATION-DIAG] 找到 {len(pg_diag)} 个分页组件:")
                for pg in pg_diag:
                    vis = "✓" if pg.get('visible') else "✗"
                    next_vis = "✓" if pg.get('nextBtnVisible') else "✗"
                    next_dis = "disabled" if pg.get('nextBtnDisabled') else "enabled"
                    LOG.info(f"    [PAGINATION-DIAG]   {vis} pagination[{pg.get('index')}] "
                             f"inTabPane={pg.get('inTabPane')} paneId='{pg.get('paneId', '')}' "
                             f"nextBtn: {next_vis} {next_dis}")
            except Exception as e:
                LOG.debug(f"    [PAGINATION-DIAG] 诊断失败: {e}")

            # 检查是否有分页组件
            pagination_sel = self.selectors["pagination"]["container"]
            has_pagination = await self.page.evaluate(f"""
                () => !!document.querySelector('{pagination_sel}')
            """)

            if not has_pagination:
                LOG.debug("无分页组件，跳过翻页查找")
                return None

            # 获取当前页码
            active_num_sel = self.selectors["pagination"]["active_number"]
            quicknext_sel = self.selectors["pagination"]["quicknext_number"]
            current_page = await self.page.evaluate(f"""
                () => {{
                    const activeBtn = document.querySelector('{active_num_sel}, {quicknext_sel}');
                    return activeBtn ? parseInt(activeBtn.textContent) : 1;
                }}
            """)
            LOG.info(f"当前页未找到 '{marker}'，当前页码: {current_page}，尝试翻页...")

            # 尝试翻页查找（最多 max_pages 页）
            for page_num in range(current_page + 1, current_page + max_pages + 1):
                # 点击下一页
                next_btn_sel = self.selectors["pagination"]["next_button"]
                next_btn = self.page.locator(next_btn_sel)
                if await next_btn.count() == 0:
                    LOG.debug("已到达最后一页，停止翻页")
                    break

                await next_btn.click()
                await self.page.wait_for_timeout(1500)  # 等待数据加载

                # 在新页查找
                row = await self._find_row_on_current_page(marker)
                if row:
                    LOG.info(f"在第 {page_num} 页找到包含 '{marker}' 的行")
                    # ★ O3: 记录分页查找轨迹
                    self._last_pagination_trace = {
                        "action": "paginate",
                        "direction": "next",
                        "start_page": current_page,
                        "target_found_on_page": page_num,
                        "pages_searched": page_num - current_page,
                        "next_button_selector": next_btn_sel,
                    }
                    return row

            LOG.warning(f"翻页 {max_pages} 页后仍未找到包含 '{marker}' 的行")
            return None

        except Exception as e:
            LOG.warning(f"分页查找失败: {e}")
            return None

    def _load_kb_templates(self) -> dict:
        """加载 KB 模板（统一使用 ProbeKB）"""
        try:
            from ..kb_loader import get_kb
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
        from ..kb_loader import detect_active_overlay_js, get_overlay_prefix

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
        """点击行内按钮（v2 版本）

        降级链（Playwright 原生 click 优先，确保触发 Vue 事件）：
        1. Playwright 原生 click（行内作用域 locator）
        2. KB XPath 模板遍历（Playwright 原生 click，支持 fixed 列 + overlay）
        3. JS btn.click()（最终降级，兼容 Element UI 固定列）

        支持：
        - 隐藏元素过滤（display:none, is-hidden）
        - 覆盖层前缀定位（弹窗/抽屉内的按钮）
        - 点击后等待加载完成
        """
        from ..kb_loader import get_kb, apply_overlay_scope

        row_index = await self._get_row_index(row)
        if row_index < 0:
            LOG.warning("无法获取行索引，使用默认值 0")
            row_index = 0

        button_text_escaped = button_text.replace('\\', '\\\\').replace("'", "\\'")

        # ★ 方法 1: Playwright 原生 click（行内作用域，正确分发浏览器事件）
        try:
            btn_locator = row.locator(
                f"button:has-text('{button_text_escaped}'), "
                f".el-button:has-text('{button_text_escaped}'), "
                f"a:has-text('{button_text_escaped}'), "
                f"span:has-text('{button_text_escaped}')"
            ).first
            if await btn_locator.count() > 0:
                is_visible = await btn_locator.is_visible()
                if is_visible:
                    await btn_locator.click(timeout=3000)
                    LOG.info(f"    ✓ Playwright 原生点击行内按钮 '{button_text}' (行索引={row_index})")
                    from .wait_helpers import wait_for_loading_complete
                    await wait_for_loading_complete(self.page)
                    return True
        except Exception as e:
            LOG.debug(f"Playwright 原生行内点击失败: {e}")

        # ★ 方法 2: KB XPath 模板（Playwright 原生 click，支持 fixed 列）
        kb = get_kb()
        patterns = kb.get_patterns("table-action-button", framework=self.framework)
        overlay_prefix = await self._detect_active_overlay()

        for pattern in patterns:
            xpath = kb.expand_step(
                pattern,
                label=button_text,
                idx=row_index + 1,
                framework=self.framework,
                apply_hidden=True
            )

            if overlay_prefix:
                scoped_xpath = apply_overlay_scope(xpath, overlay_prefix)
                if await self._click_by_xpath_with_wait(scoped_xpath):
                    return True

            if await self._click_by_xpath_with_wait(xpath):
                return True

        # ★ 方法 3: JS click 降级（最后手段，兼容 Element UI 固定列）
        # 单源实现：shared/row_interaction.js_click_row_button（Stage 1/2 兜底统一）
        clicked = await js_click_row_button(self.page, row, button_text)
        if clicked:
            LOG.info(f"    ✓ JS 降级点击行内按钮 '{button_text}' (行索引={row_index})")
            from .wait_helpers import wait_for_loading_complete
            await wait_for_loading_complete(self.page)
            return True

        LOG.warning(f"未找到按钮 '{button_text}' (行索引={row_index})")
        return False

    async def click_row_more_item(self, row: Locator, item_text: str) -> dict:
        """点击行内"更多"下拉菜单项

        使用 KB dropdown-menu 模板的 click-more 步骤 fallback 链。

        支持：
        - 隐藏元素过滤
        - 覆盖层前缀定位
        - 点击后等待加载完成

        Returns:
            dict: {
                "clicked": bool,  # 是否成功点击菜单项
                "expand_strategy": str,  # 展开策略: hover/click/vue/failed
                "actual_item_text": str,  # 实际点击的菜单项文本
            }
        """
        from ..kb_loader import get_kb, apply_overlay_scope

        result = {"clicked": False, "expand_strategy": "failed", "actual_item_text": ""}

        row_index = await self._get_row_index(row)
        if row_index < 0:
            row_index = 0

        kb = get_kb()
        more_patterns = kb.get_patterns("dropdown-menu", framework=self.framework)

        # 检测活跃覆盖层
        overlay_prefix = await self._detect_active_overlay()

        # 查找并点击"更多"按钮（优先在当前行内搜索）
        more_btn = None

        # ★ 优先：直接通过 row Locator 在当前行内查找 .el-dropdown
        try:
            row_dropdown = row.locator('.el-dropdown')
            if await row_dropdown.count() > 0:
                more_btn = row_dropdown.first
                LOG.debug(f"    ✓ 行内直接定位 .el-dropdown (行索引={row_index})")
        except Exception as e:
            LOG.debug(f"    行内直接定位失败: {e}")

        # 如果行内未找到，回退到 KB XPath 模板搜索
        if not more_btn:
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
            return result

        # 展开下拉菜单（Element UI 默认 hover 触发，click 兜底）
        # Element UI 的 mouseenter 监听器绑定在 .el-dropdown 父元素上，
        # 仅 hover 内部 <span> 可能无法触发 Vue 的事件处理器。
        expanded = False
        expand_strategy = "failed"

        # [DEBUG] 诊断 row 和 more_btn 的类型
        LOG.info(f"    [DIAG-row-type] row: type={type(row).__name__}, "
                 f"has_element_handle={hasattr(row, 'element_handle')}, "
                 f"module={type(row).__module__}")
        LOG.info(f"    [DIAG-more_btn-type] more_btn: type={type(more_btn).__name__}, "
                 f"has_element_handle={hasattr(more_btn, 'element_handle')}, "
                 f"module={type(more_btn).__module__}")

        # ★ 优先尝试：直接使用 scoped 到当前行的 .el-dropdown 展开
        # ★ 修复双 tab bug：直接传递行 DOM 元素，不再通过全局 querySelectorAll + rowIndex
        # row 可能是 ElementHandle (query_selector_all 返回) 或 Locator，需兼容
        row_el = row  # ElementHandle 可直接传给 page.evaluate
        if more_btn and row_el:
            try:
                # 获取当前行的 .el-dropdown 元素
                scoped_dropdown = await self.page.evaluate("""(targetRow) => {
                    if (!targetRow) return null;
                    const dropdown = targetRow.querySelector('.el-dropdown');
                    return dropdown ? true : null;
                }""", row_el)

                if scoped_dropdown:
                    # 通过 JS 直接触发当前行的 dropdown
                    expanded = await self.page.evaluate("""(targetRow) => {
                        if (!targetRow) return false;
                        const dropdown = targetRow.querySelector('.el-dropdown');
                        if (!dropdown) return false;

                        // 方法 1: 调用 Vue 实例的 show 方法
                        if (dropdown.__vue__) {
                            const vm = dropdown.__vue__;
                            if (typeof vm.show === 'function') {
                                vm.show();
                                return true;
                            }
                            if (typeof vm.handleMouseEnter === 'function') {
                                vm.handleMouseEnter();
                                return true;
                            }
                        }

                        // 方法 2: 派发 mouseenter 事件
                        dropdown.dispatchEvent(new MouseEvent('mouseenter', {bubbles: false}));
                        dropdown.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
                        return true;
                    }""", row_el)

                    if expanded:
                        await self.page.wait_for_timeout(600)
                        # 检查菜单是否真正展开
                        expanded = await self.page.evaluate("""(targetRow) => {
                            if (!targetRow) return false;

                            // 查找与当前行 dropdown 关联的 popper
                            const allPoppers = Array.from(document.querySelectorAll('.el-dropdown-menu'));
                            const visiblePoppers = allPoppers.filter(p =>
                                p.offsetWidth > 0 && p.offsetHeight > 0
                            );
                            return visiblePoppers.length > 0;
                        }""", row_el)

                        if expanded:
                            LOG.debug(f"    ✓ scoped hover 展开成功 (行索引={row_index})")
                            expand_strategy = "hover"
            except Exception as e:
                LOG.debug(f"scoped hover on row dropdown failed: {e}")
                expanded = False

        # Fallback: 使用全局的 more_btn 展开
        if not expanded:
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
                # 检查菜单是否展开（限定在可见菜单容器内查找子项）
                expanded = await self.page.evaluate("""() => {
                    const _menus = document.querySelectorAll('.el-dropdown-menu');
                    const _visibleMenu = Array.from(_menus).find(m => m.offsetWidth > 0 && m.offsetHeight > 0);
                    if (!_visibleMenu) return false;
                    return Array.from(_visibleMenu.querySelectorAll('.el-dropdown-menu__item'))
                        .some(el => el.offsetWidth > 0);
                }""")
                if expanded:
                    LOG.debug(f"    ✓ hover 展开成功 (行索引={row_index})")
                    expand_strategy = "hover"
            except Exception as e:
                LOG.debug(f"hover + dispatch on more button failed: {e}")

        if not expanded:
            # hover 未展开，尝试 click（某些 dropdown 配置 trigger="click"）
            LOG.debug(f"    ✗ hover 未展开，尝试 click (行索引={row_index})")
            try:
                await more_btn.click()
                await self.page.wait_for_timeout(600)
                expanded = await self.page.evaluate("""() => {
                    const _menus = document.querySelectorAll('.el-dropdown-menu');
                    const _visibleMenu = Array.from(_menus).find(m => m.offsetWidth > 0 && m.offsetHeight > 0);
                    if (!_visibleMenu) return false;
                    return Array.from(_visibleMenu.querySelectorAll('.el-dropdown-menu__item'))
                        .some(el => el.offsetWidth > 0);
                }""")
                if expanded:
                    LOG.debug(f"    ✓ click 展开成功 (行索引={row_index})")
                    expand_strategy = "click"
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
                    expand_strategy = "vue"
                    await self.page.wait_for_timeout(600)
                else:
                    LOG.debug(f"    ✗ Vue 实例未找到 (行索引={row_index})")
            except Exception as e:
                LOG.debug(f"Vue instance show() fallback failed: {e}")

        if not expanded:
            LOG.warning(f"    ✗ 所有展开策略均失败 (行索引={row_index})")
            result["expand_strategy"] = expand_strategy
            return result

        # 展开成功后，限定在当前行的下拉菜单中查找并点击目标项
        # 不再调用全局的 click_dropdown_option（它可能找到其他已关闭菜单的项）
        # ★ 修复双 tab bug：直接传递行 DOM 元素，不再通过全局 querySelectorAll + rowIndex
        clicked_item = await self.page.evaluate(f"""(targetRow) => {{
            // 步骤 1: 定位当前行的 .el-dropdown 元素
            if (!targetRow) {{
                return {{ clicked: false, error: 'Row not found' }};
            }}

            const rowDropdown = targetRow.querySelector('.el-dropdown');
            if (!rowDropdown) {{
                return {{ clicked: false, error: 'No dropdown in row' }};
            }}

            // 步骤 2: 找到与当前行 dropdown 关联的 popper
            // Element UI 会为每个 dropdown 创建一个 popper，通过位置或 Vue 实例关联
            const allPoppers = Array.from(document.querySelectorAll('.el-dropdown-menu'));
            const visiblePoppers = allPoppers.filter(p =>
                p.offsetWidth > 0 && p.offsetHeight > 0
                && !p.closest('[style*="display: none"]')
            );

            // 找到与当前 rowDropdown 关联的 popper（通过 Vue 实例或位置匹配）
            let targetPopper = null;

            // 方法 1: 通过 Vue 实例关联
            if (rowDropdown.__vue__) {{
                const vm = rowDropdown.__vue__;
                if (vm.popperElm) {{
                    targetPopper = vm.popperElm;
                }}
            }}

            // 方法 2: 如果只有一个可见 popper，直接使用
            if (!targetPopper && visiblePoppers.length === 1) {{
                targetPopper = visiblePoppers[0];
            }}

            // 方法 3: 通过位置匹配（popper 应该在 rowDropdown 附近）
            if (!targetPopper && visiblePoppers.length > 0) {{
                const dropdownRect = rowDropdown.getBoundingClientRect();
                targetPopper = visiblePoppers.find(popper => {{
                    const popperRect = popper.getBoundingClientRect();
                    // popper 应该在 dropdown 下方或附近
                    return Math.abs(popperRect.left - dropdownRect.left) < 200
                        && popperRect.top >= dropdownRect.top - 50;
                }});
            }}

            // 如果找不到 targetPopper，使用所有可见 popper 作为 fallback
            const searchPoppers = targetPopper ? [targetPopper] : visiblePoppers;

            // 步骤 3: 在目标 popper 中查找菜单项
            const items = [];
            searchPoppers.forEach(popper => {{
                popper.querySelectorAll('.el-dropdown-menu__item').forEach(item => {{
                    items.push(item);
                }});
            }});

            const visibleItems = items.filter(el =>
                el.offsetWidth > 0 && el.offsetHeight > 0
                && !el.closest('[style*="display: none"]')
            );

            // 先精确匹配，再包含匹配
            let target = visibleItems.find(el => el.textContent.trim() === '{item_text}');
            if (!target) {{
                target = visibleItems.find(el => el.textContent.trim().includes('{item_text}'));
            }}

            // [DEBUG] 列出所有菜单项及其状态
            const allItemsInfo = items.map(el => ({{
                text: el.textContent.trim(),
                cls: el.className,
                visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                disabled: el.classList.contains('cmss-disabled') || el.classList.contains('is-disabled')
            }}));

            if (target) {{
                const text = target.textContent.trim();
                const cls = target.className;
                const hasDisabledClass = cls.includes('cmss-disabled') || cls.includes('is-disabled') || cls.includes('disabled');

                // ★ 深度诊断：检查 Vue 组件链上的事件绑定 + cmss-disabled 来源
                const diagInfo = {{ targetHasVue: false, targetListeners: [], parentChain: [], cmssSource: null }};

                // 检查 target 自身的 Vue 实例
                if (target.__vue__) {{
                    diagInfo.targetHasVue = true;
                    const vm = target.__vue__;
                    diagInfo.targetListeners = Object.keys(vm.$listeners || {{}});
                    diagInfo.targetProps = {{
                        command: vm.command,
                        disabled: vm.disabled,
                        divided: vm.divided
                    }};
                }}

                // ★ 查找 cmss-disabled 的来源：
                // 1. 检查 ElDropdownItem 的 mixin 内容（computed、methods、updated/mounted）
                // 2. 检查 Vuex store 状态
                // 3. 检查 provide/inject 链
                // 4. 检查父组件（非 Vue）的 data-* 属性

                // Step 1: 检查 ElDropdownItem 的 mixin 内容
                if (target.__vue__ && target.__vue__.$options.mixins) {{
                    const mixins = target.__vue__.$options.mixins;
                    diagInfo.mixinDetails = mixins.map((mixin, idx) => {{
                        const computed = mixin.computed ? Object.keys(mixin.computed) : [];
                        const methods = mixin.methods ? Object.keys(mixin.methods) : [];
                        const hasUpdated = typeof mixin.updated === 'function';
                        const hasMounted = typeof mixin.mounted === 'function';
                        const hasCreated = typeof mixin.created === 'function';

                        // 检查 computed 中是否有 cmss/disabled 相关的
                        const cmssRelatedComputed = computed.filter(k =>
                            k.toLowerCase().includes('cmss') ||
                            k.toLowerCase().includes('disabled') ||
                            k.toLowerCase().includes('enable') ||
                            k.toLowerCase().includes('status') ||
                            k.toLowerCase().includes('switch')
                        );

                        return {{
                            idx,
                            computed,
                            methods,
                            hasUpdated,
                            hasMounted,
                            hasCreated,
                            cmssRelatedComputed
                        }};
                    }});
                }}

                // Step 2: 检查 Vuex store
                const vm = target.__vue__;
                if (vm && vm.$store) {{
                    const store = vm.$store;
                    const stateKeys = store.state ? Object.keys(store.state) : [];
                    const gettersKeys = store.getters ? Object.keys(store.getters) : [];

                    // 查找 store 中与 cmss/disabled/switch/status 相关的
                    const cmssRelatedState = stateKeys.filter(k =>
                        k.toLowerCase().includes('cmss') ||
                        k.toLowerCase().includes('disabled') ||
                        k.toLowerCase().includes('enable') ||
                        k.toLowerCase().includes('status') ||
                        k.toLowerCase().includes('switch') ||
                        k.toLowerCase().includes('black') ||
                        k.toLowerCase().includes('white')
                    );

                    // 深入检查 blackList 状态
                    const blackListState = store.state.blackList || {{}};
                    const blackListKeys = Object.keys(blackListState);
                    const blackListDetails = {{}};
                    for (const key of blackListKeys) {{
                        const val = blackListState[key];
                        if (val === null || val === undefined) {{
                            blackListDetails[key] = null;
                        }} else if (typeof val === 'boolean' || typeof val === 'number' || typeof val === 'string') {{
                            blackListDetails[key] = val;
                        }} else if (Array.isArray(val)) {{
                            blackListDetails[key] = `Array(${{val.length}})`;
                        }} else if (typeof val === 'object') {{
                            // ★ 特殊处理：打印 blackWhiteConfig 的完整内容
                            if (key === 'blackWhiteConfig') {{
                                blackListDetails[key] = JSON.stringify(val);
                            }} else {{
                                blackListDetails[key] = Object.keys(val).slice(0, 10);
                            }}
                        }}
                    }}

                    diagInfo.vuexStore = {{
                        hasStore: true,
                        stateKeys: stateKeys.slice(0, 20),
                        gettersKeys: gettersKeys.slice(0, 20),
                        cmssRelatedState,
                        totalStateKeys: stateKeys.length,
                        blackListKeys,
                        blackListDetails
                    }};
                }}

                // Step 3: 检查 provide/inject 链
                if (vm) {{
                    const injectChain = [];
                    let current = vm;
                    while (current) {{
                        if (current._provided) {{
                            const providedKeys = Object.keys(current._provided);
                            if (providedKeys.length > 0) {{
                                injectChain.push({{
                                    name: current.$options.name || 'anonymous',
                                    providedKeys
                                }});
                            }}
                        }}
                        current = current.$parent;
                    }}
                    if (injectChain.length > 0) {{
                        diagInfo.provideChain = injectChain;
                    }}
                }}

                // Step 4: 检查父组件（非 Vue）的 data-* 属性
                const parentChain = [];
                let el = target.parentElement;
                let depth = 0;
                while (el && depth < 10) {{
                    const dataAttrs = {{}};
                    for (const attr of el.attributes) {{
                        if (attr.name.startsWith('data-')) {{
                            dataAttrs[attr.name] = attr.value.substring(0, 50);
                        }}
                    }}
                    if (Object.keys(dataAttrs).length > 0) {{
                        parentChain.push({{
                            depth,
                            tag: el.tagName,
                            cls: (el.className || '').substring(0, 50),
                            dataAttrs
                        }});
                    }}
                    el = el.parentElement;
                    depth++;
                }}
                if (parentChain.length > 0) {{
                    diagInfo.parentDataAttributes = parentChain;
                }}

                // ★ 检查按钮是否被禁用（cmss-disabled 或 is-disabled）
                // 如果按钮被禁用，不执行点击，返回 disabled 状态
                if (hasDisabledClass) {{
                    return {{
                        clicked: false,
                        reason: 'button-disabled',
                        text: text,
                        cls: cls,
                        hasDisabledClass: hasDisabledClass,
                        diagInfo: diagInfo,
                        visibleCount: visibleItems.length,
                        allItems: allItemsInfo,
                        scopedToRow: true,
                        popperMethod: targetPopper ? 'matched' : 'fallback',
                        postClickState: {{}}
                    }};
                }}

                // ★ 使用 dispatchEvent 确保事件冒泡
                target.dispatchEvent(new MouseEvent('click', {{bubbles: true, cancelable: true, view: window}}));

                // [DEBUG] 点击后立即检查 DOM 状态变化
                const postClickState = {{
                    messageBoxVisible: document.querySelector('.el-message-box__wrapper:not([style*="display: none"])') ? true : false,
                    messageBoxText: document.querySelector('.el-message-box__message')?.textContent?.trim()?.substring(0, 100) || '',
                    elMessageVisible: document.querySelector('.el-message:not([style*="display: none"])') ? true : false,
                    elMessageText: document.querySelector('.el-message__content')?.textContent?.trim()?.substring(0, 100) || '',
                    elNotificationVisible: document.querySelector('.el-notification:not([style*="display: none"])') ? true : false,
                    elNotificationText: document.querySelector('.el-notification__content')?.textContent?.trim()?.substring(0, 100) || ''
                }};

                return {{
                    clicked: true,
                    text: text,
                    cls: cls,
                    hasDisabledClass: hasDisabledClass,
                    diagInfo: diagInfo,
                    visibleCount: visibleItems.length,
                    allItems: allItemsInfo,
                    scopedToRow: true,
                    popperMethod: targetPopper ? 'matched' : 'fallback',
                    postClickState: postClickState
                }};
            }}

            // 没找到可见项，返回诊断信息
            return {{
                clicked: false,
                text: '',
                visibleCount: visibleItems.length,
                totalItems: items.length,
                scopedToRow: true,
                allItems: items.map(el => ({{
                    text: el.textContent.trim(),
                    visible: el.offsetWidth > 0 && el.offsetHeight > 0,
                    parentDisplay: el.parentElement ? window.getComputedStyle(el.parentElement).display : ''
                }}))
            }};
        }}""", row_el)

        LOG.info(f"    [dropdown-click] 结果: {clicked_item}")

        if clicked_item and clicked_item.get("clicked"):
            result["clicked"] = True
            result["expand_strategy"] = expand_strategy
            result["actual_item_text"] = clicked_item.get("text", item_text)
            from .wait_helpers import wait_for_loading_complete
            await wait_for_loading_complete(self.page)
        elif clicked_item and clicked_item.get("reason") == "button-disabled":
            # ★ 按钮被禁用（cmss-disabled/is-disabled），直接返回，不走全局 fallback
            # 全局 fallback 会绕过 cmss-disabled 检查，导致点错按钮
            LOG.info(f"    [dropdown-click] 按钮被禁用 (cmss-disabled/is-disabled)，跳过")
            result["clicked"] = False
            result["reason"] = "button-disabled"
            result["actual_item_text"] = clicked_item.get("text", item_text)
        else:
            # 可见菜单中没有找到，尝试全局 fallback
            click_result = await click_dropdown_option(self.page, item_text)
            result["clicked"] = click_result.get("clicked", False) if isinstance(click_result, dict) else click_result
            result["expand_strategy"] = expand_strategy
            result["actual_item_text"] = click_result.get("actual_text", item_text) if isinstance(click_result, dict) else item_text
        return result

    async def click_row_name_link(self, row, link_class: str = "") -> bool:
        """★ 批五 F4：点击行内名称单元格链接（row_link 语义操作，Stage 1/2 同源）。

        归并后的「查看详情（名称链接）」操作文本是语义名（页面上不存在），
        不能按文本定位——优先按探测期归并采集的 link_class（如
        table-span-btn）定位行内名称链接；无 link_class/未命中时用通用
        结构性判据兜底：行内第一个 cursor:pointer 的 A/SPAN（排除
        .el-dropdown 内部元素，防误点「更多」触发器）。

        点击手法与 click_row_more_item 菜单项同款（JS dispatchEvent click
        + bubbles，Vue @click 事件可靠触发，实测路径）。

        Args:
            row: 行 Locator 或 ElementHandle（与 click_row_more_item 兼容同款）
            link_class: 探测期多数派 className（可含复合 class 空格分隔）

        Returns:
            bool: 是否成功点击
        """
        from .wait_helpers import wait_for_loading_complete

        # 兼容 ElementHandle / Locator 入参（与 click_row_more_item 同款处理）
        row_handle = row
        if hasattr(row, "element_handle"):
            try:
                row_handle = await row.element_handle()
            except Exception:
                row_handle = row

        tokens = [t for t in (link_class or "").split() if t]
        try:
            clicked = await self.page.evaluate("""(args) => {
                const rowEl = args.rowEl;
                const tokens = args.tokens || [];
                if (!rowEl) return false;
                let target = null;
                if (tokens.length) {
                    try {
                        target = rowEl.querySelector('.' + tokens.join('.'));
                    } catch (e) { target = null; }
                }
                if (!target) {
                    const candidates = rowEl.querySelectorAll('a, span');
                    for (const el of candidates) {
                        if (el.closest('.el-dropdown')) continue;
                        if (el.classList.contains('el-dropdown-link')) continue;
                        if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                        const style = window.getComputedStyle(el);
                        if (style.pointerEvents === 'none' || style.cursor !== 'pointer') continue;
                        target = el;
                        break;
                    }
                }
                if (!target) return false;
                target.scrollIntoView({block: 'center'});
                target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                return true;
            }""", {"rowEl": row_handle, "tokens": tokens})
        except Exception as e:
            LOG.debug(f"    row_name_link 点击异常: {e}")
            clicked = False

        if clicked:
            LOG.info(f"    ✓ 点击行内名称链接 (class='{link_class or 'pointer 兜底'}')")
            await wait_for_loading_complete(self.page)
            return True

        LOG.warning("    未找到行内名称链接 (row_name_link)")
        return False

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
                        // 排除隐藏/disabled
                        if (btn.disabled || btn.classList.contains('is-disabled')) return false;
                        if (btn.closest('.is-hidden') || btn.closest('[style*="display: none"]')) return false;
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
                    const menuItems = document.querySelectorAll('.el-dropdown-menu__item');
                    return Array.from(menuItems)
                        .filter(item => item.offsetWidth > 0
                            && !item.disabled
                            && !item.classList.contains('is-disabled')
                            && !item.closest('.is-hidden')
                            && !item.closest('[style*="display: none"]'))
                        .map(item => item.textContent.trim()).filter(t => t);
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


async def click_button(page: Page, text: str, max_retries: int = 3, ui_framework: str = "element-ui") -> bool:
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
        ui_framework: UI 框架名称

    Returns:
        是否成功点击
    """
    from .wait_helpers import wait_for_loading_complete
    from .locator_helpers import safe_css
    selectors = const.get_ui_selectors(ui_framework)
    button_base = selectors["button"]["base"]

    for attempt in range(max_retries):
        try:
            # 策略 1: Playwright click（自动等待可见性）+ 隐藏过滤
            safe_sel = safe_css(f'button:has-text("{text}"), {button_base}:has-text("{text}")', ui_framework)
            button = page.locator(safe_sel).first
            if await button.count() > 0:
                await button.click(timeout=3000)
                await wait_for_loading_complete(page)
                return True

            # 策略 2: 查找并滚动到可见（带完整隐藏过滤）
            hidden_css = const.HIDDEN_FILTERS_CSS.get(ui_framework, const.HIDDEN_FILTERS_CSS['_universal'])
            buttons = await page.query_selector_all(f'button:has-text("{text}"):visible{hidden_css}, {button_base}:has-text("{text}"):visible{hidden_css}')
            if buttons:
                await buttons[0].scroll_into_view_if_needed()
                await page.wait_for_timeout(300)
                await buttons[0].click()
                await wait_for_loading_complete(page)
                return True

            # 策略 3: JavaScript click（绕过遮挡）+ 隐藏/disabled 检查
            clicked = await page.evaluate(f"""
                () => {{
                    const buttons = Array.from(document.querySelectorAll('button, {button_base}'));
                    const target = buttons.find(btn => {{
                        const textMatch = btn.textContent.trim().includes('{text}');
                        const notHidden = !btn.closest('.is-hidden') && !btn.closest('[style*="display: none"]');
                        const notDisabled = !btn.disabled && !btn.classList.contains('is-disabled');
                        return textMatch && notHidden && notDisabled;
                    }});
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
                    const all = Array.from(document.querySelectorAll('button, {button_base}'));
                    const matches = all.filter(b => b.textContent.trim().includes('{text}'));
                    return {{
                        totalButtons: all.length,
                        matchingText: matches.length,
                        matches: matches.slice(0, 5).map(b => ({{
                            text: b.textContent.trim().slice(0, 30),
                            visible: b.offsetWidth > 0 && b.offsetHeight > 0,
                            hidden: !!b.closest('.is-hidden') || !!b.closest('[style*="display: none"]'),
                            disabled: b.disabled || b.classList.contains('is-disabled'),
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


async def click_dropdown_option(page: Page, option_text: str, ui_framework: str = "element-ui") -> dict:
    """点击下拉菜单选项。

    点击成功后等待加载完成（可能触发后续操作）。
    快速路径：先检查可见性，不可见时直接走 JS click，避免 30s 超时。

    Returns:
        dict: {
            "clicked": bool,
            "actual_text": str  # 实际点击的选项文本（去空格后）
        }
    """
    from .wait_helpers import wait_for_loading_complete
    selectors = const.get_ui_selectors(ui_framework)
    dropdown_menu_item = selectors["dropdown"]["menu_item"]
    select_dropdown_item = selectors["select"]["dropdown_item"]

    try:
        # 快速检查：元素是否存在且可见（限定在可见菜单容器内）
        visible_info = await page.evaluate(f"""() => {{
            const _menus = document.querySelectorAll('.el-dropdown-menu, .el-select-dropdown');
            const _visibleMenu = Array.from(_menus).find(m => m.offsetWidth > 0 && m.offsetHeight > 0);
            if (!_visibleMenu) return {{ status: 'not_found', details: null }};
            const items = Array.from(_visibleMenu.querySelectorAll(
                '{dropdown_menu_item}, {select_dropdown_item}'));
            const target = items.find(
                item => item.textContent.trim().includes('{option_text}'));
            if (!target) return {{ status: 'not_found', details: null }};

            const style = window.getComputedStyle(target);
            const rect = target.getBoundingClientRect();
            return {{
                status: (target.offsetWidth > 0 && target.offsetHeight > 0) ? 'visible' : 'hidden',
                details: {{
                    offsetWidth: target.offsetWidth,
                    offsetHeight: target.offsetHeight,
                    rect: {{ width: rect.width, height: rect.height, top: rect.top, left: rect.left }},
                    display: style.display,
                    visibility: style.visibility,
                    opacity: style.opacity,
                    className: target.className,
                    parentClassName: target.parentElement ? target.parentElement.className : '',
                    parentDisplay: target.parentElement ? window.getComputedStyle(target.parentElement).display : ''
                }}
            }};
        }}""")

        visible = visible_info.get('status', 'not_found')
        LOG.info(f"    [DEBUG-dropdown-option] option_text='{option_text}', visible={visible}")
        if visible == 'hidden' and visible_info.get('details'):
            LOG.info(f"    [DEBUG-dropdown-option] hidden 原因: {visible_info['details']}")

        if visible == 'not_found':
            return {"clicked": False, "actual_text": ""}

        if visible == 'visible':
            # 可见 → Playwright click（快速成功）
            option = page.locator(
                f'{dropdown_menu_item}:has-text("{option_text}"), '
                f'{select_dropdown_item}:has-text("{option_text}")'
            ).first
            if await option.count() > 0:
                try:
                    await option.click(timeout=3000)
                    LOG.info(f"    [DEBUG-dropdown-option] Playwright click 成功")
                    await wait_for_loading_complete(page)
                    return {"clicked": True, "actual_text": option_text}
                except Exception as e:
                    LOG.warning(f"    [DEBUG-dropdown-option] Playwright click 失败: {e}")

        # 不可见或 Playwright click 失败 → JS click（限定在可见菜单内，强制触发）
        clicked_text = await page.evaluate(f"""
            () => {{
                const _menus = document.querySelectorAll('.el-dropdown-menu, .el-select-dropdown');
                const _visibleMenu = Array.from(_menus).find(m => m.offsetWidth > 0 && m.offsetHeight > 0);
                if (!_visibleMenu) return null;
                const items = Array.from(_visibleMenu.querySelectorAll(
                    '.el-dropdown-menu__item, .el-select-dropdown__item'));
                const target = items.find(
                    item => item.textContent.trim().includes('{option_text}'));
                if (target) {{
                    const text = target.textContent.trim().replace(/\\s+/g, '');
                    // 先尝试触发 Vue 事件
                    const vueEvent = new MouseEvent('click', {{ bubbles: true, cancelable: true }});
                    target.dispatchEvent(vueEvent);
                    // 也直接调用 click()
                    target.click();
                    return text;
                }}
                return null;
            }}
        """)

        LOG.info(f"    [DEBUG-dropdown-option] JS click 结果: clicked_text={clicked_text}")

        if clicked_text:
            await wait_for_loading_complete(page)
            return {"clicked": True, "actual_text": clicked_text}

        return {"clicked": False, "actual_text": ""}

    except Exception as e:
        LOG.warning(f"点击下拉选项失败: {e}")
        return {"clicked": False, "actual_text": ""}


async def _preprocess_confirm_form(page: Page) -> dict:
    """确认弹窗表单预处理（方案批三 3.3）：填确认词 + 保守勾选首项。

    危险操作确认弹窗常带两类表单元素，纯点击确认会因表单校验失败：
      1. 「输入确认词」输入项：placeholder 含引号包裹关键词（GitHub 式
         「请输入 "DELETE" 确认删除」）→ 正则提取后填入（触发 input/change
         事件供 Vue v-model 响应）。不硬编码 DELETE，任意系统同构适用。
      2. 未选中的 radio-group / checkbox：点首个 enabled 项（EIS 删除弹窗
         「关联资源」选项）。保守条件：仅当弹窗内无其他可见文本输入框
         （确认词输入项除外）才处理，防复杂表单弹窗误伤。

    estack 纯文本 message-box（删除确认）无表单元素 → 扫描零命中 →
    行为逐字节不变。confirm_dialog 是所有确认流的唯一收口点，
    Stage 1/2 同源执行此预处理，三阶段行为一致。

    Returns:
        {"filled": [...], "radio_groups": int, "checkboxes": int,
         "other_text_inputs": int}（无弹窗/零命中时各计数字段为 0）
    """
    return await page.evaluate("""() => {
        const containers = document.querySelectorAll(
            '.el-message-box__wrapper:not([style*="display: none"]), ' +
            '.el-dialog__wrapper:not([style*="display: none"]), ' +
            '.ant-modal-wrap:not([style*="display: none"])'
        );
        let container = null;
        for (const c of containers) {
            if (c.offsetWidth > 0 && c.offsetHeight > 0) { container = c; break; }
        }
        const actions = { filled: [], radio_groups: 0, checkboxes: 0, other_text_inputs: 0 };
        if (!container) return actions;

        const isVisible = (el) => el.offsetWidth > 0 && el.offsetHeight > 0;

        // 1. 确认词输入项：placeholder 引号包裹词提取填入
        const inputs = container.querySelectorAll('input');
        for (const input of inputs) {
            const type = (input.getAttribute('type') || 'text').toLowerCase();
            if (type === 'hidden' || type === 'file' || type === 'checkbox' || type === 'radio') continue;
            if (!isVisible(input) || input.disabled) continue;
            const ph = input.getAttribute('placeholder') || '';
            const m = ph.match(/["“”'‘’「『]([^"“”'‘’」』]{1,40})["“”'‘’」』]/);
            if (m && m[1]) {
                if (!input.value) {
                    input.value = m[1];
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    actions.filled.push({ placeholder: ph, word: m[1] });
                }
                // 确认词输入项不计入「其他文本输入框」（无论本轮是否已填）
                continue;
            }
            if (type === 'text' || type === '' || type === 'search' || type === 'password') {
                actions.other_text_inputs++;
            }
        }

        // 2. radio-group / checkbox-group：无选中态时点首个 enabled 项。
        //    保守条件：弹窗内存在其他可见文本输入框时不处理（复杂表单防误伤）
        if (actions.other_text_inputs === 0) {
            for (const group of container.querySelectorAll(
                '.el-radio-group, .el-checkbox-group, .ant-radio-group, .ant-checkbox-group'
            )) {
                if (!isVisible(group)) continue;
                if (group.querySelector('.is-checked, .is-active, input:checked')) continue;
                const items = group.querySelectorAll(
                    '.el-radio, .el-radio-button, .el-checkbox, .el-checkbox-button, ' +
                    '.ant-radio-wrapper, .ant-checkbox-wrapper, label'
                );
                for (const item of items) {
                    if (!isVisible(item)) continue;
                    if (item.classList.contains('is-disabled')) continue;
                    const innerInput = item.querySelector('input');
                    if (innerInput && innerInput.disabled) continue;
                    try { item.click(); actions.radio_groups++; } catch (e) {}
                    break;
                }
            }
            // 独立 checkbox（不在 group 内）：未勾选且非禁用则勾选
            for (const cb of container.querySelectorAll('.el-checkbox, .ant-checkbox-wrapper')) {
                if (cb.closest('.el-checkbox-group, .ant-checkbox-group')) continue;
                if (!isVisible(cb)) continue;
                if (cb.classList.contains('is-checked')) continue;
                const innerInput = cb.querySelector('input');
                if (innerInput && (innerInput.disabled || innerInput.checked)) continue;
                try { cb.click(); actions.checkboxes++; } catch (e) {}
            }
        }
        return actions;
    }""")


async def confirm_dialog(page: Page, confirm: bool = True, ui_framework: str = "element-ui",
                         screenshot_holder: dict = None) -> str:
    """处理确认弹窗（Element UI MessageBox / Popconfirm / 通用模态框）。

    支持三种确认 UI：
    1. el-message-box: MessageBox.confirm()
    2. el-popconfirm: <el-popconfirm> 气泡确认框
    3. 通用模态框: 任何包含确认按钮的可见对话框

    按钮文本匹配统一使用去空格归一化（const.xpath_normalize_in / const.js_normalize_in），
    兼容 "确 定" / "确定" 等空格变体。

    Args:
        page: Playwright 页面对象
        confirm: True 点击确认，False 点击取消
        ui_framework: UI 框架名称，用于选择对应的隐藏过滤器

    Returns:
        点击的按钮文本（如 "确定"、"确认"），失败返回空字符串
    """
    from .wait_helpers import wait_for_loading_complete

    # 获取隐藏过滤器
    hidden_filter = const.HIDDEN_FILTERS.get(ui_framework, const.HIDDEN_FILTERS['_universal'])

    # 生成 XPath 归一化谓词（去空格后匹配）
    confirm_xpath_pred = const.xpath_normalize_in(const.CONFIRM_TEXTS)
    cancel_xpath_pred = const.xpath_normalize_in(const.CANCEL_TEXTS)

    # 生成 JS 归一化检测表达式
    confirm_js_check = const.js_normalize_in(const.CONFIRM_TEXTS, "txt")

    try:
        # 等待确认框出现（最多 2 秒）
        for _ in range(4):
            has_confirm = await page.evaluate(f"""() => {{
                // 1. el-message-box（最高优先级）
                const msgBox = document.querySelector('.el-message-box__wrapper:not([style*="display: none"])');
                if (msgBox && msgBox.offsetWidth > 0) return 'message-box';

                // 2. el-popconfirm
                const popconfirm = document.querySelector('.el-popconfirm:not([style*="display: none"])');
                if (popconfirm && popconfirm.offsetWidth > 0) return 'popconfirm';

                // 3. 通用对话框（包含确认按钮）- 增强过滤
                const dialogs = document.querySelectorAll('.el-dialog__wrapper:not([style*="display: none"]), .ant-modal-wrap:not([style*="display: none"])');
                for (const dialog of dialogs) {{
                    if (dialog.offsetWidth <= 0 || dialog.offsetHeight <= 0) continue;
                    const inner = dialog.querySelector('.el-dialog, .ant-modal');
                    if (inner && inner.offsetHeight <= 0) continue;

                    const btns = dialog.querySelectorAll('button');
                    const btnTexts = [];
                    for (const btn of btns) {{
                        const txt = btn.textContent.trim();
                        btnTexts.push(txt);
                        if ({confirm_js_check}) {{
                            return 'generic-dialog';
                        }}
                    }}
                    return {{type: 'no-match', buttons: btnTexts}};
                }}
                return null;
            }}""")

            if has_confirm:
                break
            await page.wait_for_timeout(500)

        if not has_confirm:
            LOG.debug("未检测到确认框容器")
            return ""

        LOG.debug(f"检测到确认框类型: {has_confirm}")

        # ★ 确认弹窗表单预处理（方案批三 3.3）：填确认词输入项 + 保守勾选
        # 首个 radio/checkbox 选项。仅 confirm=True 时处理（取消路径无需填表）；
        # 预处理失败不阻断确认点击（保持既有流程兜底）。
        if confirm:
            try:
                preprocess_actions = await _preprocess_confirm_form(page)
                if (preprocess_actions.get("filled")
                        or preprocess_actions.get("radio_groups")
                        or preprocess_actions.get("checkboxes")):
                    LOG.info(f"确认弹窗表单预处理: {preprocess_actions}")
                    await page.wait_for_timeout(300)
            except Exception as _pre_e:
                LOG.debug(f"确认弹窗表单预处理失败（忽略，继续点击确认）: {_pre_e}")

        # 按容器类型定位按钮（统一使用 XPath 去空格归一化）
        button = None

        if has_confirm == 'message-box':
            scope = ".el-message-box__btns"
            if confirm:
                # 优先: primary 按钮
                button = page.locator(f'{scope} button.el-button--primary').first
                if await button.count() == 0:
                    button = page.locator(f"xpath={scope}//button[{confirm_xpath_pred} and {hidden_filter}]").first
            else:
                button = page.locator(f"xpath={scope}//button[{cancel_xpath_pred} and {hidden_filter}]").first

        elif has_confirm == 'popconfirm':
            scope = ".el-popconfirm__action"
            if confirm:
                button = page.locator(f'{scope} button.el-button--primary').first
                if await button.count() == 0:
                    button = page.locator(f"xpath={scope}//button[{confirm_xpath_pred} and {hidden_filter}]").first
            else:
                button = page.locator(f"xpath={scope}//button[{cancel_xpath_pred} and {hidden_filter}]").first

        else:  # generic-dialog — 优先使用 JS click（可靠触发 Vue 事件）
            target_texts = const.CONFIRM_TEXTS if confirm else const.CANCEL_TEXTS
            target_json = json.dumps(target_texts, ensure_ascii=False)
            js_clicked = await page.evaluate(f"""() => {{
                const targetTexts = {target_json};
                const dialogs = document.querySelectorAll(
                    '.el-dialog__wrapper:not([style*="display: none"]), ' +
                    '.ant-modal-wrap:not([style*="display: none"])'
                );
                for (const dialog of dialogs) {{
                    if (dialog.offsetWidth <= 0 || dialog.offsetHeight <= 0) continue;
                    const buttons = dialog.querySelectorAll('button');
                    for (const btn of buttons) {{
                        if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                        if (btn.disabled || btn.classList.contains('is-disabled')) continue;
                        const txt = (btn.textContent || '').trim().replace(/\\s+/g, '');
                        if (targetTexts.includes(txt)) {{
                            btn.click();
                            return (btn.textContent || '').trim();
                        }}
                    }}
                }}
                return null;
            }}""")
            if js_clicked:
                LOG.debug(f"generic-dialog JS click 成功: '{js_clicked}'")
                await wait_for_loading_complete(page)
                return js_clicked
            # JS click 未命中，回退到 Playwright locator（兼容非 Vue 框架）
            # ★ 修复: 避免 CSS scope + xpath= 混用导致 SyntaxError
            # 使用 Playwright CSS scope + :has-text 过滤
            LOG.debug("generic-dialog JS click 未命中，回退到 Playwright locator")
            dialog_scope = ".el-dialog__wrapper:not([style*='display: none']), .ant-modal-wrap:not([style*='display: none'])"
            target_kw = "确定" if confirm else "取消"
            button = page.locator(f'{dialog_scope} button:has-text("{target_kw}")').first
            if await button.count() == 0:
                # 无精确匹配，尝试空格归一化变体 "确 定"
                target_kw_alt = "确 定" if confirm else "取 消"
                button = page.locator(f'{dialog_scope} button:has-text("{target_kw_alt}")').first

        if button and await button.count() > 0:
            button_text = await button.inner_text()
            try:
                await button.click(timeout=5000)
            except Exception as click_err:
                if "intercepts pointer events" in str(click_err):
                    LOG.debug(f"按钮被遮挡，改用 JS click: {button_text[:20]}")
                    await button.evaluate("el => el.click()")
                else:
                    raise

            await wait_for_loading_complete(page)
            if screenshot_holder is not None:
                try:
                    await page.wait_for_timeout(500)
                    try:
                        await page.wait_for_selector('.el-notification, .el-message', state='visible', timeout=1500)
                    except Exception:
                        pass

                    pinned_count = await page.evaluate("() => (window.__pinned_notifications || []).length")
                    LOG.warning(f"[诊断] 截图前钉住的通知数: {pinned_count}")

                    import base64
                    raw = await page.screenshot(type="png")
                    screenshot_holder["success_screenshot"] = base64.b64encode(raw).decode("ascii")
                    LOG.warning(f"[诊断] 截图已保存，大小: {len(raw)} bytes")
                except Exception as e:
                    LOG.warning(f"confirm_dialog 截图失败: {e}")

            return button_text

        # JS 兜底：优先在可见对话框内查找，无对话框时退回全局
        target_texts = const.CONFIRM_TEXTS if confirm else const.CANCEL_TEXTS
        target_json = json.dumps(target_texts, ensure_ascii=False)
        clicked = await page.evaluate(f"""() => {{
            const targetTexts = {target_json};
            const _dialog = document.querySelector(
                '.el-message-box__wrapper:not([style*="display: none"]), ' +
                '.el-dialog__wrapper:not([style*="display: none"]), ' +
                '.ant-modal-wrap:not([style*="display: none"])');
            const buttons = _dialog
                ? Array.from(_dialog.querySelectorAll('button'))
                : Array.from(document.querySelectorAll('button'));
            for (const btn of buttons) {{
                if (btn.offsetWidth === 0 || btn.offsetHeight === 0) continue;
                if (btn.disabled || btn.classList.contains('is-disabled')) continue;
                if (btn.closest('.is-hidden') || btn.closest('[style*="display: none"]')) continue;
                const txt = btn.textContent.trim();
                if (targetTexts.includes(txt.replace(/\\s+/g, ''))) {{
                    btn.click();
                    return txt;
                }}
            }}
            return null;
        }}""")
        if clicked:
            await wait_for_loading_complete(page)
            return clicked

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
        # 先检查页面是否已崩溃
        try:
            await page.evaluate("() => true")
        except Exception as e:
            if "crashed" in str(e).lower():
                LOG.warning("页面已崩溃，跳过关闭弹窗")
                return False

        # 检查是否有可见的关闭按钮（3秒超时，含 disabled 过滤）
        close_btn = page.locator('.el-dialog__close:visible:not([disabled]), .el-message-box__close:visible:not([disabled])').first
        if await close_btn.count() > 0:
            try:
                await close_btn.click(timeout=3000)
                await wait_for_loading_complete(page)
                return True
            except Exception as e:
                if "crashed" in str(e).lower():
                    LOG.warning("点击关闭按钮时页面崩溃")
                    return False
                raise

        # 无可见关闭按钮，按 Escape
        try:
            await page.keyboard.press('Escape')
            await wait_for_loading_complete(page)
            return True
        except Exception as e:
            if "crashed" in str(e).lower():
                LOG.warning("按 Escape 时页面崩溃")
                return False
            raise

    except Exception as e:
        LOG.debug(f"关闭弹窗: {e}")
        return False
