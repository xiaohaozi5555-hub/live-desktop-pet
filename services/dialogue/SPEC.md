# dialogue — LLM 对话服务

## 定位

让桌宠能跟弹幕/主播真聊天，不再是 `brain` 里那种纯模板台词。**跟 `brain` 是平级关系，不是上下级**——两者都直接订阅总线上已经在广播的 `perception.danmaku.*` / `command.*` / `perception.audio.command`，各自独立维护状态、独立判断要不要反应，互不指挥。核心的"该不该回复"判断**不需要新增事件类型**（`danmaku.chat` 有 `user`+`text`；`danmaku.gift` 有 `value_coins`；`audio.command` 有 `intent`+`raw_text`+`speaker_verified`，都够用）。

> 2026-07-22 更新：分级录入（`command.set_viewer_tier`）和开播/下播信号（`command.stream_start`/`stream_end`）这两个跨模块协作点，契约**加了两个新事件**（见 `packages/contract/events.md` 的 `command.*` 表），下面两节详细说明。

dialogue 自己维护：本场送礼排名（从 `danmaku.gift` 累加）、当前模式 ACTIVE/QUIET/SLEEP（从 `command.mute/unmute/sleep/wake` 镜像，不查 brain）。

## 反应叠加原则（重要）

dialogue 产出的反应是**叠加**在 `brain` 现有即时模板反应之上的，**不是替换**。例：送礼答谢——`brain` 的即时动作+模板答谢台词照旧瞬间触发（不接入LLM，保留低延迟）；dialogue 稍后再发一条个性化 `action.speak` 追加。两条 `action.speak` 前后脚发，character 端需要能顺序播放不互相打断（这个排队逻辑现在 apps/character 没有，需要一起补，见下方"需要联动改的地方"）。

## 谁值得触发 LLM 回复（判断都在 dialogue 内部，纯规则，不调 LLM）

优先级从高到低，同时命中多条时只回一次：

1. **主播批量指令**："看一下弹幕帮我回一下"（走语音，见下）→ 抓最近 10 条弹幕依次回复。
2. **星守护 / 会员**（`tier` 字段，主播手动在 `apps/control-panel` 打标，界面是"从本场已出现的观众昵称里勾选"而不是手打字防止输错；面板发布 `command.set_viewer_tier{nickname,tier}`，dialogue 订阅后写自己的 `viewers` 表——两边不共享数据库文件，靠这条命令解耦）→ 可回复。
3. **本场送礼前三名** → 可回复（`danmaku.gift.value_coins` 累加排名，dialogue 自己算，不依赖 brain）。
4. **默认**：不逐条回弹幕。主要模式是「跟主播语音聊天」（常开，见下）。

节流：即使命中 2/3，也要有冷却（同一人不连续回、每分钟回复条数有上限）——TTS 播放本身是瓶颈，回复排队会让人觉得"卡"。具体数值现在直播间规模小（<50 人，会员+星守护 <10 人），先用宽松的默认值，不需要复杂的动态限流。

## 语音聊天（唤醒词"魔丸魔丸"）

`services/perception-voice` 已有唤醒词基础设施（`intents.py` 的 `WAKE_WORD` 环境变量 + 模糊匹配），设 `PET_WAKE_WORD=魔丸` 即可复用，不用重新造。

- ~~唤醒后开一个对话窗口（一段时间内不需要重复喊"魔丸魔丸"就能继续对话）~~ **2026-08-01 撤销**：真开播实测发现，主播跟观众正常讲话的语速经常快过窗口时长，窗口会被跟桌宠不相干的话不断续上，那些话被当成聊天内容转发给 DeepSeek，白白耗 token。改成**每句都要喊一次唤醒词**，没有隐藏倒计时——`ConversationWindow` 类已从 `intents.py` 删除。
- 唤醒词+声纹校验通过后，`raw_text` 不再局限于现有固定 intent 表（mute/sleep/wake/walkthrough），命中不了固定 intent 的，转发给 dialogue 当自由聊天文本处理。这是对 `intents.py` 路由逻辑的**扩展**，不是重写——固定 intent 该匹配还是优先匹配（比如"闭嘴"还是走 mute，不会被当成聊天内容）。

