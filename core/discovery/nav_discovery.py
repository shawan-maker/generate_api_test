"""
nav_discovery.py — 递归菜单爬取模块

职责：展开侧边栏菜单树（多级），逐层点击叶子菜单项，记录实际 URL。
与 lib/browser/nav.py 的区别：nav.py 只从 DOM 读取 href（可能为空），
本模块实际点击菜单项获取 page.url（SPA 路由跳转后）。

支持多级嵌套菜单（Element UI .el-menu 结构）：
  - 一级菜单（.el-submenu__title）→ 展开后显示二级菜单
  - 二级菜单可能是叶子（.el-menu-item）或三级子菜单（.el-submenu）
  - 递归展开直到所有叶子菜单都被访问
"""

import logging
from pathlib import Path

LOG = logging.getLogger("nav_discovery")


async def crawl_menu_tree(page, wait_ms=1500, max_depth=5) -> list:
    """
    递归展开所有菜单 → 逐个点击叶子菜单项 → 记录实际 URL。

    Args:
        page: Playwright Page
        wait_ms: 点击后等待页面加载的毫秒数
        max_depth: 最大菜单层级（防止无限递归）

    Returns: [
        {
            "label": "角色管理",
            "url": "https://.../user-center/user-manage/role",
            "group": "访问控制",
            "level": 1
        },
        ...
    ]
    """
    home_url = page.url
    LOG.info(f"  菜单爬取起点: {home_url}")

    # Step 0: 诊断当前页面
    diag = await _diagnose_menu_structure(page)
    LOG.info(f"  菜单诊断: submenu_titles={diag.get('submenu_titles', 0)}, "
             f"menu_items={diag.get('menu_items', 0)}, "
             f"submenus={diag.get('submenus', 0)}, "
             f"tables={diag.get('tables', 0)}")
    LOG.info(f"  页面标题: {diag.get('title', '')}")
    LOG.info(f"  页面内容预览: {diag.get('bodyTextPreview', '')[:150]}")

    # 如果检测到 404 或空白页，等待后仍未找到菜单，返回空列表
    body_text = diag.get('bodyTextPreview', '').strip()
    if '404' in body_text or len(body_text) < 50:
        LOG.info("  检测到 404 或空白页，额外等待 8 秒让 SPA 完全加载...")
        await page.wait_for_timeout(8000)
        diag = await _diagnose_menu_structure(page)
        LOG.info(f"  再次诊断: submenu_titles={diag.get('submenu_titles', 0)}, "
                 f"menu_items={diag.get('menu_items', 0)}")

        if diag.get('submenu_titles', 0) == 0 and diag.get('menu_items', 0) == 0:
            LOG.warning("  未找到菜单元素，可能页面不正确或 SPA 未完全加载")
            return []

    # Step 1: 递归收集所有叶子菜单项（仅获取名称、分组、层级，不点击）
    LOG.info("  开始递归收集菜单结构...")
    leaf_items = await _collect_leaf_items_recursive(page, home_url, depth=0,
                                                      group_prefix="", max_depth=max_depth)

    LOG.info(f"  收集到 {len(leaf_items)} 个叶子菜单项")
    for item in leaf_items[:5]:
        LOG.info(f"    - [{item['level']}] {item['label']} (分组: {item['group']})")
    if len(leaf_items) > 5:
        LOG.info(f"    ... 还有 {len(leaf_items) - 5} 个")

    # Step 2: 逐个点击叶子菜单项，记录 URL
    discovered = []
    for item in leaf_items:
        label = item["label"]
        group = item["group"]
        level = item["level"]

        LOG.info(f"  点击菜单: [{level}] {label} (分组: {group})")

        # 从 home_url 重新导航并按层级展开菜单
        clicked = await _navigate_and_click_leaf(page, home_url, item["menu_path"])
        if not clicked:
            LOG.warning(f"    ⚠️ 未能点击: {label}")
            continue

        # 等待页面加载
        await page.wait_for_timeout(wait_ms)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass

        # 记录 URL
        url = page.url

        LOG.info(f"    URL: {url}")

        discovered.append({
            "label": label,
            "url": url,
            "group": group,
            "level": level
        })

    return discovered


