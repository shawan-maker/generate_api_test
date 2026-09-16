#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_profile.py — Profile 自动探测器 (框架层, 项目无关)

用途: 给一个被测系统的入口 URL, 零登录、纯静态地探出建 profile 所需的事实, 并生成
      projects/<id>/profile.yaml 草稿 + probe_report.md。
      对应方案设计 G1: 新增被测系统 = 复制 profile + 跑发现, 而 profile 本身由本脚本实采生成。

探测六件事:
  1. 可达性 / 是否重定向登录 / 服务器与网关特征
  2. 前端框架指纹 (Vue / React / Angular) 与 UI 组件库 (Element UI / Ant Design / iView)
  3. API 文档端点字典扫描 (Swagger / OpenAPI / knife4j / actuator / DRF / GraphQL)
  4. JS bundle 静态分析 -> API 根前缀、service 命名空间、路径样本
  5. 鉴权线索: 登录接口、token 存储键、自定义请求头 (如 SID)、Bearer 用法
  6. 验证码线索: 端点 + 类型推断 (滑块 / 图形 / 短信)

用法:
  python -m discovery.probe_profile --url https://console-xxx.example.cn --id newsys
  python -m discovery.probe_profile --url https://... --id newsys --write   # 直接写 profile.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------- 文档端点字典 (Level 1 发现) ----------
DOC_PATHS = [
    "/v3/api-docs", "/v2/api-docs", "/v3/api-docs/swagger-config",
    "/swagger.json", "/openapi.json", "/swagger-resources",
    "/swagger-ui.html", "/swagger-ui/index.html", "/doc.html",
    "/actuator/mappings", "/api/schema/", "/api/docs", "/graphql",
]

# ---------- 指纹表 ----------
FRAMEWORK_HINTS = {
    "vue": ["vue", "createApp", "new Vue", "__VUE", "v-cloak"],
    "react": ["react", "__REACT", "_reactRootContainer", "createElement"],
    "angular": ["ng-version", "platformBrowserDynamic", "angular"],
}
UILIB_HINTS = {
    "element-ui / element-plus": [".el-", "el-menu", "el-button", "#409EFF", "ElMessage"],
    "ant-design": ["ant-btn", "ant-menu", "@ant-design", "antd"],
    "iview / view-ui": ["ivu-", "iview"],
    "arco-design": ["arco-", "@arco-design"],
}

# ---------- 正则 ----------
RE_JS_SRC = re.compile(r'src=["\']([^"\']+\.js[^"\']*)["\']')
RE_CSS_SRC = re.compile(r'href=["\']([^"\']+\.css[^"\']*)["\']')
RE_PATH_STR = re.compile(r'["\'`](/[A-Za-z0-9_\-./{}$:]{4,120})["\'`]')
RE_CHUNK = re.compile(r'["\'`]?(?:static/)?js/([A-Za-z0-9_\-.~]+?\.[0-9a-f]{6,}\.js)["\'`]?')
RE_STORAGE_KEY = re.compile(r'(?:localStorage|sessionStorage)\s*\.\s*(?:get|set)Item\s*\(\s*["\']([\w\-.]+)["\']')
RE_CUSTOM_HEADER = re.compile(r'["\']([A-Z][A-Za-z]*-?[A-Za-z]*(?:ID|Id|SID|Sid|Token|Language|Tenant|Region))["\']\s*[:=]')
RE_BEARER = re.compile(r'Bearer\s*[\'"`+\s]|["\']Bearer ["\']')
# API 根前缀常量 (estack 实测: const i={dec:"/estack/api/estack",...})
RE_API_BASE = re.compile(r'(?:dec|baseURL|baseUrl|base|prefix|apiBase|apiPrefix|gateway)\s*[:=]\s*["\'](/[A-Za-z0-9_\-./]{4,80})["\']')
RE_AUTHZ = re.compile(r'Bearer|Authorization')
# 全文本鉴权/验证码信号(不依赖路径词频, 避免低频的登录/滑块接口被 600 截断漏掉)
RE_AUTH_SIGNAL = re.compile(
    r'(login|signin|sign-in|logon|authenticate|oauth|pictures-verification|checkcap|'
    r'slide|slider|puzzle|vcode|captcha|verif|backimage|sliding|xpos|'
    r'Estack-Language|Bearer|Authorization)', re.I)

