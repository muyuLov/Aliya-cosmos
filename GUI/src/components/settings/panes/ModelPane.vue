<template>
  <section class="pane">
    <PaneHeader title="模型" description="查看当前模型，切换 LLM 提供商。" />
    <div class="pane__card">
      <n-form label-placement="left" label-width="88" :show-feedback="false" class="pane__form">
        <n-form-item label="当前模型">
          <n-input :value="currentModelLabel" readonly />
        </n-form-item>
        <n-form-item label="提供商">
          <n-select
            v-model:value="settingsStore.selectedProvider"
            :options="providerOptions"
            placeholder="选择 LLM 提供商"
          />
        </n-form-item>
        <n-form-item label=" ">
          <n-button
            type="primary"
            :loading="settingsStore.switchingProvider"
            :disabled="!canSwitch"
            @click="handleSwitch"
          >
            切换提供商
          </n-button>
        </n-form-item>
      </n-form>
    </div>
    <n-alert type="warning" :bordered="false" class="pane__hint">
      提供商定义在 <code>data/config/LLMProviders.json</code>；
      切换会更新 <code>main.yml</code> 中 <code>llm.providers.name</code>，
      需重启后端 Agent 服务生效。
    </n-alert>
  </section>
</template>

<script setup>
import { computed } from 'vue';
import { NForm, NFormItem, NInput, NSelect, NButton, NAlert, useMessage } from 'naive-ui';
import { formatModelName } from '../../../utils/formatters.js';
import { settingsStore, switchProvider } from '../useSettingsStore.js';
import PaneHeader from './PaneHeader.vue';

const message = useMessage();

const currentModelLabel = computed(() =>
  settingsStore.currentModel ? formatModelName(settingsStore.currentModel) : '未选择模型'
);

const providerOptions = computed(() =>
  settingsStore.providers.map((p) => ({
    label: `${p.name}${p.isCurrent ? '（当前）' : ''}`,
    value: p.name,
  }))
);

const canSwitch = computed(() =>
  Boolean(settingsStore.selectedProvider) && settingsStore.selectedProvider !== settingsStore.currentProvider
);

async function handleSwitch() {
  const result = await switchProvider();
  if (result.ok) message.success(`已切换至 ${result.name}`);
  else if (result.error) message.error(result.error);
}
</script>
