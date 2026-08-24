# perception-face — 露脸表情感知

## 职责
截取主播露脸区域，识别表情，发布 `perception.face.expression`，供决策层调侃/夸夸、跟随反应。

## 输入
- 露脸区域截图（`mss`），区域由 `command.calibrate(region=face)` 提供，支持 **portrait(竖屏下半) / landscape(横屏左下角)** 两套预设。
- 离线：合成/桩标签用于逻辑自测。真实性已用用户提供的 3 张真实露脸照片验证（见下）。

## 输出
`face.expression{label ∈ neutral/happy/surprise/fear/sad/angry/disgust, confidence, layout}`。

## 组成
- `expression.py`：DeepFace 情绪 → 契约 face.expression 归一化（纯函数）。✅ 已自测。
- `detect.py`：`analyze_face()` 用 DeepFace 分析截图 → face.expression。✅ **已装并真实验证**。
- `run.py`：mss 低帧率截露脸区 → detect → 发 face.expression。

## 依赖（已安装）
`deepface`、`opencv-python-headless`(**需 <5 版本**，见下)、`mediapipe`(已装，未采用，见下)、`tf-keras`；`mss`/`numpy` 已装。

## Windows 踩坑与修复

1. **opencv-python-headless 5.0.0 不带 haarcascade 数据文件**（`cv2/data/` 下缺 xml，新版本打包结构变了）→ 降级到 `opencv-python-headless<5`(实装 4.13.0) 找回数据文件。
2. **mediapipe 0.10.35 已移除旧版 `mediapipe.solutions` API**，与 DeepFace 的 mediapipe 检测器适配层不兼容（`AttributeError: module 'mediapipe' has no attribute 'solutions'`）→ 放弃 mediapipe 路线，改回 opencv 检测器并修复其真正问题（见下）。
3. **核心 bug（已修复）**：本机项目路径含中文字符（`D:\调研claude与code\...`）。DeepFace 的 `OpenCvClient.__get_opencv_path()` 用 `os.path.dirname(cv2.__file__)` 现算 haarcascade 路径（不读 `cv2.data.haarcascades` 属性，直接补丁该属性无效），OpenCV 的 `CascadeClassifier` 用窄字符 API 打开中文路径文件会**静默失败**(`empty()==True`，仅打印 stderr 不抛异常)，导致检测不到人脸、DeepFace 退化为对整张截图(含背景)做情绪分析，结果不稳定且可信度低。**修复**：`detect.py` 运行时把 haarcascade `*.xml` 复制到纯 ASCII 路径 `D:/cv2data/`，并 monkeypatch `deepface.models.face_detection.OpenCv.OpenCvClient._OpenCvClient__get_opencv_path` 返回该路径。修复前后对比：置信度从 0.45–0.99(不稳定，含整图背景) 变为 0.87–0.99(稳定，真正裁剪人脸区域后判断)，且标签结果发生实质变化（如 sample_3 从 fear→sad）。

## 真实验证（用户提供 3 张真实露脸照片，已完成）

- 用户直接在对话中贴了 3 张真实自拍截图（webcam 风格，196×115 等小尺寸）。
- 修复前（未裁剪人脸，含游戏椅背景）：`fear(0.45) / sad(0.44) / fear(0.999)`——低置信度、不稳定。
- **修复 opencv 中文路径 bug 后（正确裁剪人脸区域）**：`neutral(0.87) / neutral(0.99) / sad(0.88)`。
- 端到端接 Brain：sample_3(sad) 正确触发安慰反应 `"别怕别怕，有宝宝陪着你呢！"`；两张 neutral 按设计不触发反应（避免对着普通表情持续瞎评论）。
- 如实记录：sample_2 照片肉眼看是张嘴(像惊讶/喊叫)，DeepFace 判成高置信度 neutral——真实的模型局限，不是编造的完美结果。
- **隐私处理**：3 张照片仅用于本次验证，验证完成后已从 `fixtures/face/` 删除（该目录 png/jpg 本就 `.gitignore`，从未入库），已告知用户可删除桌面原始截图。

## 离线验证
`python services/perception-face/verify_face.py` = **7/7**（情绪→表情映射 + 契约合法 + 竖屏/横屏布局 + 端到端 fear/happy → brain 反应 + deepface 依赖存在性提示）。

## 里程碑
M6：**完成**（归一化/接线/真实识别/端到端/Windows 路径 bug 均已验证与修复）。决策层"听你指挥/闭嘴"由 command/语音 覆盖（已在 M2/M4/M5 实现）。
