# fixtures — 离线夹具

目的：让每个模块**不依赖真开播**即可开发与验证（应对上下文限制 & 保证可复现）。

| 目录 | 内容 | 状态 | 消费方 |
|---|---|---|---|
| `danmaku/` | `sample-session.jsonl`：模拟抓取器原始记录（member/chat/gift/like/social），含昵称、礼物、抖币数 | ✅ | perception-danmaku / mock_bus |
| `plugin/` | `sample-plugin-state.json`：插件 OCR 原文 + 解析后 countdown_sec / prank_count | 🚫 夹具保留，能力已弃用（2026-07-29） | perception-ocr |
| `vision/` | 游戏画面样例截图（恐怖场景 / 卡关谜题）+ 期望的 game.scene 标注 | ⏳ 待补（需截图或生成图） | perception-game |
| `face/` | 露脸样例帧（竖屏/横屏两种布局）+ 期望的 face.expression 标签 | ⏳ 待补（需人脸样本） | perception-face |

## 原始弹幕记录格式

```json
{"kind":"member|chat|gift|like|social", "ts":<epoch ms>, "user":"昵称", ...}
```
`chat` 带 `content`；`gift` 带 `gift`/`count`/`coins`；`like` 带 `count`。
经 `scripts/mock_bus.py:normalize()` 归一化为契约中的 `perception.danmaku.*`。

## 图像夹具补充方式

- `vision/`：从录屏截取若干恐怖游戏关键帧（黑暗/血腥/跳吓/解谜卡关），旁边放同名 `.expected.json` 写期望的 `summary/tags/stuck`。
- `face/`：采集主播露脸截图（`portrait_*.png` 竖屏、`landscape_*.png` 横屏左下角），旁边放 `.expected.json` 写期望表情标签。
- 图像文件默认被 `.gitignore` 排除（体积大），仅提交 `.expected.json` 标注与说明。
