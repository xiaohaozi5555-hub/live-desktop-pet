#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「验证员」：开播时无人值守地判卷——记录真实数据，并检查桌宠的反应是否达到我们的标准。

存在的理由：接真实弹幕那次验证，主播开播时不会开着 Claude，没人能在旁边观察。所以那些
只能靠真流量回答的问题（礼物数量看哪个字段、价值单位、粉丝团怎么分、有没有时间戳、
会不会串到别人直播间），以及"桌宠到底有没有正确反应"，都必须由程序自己记账，下播后回看。

**它只观察，不参与数据链路。** 两条独立的连接：
  - 自己连一份抓包程序的 WebSocket → 拿原始包，回答字段层面的问题；
  - 在总线上**只订阅不发布** → 看桌宠收到了什么、又做出了什么（ReactionWatcher）。
把弹幕送上总线是 `run.py` 的职责。这样分工的意义在于：验证员停掉，桌宠照常工作；桌宠坏了，
验证员照样能如实记下"没反应"——判卷的人不能同时是答题的人。

三条硬要求，都是"一场直播只有一次机会"逼出来的：
  1) 不能崩——任何一条包解析失败都只记错误、不中断；掉线自动重连。
  2) 边跑边落盘——原始包实时 flush，报告定期覆盖写，进程被强杀也不丢已收到的数据。
  3) 自己得出结论——报告直接给出各字段的取值分布，而不是留一堆原始 JSON 等人去翻。

用法：
    python record_grab.py                     # 连 127.0.0.1:8888 开录
    python record_grab.py --port 8888         # 指定端口
    python record_grab.py --replay <raw.jsonl>  # 离线重放已录的原始包（自测/复盘用）

产物（都在 .cache/grab/，已被 .gitignore 排除——里面有观众真实昵称，不进版本库）：
    raw-<时间>.jsonl    每行一条原始包，原样保存 + _recv_ms 到达时间
    report-<时间>.txt   人看的体检报告
    report-<时间>.json  同样内容的结构化版本
