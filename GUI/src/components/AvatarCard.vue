<template>
  <section class="profile">
    <img class="profile__avatar" :src="avatarSrc" :alt="aiName" draggable="false" />
    <div class="profile__name">{{ aiName }}</div>
    <div class="profile__online" :class="{ 'is-offline': !online }">
      <span class="profile__dot" aria-hidden="true"></span>
      <span>{{ online ? '在线' : '离线' }}</span>
    </div>
  </section>
</template>

<script setup>
import avatarSrc from '@/assets/avatar.png';

defineProps({
  aiName: { type: String, default: 'Aliya' },
  online: { type: Boolean, default: true },
});
</script>

<style scoped>
.profile {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 6px;
  padding: 4px 0 6px;
}

.profile__avatar {
  width: 70px;
  height: 70px;
  border-radius: var(--rb-radius-full);
  object-fit: cover;
  display: block;
  border: 2px solid rgba(255, 182, 220, 0.46);
  box-shadow: var(--rb-glow-pink), inset 0 1px 0 rgba(255, 255, 255, 0.14);
  pointer-events: none;
  -webkit-user-drag: none;
}

.profile__name {
  font: var(--rb-text-title);
  color: var(--rb-text-strong);
  text-shadow: 0 0 12px rgba(236, 72, 153, 0.28);
  margin-top: 2px;
}

.profile__online {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: var(--rb-text-small);
  color: rgba(220, 252, 231, 0.92);
  padding: 3px 10px;
  border-radius: var(--rb-radius-full);
  background: rgba(34, 197, 94, 0.12);
  border: 1px solid rgba(34, 197, 94, 0.32);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.profile__online.is-offline {
  color: rgba(254, 202, 202, 0.92);
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(248, 113, 113, 0.30);
}

.profile__online.is-offline .profile__dot {
  background: linear-gradient(135deg, #fca5a5, #ef4444);
  box-shadow: 0 0 10px rgba(239, 68, 68, 0.65);
}

.profile__dot {
  width: 7px;
  height: 7px;
  border-radius: var(--rb-radius-full);
  background: linear-gradient(135deg, #4ade80, #22c55e);
  box-shadow: 0 0 10px rgba(34, 197, 94, 0.75);
}
</style>
