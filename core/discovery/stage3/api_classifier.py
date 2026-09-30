"""
api_classifier.py — API 分类与基础设施 API 识别

负责：
  - 基础设施 API 识别（_identify_infrastructure_apis）
  - 列表查询判断（_is_list_query, _is_post_list_query）
  - 核心 API 构建与选取（_build_core_apis_from_core_api_map, _select_core_api）
  - 按钮-API 映射（_map_buttons_to_apis）
  - 执行顺序推导（_derive_order）
  - 写操作查找（_find_last_write_op）
"""

import json
import logging
from collections import defaultdict

from core.discovery import const

LOG = logging.getLogger("api_classifier")


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

    # 降级策略：操作总数 ≤ 2 时，只排除已知的全局基础设施路径（从 KB 读取）
    from core.discovery.kb_loader import get_kb
    kb = get_kb()
    generic_infra = kb.get_infrastructure_patterns()
    if not generic_infra:
        # 兜底：最通用的基础设施模式（不依赖特定项目）
        generic_infra = ["/current-user", "/access-log", "/system-theme"]
    if total <= 2:
        for path in path_contexts:
            if any(pattern in path for pattern in generic_infra):
                infra.add(path)
        LOG.debug(f"  基础设施 API 降级识别（total={total}）: {infra}")
        return infra

    for path, contexts in path_contexts.items():
        ratio = len(contexts) / total
        if ratio >= threshold:
            infra.add(path)
            LOG.debug(f"  基础设施 API: {path} 出现在 {len(contexts)}/{total} ({ratio:.2f}) 个操作中")

    return infra


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

            # Case 1: entity 是列表（如 display-unit-tree）
            if isinstance(entity, list) and len(entity) > 0:
                return True

            # Case 2: entity.list + entity.total（如 tenants/users）
            if isinstance(entity, dict):
                has_list = any(k in entity for k in const.LIST_KEY_CANDIDATES)
                has_total = any(k in entity for k in const.TOTAL_KEY_CANDIDATES)
                if has_list and has_total:
                    return True
        except Exception:
            pass
    return False


def _is_post_list_query(ep) -> bool:
    """判断 POST 端点是否为列表查询（基于请求体特征，而非响应结构）。

    解决的问题：response_samples 按 pathname 存储，当 POST 写操作和 GET 列表查询
    共享同一 URL 时（如 POST /groups 和 GET /groups），_is_list_query 会因响应混合
    而误判 POST 写操作为列表查询。

    本函数通过请求体特征判断，不依赖 response_samples：
    1. pathname 以查询后缀结尾 → 列表查询
    2. 请求体含分页字段 → 列表查询
    3. 请求体字段数 ≤ 2 且无业务实体字段 → 可能是简单查询
    4. 其他 → 写操作

    Args:
        ep: 端点数据 dict（含 method, pathname, body_field_count, request_body_sample 等）

    Returns:
        True 表示 POST 列表查询（只读），False 表示写操作
    """
    if ep.get("method") != "POST":
        return False

    pathname = ep.get("pathname", "")

    # 策略 1：pathname 以查询后缀结尾
    query_suffixes = ("/list", "/search", "/query", "/check", "/validate")
    if any(pathname.endswith(s) for s in query_suffixes):
        return True

    # 策略 2：请求体含分页字段
    pagination_fields = {"pageNum", "pageSize", "page", "size", "offset", "limit",
                         "currentPage", "pageNo", "pageNumber"}
    body_sample = ep.get("request_body_sample")
    if body_sample:
        try:
            body = json.loads(body_sample) if isinstance(body_sample, str) else body_sample
            if isinstance(body, dict):
                body_keys = set(body.keys())
                if body_keys & pagination_fields:
                    return True
        except Exception:
            pass

    return False