"""
import argparse
import collections
import datetime
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
try:
    # line_buffering=True：stdout 被 Electron 管道接走时 Python 默认全缓冲，日志会卡在
    # 缓冲区里出不来（2026-08-02 真开播实测，这个服务一整场日志全丢）。见 perception-danmaku/run.py 同处注释。
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass
from normalize import normalize_grab  # noqa: E402
from wsclient import WSClient, WSError  # noqa: E402

OUT_DIR = os.path.join(REPO, ".cache", "grab")

TYPE_NAMES = {1: "弹幕", 2: "点赞", 3: "进直播间", 4: "关注", 5: "礼物", 6: "统计",
              7: "粉丝团", 8: "分享", 9: "下播", 101: "直播伴侣开播", 102: "直播伴侣下播"}

# 判断一个值像不像"毫秒时间戳"：13 位、且落在 2020-2100 年之间
TS_LO, TS_HI = 1577836800000, 4102444800000


class ReactionWatcher:
    """在总线上旁观：进来一条弹幕，桌宠有没有反应、反应对不对、隔了多久。

    这是验证员的本职——**判卷，不参与答题**。它只订阅总线，从不发布；数据链路是
    `run.py` 的事，两者互不依赖，所以验证员停掉桌宠照常工作，桌宠坏了验证员照样能
    如实记下"没反应"。

    归因方式（08-01 真开播后改过一次，别再退回猜时序那版）：**按 `ts` 精确匹配，不猜**。
    brain.py/dialogue.py 产出的每条 action，`ts` 字段原样继承自触发它的那条感知事件——
    哪怕 dialogue 要经过后台 worker 排队+等 LLM 生成好几秒才真正发布，那条 action 上的
    `ts` 依然是最初那条弹幕的 `ts`，不是生成完成的时刻。所以只要把"最近见过的感知事件"
    按它们自己的 `ts` 存起来，动作来了直接按 `ts` 查表就行，不用猜"离现在最近的是哪条"。

    旧版靠"总线上刚好离得最近"猜，08-01 真开播实测证明这个猜法在**要等几秒 LLM 生成**的
    反应上（dialogue 的弹幕回复）系统性猜错——弹幕/点赞/新进场随时可能插进那几秒里，
    把归因抢走。真事件的时序压力测试也证实：这类误判解释不了"完全 0 反应"这种干净的
    数字（那是另外的 bug，见 EXPECT 表和 on_bus 下面的注释），但确实会在真实数据里造成
    可观的漏计——ts 精确匹配把这整类问题连根拔掉，不需要再猜。
    没有对应感知事件的动作（比如 brain 的合批欢迎/定时互动，是按 tick 时间发的，不是由
    单条弹幕触发）匹配不上，如实记进"无法归因"——这是诚实，不是新增的缺陷。
    """
    MAX_TRACKED = 3000    # 不再是"匹配容忍窗口"，纯粹防止长时间不下播时内存无限增长

    # 我们对桌宠的标准：某类弹幕应当触发哪些动作、是否应当出声
    # ⚠️ 08-01 真开播发现 danmaku.gift 这行是 07-30"分层"改造之前定的老标准，一直没跟着
    # 更新：brain.py 早已按 GIFT_SPEAK_MIN_COINS 分级，小额礼物（真实数据里大半是 1 抖币的
    # 灯牌）只弹文字气泡不出声。继续写死 speak:True 会让"语音带昵称"这个诊断指标对小额礼物
    # 系统性显示 0——**这是判卷标准过期，不是桌宠没反应**。改成 False 对应真实的"大多数
    # 情况"，仅供参考，不是强制断言。
    # ⚠️ danmaku.enter 这行是同一晚稍早踩过的同一个坑——这次是自己给自己挖的：brain.py
    # 里进场欢迎从 speak 改 show_bubble（见 WELCOME_WINDOW_MS 那次改动）之后，这里没有
    # 跟着改，导致 verify_grab_end2end.py 立刻测出"语音里带上昵称"恒为 0——改完一处
    # EXPECT 却忘了改另一处，教训是**改 brain.py 的出声方式，必须同时检查这张表**。
    EXPECT = {
        "danmaku.enter":  {"motions": ("wave",), "speak": False},
        "danmaku.like":   {"motions": ("praise",), "speak": False},
        "danmaku.gift":   {"motions": ("thank_small", "thank_big"), "speak": False},
        "danmaku.follow": {"motions": ("beg",), "speak": True},
        # 弹幕文字由 brain 按关键词决定要不要理，不设硬性期望，只统计
        "danmaku.chat":   {"motions": (), "speak": False},
    }

    def __init__(self):
        self.events_by_ts = collections.OrderedDict()   # ts -> {type, user, recv_wall_ms, motions[], speaks[], bubbles[]}
        self.stray_actions = 0      # 找不到匹配感知事件的动作（tick 驱动的合批/主动互动等）
        self.lock = threading.Lock()

    def on_bus(self, msg):
        ch, t, ts = msg.get("channel"), msg.get("type"), msg.get("ts")
        now = time.time() * 1000
        with self.lock:
            # 只认 EXPECT 里那几类**真实弹幕事件**，不能用 `startswith("danmaku.")`：
            # 2026-08-02 新增的 `danmaku.health` 是链路健康信号、不是观众行为，按前缀收下来
            # 会在报告里多出一行永远"没反应"的假统计。
            if ch == "perception" and t in self.EXPECT:
                # 理论上两条不同的感知事件可能撞在同一毫秒 ts 上，真实数据里没见过（08-01
                # 真实连击数据里最短间隔也有 1ms），撞了就后一条覆盖前一条、前一条的动作会被
                # 记成"无法归因"——比旧版"总是抢给最新一条"的系统性错误更少见也更诚实。
                self.events_by_ts[ts] = {"type": t, "user": (msg.get("data") or {}).get("user"),
                                         "recv_wall_ms": now, "motions": [], "speaks": [], "bubbles": []}
                while len(self.events_by_ts) > self.MAX_TRACKED:
                    self.events_by_ts.popitem(last=False)     # 最早插入的先淘汰
            elif ch == "action":
                # action 的 ts 原样继承自触发它的感知事件（brain.py/dialogue.py 的 _act()
                # 调用点都这么传，哪怕 dialogue 经过后台 worker 排队+等 LLM 生成好几秒才真正
                # 发布），直接按 ts 查表即可，不用再猜"总线上刚好离得最近的是哪条"。
                e = self.events_by_ts.get(ts)
                if e is None:
                    self.stray_actions += 1
                    return
                lat = now - e["recv_wall_ms"]
                if t == "play_motion":
                    e["motions"].append((msg.get("data") or {}).get("motion"))
                elif t == "speak":
                    e["speaks"].append(((msg.get("data") or {}).get("text") or "", lat))
                elif t == "show_bubble":
                    # 08-01 真开播发现的漏记：这里此前只认 play_motion/speak 两种，
                    # 07-30 分层改造后大量反应改走 show_bubble，一直被这里当空气——
                    # 反应本身没丢，是判卷的人没看这一类，"完全没反应"因此是假警报。
                    e["bubbles"].append(((msg.get("data") or {}).get("text") or "", lat))

    def summary(self):
        by = collections.defaultdict(lambda: {"事件数": 0, "有反应": 0, "动作符合预期": 0,
                                              "语音里带上昵称": 0, "文字里带上昵称": 0, "延迟ms": []})
        with self.lock:
            events = list(self.events_by_ts.values())
        for e in events:
            row = by[e["type"]]
            row["事件数"] += 1
            reacted = bool(e["motions"] or e["speaks"] or e["bubbles"])
            row["有反应"] += 1 if reacted else 0
            exp = self.EXPECT.get(e["type"], {})
            want = exp.get("motions") or ()
            if want and any(m in want for m in e["motions"]):
                row["动作符合预期"] += 1
            if exp.get("speak") and e["user"] and any(e["user"] in s for s, _ in e["speaks"]):
                row["语音里带上昵称"] += 1
            if e["user"] and any(e["user"] in s for s, _ in e["bubbles"]):
                row["文字里带上昵称"] += 1
            for _, lat in e["speaks"] + e["bubbles"]:
                row["延迟ms"].append(round(lat))

        out = {}
        for t, row in by.items():
            lat = row.pop("延迟ms")
            row["平均延迟ms"] = round(sum(lat) / len(lat)) if lat else None
            row["最大延迟ms"] = max(lat) if lat else None
            out[t] = row
        return {"分类统计": out, "无法归因的动作数": self.stray_actions}


class Recorder:
    def __init__(self, out_dir=OUT_DIR, watcher=None):
        self.watcher = watcher          # 只读它的统计，不反过来驱动它
        os.makedirs(out_dir, exist_ok=True)
        # 文件名必须唯一到不会撞：秒级时间戳不够——进程崩溃后一秒内重启，或同一秒里起两个
        # 实例，就会共用同一份文件（自检里出现过"一边读一边往同一文件追加"的死循环）。
        base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.stamp, n = base, 1
        while os.path.exists(os.path.join(out_dir, f"raw-{self.stamp}.jsonl")):
            n += 1
            self.stamp = f"{base}-{n}"
        self.raw_path = os.path.join(out_dir, f"raw-{self.stamp}.jsonl")
        self.txt_path = os.path.join(out_dir, f"report-{self.stamp}.txt")
        self.json_path = os.path.join(out_dir, f"report-{self.stamp}.json")
        self.raw_fp = None
        self.started_ms = int(time.time() * 1000)
        # 统计桶
        self.total = 0
        self.by_type = collections.Counter()
        self.by_process = collections.Counter()
        # 直播间**只能按 RoomId 归并**：并非每条包都带 WebRoomId/RoomTitle，若把三者
        # 拼成联合键，同一个直播间会被拆成好几个，串台检测就永远误报（自检里踩到过）。
        self.rooms = collections.Counter()          # RoomId -> 条数
        self.room_meta = collections.defaultdict(lambda: {"WebRoomId": set(), "RoomTitle": set()})
        self.gift_rows = collections.Counter()      # (礼名, GiftCount, RepeatCount, DiamondCount)
        self.fields_by_type = collections.defaultdict(set)
        self.ts_candidates = collections.Counter()  # 字段名 -> 命中次数
        self.fansclub_rows = collections.Counter()  # (Data.Type, Data.Level)
        # 观众身份字段清单。**这是留给"会员/星守护到底在哪个字段"这个悬案的**：
        # 抖音的会员和星守护是各自独立的付费身份，跟粉丝团等级无关（2026-07-29 用户指出，
        # 之前拿等级近似是错的）。所以把 User 里出现过的每个字段、以及它取到过的值都记下来，
        # 真实开播后一看就知道哪个字段在标识身份。
        self.user_fields = collections.defaultdict(collections.Counter)   # 字段名 -> 取值分布
        self.users_seen = set()
        self.norm_types = collections.Counter()
        self.dropped = collections.Counter()        # 被 normalize_grab 丢弃的 Type
        self.fallback_nick = 0                      # 昵称兜底成"观众"的次数（回归监控）
        self.errors = []

    # ---- 落盘 ----
    def open(self):
        self.raw_fp = open(self.raw_path, "a", encoding="utf-8")
        return self

    def close(self):
        if self.raw_fp:
            try:
                self.raw_fp.close()
            except Exception:
                pass
            self.raw_fp = None

    def feed(self, pack, recv_ms=None):
        """吃一条原始包：落盘 + 统计 + 过一遍归一化。任何异常都只记录不抛出。"""
        self.total += 1
        try:
            if self.raw_fp:
                rec = dict(pack)
                rec["_recv_ms"] = recv_ms if recv_ms is not None else int(time.time() * 1000)
                self.raw_fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self.raw_fp.flush()          # 每条都 flush：被强杀也不丢
        except Exception as e:
            self._err("落盘失败", e)
        try:
            self._stat(pack)
        except Exception as e:
            self._err("统计失败", e)
        try:
            msg = normalize_grab(pack)
            if msg is None:
                self.dropped[pack.get("Type")] += 1
            else:
                self.norm_types[msg["type"]] += 1
                if msg.get("data", {}).get("user") == "观众":
                    self.fallback_nick += 1
        except Exception as e:
            self._err("归一化失败", e)

    def _err(self, what, e):
        line = f"{what}: {type(e).__name__}: {e}"
        if len(self.errors) < 200:                # 别让错误把内存撑爆
            self.errors.append(line)

    def _stat(self, pack):
        t = pack.get("Type")
        d = pack.get("Data") or {}
        # ⚠️ 跟 normalize_grab() 同一个坑：真实数据里 Data 是 JSON 字符串，不是对象，要再解
        # 一次。08-01 真开播实测：这里当年没跟着 07-30 那次一起改，200 条打头就全报
        # `AttributeError: 'str' object has no attribute 'get'`，后面所有基于 d.get(...) 的
        # 字段分布统计（直播间/身份字段/粉丝团分布…）从此全是空的——不是真的没数据，是这个
        # 函数自己没跑到那一步。夹具里 Data 是对象，所以离线测不出来，回归测试见
        # verify_grab_recorder.py 的字符串形态用例。
        if isinstance(d, str):
            try:
                d = json.loads(d) or {}
            except (ValueError, TypeError):
                d = {}
        if not isinstance(d, dict):
            d = {}
        self.by_type[t] += 1
        self.by_process[pack.get("ProcessName") or "?"] += 1
        room = str(d.get("RoomId")) if d.get("RoomId") is not None else "(无RoomId)"
        self.rooms[room] += 1
        for k in ("WebRoomId", "RoomTitle"):
            if d.get(k) is not None:
                self.room_meta[room][k].add(str(d[k]))
        for k, v in d.items():
            self.fields_by_type[t].add(k)
            if isinstance(v, int) and TS_LO <= v <= TS_HI:
                self.ts_candidates[k] += 1
        user = d.get("User") or {}
        if isinstance(user, dict):
            for k, v in user.items():
                if k in ("Nickname", "HeadImgUrl", "Id", "DisplayId"):
                    continue                      # 昵称/头像/ID 是身份本身，不是身份"标记"，不做取值分布
                if isinstance(v, (bool, int, float, str)) and len(str(v)) <= 40:
                    self.user_fields[k][str(v)] += 1
            if user.get("Nickname"):
                self.users_seen.add(user["Nickname"])
        if t == 5:
            self.gift_rows[(d.get("GiftName"), d.get("GiftCount"), d.get("RepeatCount"), d.get("DiamondCount"))] += 1
        if t == 7:
            self.fansclub_rows[(d.get("Type"), d.get("Level"))] += 1

    # ---- 报告 ----
    def report_dict(self):
        dur = (int(time.time() * 1000) - self.started_ms) / 1000.0
        gift_same = [r for r in self.gift_rows if r[1] != r[2]]
        return {
            "开始时间": self.stamp, "持续秒数": round(dur, 1), "收到包数": self.total,
            "各类型条数": {f"{k}({TYPE_NAMES.get(k, '未知')})": v for k, v in sorted(self.by_type.items(), key=lambda x: -x[1])},
            "来源进程": dict(self.by_process),
            "出现过的直播间": [{"RoomId": r, "条数": n,
                          "WebRoomId": sorted(self.room_meta[r]["WebRoomId"]),
                          "RoomTitle": sorted(self.room_meta[r]["RoomTitle"])}
                          for r, n in self.rooms.most_common()],
            "礼物样本": [{"礼物": r[0], "GiftCount": r[1], "RepeatCount": r[2], "DiamondCount": r[3], "次数": n}
                     for r, n in self.gift_rows.most_common()],
            "GiftCount与RepeatCount是否始终相等": (len(gift_same) == 0) if self.gift_rows else None,
            "两者不等的样本": [{"礼物": r[0], "GiftCount": r[1], "RepeatCount": r[2]} for r in gift_same],
            "各类型出现过的字段": {f"{k}({TYPE_NAMES.get(k, '未知')})": sorted(v) for k, v in sorted(self.fields_by_type.items())},
            "疑似时间戳字段": dict(self.ts_candidates),
            "粉丝团Type与Level分布": [{"Data.Type": r[0], "Data.Level": r[1], "次数": n} for r, n in self.fansclub_rows.most_common()],
            "观众身份字段取值分布": {k: dict(v.most_common(12)) for k, v in sorted(self.user_fields.items())},
            "出现过的观众人数": len(self.users_seen),
            "归一化产出": dict(self.norm_types),
            "桌宠反应": self.watcher.summary() if self.watcher else None,
            "被丢弃的类型": {f"{k}({TYPE_NAMES.get(k, '未知')})": v for k, v in self.dropped.items()},
            "昵称兜底成观众的次数": self.fallback_nick,
            "错误": self.errors,
        }

    def _lines(self, rep):
        L = [f"验证员报告  {rep['开始时间']}  录了 {rep['持续秒数']} 秒，收到 {rep['收到包数']} 条", ""]
        L.append("【各类型条数】")
        for k, v in rep["各类型条数"].items():
            L.append(f"  {k}: {v}")
        L.append("")
        L.append("【来源进程】（只该有 直播伴侣；出现别的说明配置没过滤干净）")
        for k, v in rep["来源进程"].items():
            L.append(f"  {k}: {v}")
        L.append("")
        L.append("【出现过的直播间】（多于一个 = 串到别人直播间了）")
        for r in rep["出现过的直播间"]:
            L.append(f"  RoomId={r['RoomId']} 条数={r['条数']} "
                     f"WebRoomId={','.join(r['WebRoomId']) or '—'} 标题={','.join(r['RoomTitle']) or '—'}")
        L.append("")
        L.append("【礼物样本】（拿去跟抖音里的实际价格对，确认 DiamondCount 的单位）")
        for r in rep["礼物样本"]:
            L.append(f"  {r['礼物']}: GiftCount={r['GiftCount']} RepeatCount={r['RepeatCount']} "
                     f"DiamondCount={r['DiamondCount']} 出现{r['次数']}次")
        L.append(f"  GiftCount 与 RepeatCount 始终相等: {rep['GiftCount与RepeatCount是否始终相等']}")
        for r in rep["两者不等的样本"]:
            L.append(f"    不等样本: {r}")
        L.append("")
        L.append("【疑似时间戳字段】（空 = 消息里确实没带时间戳）")
        L.append(f"  {rep['疑似时间戳字段'] or '（无）'}")
        L.append("")
        L.append("【粉丝团 Type / Level 分布】（用来区分 新加入 和 升级）")
        for r in rep["粉丝团Type与Level分布"]:
            L.append(f"  Data.Type={r['Data.Type']} Data.Level={r['Data.Level']} 次数={r['次数']}")
        L.append("")
        L.append(f"【观众身份字段取值分布】（共 {rep['出现过的观众人数']} 位观众）")
        L.append("  ↓ 会员/星守护是独立的付费身份，跟粉丝团等级无关。看哪个字段能把他们区分出来。")
        for k, dist in rep["观众身份字段取值分布"].items():
            L.append(f"  {k}: {dist}")
        if not rep["观众身份字段取值分布"]:
            L.append("  （User 里除昵称/头像/ID 外没有别的字段）")
        L.append("")
        L.append("【各类型出现过的字段】")
        for k, v in rep["各类型出现过的字段"].items():
            L.append(f"  {k}: {', '.join(v)}")
        L.append("")
        L.append(f"【归一化产出】{rep['归一化产出']}")
        L.append("")
        L.append("【桌宠反应是否达标】（验证员在总线上旁观得出，不参与数据链路）")
        react = rep.get("桌宠反应")
        if not react:
            L.append("  未接总线，本次没有判卷（是否 broker 没在跑？）")
        elif not react["分类统计"]:
            L.append("  ⚠ 总线上一条弹幕都没看到——数据链路没通，桌宠不可能有反应")
        else:
            for t, row in sorted(react["分类统计"].items()):
                n = row["事件数"]
                L.append(f"  {t}: 共 {n} 条，有反应 {row['有反应']} 条"
                         f"（{round(100 * row['有反应'] / n)}%），动作符合预期 {row['动作符合预期']} 条，"
                         f"语音带昵称 {row['语音里带上昵称']} 条，文字带昵称 {row['文字里带上昵称']} 条，"
                         f"延迟 平均{row['平均延迟ms']}ms/最大{row['最大延迟ms']}ms")
                if row["有反应"] == 0:
                    L.append(f"    ⚠ 这一类**完全没有反应**，需要排查 brain")
            L.append(f"  无法归因的动作数: {react['无法归因的动作数']}")
        L.append(f"【被丢弃的类型】{rep['被丢弃的类型']}")
        L.append(f"【昵称兜底成“观众”的次数】{rep['昵称兜底成观众的次数']}（应为 0，不为 0 说明昵称字段又没对上）")
        if rep["错误"]:
            L.append("")
            L.append(f"【错误 {len(rep['错误'])} 条】")
            L.extend("  " + e for e in rep["错误"][:20])
        return "\n".join(L)

    def write_report(self):
        rep = self.report_dict()
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
            with open(self.txt_path, "w", encoding="utf-8") as f:
                f.write(self._lines(rep) + "\n")
        except Exception as e:
            self._err("写报告失败", e)
        return rep


def run_live(host, port, rec, report_every=30.0):
    """连 WS 服务、持续收包。掉线自动重连，永不主动退出（靠 Ctrl-C / 被杀）。"""
    last_report = time.time()
    backoff = 1.0
    while True:
        ws = WSClient(host, port)
        try:
            ws.connect()
            print(f"[验证员] 已连上 ws://{host}:{port}，开录 → {rec.raw_path}")
            backoff = 1.0
            while True:
                try:
                    text = ws.recv_text()
                except OSError as e:                 # socket.timeout 也是 OSError 子类
                    if isinstance(e, WSError):
                        raise
                    print(f"[验证员] {int(time.time() - last_report)}s 没有新消息（正常，等着就行）")
                    if time.time() - last_report > report_every:
                        rec.write_report()
                        last_report = time.time()
                    continue
                try:
                    pack = json.loads(text)
                except Exception:
                    rec._err("非 JSON 文本", ValueError(text[:120]))
                    continue
                rec.feed(pack)
                if rec.total % 50 == 0:
                    print(f"[验证员] 已收 {rec.total} 条  {dict(rec.by_type)}")
                if time.time() - last_report > report_every:
                    rec.write_report()
                    last_report = time.time()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[验证员] 连接断开（{type(e).__name__}: {e}），{backoff:.0f}s 后重连")
            rec.write_report()
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            ws.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--replay", help="离线重放已录的 raw jsonl，不连网络")
    ap.add_argument("--bus-port", type=int, default=8765)
    ap.add_argument("--no-bus", action="store_true", help="不连总线，只录原始数据不判卷")
    a = ap.parse_args()

    watcher = None
    if not a.no_bus:
        try:
            sys.path.insert(0, os.path.join(REPO, "services", "bus"))
            from bus_client import BusClient
            watcher = ReactionWatcher()
            # **只订阅，不发布**。数据链路是 run.py 的事，验证员只旁观判卷。
            BusClient(port=a.bus_port, source="grab-verifier").subscribe(watcher.on_bus).connect()
            print(f"[验证员] 已在总线 127.0.0.1:{a.bus_port} 上旁观桌宠的反应")
        except Exception as e:
            # 连不上也要继续录——录制是"一场直播只有一次机会"的事，判卷不是
            print(f"[验证员] ⚠ 连总线失败（{type(e).__name__}: {e}），本次只录原始数据、不判卷")

    rec = Recorder(watcher=watcher).open()
    try:
        if a.replay:
            with open(a.replay, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rec.feed(json.loads(line))
            print(f"[验证员] 重放完毕，共 {rec.total} 条")
        else:
            run_live(a.host, a.port, rec)
    except KeyboardInterrupt:
        print("\n[验证员] 收到中断，正在收尾…")
    finally:
        rep = rec.write_report()
        rec.close()
        print(f"[验证员] 报告已写入：\n  {rec.txt_path}\n  {rec.json_path}")
        print(f"[验证员] 共 {rep['收到包数']} 条，类型分布 {rep['各类型条数']}")


if __name__ == "__main__":
    main()
