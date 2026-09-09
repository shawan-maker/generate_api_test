"""
验证 Stage 5 前置 API 依赖链追踪与导出功能
"""
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

def test_export_artifacts():
    """测试导出模块基本功能"""
    from module_discovery.export_artifacts import (
        export_postman_collection,
        export_helpers,
        export_excel_params,
        export_all
    )

    # 创建测试 manifest
    test_manifest = {
        "manifest_version": "1.0",
        "module": {
            "name": "测试模块",
            "base_url": "https://example.com",
            "login_url": "https://example.com/login"
        },
        "auth_profile": {
            "header_name": "Authorization",
            "header_prefix": "Bearer ",
            "token_storage": "cookie",
            "cookie_token_key": "token"
        },
        "pre_apis": [
            {
                "id": "get_token",
                "name": "获取认证 Token",
                "pathname": "/api/auth/token",
                "method": "POST",
                "depends_on": [],
                "extracts": [
                    {"name": "token", "path": "data.token", "used_by": ["create_user"]}
                ]
            }
        ],
        "steps": [
            {
                "action": "create_user",
                "label": "创建用户",
                "api": {
                    "method": "POST",
                    "pathname": "/api/users"
                },
                "body_template": {
                    "username": "test_user",
                    "email": "test@example.com"
                },
                "body_field_roles": {
                    "username": {"role": "name"},
                    "email": {"role": "static"}
                },
                "extract": {"id": "data.id"}
            }
        ]
    }

    # 创建临时输出目录
    output_dir = Path(__file__).parent / "test_output"
    output_dir.mkdir(exist_ok=True)

    try:
        # 测试 Postman 导出
        postman_path = output_dir / "test.postman_collection.json"
        export_postman_collection(test_manifest, postman_path)
        assert postman_path.exists(), "Postman Collection 文件未生成"

        with open(postman_path, 'r', encoding='utf-8') as f:
            collection = json.load(f)

        assert "info" in collection
        assert "item" in collection
        assert collection["info"]["name"] == "测试模块 API 测试"
        print("✓ Postman Collection 导出成功")

        # 测试 helpers 导出
        helpers_path = output_dir / "helpers.py"
        export_helpers(test_manifest, helpers_path)
        assert helpers_path.exists(), "helpers.py 文件未生成"

        with open(helpers_path, 'r', encoding='utf-8') as f:
            helpers_code = f.read()

        assert "def get_token_by_cookie" in helpers_code
        assert "def gen_timestamp" in helpers_code
        print("✓ helpers.py 导出成功")

        # 测试 Excel 导出
        excel_path = output_dir / "test_params.xlsx"
        export_excel_params(test_manifest, excel_path)
        assert excel_path.exists(), "Excel 参数文件未生成"
        print("✓ Excel 参数文件导出成功")

        # 测试批量导出
        batch_dir = output_dir / "batch_export"
        results = export_all(test_manifest, batch_dir)

        assert "postman" in results
        assert "helpers" in results
        assert "excel" in results
        print("✓ 批量导出成功")

        print("\n✅ 所有导出测试通过")
        return True

    finally:
        # 清理测试输出
        import shutil
        if output_dir.exists():
            shutil.rmtree(output_dir)


