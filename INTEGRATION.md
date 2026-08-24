# 接入与运行总指南（直播桌宠）

四层（感知/决策/表现/控制）经本地 TCP 事件契约解耦。离线全绿；接真播/真识别需你补几样东西（见末尾清单）。

## 一、把系统跑起来

```bash
# 1) 总线 + 决策（一条命令；也可分开起 broker/brain）
python scripts/run_all.py          # 起 broker+brain，按提示回放弹幕夹具

# 2) 角色（透明置顶窗，自动连总线 127.0.0.1:8765）
cd apps/character && npm start

# 3) 控制（任选）
python apps/control-panel/panel.py            # 面板 CLI（闭嘴/休息/求礼物/指定动作）
python apps/control-panel/panel.py --gui      # Tkinter 按钮面板
python apps/control-panel/panel.py --keywords # 弹幕关键词→command 桥

# 4) 各感知模块（按需启用，依赖/密钥见第三节）
python services/perception-game/run.py        # 被吓即时反应(即用) + 卡关攻略(需 API key)
python services/perception-face/run.py        # 露脸表情（需 deepface）
python services/perception-voice/run.py       # 语音·只认你（需 ASR/声纹 + 注册声纹）
python services/perception-danmaku/run.py     # 弹幕（离线回放；实时需官方玩法 APP ID）
```

## 二、接入抖音直播伴侣

1. 桌宠是**透明置顶窗**（`apps/character`）。在直播伴侣「添加素材 → 窗口捕获」选中该窗口，即进入直播画面。
2. **布局**：你分屏直播（上=游戏、下=露脸；横屏时露脸在左下角）。用控制面板的**区域校准**给 game/face/plugin 三区分别画框，竖屏/横屏各存一套预设（`command.calibrate` 已定义，校准 UI 在真实桌面版细化）。
3. 桌宠位置自定，建议放在不遮挡关键信息的角落。

## 三、各能力与所需（已就绪 / 待你补的）

| 能力 | 现状 | 运行还需 |
|---|---|---|
| 弹幕(进场/礼物分级/求礼物/昵称) | ✅ 归一化+决策真实可用 | **官方玩法**实时：你注册抖音开放平台拿 **APP ID**+开通互动数据 |
| 游戏·被吓即时反应 | ✅ 真实可用（numpy/mss 已装） | 无 |
| 游戏·卡关攻略 | ✅ 真实跑通（多模态模型，`.env` 已配 qwen3-vl-plus） | 无（换模型/换 key 见 `.env.example`） |
| 插件 OCR(倒计时/整蛊) | 🚫 已弃用（2026-07-29，用户决定） | 代码保留在 services/perception-ocr/，依赖未装、不启动 |
| 露脸表情(调侃/夸夸) | ✅ 真实验证（deepface 已装，已用真实照片验证） | 无 |
| 语音·只认你 | ✅ 真实验证（声纹已注册，各环节已用真实录音验证） | 待接线的是持续监听麦克风的运行时循环，见 `services/perception-voice/run.py` |
| 控制(面板/弹幕关键词/语音) | ✅ 真实可用（弹幕关键词已加白名单校验） | 无 |
| 角色(手绘 SVG+气泡+edge-tts) | ✅ 阶段A可用 | 阶段B：定制 Live2D/神经动画 + GPT-SoVITS 定制音（需美术选择/参考音） |

## 四、需要你提供才能推进的清单

1. **抖音开放平台 APP ID / 互动数据能力** → 解锁实时弹幕。
2. **角色美术方向**：Live2D 绑定 vs 神经单图动画；GPT-SoVITS 定制音的参考音频。
3. 开播一次做**实播彩排**（直播伴侣窗口捕获 + 弹幕/语音冒烟）。

> 全部离线验证：见 `RUNNING.md`。能力状态与待办：见 `CHANGELOG.md`。
