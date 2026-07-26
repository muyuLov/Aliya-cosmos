<template>
  <div class="sidebar">
    <TopBar
      :pinned="isPinned"
      :ai-name="aiName"
      @toggle-pin="handleTogglePin"
      @minimize="handleMinimize"
      @close="handleClose"
    />

    <main class="body">
      <AvatarCard :ai-name="aiName" />

      <hr class="divider" />

      <StatusCard
        :status-emoji="statusEmoji"
        :status-label="statusLabel"
      />

      <MoodCard
        :feeling-emoji="feelingEmoji"
        :feeling-label="feelingLabel"
      />

      <ModelCard
        :model-name="modelDisplayName"
        @open-chat="handleOpenChat"
        @model-changed="handleModelChanged"
      />

      <SettingsCard @open-settings="handleOpenSettings" />

      <hr class="divider" />

      <TokenFooter
        :ai-name="aiName"
        :token-count="tokenCount"
      />
    </main>

    <ToastNotification ref="toastRef" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useSidebarAPI } from './composables/useSidebarAPI.js';
import { formatModelName } from './utils/formatters.js';
import TopBar from './components/TopBar.vue';
import AvatarCard from './components/AvatarCard.vue';
import StatusCard from './components/StatusCard.vue';
import MoodCard from './components/MoodCard.vue';
import ModelCard from './components/ModelCard.vue';
import SettingsCard from './components/SettingsCard.vue';
import TokenFooter from './components/TokenFooter.vue';
import ToastNotification from './components/ToastNotification.vue';

const api = useSidebarAPI();

// 解构为顶层 ref，模板中自动解包（避免嵌套 .value 响应式追踪问题）
const { currentFeeling, currentStatus, tokenTotal, aiName } = api;

// computed 拆解，确保模板直接用属性名访问
const statusEmoji = computed(() => currentStatus.value.emoji);
const statusLabel = computed(() => currentStatus.value.label);
const feelingEmoji = computed(() => currentFeeling.value.emoji);
const feelingLabel = computed(() => currentFeeling.value.label);
const tokenCount = computed(() => tokenTotal.value);

const isPinned = ref(true);
const modelDisplayName = ref('获取中…');
const toastRef = ref(null);

function showToast(msg, dur) {
  toastRef.value?.show(msg, dur);
}

async function init() {
  try {
    isPinned.value = await api.isPinned();
  } catch { /* 忽略 */ }

  try {
    const identity = await api.getIdentity();
    if (identity?.aiName) {
      aiName.value = identity.aiName;
      document.title = `${identity.aiName} · 状态`;
    }
  } catch { /* 保持默认 */ }

  try {
    const model = await api.getModel();
    if (model?.model && model.model !== '未知') {
      modelDisplayName.value = formatModelName(model.model);
    }
  } catch { /* 保持默认 */ }

  try {
    const usage = await api.getTokenUsage();
    tokenTotal.value = usage.total ?? 0;
  } catch { /* 忽略 */ }

  api.setupSubscriptions();
}

function handleTogglePin() {
  api.togglePin().then((pinned) => {
    isPinned.value = pinned;
    showToast(pinned ? '已置顶' : '已取消置顶');
  });
}

function handleMinimize() { api.minimize(); }
function handleClose() { api.close(); }

function handleOpenChat() {
  api.openChat();
  showToast('正在打开聊天…');
}

function handleOpenSettings() {
  api.openSettings();
  showToast('正在打开设置…');
}

function handleModelChanged({ model }) {
  modelDisplayName.value = formatModelName(model.model);
  showToast('模型已切换');
}

onMounted(() => {
  init();
  api.subscribeEvents((payload) => {
    if (!payload?.type) return;
    switch (payload.type) {
      case 'open-chat':
        showToast('聊天窗口未配置');
        break;
      case 'switch-model':
        showToast('模型切换未配置');
        break;
      case 'open-settings':
        showToast('设置窗口未配置');
        break;
    }
  });
});

onUnmounted(() => {
  api.teardownSubscriptions();
});
</script>

<style scoped>
.sidebar {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  border-radius: var(--rb-radius-xxxl);
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 8%, var(--rb-bg-elevated) 0%, transparent 38%),
    radial-gradient(circle at 88% 22%, var(--rb-bg-3) 0%, transparent 38%),
    radial-gradient(circle at 50% 110%, var(--rb-bg-3) 0%, transparent 38%),
    linear-gradient(155deg, var(--rb-bg-3) 0%, var(--rb-bg-1) 60%, var(--rb-bg-2) 100%);
  border: 1px solid rgba(255, 190, 226, 0.36);
  box-shadow:
    0 0 24px var(--rb-glow-soft),
    inset 0 1px 0 rgba(255, 255, 255, 0.12),
    inset 0 0 0 1px var(--rb-border-faint);
}

.sidebar::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(135deg,
    rgba(255, 255, 255, 0.10), transparent 30%,
    var(--rb-border-faint) 70%, rgba(255, 255, 255, 0.04));
  opacity: 0.58;
}

.body {
  position: relative;
  z-index: 1;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  overflow-y: auto;
}

.divider {
  display: block;
  align-self: stretch;
  width: auto;
  min-height: 1px;
  border: none;
  margin: 4px 2px;
  padding: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 182, 220, 0.34), transparent);
  opacity: 0.95;
  flex-shrink: 0;
}
</style>
