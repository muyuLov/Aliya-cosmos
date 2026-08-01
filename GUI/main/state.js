// ========== 主进程共享状态 ==========
// 各模块共用的可变状态（窗口引用/置顶/缩放/Token/WS 重连计时器等），
// 集中管理以避免模块间循环依赖。
const state = {
  isDev: process.argv.includes('--dev'),

  // 窗口引用（由 windows.js 创建时写入）
  sidebarWindow: null,
  live2dWindow: null,
  tray: null,

  // 窗口行为状态
  alwaysOnTop: true,
  currentZoom: 1.0,
  sidebarVisible: true,

  // Agent WebSocket
  agentWebSocket: null,
  wsReconnectTimer: null,

  // Token 用量追踪
  tokenUsage: { total: 0, input: 0, output: 0 },

  // 状态快照批量推送缓冲
  stateBuffer: { emotion: null, status: null, token: null },
  stateFlushTimer: null,
};

module.exports = state;
