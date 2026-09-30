"""
test_field_classifier.py — field_classifier 模块单元测试

测试目标：
- _analyze_value_pattern: 值模式分析（hex hash / base64 / email / phone / text / static / None）
- _classify_value_type: 值类型分类（boolean / number / hex_id / uuid / numeric_id / string）
- _is_system_id_value: 系统 ID 判断
- is_noise_value: 噪声值过滤
- ValueIndex: 值索引的 add / lookup / lookup_all / 序列化
- build_value_index: 值索引构建
- classify_fields: 5 角色分类引擎
- classify_fields_recursive: 递归分类
- _get_nested_value: dot-path 嵌套取值
- _flatten_body_for_match: 扁平化
- _extract_by_path_generic: 通用路径提取
"""
import json
import pytest

from core.discovery.stage3.field_classifier import (
    _analyze_value_pattern,
    _classify_value_type,
    _is_system_id_value,
    is_noise_value,
    ValueIndex,
    build_value_index,
    classify_fields,
    classify_fields_recursive,
    _get_nested_value,
    _flatten_body_for_match,
    _extract_by_path_generic,
)


# ============================================================
# _analyze_value_pattern 值模式分析
# ============================================================

class TestAnalyzeValuePattern:
    """测试 _analyze_value_pattern 函数所有分支"""

    def test_hex_hash_32_chars(self):
        """32 位十六进制串 → generate/hex_hash"""
        result = _analyze_value_pattern("token", "a" * 32)
        assert result == {"role": "generate", "pattern": "hex_hash"}

    def test_hex_hash_64_chars(self):
        """64 位十六进制串 → generate/hex_hash"""
        result = _analyze_value_pattern("id", "42ffdba38c58484f9be2bc1adf1672e642ffdba38c58484f9be2bc1adf1672e6")
        assert result == {"role": "generate", "pattern": "hex_hash"}

    def test_hex_hash_mixed_case(self):
        """大小写混合的十六进制串 → generate/hex_hash"""
        result = _analyze_value_pattern("hash", "42FfDbA38C58484f9Be2bC1aDf1672e6")
        assert result == {"role": "generate", "pattern": "hex_hash"}

    def test_hex_hash_31_chars_returns_none(self):
        """31 位十六进制串不够长，不走 hex_hash 分支"""
        val = "a" * 31
        result = _analyze_value_pattern("token", val)
        # 31 字符 < 32，不匹配 hex_hash；可能落入其他分支或 None
        if result is not None:
            assert result.get("pattern") != "hex_hash"

    def test_base64_sensitive_phone(self):
        """50+ 字符 base64 值 + phone 字段名 → static/encrypted_sensitive"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        assert len(b64) >= 50
        result = _analyze_value_pattern("phone", b64)
        assert result == {"role": "static", "pattern": "encrypted_sensitive"}

    def test_base64_sensitive_mobile(self):
        """50+ 字符 base64 值 + mobile 字段名 → static/encrypted_sensitive"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        result = _analyze_value_pattern("mobileNo", b64)
        assert result == {"role": "static", "pattern": "encrypted_sensitive"}

    def test_base64_sensitive_email(self):
        """50+ 字符 base64 值 + email 字段名 → static/encrypted_sensitive"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        result = _analyze_value_pattern("emailAddr", b64)
        assert result == {"role": "static", "pattern": "encrypted_sensitive"}

    def test_base64_sensitive_password(self):
        """50+ 字符 base64 值 + password 字段名 → static/encrypted_sensitive"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        result = _analyze_value_pattern("password", b64)
        assert result == {"role": "static", "pattern": "encrypted_sensitive"}

    def test_base64_sensitive_pwd(self):
        """50+ 字符 base64 值 + pwd 字段名 → static/encrypted_sensitive"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        result = _analyze_value_pattern("userPwd", b64)
        assert result == {"role": "static", "pattern": "encrypted_sensitive"}

    def test_base64_non_sensitive(self):
        """50+ 字符 base64 值 + 非敏感字段名 → generate/base64_encrypted"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        result = _analyze_value_pattern("data", b64)
        assert result == {"role": "generate", "pattern": "base64_encrypted"}

    def test_base64_with_equals_padding(self):
        """含 = 填充的 base64 值 + 非敏感字段 → generate/base64_encrypted"""
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+="
        result = _analyze_value_pattern("payload", b64)
        assert result == {"role": "generate", "pattern": "base64_encrypted"}

    def test_email_pattern(self):
        """邮箱格式 → generate/email"""
        result = _analyze_value_pattern("contact", "user@example.com")
        assert result == {"role": "generate", "pattern": "email"}

    def test_email_with_subdomain(self):
        """带子域名的邮箱 → generate/email"""
        result = _analyze_value_pattern("email", "test@mail.example.co.jp")
        assert result == {"role": "generate", "pattern": "email"}

    def test_phone_11_digits(self):
        """11 位纯数字手机号 → generate/phone"""
        result = _analyze_value_pattern("tel", "13812345678")
        assert result == {"role": "generate", "pattern": "phone"}

    def test_phone_with_plus_prefix(self):
        """带 + 前缀的手机号 → generate/phone"""
        result = _analyze_value_pattern("phone", "+8613812345678")
        assert result == {"role": "generate", "pattern": "phone"}

    def test_phone_8_digits(self):
        """8 位数字（最短） → generate/phone"""
        result = _analyze_value_pattern("tel", "12345678")
        assert result == {"role": "generate", "pattern": "phone"}

    def test_short_text_with_letters(self):
        """2-50 字符含字母的短文本 → generate/text"""
        result = _analyze_value_pattern("field", "Hello World")
        assert result == {"role": "generate", "pattern": "text"}

    def test_short_text_chinese(self):
        """含中文的短文本 → generate/text"""
        result = _analyze_value_pattern("field", "测试数据")
        assert result == {"role": "generate", "pattern": "text"}

    def test_short_fixed_value(self):
        """≤10 字符无字母的短固定值 → static"""
        result = _analyze_value_pattern("type", "+86")
        assert result == {"role": "static"}

    def test_short_text_overrides_static(self):
        """含字母的短字符串走 text 而非 static（text 优先于 static）"""
        result = _analyze_value_pattern("type", "ACTIVE")
        assert result == {"role": "generate", "pattern": "text"}

    def test_short_single_char_returns_none(self):
        """单字符字符串：长度 < 2，不匹配 text 模式，≤10 → static"""
        result = _analyze_value_pattern("x", "A")
        assert result == {"role": "static"}

    def test_none_returns_none(self):
        """None 值 → None"""
        result = _analyze_value_pattern("field", None)
        assert result is None

    def test_empty_string_returns_none(self):
        """空字符串 → None"""
        result = _analyze_value_pattern("field", "")
        assert result is None

    def test_non_string_int_returns_none(self):
        """非字符串（整数） → None"""
        result = _analyze_value_pattern("field", 12345)
        assert result is None

    def test_non_string_bool_returns_none(self):
        """非字符串（布尔） → None"""
        result = _analyze_value_pattern("field", True)
        assert result is None

    def test_non_string_list_returns_none(self):
        """非字符串（列表） → None"""
        result = _analyze_value_pattern("field", [1, 2, 3])
        assert result is None


