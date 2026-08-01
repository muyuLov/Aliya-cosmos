// Preload 桥接 - 把受控的 IPC 暴露给渲染层
const { contextBridge, ipcRenderer } = require('electron');

// 订阅工厂：减少订阅函数中的重复模式
function makeSubscriber(channel) {
  return (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld('sidebarAPI', {
  // 窗口控制
  minimize: () => ipcRenderer.invoke('sidebar:minimize'),
  close: () => ipcRenderer.invoke('sidebar:close'),
  togglePin: () => ipcRenderer.invoke('sidebar:toggle-pin'),
  isPinned: () => ipcRenderer.invoke('sidebar:is-pinned'),
  windowDragMove: (dx, dy) => ipcRenderer.invoke('sidebar:drag-move', dx, dy),

  // 业务操作
  openChat: () => ipcRenderer.invoke('sidebar:open-chat'),
  switchModel: () => ipcRenderer.invoke('sidebar:switch-model'),
  openSettings: () => ipcRenderer.invoke('sidebar:open-settings'),
  getModel: () => ipcRenderer.invoke('sidebar:get-model'),
  getIdentity: () => ipcRenderer.invoke('sidebar:get-identity'),
  listProviders: () => ipcRenderer.invoke('sidebar:list-providers'),
  switchProvider: (name) => ipcRenderer.invoke('sidebar:switch-provider', name),

  // 统一状态快照（情绪/状态/Token 由主进程合并后批量推送）+ 事件订阅
  onStateSnapshot: makeSubscriber('sidebar:state-snapshot'),
  onEvent: makeSubscriber('sidebar:event'),

  // Token 用量查询
  getTokenUsage: () => ipcRenderer.invoke('sidebar:get-token-usage'),
});
