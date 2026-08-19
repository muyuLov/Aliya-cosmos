// ========== 窗口管理 ==========
const { BrowserWindow, screen, shell } = require('electron');
const path = require('path');
const state = require('./state');
const { logger } = require('./logger');

const SIDEBAR_WIDTH = 320;
const SIDEBAR_HEIGHT = 720;
// Live2D 独立透明窗口尺寸（紧贴状态面板左侧，与状态面板等高）
const LIVE2D_WIDTH = 360;
const LIVE2D_HEIGHT = 720;
const LIVE2D_GAP = 8; // Live2D 窗口与状态面板之间的间距
// 设置窗口（非透明常规窗口，居中于主显示器）
const SETTINGS_WIDTH = 640;
const SETTINGS_HEIGHT = 660;
// 聊天窗口（独立对话窗口，居中于主显示器）
const CHAT_WIDTH = 420;
const CHAT_HEIGHT = 560;

// ========== 渲染进程日志捕获（含第三方噪音降噪） ==========
// pixi-live2d-display / pixi.js 7.x 加载模型时会产生已知的弃用警告
// （utils.url.resolve、options.autoInteract 等）及一长串堆栈追踪。
// 这些是第三方库的无害噪音，统一降为 debug 记录，避免污染 ERROR 日志。

/** 已知第三方弃用/信息性消息片段（命中即降噪） */
const NOISE_PATTERNS = [
  'utils.url.resolve is deprecated',
  'options.autoInteract is deprecated',
  'PixiJS Deprecation Warning',
  'Live2D Cubism SDK Core Version',
  '[CSM][I]',              // Cubism Core INFO 级输出
  'CubismFramework.startUp',
  'CubismFramework.initialize',
];

/** 堆栈追踪帧（"    at ..."），用于抑制弃用警告后的堆栈 */
const STACK_FRAME_RE = /^\s+at\s+/;

/**
 * 创建渲染进程 console 捕获处理器（含噪音抑制状态机）。
 * @param {string} tag 窗口标签（sidebar/live2d/settings）
 */
function makeConsoleLogger(tag) {
  let suppressingStack = false;
  return (_e, level, message, line, sourceId) => {
    const levelTag = ['LOG', 'WARN', 'ERROR'][level] || `L${level}`;

    // 处于堆栈抑制中：后续 "at ..." 帧继续抑制，遇到非堆栈行退出抑制
    if (suppressingStack) {
      if (STACK_FRAME_RE.test(message)) {
        logger.debug(`[${tag}:${levelTag}] (suppressed stack) ${message} (${sourceId}:${line})`);
        return;
      }
      suppressingStack = false;
    }

    // 命中已知噪音 → 降为 debug，并开启堆栈抑制
    if (NOISE_PATTERNS.some((p) => message.includes(p))) {
      suppressingStack = true;
      logger.debug(`[${tag}:${levelTag}] (third-party noise) ${message} (${sourceId}:${line})`);
      return;
    }

    logger.info(`[${tag}:${levelTag}] ${message} (${sourceId}:${line})`);
  };
}

function createSidebarWindow() {
  logger.info('正在创建侧边栏窗口', { width: SIDEBAR_WIDTH, height: SIDEBAR_HEIGHT });
  const display = screen.getPrimaryDisplay();
  const { width: screenWidth } = display.workArea;
  const x = Math.max(0, screenWidth - SIDEBAR_WIDTH - 16);
  const y = 80;

  const sidebarWindow = new BrowserWindow({
    width: SIDEBAR_WIDTH,
    height: SIDEBAR_HEIGHT,
    x,
    y,
    minWidth: 280,
    minHeight: 560,
    frame: false,
    transparent: true,
    resizable: state.isDev,
    skipTaskbar: true,        // 不在任务栏显示（与 Live2D 主窗口一致，托盘是唯一入口）
    alwaysOnTop: state.alwaysOnTop,
    hasShadow: true,
    backgroundColor: '#00000000',
    title: '状态',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: state.isDev,
    },
  });

  state.sidebarWindow = sidebarWindow;
  sidebarWindow.loadFile(path.join(__dirname, '..', 'dist', 'sidebar.html'));

  sidebarWindow.once('ready-to-show', () => {
    logger.info('侧边栏窗口已显示', { x, y });
    sidebarWindow.show();
    // 侧边栏重建后，让停靠的 Live2D 回到贴靠位置
    if (state.live2dDocked) syncLive2DPosition();
  });

  // 侧边栏被拖动时，停靠的 Live2D 实时跟随
  sidebarWindow.on('moved', () => {
    if (state.live2dDocked) syncLive2DPosition();
  });

  // 捕获 renderer 进程的 console 输出（含 vue/pixi 报错）写入主进程日志
  sidebarWindow.webContents.on('console-message', makeConsoleLogger('sidebar'));

  sidebarWindow.webContents.on('render-process-gone', (_e, details) => {
    logger.error('侧边栏渲染进程崩溃', { reason: details.reason, exitCode: details.exitCode });
  });

  sidebarWindow.on('unresponsive', () => {
    logger.warn('侧边栏窗口无响应');
  });

  sidebarWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    logger.error('侧边栏页面加载失败', { code, desc, url });
  });

  sidebarWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  sidebarWindow.on('closed', () => {
    logger.info('侧边栏窗口已关闭');
    state.sidebarWindow = null;
    // 状态面板不存在了，解除停靠（避免"无侧边栏仍停靠"的残留状态）
    state.live2dDocked = false;
    // 通知 Live2D 窗口更新按钮状态（侧边栏开关 + 贴靠高亮）
    if (state.live2dWindow && !state.live2dWindow.isDestroyed()) {
      state.live2dWindow.webContents.send('live2d:sidebar-state', false);
      state.live2dWindow.webContents.send('live2d:docked-state', false);
    }
  });
}

