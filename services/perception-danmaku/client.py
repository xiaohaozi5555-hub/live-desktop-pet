#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""perception-danmaku 客户端：把弹幕来源接到事件总线（发布 perception.danmaku.*）。

两种模式：
  - 离线：feed_fixture(path, on_event)  读夹具(官方形态或 kind 形态)→归一化→回调。
  - 实时：GrabClient  连 DouyinBarrageGrab 的本地 WebSocket（**当前主用**）。
          原先这里是 OfficialClient（抖音开放平台长连接骨架），随 2026-07-26 放弃上架路线
          作废，已删除；理由与替代方案见 CHANGELOG「实时弹幕数据源」。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from normalize import normalize  # noqa: E402
from wsclient import WSClient, WSError  # noqa: E402


def feed_fixture(path, on_event):
    """离线：逐条读取夹具 → 归一化 → 回调 on_event(perception_msg)。返回已投递条数。"""
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            msg = normalize(json.loads(line))
            if msg is not None:
                on_event(msg)
                n += 1
    return n


class GrabClient:
    """实时来源：连 DouyinBarrageGrab 的 WebSocket，把每条包归一化后交给 on_event。

    这是**数据链路本身**——桌宠能不能对弹幕有反应全靠它，所以由 run.py 作为常驻服务拉起。
    「验证员」(record_grab.py) 是另一条独立的连接，只旁观、不往总线发；那台 WS 服务是广播
    式的，两个订阅者各拿一份副本互不影响。

    掉线自动重连、单条包解析失败不影响整体——直播中途断一下不能让桌宠从此哑掉。
    """

    def __init__(self, host="127.0.0.1", port=8888, on_event=None, on_log=print, on_raw=None):
        self.host, self.port = host, port
        self.on_event = on_event or (lambda m: None)
        # on_raw：拿**归一化之前**的原始包。自动分级要用 fansclub_level / is_follow_anchor
        # 这些契约里没有的字段，所以得在归一化丢掉它们之前看一眼。
        self.on_raw = on_raw
        self.on_log = on_log
        self._stop = False

    def stop(self):
        self._stop = True

    def run_forever(self, max_backoff=30.0):
        backoff = 1.0
        while not self._stop:
            ws = WSClient(self.host, self.port)
            try:
                ws.connect()
                self.on_log(f"[danmaku] 已连上抓包程序 ws://{self.host}:{self.port}")
                backoff = 1.0
                while not self._stop:
                    try:
                        text = ws.recv_text()
                    except OSError as e:
                        if isinstance(e, WSError):
                            raise
                        continue                     # 读超时：没弹幕而已，继续等
                    try:
                        pack = json.loads(text)
                    except Exception as e:
                        self.on_log(f"[danmaku] 单条解析失败已跳过: {type(e).__name__}: {e}")
                        continue
                    if self.on_raw is not None:
                        try:
                            self.on_raw(pack)
                        except Exception as e:      # 附加功能不能拖垮主链路
                            self.on_log(f"[danmaku] on_raw 出错已忽略: {type(e).__name__}: {e}")
                    try:
                        msg = normalize(pack)
                    except Exception as e:
                        self.on_log(f"[danmaku] 单条归一化失败已跳过: {type(e).__name__}: {e}")
                        continue
                    if msg is not None:
                        self.on_event(msg)
            except Exception as e:
                if self._stop:
                    break
                self.on_log(f"[danmaku] 连接断开（{type(e).__name__}: {e}），{backoff:.0f}s 后重连")
                time.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                ws.close()
