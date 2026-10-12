"""
arithmetic.py — ECSO 算术验证码识别

多预处理 + 多模型 ddddocr 识别算术表达式，计算答案。
移植自 login-kit 的 ocr_ecso_captcha.py
"""

import re
import os
import base64
import tempfile
import logging
from collections import Counter
from typing import Optional

LOG = logging.getLogger("captcha.arithmetic")


def _ensure_deps():
    global Image, np, ddddocr
    if 'Image' not in globals():
        from PIL import Image as _Image
        import numpy as _np
        import ddddocr as _ddddocr
        globals()['Image'] = _Image
        globals()['np'] = _np
        globals()['ddddocr'] = _ddddocr


class ArithmeticSolver:
    """ECSO 算术验证码识别器"""

    def __init__(self):
        self._models = None

    def _get_models(self):
        if self._models is None:
            _ensure_deps()
            self._models = {
                'default': ddddocr.DdddOcr(show_ad=False),
                'old': ddddocr.DdddOcr(show_ad=False, old=True),
            }
            try:
                self._models['beta'] = ddddocr.DdddOcr(show_ad=False, beta=True)
            except Exception:
                pass
        return self._models

    def solve(self, img_src: str) -> Optional[str]:
        """识别算术验证码并计算答案

        Args:
            img_src: 图片 base64 data URL 或文件路径

        Returns:
            str: 计算结果，None 表示识别失败
        """
        _ensure_deps()

        img_path = self._to_temp(img_src, ".png")
        try:
            img = Image.open(img_path).convert('RGB')
            return self._eval_image(img)
        except Exception as e:
            LOG.error(f"[arithmetic] 识别异常: {e}")
            return None

    def _eval_image(self, img) -> Optional[str]:
        """多预处理 + 多模型 OCR + 投票"""
        raw = img.convert('RGB')
        w = raw.width

        # 变体：裁掉右侧蓝色 "=?" 噪声区
        variants = [
            raw.crop((0, 0, int(w * 0.80), raw.height)),
            raw.crop((0, 0, int(w * 0.75), raw.height)),
        ]

        answers = []
        for vidx, vimg in enumerate(variants):
            for mname, ocr in self._get_models().items():
                try:
                    text = ocr.classification(vimg)
                except Exception:
                    continue
                ans = self._parse_expr(text)
                LOG.debug(f"  [v{vidx} {mname}] raw={text!r} -> {ans}")
                if ans is not None:
                    answers.append(ans)

        if not answers:
            return None

        best, _ = Counter(answers).most_common(1)[0]
        return str(best)

    def _parse_expr(self, s: str) -> Optional[int]:
        """从 OCR 文本解析算术表达式并计算"""
        if not s:
            return None
        t = re.sub(r'[^0-9+\-×*/xX=？\?]', '', s)
        t = re.sub(r'[=?？]+$', '', t)
        t = t.replace('x', '*').replace('X', '*')
        for pat in (r'^(\d{1,3})\s*([+\-×*/])\s*(\d{1,3})$',
                    r'^(\d{1,3})\s*([+\-×*/])\s*(\d{1,3})'):
            m = re.match(pat, t)
            if m:
                a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
                if op == '+':
                    return a + b
                if op == '-':
                    return a - b
                if op in '×*':
                    return a * b
                if op == '/':
                    return a // b if b else None
        return None

    def _to_temp(self, b64_data, suffix):
        if os.path.isfile(str(b64_data)):
            return str(b64_data)
        data = base64.b64decode(str(b64_data).split(",", 1)[-1])
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path
