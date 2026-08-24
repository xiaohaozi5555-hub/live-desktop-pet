# control-panel — 控制层

## 职责
给主播三条控制通道发 `command.*`：桌面面板 / 弹幕关键词 / 语音意图。统一映射到同一套 `command.*`，经本地总线到 brain。

## 输出（发布）
`command.mute/unmute` / `sleep/wake` / `do{action}` / `mode{name,on}` / `calibrate{region,box,layout}`。

## 组成（Python，复用本地总线，纯标准库）
- `commands.py`：命令构造器（纯函数，产出契约合法 `command.*`）。
- `keywords.py`：弹幕关键词 → command 映射；只有主播/房管白名单（`STREAMER_NAMES`/`MODERATOR_NAMES`）内的昵称才生效，可配 `PREFIX` 暗号做双重防误触。
- `panel.py`：连总线发指令，三模式：
  - CLI `python panel.py`：终端输入指令。
  - GUI `python panel.py --gui`：Tkinter 按钮面板（桌面用；无 tkinter 自动退回 CLI）。
  - 关键词桥 `python panel.py --keywords`：订阅弹幕 chat，命中关键词 → 发 command。

## 控制方式
语音(仅主播，声纹校验) + 本面板 + 弹幕关键词(白名单校验)；不做全局热键。

## 依赖
纯标准库（tkinter 为可选 GUI）。无第三方下载。

## 离线验证
`python apps/control-panel/verify_control_panel.py`（构造器契约合法 + 关键词映射含白名单 + 面板经总线驱动 brain：do:wave→wave、mute→闭嘴、关键词 unmute→恢复）。

## 备注
控制层用 Python 而非 Electron：复用 Python 总线客户端、纯标准库可离线验证、GUI 用 stdlib tkinter；与 Electron 角色经同一 TCP 总线协作。`calibrate` 命令已就位，供 face/game/ocr 区域配置（GUI 画框待细化）。
