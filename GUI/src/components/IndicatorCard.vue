<template>
  <section class="card-base" :class="[`card--${variant}`]">
    <div class="indicator">
      <div class="indicator__emoji">{{ emoji }}</div>
      <div class="indicator__text">
        <span class="indicator__prefix">{{ prefix }}：</span>
        <span class="indicator__label">{{ label }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
defineProps({
  emoji: { type: String, default: '🌿' },
  label: { type: String, default: '平静' },
  prefix: { type: String, default: '状态' },
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'violet'].includes(v),
  },
});
</script>

<style scoped>
/* card-base 的 ::before 径向渐变覆盖为 variant 专属 */
.card--violet::before {
  background: radial-gradient(circle at 76% 12%, rgba(168, 85, 247, 0.18), transparent 42%);
}

.indicator {
  position: relative;
  display: grid;
  grid-template-columns: 52px 1fr;
  align-items: center;
  gap: 8px;
}

.indicator__emoji {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: var(--rb-radius-lg);
  font-size: 28px;
  line-height: 1;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid var(--rb-border-soft);
  filter: drop-shadow(var(--rb-glow-pink));
}

.indicator__text {
  display: flex;
  align-items: baseline;
  gap: 2px;
  min-width: 0;
}

.indicator__prefix {
  font: var(--rb-text-small);
  color: var(--rb-text-muted);
  white-space: nowrap;
}

.indicator__label {
  font: var(--rb-text-body-em);
  color: var(--rb-text-strong);
  letter-spacing: 0.4px;
  text-shadow: 0 0 8px rgba(236, 72, 153, 0.2);
  white-space: nowrap;
}
</style>
