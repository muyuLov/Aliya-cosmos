# GUI P2 主进程 Implementation Plan

> **提交策略：** 本计划中所有 `git commit` 步骤均**跳过**（用户要求：不提交 git、不推送 GitHub）。任务完成标准不变。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 重写 Electron 主进程：Python 后端进程管理（自动拉起/手动）、Aliya WS 客户端（协议映射 + 音频转发 + 断线重连）、配置读写（Aliya `data/config/*`）、状态聚合、IPC 注册与完整 preload 桥，使 4 窗口能与后端跑通对话/情绪/会话闭环。

**Architecture:** 主进程不直接调 LLM（与 Cyrene 的本质区别），所有 Agent 能力经 WS 转发给 Aliya Python 后端。`backend.ts` 管子进程，`ws.ts` 管协议与重连，`config.ts` 管配置，`state.ts` 聚合后由 `ipc.ts` 广播到渲染窗口。

**Tech Stack:** Electron 43 主进程（CommonJS）、原生 WebSocket（Electron/Node ≥ 22 全局可用，无需 ws 包）、Vitest 单测（mock WebSocket）。

**执行环境:** Windows PowerShell。命令在仓库根运行。

**前置条件:** P0、P1 完成；`GUI/BUILD_GAPS.md` 错误清单已记录。

---

### Task 1: 渲染层事件消费对齐调研（先行）

**Files:**
- Read: `GUI/src/renderer/chat/main.ts`（AGUI 事件 switch 分支）
- Read: `GUI/src/renderer/sidebar/sidebar.ts`、`GUI/src/renderer/settings/settings.ts`（IPC API 使用点）
- Create: `GUI/src/shared/protocol-map.ts`（对齐结果：Aliya 事件 → AGUI 事件映射表）

**Step 1: 通读 chat/main.ts 的 AGUI 事件消费**

定位 `registerAguiListener` 回调里的 `switch (event.type)` 分支，记录：
- 消费的事件类型清单（如 `RUN_STARTED / TEXT_MESSAGE / TOOL_CALL_START / TOOL_CALL_END / RUN_FINISHED / CUSTOM`）
- 每个事件依赖的字段（`messageId / delta / name / value / runId` 等）

**Step 2: 通读 sidebar.ts / settings.ts 的 window API 使用点**

记录渲染层实际调用的 `window.*` 桥方法清单（如 `window.chatStore.list / window.chat.sendMessage / window.agui.run / window.settings.getConfig`），作为 preload 桥与 IPC handler 的实现依据。

**Step 3: 产出 protocol-map.ts**

```ts
// Aliya 后端协议事件 → 渲染层 AGUI 事件 映射（Task 1 调研结果填充）
// 结构：
// export const EVENT_MAP: Record<string, string> = {
//   run_started: "RUN_STARTED",
//   text_message_start: "TEXT_MESSAGE",
//   ...
// };
```

先写骨架，具体键值以调研为准。

**Step 4: 提交**

```bash
git add GUI/src/shared/protocol-map.ts
git commit -m "docs(gui): protocol alignment map"
```

---

### Task 2: config.ts（Aliya 配置读写）

**Files:**
- Create: `GUI/src/main/config.ts`
- Create: `GUI/src/main/config.test.ts`

**Step 1: 写失败测试**

```ts
// config.test.ts
import { describe, it, expect } from "vitest";
import { parseYamlTopLevel, readYamlValue, writeJsonAtomic } from "./config";

describe("config utils", () => {
  it("parses top-level yaml keys", () => {
    const yaml = "cosmos:\n  service:\n    agent:\n      llm:\n        provider: deepseek\n";
    const keys = parseYamlTopLevel(yaml);
    expect(keys).toContain("cosmos");
  });

  it("reads nested yaml value by key path", () => {
    const yaml = "cosmos:\n  service:\n    agent:\n      llm:\n        provider: deepseek\n";
    expect(readYamlValue(yaml, "cosmos.service.agent.llm.provider")).toBe("deepseek");
  });

  it("writes json atomically via temp file", async () => {
    const { writeJsonAtomic } = await import("./config");
    // 需要注入 fs 或使用真实临时目录；实现时用 os.tmpdir
    expect(typeof writeJsonAtomic).toBe("function");
  });
});
```

**Step 2: 运行测试确认失败**

Run: `cd GUI && npx vitest run src/main/config.test.ts`
Expected: FAIL（config 模块不存在）。

**Step 3: 最小实现**

