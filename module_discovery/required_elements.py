"""
required_elements.py — Stage 1 结构性验证（泛化版本）

核心理念：
- 不写死任何按钮文本或关键词
- 只检查结构性指标（是否有工具栏按钮、行操作按钮、表单字段等）
- 关键操作验证由业务闭环验证（validated_operations）完成

Stage 1 验证分为两层：
1. 结构性验证（本文件）：检查探测结果的完整性
2. 业务闭环验证（discover_ui.py）：验证关键操作能否触发弹窗/表单
"""

from typing import Dict, List, Tuple, Optional


def check_required_elements(ui_result: Dict, flow_name: str) -> Tuple[bool, List[Dict]]:
    """检查 ui_result 是否包含必要的结构性元素。

    泛化版本：不检查具体按钮文本，只检查：
    - toolbar_buttons 或 row_actions 是否有内容
    - dialog_buttons 是否有内容（如果需要弹窗交互）
    - form_fields 是否有内容（如果需要表单填充）

    Args:
        ui_result: Stage 1 输出
        flow_name: 流程名称（保留参数以兼容旧接口，实际不使用）

    Returns:
        (all_present, missing_elements)
        - all_present: 是否通过结构验证
        - missing_elements: 缺失的结构性元素（空列表表示全部通过）
    """
    missing = []

    # 1. 检查是否有业务操作按钮（工具栏或行操作）
    toolbar = ui_result.get("toolbar_buttons", [])
    row_actions = ui_result.get("row_actions", [])

    if not toolbar and not row_actions:
        missing.append({
            "type": "structure",
            "desc": "未发现业务操作按钮（toolbar_buttons 和 row_actions 均为空）",
            "critical": True,
        })

    # 2. 检查 summary 中的分类统计
    summary = ui_result.get("summary", {})
    categories = summary.get("categories", {})

    if not categories:
        missing.append({
            "type": "structure",
            "desc": "未发现任何按钮分类（categories 为空）",
            "critical": True,
        })

    # 3. 检查 validated_operations（业务闭环验证结果）
    validated_ops = ui_result.get("validated_operations", {})

    if not validated_ops:
        missing.append({
            "type": "structure",
            "desc": "未执行业务闭环验证（validated_operations 为空）",
            "critical": False,  # 可降级（可能是不需要验证的模块）
        })
    else:
        # 检查是否有至少一个成功验证的操作
        success_count = sum(1 for op in validated_ops.values() if op.get("success"))
        if success_count == 0:
            missing.append({
                "type": "structure",
                "desc": f"业务闭环验证失败：{len(validated_ops)} 个操作均未成功",
                "critical": True,
            })

    # 判断是否通过
    critical_missing = [m for m in missing if m.get("critical")]
    all_present = len(critical_missing) == 0

    return all_present, missing
