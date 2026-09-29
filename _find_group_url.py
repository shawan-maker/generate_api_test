"""Quick script to find 用户组 URL in the menu system."""
import asyncio
from pathlib import Path
from core.discovery.io_helpers import load_profile

async def find_url():
    from playwright.async_api import async_playwright
    from core.discovery.auth_runner import login_with_playwright

    profile = load_profile(Path('projects/ecm-compute'))
    login_url = profile['login_url']
    username = profile.get('credentials', {}).get('username', '')
    password = profile.get('credentials', {}).get('password', '')

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

        # Go to portal
        await page.goto(profile['base_url'] + '/estack/web/estack/portal',
                        wait_until='networkidle', timeout=30000)
        await page.wait_for_timeout(3000)

        # Expand all submenus and collect all links
        all_links = await page.evaluate('''() => {
            const results = [];
            const seen = new Set();
            // Click all submenu titles to expand them
            document.querySelectorAll('.el-submenu__title').forEach(el => el.click());
            return new Promise(resolve => {
                setTimeout(() => {
                    document.querySelectorAll('a[href], .el-menu-item, .router-link-active').forEach(el => {
                        const text = (el.textContent || '').trim();
                        const href = el.getAttribute('href') || '';
                        if (text && text.length > 0 && text.length < 40 && !seen.has(text + '|' + href)) {
                            seen.add(text + '|' + href);
                            results.push({text, href: href.substring(0, 150)});
                        }
                    });
                    resolve(results);
                }, 2000);
            });
        }''')
        print(f'All links ({len(all_links)}):')
        for l in all_links:
            marker = ' <<<' if ('用户' in l['text'] or '角色' in l['text'] or 'group' in l['href'].lower()) else ''
            print(f'  {l["text"]:30s}  ->  {l["href"]}{marker}')

        # Try direct navigation to common user-group URLs
        test_urls = [
            '/estack/web/estack/user-center/user-manage/group',
            '/estack/web/estack/user-center/group',
            '/estack/web/estack/user-center/usergroup',
            '/estack/web/estack/user-center/user-group',
            '/estack/web/estack/user-center/usermanage/group',
        ]
        print('\n--- URL probe ---')
        for url_path in test_urls:
            full = profile['base_url'] + url_path
            resp = await page.goto(full, wait_until='domcontentloaded', timeout=15000)
            final_url = page.url
            status = resp.status if resp else '?'
            is_404 = '404' in final_url
            print(f'  {url_path:60s}  status={status}  final={final_url[-60:]}  404={is_404}')

        await browser.close()

asyncio.run(find_url())
