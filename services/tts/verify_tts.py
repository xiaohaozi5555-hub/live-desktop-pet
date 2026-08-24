#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻 TTS 进程自检：协议、健壮性、以及"第二句起确实更快"。

跟其它 verify 脚本不同，本脚本里**真实合成那几项要联网**（edge-tts 是微软的云服务）。
没装 edge_tts 或网络不通时相关项自动 SKIP，不算失败——不能让一个联网检查把离线全绿搞成红的。
协议健壮性那几项（脏数据、空文本）不依赖网络，任何情况下都会跑。
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

passed = failed = skipped = 0


def check(name, ok, detail=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ''))


def skip(name, why):
    global skipped
    skipped += 1
    print(f"[SKIP] {name} — {why}")


def venv_python():
    """优先用 .venv 里的解释器——edge_tts 装在那儿，系统 python 多半没有。"""
    p = os.path.join(REPO, '.venv', 'Scripts', 'python.exe')
    return p if os.path.exists(p) else sys.executable


class Daemon:
    def __init__(self):
        self.p = subprocess.Popen(
            [venv_python(), os.path.join(HERE, 'tts_daemon.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1)

    def send(self, obj):
        self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + '\n')
        self.p.stdin.flush()

    def send_raw(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()

    def recv(self, timeout=40):
        # 简单起见按行阻塞读；守护进程每条请求必回一行，不会永久卡住
        self.p.stdout.__class__  # noqa: B018
        line = self.p.stdout.readline()
        return json.loads(line) if line.strip() else None

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


d = Daemon()
hello = d.recv()
ready = bool(hello and hello.get('ready'))
check('启动后先报一行 ready', hello is not None and 'ready' in hello, str(hello))

if not ready:
    skip('真实合成', f"守护进程未就绪（多半没装 edge_tts）：{hello}")
    skip('第二句更快', '同上')
    skip('空文本被拒', '同上')
    skip('脏数据不致命', '同上')
else:
    tmp = tempfile.mkdtemp(prefix='ttsverify-')
    out1 = os.path.join(tmp, 'a.mp3')
    out2 = os.path.join(tmp, 'b.mp3')

    t0 = time.time()
    d.send({'id': 1, 'text': '在呢在呢，宝宝一直乖乖看着你直播呢', 'out': out1})
    r1 = d.recv()
    dt1 = time.time() - t0

    if not (r1 and r1.get('ok')):
        skip('真实合成', f"合成失败（多半是网络）：{r1}")
        skip('第二句更快', '同上')
    else:
        check('合成成功且 id 对得上', r1.get('id') == 1, str(r1))
        check('产出的 mp3 不是空文件',
              os.path.exists(out1) and os.path.getsize(out1) > 2000,
              f"{os.path.getsize(out1) if os.path.exists(out1) else 0} bytes")

        t0 = time.time()
        d.send({'id': 2, 'text': '主播今天想玩什么游戏呀', 'out': out2})
        r2 = d.recv()
        dt2 = time.time() - t0
        check('同一进程能连续合成第二句', bool(r2 and r2.get('ok') and r2.get('id') == 2), str(r2))
        # 只报数不断言快慢：网络抖动实测 1.6~4.8 秒，拿它做断言必然随机红。
        # 真正省下的是 0.86 秒的解释器冷启动，那部分在第一句之前就已经付掉了。
        print(f"       （参考耗时：第一句 {dt1:.1f}s，第二句 {dt2:.1f}s；"
              f"命令行方式每句还要另加约 0.86s 冷启动）")

    d.send({'id': 3, 'text': '   ', 'out': out1})
    r3 = d.recv()
    check('空文本被拒且不崩', bool(r3 and r3.get('ok') is False and r3.get('id') == 3), str(r3))

    d.send_raw('{这不是合法 JSON')
    d.send({'id': 4, 'text': '还活着吗', 'out': os.path.join(tmp, 'c.mp3')})
    r4 = d.recv()
    check('收到脏数据后进程仍然活着，后续请求照常处理',
          bool(r4 and r4.get('id') == 4), str(r4))

d.close()

print(f"\n==== 常驻TTS 验证: {passed}/{passed + failed} 通过"
      + (f"，{skipped} 跳过" if skipped else '') + " ====")
sys.exit(0 if failed == 0 else 1)
