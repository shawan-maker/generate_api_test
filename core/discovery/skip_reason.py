"""
skip_reason.py — 模块跳过原因分类

当模块无有效业务操作时，分析原因并生成人类可读的跳过说明。
"""


def classify_skip_reason(manifest: dict, capture_result: dict) -> str:
    """分析模块被跳过的原因。

    Args:
        manifest: Stage 3-4 生成的 manifest
        capture_result: Stage 2 的捕获结果

    Returns:
        人类可读的跳过原因描述
    """
    core_api_map = capture_result.get("core_api_map", {})
    calls = capture_result.get("calls", [])
    total_calls = len(calls)

    # 情况 1: 完全没有捕获到 API
    if total_calls == 0:
        return "Stage 2 未捕获到任何 API 调用（页面可能为纯静态展示或需要特定操作触发）"

    # 情况 2: 只有 init 类 API，无业务 CRUD
    non_init = {k: v for k, v in core_api_map.items() if k != "init"}
    if not non_init:
        init_count = len(core_api_map.get("init", []))
        return f"仅捕获到初始化 API（{init_count} 个候选），无 CRUD 业务操作"

    # 情况 3: 有业务 API 但无法构建 steps
    steps = manifest.get("steps", [])
    if not steps:
        action_types = list(non_init.keys())
        return (
            f"捕获到 {len(non_init)} 类 API（{', '.join(action_types)}），"
            f"但无法构建有效的测试流程（参数依赖或响应格式不支持）"
        )

    # 情况 4: 兜底
    return "未知原因（有 steps 但仍被跳过）"


def classify_stage1_skip_reason(ui_result: dict) -> str:
    """分析 Stage 1 探测失败（无 playbook）的原因。

    Args:
        ui_result: Stage 1 的探测结果

    Returns:
        跳过原因描述
    """
    if not ui_result:
        return "Stage 1 探测无结果（页面可能无交互元素或加载超时）"

    buttons = ui_result.get("buttons", {})
    forms = ui_result.get("forms", {})

    if not buttons and not forms:
        return "Stage 1 未发现任何按钮或表单（页面可能为纯展示型）"

    return "Stage 1 探测到元素但未能生成有效 Playbook（业务闭环验证失败）"
