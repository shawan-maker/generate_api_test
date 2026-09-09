"""
request_interceptor.py - HTTP 请求/响应拦截与收集

职责：
- 注册 Playwright page 的 request/response 事件监听
- 收集 API 调用（URL、Method、Body、触发上下文）
- 收集响应样本（状态码、Body 片段）
- 识别权限门禁关键词
- KB 驱动的请求注入（给前端 monkey-patch XHR/fetch，自动补 tenantId 等字段）
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

LOG = logging.getLogger("request_interceptor")

# KB 缓存（模块级，进程内共享）
_KB_CACHE: Optional[dict] = None


def load_kb() -> dict:
    """加载 config/probe_lessons_kb.json（带缓存）。缺失时返回空 dict，不影响主流程。"""
    global _KB_CACHE
    if _KB_CACHE is not None:
        return _KB_CACHE
    kb_path = Path(__file__).resolve().parent.parent / "config" / "probe_lessons_kb.json"
    try:
        _KB_CACHE = json.loads(kb_path.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.warning(f"知识库加载失败(将跳过注入/门禁识别): {e}")
        _KB_CACHE = {}
    return _KB_CACHE


def resolve_inject_config(target_url: str) -> Optional[dict]:
    """根据 KB 的 systems.<x>.match / systems.<x>.inject，决定当前模块是否需要注入必填字段。

    返回 None 表示不注入；否则 {system, url_regex, body_fields, source}。所有值来自 KB。
    """
    kb = load_kb()
    for sys_name, cfg in (kb.get("systems") or {}).items():
        match = cfg.get("match") or {}
        sub = match.get("url_substring")
        if sub and sub in target_url:
            inj = cfg.get("inject") or {}
            if inj.get("enabled") and inj.get("url_regex") and inj.get("body_fields"):
                return {"system": sys_name,
                        "url_regex": inj["url_regex"],
                        "body_fields": inj["body_fields"],
                        "source": inj.get("source")}
    return None


def build_inject_js(inject_cfg: dict) -> str:
    """构造通用 XHR/fetch 包装补丁；要注入的字段与 URL 正则全部来自 KB，不在此硬编码。

    注入值优先从 KB.inject.source.endpoint（current-user）实时获取（如 adminId=当前用户 id、
    tenantId），仅在拉取失败时回退 KB.inject.body_fields 静态值 —— 实现「KB 驱动、零硬编码、
    适配任意登录账号」。
    """
    regex = inject_cfg["url_regex"]
    fields_js = json.dumps(inject_cfg["body_fields"], ensure_ascii=False)
    source = inject_cfg.get("source") or {}
    source_js = json.dumps({
        "endpoint": source.get("endpoint"),
        "field_map": source.get("field_map") or {},
    }, ensure_ascii=False)
    return f"""
