#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证员自检：起一个真的 WebSocket 服务端 → 喂夹具 → 断言录制与报告都对。

为什么要自己起服务端：wsclient.py 是手写的 RFC6455 实现，分片重组、ping 应答、
长度字段这几处最容易出错，而它要在直播时无人值守地跑——只测"能连上"没有意义，
必须把这些边角路径真的走一遍。

运行: python verify_grab_recorder.py   退出码 0=全过。纯标准库、不连外网。
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from record_grab import Recorder, ReactionWatcher   # noqa: E402
from wsclient import WSClient, GUID       # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


# ---- 最小 WebSocket 服务端（只为测试；服务端→客户端的帧不加掩码）----
def server_frame(opcode, payload, fin=True):
    n = len(payload)
    b0 = (0x80 if fin else 0x00) | opcode
    if n < 126:
        return struct.pack(">BB", b0, n) + payload
    if n < 65536:
        return struct.pack(">BBH", b0, 126, n) + payload
    return struct.pack(">BBQ", b0, 127, n) + payload


def serve_once(sock, messages, ready):
    conn, _ = sock.accept()
    req = b""
    while b"\r\n\r\n" not in req:
        req += conn.recv(4096)
    key = ""
    for line in req.decode("latin-1").split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    ready.set()
    # 先发一个 ping，客户端必须自动回 pong 且不把它当成数据
    conn.sendall(server_frame(0x9, b"hb"))
    for i, m in enumerate(messages):
        data = m.encode("utf-8")
        if i == 0 and len(data) > 10:
            # 第一条故意拆成两个分片，考验重组
            conn.sendall(server_frame(0x1, data[:10], fin=False))
            conn.sendall(server_frame(0x0, data[10:], fin=True))
        else:
            conn.sendall(server_frame(0x1, data))
    conn.sendall(server_frame(0x8, b""))          # close
    # 不能发完就 close()：客户端回的 pong 还在路上，Windows 对已关闭的套接字收到数据会回
    # RST，连带把客户端尚未读走的接收缓冲一起丢掉（表现为一条消息都收不到）。先半关写端、
    # 把对端剩下的字节读干净，再真正关闭。
    try:
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(2.0)
        while conn.recv(4096):
            pass
    except OSError:
        pass
    conn.close()


# ---- 用例 ----
fixture = os.path.join(REPO, "fixtures", "danmaku", "sample-grab.jsonl")
with open(fixture, encoding="utf-8") as f:
    lines = [l.strip() for l in f if l.strip()]

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
ready = threading.Event()
threading.Thread(target=serve_once, args=(srv, lines, ready), daemon=True).start()

out_dir = os.path.join(REPO, ".cache", "grab-selftest")
rec = Recorder(out_dir=out_dir).open()
ws = WSClient("127.0.0.1", port, timeout=5.0).connect()
ready.wait(3)
stop_reason = None
try:
    while True:
        rec.feed(json.loads(ws.recv_text()))
except Exception as e:                             # 服务端发完会发 close 帧，这是正常退出路径
    stop_reason = f"{type(e).__name__}: {e}"       # 但要记下来断言，别把真故障也吞掉
ws.close()
rep = rec.write_report()
rec.close()

check("经真实 WebSocket 收全了所有包（含分片重组、ping 未被误当数据）",
      rep["收到包数"] == len(lines), f"{rep['收到包数']}/{len(lines)}")
check("退出原因是服务端正常关闭，而不是别的异常",
      stop_reason is not None and "关闭" in stop_reason, str(stop_reason))
check("原始包逐条落盘", sum(1 for _ in open(rec.raw_path, encoding="utf-8")) == len(lines))
check("落盘的包带到达时间 _recv_ms",
      all("_recv_ms" in json.loads(l) for l in open(rec.raw_path, encoding="utf-8")))
check("类型分布正确（弹幕/点赞/进场/关注/礼物各1、礼物2条）",
      rep["各类型条数"].get("5(礼物)") == 2 and rep["各类型条数"].get("3(进直播间)") == 1,
      str(rep["各类型条数"]))
