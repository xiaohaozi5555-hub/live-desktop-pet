#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue 离线验证（纯标准库，无需真调 DeepSeek 也能验证大部分逻辑，做法照抄
`perception-game/verify_vision.py`"key 外部问题记 WARN 不判失败"）：
覆盖分级判断规则、送礼排名计算、记忆表读写、禁用词护栏拦截、卡关二次确认状态机、
模式镜像、DeepSeek 未配置 key 时的角色口吻兜底、"运行时不能卡住"的后台 worker 队列
架构（handle() 绝不同步等 DeepSeek）、以及分级录入解耦（command.set_viewer_tier）
和开播/下播 session 生命周期（command.stream_start/stream_end，含常客判定写 note）。
运行: python verify_dialogue.py   退出码 0=全过。"""
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
import validate as contract        # noqa: E402
import chat                        # noqa: E402
import guardrails                  # noqa: E402
import memory                      # noqa: E402
import persona                     # noqa: E402
import dialogue as D                # noqa: E402
from dialogue import Dialogue      # noqa: E402

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


P = lambda t, d, ts: {'channel': 'perception', 'type': t, 'ts': ts, 'data': d}
C = lambda t, d, ts: {'channel': 'command', 'type': t, 'ts': ts, 'data': d}


def reply_of(dlg, ev, timeout=5.0):
    """模拟 run.py 的完整行为：handle() 同步产出 + 等 worker 把入队任务消化完再取
    outbox 里异步生成的 action，两部分合并返回。大多数会触发 LLM 回复的用例都该用
    这个而不是裸调 handle()——LLM 回复不再同步返回，见 dialogue.py"运行时不能卡住"。"""
    sync_out = dlg.handle(ev)
    dlg.wait_idle(timeout=timeout)
    return sync_out + dlg.drain_outbox()


def _mark_tier(dlg, nickname, tier, ts=0):
    """测试用：走真实的 command.set_viewer_tier 事件模拟"主播在 control-panel 打标"——
    不再是直接写库的 hack，_set_viewer_tier() 已经实现，这就是它的真实调用路径。"""
    dlg.handle(C('set_viewer_tier', {'nickname': nickname, 'tier': tier}, ts))


# ===== 1) 分级判断规则：谁值得触发 LLM 回复 =====
d1 = Dialogue(db_path=':memory:')
out = d1.handle(P('danmaku.chat', {'user': '路人甲', 'text': '主播好'}, 1000))
check('普通观众弹幕默认不逐条回复', out == [], str(out))

_mark_tier(d1, '会员小明', 'member')
out_m = reply_of(d1, P('danmaku.chat', {'user': '会员小明', 'text': '主播好'}, 2000))
check('会员发弹幕会被回复(经后台 worker 异步生成)', any(m['type'] == 'speak' for m in out_m), str(types(out_m)))

_mark_tier(d1, '星守护老王', 'star_guardian')
out_s = reply_of(d1, P('danmaku.chat', {'user': '星守护老王', 'text': '在的在的'}, 3000))
# 2026-07-30 语音配速之后：VIP 仍然**必回**，但出不出声要看配速（这条紧接着上一条会员回复，
# 距上次出声不到 2 分钟，所以走纯文字气泡）。断言改成"有回复"而不是"有语音"——
# 必回 ≠ 必出声，这正是配速要达到的效果。
check('星守护发弹幕会被回复（出声与否看配速）',
      any(m['type'] in ('speak', 'show_bubble') for m in out_s), str(types(out_s)))

# ===== 2) 送礼排名计算：本场前三名可回复，第四名不行 =====
d2 = Dialogue(db_path=':memory:')
d2.handle(P('danmaku.gift', {'user': 'A', 'gift_name': '小心心', 'count': 1, 'value_coins': 100}, 1000))
d2.handle(P('danmaku.gift', {'user': 'B', 'gift_name': '玫瑰', 'count': 1, 'value_coins': 500}, 2000))
out_c_gift = reply_of(d2, P('danmaku.gift', {'user': 'C', 'gift_name': '嘉年华', 'count': 1, 'value_coins': 30000}, 3000))
d2.handle(P('danmaku.gift', {'user': 'D', 'gift_name': '小心心', 'count': 1, 'value_coins': 50}, 4000))
d2.wait_idle()          # A/B 这两次送礼因为当时也在"前三名"里，同样会各自入队一条追加感谢；
d2.drain_outbox()       # 消化掉、扔掉，避免残留到下面的断言里污染"恰好 X 条"这类计数
check('送礼前三名计算正确(A/B/C进前三，D不进)', d2._top_gift_users() == {'A', 'B', 'C'}, str(d2._top_gift_users()))
# 2026-07-30 分层后：出不出声按送礼人是否 VIP 判定。这里是普通观众送小额礼物，
# 所以是纯文字气泡（show_bubble）而不是语音——小额礼物很频繁，每次出声会吵。
check('送礼当次若进前三，追加个性化感谢（普通观众→纯文字气泡）',
      any(m['type'] == 'show_bubble' for m in out_c_gift), str(types(out_c_gift)))

out_d_chat = d2.handle(P('danmaku.chat', {'user': 'D', 'text': '你好'}, 5000))
check('送礼未进前三(第4名)发弹幕不触发回复', out_d_chat == [], str(out_d_chat))
# C 在 ts=3000 送礼时已经触发过一次追加感谢(占用了同人冷却)，这里 ts 要跳出 COOLDOWN_PER_USER_MS 才能再次触发
out_c_chat = reply_of(d2, P('danmaku.chat', {'user': 'C', 'text': '你好'}, 3000 + D.COOLDOWN_PER_USER_MS + 1000))
check('送礼前三名发弹幕会被回复(冷却期外)', any(m['type'] == 'speak' for m in out_c_chat), str(types(out_c_chat)))

# ===== 3) 节流：同人冷却 + 每分钟上限 =====
d3 = Dialogue(db_path=':memory:')
_mark_tier(d3, '常客', 'member')
r1 = reply_of(d3, P('danmaku.chat', {'user': '常客', 'text': '第一句'}, 1000))
r2 = d3.handle(P('danmaku.chat', {'user': '常客', 'text': '第二句'}, 1500))   # 冷却内，直接被节流拦下，不会入队
check('同人冷却内连续发弹幕，第二次不重复回复', any(m['type'] == 'speak' for m in r1) and r2 == [], str((types(r1), types(r2))))

# ===== 4) 记忆表读写（三层记忆里的第 3 层，观众画像）=====
db = memory.connect(':memory:')
check('新观众查询返回 None', memory.get_viewer(db, '张三') is None)
memory.upsert_viewer(db, '张三', 1000, gift_total=200)
v = memory.get_viewer(db, '张三')
check('写入后能查到(送礼额+最近出现时间)', v is not None and v['gift_total_session'] == 200 and v['last_seen_ts'] == 1000, str(v))
memory.upsert_viewer(db, '张三', 2000)   # gift_total=None -> 不改该字段
v2 = memory.get_viewer(db, '张三')
check('gift_total=None 时不覆盖原值，只更新 last_seen', v2['gift_total_session'] == 200 and v2['last_seen_ts'] == 2000, str(v2))
check('新观众默认 tier=normal', memory.get_viewer(db, '张三')['tier'] == 'normal')

# ===== 5) 合规护栏：禁用词拦截 =====
check('含禁用词 -> 换安全兜底模板', guardrails.enforce('这是个游戏体验') in guardrails.SAFE_FALLBACKS)
check('不含禁用词 -> 原样返回', guardrails.enforce('今天天气不错') == '今天天气不错')
check('大小写不敏感也能拦(buff)', guardrails.find_banned('这个buff效果不错') == 'BUFF')
check('安全兜底模板本身不含禁用词(不会被自己拦自己)', all(guardrails.find_banned(t) is None for t in guardrails.SAFE_FALLBACKS))

_checklist = os.path.join(REPO, '提案Demo自查清单.md')
_m = re.search(r'官方禁用词[：:]\s*(.+)', open(_checklist, encoding='utf-8').read()) if os.path.exists(_checklist) else None
if _m:
    _doc_words = {w.strip() for w in _m.group(1).split('、') if w.strip()}
    check('guardrails.BANNED_WORDS 与提案清单同步(一份清单两个用途)',
          _doc_words == set(guardrails.BANNED_WORDS),
          f'清单独有={_doc_words - set(guardrails.BANNED_WORDS)} 代码独有={set(guardrails.BANNED_WORDS) - _doc_words}')
else:
    check('能在提案清单里定位到官方禁用词一行', False, '未找到/正则未匹配——清单格式是否变了？')

# ===== 6) DeepSeek 未配置 key 时：角色口吻兜底，不抛异常 =====
_saved_env = {k: os.environ.pop(k, None) for k in ('PET_CHAT_API_KEY', 'PET_CHAT_BASE_URL', 'PET_CHAT_MODEL')}
try:
    text = chat.reply(persona.SYSTEM_PROMPT, '你好', ts=1)
    check('未配置 key 时 reply() 不抛异常', True)
    check('未配置 key 时返回角色口吻兜底文案', text in chat.FALLBACK_LINES, text)
    safe = guardrails.enforce(text)
    check('兜底文案本身也能顺利通过护栏', safe == text, safe)
finally:
    for k, v in _saved_env.items():
        if v is not None:
            os.environ[k] = v

# ===== 7) 卡关攻略：显式触发 + 二次确认（跟自动检测并存不互斥）=====
# 注：反问/确认/拒绝三种回应都是静态模板，不调 LLM，所以整套状态机全程同步，不需要 reply_of。
d4 = Dialogue(db_path=':memory:')
ask = d4.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '魔丸魔丸我卡关了', 'speaker_verified': True}, 1000))
check('"卡关"语音 -> 先反问确认，不直接触发分析', types(ask) == ['speak'] and '攻略' in speak_text(ask), str(ask))

confirm = d4.handle(P('audio.command', {'intent': 'chat', 'raw_text': '是的', 'speaker_verified': True}, 2000))
check('确认("是的") -> 发 command.do{walkthrough} 触发 perception-game 截图分析',
      any(m['channel'] == 'command' and m['type'] == 'do' and m['data'].get('action') == 'walkthrough' for m in confirm),
      str(confirm))

# ⚠️ 这条是 2026-07-30 修掉的真 bug：确认环节曾经把主播最初那句原话整个丢掉，只传一个"是"。
# 而那段原话（第几关、哪个房间、上一个任务是什么）**正是联网搜攻略时唯一有用的线索**。
_wt = next(m for m in confirm if m['channel'] == 'command' and m['type'] == 'do')
check('⚠️ 确认后的命令必须带上最初那句原话（不能只剩一个"是"）',
      '卡关' in (_wt['data'].get('note') or ''), str(_wt['data']))

d4b = Dialogue(db_path=':memory:')
d4b.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '魔丸我卡关了', 'speaker_verified': True}, 1000))
c2 = d4b.handle(P('audio.command', {'intent': 'chat', 'raw_text': '是，我在二楼那个上锁的房间', 'speaker_verified': True}, 2000))
_wt2 = next(m for m in c2 if m['channel'] == 'command' and m['type'] == 'do')
check('确认时又补充了细节 -> 原话和补充都要留着',
      '卡关' in _wt2['data']['note'] and '二楼' in _wt2['data']['note'], str(_wt2['data']))

# 上面那条顺带修出的真实交互缺陷：人回答时常是「是，<补充>」，句首肯定 + 直接跟内容。
# 原先表里只有"是的/是啊"没有单字"是"，这类回答会被判成拒绝。
import dialogue as _dm  # noqa: E402
check('"是，我在二楼" 算确认', _dm._is_affirmative('是，我在二楼那个上锁的房间'))
check('"对，就是那里" 算确认', _dm._is_affirmative('对，就是那里'))
check('"不用了" 仍然算拒绝', not _dm._is_affirmative('不用了'))
check('"但是我还没试过" 不算确认（句中的"是"不能误判）',
      not _dm._is_affirmative('但是我还没试过'), '但是我还没试过')
check('"算了" 仍然算拒绝', not _dm._is_affirmative('算了'))

# 明说"帮我找攻略"就不该再反问一句——直播时白等十几秒，原话还容易在确认环节丢掉
d4c = Dialogue(db_path=':memory:')
direct = d4c.handle(P('audio.command', {
    'intent': 'walkthrough',
    'raw_text': '魔丸帮我找找攻略，游戏名是层层恐惧，我在画室找不到碎片',
    'speaker_verified': True}, 1000))
_dwt = [m for m in direct if m['channel'] == 'command' and m['type'] == 'do']
check('明说"帮我找攻略" -> 直接触发，不反问', len(_dwt) == 1, str(types(direct)))
check('直接触发时整句原话都带上',
      '层层恐惧' in _dwt[0]['data'].get('note', '') and '画室' in _dwt[0]['data'].get('note', ''),
      str(_dwt[0]['data']))

d5 = Dialogue(db_path=':memory:')
d5.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '我卡关了', 'speaker_verified': True}, 1000))
decline = d5.handle(P('audio.command', {'intent': 'chat', 'raw_text': '算了不用了', 'speaker_verified': True}, 2000))
check('不确认("算了不用了") -> 回退兜底话术，不触发分析',
      types(decline) == ['speak'] and '敏感' in speak_text(decline)
      and not any(m['channel'] == 'command' for m in decline), str(decline))

d6 = Dialogue(db_path=':memory:')
d6.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '我卡关了', 'speaker_verified': True}, 1000))
late = d6.handle(P('audio.command', {'intent': 'chat', 'raw_text': '是的呀',
                    'speaker_verified': True}, 1000 + D.WALKTHROUGH_CONFIRM_WINDOW_MS + 5000))
check('超过确认窗口后才回答的("是的呀")不算数，不误触发分析',
      not any(m['channel'] == 'command' and m['type'] == 'do' for m in late), str(late))
d6.wait_idle()   # 上面这句超时后落回"自由聊天"逻辑，会悄悄入队一条 streamer 回复；等它跑完避免悬空线程串到下个用例

d7 = Dialogue(db_path=':memory:')
out_unverified = d7.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '我卡关了', 'speaker_verified': False}, 100))
check('非主播语音(speaker_verified=False)被丢弃', out_unverified == [], str(out_unverified))

# ===== 8) 主播批量指令："看一下弹幕帮我回一下" =====
d8 = Dialogue(db_path=':memory:')
d8.handle(P('danmaku.chat', {'user': '甲', 'text': '弹幕1'}, 100))
d8.handle(P('danmaku.chat', {'user': '乙', 'text': '弹幕2'}, 200))
batch = reply_of(d8, P('audio.command', {'intent': 'review_chat', 'raw_text': '看一下弹幕帮我回一下', 'speaker_verified': True}, 300))
# 批量回复（主播喊"看一下弹幕"）同样逐人分层。这两位是普通观众，所以都是纯文字气泡——
# 十条语音连着念是最吵的情况，正是要避免的。
check('批量回弹幕：缓冲区 2 条弹幕产出 2 条回复（普通观众→纯文字气泡）',
      len([m for m in batch if m['type'] == 'show_bubble']) == 2, str(types(batch)))

# ===== 9) 模式镜像：ACTIVE/QUIET/SLEEP，command 与语音指令效果一致 =====
d9 = Dialogue(db_path=':memory:')
d9.handle(C('mute', {}, 100))
check('command.mute -> QUIET', d9.mode == 'QUIET')
_mark_tier(d9, '会员', 'member')
out_quiet = d9.handle(P('danmaku.chat', {'user': '会员', 'text': 'hi'}, 200))
check('QUIET 时会员弹幕也不回复(安静看着不动)', out_quiet == [], str(out_quiet))
ask_quiet = d9.handle(P('audio.command', {'intent': 'walkthrough_ask', 'raw_text': '卡关了', 'speaker_verified': True}, 300))
check('QUIET 时卡关语音也不主动开口反问', ask_quiet == [], str(ask_quiet))
d9.handle(P('audio.command', {'intent': 'wake', 'raw_text': '醒醒', 'speaker_verified': True}, 400))
check('语音 wake 也能唤醒(mode 镜像语音指令，跟 command 等效)', d9.mode == 'ACTIVE')

# ===== 10) 端到端：产出消息均通过契约校验（handle()/worker 内部已过滤，这里显式再确认一遍没有被吞掉不该吞的）=====
check('批量回复的每条 speak 都契约合法', batch and all(not contract.validate_message(m) for m in batch), str(batch))
check('确认攻略产出的 command.do 契约合法', confirm and all(not contract.validate_message(m) for m in confirm), str(confirm))

# ===== 11) 分级录入解耦：command.set_viewer_tier 写自己的 viewers 表（两边不共享数据库文件）=====
d12 = Dialogue(db_path=':memory:')
check('set_viewer_tier 事件本身契约合法',
      not contract.validate_message(C('set_viewer_tier', {'nickname': '小明', 'tier': 'member'}, 1)))
d12.handle(C('set_viewer_tier', {'nickname': '小明', 'tier': 'member'}, 1000))
v12 = d12._get_viewer('小明')
check('set_viewer_tier 写入 tier 字段(新观众)', v12 is not None and v12['tier'] == 'member', str(v12))
out12 = reply_of(d12, P('danmaku.chat', {'user': '小明', 'text': '你好'}, 2000))
check('打标为 member 后端到端：发弹幕真的会被回复', any(m['type'] == 'speak' for m in out12), str(types(out12)))

d12.handle(P('danmaku.gift', {'user': '老王', 'gift_name': '小心心', 'count': 1, 'value_coins': 10}, 3000))
d12.wait_idle()
d12.drain_outbox()
check('打标前 tier 默认 normal(已存在的观众)', d12._get_viewer('老王')['tier'] == 'normal')
d12.handle(C('set_viewer_tier', {'nickname': '老王', 'tier': 'star_guardian'}, 4000))
check('已存在的观众也能被打标更新(不是只有新建路径)', d12._get_viewer('老王')['tier'] == 'star_guardian')

# 自动分级（perception-danmaku 依据粉丝团等级发的 source=auto）与手动的优先级
# 核心规则：**手动优先**——主播掌握弹幕之外的信息，机器不该拿一个等级数字推翻他
d12.handle(C('set_viewer_tier', {'nickname': '自动君', 'tier': 'member', 'source': 'auto'}, 5000))
check('自动分级能给新观众打标', d12._get_viewer('自动君')['tier'] == 'member')
d12.handle(C('set_viewer_tier', {'nickname': '自动君', 'tier': 'star_guardian', 'source': 'auto'}, 5100))
check('自动打的标可以被自动更新', d12._get_viewer('自动君')['tier'] == 'star_guardian')
d12.handle(C('set_viewer_tier', {'nickname': '自动君', 'tier': 'normal'}, 5200))
check('主播手动可以覆盖自动的判定', d12._get_viewer('自动君')['tier'] == 'normal')
d12.handle(C('set_viewer_tier', {'nickname': '自动君', 'tier': 'star_guardian', 'source': 'auto'}, 5300))
check('**手动打过标之后，自动判定不能再覆盖它**', d12._get_viewer('自动君')['tier'] == 'normal',
      str(d12._get_viewer('自动君')))
check('老王(手动打的star)也不会被自动降级',
      (d12.handle(C('set_viewer_tier', {'nickname': '老王', 'tier': 'member', 'source': 'auto'}, 5400)),
       d12._get_viewer('老王')['tier'])[1] == 'star_guardian')
check('不带 source 的老消息按手动处理（control-panel 向后兼容）',
      (d12.handle(C('set_viewer_tier', {'nickname': '兼容君', 'tier': 'member'}, 5500)),
       d12.handle(C('set_viewer_tier', {'nickname': '兼容君', 'tier': 'normal', 'source': 'auto'}, 5600)),
       d12._get_viewer('兼容君')['tier'])[2] == 'member')

# ===== 12) 开播：command.stream_start 清空本场作用域内存状态 =====
d13 = Dialogue(db_path=':memory:')
d13.handle(P('danmaku.gift', {'user': 'A', 'gift_name': 'x', 'count': 1, 'value_coins': 100}, 100))
d13.wait_idle()
d13.drain_outbox()
d13.handle(P('danmaku.chat', {'user': 'B', 'text': 'hi'}, 200))
_mark_tier(d13, 'C', 'member')
reply_of(d13, P('danmaku.chat', {'user': 'C', 'text': 'hi again'}, 300))   # 触发一次回复，顺带占用节流计数
check('开播前已经积累了本场状态(排名/缓冲/节流)',
      bool(d13._gift_rank) and bool(d13._chat_buffer) and bool(d13._last_reply_ts),
      str((d13._gift_rank, d13._chat_buffer, d13._last_reply_ts)))
d13.handle(C('stream_start', {}, 400))
check('stream_start 清空送礼排名', d13._gift_rank == {})
check('stream_start 清空弹幕缓冲', d13._chat_buffer == [])
check('stream_start 清空节流计数器', d13._last_reply_ts == {} and d13._reply_times == [])

# ===== 13) 下播：command.stream_end 先收尾沉淀（常客判定写结构化 note）再清零本场计数 =====
d14 = Dialogue(db_path=':memory:')
# 模拟"这是第 3 次来"的老观众：数据库里预先有 sessions_seen=2，这场再出现一次应该跨过常客门槛
with d14._db_lock:
    memory.sediment_session(d14._db, '二次观众', 0, sessions_seen=2, gift_total_lifetime=0, note=None)
d14.handle(P('danmaku.chat', {'user': '二次观众', 'text': '又来啦'}, 100))

d14.handle(P('danmaku.gift', {'user': '土豪', 'gift_name': '嘉年华', 'count': 1,
                              'value_coins': D.REGULAR_GIFT_LIFETIME_THRESHOLD + 1000}, 200))
d14.wait_idle()
d14.drain_outbox()

_mark_tier(d14, '会员甲', 'member')
d14.handle(P('danmaku.chat', {'user': '会员甲', 'text': '在'}, 300))

d14.handle(P('danmaku.chat', {'user': '路人乙', 'text': '路过'}, 400))   # 只出现一次、没送礼、非会员——不该判常客

d14.wait_idle()          # 等前面几条弹幕触发的回复任务(会员甲等)都落地，避免跟下面的 stream_end 写入交错
d14.drain_outbox()
d14.handle(C('stream_end', {}, 500))

v_regular = d14._get_viewer('二次观众')
check('sessions_seen 累计到第3次触发常客判定(写结构化note)',
      v_regular['sessions_seen'] == 3 and v_regular['note'] and '3' in v_regular['note'], str(v_regular))

v_gift = d14._get_viewer('土豪')
check('单场送礼超过跨场阈值也判定常客', v_gift['note'] is not None, str(v_gift))
check('stream_end 后单场计数器归零、跨场计数器保留',
      v_gift['gift_total_session'] == 0 and v_gift['gift_total_lifetime'] == D.REGULAR_GIFT_LIFETIME_THRESHOLD + 1000,
      str(v_gift))

v_member = d14._get_viewer('会员甲')
check('本来就是 member 直接判定常客(不需要凑够场次/金额)', v_member['note'] is not None, str(v_member))

v_normal = d14._get_viewer('路人乙')
check('首次/未送礼/非会员的路人不写常客note', v_normal['sessions_seen'] == 1 and v_normal['note'] is None, str(v_normal))

check('stream_end 后清空本场送礼排名和弹幕缓冲', d14._gift_rank == {} and d14._chat_buffer == [])

# ===== 14) 运行时不能卡住：handle() 绝不能因为等 DeepSeek 而阻塞（见 SPEC.md 同名一节）=====
_orig_reply = chat.reply


def _slow_reply(*a, **kw):
    time.sleep(1.5)
    return _orig_reply(*a, **kw)


d10 = Dialogue(db_path=':memory:')
_mark_tier(d10, '慢观众', 'member')
chat.reply = _slow_reply
try:
    t0 = time.time()
    out10 = d10.handle(P('danmaku.chat', {'user': '慢观众', 'text': '在吗'}, 1000))
    elapsed = time.time() - t0
    check('即使生成很慢，handle() 也几乎立刻返回(不同步等 DeepSeek)', elapsed < 1.0, f'{elapsed:.3f}s')
    check('handle() 同步返回为空：回复挪到后台队列生成', out10 == [], str(out10))
    idle = d10.wait_idle(timeout=5.0)
    check('worker 线程最终把慢任务处理完', idle)
    slow_msgs = d10.drain_outbox()
    check('慢调用最终还是在 outbox 里生成了 speak(没有丢)', any(m['type'] == 'speak' for m in slow_msgs), str(slow_msgs))
finally:
    chat.reply = _orig_reply

# 批量模式同理：一次性入队多个慢任务，其间总线接收线程(这里直接调 handle() 模拟)不受影响，
# 最高优先级的"闭嘴"指令能立刻生效，不用排在批量任务后面等。
d11 = Dialogue(db_path=':memory:')
d11.handle(P('danmaku.chat', {'user': '甲', 'text': 'x'}, 100))
d11.handle(P('danmaku.chat', {'user': '乙', 'text': 'y'}, 200))
chat.reply = _slow_reply
try:
    t0 = time.time()
    d11.handle(P('audio.command', {'intent': 'review_chat', 'raw_text': '看一下弹幕帮我回一下', 'speaker_verified': True}, 300))
    d11.handle(C('mute', {}, 301))   # 紧跟在批量指令后面的"闭嘴"，不应该被卡住
    elapsed = time.time() - t0
    check('批量模式入队后，紧随其后的闭嘴指令立刻生效(不排队等前面的慢任务)',
          d11.mode == 'QUIET' and elapsed < 1.0, f'{elapsed:.3f}s mode={d11.mode}')
    d11.wait_idle(timeout=10.0)     # 等两个慢任务都跑完再恢复 chat.reply，避免跨用例串扰
finally:
    chat.reply = _orig_reply

# ===== 15) 观众画像淘汰：跨场记忆不能无限膨胀（按"场次"算，不按日期算）=====
# 主播定的规则：超过 10 场没来就忘掉，最多只记 30 人。之所以是场次不是天数——主播不是
# 每天开播，休息两周回来，按天数判定会把所有人一次性误判成"很久没来"全部清空。
import shutil        # noqa: E402
import sqlite3       # noqa: E402
import tempfile      # noqa: E402

m1 = memory.connect(':memory:')
check('场次计数器初值为 0（还没下播过任何一场）', memory.get_session_no(m1) == 0)
check('bump_session_no 返回递增后的场次号', memory.bump_session_no(m1) == 1)
check('场次号能持久读回来', memory.get_session_no(m1) == 1)
check('连续下播两场，计数器到 2', memory.bump_session_no(m1) == 2 and memory.get_session_no(m1) == 2)

memory.upsert_viewer(m1, '当场观众', 1000)
check('upsert_viewer 不传 session_no 时自动记成当前场次',
      memory.get_viewer(m1, '当场观众')['last_seen_session'] == 2,
      str(memory.get_viewer(m1, '当场观众')))
memory.sediment_session(m1, '沉淀观众', 1000, sessions_seen=1, gift_total_lifetime=0)
check('sediment_session 同样记录当前场次',
      memory.get_viewer(m1, '沉淀观众')['last_seen_session'] == 2)

# ---- 10 场没来就删；边界（正好 10 场）留着 ----
m2 = memory.connect(':memory:')
memory.upsert_viewer(m2, '失联老观众', 1000, session_no=1)     # evict 后场次差 = 12-1 = 11 > 10
memory.upsert_viewer(m2, '边界观众', 1000, session_no=2)       # 场次差正好 10，不该删
memory.upsert_viewer(m2, '上场刚来过', 1000, session_no=11)    # 场次差 1
for _ in range(11):
    memory.bump_session_no(m2)
removed2 = memory.evict_viewers(m2)                            # 内部再 +1 -> 第 12 场
check('evict_viewers 自己把场次 +1（下播即换场）', memory.get_session_no(m2) == 12)
check('超过 10 场没来的观众被忘掉', memory.get_viewer(m2, '失联老观众') is None)
check('正好 10 场没来的还留着（阈值是"超过"，不是"达到"）',
      memory.get_viewer(m2, '边界观众') is not None)
check('上一场刚来过的当然留着', memory.get_viewer(m2, '上场刚来过') is not None)
check('返回删除条数供上层打日志', removed2 == 1, str(removed2))

# ---- 手动打标豁免：member/star_guardian 不参与淘汰 ----
m3 = memory.connect(':memory:')
memory.upsert_viewer(m3, '普通路人', 1000, session_no=0)
# ⚠️ 关键前提：tier_source 这列的 DEFAULT 就是 'manual'，upsert_viewer() 建档时根本不写它。
# 所以豁免判定绝不能写成"tier 是 VIP **或** tier_source='manual'"——那会把每个路过的路人
# 都当成"主播手动打过标"豁免掉，淘汰函数变成永远删不掉东西的空壳，且不会报任何错。
check('⚠️ 路人建档时 tier_source 默认就是 manual（所以豁免判定不能看这一列）',
      m3.execute("SELECT tier, tier_source FROM viewers WHERE nickname='普通路人'").fetchone()
      == ('normal', 'manual'))
memory.set_tier(m3, '手动会员', 'member')
memory.upsert_viewer(m3, '手动会员', 1000, session_no=0)
memory.set_tier(m3, '手动星守护', 'star_guardian')
memory.upsert_viewer(m3, '手动星守护', 1000, session_no=0)
memory.set_tier(m3, '自动会员', 'member', source='auto')
memory.upsert_viewer(m3, '自动会员', 1000, session_no=0)
memory.set_tier(m3, '被主播降级的', 'normal')                  # 主播亲手判定"这人不特殊"
memory.upsert_viewer(m3, '被主播降级的', 1000, session_no=0)
for _ in range(30):
    memory.bump_session_no(m3)
removed3 = memory.evict_viewers(m3)
check('长期不来的普通观众被淘汰', memory.get_viewer(m3, '普通路人') is None)
check('member 豁免淘汰（主播亲手认定的重要观众，删了就是直播事故）',
      memory.get_viewer(m3, '手动会员') is not None)
check('star_guardian 豁免淘汰', memory.get_viewer(m3, '手动星守护') is not None)
check('自动判定出来的 member 一并豁免（粉丝团大哥，误删代价更大）',
      memory.get_viewer(m3, '自动会员') is not None)
check('主播亲手降级回 normal 的不豁免（他明说了这人不特殊）',
      memory.get_viewer(m3, '被主播降级的') is None)
check('豁免不影响返回的删除条数', removed3 == 2, str(removed3))

# ---- 上限 30：超了删最久没见的 ----
# 全员场次差都不超过 10，把"长期不来"那条规则排除掉，单独验上限逻辑。
m4 = memory.connect(':memory:')
for _ in range(100):
    memory.bump_session_no(m4)
for i in range(5):
    memory.upsert_viewer(m4, f'冷门观众{i}', 1000 + i, session_no=91)    # evict 后场次差 10
for i in range(30):
    memory.upsert_viewer(m4, f'常来观众{i:02d}', 2000 + i, session_no=100)  # 场次差 1
removed4 = memory.evict_viewers(m4)
check('上限 30 生效：35 人裁到 30 人',
      m4.execute("SELECT COUNT(*) FROM viewers").fetchone()[0] == 30 and removed4 == 5,
      str(removed4))
check('裁掉的是最久没见的那 5 个',
      all(memory.get_viewer(m4, f'冷门观众{i}') is None for i in range(5)))
check('近期常来的 30 个一个没少',
      all(memory.get_viewer(m4, f'常来观众{i:02d}') is not None for i in range(30)))

# 上限裁剪同样豁免 VIP：31 个人里 30 个是星守护，只该动那 1 个普通观众
m5 = memory.connect(':memory:')
for i in range(30):
    memory.set_tier(m5, f'大哥{i:02d}', 'star_guardian')
    memory.upsert_viewer(m5, f'大哥{i:02d}', 1000 + i, session_no=0)
memory.upsert_viewer(m5, '挤进来的路人', 5000, session_no=0)
removed5 = memory.evict_viewers(m5)
check('上限裁剪也豁免 VIP：只动普通观众',
      removed5 == 1 and memory.get_viewer(m5, '挤进来的路人') is None
      and memory.get_viewer(m5, '大哥00') is not None, str(removed5))

# ---- 老库迁移：加了新列之后，第一次下播不能把已有观众全清空 ----
# 这条必须用临时文件库：':memory:' 每建一个连接就是一个全新空库，跨连接的迁移路径测不到。
_tmpdir = tempfile.mkdtemp(prefix='pet_mem_verify_')
try:
    for _label, _seeded_session in (('计数器还是 0', None), ('计数器已经跑到 50', 50)):
        _old = os.path.join(_tmpdir, f'old_{_seeded_session}.db')
        raw = sqlite3.connect(_old)
        # 老 schema：没有 last_seen_session，也没有 tier_source
        raw.execute("CREATE TABLE viewers (nickname TEXT PRIMARY KEY, tier TEXT DEFAULT 'normal', "
                    "gift_total_session INTEGER DEFAULT 0, last_seen_ts INTEGER, note TEXT, "
                    "sessions_seen INTEGER DEFAULT 0, gift_total_lifetime INTEGER DEFAULT 0)")
        raw.execute("INSERT INTO viewers(nickname, tier, last_seen_ts, sessions_seen) "
                    "VALUES ('老铁', 'normal', 123456, 8)")
        if _seeded_session is not None:
            raw.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            raw.execute("INSERT INTO meta(key, value) VALUES ('session_no', ?)", (str(_seeded_session),))
        raw.commit()
        raw.close()

        mo = memory.connect(_old)
        _v = memory.get_viewer(mo, '老铁')
        check(f'老库迁移（{_label}）：补出 last_seen_session 且不丢原有数据',
              _v is not None and _v['sessions_seen'] == 8 and _v['last_seen_ts'] == 123456, str(_v))
        check(f'老库迁移（{_label}）：已有行回填成当前场次，而不是留 0',
              _v['last_seen_session'] == (_seeded_session or 0), str(_v))
        memory.evict_viewers(mo)
        check(f'⚠️ 老库迁移（{_label}）后紧接着下播，老观众不会被误当成"10 场没来"清掉',
              memory.get_viewer(mo, '老铁') is not None)
        mo.close()
finally:
    shutil.rmtree(_tmpdir, ignore_errors=True)

# ---- 淘汰跟下播沉淀的先后顺序：沉淀写本场，evict 之后才换场 ----
m6 = memory.connect(':memory:')
memory.bump_session_no(m6)                                     # 正在播第 1 场
memory.sediment_session(m6, '本场来过', 700, sessions_seen=1, gift_total_lifetime=0)
memory.evict_viewers(m6)
_v6 = memory.get_viewer(m6, '本场来过')
check('本场露过面的人记的是本场场次号（沉淀在 +1 之前，场次差不偏移一格）',
      _v6['last_seen_session'] == 1 and memory.get_session_no(m6) == 2, str(_v6))

# ===== 16) 真实 DeepSeek 调用（配了 key 才跑；没配不算失败，只 SKIP）=====
_envfile = os.path.join(REPO, '.env')
if os.path.exists(_envfile):
    for line in open(_envfile, encoding='utf-8'):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
_has_key = os.environ.get('PET_CHAT_API_KEY') or os.environ.get('PET_CHAT_BASE_URL')
if _has_key:
    try:
        real_text = chat.reply(persona.SYSTEM_PROMPT, persona.build_streamer_prompt('你好呀，你叫什么名字？'), ts=1)
        check('真实 DeepSeek 调用返回非空文本', bool(real_text), real_text[:80])
    except Exception as e:
        print(f"[WARN] 真实调用失败（多半是 key 限制/欠费等外部问题）：{str(e)[:150]}")
        print("       修好 key 后重跑即真验证；离线逻辑与接线不受影响。")
else:
    print("[SKIP] 未配置 PET_CHAT_*（.env）—— 跳过真实调用。")
    print("       在 .env 配好 PET_CHAT_BASE_URL/PET_CHAT_API_KEY/PET_CHAT_MODEL 后")
    print("       `python services/dialogue/verify_dialogue.py` 即真验证 DeepSeek 对话。")

# ===== 语音配速（2026-07-30 用真实录制数据定的两层规则）=====
# 背景：VIP 必回且不限流时，实测一场 1小时46分 会出声 181 次 ≈ 每 36 秒一次。而 VIP 并不
# 刷屏（弹幕间隔中位数 66 秒），是 15 个人各自"每分钟一句"叠出来的。所以要两层：
# 连发降级治"一个人突然刷三条"，全局配速才是压住总量的主力。加完实测降到 34 次 ≈ 每 3 分钟。
dv = Dialogue(db_path=':memory:')
VIPFC = {'user': '铁粉', 'text': '主播好', 'fansclub_level': 10}

r_a = dv.handle(P('danmaku.chat', dict(VIPFC), 0))
check('VIP 第一条：出声', dv._last_voice_ts == 0, str(dv._last_voice_ts))

dv.handle(P('danmaku.chat', dict(VIPFC), 10_000))          # 10 秒后，算连发
check('VIP 连发（10 秒内）→ 降级成文字，出声时刻不变', dv._last_voice_ts == 0)

dv.handle(P('danmaku.chat', dict(VIPFC), 60_000))          # 距上条 50 秒，不算连发；但距上次出声才 1 分钟
check('不算连发但没到配速（1 分钟）→ 仍然只出文字', dv._last_voice_ts == 0)

dv.handle(P('danmaku.chat', dict(VIPFC), 200_000))         # 距上条 140 秒、距上次出声 200 秒
check('间隔够 + 配速也够 → 才出声', dv._last_voice_ts == 200_000, str(dv._last_voice_ts))

# ⚠️ 关键：被降级的那些**仍然会被回复**，只是不出声。VIP"必回"这条没有被配速破坏。
#
# ⚠️ 断言必须看 outbox 里真实产出的 action，**绝不能去 peek `_task_queue`**：后台 worker
# 一直在消费那个队列，手动 drain 会跟它抢，少掉几条随时序变化。这样写出来的测试会随机
# 红一次绿一次——比不写还糟，因为它会训练人"再跑一次就好了"。
# 超时给足：配了真实 key 时这里是 4 次真实的 DeepSeek 调用，默认 5 秒等不完，
# 只取到第一条就会误判成"后面几条没被回复"。
dv.wait_idle(timeout=90)
kinds = [m['type'] for m in dv.drain_outbox()]
check('⚠️ 四条 VIP 弹幕全部被回复（必回 ≠ 必出声）', len(kinds) == 4, str(kinds))
check('其中恰好两条出声：第 1 条 + 隔了 200 秒的第 4 条',
      kinds.count('speak') == 2 and kinds.count('show_bubble') == 2, str(kinds))
check('连发（10 秒内）那条降级成文字', kinds[1] == 'show_bubble', str(kinds))
check('不算连发但没到配速那条也降级', kinds[2] == 'show_bubble', str(kinds))

# 送礼**两层都绕过**：送礼的人往往前脚刚发过弹幕（实测就是这样，送礼前 5 秒还在聊天），
# 拿弹幕节奏去卡礼物属于误伤；礼物自己有同人 20 秒冷却挡连击。
dv2 = Dialogue(db_path=':memory:')
dv2.handle(P('danmaku.chat', {'user': '土豪', 'text': '来了', 'fansclub_level': 10}, 0))
dv2.handle(P('danmaku.gift', {'user': '土豪', 'gift_name': '名刀司命', 'count': 1,
                              'value_coins': 99, 'fansclub_level': 10}, 5_000))
dv2.wait_idle(timeout=60)
g_kinds = [m['type'] for m in dv2.drain_outbox()]
check('大额礼物绕过配速与连发，仍然出声', g_kinds.count('speak') == 2, str(g_kinds))

dv3 = Dialogue(db_path=':memory:')
dv3.handle(P('danmaku.gift', {'user': '铁粉', 'gift_name': '粉丝团灯牌', 'count': 1,
                              'value_coins': 1, 'fansclub_level': 10}, 0))
dv3.wait_idle(timeout=60)
s_kinds = [m['type'] for m in dv3.drain_outbox()]
check('小额礼物（1 抖币）不出声——一场 17 次送礼里大半是这种',
      'speak' not in s_kinds, str(s_kinds))

print(f"\n==== dialogue 服务验证: {passed}/{passed + failed} 通过（真实调用视 key 而定）====")
sys.exit(0 if failed == 0 else 1)
