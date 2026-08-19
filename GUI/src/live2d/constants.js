// ========== Live2D 渲染可调参数（集中配置） ==========

// 窗口尺寸 360×720。以下参数调整模型在窗口中的大小和位置。
// 改完后重新构建即可生效（vite build）。
export const LAYOUT = {
  /** 缩放倍率：1.0 = 适配窗口（留 4% 边距），>1 放大，<1 缩小 */
  scaleMultiplier: 1.25,

  /** 垂直偏移（CSS 像素）：0 = 居中，正数 = 向下移（建议范围 -80 ~ 80） */
  offsetY: 0,

  /** 水平偏移（CSS 像素）：0 = 居中，正数 = 向右移 */
  offsetX: 0,

  /** 边距系数（内部）：缩放时保留的窗口边距比例 */
  margin: 0.04,
};

// ========== 鼠标跟随系统 ==========
// 三层信号链：
//   原始鼠标 → 速度预测层 → focusTarget → FocusController（物理插值 → Cubism 参数）
export const PHYSICS = {
  mouseDeadZone: 3,
  idleTimeout: 3000,
  idleReturnRate: 0.9,
  idleReturnDeadZone: 0.5,
  wanderAmplitude: 2.0,
  wanderSpeed: 0.6,
  wanderDelay: 4000,

  // ---- 速度预测参数 ----
  predictionLookAhead: 60,
  predictionMaxPx: 50,
  velocitySmoothing: 0.35,
  velocityMinSpeed: 20,
};

// ========== 口型同步配置 ==========
// 输入特征（来自后端 AudioPlayer）：
//   volume  (0~1) — 归一化 RMS 音量，驱动开合幅度
//   centroid(0~1) — 频谱质心比，低→元音(嘴张) 高→辅音(嘴收窄)
//   zcr     (0~1) — 过零率，低→浊音 高→清音/停顿
//
// 输出参数（自动检测模型支持的口型参数）：
//   ParamMouthOpenY — 嘴张开/闭合（主参数）
//   ParamMouthForm — 嘴变形/形状（辅参数，区分元音口型）
//
// 情绪调制：emotionFactor 根据当前情绪缩放口型幅度
export const LIP_SYNC_CONFIG = {
  // --- 音量→口型开合 ---
  openRate: 0.40,           // Attack：开口跟踪速率（@60fps），越高响应越快
  closeRate: 0.15,          // Release：闭口跟踪速率（@60fps），越低越平滑

  // --- 频谱→口型形状 ---
  formMix: 0.6,             // 频谱质心对口型形状的影响权重 (0~1)，0=禁用车载
  formOpenRate: 0.30,       // 口型形状变化速率（@60fps）
  centroidThreshold: 0.55,  // 频谱质心分界点：低于此→元音(嘴圆张)，高于此→辅音(嘴扁)

  // --- 过零率→短暂闭口（清辅音/停顿触发微闭合） ---
  zcrGate: 0.25,            // 过零率门限，超过此值触发微闭合
  zcrCloseAmount: 0.15,     // 过零率触发时口型收缩幅度 (0~1)

  // --- 噪声与微动控制 ---
  volumeFloor: 0.005,       // 音量阈值，低于此视为静音
  microRandom: 0.02,        // 空闲时微随机扰动幅度 (0~1)，0=关闭

  // --- 情绪调制 ---
  emotionAmplify: {          // 各情绪对口型幅度的缩放
    default: 1.0,
    happy: 1.15,
    sad: 0.75,
    angry: 1.25,
    surprised: 1.3,
    neutral: 1.0,
  },
};

// ========== 帧率控制 ==========
/** 空闲帧率控制：闲置时降至 20fps 减少 GPU 负载 */
export const IDLE_FPS = 20;
export const ACTIVE_FPS = 60;

// ========== 工具栏触发 ==========
// 工具栏仅由「右键点击模型区域」打开并固定显示（桌面宠物标准交互），
// 关闭方式：点击工具栏外部 / 光标移出窗口 / 点击按钮后自动收起。
export const TOOLBAR = {
  /** 点击工具栏按钮后自动收起的延迟 */
  hideDelay: 800,
  /** 右键打开后是否固定显示（true=保持显示，直到点击外部/移出窗口） */
  contextPin: true,
};