check("识别出只有一个直播间（串台检测的基准）", len(rep["出现过的直播间"]) == 1,
      str(rep["出现过的直播间"]))
check("来源进程只有 直播伴侣", list(rep["来源进程"]) == ["直播伴侣"], str(rep["来源进程"]))
check("礼物样本抓全（小心心/嘉年华）", len(rep["礼物样本"]) == 2, str(rep["礼物样本"]))
check("能判定 GiftCount 与 RepeatCount 是否相等", rep["GiftCount与RepeatCount是否始终相等"] is True)
check("粉丝团 Type/Level 分布已记录", rep["粉丝团Type与Level分布"] == [{"Data.Type": 1, "Data.Level": 3, "次数": 1}],
      str(rep["粉丝团Type与Level分布"]))
check("夹具里没有时间戳字段 → 报告为空", rep["疑似时间戳字段"] == {}, str(rep["疑似时间戳字段"]))
check("昵称兜底次数为 0（昵称字段对得上）", rep["昵称兜底成观众的次数"] == 0)
check("统计/粉丝团/分享 被丢弃", sum(rep["被丢弃的类型"].values()) == 3, str(rep["被丢弃的类型"]))
check("归一化产出含 5 类感知 + 2 条开播下播",
      len([k for k in rep["归一化产出"] if k.startswith("danmaku.")]) == 5
      and rep["归一化产出"].get("stream_start") == 1 and rep["归一化产出"].get("stream_end") == 1,
      str(rep["归一化产出"]))
check("录制期间零错误", rep["错误"] == [], str(rep["错误"][:3]))
check("报告两种格式都写出来了", os.path.exists(rec.txt_path) and os.path.exists(rec.json_path))

# 重放模式：拿刚录的原始文件再跑一遍，结果应当一致
with open(rec.raw_path, encoding="utf-8") as f:          # 先整份读进来再喂，
    recorded = [json.loads(l) for l in f if l.strip()]   # 避免边读边写同一个文件
rec2 = Recorder(out_dir=out_dir).open()
for pack in recorded:
    rec2.feed(pack)
rep2 = rec2.write_report()
rec2.close()
check("重放模式复现同样的统计", rep2["各类型条数"] == rep["各类型条数"] and rep2["收到包数"] == rep["收到包数"])

# 回归：真实数据里 Data 是 JSON 字符串，不是对象。08-01 真开播发现 _stat() 没跟着
# normalize_grab() 07-30 那次一起改，200 条统计从第一条起就全报
# `AttributeError: 'str' object has no attribute 'get'`，后面所有基于 Data 字段的统计
# （直播间/身份字段/礼物样本…）从此全是空的——上面这些用例的夹具里 Data 全是对象，
# 测不出这个坑，这里专门用字符串形态钉死。
rec3 = Recorder(out_dir=out_dir).open()
str_pack = {"Type": 5, "ProcessName": "直播伴侣",
            "Data": json.dumps({"MsgId": "s1", "User": {"Id": "1", "Nickname": "字符串礼物"},
                                "GiftName": "小心心", "GiftCount": 1, "RepeatCount": 1,
                                "DiamondCount": 1, "RoomId": 999}, ensure_ascii=False)}
rec3.feed(str_pack)
rep3 = rec3.write_report()
rec3.close()
check("Data 是字符串时 _stat 不报错（normalize_grab 那个坑的第二例）", rep3["错误"] == [], str(rep3["错误"]))
check("Data 是字符串时也能统计到礼物样本", len(rep3["礼物样本"]) == 1, str(rep3["礼物样本"]))
check("Data 是字符串时也能统计到直播间", len(rep3["出现过的直播间"]) == 1, str(rep3["出现过的直播间"]))

