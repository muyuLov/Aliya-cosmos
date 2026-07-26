const { app, BrowserWindow, ipcMain, shell, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const WebSocket = require('ws');
const iconv = require('iconv-lite');

const isDev = process.argv.includes('--dev');
const SIDEBAR_WIDTH = 320;
const SIDEBAR_HEIGHT = 720;

// ========== 控制台编码检测 ==========
// Electron 是 GUI 进程，无法通过 chcp 切换控制台代码页（切换仅对 CMD 子进程生效）。
// 改为仅检测当前代码页，输出时按检测结果做编码转换。
let TERMINAL_ENCODING = 'utf-8';
if (process.platform === 'win32') {
  try {
    const { execSync } = require('child_process');
    // 使用 buffer + latin1 读取，避免 GBK 字节被误当 UTF-8 解码导致数字提取失败
    const buf = execSync('chcp', { stdio: 'pipe', shell: true });
    const m = buf.toString('latin1').match(/(\d+)/);
    if (m) {
      const cpId = parseInt(m[1]);
      TERMINAL_ENCODING = cpId === 65001 ? 'utf-8' : `cp${cpId}`;
    }
  } catch {
    // chcp 失败（无控制台），按系统语言回退
    const loc = String(process.env.LANG ?? process.env.LC_ALL ?? '');
    TERMINAL_ENCODING = loc.includes('zh') ? 'cp936' : 'utf-8';
  }
}

let sidebarWindow = null;
let alwaysOnTop = true;
let currentZoom = 1.0;

// ========== 日志系统 ==========
// 使用 WriteStream 批量写入避免每次 sync I/O，含毫秒时间戳 + 多级轮转
const LOG_DIR = path.resolve(__dirname, '..', 'data', 'logs');
const LOG_FILE = path.join(LOG_DIR, 'Aliya-cosmos-GUI.log');
const MAX_LOG_SIZE = 5 * 1024 * 1024;
const MAX_BACKUPS = 3;

const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const LOG_LEVEL = isDev ? 'debug' : 'info';
const LEVEL_TAGS = { debug: 'DEBUG', info: 'INFO ', warn: 'WARN ', error: 'ERROR' };

let logStream = null;
let logBytes = 0;

function ensureLogDir() {
  if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });
}

/** 滚动轮转：gui.log → gui.log.1 → gui.log.2 → gui.log.3（最旧丢弃） */
function rotateLogs() {
  try {
    for (let i = MAX_BACKUPS; i >= 1; i--) {
      const src = i === 1 ? LOG_FILE : `${LOG_FILE}.${i - 1}`;
      const dst = `${LOG_FILE}.${i}`;
      if (fs.existsSync(src)) {
        if (fs.existsSync(dst)) fs.unlinkSync(dst);
        fs.renameSync(src, dst);
      }
    }
  } catch { /* 轮转错误不影响主流程 */ }
}

function getLogStream() {
  if (logStream) return logStream;
  ensureLogDir();
  // 启动时检查文件大小，过大则轮转
  try {
    logBytes = fs.existsSync(LOG_FILE) ? fs.statSync(LOG_FILE).size : 0;
  } catch { logBytes = 0; }
  if (logBytes > MAX_LOG_SIZE) {
    rotateLogs();
    logBytes = 0;
  }
  logStream = fs.createWriteStream(LOG_FILE, { flags: 'a', encoding: 'utf-8' });
  return logStream;
}

function log(level, ...args) {
  if (LOG_LEVELS[level] < LOG_LEVELS[LOG_LEVEL]) return;

  // 毫秒级时间戳
  const ts = new Date().toISOString().replace('T', ' ').slice(0, 23);
  const tag = LEVEL_TAGS[level] || level.toUpperCase();
  const msg = args.map(a => {
    if (typeof a === 'string') return a;
    try { return JSON.stringify(a); } catch { return String(a); }
  }).join(' ');
  const line = `[${ts}] [${tag}] ${msg}\n`;

  // 控制台：按终端编码输出
  process.stdout.write(iconv.encode(line, TERMINAL_ENCODING));

  // 文件流：增量写入，溢位时自动轮转
  try {
    const stream = getLogStream();
    const len = Buffer.byteLength(line, 'utf-8');
    if (logBytes + len > MAX_LOG_SIZE) {
      stream.end();
      rotateLogs();
      logStream = null;
      logBytes = 0;
      getLogStream().write(line);
    } else {
      stream.write(line);
    }
    logBytes += len;
  } catch { /* 写日志失败不影响应用运行 */ }
}

const logger = {
  debug: (...args) => log('debug', ...args),
  info:  (...args) => log('info', ...args),
  warn:  (...args) => log('warn', ...args),
  error: (...args) => log('error', ...args),
};

