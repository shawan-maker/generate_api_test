/**
 * api_scanner.js — 通用 API 扫描器
 *
 * 功能：
 *   1. 用 cookie 或登录的方式进入目标页面
 *   2. 遍历页面上所有可见按钮（含下拉菜单），逐一点击
 *   3. 拦截所有触发的 API 请求（method/path/body）
 *   4. 响应样本捕获（供后续推导 body 结构）
 *   5. 归类输出（创建/删除/查询/修改/锁定等）
 *   6. 生成可执行测试脚本
 *
 * 用法（作为模块）:
 *   const scanner = require('./api_scanner');
 *   const result = await scanner.scan({
 *     targetUrl: 'https://.../user-manage/user',
 *     cookieFile: './cookies.json',
 *     // 如无 cookie，提供登录参数:
 *     loginUrl: 'https://.../login',
 *     username: 'xxx',
 *     password: 'xxx',
 *   });
 *
 * 参考: API_cases 的 api_discovery.js（按钮遍历+API捕获）
 *        EcsCloud 的 login.js（登录）
 *
 * 输出: { endpoints, byCategory, samples, generatedScript }
 */
const fs = require('fs');
const path = require('path');
// Playwright: 设置 NODE_PATH 指向 EcsCloud 的 node_modules
const PW_PATH = path.join(__dirname, '..', '..', 'EcsCloud', 'node_modules');
const origNodePath = process.env.NODE_PATH || '';
if (origNodePath) {
  process.env.NODE_PATH = PW_PATH + path.delimiter + origNodePath;
} else {
  process.env.NODE_PATH = PW_PATH;
}
require('module').Module._initPaths();
const { chromium } = require('playwright');

// ---- 内部工具 ----
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

/**
 * 从 cookies.json 加载 cookie 并注入 context
 */
async function injectCookies(ctx, cookieFile) {
  if (!fs.existsSync(cookieFile)) return false;
  try {
    const cookies = JSON.parse(fs.readFileSync(cookieFile, 'utf8'));
    if (!Array.isArray(cookies) || cookies.length === 0) return false;
    await ctx.addCookies(cookies);
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * 检查页面是否已登录（URL 不含 /login）
 */
async function checkLoggedIn(page, targetUrl) {
  try {
    await page.goto(targetUrl, { waitUntil: 'load', timeout: 30000 });
    await sleep(5000);
    return !page.url().includes('/login');
  } catch (e) {
    return false;
  }
}

/**
 * 获取页面上所有可见的可点击元素
 */
async function discoverButtons(page) {
  return page.evaluate(() => {
    const seen = new Set();
    const results = [];
    // 遍历所有可见节点，不限选择器
    const all = document.querySelectorAll('*');
    all.forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      const text = (el.textContent || '').trim();
      if (!text || text.length > 30 || text.length < 1) return;
      // 只保留叶子节点或按钮/链接类标签
      const tag = String(el.tagName).toLowerCase();
      let isLeaf = false;
      try { isLeaf = el.children.length === 0 || Array.from(el.children).every(c => c.offsetWidth === 0); } catch(e) { isLeaf = true; }
      const isActionTag = ['button', 'a', 'span', 'i', 'em', 'strong', 'b', 'label', 'td', 'div', 'li'].includes(tag);
      if (!isLeaf && !isActionTag) return;
      // 过滤纯数字/符号
      if (/^[\d.,%+\-\s]+$/.test(text)) return;
      if (text.length <= 1) return;
      const key = text + tag + (typeof el.className === 'string' ? el.className.slice(0, 20) : '');
      if (seen.has(key)) return;
      seen.add(key);
      // 跳过 SVG 元素（className 不是字符串）
      let cls = '';
      try { cls = String(el.className || '').slice(0, 60); } catch(e) { cls = ''; }
      results.push({
        text,
        tag: tag,
        class: cls,
        rect: `${Math.round(r.width)}x${Math.round(r.height)}`,
        pos: `${Math.round(r.x)},${Math.round(r.y)}`,
      });
    });
    return results;
  });
}

/**
 * 通过文本内容点击元素
 */
async function clickByText(page, text) {
  return page.evaluate((t) => {
    const all = document.querySelectorAll('span, button, a, div, li, .el-dropdown-menu__item, td, label, em');
    for (const el of all) {
      if ((el.textContent || '').trim() === t && el.offsetWidth > 0 && el.offsetHeight > 0) {
        el.click();
        return true;
      }
    }
    return false;
  }, text);
}

/**
 * 获取下拉菜单中的可见项
 */
