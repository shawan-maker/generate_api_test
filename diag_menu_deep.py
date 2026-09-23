"""深度诊断：登录后获取完整菜单树 DOM 结构 + 每个菜单项的页面类型"""
import asyncio, sys, json, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

TARGET_URL = "https://10.151.61.248/estack/web/estack/user-center/user-manage/role"
LOGIN_URL = "https://10.151.61.248/estack/web/estack/login"
PROFILE_PATH = Path("D:/Mobile/API_AI_test/projects/ecm-compute/profile.yaml")

async def main():
    from playwright.async_api import async_playwright
    import yaml

    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    creds = profile.get("credentials", {}) or {}
    username = creds.get("username", "")
    password = creds.get("password", "")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--ignore-certificate-errors", "--disable-web-security", "--no-sandbox",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = await context.new_page()

        # 复用主管线登录逻辑
        from core.discovery.auth_runner import login_with_playwright

        # 强制走滑块登录（不用 cookie）
        print("强制滑块登录...")
        await page.goto(LOGIN_URL, wait_until="load", timeout=30000)
        await page.wait_for_timeout(2000)

        from core.discovery.auth_runner import login_with_playwright
        ok = await login_with_playwright(page, context, LOGIN_URL, username, password,
                                         project_profile=profile)
        if not ok:
            print("❌ 登录失败")
            await browser.close()
            return
        print("✅ 滑块登录成功")

        # 保存新 cookie
        cookies = await context.cookies()
        cookie_file = Path("D:/Mobile/API_AI_test/workspace/ecm-compute/output/config/cookies.json")
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")

        # 注入 token
        token_key = profile.get("auth", {}).get("token_key", "accessToken")
        token_cookie = next((c["value"] for c in cookies if c["name"] == token_key), None)
        if token_cookie:
            await context.add_init_script(f"localStorage.setItem('{token_key}', '{token_cookie}');")

        # 导航到目标页
        print(f"导航到: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="load", timeout=45000)
        await page.wait_for_timeout(5000)

        # ====== 0. 底层 DOM 扫描 ======
        print("=" * 80)
        print("0. 底层 DOM 结构扫描")
        print("=" * 80)

        dom_scan = await page.evaluate("""
        () => {
            const selectors = [
                '.el-menu', '.el-submenu', '.el-submenu__title', '.el-menu-item',
                '.el-menu--horizontal', '.el-menu--vertical', '.el-menu--collapse',
                '[class*="menu"]', '[class*="Menu"]',
                '[class*="nav"]', '[class*="Nav"]',
                '[class*="sidebar"]', '[class*="Sidebar"]',
                '[class*="aside"]', '[class*="Aside"]',
                'nav', 'aside',
                '.layout', '[class*="layout"]', '[class*="Layout"]'
            ];
            const counts = {};
            selectors.forEach(sel => {
                try {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0) counts[sel] = els.length;
                } catch(e) {}
            });
            const bodyChildren = Array.from(document.body.children).map(el => ({
                tag: el.tagName,
                id: el.id,
                cls: (el.className || '').toString().substring(0, 200),
                childCount: el.children.length
            }));
            const allElements = document.querySelectorAll('*');
            const menuClasses = new Set();
            allElements.forEach(el => {
                const cls = (el.className || '').toString();
                cls.split(/\\s+/).forEach(c => {
                    if (c && (c.includes('menu') || c.includes('Menu') || c.includes('nav') || c.includes('Nav'))) {
                        menuClasses.add(c);
                    }
                });
            });
            let shadowRoots = 0;
            allElements.forEach(el => { if (el.shadowRoot) shadowRoots++; });
            const iframes = document.querySelectorAll('iframe');
            return {
                url: window.location.href,
                title: document.title,
                selectorCounts: counts,
                bodyChildren: bodyChildren,
                menuClasses: Array.from(menuClasses).slice(0, 50),
                shadowRoots: shadowRoots,
                iframeCount: iframes.length,
                totalElements: allElements.length,
                bodyTextPreview: document.body ? document.body.innerText.substring(0, 500) : ''
            };
        }
        """)

        print(f"  URL: {dom_scan['url']}")
        print(f"  Title: {dom_scan['title']}")
        print(f"  总元素数: {dom_scan['totalElements']}")
        print(f"  Shadow Roots: {dom_scan['shadowRoots']}")
        print(f"  iframes: {dom_scan['iframeCount']}")
        print()
        print(f"  选择器匹配:")
        for sel, count in sorted(dom_scan['selectorCounts'].items(), key=lambda x: -x[1]):
            print(f"    {sel}: {count}")
        print()
        print(f"  body 直接子元素:")
        for child in dom_scan['bodyChildren']:
            print(f"    <{child['tag']} id='{child['id']}' class='{child['cls'][:80]}'> ({child['childCount']} children)")
        print()
        print(f"  包含 menu/nav 的 class 名 ({len(dom_scan['menuClasses'])} 个):")
        for c in dom_scan['menuClasses'][:30]:
            print(f"    .{c}")
        print()
        print(f"  页面文本预览:")
        print(f"    {dom_scan['bodyTextPreview'][:300]}")

        # ====== 1. 获取完整菜单树 DOM 结构 ======
        print("=" * 80)
        print("1. 完整菜单树 DOM 结构")
        print("=" * 80)

        menu_tree = await page.evaluate("""
        () => {
            function getTree(el, depth) {
                const result = [];
                // 查找直接子菜单项
                const children = el.children;
                for (let i = 0; i < children.length; i++) {
                    const child = children[i];
                    const cls = child.className || '';

                    if (cls.includes('el-submenu')) {
                        const title = child.querySelector(':scope > .el-submenu__title');
                        const titleText = title ? title.textContent.trim() : '';
                        const submenu = child.querySelector(':scope > .el-menu');
                        const subItems = submenu ? getTree(submenu, depth + 1) : [];
                        result.push({
                            type: 'submenu',
                            title: titleText,
                            depth: depth,
                            classes: cls.substring(0, 200),
                            isOpen: cls.includes('is-opened'),
                            children: subItems
                        });
                    } else if (cls.includes('el-menu-item')) {
                        result.push({
                            type: 'menu-item',
                            text: child.textContent.trim(),
                            depth: depth,
                            classes: cls.substring(0, 200),
                            href: child.getAttribute('href') || '',
                            isActive: cls.includes('is-active')
                        });
                    }
                }
                return result;
            }

            // 找到根菜单
            const menuEl = document.querySelector('.el-menu');
            if (!menuEl) return { error: 'no .el-menu found' };
            return {
                rootClasses: menuEl.className,
                tree: getTree(menuEl, 0)
            };
        }
        """)

        def print_tree(nodes, indent=0):
            for node in nodes:
                prefix = "  " * indent
                if node["type"] == "submenu":
                    open_mark = "📂" if node.get("isOpen") else "📁"
                    print(f"{prefix}{open_mark} [{node['depth']}] {node['title']}")
                    print_tree(node.get("children", []), indent + 1)
                else:
                    active_mark = "●" if node.get("isActive") else "○"
                    print(f"{prefix}{active_mark} [{node['depth']}] {node['text']}")

        if "error" in menu_tree:
            print(f"  错误: {menu_tree['error']}")
        else:
            print(f"  根菜单 classes: {menu_tree['rootClasses']}")
            print_tree(menu_tree.get("tree", []))

        # ====== 2. 先展开所有子菜单 ======
        print()
        print("=" * 80)
        print("2. 展开所有子菜单")
        print("=" * 80)

        titles = page.locator(".el-submenu__title")
        n = await titles.count()
        print(f"  找到 {n} 个 .el-submenu__title")
        for i in range(n):
            try:
                await titles.nth(i).click(timeout=1000)
                await page.wait_for_timeout(300)
            except Exception:
                pass
        await page.wait_for_timeout(1000)

        # 展开后再次获取树
        menu_tree2 = await page.evaluate("""
        () => {
            function getTree(el, depth) {
                const result = [];
                const children = el.children;
                for (let i = 0; i < children.length; i++) {
                    const child = children[i];
                    const cls = child.className || '';
                    if (cls.includes('el-submenu')) {
                        const title = child.querySelector(':scope > .el-submenu__title');
                        const titleText = title ? title.textContent.trim() : '';
                        const submenu = child.querySelector(':scope > .el-menu');
                        const subItems = submenu ? getTree(submenu, depth + 1) : [];
                        result.push({
                            type: 'submenu',
                            title: titleText,
                            depth: depth,
                            isOpen: cls.includes('is-opened'),
                            children: subItems
                        });
                    } else if (cls.includes('el-menu-item')) {
                        result.push({
                            type: 'menu-item',
                            text: child.textContent.trim(),
                            depth: depth,
                            href: child.getAttribute('href') || '',
                            isActive: cls.includes('is-active')
                        });
                    }
                }
                return result;
            }
            const menuEl = document.querySelector('.el-menu');
            if (!menuEl) return { error: 'no .el-menu found' };
            return { tree: getTree(menuEl, 0) };
        }
        """)

        if "error" not in menu_tree2:
            print_tree(menu_tree2.get("tree", []))

        # 统计叶子菜单项
        all_leaf_items = await page.evaluate("""
        () => {
            const out = [];
            const seen = new Set();
            document.querySelectorAll('.el-menu-item').forEach(li => {
                const label = (li.textContent || '').trim();
                if (!label || seen.has(label)) return;
                seen.add(label);
                let group = '';
                let p = li.parentElement;
                while (p) {
                    const tt = p.querySelector(':scope > .el-submenu__title');
                    if (tt) {
                        group = (tt.textContent || '').trim();
                        break;
                    }
                    p = p.parentElement;
                }
                // 计算深度
                let depth = 0;
                let pp = li;
                while (pp) {
                    if (pp.classList && pp.classList.contains('el-submenu')) depth++;
                    pp = pp.parentElement;
                }
                out.push({label, group, depth});
            });
            return out;
        }
        """)

        print()
        print(f"  叶子菜单项总数: {len(all_leaf_items)}")
        for item in all_leaf_items:
            print(f"    [depth={item['depth']}] {item['label']} (分组: {item['group']})")

        # ====== 3. 逐个点击叶子菜单，检查页面类型 ======
        print()
        print("=" * 80)
        print("3. 逐个点击叶子菜单，检查页面内容类型")
        print("=" * 80)

        results = []
        for item in all_leaf_items:
            label = item["label"]
            try:
                items = page.locator(f".el-menu-item:has-text('{label}')")
                n = await items.count()
                if n == 0:
                    print(f"  ❌ {label}: 未找到菜单项")
                    continue
                await items.first.click(timeout=3000)
                await page.wait_for_timeout(2000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=3000)
                except:
                    pass

                # 检查页面内容
                page_info = await page.evaluate("""
                () => {
                    const tables = document.querySelectorAll('.el-table');
                    const tableRows = document.querySelectorAll('.el-table__body-wrapper .el-table__row');
                    const forms = document.querySelectorAll('.el-form');
                    const cards = document.querySelectorAll('.el-card');
                    const tabs = document.querySelectorAll('.el-tabs');
                    const trees = document.querySelectorAll('.el-tree');
                    const descriptions = document.querySelectorAll('.el-descriptions, .el-description');
                    const charts = document.querySelectorAll('[class*="chart"], [class*="Chart"], canvas');
                    const emptyStates = document.querySelectorAll('.el-empty, [class*="empty"]');
                    const sideTree = document.querySelectorAll('[class*="side-tree"], [class*="sideTree"]');
                    const bodyText = document.body ? document.body.innerText.substring(0, 300).trim() : '';
                    const mainArea = document.querySelector('.main-content, [class*="main-content"], [class*="content-area"]');
                    const mainText = mainArea ? mainArea.textContent.substring(0, 200).trim() : '';

                    return {
                        url: window.location.href,
                        tables: tables.length,
                        tableRows: tableRows.length,
                        forms: forms.length,
                        cards: cards.length,
                        tabs: tabs.length,
                        trees: trees.length,
                        descriptions: descriptions.length,
                        charts: charts.length,
                        emptyStates: emptyStates.length,
                        sideTree: sideTree.length,
                        bodyPreview: bodyText.substring(0, 150),
                        mainPreview: mainText.substring(0, 100)
                    };
                }
                """)

                # 判断页面类型
                page_type = "unknown"
                if page_info["tables"] > 0 and page_info["tableRows"] > 0:
                    page_type = "list_table"
                elif page_info["tables"] > 0:
                    page_type = "table_empty"
                elif page_info["forms"] > 0 and page_info["cards"] > 0:
                    page_type = "form_card"
                elif page_info["forms"] > 0:
                    page_type = "form"
                elif page_info["cards"] > 0:
                    page_type = "card"
                elif page_info["trees"] > 0:
                    page_type = "tree"
                elif page_info["charts"] > 0:
                    page_type = "chart"
                elif page_info["descriptions"] > 0:
                    page_type = "description"
                elif page_info["emptyStates"] > 0:
                    page_type = "empty"

                is_current_valid = page_info["tables"] > 0 and page_info["tableRows"] > 0
                old_filter = "✅" if is_current_valid else "❌"

                detail_parts = []
                if page_info["tables"]: detail_parts.append(f"table={page_info['tables']}")
                if page_info["tableRows"]: detail_parts.append(f"rows={page_info['tableRows']}")
                if page_info["forms"]: detail_parts.append(f"form={page_info['forms']}")
                if page_info["cards"]: detail_parts.append(f"card={page_info['cards']}")
                if page_info["tabs"]: detail_parts.append(f"tabs={page_info['tabs']}")
                if page_info["trees"]: detail_parts.append(f"tree={page_info['trees']}")
                if page_info["sideTree"]: detail_parts.append(f"sideTree={page_info['sideTree']}")
                if page_info["charts"]: detail_parts.append(f"chart={page_info['charts']}")
                if page_info["descriptions"]: detail_parts.append(f"desc={page_info['descriptions']}")
                if page_info["emptyStates"]: detail_parts.append(f"empty={page_info['emptyStates']}")

                print(f"  {old_filter} {label} [d={item['depth']}]")
                print(f"     URL: {page_info['url']}")
                print(f"     类型: {page_type} | 元素: {', '.join(detail_parts) if detail_parts else 'none'}")
                if page_info["bodyPreview"]:
                    print(f"     预览: {page_info['bodyPreview'][:120]}")
                print()

                results.append({
                    "label": label,
                    "depth": item["depth"],
                    "group": item["group"],
                    "url": page_info["url"],
                    "page_type": page_type,
                    "elements": detail_parts,
                    "old_filter": is_current_valid
                })

            except Exception as e:
                print(f"  ❌ {label}: 异常 {e}")

        # ====== 4. 汇总 ======
        print()
        print("=" * 80)
        print("4. 汇总统计")
        print("=" * 80)

        old_pass = [r for r in results if r["old_filter"]]
        old_fail = [r for r in results if not r["old_filter"]]

        type_counts = {}
        for r in results:
            t = r["page_type"]
            type_counts[t] = type_counts.get(t, 0) + 1

        print(f"  总计: {len(results)} 个叶子菜单项")
        print(f"  旧过滤器(table+rows)通过: {len(old_pass)} 个")
        print(f"  旧过滤器拒绝: {len(old_fail)} 个")
        print()
        print(f"  页面类型分布:")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"    {t}: {c}")
        print()

        if old_fail:
            print(f"  被旧过滤器拒绝的模块:")
            for r in old_fail:
                print(f"    - {r['label']} [{r['page_type']}] {' '.join(r['elements'])}")

        # 保存完整结果
        out_path = Path("D:/Mobile/API_AI_test/workspace/ecm-compute/output/debug/menu_diagnosis.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整结果已保存: {out_path}")

        await browser.close()

asyncio.run(main())
