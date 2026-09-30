"""
analyze_flow.py — Stage 3: 逻辑顺序与关联分析（Facade 层）

核心推理流水线：
  Step 0: 基础设施 API 识别  → 识别 init 阶段的辅助 API
  Step 1: 构建核心 API 列表  → 从 core_api_map 提取业务 API
  Step 2: 按钮-API 关联     → 建立 "按钮文本 → 核心 API" 映射
  Step 3: CRUD 顺序编排     → 推导正确的执行顺序
  Step 4: 数据依赖链分析    → 从响应中提取 id，注入后续请求
  Step 5: 状态断言自动推导  → 跨响应对比找出状态变化规律

本文件为 facade 层，所有实现已拆分至 stage3/ 子模块：
  - api_classifier.py: API 分类与基础设施识别
  - value_chain.py: 三原则值追踪引擎
  - field_classifier.py: 5 角色字段分类系统
  - pre_api_tracer.py: 前置 API 依赖追踪
  - manifest_builder.py: Manifest 构建
"""

import json
import logging

# Re-export 所有公共 API（保持向后兼容）
from core.discovery.stage3.api_classifier import (
    _identify_infrastructure_apis,
    _is_list_query,
    _is_post_list_query,
    _build_core_apis_from_core_api_map,
    _map_buttons_to_apis,
    _derive_order,
    _select_core_api,
    _find_last_write_op,
)
from core.discovery.stage3.value_chain import (
    build_value_chain,
    _derive_dependencies,
    _derive_state_rules,
    _parse_body,
)
from core.discovery.stage3.field_classifier import (
    ValueIndex,
    build_value_index,
    classify_fields_recursive,
    is_noise_value,
)
from core.discovery.stage3.pre_api_tracer import trace_pre_api_dependencies
from core.discovery.stage3.manifest_builder import build_manifest

LOG = logging.getLogger("analyze_flow")


def analyze(core_api_map, all_endpoints, response_samples, ui_result,
            pre_api_candidates=None, profile=None,
            operation_order=None, all_calls=None) -> dict:
    """
    完整分析流程入口。

    从 core_api_map 构建 core_apis，不再使用旧的 HTTP 方法分类逻辑。

    Args:
        core_api_map: Stage 2 操作→核心API映射（核心输入）
        all_endpoints: 所有去重端点列表
        response_samples: 响应样本 {pathname: [{status, body, ts, context, method}, ...]}
        ui_result: discover_ui 的输出
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        profile: profile.yaml 配置字典
        operation_order: Stage 2 保存的操作顺序
        all_calls: 完整调用序列（用于 ValueChain 三原则追溯）
    """
    LOG.info("开始逻辑分析...")

    # Step 0: 基础设施 API 识别
    infra_apis = _identify_infrastructure_apis(all_endpoints, threshold=0.6)
    if infra_apis:
        LOG.info(f"  Step 0: 基础设施 API 识别: {len(infra_apis)} 个")

    # Step 1: 构建 core_apis
    core_apis = _build_core_apis_from_core_api_map(core_api_map, infra_apis)
    LOG.info(f"  Step 1 (core_api_map): {list(core_apis.keys())}")

    # Step 2: 按钮-API 关联
    api_by_button = _map_buttons_to_apis(core_apis, all_endpoints, ui_result)
    LOG.info(f"  Step 2: 按钮→API 映射: {len(api_by_button)} 条")

    # Step 3: 执行顺序（直接使用 Stage 2 的 operation_order）
    execution_order = _derive_order(core_apis, operation_order)
    LOG.info(f"  Step 3: 执行顺序: {execution_order}")

    # Step 4: 数据依赖链分析（三原则值匹配驱动）
    value_chain = build_value_chain(core_apis, response_samples, all_calls)
    dep_chain = _derive_dependencies(core_apis, response_samples, execution_order,
                                     value_chain=value_chain)
    LOG.info(f"  Step 4: 依赖字段: {list(dep_chain.get('injections', {}).keys())}")
    id_producer = dep_chain.get("id_producer")
    LOG.info(f"  Step 4: ID 生产者: {id_producer or '未找到'}")

    # Step 5: 状态断言推导（数据驱动：用操作名作 key）
    state_rules = _derive_state_rules(core_apis, response_samples, id_producer)
    LOG.info(f"  Step 5: 状态断言字段: {state_rules.get('state_field') or 'success_only'}")

    # Step 6: 构建统一 value_index（Phase B 和 build_manifest 共享）
    pre_api_candidates = pre_api_candidates or []
    context_fields = profile.get("context_fields", {}) if profile else {}

    create_body_sample = None
    create_response_sample = None
    if id_producer and id_producer in core_apis:
        create_ep = core_apis[id_producer][0]
        create_body_sample = _parse_body(create_ep)
        # ★ 从 ValueChain 获取正确的创建响应（按 context 筛选，非盲取第一个）
        create_pn = create_ep.get("pathname", "")
        create_resp_samples = response_samples.get(create_pn, [])
        # 优先按 context 筛选
        op_context = f"replay:{id_producer}"
        matched_samples = [s for s in create_resp_samples
                          if s.get("context") == op_context]
        if not matched_samples:
            matched_samples = create_resp_samples
        if matched_samples:
            try:
                body_text = matched_samples[0].get("body", "{}")
                create_response_sample = json.loads(body_text) if isinstance(body_text, str) else body_text
            except Exception:
                pass

    value_index = build_value_index(
        pre_api_candidates=pre_api_candidates,
        create_body_sample=create_body_sample,
        create_response_sample=create_response_sample,
        context_fields=context_fields,
    )

    # Step 7: 前置 API 依赖链追踪（Phase B，使用共享 value_index）
    pre_api_result = None
    if pre_api_candidates:
        pre_api_result = trace_pre_api_dependencies(
            core_apis, response_samples, pre_api_candidates,
            context_fields=context_fields,
            id_field_details=dep_chain.get("id_field_details", {}),
            id_producer=id_producer,
            value_index=value_index
        )
        LOG.info(f"  Step 7: 前置 API 追踪: {len(pre_api_result.get('pre_apis', []))} 个前置 API")

    return {
        "crud_order": execution_order,
        "core_apis": core_apis,
        "api_by_button": api_by_button,
        "dependencies": dep_chain,
        "state_assertions": state_rules,
        "value_index": value_index.to_dict() if value_index else None,
        "pre_api_chain": pre_api_result,
        "core_api_map": core_api_map or {},
        "value_chain": value_chain,  # ★ 三原则值关联链
    }
