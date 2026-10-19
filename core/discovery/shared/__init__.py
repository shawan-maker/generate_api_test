"""
core.discovery.shared — Stage 1/2 共享工具集

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 新增类似逻辑都定义在此模块
"""

from .marker_utils import extract_marker_from_row

__all__ = ["extract_marker_from_row"]