### 卡关攻略：显式触发 + 二次确认

跟自动卡关检测（`perception-game/vision.py` 现有的）**并存**，不互斥。语音触发流程：

```
主播："魔丸魔丸，我卡关了"
魔丸："卡关啦？需要我来提供攻略帮助吗？"
主播："是的"                          → 触发 vision.py 的 LLM 分析
主播：（说别的 / 沉默）                 → 不触发，回一句兜底："好吧，是我敏感了哈哈我主人果然厉害"
```

二次确认是为了防止主播只是在跟弹幕闲聊时提到"卡关"两个字被误触发（直播时这种误触发比日常用手机助手更尴尬）。确认状态（"我刚问过，等答案"）由 dialogue 自己管，不需要契约层支持。

## 记忆：三层，一个 SQLite 文件，不需要 FTS5/向量检索

参考 Hermes Agent 的分层思路，但规模小得多（<50观众/场），做了大幅减法：

1. **人设/主播档案** — 不是数据库，是写死的 prompt 模板常量（角色是谁、语气、红线规则、主播是谁）。这层要保持稳定，不需要"agent自己学着改"这种复杂度。
2. **本场弹幕缓冲** — 内存里存最近 N 条即可（给"看一下弹幕"批量模式用），不需要跨会话持久化。
3. **观众画像表**（SQLite，跨场持久化）：

   ```sql
   CREATE TABLE viewers (
     nickname TEXT PRIMARY KEY,
     tier TEXT DEFAULT 'normal',   -- normal / member / star_guardian（主播手动打标）
     gift_total_session INTEGER DEFAULT 0,
     last_seen_ts INTEGER,
     note TEXT                     -- 简短印象，自由文本，见下方安全说明
   );
   ```

   回复某个观众前查一条（同步本地查询即可，不需要 Hermes 那种后台异步预取——量级小，查询本身是毫秒级），回复后更新一条。**单次 LLM 调用只带一个人的一小段记录，不会随场次时长/观众数增长**——这是解决"上下文会不会满"的结构性答案，不是靠临时裁剪。

## 开播/下播：session 生命周期（2026-07-22 新增）

主播手动触发（不做自动检测），控制面板发 `command.stream_start` / `command.stream_end`，dialogue 订阅处理：

- **`stream_start`**：重置本场作用域的内存状态——`_gift_rank`（送礼排名）、`_chat_buffer`（弹幕缓冲）、节流计数器全部清空。这一步是必须的：如果 dialogue 进程跨场不重启（比如常驻后台），没有这个信号，"本场送礼前三名"会算成"从进程启动至今"而不是"这一场"，边界就乱了。
- **`stream_end`**：先做**收尾沉淀**，再清空：
  1. 遍历本场出现过的观众（`_gift_rank` 的 key 集合 + 本场 `_chat_buffer` 里出现过的昵称），对每个人：`sessions_seen` 计数 +1（新增字段，跨场累计、永不重置，用来判断"来过几次"）；`gift_total_session` 累加进 `gift_total_lifetime`（新增字段，跨场累计、永不重置，`gift_total_session` 才是单场用的、会清零的那个）。
  2. **常客判定**（规则，不用 LLM）：`sessions_seen >= 3` 或 `gift_total_lifetime` 超过某阈值或本来就是 member/star_guardian 的，写一条**结构化、由代码拼装**的 `note`（例如"常客，已来 5 场，单场最高送礼 3000 抖币"），不是让 LLM 自由发挥写印象——这条 note 的信息来源是我们自己的数据库统计，不是弹幕原文，所以不用担心注入问题，可以放心自动生成。
  3. 清零 `gift_total_session`（单场计数器归零，`gift_total_lifetime`/`sessions_seen` 不动）。
