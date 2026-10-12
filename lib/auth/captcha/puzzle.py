"""
puzzle.py — BCov 拼图槽位检测器

v6 泛洪填充算法：从图像四边向内灌，灌不到的"被围住"像素 = 槽位内部。
找到与拼图块 bbox 形状匹配的被围区域，其左缘 = 拖动距离。

移植自 login-kit 的 detect_bcov_slot.py
"""

import os
import base64
import tempfile
import logging
from collections import deque
from typing import Optional

LOG = logging.getLogger("captcha.puzzle")


def _ensure_deps():
    global Image, ImageFilter, ImageDraw, np
    if 'Image' not in globals():
        from PIL import Image as _Image, ImageFilter as _ImageFilter, ImageDraw as _ImageDraw
        import numpy as _np
        globals()['Image'] = _Image
        globals()['ImageFilter'] = _ImageFilter
        globals()['ImageDraw'] = _ImageDraw
        globals()['np'] = _np


class PuzzleSolver:
    """BCov 拼图槽位检测器"""

    def detect(self, back_b64: str, piece_b64: str, y_height: int = 0) -> int:
        """检测拼图槽位 X 坐标

        Args:
            back_b64: 背景图 base64（JPEG）
            piece_b64: 拼图块 base64（PNG 带 alpha）
            y_height: 拼图块的 Y 坐标

        Returns:
            int: 槽位 X 坐标，-1 表示检测失败
        """
        _ensure_deps()

        back_path = self._to_temp(back_b64, ".jpg")
        piece_path = self._to_temp(piece_b64, ".png")

        try:
            return self._detect_slot(back_path, piece_path, y_height)
        except Exception as e:
            LOG.error(f"[puzzle] 检测异常: {e}")
            return -1

    def _detect_slot(self, back_path, piece_path, y_height) -> int:
        """v6 flood-fill 检测"""
        back = Image.open(back_path).convert('RGB')
        piece = Image.open(piece_path).convert('RGBA')
        back_arr = np.array(back)
        BH, BW = back_arr.shape[:2]

        # 提取拼图块 bbox
        piece_arr = np.array(piece)
        alpha = piece_arr[:, :, 3]
        ys, xs = np.where(alpha > 128)
        if len(ys) == 0:
            return -1
        px_min, px_max = int(xs.min()), int(xs.max())
        py_min, py_max = int(ys.min()), int(ys.max())
        piece_w = px_max - px_min + 1
        piece_h = py_max - py_min + 1

        # 白色像素图（luminance > 220）
        L = back_arr.mean(axis=2)
        white = (L > 220).astype(np.uint8) * 255
        from PIL import ImageFilter as IF
        w_img = Image.fromarray(white).filter(IF.MaxFilter(5))
        white_arr = (np.array(w_img) > 128).astype(np.uint8)

        # 泛洪填充：从四边向内灌
        enclosed = self._flood_enclosed(white_arr)

        # 连通分量
        label, n = self._connected_components(enclosed)

        # 找形状匹配的区域
        best_left = -1
        best_dist = float('inf')
        for cid in range(1, n + 1):
            ys_c, xs_c = np.where(label == cid)
            if len(xs_c) < 30:
                continue
            x0, x1 = int(xs_c.min()), int(xs_c.max())
            y0, y1 = int(ys_c.min()), int(ys_c.max())
            w, h = x1 - x0 + 1, y1 - y0 + 1
            dist = abs(w - piece_w) + abs(h - piece_h)
            if dist < best_dist:
                best_dist = dist
                best_left = x0

        return best_left

    def _flood_enclosed(self, white):
        """泛洪填充：从图像四边向内灌，找到被白色围住的区域"""
        BH, BW = white.shape
        visited = np.zeros((BH, BW), dtype=np.uint8)
        q = deque()

        # 从四边开始
        for x in range(BW):
            for y in (0, BH - 1):
                if white[y, x] == 0 and not visited[y, x]:
                    visited[y, x] = 1
                    q.append((y, x))
        for y in range(BH):
            for x in (0, BW - 1):
                if white[y, x] == 0 and not visited[y, x]:
                    visited[y, x] = 1
                    q.append((y, x))

        while q:
            y, x = q.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < BH and 0 <= nx < BW and white[ny, nx] == 0 and not visited[ny, nx]:
                    visited[ny, nx] = 1
                    q.append((ny, nx))

        return (white == 0) & (visited == 0)

    def _connected_components(self, binary):
        """连通分量标记"""
        BH, BW = binary.shape
        label = np.zeros((BH, BW), dtype=np.int32)
        comp_id = 0
        for y0 in range(BH):
            for x0 in range(BW):
                if binary[y0, x0] and label[y0, x0] == 0:
                    comp_id += 1
                    q = deque([(y0, x0)])
                    label[y0, x0] = comp_id
                    while q:
                        y, x = q.popleft()
                        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < BH and 0 <= nx < BW and binary[ny, nx] and label[ny, nx] == 0:
                                label[ny, nx] = comp_id
                                q.append((ny, nx))
        return label, comp_id

    def _to_temp(self, b64_data, suffix):
        """base64 -> 临时文件"""
        if os.path.isfile(str(b64_data)):
            return str(b64_data)
        data = base64.b64decode(str(b64_data).split(",", 1)[-1])
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path
