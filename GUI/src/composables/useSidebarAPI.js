import { ref } from 'vue';
import { FEELING_EMOJI, STATUS_EMOJI, DEFAULT_FEELING, DEFAULT_STATUS } from '../constants/mappings.js';

/**
 * 封装 Electron preload 桥接 API 的访问
 * 提供心情更新、状态更新、Token 用量等响应式状态自动订阅
 */
export function useSidebarAPI() {
  const api = window.sidebarAPI;

  // 响应式状态
  const currentFeeling = ref({ ...DEFAULT_FEELING });
  const currentStatus = ref({ ...DEFAULT_STATUS });
  const tokenTotal = ref(0);
  const modelName = ref('获取中…');
  const aiName = ref('Aliya');

  let emotionCleanup = null;
  let statusCleanup = null;
  let tokenCleanup = null;

  // ---------- 窗口控制 ----------

  function minimize() { api?.minimize(); }
  function close() { api?.close(); }

  async function togglePin() {
    return await api?.togglePin() ?? false;
  }

  async function isPinned() {
    return await api?.isPinned() ?? false;
  }

  // ---------- 业务操作 ----------

  async function openChat() { await api?.openChat(); }
  async function openSettings() { await api?.openSettings(); }

  async function getModel() {
    return await api?.getModel() ?? { provider: '', model: '未知', url: '' };
  }

  async function getIdentity() {
    return await api?.getIdentity() ?? { aiName: 'Aliya', userName: '' };
  }

  async function listProviders() {
    return await api?.listProviders() ?? [];
  }

  async function switchProvider(name) {
    return await api?.switchProvider(name) ?? { success: false };
  }

  async function getTokenUsage() {
    return await api?.getTokenUsage() ?? { total: 0, input: 0, output: 0 };
  }

  // ---------- 订阅 ----------

  function subscribeEmotion() {
    if (!api?.onEmotionChanged) return;
    emotionCleanup = api.onEmotionChanged(({ feeling, scores }) => {
      currentFeeling.value = {
        emoji: FEELING_EMOJI[feeling] || DEFAULT_FEELING.emoji,
        label: feeling || DEFAULT_FEELING.label,
        scores: scores || null,
      };
    });
  }

  function subscribeStatus() {
    if (!api?.onStatusChanged) return;
    statusCleanup = api.onStatusChanged(({ status, state }) => {
      currentStatus.value = {
        emoji: STATUS_EMOJI[status] || DEFAULT_STATUS.emoji,
        label: status || DEFAULT_STATUS.label,
        state: state || '',
      };
    });
  }

  function subscribeToken() {
    if (!api?.onTokenUsageChanged) return;
    tokenCleanup = api.onTokenUsageChanged((usage) => {
      if (usage) tokenTotal.value = usage.total ?? 0;
    });
  }

  function subscribeEvents(handler) {
    if (!api?.onEvent) return () => {};
    return api.onEvent(handler);
  }

  function setupSubscriptions() {
    subscribeEmotion();
    subscribeStatus();
    subscribeToken();
  }

  function teardownSubscriptions() {
    if (emotionCleanup) emotionCleanup();
    if (statusCleanup) statusCleanup();
    if (tokenCleanup) tokenCleanup();
  }

  return {
    currentFeeling,
    currentStatus,
    tokenTotal,
    modelName,
    aiName,
    minimize,
    close,
    togglePin,
    isPinned,
    openChat,
    openSettings,
    getModel,
    getIdentity,
    listProviders,
    switchProvider,
    getTokenUsage,
    setupSubscriptions,
    teardownSubscriptions,
  };
}
