"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-17 13:59:18
目标URL: https://10.151.61.248D:/Program Files (x86)/Git/estack/web/estack/user-center/user-manage/user

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
  14. 删除
  15. 搜索验证（删除后）

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
    "target_url": "https://10.151.61.248D:/Program Files (x86)/Git/estack/web/estack/user-center/user-manage/user"
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
        "userName": "AT_test_612336",
        "password": "Qo6E+yUJpiME0nXG9jQa7VTX1SKCBGFBVBsSo5wajcPVhUAmuQij23Lxze8SPj2tNKBx+tk1T4Fk+lr1Le+AYyiMsas1Wf1Kg2TgCcjkKoadjIobiCWHOFfDGOPgUMvDCD9pJVUNS1IkEcCCbOZMWgxtQ9AaDZEgu4pg5p05aH0=",
        "name": "AT_test_612336",
        "email": "hetrYUxjvozfsDhjkPwHRGvAV2cBIeQI17uIGXdNi8FZg4tJiN9LFmZDtt4Ktqg0YtD4MF9Tp266KYSFvQgiS0HS89W2ZIzFH6L2OLREDkshjKx3ZwJjCtRUoer2jFrzPnVtgFqPXYR1rwQJO/Zy+i+TSE2UfdXRh6rCMcfGafI=",
        "phone": "Vznvh5G4j9mNPkxCURnRcO7Rk0TEWC3MvkgGA0pddxvkPmQyfB7HLawfQkt0qwY3JcqGUZ4VirQyTq2dCEXh6MLrEvo39cZrHhkvinwKUYSSrVEEQ86bzAsOZxJ6TOaQQ3bCO37blvr4MIujfPHguYkpPBgtd1M4pcqbi/xHd3o=",
        "description": "auto_desc_612336",
        "policyIds": [
          "1f8e392309fc414c9d77d45d0315fedc",
          "e95978ecbbe1475aa5fa062fa3b4c163"
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
          "role": "pre_api_ref",
          "source": "list.entity_list_0_id",
          "is_array": True
        },
        "adminId": {
          "role": "context",
          "source": "context.adminId"
        },
        "tenantId": {
          "role": "context",
          "source": "context.tenantId"
        },
        "countryCode": {
          "role": "static"
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
          "tenantId": "$list.entity_list_0_tenantId",
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
        "userId": "ecd57be47cbc4a0780bb355e6ff4d7a7",
        "name": "AT_test_612336",
        "description": "auto_desc_612336",
        "tenantId": "42ffdba38c58484f9be2bc1adf1672e6",
        "countryCode": "+86"
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
        },
        "name": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "tenantId": {
          "role": "context",
          "source": "context.tenantId"
        },
        "countryCode": {
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
        "query_params": {
          "tenantId": "$list.entity_list_0_tenantId",
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
        "userId": "ecd57be47cbc4a0780bb355e6ff4d7a7"
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
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
          "tenantId": "$list.entity_list_0_tenantId",
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
          "tenantId": "$list.entity_list_0_tenantId",
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
        "userId": "ecd57be47cbc4a0780bb355e6ff4d7a7"
      },
      "body_field_roles": {
        "userId": {
          "role": "id_ref"
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
          "tenantId": "$list.entity_list_0_tenantId",
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
        "newPassword": "M5Gj2Vp2XLcdrlrjfd8HpaVZ988ssycFd1JKlvXZUXFiveODO+TGBtW/peWw2tLySLxlYyJ5HJV+3N8dKA9vdepSx2YCLjR0kINpbUFJdoqLwFnfATaKf1AoFzZlFxAAegEwBL0dfQBG1fggk1uV8mXB7XxIEqELPxEn9rd/Ryg=",
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
        "userId": "ecd57be47cbc4a0780bb355e6ff4d7a7",
        "noticeType": []
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
          "role": "id_ref"
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
          "tenantId": "$list.entity_list_0_tenantId",
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
          "tenantId": "$list.entity_list_0_tenantId",
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
            "创建用户"
          ]
        },
        {
          "name": "entity_list_0_tenantId",
          "path": "entity.list[0].tenantId",
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
    "list",
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