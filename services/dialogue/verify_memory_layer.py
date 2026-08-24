#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记忆分层验证：本场对话上下文 + 跨场主播档案。**纯离线**，不调真实模型。

单独一个文件而不是塞进 `verify_dialogue.py`，因为这是一块独立的能力，而且那个文件已经很长了。

覆盖的是主播 2026-07-30 拍板的设计：
  主播 session = 一场直播，整场对话原样留在上下文（不压缩、不检索）
  下播总结"性格 + 相处方式"写进长期记忆，其余清空
  开播读长期记忆 -> 冻结进 system prompt，整场不再变
  软隔离：档案只给主播那条线用，回观众不带
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import chat                          # noqa: E402
import memory                        # noqa: E402
import streamer_profile              # noqa: E402

passed = failed = 0


def check(name, ok, detail=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ''))


# 把真实模型换成可观测的假实现：记录每次调用拿到的 system / history，返回可控内容。
CALLS = []
_real_reply = chat.reply


def fake_reply(system_prompt, user_text, ts=0, timeout=10, max_tokens=None, history=None):
    CALLS.append({'system': system_prompt, 'user': user_text,
                  'history': list(history or []), 'max_tokens': max_tokens})
    if '更新你对这位主播的了解' in user_text:
        return '他性格急，喜欢被怼两句，不爱听客套话。'
    return f'回复{len(CALLS)}'


chat.reply = fake_reply
from dialogue import Dialogue        # noqa: E402  （必须在换掉 chat.reply 之后导入）

P = lambda t, d, ts: {'channel': 'perception', 'type': t, 'ts': ts, 'data': d}
C = lambda t, d, ts: {'channel': 'command', 'type': t, 'ts': ts, 'data': d}


def say(dlg, text, ts):
    dlg.handle(P('audio.command', {'intent': 'chat', 'raw_text': text, 'speaker_verified': True}, ts))
    dlg.wait_idle()
    dlg.drain_outbox()


# ===== 1) 本场对话上下文 =====
CALLS.clear()
d = Dialogue(db_path=':memory:')
say(d, '我在二楼那个上锁的房间', 1000)
check('第一句没有历史可带', CALLS[-1]['history'] == [], str(CALLS[-1]['history']))

say(d, '就是刚才那个房间，钥匙在哪', 2000)
h = CALLS[-1]['history']
check('第二句带上了第一轮问答（她才接得住"刚才那个房间"）',
      len(h) == 2 and h[0]['role'] == 'user' and '二楼' in h[0]['content']
      and h[1]['role'] == 'assistant', str(h))

say(d, '第三句', 3000)
check('对话按顺序累积', len(CALLS[-1]['history']) == 4, str(len(CALLS[-1]['history'])))

check('主播侧额度比观众侧大', CALLS[-1]['max_tokens'] == 600, str(CALLS[-1]['max_tokens']))

# 护栏之后的文本才进历史——被拦下来的内容不该通过历史绕回模型面前
d._remember_turn('主播说的', '她回的')
turns = d.streamer_turns()
check('记进历史的是一问一答两条', turns[-2]['content'] == '主播说的' and turns[-1]['content'] == '她回的')

# ===== 2) 开播＝新 session =====
d.handle(C('stream_start', {}, 5000))
check('开播清空本场对话（上一场不带进来）', d.streamer_turns() == [])
CALLS.clear()
say(d, '新的一句', 6000)
check('开播后第一句又没有历史了', CALLS[-1]['history'] == [])

# ===== 3) 软隔离：档案只给主播那条线 =====
dbfile = os.path.join(tempfile.mkdtemp(prefix='petmem-'), 'm.db')
conn = memory.connect(dbfile)
streamer_profile.save(conn, '他性格急，喜欢被怼。', 1)
conn.close()

CALLS.clear()
d2 = Dialogue(db_path=dbfile)
check('开播时档案被读进 system prompt（冻结快照）', '喜欢被怼' in d2._system_prompt)
say(d2, '在吗', 1000)
check('跟主播说话用的是带档案的 prompt', '喜欢被怼' in CALLS[-1]['system'])

