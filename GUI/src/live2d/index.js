// Live2D 独立窗口渲染脚本
// 由独立的透明 BrowserWindow 加载（live2d.html），不与状态面板耦合
import * as PIXI from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display/cubism4';
import { LAYOUT, PHYSICS, IDLE_FPS, ACTIVE_FPS, TOOLBAR_HIDE_DELAY } from './constants.js';
import { createLipSync } from './lip-sync.js';
import '../styles/live2d.css';

// pixi-live2d-display/cubism4 内部需通过 window.PIXI 访问 PixiJS 核心 API
window.PIXI = PIXI;

// 自动发现 assets/live2d/ 目录中的第一个 .model3.json 文件
// 支持放置任意 Live2D 模型（如 阿库露_vts.model3.json），无需改代码
const _modelFiles = import.meta.glob('../assets/live2d/*.model3.json');
const _modelPaths = Object.keys(_modelFiles);
// glob 返回相对 src/live2d/ 的路径；构建后资源位于 dist/assets/live2d/，
// 而本页运行在 dist/live2d.html，故归一化为 './assets/...' 的相对文档路径
const MODEL_PATH = (_modelPaths.length > 0 ? _modelPaths[0] : '../assets/live2d/default.model3.json')
  .replace(/^\.\.\//, './');

const canvas = document.getElementById('live2d-canvas');
const errorEl = document.getElementById('live2d-error');
const stage = document.getElementById('stage');

let app = null;
let model = null;
let resizeObserver = null;

// ========== 口型同步引擎 ==========
const lipSync = createLipSync();

// ---- 状态 ----

const pointer = { x: 0, y: 0, active: false, lastMove: 0 };
const vel = { x: 0, y: 0, prevX: 0, prevY: 0, prevTime: 0 };
const focusTarget = { x: 0, y: 0 };

let isTracking = false;
let driftPhase = 0;

// ---------- 事件处理 ----------

function onPointerMove(e) {
  const now = performance.now();

  if (pointer.active) {
    const rawDt = now - vel.prevTime;
    if (rawDt > 0 && rawDt < 200) {
      const rawVx = (e.clientX - vel.prevX) / rawDt * 1000;
      const rawVy = (e.clientY - vel.prevY) / rawDt * 1000;
      const s = PHYSICS.velocitySmoothing;
      vel.x = rawVx * s + vel.x * (1 - s);
      vel.y = rawVy * s + vel.y * (1 - s);
    } else {
      vel.x = 0;
      vel.y = 0;
    }
  } else {
    vel.x = 0;
    vel.y = 0;
  }
  vel.prevX = e.clientX;
  vel.prevY = e.clientY;
  vel.prevTime = now;

  if (pointer.active &&
      Math.abs(e.clientX - pointer.x) < PHYSICS.mouseDeadZone &&
      Math.abs(e.clientY - pointer.y) < PHYSICS.mouseDeadZone) {
    return;
  }

  pointer.x = e.clientX;
  pointer.y = e.clientY;
  pointer.active = true;
  pointer.lastMove = now;

  // 鼠标在窗口内时显示操作栏，停 2s 后自动隐藏
  showToolbar();
  hideToolbar(TOOLBAR_HIDE_DELAY);

  // 面板打开时：鼠标不在面板/工具栏内 → 延迟关闭
  if (!picker.classList.contains('picker--hidden')) {
    if (picker.matches(':hover') || toolbar.matches(':hover')) {
      if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }
    } else if (!pickerTimer) {
      pickerTimer = setTimeout(hidePicker, 1000);
    }
  }
}

function onPointerLeave() {
  pointer.active = false;
  // 鼠标离开窗口时隐藏操作栏
  hideToolbar(0);
}

// ---------- 焦点更新（每帧） ----------