def _build_core_apis_from_core_api_map(core_api_map: dict, infra_apis: set) -> dict:
    """从 core_api_map 构建 core_apis。

    core_api_map 值已改为数组格式，每项包含完整端点数据，
    不需要再查 classified_apis 表。
    """
    core_apis = {}

    for action_name, candidates in core_api_map.items():
        if action_name == "init":
            continue
        if not candidates:
            continue

        # 从候选中选取核心 API
        selected = _select_core_api(candidates, "step")
        if not selected:
            continue

        target_cat = action_name
        if target_cat not in core_apis:
            core_apis[target_cat] = []

        core_apis[target_cat].append(selected)

        # ★ 保留其他写操作候选（操作链）
        # 过滤条件：
        #   1. 是写操作（POST/PUT/PATCH/DELETE）
        #   2. pathname 不以 /list, /search, /query 结尾（这些是伪写操作=查询）
        #   3. 与核心 API 共享资源路径前缀（排除不同资源的 API）
        #   4. DELETE 操作豁免 body 检查（DELETE 通常通过 URL path params 传 ID，无请求体）
        core_pathname = selected.get("pathname", "")
        core_resource = "/".join(core_pathname.rstrip("/").split("/")[:-1])  # 如 /estack/api/estack/draco/v1/users
        query_suffixes = ("/list", "/search", "/query", "/check", "/validate", "/generate")

        others = []
        for c in candidates:
            if c is selected:
                continue
            c_method = c.get("method", "").upper()
            if c_method not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            # DELETE 操作通常通过 URL path params 传 ID，无需请求体
            # PUT 操作也可能无请求体（如状态切换 PUT /xxx/{id}/enable）
            if c.get("body_field_count", 0) <= 0 and c_method not in ("DELETE", "PUT"):
                continue
            # 排除伪写操作（pathname 以 /list 等结尾 = 实际是查询）
            c_pathname = c.get("pathname", "")
            if any(c_pathname.endswith(s) for s in query_suffixes):
                LOG.debug(f"  操作链过滤: {action_name} 跳过 {c_pathname}（伪写操作：以 {c_pathname.split('/')[-1]} 结尾）")
                continue
            # 排除不同资源路径的 API
            c_resource = "/".join(c_pathname.rstrip("/").split("/")[:-1])
            if core_resource and c_resource and core_resource != c_resource:
                LOG.debug(f"  操作链过滤: {action_name} 跳过 {c_pathname}（资源路径 {c_resource} != {core_resource}）")
                continue
            others.append(c)

        for other in others:
            core_apis[target_cat].append(other)
            LOG.info(f"  操作链: {action_name} 追加 "
                     f"{other['method']} {other['pathname'].split('/')[-1]}")

    return core_apis


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


def _derive_order(core_apis: dict, operation_order: list = None) -> list:
    """
    确定执行顺序：直接使用 Stage 2 的 operation_order，不重新推导。

    Stage 2 已按 playbook 回放顺序保存了操作序列，
    该顺序由用户在 Stage 1 中定义，是正确的业务顺序。

    Args:
        core_apis: 核心 API 字典
        operation_order: Stage 2 保存的操作顺序

    Returns:
        执行顺序列表
    """
    # 过滤掉 core_apis 中不存在的操作
    filtered = [op for op in (operation_order or []) if op in core_apis]
    # 补充 core_apis 中有但 operation_order 中没有的（兜底）
    for op in core_apis:
        if op not in filtered:
            filtered.append(op)
    return filtered


def _select_core_api(candidates, purpose="step"):
    """从候选列表中选取最合适的 API。

    Args:
        candidates: 候选 API 列表，每项包含 method, pathname, body_field_count, response_has_id 等
        purpose: 选取目的 - "step"(写操作优先), "id_extract"(写操作+响应含ID), "pre_api"(有请求体)

    Returns:
        选中的候选 dict，或 None
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    if purpose == "id_extract":
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH") and c.get("response_has_id")]
        if writes:
            return max(writes, key=lambda c: c.get("body_field_count", 0))

    elif purpose == "step":
        writes = [c for c in candidates
                  if c["method"] in ("POST", "PUT", "PATCH", "DELETE")]
        if writes:
            return max(writes, key=lambda c: c.get("body_field_count", 0))

    elif purpose == "pre_api":
        with_body = [c for c in candidates if c.get("body_field_count", 0) > 0]
        if with_body:
            return max(with_body, key=lambda c: c.get("body_field_count", 0))

    return max(candidates, key=lambda c: c.get("body_field_count", 0))


def _find_last_write_op(core_apis):
    """从 core_apis 中找最后一个写操作的操作名。"""
    for action in reversed(list(core_apis.keys())):
        for ep in core_apis[action]:
            if ep.get("method") in ("POST", "PUT", "PATCH", "DELETE"):
                return action
    return None
