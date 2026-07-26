<template>
  <div>
    <div class="version">{{ aiName }} v0.0.1</div>
    <div class="token-line">
      <span>Token：</span>
      <span class="token-line__value">{{ formattedToken }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { formatTokenCount } from '../utils/formatters.js';

const props = defineProps({
  aiName: { type: String, default: 'Aliya' },
  tokenCount: { type: Number, default: 0 },
});

const formattedToken = computed(() => formatTokenCount(props.tokenCount));
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
