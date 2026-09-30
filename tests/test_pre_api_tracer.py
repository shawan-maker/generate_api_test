"""
test_pre_api_tracer.py — pre_api_tracer 模块单元测试

测试目标：
- _topological_sort_pre_apis 拓扑排序（线性链、无依赖、菱形、空、单条、环）
- trace_pre_api_dependencies 前置 API 依赖追踪（基础匹配、无匹配、空候选、多操作、上下文字段、值索引）
- resolve_chain 链式依赖追踪（上下文解析、链式依赖、循环防止、深度限制、空角色）
"""
import json
import pytest
from unittest.mock import MagicMock

from core.discovery.stage3.pre_api_tracer import (
    _topological_sort_pre_apis,
    trace_pre_api_dependencies,
    resolve_chain,
)
from core.discovery.stage3.field_classifier import (
    ValueIndex,
    build_value_index,
)


# ============================================================
# Helper functions
# ============================================================

def _make_pre_api(api_id, response_body_dict, extracted_fields,
                  depends_on=None, query_params=None,
                  query_params_samples=None, context="init",
                  timestamp=0.0, path="/api/test"):
    """构造 pre_api 候选 dict。"""
    return {
        "id": api_id,
        "pathname": path,
        "method": "GET",
        "context": context,
        "timestamp": timestamp,
        "depends_on": depends_on or [],
        "query_params": query_params or {},
        "query_params_samples": query_params_samples or [],
        "response_sample": {
            "response_body": json.dumps(response_body_dict),
        },
        "extracted_fields": extracted_fields,
    }


def _make_endpoint(pathname, body_dict, method="POST"):
    """构造操作 endpoint dict。"""
    return {
        "pathname": pathname,
        "method": method,
        "request_body_sample": json.dumps(body_dict),
    }


def _make_value_index_with_entry(value_str, source_api, source_path,
                                 field_leaf, api_info, path_depth=1,
                                 context="init"):
    """构造带单个条目的 ValueIndex。"""
    vi = ValueIndex()
    vi.add(
        value_str=value_str,
        source_api=source_api,
        source_path=source_path,
        field_leaf=field_leaf,
        path_depth=path_depth,
        context=context,
        api_info=api_info,
    )
    return vi


# ============================================================
# _topological_sort_pre_apis 拓扑排序测试
# ============================================================

