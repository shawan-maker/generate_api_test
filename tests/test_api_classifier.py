"""
test_api_classifier.py — api_classifier 模块单元测试

测试目标：
- _is_list_query 列表查询判断
- _is_post_list_query POST 列表查询判断
- _build_core_apis_from_core_api_map 核心 API 构建
- _map_buttons_to_apis 按钮-API 映射
- _derive_order 执行顺序推导
- _select_core_api 核心 API 选取
- _find_last_write_op 写操作查找
- _identify_infrastructure_apis 基础设施 API 识别
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from core.discovery.stage3.api_classifier import (
    _is_list_query,
    _is_post_list_query,
    _build_core_apis_from_core_api_map,
    _map_buttons_to_apis,
    _derive_order,
    _select_core_api,
    _find_last_write_op,
    _identify_infrastructure_apis,
)


# ============================================================
# _is_list_query 列表查询判断测试
# ============================================================

class TestIsListQuery:
    """测试 _is_list_query：判断 API 是否为列表查询"""

    def test_entity_is_list(self):
        """entity 直接是列表 → True"""
        samples = {
            "/api/tree": [{"body": '{"entity": [{"id": "1"}, {"id": "2"}]}'}]
        }
        assert _is_list_query("/api/tree", samples) is True

    def test_entity_with_list_and_total(self):
        """entity 包含 list + total 键 → True"""
        samples = {
            "/api/users": [{"body": '{"entity": {"list": [{"id": "1"}], "total": 10}}'}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_entity_with_records_and_total_count(self):
        """entity 包含 records + totalCount 键 → True"""
        samples = {
            "/api/users": [{"body": '{"entity": {"records": [], "totalCount": 0}}'}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_entity_with_rows_and_count(self):
        """entity 包含 rows + count 键 → True"""
        samples = {
            "/api/users": [{"body": '{"entity": {"rows": [{"id": "1"}], "count": 5}}'}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_entity_is_single_dict(self):
        """entity 是单个对象（非列表结构） → False"""
        samples = {
            "/api/users/1": [{"body": '{"entity": {"id": "1", "name": "test"}}'}]
        }
        assert _is_list_query("/api/users/1", samples) is False

    def test_entity_is_empty_list(self):
        """entity 是空列表 → False（len == 0 不满足 > 0 条件）"""
        samples = {
            "/api/tree": [{"body": '{"entity": []}'}]
        }
        assert _is_list_query("/api/tree", samples) is False

    def test_empty_response_samples(self):
        """空 response_samples → False"""
        assert _is_list_query("/api/users", {}) is False
        assert _is_list_query("/api/users", None) is False

    def test_pathname_not_in_samples(self):
        """pathname 不在 samples 中 → False"""
        samples = {
            "/api/other": [{"body": '{"entity": []}'}]
        }
        assert _is_list_query("/api/users", samples) is False

    def test_string_body_json_parsing(self):
        """body 为 JSON 字符串时正确解析"""
        body_dict = {"entity": {"list": [{"id": "1"}], "total": 5}}
        samples = {
            "/api/users": [{"body": json.dumps(body_dict)}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_dict_body_no_parsing_needed(self):
        """body 已经是 dict 时直接使用"""
        samples = {
            "/api/users": [{"body": {"entity": {"list": [{"id": "1"}], "total": 5}}}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_invalid_json_body(self):
        """body 为无效 JSON 字符串 → False"""
        samples = {
            "/api/users": [{"body": "not valid json"}]
        }
        assert _is_list_query("/api/users", samples) is False

    def test_data_envelope_key(self):
        """使用 data 作为信封键 → True"""
        samples = {
            "/api/users": [{"body": '{"data": {"list": [{"id": "1"}], "total": 5}}'}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_result_envelope_key(self):
        """使用 result 作为信封键 → True"""
        samples = {
            "/api/users": [{"body": '{"result": {"items": [{"id": "1"}], "totalElements": 3}}'}]
        }
        assert _is_list_query("/api/users", samples) is True

    def test_no_envelope_key_body_is_list(self):
        """无信封键，body 本身是列表 → 不匹配（body 解析后为 list 但非 dict entity）"""
        # body 直接是 dict 但无信封键匹配时 entity = body
        # body 本身若是 list 则 entity 不会变成 list（循环不命中）
        samples = {
            "/api/users": [{"body": '[{"id": "1"}, {"id": "2"}]'}]
        }
        # body 解析后是 list，for ek 循环不会命中（body[ek] 报 TypeError）
        # entity = body (list), isinstance(entity, list) → True
        assert _is_list_query("/api/users", samples) is True

    def test_only_list_key_no_total(self):
        """entity 有 list 键但无 total → False"""
        samples = {
            "/api/users": [{"body": '{"entity": {"list": [{"id": "1"}]}}'}]
        }
        assert _is_list_query("/api/users", samples) is False

    def test_only_total_key_no_list(self):
        """entity 有 total 键但无 list → False"""
        samples = {
            "/api/users": [{"body": '{"entity": {"total": 10}}'}]
        }
        assert _is_list_query("/api/users", samples) is False

    def test_only_first_sample_checked(self):
        """只检查前 1 个 sample"""
        samples = {
            "/api/users": [
                {"body": '{"entity": {"id": "1", "name": "single"}}'},  # 第一个：非列表
                {"body": '{"entity": {"list": [], "total": 0}}'},  # 第二个：列表（不检查）
            ]
        }
        assert _is_list_query("/api/users", samples) is False


# ============================================================
# _is_post_list_query POST 列表查询判断测试
# ============================================================

class TestIsPostListQuery:
    """测试 _is_post_list_query：判断 POST 端点是否为列表查询"""

    def test_pathname_ends_with_list(self):
        """pathname 以 /list 结尾 → True"""
        ep = {"method": "POST", "pathname": "/api/users/list"}
        assert _is_post_list_query(ep) is True

    def test_pathname_ends_with_search(self):
        """pathname 以 /search 结尾 → True"""
        ep = {"method": "POST", "pathname": "/api/users/search"}
        assert _is_post_list_query(ep) is True

    def test_pathname_ends_with_query(self):
        """pathname 以 /query 结尾 → True"""
        ep = {"method": "POST", "pathname": "/api/users/query"}
        assert _is_post_list_query(ep) is True

    def test_pathname_ends_with_check(self):
        """pathname 以 /check 结尾 → True"""
        ep = {"method": "POST", "pathname": "/api/users/check"}
        assert _is_post_list_query(ep) is True

    def test_pathname_ends_with_validate(self):
        """pathname 以 /validate 结尾 → True"""
        ep = {"method": "POST", "pathname": "/api/users/validate"}
        assert _is_post_list_query(ep) is True

    def test_post_with_page_in_body(self):
        """POST + 请求体含 page 分页字段 → True"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '{"page": 1, "pageSize": 10, "name": "test"}',
        }
        assert _is_post_list_query(ep) is True

    def test_post_with_page_num_in_body(self):
        """POST + 请求体含 pageNum 分页字段 → True"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '{"pageNum": 1, "pageSize": 10}',
        }
        assert _is_post_list_query(ep) is True

    def test_post_with_current_page_in_body(self):
        """POST + 请求体含 currentPage 分页字段 → True"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '{"currentPage": 1, "size": 20}',
        }
        assert _is_post_list_query(ep) is True

    def test_post_with_offset_limit_in_body(self):
        """POST + 请求体含 offset/limit 分页字段 → True"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '{"offset": 0, "limit": 20}',
        }
        assert _is_post_list_query(ep) is True

    def test_regular_post_write(self):
        """普通 POST 写操作 → False"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '{"userName": "test", "password": "123"}',
        }
        assert _is_post_list_query(ep) is False

    def test_get_method(self):
        """GET 方法 → False（只处理 POST）"""
        ep = {"method": "GET", "pathname": "/api/users/list"}
        assert _is_post_list_query(ep) is False

    def test_missing_method(self):
        """缺少 method 字段 → False"""
        ep = {"pathname": "/api/users/list"}
        assert _is_post_list_query(ep) is False

    def test_missing_pathname(self):
        """缺少 pathname 字段 → False"""
        ep = {"method": "POST"}
        assert _is_post_list_query(ep) is False

    def test_empty_request_body_sample(self):
        """request_body_sample 为空 → False（仅靠 pathname 判断）"""
        ep = {"method": "POST", "pathname": "/api/users"}
        assert _is_post_list_query(ep) is False

    def test_invalid_json_body_sample(self):
        """request_body_sample 为无效 JSON → False"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": "not json",
        }
        assert _is_post_list_query(ep) is False

    def test_dict_body_sample(self):
        """request_body_sample 已经是 dict → True"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": {"pageNum": 1, "pageSize": 10},
        }
        assert _is_post_list_query(ep) is True

    def test_body_sample_is_array(self):
        """request_body_sample 是数组（非 dict） → False"""
        ep = {
            "method": "POST",
            "pathname": "/api/users",
            "request_body_sample": '[1, 2, 3]',
        }
        assert _is_post_list_query(ep) is False