function updateFocus() {
  if (!model || !app) return;

  const now = performance.now();
  const elapsed = now - pointer.lastMove;
  const isIdle = !pointer.active || elapsed > PHYSICS.idleTimeout;

  // 空闲时降低渲染帧率
  if (isIdle && !isLowFps) {
    app.ticker.maxFPS = IDLE_FPS;
    isLowFps = true;
  } else if (!isIdle && isLowFps) {
    app.ticker.maxFPS = ACTIVE_FPS;
    isLowFps = false;
  }

  const dt = Math.min(app.ticker.deltaMS / 1000, 0.05);

  if (isIdle) {
    if (isTracking) {
      focusTarget.x = pointer.x;
      focusTarget.y = pointer.y;
      isTracking = false;
    }

    const decay = Math.exp(-PHYSICS.idleReturnRate * dt);
    focusTarget.x = model.x + (focusTarget.x - model.x) * decay;
    focusTarget.y = model.y + (focusTarget.y - model.y) * decay;

    if (Math.abs(focusTarget.x - model.x) < PHYSICS.idleReturnDeadZone) focusTarget.x = model.x;
    if (Math.abs(focusTarget.y - model.y) < PHYSICS.idleReturnDeadZone) focusTarget.y = model.y;

    const atCenter = focusTarget.x === model.x && focusTarget.y === model.y;
    if (atCenter && elapsed > PHYSICS.idleTimeout + PHYSICS.wanderDelay) {
      driftPhase += dt * PHYSICS.wanderSpeed;
      const wx = Math.sin(driftPhase) * PHYSICS.wanderAmplitude;
      const wy = Math.cos(driftPhase * 0.7) * PHYSICS.wanderAmplitude * 0.8;
      model.focus(focusTarget.x + wx, focusTarget.y + wy);
    } else {
      model.focus(focusTarget.x, focusTarget.y);
    }
  } else {
    if (!isTracking) {
      isTracking = true;
      driftPhase = 0;
    }

    const px = pointer.x + computePrediction(vel.x);
    const py = pointer.y + computePrediction(vel.y);
    focusTarget.x = px;
    focusTarget.y = py;
    model.focus(focusTarget.x, focusTarget.y);
  }

  // ---- 口型同步（TTS 音量 → ParamMouthOpenY） ----
  // 使用帧率无关 dt 确保 idle 20fps / active 60fps 行为一致
  lipSync.update(dt);
}

function computePrediction(v) {
  const speed = Math.abs(v);
  if (speed < PHYSICS.velocityMinSpeed) return 0;
  const raw = v * PHYSICS.predictionLookAhead / 1000;
  return Math.max(-PHYSICS.predictionMaxPx, Math.min(PHYSICS.predictionMaxPx, raw));
}

// ========== 顶部操作栏 ==========

const toolbar = document.getElementById('toolbar');
let toolbarTimer = null;
let isPinned = true;
let isLowFps = false;

/** toolbar mouseleave 处理器引用，用于 cleanup */
const onToolbarMouseLeave = () => hideToolbar();

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

function hideToolbar(delay) {
  if (toolbarTimer) clearTimeout(toolbarTimer);
  toolbarTimer = setTimeout(() => {
    if (!toolbar.matches(':hover')) {
      toolbar.classList.add('toolbar--hidden');
    }
  }, delay ?? TOOLBAR_HIDE_DELAY);
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
      hideToolbar(800);
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
        case 'snap':     api.snapToSidebar(); break;
        case 'minimize': api.minimize(); break;
        case 'close':    api.close(); break;
        case 'pin':
          isPinned = await api.togglePin();
          btn.classList.toggle('is-pinned', isPinned);
          break;
      }
      hideToolbar(800);
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

  // 监听侧边栏状态变更（侧边栏自身关闭时更新按钮）
  if (api.onSidebarState) {
    api.onSidebarState((visible) => {
      const btn = toolbar.querySelector('[data-action="sidebar"]');
      if (btn) btn.classList.toggle('is-active', visible);
    });
  }
}

// ========== 窗口拖拽 ==========

let isDragging = false;
let dragLastX = 0;
let dragLastY = 0;

function onMouseDown(e) {
  // 在工具栏或面板上按下时不触发拖动
  if (toolbar.contains(e.target) || picker.contains(e.target)) return;
  isDragging = true;
  dragLastX = e.screenX;
  dragLastY = e.screenY;
}

function onMouseMove(e) {
  if (!isDragging) return;
  const dx = e.screenX - dragLastX;
  const dy = e.screenY - dragLastY;
  dragLastX = e.screenX;
  dragLastY = e.screenY;
  if (dx !== 0 || dy !== 0) {
    window.live2dAPI?.windowDragMove(dx, dy);
  }
}

function onMouseUp() {
  isDragging = false;
}

