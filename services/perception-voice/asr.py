#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文 ASR：faster-whisper（或 FunASR）。⚠️ 需装 faster-whisper（首次拉模型）。
识别文本交给 intents.to_audio_command 转意图。"""
_MODEL = None


def _model(size="small"):
    global _MODEL
    if _MODEL is None:
        from faster_whisper import WhisperModel
        _MODEL = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL


# 命令关键词提示词：偏向 faster-whisper 正确识别指令词（实测能纠正同音字误听，如"吹一下"->"催一下"）。
#
# ⚠️ 唤醒词必须在这里面。实测（2026-07-27，用户真实录音）：表里缺"魔丸"时，"魔丸你好"被听成
# "摸完你好"，唤醒词匹配直接落空，现象就是"喊她完全没反应"；把"魔丸"加进来后同一段音频识别成
# "魔丸你好 魔丸你好 魔丸"，一次就对。改唤醒词(PET_WAKE_WORD)时记得同步改这里。
# 原表里的"小幽"是早期名字，已随之移除——它还会把近似音引导成"小幽默"（实测出现过这种幻觉）；
# "求礼物/要礼物"对应的能力已在 2026-07-14 移除，一并删掉。
COMMAND_PROMPT = ("魔丸 魔丸你好 闭嘴 安静 可以说话 恢复 休息 睡觉 醒醒 "
                  "卡关 攻略 怎么过 看一下弹幕 催一下")


def transcribe(audio, sample_rate=16000, size="small", initial_prompt=COMMAND_PROMPT):
    """audio: 16k 单声道 float32 numpy 或 wav 路径 → 中文文本。需 faster-whisper。
    initial_prompt 偏向指令词表识别；传 None/"" 可关闭。"""
    segments, _ = _model(size).transcribe(audio, language="zh", initial_prompt=initial_prompt or None)
    return "".join(s.text for s in segments).strip()
