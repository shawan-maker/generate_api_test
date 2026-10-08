"""
全局单位配额_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:50:31
目标URL: https://10.151.61.248/estack/web/estack/user-center/project-manage/quota-manage/global-organization-quota

执行流程:
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
    "name": "全局单位配额",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/project-manage/quota-manage/global-organization-quota"
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
  "steps": [],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {}
  },
  "pre_apis": [
    {
      "name": "前置 API: root",
      "id": "root",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/tenants/root",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_list_0_id",
          "path": "entity.list[0].id",
          "used_by": [
            "前往项目配额",
            "修改配额",
            "申请历史",
            "修改保留配额",
            "全局单位配额",
            "批量修改配额"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    },
    {
      "name": "前置 API: pools",
      "id": "pools",
      "method": "GET",
      "pathname": "/estack/api/estack/virgo/v1/tenant-quota-items/158222ee87484790ae9651a459e82d0a/pools",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_0_pools_0_poolId",
          "path": "entity[0].pools[0].poolId",
          "used_by": [
            "前往项目配额",
            "修改配额",
            "申请历史",
            "修改保留配额",
            "全局单位配额",
            "批量修改配额"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "root",
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