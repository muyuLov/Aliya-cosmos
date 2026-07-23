// Electron 主进程 - 昔涟状态面板
// 参考 Cyrene-Agent 视觉风格，使用无边框 + 透明背景实现圆角窗口
const { app, BrowserWindow, ipcMain, shell, screen } = require('electron');
const path = require('path');
const fs = require('fs');
const WebSocket = require('ws');

const isDev = process.argv.includes('--dev');
const SIDEBAR_WIDTH = 320;
const SIDEBAR_HEIGHT = 720;

// Windows 控制台 UTF-8 编码适配（解决中文乱码）
if (process.platform === 'win32') {
  try {
    require('child_process').execSync('chcp 65001 >nul 2>&1', { stdio: 'ignore', shell: true });
  } catch { /* 编码切换失败不影响运行 */ }
}

let sidebarWindow = null;
let alwaysOnTop = true;
let currentZoom = 1.0;

// ====== 日志系统 ======
const LOG_DIR = path.join(__dirname, 'logs');
const LOG_FILE = path.join(LOG_DIR, 'gui.log');
const MAX_LOG_SIZE = 5 * 1024 * 1024; // 5MB 轮转

const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const LOG_LEVEL = isDev ? 'debug' : 'info';

function ensureLogDir() {
  if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });
}

function rotateLog() {
  try {
    if (fs.existsSync(LOG_FILE) && fs.statSync(LOG_FILE).size > MAX_LOG_SIZE) {
      const bak = LOG_FILE + '.1';
      if (fs.existsSync(bak)) fs.unlinkSync(bak);
      fs.renameSync(LOG_FILE, bak);
    }
  } catch { /* 忽略轮转错误 */ }
}

function log(level, ...args) {
  if (LOG_LEVELS[level] < LOG_LEVELS[LOG_LEVEL]) return;
  const ts = new Date().toISOString().replace('T', ' ').split('.')[0];
  const msg = args.map(a => (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' ');
  const line = `[${ts}] [${level.toUpperCase()}] ${msg}`;
  console.log(line);
  try {
    ensureLogDir();
    rotateLog();
    fs.appendFileSync(LOG_FILE, line + '\n', 'utf-8');
  } catch { /* 忽略写文件错误 */ }
}

const logger = {
  debug: (...args) => log('debug', ...args),
  info:  (...args) => log('info', ...args),
  warn:  (...args) => log('warn', ...args),
  error: (...args) => log('error', ...args),
};

// ====== 模型配置读取（自动从 Aliya 主项目配置获取） ======
const ALIYA_ROOT = path.resolve(__dirname, '..');
const PROVIDERS_FILE = path.join(ALIYA_ROOT, 'data/config/LLMProviders.json');
const MAIN_YML = path.join(ALIYA_ROOT, 'data/config/main.yml');

/** 简易 YAML key-value 行解析，只提取指定 section 下的 name */
function getCurrentProviderName() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    // 匹配 providers: 块下的 name: <值>
    const match = yaml.match(/providers:\s*$.*?^\s+name:\s*(\S+)/ms);
    return match ? match[1] : 'deepseek';
  } catch {
    return 'deepseek'; // fallback 默认值
  }
}

/** 获取当前模型配置 */
function getModelConfig() {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
    const providerName = getCurrentProviderName();
    const config = providers[providerName];
    logger.debug('模型配置已读取', { provider: providerName, model: config?.model });
    return {
      provider: providerName,
      model: config?.model || 'unknown',
      url: config?.url || '',
    };
  } catch {
    logger.warn('读取模型配置失败，使用默认值');
    return { provider: '', model: '未选择模型', url: '' };
  }
}

/** 获取角色身份信息（从 main.yml 读取 ai_name 和 user_name） */
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
  logger.info('创建侧边栏窗口');
  // 把窗口放在主屏右上角附近
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
    frame: false,                    // 无边框
    transparent: true,                // 透明背景，配合 CSS 圆角
    resizable: isDev,                // 仅开发模式可调整大小
    skipTaskbar: false,
    alwaysOnTop: true,               // 默认置顶
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

  sidebarWindow.loadFile(path.join(__dirname, 'src', 'index.html'));
  sidebarWindow.once('ready-to-show', () => {
    sidebarWindow.show();
    logger.info('侧边栏窗口已显示', { x, y, width: SIDEBAR_WIDTH, height: SIDEBAR_HEIGHT });
  });

  // 外部链接用系统浏览器打开
  sidebarWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  sidebarWindow.on('closed', () => {
    sidebarWindow = null;
  });
}

// ====== IPC 处理 ======
ipcMain.handle('sidebar:minimize', () => {
  sidebarWindow?.minimize();
});

ipcMain.handle('sidebar:close', () => {
  sidebarWindow?.close();
});

ipcMain.handle('sidebar:toggle-pin', () => {
  alwaysOnTop = !alwaysOnTop;
  sidebarWindow?.setAlwaysOnTop(alwaysOnTop);
  return alwaysOnTop;
});

ipcMain.handle('sidebar:is-pinned', () => alwaysOnTop);

ipcMain.handle('sidebar:open-chat', () => {
  // 简化版：发送事件给渲染层，未来可扩展为独立聊天窗口
  sidebarWindow?.webContents.send('sidebar:event', { type: 'open-chat' });
});