async def _collect_leaf_items_recursive(page, home_url, depth, group_prefix, max_depth) -> list:
    """递归收集所有叶子菜单项（仅 DOM 结构，不点击导航）。

    通过展开所有层级的 el-submenu，然后读取所有 el-menu-item 获取完整菜单树。
    每个菜单项记录其 menu_path（从根到该菜单的路径），用于后续点击。

    Returns: [{"label": "...", "group": "...", "level": N, "menu_path": [...]}, ...]
    """
    if depth >= max_depth:
        return []

    # 展开所有当前可见的 .el-submenu__title（按层级逐个）
    await _expand_all_visible_submenus(page)
    await page.wait_for_timeout(800)

    # 使用 JS 递归读取完整菜单树结构
    menu_data = await page.evaluate("""
    () => {
        function collectItems(menuEl, depth, groupPath) {
            const results = [];
            if (!menuEl || !menuEl.children) return results;

            for (let i = 0; i < menuEl.children.length; i++) {
                const child = menuEl.children[i];
                const cls = (child.className || '').toString();

                if (cls.includes('el-submenu')) {
                    // 这是一个分组节点
                    const titleEl = child.querySelector(':scope > .el-submenu__title');
                    const titleText = titleEl ? titleEl.textContent.trim() : '';
                    const subMenu = child.querySelector(':scope > .el-menu');

                    if (subMenu) {
                        const newGroupPath = groupPath ? groupPath + ' > ' + titleText : titleText;
                        const subItems = collectItems(subMenu, depth + 1, newGroupPath);
                        results.push(...subItems);
                    }
                } else if (cls.includes('el-menu-item')) {
                    // 这是一个叶子菜单项
                    const label = (child.textContent || '').trim();
                    if (label) {
                        results.push({
                            label: label,
                            group: groupPath || '',
                            level: depth,
                            menu_path: groupPath ? groupPath.split(' > ').concat([label]) : [label]
                        });
                    }
                }
            }
            return results;
        }

        // 找到根 .el-menu
        const rootMenu = document.querySelector('.el-menu');
        if (!rootMenu) return [];
        return collectItems(rootMenu, 0, '');
    }
    """)

    return menu_data


async def _expand_all_visible_submenus(page, max_rounds=10):
    """展开所有可见的 .el-submenu__title（多轮扫描，处理嵌套展开后的新子菜单）。"""
    for round_num in range(max_rounds):
        # 查找所有未展开的 .el-submenu__title
        new_expanded = await page.evaluate("""
        () => {
            let count = 0;
            document.querySelectorAll('.el-submenu').forEach(submenu => {
                const cls = (submenu.className || '').toString();
                if (!cls.includes('is-opened')) {
                    const title = submenu.querySelector(':scope > .el-submenu__title');
                    if (title) {
                        // 检查是否可见
                        const rect = title.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            title.click();
                            count++;
                        }
                    }
                }
            });
            return count;
        }
        """)

        if new_expanded == 0:
            break  # 没有新的子菜单需要展开

        await page.wait_for_timeout(500)
        LOG.debug(f"    展开轮次 {round_num + 1}: 展开了 {new_expanded} 个子菜单")