LOGIN_KW = ("login", "signin", "sign-in", "logon", "authenticate", "oauth/token")
CAPTCHA_KW = ("captcha", "checkcap", "verif", "slide", "slider", "puzzle", "pictures-verification", "vcode")
SLIDER_KW = ("xpos", "slide", "slider", "puzzle", "pictures-verification", "backimage", "sliding")


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def fetch(url: str, timeout: int = 15, limit: int = 16_000_000):
    """返回 (status, final_url, headers, text)。失败时 status=None, text=错误信息。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        r = urllib.request.urlopen(req, timeout=timeout, context=_ctx())
        raw = r.read(limit)
        try:
            text = raw.decode("utf-8", "ignore")
        except Exception:
            text = ""
        return r.getcode(), r.geturl(), dict(r.getheaders()), text
    except urllib.error.HTTPError as e:
        return e.code, url, dict(getattr(e, "headers", {}) or {}), ""
    except Exception as e:
        return None, url, {}, f"{type(e).__name__}: {e}"


# ============ 1. 入口页 ============
def probe_entry(url: str) -> dict:
    st, final, hdr, text = fetch(url)
    out = {
        "requested_url": url,
        "status": st,
        "final_url": final,
        "redirected": final.rstrip("/") != url.rstrip("/"),
        "server": hdr.get("Server"),
        "content_type": hdr.get("Content-Type"),
        "is_spa_shell": bool(text) and "<div id=" in text and len(text) < 20000,
        "title": (re.search(r"<title>(.*?)</title>", text, re.S).group(1).strip()
                  if re.search(r"<title>(.*?)</title>", text, re.S) else None),
        "js_assets": RE_JS_SRC.findall(text)[:20],
        "css_assets": RE_CSS_SRC.findall(text)[:10],
        "html_len": len(text),
        "_text": text,
    }
    return out


# ============ 2. 框架指纹 ============
def detect_stack(*texts: str) -> dict:
    blob = "\n".join(t or "" for t in texts)
    low = blob.lower()
    fw = {}
    for name, kws in FRAMEWORK_HINTS.items():
        n = sum(low.count(k.lower()) for k in kws)
        if n:
            fw[name] = n
    ui = {}
    for name, kws in UILIB_HINTS.items():
        n = sum(low.count(k.lower()) for k in kws)
        if n:
            ui[name] = n
    return {
        "framework": max(fw, key=fw.get) if fw else None,
        "framework_scores": fw,
        "ui_library": max(ui, key=ui.get) if ui else None,
        "ui_library_scores": ui,
    }


# ============ 3. 文档端点 ============
def probe_docs(base: str) -> list:
    rows = []
    for p in DOC_PATHS:
        u = urljoin(base, p)
        st, final, hdr, text = fetch(u, timeout=8, limit=4000)
        hit = bool(st and st < 400 and ("json" in (hdr.get("Content-Type") or "").lower()
                                        or "swagger" in text.lower() or "openapi" in text.lower()))
        rows.append({"path": p, "status": st, "content_type": hdr.get("Content-Type"), "looks_like_doc": hit})
    return rows


# ============ 4. JS bundle 静态分析 ============
def analyse_js(base: str, js_assets: list, max_files: int = 6, max_chunks: int = 14,
               progress=None) -> dict:
    """
    两轮分析:
      轮1 — 入口 bundle(app.js / chunk-vendors.js), 通常只是壳, 但能挖出 chunk 清单;
      轮2 — 懒加载 chunk, 业务 API 路径几乎全在这里(实测 estack: 轮1 仅 18 条, 轮2 才是金矿)。
    """
    paths, storage_keys, headers_found, chunks = Counter(), Counter(), Counter(), set()
    api_bases = Counter()
    auth_signals = Counter()
    state = {"bearer": False, "authz": False}
    fetched = []
    js_dir = None

    def scan(text: str):
        for m in RE_PATH_STR.findall(text):
            if len(m) > 4 and not m.lower().endswith(
                    (".js", ".css", ".png", ".svg", ".jpg", ".jpeg", ".gif", ".woff",
                     ".woff2", ".ttf", ".map", ".mp4", ".ico")):
                paths[m] += 1
        for k in RE_STORAGE_KEY.findall(text):
            storage_keys[k] += 1
        for h in RE_CUSTOM_HEADER.findall(text):
            headers_found[h] += 1
        for c in RE_CHUNK.findall(text):
            chunks.add(c)
        for b in RE_API_BASE.findall(text):
            api_bases[b] += 1
        if RE_BEARER.search(text):
            state["bearer"] = True
        if RE_AUTHZ.search(text):
            state["authz"] = True
        for s in set(m.lower() for m in RE_AUTH_SIGNAL.findall(text)):
            auth_signals[s] += 1

    # ---- 轮1: 入口资源 (estack 实测为单 bundle ~12MB, 必须读全) ----
    for rel in js_assets[:max_files]:
        u = rel if rel.startswith("http") else urljoin(base, rel)
        st, _f, _h, text = fetch(u, timeout=60, limit=16_000_000)
        if not st or st >= 400 or not text:
            continue
        if js_dir is None:
            js_dir = u.rsplit("/", 1)[0]      # 例: https://host/estack/web/estack/js
        fetched.append({"url": u, "size": len(text), "round": 1})
        scan(text)

    # ---- 轮2: 懒加载 chunk (webpack 拆包项目才有; estack 单 bundle 则自然跳过) ----
    round1_chunks = sorted(chunks)
    if js_dir and round1_chunks:
        if progress:
            progress(f"  轮1 挖出 {len(round1_chunks)} 个 chunk, 抓取前 {max_chunks} 个 ...")
        for name in round1_chunks[:max_chunks]:
            u = f"{js_dir}/{name.split('/')[-1]}"
            st, _f, _h, text = fetch(u, timeout=60, limit=16_000_000)
            if not st or st >= 400 or not text:
                continue
            fetched.append({"url": u, "size": len(text), "round": 2})
            scan(text)

    return {
        "js_fetched": fetched,
        "api_path_samples": [p for p, _ in paths.most_common(600)],
        "storage_keys": [k for k, _ in storage_keys.most_common(15)],
        "custom_headers": [h for h, _ in headers_found.most_common(15)],
        "uses_bearer": state["bearer"],
        "uses_authz": state["authz"],
        "api_base_consts": [b for b, _ in api_bases.most_common(8)],
        "auth_signals": [s for s, _ in auth_signals.most_common(30)],
        "chunk_files": sorted(chunks)[:60],
        "chunk_total": len(chunks),
        "_js_text_len": sum(f["size"] for f in fetched),
    }


def infer_api_shape(path_samples: list, api_bases: list = None) -> dict:
    """从路径样本反推 API 根前缀与 service 命名空间。

    api_bases 优先: 若 JS 里能直接抓到形如 dec:"/estack/api/estack" 的根前缀常量,
    用它作为 api_base(最准); 否则回退到路径公共前缀启发式。
    """
    api_like = [p for p in path_samples if re.search(r"/(api|v\d)(/|$)", p)]
    # 根前缀: 取出现最多的前 2~3 段公共前缀 (回退用)
    pref = Counter()
    for p in api_like:
        seg = [s for s in p.split("/") if s]
        for n in (2, 3, 4):
            if len(seg) >= n:
                pref["/" + "/".join(seg[:n])] += 1
    api_base_candidates = [p for p, c in pref.most_common(6) if c >= 2]

    # service 命名空间: <root>/<service>/v1/... 中的 <service>
    svc = Counter()
    for p in api_like:
        m = re.search(r"/([a-z][a-z0-9\-]{2,20})/(?:v\d|inner)(?:/|$)", p)
        if m:
            svc[m.group(1)] += 1

    # 优先采用 JS 常量里的根前缀
    api_base = None
    if api_bases:
        for b in api_bases:
            if "/api/" in b:
                api_base = b
                break
        if api_base is None:
            api_base = api_bases[0]

    return {
        "api_base": api_base,
        "api_base_candidates": ([api_base] + api_base_candidates) if api_base else api_base_candidates,
        "service_prefixes": [s for s, _ in svc.most_common(40)],
        "api_path_count": len(api_like),
        "api_paths_preview": api_like[:40],
    }


def infer_auth(path_samples: list, js: dict, authz: bool = False) -> dict:
    signals = set(js.get("auth_signals", []))
    login = [p for p in path_samples if any(k in p.lower() for k in LOGIN_KW)]
    captcha = [p for p in path_samples if any(k in p.lower() for k in CAPTCHA_KW)]
    blob = " ".join(path_samples).lower()
    # 滑块判定: 优先看全文本信号(不依赖路径词频)
    slider_kw_hit = any(k in signals for k in ("slide", "slider", "puzzle",
                                              "pictures-verification", "backimage",
                                              "sliding", "xpos"))
    captcha_type = None
    if captcha or slider_kw_hit:
        captcha_type = "slider" if (slider_kw_hit or any(k in blob for k in SLIDER_KW)) else "image"
    token_keys = [k for k in js.get("storage_keys", [])
                  if re.search(r"token|auth|jwt|session|sid", k, re.I)]
    # 登录端点: 路径样本 + 全文本信号双重确认
    login_present = bool(login) or any(k in signals for k in
                                       ("login", "signin", "sign-in", "logon", "authenticate"))
    return {
        "login_detected": login_present,
        "login_endpoints": login[:10],
        "captcha_endpoints": captcha[:10],
        "captcha_type_guess": captcha_type,
        "token_storage_keys": token_keys,
        "custom_headers": js.get("custom_headers", []),
        "auth_signals": sorted(signals),
        "scheme_guess": "bearer" if (js.get("uses_bearer") or authz or "bearer" in signals
                                     or "authorization" in signals) else "unknown",
    }


# ============ 5. 生成 profile 草稿 ============
def build_profile_yaml(pid: str, url: str, entry: dict, stack: dict,
                       shape: dict, auth: dict, docs: list) -> str:
    split = urlsplit(entry.get("final_url") or url)
    base_url = f"{split.scheme}://{split.netloc}"
    web_entry = split.path.rstrip("/") or "/"
    api_base = shape.get("api_base") or (shape["api_base_candidates"][0] if shape["api_base_candidates"] else "/api")
    doc_hits = [d["path"] for d in docs if d["looks_like_doc"]]
    upper = pid.upper().replace("-", "_")

    def ylist(items, indent="  ", empty_hint="# (未探到, 待实采补充)"):
        if not items:
            return f"{indent}{empty_hint}"
        return "\n".join(f'{indent}- "{i}"' for i in items)

    return f"""# ===== {pid} profile — 由 discovery/probe_profile.py 自动探测生成 =====
