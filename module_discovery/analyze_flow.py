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


def _filter_by_cooccurrence(all_endpoints: list, threshold: float = 0.6) -> set:
    """
    同现频率过滤：统计每个 API 出现在多少个不同按钮上下文中。

    如果一个 API 在 60%+ 的按钮点击后都出现，说明它是辅助 API（菜单加载、字典、通知等）。

    Args:
        all_endpoints: 所有去重端点列表，每个包含 contexts 字段
        threshold: 同现比例阈值，默认 0.6

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
                LOG.debug(f"同现过滤: {pathname} 出现在 {len(contexts)}/{total_buttons} ({ratio:.2f}) 个按钮上下文")
                supporting_apis.add(pathname)

    return supporting_apis


def analyze(classified_apis: dict, all_endpoints: list,
            response_samples: dict, ui_result: dict) -> dict:
    """
    完整分析流程入口。
    :param classified_apis:   capture_apis 归类结果（by_category）
    :param all_endpoints:     所有去重端点列表
    :param response_samples:  响应样本 {pathname: [{status, body}, ...]}
    :param ui_result:         discover_ui 的输出
    :return: FlowAnalysis
    """
    LOG.info("开始逻辑分析...")

    # Step 0: 同现频率过滤（识别在多个按钮上下文中都出现的辅助 API）
    cooccurrence_support = _filter_by_cooccurrence(all_endpoints, threshold=0.6)
    if cooccurrence_support:
        LOG.info(f"  Step 0: 同现频率过滤: {len(cooccurrence_support)} 个 API 被标记为辅助")

    # Step 1: 过滤辅助 API，找核心 CRUD API
    core_apis = _filter_core_apis(classified_apis, response_samples, cooccurrence_support)
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

    return {
        "crud_order": execution_order,
        "core_apis": core_apis,
        "api_by_button": api_by_button,
        "dependencies": dep_chain,
        "state_assertions": state_rules,
    }


def _filter_core_apis(classified: dict, response_samples: dict,
                      cooccurrence_support: set = None) -> dict:
    """
    Step 1: 过滤辅助 API。

    辅助 API 判断标准：
    - URL 包含菜单/主题/字典/通知等关键词
    - 在所有按钮点击后都会出现（同现频率高）→ cooccurrence_support
    - GET 类查询且路径不含核心资源名
    """
    core = {}
    supporting_patterns = const.SUPPORTING_API_KEYWORDS
    cooccurrence_support = cooccurrence_support or set()

    for category, endpoints in classified.items():
        if category == "support":
            continue
        # 过滤路径含辅助关键词的
        real_eps = []
        for ep in endpoints:
            p = ep["pathname"].lower()
            if any(kw in p for kw in supporting_patterns):
                continue
            # 同现频率过滤：在 60%+ 的按钮上下文中都出现的 API 是辅助 API
            if ep["pathname"] in cooccurrence_support:
                LOG.debug(f"  同现过滤跳过: {ep['pathname']}")
                continue
            real_eps.append(ep)

        if real_eps:
            core[category] = real_eps

    # 如果 "other_post" 和 "other_get" 中有真正的业务 API，
    # 尝试通过响应体判断（列表→query；含单条 id→execute）
    for cat in ("other_post", "other_get"):
        if cat not in core:
            continue
        real_eps = []
        target_cat = None
        for ep in core[cat]:
            pn = ep["pathname"]
            samples = response_samples.get(pn, [])
            if not samples:
                continue
            for s in samples:
                try:
                    body = json.loads(s["body"]) if isinstance(s["body"], str) else {}
                except Exception as e:
                    LOG.debug(f"解析响应样本 JSON 失败 ({pn}): {e}")
                    continue
                entity = _extract_entity_with_fallback(body)
                if isinstance(entity, dict):
                    # 查找列表键（泛化：不再硬编码 "list"）
                    for lk in _LIST_KEY_CANDIDATES:
                        if lk in entity and isinstance(entity[lk], list):
                            target_cat = "query"
                            real_eps.append(ep)
                            break
                    else:
                        # 查找 ID 键
                        if "id" in entity:
                            real_eps.append(ep)
                        elif isinstance(entity, list) and len(entity) > 0:
                            target_cat = "query"
                            real_eps.append(ep)
        if real_eps:
            dest = target_cat or "execute"
            core[dest] = core.get(dest, []) + real_eps
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
    Step 3: 根据核心 API 类别推导执行顺序（增强版：时序验证 + 依赖约束）。

    策略：
    1. 静态优先级（CRUD_EXECUTION_ORDER）作为基础顺序
    2. 时序验证（interceptor 时间戳）调整顺序
    3. 依赖约束（拓扑排序）确保前置步骤先执行

    Returns:
        排序后的 CRUD 类别列表
    """
    available = set(core_apis.keys())

    # 1. 静态优先级（基础顺序）
    base_order = [step for step in const.CRUD_EXECUTION_ORDER if step in available]

    if not base_order:
        return []

    # 2. 时序验证（从 all_endpoints 提取时间戳）
    if all_endpoints:
        temporal_order = _derive_temporal_order(core_apis, all_endpoints)
        if temporal_order:
            LOG.debug(f"  时序推导: {temporal_order}")
            # 合并策略：静态优先级为主，时序为辅（仅调整相邻步骤）
            base_order = _merge_orders(base_order, temporal_order)

    # 3. 依赖约束（拓扑排序）
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
    """
    first_seen = {}

    for category, endpoints in core_apis.items():
        for ep in endpoints:
            # 从 contexts 中提取时间戳（格式: "click:按钮名:timestamp"）
            for ctx in ep.get("contexts", []):
                parts = ctx.split(":")
                if len(parts) >= 3:
                    try:
                        ts = float(parts[-1])
                        if category not in first_seen or ts < first_seen[category]:
                            first_seen[category] = ts
                    except (ValueError, TypeError):
                        continue

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

    规则：
    - query/detail/update/lock/unlock/delete 依赖 create（需要 id）
    - unlock 依赖 lock（必须先锁定才能解锁）
    - delete 依赖 create（必须先创建才能删除）
    """
    graph = {cat: set() for cat in core_apis}

    # 通用规则：大部分操作依赖 create
    if "create" in core_apis:
        for cat in ("query", "detail", "update", "lock", "unlock", "delete"):
            if cat in graph:
                graph[cat].add("create")

    # 特殊规则：unlock 依赖 lock
    if "unlock" in graph and "lock" in core_apis:
        graph["unlock"].add("lock")

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
    for category, endpoints in core_apis.items():
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
                req_body = json.loads(body_sample) if isinstance(body_sample, str) else {}
            except Exception as e:
                LOG.debug(f"解析请求体 JSON 失败: {e}")
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


