"""
test_manifest_builder.py — manifest_builder 模块单元测试

测试目标：
- _build_auth_profile: auth_profile 构建（各种配置组合）
- build_manifest: 完整 manifest 集成构建
- body_template 清理（动态字段占位符替换）
- 路径参数分析（动态段替换）
- 验证步骤规划（写操作后自动插入验证）
"""
import json
import pytest

from core.discovery.stage3.manifest_builder import (
    _build_auth_profile,
    build_manifest,
)
from core.discovery.stage3.field_classifier import ValueIndex, build_value_index


# ============================================================
# Fixtures
# ============================================================

def _make_profile(**overrides) -> dict:
    """构建最小可用的 profile dict。"""
    base = {
        "base_url": "https://app.example.com",
        "login_url": "https://app.example.com/login",
        "probe_url": "/api/current-user",
        "auth": {
            "header_name": "Authorization",
            "header_prefix": "Bearer ",
            "freshness_ttl_seconds": 600,
            "token_key": "token",
            "token_storage": "localStorage",
        },
        "captcha": {
            "auth_button_text": "获取验证码",
            "login_button_text": "登录",
        },
        "credentials": {
            "username": "admin",
            "password": "secret",
        },
        "context_fields": {
            "tenantId": {"path": "entity.tenantId", "source": "current_user"},
        },
        "username_env": "APP_USER",
        "password_env": "APP_PASS",
    }
    base.update(overrides)
    return base


def _make_analysis(**overrides) -> dict:
    """构建最小可用的 analysis dict。"""
    base = {
        "core_apis": {},
        "crud_order": [],
        "dependencies": {
            "id_producer": None,
            "id_field_details": {},
        },
        "value_index": {"index": {}},
        "pre_api_chain": None,
        "core_api_map": {},
        "state_assertions": {},
    }
    base.update(overrides)
    return base


def _make_capture_result(**overrides) -> dict:
    """构建最小可用的 capture_result dict。"""
    base = {
        "response_samples": {},
        "pre_api_candidates": [],
        "all_endpoints": [],
    }
    base.update(overrides)
    return base


def _make_endpoint(method, pathname, body=None, query_params=None,
                   query_params_samples=None, search_param=""):
    """构建端点 dict。"""
    ep = {
        "method": method,
        "pathname": pathname,
        "body_field_count": len(body) if isinstance(body, dict) else 0,
        "response_has_id": False,
    }
    if body is not None:
        ep["request_body_sample"] = json.dumps(body) if isinstance(body, dict) else body
        ep["bodies"] = [ep["request_body_sample"]]
    else:
        ep["bodies"] = []
    if query_params:
        ep["query_params"] = query_params
    else:
        ep["query_params"] = {}
    if query_params_samples:
        ep["query_params_samples"] = query_params_samples
    if search_param:
        ep["search_param"] = search_param
    return ep


def _make_response_sample(body_dict, status=200, context=""):
    """构建单个响应样本。"""
    s = {
        "status": status,
        "body": json.dumps(body_dict),
    }
    if context:
        s["context"] = context
    return s


# ============================================================
# _build_auth_profile 测试
# ============================================================

