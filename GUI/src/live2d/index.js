// Live2D 独立窗口渲染脚本（Soullink Emotion SDK 版）
// 由独立的透明 BrowserWindow 加载（live2d.html），不与状态面板耦合
//
// 表情/动作引擎：@soullink-emotion/engine（SoullinkRuntime）
//   - VAD 情绪状态 → FACS 表情合成
//   - 动作风格（motionStyle）+ 空闲微动 + 反应动作
//   - TTS 音量口型（LipSyncController）+ 原生 exp3/motion3 播放
// 渲染层：@soullink-emotion/live2d-pixi（Live2DRenderer）
import * as PIXI from 'pixi.js';
import { SoullinkRuntime, getVADPreset } from '@soullink-emotion/engine';
import { Live2DRenderer, createScriptTagCubismLoader } from '@soullink-emotion/live2d-pixi';
import { AKULU_PROFILE } from './profile.js';
import { resolveEmotion } from './emotion-map.js';
import { LAYOUT, PHYSICS, LIP_SYNC_CONFIG, TOOLBAR } from './constants.js';
import '../styles/live2d.css';

// pixi-live2d-display/cubism4 内部需通过 window.PIXI 访问 PixiJS 核心 API
window.PIXI = PIXI;

const stage = document.getElementById('stage');
const errorEl = document.getElementById('live2d-error');

let renderer = null;
let runtime = null;
let rafId = null;
let lastFrameTime = 0;
let speechEndTimer = null;

// ---- TTS 音频水平适配器（驱动 SDK 口型） ----
// 主进程将后端 AudioPlayer 的实时音频特征经 IPC 推送到本窗口，
// 这里把 volume 转成 SDK 的 AudioLevelAnalyzer 接口。
const audioLevel = { level: 0, peak: 0, available: false };
const audioAnalyzer = {
  getLevel: () => audioLevel.level,
  getPeak: () => audioLevel.peak,
  isAvailable: () => audioLevel.available,
  reset: () => { audioLevel.level = 0; audioLevel.peak = 0; },
};

// ---- 鼠标状态 ----
const pointer = { x: 0, y: 0, active: false, lastMove: 0 };

// ---------- 帧循环（驱动表情/动作引擎） ----------

function frame(now) {
  rafId = requestAnimationFrame(frame);
  if (!runtime || !renderer) return;

  if (!lastFrameTime) lastFrameTime = now;
  const timeSeconds = now / 1000;
  const deltaSeconds = Math.min(Math.max((now - lastFrameTime) / 1000, 0), 0.05);
  lastFrameTime = now;

  // 鼠标静止超过 idleTimeout → 注视回归 SDK 默认视线
  if (pointer.active && now - pointer.lastMove > PHYSICS.idleTimeout) {
    pointer.active = false;
    applyMouseGaze();
  }

  const snapshot = runtime.update(timeSeconds, deltaSeconds);
  renderer.applyNativeAnimation(snapshot.nativeAnimation);
  renderer.setParameters(snapshot.live2dParams);
}

// ---------- 情绪驱动（Agent 情绪 → SDK 表情/动作） ----------

let lastEmotionKey = '';
let lastEmotionAt = 0;

function onEmotion(payload) {
  if (!runtime) return;

  const feeling = payload?.feeling || payload?.dominant;
  const target = resolveEmotion(feeling);
  const nowMs = performance.now();
  const now = nowMs / 1000;

  // 相同情绪在窗口内不重复触发，避免 feeling_scores 周期推送导致表情抖动
  const key = `${target.emotion}:${target.variant}`;
  if (key === lastEmotionKey && nowMs - lastEmotionAt < 2000) return;
  lastEmotionKey = key;
  lastEmotionAt = nowMs;

  const vad = getVADPreset(target.emotion, target.variant);
  // 非中性情绪加强表情幅度，让情绪响应更灵敏明显
  const intensity = target.emotion === 'neutral'
    ? target.intensity
    : Math.min(1, target.intensity * 1.2);

  runtime.triggerIntent({
    emotion: target.emotion,
    variant: target.variant,
    naturalEmotion: target.emotion,
    naturalVAD: vad,
    intensity,
    contextTags: ['agent_emotion'],
  }, now, { vadTarget: vad });
}

// ---------- 口型同步（TTS 音量 → SDK 口型） ----------

