"""
日志设置_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 11:11:15
目标URL: https://10.151.61.248/estack/web/estack/user-center/log/config

执行流程:
  1. 编辑
  2. 搜索验证（编辑后）
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
    "name": "日志设置",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/log/config"
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
      "action": "编辑",
      "label": "编辑",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/libra/v1/logs/config/period/update",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "effectiveYears": "-1"
      },
      "body_field_roles": {
        "effectiveYears": {
          "role": "static"
        }
      },
      "requires": []
    },
    {
      "action": "post",
      "label": "搜索验证（编辑后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/pegasi/v1/menu/tree",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "userId": None,
        "rootId": "all-resource",
        "isVisible": 1
      },
      "body_field_roles": {
        "userId": {
          "role": "context",
          "source": "access_log.entity_0_userId"
        },
        "rootId": {
          "role": "static"
        },
        "isVisible": {
          "role": "static"
        }
      },
      "requires": [],
      "assertion": "search_verify",
      "search_param": "rootId"
    }
  ],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {}
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