"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:23:06
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
  15. 迁移
  16. 搜索验证（迁移后）
  17. 删除
  18. 搜索验证（删除后）
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
        "password": "XuKCf3BLiJVlM/hXF8/qp25mIBacMt/Rqkl8DSV9NMyQfRXLdga/hKXtHURcj2tuR7N+y2f8oNvrd3GL56el3Dg6wCtpJCJm9rjOWL2fU/VMGeT1Zhn4MNnSzy7UQ/nwOX2doFru4XUtOtTaWb/TH1spU/qYOZioPkah7HlKL18=",
        "name": "${gen_test_name(\"name\")}",
        "email": "dzqjzbQ65Nl9mQ0IuJ36ZWDe2lsE0yAhvhoHNU6mSR8osvKdqn6JgOEYyAEzVwtENedCCZwap8vthcOJqi5HX3wVHWvNmTpzPzikBHPuFAtjVXOml9eOf7ifFBxpHddxUxilkBs1Rgq0Nqxq3Gb0atzTLXNQbdv51VLCZoAED0E=",
        "phone": "hc+cCJOjS9Uo7Kxwe43wNfBxgevC2/3XJOZRrd/wWD1Jm/JvXCa9XrIfazEkdmvG3zx4F739ynSiz3b2FW3+ld/f4N0L6wvw/uctxLt+F+8Rhm+ulbpR14duTKdz88IPNMYdz7zt/bDixLUf+BUVlgG4xkkfIlNRhUqkD+fgGAM=",
        "description": "${gen_mutable_value()}",
        "policyIds": [
          "1f8e392309fc414c9d77d45d0315fedc"
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
          "source": "root.entity"
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
          "tenantId": "$root.entity",
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
        "phone": "hWMsEOvgGVeGWKHjkrFr0PUmEEmV4BtvoYcg6xZsGkzr4UEsneBaAGp1FxHK+HupDopDOj84MgngD/39KZHDnlNELSYgEWXOIJ6gLocvbjMDwvhm2q5cajr40iIj5ZFK+WK7y1KXQIOGqxL2dWS8v5cUMTcubWbpVdAgVluahS4=",
        "email": "EEu/DvRV3brGov/ZGHFKEcxB3JDRCUITiOb2E9P3RTC//FIiIhba29aybmKAJMV/BgEE6y51ht4GhtJzxbNSuiAA+UQUR2PvqUTFV6+pE5GnxHTONjfemrndEbRowc0tFV2jFD/gRs7Jc2NpgcoPdpYDjN4qMPBBrsuKkTFXhJ0="
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
          "source": "root.entity"
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
          "tenantId": "$root.entity",
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
          "id": "side-tree",
          "label": "授权(side-tree)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/pegasi/v1/menu/side-tree",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "userId": None,
            "isVisible": 1
          },
          "body_field_roles": {
            "userId": {
              "role": "context",
              "source": "access_log.entity_0_userId"
            },
            "isVisible": {
              "role": "static"
            }
          },
          "extract": []
        },
        {
          "id": "side-tree",
          "label": "授权(side-tree)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/pegasi/v1/menu/side-tree",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "userId": None,
            "isVisible": 1
          },
          "body_field_roles": {
            "userId": {
              "role": "context",
              "source": "access_log.entity_0_userId"
            },
            "isVisible": {
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
            "pathname": "/estack/api/estack/pegasi/v1/menu/tree",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "userId": None,
            "rootId": "all-resource",
            "isVisible": 1
          },
          "body_field_roles": {
            "userId": {
              "role": "context",
              "source": "access_log.entity_0_userId"
            },
            "rootId": {
              "role": "static"
            },
            "isVisible": {
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
          "tenantId": "$root.entity",
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
          "tenantId": "$root.entity",
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
          "tenantId": "$root.entity",
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
          "tenantId": "$root.entity",
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
        "newPassword": "geD+kAXXnbxmHMu23kDy0phjwuysl5paNIABRa/vahnTKtDxiVohOTIENSoY8BnMsAt5ZJZkHxiCumIEvxwtAUAg2nHaXDvhs3w8Ucn2hHyjGm1Ozg1m0eXEozU92XECAPCzRlfl6tPqTir48zS294vhM6riEOm192cKi/nvIp8=",
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
          "tenantId": "$root.entity",
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
            "source": "users.entity_list_0_tenantId",
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
          "source": "users.entity_list_0_tenantId"
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
          "tenantId": "$root.entity",
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
          "tenantId": "$root.entity",
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
            "创建用户",
            "编辑"
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
      "name": "前置 API: root",
      "id": "root",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/158222ee87484790ae9651a459e82d0a/root",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity",
          "path": "entity",
          "used_by": [
            "创建用户",
            "编辑"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: access-log",
      "id": "access_log",
      "method": "GET",
      "pathname": "/estack/api/estack/pegasi/v1/menu/access-log",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_userId",
          "path": "entity[0].userId",
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
            "迁移"
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
    "access_log",
    "password_policy",
    "users"
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