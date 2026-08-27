import cv2
import numpy as np
import sys
import json

def identify_gap(background_path, gap_path):
    """
    识别滑块验证码的缺口位置
    改进版 - 添加多个匹配方法和预处理
    """
    bg_img = cv2.imread(background_path)
    tp_img = cv2.imread(gap_path, cv2.IMREAD_UNCHANGED)  # 保留 alpha 通道

    if bg_img is None:
        return {"error": f"Background not loaded from {background_path}", "gap_location": 0}
    if tp_img is None:
        return {"error": f"Gap not loaded from {gap_path}", "gap_location": 0}

    h_bg, w_bg = bg_img.shape[:2]
    h_tp, w_tp = tp_img.shape[:2]

    # 如果缺口图有 alpha 通道，提取 alpha 作为 mask
    mask = None
    if tp_img.shape[2] == 4:
        alpha = tp_img[:, :, 3]
        tp_rgb = tp_img[:, :, :3]
        mask = alpha
    else:
        tp_rgb = tp_img

    # ---- 预处理：自动裁剪模板到内容区域，去掉白边 ----
    # JPEG 模板无 alpha 时直接整图匹配会被白边带偏，这是 OpenCV 置信度暴跌的主因。
    def crop_to_content(img, thresh=245):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        m = g < thresh
        if not m.any():
            return img
        ys, xs = np.where(m)
        return img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    tp_crop = crop_to_content(tp_rgb)

    results = {}

    # 方法1: 边缘检测 + 模板匹配 (原始RF方法)
    bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
    tp_gray = cv2.cvtColor(tp_rgb, cv2.COLOR_BGR2GRAY)

    bg_blur = cv2.GaussianBlur(bg_gray, (3, 3), 0)
    tp_blur = cv2.GaussianBlur(tp_gray, (3, 3), 0)
    bg_edge = cv2.Canny(bg_blur, 50, 150)
    tp_edge = cv2.Canny(tp_blur, 50, 150)

    bg_edge_rgb = cv2.cvtColor(bg_edge, cv2.COLOR_GRAY2RGB)
    tp_edge_rgb = cv2.cvtColor(tp_edge, cv2.COLOR_GRAY2RGB)

    # ---- 关键改进: 用裁剪后的模板做边缘 + 彩色匹配（模板无 alpha 时比原图匹配可靠得多）----
    tp_crop_gray = cv2.cvtColor(tp_crop, cv2.COLOR_BGR2GRAY) if tp_crop.ndim == 3 else tp_crop
    tp_crop_edge = cv2.Canny(cv2.GaussianBlur(tp_crop_gray, (3, 3), 0), 50, 150)

    res_ce = cv2.matchTemplate(bg_edge, tp_crop_edge, cv2.TM_CCOEFF_NORMED)
    _, max_val_ce, _, max_loc_ce = cv2.minMaxLoc(res_ce)
    results['cropped_edge'] = {"loc": int(max_loc_ce[0]), "conf": float(max_val_ce)}

    res_cc = cv2.matchTemplate(bg_img, tp_crop, cv2.TM_CCOEFF_NORMED)
    _, max_val_cc, _, max_loc_cc = cv2.minMaxLoc(res_cc)
    results['cropped_color'] = {"loc": int(max_loc_cc[0]), "conf": float(max_val_cc)}

    # TM_CCOEFF_NORMED on original edge
    res1 = cv2.matchTemplate(bg_edge_rgb, tp_edge_rgb, cv2.TM_CCOEFF_NORMED)
    _, max_val1, _, max_loc1 = cv2.minMaxLoc(res1)
    results['edge_ccoeff'] = {"loc": int(max_loc1[0]), "conf": float(max_val1)}

    # TM_CCORR_NORMED (RF中用的)
    res2 = cv2.matchTemplate(bg_edge_rgb, tp_edge_rgb, cv2.TM_CCORR_NORMED)
    _, max_val2, _, max_loc2 = cv2.minMaxLoc(res2)
    results['edge_ccorr'] = {"loc": int(max_loc2[0]), "conf": float(max_val2)}

    # 方法2: 直接灰度匹配
    res3 = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    _, max_val3, _, max_loc3 = cv2.minMaxLoc(res3)
    results['gray_ccoeff'] = {"loc": int(max_loc3[0]), "conf": float(max_val3)}

    res4 = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCORR_NORMED, mask=mask)
    _, max_val4, _, max_loc4 = cv2.minMaxLoc(res4)
    results['gray_ccorr'] = {"loc": int(max_loc4[0]), "conf": float(max_val4)}

    # ---- 关键: 对 JPEG 模板（无 alpha）合成 mask（阈值提取拼图形状），等效 alpha 匹配 ----
    # 用 TM_CCORR_NORMED 保证 OpenCV 各版本都支持 mask
    computed_mask = (cv2.cvtColor(tp_rgb, cv2.COLOR_BGR2GRAY) < 240).astype(np.uint8) * 255
    res_cm = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCORR_NORMED, mask=computed_mask)
    _, max_val_cm, _, max_loc_cm = cv2.minMaxLoc(res_cm)
    results['computed_mask_gray'] = {"loc": int(max_loc_cm[0]), "conf": float(max_val_cm)}

    tp_crop_mask = (tp_crop_gray < 240).astype(np.uint8) * 255
    res_cm_e = cv2.matchTemplate(bg_edge, tp_crop_edge, cv2.TM_CCORR_NORMED, mask=tp_crop_mask)
    _, max_val_cm_e, _, max_loc_cm_e = cv2.minMaxLoc(res_cm_e)
    results['computed_mask_edge'] = {"loc": int(max_loc_cm_e[0]), "conf": float(max_val_cm_e)}

    # 方法3: 直方图均衡化后匹配
    bg_eq = cv2.equalizeHist(bg_gray)
    tp_eq = cv2.equalizeHist(tp_gray)

    res5 = cv2.matchTemplate(bg_eq, tp_eq, cv2.TM_CCOEFF_NORMED)
    _, max_val5, _, max_loc5 = cv2.minMaxLoc(res5)
    results['eq_ccoeff'] = {"loc": int(max_loc5[0]), "conf": float(max_val5)}

    # 方法4: 使用 mask 的边缘匹配（如果有 alpha）
    if mask is not None:
        tp_edge_masked = cv2.bitwise_and(tp_edge, tp_edge, mask=mask)
        res6 = cv2.matchTemplate(bg_edge, tp_edge_masked, cv2.TM_CCOEFF_NORMED)
        _, max_val6, _, max_loc6 = cv2.minMaxLoc(res6)
        results['masked_edge'] = {"loc": int(max_loc6[0]), "conf": float(max_val6)}

    # 方法5: 彩色图像直接匹配（用原始模板）
    res7 = cv2.matchTemplate(bg_img, tp_rgb, cv2.TM_CCOEFF_NORMED)
    _, max_val7, _, max_loc7 = cv2.minMaxLoc(res7)
    results['color_ccoeff'] = {"loc": int(max_loc7[0]), "conf": float(max_val7)}

    # ---- 方法6: 仅基于背景图的色彩差异找缺口（不依赖模板图）----
    mid_start = h_bg // 3
    mid_end = h_bg * 2 // 3
    strip = bg_gray[mid_start:mid_end, :]
    col_avg = np.mean(strip, axis=0)
    grad = np.abs(np.diff(col_avg))
    grad_smooth = cv2.GaussianBlur(grad.reshape(1, -1).astype(np.float32), (1, 11), 0).flatten()
    gap_candidate = int(np.argmax(grad_smooth))
    if 40 <= gap_candidate <= w_bg - w_tp:
        results['gradient'] = {"loc": gap_candidate, "conf": 0.25 + (grad_smooth[gap_candidate] / max(grad_smooth[gap_candidate] + 1e-6, 0.001)) * 0.30}

    # ---- 方法7: 基于饱和度通道找缺口 ----
    bg_hsv = cv2.cvtColor(bg_img, cv2.COLOR_BGR2HSV)
    sat = bg_hsv[:, :, 1]
    strip_sat = sat[mid_start:mid_end, :]
    sat_avg = np.mean(strip_sat, axis=0)
    sat_grad = np.abs(np.diff(sat_avg))
    sat_grad_smooth = cv2.GaussianBlur(sat_grad.reshape(1, -1).astype(np.float32), (1, 11), 0).flatten()
    sat_candidate = int(np.argmax(sat_grad_smooth))
    if 40 <= sat_candidate <= w_bg - w_tp:
        results['sat_gradient'] = {"loc": sat_candidate, "conf": 0.22 + (sat_grad_smooth[sat_candidate] / max(sat_grad_smooth[sat_candidate] + 1e-6, 0.001)) * 0.25}

    # 选择置信度最高的结果
    best_method = max(results, key=lambda k: results[k]['conf'])
    best = results[best_method]

    print(f"[DEBUG] 背景图: {bg_img.shape}, 缺口图: {tp_img.shape}, 裁剪后: {tp_crop.shape}", file=sys.stderr)
    for method, data in sorted(results.items()):
        marker = " <-- BEST" if method == best_method else ""
        print(f"[DEBUG] {method}: loc={data['loc']}, conf={data['conf']:.4f}{marker}", file=sys.stderr)

    return {
        "gap_location": best['loc'],
        "confidence": best['conf'],
        "chosen_method": best_method,
        "all_results": {k: {"loc": int(v['loc']), "conf": round(float(v['conf']), 4)} for k, v in results.items()}
    }


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return _clean(obj.tolist())
    return obj


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: python identify_gap.py <background_path> <gap_path>", "gap_location": 0}))
        sys.exit(0)

    result = _clean(identify_gap(sys.argv[1], sys.argv[2]))
    print(json.dumps(result))