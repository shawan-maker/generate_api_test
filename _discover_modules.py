"""Discover all modules under user-center from the root URL."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import asyncio
from pathlib import Path
from core.discovery.io_helpers import load_profile


async def discover_modules():
    from playwright.async_api import async_playwright
    from core.discovery.auth_runner import login_with_playwright

    profile = load_profile(Path('projects/ecm-compute'))
    login_url = profile['login_url']
    username = profile.get('credentials', {}).get('username', '')
    password = profile.get('credentials', {}).get('password', '')
    base_url = profile['base_url']

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=['--ignore-certificate-errors', '--disable-web-security',
                  '--no-sandbox', '--disable-blink-features=AutomationControlled']
        )
        context = await browser.new_context(
            viewport={'width': 1600, 'height': 1000}, locale='zh-CN',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        page = await context.new_page()

        ok = await login_with_playwright(page, context, login_url, username, password,
                                         project_profile=profile)
        if not ok:
            print('Login failed')
            await browser.close()
            return

        root = base_url + '/estack/web/estack/user-center/account-manage/basic-message'
        await page.goto(root, wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)
        print('Current URL:', page.url)

        # Expand all submenus iteratively
        for _ in range(5):
            await page.evaluate('''() => {
                document.querySelectorAll('.el-submenu__title').forEach(el => {
                    const parent = el.closest('.el-submenu');
                    if (parent && !parent.classList.contains('is-opened')) el.click();
                });
            }''')
            await page.wait_for_timeout(1000)

        # Collect all menu items with hierarchy
        links = await page.evaluate('''() => {
            const results = [];
            const seen = new Set();
            document.querySelectorAll('.el-menu-item, a.el-menu-item').forEach(el => {
                const text = (el.textContent || '').trim();
                const href = el.getAttribute('href') || '';
                const key = text + '|' + href;
                if (text && text.length > 0 && text.length < 40 && !seen.has(key)) {
                    seen.add(key);
                    // Find parent submenu title
                    let parentTitle = '';
                    const submenu = el.closest('.el-submenu');
                    if (submenu) {
                        const titleEl = submenu.querySelector('.el-submenu__title');
                        if (titleEl) parentTitle = (titleEl.textContent || '').trim();
                    }
                    results.push({text, href: href.substring(0, 200), parent: parentTitle.substring(0, 40)});
                }
            });
            return results;
        }''')
        print(f'\n=== All leaf menu items ({len(links)}) ===')
        current_parent = ''
        for l in links:
            if l['parent'] and l['parent'] != current_parent:
                current_parent = l['parent']
                print(f'\n  [{current_parent}]')
            print(f'    {l["text"]:30s}  ->  {l["href"]}')

        # Also collect submenu titles (groups)
        groups = await page.evaluate('''() => {
            const results = [];
            document.querySelectorAll('.el-submenu__title').forEach(el => {
                const text = (el.textContent || '').trim();
                if (text) results.push(text);
            });
            return results;
        }''')
        print(f'\n=== Submenu groups ({len(groups)}) ===')
        for g in groups:
            print(f'  {g}')

        # Navigate to user-center and expand
        print('\n=== Probing user-center URLs ===')
        probe_urls = [
            '/estack/web/estack/user-center/user-manage/user',
            '/estack/web/estack/user-center/user-manage/role',
            '/estack/web/estack/user-center/user-manage/group',
            '/estack/web/estack/user-center/usergroup',
            '/estack/web/estack/user-center/user-group',
            '/estack/web/estack/user-center/group',
            '/estack/web/estack/user-center/usermanage/group',
        ]
        for url_path in probe_urls:
            full = base_url + url_path
            try:
                resp = await page.goto(full, wait_until='domcontentloaded', timeout=10000)
                final_url = page.url
                status = resp.status if resp else '?'
                is_404 = '404' in final_url or 'warning' in final_url
                print(f'  {url_path:60s}  status={status}  404={is_404}')
            except Exception as e:
                print(f'  {url_path:60s}  ERROR: {e}')

        await browser.close()

asyncio.run(discover_modules())