ipcMain.handle('sidebar:switch-model', () => {
  sidebarWindow?.webContents.send('sidebar:event', { type: 'switch-model' });
});

ipcMain.handle('sidebar:open-settings', () => {
  sidebarWindow?.webContents.send('sidebar:event', { type: 'open-settings' });
});

// 自动获取当前模型配置
ipcMain.handle('sidebar:get-model', () => {
  logger.debug('IPC: 查询模型配置');
  return getModelConfig();
});

// 自动获取角色身份信息
ipcMain.handle('sidebar:get-identity', () => {
  logger.debug('IPC: 查询角色身份');
  return getIdentity();
});

// 获取所有可用 provider 列表
ipcMain.handle('sidebar:list-providers', () => {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
    const current = getCurrentProviderName();
    return Object.entries(providers).map(([name, cfg]) => ({
      name,
      model: cfg.model,
      url: cfg.url,
      isCurrent: name === current,
    }));
  } catch {
    return [];
  }
});

// 切换当前 provider（更新 main.yml）
ipcMain.handle('sidebar:switch-provider', (_evt, providerName) => {
  try {
    logger.info('切换 provider', { provider: providerName });
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    // 只在 llm: 块内替换第一个 8 空格缩进的 name:
    const updated = yaml.replace(
      /(llm:[\s\S]*?providers:[\s\S]*?^\s{8}name:\s*)\S+/m,
      `$1${providerName}`
    );
    fs.writeFileSync(MAIN_YML, updated, 'utf-8');
    logger.info('provider 切换成功', { provider: providerName });
    return { success: true, model: getModelConfig() };
  } catch (err) {
    logger.error('provider 切换失败', err.message);
    return { success: false, error: err.message };
  }
});

ipcMain.handle('sidebar:set-zoom', (_evt, delta) => {
  currentZoom = Math.min(1.5, Math.max(0.7, currentZoom + delta));
  sidebarWindow?.webContents.setZoomFactor(currentZoom);
  return currentZoom;
});

// 获取当前累积的 token 用量
ipcMain.handle('sidebar:get-token-usage', () => {
  return { ...tokenUsage };
});

/** 解析 ${VAR:default} 格式的环境变量值 */
function resolveEnvValue(raw) {
  const m = typeof raw === 'string' ? raw.match(/^\$\{(\w+)(?::(.*?))?\}$/) : null;
  if (!m) return raw;                     // 不是 env 变量格式，原样返回
  const [, varName, defaultVal] = m;
  return process.env[varName] || defaultVal || '';
}

// ====== Token 用量追踪 ======
let tokenUsage = { total: 0, input: 0, output: 0 };

function resetTokenUsage() {
  tokenUsage = { total: 0, input: 0, output: 0 };
}

function accumulateToken(usage) {
  if (!usage) return;
  const inTokens = usage.prompt_tokens || usage.input_tokens || 0;
  const outTokens = usage.completion_tokens || usage.output_tokens || 0;
  tokenUsage.total += inTokens + outTokens;
  tokenUsage.input += inTokens;
  tokenUsage.output += outTokens;
  logger.debug('Token 用量累积', { input: inTokens, output: outTokens, total: tokenUsage.total });
  sidebarWindow?.webContents.send('sidebar:token-usage', { ...tokenUsage });
}

// ====== Agent WebSocket 连接（获取心情/状态/Token） ======
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
      // 延迟查询确保 agent ready（WS 广播服务器启动时 agent 可能还未注入）
      const queryAgent = () => {
        try {
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        } catch {}
      };
      queryAgent();                                    // 立即查一次
      setTimeout(queryAgent, 2000);                     // 2s 后再查一次（容错 agent 初始化延迟）
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'feeling_scores' && data.dominant) {
          sidebarWindow?.webContents.send('sidebar:emotion', { feeling: data.dominant });
        }
        // 从 brain_complete 提取 token 数据
        if (data.type === 'brain_complete') {
          if (data.usage) accumulateToken(data.usage);
          // 每次脑回路完成后重新查询心情
          ws.send(JSON.stringify({ type: 'get_feeling_scores' }));
          // 重新查询累计 token
          ws.send(JSON.stringify({ type: 'get_token_usage' }));
        }
        // 直接接收 token_usage 响应
        if (data.type === 'token_usage') {
          if (data.total !== undefined) {
            tokenUsage = { total: data.total, input: data.input || 0, output: data.output || 0 };
          } else if (data.usage) {
            accumulateToken(data.usage);
          }
          sidebarWindow?.webContents.send('sidebar:token-usage', { ...tokenUsage });
        }
      } catch (e) {
        logger.warn('WS 消息解析失败', e.message);
      }
    };

    ws.onclose = (evt) => {
      logger.warn('Agent WebSocket 断开', { code: evt.code, reason: evt.reason });
      // 5 秒后重连
      setTimeout(() => {
        try { connectAgentWebSocket(); } catch { logger.error('WS 重连失败'); }
      }, 5000);
    };

    ws.onerror = (evt) => {
      logger.error('Agent WebSocket 错误', evt.message || '未知错误');
      // onclose 会随后触发，由重连逻辑处理
    };
  } catch (e) {
    logger.warn('Agent WebSocket 不可用', e.message || '未知错误');
  }
}

// ====== App 生命周期 ======
app.whenReady().then(() => {
  logger.info('应用启动', { version: '0.0.1', dev: isDev });
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
  logger.info('应用即将退出');
});
