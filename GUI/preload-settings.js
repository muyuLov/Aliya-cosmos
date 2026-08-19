// Preload 桥接 - 设置窗口
const { contextBridge, ipcRenderer } = require('electron');

// 订阅工厂：减少订阅函数中的重复模式
function makeSubscriber(channel) {
  return (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld('settingsAPI', {
  // 窗口控制
  minimize: () => ipcRenderer.invoke('settings:minimize'),
  close: () => ipcRenderer.invoke('settings:close'),
  windowDragMove: (dx, dy) => ipcRenderer.invoke('settings:drag-move', dx, dy),

  // 配置读取（一次性快照：身份/模型/服务/提供商列表）
  getConfig: () => ipcRenderer.invoke('settings:get-config'),

  // 身份信息保存（ai_name / user_name → main.yml 定点写入，保留注释）
  saveIdentity: (identity) => ipcRenderer.invoke('settings:save-identity', identity),

  // 提供商列表 / 切换（复用状态面板通道）
  listProviders: () => ipcRenderer.invoke('sidebar:list-providers'),
  switchProvider: (name) => ipcRenderer.invoke('sidebar:switch-provider', name),

  // 实时状态订阅（Token / WS 连接 / 情绪状态快照）
  onStateSnapshot: makeSubscriber('settings:state-snapshot'),
});
