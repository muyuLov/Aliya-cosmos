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

// Agent 侧英文 VAD 情绪标签（agent/emotion/vad.py）→ 侧边栏显示
export const FEELING_EMOJI = {
  neutral: '🌿',
  calm: '🌿',
  happy: '✨',
  excited: '🎉',
  shy: '🌹',
  affectionate: '🌸',
  curious: '🤔',
  confused: '😵',
  tired: '😪',
  sad: '💧',
  anxiety: '😰',
  anger: '😠',
  angry: '😠',
  concerned: '💙',
  surprised: '😲',
  bored: '🥱',
  grateful: '🥹',
  relieved: '😌',
  disgusted: '🤢',
};

/** 英文情绪标签 → 中文显示名（侧边栏友好展示） */
export const FEELING_LABEL = {
  neutral: '平静',
  calm: '平静',
  happy: '开心',
  excited: '激动',
  shy: '害羞',
  affectionate: '温柔',
  curious: '好奇',
  confused: '困惑',
  tired: '疲惫',
  sad: '难过',
  anxiety: '焦虑',
  anger: '生气',
  angry: '生气',
  concerned: '担心',
  surprised: '惊讶',
  bored: '无聊',
  grateful: '感动',
  relieved: '安心',
  disgusted: '厌恶',
};

/** 默认状态/心情回退值 */
export const DEFAULT_STATUS = { emoji: '🌸', label: '陪伴中' };
export const DEFAULT_FEELING = { emoji: '🌿', label: '平静' };