async def _navigate_and_click_leaf(page, home_url, menu_path: list) -> bool:
    """从 home_url 出发，按 menu_path 逐级展开菜单，最终点击叶子菜单项。

    Args:
        page: Playwright Page
        home_url: 菜单首页 URL
        menu_path: 菜单路径，如 ["项目管理", "容量管理", "资源池容量"]

    Returns:
        bool: 是否成功点击
    """
    if not menu_path:
        return False

    # 导航回首页
    try:
        await page.goto(home_url, wait_until="load", timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(2000)

    # 逐级展开菜单路径（除了最后一个叶子节点）
    for i, name in enumerate(menu_path[:-1]):
        # 点击该层级的 submenu title
        clicked = await page.evaluate(f"""
        () => {{
            const titles = document.querySelectorAll('.el-submenu__title');
            for (const title of titles) {{
                if (title.textContent.trim() === '{_js_escape(name)}') {{
                    // 检查是否已展开
                    const submenu = title.closest('.el-submenu');
                    if (submenu && !submenu.className.includes('is-opened')) {{
                        title.click();
                        return 'expanded';
                    }} else if (submenu && submenu.className.includes('is-opened')) {{
                        return 'already_open';
                    }}
                    title.click();
                    return 'clicked';
                }}
            }}
            return 'not_found';
        }}
        """)

        if clicked == 'not_found':
            LOG.debug(f"    菜单路径未找到: {name}")
            return False

        await page.wait_for_timeout(500)

    # 最后点击叶子菜单项
    leaf_name = menu_path[-1]
    clicked = await page.evaluate(f"""
    () => {{
        const items = document.querySelectorAll('.el-menu-item');
        for (const item of items) {{
            if (item.textContent.trim() === '{_js_escape(leaf_name)}') {{
                const rect = item.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {{
                    item.click();
                    return true;
                }}
            }}
        }}
        return false;
    }}
    """)

    return clicked


def _js_escape(s: str) -> str:
    """转义 JS 字符串中的特殊字符。"""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


async def _diagnose_menu_structure(page) -> dict:
    """诊断当前页面的菜单结构"""
    try:
        result = await page.evaluate("""
            () => {
                const body = document.body;
                const title = document.title;
                const url = window.location.href;

                // 检查常见的导航容器
                const navContainers = [
                    'nav', '.nav', '[class*="nav"]', '[class*="Nav"]',
                    'aside', '.aside', '[class*="aside"]', '[class*="Aside"]',
                    '.sidebar', '[class*="sidebar"]', '[class*="Sidebar"]',
                    '.menu', '[class*="menu"]', '[class*="Menu"]',
                    'header', '.header', '[class*="header"]', '[class*="Header"]',
                    '.layout', '[class*="layout"]', '[class*="Layout"]'
                ];

                const navElements = {};
                navContainers.forEach(sel => {
                    try {
                        navElements[sel] = document.querySelectorAll(sel).length;
                    } catch(e) {
                        navElements[sel] = 'error';
                    }
                });

                // 检查页面主要内容
                const mainContent = document.querySelector('main, [role="main"], .main, #main, [class*="main"]');
                const mainText = mainContent ? mainContent.textContent.substring(0, 200) : '';

                // 检查所有链接
                const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 10).map(a => ({
                    href: a.getAttribute('href'),
                    text: a.textContent.trim().substring(0, 50)
                }));

                return {
                    submenu_titles: document.querySelectorAll('.el-submenu__title').length,
                    menu_items: document.querySelectorAll('.el-menu-item').length,
                    submenus: document.querySelectorAll('.el-submenu').length,
                    tables: document.querySelectorAll('.el-table').length,
                    nav_elements: navElements,
                    title: title,
                    url: url,
                    bodyTextPreview: body ? body.textContent.substring(0, 300).trim() : '',
                    mainContentPreview: mainText,
                    links: links,
                    bodyChildCount: body ? body.children.length : 0
                };
            }
        """)
        return result
    except Exception as e:
        return {"error": str(e)}


def save_discovery_result(discovered: list, workspace_dir: Path):
    """保存导航发现结果到 workspace/<project>/kb/navigation_discovered.json"""
    import json
    output_path = workspace_dir / "kb" / "navigation_discovered.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(discovered, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return output_path


def load_discovery_result(workspace_dir: Path, max_age_days=7) -> list:
    """
    加载缓存的导航发现结果。
    max_age_days: 缓存有效期（天），超过则返回空列表。
    """
    import json
    from datetime import datetime

    cache_path = workspace_dir / "kb" / "navigation_discovered.json"
    if not cache_path.exists():
        return []

    # 检查文件年龄
    mtime = cache_path.stat().st_mtime
    age_days = (datetime.now().timestamp() - mtime) / 86400
    if age_days > max_age_days:
        return []

    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return []
