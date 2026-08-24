#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集成验证：真实 TCP 本地总线上 danmaku → brain → (recorder) 端到端，含 mute 闭嘴。
纯标准库、不开播、不装依赖。运行: python scripts/verify_integration.py  退出码 0=全过。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in ('services/bus', 'services/brain', 'services/perception-danmaku', 'packages/contract'):
    sys.path.insert(0, os.path.join(REPO, *p.split('/')))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
from broker import Broker          # noqa: E402
from bus_client import BusClient   # noqa: E402
from brain import Brain            # noqa: E402
import normalize as N              # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


PORT = 8791
broker = Broker(port=PORT).start()
time.sleep(0.2)

actions = []
rec = BusClient(port=PORT, source='recorder')
rec.subscribe(lambda m: actions.append(m) if m.get('channel') == 'action' else None).connect()

brain = Brain()
bc = BusClient(port=PORT, source='brain')
bc.subscribe(lambda m: [bc.publish(a) for a in brain.handle(m)] if m.get('channel') in ('perception', 'command') else None).connect()
time.sleep(0.3)

dan = BusClient(port=PORT, source='danmaku').connect()
events = [json.loads(l) for l in open(os.path.join(REPO, 'fixtures', 'danmaku', 'sample-official.jsonl'), encoding='utf-8') if l.strip()]
for e in events:
    m = N.normalize(e)
    if m:
        dan.publish(m)
        time.sleep(0.05)
time.sleep(0.6)

motions = [a['data'].get('motion') for a in actions if a['type'] == 'play_motion']
speaks = [a['data'].get('text', '') for a in actions if a['type'] == 'speak']
check('总线上收到 action', len(actions) > 0, f"{len(actions)} 条")
check('大礼物 → thank_big 经真实 TCP 到达', 'thank_big' in motions, str(motions))
check('进场 → wave 欢迎经总线到达', 'wave' in motions)
check('答谢语音含礼物文案', any('嘉年华' in s for s in speaks), str([s for s in speaks if s][:2]))

# 闭嘴：mute 后进场不再欢迎
before = len(actions)
ctrl = BusClient(port=PORT, source='panel').connect()
time.sleep(0.2)
ctrl.publish({'channel': 'command', 'type': 'mute', 'ts': 0, 'data': {}})
time.sleep(0.25)
dan.publish(N.normalize({'msg_type': 'member', 'user': {'nick_name': '路人'}, 'timestamp': 0}))
time.sleep(0.4)
post = actions[before:]
post_motions = [a['data'].get('motion') for a in post if a['type'] == 'play_motion']
check('mute 后闭嘴：进场不再 speak/wave（仅 stop）',
      not any(a['type'] == 'speak' for a in post) and 'wave' not in post_motions,
      str([a['type'] for a in post]))

broker.stop()
print(f"\n==== 集成验证(真实TCP总线): {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
