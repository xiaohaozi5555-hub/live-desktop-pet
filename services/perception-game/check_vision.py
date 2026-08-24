#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视觉后端连通性自检：读 .env → 试调配置的模型 → 把常见错误翻译成人话。
运行: python services/perception-game/check_vision.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _load_env():
    p = os.path.join(REPO, '.env')
    if os.path.exists(p):
        for line in open(p, encoding='utf-8'):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    _load_env()
    base = os.environ.get('PET_VISION_BASE_URL')
    key = os.environ.get('PET_VISION_API_KEY')
    model = os.environ.get('PET_VISION_MODEL', 'qwen-vl-plus')
    if not (base and key):
        print('未配置 PET_VISION_BASE_URL / PET_VISION_API_KEY（见 .env.example）'); return 2
    from openai import OpenAI
    try:
        c = OpenAI(api_key=key, base_url=base)
        r = c.chat.completions.create(model=model, messages=[{'role': 'user', 'content': '回一个字'}], max_tokens=5)
        print(f'[OK] {model} 可用 -> {r.choices[0].message.content!r}')
        return 0
    except Exception as e:
        s = str(e)
        print(f'[FAIL] {model}: {s[:200]}')
        if 'API-Key restrictions' in s or 'access_denied' in s:
            print('  → 这个 DashScope key 配了访问限制。去百炼控制台「API-KEY 管理」：删掉 IP 白名单/模型限制，'
                  '或直接新建一个不限制的 key。')
        elif '401' in s or 'invalid_api_key' in s.lower() or 'invalid' in s.lower():
            print('  → key 无效，检查是否复制完整/是否 DashScope 的 key。')
        elif 'model' in s.lower() and ('not' in s.lower() or '无权' in s or 'permission' in s.lower()):
            print('  → 该模型未开通/无权限。去百炼「模型广场」开通，或换 qwen-vl-plus。')
        elif 'insufficient' in s.lower() or 'quota' in s.lower() or 'balance' in s.lower():
            print('  → 额度/余额不足。检查免费额度是否用尽或需充值。')
        return 1


if __name__ == '__main__':
    sys.exit(main())