- **主播的"我的习惯"**：这个跟观众画像是两回事——主播自己的偏好/习惯是**主播本人的输入，可信**，不受"弹幕是不可信输入"这条限制。v1 建议最简单地做：`streamer_notes` 一张单独的小表（或者干脆一个文本文件），主播自己在控制面板里直接写/改，不需要 LLM 自动总结这么复杂。等这个简单版跑顺了，v2 可以考虑"下播时 dialogue 起草一段总结草稿，主播确认/编辑后再存"，人在环上，不要让它自己悄悄写。

schema 相应加两个字段：

```sql
ALTER TABLE viewers ADD COLUMN sessions_seen INTEGER DEFAULT 0;       -- 跨场累计，不随 stream_end 清零
ALTER TABLE viewers ADD COLUMN gift_total_lifetime INTEGER DEFAULT 0; -- 跨场累计，不随 stream_end 清零
```

✅ **已实现**（2026-07-23）：`_command()` 把 `set_viewer_tier`/`stream_start`/`stream_end` 分派到三个新方法。`set_viewer_tier` 调 `memory.set_tier()`——新函数，不复用 `upsert_viewer()`（它的语义明确是"不碰 tier"）。`stream_start` 直接清空 `_gift_rank`/`_chat_buffer`/`_last_reply_ts`/`_reply_times`。`stream_end` 遍历 `_gift_rank` 的 key 并集 `_chat_buffer` 里的昵称（不用另外查数据库），按"来过 ≥3 场 / 跨场送礼 ≥5000 抖币（`REGULAR_GIFT_LIFETIME_THRESHOLD`，SPEC 原文没给具体数字，先用这个默认值）/ 本来就是 member 或 star_guardian"三选一判定常客，写结构化 note（用 `memory.sediment_session()`，同一次写顺带把 `gift_total_session` 归零）。note 文案用的是"已来 N 场，累计送礼 M 抖币"而不是上面例句里的"单场最高送礼"——schema 只加了 `sessions_seen`/`gift_total_lifetime` 这两个字段，没有单独跟踪"历史单场最高送礼"，例句里那个措辞只是举例，不是字段要求。schema 变更直接写进 `CREATE TABLE IF NOT EXISTS`（不是真的跑 `ALTER TABLE`）——这是 dev 阶段，没有需要迁移的历史数据。

⏳ **主播的"我的习惯"（`streamer_notes`）还没做**——这次任务范围是观众画像的 session 生命周期，主播自己的偏好记录是单独一块，还没排期。

## 运行时不能卡住：LLM 调用必须挪出总线接收线程（2026-07-22 新增，重要）

`services/bus/bus_client.py` 现在是**单线程顺序处理**：一条消息的所有回调跑完，才收下一条。`Dialogue.handle()` 如果同步调用 DeepSeek（尤其是"看一下弹幕"批量模式，一次最多 10 次顺序调用，每次最长等到 `timeout=10s`），会导致这段时间里**所有其他事件都排队等着**——包括"闭嘴"这种最高优先级的语音指令，直播时会很尴尬。这个问题不止批量模式有，**任何一次单条 LLM 回复调用**都会短暂阻塞整条总线的处理。

评估过两个思路，都不是根本解法：
- **滑动窗口/缩小批量**：能降低最坏情况（比如批量条数从10降到5），但没解决"单线程同步阻塞"这个根因，只是缩小影响范围。
- **Prompt caching（Claude的提示词缓存）**：不适用——① 这里用的是 DeepSeek 不是 Claude，这个功能挂不上；② 就算挂得上，缓存解决的是"重复大段前缀的处理成本/延迟"，我们的人设prompt本来就很短，缓存收益有限；③ 缓存不解决"一个线程顺序等N次网络请求"这个并发问题，就算每次调用都缓存命中、瞬间返回，N次顺序调用还是要排队执行，只是每次更快而已，量大的时候依然会叠加延迟。

