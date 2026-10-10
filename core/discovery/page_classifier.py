"""
page_classifier.py — 页面类型分类器（单一事实来源）

被 Stage 1 验证 (required_elements.py) 和 Playbook 生成 (builder.py) 共同使用。
确保两处使用完全一致的分类逻辑，避免不一致导致的验证失败。
"""

from typing import Dict, List, Optional

# ============================================================================
# 统一关键词列表
# ============================================================================

# CRUD 操作关键词（创建/删除类）
CRUD_KEYWORDS = [
    # 创建类
    "创建", "新增", "添加", "新建", "add", "create", "new",
    "插入", "insert", "录入", "登记",
    # 删除类
    "删除", "移除", "delete", "remove", "清除", "clear"
]

# 详情/查看类关键词（这些按钮不算 CRUD 操作）
DETAIL_KEYWORDS = ["详情", "查看", "detail", "view", "操作详情"]


# ============================================================================
# 统一分类函数
# ============================================================================

def classify_page_type(ui_result: Dict, validated_ops: Optional[Dict] = None) -> str:
    """分类页面类型：crud / read_only / form_page

    判断逻辑（按优先级）：
    1. validated_ops 有成功操作或 CRUD 操作名 → crud
    2. toolbar/row 按钮含 CRUD 关键词 → crud
    3. toolbar/row 按钮含非详情类操作按钮 → crud
    4. 只有 form_actions → form_page
    5. 其他 → read_only

    Args:
        ui_result: Stage 1 探测结果（完整 dict）
        validated_ops: validated_operations 字典（可选，默认从 ui_result 提取）

    Returns:
        "crud" | "read_only" | "form_page"

    Examples:
        >>> ui_result = {"toolbar_buttons": [{"text": "创建"}], ...}
        >>> classify_page_type(ui_result)
        'crud'

        >>> ui_result = {"toolbar_buttons": [{"text": "操作详情"}], ...}
        >>> classify_page_type(ui_result)
        'read_only'
    """
    # 提取字段
    if validated_ops is None:
        validated_ops = ui_result.get("validated_operations", {})

    toolbar = ui_result.get("toolbar_buttons", [])
    row_actions = ui_result.get("row_actions", [])
    form_actions = ui_result.get("form_actions", [])

    all_buttons = toolbar + row_actions

    # ---- 1. 检查 validated_ops ----
    if validated_ops:
        # 有成功验证的操作 → crud
        if any(op.get("success") for op in validated_ops.values()):
            return "crud"
        # 操作名包含 CRUD 关键词 → crud
        for action in validated_ops.keys():
            if any(kw in action.lower() for kw in CRUD_KEYWORDS):
                return "crud"

    # ---- 2. 检查按钮关键词 ----
    has_crud_button = any(
        any(kw in btn.get("text", "").lower() for kw in CRUD_KEYWORDS)
        for btn in all_buttons
    )
    if has_crud_button:
        return "crud"

    # ---- 3. 检查是否有非详情类操作按钮 ----
    # 如果所有按钮都是详情类 → read_only；否则 → crud
    has_non_detail_button = any(
        not any(kw in btn.get("text", "").lower() for kw in DETAIL_KEYWORDS)
        for btn in all_buttons
    )
    if has_non_detail_button:
        return "crud"

    # ---- 4. 表单配置页面 ----
    if form_actions:
        return "form_page"

    # ---- 5. 只读页面（兜底） ----
    return "read_only"
