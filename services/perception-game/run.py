#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""game 运行器：
  - 实时截游戏区 → 被吓启发式 → 发 perception.game.scare（本地、低延迟）。
  - 收到「卡关」指令(command.do{action:walkthrough} / audio.command intent=walkthrough)
    → 截游戏画面 → Claude 多模态(vision.py) → 发 perception.game.scene（需 API key）。
区域由 command.calibrate(region=game) 提供；默认主显示器。需 mss + numpy（攻略另需 anthropic + key）。"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
try:
    # line_buffering=True：不加的话 stdout 被 Electron 管道接走时会全缓冲，日志出不来。
    # 2026-08-02 真开播实测：主播用了卡关攻略，但 `[game] 找攻略中…` / `[game][耗时] 攻略…`
    # 这些关键诊断一行都没进 startup.log，事后完全无法判断搜索质量和耗时。
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
except Exception:
    pass
from bus_client import BusClient   # noqa: E402
from scare import ScareDetector, frame_luminance  # noqa: E402
from window_title import GameTracker             # noqa: E402

# 控制台按钮触发攻略时，截图前先等这么久，让主播有时间切回游戏画面（见 on_walkthrough）。
# 语音触发不受影响——那条路径不需要离开游戏。设 0 可关掉。
CONSOLE_SHOT_DELAY_S = float(os.environ.get('PET_WALKTHROUGH_SHOT_DELAY_S', 5))


def _load_dotenv(repo):
    """极简 .env 读取（KEY=VAL），把 API key 等注入环境。纯标准库。"""
    p = os.path.join(repo, '.env')
    if not os.path.exists(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


def _is_walkthrough(msg):
    t, d = msg.get('type'), msg.get('data', {})
    if msg.get('channel') == 'command' and t == 'do' and d.get('action') == 'walkthrough':
        return True
    if msg.get('channel') == 'perception' and t == 'audio.command' \
            and d.get('intent') in ('walkthrough', '卡关') and d.get('speaker_verified'):
        return True
    return False


def _walkthrough_note(msg):
    """主播自己补充的"卡在哪"。**这条信息只有他知道**——游戏名能自动认，但"第几关、
    我已经试过什么"认不出来，而它恰恰最能决定搜索质量。

    两个来源：控制台打字（`command.do` 带 note）、语音（用原话当描述）。
    """
    d = msg.get('data', {})
    if msg.get('channel') == 'command':
        return (d.get('note') or '').strip() or None
    raw = (d.get('raw_text') or '').strip()
    return raw or None


def run(region=None, port=8765, fps=10):
    import mss
    import numpy as np
    from mss.tools import to_png
    _load_dotenv(REPO)
    bus = BusClient(port=port, source='perception.game').connect()

    tracker = GameTracker()          # 持续跟踪前台窗口，攻略时才知道主播在玩什么

    def on_walkthrough(msg):
        if not _is_walkthrough(msg):
            return
        try:
            import vision
            # 从控制台按钮触发时先等几秒再截图。主播反馈（2026-08-02 真开播）：
            # "我必须要切到桌宠控制面板点那个卡关按钮，所以我点了之后很有可能后面截取的
            # 并不是我的游戏画面而是其他乱七八糟的" —— 点按钮那一刻前台就是控制台，
            # 立刻截屏截到的就是控制台自己。语音触发不用等（那时人还在游戏里）。
            if msg.get('source') == 'console' and CONSOLE_SHOT_DELAY_S > 0:
                print(f"[game] 控制台触发：{CONSOLE_SHOT_DELAY_S:.0f} 秒后截图，"
                      f"请切回游戏画面…", flush=True)
                time.sleep(CONSOLE_SHOT_DELAY_S)
            with mss.mss() as s2:
                shot = s2.grab(region or s2.monitors[1])
            png = to_png(shot.rgb, shot.size)
            note = _walkthrough_note(msg)
            # 主播自己说了游戏名就用他说的；没说才用前台窗口认出来的。
            # 他本人说的最可靠——窗口标题会遇到中英文名不一致、带一堆版本号、
            # 或者前台是启动器/模拟器根本不是游戏本体这些情况。
            import websearch
            said = websearch.extract_game(note)
            game = said or tracker.current()
            print(f"[game] 找攻略中… 游戏={game!r}（{'主播说的' if said else '窗口标题'}）"
                  f" 补充={note!r}")
            t0 = time.time()
            scene = vision.walkthrough(png, game=game, note=note, ts=int(time.time() * 1000))
            bus.publish(scene)
            srcs = scene['data'].get('sources', [])
            print(f"[game][耗时] 攻略 {time.time() - t0:.1f}s，引用来源 {len(srcs)} 条")
            for s in srcs[:3]:
                print(f"    · {s['title'][:50]}  {s['url'][:70]}")
            print(f"[game] 已发 game.scene: {scene['data'].get('hint', '')}")
        except Exception as e:
            print(f"[game] 攻略失败: {type(e).__name__}: {e}")

    bus.subscribe(on_walkthrough)

    det = ScareDetector()
    print(f"[game] 采集{'区域' if region else '主显示器'}，被吓检测中… ({fps}fps)；发 walkthrough 指令可触发攻略")
    with mss.mss() as sct:
        mon = region or sct.monitors[1]
        n = 0
        while True:
            img = np.asarray(sct.grab(mon))
            # 每秒认一次前台窗口。**必须持续跟踪而不是触发攻略时才查**——主播喊"帮我看看
            # 怎么过"的那一刻，前台多半已经切到控制台或者别处了，当场查只会查到我们自己。
            n += 1
            if n % max(1, fps) == 0:
                tracker.poll()
            inten = det.push(frame_luminance(img))
            if inten > 0:
                bus.publish({"channel": "perception", "type": "game.scare", "ts": int(time.time() * 1000),
                             "source": "perception.game", "data": {"intensity": inten}})
                print(f"  被吓! intensity={inten}")
            time.sleep(1.0 / fps)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--region', help='top,left,width,height（像素）')
    ap.add_argument('--fps', type=int, default=10)
    ap.add_argument('--port', type=int, default=8765)
    a = ap.parse_args()
    reg = None
    if a.region:
        t, l, w, h = (int(x) for x in a.region.split(','))
        reg = {"top": t, "left": l, "width": w, "height": h}
    run(region=reg, port=a.port, fps=a.fps)
