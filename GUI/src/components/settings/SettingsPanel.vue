<template>
  <div class="settings-shell">
    <WindowTitlebar title="Aliya 设置" :connected="settingsStore.wsConnected" :api="api" />

    <div class="settings-layout">
      <!-- 左侧导航 -->
      <nav class="settings-nav">
        <button
          v-for="item in navItems"
          :key="item.key"
          type="button"
          class="settings-nav__item"
          :class="{ 'settings-nav__item--active': activePane === item.key }"
          @click="activePane = item.key"
        >
          <span class="settings-nav__icon" v-html="item.icon" />
          <span class="settings-nav__label">{{ item.label }}</span>
        </button>
      </nav>

      <!-- 内容区 -->
      <main class="settings-content">
        <IdentityPane v-if="activePane === 'identity'" />
        <ModelPane v-else-if="activePane === 'model'" />
        <ServicePane v-else />
      </main>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import WindowTitlebar from '../common/WindowTitlebar.vue';
import IdentityPane from './panes/IdentityPane.vue';
import ModelPane from './panes/ModelPane.vue';
import ServicePane from './panes/ServicePane.vue';
import {
  settingsStore,
  loadSettingsConfig,
  onStateSnapshot,
} from './useSettingsStore.js';

const api = window.settingsAPI;

const navItems = [
  {
    key: 'identity',
    label: '角色',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  },
  {
    key: 'model',
    label: '模型',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/></svg>',
  },
  {
    key: 'service',
    label: '服务',
    icon: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
  },
];

const activePane = ref('identity');

let snapshotCleanup = null;

onMounted(() => {
  loadSettingsConfig();
  if (api?.onStateSnapshot) snapshotCleanup = api.onStateSnapshot(onStateSnapshot);
});

onUnmounted(() => {
  snapshotCleanup?.();
});
</script>

<style scoped>
.settings-shell {
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

.settings-layout {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* ---------- 左侧导航 ---------- */

.settings-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 152px;
  flex-shrink: 0;
  padding: 14px 10px;
  border-right: 1px solid var(--rb-border-soft);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.008));
  user-select: none;
}

.settings-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--rb-radius-lg);
  border: 1px solid transparent;
  background: transparent;
  color: var(--rb-text-muted);
  cursor: pointer;
  transition: background 140ms ease, color 140ms ease, border-color 140ms ease,
              box-shadow 200ms ease;
}

.settings-nav__item:hover {
  color: var(--rb-text-default);
  background: rgba(255, 255, 255, 0.05);
}

.settings-nav__item--active {
  color: var(--rb-text-strong);
  background: var(--rb-grad-pink);
  border-color: rgba(255, 182, 220, 0.34);
  box-shadow: 0 0 16px rgba(236, 72, 153, 0.30), inset 0 1px 0 rgba(255, 255, 255, 0.14);
}

.settings-nav__icon {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.settings-nav__label {
  font: var(--rb-text-body);
}

/* ---------- 内容区 ---------- */

.settings-content {
  flex: 1;
  min-width: 0;
  padding: 20px 22px;
  overflow-y: auto;
}
</style>
