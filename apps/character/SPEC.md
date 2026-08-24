# character — 表现层（桌宠窗口）

## 职责
Electron 透明置顶窗口，订阅 `action.*`，渲染角色动作/表情/气泡/语音，供直播伴侣窗口捕获。

## 订阅（输入）
`action.play_motion` / `set_expression` / `show_bubble` / `speak` / `stop`。

## 表现能力
- 角色渲染（渲染器可换，藏在 action 契约后）：
  - 阶段 A：同角色**成套立绘** + 补间（idle/scared/wave/thank_small/thank_big/laugh/praise/beg/sleep）。
  - 阶段 B：**Live2D**（pixi-live2d-display + lipsync 补丁）或**神经单图动画**。
- 文字气泡（`show_bubble`）+ 字幕。
- TTS：edge-tts（默认 zh-CN-XiaoyiNeural）→ GPT-SoVITS(定制萝莉音) 升级；按振幅做口型。
- `stop`：立即安静，仅 idle（对应闭嘴/休息）。

## 依赖
Electron（已装）、edge-tts（venv，已装）。阶段B升级另需 `pixi.js` + `pixi-live2d-display(-lipsyncpatch)` 或神经动画方案。

## 窗口/采集
无边框透明、always-on-top、可切鼠标穿透；主播在直播伴侣加「窗口捕获」指向本窗口，见 INTEGRATION.md。

## 离线验证
`node verify_character.js`；也可用调试面板/命令手动注入 `action.*` 序列，肉眼确认动作/气泡/语音。
