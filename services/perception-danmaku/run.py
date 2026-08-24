#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""danmaku 运行器：把弹幕来源接到本地总线，发 perception.danmaku.* / command.stream_*。

两种模式：
  默认（--grab）：连 DouyinBarrageGrab 的本地 WebSocket，实时转发。**这是桌宠对弹幕
                  有反应的唯一数据来源**，由 apps/character 作为常驻服务拉起。
  --fixture     ：离线回放夹具，开发与回归用。

注意：本模块只负责"把数据送上总线"。判断桌宠反应对不对是「验证员」
（record_grab.py）的事，它另开一条连接旁观，不参与数据链路。"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
try:
    # ⚠️ line_buffering=True 不能省。这个服务的 stdout 是被 Electron 用管道接走的，
    # 不是终端——Python 对管道默认走全缓冲（4KB 才冲一次），于是 2026-08-02 那场真开播
    # **这个服务一整场一行日志都没出现在 startup.log 里**，连"已连上抓包程序"都没有，
    # 事后查断流原因时等于两眼一抹黑。逐行冲掉才能保证日志是实时可用的排查材料。
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
except Exception:
    pass
from bus_client import BusClient   # noqa: E402
import client as danmaku           # noqa: E402

# ---- 弹幕断流告警（2026-08-02 真开播后加）------------------------------------
# 那场开播 19 分钟后弹幕突然断流 58 分钟，而抓包程序进程还活着、我方 WebSocket 也没断，
# 主播全程不知情，一直到下播复盘才发现。根因在第三方抓包程序里（详见 CHANGELOG），我们
# 修不了它，但**至少不能让主播蒙在鼓里播完一整场**。
STALL_WARN_MS = int(os.environ.get('PET_DANMAKU_STALL_MS', 5 * 60_000))
STALL_REPEAT_MS = int(os.environ.get('PET_DANMAKU_STALL_REPEAT_MS', 10 * 60_000))
WATCHDOG_TICK_S = 15

# ---- 自动恢复 ----------------------------------------------------------------
# 光告警不够，主播要的是**恢复**。抓包程序的 bug 我们改不了（源码分析见 CHANGELOG），但
# 手里有一个"让它重新开始"的把手：**重启它**。
#
# 为什么重启能恢复：它是靠系统代理拦截直播伴侣的流量的，而 WebSocket 的拦截处理器**只在
# 连接建立那一刻挂一次**。断流是那条连接上的状态坏掉了。杀掉抓包程序 → 它的代理没了 →
# 直播伴侣那条走代理的连接被迫断开 → 直播伴侣重连 → 新连接被新实例重新拦截，状态全新。
#
# ⚠️ **这条推理还没有被真实开播验证过**，是基于源码得出的最合理路径。真开播时验证方式：
# 看断流告警之后是不是自动跟上一条"弹幕恢复了"。
GRAB_EXE = os.environ.get('PET_GRAB_EXE', r'D:\BarrageGrab\WssBarrageServer.exe')
AUTO_RECOVER = os.environ.get('PET_DANMAKU_AUTO_RECOVER', '1') != '0'
MAX_RECOVER_TRIES = int(os.environ.get('PET_DANMAKU_MAX_RECOVER', 3))
RECOVER_VERIFY_S = 25
CREATE_NEW_CONSOLE = 0x00000010


def _proc_alive(image_name):
    import subprocess
    try:
        out = subprocess.run(['tasklist', '/fi', f'imagename eq {image_name}'],
                             capture_output=True, text=True, timeout=15)
        return image_name.lower() in (out.stdout or '').lower()
    except Exception:
        return False


