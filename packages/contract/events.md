# 事件契约 (Event Contract) — 唯一集成边界

所有模块**只依赖本契约**，不依赖彼此实现。传输为本地 TCP 总线（真实运行）或进程内总线（离线 mock/测试用）；消息为 JSON。

## 通用信封 (Envelope)

每条消息统一结构：

| 字段 | 类型 | 说明 |
|---|---|---|
| `channel` | string | `perception` / `command` / `action` 三者之一 |
| `type` | string | 该频道下的事件类型（见下表） |
| `ts` | integer | 事件时间戳（epoch 毫秒） |
| `source` | string | 发布方模块 id（可选，便于溯源），如 `perception.danmaku` |
| `data` | object | 类型专属载荷 |

## `perception.*`（感知层发布）

| type | data 字段 | 含义 |
|---|---|---|
| `danmaku.enter` | `user`(str), `fansclub_level`(int 可选) | 观众进场 |
| `danmaku.chat` | `user`(str), `text`(str), `fansclub_level`(int 可选) | 弹幕文字 |
| `danmaku.gift` | `user`(str), `gift_name`(str), `count`(int), `value_coins`(int), `fansclub_level`(int 可选) | 礼物。`value_coins` 用于分级答谢；**单位是抖币，1:1**（2026-07-30 真实开播核对过实际价格） |

> `fansclub_level` = 粉丝团（灯牌）等级，0 或缺省＝没灯牌。主播定的回复分层里**灯牌 ≥8 级属于"必回且出声"**，所以要一路带到 dialogue。
> ⚠️ 它**不能**拿来判断会员/星守护：2026-07-30 真实抓包实测，观众字段里没有任何一项能标出这两个身份（`PayLevel` 是 0~40 的财富等级），只能靠主播在控制台手动勾选。
| `danmaku.like` | `user`(str), `count`(int) | 点赞 |
| `danmaku.follow` | `user`(str) | 关注/加粉团 |
| `danmaku.health` | `ok`(bool), `silent_ms`(int 可选), `connected`(bool 可选), `recovery`(str 可选) | **弹幕链路健康信号**，见下方说明 |

> `danmaku.health` 是 2026-08-02 真开播后新增的：那场开播 19 分钟后弹幕**突然断流 58 分钟**，
> 而抓包程序进程还活着、我方 WebSocket 连接也还在，主播全程不知情，一直到下播复盘才发现。
> 断流本身修不了（根因在第三方抓包程序，详见 CHANGELOG 同名一节），所以至少要让主播**当场
> 知道**。由 `perception-danmaku/run.py`（数据链路本身）发布：它是唯一知道"我多久没收到包"的人。
>
> ⚠️ 这**不违反**「验证员不能进数据链路」那条原则（见 memory `feedback_observer_not_in_datapath`）：
> 那条禁止的是"被观察者给自己的表现打分"，而这里报的是**自己输入断了**这个客观运行状态，
> 不是自评质量。真要连 run.py 整个死掉也能发现，靠的是 `main.js` 的 `service danmaku: exit` 日志
> 和控制台的服务状态灯，跟这条是两层不同的兜底。
>
> `ok:false` = 已经超过阈值没收到任何弹幕包；`ok:true` = 恢复收到了。`connected` 是我方到抓包
> 程序 WebSocket 的连接状态——**它为 true 而 ok 为 false，正是这次真实故障的特征**（连接好好的，
> 就是没数据），能一眼区分"抓包程序挂了"和"抓包程序活着但不出数据"这两种完全不同的故障。
>
> `recovery` 是自动恢复的进度：`trying`(正在重启抓包程序) / `ok`(重启完成，等直播伴侣重连) /
> `failed:<原因>` / `gaveup`(试满次数放弃)。⚠️ **`recovery:'ok'` 只表示"程序重启成功"，
> 不表示弹幕真回来了**——真回来一定是另发一条 `ok:true`，判断恢复只认那一条，别把这两个搞混。
| `face.expression` | `label`(str: neutral/happy/surprise/fear/sad/angry/disgust), `confidence`(number), `layout`(str: portrait/landscape 可选) | 主播露脸表情 |
| `game.scene` | `summary`(str), `tags`(array), `stuck`(bool), `hint`(str 可选), `sources`(array 可选) | 游戏画面理解（LLM，按需+周期）。`sources` 是攻略出处 `[{title,url}]`——**卡关攻略必须联网搜到真材料才会有这个字段，搜不到时宁可没有也绝不伪造**（理由见 `services/perception-game/vision.py` 顶部：模型在没有真材料时会编出互相矛盾的答案和假链接） |
| `game.scare` | `intensity`(number 0..1) | 本地快速惊吓强度（亮度/音量突变，低延迟） |
| `plugin.state` | `countdown_sec`(int 可选), `prank_count`(int 可选), … | 插件 OCR 解析状态 |
| `audio.command` | `intent`(str), `raw_text`(str), `speaker_verified`(bool) | 语音指令（**仅 speaker_verified=true 才采信**） |
| `audio.self_speaking` | `on`(bool) | 桌宠自己正在出声（true=开始合成/播放，false=播完）。**由 `apps/character` 发布**——它是唯一知道音频真实起止的一方。用途见下方「为什么需要 self_speaking」 |

