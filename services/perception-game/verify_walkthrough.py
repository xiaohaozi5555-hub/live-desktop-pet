#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡关攻略链路离线验证：认游戏名 + 查询词 + "只用真材料"这条铁律。

**纯离线**：不联网、不调模型、不需要 Windows 前台窗口（探测函数可注入）。
重点不是"能不能搜到"，而是**搜不到的时候会不会编**——2026-07-30 实测过，模型在没有真实材料时
会给出互相矛盾且极其自信的答案，还伪造过攻略站链接和原文引述。那是这条链路存在的全部理由。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import validate as contract        # noqa: E402
import vision                      # noqa: E402
import websearch                   # noqa: E402
import window_title as wt          # noqa: E402

passed = failed = 0


def check(name, ok, detail=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ''))


# ---- 1) 认游戏名 ----
check('桌宠自己不算游戏', not wt.looks_like_game('魔丸 · 控制台', 'electron.exe'))
check('直播伴侣不算游戏', not wt.looks_like_game('抖音直播伴侣', 'webcast_mate.exe'))
check('浏览器不算游戏（查攻略时会开）', not wt.looks_like_game('层层恐惧攻略 - 百度', 'chrome.exe'))
check('资源管理器不算游戏', not wt.looks_like_game('此电脑', 'explorer.exe'))
check('空标题不算游戏', not wt.looks_like_game('', 'game.exe'))
check('真游戏认得出', wt.looks_like_game('Layers of Fear', 'lof.exe'))

check('去掉 Steam 后缀', wt.clean_title('Layers of Fear - Steam') == 'Layers of Fear')
check('取分隔符前面那段', wt.clean_title('层层恐惧 - 存档3') == '层层恐惧')
check('分隔符前太短就不切', wt.clean_title('A - 很长的正式名字') == 'A - 很长的正式名字')

# 关键行为：触发攻略那一刻前台往往已经不是游戏了，必须记得住上一个
seq = [('层层恐惧', 'lof.exe'), ('魔丸 · 控制台', 'electron.exe'), ('此电脑', 'explorer.exe')]
box = {'i': 0}


def fake_probe():
    v = seq[min(box['i'], len(seq) - 1)]
    box['i'] += 1
    return v


tr = wt.GameTracker(probe=fake_probe)
tr.poll()
check('先认出游戏', tr.current() == '层层恐惧', str(tr.current()))
tr.poll(); tr.poll()
check('之后切到控制台/资源管理器，仍记得是哪个游戏（这条最关键）',
      tr.current() == '层层恐惧', str(tr.current()))

tr2 = wt.GameTracker(probe=lambda: ('魔丸 · 控制台', 'electron.exe'))
tr2.poll()
check('从头到尾没见过游戏 → None，不瞎猜', tr2.current() is None)

# ---- 2) 查询词 ----
q = websearch.build_query(game='层层恐惧', note='画室 拼画 碎片')
check('查询词带上游戏名和主播的补充', '层层恐惧' in q and '画室' in q, q)
check('查询词锚定成游戏（实测不加会搜出"层层"的词典条目）', '游戏' in q, q)
q2 = websearch.build_query(game=None, note=None, scene='昏暗的走廊')
check('什么都没有时退回用画面描述', '昏暗的走廊' in q2, q2)

# ---- 2b) 主播自己报的游戏名优先于窗口标题 ----
REAL = '我卡关了帮我找找攻略，游戏名是层层恐惧，我现在在这个房子里有个东西找不到，上一个任务是修好电闸'
check('从真实求助长句里认出游戏名', websearch.extract_game(REAL) == '层层恐惧',
      str(websearch.extract_game(REAL)))
check('书名号写法', websearch.extract_game('我在玩《生化危机8》卡住了') == '生化危机8')
check('"我在玩XXX"写法', websearch.extract_game('我在玩层层恐惧') == '层层恐惧')
check('没报游戏名就返回 None（交给窗口标题兜底）',
      websearch.extract_game('我卡在这个房间里出不去了') is None)
check('"我在玩游戏"这种废话不当成游戏名',
      websearch.extract_game('我在玩游戏呢') is None, str(websearch.extract_game('我在玩游戏呢')))

# ---- 3) 铁律：没有真材料就不许编 ----
scene = vision.make_scene('昏暗的画室，画布缺了几块', ['dark', 'puzzle'], True, None, 1)


def analyzer(_png, ts=0):
    return scene


def empty_search(game=None, note=None, scene=None):
    return {'query': 'q', 'results': [], 'pages': []}


def ok_search(game=None, note=None, scene=None):
    return {'query': 'q', 'pages': [],
            'results': [{'title': '层层恐惧攻略_逗游网', 'url': 'http://example/1',
                         'snippet': '第一张碎片在储物柜内'}]}


r_empty = vision.walkthrough(b'x', game='层层恐惧', note='卡住了', ts=1,
                             searcher=empty_search, analyzer=analyzer)
check('搜不到时如实说没搜到', '没搜到' in (r_empty['data'].get('hint') or ''),
      r_empty['data'].get('hint'))
check('⚠️ 搜不到时绝不给出任何来源（伪造出处是最危险的失败模式）',
      'sources' not in r_empty['data'])
check('搜不到时消息仍然合法', not contract.validate_message(r_empty))

_real_call = vision._text_call
vision._text_call = lambda prompt, max_tokens=500: f'[材料长度{len(prompt)}]答案'
try:
    r_ok = vision.walkthrough(b'x', game='层层恐惧', note='卡住了', ts=1,
                              searcher=ok_search, analyzer=analyzer)
finally:
    vision._text_call = _real_call
check('搜到时带回来源，主播能自己核对', len(r_ok['data'].get('sources', [])) == 1,
      str(r_ok['data'].get('sources')))
check('来源含标题和链接',
      r_ok['data']['sources'][0].get('url') == 'http://example/1')
check('搜到时消息合法', not contract.validate_message(r_ok))


def boom_call(prompt, max_tokens=500):
    raise RuntimeError('模型挂了')


vision._text_call = boom_call
try:
    r_err = vision.walkthrough(b'x', game='层层恐惧', note='卡住了', ts=1,
                               searcher=ok_search, analyzer=analyzer)
finally:
    vision._text_call = _real_call
check('总结失败也不抛异常、不卡死链路', bool(r_err['data'].get('hint')),
      r_err['data'].get('hint'))

print(f"\n==== 卡关攻略链路 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
