// ========== IPC 处理器注册 ==========
const { ipcMain } = require('electron');
const fs = require('fs');
const state = require('./state');
const { logger } = require('./logger');
const {
  PROVIDERS_FILE,
  MAIN_YML,
  getCurrentProviderName,
  getModelConfig,
  getIdentity,
} = require('./config');
const { createSidebarWindow, dockLive2D, syncLive2DPosition } = require('./windows');

function registerIpcHandlers() {
  // ========== 窗口拖拽 ==========
  ipcMain.handle('live2d:drag-move', (_evt, dx, dy) => {
    if (!state.live2dWindow || state.live2dWindow.isDestroyed()) return;
    // 用户手动拖动 Live2D → 解除停靠（侧边栏移动不再带动它）
    if (state.live2dDocked) {
      state.live2dDocked = false;
      logger.debug('Live2D 拖动，解除停靠');
      state.live2dWindow.webContents.send('live2d:docked-state', false);
    }
    const [x, y] = state.live2dWindow.getPosition();
    state.live2dWindow.setPosition(x + dx, y + dy);
  });

  ipcMain.handle('sidebar:drag-move', (_evt, dx, dy) => {
    if (!state.sidebarWindow || state.sidebarWindow.isDestroyed()) return;
    const [x, y] = state.sidebarWindow.getPosition();
    state.sidebarWindow.setPosition(x + dx, y + dy);
    // 拖动结束前同步停靠位置（moved 事件兜底）
    if (state.live2dDocked) {
      syncLive2DPosition();
    }
  });

  // 贴靠到侧边栏：恢复停靠状态并吸附
  ipcMain.handle('live2d:snap-to-sidebar', () => {
    dockLive2D();
    return state.live2dDocked;
  });

  ipcMain.handle('live2d:is-docked', () => state.live2dDocked);

  // ========== 窗口控制 ==========
  ipcMain.handle('sidebar:minimize', () => {
    state.sidebarWindow?.minimize();
  });

  ipcMain.handle('sidebar:close', () => {
    state.sidebarWindow?.close();
  });

  ipcMain.handle('live2d:close', () => {
    state.live2dWindow?.close();
  });

  ipcMain.handle('live2d:minimize', () => {
    state.live2dWindow?.minimize();
  });

  ipcMain.handle('sidebar:toggle-pin', () => {
    state.alwaysOnTop = !state.alwaysOnTop;
    state.sidebarWindow?.setAlwaysOnTop(state.alwaysOnTop);
    state.live2dWindow?.setAlwaysOnTop(state.alwaysOnTop);
    logger.debug('置顶状态切换', { alwaysOnTop: state.alwaysOnTop });
    return state.alwaysOnTop;
  });

  ipcMain.handle('sidebar:is-pinned', () => state.alwaysOnTop);

  // 状态面板可见性（开/关）
  ipcMain.handle('sidebar:toggle-visibility', () => {
    // Live2D 窗口的"切换侧边栏"按钮：独立切换侧边栏可见性
    if (!state.sidebarWindow || state.sidebarWindow.isDestroyed()) {
      createSidebarWindow();
      state.sidebarVisible = true;
    } else {
      state.sidebarVisible = !state.sidebarVisible;
      if (state.sidebarVisible) {
        state.sidebarWindow.show();
        if (state.sidebarWindow.isMinimized()) state.sidebarWindow.restore();
        state.sidebarWindow.focus();
      } else {
        state.sidebarWindow.hide();
      }
    }
    logger.debug('Live2D 按钮切换侧边栏可见性', { sidebarVisible: state.sidebarVisible });
    return state.sidebarVisible;
  });

  ipcMain.handle('sidebar:open-chat', () => {
    state.sidebarWindow?.webContents.send('sidebar:event', { type: 'open-chat' });
  });

  ipcMain.handle('sidebar:switch-model', () => {
    state.sidebarWindow?.webContents.send('sidebar:event', { type: 'switch-model' });
  });

  ipcMain.handle('sidebar:open-settings', () => {
    state.sidebarWindow?.webContents.send('sidebar:event', { type: 'open-settings' });
  });

  // ========== 业务查询/操作 ==========
  ipcMain.handle('sidebar:get-model', () => getModelConfig());

  ipcMain.handle('sidebar:get-identity', () => getIdentity());

  ipcMain.handle('sidebar:list-providers', () => {
    try {
      const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
      const current = getCurrentProviderName();
      const list = Object.entries(providers).map(([name, cfg]) => ({
        name,
        model: cfg.model,
        url: cfg.url,
        isCurrent: name === current,
      }));
      logger.debug('提供商列表查询', { count: list.length, current });
      return list;
    } catch {
      logger.warn('提供商列表读取失败');
      return [];
    }
  });

  ipcMain.handle('sidebar:switch-provider', (_evt, providerName) => {
    try {
      logger.info('切换 Provider', { to: providerName });
      const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
      // 灵活匹配 llm → providers → name 链，不依赖固定缩进数
      const updated = yaml.replace(
        /^( *)(llm:[\s\S]*?providers:[\s\S]*?)(^\s+name:\s*)\S+/m,
        (_match, indent, prefix, namePart) => `${indent}${prefix}${namePart}${providerName}`
      );
      if (updated === yaml) {
        logger.warn('main.yml 中未找到可替换的 name 字段');
        return { success: false, error: '未在配置中找到可替换的 provider name' };
      }
      fs.writeFileSync(MAIN_YML, updated, 'utf-8');
      const modelCfg = getModelConfig();
      logger.info('Provider 切换成功', { provider: providerName, model: modelCfg.model });
      return { success: true, model: modelCfg };
    } catch (err) {
      logger.error('Provider 切换失败', err.message);
      return { success: false, error: err.message };
    }
  });

  ipcMain.handle('sidebar:set-zoom', (_evt, delta) => {
    state.currentZoom = Math.min(1.5, Math.max(0.7, state.currentZoom + delta));
    state.sidebarWindow?.webContents.setZoomFactor(state.currentZoom);
    return state.currentZoom;
  });

  ipcMain.handle('sidebar:get-token-usage', () => ({ ...state.tokenUsage }));
}

module.exports = { registerIpcHandlers };
