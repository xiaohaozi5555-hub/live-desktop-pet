#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""face 运行器：定时截露脸区 → DeepFace 表情 → 发 perception.face.expression（低帧率）。
区域由 command.calibrate(region=face) 提供，支持 portrait(竖屏下半)/landscape(横屏左下)。需 deepface。"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
from bus_client import BusClient   # noqa: E402
from detect import analyze_face    # noqa: E402


def run(region=None, port=8765, fps=3, layout='portrait'):
    import mss
    import numpy as np
    bus = BusClient(port=port, source='perception.face').connect()
    print(f"[face] 露脸表情识别中… ({fps}fps, {layout})")
    with mss.mss() as sct:
        mon = region or sct.monitors[1]
        while True:
            img = np.asarray(sct.grab(mon))[:, :, :3]
            try:
                bus.publish(analyze_face(img, layout, int(time.time() * 1000)))
            except Exception as e:
                print(f"[face] 识别失败(是否装 deepface / 画面有露脸?): {e}")
            time.sleep(1.0 / fps)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--region', help='top,left,width,height')
    ap.add_argument('--fps', type=int, default=3)
    ap.add_argument('--layout', default='portrait', choices=['portrait', 'landscape'])
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    reg = None
    if a.region:
        t, l, w, h = (int(x) for x in a.region.split(','))
        reg = {"top": t, "left": l, "width": w, "height": h}
    run(region=reg, port=a.port, fps=a.fps, layout=a.layout)
