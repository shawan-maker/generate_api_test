"""
test_value_chain.py — value_chain 模块单元测试

测试目标：
- _parse_body: 请求体解析
- _looks_like_identifier: 标识符判断
- _compute_static_segments: 静态路径段计算
- _find_value_in_response / _search_value_recursive: 响应值搜索
- _extract_dynamic_values: 动态值提取
- _flatten_responses: 响应展平
- _get_ultimate_source: 最终来源提取
- _find_state_value: 状态字段查找
- _extract_entity_with_fallback: 信封键回退提取
- _discover_envelope_keys / _discover_success_check / _discover_list_structure: 响应约定发现
- _discover_response_contract: 完整响应约定
- build_value_chain: 集成测试
- _derive_dependencies / _derive_state_rules: 依赖与状态推导
"""
import json
import pytest

from core.discovery.stage3.value_chain import (
    _parse_body,
    _looks_like_identifier,
    _compute_static_segments,
    _find_value_in_response,
    _search_value_recursive,
    _extract_dynamic_values,
    _flatten_responses,
    _get_ultimate_source,
    _find_state_value,
    _extract_entity_with_fallback,
    _discover_envelope_keys,
    _discover_success_check,
    _discover_list_structure,
    _discover_response_contract,
    build_value_chain,
    _derive_dependencies,
    _derive_state_rules,
)
from core.discovery import const


# ============================================================
# _parse_body: 请求体解析
# ============================================================

class TestParseBody:
    """测试 _parse_body 多种输入格式"""

    def test_endpoint_with_request_body_sample_string(self):
        """endpoint dict 含 request_body_sample（JSON 字符串）→ 解析为 dict"""
        ep = {"request_body_sample": '{"name": "test", "age": 10}'}
        result = _parse_body(ep)
        assert result == {"name": "test", "age": 10}

    def test_endpoint_with_bodies_list(self):
        """endpoint dict 含 bodies 列表 → 解析第一个元素"""
        ep = {"bodies": ['{"id": "abc12345"}']}
        result = _parse_body(ep)
        assert result == {"id": "abc12345"}

    def test_endpoint_request_body_sample_takes_priority(self):
        """request_body_sample 优先于 bodies"""
        ep = {
            "request_body_sample": '{"from": "sample"}',
            "bodies": ['{"from": "bodies"}'],
        }
        result = _parse_body(ep)
        assert result == {"from": "sample"}

    def test_raw_json_string(self):
        """原始 JSON 字符串 → 解析"""
        result = _parse_body('{"key": "value"}')
        assert result == {"key": "value"}

    def test_raw_dict_without_endpoint_keys(self):
        """原始 dict 无 request_body_sample/bodies → 视为 endpoint，返回 {}"""
        # _parse_body 将所有 dict 视为 endpoint dict，无对应键则返回 {}
        assert _parse_body({"key": "value"}) == {}

    def test_raw_list_passthrough(self):
        """原始 list → 直接返回（list 不走 endpoint 分支）"""
        lst = [{"a": 1}]
        assert _parse_body(lst) == [{"a": 1}]

    def test_invalid_json_string(self):
        """无效 JSON 字符串 → {}"""
        assert _parse_body("not valid json {{{") == {}

    def test_none_input(self):
        """None → {}"""
        assert _parse_body(None) == {}

    def test_empty_dict_endpoint(self):
        """空 endpoint dict → {}"""
        assert _parse_body({}) == {}

    def test_empty_bodies_list(self):
        """bodies 为空列表 → {}"""
        assert _parse_body({"bodies": []}) == {}

    def test_json_string_resolves_to_scalar(self):
        """JSON 字符串解析为标量（非 dict/list）→ {}"""
        assert _parse_body('"just a string"') == {}

    def test_json_array_string(self):
        """JSON 数组字符串 → 解析为 list"""
        result = _parse_body('[1, 2, 3]')
        assert result == [1, 2, 3]


# ============================================================
# _looks_like_identifier: 标识符判断
# ============================================================