async function getDropdownItems(page) {
  return page.evaluate(() => {
    const items = document.querySelectorAll('.el-dropdown-menu__item, [class*="dropdown"] li');
    return Array.from(items).filter(i => i.offsetWidth > 0).map(i => i.textContent.trim());
  });
}

/**
 * 获取弹窗中的表单字段
 */
async function getDialogFields(page) {
  return page.evaluate(() => {
    const dlgs = document.querySelectorAll(
      '.el-dialog, .el-dialog__wrapper, [role="dialog"], ' +
      '[class*="Drawer"], .el-drawer'
    );
    for (const dlg of dlgs) {
      if (dlg.offsetWidth <= 0) continue;
      const labels = dlg.querySelectorAll('.el-form-item__label, label');
      const inputs = dlg.querySelectorAll('input, textarea');
      return {
        visible: true,
        className: dlg.className.slice(0, 80),
        fields: Array.from(labels).map(l => (l.textContent || '').trim()).filter(x => x),
        inputCount: inputs.length,
        inputPlaceholders: Array.from(inputs).map(i => i.placeholder || '').filter(x => x),
      };
    }
    // 也查 body 最后追加的子节点（可能是 Dialog）
    const last = document.body.lastElementChild;
    if (last && last.offsetWidth > 0) {
      const inputs = last.querySelectorAll('input');
      return {
        visible: true,
        className: last.className.slice(0, 80),
        fields: [],
        inputCount: inputs.length,
        inputPlaceholders: Array.from(inputs).map(i => i.placeholder || ''),
        note: 'body:last-child',
      };
    }
    return null;
  });
}

/**
 * 关闭当前弹窗
 */
async function closeDialog(page) {
  // 尝试点击关闭按钮
  const closed = await page.evaluate(() => {
    const btns = document.querySelectorAll(
      '.el-dialog__headerbtn, .el-drawer__close-btn, ' +
      '[class*="close"], [class*="Close"], ' +
      '.el-message-box__close, .el-overlay-dialog .el-dialog__headerbtn'
    );
    for (const btn of btns) {
      if (btn.offsetWidth > 0 && btn.offsetHeight > 0) {
        btn.click();
        return 'clicked_close';
      }
    }
    // 按 Escape
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    return 'sent_escape';
  });
  await sleep(1000);
  return closed;
}

/**
 * 填写弹窗中的输入框（填随机测试数据）
 */
async function fillDialog(page) {
  await page.evaluate((ts) => {
    const dlg = document.querySelector(
      '.el-dialog, .el-dialog__wrapper, [role="dialog"], ' +
      '.el-drawer, [class*="Drawer"]'
    );
    if (!dlg || dlg.offsetWidth <= 0) return 0;
    const inputs = dlg.querySelectorAll('input');
    let count = 0;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value'
    ).set;

    inputs.forEach(inp => {
      if (inp.offsetWidth <= 0) return;
      const ph = (inp.placeholder || '').toLowerCase();
      let val = '';
      if (ph.includes('姓名') || ph.includes('名称') || ph.includes('name')) {
        val = 'AT_测试_' + ts;
      } else if (ph.includes('邮箱') || ph.includes('mail') || ph.includes('email')) {
        val = 'at_' + ts + '@example.com';
      } else if (ph.includes('手机') || ph.includes('phone') || ph.includes('电话')) {
        val = '13800138000';
      } else if (ph.includes('描述') || ph.includes('remark') || ph.includes('备注')) {
        val = '自动创建_' + ts;
      } else if (ph.includes('用户名') || ph.includes('user')) {
        val = 'atuser_' + ts;
      } else if (count === 0) {
        val = 'AT_' + ts;  // 第一个输入框兜底
      }
      if (val) {
        nativeSetter.call(inp, val);
        inp.dispatchEvent(new Event('input', { bubbles: true }));
        inp.dispatchEvent(new Event('change', { bubbles: true }));
        count++;
      }
    });
    return count;
  }, Date.now().toString(36).slice(-6));
  await sleep(1000);
}

/**
 * 默认跳过列表（导航菜单、静态文本等）
 */
const DEFAULT_SKIP_TEXTS = new Set([
  '用户管理', '用户组管理', '角色管理', '授权管理', '黑白名单',
  '项目管理', '日志管理', '账户管理', '访问控制', '单位管理',
  '代维管理', '部门管理', '当前组织：', '正常', '是', '共',
  '每页显示', '条', '前往页', 'GO', 'eStack Enterprise',
  '总览', '运营中心', '文档中心', '消息中心',
]);

const DEFAULT_SKIP_PREFIXES = ['消息中心', '共', '每页'];

