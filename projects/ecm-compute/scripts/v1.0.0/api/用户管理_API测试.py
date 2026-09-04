"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-04 14:41:58
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 查询验证（创建后）
  3. 编辑
  4. 查询验证（编辑后）
  5. 冻结
  6. 查询验证（冻结后）
  7. 启用
  8. 查询验证（启用后）
  9. 重置密码
  10. 查询验证（重置密码后）
  11. 批量删除
  12. 查询验证（删除后）

状态断言:
  - 删除后数据不应出现
"""

import sys, json
from pathlib import Path

# 找到同目录的 lib/（脚本独立运行时使用）
_lib = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_lib.parent))
from lib.test_runtime import TestRunner

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
    "captcha": {
      "auth_button_text": "点击完成认证",
      "login_button_text": "登录"
    },
    "credentials_env": {
      "username": "APP_USER",
      "password": "APP_PASS"
    },
    "credentials_default": {
      "username": "estack-yy",
      "password": "R@9eDuck$!mpleM00n"
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
        "userName": "autotest496052",
        "password": "cRTuXumd/pUcvTmWvm7OaEf4VGgKwCqQq9tTRvh+WRf+hQZPmi5XP++8oovsVVCJQ7sE5yNhx/fn0rCxPn5NyVQfDbHyp+ZgI/7X+eWZ987yAN3HWmAmm5HRgmwRm76wxBtaUoNN9W5Eh3mjrTsTcbOOBxB1XuiwgK+hzWYSnCk=",
        "name": "test_496052",
        "email": "ArEN/57hYmo82S4QlwK5D/VhZZE/z7lbvis8ThfnDmtZ/AnnxP5P0ogIKfTOsEQDrbKFrJzw+znTtBvj+3zdnH3XCaF+mi5AQV9czTU3RyooIz0hz7gkB9JsKfQREEVNhBEuMJRW4XDz/UWDuxcMcLbARM9AbFXz1ROWfaScqBo=",
        "phone": "D2zr64rw1ZovsQZS+SEtwzCYfaSfV9HREMlbxkluqgrVS1chPZc0Ist9e6I2nH12BlU+ApmDwlDP78PV7n5vyCE4AqLWAWacb2zCYtwwTmFsplA+l9WdUR8cbT3db/5zRqe3DxtHwjUe1kGdvjYi1Q/lS2S7hm7kqS5QJi0LU34=",
        "description": "auto_desc_496052",
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
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "ef3949bfc9c64f2f93ff2649dbfe6a09",
        "name": "test_496074",
        "description": "auto_desc_496074",
        "tenantId": "34e2594fca9e44cd9e23b25474838c7f",
        "countryCode": "+86",
        "phone": "KTGXtH9J/7Qi49jDFDPNolslOA/NyZW56vVPrha1NyBbOauebG+2FZIhPGMuLY4G7KkRF/YzhDvGE2FJOUnsH1PX8EnUCFCP8A+wEgEW1/HYHSAJEx6QRPQ5GEwCNv6aL0I4ZvT+NeyMZT7nQcawEWgN26Z9T7qXfl4t1WozXgI=",
        "email": "Q0JiHO0OvyPF0bf+O5ulGJzPzvyv5HOgTXRpNmtH5fhYXP2q+BFkATcIfXRdYq0faInExZ1KdAqfXWgQH1awn/CwX6rUPZe9u413Jls+ttkCVbJZz8CrYbcPAtOYXPevGbFt6/zLZas5mkkRF8NZ7wxkn0ntm5z0ADkk5VtMv1w=",
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
        "phone": {
          "role": "static"
        },
        "email": {
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
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "ef3949bfc9c64f2f93ff2649dbfe6a09",
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
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
      "label": "查询验证（启用后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "userId": "ef3949bfc9c64f2f93ff2649dbfe6a09",
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
      "label": "查询验证（重置密码后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
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
        "ef3949bfc9c64f2f93ff2649dbfe6a09"
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
      "label": "查询验证（删除后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/users",
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "not_contains_id"
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
  }
}

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    runner = TestRunner(MANIFEST)
    steps = sys.argv[1:] if len(sys.argv) > 1 else None
    runner.run(steps_filter=steps)