class TestLooksLikeIdentifier:
    """测试 _looks_like_identifier 各种输入"""

    def test_long_alphanumeric_mixed(self):
        """长字母数字混合（≥8 字符且含字母+数字）→ True"""
        assert _looks_like_identifier("abc12345def") is True

    def test_uuid_like(self):
        """UUID 风格字符串 → True"""
        assert _looks_like_identifier("a1b2c3d4-e5f6-7890") is True

    def test_pure_digit_16plus(self):
        """纯数字 ≥16 位 → True"""
        assert _looks_like_identifier("1234567890123456") is True

    def test_short_string(self):
        """短字符串（<6 字符）→ False"""
        assert _looks_like_identifier("abc") is False

    def test_short_digit(self):
        """短纯数字（<8 位）→ False"""
        assert _looks_like_identifier("12345") is False

    def test_url_http(self):
        """HTTP URL → False"""
        assert _looks_like_identifier("http://example.com/api") is False

    def test_url_https(self):
        """HTTPS URL → False"""
        assert _looks_like_identifier("https://example.com") is False

    def test_path_like(self):
        """路径形式（/开头）→ False"""
        assert _looks_like_identifier("/api/v1/users") is False

    def test_boolean_true_string(self):
        """'true' → False"""
        assert _looks_like_identifier("true") is False

    def test_boolean_false_string(self):
        """'false' → False"""
        assert _looks_like_identifier("false") is False

    def test_none_value(self):
        """None → False"""
        assert _looks_like_identifier(None) is False

    def test_bool_value(self):
        """布尔值 → False"""
        assert _looks_like_identifier(True) is False

    def test_float_value(self):
        """浮点数 → False"""
        assert _looks_like_identifier(3.14) is False

    def test_pure_alpha_long(self):
        """纯字母（无数字）→ False"""
        assert _looks_like_identifier("abcdefgh") is False

    def test_pure_digit_under_16(self):
        """纯数字 8~15 位 → False"""
        assert _looks_like_identifier("12345678") is False

    def test_int_type_short(self):
        """int 类型短数字 → False"""
        assert _looks_like_identifier(42) is False

    def test_null_string(self):
        """'null' → False"""
        assert _looks_like_identifier("null") is False

    def test_zero_string(self):
        """'0' → False"""
        assert _looks_like_identifier("0") is False

    def test_one_string(self):
        """'1' → False"""
        assert _looks_like_identifier("1") is False


# ============================================================
# _compute_static_segments: 静态路径段计算
# ============================================================

class TestComputeStaticSegments:
    """测试 _compute_static_segments 数据驱动静态段识别"""

    def test_empty_list_returns_fallback(self):
        """空列表 → 返回通用框架保留词"""
        result = _compute_static_segments([])
        assert "api" in result
        assert "v1" in result
        assert "v2" in result

    def test_common_segments_identified(self):
        """多个调用中有共同段 → 被识别为静态"""
        calls = [
            {"pathname": "/api/v1/users/aaa11111"},
            {"pathname": "/api/v1/users/bbb22222"},
            {"pathname": "/api/v1/groups/ccc33333"},
            {"pathname": "/api/v1/groups/ddd44444"},
        ]
        result = _compute_static_segments(calls)
        assert "api" in result
        assert "v1" in result
        assert "users" in result
        assert "groups" in result

    def test_single_call_all_segments_static(self):
        """单个调用 → 所有段都算静态（阈值 max(2, 1*0.5)=2 但只有1个pathname, 所以 ≥2 才命中 → 仅 universal_static）"""
        calls = [{"pathname": "/api/v1/users"}]
        result = _compute_static_segments(calls)
        # universal_static 总在结果中
        assert "api" in result
        assert "v1" in result

    def test_dynamic_ids_not_static(self):
        """动态 ID 不出现在多数 pathname 中 → 不是静态段"""
        calls = [
            {"pathname": "/api/v1/users/abc12345"},
            {"pathname": "/api/v1/users/def67890"},
            {"pathname": "/api/v1/groups/xyz11111"},
        ]
        result = _compute_static_segments(calls)
        assert "abc12345" not in result
        assert "def67890" not in result
        assert "xyz11111" not in result


# ============================================================
# _find_value_in_response: 响应中查找值
# ============================================================

