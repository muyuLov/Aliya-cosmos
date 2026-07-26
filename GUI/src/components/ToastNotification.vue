<template>
  <div class="toast" :class="{ 'is-show': visible }" role="status" aria-live="polite">
    {{ message }}
  </div>
</template>

<script setup>
import { ref } from 'vue';

const visible = ref(false);
const message = ref('');
let timer = null;

function show(msg, duration = 1800) {
  message.value = msg;
  visible.value = true;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    visible.value = false;
  }, duration);
}

defineExpose({ show });
</script>

<style scoped>
.toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 8px 16px;
  background: rgba(15, 13, 31, 0.95);
  color: var(--rb-text-strong);
  font: var(--rb-text-small);
  border-radius: var(--rb-radius-md);
  border: 1px solid var(--rb-border-strong);
  box-shadow: var(--rb-shadow-elevated);
  opacity: 0;
  pointer-events: none;
  transition: opacity 200ms ease, transform 200ms ease;
  z-index: 100;
}

.toast.is-show {
  opacity: 1;
  transform: translateX(-50%) translateY(-4px);
}
</style>