function calcLive2DRect() {
  // Live2D 是程序主入口：即使状态面板未创建，也返回独立主窗口位置
  // （屏幕右侧垂直居中，而不是落到角落）
  const display = screen.getPrimaryDisplay();
  const wa = display.workArea;

  if (!state.sidebarWindow || state.sidebarWindow.isDestroyed()) {
    const x = wa.x + wa.width - LIVE2D_WIDTH - 16;
    const y = wa.y + Math.max(0, Math.round((wa.height - LIVE2D_HEIGHT) / 2));
    return { x, y, width: LIVE2D_WIDTH, height: LIVE2D_HEIGHT };
  }

  const [sbX, sbY] = state.sidebarWindow.getPosition();

  // 默认贴靠侧边栏左侧；若左侧放不下（靠近屏幕左缘），改贴到右侧
  let x = sbX - LIVE2D_WIDTH - LIVE2D_GAP;
  if (x < wa.x) {
    x = sbX + SIDEBAR_WIDTH + LIVE2D_GAP;
  }
  x = Math.max(wa.x, Math.min(x, wa.x + wa.width - LIVE2D_WIDTH));
  const y = Math.max(wa.y, Math.min(sbY, wa.y + wa.height - LIVE2D_HEIGHT));

  return { x, y, width: LIVE2D_WIDTH, height: LIVE2D_HEIGHT };
}

// 进入停靠状态并贴靠到侧边栏
// 仅当状态面板存在时停靠才有意义；不存在则先创建（ready-to-show 后自动吸附）
function dockLive2D() {
  state.live2dDocked = true;
  if (!state.sidebarWindow || state.sidebarWindow.isDestroyed()) {
    logger.info('贴靠请求：状态面板不存在，先创建');
    createSidebarWindow();
    return;
  }
  syncLive2DPosition();
}

// 同步停靠位置（仅处于停靠状态时生效）
function syncLive2DPosition() {
  if (!state.live2dDocked) return;
  if (!state.live2dWindow || state.live2dWindow.isDestroyed()) return;
  const rect = calcLive2DRect();
  if (!rect) return;
  state.live2dWindow.setBounds(rect);
}

function createLive2DWindow() {
  const rect = calcLive2DRect();
  logger.info('正在创建 Live2D 窗口', rect);

  const live2dWindow = new BrowserWindow({
    width: rect.width,
    height: rect.height,
    x: rect.x,
    y: rect.y,
    frame: false,
    transparent: true,
    resizable: false,
    skipTaskbar: true,        // 不在任务栏显示
    alwaysOnTop: state.alwaysOnTop,
    hasShadow: false,
    backgroundColor: '#00000000',
    title: 'Aliya · Live2D',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload-live2d.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: state.isDev,
    },
  });

  state.live2dWindow = live2dWindow;
  live2dWindow.loadFile(path.join(__dirname, '..', 'dist', 'live2d.html'));

  live2dWindow.once('ready-to-show', () => {
    logger.info('Live2D 窗口已显示');
    live2dWindow.show();
  });

  // 主入口语义：用户关闭 Live2D 窗口时，默认隐藏到托盘继续后台运行；
  // 仅当应用真正退出（isQuitting）时才真正关闭窗口
  live2dWindow.on('close', (e) => {
    if (state.isQuitting) return; // 应用退出中，放行
    logger.info('Live2D 窗口关闭 → 隐藏到托盘');
    e.preventDefault();
    live2dWindow.hide();
  });

  live2dWindow.webContents.on('console-message', makeConsoleLogger('live2d'));

  live2dWindow.webContents.on('render-process-gone', (_e, details) => {
    logger.error('Live2D 渲染进程崩溃', { reason: details.reason, exitCode: details.exitCode });
  });

  live2dWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    logger.error('Live2D 页面加载失败', { code, desc, url });
  });

  live2dWindow.on('closed', () => {
    logger.info('Live2D 窗口已关闭');
    state.live2dWindow = null;
  });
}

/** 是否任一窗口可见（Live2D 主入口 / 状态面板 / 设置窗口 / 聊天窗口） */
function isAnyWindowVisible() {
  const check = (win) => Boolean(win && !win.isDestroyed() && win.isVisible());
  return check(state.live2dWindow) || check(state.sidebarWindow)
    || check(state.settingsWindow) || check(state.chatWindow);
}