class TestFindValueInResponse:
    """测试 _find_value_in_response 路径查找"""

    def test_simple_nested_dict(self):
        """简单嵌套 dict → 正确路径"""
        body = json.dumps({"entity": {"id": "12345"}})
        paths = _find_value_in_response(body, "12345")
        assert "entity.id" in paths

    def test_value_in_array(self):
        """数组中的值 → 路径含索引"""
        body = json.dumps({"data": {"items": ["a", "target_val", "c"]}})
        paths = _find_value_in_response(body, "target_val")
        assert "data.items[1]" in paths

    def test_value_not_found(self):
        """值不存在 → 空列表"""
        body = json.dumps({"entity": {"id": "999"}})
        paths = _find_value_in_response(body, "nonexistent")
        assert paths == []

    def test_deeply_nested(self):
        """深层嵌套 → 正确深层路径"""
        body = json.dumps({"a": {"b": {"c": {"d": "deep_val"}}}})
        paths = _find_value_in_response(body, "deep_val")
        assert "a.b.c.d" in paths

    def test_empty_body(self):
        """空 body → 空列表"""
        assert _find_value_in_response("", "val") == []
        assert _find_value_in_response(None, "val") == []

    def test_invalid_json_body(self):
        """无效 JSON → 空列表"""
        assert _find_value_in_response("not json", "val") == []

    def test_multiple_matches(self):
        """同一值在多处出现 → 返回多个路径"""
        body = json.dumps({"x": {"id": "dup"}, "y": {"ref": "dup"}})
        paths = _find_value_in_response(body, "dup")
        assert len(paths) == 2
        assert "x.id" in paths
        assert "y.ref" in paths

    def test_numeric_value_match(self):
        """数值类型匹配（int/float 转字符串比较）"""
        body = json.dumps({"entity": {"count": 42}})
        paths = _find_value_in_response(body, "42")
        assert "entity.count" in paths

    def test_dict_body_directly(self):
        """body 已经是 dict（非字符串）也能处理"""
        body = {"entity": {"id": "abc123"}}
        paths = _find_value_in_response(body, "abc123")
        assert "entity.id" in paths


# ============================================================
# _search_value_recursive: 递归搜索
# ============================================================

class TestSearchValueRecursive:
    """测试 _search_value_recursive 递归搜索细节"""

    def test_flat_dict(self):
        """扁平 dict 中搜索"""
        results = []
        _search_value_recursive({"key": "val"}, "val", "", results, max_depth=5)
        assert results == ["key"]

    def test_nested_dict_in_array(self):
        """数组中嵌套 dict"""
        obj = {"items": [{"id": "x1"}, {"id": "target"}]}
        results = []
        _search_value_recursive(obj, "target", "", results, max_depth=5)
        assert "items[1].id" in results

    def test_max_depth_limit(self):
        """超过 max_depth 停止搜索"""
        obj = {"a": {"b": {"c": {"d": "deep"}}}}
        results = []
        _search_value_recursive(obj, "deep", "", results, max_depth=2)
        assert results == []

    def test_empty_values_skipped(self):
        """空字符串值被跳过"""
        obj = {"key": "", "other": "target"}
        results = []
        _search_value_recursive(obj, "", "", results, max_depth=5)
        assert results == []

    def test_bool_and_float_values(self):
        """布尔和浮点值也能匹配"""
        obj = {"flag": True, "rate": 3.14}
        results_true = []
        _search_value_recursive(obj, "True", "", results_true, max_depth=5)
        assert "flag" in results_true

        results_float = []
        _search_value_recursive(obj, "3.14", "", results_float, max_depth=5)
        assert "rate" in results_float


# ============================================================
# _extract_dynamic_values: 动态值提取
# ============================================================

