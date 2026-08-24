#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brain 运行器：连本地总线，收 perception/command → Brain.handle → 发 action。"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)                                    # brain
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))  # bus_client
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    # line_buffering=True：管道下 Python 默认全缓冲，日志会卡住出不来（见 perception-danmaku/run.py）
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
except Exception:
    pass
from bus_client import BusClient   # noqa: E402
from brain import Brain            # noqa: E402


def main(host='127.0.0.1', port=8765):
    brain = Brain()
    client = BusClient(host, port, source='brain')

    def on(msg):
        if msg.get('channel') in ('perception', 'command'):
            for a in brain.handle(msg):
                client.publish(a)

    client.subscribe(on).connect()

    def ticker():                       # 周期心跳：驱动求礼物模式的周期话术
        while True:
            time.sleep(1)
            for a in brain.tick(int(time.time() * 1000)):
                client.publish(a)
    threading.Thread(target=ticker, daemon=True).start()

    print(f"[brain] 已连总线 {host}:{port}，等待事件…（求礼物模式会周期夸观众+求礼物）")
    threading.Event().wait()


if __name__ == '__main__':
    main()
