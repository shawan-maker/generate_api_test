"""
导航与菜单抓取 —— 对应方案设计 第4节(功能发现 step1 功能面) 与 P0 菜单树抓取。
优先走 service-menu API(最稳), 失败回退 DOM 枚举(.el-menu-item)。
"""
import re
import json


async def capture_menu_via_dom(page) -> list:
    """
    枚举左侧菜单树。返回 [{label, href, level}]。
    先展开所有 .el-submenu 以暴露嵌套项, 再用 JS 直接读 .el-menu-item(Element UI)。
    """
    # 展开所有子菜单, 暴露嵌套的 menu-item
    try:
        titles = page.locator(".el-submenu__title")
        n = await titles.count()
        for i in range(n):
            try:
                await titles.nth(i).click(timeout=1000)
            except Exception:
                pass
        await page.wait_for_timeout(1200)
    except Exception:
        pass

    js = r"""
    () => {
      const out = [];
      const seen = new Set();
      const push = (label, href, level, isGroup, group) => {
        const k = label + '|' + (isGroup?1:0);
        if (!label || seen.has(k)) return;
        seen.add(k); out.push({label, href: href||'', level, is_group: !!isGroup, group: group||''});
      };
      document.querySelectorAll('.el-menu-item').forEach(li => {
        const a = li.querySelector('a');
        const href = a ? a.getAttribute('href') : (li.getAttribute('data-path') || '');
        let g=''; let p=li.parentElement;
        while(p){ const tt=p.querySelector(':scope > .el-submenu__title'); if(tt){g=(tt.textContent||'').trim();break;} p=p.parentElement; }
        push((li.textContent||'').trim(), href, 0, false, g);
      });
      document.querySelectorAll('.el-submenu__title').forEach(t => {
        push((t.textContent||'').trim(), '', 0, true, '');
      });
      return out;
    }
    """
    return await page.evaluate(js)


def _parse_menu_response(body) -> list:
    """尽力从 service-menu 响应里抽出菜单项文本。结构因系统而异, 这里做宽松解析。"""
    items = []

    def rec(obj):
        if isinstance(obj, dict):
            for k in ("name", "title", "label", "text", "menuName", "productName"):
                if k in obj and isinstance(obj[k], str):
                    items.append({"label": obj[k], "href": obj.get("path") or obj.get("url") or "", "level": 0})
                    break
            for v in obj.values():
                rec(v)
        elif isinstance(obj, list):
            for v in obj:
                rec(v)
    rec(body)
    return items


async def capture_menu_via_api(get_func, endpoints: list) -> list:
    """
    通过 service-menu 类接口拉功能树。get_func(method, url) -> (status, json_body)。
    任一命中即返回解析出的菜单项。
    """
    import re as _re
    for pat in endpoints:
        rx = _re.compile(pat)
        # 这里只试 profile 里给的精确/正则前缀; 实际运行期会由 crawler 提供真实 URL 列表
        try:
            status, body = await get_func("GET", pat if pat.startswith("http") else pat)
        except Exception:
            continue
        if status and 200 <= status < 300 and body:
            items = _parse_menu_response(body)
            if items:
                return items
    return []


def summary_menu(items: list) -> str:
    lines = []
    for it in items:
        indent = "  " * it.get("level", 0)
        lines.append(f"{indent}- {it['label']}  ({it.get('href','') or 'no-href'})")
    return "\n".join(lines) if lines else "(空)"
