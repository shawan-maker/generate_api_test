"""
角色管理_API测试.py — 由 module_discovery 自动生成 (manifest 模式)
生成时间: 2026-08-28 11:30:32
目标URL: https://10.151.61.248/estack/web/estack/user-center/user-manage/role

执行流程:
  1. 创建
  2. 查询验证（创建后）
  3. 修改
  4. 查询验证（修改后）
  5. 删除
  6. 查询验证（删除后）

状态断言:
  - 删除后数据不应出现
"""

import sys, json
from pathlib import Path

# 确保能找到 lib（向上搜索含 lib/auth.py 的仓库根）
_root = Path(__file__).resolve()
while _root.parent != _root and not (_root / 'lib' / 'auth.py').exists():
    _root = _root.parent
sys.path.insert(0, str(_root))

from lib.test_runtime import TestRunner

MANIFEST = {
  "manifest_version": "1.0",
  "module": {
    "name": "角色管理",
    "base_url": "https://10.151.61.248",
    "login_url": "https://10.151.61.248/estack/web/estack/login",
    "target_url": "https://10.151.61.248/estack/web/estack/user-center/user-manage/role"
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
    "captcha": {
      "auth_button_text": "点击完成认证",
      "login_button_text": "登录"
    },
    "credentials_env": {
      "username": "APP_USER",
      "password": "APP_PASS"
    },
    "credentials_default": {
      "username": "estack-yy",
      "password": "R@9eDuck$!mpleM00n"
    }
  },
  "steps": [
    {
      "action": "create",
      "label": "创建",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/policies",
        "query_params": {}
      },
      "body_template": {
        "tenantId": "cf2ecc23183244d8959023943338ac98",
        "policyName": "autotest87823612",
        "description": "自动测试创建于823620",
        "policyCategory": "ORGANIZATION",
        "menuIdList": [
          "all-resource",
          "a92d9efd05da4b1db5e3ee2f630e82d7",
          "2dbfe534b60d46189c727ab9a1613c31",
          "ccc0450fd0174f66b55f49c3632a4992",
          "fae3d97810c94a1482b227ecd26c3245",
          "ed76f32fd324468ea41b3648a8820df7",
          "b89a5e55185c4221948f400650c0c295",
          "573d7fa496684d4a9f5971dbe2caad5d",
          "b2dc70c7f80849d89c7826345977a4a6",
          "6e293f4a1f664208ab4b301c58b9fb44",
          "8363eea87dd34a7ab05835d787dc466d",
          "d9c6fabfa3dd42e794c79b660634c90b",
          "83cb6834fa444d738e03a26ff1122119",
          "2c72d9a9f5c4446c8a006d0e722d1f58",
          "36c325679bdf4997a10d728f76388a30",
          "b15cfad201554aabadbd250e364b9721",
          "ac7d649c721a4314a67bad03848a42ca",
          "3312b058fa894996960ef3ad951dd1da",
          "f6dad81fdd9a4cb28be4fcac4285d656",
          "a1ef3ab0e0704367aa8548ae1dbaa2e7",
          "ce71e113b1ae484dae21f93d35c0fc96",
          "00fd4034e21f4c9fb68e42932d169a65",
          "62a4d9110008467180f9bb61eb8d5b3a",
          "647d881e603341d0b03783551a25a675",
          "d58a037291c14257a4d9a8cb97b70df0",
          "5188297991d946f78bba1df27f509c8e",
          "6191622cfa2241d0aaea991be304d321",
          "b921a6c5018146f180bb6ad63425aa5f",
          "43c6f09a2750447cb23f4c57e02f8ab5",
          "d3d84a0fb13f4ef8b6018958f17d9038",
          "775d69a08f60425a9c9162f5b279fb65",
          "7817d9ba9a594601937f44486fa1638b",
          "87b95f407d8d40a9b0f8f77a259f07a9",
          "657309ff5fc8481d8d31fb5ad6ae4aa0",
          "d5d72305d6f745abab87484896522756",
          "91c45f372ae34f36905a26706c1adea6",
          "aa7fbebe5512478189ca65ddc0e52188",
          "da036e06d69a435e8a40191af84f6682",
          "4fd4b119c6bf46afa2994b879218270b",
          "3f921bc4b84f4774a76475e7432890db",
          "3375fec9a10643769b9e830df79a4874",
          "8d46f545e5e145a38ffcdda832bb69aa",
          "4ad620b66f6d498ead44b40c3ee98b1f",
          "365acbab232e44e1a88f3df622f0427f",
          "8a60979c2a1d4390ab0d017ecb6b4334",
          "60597817b8e34d4595a940e19a632220",
          "c07b73c8f2f24ec783c64a6ed5395fde",
          "c7ef52f7cedf4fa4b6ab837377ca4fec",
          "178e64c3c36d4470bf76c8c82d40a40e",
          "3b8d12c96b0d4b4daf1bab3a8371bc48",
          "07845ff1e0454cfa91c232778172ee50",
          "b9ba6e31bfc8499d8376d8e3d89e5d1d",
          "c5f728a9616d4dcdaac9845e00e4da7a",
          "e367f36085d34d29bd040c552cb6a5b6",
          "257c5dc4bc824e1c9183887485ab4b0a",
          "e3c54fc65514497784a6f7352f5f46f0",
          "b3f4f356da4e45cbb00817e996d47779",
          "fd74c14f8c924fc39f52f4df376f71ec",
          "dee3f9266a264706b232ab4c321bf695",
          "91cc9b68c0394312903a269f99a19019",
          "4e15bf6d440b4462b1dd3ef801d371dc",
          "d4d3df4c79664188a35640efa408e986",
          "60670423261744f9a68009944fa26861",
          "9f1b1143d6d240fd8dcde58f3b72e6ce",
          "e9be7a734de6453dac935fce138bb971",
          "a72e01f8b8c4419495fca22be3fb25ae",
          "76cd8d79ff2f4e9db550c2cc544e30c0"
        ],
        "policyDocument": "{\"version\":\"v1\",\"statement\":[{\"effect\":\"allow\",\"action\":[\"WORKFLOW:*\"],\"resource\":[\"*\"]}]}"
      },
      "body_field_roles": {
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
        },
        "policyName": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "policyCategory": {
          "role": "static"
        },
        "menuIdList": {
          "role": "static"
        },
        "policyDocument": {
          "role": "static"
        }
      },
      "requires": [],
      "extract": {
        "id": "entity.id",
        "names": [
          "policyName"
        ]
      }
    },
    {
      "action": "post",
      "label": "查询验证（创建后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/policies/list",
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "tenantId": "cf2ecc23183244d8959023943338ac98",
        "isIncludeDefaultPolicy": True
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
        },
        "isIncludeDefaultPolicy": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "update",
      "label": "修改",
      "api": {
        "method": "PUT",
        "pathname": "/estack/api/estack/draco/v1/policies/{id}",
        "query_params": {}
      },
      "body_template": {
        "id": "0d8830f139524f19a9bd30d96cb8aace",
        "policyName": "autotest87823612",
        "description": "auto_edited_823625",
        "tenantId": "cf2ecc23183244d8959023943338ac98"
      },
      "body_field_roles": {
        "id": {
          "role": "id_ref"
        },
        "policyName": {
          "role": "name"
        },
        "description": {
          "role": "mutable"
        },
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
        }
      },
      "requires": [
        "id"
      ]
    },
    {
      "action": "post",
      "label": "查询验证（修改后）",
      "api": {
        "method": "POST",
        "pathname": "/estack/api/estack/draco/v1/policies/list",
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "tenantId": "cf2ecc23183244d8959023943338ac98",
        "isIncludeDefaultPolicy": True
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
        },
        "isIncludeDefaultPolicy": {
          "role": "static"
        }
      },
      "requires": [
        "id"
      ],
      "assertion": "contains_id"
    },
    {
      "action": "delete",
      "label": "删除",
      "api": {
        "method": "DELETE",
        "pathname": "/estack/api/estack/draco/v1/policies/{id}",
        "query_params": {
          "tenantId": "cf2ecc23183244d8959023943338ac98"
        }
      },
      "body_template": {
        "roleId": "0d8830f139524f19a9bd30d96cb8aace",
        "tenantId": "cf2ecc23183244d8959023943338ac98"
      },
      "body_field_roles": {
        "roleId": {
          "role": "id_ref"
        },
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
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
        "pathname": "/estack/api/estack/draco/v1/policies/list",
        "query_params": {}
      },
      "body_template": {
        "pageNum": 1,
        "pageSize": 10,
        "tenantId": "cf2ecc23183244d8959023943338ac98",
        "isIncludeDefaultPolicy": True
      },
      "body_field_roles": {
        "pageNum": {
          "role": "static"
        },
        "pageSize": {
          "role": "static"
        },
        "tenantId": {
          "role": "context",
          "source": "create_body.tenantId"
        },
        "isIncludeDefaultPolicy": {
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
    "state_field": None,
    "values_by_crud": {},
    "after_delete": "NOT_EXIST"
  }
}

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    runner = TestRunner(MANIFEST)
    steps = sys.argv[1:] if len(sys.argv) > 1 else None
    runner.run(steps_filter=steps)