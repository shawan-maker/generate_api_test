"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-18 12:33:07
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
        "query_params": {}
      },
      "body_template": {
        "userName": "AT_test_643315",
        "password": "MC9Oa+R6mpVwjCo+yXnB+CYmhxmkKeXv2AJGPKX7qN2x4NNok0z/Qi9Hm9+whRyY7l+w3MUkuu087o+B3kW5XsXY6t8mFXo85fx9hD+cczLIt9UwGuT7wxXQubVZdS0i5LgHJ8Um/I8TgXwhS639cSGyUo9Tj7RLu1HC0gSTYnc=",
        "name": "AT_test_643315",
        "email": "gwVhneR4xcvXwihBD7DIjEe3l4SE5q7jELGxOukLMFdWEije8Yo4DPZMZwt9KIzt9s7oLJUTB/l5LPMiNAeq1OlVfw34KmNi/8Is2FQnsvSF9a7Aef3ssfen2Z3938efGpXh/dY++FXaCKwQkxxRNdRA1AEOFnpPDBk7WIFcgrk=",
        "phone": "HZWD8w26JXZnTAr+HJMMvXzZ/DX9kzpb2XBOpWihTP1Ipx9y0isSZ4z1HZK53GhRe8c15zAdUB6Vv+7fTrFmAIiW0nf00olD6t3uKRHrg4M3KjsLffRigUdVdTuv92yQ5r4NFBiIPpUaSOEsmspZVbdAduoZoYJUVVf6fIoC6mw=",
        "description": "auto_desc_643315",
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
          "role": "static"
        },
        "adminId": {
          "role": "context",
          "source": "current_user.entity_adminId"
        },
        "tenantId": {
          "role": "context",
          "source": "create.tenantId"
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/{id}",
        "query_params": {}
      },
      "body_template": {
        "userId": "14106ef930614cb7a585a2650401dcbd",
        "name": "AT_test_643315",
        "description": "auto_desc_643315",
        "tenantId": "42ffdba38c58484f9be2bc1adf1672e6",
        "countryCode": "+86"
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
          "source": "create_body.tenantId"
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/{id}/suspend",
        "query_params": {}
      },
      "body_template": {
        "userId": "14106ef930614cb7a585a2650401dcbd"
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/{id}/enable",
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/lock/{id}",
        "query_params": {}
      },
      "body_template": {
        "userId": "14106ef930614cb7a585a2650401dcbd"
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "query_params": {}
      },
      "body_template": {
        "forceChangePassword": 1,
        "isRandomPassword": 1,
        "newPassword": "OacWVoZcQyEo172NTgPaWtbeDxBtgaZpvDiNf8tJCZBUPWkgsqRFjhO62ENe5y24ThBMagSayQl8G8jlDyGVlA2ZnprgMia4C6lB91zuUlu2JKzyn9txIrThPWxvFY8MpT0ox+3+hLfRH3CCOBjRYPigbO4SA04gs9VYKZONApM=",
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
        "userId": "14106ef930614cb7a585a2650401dcbd",
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
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/migrate/{id}",
        "query_params": {}
      },
      "body_template": {
        "tenantId": "5bcbffa731154b1da9c3dd41356088d8",
        "userIds": [
          "14106ef930614cb7a585a2650401dcbd"
        ]
      },
      "body_field_roles": {
        "tenantId": {
          "role": "context",
          "source": "current_user.entity_tenantId"
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
        "pathname": "/estack/api/estack/draco/v1/users/{id}",
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
        "query_params": {
          "tenantId": "$create.tenantId",
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
            "创建用户",
            "编辑"
          ]
        },
        {
          "name": "entity_tenantId",
          "path": "entity.tenantId",
          "used_by": [
            "迁移"
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
    }
  ],
  "pre_api_refs": [
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