def _port_alive(host, port, timeout=1.0):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _restart_grabber(ws_host, ws_port):
    """重启抓包程序。返回 (成功?, 说明)。"""
    import subprocess
    if not os.path.exists(GRAB_EXE):
        return False, f'找不到抓包程序：{GRAB_EXE}'
    name = os.path.basename(GRAB_EXE)

    # ⚠️ **只温和关闭，绝不 /F 强杀。**
    # 它正常退出时会把系统代理抹平（ProxyEnable=0 + 清空），网络于是回到直连、一切正常；
    # 强杀会跳过这一步，让系统代理仍然指着一个已经没人监听的 8827 端口——**那等于整机断网**，
    # 现象跟宽带故障一模一样（CHANGELOG 里专门写过这个坑）。宁可这次恢复失败，也绝不能在
    # 直播途中把主播的网弄断。
    try:
        subprocess.run(['taskkill', '/IM', name], capture_output=True, timeout=20)
    except Exception as e:
        return False, f'关闭抓包程序失败：{type(e).__name__}: {e}'
    for _ in range(20):
        if not _proc_alive(name):
            break
        time.sleep(0.5)
    else:
        return False, '抓包程序没响应关闭请求；没有强杀它（强杀会把系统代理留在死端口上＝整机断网）'

    try:
        subprocess.Popen([GRAB_EXE], cwd=os.path.dirname(GRAB_EXE),
                         creationflags=CREATE_NEW_CONSOLE, close_fds=True)
    except Exception as e:
        return False, f'重新启动失败：{type(e).__name__}: {e}（系统代理已被它自己抹平，网络不受影响）'

    for _ in range(RECOVER_VERIFY_S * 2):
        if _port_alive(ws_host, ws_port):
            return True, ''
        time.sleep(0.5)
    return False, f'重启后 {RECOVER_VERIFY_S}s 内 {ws_port} 端口没起来'


def _health(ok, silent_ms, connected, recovery=None):
    data = {'ok': bool(ok), 'silent_ms': int(silent_ms), 'connected': bool(connected)}
    if recovery:
        data['recovery'] = recovery      # 'trying' / 'ok' / 'failed:<原因>' / 'gaveup'
    return {'channel': 'perception', 'type': 'danmaku.health', 'ts': int(time.time() * 1000),
            'source': 'perception.danmaku', 'data': data}


def _watchdog(bus, state, ws_host, ws_port):
    """只看"多久没收到包"，不碰数据链路本身。断流就先告警、再自动重启抓包程序把它救回来；
    恢复时补一条 ok:true——不然主播看到一次告警之后永远不知道到底恢复没有。"""
    warned_at = None
    tries = 0

    def say(msg):
        print(f"[danmaku] {msg}", flush=True)

    def publish(m):
        try:
            bus.publish(m)
        except Exception:
            pass

    while True:
        time.sleep(WATCHDOG_TICK_S)
        now = int(time.time() * 1000)
        # 一条都没收到过时，用"连上抓包程序的时刻"当基线：08-01 有过整场 0 条的情况，
        # 那种故障同样要能报出来，不能因为"从来没收到过"就永远不吭声。
        base = state.get('last_pack_ms') or state.get('connected_at')
        if not base:
            continue
        silent = now - base
        connected = bool(state.get('connected'))

        if silent >= STALL_WARN_MS:
            if warned_at is not None and now - warned_at < STALL_REPEAT_MS:
                continue
            never = state.get('last_pack_ms') is None
            say(f"⚠ {'连上之后一条弹幕都没收到' if never else '弹幕断流'}："
                f"已静默 {silent / 60000:.1f} 分钟，到抓包程序的连接{'还在' if connected else '已断'}")
            warned_at = now

            if not AUTO_RECOVER:
                publish(_health(False, silent, connected))
                continue
            if tries >= MAX_RECOVER_TRIES:
                say(f"已经自动重启过 {tries} 次仍然没救回来，不再重试——继续重启只会反复打断"
                    f"直播伴侣的连接。请手动看一眼那个黑窗口。")
                publish(_health(False, silent, connected, recovery='gaveup'))
                continue

            tries += 1
            say(f"正在自动重启抓包程序把弹幕救回来（第 {tries}/{MAX_RECOVER_TRIES} 次）…")
            publish(_health(False, silent, connected, recovery='trying'))
            ok, why = _restart_grabber(ws_host, ws_port)
            if ok:
                # 端口起来了只代表"程序活了"，**不代表弹幕真的回来了**——真回来要等下一条
                # 包到达，那时循环下面那个分支会自己发 ok:true。这里不谎报成功。
                say("抓包程序已重启，等直播伴侣重连…（真的收到弹幕才算恢复）")
                publish(_health(False, silent, connected, recovery='ok'))
                state['connected_at'] = int(time.time() * 1000)
            else:
                say(f"自动重启没成功：{why}")
                publish(_health(False, silent, connected, recovery=f'failed:{why}'))

        elif warned_at is not None:
            say(f"✔ 弹幕恢复了（断了 {silent / 60000:.1f} 分钟）")
            publish(_health(True, silent, connected))
            warned_at = None
            tries = 0        # 真恢复了就把重试次数清零，下次再断还有完整的三次机会


