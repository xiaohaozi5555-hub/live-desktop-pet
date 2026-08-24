#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨运行时验证：Python broker + Python brain/danmaku，Node 客户端(bus_probe.js)接收 action。
证明 Electron 角色(Node net)能收到 Python 端的 action.*。运行: python scripts/verify_xruntime.py"""
import json
import os
import subprocess
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

PORT = 8792
broker = Broker(port=PORT).start()
time.sleep(0.2)

# Node 探针子进程（监听 4.5s）
probe = subprocess.Popen(['node', os.path.join(REPO, 'scripts', 'bus_probe.js'), str(PORT), '4500'],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8')
time.sleep(0.6)   # 等 Node 连上

brain = Brain()
bc = BusClient(port=PORT, source='brain')
bc.subscribe(lambda m: [bc.publish(a) for a in brain.handle(m)] if m.get('channel') in ('perception', 'command') else None).connect()
time.sleep(0.2)

dan = BusClient(port=PORT, source='danmaku').connect()
for e in (json.loads(l) for l in open(os.path.join(REPO, 'fixtures', 'danmaku', 'sample-official.jsonl'), encoding='utf-8') if l.strip()):
    m = N.normalize(e)
    if m:
        dan.publish(m)
        time.sleep(0.05)

out, _ = probe.communicate(timeout=12)
broker.stop()
print("---- Node 探针输出 ----")
print(out.strip())
ok = ('action#' in out) and (probe.returncode == 0)
print(f"\n==== 跨运行时验证(Python broker → Node 客户端): {'PASS' if ok else 'FAIL'} ====")
sys.exit(0 if ok else 1)
