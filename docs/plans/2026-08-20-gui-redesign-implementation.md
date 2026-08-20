# Aliya GUI Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将现有 Vue3+Electron 的 Aliya 状态面板重写为原生 TS + 原生 Electron 的单窗口整合 GUI（聊天区 + Live2D 嵌入 + 状态栏），Live2D 沿用 Cyrene-Agent 的 live2d 模块。

**Architecture:** 主进程（`src/main/`）负责窗口/WS/状态聚合/配置/IPC，复用现有 `GUI/main/` 成熟模块并精简为单窗口；渲染进程（`src/renderer/`）纯原生 TS，以事件总线 `bus.ts` 替代 Pinia，各组件类订阅总线增量更新 DOM；Live2D 直接搬运 `example/Cyrene-Agent-master/src/renderer/live2d/` 接入同窗口 `#live2d-canvas`。设计蓝本见 `docs/plans/2026-08-20-gui-redesign-design.md`。

**Tech Stack:** TypeScript（tsc 构建）、Electron 31、soullink-emotion SDK（`@soullink-emotion/live2d-pixi` + `@soullink-emotion/sdk`）、vitest + happy-dom（测试）、ws（主进程 WS 客户端）。

---

## 前置约束（实施者必读）

- **后端契约**：事件名必须对齐 `agent/events.py` 与 `agent/ws.py`：`run_started` / `text_message_content` / `tool_call_start` / `tool_call_args` / `tool_call_result` / `tool_call_end` / `text_message_start` / `text_message_end` / `run_finished` / `token_usage` / `status_changed` / `emotion_changed` / `tts_features` / `confirm_request`；客户端可发 `user_message` / `stop` / `confirm_response` / `ping` / `get_token_usage` / `get_emotion_state` / `close`。
- **配置格式**：`data/config/main.yml` 为含尾注的 YAML，用定点行替换（按 key 行匹配），禁止整体重写。现有 `GUI/main/config.ts` 已实现，直接复用。
- **视觉令牌**：`--rb-*` 系列 CSS 变量沿用现有 `GUI/src/styles/tokens.css`，直接复制。
- **YAGNI**：不做 E2E、不做主题切换、不做多窗口、不做跨平台非 Windows 适配。
- 每个 Task 实现一个动作，TDD（先写失败测试 → 跑红 → 实现 → 跑绿 → commit）。

---

## Task 1: 脚手架 — tsconfig + package.json 改造

**Files:**
- Modify: `GUI/package.json`
- Create: `GUI/tsconfig.json`
- Create: `GUI/index.html`

**Step 1: 写失败测试（验证 tsc 配置可被解析）**

创建 `GUI/tsconfig.json` 内容如下（先留空 `include` 触发后续编译，不写测试文件，仅用 `tsc --noEmit` 作为验证手段）：
实际本任务无单测，用构建命令验证。

**Step 2: 改造 package.json**

将 `GUI/package.json` 改为：
```json
{
  "name": "aliya-cosmos-gui",
  "version": "0.1.0",
  "description": "《彼方的她-Aliya》—— 单窗口桌面 Agent",
  "main": "dist/main/index.js",
  "private": true,
  "scripts": {
    "compile": "tsc -p tsconfig.json",
    "start": "npm run compile && electron .",
    "dev": "npm run compile && electron . --dev",
    "typecheck": "tsc --noEmit -p tsconfig.json",
    "test": "vitest run"
  },
  "devDependencies": {
    "@types/node": "^20.0.0",
    "@types/ws": "^8.5.0",
    "electron": "^31.0.0",
    "happy-dom": "^14.0.0",
    "typescript": "^5.5.0",
    "vitest": "^2.0.0"
  },
  "dependencies": {
    "@soullink-emotion/live2d-pixi": "^0.1.0-beta.1",
    "@soullink-emotion/sdk": "^0.1.0-beta.1",
    "iconv-lite": "^0.7.3",
    "koffi": "^3.1.5",
    "pixi.js": "^7.4.3",
    "ws": "^8.21.1"
  }
}
```
注意：删除 `vue`/`pinia`/`naive-ui`/`vite`/`@vitejs/plugin-vue`/`pixi-live2d-display`/`vue-demi` 依赖，保留 soullink SDK、pixi.js、ws、koffi、iconv-lite。

**Step 3: 创建 tsconfig.json**
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "CommonJS",
    "moduleResolution": "node",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "sourceMap": true,
    "types": ["node"]
  },
  "include": ["src/**/*.ts"],
  "exclude": ["node_modules", "dist"]
}
```

**Step 4: 创建 index.html**
```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Aliya</title>
  <link rel="stylesheet" href="styles/tokens.css" />
  <link rel="stylesheet" href="styles/app.css" />
</head>
<body>
  <div id="status-bar"></div>
  <div id="live2d-canvas"></div>
  <div id="chat"></div>
  <script src="renderer/main.js"></script>
