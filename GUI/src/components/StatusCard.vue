<template>
  <section class="card card--status">
    <div class="indicator">
      <div class="indicator__emoji">🌸</div>
      <div class="indicator__text">
        <span class="indicator__prefix">状态：</span>
        <span class="indicator__label">陪伴中</span>
      </div>
    </div>
  </section>
</template>

<script setup>
/**
 * 状态指示卡——展示角色当前活动状态（陪伴/思考/工作等）
 * 未来可由 WebSocket 推送动态更新
 */
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

.card--status,
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