/**
 * 按 URL 模式归类 API
 */
function classifyEndpoint(method, pathname) {
  const p = pathname.toLowerCase();
  if (method === 'DELETE') return '删除';
  if (method === 'PUT' || method === 'PATCH') return '修改';
  if (method === 'POST') {
    if (p.includes('/create') || p.includes('/add') || p.includes('/save')) return '创建';
    if (p.includes('/delete') || p.includes('/remove')) return '删除';
    if (p.includes('/update') || p.includes('/edit') || p.includes('/modify')) return '修改';
    if (p.includes('/lock')) return '锁定';
    if (p.includes('/unlock') || p.includes('/enable')) return '解锁/启用';
    if (p.includes('/disable') || p.includes('/stop')) return '停用';
    if (p.includes('/list') || p.includes('/page') || p.includes('/search')) return '查询';
    if (p.includes('/export')) return '导出';
    if (p.includes('/import')) return '导入';
    if (p.includes('/reset')) return '重置密码';
    if (p.includes('/login')) return '登录';
    return '其他POST';
  }
  if (method === 'GET') {
    if (p.includes('/detail')) return '详情';
    if (p.includes('/current')) return '当前用户';
    return '其他GET';
  }
  return '其他';
}

// ===== 导出接口 =====

/**
 * 主扫描函数
 * @param {Object} opts
 * @param {string} opts.targetUrl - 目标页面 URL
 * @param {string} [opts.cookieFile] - cookies.json 路径
 * @param {string} [opts.loginUrl] - 登录 URL（无 cookie 时使用）
 * @param {string} [opts.username] - 登录用户名
 * @param {string} [opts.password] - 登录密码
 * @param {string[]} [opts.skipTexts] - 额外跳过的按钮文本
 * @param {number} [opts.clickDelay=5000] - 点击后等待时间(ms)
 * @param {boolean} [opts.headless=false] - 是否无头模式
 * @returns {Promise<Object>} 扫描结果
 */
