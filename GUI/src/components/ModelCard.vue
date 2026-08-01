<template>
  <section class="card-base feed-card">
    <div class="feed-row">
      <span class="feed-row__emoji" aria-hidden="true">🍰</span>
      <span>当前模型：</span>
      <span class="feed-row__name">{{ modelName }}</span>
    </div>
    <button type="button" class="action-btn" title="打开聊天" @click="handleOpenChat">
      <span class="action-btn__icon" aria-hidden="true">💬</span>
      <span>打开聊天</span>
    </button>
    <button
      type="button"
      class="action-btn"
      :class="{ 'is-active': showPicker }"
      title="切换模型"
      @click.stop="showPicker = !showPicker"
    >
      <span class="action-btn__icon" aria-hidden="true">⇄</span>
      <span>{{ showPicker ? '关闭列表' : '切换模型' }}</span>
    </button>
    <ModelSelector
      v-if="showPicker"
      @select="handleProviderSwitch"
      @close="showPicker = false"
    />
  </section>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useAppStore } from '../stores/appStore.js';
import ModelSelector from './ModelSelector.vue';

const store = useAppStore();

const modelName = computed(() => store.modelName);
const showPicker = ref(false);

function handleOpenChat() {
  store.openChat();
}

async function handleProviderSwitch(name) {
  showPicker.value = false;
  await store.switchProvider(name);
}
</script>

<style scoped>
.feed-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  overflow: visible;
}

.feed-row {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font: var(--rb-text-small);
  color: var(--rb-text-muted);
}

.feed-row__name {
  color: var(--rb-text-strong);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.feed-row__emoji {
  margin-right: 2px;
  filter: drop-shadow(0 0 6px rgba(236, 72, 153, 0.4));
}

/* action-btn 基础样式见 base.css 全局共享类 */
.action-btn.is-active {
  background: var(--rb-grad-pink-hot);
  color: var(--rb-text-on-pink);
  box-shadow: 0 0 18px rgba(236, 72, 153, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.22);
}
</style>