class TestExtractDynamicValues:
    """测试 _extract_dynamic_values 提取逻辑"""

    def test_body_with_id_like_values(self):
        """body 中有 ID 类值 → 被提取"""
        body = {"groupId": "abc12345def", "name": "test"}
        result = _extract_dynamic_values(body=body, static_segments={"api", "v1"})
        assert "groupId" in result
        assert result["groupId"] == "abc12345def"

    def test_query_params_with_ids(self):
        """query 参数中有 ID → 被提取，键名加 _query_ 前缀"""
        result = _extract_dynamic_values(
            query_params={"parentId": "xyz98765432"},
            static_segments={"api"},
        )
        assert "_query_parentId" in result

    def test_pathname_dynamic_segments(self):
        """pathname 中动态段 → 被提取，键名加 _path_ 前缀"""
        result = _extract_dynamic_values(
            pathname="/api/v1/users/abc12345def",
            static_segments={"api", "v1", "users"},
        )
        assert "_path_abc12345" in result
        assert result["_path_abc12345"] == "abc12345def"

    def test_noise_values_excluded(self):
        """短值、URL 等噪声 → 不被提取"""
        body = {"name": "short", "url": "https://example.com", "flag": True}
        result = _extract_dynamic_values(body=body, static_segments=set())
        assert "name" not in result
        assert "url" not in result
        assert "flag" not in result

    def test_excluded_keys_from_const(self):
        """const.EXTRACT_EXCLUDE_KEYS 中的字段被排除"""
        body = {
            "pageNum": "12345678",
            "pageSize": "87654321",
            "sort": "abcdef12",
            "password": "secret12345678",
            "groupId": "real_id_12345",
        }
        result = _extract_dynamic_values(body=body, static_segments=set())
        assert "pageNum" not in result
        assert "pageSize" not in result
        assert "sort" not in result
        assert "password" not in result
        assert "groupId" in result

    def test_array_body_first_element(self):
        """body 是数组 → 提取第一个元素（如果像 ID）"""
        body = ["abc12345def", "other"]
        result = _extract_dynamic_values(body=body, static_segments=set())
        assert "_array_item" in result

    def test_list_field_in_body(self):
        """body 中有列表字段，第一个元素像 ID → 提取"""
        body = {"ids": ["abc12345xyz", "other"]}
        result = _extract_dynamic_values(body=body, static_segments=set())
        assert "ids" in result
        assert result["ids"] == "abc12345xyz"

    def test_none_static_segments_fallback(self):
        """static_segments=None 时使用默认集合"""
        result = _extract_dynamic_values(
            pathname="/api/v1/users/abc12345def",
            static_segments=None,
        )
        # api/v1 是默认静态段，users 和 ID 段不是
        assert "_path_abc12345" in result

    def test_query_params_excluded_keys(self):
        """query 参数中 EXTRACT_EXCLUDE_KEYS 被排除"""
        result = _extract_dynamic_values(
            query_params={"page": "1234567890ab", "token": "abcdef12345"},
            static_segments=set(),
        )
        assert "_query_page" not in result
        assert "_query_token" not in result


# ============================================================
# _flatten_responses: 响应展平
# ============================================================

class TestFlattenResponses:
    """测试 _flatten_responses 展平逻辑"""

    def test_basic_flatten(self):
        """基本展平：带 pathname 字段"""
        samples = {
            "/api/users": [
                {"status": 200, "body": '{"id":"1"}', "ts": 1.0, "context": "replay:创建", "method": "POST"},
            ],
            "/api/groups": [
                {"status": 200, "body": '{"id":"2"}', "ts": 2.0, "context": "replay:查询", "method": "GET"},
            ],
        }
        flat = _flatten_responses(samples)
        assert len(flat) == 2
        pathnames = {r["pathname"] for r in flat}
        assert pathnames == {"/api/users", "/api/groups"}

    def test_empty_samples(self):
        """空样本 → 空列表"""
        assert _flatten_responses({}) == []

    def test_multiple_samples_per_path(self):
        """同一路径多个样本 → 全部展平"""
        samples = {
            "/api/x": [
                {"status": 200, "body": "a", "ts": 1},
                {"status": 200, "body": "b", "ts": 2},
            ],
        }
        flat = _flatten_responses(samples)
        assert len(flat) == 2

    def test_missing_fields_default(self):
        """缺失字段使用默认值"""
        samples = {"/api/x": [{}]}
        flat = _flatten_responses(samples)
        assert flat[0]["ts"] == 0
        assert flat[0]["body"] == ""
        assert flat[0]["context"] == ""


# ============================================================
# _get_ultimate_source: 最终来源提取
# ============================================================

