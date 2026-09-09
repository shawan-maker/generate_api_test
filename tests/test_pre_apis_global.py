"""
测试前置 API 数据驱动识别和全局共享架构
"""
import pytest
import json
import tempfile
from pathlib import Path
from module_discovery.capture_apis import _identify_pre_api_candidates
from module_discovery.pre_api_merger import merge_pre_apis_across_modules
from lib.runtime.global_pre_apis import GlobalPreApiExecutor


def test_identify_pre_api_candidates_data_driven():
    """测试数据驱动的前置 API 识别（不再使用硬编码模式）"""
    # 模拟 Stage 2 捕获的 API 调用
    calls = [
        {
            "method": "GET",
            "pathname": "/estack/api/estack/draco/v1/users/current-user",
            "query_params": {},
            "body": "",
            "context": "init",
        },
        {
            "method": "GET",
            "pathname": "/estack/api/estack/draco/v1/policies",
            "query_params": {},
            "body": "",
            "context": "init",
        },
        {
            "method": "GET",
            "pathname": "/estack/api/estack/pegasi/v1/system-theme",
            "query_params": {},
            "body": "",
            "context": "init",
        },
        {
            "method": "POST",
            "pathname": "/estack/api/estack/draco/v1/users",
            "query_params": {},
            "body": '{"userName":"test"}',
            "context": "replay:create",
        },
    ]

    # 模拟响应样本（GET 请求保留完整响应体）
    samples = {
        "/estack/api/estack/draco/v1/users/current-user": [
            {
                "status": 200,
                "body": json.dumps({
                    "entity": {
                        "id": "admin-123",
                        "tenantId": "tenant-456",
                        "userName": "admin"
                    }
                })
            }
        ],
        "/estack/api/estack/draco/v1/policies": [
            {
                "status": 200,
                "body": json.dumps({
                    "entity": {
                        "list": [
                            {"id": "policy-789", "name": "default"},
                            {"id": "policy-abc", "name": "strict"}
                        ]
                    }
                })
            }
        ],
        "/estack/api/estack/pegasi/v1/system-theme": [
            {
                "status": 200,
                "body": json.dumps({
                    "entity": {"theme": "dark"}
                })
            }
        ],
    }

    candidates = _identify_pre_api_candidates(calls, samples)

    # 应该识别出 3 个 GET API 作为候选（不再过滤路径模式）
    assert len(candidates) == 3, f"Expected 3 candidates, got {len(candidates)}"

    # 验证候选结构
    paths = {c["pathname"] for c in candidates}
    assert "/estack/api/estack/draco/v1/users/current-user" in paths
    assert "/estack/api/estack/draco/v1/policies" in paths
    assert "/estack/api/estack/pegasi/v1/system-theme" in paths

    # POST 请求不应该被识别（只收集有响应的 API）
    assert not any(c["method"] == "POST" for c in candidates)

    # 验证提取的字段
    current_user_candidate = next(c for c in candidates if "current-user" in c["pathname"])
    field_names = {f["name"] for f in current_user_candidate["extracted_fields"]}
    assert "entity_id" in field_names or "entity.id" in str(current_user_candidate["extracted_fields"])
    assert "entity_tenantId" in field_names or "entity.tenantId" in str(current_user_candidate["extracted_fields"])


def test_global_pre_apis_executor():
    """测试全局前置 API 执行器"""
    # 创建临时配置
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
                    {"name": "adminId", "path": "entity.id"},
                ]
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f)
        config_path = Path(f.name)

    try:
        # 创建 mock session
        class MockSession:
            def get(self, url, **kwargs):
                class MockResponse:
                    status_code = 200
                    def json(self):
                        return {
                            "entity": {
                                "id": "admin-123",
                                "tenantId": "tenant-456"
                            }
                        }
                return MockResponse()

        executor = GlobalPreApiExecutor(config_path, "https://example.com", MockSession())
        context = executor.execute_all()

        # 验证提取结果
        assert context["tenantId"] == "tenant-456"
        assert context["adminId"] == "admin-123"

        # 验证保存和加载
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            output_path = Path(f.name)

        executor.save_context(output_path)
        loaded = GlobalPreApiExecutor.load_context(output_path)

        assert loaded["tenantId"] == "tenant-456"
        assert loaded["adminId"] == "admin-123"

        output_path.unlink()
    finally:
        config_path.unlink()


def test_pre_api_merger_dedup():
    """测试跨模块前置 API 去重合并"""
    # 创建临时项目目录
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        kb_dir = project_dir / "kb" / "module_discovered"
        kb_dir.mkdir(parents=True)

        # 创建两个模块的 manifest，包含相同的前置 API
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
                        {"name": "tenantId", "path": "entity.tenantId"}
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
                        {"name": "adminId", "path": "entity.id"}
                    ]
                }
            ],
            "pre_api_refs": ["current_user"]
        }

        (kb_dir / "用户管理_manifest.json").write_text(json.dumps(manifest1))
        (kb_dir / "角色管理_manifest.json").write_text(json.dumps(manifest2))

        # 执行合并
        result = merge_pre_apis_across_modules(project_dir, ["用户管理", "角色管理"])

        # 验证去重
        assert len(result["pre_apis"]) == 1, "应该去重为 1 个前置 API"

        pre_api = result["pre_apis"][0]
        assert pre_api["id"] == "current_user"

        # 验证字段合并（两个模块的 extracts 应该合并）
        extract_names = {e["name"] for e in pre_api["extracts"]}
        assert "tenantId" in extract_names
        assert "adminId" in extract_names

        # 验证文件生成
        assert (project_dir / "kb" / "pre_apis_discovered.json").exists()
        assert (project_dir / "scripts" / "v1.0.0" / "api" / "pre_apis_config.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
