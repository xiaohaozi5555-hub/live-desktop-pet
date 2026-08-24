#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻 edge-tts 合成进程：读 stdin 的 JSON 行，合成一段音频，写 stdout 一行结果。

**为什么要常驻**（2026-07-29 实测，别凭感觉改回去）：
用 `edge-tts` 命令行每合成一句要 2.6~4.8 秒，拆开来看是两部分——

- **约 0.86 秒**：python 解释器冷启动 + `import edge_tts`。每句都要重付一次，纯浪费。
- **约 2 秒**：到微软语音服务的一次网络往返。这部分砍不掉，而且实测**跟文本长短几乎无关**
  （4 字 3.06s / 36 字 2.78s，短的反而更慢），所以"把回复拆成短句先说第一句"是没用的，
  拆几句就要重付几次这 2 秒。

常驻进程消掉的就是上面第一项。剩下那 2 秒要再压只能换本地 TTS 引擎（见 CHANGELOG 待办 5a）。

**协议**（每行一个 JSON，靠 id 配对，允许乱序返回）：
    进 ← {"id": 1, "text": "在呢在呢", "voice": "zh-CN-XiaoyiNeural", "out": "C:\\...\\a.mp3"}
    出 → {"id": 1, "ok": true}
       → {"id": 1, "ok": false, "error": "..."}
启动就绪后先发一行 {"ready": true}，调用方据此判断可以开始派活。

调用方（`apps/character/main.js`）在本进程起不来或出错时会自动退回"每次现起一个 edge-tts"，
所以这里任何异常都只影响单次合成的速度，不会让桌宠彻底说不出话。
"""
import asyncio
import json
import sys

DEFAULT_VOICE = 'zh-CN-XiaoyiNeural'


def _reply(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _synth(text, voice, out):
    """每次现建一个事件循环。建循环是毫秒级的，真正贵的解释器启动和 import 已经在外面付过了。"""
    asyncio.run(edge_tts.Communicate(text, voice or DEFAULT_VOICE).save(out))


def main():
    # ⚠️ stdin 也必须显式转 utf-8。Windows 上它默认走 ANSI 代码页(cp936)，中文文本读进来会
    # 变成 '\udc80' 这类代理字符，合成时报 UnicodeEncodeError——看起来像网络错误，其实是编码。
    try:
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

    global edge_tts
    try:
        import edge_tts as _mod
        edge_tts = _mod
    except Exception as e:                       # noqa: BLE001
        _reply({'ready': False, 'error': f'{type(e).__name__}: {e}'})
        return 1

    _reply({'ready': True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue                             # 半截行/脏数据：跳过，不要把常驻进程搞死
        rid = req.get('id')
        text = (req.get('text') or '').strip()
        out = req.get('out')
        if not text or not out:
            _reply({'id': rid, 'ok': False, 'error': 'text/out 不能为空'})
            continue
        try:
            _synth(text, req.get('voice'), out)
            _reply({'id': rid, 'ok': True})
        except Exception as e:                   # noqa: BLE001
            # 单次失败不退出：网络抖一下不该让后面每句都退回慢路径
            _reply({'id': rid, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
    return 0


if __name__ == '__main__':
    sys.exit(main())
