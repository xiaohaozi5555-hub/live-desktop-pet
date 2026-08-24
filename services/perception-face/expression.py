#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露脸表情归一化：DeepFace 情绪标签 → perception.face.expression（纯函数，可测）。"""

# DeepFace 情绪 → 契约 face.expression.label（契约枚举同名）
_MAP = {"angry": "angry", "disgust": "disgust", "fear": "fear",
        "happy": "happy", "sad": "sad", "surprise": "surprise", "neutral": "neutral"}
VALID = set(_MAP.values())


def to_expression(emotion, confidence=1.0, layout="portrait", ts=0):
    label = _MAP.get(str(emotion).lower(), "neutral")
    return {"channel": "perception", "type": "face.expression", "ts": ts,
            "source": "perception.face",
            "data": {"label": label, "confidence": float(confidence), "layout": layout}}