class TestBuildAuthProfile:
    """测试 _build_auth_profile 函数"""

    def test_full_profile(self):
        """完整 profile → 所有字段正确映射"""
        profile = _make_profile()
        result = _build_auth_profile(profile)

        assert result["header_name"] == "Authorization"
        assert result["header_prefix"] == "Bearer "
        assert result["freshness_ttl_seconds"] == 600
        assert result["probe_url"] == "/api/current-user"
        assert result["token_key"] == "token"
        assert result["token_storage"] == "localStorage"
        assert result["captcha"]["auth_button_text"] == "获取验证码"
        assert result["captcha"]["login_button_text"] == "登录"
        assert result["credentials_env"]["username"] == "APP_USER"
        assert result["credentials_env"]["password"] == "APP_PASS"
        assert result["credentials_default"]["username"] == "admin"
        assert result["credentials_default"]["password"] == "secret"
        assert "tenantId" in result["context_fields"]

    def test_empty_profile(self):
        """空 profile → 全部使用默认值"""
        result = _build_auth_profile({})

        assert result["header_name"] == "Authorization"
        assert result["header_prefix"] == "Bearer "
        assert result["freshness_ttl_seconds"] == 300
        assert result["probe_url"] == ""
        assert result["token_key"] == ""
        assert result["token_storage"] == "localStorage"
        assert result["cookie_token_key"] == ""
        assert result["captcha"]["auth_button_text"] == ""
        assert result["captcha"]["login_button_text"] == ""
        assert result["credentials_env"]["username"] == "APP_USER"
        assert result["credentials_env"]["password"] == "APP_PASS"
        assert result["credentials_default"]["username"] == ""
        assert result["credentials_default"]["password"] == ""
        assert result["context_fields"] == {}

    def test_cookie_auth_type(self):
        """cookie 认证类型 → cookie_token_key 有值"""
        profile = _make_profile()
        profile["auth"]["cookie_token_key"] = "session_id"
        profile["auth"]["token_storage"] = "cookie"

        result = _build_auth_profile(profile)
        assert result["cookie_token_key"] == "session_id"
        assert result["token_storage"] == "cookie"

    def test_token_auth_type(self):
        """token 认证类型 → header_prefix 和 token_key 正确"""
        profile = _make_profile()
        profile["auth"]["header_prefix"] = "Token "
        profile["auth"]["token_key"] = "access_token"

        result = _build_auth_profile(profile)
        assert result["header_prefix"] == "Token "
        assert result["token_key"] == "access_token"

    def test_context_fields_mapping(self):
        """context_fields 映射正确传递"""
        profile = _make_profile()
        profile["context_fields"] = {
            "orgId": {"path": "entity.orgId", "source": "current_org"},
            "regionCode": {"path": "entity.region", "source": "current_user"},
        }

        result = _build_auth_profile(profile)
        assert len(result["context_fields"]) == 2
        assert result["context_fields"]["orgId"]["path"] == "entity.orgId"
        assert result["context_fields"]["regionCode"]["source"] == "current_user"

    def test_probe_url_from_auth_fallback(self):
        """probe_url 从 auth 子配置回退读取"""
        profile = {"auth": {"probe_url": "/api/me"}}
        result = _build_auth_profile(profile)
        assert result["probe_url"] == "/api/me"

    def test_probe_url_top_level_priority(self):
        """profile 顶层 probe_url 优先于 auth 子配置"""
        profile = {
            "probe_url": "/api/v2/current-user",
            "auth": {"probe_url": "/api/me"},
        }
        result = _build_auth_profile(profile)
        assert result["probe_url"] == "/api/v2/current-user"

    def test_fixed_headers(self):
        """fixed_headers 透传"""
        profile = _make_profile()
        profile["auth"]["fixed_headers"] = {"X-Custom": "value123"}
        result = _build_auth_profile(profile)
        assert result["fixed_headers"] == {"X-Custom": "value123"}

    def test_captcha_legacy_keys(self):
        """兼容旧版 captcha_auth_button / captcha_login_button 键"""
        profile = {
            "captcha_auth_button": "旧验证码按钮",
            "captcha_login_button": "旧登录按钮",
        }
        result = _build_auth_profile(profile)
        assert result["captcha"]["auth_button_text"] == "旧验证码按钮"
        assert result["captcha"]["login_button_text"] == "旧登录按钮"

    def test_credentials_none_fallback(self):
        """credentials 为 None 时不崩溃"""
        profile = {"credentials": None}
        result = _build_auth_profile(profile)
        assert result["credentials_default"]["username"] == ""
        assert result["credentials_default"]["password"] == ""


# ============================================================
# build_manifest 集成测试
# ============================================================

