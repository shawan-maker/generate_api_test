"""
analyze_flow.py — Stage 3: 逻辑顺序与关联分析

核心推理流水线：
  Step 1: API 角色分类          → 过滤辅助 API，保留核心 CRUD API
  Step 2: 按钮-API 关联         → 建立 "按钮文本 → 核心 API" 映射
  Step 3: CRUD 顺序编排         → 推导正确的执行顺序
  Step 4: 数据依赖链分析        → 从响应中提取 id，注入后续请求
  Step 5: 状态断言自动推导      → 跨响应对比找出状态变化规律
"""

import re
import json
import logging
from typing import Optional
from collections import defaultdict
from . import const

LOG = logging.getLogger("analyze_flow")


def _identify_infrastructure_apis(all_endpoints: list, threshold: float = 0.6) -> set:
    """统计每个 GET 端点出现在多少不同操作 context 中。

    如果一个 GET 端点出现在 ≥threshold 的不同操作中 → 基础设施 API。
    例如 /current-user 在 create/update/delete/migrate 中都出现 → 基础设施。
    而 /display-unit-tree 只在 migrate 中出现 → 业务 API。

    额外规则：如果一个端点只在 init 上下文中出现（无任何 replay: 上下文），
    也视为基础设施 API（页面初始化加载的资源）。

    操作总数 ≤ 2 时降级为保守策略：只排除已知的全局基础设施路径
    （/current-user, /access-log 等），避免频率统计不可靠。

    Args:
        all_endpoints: 所有去重端点列表（含 method/context 字段）
        threshold: 出现比例阈值（默认 60%）

    Returns:
        set: 基础设施 API 的 pathname 集合
    """
    path_contexts = defaultdict(set)
    all_contexts = set()
    init_only_paths = set()  # 只在 init 上下文出现的端点

    for ep in all_endpoints:
        pathname = ep.get("pathname", "")
        contexts = ep.get("contexts", [])

        has_replay = False
        for ctx in contexts:
            if ctx.startswith("replay:"):
                has_replay = True
                action = ctx.split(":", 1)[1]
                path_contexts[pathname].add(action)
                all_contexts.add(action)

        # 记录只在 init 上下文出现的端点
        if not has_replay:
            init_only_paths.add(pathname)

    # 规则 1：只在 init 上下文出现 → 基础设施 API
    infra = set(init_only_paths)
    if init_only_paths:
        LOG.debug(f"  基础设施 API（init-only）: {init_only_paths}")

    # 规则 2：频率统计（出现在多个 replay 操作中）
    total = len(all_contexts)
    if total == 0:
        return infra

    # 降级策略：操作总数 ≤ 2 时，只排除已知的全局基础设施路径
    _KNOWN_GLOBAL_INFRA = {"/users/current-user", "/current-user", "/access-log",
                           "/system-theme", "/favorite"}
    if total <= 2:
        for path in path_contexts:
            if path in _KNOWN_GLOBAL_INFRA:
                infra.add(path)
        LOG.debug(f"  基础设施 API 降级识别（total={total}）: {infra}")
        return infra

    for path, contexts in path_contexts.items():
        ratio = len(contexts) / total
        if ratio >= threshold:
            infra.add(path)
            LOG.debug(f"  基础设施 API: {path} 出现在 {len(contexts)}/{total} ({ratio:.2f}) 个操作中")

    return infra


def _filter_by_cooccurrence(all_endpoints: list, threshold: float = 0.6,
                            response_samples: dict = None) -> set:
    """
    同现频率过滤：统计每个 API 出现在多少个不同按钮上下文中。

    如果一个 API 在 60%+ 的按钮点击后都出现，说明它是辅助 API（菜单加载、字典、通知等）。
    例外：响应含 list+total 结构的列表查询 API 不会被过滤（即使同现频率高）。

    Args:
        all_endpoints: 所有去重端点列表，每个包含 contexts 字段
        threshold: 同现比例阈值，默认 0.6
        response_samples: 响应样本（用于列表查询保护判断）

    Returns:
        set: 被判定为辅助 API 的 pathname 集合
    """
    # 统计每个 API 出现的不同按钮上下文数
    api_contexts = defaultdict(set)
    total_button_contexts = set()

    for ep in all_endpoints:
        pathname = ep.get("pathname", "")
        contexts = ep.get("contexts", [])

        for ctx in contexts:
            # 提取按钮标识（click:按钮名 或 dropdown:更多:子项）
            if ":" in ctx:
                button_key = ctx.split(":", 1)[0] + ":" + ctx.split(":", 1)[1].split(":")[0]
                api_contexts[pathname].add(button_key)
                total_button_contexts.add(button_key)

    # 计算同现比例
    supporting_apis = set()
    total_buttons = len(total_button_contexts)

    if total_buttons > 0:
        for pathname, contexts in api_contexts.items():
            ratio = len(contexts) / total_buttons
            if ratio >= threshold:
                # 列表查询保护：响应含 list+total 结构的 API 不应被过滤
                if _is_list_query(pathname, response_samples):
                    LOG.debug(f"同现过滤保护: {pathname} 是列表查询，保留")
                    continue
                LOG.debug(f"同现过滤: {pathname} 出现在 {len(contexts)}/{total_buttons} ({ratio:.2f}) 个按钮上下文")
                supporting_apis.add(pathname)

    return supporting_apis


def _is_list_query(pathname: str, response_samples: dict) -> bool:
    """判断 API 是否为列表查询（响应含 list+total 结构）。

    列表查询 API 即使出现在多个按钮上下文中也不应被同现过滤器排除，
    因为页面操作后刷新列表是常见行为。
    """
    if not response_samples:
        return False
    samples = response_samples.get(pathname, [])
    for s in samples[:1]:
        try:
            body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            # 尝试所有候选信封键，而非硬编码 "entity"
            entity = body
            for ek in const.ENVELOPE_KEY_CANDIDATES:
                if ek in body and isinstance(body[ek], (dict, list)):
                    entity = body[ek]
                    break
            if isinstance(entity, dict):
                has_list = any(k in entity for k in const.LIST_KEY_CANDIDATES)
                has_total = any(k in entity for k in const.TOTAL_KEY_CANDIDATES)
                if has_list and has_total:
                    return True
        except Exception:
            pass
    return False


def analyze(classified_apis: dict, all_endpoints: list,
            response_samples: dict, ui_result: dict,
            pre_api_candidates: list = None, profile: dict = None) -> dict:
    """
    完整分析流程入口。
    :param classified_apis:   capture_apis 归类结果（by_category）
    :param all_endpoints:     所有去重端点列表
    :param response_samples:  响应样本 {pathname: [{status, body}, ...]}
    :param ui_result:         discover_ui 的输出
    :param pre_api_candidates: 前置 API 候选列表（来自 Phase A）
    :param profile:           profile.yaml 配置字典（用于读取 configurable 参数）
    :return: FlowAnalysis
    """
    LOG.info("开始逻辑分析...")

    # Step -1: 用当前分类器重新分类所有端点
    # 原因：分类器逻辑可能已修复（如 replay context 匹配），但 Stage 2 输出中
    # 的分类结果仍是旧分类器的输出。重新分类确保使用最新逻辑。
    reclassified_apis = _reclassify_all_endpoints(classified_apis, all_endpoints)

    # Step 0a: 基础设施 API 识别（频率统计替代 URL 关键词）
    infra_apis = _identify_infrastructure_apis(all_endpoints, threshold=0.6)
    if infra_apis:
        LOG.info(f"  Step 0a: 基础设施 API 识别: {len(infra_apis)} 个 API 被标记为基础设施")

    # Step 0b: 同现频率过滤（识别在多个按钮上下文中都出现的辅助 API）
    cooccurrence_support = _filter_by_cooccurrence(all_endpoints, threshold=0.6,
                                                   response_samples=response_samples)
    if cooccurrence_support:
        LOG.info(f"  Step 0b: 同现频率过滤: {len(cooccurrence_support)} 个 API 被标记为辅助")

    # Step 1: 过滤辅助 API，找核心 CRUD API
    core_apis = _filter_core_apis(reclassified_apis, response_samples, cooccurrence_support,
                                  profile, infra_apis)
    LOG.info(f"  Step 1: 核心 API 类别: {list(core_apis.keys())}")

    # Step 2: 按钮-API 关联
    api_by_button = _map_buttons_to_apis(core_apis, all_endpoints, ui_result)
    LOG.info(f"  Step 2: 按钮→API 映射: {len(api_by_button)} 条")

    # Step 3: CRUD 顺序编排（增强：时序验证 + 依赖约束）
    execution_order = _derive_order(core_apis, all_endpoints)
    LOG.info(f"  Step 3: 执行顺序: {execution_order}")

    # Step 4: 数据依赖链分析
    dep_chain = _derive_dependencies(core_apis, response_samples)
    LOG.info(f"  Step 4: 依赖字段: {list(dep_chain.get('injections', {}).keys())}")

    # Step 5: 状态断言推导
    state_rules = _derive_state_rules(core_apis, response_samples)
    LOG.info(f"  Step 5: 状态断言字段: {state_rules.get('state_field') or 'success_only'}")

    # Step 6: 前置 API 依赖链追踪（Phase B）
    pre_api_candidates = pre_api_candidates or []
    pre_api_result = None
    if pre_api_candidates:
        pre_api_result = trace_pre_api_dependencies(
            core_apis, response_samples, pre_api_candidates,
            context_fields={}, id_field_details=dep_chain.get("id_field_details", {})
        )
        LOG.info(f"  Step 6: 前置 API 追踪: {len(pre_api_result.get('pre_apis', []))} 个前置 API")

    return {
        "crud_order": execution_order,
        "core_apis": core_apis,
        "api_by_button": api_by_button,
        "dependencies": dep_chain,
        "state_assertions": state_rules,
        "pre_api_chain": pre_api_result,
        "reclassified_apis": reclassified_apis,  # 添加重新分类的数据
    }


