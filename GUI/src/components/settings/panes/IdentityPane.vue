<template>
  <section class="pane">
    <PaneHeader title="角色" description="设置 AI 与用户的称呼，用于记忆格式化和对话上下文。" />
    <div class="pane__card">
      <n-form label-placement="left" label-width="88" :show-feedback="false" class="pane__form">
        <n-form-item label="AI 名称">
          <n-input
            v-model:value="settingsStore.aiName"
            placeholder="如 Aliya"
            maxlength="20"
            clearable
          />
        </n-form-item>
        <n-form-item label="用户名称">
          <n-input
            v-model:value="settingsStore.userName"
            placeholder="你的称呼"
            maxlength="20"
            clearable
          />
        </n-form-item>
        <n-form-item label=" ">
          <n-button
            type="primary"
            :loading="settingsStore.savingIdentity"
            :disabled="!dirty"
            @click="handleSave"
          >
            保存身份
          </n-button>
        </n-form-item>
      </n-form>
    </div>
    <n-alert type="info" :bordered="false" class="pane__hint">
      名称将写入 <code>data/config/main.yml</code> 的 <code>characters</code> 节点，
      用于记忆格式化与对话上下文。修改后需重启后端 Agent 服务生效。
    </n-alert>
  </section>
</template>

<script setup>
import { computed } from 'vue';
import { NForm, NFormItem, NInput, NButton, NAlert, useMessage } from 'naive-ui';
import { settingsStore, isIdentityDirty, saveIdentity } from '../useSettingsStore.js';
import PaneHeader from './PaneHeader.vue';

const message = useMessage();
const dirty = computed(() => isIdentityDirty());

async function handleSave() {
  const result = await saveIdentity();
  if (result.ok) message.success('身份信息已保存');
  else message.error(result.error);
}
</script>
