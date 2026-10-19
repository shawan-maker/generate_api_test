"""
shared/form_validation.py — 表单验证错误检测（Stage 1/2 统一实现）

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效

调用方：
    - Stage 1: base_executor.py (_verify_operation_success 中)
    - Stage 2: replay_engine.py (_step_assert_success 中)
"""

import logging
from playwright.async_api import Page

LOG = logging.getLogger(__name__)


async def detect_form_errors(page: Page) -> list:
    """检测表单验证错误，返回错误消息列表。

    检查 Element UI / Ant Design 的表单验证错误元素：
    - .el-form-item__error (Element UI)
    - .el-form-item.is-error (Element UI)
    - .ant-form-item-explain-error (Ant Design)

    Args:
        page: Playwright Page 对象

    Returns:
        list of str: 错误消息文本列表，空列表表示无错误
    """
    try:
        errors = await page.evaluate("""() => {
            const errors = [];

            // Element UI: .el-form-item__error
            document.querySelectorAll('.el-form-item__error').forEach(el => {
                if (el.offsetWidth > 0 || el.offsetHeight > 0) {
                    const text = el.textContent.trim();
                    if (text) errors.push(text);
                }
            });

            // Element UI: .el-form-item.is-error 中的 label
            document.querySelectorAll('.el-form-item.is-error').forEach(item => {
                const label = item.querySelector('.el-form-item__label');
                const error = item.querySelector('.el-form-item__error');
                if (label && error) {
                    const labelText = label.textContent.trim().replace(/[：:]/g, '');
                    const errorText = error.textContent.trim();
                    if (errorText && !errors.includes(errorText)) {
                        errors.push(`${labelText}: ${errorText}`);
                    }
                }
            });

            // Ant Design: .ant-form-item-explain-error
            document.querySelectorAll('.ant-form-item-explain-error').forEach(el => {
                if (el.offsetWidth > 0 || el.offsetHeight > 0) {
                    const text = el.textContent.trim();
                    if (text) errors.push(text);
                }
            });

            return errors;
        }""")
        return errors or []
    except Exception as e:
        LOG.debug(f"    ⚠️ 表单错误检测失败: {e}")
        return []


async def has_form_errors_visible(page: Page) -> bool:
    """快速检查页面中是否有可见的表单验证错误。

    Args:
        page: Playwright Page 对象

    Returns:
        bool: 是否有可见的表单错误
    """
    try:
        has_errors = await page.evaluate("""() => {
            // Element UI
            const elErrors = document.querySelectorAll('.el-form-item__error');
            for (const err of elErrors) {
                if (err.offsetWidth > 0) return true;
            }
            // Ant Design
            const antErrors = document.querySelectorAll('.ant-form-item-explain-error');
            for (const err of antErrors) {
                if (err.offsetWidth > 0) return true;
            }
            return false;
        }""")
        return bool(has_errors)
    except Exception:
        return False
