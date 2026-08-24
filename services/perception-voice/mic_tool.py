#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""麦克风测试 / 声纹重录，供控制台按钮调用。

为什么单独做这个：排查"喊了没反应"时，让用户对着看不见提示的后台进程盲说是不可行的——
实测两次都因为不知道何时该开口而拿到无效数据。这里把过程拆成"按提示读一句、录一段"，
每一步都通过 stdout 的 JSON 事件回报给控制台 UI，由 UI 显示倒计时和结果。

用法：
  python mic_tool.py test           # 录一段，报音量/切段/声纹相似度/ASR 结果
  python mic_tool.py enroll         # 按提示读 5 句，重新注册声纹（旧的自动备份）

输出：每行一个 JSON 事件，字段 ev 见下面各处 _ev(...) 调用。
"""
import json
import os
import shutil
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
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
import run as voicerun    # noqa: E402  复用同一套采样率/VAD参数/设备选择

SR = voicerun.SAMPLE_RATE

# 注册用的句子：都带唤醒词，且长短、语气有变化——声纹取多段平均，样本单一会过拟合到某个语调。
ENROLL_LINES = [
    "魔丸，你好呀",
    "魔丸，今天直播开心吗",
    "魔丸，帮我看看这里怎么过",
    "魔丸，我们一起加油吧",
    "魔丸，休息一下吧",
]


def _ev(ev, **kw):
    print(json.dumps({"ev": ev, **kw}, ensure_ascii=False), flush=True)


def _record(sd, device, seconds):
    """录固定时长，返回 (int16 bytes, 峰值)。不做 VAD，注册时要的是完整一句。"""
    import numpy as np
    frames = []
    peak = 0
    stream = sd.RawInputStream(samplerate=SR, blocksize=voicerun.FRAME_SAMPLES,
                               dtype='int16', channels=1, device=device)
    stream.start()
    t_end = time.time() + seconds
    last_report = 0
    try:
        while time.time() < t_end:
            data, _ = stream.read(voicerun.FRAME_SAMPLES)
            b = bytes(data)
            if len(b) < voicerun.FRAME_SAMPLES * 2:
                continue
            frames.append(b)
            arr = np.frombuffer(b, dtype=np.int16)
            peak = max(peak, int(abs(arr).max()))
            now = time.time()
            if now - last_report > 0.2:          # 给 UI 一个电平条，让人看见"确实在收音"
                last_report = now
                _ev("level", peak=int(abs(arr).max()), remain=round(t_end - now, 1))
    finally:
        stream.stop(); stream.close()
    return b"".join(frames), peak


def _save_wav(pcm, path):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm)
    return path


def cmd_test(seconds=6.0):
    import numpy as np
    import sounddevice as sd
    import asr
    import speaker

    device = voicerun.pick_device(sd)
    name = sd.query_devices(device)['name']
    _ev("device", name=name)
    _ev("prompt", text="随便说一句话，比如「魔丸你好呀」", seconds=seconds)
    pcm, peak = _record(sd, device, seconds)
    _ev("recorded", peak=peak)

    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if peak < 800:
        _ev("result", ok=False, reason="音量太小，几乎没收到声音——确认麦克风没静音、增益够大",
            peak=peak, sim=None, text="")
        return
    sim = None
    if os.path.exists(speaker.VOICEPRINT):
        _, sim = speaker.verify(audio, voicerun.THRESHOLD)
    text = asr.transcribe(audio, sample_rate=SR)
    _ev("result", ok=True, peak=peak, sim=(round(float(sim), 3) if sim is not None else None),
        text=text, threshold=voicerun.THRESHOLD)


def cmd_enroll(seconds=4.0):
    import sounddevice as sd
    import speaker

    device = voicerun.pick_device(sd)
    _ev("device", name=sd.query_devices(device)['name'])
    enroll_dir = os.path.join(HERE, 'enroll')
    os.makedirs(enroll_dir, exist_ok=True)

    # 质量门槛。上一版只在"每一段都太小"时才拒绝，结果 5 句里 2 句静音、1 句削波照样通过，
    # 建出来的声纹比原来还差。改成逐句判定 + 当场重录，并要求至少 MIN_GOOD 段合格。
    MIN_PEAK, CLIP_PEAK, MIN_GOOD, MAX_RETRY = 800, 32000, 3, 2

    def record_line(i, line, attempt):
        tip = line if attempt == 0 else f"{line}（这句再来一次）"
        _ev("prompt", text=tip, index=i, total=len(ENROLL_LINES), seconds=seconds)
        time.sleep(1.5)                            # 留出看清句子、准备开口的时间
        _ev("recording", index=i, total=len(ENROLL_LINES))
        pcm, peak = _record(sd, device, seconds)
        weak, clipped = peak < MIN_PEAK, peak >= CLIP_PEAK
        _ev("recorded", index=i, peak=peak, weak=weak, clipped=clipped)
        return pcm, peak, weak, clipped

    paths, rejected = [], []
    for i, line in enumerate(ENROLL_LINES, 1):
        for attempt in range(MAX_RETRY + 1):
            pcm, peak, weak, clipped = record_line(i, line, attempt)
            if not weak and not clipped:
                paths.append(_save_wav(pcm, os.path.join(enroll_dir, f"enroll_{i}.wav")))
                break
            if attempt == MAX_RETRY:
                rejected.append({"index": i, "peak": peak, "why": "太小" if weak else "爆音"})
        time.sleep(0.4)

    if len(paths) < MIN_GOOD:
        _ev("done", ok=False,
            reason=f"只录到 {len(paths)} 段合格音频（至少要 {MIN_GOOD} 段），声纹未更新。"
                   f"太小的说明麦克风没收到声音，爆音的说明离麦太近。")
        return

    # 旧声纹先备份，别把唯一一份可用数据覆盖掉
    if os.path.exists(speaker.VOICEPRINT):
        bak = speaker.VOICEPRINT + time.strftime(".bak-%Y%m%d-%H%M%S")
        shutil.copy2(speaker.VOICEPRINT, bak)
        _ev("backup", path=os.path.basename(bak))

    speaker.enroll(paths)

    # 自检：拿刚录的几段回测新声纹，相似度应当明显高于阈值；否则说明录得不干净
    import numpy as np
    sims = []
    for p in paths:
        with wave.open(p, 'rb') as w:
            pcm = w.readframes(w.getnframes())
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        sims.append(speaker.verify(audio, voicerun.THRESHOLD)[1])
    _ev("done", ok=True, count=len(paths), rejected=rejected,
        selfsim_min=round(float(min(sims)), 3), selfsim_avg=round(float(sum(sims) / len(sims)), 3))


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'test'
    try:
        if mode == 'enroll':
            cmd_enroll()
        else:
            cmd_test()
    except Exception as e:                          # noqa: BLE001 UI 需要拿到失败原因
        _ev("error", message=f"{type(e).__name__}: {e}")
        sys.exit(1)