class TestGetUltimateSource:
    """测试 _get_ultimate_source 递归 sub_traces"""

    def test_simple_trace(self):
        """简单链（无 sub_traces）→ 返回最后一个节点"""
        trace = [{"step": "创建", "pathname": "/api/users", "response_path": "entity.id"}]
        result = _get_ultimate_source(trace)
        assert result["step"] == "创建"
        assert result["pathname"] == "/api/users"

    def test_with_sub_traces(self):
        """有 sub_traces → 递归到最深层"""
        trace = [
            {
                "step": "更新",
                "pathname": "/api/users/123",
                "response_path": "entity.id",
                "sub_traces": {
                    "request_field": "userId",
                    "request_value": "u001",
                    "trace_path": [
                        {"step": "创建", "pathname": "/api/users", "response_path": "entity.id"},
                    ],
                },
            }
        ]
        result = _get_ultimate_source(trace)
        assert result["step"] == "创建"

    def test_empty_trace(self):
        """空链 → 空 dict"""
        assert _get_ultimate_source([]) == {}

    def test_sub_traces_without_trace_path(self):
        """sub_traces 中无 trace_path → 停止递归"""
        trace = [
            {
                "step": "操作A",
                "pathname": "/a",
                "response_path": "x",
                "sub_traces": {"request_field": "f"},
            }
        ]
        result = _get_ultimate_source(trace)
        assert result["step"] == "操作A"


# ============================================================
# _find_state_value: 状态字段查找
# ============================================================

class TestFindStateValue:
    """测试 _find_state_value 通用状态字段查找"""

    def test_status_field(self):
        """entity 含 status → 找到"""
        result = _find_state_value({"status": "ACTIVE", "name": "test"})
        assert result is not None
        assert result["field"] == "status"
        assert result["value"] == "ACTIVE"

    def test_enabled_field(self):
        """entity 含 enabled（布尔）→ 找到"""
        result = _find_state_value({"enabled": True, "name": "test"})
        assert result is not None
        assert result["field"] == "enabled"
        assert result["value"] is True

    def test_state_field(self):
        """entity 含 state → 找到"""
        result = _find_state_value({"state": "LOCKED"})
        assert result["field"] == "state"
        assert result["value"] == "LOCKED"

    def test_no_state_fields(self):
        """entity 中无状态字段 → None"""
        assert _find_state_value({"name": "test", "age": 10}) is None

    def test_empty_string_state_ignored(self):
        """状态字段值为空字符串 → 忽略"""
        assert _find_state_value({"status": ""}) is None

    def test_locked_field(self):
        """entity 含 locked → 找到"""
        result = _find_state_value({"locked": False})
        assert result["field"] == "locked"
        assert result["value"] is False

    def test_priority_order(self):
        """多个状态字段同时存在 → 按优先级返回第一个"""
        result = _find_state_value({"state": "A", "status": "B"})
        assert result["field"] == "state"


# ============================================================
# _extract_entity_with_fallback: 信封键回退提取
# ============================================================

class TestExtractEntityWithFallback:
    """测试 _extract_entity_with_fallback 提取逻辑"""

    def test_standard_envelope_key(self):
        """标准信封键 entity → 提取"""
        body = {"entity": {"id": "1", "name": "test"}}
        result = _extract_entity_with_fallback(body)
        assert result == {"id": "1", "name": "test"}

    def test_data_envelope_key(self):
        """data 信封键 → 提取"""
        body = {"data": {"id": "2"}}
        result = _extract_entity_with_fallback(body)
        assert result == {"id": "2"}

    def test_custom_fallback_keys(self):
        """自定义回退键 → 提取"""
        body = {"response": {"id": "3"}}
        result = _extract_entity_with_fallback(body, fallback_keys=["response"])
        assert result == {"id": "3"}

    def test_empty_dict(self):
        """空 dict → 空 dict"""
        assert _extract_entity_with_fallback({}) == {}

    def test_non_dict_input(self):
        """非 dict 输入 → 空 dict"""
        assert _extract_entity_with_fallback("string") == {}
        assert _extract_entity_with_fallback(None) == {}

    def test_envelope_key_with_none_value(self):
        """信封键值为 None → 跳过，尝试下一个"""
        body = {"entity": None, "data": {"id": "4"}}
        result = _extract_entity_with_fallback(body)
        assert result == {"id": "4"}

    def test_no_matching_key(self):
        """无匹配的信封键 → 空 dict"""
        body = {"unknown_key": {"id": "5"}}
        result = _extract_entity_with_fallback(body)
        assert result == {}