### 为什么需要 `audio.self_speaking`

2026-07-29 实测到一个死循环：桌宠的 TTS 从音箱放出来，被麦克风原样收回去，声纹比对**没能挡住**
（合成音相似度过了阈值，而声纹本来就是用来分"主播 vs 别人"、不是分"人 vs 机器"的），于是被
当成主播的新话又回一句——一句接一句停不下来，只能杀进程。日志见 `startup.log` 14:14:43 起连续 5 轮。

所以 `services/perception-voice` 订阅这条事件，在桌宠出声期间**直接丢掉麦克风帧**（并在结束后
再多压制一小段尾巴，盖住音箱余响）。

两个设计选择，都是有意的：

1. **发布的是"我在出声"这个事实，不是"把麦克风关掉"这条命令。** 谁该拿它做什么由订阅方自己
   决定——以后若要在说话时也暂停别的东西（比如露脸检测），可以复用同一条事实，不用再加命令。
2. **走总线而不是父子进程私有通道。** `apps/character` 确实是 `perception-voice` 的父进程，用
   stdin 传更省事，但那样一来"单独跑 `python run.py`"或从旧面板起的语音进程就还是会自问自答。

⚠️ 这是**表现层往 `perception.*` 发布**的唯一一处（该频道其余都由感知层发布）。理由是它报告的
确实是一个可被感知的物理事实——本机此刻正在外放声音。订阅方必须自带超时保护：万一 `on:false`
因为渲染进程崩溃或总线断开而没送到，麦克风不能就此永久失聪（两侧都已各自实现，见
`main.js:GATE_MAX_MS` 与 `run.py:SpeakGate`）。

## `command.*`（控制层发布：面板 / 弹幕关键词 / 语音意图）

| type | data 字段 | 含义 |
|---|---|---|
| `mute` / `unmute` | — | 闭嘴 / 恢复 |
| `sleep` / `wake` | — | 休息（只 idle）/ 唤醒 |
| `do` | `action`(str) | 指定动作，如 wave/thank/scared |
| `mode` | `name`(str), `on`(bool) | 模式开关 |
| `calibrate` | `region`(str: game/face/plugin), `box`(object: x,y,w,h), `layout`(str: portrait/landscape) | 屏幕区域校准 |
| `set_viewer_tier` | `nickname`(str), `tier`(str: normal/member/star_guardian), `source`(str, 可选: manual/auto，缺省 manual) | 观众分级（`services/dialogue` 订阅写入自己的观众画像表；模块间不共享数据库文件，靠这条命令解耦）。两个发布方：`apps/control-panel` 主播手动打标；`services/perception-danmaku` 依据抓包数据里的粉丝团等级自动判定（`autotier.py`，带 `source:"auto"`）。**手动优先**——`memory.set_tier()` 遇到 auto 且该观众已被手动打过标时直接跳过，不覆盖 |
| `stream_start` / `stream_end` | — | 开播/下播。两个来源：`apps/control-panel` 手动按钮，以及 `services/perception-danmaku` 从直播伴侣抓取流里的 `101/102` 自动转发（见 `normalize.py:normalize_grab`）——发布方不同但语义一致，订阅方不需要区分。`stream_end` 后 `services/dialogue` 做本场收尾：清空本场送礼计数、把值得记住的常客信息沉淀进跨场记忆，见 `services/dialogue/SPEC.md` |

## `action.*`（决策层发布 → 表现层消费）

| type | data 字段 | 含义 |
|---|---|---|
| `play_motion` | `motion`(str) | 播放动作：idle/wave/scared/thank_small/thank_big/laugh/praise/beg/sleep |
| `set_expression` | `expression`(str) | 设置表情：happy/scared/smug/blush/surprised … |
| `show_bubble` | `text`(str), `duration_ms`(int 可选) | 文字气泡 |
| `speak` | `text`(str), `emotion`(str 可选), `voice`(str 可选) | TTS 说话（口型同步） |
| `stop` | — | 立即安静，仅保留 idle（对应"闭嘴/休息"） |

## 校验

`validate.py` 依据 `schema/*.json`（信封）+ 内置 `PAYLOAD_SPEC`（载荷）校验每条消息。
运行：`python packages/contract/validate.py <某.jsonl>`。