# ============================================================
# _classify_value_type 值类型分类
# ============================================================

class TestClassifyValueType:
    """测试 _classify_value_type 函数所有返回类型"""

    def test_bool_true(self):
        """True → boolean"""
        assert _classify_value_type(True) == "boolean"

    def test_bool_false(self):
        """False → boolean"""
        assert _classify_value_type(False) == "boolean"

    def test_int(self):
        """整数 → number"""
        assert _classify_value_type(42) == "number"

    def test_float(self):
        """浮点数 → number"""
        assert _classify_value_type(3.14) == "number"

    def test_zero(self):
        """0 → number"""
        assert _classify_value_type(0) == "number"

    def test_hex_id_32_chars(self):
        """32 位十六进制串 → hex_id"""
        assert _classify_value_type("42ffdba38c58484f9be2bc1adf1672e6") == "hex_id"

    def test_hex_id_uppercase(self):
        """大写十六进制串 → hex_id"""
        assert _classify_value_type("42FFDBA38C58484F9BE2BC1ADF1672E6") == "hex_id"

    def test_uuid_format(self):
        """标准 UUID 格式 → uuid"""
        assert _classify_value_type("550e8400-e29b-41d4-a716-446655440000") == "uuid"

    def test_numeric_id_8_digits(self):
        """8 位纯数字 → numeric_id"""
        assert _classify_value_type("12345678") == "numeric_id"

    def test_numeric_id_long(self):
        """长数字串 → numeric_id"""
        assert _classify_value_type("1234567890123456") == "numeric_id"

    def test_short_digit_string(self):
        """短于 8 位的数字串 → string"""
        assert _classify_value_type("12345") == "string"

    def test_regular_string(self):
        """普通字符串 → string"""
        assert _classify_value_type("hello world") == "string"

    def test_none_returns_string(self):
        """None → string"""
        assert _classify_value_type(None) == "string"

    def test_empty_string(self):
        """空字符串 → string"""
        assert _classify_value_type("") == "string"

    def test_list_returns_string(self):
        """列表（非 str/int/float） → string"""
        assert _classify_value_type([1, 2]) == "string"


# ============================================================
# _is_system_id_value 系统 ID 判断
# ============================================================

