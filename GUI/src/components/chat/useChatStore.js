// ========== 聊天窗口共享状态（模块级单例） ==========
// 消息列表、busy 生命周期（发送→brain_complete/error/notice 解除）、
// 工具确认请求的待决状态，集中在此供 ChatPanel 各区块共享。
import { reactive } from 'vue';

const api = window.chatAPI;

let seq = 0;

export const chatStore = reactive({
  connected: false,          // Agent WS 连接状态
  busy: false,               // agent 处理中（思考指示 + 停止按钮）
  messages: [],              // { id, role: 'user'|'ai'|'system', text }
  confirm: null,             // 待决工具确认 { tool, params }
});

function pushMessage(role, text) {
  chatStore.messages.push({ id: ++seq, role, text });
}

/** 发送用户消息；成功返回 true 并置 busy */
export async function sendMessage(text) {
  const trimmed = String(text || '').trim();
  if (!trimmed || chatStore.busy) return false;
  const result = await api?.sendMessage(trimmed);
  if (!result?.success) {
    pushMessage('system', result?.error || '发送失败');
    return false;
  }
  pushMessage('user', trimmed);
  chatStore.busy = true;
  return true;
}

/** 停止当前回复（后端打断后回 notice"已停止回复"） */
export function stopReply() {
  api?.stop();
}

/** 拉取当前连接状态：状态快照为事件驱动，窗口创建晚于 WS 连接时需主动查询 */
export async function fetchConnectionState() {
  const snap = await api?.getState?.();
  if (snap && typeof snap.connected === 'boolean') {
    chatStore.connected = snap.connected;
  }
}

/** 响应工具确认请求 */
export function respondConfirm(allowed) {
  if (!chatStore.confirm) return;
  chatStore.confirm = null;
  api?.confirmResponse(allowed);
}

// ---------- 主进程推送事件处理 ----------

export function onReply(data) {
  if (!data?.reply) return;
  pushMessage('ai', data.reply);
  chatStore.busy = false;
}

export function onError(data) {
  if (data?.message) pushMessage('system', `出错了：${data.message}`);
  chatStore.busy = false;
}

export function onNotice(data) {
  if (data?.message) pushMessage('system', data.message);
  chatStore.busy = false;
}

export function onConfirmRequest(data) {
  if (!data?.tool) return;
  chatStore.confirm = { tool: data.tool, params: data.params || {} };
}

export function onStateSnapshot(snap) {
  if (!snap) return;
  if (typeof snap.connected === 'boolean') chatStore.connected = snap.connected;
  // 连接断开时，进行中的回复不会再有结果
  if (snap.connected === false) chatStore.busy = false;
}
