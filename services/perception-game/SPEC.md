# perception-game — 游戏画面感知

## 职责
两类事件：即时"惊吓"(低延迟、本地) 与 深度"场景理解/卡关攻略"(LLM)。

## 输入
- 游戏区截图(mss)，区域由 `command.calibrate(region=game)` 提供（竖屏上半 / 横屏对应区）。
- 系统/游戏音频 RMS（可选，惊吓用）。
- 离线：合成帧(numpy) 用于被吓单测；真实关键帧待补 `fixtures/vision/`（供攻略验证）。

## 输出
- `game.scare{intensity 0..1}`：亮度/音量突变启发式，**不走 LLM**，供桌宠对恐怖画面即时做被吓反应。✅ **完成**。
- `game.scene{summary,tags[],stuck,hint?}`：多模态大模型(vision.py)，**支持国产**(Qwen-VL/GLM-4V/豆包/Kimi via OpenAI 兼容) 或 Claude，按需「卡关」触发 + 低频周期采样。✅ **代码就绪**，运行需在 `.env` 配国产 `PET_VISION_*`(推荐) 或 `ANTHROPIC_API_KEY`。

## 组成
- `scare.py`：`ScareDetector`(亮度/音量突变 + 冷却) + `frame_luminance`。纯 numpy。
- `run.py`：mss 实时采集游戏区 → 被吓检测 → 发 `game.scare`；收到 walkthrough 指令 → 截图 → `vision.analyze` → 发 `game.scene`。
- `vision.py`：`analyze()` 已实现（anthropic 多模态 + 结构化输出 → game.scene）；运行需 ANTHROPIC_API_KEY。

## 依赖
`numpy`、`mss`、`anthropic`、`openai`（已装）；攻略运行时需在 `.env` 配国产 `PET_VISION_*`（推荐 Qwen-VL）或 `ANTHROPIC_API_KEY`。

## 离线验证
`python services/perception-game/verify_game.py` = **7/7**（暗/亮帧亮度、稳定不误触、闪光/变黑触发、冷却、端到端合成恐怖帧→桌宠 scared）。
`python services/perception-game/verify_vision.py` = **2/2**（make_scene 契约 + 端到端 brain 说攻略；配 key 后真调 LLM 冒烟）。

## 里程碑
M7：**被吓即时反应 + 卡关攻略均已接入**；攻略运行时需你在 `.env` 配 ANTHROPIC_API_KEY。
