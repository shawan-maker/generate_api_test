"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-10 11:21:07
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 查询验证（创建后）
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
    "name": "用户管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/user"
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
    "freshness_ttl_seconds": 1800,
    "fixed_headers": {
      "Estack-Language": "zh-CN"
    },
    "probe_url": "/estack/api/estack/draco/v1/users/current-user",
    "context_fields": {
      "tenantId": {
        "path": "entity.tenantId"
      },
      "adminId": {
        "path": "entity.id"
      }
    },
    "token_key": "estackToken",
    "token_storage": "localStorage",
    "cookie_token_key": "accessToken",
    "credentials_env": {
      "username": "APP_USER",
      "password": "APP_PASS"
    }
  },
  "steps": [
    {
      "action": "create",
      "label": "创建用户",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/users",
        "query_params": {}
      },
      "body_template": {
        "userName": "autotest010424",
        "password": "Q763nZJ/poVavV2SEmECzWXglsH7Df1+Oj78EsqKQ0EHy+TZkd/nGZh3SSNEoMDVwOR/p9gRH5sgHBxrndZb/BBmPJnYQMqUKvQPSDCX/nCg+FSKIJs9HfRP7OxStyyWY8ZtzZzhmBaZBOy5YjAqUkRTMKHvz3XS3eO2TeplJZE=",
        "name": "test_010424",
        "email": "RPVY1mUyutXyQ/xsYpi/x/wi2Th6V78gsb9YCDt2ufPkzLJJMlAFNriUE7+RzhSEMzhiC5H2pD1ay13fngpFZW58qD4tTAo2MeR7dCSTRFxdhEHaCXitCadxm6oF4GeLROVv6PKBXFZ8lehsZJy4cAzdNhlOcgIzMT00rUS7k4A=",
        "phone": "E98pYupLR5JDo4+40nuhESr79MQ5YhVQIdqDnfZGtzxsV58/gvmQD65RtFPii0GZKHd5sywjE1yj12+CqxE37G4KdCQzWj0vEDlQUMfDCmRW1PiKhSUp2763W0p02OF6MeNlgJlgOQOwE5mqMQrpoY5qGQEDrGyYsCXrQOMZZp8=",
        "description": "auto_desc_010424",
        "policyIds": [
          "1f8e392309fc414c9d77d45d0315fedc"
        ],
        "adminId": "644e7d7b19c744d6a1d3194b8d819fd3",
        "tenantId": "42ffdba38c58484f9be2bc1adf1672e6",
        "countryCode": "+86"
      },
      "body_field_roles": {
        "userName": {
          "role": "name"
        },
        "password": {
          "role": "pre_api_ref",
          "source": "current_user.entity_password"
        },
        "name": {
          "role": "pre_api_ref",
          "source": "current_user.entity_name"
        },
        "email": {
          "role": "pre_api_ref",
          "source": "current_user.entity_email"
        },
        "phone": {
          "role": "pre_api_ref",
          "source": "current_user.entity_phone"
        },
        "description": {
          "role": "pre_api_ref",
          "source": "current_user.entity_description"
        },
        "policyIds": {
          "role": "pre_api_ref",
          "source": "list.entity_list_0_id",
          "is_array": True
        },
        "adminId": {
          "role": "pre_api_ref",
          "source": "current_user.entity_adminId"
        },
        "tenantId": {
          "role": "pre_api_ref",
          "source": "display_by_role.entity_0_id"
        },
        "countryCode": {
          "role": "pre_api_ref",
          "source": "current_user.entity_countryCode"
        }
      },
      "requires": [],
      "extract": {
        "id": "entity.id",
        "names": [
          "userName",
          "name"
        ]
      }
    },
    {
      "action": "get",
      "label": "查询验证（创建后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$display_by_role.entity_0_id",
          "pageNum": "1",
          "pageSize": "10"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    }
  ],
  "state_assertions": {
    "state_field": "state",
    "values_by_crud": {
      "create": "ENABLE"
    },
    "after_create": "ENABLE"
  },
  "pre_apis": [
    {
      "name": "获取当前用户信息",
      "id": "current_user",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/users/current-user",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_password",
          "path": "entity.password",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_name",
          "path": "entity.name",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_email",
          "path": "entity.email",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_phone",
          "path": "entity.phone",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_description",
          "path": "entity.description",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_adminId",
          "path": "entity.adminId",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "entity_countryCode",
          "path": "entity.countryCode",
          "used_by": [
            "create"
          ]
        },
        {
          "name": "tenantId",
          "path": "entity.tenantId",
          "used_by": [
            "query"
          ]
        },
        {
          "name": "adminId",
          "path": "entity.id",
          "used_by": [
            "query"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: list",
      "id": "list",
      "method": "POST",
      "pathname": "/estack/api/estack/draco/v1/policies/list",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_list_0_id",
          "path": "entity.list[0].id",
          "used_by": [
            "create"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
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
            "create"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "current_user",
    "list",
    "display_by_role"
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