#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制指令构造器：产出 command.* 契约消息（纯函数，可测）。"""
import time


def _now():
    return int(time.time() * 1000)


def _cmd(type_, data=None, ts=None):
    return {"channel": "command", "type": type_, "ts": ts or _now(), "source": "control.panel", "data": data or {}}


def mute():
    return _cmd("mute")


def unmute():
    return _cmd("unmute")


def sleep():
    return _cmd("sleep")


def wake():
    return _cmd("wake")


def do(action):
    return _cmd("do", {"action": action})


def calibrate(region, x, y, w, h, layout="portrait"):
    """屏幕区域校准：region ∈ game/face/plugin；layout ∈ portrait/landscape。"""
    return _cmd("calibrate", {"region": region, "box": {"x": x, "y": y, "w": w, "h": h}, "layout": layout})


def set_viewer_tier(nickname, tier):
    """观众分级打标：tier ∈ normal/member/star_guardian（见 packages/contract/events.md）。"""
    return _cmd("set_viewer_tier", {"nickname": nickname, "tier": tier})


def stream_start():
    return _cmd("stream_start")


def stream_end():
    return _cmd("stream_end")
