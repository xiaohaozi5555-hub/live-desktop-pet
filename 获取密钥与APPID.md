# 获取密钥与 APP ID 指引（对应第 1、2 条）

## 一、国产多模态 key（第 1 条 · 卡关攻略/场景理解用；最易上手推荐 Qwen-VL）

**阿里云百炼 DashScope（通义千问 Qwen-VL）**
1. 打开 `https://bailian.console.aliyun.com/` → 用阿里云账号登录、完成实名（有免费额度）。
2. 左侧「API-KEY」→ 创建 API-KEY，复制 `sk-...`。
3. 复制 `.env.example` 为 `.env`，填：
   ```
   PET_VISION_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
   PET_VISION_API_KEY=sk-你的key
   PET_VISION_MODEL=qwen-vl-max
   ```
4. 验证：`python services/perception-game/verify_vision.py` —— 会真调模型分析一张图并输出 game.scene。

**备选（任选其一，替换上面三行）**
- 智谱 GLM-4V：`open.bigmodel.cn` 拿 key → `BASE_URL=https://open.bigmodel.cn/api/paas/v4`、`MODEL=glm-4v-plus`。
- 豆包 Doubao：火山引擎方舟 `console.volcengine.com/ark` → `BASE_URL=https://ark.cn-beijing.volces.com/api/v3`、MODEL 用你的「推理接入点」名。
- Kimi：`platform.moonshot.cn` → `BASE_URL=https://api.moonshot.cn/v1`、MODEL 用其 vision 型号。

> 同一个 DashScope key 还能做**文生图**（通义万相），用于第 5 条角色生成 `gen_character.py`（把 `PET_IMAGE_*` 填成同样的 base_url/key，MODEL 用 `wan2.2-t2i-flash`；或用智谱 `cogview-3-plus`）。

## 二、抖音开放平台 APP ID（第 2 条 · 实时弹幕；**我无法替你注册**）

为什么替不了：注册需要**你的抖音号登录 + 实名/开发者资质**（个人可做，公开上线还需软著+提审），属于你的身份与账号操作，我没有也不能代持。步骤：
1. 打开 `https://developer.open-douyin.com/` → 右上「登录」用你的抖音号扫码。
2. 「控制台 → 创建应用」→ 应用类目选「**直播玩法 / 互动玩法**」（即直播伴侣里那类插件）。
3. 拿到 **AppID / AppSecret**。
4. 应用「能力」里申请「**互动数据 / 评论互动**」能力（拿评论/礼物/点赞推送）。
5. 用**直播伴侣的调试面板**填入 AppID + 玩法启动地址，即可本地联调（无需先提审）；公开上线再走软著+提审。
6. 把 AppID/Secret 给我，我把 `services/perception-danmaku/client.py:OfficialClient` 的长连接握手补齐并联调。

## 三、其余
- 第 3 条（语音）：你录几句话（wav），我用 `speaker.enroll` 注册声纹 → 装 ASR/声纹后"只认你"生效。
- 第 4 条（露脸）：你发几张露脸截图（竖屏/横屏），我装 deepface 调表情准确率。
- 第 5 条（角色）：见 `assets/character/gen_character.py`（用上面的国产文生图 key 生成整套立绘）。
