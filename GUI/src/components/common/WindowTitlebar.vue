<template>
  <header ref="titlebarEl" class="titlebar">
    <div class="titlebar__brand">
      <span class="titlebar__logo">⚙</span>
      <span class="titlebar__title">{{ title }}</span>
      <n-tag
        v-if="connected !== null"
        size="small"
        :bordered="false"
        :type="connected ? 'success' : 'error'"
        class="titlebar__status"
      >
        {{ connected ? '已连接' : '未连接' }}
      </n-tag>
    </div>
    <div class="titlebar__actions">
      <button type="button" class="win-btn" aria-label="最小化" title="最小化" @click="handleMinimize">
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <line x1="1" y1="5" x2="9" y2="5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
        </svg>
      </button>
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
import { onMounted, onUnmounted, ref } from 'vue';
import { NTag } from 'naive-ui';

// 连接徽标：传 null 隐藏（无连接语义的窗口）
const props = defineProps({
  title: { type: String, required: true },
  connected: { type: Boolean, default: null },
  // 窗口控制 API（各窗口 preload 提供的同形接口）
  api: { type: Object, required: true },
});

const titlebarEl = ref(null);

function handleMinimize() { props.api?.minimize(); }
function handleClose() { props.api?.close(); }

// 标题栏拖拽（增量位移上报主进程移动窗口）
let dragging = false;
let dragX = 0;
let dragY = 0;

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
  if (dx || dy) {
    props.api?.windowDragMove?.(dx, dy);
  }
};
const onUp = () => { dragging = false; };

onMounted(() => {
  titlebarEl.value?.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
});
onUnmounted(() => {
  titlebarEl.value?.removeEventListener('mousedown', onDown);
  window.removeEventListener('mousemove', onMove);
  window.removeEventListener('mouseup', onUp);
});
</script>

<style scoped>
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  padding: 0 14px 0 18px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.10), rgba(255, 255, 255, 0.025));
  border-bottom: 1px solid var(--rb-border-soft);
  flex-shrink: 0;
  user-select: none;
}

.titlebar__brand {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.titlebar__logo {
  font-size: 16px;
  filter: drop-shadow(0 0 8px rgba(236, 72, 153, 0.5));
}

.titlebar__title {
  font: var(--rb-text-body-em);
  color: var(--rb-text-strong);
  text-shadow: 0 0 14px rgba(236, 72, 153, 0.22);
  white-space: nowrap;
}

.titlebar__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

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

.win-btn:hover {
  background: rgba(236, 72, 153, 0.22);
  border-color: rgba(255, 182, 220, 0.34);
  color: var(--rb-text-strong);
  transform: translateY(-1px);
  box-shadow: 0 0 14px rgba(236, 72, 153, 0.26), inset 0 1px 0 rgba(255, 255, 255, 0.12);
}

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
</style>
