"""
部门管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:14:51
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/department

执行流程:
  1. 创建部门
  2. 搜索验证（创建部门后）
"""

import sys, json
from pathlib import Path

# 找到同目录的 lib/（脚本独立运行时使用）
_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))
from lib.runtime.test_runtime import TestRunner

MANIFEST = {
  "manifest_version": "1.2",
  "module": {
    "name": "部门管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/department"
  },
  "response_contract": {
    "envelope_keys": [
      "entity"
    ],
    "success_check": {
      "type": "field_and_absence",
      "success_field": "success",
      "error_field": "errorCode"
    },
    "list_keys": [
      "list"
    ],
    "total_keys": [
      "total"
    ],
    "id_field": "id"
  },
  "auth_profile": {
    "header_name": "Authorization",
    "header_prefix": "Bearer ",
    "freshness_ttl_seconds": 300,
    "fixed_headers": {},
    "probe_url": "",
    "context_fields": {},
    "token_key": "accessToken",
    "token_storage": "localStorage",
    "cookie_token_key": "accessToken",
    "credentials_env": {
      "username": "APP_USER",
      "password": "APP_PASS"
    }
  },
  "steps": [
    {
      "action": "创建部门",
      "label": "创建部门",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/tenants",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "adminUserCreateReqVO": {
          "description": "${gen_mutable_value()}",
          "email": "Ug2CY8/PO6HT3NvCNM83iFdXMDogJZRKwsGfgdXt14ZQVJy5hBQGmMz7wu5TSP8UY/ynB/VMydv9q3Rq/QBcPeJbABhbJaZOTfvPfk1i+ol1OKH4S2hQT3Mz73xtwBtf8UYc5osW1K1T0iRln2Ctgwcp3NHbt5/u4O3RFXttAvA=",
          "name": "${gen_test_name(\"name\")}",
          "password": "L5sBzQS4f2E6KLmrZuMZbrXbzLcrd0xB/+9AxSVgV4XpaDJdF/w2uexMIF30FDcnG2yn8vSWtx2krvJ00bl1YhYz/veMJPsfgmw7YfkfsDjThMlwtwitPNttl7rqf7L5aCWRQFPJ7lzk9p3ROAGHlyVgrtSjWsw7Hlo+DZkaAAE=",
          "phone": "Ff3dBIPruvZXYVGqAtv4HPHDTJFkVzDbUxvlixAM4gv0vhqJBEWBJE8TDhKSpf5y22kXYv7V2yzKxj32mZg1FAabC9LlZ+SdfC/5PCTgvLWBcIvGQTT8KtUtNONhgtNTnTsdxcL/Zucz7BZEmqgzjDA4uXuHbNccDH4iqQK1rfU=",
          "userName": "${gen_test_name(\"userName\")}",
          "countryCode": None
        },
        "description": "${gen_mutable_value()}",
        "name": "${gen_test_name(\"name\")}",
        "tenantId": None,
        "tenantType": None
      },
      "body_field_roles": {
        "adminUserCreateReqVO": {
          "role": "static"
        },
        "description": {
          "role": "mutable"
        },
        "name": {
          "role": "name"
        },
        "tenantId": {
          "role": "context",
          "source": "display_by_role.entity_0_id"
        },
        "tenantType": {
          "role": "context",
          "source": "display_by_role.entity_0_tenantType"
        },
        "adminUserCreateReqVO.description": {
          "role": "mutable"
        },
        "adminUserCreateReqVO.email": {
          "role": "static"
        },
        "adminUserCreateReqVO.name": {
          "role": "name"
        },
        "adminUserCreateReqVO.password": {
          "role": "static"
        },
        "adminUserCreateReqVO.phone": {
          "role": "static"
        },
        "adminUserCreateReqVO.userName": {
          "role": "name"
        },
        "adminUserCreateReqVO.countryCode": {
          "role": "context",
          "source": "current_user.entity_countryCode"
        }
      },
      "requires": []
    },
    {
      "action": "get",
      "label": "搜索验证（创建部门后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/children",
        "path_params": {},
        "query_params": {
          "tenantId": "537d49a60cfe4890bb34f8e1fd8cc7df",
          "pageNum": "1",
          "pageSize": "10"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [],
      "assertion": "search_verify",
      "search_param": "name"
    }
  ],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {}
  },
  "pre_apis": [
    {
      "name": "前置 API: display-by-role",
      "id": "display_by_role",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/display-by-role",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_id",
          "path": "entity[0].id",
          "used_by": [
            "创建部门"
          ]
        },
        {
          "name": "entity_0_tenantType",
          "path": "entity[0].tenantType",
          "used_by": [
            "创建部门"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "获取当前用户信息",
      "id": "current_user",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/users/current-user",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_countryCode",
          "path": "entity.countryCode",
          "used_by": [
            "创建部门"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "display_by_role",
    "current_user"
  ]
}

# --- 全局前置 API 支持 ---
_shared_ctx_file = Path(__file__).resolve().parent / ".shared_context.json"
SHARED_CONTEXT = {}
if _shared_ctx_file.exists():
    try:
        from lib.runtime.global_pre_apis import GlobalPreApiExecutor
        SHARED_CONTEXT = GlobalPreApiExecutor.load_context(_shared_ctx_file)
        if SHARED_CONTEXT:
            print("  ✅ 检测到共享上下文")
    except ImportError:
        pass

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    runner = TestRunner(MANIFEST, shared_context=SHARED_CONTEXT)
    steps = sys.argv[1:] if len(sys.argv) > 1 else None
    runner.run(steps_filter=steps)