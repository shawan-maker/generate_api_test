"""
shared/tab_helpers.py — Tab 状态管理 Stage 1/2 统一实现

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. Tab 恢复逻辑统一

统一逻辑：
- restore_active_tab: 检查当前激活的 Tab，不匹配则切换

调用方：
    - Stage 1: base_executor.py (_ensure_on_list_page)
    - Stage 2: replay_engine.py (_step_navigate_back)
"""

import logging

LOG = logging.getLogger(__name__)


async def restore_active_tab(page, tab_name: str, tab_locator: str = ""):
    """检查当前激活的 Tab，如果不是目标 Tab，点击切换。

    多 Tab 页面（如黑白名单）在 create/edit 操作后可能跳回默认 Tab，
    需要重新切换到目标 Tab 才能继续操作。

    Stage 1/2 统一实现：
    - Stage 1: base_executor._ensure_on_list_page 调用
    - Stage 2: replay_engine._step_navigate_back 调用

    Args:
        page: Playwright Page 对象
        tab_name: 目标 Tab 名称
        tab_locator: 目标 Tab 的 Playwright locator（可选）
    """
    if not tab_name:
        return

    try:
        current_tab = await page.evaluate("""() => {
            const activeItem = document.querySelector('.el-tabs__item.is-active');
            return activeItem ? activeItem.textContent.trim() : '';
        }""")

        if current_tab and current_tab != tab_name:
            LOG.info(f"    [TAB-RESTORE] 当前 Tab '{current_tab}' ≠ 目标 '{tab_name}'，重新切换")

            # 优先使用精确的 locator
            if tab_locator:
                await page.click(tab_locator, timeout=5000)
            else:
                # 回退到文本匹配
                await page.click(
                    f'[role="tab"]:has-text("{tab_name}"), '
                    f'.el-tabs__item:has-text("{tab_name}"), '
                    f'.ant-tabs-tab:has-text("{tab_name}")',
                    timeout=5000,
                )
            await page.wait_for_timeout(1500)
        elif not current_tab:
            # 页面可能没有 tabs 或 tabs 未渲染完成，跳过
            pass
    except Exception as e:
        LOG.debug(f"    restore_active_tab: {e}")
