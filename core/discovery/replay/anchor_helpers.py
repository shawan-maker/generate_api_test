# -*- coding: utf-8 -*-
"""
anchor_helpers.py - 输入项锚点共享模块（Stage 1 记录 / Stage 2 回放双端共用）

输入项锚点模型：
- 任何组件交互（radio / transfer-box / select / table …）都归属到唯一"输入项"容器
  （Element UI: .el-form-item），锚点键 = 输入项标签文本。
- 所有探测、归因、执行、状态判读都限定在锚点容器内——禁止全局第一命中。
- 解析策略（降级链，尽量少用序号）：
    1. text        : 标签文本在页面内唯一
    2. text+attr   : 文本 + 唯一性业务属性过滤（业务 class token；剔除 el-*/is-*/ant-*/data-v-* 等易变类）
    3. text+ordinal: 文本 + 同文本候选序号（最后手段，显式记录 ordinal）
- 任何一级解析失败都显式抛错（AnchorResolutionError，携带诊断信息），不做全局兜底。

锚点描述符（KB 中记录的 anchor dict）：
    {
      "text":      "授权角色",                                  # 标签原文（空白折叠后，用于精确匹配）
      "norm_text": "授权角色",                                  # 规范化文本（去尾部冒号/星号，用于展示/target_label）
      "strategy":  "text" | "text+attr" | "text+ordinal",
      "attr":      {"class": "业务类名"} | None,
      "ordinal":   int | None
    }

容器内相对定位：
- 记录侧产生的 interaction_steps 中，selector 一律是锚点容器内相对选择器，
  并携带 anchor 描述符；回放侧用 build_anchor_css(anchor) + " >> " + selector 合成
  Playwright 引擎链，全程无全局匹配、无坐标。
"""

import json
import re
import logging

LOG = logging.getLogger("anchor_helpers")


class AnchorResolutionError(Exception):
    """锚点解析失败（容器未找到 / 歧义 / CSS 唯一性校验不过），携带诊断信息。"""

    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


# ---------------------------------------------------------------------------
# 归一化（Python 侧，与 JS 侧保持一致）
# ---------------------------------------------------------------------------

