/**
 * 状态/心情 → Emoji 映射表
 * 与后端 WebSocket 推送的字符串值对应
 */

export const STATUS_EMOJI = {
  '陪伴中': '🌸',
  '思考中': '💭',
  '工作中': '⚡',
  '聆听中': '🫧',
  '提醒中': '🔔',
  '离线':   '💤',
};

export const FEELING_EMOJI = {
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

/** 默认状态/心情回退值 */
export const DEFAULT_STATUS = { emoji: '🌸', label: '陪伴中' };
export const DEFAULT_FEELING = { emoji: '🌿', label: '平静' };
