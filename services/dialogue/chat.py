#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek 对话生成：OpenAI 兼容协议，复用 `perception-game/vision.py` 里已验证过的调用模式
（同一套代码结构，换 base_url/key/model，去掉图片输入）。Key 见 .env 的 PET_CHAT_*。

模型用 deepseek-v4-flash，显式关闭 thinking（DeepSeek 官方文档：thinking 开关默认 enabled，
不关的话会真实消耗 reasoning token、拖慢并可能吃光 max_tokens 预算），$0.14/1M 输入 token，
够用够便宜；不用 v4-pro，那个贵约 3 倍，给复杂推理场景用，我们纯中文口语短回复用不上。

未配置 key / 调用超时或出错：不抛异常打断调用方——参照 vision.py 的"未配置视觉后端"做法，
返回一句角色口吻提示，让桌宠直接说出来，而不是只在终端打印一行错误、桌宠沉默尴尬。"""
import os
import random

FALLBACK_LINES = (
    "主人还没给我配对话的钥匙呢，去 .env 配一下我才能好好聊天哦～",
)

TIMEOUT_FALLBACK_LINES = (
    "呃，宝宝脑子突然卡了一下，晚点再聊好不好~",
)


def _configured():
    return bool(os.environ.get("PET_CHAT_API_KEY") or os.environ.get("PET_CHAT_BASE_URL"))


def _call(system_prompt, user_text, timeout, max_tokens=None, history=None):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ.get("PET_CHAT_API_KEY"),
                    base_url=os.environ.get("PET_CHAT_BASE_URL"))
    model = os.environ.get("PET_CHAT_MODEL", "deepseek-v4-flash")
    # history 是本场已经说过的话（[{role, content}, …]），夹在 system 和这一句之间。
    # 不传就退化成原来的单轮调用——回观众弹幕就该是单轮的，那边不需要上下文。
    #
    # 顺序很重要：system 固定在最前且整场不变，历史只在尾部追加。DeepSeek 的上下文缓存
    # **要求前缀完全一致**才命中（官方 guides/kv_cache），只往后面追加的话，前面所有轮次
    # 都还能吃到缓存；一旦回头改动 system 或中间某轮，整段缓存全部作废。
    msgs = [{"role": "system", "content": system_prompt}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user_text})
    resp = client.chat.completions.create(
        model=model,
        messages=msgs,
        max_tokens=max_tokens or 200,
        timeout=timeout,
        # DeepSeek 的 thinking 开关默认是 enabled（官方文档），不显式关掉会真的偷偷
        # 消耗 reasoning token——实测最坏能把 200 token 预算全吃光，正文变成空字符串。
        extra_body={"thinking": {"type": "disabled"}},
    )
    return (resp.choices[0].message.content or "").strip()


def reply(system_prompt, user_text, ts=0, timeout=10, max_tokens=None, history=None):
    """生成一句回复。未配置 key 或调用失败都不抛异常，返回角色口吻兜底话术。"""
    if not _configured():
        return random.choice(FALLBACK_LINES)
    try:
        text = _call(system_prompt, user_text, timeout, max_tokens, history)
        return text or random.choice(TIMEOUT_FALLBACK_LINES)
    except Exception:
        return random.choice(TIMEOUT_FALLBACK_LINES)