**推荐做法**：生产者-消费者模式，把所有 LLM 调用挪到后台 worker 线程，总线接收线程只做"值不值得回复"这类快速规则判断，不等 LLM：

1. `Dialogue.handle()` 只做规则判断（分级/排名/节流/命中哪个触发路径），命中了就把"要回复给谁、回什么"这个小任务塞进一个线程安全队列（`queue.Queue()`），立刻返回——不等 DeepSeek。
2. **v1 先用一个（只有一个，不并发）后台 worker 线程**消费这个队列：查记忆 → 拼prompt → 调DeepSeek → 过护栏 → 把生成好的 `action.speak` 放进另一个"待发布"队列。批量模式（5-10条）一个 worker 顺序处理，单条大概 1-3 秒（`deepseek-v4-flash` 非思考模式、短回复、非流式调用的合理估算，未接真实 key 实测过，实测数字以 `verify_dialogue.py` 真实调用为准），5条约5-15秒内依次生成完，不是同时完成——**这是已知的、v1 阶段接受的权衡**，不是遗漏。原因：① 总线接收线程不再被卡住，最紧急的问题已经解决；② 桌宠说话本身也是逐句顺序播放（TTS 排队），就算生成端并发做快了，观众听到的节奏还是受"说话"这个环节限制，并发生成的实际收益比看起来小；③ 不并发就不需要给节流计数器加锁，代码更简单、没有竞态风险（⚠️ 这条原本还写了"数据库读写"，判断不完整，见下方"订正"——数据库那把锁 v1 现在就需要，不是升级到多 worker 才需要）。
3. `run.py` 现在的 `threading.Event().wait()` 换成一个循环，从"待发布"队列里取消息就 `client.publish()`——发布本身很快，不会阻塞。
4. 这样总线接收线程**永远不会因为等 DeepSeek 而卡住**，批量模式也不用特殊处理——就是一次性塞 10 个任务进队列，worker 顺序消化，其间新弹幕/新礼物/闭嘴指令照样能被总线接收线程实时处理。

**升级路径（先不做，真跑起来发现批量模式太慢再考虑）**：加到 2-3 个并发 worker。届时需要给节流计数器（`_last_reply_ts`/`_reply_times`）额外加锁，防止多个 worker 同时读到"还没到上限"、一起放行导致节流失效——这两个计数器只在总线接收线程里读写，现在（单 worker）没有跨线程访问，不存在这个风险，不需要现在就加锁。

> **订正**（2026-07-23，实现时发现）：上面这条原本把 `memory.py` 的锁也归进"多 worker 才需要"，判断不完整——**哪怕只有一个 worker，`memory.py` 的锁现在就需要**。原因：总线接收线程本身也会直接查/写观众表（`_danmaku_gift` 记账、`_danmaku_chat` 查 tier），worker 线程生成回复时也要查/写，这两类线程会并发访问同一个 `sqlite3.Connection`，Python 的 sqlite3 连接对象不是无锁并发安全的——实现时 `verify_dialogue.py` 连跑几次就复现了一次 `sqlite3.OperationalError: not an error`。已修：`Dialogue` 加了 `self._db_lock`，所有 `self._db` 访问统一走 `_get_viewer`/`_upsert_viewer` 两个加锁的私有方法，不再有任何调用点绕过它直接摸 `self._db`。

