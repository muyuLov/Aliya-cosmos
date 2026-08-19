<template>
  <div class="sidebar">
    <TopBar />

    <main class="body">
      <AvatarCard />

      <hr class="divider" />

      <IndicatorCard
        :emoji="statusEmoji"
        :label="statusLabel"
        prefix="状态"
      />

      <IndicatorCard
        :emoji="feelingEmoji"
        :label="feelingLabel"
        prefix="心情"
        variant="violet"
      />

      <ModelCard />

      <SettingsCard @open-settings="handleOpenSettings" />

      <hr class="divider" />

      <TokenFooter />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useMessage } from 'naive-ui';
import { useAppStore } from '../../stores/appStore.js';
import TopBar from '../TopBar.vue';
import AvatarCard from '../AvatarCard.vue';
import IndicatorCard from '../IndicatorCard.vue';
import ModelCard from '../ModelCard.vue';
import SettingsCard from '../SettingsCard.vue';
import TokenFooter from '../TokenFooter.vue';

const store = useAppStore();
const message = useMessage();

const statusEmoji = computed(() => store.currentStatus.emoji);
const statusLabel = computed(() => store.currentStatus.label);
const feelingEmoji = computed(() => store.currentFeeling.emoji);
const feelingLabel = computed(() => store.currentFeeling.label);

function showToast(msg) {
  message.info(msg, { duration: 1800 });
}

function handleOpenSettings() {
  // 打开真实设置窗口（主进程 createSettingsWindow 单例）
  store.openSettings();
}

let eventCleanup = null;

onMounted(() => {
  store.init();

  eventCleanup = store.subscribeEvents((payload) => {
    if (!payload?.type) return;
    switch (payload.type) {
      case 'switch-model':
        showToast('请在设置窗口中切换模型');
        break;
      case 'open-settings':
        // 主进程已直接打开设置窗口，此处无需处理
        break;
    }
  });
});

onUnmounted(() => {
  if (eventCleanup) eventCleanup();
  store.dispose();
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
