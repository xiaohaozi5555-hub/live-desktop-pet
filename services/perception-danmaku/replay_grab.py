#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把录下来的真实直播数据重放到总线上，不用真开播就能验证桌宠的整条反应链。

用法（先正常启动桌宠，让总线和各服务都跑起来，然后另开一个终端）：
    python services/perception-danmaku/replay_grab.py                 # 默认 10 倍速重放最新一份
    python services/perception-danmaku/replay_grab.py --speed 1       # 原速，用来看真实节奏
    python services/perception-danmaku/replay_grab.py --minutes 5     # 只放录音的前 5 分钟
    python services/perception-danmaku/replay_grab.py --no-lifecycle  # 不发开播/下播

**它不是"验证员"，也不占数据链路的位置**：`run.py` 才是真实链路（连抓包程序 → 总线），
`record_grab.py` 只旁观判卷。这个脚本是第三种东西——**离线回放**，只在没真开播时用来
把历史数据灌进总线。三者不要混：真开播时不该跑这个。

⚠️ **限流是按真实时钟算的**（"每分钟最多回 1 条普通观众"）。所以压缩倍速重放时，
桌宠的回复会比真实直播少得多——10 倍速下，录音里的 10 分钟只占真实 1 分钟，那一分钟里
仍然只会回 1 条。想看真实的回复密度就用 `--speed 1` 配合 `--minutes` 放一小段。

⚠️ 默认会重放开播/下播事件。下播会触发**真实的**收尾：总结主播档案（一次 LLM 调用）、
观众场次 +1 并淘汰很久没来的人。这些都会写进真实的 `dialogue_memory.db`。不想动数据库
就加 `--no-lifecycle`。
"""
import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from bus_client import BusClient   # noqa: E402
import normalize                   # noqa: E402

CACHE = os.path.join(REPO, '.cache', 'grab')
# 类型号 -> 人话，只为打印好看（对应 normalize_grab 里的分支）
KIND = {1: '弹幕', 2: '点赞', 3: '进场', 4: '关注', 5: '礼物',
        6: '统计', 7: '粉丝团', 8: '分享', 101: '开播', 102: '下播'}


def newest_raw():
    files = sorted(glob.glob(os.path.join(CACHE, 'raw-*.jsonl')))
    return files[-1] if files else None


def load(path):
    """读原始包。`_recv_ms` 是录制时的接收时刻，用来还原事件之间的间隔。"""
    rows = []
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    rows.sort(key=lambda r: r.get('_recv_ms') or 0)
    return rows


def run(path, speed=10.0, minutes=None, limit=None, lifecycle=True, port=8765):
    rows = load(path)
    if not rows:
        print(f'[replay] {path} 里没有可用数据')
        return 1
    base = rows[0].get('_recv_ms') or 0
    if minutes:
        cutoff = base + minutes * 60_000
        rows = [r for r in rows if (r.get('_recv_ms') or 0) <= cutoff]
    if not lifecycle:
        rows = [r for r in rows if r.get('Type') not in (101, 102)]
    if limit:
        rows = rows[:limit]

    span = ((rows[-1].get('_recv_ms') or 0) - base) / 1000.0
    print(f'[replay] {os.path.basename(path)}：{len(rows)} 条，录音跨度 {span/60:.1f} 分钟，'
          f'{speed:g} 倍速重放 → 约 {span/speed/60:.1f} 分钟')
    if speed > 1:
        print('[replay] ⚠️ 限流按真实时钟算，压缩倍速下桌宠回复会明显少于真实直播')
    if lifecycle:
        print('[replay] ⚠️ 会重放开播/下播，下播将真实触发档案总结与观众淘汰（--no-lifecycle 可跳过）')

    bus = BusClient(port=port, source='perception.danmaku.replay').connect()
    print('[replay] 已连总线，开始…\n')

    started = time.time()
    stats = {}
    sent = 0
    for r in rows:
        # 按录音里的相对时刻等待，保证节奏跟真实直播一致（只是整体被压缩了 speed 倍）
        due = ((r.get('_recv_ms') or 0) - base) / 1000.0 / speed
        wait = due - (time.time() - started)
        if wait > 0:
            time.sleep(wait)
        msg = normalize.normalize_grab(r)
        t = r.get('Type')
        stats[t] = stats.get(t, 0) + 1
        if msg is None:
            continue                       # 统计/粉丝团/分享这几类契约里没有，本来就丢弃
        bus.publish(msg)
        sent += 1
        d = msg.get('data') or {}
        who = d.get('user') or ''
        extra = d.get('text') or d.get('gift_name') or ''
        print(f"  [{KIND.get(t, t)}] {who} {extra}"[:88], flush=True)

    print(f'\n[replay] 放完了：读入 {len(rows)} 条，发上总线 {sent} 条')
    print('[replay] 各类型：' + '  '.join(f'{KIND.get(k, k)}×{v}' for k, v in sorted(stats.items())))
    print('[replay] 桌宠的反应看它自己的窗口和控制台；这个脚本只负责放数据，不判分。')
    time.sleep(2)                          # 给最后几条留点时间走完总线
    bus.close()
    return 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='原始包路径，默认取 .cache/grab/ 里最新的一份')
    ap.add_argument('--speed', type=float, default=10.0, help='倍速，1=原速')
    ap.add_argument('--minutes', type=float, help='只放录音的前 N 分钟')
    ap.add_argument('--limit', type=int, help='最多放几条')
    ap.add_argument('--no-lifecycle', action='store_true', help='不重放开播/下播事件')
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    f = a.file or newest_raw()
    if not f or not os.path.exists(f):
        print('[replay] 找不到原始包。先真开播录一次，或用 --file 指定。')
        sys.exit(2)
    sys.exit(run(f, speed=a.speed, minutes=a.minutes, limit=a.limit,
                 lifecycle=not a.no_lifecycle, port=a.port))
