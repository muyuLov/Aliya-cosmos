<template>
  <section class="pane">
    <PaneHeader title="服务" description="后端 Agent 服务连接状态与运行信息。" />

    <!-- 连接状态卡 -->
    <div class="service-card" :class="settingsStore.wsConnected ? 'service-card--ok' : 'service-card--bad'">
      <span class="service-card__dot" />
      <div class="service-card__info">
        <div class="service-card__label">{{ settingsStore.wsConnected ? '服务已连接' : '服务未连接' }}</div>
        <div class="service-card__addr">{{ settingsStore.wsHost }}:{{ settingsStore.wsPort }}</div>
      </div>
      <n-tag
        size="small"
        :bordered="false"
        :type="settingsStore.wsConnected ? 'success' : 'error'"
      >
        {{ settingsStore.wsConnected ? '在线' : '离线' }}
      </n-tag>
    </div>

    <!-- 运行信息 -->
    <div class="pane__card">
      <div class="service-row">
        <span class="service-row__label">TTS 提供商</span>
        <span class="service-row__value">{{ settingsStore.ttsProvider || '—' }}</span>
      </div>
      <div class="service-row">
        <span class="service-row__label">Token 用量</span>
        <span class="service-row__value">{{ formattedToken }}</span>
      </div>
      <div class="service-row">
        <span class="service-row__label">GUI 版本</span>
        <span class="service-row__value">{{ settingsStore.appVersion || '0.1.0' }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed } from 'vue';
import { NTag } from 'naive-ui';
import { formatTokenCount } from '../../../utils/formatters.js';
import { settingsStore } from '../useSettingsStore.js';
import PaneHeader from './PaneHeader.vue';

const formattedToken = computed(() => formatTokenCount(settingsStore.tokenTotal));
</script>

<style scoped>
.service-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  margin-bottom: var(--rb-space-lg);
  border-radius: var(--rb-radius-xl);
  border: 1px solid var(--rb-border-soft);
  background: rgba(255, 255, 255, 0.04);
}

.service-card__dot {
  width: 10px;
  height: 10px;
  border-radius: var(--rb-radius-full);
  flex-shrink: 0;
}

.service-card--ok .service-card__dot {
  background: #22c55e;
  box-shadow: 0 0 10px rgba(34, 197, 94, 0.6);
}

.service-card--bad .service-card__dot {
  background: #ef4444;
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.6);
}

.service-card__info {
  flex: 1;
  min-width: 0;
}

.service-card__label {
  font: var(--rb-text-body-em);
  color: var(--rb-text-strong);
}

.service-card__addr {
  margin-top: 2px;
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  color: var(--rb-pink-300);
}

.service-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 2px;
}

.service-row + .service-row {
  border-top: 1px solid var(--rb-border-faint);
}

.service-row__label {
  font: var(--rb-text-small);
  color: var(--rb-text-muted);
}

.service-row__value {
  font: var(--rb-text-small-em);
  color: var(--rb-text-default);
}
</style>