class TestIsSystemIdValue:
    """测试 _is_system_id_value 的 True/False 情况"""

    def test_hex_id(self):
        """十六进制 ID → True"""
        assert _is_system_id_value("42ffdba38c58484f9be2bc1adf1672e6") is True

    def test_uuid(self):
        """UUID → True"""
        assert _is_system_id_value("550e8400-e29b-41d4-a716-446655440000") is True

    def test_numeric_id(self):
        """长数字 ID → True"""
        assert _is_system_id_value("1234567890") is True

    def test_long_alphanumeric_id(self):
        """32+ 字符字母数字混合 ID → True"""
        assert _is_system_id_value("abcdef1234567890abcdef1234567890") is True

    def test_short_string(self):
        """短字符串 → False"""
        assert _is_system_id_value("hello") is False

    def test_pure_alpha_non_hex_long(self):
        """32+ 字符纯非hex字母（无数字） → False"""
        assert _is_system_id_value("g" * 40) is False

    def test_pure_hex_alpha_long(self):
        """32+ 字符纯 hex 字母（如 'a'） → True（匹配 hex_id）"""
        assert _is_system_id_value("a" * 40) is True

    def test_pure_digit_long(self):
        """32+ 字符纯数字 → True（numeric_id 已匹配）"""
        assert _is_system_id_value("1" * 40) is True

    def test_none(self):
        """None → False"""
        assert _is_system_id_value(None) is False

    def test_bool(self):
        """布尔值 → False"""
        assert _is_system_id_value(True) is False


# ============================================================
# is_noise_value 噪声值过滤
# ============================================================

class TestIsNoiseValue:
    """测试 is_noise_value 的各种情况"""

    def test_none(self):
        """None → True"""
        assert is_noise_value(None) is True

    def test_bool_true(self):
        """True → True"""
        assert is_noise_value(True) is True

    def test_bool_false(self):
        """False → True"""
        assert is_noise_value(False) is True

    def test_empty_string(self):
        """空字符串 → True"""
        assert is_noise_value("") is True

    def test_short_string_2_chars(self):
        """2 字符短字符串 → True"""
        assert is_noise_value("ab") is True

    def test_common_true(self):
        """'true' → True"""
        assert is_noise_value("true") is True

    def test_common_false(self):
        """'false' → True"""
        assert is_noise_value("false") is True

    def test_common_null(self):
        """'null' → True"""
        assert is_noise_value("null") is True

    def test_common_none_str(self):
        """'none' → True"""
        assert is_noise_value("none") is True

    def test_common_active(self):
        """'active' → True"""
        assert is_noise_value("active") is True

    def test_common_success(self):
        """'success' → True"""
        assert is_noise_value("success") is True

    def test_common_yes(self):
        """'yes' → True"""
        assert is_noise_value("yes") is True

    def test_common_enable(self):
        """'enable' → True"""
        assert is_noise_value("enable") is True

    def test_common_disabled(self):
        """'disabled' → True"""
        assert is_noise_value("disabled") is True

    def test_small_number_zero(self):
        """小数字 0 → True"""
        assert is_noise_value(0) is True

    def test_small_number_99(self):
        """小数字 99 → True"""
        assert is_noise_value(99) is True

    def test_small_number_string(self):
        """数字字符串 '42' → True"""
        assert is_noise_value("42") is True

    def test_string_100_not_noise(self):
        """数字 100 → False（≥ 100）"""
        assert is_noise_value(100) is False

    def test_digit_string_100(self):
        """数字字符串 '100' → False"""
        assert is_noise_value("100") is False

    def test_long_hex_not_noise(self):
        """长十六进制串 → False"""
        assert is_noise_value("42ffdba38c58484f9be2bc1adf1672e6") is False

    def test_meaningful_string(self):
        """有意义的字符串 → False"""
        assert is_noise_value("zhangsan@example.com") is False

    def test_meaningful_name(self):
        """人名 → False"""
        assert is_noise_value("zhangsan") is False

    def test_bracket_empty_list(self):
        """'[]' → True"""
        assert is_noise_value("[]") is True

    def test_bracket_empty_dict(self):
        """'{}' → True"""
        assert is_noise_value("{}") is True

    def test_male(self):
        """'male' → True"""
        assert is_noise_value("male") is True

    def test_female(self):
        """'female' → True"""
        assert is_noise_value("female") is True


# ============================================================
# ValueIndex 值索引
# ============================================================

