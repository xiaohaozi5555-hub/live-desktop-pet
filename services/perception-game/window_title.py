#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""认出"主播正在玩什么游戏"——读 Windows 前台窗口的标题和进程名。纯 ctypes，不加依赖。

**为什么需要**：卡关攻略要联网搜，而搜索词的质量直接决定搜到什么。实测直接搜「层层恐惧 攻略」
返回的是"层层"这个词的汉语词典和百科条目；必须带上准确的游戏名才有用。让主播每次开口报游戏名
不现实，所以自动认。

**核心难点不是"读前台窗口"，是"读哪一刻的前台窗口"**：主播触发攻略的那一瞬间，前台多半已经
不是游戏了——他可能刚 alt-tab 出来点控制台、或者正对着麦克风说话。所以这里持续跟踪，记住
**最近一个"看起来像游戏"的前台窗口**，而不是当场抓一次。

判断"像游戏"用的是排除法：把我们自己的窗口、直播伴侣、系统外壳、浏览器这些明显不是游戏的排掉，
剩下的就认。宁可偶尔认错成别的应用（大不了搜出来的攻略不对），也不要因为规则太严而认不出游戏。
"""
import os
import sys
import time

# 明确不是游戏的进程。全小写比较。
EXCLUDE_EXE = {
    'explorer.exe', 'searchhost.exe', 'shellexperiencehost.exe', 'startmenuexperiencehost.exe',
    'textinputhost.exe', 'applicationframehost.exe', 'dwm.exe', 'lockapp.exe',
    'electron.exe',                                   # 桌宠自己和控制台
    'webcast_mate.exe', 'douyin.exe', 'living.exe',   # 直播伴侣一族
    'chrome.exe', 'msedge.exe', 'firefox.exe',        # 浏览器（查攻略时会开，别当成游戏）
    'code.exe', 'windowsterminal.exe', 'cmd.exe', 'powershell.exe', 'python.exe',
    'obs64.exe', 'obs32.exe',
}

# 标题里出现这些词就不当成游戏（进程名兜不住的情况，比如直播伴侣改了 exe 名）
EXCLUDE_TITLE = ('直播伴侣', '魔丸', '控制台', '任务管理器', '设置', 'Program Manager')

# 标题里常见的噪声后缀，搜索前去掉能让查询词干净很多
_TITLE_NOISE = (
    ' - Steam', ' on Steam', ' - DirectX 11', ' - DirectX 12', ' - Vulkan',
    ' (DX11)', ' (DX12)', ' [DX11]', ' [DX12]', ' - 64-bit', ' (64-bit)',
)


def _win_probe():
    """真实探测：返回 (窗口标题, 进程名)。非 Windows 或调用失败返回 (None, None)。"""
    if not sys.platform.startswith('win'):
        return (None, None)
    try:
        import ctypes
        from ctypes import wintypes
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32

        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return (None, None)

        n = u32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value or ''

        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ''
        # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION：比 QUERY_INFORMATION 权限低，
        # 对以管理员身份运行的游戏也能查到名字，普通权限就够。
        h = k32.OpenProcess(0x1000, False, pid.value)
        if h:
            try:
                size = wintypes.DWORD(260)
                pbuf = ctypes.create_unicode_buffer(size.value)
                if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                    exe = os.path.basename(pbuf.value)
            finally:
                k32.CloseHandle(h)
        return (title, exe)
    except Exception:
        return (None, None)


def clean_title(title):
    """把窗口标题收拾成适合当搜索词的游戏名。"""
    t = (title or '').strip()
    for noise in _TITLE_NOISE:
        if t.endswith(noise):
            t = t[: -len(noise)].strip()
    # 「游戏名 - 存档3」「游戏名 | v1.2」这类，取分隔符前面那段（通常才是游戏名）
    for sep in (' - ', ' | ', ' — '):
        if sep in t:
            head = t.split(sep)[0].strip()
            if len(head) >= 2:            # 太短说明分隔符前面不是游戏名，别切
                t = head
                break
    return t


def looks_like_game(title, exe):
    """排除法：明显不是游戏的排掉，剩下的都当候选。"""
    if not title or not title.strip():
        return False
    if exe and exe.lower() in EXCLUDE_EXE:
        return False
    if any(k in title for k in EXCLUDE_TITLE):
        return False
    return True


class GameTracker:
    """持续跟踪前台窗口，记住最近一个像游戏的。

    probe 可注入，方便离线测试（真实实现见 `_win_probe`）。
    """

    def __init__(self, probe=None, clock=time.time):
        self._probe = probe or _win_probe
        self._clock = clock
        self._name = None
        self._exe = None
        self._seen_at = 0.0

    def poll(self):
        """采一次前台窗口。像游戏就记下来，不像就保持上一次的记录不动。"""
        title, exe = self._probe()
        if looks_like_game(title, exe):
            name = clean_title(title)
            if name:
                self._name, self._exe, self._seen_at = name, exe, self._clock()
        return self._name

    def current(self):
        """当前认定的游戏名（可能是几分钟前记下的）。没有则 None。"""
        return self._name

    def info(self):
        return {'game': self._name, 'exe': self._exe, 'seen_ago': (self._clock() - self._seen_at) if self._name else None}


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    t = GameTracker()
    print('每秒采一次前台窗口，Ctrl+C 结束。切到游戏里看看认得对不对：')
    try:
        while True:
            t.poll()
            raw_title, raw_exe = _win_probe()
            print(f"  前台=[{raw_exe}] {raw_title!r:50.50}  ->  认定游戏: {t.current()!r}")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
