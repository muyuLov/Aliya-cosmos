# GUI P0 脚手架 Implementation Plan

> **提交策略：** 本计划中所有 `git commit` 步骤均**跳过**（用户要求：不提交 git、不推送 GitHub）。任务完成标准不变。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 创建 `GUI/` Electron 工程骨架（package.json + Vite 多入口 + tsc 三套 + 可启动的 4 窗口空壳），后续阶段在此基础上一层层搬入 Cyrene 渲染层与 Aliya WS 适配。

**Architecture:** 对标 Cyrene：`tsc` 编译主进程/preload（CommonJS）到 `dist/main`、`dist/preload`，Vite 编译渲染层多入口（chat/sidebar/settings/index 桌宠）到 `dist/renderer`。主进程入口 `dist/main/main/index.js`，`electron .` 启动。

**Tech Stack:** Electron 43、TypeScript 5、Vite 5、Vitest 4、pixi.js 7、pixi-live2d-display 0.5.0-beta。

**参考源：** `example/Cyrene-Agent-master/`（构建配置、package.json、vite.config.ts、tsconfig.*）。

**前置条件：**
- 已读设计文档 `docs/plans/2026-08-25-cyrene-ui-replica-design.md`
- Node.js ≥ 24（Cyrene engines 要求；若本机版本低，需先装）

---

### Task 1: 创建 GUI 目录与 package.json

**Files:**
- Create: `GUI/package.json`
- Create: `GUI/.gitignore`

**Step 1: 创建 package.json**

```json
{
  "name": "aliya-gui",
  "version": "0.1.0",
  "description": "Aliya desktop Live2D companion (Cyrene UI replica)",
  "main": "dist/main/main/index.js",
  "scripts": {
    "build:main": "tsc -p tsconfig.main.json",
    "build:preload": "tsc -p tsconfig.preload.json",
    "build:renderer": "vite build",
    "build": "npm run build:main && npm run build:preload && npm run build:renderer",
    "dev": "npm run build:main && npm run build:preload && concurrently \"vite\" \"cross-env VITE_DEV=1 electron .\"",
    "start": "electron .",
    "typecheck": "tsc -p tsconfig.json --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "engines": {
    "node": ">=24",
    "npm": ">=10"
  },
  "devDependencies": {
    "concurrently": "^9.2.1",
    "cross-env": "^7.0.3",
    "electron": "^43.0.0",
    "typescript": "^5.6.0",
    "vite": "^5.0.0",
    "vitest": "^4.1.9"
  },
  "dependencies": {
    "pixi-live2d-display": "0.5.0-beta",
    "pixi.js": "^7.3.0"
  }
}
```

**Step 2: 创建 .gitignore**

```
node_modules/
dist/
```

**Step 3: 安装依赖并验证**

Run: `cd GUI && npm install`
Expected: 安装完成，无致命报错（electron 二进制下载可能较慢）。

**Step 4: 提交**

```bash
git add GUI/package.json GUI/.gitignore
git commit -m "feat(gui): scaffold package.json"
```

---

### Task 2: tsconfig 三套

**Files:**
- Create: `GUI/tsconfig.json`（渲染层 typecheck 用）
- Create: `GUI/tsconfig.main.json`
- Create: `GUI/tsconfig.preload.json`

**Step 1: 创建 tsconfig.json**（与 Cyrene 一致，renderer 由 Vite 处理，此文件仅 `--noEmit` typecheck）

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vite/client", "node"]
  },
  "include": ["src/**/*.ts", "src/**/*.tsx"]
}
```

**Step 2: 创建 tsconfig.main.json**（主进程编译，CommonJS）

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022"],
    "outDir": "dist/main",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": false,
    "sourceMap": true
  },
  "include": ["src/main/**/*.ts", "src/shared/**/*.ts"],
  "exclude": ["src/shared/renderer-base.ts"]
}
```

