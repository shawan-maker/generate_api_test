"""Stage 2 无写 API 根因分析 — 基于日志和数据的确定性结论"""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('workspace/estack-ceshiyu/kb/module_discovered/用户管理_ui.json', 'r', encoding='utf-8') as f:
    ui = json.load(f)

create_op = ui.get('validated_operations', {}).get('创建用户', {})
print("=" * 70)
print("根因分析：Stage 2 无写 API")
print("=" * 70)

# 1. 创建用户的状态
print("\n1. 创建用户操作状态：")
print(f"   success: {create_op.get('success')}")
print(f"   error_type: {create_op.get('error_type')}")
print(f"   error_text: {create_op.get('error_text', '')[:100]}")

# 2. fill_data 分析
fill_data = create_op.get('fill_data', {})
empty_fields = {k: v for k, v in fill_data.items() if not v}
filled_fields = {k: v for k, v in fill_data.items() if v}
print(f"\n2. 创建用户 fill_data 分析：")
print(f"   总字段数: {len(fill_data)}")
print(f"   非空字段 ({len(filled_fields)}): {list(filled_fields.keys())}")
print(f"   空值字段 ({len(empty_fields)}): {list(empty_fields.keys())}")

# 3. form_fields 中的真实姓名
form_fields = create_op.get('form_fields', [])
real_name_field = None
for f in form_fields:
    if f.get('label') == '真实姓名':
        real_name_field = f
        break

print(f"\n3. 真实姓名 字段定义：")
if real_name_field:
    print(f"   selector: {real_name_field.get('selector', 'N/A')}")
    print(f"   type: {real_name_field.get('type', 'N/A')}")
    print(f"   kb_category: {real_name_field.get('kb_category', 'N/A')}")
    print(f"   placeholder: {real_name_field.get('placeholder', 'N/A')}")
else:
    print("   未在 form_fields 中找到")

# 4. 连锁效应分析
print(f"\n4. 连锁效应分析：")
vo = ui.get('validated_operations', {})
downstream_ops = ['编辑', '删除', '冻结', '启用', '授权', '设置到期时间', '重置密码']
print(f"   创建用户失败 → 测试用户未创建")
print(f"   以下操作依赖 find_row 定位测试用户：")
for op in downstream_ops:
    op_data = vo.get(op, {})
    steps = op_data.get('steps', [])
    has_find_row = any(s.get('action') == 'find_row' for s in steps) if steps else False
    print(f"   - {op}: success={op_data.get('success')}")

# 5. Stage 2 结果
with open('workspace/estack-ceshiyu/kb/module_discovered/用户管理_capture.json', 'r', encoding='utf-8') as f:
    cap = json.load(f)
core_api_map = cap.get('core_api_map', {})

print(f"\n5. Stage 2 各操作 API 捕获情况：")
all_ops = list(vo.keys())
for op in all_ops:
    apis = core_api_map.get(op, [])
    write_apis = [a for a in apis if a.get('method', '').upper() in ('POST', 'PUT', 'PATCH', 'DELETE')]
    api_captured = apis[0].get('api_captured', True) if apis else 'N/A'
    if not apis:
        status = "无 API 数据"
    elif api_captured == False:
        status = "前端错误，API 未捕获"
    elif write_apis:
        status = f"{len(apis)} 个 API ({len(write_apis)} 写 + {len(apis)-len(write_apis)} 读)"
    else:
        status = f"{len(apis)} 个 API (仅 GET)"
    print(f"   {op:<16}: {status}")

print("\n" + "=" * 70)
print("结论：")
print("=" * 70)
print("根因：创建用户操作在 Stage 1 中失败（真实姓名 字段填充失败）")
print("  → 测试用户未创建")
print("  → 后续 8 个操作（编辑/删除/冻结/启用/授权/设置到期时间/重置密码/迁移）")
print("    的 find_row 步骤找不到测试数据，操作被跳过")
print("  → 写操作 API 未被触发，只捕获到 GET 列表刷新")
print()
print("真实姓名 字段填充失败原因：")
print("  form_filler 的 CSS 选择器超时（3 秒），未能定位到该字段的 input 元素")
print("  选择器: .el-form-item:has(.el-form-item__label:has-text(\"真实姓名\")) input")
print("  这是一个 form_filler 的选择器生成/匹配问题，需要检查实际 DOM 结构")
