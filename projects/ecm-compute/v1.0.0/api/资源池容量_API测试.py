"""
资源池容量_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-09-24 10:15:03
目标URL: https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/resource-pool-capacity

执行流程:
  1. query
  2. 搜索验证（query后）
  3. 资源池容量
  4. 搜索验证（资源池容量后）
  5. 修改容量
  6. 搜索验证（删除后）
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
    "name": "资源池容量",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/resource-pool-capacity"
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
      "action": "query",
      "label": "query",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（query后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "poolOrDataCenterId"
    },
    {
      "action": "资源池容量",
      "label": "资源池容量",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（资源池容量后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_verify",
      "search_param": "poolOrDataCenterId"
    },
    {
      "action": "修改容量",
      "label": "修改容量",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "搜索验证（删除后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/virgo/v1/capacity/statistics-pool",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "poolOrDataCenterId": None,
        "productNames": []
      },
      "body_field_roles": {
        "poolOrDataCenterId": {
          "role": "context",
          "source": "pools.entity_0_pools_0_poolId"
        },
        "productNames": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "search_not_found",
      "search_param": "poolOrDataCenterId"
    }
  ],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {}
  },
  "pre_apis": [
    {
      "name": "前置 API: pools",
      "id": "pools",
      "method": "GET",
      "pathname": "/estack/api/estack/pegasi/v1/service-menu/service-item/pools",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_pools_0_poolId",
          "path": "entity[0].pools[0].poolId",
          "used_by": [
            "query",
            "资源池容量",
            "post",
            "修改容量"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "pools"
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