function onMouthOpen(data) {
  if (!runtime) return;

  const volume = Math.max(0, Math.min(1, Number(data?.volume) || 0));
  audioLevel.level = volume;
  audioLevel.peak = volume;
  audioLevel.available = true;

  if (volume > LIP_SYNC_CONFIG.volumeFloor) {
    if (speechEndTimer) { clearTimeout(speechEndTimer); speechEndTimer = null; }
    runtime.setVoicePlaybackActive(true);
  } else if (!speechEndTimer) {
    // 静音防抖：容忍语音中的短暂停顿
    speechEndTimer = setTimeout(() => {
      speechEndTimer = null;
      audioLevel.level = 0;
      audioLevel.peak = 0;
      runtime.setVoicePlaybackActive(false);
    }, 600);
  }
}

// ---------- 鼠标注视（add 叠加在 SDK gaze 之上） ----------

function applyMouseGaze() {
  if (!runtime) return;

  const w = Math.max(1, window.innerWidth);
  const h = Math.max(1, window.innerHeight);

  if (!pointer.active) {
    runtime.setCustomChannel('mouseGazeX', 0);
    runtime.setCustomChannel('mouseGazeY', 0);
    return;
  }

  const gx = Math.max(-1, Math.min(1, ((pointer.x - w / 2) / (w / 2)) * 0.6));
  const gy = Math.max(-1, Math.min(1, ((pointer.y - h / 2) / (h / 2)) * 0.6));
  runtime.setCustomChannel('mouseGazeX', gx);
  runtime.setCustomChannel('mouseGazeY', gy);
}

// ---------- 事件处理 ----------

/**
 * 右键点击模型区域 → 打开工具栏并固定显示（主入口，桌面宠物标准交互）。
 * 固定模式下保持显示，直到点击工具栏外部或光标移出窗口。
 */
function onContextMenu(e) {
  e.preventDefault();
  toolbarPinned = true;
  hidePicker();
  showToolbar();
}

/** 点击工具栏/面板外部 → 关闭固定模式（并收起工具栏） */
const toolbarOutsideClickHandler = (e) => {
  if (toolbar.contains(e.target) || picker.contains(e.target)) return;
  if (toolbarPinned) {
    toolbarPinned = false;
    hideToolbarImmediate();
  }
};

/** 光标移出窗口：复位注视并退出固定模式 */
function onPointerOutside() {
  pointer.active = false;
  applyMouseGaze();
  toolbarPinned = false; // 光标移出窗口 → 退出固定模式
  hideToolbarImmediate();
}

/** 本地 mousemove：驱动注视平滑跟随，并处理面板自动关闭 */
function onPointerMove(e) {
  const now = performance.now();
  pointer.x = e.clientX;
  pointer.y = e.clientY;
  pointer.active = true;
  pointer.lastMove = now;
  applyMouseGaze();

  // 面板打开时：鼠标不在面板/工具栏内 → 延迟关闭
  if (!picker.classList.contains('picker--hidden')) {
    if (picker.matches(':hover') || toolbar.matches(':hover')) {
      if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }
    } else if (!pickerTimer) {
      pickerTimer = setTimeout(hidePicker, 1000);
    }
  }
}

// ========== 顶部操作栏 ==========

const toolbar = document.getElementById('toolbar');
let toolbarTimer = null;
let isPinned = true;
/** 右键固定模式：true 时工具栏保持显示，直到点击外部/移出窗口 */
let toolbarPinned = false;

/** toolbar mouseleave 处理器引用，用于 cleanup */
const onToolbarMouseLeave = () => {
  if (!toolbarPinned) scheduleHideToolbar();
};

/** 面板外部点击关闭的 handler，直接声明为 const 以避免运行时赋值问题 */
const pickerCloseHandler = (e) => {
  if (!picker.contains(e.target) && !toolbar.contains(e.target)) {
    hidePicker();
  }
};

function showToolbar() {
  if (toolbarTimer) { clearTimeout(toolbarTimer); toolbarTimer = null; }
  toolbar.classList.remove('toolbar--hidden');
}

/** 启动工具栏隐藏倒计时（已计时中不重置，避免 mousemove 高频重置导致永不隐藏） */
function scheduleHideToolbar(delay = TOOLBAR.hideDelay) {
  if (toolbarTimer) return;
  toolbarTimer = setTimeout(() => {
    toolbarTimer = null;
    if (!toolbar.matches(':hover')) {
      toolbar.classList.add('toolbar--hidden');
    }
  }, delay);
}

