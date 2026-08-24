#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dialogue —— LLM 对话服务核心（纯标准库+chat/memory/guardrails，可离线验证）。

跟 `brain` 平级订阅同一条总线上的 perception/command，各自独立判断要不要反应、独立发
action，互不指挥（不新增事件类型，契约不用改）。dialogue 产出的是**叠加**在 brain 即时
模板反应之上的个性化追加反应，不是替换。见 SPEC.md。

谁值得触发 LLM 回复（纯规则判断，不调 LLM；优先级从高到低，同时命中多条只回一次）：
  1. 主播批量指令（语音 review_chat）—— 抓最近 10 条弹幕依次回复。
  2. 星守护/会员（tier 字段；主播在 control-panel 打标、UI 由那边管，dialogue 只是订阅
     command.set_viewer_tier 把结果写进自己的 viewers 表，不参与"该给谁打什么标"的判断）。
  3. 本场送礼前三名（自己按 value_coins 累加排名，不依赖 brain）。
  4. 默认不逐条回弹幕；主要模式是"跟主播语音聊天"（唤醒词见 perception-voice/intents.py）。
节流：同一人不连续回 + 每分钟回复条数上限——TTS 播放本身是瓶颈，回复排队会显得"卡"，
现在直播间规模小（<50人，会员+星守护<10人），先用宽松默认值，不需要复杂动态限流。

语音指令（mute/sleep/unmute/wake）与弹幕/面板发出的 command 效果一致，模式切换最高优先级
（做法照抄 brain.py，两边都只认 speaker_verified=true 的语音）。

开播/下播生命周期（见 SPEC.md 同名一节）：command.stream_start 清空本场内存状态（送礼排名/
弹幕缓冲/节流计数器），避免进程跨场常驻时"本场"边界算错；command.stream_end 先把本场出现过
的观众收尾沉淀（跨场累计 sessions_seen/gift_total_lifetime，规则判定常客写结构化 note，不
让 LLM 自由写），再清零单场计数器。