class TestValueIndex:
    """测试 ValueIndex 类的 add / lookup / lookup_all / 序列化"""

    def _make_index(self):
        """构建一个包含若干条目的 ValueIndex"""
        vi = ValueIndex()
        vi.add("zhangsan", "get_user", "get_user.name", "name", 1,
               timestamp=1.0, context="init")
        vi.add("tenant_abc", "get_tenant", "get_tenant.tenantId", "tenantid", 1,
               timestamp=2.0, context="init")
        vi.add("dept_xyz", "get_dept", "get_dept.deptId", "deptid", 1,
               timestamp=3.0, context="replay:创建")
        return vi

    def test_add_and_lookup_basic(self):
        """基本的 add + lookup 流程"""
        vi = self._make_index()
        result = vi.lookup("zhangsan")
        assert result is not None
        assert result["source_api"] == "get_user"
        assert result["source_path"] == "get_user.name"

    def test_lookup_not_found(self):
        """查找不存在的值 → None"""
        vi = self._make_index()
        result = vi.lookup("nonexistent_value")
        assert result is None

    def test_lookup_list_value(self):
        """列表值：取第一个元素查找"""
        vi = self._make_index()
        result = vi.lookup(["zhangsan", "other"])
        assert result is not None
        assert result["source_api"] == "get_user"

    def test_lookup_empty_list(self):
        """空列表 → None"""
        vi = self._make_index()
        result = vi.lookup([])
        assert result is None

    def test_add_noise_value_ignored(self):
        """噪声值不会被添加到索引"""
        vi = ValueIndex()
        vi.add("true", "api1", "api1.status", "status", 1)
        vi.add(None, "api1", "api1.field", "field", 1)
        vi.add("", "api1", "api1.empty", "empty", 1)
        assert len(vi.index) == 0

    def test_to_dict(self):
        """to_dict 序列化"""
        vi = self._make_index()
        d = vi.to_dict()
        assert "index" in d
        assert isinstance(d["index"], dict)

    def test_from_dict(self):
        """from_dict 反序列化"""
        vi = self._make_index()
        d = vi.to_dict()
        vi2 = ValueIndex.from_dict(d)
        assert vi2.lookup("zhangsan") is not None
        assert vi2.lookup("zhangsan")["source_api"] == "get_user"

    def test_serialization_roundtrip(self):
        """序列化 + 反序列化 roundtrip"""
        vi = self._make_index()
        d = vi.to_dict()
        vi2 = ValueIndex.from_dict(d)
        # 验证所有原始值都可查找
        for val in ["zhangsan", "tenant_abc", "dept_xyz"]:
            assert vi2.lookup(val) is not None

    def test_from_dict_empty(self):
        """空 dict 反序列化"""
        vi = ValueIndex.from_dict({})
        assert vi.index == {}

    def test_lookup_field_name_match_scoring(self):
        """字段名匹配加分：多源时 field_leaf 与 request_field_name 一致的得分更高"""
        vi = ValueIndex()
        vi.add("same_value", "api_a", "api_a.otherField", "otherfield", 1, context="init")
        vi.add("same_value", "api_b", "api_b.tenantId", "tenantid", 1, context="init")
        # 当 request_field_name="tenantId" 时，api_b 应匹配（字段名一致 +30）
        result = vi.lookup("same_value", request_field_name="tenantId")
        assert result["source_api"] == "api_b"

    def test_lookup_path_depth_scoring(self):
        """路径深度：浅路径优先"""
        vi = ValueIndex()
        vi.add("val123", "api_deep", "api_deep.a.b.c.d", "d", 4, context="init")
        vi.add("val123", "api_shallow", "api_shallow.id", "id", 1, context="init")
        result = vi.lookup("val123")
        assert result["source_api"] == "api_shallow"

    def test_lookup_context_action_priority(self):
        """时序优先：当前操作窗口 +100 > init +50 > 其他 +10"""
        vi = ValueIndex()
        vi.add("shared_val", "api_init", "api_init.f", "f", 1, context="init")
        vi.add("shared_val", "api_create", "api_create.f", "f", 1, context="replay:创建")
        vi.add("shared_val", "api_other", "api_other.f", "f", 1, context="replay:删除")
        # 当前操作是"创建"时，api_create 应优先
        result = vi.lookup("shared_val", current_action="创建")
        assert result["source_api"] == "api_create"

    def test_lookup_exclude_sources(self):
        """exclude_sources 排除指定来源前缀"""
        vi = ValueIndex()
        vi.add("val_x", "create_body", "create_body.fieldA", "fielda", 0, context="create")
        vi.add("val_x", "get_api", "get_api.fieldA", "fielda", 1, context="init")
        # 排除 create_body 后应返回 get_api
        result = vi.lookup("val_x", exclude_sources={"create_body"})
        assert result is not None
        assert result["source_api"] == "get_api"

    def test_lookup_exclude_sources_all_excluded(self):
        """所有来源都被排除 → None"""
        vi = ValueIndex()
        vi.add("val_y", "create_body", "create_body.f", "f", 0, context="create")
        result = vi.lookup("val_y", exclude_sources={"create_body"})
        assert result is None

    def test_lookup_all_basic(self):
        """lookup_all 返回所有匹配条目"""
        vi = ValueIndex()
        vi.add("shared", "api_a", "api_a.f", "f", 1, context="init")
        vi.add("shared", "api_b", "api_b.f", "f", 1, context="init")
        results = vi.lookup_all("shared")
        assert len(results) == 2

    def test_lookup_all_not_found(self):
        """lookup_all 未找到 → 空列表"""
        vi = ValueIndex()
        results = vi.lookup_all("missing")
        assert results == []

    def test_lookup_all_list_value(self):
        """lookup_all 列表值取第一个元素"""
        vi = ValueIndex()
        vi.add("item1", "api_a", "api_a.f", "f", 1, context="init")
        results = vi.lookup_all(["item1", "item2"])
        assert len(results) == 1

    def test_lookup_all_empty_list(self):
        """lookup_all 空列表 → 空列表"""
        vi = ValueIndex()
        results = vi.lookup_all([])
        assert results == []

    def test_add_stores_api_info(self):
        """add 存储 api_info 字段"""
        vi = ValueIndex()
        api_data = {"id": "my_api", "method": "GET"}
        vi.add("some_val", "my_api", "my_api.f", "f", 1, api_info=api_data)
        result = vi.lookup("some_val")
        assert result["api_info"] == api_data

    def test_add_field_leaf_lowercased(self):
        """add 时 field_leaf 被转为小写"""
        vi = ValueIndex()
        vi.add("val123", "api1", "api1.TenantId", "TenantId", 1)
        result = vi.lookup("val123")
        assert result["field_leaf"] == "tenantid"