</body>
</html>
```

**Step 5: 验证并 commit**
Run: `cd GUI && npm install && npx tsc --noEmit -p tsconfig.json`
Expected: 无 `src` 文件时无错误（或仅 "no inputs" 警告，可接受）。
```bash
git add GUI/package.json GUI/tsconfig.json GUI/index.html
git commit -m "build: GUI 脚手架改为 tsc + 原生 Electron（弃用 Vue/Vite）"
```

---

## Task 2: shared 协议常量

**Files:**
- Create: `GUI/src/shared/protocol.ts`
- Create: `GUI/src/shared/protocol.test.ts`

**Step 1: 写失败测试**
```ts
import { EVENTS, ClientMessage } from '../shared/protocol';

test('EVENTS 含关键协议事件', () => {
  expect(EVENTS.RUN_STARTED).toBe('run_started');
  expect(EVENTS.TEXT_MESSAGE_CONTENT).toBe('text_message_content');
  expect(EVENTS.TOOL_CALL_START).toBe('tool_call_start');
  expect(EVENTS.TOKEN_USAGE).toBe('token_usage');
  expect(EVENTS.STATUS_CHANGED).toBe('status_changed');
  expect(EVENTS.EMOTION_CHANGED).toBe('emotion_changed');
  expect(EVENTS.TTS_FEATURES).toBe('tts_features');
  expect(EVENTS.CONFIRM_REQUEST).toBe('confirm_request');
});

test('ClientMessage 构造 user_message', () => {
  const msg: ClientMessage = { type: 'user_message', text: '你好' };
  expect(msg.type).toBe('user_message');
});
```

**Step 2: 运行测试确认失败**
Run: `cd GUI && npx vitest run src/shared/protocol.test.ts`
Expected: FAIL（模块不存在）。

**Step 3: 实现 protocol.ts**
```ts
export const EVENTS = {
  RUN_STARTED: 'run_started',
  TEXT_MESSAGE_START: 'text_message_start',
  TEXT_MESSAGE_CONTENT: 'text_message_content',
  TEXT_MESSAGE_END: 'text_message_end',
  TOOL_CALL_START: 'tool_call_start',
  TOOL_CALL_ARGS: 'tool_call_args',
  TOOL_CALL_RESULT: 'tool_call_result',
  TOOL_CALL_END: 'tool_call_end',
  RUN_FINISHED: 'run_finished',
  TOKEN_USAGE: 'token_usage',
  STATUS_CHANGED: 'status_changed',
  EMOTION_CHANGED: 'emotion_changed',
  TTS_FEATURES: 'tts_features',
  CONFIRM_REQUEST: 'confirm_request',
  ERROR: 'error',
  NOTICE: 'notice',
} as const;

export type ServerEvent = typeof EVENTS[keyof typeof EVENTS];

export type ClientMessage =
  | { type: 'user_message'; text: string }
  | { type: 'stop' }
  | { type: 'confirm_response'; approved: boolean }
  | { type: 'ping' }
  | { type: 'get_token_usage' }
  | { type: 'get_emotion_state' }
  | { type: 'close' };

export interface StateSnapshot {
  connection: 'connected' | 'disconnected' | 'connecting';
  model: string;
  tokens: { prompt: number; completion: number; total: number };
  emotion: string;
  topMost: boolean;
}
```

**Step 4: 运行测试确认通过**
Run: `cd GUI && npx vitest run src/shared/protocol.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/shared/protocol.ts GUI/src/shared/protocol.test.ts
git commit -m "feat: 新增 shared 协议常量（对齐 agent/events.py）"
```

---

## Task 3: 事件总线 bus.ts

**Files:**
- Create: `GUI/src/renderer/bus.ts`
- Create: `GUI/src/renderer/bus.test.ts`

**Step 1: 写失败测试**
```ts
import { Bus } from '../renderer/bus';

test('订阅者收到 emit 的事件', () => {
  const bus = new Bus();
  let got: unknown;
  bus.on('foo', (p) => (got = p));
  bus.emit('foo', 42);
  expect(got).toBe(42);
});

test('off 后不再收到', () => {
  const bus = new Bus();
  let count = 0;
  const h = () => count++;
  bus.on('bar', h);
  bus.emit('bar');
  bus.off('bar', h);
  bus.emit('bar');
  expect(count).toBe(1);
});

