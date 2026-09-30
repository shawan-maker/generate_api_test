"""
manifest_builder.py — Manifest 构建（从 analyze_flow.py 提取）

负责将 Stage 3 分析结果 + 响应约定发现 + 项目配置 → 结构化的 manifest dict。

核心函数:
  - build_manifest: 构建完整测试清单（~1230 行，含 11 个嵌套辅助函数）
  - _build_auth_profile: 从项目配置构建 auth_profile
  - _discover_response_contract: 从响应样本自动发现响应约定
  - _discover_envelope_keys / _discover_success_check / _discover_list_structure:
    响应约定发现的子步骤
"""

import re
import json
import logging
from collections import defaultdict

from core.discovery import const
from core.discovery.stage3.field_classifier import (
    ValueIndex, build_value_index, classify_fields_recursive,
    _get_nested_value, _flatten_body_for_match, _extract_by_path_generic,
)
from core.discovery.stage3.api_classifier import (
    _is_list_query, _is_post_list_query, _select_core_api,
)
from core.discovery.stage3.value_chain import (
    _parse_body, _discover_envelope_keys, _discover_success_check,
    _discover_list_structure, _discover_response_contract,
)

LOG = logging.getLogger("manifest_builder")


# ========== Auth Profile ==========

def _build_auth_profile(profile: dict) -> dict:
    """从项目 profile dict 构建 manifest 中的 auth_profile。

    Args:
        profile: 项目配置 dict（来自 project.yaml 或 run.py 传入）

    Returns:
        auth_profile dict
    """
    auth_cfg = profile.get("auth", {})
    captcha_cfg = profile.get("captcha", {})
    creds_cfg = profile.get("credentials", {}) or {}

    # 获取 probe_url 和 context_fields（必须从 profile 读取，无业务特定默认值）
    probe_url = profile.get("probe_url", auth_cfg.get("probe_url", ""))
    context_fields = profile.get("context_fields", {})

    if not probe_url:
        LOG.warning("profile.yaml 缺少 probe_url，auth 探活将不可用")
    if not context_fields:
        LOG.warning("profile.yaml 缺少 context_fields，上下文注入将不可用")

    return {
        "header_name": auth_cfg.get("header_name", "Authorization"),
        "header_prefix": auth_cfg.get("header_prefix", "Bearer "),
        "freshness_ttl_seconds": auth_cfg.get("freshness_ttl_seconds", 300),
        "fixed_headers": auth_cfg.get("fixed_headers", {}),
        "probe_url": probe_url,
        "context_fields": context_fields,
        "token_key": auth_cfg.get("token_key", ""),
        "token_storage": auth_cfg.get("token_storage", "localStorage"),
        "cookie_token_key": auth_cfg.get("cookie_token_key", ""),
        "captcha": {
            "auth_button_text": captcha_cfg.get("auth_button_text",
                                                 profile.get("captcha_auth_button", "")),
            "login_button_text": captcha_cfg.get("login_button_text",
                                                  profile.get("captcha_login_button", "")),
        },
        "credentials_env": {
            "username": profile.get("username_env", "APP_USER"),
            "password": profile.get("password_env", "APP_PASS"),
        },
        "credentials_default": {
            "username": creds_cfg.get("username", ""),
            "password": creds_cfg.get("password", ""),
        },
    }


# ========== Manifest 构建 ==========