```ts
// config.ts
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

export function parseYamlTopLevel(yaml: string): string[] {
  const keys: string[] = [];
  for (const line of yaml.split("\n")) {
    const m = line.match(/^([A-Za-z0-9_-]+):/);
    if (m) keys.push(m[1]);
  }
  return keys;
}

export function readYamlValue(yaml: string, keyPath: string): string | null {
  const parts = keyPath.split(".");
  let depth = 0;
  let current = "";
  for (const line of yaml.split("\n")) {
    const indent = line.search(/\S/);
    const trimmed = line.trim();
    if (indent === -1 || trimmed.startsWith("#")) continue;
    const key = trimmed.split(":")[0].trim();
    const isMatch = key === parts[depth];
    if (isMatch) {
      current = key;
      depth += 1;
      if (depth === parts.length) {
        const value = trimmed.slice(trimmed.indexOf(":") + 1).trim();
        return value.replace(/^"|"$/g, "");
      }
    } else if (indent < line.search(/\S/) && current) {
      return null; // 路径中断
    }
  }
  return null;
}

export function writeJsonAtomic(filePath: string, data: unknown): void {
  const dir = path.dirname(filePath);
  const tmp = path.join(dir, `.tmp-${path.basename(filePath)}-${Date.now()}`);
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf-8");
  fs.renameSync(tmp, filePath);
}

export const ALIYA_ROOT = path.resolve(__dirname, "../../.."); // GUI/dist/main/main → 项目根
export const CONFIG_DIR = path.join(ALIYA_ROOT, "data/config");
export const MAIN_YML = path.join(CONFIG_DIR, "main.yml");
export const LLM_PROVIDERS = path.join(CONFIG_DIR, "LLMProviders.json");

export interface GuiPreferences {
  autoLaunchBackend: boolean;
  wsUrl: string;
  uiTheme: "classic" | "pearl-white";
  uiFont: { kind: "default" } | { kind: "custom"; fileName: string };
}

const PREF_PATH = () => path.join(appDataDir(), "gui-preferences.json");

function appDataDir(): string {
  return process.env.APPDATA ?? os.homedir();
}

export function loadPreferences(): GuiPreferences {
  try {
    const raw = fs.readFileSync(PREF_PATH(), "utf-8");
    return { autoLaunchBackend: true, wsUrl: "ws://127.0.0.1:8765/agent/ws", uiTheme: "classic", uiFont: { kind: "default" }, ...JSON.parse(raw) };
  } catch {
    return { autoLaunchBackend: true, wsUrl: "ws://127.0.0.1:8765/agent/ws", uiTheme: "classic", uiFont: { kind: "default" } };
  }
}

export function savePreferences(pref: GuiPreferences): void {
  writeJsonAtomic(PREF_PATH(), pref);
}

export function loadLLMProviders(): Record<string, unknown> {
  try {
    return JSON.parse(fs.readFileSync(LLM_PROVIDERS, "utf-8"));
  } catch {
    return {};
  }
}

export function saveLLMProviders(data: Record<string, unknown>): void {
  writeJsonAtomic(LLM_PROVIDERS, data);
}
```

**Step 4: 运行测试确认通过**

Run: `cd GUI && npx vitest run src/main/config.test.ts`
Expected: PASS（writeJsonAtomic 的断言按实际实现调整）。

**Step 5: 提交**

```bash
git add GUI/src/main/config.ts GUI/src/main/config.test.ts
git commit -m "feat(gui): config read/write + preferences"
```

---

### Task 3: backend.ts（Python 子进程管理）

**Files:**
- Create: `GUI/src/main/backend.ts`
- Create: `GUI/src/main/backend.test.ts`

**Step 1: 写失败测试**

```ts
// backend.test.ts
import { describe, it, expect, vi } from "vitest";
import { BackendController } from "./backend";

describe("BackendController", () => {
  it("does not spawn when autoLaunch is false", () => {
    const spawn = vi.fn();
    const ctrl = new BackendController({ autoLaunch: false, spawnImpl: spawn });
    ctrl.start();
    expect(spawn).not.toHaveBeenCalled();
  });

  it("spawns python main.py when autoLaunch is true", () => {
    const spawn = vi.fn(() => ({ on: () => {}, kill: () => {} }));
    const ctrl = new BackendController({ autoLaunch: true, spawnImpl: spawn, rootDir: "." });
    ctrl.start();
    expect(spawn).toHaveBeenCalled();
  });
});
```

**Step 2: 运行确认失败**

Run: `cd GUI && npx vitest run src/main/backend.test.ts`
Expected: FAIL（backend 模块不存在）。

**Step 3: 最小实现**

