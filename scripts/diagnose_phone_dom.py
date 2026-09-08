#!/usr/bin/env python3
"""诊断手机号字段的实际 DOM 结构"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

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

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # 登录
        print("正在登录...")
        await page.goto(login_url)
        await page.wait_for_load_state('networkidle')

        # 填入凭据
        await page.fill('input[placeholder="请输入用户名"]', username)
        await page.fill('input[type="password"]', password)

        # 点击登录
        await page.click('button:has-text("登录")')
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(3000)

        # 导航到用户管理
        print("导航到用户管理...")
        await page.goto(f"{base_url}/#/system/user")
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(3000)

        # 点击创建用户按钮
        print("点击创建用户...")
        create_btn = await page.query_selector('button:has-text("创建用户")')
        if create_btn:
            await create_btn.click()
            await page.wait_for_timeout(2000)

            # 检查手机号字段的 DOM 结构
            print("\n=== 检查手机号字段 DOM 结构 ===")
            dom_info = await page.evaluate('''() => {
                const result = [];

                // 查找所有包含"手机号"文本的 el-form-item
                const formItems = document.querySelectorAll('.el-form-item');
                for (const fi of formItems) {
                    const label = fi.querySelector('.el-form-item__label');
                    if (label && label.textContent.includes('手机号')) {
                        result.push({
                            label: label.textContent.trim(),
                            hasElSelect: !!fi.querySelector('.el-select'),
                            hasElInput: !!fi.querySelector('.el-input'),
                            allInputs: Array.from(fi.querySelectorAll('input')).map(inp => ({
                                type: inp.type,
                                readOnly: inp.readOnly,
                                className: inp.className,
                                placeholder: inp.placeholder,
                                parentClass: inp.parentElement?.className || '',
                                grandParentClass: inp.parentElement?.parentElement?.className || ''
                            })),
                            html: fi.innerHTML.substring(0, 1000)
                        });
                    }
                }

                return result;
            }''')

            for info in dom_info:
                print(f"\n标签: {info['label']}")
                print(f"包含 el-select: {info['hasElSelect']}")
                print(f"包含 el-input: {info['hasElInput']}")
                print(f"Input 数量: {len(info['allInputs'])}")

                for i, inp in enumerate(info['allInputs']):
                    print(f"\n  Input[{i}]:")
                    print(f"    type: {inp['type']}")
                    print(f"    readOnly: {inp['readOnly']}")
                    print(f"    placeholder: {inp['placeholder']}")
                    print(f"    className: {inp['className']}")
                    print(f"    parentClass: {inp['parentClass']}")
                    print(f"    grandParentClass: {inp['grandParentClass']}")

                print(f"\nHTML 片段:\n{info['html'][:500]}")

        input("\n按 Enter 关闭浏览器...")
        await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