# ============================================================
# build_value_index 值索引构建
# ============================================================

class TestBuildValueIndex:
    """测试 build_value_index 函数"""

    def test_basic_with_pre_api(self):
        """基本构建：包含前置 API 响应"""
        pre_apis = [
            {
                "id": "get_user",
                "context": "init",
                "timestamp": 1.0,
                "response_sample": {
                    "response_body": json.dumps({"entity": {"name": "zhangsan", "tenantId": "t001"}})
                },
                "extracted_fields": [
                    {"path": "entity.name", "name": "entity_name"},
                    {"path": "entity.tenantId", "name": "entity_tenantId"},
                ],
            }
        ]
        create_body = {"userName": "zhangsan", "tenantId": "t001"}
        vi = build_value_index(pre_apis, create_body)
        assert vi.lookup("zhangsan") is not None
        assert vi.lookup("t001") is not None

    def test_with_create_body(self):
        """create_body 数据被索引"""
        create_body = {"userName": "testuser", "status": "ACTIVE"}
        vi = build_value_index([], create_body)
        # "testuser" 不是噪声值，应被索引
        assert vi.lookup("testuser") is not None

    def test_with_create_response(self):
        """create_response 数据被索引"""
        create_body = {"userName": "testuser"}
        create_response = {"entity": {"id": "new_id_123", "userName": "testuser"}}
        vi = build_value_index([], create_body, create_response)
        result = vi.lookup("new_id_123")
        assert result is not None
        assert result["source_api"] == "create"

    def test_empty_inputs(self):
        """空输入 → 空索引"""
        vi = build_value_index([], {}, {})
        assert len(vi.index) == 0

    def test_none_inputs(self):
        """None 输入 → 空索引"""
        vi = build_value_index(None, None, None)
        assert len(vi.index) == 0

    def test_pre_api_invalid_json(self):
        """无效 JSON 响应体被跳过"""
        pre_apis = [
            {
                "id": "bad_api",
                "context": "init",
                "timestamp": 1.0,
                "response_sample": {"response_body": "not valid json{{{"},
                "extracted_fields": [
                    {"path": "entity.name", "name": "name"},
                ],
            }
        ]
        vi = build_value_index(pre_apis, {})
        assert len(vi.index) == 0

    def test_pre_api_missing_fields(self):
        """缺少 path/name 的 extracted_fields 被跳过"""
        pre_apis = [
            {
                "id": "api1",
                "context": "init",
                "timestamp": 1.0,
                "response_sample": {"response_body": '{"data": {"id": "123"}}'},
                "extracted_fields": [
                    {"path": "", "name": "id"},       # 空 path
                    {"path": "data.id", "name": ""},   # 空 name
                ],
            }
        ]
        vi = build_value_index(pre_apis, {})
        assert len(vi.index) == 0

    def test_create_response_envelope_discovery(self):
        """create_response 自动发现 envelope key"""
        create_response = {"data": {"userId": "uid_999"}}
        vi = build_value_index([], {}, create_response)
        result = vi.lookup("uid_999")
        assert result is not None
        assert result["source_path"] == "create.userId"


