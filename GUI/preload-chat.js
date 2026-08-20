// Preload 桥接 - 聊天窗口
const { contextBridge, ipcRenderer } = require('electron');

// 订阅工厂：减少订阅函数中的重复模式
function makeSubscriber(channel) {
  return (handler) => {
    const listener = (_evt, payload) => handler(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld('chatAPI', {
  // 窗口控制
  minimize: () => ipcRenderer.invoke('chat:minimize'),
  close: () => ipcRenderer.invoke('chat:close'),
  windowDragMove: (dx, dy) => ipcRenderer.invoke('chat:drag-move', dx, dy),

  // 消息发送（经主进程 agent WS 中转；失败返回 { success:false, error }）
  sendMessage: (text) => ipcRenderer.invoke('chat:send-message', text),

  // 当前连接状态查询（窗口挂载时拉取，避免事件驱动快照遗漏初始状态）
  getState: () => ipcRenderer.invoke('chat:get-state'),

  // 停止当前回复
  stop: () => ipcRenderer.invoke('chat:stop'),

  // 工具执行确认（confirm_request 的响应；callId 为后端挂起确认的标识）
  confirmResponse: (allowed, callId) => ipcRenderer.invoke('chat:confirm', Boolean(allowed), callId),

  // AI 回复（旧协议 brain_complete.reply，兼容保留）
  onReply: makeSubscriber('chat:reply'),

  // 流式回复（新协议 text_message_* 系列）
  onStreamStart: makeSubscriber('chat:stream-start'),
  onStreamDelta: makeSubscriber('chat:stream-delta'),
  onStreamEnd: makeSubscriber('chat:stream-end'),
  // 回复回合结束（run_finished，兜底解除 busy）
  onRunFinished: makeSubscriber('chat:run-finished'),
  // 工具调用过程
  onToolStart: makeSubscriber('chat:tool-start'),
  onToolEnd: makeSubscriber('chat:tool-end'),

  // 工具执行确认请求（tool/params）
  onConfirmRequest: makeSubscriber('chat:confirm-request'),

  // 后端错误 / 通知（含 stop 后的"已停止回复"）
  onError: makeSubscriber('chat:error'),
  onNotice: makeSubscriber('chat:notice'),

  // 实时状态订阅（连接状态，与侧边栏/设置同源）
  onStateSnapshot: makeSubscriber('chat:state-snapshot'),
});
