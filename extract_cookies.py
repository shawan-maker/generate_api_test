#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交互式浏览器登录 - 手动完成滑块验证并提取 cookie
"""

import asyncio
import json
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,  # 非无头模式，方便手动操作
            slow_mo=100
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True
        )
        page = await context.new_page()

        # 导航到登录页
        print("🌐 导航到登录页...")
        await page.goto("https://10.151.61.248/estack/web/estack/login", wait_until="networkidle")
        await page.wait_for_timeout(2000)

        # 填充用户名
        print("📝 填充用户名...")
        await page.fill("input[placeholder*='用户名'], input[placeholder*='账号']", "estack-yy")

        # 填充密码
        print("🔒 填充密码...")
        await page.fill("input[type='password']", "R@9eDuck$!mpleM00n")

        print("\n" + "="*60)
        print("请手动完成滑块验证，然后点击登录按钮")
        print("登录成功后按 Enter 键继续...")
        print("="*60 + "\n")

        # 等待用户完成登录
        input(">>> 按 Enter 继续...")

        # 等待页面跳转完成
        await page.wait_for_timeout(2000)

        # 提取 cookies
        print("\n🍪 提取 cookies...")
        cookies = await context.cookies()

        # 提取 localStorage token
        print("🔑 提取 localStorage token...")
        token = await page.evaluate("() => localStorage.getItem('estackToken')")

        # 输出结果
        print("\n" + "="*60)
        print("Cookies:")
        print("="*60)
        print(json.dumps(cookies, indent=2, ensure_ascii=False))

        print("\n" + "="*60)
        print("Token (estackToken):")
        print("="*60)
        print(token if token else "(空)")

        # 保存到文件
        with open("cookies_extracted.json", "w", encoding="utf-8") as f:
            json.dump({
                "cookies": cookies,
                "token": token
            }, f, indent=2, ensure_ascii=False)

        print(f"\n✅ 已保存到 cookies_extracted.json")

        input("\n按 Enter 关闭浏览器...")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
