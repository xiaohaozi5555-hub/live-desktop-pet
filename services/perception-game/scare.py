#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""被吓启发式：从游戏画面亮度突变(+可选音量突变)检测"惊吓"，产出 game.scare 强度。
低延迟、不走 LLM——用于桌宠对恐怖画面即时做被吓反应。纯 numpy/标准库。"""


def frame_luminance(frame):
    """帧 -> 平均亮度[0,1]。frame 可为 numpy 数组(H,W,3/4) 或已是标量亮度。"""
    try:
        import numpy as np
        a = np.asarray(frame, dtype=np.float32)
        if a.ndim >= 3:
            a = a[..., :3].mean(axis=2)      # 丢 alpha，RGB 均值
        return float(a.mean()) / 255.0
    except Exception:
        return float(frame)                  # 已是标量


class ScareDetector:
    """连续帧亮度/音量的突变检测。push() 返回本帧惊吓强度[0,1]（0=无）。"""

    def __init__(self, lum_delta=0.18, audio_delta=0.25, cooldown_frames=8):
        self.lum_delta = lum_delta          # 亮度突变阈值
        self.audio_delta = audio_delta      # 音量突变阈值
        self.cooldown = cooldown_frames     # 触发后冷却帧数，避免连发
        self.prev_lum = None
        self.prev_audio = None
        self._cool = 0

    def push(self, luminance, audio_rms=None):
        intensity = 0.0
        if self.prev_lum is not None:
            d = abs(luminance - self.prev_lum)          # 变暗(跳吓前)或闪光(血腥/jumpscare)都算
            if d > self.lum_delta:
                intensity = max(intensity, min(1.0, d / (self.lum_delta * 2)))
        if audio_rms is not None and self.prev_audio is not None:
            da = audio_rms - self.prev_audio            # 只在突然变响时算(尖叫/音效)
            if da > self.audio_delta:
                intensity = max(intensity, min(1.0, da / (self.audio_delta * 2)))
        self.prev_lum = luminance
        self.prev_audio = audio_rms
        if self._cool > 0:
            self._cool -= 1
            return 0.0
        if intensity > 0:
            self._cool = self.cooldown
            return round(intensity, 2)
        return 0.0
