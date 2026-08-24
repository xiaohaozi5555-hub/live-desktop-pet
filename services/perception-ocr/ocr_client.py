#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件 OCR 客户端：截插件区 → RapidOCR 出文字 → parse → 发 perception.plugin.state。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
from parse import parse_plugin_text, make_state   # noqa: E402

_OCR = None


def _get_ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def ocr_texts(image):
    """图像(numpy 数组 或 路径) → 文本行列表。用 RapidOCR。"""
    result, _ = _get_ocr()(image)
    return [line[1] for line in (result or [])]


def run(regions=None, port=8765, interval=1.0):
    """实时：定时截插件区 → OCR → parse → 发 plugin.state（变化才发）。"""
    import mss
    import numpy as np
    from bus_client import BusClient
    bus = BusClient(port=port, source='perception.ocr').connect()
    last = None
    print(f"[ocr] 插件区 OCR 中… ({len(regions or [])} 块)")
    with mss.mss() as sct:
        while True:
            texts = []
            for r in (regions or []):
                texts += ocr_texts(np.asarray(sct.grab(r)))
            parsed = parse_plugin_text(texts)
            if parsed and parsed != last:
                bus.publish(make_state(parsed, int(time.time() * 1000)))
                print(f"  plugin.state: {parsed}")
                last = parsed
            time.sleep(interval)
