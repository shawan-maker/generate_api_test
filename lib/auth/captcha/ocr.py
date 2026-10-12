"""
ocr.py — 字符验证码 OCR

使用 ddddocr 识别字符验证码（天枢等平台）。
移植自 login-kit 的 ocr_captcha.py
"""

import os
import base64
import tempfile
import logging
from typing import Optional

LOG = logging.getLogger("captcha.ocr")


def _ensure_deps():
    global ddddocr
    if 'ddddocr' not in globals():
        import ddddocr as _ddddocr
        globals()['ddddocr'] = _ddddocr


class OcrSolver:
    """字符验证码 OCR 识别器"""

    def __init__(self):
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            _ensure_deps()
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        return self._ocr

    def solve(self, img_data) -> Optional[str]:
        """识别字符验证码

        Args:
            img_data: 图片 bytes 或 base64 字符串或文件路径

        Returns:
            str: 识别结果，None 表示失败
        """
        _ensure_deps()

        if isinstance(img_data, bytes):
            raw = img_data
        elif os.path.isfile(str(img_data)):
            with open(str(img_data), 'rb') as f:
                raw = f.read()
        else:
            raw = base64.b64decode(str(img_data).split(",", 1)[-1])

        try:
            code = self._get_ocr().classification(raw)
            return (code or '').strip()
        except Exception as e:
            LOG.error(f"[ocr] 识别异常: {e}")
            return None
