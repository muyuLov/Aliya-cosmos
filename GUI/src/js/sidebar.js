// 昔涟状态面板 - 交互逻辑
// 通过 preload 暴露的 window.sidebarAPI 与主进程通信
(function () {
  'use strict';

  // ===== DOM 引用 =====
  const pinBtn = document.getElementById('pin-btn');
  const minBtn = document.getElementById('min-btn');
  const closeBtn = document.getElementById('close-btn');
  const settingsBtn = document.getElementById('settings-btn');
  const modelSwitchBtn = document.getElementById('model-switch-btn');
  const openChatBtn = document.getElementById('open-chat-btn');
  const feedingModel = document.getElementById('feeding-model');
  const providerPicker = document.getElementById('provider-picker');
  const providerList = document.getElementById('provider-list');
  const feelingEmoji = document.getElementById('feeling-emoji');
  const feelingLabel = document.getElementById('feeling-label');
  const toast = document.getElementById('toast');
  const tokenUsageEl = document.getElementById('token-usage');

  // ===== 状态映射 =====
  const STATUS_EMOJI = {
    '陪伴中': '🌸',
    '思考中': '💭',
    '工作中': '⚡',
    '聆听中': '🫧',
    '提醒中': '🔔',
    '离线':   '💤',
  };

  const FEELING_EMOJI = {
    '平静': '🌿',
    '开心': '✨',
    '温柔': '🌸',
    '激动': '🎉',
    '撒娇': '🥺',
    '担心': '💙',
    '难过': '💧',
    '感动': '🥹',
    '害羞': '🌹',
  };

  // ===== 工具：Toast 提示 =====
  let toastTimer = null;
  function showToast(message, duration = 1800) {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.add('is-show');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.classList.remove('is-show');
    }, duration);
  }

  // ===== 工具：切换按钮激活态 =====
  function setActive(btn, active) {
    btn.classList.toggle('is-active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  }

  // ===== 工具：Token 自动换算 =====
  function formatTokenCount(n) {
    if (typeof n !== 'number' || n < 0) return '—';
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(n);
  }

  // ===== 工具：格式化模型名为友好显示 =====
  function formatModelName(raw) {
    if (!raw || raw === '未知') return '未选择模型';
    // deepseek-v4-flash → DeepSeek V4 Flash
    return raw
      .split(/[-_@]/)
      .map((seg) => seg.charAt(0).toUpperCase() + seg.slice(1))
      .join(' ');
  }

  // ===== API 守卫 =====
  // 当通过 file:// 直接打开（无 preload）时，给一组 no-op 让页面不报错
  if (!window.sidebarAPI) {
    window.sidebarAPI = {
      minimize: async () => {},
      close: async () => {},
      togglePin: async () => false,
      isPinned: async () => false,
      openChat: async () => {},
      switchModel: async () => {},
      openSettings: async () => {},
      getModel: async () => ({ provider: '', model: '未选择模型', url: '' }),
      getIdentity: async () => ({ aiName: 'Aliya', userName: '' }),
      getTokenUsage: async () => ({ total: 0, input: 0, output: 0 }),
      listProviders: async () => [],
      switchProvider: async () => ({ success: false }),
      onEvent: () => () => {},
      onEmotionChanged: () => () => {},
      onTokenUsageChanged: () => () => {},
    };
  }

  // ===== 事件绑定 =====

  // 置顶切换
  pinBtn.addEventListener('click', async () => {
    const pinned = await window.sidebarAPI.togglePin();
    setActive(pinBtn, pinned);
    pinBtn.setAttribute('aria-label', pinned ? '取消置顶' : '置顶');
    pinBtn.setAttribute('title', pinned ? '取消置顶' : '置顶');
    showToast(pinned ? '已置顶' : '已取消置顶');
  });

  // 最小化
  minBtn.addEventListener('click', () => {
    window.sidebarAPI.minimize();
  });

  // 关闭
  closeBtn.addEventListener('click', () => {
    window.sidebarAPI.close();
  });

  // 打开聊天
  openChatBtn.addEventListener('click', async () => {
    await window.sidebarAPI.openChat();
    showToast('正在打开聊天…');
  });

  // 切换模型：点击切换按钮显示/隐藏选择浮层
  modelSwitchBtn.addEventListener('click', async () => {
    if (providerPicker && !providerPicker.hidden) {
      providerPicker.hidden = true;
      return;
    }
    await showProviderPicker();
  });

  // 点击外部关闭浮层
  document.addEventListener('click', (e) => {
    if (providerPicker && !providerPicker.hidden) {
      const target = e.target;
      if (!providerPicker.contains(target) && target !== modelSwitchBtn) {
        providerPicker.hidden = true;
      }
    }
  });

  /** 显示 provider 选择浮层 */
  async function showProviderPicker() {
    if (!providerPicker || !providerList) return;
    try {
      const providers = await window.sidebarAPI.listProviders();
      if (!providers || providers.length === 0) {
        showToast('未检测到可用模型');
        return;
      }
      providerList.innerHTML = providers
        .slice().sort((a, b) => (a.isCurrent ? -1 : b.isCurrent ? 1 : 0))
        .map(p => `
        <button type="button" class="provider-picker__item${p.isCurrent ? ' is-current' : ''}"
                data-provider="${p.name}">
          <div class="provider-picker__item-info">
            <span class="provider-picker__item-name">${p.name}</span>
            <span class="provider-picker__item-model">${formatModelName(p.model)}</span>
          </div>
        </button>
      `).join('');
      providerPicker.hidden = false;

      // 绑定选项点击
      providerList.querySelectorAll('.provider-picker__item').forEach(btn => {
        btn.addEventListener('click', async () => {
          const name = btn.dataset.provider;
          if (btn.classList.contains('is-current')) {
            providerPicker.hidden = true;
            return;
          }
          const result = await window.sidebarAPI.switchProvider(name);
          if (result.success) {
            feedingModel.textContent = formatModelName(result.model.model);
            showToast(`已切换至 ${name}`);
          } else {
            showToast('切换失败');
          }
          providerPicker.hidden = true;
        });
      });
    } catch (e) {
      showToast('获取模型列表失败');
      providerPicker.hidden = true;
    }
  }

  // 设置
  settingsBtn.addEventListener('click', async () => {
    await window.sidebarAPI.openSettings();
    showToast('正在打开设置…');
  });

  // ===== 初始化：同步置顶状态 + 自动获取模型 + 角色身份 =====
  (async function init() {
    try {
      const pinned = await window.sidebarAPI.isPinned();
      setActive(pinBtn, pinned);
      pinBtn.setAttribute('aria-label', pinned ? '取消置顶' : '置顶');
      pinBtn.setAttribute('title', pinned ? '取消置顶' : '置顶');
    } catch (e) {
      // 静默失败
    }

    // 自动获取角色身份信息
    try {
      const identity = await window.sidebarAPI.getIdentity();
      const aiName = identity?.aiName || 'Aliya';
      const titlebarName = document.querySelector('.titlebar__name');
      const profileName = document.querySelector('.profile__name');
      const avatarImg = document.querySelector('.profile__avatar');
      const versionEl = document.querySelector('.version');
      if (titlebarName) titlebarName.textContent = aiName;
      if (profileName) profileName.textContent = aiName;
      if (avatarImg) avatarImg.alt = aiName;
      if (versionEl) versionEl.textContent = `${aiName} v0.0.1`;
      document.title = `${aiName} · 状态`;
    } catch (e) {
      // 静默失败，保持 HTML 默认值
    }

    // 自动获取模型配置
    try {
      const model = await window.sidebarAPI.getModel();
      if (model && model.model && model.model !== '未知') {
        const feedingEl = document.getElementById('feeding-model');
        if (feedingEl) {
          feedingEl.textContent = formatModelName(model.model);
        }
      }
    } catch (e) {
      // 静默失败，保持 HTML 默认值
    }

    // 自动获取并显示 Token 用量
    try {
      const usage = await window.sidebarAPI.getTokenUsage();
      if (usage && tokenUsageEl) {
        tokenUsageEl.textContent = formatTokenCount(usage.total);
      }
    } catch (e) {
      // 静默失败
    }

    // 订阅心情更新（从 agent WebSocket 推送）
    window.sidebarAPI.onEmotionChanged?.(({ feeling }) => {
      const emoji = FEELING_EMOJI[feeling] || '🌿';
      if (feelingEmoji) feelingEmoji.textContent = emoji;
      if (feelingLabel) feelingLabel.textContent = feeling || '平静';
    });

    // 订阅 Token 用量更新（从 agent WebSocket 推送）
    window.sidebarAPI.onTokenUsageChanged?.((usage) => {
      if (!usage || !tokenUsageEl) return;
      tokenUsageEl.textContent = formatTokenCount(usage.total);
    });
  })();

  // ===== 监听主进程事件（未来扩展） =====
  window.sidebarAPI.onEvent?.((payload) => {
    if (!payload || !payload.type) return;
    switch (payload.type) {
      case 'open-chat':
        showToast('聊天窗口未配置');
        break;
      case 'switch-model':
        showToast('模型切换未配置');
        break;
      case 'open-settings':
        showToast('设置窗口未配置');
        break;
    }
  });

  // 暴露 emoji 映射（供未来动态更新使用）
  window.__cyreneState__ = { STATUS_EMOJI, FEELING_EMOJI };
})();