// ========== 模型配置读取 ==========
const ALIYA_ROOT = path.resolve(__dirname, '..');
const PROVIDERS_FILE = path.join(ALIYA_ROOT, 'data/config/LLMProviders.json');
const MAIN_YML = path.join(ALIYA_ROOT, 'data/config/main.yml');

function getCurrentProviderName() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const match = yaml.match(/providers:\s*$.*?^\s+name:\s*(\S+)/ms);
    return match ? match[1] : 'deepseek';
  } catch {
    return 'deepseek';
  }
}

function getModelConfig() {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
    const providerName = getCurrentProviderName();
    const config = providers[providerName];
    return {
      provider: providerName,
      model: config?.model || 'unknown',
      url: config?.url || '',
    };
  } catch {
    return { provider: '', model: '未选择模型', url: '' };
  }
}

function getIdentity() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const aiName = yaml.match(/^\s{4}ai_name:\s*(\S+)/m)?.[1] || 'Aliya';
    const userName = yaml.match(/^\s{4}user_name:\s*(\S+)/m)?.[1] || '';
    return { aiName, userName };
  } catch {
    return { aiName: 'Aliya', userName: '' };
  }
}

function createSidebarWindow() {
  logger.info('正在创建侧边栏窗口', { width: SIDEBAR_WIDTH, height: SIDEBAR_HEIGHT });
  const display = screen.getPrimaryDisplay();
  const { width: screenWidth } = display.workArea;
  const x = Math.max(0, screenWidth - SIDEBAR_WIDTH - 16);
  const y = 80;

  sidebarWindow = new BrowserWindow({
    width: SIDEBAR_WIDTH,
    height: SIDEBAR_HEIGHT,
    x,
    y,
    minWidth: 280,
    minHeight: 560,
    frame: false,
    transparent: true,
    resizable: isDev,
    skipTaskbar: false,
    alwaysOnTop: true,
    hasShadow: true,
    backgroundColor: '#00000000',
    title: '状态',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: isDev,
    },
  });

  sidebarWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));

  sidebarWindow.once('ready-to-show', () => {
    logger.info('侧边栏窗口已显示', { x, y });
    sidebarWindow.show();
  });

  sidebarWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  sidebarWindow.on('closed', () => {
    logger.info('侧边栏窗口已关闭');
    sidebarWindow = null;
  });
}

// ========== IPC 处理 ==========

ipcMain.handle('sidebar:minimize', () => {
  sidebarWindow?.minimize();
});

ipcMain.handle('sidebar:close', () => {
  sidebarWindow?.close();
});

ipcMain.handle('sidebar:toggle-pin', () => {
  alwaysOnTop = !alwaysOnTop;
  sidebarWindow?.setAlwaysOnTop(alwaysOnTop);
  logger.debug('置顶状态切换', { alwaysOnTop });
  return alwaysOnTop;
});

ipcMain.handle('sidebar:is-pinned', () => alwaysOnTop);

ipcMain.handle('sidebar:open-chat', () => {
  sidebarWindow?.webContents.send('sidebar:event', { type: 'open-chat' });
});

ipcMain.handle('sidebar:switch-model', () => {
  sidebarWindow?.webContents.send('sidebar:event', { type: 'switch-model' });
});

ipcMain.handle('sidebar:open-settings', () => {
  sidebarWindow?.webContents.send('sidebar:event', { type: 'open-settings' });
});

ipcMain.handle('sidebar:get-model', () => getModelConfig());

ipcMain.handle('sidebar:get-identity', () => getIdentity());

ipcMain.handle('sidebar:list-providers', () => {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
    const current = getCurrentProviderName();
    const list = Object.entries(providers).map(([name, cfg]) => ({
      name,
      model: cfg.model,
      url: cfg.url,
      isCurrent: name === current,
    }));
    logger.debug('提供商列表查询', { count: list.length, current });
    return list;
  } catch {
    logger.warn('提供商列表读取失败');
    return [];
  }
});

ipcMain.handle('sidebar:switch-provider', (_evt, providerName) => {
  try {
    logger.info('切换 Provider', { to: providerName });
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const updated = yaml.replace(
      /(llm:[\s\S]*?providers:[\s\S]*?^\s{8}name:\s*)\S+/m,
      `$1${providerName}`
    );
    fs.writeFileSync(MAIN_YML, updated, 'utf-8');
    const modelCfg = getModelConfig();
    logger.info('Provider 切换成功', { provider: providerName, model: modelCfg.model });
    return { success: true, model: modelCfg };
  } catch (err) {
    logger.error('Provider 切换失败', err.message);
    return { success: false, error: err.message };
  }
});