def normalize_text(s: str) -> str:
    """空白折叠 + trim（与 JS norm() 对齐）。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


def strip_label_decorations(s: str) -> str:
    """去掉标签尾部冒号/星号装饰（用于 norm_text）。"""
    s = normalize_text(s)
    s = re.sub(r"[：:]\s*$", "", s)
    s = re.sub(r"[*＊]\s*$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Stage 1: 从组件元素提取锚点描述符
# ---------------------------------------------------------------------------

# 在组件元素上执行：向上找输入项容器，采集标签文本与唯一性属性候选
_EXTRACT_ANCHOR_JS = r"""
el => {
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    const fi = el.closest('.el-form-item, .ant-form-item');
    if (!fi) return { found: false, reason: 'no_form_item_ancestor' };

    let labelEl = fi.querySelector('.el-form-item__label, .ant-form-item-label label');
    if (!labelEl) labelEl = fi.querySelector('label, [class*="label"]');
    if (!labelEl) return { found: false, reason: 'no_label_element' };

    const raw = norm(labelEl.textContent);
    if (!raw) return { found: false, reason: 'empty_label_text' };

    const labelOf = (x) => {
        let l = x.querySelector('.el-form-item__label, .ant-form-item-label label');
        if (!l) l = x.querySelector('label, [class*="label"]');
        return l;
    };
    const items = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item'));
    const matches = items.filter(x => {
        const l = labelOf(x);
        return l && norm(l.textContent) === raw;
    });

    // 业务属性候选：剔除框架类(el-*)、状态类(is-*)、ant-*、作用域标记(data-v-*)
    const clsTokens = ((fi.className || '') + '').split(/\s+/).filter(c =>
        c && !/^el-/.test(c) && !/^is-/.test(c) && !/^ant-/.test(c) && !/^data-v-/.test(c));

    // 唯一性属性探测：该 class token 在全部输入项中唯一出现（保证 CSS 全页唯一）
    let uniqueToken = null;
    for (const t of clsTokens) {
        const cnt = items.filter(m => ((m.className || '') + '').split(/\s+/).includes(t)).length;
        if (cnt === 1) { uniqueToken = t; break; }
    }

    return {
        found: true,
        text: raw,
        norm_text: raw.replace(/[：:]\s*$/, '').replace(/[*＊]\s*$/, '').trim(),
        id: fi.id || null,
        classes: clsTokens,
        unique_class_token: uniqueToken,
        same_label_count: matches.length,
        ordinal: matches.indexOf(fi),
    };
}
"""


def build_anchor_descriptor(raw: dict) -> dict:
    """从提取结果按降级链确定 strategy，构建锚点描述符（纯数据，无 IO）。"""
    text = raw["text"]
    strategy = "text"
    attr = None
    ordinal = None

    if raw.get("same_label_count") != 1:
        tok = raw.get("unique_class_token")
        if tok and re.fullmatch(r"[A-Za-z0-9_-]+", tok or ""):
            strategy = "text+attr"
            attr = {"class": tok}
        else:
            strategy = "text+ordinal"
            ordinal = raw.get("ordinal", 0)

    return {
        "text": text,
        "norm_text": raw.get("norm_text") or text,
        "strategy": strategy,
        "attr": attr,
        "ordinal": ordinal,
    }


# ---------------------------------------------------------------------------
# 描述符 → Playwright 选择器（Stage 1 记录步骤 / Stage 2 回放合成共用）
# ---------------------------------------------------------------------------

def build_anchor_css(anchor: dict) -> str:
    """由锚点描述符生成唯一锚定 Playwright 选择器（容器定位的权威实现）。

    - text        : .el-form-item:has(.el-form-item__label:text-is("<text>"))
    - text+attr   : .el-form-item.<业务类名>（业务类名解析时已保证全页唯一）
    - text+ordinal: text 基础式 + ">> nth=<序号>"
    """
    anchor = anchor or {}
    text = anchor.get("text") or ""
    strategy = anchor.get("strategy") or "text"
    quoted = json.dumps(text, ensure_ascii=False)
    base = f'.el-form-item:has(.el-form-item__label:text-is({quoted}))'

    if strategy == "text+attr":
        cls = ((anchor.get("attr") or {}).get("class") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]+", cls or ""):
            return f'.el-form-item.{cls}'
        # 属性不可安全生成 CSS 时退回文本式（若文本歧义，回放解析会显式失败）
        return base
    if strategy == "text+ordinal":
        ordinal = anchor.get("ordinal") or 0
        return f'{base} >> nth={ordinal}'
    return base


async def validate_anchor_css(page, css: str) -> int:
    """记录时校验锚点选择器全页唯一（期望返回 1）。"""
    return await page.locator(css).count()


# ---------------------------------------------------------------------------
# Stage 1 / Stage 2: 从描述符解析锚点容器
# ---------------------------------------------------------------------------

# 按描述符在页面中解析输入项容器（strategy 对应过滤逻辑；不唯一/未找到 → null）
_RESOLVE_ANCHOR_JS = r"""
anchor => {
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    const labelOf = (x) => {
        let l = x.querySelector('.el-form-item__label, .ant-form-item-label label');
        if (!l) l = x.querySelector('label, [class*="label"]');
        return l;
    };
    const items = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item'));
    const matches = items.filter(x => {
        const l = labelOf(x);
        return l && norm(l.textContent) === anchor.text;
    });
    const strategy = anchor.strategy;
    if (strategy === 'text+attr') {
        const cls = (anchor.attr && anchor.attr.class) || '';
        const cand = matches.filter(x => ((x.className || '') + '').split(/\s+/).includes(cls));
        if (cand.length === 1) return cand[0];
        return null;
    }
    if (strategy === 'text+ordinal') {
        const i = (anchor.ordinal === null || anchor.ordinal === undefined) ? 0 : anchor.ordinal;
        return matches[i] || null;
    }
    // strategy 'text': 文本必须唯一
    if (matches.length === 1) return matches[0];
    return null;
}
"""

# 解析失败时的诊断信息（候选数量与各候选标签）
_ANCHOR_DIAG_JS = r"""
anchor => {
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();
    const labelOf = (x) => {
        let l = x.querySelector('.el-form-item__label, .ant-form-item-label label');
        if (!l) l = x.querySelector('label, [class*="label"]');
        return l;
    };
    const items = Array.from(document.querySelectorAll('.el-form-item, .ant-form-item'));
    const matches = items.map(x => ({
        label: labelOf(x) ? norm(labelOf(x).textContent) : '',
        cls: ((x.className || '') + '').slice(0, 60),
    })).filter(m => m.label === anchor.text);
    return { match_count: matches.length, matches: matches.slice(0, 5) };
}
"""


async def resolve_anchor_handle(page, anchor: dict):
    """按描述符解析锚点容器，返回 ElementHandle；失败抛 AnchorResolutionError。"""
    handle = await page.evaluate_handle(_RESOLVE_ANCHOR_JS, anchor)
    el = handle.as_element()
    if el is None:
        try:
            diag = await page.evaluate(_ANCHOR_DIAG_JS, anchor)
        except Exception:
            diag = {}
        raise AnchorResolutionError(
            f"锚点容器未找到: text={anchor.get('text')!r} strategy={anchor.get('strategy')} "
            f"attr={anchor.get('attr')} ordinal={anchor.get('ordinal')}",
            diag)
    return el


async def establish_anchor(page, locator):
    """从组件 Locator 建立输入项锚点：提取描述符 → CSS 唯一性校验 → 解析容器。

    Returns:
        (anchor_descriptor, container_element_handle)
    Raises:
        AnchorResolutionError（每级失败显式抛出，携带诊断）
    """
    raw = await locator.evaluate(_EXTRACT_ANCHOR_JS)
    if not raw or not raw.get("found"):
        raise AnchorResolutionError(
            f"锚点提取失败（组件无输入项容器或标签文本）: "
            f"reason={raw.get('reason') if raw else 'no_result'}",
            {"raw": raw})
    anchor = build_anchor_descriptor(raw)
    css = build_anchor_css(anchor)
    try:
        count = await page.locator(css).count()
    except Exception as e:
        raise AnchorResolutionError(f"锚点 CSS 解析异常: {css} ({e})", {"css": css})
    if count != 1:
        raise AnchorResolutionError(
            f"锚点 CSS 唯一性校验失败: 期望 1 实际 {count}: {css}",
            {"css": css, "count": count, "strategy": anchor.get("strategy")})
    container = await resolve_anchor_handle(page, anchor)
    return anchor, container


# ---------------------------------------------------------------------------
# 容器内相对查询
# ---------------------------------------------------------------------------

async def scoped_query_handle(page, container, css: str):
    """在锚点容器内按 CSS 查询第一个匹配元素（禁止越出容器），返回 ElementHandle 或 None。"""
    handle = await page.evaluate_handle(
        "(args) => args[0].querySelector(args[1])", [container, css])
    return handle.as_element()


# ---------------------------------------------------------------------------
# transfer-box 作用域状态读取（R1 组合判据的唯一权威实现，Stage 1 / Stage 2 共用）
# ---------------------------------------------------------------------------

# 返回: {exists, left_rows: [..], right: {ok, why, text}}
# exists   : 右侧面板存在且可见（组件已渲染）
# left_rows: 左面板行文本快照（radio 换相 diff 用）
# right    : R1 组合判据（计数>0 / 移除按钮 / 排除空态文案后非空）——
#            注意计数正则兼容"已选择"与"已选项"两种文案头（本应用为"已选项"）
_READ_BOX_JS = r"""
args => {
    const [container, excludeTexts] = args;
    const norm = s => (s || '').replace(/\s+/g, ' ').trim();

    const left = container.querySelector(
        '[class*="transfer-box-left"], [class*="transfer-left"], .el-transfer-panel:first-child');
    const right = container.querySelector(
        '[class*="transfer-box-right"], [class*="transfer-right"], .el-transfer-panel:last-child');

    let leftRows = [];
    if (left) {
        leftRows = Array.from(left.querySelectorAll('li, tr, .el-checkbox'))
            .map(r => norm(r.textContent)).filter(Boolean).slice(0, 20);
    }

    const rightState = (() => {
        if (!right) return { ok: false, why: 'right_panel_not_found' };
        const r = right.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return { ok: false, why: 'right_panel_not_visible' };
        const text = (right.textContent || '').trim();
        if ((excludeTexts || []).some(x => text.includes(x))) {
            return { ok: false, why: 'right_panel_no_data', text: text.substring(0, 50) };
        }
        const countMatch = text.match(/已选[择项]\s*[（(]\s*(\d+)\s*[项个]/);
        if (countMatch) {
            return parseInt(countMatch[1], 10) > 0
                ? { ok: true, why: 'right_panel_count_positive', text: text.substring(0, 60) }
                : { ok: false, why: 'right_panel_count_zero', text: text.substring(0, 60) };
        }
        if (right.querySelector('[class*="remove"], button') && text.includes('移除')) {
            return { ok: true, why: 'right_panel_has_remove', text: text.substring(0, 60) };
        }
        return text
            ? { ok: true, why: 'right_panel_has_content', text: text.substring(0, 60) }
            : { ok: false, why: 'right_panel_empty' };
    })();

    const rr = right ? right.getBoundingClientRect() : null;
    return {
        exists: !!(right && rr.width > 0 && rr.height > 0),
        left_rows: leftRows,
        right: rightState,
    };
}
"""


async def read_transfer_box_state(page, container, exclude_texts=("暂无数据", "无数据")) -> dict:
    """读取锚点容器内 transfer-box 状态（R1 组合判据权威实现）。"""
    try:
        state = await page.evaluate(_READ_BOX_JS, [container, list(exclude_texts or [])])
        return state or {"exists": False, "left_rows": [], "right": {"ok": False, "why": "probe_failed"}}
    except Exception as e:
        LOG.warning(f"read_transfer_box_state 失败: {e}")
        return {"exists": False, "left_rows": [], "right": {"ok": False, "why": f"probe_error: {e}"}}
