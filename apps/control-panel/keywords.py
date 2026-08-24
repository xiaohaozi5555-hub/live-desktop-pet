#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""弹幕关键词 → 控制指令映射（纯函数，可测）。

只有主播/房管白名单（STREAMER_NAMES / MODERATOR_NAMES）内的弹幕昵称才能触发控制指令，
避免任意观众打出关键词就能让桌宠静音/睡眠。白名单通过环境变量配置（逗号分隔多个昵称）：
    PET_STREAMER_NAMES="你的抖音昵称"
    PET_MODERATOR_NAMES="房管A,房管B"
未配置时默认只放行项目所有者本人昵称（见 STREAMER_NAMES 默认值）。
PREFIX 是额外的一层混淆（暗号前缀），跟白名单校验叠加使用，不是身份校验的替代品。

注意：`match()` 不做身份校验，只给「控制面板 CLI/GUI」这种本机可信输入使用；
弹幕这种不可信输入一律走 `match_danmaku()`。
"""
import os

import commands as C


def _names(env_key, default=()):
    raw = os.environ.get(env_key)
    if raw is None:
        return list(default)
    return [n.strip() for n in raw.split(",") if n.strip()]


# 默认只认项目所有者本人；部署时按需用环境变量覆盖成你实际的抖音昵称。
STREAMER_NAMES = _names("PET_STREAMER_NAMES", ("晨昊",))
MODERATOR_NAMES = _names("PET_MODERATOR_NAMES", ())

# (关键词元组, 生成 command 的函数)
TABLE = [
    (("闭嘴", "安静", "别说话"), C.mute),
    (("可以说话", "恢复", "继续说"), C.unmute),
    (("休息", "睡觉", "下播"), C.sleep),
    (("醒醒", "起来", "开始"), C.wake),
    (("挥手", "打招呼"), lambda: C.do("wave")),
    (("被吓", "吓一跳"), lambda: C.do("scared")),
    (("谢谢", "答谢"), lambda: C.do("thank_big")),
]

PREFIX = ""   # 设为非空(如 "//") 则额外要求以该前缀开头，双重防误触


def is_authorized(user):
    """弹幕昵称是否在主播/房管白名单内。"""
    u = (user or "").strip()
    return bool(u) and (u in STREAMER_NAMES or u in MODERATOR_NAMES)


def _lookup(t):
    for kws, fn in TABLE:
        if any(k in t for k in kws):
            return fn
    return None


def match(text):
    """本机可信输入（控制面板 CLI/GUI）用：不做身份校验。"""
    t = (text or "").strip()
    if PREFIX:
        if not t.startswith(PREFIX):
            return None
        t = t[len(PREFIX):]
    fn = _lookup(t)
    return fn() if fn else None


def match_danmaku(text, user):
    """弹幕（不可信输入）专用：昵称不在白名单一律不生效。"""
    if not is_authorized(user):
        return None
    return match(text)