✅ **已实现**（2026-07-23）：`Dialogue.__init__` 启动唯一一个后台 daemon worker 线程；判断"值得回复"的路径（送礼前三名答谢、会员/星守护弹幕回复、语音自由聊天、`review_chat` 批量模式）都改成 `self._task_queue.put(...)` 入队后立刻 `return []`，不再同步调 `chat.reply()`；worker 消费队列生成 `action.speak` 后过一遍契约校验，放进 `self.outbox`；`run.py` 把原来的 `threading.Event().wait()` 换成 `while True: client.publish(dlg.outbox.get())`。唯一不走队列、保持同步的是卡关二次确认的反问/确认/拒绝三句——它们是静态模板不调 LLM，本来就快，不需要挪。另外顺手给 `services/bus/bus_client.py` 的 `publish()` 加了个 `threading.Lock()`：新架构下总线接收线程（发即时 action）和主线程（从 outbox 发 worker 生成的 action）会并发调用同一个 socket 的 `publish()`，不加锁有极小概率交错写坏 TCP 流；这个锁对单线程调用方无副作用，`brain/run.py` 的 ticker+接收双线程发布场景也顺带更安全了。`Dialogue` 也加了 `self._db_lock`（见下方"订正"），`self._db` 的每个读写点都统一走 `_get_viewer`/`_upsert_viewer` 两个加锁方法，没有遗漏的裸访问。

## 模型：DeepSeek `deepseek-v4-flash`

非思考模式，`$0.14/1M` 输入 token，1M 上下文，支持 function calling/JSON 输出。**不用 `deepseek-v4-pro`**（贵约3倍，给复杂推理场景用，我们纯中文口语短回复用不上）。API 是 OpenAI 兼容协议，复用 `services/perception-game/vision.py` 里已经验证过的调用模式（同一套代码结构，换 base_url/key/model，去掉图片输入）。Key 见 `.env` 的 `PET_CHAT_*`（`.env.example` 已加好占位）。

## 合规护栏（硬性前提，不是锦上添花）

之前的纯模板系统里，说出口的每一句话都是我们自己写的，零风险。**接入 LLM 现场生成之后，第一次有了"取决于观众打了什么字"这个变量**——理论上有观众故意打一段话，试图让桌宠说出不合规内容（提示词注入的一种），直播现场真人观众在看，风险比一般聊天机器人更现实。

不能只靠"在提示词里叮嘱模型不要怎样"。**生成完之后必须过一道确定性检查**再决定要不要念出来：查一遍 `提案Demo自查清单.md` 里那份官方禁用词清单（游戏、玩家、抽奖、盲盒、礼盒、BUFF、HP、PVP 等），不过关就换一句安全兜底模板，不重试冒险。同一份清单，之前是给人工审 demo 用的，现在多一个运行时自动挡一道的用途。

## 失败兜底

DeepSeek 调用超时/出错：参照 `perception-game/vision.py` 现有做法（未配置视觉后端时用角色口吻提示而不是报错），退回一句安全模板话，不报错不卡死、不让桌宠沉默尴尬。

## 需要联动改的地方（不在本模块目录内，做的时候留意）

- `services/perception-voice/intents.py`：✅ **已完成**。新增 `ConversationWindow`（唤醒后免重复唤醒词的对话窗口）+ `to_audio_command(..., window=...)`，命中不了固定 intent 时转发 `raw_text` 给 dialogue（`intent='chat'`）。顺带把"卡关"类关键词的产出值从 `walkthrough` 改成了 `walkthrough_ask`——原来的 `walkthrough` 会被 `perception-game/run.py` 直接截图分析，没有二次确认这一步；改名后语音说"卡关"只会先经 dialogue 反问确认，确认了才由 dialogue 发 `command.do{action:'walkthrough'}` 触发真正的分析（这条 perception-game 本来就在监听，不用改它）。新增了 `review_chat`（"看一下弹幕帮我回一下"）意图。`verify_voice.py` 同步更新（18/18 通过，含新增的窗口/转发测试）。`gift_begging` 那一条已经在中控台这轮清掉了。
- `apps/character`：`action.speak` 需要能排队播放（brain 的即时反应 + dialogue 的追加反应前后脚到达时不能互相打断）——**这个文件目前被 `feature/character-look` worktree 占用**，改之前跟那边协调一下，别冲突。⏳ **本轮未动**，`renderer.js` 现在 `speak()` 是新调用直接打断上一条，需要时另行协调。

