# perception-ocr — 直播插件感知

> 🚫 **已弃用（2026-07-29，用户决定）**：依赖 rapidocr-onnxruntime 未装，不启动。
> 代码保留，装回依赖 + 跑 verify_ocr.py 即可恢复。

## 职责
对直播插件显示区域做 OCR，解析结构化状态，发布 `perception.plugin.state`。

## 输入
- 插件区截图(mss)，区域由 `command.calibrate(region=plugin)` 提供（可多块）。
- 离线：`fixtures/plugin/sample-plugin-state.json`（raw_ocr 原文 + parsed 期望）。

## 输出
`plugin.state{countdown_sec?, prank_count?, …}`。

## 组成
- `parse.py`：文本 → 结构化(正则)，**与 OCR 引擎解耦**，不看图即可离线测/调。✅ **完成**。
- `ocr_client.py`：截图 → RapidOCR 出文字 → parse → plugin.state（变化才发）。⚠️ 当年验证通过，但 rapidocr-onnxruntime 现已不在环境中（已弃用，见顶部标注）。

## 依赖
`rapidocr-onnxruntime`（**未装**，已弃用）、`mss`、`numpy`、`Pillow`（已装）。

## 离线验证
`python services/perception-ocr/verify_ocr.py` = **8/8**（夹具"00:30:00"→1800、"整蛊 7 次"→7、多形态解析、契约合法、端到端剩 5 分钟→brain 提醒气泡）。
`python services/perception-ocr/verify_ocr_live.py` = **6/6**（PIL 合成中文插件图 → RapidOCR 真识别 → 解析 1800/7 → brain 提醒；自证，无需真实截图）。

## 里程碑
M8：**完成**（文本解析 + RapidOCR 真识别自证）。真实直播插件区可再调区域/阈值。

## 备注
倒计时里程碑(剩 5/1 分钟)由 brain 触发提醒；整蛊计数变化触发反应。
