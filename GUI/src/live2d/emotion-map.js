// ========== Agent 情绪 → Soullink SDK 情绪映射 ==========
// Agent 侧定义 9 种中文情绪标签（agent/emotion/feeling_scores.py），
// 这里映射到 SDK 的 emotion + variant（变体）以及 VAD 强度。

export const FEELING_TO_EMOTION = {
  平静: { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.35 },
  开心: { emotion: 'happy', variant: 'bright_smile', intensity: 0.75 },
  温柔: { emotion: 'affectionate', variant: 'warm', intensity: 0.7 },
  激动: { emotion: 'excited', variant: 'sparkle', intensity: 0.85 },
  撒娇: { emotion: 'affectionate', variant: 'warm', intensity: 0.7 },
  担心: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.6 },
  难过: { emotion: 'sad', variant: 'downcast', intensity: 0.7 },
  感动: { emotion: 'affectionate', variant: 'tender', intensity: 0.8 },
  害羞: { emotion: 'shy', variant: 'bashful', intensity: 0.7 },
};

// 兜底：未知标签回到中性
export const FALLBACK_EMOTION = { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.35 };

/**
 * 将 Agent 情绪标签解析为 SDK 情绪目标
 * @param {string|null|undefined} feeling 中文情绪标签
 * @returns {{emotion: string, variant: string, intensity: number}}
 */
export function resolveEmotion(feeling) {
  if (!feeling) return FALLBACK_EMOTION;
  return FEELING_TO_EMOTION[feeling] || FALLBACK_EMOTION;
}
