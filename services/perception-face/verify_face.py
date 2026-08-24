#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M6 露脸表情验证（自证逻辑，无需 DeepFace/露脸图）：表情归一化 + 端到端 brain 反应。
真实 DeepFace 识别需装 deepface + 你的露脸截图，本脚本仅验证契约映射与接线。"""
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
from expression import to_expression   # noqa: E402
from brain import Brain                # noqa: E402
import validate as contract            # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


# 1) 归一化映射 + 契约
for emo, exp in [('happy', 'happy'), ('surprise', 'surprise'), ('FEAR', 'fear'), ('unknown', 'neutral')]:
    m = to_expression(emo, 0.9, 'portrait', ts=1)
    check(f'{emo} → face.expression {exp} 且契约合法', m['data']['label'] == exp and not contract.validate_message(m))

# 2) 布局字段（竖屏/横屏）
check('layout=landscape 透传', to_expression('happy', 0.8, 'landscape')['data']['layout'] == 'landscape')

# 3) 端到端：看表情做调侃/夸夸
fear_acts = Brain().handle(to_expression('fear', 0.9, 'portrait', ts=1000))
check('露脸 fear → brain 调侃(smug 表情 + 说话)',
      any(a['type'] == 'set_expression' and a['data'].get('expression') == 'smug' for a in fear_acts)
      and any(a['type'] == 'speak' for a in fear_acts), str([a['type'] for a in fear_acts]))
happy_acts = Brain().handle(to_expression('happy', 0.9, 'portrait', ts=2000))
check('露脸 happy → brain 夸夸(说话)',
      any(a['type'] == 'speak' and ('灿烂' in a['data'].get('text', '') or '状态' in a['data'].get('text', '')) for a in happy_acts),
      str([a['type'] for a in happy_acts]))

# 4) 真实 DeepFace：已用用户 3 张真实露脸照片验证过（见 SPEC.md / process.md），
#    照片处理完即删除(隐私)，故此处仅做依赖存在性提示，不重跑真实识别。
try:
    import deepface  # noqa: F401
    print("[NOTE] deepface 已装；真实识别已用用户3张照片端到端验证(结果见 SPEC.md)，此处不重跑。")
except Exception:
    print("[SKIP] 未装 deepface（TensorFlow 较重）——真实表情识别待装 + 你的露脸截图；归一化与接线已自证。")

print(f"\n==== 露脸表情 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
