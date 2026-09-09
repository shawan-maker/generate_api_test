"""
stage_validators.py — 阶段质量门控

职责：
- 验证每个 Stage 的输出质量
- 防止 garbage-in-garbage-out
- 提供诊断信息帮助定位问题
"""

import logging
from typing import Tuple, List

LOG = logging.getLogger("stage_validators")


def validate_stage1(
    ui_result: dict,
    required_flows: list = None
) -> Tuple[bool, List[str], list]:
    """验证 Stage 1 UI 探测结果质量。

    Args:
        ui_result: Stage 1 输出 (buttons.json)
        required_flows: 需要验证的流程列表（如 ["create_flow", "delete_flow"]）
                       默认验证所有 CRUD 流程。设为 [] 则跳过必须元素检查。

    Returns:
        (is_valid, issues, missing_elements)
        - is_valid: 是否通过验证（仅检查 critical 元素）
        - issues: 问题描述列表
        - missing_elements: 缺失的必须元素列表（含 critical 标记）
    """
    issues = []

    # 1. 检查基本结构
    if not isinstance(ui_result, dict):
        return False, ["ui_result 不是字典"], []

    # 2. 检查按钮分类
    toolbar = ui_result.get("toolbar_buttons", [])
    row_actions = ui_result.get("row_actions", [])
    dropdown_items = ui_result.get("dropdown_items", [])

    if not toolbar and not row_actions:
        issues.append("未发现工具栏按钮或行操作按钮，探测可能不完整")

    if not toolbar:
        issues.append("未发现工具栏按钮（toolbar_buttons 为空）")

    if not row_actions:
        issues.append("未发现行操作按钮（row_actions 为空）")

    # 3. 检查总数
    summary = ui_result.get("summary", {})
    total = summary.get("total_buttons") or summary.get("total", 0)

    if total < 3:
        issues.append(f"按钮总数过少 ({total})，可能探测不完整")

    if total > 100:
        issues.append(f"按钮总数过多 ({total})，可能存在重复或误识别")

    # 4. 检查关键功能
    if not summary.get("has_create"):
        issues.append("未发现创建功能（has_create=false），生命周期测试将不完整")

    if not summary.get("has_delete"):
        issues.append("未发现删除功能（has_delete=false），生命周期测试将不完整")

    # 5. 检查分类统计
    categories = summary.get("categories", {})
    if not categories:
        issues.append("分类统计为空（categories），按钮分类可能失败")

    # 6. 检查必须元素（新增）
    all_missing = []
    if required_flows is None:
        # 默认验证所有 CRUD 流程
        from .required_elements import REQUIRED_ELEMENTS
        required_flows = list(REQUIRED_ELEMENTS.keys())

    if required_flows:
        from .required_elements import check_required_elements
        for flow in required_flows:
            present, missing = check_required_elements(ui_result, flow)
            if not present:
                all_missing.extend(missing)
                for m in missing:
                    if m.get("critical"):
                        issues.append(
                            f"[{flow}] 缺少必须元素: {m['desc']} (critical)"
                        )
                    else:
                        issues.append(
                            f"[{flow}] 缺少元素: {m['desc']} (可降级)"
                        )

    # 7. 检查业务闭环验证结果（validated_operations）
    validated_ops = ui_result.get("validated_operations", {})
    if not validated_ops:
        issues.append("未执行业务闭环验证（validated_operations 为空）")
    else:
        # 检查关键操作是否已验证
        critical_ops = ["create", "delete"]
        for op in critical_ops:
            if op not in validated_ops:
                issues.append(f"关键操作未验证: {op}")
            else:
                op_result = validated_ops[op]
                if not op_result.get("success"):
                    error = op_result.get("error", "未知错误")
                    issues.append(f"关键操作验证失败: {op} - {error}")
                else:
                    # 检查是否有选择器
                    if not op_result.get("selectors"):
                        issues.append(f"操作 {op} 缺少 selectors")
                    # 只有需要表单填充的操作才检查 fill_data
                    if op in ["create", "update"] and not op_result.get("fill_data"):
                        issues.append(f"操作 {op} 缺少 fill_data")

        # 统计已验证的操作数
        validated_count = sum(1 for op in validated_ops.values() if op.get("success"))
        LOG.info(f"业务闭环验证: {validated_count}/{len(validated_ops)} 个操作成功")

    # 判断是否通过（只检查 critical 元素）
    critical_missing = [m for m in all_missing if m.get("critical")]
    is_valid = len(critical_missing) == 0 and len(issues) == 0

    if not is_valid:
        LOG.warning(f"Stage 1 验证失败: {len(issues)} 个问题")
        for issue in issues:
            LOG.warning(f"  - {issue}")
    else:
        LOG.info(f"Stage 1 验证通过: 发现 {total} 个按钮")

    return is_valid, issues, all_missing


