#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键起本地系统：broker + brain + 弹幕回放（一个进程）。角色另开 `npm start`。
用法: python scripts/run_all.py
流程：本脚本起总线与决策 → 你另开终端 `cd apps/character && npm start`（角色自动连总线）
     → 回车开始回放弹幕夹具 → 桌宠随弹幕实时反应。"""
import os
import sys
import threading
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
import client as danmaku           # noqa: E402


def main():
    Broker().start()
    brain = Brain()
    bc = BusClient(source='brain')
    bc.subscribe(lambda m: [bc.publish(a) for a in brain.handle(m)]
                 if m.get('channel') in ('perception', 'command') else None).connect()
    print("[run_all] 总线 + 决策已启动 @127.0.0.1:8765")
    print("[run_all] 另开终端启动角色:  cd apps/character && npm start")
    try:
        input("[run_all] 角色连上后按回车，开始回放弹幕夹具…")
    except EOFError:
        pass
    dan = BusClient(source='danmaku').connect()
    fixture = os.path.join(REPO, 'fixtures', 'danmaku', 'sample-official.jsonl')
    danmaku.feed_fixture(fixture, lambda m: (dan.publish(m),
                                             print(f"  发弹幕: {m['type']} {m['data'].get('user', '')}"),
                                             time.sleep(1.2)))
    print("[run_all] 回放结束。Ctrl+C 退出。")
    threading.Event().wait()


if __name__ == '__main__':
    main()
