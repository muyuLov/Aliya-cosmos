<template>
  <div class="pane-header">
    <h2 class="pane-header__title">{{ title }}</h2>
    <p v-if="description" class="pane-header__desc">{{ description }}</p>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  description: { type: String, default: '' },
});
</script>

<style scoped>
.pane-header {
  margin-bottom: var(--rb-space-lg);
}

.pane-header__title {
  margin: 0;
  font: var(--rb-text-title);
  color: var(--rb-text-strong);
  text-shadow: 0 0 16px rgba(236, 72, 153, 0.2);
}

.pane-header__desc {
  margin: 6px 0 0;
  font: var(--rb-text-small);
  color: var(--rb-text-muted);
  line-height: 1.6;
}
</style>
