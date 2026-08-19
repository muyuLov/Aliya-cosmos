// ========== App 生命周期 / 主进程组装 ==========
const { app } = require('electron');
const state = require('./state');
const { logger, closeLogStream, TERMINAL_ENCODING } = require('./logger');
const { createLive2DWindow, createSettingsWindow, showLive2DWindow } = require('./windows');
const { createTray } = require('./tray');
const { connectAgentWebSocket, closeAgentWebSocket } = require('./ws');
const { registerIpcHandlers } = require('./ipc');

app.whenReady().then(() => {
  logger.info('═══════ Aliya-cosmos GUI 启动 ═══════', { version: '0.2.0', dev: state.isDev });
  logger.info('运行环境', {
    platform: process.platform,
    arch: process.arch,
    node: process.versions.node,
    electron: process.versions.electron,
    encoding: TERMINAL_ENCODING,
  });

  // Windows 通知要求设置 AppUserModelID，否则通知可能无法正确显示应用身份
  if (process.platform === 'win32') {
    app.setAppUserModelId('Aliya-cosmos');
  }

  registerIpcHandlers();
  // Live2D 为程序主入口：启动即创建主窗口
  createLive2DWindow();
  createTray();
  connectAgentWebSocket();

  // 调试开关：ALIYA_OPEN_SETTINGS=1 时启动即打开设置窗口（冒烟测试用）
  if (process.env.ALIYA_OPEN_SETTINGS === '1') {
    setTimeout(() => {
      logger.info('调试模式：自动打开设置窗口');
      createSettingsWindow();
    }, 2500);
  }

  app.on('activate', () => {
    // 主入口语义：激活时显示或重建 Live2D 主窗口
    logger.info('应用被重新激活，显示/重建 Live2D 主窗口');
    showLive2DWindow();
  });
});

// 开始退出流程：放行 Live2D 窗口的 close（否则会被隐藏到托盘拦截，应用无法退出）
app.on('before-quit', () => {
  state.isQuitting = true;
});

app.on('window-all-closed', () => {
  logger.info('所有窗口已关闭');
  // 主入口语义：Live2D 已关闭且无其他窗口时退出应用；
  // 用户关闭 Live2D 通常被拦截为"隐藏到托盘"，只有真正退出流程才会走到这里
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  logger.info('═══════ Aliya-cosmos GUI 退出 ═══════');
  if (state.tray) {
    state.tray.destroy();
    state.tray = null;
  }
  closeAgentWebSocket();
  closeLogStream();
});
