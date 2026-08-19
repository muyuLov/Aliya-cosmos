// ========== 设置窗口共享状态（模块级单例） ==========
// 配置快照加载、实时状态订阅与设置动作集中在此，
// 标题栏与各面板组件共享同一份数据，避免逐层传递。
import { reactive } from 'vue';

const api = window.settingsAPI;

export const settingsStore = reactive({
  // 连接 / 服务
  wsConnected: false,
  wsHost: '127.0.0.1',
  wsPort: '8765',
  ttsProvider: '',
  tokenTotal: 0,
  appVersion: '',
  // 身份
  aiName: '',
  userName: '',
  initialAiName: '',
  initialUserName: '',
  savingIdentity: false,
  // 模型 / 提供商
  currentProvider: '',
  currentModel: '',
  providers: [],
  selectedProvider: null,
  switchingProvider: false,
  // 生命周期
  loaded: false,
});

/** 身份表单是否有未保存修改 */
export function isIdentityDirty() {
  return settingsStore.aiName !== settingsStore.initialAiName
    || settingsStore.userName !== settingsStore.initialUserName;
}

/** 一次性配置快照 → 填充 store */
export async function loadSettingsConfig() {
  const cfg = await api?.getConfig();
  if (!cfg) return;
  settingsStore.aiName = cfg.identity?.aiName || 'Aliya';
  settingsStore.userName = cfg.identity?.userName || '';
  settingsStore.initialAiName = settingsStore.aiName;
  settingsStore.initialUserName = settingsStore.userName;
  settingsStore.currentProvider = cfg.model?.provider || '';
  settingsStore.currentModel = cfg.model?.model || '';
  settingsStore.selectedProvider = settingsStore.currentProvider || null;
  settingsStore.providers = cfg.providers || [];
  settingsStore.wsHost = cfg.ws?.host || '127.0.0.1';
  settingsStore.wsPort = cfg.ws?.port || '8765';
  settingsStore.ttsProvider = cfg.ttsProvider || '';
  settingsStore.tokenTotal = cfg.tokenUsage?.total ?? 0;
  settingsStore.wsConnected = Boolean(cfg.wsConnected);
  settingsStore.appVersion = cfg.appVersion || '';
  settingsStore.loaded = true;
}

/** 实时状态推送（Token / WS 连接） */
export function onStateSnapshot(snap) {
  if (!snap) return;
  if (typeof snap.connected === 'boolean') settingsStore.wsConnected = snap.connected;
  if (snap.token !== undefined) settingsStore.tokenTotal = snap.token ?? 0;
}

/** 保存身份信息；成功返回 true（提示由调用方通过 message 展示） */
export async function saveIdentity() {
  settingsStore.savingIdentity = true;
  try {
    const result = await api?.saveIdentity({
      aiName: settingsStore.aiName.trim(),
      userName: settingsStore.userName.trim(),
    });
    if (result?.success) {
      settingsStore.initialAiName = settingsStore.aiName.trim();
      settingsStore.initialUserName = settingsStore.userName.trim();
      return { ok: true };
    }
    return { ok: false, error: result?.error || '保存失败' };
  } catch {
    return { ok: false, error: '保存失败：IPC 调用异常' };
  } finally {
    settingsStore.savingIdentity = false;
  }
}

/** 切换 LLM 提供商；成功返回 { ok, name } */
export async function switchProvider() {
  const name = settingsStore.selectedProvider;
  if (!name) return { ok: false, error: '' };
  settingsStore.switchingProvider = true;
  try {
    const result = await api?.switchProvider(name);
    if (result?.success) {
      settingsStore.currentProvider = name;
      settingsStore.currentModel = result.model?.model || settingsStore.currentModel;
      settingsStore.providers = (await api?.listProviders()) || [];
      return { ok: true, name };
    }
    return { ok: false, error: result?.error || '切换失败' };
  } catch {
    return { ok: false, error: '切换失败：IPC 调用异常' };
  } finally {
    settingsStore.switchingProvider = false;
  }
}
