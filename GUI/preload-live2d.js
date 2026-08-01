// Preload 桥接 - Live2D 窗口
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('live2dAPI', {
  minimize: () => ipcRenderer.invoke('live2d:minimize'),
  close: () => ipcRenderer.invoke('live2d:close'),
  togglePin: () => ipcRenderer.invoke('sidebar:toggle-pin'),
  isPinned: () => ipcRenderer.invoke('sidebar:is-pinned'),
  toggleSidebar: () => ipcRenderer.invoke('sidebar:toggle-visibility'),
  snapToSidebar: () => ipcRenderer.invoke('live2d:snap-to-sidebar'),
  openChat: () => ipcRenderer.invoke('sidebar:open-chat'),
  openSettings: () => ipcRenderer.invoke('sidebar:open-settings'),
  switchModel: () => ipcRenderer.invoke('sidebar:switch-model'),
  listProviders: () => ipcRenderer.invoke('sidebar:list-providers'),
  switchProvider: (name) => ipcRenderer.invoke('sidebar:switch-provider', name),
  windowDragMove: (dx, dy) => ipcRenderer.invoke('live2d:drag-move', dx, dy),
  // 侧边栏状态变更通知
  onSidebarState: (handler) => {
    const listener = (_evt, visible) => handler(visible);
    ipcRenderer.on('live2d:sidebar-state', listener);
    return () => ipcRenderer.removeListener('live2d:sidebar-state', listener);
  },
  // 口型同步：接收 TTS 音频特征数据驱动 Live2D 参数
  // 数据格式：{volume: 0~1, centroid: 0~1, zcr: 0~1}
  onMouthOpen: (handler) => {
    const listener = (_evt, data) => handler(data);
    ipcRenderer.on('live2d:mouth-open', listener);
    return () => ipcRenderer.removeListener('live2d:mouth-open', listener);
  },
});