async function scan(opts = {}) {
  const {
    targetUrl,
    cookieFile,
    loginUrl,
    username,
    password,
    skipTexts = [],
    clickDelay = 5000,
    headless = false,
  } = opts;

  if (!targetUrl) throw new Error('targetUrl is required');

  const allSkips = new Set([...DEFAULT_SKIP_TEXTS, ...skipTexts]);

  // ---- 启动浏览器 ----
  const browser = await chromium.launch({
    headless,
    args: ['--ignore-certificate-errors', '--disable-web-security', '--no-sandbox'],
  });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  // ---- 登录/加载 cookie ----
  let loggedIn = false;
  if (cookieFile) {
    const injected = await injectCookies(ctx, cookieFile);
    if (injected) {
      loggedIn = await checkLoggedIn(page, targetUrl);
    }
  }

  if (!loggedIn && loginUrl && username && password) {
    console.log('  [扫描器] 执行登录...');
    const { loginToEStack } = require(path.join(__dirname, '..', 'scripts', 'login.js'));
    const ok = await loginToEStack(page, username, password, {
      loginUrl,
      screenshotDir: path.join(cookieFile ? path.dirname(cookieFile) : __dirname, '..', 'output', 'screenshots'),
    });
    if (!ok) {
      await browser.close();
      throw new Error('登录失败');
    }
    loggedIn = true;
    // 保存 cookie
    if (cookieFile) {
      const cookies = await ctx.cookies();
      fs.mkdirSync(path.dirname(cookieFile), { recursive: true });
      fs.writeFileSync(cookieFile, JSON.stringify(cookies, null, 2), 'utf8');
    }
  }

  if (!loggedIn) {
    // 直接尝试访问
    await page.goto(targetUrl, { waitUntil: 'load', timeout: 30000 }).catch(() => {});
    await sleep(5000);
  }

  // 如果不在目标页面，跳转
  const baseUrl = new URL(targetUrl).origin;
  
  // ---- API 拦截 ----
  const allCalls = [];
  let currentContext = 'page-load';

  page.on('request', req => {
    const u = req.url();
    if (!u.includes('/estack/api/estack/')) return;
    if (req.method() === 'OPTIONS') return;
    const pn = u.split('?')[0];
    if (/\.(js|css|png|jpg|svg|woff|ttf|ico|map)$/i.test(pn)) return;
    if (pn.includes('/web/')) return;
    allCalls.push({
      method: req.method(),
      pathname: pn.replace(baseUrl, ''),
      body: req.postData() || '',
      context: currentContext,
      ts: Date.now(),
    });
  });

  const samples = {};
  page.on('response', async resp => {
    const u = resp.url();
    if (!u.includes('/estack/api/estack/')) return;
    const ct = resp.headers()['content-type'] || '';
    if (!ct.includes('json')) return;
    try {
      const b = await resp.text();
      if (b && b.length > 20) {
        const pn = u.split('?')[0].replace(baseUrl, '');
        if (!samples[pn]) samples[pn] = [];
        if (samples[pn].length < 2) samples[pn].push({ status: resp.status(), body: b.length > 2000 ? b.slice(0, 2000) + '...' : b });
      }
    } catch(e) {}
  });

  // ---- 发现按钮 ----
  const buttons = await discoverButtons(page);
  console.log(`  [扫描器] 发现 ${buttons.length} 个可见元素`);
  buttons.forEach(b => console.log(`    [${b.tag}] "${b.text}" @${b.pos} ${b.rect} class="${b.class}"`));

  // 过滤
  const actionable = buttons.filter(b => {
    if (allSkips.has(b.text)) return false;
    if (DEFAULT_SKIP_PREFIXES.some(p => b.text.startsWith(p))) return false;
    if (b.text.length <= 1) return false;
    return true;
  });
  console.log(`  [扫描器] ${actionable.length} 个可操作按钮`);

  // ---- 逐一点击 ----
  for (const btn of actionable) {
    currentContext = `click:${btn.text}`;
    const clicked = await clickByText(page, btn.text);
    if (!clicked) continue;
    console.log(`  [扫描器] ✅ "${btn.text}"`);
    await sleep(clickDelay);

    // 检查弹窗
    const dlg = await getDialogFields(page);
    if (dlg) {
      console.log(`    弹窗: ${dlg.fields.join(', ')} (${dlg.inputCount} inputs)`);
      if (dlg.inputCount > 0) {
        await fillDialog(page);
        console.log(`    ✅ 已填表`);
        await sleep(1000);
      }
      
      // 点击"确定/保存"提交
      const submitClicked = await clickByText(page, '确定');
      if (submitClicked) {
        console.log(`    ✅ 点击"确定"`);
        await sleep(clickDelay);
      } else {
        const saveClicked = await clickByText(page, '保存');
        if (saveClicked) {
          console.log(`    ✅ 点击"保存"`);
          await sleep(clickDelay);
        }
      }
      
      // 关闭弹窗
      await closeDialog(page);
      await sleep(1000);
    }

    // 如果是"更多"按钮，遍历下拉菜单
    if (btn.text === '更多') {
      await sleep(2000);
      const items = await getDropdownItems(page);
      console.log(`    下拉菜单: ${items.join(', ')}`);
      for (const item of items) {
        if (allSkips.has(item)) continue;
        currentContext = `dropdown:${item}`;
        const itemClicked = await clickByText(page, item);
        if (!itemClicked) continue;
        console.log(`    ✅ 子项"${item}"`);
        await sleep(clickDelay);

        // 确认弹窗
        const confirmClicked = await clickByText(page, '确定');
        if (confirmClicked) {
          console.log(`    ✅ 确认"${item}"`);
          await sleep(clickDelay);
        }
        
        await closeDialog(page);
        await sleep(1000);

        // 重新展开"更多"
        await clickByText(page, '更多');
        await sleep(2000);
      }
    }
  }

  // ---- 归类 ----
  const uniq = {};
  for (const api of allCalls) {
    const key = api.method + ' ' + api.pathname;
    if (!uniq[key]) {
      uniq[key] = { method: api.method, pathname: api.pathname, contexts: new Set(), bodies: [] };
    }
    uniq[key].contexts.add(api.context);
    if (api.body && !uniq[key].bodies.includes(api.body)) {
      uniq[key].bodies.push(api.body);
    }
  }

  const byCategory = {};
  for (const ep of Object.values(uniq)) {
    const cat = classifyEndpoint(ep.method, ep.pathname);
    if (!byCategory[cat]) byCategory[cat] = [];
    byCategory[cat].push({
      method: ep.method,
      pathname: ep.pathname,
      contexts: [...ep.contexts],
      bodies: ep.bodies,
    });
  }

  // ---- 关闭 ----
  await browser.close();

  return {
    totalCalls: allCalls.length,
    uniqueEndpoints: Object.keys(uniq).length,
    byCategory,
    allEndpoints: Object.values(uniq).map(e => ({
      method: e.method,
      pathname: e.pathname,
      contexts: [...e.contexts],
      bodies: e.bodies,
    })),
    samples,
  };
}

module.exports = { scan, classifyEndpoint, discoverButtons, clickByText, getDialogFields, fillDialog, closeDialog };
