// ========== Agent WebSocket 连接 ==========
const WebSocket = require('ws');
const fs = require('fs');
const state = require('./state');
const { logger } = require('./logger');
const { MAIN_YML, resolveEnvValue } = require('./config');
const { accumulateToken, pushSidebarState } = require('./state-push');
const { showAliyaNotification } = require('./notifications');

function connectAgentWebSocket() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const hostRaw = yaml.match(/^\s{8}host:\s*([^#\s]+)/m)?.[1] || '127.0.0.1';
    const portRaw = yaml.match(/^\s{8}port:\s*([^#\s]+)/m)?.[1] || '8765';
    const host = resolveEnvValue(hostRaw);
    const port = resolveEnvValue(portRaw);
    const url = `ws://${host}:${port}/agent/ws`;

    logger.info('正在连接 Agent WebSocket', { url });
    // 关闭旧连接（防止重复连接泄漏）
    if (state.agentWebSocket) {
      try { state.agentWebSocket.close(); } catch {}
      state.agentWebSocket = null;
    }
    const ws = new WebSocket(url);
    state.agentWebSocket = ws;

    ws.onopen = () => {
      if (ws !== state.agentWebSocket) return; // 已被取代
      logger.info('Agent WebSocket 已连接');
      const queryAgent = (label) => {
        try {
          logger.debug('WS 查询 Agent 状态', { label });
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        } catch {}
      };
      queryAgent('initial');
      setTimeout(() => queryAgent('deferred(2s)'), 2000);
    };

    // WS 消息分发映射表（按 type 路由到对应处理函数）
    const WS_HANDLERS = {
      feeling_scores(data) {
        if (typeof data.dominant !== 'string') return;
        logger.debug('心情查询结果', { feeling: data.dominant });
        pushSidebarState({ emotion: { feeling: data.dominant || '平静' } });
      },
      emotion_changed(data) {
        if (!data.feeling) return;
        logger.debug('实时心情推送', { feeling: data.feeling });
        pushSidebarState({
          emotion: { feeling: data.feeling, scores: data.scores || null },
        });
      },
      status_changed(data) {
        if (!data.status) return;
        logger.debug('状态变更', { status: data.status });
        pushSidebarState({
          status: { status: data.status, state: data.state || '' },
        });
      },
      brain_complete(data) {
        if (data.usage) accumulateToken(data.usage);
        if (data.emotion) {
          pushSidebarState({
            emotion: { feeling: data.emotion, scores: data.feeling_scores || null },
          });
        } else {
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
        }
        ws.send(JSON.stringify({ type: 'get_token_usage' }));
        // 侧边栏不可见时弹出桌面通知（覆盖未创建/隐藏/最小化三种情况）
        if (data.reply && (!state.sidebarWindow || state.sidebarWindow.isDestroyed() || !state.sidebarWindow.isVisible())) {
          showAliyaNotification(data.reply);
        }
      },
      token_usage(data) {
        if (data.total !== undefined) {
          state.tokenUsage = { total: data.total, input: data.input || 0, output: data.output || 0 };
        } else if (data.usage) {
          accumulateToken(data.usage);
        }
        logger.debug('Token 用量同步', { total: state.tokenUsage.total });
        pushSidebarState({ token: state.tokenUsage.total });
      },
      tts_features(data) {
        if (typeof data.volume !== 'number') return;
        state.live2dWindow?.webContents.send('live2d:mouth-open', {
          volume: data.volume,
          centroid: data.centroid ?? 0.5,
          zcr: data.zcr ?? 0,
        });
      },
    };

    ws.onmessage = (event) => {
      if (ws !== state.agentWebSocket) return;
      try {
        const data = JSON.parse(event.data);
        logger.debug('WS 收到消息', { type: data.type });
        const handler = WS_HANDLERS[data.type];
        if (handler) handler(data);
      } catch (e) {
        logger.warn('WS 消息解析失败', { error: e.message, raw: String(event.data).slice(0, 120) });
      }
    };

    ws.onclose = (evt) => {
      if (ws !== state.agentWebSocket) state.agentWebSocket = null;
      logger.warn('Agent WebSocket 断开', { code: evt.code, reason: evt.reason || '(无)' });
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = setTimeout(() => {
        logger.info('WS 尝试重连…');
        try { connectAgentWebSocket(); } catch { logger.error('WS 重连失败'); }
      }, 5000);
    };

    ws.onerror = () => {
      logger.warn('Agent WebSocket 连接异常');
    };
  } catch (e) {
    logger.warn('Agent WebSocket 不可用', { error: e.message || '未知错误' });
    clearTimeout(state.wsReconnectTimer);
    state.wsReconnectTimer = setTimeout(() => {
      try { connectAgentWebSocket(); } catch {}
    }, 5000);
  }
}

function closeAgentWebSocket() {
  clearTimeout(state.wsReconnectTimer);
  state.wsReconnectTimer = null;
  if (state.agentWebSocket) {
    try { state.agentWebSocket.close(); } catch {}
    state.agentWebSocket = null;
  }
}

module.exports = { connectAgentWebSocket, closeAgentWebSocket };
