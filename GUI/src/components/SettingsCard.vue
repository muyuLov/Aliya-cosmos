<template>
  <section class="card-base" style="padding: 10px;">
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
/* action-btn 基础样式见 base.css 全局共享类 */
.action-btn--settings {
  background: rgba(255, 255, 255, 0.06);
}

.action-btn--settings:hover {
  background: rgba(236, 72, 153, 0.20);
}
</style>
