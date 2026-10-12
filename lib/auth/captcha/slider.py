"""
slider.py — 滑块验证码求解器

复用 EcsCloud 的 identify_gap_raw.py 多算法投票缺口检测，
提供浏览器集成辅助函数（几何读取、距离换算、拖拽）。

cv2/numpy 延迟加载，避免 import 时就触发 cv2 加载。
"""

import sys
import json
import base64
import os
import tempfile
import logging

LOG = logging.getLogger("captcha.slider")


def _ensure_cv2():
    """确保 cv2 和 numpy 可用"""
    global cv2, np
    if 'cv2' not in globals():
        import cv2 as _cv2
        import numpy as _np
        globals()['cv2'] = _cv2
        globals()['np'] = _np


class SliderSolver:
    """滑块验证码求解器"""

    def solve(self, background, gap):
        """求解滑块缺口

        Args:
            background: 背景图（base64 / bytes / 文件路径）
            gap: 拼图块（base64 / bytes / 文件路径，PNG 带 alpha）

        Returns:
            dict: {gap_location, confidence, chosen_method, votes, bg_w, tpl_w}
        """
        bg_path = self._to_temp_file(background, ".jpg")
        gap_path = self._to_temp_file(gap, ".png")
        result = self._identify_gap_raw(bg_path, gap_path)

        _ensure_cv2()
        bg = cv2.imread(bg_path)
        tp = cv2.imread(gap_path, cv2.IMREAD_UNCHANGED)
        result["bg_w"] = int(bg.shape[1]) if bg is not None else 0
        result["bg_h"] = int(bg.shape[0]) if bg is not None else 0
        result["tpl_w"] = int(tp.shape[1]) if tp is not None else 0
        result["tpl_h"] = int(tp.shape[0]) if tp is not None else 0
        return result

    def _identify_gap_raw(self, bg_path, sl_path):
        """多算法投票缺口检测（移植自 identify_gap_raw.py）

        4 种算法：
        1. 边缘匹配（Canny + matchTemplate）
        2. 掩膜亮度异常（filter2D 滑动均值差）
        3. 带 mask 彩色匹配（TM_CCORR_NORMED + mask）
        4. 灰度梯度对（Sobel + 卷积）

        Returns:
            dict: {gap_location, confidence, method, votes, bg_w, bg_h, tpl_w, tpl_h}
        """
        _ensure_cv2()

        bg = cv2.imread(bg_path, cv2.IMREAD_COLOR)
        sl = cv2.imread(sl_path, cv2.IMREAD_UNCHANGED)

        if bg is None or sl is None:
            return {"gap_location": 0, "confidence": 0.0, "method": "read_fail"}

        # 提取 alpha 通道
        if sl.ndim == 3 and sl.shape[2] == 4:
            alpha = sl[:, :, 3]
            tpl_full = sl[:, :, :3]
        else:
            alpha = np.full(sl.shape[:2], 255, np.uint8)
            tpl_full = sl if sl.ndim == 3 else cv2.cvtColor(sl, cv2.COLOR_GRAY2BGR)

        # 裁到 alpha 的最小外接矩形
        ys, xs = np.where(alpha > 10)
        if len(xs) == 0:
            return {"gap_location": 0, "confidence": 0.0, "method": "empty_alpha"}
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        tpl = tpl_full[y0:y1, x0:x1]
        msk = alpha[y0:y1, x0:x1]

        BH, BW = bg.shape[:2]
        th, tw = tpl.shape[:2]
        if th > BH or tw > BW:
            return {"gap_location": 0, "confidence": 0.0, "method": "tpl_too_large"}

        hard = (msk > 128).astype(np.uint8) * 255
        mask3 = cv2.cvtColor(hard, cv2.COLOR_GRAY2BGR)

        results = {}

        # 方法 1: 边缘匹配
        try:
            bge = cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 50, 150)
            cont = cv2.Canny(hard, 50, 150)
            r = cv2.matchTemplate(bge, cont, cv2.TM_CCOEFF_NORMED)
            r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
            _, mx, _, ml = cv2.minMaxLoc(r)
            results["shape_edge"] = (int(ml[0]), float(mx))
        except Exception:
            pass

        # 方法 2: 掩膜亮度异常
        try:
            gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY).astype(np.float32)
            mf = (hard > 0).astype(np.float32)
            area = mf.sum()
            inside = cv2.filter2D(gray, -1, mf / area, borderType=cv2.BORDER_CONSTANT)
            box = np.ones((th, tw), np.float32) / (th * tw)
            outside = cv2.filter2D(gray, -1, box, borderType=cv2.BORDER_CONSTANT)
            diff = inside - outside
            h2 = slice(max(0, th // 2), min(BH, BH - th // 2))
            sub = diff[h2, :]
            idx = np.unravel_index(np.argmax(np.abs(sub)), sub.shape)
            gx = int(idx[1] - tw // 2)
            rng = float(np.abs(sub).max())
            results["mask_lum"] = (max(0, gx), min(1.0, rng / 60.0))
        except Exception:
            pass

        # 方法 3: 带 mask 彩色匹配
        try:
            r = cv2.matchTemplate(bg, tpl, cv2.TM_CCORR_NORMED, mask=mask3)
            r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
            _, mx, _, ml = cv2.minMaxLoc(r)
            results["color_mask"] = (int(ml[0]), float(mx))
        except Exception:
            pass

        # 方法 4: 灰度梯度对
        try:
            g = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
            gx_ = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
            col = np.abs(gx_).sum(axis=0)
            col = col - col.mean()
            kern = np.zeros(tw)
            kern[0:3] = 1
            kern[-3:] = 1
            sc = np.convolve(col, kern[::-1], mode='valid')
            i = int(np.argmax(sc))
            rng = float(sc.max() - sc.min())
            conf = float((sc[i] - sc.min()) / rng) if rng > 1e-6 else 0.0
            results["grad_pair"] = (i, conf * 0.8)
        except Exception:
            pass

        if not results:
            return {"gap_location": 0, "confidence": 0.0, "method": "none"}

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

        return {
            "gap_location": loc,
            "confidence": round(conf, 4),
            "chosen_method": method,
            "votes": len(best),
            "bg_w": BW, "bg_h": BH,
            "tpl_w": tw, "tpl_h": th,
            "tpl_off_x": x0, "tpl_off_y": y0,
        }

    def read_geometry(self, page):
        """读取可见 #slideVerify 的几何信息

        Returns:
            dict: {ok, dispW, dispH, handle:{x,y,w,h}, trackW, reason}
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

    def compute_drag_distance(self, geo, gap_result):
        """把原图缺口 x 换算为滑轨拖拽像素距离

        Args:
            geo: read_geometry() 返回值
            gap_result: solve() 返回值

        Returns:
            float: 拖拽距离（像素）
        """
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

    async def human_drag(self, page, handle_box, distance):
        """拟人化拖拽（委托给 human_drag 模块）"""
        from ..human_drag import human_drag_v2
        await human_drag_v2(page, handle_box, distance)

    def _to_temp_file(self, b64_or_path, suffix):
        """base64 字符串或文件路径 -> 临时文件路径"""
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
