#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线全链路演示：弹幕夹具 -> 归一化(mock_bus) -> Brain 决策 -> action。
不开播、不装第三方依赖即可看到桌宠会怎么反应。运行: python scripts/run_offline.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)                                   # mock_bus
sys.path.insert(0, os.path.join(REPO, 'services', 'brain'))  # brain
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import mock_bus            # noqa: E402
from brain import Brain    # noqa: E402

brain = Brain()
bus = mock_bus.Bus()


def handler(msg):
    d = msg['data']
    who = d.get('user', '')
    what = d.get('text', '') or (f"{d.get('gift_name')}x{d.get('count')}" if msg['type'] == 'danmaku.gift' else '')
    print(f"[{msg['type']:<14}] {who} {what}".rstrip())
    for a in brain.handle(msg):
        ad = a['data']
        detail = ad.get('text') or ad.get('motion') or ad.get('expression') or ''
        print(f"      ↳ {a['type']}: {detail}")


bus.subscribe(handler)
fixture = os.path.join(REPO, 'fixtures', 'danmaku', 'sample-session.jsonl')
print("== 离线全链路：弹幕夹具 → 归一化 → Brain → action ==")
published, errors = mock_bus.replay(fixture, bus)
print(f"== 处理 {len(published)} 条弹幕，校验错误 {len(errors)} ==")
sys.exit(1 if errors else 0)
