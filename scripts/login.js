/**
 * EStack 私有云登录自动化脚本
 * 技术栈: Node.js v20 + Playwright + Python OpenCV
 *
 * 登录流程:
 *   1. 打开登录页 + 关闭 Cookie 同意弹窗
 *   2. 定位【可见】的 LoginContain 实例，设置 loginForm 数据
 *   3. 触发 ElForm.validate() 表单验证
 *   4. 点击"点击完成认证"（多策略，首选可见实例内的文本节点）
 *   5. 等待滑块渲染（可见实例天然布局正确，无需手术刀；异常环境才降级修复）
 *   6. 用【接口原始素材】(backImage/slidingImage) + alpha mask 识别缺口
 *   7. Playwright mouse API 人类轨迹拖拽 + containerSuccess 信号校验
 *   8. 点击登录按钮
 *
 * ★ 关键根因（2026-08-11 定位）:
 *   登录页同时存在 2 套 LoginContain / #slideVerify 实例：
 *     - 隐藏实例：父链 .zong-login-page[display:none] → 全链 0×0
 *     - 可见实例：父链 .login-page[display:block]     → 用户实际看到的
 *   旧代码 findLC 递归取"第一个"，恰好命中隐藏实例，导致：
 *     表单填了但页面不变 / 点认证后表单不消失 / 滑块塌缩到左下角 / 报"请输入用户名密码"。
 *   修复：findVisibleLC() 从"可见 input"反查组件，旧逻辑保留为兜底。
 *
 * ★ 缺口识别（2026-08-11 修复）:
 *   旧方案 canvas.toDataURL('image/jpeg') 会丢弃 alpha 通道，拼图块透明区变成黑/白块，
 *   模板匹配被严重干扰（12 算法结果分散在 79/180/238/260，置信度 0.07~0.55）。
 *   新方案直接拦截 /images/pictures-verification 接口，拿服务端原始素材：
 *     slidingImage = 60×60 PNG（带真 alpha 的纯净拼图块）
 *     backImage    = 380×260 JPEG（含缺口的完整背景图）
 *   用 alpha 作 matchTemplate 的 mask → 置信度 0.97+，多算法一致。
 *
 * 使用方式:
 *   const { loginToEStack } = require('./scripts/login.js');
 *   await loginToEStack(page, 'username', 'password');
 *   或直接运行: node scripts/login.js
 *
 * 设计原则: 不删除任何已验证的旧逻辑，全部保留为兜底；新逻辑作为首选并验证有效后才覆盖。
 */

const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Python 环境配置
const PYTHON_PATH = 'C:\\Python311\\python.exe';
const PY_SCRIPT = path.join(__dirname, 'identify_gap.py');
const SCREENSHOT_DIR = path.join(__dirname, '..', 'output', 'screenshots');

