#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M5 语音验证（自证逻辑，无需 ASR/声纹/麦克风）：意图映射 + audio.command 构造 + 端到端 brain。
真实 ASR/声纹需装 faster-whisper/resemblyzer + 你的语音注册，本脚本仅验证文本→意图→决策链路。"""
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
import intents                     # noqa: E402
from brain import Brain            # noqa: E402
import validate as contract        # noqa: E402

passed = failed = 0


def check(n, ok, d=''):
    global passed, failed
    passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
    print(f"[{'PASS' if ok else 'FAIL'}] {n}" + (f" — {d}" if d else ''))


# 1) 文本 → 意图
check('“先闭嘴” → mute', intents.to_intent('先闭嘴') == 'mute')
check('“卡关了怎么过” → walkthrough_ask（二次确认，不直接触发攻略分析）', intents.to_intent('卡关了怎么过') == 'walkthrough_ask')
check('“看一下弹幕帮我回一下” → review_chat', intents.to_intent('看一下弹幕帮我回一下') == 'review_chat')
# 两级触发（2026-07-30）：随口提一句要反问；已经明说要攻略的直接办，别浪费直播时间再问一遍
check('“帮我找找攻略” → walkthrough（直接触发，不反问）',
      intents.to_intent('帮我找找攻略') == 'walkthrough', str(intents.to_intent('帮我找找攻略')))
check('真实求助长句 → walkthrough',
      intents.to_intent('我卡关了帮我找找攻略，游戏名是层层恐惧，我在这个房子里找不到东西') == 'walkthrough')
check('只是随口说卡关 → 仍然 walkthrough_ask（要反问，别乱截屏搜）',
      intents.to_intent('这关我卡关了') == 'walkthrough_ask')
check('“不知道该干嘛”也算卡关求助', intents.to_intent('我不知道该干嘛了') == 'walkthrough_ask')
check('“今天天气不错” → None', intents.to_intent('今天天气不错') is None)
check('"求礼物"已移除，不再命中任何固定意图', intents.to_intent('帮我催一下求礼物') is None)

# 2) audio.command 构造契约合法 + 声纹字段
cmd = intents.to_audio_command('先闭嘴', speaker_verified=True, ts=1)
check('audio.command 契约合法且 speaker_verified=True', cmd and not contract.validate_message(cmd) and cmd['data']['speaker_verified'] is True, str(cmd and cmd['data']))

# 3) 端到端：只认主播
b = Brain()
b.handle(intents.to_audio_command('闭嘴', speaker_verified=False, ts=2))
check('非主播语音 mute 被忽略(mode 仍 ACTIVE)', b.mode == 'ACTIVE')
b.handle(intents.to_audio_command('闭嘴', speaker_verified=True, ts=3))
check('主播语音 mute 生效(mode=QUIET)', b.mode == 'QUIET')
# "求礼物"已从固定 intent 表移除（见上面第43行 to_intent 断言）；08-01 起 to_audio_command
# 对没命中固定表的话统一转发成 'chat'（不再有"无唤醒词=什么都不做"的旧分支），
# 所以这里要确认的是"不会被当成求礼物指令"，不是"什么都不产生"。
cmd_gift = intents.to_audio_command('催一下求礼物', speaker_verified=True, ts=4)
check('"求礼物"已移除，不会被识别成任何固定指令（落到 chat 兜底，不是复活的求礼物意图）',
      cmd_gift is None or cmd_gift['data']['intent'] == 'chat', str(cmd_gift))

# 3b) 唤醒词门槛（防恐怖游戏音效误触）
intents.WAKE_WORD = "小幽"
try:
    check('唤醒词开启：“小幽 闭嘴” → mute', intents.to_intent('小幽 闭嘴') == 'mute')
    check('唤醒词开启：无唤醒词“闭嘴” → None(不误触)', intents.to_intent('闭嘴') is None)
finally:
    intents.WAKE_WORD = ""      # 复位，避免影响其它用例

# 3c) 唤醒词 + 自由聊天转发（供 services/dialogue 服务用，见其 SPEC.md）。2026-08-01
#     取消了"免重复唤醒词"的对话窗口——真开播实测发现主播跟观众讲话的语速经常快过窗口
#     时长，窗口会被跟桌宠不相干的话续上、白白耗 DeepSeek token。现在每句都要喊唤醒词。
intents.WAKE_WORD = "魔丸"
try:
    cmd = intents.to_audio_command('魔丸魔丸今天天气怎么样', speaker_verified=True, ts=100)
    check('唤醒词命中+命中不了固定intent → 转发 chat', cmd is not None and cmd['data']['intent'] == 'chat', str(cmd))
    check('转发的 raw_text 是原话', cmd is not None and cmd['data']['raw_text'] == '魔丸魔丸今天天气怎么样')

    cmd2 = intents.to_audio_command('继续聊会儿呗', speaker_verified=True, ts=101)
    check('紧跟着的下一句没喊唤醒词 → 不再自动转发（没有免唤醒词的窗口了）', cmd2 is None, str(cmd2))

    cmd3 = intents.to_audio_command('魔丸 闭嘴', speaker_verified=True, ts=600)
    check('喊了唤醒词+命中固定intent → 优先 mute，不当聊天内容', cmd3 is not None and cmd3['data']['intent'] == 'mute', str(cmd3))

    check('没喊唤醒词、也不匹配固定表 → None（不会把跟观众讲的话误转发成聊天内容）',
          intents.to_audio_command('随便聊聊', speaker_verified=True, ts=1) is None)
finally:
    intents.WAKE_WORD = ""      # 复位，避免影响其它用例

# 4) 掐麦（SpeakGate）——防"她听见自己→当成主播说话→再回一句"的死循环
#    2026-07-29 实测过这个死循环（连说 5 轮只能杀进程），所以这里逐条钉死行为。
import inspect                     # noqa: E402
import importlib.util as _ilu      # noqa: E402

# 按文件路径显式加载：sys.path 里 services/brain 排在本目录前面，直接 `import run`
# 会拿到 brain 的 run.py（踩过）。
_spec = _ilu.spec_from_file_location('voice_run', os.path.join(HERE, 'run.py'))
voice_run = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(voice_run)

ON = {'channel': 'perception', 'type': 'audio.self_speaking', 'ts': 1, 'data': {'on': True}}
OFF = {'channel': 'perception', 'type': 'audio.self_speaking', 'ts': 2, 'data': {'on': False}}

check('self_speaking 事件本身契约合法', not contract.validate_message(ON), str(contract.validate_message(ON)))
check('self_speaking 缺 on 字段会被契约拦下',
      bool(contract.validate_message({**ON, 'data': {}})))

class _Clock:
    """可拨动的假时钟：handle() 和 blocked() 必须共用同一个时间源，用真实时间没法测。"""
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


clk = _Clock()
g = voice_run.SpeakGate(tail_ms=500, max_ms=30000, on_log=lambda m: None, clock=clk)
check('默认不挡（没人说话时正常收音）', not g.blocked())

g.handle(ON)
check('桌宠开始说话 → 挡住', g.blocked())
clk.t = 1005.0
check('一直说 → 一直挡', g.blocked())

g.handle(OFF)                      # 此刻 1005.0，尾巴到 1005.5
clk.t = 1005.2
check('刚说完 → 尾巴期内仍然挡（盖住音箱余响）', g.blocked())
clk.t = 1005.7
check('尾巴过完 → 恢复收音', not g.blocked())

# 兜底：on:false 丢了（渲染进程崩 / 总线断），不能让麦克风永久失聪
clk2 = _Clock(2000.0)
g2 = voice_run.SpeakGate(tail_ms=500, max_ms=30000, on_log=lambda m: None, clock=clk2)
g2.handle(ON)
clk2.t = 2029.0
check('兜底前仍然挡', g2.blocked())
clk2.t = 2031.0
check('超过 30s 没等到"说完了" → 自动放行，不永久失聪', not g2.blocked())

# 连说多句：中间不解除（renderer 只在队列排空后才回报），别在句缝里把余响收进去
clk4 = _Clock(3000.0)
g4 = voice_run.SpeakGate(tail_ms=500, max_ms=30000, on_log=lambda m: None, clock=clk4)
g4.handle(ON)
clk4.t = 3004.0
g4.handle(ON)                      # 第二句开始，重复置位
check('连说多句时重复置位不会误解除', g4.blocked())
clk4.t = 3031.0                    # 距第一次置位已 31s，但距最后一次只有 27s
check('兜底按最后一次置位算：还在连着说就不提前放行', g4.blocked())

# 不相干的消息不许影响收音（总线上什么都有，别误伤）
clk3 = _Clock()
g3 = voice_run.SpeakGate(tail_ms=500, on_log=lambda m: None, clock=clk3)
g3.handle({'channel': 'action', 'type': 'speak', 'ts': 1, 'data': {'text': '你好'}})
g3.handle({'channel': 'perception', 'type': 'danmaku.chat', 'ts': 1, 'data': {'user': 'a', 'text': 'b'}})
check('action.speak / 弹幕等其它消息不会误触发掐麦', not g3.blocked())

# 结构约束：掐麦只是"听总线的一个事实"，不许顺手变成发布者。
# 同理于 record_grab 那条教训——观察/旁路逻辑不能悄悄挤进数据链路。
check('SpeakGate 不持有任何发布通道（构造参数里没有 bus/publish）',
      not any(k in ('bus', 'publish', 'client') for k in inspect.signature(voice_run.SpeakGate.__init__).parameters))
check('SpeakGate 只暴露 handle/blocked，没有 publish 类方法',
      not [m for m in dir(voice_run.SpeakGate) if 'publish' in m or 'send' in m])

# 5) 真实 ASR/声纹：需装依赖 + 注册声纹
import importlib.util  # noqa: E402
missing = [m for m in ('faster_whisper', 'resemblyzer', 'sounddevice', 'webrtcvad')
           if importlib.util.find_spec(m) is None]
if missing:
    print(f"[SKIP] 未装 {', '.join(missing)}（torch 等较重）——真实 ASR/声纹待装 + 你的语音注册；意图与接线已自证。")

print(f"\n==== 语音控制 验证: {passed}/{passed + failed} 通过 ====")
sys.exit(0 if failed == 0 else 1)