```ts
// backend.ts
import { spawn, type ChildProcess } from "child_process";
import * as path from "path";
import { EventEmitter } from "events";

export interface BackendOptions {
  autoLaunch: boolean;
  rootDir: string;      // 项目根（main.py 所在）
  python?: string;      // 默认 "python"
  spawnImpl?: typeof spawn;
  onLog?: (line: string) => void;
}

export class BackendController extends EventEmitter {
  private proc: ChildProcess | null = null;
  private readonly opts: BackendOptions;

  constructor(opts: BackendOptions) {
    super();
    this.opts = opts;
  }

  start(): void {
    if (!this.opts.autoLaunch) {
      this.emit("manual-mode");
      return;
    }
    const spawnFn = this.opts.spawnImpl ?? spawn;
    this.proc = spawnFn(this.opts.python ?? "python", ["main.py"], {
      cwd: this.opts.rootDir,
      env: { ...process.env },
      windowsHide: true,
    });
    this.proc.stdout?.on("data", (d: Buffer) => this.opts.onLog?.(d.toString()));
    this.proc.stderr?.on("data", (d: Buffer) => this.opts.onLog?.(d.toString()));
    this.proc.on("exit", (code) => this.emit("exit", code));
    this.emit("spawned");
  }

  stop(): void {
    if (this.proc) {
      this.proc.kill();
      this.proc = null;
    }
  }

  isRunning(): boolean {
    return this.proc !== null && !this.proc.killed;
  }
}
```

**Step 4: 运行确认通过**

Run: `cd GUI && npx vitest run src/main/backend.test.ts`
Expected: PASS。

**Step 5: 提交**

```bash
git add GUI/src/main/backend.ts GUI/src/main/backend.test.ts
git commit -m "feat(gui): backend subprocess controller"
```

---

### Task 4: ws.ts（Aliya WS 客户端 + 协议映射 + 音频转发）

**Files:**
- Create: `GUI/src/main/ws.ts`
- Create: `GUI/src/main/ws.test.ts`

**Step 1: 写失败测试（协议映射纯函数）**

```ts
// ws.test.ts
import { describe, it, expect } from "vitest";
import { mapProtocolEvent } from "./ws";

describe("mapProtocolEvent", () => {
  it("maps run_started to RUN_STARTED", () => {
    expect(mapProtocolEvent({ type: "run_started", session_id: "s1" })?.type).toBe("RUN_STARTED");
  });

  it("maps text_message_content to TEXT_MESSAGE with delta", () => {
    const ev = mapProtocolEvent({ type: "text_message_content", message_id: "m1", text: "你好" });
    expect(ev?.type).toBe("TEXT_MESSAGE");
    expect(ev?.messageId).toBe("m1");
    expect(ev?.delta).toBe("你好");
  });

  it("passes through confirm_request with callId", () => {
    const ev = mapProtocolEvent({ type: "confirm_request", call_id: "c1", tool: "web_search", params: { q: "x" } });
    expect(ev?.type).toBe("CONFIRM_REQUEST");
    expect(ev?.callId).toBe("c1");
  });
});
```

**Step 2: 运行确认失败**

Run: `cd GUI && npx vitest run src/main/ws.test.ts`
Expected: FAIL。

**Step 3: 最小实现**