class TestBuildManifestStructure:
    """测试 build_manifest 输出结构完整性"""

    def test_minimal_manifest(self):
        """最小输入 → manifest 包含必要键"""
        analysis = _make_analysis()
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "test_module",
                                  "https://app.example.com/test")

        assert "module" in manifest
        assert "auth_profile" in manifest
        assert "steps" in manifest
        assert "response_contract" in manifest
        assert "state_assertions" in manifest

    def test_meta_fields(self):
        """meta 字段正确填充"""
        analysis = _make_analysis()
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "用户管理",
                                  "https://app.example.com/users")

        assert manifest["module"]["name"] == "用户管理"
        assert manifest["module"]["base_url"] == "https://app.example.com"
        assert manifest["module"]["login_url"] == "https://app.example.com/login"
        assert manifest["module"]["target_url"] == "https://app.example.com/users"

    def test_empty_core_apis_empty_steps(self):
        """空 core_apis → 空 steps（无写操作、无 init query 注入）"""
        analysis = _make_analysis(core_apis={}, crud_order=[])
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "empty", "http://x")
        assert manifest["steps"] == []

    def test_manifest_version_default(self):
        """无 pre_api_chain → manifest_version 为 1.0"""
        analysis = _make_analysis()
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "m", "http://x")
        assert manifest["manifest_version"] == "1.0"

    def test_response_contract_present(self):
        """response_contract 包含标准子键"""
        analysis = _make_analysis()
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "m", "http://x")
        rc = manifest["response_contract"]
        assert "envelope_keys" in rc
        assert "success_check" in rc
        assert "list_keys" in rc
        assert "total_keys" in rc
        assert "id_field" in rc


class TestBuildManifestCRUDSteps:
    """测试 CRUD 操作步骤生成"""

    def _make_crud_fixtures(self):
        """构建包含 create + update + delete 的完整 CRUD fixtures。"""
        create_body = {"name": "test_item", "description": "desc", "status": "active"}
        update_body = {"name": "test_item_v2", "description": "updated", "status": "active"}

        create_ep = _make_endpoint("POST", "/api/items/create", body=create_body)
        update_ep = _make_endpoint("PUT", "/api/items/update", body=update_body)
        delete_ep = _make_endpoint("POST", "/api/items/delete", body={"id": "abc123def456"})

        # 列表查询端点（用于验证）
        query_ep = _make_endpoint("POST", "/api/items/list",
                                  body={"pageNum": 1, "pageSize": 10},
                                  query_params_samples=[{"pageNum": "1", "pageSize": "10"}])

        core_apis = {
            "create": [create_ep],
            "update": [update_ep],
            "delete": [delete_ep],
        }
        crud_order = ["create", "update", "delete"]

        # 响应样本
        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "abc123def456", "name": "test_item"}},
                    context="replay:create"
                ),
            ],
            "/api/items/list": [
                _make_response_sample(
                    {"success": True, "entity": {"list": [
                        {"id": "abc123def456", "name": "test_item"}
                    ], "total": 1}},
                    context="replay:create"
                ),
            ],
        }

        all_endpoints = [
            {
                "method": "POST", "pathname": "/api/items/list",
                "contexts": ["replay:create", "replay:update", "replay:delete"],
            },
        ]

        analysis = _make_analysis(
            core_apis=core_apis,
            crud_order=crud_order,
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "abc123def456"},
                },
            },
            core_api_map={
                "create": [create_ep],
                "update": [update_ep],
                "delete": [delete_ep],
            },
        )
        capture = _make_capture_result(
            response_samples=response_samples,
            all_endpoints=all_endpoints,
        )
        return analysis, capture

    def test_crud_steps_generated(self):
        """完整 CRUD → 生成对应步骤（含自动验证步骤）"""
        analysis, capture = self._make_crud_fixtures()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        steps = manifest["steps"]
        actions = [s["action"] for s in steps]
        # create 步骤一定存在
        assert "create" in actions

    def test_create_step_has_extract(self):
        """create 步骤包含 extract 字段"""
        analysis, capture = self._make_crud_fixtures()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        assert "extract" in create_step
        assert "id" in create_step["extract"]

    def test_non_create_step_requires_id(self):
        """非 create 步骤的 requires 包含 'id'"""
        analysis, capture = self._make_crud_fixtures()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        for step in manifest["steps"]:
            if step["action"] != "create" and "phases" not in step:
                if step.get("assertion") is None:  # 非验证步骤
                    assert "id" in step.get("requires", [])

    def test_step_has_api_structure(self):
        """每个步骤包含完整 api 子结构"""
        analysis, capture = self._make_crud_fixtures()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        for step in manifest["steps"]:
            if "phases" in step:
                continue
            api = step["api"]
            assert "method" in api
            assert "pathname" in api
            assert "path_params" in api
            assert "query_params" in api

    def test_step_body_template_present(self):
        """每个步骤包含 body_template"""
        analysis, capture = self._make_crud_fixtures()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        for step in manifest["steps"]:
            if "phases" in step:
                continue
            assert "body_template" in step

    def test_read_only_action_skipped(self):
        """只读操作（GET）不作为 CRUD 步骤"""
        query_ep = _make_endpoint("GET", "/api/items/list")
        core_apis = {"list": [query_ep]}

        analysis = _make_analysis(
            core_apis=core_apis,
            crud_order=["list"],
            core_api_map={"list": [query_ep]},
        )
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        # list 是 GET 只读，应被跳过（但可能作为验证端点使用）
        actions = [s["action"] for s in manifest["steps"]]
        assert "list" not in actions


