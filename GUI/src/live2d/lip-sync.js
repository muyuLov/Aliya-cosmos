// ========== 口型同步引擎（TTS 音频特征 → Live2D 面部动画） ==========
// 输入特征（来自后端 AudioPlayer，经 IPC onMouthOpen 推送）：
//   volume  (0~1) — 归一化 RMS 音量，驱动开合幅度
//   centroid(0~1) — 频谱质心比，低→元音(嘴张) 高→辅音(嘴收窄)
//   zcr     (0~1) — 过零率，低→浊音 高→清音/停顿
//
// 输出参数（自动检测模型支持的口型参数）：
//   ParamMouthOpenY — 嘴张开/闭合（主参数）
//   ParamMouthForm — 嘴变形/形状（辅参数，区分元音口型）
//
// 情绪调制：emotionFactor 根据当前情绪缩放口型幅度
import { LIP_SYNC_CONFIG } from './constants.js';

/**
 * 创建口型同步引擎实例。
 * 返回对象：
 *   - setup(api)     注册 IPC onMouthOpen 监听（音频特征驱动）
 *   - patch(model)   patch coreModel.update() 注入口型参数
 *   - update(dt)     每帧推进口型参数（帧率无关，20/60fps 行为一致）
 *   - setEmotion(f)  根据情绪调整口型幅度因子
 */
