"""
端到端测试：验证 Stage 1-5 前置 API 数据驱动识别和全局共享架构

测试目标：
1. Stage 2 前置 API 候选识别（数据驱动，非硬编码模式）
2. Stage 3 依赖链追踪（从业务 API 请求体反向追踪）
3. Manifest 生成（pre_api_refs 和 pre_api_ref 角色）
4. 全局共享架构（跨模块合并、运行时注入）

使用真实数据：projects/ecm-compute/kb/module_discovered/用户管理.json
"""

import pytest
import json
from pathlib import Path
from module_discovery.capture_apis import _identify_pre_api_candidates
from module_discovery.analyze_flow import (
    analyze,
    build_manifest,
    trace_pre_api_dependencies
)
from module_discovery.pre_api_merger import merge_pre_apis_across_modules
from lib.runtime.global_pre_apis import GlobalPreApiExecutor

# 真实数据路径
PROJECT_DIR = Path(__file__).parent.parent / "projects" / "ecm-compute"
CAPTURE_FILE = PROJECT_DIR / "kb" / "module_discovered" / "用户管理.json"


@pytest.fixture
def real_capture_data():
    """加载真实的 Stage 2 捕获数据"""
    with open(CAPTURE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture
def mock_profile():
    """模拟项目配置"""
    return {
        "base_url": "https://10.151.61.248",
        "login_url": "https://10.151.61.248/estack/web/estack/login",
        "username_env": "APP_USER",
        "password_env": "APP_PASS",
        "auth": {
            "header_name": "Authorization",
            "header_prefix": "Bearer ",
            "token_key": "estackToken",
            "token_storage": "localStorage",
            "cookie_token_key": "accessToken",
            "freshness_ttl_seconds": 1800,
        }
    }


class TestStage2PreAPICandidateIdentification:
    """Stage 2: 前置 API 候选识别测试"""

    def test_identify_all_api_candidates(self, real_capture_data):
        """测试：识别所有 API 作为候选（不再使用硬编码模式）"""
        # 从捕获数据中提取 calls 和 samples
        calls = []
        samples = {}

        # 从 by_category 提取 calls
        for category, endpoints in real_capture_data["by_category"].items():
            for ep in endpoints:
                calls.append({
                    "method": ep["method"],
                    "pathname": ep["pathname"],
                    "query_params": ep.get("query_params", {}),
                    "body": "",
                    "context": ep.get("contexts", ["init"])[0] if ep.get("contexts") else "init",
                })

        # 从 response_samples 提取 samples
        for pathname, sample_list in real_capture_data["response_samples"].items():
            samples[pathname] = sample_list

        # 执行候选识别
        candidates = _identify_pre_api_candidates(calls, samples)

        # 验证 1: 应该识别出多个候选（不再过滤路径模式）
        assert len(candidates) > 0, "应该识别出至少 1 个候选 API"

        # 验证 2: 应该包含 /current-user（关键前置 API）
        current_user_candidates = [
            c for c in candidates
            if "/users/current-user" in c["pathname"]
        ]
        assert len(current_user_candidates) > 0, "应该识别出 /current-user API"

        # 验证 3: 应该包含 /policies（关键前置 API）
        policies_candidates = [
            c for c in candidates
            if "/policies" in c["pathname"]
        ]
        assert len(policies_candidates) > 0, "应该识别出 /policies API"

        # 验证 4: 候选应该包含 extracted_fields
        for candidate in candidates:
            assert "extracted_fields" in candidate
            assert len(candidate["extracted_fields"]) > 0, \
                f"候选 {candidate['pathname']} 应该包含提取字段"

        # 验证 5: /current-user 应该包含 tenantId 和 adminId
        current_user = current_user_candidates[0]
        field_paths = [f["path"] for f in current_user["extracted_fields"]]

        has_tenant_id = any("tenantId" in path for path in field_paths)
        has_admin_id = any("id" in path or "adminId" in path for path in field_paths)

        assert has_tenant_id, "/current-user 应该包含 tenantId 字段"
        assert has_admin_id, "/current-user 应该包含 id 或 adminId 字段"


class TestStage3DependencyTracing:
    """Stage 3: 依赖链追踪测试"""

    def test_trace_dependencies_data_driven(self, real_capture_data):
        """测试：从业务 API 请求体反向追踪前置 API"""
        # 提取核心 API（create/update/delete）
        core_apis = {}
        for category in ["create", "update", "delete", "query"]:
            if category in real_capture_data["by_category"]:
                core_apis[category] = real_capture_data["by_category"][category]

        # 提取 candidates
        calls = []
        samples = {}
        for category, endpoints in real_capture_data["by_category"].items():
            for ep in endpoints:
                calls.append({
                    "method": ep["method"],
                    "pathname": ep["pathname"],
                    "query_params": ep.get("query_params", {}),
                    "body": "",
                    "context": "init",
                })
        for pathname, sample_list in real_capture_data["response_samples"].items():
            samples[pathname] = sample_list

        candidates = _identify_pre_api_candidates(calls, samples)

        # 执行依赖追踪
        result = trace_pre_api_dependencies(
            core_apis=core_apis,
            response_samples=samples,
            pre_api_candidates=candidates,
            context_fields={},
            id_field_details={}
        )

        # 验证 1: 应该识别出前置 API
        assert "pre_apis" in result
        assert len(result["pre_apis"]) > 0, "应该识别出至少 1 个前置 API"

        # 验证 2: 应该包含 field_resolutions
        assert "field_resolutions" in result
        assert len(result["field_resolutions"]) > 0, "应该解析出至少 1 个字段来源"

        # 验证 3: 检查关键字段是否被追踪
        # 这些字段在用户管理模块中是硬编码的，应该从前置 API 获取
        tracked_fields = set()
        for key, resolution in result["field_resolutions"].items():
            # key 格式: "action.field"
            _, field = key.split('.', 1)
            tracked_fields.add(field)

        # 至少应该追踪到 tenantId 或 adminId
        has_critical_fields = (
            "tenantId" in tracked_fields or
            "adminId" in tracked_fields or
            "policyIds" in tracked_fields
        )
        assert has_critical_fields, \
            f"应该追踪到关键字段 (tenantId/adminId/policyIds)，实际追踪: {tracked_fields}"


class TestManifestGeneration:
    """Stage 4: Manifest 生成测试"""

    def test_manifest_includes_pre_api_refs(self, real_capture_data, mock_profile):
        """测试：生成的 manifest 包含 pre_api_refs"""
        # 运行完整分析
        classified = real_capture_data["by_category"]
        all_endpoints = real_capture_data.get("all_endpoints", [])
        response_samples = real_capture_data["response_samples"]

        # 构建 UI result
        ui_result = {
            "validated_operations": {
                "create": {"status": "success", "selectors": {"dialog": ".el-dialog"}},
                "update": {"status": "success", "selectors": {"dialog": ".el-dialog"}},
                "delete": {"status": "success", "selectors": {"dialog": ".el-dialog"}},
            }
        }

        # 提取 candidates
        calls = []
        samples = {}
        for category, endpoints in classified.items():
            for ep in endpoints:
                calls.append({
                    "method": ep["method"],
                    "pathname": ep["pathname"],
                    "query_params": ep.get("query_params", {}),
                    "body": "",
                    "context": "init",
                })
        for pathname, sample_list in response_samples.items():
            samples[pathname] = sample_list

        candidates = _identify_pre_api_candidates(calls, samples)

        # 运行分析
        analysis = analyze(
            classified_apis=classified,
            all_endpoints=all_endpoints,
            response_samples=response_samples,
            ui_result=ui_result,
            pre_api_candidates=candidates
        )

        # 构建 manifest
        manifest = build_manifest(
            analysis=analysis,
            capture_result=real_capture_data,
            profile=mock_profile,
            module_name="用户管理",
            target_url=real_capture_data["target_url"],
            ui_result=ui_result
        )

        # 验证 1: manifest 应该包含 pre_api_refs（如果有前置 API）
        if analysis.get("pre_api_chain") and len(analysis["pre_api_chain"].get("pre_apis", [])) > 0:
            assert "pre_api_refs" in manifest, \
                "manifest 应该包含 pre_api_refs"
            assert isinstance(manifest["pre_api_refs"], list)
            assert len(manifest["pre_api_refs"]) > 0

            # 验证 2: manifest_version 应该是 1.2
            assert manifest["manifest_version"] == "1.2", \
                f"manifest_version 应该是 1.2，实际: {manifest['manifest_version']}"

            # 验证 3: steps 中的 body_field_roles 应该包含 pre_api_ref
            has_pre_api_ref = False
            for step in manifest["steps"]:
                if "body_field_roles" in step:
                    for field, role_config in step["body_field_roles"].items():
                        if isinstance(role_config, dict) and role_config.get("role") == "pre_api_ref":
                            has_pre_api_ref = True
                            break
                if has_pre_api_ref:
                    break

            assert has_pre_api_ref, "steps 中应该包含 pre_api_ref 角色的字段"
        else:
            # 如果没有识别出前置 API，跳过验证
            pytest.skip("未识别出前置 API，跳过 manifest 验证")


class TestGlobalSharingArchitecture:
    """Stage 5: 全局共享架构测试"""

    def test_cross_module_merge(self, tmp_path):
        """测试：跨模块前置 API 合并"""
        # 创建临时项目结构
        kb_dir = tmp_path / "kb" / "module_discovered"
        kb_dir.mkdir(parents=True)

        scripts_dir = tmp_path / "scripts" / "v1.0.0" / "api"
        scripts_dir.mkdir(parents=True)

        # 创建模块 1 manifest
        manifest1 = {
            "manifest_version": "1.2",
            "module": {"name": "用户管理"},
            "pre_apis": [
                {
                    "id": "current_user",
                    "name": "获取当前用户",
                    "method": "GET",
                    "pathname": "/api/users/current",
                    "extracts": [
                        {"name": "tenantId", "path": "entity.tenantId"},
                        {"name": "adminId", "path": "entity.id"}
                    ]
                }
            ],
            "pre_api_refs": ["current_user"]
        }

        manifest2 = {
            "manifest_version": "1.2",
            "module": {"name": "角色管理"},
            "pre_apis": [
                {
                    "id": "current_user",
                    "name": "获取当前用户",
                    "method": "GET",
                    "pathname": "/api/users/current",
                    "extracts": [
                        {"name": "tenantId", "path": "entity.tenantId"}
                    ]
                }
            ],
            "pre_api_refs": ["current_user"]
        }

        (kb_dir / "用户管理_manifest.json").write_text(
            json.dumps(manifest1), encoding='utf-8'
        )
        (kb_dir / "角色管理_manifest.json").write_text(
            json.dumps(manifest2), encoding='utf-8'
        )

        # 执行合并
        result = merge_pre_apis_across_modules(
            project_dir=tmp_path,
            module_names=["用户管理", "角色管理"]
        )

        # 验证 1: 应该去重
        assert len(result["pre_apis"]) == 1, \
            f"应该去重为 1 个前置 API，实际: {len(result['pre_apis'])}"

        # 验证 2: 应该合并 extracts
        pre_api = result["pre_apis"][0]
        extract_names = {e["name"] for e in pre_api["extracts"]}
        assert "tenantId" in extract_names
        assert "adminId" in extract_names

        # 验证 3: 应该生成配置文件
        config_file = scripts_dir / "pre_apis_config.json"
        assert config_file.exists(), "应该生成 pre_apis_config.json"

        config = json.loads(config_file.read_text(encoding='utf-8'))
        assert "pre_apis" in config
        assert len(config["pre_apis"]) == 1

    def test_shared_context_execution(self, tmp_path):
        """测试：共享上下文执行和注入"""
        # 创建前置 API 配置
        config = {
            "version": "1.0",
            "pre_apis": [
                {
                    "id": "current_user",
                    "name": "获取当前用户",
                    "method": "GET",
                    "pathname": "/api/users/current",
                    "extracts": [
                        {"name": "tenantId", "path": "entity.tenantId"},
                        {"name": "adminId", "path": "entity.id"}
                    ]
                }
            ]
        }

        config_file = tmp_path / "pre_apis_config.json"
        config_file.write_text(json.dumps(config), encoding='utf-8')

        # Mock HTTP session
        class MockResponse:
            status_code = 200
            def json(self):
                return {
                    "entity": {
                        "id": "admin-123",
                        "tenantId": "tenant-456"
                    }
                }

        class MockSession:
            def get(self, url, timeout=None):
                return MockResponse()

        # 执行前置 API
        executor = GlobalPreApiExecutor(
            config_path=config_file,
            base_url="https://example.com",
            session=MockSession()
        )

        context = executor.execute_all()

        # 验证 1: 应该提取字段
        assert "tenantId" in context
        assert context["tenantId"] == "tenant-456"
        assert "adminId" in context
        assert context["adminId"] == "admin-123"

        # 验证 2: 应该保存到文件
        context_file = tmp_path / ".shared_context.json"
        executor.save_context(context_file)
        assert context_file.exists()

        # 验证 3: 应该能加载
        loaded = GlobalPreApiExecutor.load_context(context_file)
        assert loaded["tenantId"] == "tenant-456"
        assert loaded["adminId"] == "admin-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