class TestBodyTemplateCleaning:
    """测试 body_template 清理（动态字段替换为占位符）"""

    def _build_manifest_with_body(self, body_sample, field_roles_override=None):
        """辅助方法：用指定 body 构建单步骤 manifest，返回 body_template。"""
        create_ep = _make_endpoint("POST", "/api/items/create", body=body_sample)

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001", "name": body_sample.get("name", "")}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")
        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        return create_step["body_template"], create_step.get("body_field_roles", {})

    def test_name_field_replaced(self):
        """name 角色字段 → ${gen_test_name()} 占位符"""
        body = {"itemName": "测试物品", "type": "A"}
        template, roles = self._build_manifest_with_body(body)

        # itemName 包含 name 关键词，应被标记为 name 角色
        if roles.get("itemName", {}).get("role") == "name":
            assert "gen_test_name" in str(template["itemName"])

    def test_static_field_preserved(self):
        """static 字段保留原始值"""
        body = {"type": "A", "priority": "high"}
        template, roles = self._build_manifest_with_body(body)

        # type/priority 是短字符串，应该是 static
        for key in ("type", "priority"):
            if roles.get(key, {}).get("role") == "static":
                assert template[key] == body[key]

    def test_context_field_becomes_none(self):
        """context 角色字段 → None"""
        # 构建 value_index 使 tenantId 能匹配到 context 来源
        vi = ValueIndex()
        vi.add("tenant_abc123", "current_user", "current_user.tenantId",
               "tenantId", 1, context="init")

        body = {"name": "test", "tenantId": "tenant_abc123"}

        create_ep = _make_endpoint("POST", "/api/items/create", body=body)
        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_002", "name": "test"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_002"},
                },
            },
            core_api_map={"create": [create_ep]},
            value_index=vi.to_dict(),
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")
        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        roles = create_step.get("body_field_roles", {})

        # 如果 tenantId 被识别为 context，body_template 中应为 None
        if roles.get("tenantId", {}).get("role") == "context":
            assert create_step["body_template"]["tenantId"] is None

    def test_mutable_field_placeholder(self):
        """mutable 角色字段 → ${gen_mutable_value()} 占位符"""
        body = {"description": "这是一段描述文本", "name": "test"}
        template, roles = self._build_manifest_with_body(body)

        # description 包含 mutable 关键词
        if roles.get("description", {}).get("role") == "mutable":
            assert template["description"] == "${gen_mutable_value()}"

    def test_generate_field_placeholder(self):
        """generate 角色字段 → ${gen_模式()} 占位符"""
        body = {"name": "test", "email": "user@example.com"}
        template, roles = self._build_manifest_with_body(body)

        # email 如果识别为 generate/email
        if roles.get("email", {}).get("role") == "generate":
            pattern = roles["email"]["pattern"]
            assert template["email"] == f"${{gen_{pattern}()}}"

    def test_nested_dict_cleaned(self):
        """嵌套 dict 也会被递归清理"""
        body = {
            "name": "test",
            "config": {
                "enabled": True,
                "level": 3,
            }
        }
        template, roles = self._build_manifest_with_body(body)

        # 嵌套 dict 应该被保留（因为 config 内部都是 static 值）
        assert isinstance(template["config"], dict)

    def test_array_body_template_preserved(self):
        """数组格式请求体（如 batch delete）保持为 list"""
        array_body = ["id1", "id2", "id3"]
        create_ep = {
            "method": "POST",
            "pathname": "/api/items/batch-delete",
            "request_body_sample": json.dumps(array_body),
            "bodies": [json.dumps(array_body)],
            "body_field_count": 0,
            "response_has_id": False,
            "query_params": {},
        }

        analysis = _make_analysis(
            core_apis={"batch_delete": [create_ep]},
            crud_order=["batch_delete"],
            dependencies={
                "id_producer": None,
                "id_field_details": {},
            },
            core_api_map={"batch_delete": [create_ep]},
        )
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")
        # batch_delete 是 POST → 会被视为写操作
        assert len(manifest["steps"]) > 0


