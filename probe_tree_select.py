"""探测迁移弹窗中 tree-select 的真实 DOM 结构"""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def probe():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=['--ignore-certificate-errors', '--no-sandbox',
              '--disable-blink-features=AutomationControlled']
    )
    ctx = await browser.new_context(
        viewport={'width': 1600, 'height': 1000},
        locale='zh-CN',
        ignore_https_errors=True
    )
    page = await ctx.new_page()

    # Cookie 鉴权
    cookie_path = Path('projects/ecm-compute/output/config/cookies.json')
    cookies = json.loads(cookie_path.read_text(encoding='utf-8'))
    await ctx.add_cookies(cookies)

    await page.goto('https://10.151.61.248/estack/web/estack/login',
                    wait_until='networkidle')
    await page.evaluate("""() => {
        localStorage.setItem('estackToken',
            document.cookie.match(/accessToken=([^;]+)/)?.[1] || '');
    }""")

    await page.goto(
        'https://10.151.61.248/estack/web/estack/user-center/user-manage/user',
        wait_until='networkidle'
    )
    await page.wait_for_timeout(3000)

    # 选中第一行
    await page.evaluate("""() => {
        const cb = document.querySelector('.el-table__body-wrapper .el-checkbox__input');
        if (cb) cb.click();
    }""")
    await page.wait_for_timeout(1000)

    # 点击"批量迁移"（JS 强制点击，绕过 disabled）
    await page.evaluate("""() => {
        const btns = document.querySelectorAll('button');
        for (const btn of btns) {
            if (btn.textContent.includes('批量迁移')) {
                btn.disabled = false;
                btn.click();
                break;
            }
        }
    }""")
    await page.wait_for_timeout(2000)

    # ---- 探测弹窗中 el-select 的结构 ----
    result = await page.evaluate("""() => {
        const result = { dialog: null, selects: [], dropdowns: [] };

        const dialog = document.querySelector(
            '.el-dialog__wrapper:not([style*="display: none"])');
        if (!dialog || dialog.offsetWidth <= 0) return result;

        result.dialog = {
            title: dialog.querySelector('.el-dialog__title')?.textContent || '',
            width: dialog.offsetWidth,
            height: dialog.offsetHeight
        };

        // 探测所有 el-select
        const selects = dialog.querySelectorAll('.el-select');
        for (const sel of selects) {
            const formItem = sel.closest('.el-form-item');
            const labelEl = formItem
                ? formItem.querySelector('.el-form-item__label') : null;
            const label = labelEl ? labelEl.textContent.trim() : '';

            result.selects.push({
                label,
                classes: Array.from(sel.classList),
                outerHTML: sel.outerHTML.substring(0, 500)
            });
        }

        // 探测所有可见下拉面板
        const allDDs = document.querySelectorAll(
            '.el-select-dropdown, [class*="dropdown"]');
        for (const dd of allDDs) {
            if (dd.offsetWidth <= 0 || dd.offsetHeight <= 0) continue;

            const info = {
                classes: Array.from(dd.classList),
                width: dd.offsetWidth,
                height: dd.offsetHeight,
                childTags: Array.from(dd.children).map(c => c.tagName),
                hasElTree: dd.querySelector('.el-tree') !== null,
                hasElTreeNode: dd.querySelector('.el-tree-node') !== null,
                treeStructure: null
            };

            const tree = dd.querySelector('.el-tree');
            if (tree) {
                const nodes = tree.querySelectorAll('.el-tree-node');
                const contents = tree.querySelectorAll('.el-tree-node__content');
                const labels = tree.querySelectorAll('.el-tree-node__label');

                info.treeStructure = {
                    nodeCount: nodes.length,
                    contentCount: contents.length,
                    labelCount: labels.length,
                    firstNodeClasses: nodes[0]
                        ? Array.from(nodes[0].classList) : [],
                    firstNodeHTML: nodes[0]
                        ? nodes[0].innerHTML.substring(0, 500) : '',
                    allLabelTexts: Array.from(labels)
                        .slice(0, 20).map(l => l.textContent.trim()),
                };
            }

            const stdItems = dd.querySelectorAll('.el-select-dropdown__item');
            info.standardItemCount = stdItems.length;

            result.dropdowns.push(info);
        }

        return result;
    }""")

    print('=== 迁移弹窗结构 ===')
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # ---- 点击每个 select 展开 ----
    selects_in_dialog = page.locator(
        '.el-dialog__wrapper:not([style*="display: none"]) .el-select')
    sel_count = await selects_in_dialog.count()
    print(f'\n弹窗中有 {sel_count} 个 el-select')

    for i in range(sel_count):
        await selects_in_dialog.nth(i).click()
        await page.wait_for_timeout(1000)

    await page.wait_for_timeout(1500)

    # ---- 探测展开后的面板 ----
    panel_result = await page.evaluate("""() => {
        const panels = document.querySelectorAll(
            '.el-select-dropdown, [class*="dropdown"]');
        const result = [];
        for (const panel of panels) {
            if (panel.offsetWidth <= 0 && panel.offsetHeight <= 0) continue;

            const info = {
                classes: Array.from(panel.classList),
                size: { w: panel.offsetWidth, h: panel.offsetHeight },
                hasElTree: panel.querySelector('.el-tree') !== null,
                fullInnerHTML: panel.innerHTML.substring(0, 2000),
            };

            const tree = panel.querySelector('.el-tree');
            if (tree) {
                const nodes = tree.querySelectorAll('.el-tree-node');
                const visNodes = Array.from(nodes)
                    .filter(n => n.offsetWidth > 0);
                const contents = tree.querySelectorAll('.el-tree-node__content');
                const visContents = Array.from(contents)
                    .filter(c => c.offsetWidth > 0);
                const labels = tree.querySelectorAll('.el-tree-node__label');
                const visLabels = Array.from(labels)
                    .filter(l => l.offsetWidth > 0);

                info.tree = {
                    totalNodes: nodes.length,
                    visibleNodes: visNodes.length,
                    totalContents: contents.length,
                    visibleContents: visContents.length,
                    totalLabels: labels.length,
                    visibleLabels: visLabels.length,
                    labelTexts: visLabels.slice(0, 15)
                        .map(l => l.textContent.trim()),
                    firstNodeHTML: visNodes[0]
                        ? visNodes[0].outerHTML.substring(0, 800) : '',
                    firstContentHTML: visContents[0]
                        ? visContents[0].outerHTML.substring(0, 500) : '',
                };
            }

            const items = panel.querySelectorAll('.el-select-dropdown__item');
            const visItems = Array.from(items)
                .filter(i => i.offsetWidth > 0);
            info.standardItems = {
                total: items.length,
                visible: visItems.length,
                texts: visItems.slice(0, 10).map(i => i.textContent.trim())
            };

            result.push(info);
        }
        return result;
    }""")

    print('\n=== 展开后的下拉面板结构 ===')
    print(json.dumps(panel_result, indent=2, ensure_ascii=False))

    await browser.close()
    await pw.stop()


asyncio.run(probe())
