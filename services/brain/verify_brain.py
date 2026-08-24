#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策层离线验证（纯标准库）：把感知/控制事件喂进 Brain，断言产出的 action 正确。
运行: python verify_brain.py   退出码 0=全过。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import brain as B  # noqa: E402
from brain import Brain  # noqa: E402

passed = 0
failed = 0


def check(name, ok, detail=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ''))


def types(msgs):
    return [m['type'] for m in msgs]


def speak_text(msgs):
    return ' '.join(m['data'].get('text', '') for m in msgs if m['type'] == 'speak')


def motion(msgs):
    return [m['data'].get('motion') for m in msgs if m['type'] == 'play_motion']


def bubble_text(msgs):
    return ' '.join(m['data'].get('text', '') for m in msgs if m['type'] == 'show_bubble')


P = lambda t, d, ts: {'channel': 'perception', 'type': t, 'ts': ts, 'data': d}
C = lambda t, d, ts: {'channel': 'command', 'type': t, 'ts': ts, 'data': d}

# 进场欢迎（含昵称、挥手）——08-01 改成纯气泡+窗口制，见 brain.py 里 WELCOME_WINDOW_MS 注释
b = Brain()
o = b.handle(P('danmaku.enter', {'user': '夜行猫'}, 1000))
check('进场 -> 挥手+欢迎气泡(含昵称)', 'wave' in motion(o) and '夜行猫' in bubble_text(o), str(types(o)))
check('进场欢迎不出声（观众源源不断进场时曾几乎连续说话，改气泡）', not any(m['type'] == 'speak' for m in o))

# 礼物分级。10 抖币是"出不出声"的门槛（GIFT_SPEAK_MIN_COINS），刚好卡在门槛上要出声。
small = b.handle(P('danmaku.gift', {'user': 'A', 'gift_name': '小心心', 'count': 1, 'value_coins': 10}, 2000))
check('小礼物(10抖币=门槛) -> thank_small + 仍出声', 'thank_small' in motion(small) and '小心心' in speak_text(small), str(types(small)))
check('中礼物(1000抖币) -> thank_big', 'thank_big' in motion(b.handle(P('danmaku.gift', {'user': 'B', 'gift_name': '玫瑰', 'count': 1, 'value_coins': 1000}, 3000))))
big = b.handle(P('danmaku.gift', {'user': '神秘大哥', 'gift_name': '嘉年华', 'count': 1, 'value_coins': 30000}, 4000))
check('大礼物(30000抖币) -> thank_big + 壕/大哥文案', 'thank_big' in motion(big) and ('大哥' in speak_text(big) or '壕' in speak_text(big)), speak_text(big))

# 被吓
check('game.scare 强度0.9 -> scared+气泡', 'scared' in motion(b.handle(P('game.scare', {'intensity': 0.9}, 6000))))
# 卡关提示
sc = b.handle(P('game.scene', {'summary': 's', 'tags': [], 'stuck': True, 'hint': '查保险箱密码'}, 7000))
check('game.scene 卡关 -> 说出攻略提示', '保险箱' in speak_text(sc), speak_text(sc))
# 攻略必须"说出来 + 留一条长气泡"。speak 的气泡播完音频就没了，而攻略是要照着做的信息，
# 主播多半正忙着操作没法立刻抬头（2026-08-02 真开播反馈："我也没有找到哪里可以查看"）。
check('攻略除了出声还留一条文字气泡（听完能回头看）',
      any(m['type'] == 'show_bubble' and '保险箱' in m['data']['text'] for m in sc), str(types(sc)))
check(f'攻略气泡停留 {B.WALKTHROUGH_BUBBLE_MS // 1000} 秒，比普通气泡长得多',
      all(m['data'].get('duration_ms') == B.WALKTHROUGH_BUBBLE_MS for m in sc if m['type'] == 'show_bubble')
      and B.WALKTHROUGH_BUBBLE_MS > B.GIFT_BUBBLE_MS, str(B.WALKTHROUGH_BUBBLE_MS))
