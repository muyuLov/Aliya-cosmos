// ========== 桌面通知（所有界面不可见时弹窗提醒 Aliya 回复） ==========
const { Notification } = require('electron');
const { logger } = require('./logger');
const { getIdentity } = require('./config');
const { showLive2DWindow } = require('./windows');

function showAliyaNotification(body) {
  if (!body) return;
  try {
    const { aiName } = getIdentity();
    const title = aiName || 'Aliya';
    const maxLen = 120;
    const truncated = body.length > maxLen ? body.slice(0, maxLen) + '…' : body;
    const notification = new Notification({ title, body: truncated });
    notification.on('click', () => {
      // 点击通知 → 唤起 Live2D 主窗口（不存在则重建）
      showLive2DWindow();
    });
    notification.show();
  } catch (e) {
    logger.warn('桌面通知失败', { error: e.message });
  }
}

module.exports = { showAliyaNotification };