## 不需要的复杂度（参考 Hermes Agent 调研后明确排除的）

- 不需要多平台消息网关（Telegram/Discord等）——桌宠只活在直播间里。
- 不需要通用工具注册表/多模型供应商抽象——我们就一个文本模型（DeepSeek）+ 已有的一个视觉模型（Qwen-VL，vision.py 那边）。
- 不需要自主技能生成/自我进化——人设要稳定可控，不要让它自己长出不可预期的行为，这对直播现场和提审合规都不友好。
- 观众画像的 `note`（印象）字段：只让规则/代码拼装结构化内容写入（见上方"开播/下播"一节的常客判定），不让 LLM 自己写自由文本印象——弹幕是不可信输入，别让观众通过聊天内容注入进一段会被反复喂回 LLM 的持久化文本里。

## 离线验证

`python services/dialogue/verify_dialogue.py` = **52/52 通过**（不需要真调用 API 也能验证大部分逻辑，参考 `perception-game/verify_vision.py` "key 外部问题记 WARN 不判失败"的做法）。覆盖：分级判断规则（会员/星守护可回、送礼前三名可回、默认不逐条回）、送礼排名计算、节流冷却、记忆表读写、禁用词护栏拦截（含与 `提案Demo自查清单.md` 官方清单的一致性检查，清单更新了这里会自动提醒）、卡关二次确认状态机（确认/不确认/超时三种分支）、模式镜像、DeepSeek 未配置 key 时的角色口吻兜底、**"运行时不能卡住"的后台 worker 队列架构**（把 `chat.reply` 临时换成人为 sleep 1.5s 的慢版本，断言 `handle()` 仍几乎瞬时返回、批量模式排队中"闭嘴"指令不受阻塞、慢任务最终仍在 `outbox` 里正确生成）、**分级录入解耦**（`set_viewer_tier` 写自己的 `viewers` 表 + 端到端打标后弹幕真的被回复）、**开播/下播 session 生命周期**（`stream_start` 清空本场内存状态；`stream_end` 三种常客判定条件各给了独立用例，含一个"都不满足、不该判常客"的对照组，外加单场计数器归零、跨场计数器保留的断言）。`.env` 配好 `PET_CHAT_*` 后重跑本脚本会额外真调一次 DeepSeek（未配置只 SKIP，不算失败）。另外用真实 broker + `run.py` 进程做过两轮端到端冒烟（送礼触发异步回复；`stream_start`→`set_viewer_tier`→打标观众发弹幕→收到回复→`stream_end`，全走真实 TCP 总线），不止离线单测。

骨架实现：`persona.py`（人设常量）/ `memory.py`（SQLite viewers 表）/ `guardrails.py`（禁用词护栏）/ `chat.py`（DeepSeek 调用，未配置 key 或调用失败都返回角色口吻兜底，不抛异常）/ `dialogue.py`（核心决策类 + 后台 worker 队列）/ `run.py`（连总线运行器，跟 `brain/run.py` 同构，主循环从 `dlg.outbox` 阻塞取消息发布）。

## 里程碑

骨架 + 离线验证：**完成**（2026-07-22）。并发架构（单 worker 队列，运行时不卡总线）：**完成**（2026-07-23）。分级录入解耦 + 开播/下播 session 生命周期（`set_viewer_tier`/`stream_start`/`stream_end`）：**完成**（2026-07-23）。待办：① `.env` 配 `PET_CHAT_*`（DeepSeek key）才能真实生成对话，未配置时会自动走角色口吻兜底，不影响其它逻辑跑通；② `apps/character` 的 `action.speak` 排队播放，待与 `feature/character-look` 协调；③ 主播的"我的习惯"（`streamer_notes`）还没做，见上方"开播/下播"一节；④ `perception-voice/run.py` 的真实麦克风循环仍是骨架（`NotImplementedError`），要接通"魔丸"语音聊天的真实音频链路需要先把那个循环实现打通（不在本轮范围）。
