<template>
  <section class="card card--feeling">
    <div class="indicator">
      <div class="indicator__emoji">{{ feelingEmoji }}</div>
      <div class="indicator__text">
        <span class="indicator__prefix">心情：</span>
        <span class="indicator__label">{{ feelingLabel }}</span>
      </div>
    </div>
  </section>
</template>

<script setup>
defineProps({
  feelingEmoji: { type: String, default: '🌿' },
  feelingLabel: { type: String, default: '平静' },
});
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
  background: radial-gradient(circle at 76% 12%, rgba(168, 85, 247, 0.18), transparent 42%);
}

.card--feeling {
  padding: 12px;
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