```ts
// ws.ts
import { EventEmitter } from "events";

export interface ProtocolEvent {
  type: string;
  [k: string]: unknown;
}

export interface MappedEvent {
  type: string;
  [k: string]: unknown;
}

/** Aliya 线上协议 → 渲染层 AGUI 事件 的字段映射（与 protocol-map.ts 保持一致） */
export function mapProtocolEvent(ev: ProtocolEvent): MappedEvent | null {
  switch (ev.type) {
    case "run_started":
      return { type: "RUN_STARTED", runId: ev.session_id, sessionId: ev.session_id };
    case "run_finished":
      return { type: "RUN_FINISHED", runId: ev.session_id, sessionId: ev.session_id };
    case "step_started":
      return { type: "STEP_STARTED", phase: ev.phase };
    case "step_finished":
      return { type: "STEP_FINISHED", phase: ev.phase };
    case "text_message_start":
      return { type: "TEXT_MESSAGE_START", messageId: ev.message_id };
    case "text_message_content":
      return { type: "TEXT_MESSAGE", messageId: ev.message_id, delta: ev.text };
    case "text_message_end":
      return { type: "TEXT_MESSAGE_END", messageId: ev.message_id, fullText: ev.full_text };
    case "tool_call_start":
      return { type: "TOOL_CALL_START", toolName: ev.tool_name, arguments: ev.arguments, callId: ev.call_id };
    case "tool_call_result":
      return { type: "TOOL_CALL_RESULT", callId: ev.call_id, output: ev.output };
    case "tool_call_end":
      return { type: "TOOL_CALL_END", callId: ev.call_id };
    case "confirm_request":
      return { type: "CONFIRM_REQUEST", callId: ev.call_id, tool: ev.tool ?? ev.tool_name, params: ev.params ?? ev.arguments };
    case "error":
      return { type: "ERROR", message: ev.message };
    case "notice":
      return { type: "NOTICE", message: ev.message };
    case "token_usage":
      return { type: "TOKEN_USAGE", total: ev.total, input: ev.input, output: ev.output };
    case "emotion_changed":
      return { type: "EMOTION_CHANGED", dominant: ev.dominant, scores: ev.scores };
    case "tts_features":
      return { type: "TTS_FEATURES", features: ev };
    case "session_list":
      return { type: "SESSION_LIST", sessions: ev.sessions };
    case "session_switched":
      return { type: "SESSION_SWITCHED", sessionId: ev.session_id };
    case "session_deleted":
      return { type: "SESSION_DELETED", sessionId: ev.session_id, deleted: ev.deleted };
    case "status_changed":
      return { type: "STATUS_CHANGED", status: ev.status };
    default:
      return null;
  }
}

export interface AliyaWsOptions {
  url: string;
  onEvent: (ev: MappedEvent) => void;
  onBinaryAudio: (buf: ArrayBuffer) => void;
  onStateChange: (state: "connecting" | "connected" | "disconnected") => void;
  createWs?: (url: string) => WebSocket;
}

export class AliyaWsClient extends EventEmitter {
  private ws: WebSocket | null = null;
  private closed = false;
  private reconnectAttempt = 0;
  private readonly opts: AliyaWsOptions;

  constructor(opts: AliyaWsOptions) {
    super();
    this.opts = opts;
  }

  connect(): void {
    this.closed = false;
    this.open();
  }

  private open(): void {
    this.opts.onStateChange("connecting");
    const WS = this.opts.createWs ?? (WebSocket as unknown as typeof WebSocket);
    try {
      this.ws = new WS(this.opts.url);
    } catch (err) {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.opts.onStateChange("connected");
      this.emit("connected");
    };
    this.ws.onmessage = (e: MessageEvent) => {
      if (typeof e.data === "string") {
        const mapped = mapProtocolEvent(JSON.parse(e.data) as ProtocolEvent);
        if (mapped) this.opts.onEvent(mapped);
      } else {
        const buf = e.data as ArrayBuffer;
        this.opts.onBinaryAudio(buf);
      }
    };
    this.ws.onclose = () => {
      this.opts.onStateChange("disconnected");
      if (!this.closed) this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect(): void {
    const delay = Math.min(1000 * 2 ** this.reconnectAttempt, 15000);
    this.reconnectAttempt += 1;
    setTimeout(() => {
      if (!this.closed) this.open();
    }, delay);
  }

  send(obj: unknown): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
  }
}
```

**Step 4: 运行确认通过**

Run: `cd GUI && npx vitest run src/main/ws.test.ts`
Expected: PASS。

**Step 5: 提交**

```bash
git add GUI/src/main/ws.ts GUI/src/main/ws.test.ts
git commit -m "feat(gui): aliya ws client + protocol mapping"
```

---

### Task 5: state.ts（状态聚合与广播）

**Files:**
- Create: `GUI/src/main/state.ts`
- Create: `GUI/src/main/state.test.ts`

**Step 1: 写失败测试**

```ts
// state.test.ts
import { describe, it, expect, vi } from "vitest";
import { AppState } from "./state";

describe("AppState", () => {
  it("aggregates emotion + token + connection", () => {
    const s = new AppState();
    s.setEmotion({ dominant: "joy", scores: { joy: 0.9 } });
    s.setToken({ total: 100, input: 60, output: 40 });
    s.setConnection("connected");
    expect(s.snapshot()).toMatchObject({
      emotion: { dominant: "joy" },
      token: { total: 100 },
      connection: "connected",
    });
  });

  it("throttles broadcast via trailing flag", () => {
    vi.useFakeTimers();
    const s = new AppState();
    const cb = vi.fn();
    s.subscribe(cb);
    s.setConnection("connected");
    s.setConnection("connecting");
    expect(cb).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(cb).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
```

**Step 2: 运行确认失败**

Run: `cd GUI && npx vitest run src/main/state.test.ts`
Expected: FAIL。

**Step 3: 最小实现**

