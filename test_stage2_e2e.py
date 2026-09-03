"""
test_stage2_e2e.py — Stage 2 API 捕获端到端测试

验证用户管理模块的 API 捕获效果，检查输出文件是否为 Stage 3 做好准备。
"""
import asyncio
import sys
import io
import json
from pathlib import Path

# Windows GBK 编码兼容
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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
    target_url = base_url + "/estack/web/estack/user-center/user-manage/user"

    print("=" * 70)
    print("Stage 2 端到端测试：用户管理模块")
    print("=" * 70)

    from playwright.async_api import async_playwright
    from module_discovery.run import _login_with_playwright
    from module_discovery.capture_apis import capture_all

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # [1/5] 登录
        print("\n[1/5] 登录...")
        ok = await _login_with_playwright(page, context, login_url, username, password,
                                          project_profile=profile)
        if not ok:
            print("登录失败!")
            await browser.close()
            return
        print("[2/5] 登录成功")

        # [3/5] 导航到目标页面
        print(f"\n[3/5] 导航到目标页面: {target_url}")
        await page.goto(target_url, wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # [4/5] 加载 Stage 1 结果
        print("\n[4/5] 加载 Stage 1 探测结果...")
        ui_result_path = ROOT / "projects" / "ecm-compute" / "kb" / "module_discovered" / "用户管理_ui.json"
        if not ui_result_path.exists():
            print(f"❌ Stage 1 结果不存在: {ui_result_path}")
            print("   请先运行 Stage 1")
            return

        with open(ui_result_path, 'r', encoding='utf-8') as f:
            ui_result = json.load(f)

        validated_ops = ui_result.get("validated_operations", {})
        print(f"   Stage 1 已验证操作: {list(validated_ops.keys())}")

        # [5/5] 运行 Stage 2 API 捕获
        print("\n[5/5] 运行 Stage 2 API 捕获...")
        capture_result = await capture_all(
            page=page,
            ui_result=ui_result,
            base_url=base_url,
            target_url=target_url,
            project_dir=ROOT / "projects" / "ecm-compute",
            module_name="用户管理",
        )

        # 保存结果
        output_path = ROOT / "projects" / "ecm-compute" / "kb" / "module_discovered" / "用户管理_capture.json"
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(capture_result, f, ensure_ascii=False, indent=2)

        print(f"\n✓ 捕获结果已保存: {output_path}")

        # 验证结果
        print("\n" + "=" * 70)
        print("验证捕获结果...")
        print("=" * 70)

        apis = capture_result.get("apis", {})
        print(f"\n✓ 捕获到 {len(apis)} 个 API 端点:")
        for crud, api_list in apis.items():
            if api_list:
                print(f"  {crud}: {len(api_list)} 个")
                for api in api_list[:3]:  # 最多显示3个
                    url = api.get("url", "")
                    method = api.get("method", "")
                    print(f"    - {method} {url[:60]}...")

        # 检查 Stage 3 需要的字段
        print("\n✓ Stage 3 准备检查:")
        required_fields = ["apis", "form_fields", "selectors"]
        missing = [f for f in required_fields if f not in capture_result]
        if missing:
            print(f"  ❌ 缺少字段: {missing}")
        else:
            print(f"  ✓ 所有必需字段存在")

        # 检查 form_fields
        form_fields = capture_result.get("form_fields", [])
        print(f"\n✓ 表单字段: {len(form_fields)} 个")
        for field in form_fields[:5]:
            label = field.get("label", "")
            role = field.get("body_field_role", "unknown")
            print(f"  - {label} (role: {role})")

        # 检查 selectors
        selectors = capture_result.get("selectors", {})
        print(f"\n✓ 选择器: {len(selectors)} 个操作")
        for op, sel in selectors.items():
            print(f"  - {op}: {sel}")

        input("\n按 Enter 关闭浏览器...")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
