const { contextBridge, ipcRenderer } = require('electron');

// 控制台窗口的受限 API。渲染进程不直接碰 socket / child_process，全部经主进程代理。
contextBridge.exposeInMainWorld('consoleAPI', {
  publish: (msg) => ipcRenderer.invoke('bus-publish', msg),
  voiceStart: () => ipcRenderer.invoke('voice-start'),
  voiceStop: () => ipcRenderer.invoke('voice-stop'),
  micRun: (mode) => ipcRenderer.invoke('mic-run', mode),
  micStop: () => ipcRenderer.invoke('mic-stop'),
  // 绿幕模式（供直播采集）。set 会把桌宠带新参数重启——运行时改不了透明和硬件加速。
  streamModeGet: () => ipcRenderer.invoke('stream-mode-get'),
  streamModeSet: (on) => ipcRenderer.invoke('stream-mode-set', on),
  // 移出视野：只是挪窗口位置，即时生效不用重启
  offscreenGet: () => ipcRenderer.invoke('offscreen-get'),
  offscreenSet: (on) => ipcRenderer.invoke('offscreen-set', on),
  onMicEvent: (cb) => ipcRenderer.on('mic-event', (_e, evt) => cb(evt)),
  onBus: (cb) => ipcRenderer.on('bus-message', (_e, msg) => cb(msg)),
  onBusState: (cb) => ipcRenderer.on('bus-state', (_e, s) => cb(s)),
  onVoiceState: (cb) => ipcRenderer.on('voice-state', (_e, s) => cb(s)),
  onVoiceLog: (cb) => ipcRenderer.on('voice-log', (_e, line) => cb(line)),
});
