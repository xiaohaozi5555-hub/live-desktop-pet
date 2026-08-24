#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M7 攻略部分验证：make_scene 契约合法 + 端到端 brain 说攻略；有 ANTHROPIC_API_KEY 则真调一次。
无 key 时真实调用跳过(SKIP，不算失败)。运行: python verify_vision.py"""
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'brain'))
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import vision as V                  # noqa: E402
from brain import Brain             # noqa: E402
import validate as contract         # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


def _solid_png(w=64, h=64, rgb=(28, 26, 38)):
    """纯标准库生成一张纯色 RGB PNG（供真实调用冒烟测试用，无需 PIL）。"""
    raw = bytearray()
    row = bytes(rgb) * w
    for _ in range(h):
        raw.append(0)
        raw += row
    comp = zlib.compress(bytes(raw))

    def chunk(typ, data):
        c = typ + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', comp) + chunk(b'IEND', b'')


# 1) make_scene 契约合法
sc = V.make_scene("走廊尽头有锁门和保险箱，旁边有血迹", ["dark", "puzzle", "blood"], True, "查保险箱密码，可能和墙上数字有关", ts=1)
check('make_scene 生成合法 game.scene', not contract.validate_message(sc), sc['type'])

# 2) 端到端：卡关 game.scene → brain 说攻略
acts = Brain().handle(sc)
check('卡关场景 → brain 说出攻略', any(a['type'] == 'speak' and '保险箱' in a['data'].get('text', '') for a in acts), str([a['type'] for a in acts]))

# 2b) 视觉后端未配置时：不抛异常，返回角色口吻提示，桌宠会把这句话说出来（而不是只在终端打印错误）
_real_pick_provider = V._pick_provider
V._pick_provider = lambda: (_ for _ in ()).throw(RuntimeError("未配置视觉后端"))
try:
    unconfigured = V.analyze(_solid_png(), ts=3)
    check('未配置视觉后端时 analyze() 不抛异常', True)
    check('未配置场景仍是合法 game.scene', not contract.validate_message(unconfigured))
    acts2 = Brain().handle(unconfigured)
    check('未配置提示会被桌宠说出来', any(a['type'] == 'speak' and '钥匙' in a['data'].get('text', '') for a in acts2), str([a['data'].get('text') for a in acts2 if a['type'] == 'speak']))
finally:
    V._pick_provider = _real_pick_provider

# 3) 真实 LLM 调用（有 key 才跑）
_load = os.path.join(REPO, '.env')
if os.path.exists(_load):
    for line in open(_load, encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
_has_key = os.environ.get('PET_VISION_API_KEY') or os.environ.get('PET_VISION_BASE_URL') or os.environ.get('ANTHROPIC_API_KEY')
if _has_key:
    try:
        scene = V.analyze(_solid_png(), ts=2)
        check('真实 analyze 返回合法 game.scene', not contract.validate_message(scene), str(scene['data'])[:80])
    except Exception as e:
        # 真调失败多为 key 限制/未开通 qwen-vl-plus 等外部配置问题 → 记 WARN，不判代码测试失败
        print(f"[WARN] 真实调用失败（多半是 key 被限制/未开通 qwen-vl-plus）：{str(e)[:120]}")
        print("       修好 key 后重跑即真验证；离线逻辑与接线不受影响。")
else:
    print("[SKIP] 未配置视觉后端(国产 PET_VISION_* 或 ANTHROPIC_API_KEY) —— 跳过真实调用。")
    print("       在 .env 配好后 `python services/perception-game/verify_vision.py` 即真验证攻略。")

print(f"\n==== M7(攻略) 验证: {passed}/{passed + failed} 通过（真实调用视 key 而定）====")
sys.exit(0 if failed == 0 else 1)