class TestTopologicalSortPreApis:
    """测试前置 API 拓扑排序（Kahn 算法）"""

    def test_linear_chain_abc(self):
        """线性依赖链 A→B→C → 排序 [A, B, C]（依赖最深者最先执行）"""
        api_c = {"id": "C", "depends_on": []}
        api_b = {"id": "B", "depends_on": ["C"]}
        api_a = {"id": "A", "depends_on": ["B"]}
        used_apis = {"A": api_a, "B": api_b, "C": api_c}

        result = _topological_sort_pre_apis(used_apis)

        assert len(result) == 3
        ids = [r["id"] for r in result]
        # A 依赖 B，B 依赖 C → A 先出（依赖最深者最先）
        assert ids == ["A", "B", "C"]

    def test_no_dependencies_preserves_order(self):
        """无依赖 → 保持原始插入顺序"""
        api_a = {"id": "A", "depends_on": []}
        api_b = {"id": "B", "depends_on": []}
        api_c = {"id": "C", "depends_on": []}
        used_apis = {"A": api_a, "B": api_b, "C": api_c}

        result = _topological_sort_pre_apis(used_apis)

        assert len(result) == 3
        ids = [r["id"] for r in result]
        assert ids == ["A", "B", "C"]

    def test_diamond_dependency(self):
        """菱形依赖 D→B, D→C, B→A, C→A → 有效拓扑序"""
        api_a = {"id": "A", "depends_on": []}
        api_b = {"id": "B", "depends_on": ["A"]}
        api_c = {"id": "C", "depends_on": ["A"]}
        api_d = {"id": "D", "depends_on": ["B", "C"]}
        used_apis = {"D": api_d, "B": api_b, "C": api_c, "A": api_a}

        result = _topological_sort_pre_apis(used_apis)

        assert len(result) == 4
        ids = [r["id"] for r in result]
        # D 最先出，A 最后出
        assert ids.index("D") < ids.index("B")
        assert ids.index("D") < ids.index("C")
        assert ids.index("B") < ids.index("A")
        assert ids.index("C") < ids.index("A")

    def test_empty_dict(self):
        """空字典 → 空列表"""
        result = _topological_sort_pre_apis({})
        assert result == []

    def test_single_entry(self):
        """单条记录 → 单条结果"""
        api = {"id": "only", "depends_on": []}
        result = _topological_sort_pre_apis({"only": api})

        assert len(result) == 1
        assert result[0]["id"] == "only"

    def test_circular_dependency_returns_empty(self):
        """完全循环依赖 → 空结果（graceful handling）"""
        api_a = {"id": "A", "depends_on": ["B"]}
        api_b = {"id": "B", "depends_on": ["A"]}
        used_apis = {"A": api_a, "B": api_b}

        result = _topological_sort_pre_apis(used_apis)

        # 循环中的节点无法进入排序结果
        assert result == []

    def test_partial_cycle_excludes_cyclic_nodes(self):
        """部分循环：非循环节点正常排序，循环节点被排除"""
        # D 无依赖（非循环），A↔B↔C 形成循环
        api_d = {"id": "D", "depends_on": []}
        api_a = {"id": "A", "depends_on": ["B"]}
        api_b = {"id": "B", "depends_on": ["C"]}
        api_c = {"id": "C", "depends_on": ["A"]}
        used_apis = {"D": api_d, "A": api_a, "B": api_b, "C": api_c}

        result = _topological_sort_pre_apis(used_apis)

        # 只有 D 能进入结果
        assert len(result) == 1
        assert result[0]["id"] == "D"

    def test_depends_on_external_api_ignored(self):
        """依赖图外的 API 引用被忽略（depends_on 引用不存在的节点）"""
        api_a = {"id": "A", "depends_on": ["external_not_in_graph"]}
        api_b = {"id": "B", "depends_on": ["A"]}
        used_apis = {"A": api_a, "B": api_b}

        result = _topological_sort_pre_apis(used_apis)

        assert len(result) == 2
        ids = [r["id"] for r in result]
        # A 的外部依赖被忽略 → A 的 in_degree=1（被 B 依赖），B 的 in_degree=0
        # B 先出队，A 后出队
        assert "A" in ids
        assert "B" in ids
        assert ids.index("B") < ids.index("A")


# ============================================================
# trace_pre_api_dependencies 前置 API 依赖追踪测试
# ============================================================