def _reclassify_all_endpoints(classified_apis: dict, all_endpoints: list) -> dict:
    """用当前分类器重新分类所有端点。

    Stage 2 保存的 by_category 可能由旧版分类器生成。在 analyze() 入口
    重新分类，确保分类逻辑修复（如 replay context 匹配）立即生效。

    注意：Stage 2 的时间戳优先分类法是 query/detail 分类的唯一可靠来源
    （classify_endpoint 兜底逻辑不会产生 query 类别），因此对于
    Stage 2 已分类为 query/detail 的端点，直接保留原始分类。

    Args:
        classified_apis: Stage 2 保存的 by_category
        all_endpoints: 所有去重端点列表（含 method/pathname/contexts）

    Returns:
        重新分类后的 by_category dict
    """
    from .endpoint_classifier import classify_endpoint

    # ★ 构建端点到原始类别的映射（用于保留 Stage 2 的 query/detail 分类）
    original_cat_map = {}  # {(method, pathname): category}
    for cat, eps in classified_apis.items():
        for ep in eps:
            key = (ep.get("method", "GET"), ep.get("pathname", ""))
            original_cat_map[key] = cat

    reclassified = {}
    for ep in all_endpoints:
        method = ep.get("method", "GET")
        pathname = ep.get("pathname", "")
        contexts = ep.get("contexts", [])

        # ★ 保留 Stage 2 的 query/detail 分类（时间戳优先分类法的结果）
        original_cat = original_cat_map.get((method, pathname))
        if original_cat in ("query", "detail"):
            new_cat = original_cat
        else:
            new_cat = classify_endpoint(method, pathname, contexts)

        if new_cat not in reclassified:
            reclassified[new_cat] = []
        # 从原分类中找到完整的 endpoint dict（保留 bodies 等字段）
        found = False
        for old_cat, old_eps in classified_apis.items():
            for old_ep in old_eps:
                if old_ep["pathname"] == pathname and old_ep["method"] == method:
                    reclassified[new_cat].append(old_ep)
                    found = True
                    break
            if found:
                break
        if not found:
            # 端点不在原始分类中（理论上不应发生），构造最小 dict
            reclassified[new_cat].append({
                "method": method,
                "pathname": pathname,
                "contexts": contexts,
                "bodies": ep.get("bodies", []),
                "request_body_sample": None,
                "query_params": {},
            })
    return reclassified


def _filter_core_apis(classified: dict, response_samples: dict,
                      cooccurrence_support: set = None, profile: dict = None,
                      infra_apis: set = None) -> dict:
    """
    Step 1: 过滤辅助 API。

    辅助 API 判断标准：
    - 基础设施 API（频率统计识别，在多个操作中都出现）→ infra_apis
    - 在所有按钮点击后都会出现（同现频率高）→ cooccurrence_support
    - GET 类查询且路径不含核心资源名
    """
    core = {}
    cooccurrence_support = cooccurrence_support or set()
    infra_apis = infra_apis or set()

    for category, endpoints in classified.items():
        # 跳过 support 类别（校验类 API）
        if category == "support":
            continue

        real_eps = []
        for ep in endpoints:
            # 基础设施 API 过滤（频率统计结果）
            if ep["pathname"] in infra_apis:
                LOG.debug(f"  基础设施 API 过滤: {ep['pathname']}")
                continue

            # 同现频率过滤：在 60%+ 的按钮上下文中都出现的 API 是辅助 API
            if ep["pathname"] in cooccurrence_support:
                LOG.debug(f"  同现过滤跳过: {ep['pathname']}")
                continue

            # ★ 豁免验证类别：query/detail 是验证专用，保留 GET 端点
            if category in ("query", "detail"):
                real_eps.append(ep)
                continue

            # 关键：只保留 POST/PUT/DELETE 方法作为核心业务 API
            # GET 请求是 pre-API（数据加载）或基础设施 API，不应纳入 core_apis
            method = ep.get("method", "GET")
            if method not in ("POST", "PUT", "DELETE", "PATCH"):
                LOG.debug(f"  跳过 GET 请求（pre-API 或基础设施）: {ep['pathname']}")
                continue

            # 过滤辅助 POST：路径包含 /list, /check, /query 等关键词的是数据加载 POST
            if method == "POST":
                path_lower = ep["pathname"].lower()
                helper_keywords = ["/list", "/check", "/query", "/search", "/get"]
                if any(kw in path_lower for kw in helper_keywords):
                    LOG.debug(f"  跳过辅助 POST（数据加载）: {ep['pathname']}")
                    continue

            real_eps.append(ep)

        if real_eps:
            core[category] = real_eps

    # 如果 "other_post" 和 "other_get" 中有真正的业务 API，
    # 尝试通过响应体判断（列表→query；含单条 id→execute）
    # 修复：每个端点独立分类，避免 target_cat 跨端点共享
    from collections import defaultdict
    for cat in ("other_post", "other_get"):
        if cat not in core:
            continue
        reclassified = defaultdict(list)
        for ep in core[cat]:
            pn = ep["pathname"]
            samples = response_samples.get(pn, [])
            if not samples:
                continue
            ep_cat = None  # 每个端点独立的目标分类
            for s in samples:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
                except Exception as e:
                    LOG.debug(f"解析响应样本 JSON 失败 ({pn}): {e}")
                    continue
                entity = _extract_entity_with_fallback(body)
                if isinstance(entity, dict):
                    # 查找列表键（泛化：不再硬编码 "list"）
                    # 修复: 排除 "data" 字段，避免非列表响应被误判为 query
                    for lk in const.LIST_KEY_CANDIDATES:
                        if lk == "data":  # data 字段太通用，跳过
                            continue
                        if lk in entity and isinstance(entity[lk], list):
                            # 额外检查：列表元素应为 dict（典型列表响应结构）
                            if entity[lk] and isinstance(entity[lk][0], dict):
                                ep_cat = "query"
                                reclassified[ep_cat].append(ep)
                                break
                    else:
                        # 查找 ID 键（使用通用 ID 字段列表，而非硬编码 "id"）
                        if any(id_field in entity for id_field in const.COMMON_ID_FIELDS):
                            reclassified["execute"].append(ep)
                        elif isinstance(entity, list) and len(entity) > 0:
                            reclassified["query"].append(ep)
                    break  # 只用第一个有效响应样本
            if ep_cat is None:
                # 无响应样本或无法分类 → 默认为 execute
                reclassified["execute"].append(ep)

        for dest_cat, eps in reclassified.items():
            core[dest_cat] = core.get(dest_cat, []) + eps
        del core[cat]

    return core


