"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-15 14:23:20
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 查询验证（创建用户后）
  3. 编辑
  4. 查询验证（编辑后）
  5. 冻结
  6. 查询验证（冻结后）
  7. 启用
  8. 查询验证（启用后）
  9. 锁定
  10. 查询验证（锁定后）
  11. 重置密码
  12. 迁移
  13. 删除
  14. 查询验证（删除后）
"""

import sys, json
from pathlib import Path

# 找到同目录的 lib/（脚本独立运行时使用）
_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))
from lib.runtime.test_runtime import TestRunner

MANIFEST = {
  "manifest_version": "1.0",
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
        "userName": "AT_test_452467",
        "password": "gj6hbKon7HByRYJNz/ISGSHzqIX1Fv1RyGoZW2i1Y4rf82pPDphVb6EKOoFsvKIN2H0ugXnvSKC6Xu3rQBlCZS1SbCVwNp2JbXfG4iQsX9SJ0KFC/hTewqEiW7xX8zzRmKA8o40qpWf/A9xXUlDdpQSXCZIuXlfGrWjVD99/SXA=",
        "name": "AT_test_452467",
        "email": "PirGjcoqS/gT9Cqb4cPXlCuTvP9y43v0OMWo4E+JMl2BYe4SLinQ7XIjlspw3xTs1K14JEHPxO1emu4lxbZY1YkFe2RRbKNeXkt+aJkgMhJy2oMHtcVI3siVOZUPihrxBjS2dp5KEis4koE9j2C8FCoLGSlBFO1M8av9xPnES14=",
        "phone": "R0+NfxTWmdRpj4Bsu0YksJfvtXkWQ0/3f3j5Km2ZK45r/Hu1TEbI3nfBMGBs6fGNfwPckmaBHmlADKAeA2CueefXPuvAywmYsjiGWsQTOfa9IwoTD/Kv5scLwO1INgHClDgZNcDXVSmhzw186OWJgTqjODKNTZX5PHu/rYgImWY=",
        "description": "auto_desc_452467",
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
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "查询验证（创建用户后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "78f2da3729b849008578f5d3a419ee31",
        "name": "AT_test_452467",
        "description": "auto_desc_452467",
        "tenantId": "42ffdba38c58484f9be2bc1adf1672e6",
        "countryCode": "+86",
        "adminId": "93552edc908e4dadae761fff1fd0f24c"
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
      "label": "查询验证（编辑后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "78f2da3729b849008578f5d3a419ee31",
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
      "label": "查询验证（冻结后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "启用",
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
      "label": "查询验证（启用后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "78f2da3729b849008578f5d3a419ee31",
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
      "label": "查询验证（锁定后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "重置密码",
      "label": "重置密码",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/password-policy",
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ]
    },
    {
      "action": "迁移",
      "label": "迁移",
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
      "action": "删除",
      "label": "删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/users/{id}",
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
      "label": "查询验证（删除后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {
          "tenantId": "$tenantId",
          "pageNum": "1",
          "pageSize": "10",
          "name": "AT_test_452467"
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
      "创建用户": "ENABLE",
      "编辑": "ENABLE",
      "冻结": "DISABLE",
      "启用": "ENABLE",
      "删除": "ENABLE"
    }
  }
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