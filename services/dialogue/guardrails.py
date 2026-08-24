#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合规护栏：LLM 现场生成的文本，说出口前必须过一道确定性检查（不能只靠 prompt 叮嘱模型自律）。
不过关就换安全兜底模板，不重试冒险。见 SPEC.md「合规护栏」节。

BANNED_WORDS 来源：`提案Demo自查清单.md`「话术/文案红线」一条——之前给人工审 demo 用，这里
是同一份清单的运行时用途。清单更新时这里要同步（verify_dialogue.py 有一致性检查会提醒）。"""
import os
import random
import re

# 护栏总开关。BANNED_WORDS 整张表来自抖音直播玩法的准入规则，是为"上架平台"服务的；
# 2026-07-26 项目已决定不走官方上架、改做主播自用程序（见 CHANGELOG「方向变更」），
# 这张表对自用场景就不再是硬约束了。但代码保留、默认仍开着——万一以后重新对外提交，
# 这些规则是查证过的资产，不该删掉重查。自用时在 .env 里设 PET_GUARDRAILS=off 关闭。
ENABLED = os.environ.get("PET_GUARDRAILS", "on").strip().lower() not in ("off", "0", "false", "no")

# 纯字母短词（T / CD / HP / AOE …）必须按独立词匹配，不能用子串：
# "T" 用子串会命中任何含拉丁字母 t 的文本（英文单词、颜文字 T_T、拼音），把正常回复大面积误伤。
# 中文词没有词边界概念，继续用子串匹配。
_PURE_ASCII = re.compile(r"^[A-Za-z]+$")

BANNED_WORDS = (
    "游戏", "玩家", "背包", "血条", "赛季", "BUFF", "DEBUFF", "人头", "QTE", "AOE",
    "平A", "KDA", "CD", "PVP", "PVE", "RPG", "MOBA", "AVG", "DPS", "T", "HP",
    "排位", "连杀", "吃鸡", "抽奖", "盲盒", "礼盒",
)

# 不过审时的安全兜底模板：本身不含任何禁用词，说什么都不会踩线。
SAFE_FALLBACKS = (
    "这个我先卖个萌，晚点再说~",
    "哎呀我一时词穷，你们继续继续~",
    "宝宝突然不知道说啥了，嘿嘿~",
)


def find_banned(text):
    """命中则返回那个禁用词，未命中返回 None。大小写不敏感（BUFF/buff 都拦）。
    纯字母词按独立词匹配，中文词按子串匹配（原因见文件顶部 _PURE_ASCII 处的注释）。"""
    up = (text or "").upper()
    for w in BANNED_WORDS:
        if _PURE_ASCII.match(w):
            # 下划线也算词内字符，否则颜文字 T_T 会被当成独立词 "T" 命中
            if re.search(rf"(?<![A-Za-z_]){re.escape(w.upper())}(?![A-Za-z_])", up):
                return w
        elif w.upper() in up:
            return w
    return None


def enforce(text):
    """生成完的文本过一遍禁用词检查再决定要不要念出来；不过关换安全兜底模板。
    护栏关闭时只兜空文本——空回复不管什么场景都不能直接念出来。"""
    if not ENABLED:
        return text or random.choice(SAFE_FALLBACKS)
    if text and not find_banned(text):
        return text
    return random.choice(SAFE_FALLBACKS)
