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
    skipTaskbar: false,
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
  sidebarWindow.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    const tag = ['LOG', 'WARN', 'ERROR'][level] || `L${level}`;
    logger.info(`[sidebar:${tag}] ${message} (${sourceId}:${line})`);
  });

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
    // 通知 Live2D 窗口更新按钮状态
    if (state.live2dWindow && !state.live2dWindow.isDestroyed()) {
      state.live2dWindow.webContents.send('live2d:sidebar-state', false);
    }
  });
}

function calcLive2DRect() {
  if (!state.sidebarWindow || state.sidebarWindow.isDestroyed()) return null;
  const [sbX, sbY] = state.sidebarWindow.getPosition();
  const display = screen.getDisplayNearestPoint({ x: sbX, y: sbY });
  const wa = display.workArea;

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
function dockLive2D() {
  state.live2dDocked = true;
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
  const rect = calcLive2DRect() || {
    x: 0, y: 80, width: LIVE2D_WIDTH, height: LIVE2D_HEIGHT,
  };
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

  live2dWindow.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    const tag = ['LOG', 'WARN', 'ERROR'][level] || `L${level}`;
    logger.info(`[live2d:${tag}] ${message} (${sourceId}:${line})`);
  });

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

module.exports = {
  createSidebarWindow,
  createLive2DWindow,
  calcLive2DRect,
  syncLive2DPosition,
  dockLive2D,
};