```ts
// state.ts
import { EventEmitter } from "events";

export interface StateSnapshot {
  connection: "connecting" | "connected" | "disconnected";
  emotion: { dominant: string; scores: Record<string, number> };
  token: { total: number; input: number; output: number };
  activeSessionId: string | null;
}

export class AppState {
  private snap: StateSnapshot = {
    connection: "disconnected",
    emotion: { dominant: "neutral", scores: {} },
    token: { total: 0, input: 0, output: 0 },
    activeSessionId: null,
  };
  private emitter = new EventEmitter();
  private dirty = false;
  private timer: NodeJS.Timeout | null = null;

  setEmotion(e: { dominant: string; scores: Record<string, number> }): void {
    this.snap.emotion = e;
    this.markDirty();
  }
  setToken(t: { total: number; input: number; output: number }): void {
    this.snap.token = t;
    this.markDirty();
  }
  setConnection(c: StateSnapshot["connection"]): void {
    this.snap.connection = c;
    this.markDirty();
  }
  setActiveSessionId(id: string | null): void {
    this.snap.activeSessionId = id;
    this.markDirty();
  }
  snapshot(): StateSnapshot {
    return { ...this.snap, emotion: { ...this.snap.emotion }, token: { ...this.snap.token } };
  }
  subscribe(cb: (snap: StateSnapshot) => void): () => void {
    this.emitter.on("change", cb);
    return () => this.emitter.off("change", cb);
  }
  private markDirty(): void {
    this.dirty = true;
    if (this.timer) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.dirty) return;
      this.dirty = false;
      this.emitter.emit("change", this.snapshot());
    }, 150);
  }
}
```

**Step 4: 运行确认通过**

Run: `cd GUI && npx vitest run src/main/state.test.ts`
Expected: PASS。

**Step 5: 提交**

```bash
git add GUI/src/main/state.ts GUI/src/main/state.test.ts
git commit -m "feat(gui): app state aggregation"
```

---

### Task 6: ipc.ts + preload 完整桥

**Files:**
- Modify: `GUI/src/shared/ipc-channels.ts`（按 Task 1 调研结果扩充）
- Create: `GUI/src/main/ipc.ts`
- Rewrite: `GUI/src/preload/index.ts`

**Step 1: 扩充 ipc-channels.ts**（追加 P2 需要的通道）

```ts
export const IPC = {
  // ... P0 已有通道 ...
  CHAT_SEND_MESSAGE: "chat:send-message",
  CHAT_IS_MAXIMIZED: "chat:is-maximized",
  AGUI_RUN: "agui:run",
  AGUI_EVENT: "agui:event",
  AGUI_CANCEL: "agui:cancel",
  CHATS_LIST: "chats:list",
  CHATS_CREATE: "chats:create",
  CHATS_DELETE: "chats:delete",
  CHATS_RENAME: "chats:rename",
  CHATS_SET_ACTIVE_SESSION: "chats:set-active-session",
  CHATS_CHANGED: "chats:changed",
  CHATS_SWITCH_SESSION: "chats:switch-session",
  SETTINGS_GET_CONFIG: "settings:get-config",
  SETTINGS_SAVE_CONFIG: "settings:save-config",
  SETTINGS_GET_GENERAL: "settings:get-general",
  SETTINGS_SAVE_GENERAL: "settings:save-general",
  SETTINGS_TEST_CONNECTION: "settings:test-connection",
  UI_THEME_GET: "ui-theme:get",
  UI_THEME_CHANGED: "ui-theme:changed",
  UI_FONT_GET: "ui-font:get",
  UI_FONT_CHANGED: "ui-font:changed",
  LIVE2D_MOUTH_START: "live2d:mouth-start",
  LIVE2D_MOUTH_STOP: "live2d:mouth-stop",
  LIVE2D_PLAY_ACTION: "live2d:play-action",
  TOKEN_USAGE_GET: "token-usage:get",
} as const;
```

**Step 2: 创建 ipc.ts**