def _map_buttons_to_apis(core_apis: dict, all_endpoints: list,
                         ui_result: dict) -> dict:
    """
    Step 2: 构建 按钮文本 → 核心API 的映射。

    方法：
    - 从 ui_result 中收集所有按钮文本
    - 对每个核心 API，查看其 contexts 中包含了哪些按钮点击
    - 建立反向映射
    """
    # 收集所有按钮文本
    button_texts = set()
    for key in ("toolbar_buttons", "row_actions", "dropdowns"):
        for btn in ui_result.get(key, []):
            button_texts.add(btn["text"])
            if btn.get("parent"):
                button_texts.add(f"{btn['parent']}→{btn['text']}")

    # 建立 context → button 映射
    mapping = {}
    for category, endpoints in core_apis.items():
        for ep in endpoints:
            for ctx in ep.get("contexts", []):
                # ctx 格式: "click:新增用户" 或 "dropdown:更多:锁定"
                parts = ctx.split(":")
                if len(parts) >= 2:
                    btn_text = parts[1]
                    if btn_text not in mapping:
                        mapping[btn_text] = []
                    mapping[btn_text].append({
                        "category": category,
                        "method": ep["method"],
                        "pathname": ep["pathname"],
                        "body_sample": ep.get("request_body_sample"),
                        "query_params": ep.get("query_params", {}),
                    })

    return mapping


def _derive_order(core_apis: dict, all_endpoints: list = None) -> list:
    """
    Step 3: 根据核心 API 类别推导执行顺序（基于时间戳 + 依赖约束）。

    策略：
    1. 时间戳验证（interceptor 时间戳）作为基础顺序
    2. 依赖约束（拓扑排序）确保前置步骤先执行

    注意：query/detail/execute 仅用于验证步骤，不作为独立业务步骤出现在排序中。

    Returns:
        排序后的 CRUD 类别列表
    """
    available = set(core_apis.keys())

    # query/detail/execute 仅用于验证步骤，不进入 crud_order
    _VERIFY_ONLY = {"query", "detail", "execute"}
    available -= _VERIFY_ONLY

    if not available:
        return []

    # 1. 时间戳验证（从 all_endpoints 提取时间戳）作为基础顺序
    if all_endpoints:
        temporal_order = _derive_temporal_order(core_apis, all_endpoints)
        if temporal_order:
            LOG.debug(f"  时序推导: {temporal_order}")
            base_order = temporal_order
        else:
            # 如果没有时间戳信息，使用核心 API 的键顺序作为备选
            base_order = list(available)
            LOG.debug(f"  无时间戳信息，使用键顺序: {base_order}")
    else:
        base_order = list(available)
        LOG.debug(f"  无 all_endpoints，使用键顺序: {base_order}")

    # 2. 依赖约束（拓扑排序）
    dep_graph = _build_dependency_graph(core_apis)
    if dep_graph:
        topo_order = _topological_sort(dep_graph, base_order)
        if topo_order:
            LOG.debug(f"  拓扑排序: {topo_order}")
            base_order = topo_order

    return base_order


def _derive_temporal_order(core_apis: dict, all_endpoints: list) -> list:
    """
    从 interceptor 时间戳推导执行顺序。

    策略：统计每个 CRUD 类别的首次出现时间，按时间排序。

    支持两种 context 格式：
    - 旧格式：click:按钮名:timestamp
    - 新格式：replay:action（需要从 request_interceptor 的 submit_marks 获取时间戳）
    """
    first_seen = {}

    for category, endpoints in core_apis.items():
        for ep in endpoints:
            for ctx in ep.get("contexts", []):
                # 旧格式：click:按钮名:timestamp
                parts = ctx.split(":")
                if len(parts) >= 3:
                    try:
                        ts = float(parts[-1])
                        if category not in first_seen or ts < first_seen[category]:
                            first_seen[category] = ts
                        continue
                    except (ValueError, TypeError):
                        pass

                # 新格式：replay:action
                # 这种情况下，all_endpoints 中没有直接的时间戳
                # 需要通过 endpoint 在列表中的位置来推断顺序
                if parts[0] == "replay" and len(parts) == 2:
                    # 使用 endpoint 在 all_endpoints 中的索引作为时间戳
                    ep_index = -1
                    for i, all_ep in enumerate(all_endpoints):
                        if (all_ep.get("method") == ep.get("method") and
                            all_ep.get("pathname") == ep.get("pathname")):
                            ep_index = i
                            break
                    if ep_index >= 0:
                        if category not in first_seen or ep_index < first_seen[category]:
                            first_seen[category] = ep_index

    if not first_seen:
        return []

    # 按时间戳排序
    sorted_cats = sorted(first_seen.items(), key=lambda x: x[1])
    return [cat for cat, _ in sorted_cats]


def _merge_orders(base: list, temporal: list) -> list:
    """
    合并静态优先级与时序顺序。

    策略：保持静态优先级为主，仅调整时序差异明显的相邻步骤。
    """
    # 如果时序顺序与静态顺序一致，直接返回
    if base == temporal:
        return base

    # 检查是否有明显的时序冲突（如 delete 在 create 之前）
    result = list(base)
    for i in range(len(result) - 1):
        curr, next_cat = result[i], result[i + 1]
        if curr in temporal and next_cat in temporal:
            curr_idx = temporal.index(curr)
            next_idx = temporal.index(next_cat)
            # 如果时序上 next 明显早于 curr（差距 > 2 位），交换
            if next_idx < curr_idx - 2:
                result[i], result[i + 1] = next_cat, curr
                LOG.debug(f"  时序调整: {curr} ↔ {next_cat}")

    return result


def _build_dependency_graph(core_apis: dict) -> dict:
    """
    构建 CRUD 依赖图。

    规则（仅保留强依赖）：
    - 所有写操作依赖 create（需要 id）
    - delete 依赖所有其他写操作（必须先完成所有业务操作再删除）

    注意：不再强制 "unlock 依赖 lock"，因为：
    - 如果 capture 阶段是 lock→unlock 顺序，时序已保证
    - 如果 capture 阶段是 unlock→lock（异常），强制依赖反而会掩盖问题
    """
    graph = {cat: set() for cat in core_apis}

    # 通用规则：所有写操作依赖 create（需要 id）
    if "create" in core_apis:
        for cat in core_apis.keys():
            if cat in ("update", "lock", "unlock", "reset", "delete", "migrate", "authorize"):
                graph[cat].add("create")

    # 关键规则：delete 依赖所有其他写操作（必须先完成所有业务操作再删除）
    if "delete" in graph:
        for cat in core_apis.keys():
            if cat not in ("delete", "query", "detail"):
                graph["delete"].add(cat)

    return graph


def _topological_sort(graph: dict, base_order: list) -> list:
    """
    拓扑排序（Kahn 算法），保持 base_order 中的相对顺序。

    如果存在环或无法排序，返回 None。
    """
    in_degree = {cat: 0 for cat in graph}
    for cat, deps in graph.items():
        for dep in deps:
            if dep in in_degree:
                in_degree[cat] += 1

    # 初始化队列（按 base_order 顺序）
    queue = [cat for cat in base_order if in_degree[cat] == 0]
    result = []

    while queue:
        # 按 base_order 顺序选择（保持稳定排序）
        curr = queue.pop(0)
        result.append(curr)

        # 更新依赖
        for cat in base_order:
            if curr in graph.get(cat, set()):
                in_degree[cat] -= 1
                if in_degree[cat] == 0 and cat not in result:
                    queue.append(cat)

    # 检查是否所有节点都已排序
    if len(result) != len(graph):
        LOG.warning("  拓扑排序检测到环或无法排序")
        return None

    return result