// ---------- 布局 ----------

function layoutModel() {
  if (!app || !model) return;

  const w = app.renderer.width / app.renderer.resolution;
  const h = app.renderer.height / app.renderer.resolution;

  const lb = model.getLocalBounds();
  const origW = lb.width;
  const origH = lb.height;
  if (!origW || !origH) return;

  // 基础缩放：适配窗口（保留边距） × 用户倍率
  const fitScale = Math.min(w / origW, h / origH) * (1 - LAYOUT.margin);
  const scale = fitScale * LAYOUT.scaleMultiplier;
  model.scale.set(scale);

  // 定位：居中 + 用户偏移（以模型缩放后的实际像素计算）
  model.pivot.set(lb.x + origW / 2, lb.y + origH / 2);
  model.x = w / 2 + LAYOUT.offsetX;
  model.y = h / 2 + LAYOUT.offsetY;
}

// ---------- 初始化 ----------

async function init() {
  const w = Math.max(1, window.innerWidth);
  const h = Math.max(1, window.innerHeight);

  try {
    app = new PIXI.Application({
      view: canvas,
      width: w,
      height: h,
      backgroundAlpha: 0,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoDensity: true,
      antialias: false,                // 模型已含抗锯齿纹理，关闭 GPU MSAA
      autoStart: true,
      powerPreference: 'high-performance',
    });

    model = await Live2DModel.from(MODEL_PATH);
    app.stage.addChild(model);
    layoutModel();

    model.autoInteract = false;
    model.interactive = false;          // 使用自定义 pointer 事件，不启用 PIXI 交互系统

    // 口型同步：patch coreModel.update() 在 motion/物理之后注入口型参数
    if (model.internalModel?.coreModel) {
      lipSync.patch(model.internalModel.coreModel);
    }

    focusTarget.x = model.x;
    focusTarget.y = model.y;

    vel.prevX = model.x;
    vel.prevY = model.y;
    vel.prevTime = performance.now();

    stage.addEventListener('pointermove', onPointerMove);
    stage.addEventListener('pointerleave', onPointerLeave);
    stage.addEventListener('mousedown', onMouseDown);
    stage.addEventListener('mousemove', onMouseMove);
    stage.addEventListener('mouseup', onMouseUp);
    app.ticker.add(updateFocus);

    // ---- 操作栏 ----
    setupToolbar();
    lipSync.setup(window.live2dAPI);
    toolbar.addEventListener('mouseenter', showToolbar);
    toolbar.addEventListener('mouseleave', onToolbarMouseLeave);

    resizeObserver = new ResizeObserver(() => {
      if (!app) return;
      const nw = Math.max(1, window.innerWidth);
      const nh = Math.max(1, window.innerHeight);
      app.renderer.resize(nw, nh);
      layoutModel();
      focusTarget.x = model.x;
      focusTarget.y = model.y;
    });
    resizeObserver.observe(document.body);
  } catch (err) {
    console.error('[Live2D] 模型加载失败', err);
    errorEl.textContent = 'Live2D 加载失败';
    errorEl.hidden = false;
    cleanup();
  }
}

// ---------- 统一清理 ----------

function cleanup() {
  if (toolbarTimer) { clearTimeout(toolbarTimer); toolbarTimer = null; }
  if (pickerTimer) { clearTimeout(pickerTimer); pickerTimer = null; }

  try { stage.removeEventListener('pointermove', onPointerMove); } catch {}
  try { stage.removeEventListener('pointerleave', onPointerLeave); } catch {}
  try { stage.removeEventListener('mousedown', onMouseDown); } catch {}
  try { stage.removeEventListener('mousemove', onMouseMove); } catch {}
  try { stage.removeEventListener('mouseup', onMouseUp); } catch {}
  try { stage.removeEventListener('pointerdown', pickerCloseHandler); } catch {}
  try { toolbar.removeEventListener('mouseenter', showToolbar); } catch {}
  try { toolbar.removeEventListener('mouseleave', onToolbarMouseLeave); } catch {}

  if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }

  if (model) { model.destroy({ children: true }); model = null; }
  if (app) {
    app.ticker.stop();
    app.destroy(true);
    app = null;
  }
}

window.addEventListener('pagehide', cleanup);
window.addEventListener('beforeunload', cleanup);

init();
