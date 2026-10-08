"""
容量预测_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 11:02:18
目标URL: https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/capacity-forecast

执行流程:
  1. 确定
  2. 查询验证（确定后）
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
    "name": "容量预测",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/project-manage/capacity-manage/capacity-forecast"
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
      "action": "确定",
      "label": "确定",
      "phases": [
        {
          "id": "savePredictionInfo",
          "label": "确定(savePredictionInfo)",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/virgo/v1/capacity-prediction/savePredictionInfo",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "period": None,
            "itemIdList": [
              "29fb3aebfd2c4eb8b6cf80d2d5dfb638",
              "e9db5f9e19694521959375a6dd84b58d",
              "a076430821c14b30a9010cefedeb3f21"
            ],
            "isNext": False,
            "poolId": None
          },
          "body_field_roles": {
            "period": {
              "role": "context",
              "source": "queryPredictionInfo.entity_period"
            },
            "itemIdList": {
              "role": "static"
            },
            "isNext": {
              "role": "static"
            },
            "poolId": {
              "role": "context",
              "source": "queryPredictionInfo.entity_poolId"
            }
          },
          "extract": []
        },
        {
          "id": "main",
          "label": "确定",
          "api": {
            "method": "POST",
            "pathname": "/estack/api/estack/virgo/v1/capacity-prediction/savePredictionInfo",
            "path_params": {},
            "query_params": {}
          },
          "body_template": {
            "period": None,
            "itemIdList": [
              "29fb3aebfd2c4eb8b6cf80d2d5dfb638",
              "e9db5f9e19694521959375a6dd84b58d",
              "a076430821c14b30a9010cefedeb3f21"
            ],
            "isNext": False,
            "poolId": None
          },
          "body_field_roles": {
            "period": {
              "role": "context",
              "source": "queryPredictionInfo.entity_period"
            },
            "itemIdList": {
              "role": "static"
            },
            "isNext": {
              "role": "static"
            },
            "poolId": {
              "role": "context",
              "source": "queryPredictionInfo.entity_poolId"
            }
          }
        }
      ],
      "requires": []
    },
    {
      "action": "get",
      "label": "查询验证（确定后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/pegasi/v1/service-menu/service-item/all-pools",
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
      "name": "前置 API: queryPredictionInfo",
      "id": "queryPredictionInfo",
      "method": "GET",
      "pathname": "/estack/api/estack/virgo/v1/capacity-prediction/queryPredictionInfo",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_period",
          "path": "entity.period",
          "used_by": [
            "*"
          ]
        },
        {
          "name": "entity_poolId",
          "path": "entity.poolId",
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
    "queryPredictionInfo"
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