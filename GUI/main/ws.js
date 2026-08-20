// ========== Agent WebSocket 连接 ==========
const WebSocket = require('ws');
const state = require('./state');
const { logger } = require('./logger');
const { getWsEndpoint } = require('./config');
const { isAnyWindowVisible } = require('./windows');
const { accumulateToken, pushSidebarState } = require('./state-push');
const { showAliyaNotification } = require('./notifications');

function connectAgentWebSocket() {
  try {
    const { host, port } = getWsEndpoint();
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
      // 推送连接状态（驱动侧边栏在线徽章与设置窗口连接指示）
      pushSidebarState({ connected: true });
      const queryAgent = (label) => {
        try {
          logger.debug('WS 查询 Agent 状态', { label });
          ws.send(JSON.stringify({ type: 'get_emotion_state' }));
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        } catch {}
      };
      queryAgent('initial');
      setTimeout(() => queryAgent('deferred(2s)'), 2000);
    };

    // WS 消息分发映射表（按 type 路由到对应处理函数）
    const WS_HANDLERS = {
      // ── 新协议：两阶段事件流（AG-UI 风格）─────────────────────
      run_started(data) {
        logger.debug('回复回合开始', { sessionId: data.session_id });
      },
      step_started(data) {
        logger.debug('阶段开始', { phase: data.phase });
      },
      step_finished(data) {
        logger.debug('阶段结束', { phase: data.phase });
      },
      text_message_start(data) {
        state.chatWindow?.webContents.send('chat:stream-start', {
          messageId: data.message_id,
        });
      },
      text_message_content(data) {
        state.chatWindow?.webContents.send('chat:stream-delta', {
          messageId: data.message_id,
          text: data.text,
        });
      },
      text_message_end(data) {
        state.chatWindow?.webContents.send('chat:stream-end', {
          messageId: data.message_id,
          fullText: data.full_text,
        });
      },
      run_finished(data) {
        logger.debug('回复回合结束', { sessionId: data.session_id });
        state.chatWindow?.webContents.send('chat:run-finished', {
          sessionId: data.session_id,
        });
      },
      // 工具调用过程 → 聊天窗口状态（可选展示）
      tool_call_start(data) {
        state.chatWindow?.webContents.send('chat:tool-start', {
          tool: data.tool_name || '',
          arguments: data.arguments || {},
        });
      },
      tool_call_end(data) {
        state.chatWindow?.webContents.send('chat:tool-end', { callId: data.call_id });
      },
      // ── 旧协议（brain_complete）兼容 ───────────────────────────
      emotion_state(data) {
        const feeling = data.dominant || '';
        if (typeof feeling !== 'string' || !feeling) return;
        logger.debug('心情查询结果', { feeling });
        pushSidebarState({ emotion: { feeling } });
      },
      emotion_changed(data) {
        const feeling = data.emotion || data.feeling;
        if (!feeling) return;
        logger.debug('实时心情推送', { feeling });
        pushSidebarState({
          emotion: { feeling, scores: data.scores || null },
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
          pushSidebarState({ emotion: { feeling: data.emotion } });
        } else {
          ws.send(JSON.stringify({ type: 'get_emotion_state' }));
        }
        ws.send(JSON.stringify({ type: 'get_token_usage' }));
        // 聊天窗口：推送 AI 回复（busy 状态由渲染端据此解除）
        if (data.reply) {
          state.chatWindow?.webContents.send('chat:reply', { reply: data.reply });
        }
        // 桌面通知：仅当所有界面（Live2D 主入口 / 状态面板 / 设置窗口）都不可见时才弹出
        if (data.reply && !isAnyWindowVisible()) {
          showAliyaNotification(data.reply);
        }
      },
      // 工具执行确认请求 → 聊天窗口展示横幅（允许/拒绝）
      confirm_request(data) {
        state.chatWindow?.webContents.send('chat:confirm-request', {
          tool: data.tool || '',
          params: data.params || {},
          callId: data.call_id || '',
        });
      },
      // 后端错误 / 通知（含 stop 打断后的"已停止回复"）→ 聊天窗口
      error(data) {
        state.chatWindow?.webContents.send('chat:error', { message: data.message || '未知错误' });
      },
      notice(data) {
        state.chatWindow?.webContents.send('chat:notice', { message: data.message || '' });
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
      pushSidebarState({ connected: false });
      clearTimeout(state.wsReconnectTimer);
      state.wsReconnectTimer = setTimeout(() => {
        logger.info('WS 尝试重连…');
        try { connectAgentWebSocket(); } catch { logger.error('WS 重连失败'); }
      }, 5000);
    };

    ws.onerror = () => {
      logger.warn('Agent WebSocket 连接异常');
      pushSidebarState({ connected: false });
    };
  } catch (e) {
    logger.warn('Agent WebSocket 不可用', { error: e.message || '未知错误' });
    pushSidebarState({ connected: false });
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
