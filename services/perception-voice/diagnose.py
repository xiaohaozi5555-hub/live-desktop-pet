#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语音链路诊断：录一段真实说话，把每一步的原始数字打出来，定位"喊了没反应"卡在哪。

run.py 在真实运行时任何一步不过都是静悄悄跳过（这是对的，不然直播时满屏日志），
但排查时就看不见原因。这个脚本把同一条管线拆开跑，**不做任何过滤**，每段都打印：
  切到几段、每段多长、声纹相似度是多少（对比阈值）、ASR 听成了什么、意图会是什么。

用法：  python services/perception-voice/diagnose.py [录音秒数]
然后正常说话，比如"魔丸你好呀，今天直播开心吗"。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, 'services', 'bus'))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _load_dotenv(repo):
    p = os.path.join(repo, '.env')
    if not os.path.exists(p):
        return
    for line in open(p, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv(REPO)
import intents          # noqa: E402  必须在 _load_dotenv 之后
import run as voicerun  # noqa: E402  复用同一套 VAD 参数，避免诊断和实跑不一致


def main(seconds=12.0):
    import numpy as np
    import sounddevice as sd
    import webrtcvad
    import asr
    import speaker

    print("=" * 62)
    device = voicerun.pick_device(sd)          # 跟 run.py 用同一套选设备逻辑
    dev = sd.query_devices(device)
    print(f"输入设备      : {dev['name']}")
    print(f"声纹文件      : {'在' if os.path.exists(speaker.VOICEPRINT) else '缺失!'}  {speaker.VOICEPRINT}")
    print(f"唤醒词        : {intents.WAKE_WORD or '(未设置)'}")
    print(f"VAD 灵敏度    : {voicerun.VAD_AGGRESSIVENESS} (0~3, 越大越严)")
    print(f"最短语音段    : {voicerun.MIN_SPEECH_MS}ms   句末静音: {voicerun.SILENCE_TAIL_MS}ms")
    print("=" * 62)
    print(f"\n请在 {seconds:.0f} 秒内正常说话，建议说：「魔丸你好呀，今天直播开心吗」\n")

    vad = webrtcvad.Vad(voicerun.VAD_AGGRESSIVENESS)
    stream = sd.RawInputStream(samplerate=voicerun.SAMPLE_RATE, blocksize=voicerun.FRAME_SAMPLES,
                               dtype='int16', channels=1, device=device)
    stream.start()

    segs, buf, speaking, silence_ms, voiced_ms = [], [], False, 0, 0
    peak = 0
    t_end = time.time() + seconds
    try:
        while time.time() < t_end:
            data, _ = stream.read(voicerun.FRAME_SAMPLES)
            frame = bytes(data)
            if len(frame) < voicerun.FRAME_SAMPLES * 2:
                continue
            arr = np.frombuffer(frame, dtype=np.int16)
            peak = max(peak, int(np.abs(arr).max()))
            if vad.is_speech(frame, voicerun.SAMPLE_RATE):
                if not speaking:
                    speaking, buf, silence_ms, voiced_ms = True, [], 0, 0
                buf.append(frame); voiced_ms += voicerun.FRAME_MS; silence_ms = 0
            elif speaking:
                buf.append(frame); silence_ms += voicerun.FRAME_MS
            if speaking and silence_ms >= voicerun.SILENCE_TAIL_MS:
                if voiced_ms >= voicerun.MIN_SPEECH_MS:
                    segs.append((b"".join(buf), voiced_ms))
                    print(f"  · 切出第 {len(segs)} 段（有效语音 {voiced_ms}ms）")
                else:
                    print(f"  · 有声音但太短被丢弃（{voiced_ms}ms < {voicerun.MIN_SPEECH_MS}ms）")
                speaking, buf, silence_ms, voiced_ms = False, [], 0, 0
    finally:
        stream.stop(); stream.close()

    print(f"\n录音结束。峰值音量 {peak}/32768 "
          f"({'太小，麦克风可能没收到声音' if peak < 500 else '正常'})")
    if not segs:
        print("\n❌ 一段语音都没切出来。要么没说话，要么 VAD 没认出人声（麦克风增益太低/设备选错）。")
        return

    print(f"\n共 {len(segs)} 段，逐段分析：\n")
    for i, (pcm, ms) in enumerate(segs, 1):
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        ok, sim = speaker.verify(audio, voicerun.THRESHOLD)
        text = asr.transcribe(audio, sample_rate=voicerun.SAMPLE_RATE)
        cmd = intents.to_audio_command(text, True, ts=int(time.time() * 1000) + i)
        print(f"【第 {i} 段】{ms}ms")
        print(f"   声纹相似度 : {sim:.3f}   （阈值 {voicerun.THRESHOLD} → {'通过' if ok else '不通过 ❌'}）")
        print(f"   ASR 听成   : {text!r}")
        if intents.WAKE_WORD:
            hit = intents._fuzzy_contains(intents.WAKE_WORD, text or '')
            print(f"   唤醒词命中 : {'是' if hit else '否'}（找「{intents.WAKE_WORD}」）")
        print(f"   最终意图   : {cmd['data']['intent'] if cmd else 'None（不会转发）'}")
        print()

    sims = [speaker.verify(np.frombuffer(p, dtype=np.int16).astype(np.float32) / 32768.0, voicerun.THRESHOLD)[1]
            for p, _ in segs]
    print("-" * 62)
    print(f"声纹相似度：最低 {min(sims):.3f}  最高 {max(sims):.3f}  平均 {sum(sims)/len(sims):.3f}")
    if max(sims) < voicerun.THRESHOLD:
        print("→ 所有段都没过声纹。若确认是本人在说话，多半是声纹是用别的麦克风/环境录的，需要重新注册。")


if __name__ == '__main__':
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 12.0)
