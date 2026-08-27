"""
滑块验证码求解 —— 直接复用 EcsCloud scripts/identify_gap.py 的纯函数(多算法投票)，
并补充浏览器端缺口几何读取、拖拽距离换算、拟人化拖拽三个辅助函数。

设计原则(沿用 EcsCloud): 不重写已验证的逻辑，只在原基础上加薄包装。

注意: cv2/numpy 在函数内部按需导入（避免 import auth 时就触发 cv2 加载）。
"""
import sys
import json
import base64
import os
import tempfile

# cv2/numpy 延迟加载（仅在实际用到这些库的函数中导入）
def _ensure_cv2():
    """确保 cv2 和 numpy 可用，仅在第一次调用时导入。"""
    global cv2, np
    if 'cv2' not in globals():
        import cv2 as _cv2
        import numpy as _np
        globals()['cv2'] = _cv2
        globals()['np'] = _np


# ===================== 以下 identify_gap / _clean 原样复用 EcsCloud =====================
def identify_gap(background_path, gap_path):
    """
    识别滑块验证码的缺口位置
    改进版 - 添加多个匹配方法和预处理
    """
    _ensure_cv2()
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
    def crop_to_content(img, thresh=245):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        m = g < thresh
        if not m.any():
            return img
        ys, xs = np.where(m)
        return img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    tp_crop = crop_to_content(tp_rgb)

    results = {}

    bg_gray = cv2.cvtColor(bg_img, cv2.COLOR_BGR2GRAY)
    tp_gray = cv2.cvtColor(tp_rgb, cv2.COLOR_BGR2GRAY)
    bg_blur = cv2.GaussianBlur(bg_gray, (3, 3), 0)
    tp_blur = cv2.GaussianBlur(tp_gray, (3, 3), 0)
    bg_edge = cv2.Canny(bg_blur, 50, 150)
    tp_edge = cv2.Canny(tp_blur, 50, 150)
    bg_edge_rgb = cv2.cvtColor(bg_edge, cv2.COLOR_GRAY2RGB)
    tp_edge_rgb = cv2.cvtColor(tp_edge, cv2.COLOR_GRAY2RGB)

    tp_crop_gray = cv2.cvtColor(tp_crop, cv2.COLOR_BGR2GRAY) if tp_crop.ndim == 3 else tp_crop
    tp_crop_edge = cv2.Canny(cv2.GaussianBlur(tp_crop_gray, (3, 3), 0), 50, 150)

    res_ce = cv2.matchTemplate(bg_edge, tp_crop_edge, cv2.TM_CCOEFF_NORMED)
    _, max_val_ce, _, max_loc_ce = cv2.minMaxLoc(res_ce)
    results['cropped_edge'] = {"loc": int(max_loc_ce[0]), "conf": float(max_val_ce)}

    res_cc = cv2.matchTemplate(bg_img, tp_crop, cv2.TM_CCOEFF_NORMED)
    _, max_val_cc, _, max_loc_cc = cv2.minMaxLoc(res_cc)
    results['cropped_color'] = {"loc": int(max_loc_cc[0]), "conf": float(max_val_cc)}

    res1 = cv2.matchTemplate(bg_edge_rgb, tp_edge_rgb, cv2.TM_CCOEFF_NORMED)
    _, max_val1, _, max_loc1 = cv2.minMaxLoc(res1)
    results['edge_ccoeff'] = {"loc": int(max_loc1[0]), "conf": float(max_val1)}

    res2 = cv2.matchTemplate(bg_edge_rgb, tp_edge_rgb, cv2.TM_CCORR_NORMED)
    _, max_val2, _, max_loc2 = cv2.minMaxLoc(res2)
    results['edge_ccorr'] = {"loc": int(max_loc2[0]), "conf": float(max_val2)}

    res3 = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCOEFF_NORMED, mask=mask)
    _, max_val3, _, max_loc3 = cv2.minMaxLoc(res3)
    results['gray_ccoeff'] = {"loc": int(max_loc3[0]), "conf": float(max_val3)}

    res4 = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCORR_NORMED, mask=mask)
    _, max_val4, _, max_loc4 = cv2.minMaxLoc(res4)
    results['gray_ccorr'] = {"loc": int(max_loc4[0]), "conf": float(max_val4)}

    computed_mask = (cv2.cvtColor(tp_rgb, cv2.COLOR_BGR2GRAY) < 240).astype(np.uint8) * 255
    res_cm = cv2.matchTemplate(bg_gray, tp_gray, cv2.TM_CCORR_NORMED, mask=computed_mask)
    _, max_val_cm, _, max_loc_cm = cv2.minMaxLoc(res_cm)
    results['computed_mask_gray'] = {"loc": int(max_loc_cm[0]), "conf": float(max_val_cm)}

    tp_crop_mask = (tp_crop_gray < 240).astype(np.uint8) * 255
    res_cm_e = cv2.matchTemplate(bg_edge, tp_crop_edge, cv2.TM_CCORR_NORMED, mask=tp_crop_mask)
    _, max_val_cm_e, _, max_loc_cm_e = cv2.minMaxLoc(res_cm_e)
    results['computed_mask_edge'] = {"loc": int(max_loc_cm_e[0]), "conf": float(max_val_cm_e)}

    res5 = cv2.matchTemplate(cv2.equalizeHist(bg_gray), cv2.equalizeHist(tp_gray), cv2.TM_CCOEFF_NORMED)
    _, max_val5, _, max_loc5 = cv2.minMaxLoc(res5)
    results['eq_ccoeff'] = {"loc": int(max_loc5[0]), "conf": float(max_val5)}

    if mask is not None:
        tp_edge_masked = cv2.bitwise_and(tp_edge, tp_edge, mask=mask)
        res6 = cv2.matchTemplate(bg_edge, tp_edge_masked, cv2.TM_CCOEFF_NORMED)
        _, max_val6, _, max_loc6 = cv2.minMaxLoc(res6)
        results['masked_edge'] = {"loc": int(max_loc6[0]), "conf": float(max_val6)}

    res7 = cv2.matchTemplate(bg_img, tp_rgb, cv2.TM_CCOEFF_NORMED)
    _, max_val7, _, max_loc7 = cv2.minMaxLoc(res7)
    results['color_ccoeff'] = {"loc": int(max_loc7[0]), "conf": float(max_val7)}

    mid_start = h_bg // 3
    mid_end = h_bg * 2 // 3
    strip = bg_gray[mid_start:mid_end, :]
    col_avg = np.mean(strip, axis=0)
    grad = np.abs(np.diff(col_avg))
    grad_smooth = cv2.GaussianBlur(grad.reshape(1, -1).astype(np.float32), (1, 11), 0).flatten()
    gap_candidate = int(np.argmax(grad_smooth))
    if 40 <= gap_candidate <= w_bg - w_tp:
        results['gradient'] = {"loc": gap_candidate, "conf": 0.25 + (grad_smooth[gap_candidate] / max(grad_smooth[gap_candidate] + 1e-6, 0.001)) * 0.30}

    bg_hsv = cv2.cvtColor(bg_img, cv2.COLOR_BGR2HSV)
    sat = bg_hsv[:, :, 1]
    strip_sat = sat[mid_start:mid_end, :]
    sat_avg = np.mean(strip_sat, axis=0)
    sat_grad = np.abs(np.diff(sat_avg))
    sat_grad_smooth = cv2.GaussianBlur(sat_grad.reshape(1, -1).astype(np.float32), (1, 11), 0).flatten()
    sat_candidate = int(np.argmax(sat_grad_smooth))
    if 40 <= sat_candidate <= w_bg - w_tp:
        results['sat_gradient'] = {"loc": sat_candidate, "conf": 0.22 + (sat_grad_smooth[sat_candidate] / max(sat_grad_smooth[sat_candidate] + 1e-6, 0.001)) * 0.25}

    # 多算法投票：位置聚类，票多者胜；同票取置信度高
    clusters = {}
    for method_name, result in results.items():
        loc = result['loc']
        conf = result['conf']
        placed = False
        for cluster_center in list(clusters):
            if abs(cluster_center - loc) <= 8:  # 8像素内视为同一位置
                clusters[cluster_center].append({"method": method_name, "loc": loc, "conf": conf})
                placed = True
                break
        if not placed:
            clusters[loc] = [{"method": method_name, "loc": loc, "conf": conf}]

    # 选择票数最多的聚类；同票取置信度高
    best_cluster_center = max(clusters, key=lambda c: (len(clusters[c]), max(m['conf'] for m in clusters[c])))
    members = clusters[best_cluster_center]
    final_loc = int(round(sum(m['loc'] for m in members) / len(members)))
    final_conf = max(m['conf'] for m in members)
    chosen_methods = "+".join(m['method'] for m in members)

    return {
        "gap_location": final_loc,
        "confidence": final_conf,
        "chosen_method": chosen_methods,
        "votes": len(members),
        "all_results": {k: {"loc": int(v['loc']), "conf": round(float(v['conf']), 4)} for k, v in results.items()}
    }


