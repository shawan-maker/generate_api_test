/**
 * capture_user_manage_apis.js
 *
 * 通过 Playwright 登录 estack → 跳转用户管理页面 → 逐一点击各个操作按钮
 * （新增、编辑、锁定、解锁、删除），拦截抓取所有 API 请求的 method/path/body，
 * 保存到 projects/estack/kb/user_manage_captured.json
 *
 * 用法: node capture_user_manage_apis.js [--url <完整URL>]
 *
 * 依赖: playwright 已安装在 D:\Mobile\EcsCloud 环境
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

// ---- 配置 ----
const PROFILE_PATH = path.resolve(__dirname, '..', 'profile.yaml');
const OUTPUT_PATH = path.resolve(__dirname, '..', 'kb', 'user_manage_captured.json');

// 目标页面 URL (可通过环境变量或参数覆盖)
const args = process.argv.slice(2);
const urlIdx = args.indexOf('--url');
const DEFAULT_URL = urlIdx >= 0 && args[urlIdx + 1]
  ? args[urlIdx + 1]
  : 'https://console-estack-syyhb.cmecloud.cn/estack/web/estack/user-center/user-manage/user';

// ---- 工具 ----
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function parseYamlSimple(text) {
  const lines = text.split('\n');
  const result = {};
  let currentKey = null, currentVal = null;
  for (const line of lines) {
    const m = line.match(/^(\w[\w.-]*):\s*(.*)/);
    if (m) {
      if (currentKey !== null) result[currentKey] = currentVal;
      currentKey = m[1];
      let val = m[2].trim();
      if (val.startsWith('"') && val.endsWith('"')) val = val.slice(1, -1);
      if (val.startsWith("'") && val.endsWith("'")) val = val.slice(1, -1);
      currentVal = val;
    } else if (currentKey && line.startsWith('  ')) {
      // 子级缩进 - 简单拼串
      currentVal += '\n' + line;
    }
  }
  if (currentKey !== null) result[currentKey] = currentVal;
  return result;
}

function parseNestedYaml(text) {
  const lines = text.split('\n');
  const result = {};
  let currentSection = null;
  let currentSub = null;
  for (const line of lines) {
    if (line.match(/^\w/)) {
      const [k, ...rest] = line.split(':');
      currentSection = k.trim();
      result[currentSection] = {};
      currentSub = null;
      const v = rest.join(':').trim();
      if (v) {
        result[currentSection] = v;
        currentSection = null;
      }
    } else if (currentSection && line.match(/^\s{2}\w/)) {
      const [k, ...rest] = line.split(':');
      currentSub = k.trim();
      result[currentSection][currentSub] = rest.join(':').trim();
    } else if (currentSection && currentSub && line.match(/^\s{4}/)) {
      // 四层缩进 - auth: header_name 等
      const [k, ...rest] = line.split(':');
      if (!result[currentSection][currentSub]) result[currentSection][currentSub] = {};
      result[currentSection][currentSub][k.trim()] = rest.join(':').trim();
    }
  }
  return result;
}

function extractFixedHeaders(text) {
  const headers = {};
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('fixed_headers:')) {
      for (let j = i + 1; j < lines.length; j++) {
        const sub = lines[j].match(/^\s{4}(\w[\w-]*):\s*(.+)/);
        if (!sub) break;
        headers[sub[1]] = sub[2].trim();
      }
    }
  }
  return headers;
}

function extractAuth(text) {
  const auth = {};
  const lines = text.split('\n');
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].match(/^auth:/)) {
      for (let j = i + 1; j < lines.length; j++) {
        if (lines[j].match(/^\w/)) break;
        const m = lines[j].match(/^\s{4}(\w[\w-]*):\s*(.*)/);
        if (m) auth[m[1].trim()] = m[2].trim();
      }
    }
  }
  return auth;
}

// ---- 主流程 ----
async function main() {
  console.log('='.repeat(60));
  console.log('用户管理页面 API 捕获脚本');
  console.log('='.repeat(60));
  console.log(`目标 URL: ${DEFAULT_URL}`);

  // 1. 读取 profile
  const yamlText = fs.readFileSync(PROFILE_PATH, 'utf-8');
  const profile = parseNestedYaml(yamlText);

  const baseUrl = profile.base_url || 'https://console-estack-syyhb.cmecloud.cn';
  const loginUrl = profile.login_url || (baseUrl + '/estack/web/estack/login');
  const fixedHeaders = extractFixedHeaders(yamlText);
  const authCfg = extractAuth(yamlText);
  const headerName = authCfg.header_name || 'Authorization';
  const headerPrefix = authCfg.header_prefix || 'Bearer ';
  const usernameEnv = profile.credentials?.username_env || 'ESTACK_USER';
  const passwordEnv = profile.credentials?.password_env || 'ESTACK_PASS';

  const username = process.env[usernameEnv];
  const password = process.env[passwordEnv];
  if (!username || !password) {
    console.error(`❌ 请设置环境变量 ${usernameEnv} / ${passwordEnv}`);
    process.exit(1);
  }

  // 2. 存储: 捕获到的 API 列表
  const capturedApis = [];   // { method, url, pathname, requestBody, requestHeaders, timestamp, action: 'list'|'create'|'edit'|'lock'|'unlock'|'delete'|'detail' }
  let currentAction = 'list';  // 标记当前操作上下文

  function addCapture(method, url, body, headers, actionHint) {
    try {
      const u = new URL(url);
      capturedApis.push({
        method,
        url: url,
        pathname: u.pathname,
        searchParams: Object.fromEntries(u.searchParams),
        requestBody: body || null,
        requestHeaders: actionHint === 'headers-full' ? headers : undefined,
        timestamp: Date.now(),
        action: actionHint || currentAction,
      });
    } catch (e) {
      // non-URL 忽略
    }
  }

  // 3. 启动浏览器
  const browser = await chromium.launch({
    headless: false,
    args: ['--ignore-certificate-errors', '--disable-web-security', '--no-sandbox'],
  });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    locale: 'zh-CN',
  });
  const page = await context.newPage();

  // 4. 监听 API 请求 (request 阶段可获取 body)
  page.on('request', req => {
    const url = req.url();
    const method = req.method();
    // 只抓 estack API
    if (url.includes('/estack/api/estack/') && method !== 'OPTIONS') {
      try {
        const u = new URL(url);
        // 过滤静态资源 / 非 API 路径
        if (u.pathname.match(/\.(js|css|png|jpg|jpeg|gif|svg|woff|ttf|ico)$/i)) return;
        if (u.pathname.includes('/web/')) return;  // vue 前端路由
        addCapture(method, url, req.postData() || null, req.headers(), 'headers-full');
      } catch (e) { /* ignore */ }
    }
  });

  // 也监听 response (为了拿到错误响应中的提示)
  page.on('response', async resp => {
    const url = resp.url();
    if (url.includes('/estack/api/estack/') && url.includes('/users/')) {
      try {
        const ct = resp.headers()['content-type'] || '';
        if (ct.includes('json')) {
          const body = await resp.text();
          // 保存响应样本 (仅当成功且有内容)
          if (body && body.length > 10) {
            const pathname = new URL(url).pathname;
            if (!responseSamples[pathname]) responseSamples[pathname] = [];
            if (responseSamples[pathname].length < 3) {
              responseSamples[pathname].push({
                status: resp.status(),
                body: body.length > 2000 ? body.slice(0, 2000) + '...' : body,
                action: currentAction,
              });
            }
          }
        }
      } catch (e) { /* ignore */ }
    }
  });

  const responseSamples = {};

  // 5. 登录
  console.log(`\n🔄 正在打开登录页面: ${loginUrl}`);
  await page.goto(loginUrl, { waitUntil: 'networkidle', timeout: 45000 }).catch(() =>
    page.goto(loginUrl, { waitUntil: 'load', timeout: 45000 })
  );
  await sleep(4000);

  // 切换中文
  try {
    const hasLang = await page.evaluate(() => {
      const el = document.querySelector('.lang-style');
      return el && el.offsetWidth > 0;
    });
    if (hasLang) {
      await page.click('.lang-style');
      await sleep(1000);
      await page.evaluate(() => {
        const items = document.querySelectorAll('.el-dropdown-menu__item');
        for (const item of items) {
          if (item.textContent.trim() === '简体中文') { item.click(); return; }
        }
      });
      await sleep(1000);
      console.log('✅ 已切换到中文');
    }
  } catch (e) { console.log('⚠️ 语言切换略过'); }

  // 填用户名密码
  await page.fill('input[placeholder="用户名"]', username);
  await page.fill('input[placeholder="登录密码"]', password);
  await sleep(500);

  // 滑块验证 + 登录
  currentAction = 'login';
  console.log('\n🔄 执行滑块验证 + 登录...（请等待浏览器自动完成）');
  // 尝试找到认证按钮
  try {
    const authBtns = page.locator('button:has-text("认证"),button:has-text("完成认证")');
    if (await authBtns.first().isVisible({ timeout: 2000 })) {
      await authBtns.first().click();
      console.log('✅ 点击了认证按钮');
    }
  } catch (e) { console.log('⚠️ 认证按钮点击略过'); }
  await sleep(3000);

  // 检查是否需要滑块
  try {
    const sliderVisible = await page.evaluate(() => {
      const sv = document.querySelector('#slideVerify');
      return sv && sv.offsetWidth > 0;
    });
    if (sliderVisible) {
      console.log('⚠️ 检测到滑块验证，请手动完成滑块验证 + 点击登录按钮...');
      console.log('   浏览器保持打开，你手动操作。完成后按回车继续...');
      await new Promise(resolve => process.stdin.once('data', resolve));
    }
  } catch (e) { /* noop */ }

  // 尝试点击登录按钮
  try {
    const loginBtn = page.locator('button:has-text("登录")');
    if (await loginBtn.first().isVisible({ timeout: 2000 })) {
      await loginBtn.first().click();
      console.log('✅ 点击了登录按钮');
      await sleep(5000);
    }
  } catch (e) { console.log('⚠️ 登录按钮点击略过'); }

  // 检查是否已登录
  let loggedIn = false;
  for (let attempt = 0; attempt < 10; attempt++) {
    const token = await page.evaluate(() => localStorage.getItem('estackToken'));
    if (token) { loggedIn = true; console.log(`✅ 登录成功, token=${token.slice(0, 12)}...`); break; }
    // 检查 cookie
    const cookies = await context.cookies();
    const tCookie = cookies.find(c => c.name === 'accessToken');
    if (tCookie) { loggedIn = true; console.log(`✅ 登录成功 (cookie accessToken)`); break; }
    await sleep(2000);
  }

  if (!loggedIn) {
    console.log('⚠️ 等待手动登录... 完成登录后按回车继续');
    await new Promise(resolve => process.stdin.once('data', resolve));
    const token = await page.evaluate(() => localStorage.getItem('estackToken'));
    if (token) console.log(`✅ token: ${token.slice(0, 12)}...`);
  }

  // 6. 导航到用户管理页面
  console.log(`\n🔄 导航到用户管理页面: ${DEFAULT_URL}`);
  currentAction = 'page-load';
  await page.goto(DEFAULT_URL, { waitUntil: 'networkidle', timeout: 45000 }).catch(() =>
    page.goto(DEFAULT_URL, { waitUntil: 'load', timeout: 45000 })
  );
  await sleep(5000);
  console.log('✅ 用户管理页面已加载');

  // ---- 7. 逐一点击功能按钮，抓 API ----
  
  // 7a. 用户列表 (自动加载，已抓取)
  currentAction = 'list';
  await sleep(2000);
  console.log('\n📋 步骤1/5: 用户列表 —— 已自动加载');

  // 7b. 点击"新增用户"
  currentAction = 'create';
  console.log('\n➕ 步骤2/5: 点击"新增用户"...');
  try {
    const createBtn = page.locator('button:has-text("新增用户"),button:has-text("新建用户"),span:has-text("新增用户")');
    if (await createBtn.first().isVisible({ timeout: 3000 })) {
      await createBtn.first().click();
      console.log('✅ 点击了新增用户');
      await sleep(4000);
      
      // 关闭弹窗(如果需要)
      try {
        const cancelBtn = page.locator('button:has-text("取消"),button:has-text("关闭"),.el-dialog__close');
        if (await cancelBtn.first().isVisible({ timeout: 2000 })) {
          // 先尝试把弹窗里表单的数据抓下来
          const formFields = await page.evaluate(() => {
            const inputs = document.querySelectorAll('.el-dialog input, .el-dialog textarea');
            return Array.from(inputs).map(i => ({ name: i.name || i.placeholder || 'unknown', value: i.value }));
          });
          if (formFields.length > 0) {
            console.log('   弹窗表单字段:', formFields.map(f => f.name).join(', '));
          }
          await cancelBtn.first().click();
          await sleep(1500);
          console.log('✅ 已关闭新增弹窗');
        }
      } catch (e) { console.log('⚠️ 弹窗关闭略过'); }
    } else {
      console.log('⚠️ 未找到"新增用户"按钮');
    }
  } catch (e) { console.log('❌ 新增用户操作失败:', e.message); }

  // 7c. 找到列表第一行，点击编辑
  currentAction = 'edit';
  console.log('\n✏️ 步骤3/5: 点击"编辑"...');
  try {
    const editBtn = page.locator('button:has-text("编辑"),span:has-text("编辑")');
    if (await editBtn.first().isVisible({ timeout: 3000 })) {
      await editBtn.first().click();
      console.log('✅ 点击了编辑');
      await sleep(4000);
      try {
        const cancelBtn = page.locator('button:has-text("取消"),.el-dialog__close');
        if (await cancelBtn.first().isVisible({ timeout: 2000 })) {
          await cancelBtn.first().click();
          await sleep(1000);
        }
      } catch (e) {}
    } else {
      console.log('⚠️ 未找到"编辑"按钮');
    }
  } catch (e) { console.log('❌ 编辑操作失败:', e.message); }

  // 7d. 锁定/禁用
  currentAction = 'lock';
  console.log('\n🔒 步骤4/5: 点击"锁定/禁用"...');
  for (const label of ['锁定', '禁用', '停用']) {
    try {
      const lockBtn = page.locator(`span:has-text("${label}"),button:has-text("${label}")`);
      if (await lockBtn.first().isVisible({ timeout: 1500 })) {
        await lockBtn.first().click();
        await sleep(2000);
        // 确认对话框
        try {
          const confirmBtn = page.locator('button:has-text("确定"),button:has-text("确认")');
          if (await confirmBtn.first().isVisible({ timeout: 1500 })) {
            await confirmBtn.first().click();
            await sleep(2000);
          }
        } catch (e) {}
        console.log(`✅ 点击了"${label}"`);
        break;
      }
    } catch (e) { /* continue */ }
  }

  // 7e. 删除
  currentAction = 'delete';
  console.log('\n🗑️ 步骤5/5: 点击"删除"...');
  try {
    const delBtn = page.locator('span:has-text("删除"),button:has-text("删除")');
    if (await delBtn.first().isVisible({ timeout: 3000 })) {
      await delBtn.first().click();
      await sleep(2000);
      try {
        const confirmBtn = page.locator('button:has-text("确定"),button:has-text("确认")');
        if (await confirmBtn.first().isVisible({ timeout: 1500 })) {
          await confirmBtn.first().click();
          await sleep(2000);
        }
      } catch (e) {}
      console.log('✅ 点击了删除');
    } else {
      console.log('⚠️ 未找到"删除"按钮');
    }
  } catch (e) { console.log('❌ 删除操作失败:', e.message); }

  // 8. 保存结果
  console.log('\n' + '='.repeat(60));
  console.log(`捕获到 ${capturedApis.length} 个 API 请求`);

  // 按 action 分组统计
  const byAction = {};
  for (const api of capturedApis) {
    const act = api.action || 'unknown';
    if (!byAction[act]) byAction[act] = [];
    byAction[act].push(api);
  }
  for (const [act, apis] of Object.entries(byAction)) {
    console.log(`  ${act}: ${apis.length} 条`);
    // 去重路径
    const paths = new Set(apis.map(a => a.method + ' ' + a.pathname));
    for (const p of paths) console.log(`    ${p}`);
  }

  // 去重合并 + 提取 body 结构
  const uniqueEndpoints = {};
  for (const api of capturedApis) {
    const key = api.method + ' ' + api.pathname;
    if (!uniqueEndpoints[key]) {
      uniqueEndpoints[key] = { ...api };
    }
  }

  const output = {
    captureTime: new Date().toISOString(),
    targetUrl: DEFAULT_URL,
    endpointCount: Object.keys(uniqueEndpoints).length,
    apiCount: capturedApis.length,
    endpoints: Object.values(uniqueEndpoints).map(e => ({
      method: e.method,
      pathname: e.pathname,
      searchParams: e.searchParams && Object.keys(e.searchParams).length > 0 ? e.searchParams : undefined,
      requestBody: e.requestBody || undefined,
      action: e.action,
    })),
    responseSamples,
  };

  fs.mkdirSync(path.dirname(OUTPUT_PATH), { recursive: true });
  fs.writeFileSync(OUTPUT_PATH, JSON.stringify(output, null, 2), 'utf-8');
  console.log(`\n✅ 已保存到: ${OUTPUT_PATH}`);
  console.log(`   共 ${output.endpointCount} 个唯一端点`);

  // 9. 清理 (Playwright 不会自动退出)
  // await browser.close();
  console.log('\n⚠️ 浏览器保持打开供你检查。完成后手动关闭。');
  console.log('按 Ctrl+C 退出脚本');
}

main().catch(e => {
  console.error('❌ 脚本异常:', e);
  process.exit(1);
});