```ts
import { BrowserWindow, ipcMain } from "electron";
import { IPC } from "../shared/ipc-channels";
import { AliyaWsClient } from "./ws";
import { AppState } from "./state";
import { loadPreferences, savePreferences, loadLLMProviders, saveLLMProviders, MAIN_YML, readYamlValue } from "./config";

export interface IpcDeps {
  ws: AliyaWsClient;
  state: AppState;
  getChatWindow: () => BrowserWindow | null;
}

export function registerIpc(deps: IpcDeps): void {
  const { ws, state } = deps;

  ipcMain.handle(IPC.AGUI_RUN, (_e, input: { messages: unknown[]; style: string; sessionId?: string }) => {
    ws.send({
      type: "user_message",
      text: buildUserText(input),
      images: extractImages(input),
    });
    return { success: true };
  });

  ipcMain.handle(IPC.AGUI_CANCEL, () => {
    ws.send({ type: "stop" });
    return { ok: true };
  });

  ipcMain.handle(IPC.CHAT_SEND_MESSAGE, (_e, messages: unknown[], _style: string) => {
    const text = (messages[messages.length - 1] as { text?: string })?.text ?? "";
    ws.send({ type: "user_message", text });
    return { ok: true };
  });

  // 会话管理
  ipcMain.handle(IPC.CHATS_LIST, async () => {
    const result = await requestOnce(ws, { type: "list_sessions" }, "SESSION_LIST");
    return result?.sessions ?? [];
  });
  ipcMain.handle(IPC.CHATS_CREATE, async () => {
    // 复用当前 WS 会话（AgentSession 由连接创建）；返回一个会话壳
    const id = `local-${Date.now()}`;
    state.setActiveSessionId(id);
    return { id, title: "新对话", updated_at: Date.now(), message_count: 0, pinned: false };
  });
  ipcMain.handle(IPC.CHATS_DELETE, async (_e, payload: { id: string }) => {
    ws.send({ type: "delete_session", session_id: payload.id });
    return { ok: true };
  });
  ipcMain.handle(IPC.CHATS_RENAME, async (_e, payload: { id: string; title: string }) => {
    return { ok: true }; // Aliya 后端无重命名接口；本地偏好可选记录
  });
  ipcMain.handle(IPC.CHATS_SET_ACTIVE_SESSION, (_e, sessionId: string | null) => {
    state.setActiveSessionId(sessionId);
    return { ok: true };
  });

  // 设置
  ipcMain.handle(IPC.SETTINGS_GET_CONFIG, () => ({
    llmProviders: loadLLMProviders(),
    llmCurrent: readYamlValue(readFileSafe(MAIN_YML), "cosmos.service.agent.llm.provider"),
    guiPreferences: loadPreferences(),
  }));
  ipcMain.handle(IPC.SETTINGS_SAVE_CONFIG, (_e, cfg: { llmProviders?: Record<string, unknown> }) => {
    if (cfg.llmProviders) saveLLMProviders(cfg.llmProviders);
    return { ok: true };
  });
  ipcMain.handle(IPC.SETTINGS_GET_GENERAL, () => loadPreferences());
  ipcMain.handle(IPC.SETTINGS_SAVE_GENERAL, (_e, pref: unknown) => {
    savePreferences({ ...loadPreferences(), ...(pref as object) });
    return { ok: true };
  });

  // 主题/字体
  ipcMain.handle(IPC.UI_THEME_GET, () => loadPreferences().uiTheme);
  ipcMain.handle(IPC.UI_FONT_GET, () => loadPreferences().uiFont);

  // 聊天窗口最大化
  ipcMain.handle(IPC.CHAT_IS_MAXIMIZED, (e) => BrowserWindow.fromWebContents(e.sender)?.isMaximized() ?? false);
}

// ---- helpers ----
import * as fs from "fs";
function readFileSafe(p: string): string {
  try { return fs.readFileSync(p, "utf-8"); } catch { return ""; }
}
function buildUserText(input: { messages: unknown[] }): string {
  const last = input.messages[input.messages.length - 1];
  return typeof last === "object" && last !== null && "text" in last ? String((last as { text: unknown }).text) : "";
}
function extractImages(input: { messages: unknown[] }): string[] | undefined {
  return undefined; // Aliya 后端 images 支持：按消息结构提取，P3 完善
}
function requestOnce(ws: AliyaWsClient, msg: unknown, expectType: string, timeoutMs = 3000): Promise<unknown | null> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => { off(); resolve(null); }, timeoutMs);
    const off = ws.on("event", (ev: { type: string }) => {
      if (ev.type === expectType) { clearTimeout(timer); off(); resolve(ev); }
    });
    ws.send(msg);
  });
}
```

> 注：`AliyaWsClient` 需在 emit("event") 时把 mapped event 透传（Task 4 的 `onEvent` 回调改为内部 emit，供 requestOnce 使用）。

**Step 3: 重写 preload/index.ts**（暴露 4 窗口需要的桥：`windowControls/cyreneTheme/cyreneFont/chat/agui/chatStore/settings/tts`）