def _derive_dependencies(core_apis: dict, response_samples: dict) -> dict:
    """
    Step 4: 从响应样本中分析数据依赖关系（增强版：递归提取嵌套 ID）。

    重点找出：
    - 创建 API 的响应中返回的 id 字段名（如 id, userId, resourceId）
    - 深层嵌套路径（如 entity.data.id, entity.user.id）
    - 后续 API 请求体中引用这些 id 的模式
    """
    injections = {}  # {依赖字段: {"source_crud": "create", "source_path": "..."}}
    id_field_candidates = {}

    # 从所有响应中找 ID 字段（递归提取）
    # create 优先遍历：确保 ID 字段来源绑定到 create 操作，而非 reset 等其他操作
    ordered_categories = []
    if "create" in core_apis:
        ordered_categories.append("create")
    for cat in core_apis:
        if cat != "create":
            ordered_categories.append(cat)

    for category in ordered_categories:
        endpoints = core_apis[category]
        for ep in endpoints:
            pn = ep["pathname"]
            samples = response_samples.get(pn, [])
            for s in samples[:1]:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
                except Exception as e:
                    LOG.debug(f"解析 ID 字段响应样本失败 ({pn}): {e}")
                    continue

                # 递归提取所有 ID 字段（包括深层嵌套）
                found_ids = _extract_ids_recursive(body, path="", max_depth=4)

                for field_path, field_name, sample_value in found_ids:
                    if field_name not in id_field_candidates:
                        id_field_candidates[field_name] = {
                            "source_crud": category,
                            "sample_value": sample_value,
                            "path": field_path,  # 新增：记录完整路径
                        }
                        LOG.debug(f"  发现 ID 字段: {field_path} = {sample_value}")

    # 检查后续 API 是否引用了这些 id 字段
    for category, endpoints in core_apis.items():
        for ep in endpoints:
            body_sample = ep.get("request_body_sample") or (
                ep.get("bodies")[0] if ep.get("bodies") else None)
            if not body_sample:
                continue
            try:
                req_body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
            except Exception as e:
                LOG.debug(f"解析请求体 JSON 失败: {e}")
                continue
            if isinstance(req_body, list):
                # 数组格式（如 batch delete ["id1", "id2"]）：记录 ID 注入点
                if req_body and isinstance(req_body[0], str) and len(req_body[0]) >= 8:
                    injections["__array_items__"] = {
                        "source": "create",
                        "source_path": "entity.id",
                    }
                continue
            if not isinstance(req_body, dict):
                continue
            for fname in const.COMMON_ID_FIELDS:
                if fname in req_body and fname not in injections:
                    injections[fname] = {
                        "source": id_field_candidates.get(fname, {}).get(
                            "source_crud",
                            "create" if "create" in core_apis else category),
                        "source_path": id_field_candidates.get(fname, {}).get("path", fname),
                    }

    result = {
        "injections": injections,
        "id_fields_found": list(id_field_candidates.keys()),
        "id_field_details": id_field_candidates,  # 新增：包含路径信息
    }
    return result


def _extract_ids_recursive(obj, path: str = "", max_depth: int = 4, _depth: int = 0):
    """
    递归遍历 JSON 对象，提取所有 ID 字段。

    Args:
        obj: JSON 对象（dict/list/primitive）
        path: 当前路径（如 "entity.data"）
        max_depth: 最大递归深度
        _depth: 当前深度（内部使用）

    Returns:
        list of tuples: (full_path, field_name, sample_value)
        例如: [("entity.id", "id", "12345"), ("entity.data.userId", "userId", "67890")]
    """
    if _depth >= max_depth:
        return []

    results = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key

            # 检查是否是 ID 字段
            if key in const.COMMON_ID_FIELDS:
                if isinstance(value, (str, int)) and value:
                    results.append((current_path, key, str(value)))

            # 递归进入子对象
            if isinstance(value, (dict, list)):
                results.extend(_extract_ids_recursive(value, current_path, max_depth, _depth + 1))

    elif isinstance(obj, list):
        # 只检查列表的第一个元素（避免重复）
        if len(obj) > 0:
            first = obj[0]
            if isinstance(first, (dict, list)):
                results.extend(_extract_ids_recursive(first, f"{path}[0]", max_depth, _depth + 1))

    return results



def _derive_state_rules(core_apis: dict, response_samples: dict) -> dict:
    """
    Step 5: 从响应样本中推导状态断言规则。

    跨 CRUD 类别对比状态字段的值变化规律：
    - create 后的 state 值 → "after_create"
    - lock 后的 state 值 → "after_lock"
    - unlock 后的 state 值 → "after_unlock"
    """
    state_field = None
    state_values = {}  # {crud_category: state_value}

    for category, endpoints in core_apis.items():
        for ep in endpoints:
            pn = ep["pathname"]
            samples = response_samples.get(pn, [])
            for s in samples[:1]:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
                except Exception as e:
                    LOG.debug(f"解析状态断言响应样本失败 ({pn}): {e}")
                    continue
                entity = _extract_entity_with_fallback(body)
                if isinstance(entity, dict):
                    vals = _find_state_value(entity)
                    if vals:
                        if state_field is None:
                            state_field = vals["field"]
                        if vals["field"] == state_field:
                            if category not in state_values:
                                state_values[category] = vals["value"]
                elif isinstance(entity, list) and len(entity) > 0:
                    # 实体是列表，检查第一个元素的状态
                    first = entity[0]
                    if isinstance(first, dict):
                        vals = _find_state_value(first)
                        if vals:
                            if state_field is None:
                                state_field = vals["field"]
                            if vals["field"] == state_field:
                                if category not in state_values:
                                    state_values[category] = vals["value"]

    # 组合状态规则
    rules = {
        "state_field": state_field,
        "values_by_crud": state_values,
    }

    # 如果有 create 和 lock 状态值，推导完整的断言逻辑
    if "create" in state_values:
        rules["after_create"] = state_values["create"]
    if "lock" in state_values:
        rules["after_lock"] = state_values["lock"]
    if "unlock" in state_values:
        rules["after_unlock"] = state_values["unlock"]
    if "delete" in core_apis:
        rules["after_delete"] = "NOT_EXIST"

    return rules


def _find_state_value(entity: dict) -> Optional[dict]:
    """在实体字典中递归查找状态字段。"""
    for fname in const.STATE_FIELD_NAMES:
        if fname in entity:
            val = entity[fname]
            if isinstance(val, str) and val:
                return {"field": fname, "value": val}
            if isinstance(val, bool):
                return {"field": fname, "value": val}
    return None


def _extract_entity_with_fallback(body: dict, fallback_keys: list = None) -> any:
    """从响应体中提取实体数据（带信封键回退）。

    优先使用发现的信封键，找不到时回退到 const.ENVELOPE_KEY_DEFAULTS。

    Args:
        body: 响应体 dict
        fallback_keys: 回退的信封键列表

    Returns:
        提取的实体数据，或空 dict/list
    """
    if not isinstance(body, dict):
        return {}

    # 优先使用发现的信封键
    keys_to_try = fallback_keys or const.ENVELOPE_KEY_DEFAULTS

    for key in keys_to_try:
        if key in body and body[key] is not None:
            return body[key]

    # 都找不到，返回空
    return {}


# ========== Manifest 构建（Phase 2: 泛化架构）==========

# 步骤中文标签映射
_STEP_LABELS = {
    "create": "创建", "query": "查询", "detail": "详情",
    "update": "修改", "lock": "锁定", "unlock": "解锁",
    "reset": "重置", "export": "导出", "execute": "其他操作",
    "authorize": "授权", "delete": "删除", "migrate": "迁移",
}


def _parse_body(ep_or_sample) -> dict | list:
    """从 endpoint dict 或 body sample 解析出 body dict 或 list。

    Args:
        ep_or_sample: endpoint dict（含 request_body_sample/bodies）或直接的 body sample

    Returns:
        解析后的 dict 或 list，失败返回 {}
    """
    sample = None
    if isinstance(ep_or_sample, dict):
        sample = ep_or_sample.get("request_body_sample") or (
            (ep_or_sample.get("bodies") or [None])[0])
    else:
        sample = ep_or_sample

    if not sample:
        return {}
    try:
        body = json.loads(sample) if isinstance(sample, str) else sample
        # 支持 dict 和 list
        if isinstance(body, (dict, list)):
            return body
        return {}
    except Exception:
        return {}


def _discover_envelope_keys(response_samples: dict) -> list:
    """从响应样本自动发现信封键。

    统计各候选键在实际响应中的出现频率，按频率排序返回。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        按出现频率排序的信封键列表
    """
    hit_count = {k: 0 for k in const.ENVELOPE_KEY_CANDIDATES}
    total = 0

    for pathname, samples in response_samples.items():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            total += 1
            for key in const.ENVELOPE_KEY_CANDIDATES:
                if key in body:
                    hit_count[key] += 1

    if total == 0:
        return const.ENVELOPE_KEY_DEFAULTS

    # 按命中数降序排列，只保留有命中的
    found = [(k, c) for k, c in hit_count.items() if c > 0]
    found.sort(key=lambda x: -x[1])

    if found:
        return [k for k, _ in found]
    return const.ENVELOPE_KEY_DEFAULTS