// ===== 关键：找可见 LoginContain 的源码（注入到浏览器上下文）=====
// 假说：页面同时存在 2 个 LoginContain 实例（一个在 .zong-login-page[display:none] 内，一个在
// .login-page[display:block] 内）。旧 findLC 递归取第一个，命中隐藏实例，导致所有点击 / 填表对
// 实际可见表单无效 → 出现 "表单不消失 + 滑块浮在左下角 + 报"请输入用户名密码"" 这一整套异常。
// 策略顺序：
//   1. 从可见的 .用户名/密码 input 往上找最近的 LoginContain
//   2. 遍历所有 LoginContain，选子树中含可见 input 的
//   3. 排除 .zong-login-page 内的隐藏实例
//   4. 旧逻辑（递归第一个）兜底
const FIND_LC_SRC = `
function findVisibleLC() {
  function isVis(el) { if (!el || !el.getBoundingClientRect) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }
  function upToLC(vm) { let c = vm; let g = 0; while (c && g++ < 30) { if (c.$options && c.$options.name === 'LoginContain') return c; c = c.$parent; } return null; }
  function findAllLC() {
    const app = document.querySelector('#app') && document.querySelector('#app').__vue__;
    const all = [];
    (function walk(c, d) { if (!c || d > 40) return; if (c.$options && c.$options.name === 'LoginContain') all.push(c); (c.$children || []).forEach(function (ch) { walk(ch, d + 1); }); })(app, 0);
    return all;
  }
  // 1. 从可见 input 反查
  var inputs = Array.prototype.slice.call(document.querySelectorAll('input[placeholder="用户名"], input[placeholder="登录密码"]'));
  var visInput = inputs.filter(isVis)[0];
  if (visInput) {
    var node = visInput, guard = 0;
    while (node && guard++ < 30) {
      if (node.__vue__) { var lc = upToLC(node.__vue__); if (lc) return { lc: lc, via: 'visible-input' }; }
      node = node.parentElement;
    }
  }
  // 2. 含可见 input 的子树
  var all = findAllLC();
  for (var i = 0; i < all.length; i++) {
    var el = all[i].$el;
    if (el && el.querySelector) {
      var inp = Array.prototype.slice.call(el.querySelectorAll('input')).filter(isVis)[0];
      if (inp) return { lc: all[i], via: 'subtree-visible-input' };
    }
  }
  // 3. 排除 .zong-login-page
  for (var j = 0; j < all.length; j++) {
    var e2 = all[j].$el;
    if (e2 && e2.closest && !e2.closest('.zong-login-page')) return { lc: all[j], via: 'not-in-zong-login-page' };
  }
  // 4. 旧逻辑兜底（保留兼容）
  return all[0] ? { lc: all[0], via: 'legacy-first' } : { lc: null, via: 'none' };
}
function findLC_legacy(c) {
  if (!c) return null;
  if (c.$options && c.$options.name === 'LoginContain') return c;
  for (var i = 0; i < (c.$children || []).length; i++) { var r = findLC_legacy(c.$children[i]); if (r) return r; }
  return null;
}
function findLC_smart() {
  var r = findVisibleLC();
  return r.lc || findLC_legacy(document.querySelector('#app') && document.querySelector('#app').__vue__);
}
function findElForm_smart(comp) {
  if (!comp) return null;
  if (comp.$options && comp.$options.name === 'ElForm' && comp.fields) return comp;
  for (var i = 0; i < (comp.$children || []).length; i++) { var r = findElForm_smart(comp.$children[i]); if (r) return r; }
  return null;
}
function findClickBtnSmart(comp, text) {
  if (!comp) return null;
  if (comp.$options && comp.$options.name === 'ElButton' && comp.$el && (comp.$el.textContent || '').indexOf(text) >= 0) return comp;
  for (var i = 0; i < (comp.$children || []).length; i++) { var r = findClickBtnSmart(comp.$children[i], text); if (r) return r; }
  return null;
}
function findAuthTextNode(lc) {
  var el = lc.$el;
  if (!el) return null;
  var cands = Array.prototype.slice.call(el.querySelectorAll('*')).filter(function (n) {
    var t = (n.textContent || '').trim(); return t === '点击完成认证' && n.children.length === 0;
  });
  if (cands[0]) return cands[0];
  var all = Array.prototype.slice.call(el.querySelectorAll('div,span,button,a')).filter(function (n) {
    return (n.textContent || '').trim().indexOf('点击完成认证') >= 0;
  });
  return all[all.length - 1] || null;
}
`;

/**
 * 核心登录函数 - 导出让外部 UI 自动化调用
 * @param {object} page - Playwright Page 对象
 * @param {string} username - 用户名
 * @param {string} password - 密码
 * @param {object} options - 可选参数
 * @returns {Promise<boolean>} 登录是否成功
 */
