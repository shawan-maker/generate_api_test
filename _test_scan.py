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

        # Scan with form_filler.scan_form_fields
        from module_discovery.form_filler import scan_form_fields
        fields = await scan_form_fields(page)
        print(f'Scanned {len(fields)} fields')
        for f in fields:
            if f.get('kb_category') in ('el-select', 'el-cascader', 'date-picker'):
                sel = f.get('selector', '')
                label = f.get('label', '')
                count = await page.locator(sel).count() if sel else 0
                print(f'  {label}: kb_cat={f["kb_category"]}, selector="{sel}", matches={count}')
        
        # Also test _expand_by_selector for system role
        from module_discovery.form_filler import MultiStepExecutor
        from module_discovery.kb_loader import ProbeKB
        kb = ProbeKB()
        executor = MultiStepExecutor(page, kb, framework='element-ui')
        
        for f in fields:
            if f.get('kb_category') == 'el-select':
                sel = f.get('selector', '')
                label = f.get('label', '')
                print(f'\n--- Testing {label} ---')
                expanded = await executor._expand_by_selector(sel, label)
                print(f'  Expanded: {expanded}')
                if expanded:
                    await page.wait_for_timeout(1000)
                    opt = await executor._discover_first_option()
                    print(f'  Option: {repr(opt)}')

        await browser.close()

asyncio.run(test())
