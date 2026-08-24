#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""游戏画面·被吓反应 验证：合成帧 → 亮度突变检测 → game.scare → brain 被吓反应。
纯 numpy/标准库、无需真实截图/API key。运行: python verify_game.py  退出码 0=全过。"""
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
import numpy as np                                  # noqa: E402
from scare import ScareDetector, frame_luminance    # noqa: E402
from brain import Brain                              # noqa: E402
import validate as contract                          # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


# 1) frame_luminance：暗帧≈0，亮帧≈1
dark = np.full((40, 40, 4), 10, dtype=np.uint8)
bright = np.full((40, 40, 4), 240, dtype=np.uint8)
check('暗帧亮度≈0', frame_luminance(dark) < 0.1, f"{frame_luminance(dark):.3f}")
check('亮帧亮度≈1', frame_luminance(bright) > 0.9, f"{frame_luminance(bright):.3f}")

# 2) 稳定画面不误触
det = ScareDetector()
steady = [det.push(0.4) for _ in range(10)]
check('稳定亮度不触发被吓', all(v == 0 for v in steady), str(steady[:3]))

# 3) 突然闪光/变暗 → 触发
det = ScareDetector()
det.push(0.4)
flash = det.push(0.95)              # 突然闪亮
check('突然闪光触发被吓 intensity>0', flash > 0, str(flash))
det2 = ScareDetector()
det2.push(0.5)
dark_jump = det2.push(0.05)         # 突然变黑
check('突然变黑触发被吓', dark_jump > 0, str(dark_jump))

# 4) 冷却：紧接的突变不连发
det3 = ScareDetector(cooldown_frames=5)
det3.push(0.4)
first = det3.push(0.95)
second = det3.push(0.1)             # 冷却期内
check('冷却期内不连发', first > 0 and second == 0, f"{first},{second}")

# 5) 端到端：合成帧序列 → 检测 → game.scare → brain → 被吓动作
det4 = ScareDetector()
brain = Brain()
frames = [dark] * 5 + [bright] + [dark] * 3        # 第6帧突亮
actions = []
ts = 0
for f in frames:
    ts += 100
    inten = det4.push(frame_luminance(f))
    if inten > 0:
        ev = {"channel": "perception", "type": "game.scare", "ts": ts, "source": "perception.game", "data": {"intensity": inten}}
        assert not contract.validate_message(ev)
        actions += brain.handle(ev)
motions = [a['data'].get('motion') for a in actions if a['type'] == 'play_motion']
check('端到端：恐怖闪帧 → 桌宠 scared', 'scared' in motions, str(motions))

print(f"\n==== 游戏画面·被吓反应 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