**Step 3: 创建 tsconfig.preload.json**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "commonjs",
    "lib": ["ES2022", "DOM"],
    "outDir": "dist/preload",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "declaration": false,
    "sourceMap": true
  },
  "include": ["src/preload/**/*.ts", "src/shared/**/*.ts"],
  "exclude": ["src/shared/renderer-base.ts"]
}
```

**Step 4: 提交**

```bash
git add GUI/tsconfig.json GUI/tsconfig.main.json GUI/tsconfig.preload.json
git commit -m "feat(gui): add tsconfig triple"
```

---

### Task 3: Vite 多入口配置

**Files:**
- Create: `GUI/vite.config.ts`

**Step 1: 创建 vite.config.ts**（4 入口：桌宠 index + chat/sidebar/settings）

```ts
import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  root: resolve(__dirname, "src/renderer"),
  base: "./",
  build: {
    outDir: resolve(__dirname, "dist/renderer"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        renderer: resolve(__dirname, "src/renderer/index.html"),
        chat: resolve(__dirname, "src/renderer/chat/index.html"),
        sidebar: resolve(__dirname, "src/renderer/sidebar/index.html"),
        settings: resolve(__dirname, "src/renderer/settings/index.html"),
      },
    },
  },
  server: {
    port: 5173,
    strictPort: false,
  },
});
```

**Step 2: 提交**

```bash
git add GUI/vite.config.ts
git commit -m "feat(gui): vite multi-entry config"
```

---

### Task 4: 最小可启动骨架（4 窗口空壳 + preload）

**Files:**
- Create: `GUI/src/shared/ipc-channels.ts`（最小通道集，后续阶段扩充）
- Create: `GUI/src/main/index.ts`
- Create: `GUI/src/main/windows.ts`
- Create: `GUI/src/main/tray.ts`
- Create: `GUI/src/preload/index.ts`
- Create: `GUI/src/renderer/index.html`、`chat/index.html`、`sidebar/index.html`、`settings/index.html`（各含一个占位 `<div>`）

**Step 1: 创建最小 ipc-channels.ts**

```ts
// IPC channel names shared between main and renderer（最小集，P2 扩充）
export const IPC = {
  WINDOW_MINIMIZE: "window:minimize",
  WINDOW_CLOSE: "window:close",
  APP_QUIT: "app:quit",
  CHAT_MINIMIZE: "chat:minimize",
  CHAT_CLOSE: "chat:close",
  CHAT_TOGGLE_MAXIMIZE: "chat:toggle-maximize",
  SIDEBAR_MINIMIZE: "sidebar:minimize",
  SIDEBAR_CLOSE: "sidebar:close",
  SETTINGS_MINIMIZE: "settings:minimize",
  SETTINGS_CLOSE: "settings:close",
  UI_THEME_GET: "ui-theme:get",
  UI_THEME_CHANGED: "ui-theme:changed",
} as const;
```

**Step 2: 创建 windows.ts**（4 个 BrowserWindow 工厂）

```ts
import { BrowserWindow, screen } from "electron";
import * as path from "path";

const DEV = !!process.env.VITE_DEV;

function loadRenderer(win: BrowserWindow, page: string): void {
  if (DEV) {
    void win.loadURL(`http://localhost:5173/${page}`);
  } else {
    void win.loadFile(path.join(__dirname, "../renderer", page));
  }
}

export function createPetWindow(): BrowserWindow {
  const { workArea } = screen.getPrimaryDisplay();
  const win = new BrowserWindow({
    width: 420,
    height: 560,
    x: workArea.x + workArea.width - 470,
    y: workArea.y + 80,
    transparent: true,
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    hasShadow: false,
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  loadRenderer(win, "index.html");
  return win;
}

export function createChatWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 980,
    height: 720,
    frame: false,
    minWidth: 720,
    minHeight: 520,
    backgroundColor: "#0f0d1f",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  loadRenderer(win, "chat/index.html");
  return win;
}

export function createSidebarWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 320,
    height: 640,
    frame: false,
    alwaysOnTop: false,
    backgroundColor: "#0f0d1f",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  loadRenderer(win, "sidebar/index.html");
  return win;
}

