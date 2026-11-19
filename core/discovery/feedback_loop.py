"""
feedback_loop.py — Stage 1 探测补救工具

将 Stage 1 验证发现的缺失元素修复结果合并回 ui_result：
- patch_ui_result() — 修复结果合并回 ui_result（stage1_rescue 补救链主路径）

注意：所有重试/补救逻辑已移至 discover_ui.py 的 _error_driven_retry。
（D 轮反馈环策略匹配/经验库功能已放弃——match_debug_strategy/
save_fix_to_experience/_load_strategies 连同 stage2_errors.py 已删除。）
"""

import logging

LOG = logging.getLogger("feedback_loop")


def patch_ui_result(ui_result: dict, fix_result: dict) -> dict:
    """将找到的元素合并回 ui_result。

    浅拷贝原始 ui_result，将 fix_result 中的元素按 location
    追加到对应的列表，去重后返回。

    Args:
        ui_result: 原始 Stage 1 输出
        fix_result: execute_debug_strategy 或 Vision API 的返回值

    Returns:
        patched ui_result 副本
    """
    import copy
    patched = copy.deepcopy(ui_result)
    elements = fix_result.get("elements", [])

    if not elements:
        return patched

    added_count = 0
    for elem in elements:
        location = elem.get("location", "toolbar")
        text = elem.get("text", "")
        if not text:
            continue

        # 确定目标列表
        if location == "dialog":
            key = "dialog_buttons"
        elif location == "row_action":
            key = "row_actions"
        else:
            key = "toolbar_buttons"

        # 去重检查
        existing_texts = {b.get("text", "") for b in patched.get(key, [])}
        if text in existing_texts:
            continue

        patched.setdefault(key, []).append(elem)
        added_count += 1

    if added_count > 0:
        summary = patched.get("summary", {})
        total = sum(
            len(patched.get(k, []))
            for k in ["toolbar_buttons", "row_actions", "dialog_buttons",
                       "menu_items", "dropdowns"]
        )
        summary["total_buttons"] = total
        patched["summary"] = summary
        LOG.info(f"  patch_ui_result: 新增 {added_count} 个元素，总计 {total} 个按钮")

    return patched
