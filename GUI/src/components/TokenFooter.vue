<template>
  <div>
    <div class="version">{{ aiName }} v{{ appVersion }}</div>
    <div class="token-line">
      <span>Token：</span>
      <span class="token-line__value">{{ formattedToken }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAppStore } from '../stores/appStore.js';
import { formatTokenCount } from '../utils/formatters.js';

const store = useAppStore();
const aiName = computed(() => store.aiName);
const formattedToken = computed(() => formatTokenCount(store.tokenTotal));

// Vite define 在 JS 中替换 __APP_VERSION__，template 通过此变量引用
const appVersion = __APP_VERSION__;
</script>

<style scoped>
.version {
  font: var(--rb-text-micro);
  color: rgba(235, 229, 245, 0.38);
  text-align: center;
  padding: 2px 0 0;
  letter-spacing: 0.4px;
}

.token-line {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font: var(--rb-text-micro);
  color: rgba(235, 229, 245, 0.30);
  padding: 2px 0 0;
  user-select: none;
}

.token-line__value {
  color: rgba(235, 229, 245, 0.50);
  font-weight: 500;
}
</style>