/** 立即隐藏工具栏（无倒计时） */
function hideToolbarImmediate() {
  if (toolbarTimer) { clearTimeout(toolbarTimer); toolbarTimer = null; }
  toolbar.classList.add('toolbar--hidden');
}

// ========== 模型切换面板 ==========

const picker = document.getElementById('model-picker');
const pickerList = document.getElementById('picker-list');

let pickerTimer = null;

function showPicker() {
  if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }
  picker.classList.remove('picker--hidden');
  loadProviderList();
}

function hidePicker() {
  if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }
  picker.classList.add('picker--hidden');
}

async function loadProviderList() {
  const api = window.live2dAPI;
  if (!api) return;
  const providers = await api.listProviders();
  if (!providers || providers.length === 0) {
    pickerList.innerHTML = '<div style="padding:10px;text-align:center;color:#6b6388;font-size:12px;">暂无可用模型</div>';
    return;
  }

  pickerList.innerHTML = providers
    .sort((a, b) => (a.isCurrent ? -1 : b.isCurrent ? 1 : 0))
    .map((p) => `
      <button type="button" class="picker__item${p.isCurrent ? ' is-current' : ''}" data-provider="${p.name}">
        <span class="picker__item-name">${p.name}</span>
        <span class="picker__item-model">${p.model || '未知'}</span>
        <span class="picker__item-check">✓</span>
      </button>
    `).join('');

  // 点击某一项 → 切换
  pickerList.querySelectorAll('.picker__item').forEach((el) => {
    el.addEventListener('click', async () => {
      const name = el.dataset.provider;
      const result = await api.switchProvider(name);
      if (result.success) {
        // 刷新列表高亮当前项
        loadProviderList();
      }
      hidePicker();
      scheduleHideToolbar(800);
    });
  });
}

// ---------- 操作栏设置 ----------

function setupToolbar() {
  const api = window.live2dAPI;
  if (!api) return;

  toolbar.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const action = btn.dataset.action;
      switch (action) {
        case 'chat':     api.openChat(); break;
        case 'model':
          if (picker.classList.contains('picker--hidden')) {
            showPicker();
          } else {
            hidePicker();
          }
          break;
        case 'settings': api.openSettings(); break;
        case 'sidebar':
          await api.toggleSidebar();
          btn.classList.toggle('is-active');
          break;
        case 'snap':
          api.snapToSidebar().then((docked) => {
            const snapBtn = toolbar.querySelector('[data-action="snap"]');
            if (snapBtn) snapBtn.classList.toggle('is-docked', docked);
          });
          break;
        case 'minimize': api.minimize(); break;
        case 'close':    api.close(); break;
        case 'pin':
          isPinned = await api.togglePin();
          btn.classList.toggle('is-pinned', isPinned);
          break;
      }
      // 操作完成：退出固定模式并自动收起（重新打开需再次右键）
      if (toolbarPinned) {
        toolbarPinned = false;
      }
      scheduleHideToolbar(800);
    });
  });

  // 点击面板外部 → 关闭（handler 已声明为模块级 const）
  stage.addEventListener('pointerdown', pickerCloseHandler);

  // 查询当前置顶状态
  api.isPinned().then((pinned) => {
    isPinned = pinned;
    const pinBtn = toolbar.querySelector('[data-action="pin"]');
    if (pinBtn) pinBtn.classList.toggle('is-pinned', pinned);
  });

  // 查询当前贴靠状态
  api.isDocked().then((docked) => {
    const snapBtn = toolbar.querySelector('[data-action="snap"]');
    if (snapBtn) snapBtn.classList.toggle('is-docked', docked);
  });

  // 拖动解除停靠时实时更新按钮高亮
  if (api.onDockedState) {
    api.onDockedState((docked) => {
      const snapBtn = toolbar.querySelector('[data-action="snap"]');
      if (snapBtn) snapBtn.classList.toggle('is-docked', docked);
    });
  }

  // 监听侧边栏状态变更（侧边栏自身关闭时更新按钮）
  if (api.onSidebarState) {
    api.onSidebarState((visible) => {
      const btn = toolbar.querySelector('[data-action="sidebar"]');
      if (btn) btn.classList.toggle('is-active', visible);
    });
  }
}

// ========== 窗口拖拽 + 互动反馈 ==========

const INTERACTION = {
  clickMoveThreshold: 8,   // 按下后位移超过该值视为拖拽
  clickMaxMs: 500,         // 按下到抬起超过该时长视为拖拽
  clickCooldownMs: 1800,   // 单击可爱反应冷却
  doubleClickMs: 350,      // 双击判定间隔
};