export function createSettingsWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 900,
    height: 700,
    frame: false,
    backgroundColor: "#0f0d1f",
    webPreferences: {
      preload: path.join(__dirname, "../preload/index.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  loadRenderer(win, "settings/index.html");
  return win;
}
```

**Step 3: 创建 preload/index.ts（最小桥）**

```ts
import { contextBridge, ipcRenderer } from "electron";
import { IPC } from "../shared/ipc-channels";

const windowControls = {
  minimize: () => ipcRenderer.send(IPC.WINDOW_MINIMIZE),
  close: () => ipcRenderer.send(IPC.WINDOW_CLOSE),
  quit: () => ipcRenderer.send(IPC.APP_QUIT),
};
contextBridge.exposeInMainWorld("windowControls", windowControls);

const cyreneThemeApi = {
  get: () => ipcRenderer.invoke(IPC.UI_THEME_GET) as Promise<string>,
  onChanged: (callback: (theme: string) => void) => {
    const listener = (_e: unknown, theme: string) => callback(theme);
    ipcRenderer.on(IPC.UI_THEME_CHANGED, listener);
    return () => ipcRenderer.off(IPC.UI_THEME_CHANGED, listener);
  },
};
contextBridge.exposeInMainWorld("cyreneTheme", cyreneThemeApi);
```

**Step 4: 创建 main/index.ts（生命周期 + 组装 + IPC 注册）**

```ts
import { app, BrowserWindow, ipcMain } from "electron";
import { IPC } from "../shared/ipc-channels";
import { createPetWindow, createChatWindow, createSidebarWindow, createSettingsWindow } from "./windows";
import { setupTray } from "./tray";

let petWin: BrowserWindow | null = null;
let chatWin: BrowserWindow | null = null;
let sidebarWin: BrowserWindow | null = null;
let settingsWin: BrowserWindow | null = null;

function registerWindowIpc(): void {
  ipcMain.on(IPC.WINDOW_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.WINDOW_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.APP_QUIT, () => app.quit());
  ipcMain.on(IPC.CHAT_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.CHAT_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.CHAT_TOGGLE_MAXIMIZE, (e) => {
    const win = BrowserWindow.fromWebContents(e.sender);
    if (!win) return;
    if (win.isMaximized()) win.unmaximize();
    else win.maximize();
  });
  ipcMain.on(IPC.SIDEBAR_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.SIDEBAR_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.on(IPC.SETTINGS_MINIMIZE, (e) => BrowserWindow.fromWebContents(e.sender)?.minimize());
  ipcMain.on(IPC.SETTINGS_CLOSE, (e) => BrowserWindow.fromWebContents(e.sender)?.hide());
  ipcMain.handle(IPC.UI_THEME_GET, () => "classic");
}

app.whenReady().then(() => {
  registerWindowIpc();
  petWin = createPetWindow();
  chatWin = createChatWindow();
  sidebarWin = createSidebarWindow();
  settingsWin = createSettingsWindow();
  setupTray(() => chatWin?.show(), () => app.quit());
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
```

**Step 5: 创建 tray.ts**

```ts
import { Tray, Menu, nativeImage } from "electron";
import * as path from "path";

export function setupTray(onOpenChat: () => void, onQuit: () => void): Tray {
  const icon = nativeImage.createEmpty();
  const tray = new Tray(icon);
  tray.setToolTip("Aliya");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "打开聊天", click: onOpenChat },
    { label: "退出", click: onQuit },
  ]));
  return tray;
}
```

**Step 6: 创建 4 个占位 HTML**

`src/renderer/index.html`、`chat/index.html`、`sidebar/index.html`、`settings/index.html`，内容均为：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <title>Aliya</title>
</head>
<body>
  <div id="app">Aliya（占位）</div>
</body>
</html>
```

**Step 7: 构建验证**

Run: `cd GUI && npm run typecheck && npm run build`
Expected: `tsc --noEmit` 无错误；构建产物 `dist/main/`、`dist/preload/`、`dist/renderer/` 生成。

Run: `cd GUI && npm start`
Expected: 弹出 4 个窗口（桌宠透明、聊天/侧栏/设置深色），托盘图标出现，点窗口控制按钮生效。

**Step 8: 提交**

```bash
git add GUI/src
git commit -m "feat(gui): minimal bootable 4-window shell"
```

---

### Task 5: vitest 环境接入

**Files:**
- Create: `GUI/vitest.config.ts`

**Step 1: 创建 vitest.config.ts**

```ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

**Step 2: 提交**

```bash
git add GUI/vitest.config.ts
git commit -m "feat(gui): add vitest config"
```

---

## 完成标准

- [ ] `cd GUI && npm install` 成功
- [ ] `npm run typecheck` 无错误
- [ ] `npm run build` 产物齐全
- [ ] `npm start` 弹出 4 个窗口 + 托盘，占位页可见
- [ ] 各 Task 均有独立 commit
