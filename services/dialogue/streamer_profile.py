#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主播档案：跨场记住"主播是个什么样的人、跟她怎么相处"。纯存取，不调模型。

跟 `memory.py` 的观众画像分开成两个模块，因为是两件事：观众那边是**很多人、结构化字段、
会淘汰**；主播这边是**一个人、一段自然语言、不淘汰**。共用同一个 sqlite 连接，各建各的表。

**记什么**（主播 2026-07-30 定）：性格 + 相处方式。不记"玩到第几关"这类会过期的进度——
那种东西记错了比不记更糟，而且下一场大概率已经变了。

**为什么是一段自然语言而不是结构化字段**：性格和相处方式没法拆成列。但因此它是模型写的，
有上限约束（见 MAX_CHARS）：上限不是省钱，是逼它每次重写时做取舍、只留真正稳定的特征，
否则档案会越滚越长，最后变成一坨没人读得下去的流水账。

**冻结快照**：开播时读一次，塞进 system prompt 前缀，**整场不再变**。DeepSeek 的上下文缓存
要求前缀完全一致才命中（官方 guides/kv_cache），中途改 system prompt 会让整场缓存作废。
所以下播才写，写完下一场生效。
"""

MAX_CHARS = 600          # 档案上限。约 400 token，够写清性格和相处方式，又逼着做取舍

_SCHEMA = """
CREATE TABLE IF NOT EXISTS streamer_profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),   -- 只有一行：主播就一个人
  profile TEXT,
  updated_ts INTEGER,
  sessions INTEGER DEFAULT 0               -- 这份档案是攒了几场攒出来的
)
"""


def ensure(conn):
    conn.execute(_SCHEMA)
    conn.commit()


def load(conn):
    """读档案。没有就返回 None——第一次开播时她本来就不认识主播，不该假装认识。"""
    ensure(conn)
    row = conn.execute("SELECT profile, sessions FROM streamer_profile WHERE id=1").fetchone()
    if not row or not row[0]:
        return None
    return {'profile': row[0], 'sessions': row[1] or 0}


def save(conn, profile, ts=0):
    """写档案。超长直接截断——宁可留半句，也不要让它无限膨胀把 system prompt 撑爆。"""
    ensure(conn)
    text = (profile or '').strip()[:MAX_CHARS]
    if not text:
        return False
    cur = load(conn)
    sessions = (cur['sessions'] if cur else 0) + 1
    conn.execute(
        "INSERT INTO streamer_profile(id, profile, updated_ts, sessions) VALUES (1,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET profile=excluded.profile, updated_ts=excluded.updated_ts, "
        "sessions=excluded.sessions",
        (text, ts, sessions))
    conn.commit()
    return True


def build_summary_prompt(old_profile, turns):
    """下播时让模型重写档案用的提示词。

    刻意做成"拿旧档案 + 本场对话，**重写**出新档案"而不是"追加一段"：
    追加会让档案越滚越长且自相矛盾（"他喜欢被怼"和"他喜欢被哄"并存），重写则强迫它
    在冲突时做判断。这也是把上限设成硬约束的原因。
    """
    convo = '\n'.join(
        f"{'主播' if t.get('role') == 'user' else '魔丸'}：{t.get('content', '')}"
        for t in (turns or []))
    old = old_profile or '（还没有档案，这是第一次）'
    return f"""下面是你这一场跟主播的完整对话。请更新你对这位主播的了解。

【你原来记的】
{old}

【本场对话】
{convo}

要求：
1. **只写两件事**：主播是个什么样的人（性格）、跟他相处该用什么方式。
2. **不要写进度类的事**（玩到第几关、这场做了什么）——下一场就过期了，记错比不记更糟。
3. 把旧档案和本场新看到的**合并重写成一段**，不是在后面追加。有冲突时以更多次出现的为准。
4. 只在**反复出现**的地方下结论，一次性的玩笑话不算性格。
5. 控制在 {MAX_CHARS} 字以内，用第二人称"他"称呼主播。
6. 只输出档案正文，不要解释、不要标题。

如果本场对话太少、看不出什么新东西，就把原来的档案原样输出。"""


def as_prompt_block(profile):
    """把档案拼成 system prompt 里的一段。没有档案就返回空串，不留占位。"""
    if not profile:
        return ''
    return ("\n\n【你记得的主播】（跨场攒下来的印象，别直接背出来，自然地用）\n"
            f"{profile}\n")
