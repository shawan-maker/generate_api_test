#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动登录辅助脚本 - 打开浏览器让你手动登录，登录成功后自动保存 cookie
"""

import sys
import io
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

# 设置 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='ignore')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='ignore')

WORKSPACE_DIR = Path("workspace/estack/output/config")
COOKIES_FILE = WORKSPACE_DIR / "cookies.json"

async def main():
    print("=" * 60)
    print("手动登录辅助工具")
    print("=" * 60)
    print("\n1. 浏览器将打开登录页面")
    print("2. 请手动完成登录（输入用户名、密码、验证码）")
    print("3. 登录成功后，脚本会自动检测并保存 cookie")
    print("4. 保存完成后浏览器会自动关闭\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()

        # 导航到登录页
        login_url = "https://10.151.61.248/estack/web/estack/login"
        print(f"正在打开: {login_url}")
        await page.goto(login_url, wait_until="networkidle", timeout=60000)

        print("\n✓ 登录页面已打开，请手动完成登录...")
        print("  （脚本将每 2 秒检查一次登录状态）\n")

        # 轮询检测登录成功
        max_wait = 300  # 最多等待 5 分钟
        waited = 0

        while waited < max_wait:
            await asyncio.sleep(2)
            waited += 2

            current_url = page.url

            # 检测是否已离开登录页
            if "/login" not in current_url:
                # 检查是否有登录表单（双重验证）
                has_login_form = await page.evaluate("""() => {
                    return !!(document.querySelector('input[placeholder="用户名"]')
                        || document.querySelector('.zong-login-page')
                        || document.querySelector('.login-page')
                        || document.querySelector('input[placeholder="登录密码"]'));
                }""")

                if not has_login_form:
                    print(f"\n✓ 检测到已登录！当前 URL: {current_url}")
                    break

            if waited % 10 == 0:
                print(f"  等待中... ({waited}s)")

        if waited >= max_wait:
            print("\n✗ 等待超时，未检测到登录成功")
            await browser.close()
            return False

        # 等待页面稳定
        print("等待页面加载完成...")
        await asyncio.sleep(3)

        # 保存 cookie
        print(f"\n保存 cookie 到: {COOKIES_FILE}")
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

        cookies = await context.cookies()
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)

        print(f"✓ 已保存 {len(cookies)} 个 cookie")

        # 保存 localStorage（如果需要）
        try:
            storage = await page.evaluate("() => JSON.parse(JSON.stringify(localStorage))")
            if storage:
                storage_file = WORKSPACE_DIR / "localStorage.json"
                with open(storage_file, "w", encoding="utf-8") as f:
                    json.dump(storage, f, indent=2, ensure_ascii=False)
                print(f"✓ 已保存 localStorage 到: {storage_file}")
        except Exception as e:
            print(f"⚠ localStorage 保存失败: {e}")

        print("\n" + "=" * 60)
        print("✓ Cookie 保存成功！可以关闭浏览器了")
        print("=" * 60)

        # 等待用户确认
        input("\n按 Enter 键关闭浏览器...")

        await browser.close()
        return True

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