def build_manifest(analysis: dict, capture_result: dict,
                   profile: dict, module_name: str, target_url: str,
                   ui_result: dict = None, infra_apis: set = None) -> dict:
    """构建完整测试清单（manifest）。

    将 Stage 3 分析结果 + 响应约定发现 + 项目配置 → 结构化的 manifest dict。

    Args:
        analysis: Stage 3 analyze() 的输出
        capture_result: Stage 2 捕获结果
        profile: 项目配置 dict
        module_name: 模块名称
        target_url: 目标页面 URL
        ui_result: Stage 1 UI 探测结果（可选，用于动态标签提取）
        infra_apis: 基础设施 API 路径集合（可选，用于过滤辅助 API）

    Returns:
        完整的 manifest dict
    """
    response_samples = capture_result.get("response_samples", {})
    core_apis = analysis.get("core_apis", {})
    crud_order = analysis.get("crud_order", [])
    infra_apis = infra_apis or set()
    pre_api_candidates = capture_result.get("pre_api_candidates", [])

    # 获取 id_producer（用于替代硬编码的 "create"）
    id_producer = analysis.get("dependencies", {}).get("id_producer")

    # 1. 发现响应约定
    response_contract = _discover_response_contract(response_samples)

    # 2. 构建 auth_profile
    auth_profile = _build_auth_profile(profile)

    # 3. 获取 create body 样本（用于字段分类）
    create_body_sample = None
    if id_producer and id_producer in core_apis:
        create_body_sample = _parse_body(core_apis[id_producer][0])

    # 4. 构建 steps 列表
    id_field_details = analysis.get("dependencies", {}).get("id_field_details", {})

    # ★ 用 id_field_details（创建响应中识别的 ID 字段）覆盖全局 id_field
    # 全局 _discover_id_field 扫描所有端点投票，init 端点的 id 字段会"污染"结果
    # id_field_details 只来自创建响应，是模块真正的 ID 字段
    if id_field_details:
        real_id_field = next(iter(id_field_details.keys()))
        if real_id_field != response_contract.get("id_field"):
            LOG.info(f"  ID字段覆盖: {response_contract.get('id_field')} → {real_id_field}（来自创建响应）")
            response_contract["id_field"] = real_id_field

    # 5. 获取 context_fields 字段名（用于优先标记 context 角色）
    context_fields = auth_profile.get("context_fields", {})
    context_field_names = set(context_fields.keys())

    # 6. 复用 analyze() 构建的 value_index（与 Phase B 共享同一份）
    value_index_dict = analysis.get("value_index")
    if value_index_dict is not None:
        value_index = ValueIndex.from_dict(value_index_dict)
    else:
        # 向后兼容：如果 analysis 中没有 value_index（旧数据），内部构建
        create_response_sample = None
        if id_producer and id_producer in core_apis:
            create_eps = core_apis[id_producer]
            create_pn = create_eps[0].get("pathname", "")
            create_resp_samples = response_samples.get(create_pn, [])
            # ★ 按 context 筛选创建响应样本
            op_context = f"replay:{id_producer}"
            matched_samples = [s for s in create_resp_samples if s.get("context") == op_context]
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

    # 获取可用的验证端点（query / detail）
    # 优先从 core_apis 获取；如果 query/detail 被同现过滤器误杀，
    # 则从 capture_result 原始数据中回退查找
    #
    # 关键校验：验证端点的响应样本必须包含 entity ID，否则 contains_id 断言无意义
    create_id_sample = None
    if create_body_sample and isinstance(create_body_sample, dict):
        # 从 create 响应样本中提取 ID（用于验证 verify endpoint 是否返回同类数据）
        create_eps = core_apis.get(id_producer, [])
        if create_eps:
            # 优先从 id_field_details 获取真实的 ID 字段
            for ep in create_eps:
                if create_id_sample:
                    break
                ep_pn = ep.get("pathname", "")
                ep_resp_samples = response_samples.get(ep_pn, [])
                # ★ 按 context 筛选创建响应样本
                op_context = f"replay:{id_producer}"
                matched_samples = [s for s in ep_resp_samples if s.get("context") == op_context]
                if not matched_samples:
                    matched_samples = ep_resp_samples
                for s in matched_samples:
                    try:
                        body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
                        if id_field_details:
                            id_field_name = next(iter(id_field_details.keys()))
                            create_id_sample = id_field_details[id_field_name]["sample_value"]
                        else:
                            # 降级：使用 response_contract
                            envelope_keys = response_contract.get("envelope_keys", const.ENVELOPE_KEY_CANDIDATES)
                            id_field = response_contract.get("id_field", "id")
                            entity = body
                            for ek in envelope_keys:
                                if ek in body and isinstance(body[ek], dict):
                                    entity = body[ek]
                                    break
                            if isinstance(entity, dict) and id_field in entity:
                                create_id_sample = entity[id_field]
                        if create_id_sample:
                            break
                    except Exception:
                        pass

    def _endpoint_returns_entity_id(ep) -> bool:
        """检查端点的响应样本是否包含 create 提取的 entity ID。

        值匹配原则：Stage 2 搜索时用的是本轮 marker，所以搜索响应中应包含本轮创建的 ID。
        如果 create ID 不在响应中，说明该端点与 create 不是同类资源。
        """
        if not create_id_sample:
            return True  # 无法校验时默认通过
        samples = response_samples.get(ep["pathname"], [])
        for s in samples:
            try:
                body_text = s["body"] if isinstance(s["body"], str) else json.dumps(s["body"])
                if create_id_sample in body_text:
                    return True
            except Exception:
                pass
        return False

    verify_endpoints = {}
    # ★ 使用 core_api_map 选择验证端点（语义优先级 > 字母排序）
    core_api_map = analysis.get("core_api_map", {})

    # 验证端点选择优先级：搜索操作 > 业务阶段列表查询 > init 操作(备选) > 列表查询兜底
    verify_ep = None
    verify_source = None  # 记录来源用于日志

    # 优先级 1：从"搜索"操作的核心 API 获取
    # 使用关键词匹配而非精确匹配，支持不同项目的操作命名
    for action_name, candidates in core_api_map.items():
        action_lower = action_name.lower()
        if any(kw in action_lower for kw in const.SEARCH_ACTION_KEYWORDS):
            full_ep = _select_core_api(candidates, "step")
            if full_ep and _endpoint_returns_entity_id(full_ep):
                verify_ep = full_ep
                verify_source = f"搜索操作 ({action_name})"
                break

    # 优先级 2：从 all_endpoints 中找业务阶段触发的列表查询
    # contexts 包含 "replay:" 说明是 CRUD 操作后自动触发的刷新查询，优先于 init
    if verify_ep is None:
        for ep in capture_result.get("all_endpoints", []):
            ctx_list = ep.get("contexts", [])
            has_replay = any("replay:" in str(c) for c in ctx_list)
            if not has_replay:
                continue
            pn = ep.get("pathname", "")
            if _is_list_query(pn, response_samples):
                verify_ep = ep
                verify_source = f"业务阶段列表查询 ({pn[:50]})"
                break

    # 优先级 3：从 init 操作获取（导航阶段的列表查询，仅作备选）
    if verify_ep is None and "init" in core_api_map:
        candidates = core_api_map["init"]
        full_ep = _select_core_api(candidates, "step")
        if full_ep and _endpoint_returns_entity_id(full_ep):
            verify_ep = full_ep
            verify_source = "init 操作（页面初始加载）"

    # 优先级 3：兜底 — 从所有 GET 端点中找列表查询
    if verify_ep is None:
        for ep in capture_result.get("all_endpoints", []):
            if ep.get("method") == "GET":
                pn = ep.get("pathname", "")
                # Try to find in core_api_map candidates
                for action, candidates in core_api_map.items():
                    for c in candidates:
                        if c.get("method") == ep.get("method") and c.get("pathname") == pn:
                            full_ep = c
                            if full_ep and _is_list_query(pn, response_samples) and _endpoint_returns_entity_id(full_ep):
                                verify_ep = full_ep
                                verify_source = f"列表查询兜底 ({pn[:50]})"
                                break
                    if verify_ep:
                        break
                if verify_ep:
                    break

    # 优先级 4：POST 列表查询兜底
    # 某些模块的列表查询是 POST（如 POST /policies/list），上面只搜 GET 会漏掉
    if verify_ep is None:
        for ep in capture_result.get("all_endpoints", []):
            if ep.get("method") == "POST":
                pn = ep.get("pathname", "")
                if not _is_list_query(pn, response_samples):
                    continue
                # Try to find in core_api_map candidates
                for action, candidates in core_api_map.items():
                    for c in candidates:
                        if c.get("method") == ep.get("method") and c.get("pathname") == pn:
                            full_ep = c
                            if full_ep and _endpoint_returns_entity_id(full_ep):
                                verify_ep = full_ep
                                verify_source = f"POST 列表查询兜底 ({pn[:50]})"
                                break
                    if verify_ep:
                        break
                if verify_ep:
                    break

    if verify_ep:
        verify_endpoints["query"] = {
            "ep": verify_ep,
            "body_sample": _parse_body(verify_ep),
        }
        LOG.info(f"  验证端点来源: {verify_source} → {verify_ep['pathname'][:60]}")
    else:
        LOG.warning("  验证端点未找到，写操作后将无自动验证")


    def _resolve_search_param_source(search_param: str, query_ep: dict,
                                     create_body: dict, resp_samples: dict) -> str:
        """分析 search_param 值在 create body 中的来源字段。

        通过对比搜索流量的参数值和 create body 字段值，确定映射关系。
        例如：search_param="name" 的值实际来自 create_body["userName"]。

        Args:
            search_param: 搜索参数名（如 "name"）
            query_ep: query 端点数据
            create_body: create 请求体样本
            resp_samples: 响应样本字典

        Returns:
            create body 中的源字段名，找不到则返回空字符串
        """
        if not create_body or not isinstance(create_body, dict):
            return ""

        # 从捕获的搜索流量中获取 search_param 的实际值
        # 优先检查 URL query params（GET 列表查询）
        query_params_samples = query_ep.get("query_params_samples", [])
        search_value = None
        for sample in query_params_samples:
            if isinstance(sample, dict) and search_param in sample:
                search_value = sample[search_param]
                break

        # 再检查 POST body（POST 列表查询）
        if not search_value:
            bodies = query_ep.get("bodies", [])
            for b in bodies:
                parsed = None
                if isinstance(b, str):
                    try:
                        parsed = json.loads(b)
                    except (json.JSONDecodeError, ValueError):
                        continue
                elif isinstance(b, dict):
                    parsed = b
                if isinstance(parsed, dict) and search_param in parsed:
                    search_value = parsed[search_param]
                    break

        if not search_value:
            return ""

        # 对比 create body 字段值，找出匹配项
        for field_name, field_value in create_body.items():
            if isinstance(field_value, str) and field_value == search_value:
                return field_name

        # 如果精确匹配失败，尝试部分匹配（如搜索值包含在字段值中）
        for field_name, field_value in create_body.items():
            if isinstance(field_value, str) and search_value in field_value:
                return field_name

        return ""

    steps = []

    # 提前获取 field_resolutions（用于 verify 步骤 query_params 生成）
    pre_api_chain = analysis.get("pre_api_chain")
    field_resolutions = {}
    collected_pre_api_ids = set()
    if pre_api_chain and pre_api_chain.get("pre_apis"):
        field_resolutions = pre_api_chain.get("field_resolutions", {})
        # 收集实际被收集的前置 API ID（用于限制嵌套 static 提升范围）
        collected_pre_api_ids = {api["id"] for api in pre_api_chain["pre_apis"] if "id" in api}

    def _get_create_pre_api_ref_source(field_name):
        """检查 create 步骤中指定字段的 context 来源，返回其 source 或 None。

        用于 verify 步骤的 query_params 生成：如果 create body 中某字段使用了
        context（如 tenantId ← current_user.entity_tenantId），verify 步骤
        的 query_params 也应引用相同来源，确保 tenantId 一致。
        """
        # 在 steps 中找到 create 步骤的 body_field_roles
        for step in steps:
            if step.get("action") == id_producer:
                field_roles = step.get("body_field_roles", {})
                role_info = field_roles.get(field_name, {})
                if role_info.get("role") == "context":
                    return role_info.get("source", "")
        return None

    def _analyze_path_params(pathname: str, body_sample, field_roles: dict,
                             value_index, create_body_sample: dict,
                             current_action: str, id_producer: str) -> dict:
        """分析 pathname 中的动态关联参数，在 Stage 3 确定完整映射。

        遍历 pathname 的每个路径段，用值匹配确定是否为动态参数及其来源。
        运行时只需执行映射，不做任何分析。

        与 body 字段的 classify_fields 对称：
          - classify_fields 对 body 的 key-value 做 Pass 1~5 分类
          - _analyze_path_params 对 pathname 的每个 segment 做 Step 0~3 分析
          - 两者共享同一个 value_index，共享值匹配原则
          - path 段无字段名信号，Pass 1 (name) 和 Pass 2 (mutable) 不适用

        Args:
            pathname: 原始 pathname（含真实值，未替换）
            body_sample: 请求体样本（用于值关联）
            field_roles: classify_fields 的输出（复用 body 的分析结果）
            value_index: 全局值索引
            create_body_sample: create 步骤的请求体
            current_action: 当前操作名
            id_producer: ID 生产者操作名

        Returns:
            {
                "pathname": 替换后的 pathname（如 /users/migrate/{path_0}），
                "path_params": {
                    "path_0": {
                        "original_value": "5bcbffa7...",
                        "source": "display_by_role.entity_0_children_0_id",
                        "match_from": "body_context"
                    },
                    ...
                },
                "has_create_id_ref": bool  # 是否引用了 create.id
            }
        """
        segments = pathname.split("/")
        path_params = {}
        has_create_id_ref = False
        param_counter = 0

        for i, segment_value in enumerate(segments):
            if not segment_value:
                continue

            # ── Step 0: 特异性判定 ──
            # 纯数字 ≥ 2 字符有特异性；含字母 ≥ 16 字符有特异性
            # 防止 v1, active, api 等静态路径段被误匹配
            is_pure_digit = segment_value.isdigit()
            if is_pure_digit:
                has_specificity = len(segment_value) >= 2
            else:
                has_specificity = len(segment_value) >= 16

            if not has_specificity:
                continue

            source = None
            match_from = None

            # ── Step 1: body 关联 ──
            if isinstance(body_sample, dict):
                for flat_key, flat_value in _flatten_body_for_match(body_sample, field_roles):
                    if isinstance(flat_value, (str, int, float, bool)) and str(flat_value) == segment_value:
                        role_info = field_roles.get(flat_key, {})
                        if role_info.get("role") == "context":
                            source = role_info.get("source", "")
                            match_from = "body_context"
                        # 如果字段角色不是 context，说明是固定值，不关联
                        break
                    elif isinstance(flat_value, list) and segment_value in [str(v) for v in flat_value]:
                        role_info = field_roles.get(flat_key, {})
                        if role_info.get("role") == "context":
                            source = role_info.get("source", "")
                            match_from = "body_context"
                        break

            # ── Step 1.5: body 已有 source 优先 ──
            # 如果 body 中已有 context source 指向某个 pre-API，且 value_index 中
            # 该 pre-API 也能匹配当前 segment_value → 优先复用该 source
            # 原因：复用已有 source 确保 pre-API 一定被收集，避免运行时 state 缺失
            if source is None and value_index is not None and isinstance(field_roles, dict):
                # 收集 body 中所有 context 字段的 source（api_id 前缀）
                body_context_api_ids = set()
                for role_info in field_roles.values():
                    if role_info.get("role") == "context":
                        src = role_info.get("source", "")
                        if "." in src:
                            api_id = src.split(".", 1)[0]
                            body_context_api_ids.add(api_id)

                # 如果 body 有 context source，检查 value_index 是否能匹配到同一 pre-API
                if body_context_api_ids:
                    entries = value_index.lookup_all(segment_value, current_action=current_action)
                    for entry in entries:
                        entry_api_id = entry.get("source_api", "")
                        if entry_api_id in body_context_api_ids:
                            source = entry.get("source_path", "")
                            match_from = "body_source_reuse"
                            break

            # ── Step 2: value_index 关联 ──
            if source is None and value_index is not None:
                entry = value_index.lookup(segment_value, current_action=current_action)
                if entry:
                    source = entry.get("source_path", "")
                    match_from = "value_index"

            # ── Step 3: 值模式兜底 ──
            if source is None:
                # 检查是否是 ID 模式
                is_id_pattern = False
                if is_pure_digit and len(segment_value) >= 8:
                    is_id_pattern = True
                elif not is_pure_digit and len(segment_value) >= 16:
                    if re.fullmatch(r'[0-9a-fA-F]{24,}', segment_value):
                        is_id_pattern = True
                    elif len(segment_value) == 36 and segment_value.count('-') == 4:
                        is_id_pattern = True  # UUID 格式
                    elif re.fullmatch(r'[A-Za-z0-9]{16,}', segment_value):
                        # 混合大小写+数字的 ID（如 AccessKey ID: 93552EDCG26cEWowiJylDR7M）
                        has_letter = any(c.isalpha() for c in segment_value)
                        has_digit = any(c.isdigit() for c in segment_value)
                        if has_letter and has_digit:
                            is_id_pattern = True

                if is_id_pattern and current_action != id_producer:
                    if id_producer is not None:
                        source = "create.id"
                        match_from = "create_id_fallback"
                    else:
                        # 无 id_producer（如纯查询模块），从列表查询响应中提取
                        source = "query.id"
                        match_from = "query_id_fallback"
                else:
                    # 非 ID 模式或是 create 操作 → static，不替换
                    continue

            # 生成 path_params 条目
            key = f"path_{param_counter}"
            segments[i] = f"{{{key}}}"
            path_params[key] = {
                "original_value": segment_value,
                "source": source,
                "match_from": match_from,
            }
            if source == "create.id" or source.startswith("create."):
                has_create_id_ref = True
            param_counter += 1

        new_pathname = "/".join(segments)
        return {
            "pathname": new_pathname,
            "path_params": path_params,
            "has_create_id_ref": has_create_id_ref,
        }

    def _strip_path_params_original(path_params):
        """移除 path_params 中的 original_value（诊断信息，运行时不用）。"""
        return {k: {kk: vv for kk, vv in v.items() if kk != "original_value"}
                for k, v in path_params.items()}

    def _clean_body_template(body_template, field_roles, value_index=None,
                              current_action="", collected_pre_api_ids=None):
        """清理 body_template，将动态角色字段替换为 ${function()} 占位符。

        按角色清理：
        - context  → None / []（运行时从 state 解析）
        - name     → ${gen_test_name("字段名")}（纯函数生成，不依赖捕获值）
        - mutable  → ${gen_mutable_value()}（纯函数生成）
        - generate → ${gen_模式名()}（如 ${gen_email()}、${gen_phone()}）
        - static   → 保留原始捕获值

        嵌套 dict 递归处理，field_roles 中的 dot-prefixed key 会被 strip 后传入。

        Args:
            body_template: 请求体模板（dict 或 list）
            field_roles: 字段角色映射（会被就地修改以添加提升的字段）
            value_index: 值索引（用于嵌套对象内部字段的值匹配）
            current_action: 当前操作名
            collected_pre_api_ids: 实际收集的前置 API ID 集合（限制提升范围）

        Returns:
            清理后的 body_template（深拷贝，不修改原对象）
        """
        if isinstance(body_template, list):
            return list(body_template)
        if not isinstance(body_template, dict):
            return body_template

        cleaned = {}
        for key, value in body_template.items():
            role_info = field_roles.get(key, {})
            role = role_info.get("role", "static")

            if role == "context":
                # 数组类型保留空列表，确保运行时 is_array 检测正确
                if isinstance(value, list) or role_info.get("is_array"):
                    cleaned[key] = []
                else:
                    cleaned[key] = None  # 标记为运行时解析
            elif role == "name":
                # name 角色：替换为生成器占位符，不依赖原始捕获值
                cleaned[key] = '${gen_test_name("' + key + '")}'
            elif role == "mutable":
                # mutable 角色：替换为生成器占位符
                cleaned[key] = "${gen_mutable_value()}"
            elif role == "generate":
                # generate 角色：按 pattern 生成对应函数调用
                pattern = role_info.get("pattern", "text")
                cleaned[key] = "${gen_" + pattern + "()}"
            elif isinstance(value, dict):
                # 递归清理嵌套对象
                prefix = f"{key}."
                nested_roles = {
                    rk[len(prefix):]: rv
                    for rk, rv in field_roles.items()
                    if rk.startswith(prefix)
                }
                if nested_roles:
                    cleaned[key] = _clean_body_template(value, nested_roles, value_index, current_action, collected_pre_api_ids)
                else:
                    cleaned[key] = value
            else:
                # static：保留原始值
                cleaned[key] = value
        return cleaned

    def _make_step(action, ep, body_sample, assertion=None):
        """构建单个步骤 dict。"""
        # 处理数组格式请求体（如 batch delete ["id1", "id2"]）
        if isinstance(body_sample, list):
            field_roles = {
                "__array_items__": {"role": "context", "source": f"create.id", "is_array": True}
            }
        else:
            # For create step, exclude create_body sources to prevent self-reference
            exclude = {"create_body"} if action == id_producer else None
            field_roles = classify_fields_recursive(
                body_sample, value_index, create_body_sample, current_action=action,
                exclude_sources=exclude
            )

        extract = None
        if action == id_producer:
            envelope_keys = response_contract["envelope_keys"]
            extract_fields = {}
            # 从 id_field_details 获取实际 ID 字段名和路径
            for field_name, field_info in id_field_details.items():
                extract_fields[field_name] = field_info["path"]
            # 兼容：也存一份 "id" key
            if id_field_details:
                first_info = next(iter(id_field_details.values()))
                extract_fields["id"] = first_info["path"]
            else:
                # 回退：使用全局 id_field
                id_field = response_contract["id_field"]
                extract_fields["id"] = f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field
            extract_fields["names"] = [k for k, v in field_roles.items() if v.get("role") == "name" and "." not in k]
            extract = extract_fields

        # 分析 pathname 中的动态关联参数（值匹配驱动）
        pathname = ep["pathname"]
        path_result = _analyze_path_params(
            pathname, body_sample, field_roles, value_index,
            create_body_sample, action, id_producer
        )
        pathname = path_result["pathname"]
        path_params = path_result["path_params"]

        # ★ 修正：body 中与 path param 同值的字段应引用 path param 的 source
        # 例如 PUT /accesskey/{id}/update body 中 id="93552..." 应和 path 中的 id 使用同一 source
        if path_params and isinstance(body_sample, dict):
            for pp_key, pp_info in path_params.items():
                pp_original = pp_info.get("original_value", "")
                pp_source = pp_info.get("source", "")
                if not pp_original or not pp_source:
                    continue
                for body_key, body_val in body_sample.items():
                    if isinstance(body_val, str) and body_val == pp_original:
                        if body_key in field_roles:
                            old_role = field_roles[body_key].get("role", "")
                            if old_role in ("generate", "static"):
                                field_roles[body_key] = {"role": "context", "source": pp_source}
                                LOG.info(f"  body.{body_key} 角色修正: {old_role} → context (source={pp_source})")

        # requires: 非 create 操作需要 id（确保 create 已成功）
        # 当 id_producer 为 None 时（纯查询模块，无创建操作），不添加 id 依赖
        requires = [] if (id_producer is None or action == id_producer) else ["id"]

        step = {
            "action": action,
            "label": (ui_result or {}).get("button_labels", {}).get(action)
                     or action,
            "api": {
                "method": ep["method"],
                "pathname": pathname,
                "path_params": _strip_path_params_original(path_params),
                "query_params": ep.get("query_params", {}),
            },
            "body_template": body_sample if isinstance(body_sample, list) else _clean_body_template(body_sample or {}, field_roles, value_index, action, collected_pre_api_ids),
            "body_field_roles": field_roles,
            "requires": requires,
        }
        if extract:
            step["extract"] = extract
        if assertion:
            step["assertion"] = assertion
        return step

    def _make_phased_step(action, eps):
        """构建带 phases 的多阶段步骤。

        eps[0] = 核心 API（body_field_count 最大）
        eps[1:] = 前置 phase（按 body_field_count 降序，少的先执行）

        执行顺序：eps[-1] → ... → eps[1] → eps[0]
        （最少的先执行 = 查询/准备 → 最多的后执行 = 提交）
        """
        # 核心 API（最后执行）
        core_ep = eps[0]
        core_body = _parse_body(core_ep)
        core_roles = classify_fields_recursive(
            core_body, value_index, create_body_sample, current_action=action
        )

        # 前置 phases（反转，最少的先执行）
        prepare_eps = list(reversed(eps[1:]))
        phases = []
        all_extracts = {}  # {field_name: "phase_id.state_key"}

        for pep in prepare_eps:
            phase_id = pep["pathname"].split("/")[-1]  # 如 "generate"
            pbody = _parse_body(pep)
            proles = classify_fields_recursive(
                pbody, value_index, create_body_sample, current_action=action
            )

            # 推导 extract：从响应样本中提取与核心 body 同名字段
            extracts = _infer_phase_extracts(pep, core_body, all_extracts)

            # 分析 pathname 中的动态关联参数
            path_result = _analyze_path_params(
                pep["pathname"], pbody, proles, value_index,
                create_body_sample, action, id_producer
            )

            phases.append({
                "id": phase_id,
                "label": f"{action}({phase_id})",
                "api": {
                    "method": pep["method"],
                    "pathname": path_result["pathname"],
                    "path_params": _strip_path_params_original(path_result["path_params"]),
                    "query_params": pep.get("query_params", {}),
                },
                "body_template": pbody or {},
                "body_field_roles": proles,
                "extract": extracts,
            })

        # 核心 phase（最后执行）
        core_path_result = _analyze_path_params(
            core_ep["pathname"], core_body, core_roles, value_index,
            create_body_sample, action, id_producer
        )
        phases.append({
            "id": "main",
            "label": action,
            "api": {
                "method": core_ep["method"],
                "pathname": core_path_result["pathname"],
                "path_params": _strip_path_params_original(core_path_result["path_params"]),
                "query_params": core_ep.get("query_params", {}),
            },
            "body_template": core_body if isinstance(core_body, list) else (core_body or {}),
            "body_field_roles": core_roles,
        })

        # 后处理：profile 配置的 static 字段覆盖
        # 某些场景下服务端会验证字段格式但忽略实际值（如 RSA 加密密码），
        # 此时应保持原始模板值（static），不替换为动态值。
        # 配置格式（profile.yaml）:
        #   static_overrides:
        #     - trigger_field: "isRandomPassword"  # 触发条件字段
        #       target_fields: ["newPassword"]      # 需要强制 static 的目标字段
        if isinstance(core_body, dict):
            static_overrides = profile.get("static_overrides", [])
            for override in static_overrides:
                trigger_field = override.get("trigger_field")
                target_fields = override.get("target_fields", [])
                if trigger_field and core_body.get(trigger_field):
                    for target in target_fields:
                        if target in core_roles and core_roles[target].get("role") != "static":
                            core_roles[target] = {"role": "static"}
                            LOG.info(f"  操作链: {target} 恢复为 static（{trigger_field}={core_body.get(trigger_field)} 时服务端验证格式但忽略值）")

        # 清理所有 phase 的 body_template 中的 context 字段值
        for phase in phases:
            phase["body_template"] = _clean_body_template(
                phase["body_template"], phase["body_field_roles"], value_index, action, collected_pre_api_ids
            )

        # extract（如果 action == id_producer）
        extract = None
        if action == id_producer:
            envelope_keys = response_contract["envelope_keys"]
            id_field = response_contract["id_field"]
            extract = {
                "id": f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field,
                "names": [k for k, v in core_roles.items() if v.get("role") == "name" and "." not in k],
            }

        step = {
            "action": action,
            "label": (ui_result or {}).get("button_labels", {}).get(action) or action,
            "phases": phases,
            "requires": [] if (id_producer is None or action == id_producer) else ["id"],
        }
        if extract:
            step["extract"] = extract
        return step

    def _infer_phase_extracts(phase_ep, core_body, all_extracts):
        """推导 phase 的 extract 映射。

        策略：检查 phase 响应中的字段，如果字段名在核心 body 中出现，
        建立 extract 映射，供核心 phase 引用。
        支持后缀模糊匹配（如 response 中 password → request 中 newPassword）。
        """
        pathname = phase_ep["pathname"]
        samples = response_samples.get(pathname, [])
        if not samples:
            return []

        try:
            resp_body = json.loads(samples[0].get("body", "{}")) if isinstance(
                samples[0].get("body", "{}"), str) else samples[0].get("body", {})
        except Exception:
            return []

        entity = None
        for key in const.ENVELOPE_KEY_CANDIDATES:
            if key in resp_body and isinstance(resp_body[key], dict):
                entity = resp_body[key]
                break
        if entity is None:
            entity = resp_body
        if not isinstance(entity, dict):
            return []

        phase_id = pathname.split("/")[-1]
        extracts = []

        # 构建核心 body 字段的后缀索引（用于模糊匹配）
        core_body_lower = {}
        if isinstance(core_body, dict):
            for cb_field in core_body:
                core_body_lower[cb_field.lower()] = cb_field

        for field_name, value in entity.items():
            if value is None:
                continue

            matched_core_field = None

            # 1. 精确匹配：字段名在核心 body 中直接出
            if isinstance(core_body, dict) and field_name in core_body:
                matched_core_field = field_name
            else:
                # 2. 后缀模糊匹配：response 字段名是 request 字段名的后缀
                #    如 response "password" → request "newPassword"
                fn_lower = field_name.lower()
                for cb_lower, cb_field in core_body_lower.items():
                    if cb_lower != fn_lower and cb_lower.endswith(fn_lower):
                        # 确认前缀部分合理（new, updated, old 等）
                        prefix = cb_lower[:len(cb_lower) - len(fn_lower)]
                        if prefix in ("new", "updated", "old", "target", "final"):
                            matched_core_field = cb_field
                            LOG.info(f"  操作链 extract 模糊匹配: {field_name} → {cb_field}")
                            break

            if matched_core_field:
                state_key = f"{phase_id}_{field_name}"
                extracts.append({
                    "name": state_key,
                    "path": f"entity.{field_name}",
                })
                all_extracts[matched_core_field] = f"{phase_id}.{state_key}"

        return extracts

    def _make_verify_step(verify_action, label, assertion):
        """构建验证步骤（基于可用的验证端点）。"""
        if verify_action not in verify_endpoints:
            return None

        ep = verify_endpoints[verify_action]["ep"]
        body_sample = verify_endpoints[verify_action]["body_sample"]

        field_roles = classify_fields_recursive(
            body_sample, value_index, create_body_sample, current_action=verify_action
        )

        # 分析 pathname 中的动态关联参数
        path_result = _analyze_path_params(
            ep["pathname"], body_sample, field_roles, value_index,
            create_body_sample, verify_action, id_producer
        )

        # 构建 query_params：从 samples 提取固定参数，映射到 state 引用
        query_params = {}
        samples = ep.get("query_params_samples", [])
        if samples and isinstance(samples[0], dict):
            # 取参数最多的样本作为基准
            best_sample = max(samples, key=lambda s: len(s) if isinstance(s, dict) else 0)
            # 排除搜索参数（search_param 会在运行时动态添加）
            search_param_name = ep.get("search_param", "")
            for pk, pv in best_sample.items():
                if pk == search_param_name:
                    continue
                # 优先检查 create step 中同名字段是否为 pre_api_ref
                # 如果 create 使用了 pre_api_ref 来源，verify 步骤也应使用相同来源
                create_ref_source = _get_create_pre_api_ref_source(pk)
                if create_ref_source:
                    query_params[pk] = f"${create_ref_source}"
                elif pk in context_field_names:
                    query_params[pk] = f"${pk}"
                else:
                    # 保持原始值（如 pageNum=1, pageSize=10）
                    query_params[pk] = pv

        step = {
            "action": ep["method"].lower(),
            "label": label,
            "api": {
                "method": ep["method"],
                "pathname": path_result["pathname"],
                "path_params": _strip_path_params_original(path_result["path_params"]),
                "query_params": query_params if query_params else ep.get("query_params", {}),
            },
            "body_template": _clean_body_template(body_sample or {}, field_roles, value_index, verify_action, collected_pre_api_ids),
            "body_field_roles": field_roles,
            "requires": [] if id_producer is None else ["id"],
            "assertion": assertion,
        }
        # search_verify / search_not_found 需要携带搜索参数名
        if assertion in ("search_verify", "search_not_found"):
            search_param = ep.get("search_param", "")
            if search_param:
                step["search_param"] = search_param
                # 分析 search_param 值在 create body 中的来源字段
                # 从捕获的搜索流量中获取参数值，匹配 create body 字段
                search_param_source = _resolve_search_param_source(
                    search_param, ep, create_body_sample, response_samples
                )
                if search_param_source:
                    step["search_param_source"] = search_param_source
        return step

    def _plan_verify_steps(action):
        """根据操作类型和可用端点，自动规划验证步骤。

        逻辑完全数据驱动，不依赖任何特定模块：
        - 写操作后，如果有可用的查询端点就插入验证
        - 断言类型由响应结构决定（列表→contains/not_contains，详情→field_changed）

        Args:
            action: 当前 CRUD 操作类型

        Returns:
            [(verify_action, label, assertion), ...] 验证步骤规划列表
        """
        plans = []
        action_label = (ui_result or {}).get("button_labels", {}).get(action) \
                       or action

        # 写操作后验证：基于 HTTP method 判断，不硬编码操作名
        # 检查该 action 对应的端点是否为写操作
        is_write_op = False
        is_delete_op = False
        if action in core_apis:
            for ep in core_apis[action]:
                method = ep.get("method", "GET")
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    is_write_op = True
                if method == "DELETE":
                    is_delete_op = True

        if is_write_op:
            # 优先用 query（列表验证），其次 detail（详情验证）
            if "query" in verify_endpoints:
                query_ep = verify_endpoints["query"]["ep"]
                search_param = query_ep.get("search_param", "")

                # 有 search_param → 使用 search_verify/search_not_found
                if search_param:
                    if is_delete_op:
                        plans.append(("query", f"搜索验证（删除后）", "search_not_found"))
                    else:
                        plans.append(("query", f"搜索验证（{action_label}后）", "search_verify"))
                # 无 search_param → 使用 contains_id/not_contains_id
                elif is_delete_op:
                    plans.append(("query", f"查询验证（删除后）", "not_contains_id"))
                elif action == id_producer:
                    plans.append(("query", f"查询验证（创建后）", "contains_id"))
                else:
                    plans.append(("query", f"查询验证（{action_label}后）", "contains_id"))
            elif "detail" in verify_endpoints:
                # 非 create/delete 的写操作可以用 detail 验证
                if action != id_producer and not is_delete_op:
                    plans.append(("detail", f"详情验证（{action_label}后）", "field_changed"))

        return plans

    # ── 无 id_producer 但有写操作时，插入列表查询步骤获取 ID ──
    has_write_ops = any(
        ep.get("method") in ("PUT", "PATCH", "DELETE")
        for action_eps in core_apis.values()
        for ep in action_eps
    )
    if id_producer is None and has_write_ops:
        # 优先找模块自身的列表查询端点（通过 write 端点的 pathname 推断）
        # 例如：PUT /accesskey/{id}/update → 查找 POST /accesskey/list
        init_query_ep = None
        write_path_prefixes = set()
        for action_eps in core_apis.values():
            for ep in action_eps:
                pn = ep.get("pathname", "")
                # 提取模块路径前缀（去掉最后的 ID 段和操作段）
                segments = [s for s in pn.split("/") if s]
                if len(segments) >= 4:
                    # 取倒数第 2~3 段作为模块标识（如 accesskey）
                    prefix = "/".join(segments[:-2]) if "/update" in pn or segments[-1] != "update" else "/".join(segments[:-3])
                    write_path_prefixes.add(prefix)

        # 从 all_endpoints 中找匹配的列表查询
        for ep in capture_result.get("all_endpoints", []):
            pn = ep.get("pathname", "")
            if ep.get("method") == "POST" and pn.endswith("/list"):
                segments = [s for s in pn.split("/") if s]
                if len(segments) >= 4:
                    ep_prefix = "/".join(segments[:-1])
                    if any(ep_prefix.startswith(wp) or wp.startswith(ep_prefix) for wp in write_path_prefixes):
                        init_query_ep = ep
                        break
            elif ep.get("method") == "GET" and _is_list_query(pn, response_samples):
                init_query_ep = ep
                break

        # 降级：使用 verify_endpoints 中的 query
        if init_query_ep is None and "query" in verify_endpoints:
            init_query_ep = verify_endpoints["query"]["ep"]

        if init_query_ep:
            query_ep = init_query_ep
            query_body = _parse_body(query_ep)
            query_roles = classify_fields_recursive(
                query_body, value_index, create_body_sample, current_action="query"
            )
            path_result = _analyze_path_params(
                query_ep["pathname"], query_body, query_roles, value_index,
                create_body_sample, "query", id_producer
            )
            init_step = {
                "action": "query",
                "label": "初始列表查询（获取 ID）",
                "api": {
                    "method": query_ep["method"],
                    "pathname": path_result["pathname"],
                    "path_params": _strip_path_params_original(path_result["path_params"]),
                    "query_params": query_ep.get("query_params", {}),
                },
                "body_template": _clean_body_template(query_body or {}, query_roles, value_index, "query", collected_pre_api_ids),
                "body_field_roles": query_roles,
                "requires": [],
                "extract": {
                    "id": f"{response_contract['envelope_keys'][0]}.list_0.id" if response_contract["envelope_keys"] else "list_0.id",
                },
            }
            steps.append(init_step)
            LOG.info("  插入初始列表查询步骤（从列表响应提取 ID）")

            # ★ 同时更新 verify_endpoints，让后续验证步骤使用正确的列表查询端点
            verify_endpoints["query"] = {
                "ep": query_ep,
                "body_sample": query_body,
            }
            LOG.info(f"  更新验证端点为: {query_ep['method']} {query_ep['pathname']}")

    for action in crud_order:
        eps = core_apis.get(action, [])
        if not eps:
            continue

        # 跳过只读操作：它们应作为验证端点（verify_endpoints），不作为 CRUD 步骤
        # GET → 只读；POST 用请求体特征判断（避免 response_samples 混合导致误判）；
        # PUT/PATCH/DELETE → 一定是写操作
        all_read_only = all(
            ep.get("method") == "GET"
            or _is_post_list_query(ep)
            for ep in eps
        )
        if all_read_only:
            LOG.info(f"  跳过只读操作 '{action}'（将作为验证端点使用）")
            continue

        # 分支：单 API vs 多 API 操作链
        if len(eps) == 1:
            # 单 API 操作
            ep = eps[0]
            body_sample = _parse_body(ep)

            # 主步骤
            steps.append(_make_step(action, ep, body_sample))
        else:
            # 多 API 操作链：生成带 phases 的 step
            phased_step = _make_phased_step(action, eps)
            steps.append(phased_step)

        # 根据可用端点自动规划验证步骤
        for verify_action, label, assertion in _plan_verify_steps(action):
            verify = _make_verify_step(verify_action, label, assertion)
            if verify:
                steps.append(verify)

    # 5. 组装 manifest（增强版：包含前置 API）
    manifest_version = "1.0"
    pre_apis_list = []
    # pre_api_chain 和 field_resolutions 已在前面获取

    if pre_api_chain and pre_api_chain.get("pre_apis"):
        manifest_version = "1.1"

        # 构建 pre_apis 数组（带 extracts 信息）
        for api_info in pre_api_chain["pre_apis"]:
            pre_api_entry = {
                "name": api_info.get("name", ""),
                "id": api_info.get("id", ""),
                "method": api_info.get("method", "GET"),
                "pathname": api_info.get("pathname", ""),
                "depends_on": api_info.get("depends_on", []),
                "extracts": [],
                "body_template": {},
                "query_params": {}
            }

            # 从 field_resolutions 找出哪些字段是从这个 API 提取的
            # 同时扫描 steps 中的 body_field_roles 和 path_params，收集 context 来源
            api_id_prefix = api_info["id"] + "."
            # 从 steps 中收集 context 字段引用此 API 的
            for step in steps:
                for fr_target in [step.get("body_field_roles", {})] + [
                    p.get("body_field_roles", {}) for p in step.get("phases", [])
                ]:
                    for field_name, role_info in (fr_target or {}).items():
                        if role_info.get("role") == "context":
                            source = role_info.get("source", "")
                            if source.startswith(api_id_prefix):
                                extract_name = source.split(".", 1)[1]
                                # 查找对应的 extracted_fields 获取 path
                                for ef in api_info.get("extracted_fields", []):
                                    if ef["name"] == extract_name:
                                        if not any(e["name"] == extract_name for e in pre_api_entry["extracts"]):
                                            # 收集 used_by 信息
                                            used_by = set()
                                            for s in steps:
                                                if s.get("body_field_roles", {}).get(field_name, {}).get("source") == source:
                                                    used_by.add(s.get("action", ""))
                                            pre_api_entry["extracts"].append({
                                                "name": extract_name,
                                                "path": ef["path"],
                                                "used_by": list(used_by) if used_by else ["*"]
                                            })
                                        break

                # 扫描 path_params 中的 context 来源
                for pp_target in [step.get("api", {}).get("path_params", {})] + [
                    p.get("api", {}).get("path_params", {}) for p in step.get("phases", [])
                ]:
                    for pk, pm in (pp_target or {}).items():
                        source = pm.get("source", "")
                        if source.startswith(api_id_prefix):
                            extract_name = source.split(".", 1)[1]
                            # 查找对应的 extracted_fields 获取 path
                            for ef in api_info.get("extracted_fields", []):
                                if ef["name"] == extract_name:
                                    if not any(e["name"] == extract_name for e in pre_api_entry["extracts"]):
                                        pre_api_entry["extracts"].append({
                                            "name": extract_name,
                                            "path": ef["path"],
                                            "used_by": ["url_path"]
                                        })
                                    break

            # 兼容旧格式：也从 field_resolutions 中收集
            for key, resolution in field_resolutions.items():
                step_action, field_name = key.split('.', 1)
                source = resolution.get("source", "")
                if source.startswith(api_id_prefix):
                    extract_name = source.split(".", 1)[1]
                    for ef in api_info.get("extracted_fields", []):
                        if ef["name"] == extract_name:
                            if not any(e["name"] == extract_name for e in pre_api_entry["extracts"]):
                                used_by = []
                                for k, res in field_resolutions.items():
                                    if res.get("source") == source:
                                        action, _ = k.split('.', 1)
                                        used_by.append(action)
                                pre_api_entry["extracts"].append({
                                    "name": extract_name,
                                    "path": ef["path"],
                                    "used_by": used_by if used_by else ["*"]
                                })
                            break

            pre_apis_list.append(pre_api_entry)

        # 验证：检查 path_params 引用的 pre-API 是否都已收集
        all_path_param_sources = set()
        for step in steps:
            # 收集 step 级别的 path_params
            for pm in step.get("api", {}).get("path_params", {}).values():
                source = pm.get("source", "")
                if source and not source.startswith(("create.", "auth.")):
                    api_id = source.split(".")[0]
                    all_path_param_sources.add(api_id)
            # 收集 phase 级别的 path_params
            for phase in step.get("phases", []):
                for pm in phase.get("api", {}).get("path_params", {}).values():
                    source = pm.get("source", "")
                    if source and not source.startswith(("create.", "auth.")):
                        api_id = source.split(".")[0]
                        all_path_param_sources.add(api_id)

        collected_api_ids = {api.get("id") for api in pre_apis_list}
        missing_api_ids = all_path_param_sources - collected_api_ids
        if missing_api_ids:
            LOG.warning(f"  ⚠️ path_params 引用了 {len(missing_api_ids)} 个未收集的 pre-API: {missing_api_ids}")

        # 补充 context_fields 提取：确保 query_params 引用的字段被提取
        if context_fields:
            for ctx_name, ctx_config in context_fields.items():
                ctx_path = ctx_config.get("path", "")
                if not ctx_path:
                    continue
                # 检查是否已有此字段提取
                already_extracted = False
                for pre_api in pre_apis_list:
                    if any(e.get("name") == ctx_name for e in pre_api.get("extracts", [])):
                        already_extracted = True
                        break
                if already_extracted:
                    continue
                # 找到能提供此字段的 pre_api（验证完整路径是否存在于响应中）
                for pre_api in pre_apis_list:
                    api_id = pre_api.get("id", "")
                    # 查找对应的 pre_api_candidates 获取响应样本
                    for candidate in capture_result.get("pre_api_candidates", []):
                        if candidate.get("id") == api_id:
                            body_text = candidate.get("response_sample", {}).get("response_body", "")
                            if body_text:
                                try:
                                    body = json.loads(body_text) if isinstance(body_text, str) else body_text
                                    # 验证完整路径是否存在（不仅是前缀）
                                    test_value = _extract_by_path_generic(body, ctx_path)
                                    if test_value is not None:
                                        pre_api["extracts"].append({
                                            "name": ctx_name,
                                            "path": ctx_path,
                                            "used_by": ["query"]
                                        })
                                        LOG.info(f"  补充 context_field 提取: {ctx_name} -> {ctx_path} (from {api_id})")
                                        already_extracted = True
                                except Exception:
                                    pass
                            break
                    if already_extracted:
                        break

    manifest = {
        "manifest_version": manifest_version,
        "module": {
            "name": module_name,
            "base_url": profile.get("base_url", ""),
            "login_url": profile.get("login_url", ""),
            "target_url": target_url,
        },
        "response_contract": response_contract,
        "auth_profile": auth_profile,
        "steps": steps,
        "state_assertions": analysis.get("state_assertions", {}),
    }

    # 添加 pre_apis（如果有）
    if pre_apis_list:
        manifest["pre_apis"] = pre_apis_list
        # v1.2: 添加 pre_api_refs 列表（仅包含 ID，用于项目级共享）
        manifest["pre_api_refs"] = [api["id"] for api in pre_apis_list]
        manifest["manifest_version"] = "1.2"

    LOG.info(f"  Manifest 构建完成: {len(steps)} 个步骤, "
             f"信封键={response_contract['envelope_keys'][:3]}, "
             f"ID字段={response_contract['id_field']}, "
             f"前置API={len(pre_apis_list)} 个")

    return manifest
