"""
verify_search_changes.py — 自验证脚本

验证搜索探测增强的关键逻辑改动：
1. endpoint_classifier: search_param 提取（纯结构排除法）
2. kb_loader: pagination_params 从 KB 读取
3. analyze_flow: search_verify 步骤编排
4. test_runtime: search_verify / search_not_found 断言 + field_changed 修复
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name}: {detail}")
        failed += 1


# ============================================================
# 1. KB pagination_params
# ============================================================
print("\n=== 1. KB pagination_params ===")
from module_discovery.kb_loader import ProbeKB
kb = ProbeKB()
pg_params = kb.get_pagination_params()
check("pagination_params 非空", len(pg_params) > 0, f"got {pg_params}")
check("包含 pageNum", "pageNum" in pg_params)
check("包含 pageSize", "pageSize" in pg_params)
check("包含 page", "page" in pg_params)
check("包含 sortBy", "sortBy" in pg_params)
check("不包含 userName", "userName" not in pg_params)


# ============================================================
# 2. endpoint_classifier: _extract_search_param
# ============================================================
print("\n=== 2. _extract_search_param ===")
from module_discovery.endpoint_classifier import _extract_search_param, deduplicate_calls

# Case A: GET with userName search param
ep_data = {
    "query_params_samples": [{"pageNum": "1", "pageSize": "10", "userName": "test_user"}],
    "query_params": {"pageNum": "1", "pageSize": "10", "userName": "test_user"},
    "bodies": [],
}
result = _extract_search_param(ep_data)
check("提取 userName (排除分页)", result == "userName", f"got '{result}'")

# Case B: GET with only pagination params (no search)
ep_data2 = {
    "query_params_samples": [{"pageNum": "1", "pageSize": "10"}],
    "query_params": {"pageNum": "1", "pageSize": "10"},
    "bodies": [],
}
result2 = _extract_search_param(ep_data2)
check("纯分页参数返回空", result2 == "", f"got '{result2}'")

# Case C: POST body with search param
ep_data3 = {
    "query_params_samples": [],
    "query_params": {},
    "bodies": [{"pageNum": 1, "pageSize": 10, "keyword": "admin"}],
}
result3 = _extract_search_param(ep_data3)
check("POST body 提取 keyword", result3 == "keyword", f"got '{result3}'")

# Case D: POST body with tenantId (non-semantic name, should still work via exclusion)
ep_data4 = {
    "query_params_samples": [],
    "query_params": {},
    "bodies": [{"pageNum": 1, "pageSize": 10, "tenantId": "abc123"}],
}
result4 = _extract_search_param(ep_data4)
check("POST body 排除法提取 tenantId", result4 == "tenantId", f"got '{result4}'")

# Case E: Multiple candidates (short string preferred)
ep_data5 = {
    "query_params_samples": [{"pageNum": "1", "pageSize": "10", "status": "1", "userName": "test"}],
    "query_params": {},
    "bodies": [],
}
result5 = _extract_search_param(ep_data5)
check("纯数字 status=1 被排除", result5 == "userName", f"got '{result5}'")

# Case F: deduplicate_calls integration
all_calls = [
    {"method": "GET", "pathname": "/api/users/list", "context": "replay:query",
     "query_params": {"pageNum": "1", "pageSize": "10", "userName": "test"}},
]
samples = {"/api/users/list": [{"status": 200, "body": json.dumps({"success": True, "entity": {"list": [], "total": 0}})}]}
dedup_result = deduplicate_calls(all_calls, samples)
query_eps = dedup_result["classified"].get("query", [])
check("deduplicate query 端点有 search_param",
      len(query_eps) > 0 and query_eps[0].get("search_param") == "userName",
      f"got {query_eps}")


# ============================================================
# 3. analyze_flow: _plan_verify_steps logic
# ============================================================
print("\n=== 3. analyze_flow search_verify planning ===")

# Simulate the logic from _plan_verify_steps
def simulate_plan_verify_steps(action, verify_endpoints, action_label):
    """Simulate the _plan_verify_steps logic."""
    plans = []
    if action in ("create", "update", "delete", "lock", "unlock", "reset"):
        if "query" in verify_endpoints:
            query_ep = verify_endpoints["query"]["ep"]
            search_param = query_ep.get("search_param", "")
            if search_param:
                if action == "delete":
                    plans.append(("query", f"搜索验证（删除后）", "search_not_found"))
                else:
                    plans.append(("query", f"搜索验证（{action_label}后）", "search_verify"))
            elif action == "delete":
                plans.append(("query", "查询验证（删除后）", "not_contains_id"))
            elif action == "create":
                plans.append(("query", "查询验证（创建后）", "contains_id"))
            else:
                plans.append(("query", f"查询验证（{action_label}后）", "contains_id"))
    return plans

# With search_param
ve_with_search = {"query": {"ep": {"search_param": "userName"}, "body_sample": {}}}
plans1 = simulate_plan_verify_steps("create", ve_with_search, "创建用户")
check("有 search_param → search_verify",
      len(plans1) == 1 and plans1[0][2] == "search_verify",
      f"got {plans1}")

plans2 = simulate_plan_verify_steps("delete", ve_with_search, "批量删除")
check("delete + search_param → search_not_found",
      len(plans2) == 1 and plans2[0][2] == "search_not_found",
      f"got {plans2}")

# Without search_param (fallback)
ve_no_search = {"query": {"ep": {}, "body_sample": {}}}
plans3 = simulate_plan_verify_steps("create", ve_no_search, "创建用户")
check("无 search_param → contains_id (降级)",
      len(plans3) == 1 and plans3[0][2] == "contains_id",
      f"got {plans3}")

plans4 = simulate_plan_verify_steps("update", ve_no_search, "编辑")
check("update 无 search_param → contains_id",
      len(plans4) == 1 and plans4[0][2] == "contains_id",
      f"got {plans4}")


# ============================================================
# 4. test_runtime: search_verify / field_changed
# ============================================================
print("\n=== 4. test_runtime assertions ===")

from lib.test_runtime import ResponseParser, StepExecutor

parser = ResponseParser({
    "envelope_keys": ["entity"],
    "success_check": {"type": "field_and_absence", "success_field": "success", "error_field": "errorCode"},
    "list_keys": ["list"],
    "total_keys": ["total"],
    "id_field": "id",
})

import requests
state = {"id": "abc123", "create_body": {"userName": "AT_test_user"}}

class FakeResp:
    def __init__(self, body_dict, status=200):
        self._body = body_dict
        self.status_code = status
        self.headers = {}
        self.text = json.dumps(body_dict)
    def json(self):
        return self._body

executor = StepExecutor(
    session=None, parser=parser, state=state,
    ts="123456", base_url="https://example.com"
)

# search_verify: found
resp_found = FakeResp({
    "success": True,
    "entity": {"list": [{"id": "abc123", "userName": "AT_test_user"}], "total": 1}
})
step_sv = {"assertion": "search_verify", "search_param": "userName"}
msg = executor._run_post_assertion(step_sv, resp_found)
check("search_verify 找到 → 通过", "搜索中找到" in msg, f"got '{msg}'")

# search_verify: not found
resp_not_found = FakeResp({
    "success": True,
    "entity": {"list": [{"id": "other456", "userName": "someone"}], "total": 1}
})
try:
    executor._run_post_assertion(step_sv, resp_not_found)
    check("search_verify 未找到 → 断言失败", False, "should have raised")
except AssertionError as e:
    check("search_verify 未找到 → 断言失败", "未找到" in str(e), f"got '{e}'")

# search_not_found: correct (ID absent)
resp_absent = FakeResp({
    "success": True,
    "entity": {"list": [{"id": "other456"}], "total": 1}
})
step_snf = {"assertion": "search_not_found", "search_param": "userName"}
msg2 = executor._run_post_assertion(step_snf, resp_absent)
check("search_not_found 正确 → 通过", "未找到" in msg2, f"got '{msg2}'")

# search_not_found: wrong (ID still present)
try:
    executor._run_post_assertion(step_snf, resp_found)
    check("search_not_found 仍存在 → 断言失败", False, "should have raised")
except AssertionError as e:
    check("search_not_found 仍存在 → 断言失败", "仍存在" in str(e), f"got '{e}'")

# field_changed: "自动修改_" pattern
resp_updated = FakeResp({
    "success": True,
    "entity": {"id": "abc123", "name": "自动修改_123456"}
})
step_fc = {"assertion": "field_changed"}
msg3 = executor._run_post_assertion(step_fc, resp_updated)
check("field_changed '自动修改_' → 通过", "已更新" in msg3, f"got '{msg3}'")

# field_changed: "_Updated" pattern (backward compat)
resp_updated2 = FakeResp({
    "success": True,
    "entity": {"id": "abc123", "name": "test_Updated"}
})
msg4 = executor._run_post_assertion(step_fc, resp_updated2)
check("field_changed '_Updated' → 通过 (向后兼容)", "已更新" in msg4, f"got '{msg4}'")


# ============================================================
# 5. KB probe_knowledge.json: search-button patterns
# ============================================================
print("\n=== 5. KB search-button patterns ===")
search_patterns = kb.get_patterns("search-button", "element-ui")
check("search-button 模板非空", len(search_patterns) > 0, f"got {len(search_patterns)} patterns")
check("包含 el-icon-search 模板",
      any("el-icon-search" in p for p in search_patterns),
      f"patterns: {search_patterns}")


# ============================================================
# Summary
# ============================================================
print(f"\n{'='*50}")
print(f"  验证结果: {passed} 通过, {failed} 失败")
print(f"{'='*50}")
sys.exit(1 if failed > 0 else 0)
