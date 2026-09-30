"""
discover_ui.py — Stage 1: 前端按钮/元素探测 + 业务闭环验证（Facade 层）

本文件为 facade 层，所有实现已拆分至子模块：
  - ui_scanner/element_scanner.py: DOM 元素发现
  - ui_scanner/button_detector.py: 按钮检测与分类
  - ui_scanner/form_scanner.py: 表单字段扫描
  - operation_executor/base_executor.py: 通用执行工具
  - operation_executor/crud_executor.py: CRUD 操作执行
  - operation_executor/navigation_executor.py: 跨页面导航
  - playbook_builder/builder.py: Playbook 生成
"""

# Re-export 所有公共 API（保持向后兼容）

# ui_scanner
from core.discovery.ui_scanner.element_scanner import (
    discover_all,
    _scan_hints,
    _scan_iframes,
    _scan_dialog_buttons,
    _scan_candidates,
    _discover_search_inputs,
    _discover_dropdowns,
    _scan_create_dialog,
    _scan_fields_in_all_frames,
    _scan_page_buttons_for_create,
    _snapshot_page_structure,
    _detect_ui_framework,
    _kb_enrichment_scan,
)
from core.discovery.ui_scanner.button_detector import (
    _close_dialog,
    _classify_button_location,
    _count_categories,
    _build_button_labels,
    _check_precondition_state,
    _retry_precondition,
    _prewarm_table_data,
    _infer_action_role,
    _find_create_delete_actions,
)
from core.discovery.ui_scanner.form_scanner import (
    _deep_scan_form_item,
    _resolve_label_selector,
)

# operation_executor
from core.discovery.operation_executor.base_executor import (
    cleanup_ui_overlays,
    wait_for_spa_ready,
    _click_button_escalating,
    _extract_fallback_marker,
    _ensure_row_selected,
    _try_fill_empty_selects,
    _install_message_capture,
    _get_captured_messages,
    _verify_operation_success,
    _check_data_changed,
    _ensure_on_list_page,
    _check_create_api_triggered_simple,
)
from core.discovery.operation_executor.crud_executor import (
    discover_and_validate,
    _validate_business_flow,
    _error_driven_retry,
    _do_create,
    _do_query,
    _do_query_via_search_input,
    _do_detail,
    _do_edit,
    _do_delete,
    _do_generic_operation,
    _do_save_or_confirm,
    _apply_fix,
    _guess_format,
    _extract_field_names,
    _vision_analysis,
    _get_page_state_key,
)
from core.discovery.operation_executor.navigation_executor import (
    _explore_and_operate_in_new_page,
    _handle_cross_page_operation,
    _navigate_back_to_list,
)

# playbook_builder
from core.discovery.playbook_builder.builder import (
    build_playbook,
    _classify_op_steps_type,
    _build_create_steps,
    _build_delete_steps,
    _build_update_steps,
    _build_query_steps,
    _build_generic_steps,
    _build_page_nav_steps,
)

# 旧的辅助函数（保留向后兼容）
from core.discovery.ui_scanner.element_scanner import _get_kb

LOG = __import__("logging").getLogger("discover_ui")