def test_pre_api_tracking():
    """测试前置 API 依赖链追踪"""
    from module_discovery.analyze_flow import trace_pre_api_dependencies

    # 模拟前置 API 候选
    pre_api_candidates = [
        {
            "id": "current_user",
            "pathname": "/api/users/current",
            "method": "GET",
            "response_sample": {
                "response_body": json.dumps({
                    "data": {
                        "id": "user_123",
                        "tenantId": "tenant_456",
                        "name": "Test User"
                    }
                })
            },
            "extracted_fields": [
                {"name": "user_id", "path": "data.id", "type": "string"},
                {"name": "tenant_id", "path": "data.tenantId", "type": "string"}
            ]
        },
        {
            "id": "policies",
            "pathname": "/api/policies",
            "method": "GET",
            "response_sample": {
                "response_body": json.dumps({
                    "data": {
                        "list": [
                            {"id": "policy_001", "name": "Admin"},
                            {"id": "policy_002", "name": "User"}
                        ]
                    }
                })
            },
            "extracted_fields": [
                {"name": "policy_ids", "path": "data.list[*].id", "type": "array"}
            ]
        }
    ]

    # 模拟核心 API
    core_apis = {
        "create": [
            {
                "pathname": "/api/users",
                "method": "POST",
                "request_body_sample": json.dumps({
                    "tenantId": "tenant_456",
                    "policyIds": ["policy_001", "policy_002"],
                    "username": "new_user"
                })
            }
        ]
    }

    # 模拟响应样本
    response_samples = {}

    # 执行追踪
    result = trace_pre_api_dependencies(
        core_apis=core_apis,
        response_samples=response_samples,
        pre_api_candidates=pre_api_candidates,
        context_fields={},
        id_field_details={}
    )

    # 验证结果
    assert result is not None, "追踪返回 None"
    assert "pre_apis" in result, "缺少 pre_apis 字段"
    assert "field_resolutions" in result, "缺少 field_resolutions 字段"

    pre_apis = result["pre_apis"]
    assert len(pre_apis) > 0, "未识别到前置 API"

    # 验证字段解析
    field_resolutions = result["field_resolutions"]
    assert "create.tenantId" in field_resolutions, "未解析 tenantId"

    tenant_resolution = field_resolutions["create.tenantId"]
    assert tenant_resolution["source"] == "current_user.tenant_id", \
        f"tenantId 解析来源错误: {tenant_resolution['source']}"

    print("✓ 前置 API 依赖链追踪测试通过")
    print(f"  - 识别到 {len(pre_apis)} 个前置 API")
    print(f"  - 解析了 {len(field_resolutions)} 个字段依赖")

    return True


def test_runtime_pre_api_execution():
    """测试运行时前置 API 执行"""
    from lib.runtime.test_runtime import TestRunner

    # 创建带前置 API 的 manifest
    manifest = {
        "manifest_version": "1.0",
        "module": {
            "name": "测试模块",
            "base_url": "https://example.com"
        },
        "response_contract": {},
        "auth_profile": {},
        "pre_apis": [
            {
                "id": "init_config",
                "name": "初始化配置",
                "pathname": "/api/config",
                "method": "GET",
                "depends_on": [],
                "extracts": [
                    {"name": "region", "path": "data.region"}
                ]
            }
        ]
    }

    runner = TestRunner(manifest)

    # 验证 execute_pre_apis 方法存在
    assert hasattr(runner, "execute_pre_apis"), "TestRunner 缺少 execute_pre_apis 方法"

    # 验证 _extract_with_array_index 方法存在
    assert hasattr(runner, "_extract_with_array_index"), \
        "TestRunner 缺少 _extract_with_array_index 方法"

    # 测试数组索引提取
    test_data = {
        "data": {
            "list": [
                {"id": "item_1"},
                {"id": "item_2"}
            ]
        }
    }

    result = runner._extract_with_array_index(test_data, "data.list[0].id")
    assert result == "item_1", f"数组索引提取失败: {result}"

    result = runner._extract_with_array_index(test_data, "data.list[1].id")
    assert result == "item_2", f"数组索引提取失败: {result}"

    print("✓ 运行时前置 API 执行测试通过")
    return True


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print("=" * 60)
    print("Stage 5 功能验证测试")
    print("=" * 60)

    tests = [
        ("导出模块", test_export_artifacts),
        ("前置 API 追踪", test_pre_api_tracking),
        ("运行时执行", test_runtime_pre_api_execution)
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\n[TEST] {name}")
        print("-" * 60)
        try:
            if test_func():
                passed += 1
        except Exception as e:
            print(f"[FAIL] 测试失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
