// ========== 系统托盘 ==========
// 主入口语义：Live2D 窗口关闭时隐藏到托盘，托盘是后台运行时的唯一入口。
const { Tray, Menu, app } = require('electron');
const path = require('path');
const fs = require('fs');
const state = require('./state');
const { logger } = require('./logger');
const { getIdentity } = require('./config');
const { isAnyWindowVisible, showLive2DWindow } = require('./windows');

const TRAY_ICON = path.join(__dirname, '..', 'src', 'assets', 'cosmos.ico');

function updateTrayMenu() {
  if (!state.tray) return;
  const isVisible = isAnyWindowVisible();
  const label = isVisible ? '隐藏' : '显示';
  const contextMenu = Menu.buildFromTemplate([
    {
      label,
      click: () => toggleVisibility(),
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.quit();
      },
    },
  ]);
  state.tray.setContextMenu(contextMenu);
}

function toggleVisibility() {
  if (isAnyWindowVisible()) {
    // 隐藏所有窗口（Live2D 主入口 + 状态面板 + 设置窗口）
    if (state.live2dWindow && !state.live2dWindow.isDestroyed()) state.live2dWindow.hide();
    if (state.sidebarWindow && !state.sidebarWindow.isDestroyed()) state.sidebarWindow.hide();
    if (state.settingsWindow && !state.settingsWindow.isDestroyed()) state.settingsWindow.hide();
    state.sidebarVisible = false;
  } else {
    // 显示 Live2D 主窗口（不存在则重建）
    showLive2DWindow();
    // 确保状态面板保持隐藏（主入口即 Live2D）
    if (state.sidebarWindow && !state.sidebarWindow.isDestroyed()) state.sidebarWindow.hide();
    state.sidebarVisible = false;
  }
  logger.debug('系统托盘切换可见性', { sidebarVisible: state.sidebarVisible });
  updateTrayMenu();
}

function createTray() {
  // 托盘图标存在时才创建
  if (!fs.existsSync(TRAY_ICON)) {
    logger.warn('托盘图标文件不存在，跳过托盘创建');
    return;
  }
  try {
    const { aiName } = getIdentity();
    state.tray = new Tray(TRAY_ICON);
    state.tray.setToolTip(aiName || 'Aliya');
    updateTrayMenu();

    // 左键单击切换可见性
    state.tray.on('click', () => {
      toggleVisibility();
    });
    logger.info('系统托盘已创建');
  } catch (e) {
    logger.warn('系统托盘创建失败', { error: e.message });
  }
}

module.exports = { createTray, toggleVisibility, updateTrayMenu };