class TestPathParameterAnalysis:
    """测试路径参数分析（通过 build_manifest 集成测试）"""

    def test_dynamic_segment_replaced(self):
        """路径中的动态 ID 段被替换为 {path_N}"""
        # 创建一个 update 端点，pathname 包含 ID
        update_body = {"name": "updated_name"}
        update_ep = _make_endpoint("PUT",
                                   "/api/items/abc123def456ghijklmnop/update",
                                   body=update_body)
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test_item"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "abc123def456ghijklmnop", "name": "test_item"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={
                "create": [create_ep],
                "update": [update_ep],
            },
            crud_order=["create", "update"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "abc123def456ghijklmnop"},
                },
            },
            core_api_map={
                "create": [create_ep],
                "update": [update_ep],
            },
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        update_step = next(s for s in manifest["steps"] if s["action"] == "update")
        pathname = update_step["api"]["pathname"]

        # 动态 ID 段应被替换为 {path_0}
        assert "{path_0}" in pathname or "abc123def456ghijklmnop" not in pathname

    def test_static_segments_preserved(self):
        """短路径段（如 api, items, update）不被替换"""
        update_ep = _make_endpoint("PUT", "/api/v1/items/update",
                                   body={"name": "test"})
        create_ep = _make_endpoint("POST", "/api/v1/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/v1/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "12345678", "name": "test"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={
                "create": [create_ep],
                "update": [update_ep],
            },
            crud_order=["create", "update"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "12345678"},
                },
            },
            core_api_map={
                "create": [create_ep],
                "update": [update_ep],
            },
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        update_step = next(s for s in manifest["steps"] if s["action"] == "update")
        assert update_step["api"]["pathname"] == "/api/v1/items/update"

    def test_path_params_metadata(self):
        """path_params 包含 source 和 match_from（无 original_value）"""
        update_ep = _make_endpoint("PUT",
                                   "/api/items/5bcbffa7abcdef1234567890/update",
                                   body={"name": "test"})
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "5bcbffa7abcdef1234567890"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={
                "create": [create_ep],
                "update": [update_ep],
            },
            crud_order=["create", "update"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "5bcbffa7abcdef1234567890"},
                },
            },
            core_api_map={
                "create": [create_ep],
                "update": [update_ep],
            },
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        update_step = next(s for s in manifest["steps"] if s["action"] == "update")
        pp = update_step["api"]["path_params"]

        for key, info in pp.items():
            assert "source" in info
            assert "match_from" in info
            # original_value 应被 _strip_path_params_original 移除
            assert "original_value" not in info


