"""
用户组管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-24 15:56:08
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/group

执行流程:
  1. 创建用户组
  2. 搜索验证（创建用户组后）
  3. 批量删除
  4. 搜索验证（删除后）

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
    "name": "用户组管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/group"
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
      "action": "创建用户组",
      "label": "创建用户组",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "organizationId": None,
        "name": "${gen_test_name(\"name\")}",
        "description": "${gen_mutable_value()}",
        "tenantId": None
      },
      "body_field_roles": {
        "organizationId": {
          "role": "context",
          "source": "display_by_role.entity_0_name"
        },
        "name": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "tenantId": {
          "role": "context",
          "source": "display_by_role.entity_0_id"
        }
      },
      "requires": [],
      "extract": {
        "id": "entity.id",
        "names": [
          "name"
        ]
      }
    },
    {
      "action": "get",
      "label": "搜索验证（创建用户组后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10",
          "tenantId": "$display_by_role.entity_0_id"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "name",
      "search_param_source": "name"
    },
    {
      "action": "批量删除",
      "label": "批量删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/groups/batch-delete",
        "path_params": {},
        "query_params": {}
      },
      "body_template": [
        "85250deb74504ff0ad174d2dd2b195a6"
      ],
      "body_field_roles": {
        "__array_items__": {
          "role": "context",
          "source": "create.id",
          "is_array": True
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（删除后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10",
          "tenantId": "$display_by_role.entity_0_id"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [
        "id"
      ],
      "assertion": "search_not_found",
      "search_param": "name",
      "search_param_source": "name"
    }
  ],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {},
    "after_delete": "NOT_EXIST"
  },
  "pre_apis": [
    {
      "name": "前置 API: display-by-role",
      "id": "display_by_role",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/display-by-role",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_name",
          "path": "entity[0].name",
          "used_by": [
            "创建用户组"
          ]
        },
        {
          "name": "entity_0_id",
          "path": "entity[0].id",
          "used_by": [
            "创建用户组"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
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