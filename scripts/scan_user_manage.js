/**
 * scan_user_manage.js — 扫描用户管理模块，捕获完整 API
 *
 * 用法: cd D:\Mobile\API_AI_test && node scripts/scan_user_manage.js
 */
const path = require('path');
const fs = require('fs');
const scanner = require('./api_scanner');

const BASE_URL = 'https://console-estack-syyhb.cmecloud.cn';
const TARGET_URL = BASE_URL + '/estack/web/estack/user-center/user-manage/user';
const COOKIE_FILE = path.join(__dirname, '..', '..', 'EcsCloud', 'output', 'config', 'cookies.json');
const OUTPUT_FILE = path.join(__dirname, '..', 'projects', 'estack', 'kb', 'user_manage_discovered.json');

async function main() {
  console.log('='.repeat(70));
  console.log('  扫描用户管理模块 → 捕获 API');
  console.log('='.repeat(70));

  const result = await scanner.scan({
    targetUrl: TARGET_URL,
    cookieFile: COOKIE_FILE,
    loginUrl: BASE_URL + '/estack/web/estack/login',
    username: 'estack-yy',
    password: 'R@9eDuck$!mpleM00n',
    headless: false,
    clickDelay: 6000,
  });

  // 保存结果
  fs.mkdirSync(path.dirname(OUTPUT_FILE), { recursive: true });
  fs.writeFileSync(OUTPUT_FILE, JSON.stringify(result, null, 2), 'utf-8');

  console.log(`\n📊 扫描结果:`);
  console.log(`  总 API 请求: ${result.totalCalls}`);
  console.log(`  去重端点: ${result.uniqueEndpoints}`);

  console.log('\n📋 按分类:');
  const sortedCats = Object.entries(result.byCategory).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [cat, eps] of sortedCats) {
    console.log(`  ── ${cat} (${eps.length}) ──`);
    for (const ep of eps) {
      console.log(`    ${ep.method.padEnd(6)} ${ep.pathname}`);
      if (ep.bodies.length > 0) {
        ep.bodies.slice(0, 1).forEach(b => console.log(`         body: ${b.length > 200 ? b.slice(0,200)+'...' : b}`));
      }
    }
  }

  console.log(`\n✅ 已保存到: ${OUTPUT_FILE}`);
  console.log('\n⚠️ 浏览器可能需要手动关闭。');
}

main().catch(e => { console.error('❌', e); process.exit(1); });
