#!/usr/bin/env python3
"""验证 playbook builder 修改效果"""

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.discovery.playbook_builder.builder import build_playbook

def main():
    # 读取用户管理_ui.json
    ui_file = project_root / "workspace" / "estack" / "kb" / "module_discovered" / "用户管理_ui.json"

    print(f"读取 {ui_file}...")
    with open(ui_file, 'r', encoding='utf-8') as f:
        ui_result = json.load(f)

    # 重新生成 playbook
    print("调用 build_playbook 重新生成 playbook...")
    playbook = build_playbook(ui_result)

    # 保存新的 playbook
    output_file = project_root / "workspace" / "estack" / "kb" / "module_discovered" / "用户管理_playbook_new.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(playbook, f, ensure_ascii=False, indent=2)

    print(f"✓ 已保存到 {output_file}\n")

    # 验证修改效果
    print("=" * 80)
    print("验证 playbook 修改效果")
    print("=" * 80)

    issues_found = []

    # 问题1：检查创建用户的系统角色字段
    print("\n1. 创建用户 - 系统角色字段")
    create_op = playbook.get("operations", {}).get("创建用户", {})
    create_found = False
    for step in create_op.get("steps", []):
        if step.get("action") == "fill_form":
            for field in step.get("fields", []):
                if field.get("label") == "系统角色":
                    create_found = True
                    option_text = field.get("option_text")
                    interaction = field.get("interaction")
                    select_sub_type = field.get("select_sub_type")

                    print(f"  option_text: {repr(option_text)}")
                    print(f"  interaction: {interaction}")
                    print(f"  select_sub_type: {select_sub_type}")

                    # 验证修改
                    if option_text and option_text != "":
                        issues_found.append("创建用户-系统角色: option_text 未清空")
                    if not interaction:
                        issues_found.append("创建用户-系统角色: 缺少 interaction 字段")
                    elif not interaction.get("click_strategy"):
                        issues_found.append("创建用户-系统角色: interaction 缺少 click_strategy")
                    break

    if not create_found:
        issues_found.append("创建用户-系统角色: 字段未找到")

    # 问题2：检查迁移的待迁移部门字段
    print("\n2. 迁移 - 待迁移部门字段")
    migration_op = playbook.get("operations", {}).get("迁移", {})
    migration_found = False
    for step in migration_op.get("steps", []):
        if step.get("action") == "fill_form":
            for field in step.get("fields", []):
                if field.get("label") == "待迁移部门":
                    migration_found = True
                    option_text = field.get("option_text")
                    interaction = field.get("interaction")
                    select_sub_type = field.get("select_sub_type")

                    print(f"  option_text: {repr(option_text)}")
                    print(f"  interaction: {interaction}")
                    print(f"  select_sub_type: {select_sub_type}")

                    # 验证修改
                    if option_text and option_text != "":
                        issues_found.append("迁移-待迁移部门: option_text 未清空")
                    if not interaction:
                        issues_found.append("迁移-待迁移部门: 缺少 interaction 字段")
                    elif interaction.get("click_strategy") != "handleNodeClick":
                        issues_found.append(f"迁移-待迁移部门: click_strategy 应为 handleNodeClick，实际为 {interaction.get('click_strategy')}")
                    if select_sub_type != "select-tree":
                        issues_found.append(f"迁移-待迁移部门: select_sub_type 应为 select-tree，实际为 {select_sub_type}")
                    break

    if not migration_found:
        issues_found.append("迁移-待迁移部门: 字段未找到")

    # 问题3：检查重置密码的通知方式字段
    print("\n3. 重置密码 - 通知方式字段")
    reset_op = playbook.get("operations", {}).get("重置密码", {})
    reset_found = False
    for step in reset_op.get("steps", []):
        if step.get("action") == "fill_form":
            for field in step.get("fields", []):
                if field.get("label") == "通知方式":
                    reset_found = True
                    option_text = field.get("option_text")
                    interaction = field.get("interaction")
                    select_sub_type = field.get("select_sub_type")

                    print(f"  option_text: {repr(option_text)}")
                    print(f"  interaction: {interaction}")
                    print(f"  select_sub_type: {select_sub_type}")

                    # 验证修改
                    if option_text and option_text != "":
                        issues_found.append("重置密码-通知方式: option_text 未清空")
                    if not interaction:
                        issues_found.append("重置密码-通知方式: 缺少 interaction 字段")
                    elif not interaction.get("click_strategy"):
                        issues_found.append("重置密码-通知方式: interaction 缺少 click_strategy")
                    break

    if not reset_found:
        issues_found.append("重置密码-通知方式: 字段未找到")

    # 问题4：检查授权操作是否存在
    print("\n4. 授权操作")
    auth_op = playbook.get("operations", {}).get("授权")
    if auth_op:
        print(f"  ✓ 授权操作存在")
        print(f"  role: {auth_op.get('role')}")
        print(f"  steps: {len(auth_op.get('steps', []))}")
    else:
        issues_found.append("授权操作: 操作不存在")
        print(f"  ✗ 授权操作不存在")

    # 输出验证结果
    print("\n" + "=" * 80)
    if issues_found:
        print(f"⚠️  发现 {len(issues_found)} 个问题：")
        for i, issue in enumerate(issues_found, 1):
            print(f"  {i}. {issue}")
        print("\n建议：")
        print("  - 对于 option_text 未清空的问题：检查 builder.py 的修改是否生效")
        print("  - 对于缺少 interaction 字段的问题：检查字段类型是否正确识别为选择框")
        print("  - 对于授权操作不存在的问题：需要检查 Stage 1 是否正确捕获了授权操作")
        return 1
    else:
        print("✓ 所有修改验证通过！")
        print("\n修改效果：")
        print("  ✓ 选择框字段已清空 option_text")
        print("  ✓ 选择框字段已添加 interaction 元数据")
        print("  ✓ 树选择框字段已标记为 handleNodeClick")
        print("  ✓ 授权操作已正确生成")
        return 0

if __name__ == "__main__":
    sys.exit(main())