def replay(host='127.0.0.1', port=8765, fixture=None, delay=0.8):
    fixture = fixture or os.path.join(REPO, 'fixtures', 'danmaku', 'sample-official.jsonl')
    bus = BusClient(host, port, source='perception.danmaku').connect()
    print(f"[danmaku] 回放 {os.path.basename(fixture)} → 总线 …")
    danmaku.feed_fixture(fixture, lambda m: (bus.publish(m), time.sleep(delay)))
    print("[danmaku] 回放结束")


def run_live(host='127.0.0.1', port=8765, ws_host='127.0.0.1', ws_port=8888, auto_tier=None):
    """auto_tier=None 时读环境变量 PET_AUTO_TIER，**默认关**——会员/星守护与粉丝团等级
    无关，映射还没搞清楚，宁可不分级也不要把错档位写进观众画像库。见 autotier.py 顶部。"""
    from autotier import AutoTier
    if auto_tier is None:
        auto_tier = os.environ.get('PET_AUTO_TIER') == '1'
    bus = BusClient(host, port, source='perception.danmaku').connect()
    auto = AutoTier(bus.publish, on_log=print) if auto_tier else None
    print(f"[danmaku] 已连总线 {host}:{port}，等待抓包程序 ws://{ws_host}:{ws_port} …")
    if auto:
        print(f"[danmaku] ⚠ 自动分级已开（实验性，映射未确认）：粉丝团 ≥{auto.member_level}=会员，"
              f"≥{auto.star_level}=星守护；主播手动打的标不会被覆盖")
    else:
        print("[danmaku] 自动分级关闭（默认）：分级仍由主播在面板手动打标")

    # 断流告警用的共享状态。on_raw 拿的是**归一化之前**的原始包，所以连"统计/分享"这类
    # 我们最终会丢弃的类型也算数——判断"链路还有没有在动"就该用最原始的信号。
    state = {'last_pack_ms': None, 'connected': False, 'connected_at': None}

    def on_raw(pack):
        state['last_pack_ms'] = int(time.time() * 1000)
        if auto:
            auto.feed(pack)

    def on_log(msg):
        text = str(msg)
        if '已连上抓包程序' in text:
            state['connected'] = True
            state['connected_at'] = int(time.time() * 1000)
        elif '连接断开' in text:
            state['connected'] = False
        print(text, flush=True)

    threading.Thread(target=_watchdog, args=(bus, state, ws_host, ws_port), daemon=True).start()
    print(f"[danmaku] 断流看护已开：超过 {STALL_WARN_MS / 60000:.0f} 分钟没弹幕会告警"
          + (f"并自动重启抓包程序（最多 {MAX_RECOVER_TRIES} 次）" if AUTO_RECOVER else "（自动恢复已关）"),
          flush=True)
    danmaku.GrabClient(ws_host, ws_port, on_event=bus.publish,
                       on_raw=on_raw, on_log=on_log).run_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--fixture', help='离线回放这个夹具，而不是连实时来源')
    ap.add_argument('--delay', type=float, default=0.8)
    ap.add_argument('--port', type=int, default=8765, help='总线端口')
    ap.add_argument('--ws-port', type=int, default=8888, help='抓包程序的 WebSocket 端口')
    ap.add_argument('--auto-tier', action='store_true', help='打开自动分级（实验性，映射未确认，默认关）')
    a = ap.parse_args()
    if a.fixture:
        replay(port=a.port, fixture=a.fixture, delay=a.delay)
    else:
        run_live(port=a.port, ws_port=a.ws_port, auto_tier=True if a.auto_tier else None)
