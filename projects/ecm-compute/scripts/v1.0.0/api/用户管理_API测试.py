"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-03 14:38:19
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 重置密码
  3. 更多
  4. 查询
  5. 编辑
  6. 启用
  7. 批量删除

状态断言:
  - 删除后数据不应出现
"""

import sys, json
from pathlib import Path

# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）
_root = Path(__file__).resolve()
while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

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
        "userName": "autotest417387",
        "password": "cEskk3LJ4WEVoTVYb7p31s9fEhvHxE2xfJDgaCI8Lwcm6Sm+EXyVwFq4CsGS+KBPsIBJYBdWl87x0ZfwTgwARz7KZqhhRimWfbOMTIwJdvcAwSBX8MfaM/nmwXnlAJfCOaHoViDs4O/Cz5zhXVBAhyjUynCtNreq3BuCLIOVFCg=",
        "name": "test_417387",
        "email": "bL5qXNYbieDntjIX4DHCURVf7DdpMRkbrGTKTr6OXq2tP6UbZWVICTftyEsFkL93YZ1fHIaECBG11z5jjcbI8BlYgd9MzAgNin56v6Ta+ZsIUXZWxpVqvVVIEyqw+U+dLoTVI21r5AVcfFDVeQuO9GBOXlhUNDfz4lScJsqBhbQ=",
        "phone": "bO25ofhDb2gtVRRFro6/7OeSp3udnsRzfFNoh0zDmbQKpnJ34nm1LeZsl1drPLsnMXVfind0n9GmV4C8j1bMTQ0eJBT5XkRxvEZzFtnlLWzULZ8etgQye5wjexM3oi4PVE4HapvNbyRjg/m2FWiatsxUa4w0bIpdXzROtpxjVXc=",
        "description": "auto_desc_417387",
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
      "action": "reset",
      "label": "重置密码",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/password-reset/reset",
        "query_params": {}
      },
      "body_template": {
        "forceChangePassword": 1,
        "isRandomPassword": 1,
        "newPassword": "TJDzBB0yux2AHhinEcJ+YNeoi6+c8kc6IlZaJbsfsT8Plc2bC6LTXjQzKBiLStJ/OJOsRns7H+KGyNuUfcx3wlMXNEewj1spoQZOQNcuIGiWW7OIVpnDX1s3FzdlOZtOzvI9X3LWCUEtVmmfWBHxZ9mSpxwVUUiOr/aaHoM0OAY=",
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
        "userId": "0f8e1b534a8f4d31a8839c157b993aca",
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
      "action": "execute",
      "label": "更多",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/display-by-role",
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ]
    },
    {
      "action": "query",
      "label": "查询",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/policies/list",
        "query_params": {}
      },
      "body_template": {
        "policyType": "SYSTEM",
        "policyCategory": "ORGANIZATION",
        "tenantId": "34e2594fca9e44cd9e23b25474838c7f"
      },
      "body_field_roles": {
        "policyType": {
          "role": "static"
        },
        "policyCategory": {
          "role": "static"
        },
        "tenantId": {
          "role": "context",
          "source": "context.tenantId"
        }
      },
      "requires": [
        "id"
      ]
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
        "userId": "0f8e1b534a8f4d31a8839c157b993aca",
        "name": "test_417409",
        "description": "auto_desc_417409",
        "tenantId": "34e2594fca9e44cd9e23b25474838c7f",
        "countryCode": "+86",
        "phone": "JYiGkawxmqRrnnW0xNnT9tnoNjRuKnKFW8qAvmocNdZ6hHx/F6KE3pnaMtR/veXLHuEsJPMx2Po9Axcxci6iG8t8DO006YVHNlVf37xkKZrbtzEm/uzRJgn+rmF4jExkuVhVDCcXIS21k9n5DcGi71INz5BsoWgTpG6umRZK4/g=",
        "email": "dkCU75JkQGoLusxN8HF2TSAKed/jps+Xn7vZuMNWLNKR8cuX8BIGMunFcBpxMvWec/NWJFXYCiZ//0DKoAhlLBlXYgiBAQd6RpG5gwCIg6CUJl3sO5SExmc0nCWrugwODL/TCGzv3BR2shS3VjTktJDp7kMhw/Zg92byef+toqQ=",
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
      "action": "delete",
      "label": "批量删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/users/batch/delete",
        "query_params": {}
      },
      "body_template": [
        "0f8e1b534a8f4d31a8839c157b993aca"
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
    }
  ],
  "state_assertions": {
    "state_field": "state",
    "values_by_crud": {
      "create": "ENABLE",
      "update": "ENABLE",
      "unlock": "ENABLE"
    },
    "after_create": "ENABLE",
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