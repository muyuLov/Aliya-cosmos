// ========== IPC 处理器注册 ==========
const { ipcMain } = require('electron');
const fs = require('fs');
const state = require('./state');
const { logger } = require('./logger');
const {
  PROVIDERS_FILE,
  getCurrentProviderName,
  getModelConfig,
  getIdentity,
  getWsEndpoint,
  getTTSProviderName,
  saveIdentity,
  switchProvider,
} = require('./config');
const { createSidebarWindow, createSettingsWindow, createChatWindow, dockLive2D, syncLive2DPosition } = require('./windows');

function listProviders() {
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
}

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

  // 打开聊天：直接创建/聚焦聊天窗口（Live2D 工具栏与侧边栏入口统一走这里）
  ipcMain.handle('sidebar:open-chat', () => {
    createChatWindow();
  });

  ipcMain.handle('sidebar:switch-model', () => {
    state.sidebarWindow?.webContents.send('sidebar:event', { type: 'switch-model' });
  });

  // 打开设置：改为打开真实设置窗口（不再回传事件占位）
  ipcMain.handle('sidebar:open-settings', () => {
    createSettingsWindow();
  });

  // ========== 设置窗口 ==========
  ipcMain.handle('settings:open', () => {
    createSettingsWindow();
  });

  ipcMain.handle('settings:drag-move', (_evt, dx, dy) => {
    if (!state.settingsWindow || state.settingsWindow.isDestroyed()) return;
    const [x, y] = state.settingsWindow.getPosition();
    state.settingsWindow.setPosition(x + dx, y + dy);
  });

  ipcMain.handle('settings:minimize', () => {
    state.settingsWindow?.minimize();
  });

  ipcMain.handle('settings:close', () => {
    state.settingsWindow?.close();
  });

  // ========== 聊天窗口 ==========
  // 消息发送经主进程持有的 agent WS 中转（后端每连接独立建 agent，渲染进程不直连）

  // 当前连接状态查询：状态快照为事件驱动（仅连接变化时推送），
  // 聊天窗口创建晚于 WS 连接时收不到初始快照，需由渲染端挂载时主动拉取
  ipcMain.handle('chat:get-state', () => ({
    connected: Boolean(state.agentWebSocket && state.agentWebSocket.readyState === 1),
  }));

  ipcMain.handle('chat:send-message', (_evt, text) => {
    const ws = state.agentWebSocket;
    logger.info('聊天发送请求', { len: String(text || '').length, wsReadyState: ws?.readyState });
    if (!ws || ws.readyState !== 1) {
      return { success: false, error: 'Agent 服务未连接' };
    }
    const trimmed = String(text || '').trim();
    if (!trimmed) return { success: false, error: '消息为空' };
    try {
      ws.send(JSON.stringify({ type: 'user_message', text: trimmed }));
      logger.info('user_message 已发往后端', { len: trimmed.length });
      return { success: true };
    } catch (e) {
      logger.error('user_message 发送失败', { error: e.message });
      return { success: false, error: '发送失败' };
    }
  });

  ipcMain.handle('chat:stop', () => {
    const ws = state.agentWebSocket;
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'stop' }));
  });

  ipcMain.handle('chat:confirm', (_evt, allowed, callId) => {
    const ws = state.agentWebSocket;
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({
        type: 'confirm_response',
        allowed: Boolean(allowed),
        call_id: String(callId || ''),
      }));
    }
  });

  ipcMain.handle('chat:drag-move', (_evt, dx, dy) => {
    if (!state.chatWindow || state.chatWindow.isDestroyed()) return;
    const [x, y] = state.chatWindow.getPosition();
    state.chatWindow.setPosition(x + dx, y + dy);
  });

  ipcMain.handle('chat:minimize', () => {
    state.chatWindow?.minimize();
  });

  ipcMain.handle('chat:close', () => {
    state.chatWindow?.close();
  });

  // 设置窗口一次性配置快照
  ipcMain.handle('settings:get-config', () => {
    const ws = getWsEndpoint();
    return {
      identity: getIdentity(),
      model: getModelConfig(),
      providers: listProviders(),
      ws,
      ttsProvider: getTTSProviderName(),
      tokenUsage: { ...state.tokenUsage },
      wsConnected: Boolean(
        state.agentWebSocket && state.agentWebSocket.readyState === 1 // WebSocket.OPEN
      ),
      appVersion: process.env.npm_package_version || '',
    };
  });

  // 保存身份信息（ai_name / user_name）
  ipcMain.handle('settings:save-identity', (_evt, identity) => {
    logger.info('保存身份信息', { identity });
    const result = saveIdentity(identity);
    logger.info('身份信息保存结果', { success: result.success, error: result.error || '' });
    return result;
  });

  // ========== 业务查询/操作 ==========
  ipcMain.handle('sidebar:get-model', () => getModelConfig());

  ipcMain.handle('sidebar:get-identity', () => getIdentity());

  ipcMain.handle('sidebar:list-providers', () => listProviders());

  ipcMain.handle('sidebar:switch-provider', (_evt, providerName) => {
    logger.info('切换 Provider', { to: providerName });
    const result = switchProvider(providerName);
    logger.info('Provider 切换结果', { success: result.success, model: result.model?.model });
    return result;
  });

  ipcMain.handle('sidebar:set-zoom', (_evt, delta) => {
    state.currentZoom = Math.min(1.5, Math.max(0.7, state.currentZoom + delta));
    state.sidebarWindow?.webContents.setZoomFactor(state.currentZoom);
    return state.currentZoom;
  });

  ipcMain.handle('sidebar:get-token-usage', () => ({ ...state.tokenUsage }));
}

module.exports = { registerIpcHandlers };
