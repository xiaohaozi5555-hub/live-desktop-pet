#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""露脸表情识别：DeepFace 分析露脸区域 → face.expression。
⚠️ 需装 deepface(会拉 TensorFlow) + 你的露脸截图调准确率。归一化逻辑在 expression.py（已自测）。

Windows 中文路径坑：本机项目路径含中文字符(D:\\调研claude与code\\...)。DeepFace 的
OpenCvClient.__get_opencv_path() 用 os.path.dirname(cv2.__file__) 现算 haarcascade 路径
（不读 cv2.data.haarcascades 属性，补丁那个没用），OpenCV 的 CascadeClassifier 用窄字符
API 打开中文路径下的文件会失败(empty()==True)，导致检测不到人脸、退化为对整张图(含背景)
分析、结果不稳定。修复：把 haarcascade *.xml 复制到纯 ASCII 路径(D:/cv2data/，ASCII 且在 D 盘
——既避开中文路径坑，又不占 C 盘)，运行时 monkeypatch DeepFace 那个私有方法
(名称改写为 _OpenCvClient__get_opencv_path)指过去。DeepFace 权重同样定向到 D 盘(DEEPFACE_HOME)。"""
import os
import shutil
import sys

# 模型/权重放 D 盘，别落 C 盘（C 盘空间紧张）：DeepFace 默认落 ~/.deepface(C)，改到 D。
os.environ.setdefault("DEEPFACE_HOME", "D:/datalab")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from expression import to_expression   # noqa: E402

_ASCII_CASCADE_DIR = "D:/cv2data"      # ASCII 路径且在 D 盘（避开中文路径 bug + 不占 C 盘）
_patched = False


def _ensure_ascii_cascade_dir():
    """把 haarcascade xml 复制到 ASCII 路径，并 monkeypatch DeepFace 的路径解析方法指过去。"""
    global _patched
    if _patched:
        return
    import cv2
    src = os.path.join(os.path.dirname(cv2.__file__), "data")
    if not src.isascii():
        os.makedirs(_ASCII_CASCADE_DIR, exist_ok=True)
        for name in os.listdir(src):
            if not name.endswith(".xml"):
                continue
            dst = os.path.join(_ASCII_CASCADE_DIR, name)
            if not os.path.exists(dst):
                shutil.copy(os.path.join(src, name), dst)
        from deepface.models.face_detection.OpenCv import OpenCvClient
        OpenCvClient._OpenCvClient__get_opencv_path = lambda self: _ASCII_CASCADE_DIR
    _patched = True


def analyze_face(image, layout="portrait", ts=0):
    """image: numpy 数组（露脸区域截图）→ face.expression。需 deepface。"""
    _ensure_ascii_cascade_dir()
    from deepface import DeepFace
    res = DeepFace.analyze(image, actions=["emotion"], enforce_detection=False, silent=True)
    r = res[0] if isinstance(res, list) else res
    emo = r.get("dominant_emotion", "neutral")
    conf = float(r.get("emotion", {}).get(emo, 0.0)) / 100.0
    return to_expression(emo, conf, layout, ts)
