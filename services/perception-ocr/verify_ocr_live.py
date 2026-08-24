#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M8 真读验证（自证，无需用户截图）：PIL 合成中文插件图 → RapidOCR 真识别 → parse → plugin.state。
运行: python verify_ocr_live.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'brain'))
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import numpy as np                         # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
import ocr_client                          # noqa: E402
from parse import parse_plugin_text, make_state  # noqa: E402
from brain import Brain                     # noqa: E402
import validate as contract                 # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


def _font(sz):
    for p in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/simsun.ttc'):
        if os.path.exists(p):
            return ImageFont.truetype(p, sz)
    return ImageFont.load_default()


def _synth_plugin_image():
    img = Image.new('RGB', (420, 140), (18, 18, 26))
    d = ImageDraw.Draw(img)
    f = _font(34)
    d.text((14, 12), '距离下播 00:30:00', fill=(238, 238, 238), font=f)
    d.text((14, 74), '被整蛊次数: 7', fill=(238, 238, 238), font=f)
    return np.array(img)

# 1) 合成图真识别
texts = ocr_client.ocr_texts(_synth_plugin_image())
joined = ' '.join(texts)
print(f"    OCR 文本: {texts}")
check('RapidOCR 识别出倒计时文字', '00:30:00' in joined or '30:00' in joined, joined)
check('RapidOCR 识别出整蛊次数', '7' in joined, joined)

# 2) 解析
parsed = parse_plugin_text(texts)
check('解析 countdown_sec=1800', parsed.get('countdown_sec') == 1800, str(parsed))
check('解析 prank_count=7', parsed.get('prank_count') == 7, str(parsed))

# 3) 端到端：plugin.state(倒计时 5 分钟) → brain 提醒
st = make_state({'countdown_sec': 300, 'prank_count': parsed.get('prank_count', 0)}, ts=1)
check('plugin.state 契约合法', not contract.validate_message(st))
acts = Brain().handle(st)
check('剩 5 分钟 → brain 提醒气泡', any(a['type'] == 'show_bubble' and '分钟' in a['data'].get('text', '') for a in acts))

print(f"\n==== 插件 OCR·真读 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
