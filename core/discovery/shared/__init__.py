"""
core.discovery.shared — Stage 1/2 共享工具集

设计原则：
1. 方法只定义一次，Stage 1/2 同时调用
2. 修改处理方法时两边同时生效
3. 新增类似逻辑都定义在此模块

模块结构：
- marker_utils.py:       marker 提取
- constants.py:          共享运行时常量
- message_capture.py:    MutationObserver 消息捕获
- dialog_helpers.py:     弹框检测/关闭/确认
- form_validation.py:    表单验证错误检测
- fill_helpers.py:       数据填充工具（值生成 + fill_rule 元数据 + 跳过规则评估）
- success_verifier.py:   操作成功验证（六层策略链）
- switch_ops.py:         Switch 切换操作
- row_interaction.py:    行交互（定位/勾选/按钮点击）
"""

from .marker_utils import extract_marker_from_row

from .message_capture import (
    install_message_capture,
    get_captured_messages,
    unpin_notifications,
)

from .dialog_helpers import (
    detect_overlay,
    has_visible_confirm_dialog,
    dismiss_overlay,
    handle_confirm,
    confirm_if_present,
    wait_for_confirm_box_dismissed,
)

from .form_validation import (
    detect_form_errors,
    has_form_errors_visible,
)

from .fill_helpers import (
    generate_fill_value,
    apply_fill_rule,
    evaluate_skip_rules,
    TREE_SELECT_SKIP_RULES,
    SELECT_SKIP_RULES,
)

from .success_verifier import verify_operation_success

from .switch_ops import (
    toggle_switch,
    get_switch_state,
)

from .row_interaction import (
    locate_row_with_retry,
    select_row_checkbox,
    click_row_button,
    get_row_selector_by_marker,
)

from .tab_helpers import restore_active_tab

__all__ = [
    # marker
    "extract_marker_from_row",
    # message capture
    "install_message_capture",
    "get_captured_messages",
    "unpin_notifications",
    # dialog
    "detect_overlay",
    "has_visible_confirm_dialog",
    "dismiss_overlay",
    "handle_confirm",
    "confirm_if_present",
    "wait_for_confirm_box_dismissed",
    # form validation
    "detect_form_errors",
    "has_form_errors_visible",
    # fill helpers
    "generate_fill_value",
    "apply_fill_rule",
    "evaluate_skip_rules",
    "TREE_SELECT_SKIP_RULES",
    "SELECT_SKIP_RULES",
    # success verification
    "verify_operation_success",
    # switch
    "toggle_switch",
    "get_switch_state",
    # row interaction
    "locate_row_with_retry",
    "select_row_checkbox",
    "click_row_button",
    "get_row_selector_by_marker",
    # tab helpers
    "restore_active_tab",
]
