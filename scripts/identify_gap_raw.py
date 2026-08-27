#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
用接口下发的原始素材识别缺口
  backImage  : 完整背景图（含缺口），JPEG
  slidingImage: 纯净拼图块，PNG 带 alpha
输出 JSON: gap_location(原图 x), bg_w, bg_h, tpl_w, tpl_h, confidence, method
"""
import sys
import json
import cv2
import numpy as np


def log(m):
    print("[DEBUG] %s" % m, file=sys.stderr)


def main():
    bg = cv2.imread(sys.argv[1], cv2.IMREAD_COLOR)
    sl = cv2.imread(sys.argv[2], cv2.IMREAD_UNCHANGED)
    if bg is None or sl is None:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "read_fail"}))
        return

    log("back: %s  slide: %s" % (bg.shape, sl.shape))

    if sl.ndim == 3 and sl.shape[2] == 4:
        alpha = sl[:, :, 3]
        tpl_full = sl[:, :, :3]
    else:
        alpha = np.full(sl.shape[:2], 255, np.uint8)
        tpl_full = sl if sl.ndim == 3 else cv2.cvtColor(sl, cv2.COLOR_GRAY2BGR)

    nz = int((alpha > 10).sum())
    log("alpha nonzero = %d / %d (%.1f%%)" % (nz, alpha.size, 100.0 * nz / alpha.size))

    # 裁到 alpha 的最小外接矩形
    ys, xs = np.where(alpha > 10)
    if len(xs) == 0:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "empty_alpha"}))
        return
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    tpl = tpl_full[y0:y1, x0:x1]
    msk = alpha[y0:y1, x0:x1]
    log("tpl bbox: x=[%d,%d) y=[%d,%d) -> %dx%d" % (x0, x1, y0, y1, x1 - x0, y1 - y0))

    BH, BW = bg.shape[:2]
    th, tw = tpl.shape[:2]
    if th > BH or tw > BW:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "tpl_too_large"}))
        return

    hard = (msk > 128).astype(np.uint8) * 255
    mask3 = cv2.cvtColor(hard, cv2.COLOR_GRAY2BGR)

    results = {}

    # 缺口是被"挖空/遮罩"的区域：拼图块原始纹理已不在原处，
    # 所以直接用彩色模板匹配往往错。这里主打"形状/边缘"特征。

    # 1) 边缘匹配：缺口边界会留下与拼图块轮廓一致的强边缘
    try:
        bge = cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 50, 150)
        # 模板用 alpha 的轮廓（拼图块形状边缘）
        cont = cv2.Canny(hard, 50, 150)
        r = cv2.matchTemplate(bge, cont, cv2.TM_CCOEFF_NORMED)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        _, mx, _, ml = cv2.minMaxLoc(r)
        results["shape_edge"] = (int(ml[0]), float(mx))
    except Exception as e:
        log("shape_edge fail: %s" % e)

    # 2) 掩膜内亮度异常（遮罩通常提亮或压暗）
    try:
        gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
        mf = (hard > 0).astype(np.float32)
        area = mf.sum()
        # 掩膜内均值（滑动）
        inside = cv2.filter2D(gray, -1, mf / area, borderType=cv2.BORDER_CONSTANT)
        # 外框均值
        box = np.ones((th, tw), np.float32) / (th * tw)
        outside = cv2.filter2D(gray, -1, box, borderType=cv2.BORDER_CONSTANT)
        diff = inside - outside
        band = diff[y0 + th // 2: y0 + th // 2 + 1, :] if y0 + th // 2 < BH else diff[BH // 2: BH // 2 + 1, :]
        # 全图找极值（取绝对值最大）
        h2 = slice(max(0, th // 2), min(BH, BH - th // 2))
        sub = diff[h2, :]
        idx = np.unravel_index(np.argmax(np.abs(sub)), sub.shape)
        gx = int(idx[1] - tw // 2)
        rng = float(np.abs(sub).max())
        results["mask_lum"] = (max(0, gx), min(1.0, rng / 60.0))
    except Exception as e:
        log("mask_lum fail: %s" % e)

    # 3) 带 mask 的彩色匹配（部分实现缺口只是半透明覆盖，纹理仍在）
    try:
        r = cv2.matchTemplate(bg, tpl, cv2.TM_CCORR_NORMED, mask=mask3)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        _, mx, _, ml = cv2.minMaxLoc(r)
        results["color_mask"] = (int(ml[0]), float(mx))
    except Exception as e:
        log("color_mask fail: %s" % e)

    # 4) 灰度梯度：缺口左右边界形成竖直强梯度对
    try:
        g = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        gx_ = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        col = np.abs(gx_).sum(axis=0)
        col = col - col.mean()
        # 缺口宽 tw：左边界正峰 + 右边界正峰
        kern = np.zeros(tw)
        kern[0:3] = 1
        kern[-3:] = 1
        sc = np.convolve(col, kern[::-1], mode='valid')
        i = int(np.argmax(sc))
        rng = float(sc.max() - sc.min())
        conf = float((sc[i] - sc.min()) / rng) if rng > 1e-6 else 0.0
        results["grad_pair"] = (i, conf * 0.8)
    except Exception as e:
        log("grad_pair fail: %s" % e)

    for k in sorted(results):
        log("%-14s loc=%-4d conf=%.4f" % (k, results[k][0], results[k][1]))

    if not results:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "none"}))
        return

    # 聚类投票
    clusters = {}
    for k, (loc, conf) in results.items():
        hit = None
        for c in clusters:
            if abs(c - loc) <= 10:
                hit = c
                break
        clusters.setdefault(hit if hit is not None else loc, []).append((k, loc, conf))

    best = max(clusters.values(), key=lambda ms: (len(ms), max(m[2] for m in ms)))
    loc = int(round(sum(m[1] for m in best) / len(best)))
    conf = max(m[2] for m in best)
    method = "+".join(m[0] for m in best)
    log("winner loc=%d votes=%d %s" % (loc, len(best), method))

    print(json.dumps({
        "gap_location": loc,
        "confidence": round(conf, 4),
        "method": method,
        "votes": len(best),
        "bg_w": BW, "bg_h": BH,
        "tpl_w": tw, "tpl_h": th,
        "tpl_off_x": x0, "tpl_off_y": y0,
        "all": {k: {"loc": v[0], "conf": round(v[1], 4)} for k, v in results.items()},
    }))


if __name__ == "__main__":
    main()