# 插件倒计时
check('plugin.state 剩5分钟 -> 提醒气泡', any(m['type'] == 'show_bubble' for m in b.handle(P('plugin.state', {'countdown_sec': 300}, 8000))))
# 微额礼物（1 抖币的粉丝团灯牌之类，实测占了一场里的大半）：只做动作 + 文字气泡，绝不出声。
# 这条断言是新规则的守门人：谁把它改回 speak，就是把"桌宠一直出声盖过主播"的问题改回来了。
tiny = b.handle(P('danmaku.gift', {'user': '小灯牌', 'gift_name': '粉丝团灯牌', 'count': 1, 'value_coins': 1}, 8500))
check('微额礼物(1抖币) -> 不出声，只有 thank_small + 气泡',
      'thank_small' in motion(tiny) and not any(m['type'] == 'speak' for m in tiny)
      and '粉丝团灯牌' in bubble_text(tiny), str(types(tiny)))
check('微额礼物气泡停留 8 秒（替代一句语音，要够时间看清）',
      all(m['data'].get('duration_ms') == 8000 for m in tiny if m['type'] == 'show_bubble'))

# 关注：出声 + 顺势要灯牌（关注是最容易转粉丝团的时刻，所以值得占用声音）
fw = b.handle(P('danmaku.follow', {'user': '新粉'}, 9000))
check('关注 -> 出声答谢（含昵称）', '关注' in speak_text(fw) and '新粉' in speak_text(fw), speak_text(fw))
check('关注答谢顺势要灯牌', '灯牌' in speak_text(fw), speak_text(fw))
check('每条关注答谢话术都带昵称占位和灯牌',
      all('{user}' in s and '灯牌' in s for s in B.FOLLOW_THANKS), str(len(B.FOLLOW_THANKS)))

# 闭嘴/休息 状态机
b2 = Brain()
mute_out = b2.handle(C('mute', {}, 100))
check('mute -> 发 stop 且进入 QUIET', 'stop' in types(mute_out) and b2.mode == 'QUIET')
check('QUIET 下进场不产生任何 action（安静）', b2.handle(P('danmaku.enter', {'user': 'X'}, 200)) == [])
b2.handle(C('wake', {}, 300))
check('wake -> 恢复 ACTIVE 且能再欢迎', 'wave' in motion(b2.handle(P('danmaku.enter', {'user': 'Y'}, 5000))) and b2.mode == 'ACTIVE')

# 只认主播语音（声纹）
b3 = Brain()
check('非主播语音 mute 被忽略(mode 不变)', b3.handle(P('audio.command', {'intent': 'mute', 'raw_text': '闭嘴', 'speaker_verified': False}, 100)) == [] and b3.mode == 'ACTIVE')
b3.handle(P('audio.command', {'intent': 'mute', 'raw_text': '闭嘴', 'speaker_verified': True}, 200))
check('主播语音 mute 生效 -> QUIET', b3.mode == 'QUIET')

# 记昵称（欢迎/dialogue 服务用）+ tick 无待办时不出话
b4 = Brain()
b4.handle(P('danmaku.gift', {'user': '土豪哥', 'gift_name': '嘉年华', 'count': 1, 'value_coins': 30000}, 100))
check('记住观众昵称', '土豪哥' in b4.recent)
check('无合批欢迎待办时 tick 不出话', b4.tick(1000) == [])
b4.handle(C('mute', {}, 2000))
check('闭嘴后 tick 不出话', b4.tick(40000) == [])

# 整蛊计数变化 → 反应
b5 = Brain()
b5.handle(P('plugin.state', {'prank_count': 7}, 1))                       # 首次只记录
check('整蛊首次只记录不反应', b5.handle(P('plugin.state', {'prank_count': 7}, 2)) == [])
r5 = b5.handle(P('plugin.state', {'prank_count': 8}, 3))
check('整蛊计数+1 → 被吓反应 + 说"第8次"',
      any(a['type'] == 'speak' and '8' in a['data']['text'] for a in r5)
      and any(a['type'] == 'play_motion' and a['data']['motion'] == 'scared' for a in r5), str([a['type'] for a in r5]))

# 合批欢迎：短时多进场 → 首个立即欢迎、其余 tick 合并（都在欢迎窗口内，纯气泡）
b6 = Brain()
r6a = b6.handle(P('danmaku.enter', {'user': 'A'}, 1))
b6.handle(P('danmaku.enter', {'user': 'B'}, 500))       # 冷却内 → 入队
b6.handle(P('danmaku.enter', {'user': 'C'}, 800))       # 入队
check('首个进场立即欢迎气泡(含A)', any(a['type'] == 'show_bubble' and 'A' in a['data']['text'] for a in r6a))
fl = b6.tick(5000)
check('合批欢迎：tick 一起欢迎 B、C(气泡)', any(a['type'] == 'show_bubble' and 'B' in a['data']['text'] and 'C' in a['data']['text'] for a in fl), str([a['type'] for a in fl]))

