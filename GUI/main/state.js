// ========== 主进程共享状态 ==========
// 各模块共用的可变状态（窗口引用/置顶/缩放/Token/WS 重连计时器等），
// 集中管理以避免模块间循环依赖。
const state = {
  isDev: process.argv.includes('--dev'),

  // 窗口引用（由 windows.js 创建时写入）
  sidebarWindow: null,
  live2dWindow: null,
  settingsWindow: null,
  chatWindow: null,
  tray: null,

  // 窗口行为状态
  alwaysOnTop: true,
  currentZoom: 1.0,
  sidebarVisible: true,
  // Live2D 是否贴靠状态面板（默认不停靠：Live2D 是独立主窗口，
  // 仅当用户显式点击"贴靠"且状态面板存在时才进入停靠状态）
  live2dDocked: false,
  // 应用是否正在退出（关闭 Live2D 窗口时据此判断隐藏到托盘还是真正关闭）
  isQuitting: false,

  // Agent WebSocket
  agentWebSocket: null,
  wsReconnectTimer: null,

  // Token 用量追踪
  tokenUsage: { total: 0, input: 0, output: 0 },

  // 状态快照批量推送缓冲
  stateBuffer: { emotion: null, status: null, token: null, connected: null },
  stateFlushTimer: null,
};

module.exports = state;