// 单击可爱反应候选（随机触发）
const CLICK_REACTIONS = [
  { emotion: 'happy', variant: 'bright_smile', intensity: 0.6 },   // 眯眯眼笑
  { emotion: 'surprised', variant: 'soft_surprise', intensity: 0.55 }, // 瞳孔缩小惊讶
  { emotion: 'shy', variant: 'bashful', intensity: 0.55 },         // 害羞脸红
];

let isDragging = false;
let dragLastX = 0;
let dragLastY = 0;
let pressX = 0;
let pressY = 0;
let pressTime = 0;
let clickMoved = false;
let lastClickTime = 0;
let lastReactionAt = 0;

function onMouseDown(e) {
  // 仅左键触发拖动/点击；右键用于打开工具栏（onContextMenu）
  if (e.button !== 0) return;
  // 在工具栏或面板上按下时不触发拖动
  if (toolbar.contains(e.target) || picker.contains(e.target)) return;
  pressX = e.clientX;
  pressY = e.clientY;
  pressTime = performance.now();
  clickMoved = false;
  isDragging = true;
  dragLastX = e.screenX;
  dragLastY = e.screenY;
}

function onMouseMove(e) {
  if (!isDragging) return;
  // 位移超过阈值 → 判定为拖拽（点击反应失效）
  if (!clickMoved && Math.hypot(e.clientX - pressX, e.clientY - pressY) > INTERACTION.clickMoveThreshold) {
    clickMoved = true;
  }
  const dx = e.screenX - dragLastX;
  const dy = e.screenY - dragLastY;
  dragLastX = e.screenX;
  dragLastY = e.screenY;
  if (dx !== 0 || dy !== 0) {
    window.live2dAPI?.windowDragMove(dx, dy);
  }
}

function onMouseUp(e) {
  // 仅左键参与拖动/点击判定（右键用于打开工具栏）
  if (e.button !== 0) return;
  isDragging = false;
  const now = performance.now();
  // 位移小且时间短 → 视为点击，触发互动反应
  if (!clickMoved && now - pressTime < INTERACTION.clickMaxMs) {
    handleClick(now);
  }
}

function handleClick(now) {
  // 双击 → 挥挥手
  if (now - lastClickTime < INTERACTION.doubleClickMs) {
    lastClickTime = 0;
    triggerWave();
    return;
  }
  lastClickTime = now;
  // 单击冷却，避免连点刷表情
  if (now - lastReactionAt < INTERACTION.clickCooldownMs) return;
  lastReactionAt = now;
  triggerCuteReaction();
}

function triggerIntentEmotion(pick, tags, intensity) {
  if (!runtime) return;
  const vad = getVADPreset(pick.emotion, pick.variant);
  runtime.triggerIntent({
    emotion: pick.emotion,
    variant: pick.variant,
    naturalEmotion: pick.emotion,
    naturalVAD: vad,
    intensity,
    contextTags: tags,
  }, performance.now() / 1000, { vadTarget: vad });
}

// 单击：随机一个可爱的表情反应
function triggerCuteReaction() {
  const pick = CLICK_REACTIONS[Math.floor(Math.random() * CLICK_REACTIONS.length)];
  triggerIntentEmotion(pick, ['interaction'], pick.intensity);
}

// 双击：挥挥手（happy 映射到 motionMap 招手）
function triggerWave() {
  triggerIntentEmotion(
    { emotion: 'happy', variant: 'bright_smile' },
    ['interaction', 'wave'],
    0.9
  );
}

// ---------- 初始化 ----------