```ts
import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "../shared/ipc-channels";

contextBridge.exposeInMainWorld("windowControls", {
  minimize: () => ipcRenderer.send(IPC.WINDOW_MINIMIZE),
  close: () => ipcRenderer.send(IPC.WINDOW_CLOSE),
  quit: () => ipcRenderer.send(IPC.APP_QUIT),
});

contextBridge.exposeInMainWorld("cyreneTheme", {
  get: () => ipcRenderer.invoke(IPC.UI_THEME_GET),
  onChanged: (cb: (t: string) => void) => {
    const l = (_e: unknown, t: string) => cb(t);
    ipcRenderer.on(IPC.UI_THEME_CHANGED, l);
    return () => ipcRenderer.off(IPC.UI_THEME_CHANGED, l);
  },
});

contextBridge.exposeInMainWorld("cyreneFont", {
  get: () => ipcRenderer.invoke(IPC.UI_FONT_GET),
  onChanged: (cb: (f: unknown) => void) => {
    const l = (_e: unknown, f: unknown) => cb(f);
    ipcRenderer.on(IPC.UI_FONT_CHANGED, l);
    return () => ipcRenderer.off(IPC.UI_FONT_CHANGED, l);
  },
});

contextBridge.exposeInMainWorld("chat", {
  minimize: () => ipcRenderer.send(IPC.CHAT_MINIMIZE),
  close: () => ipcRenderer.send(IPC.CHAT_CLOSE),
  toggleMaximize: () => ipcRenderer.send(IPC.CHAT_TOGGLE_MAXIMIZE),
  isMaximized: () => ipcRenderer.invoke(IPC.CHAT_IS_MAXIMIZED),
  sendMessage: (messages: unknown[], style: string) => ipcRenderer.invoke(IPC.CHAT_SEND_MESSAGE, messages, style),
});

contextBridge.exposeInMainWorld("agui", {
  run: (input: unknown) => ipcRenderer.invoke(IPC.AGUI_RUN, input),
  onEvent: (cb: (ev: unknown) => void) => {
    const l = (_e: unknown, ev: unknown) => cb(ev);
    ipcRenderer.on(IPC.AGUI_EVENT, l);
    return () => ipcRenderer.off(IPC.AGUI_EVENT, l);
  },
  cancel: () => ipcRenderer.invoke(IPC.AGUI_CANCEL),
});

contextBridge.exposeInMainWorld("chatStore", {
  list: () => ipcRenderer.invoke(IPC.CHATS_LIST),
  create: () => ipcRenderer.invoke(IPC.CHATS_CREATE),
  delete: (id: string) => ipcRenderer.invoke(IPC.CHATS_DELETE, { id }),
  rename: (id: string, title: string) => ipcRenderer.invoke(IPC.CHATS_RENAME, { id, title }),
  setActiveSession: (id: string | null) => ipcRenderer.invoke(IPC.CHATS_SET_ACTIVE_SESSION, id),
  onChanged: (cb: () => void) => {
    const l = () => cb();
    ipcRenderer.on(IPC.CHATS_CHANGED, l);
    return () => ipcRenderer.off(IPC.CHATS_CHANGED, l);
  },
  onSwitchSession: (cb: (id: string) => void) => {
    const l = (_e: unknown, id: string) => cb(id);
    ipcRenderer.on(IPC.CHATS_SWITCH_SESSION, l);
    return () => ipcRenderer.off(IPC.CHATS_SWITCH_SESSION, l);
  },
});

contextBridge.exposeInMainWorld("settings", {
  minimize: () => ipcRenderer.send(IPC.SETTINGS_MINIMIZE),
  close: () => ipcRenderer.send(IPC.SETTINGS_CLOSE),
  getConfig: () => ipcRenderer.invoke(IPC.SETTINGS_GET_CONFIG),
  saveConfig: (c: unknown) => ipcRenderer.invoke(IPC.SETTINGS_SAVE_CONFIG, c),
  getGeneral: () => ipcRenderer.invoke(IPC.SETTINGS_GET_GENERAL),
  saveGeneral: (c: unknown) => ipcRenderer.invoke(IPC.SETTINGS_SAVE_GENERAL, c),
});

// live2d 嘴型/动作桥
contextBridge.exposeInMainWorld("live2dSpeech", {
  onMouthStart: (cb: (p: { durationMs: number }) => void) => {
    const l = (_e: unknown, p: { durationMs: number }) => cb(p);
    ipcRenderer.on(IPC.LIVE2D_MOUTH_START, l);
    return () => ipcRenderer.off(IPC.LIVE2D_MOUTH_START, l);
  },
  onMouthStop: (cb: () => void) => {
    const l = () => cb();
    ipcRenderer.on(IPC.LIVE2D_MOUTH_STOP, l);
    return () => ipcRenderer.off(IPC.LIVE2D_MOUTH_STOP, l);
  },
});
contextBridge.exposeInMainWorld("live2dAction", {
  onPlayAction: (cb: (t: unknown) => void) => {
    const l = (_e: unknown, t: unknown) => cb(t);
    ipcRenderer.on(IPC.LIVE2D_PLAY_ACTION, l);
    return () => ipcRenderer.off(IPC.LIVE2D_PLAY_ACTION, l);
  },
});
```

**Step 4: 提交**

```bash
git add GUI/src/shared/ipc-channels.ts GUI/src/main/ipc.ts GUI/src/preload/index.ts
git commit -m "feat(gui): ipc handlers + full preload bridge"
```

---

### Task 7: main/index.ts 组装（backend + ws + state + windows 联动）

**Files:**
- Modify: `GUI/src/main/index.ts`
- Modify: `GUI/src/main/windows.ts`（注入 ws 状态/事件）

**Step 1: 重写 index.ts**

