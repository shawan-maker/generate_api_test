import asyncio, json, sys
sys.stdout.reconfigure(encoding='utf-8')
from playwright.async_api import async_playwright
from pathlib import Path

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(ignore_https_errors=True)
        cookie_file = Path('projects/ecm-compute/output/config/cookies.json')
        if cookie_file.exists():
            cookies = json.loads(cookie_file.read_text(encoding='utf-8'))
            await context.add_cookies(cookies)
        page = await context.new_page()
        await page.goto('https://10.151.61.248/estack/web/estack/user-center/user-manage/user',
                        wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)

        # Click create button (page-nav mode)
        await page.click("button:has-text('创建用户')", timeout=5000)
        await page.wait_for_timeout(2000)

        # Check new selector
        sel = '.el-form-item:nth-child(2) .el-select'
        count = await page.locator(sel).count()
        print(f'Selector "{sel}" matches {count} elements')

        if count > 0:
            el = page.locator(sel).first
            await el.click(timeout=3000)
            print('Clicked el-select')
            await page.wait_for_timeout(2000)

            # Check ALL dropdown elements
            dd_info = await page.evaluate('''() => {
                const dds = document.querySelectorAll(".el-select-dropdown");
                return Array.from(dds).map((dd, i) => ({
                    idx: i,
                    display: dd.style.display || "(not set)",
                    visibility: dd.style.visibility || "(not set)",
                    visible: dd.offsetWidth > 0 && dd.offsetHeight > 0,
                    itemCount: dd.querySelectorAll(".el-select-dropdown__item").length,
                    firstItems: Array.from(dd.querySelectorAll(".el-select-dropdown__item")).slice(0, 5).map(it => ({
                        text: it.textContent.trim(),
                        visible: it.offsetWidth > 0 && it.offsetHeight > 0,
                        classes: it.className
                    }))
                }));
            }''')
            print(f'\nDropdowns found: {len(dd_info)}')
            for dd in dd_info:
                print(f'  [{dd["idx"]}] visible={dd["visible"]} display={dd["display"]} visibility={dd["visibility"]} items={dd["itemCount"]}')
                for it in dd['firstItems']:
                    print(f'    - "{it["text"]}" visible={it["visible"]} classes={it["classes"]}')

        await browser.close()

asyncio.run(test())
