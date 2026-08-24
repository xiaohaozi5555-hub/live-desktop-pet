#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音识别文本 → 意图 → perception.audio.command（纯函数，可测）。
决策层只采信 speaker_verified=true 的指令（"只识别我的声音"）。

2026-07：扩展支持 dialogue 服务的语音聊天（见 services/dialogue/SPEC.md）——命中不了
固定 intent 表的，转发 raw_text 给 dialogue 当自由聊天文本处理（intent='chat'）。固定
intent 表依然优先匹配（比如"闭嘴"还是走 mute，不会被当成聊天内容）。

2026-08-01：**去掉了"免重复唤醒词"的对话窗口**（曾经叫 ConversationWindow，喊一次唤醒词
开 18 秒窗口、窗口内任何有效发言都会把窗口续满 18 秒）。主播真开播实测发现：自己跟观众
讲话的语速经常快过 18 秒一次，窗口会被跟桌宠毫不相干的话不断续上，那些话被当成"跟桌宠
聊天"转发给 DeepSeek，白白耗 token。改成每句都要喊一次唤醒词——没有隐藏的倒计时要主播
去猜/去控制，这也是主播要求的"停顿就该停，不用我数秒数"：一句话说没说完，本来就是靠
VAD 的静音判定（run.py 的 SILENCE_TAIL_MS=700ms）来判断，跟这里的唤醒词逻辑是两回事，
不受这次改动影响——单句"说完了没"一直都是自然停顿触发，从没有过固定秒数。"""
import os
import re

# 唤醒词：设环境变量 PET_WAKE_WORD（如"魔丸"）后，只处理含唤醒词的语音——
# 配合声纹验证进一步防恐怖游戏音效/尖叫误触。留空=不要求唤醒词（向后兼容）。
WAKE_WORD = os.environ.get("PET_WAKE_WORD", "")

# 模糊包含匹配：允许关键词相邻字符间插入至多1个无关字（容忍 ASR 同音字/插字误听，
# 实测"要个礼物"仍需命中"要礼物"）。缓存编译结果避免重复编译正则。
_FUZZY_CACHE = {}


def _fuzzy_contains(keyword, text):
    pat = _FUZZY_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(".{0,1}".join(re.escape(c) for c in keyword))
        _FUZZY_CACHE[keyword] = pat
    return pat.search(text) is not None

# 明确"就是在叫我去找攻略"的说法。命中这些**直接触发，不再反问**。
#
# 为什么要分两级（2026-07-30 按用户描述的真实场景重做）：主播的求助原话通常是长句——
# 「我卡关了，帮我找找攻略，游戏名是XXX，我现在在这个房子里有个东西找不到，上一个任务是XXX，
# 后续没提示了」。这一整段**正是搜索质量的来源**。原先所有"卡关"类说法都先反问一句
# "需要我帮忙吗"、等主播答"是"再触发，那句"是"就成了唯一被传下去的内容，前面那段全丢了；
# 而且直播中平白多花十几秒。所以：话里已经明说要攻略的，直接办；只是随口提了句卡关的，才反问。
WALKTHROUGH_DIRECT = ("帮我找", "找找攻略", "找攻略", "搜攻略", "查攻略", "帮我看看怎么过",
                      "帮我搜", "给我攻略", "帮忙找")

# (关键词, 意图)。意图与 brain._command / 控制面板 / dialogue 保持一致。
# "卡关"类默认产出 walkthrough_ask（要二次确认），命中 WALKTHROUGH_DIRECT 时才升级成
# walkthrough 直接触发——见 to_intent()。
TABLE = [
    (("闭嘴", "安静", "别说话", "别吵"), "mute"),
    (("可以说话", "恢复", "继续说", "解禁"), "unmute"),
    (("休息", "睡觉", "下播"), "sleep"),
    (("醒醒", "起来", "开始工作"), "wake"),
    (("卡关", "攻略", "怎么过", "过不去", "不知道该干嘛", "没有提示"), "walkthrough_ask"),
    (("看一下弹幕", "看看弹幕", "回一下弹幕", "回复弹幕", "帮我回一下"), "review_chat"),
]


def _match_table(text):
    """只查固定 intent 表，不做唤醒词判断（供 to_intent / to_audio_command 共用）。"""
    for kws, intent in TABLE:
        if any(_fuzzy_contains(k, text) for k in kws):
            # 话里已经明说"帮我找攻略"了，就别再反问一句"需要我帮忙吗"——
            # 那既浪费直播时间，又会让主播那段带着关卡信息的原话在确认环节丢掉。
            if intent == 'walkthrough_ask' and any(_fuzzy_contains(k, text) for k in WALKTHROUGH_DIRECT):
                return 'walkthrough'
            return intent
    return None


def to_intent(text):
    """文本 -> 固定意图（单句判断）。启用唤醒词时，只有文本本身包含唤醒词才继续判断。"""
    t = (text or "").strip()
    if WAKE_WORD and not _fuzzy_contains(WAKE_WORD, t):
        return None
    return _match_table(t)


def to_audio_command(text, speaker_verified, ts=0):
    """文本 + 声纹结果 → audio.command 消息；没喊唤醒词返回 None。

    每一句都要求唤醒词（见文件头 2026-08-01 那段说明，这里不重复）。命中固定 intent 表的
    按表走，命中不了的转发 raw_text 给 dialogue 当自由聊天文本（intent='chat'）。"""
    t = (text or "").strip()
    if WAKE_WORD and not _fuzzy_contains(WAKE_WORD, t):
        return None
    intent = _match_table(t) or "chat"
    return {"channel": "perception", "type": "audio.command", "ts": ts, "source": "perception.voice",
            "data": {"intent": intent, "raw_text": text, "speaker_verified": bool(speaker_verified)}}
