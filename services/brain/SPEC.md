# brain — 决策/编排

## 职责
订阅全部 `perception.*` 与 `command.*`，按规则/LLM 决策，发布 `action.*` 给表现层。

## 订阅（输入）
所有 `perception.*`（弹幕/表情/游戏/插件/语音）+ 所有 `command.*`（面板/弹幕关键词/语音意图）。

## 发布（输出）
`action.play_motion` / `set_expression` / `show_bubble` / `speak` / `stop`。

## 决策策略
- **规则快路径（无 LLM）**：进场→欢迎(合批+限流)；礼物→按 `value_coins` 分级答谢(thank_small/…/thank_big)；`game.scare`→被吓反应(表情+气泡+音效)；倒计时里程碑→提醒。
- **LLM 路径（Claude）**：卡关攻略(按需)、看 `face.expression` 调侃/夸夸、求礼物话术(用最近昵称)、弹幕闲聊。⚠️ 需 API key → M7。
- **状态机 + 模式**：ACTIVE / QUIET(闭嘴，仅 idle，不 speak/bubble) / SLEEP / GIFT_BEGGING；冷却 + 优先级队列 + 防刷屏。
- **优先级**：`audio.command`(speaker_verified) 与 `command.*` 最高，覆盖一切（"我让他干嘛就干嘛"）。

## 计划依赖（待批准）
`anthropic`（LLM）；本地 WS 客户端。

## 离线夹具与验证
用 `scripts/mock_bus.py` 回放弹幕夹具 + 合成事件，断言：小礼物→thank_small、大礼物→thank_big、进场→welcome、mute→后续 perception 不再产生 speak。规则路径全程无需 API key。

## 里程碑
M2（骨架+规则+状态机），M7（接入 LLM 路径）。
