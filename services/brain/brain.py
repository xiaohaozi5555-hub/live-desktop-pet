#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""brain — 决策/编排引擎（纯标准库，可离线验证）。

订阅 perception.* 与 command.*，按规则 + 状态机产出 action.*：
- 规则快路径：进场欢迎(限流) / 礼物按 value 分级答谢 / 被吓反应 / 卡关提示 / 倒计时提醒。
- 状态机：ACTIVE / QUIET(闭嘴,仅idle) / SLEEP；冷却防刷屏。
- 只采信声纹通过(speaker_verified)的语音指令；控制指令最高优先级。
- 卡关攻略走 LLM（见 perception-game/vision.py）；看表情调侃/夸夸当前是规则版，想让文案更
  自然可以换成 LLM 生成（见 services/dialogue/SPEC.md，那边是平级订阅、叠加反应，不改这里）。
- "求礼物"能力已移除（诱导送礼合规红线），2026-07-14。

**出声 vs 只显示文字**（2026-07-30 首场真实直播后定的分层，08-01 第二场真开播发现"进场
欢迎"也踩了同一个坑——观众源源不断进场时几乎连续出声，一并改，改动前请先看这段）：
桌宠一直出声会盖过主播、影响直播观感，所以不是每件事都值得开口。
`speak` = 出声(带气泡)，`show_bubble` = 只有文字气泡不出声，两者都是既有契约事件。
判断标准是"这件事值不值得打断主播的声音"：高价礼物、关注、定时的关注提醒值得；
小额礼物、被吓/倒计时/进场欢迎这类高频小事只走气泡。新增反应时按这个标准归类，别一律用 speak。
"""
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'packages', 'contract'))
import validate as contract  # noqa: E402

QUIET_MODES = ('QUIET', 'SLEEP')

# 礼物答谢的"开口门槛"，单位抖币（value_coins 与抖币 1:1，已核实）。
# 低于这个数只做动作 + 文字气泡，不出声：小额礼物太频繁——首场实测 17 次送礼里大半是
# 1 抖币的粉丝团灯牌，每条都念一遍会一直有人声压着主播。门槛以上维持原来的语音分级。
GIFT_SPEAK_MIN_COINS = 10
# 不出声的小额答谢气泡挂多久。比被吓/倒计时那种一闪而过的提示长，因为它替代的是一句语音，
# 得让观众有时间看到"宝宝确实收到了你的礼物"。
GIFT_BUBBLE_MS = 8000

# 进场欢迎（2026-08-01 第二场真开播暴露）：原先每人进场就欢迎一次、冷却只有 3 秒，观众
# 源源不断进场时桌宠几乎连续出声，主播反馈"不停在说欢迎词，影响直播"。改两处：①出声降级成
# 纯文字气泡；②不再来一个欢迎一个，改成每 WELCOME_CYCLE_MS 只开放 WELCOME_WINDOW_MS 的
# "欢迎窗口"，窗口外进场的人安静不欢迎（仍会被记住昵称，只是不弹欢迎）。窗口内部沿用原有的
# 3 秒合批逻辑，避免开窗瞬间涌进一堆人时气泡刷得比能读还快。
WELCOME_WINDOW_MS = 60_000        # 欢迎窗口一次开放多久
WELCOME_CYCLE_MS = 10 * 60_000    # 欢迎窗口出现的周期
WELCOME_BUBBLE_MS = 8000          # 欢迎气泡停留时长

# 卡关攻略的结果气泡停多久。比别的气泡长得多是有原因的：攻略是"要照着做"的信息，
# 不是听个响的闲聊，主播多半正忙着操作、没法立刻抬头看，得给他回头看一眼的时间。
WALKTHROUGH_BUBBLE_MS = 30000

# 关注答谢话术：答谢 + 顺势要灯牌（灯牌只是身份标识，不是打赏，不踩"求礼物"红线）。
# 写成多条随机取用，是因为关注是高频事件，固定一句话连着听几遍就像机器人复读。
FOLLOW_THANKS = (
    "谢谢 {user} 的关注，可以的话我还想要个灯牌哦~",
    "{user} 关注啦！要是再来个灯牌，本公主就更喜欢你了哦~",
    "谢谢 {user}！关注都点了，灯牌是不是也顺手安排一下嘛~",
    "收到 {user} 的关注啦，么么哒~ 有灯牌的话记得亮出来给宝宝看看哦！",
    "谢谢 {user} 的关注！灯牌什么的……宝宝就随口一提，你懂的哦~",
    "{user} 来啦，关注已收下~ 灯牌也点亮一个嘛，宝宝就认得你啦！",
)

# 定时主动互动：直播间大部分观众是静默看客，不主动开口就没人想起点关注。
# 用 speak 出声——纯气泡在恐怖直播画面里根本没人注意，这条提醒就白发了。
PROACTIVE_INTERVAL_MS = 10 * 60 * 1000
# 文案性格：腹黑 + 可爱 + 调皮 + 古灵精怪，不要客服腔。
# 合规：只要关注/要灯牌，不要"求打赏/求礼物"；避开"游戏""玩家"等平台禁用词
# （完整清单见 services/dialogue/guardrails.py 的 BANNED_WORDS）——所以第 4 条是
# "准点开播"而不是主播原话里的"准点直播游戏"。改文案时请照着这两条自查。
PROACTIVE_LINES = (
    "没点关注的宝子们，看到本公主还不点点关注？",
    "我家主人直播这么辛苦，为什么不给我们点关注！不要逼我生气哦！",
    "求求大家，把关注点一点吧，不然小蝴蝶又要骂我了！",
    "主播每天00:30准点开播哟，有灯牌就能进群一起玩啦！",
    "咦？怎么还在播啊，好累哦。",
    "偷偷告诉你们，关注了的人本公主都记着呢，没关注的……哼，我也记着哦。",
    "刚进来的宝子别装看不见，右上角那个关注按钮，它在等你哦。",
    "宝宝数了数，屏幕前还有一半的人没关注呢……我看着你们哦。",
    "点了关注就是自己人啦，下次开播我第一个喊你名字！",
    "小蝴蝶又被吓到啦，快来点个关注给她压压惊嘛~",
)

# 评论关键词 -> 动作反应（对应`提案文档草稿.md`「指令设计」里的评论指令词草案）。
# 提案/demo 阶段先用固定表在本地匹配；真实上线后关键词命中由抖音开放平台判定、
# 结构化事件推给我们，这份表和下面的匹配/反应逻辑原样保留，只是换个数据来源。
# (关键词元组, 动作, 语音情绪, 话术模板(None=只做动作不说话)，{user}会被替换成昵称)
COMMENT_KEYWORDS = (
    (("你好", "主播好", "主播"), 'wave', 'cheerful', "{user} 好呀，欢迎来聊天~"),
    (("加油",), 'wave', 'cheerful', "谢谢 {user} 的加油！"),
    (("哈哈", "233"), 'laugh', 'happy', None),
    (("好棒", "厉害"), 'praise', 'happy', None),
    (("谢谢",), 'thank_small', 'happy', None),
)


def _act(type_, data, ts):
    return {'channel': 'action', 'type': type_, 'ts': ts, 'source': 'brain', 'data': data}


class Brain:
    def __init__(self):
        self.mode = 'ACTIVE'
        self._last = {}         # 冷却时间戳
        self.recent = []        # 最近观众昵称（欢迎用）
        self._prank = None      # 上次整蛊计数（用于检测被整蛊）
        self._welcome_q = []    # 冷却内待合批欢迎的进场昵称
        self._welcome_cycle_start = None   # 欢迎窗口的起点（懒初始化：第一个进场事件那一刻开窗）
        self._proactive_q = []      # 本轮还没播过的主动互动话术（洗好牌的队列）
        self._proactive_last = None  # 上一条主动互动，用于跨轮衔接时不重复

    def _remember(self, user):
        if user and user not in self.recent:
            self.recent.append(user)
            self.recent = self.recent[-8:]

    def tick(self, ts):
        """周期心跳（brain 运行器周期调用）：合批欢迎 + 定时主动互动。闭嘴/休息时不出声。"""
        if self.mode in QUIET_MODES:
            return []                            # 闭嘴/休息期间连主动互动也一起停
        out = []
        if self._welcome_q and self._cooldown('welcome_flush', ts, 3000):    # 合批欢迎
            names = self._welcome_q[:3]
            extra = len(self._welcome_q) - len(names)
            self._welcome_q = []
            txt = "还有 " + "、".join(names) + (f" 等 {extra} 位" if extra else "") + " 也来啦，欢迎欢迎~"
            out += [_act('play_motion', {'motion': 'wave'}, ts), _act('show_bubble', {'text': txt, 'duration_ms': WELCOME_BUBBLE_MS}, ts)]
        # 定时主动互动。第一次 tick 只对时不说话：那一刻刚开播、观众还没进来，
        # 一上来喊"还不点关注"是喊给空房间听，白白浪费一轮。
        if self._last.get('proactive') is None:
            self._last['proactive'] = ts
        elif self._cooldown('proactive', ts, PROACTIVE_INTERVAL_MS):
            out.append(_act('speak', {'text': self._next_proactive(), 'emotion': 'cheerful'}, ts))
        return [m for m in out if not contract.validate_message(m)]

    def _next_proactive(self):
        """轮播取一条主动互动话术：一轮内不重复，播完重新洗牌。
        纯 random.choice 会连着抽中同一条，观众二十分钟内听到两遍一样的话就出戏了。"""
        if not self._proactive_q:
            q = list(PROACTIVE_LINES)
            random.shuffle(q)
            if len(q) > 1 and q[0] == self._proactive_last:   # 上一轮末尾和这一轮开头也别撞
                q.append(q.pop(0))
            self._proactive_q = q
        self._proactive_last = self._proactive_q.pop(0)
        return self._proactive_last

    def _cooldown(self, key, ts, ms):
        last = self._last.get(key)
        if last is not None and ts - last < ms:
            return False
        self._last[key] = ts
        return True

    def _welcome_window_open(self, ts):
        """欢迎窗口：每 WELCOME_CYCLE_MS 开放 WELCOME_WINDOW_MS，其余时间不欢迎新进场的人。
        第一个进场事件就开窗——不然开播刚开始最该被欢迎的头几位反而赶上关着的那 9 分钟。"""
        if self._welcome_cycle_start is None:
            self._welcome_cycle_start = ts
        phase = (ts - self._welcome_cycle_start) % WELCOME_CYCLE_MS
        return phase < WELCOME_WINDOW_MS

    def handle(self, ev):
        """处理一条事件，返回一组已通过契约校验的 action 消息。"""
        msgs = self._route(ev)
        return [m for m in msgs if not contract.validate_message(m)]

    # --- 路由 ---
    def _route(self, ev):
        ch, t, d, ts = ev.get('channel'), ev.get('type'), ev.get('data', {}), ev.get('ts', 0)
        if ch == 'command':
            return self._command(t, d, ts)
        if ch == 'perception' and t == 'audio.command':
            if not d.get('speaker_verified'):
                return []                       # 非主播声音：丢弃（"只识别我的声音"）
            return self._command(d.get('intent'), d, ts)
        if ch != 'perception':
            return []
        if self.mode in QUIET_MODES:
            return []                           # 闭嘴/休息：安静看着不动
        return self._perceive(t, d, ts)

    # --- 控制指令（面板 / 弹幕关键词 / 语音意图，统一入口）---
    def _command(self, intent, d, ts):
        if intent == 'mute':
            self.mode = 'QUIET'; return [_act('stop', {}, ts)]
        if intent == 'sleep':
            # 先停止语音，再播放持久睡眠动作：顺序反过来的话，渲染器的 stop 会把刚进入的
            # 睡眠动画立即重置回 idle（Codex 交付时发现并修复的顺序问题）。
            self.mode = 'SLEEP'; return [_act('stop', {}, ts), _act('play_motion', {'motion': 'sleep'}, ts)]
        if intent in ('unmute', 'wake'):
            self.mode = 'ACTIVE'; return [_act('play_motion', {'motion': 'idle'}, ts)]
        if intent == 'do' and d.get('action'):
            return [_act('play_motion', {'motion': d['action']}, ts)]
        return []

    # --- 感知事件 -> 反应 ---
    def _perceive(self, t, d, ts):
        if t.startswith('danmaku.') and d.get('user'):
            self._remember(d['user'])
        if t == 'danmaku.enter':
            if not self._welcome_window_open(ts):
                return []                       # 欢迎窗口关闭中：安静记下昵称（上面已 _remember），不欢迎
            if self._cooldown('welcome', ts, 3000):
                return [_act('play_motion', {'motion': 'wave'}, ts),
                        _act('show_bubble', {'text': f"欢迎 {d.get('user', '观众')} 进入直播间~", 'duration_ms': WELCOME_BUBBLE_MS}, ts)]
            self._welcome_q.append(d.get('user', '观众'))   # 冷却内的进场 → 合批，等 tick 一起欢迎
            self._welcome_q = self._welcome_q[-10:]
            return []
        elif t == 'danmaku.gift':
            return self._gift(d, ts)
        elif t == 'danmaku.chat':
            return self._comment(d, ts)
        elif t == 'danmaku.follow':
            # beg 动作(wink+爱心+"爱你哟！")已不带求礼物话术，复用作关注答谢的撒娇反应。
            # 关注要出声：新粉刚点关注是最愿意再往前一步（进粉丝团/亮灯牌）的时刻，
            # 只弹个气泡他多半划走了就看不到。
            line = random.choice(FOLLOW_THANKS).format(user=d.get('user') or '观众')
            return [_act('play_motion', {'motion': 'beg'}, ts),
                    _act('speak', {'text': line, 'emotion': 'happy'}, ts)]
        elif t == 'danmaku.like':
            if self._cooldown('like', ts, 8000):
                return [_act('play_motion', {'motion': 'praise'}, ts)]
        elif t == 'game.scare':
            if d.get('intensity', 0) >= 0.5 and self._cooldown('scare', ts, 1200):
                return [_act('play_motion', {'motion': 'scared'}, ts),
                        _act('show_bubble', {'text': '呀啊——吓死宝宝了！', 'duration_ms': 2500}, ts)]
        elif t == 'game.scene':
            if d.get('stuck') and d.get('hint'):          # hint 来自 vision.py 的 LLM 分析
                # 出声 + 再留一条长气泡。`speak` 的气泡只在音频播放期间显示，播完就消失，
                # 而攻略又长又要记（2026-08-02 主播反馈"攻略的反馈我并不知道怎么给我…
                # 我也没有找到哪里可以查看"）。语音是主播要的形式，保留；后面补一条能停
                # 半分钟的文字气泡，让他听完还能回头看一眼。
                line = f"卡住啦？试试：{d['hint']}"
                return [_act('speak', {'text': line, 'emotion': 'gentle'}, ts),
                        _act('show_bubble', {'text': line, 'duration_ms': WALKTHROUGH_BUBBLE_MS}, ts)]
        elif t == 'plugin.state':
            out = []
            cd = d.get('countdown_sec')
            if cd in (300, 60):
                out.append(_act('show_bubble', {'text': f"还有 {cd // 60} 分钟下播啦！", 'duration_ms': 3000}, ts))
            pc = d.get('prank_count')
            if isinstance(pc, int) and not isinstance(pc, bool):
                if self._prank is not None and pc > self._prank:   # 被整蛊次数增加 → 反应
                    out += [_act('play_motion', {'motion': 'scared'}, ts),
                            _act('speak', {'text': f"呜哇又被整蛊啦！这已经是第 {pc} 次了！", 'emotion': 'funny'}, ts)]
                self._prank = pc
            return out
        elif t == 'face.expression':                       # 规则版调侃/夸夸；可换 LLM 生成更自然的文案
            lab, conf = d.get('label'), d.get('confidence', 0)
            if conf < 0.6 or not self._cooldown('face', ts, 6000):
                return []
            if lab == 'happy':                             # 夸夸
                return [_act('set_expression', {'expression': 'happy'}, ts),
                        _act('speak', {'text': "主播今天笑得好灿烂，状态超好的嘛~", 'emotion': 'cheerful'}, ts)]
            if lab == 'fear':                              # 调侃：坏笑表情 / 真大笑动作随机二选一
                reaction = random.choice([
                    _act('set_expression', {'expression': 'smug'}, ts),
                    _act('play_motion', {'motion': 'laugh'}, ts),
                ])
                return [reaction,
                        _act('speak', {'text': "哈哈哈没想到这也能吓到小蝴蝶！", 'emotion': 'teasing'}, ts)]
            if lab in ('sad', 'angry'):                    # 安慰
                return [_act('speak', {'text': "别怕别怕，有宝宝陪着你呢！", 'emotion': 'gentle'}, ts)]
            if lab == 'surprise':
                return [_act('set_expression', {'expression': 'surprised'}, ts)]
        return []

    def _gift(self, d, ts):
        user, gift, v = d.get('user', '观众'), d.get('gift_name', '礼物'), d.get('value_coins', 0)
        if v >= 10000:
            return [_act('play_motion', {'motion': 'thank_big'}, ts),
                    _act('speak', {'text': f"哇！谢谢 {user} 的 {gift}！大哥太壕啦！！", 'emotion': 'excited'}, ts)]
        if v >= 1000:
            return [_act('play_motion', {'motion': 'thank_big'}, ts),
                    _act('speak', {'text': f"谢谢 {user} 的 {gift}~ 么么哒！", 'emotion': 'happy'}, ts)]
        if v >= GIFT_SPEAK_MIN_COINS:
            return [_act('play_motion', {'motion': 'thank_small'}, ts),
                    _act('speak', {'text': f"谢谢 {user} 送的 {gift}~", 'emotion': 'happy'}, ts)]
        # 门槛以下：动作照做、文字照显，但不出声（原因见文件顶部 GIFT_SPEAK_MIN_COINS）。
        # 不是"不理"这类礼物——观众仍能从动作和气泡看到被答谢了，只是不占用声音。
        return [_act('play_motion', {'motion': 'thank_small'}, ts),
                _act('show_bubble', {'text': f"谢谢 {user} 送的 {gift}~", 'duration_ms': GIFT_BUBBLE_MS}, ts)]

    def _comment(self, d, ts):
        text, user = d.get('text', ''), d.get('user', '观众')
        for kws, motion, emotion, template in COMMENT_KEYWORDS:
            if any(k in text for k in kws):
                if not self._cooldown('comment_' + motion, ts, 5000):
                    return []
                out = [_act('play_motion', {'motion': motion}, ts)]
                if template:
                    out.append(_act('speak', {'text': template.format(user=user), 'emotion': emotion}, ts))
                return out
        return []
