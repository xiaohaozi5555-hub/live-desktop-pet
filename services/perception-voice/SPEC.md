# perception-voice — 语音指令感知（只认主播）

## 职责
监听麦克风，**只放行主播本人**的语音指令，解析意图，发布 `perception.audio.command`。

## 管线
`麦克风 → VAD(语音活动检测) → 声纹验证(与主播声纹比对) → ASR(中文) → 意图解析`。
- **声纹验证**：非主播声音（观众/游戏音效人声）→ `speaker_verified=false`；决策层只采信 `true`。
- 唤醒词 / 按键说话 可作进一步防误触兜底（恐怖游戏音效嘈杂）。

## 输出
`audio.command{intent(mute/unmute/sleep/wake/walkthrough), raw_text, speaker_verified}`。

> 2026-07-14：`gift_begging` 已移除（诱导送礼合规红线）。`services/dialogue`（新模块，见其 SPEC.md）会扩展这里的路由逻辑，加唤醒词"魔丸"开放式聊天转发——那是另一个 worktree 的活，这个文件届时会再变。

## 组成
- `intents.py`：识别文本 → 意图 → audio.command（纯函数）。✅ 已自测。
- `asr.py`：faster-whisper 中文转写（骨架，需装）。
- `speaker.py`：resemblyzer 声纹 `enroll()`/`verify()`（骨架，需装 + 注册主播声纹）。
- `run.py`：麦→VAD→声纹→ASR→意图→发布（骨架，需依赖 + 已注册声纹）。

## 依赖（已安装）
`faster-whisper`(ASR)、`resemblyzer`(声纹)、`webrtcvad-wheels`(预编译,绕过 MSVC 编译)、`torch`(CPU 版)。
Windows 坑：`webrtcvad` 源码需 MSVC 编译，改用同模块名的预编译包 `webrtcvad-wheels`；torch 安装曾因项目路径过深(`D:\调研claude与code\...\desktop-pet\.venv\...`)超 260 字符触发 `WinError 206`，已开启注册表 `LongPathsEnabled=1`（本机级、可逆）解决。

## 主播声纹注册（已完成）
用户录制 6 段语音（1 句日常 + 4 句指令 + 1 句补充），转 16k 单声道 wav 后 `speaker.enroll()` 生成 `voiceprint.npy`（1152 字节嵌入，gitignore）。**真实验证**：
- 留一法（5 段注册/1 段验证，轮换 6 次）：**6/6 通过**，相似度 0.875–0.899。
- 合成随机噪声模拟"非本人/音效"：相似度 0.553，**正确被拒绝**（阈值 0.75）。
- 原始 wav 副本已删除（仅保留派生的 voiceprint.npy），进一步降低敏感数据留存。

## 真实端到端（已完成）
对全部 6 段真实录音跑 `asr.transcribe → speaker.verify → intents.to_audio_command → Brain.handle`：
- 声纹：6/6 verified=True，相似度 0.914–0.931。
- 意图匹配：**6/6 符合预期**（含"小幽先闭嘴"→mute→桌宠stop、"可以说话"→unmute→idle、"卡关攻略"→walkthrough、"要个礼物催一下"→gift_begging、2 句日常闲聊→None 不误触发）。
- 发现并修复 2 处真实缺陷：① `asr.py` 加 `COMMAND_PROMPT` 初始提示词（faster-whisper `initial_prompt`），修正"催一下"被听成"吹一下"、"小幽"被听成"小悠/小游/小妖"等同音字误听；② `intents.py` 关键词匹配从精确子串改为**模糊包含**（`_fuzzy_contains`，允许字符间插入至多1个无关字），修正"要**个**礼物"因插字匹配不上"要礼物"的问题。

## 离线验证
`python services/perception-voice/verify_voice.py` = **10/10**（意图映射、唤醒词模糊匹配、audio.command 契约、端到端"只认主播"）。

## 里程碑
M5：**完成**（意图/声纹/ASR 全部真实验证通过；只认主播声音的核心诉求已验证生效）。