def _clean(obj):
    _ensure_cv2()
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


# ===================== 薄包装: 供浏览器端调用 =====================
def _to_temp_file(b64_or_path, suffix):
    """base64 字符串或文件路径 -> 临时文件路径。"""
    if isinstance(b64_or_path, (bytes, bytearray)):
        data = bytes(b64_or_path)
    elif os.path.isfile(str(b64_or_path)):
        return str(b64_or_path)
    else:
        data = base64.b64decode(str(b64_or_path).split(",", 1)[-1])
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def solve_slider(background, gap):
    """
    求解滑块缺口。background/gap 可为 文件路径 / base64 字符串 / bytes。
    返回: {gap_location, confidence, bg_w, tpl_w, chosen_method}
    """
    _ensure_cv2()
    bg_path = _to_temp_file(background, ".jpg")
    gap_path = _to_temp_file(gap, ".png")
    res = identify_gap(bg_path, gap_path)
    bg = cv2.imread(bg_path)
    tp = cv2.imread(gap_path, cv2.IMREAD_UNCHANGED)
    res["bg_w"] = int(bg.shape[1]) if bg is not None else 0
    res["tpl_w"] = int(tp.shape[1]) if tp is not None else 0
    return _clean(res)


def read_slider_geo(page):
    """
    读取可见 #slideVerify 的几何信息。返回:
    {ok, dispW, dispH, handle:{x,y,w,h}, trackW, reason}
    """
    js = r"""
    () => {
      const svs = Array.prototype.slice.call(document.querySelectorAll('#slideVerify'));
      const sv = svs.find(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
      if (!sv) return { ok:false, reason:'no_visible_slideVerify', total: svs.length };
      const bg = sv.querySelector('canvas:not(.slide-verify-block)');
      const handle = sv.querySelector('.slide-verify-slider-mask-item');
      const track = sv.querySelector('.slide-verify-slider');
      if (!bg || !handle || !track) return { ok:false, reason:'missing_parts' };
      const rh = handle.getBoundingClientRect();
      const rt = track.getBoundingClientRect();
      const rb = bg.getBoundingClientRect();
      if (rh.width < 1 || rb.width < 1) return { ok:false, reason:'zero_bbox' };
      return { ok:true, dispW: rb.width, dispH: rb.height,
        handle:{x:rh.x, y:rh.y, w:rh.width, h:rh.height}, trackW: rt.width };
    }
    """
    return page.evaluate(js)