# 欢迎窗口：每 WELCOME_CYCLE_MS 只开放 WELCOME_WINDOW_MS，08-01 首场真开播发现"来一个欢迎
# 一个"在观众源源不断进场时几乎连续出声，改成窗口制（见 brain.py 里 WELCOME_WINDOW_MS 注释）。
b10 = Brain()
r10a = b10.handle(P('danmaku.enter', {'user': '开局'}, 0))
check('欢迎窗口打开(刚开播)时进场 -> 挥手+气泡欢迎', 'wave' in motion(r10a) and '开局' in bubble_text(r10a), str(types(r10a)))
r10b = b10.handle(P('danmaku.enter', {'user': '窗口关了'}, B.WELCOME_WINDOW_MS + 1000))
check('欢迎窗口关闭期间进场 -> 安静不欢迎', r10b == [], str(types(r10b)))
check('窗口关闭期间 tick 也不补欢迎（没入队）', b10.tick(B.WELCOME_WINDOW_MS + 1000) == [])
r10c = b10.handle(P('danmaku.enter', {'user': '下一轮'}, B.WELCOME_CYCLE_MS))
check('下一个周期窗口重新打开 -> 恢复欢迎', 'wave' in motion(r10c) and '下一轮' in bubble_text(r10c), str(types(r10c)))

# 定时主动互动（提醒点关注/加灯牌）
IV = B.PROACTIVE_INTERVAL_MS
b7 = Brain()
check('首次 tick 只对时不主动说话（刚开播房间还空着）', b7.tick(0) == [])
check('未到间隔不主动说话', b7.tick(IV - 1) == [])
pro = b7.tick(IV)
check('满 10 分钟 -> 主动互动出声（气泡没人看，必须 speak）',
      types(pro) == ['speak'] and speak_text(pro) in B.PROACTIVE_LINES, str(types(pro)))
check('说完立刻再 tick 不会连发', b7.tick(IV + 1) == [])

# 闭嘴/休息期间不能主动开口——主播喊了闭嘴还自己冒话是最扎眼的故障
b8 = Brain()
b8.tick(0)
b8.handle(C('mute', {}, 1))
check('QUIET 下到点也不主动说话', b8.tick(IV * 5) == [])
b8.handle(C('sleep', {}, 2))
check('SLEEP 下到点也不主动说话', b8.tick(IV * 9) == [])

# 轮播：一轮之内不重复（连着听到同一句会像复读机）
b9 = Brain()
b9.tick(0)
spoken = [speak_text(b9.tick(IV * (i + 1))) for i in range(len(B.PROACTIVE_LINES))]
check('一轮轮播刚好覆盖全部话术且不重复', sorted(spoken) == sorted(B.PROACTIVE_LINES), str(len(set(spoken))))
check('跨轮衔接处不重复（新一轮第一句 != 上一轮最后一句）', b9._next_proactive() != spoken[-1], spoken[-1])

# 合规红线：主动互动/关注答谢的文案不能踩平台禁用词，也不能变成"求打赏"。
# 直接复用 dialogue 服务那份运行时禁用词清单，避免两边各写一份而漂移；
# 那个服务可能正在改，导入不到就退化成本地兜底清单，不因此判失败。
try:
    sys.path.insert(0, os.path.join(HERE, '..', 'dialogue'))
    from guardrails import find_banned  # noqa: E402
    _src = 'dialogue/guardrails.py'
except Exception:                                    # pragma: no cover - 仅在依赖缺失时走到
    _BANNED = ('游戏', '玩家', '抽奖', '盲盒', '礼盒', '背包', '血条')
    find_banned = lambda s: next((w for w in _BANNED if w in s), None)   # noqa: E731
    _src = '本地兜底清单'
_lines = list(B.PROACTIVE_LINES) + list(B.FOLLOW_THANKS)
_hit = [(s, find_banned(s)) for s in _lines if find_banned(s)]
check(f'主动互动/关注文案无平台禁用词（清单来源：{_src}）', not _hit, str(_hit))
check('主动互动文案不含求打赏/求礼物表述',
      not [s for s in B.PROACTIVE_LINES if '打赏' in s or '礼物' in s or '刷个' in s])
check('主动互动话术凑够 10 条左右（少了会明显循环）', len(B.PROACTIVE_LINES) >= 8, str(len(B.PROACTIVE_LINES)))

print(f"\n==== 决策层验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