def _discover_success_check(response_samples: dict) -> dict:
    """从响应样本自动发现成功判断模式。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        success_check 配置 dict
    """
    has_success = 0
    has_error_code = 0
    total = 0

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            total += 1
            for f in const.SUCCESS_FIELD_CANDIDATES:
                if f in body:
                    has_success += 1
                    break
            for f in const.ERROR_FIELD_CANDIDATES:
                if f in body:
                    has_error_code += 1
                    break

    if total > 0 and has_success / total > 0.3:
        # 找到 success 字段 → field_and_absence 模式
        # 确定具体的 success_field 和 error_field
        success_field = "success"
        error_field = None
        for samples in response_samples.values():
            for s in samples[:1]:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
                except Exception:
                    continue
                if isinstance(body, dict):
                    for f in const.SUCCESS_FIELD_CANDIDATES:
                        if f in body:
                            success_field = f
                            break
                    for f in const.ERROR_FIELD_CANDIDATES:
                        if f in body:
                            error_field = f
                            break
                    break
            if success_field != "success" or error_field:
                break

        return {
            "type": "field_and_absence",
            "success_field": success_field,
            "error_field": error_field,
        }

    # 未找到明确的 success 字段 → 仅 HTTP 状态码
    return {"type": "http_only"}


def _discover_list_structure(response_samples: dict, envelope_keys: list) -> tuple:
    """从查询类响应中发现列表结构。

    Args:
        response_samples: 响应样本
        envelope_keys: 已发现的信封键

    Returns:
        (list_keys, total_keys) 元组
    """
    list_hits = {k: 0 for k in const.LIST_KEY_CANDIDATES}
    total_hits = {k: 0 for k in const.TOTAL_KEY_CANDIDATES}

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue

            # 在信封内查找列表结构
            entity = None
            for ek in envelope_keys:
                if ek in body:
                    entity = body[ek]
                    break
            if not isinstance(entity, dict):
                continue

            for k in const.LIST_KEY_CANDIDATES:
                if k in entity and isinstance(entity[k], list):
                    list_hits[k] += 1
            for k in const.TOTAL_KEY_CANDIDATES:
                if k in entity and isinstance(entity[k], (int, float)):
                    total_hits[k] += 1

    # 按命中数排序，保留有命中的
    found_list = [k for k, c in sorted(list_hits.items(), key=lambda x: -x[1]) if c > 0]
    found_total = [k for k, c in sorted(total_hits.items(), key=lambda x: -x[1]) if c > 0]

    return (
        found_list or const.LIST_KEY_DEFAULTS,
        found_total or const.TOTAL_KEY_DEFAULTS,
    )


def _discover_id_field(response_samples: dict, envelope_keys: list) -> str:
    """从创建类响应中发现 ID 字段名。

    Args:
        response_samples: 响应样本
        envelope_keys: 已发现的信封键

    Returns:
        ID 字段名（默认从 const.DEFAULT_ID_FIELD）
    """
    # 优先使用 COMMON_ID_FIELDS 中的顺序
    id_hits = {}

    for samples in response_samples.values():
        for s in samples[:3]:
            try:
                body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
            except Exception:
                continue
            if not isinstance(body, dict):
                continue

            # 在信封内查找 ID 字段
            entity = None
            for ek in envelope_keys:
                if ek in body:
                    entity = body[ek]
                    break
            if not isinstance(entity, dict):
                continue

            for fname in const.COMMON_ID_FIELDS:
                if fname in entity:
                    val = entity[fname]
                    if isinstance(val, (str, int)) and val:
                        id_hits[fname] = id_hits.get(fname, 0) + 1

    if id_hits:
        # 优先选 const.DEFAULT_ID_FIELD，其次选出现最多的
        if const.DEFAULT_ID_FIELD in id_hits:
            return const.DEFAULT_ID_FIELD
        return max(id_hits, key=id_hits.get)

    return const.DEFAULT_ID_FIELD


def _discover_response_contract(response_samples: dict) -> dict:
    """从响应样本自动发现完整的响应约定。

    Args:
        response_samples: {pathname: [{status, body}, ...]}

    Returns:
        response_contract dict
    """
    envelope_keys = _discover_envelope_keys(response_samples)
    success_check = _discover_success_check(response_samples)
    list_keys, total_keys = _discover_list_structure(response_samples, envelope_keys)
    id_field = _discover_id_field(response_samples, envelope_keys)

    return {
        "envelope_keys": envelope_keys,
        "success_check": success_check,
        "list_keys": list_keys,
        "total_keys": total_keys,
        "id_field": id_field,
    }


