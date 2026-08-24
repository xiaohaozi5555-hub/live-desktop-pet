# 运行指南（离线可跑的"活的桌宠"）

**弹幕(夹具) → 归一化 → 决策 → 动作/语音/气泡** 全链路可离线跑，无需开播、无需系统改动。
最省事的方式：双击根目录的 `start-pet.cmd`（或 `启动桌宠.cmd`）。下面是分步骤的手动方式。

## 一、离线验证（一条命令看结果，无需 GUI）

```bash
python scripts/verify_contract.py            # 契约+夹具+mock总线
python services/brain/verify_brain.py        # 决策规则+状态机
python services/perception-danmaku/verify_danmaku.py  # 官方弹幕归一化+端到端
python scripts/verify_integration.py         # 真实 TCP 总线端到端
python scripts/verify_xruntime.py            # Python broker → Node 客户端(跨运行时)
python scripts/run_offline.py                # 弹幕→决策→action 文字演示（进程内）
```

各模块下还有对应的 `verify_*.py`（`services/perception-{game,ocr,face,voice}/`、`apps/control-panel/verify_control_panel.py`、`apps/character/verify_character.js`），跑法同上。

## 二、桌面上跑"活的桌宠"（看角色真实反应）

```bash
# 终端 A：起总线 + 决策，并按提示回放弹幕
python scripts/run_all.py

# 终端 B：起角色（透明置顶窗，自动连总线 127.0.0.1:8765）
cd apps/character && npm start
# 回到终端 A 按回车 → 桌宠随弹幕做 欢迎/答谢/被吓… 反应
```

组件也可分开起：`python services/bus/broker.py`、`python services/brain/run.py`、
`python services/perception-danmaku/run.py`（弹幕回放）、`npm start`（角色）。

## 三、直播伴侣接入

角色是透明置顶窗，在抖音直播伴侣里用「窗口捕获」指向它即可进直播画面。
实时弹幕：走官方「直播玩法/互动数据」，待你注册应用拿 APP ID 后接 `services/perception-danmaku/client.py` 的 `OfficialClient`（见 SPEC）。

## 端口

本地总线默认 `127.0.0.1:8765`（角色可用环境变量 `BUS_PORT` 覆盖）。纯本机回环，不对外。
