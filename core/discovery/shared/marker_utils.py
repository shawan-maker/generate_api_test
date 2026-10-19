"""
Stage 1 和 Stage 2 共享的 marker 工具集

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 新增类似逻辑都定义在此文件

提取规则：
- 遍历表格第一行的每个 td cell
- 跳过操作列、checkbox、按钮等交互元素
- 如果包含冒号分隔符（中英文），取冒号后面的值
  例如 "组织： AT_test_799040" → "AT_test_799040"
- 长度限制 2-30 字符
- 跳过纯数字
"""

import logging
from typing import Optional
from playwright.async_api import Page

LOG = logging.getLogger(__name__)


async def extract_marker_from_row(page: Page) -> Optional[str]:
    """
    从表格第一行提取 marker（Stage 1/2 统一实现）

    提取规则：
    1. 遍历第一行的每个 td cell
    2. 跳过操作列、checkbox、按钮
    3. 如果包含冒号分隔符，取冒号后面的值
       例如 "组织： AT_test_799040" → "AT_test_799040"
    4. 长度限制 2-30 字符

    Args:
        page: Playwright Page 对象

    Returns:
        marker 值，或 None（无数据行/提取失败）

    调用方：
        - Stage 1: crud_executor.py (create 后提取 marker)
        - Stage 2: replay_engine.py (create 后提取 marker, first_row 模式)

    示例：
        ```python
        # create 成功后
        await wait_for_table_ready(page, timeout=10000)
        marker = await extract_marker_from_row(page)
        if marker:
            LOG.info(f"✓ marker 已从列表页提取: {marker}")
        ```
    """
    # 操作列相关文本，不应作为 marker
    _skip_texts = {
        "操作", "编辑", "删除", "修改", "查看", "详情", "更多",
        "冻结", "解冻", "启用", "禁用", "锁定", "解锁", "重置密码",
        "批量删除", "导入", "导出", "授权", "迁移"
    }

    try:
        marker = await page.evaluate("""(skipTexts) => {
            const table = document.querySelector('.el-table__body, table tbody');
            if (!table) return null;
            const rows = table.querySelectorAll('tr');
            if (rows.length === 0) return null;
            const cells = rows[0].querySelectorAll('td .cell, td');
            for (const cell of cells) {
                const text = (cell.textContent || '').trim();
                if (!text || text.length > 30 || text.length < 2) continue;
                if (/^\\d+$/.test(text)) continue;
                if (cell.querySelector('.el-checkbox, .el-radio')) continue;
                if (cell.querySelector('button, .el-button, .el-dropdown')) continue;
                if (skipTexts.includes(text)) continue;

                // 优化：如果包含冒号分隔符，只取冒号后面的值部分
                // 例如 "组织： AT_test_799040" → "AT_test_799040"
                if (text.includes('：') || text.includes(':')) {
                    const parts = text.split(/[：:]/);
                    if (parts.length >= 2) {
                        const valuePart = parts[1].trim();
                        if (valuePart.length >= 2 && valuePart.length <= 30) {
                            return valuePart;
                        }
                    }
                }

                return text;
            }
            return null;
        }""", list(_skip_texts))

        if marker:
            LOG.debug(f"    ✓ marker 从列表页提取: '{marker}'")
        return marker

    except Exception as e:
        LOG.debug(f"    ⚠️ marker 提取失败: {e}")
        return None
