<template>
  <div class="chat-shell">
    <WindowTitlebar title="与 Aliya 聊天" :connected="chatStore.connected" :api="api" />

    <!-- 断线提示条 -->
    <div v-if="!chatStore.connected" class="chat-offline">
      Agent 服务未连接，消息暂无法发送（正在自动重连…）
    </div>

    <main ref="listEl" class="chat-list">
      <!-- 空状态 -->
      <div v-if="chatStore.messages.length === 0" class="chat-empty">
        <div class="chat-empty__emoji">🌸</div>
        <div class="chat-empty__text">和 Aliya 说点什么吧～</div>
      </div>

      <!-- 消息气泡 -->
      <div
        v-for="msg in chatStore.messages"
        :key="msg.id"
        class="chat-bubble"
        :class="`chat-bubble--${msg.role}`"
      >
        <div class="chat-bubble__text">{{ msg.text }}</div>
      </div>

      <!-- 流式回复中（新协议 text_message_*） -->
      <div v-if="chatStore.streaming" class="chat-bubble chat-bubble--ai">
        <div class="chat-bubble__text">
          {{ chatStore.streaming.text }}<span class="chat-caret" />
        </div>
      </div>

      <!-- 思考中指示 -->
      <div v-if="chatStore.busy && !chatStore.streaming" class="chat-thinking">
        <span class="chat-thinking__dot" />
        <span class="chat-thinking__dot" />
        <span class="chat-thinking__dot" />
        <span class="chat-thinking__label">Aliya 正在思考…</span>
      </div>
    </main>

    <!-- 工具确认横幅 -->
    <div v-if="chatStore.confirm" class="chat-confirm">
      <div class="chat-confirm__info">
        <div class="chat-confirm__title">Aliya 请求执行工具：{{ chatStore.confirm.tool }}</div>
        <div class="chat-confirm__params">{{ paramsSummary }}</div>
      </div>
      <div class="chat-confirm__actions">
        <n-button size="small" type="error" secondary @click="respondConfirm(false)">拒绝</n-button>
        <n-button size="small" type="primary" @click="respondConfirm(true)">允许</n-button>
      </div>
    </div>

    <!-- 输入区 -->
    <footer class="chat-input">
      <textarea
        ref="inputEl"
        v-model="draft"
        class="chat-input__field"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows="3"
        :disabled="!chatStore.connected"
        @keydown.enter.exact.prevent="handleEnterKey"
      />
      <div class="chat-input__actions">
        <n-button
          v-if="chatStore.busy"
          size="small"
          type="warning"
          secondary
          @click="stopReply"
        >
          停止
        </n-button>
        <n-button
          v-else
          size="small"
          type="primary"
          :disabled="!draft.trim() || !chatStore.connected"
          @click="handleSend"
        >
          发送
        </n-button>
      </div>
    </footer>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, computed, watch } from 'vue';
import { NButton } from 'naive-ui';
import WindowTitlebar from '../common/WindowTitlebar.vue';
import {
  chatStore,
  sendMessage,
  stopReply,
  respondConfirm,
  fetchConnectionState,
  onReply,
  onError,
  onNotice,
  onConfirmRequest,
  onStateSnapshot,
  onStreamStart,
  onStreamDelta,
  onStreamEnd,
  onRunFinished,
  onToolStart,
  onToolEnd,
} from './useChatStore.js';

const api = window.chatAPI;

const draft = ref('');
const listEl = ref(null);
const inputEl = ref(null);

const paramsSummary = computed(() => {
  const params = chatStore.confirm?.params;
  if (!params || Object.keys(params).length === 0) return '（无参数）';
  return Object.entries(params)
    .map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join('，')
    .slice(0, 200);
});

// Enter 发送；中文 IME 组合中的 Enter（确认候选词）不触发发送
function handleEnterKey(e) {
  if (e.isComposing || e.keyCode === 229) return;
  handleSend();
}

async function handleSend() {
  // 诊断：发送动作入口（经 console 捕获写入主进程日志）
  console.log('[chat] handleSend', { len: draft.value.length, busy: chatStore.busy, connected: chatStore.connected });
  if (!draft.value.trim() || chatStore.busy) return;
  const sent = await sendMessage(draft.value);
  if (sent) draft.value = '';
}

// 新消息到达时滚动到底部（消息为原地 push，watch 长度即可）
function scrollToBottom() {
  nextTick(() => {
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
  });
}

watch(() => chatStore.messages.length, scrollToBottom);
watch(() => chatStore.busy, scrollToBottom);
// 流式回复逐 token 更新时持续滚动
watch(() => chatStore.streaming?.text, scrollToBottom);

onMounted(() => {
  // 诊断：preload 注入状态（经 console 捕获写入主进程日志）
  console.log('[chat] chatAPI 可用:', typeof window.chatAPI);
  api?.onReply?.(onReply);
  api?.onError?.(onError);
  api?.onNotice?.(onNotice);
  api?.onConfirmRequest?.(onConfirmRequest);
  api?.onStateSnapshot?.(onStateSnapshot);
  // 流式回复订阅
  api?.onStreamStart?.(onStreamStart);
  api?.onStreamDelta?.(onStreamDelta);
  api?.onStreamEnd?.(onStreamEnd);
  api?.onRunFinished?.(onRunFinished);
  api?.onToolStart?.(onToolStart);
  api?.onToolEnd?.(onToolEnd);
  // 主动拉取当前连接状态（事件驱动快照可能遗漏初始状态）
  fetchConnectionState();
  inputEl.value?.focus();
});
</script>