def _classify_body_fields(body_sample: dict, id_field_details: dict,
                          create_body_sample: dict = None,
                          context_field_names: set = None) -> dict:
    """为请求体中每个字段标注角色（role）。

    替代 gen_test.py 中 _gen_payload_code() 的硬编码字段分类。

    角色说明：
    - "id_ref":    ID 引用字段，运行时从 state['id'] 获取
    - "context":   上下文字段（tenantId 等），从 state 或 create_body 获取
    - "name":      名称字段，运行时添加 AT_{TS}_ 前缀
    - "mutable":   可变字段（description 等），运行时生成新值
    - "static":    静态字段，直接复制 body_template 的值

    Args:
        body_sample: 请求体样本 dict
        id_field_details: Stage 3 分析的 ID 字段详情
                          {field_name: {source_crud, sample_value, path}}
        create_body_sample: create 步骤的 body 样本（用于判断 context 字段）
        context_field_names: auth_profile.context_fields 中声明的字段名集合

    Returns:
        {field_name: {"role": ..., "source": ...}} 字典
    """
    if not body_sample:
        return {}

    context_field_names = context_field_names or set()

    id_field_names = set()
    if id_field_details:
        id_field_names = set(id_field_details.keys())
    id_field_names.add(const.DEFAULT_ID_FIELD)  # 始终包含默认 ID 字段

    # context 字段优先级高于 id_ref：在 context_fields 中声明的字段，
    # 即使名称出现在 id_field_details 中，也标记为 context
    effective_id_fields = id_field_names - context_field_names

    create_keys = set()
    if create_body_sample and isinstance(create_body_sample, dict):
        create_keys = set(create_body_sample.keys())

    roles = {}
    for key, value in body_sample.items():
        key_lower = key.lower()

        # 0. 上下文字段优先：在 auth_profile.context_fields 中声明
        if key in context_field_names:
            roles[key] = {"role": "context", "source": f"context.{key}"}
            continue

        # 1. ID 引用字段：在 id_field_details 中出现（排除 context 字段），或字段名匹配 ID 模式
        if key in effective_id_fields:
            roles[key] = {"role": "id_ref"}
            continue

        # 判断辅助标志
        is_name_like = any(kw in key_lower for kw in const.NAME_FIELD_KEYWORDS)
        is_mutable_like = any(kw in key_lower for kw in const.MUTABLE_FIELD_KEYWORDS)
        ends_with_id = (key_lower.endswith("id") and key_lower != "id")

        # 2. ID 引用字段（补充）：以 Id 结尾、不在 create body 中、非名称类
        #    例: roleId, policyId, userId（在 delete/update body 中出现但不在 create body 中）
        if ends_with_id and key not in create_keys and not is_name_like:
            roles[key] = {"role": "id_ref"}
            continue

        # 3. 上下文字段：以 Id 结尾且在 create body 中出现的字段
        #    例: tenantId, adminId（从创建时传递的上下文）
        if ends_with_id and key in create_keys and not is_name_like:
            roles[key] = {"role": "context", "source": f"create_body.{key}"}
            continue

        # 4. 名称字段：字段名含 name/title/label 且值是短可读字符串
        if is_name_like and isinstance(value, str) and 1 < len(value) < 50:
            roles[key] = {"role": "name"}
            continue

        # 4. 可变字段：description 类字段
        if is_mutable_like:
            roles[key] = {"role": "mutable"}
            continue

        # 5. 静态字段：其他所有
        roles[key] = {"role": "static"}

    # 降级分析：对 static 字段的长字符串做值模式分析
    # 识别加密值、邮箱、手机号等用户输入字段
    for key, role_info in roles.items():
        if role_info.get("role") == "static":
            value = body_sample[key]
            if isinstance(value, str) and len(value) > 20:
                pattern = _analyze_value_pattern(key, value)
                if pattern and pattern["role"] == "test_value":
                    roles[key] = {
                        "role": "test_value",
                        "value_pattern": pattern["value_pattern"],
                    }
                    LOG.debug(f"    _classify_body_fields: {key} 降级为 test_value/{pattern['value_pattern']}")

    return roles


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

    # 1. 发现响应约定
    response_contract = _discover_response_contract(response_samples)

    # 2. 构建 auth_profile
    auth_profile = _build_auth_profile(profile)

    # 3. 获取 create body 样本（用于字段分类）
    create_body_sample = None
    if "create" in core_apis:
        create_body_sample = _parse_body(core_apis["create"][0])

    # 4. 构建 steps 列表
    id_field_details = analysis.get("dependencies", {}).get("id_field_details", {})

    # 5. 获取 context_fields 字段名（用于优先标记 context 角色）
    context_fields = auth_profile.get("context_fields", {})
    context_field_names = set(context_fields.keys())

    # 获取可用的验证端点（query / detail）
    # 优先从 core_apis 获取；如果 query/detail 被同现过滤器误杀，
    # 则从 capture_result 原始数据中回退查找
    #
    # 关键校验：验证端点的响应样本必须包含 entity ID，否则 contains_id 断言无意义
    create_id_sample = None
    if create_body_sample and isinstance(create_body_sample, dict):
        # 从 create 响应样本中提取 ID（用于验证 verify endpoint 是否返回同类数据）
        create_eps = core_apis.get("create", [])
        if create_eps:
            create_resp_samples = response_samples.get(create_eps[0]["pathname"], [])
            for s in create_resp_samples:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else s["body"]
                    # 使用 response_contract 发现的信封键和 ID 字段，而非硬编码
                    envelope_keys = response_contract.get("envelope_keys", const.ENVELOPE_KEY_CANDIDATES)
                    id_field = response_contract.get("id_field", const.DEFAULT_ID_FIELD)
                    entity = body
                    for ek in envelope_keys:
                        if ek in body and isinstance(body[ek], dict):
                            entity = body[ek]
                            break
                    if isinstance(entity, dict) and id_field in entity:
                        create_id_sample = entity[id_field]
                        break
                except Exception:
                    pass

    def _endpoint_returns_entity_id(ep) -> bool:
        """检查端点的响应样本是否包含 create 提取的 entity ID。"""
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
    # 使用重新分类的数据，而不是原始捕获数据
    reclassified = analysis.get("reclassified_apis", {})
    raw_classified = reclassified if reclassified else capture_result.get("by_category", {})

    for verify_action in ["query", "detail"]:
        eps = core_apis.get(verify_action, [])
        selected = False
        for ep in eps:
            if _endpoint_returns_entity_id(ep):
                verify_endpoints[verify_action] = {
                    "ep": ep,
                    "body_sample": _parse_body(ep),
                }
                selected = True
                break
        if selected:
            continue
        # 回退 1：从重新分类的数据中查找
        raw_eps = raw_classified.get(verify_action, [])
        # 过滤掉基础设施 API（菜单、主题等）
        for ep in raw_eps:
            if ep["pathname"] in infra_apis:
                continue
            if _endpoint_returns_entity_id(ep):
                verify_endpoints[verify_action] = {
                    "ep": ep,
                    "body_sample": _parse_body(ep),
                }
                LOG.info(f"  验证端点回退1: {verify_action} ← {ep['pathname'][:60]}")
                selected = True
                break
        if selected:
            continue
        # 回退 2：从 other_get/other_post 中搜索列表查询端点
        for fallback_cat in ("other_get", "other_post"):
            fallback_eps = raw_classified.get(fallback_cat, [])
            for ep in fallback_eps:
                # 过滤掉基础设施 API（菜单、主题等）
                if ep["pathname"] in infra_apis:
                    continue
                if _endpoint_returns_entity_id(ep):
                    verify_endpoints[verify_action] = {
                        "ep": ep,
                        "body_sample": _parse_body(ep),
                    }
                    LOG.info(f"  验证端点回退2: {verify_action} ← "
                             f"{ep['pathname'][:60]} (from {fallback_cat})")
                    selected = True
                    break
            if selected:
                break
        if verify_action not in verify_endpoints and eps:
            LOG.warning(f"  验证端点 {verify_action} 的响应不含 entity ID，跳过自动验证")

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
        query_params_samples = query_ep.get("query_params_samples", [])
        search_value = None
        for sample in query_params_samples:
            if isinstance(sample, dict) and search_param in sample:
                search_value = sample[search_param]
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
    if pre_api_chain and pre_api_chain.get("pre_apis"):
        field_resolutions = pre_api_chain.get("field_resolutions", {})

    def _get_create_pre_api_ref_source(field_name):
        """检查 create 步骤中指定字段是否有 pre_api_ref 解析，返回其 source 或 None。

        用于 verify 步骤的 query_params 生成：如果 create body 中某字段使用了
        pre_api_ref（如 tenantId ← display_by_role.entity_0_id），verify 步骤
        的 query_params 也应引用相同来源，确保 tenantId 一致。
        """
        key = f"create.{field_name}"
        if key in field_resolutions:
            return field_resolutions[key].get("source", "")
        return None

    def _make_step(action, ep, body_sample, assertion=None):
        """构建单个步骤 dict。"""
        # 处理数组格式请求体（如 batch delete ["id1", "id2"]）
        if isinstance(body_sample, list):
            field_roles = {
                "__array_items__": {"role": "id_ref", "source": "create.id"}
            }
        else:
            field_roles = _classify_body_fields(
                body_sample, id_field_details, create_body_sample, context_field_names
            )

        extract = None
        if action == "create":
            envelope_keys = response_contract["envelope_keys"]
            id_field = response_contract["id_field"]
            extract = {
                "id": f"{envelope_keys[0]}.{id_field}" if envelope_keys else id_field,
                "names": [k for k, v in field_roles.items() if v.get("role") == "name"],
            }

        pathname = re.sub(r"[0-9a-f]{20,}", "{id}", ep["pathname"])

        step = {
            "action": action,
            "label": (ui_result or {}).get("button_labels", {}).get(action)
                     or _STEP_LABELS.get(action, action),
            "api": {
                "method": ep["method"],
                "pathname": pathname,
                "query_params": ep.get("query_params", {}),
            },
            "body_template": body_sample if isinstance(body_sample, list) else (body_sample or {}),
            "body_field_roles": field_roles,
            "requires": ["id"] if action != "create" else [],
        }
        if extract:
            step["extract"] = extract
        if assertion:
            step["assertion"] = assertion
        return step

    def _make_verify_step(verify_action, label, assertion):
        """构建验证步骤（基于可用的验证端点）。"""
        if verify_action not in verify_endpoints:
            return None

        ep = verify_endpoints[verify_action]["ep"]
        body_sample = verify_endpoints[verify_action]["body_sample"]

        field_roles = _classify_body_fields(
            body_sample, id_field_details, create_body_sample, context_field_names
        )
        pathname = re.sub(r"[0-9a-f]{20,}", "{id}", ep["pathname"])

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
                "pathname": pathname,
                "query_params": query_params if query_params else ep.get("query_params", {}),
            },
            "body_template": body_sample or {},
            "body_field_roles": field_roles,
            "requires": ["id"],
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
                       or _STEP_LABELS.get(action, action)

        # 写操作后验证：有 query/detail 端点才插入
        if action in const.WRITE_OPERATIONS:
            # 优先用 query（列表验证），其次 detail（详情验证）
            if "query" in verify_endpoints:
                query_ep = verify_endpoints["query"]["ep"]
                search_param = query_ep.get("search_param", "")

                # 有 search_param → 使用 search_verify/search_not_found
                if search_param:
                    if action == "delete":
                        plans.append(("query", f"搜索验证（删除后）", "search_not_found"))
                    else:
                        plans.append(("query", f"搜索验证（{action_label}后）", "search_verify"))
                # 无 search_param → 使用 contains_id/not_contains_id（旧逻辑）
                elif action == "delete":
                    plans.append(("query", f"查询验证（删除后）", "not_contains_id"))
                elif action == "create":
                    plans.append(("query", f"查询验证（创建后）", "contains_id"))
                else:
                    plans.append(("query", f"查询验证（{action_label}后）", "contains_id"))
            elif "detail" in verify_endpoints:
                if action in ("update", "lock", "unlock"):
                    plans.append(("detail", f"详情验证（{action_label}后）", "field_changed"))

        return plans

    for action in crud_order:
        eps = core_apis.get(action, [])
        if not eps:
            continue

        ep = eps[0]
        body_sample = _parse_body(ep)

        # 主步骤
        steps.append(_make_step(action, ep, body_sample))

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
            for key, resolution in field_resolutions.items():
                # key 格式: "action.field"
                step_action, field_name = key.split('.', 1)
                source = resolution.get("source", "")
                if source.startswith(api_info["id"] + "."):
                    extract_name = source.split(".", 1)[1]
                    # 查找对应的 extracted_fields 获取 path
                    for ef in api_info.get("extracted_fields", []):
                        if ef["name"] == extract_name:
                            # 避免重复
                            if not any(e["name"] == extract_name for e in pre_api_entry["extracts"]):
                                # 收集 used_by 信息
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

        # 应用 field_resolutions 到 steps 的 body_field_roles
        for step in steps:
            action = step.get("action", "")
            field_roles = step.get("body_field_roles", {})
            for field_name in list(field_roles.keys()):
                key = f"{action}.{field_name}"
                if key in field_resolutions:
                    # 保护 name/mutable 字段不被 pre_api_ref 覆盖
                    # 这些是业务测试值，运行时应自动生成唯一值
                    existing_role = field_roles[field_name].get("role", "")
                    if existing_role in ("name", "mutable"):
                        continue
                    resolution = field_resolutions[key]
                    strategy = resolution.get("strategy", "")

                    if strategy == "value_pattern_analysis":
                        # 值模式分析结果：标记为 test_value
                        field_roles[field_name] = {
                            "role": "test_value",
                            "value_pattern": resolution["value_pattern"],
                        }
                    else:
                        # pre_api_ref 来源
                        field_roles[field_name] = {
                            "role": "pre_api_ref",
                            "source": resolution["source"],
                        }
                        if resolution.get("is_array"):
                            field_roles[field_name]["is_array"] = True

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


