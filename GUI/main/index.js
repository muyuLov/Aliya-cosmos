// ========== App 生命周期 / 主进程组装 ==========
const { app } = require('electron');
const state = require('./state');
const { logger, closeLogStream, TERMINAL_ENCODING } = require('./logger');
const { createLive2DWindow } = require('./windows');
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
  createLive2DWindow();
  createTray();
  connectAgentWebSocket();

  app.on('activate', () => {
    if (!state.live2dWindow || state.live2dWindow.isDestroyed()) {
      logger.info('应用被重新激活，重建 Live2D');
      createLive2DWindow();
    }
  });
});

app.on('window-all-closed', () => {
  logger.info('所有窗口已关闭');
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
