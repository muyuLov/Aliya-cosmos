import { defineStore } from 'pinia';
import { ref } from 'vue';
import {
  FEELING_EMOJI,
  FEELING_LABEL,
  STATUS_EMOJI,
  DEFAULT_FEELING,
  DEFAULT_STATUS,
} from '../constants/mappings.js';
import { formatModelName } from '../utils/formatters.js';

/**
 * 应用统一状态 Store（Pinia）
 *
 * 单一数据源：心情/状态/Token/模型信息等由主进程经 IPC 批量推送，
 * 在此 Store 中订阅一次，所有组件共享读写，避免重复订阅与重复创建响应式状态。
 */
export const useAppStore = defineStore('app', () => {
  const api = window.sidebarAPI;

  // ---------- 响应式状态 ----------
  const currentFeeling = ref({ ...DEFAULT_FEELING });
  const currentStatus = ref({ ...DEFAULT_STATUS });
  const tokenTotal = ref(0);
  const modelName = ref('获取中…');
  const aiName = ref('Aliya');
  const pinned = ref(true);
  /** Agent WebSocket 连接状态（驱动在线徽章） */
  const connected = ref(false);

  // ---------- 订阅生命周期（单例） ----------

  let snapshotCleanup = null;
  let identityLoaded = false;

  /** 初始化：注册状态快照订阅 + 惰性加载基础信息（仅执行一次） */
  function init() {
    if (snapshotCleanup) return;
    if (api?.onStateSnapshot) {
      snapshotCleanup = api.onStateSnapshot((state) => {
        if (!state) return;
        if (state.feeling) {
          currentFeeling.value = {
            emoji: FEELING_EMOJI[state.feeling] || DEFAULT_FEELING.emoji,
            label: FEELING_LABEL[state.feeling] || state.feeling || DEFAULT_FEELING.label,
            scores: state.scores || null,
          };
        }
        if (state.status) {
          currentStatus.value = {
            emoji: STATUS_EMOJI[state.status] || DEFAULT_STATUS.emoji,
            label: state.status || DEFAULT_STATUS.label,
            state: state.state || '',
          };
        }
        if (state.token !== undefined) tokenTotal.value = state.token ?? 0;
        if (typeof state.connected === 'boolean') connected.value = state.connected;
      });
    }
    loadIdentity();
  }

  /** 释放订阅 */
  function dispose() {
    if (snapshotCleanup) {
      snapshotCleanup();
      snapshotCleanup = null;
    }
  }

  // ---------- 基础信息（惰性加载一次） ----------

  async function loadIdentity() {
    if (identityLoaded) return;
    identityLoaded = true;
    try {
      const [identity, model, pinnedState, usage] = await Promise.all([
        api?.getIdentity(),
        api?.getModel(),
        api?.isPinned(),
        api?.getTokenUsage(),
      ]);
      if (identity?.aiName) aiName.value = identity.aiName;
      if (model?.model && model.model !== '未知') {
        modelName.value = formatModelName(model.model);
      }
      pinned.value = pinnedState ?? true;
      if (usage?.total !== undefined) tokenTotal.value = usage.total ?? 0;
    } catch { /* 加载失败使用默认值 */ }
  }

  // ---------- 窗口控制 ----------

  function minimize() { api?.minimize(); }
  function close() { api?.close(); }

  async function togglePin() {
    pinned.value = (await api?.togglePin()) ?? pinned.value;
    return pinned.value;
  }

  function windowDragMove(dx, dy) {
    api?.windowDragMove(dx, dy);
  }

  // ---------- 业务操作 ----------

  async function openChat() { await api?.openChat(); }
  async function openSettings() { await api?.openSettings(); }

  async function listProviders() {
    return (await api?.listProviders()) ?? [];
  }

  async function switchProvider(name) {
    const result = (await api?.switchProvider(name)) ?? { success: false };
    if (result.success && result.model?.model) {
      modelName.value = formatModelName(result.model.model);
    }
    return result;
  }

  /** 订阅主进程事件（如 open-chat / switch-model / open-settings） */
  function subscribeEvents(handler) {
    if (!api?.onEvent) return () => {};
    return api.onEvent(handler);
  }

  return {
    currentFeeling,
    currentStatus,
    tokenTotal,
    modelName,
    aiName,
    pinned,
    connected,
    init,
    dispose,
    minimize,
    close,
    togglePin,
    windowDragMove,
    openChat,
    openSettings,
    listProviders,
    switchProvider,
    subscribeEvents,
  };
});
