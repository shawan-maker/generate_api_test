"""
AccessKey设置_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-24 16:00:01
目标URL: https://10.151.61.248/estack/web/estack/user-center/account-manage/access-key-manage

执行流程:
  1. 创建
  2. 查询验证（创建后）
  3. 禁用
  4. 查询验证（禁用后）
  5. 删除
  6. 查询验证（删除后）

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
  "manifest_version": "1.0",
  "module": {
    "name": "AccessKey设置",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/account-manage/access-key-manage"
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
    "id_field": "accessKeyId"
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
      "action": "创建",
      "label": "创建",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/accesskey/create",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [],
      "extract": {
        "accessKeyId": "entity.accessKeyId",
        "id": "entity.accessKeyId",
        "names": []
      }
    },
    {
      "action": "post",
      "label": "查询验证（创建后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/accesskey/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "禁用",
      "label": "禁用",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/accesskey/{path_0}/update",
        "path_params": {
          "path_0": {
            "source": "create.accessKeyId",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "id": None,
        "accessKeyId": None,
        "accessKeyStatus": "DISABLE"
      },
      "body_field_roles": {
        "id": {
          "role": "context",
          "source": "create.accessKeyId"
        },
        "accessKeyId": {
          "role": "context",
          "source": "create.accessKeyId"
        },
        "accessKeyStatus": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "查询验证（禁用后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/accesskey/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "删除",
      "label": "删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/accesskey/{path_0}",
        "path_params": {
          "path_0": {
            "source": "create.accessKeyId",
            "match_from": "body_context"
          }
        },
        "query_params": {}
      },
      "body_template": {
        "id": None
      },
      "body_field_roles": {
        "id": {
          "role": "context",
          "source": "create.accessKeyId"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "查询验证（删除后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/accesskey/list",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "not_contains_id"
    }
  ],
  "state_assertions": {
    "state_field": "status",
    "values_by_crud": {
      "创建": "AVAILABLE"
    },
    "after_create": "AVAILABLE",
    "after_delete": "NOT_EXIST"
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