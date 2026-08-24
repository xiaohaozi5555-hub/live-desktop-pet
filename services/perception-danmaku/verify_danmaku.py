#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弹幕感知离线验证：官方事件/夹具 → 归一化 → 契约校验 → Brain 端到端。
运行: python verify_danmaku.py   退出码 0=全过。纯标准库、不接真弹幕。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "packages", "contract"))
sys.path.insert(0, os.path.join(REPO, "services", "brain"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import normalize as N          # noqa: E402
import validate as contract    # noqa: E402
import client as danmaku_client  # noqa: E402
from brain import Brain        # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# 1) 官方事件归一化
off = [N.normalize(e) for e in load(os.path.join(REPO, "fixtures", "danmaku", "sample-official.jsonl"))]
off = [m for m in off if m]
types = [m["type"] for m in off]
check("官方事件归一化数量=6", len(off) == 6, str(len(off)))
check("覆盖 进场/评论/点赞/礼物/关注", {"danmaku.enter", "danmaku.chat", "danmaku.like", "danmaku.gift", "danmaku.follow"} <= set(types), str(sorted(set(types))))
check("官方归一化结果全部通过契约校验", all(not contract.validate_message(m) for m in off))
gifts = {m["data"]["user"]: m["data"]["value_coins"] for m in off if m["type"] == "danmaku.gift"}
check("大礼物 value_coins=30000", gifts.get("神秘大哥") == 30000, str(gifts))
check("小礼物 value_coins=10", gifts.get("路过的骑士") == 10)

# 2) 夹具(kind)归一化仍可用（回归）
fix = [N.normalize(e) for e in load(os.path.join(REPO, "fixtures", "danmaku", "sample-session.jsonl"))]
fix = [m for m in fix if m]
check("kind 夹具归一化=13 且全部合法", len(fix) == 13 and all(not contract.validate_message(m) for m in fix), str(len(fix)))

# 3) 端到端：官方事件经 client → Brain → action
brain = Brain()
acts = []
danmaku_client.feed_fixture(os.path.join(REPO, "fixtures", "danmaku", "sample-official.jsonl"),
                            lambda m: acts.extend(brain.handle(m)))
motions = [a["data"].get("motion") for a in acts if a["type"] == "play_motion"]
check("端到端：官方大礼物 → thank_big", "thank_big" in motions, str(motions))
check("端到端：官方进场 → wave 欢迎", "wave" in motions)

# 4a) ⚠️ 真实数据里 Data 是 **JSON 字符串**，不是对象。
# 2026-07-30 真实开播踩过：908 条全抓到了，却因为归一化把 Data 当 dict 用而全线报
# `'str' object has no attribute 'get'`，桌宠一条都没收到。**离线夹具当时用的是对象，
# 所以离线全绿也测不出来**——这就是为什么这里必须单独用真实形状再测一遍。
_real_shape = {
    "Type": 1, "ProcessName": "直播伴侣",
    "Data": json.dumps({"Content": "主播这里好吓人", "RoomId": "7668361475987114758",
                        "User": {"Nickname": "夜行猫", "Id": 1, "PayLevel": 14,
                                 "FansClub": {"ClubName": "", "Level": 9}}}, ensure_ascii=False),
}
_m = N.normalize_grab(_real_shape)
check("Data 是 JSON 字符串时也能归一化（真实数据就是这个形状）",
      _m is not None and _m["type"] == "danmaku.chat" and _m["data"]["user"] == "夜行猫", str(_m))
check("字符串 Data 归一化后仍然合法", _m is not None and not contract.validate_message(_m))
check("Data 是坏字符串时安全返回 None，不炸整条链路",
      N.normalize_grab({"Type": 1, "Data": "{不是合法JSON"}) is None)

# 4) 直播伴侣抓取(grab)适配器：{Type,Data} 包 → 契约
raw_grab = load(os.path.join(REPO, "fixtures", "danmaku", "sample-grab.jsonl"))
grab = [m for m in (N.normalize(e) for e in raw_grab) if m]
gtypes = [m["type"] for m in grab]
check("grab 归一化结果全部通过契约校验", all(not contract.validate_message(m) for m in grab))
check("grab 覆盖 进场/弹幕/点赞/礼物/关注",
      {"danmaku.enter", "danmaku.chat", "danmaku.like", "danmaku.gift", "danmaku.follow"} <= set(gtypes),
      str(sorted(set(gtypes))))
check("grab 开播/下播 → command.stream_start/stream_end",
      [m["type"] for m in grab if m["channel"] == "command"] == ["stream_start", "stream_end"])
# 统计(6)/粉丝团(7)/分享(8) 目前无对应契约事件，必须被丢弃而不是误报
check("grab 丢弃 统计/粉丝团/分享 三类", len(grab) == len(raw_grab) - 3, f"{len(grab)}/{len(raw_grab)}")
ggifts = {m["data"]["user"]: (m["data"]["gift_name"], m["data"]["value_coins"]) for m in grab if m["type"] == "danmaku.gift"}
check("grab 大礼物 嘉年华 value_coins=30000", ggifts.get("神秘大哥") == ("嘉年华", 30000), str(ggifts))
check("grab 小礼物 小心心 value_coins=1", ggifts.get("路过的骑士") == ("小心心", 1))
check("grab 昵称正确解析（不是兜底的“观众”）", "观众" not in [m["data"].get("user") for m in grab if m["channel"] == "perception"])

# 5) 端到端：grab 包 → Brain → action
brain2 = Brain()
acts2 = []
for e in raw_grab:
    m = N.normalize(e)
    if m:
        acts2.extend(brain2.handle(m))
motions2 = [a["data"].get("motion") for a in acts2 if a["type"] == "play_motion"]
check("端到端：grab 大礼物 → thank_big", "thank_big" in motions2, str(motions2))
check("端到端：grab 进场 → wave 欢迎", "wave" in motions2)

# 6) 自动分级（依据抓包数据里的粉丝团等级）
from autotier import AutoTier  # noqa: E402  （默认关，但判定逻辑仍需回归）

sent = []
at = AutoTier(sent.append, member_level=1, star_level=15)


def pack(nick, level=None, following=None, typ=1):
    u = {"Nickname": nick}
    if level is not None:
        u["FansClubLevel"] = level
    if following is not None:
        u["IsFollowAnchor"] = following
    return {"Type": typ, "ProcessName": "直播伴侣", "Data": {"User": u}}


check("粉丝团 0 级 → normal，不发命令（默认值不占总线）", at.feed(pack("路人", 0)) is None)
check("只是关注了但没进粉丝团 → 仍是 normal", at.feed(pack("关注党", 0, True)) is None)
m = at.feed(pack("小会员", 3))
check("粉丝团 3 级 → member", m and m["data"]["tier"] == "member", str(m and m["data"]))
check("自动发的命令带 source=auto", m and m["data"]["source"] == "auto")
check("自动分级命令通过契约校验", not contract.validate_message(m))
s = at.feed(pack("铁粉大哥", 20))
check("粉丝团 20 级 → star_guardian", s and s["data"]["tier"] == "star_guardian")
check("同一人同一档位不重复发", at.feed(pack("小会员", 3)) is None and len(sent) == 2)
up = at.feed(pack("小会员", 18))
check("升档了会再发一次", up and up["data"]["tier"] == "star_guardian")
check("没带分级字段的包不猜", at.feed(pack("无信息")) is None)
check("开播事件会清空本场记录", at.feed({"Type": 101, "Data": {}}) is None and at._sent == {})
check("清空后同一人会重新判定一次", at.feed(pack("小会员", 3)) is not None)

# ---- 断流告警 + 自动恢复（2026-08-02 真开播断流 58 分钟后加）----
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("danmaku_run", os.path.join(HERE, "run.py"))
_dr = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_dr)

check("断流告警消息通过契约校验", not contract.validate_message(_dr._health(False, 300000, True)))
check("恢复消息通过契约校验", not contract.validate_message(_dr._health(True, 1000, True)))
for _stage in ('trying', 'ok', 'failed:端口没起来', 'gaveup'):
    check(f"恢复进度 {_stage!r} 的消息也通过契约校验",
          not contract.validate_message(_dr._health(False, 1, True, recovery=_stage)))
check("recovery 字段如实带出来",
      _dr._health(False, 1, True, recovery='trying')['data']['recovery'] == 'trying')
check("不传 recovery 时不塞这个字段（平时的告警不该看起来像在恢复）",
      'recovery' not in _dr._health(False, 1, True)['data'])

# 找不到抓包程序时要老实失败，不能抛异常把看护线程带走——它挂了就再也没人报断流了。
_saved = _dr.GRAB_EXE
try:
    _dr.GRAB_EXE = os.path.join(HERE, "这个文件不存在.exe")
    _ok, _why = _dr._restart_grabber("127.0.0.1", 65500)
    check("抓包程序不存在时老实返回失败，不抛异常", _ok is False and "找不到" in _why, str(_why))
finally:
    _dr.GRAB_EXE = _saved

# ⚠️ 安全红线，源码级钉死：**绝不能强杀抓包程序**。
# 它正常退出会把系统代理抹平（网络回到直连），强杀则跳过这一步，让系统代理仍指着一个
# 没人监听的端口 —— 那等于整机断网，现象跟宽带故障一模一样。以后谁figure图省事加个 /F，
# 这条会立刻红。
_src = open(os.path.join(HERE, "run.py"), encoding="utf-8").read()
_kill_call = _src[_src.index("def _restart_grabber"):_src.index("def run_live")]
check("恢复流程里没有对抓包程序的强杀（/F）——强杀会把系统代理留在死端口上＝整机断网",
      "'/F'" not in _kill_call and '"/F"' not in _kill_call and "/f'" not in _kill_call.lower())

print(f"\n==== 弹幕感知离线验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
