"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-09 16:17:10
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 搜索验证（创建用户后）
  3. 编辑
  4. 搜索验证（编辑后）
  5. 冻结
  6. 搜索验证（冻结后）
  7. 启用
  8. 搜索验证（启用后）
  9. 重置密码
  10. 搜索验证（重置密码后）
  11. 批量删除
  12. 搜索验证（删除后）

状态断言:
  - 删除后数据不应出现
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
        "userName": "autotest940091",
        "password": "TksFfso6oJHGuiyz1o5/mvm99wPUkrjOb18FoEew6KPlH5GngmbNUvKkQvb5Bq34sQX3V/bv6eOpxksEF0SxqiUARmtLzUew+AH90DXoaubZ2cGbd6uNg1joGjX1OkfiRnXW/iV3sk+MuhOYy9k9jnPDlfC7ZfKCOLPj0wTPHKA=",
        "name": "test_940091",
        "email": "MmFyZy2zteoWTjLqMmtfddJJ8KJL2v6v94jtoMemwVBEdlAO7StP35gPqyjyy2LMm2H/pOHUJD7zB/4HRI9ghydu++pRSP+EC6Kik88udTZYF5RjtESl2Om515Rmhy5H9mxCOFrI0CaYO0O8EVKvXRWbP8WRg9NYWdcBVT7tppU=",
        "phone": "RxMRQQI5QjNa+Tz0imq+Y2P0W2f7uwLHSJu0spwB3VZ8NwpMRGzIn0kmuX2wn4/SRYxMCYwJnyH40vy/f3YNjyqABZQX2DBkMfOH+LVZAh+XBAOzhOnOFcBWhcndHl5WWhNCzRiKMyyKmcC0c5E5YRO6mxjPeLQJhIKOiZn958k=",
        "description": "auto_desc_940091",
        "policyIds": [
          "1f8e392309fc414c9d77d45d0315fedc"
        ],
        "adminId": "644e7d7b19c744d6a1d3194b8d819fd3",
        "tenantId": "34e2594fca9e44cd9e23b25474838c7f",
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
      "label": "搜索验证（创建用户后）",
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
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "update",
      "label": "编辑",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{id}",
        "query_params": {}
      },
      "body_template": {
        "userId": "35df6561f43a49878246aba4ff69cc37",
        "name": "test_940118",
        "description": "auto_desc_940118",
        "tenantId": "34e2594fca9e44cd9e23b25474838c7f",
        "countryCode": "+86",
        "phone": "dA4HQP5xe3KMIMi9QydzD3dEPzCZUhlddndsdl7eyuydLki1/v7r5lL1EDztrLlzHIN4n9BScNUPJRSYWsYdy8Vc80cPvwORlZu5uRaDBtyxVveoqH6hPzOvnJW8r4XBg6eNlThGhOrh9Z1Lokt++uDx2N3keo+EYAJKvA+octs=",
        "email": "S/cSTzpiTmXvdHJwRaH1WFfmOqlSo3ED0rQy3FxI8TxZ10Tpo82mUzXC9jgugJeKNcX6v8voBq955aDThXB1pP7lHxZJNnxcbW53eqVmFf13mYVwvEoMG7sKhnef0MvWkAeGdRvRv+lVi20rfaZwJDeaXE9vbqOi3w8FDzvH1FM=",
        "adminId": "93552edc908e4dadae761fff1fd0f24c"
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
        },
        "name": {
          "role": "pre_api_ref",
          "source": "current_user.entity_name"
        },
        "description": {
          "role": "pre_api_ref",
          "source": "current_user.entity_description"
        },
        "tenantId": {
          "role": "pre_api_ref",
          "source": "display_by_role.entity_0_id"
        },
        "countryCode": {
          "role": "pre_api_ref",
          "source": "current_user.entity_countryCode"
        },
        "phone": {
          "role": "pre_api_ref",
          "source": "current_user.entity_phone"
        },
        "email": {
          "role": "pre_api_ref",
          "source": "current_user.entity_email"
        },
        "adminId": {
          "role": "pre_api_ref",
          "source": "current_user.entity_id"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（编辑后）",
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
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "lock",
      "label": "冻结",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{id}/suspend",
        "query_params": {}
      },
      "body_template": {
        "userId": "35df6561f43a49878246aba4ff69cc37",
        "tenantId": "cec63451f8bf4ceebb9ada0b87d829bf",
        "adminId": "93552edc908e4dadae761fff1fd0f24c"
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
        },
        "tenantId": {
          "role": "context",
          "source": "context.tenantId"
        },
        "adminId": {
          "role": "context",
          "source": "context.adminId"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（冻结后）",
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
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "unlock",
      "label": "启用",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{id}/enable",
        "query_params": {}
      },
      "body_template": {
        "tenantId": "cec63451f8bf4ceebb9ada0b87d829bf",
        "adminId": "93552edc908e4dadae761fff1fd0f24c"
      },
      "body_field_roles": {
        "tenantId": {
          "role": "context",
          "source": "context.tenantId"
        },
        "adminId": {
          "role": "context",
          "source": "context.adminId"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（启用后）",
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
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "reset",
      "label": "重置密码",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/password-reset/generate",
        "query_params": {}
      },
      "body_template": {
        "userId": "35df6561f43a49878246aba4ff69cc37",
        "passwordPolicy": {
          "id": "90542be67d584ab09daa12e697fb041a",
          "tenantId": "cec63451f8bf4ceebb9ada0b87d829bf",
          "minPasswordLength": 8,
          "requireLowercaseCharacters": False,
          "requireUppercaseCharacters": True,
          "requireNumbers": True,
          "requireSymbols": True,
          "minPasswordDifferentCharacter": 0,
          "createdAt": "2023-08-30 10:07:14",
          "updatedAt": "2025-03-08 18:39:42",
          "deleted": False,
          "userName": None
        },
        "isRandomPassword": False
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
        },
        "passwordPolicy": {
          "role": "static"
        },
        "isRandomPassword": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（重置密码后）",
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
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "userName"
    },
    {
      "action": "delete",
      "label": "批量删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/users/batch/delete",
        "query_params": {}
      },
      "body_template": [
        "35df6561f43a49878246aba4ff69cc37"
      ],
      "body_field_roles": {
        "__array_items__": {
          "role": "id_ref",
          "source": "create.id"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（删除后）",
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
      "assertion": "search_not_found",
      "search_param": "name",
      "search_param_source": "userName"
    }
  ],
  "state_assertions": {
    "state_field": "state",
    "values_by_crud": {
      "create": "ENABLE",
      "update": "ENABLE",
      "lock": "DISABLE",
      "unlock": "ENABLE"
    },
    "after_create": "ENABLE",
    "after_lock": "DISABLE",
    "after_unlock": "ENABLE",
    "after_delete": "NOT_EXIST"
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
            "create",
            "update"
          ]
        },
        {
          "name": "entity_email",
          "path": "entity.email",
          "used_by": [
            "create",
            "update"
          ]
        },
        {
          "name": "entity_phone",
          "path": "entity.phone",
          "used_by": [
            "create",
            "update"
          ]
        },
        {
          "name": "entity_description",
          "path": "entity.description",
          "used_by": [
            "create",
            "update"
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
            "create",
            "update"
          ]
        },
        {
          "name": "entity_id",
          "path": "entity.id",
          "used_by": [
            "update"
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
            "create",
            "update"
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