ipcMain.handle('sidebar:set-zoom', (_evt, delta) => {
  currentZoom = Math.min(1.5, Math.max(0.7, currentZoom + delta));
  sidebarWindow?.webContents.setZoomFactor(currentZoom);
  return currentZoom;
});

ipcMain.handle('sidebar:get-token-usage', () => ({ ...tokenUsage }));

// ========== Token 用量追踪 ==========

let tokenUsage = { total: 0, input: 0, output: 0 };

function accumulateToken(usage) {
  if (!usage) return;
  const inTokens = usage.prompt_tokens || usage.input_tokens || 0;
  const outTokens = usage.completion_tokens || usage.output_tokens || 0;
  tokenUsage.total += inTokens + outTokens;
  tokenUsage.input += inTokens;
  tokenUsage.output += outTokens;
  logger.debug('Token 累积', { in: inTokens, out: outTokens, total: tokenUsage.total });
  sidebarWindow?.webContents.send('sidebar:token-usage', { ...tokenUsage });
}

// ========== Agent WebSocket 连接 ==========

let wsReconnectTimer = null;

function connectAgentWebSocket() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const hostRaw = yaml.match(/^\s{8}host:\s*([^#\s]+)/m)?.[1] || '127.0.0.1';
    const portRaw = yaml.match(/^\s{8}port:\s*([^#\s]+)/m)?.[1] || '8765';
    const host = resolveEnvValue(hostRaw);
    const port = resolveEnvValue(portRaw);
    const url = `ws://${host}:${port}/agent/ws`;

    logger.info('正在连接 Agent WebSocket', { url });
    const ws = new WebSocket(url);

    ws.onopen = () => {
      logger.info('Agent WebSocket 已连接');
      // 首次建立连接后立即查询，2s 后再补一次（容错 agent 初始化延迟）
      const queryAgent = (label) => {
        try {
          logger.debug('WS 查询 Agent 状态', { label });
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        } catch {}
      };
      queryAgent('initial');
      setTimeout(() => queryAgent('deferred(2s)'), 2000);
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        logger.debug('WS 收到消息', { type: data.type });
        if (data.type === 'feeling_scores' && data.dominant) {
          logger.info('心情更新', { feeling: data.dominant });
          sidebarWindow?.webContents.send('sidebar:emotion', { feeling: data.dominant });
        }
        if (data.type === 'brain_complete') {
          if (data.usage) accumulateToken(data.usage);
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        }
        if (data.type === 'token_usage') {
          if (data.total !== undefined) {
            tokenUsage = { total: data.total, input: data.input || 0, output: data.output || 0 };
          } else if (data.usage) {
            accumulateToken(data.usage);
          }
          logger.debug('Token 用量同步', { total: tokenUsage.total });
          sidebarWindow?.webContents.send('sidebar:token-usage', { ...tokenUsage });
        }
      } catch (e) {
        logger.warn('WS 消息解析失败', { error: e.message, raw: String(event.data).slice(0, 120) });
      }
    };

    ws.onclose = (evt) => {
      logger.warn('Agent WebSocket 断开', { code: evt.code, reason: evt.reason || '(无)' });
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = setTimeout(() => {
        logger.info('WS 尝试重连…');
        try { connectAgentWebSocket(); } catch { logger.error('WS 重连失败'); }
      }, 5000);
    };

    ws.onerror = () => {
      logger.warn('Agent WebSocket 连接异常');
    };
  } catch (e) {
    logger.warn('Agent WebSocket 不可用', { error: e.message || '未知错误' });
    // 不可用时仍然尝试重连
    clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(() => {
      try { connectAgentWebSocket(); } catch {}
    }, 5000);
  }
}

function resolveEnvValue(raw) {
  const m = typeof raw === 'string' ? raw.match(/^\$\{(\w+)(?::(.*?))?\}$/) : null;
  if (!m) return raw;
  const [, varName, defaultVal] = m;
  return process.env[varName] || defaultVal || '';
}

// ========== App 生命周期 ==========

app.whenReady().then(() => {
  logger.info('═══════ Aliya-cosmos GUI 启动 ═══════', { version: '0.2.0', dev: isDev });
  logger.info('运行环境', {
    platform: process.platform,
    arch: process.arch,
    node: process.versions.node,
    electron: process.versions.electron,
    encoding: TERMINAL_ENCODING,
  });

  createSidebarWindow();
  connectAgentWebSocket();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      logger.info('应用被重新激活，重建窗口');
      createSidebarWindow();
    }
  });
});

app.on('window-all-closed', () => {
  logger.info('所有窗口已关闭');
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  logger.info('═══════ Aliya-cosmos GUI 退出 ═══════');
});
