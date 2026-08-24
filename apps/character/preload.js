const { contextBridge, ipcRenderer } = require('electron');

// 渲染进程可用的受限 API（contextIsolation 下的安全桥）
contextBridge.exposeInMainWorld('petAPI', {
  // 请求 TTS：main 进程调 edge-tts 合成，返回 base64 data URI
  speak: (text, voice) => ipcRenderer.invoke('speak', { text, voice }),
  // 告诉 main「这一句真的播完了」——main 据此解除掐麦（见 main.js 的 setSelfSpeaking）。
  // 只有渲染进程知道 <audio> 什么时候 ended，所以这个回报不能省。
  speakDone: () => ipcRenderer.send('speak-done'),
  // 接收 main 下发的渲染指令（capture/demo 模式用）
  onCommand: (cb) => ipcRenderer.on('command', (_e, msg) => cb(msg)),
  // 接收来自总线的 action.*（brain 决策结果）
  onAction: (cb) => ipcRenderer.on('action', (_e, msg) => cb(msg)),
});
