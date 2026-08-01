<template>
  <div class="provider-picker" @click.stop>
    <div class="provider-picker__list" id="provider-list">
      <button
        v-for="p in providers"
        :key="p.name"
        type="button"
        class="provider-picker__item"
        :class="{ 'is-current': p.isCurrent }"
        :data-provider="p.name"
        @click="handleSelect(p)"
      >
        <div class="provider-picker__item-info">
          <span class="provider-picker__item-name">{{ p.name }}</span>
          <span class="provider-picker__item-model">{{ formatModelName(p.model) }}</span>
        </div>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useAppStore } from '../stores/appStore.js';
import { formatModelName } from '../utils/formatters.js';

const store = useAppStore();

const emit = defineEmits(['select', 'close']);

const providers = ref([]);

async function loadProviders() {
  const list = await store.listProviders();
  providers.value = (list || []).slice().sort((a, b) =>
    a.isCurrent ? -1 : b.isCurrent ? 1 : 0
  );
}

function handleSelect(p) {
  emit('select', p.name);
}

function onClickOutside(e) {
  const el = document.querySelector('.provider-picker');
  if (el && !el.contains(e.target)) {
    emit('close');
  }
}

onMounted(() => {
  loadProviders();
  document.addEventListener('click', onClickOutside);
});

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside);
});
</script>

<style scoped>
.provider-picker {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 10;
  margin: -1px;
  border-radius: var(--rb-radius-xl);
  border: 1px solid var(--rb-border-strong);
  background: rgba(15, 13, 31, 0.98);
  box-shadow: var(--rb-shadow-elevated);
  animation: pickerFadeIn 150ms ease;
}

@keyframes pickerFadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

.provider-picker__list {
  display: flex;
  flex-direction: column;
  padding: 6px;
  gap: 4px;
  max-height: 220px;
  overflow-y: auto;
  scrollbar-width: none;
}
.provider-picker__list::-webkit-scrollbar { display: none; }

.provider-picker__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 10px 12px;
  border-radius: var(--rb-radius-md);
  font: var(--rb-text-small);
  color: var(--rb-text-default);
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition: background 120ms ease, border-color 120ms ease, color 120ms ease;
  text-align: left;
}

.provider-picker__item:hover {
  background: rgba(236, 72, 153, 0.14);
  border-color: var(--rb-border-soft);
  color: var(--rb-text-strong);
}

.provider-picker__item.is-current {
  background: rgba(236, 72, 153, 0.18);
  border-color: var(--rb-border-strong);
  color: var(--rb-text-strong);
}

.provider-picker__item.is-current::after {
  content: '✓';
  margin-left: auto;
  color: var(--rb-pink-400);
  font-weight: 600;
}

.provider-picker__item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.provider-picker__item-name {
  font-weight: 500;
  white-space: nowrap;
}

.provider-picker__item-model {
  font-size: 11px;
  color: var(--rb-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
