#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制面板：连本地总线，发 command.*（面板/关键词是"只认主播"之外的两条控制通道）。

  CLI :  python panel.py            终端输入指令（见运行时提示，覆盖 CLI_MAP 全部指令）
  GUI :  python panel.py --gui      Tkinter 按钮面板（桌面用；无 tkinter 自动退回 CLI）
  关键词桥: python panel.py --keywords   订阅弹幕 chat，命中关键词 → 发 command
            （只有 keywords.STREAMER_NAMES / MODERATOR_NAMES 白名单内的弹幕昵称才会生效，
             见 keywords.py 顶部说明；本机 CLI/GUI 输入不受此限制，视为可信输入）
"""
import os
import queue
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
from bus_client import BusClient   # noqa: E402
import commands as C               # noqa: E402
import keywords as K               # noqa: E402
import validate as contract        # noqa: E402

CLI_MAP = {
    'mute': C.mute, 'unmute': C.unmute, 'sleep': C.sleep, 'wake': C.wake,
    'wave': lambda: C.do('wave'), 'scared': lambda: C.do('scared'), 'laugh': lambda: C.do('laugh'),
    'thank': lambda: C.do('thank_big'), 'thank_small': lambda: C.do('thank_small'),
    'beg': lambda: C.do('beg'), 'praise': lambda: C.do('praise'),
}

# GUI 按钮分组：(分组标题, 每行列数, [(按钮文字, 取 command 的函数), ...])
# 覆盖 action-map.js 里全部 9 种 motion：wave/scared/thank_small/thank_big/laugh/praise/beg 由
# "动作"区直接触发（do）；idle/sleep 两种由"状态"区的 唤醒/休息 触发——它们本就是这两个按钮的
# 动画效果，额外加 do:idle / do:sleep 按钮只会是同画面的重复，还容易跟状态切换搞混，故不重复加。
GUI_GROUPS = [
    ("状态", 2, [
        ("闭嘴", C.mute), ("恢复", C.unmute),
        ("休息", C.sleep), ("唤醒", C.wake),
    ]),
    ("动作", 3, [
        ("挥手", lambda: C.do('wave')), ("被吓", lambda: C.do('scared')), ("大笑", lambda: C.do('laugh')),
        ("谢礼", lambda: C.do('thank_small')), ("答谢", lambda: C.do('thank_big')), ("夸夸", lambda: C.do('praise')),
        ("撒娇", lambda: C.do('beg')),
    ]),
    ("直播", 2, [
        ("开播", C.stream_start), ("下播", C.stream_end),
    ]),
]

# 观众分级：中文标签(界面显示) <-> 契约 tier 值(发布用)，顺序即下拉选项顺序。
TIER_OPTIONS = [("普通", "normal"), ("会员", "member"), ("星守护", "star_guardian")]
TIER_LABEL_TO_CODE = dict(TIER_OPTIONS)
TIER_LABELS = [label for label, _ in TIER_OPTIONS]

VOICE_RUN_PY = os.path.join(REPO, 'services', 'perception-voice', 'run.py')


def _send(bus, cmd):
    if cmd and not contract.validate_message(cmd):
        bus.publish(cmd)
        return True
    return False


def run_cli(port=8765):
    bus = BusClient(port=port, source='control.panel').connect()
    print("控制面板(CLI)。指令: " + " / ".join(CLI_MAP) + " | 中文关键词 | quit")
    for line in sys.stdin:
        line = line.strip()
        if line in ('quit', 'exit'):
            break
        if not line:
            continue
        if line in CLI_MAP:
            cmd = CLI_MAP[line]()
        else:
            cmd = K.match(line)     # 也接受中文关键词
        print(f"  -> {cmd['type']} {cmd['data']}" if _send(bus, cmd) else "  (无效指令)")


def run_keyword_bridge(port=8765):
    bus = BusClient(port=port, source='control.keywords').connect()

    def on(m):
        if m.get('type') == 'danmaku.chat':
            d = m.get('data', {})
            cmd = K.match_danmaku(d.get('text', ''), d.get('user'))   # 弹幕是不可信输入，走白名单校验
            if cmd:
                _send(bus, cmd)
    bus.subscribe(on)
    whitelist = K.STREAMER_NAMES + K.MODERATOR_NAMES
    print(f"关键词桥已启动，监听弹幕 chat…（白名单昵称: {whitelist or '（空，未配置则无人能触发）'}）")
    threading.Event().wait()


def _build_voice_panel(root, row, bus):
    """语音识别目前只是"壳子"：负责起停 services/perception-voice/run.py 子进程，把总线上的
    perception.audio.command 显示出来。run.py 的实时麦克风循环本身还没实现（一启动就会退出并报
    NotImplementedError），本 worktree 也还没有声纹注册文件，所以现在点"开启"大概率立刻变回
    "已停止"——这是预期状态；等 run.py 补完实时循环、重新 enroll 声纹后，这个壳子不用改就能用。
    """
    import tkinter as tk
    frame = tk.LabelFrame(root, text="语音识别（实时监听，需已注册声纹）", padx=3, pady=3)
    frame.grid(row=row, column=0, sticky='we', padx=4, pady=4)

    status = tk.StringVar(value="未开启")
    heard = tk.StringVar(value="（还没听到过）")
    state = {'proc': None}
    heard_q = queue.Queue()

    def start():
        if state['proc'] is not None:
            return
        try:
            proc = subprocess.Popen(
                [sys.executable, VOICE_RUN_PY],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace',
                cwd=os.path.dirname(VOICE_RUN_PY),
            )
        except Exception as e:
            status.set(f"启动失败：{e}")
            return
        state['proc'] = proc
        toggle_btn.config(text="关闭语音识别")
        status.set("启动中…")

    def stop():
        proc = state['proc']
        state['proc'] = None
        if proc is not None:
            proc.terminate()
        toggle_btn.config(text="开启语音识别")
        status.set("未开启")

    def toggle():
        stop() if state['proc'] is not None else start()

    def poll():
        proc = state['proc']
        if proc is not None and proc.poll() is not None:      # 子进程已退出（目前预期会退出）
            tail = ''
            try:
                lines = (proc.stderr.read() or '').strip().splitlines()
                tail = lines[-1] if lines else ''
            except Exception:
                pass
            state['proc'] = None
            toggle_btn.config(text="开启语音识别")
            status.set(f"已停止：{tail[:44]}" if tail else "已停止")
        while not heard_q.empty():
            heard.set(heard_q.get_nowait())
        root.after(300, poll)

    def on_audio_command(m):    # 跑在 BusClient 的接收线程里，只丢进队列，真正更新交给上面 poll()（主线程）
        if m.get('channel') == 'perception' and m.get('type') == 'audio.command':
            d = m.get('data', {})
            mark = '✓' if d.get('speaker_verified') else '✗未验证声纹（已丢弃）'
            heard_q.put(f"「{d.get('raw_text', '')}」→ {d.get('intent')} {mark}")
    bus.subscribe(on_audio_command)

    toggle_btn = tk.Button(frame, text="开启语音识别", width=14, height=2, command=toggle)
    toggle_btn.grid(row=0, column=0, padx=2, pady=2)
    tk.Label(frame, textvariable=status, anchor='w', width=24).grid(row=0, column=1, padx=2, pady=2, sticky='w')
    tk.Label(frame, text="最近识别:", anchor='w').grid(row=1, column=0, sticky='w', padx=2)
    tk.Label(frame, textvariable=heard, anchor='w', wraplength=220, justify='left').grid(
        row=1, column=1, sticky='w', padx=2, pady=(0, 4))

    root.protocol("WM_DELETE_WINDOW", lambda: (stop(), root.destroy()))
    root.after(300, poll)


def _build_viewer_panel(root, row, bus):
    """观众分级：昵称从弹幕事件（enter/chat/gift/follow/like 的 data.user）自动收集去重，
    不用主播手打字。改分级只发 command.set_viewer_tier，数据存哪、怎么用交给 dialogue 服务
    订阅处理——这边不碰、也不关心任何数据库文件。
    """
    import tkinter as tk
    from tkinter import ttk
    frame = tk.LabelFrame(root, text="观众分级（本场已出现，自动收集）", padx=3, pady=3)
    frame.grid(row=row, column=0, sticky='we', padx=4, pady=4)

    canvas = tk.Canvas(frame, height=130, width=252, highlightthickness=0)
    vsb = tk.Scrollbar(frame, orient='vertical', command=canvas.yview)
    inner = tk.Frame(canvas)
    inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=inner, anchor='nw')
    canvas.configure(yscrollcommand=vsb.set)
    canvas.grid(row=0, column=0, sticky='we')
    vsb.grid(row=0, column=1, sticky='ns')

    def _on_wheel(e):    # 只在鼠标悬停在列表上时接管滚轮，离开就还给整个窗口
        canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')
    canvas.bind('<Enter>', lambda e: canvas.bind_all('<MouseWheel>', _on_wheel))
    canvas.bind('<Leave>', lambda e: canvas.unbind_all('<MouseWheel>'))

    placeholder = tk.Label(inner, text="（还没有观众进来）", fg="#888888")
    placeholder.grid(row=0, column=0, columnspan=2, sticky='w', padx=2, pady=4)

    seen = set()
    nick_q = queue.Queue()

    def add_row(nickname):
        if nickname in seen:
            return
        if not seen:
            placeholder.grid_forget()
        seen.add(nickname)
        r = len(seen) - 1
        shown = nickname if len(nickname) <= 12 else nickname[:11] + '…'
        tk.Label(inner, text=shown, anchor='w', width=13).grid(row=r, column=0, sticky='w', padx=2, pady=1)
        cb = ttk.Combobox(inner, values=TIER_LABELS, width=7, state='readonly')
        cb.set(TIER_LABELS[0])
        cb.grid(row=r, column=1, padx=2, pady=1)

        def on_pick(_e, nickname=nickname, cb=cb):
            _send(bus, C.set_viewer_tier(nickname, TIER_LABEL_TO_CODE[cb.get()]))
        cb.bind('<<ComboboxSelected>>', on_pick)

    def poll():
        while not nick_q.empty():
            add_row(nick_q.get_nowait())
        root.after(300, poll)

    def on_danmaku(m):    # 跑在 BusClient 接收线程里，只丢队列，真正建行交给 poll()（主线程）
        if m.get('channel') == 'perception' and str(m.get('type', '')).startswith('danmaku.'):
            user = (m.get('data') or {}).get('user')
            if user:
                nick_q.put(user)
    bus.subscribe(on_danmaku)

    root.after(300, poll)


def run_gui(port=8765):
    try:
        import tkinter as tk
    except Exception as e:
        print("无 tkinter，退回 CLI：", e)
        return run_cli(port)
    bus = BusClient(port=port, source='control.panel').connect()
    root = tk.Tk()
    root.title("桌宠控制面板")
    root.attributes('-topmost', True)
    root.resizable(False, False)
    for gi, (title, cols, btns) in enumerate(GUI_GROUPS):
        frame = tk.LabelFrame(root, text=title, padx=3, pady=3)
        frame.grid(row=gi, column=0, sticky='we', padx=4, pady=4)
        for i, (label, fn) in enumerate(btns):
            tk.Button(frame, text=label, width=9, height=2,
                      command=lambda fn=fn: _send(bus, fn())).grid(row=i // cols, column=i % cols, padx=2, pady=2)
    _build_voice_panel(root, len(GUI_GROUPS), bus)
    _build_viewer_panel(root, len(GUI_GROUPS) + 1, bus)
    root.mainloop()


if __name__ == '__main__':
    if '--gui' in sys.argv:
        run_gui()
    elif '--keywords' in sys.argv:
        run_keyword_bridge()
    else:
        run_cli()