# ============================================================
# _build_core_apis_from_core_api_map 核心 API 构建测试
# ============================================================

class TestBuildCoreApisFromCoreApiMap:
    """测试 _build_core_apis_from_core_api_map：从 core_api_map 构建 core_apis"""

    def test_basic_build(self):
        """基本构建：单个操作单个候选"""
        core_api_map = {
            "create": [
                {"method": "POST", "pathname": "/api/users", "body_field_count": 4},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert "create" in result
        assert len(result["create"]) == 1
        assert result["create"][0]["pathname"] == "/api/users"

    def test_skip_init_action(self):
        """init 操作被跳过"""
        core_api_map = {
            "init": [
                {"method": "GET", "pathname": "/api/current-user", "body_field_count": 0},
            ],
            "create": [
                {"method": "POST", "pathname": "/api/users", "body_field_count": 3},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert "init" not in result
        assert "create" in result

    def test_empty_candidates(self):
        """空候选列表被跳过"""
        core_api_map = {
            "create": [],
            "update": [
                {"method": "PUT", "pathname": "/api/users/1", "body_field_count": 2},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert "create" not in result
        assert "update" in result

    def test_empty_core_api_map(self):
        """空 core_api_map → 空结果"""
        result = _build_core_apis_from_core_api_map({}, set())
        assert result == {}

    def test_chain_candidate_appending(self):
        """操作链候选追加：同资源路径的写操作被追加"""
        core_api_map = {
            "create": [
                {"method": "POST", "pathname": "/api/v1/users", "body_field_count": 4},
                {"method": "POST", "pathname": "/api/v1/accounts", "body_field_count": 2},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert "create" in result
        # 核心 API + 操作链候选（同资源路径 /api/v1）
        assert len(result["create"]) == 2

    def test_chain_filters_query_suffix(self):
        """操作链过滤：pathname 以 /list 等查询后缀结尾的被排除"""
        core_api_map = {
            "create": [
                {"method": "POST", "pathname": "/api/users", "body_field_count": 4},
                {"method": "POST", "pathname": "/api/users/list", "body_field_count": 2},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert len(result["create"]) == 1
        assert result["create"][0]["pathname"] == "/api/users"

    def test_chain_filters_different_resource(self):
        """操作链过滤：不同资源路径的 API 被排除"""
        core_api_map = {
            "create": [
                {"method": "POST", "pathname": "/api/users", "body_field_count": 4},
                {"method": "POST", "pathname": "/api/roles/assign", "body_field_count": 2},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert len(result["create"]) == 1

    def test_chain_keeps_delete_without_body(self):
        """操作链保留：DELETE 操作无需 body_field_count"""
        core_api_map = {
            "delete": [
                {"method": "DELETE", "pathname": "/api/users/1", "body_field_count": 0},
                {"method": "DELETE", "pathname": "/api/users/1/roles", "body_field_count": 0},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert "delete" in result
        # DELETE 操作豁免 body 检查
        assert len(result["delete"]) >= 1

    def test_chain_filters_get_candidates(self):
        """操作链过滤：GET 方法不被追加为链候选"""
        core_api_map = {
            "create": [
                {"method": "POST", "pathname": "/api/users", "body_field_count": 4},
                {"method": "GET", "pathname": "/api/users/1", "body_field_count": 0},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert len(result["create"]) == 1

    def test_selects_write_over_read(self):
        """选取核心 API：写操作优先于读操作"""
        core_api_map = {
            "create": [
                {"method": "GET", "pathname": "/api/users/list", "body_field_count": 0},
                {"method": "POST", "pathname": "/api/users", "body_field_count": 5},
            ],
        }
        result = _build_core_apis_from_core_api_map(core_api_map, set())
        assert result["create"][0]["method"] == "POST"


# ============================================================
# _map_buttons_to_apis 按钮-API 映射测试
# ============================================================

class TestMapButtonsToApis:
    """测试 _map_buttons_to_apis：构建按钮文本到核心 API 的映射"""

    def test_toolbar_button_mapping(self):
        """工具栏按钮通过 click context 映射"""
        core_apis = {
            "create": [{
                "method": "POST",
                "pathname": "/api/users",
                "contexts": ["click:新增用户"],
                "request_body_sample": '{"name":"test"}',
                "query_params": {},
            }],
        }
        ui_result = {
            "toolbar_buttons": [{"text": "新增用户"}],
            "row_actions": [],
            "dropdowns": [],
        }
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        assert "新增用户" in mapping
        assert mapping["新增用户"][0]["pathname"] == "/api/users"
        assert mapping["新增用户"][0]["method"] == "POST"

    def test_row_action_mapping(self):
        """行操作按钮映射"""
        core_apis = {
            "delete": [{
                "method": "DELETE",
                "pathname": "/api/users/1",
                "contexts": ["click:删除"],
                "query_params": {},
            }],
        }
        ui_result = {
            "toolbar_buttons": [],
            "row_actions": [{"text": "删除"}],
            "dropdowns": [],
        }
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        assert "删除" in mapping
        assert mapping["删除"][0]["category"] == "delete"

    def test_dropdown_mapping(self):
        """下拉菜单按钮映射"""
        core_apis = {
            "state_change": [{
                "method": "POST",
                "pathname": "/api/users/lock",
                "contexts": ["click:锁定"],
                "query_params": {},
            }],
        }
        ui_result = {
            "toolbar_buttons": [],
            "row_actions": [],
            "dropdowns": [{"text": "锁定", "parent": "更多"}],
        }
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        assert "锁定" in mapping

    def test_empty_ui_result(self):
        """空 ui_result → 空映射"""
        core_apis = {
            "create": [{
                "method": "POST",
                "pathname": "/api/users",
                "contexts": ["click:新增"],
                "query_params": {},
            }],
        }
        ui_result = {}
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        # contexts 中有 click:新增 但 ui_result 为空 → button_texts 为空
        # 但映射是基于 contexts 建立的，不受 button_texts 影响
        # 实际上 _map_buttons_to_apis 中 button_texts 仅做收集，映射靠 contexts
        assert "新增" in mapping

    def test_multiple_contexts_same_endpoint(self):
        """同一端点多个 context 产生多个映射"""
        core_apis = {
            "create": [{
                "method": "POST",
                "pathname": "/api/users",
                "contexts": ["click:新增", "click:创建"],
                "query_params": {},
            }],
        }
        ui_result = {
            "toolbar_buttons": [{"text": "新增"}, {"text": "创建"}],
            "row_actions": [],
            "dropdowns": [],
        }
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        assert "新增" in mapping
        assert "创建" in mapping

    def test_context_without_colon_ignored(self):
        """context 无冒号分隔符时被忽略"""
        core_apis = {
            "create": [{
                "method": "POST",
                "pathname": "/api/users",
                "contexts": ["replay_create"],  # 无冒号
                "query_params": {},
            }],
        }
        ui_result = {"toolbar_buttons": [], "row_actions": [], "dropdowns": []}
        mapping = _map_buttons_to_apis(core_apis, [], ui_result)
        assert mapping == {}

    def test_empty_core_apis(self):
        """空 core_apis → 空映射"""
        ui_result = {
            "toolbar_buttons": [{"text": "新增"}],
            "row_actions": [],
            "dropdowns": [],
        }
        mapping = _map_buttons_to_apis({}, [], ui_result)
        assert mapping == {}


# ============================================================
# _derive_order 执行顺序推导测试
# ============================================================

class TestDeriveOrder:
    """测试 _derive_order：确定执行顺序"""

    def test_with_explicit_operation_order(self):
        """有明确 operation_order → 过滤到 core_apis 中存在的"""
        core_apis = {"create": [], "update": [], "delete": []}
        operation_order = ["create", "update", "delete", "nonexistent"]
        result = _derive_order(core_apis, operation_order)
        assert result == ["create", "update", "delete"]

    def test_without_operation_order(self):
        """无 operation_order → 返回 core_apis 所有操作"""
        core_apis = {"create": [], "query": [], "update": [], "delete": []}
        result = _derive_order(core_apis, None)
        assert set(result) == {"create", "query", "update", "delete"}

    def test_unlisted_actions_appended(self):
        """operation_order 未列出的操作被追加到末尾"""
        core_apis = {"create": [], "query": [], "update": [], "delete": []}
        operation_order = ["create", "delete"]
        result = _derive_order(core_apis, operation_order)
        assert result[0] == "create"
        assert result[1] == "delete"
        # query 和 update 被追加
        assert "query" in result
        assert "update" in result
        assert len(result) == 4

    def test_empty_operation_order(self):
        """空 operation_order → 返回 core_apis 所有操作"""
        core_apis = {"create": [], "delete": []}
        result = _derive_order(core_apis, [])
        assert set(result) == {"create", "delete"}

    def test_operation_order_none(self):
        """operation_order 为 None → 返回 core_apis 所有操作"""
        core_apis = {"create": []}
        result = _derive_order(core_apis, None)
        assert result == ["create"]

    def test_preserves_order(self):
        """保持 operation_order 中的顺序"""
        core_apis = {"delete": [], "create": [], "update": []}
        operation_order = ["create", "update", "delete"]
        result = _derive_order(core_apis, operation_order)
        assert result == ["create", "update", "delete"]

    def test_empty_core_apis(self):
        """空 core_apis → 空列表"""
        result = _derive_order({}, ["create", "delete"])
        assert result == []


# ============================================================
# _select_core_api 核心 API 选取测试
# ============================================================

class TestSelectCoreApi:
    """测试 _select_core_api：从候选列表中选取最合适的 API"""

    def test_purpose_step_write_ops(self):
        """purpose='step' → 写操作中 body_field_count 最大的"""
        candidates = [
            {"method": "GET", "pathname": "/api/users", "body_field_count": 0},
            {"method": "POST", "pathname": "/api/users", "body_field_count": 5},
            {"method": "POST", "pathname": "/api/users/extra", "body_field_count": 3},
        ]
        result = _select_core_api(candidates, "step")
        assert result["pathname"] == "/api/users"
        assert result["body_field_count"] == 5

    def test_purpose_step_includes_delete(self):
        """purpose='step' → DELETE 也算写操作"""
        candidates = [
            {"method": "GET", "pathname": "/api/users/1", "body_field_count": 0},
            {"method": "DELETE", "pathname": "/api/users/1", "body_field_count": 0},
        ]
        result = _select_core_api(candidates, "step")
        assert result["method"] == "DELETE"

    def test_purpose_id_extract(self):
        """purpose='id_extract' → 写操作 + response_has_id"""
        candidates = [
            {"method": "POST", "pathname": "/api/users", "body_field_count": 5, "response_has_id": True},
            {"method": "POST", "pathname": "/api/users/check", "body_field_count": 3, "response_has_id": False},
            {"method": "GET", "pathname": "/api/users/1", "body_field_count": 0, "response_has_id": True},
        ]
        result = _select_core_api(candidates, "id_extract")
        assert result["pathname"] == "/api/users"
        assert result["response_has_id"] is True

    def test_purpose_id_extract_no_write_with_id(self):
        """purpose='id_extract' 但无写操作含 ID → fallback 到 body_field_count 最大"""
        candidates = [
            {"method": "POST", "pathname": "/api/users", "body_field_count": 5, "response_has_id": False},
            {"method": "GET", "pathname": "/api/users/1", "body_field_count": 0, "response_has_id": True},
        ]
        result = _select_core_api(candidates, "id_extract")
        # 无 writes with response_has_id → fallback 到最后 max(candidates, body_field_count)
        assert result["body_field_count"] == 5

    def test_purpose_pre_api(self):
        """purpose='pre_api' → 有 body 的候选"""
        candidates = [
            {"method": "GET", "pathname": "/api/config", "body_field_count": 0},
            {"method": "POST", "pathname": "/api/check", "body_field_count": 3},
        ]
        result = _select_core_api(candidates, "pre_api")
        assert result["pathname"] == "/api/check"

    def test_purpose_pre_api_no_body(self):
        """purpose='pre_api' 但无 body → fallback"""
        candidates = [
            {"method": "GET", "pathname": "/api/config", "body_field_count": 0},
            {"method": "GET", "pathname": "/api/info", "body_field_count": 0},
        ]
        result = _select_core_api(candidates, "pre_api")
        # 无 with_body → fallback 到 max(candidates, body_field_count)，都为 0 取第一个
        assert result is not None

    def test_empty_candidates(self):
        """空候选列表 → None"""
        assert _select_core_api([], "step") is None
        assert _select_core_api(None, "step") is None

    def test_single_candidate(self):
        """单个候选 → 直接返回"""
        candidate = {"method": "POST", "pathname": "/api/users", "body_field_count": 3}
        result = _select_core_api([candidate], "step")
        assert result is candidate

    def test_purpose_step_put_and_patch(self):
        """purpose='step' → PUT 和 PATCH 也算写操作"""
        candidates = [
            {"method": "PUT", "pathname": "/api/users/1", "body_field_count": 4},
            {"method": "PATCH", "pathname": "/api/users/1", "body_field_count": 6},
        ]
        result = _select_core_api(candidates, "step")
        assert result["method"] == "PATCH"
        assert result["body_field_count"] == 6


# ============================================================
# _find_last_write_op 写操作查找测试
# ============================================================

class TestFindLastWriteOp:
    """测试 _find_last_write_op：从 core_apis 中找最后一个写操作"""

    def test_dict_with_post(self):
        """包含 POST 操作 → 返回操作名"""
        core_apis = {
            "query": [{"method": "GET", "pathname": "/api/users"}],
            "create": [{"method": "POST", "pathname": "/api/users"}],
        }
        result = _find_last_write_op(core_apis)
        assert result == "create"

    def test_dict_with_put(self):
        """包含 PUT 操作 → 返回操作名"""
        core_apis = {
            "query": [{"method": "GET", "pathname": "/api/users"}],
            "update": [{"method": "PUT", "pathname": "/api/users/1"}],
        }
        result = _find_last_write_op(core_apis)
        assert result == "update"

    def test_dict_with_delete(self):
        """包含 DELETE 操作 → 返回操作名"""
        core_apis = {
            "create": [{"method": "POST", "pathname": "/api/users"}],
            "delete": [{"method": "DELETE", "pathname": "/api/users/1"}],
        }
        result = _find_last_write_op(core_apis)
        assert result == "delete"

    def test_dict_with_patch(self):
        """包含 PATCH 操作 → 返回操作名"""
        core_apis = {
            "query": [{"method": "GET", "pathname": "/api/users"}],
            "update": [{"method": "PATCH", "pathname": "/api/users/1"}],
        }
        result = _find_last_write_op(core_apis)
        assert result == "update"

    def test_dict_only_get(self):
        """只有 GET 操作 → None"""
        core_apis = {
            "query": [{"method": "GET", "pathname": "/api/users"}],
            "detail": [{"method": "GET", "pathname": "/api/users/1"}],
        }
        result = _find_last_write_op(core_apis)
        assert result is None

    def test_empty_dict(self):
        """空字典 → None"""
        result = _find_last_write_op({})
        assert result is None

    def test_last_write_wins(self):
        """多个写操作 → 返回最后一个（按 dict key 顺序）"""
        core_apis = {
            "create": [{"method": "POST", "pathname": "/api/users"}],
            "update": [{"method": "PUT", "pathname": "/api/users/1"}],
            "delete": [{"method": "DELETE", "pathname": "/api/users/1"}],
        }
        result = _find_last_write_op(core_apis)
        assert result == "delete"

    def test_mixed_endpoints_in_action(self):
        """同一操作下有多个端点，含写操作"""
        core_apis = {
            "create": [
                {"method": "GET", "pathname": "/api/config"},
                {"method": "POST", "pathname": "/api/users"},
            ],
        }
        result = _find_last_write_op(core_apis)
        assert result == "create"


# ============================================================
# _identify_infrastructure_apis 基础设施 API 识别测试
# ============================================================

class TestIdentifyInfrastructureApis:
    """测试 _identify_infrastructure_apis：识别基础设施 API"""

    @patch("core.discovery.kb_loader.get_kb")
    def test_high_frequency_across_operations(self, mock_get_kb):
        """高频端点跨多个操作出现 → 基础设施"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = ["/current-user", "/access-log"]
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/current-user", "contexts": [
                "replay:create", "replay:query", "replay:update", "replay:delete"
            ]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/current-user" in result
        assert "/api/users" not in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_low_frequency_not_infra(self, mock_get_kb):
        """低频端点 → 非基础设施"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = ["/current-user"]
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/display-unit-tree", "contexts": ["replay:migrate"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
            {"pathname": "/api/roles", "contexts": ["replay:query"]},
            {"pathname": "/api/departments", "contexts": ["replay:update"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/display-unit-tree" not in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_init_only_endpoints(self, mock_get_kb):
        """只在 init 上下文出现的端点 → 基础设施"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = []
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/system-config", "contexts": ["init"]},
            {"pathname": "/api/theme", "contexts": ["init"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/system-config" in result
        assert "/api/theme" in result

    def test_empty_list(self):
        """空端点列表 → 空集合"""
        result = _identify_infrastructure_apis([], threshold=0.6)
        assert result == set()

    @patch("core.discovery.kb_loader.get_kb")
    def test_degraded_mode_low_total(self, mock_get_kb):
        """操作总数 ≤ 2 时降级为模式匹配策略"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = ["/current-user", "/access-log"]
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/current-user", "contexts": ["replay:create"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/current-user" in result
        assert "/api/users" not in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_degraded_mode_fallback_patterns(self, mock_get_kb):
        """降级模式下 KB 无 patterns 时使用兜底列表"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = []
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/current-user", "contexts": ["replay:create"]},
            {"pathname": "/access-log", "contexts": ["replay:create"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        # 兜底 generic_infra = ["/current-user", "/access-log", "/system-theme"]
        assert "/current-user" in result
        assert "/access-log" in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_no_replay_contexts(self, mock_get_kb):
        """无 replay context → 仅 init-only 路径返回"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = []
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/init-data", "contexts": ["init"]},
            {"pathname": "/api/config", "contexts": ["init"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        # 两个都是 init-only → 基础设施
        assert "/api/init-data" in result
        assert "/api/config" in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_mixed_init_and_replay(self, mock_get_kb):
        """混合 init 和 replay context"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = ["/current-user"]
        mock_get_kb.return_value = mock_kb

        endpoints = [
            {"pathname": "/api/current-user", "contexts": [
                "init", "replay:create", "replay:query", "replay:update"
            ]},
            {"pathname": "/api/theme", "contexts": ["init"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        # /api/current-user 在 3 个 replay 操作中出现（3/3=1.0 >= 0.6）
        assert "/api/current-user" in result
        # /api/theme 只在 init 出现
        assert "/api/theme" in result
        # /api/users 只在 1 个 replay 操作出现（1/3=0.33 < 0.6）
        assert "/api/users" not in result

    @patch("core.discovery.kb_loader.get_kb")
    def test_threshold_boundary(self, mock_get_kb):
        """阈值边界测试：刚好等于 threshold"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = []
        mock_get_kb.return_value = mock_kb

        # 3 个 replay context，端点出现在 2 个中（2/3 ≈ 0.67 >= 0.6）
        endpoints = [
            {"pathname": "/api/shared", "contexts": ["replay:create", "replay:update"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
            {"pathname": "/api/roles", "contexts": ["replay:query"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/shared" in result  # 2/3 >= 0.6

    @patch("core.discovery.kb_loader.get_kb")
    def test_below_threshold(self, mock_get_kb):
        """低于阈值不标记"""
        mock_kb = MagicMock()
        mock_kb.get_infrastructure_patterns.return_value = []
        mock_get_kb.return_value = mock_kb

        # 4 个 replay context，端点出现在 2 个中（2/4 = 0.5 < 0.6）
        endpoints = [
            {"pathname": "/api/shared", "contexts": ["replay:create", "replay:update"]},
            {"pathname": "/api/users", "contexts": ["replay:create"]},
            {"pathname": "/api/roles", "contexts": ["replay:query"]},
            {"pathname": "/api/depts", "contexts": ["replay:delete"]},
        ]
        result = _identify_infrastructure_apis(endpoints, threshold=0.6)
        assert "/api/shared" not in result  # 2/4 < 0.6
