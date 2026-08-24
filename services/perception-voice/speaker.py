#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""声纹验证（只认主播）：resemblyzer 提取声纹嵌入，与注册的主播声纹比对。
⚠️ 需装 resemblyzer(会拉 torch) + 先用主播语音注册。对应"只识别我的声音"。"""
import os

VOICEPRINT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voiceprint.npy")  # gitignore
_ENC = None


def _encoder():
    global _ENC
    if _ENC is None:
        from resemblyzer import VoiceEncoder
        _ENC = VoiceEncoder()
    return _ENC


def enroll(wav_paths, out_path=VOICEPRINT):
    """用主播几段语音注册声纹（存平均嵌入）。需 resemblyzer。"""
    import numpy as np
    from resemblyzer import preprocess_wav
    embs = [_encoder().embed_utterance(preprocess_wav(p)) for p in wav_paths]
    emb = np.mean(embs, axis=0)
    np.save(out_path, emb)
    return out_path


def verify(audio, threshold=0.75, voiceprint=VOICEPRINT):
    """audio: 16k float32 → 是否主播本人。需 resemblyzer + 已注册声纹。"""
    import numpy as np
    if not os.path.exists(voiceprint):
        raise FileNotFoundError("未注册主播声纹，请先 enroll()")
    ref = np.load(voiceprint)
    emb = _encoder().embed_utterance(audio)
    sim = float(np.dot(ref, emb) / (np.linalg.norm(ref) * np.linalg.norm(emb)))
    return sim >= threshold, sim
