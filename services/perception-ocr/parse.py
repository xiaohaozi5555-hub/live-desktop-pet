#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件文字解析：把 OCR 出的文本解析为结构化 plugin.state（纯标准库，可离线测）。
与实际 OCR 引擎解耦——OCR 只负责出文本，解析规则在此，便于不看图即可验证/调。"""
import re


def _to_seconds(groups):
    parts = [int(x) for x in groups if x is not None]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def parse_plugin_text(texts):
    """texts: str / list[str] / dict。返回 {countdown_sec?, prank_count?}。"""
    if isinstance(texts, dict):
        texts = list(texts.values())
    if isinstance(texts, str):
        texts = [texts]
    joined = " ".join(str(t) for t in texts)
    out = {}
    m = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', joined)          # 00:30:00 或 30:00
    if m:
        sec = _to_seconds(m.groups())
        if sec is not None:
            out['countdown_sec'] = sec
    m2 = re.search(r'整蛊[^\d]*(\d+)', joined) or re.search(r'次数[:：]?\s*(\d+)', joined)
    if m2:
        out['prank_count'] = int(m2.group(1))
    return out


def make_state(parsed, ts=0):
    """把解析结果封成 perception.plugin.state 消息。"""
    return {"channel": "perception", "type": "plugin.state", "ts": ts,
            "source": "perception.ocr", "data": dict(parsed)}
