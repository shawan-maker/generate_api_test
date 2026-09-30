"""
result_factory.py — 标准化操作结果构造

消除 operation_executor 中 45+ 处内联错误结果构造，统一为标准化工厂函数。

用法:
    from core.discovery.operation_executor.result_factory import make_error, make_success

    # 简单错误
    return make_error("click_failed", "按钮点击失败")

    # 带额外字段
    return make_error("row_not_found", "未找到目标行", trigger_text="删除")

    # 成功结果
    return make_success(fill_data={"name": "test"}, captured_apis=[...])
"""

import logging

LOG = logging.getLogger("result_factory")


def make_error(error_type: str, error_text: str,
               trigger_text: str = None,
               trigger_locator_verified: str = None,
               **kwargs) -> dict:
    """构造标准化错误结果。

    Args:
        error_type: 错误类型标识（如 "click_failed", "row_not_found"）
        error_text: 人类可读的错误描述
        trigger_text: 触发按钮的文本（可选）
        trigger_locator_verified: 触发定位器验证方式（可选）
        **kwargs: 其他附加字段（如 captured_apis, fill_data 等）

    Returns:
        标准化的错误结果字典
    """
    result = {
        "success": False,
        "error_type": error_type,
        "error_text": error_text,
    }
    if trigger_text is not None:
        result["trigger_text"] = trigger_text
    if trigger_locator_verified is not None:
        result["trigger_locator_verified"] = trigger_locator_verified
    result.update(kwargs)
    return result


def make_success(**kwargs) -> dict:
    """构造标准化成功结果。

    Args:
        **kwargs: 成功结果的字段（如 fill_data, captured_apis, selectors 等）

    Returns:
        标准化的成功结果字典
    """
    return {"success": True, **kwargs}


# ============================================================
# 常用错误类型快捷函数
# ============================================================

def click_failed(text: str = "按钮点击失败", **kwargs) -> dict:
    """按钮点击失败"""
    return make_error("click_failed", text, **kwargs)


def row_not_found(text: str = "未找到目标数据行", **kwargs) -> dict:
    """目标行未找到"""
    return make_error("row_not_found", text, **kwargs)


def no_dialog(text: str = "未弹出预期的对话框", **kwargs) -> dict:
    """预期对话框未弹出"""
    return make_error("no_dialog", text, **kwargs)


def submit_failed(text: str = "提交按钮点击失败或无响应", **kwargs) -> dict:
    """提交失败"""
    return make_error("submit_failed", text, **kwargs)


def no_success_signal(text: str = "操作后未检测到成功信号", **kwargs) -> dict:
    """未检测到成功信号"""
    return make_error("no_success_signal", text, **kwargs)


def api_error(text: str = "API 返回错误", **kwargs) -> dict:
    """API 错误"""
    return make_error("api_error", text, **kwargs)


def form_validation(text: str = "表单校验未通过", **kwargs) -> dict:
    """表单校验失败"""
    return make_error("form_validation", text, **kwargs)


def no_fields(text: str = "无可填写的表单字段", **kwargs) -> dict:
    """无可填写字段"""
    return make_error("no_fields", text, **kwargs)


def no_locator(text: str = "缺少触发按钮定位器", **kwargs) -> dict:
    """缺少定位器"""
    return make_error("no_locator", text, **kwargs)


def skipped(text: str = "操作被跳过", **kwargs) -> dict:
    """操作被跳过"""
    return make_error("skipped", text, **kwargs)


def exception(text: str = "操作执行异常", **kwargs) -> dict:
    """执行异常"""
    return make_error("exception", text, **kwargs)