async function loginToEStack(page, username, password, options = {}) {
  const loginUrl = options.loginUrl || 'https://console-estack.dw.cmecloud.cn/estack/web/estack/login';
  const successUrlKeyword = options.successUrlKeyword || '/portal';
  const screenshotDir = options.screenshotDir || SCREENSHOT_DIR;
  const pythonPath = options.pythonPath || PYTHON_PATH;
  const pyScript = options.pyScript || PY_SCRIPT;

  if (!fs.existsSync(screenshotDir)) fs.mkdirSync(screenshotDir, { recursive: true });

  // ===== 0. 拦截验证码接口，抓服务端原始素材（缺口识别的关键数据源）=====
  // slidingImage: 60×60 PNG 带真 alpha；backImage: 380×260 JPEG 含缺口
  // 比 canvas.toDataURL 干净得多（canvas 那份是合成缩放后的，且 JPEG 会丢 alpha）
  let captchaMaterial = null;
  const onCaptchaResp = async (resp) => {
    if (!/pictures-verification/.test(resp.url())) return;
    try {
      const j = await resp.json();
      if (j && j.entity && j.entity.backImage) captchaMaterial = j.entity;
    } catch (e) { /* ignore */ }
  };
  page.on('response', onCaptchaResp);

  // ===== 1. 打开登录页 =====
  console.log('[登录] 1. 打开登录页面');
  await page.goto(loginUrl, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(5000);

  // ===== 1.5. 关闭底部 Cookie 同意弹窗 =====
  // 根因：z=9999 fixed 的 .cookie-banner 会遮挡「点击完成认证 / 登录」按钮。
  // 即使本会话已接受过，重新 goto 后偶发仍出现，且它会拦截 click 导致后续步骤假失败。
  await page.evaluate(() => {
    const kill = el => el && el.style.setProperty('display', 'none', 'important');
    for (const sel of ['.cookie-banner', '#cookie-banner', '[class*=cookie-consent]']) {
      document.querySelectorAll(sel).forEach(kill);
    }
    for (const el of document.querySelectorAll('div,section,aside')) {
      const cs = getComputedStyle(el);
      if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
      const r = el.getBoundingClientRect();
      if (r.top < window.innerHeight - 200) continue;
      const t = el.innerText || '';
      if (/Cookie/i.test(t) && /(全部接受|接受|同意|Allow)/i.test(t)) kill(el);
    }
  }).catch(() => {});
  await page.locator('.cookie-banner').filter({ hasText: '全部接受' }).first()
    .click({ timeout: 1500 }).catch(() => {});
  await page.waitForTimeout(500);

  // ===== 2. 填写表单 =====
  // 多实例环境下，找可见 LoginContain（见 findVisibleLC 注释）。
  console.log('[登录] 2. 填写用户名密码');
  await page.evaluate(({ src, u, p }) => {
    eval(src);
    const pick = findLC_smart();
    if (pick) { pick.loginForm.username = u; pick.loginForm.password = p; }
    // 旧逻辑兜底：直接 dispatch 同步 input（兼容 v-model 链未挂载的环境）
    document.querySelectorAll('input').forEach(inp => {
      if (inp.placeholder === '用户名') { inp.value = u; inp.dispatchEvent(new Event('input', { bubbles: true })); }
      if (inp.placeholder === '登录密码') { inp.value = p; inp.dispatchEvent(new Event('input', { bubbles: true })); }
    });
  }, { src: FIND_LC_SRC, u: username, p: password });
  await page.waitForTimeout(1000);

  // ===== 3. 触发表单验证 =====
  console.log('[登录] 3. 触发表单验证');
  await page.evaluate(() => {
    document.querySelectorAll('input').forEach(inp => inp.dispatchEvent(new Event('blur', { bubbles: true })));
  });
  await page.evaluate(async ({ src }) => {
    eval(src);
    const lc = findLC_smart();
    const ef = findElForm_smart(lc);
    if (ef && typeof ef.validate === 'function') await new Promise(r => ef.validate(valid => r(valid)));
  }, { src: FIND_LC_SRC });

  // ===== 4. 点击"点击完成认证" — 多策略兼容不同环境 =====
  // 策略A: Playwright 原生 .click() (trusted 事件，最接近真人点击) — 首选
  // 策略B: 原生 dispatchEvent (避免某些环境下 .click() 引发路由跳转) — 备选
  // 策略C: Vue ElButton.handleClick() (旧逻辑，最保守) — 最后兜底
  console.log('[登录] 4. 点击"点击完成认证"');

  const isVerifySet = () => page.evaluate(({ src }) => {
    eval(src);
    const lc = findLC_smart();
    return !!(lc && lc.isVerify === true);
  }, { src: FIND_LC_SRC });

  let clickStrategy = null;

  // 策略 A: Playwright 原生 .click() — 首选
  try {
    const btn = page.locator('button:has-text("点击完成认证")').first();
    if (await btn.isVisible({ timeout: 1500 }).catch(() => false)) {
      await btn.click({ timeout: 5000, force: true }).catch(() => {});
      await page.waitForTimeout(800);
      if (await isVerifySet()) clickStrategy = 'A:playwright_click';
    }
  } catch (e) { /* fallthrough */ }

  // 策略 B: 找可见 LoginContain 内的"点击完成认证"文本节点，dispatchEvent（关键 — 多实例环境只有这条能切到对的实例）
  if (!clickStrategy) {
    try {
      const dispatched = await page.evaluate(({ src }) => {
        eval(src);
        const lc = findLC_smart();
        if (!lc) return { ok: false, reason: 'no_lc' };
        const target = findAuthTextNode(lc);
        if (!target) { return { ok: false, reason: 'no_text_node' }; }
        target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
        return { ok: true, via: 'visible-lc-text-node' };
      }, { src: FIND_LC_SRC });
      if (dispatched.ok) {
        await page.waitForTimeout(800);
        if (await isVerifySet()) clickStrategy = 'B:visible_lc_text';
      } else {
        console.log(`[登录]   策略B 无目标: ${dispatched.reason}`);
      }
    } catch (e) { /* fallthrough */ }
  }

  // 策略 C: 原生 dispatchEvent（找任意 ElButton 触发 click — 旧逻辑兜底）
  if (!clickStrategy) {
    try {
      const dispatched = await page.evaluate(({ src }) => {
        eval(src);
        const lc = findLC_smart();
        const btn = findClickBtnSmart(lc, '点击完成认证');
        if (btn && btn.$el) { btn.$el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); return true; }
        return false;
      }, { src: FIND_LC_SRC });
      if (dispatched) {
        await page.waitForTimeout(800);
        if (await isVerifySet()) clickStrategy = 'C:dispatchEvent';
      }
    } catch (e) { /* fallthrough */ }
  }

  // 策略 D: Vue ElButton.handleClick() — 保留旧逻辑兜底
  if (!clickStrategy) {
    try {
      const ok = await page.evaluate(async ({ src }) => {
        eval(src);
        const lc = findLC_smart();
        const btn = findClickBtnSmart(lc, '点击完成认证');
        if (btn && typeof btn.handleClick === 'function') { await btn.handleClick(); return true; }
        return false;
      }, { src: FIND_LC_SRC });
      if (ok) {
        await page.waitForTimeout(800);
        if (await isVerifySet()) clickStrategy = 'D:handleClick';
      }
    } catch (e) { /* fallthrough */ }
  }

  if (!clickStrategy) { console.error('[登录] ✗ 所有点击策略都失败，isVerify 未置 true'); return false; }
  console.log(`[登录]   ✓ 视图已切换 (策略 ${clickStrategy})`);
  await page.waitForTimeout(2000);  // 给滑块组件异步加载图片的时间

  // ===== 5. 等待滑块渲染 =====
  // ★ 可见实例的滑块天然布局正确（380×319，坐标可用），无需任何手术刀。
  //   旧版"appendChild 到 body + fixed 居中"是在给隐藏实例治病，反而把 handle 坐标
  //   顶出视口（y=1015）导致拖拽全废。这里只在真异常时才降级到旧修复逻辑。
  console.log('[登录] 5. 等待滑块渲染');

  const readSlider = () => page.evaluate(({ src }) => {
    eval(src);
    const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
    const sv = svs.find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    if (!sv) return { ok: false, reason: 'no_visible_slideVerify', total: svs.length };
    const vm = sv.__vue__;
    const bg = sv.querySelector('canvas:not(.slide-verify-block)');
    const handle = sv.querySelector('.slide-verify-slider-mask-item');
    const track = sv.querySelector('.slide-verify-slider');
    if (!bg || !handle || !track) return { ok: false, reason: 'missing_parts' };
    const rh = handle.getBoundingClientRect();
    const rt = track.getBoundingClientRect();
    const rb = bg.getBoundingClientRect();
    if (rh.width < 1 || rb.width < 1) return { ok: false, reason: 'zero_bbox' };
    return {
      ok: true,
      L: vm ? vm.L : null, blockyY: vm ? vm.blockyY : null, capcode: vm ? vm.capcode : null,
      dispW: rb.width, dispH: rb.height,
      handle: { x: rh.x, y: rh.y, w: rh.width, h: rh.height },
      trackW: rt.width,
    };
  }, { src: FIND_LC_SRC });

  let geo = null;
  for (let i = 0; i < 20; i++) {
    geo = await readSlider();
    if (geo.ok) break;
    await page.waitForTimeout(500);
  }

  if (!geo || !geo.ok) {
    // ---- 降级：旧的手术刀修复逻辑（保留兼容只有单实例、且被 CSS 夹掉的环境）----
    console.log(`[登录]   ⚠ 滑块未就绪(${geo && geo.reason})，降级到手术刀修复`);
    await page.evaluate(() => {
      const sv = document.querySelector('#slideVerify');
      if (!sv) return;
      document.body.appendChild(sv);
      const vw = window.innerWidth, vh = window.innerHeight, W = 380, H = 260;
      sv.style.setProperty('position', 'fixed', 'important');
      sv.style.setProperty('left', ((vw - W) / 2) + 'px', 'important');
      sv.style.setProperty('top', ((vh - H) / 2) + 'px', 'important');
      sv.style.setProperty('width', W + 'px', 'important');
      sv.style.setProperty('z-index', '99999', 'important');
      sv.style.setProperty('background', '#fff', 'important');
      sv.style.setProperty('display', 'block', 'important');
      sv.style.setProperty('visibility', 'visible', 'important');
      sv.style.setProperty('opacity', '1', 'important');
    }).catch(() => {});
    await page.waitForTimeout(1000);
    // 旧兜底：逐级放开父链 display
    await page.evaluate(() => {
      const sv = document.querySelector('#slideVerify');
      if (!sv) return;
      let p = sv.parentElement;
      while (p && p !== document.body) {
        p.style.display = 'block'; p.style.visibility = 'visible'; p.style.opacity = '1';
        p = p.parentElement;
      }
      sv.style.display = 'block'; sv.style.visibility = 'visible'; sv.style.opacity = '1';
    }).catch(() => {});
    await page.waitForTimeout(600);
    geo = await readSlider();
    if (!geo.ok) { console.error(`[登录] ✗ 滑块始终不可用 (${geo.reason})`); return false; }
  }
  console.log(`[登录]   ✓ 滑块就绪 ${Math.round(geo.dispW)}×${Math.round(geo.dispH)} handle@(${Math.round(geo.handle.x)},${Math.round(geo.handle.y)}) track=${Math.round(geo.trackW)}`);

  // ===== 6. 识别缺口位置 =====
  console.log('[登录] 6. 识别滑块缺口位置');

  // 6a. 首选：接口原始素材 + alpha mask（置信度 0.97+，已验证）
  let gapLoc = 0, gapSource = '', bgW = geo.dispW, tplW = 0;
  const rawPy = path.join(__dirname, 'identify_gap_raw.py');

  const identifyFromMaterial = () => {
    if (!captchaMaterial || !captchaMaterial.backImage || !captchaMaterial.slidingImage) return null;
    try {
      const backPath = path.join(screenshotDir, 'cap_back.jpg');
      const slidePath = path.join(screenshotDir, 'cap_slide.png');
      fs.writeFileSync(backPath, Buffer.from(captchaMaterial.backImage.replace(/^data:[^,]+,/, ''), 'base64'));
      fs.writeFileSync(slidePath, Buffer.from(captchaMaterial.slidingImage.replace(/^data:[^,]+,/, ''), 'base64'));
      const out = execSync(`"${pythonPath}" "${rawPy}" "${backPath}" "${slidePath}"`, { timeout: 30000, encoding: 'utf-8' });
      const line = out.trim().split('\n').filter(l => l.trim().startsWith('{')).pop();
      return line ? JSON.parse(line) : null;
    } catch (e) {
      console.log(`[登录]   ⚠ 原始素材识别异常: ${e.message.slice(0, 120)}`);
      return null;
    }
  };

  if (fs.existsSync(rawPy)) {
    const r = identifyFromMaterial();
    if (r && r.gap_location > 0) {
      gapLoc = r.gap_location; gapSource = `raw:${r.method}(${r.votes}票)`;
      bgW = r.bg_w; tplW = r.tpl_w;
      console.log(`[登录]   缺口 ${gapLoc}px | 置信度 ${r.confidence} | ${gapSource}`);
    }
  }

  // 6b. 兜底：前端组件暴露的 gapX（部分环境有）— 保留旧逻辑
  if (gapLoc <= 0) {
    const gapFromFrontend = await page.evaluate(() => {
      const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
      const sv = svs.find(e => { const r = e.getBoundingClientRect(); return r.width > 0; }) || svs[0];
      if (sv && sv.__vue__) {
        const v = sv.__vue__;
        const keys = ['gapX', 'gap_x', 'gapLeft', 'gap_left', 'offsetX', 'offset_x'];
        for (const k of keys) if (typeof v[k] === 'number' && v[k] > 0) return { source: 'vue.' + k, value: v[k] };
      }
      return null;
    });
    if (gapFromFrontend) { gapLoc = gapFromFrontend.value; gapSource = gapFromFrontend.source; }
  }

  // 6c. 兜底：canvas 导出 + 旧 OpenCV 脚本（保留兼容无接口素材的环境）
  if (gapLoc <= 0) {
    console.log('[登录]   降级到 canvas 导出 + OpenCV');
    const canvasData = await page.evaluate(() => {
      const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
      const sv = svs.find(e => { const r = e.getBoundingClientRect(); return r.width > 0; }) || svs[0];
      const bg = sv && sv.querySelector('canvas:not(.slide-verify-block)');
      const gap = sv && sv.querySelector('canvas.slide-verify-block');
      return { bgUrl: bg && bg.toDataURL('image/png'), gapUrl: gap && gap.toDataURL('image/png') };
    });
    const alphaPy = path.join(__dirname, 'identify_gap_alpha.py');
    const usePy = fs.existsSync(alphaPy) ? alphaPy : pyScript;
    if (canvasData.bgUrl && canvasData.gapUrl) {
      try {
        const bgPath = path.join(screenshotDir, 'bg.png'), gapPath = path.join(screenshotDir, 'gap.png');
        fs.writeFileSync(bgPath, Buffer.from(canvasData.bgUrl.split(',')[1], 'base64'));
        fs.writeFileSync(gapPath, Buffer.from(canvasData.gapUrl.split(',')[1], 'base64'));
        const pyOut = execSync(`"${pythonPath}" "${usePy}" "${bgPath}" "${gapPath}"`, { timeout: 30000, encoding: 'utf-8' });
        const line = pyOut.trim().split('\n').filter(l => l.trim().startsWith('{')).pop();
        if (line) {
          const r = JSON.parse(line);
          if (r.gap_location > 0) { gapLoc = r.gap_location; gapSource = 'canvas_opencv'; }
        }
      } catch (e) { console.log(`[登录]   ⚠ OpenCV 兜底异常: ${e.message.slice(0, 100)}`); }
    }
  }

  if (gapLoc <= 0) { console.error('[登录] ✗ 无法识别缺口位置'); return false; }

  // 换算：原图坐标 -> 滑轨拖拽距离
  const scale = geo.dispW / (bgW || geo.dispW);
  const gapDisp = gapLoc * scale;
  const blockDispW = (tplW || 41) * scale;
  const maxSlide = geo.trackW - geo.handle.w;
  const maxBlock = geo.dispW - blockDispW;
  const baseDist = maxBlock > 0 ? gapDisp * (maxSlide / maxBlock) : gapDisp;
  console.log(`[登录]   换算: scale=${scale.toFixed(3)} 显示缺口=${gapDisp.toFixed(1)} 拖拽距离=${baseDist.toFixed(1)}px`);

  // ===== 7. 人类轨迹拖拽 + 校验 =====
  const box = geo.handle;
  async function doDrag(distance) {
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
    await page.mouse.move(cx, cy, { steps: 6 });
    await page.waitForTimeout(180 + Math.floor(Math.random() * 120));
    await page.mouse.down();
    await page.waitForTimeout(120 + Math.floor(Math.random() * 80));
    const totalSteps = 28 + Math.floor(Math.random() * 10);
    for (let s = 1; s <= totalSteps; s++) {
      const progress = s / totalSteps;
      const eased = progress < 0.5 ? 4 * progress ** 3 : 1 - Math.pow(-2 * progress + 2, 3) / 2;
      const yJitter = Math.sin(progress * Math.PI * (2 + Math.random())) * (1.2 + Math.random() * 1.5);
      await page.mouse.move(cx + eased * distance, cy + yJitter);
      const delay = progress < 0.12 ? 22 + Math.random() * 14
        : progress > 0.88 ? 26 + Math.random() * 16
        : 7 + Math.random() * 9;
      await page.waitForTimeout(delay);
    }
    const overshoot = 1.5 + Math.random() * 1.5;
    await page.mouse.move(cx + distance + overshoot, cy + (Math.random() - 0.5) * 1.2);
    await page.waitForTimeout(70 + Math.random() * 40);
    await page.mouse.move(cx + distance, cy + (Math.random() - 0.5) * 0.6);
    await page.waitForTimeout(140 + Math.random() * 80);
    await page.mouse.up();
  }

  // 多信号校验：组件 containerSuccess / 组件消失（成功后销毁）/ 文案 / 失败标志
  async function checkVerify() {
    return await page.evaluate(() => {
      const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
      const txt = document.body.innerText || '';
      const states = svs.map(e => {
        const vm = e.__vue__;
        return vm ? { s: !!vm.containerSuccess, f: !!vm.containerFail } : { s: false, f: false };
      });
      if (states.some(x => x.s)) return { ok: true, reason: 'containerSuccess' };
      if (/校验成功|验证成功|通过验证|认证成功/.test(txt)) return { ok: true, reason: 'text_success' };
      // 滑块组件整体消失 = 验证通过后被销毁
      if (svs.length === 0) return { ok: true, reason: 'slider_destroyed' };
      if (states.some(x => x.f)) return { ok: false, reason: 'fail_toast' };
      if (/验证失败|校验失败/.test(txt)) return { ok: false, reason: 'fail_toast' };
      return { ok: false, reason: 'pending' };
    });
  }
  async function pollVerify(maxMs = 6000) {
    const deadline = Date.now() + maxMs;
    while (Date.now() < deadline) {
      const r = await checkVerify();
      if (r.ok || r.reason === 'fail_toast') return r;
      await page.waitForTimeout(200);
    }
    return { ok: false, reason: 'timeout' };
  }

  console.log('[登录] 7. 拖拽滑块');
  await doDrag(baseDist);
  let verifyResult = await pollVerify(6000);
  let verifyOk = verifyResult.ok;
  console.log(`[登录]   首次校验: ${verifyOk ? '✓' : '✗'} (${verifyResult.reason})`);

  // 失败重试：刷新验证码 -> 重新识别 -> 重拖（最多 4 轮）
  for (let round = 1; round <= 4 && !verifyOk; round++) {
    console.log(`[登录]   第 ${round} 轮重试...`);
    captchaMaterial = null;
    // 触发刷新（点刷新按钮 / 重新点认证）
    await page.evaluate(({ src }) => {
      eval(src);
      const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
      const sv = svs.find(e => { const r = e.getBoundingClientRect(); return r.width > 0; });
      if (sv) {
        const btn = sv.querySelector('.slide-verify-refresh-icon, [class*=refresh]');
        if (btn) { btn.dispatchEvent(new MouseEvent('click', { bubbles: true })); return; }
        if (sv.__vue__ && typeof sv.__vue__.reset === 'function') { sv.__vue__.reset(); return; }
      }
      const lc = findLC_smart();
      const t = lc && findAuthTextNode(lc);
      if (t) t.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
    }, { src: FIND_LC_SRC }).catch(() => {});
    await page.waitForTimeout(2500);

    const g2 = await readSlider();
    if (!g2.ok) { console.log(`[登录]   重试轮滑块不可用 (${g2.reason})`); continue; }
    const r2 = identifyFromMaterial();
    if (!r2 || r2.gap_location <= 0) { console.log('[登录]   重试轮识别失败'); continue; }
    const sc2 = g2.dispW / (r2.bg_w || g2.dispW);
    const mb2 = g2.dispW - (r2.tpl_w || 41) * sc2;
    const ms2 = g2.trackW - g2.handle.w;
    const d2 = mb2 > 0 ? r2.gap_location * sc2 * (ms2 / mb2) : r2.gap_location * sc2;
    box.x = g2.handle.x; box.y = g2.handle.y; box.w = g2.handle.w; box.h = g2.handle.h;
    console.log(`[登录]   重试识别 缺口=${r2.gap_location} 距离=${d2.toFixed(1)}px (conf ${r2.confidence})`);
    await doDrag(d2);
    const rr = await pollVerify(6000);
    if (rr.ok) { console.log(`[登录]   第 ${round} 轮: ✓ (${rr.reason})`); verifyOk = true; break; }
    console.log(`[登录]   第 ${round} 轮: ✗ (${rr.reason})`);
  }

  if (!verifyOk) { console.error('[登录] ✗ 滑块验证失败'); return false; }

  // ===== 8. 点击登录 =====
  console.log('[登录] 8. 点击登录按钮');
  await page.evaluate(() => {
    // 首选可见按钮（多实例环境下 XPath [1] 会命中隐藏实例）
    const vis = Array.prototype.slice.call(document.querySelectorAll('button')).filter(b => {
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (b.textContent || '').trim() === '登录';
    });
    if (vis[0]) { vis[0].click(); return; }
    const btn = document.evaluate(
      '(//div[@class="el-form-item__content"]//span[contains(text(),"登录")]/parent::button)[1]',
      document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
    ).singleNodeValue;
    if (btn) btn.click();
  });
  await page.waitForTimeout(3000);

  // 关闭密码提醒弹窗
  const cancelBtn = page.locator('//div[@aria-label="密码提醒"]//button/span[contains(.,"取 消")]');
  if (await cancelBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    console.log('[登录]   关闭密码提醒');
    await cancelBtn.click();
    await page.waitForTimeout(1000);
    await page.evaluate(() => {
      const vis = Array.prototype.slice.call(document.querySelectorAll('button')).filter(b => {
        const r = b.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && (b.textContent || '').trim() === '登录';
      });
      if (vis[0]) { vis[0].click(); return; }
      const btn = document.evaluate(
        '(//div[@class="el-form-item__content"]//span[contains(text(),"登录")]/parent::button)[1]',
        document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
      ).singleNodeValue;
      if (btn) btn.click();
    });
  }

  await page.waitForTimeout(5000);
  const url = page.url();
  const success = url.includes(successUrlKeyword);
  console.log(`[登录] 9. 结果: ${success ? '✓ 成功' : '✗ 失败'} (${url})`);
  return success;
}