# ============================================================
# classify_fields 5 角色分类
# ============================================================

class TestClassifyFields:
    """测试 classify_fields 的 5-pass 分类逻辑"""

    def _make_value_index(self):
        """构建一个简单的 ValueIndex"""
        vi = ValueIndex()
        vi.add("t001_xyz", "get_tenant", "get_tenant.tenantId", "tenantid", 1, context="init")
        vi.add("dept_abc", "get_dept", "get_dept.deptId", "deptid", 1, context="init")
        return vi

    def test_name_field(self):
        """Pass 1: 含 name 关键词的字段 → name"""
        vi = self._make_value_index()
        body = {"userName": "zhangsan", "otherField": "val"}
        roles = classify_fields(body, vi)
        assert roles["userName"]["role"] == "name"

    def test_title_field(self):
        """Pass 1: 含 title 关键词的字段 → name"""
        vi = self._make_value_index()
        body = {"title": "测试标题数据", "otherField": "val"}
        roles = classify_fields(body, vi)
        assert roles["title"]["role"] == "name"

    def test_label_field(self):
        """Pass 1: 含 label 关键词的字段 → name"""
        vi = self._make_value_index()
        body = {"displayName": "显示名称测试"}
        roles = classify_fields(body, vi)
        assert roles["displayName"]["role"] == "name"

    def test_name_field_too_short(self):
        """Pass 1: 值太短（≤1 字符）不匹配 name"""
        vi = self._make_value_index()
        body = {"userName": "A"}
        roles = classify_fields(body, vi)
        # 值长度 1，不满足 1 < len < 50
        assert roles["userName"]["role"] != "name"

    def test_mutable_field_description(self):
        """Pass 2: 含 description 关键词的字段 → mutable"""
        vi = self._make_value_index()
        body = {"description": "some description text here"}
        roles = classify_fields(body, vi)
        assert roles["description"]["role"] == "mutable"

    def test_mutable_field_remark(self):
        """Pass 2: 含 remark 关键词的字段 → mutable"""
        vi = self._make_value_index()
        body = {"remark": "备注内容说明"}
        roles = classify_fields(body, vi)
        assert roles["remark"]["role"] == "mutable"

    def test_mutable_field_memo(self):
        """Pass 2: 含 memo 关键词的字段 → mutable"""
        vi = self._make_value_index()
        body = {"memo": "memo content"}
        roles = classify_fields(body, vi)
        assert roles["memo"]["role"] == "mutable"

    def test_context_field_matched(self):
        """Pass 3: 值在 value_index 中匹配 → context"""
        vi = self._make_value_index()
        body = {"tenantId": "t001_xyz", "userName": "zhangsan"}
        roles = classify_fields(body, vi)
        assert roles["tenantId"]["role"] == "context"
        assert roles["tenantId"]["source"] == "get_tenant.tenantId"

    def test_generate_hex_hash(self):
        """Pass 4: 长十六进制串 → generate/hex_hash"""
        vi = ValueIndex()
        body = {"token": "a" * 32}
        roles = classify_fields(body, vi)
        assert roles["token"]["role"] == "generate"
        assert roles["token"]["pattern"] == "hex_hash"

    def test_generate_base64(self):
        """Pass 4: 长 base64 串 → generate/base64_encrypted"""
        vi = ValueIndex()
        b64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwx+/"
        body = {"data": b64}
        roles = classify_fields(body, vi)
        assert roles["data"]["role"] == "generate"
        assert roles["data"]["pattern"] == "base64_encrypted"

    def test_static_fallback(self):
        """Pass 5: 兜底 → static"""
        vi = ValueIndex()
        body = {"type": "ADMIN", "flag": "Y"}
        roles = classify_fields(body, vi)
        assert roles["type"]["role"] == "static"
        assert roles["flag"]["role"] == "static"

    def test_empty_body(self):
        """空 body → 空 dict"""
        vi = ValueIndex()
        roles = classify_fields({}, vi)
        assert roles == {}

    def test_none_body(self):
        """None body → 空 dict"""
        vi = ValueIndex()
        roles = classify_fields(None, vi)
        assert roles == {}

    def test_exclude_sources(self):
        """exclude_sources 排除自引用"""
        vi = ValueIndex()
        vi.add("shared_val", "create_body", "create_body.fieldA", "fielda", 0, context="create")
        vi.add("shared_val", "get_api", "get_api.fieldA", "fielda", 1, context="init")
        body = {"fieldA": "shared_val"}
        roles = classify_fields(body, vi, exclude_sources={"create_body"})
        assert roles["fieldA"]["role"] == "context"
        assert roles["fieldA"]["source"] == "get_api.fieldA"

    def test_priority_name_over_context(self):
        """name 优先于 context（Pass 1 > Pass 3）"""
        vi = ValueIndex()
        vi.add("zhangsan", "get_user", "get_user.userName", "username", 1, context="init")
        body = {"userName": "zhangsan"}
        roles = classify_fields(body, vi)
        # Pass 1 (name) 在 Pass 3 (context) 之前
        assert roles["userName"]["role"] == "name"

    def test_priority_mutable_over_context(self):
        """mutable 优先于 context（Pass 2 > Pass 3）"""
        vi = ValueIndex()
        vi.add("some text", "get_api", "get_api.description", "description", 1, context="init")
        body = {"description": "some text"}
        roles = classify_fields(body, vi)
        assert roles["description"]["role"] == "mutable"

    def test_generate_only_for_long_strings(self):
        """Pass 4 generate 仅对 > 20 字符的字符串生效"""
        vi = ValueIndex()
        body = {"code": "short_code_abc"}  # 14 chars, < 20
        roles = classify_fields(body, vi)
        assert roles["code"]["role"] == "static"

    def test_multiple_fields_classification(self):
        """多字段同时分类"""
        vi = self._make_value_index()
        body = {
            "userName": "zhangsan",
            "description": "description text here",
            "tenantId": "t001_xyz",
            "token": "a" * 40,
            "status": "1",
        }
        roles = classify_fields(body, vi)
        assert roles["userName"]["role"] == "name"
        assert roles["description"]["role"] == "mutable"
        assert roles["tenantId"]["role"] == "context"
        assert roles["token"]["role"] == "generate"
        assert roles["status"]["role"] == "static"


