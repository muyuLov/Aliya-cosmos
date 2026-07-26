<template>
  <section class="card" style="padding: 10px;">
    <button type="button" class="action-btn action-btn--settings" title="打开设置" @click="$emit('open-settings')">
      <span class="action-btn__icon" aria-hidden="true">⚙</span>
      <span>设置</span>
    </button>
  </section>
</template>

<script setup>
defineEmits(['open-settings']);
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

.action-btn--settings {
  background: rgba(255, 255, 255, 0.06);
}

.action-btn--settings:hover {
  background: rgba(236, 72, 153, 0.20);
}

.action-btn__icon {
  font-size: 15px;
  width: 18px;
  text-align: center;
  filter: drop-shadow(0 0 5px rgba(236, 72, 153, 0.45));
}
</style>
