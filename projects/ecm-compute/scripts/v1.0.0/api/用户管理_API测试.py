"""
用户管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-11 15:39:28
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user

执行流程:
  1. 创建用户
  2. 编辑
  3. 冻结
  4. 启用
  5. 重置密码
  6. 批量删除

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
        "userName": "autotest112205",
        "password": "dU4OokoThhVbtvkzxMg8W2MKNCSqcR/eNRg0wK0JOVw0lg0eepyjd4fJwHaaXCb/Xu2GaLK919P9dt76oCDUZpipqrkTQgHtXX+O4hGXtRg3kfWMNKNs9iW0lkBmAZAzeRTWI0PPTS8Bf/TI43FoH7zAuLE6vRZO0GkDKVEZuCo=",
        "name": "test_112205",
        "email": "fnd2rRMZbOHDudE87jKlNiuNydtkVy/QnrdwhhLoL9EsPmLpGBusl5Ion6Ijq9BmzfvavxWDEHJuR/XSNOBDtAg/C98+1JFn5/dkkhDPBEsHrjOfwXh5wNjVVvNpvmF1yIN0YUvxhPsDneBeoUgW9Zn/QVA+PdtIsYitTsQXpjw=",
        "phone": "A5IMhFVe8I5ZiSRh3gUhgG5YbPnVVgHHNqBB6fzutENatj8Vv31yb4pOXQfuQn+pBW/ObQvsEtrPZvieFM7b2Ecga7Ps/YfzDxkvMlK4N0pgHMXPwlyUZFQltxIJOQ1WGxIcanX/AG0LvIcbujvjjFSHA+prTD6CIe+w3cCXF9g=",
        "description": "auto_desc_112205",
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
          "role": "test_value",
          "value_pattern": "hex_hash"
        },
        "tenantId": {
          "role": "pre_api_ref",
          "source": "display_unit_tree_ignore_currentuser.entity_0_id"
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
      "action": "update",
      "label": "编辑",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/users/{id}",
        "query_params": {}
      },
      "body_template": {
        "userId": "1c44fd6a191d40b3bafa17b0ad0c6cdf",
        "name": "test_112246",
        "description": "auto_desc_112246",
        "tenantId": "42ffdba38c58484f9be2bc1adf1672e6",
        "countryCode": "+86",
        "phone": "CH9buE4vsy7Hw9yqb0ikBT778f6WyhkXe3iJs1iodhyCxafayCJ0Ufcf3/jlNNMcar7lJRR4RYjX/lKaZPC3PV1U7AWk20sDtY4TOk0G+un+Zv1gUc3yhAHrvgQToJdp9MFVD5S4nrlwwoXIyqj9i9QOIgCDE84sqsZq1XQF10c=",
        "email": "AjFkKrNKqnsy2OYoTsI3pm2jpT7ZTumzJSnBZmFTFRMiQce/gAsh1wktNGsebOeZ830C7iJf9ZrWT1WlN5yJo0/VVYqF75EBCyPZDKT57PakU8YTjvOrDC64hrDDU5iJz3Fd+lnTiL0nbMZdXYwtTsCNaLfkKKR24sO3B6P+/vs=",
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
          "role": "pre_api_ref",
          "source": "display_unit_tree_ignore_currentuser.entity_0_id"
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
          "role": "pre_api_ref",
          "source": "access_log.entity_0_userId"
        }
      },
      "requires": [
        "id"
      ]
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
        "userId": "1c44fd6a191d40b3bafa17b0ad0c6cdf",
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
      "action": "reset",
      "label": "重置密码",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/password-reset/generate",
        "query_params": {}
      },
      "body_template": {
        "userId": "1c44fd6a191d40b3bafa17b0ad0c6cdf",
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
      "action": "delete",
      "label": "批量删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/users/batch/delete",
        "query_params": {}
      },
      "body_template": [
        "1c44fd6a191d40b3bafa17b0ad0c6cdf"
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
      "name": "前置 API: display-unit-tree-ignore-currentuser",
      "id": "display_unit_tree_ignore_currentuser",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/display-unit-tree-ignore-currentuser",
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
            "update"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "display_unit_tree_ignore_currentuser",
    "access_log"
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