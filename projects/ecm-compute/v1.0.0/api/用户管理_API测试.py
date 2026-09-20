"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-18 19:03:50
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 搜索验证（创建用户后）
  3. query
  4. 编辑
  5. 搜索验证（编辑后）
  6. 冻结
  7. 搜索验证（冻结后）
  8. 启用
  9. 搜索验证（启用后）
  10. 锁定
  11. 搜索验证（锁定后）
  12. 重置密码
  13. 搜索验证（重置密码后）
  14. 迁移
  15. 搜索验证（迁移后）
  16. 删除
  17. 搜索验证（删除后）

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
      "action": "创建用户",
      "label": "创建用户",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/users",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "userName": "AT_test_717709",
        "password": "FZiGt/qDULlqJiTdrxKwFiZfgx5SDEQpfXvJ6YU246wZuFqpGgM6Q82fad+0LJFWkQw0ilzPUd2i3Xm7YEXFIz+U4h+18vgOMGYFJDAWjb1t5U+Oy6aFCBjzYqLUuy7McDV4h49cTzGD4FS9d2OZwR3s/+DA3efCBBCD2PyeOGQ=",
        "name": "AT_test_717709",
        "email": "e061bCUAlgPn1FZDV+g2Ffzrc5er1fvW3jyYP3efNt/yj+ZSU2wyE+t87utArQ0BofhOkXrV3AW0Pe7f4IwXJqrbO95kDy3fYaJ3QcUqpZ9KHm3YC2h24GJl03ZlIWSWXsT1N/c5xowAZXWjOZIOLTyKpSH55QIZB58dOdwkUkQ=",
        "phone": "aBhfvgloQdk+n6DJowi1yEEOqRyZkcb96niIVu7rSgGZIfsJTDOS9Oq71tfg5X8bxu5YjIHLN0VXTcD8Z+dqT6iovTaY6qbuIiApcbxWMQJwH4TxxtBM7sAE8qUdTsaEa2v7nJPfw9H6FFaSMJrFGxSOBrZhaIt/jDT/E917RT0=",
        "description": "auto_desc_717709",
        "policyIds": [],
        "adminId": None,
        "tenantId": None,
        "countryCode": None
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
        "policyIds": {
          "role": "context",
          "source": "users.entity_list_0_policyList_0_id"
        },
        "adminId": {
          "role": "context",
          "source": "current_user.entity_adminId"
        },
        "tenantId": {
          "role": "context",
          "source": "users.entity_list_0_tenantId"
        },
        "countryCode": {
          "role": "context",
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
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "query",
      "label": "query",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ]
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
        "name": "AT_test_717709",
        "description": "auto_desc_717709",
        "tenantId": None,
        "countryCode": None
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
          "source": "users.entity_list_0_tenantId"
        },
        "countryCode": {
          "role": "context",
          "source": "current_user.entity_countryCode"
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
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "get",
      "label": "搜索验证（冻结后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "get",
      "label": "搜索验证（启用后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "get",
      "label": "搜索验证（锁定后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
        "newPassword": "KFEHujvijlBnPH19k20VzQwPkXoNG31udBzKym6F+KitfFhXHcPBAWUjiuMFf4MwLLUEbV1HyCg3qwvODpI3v3iD/dGUHcNrpfHXBD1g6V3amU2FWPRBviMn3nWk2aOfWKVW0A1M6q4SmCluKN0STKkPPPfspMBYEmtS/SnQMVI=",
        "passwordPolicy": {
          "id": "90542be67d584ab09daa12e697fb041a",
          "tenantId": None,
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
        "passwordPolicy.tenantId": {
          "role": "context",
          "source": "current_user.entity_tenantId"
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
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "迁移",
      "label": "迁移",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/migrate/{path_0}",
        "path_params": {
          "path_0": {
            "source": "display_by_role.entity_0_children_0_id",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "tenantId": None,
        "userIds": []
      },
      "body_field_roles": {
        "tenantId": {
          "role": "context",
          "source": "display_by_role.entity_0_children_0_id"
        },
        "userIds": {
          "role": "context",
          "source": "create.id"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（迁移后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "action": "删除",
      "label": "删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/users/{path_0}",
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
      "action": "get",
      "label": "搜索验证（删除后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "path_params": {},
        "query_params": {
          "tenantId": "$users.entity_list_0_tenantId",
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
      "创建用户": "ENABLE",
      "编辑": "ENABLE",
      "冻结": "DISABLE",
      "启用": "ENABLE",
      "删除": "ENABLE"
    },
    "after_create": "ENABLE",
    "after_delete": "NOT_EXIST"
  },
  "pre_apis": [
    {
      "name": "获取用户列表",
      "id": "users",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/users",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_list_0_policyList_0_id",
          "path": "entity.list[0].policyList[0].id",
          "used_by": [
            "创建用户"
          ]
        },
        {
          "name": "entity_list_0_tenantId",
          "path": "entity.list[0].tenantId",
          "used_by": [
            "编辑",
            "创建用户"
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
          "name": "entity_adminId",
          "path": "entity.adminId",
          "used_by": [
            "创建用户"
          ]
        },
        {
          "name": "entity_countryCode",
          "path": "entity.countryCode",
          "used_by": [
            "编辑",
            "创建用户"
          ]
        },
        {
          "name": "entity_tenantId",
          "path": "entity.tenantId",
          "used_by": [
            "重置密码"
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
      "name": "前置 API: display-by-role",
      "id": "display_by_role",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/display-by-role",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_children_0_id",
          "path": "entity[0].children[0].id",
          "used_by": [
            "迁移"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "users",
    "current_user",
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