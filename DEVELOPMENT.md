# 开发须知

给接手/继续开发这个项目的人看。读这份 + [ARCHITECTURE.md](ARCHITECTURE.md) + [RUNNING.md](RUNNING.md) + [INTEGRATION.md](INTEGRATION.md) 就能上手。

## 项目位置与隔离环境

- Python：项目根下 `.venv\`，**所有 Python 命令都用 `.venv\Scripts\python.exe`**，不要用系统 Python。
- Node/Electron：`apps\character\node_modules\`。
- `.venv`、`node_modules`、`.env`、`voiceprint.npy`、`startup.log` 都是本机状态，不在 git 里（见 `.gitignore`）；换机器需按下面"环境搭建"重建。

## 环境搭建

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# torch 的 CPU 版本不在默认 PyPI 索引，单独装：
.venv\Scripts\python.exe -m pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu

# resemblyzer 必须 --no-deps，否则会连带尝试从源码编译真正的 webrtcvad（需要 MSVC，本机通常没装）：
.venv\Scripts\python.exe -m pip install Resemblyzer==0.1.4 --no-deps

cd apps\character
npm install
```

如果 Electron 二进制从 GitHub 拉取失败，换国内镜像重装一次：

```powershell
$env:ELECTRON_MIRROR="https://npmmirror.com/mirrors/electron/"
node node_modules\electron\install.js
```

## 私密文件（gitignore，不入库，勿外传）

- `.env`：视觉后端 API key（见 `.env.example`）。
- `services/perception-voice/voiceprint.npy`：主播声纹派生数据。

## 已知环境坑

- **Windows 长路径**：项目路径深 + torch 内部深层目录可能超 260 字符（`WinError 206`）。修复：开注册表 `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1`（本机级、可逆）。
- **非 ASCII 路径下 OpenCV 检测失败**：如果项目路径包含中文等非 ASCII 字符，OpenCV 的 `CascadeClassifier` 用窄字符 API 打开该路径下的 haarcascade 文件会**静默失败**（`empty()==True`），DeepFace 因此检测不到人脸、退化为对整图判断，结果不稳定。`services/perception-face/detect.py` 已经自带修复：把 haarcascade xml 复制到 `D:/cv2data/`（纯 ASCII、不占 C 盘）并 monkeypatch DeepFace 的路径解析方法；这段代码用 `str.isascii()` 自我判断是否需要触发，路径本身是 ASCII 时不会做任何多余动作，不需要手动开关。
- **依赖版本坑**：`opencv-python-headless` 必须 `<5`（5.0 起不带 haarcascade 数据文件，`requirements.txt` 已锁定 4.13）；`mediapipe 0.10.35` 已移除 DeepFace 依赖的旧版 `solutions` API，DeepFace 的人脸检测请用默认 opencv backend，不要切换到 mediapipe backend。
- **模型/权重统一放 D 盘**：`services/perception-face/detect.py` 顶部把 `DEEPFACE_HOME` 定向到 `D:/datalab`；faster-whisper 走机器级 `HF_HOME=D:\datalab`。换机器记得把这些环境变量/路径指到某个 ASCII、非 C 盘的目录。
- **PowerShell 中文乱码**：Python 脚本入口统一 `sys.stdout.reconfigure(encoding='utf-8')`；批处理脚本(.cmd)开头加 `chcp 65001 >nul`。
- **批处理脚本(.cmd)里调 npm**：必须写 `call npm ...`，不能直接写 `npm ...`——因为 `npm` 实际上是 `npm.cmd`，不加 `call` 会导致父脚本被提前终止、后续命令不再执行。

## 从其他同类原型借鉴的经验

开发过程中对照过另一份桌宠原型（同样的需求，不同实现路径：单体 Electron + React + PixiJS，六大能力全部用事件模拟器模拟，未接真实感知）。它在"真实用户能不能双击打开"这件事上踩过一些坑，有几条被吸收进了本项目：

- **Electron 窗口启动诊断**：`apps/character/main.js` 现在把窗口生命周期事件（created/show/hide/closed/did-finish-load/did-fail-load/render-process-gone/console-message）落盘到 `startup.log`，主播反馈"打不开/看不见"时可以直接看日志而不是猜。
- **窗口越界纠正**：如果记住的窗口坐标落在当前屏幕可见区域之外（比如换了显示器），启动时自动纠正回主屏。
- **弹幕关键词身份校验**：对照那份原型的 `commandPermissions` 模型后发现，本项目的弹幕关键词控制通道（`apps/control-panel/keywords.py`）原来没有任何身份校验——任何观众打出"闭嘴"之类的关键词都能让桌宠静音。现在只有 `STREAMER_NAMES`/`MODERATOR_NAMES` 白名单内的昵称才会生效（本机 CLI/GUI 面板不受此限制，视为可信输入）。
- **一键启动脚本**：`start-pet.cmd`（+ 中文名 `启动桌宠.cmd` 转调）一次性拉起总线/决策/角色三个窗口，降低多终端操作门槛；ASCII 主脚本 + Chinese 名转调脚本这个两层结构也是从那边学的，用来规避批处理脚本文件名编码问题。
- **AI 未配置时的角色化提示**：视觉后端没配置 key 时，`services/perception-game/vision.py` 现在返回一句角色口吻的提示（桌宠会说出来），而不是只抛异常在你看不到的终端里打印。

没有采用的部分：它的"感知能力"全部是控制台按钮模拟出来的假事件，没有一处接了真实数据，这块完全没有参考价值；架构上它是单体应用，换来的是启动简单，代价是牺牲了本项目契约解耦、模块独立验证、可替换升级这些核心设计，也不采用。

## 关键文件指路

- 全局：[ARCHITECTURE.md](ARCHITECTURE.md)（架构）、[RUNNING.md](RUNNING.md)（运行）、[INTEGRATION.md](INTEGRATION.md)（直播伴侣接入 + 各模块启用）、[CHANGELOG.md](CHANGELOG.md)（能力状态）。
- 提审/密钥：`抖音玩法提审规则要点.md`、`获取密钥与APPID.md`、`.env.example`。
- 契约（改代码前必读）：`packages/contract/events.md`。
- 一键启动：`start-pet.cmd`（`scripts/win/` 下是它调用的分组件启动脚本）。