# ============================================================
# classify_fields_recursive 递归分类
# ============================================================

class TestClassifyFieldsRecursive:
    """测试 classify_fields_recursive 嵌套 dict 处理"""

    def test_flat_body(self):
        """扁平 body 与 classify_fields 结果一致"""
        vi = ValueIndex()
        body = {"userName": "zhangsan", "status": "ACTIVE"}
        roles = classify_fields_recursive(body, vi)
        assert roles["userName"]["role"] == "name"
        assert roles["status"]["role"] == "static"

    def test_nested_dict(self):
        """嵌套 dict 产生 dot-prefixed key"""
        vi = ValueIndex()
        body = {
            "passwordPolicy": {
                "tenantId": "t001",
                "maxLength": "10",
            },
            "userName": "zhangsan",
        }
        roles = classify_fields_recursive(body, vi)
        assert "userName" in roles
        assert "passwordPolicy.tenantId" in roles
        assert "passwordPolicy.maxLength" in roles

    def test_deeply_nested(self):
        """多层嵌套"""
        vi = ValueIndex()
        body = {
            "outer": {
                "inner": {
                    "userName": "deepuser",
                }
            }
        }
        roles = classify_fields_recursive(body, vi)
        assert "outer.inner.userName" in roles
        assert roles["outer.inner.userName"]["role"] == "name"

    def test_nested_context_match(self):
        """嵌套字段通过 value_index 匹配为 context"""
        vi = ValueIndex()
        vi.add("tid_nested", "get_tenant", "get_tenant.tenantId", "tenantid", 1, context="init")
        body = {"config": {"tenantId": "tid_nested"}}
        roles = classify_fields_recursive(body, vi)
        assert roles["config.tenantId"]["role"] == "context"


# ============================================================
# _get_nested_value dot-path 提取
# ============================================================

class TestGetNestedValue:
    """测试 _get_nested_value 函数"""

    def test_simple_key(self):
        """简单 key 取值"""
        assert _get_nested_value({"x": 2}, "x") == 2

    def test_nested_key(self):
        """嵌套 key 取值"""
        assert _get_nested_value({"a": {"b": 1}}, "a.b") == 1

    def test_missing_key(self):
        """不存在的 key → None"""
        assert _get_nested_value({"a": {"b": 1}}, "a.c") is None

    def test_deep_nested(self):
        """深层嵌套取值"""
        d = {"a": {"b": {"c": {"d": 42}}}}
        assert _get_nested_value(d, "a.b.c.d") == 42

    def test_non_dict_intermediate(self):
        """中间层非 dict → None"""
        assert _get_nested_value({"a": 42}, "a.b") is None

    def test_none_dict(self):
        """None dict → None"""
        assert _get_nested_value(None, "a") is None

    def test_empty_key(self):
        """空 key（无 dot）从 dict 取空字符串 key"""
        d = {"": "empty_key_val"}
        assert _get_nested_value(d, "") == "empty_key_val"

    def test_missing_top_level(self):
        """顶层 key 不存在 → None"""
        assert _get_nested_value({"a": 1}, "b") is None


# ============================================================
# _flatten_body_for_match 扁平化
# ============================================================