test('同一 handler 去重订阅', () => {
  const bus = new Bus();
  let count = 0;
  const h = () => count++;
  bus.on('baz', h);
  bus.on('baz', h);
  bus.emit('baz');
  expect(count).toBe(1);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/bus.test.ts`
Expected: FAIL。

**Step 3: 实现 bus.ts**
```ts
type Handler = (payload: unknown) => void;

export class Bus {
  private map = new Map<string, Set<Handler>>();

  on(event: string, handler: Handler): void {
    let set = this.map.get(event);
    if (!set) {
      set = new Set();
      this.map.set(event, set);
    }
    set.add(handler);
  }

  off(event: string, handler: Handler): void {
    this.map.get(event)?.delete(handler);
  }

  emit(event: string, payload?: unknown): void {
    this.map.get(event)?.forEach((h) => h(payload));
  }
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/bus.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/bus.ts GUI/src/renderer/bus.test.ts
git commit -m "feat: 新增渲染层事件总线 bus.ts（替代 Pinia）"
```

---

## Task 4: 主进程 — config.ts 定点配置

**Files:**
- Create: `GUI/src/main/config.ts`（从现有 `GUI/main/config.ts` 改写，去掉 ESM/vue 残留，纯 TS）
- Create: `GUI/src/main/config.test.ts`

**Step 1: 写失败测试**
```ts
import { readConfigValue, writeConfigValue } from '../main/config';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

const tmp = path.join(os.tmpdir(), `aliya-cfg-${Date.now()}.yml`);
const sample = 'cosmos:\n  service:\n    host: 127.0.0.1\n    port: 8000  # 后端端口\n';

beforeEach(() => fs.writeFileSync(tmp, sample));
afterEach(() => fs.rmSync(tmp, { force: true }));

test('定点读取标量', () => {
  expect(readConfigValue(tmp, 'cosmos.service.port')).toBe('8000');
});

test('定点写入保留注释', () => {
  writeConfigValue(tmp, 'cosmos.service.port', '9000');
  const out = fs.readFileSync(tmp, 'utf8');
  expect(out).toContain('port: 9000');
  expect(out).toContain('# 后端端口');
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/main/config.test.ts`
Expected: FAIL。

**Step 3: 实现 config.ts**（纯 TS，无框架依赖）
```ts
import * as fs from 'fs';

function resolveLine(lines: string[], keyPath: string): number {
  const keys = keyPath.split('.');
  const indentOf = (s: string) => s.length - s.trimStart().length;
  let depth = -1;
  let idx = -1;
  const stack: { key: string; indent: number }[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (!line.trim() || line.trim().startsWith('#')) continue;
    const indent = indentOf(line);
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop();
    const m = line.match(/^(\s*)([\w.-]+):/);
    if (!m) continue;
    const curKey = m[2];
    if (stack.length === keys.length - 1 && curKey === keys[stack.length]) {
      stack.push({ key: curKey, indent });
      if (stack.length === keys.length) return i;
    } else {
      stack.push({ key: curKey, indent });
      if (stack.length > keys.length) stack.pop();
    }
  }
  return -1;
}

export function readConfigValue(file: string, keyPath: string): string | null {
  const lines = fs.readFileSync(file, 'utf8').split('\n');
  const i = resolveLine(lines, keyPath);
  if (i < 0) return null;
  const m = lines[i].match(/:\s*(.+?)\s*(#.*)?$/);
  return m ? m[1].replace(/^["']|["']$/g, '') : null;
}

export function writeConfigValue(file: string, keyPath: string, value: string): void {
  const content = fs.readFileSync(file, 'utf8');
  const lines = content.split('\n');
  const i = resolveLine(lines, keyPath);
  if (i < 0) return;
  const indent = lines[i].length - lines[i].trimStart().length;
  const comment = lines[i].match(/\s*#.*$/)?.[0] ?? '';
  lines[i] = `${' '.repeat(indent)}${lines[i].trim().split(':')[0]}: ${value}${comment}`;
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, lines.join('\n'));
  fs.renameSync(tmp, file);
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/main/config.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/main/config.ts GUI/src/main/config.test.ts
git commit -m "feat: 主进程 config.ts 定点 YAML 读写（保留注释）"
```

---

## Task 5: 主进程 — state.ts 状态聚合 + 节流

**Files:**
- Create: `GUI/src/main/state.ts`
- Create: `GUI/src/main/state.test.ts`

**Step 1: 写失败测试**
```ts
import { StateAggregator } from '../main/state';

test('节流合并快照', (done) => {
  const agg = new StateAggregator(50);
  let pushes = 0;
  agg.onSnapshot = () => pushes++;
  agg.update({ connection: 'connected' });
  agg.update({ model: 'gpt-4o' });
  agg.update({ tokens: { prompt: 10, completion: 5, total: 15 } });
  setTimeout(() => {
    expect(pushes).toBe(1);
    expect(agg.snapshot.model).toBe('gpt-4o');
    expect(agg.snapshot.tokens.total).toBe(15);
    done();
  }, 80);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/main/state.test.ts`
Expected: FAIL。

**Step 3: 实现 state.ts**
```ts
import { StateSnapshot } from '../shared/protocol';

export class StateAggregator {
  snapshot: StateSnapshot = {
    connection: 'disconnected',
    model: '',
    tokens: { prompt: 0, completion: 0, total: 0 },
    emotion: '',
    topMost: false,
  };
  onSnapshot: ((s: StateSnapshot) => void) | null = null;
  private timer: NodeJS.Timeout | null = null;

  constructor(private throttleMs = 50) {}

  update(patch: Partial<StateSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    if (!this.timer) {
      this.timer = setTimeout(() => {
        this.timer = null;
        this.onSnapshot?.(this.snapshot);
      }, this.throttleMs);
    }
  }
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/main/state.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/main/state.ts GUI/src/main/state.test.ts
git commit -m "feat: 主进程 state.ts 状态聚合 + 节流快照"
```

---

## Task 6: 主进程 — ws.ts 后端客户端

**Files:**
- Create: `GUI/src/main/ws.ts`
- Create: `GUI/src/main/ws.test.ts`（用 mock WebSocket 验证收发与重连）

**Step 1: 写失败测试**（使用 `ws` 包的轻量 mock，验证事件分发）
```ts
import { AgentSocket } from '../main/ws';
import { EVENTS } from '../shared/protocol';

class FakeWs {
  sent: any[] = [];
  handlers: Record<string, (d: any) => void> = {};
  on(ev: string, h: (d: any) => void) { this.handlers[ev] = h; }
  send(d: string) { this.sent.push(JSON.parse(d)); }
  emit(ev: string, d: any) { this.handlers[ev]?.(d); }
  close() {}
}

test('收到 run_started 触发回调', () => {
  const fake = new FakeWs();
  const sock = new AgentSocket(fake as any, 'ws://x');
  let got: any = null;
  sock.on(EVENTS.RUN_STARTED, (p) => (got = p));
  fake.emit('message', JSON.stringify({ type: EVENTS.RUN_STARTED, run_id: 'r1' }));
  expect(got.run_id).toBe('r1');
});

test('sendUserMessage 发送 user_message', () => {
  const fake = new FakeWs();
  const sock = new AgentSocket(fake as any, 'ws://x');
  sock.sendUserMessage('hi');
  expect(fake.sent[0]).toEqual({ type: 'user_message', text: 'hi' });
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/main/ws.test.ts`
Expected: FAIL。

**Step 3: 实现 ws.ts**
```ts
import WebSocket from 'ws';
import { ClientMessage, EVENTS, ServerEvent } from '../shared/protocol';

type Listener = (payload: any) => void;

export class AgentSocket {
  private ws: WebSocket | null = null;
  private listeners = new Map<string, Listener[]>();
  private reconnectTimer: NodeJS.Timeout | null = null;
  private attempts = 0;

  constructor(private url: string) {}

  connect(): void {
    this.ws = new WebSocket(this.url);
    this.ws.on('message', (data: Buffer) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg && msg.type) this.dispatch(msg.type, msg);
      } catch { /* 忽略坏帧 */ }
    });
    this.ws.on('close', () => this.scheduleReconnect());
    this.ws.on('error', () => this.ws?.close());
    this.attempts = 0;
  }

  private scheduleReconnect(): void {
    const delay = Math.min(5000 * 2 ** this.attempts, 30000);
    this.attempts++;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  on(event: string, fn: Listener): void {
    const arr = this.listeners.get(event) ?? [];
    arr.push(fn);
    this.listeners.set(event, arr);
  }

  private dispatch(type: string, payload: any): void {
    this.listeners.get(type)?.forEach((fn) => fn(payload));
  }

  send(msg: ClientMessage): void {
    this.ws?.send(JSON.stringify(msg));
  }

  sendUserMessage(text: string): void { this.send({ type: 'user_message', text }); }
  sendStop(): void { this.send({ type: 'stop' }); }
  sendConfirm(approved: boolean): void { this.send({ type: 'confirm_response', approved }); }
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/main/ws.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/main/ws.ts GUI/src/main/ws.test.ts
git commit -m "feat: 主进程 ws.ts 后端 /agent/ws 客户端（自动重连 + 事件分发）"
```

---

## Task 7: 主进程 — windows/tray/notifications/ipc 组装

**Files:**
- Create: `GUI/src/main/windows.ts`
- Create: `GUI/src/main/tray.ts`
- Create: `GUI/src/main/notifications.ts`
- Create: `GUI/src/main/ipc.ts`
- Create: `GUI/src/main/index.ts`
- Create: `GUI/src/preload/index.ts`

**Step 1: 写失败测试**（验证 windows 创建无边框透明窗口；用 mock BrowserWindow）
简化：本任务以"类型检查 + 手动启动"为主，因 Electron API 难单测，仅对 `notifications.ts` 做轻量测试。

`notifications.test.ts`：
```ts
import { shouldNotify } from '../main/notifications';

test('窗口可见时不通知', () => {
  expect(shouldNotify(true)).toBe(false);
});
test('窗口隐藏时通知', () => {
  expect(shouldNotify(false)).toBe(true);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/main/notifications.test.ts`
Expected: FAIL。

**Step 3: 实现 notifications.ts**
```ts
export function shouldNotify(windowVisible: boolean): boolean {
  return !windowVisible;
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/main/notifications.test.ts`
Expected: PASS。

**Step 5: 实现其余主进程模块**

`windows.ts`：
```ts
import { BrowserWindow } from 'electron';

export function createMainWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 420,
    height: 720,
    transparent: true,
    frame: false,
    roundedCorners: true,
    webPreferences: {
      preload: `${__dirname}/../preload/index.js`,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(`${__dirname}/../../index.html`);
  return win;
}
```

`tray.ts`：
```ts
import { app, Tray, Menu, BrowserWindow } from 'electron';

export function createTray(win: BrowserWindow): Tray {
  const tray = new Tray(`${__dirname}/../../assets/icon.png`);
  tray.setToolTip('Aliya');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示', click: () => win.show() },
    { label: '退出', click: () => { (app as any).isQuiting = true; app.quit(); } },
  ]));
  tray.on('click', () => (win.isVisible() ? win.hide() : win.show()));
  return tray;
}
```

`ipc.ts`：
```ts
import { ipcMain, BrowserWindow } from 'electron';
import { AgentSocket } from './ws';

export function registerIpc(win: BrowserWindow, sock: AgentSocket): void {
  ipcMain.handle('sendUserMessage', (_e, text: string) => sock.sendUserMessage(text));
  ipcMain.handle('sendStop', () => sock.sendStop());
  ipcMain.handle('sendConfirm', (_e, approved: boolean) => sock.sendConfirm(approved));
  ipcMain.on('updateConfig', (_e, key: string, value: string) => {
    // 委托 config.writeConfigValue
  });
}
```

`index.ts`：
```ts
import { app, BrowserWindow } from 'electron';
import { createMainWindow } from './windows';
import { createTray } from './tray';
import { AgentSocket } from './ws';
import { StateAggregator } from './state';
import { registerIpc } from './ipc';
import { readConfigValue } from './config';

let mainWindow: BrowserWindow;

app.on('ready', () => {
  mainWindow = createMainWindow();
  const host = readConfigValue('data/config/main.yml', 'cosmos.service.host') ?? '127.0.0.1';
  const port = readConfigValue('data/config/main.yml', 'cosmos.service.port') ?? '8000';
  const state = new StateAggregator(50);
  const sock = new AgentSocket(`ws://${host}:${port}/agent/ws`);
  state.onSnapshot = (s) => mainWindow?.webContents.send('app:state-snapshot', s);
  sock.on('status_changed', (p) => state.update({ connection: p.status }));
  sock.on('token_usage', (p) => state.update({ tokens: p.tokens }));
  sock.on('emotion_changed', (p) => state.update({ emotion: p.emotion }));
  sock.connect();
  createTray(mainWindow);
  registerIpc(mainWindow, sock);
  mainWindow.on('close', (e) => {
    if (!(app as any).isQuiting) { e.preventDefault(); mainWindow.hide(); }
  });
});
```

`preload/index.ts`：
```ts
import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('aliya', {
  sendUserMessage: (text: string) => ipcRenderer.invoke('sendUserMessage', text),
  sendStop: () => ipcRenderer.invoke('sendStop'),
  sendConfirm: (approved: boolean) => ipcRenderer.invoke('sendConfirm', approved),
  updateConfig: (key: string, value: string) => ipcRenderer.send('updateConfig', key, value),
  onStateSnapshot: (cb: (s: any) => void) => ipcRenderer.on('app:state-snapshot', (_e, s) => cb(s)),
});
```

**Step 6: 类型检查 + 手动验证 + commit**
Run: `cd GUI && npx tsc --noEmit -p tsconfig.json`
Expected: 无类型错误（Electron 类型需 `npm install` 后可用）。
```bash
git add GUI/src/main GUI/src/preload
git commit -m "feat: 主进程窗口/托盘/通知/IPC 组装（单窗口）"
```

---

## Task 8: 渲染层基座 — main.ts 装配 + styles

**Files:**
- Create: `GUI/src/renderer/main.ts`
- Create: `GUI/src/renderer/styles/tokens.css`（复制现有 `GUI/src/styles/tokens.css` 的 `--rb-*` 变量）
- Create: `GUI/src/renderer/styles/app.css`

**Step 1: 写失败测试**（happy-dom 挂载验证容器存在）
`renderer/main.test.ts`：
```ts
import { setupBusBridge } from '../renderer/main';

test('main 装配不抛错（无 DOM 时优雅退出）', () => {
  expect(() => setupBusBridge()).not.toThrow();
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/main.test.ts`
Expected: FAIL。

**Step 3: 实现 main.ts**
```ts
import { Bus } from './bus';

export function setupBusBridge(): Bus {
  const bus = new Bus();
  const api = (window as any).aliya;
  if (api?.onStateSnapshot) {
    api.onStateSnapshot((s: any) => {
      bus.emit('state', s);
      if (s.connection) bus.emit('connection', s.connection);
      if (s.tokens) bus.emit('tokens', s.tokens);
      if (s.emotion) bus.emit('emotion', s.emotion);
    });
  }
  return bus;
}

// 实际装配在 DOMContentLoaded 中调用各组件 mount，见后续 Task。
```

`styles/tokens.css`（示例，复制真实值）：
```css
:root {
  --rb-bg: #1a1626;
  --rb-panel: #241f33;
  --rb-accent: #c77dff;
  --rb-text: #ede7f6;
  --rb-muted: #9a8fb5;
}
```

`styles/app.css`：
```css
* { box-sizing: border-box; margin: 0; }
body { background: var(--rb-bg); color: var(--rb-text); font-family: system-ui, sans-serif; overflow: hidden; }
#status-bar { position: fixed; top: 0; left: 0; right: 0; height: 48px; }
#live2d-canvas { position: fixed; top: 48px; left: 0; right: 0; bottom: 240px; }
#chat { position: fixed; left: 0; right: 0; bottom: 0; height: 240px; }
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/main.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/main.ts GUI/src/renderer/styles
git commit -m "feat: 渲染层基座 main.ts + 样式令牌"
```

---

## Task 9: 渲染组件 — StatusBar.ts

**Files:**
- Create: `GUI/src/renderer/components/StatusBar.ts`
- Create: `GUI/src/renderer/components/StatusBar.test.ts`

**Step 1: 写失败测试**（happy-dom）
```ts
import { StatusBar } from '../renderer/components/StatusBar';

test('disconnected 时徽章显示离线 class', () => {
  const el = document.createElement('div');
  const bar = new StatusBar();
  bar.mount(el);
  bar.setConnection('disconnected');
  expect(el.querySelector('.status-dot')?.className).toContain('offline');
});

test('token 更新渲染数值', () => {
  const el = document.createElement('div');
  const bar = new StatusBar();
  bar.mount(el);
  bar.setTokens({ prompt: 10, completion: 5, total: 15 });
  expect(el.querySelector('.token-total')?.textContent).toBe('15');
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/components/StatusBar.test.ts`
Expected: FAIL。

**Step 3: 实现 StatusBar.ts**
```ts
export class StatusBar {
  private root!: HTMLElement;
  mount(el: HTMLElement): void {
    this.root = el;
    el.innerHTML = `
      <div class="title">Aliya</div>
      <span class="status-dot"></span>
      <span class="token-total">0</span>
      <button class="btn-settings">设置</button>
      <button class="btn-top">置顶</button>
      <button class="btn-min">最小化</button>
      <button class="btn-close">关闭</button>`;
  }
  setConnection(state: string): void {
    const dot = this.root.querySelector('.status-dot');
    if (dot) dot.className = `status-dot ${state === 'connected' ? 'online' : 'offline'}`;
  }
  setTokens(t: { prompt: number; completion: number; total: number }): void {
    const el = this.root.querySelector('.token-total');
    if (el) el.textContent = String(t.total);
  }
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/components/StatusBar.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/components/StatusBar.ts GUI/src/renderer/components/StatusBar.test.ts
git commit -m "feat: 渲染组件 StatusBar（连接徽章 + Token 统计 + 窗口按钮）"
```

---

## Task 10: 渲染组件 — Settings.ts 浮层

**Files:**
- Create: `GUI/src/renderer/components/Settings.ts`
- Create: `GUI/src/renderer/components/Settings.test.ts`

**Step 1: 写失败测试**
```ts
import { Settings } from '../renderer/components/Settings';

test('打开后显示浮层，关闭后隐藏', () => {
  const el = document.createElement('div');
  const s = new Settings();
  s.mount(el);
  s.open();
  expect(el.querySelector('.settings-panel')?.classList.contains('hidden')).toBe(false);
  s.close();
  expect(el.querySelector('.settings-panel')?.classList.contains('hidden')).toBe(true);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/components/Settings.test.ts`
Expected: FAIL。

**Step 3: 实现 Settings.ts**
```ts
export class Settings {
  private root!: HTMLElement;
  mount(el: HTMLElement): void {
    this.root = el;
    el.innerHTML = `<div class="settings-panel hidden">
      <input class="identity" placeholder="身份" />
      <select class="provider"><option value="openai">OpenAI</option><option value="local">Local</option></select>
      <button class="save">保存</button>
      <button class="close-btn">关闭</button>
    </div>`;
    el.querySelector('.close-btn')?.addEventListener('click', () => this.close());
    el.querySelector('.save')?.addEventListener('click', () => {
      const api = (window as any).aliya;
      api?.updateConfig('cosmos.identity.name', (el.querySelector('.identity') as HTMLInputElement).value);
    });
  }
  open(): void { this.root.querySelector('.settings-panel')?.classList.remove('hidden'); }
  close(): void { this.root.querySelector('.settings-panel')?.classList.add('hidden'); }
}
```

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/components/Settings.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/components/Settings.ts GUI/src/renderer/components/Settings.test.ts
git commit -m "feat: 渲染组件 Settings 浮层（身份/提供商编辑）"
```

---

## Task 11: 聊天模块 — 搬运 Cyrene chat/ 并适配总线

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/chat/*.ts` → `GUI/src/renderer/chat/`（排除 `.test.ts` 先搬核心，测试后续补）
- Create/Modify: `GUI/src/renderer/chat/adapter.ts`（将总线事件映射到 chat 模块订阅）

**Step 1: 写失败测试**（验证 chat 模块挂载与增量文本）
```ts
import { ChatView } from '../renderer/chat/main';
import { Bus } from '../renderer/bus';
import { EVENTS } from '../shared/protocol';

test('text_message_content 增量追加到消息气泡', () => {
  const el = document.createElement('div');
  const bus = new Bus();
  const chat = new ChatView();
  chat.mount(el);
  chat.bindBus(bus);
  bus.emit(EVENTS.TEXT_MESSAGE_START, { message_id: 'm1' });
  bus.emit(EVENTS.TEXT_MESSAGE_CONTENT, { message_id: 'm1', delta: '你' });
  bus.emit(EVENTS.TEXT_MESSAGE_CONTENT, { message_id: 'm1', delta: '好' });
  expect(el.textContent).toContain('你好');
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/chat/adapter.test.ts`
Expected: FAIL（chat 模块未适配总线）。

**Step 3: 搬运 + 适配**
- 复制 Cyrene `chat/main.ts` 等核心文件到 `GUI/src/renderer/chat/`，将其中依赖 Cyrene 自身 IPC/状态的部分改为订阅 `Bus`。
- 新增 `adapter.ts`：`chat.bindBus(bus)` 内部把 `bus.on(EVENTS.TEXT_MESSAGE_CONTENT, ...)` 转调 `chat.appendDelta(...)` 等。

> 注意：Cyrene `chat/main.ts` 为 139KB 大文件，搬运后需人工核对其 import 路径（去掉 Cyrene 专有依赖，仅保留 DOM 与事件逻辑）。若其依赖过多外部模块，则只搬运 `message-segmentation.ts` 与 `types.ts` 作为算法基础，聊天 UI 以精简版 `ChatView` 重写（YAGNI，不照搬全部附件/推理折叠功能，仅保留消息列表 + 输入区 + 工具卡骨架）。

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/chat/adapter.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/chat
git commit -m "feat: 聊天模块（沿用 Cyrene chat/ 适配事件总线）"
```

---

## Task 12: Live2D 模块 — 搬运 Cyrene live2d/

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/live2d/*.ts` → `GUI/src/renderer/live2d/`
- Create/Modify: `GUI/src/renderer/live2d/adapter.ts`

**Step 1: 写失败测试**（验证 mouth-sync 订阅 tts_features）
```ts
import { MouthSync } from '../renderer/live2d/mouth-sync';
import { Bus } from '../renderer/bus';

test('tts_features 触发口型更新回调', () => {
  const bus = new Bus();
  let opened = false;
  const ms = new MouthSync(() => { opened = true; });
  ms.bindBus(bus);
  bus.emit('tts_features', { open: 0.8 });
  expect(opened).toBe(true);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/live2d/adapter.test.ts`
Expected: FAIL。

**Step 3: 搬运 + 适配**
- 复制 Cyrene `live2d/` 全部 13 个 TS 文件到 `GUI/src/renderer/live2d/`。
- 将 `manager.ts` 的 canvas 挂载点改为 `#live2d-canvas`（从 Cyrene 的自定义容器改为我们的 ID）。
- 新增 `adapter.ts`：把 `bus.on('tts_features', ...)` → `mouthSync.update(...)`，`bus.on('emotion', ...)` → `manager.setEmotion(...)`。

**Step 4: 运行确认通过**
Run: `cd GUI && npx vitest run src/renderer/live2d/adapter.test.ts`
Expected: PASS。

**Step 5: commit**
```bash
git add GUI/src/renderer/live2d
git commit -m "feat: Live2D 模块（沿用 Cyrene live2d/ 接入总线）"
```

---

## Task 13: 总装配 — main.ts 连接所有模块 + 手动联调

**Files:**
- Modify: `GUI/src/renderer/main.ts`（补全 DOMContentLoaded 装配）

**Step 1: 写失败测试**（集成：装配后各容器有子节点）
```ts
import { bootstrap } from '../renderer/main';

test('bootstrap 后 status-bar/chat 有内容', () => {
  document.body.innerHTML = '<div id="status-bar"></div><div id="live2d-canvas"></div><div id="chat"></div>';
  bootstrap();
  expect(document.getElementById('status-bar')!.children.length).toBeGreaterThan(0);
  expect(document.getElementById('chat')!.children.length).toBeGreaterThan(0);
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/main.test.ts`
Expected: FAIL。

**Step 3: 实现 bootstrap（修改 main.ts）**
```ts
import { setupBusBridge } from './bus-bridge'; // 合并进 main.ts
import { StatusBar } from './components/StatusBar';
import { Settings } from './components/Settings';
import { ChatView } from './chat/main';
import { initLive2D } from './live2d/adapter';

export function bootstrap(): void {
  const bus = setupBusBridge();
  const statusBar = new StatusBar();
  statusBar.mount(document.getElementById('status-bar')!);
  const settings = new Settings();
  settings.mount(document.body);
  const chat = new ChatView();
  chat.mount(document.getElementById('chat')!);
  chat.bindBus(bus);
  initLive2D(bus, document.getElementById('live2d-canvas')!);
  bus.on('connection', (s) => statusBar.setConnection(s as string));
  bus.on('tokens', (t) => statusBar.setTokens(t as any));
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', bootstrap);
}
```

**Step 4: 运行确认通过 + 全量测试**
Run: `cd GUI && npx vitest run && npx tsc --noEmit -p tsconfig.json`
Expected: 全部 PASS，类型检查无错。

**Step 5: commit**
```bash
git add GUI/src/renderer/main.ts
git commit -m "feat: 渲染层总装配（StatusBar+Settings+Chat+Live2D 总线联动）"
```

---

## Task 14: 全量验证与降级处理

**Files:**
- Modify: `GUI/src/renderer/live2d/adapter.ts`（加 Live2D 初始化 try/catch 降级）
- Modify: `GUI/src/renderer/main.ts`（各组件 mount 包 try/catch）

**Step 1: 写失败测试**（降级：Live2D 失败不影响聊天）
```ts
import { bootstrap } from '../renderer/main';

test('Live2D 初始化失败时聊天仍可用', () => {
  document.body.innerHTML = '<div id="status-bar"></div><div id="live2d-canvas"></div><div id="chat"></div>';
  // 模拟 canvas 获取失败
  const orig = HTMLCanvasElement.prototype.getContext;
  (HTMLCanvasElement.prototype as any).getContext = () => null;
  expect(() => bootstrap()).not.toThrow();
  (HTMLCanvasElement.prototype as any).getContext = orig;
});
```

**Step 2: 运行确认失败**
Run: `cd GUI && npx vitest run src/renderer/main.test.ts`
Expected: FAIL（未捕获则抛错）。

**Step 3: 实现降级**
- `adapter.ts` 的 `initLive2D` 包 `try/catch`，失败则隐藏 `#live2d-canvas` 并 `console.warn`。
- `main.ts` 的 `bootstrap` 中每个 `mount` 包 `try/catch`。

**Step 4: 运行确认通过 + 手动启动**
Run: `cd GUI && npx vitest run && npm start`（手动确认窗口显示、可对话、Live2D 有反应）
Expected: 测试 PASS；手动启动窗口正常。

**Step 5: commit**
```bash
git add GUI/src/renderer/main.ts GUI/src/renderer/live2d/adapter.ts
git commit -m "fix: GUI 局部错误降级（Live2D 失败不影响聊天/状态栏）"
```

---

## 成功标准（验收清单）

1. `npm start` 启动单一主窗口，含聊天区 + Live2D + 状态栏，无独立 Live2D/设置窗口。
2. 输入文本 → 流式显示 Agent 回复（增量）；Token 统计增长。
3. 后端 `tool_call_*` → 聊天区渲染工具卡片。
4. `confirm_request` → 允许/拒绝按钮，发 `confirm_response`。
5. `tts_features` 驱动口型、`emotion_changed` 调制表情（Cyrene live2d）。
6. 断后端 → 头像徽章转灰 + 提示；恢复自动重连。
7. 设置浮层改身份/提供商 → `main.yml` 定点写入保留注释。
8. `package.json` 无 vue/pinia/naive-ui/vite。
9. `npx tsc --noEmit` 通过；`bus`/`config`/`state`/`ws`/`StatusBar`/`Settings`/chat/live2d 单测通过。

> 计划完整并保存到 `docs/plans/2026-08-20-gui-redesign-implementation.md`。两种执行方式：
> **1. Subagent-Driven（本会话）** — 我每个 Task 派发新子代理实现，任务间做代码评审，快速迭代。
> **2. Parallel Session（独立会话）** — 新开会话使用 superpowers:executing-plans 批量执行并设检查点。
> 选哪种？