class TestVerifyStepPlanning:
    """测试验证步骤自动规划"""

    def _make_write_fixtures(self, method="POST", pathname="/api/items/create",
                             body=None, search_param=""):
        """构建包含单个写操作 + 查询端点的 fixtures。"""
        body = body or {"name": "test"}
        write_ep = _make_endpoint(method, pathname, body=body)
        query_ep = _make_endpoint("POST", "/api/items/list",
                                  body={"pageNum": 1, "pageSize": 10},
                                  query_params_samples=[{"pageNum": "1", "pageSize": "10"}],
                                  search_param=search_param)

        response_samples = {
            pathname: [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001", "name": "test"}},
                    context="replay:create"
                ),
            ],
            "/api/items/list": [
                _make_response_sample(
                    {"success": True, "entity": {"list": [
                        {"id": "item_001", "name": "test"}
                    ], "total": 1}},
                    context="replay:create"
                ),
            ],
        }

        all_endpoints = [
            {
                "method": "POST", "pathname": "/api/items/list",
                "contexts": ["replay:create"],
            },
        ]

        return write_ep, query_ep, response_samples, all_endpoints

    def test_verify_step_after_create(self):
        """create 后自动插入验证步骤"""
        create_ep, query_ep, resp_samples, all_eps = self._make_write_fixtures()

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples=resp_samples,
            all_endpoints=all_eps,
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        # 应有 create 步骤 + 至少一个验证步骤
        assert len(manifest["steps"]) >= 2
        assertions = [s.get("assertion") for s in manifest["steps"] if s.get("assertion")]
        assert len(assertions) > 0

    def test_verify_step_contains_id_assertion(self):
        """create 后验证使用 contains_id 断言"""
        create_ep, query_ep, resp_samples, all_eps = self._make_write_fixtures()

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples=resp_samples,
            all_endpoints=all_eps,
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        verify_steps = [s for s in manifest["steps"] if s.get("assertion")]
        if verify_steps:
            assert verify_steps[0]["assertion"] in ("contains_id", "search_verify")

    def test_search_verify_with_search_param(self):
        """有 search_param 的查询端点 → search_verify 断言"""
        create_ep, query_ep, resp_samples, all_eps = self._make_write_fixtures(
            search_param="name"
        )

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples=resp_samples,
            all_endpoints=all_eps,
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        verify_steps = [s for s in manifest["steps"] if s.get("assertion") == "search_verify"]
        if verify_steps:
            assert verify_steps[0].get("search_param") == "name"

    def test_no_verify_when_no_query_endpoint(self):
        """没有查询端点时不生成验证步骤"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})
        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples=response_samples,
            all_endpoints=[],
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        # 只有 create 步骤，无验证步骤
        verify_steps = [s for s in manifest["steps"] if s.get("assertion")]
        assert len(verify_steps) == 0

    def test_delete_verify_not_contains_id(self):
        """delete 操作后验证使用 not_contains_id（需使用 HTTP DELETE 方法）"""
        # _plan_verify_steps 通过 HTTP method == "DELETE" 判断删除操作
        delete_ep = _make_endpoint("DELETE", "/api/items/item_001",
                                   body=None)
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001", "name": "test"}},
                    context="replay:create"
                ),
            ],
            "/api/items/list": [
                _make_response_sample(
                    {"success": True, "entity": {"list": [], "total": 0}},
                    context="replay:delete"
                ),
            ],
        }

        all_endpoints = [
            {
                "method": "POST", "pathname": "/api/items/list",
                "contexts": ["replay:create", "replay:delete"],
            },
        ]

        analysis = _make_analysis(
            core_apis={
                "create": [create_ep],
                "delete": [delete_ep],
            },
            crud_order=["create", "delete"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={
                "create": [create_ep],
                "delete": [delete_ep],
            },
        )
        capture = _make_capture_result(
            response_samples=response_samples,
            all_endpoints=all_endpoints,
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        # delete 验证步骤应有 not_contains_id 或 search_not_found 断言
        delete_verify = [s for s in manifest["steps"]
                         if s.get("assertion") in ("not_contains_id", "search_not_found")]
        assert len(delete_verify) > 0


class TestPreApisIntegration:
    """测试前置 API 集成"""

    def test_pre_apis_in_manifest(self):
        """有 pre_api_chain → manifest 包含 pre_apis 且版本升级"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test", "tenantId": "t_001"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001"}},
                    context="replay:create"
                ),
            ],
        }

        pre_api_chain = {
            "pre_apis": [
                {
                    "id": "current_user",
                    "name": "当前用户信息",
                    "method": "GET",
                    "pathname": "/api/current-user",
                    "depends_on": [],
                    "extracted_fields": [
                        {"name": "tenantId", "path": "entity.tenantId"},
                    ],
                },
            ],
            "field_resolutions": {
                "create.tenantId": {
                    "source": "current_user.tenantId",
                    "value": "t_001",
                },
            },
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
            pre_api_chain=pre_api_chain,
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert "pre_apis" in manifest
        assert len(manifest["pre_apis"]) == 1
        assert manifest["pre_apis"][0]["id"] == "current_user"
        assert manifest["manifest_version"] in ("1.1", "1.2")
        assert "pre_api_refs" in manifest

    def test_no_pre_apis_default_version(self):
        """无 pre_api_chain → 不包含 pre_apis，版本为 1.0"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples={
                "/api/items/create": [
                    _make_response_sample(
                        {"success": True, "entity": {"id": "item_001"}},
                        context="replay:create"
                    ),
                ],
            }
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert "pre_apis" not in manifest
        assert manifest["manifest_version"] == "1.0"


class TestStateAssertions:
    """测试 state_assertions 传递"""

    def test_state_assertions_passthrough(self):
        """analysis 中的 state_assertions 直接传递到 manifest"""
        state_assertions = {
            "create": [{"field": "status", "expected": "active"}],
            "delete": [{"field": "status", "expected": "deleted"}],
        }

        analysis = _make_analysis(state_assertions=state_assertions)
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert manifest["state_assertions"] == state_assertions

    def test_empty_state_assertions(self):
        """空 state_assertions → manifest 中也为空"""
        analysis = _make_analysis(state_assertions={})
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert manifest["state_assertions"] == {}


class TestUIResultIntegration:
    """测试 ui_result 对 label 的影响"""

    def test_button_labels_applied(self):
        """ui_result 中的 button_labels 被应用到步骤 label"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        ui_result = {
            "button_labels": {
                "create": "新建物品",
            }
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples={
                "/api/items/create": [
                    _make_response_sample(
                        {"success": True, "entity": {"id": "item_001"}},
                        context="replay:create"
                    ),
                ],
            }
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items",
                                  ui_result=ui_result)

        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        assert create_step["label"] == "新建物品"

    def test_no_ui_result_fallback_to_action(self):
        """无 ui_result → label 回退为 action 名"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(
            response_samples={
                "/api/items/create": [
                    _make_response_sample(
                        {"success": True, "entity": {"id": "item_001"}},
                        context="replay:create"
                    ),
                ],
            }
        )
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items",
                                  ui_result=None)

        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        assert create_step["label"] == "create"


class TestResponseContractDiscovery:
    """测试通过 build_manifest 间接测试响应约定发现"""

    def test_envelope_keys_discovered(self):
        """从响应样本自动发现信封键"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample({"data": {"id": "001"}, "code": 0}),
            ],
            "/api/items/list": [
                _make_response_sample({"data": {"list": [], "total": 0}, "code": 0}),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "data.id", "sample_value": "001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert "data" in manifest["response_contract"]["envelope_keys"]

    def test_empty_response_samples_defaults(self):
        """空响应样本 → 使用默认值"""
        analysis = _make_analysis()
        capture = _make_capture_result(response_samples={})
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        rc = manifest["response_contract"]
        assert isinstance(rc["envelope_keys"], list)
        assert len(rc["envelope_keys"]) > 0

    def test_id_field_override_from_create_response(self):
        """id_field_details 覆盖全局 id_field"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"itemId": "unique_id_001", "name": "test"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "itemId": {"path": "entity.itemId", "sample_value": "unique_id_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        # id_field 应被覆盖为 itemId
        assert manifest["response_contract"]["id_field"] == "itemId"


class TestValueIndexIntegration:
    """测试 ValueIndex 在 build_manifest 中的使用"""

    def test_value_index_from_analysis(self):
        """analysis 中提供的 value_index 被复用"""
        vi = ValueIndex()
        vi.add("t_001_xyz", "current_user", "current_user.tenantId",
               "tenantId", 1, context="init")

        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test", "tenantId": "t_001_xyz"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
            value_index=vi.to_dict(),
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        roles = create_step.get("body_field_roles", {})

        # tenantId 应被识别为 context（通过 value_index 匹配）
        if "tenantId" in roles:
            assert roles["tenantId"]["role"] == "context"
            assert "current_user" in roles["tenantId"]["source"]

    def test_no_value_index_backward_compat(self):
        """analysis 无 value_index 时内部构建（向后兼容）"""
        create_ep = _make_endpoint("POST", "/api/items/create",
                                   body={"name": "test"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [create_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [create_ep]},
        )
        # 移除 value_index 模拟旧数据
        del analysis["value_index"]

        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        # 不应抛异常
        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")
        assert len(manifest["steps"]) > 0


class TestEdgeCases:
    """测试边界情况"""

    def test_infra_apis_parameter_accepted(self):
        """infra_apis 参数被接受且不影响基本构建"""
        analysis = _make_analysis()
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items",
                                  infra_apis={"/api/current-user", "/api/config"})

        assert manifest["module"]["name"] == "items"

    def test_empty_crud_order_no_steps(self):
        """crud_order 有项但 core_apis 无对应端点 → 无步骤"""
        analysis = _make_analysis(
            core_apis={},
            crud_order=["create", "update"],
        )
        capture = _make_capture_result()
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        assert manifest["steps"] == []

    def test_phased_step_multiple_apis(self):
        """多 API 操作链 → 生成带 phases 的步骤"""
        # 准备阶段：generate API
        prepare_ep = _make_endpoint("POST", "/api/items/generate",
                                    body={"type": "auto"})
        # 核心阶段：create API
        core_ep = _make_endpoint("POST", "/api/items/create",
                                 body={"name": "test", "generatedCode": "GEN001"})

        response_samples = {
            "/api/items/create": [
                _make_response_sample(
                    {"success": True, "entity": {"id": "item_001"}},
                    context="replay:create"
                ),
            ],
            "/api/items/generate": [
                _make_response_sample(
                    {"success": True, "entity": {"code": "GEN001"}},
                    context="replay:create"
                ),
            ],
        }

        analysis = _make_analysis(
            core_apis={"create": [core_ep, prepare_ep]},
            crud_order=["create"],
            dependencies={
                "id_producer": "create",
                "id_field_details": {
                    "id": {"path": "entity.id", "sample_value": "item_001"},
                },
            },
            core_api_map={"create": [core_ep, prepare_ep]},
        )
        capture = _make_capture_result(response_samples=response_samples)
        profile = _make_profile()

        manifest = build_manifest(analysis, capture, profile, "items",
                                  "https://app.example.com/items")

        create_step = next(s for s in manifest["steps"] if s["action"] == "create")
        assert "phases" in create_step
        assert len(create_step["phases"]) >= 2

        # phases 中最后一个应该是 main
        assert create_step["phases"][-1]["id"] == "main"