// ===== 独立运行入口 =====
if (require.main === module) {
  (async () => {
    const browser = await chromium.launch({
      headless: false,
      args: [
        '--ignore-certificate-errors', '--disable-web-security',
        '--disable-blink-features=AutomationControlled', '--start-maximized',
        '--no-sandbox', '--disable-setuid-sandbox'
      ]
    });
    const context = await browser.newContext({
      viewport: { width: 1920, height: 1080 },
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    });
    const page = await context.newPage();
    try {
      const success = await loginToEStack(page, process.env.LOGIN_USER || 'estack-tenant-admin-0808', process.env.LOGIN_PASS || 'R@9eDuck$!mpleM00n');
      console.log(success ? '\n★★★ 登录成功！★★★' : '\n✗ 登录失败');
      await page.waitForTimeout(5000);
    } catch (e) {
      console.error('错误:', e.message);
    }
    await browser.close();
  })();
}

/**
 * 登录并保存 cookies 到文件，供后续 headless 用例使用
 */
async function loginAndSaveCookies(username, password, options = {}) {
  const loginUrl = options.loginUrl || 'https://console-estack.dw.cmecloud.cn/estack/web/estack/login';
  const cookieFile = options.cookieFile || path.join(__dirname, '..', 'output', 'config', 'cookies.json');
  // 自适应无头：后台批跑(HEADLESS=1)用无头浏览器即可完成滑块登录
  // （缺口识别走接口原始素材 + Python OpenCV，拖拽是合成鼠标事件，无需可见窗口渲染）。
  // 可见模式(HEADLESS≠1，如 run_all_with_auth 单窗口)仍用有头浏览器，便于人工观察。
  const headless = process.env.HEADLESS === '1';
  console.log(`[登录启动] 打开浏览器（${headless ? '无头' : '有头'}模式）...`);
  const browser = await chromium.launch({
    headless,
    args: [
      '--ignore-certificate-errors', '--disable-web-security',
      '--disable-blink-features=AutomationControlled', '--start-maximized',
      '--no-sandbox', '--disable-setuid-sandbox'
    ]
  });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
  });
  const page = await context.newPage();
  try {
    const success = await loginToEStack(page, username, password, {
      ...options,
      screenshotDir: path.join(__dirname, '..', 'output', 'screenshots'),
      loginUrl,
      successUrlKeyword: options.successUrlKeyword || '/portal',
    });
    if (!success) { console.error('[登录启动] ✗ 登录失败，无法保存 cookies'); return false; }
    const cookies = await context.cookies();
    fs.mkdirSync(path.dirname(cookieFile), { recursive: true });
    fs.writeFileSync(cookieFile, JSON.stringify(cookies, null, 2), 'utf8');
    console.log(`[登录启动] ✓ Cookies 已保存到 ${cookieFile}（共 ${cookies.length} 条）`);
    const hasToken = cookies.some(c => c.name === 'accessToken' || c.name === 'Authorization');
    if (!hasToken) console.warn('[登录启动] ⚠️  cookies 中未找到 accessToken，请检查');
    else {
      const tokenVal = cookies.find(c => c.name === 'accessToken')?.value || '';
      console.log(`[登录启动] accessToken: ${tokenVal.slice(0, 8)}...${tokenVal.slice(-8)}`);
    }
    return true;
  } catch (e) {
    console.error('[登录启动] ✗ 异常:', e.message);
    return false;
  } finally {
    await browser.close().catch(() => {});
  }
}

module.exports = { loginToEStack, loginAndSaveCookies };