# 探测源: {url}
# 注意: 标记 TODO 的字段为"推断值", 需登录实采后确认 (对应方案 G1: profile 待实采)

name: "{pid}"
display_name: "{entry.get('title') or pid}"
base_url: "{base_url}"
web_entry: "{web_entry}"

# ---- 前端栈 (决定 nav_kb 选择) ----
frontend:
  framework: "{stack.get('framework') or 'unknown'}"
  ui_library: "{stack.get('ui_library') or 'unknown'}"
  nav_kb: "nav_kb.json"          # Element UI 系可直接复用 config/base_nav_kb.json

# ---- API 形状 ----
api_base: "{api_base}"           # TODO 确认: 候选 {shape['api_base_candidates'][:3]}
service_prefixes:
{ylist(shape['service_prefixes'])}

# ---- API 文档端点 (Level 1 发现) ----
openapi_docs:
{ylist(doc_hits, empty_hint="# 无 Swagger/OpenAPI 端点 -> 必须走流量捕获 + 前端探测")}

# ---- 鉴权 ----
auth:
  scheme: "{auth.get('scheme_guess')}"
  login_path: "{(auth['login_endpoints'][0] if auth['login_endpoints'] else '')}"   # TODO 确认
  token_storage_key: "{(auth['token_storage_keys'][0] if auth['token_storage_keys'] else '')}"
  header_name: "Authorization"
  header_template: "Bearer {{{{token}}}}"
  extra_headers:                 # TODO 实采确认固定值(如 SID/语言)
{ylist(auth['custom_headers'][:6], indent="    ")}
  captcha:
    type: "{auth.get('captcha_type_guess') or 'none'}"
    endpoints:
{ylist(auth['captcha_endpoints'][:5], indent="      ")}

