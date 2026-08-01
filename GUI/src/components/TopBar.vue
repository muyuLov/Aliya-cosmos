<template>
  <header class="titlebar">
    <button
      type="button"
      class="icon-btn"
      :class="{ 'is-active': pinned }"
      :aria-label="pinned ? '取消置顶' : '置顶'"
      :title="pinned ? '取消置顶' : '置顶'"
      @click="handleTogglePin"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 17v5"/>
        <path d="M9 10.76V6a3 3 0 0 1 6 0v4.76a2 2 0 0 0 .59 1.41L18 14H6l2.41-1.83A2 2 0 0 0 9 10.76Z"/>
      </svg>
    </button>
    <div class="titlebar__title">
      <span class="titlebar__name">{{ aiName }}</span>
      <span class="titlebar__hint">状态面板</span>
    </div>
    <div class="titlebar__actions">
      <button type="button" class="win-btn win-btn--close" aria-label="关闭" title="关闭" @click="handleClose">
        <svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true">
          <line x1="2" y1="2" x2="7" y2="7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
          <line x1="7" y1="2" x2="2" y2="7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        </svg>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed, onMounted, onUnmounted } from 'vue';
import { useAppStore } from '../stores/appStore.js';

const store = useAppStore();
const pinned = computed(() => store.pinned);
const aiName = computed(() => store.aiName);

async function handleTogglePin() {
  await store.togglePin();
}

function handleClose() { store.close(); }

onMounted(() => {
  // 窗口拖拽（工具栏区域）
  let dragging = false;
  let dragX = 0;
  let dragY = 0;
  const el = document.querySelector('.titlebar');

  const onDown = (e) => {
    if (e.target.closest('button')) return;
    dragging = true;
    dragX = e.screenX;
    dragY = e.screenY;
  };
  const onMove = (e) => {
    if (!dragging) return;
    const dx = e.screenX - dragX;
    const dy = e.screenY - dragY;
    dragX = e.screenX;
    dragY = e.screenY;
    if (dx || dy) store.windowDragMove(dx, dy);
  };
  const onUp = () => { dragging = false; };

  el?.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  onUnmounted(() => {
    el?.removeEventListener('mousedown', onDown);
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  });
});
</script>

<style scoped>
.titlebar {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  height: 52px;
  padding: 0 12px 0 14px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0.025));
  border-bottom: 1px solid var(--rb-border-soft);
  flex-shrink: 0;
}

.titlebar__title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.titlebar__name {
  font: var(--rb-text-body-em);
  color: var(--rb-text-strong);
  text-shadow: 0 0 14px rgba(236, 72, 153, 0.22);
  white-space: nowrap;
}

.titlebar__hint {
  font: var(--rb-text-micro);
  color: rgba(235, 229, 245, 0.62);
  padding: 3px 9px;
  border: 1px solid var(--rb-border-soft);
  border-radius: var(--rb-radius-full);
  background: rgba(255, 255, 255, 0.06);
  white-space: nowrap;
}

.titlebar__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.icon-btn,
.win-btn {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--rb-radius-full);
  color: rgba(235, 229, 245, 0.7);
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 182, 220, 0.16);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
  transition: background 120ms ease, color 120ms ease, transform 120ms ease,
              border-color 120ms ease, box-shadow 200ms ease;
}

.icon-btn:hover,
.win-btn:hover {
  background: rgba(236, 72, 153, 0.22);
  border-color: rgba(255, 182, 220, 0.34);
  color: var(--rb-text-strong);
  transform: translateY(-1px);
  box-shadow: 0 0 14px rgba(236, 72, 153, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

.icon-btn:active,
.win-btn:active { transform: translateY(0); }

.win-btn--close {
  background: rgba(219, 39, 119, 0.16);
  color: #ffb6dc;
}

.win-btn--close:hover {
  background: var(--rb-grad-pink-hot);
  color: var(--rb-text-on-pink);
  box-shadow: 0 0 18px rgba(236, 72, 153, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.2);
}

.icon-btn.is-active {
  background: var(--rb-grad-pink-hot);
  color: var(--rb-text-on-pink);
  box-shadow: 0 0 18px rgba(236, 72, 153, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.22);
}
</style>