class TestFlattenBodyForMatch:
    """测试 _flatten_body_for_match 函数"""

    def test_flat_body(self):
        """扁平 body 直接展开"""
        body = {"userId": "def", "name": "test"}
        roles = {"userId": {"role": "static"}, "name": {"role": "name"}}
        result = _flatten_body_for_match(body, roles)
        assert ("userId", "def") in result
        assert ("name", "test") in result

    def test_nested_with_roles(self):
        """嵌套 dict + 有对应 dot-prefixed roles → 展开"""
        body = {"passwordPolicy": {"id": "abc"}, "userId": "def"}
        roles = {"passwordPolicy.id": {"role": "context"}, "userId": {"role": "static"}}
        result = _flatten_body_for_match(body, roles)
        assert ("userId", "def") in result
        assert ("passwordPolicy.id", "abc") in result

    def test_nested_without_roles(self):
        """嵌套 dict 但没有对应 dot-prefixed roles → 不展开"""
        body = {"config": {"key": "val"}, "userId": "def"}
        roles = {"userId": {"role": "static"}}
        result = _flatten_body_for_match(body, roles)
        # config 下的子字段不应出现
        keys = [k for k, v in result]
        assert "config.key" not in keys
        assert "userId" in keys

    def test_list_values_preserved(self):
        """列表值被保留"""
        body = {"tags": ["a", "b"], "id": "123"}
        roles = {"tags": {"role": "static"}, "id": {"role": "static"}}
        result = _flatten_body_for_match(body, roles)
        assert ("tags", ["a", "b"]) in result

    def test_non_dict_body(self):
        """非 dict body → 空列表"""
        result = _flatten_body_for_match("not a dict", {})
        assert result == []

    def test_none_body(self):
        """None body → 空列表"""
        result = _flatten_body_for_match(None, {})
        assert result == []

    def test_nested_list_values(self):
        """嵌套 dict 中的列表值被展开"""
        body = {"config": {"tags": [1, 2]}}
        roles = {"config.tags": {"role": "static"}}
        result = _flatten_body_for_match(body, roles)
        assert ("config.tags", [1, 2]) in result


# ============================================================
# _extract_by_path_generic 通用路径提取
# ============================================================

class TestExtractByPathGeneric:
    """测试 _extract_by_path_generic 函数"""

    def test_simple_path(self):
        """简单路径提取"""
        obj = {"name": "test"}
        assert _extract_by_path_generic(obj, "name") == "test"

    def test_nested_path(self):
        """嵌套路径提取"""
        obj = {"entity": {"id": "123"}}
        assert _extract_by_path_generic(obj, "entity.id") == "123"

    def test_array_index(self):
        """数组索引提取"""
        obj = {"entity": {"list": [{"id": "first"}, {"id": "second"}]}}
        assert _extract_by_path_generic(obj, "entity.list[0].id") == "first"

    def test_array_index_second(self):
        """数组第二个元素"""
        obj = {"items": [{"val": "a"}, {"val": "b"}]}
        assert _extract_by_path_generic(obj, "items[1].val") == "b"

    def test_wildcard_array(self):
        """通配符 [*] 取第一个元素"""
        obj = {"items": [{"id": "first"}, {"id": "second"}]}
        assert _extract_by_path_generic(obj, "items[*].id") == "first"

    def test_empty_path(self):
        """空路径 → None"""
        assert _extract_by_path_generic({"a": 1}, "") is None

    def test_none_obj(self):
        """None obj → None"""
        assert _extract_by_path_generic(None, "a.b") is None

    def test_missing_path(self):
        """不存在的路径 → None"""
        obj = {"a": {"b": 1}}
        assert _extract_by_path_generic(obj, "a.c.d") is None

    def test_out_of_range_index(self):
        """数组越界 → None"""
        obj = {"items": [{"id": "1"}]}
        assert _extract_by_path_generic(obj, "items[5].id") is None

    def test_wildcard_empty_array(self):
        """通配符 + 空数组 → None"""
        obj = {"items": []}
        assert _extract_by_path_generic(obj, "items[*].id") is None

    def test_wildcard_non_array(self):
        """通配符 + 非数组 → None"""
        obj = {"items": "not_array"}
        assert _extract_by_path_generic(obj, "items[*].id") is None

    def test_deep_nested_with_arrays(self):
        """深层嵌套含数组的路径"""
        obj = {
            "data": {
                "records": [
                    {"children": [{"name": "child0"}]},
                    {"children": [{"name": "child1"}]},
                ]
            }
        }
        assert _extract_by_path_generic(obj, "data.records[1].children[0].name") == "child1"

    def test_none_path(self):
        """None path → None"""
        assert _extract_by_path_generic({"a": 1}, None) is None