# 回归：08-01 真开播报告里 danmaku.gift 类"有反应"记成了 0/13，追查发现 ReactionWatcher.
# on_bus 只认 play_motion/speak 两种动作——07-30 分层后大量反应改走 show_bubble，
# 一直被当空气漏记，"完全没反应"是判卷漏看，不是桌宠真没反应。
w = ReactionWatcher()
w.on_bus({"channel": "perception", "type": "danmaku.gift", "ts": 1000, "data": {"user": "小明"}})
w.on_bus({"channel": "action", "type": "play_motion", "ts": 1000, "data": {"motion": "thank_small"}})
w.on_bus({"channel": "action", "type": "show_bubble", "ts": 1000, "data": {"text": "谢谢 小明 送的礼物~"}})
gift_row = w.summary()["分类统计"]["danmaku.gift"]
check("show_bubble 反应现在会被正确记为'有反应'", gift_row["有反应"] == 1, str(gift_row))
check("show_bubble 带昵称会被记入'文字里带上昵称'", gift_row["文字里带上昵称"] == 1, str(gift_row))

# 回归：08-01 真开播还发现旧版"猜离得最近的弹幕"在 dialogue 的回复上系统性猜错——弹幕
# 回复要经后台 worker 排队 + 等几秒 LLM 生成才真正发布 action，这几秒里随时会插进新的
# 弹幕/进场/点赞，旧版会把动作错发给那条新插入的事件。改成按 ts 精确匹配后，这里直接
# 模拟"动作很晚才到、中间插了两条别的事件"，验证归因依然精确对回最初那条弹幕。
w2 = ReactionWatcher()
w2.on_bus({"channel": "perception", "type": "danmaku.chat", "ts": 2000, "data": {"user": "老王"}})
w2.on_bus({"channel": "perception", "type": "danmaku.enter", "ts": 2001, "data": {"user": "路人甲"}})
w2.on_bus({"channel": "perception", "type": "danmaku.like", "ts": 2002, "data": {"user": "路人乙"}})
w2.on_bus({"channel": "action", "type": "speak", "ts": 2000, "data": {"text": "老王你好呀"}})   # 用最初的 ts，不是"现在"
s2 = w2.summary()["分类统计"]
check("ts 精确匹配：动作晚到+中间插了别的事件，依然精确对回触发它的那条弹幕（旧版会错发给路人乙）",
      s2["danmaku.chat"]["有反应"] == 1
      and s2.get("danmaku.enter", {}).get("有反应", 0) == 0
      and s2.get("danmaku.like", {}).get("有反应", 0) == 0,
      str(s2))

# 没有对应感知事件的动作（比如 brain 合批欢迎/定时互动，按 tick 时间发不是由单条弹幕触发）
# 应该老实记成"无法归因"，不该瞎认领给某条不相干的弹幕。
w3 = ReactionWatcher()
w3.on_bus({"channel": "action", "type": "speak", "ts": 9999, "data": {"text": "没头没脑的一句"}})
check("匹配不上任何感知事件的动作被诚实记成'无法归因'", w3.stray_actions == 1)

# 2026-08-02 新增的 danmaku.health 是链路健康信号，不是观众行为——它长得像 danmaku.*，
# 但绝不能被算进"桌宠该不该反应"的统计里，否则报告里会凭空多出一行永远 0% 的假数据。
w4 = ReactionWatcher()
w4.on_bus({"channel": "perception", "type": "danmaku.health", "ts": 5000,
           "data": {"ok": False, "silent_ms": 300000, "connected": True}})
w4.on_bus({"channel": "perception", "type": "danmaku.chat", "ts": 5001, "data": {"user": "小明"}})
w4.on_bus({"channel": "action", "type": "show_bubble", "ts": 5001, "data": {"text": "回小明"}})
s4 = w4.summary()["分类统计"]
check("danmaku.health 不会被当成观众事件混进判卷统计",
      "danmaku.health" not in s4 and s4["danmaku.chat"]["有反应"] == 1, str(sorted(s4)))

print(f"\n==== 验证员自检: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