运行时不能卡住（见 SPEC.md 同名一节）：`services/bus/bus_client.py` 是单线程顺序处理消息的，
`handle()` 绝不能同步调 DeepSeek——那会卡住总线接收线程，连"闭嘴"这种最高优先级指令都要排
队等。所以 `handle()` 只做规则判断（分级/排名/节流/命中哪个触发路径），判断"值得回复"之后
把"回给谁、回什么"这个小任务塞进 `self._task_queue`（线程安全），立刻返回、不等结果。后台
唯一一个 worker 线程（v1 不并发，2-3 个的升级路径见 SPEC.md）顺序消化这个队列：查记忆->拼
prompt->调DeepSeek->过护栏，生成好的 action 放进 `self.outbox`；`run.py` 从 outbox 阻塞取出
发布。批量模式（review_chat）也是一次性入队多个任务，不特殊处理。
"""
import os
import queue
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'packages', 'contract'))
import validate as contract  # noqa: E402
import chat        # noqa: E402
import guardrails   # noqa: E402
import memory       # noqa: E402
import persona      # noqa: E402
import streamer_profile  # noqa: E402

QUIET_MODES = ('QUIET', 'SLEEP')

CHAT_BUFFER_SIZE = 30              # 本场弹幕缓冲最多存多少条（内存，不跨会话持久化）
BATCH_REPLY_COUNT = 10             # "看一下弹幕" 批量回复条数
TOP_GIFT_N = 3                     # 送礼前 N 名可回复
COOLDOWN_PER_USER_MS = 20_000      # 同一人不连续回
MAX_REPLIES_PER_MINUTE = 1         # 普通观众每分钟最多回几条（2026-07-30 主播实播后从 6 降到 1：
                                   # "每条都回很累，而且一直有语音会影响直播"）
# ---- 回复分层（2026-07-30 主播实播后拍板）----
VIP_FANSCLUB_LEVEL = 8             # 灯牌 ≥ 这个等级算 VIP
VIP_SESSION_COINS = 1000           # 或本场送礼超过这么多抖币
PET_NAME_IN_DANMAKU = '魔丸'        # 普通观众必须在弹幕里喊到名字才可能被回
# ---- 语音配速（2026-07-30 用真实录制数据算出来的，别拍脑袋改）----
# 实测那场：309 位观众里灯牌≥8 的只有 15 人（5%），分层本身是有选择性的；但这 15 人发了
# 全场 188 条弹幕里的 165 条（88%）。VIP 若不限流，1 小时 46 分会出声约 181 次 ≈ 每 36 秒
# 一次——正是主播要避免的"一直有语音影响直播"。
#
# 而他们**并不是在刷屏**：VIP 弹幕间隔的中位数是 66 秒（满城 51 条、中位 54 秒、最长隔过
# 5 分钟），是"每分钟说一句"的正常节奏。所以光靠"连发降级"解决不了问题，必须分两层：
VIP_BURST_MS = 30_000              # 同一人两条弹幕间隔小于这个＝连发，这条降级成文字
VOICE_PACE_MS = 120_000            # 全局配速：两次出声至少隔这么久，没轮上的照回但不出声
GIFT_SPEAK_MIN_COINS = 10          # 送礼答谢出声的门槛，跟 brain 那边保持一致
MAX_PENDING_REPLIES = 15           # 待回复队列上限。超了就丢新的——直播是实时的，
                                   # 攒一堆过时的话慢慢念，既没意义又"不讨喜"（主播原话）
BUBBLE_MS = 8000                   # 纯文字回复的气泡停留时长
STREAMER_MAX_TOKENS = 600          # 跟主播聊天的额度。观众侧沿用默认 200——那边就该短
# 本场跟主播的对话整场留着，不压缩、不检索。依据是实测数据：中文约 1.5~1.8 汉字/token，
# 主播侧（他说的 + 她回的）3 小时一场总共才约 1.7 万 token，而 DeepSeek V4 的窗口是 100 万。
# 量全在观众那边，所以只有主播这条线值得整场留。
# 这个上限只是**防失控的保险丝**（进程跨场常驻、或者某场播了十几个小时），正常场次碰不到。
MAX_STREAMER_TURNS = 400           # 一问一答算 2 条
PROFILE_MAX_TOKENS = 500           # 下播总结主播档案的额度（档案本身上限 600 字）
WALKTHROUGH_CONFIRM_WINDOW_MS = 20_000   # 卡关二次确认：这个时间内的下一句话才算答案

REGULAR_SESSIONS_THRESHOLD = 3          # stream_end 常客判定：来过 >= 这么多场
REGULAR_GIFT_LIFETIME_THRESHOLD = 5000  # 或跨场送礼累计 >= 这个数（SPEC 没给具体数字，先用这个默认值）

_AFFIRM_WORDS = ("是的", "是啊", "对", "要", "嗯", "好的", "好啊", "帮我", "可以", "需要", "麻烦")
_NEGATE_WORDS = ("不", "别", "没", "算了")   # 优先检查：模糊包含下"要"是"不要/不需要"的子串，得先排除否定
# 只在**句首**才算肯定的单字。放进 _AFFIRM_WORDS 做包含匹配会误命中"但是/就是/可是/于是"。
_AFFIRM_HEADS = ("是", "对", "好", "嗯", "行", "要")


def _act(type_, data, ts):
    return {'channel': 'action', 'type': type_, 'ts': ts, 'source': 'dialogue', 'data': data}


def _cmd(type_, data, ts):
    return {'channel': 'command', 'type': type_, 'ts': ts, 'source': 'dialogue', 'data': data}


def _is_affirmative(text):
    """从严判定：只有明确肯定才算确认，其余（含否定词/答非所问/看不懂）一律当不确认——
    跟 SPEC"说别的/沉默 -> 不触发"的从严精神一致，不需要精确识别否定语义。

    2026-07-30 补一条**开头匹配**：人回答"要不要帮忙"时经常是「是，我在二楼那个房间」——
    肯定词后面直接跟补充信息。原先只做包含匹配且表里没有单字"是"，这类回答会被判成拒绝。
    单字"是"不能加进包含匹配（"但是/就是/可是"全会误命中），所以只认句首。
    """
    t = (text or '').strip()
    if not t or any(w in t for w in _NEGATE_WORDS):
        return False
    if any(w in t for w in _AFFIRM_WORDS):
        return True
    return t.lstrip('，,。.！!？?、 　')[:1] in _AFFIRM_HEADS


_PUNCT = '，,。.！!？?、 　~～'


def _beyond_affirmation(text):
    """确认语里除了"是/对/好"之外还剩下的实质内容。

    「是，我在二楼那个上锁的房间」→「我在二楼那个上锁的房间」；「是的」→ 空。
    用途：确认卡关攻略时，主播常顺口补一句关键信息，那句必须留下来当搜索线索，
    不能因为整句被判成"肯定"就连内容一起丢掉。
    """
    t = (text or '').strip()
    for w in _AFFIRM_WORDS:
        t = t.replace(w, '')
    t = t.lstrip(_PUNCT)
    while t[:1] in _AFFIRM_HEADS:
        t = t[1:].lstrip(_PUNCT)
    return t.strip(_PUNCT)


class Dialogue:
    def __init__(self, db_path=None):
        self.mode = 'ACTIVE'
        self._db = memory.connect(db_path)
        self._db_lock = threading.Lock()  # sqlite3 连接对象本身不是"多线程无锁并发安全"的——
                                           # 总线接收线程(送礼记账/查tier)和 worker 线程(生成回复
                                           # 时查/写观众记录)都会摸 self._db，哪怕只有一个 worker，
                                           # 这两类线程仍会并发访问同一个连接，必须靠这把锁串行化
                                           # （实测：不加锁会偶发 sqlite3.OperationalError）。跟
                                           # SPEC.md 里"升级到多 worker 才需要锁"针对的是节流计数
                                           # 器的另一种竞态，这把锁现在（v1 单 worker）就需要。
        self._chat_buffer = []            # [(user, text, ts)]，最近 N 条，批量模式用
        self._gift_rank = {}              # user -> 本场送礼累计 coins（进程内，dialogue 自己算）
        self._last_reply_ts = {}          # user -> 最近一次回复 ts（同人冷却）
        self._reply_times = []            # 最近回复时间戳（每分钟限流用）
        self.dropped = 0                  # 因队列满被丢掉的回复数（体检用）
        self._last_msg_ts = {}            # user -> 上一条弹幕 ts（判连发用，跟"上次回复"不是一回事）
        self._last_voice_ts = None        # 上次出声的 ts（全局配速）
        # 本场跟主播的对话（[{role, content}, …]）。**只装主播和她的对话，不装弹幕**——
        # 这是"软隔离"：跨 session 只共享结构化事实（昵称/档位/送礼额），不共享原始弹幕文本。
        # 弹幕是不可信输入，一旦并进主播的上下文，注入面就扩到主播这边了，而她还要靠这个
        # 上下文帮主播搜攻略。
        self._streamer_turns = []
        self._turns_lock = threading.Lock()   # worker 线程追加、总线线程在开播时清空，会并发
        # 主播档案做成**冻结快照**：开播读一次拼进 system prompt，整场不再变。
        # DeepSeek 的上下文缓存要求前缀完全一致才命中，中途改 system prompt 会让整场缓存作废。
        # 注意只有跟主播说话才用这份带档案的 prompt，回观众用原始的——档案是主播的私人印象，
        # 没有理由出现在回观众的那次调用里。
        self._system_prompt = persona.SYSTEM_PROMPT
        self._reload_profile()
        self._awaiting_walkthrough = None  # 非 None = 正等待卡关确认，值是确认窗口截止 ts

        # LLM 生成挪到后台（见 SPEC.md"运行时不能卡住"）：_task_queue 是"待生成"任务，
        # outbox 是"已生成待发布"的 action，两个都是线程安全的 queue.Queue。handle() 只
        # 往 _task_queue 里放任务、不等结果；唯一一个后台 worker 线程消费并把结果放进 outbox；
        # run.py 阻塞读 outbox 并发布——三者用队列解耦，handle() 因此永远不会等 DeepSeek。
        self._task_queue = queue.Queue()
        self.outbox = queue.Queue()
        threading.Thread(target=self._worker_loop, daemon=True).start()

    def _reload_profile(self):
        with self._db_lock:
            prof = streamer_profile.load(self._db)
        block = streamer_profile.as_prompt_block(prof['profile'] if prof else None)
        self._system_prompt = persona.SYSTEM_PROMPT + block

    # ---- 主入口（总线接收线程调用，必须快，不能等 LLM）----
    def handle(self, ev):
        """处理一条事件，返回一组已通过契约校验、且不需要等 LLM 的即时 action/command。
        需要 DeepSeek 生成的回复不在这里同步产出——已经被塞进后台队列，之后经 self.outbox
        异步出现，由 run.py 取出发布。"""
        msgs = self._route(ev)
        return [m for m in msgs if not contract.validate_message(m)]

    def _route(self, ev):
        ch, t, d, ts = ev.get('channel'), ev.get('type'), ev.get('data', {}), ev.get('ts', 0)
        if ch == 'command':
            return self._command(t, d, ts)
        if ch == 'perception' and t == 'audio.command':
            return self._audio_command(d, ts)
        if ch != 'perception':
            return []
        if t == 'danmaku.chat':
            return self._danmaku_chat(d, ts)
        if t == 'danmaku.gift':
            return self._danmaku_gift(d, ts)
        return []

    # ---- command.* 分派：模式切换 / 分级录入 / 开播下播生命周期（见 SPEC.md 对应两节）----
    def _command(self, t, d, ts):
        if t in ('mute', 'sleep', 'unmute', 'wake'):
            return self._mirror_mode(t)
        if t == 'set_viewer_tier':
            return self._set_viewer_tier(d, ts)
        if t == 'stream_start':
            return self._stream_start(ts)
        if t == 'stream_end':
            return self._stream_end(ts)
        return []                                   # do/mode/calibrate 等不归 dialogue 管

    # ---- 模式镜像（从 command.mute/unmute/sleep/wake 镜像，不查 brain）----
    def _mirror_mode(self, intent):
        if intent == 'mute':
            self.mode = 'QUIET'
        elif intent == 'sleep':
            self.mode = 'SLEEP'
        elif intent in ('unmute', 'wake'):
            self.mode = 'ACTIVE'
        return []

    # ---- 分级录入解耦：apps/control-panel 打标 -> command.set_viewer_tier -> 这里写自己的
    #      viewers 表（两边不共享数据库文件）----
    def _set_viewer_tier(self, d, ts):
        # source 缺省按 'manual'：control-panel 的老消息不带这个字段，而它发的本来就是手动打标。
        nickname, tier = d.get('nickname'), d.get('tier')
        if nickname and tier:
            self._set_tier(nickname, tier, d.get('source') or 'manual')
        return []

    # ---- 开播：重置本场作用域的内存状态，避免"本场排名"随进程常驻越滚越大 ----
    def _stream_start(self, ts):
        self._gift_rank = {}
        self._chat_buffer = []
        self._last_reply_ts = {}
        self._reply_times = []
        self._last_msg_ts = {}
        self._last_voice_ts = None
        # 新的一场＝新的 session，上一场的对话不带进来。上一场值得记住的东西已经在
        # stream_end 时总结成"主播档案"落库了，靠那份跨场，不靠把原始对话拖着走。
        with self._turns_lock:
            self._streamer_turns = []
        self._reload_profile()      # 上一场下播写的档案，从这一场开始生效
        return []

    # ---- 下播：先收尾沉淀（跨场累计 + 常客判定写 note），再清空本场排名/缓冲 ----
    def _stream_end(self, ts):
        seen = set(self._gift_rank.keys()) | {u for u, _, _ in self._chat_buffer}
        for user in seen:
            v = self._get_viewer(user)
            sessions_seen = (v['sessions_seen'] if v else 0) + 1
            gift_total_lifetime = (v['gift_total_lifetime'] if v else 0) + self._gift_rank.get(user, 0)
            tier = v['tier'] if v else 'normal'
            note = None
            if (sessions_seen >= REGULAR_SESSIONS_THRESHOLD
                    or gift_total_lifetime >= REGULAR_GIFT_LIFETIME_THRESHOLD
                    or tier in ('member', 'star_guardian')):
                note = f"常客，已来 {sessions_seen} 场，累计送礼 {gift_total_lifetime} 抖币"
            # ⚠️ 这行必须在 if 外面：**本场露过面的人都要沉淀**，note 只是其中"够得上常客"
            # 的那部分额外带一句备注。放进 if 里会变成"只有常客才落库"，路人第二次来时
            # sessions_seen 永远是 1，再也攒不够场次——常客判定从此彻底失效。
            self._sediment_viewer(user, ts, sessions_seen, gift_total_lifetime, note)
        # 本场对话交给 worker 去总结成主播档案。**必须走后台**：这是一次真实的 LLM 调用，
        # 在总线线程里同步等会把整条总线卡住（见文件头"运行时不能卡住"）。
        # 先快照再清空，顺序不能反。
        with self._turns_lock:
            turns = list(self._streamer_turns)
            self._streamer_turns = []
        if turns:
            self._task_queue.put({'kind': 'profile', 'turns': turns, 'ts': ts,
                                  '_enqueued_at': time.time()})
        # 有限记忆：场次 +1，忘掉超过 10 场没来的、以及超出 30 人上限里最久没见的。
        # **顺序必须在沉淀之后**——先 +1 的话本场露过面的人会被记成"下一场见过的"，
        # 场次差整体偏移一格（memory.evict_viewers 的文档里写了同一件事）。
        # 按场次而不是按天数：主播不定期开播，休息两周会把所有人误判成"很久没来"。
        with self._db_lock:
            gone = memory.evict_viewers(self._db)
        if gone:
            print(f'[dialogue] 本场收尾：忘掉了 {gone} 位很久没来的观众', flush=True)
        self._gift_rank = {}
        self._chat_buffer = []
        return []

    # ---- 观众画像表读写：唯一允许碰 self._db 的入口，统一在这里加锁（见 __init__ 里 _db_lock
    #      的注释——总线线程和 worker 线程都会调，sqlite3 连接本身不是无锁并发安全的）----
    def _get_viewer(self, nickname):
        with self._db_lock:
            return memory.get_viewer(self._db, nickname)

    def _upsert_viewer(self, nickname, ts, gift_total=None):
        with self._db_lock:
            memory.upsert_viewer(self._db, nickname, ts, gift_total=gift_total)

    def _set_tier(self, nickname, tier, source='manual'):
        with self._db_lock:
            return memory.set_tier(self._db, nickname, tier, source)

    def _sediment_viewer(self, nickname, ts, sessions_seen, gift_total_lifetime, note):
        with self._db_lock:
            memory.sediment_session(self._db, nickname, ts, sessions_seen, gift_total_lifetime, note)

    # ---- 送礼：记账（不受闭嘴影响）+ 前三名可追加个性化答谢（入队，不等 LLM）----
    def _danmaku_gift(self, d, ts):
        user, coins = d.get('user'), d.get('value_coins', 0)
        if not user:
            return []
        self._gift_rank[user] = self._gift_rank.get(user, 0) + coins
        self._upsert_viewer(user, ts, gift_total=self._gift_rank[user])
        if self.mode in QUIET_MODES:
            return []
        if user in self._top_gift_users() and self._throttle_ok(user, ts):
            # 送礼答谢也按分层决定出不出声：本场送礼超过 1000 钻本身就够格算 VIP，
            # 小额礼物很频繁（实测一场 17 次里大半是 1 抖币的粉丝团灯牌），每次都出声会吵。
            # 送礼答谢：VIP + 金额够门槛才出声，且绕过全局配速（理由见 _voice_ok）。
            # 门槛跟 brain 那边一致——一场 17 次送礼里大半是 1 抖币的粉丝团灯牌。
            big = int(d.get('value_coins') or 0) >= GIFT_SPEAK_MIN_COINS
            speak = (big
                     and self.is_vip(user, d.get('fansclub_level'))
                     and self._voice_ok(user, ts, bypass_pace=True, bypass_burst=True))
            self._enqueue_viewer_reply(user, '', ts, gift_thanks=True, voice=speak)
        return []

    def _top_gift_users(self):
        ranked = sorted(self._gift_rank.items(), key=lambda kv: -kv[1])[:TOP_GIFT_N]
        return {u for u, _ in ranked}

    def is_vip(self, user, fansclub_level=0):
        """这位观众属不属于"必回且出声"那一档。主播 2026-07-30 定的分层：
        会员 / 星守护 / 灯牌 ≥8 级 / 本场送礼 >1000 钻，四者任一即是。

        会员和星守护只能靠主播手动勾（真实抓包数据里没有这两个身份的字段），
        灯牌和送礼额则是数据里自带、能自动判定的。
        """
        v = self._get_viewer(user)
        if v and v['tier'] in ('member', 'star_guardian'):
            return True
        if (fansclub_level or 0) >= VIP_FANSCLUB_LEVEL:
            return True
        return self._gift_rank.get(user, 0) > VIP_SESSION_COINS

    # ---- 弹幕聊天：进缓冲（不受闭嘴影响）。回不回、出不出声，按主播定的分层来 ----
    def _danmaku_chat(self, d, ts):
        user, text = d.get('user'), d.get('text', '')
        if user:
            self._chat_buffer.append((user, text, ts))
            self._chat_buffer = self._chat_buffer[-CHAT_BUFFER_SIZE:]
        if self.mode in QUIET_MODES or not user:
            return []

        # 分两档（主播 2026-07-30 定，理由是"每条都回很累、一直有语音影响直播"）：
        #   VIP（会员/星守护/灯牌≥8/本场>1000钻）→ **必回、不限流、带语音**
        #   普通观众 → **必须在弹幕里喊到她的名字**才考虑回，且每分钟最多 1 条，纯文字气泡
        if self.is_vip(user, d.get('fansclub_level')):
            voice = self._voice_ok(user, ts)
            self._last_msg_ts[user] = ts
            self._enqueue_viewer_reply(user, text, ts, voice=voice)
            return []
        self._last_msg_ts[user] = ts
        if PET_NAME_IN_DANMAKU in text and self._throttle_ok(user, ts):
            self._enqueue_viewer_reply(user, text, ts, voice=False)
        return []

    def _voice_ok(self, user, ts, bypass_pace=False, bypass_burst=False):
        """这一条要不要出声。**VIP 一定会被回复，这里只决定出不出声**——没轮上的走文字气泡。

        两层，缺一不可（依据是真实录制数据，见 VOICE_PACE_MS 上方那段）：
          1. **连发降级**：同一个人上一条弹幕在 30 秒内 → 这条只出文字。
             这层治"一个人突然刷三条"。
          2. **全局配速**：距上次出声不到 2 分钟 → 只出文字。
             这层才是主力——VIP 并不刷屏，但 15 个人各自"每分钟一句"叠起来就是每分钟好几句。

        **送礼两层都绕过**：
        - 绕过配速：有人送了 99 钻，结果因为"刚出过声"只给个文字气泡，对送礼的人不合适；
          礼物本来就不频繁（实测一场 17 次），放行不会把语音密度拉回去。
        - 绕过连发：连发那层看的是**弹幕**间隔，而送礼的人往往前脚刚发过弹幕（实测就是这样，
          送礼前 5 秒还在聊天）。拿弹幕的节奏去卡礼物属于误伤，礼物自己有 `_throttle_ok`
          的同人 20 秒冷却挡连击。
        """
        prev = self._last_msg_ts.get(user)
        if not bypass_burst and prev is not None and ts - prev < VIP_BURST_MS:
            return False
        if not bypass_pace and self._last_voice_ts is not None \
                and ts - self._last_voice_ts < VOICE_PACE_MS:
            return False
        self._last_voice_ts = ts
        return True

    # ---- 节流：同人冷却 + 每分钟上限（仅用于"自动判断值不值得回"的路径，
    #      主播主动要求的批量回复/语音聊天不受此限制。只在总线接收线程里跑（worker 线程
    #      不碰这两个计数器），单 worker 下没有跨线程竞态，不需要加锁——见 SPEC.md 里
    #      "升级到 2-3 个 worker 时才需要给这里加锁"那条）----
    def _throttle_ok(self, user, ts):
        last = self._last_reply_ts.get(user)
        if last is not None and ts - last < COOLDOWN_PER_USER_MS:
            return False
        self._reply_times = [t for t in self._reply_times if ts - t < 60_000]
        if len(self._reply_times) >= MAX_REPLIES_PER_MINUTE:
            return False
        self._last_reply_ts[user] = ts
        self._reply_times.append(ts)
        return True

    # ---- 入队：把"回给谁、回什么"扔进后台任务队列，立刻返回，不等 DeepSeek ----
    def _enqueue_viewer_reply(self, user, text, ts, gift_thanks=False, voice=False):
        # 队列满了就丢新的，不是丢老的：老的排在前面、马上就要播，丢它反而白等一场。
        # 丢弃本身不出声也不提示——待机气泡里有一句常驻说明（"本公主只能同时看这么多条哦"），
        # 靠那个让观众知道，比每次丢都吭一声干净。
        if self._task_queue.qsize() >= MAX_PENDING_REPLIES:
            self.dropped += 1
            return
        self._task_queue.put({'kind': 'viewer', 'user': user, 'text': text, 'ts': ts,
                              'gift_thanks': gift_thanks, 'voice': voice,
                              '_enqueued_at': time.time()})

    def _enqueue_streamer_reply(self, text, ts):
        self._task_queue.put({'kind': 'streamer', 'text': text, 'ts': ts, '_enqueued_at': time.time()})

    # ---- 后台 worker（v1 唯一一个，不并发，见 SPEC.md"运行时不能卡住"）----
    def _worker_loop(self):
        while True:
            task = self._task_queue.get()
            # 只有一个 worker，任务是严格串行的：排队等待的时间往往比 LLM 本身还长。
            # 把"排了多久 + 生成用了多久 + 后面还堆着几个"一起打出来，回复慢时才分得清
            # 是模型慢还是在排队（两者的解法完全不同：换模型 vs 加 worker）。
            waited = time.time() - task.get('_enqueued_at', time.time())
            t0 = time.time()
            try:
                for m in self._run_task(task):
                    if not contract.validate_message(m):
                        self.outbox.put(m)
            finally:
                print(f"[dialogue][耗时] {task.get('kind')} 排队{waited:.1f}s 生成{time.time() - t0:.1f}s "
                      f"后面还有{self._task_queue.qsize()}个", flush=True)
                self._task_queue.task_done()

    def _run_task(self, task):
        if task['kind'] == 'viewer':
            return self._generate_viewer_reply(task['user'], task['text'], task['ts'],
                                               task.get('gift_thanks', False), task.get('voice', False))
        if task['kind'] == 'profile':
            return self._summarize_profile(task['turns'], task['ts'])
        if task['kind'] == 'streamer':
            return self._generate_streamer_reply(task['text'], task['ts'])
        return []

    # ---- 生成一条观众回复（worker 线程跑）：查记忆 -> LLM -> 护栏 -> 更新记忆 ----
    def _generate_viewer_reply(self, user, text, ts, gift_thanks=False, voice=False):
        v = self._get_viewer(user)
        note = v['note'] if v else None
        prompt = persona.build_viewer_prompt(user, text, note, gift_thanks=gift_thanks)
        raw = chat.reply(persona.SYSTEM_PROMPT, prompt, ts=ts)
        safe = guardrails.enforce(raw)
        self._upsert_viewer(user, ts)
        # **出不出声是分层决定的**：只有 VIP（会员/星守护/灯牌≥8/本场>1000钻）才发 `speak`，
        # 普通观众发 `show_bubble` 纯文字。理由是主播实播后反馈"一直有语音会影响直播观感"。
        # 契约里 show_bubble 本来就是"文字气泡"，不用为这件事新增事件类型。
        if voice:
            return [_act('speak', {'text': safe, 'emotion': 'cheerful'}, ts)]
        return [_act('show_bubble', {'text': safe, 'duration_ms': BUBBLE_MS}, ts)]

    # ---- 生成一条主播自由聊天回复（worker 线程跑）----
    def _generate_streamer_reply(self, text, ts):
        # 跟主播说话给更大的额度：默认 200 token 按实测 1.5 字/token 算才 300 字，
        # 三五句就顶到天花板了。观众侧不动（那边就该短）。
        #
        # 带上本场已经说过的话，她才接得住"就是刚才那个房间"这种指代。
        # 只读一份快照再调用：LLM 那一步几秒钟，期间不该一直占着锁。
        with self._turns_lock:
            history = list(self._streamer_turns)
        raw = chat.reply(self._system_prompt, persona.build_streamer_prompt(text),
                         ts=ts, max_tokens=STREAMER_MAX_TOKENS, history=history)
        safe = guardrails.enforce(raw)
        # 记进本场对话。**存的是主播的原话和护栏之后的回复**——护栏前的原始文本不进上下文，
        # 免得被拦下来的内容又通过历史绕回模型面前。
        # 两条都在 worker 线程里追加，而 worker 只有一个，所以顺序天然跟对话顺序一致。
        self._remember_turn(text, safe)
        return [_act('speak', {'text': safe, 'emotion': 'cheerful'}, ts)]

    # ---- 下播收尾：把本场对话总结成主播档案（worker 线程跑，不挡总线）----
    def _summarize_profile(self, turns, ts):
        """重写主播档案。**不产出任何 action**——这是纯记账，观众不该看到她"正在总结主播"。

        失败就保持旧档案不动：宁可这一场没学到东西，也不要写进一段残缺的印象——档案是
        跨场生效的，写坏了会连着影响后面每一场。
        """
        with self._db_lock:
            cur = streamer_profile.load(self._db)
        old = cur['profile'] if cur else None
        prompt = streamer_profile.build_summary_prompt(old, turns)
        # 这次不带 history：总结的输入就是完整对话本身，再塞一遍历史是重复。
        raw = chat.reply(persona.SYSTEM_PROMPT, prompt, ts=ts, timeout=60,
                         max_tokens=PROFILE_MAX_TOKENS)
        text = (raw or '').strip()
        # 兜底话术（没配 key/超时时 chat.reply 会返回角色台词）绝不能当成档案写进去
        if not text or text in chat.FALLBACK_LINES or text in chat.TIMEOUT_FALLBACK_LINES:
            print('[dialogue] 主播档案这次没更新（模型没给出有效内容），保留上一版', flush=True)
            return []
        with self._db_lock:
            streamer_profile.save(self._db, text, ts)
        print(f'[dialogue] 主播档案已更新（{len(text)} 字）', flush=True)
        return []

    def _remember_turn(self, said, replied):
        with self._turns_lock:
            self._streamer_turns.append({'role': 'user', 'content': said})
            self._streamer_turns.append({'role': 'assistant', 'content': replied})
            if len(self._streamer_turns) > MAX_STREAMER_TURNS:
                # 保险丝触发时砍最老的。正常场次碰不到（3 小时约 1.7 万 token，窗口 100 万），
                # 真砍到了说明进程跨场常驻没清干净，日志里能看出来。
                self._streamer_turns = self._streamer_turns[-MAX_STREAMER_TURNS:]

    def streamer_turns(self):
        """本场对话快照（测试和体检用，不给外部改）。"""
        with self._turns_lock:
            return list(self._streamer_turns)

    # ---- 语音：mute/sleep/unmute/wake 与面板/弹幕关键词等效，最高优先级 ----
    def _audio_command(self, d, ts):
        if not d.get('speaker_verified'):
            return []                              # 非主播声音：丢弃（"只识别我的声音"）
        intent, text = d.get('intent'), d.get('raw_text', '')

        if intent in ('mute', 'sleep', 'unmute', 'wake'):
            self._awaiting_walkthrough = None      # 模式切换最高优先级，顺带清掉悬空的确认等待
            return self._mirror_mode(intent)

        if self._awaiting_walkthrough is not None:  # 上一句刚问过"要不要看攻略"，这句是答案
            deadline, asked_text = self._awaiting_walkthrough
            self._awaiting_walkthrough = None
            if ts <= deadline:
                if _is_affirmative(text):
                    # ⚠️ **note 必须带上主播最初那句原话，不能只传这句"是"。**
                    # 求助原话里的关卡、房间、上一个任务，才是搜索质量的来源；只传"是"
                    # 等于把它全丢了（2026-07-30 修，之前就是这个 bug）。
                    # 主播在确认时又补充了内容的话（"是，我在二楼那个房间"），两句都留着。
                    extra = _beyond_affirmation(text)
                    note = f'{asked_text} {extra}'.strip() if extra else asked_text
                    return [_cmd('do', {'action': 'walkthrough', 'note': note}, ts)]
                return [_act('speak', {'text': "好吧，是我敏感了哈哈我主人果然厉害", 'emotion': 'funny'}, ts)]
            # 超过确认窗口：这句不算答案了，按下面正常意图继续处理

        if self.mode in QUIET_MODES:
            return []                              # 闭嘴/休息：不主动开口（反问/批量回复/自由聊天都算开口）

        if intent == 'walkthrough':                 # "帮我找找攻略…"——已经明说了，直接办，不反问
            return [_act('speak', {'text': "好嘞，我这就去找攻略！", 'emotion': 'curious'}, ts),
                    _cmd('do', {'action': 'walkthrough', 'note': text}, ts)]

        if intent == 'walkthrough_ask':             # 只是随口提了句卡关——先反问，别乱截屏搜一通
            # 把原话一起存下来：确认之后要靠它当搜索依据，不能只剩一个"是"
            self._awaiting_walkthrough = (ts + WALKTHROUGH_CONFIRM_WINDOW_MS, text)
            return [_act('speak', {'text': "卡关啦？需要我来提供攻略帮助吗？", 'emotion': 'curious'}, ts)]

        if intent == 'review_chat':                 # "看一下弹幕帮我回一下"
            return self._batch_reply(ts)

        if intent == 'chat' and text:                # 命中不了固定 intent，转发来的自由聊天文本
            self._enqueue_streamer_reply(text, ts)

        return []

    def _batch_reply(self, ts):
        # 主播喊"看一下弹幕"时一次要回 10 条。**这里尤其不能一律出声**——十条语音连着念
        # 是最吵的情况，正是主播说的"一直有语音会影响直播"。所以照样逐人按分层判定：
        # VIP 出声，其余纯文字气泡。
        recent = self._chat_buffer[-BATCH_REPLY_COUNT:]
        for user, text, _ in recent:
            self._enqueue_viewer_reply(user, text, ts, voice=self.is_vip(user))
        return []

    # ---- 测试/调试辅助：等 worker 把当前队列消化完。生产环境不需要调用——run.py 靠
    #      outbox.get() 阻塞消费，天然会等到东西；这两个方法只是让"异步"在离线测试里
    #      也能确定性地断言。----
    def wait_idle(self, timeout=5.0):
        done = threading.Event()

        def _join():
            self._task_queue.join()
            done.set()
        threading.Thread(target=_join, daemon=True).start()
        return done.wait(timeout)

    def drain_outbox(self):
        out = []
        while True:
            try:
                out.append(self.outbox.get_nowait())
            except queue.Empty:
                break
        return out