# ============================================================
# _discover_envelope_keys: 信封键发现
# ============================================================

class TestDiscoverEnvelopeKeys:
    """测试 _discover_envelope_keys 频率统计"""

    def test_entity_most_frequent(self):
        """entity 出现最频繁 → 排第一"""
        samples = {
            "/a": [
                {"body": json.dumps({"entity": {"id": 1}, "success": True})},
                {"body": json.dumps({"entity": {"id": 2}, "success": True})},
            ],
            "/b": [
                {"body": json.dumps({"data": {"id": 3}, "success": True})},
            ],
        }
        keys = _discover_envelope_keys(samples)
        assert keys[0] == "entity"

    def test_no_samples_returns_defaults(self):
        """无样本 → 返回默认值"""
        keys = _discover_envelope_keys({})
        assert keys == const.ENVELOPE_KEY_DEFAULTS

    def test_no_envelope_keys_found(self):
        """响应中没有候选信封键 → 返回默认值"""
        samples = {"/a": [{"body": json.dumps({"foo": "bar"})}]}
        keys = _discover_envelope_keys(samples)
        assert keys == const.ENVELOPE_KEY_DEFAULTS

    def test_invalid_json_skipped(self):
        """无效 JSON 被跳过，不影响统计"""
        samples = {
            "/a": [{"body": "not json"}],
            "/b": [{"body": json.dumps({"entity": {"id": 1}})}],
        }
        keys = _discover_envelope_keys(samples)
        assert "entity" in keys


# ============================================================
# _discover_success_check: 成功判断发现
# ============================================================

class TestDiscoverSuccessCheck:
    """测试 _discover_success_check 成功模式检测"""

    def test_success_field_detected(self):
        """发现 success 字段 → field_and_absence 模式"""
        samples = {
            "/a": [
                {"body": json.dumps({"success": True, "entity": {}})},
                {"body": json.dumps({"success": True, "entity": {}})},
            ],
        }
        result = _discover_success_check(samples)
        assert result["type"] == "field_and_absence"
        assert result["success_field"] == "success"

    def test_no_success_field(self):
        """无 success 字段 → http_only 模式"""
        samples = {
            "/a": [{"body": json.dumps({"entity": {"id": 1}})}],
        }
        result = _discover_success_check(samples)
        assert result["type"] == "http_only"

    def test_code_field_detected(self):
        """code 字段也算成功判断字段"""
        samples = {
            "/a": [
                {"body": json.dumps({"code": 200, "data": {}})},
                {"body": json.dumps({"code": 200, "data": {}})},
            ],
        }
        result = _discover_success_check(samples)
        assert result["type"] == "field_and_absence"

    def test_error_field_detected(self):
        """同时检测到 error 字段"""
        samples = {
            "/a": [
                {"body": json.dumps({"success": True, "errorCode": None, "entity": {}})},
            ],
        }
        result = _discover_success_check(samples)
        assert result["type"] == "field_and_absence"
        assert result["error_field"] == "errorCode"

    def test_empty_samples(self):
        """空样本 → http_only"""
        result = _discover_success_check({})
        assert result["type"] == "http_only"


# ============================================================
# _discover_list_structure: 列表结构发现
# ============================================================

