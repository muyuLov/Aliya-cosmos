// ========== Token 用量追踪 & 状态快照批量推送 ==========
const state = require('./state');
const { logger } = require('./logger');

function accumulateToken(usage) {
  if (!usage) return;
  const inTokens = usage.prompt_tokens || usage.input_tokens || 0;
  const outTokens = usage.completion_tokens || usage.output_tokens || 0;
  state.tokenUsage.total += inTokens + outTokens;
  state.tokenUsage.input += inTokens;
  state.tokenUsage.output += outTokens;
  logger.debug('Token 累积', { in: inTokens, out: outTokens, total: state.tokenUsage.total });
  pushSidebarState({ token: state.tokenUsage.total });
}

// ========== 状态快照批量推送 ==========
// 高频状态（情绪/状态/Token）合并为 50ms 批量单通道推送，
// 减少渲染进程 IPC 唤醒频率，降低主/渲染进程通信开销。
const STATE_FLUSH_INTERVAL = 50;

/** 写入待推送状态（可多次调用，定时合并） */
function pushSidebarState(patch) {
  Object.assign(state.stateBuffer, patch);
  if (!state.stateFlushTimer) {
    state.stateFlushTimer = setTimeout(flushSidebarState, STATE_FLUSH_INTERVAL);
  }
}

/** 合并缓冲并一次性推送给状态面板 */
function flushSidebarState() {
  state.stateFlushTimer = null;
  const snap = {};
  if (state.stateBuffer.emotion) {
    snap.feeling = state.stateBuffer.emotion.feeling;
    snap.scores = state.stateBuffer.emotion.scores || null;
  }
  if (state.stateBuffer.status) {
    snap.status = state.stateBuffer.status.status;
    snap.state = state.stateBuffer.status.state || '';
  }
  if (state.stateBuffer.token !== null) snap.token = state.stateBuffer.token;
  state.stateBuffer = { emotion: null, status: null, token: null };
  if (Object.keys(snap).length > 0) {
    state.sidebarWindow?.webContents.send('sidebar:state-snapshot', snap);
    // 同步情绪到 Live2D 窗口，驱动 SDK 表情/动作系统
    if (snap.feeling) {
      state.live2dWindow?.webContents.send('live2d:emotion', {
        feeling: snap.feeling,
        scores: snap.scores || null,
      });
    }
  }
}

module.exports = { accumulateToken, pushSidebarState };
