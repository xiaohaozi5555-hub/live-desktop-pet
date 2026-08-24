#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依据抓包数据自动判定观众分级，发 command.set_viewer_tier（source=auto）。

⚠️ **默认关闭，因为映射关系还没搞清楚（2026-07-29 用户指出）。**

最初的实现拿「粉丝团等级」去近似会员/星守护，**这是错的**：抖音的「会员」和「星守护」
是各自独立的付费身份，跟粉丝团几级没有关系。一个 20 级粉丝团的铁粉可能既不是会员也不是
守护，反过来也成立。按错的映射自动打标，会把错误档位写进 dialogue 的观众画像库，进而影响
真实的回复策略——比"没有自动分级"更糟。

所以现在：**不猜**。等真实开播，由「验证员」(record_grab.py) 把每位观众身上出现过的所有
身份字段原样记下来，看清楚会员/守护到底体现在哪个字段上，再回来把 `tier_of()` 改对。
在那之前分级仍然只走主播手动打标（面板照旧可用）。

阈值环境变量（仅在显式打开自动分级时才生效）：
    PET_AUTO_TIER=1        打开自动分级（默认关）
    PET_TIER_MEMBER_LEVEL  默认 1
    PET_TIER_STAR_LEVEL    默认 15

**手动始终优先**（这条规则本身是对的，保留）：发出去的命令带 `source: "auto"`，
dialogue 的 `memory.set_tier()` 看到 auto 且该观众已被主播手动打过标时跳过不覆盖。
"""
import os
import time


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class AutoTier:
    """吃原始包 → 必要时发 command.set_viewer_tier。同一观众同一档位每场只发一次。"""

    def __init__(self, publish, member_level=None, star_level=None, on_log=None):
        self.publish = publish
        self.member_level = member_level if member_level is not None else _int_env("PET_TIER_MEMBER_LEVEL", 1)
        self.star_level = star_level if star_level is not None else _int_env("PET_TIER_STAR_LEVEL", 15)
        self.on_log = on_log or (lambda *_: None)
        self._sent = {}                 # 昵称 -> 已发过的档位，避免同一个人刷屏时反复发命令
        self.sent_count = 0

    def tier_of(self, level, following):
        """等级/关注 → 档位。

        ⚠️ **这个映射目前是错的，等真实数据再改**：会员/星守护是独立的付费身份，不由粉丝团
        等级决定。保留这段代码只是为了在弄清正确字段后能就地替换，默认不会被调用（自动分级
        默认关）。
        """
        level = level or 0
        if level >= self.star_level:
            return "star_guardian"
        if level >= self.member_level:
            return "member"
        return "normal"

    def reset(self):
        """开播时清空。跨场不保留，避免上一场的判断压住这一场的新情况。"""
        self._sent.clear()

    def feed(self, pack):
        """喂一条抓包原始包。返回发出去的命令（没发就返回 None），方便测试断言。"""
        if not isinstance(pack, dict):
            return None
        if pack.get("Type") == 101:                    # 直播伴侣开播
            self.reset()
            return None
        data = pack.get("Data") or {}
        user = (data.get("User") or {})
        nickname = user.get("Nickname")
        if not nickname:
            return None
        # 字段可能挂在 User 上，也可能平铺在 Data 上，两处都认
        level = user.get("FansClubLevel", data.get("FansClubLevel", data.get("fansclub_level")))
        following = user.get("IsFollowAnchor", data.get("IsFollowAnchor", data.get("is_follow_anchor")))
        if level is None and following is None:
            return None                                # 这条包没带分级信息，不猜
        tier = self.tier_of(level, following)
        if tier == "normal":
            return None                                # normal 是默认值，不必发命令占总线
        if self._sent.get(nickname) == tier:
            return None
        msg = {"channel": "command", "type": "set_viewer_tier",
               "ts": int(time.time() * 1000), "source": "perception.danmaku",
               "data": {"nickname": nickname, "tier": tier, "source": "auto"}}
        try:
            self.publish(msg)
        except Exception as e:
            self.on_log(f"[autotier] 发送失败: {type(e).__name__}: {e}")
            return None
        self._sent[nickname] = tier
        self.sent_count += 1
        self.on_log(f"[autotier] {nickname} 粉丝团{level}级 → {tier}")
        return msg