/** 显示 Live2D 主窗口（不存在则重建），用于托盘/通知唤起 */
function showLive2DWindow() {
  if (!state.live2dWindow || state.live2dWindow.isDestroyed()) {
    createLive2DWindow();
    return;
  }
  state.live2dWindow.show();
  if (state.live2dWindow.isMinimized()) state.live2dWindow.restore();
  state.live2dWindow.focus();
}

// ========== 设置窗口（单例：已存在则聚焦） ==========

function createSettingsWindow() {
  // 单例复用：窗口已存在时仅显示并聚焦
  if (state.settingsWindow && !state.settingsWindow.isDestroyed()) {
    state.settingsWindow.show();
    if (state.settingsWindow.isMinimized()) state.settingsWindow.restore();
    state.settingsWindow.focus();
    logger.debug('设置窗口已存在，聚焦');
    return;
  }

  const display = screen.getPrimaryDisplay();
  const { workArea } = display;
  const x = Math.round(workArea.x + (workArea.width - SETTINGS_WIDTH) / 2);
  const y = Math.round(workArea.y + (workArea.height - SETTINGS_HEIGHT) / 2);
  logger.info('正在创建设置窗口', { x, y, width: SETTINGS_WIDTH, height: SETTINGS_HEIGHT });

  const settingsWindow = new BrowserWindow({
    width: SETTINGS_WIDTH,
    height: SETTINGS_HEIGHT,
    x,
    y,
    minWidth: 460,
    minHeight: 560,
    frame: false,
    transparent: true,
    resizable: true,
    skipTaskbar: true,        // 不在任务栏显示（与 Live2D/状态面板一致，托盘是唯一入口）
    alwaysOnTop: false,
    hasShadow: true,
    backgroundColor: '#00000000',
    title: '设置',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload-settings.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: state.isDev,
    },
  });

  state.settingsWindow = settingsWindow;
  settingsWindow.loadFile(path.join(__dirname, '..', 'dist', 'settings.html'));

  settingsWindow.once('ready-to-show', () => {
    logger.info('设置窗口已显示');
    settingsWindow.show();
  });

  settingsWindow.webContents.on('console-message', makeConsoleLogger('settings'));

  settingsWindow.webContents.on('render-process-gone', (_e, details) => {
    logger.error('设置渲染进程崩溃', { reason: details.reason, exitCode: details.exitCode });
  });

  settingsWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    logger.error('设置页面加载失败', { code, desc, url });
  });

  settingsWindow.on('closed', () => {
    logger.info('设置窗口已关闭');
    state.settingsWindow = null;
  });
}

// ========== 聊天窗口（单例：已存在则聚焦） ==========

function createChatWindow() {
  // 单例复用：窗口已存在时仅显示并聚焦
  if (state.chatWindow && !state.chatWindow.isDestroyed()) {
    state.chatWindow.show();
    if (state.chatWindow.isMinimized()) state.chatWindow.restore();
    state.chatWindow.focus();
    logger.debug('聊天窗口已存在，聚焦');
    return;
  }

  const display = screen.getPrimaryDisplay();
  const { workArea } = display;
  const x = Math.round(workArea.x + (workArea.width - CHAT_WIDTH) / 2);
  const y = Math.round(workArea.y + (workArea.height - CHAT_HEIGHT) / 2);
  logger.info('正在创建聊天窗口', { x, y, width: CHAT_WIDTH, height: CHAT_HEIGHT });

  const chatWindow = new BrowserWindow({
    width: CHAT_WIDTH,
    height: CHAT_HEIGHT,
    x,
    y,
    minWidth: 340,
    minHeight: 420,
    frame: false,
    transparent: true,
    resizable: true,
    skipTaskbar: true,        // 不在任务栏显示（托盘是唯一入口）
    alwaysOnTop: false,
    hasShadow: true,
    backgroundColor: '#00000000',
    title: 'Aliya · 聊天',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload-chat.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: state.isDev,
    },
  });

  state.chatWindow = chatWindow;
  chatWindow.loadFile(path.join(__dirname, '..', 'dist', 'chat.html'));

  chatWindow.once('ready-to-show', () => {
    logger.info('聊天窗口已显示');
    chatWindow.show();
  });

  chatWindow.webContents.on('console-message', makeConsoleLogger('chat'));

  chatWindow.webContents.on('render-process-gone', (_e, details) => {
    logger.error('聊天渲染进程崩溃', { reason: details.reason, exitCode: details.exitCode });
  });

  chatWindow.webContents.on('did-fail-load', (_e, code, desc, url) => {
    logger.error('聊天页面加载失败', { code, desc, url });
  });

  chatWindow.on('closed', () => {
    logger.info('聊天窗口已关闭');
    state.chatWindow = null;
  });
}

module.exports = {
  createSidebarWindow,
  createLive2DWindow,
  createSettingsWindow,
  createChatWindow,
  calcLive2DRect,
  syncLive2DPosition,
  dockLive2D,
  isAnyWindowVisible,
  showLive2DWindow,
};
