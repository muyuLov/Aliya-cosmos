// ========== 主进程日志系统 ==========
// 彩色控制台输出 + 每日日志文件（YYYY-MM-DD）+ 单日文件大小轮转 + 超期自动清理
const fs = require('fs');
const path = require('path');
const iconv = require('iconv-lite');

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

const LOG_DIR = path.resolve(__dirname, '..', '..', 'data', 'logs');
const LOG_FILE_PREFIX = 'Aliya-cosmos-GUI';
const MAX_LOG_SIZE = 5 * 1024 * 1024;  // 单日文件上限
const MAX_BACKUPS = 3;                 // 单日文件滚动备份数
const KEEP_DAYS = 30;                  // 日志保留天数，超期自动清理

const LOG_LEVELS = { debug: 0, info: 1, warn: 2, error: 3 };
const LOG_LEVEL = process.argv.includes('--dev') ? 'debug' : 'info';
const LEVEL_TAGS = { debug: 'DEBUG', info: 'INFO ', warn: 'WARN ', error: 'ERROR' };

// ========== ANSI 彩色输出 ==========
// 检测顺序：显式禁用（NO_COLOR/--no-color）→ 显式强制（FORCE_COLOR）→ 平台兜底。
// 注意：Electron 主进程在 Windows 下 process.stdout.isTTY 恒为 undefined，
// fstatSync(1) 也会抛 EISDIR，无法用 TTY 判断；Windows 默认启用彩色，
// 与 Python 端 core/logger 的 console color 策略保持一致。
const RESET = '\x1b[0m';
const BOLD = '\x1b[1m';
const DIM = '\x1b[2m';
const FG = {
  white:  '\x1b[97m',  // 亮白
  cyan:   '\x1b[36m',  // 青
  green:  '\x1b[32m',  // 绿
  yellow: '\x1b[33m',  // 黄
  red:    '\x1b[31m',  // 红
};
// 时间戳：暗白，低调不抢眼（与 Python 端 core/logger/formatter.py 一致）
const TS_STYLE = DIM + FG.white;
// 各级别配色：标签色 + 消息本体色，与 Python 端 _LEVEL_STYLES 对齐
const LEVEL_STYLES = {
  debug: { tag: DIM + FG.cyan,    msg: DIM + FG.white },  // 级别暗青，消息暗白
  info:  { tag: BOLD + FG.green,  msg: FG.white },         // 级别加粗绿，消息亮白
  warn:  { tag: BOLD + FG.yellow, msg: FG.yellow },        // 级别加粗黄，消息黄
  error: { tag: BOLD + FG.red,    msg: FG.red },           // 级别加粗红，消息红
};
function detectColor() {
  // 显式禁用：NO_COLOR 存在且非空，或 --no-color 参数
  if (process.env.NO_COLOR || process.argv.includes('--no-color')) return false;
  // 显式强制：FORCE_COLOR，'0' 表示禁用，其余任意值表示启用
  const forced = process.env.FORCE_COLOR;
  if (forced !== undefined) return forced !== '0';
  // 非 Windows：仅当 stdout 为 TTY 时输出彩色，避免重定向到文件混入转义码
  if (process.platform !== 'win32') return Boolean(process.stdout?.isTTY);
  // Windows + Electron：stdout 恒非 TTY，现代终端（VSCode / Windows
  // Terminal / PowerShell 7 / ConEmu）普遍支持 ANSI，默认启用
  return true;
}
const USE_COLOR = detectColor();

let logStream = null;
let logBytes = 0;
let currentLogDate = '';
let currentLogFile = '';
let cleanupDone = false;

/** 本地日期 YYYY-MM-DD（日志按天切分，用本地时间避免时区错位） */
function formatDate(d = new Date()) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function getLogFile(date) {
  return path.join(LOG_DIR, `${LOG_FILE_PREFIX}-${date}.log`);
}

/** 清理超过 KEEP_DAYS 的旧日志（含滚动备份），仅匹配按天命名的新格式 */
function cleanupOldLogs() {
  try {
    const cutoff = Date.now() - KEEP_DAYS * 24 * 3600 * 1000;
    for (const name of fs.readdirSync(LOG_DIR)) {
      const m = name.match(/^Aliya-cosmos-GUI-(\d{4}-\d{2}-\d{2})\.log(\.\d+)?$/);
      if (!m) continue;
      const fileDate = new Date(`${m[1]}T00:00:00`).getTime();
      if (fileDate < cutoff) {
        try { fs.unlinkSync(path.join(LOG_DIR, name)); } catch {}
      }
    }
  } catch { /* 清理失败不影响运行 */ }
}

function ensureLogDir() {
  if (!fs.existsSync(LOG_DIR)) fs.mkdirSync(LOG_DIR, { recursive: true });
}

/** 滚动轮转：当日文件 → .1 → .2 → .3（最旧丢弃） */
function rotateLogs(file) {
  try {
    for (let i = MAX_BACKUPS; i >= 1; i--) {
      const src = i === 1 ? file : `${file}.${i - 1}`;
      const dst = `${file}.${i}`;
      if (fs.existsSync(src)) {
        if (fs.existsSync(dst)) fs.unlinkSync(dst);
        fs.renameSync(src, dst);
      }
    }
  } catch { /* 轮转错误不影响主流程 */ }
}

function getLogStream() {
  const date = formatDate();
  if (logStream && currentLogDate === date) return logStream;

  // 跨天或首次：关闭旧流，切换当日文件
  if (logStream) {
    try { logStream.end(); } catch {}
    logStream = null;
  }
  if (!cleanupDone) {
    cleanupDone = true;
    cleanupOldLogs();
  }
  ensureLogDir();

  currentLogDate = date;
  currentLogFile = getLogFile(date);
  try {
    logBytes = fs.existsSync(currentLogFile) ? fs.statSync(currentLogFile).size : 0;
  } catch { logBytes = 0; }
  if (logBytes > MAX_LOG_SIZE) {
    rotateLogs(currentLogFile);
    logBytes = 0;
  }
  logStream = fs.createWriteStream(currentLogFile, { flags: 'a', encoding: 'utf-8' });
  // WriteStream 退出时自动关闭
  logStream.on('error', () => {});
  return logStream;
}

function closeLogStream() {
  if (logStream) {
    try { logStream.end(); } catch {}
    logStream = null;
  }
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

  // 控制台：按终端编码输出，彩色模式下时间戳暗白 + 级别标签/消息本体按级别着色
  if (USE_COLOR) {
    const style = LEVEL_STYLES[level] || { tag: '', msg: '' };
    process.stdout.write(iconv.encode(
      `${TS_STYLE}[${ts}]${RESET} ${style.tag}[${tag}]${RESET} ${style.msg}${msg}${RESET}\n`,
      TERMINAL_ENCODING
    ));
  } else {
    process.stdout.write(iconv.encode(line, TERMINAL_ENCODING));
  }

  // 文件流：写入当日文件，增量写入，溢位时自动轮转（文件不写 ANSI 颜色）
  try {
    const stream = getLogStream();
    const len = Buffer.byteLength(line, 'utf-8');
    if (logBytes + len > MAX_LOG_SIZE) {
      stream.end();
      rotateLogs(currentLogFile);
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

module.exports = { logger, closeLogStream, TERMINAL_ENCODING };
