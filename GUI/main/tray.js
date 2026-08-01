// ========== 系统托盘 ==========
const { Tray, Menu, app } = require('electron');
const path = require('path');
const fs = require('fs');
const state = require('./state');
const { logger } = require('./logger');
const { getIdentity } = require('./config');
const { createLive2DWindow } = require('./windows');

const TRAY_ICON = path.join(__dirname, '..', 'src', 'assets', 'cosmos.ico');

/** 当前是否任一窗口可见（托盘菜单文案依据） */
function isAnyWindowVisible() {
  return state.live2dWindow && !state.live2dWindow.isDestroyed() && state.live2dWindow.isVisible();
}

function updateTrayMenu() {
  if (!state.tray) return;
  const isVisible = isAnyWindowVisible();
  const label = isVisible ? '隐藏' : '显示';
  const contextMenu = Menu.buildFromTemplate([
    {
      label,
      click: () => toggleSidebarVisibility(),
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

function toggleSidebarVisibility() {
  const isVisible = isAnyWindowVisible();
  if (isVisible) {
    // 隐藏所有窗口
    if (state.sidebarWindow && !state.sidebarWindow.isDestroyed()) state.sidebarWindow.hide();
    if (state.live2dWindow && !state.live2dWindow.isDestroyed()) state.live2dWindow.hide();
    state.sidebarVisible = false;
  } else {
    // 只显示 Live2D 窗口
    if (!state.live2dWindow || state.live2dWindow.isDestroyed()) {
      createLive2DWindow();
    } else {
      state.live2dWindow.show();
      if (state.live2dWindow.isMinimized()) state.live2dWindow.restore();
    }
    // 确保侧边栏保持隐藏
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

    // 左键单击切换状态面板可见性
    state.tray.on('click', () => {
      toggleSidebarVisibility();
    });
    logger.info('系统托盘已创建');
  } catch (e) {
    logger.warn('系统托盘创建失败', { error: e.message });
  }
}

module.exports = { createTray, toggleSidebarVisibility, updateTrayMenu };