async function init() {
  try {
    runtime = new SoullinkRuntime({
      profile: AKULU_PROFILE,
      audioLevelAnalyzer: audioAnalyzer,
      // ── 表情更明显、过渡更自然 ──
      personality: {
        expressiveness: 0.95,   // 0.88 → 0.95：FACS 表情幅度更大
        softness: 0.62,          // 0.7 → 0.62：动作更干脆利落
        shyness: 0.55,
        gazeStability: 0.58,     // 0.72 → 0.58：视线更活泼
      },
      // ── 情绪响应更灵敏、表情保持更久 ──
      emotionPersonality: {
        reactivity: 1.3,         // 1 → 1.3：情绪 nudge 幅度更强
        decayRate: 0.012,        // 0.018 → 0.012：情绪回落更慢，表情不一闪而过
        emotionHoldSeconds: 26,  // 18 → 26：表情保持时间更长
        ambientDriftStrength: 0.052, // 0.034 → 0.052：待机情绪微漂移更丰富
      },
      // ── 待机动作更生动（介于 natural 与 lively 之间偏活泼） ──
      motionStyle: {
        spontaneity: 1.38,       // 自发小动作频率更高
        gestureFrequency: 1.32,  // 表情过渡/手势更频繁
        gazeStability: 0.58,     // 视线游移更多
        blinkRate: 1.16,         // 眨眼稍快
        breathRate: 1.08,        // 呼吸稍快
        breathVariance: 0.62,    // 呼吸起伏更不规律
        microMotionGain: 1.28,   // 头/面微小动作幅度更大
        idleActionGain: 1.34,    // 待机动作幅度更大
        avoidRepeatWindow: 4,
        speechAccentGain: 1.12,  // 说话时语气重音更明显
      },
    });

    renderer = new Live2DRenderer(stage, {
      // live2d.html 已直接加载 ./lib/live2dcubismcore.min.js，
      // createScriptTagCubismLoader 检测到 window.Live2DCubismCore 会直接放行
      cubismLoader: createScriptTagCubismLoader('./lib/live2dcubismcore.min.js'),
    });

    renderer.setViewScale(LAYOUT.scaleMultiplier);
    renderer.setViewOffset({ x: LAYOUT.offsetX, y: LAYOUT.offsetY });

    const paramMeta = await renderer.load(AKULU_PROFILE.modelPath);
    // 把模型全部参数元数据（min/max/default）传给 runtime，
    // 否则 profile 的 privateEmotionMap（脸红/爱心眼/星星眼等 VTS 按键表情）不会激活
    if (paramMeta) {
      runtime.setPrivateVADParameters(paramMeta);
    }

    // 鼠标事件（窗口始终可交互，本地事件全部可达）
    window.addEventListener('mousemove', onPointerMove);
    // 拖动（mousedown 按住后移动）在 stage 级监听
    stage.addEventListener('mousemove', onMouseMove);
    stage.addEventListener('mouseleave', onPointerOutside);
    stage.addEventListener('mousedown', onMouseDown);
    stage.addEventListener('mouseup', onMouseUp);
    // 右键点击模型 → 打开工具栏（主入口，桌面宠物标准交互）
    stage.addEventListener('contextmenu', onContextMenu);
    // 点击工具栏/面板外部 → 关闭固定模式
    window.addEventListener('pointerdown', toolbarOutsideClickHandler);

    // ---- 操作栏 ----
    setupToolbar();
    toolbar.addEventListener('mouseenter', showToolbar);
    toolbar.addEventListener('mouseleave', onToolbarMouseLeave);

    // ---- 外部数据通道（主进程推送） ----
    const api = window.live2dAPI;
    if (api?.onMouthOpen) api.onMouthOpen(onMouthOpen);
    if (api?.onEmotion) api.onEmotion(onEmotion);

    lastFrameTime = 0;
    rafId = requestAnimationFrame(frame);
  } catch (err) {
    console.error('[Live2D] 模型加载失败', err);
    errorEl.textContent = 'Live2D 加载失败';
    errorEl.hidden = false;
    cleanup();
  }
}

// ---------- 统一清理 ----------

function cleanup() {
  if (speechEndTimer) { clearTimeout(speechEndTimer); speechEndTimer = null; }
  if (toolbarTimer) { clearTimeout(toolbarTimer); toolbarTimer = null; }
  if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }
  if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }

  try { window.removeEventListener('mousemove', onPointerMove); } catch {}
  try { stage.removeEventListener('mousemove', onMouseMove); } catch {}
  try { stage.removeEventListener('mouseleave', onPointerOutside); } catch {}
  try { stage.removeEventListener('mousedown', onMouseDown); } catch {}
  try { stage.removeEventListener('mouseup', onMouseUp); } catch {}
  try { stage.removeEventListener('contextmenu', onContextMenu); } catch {}
  try { window.removeEventListener('pointerdown', toolbarOutsideClickHandler); } catch {}
  try { stage.removeEventListener('pointerdown', pickerCloseHandler); } catch {}
  try { toolbar.removeEventListener('mouseenter', showToolbar); } catch {}
  try { toolbar.removeEventListener('mouseleave', onToolbarMouseLeave); } catch {}

  if (renderer) { renderer.destroy(); renderer = null; }
  runtime = null;
}

window.addEventListener('pagehide', cleanup);
window.addEventListener('beforeunload', cleanup);

init();
