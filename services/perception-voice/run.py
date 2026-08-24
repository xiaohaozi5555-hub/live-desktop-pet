#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice 运行器：麦克风 → VAD → 声纹验证(仅主播) → ASR → 意图 → 发 perception.audio.command。
需装 sounddevice + webrtcvad + faster-whisper + resemblyzer，并先用主播语音 enroll 声纹。
管线：录音分段(VAD) → speaker.verify 通过才继续 → asr.transcribe → intents.to_audio_command。

设计要点：
- **采集与识别分线程**。ASR(whisper small/int8 CPU) 处理一句要一到几秒，若在采集线程里同步跑，
  这期间的麦克风数据会丢，连着说两句就会漏第二句。所以采集线程只做 VAD 切段，切好的段丢进
  队列，由 worker 线程做声纹+ASR。
- **先验声纹再跑 ASR**。声纹比对(resemblyzer)比 ASR 便宜得多，不是主播的声音直接丢弃，省掉整段
  ASR 开销——恐怖游戏音效、观众说话都会频繁触发 VAD，这个顺序很重要。
- stdout 每行一条状态，控制台直接读这个显示"最近听到什么"。
"""
import os
import queue
import sys
import threading
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
    """极简 .env 读取（KEY=VAL），做法照抄 dialogue/run.py。
    **必须在 import intents 之前跑**——intents.WAKE_WORD 是模块导入时就从环境变量读取的，
    晚了唤醒词读不到，会导致带对话窗口的语音永远不转发。"""
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

from bus_client import BusClient   # noqa: E402
import intents                     # noqa: E402

SAMPLE_RATE = 16000
FRAME_MS = 30                       # webrtcvad 只接受 10/20/30ms
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
SILENCE_TAIL_MS = 700               # 连续静音多久算这句说完
MIN_SPEECH_MS = 400                 # 短于此的段直接丢（咳嗽、键盘声、爆音）
MAX_SEGMENT_MS = 15000              # 单段上限，防止一直不停被无限缓冲
VAD_AGGRESSIVENESS = 2              # 0~3，越大越严格（越容易把弱音判成静音）

# 声纹判定阈值。原来写死 0.75，实测偏严：2026-07-27 用主播真实录音(BOYA)重建声纹后，
# 本人语音回测 0.764~0.819，而静音/底噪段是 0.457~0.473——真人和非人声之间有很宽的空档，
# 但 0.75 几乎贴着本人语音的下沿，换一句新话就容易掉到线下（现象=喊了没反应）。
# 取 0.60 落在空档中间，两边都有余量。可用 PET_VOICE_THRESHOLD 调整。
THRESHOLD = float(os.environ.get('PET_VOICE_THRESHOLD', '0.60'))

GATE_TAIL_MS = 600                  # 桌宠播完后再多压制这么久，盖住音箱余响/房间混响
GATE_MAX_MS = 30000                 # 兜底上限，见 SpeakGate 文档


def _log(msg):
    print(msg, flush=True)


class SpeakGate:
    """桌宠自己出声期间，把麦克风帧整个丢掉。

    **为什么需要**：2026-07-29 实测到死循环——TTS 从音箱放出来被麦克风收回去，声纹**没能挡住**
    （合成音的相似度过了阈值；声纹本来就是用来分"主播 vs 别人"，不是分"人 vs 机器"），于是
    被当成主播的新话又回一句，一句接一句停不下来，只能杀进程。

    信号来自总线的 `perception.audio.self_speaking`，由 `apps/character` 发布——只有它知道音频
    真实的起止。这里刻意**不去猜播放时长**：合成 3.8~5.3s、播放长短不一，猜短了漏声音进来，
    猜长了会把主播真正说的话吃掉。

    两个保护：
    - `tail_ms`：解除后再压制一小段，音箱余响和房间混响不会正好在 ended 那一刻消失。
    - `max_ms`：万一 `on:false` 因为渲染进程崩溃或总线断开没送到，麦克风不能就此永久失聪。
      到点自动放行，宁可漏进来一点回声，也不能变成"她再也听不见我"。
    """

    def __init__(self, tail_ms=GATE_TAIL_MS, max_ms=GATE_MAX_MS, on_log=None, clock=time.time):
        self.tail = tail_ms / 1000.0
        self.max = max_ms / 1000.0
        self._on = False
        self._since = 0.0
        self._until = 0.0          # 解除后尾巴的截止时刻
        self._log = on_log or (lambda m: None)
        self._clock = clock        # 注入时钟：handle 与 blocked 必须用同一个，否则测试里两边对不上
        self._lock = threading.Lock()

    def handle(self, msg):
        """总线消息回调：只认 perception/audio.self_speaking，其余一律不管。"""
        if msg.get('channel') != 'perception' or msg.get('type') != 'audio.self_speaking':
            return
        on = bool((msg.get('data') or {}).get('on'))
        now = self._clock()
        with self._lock:
            if on:
                if not self._on:
                    self._log('[voice] 桌宠开始说话，暂停收音')
                self._on, self._since = True, now
            elif self._on:
                self._on, self._until = False, now + self.tail
                self._log('[voice] 桌宠说完了，恢复收音')

    def blocked(self, now=None):
        """这一帧要不要丢掉。now 可注入，方便测试。"""
        now = self._clock() if now is None else now
        with self._lock:
            if self._on and now - self._since > self.max:
                # 已经白挡了 30 秒，不再加尾巴——此刻的首要风险是"她再也听不见我"，
                # 而不是漏进来一点回声。
                self._on, self._until = False, 0.0
                self._log('[voice] 兜底解除掐麦：等了 30s 没收到"说完了"，先恢复收音')
            if self._on:
                return True
            return now < self._until


def _warmup():
    """把模型加载提前到"还没人说话"的时候做掉。

    实测（2026-07-29 日志）第一句要 声纹 12.4s + 识别 24.7s ≈ 37 秒，之后每句只要 0.0s + 1.8s。
    差的全是懒加载：torch / faster-whisper 都是第一次用到才导入并建模型。这 37 秒里用户以为
    她没听见，会把同一句再说一遍——于是排队直接翻倍，体感比实际还慢。所以先拿一段噪声空跑一次。

    预热失败不阻塞启动：大不了退回原来的"第一句慢"，不能因为预热出错就整个语音功能起不来。
    """
    try:
        import numpy as np
        import asr
        import speaker
        dummy = (np.random.randn(SAMPLE_RATE) * 0.01).astype(np.float32)   # 1 秒极轻噪声
    except Exception as e:              # noqa: BLE001
        # 依赖缺失等：照旧启动，退回"第一句慢"，不要因为预热失败反而连听都听不了
        _log(f"[voice] 预热跳过（依赖不全）：{type(e).__name__}: {e}")
        return
    # 两个模型并行加载：它们互不相干（声纹走 torch，识别走 ctranslate2），各自的 global 缓存
    # 也是独立的。串行做实测要 57 秒（声纹 28.7s + 识别 28.3s，2026-07-30 冷盘），而这段时间
    # 用户只能干等着"开始监听"，能重叠就该重叠。
    spent = {}

    def warm(name, fn):
        s = time.time()
        try:
            fn()
        except Exception as e:          # noqa: BLE001
            _log(f"[voice] {name}预热跳过：{type(e).__name__}: {e}")
        spent[name] = time.time() - s

    t0 = time.time()
    jobs = [
        threading.Thread(target=warm, args=('声纹', lambda: speaker.verify(dummy, 2.0)), daemon=True),
        threading.Thread(target=warm, args=('识别', lambda: asr.transcribe(dummy, sample_rate=SAMPLE_RATE)), daemon=True),
    ]
    for j in jobs:
        j.start()
    for j in jobs:
        j.join()
    _log(f"[voice][耗时] 预热完成 声纹{spent.get('声纹', 0):.1f}s 识别{spent.get('识别', 0):.1f}s "
         f"实际等待{time.time() - t0:.1f}s（两个模型并行加载；这段原本是要压在你第一句话上的）")


def pick_device(sd):
    """选输入设备。默认跟随系统默认设备；设 PET_MIC 可指定（设备序号，或名字的一部分，
    如 PET_MIC=BOYA）。

    为什么需要这个：只用系统默认设备时，一旦默认设备变了（外接麦拔掉、系统自动切回主板
    声卡），程序会静悄悄地录错设备——现象就是"喊她完全没反应"，而日志里什么都看不出来。
    实测踩过一次：外接麦断开后默认回到主板 Realtek，VAD 一段人声都切不出来。
    所以这里无论选中哪个，都要把设备名打出来。"""
    want = os.environ.get('PET_MIC', '').strip()
    chosen = None
    if want:
        try:
            chosen = int(want)
        except ValueError:
            for i, d in enumerate(sd.query_devices()):
                if d['max_input_channels'] > 0 and want.lower() in d['name'].lower():
                    chosen = i
                    break
            if chosen is None:
                _log(f"[voice] 找不到名字含「{want}」的麦克风，改用系统默认")
    if chosen is None:
        chosen = sd.default.device[0]
    try:
        name = sd.query_devices(chosen)['name']
    except Exception:
        name = f'#{chosen}'
    _log(f"[voice] 使用麦克风：{name}")
    return chosen


def _worker(segments, bus, threshold):
    """消费切好的语音段：声纹 → ASR → 意图 → 发总线。单段异常不允许打断整个循环。"""
    import numpy as np
    import asr
    import speaker

    while True:
        pcm = segments.get()
        if pcm is None:
            return
        try:
            # 每段都记耗时：语音回复"要等很久"是实测存在的问题，但各段的实验室数字加起来
            # 对不上真实感受，所以把真实链路的每一步都量出来，别靠猜。
            t0 = time.time()
            audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
            seg_sec = len(audio) / SAMPLE_RATE
            ok, sim = speaker.verify(audio, threshold)
            t_vp = time.time()
            if not ok:
                _log(f"[voice] 非主播声音，已忽略 (相似度 {sim:.2f})")
                continue
            text = asr.transcribe(audio, sample_rate=SAMPLE_RATE)
            t_asr = time.time()
            if not text:
                continue
            _log(f"[voice] 听到：{text}")
            _log(f"[voice][耗时] 语音段{seg_sec:.1f}s 声纹{t_vp - t0:.1f}s "
                 f"识别{t_asr - t_vp:.1f}s 队列积压{segments.qsize()}段")
            cmd = intents.to_audio_command(text, True, ts=int(time.time() * 1000))
            if cmd:
                bus.publish(cmd)
                _log(f"[voice] -> intent={cmd['data']['intent']}")
            else:
                _log("[voice] （没喊唤醒词，未转发）")
        except Exception as e:                       # noqa: BLE001
            _log(f"[voice] 处理这段语音出错，已跳过：{type(e).__name__}: {e}")
        finally:
            segments.task_done()


def run(port=8765, threshold=None):
    import sounddevice as sd
    import webrtcvad
    import speaker

    threshold = THRESHOLD if threshold is None else threshold

    if not os.path.exists(speaker.VOICEPRINT):
        _log(f"[voice] 未注册主播声纹，请先 enroll。找不到：{speaker.VOICEPRINT}")
        return 1

    # 先预热再开麦：预热要几十秒，这期间麦克风还没开，不会让用户误以为已经能听了
    _log("[voice] 正在预热识别模型（首次约 30~40 秒，之后每句 2 秒左右）…")
    _warmup()

    bus = BusClient(port=port, source='perception.voice').connect()
    gate = SpeakGate(on_log=_log)
    bus.subscribe(gate.handle)          # 只订阅、不发布，掐麦不改变本服务在链路上的角色
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    segments = queue.Queue(maxsize=8)
    threading.Thread(target=_worker, args=(segments, bus, threshold), daemon=True).start()

    wake = intents.WAKE_WORD or "(未设唤醒词，任意语句都会被处理)"
    _log(f"[voice] 开始监听麦克风，仅主播声纹放行。唤醒词：{wake}")

    device = pick_device(sd)
    stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=FRAME_SAMPLES,
                               dtype='int16', channels=1, device=device)
    stream.start()
    buf, speaking, silence_ms, voiced_ms = [], False, 0, 0
    quiet_since = time.time()          # 长时间一段人声都没切出来 → 多半是设备选错，主动提醒
    try:
        while True:
            data, _overflowed = stream.read(FRAME_SAMPLES)
            frame = bytes(data)
            if len(frame) < FRAME_SAMPLES * 2:       # 设备偶发短读，跳过这帧
                continue

            # 桌宠正在出声：这一帧里多半是她自己的声音，直接丢。注意仍然要照常 read()，
            # 不然驱动缓冲会溢出。说到一半被她插话时，手上这段已经混进了她的声音，整段作废。
            if gate.blocked():
                if speaking:
                    speaking, buf, silence_ms, voiced_ms = False, [], 0, 0
                quiet_since = time.time()            # 别把"她在说话"算成"一分钟没听到人声"
                continue

            is_speech = vad.is_speech(frame, SAMPLE_RATE)

            if is_speech:
                if not speaking:
                    speaking, buf, silence_ms, voiced_ms = True, [], 0, 0
                buf.append(frame)
                voiced_ms += FRAME_MS
                silence_ms = 0
            elif speaking:
                buf.append(frame)
                silence_ms += FRAME_MS

            too_long = speaking and (voiced_ms + silence_ms) >= MAX_SEGMENT_MS
            if speaking and (silence_ms >= SILENCE_TAIL_MS or too_long):
                if voiced_ms >= MIN_SPEECH_MS:
                    try:
                        segments.put_nowait(b"".join(buf))
                        quiet_since = time.time()
                    except queue.Full:
                        # 识别跟不上说话速度时宁可丢新段，也不要无限堆积内存
                        _log("[voice] 识别队列已满，丢弃这一段")
                speaking, buf, silence_ms, voiced_ms = False, [], 0, 0

            # 静默兜底提示：开着麦却长时间一段人声都没有，通常是录错设备（外接麦被拔了、
            # 系统默认切回主板声卡）。不提示的话用户只会觉得"她不理我"，无从排查。
            if time.time() - quiet_since > 60:
                _log("[voice] 一分钟没听到人声，确认下麦克风是否选对（可用 PET_MIC 指定）")
                quiet_since = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        stream.stop()
        stream.close()
    return 0


if __name__ == '__main__':
    sys.exit(run())
