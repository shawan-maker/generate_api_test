"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-30 16:29:02
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 搜索验证（创建用户后）
  3. 编辑
  4. 搜索验证（编辑后）
  5. 授权
  6. 搜索验证（授权后）
  7. 冻结
  8. 搜索验证（冻结后）
  9. 启用
  10. 搜索验证（启用后）
  11. 锁定
  12. 搜索验证（锁定后）
  13. 重置密码
  14. 搜索验证（重置密码后）
  15. 删除
  16. 搜索验证（删除后）
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
      "action": "创建用户",
      "label": "创建用户",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/users",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "userName": "${gen_test_name(\"userName\")}",
        "password": "VFJPgr6HTdwPUnqs6cG5nrF6isaEaGvWM1su5dZLCfzIZGwBcdJBEwa3CEJV7PkQKpyhBsdFS84+da1bxVEOA1NL8JSbvH/XmKKw3y6wIoVq4ju0Mac8+bhoHoK5kSzw83D0IgMPkhgmCiyE17jRr49iP1EWZRpfoJ4/4/d3rS4=",
        "name": "${gen_test_name(\"name\")}",
        "email": "QJ/1EOl0biYTN2R5sU6mX37pPWrMK8ewZYrl/2mIix+AcQuFgtjWz+2hVEXjklDqI+wdZj439RI6TCE8tNLKImH9JS752zyYKO05h4Wu8a2HQv01myGOgBoeGEGZs0IgWntwZd/Dwd+JQIel55xGtOF9T+rMlaf4eQs4QpylI5w=",
        "phone": "GkN516cX1Gs4EnNUe8gD5ESglwYB/gTsdhzS4UG5jOl5ILJATxaYmLa8NDdjIKH1k4XT/MA7nWSZAgjnUI7CPwl+vsfGKfwMX8aZRK4PHJyHAGzaDL6Fn0FiJDnpxkF6kp8YDkuYoRUss5Zb0aiWXIs3N9UEiB6OAib2VqolO5w=",
        "description": "${gen_mutable_value()}",
        "policyIds": [
          "1f8e392309fc414c9d77d45d0315fedc",
          "e8adab357a3c4fa0b76abfe82fc201a6"
        ],
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
          "role": "static"
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
        "phone": "aMVe1SWWnWdFC2TrnKQqMamhWyDWCEVwlnF6Gfsj0TMzs5lgicWKVuF/v+JX2ha30MlBDtd0eAPsgB0gkoajJznWYPFKmPaMRJxRSRO1WYcpfptcr/2orduGUFLPxLiJIblp1GbZp6MBDUJjK3jk05aItNiH7Q3RsaHVuwM02/8=",
        "email": "gwMKsbRDrWO6JSlfpICtL7xppc5a6EPXd5q07+T7IAAyBc07jxq+wk47QgdtvuVpFZTUZ1Lf5WKLrezeKPUipBgbb/m3z4Z3bfB5/HkFC2pKRy07HqBLBzI3+XUxeub0ysm8c9Gavbxna89KNFQz4OqfwR8mxTLqjN/JqhPosK4="
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
      "action": "授权",
      "label": "授权",
      "phases": [
        {
          "id": "attach-to-group",
          "label": "授权(attach-to-group)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/draco/v1/policies/attach-to-group",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "policies": [],
            "principleIds": [],
            "statement": {
              "collections": "*",
              "pools": "*",
              "projects": "*"
            },
            "tenantId": None
          },
          "body_field_roles": {
            "policies": {
              "role": "context",
              "source": "users.entity_list_0_policyList_0_id"
            },
            "principleIds": {
              "role": "context",
              "source": "groups.entity_list_0_id"
            },
            "statement": {
              "role": "static"
            },
            "tenantId": {
              "role": "context",
              "source": "users.entity_list_0_tenantId"
            },
            "statement.collections": {
              "role": "static"
            },
            "statement.pools": {
              "role": "static"
            },
            "statement.projects": {
              "role": "static"
            }
          },
          "extract": []
        },
        {
          "id": "main",
          "label": "授权",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/draco/v1/policies/attach-to-user",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "policies": [],
            "principleIds": [],
            "statement": {
              "collections": "*",
              "pools": "*",
              "projects": "*"
            },
            "tenantId": None
          },
          "body_field_roles": {
            "policies": {
              "role": "context",
              "source": "users.entity_list_0_policyList_0_id"
            },
            "principleIds": {
              "role": "context",
              "source": "create.id"
            },
            "statement": {
              "role": "static"
            },
            "tenantId": {
              "role": "context",
              "source": "users.entity_list_0_tenantId"
            },
            "statement.collections": {
              "role": "static"
            },
            "statement.pools": {
              "role": "static"
            },
            "statement.projects": {
              "role": "static"
            }
          }
        }
      ],
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（授权后）",
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
        "newPassword": "etqMVhjtwGxjxvrzAdPwt1S5NQXJvVeEuO07JuUNhDC9jVjBhA1vjJE+jhRWNOk3qsk23LualReAJ6W1W3gBt9iZy41ShNWP44JS0sW2tlrVQdGCgXaiMXbdxo3QoZjIWrW8QpaV+mEPUKFd9mz/jcCk7a6WfQyGfZ+KQYS+SMc=",
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
      "授权": "OK",
      "冻结": "DISABLE",
      "启用": "ENABLE",
      "删除": "ENABLE"
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
          "name": "entity_policyList_0_policyType",
          "path": "entity.policyList[0].policyType",
          "used_by": [
            "用户"
          ]
        },
        {
          "name": "entity_policyList_0_policyCategory",
          "path": "entity.policyList[0].policyCategory",
          "used_by": [
            "用户"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "获取用户列表",
      "id": "users",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/users",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_list_0_tenantId",
          "path": "entity.list[0].tenantId",
          "used_by": [
            "编辑",
            "创建用户"
          ]
        },
        {
          "name": "entity_list_0_policyList_0_id",
          "path": "entity.list[0].policyList[0].id",
          "used_by": [
            "*"
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
    "users",
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