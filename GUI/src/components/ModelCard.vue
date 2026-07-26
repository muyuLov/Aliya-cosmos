<template>
  <section class="card feed-card">
    <div class="feed-row">
      <span class="feed-row__emoji" aria-hidden="true">🍰</span>
      <span>当前模型：</span>
      <span class="feed-row__name">{{ modelName }}</span>
    </div>
    <button type="button" class="action-btn" title="打开聊天" @click="$emit('open-chat')">
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
import { ref, watch } from 'vue';
import ModelSelector from './ModelSelector.vue';

defineProps({
  modelName: { type: String, default: '获取中…' },
});

const emit = defineEmits(['open-chat', 'model-changed']);

const showPicker = ref(false);

// 调试：监视 showPicker 变化输出到控制台
watch(showPicker, (val) => {
  try { console.log('[ModelCard] showPicker:', val); } catch {}
});

function handleProviderSwitch(name) {
  showPicker.value = false;
  const api = window.sidebarAPI;
  if (!api) {
    try { console.warn('[ModelCard] sidebarAPI 不可用'); } catch {}
    return;
  }
  api.switchProvider(name).then((result) => {
    if (result.success) {
      emit('model-changed', { model: result.model });
    }
  });
}
</script>

<style scoped>
.card {
  position: relative;
  overflow: hidden;
  border-radius: var(--rb-radius-xl);
  border: 1px solid rgba(255, 182, 220, 0.16);
  background: rgba(255, 255, 255, 0.055);
  box-shadow: var(--rb-shadow-card), inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.card::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(circle at 20% 10%, rgba(255, 110, 199, 0.16), transparent 42%);
}

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

.action-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  width: 100%;
  padding: 9px 12px;
  font: var(--rb-text-small-em);
  color: var(--rb-text-default);
  background: linear-gradient(135deg, rgba(236, 72, 153, 0.20), rgba(168, 85, 247, 0.18));
  border: 1px solid var(--rb-border-soft);
  border-radius: var(--rb-radius-md);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition: background 120ms ease, color 120ms ease, transform 120ms ease,
              border-color 120ms ease, box-shadow 200ms ease;
}

.action-btn:hover {
  background: rgba(236, 72, 153, 0.30);
  border-color: rgba(255, 182, 220, 0.36);
  color: var(--rb-text-strong);
  transform: translateY(-1px);
  box-shadow: 0 0 14px rgba(236, 72, 153, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.10);
}

.action-btn:active { transform: translateY(0); }

.action-btn.is-active {
  background: var(--rb-grad-pink-hot);
  color: var(--rb-text-on-pink);
  box-shadow: 0 0 18px rgba(236, 72, 153, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.22);
}

.action-btn__icon {
  font-size: 15px;
  width: 18px;
  text-align: center;
  filter: drop-shadow(0 0 5px rgba(236, 72, 153, 0.45));
}
</style>
