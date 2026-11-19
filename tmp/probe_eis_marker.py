"""EIS 云主机列表页 marker 提取 vs 行查找差异探针"""
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
        await page.wait_for_timeout(5000)

        r = await page.evaluate("""() => {
            // 1. marker_utils.extract_marker_from_row 同款逻辑
            const firstBody = document.querySelector('.el-table__body, table tbody');
            const bodyCls = firstBody ? firstBody.className : null;
            const firstBodyTable = firstBody ? firstBody.closest('table, .el-table__body-wrapper') : null;
            let markerCandidate = null;
            if (firstBody) {
                const rows = firstBody.querySelectorAll('tr');
                const cells = rows[0] ? Array.from(rows[0].querySelectorAll('td .cell, td')).map(c => (c.textContent || '').trim()).filter(t => t) : [];
                markerCandidate = { rowTexts: cells.slice(0, 5), allBodiesCount: document.querySelectorAll('.el-table__body').length };
            }
            // 2. 所有 .el-table__body 分布
            const bodies = Array.from(document.querySelectorAll('.el-table__body')).map((b, i) => {
                const rows = b.querySelectorAll('tr');
                const firstRowCells = rows[0] ? Array.from(rows[0].querySelectorAll('td')).map(c => (c.textContent || '').trim().slice(0, 25)).filter(t => t) : [];
                const wrapperCls = b.parentElement ? b.parentElement.className : '';
                return { i, wrapper: wrapperCls.slice(0, 60), rows: rows.length, firstRow: firstRowCells.slice(0, 6) };
            });
            // 3. 文本是否含 csy-wx-pm-os01-eis-node04
            const target = 'csy-wx-pm-os01-eis-node04';
            const where = [];
            document.querySelectorAll('.el-table__body-wrapper').forEach((w, i) => {
                if ((w.textContent || '').includes(target)) where.push(i);
            });
            // 4. main wrapper 首行 inner_text（button_driver 视角）
            const mainWrappers = Array.from(document.querySelectorAll('.el-table__body-wrapper'));
            const mainRowTexts = [];
            if (mainWrappers[0]) {
                mainWrappers[0].querySelectorAll('tbody tr').forEach((tr, i) => {
                    if (i < 3) mainRowTexts.push((tr.innerText || '').replace(/\\n/g, '|').slice(0, 120));
                });
            }
            return { bodyCls, bodies, where, mainRowTexts, markerCandidate, wrapperCount: mainWrappers.length };
        }""")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        await browser.close()


asyncio.run(main())
