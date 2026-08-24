#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制层验证：命令构造器 + 弹幕关键词映射（含白名单）+ 面板经总线驱动 brain（端到端）。
纯标准库。运行: python verify_control_panel.py  退出码 0=全过。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
for p in ('services/bus', 'services/brain', 'packages/contract'):
    sys.path.insert(0, os.path.join(REPO, *p.split('/')))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import commands as C               # noqa: E402
import keywords as K               # noqa: E402
import validate as contract        # noqa: E402
from broker import Broker          # noqa: E402
from bus_client import BusClient   # noqa: E402
from brain import Brain            # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


# 1) 命令构造器合法
for name, cmd in [('mute', C.mute()), ('do:wave', C.do('wave')),
                  ('calibrate:face', C.calibrate('face', 0, 960, 540, 300, 'portrait'))]:
    check(f'构造器 {name} 通过契约校验', not contract.validate_message(cmd), str(cmd.get('type')))

# 2) 关键词映射（本机可信输入 match()，不做身份校验）
check('关键词 “先闭嘴吧” → mute', (K.match('先闭嘴吧') or {}).get('type') == 'mute')
check('关键词 “帮我挥手” → do wave', (K.match('帮我挥手') or {}).get('data', {}).get('action') == 'wave')
check('无关弹幕 → None', K.match('主播今天真好看') is None)

# 2b) 弹幕关键词身份白名单（match_danmaku：只有白名单昵称才生效，防任意观众打关键词捣乱）
owner = K.STREAMER_NAMES[0]
check('白名单昵称打关键词 → 生效', (K.match_danmaku('先闭嘴吧', owner) or {}).get('type') == 'mute')
check('非白名单路人打同样关键词 → 不生效', K.match_danmaku('先闭嘴吧', '随便一个路人') is None)
check('空昵称 → 不生效', K.match_danmaku('先闭嘴吧', '') is None)
check('is_authorized 对白名单昵称返回 True', K.is_authorized(owner) is True)
check('is_authorized 对路人返回 False', K.is_authorized('随便一个路人') is False)

# 3) 端到端：面板 command → 总线 → brain → action
PORT = 8793
broker = Broker(port=PORT).start()
time.sleep(0.2)
actions = []
rec = BusClient(port=PORT, source='rec')
rec.subscribe(lambda m: actions.append(m) if m.get('channel') == 'action' else None).connect()
brain = Brain()
bc = BusClient(port=PORT, source='brain')
bc.subscribe(lambda m: [bc.publish(a) for a in brain.handle(m)] if m.get('channel') in ('perception', 'command') else None).connect()
time.sleep(0.3)
panel = BusClient(port=PORT, source='control.panel').connect()

panel.publish(C.do('wave'))
time.sleep(0.3)
check('面板 do:wave → 角色 wave', any(a['type'] == 'play_motion' and a['data'].get('motion') == 'wave' for a in actions))

panel.publish(C.mute())
time.sleep(0.2)
n = len(actions)
panel.publish({'channel': 'perception', 'type': 'danmaku.enter', 'ts': 0, 'data': {'user': '路人'}})
time.sleep(0.3)
post = actions[n:]
check('面板 mute 后进场不再欢迎（闭嘴）', not any(a['type'] == 'speak' for a in post), str([a['type'] for a in post]))

panel.publish(K.match('可以说话了'))   # unmute via keyword
time.sleep(0.2)
n = len(actions)
panel.publish({'channel': 'perception', 'type': 'danmaku.enter', 'ts': 0, 'data': {'user': '路人B'}})
time.sleep(0.3)
check('关键词 unmute 后恢复欢迎', any(a['type'] == 'play_motion' and a['data'].get('motion') == 'wave' for a in actions[n:]))

broker.stop()
print(f"\n==== 控制层验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