# ========== Phase B: 前置 API 依赖链追踪 ==========

def trace_pre_api_dependencies(core_apis: dict, response_samples: dict,
                               pre_api_candidates: list, context_fields: dict,
                               id_field_details: dict) -> dict:
    """
    递归追踪参数依赖链，生成前置 API 列表。

    分析业务 API 的请求体参数，识别哪些参数应从前置 API 动态获取。
    使用三策略匹配算法：精确值匹配 → 字段名启发式 → 列表成员检查。

    Args:
        core_apis: 核心 CRUD API 字典 {category: [endpoints]}
        response_samples: 响应样本字典
        pre_api_candidates: 前置 API 候选列表（来自 Phase A）
        context_fields: 上下文字段配置（预留参数）
        id_field_details: ID 字段详情

    Returns:
        {
            'pre_apis': [...],  # 排序后的前置 API 列表
            'field_resolutions': {...}  # 字段解析映射
        }
    """
    LOG.info(f"[Phase B] 开始前置 API 依赖链追踪...")

    # Phase 1: 构建参数清单（从业务 API 的请求体中提取）
    param_inventory = []
    for category, endpoints in core_apis.items():
        if category not in ('create', 'update', 'delete'):
            continue
        for ep in endpoints:
            body_sample = _parse_body(ep)
            if not body_sample or not isinstance(body_sample, dict):
                continue
            for field_name, field_value in body_sample.items():
                # 追踪标量值和列表值
                if isinstance(field_value, list):
                    # 列表值：保留整个列表，_resolve_parameter_source 会处理
                    if field_value:  # 非空列表
                        param_inventory.append({
                            'step_action': category,
                            'field_name': field_name,
                            'field_value': field_value,
                            'pathname': ep.get('pathname', '')
                        })
                elif isinstance(field_value, (str, int, float, bool)):
                    param_inventory.append({
                        'step_action': category,
                        'field_name': field_name,
                        'field_value': field_value,
                        'pathname': ep.get('pathname', '')
                    })

    LOG.info(f"  Phase 1: 收集到 {len(param_inventory)} 个参数")

    # Phase 2: 构建前置 API 索引（扁平化所有前置 API 响应）
    pre_api_index = {}  # {value_str: (api, field_info)}
    for api in pre_api_candidates:
        body_text = api.get('response_sample', {}).get('response_body', '')
        if not body_text:
            continue
        try:
            body = json.loads(body_text) if isinstance(body_text, str) else body_text
        except Exception:
            continue

        # 提取所有可提取字段
        for field_info in api.get('extracted_fields', []):
            path = field_info.get('path', '')
            value = _extract_by_path_generic(body, path)
            if value is not None:
                # 将值转换为字符串作为索引键
                key = str(value)
                if key not in pre_api_index:
                    pre_api_index[key] = {
                        'api': api,
                        'field': field_info,
                        'value': value
                    }

    LOG.info(f"  Phase 2: 前置 API 索引包含 {len(pre_api_index)} 个值")

    # Phase 3: 三策略匹配 + 值模式分析降级
    field_resolutions = {}
    used_apis = {}  # {api_id: api_info}

    for param in param_inventory:
        resolution = _resolve_parameter_source(param, pre_api_index)
        if resolution:
            # 使用字符串键 "action.field" 而非元组，以便 JSON 序列化
            key = f"{param['step_action']}.{param['field_name']}"
            field_resolutions[key] = resolution
            # 记录使用的前置 API
            api_id = resolution['source'].split('.')[0]
            if api_id not in used_apis:
                used_apis[api_id] = resolution['api']
        else:
            # ★ 值模式分析降级（仅对 create 步骤）
            # 当三策略匹配都返回 None 时，根据值的特征判断是否为用户输入字段
            if param['step_action'] == 'create':
                pattern = _analyze_value_pattern(param['field_name'], param['field_value'])
                if pattern and pattern['role'] == 'test_value':
                    key = f"{param['step_action']}.{param['field_name']}"
                    field_resolutions[key] = {
                        'source': '',
                        'strategy': 'value_pattern_analysis',
                        'value_pattern': pattern['value_pattern'],
                    }
                    LOG.debug(f"    值模式降级: {param['field_name']} → "
                              f"test_value/{pattern['value_pattern']}")

    LOG.info(f"  Phase 3: 解析了 {len(field_resolutions)} 个字段来源")

    # Phase 4: 拓扑排序（处理前置 API 之间的依赖）
    pre_apis = _topological_sort_pre_apis(used_apis)

    # Phase 5: 添加 context_fields 提取（确保 query_params 引用的字段被提取）
    if context_fields:
        for field_name, field_config in context_fields.items():
            path = field_config.get('path', '')
            if not path:
                continue

            # 检查是否已有字段提取
            already_extracted = False
            for pre_api in pre_apis:
                for extract in pre_api.get('extracts', []):
                    if extract.get('name') == field_name or extract.get('path') == path:
                        already_extracted = True
                        break
                if already_extracted:
                    break

            if already_extracted:
                continue

            # 找到能提供此字段的 pre_api（根据 path 前缀匹配）
            path_prefix = path.split('.')[0]  # 例如 "entity"
            target_api = None
            for pre_api in pre_apis:
                # 检查该 pre_api 的响应是否包含此路径前缀
                body_text = pre_api.get('response_sample', {}).get('response_body', '')
                if body_text:
                    try:
                        body = json.loads(body_text) if isinstance(body_text, str) else body_text
                        if path_prefix in body:
                            target_api = pre_api
                            break
                    except Exception:
                        continue

            if target_api:
                # 添加提取字段
                if 'extracts' not in target_api:
                    target_api['extracts'] = []
                target_api['extracts'].append({
                    'name': field_name,
                    'path': path
                })
                LOG.info(f"  Phase 5: 添加 context_field 提取: {field_name} -> {path} (from {target_api.get('id')})")

    return {
        'pre_apis': pre_apis,
        'field_resolutions': field_resolutions
    }