# ---- 菜单 ----
menu:
  # TODO 实采: 很多控制台首页(portal)不渲染菜单, 需填一个"具体功能页"作为落点
  menu_landing: "{web_entry}"
  api_endpoints: []
  dom_menu_selector: ".el-menu, .el-submenu, .el-menu-item"

# ---- 凭据: 只放 env var 引用, 绝不落明文 ----
credentials:
  username_env: "{upper}_USER"
  password_env: "{upper}_PASS"

# ---- 安全护栏 ----
guardrails:
  resource_prefix: "AT_"
  allow_write: false             # TODO 确认测试环境后再开
  danger_blacklist: []
"""


def build_report(pid, url, entry, stack, js, shape, auth, docs) -> str:
    L = []
    L.append(f"# Profile 探测报告 — {pid}")
    L.append(f"\n- 探测目标: {url}")
    L.append(f"- 最终 URL: {entry.get('final_url')} (重定向: {'是' if entry.get('redirected') else '否'})")
    L.append(f"- HTTP 状态: {entry.get('status')} | Server: {entry.get('server')}")
    L.append(f"- 页面标题: {entry.get('title')}")
    L.append(f"- SPA 外壳: {'是' if entry.get('is_spa_shell') else '否'} (HTML {entry.get('html_len')} 字节)")

    L.append("\n## 1. 前端栈")
    L.append(f"- 框架: **{stack.get('framework') or '未识别'}** {stack.get('framework_scores')}")
    L.append(f"- UI 库: **{stack.get('ui_library') or '未识别'}** {stack.get('ui_library_scores')}")

    L.append("\n## 2. API 文档端点扫描")
    hits = [d for d in docs if d["looks_like_doc"]]
    if hits:
        L.append("**命中(最大赢面, 可直接取全量端点):**")
        for d in hits:
            L.append(f"- `{d['path']}` → {d['status']} ({d['content_type']})")
    else:
        L.append("全部未命中 → **Level 1 发现判死, 必须走流量捕获 + 前端表单探测**。明细:")
        for d in docs:
            L.append(f"- `{d['path']}` → {d['status']}")

    L.append("\n## 3. API 形状(JS 静态分析)")
    L.append(f"- 分析 JS 文件 {len(js['js_fetched'])} 个, 共 {js['_js_text_len']} 字符")
    L.append(f"- 抽出 API 风格路径 {shape['api_path_count']} 条")
    L.append(f"- **api_base(检测常量优先): {shape.get('api_base') or '未检测到常量, 用候选'}**")
    L.append(f"- api_base 候选: {shape['api_base_candidates'][:5]}")
    L.append(f"- api_base 常量命中: {js.get('api_base_consts')}")
    L.append(f"- service 命名空间({len(shape['service_prefixes'])}): {shape['service_prefixes']}")
    if shape["api_paths_preview"]:
        L.append("- 路径样例:")
        for p in shape["api_paths_preview"][:25]:
            L.append(f"  - `{p}`")

    L.append("\n## 4. 鉴权线索")
    L.append(f"- 方案推断: **{auth['scheme_guess']}**")
    L.append(f"- 登录端点候选: {auth['login_endpoints'] or '未探到'}")
    L.append(f"- token 存储键: {auth['token_storage_keys'] or '未探到'}")
    L.append(f"- 自定义请求头候选: {auth['custom_headers'] or '未探到'}")
    L.append(f"- 验证码类型推断: **{auth['captcha_type_guess'] or 'none'}**; 端点: {auth['captcha_endpoints'] or '无'}")
    if auth["captcha_type_guess"] == "slider":
        L.append("  > 滑块验证码 → 复用 `lib/slider.py`(EcsCloud identify_gap) + Playwright 拖拽")

    L.append("\n## 5. 懒加载 chunk(补全接口用)")
    L.append(f"- 发现 chunk {len(js['chunk_files'])} 个" + (f", 前 10: {js['chunk_files'][:10]}" if js["chunk_files"] else ""))

    L.append("\n## 6. 待实采确认项(TODO)")
    L.append("- `api_base` 最终值(候选里挑或登录后抓包确认)")
    L.append("- `menu.menu_landing`: portal 首页常不渲染菜单, 需指定一个具体功能页")
    L.append("- `auth.extra_headers` 固定值(如 SID / 语言头)")
    L.append("- `guardrails.allow_write`: 确认是测试环境后再开启写操作")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Profile 自动探测器")
    ap.add_argument("--url", required=True, help="被测系统入口 URL")
    ap.add_argument("--id", required=True, help="项目 id (用于 projects/<id>/)")
    ap.add_argument("--write", action="store_true", help="直接写入 projects/<id>/profile.yaml")
    ap.add_argument("--max-js", type=int, default=6, help="最多分析多少个入口 JS")
    ap.add_argument("--max-chunks", type=int, default=14, help="最多分析多少个懒加载 chunk")
    args = ap.parse_args()

    print(f"[1/5] 探测入口页 {args.url} ...", flush=True)
    entry = probe_entry(args.url)
    if entry["status"] is None:
        print(f"  !! 入口不可达: {entry['_text'][:160]}")
        return 2
    print(f"  status={entry['status']} final={entry['final_url']} js={len(entry['js_assets'])}", flush=True)

    split = urlsplit(entry["final_url"])
    base = f"{split.scheme}://{split.netloc}"

    print("[2/5] 扫描 API 文档端点 ...", flush=True)
    docs = probe_docs(base)
    print(f"  命中 {sum(1 for d in docs if d['looks_like_doc'])} / {len(docs)}", flush=True)

    print(f"[3/5] 分析 JS bundle (入口 {args.max_js} 个 + chunk {args.max_chunks} 个) ...", flush=True)
    js = analyse_js(base, entry["js_assets"], max_files=args.max_js,
                    max_chunks=args.max_chunks, progress=lambda m: print(m, flush=True))
    print(f"  抓取 JS {len(js['js_fetched'])} 个 (共 {js['_js_text_len']} 字符), "
          f"路径样本 {len(js['api_path_samples'])} 条, chunk 总数 {js.get('chunk_total', 0)}", flush=True)

    print("[4/5] 推断 API 形状与鉴权 ...", flush=True)
    css_text = ""
    for c in entry["css_assets"][:2]:
        _s, _f, _h, t = fetch(c if c.startswith("http") else urljoin(base, c), timeout=20)
        css_text += t or ""
    stack = detect_stack(entry["_text"], css_text, *[""])
    # JS 文本已在 analyse_js 内部读过, 这里用路径样本+headers 再补一次指纹
    stack2 = detect_stack(" ".join(js["api_path_samples"]) + " ".join(js["custom_headers"]))
    for k, v in (stack2.get("framework_scores") or {}).items():
        stack["framework_scores"][k] = stack["framework_scores"].get(k, 0) + v
    if stack["framework_scores"]:
        stack["framework"] = max(stack["framework_scores"], key=stack["framework_scores"].get)
    shape = infer_api_shape(js["api_path_samples"], js.get("api_base_consts"))
    auth = infer_auth(js["api_path_samples"], js, js.get("uses_authz", False))
    print(f"  framework={stack['framework']} ui={stack['ui_library']} "
          f"api_base={shape['api_base_candidates'][:2]} captcha={auth['captcha_type_guess']}", flush=True)

    print("[5/5] 生成 profile 草稿与报告 ...", flush=True)
    yaml_text = build_profile_yaml(args.id, args.url, entry, stack, shape, auth, docs)
    report = build_report(args.id, args.url, entry, stack, js, shape, auth, docs)

    outdir = ROOT / "projects" / args.id
    (outdir / "kb").mkdir(parents=True, exist_ok=True)
    (outdir / "captures").mkdir(parents=True, exist_ok=True)
    (outdir / "output" / "config").mkdir(parents=True, exist_ok=True)

    draft = outdir / ("profile.yaml" if args.write else "profile.probed.yaml")
    draft.write_text(yaml_text, encoding="utf-8")
    (outdir / "probe_report.md").write_text(report, encoding="utf-8")
    (outdir / "captures" / "probe_raw.json").write_text(
        json.dumps({"entry": {k: v for k, v in entry.items() if k != "_text"},
                    "stack": stack, "docs": docs,
                    "js": {k: v for k, v in js.items() if not k.startswith("_")},
                    "shape": shape, "auth": auth}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成。产物:")
    print(f"  - {draft}")
    print(f"  - {outdir / 'probe_report.md'}")
    print(f"  - {outdir / 'captures' / 'probe_raw.json'}")
    if not args.write:
        print(f"\n提示: 审阅 profile.probed.yaml 后, 重命名为 profile.yaml 或加 --write 直接覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
