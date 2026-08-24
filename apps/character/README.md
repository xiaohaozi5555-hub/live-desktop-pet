# character — 表现层 (Electron)

透明置顶桌宠窗，订阅 `action.*` 契约渲染角色（AI 生成透明背景逐帧插画，按状态切图+计时播放）+ 表情 + 文字气泡 + edge-tts 语音。渲染器藏在 `action.*` 后，后续升级（如 Live2D）不动其它层（见 [../../ARCHITECTURE.md](../../ARCHITECTURE.md)）。

## 运行

| 命令 | 说明 |
|---|---|
| `npm start` | 可见桌宠；双击桌宠打开/关闭调试面板手动注入 action |
| `npm run demo` | 自动播放一段序列：欢迎→被吓→大礼答谢→说话→停 |
| `npm run capture` | 逐状态截图到 `.cache/`（视觉验证用） |
| `npm run verify` / `node verify_character.js` | 逻辑验证：action-map 映射 + edge-tts 出声（退出码 0=全过） |

## 能力

- 表情：`neutral / happy / scared / surprised / smug / blush / sleepy`
- 动作：`idle / wave / scared / thank_small / thank_big / laugh / praise / beg / sleep`
- 环境待机：无事件时 `idle`（坐姿吃东西）与 `idle_sleep`（4 种随机睡姿之一）自动轮换；事件/表情随时可打断；纯渲染层行为，不经过 `action.*` 契约
- 语音：edge-tts（venv，默认 `zh-CN-XiaoyiNeural`）→ mp3 → base64 播放，说话时嘴同步动
- `stop`（闭嘴/休息）：停语音、清气泡、回 idle

## 渲染架构

`state-packs/agent-0N-*.js`（各状态的帧序列/时长/气泡文案配置）→ `state-registry.js`（汇总）→ `action-map.js`（契约映射）→ `renderer.js`（按配置播放逐帧动画+气泡特效）。`dev-tools.js` 只用这几个文件暴露的公共接口补开发期用的调试面板和 `npm run demo` 回放，不改内部逻辑。

## 依赖

- Electron（devDependency，本目录 `npm install`）。
- edge-tts（仓库根 `.venv`，联网调微软接口合成）。
- `.cache/` 为运行期音频/截图，已 gitignore。
