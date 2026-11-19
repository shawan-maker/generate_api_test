"""EIS 列表页状态快速诊断"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "https://10.251.137.30:32438"
LIST_URL = f"{BASE}/page/eis/virtualManage"
COOKIES = Path(r"D:\Mobile\API_AI_test\workspace\eis\output\config\cookies.json")


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True)
        cookies = json.loads(COOKIES.read_text(encoding="utf-8"))
        await ctx.add_cookies(cookies)
        page = await ctx.new_page()
        await page.goto(LIST_URL, wait_until="load", timeout=45000)
        await page.wait_for_timeout(6000)
        print("URL:", page.url)
        html = await page.content()
        print("页面长度:", len(html))
        # 关键元素探测
        r = await page.evaluate("""() => ({
            hasTable: !!document.querySelector('.el-table'),
            hasMenu: document.querySelectorAll('.el-menu-item').length,
            hasLogin: !!document.querySelector('input[type=password]'),
            hasProductCard: document.querySelectorAll('.product-card').length,
            bodySnippet: (document.body.innerText || '').slice(0, 300),
        })""")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        # cookie 名
        cs = ctx.cookies()
        print("cookie keys:", [c["name"] for c in cs][:10])
        await browser.close()


asyncio.run(main())
