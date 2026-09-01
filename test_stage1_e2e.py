"""
test_stage1_e2e.py — Stage 1 端到端测试脚本

验证用户管理模块的探测效果，特别关注：
1. 手机号字段是否被正确识别为复合组件
2. 表单填充时是否能正确定位到手机号输入框
3. Vision API 增强是否生效
"""
import asyncio
import sys
import io
from pathlib import Path

# Windows GBK 编码兼容：日志中的 emoji/中文不崩溃
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from module_discovery.run import run_stage1
from playwright.async_api import async_playwright
import yaml


async def main():
    # 读取配置
    profile_path = ROOT / "projects" / "ecm-compute" / "profile.yaml"
    with open(profile_path, 'r', encoding='utf-8') as f:
        profile = yaml.safe_load(f)

    base_url = profile['base_url']
    login_url = profile['login_url']
    username = profile['credentials']['username']
    password = profile['credentials']['password']
    target_url = "https://10.151.61.248/estack/web/estack/user-center/user-manage/user"

    print("=" * 70)
    print("Stage 1 端到端测试：用户管理模块")
    print("=" * 70)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # 登录（复用 run.py 的登录流程）
        print("\n[1/4] 登录...")
        from module_discovery.run import _login_with_playwright
        ok = await _login_with_playwright(page, context, login_url, username, password,
                                          project_profile=profile)
        if not ok:
            print("登录失败!")
            await browser.close()
            return
        print("[2/4] 登录成功")

        # 运行 Stage 1
        print("[3/4] 运行 Stage 1 探测...")
        project_dir = ROOT / "projects" / "ecm-compute"
        ui_result = await run_stage1(
            page,
            project_dir,
            "用户管理",
            target_url
        )

        # 验证结果
        print("\n[4/4] 验证探测结果...")
        if ui_result:
            form_fields = ui_result.get("form_fields", [])
            print(f"\n✓ 探测到 {len(form_fields)} 个表单字段")

            # 检查手机号字段
            phone_fields = [f for f in form_fields if "手机" in f.get("label", "")]
            print(f"\n✓ 手机号相关字段: {len(phone_fields)} 个")
            for field in phone_fields:
                print(f"  - {field.get('label')} (type={field.get('type')}, kb_category={field.get('kb_category')})")

            # 检查是否识别为复合组件
            composite_phone = [f for f in phone_fields if "(下拉)" in f.get("label", "")]
            if composite_phone:
                print(f"\n✓✓ 手机号字段被识别为复合组件（包含国家编码下拉框）")
            else:
                print(f"\n✗✗ 手机号字段未被识别为复合组件")

            # 检查业务闭环验证结果
            validated_ops = ui_result.get("validated_operations", {})
            print(f"\n✓ 业务闭环验证: {len(validated_ops)} 个操作成功")
            for op_name, op_result in validated_ops.items():
                status = "✓" if op_result.get("success") else "✗"
                print(f"  {status} {op_name}")

            # 保存结果
            import json
            output_path = ROOT / "test_stage1_result.json"
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(ui_result, f, ensure_ascii=False, indent=2)
            print(f"\n✓ 结果已保存到: {output_path}")
        else:
            print("\n✗✗ Stage 1 返回空结果")

        input("\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