(() => {{
  const MATCH = new RegExp({json.dumps(regex)});
  const STATIC = {fields_js};
  const SOURCE = {source_js};
  window.__kbInjectVals = Object.assign({{}}, STATIC);
  async function kbRefresh() {{
    if (!SOURCE || !SOURCE.endpoint) return;
    try {{
      const r = await fetch(SOURCE.endpoint, {{ credentials: 'include', cache: 'no-store' }});
      if (!r.ok) return;
      const j = await r.json();
      const ent = (j && j.entity) || {{}};
      const m = SOURCE.field_map || {{}};
      for (const bodyKey in m) {{
        const respKey = m[bodyKey];
        const v = ent[respKey];
        if (v !== undefined && v !== null && v !== '') {{ window.__kbInjectVals[bodyKey] = v; }}
      }}
    }} catch (e) {{}}
  }}
  kbRefresh();
  setInterval(kbRefresh, 10000);

  function inject(b) {{
    try {{
      const o = JSON.parse(b);
      let changed = false;
      const vals = window.__kbInjectVals || STATIC;
      for (const k in vals) {{
        if (o[k] === undefined || o[k] === null || o[k] === '') {{ o[k] = vals[k]; changed = true; }}
      }}
      if (changed) return JSON.stringify(o);
    }} catch (e) {{}}
    return b;
  }}
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (m, u) {{ this.__u = u; return origOpen.apply(this, arguments); }};
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (b) {{
    if (b && typeof b === 'string' && MATCH.test(this.__u || '')) {{ arguments[0] = inject(b); }}
    return origSend.apply(this, arguments);
  }};
  const origFetch = window.fetch;
  window.fetch = function (u, opts) {{
    if (opts && opts.body && typeof opts.body === 'string' && MATCH.test(typeof u === 'string' ? u : ('' + u))) {{
      opts = Object.assign({{}}, opts); opts.body = inject(opts.body);
    }}
    return origFetch(u, opts);
  }};
  window.__kbInjected = true;
}})();
"""


class RequestInterceptor:
    """Playwright 请求/响应拦截器。

    用法：
        interceptor = RequestInterceptor(page, base_url, target_url)
        await interceptor.install()
        # ... 驱动页面操作 ...
        calls, samples, gates = interceptor.collect()

    参数：
        capture_all_mode: True 时捕获所有 XHR/fetch；False 时只捕获 api_path_prefix 匹配的请求
        api_path_prefix: API 路径前缀（默认 /estack/api），不同项目可配置不同值
    """

    # 静态资源扩展名（拦截器忽略这些请求）
    SKIP_EXTENSIONS = (
        '.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
        '.woff', '.woff2', '.ttf', '.eot', '.map', '.mp4', '.webm'
    )

    def __init__(self, page, base_url: str, target_url: str, capture_all_mode: bool = False, api_path_prefix: str = None):
        self.page = page
        self.base_url = base_url
        self.target_url = target_url
        self.capture_all_mode = capture_all_mode
        # API 路径前缀（从 profile.yaml 的 api_base 传入，无业务特定默认值）
        self.api_path_prefix = api_path_prefix or ""
        self.calls: List[Dict] = []
        self.samples: Dict[str, List] = {}
        self.permission_gates: List[Dict] = []
        self._ctx_holder = {"v": "init"}
        self._installed = False
        self._sid: Optional[str] = None

        # 加载 KB 与权限门禁关键词
        kb = load_kb()
        self._gate_keywords = kb.get("permission_gate_keywords") or [
            "您没有", "请先授权", "权限不足", "无权限",
            "permission denied", "forbidden", "not authorized", "403",
        ]

    def set_context(self, ctx: str):
        """设置当前操作上下文标签（如 'click:创建用户'、'row:编辑'）。"""
        self._ctx_holder["v"] = ctx

    async def install(self):
        """安装请求/响应监听器 + KB 注入补丁（如需要）。"""
        if self._installed:
            return

        # 注册事件监听（同步包装异步回调）
        self.page.on("request", self._on_request)
        self.page.on("response",
                     lambda r: __import__("asyncio").create_task(self._on_response(r)))

        # KB 驱动请求注入补丁（如 estack 的 tenantId 缺失）
        inject_cfg = resolve_inject_config(self.target_url)
        if inject_cfg:
            js = build_inject_js(inject_cfg)
            try:
                await self.page.add_init_script(js)
                await self.page.evaluate(js)
                LOG.info(f"  🔧 已应用知识库注入补丁(system={inject_cfg['system']}): "
                         f"对 {inject_cfg['url_regex']} 自动补 {list(inject_cfg['body_fields'])}")
            except Exception as e:
                LOG.warning(f"  注入补丁应用失败(不影响其他捕获): {e}")

        # 从页面提取 sid（SPA 框架登录后注入到 cookie/localStorage 的 session ID）
        await self._extract_sid()

        self._installed = True

    async def _extract_sid(self):
        """从浏览器中提取 sid（session identifier）。

        SPA 框架在登录后将 sid 写入 cookie 或 localStorage，前端 axios
        interceptor 自动附加到所有请求头。自动化脚本需要同步提取。
        """
        try:
            sid = await self.page.evaluate("""() => {
                // 1) 从 cookie 取
                const cookies = document.cookie.split(';').map(c => c.trim());
                for (const c of cookies) {
                    const [name, ...rest] = c.split('=');
                    if (name.trim() === 'sid' || name.trim() === 'JSESSIONID'
                        || name.trim() === 'SESSIONID') {
                        return {source: 'cookie', value: rest.join('=')};
                    }
                }
                // 2) 从 localStorage 取
                for (const key of ['sid', 'sessionId', 'SESSION_ID']) {
                    const v = localStorage.getItem(key);
                    if (v) return {source: 'localStorage.' + key, value: v};
                }
                // 3) 从 Vuex store 取（estack SPA 常见）
                try {
                    const app = document.querySelector('#app');
                    if (app && app.__vue__ && app.__vue__.$store) {
                        const state = app.__vue__.$store.state;
                        for (const mod of ['login', 'user', 'auth']) {
                            if (state[mod] && state[mod].sid) {
                                return {source: 'store.' + mod, value: state[mod].sid};
                            }
                        }
                    }
                } catch(e) {}
                return null;
            }""")
            if sid:
                self._sid = sid["value"]
                LOG.info(f"  🔑 提取到 sid ({sid['source']}): {self._sid[:20]}...")
            else:
                self._sid = None
                LOG.info("  ℹ️ 未找到 sid（SPA 可能尚未初始化或不需要 sid）")
        except Exception as e:
            self._sid = None
            LOG.debug(f"  sid 提取失败(不影响主流程): {e}")

    def _should_capture(self, url: str) -> bool:
        """判断是否应该捕获此请求"""
        if self.capture_all_mode:
            # 捕获所有 XHR/fetch（排除静态资源）
            return True
        # 默认模式：只捕获匹配的 API（使用可配置的路径前缀）
        return f"{self.base_url}{self.api_path_prefix}" in url

    def _on_request(self, req):
        """请求事件：记录 method、pathname、body、context、query_params。"""
        u = req.url
        if not self._should_capture(u):
            return
        if req.method == "OPTIONS":
            return
        parsed = urlparse(u)
        pn = parsed.path
        query_params = {k: v[0] for k, v in parse_qs(parsed.query).items()} if parsed.query else {}
        if pn.endswith(self.SKIP_EXTENSIONS):
            return
        if "/web/" in pn:
            return
        self.calls.append({
            "method": req.method,
            "pathname": pn.replace(self.base_url, ""),
            "query_params": query_params,
            "body": req.post_data or "",
            "context": self._ctx_holder["v"],
            "ts": time.time(),
        })

    async def _on_response(self, resp):
        """响应事件：收集样本、识别权限门禁。"""
        u = resp.url
        if not self._should_capture(u):
            return
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            return
        try:
            b = await resp.text()
            if b and len(b) > 20:
                pn = urlparse(u).path.replace(self.base_url, "")
                # 权限门禁识别（修复: 纯数字关键词需状态码匹配，避免子串误报）
                status = resp.status
                low = b.lower()
                hit = None
                for kw in self._gate_keywords:
                    kw_low = kw.lower()
                    # 纯数字关键词（如 "403", "401"）：必须 HTTP 状态码匹配才视为命中
                    if kw_low.isdigit():
                        if str(status) == kw_low:
                            hit = kw
                            break
                    elif kw_low in low:
                        hit = kw
                        break
                if hit:
                    self.permission_gates.append({
                        "method": resp.request.method,
                        "pathname": pn,
                        "status": status,
                        "keyword_hit": hit,
                        "snippet": (b[:200] + "...") if len(b) > 200 else b,
                    })
                    LOG.info(f"  ⚠️ 命中权限门禁: {resp.request.method} {pn} "
                             f"(HTTP {status}, 命中关键词: {hit})")
                # 响应样本收集（每个路径最多保留 2 个样本）
                # 修复: 前置 API 候选路径（/current-user, /policies 等）响应常超 3000 字符，
                #       截断后 JSON 解析失败导致前置 API 无法识别。
                #       对 GET 请求保留完整响应体（通常 <20KB），POST/PUT 仍截断。
                if pn not in self.samples:
                    self.samples[pn] = []
                if len(self.samples[pn]) < 2:
                    body_for_sample = b  # GET 请求保留完整，便于前置 API 字段提取
                    if resp.request.method != "GET" and len(b) > 3000:
                        body_for_sample = b[:3000]
                    self.samples[pn].append({
                        "status": resp.status,
                        "body": body_for_sample
                    })
        except Exception as e:
            LOG.debug(f"处理响应样本失败: {e}")

    def collect(self):
        """返回收集到的 (calls, samples, permission_gates, sid)。"""
        return self.calls, self.samples, self.permission_gates, self._sid
