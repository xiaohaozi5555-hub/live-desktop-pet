#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证：抓包程序 → 弹幕感知服务 → 真实TCP总线 → brain → 桌宠动作，验证员在旁判卷。

回答两个问题：
  1) 假设弹幕礼物都能抓到，桌宠到底会不会动、动得对不对？
  2) 验证员能不能如实判出这件事（而且**不参与**数据链路）？

架构分工在这里被固化下来：数据由 `client.GrabClient`（run.py 的实时模式）送上总线，
验证员 `ReactionWatcher` 只订阅、不发布。曾经把两者揉在一起过——验证员顺手把数据也
转发了，那样它就没法独立判断链路是否断掉。本脚本用两条独立连接把这个分工钉死。

运行: python verify_grab_end2end.py   退出码 0=全过。纯标准库、不连外网、不需要抓包程序。
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in ("services/bus", "services/brain", "services/perception-danmaku"):
    sys.path.insert(0, os.path.join(REPO, *p.split("/")))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from broker import Broker                      # noqa: E402
from bus_client import BusClient               # noqa: E402
from brain import Brain                        # noqa: E402
from client import GrabClient                  # noqa: E402
from record_grab import ReactionWatcher        # noqa: E402
import wstestserver                            # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


PORT = 8793
broker = Broker(port=PORT).start()
time.sleep(0.3)

actions = []
BusClient(port=PORT, source="action-tap").subscribe(
    lambda m: actions.append(m) if m.get("channel") == "action" else None).connect()

commands = []
BusClient(port=PORT, source="cmd-tap").subscribe(
    lambda m: commands.append(m) if m.get("channel") == "command" else None).connect()

brain = Brain()
bc = BusClient(port=PORT, source="brain")
bc.subscribe(lambda m: [bc.publish(a) for a in brain.handle(m)]
             if m.get("channel") in ("perception", "command") else None).connect()

# 验证员：只订阅，不发布
watcher = ReactionWatcher()
BusClient(port=PORT, source="grab-verifier").subscribe(watcher.on_bus).connect()
time.sleep(0.4)

# 数据链路：假的抓包程序 → GrabClient → 总线
packs = [l.strip() for l in
         open(os.path.join(REPO, "fixtures", "danmaku", "sample-grab.jsonl"), encoding="utf-8") if l.strip()]
ws_port, _ = wstestserver.serve_once(packs, delay=0.08)
bus = BusClient(port=PORT, source="perception.danmaku").connect()
gc = GrabClient("127.0.0.1", ws_port, on_event=bus.publish, on_log=lambda *_: None)
th = threading.Thread(target=gc.run_forever, daemon=True)
th.start()
time.sleep(3.0)
gc.stop()

motions = [a["data"].get("motion") for a in actions if a["type"] == "play_motion"]
speaks = [a["data"].get("text", "") for a in actions if a["type"] == "speak"]

# ---- 数据链路 ----
check("感知服务把弹幕送上了总线，桌宠收到动作指令", len(actions) > 0, f"{len(actions)} 条 action")
check("进场 → 挥手欢迎", "wave" in motions, str(motions))
check("欢迎语带上观众昵称", any("夜行的猫" in s for s in speaks), str(speaks[:3]))
check("点赞 → praise 夸夸", "praise" in motions)
check("小礼物 → thank_small", "thank_small" in motions)
check("大礼物 → thank_big", "thank_big" in motions)
check("答谢语音带出礼物名", any("嘉年华" in s for s in speaks), str([s for s in speaks if "谢" in s][:3]))
check("关注 → 有答谢反应", "beg" in motions)
check("开播/下播作为 command 上总线", [c["type"] for c in commands] == ["stream_start", "stream_end"],
      str([c["type"] for c in commands]))

# ---- 验证员判卷 ----
summary = watcher.summary()
stats = summary["分类统计"]
check("验证员看到了全部五类弹幕事件",
      {"danmaku.enter", "danmaku.chat", "danmaku.like", "danmaku.gift", "danmaku.follow"} <= set(stats),
      str(sorted(stats)))
check("验证员判出进场有反应且动作符合预期",
      stats["danmaku.enter"]["有反应"] == 1 and stats["danmaku.enter"]["动作符合预期"] == 1,
      str(stats.get("danmaku.enter")))
check("验证员判出礼物两条都符合预期",
      stats["danmaku.gift"]["事件数"] == 2 and stats["danmaku.gift"]["动作符合预期"] == 2,
      str(stats.get("danmaku.gift")))
check("验证员核到了语音里的昵称",  # 08-01 夜里 brain.py 把进场欢迎从 speak 改成 show_bubble
      # 之后，enter 这类不再出声，改用同样"分层后依然出声"的 follow（关注答谢）来测这条断言
      stats["danmaku.follow"]["语音里带上昵称"] == 1, str(stats.get("danmaku.follow")))
check("验证员量到了反应延迟", isinstance(stats["danmaku.enter"]["平均延迟ms"], int),
      str(stats["danmaku.enter"]["平均延迟ms"]))

# ---- 分工钉死：验证员不能是数据源 ----
before = len(actions)
w2 = ReactionWatcher()
w2.on_bus({"channel": "perception", "type": "danmaku.enter", "data": {"user": "测试"}})
time.sleep(0.4)
check("验证员收消息不会引起任何总线发布（它只判卷不答题）", len(actions) == before,
      f"{before} → {len(actions)}")
check("桌宠没反应时验证员如实记为 0，而不是掩盖",
      w2.summary()["分类统计"]["danmaku.enter"]["有反应"] == 0)

print(f"\n==== grab 端到端验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