class TestDiscoverListStructure:
    """测试 _discover_list_structure 列表/总数键检测"""

    def test_list_and_total_detected(self):
        """检测到 list 和 total 键"""
        samples = {
            "/api/list": [
                {"body": json.dumps({
                    "entity": {"list": [{"id": 1}], "total": 10}
                })},
            ],
        }
        list_keys, total_keys = _discover_list_structure(samples, ["entity"])
        assert "list" in list_keys
        assert "total" in total_keys

    def test_records_and_total_count(self):
        """检测到 records 和 totalCount"""
        samples = {
            "/api/list": [
                {"body": json.dumps({
                    "data": {"records": [{"id": 1}], "totalCount": 5}
                })},
            ],
        }
        list_keys, total_keys = _discover_list_structure(samples, ["data"])
        assert "records" in list_keys
        assert "totalCount" in total_keys

    def test_no_list_found_returns_defaults(self):
        """未找到列表结构 → 返回默认值"""
        samples = {"/a": [{"body": json.dumps({"entity": {"id": 1}})}]}
        list_keys, total_keys = _discover_list_structure(samples, ["entity"])
        assert list_keys == const.LIST_KEY_DEFAULTS
        assert total_keys == const.TOTAL_KEY_DEFAULTS

    def test_envelope_not_dict_skipped(self):
        """信封值非 dict → 跳过"""
        samples = {
            "/a": [{"body": json.dumps({"entity": "just_string"})},],
        }
        list_keys, total_keys = _discover_list_structure(samples, ["entity"])
        assert list_keys == const.LIST_KEY_DEFAULTS


# ============================================================
# _discover_response_contract: 完整响应约定
# ============================================================

class TestDiscoverResponseContract:
    """测试 _discover_response_contract 组合结果"""

    def test_full_contract(self):
        """完整约定包含所有字段"""
        samples = {
            "/api/create": [
                {"body": json.dumps({"success": True, "entity": {"id": "1"}})},
            ],
            "/api/list": [
                {"body": json.dumps({"success": True, "entity": {"list": [], "total": 0}})},
            ],
        }
        contract = _discover_response_contract(samples)
        assert "envelope_keys" in contract
        assert "success_check" in contract
        assert "list_keys" in contract
        assert "total_keys" in contract
        assert "id_field" in contract
        assert contract["id_field"] == const.DEFAULT_ID_FIELD

    def test_empty_samples_contract(self):
        """空样本 → 默认约定"""
        contract = _discover_response_contract({})
        assert contract["envelope_keys"] == const.ENVELOPE_KEY_DEFAULTS
        assert contract["success_check"]["type"] == "http_only"


# ============================================================
# build_value_chain: 集成测试
# ============================================================

class TestBuildValueChain:
    """build_value_chain 集成测试"""

    def test_basic_chain(self):
        """基本场景：创建→查询，ID 追溯链"""
        core_apis = {
            "创建用户组": [{
                "method": "POST",
                "pathname": "/api/v1/groups",
                "request_body_sample": '{"name": "TestGroup"}',
                "query_params": {},
            }],
            "查询用户组": [{
                "method": "GET",
                "pathname": "/api/v1/groups/abc12345def",
                "request_body_sample": None,
                "query_params": {},
            }],
        }
        response_samples = {
            "/api/v1/groups": [{
                "status": 200,
                "body": json.dumps({"entity": {"id": "abc12345def", "name": "TestGroup"}}),
                "ts": 100.0,
                "context": "replay:创建用户组",
                "method": "POST",
            }],
        }
        all_calls = [
            {
                "method": "POST", "pathname": "/api/v1/groups",
                "body": '{"name": "TestGroup"}', "context": "replay:创建用户组",
                "ts": 99.0, "query_params": {},
            },
            {
                "method": "GET", "pathname": "/api/v1/groups/abc12345def",
                "body": "", "context": "replay:查询用户组",
                "ts": 200.0, "query_params": {},
            },
        ]
        result = build_value_chain(core_apis, response_samples, all_calls)
        assert "chains" in result
        assert "id_producer" in result
        assert "id_field_details" in result
        assert "injections" in result

    def test_empty_core_apis(self):
        """空 core_apis → 空 chains"""
        result = build_value_chain({}, {}, [])
        assert result["chains"] == {}
        assert result["id_producer"] is None

    def test_endpoints_with_no_dynamic_values(self):
        """请求中无动态值 → chains 为空"""
        core_apis = {
            "查询": [{
                "method": "GET",
                "pathname": "/api/v1/list",
                "request_body_sample": None,
                "query_params": {"page": "1"},
            }],
        }
        result = build_value_chain(core_apis, {}, [])
        assert result["chains"] == {}


# ============================================================
# _derive_dependencies: 依赖推导
# ============================================================

