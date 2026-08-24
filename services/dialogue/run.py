#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue 运行器：连本地总线，收 perception/command → Dialogue.handle → 发 action/command。
跟 `services/brain/run.py` 同构、平级——两边独立连总线、独立订阅、独立发布，互不指挥。

LLM 生成在 Dialogue 内部的后台 worker 线程里跑（见 dialogue.py/SPEC.md"运行时不能卡住"），
本文件只管两头：把总线事件喂给 dlg.handle()（同步、即时）+ 把 dlg.outbox 里 worker 异步
生成好的 action 取出发布——两条路径互不阻塞对方。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)                                    # dialogue
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))  # bus_client
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _load_dotenv(repo):
    """极简 .env 读取（KEY=VAL），把 PET_CHAT_* 等注入环境。纯标准库，做法照抄
    perception-game/run.py。"""
    p = os.path.join(repo, '.env')
    if not os.path.exists(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(REPO)

from bus_client import BusClient   # noqa: E402
from dialogue import Dialogue      # noqa: E402


def main(host='127.0.0.1', port=8765):
    dlg = Dialogue()
    client = BusClient(host, port, source='dialogue')

    def on(msg):
        if msg.get('channel') in ('perception', 'command'):
            for a in dlg.handle(msg):
                client.publish(a)

    client.subscribe(on).connect()
    print(f"[dialogue] 已连总线 {host}:{port}，等待事件…"
          f"（跟 brain 平级订阅，独立发 action，互不指挥；LLM 回复在后台 worker 生成，不卡总线）")
    while True:                         # 阻塞取 worker 生成好的 action，取到就发布；空闲时不占 CPU
        client.publish(dlg.outbox.get())


if __name__ == '__main__':
    main()
