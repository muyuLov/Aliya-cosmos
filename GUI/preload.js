// Preload 桥接 - 把受控的 IPC 暴露给渲染层
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sidebarAPI', {
  // 窗口控制
  minimize: () => ipcRenderer.invoke('sidebar:minimize'),
  close: () => ipcRenderer.invoke('sidebar:close'),
  togglePin: () => ipcRenderer.invoke('sidebar:toggle-pin'),
  isPinned: () => ipcRenderer.invoke('sidebar:is-pinned'),

  // 业务操作
  openChat: () => ipcRenderer.invoke('sidebar:open-chat'),
  switchModel: () => ipcRenderer.invoke('sidebar:switch-model'),
  openSettings: () => ipcRenderer.invoke('sidebar:open-settings'),
  getModel: () => ipcRenderer.invoke('sidebar:get-model'),
  getIdentity: () => ipcRenderer.invoke('sidebar:get-identity'),
  listProviders: () => ipcRenderer.invoke('sidebar:list-providers'),
  switchProvider: (name) => ipcRenderer.invoke('sidebar:switch-provider', name),

  // 情绪更新（从 agent WebSocket 推送）
  onEmotionChanged: (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on('sidebar:emotion', listener);
    return () => ipcRenderer.removeListener('sidebar:emotion', listener);
  },

  // 状态更新（从 agent WebSocket 推送）
  onStatusChanged: (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on('sidebar:status', listener);
    return () => ipcRenderer.removeListener('sidebar:status', listener);
  },

  // Token 用量查询与推送
  getTokenUsage: () => ipcRenderer.invoke('sidebar:get-token-usage'),
  onTokenUsageChanged: (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on('sidebar:token-usage', listener);
    return () => ipcRenderer.removeListener('sidebar:token-usage', listener);
  },

  // 事件订阅
  onEvent: (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on('sidebar:event', listener);
    return () => ipcRenderer.removeListener('sidebar:event', listener);
  },
});
