#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
滑块缺口识别 - alpha mask 版
核心改进: 读取 PNG 的真实 alpha 通道作为 matchTemplate 的 mask,
         避免 JPEG 丢失透明信息导致拼图块边缘/透明区域干扰匹配。
用法: python identify_gap_alpha.py <bg.png> <block.png>
输出: JSON {"gap_location": int, "confidence": float, "method": str}
"""
import sys
import json
import cv2
import numpy as np


def log(msg):
    print("[DEBUG] %s" % msg, file=sys.stderr)


def load_rgba(path):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError("cannot read %s" % path)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    return img


def crop_alpha_bbox(rgba):
    """按 alpha 通道裁出拼图块的最小外接矩形，返回 (crop_rgba, x_offset, y_offset)"""
    alpha = rgba[:, :, 3]
    ys, xs = np.where(alpha > 10)
    if len(xs) == 0:
        return rgba, 0, 0
    x0, x1 = xs.min(), xs.max() + 1
    y0, y1 = ys.min(), ys.max() + 1
    return rgba[y0:y1, x0:x1], int(x0), int(y0)


def main():
    bg_path, blk_path = sys.argv[1], sys.argv[2]
    bg_rgba = load_rgba(bg_path)
    blk_rgba = load_rgba(blk_path)

    log("bg: %s  block: %s" % (bg_rgba.shape, blk_rgba.shape))
    log("block alpha: min=%d max=%d nonzero=%d/%d" % (
        blk_rgba[:, :, 3].min(), blk_rgba[:, :, 3].max(),
        int((blk_rgba[:, :, 3] > 10).sum()), blk_rgba[:, :, 3].size))

    # 按 alpha 裁剪出拼图块本体
    tpl_rgba, ox, oy = crop_alpha_bbox(blk_rgba)
    log("crop bbox: offset=(%d,%d) size=%s" % (ox, oy, tpl_rgba.shape))

    bg = bg_rgba[:, :, :3]
    tpl = tpl_rgba[:, :, :3]
    mask = tpl_rgba[:, :, 3]

    if tpl.shape[0] > bg.shape[0] or tpl.shape[1] > bg.shape[1]:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "template_too_large"}))
        return

    results = {}

    # 硬 mask（二值化 alpha）
    hard = (mask > 128).astype(np.uint8) * 255
    mask3 = cv2.cvtColor(hard, cv2.COLOR_GRAY2BGR)

    # --- 方法1: 彩色 TM_CCORR_NORMED + mask（官方推荐组合）---
    try:
        r = cv2.matchTemplate(bg, tpl, cv2.TM_CCORR_NORMED, mask=mask3)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        _, mx, _, ml = cv2.minMaxLoc(r)
        results["color_ccorr_mask"] = (ml[0] + ox, float(mx))
    except Exception as e:
        log("color_ccorr_mask fail: %s" % e)

    # --- 方法2: 灰度 TM_CCORR_NORMED + mask ---
    try:
        bgg = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
        tpg = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        r = cv2.matchTemplate(bgg, tpg, cv2.TM_CCORR_NORMED, mask=hard)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        _, mx, _, ml = cv2.minMaxLoc(r)
        results["gray_ccorr_mask"] = (ml[0] + ox, float(mx))
    except Exception as e:
        log("gray_ccorr_mask fail: %s" % e)

    # --- 方法3: TM_SQDIFF_NORMED + mask（取最小）---
    try:
        r = cv2.matchTemplate(bg, tpl, cv2.TM_SQDIFF_NORMED, mask=mask3)
        r = np.nan_to_num(r, nan=1.0, posinf=1.0, neginf=1.0)
        mn, _, ml, _ = cv2.minMaxLoc(r)
        results["sqdiff_mask"] = (ml[0] + ox, float(1.0 - mn))
    except Exception as e:
        log("sqdiff_mask fail: %s" % e)

    # --- 方法4: 边缘 + mask ---
    try:
        bge = cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 60, 180)
        tpe = cv2.Canny(cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), 60, 180)
        tpe = cv2.bitwise_and(tpe, tpe, mask=hard)
        r = cv2.matchTemplate(bge, tpe, cv2.TM_CCOEFF_NORMED)
        r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
        _, mx, _, ml = cv2.minMaxLoc(r)
        results["edge_mask"] = (ml[0] + ox, float(mx))
    except Exception as e:
        log("edge_mask fail: %s" % e)

    # --- 方法5: 缺口暗区检测（不依赖 template）---
    # 缺口通常被半透明白/暗遮罩覆盖，按列统计"异常亮度"峰值
    try:
        gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
        h, w = gray.shape
        tpl_h = tpl.shape[0]
        y0 = max(0, oy)
        y1 = min(h, oy + tpl_h)
        band = gray[y0:y1, :]
        col_std = band.std(axis=0)
        col_mean = band.mean(axis=0)
        # 缺口区域: 方差低（被均匀遮罩）
        score = -col_std + 0.0 * col_mean
        tw = tpl.shape[1]
        kern = np.ones(tw) / tw
        smooth = np.convolve(score, kern, mode='valid')
        # 排除最左侧（拼图块自身所在位置）
        lo = tw
        if len(smooth) > lo:
            idx = int(np.argmax(smooth[lo:])) + lo
            rng = smooth.max() - smooth.min()
            conf = float((smooth[idx] - smooth.min()) / rng) if rng > 1e-6 else 0.0
            results["dark_band"] = (idx, conf * 0.6)
    except Exception as e:
        log("dark_band fail: %s" % e)

    for k in sorted(results):
        loc, conf = results[k]
        log("%-20s loc=%-4d conf=%.4f" % (k, loc, conf))

    if not results:
        print(json.dumps({"gap_location": 0, "confidence": 0.0, "method": "none"}))
        return

    # 投票：位置聚类，票多者胜；同票取置信度高
    locs = [v[0] for v in results.values()]
    clusters = {}
    for k, (loc, conf) in results.items():
        placed = False
        for c in list(clusters):
            if abs(c - loc) <= 8:
                clusters[c].append((k, loc, conf))
                placed = True
                break
        if not placed:
            clusters[loc] = [(k, loc, conf)]

    best_c = max(clusters.items(), key=lambda kv: (len(kv[1]), max(x[2] for x in kv[1])))
    members = best_c[1]
    final_loc = int(round(sum(m[1] for m in members) / len(members)))
    final_conf = max(m[2] for m in members)
    method = "+".join(m[0] for m in members)
    log("vote winner: loc=%d votes=%d method=%s" % (final_loc, len(members), method))

    print(json.dumps({
        "gap_location": final_loc,
        "confidence": round(final_conf, 4),
        "method": method,
        "votes": len(members),
        "all": {k: {"loc": v[0], "conf": round(v[1], 4)} for k, v in results.items()},
    }))


if __name__ == "__main__":
    main()