def _analyze_value_pattern(field_name: str, value) -> dict:
    """根据值的特征推断字段角色（方案 B 降级：值模式分析）。

    主要数据驱动（看值本身的特征），对加密/不可读值用字段名辅助判断。
    用于区分「系统引用字段」和「用户输入字段」。

    Args:
        field_name: 字段名（加密值时用作辅助信号）
        value: 字段值

    Returns:
        {"role": "test_value"|"static", "value_pattern": "..."} 或 None
    """
    if not isinstance(value, str) or not value:
        return None

    fn_lower = field_name.lower()

    # 1. 长十六进制串（≥32 字符）→ hash/加密值，不可重放
    if re.fullmatch(r'[0-9a-fA-F]{32,}', value):
        return {"role": "test_value", "value_pattern": "hex_hash"}

    # 2. 长 Base64 串（≥50 字符，含 +/=）→ 加密值，不可重放
    #    对加密值用字段名辅助判断应生成什么类型的测试值
    if len(value) >= 50 and re.search(r'[+/=]', value) and re.fullmatch(r'[A-Za-z0-9+/=]+', value):
        # 字段名含敏感信息（phone/email/password）→ 标记为 static，复用原始加密值
        # 原因：服务端要求加密后的值，我们无法生成有效的加密值（不知道公钥）
        if any(kw in fn_lower for kw in ('phone', 'mobile', 'cell', 'email', 'mail', 'password', 'passwd', 'pwd')):
            return {"role": "static", "value_pattern": "encrypted_sensitive"}
        return {"role": "test_value", "value_pattern": "base64_encrypted"}

    # 3. 短可读字符串 — 进一步分析子模式
    if len(value) < 80:
        # 3a. 含 @ → 邮箱
        if '@' in value and '.' in value.split('@')[-1]:
            return {"role": "test_value", "value_pattern": "email"}

        # 3b. 纯数字且长度 8-15 → 手机号
        if re.fullmatch(r'\+?\d{8,15}', value):
            return {"role": "test_value", "value_pattern": "phone"}

        # 3c. 短可读字符串（含字母，长度 2-50）→ 名称/文本
        if 2 <= len(value) <= 50 and re.search(r'[a-zA-Z一-鿿]', value):
            return {"role": "test_value", "value_pattern": "text"}

    # 4. 短固定格式值（如 "+86", "1", "ACTIVE"）→ 静态
    if len(value) <= 10:
        return {"role": "static"}

    return None


def _resolve_parameter_source(param: dict, pre_api_index: dict) -> dict:
    """
    使用三策略匹配解析参数来源。

    策略 1: 精确值匹配
    策略 2: 字段名启发式 + 值比对门控
    策略 3: 列表成员检查

    Args:
        param: 参数信息 {step_action, field_name, field_value}
        pre_api_index: 前置 API 索引 {value_str: {api, field, value}}

    Returns:
        解析结果 {source, api, strategy} 或 None
    """
    field_name = param['field_name']
    field_value = param['field_value']

    # 处理列表值：如果值是一个列表，检查第一个元素
    is_array = False
    if isinstance(field_value, list):
        if not field_value:
            return None
        field_value = field_value[0]
        is_array = True

    field_value_str = str(field_value)

    # ── 纯值驱动匹配：不再用字段名后缀做门卫 ──
    # 值本身就是最好的标识，匹配上了自然就知道来源，不需要关心字段叫什么

    # 策略 1: 精确值匹配（最高优先级，直接按值搜索，不做字段名过滤）
    if field_value_str in pre_api_index:
        info = pre_api_index[field_value_str]
        api_id = info['api']['id']
        field_name_in_api = info['field']['name']
        return {
            'source': f"{api_id}.{field_name_in_api}",
            'api': info['api'],
            'strategy': 'exact_value_match',
            'is_array': is_array
        }

    # 策略 2: 字段名启发式 + 值比对门控（降级方案，仅当值匹配失败时尝试）
    # 字段名完全匹配（取路径最后一段），不做子串匹配
    for value_str, info in pre_api_index.items():
        api_field_name = info['field']['name']
        api_field_leaf = api_field_name.rsplit('_', 1)[-1].lower()
        if field_name.lower() == api_field_leaf or field_name.lower() == api_field_name.lower():
            # ★ 新增：字段名匹配时，检查值是否一致
            if value_str == field_value_str:
                # 值一致 → 系统引用
                api_id = info['api']['id']
                return {
                    'source': f"{api_id}.{api_field_name}",
                    'api': info['api'],
                    'strategy': 'field_name_and_value_match',
                    'is_array': False
                }
            else:
                # 字段名匹配但值不同 → 用户输入字段，不是系统引用
                LOG.debug(f"    字段 {field_name}: 名称匹配但值不同 "
                          f"(请求值={field_value_str[:30]}..., "
                          f"响应值={value_str[:30]}...) → 判定为用户输入")
                return None  # 不返回，交给值模式分析

    # 策略 3: 列表成员检查（参数值是否在某个列表响应中）
    # 检查前置 API 索引中的列表字段
    for value_str, info in pre_api_index.items():
        field_path = info['field']['path']
        # 检查是否为列表字段（路径包含 [0]）
        if '[0]' in field_path:
            # 提取列表路径（去掉 [0] 部分）
            list_path = field_path.split('[0]')[0]
            api = info['api']
            body_text = api.get('response_sample', {}).get('response_body', '')
            if not body_text:
                continue
            try:
                body = json.loads(body_text) if isinstance(body_text, str) else body_text
                list_value = _extract_by_path_generic(body, list_path)
                if isinstance(list_value, list):
                    # 检查参数值是否在列表中
                    for item in list_value:
                        if isinstance(item, dict):
                            item_id = item.get('id')
                            if str(item_id) == field_value_str:
                                api_id = api['id']
                                field_name_in_api = info['field']['name']
                                return {
                                    'source': f"{api_id}.{field_name_in_api}",
                                    'api': api,
                                    'strategy': 'list_membership',
                                    'is_array': True
                                }
            except Exception:
                continue

    return None


def _topological_sort_pre_apis(used_apis: dict) -> list:
    """
    对前置 API 进行拓扑排序。

    Args:
        used_apis: {api_id: api_info} 字典

    Returns:
        排序后的前置 API 列表
    """
    # 构建依赖图
    graph = {}
    for api_id, api_info in used_apis.items():
        graph[api_id] = api_info.get('depends_on', [])

    # 拓扑排序（Kahn 算法）
    in_degree = {node: 0 for node in graph}
    for node in graph:
        for dep in graph[node]:
            if dep in in_degree:
                in_degree[dep] += 1

    queue = [node for node in in_degree if in_degree[node] == 0]
    sorted_apis = []

    while queue:
        node = queue.pop(0)
        sorted_apis.append(used_apis[node])

        for dep in graph.get(node, []):
            if dep in in_degree:
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    queue.append(dep)

    return sorted_apis


def _extract_by_path_generic(obj: dict, path: str):
    """
    通用的路径提取函数（支持数组索引）。

    Args:
        obj: JSON 对象
        path: 路径字符串，例如 "entity.list[0].id"

    Returns:
        提取的值，或 None
    """
    if not path or obj is None:
        return None

    # 分割路径段
    parts = []
    current = ''
    for char in path:
        if char in '.[':
            if current:
                parts.append(current)
                current = ''
            if char == '[':
                parts.append('[')
        elif char == ']':
            if current:
                parts.append(current)
                current = ''
            parts.append(']')
        else:
            current += char
    if current:
        parts.append(current)

    # 遍历提取
    value = obj
    i = 0
    while i < len(parts):
        part = parts[i]

        if part == '[':
            # 下一个部分是索引
            i += 1
            if i < len(parts):
                try:
                    index = int(parts[i])
                    value = value[index]
                except (ValueError, IndexError, TypeError):
                    return None
            i += 1
            # 跳过 ']'
            if i < len(parts) and parts[i] == ']':
                i += 1
        else:
            # 普通字段访问
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return None
            i += 1

        if value is None:
            return None

    return value
