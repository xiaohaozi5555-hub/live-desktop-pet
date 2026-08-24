#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 mock 总线：进程内 发布/订阅 + 弹幕夹具归一化回放。

作用：在**不开播、不装任何第三方依赖**的前提下，验证
    原始弹幕夹具 -> 归一化为 perception.danmaku.* -> 契约校验 -> 投递给订阅者
这条链路。真实运行时传输换成本地 TCP 总线（见 services/bus），归一化逻辑在 services/perception-danmaku。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "packages", "contract"))
import validate as contract  # noqa: E402
sys.path.insert(0, os.path.join(REPO, "services", "perception-danmaku"))
from normalize import normalize_fixture as normalize  # 归一化规范来源  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class Bus:
    """极简进程内总线：publish 广播给所有匹配的订阅者。"""

    def __init__(self):
        self._subs = []

    def subscribe(self, handler, predicate=None):
        self._subs.append((handler, predicate))

    def publish(self, msg):
        for handler, predicate in self._subs:
            if predicate is None or predicate(msg):
                handler(msg)


# normalize 已迁至 services/perception-danmaku/normalize.py（单一规范来源），此处复用其 normalize_fixture。


def load_fixture(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def replay(fixture_path, bus):
    """回放夹具：归一化 -> 校验 -> publish；返回 (已投递消息列表, 校验错误列表)。"""
    published, errors = [], []
    for raw in load_fixture(fixture_path):
        msg = normalize(raw)
        if msg is None:
            continue
        errs = contract.validate_message(msg)
        if errs:
            errors.append((msg, errs))
            continue
        bus.publish(msg)
        published.append(msg)
    return published, errors


if __name__ == "__main__":
    fixture = os.path.join(REPO, "fixtures", "danmaku", "sample-session.jsonl")
    bus = Bus()

    def printer(msg):
        d = msg["data"]
        who = d.get("user", "")
        detail = {
            "danmaku.enter": f"{who} 进入直播间",
            "danmaku.chat": f"{who}: {d.get('text','')}",
            "danmaku.gift": f"{who} 送出 {d.get('gift_name')} x{d.get('count')} ({d.get('value_coins')} 抖币)",
            "danmaku.like": f"{who} 点赞 x{d.get('count')}",
            "danmaku.follow": f"{who} 关注了主播",
        }.get(msg["type"], msg["type"])
        print(f"  [{msg['type']:<14}] {detail}")

    bus.subscribe(printer)
    print(f"== mock 总线回放: {fixture} ==")
    published, errors = replay(fixture, bus)
    print(f"== 投递 {len(published)} 条, 校验失败 {len(errors)} 条 ==")
    sys.exit(1 if errors else 0)
