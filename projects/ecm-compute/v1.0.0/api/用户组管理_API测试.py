"""
用户组管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-29 18:53:31
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/user-group

执行流程:
  1. 创建用户组
  2. 搜索验证（创建用户组后）
  3. 编辑
  4. 搜索验证（编辑后）
  5. 添加用户
  6. 搜索验证（添加用户后）
  7. 授权
  8. 搜索验证（授权后）
  9. 删除用户组
  10. 搜索验证（删除后）

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
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/user-group"
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
          "source": "users.entity_list_0_tenantId"
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
          "tenantId": "$users.entity_list_0_tenantId"
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
      "action": "编辑",
      "label": "编辑",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/groups/{path_0}",
        "path_params": {
          "path_0": {
            "source": "create.id",
            "match_from": "body_context"
          }
        },
        "query_params": {
          "name": "AT_test_678782",
          "description": "auto_desc_678782"
        }
      },
      "body_template": {
        "name": "${gen_test_name(\"name\")}",
        "description": "${gen_mutable_value()}",
        "groupId": None,
        "tenantId": None
      },
      "body_field_roles": {
        "name": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "groupId": {
          "role": "context",
          "source": "create.id"
        },
        "tenantId": {
          "role": "context",
          "source": "users.entity_list_0_tenantId"
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
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10",
          "tenantId": "$users.entity_list_0_tenantId"
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
      "action": "添加用户",
      "label": "添加用户",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/groups/userGroupAttachList",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "groupIds": [],
        "userIds": [],
        "tenantId": None,
        "flag": 1
      },
      "body_field_roles": {
        "groupIds": {
          "role": "context",
          "source": "create.id"
        },
        "userIds": {
          "role": "context",
          "source": "users.entity_list_0_id"
        },
        "tenantId": {
          "role": "context",
          "source": "users.entity_list_0_tenantId"
        },
        "flag": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "get",
      "label": "搜索验证（添加用户后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10",
          "tenantId": "$users.entity_list_0_tenantId"
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
              "source": "users.entity_list_0_id"
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
        "pathname": "/estack/api/estack/draco/v1/groups",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10",
          "tenantId": "$users.entity_list_0_tenantId"
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
      "action": "删除用户组",
      "label": "删除用户组",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/groups/batch-delete",
        "path_params": {},
        "query_params": {}
      },
      "body_template": [
        "27d07abaa79b4e79a7a83ecda91d837c"
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
          "tenantId": "$users.entity_list_0_tenantId"
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
    "state_field": "state",
    "values_by_crud": {
      "授权": "ERROR"
    },
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
            "添加用户",
            "编辑",
            "创建用户组"
          ]
        },
        {
          "name": "entity_list_0_id",
          "path": "entity.list[0].id",
          "used_by": [
            "添加用户"
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
    }
  ],
  "pre_api_refs": [
    "display_by_role",
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