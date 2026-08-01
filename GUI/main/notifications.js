// ========== 桌面通知（窗口隐藏/最小化时弹窗提醒 Aliya 回复） ==========
const { Notification } = require('electron');
const state = require('./state');
const { logger } = require('./logger');
const { getIdentity } = require('./config');

function showAliyaNotification(body) {
  if (!body) return;
  try {
    const { aiName } = getIdentity();
    const title = aiName || 'Aliya';
    const maxLen = 120;
    const truncated = body.length > maxLen ? body.slice(0, maxLen) + '…' : body;
    const notification = new Notification({ title, body: truncated });
    notification.on('click', () => {
      const win = state.sidebarWindow;
      if (!win || win.isDestroyed()) return;
      if (win.isMinimized()) win.restore();
      win.show();
      win.focus();
    });
    notification.show();
  } catch (e) {
    logger.warn('桌面通知失败', { error: e.message });
  }
}

module.exports = { showAliyaNotification };