def validate_stage2(capture_result: dict) -> Tuple[bool, List[str]]:
    """验证 Stage 2 API 捕获结果质量。

    Args:
        capture_result: Stage 2 输出 (api_capture.json)

    Returns:
        (is_valid, issues) — 是否通过验证 + 问题列表
    """
    issues = []

    if not isinstance(capture_result, dict):
        return False, ["capture_result 不是字典"]

    # 1. 检查分类结果（兼容 "by_category" 和 "classified" 两个字段名）
    classified = capture_result.get("by_category") or capture_result.get("classified", {})

    if not classified:
        issues.append("分类结果为空（by_category/classified 为空），分类可能失败")

    # 2. 检查核心 API 是否存在
    core_categories = ["create", "query", "update", "delete"]
    missing_core = []

    for cat in core_categories:
        if cat not in classified or not classified[cat]:
            missing_core.append(cat)

    if missing_core:
        issues.append(f"缺少核心 CRUD API: {', '.join(missing_core)}")

    # 3. 检查端点数量
    stats = capture_result.get("stats", {})
    total_calls = stats.get("total_calls", 0)
    unique_endpoints = stats.get("unique_endpoints", 0)

    if total_calls < 5:
        issues.append(f"捕获的 API 调用过少 ({total_calls})，可能拦截不完整")

    if unique_endpoints < 3:
        issues.append(f"唯一端点过少 ({unique_endpoints})，可能去重过度")

    # 4. 检查响应样本
    samples = capture_result.get("response_samples", {})

    if not samples:
        issues.append("响应样本为空（response_samples 为空），响应收集可能失败")

    # 5. 检查辅助 API 比例
    support_count = 0
    core_count = 0

    for cat, apis in classified.items():
        if cat in ("support", "other_get", "other_post"):
            support_count += len(apis)
        else:
            core_count += len(apis)

    if core_count > 0:
        support_ratio = support_count / core_count
        if support_ratio > 5:
            issues.append(f"辅助 API 比例过高 ({support_ratio:.1f}:1)，分类可能不准确")

    # 判断是否通过
    is_valid = len(issues) == 0

    if not is_valid:
        LOG.warning(f"Stage 2 验证失败: {len(issues)} 个问题")
        for issue in issues:
            LOG.warning(f"  - {issue}")
    else:
        LOG.info(f"Stage 2 验证通过: {unique_endpoints} 个端点, {total_calls} 次调用")

    return is_valid, issues


