// ========== 阿库露 Live2D 模型 Profile（Soullink Emotion SDK） ==========
// 依据 src/assets/live2d/阿库露_vts.cdi3.json 的真实参数 ID 手工映射。
// 模型是 VTube Studio 导出：标准 Cubism 参数（头部/身体/眼/眉/嘴/呼吸）
// + VTS 特有“按键表情”参数（RedButton/爱心眼/星星眼等 0~1 开关）。
//
// schemaVersion 2 支持 customParams 与 privateEmotionMap：
//   - parameterMap      标准 FACS → Cubism 参数
//   - customParams      自定义通道（鼠标注视偏移等）→ Cubism 参数
//   - privateEmotionMap VTS 按键表情 → 情绪/VAD 声明式驱动
//   - expressionMap     情绪/变体 → exp3 原生表情
//   - motionMap         情绪/变体 → motion3 原生动作
//   - nativeAnimations  模型可用的 exp3 / motion3 目录

export const AKULU_PROFILE = {
  modelId: 'akulu_vts',
  displayName: '阿库露',
  version: '1.0.0',
  schemaVersion: 2,
  // 构建后页面位于 dist/live2d.html，模型在 dist/assets/live2d/ 下
  modelPath: './assets/live2d/阿库露_vts.model3.json',

  parameterMap: {
    // ── 头部 ──
    headX: { target: 'ParamAngleX', mode: 'set', scale: 30, min: -30, max: 30 },
    headY: { target: 'ParamAngleY', mode: 'set', scale: 30, min: -30, max: 30 },
    headZ: { target: 'ParamAngleZ', mode: 'set', scale: 30, min: -30, max: 30 },
    // ── 身体 ──
    bodyX: { target: 'ParamBodyAngleX', mode: 'set', scale: 10, min: -10, max: 10 },
    bodyY: { target: 'ParamBodyAngleY', mode: 'set', scale: 10, min: -10, max: 10 },
    bodyZ: { target: 'ParamBodyAngleZ', mode: 'set', scale: 10, min: -10, max: 10 },
    // ── 眼睛 ──
    eyeOpen: { targets: ['ParamEyeLOpen', 'ParamEyeROpen'], mode: 'set', scale: 1, min: 0, max: 1 },
    eyeBlinkL: { target: 'ParamEyeLOpen', mode: 'add', scale: -1, min: 0, max: 1 },
    eyeBlinkR: { target: 'ParamEyeROpen', mode: 'add', scale: -1, min: 0, max: 1 },
    gazeX: { target: 'ParamEyeBallX', mode: 'set', scale: 1, min: -1, max: 1 },
    gazeY: { target: 'ParamEyeBallY', mode: 'set', scale: 1, min: -1, max: 1 },
    // ── 眉毛（阿库露使用上下 / 角度双通道） ──
    browInnerUp: { targets: ['ParamBrowLY', 'ParamBrowRY'], mode: 'set', scale: 1, min: -1, max: 1 },
    browOuterUp: { targets: ['ParamBrowLAngle', 'ParamBrowRAngle'], mode: 'set', scale: 1, min: -1, max: 1 },
    browDown: { targets: ['ParamBrowLForm', 'ParamBrowRForm'], mode: 'subtract', scale: 0.85, min: -1, max: 1 },
    // ── 嘴部 ──
    mouthOpen: { target: 'ParamMouthOpenY', mode: 'set', scale: 1, min: 0, max: 1 },
    mouthSmile: { target: 'ParamMouthForm', mode: 'set', scale: 1, min: -1, max: 1 },
    mouthFrown: { target: 'ParamMouthForm', mode: 'subtract', scale: 1, min: -1, max: 1 },
    mouthPucker: { target: 'mouth_pucker', mode: 'set', scale: 1, min: 0, max: 1 },
    // ── 其他 ──
    breath: { target: 'ParamBreath', mode: 'set', scale: 1, min: 0, max: 1 },
  },

  // 鼠标注视偏移：add 模式叠加在 SDK gaze 之上，空闲置 0 不影响内置视线
  customParams: {
    mouseGazeX: { target: 'ParamEyeBallX', mode: 'add', scale: 0.45, min: -1, max: 1 },
    mouseGazeY: { target: 'ParamEyeBallY', mode: 'add', scale: 0.45, min: -1, max: 1 },
  },

  // VTS 按键表情（0=关 1=开）：由 VAD 情绪连续性驱动
  privateEmotionMap: {
    blushFace: {
      target: 'RedButton',
      category: 'blush',
      emotions: ['shy', 'affectionate'],
      priority: 92,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    loveEyes: {
      target: 'aixiButton3',
      category: 'positiveEye',
      emotions: ['affectionate', 'happy'],
      priority: 90,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    starEyes: {
      targets: ['StarButton', 'StarButton2'],
      category: 'privateEffect',
      emotions: ['excited', 'happy'],
      priority: 88,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    squeezedEyes: {
      target: 'CryButton3',
      category: 'positiveEye',
      emotions: ['happy', 'affectionate'],
      priority: 86,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    oMouth: {
      target: 'RedButton2',
      category: 'privateEffect',
      emotions: ['surprised'],
      priority: 85,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    shrinkPupil: {
      target: 'eyesmallerButton',
      category: 'privateEffect',
      emotions: ['surprised', 'confused'],
      priority: 84,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    dullFace: {
      target: 'CryButton4',
      category: 'privateEffect',
      // 注意：不能包含 neutral —— neutral_ack 变体名含 "neutral" 子串会误触发发呆脸
      emotions: ['confused'],
      priority: 82,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    cryEyes: {
      target: 'CryButton',
      category: 'tear',
      emotions: ['sad'],
      priority: 95,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    angerMark: {
      targets: ['AngryButton', 'AngryButton2'],
      category: 'anger',
      emotions: ['anger', 'angry'],
      priority: 93,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    sweatDrop: {
      target: 'EXPButton',
      category: 'sweat',
      emotions: ['concerned', 'anxiety'],
      priority: 80,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    awkwardFace: {
      target: 'BlackButton2',
      category: 'privateEffect',
      emotions: ['confused'],
      priority: 81,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
    loveSymbol: {
      targets: ['LoveButton', 'LoveButton2'],
      category: 'privateEffect',
      emotions: ['affectionate'],
      priority: 87,
      exclusiveGroup: 'face-button',
      activeValue: 1,
      neutralValue: 0,
      source: 'manual',
      confidence: 1,
    },
  },

  // 原生表情（exp3）：情绪 → 表情名称（需与 model3.json Expressions 注册名一致）
  expressionMap: {
    shy: '害羞脸',
    affectionate: '脸红',
    happy: '眯眯眼脸',
    surprised: '瞳孔缩小',
    excited: '眯眯眼脸',
    confused: '呆呆脸',
    sad: '脸黑',
    angry: '脸黑',
  },

  // 原生动作（motion3）：情绪 → 动作组/索引
  motionMap: {
    happy: { group: '招手', index: 0, priority: 'normal' },
  },

  nativeAnimations: {
    expressions: [
      { name: '呆呆脸', file: '呆呆脸.exp3.json' },
      { name: '害羞脸', file: '害羞脸.exp3.json' },
      { name: '挥挥手', file: '挥挥手.exp3.json' },
      { name: '脸黑', file: '脸黑.exp3.json' },
      { name: '脸红', file: '脸红.exp3.json' },
      { name: '眯眯眼脸', file: '眯眯眼脸.exp3.json' },
      { name: '瞳孔缩小', file: '瞳孔缩小.exp3.json' },
      { name: 'O形嘴', file: 'O形嘴.exp3.json' },
    ],
    motions: [
      { group: '招手', index: 0, file: '招手.motion3.json' },
    ],
  },

  idleConfig: {},
};
