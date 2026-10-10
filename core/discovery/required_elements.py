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

# 导入统一分类函数（单一事实来源）
from core.discovery.page_classifier import classify_page_type


def check_required_elements(ui_result: Dict, flow_name: str) -> Tuple[bool, List[Dict]]:
    """检查 ui_result 是否包含必要的结构性元素。

    泛化版本：不检查具体按钮文本，只检查：
    - toolbar_buttons 或 row_actions 是否有内容
    - dialog_buttons 是否有内容（如果需要弹窗交互）
    - form_fields 是否有内容（如果需要表单填充）

    根据页面类型调整验证标准：
    - crud 页面：要求至少一个操作成功
    - read_only 页面：不要求操作成功，只要求检测到表格数据
    - form_page：要求至少一个表单按钮可点击

    Args:
        ui_result: Stage 1 输出
        flow_name: 流程名称（保留参数以兼容旧接口，实际不使用）

    Returns:
        (all_present, missing_elements)
        - all_present: 是否通过结构验证
        - missing_elements: 缺失的结构性元素（空列表表示全部通过）
    """
    missing = []

    # 0. 检测页面类型（使用统一分类函数）
    page_type = classify_page_type(ui_result)

    # 1. 检查是否有业务操作按钮（工具栏或行操作）
    toolbar = ui_result.get("toolbar_buttons", [])
    row_actions = ui_result.get("row_actions", [])
    form_actions = ui_result.get("form_actions", [])

    # 对于 read_only 和 form_page 页面，允许没有操作按钮
    if page_type not in ("read_only", "form_page") and not toolbar and not row_actions:
        missing.append({
            "type": "structure",
            "desc": "未发现业务操作按钮（toolbar_buttons 和 row_actions 均为空）",
            "critical": True,
        })

    # 2. 检查 summary 中的分类统计
    summary = ui_result.get("summary", {})
    categories = summary.get("categories", {})

    # read_only 和 form_page 页面允许 categories 为空（可能只有表格数据或表单按钮）
    if page_type not in ("read_only", "form_page") and not categories:
        missing.append({
            "type": "structure",
            "desc": "未发现任何按钮分类（categories 为空）",
            "critical": True,
        })

    # 3. 根据页面类型检查 validated_operations
    validated_ops = ui_result.get("validated_operations", {})

    if page_type == "crud":
        # CRUD 页面：要求至少一个操作成功
        if not validated_ops:
            missing.append({
                "type": "structure",
                "desc": "未执行业务闭环验证（validated_operations 为空）",
                "critical": False,
            })
        else:
            success_count = sum(1 for op in validated_ops.values() if op.get("success"))
            if success_count == 0:
                missing.append({
                    "type": "structure",
                    "desc": f"业务闭环验证失败：{len(validated_ops)} 个操作均未成功",
                    "critical": True,
                })

    elif page_type == "read_only":
        # 只读页面：不要求操作成功，只检查是否有表格数据或 tab
        page_structure = ui_result.get("page_structure", {})
        has_table = page_structure.get("mainBodyRows", 0) > 0
        has_tabs = len(page_structure.get("tabs", [])) > 0

        if not has_table and not has_tabs:
            missing.append({
                "type": "structure",
                "desc": "只读页面未检测到表格数据或 tab 结构",
                "critical": True,
            })
        # 即使 validated_operations 全部失败也不标记为 critical

    elif page_type == "form_page":
        # 表单配置页面：要求至少一个表单按钮可点击
        if not form_actions:
            missing.append({
                "type": "structure",
                "desc": "表单配置页面未检测到表单按钮（form_actions 为空）",
                "critical": True,
            })
        else:
            # form_page 允许 validated_operations 为空（因为 form_actions 可能不触发 CRUD 流程）
            # 只检查是否有 form_action 按钮存在
            pass

    # 4. 检查 form_actions（如果有表单按钮，应该被正确分类）
    if form_actions:
        # form_actions 不应该出现在 toolbar_buttons 中
        for fa in form_actions:
            fa_text = fa.get("text", "")
            for tb in toolbar:
                if tb.get("text") == fa_text:
                    missing.append({
                        "type": "structure",
                        "desc": f"表单按钮 '{fa_text}' 被错误分类为工具栏按钮",
                        "critical": False,
                    })

    # 判断是否通过
    critical_missing = [m for m in missing if m.get("critical")]
    all_present = len(critical_missing) == 0

    return all_present, missing
