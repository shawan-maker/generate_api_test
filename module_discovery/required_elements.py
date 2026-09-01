"""
required_elements.py — 定义各操作流程的必须元素

Stage 1 验证时检查这些元素是否存在于 ui_result 中。
通过 REQUIRED_ELEMENTS 定义每个流程的关键元素，确保 Stage 2 有足够信息执行操作。
"""

from typing import Dict, List, Tuple, Optional
from . import const


# 各操作流程的必须元素定义
REQUIRED_ELEMENTS = {
    "create_flow": [
        {
            "type": "button",
            "action": "create",
            "desc": "创建/新增按钮",
            "location": ["toolbar", "row_action"],
            "critical": True,  # 缺失则阻断
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确定/提交按钮（表单提交）",
            "location": ["dialog", "toolbar"],
            "critical": True,
        },
        {
            "type": "input",
            "required": True,
            "desc": "必填输入项（至少1个）",
            "min_count": 1,
            "critical": False,  # 缺失但可降级（使用默认值）
        },
    ],

    "delete_flow": [
        {
            "type": "button",
            "action": "delete",
            "desc": "删除按钮",
            "location": ["row_action", "toolbar"],
            "critical": True,
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确认删除按钮",
            "location": ["dialog", "toolbar"],
            "critical": True,
        },
    ],

    "update_flow": [
        {
            "type": "button",
            "action": "update",
            "desc": "编辑按钮",
            "location": ["row_action"],
            "critical": True,
        },
        {
            "type": "button",
            "action": "confirm",
            "desc": "确定按钮",
            "location": ["dialog"],
            "critical": True,
        },
    ],

    "query_flow": [
        {
            "type": "button",
            "action": "query",
            "desc": "查询/搜索按钮",
            "location": ["toolbar"],
            "critical": False,  # 可降级（直接调 API）
        },
    ],
}


def get_required_elements(flow_name: str) -> List[Dict]:
    """获取指定流程的必须元素列表。

    Args:
        flow_name: 流程名称（create_flow/delete_flow/update_flow/query_flow）

    Returns:
        必须元素列表
    """
    return REQUIRED_ELEMENTS.get(flow_name, [])


def check_required_elements(ui_result: Dict, flow_name: str) -> Tuple[bool, List[Dict]]:
    """检查 ui_result 是否包含必须元素。

    Args:
        ui_result: Stage 1 输出（包含 toolbar_buttons, row_actions, dialog_buttons 等）
        flow_name: 流程名称

    Returns:
        (all_present, missing_elements)
        - all_present: 是否全部 critical 元素存在
        - missing_elements: 缺失的元素列表（含 critical 标记）
    """
    required = get_required_elements(flow_name)
    missing = []

    for req in required:
        found = False

        if req["type"] == "button":
            # 检查按钮
            locations = req.get("location", [])
            for loc in locations:
                # 支持多种字段名：toolbar_buttons, row_actions, dialog_buttons, dropdowns
                # 注意：row_actions 没有 _buttons 后缀
                field_name = f"{loc}_buttons" if loc != "row_action" else "row_actions"
                buttons = ui_result.get(field_name, []) + ui_result.get("dropdowns", [])
                for btn in buttons:
                    # 优先使用 action 字段，否则从 text 推断
                    action = btn.get("action") or _infer_action(btn.get("text", ""))
                    if action == req["action"]:
                        found = True
                        break
                if found:
                    break

        elif req["type"] == "input":
            # 检查输入字段
            fields = ui_result.get("form_fields", [])
            min_count = req.get("min_count", 1)
            if req.get("required"):
                # 检查必填字段
                matching = [f for f in fields if f.get("required")]
                found = len(matching) >= min_count
            else:
                # 检查任意字段
                found = len(fields) >= min_count

        if not found:
            missing.append(req)

    # 判断是否全部 critical 元素存在
    critical_missing = [m for m in missing if m.get("critical")]
    all_present = len(critical_missing) == 0

    return all_present, missing


def _infer_action(button_text: str) -> str:
    """从按钮文本推断 action。

    使用 const.ACTION_KEYWORDS 进行匹配。

    Args:
        button_text: 按钮文本

    Returns:
        推断的 action 名称（如 "create", "delete"），未匹配返回 "unknown"
    """
    for action, keywords in const.ACTION_KEYWORDS.items():
        for kw in keywords:
            if kw in button_text:
                return action
    return "unknown"