export function createLipSync() {
  // ── 运行时状态 ────────────────────────────────────────────────
  let lipAudioVolume = 0.0;      // 原始音量 (IPC 写入)
  let lipCentroid = 0.5;         // 频谱质心 (IPC 写入)
  let lipZcr = 0.0;              // 过零率 (IPC 写入)
  let lipOpenValue = 0.0;        // ParamMouthOpenY 当前值（平滑后）
  let lipFormValue = 0.0;        // ParamMouthForm 当前值（平滑后）
  let lipEmotionFactor = 1.0;    // 当前情绪幅度因子

  // 检测到的口型参数（由 detectMouthParam 写入）
  let mouthOpenParamId = 'ParamMouthOpenY';
  let mouthFormParamId = 'ParamMouthForm';
  let mouthOpenMax = 1;
  let mouthFormMax = 1;

  /**
   * 口型同步：每帧计算 mouthOpenValue（Attack/Release 非对称平滑），
   * 实际写入参数由 patch 在 coreModel.update() 内部完成。
   *
   * Attack/Release 模型（帧率无关）：
   *   - target = mouthAudioVolume（有声时 0~1，静音时 0）
   *   - target > current → 开口 → Attack 速率（快 0.40）
   *   - target < current → 闭口 → Release 速率（慢 0.15）
   *   - 统一分支，无硬切换，过渡自然
   *   - dt 来自 app.ticker，空闲 20fps / 活跃 60fps 下行为一致
   *
   * @param {number} dt - 距上帧的秒数（app.ticker.deltaMS / 1000）
   */
  function update(dt) {
    dt = Math.min(dt, 0.05);
    const cfg = LIP_SYNC_CONFIG;

    const hasAudio = lipAudioVolume > cfg.volumeFloor;

    // ── ① 目标音量（情绪调制） ──
    const volumeTarget = hasAudio
      ? Math.min(1, lipAudioVolume * lipEmotionFactor)
      : 0;

    // ── ② 口型开合（ParamMouthOpenY）带 Attack/Release ──
    const openRate = volumeTarget > lipOpenValue ? cfg.openRate : cfg.closeRate;
    const lerpOpen = 1 - Math.pow(1 - openRate, dt * 60);
    lipOpenValue += (volumeTarget - lipOpenValue) * lerpOpen;
    if (lipOpenValue < 0.001) lipOpenValue = 0;

    // ── ③ 口型形状（ParamMouthForm）由频谱质心驱动 ──
    //     centroid > threshold → 辅音(ss/ff)，口型扁平
    //     centroid < threshold → 元音(ah)，口型圆张
    let formTarget = 0.5; // 中性值
    if (hasAudio) {
      const centroidBias = (lipCentroid - cfg.centroidThreshold) * 2;
      // centroidBias: <0 → 元音(嘴圆)，>0 → 辅音(嘴扁)
      formTarget = 0.5 + centroidBias * cfg.formMix;
      formTarget = Math.max(0, Math.min(1, formTarget));

      // 过零率高（清辅音）→ 短暂微闭合
      if (lipZcr > cfg.zcrGate) {
        formTarget -= cfg.zcrCloseAmount;
      }
    }
    const lerpForm = 1 - Math.pow(1 - cfg.formOpenRate, dt * 60);
    lipFormValue += (formTarget - lipFormValue) * lerpForm;
    if (Math.abs(lipFormValue - 0.5) < 0.005) lipFormValue = 0.5;

    // ── ④ 空闲微随机（死寂时给嘴唇一点点自然颤动） ──
    if (!hasAudio && cfg.microRandom > 0) {
      lipOpenValue += (Math.random() - 0.5) * cfg.microRandom * dt * 10;
      lipOpenValue = Math.max(0, Math.min(0.03, lipOpenValue));
    }
  }

  /**
   * 检测 Live2D 模型的口型参数 ID 和值域范围。
   * 分别查找开合（ParamMouthOpenY）和形状（ParamMouthForm）参数。
   */
  function detectMouthParam(coreModel) {
    const openCandidates = ['ParamMouthOpenY', 'ParamA', 'MouthOpen'];
    const formCandidates = ['ParamMouthForm', 'ParamMouthX', 'MouthSmile'];

    for (const candidates of [openCandidates, formCandidates]) {
      const isOpen = candidates === openCandidates;
      for (const paramId of candidates) {
        try {
          const idx = coreModel.getParameterIndex(paramId);
          if (idx >= 0) {
            const maxVal = coreModel._model?.parameters?.maximumValues?.[idx];
            const minVal = coreModel._model?.parameters?.minimumValues?.[idx];
            if (isOpen) {
              mouthOpenParamId = paramId;
              if (typeof maxVal === 'number' && maxVal > 0) mouthOpenMax = maxVal;
            } else {
              mouthFormParamId = paramId;
              if (typeof maxVal === 'number' && maxVal > 0) mouthFormMax = maxVal;
            }
            console.log(`[LipSync] 检测到参数 | ${isOpen ? '开合' : '形状'}=${paramId} min=${minVal} max=${maxVal}`);
            break;
          }
        } catch (_) { /* 继续下一个 */ }
      }
    }
    console.log(`[LipSync] 参数检测完成 | open=${mouthOpenParamId}(${mouthOpenMax}) form=${mouthFormParamId}(${mouthFormMax})`);
  }

  /**
   * Patch coreModel.update() — 在 motion/物理之后、提交之前注入口型参数。
   * 同时写入 ParamMouthOpenY（开合）和 ParamMouthForm（形状）。
   */
  function patch(coreModel) {
    if (!coreModel || typeof coreModel.setParameterValueById !== 'function') {
      console.warn('[LipSync] coreModel API 不可用');
      return;
    }
    detectMouthParam(coreModel);

    const origUpdate = coreModel.update.bind(coreModel);
    const self = coreModel;
    self.update = function () {
      try {
        // mouthOpenValue → ParamMouthOpenY（幅度 × 模型实际最大值）
        self.setParameterValueById(mouthOpenParamId, lipOpenValue * mouthOpenMax);
        // mouthFormValue → ParamMouthForm（如果模型有这个参数且值非中性）
        if (mouthFormParamId !== mouthOpenParamId) {
          self.setParameterValueById(mouthFormParamId, lipFormValue * mouthFormMax);
        }
      } catch (_) { /* 参数不存在时静默 */ }
      return origUpdate();
    };
    console.log('[LipSync] coreModel.update() 已 patch');
  }

  /** 注册 IPC 音频特征监听（volume/centroid/zcr → 口型） */
  function setup(api) {
    if (!api || !api.onMouthOpen) {
      console.warn('[LipSync] window.live2dAPI 或 onMouthOpen 不可用');
      return;
    }
    api.onMouthOpen((data) => {
      if (data && typeof data.volume === 'number') {
        lipAudioVolume = Math.max(0, Math.min(1, data.volume));
        if (typeof data.centroid === 'number') {
          lipCentroid = data.centroid;
        }
        if (typeof data.zcr === 'number') {
          lipZcr = data.zcr;
        }
      }
    });
    console.log('[LipSync] IPC 监听已注册（音频特征驱动）');
  }

  /**
   * 根据当前情绪更新口型幅度因子。
   * @param {string} feeling - 情绪名称（如 "happy", "sad"）
   */
  function setEmotion(feeling) {
    const amp = LIP_SYNC_CONFIG.emotionAmplify;
    lipEmotionFactor = amp[feeling] ?? amp.default;
  }

  return { setup, patch, update, setEmotion };
}