def _parse_body(ep_or_sample) -> dict:
    """从 endpoint dict 或 body sample 解析出 body dict。

    Args:
        ep_or_sample: endpoint dict（含 request_body_sample/bodies）或直接的 body sample

    Returns:
        解析后的 dict，失败返回 {}
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
        return body if isinstance(body, dict) else {}
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
                          create_body_sample: dict = None) -> dict:
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

    Returns:
        {field_name: {"role": ..., "source": ...}} 字典
    """
    if not body_sample:
        return {}

    id_field_names = set()
    if id_field_details:
        id_field_names = set(id_field_details.keys())
    id_field_names.add(const.DEFAULT_ID_FIELD)  # 始终包含默认 ID 字段

    create_keys = set()
    if create_body_sample and isinstance(create_body_sample, dict):
        create_keys = set(create_body_sample.keys())

    roles = {}
    for key, value in body_sample.items():
        key_lower = key.lower()

        # 1. ID 引用字段：在 id_field_details 中出现，或字段名匹配 ID 模式
        if key in id_field_names:
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

    # 获取 probe_url 和 context_fields
    probe_url = profile.get("probe_url", auth_cfg.get("probe_url", ""))
    context_fields = profile.get("context_fields", {})

    # 为 estack 类项目设置默认值（如果未显式配置）
    base_url = profile.get("base_url", "")
    api_base = profile.get("api_base", "")
    is_estack_like = "estack" in base_url.lower() or "estack" in api_base.lower()

    if is_estack_like:
        if not probe_url:
            probe_url = "/estack/api/estack/draco/v1/users/current-user"
        if not context_fields:
            context_fields = {
                "tenantId": {"path": "entity.tenantId"},
                "adminId": {"path": "entity.id"}
            }

    return {
        "header_name": auth_cfg.get("header_name", "Authorization"),
        "header_prefix": auth_cfg.get("header_prefix", "Bearer "),
        "freshness_ttl_seconds": auth_cfg.get("freshness_ttl_seconds", 300),
        "fixed_headers": auth_cfg.get("fixed_headers", {}),
        "probe_url": probe_url,
        "context_fields": context_fields,
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
                   ui_result: dict = None) -> dict:
    """构建完整测试清单（manifest）。

    将 Stage 3 分析结果 + 响应约定发现 + 项目配置 → 结构化的 manifest dict。

    Args:
        analysis: Stage 3 analyze() 的输出
        capture_result: Stage 2 捕获结果
        profile: 项目配置 dict
        module_name: 模块名称
        target_url: 目标页面 URL
        ui_result: Stage 1 UI 探测结果（可选，用于动态标签提取）

    Returns:
        完整的 manifest dict
    """
    response_samples = capture_result.get("response_samples", {})
    core_apis = analysis.get("core_apis", {})
    crud_order = analysis.get("crud_order", [])

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

    # 获取可用的验证端点（query / detail）
    # 优先从 core_apis 获取；如果 query/detail 被同现过滤器误杀，
    # 则从 capture_result 原始数据中回退查找
    verify_endpoints = {}
    for verify_action in ["query", "detail"]:
        eps = core_apis.get(verify_action, [])
        if eps:
            ep = eps[0]
            verify_endpoints[verify_action] = {
                "ep": ep,
                "body_sample": _parse_body(ep),
            }
        else:
            # 回退：从 capture_result 原始分类中查找
            raw_classified = capture_result.get("by_category", {})
            raw_eps = raw_classified.get(verify_action, [])
            # 过滤掉明显的辅助 API（菜单、主题等）
            for ep in raw_eps:
                p = ep["pathname"].lower()
                if any(kw in p for kw in const.SUPPORTING_API_KEYWORDS):
                    continue
                verify_endpoints[verify_action] = {
                    "ep": ep,
                    "body_sample": _parse_body(ep),
                }
                LOG.info(f"  验证端点回退: {verify_action} ← {ep['pathname'][:60]}")
                break

    steps = []

    def _make_step(action, ep, body_sample, assertion=None):
        """构建单个步骤 dict。"""
        field_roles = _classify_body_fields(
            body_sample, id_field_details, create_body_sample
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
            "body_template": body_sample,
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
            body_sample, id_field_details, create_body_sample
        )
        pathname = re.sub(r"[0-9a-f]{20,}", "{id}", ep["pathname"])

        step = {
            "action": ep["method"].lower(),
            "label": label,
            "api": {
                "method": ep["method"],
                "pathname": pathname,
                "query_params": ep.get("query_params", {}),
            },
            "body_template": body_sample or {},
            "body_field_roles": field_roles,
            "requires": ["id"],
            "assertion": assertion,
        }
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
        if action in ("create", "update", "delete", "lock", "unlock", "reset"):
            # 优先用 query（列表验证），其次 detail（详情验证）
            if "query" in verify_endpoints:
                if action == "delete":
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

    # 5. 组装 manifest
    manifest = {
        "manifest_version": "1.0",
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

    LOG.info(f"  Manifest 构建完成: {len(steps)} 个步骤, "
             f"信封键={response_contract['envelope_keys'][:3]}, "
             f"ID字段={response_contract['id_field']}")

    return manifest
