<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
    <n-message-provider placement="top">
      <ChatPanel />
    </n-message-provider>
  </n-config-provider>
</template>

<script setup>
import { NConfigProvider, NMessageProvider } from 'naive-ui';
import { darkTheme, themeOverrides } from './theme/naive-theme.js';
import ChatPanel from './components/chat/ChatPanel.vue';
</script>
