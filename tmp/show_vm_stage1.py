import json
from pathlib import Path

base = Path(r"D:\Mobile\API_AI_test\workspace\eis\kb\module_discovered")
ui = json.loads((base / "云主机-云主机_ui.json").read_text(encoding="utf-8"))
pb = json.loads((base / "云主机-云主机_playbook.json").read_text(encoding="utf-8"))

print("=" * 72)
print("云主机-云主机（/page/eis/virtualManage）Stage 1 业务探测结果")
print("=" * 72)

s = ui.get("summary", {})
print(f"\n【探测概况】按钮 {s.get('total_buttons')} 个 | has_create={s.get('has_create')} "
      f"| has_delete={s.get('has_delete')} | has_form={s.get('has_form')}")
print(f"表单字段 {len(ui.get('form_fields', []))} 个 | 下拉项 {len(ui.get('dropdowns', []))} 个 "
      f"| 搜索框 {len(ui.get('search_inputs', []))} 个 | 行操作 {len(ui.get('row_actions', []))} 个")

# 表单字段
print("\n【创建云主机表单字段】(17 个，含类型/kb_category/必填)")
for f in ui.get("form_fields", []):
    req = "必填" if f.get("required") else "可选"
    print(f"  - {f.get('label')} [{f.get('type')}/{f.get('kb_category')}] {req}")

# validated_operations
vo = ui.get("validated_operations", {})
print(f"\n【操作验证结果】({len(vo)} 个)")
ok = [k for k, v in vo.items() if v.get("success")]
fail = [k for k, v in vo.items() if not v.get("success")]
print(f"  ✅ 成功 ({len(ok)}): {ok}")
print(f"  ❌ 失败 ({len(fail)}): {fail}")
for k, v in vo.items():
    if not v.get("success") and v.get("error_type"):
        print(f"     - {k}: {v['error_type']}")

# playbook operations
ops = pb.get("operations", {})
print(f"\n【Playbook 操作清单】({len(ops)} 个，含 1 个内部操作)")
for name, op in ops.items():
    role = op.get("action_type", op.get("role", "?"))
    ds = op.get("detection_status", "?")
    btn = op.get("button", {})
    btn_text = btn.get("text", "") if isinstance(btn, dict) else ""
    n_steps = len(op.get("steps", []))
    marker = "✅" if "success" in str(ds) else ("❌" if "failed" in str(ds) else "⚠️")
    print(f"  {marker} [{role:12s}] {name} (按钮:{btn_text}) — {ds}, {n_steps} 步")