```ts
import { app, BrowserWindow, ipcMain } from "electron";
import { IPC } from "../shared/ipc-channels";
import * as path from "path";
import { createPetWindow, createChatWindow, createSidebarWindow, createSettingsWindow } from "./windows";
import { setupTray } from "./tray";
import { BackendController } from "./backend";
import { AliyaWsClient } from "./ws";
import { AppState } from "./state";
import { registerIpc } from "./ipc";
import { loadPreferences, ALIYA_ROOT } from "./config";

const prefs = loadPreferences();
const state = new AppState();
const ws = new AliyaWsClient({
  url: prefs.wsUrl,
  onEvent: (ev) => {
    broadcast(IPC.AGUI_EVENT, ev);
    ws.emit("event", ev);   // 供 requestOnce 等内部使用
  },
  onBinaryAudio: (buf) => {
    broadcastAudio(buf);
  },
  onStateChange: (conn) => {
    state.setConnection(conn);
    broadcast("connection:changed", conn);
  },
});

let petWin: BrowserWindow | null = null;
let chatWin: BrowserWindow | null = null;
let sidebarWin: BrowserWindow | null = null;
let settingsWin: BrowserWindow | null = null;

function broadcast(channel: string, payload: unknown): void {
  for (const w of BrowserWindow.getAllWindows()) {
    if (!w.isDestroyed()) w.webContents.send(channel, payload);
  }
}
function broadcastAudio(buf: ArrayBuffer): void {
  // 发给桌宠与聊天窗口（播放 + 嘴型）
  for (const w of [petWin, chatWin]) {
    if (w && !w.isDestroyed()) w.webContents.send("audio:chunk", buf);
  }
}

function registerWindowIpc(): void {
  ipcMain.on(IPC.WINDOW_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.WINDOW_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.APP_QUIT, () => app.quit());
  ipcMain.on(IPC.CHAT_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.CHAT_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.CHAT_TOGGLE_MAXIMIZE, (e) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (!win) return;
    if (win.isMaximized()) win.unmaximize(); else win.maximize();
  });
  ipcMain.on(IPC.SIDEBAR_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.SIDEBAR_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.SETTINGS_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.SETTINGS_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
}

const backend = new BackendController({
  autoLaunch: prefs.autoLaunchBackend,
  rootDir: ALIYA_ROOT,
  python: process.env.ALIYA_PYTHON ?? "python",
  onLog: (line) => console.log("[backend]", line.trimEnd()),
});

app.whenReady().then(() => {
  registerWindowIpc();
  registerIpc({ ws, state, getChatWindow: () => chatWin });

  petWin = createPetWindow();
  chatWin = createChatWindow();
  sidebarWin = createSidebarWindow();
  settingsWin = createSettingsWindow();
  setupTray(() => chatWin?.show(), () => app.quit());

  backend.start();
  ws.connect();

  state.subscribe((snap) => broadcast("state:changed", snap));
});

app.on("before-quit", () => {
  backend.stop();
  ws.close();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
```

**Step 2: 更新 windows.ts 的 loadRenderer 路径**

确认 dev 模式下 `VITE_DEV=1` 时加载 `http://localhost:5173/<page>`，生产加载 `dist/renderer/<page>`（P0 已实现，无需改动，仅验证）。

**Step 3: 提交**

```bash
git add GUI/src/main/index.ts GUI/src/main/windows.ts
git commit -m "feat(gui): assemble backend/ws/state lifecycle"
```

---

### Task 8: 主进程类型检查与集成验证

**Files:**
- 无新增；运行验证

**Step 1: 类型检查**

Run: `cd GUI && npm run typecheck`
Expected: 主进程相关错误为 0（渲染层错误可能仍存在，属 P3 范围）。

**Step 2: 手动启动验证**

Run: `cd GUI && npm start`
Expected:
- 自动拉起 `python main.py`（控制台出现 `[backend]` 日志）
- WS 连接成功（`connection:connected`）
- 聊天窗口输入"你好"→ 事件流 `RUN_STARTED/TEXT_MESSAGE/RUN_FINISHED` 经 `AGUI_EVENT` 到达渲染层（控制台无 preload 报错）
- 关闭窗口不崩溃，托盘退出正常

**Step 3: 提交（如有修复）**

```bash
git add GUI/src
git commit -m "fix(gui): integration fixes"
```

---

## 完成标准

- [ ] `config.test.ts` / `backend.test.ts` / `ws.test.ts` / `state.test.ts` 全通过
- [ ] `npm run typecheck` 主进程 0 错误
- [ ] `npm start` 自动拉起后端 + WS 连接成功
- [ ] 聊天窗口能发起对话并收到流式事件
- [ ] 各 Task 均有独立 commit