def validate_stage3(analysis: dict) -> Tuple[bool, List[str]]:
    """验证 Stage 3 分析结果质量。

    Args:
        analysis: Stage 3 输出 (analysis.json)

    Returns:
        (is_valid, issues) — 是否通过验证 + 问题列表
    """
    issues = []

    if not isinstance(analysis, dict):
        return False, ["analysis 不是字典"]

    # 1. 检查执行顺序（兼容 crud_order 和 order 两个字段名）
    order = analysis.get("crud_order") or analysis.get("order", [])

    if not order:
        issues.append("执行顺序为空（crud_order/order 为空），顺序推导可能失败")
    elif len(order) < 2:
        issues.append(f"执行顺序过短 ({len(order)} 步)，可能遗漏关键步骤")

    # 2. 检查核心步骤是否存在（兼容 str 和 dict 两种格式）
    if order and isinstance(order[0], str):
        step_names = order
    elif order:
        step_names = [s.get("name", "") for s in order]
    else:
        step_names = []

    if not any("create" in name.lower() for name in step_names):
        issues.append("执行顺序上缺少创建步骤")

    if not any("delete" in name.lower() for name in step_names):
        issues.append("执行顺序上缺少删除步骤")

    # 3. 检查依赖关系
    deps = analysis.get("dependencies", {})

    if not deps:
        issues.append("依赖关系为空（dependencies 为空），数据流分析可能失败")

    # 4. 检查状态断言
    assertions = analysis.get("state_assertions", {})

    if not assertions:
        issues.append("状态断言为空（state_assertions 为空），状态推导可能失败")

    # 5. 检查 API 映射（兼容 api_by_button 和 api_mapping 两个字段名）
    api_mapping = analysis.get("api_by_button") or analysis.get("api_mapping", {})

    if not api_mapping:
        issues.append("API 映射为空（api_by_button/api_mapping 为空），按钮-API 关联可能失败")

    # 判断是否通过
    is_valid = len(issues) == 0

    if not is_valid:
        LOG.warning(f"Stage 3 验证失败: {len(issues)} 个问题")
        for issue in issues:
            LOG.warning(f"  - {issue}")
    else:
        LOG.info(f"Stage 3 验证通过: {len(order)} 个步骤, {len(deps)} 个依赖")

    return is_valid, issues


def validate_stage4(script_content: str, script_path: str) -> Tuple[bool, List[str]]:
    """验证 Stage 4 生成的脚本质量。

    Args:
        script_content: 生成的脚本内容
        script_path: 脚本文件路径

    Returns:
        (is_valid, issues) — 是否通过验证 + 问题列表
    """
    issues = []

    if not script_content:
        return False, ["脚本内容为空"]

    # 1. 检查 manifest 架构关键元素
    if "MANIFEST = " not in script_content:
        issues.append("脚本中未找到 MANIFEST 定义")

    if "TestRunner" not in script_content:
        issues.append("脚本中未找到 TestRunner 引用")

    if "test_runtime" not in script_content:
        issues.append("脚本中未找到 test_runtime 导入")

    # 2. 检查响应契约
    if "response_contract" not in script_content:
        issues.append("MANIFEST 中缺少 response_contract")

    # 3. 检查步骤定义
    if "steps" not in script_content:
        issues.append("MANIFEST 中缺少 steps 定义")

    # 4. 检查字段角色
    if "body_field_roles" not in script_content:
        issues.append("步骤中缺少 body_field_roles 定义")

    # 5. 检查文件长度（manifest 架构脚本应该很简洁）
    line_count = len(script_content.split("\n"))

    if line_count < 50:
        issues.append(f"脚本过短 ({line_count} 行)，可能缺少必要的 manifest 数据")

    if line_count > 800:
        issues.append(f"脚本过长 ({line_count} 行)，manifest 架构脚本不应超过 800 行")

    # 6/7. 根据脚本架构分流检查
    # manifest 纯模式：有 MANIFEST + TestRunner，但无 def test_（由 TestRunner 驱动步骤）
    is_manifest_mode = (
        "MANIFEST" in script_content
        and "TestRunner" in script_content
        and "def test_" not in script_content
    )

    if not is_manifest_mode:
        # 旧架构或混合格式：检查 test_ 函数和 assert 数量
        if "def test_" not in script_content:
            issues.append("脚本中未找到 test_ 函数")

        assert_count = script_content.count("assert ")
        if assert_count < 3 and line_count >= 10:
            issues.append(f"断言过少 ({assert_count} 个)，测试覆盖可能不足")
    else:
        # manifest 纯模式的专用检查
        steps_count = script_content.count('"action":')
        if steps_count < 3:
            issues.append(f"manifest 步骤过少 ({steps_count} 个)")

        if '"action": "create"' not in script_content:
            issues.append("manifest 缺少 create 步骤")
        if '"action": "delete"' not in script_content:
            issues.append("manifest 缺少 delete 步骤")

    # 判断是否通过
    is_valid = len(issues) == 0

    if not is_valid:
        LOG.warning(f"Stage 4 验证失败: {len(issues)} 个问题")
        for issue in issues:
            LOG.warning(f"  - {issue}")
    else:
        LOG.info(f"Stage 4 验证通过: {line_count} 行 manifest 架构脚本")

    return is_valid, issues