def compute_drag_distance(geo, gap_result):
    """把原图缺口 x 换算为滑轨拖拽像素距离(移植自 EcsCloud login.js)。"""
    gap_loc = gap_result.get("gap_location", 0)
    bg_w = gap_result.get("bg_w") or geo.get("dispW") or 1
    tpl_w = gap_result.get("tpl_w") or 41
    scale = geo["dispW"] / bg_w
    gap_disp = gap_loc * scale
    block_disp_w = tpl_w * scale
    max_slide = geo["trackW"] - geo["handle"]["w"]
    max_block = geo["dispW"] - block_disp_w
    base_dist = gap_disp * (max_slide / max_block) if max_block > 0 else gap_disp
    return float(base_dist)


async def human_drag(page, handle_box, distance):
    """拟人化缓动拖拽(移植自 EcsCloud login.js doDrag)。"""
    import asyncio, random
    cx = handle_box["x"] + handle_box["w"] / 2
    cy = handle_box["y"] + handle_box["h"] / 2
    await page.mouse.move(cx, cy, steps=6)
    await asyncio.sleep(0.18 + random.random() * 0.12)
    await page.mouse.down()
    await asyncio.sleep(0.12 + random.random() * 0.08)
    total = 28 + random.randint(0, 9)
    for s in range(1, total + 1):
        p = s / total
        eased = p ** 3 * 4 if p < 0.5 else 1 - ((-2 * p + 2) ** 3) / 2
        y_jit = __import__("math").sin(p * 3.14159 * (2 + random.random())) * (1.2 + random.random() * 1.5)
        await page.mouse.move(cx + eased * distance, cy + y_jit)
        delay = 22 + random.random() * 14 if p < 0.12 else (26 + random.random() * 16 if p > 0.88 else 7 + random.random() * 9)
        await asyncio.sleep(delay / 1000.0)
    overshoot = 1.5 + random.random() * 1.5
    await page.mouse.move(cx + distance + overshoot, cy + (random.random() - 0.5) * 1.2)
    await asyncio.sleep(0.07 + random.random() * 0.04)
    await page.mouse.move(cx + distance, cy + (random.random() - 0.5) * 0.6)
    await asyncio.sleep(0.14 + random.random() * 0.08)
    await page.mouse.up()


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(json.dumps({"error": "Usage: python slider.py <background_path> <gap_path>", "gap_location": 0}))
        sys.exit(0)
    result = solve_slider(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False))
