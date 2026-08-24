# 架构

## 设计原则

**所有模块只依赖一份事件契约**（`packages/contract/`），经本地消息总线通信，互不依赖内部实现。这样每个模块可独立开发、独立离线验证、独立替换升级——也是应对上下文限制的核心：每个模块只需读自己的 `SPEC.md` + 契约。

## 四层

```
┌─────────────── 感知层 services/perception-* (Python) ───────────────┐
│  danmaku  DouyinBarrageGrab(系统代理读直播伴侣) -> danmaku.*          │
│  face     露脸区域截图 + DeepFace/MediaPipe    -> face.expression     │
│  game     游戏区截图 + Claude多模态(按需/周期) -> game.scene          │
│           + 本地亮度/音量突变启发式(低延迟)     -> game.scare          │
│  ocr      [已弃用 2026-07-29，代码保留不启动]  -> plugin.state         │
│  voice    麦克风 -> VAD -> 声纹验证(仅主播) -> ASR -> audio.command    │
└──────────────────────────────┬──────────────────────────────────────┘
                     perception.*│
┌──────────────────────────────▼──────────────────────────────────────┐
│ 决策层 services/brain (Python)                                        │
│  规则快路径: 进场欢迎(合批限流) / 礼物按value分级答谢 / 惊吓反应 /      │
│              倒计时提醒                                                │
│  LLM 路径(Claude): 卡关攻略(按需) / 看表情调侃夸夸 / 求礼物话术(带昵称) │
│  状态机: ACTIVE / QUIET(闭嘴) / SLEEP / GIFT_BEGGING + 冷却 + 防刷屏   │
│  声纹通过的语音指令最高优先级                                          │
└───────────────┬──────────────────────────────▲──────────────────────┘
        action.* │                      command.*│
┌────────────────▼─────────────┐   ┌─────────────┴──────────────────────┐
│ 表现层 apps/character         │   │ 控制层 apps/control-panel + 弹幕关键词│
│ Electron 透明置顶窗           │   │  切模式 / 发指令 / 屏幕区域校准       │
│ Live2D/立绘 + 气泡 + TTS+口型 │   │  (语音意图也归一化为 command.*)       │
│ 由直播伴侣窗口捕获进直播       │   └────────────────────────────────────┘
└──────────────────────────────┘
```

## 事件契约

三频道：`perception.*`（感知发布）、`command.*`（控制发布）、`action.*`（决策发布→表现消费）。
通用信封 `{channel,type,ts,source,data}`，逐类型载荷见 [packages/contract/events.md](packages/contract/events.md)，由 `validate.py` 校验。

## 传输

- **离线/测试**：进程内 mock 总线（`scripts/mock_bus.py`），用于离线回放与验证，不需要起真实进程。
- **真实运行**：本地 TCP 总线（JSON 行，`services/bus/broker.py`），Python 服务与 Electron 均连接；契约不变，仅换传输。

## 角色渲染（藏在 action.* 契约后，可独立升级）

- 阶段 A：AI 生成同角色**成套立绘** + 补间（先打通端到端）。
- 阶段 B：**定制 Live2D 绑定(Cubism)** 或**神经单图动画(Talking-Head-Anime-3 类)**，契约不变。

## 模块化开发原则

契约解耦 + 每模块独立 SPEC/夹具 + `CHANGELOG.md` 记录能力状态 + 可按模块独立提交/派子任务，改一个模块不需要读懂全部代码。
