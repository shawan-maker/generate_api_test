"""
容量告警_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:59:45
目标URL: https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/capacity-warning

执行流程:
  1. 初始列表查询（获取 ID）
  2. 阈值设置
  3. 查询验证（阈值设置后）
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
    "name": "容量告警",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/capacity-warning"
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
      "list",
      "records",
      "rows",
      "items"
    ],
    "total_keys": [
      "total",
      "totalCount",
      "count"
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
      "label": "初始列表查询（获取 ID）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/pegasi/v1/service-menu/service-item/pools",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [],
      "extract": {
        "id": "entity.list_0.id"
      }
    },
    {
      "action": "阈值设置",
      "label": "阈值设置",
      "phases": [
        {
          "id": "item-thresholds",
          "label": "阈值设置(item-thresholds)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/virgo/v1/capacity-alarms/item-thresholds",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "poolId": None,
            "resourceTypes": []
          },
          "body_field_roles": {
            "poolId": {
              "role": "context",
              "source": "pools.entity_0_pools_0_poolId"
            },
            "resourceTypes": {
              "role": "static"
            }
          },
          "extract": []
        },
        {
          "id": "item-thresholds",
          "label": "阈值设置(item-thresholds)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/virgo/v1/capacity-alarms/item-thresholds",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "poolId": None,
            "resourceTypes": []
          },
          "body_field_roles": {
            "poolId": {
              "role": "context",
              "source": "pools.entity_0_pools_0_poolId"
            },
            "resourceTypes": {
              "role": "static"
            }
          },
          "extract": []
        },
        {
          "id": "main",
          "label": "阈值设置",
          "api": {
            "method": "PUT",
            "pathname": "/estack/api/estack/virgo/v1/capacity-alarms/item-thresholds",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "capacityAlarmThresholdVos": [
              {
                "id": "0b09cf8d26db412db0d33d4bacd5a6f9",
                "itemCapacityId": None,
                "itemName": "MySQL_MEM",
                "itemDescription": "MySQL的内存数目",
                "poolId": "CIDC-ESTACK-31",
                "resourceType": "MySQL",
                "targetName": "MySQL",
                "createdAt": "2026-10-08 10:57:35",
                "updatedAt": None,
                "usedRate": 0.11,
                "enableCommonAlarm": False,
                "enableMajorAlarm": False,
                "enableCriticalAlarm": False,
                "deleted": False
              },
              {
                "id": "ebd4adc733764c77add0745e2dbffb2c",
                "itemCapacityId": None,
                "itemName": "MySQL_CPU",
                "itemDescription": "MySQL的CPU数目",
                "poolId": "CIDC-ESTACK-31",
                "resourceType": "MySQL",
                "targetName": "MySQL",
                "createdAt": "2026-10-08 10:57:35",
                "updatedAt": None,
                "usedRate": 0.04,
                "enableCommonAlarm": False,
                "enableMajorAlarm": False,
                "enableCriticalAlarm": False,
                "deleted": False
              }
            ],
            "poolId": None,
            "resourceType": None
          },
          "body_field_roles": {
            "capacityAlarmThresholdVos": {
              "role": "static"
            },
            "poolId": {
              "role": "context",
              "source": "pools.entity_0_pools_0_poolId"
            },
            "resourceType": {
              "role": "context",
              "source": "products.entity_0_resourceType"
            }
          }
        }
      ],
      "requires": []
    },
    {
      "action": "get",
      "label": "查询验证（阈值设置后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/pegasi/v1/service-menu/service-item/pools",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [],
      "assertion": "contains_id"
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
            "*"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: products",
      "id": "products",
      "method": "GET",
      "pathname": "/estack/api/estack/virgo/v1/pool-quota-items/products",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_resourceType",
          "path": "entity[0].resourceType",
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
    "pools",
    "products"
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