class TestDeriveDependencies:
    """测试 _derive_dependencies 从 value_chain 提取依赖"""

    def test_with_value_chain(self):
        """有 value_chain → 正确提取"""
        vc = {
            "id_producer": "创建",
            "id_field_details": {"id": {"source_action": "创建"}},
            "injections": {"groupId": {"source": "创建"}},
            "chains": {"查询.groupId": {}},
        }
        result = _derive_dependencies({}, {}, [], value_chain=vc)
        assert result["id_producer"] == "创建"
        assert result["id_field_details"] == {"id": {"source_action": "创建"}}
        assert result["injections"] == {"groupId": {"source": "创建"}}
        assert result["value_chain"] is vc

    def test_without_value_chain(self):
        """无 value_chain → 空结果"""
        result = _derive_dependencies({}, {}, [], value_chain=None)
        assert result["id_producer"] is None
        assert result["id_field_details"] == {}
        assert result["injections"] == {}

    def test_value_chain_missing_keys(self):
        """value_chain 中缺少某些键 → 使用默认值"""
        vc = {"id_producer": "创建"}
        result = _derive_dependencies({}, {}, [], value_chain=vc)
        assert result["id_producer"] == "创建"
        assert result["id_field_details"] == {}
        assert result["injections"] == {}


# ============================================================
# _derive_state_rules: 状态规则推导
# ============================================================

class TestDeriveStateRules:
    """测试 _derive_state_rules 跨 CRUD 状态推导"""

    def test_state_detected_from_create(self):
        """创建操作响应中有 status 字段 → 检测到状态规则"""
        core_apis = {
            "创建用户": [{
                "method": "POST",
                "pathname": "/api/users",
            }],
        }
        response_samples = {
            "/api/users": [{
                "body": json.dumps({"entity": {"id": "1", "status": "ACTIVE"}}),
            }],
        }
        rules = _derive_state_rules(core_apis, response_samples, id_producer="创建用户")
        assert rules["state_field"] == "status"
        assert "创建用户" in rules["values_by_crud"]
        assert rules["values_by_crud"]["创建用户"] == "ACTIVE"

    def test_no_state_fields_in_responses(self):
        """响应中无状态字段 → state_field 为 None"""
        core_apis = {
            "创建": [{"method": "POST", "pathname": "/api/items"}],
        }
        response_samples = {
            "/api/items": [{"body": json.dumps({"entity": {"id": "1", "name": "test"}})}],
        }
        rules = _derive_state_rules(core_apis, response_samples)
        assert rules["state_field"] is None

    def test_list_entity_state(self):
        """entity 是列表时，检查第一个元素的状态"""
        core_apis = {
            "查询": [{"method": "GET", "pathname": "/api/items"}],
        }
        response_samples = {
            "/api/items": [{
                "body": json.dumps({
                    "entity": [{"id": "1", "status": "ACTIVE"}, {"id": "2", "status": "LOCKED"}]
                }),
            }],
        }
        rules = _derive_state_rules(core_apis, response_samples)
        assert rules["state_field"] == "status"
        assert rules["values_by_crud"]["查询"] == "ACTIVE"

    def test_empty_core_apis(self):
        """空 core_apis → 空规则"""
        rules = _derive_state_rules({}, {})
        assert rules["state_field"] is None
        assert rules["values_by_crud"] == {}

    def test_invalid_json_body_skipped(self):
        """无效 JSON body → 跳过该样本"""
        core_apis = {
            "创建": [{"method": "POST", "pathname": "/api/x"}],
        }
        response_samples = {
            "/api/x": [{"body": "not valid json"}],
        }
        rules = _derive_state_rules(core_apis, response_samples)
        assert rules["state_field"] is None

    def test_after_create_with_id_producer(self):
        """有 id_producer 且在 state_values 中 → 设置 after_create"""
        core_apis = {
            "创建组": [{"method": "POST", "pathname": "/api/groups"}],
        }
        response_samples = {
            "/api/groups": [{
                "body": json.dumps({"entity": {"id": "g1", "state": "NORMAL"}}),
            }],
        }
        rules = _derive_state_rules(core_apis, response_samples, id_producer="创建组")
        assert rules.get("after_create") == "NORMAL"
