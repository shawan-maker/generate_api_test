"""
代维管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:13:06
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/maintenance

执行流程:
  1. 创建代维用户
  2. 搜索验证（创建代维用户后）
  3. 编辑
  4. 搜索验证（编辑后）
  5. 冻结
  6. 搜索验证（冻结后）
  7. 启用
  8. 搜索验证（启用后）
  9. 锁定
  10. 搜索验证（锁定后）
  11. 重置密码
  12. 搜索验证（重置密码后）
  13. 删除
  14. 搜索验证（删除后）
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
    "name": "代维管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/maintenance"
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
      "action": "创建代维用户",
      "label": "创建代维用户",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/create",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "userName": "${gen_test_name(\"userName\")}",
        "password": "K8XeejS3/NfT1PNlZXf0r+mUmUuLfu+qKJrJr4VRNvS2F9VIqE34updGfs8VaoUiaGprlqcBrLtBAm9KLT2TKPU8D3Fy5zOCtHGN6wYyAT3g8ZWwttOBX3g6YEO82uuDxIIpRwexThs6ZVJ3MK2DFJXAtea2NG+/Q+TGgACUl8Y=",
        "name": "${gen_test_name(\"name\")}",
        "email": "bGhMMjB+Zrugx3VUpWq8xPs7fied3hfFhPa0bRAt8ljhXueYvUa2QGbbchJsDeUbA0DXmRljJqVy1FnQrxN9Z/JLOkX6asWbRIH+sOVzaTxd5dM4fhHtOhST6++FwFq/a13gQhAfCaRauwhnfzeafH6pwIkLqPLbrgpHT9Bv4Pg=",
        "phone": "Wms5CbrusKyzOU+fx0qzFEJ5KU+Dhpm6qMy1kvbexlJijN6ygcClnM42bAjfZyL3r0EzME7fH6xYe7Q5KQqGDg4i3g9fxQ3I1um3iw/+UU7v5jKtFH2Vc2besofmL331TUmiIC1G3jMiYOdDd/oJoXNRy+ZBjR2fY57hWmWFLZk=",
        "description": "${gen_mutable_value()}",
        "countryCode": None,
        "maintainTenantIds": []
      },
      "body_field_roles": {
        "userName": {
          "role": "name"
        },
        "password": {
          "role": "static"
        },
        "name": {
          "role": "name"
        },
        "email": {
          "role": "static"
        },
        "phone": {
          "role": "static"
        },
        "description": {
          "role": "mutable"
        },
        "countryCode": {
          "role": "context",
          "source": "current_user.entity_countryCode"
        },
        "maintainTenantIds": {
          "role": "context",
          "source": "root.entity_list_0_id"
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
      "action": "post",
      "label": "搜索验证（创建代维用户后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "编辑",
      "label": "编辑",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{path_0}",
        "path_params": {
          "path_0": {
            "source": "create.id",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "userId": None,
        "name": "${gen_test_name(\"name\")}",
        "description": "${gen_mutable_value()}",
        "tenantId": None,
        "countryCode": None,
        "phone": "X9Nvc6y8aUdkx5e1orBJfONbI+5FziOOMjm7XFL87bSAwvMokX9eL5EG2yYqCc5hsYgUSqqctCgyJcXVmWZtdFWgqRwxazpRXxPAyEnuogd5ZROJy15UWTpltjuHHJt6Ad2sIVjtWokqDopjKL7yWYDEk+t92PMLYprb6ctA7c4=",
        "email": "gJRyT06NMul+BsY3KqQn/V45PIbOctzhdYbQwlcNnkLU/yYOa+hrZWO2eo+nT9KkoTdTm8rA03tLlVBYa4hH48xaea/m77J/j4Og+w7iRBppGpjP+Uoae+nCykvsfHzQKJxMEexZSo7ak/MP4bPM/9iPFOvVt2C6tu/JSVE8W2I="
      },
      "body_field_roles": {
        "userId": {
          "role": "context",
          "source": "create.id"
        },
        "name": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "tenantId": {
          "role": "context",
          "source": "current_user.entity_tenantId"
        },
        "countryCode": {
          "role": "context",
          "source": "current_user.entity_countryCode"
        },
        "phone": {
          "role": "static"
        },
        "email": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（编辑后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "冻结",
      "label": "冻结",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{path_0}/suspend",
        "path_params": {
          "path_0": {
            "source": "create.id",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "userId": None
      },
      "body_field_roles": {
        "userId": {
          "role": "context",
          "source": "create.id"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（冻结后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "启用",
      "label": "启用",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{path_0}/enable",
        "path_params": {
          "path_0": {
            "source": "create.id",
            "match_from": "value_index"
          }
        },
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（启用后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "锁定",
      "label": "锁定",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/users/lock/{path_0}",
        "path_params": {
          "path_0": {
            "source": "create.id",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "userId": None
      },
      "body_field_roles": {
        "userId": {
          "role": "context",
          "source": "create.id"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（锁定后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "重置密码",
      "label": "重置密码",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/password-reset/reset",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "forceChangePassword": 1,
        "isRandomPassword": 1,
        "newPassword": "LPDqxCeJBWf0J0WSgG9RLxgQq7mdAnGK+x+CatbiRpImAHap86PVQ5J9wNLet4x6AtkM4TZxrw1U8DFvOpF41kQMn5KA+VOCC4YY4qH672en81B0Z8T62pAeq5P5jRSCY7/hadOIZlLxtRGQjq3f52tQPAPp50YZ7hlY7TvwkSA=",
        "passwordPolicy": {
          "id": None,
          "tenantId": None,
          "minPasswordLength": 8,
          "requireLowercaseCharacters": False,
          "requireUppercaseCharacters": True,
          "requireNumbers": True,
          "requireSymbols": True,
          "minPasswordDifferentCharacter": 0,
          "createdAt": None,
          "updatedAt": None,
          "deleted": False,
          "userName": None
        },
        "userId": None,
        "noticeType": [
          "email"
        ]
      },
      "body_field_roles": {
        "forceChangePassword": {
          "role": "static"
        },
        "isRandomPassword": {
          "role": "static"
        },
        "newPassword": {
          "role": "static"
        },
        "passwordPolicy": {
          "role": "static"
        },
        "userId": {
          "role": "context",
          "source": "create.id"
        },
        "noticeType": {
          "role": "static"
        },
        "passwordPolicy.id": {
          "role": "context",
          "source": "password_policy.entity_id"
        },
        "passwordPolicy.tenantId": {
          "role": "context",
          "source": "current_user.entity_tenantId"
        },
        "passwordPolicy.minPasswordLength": {
          "role": "static"
        },
        "passwordPolicy.requireLowercaseCharacters": {
          "role": "static"
        },
        "passwordPolicy.requireUppercaseCharacters": {
          "role": "static"
        },
        "passwordPolicy.requireNumbers": {
          "role": "static"
        },
        "passwordPolicy.requireSymbols": {
          "role": "static"
        },
        "passwordPolicy.minPasswordDifferentCharacter": {
          "role": "static"
        },
        "passwordPolicy.createdAt": {
          "role": "context",
          "source": "password_policy.entity_createdAt"
        },
        "passwordPolicy.updatedAt": {
          "role": "context",
          "source": "password_policy.entity_updatedAt"
        },
        "passwordPolicy.deleted": {
          "role": "static"
        },
        "passwordPolicy.userName": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（重置密码后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "删除",
      "label": "删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/maintain/delete",
        "path_params": {},
        "query_params": {}
      },
      "body_template": [
        "9340201260534ac599f2e2454bdf84b2"
      ],
      "body_field_roles": {
        "__array_items__": {
          "role": "context",
          "source": "create.id",
          "is_array": True
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（删除后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/maintain/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "name": "${gen_test_name(\"name\")}"
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "name": {
          "role": "name"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_not_found",
      "search_param": "name",
      "search_param_source": "userName"
    }
  ],
  "state_assertions": {
    "state_field": "state",
    "values_by_crud": {
      "创建代维用户": "ENABLE",
      "编辑": "ENABLE",
      "冻结": "DISABLE",
      "启用": "ENABLE"
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
          "name": "entity_countryCode",
          "path": "entity.countryCode",
          "used_by": [
            "创建代维用户",
            "编辑"
          ]
        },
        {
          "name": "entity_tenantId",
          "path": "entity.tenantId",
          "used_by": [
            "编辑"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: root",
      "id": "root",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/root",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_list_0_id",
          "path": "entity.list[0].id",
          "used_by": [
            "创建代维用户"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: password-policy",
      "id": "password_policy",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/password-policy",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_id",
          "path": "entity.id",
          "used_by": [
            "重置密码"
          ]
        },
        {
          "name": "entity_createdAt",
          "path": "entity.createdAt",
          "used_by": [
            "重置密码"
          ]
        },
        {
          "name": "entity_updatedAt",
          "path": "entity.updatedAt",
          "used_by": [
            "重置密码"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "current_user",
    "root",
    "password_policy"
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