"""
feedback_loop.py — Stage 1 探测补救工具

当 Stage 1 验证发现缺失元素时，提供以下能力：
1. match_debug_strategy() — 匹配诊断策略
2. patch_ui_result() — 将修复结果合并回 ui_result
3. save_fix_to_experience() — 将成功修复写入经验库

注意：所有重试/补救逻辑已移至 discover_ui.py 的 _error_driven_retry。
本模块仅提供策略匹配和结果合并能力。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .stage2_errors import Stage1MissingError, Stage2LocatorError, Stage2TimingError, Stage2Error
from . import const

LOG = logging.getLogger("feedback_loop")

# 默认策略文件路径（项目根目录下）
_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def match_debug_strategy(error: Stage2Error) -> Optional[dict]:
    """匹配诊断策略。

    从 config/debug_strategies.json 加载策略，根据错误类型匹配。

    Args:
        error: Stage2Error 子类实例

    Returns:
        匹配的策略 dict（含 steps），或 None（无匹配）
    """
    strategies = _load_strategies()
    if not strategies:
        return None

    if isinstance(error, Stage1MissingError):
        for name, strategy in strategies.items():
            trigger = strategy.get("trigger", {})
            condition = trigger.get("condition", "")
            if "Stage1MissingError" in condition:
                LOG.info(f"  匹配策略: {name} — {strategy.get('description', '')}")
                return {**strategy, "_name": name}

    elif isinstance(error, Stage2LocatorError):
        for name, strategy in strategies.items():
            trigger = strategy.get("trigger", {})
            condition = trigger.get("condition", "")
            if "button_click_failed" in name or "click" in condition.lower():
                LOG.info(f"  匹配策略: {name} — {strategy.get('description', '')}")
                return {**strategy, "_name": name}

    return None


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


def save_fix_to_experience(strategy: dict, fix_result: dict, project_dir: Path) -> None:
    """将成功修复写入经验库（debug_strategies.json）。"""
    try:
        strategy_name = strategy.get("_name", "")
        if not strategy_name:
            return

        config_path = project_dir / "config" / "debug_strategies.json"
        if not config_path.exists():
            return

        data = json.loads(config_path.read_text(encoding="utf-8"))
        strategies = data.get("strategies", {})

        if strategy_name in strategies:
            meta = strategies[strategy_name].setdefault("metadata", {})
            meta["success_count"] = meta.get("success_count", 0) + 1
            from datetime import datetime
            meta["last_used"] = datetime.now().isoformat()

            config_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            LOG.info(f"  经验库更新: {strategy_name} success_count={meta['success_count']}")

    except Exception as e:
        LOG.warning(f"  经验库写入失败（不阻断主流程）: {e}")


# ---- 内部辅助函数 ----

def _load_strategies() -> dict:
    """加载 debug_strategies.json。"""
    config_path = _CONFIG_DIR / "debug_strategies.json"
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return data.get("strategies", {})
    except Exception as e:
        LOG.warning(f"  加载策略文件失败: {e}")
        return {}
