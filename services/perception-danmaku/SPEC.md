# perception-danmaku — 弹幕感知

## 职责
抓取抖音直播间的进场 / 评论 / 礼物 / 点赞 / 关注，归一化为 `perception.danmaku.*` 发布到总线。

## 输入（原始源）
- **主用：抖音开放平台「直播玩法 / 互动数据」官方长连接**（评论/礼物/点赞/进场）。合规、不改系统、顺带把桌宠塞进直播间。
  - 前置（主播侧动作，Agent 替不了）：开放平台注册 + 建「直播玩法」应用拿 **APP ID** + 申请「互动数据」能力；公开上线需软著 + 提审，**本地调试面板**可先开发。
  - 机制：`access_token` → 启动任务(roomID/token) → 官方长连接推送事件。
- 兜底：DouyinBarrageGrab（系统代理+根证书，需授权）/ dycast（输房间号，网页）。
- 离线：`fixtures/danmaku/sample-official.jsonl`（官方事件形态）、`sample-session.jsonl`（kind 形态）。

## 输出（发布）
`danmaku.enter` / `danmaku.chat` / `danmaku.gift`(含 value_coins) / `danmaku.like` / `danmaku.follow`。

## 组成
- `normalize.py`：归一化，含 `normalize_official`（官方事件）+ `normalize_fixture`（离线夹具，mock_bus 复用）+ 自动分派 `normalize`。字段名 TODO：拿到 SDK 后按官方互动数据文档校对。
- `client.py`：`feed_fixture`（离线，可用）+ `OfficialClient.connect`（实时骨架，待 APP ID 补齐握手与 `websockets` 依赖）。

## 计划依赖（待批准）
实时连接需 `websockets`（届时申请安装）；官方接入无需系统代理/证书。兜底方案才需 DouyinBarrageGrab/dycast。

## 离线夹具与验证
`python services/perception-danmaku/verify_danmaku.py` = **8/8**（官方事件→归一化→契约校验→Brain 端到端：大礼物→thank_big、进场→wave 欢迎；kind 夹具回归）。

## 里程碑
M3：**离线归一化 + 客户端骨架已完成（8/8，零系统改动）**；实时官方长连接待主播 APP ID / 能力开通后接入。

## 备注
昵称 `user` 供"欢迎 / 求礼物话术"；礼物 `value_coins`（官方钻/抖币价值 × 数量）供分级答谢。
