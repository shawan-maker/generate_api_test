"""
单位管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-10-08 10:07:22
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/unit

执行流程:
  1. 创建单位
  2. 搜索验证（创建单位后）
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
    "name": "单位管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/unit"
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
      "action": "创建单位",
      "label": "创建单位",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/tenants",
        "path_params": {},
        "query_params": {}
      },
      "body_template": {
        "adminUserCreateReqVO": {
          "userName": "${gen_test_name(\"userName\")}",
          "name": "${gen_test_name(\"name\")}",
          "password": "AoGkQmPowpeDvJ3PoGwU1MPPT7x9I3N96JNfdEFp6ixDn0imPZZhGsy2PR4qDiedPKcdb4VidkOMLczijw3FGUdEF02kZchu5Ud75xFShczCGpWoh4OLwJDBlclbhI37SrlhFfbDXUaJ3RJ4fGgbdIHuPqLuHeVAHP/mt7JPeJU=",
          "email": "XjVYSLdjiK8fbIFuZzVQbkgz//IWVWDaGP8OKUvJP6O2tePOayBFyY5fKFkHb0+GNrixDEkKSUP445wO5tlK+uUEHgCBWB3aMtMs0BeMNIX9uQBv1PVnMF7Yk7qNs8N163mR+w5+QNlAIcmB781yPyB3c2wuoLFr/RRmkRJ7sGo=",
          "phone": "Mpf48+TCyXxa+Vg7DTX3/8NO7WS1YqbiixIVUyo3Yb/LHS59ByfQocSxaSgcEmANMYxjH8tg44VuoYUUk4oVuKGIKwfOdc+Z9VchJGSt4/xN9OATMklFZ4E4+penumYeNEB8azRhqyWU/5Xn1EZHUhLSVElK1K/OjyX907iO3H0=",
          "description": "${gen_mutable_value()}",
          "countryCode": None
        },
        "name": "${gen_test_name(\"name\")}",
        "description": "${gen_mutable_value()}",
        "tenantType": "PHYSICAL"
      },
      "body_field_roles": {
        "adminUserCreateReqVO": {
          "role": "static"
        },
        "name": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "tenantType": {
          "role": "static"
        },
        "adminUserCreateReqVO.userName": {
          "role": "name"
        },
        "adminUserCreateReqVO.name": {
          "role": "name"
        },
        "adminUserCreateReqVO.password": {
          "role": "static"
        },
        "adminUserCreateReqVO.email": {
          "role": "static"
        },
        "adminUserCreateReqVO.phone": {
          "role": "static"
        },
        "adminUserCreateReqVO.description": {
          "role": "mutable"
        },
        "adminUserCreateReqVO.countryCode": {
          "role": "context",
          "source": "current_user.entity_countryCode"
        }
      },
      "requires": []
    },
    {
      "action": "get",
      "label": "搜索验证（创建单位后）",
      "api": {
        "method": "GET",
        "pathname": "/estack/api/estack/draco/v1/tenants/root",
        "path_params": {},
        "query_params": {
          "pageNum": "1",
          "pageSize": "10"
        }
      },
      "body_template": {},
      "body_field_roles": {},
      "requires": [],
      "assertion": "search_verify",
      "search_param": "name"
    }
  ],
  "state_assertions": {
    "state_field": None,
    "values_by_crud": {}
  },
  "pre_apis": [
    {
      "name": "获取当前用户信息",
      "id": "current_user",
      "method": "GET",
      "pathname": "/estack/api/estack/draco/v1/users/current-user",
      "depends_on": [],
      "extracts": [
        {
          "name": "entity_countryCode",
          "path": "entity.countryCode",
          "used_by": [
            "创建单位"
          ]
        }
      ],
      "body_template": {},
      "query_params": {}
    }
  ],
  "pre_api_refs": [
    "current_user"
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