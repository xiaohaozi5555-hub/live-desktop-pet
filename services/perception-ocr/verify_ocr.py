#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""插件 OCR·解析部分 验证：OCR 文本 → parse → plugin.state → brain 提醒。
纯标准库、无需装 OCR、无需真实截图。运行: python verify_ocr.py  退出码 0=全过。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'brain'))
sys.path.insert(0, os.path.join(REPO, 'packages', 'contract'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
from parse import parse_plugin_text, make_state   # noqa: E402
from brain import Brain                            # noqa: E402
import validate as contract                        # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


# 1) 夹具文本解析
fx = json.load(open(os.path.join(REPO, 'fixtures', 'plugin', 'sample-plugin-state.json'), encoding='utf-8'))
parsed = parse_plugin_text(fx['raw_ocr'])
check('夹具解析 countdown_sec=1800', parsed.get('countdown_sec') == 1800, str(parsed))
check('夹具解析 prank_count=7', parsed.get('prank_count') == 7)
check('解析结果与夹具 parsed 一致', parsed == fx['parsed'], f"{parsed} vs {fx['parsed']}")

# 2) 其它文本形态
check('“距离下播 00:05:00” → 300', parse_plugin_text('距离下播 00:05:00').get('countdown_sec') == 300)
check('“被整蛊次数：12” → 12', parse_plugin_text('被整蛊次数：12').get('prank_count') == 12)
check('无关文本 → 空', parse_plugin_text('主播好帅') == {})

# 3) plugin.state 契约合法 + 端到端 brain 提醒（5 分钟里程碑）
st = make_state({'countdown_sec': 300, 'prank_count': 7}, ts=1)
check('plugin.state 通过契约校验', not contract.validate_message(st), st['type'])
acts = Brain().handle(st)
check('剩 5 分钟 → brain 发提醒气泡', any(a['type'] == 'show_bubble' and '分钟' in a['data'].get('text', '') for a in acts), str([a['type'] for a in acts]))

print(f"\n==== 插件 OCR·解析 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
