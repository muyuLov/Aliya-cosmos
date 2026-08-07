// ========== Agent 情绪 → Soullink SDK 情绪映射 ==========
// Agent 侧定义英文 VAD 情绪标签（agent/emotion/vad.py 的 emotionVADPresets），
// 这里映射到 SDK 的 emotion + variant（变体）以及 VAD 强度。

export const FEELING_TO_EMOTION = {
  neutral: { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.35 },
  calm: { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.4 },
  happy: { emotion: 'happy', variant: 'bright_smile', intensity: 0.75 },
  excited: { emotion: 'excited', variant: 'sparkle', intensity: 0.85 },
  shy: { emotion: 'shy', variant: 'bashful', intensity: 0.7 },
  affectionate: { emotion: 'affectionate', variant: 'warm', intensity: 0.7 },
  curious: { emotion: 'excited', variant: 'sparkle', intensity: 0.6 },
  confused: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.6 },
  tired: { emotion: 'sad', variant: 'downcast', intensity: 0.5 },
  sad: { emotion: 'sad', variant: 'downcast', intensity: 0.7 },
  anxiety: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.7 },
  anger: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.8 },
  angry: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.8 }, // anger 别名
  concerned: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.6 },
  surprised: { emotion: 'surprised', variant: 'soft_surprise', intensity: 0.6 },
  bored: { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.3 },
  grateful: { emotion: 'affectionate', variant: 'tender', intensity: 0.8 },
  relieved: { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.45 },
  disgusted: { emotion: 'concerned', variant: 'soft_concern', intensity: 0.7 },
};

// 兜底：未知标签回到中性
export const FALLBACK_EMOTION = { emotion: 'neutral', variant: 'neutral_ack', intensity: 0.35 };

/**
 * 将 Agent 情绪标签解析为 SDK 情绪目标
 * @param {string|null|undefined} feeling 英文情绪标签（如 happy / sad / neutral）
 * @returns {{emotion: string, variant: string, intensity: number}}
 */
export function resolveEmotion(feeling) {
  if (!feeling) return FALLBACK_EMOTION;
  return FEELING_TO_EMOTION[feeling] || FALLBACK_EMOTION;
}