d2.handle(C('set_viewer_tier', {'nickname': '会员小明', 'tier': 'member'}, 1000))
CALLS.clear()
d2.handle(P('danmaku.chat', {'user': '会员小明', 'text': '主播好'}, 2000))
d2.wait_idle()
d2.drain_outbox()
check('⚠️ 回观众**不带**主播档案（软隔离：档案是主播的私人印象）',
      CALLS and '喜欢被怼' not in CALLS[-1]['system'], str(bool(CALLS)))

# ===== 4) 下播总结 =====
dbfile2 = os.path.join(tempfile.mkdtemp(prefix='petmem-'), 'm.db')
d3 = Dialogue(db_path=dbfile2)
say(d3, '今天好累啊', 1000)
d3.handle(C('stream_end', {}, 2000))
d3.wait_idle()
conn2 = memory.connect(dbfile2)
prof = streamer_profile.load(conn2)
check('下播把本场对话总结成档案落库', prof is not None and '喜欢被怼' in prof['profile'], str(prof))
check('档案记了攒过几场', prof and prof['sessions'] == 1, str(prof))
check('下播后本场对话被清空', d3.streamer_turns() == [])

d4 = Dialogue(db_path=dbfile2)
check('下一场开播读回档案（跨场生效）', '喜欢被怼' in d4._system_prompt)

# ===== 5) 兜底话术绝不能被当成档案写进去 =====
# 没配 key 时 chat.reply 会返回角色台词。那是"没生成出来"，不是"总结出来的档案"——
# 写进去会让她下一场带着一句莫名其妙的台词当人设。
dbfile3 = os.path.join(tempfile.mkdtemp(prefix='petmem-'), 'm.db')
d5 = Dialogue(db_path=dbfile3)
say(d5, '随便说一句', 1000)
chat.reply = lambda *a, **k: chat.FALLBACK_LINES[0]
try:
    d5.handle(C('stream_end', {}, 2000))
    d5.wait_idle()
finally:
    chat.reply = fake_reply
conn3 = memory.connect(dbfile3)
check('⚠️ 模型没给出有效内容时，保留旧档案而不是写进兜底台词',
      streamer_profile.load(conn3) is None, str(streamer_profile.load(conn3)))

# ===== 6) 档案存取本身 =====
conn4 = memory.connect(':memory:')
check('没有档案时返回 None（第一次开播她本来就不认识主播）', streamer_profile.load(conn4) is None)
check('空档案不写入', streamer_profile.save(conn4, '   ', 1) is False)
streamer_profile.save(conn4, 'x' * 5000, 1)
check(f'超长档案截断到 {streamer_profile.MAX_CHARS} 字（不能让它无限膨胀撑爆 prompt）',
      len(streamer_profile.load(conn4)['profile']) == streamer_profile.MAX_CHARS)
streamer_profile.save(conn4, '第二版', 2)
check('重写而不是追加（旧的被替换掉）', streamer_profile.load(conn4)['profile'] == '第二版')
# 上面那次空档案 save 返回 False 没写入，所以到这里只成功写过两次
check('攒的场次只在真正写入时累加（空档案那次不算）',
      streamer_profile.load(conn4)['sessions'] == 2, str(streamer_profile.load(conn4)['sessions']))
check('没有档案时 prompt 段落是空串，不留占位', streamer_profile.as_prompt_block(None) == '')

p = streamer_profile.build_summary_prompt('旧档案内容', [{'role': 'user', 'content': '我怕突然出现的脸'}])
check('总结提示词带上旧档案和本场对话', '旧档案内容' in p and '突然出现的脸' in p)
check('总结提示词要求重写而不是追加', '重写' in p)
check('总结提示词明确不记进度类的事', '进度' in p)

chat.reply = _real_reply
print(f"\n==== 记忆分层 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
