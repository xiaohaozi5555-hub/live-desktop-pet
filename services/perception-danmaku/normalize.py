#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perception-danmaku 归一化：把不同来源的弹幕原始事件统一成 perception.danmaku.*。

来源适配器：
  - grab    ：DouyinBarrageGrab 抓取直播伴侣自身流量后推出的 {Type,ProcessName,Data} 包（**主用**）。
  - official：抖音开放平台「直播玩法 / 互动数据」推送（上架路线已放弃，保留供回归与将来切换）。
  - fixture ：离线夹具的 kind-based 记录（开发/回归测试用；mock_bus 复用本函数）。

grab 的字段名取自其源码 Modles/JsonEntity/BarrageMessages.cs；official 的字段名取自
直播伴侣插件通道实测样本。本模块纯标准库，可离线单测。
"""
import json
import time


def _now_ms():
    return int(time.time() * 1000)


def _perc(type_, data, ts=None):
    return {"channel": "perception", "type": type_, "ts": ts or _now_ms(),
            "source": "perception.danmaku", "data": data}


def _cmd(type_, data=None, ts=None):
    return {"channel": "command", "type": type_, "ts": ts or _now_ms(),
            "source": "perception.danmaku", "data": data or {}}


# ---- 离线夹具适配器（kind-based）----
def normalize_fixture(raw):
    kind, ts = raw.get("kind"), raw.get("ts")
    if kind == "member":
        return _perc("danmaku.enter", {"user": raw["user"]}, ts)
    if kind == "chat":
        return _perc("danmaku.chat", {"user": raw["user"], "text": raw["content"]}, ts)
    if kind == "gift":
        return _perc("danmaku.gift", {"user": raw["user"], "gift_name": raw["gift"],
                                      "count": int(raw.get("count", 1)), "value_coins": int(raw.get("coins", 0))}, ts)
    if kind == "like":
        return _perc("danmaku.like", {"user": raw["user"], "count": int(raw.get("count", 1))}, ts)
    if kind == "social":
        return _perc("danmaku.follow", {"user": raw["user"]}, ts)
    return None


# ---- 官方玩法适配器 ----
# 官方推送事件通用形态（字段名 TODO: 以拿到的 SDK/互动数据文档为准校对）：
#   {"msg_type":"comment|gift|like|member|social", "user":{"nick_name":..,"open_id":..}, ...}
def _user(evt):
    u = evt.get("user") or {}
    return (u.get("nick_name") or u.get("nickname") or evt.get("nickname")
            or evt.get("nick_name") or evt.get("user_name") or "观众")


def normalize_official(evt):
    mt = evt.get("msg_type") or evt.get("type") or evt.get("method")
    ts = evt.get("timestamp") or evt.get("ts")
    user = _user(evt)
    if mt in ("comment", "live_comment", "chat"):
        return _perc("danmaku.chat", {"user": user, "text": evt.get("content") or evt.get("comment") or ""}, ts)
    if mt in ("gift", "live_gift"):
        count = int(evt.get("gift_count") or evt.get("repeat_count") or evt.get("count") or 1)
        if evt.get("gift_value") is not None:
            value = int(evt["gift_value"])                 # 已是总价值
        else:
            unit = int(evt.get("diamond_count") or evt.get("coins") or 0)  # 单个礼物价值(钻/抖币)
            value = unit * count
        return _perc("danmaku.gift", {"user": user, "gift_name": evt.get("gift_name") or str(evt.get("gift_id") or "礼物"),
                                      "count": count, "value_coins": value}, ts)
    if mt in ("like", "live_like"):
        return _perc("danmaku.like", {"user": user, "count": int(evt.get("like_count") or evt.get("count") or 1)}, ts)
    if mt in ("member", "live_user_enter", "enter"):
        return _perc("danmaku.enter", {"user": user}, ts)
    if mt in ("social", "follow"):
        return _perc("danmaku.follow", {"user": user}, ts)
    return None


# ---- 直播伴侣抓取适配器（DouyinBarrageGrab）----
# 包形态：{"Type": <PackMsgType>, "ProcessName": "直播伴侣", "Data": {消息本体}}
# PackMsgType：1弹幕 2点赞 3进直播间 4关注 5礼物 6统计 7粉丝团 8分享 9下播
#              101直播伴侣开播 102直播伴侣下播
# 消息本体无时间戳字段，故一律用到达时间——这反而避开了夹具重放时 ts 写死导致 brain 冷却
# 误吞事件的老问题（见 CHANGELOG「夹具重放的冷却陷阱」）。
def _grab_user(d):
    return (d.get("User") or {}).get("Nickname") or "观众"


def _grab_fansclub(d):
    """粉丝团（灯牌）等级。真实数据结构是 User.FansClub = {"ClubName": "", "Level": 9}。

    为什么要带出来：主播定的回复分层里，**灯牌 8 级以上属于"必回且要出声"**，而这个等级只在
    原始包里有，不带过来下游就无从判断。0 或缺失＝没灯牌。
    ⚠️ 它**不能**用来判断会员/星守护——2026-07-30 真实开播实测，整个 User 里没有任何字段能
    标出这两个身份（PayLevel 是 0~40 的财富等级，不是会员标志），只能靠主播在控制台手动勾。
    """
    fc = (d.get("User") or {}).get("FansClub")
    if isinstance(fc, dict):
        try:
            return int(fc.get("Level") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def normalize_grab(pack):
    """DouyinBarrageGrab 包 → perception.danmaku.* / command.stream_*。无对应契约事件的返回 None。"""
    t, d = pack.get("Type"), pack.get("Data") or {}
    # ⚠️ **真实数据里 Data 是一段 JSON 字符串，不是对象**——要再解一次。
    # 2026-07-30 真实开播实测：908 条全部抓到了，却因为这里当 dict 用而 200 条全报
    # `AttributeError: 'str' object has no attribute 'get'`，整条链路一条都没通到桌宠，
    # 现象是"黑终端里看得见弹幕，桌宠毫无反应"。离线夹具里 Data 是对象，所以测不出来。
    if isinstance(d, str):
        try:
            d = json.loads(d) or {}
        except (ValueError, TypeError):
            return None
    if not isinstance(d, dict):
        return None
    user = _grab_user(d)
    fc = _grab_fansclub(d)
    if t == 1:
        return _perc("danmaku.chat", {"user": user, "text": d.get("Content") or "",
                                      "fansclub_level": fc})
    if t == 2:
        return _perc("danmaku.like", {"user": user, "count": int(d.get("Count") or 1)})
    if t == 3:
        return _perc("danmaku.enter", {"user": user, "fansclub_level": fc})
    if t == 4:
        return _perc("danmaku.follow", {"user": user})
    if t == 5:
        # DiamondCount 是单个礼物的抖币价值，乘以数量得总价值（契约 value_coins）。
        # GiftCount / RepeatCount 谁是总数需用真实数据核对，先取较大者兜底。
        # 2026-07-30 真实开播核实：`DiamondCount` **单位就是抖币，1:1**（名刀司命=99、
        # 为你闪耀=9、小心心=1、粉丝团灯牌=1，跟抖音里的实际价格对得上）。
        # ⚠️ 这跟插件通道当年实测的"单位是分、差 10 倍"不同，以真实抓包数据为准。
        # `GiftCount` 与 `RepeatCount` 那 17 次送礼里**始终都是 1**，没出现连击，谁是总数
        # 仍未确认，继续取较大者兜底。
        count = max(int(d.get("GiftCount") or 1), int(d.get("RepeatCount") or 1))
        return _perc("danmaku.gift", {"user": user, "gift_name": d.get("GiftName") or "礼物",
                                      "count": count, "value_coins": int(d.get("DiamondCount") or 0) * count,
                                      "fansclub_level": fc})
    if t == 101:
        return _cmd("stream_start")
    if t == 102:
        return _cmd("stream_end")
    # 6统计/8分享：契约里没有对应事件。7粉丝团：FansclubMsg.Type 的取值（加入/升级）
    # 未经真实数据确认，宁可不发也不误报成 danmaku.follow，待接真实弹幕后补。
    # 9下播是直播间侧的下播事件，与 102 语义重叠，统一只认 102，避免重复收尾。
    return None


def normalize(evt):
    """自动分派：kind→夹具，Type/Data→grab，其余按官方事件解析。返回契约消息或 None。"""
    if "kind" in evt:
        return normalize_fixture(evt)
    if "Type" in evt and "Data" in evt:
        return normalize_grab(evt)
    return normalize_official(evt)