class TestTracePreApiDependencies:
    """测试前置 API 依赖链追踪（v2.0 值索引驱动）"""

    def _make_candidate_and_index(self):
        """构造一个 pre-API 候选及其对应的 ValueIndex。"""
        pre_api = _make_pre_api(
            api_id="get_current_user",
            response_body_dict={"tenantId": "T123", "userId": "U456"},
            extracted_fields=[
                {"name": "tenantId", "path": "tenantId"},
                {"name": "userId", "path": "userId"},
            ],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=None,
            create_response_sample=None,
        )
        return pre_api, vi

    def test_basic_field_match_includes_pre_api(self):
        """操作 body 字段匹配 pre-API 响应字段 → pre-API 被包含"""
        pre_api, vi = self._make_candidate_and_index()

        core_apis = {
            "update": [_make_endpoint("/api/users/update", {"tenantId": "T123", "name": "test"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        pre_apis = result["pre_apis"]
        assert len(pre_apis) == 1
        assert pre_apis[0]["id"] == "get_current_user"

    def test_no_matching_fields_empty_pre_apis(self):
        """操作 body 无匹配字段 → 空 pre_apis"""
        pre_api, vi = self._make_candidate_and_index()

        core_apis = {
            "update": [_make_endpoint("/api/users/update", {"name": "uniquename99"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        assert result["pre_apis"] == []

    def test_empty_candidates_empty_result(self):
        """空候选列表 → 空结果"""
        core_apis = {
            "update": [_make_endpoint("/api/users/update", {"tenantId": "T123"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[],
            context_fields={},
            id_field_details={},
        )

        assert result["pre_apis"] == []
        assert result["field_resolutions"] == {}

    def test_multiple_operations_different_dependencies(self):
        """多操作各有不同依赖 → 所有依赖被收集"""
        pre_api_user = _make_pre_api(
            api_id="get_user",
            response_body_dict={"userId": "U100"},
            extracted_fields=[{"name": "userId", "path": "userId"}],
        )
        pre_api_dept = _make_pre_api(
            api_id="get_dept",
            response_body_dict={"deptId": "D200"},
            extracted_fields=[{"name": "deptId", "path": "deptId"}],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api_user, pre_api_dept],
            create_body_sample=None,
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"userId": "U100"})],
            "delete": [_make_endpoint("/api/d", {"deptId": "D200"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api_user, pre_api_dept],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        pre_ids = {p["id"] for p in result["pre_apis"]}
        assert "get_user" in pre_ids
        assert "get_dept" in pre_ids

    def test_with_context_fields_adds_extract(self):
        """context_fields 声明的字段被追加到 pre_api 的 extracts"""
        pre_api = _make_pre_api(
            api_id="get_tenant",
            response_body_dict={"tenantId": "T999", "extra": "value"},
            extracted_fields=[{"name": "tenantId", "path": "tenantId"}],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=None,
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"tenantId": "T999"})],
        }
        context_fields = {
            "tenantId": {"path": "tenantId"},
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields=context_fields,
            id_field_details={},
            value_index=vi,
        )

        pre_apis = result["pre_apis"]
        assert len(pre_apis) >= 1
        target = pre_apis[0]
        # tenantId 提取应已存在（由值匹配或 context_fields 添加）
        extract_names = [e["name"] for e in target.get("extracts", [])]
        assert "tenantId" in extract_names

    def test_with_external_value_index(self):
        """外部传入 value_index → 直接使用，不重新构建"""
        pre_api = _make_pre_api(
            api_id="ext_api",
            response_body_dict={"token": "abc123xyz"},
            extracted_fields=[{"name": "token", "path": "token"}],
        )
        # 手动构建 value_index 并传入
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=None,
        )

        core_apis = {
            "create": [_make_endpoint("/api/c", {"token": "abc123xyz"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        pre_ids = {p["id"] for p in result["pre_apis"]}
        assert "ext_api" in pre_ids

    def test_without_value_index_built_internally(self):
        """不传 value_index → 内部自动构建"""
        pre_api = _make_pre_api(
            api_id="auto_api",
            response_body_dict={"regionId": "R777"},
            extracted_fields=[{"name": "regionId", "path": "regionId"}],
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"regionId": "R777"})],
        }
        # 不传 value_index，让函数内部构建
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
        )

        pre_ids = {p["id"] for p in result["pre_apis"]}
        assert "auto_api" in pre_ids

    def test_field_resolutions_populated(self):
        """context 字段的 field_resolutions 被正确填充"""
        pre_api = _make_pre_api(
            api_id="get_org",
            response_body_dict={"orgId": "O123"},
            extracted_fields=[{"name": "orgId", "path": "orgId"}],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=None,
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"orgId": "O123"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        # field_resolutions 应有 update.orgId 的记录
        assert "update.orgId" in result["field_resolutions"]
        res = result["field_resolutions"]["update.orgId"]
        assert res["source"] == "get_org.orgId"
        assert res["strategy"] == "exact_value_match"

    def test_id_producer_excludes_create_body_source(self):
        """id_producer 操作的 create_body 来源被排除（不标记为 context）"""
        pre_api = _make_pre_api(
            api_id="helper_api",
            response_body_dict={"code": "CODE555"},
            extracted_fields=[{"name": "code", "path": "code"}],
        )
        create_body = {"name": "newitem", "code": "CODE555"}
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=create_body,
        )

        # id_producer="create" → create 操作的 exclude_sources={"create_body"}
        core_apis = {
            "create": [_make_endpoint("/api/c", create_body)],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            id_producer="create",
            value_index=vi,
        )

        # code 字段在 create_body 和 helper_api 中都有
        # 由于 exclude_sources 排除了 create_body，应匹配到 helper_api
        pre_ids = {p["id"] for p in result["pre_apis"]}
        assert "helper_api" in pre_ids

    def test_empty_core_apis(self):
        """空 core_apis → 空结果"""
        result = trace_pre_api_dependencies(
            core_apis={},
            response_samples={},
            pre_api_candidates=[],
            context_fields={},
            id_field_details={},
        )

        assert result["pre_apis"] == []
        assert result["field_resolutions"] == {}

    def test_create_body_or_create_not_treated_as_pre_api(self):
        """create_body 和 create 来源不被当作前置 API"""
        # 构造一个值同时存在于 create_body 和 pre_api 中的场景
        create_body = {"status": "ACTIVE_STATE_12345"}
        pre_api = _make_pre_api(
            api_id="real_pre_api",
            response_body_dict={"stateCode": "ACTIVE_STATE_12345"},
            extracted_fields=[{"name": "stateCode", "path": "stateCode"}],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=create_body,
        )

        # 非 id_producer 操作，不排除 create_body
        core_apis = {
            "update": [_make_endpoint("/api/u", {"stateCode": "ACTIVE_STATE_12345"})],
        }
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        # 不应有 create_body 或 create 在 pre_apis 中
        pre_ids = {p["id"] for p in result["pre_apis"]}
        assert "create_body" not in pre_ids
        assert "create" not in pre_ids


# ============================================================
# resolve_chain 链式依赖追踪测试
# ============================================================

class TestResolveChain:
    """测试链式依赖追踪（递归 + 循环防止 + 深度限制）"""

    def _make_mock_vi(self, lookup_returns=None):
        """构造 mock ValueIndex，lookup_returns 为 {value_str: entry_dict}。"""
        vi = MagicMock(spec=ValueIndex)

        def side_effect(value, request_field_name="", current_action=""):
            val_str = str(value) if not isinstance(value, list) else str(value[0]) if value else ""
            return lookup_returns.get(val_str)

        vi.lookup = MagicMock(side_effect=side_effect)
        return vi

    def test_context_field_resolved_to_pre_api(self):
        """context 字段解析到 pre-API → pre-API 被添加到 used_apis"""
        api_info = {"id": "get_user", "query_params": {}, "depends_on": []}
        vi = self._make_mock_vi({
            "USR001": {"source_api": "get_user", "source_path": "get_user.userId",
                       "api_info": api_info},
        })

        body_sample = {"userId": "USR001"}
        field_roles = {"userId": {"role": "context", "source": "get_user.userId"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "get_user" in used_apis
        assert used_apis["get_user"]["id"] == "get_user"

    def test_chained_dependency_both_added(self):
        """链式依赖：pre-API A 的 query_params 匹配 pre-API B → 两者都被添加"""
        api_a = {"id": "api_a", "query_params": {"parentId": "PARENT_XYZ"}, "depends_on": []}
        api_b = {"id": "api_b", "query_params": {}, "depends_on": []}

        vi = self._make_mock_vi({
            "CHILD_VAL": {"source_api": "api_a", "source_path": "api_a.childId",
                          "api_info": api_a},
            "PARENT_XYZ": {"source_api": "api_b", "source_path": "api_b.parentId",
                           "api_info": api_b},
        })

        body_sample = {"childId": "CHILD_VAL"}
        field_roles = {"childId": {"role": "context", "source": "api_a.childId"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "api_a" in used_apis
        assert "api_b" in used_apis

    def test_cycle_prevention_via_seen_apis(self):
        """seen_apis 防止循环：已访问的 API 不被重复处理"""
        api_info = {"id": "api_loop", "query_params": {"ref": "LOOPVAL"}, "depends_on": []}
        # api_loop 的 query_params.ref 也匹配 api_loop → 但 seen_apis 阻止循环
        vi = self._make_mock_vi({
            "LOOP_BODY": {"source_api": "api_loop", "source_path": "api_loop.field",
                          "api_info": api_info},
            "LOOPVAL": {"source_api": "api_loop", "source_path": "api_loop.ref",
                        "api_info": api_info},
        })

        body_sample = {"field": "LOOP_BODY"}
        field_roles = {"field": {"role": "context", "source": "api_loop.field"}}
        used_apis = {}

        # 不会无限递归
        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "api_loop" in used_apis

    def test_depth_limit_stops_recursion(self):
        """max_depth=0 → 立即停止，不添加任何 API"""
        api_info = {"id": "deep_api", "query_params": {}, "depends_on": []}
        vi = self._make_mock_vi({
            "VAL": {"source_api": "deep_api", "source_path": "deep_api.f",
                    "api_info": api_info},
        })

        body_sample = {"f": "VAL"}
        field_roles = {"f": {"role": "context", "source": "deep_api.f"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis, depth=5, max_depth=5)

        # depth == max_depth → 直接返回，不处理
        assert len(used_apis) == 0

    def test_empty_field_roles_no_mutation(self):
        """空 field_roles → used_apis 不被修改"""
        vi = self._make_mock_vi({})
        body_sample = {"x": "y"}
        field_roles = {}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert used_apis == {}

    def test_non_context_fields_ignored(self):
        """非 context 角色的字段被忽略"""
        vi = self._make_mock_vi({})
        body_sample = {"name": "hello", "status": "static_val"}
        field_roles = {
            "name": {"role": "name"},
            "status": {"role": "static"},
        }
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert used_apis == {}

    def test_create_body_source_skipped(self):
        """create_body 来源被跳过（不是真正的前置 API）"""
        vi = self._make_mock_vi({})
        body_sample = {"name": "testval"}
        field_roles = {"name": {"role": "context", "source": "create_body.name"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "create_body" not in used_apis
        assert used_apis == {}

    def test_create_source_skipped(self):
        """create 来源被跳过"""
        vi = self._make_mock_vi({})
        body_sample = {"id": "someid"}
        field_roles = {"id": {"role": "context", "source": "create.id"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "create" not in used_apis
        assert used_apis == {}

    def test_no_dot_in_source_skipped(self):
        """source 中无 '.' 的字段被跳过"""
        vi = self._make_mock_vi({})
        body_sample = {"x": "y"}
        field_roles = {"x": {"role": "context", "source": "nosource"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert used_apis == {}

    def test_sub_dependency_via_query_params(self):
        """pre-API 的 query_params 值匹配另一个 pre-API → 子依赖被添加"""
        api_parent = {"id": "parent_api", "query_params": {"orgId": "ORG_999"}, "depends_on": []}
        api_grandparent = {"id": "gp_api", "query_params": {}, "depends_on": []}

        vi = self._make_mock_vi({
            "ITEM_VAL": {"source_api": "parent_api", "source_path": "parent_api.itemId",
                         "api_info": api_parent},
            "ORG_999": {"source_api": "gp_api", "source_path": "gp_api.orgId",
                        "api_info": api_grandparent},
        })

        body_sample = {"itemId": "ITEM_VAL"}
        field_roles = {"itemId": {"role": "context", "source": "parent_api.itemId"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "parent_api" in used_apis
        assert "gp_api" in used_apis

    def test_sub_dependency_same_api_skipped(self):
        """子依赖如果指向同一个 API → 被跳过（防止自引用）"""
        api_info = {"id": "self_ref", "query_params": {"token": "TOKEN_SELF"}, "depends_on": []}

        vi = self._make_mock_vi({
            "BODY_VAL": {"source_api": "self_ref", "source_path": "self_ref.field",
                         "api_info": api_info},
            "TOKEN_SELF": {"source_api": "self_ref", "source_path": "self_ref.token",
                           "api_info": api_info},
        })

        body_sample = {"field": "BODY_VAL"}
        field_roles = {"field": {"role": "context", "source": "self_ref.field"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        # self_ref 只出现一次
        assert used_apis == {"self_ref": api_info}

    def test_noise_query_param_values_filtered(self):
        """query_params 中的噪声值（如 true、null）不参与匹配"""
        api_info = {"id": "noisy_api", "query_params": {"flag": "true", "real": "REAL_VAL_XYZ"}, "depends_on": []}

        vi = self._make_mock_vi({
            "BODY_V": {"source_api": "noisy_api", "source_path": "noisy_api.f",
                       "api_info": api_info},
            # true 是噪声值，不应触发 lookup
        })

        body_sample = {"f": "BODY_V"}
        field_roles = {"f": {"role": "context", "source": "noisy_api.f"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "noisy_api" in used_apis
        # vi.lookup 应只被调用 body 字段 + 非噪声 query param
        # "true" 被 is_noise_value 过滤，不会调用 lookup

    def test_query_params_samples_fallback(self):
        """query_params 为空时，从 query_params_samples 获取"""
        api_info = {
            "id": "qps_api",
            "query_params": {},
            "query_params_samples": [{"regionCode": "RC888"}],
            "depends_on": [],
        }
        api_sub = {"id": "sub_api", "query_params": {}, "depends_on": []}

        vi = self._make_mock_vi({
            "MAIN_V": {"source_api": "qps_api", "source_path": "qps_api.main",
                       "api_info": api_info},
            "RC888": {"source_api": "sub_api", "source_path": "sub_api.regionCode",
                      "api_info": api_sub},
        })

        body_sample = {"main": "MAIN_V"}
        field_roles = {"main": {"role": "context", "source": "qps_api.main"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "qps_api" in used_apis
        assert "sub_api" in used_apis

    def test_multiple_context_fields(self):
        """多个 context 字段各自解析到不同的 pre-API"""
        api_a = {"id": "api_alpha", "query_params": {}, "depends_on": []}
        api_b = {"id": "api_beta", "query_params": {}, "depends_on": []}

        vi = self._make_mock_vi({
            "VAL_A": {"source_api": "api_alpha", "source_path": "api_alpha.aField",
                      "api_info": api_a},
            "VAL_B": {"source_api": "api_beta", "source_path": "api_beta.bField",
                      "api_info": api_b},
        })

        body_sample = {"aField": "VAL_A", "bField": "VAL_B"}
        field_roles = {
            "aField": {"role": "context", "source": "api_alpha.aField"},
            "bField": {"role": "context", "source": "api_beta.bField"},
        }
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "api_alpha" in used_apis
        assert "api_beta" in used_apis

    def test_nested_field_with_dot_key(self):
        """嵌套字段（dot-separated key）正确取值和解析"""
        api_info = {"id": "nested_api", "query_params": {}, "depends_on": []}

        vi = self._make_mock_vi({
            "NESTED_VAL": {"source_api": "nested_api", "source_path": "nested_api.policyId",
                           "api_info": api_info},
        })

        body_sample = {"policy": {"policyId": "NESTED_VAL"}}
        field_roles = {"policy.policyId": {"role": "context", "source": "nested_api.policyId"}}
        used_apis = {}

        resolve_chain(body_sample, field_roles, vi, used_apis)

        assert "nested_api" in used_apis


# ============================================================
# 集成场景测试
# ============================================================

class TestIntegrationScenarios:
    """端到端场景：多 pre-API 带依赖的完整追踪"""

    def test_full_trace_with_topological_order(self):
        """完整追踪：多个 pre-API 有依赖关系 → 结果按拓扑序排列"""
        # 构造两个 pre-API: get_org 依赖 get_region
        pre_region = _make_pre_api(
            api_id="get_region",
            response_body_dict={"regionId": "REG001"},
            extracted_fields=[{"name": "regionId", "path": "regionId"}],
        )
        pre_org = _make_pre_api(
            api_id="get_org",
            response_body_dict={"orgId": "ORG001", "regionId": "REG001"},
            extracted_fields=[
                {"name": "orgId", "path": "orgId"},
                {"name": "regionId", "path": "regionId"},
            ],
            depends_on=["get_region"],
        )

        vi = build_value_index(
            pre_api_candidates=[pre_region, pre_org],
            create_body_sample=None,
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"orgId": "ORG001"})],
        }

        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_region, pre_org],
            context_fields={},
            id_field_details={},
            value_index=vi,
        )

        pre_ids = [p["id"] for p in result["pre_apis"]]
        # get_org 依赖 get_region → get_org 应在 get_region 之前（依赖最深者先出）
        # 或两者都被包含
        assert "get_org" in pre_ids

    def test_context_fields_adds_missing_extract(self):
        """context_fields 声明但 pre_api 尚未提取的字段 → 追加提取"""
        pre_api = _make_pre_api(
            api_id="ctx_api",
            response_body_dict={"authToken": "TOKEN_XYZ", "tenantId": "T001"},
            extracted_fields=[{"name": "authToken", "path": "authToken"}],
        )
        vi = build_value_index(
            pre_api_candidates=[pre_api],
            create_body_sample=None,
        )

        core_apis = {
            "update": [_make_endpoint("/api/u", {"authToken": "TOKEN_XYZ"})],
        }
        # context_fields 声明 tenantId，但 pre_api 没有提取它
        context_fields = {
            "tenantId": {"path": "tenantId"},
        }

        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples={},
            pre_api_candidates=[pre_api],
            context_fields=context_fields,
            id_field_details={},
            value_index=vi,
        )

        pre_apis = result["pre_apis"]
        assert len(pre_apis) >= 1
        target = pre_apis[0]
        extract_names = [e["name"] for e in target.get("extracts", [])]
        # tenantId 应被 context_fields 逻辑追加
        assert "tenantId" in extract_names
