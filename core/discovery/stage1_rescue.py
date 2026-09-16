"""
stage1_rescue.py — Stage 1 Phase C/D/E 补救子例程

从 run.py 提取的 hints_rescan、precondition_retry、vision_rescue 函数。
"""

import logging

LOG = logging.getLogger("stage1_rescue")


async def hints_rescan(page, ui_result: dict, missing: list) -> dict:
    """Phase C: hints 定向重探。

    用 missing_elements 作为 hints 调用 _scan_hints，
    将找到的元素合并回 ui_result。
    """
    from .discover_ui import _scan_hints
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase C: hints 定向重探 ({len(missing)} 个缺失元素)")

    found = await _scan_hints(page, missing)
    if found:
        LOG.info(f"  Phase C: 找到 {len(found)} 个元素")
        ui_result = patch_ui_result(ui_result, {"elements": found})
    else:
        LOG.info("  Phase C: 未找到额外元素")

    return ui_result


async def precondition_retry(page, ui_result: dict, missing: list) -> dict:
    """Phase D: 检查前置操作，换方式重试点击，重新扫描弹窗内元素。"""
    from .discover_ui import (
        _check_precondition_state, _retry_precondition,
        _scan_dialog_buttons,
    )
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase D: 前置操作重试 ({len(missing)} 个缺失元素)")

    added_any = False
    for elem in missing:
        if elem.get("type") != "button":
            continue
        action = elem.get("action", "")
        # 只对需要前置操作（弹窗内按钮）的元素重试
        expected_location = elem.get("location", [])
        if "dialog" not in expected_location:
            continue

        # 找到对应的触发按钮（toolbar 中同 action 的按钮）
        trigger_btn = None
        for btn in ui_result.get("toolbar_buttons", []):
            if btn.get("action") == action:
                trigger_btn = btn
                break
        if not trigger_btn:
            for btn in ui_result.get("row_actions", []):
                if btn.get("action") == action:
                    trigger_btn = btn
                    break
        if not trigger_btn:
            continue

        # 检查前置操作状态
        expected_title = ""
        for ff in ui_result.get("form_fields", []):
            if ff.get("name"):
                expected_title = ff.get("dialog_title", "")
                break

        state = await _check_precondition_state(page, {
            "type": "dialog", "title": expected_title
        })

        if not state["success"]:
            # 前置操作未成功，尝试重试点击
            retry_ok = await _retry_precondition(page, trigger_btn)
            if retry_ok:
                # 重试成功，扫描弹窗内按钮
                dialog_btns = await _scan_dialog_buttons(page)
                if dialog_btns:
                    ui_result = patch_ui_result(ui_result, {"elements": dialog_btns})
                    added_any = True
                    LOG.info(f"  Phase D: 重试成功，新增 {len(dialog_btns)} 个弹窗按钮")
        else:
            # 前置操作已成功（弹窗已打开），直接扫描弹窗
            dialog_btns = await _scan_dialog_buttons(page)
            if dialog_btns:
                ui_result = patch_ui_result(ui_result, {"elements": dialog_btns})
                added_any = True
                LOG.info(f"  Phase D: 弹窗已打开，新增 {len(dialog_btns)} 个弹窗按钮")

    if not added_any:
        LOG.info("  Phase D: 未找到额外元素")

    return ui_result


async def vision_rescue(page, ui_result: dict, missing: list) -> dict:
    """Phase E: Vision 截图分析兜底。"""
    from .ai_debug_assistant import ai_assisted_analysis
    from .feedback_loop import patch_ui_result

    LOG.info(f"  Phase E: Vision 截图分析 ({len(missing)} 个缺失元素)")

    try:
        result = await ai_assisted_analysis(page, missing, expected_context=None)

        if result.get("found"):
            ui_result = patch_ui_result(ui_result, result)
            LOG.info(f"  Phase E: Vision 找到 {len(result.get('elements', []))} 个元素")
        else:
            diagnosis = result.get("diagnosis", "unknown")
            source = result.get("source", "unknown")
            LOG.info(f"  Phase E: Vision 未找到元素 (source={source}, diagnosis={diagnosis})")

    except Exception as e:
        LOG.warning(f"  Phase E: Vision 分析异常: {e}")

    return ui_result