<style scoped>
.chat-shell {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  border-radius: var(--rb-radius-xxxl);
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 8%, var(--rb-bg-elevated) 0%, transparent 38%),
    radial-gradient(circle at 88% 22%, var(--rb-bg-3) 0%, transparent 38%),
    linear-gradient(155deg, var(--rb-bg-3) 0%, var(--rb-bg-1) 60%, var(--rb-bg-2) 100%);
  border: 1px solid rgba(255, 190, 226, 0.36);
  box-shadow:
    0 0 24px var(--rb-glow-soft),
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    inset 0 0 0 1px var(--rb-border-faint);
}

/* ---------- 断线提示条 ---------- */

.chat-offline {
  padding: 6px 16px;
  font: var(--rb-text-micro);
  color: #ffc9d8;
  background: rgba(219, 39, 119, 0.18);
  border-bottom: 1px solid rgba(255, 182, 220, 0.24);
  flex-shrink: 0;
}

/* ---------- 消息列表 ---------- */

.chat-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
}

.chat-empty {
  margin: auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  opacity: 0.75;
}

.chat-empty__emoji {
  font-size: 34px;
  filter: drop-shadow(0 0 12px rgba(236, 72, 153, 0.4));
}

.chat-empty__text {
  font: var(--rb-text-small);
  color: var(--rb-text-muted);
}

.chat-bubble {
  max-width: 82%;
  padding: 8px 12px;
  border-radius: var(--rb-radius-lg);
  line-height: 1.55;
  font: var(--rb-text-small);
  word-break: break-word;
  white-space: pre-wrap;
}

.chat-bubble--user {
  align-self: flex-end;
  color: var(--rb-text-on-pink);
  background: var(--rb-grad-pink);
  border: 1px solid rgba(255, 182, 220, 0.34);
  box-shadow: 0 0 12px rgba(236, 72, 153, 0.22);
  border-bottom-right-radius: var(--rb-radius-xs);
}

.chat-bubble--ai {
  align-self: flex-start;
  color: var(--rb-text-default);
  background: var(--rb-bg-elevated);
  border: 1px solid var(--rb-border-soft);
  box-shadow: var(--rb-shadow-card);
  border-bottom-left-radius: var(--rb-radius-xs);
}

.chat-bubble--system {
  align-self: center;
  max-width: 90%;
  color: var(--rb-text-muted);
  font: var(--rb-text-micro);
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--rb-border-faint);
}

/* ---------- 流式光标 ---------- */

.chat-caret {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -0.15em;
  background: var(--rb-pink-400);
  animation: chat-caret-blink 0.9s steps(1) infinite;
}

@keyframes chat-caret-blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ---------- 思考指示 ---------- */

.chat-thinking {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 10px 14px;
}

.chat-thinking__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--rb-radius-full);
  background: var(--rb-pink-400);
  animation: chat-dot 1.2s ease-in-out infinite;
}

.chat-thinking__dot:nth-child(2) { animation-delay: 0.15s; }
.chat-thinking__dot:nth-child(3) { animation-delay: 0.3s; }

@keyframes chat-dot {
  0%, 60%, 100% { opacity: 0.25; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}

.chat-thinking__label {
  margin-left: 4px;
  font: var(--rb-text-micro);
  color: var(--rb-text-muted);
}

/* ---------- 工具确认横幅 ---------- */

.chat-confirm {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 16px 10px;
  padding: 10px 12px;
  border-radius: var(--rb-radius-lg);
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.35);
  flex-shrink: 0;
}

.chat-confirm__info {
  flex: 1;
  min-width: 0;
}

.chat-confirm__title {
  font: var(--rb-text-small-em);
  color: #ffe3b0;
}

.chat-confirm__params {
  margin-top: 2px;
  font: var(--rb-text-micro);
  color: var(--rb-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-confirm__actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

/* ---------- 输入区 ---------- */

.chat-input {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid var(--rb-border-soft);
  background: rgba(255, 255, 255, 0.03);
  flex-shrink: 0;
}

.chat-input__field {
  flex: 1;
  resize: none;
  padding: 8px 12px;
  font: var(--rb-text-small);
  font-family: inherit;
  line-height: 1.5;
  color: var(--rb-text-default);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(236, 72, 153, 0.18);
  border-radius: var(--rb-radius-lg);
  outline: none;
  transition: border-color 140ms ease, box-shadow 140ms ease;
}

.chat-input__field:focus {
  border-color: #ec4899;
  box-shadow: 0 0 0 2px rgba(236, 72, 153, 0.18);
}

.chat-input__field::placeholder {
  color: #6b6388;
}

.chat-input__field:disabled {
  opacity: 0.5;
}
</style>
