"""
stage2_errors.py — Stage 2 错误分类

定义 Stage 2 执行过程中可能遇到的三种错误类型：
- L1: Stage1MissingError — Stage 1 未探测到必须元素
- L2: Stage2LocatorError — Stage 2 定位失败（选择器/iframe/覆盖层问题）
- L3: Stage2TimingError — Stage 2 时序失败（等待不充分/动画未完成）

这些错误分类用于触发不同的修复策略：
- L1 → 反馈给 Stage 1，携带 hints 重新探测
- L2 → 触发 diagnostic_mode 修复选择器
- L3 → 增加等待时间或调整等待策略
"""

from typing import List, Optional


class Stage2Error(Exception):
    """Stage 2 操作失败基类"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class Stage1MissingError(Stage2Error):
    """L1: Stage 1 未探测到必须元素

    当 Stage 2 尝试操作某个元素，但在 ui_result 中找不到该元素时抛出。
    这表示 Stage 1 的探测不完整，需要反馈给 Stage 1 重新探测。

    Attributes:
        element_type: 元素类型（"button" / "input"）
        action: 元素动作（如 "confirm", "create", "delete"）
        context: 操作上下文（如 "提交创建表单"）
        expected_location: 期望的元素位置（如 ["toolbar", "dialog"]）
    """

    def __init__(
        self,
        element_type: str,
        action: str,
        context: str,
        expected_location: Optional[List[str]] = None
    ):
        self.element_type = element_type
        self.action = action
        self.context = context
        self.expected_location = expected_location or []

        message = f"Stage 1 未探测到: {element_type}[{action}] in {context}"
        if expected_location:
            message += f" (期望位置: {expected_location})"

        super().__init__(message)


class Stage2LocatorError(Stage2Error):
    """L2: Stage 2 定位失败

    当 ui_result 中有元素记录，但 Stage 2 在页面上找不到对应元素时抛出。
    这通常是选择器问题、iframe 问题或覆盖层问题。

    Attributes:
        element_desc: 元素描述（如 "按钮[confirm]"）
        selector: 使用的选择器
        tried_iframes: 是否已尝试 iframe 穿透
    """

    def __init__(
        self,
        element_desc: str,
        selector: str,
        tried_iframes: bool = False
    ):
        self.element_desc = element_desc
        self.selector = selector
        self.tried_iframes = tried_iframes

        message = f"定位失败: {element_desc} (selector={selector})"
        if tried_iframes:
            message += " [已尝试 iframe 穿透]"

        super().__init__(message)


class Stage2TimingError(Stage2Error):
    """L3: Stage 2 时序失败

    当元素存在且可以定位，但点击时机不对时抛出。
    这通常是因为等待不充分、动画未完成或元素还未就绪。

    Attributes:
        element_desc: 元素描述
        wait_strategy: 使用的等待策略
    """

    def __init__(self, element_desc: str, wait_strategy: str):
        self.element_desc = element_desc
        self.wait_strategy = wait_strategy

        message = f"时序失败: {element_desc} (wait={wait_strategy})"
        super().__init__(message)
