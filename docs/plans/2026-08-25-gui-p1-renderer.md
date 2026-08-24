# GUI P1 渲染层拷贝 Implementation Plan

> **提交策略：** 本计划中所有 `git commit` 步骤均**跳过**（用户要求：不提交 git、不推送 GitHub）。任务完成标准不变。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Cyrene 渲染层（HTML/CSS/TS + 公共资源）原样拷贝进 `GUI/src/renderer/` 与 `GUI/src/shared/`，为 P2 主进程适配与 P3 文案/裁剪提供完整代码基底。

**Architecture:** 纯拷贝阶段，不改逻辑。拷贝源 `example/Cyrene-Agent-master/src/{renderer,shared}` 中与 4 窗口（chat/sidebar/settings/桌宠）相关的文件；删除 music/sticker-manager/tasks/call 等无关文件。完成标准是"文件到位 + 错误清单已记录"——类型完整通过依赖 P2（preload 桥）+ P3（裁剪）完成。

**Tech Stack:** 无新增依赖；本阶段用 PowerShell `Copy-Item` 机械搬运。

**执行环境:** Windows PowerShell（本仓库开发环境）。所有命令在仓库根 `c:/Users/VOS-User/Desktop/Aliya-cosmos` 下运行。

**前置条件:** P0 脚手架已完成（`GUI/` 目录 + vite + tsconfig）。

---

### Task 1: 拷贝 shared/ 基础模块

**Files:**
- Copy: `example/Cyrene-Agent-master/src/shared/` → `GUI/src/shared/`（指定清单）

**Step 1: 建立目标目录并拷贝所需文件**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/shared | Out-Null
$src = "example/Cyrene-Agent-master/src/shared"
$dst = "GUI/src/shared"
$files = @(
  "ipc-channels.ts", "chat-types.ts", "chat-ui.ts", "chat-context.ts",
  "ui-theme.ts", "ui-font.ts", "renderer-base.ts",
  "message-segmentation.ts", "reasoning.ts", "live2d-actions.ts",
  "preferences.ts", "tts-types.ts", "tts-early-playback.ts"
)
foreach ($f in $files) { Copy-Item "$src/$f" "$dst/$f" }
```

**Step 2: 验证**

Run: `Get-ChildItem GUI/src/shared | Select-Object Name`
Expected: 列表包含上述 13 个文件，无多余 music/sticker 文件。

**Step 3: 提交**

```bash
git add GUI/src/shared
git commit -m "feat(gui): copy shared modules from Cyrene"
```

---

### Task 2: 拷贝 ui/ 样式与主题

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/ui/{tokens.css,fonts.css,base.css,theme.css,theme.ts}` → `GUI/src/renderer/ui/`

**Step 1: 拷贝**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/renderer/ui | Out-Null
$src = "example/Cyrene-Agent-master/src/renderer/ui"
$dst = "GUI/src/renderer/ui"
Copy-Item "$src/tokens.css" $dst
Copy-Item "$src/fonts.css" $dst
Copy-Item "$src/base.css" $dst
Copy-Item "$src/theme.css" $dst
Copy-Item "$src/theme.ts" $dst
```

> 不拷 `chart.css`（任务窗口图表）、`modal.css/modal.ts`（贴纸模态框）、`preview.html`。

**Step 2: 验证**

Run: `Get-ChildItem GUI/src/renderer/ui | Select-Object Name`
Expected: 5 个文件（tokens/fonts/base/theme.css + theme.ts）。

**Step 3: 提交**

```bash
git add GUI/src/renderer/ui
git commit -m "feat(gui): copy ui theme system"
```

---

### Task 3: 拷贝 chat/ 模块

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/chat/` → `GUI/src/renderer/chat/`（源码 + CSS + HTML，跳过 *.test.ts）

**Step 1: 拷贝**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/renderer/chat | Out-Null
$src = "example/Cyrene-Agent-master/src/renderer/chat"
$dst = "GUI/src/renderer/chat"
Copy-Item "$src/index.html" $dst
Copy-Item "$src/chat.css" $dst
Copy-Item "$src/main.ts" $dst
Copy-Item "$src/types.ts" $dst
Copy-Item "$src/message-segmentation.ts" $dst
Copy-Item "$src/sticker-src.ts" $dst
Copy-Item "$src/attachment-labels.ts" $dst
Copy-Item "$src/document-processing.ts" $dst
Copy-Item "$src/reasoning-dropdown.ts" $dst
```

**Step 2: 验证**

Run: `Get-ChildItem GUI/src/renderer/chat -Name`
Expected: 上述 9 个文件（不含 `.test.ts`）。

**Step 3: 提交**

```bash
git add GUI/src/renderer/chat
git commit -m "feat(gui): copy chat module"
```

---

### Task 4: 拷贝 sidebar/ 模块

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/sidebar/{index.html,sidebar.css,sidebar.ts}` → `GUI/src/renderer/sidebar/`

**Step 1: 拷贝**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/renderer/sidebar | Out-Null
$src = "example/Cyrene-Agent-master/src/renderer/sidebar"
$dst = "GUI/src/renderer/sidebar"
Copy-Item "$src/index.html" $dst
Copy-Item "$src/sidebar.css" $dst
Copy-Item "$src/sidebar.ts" $dst
```

**Step 2: 提交**

```bash
git add GUI/src/renderer/sidebar
git commit -m "feat(gui): copy sidebar module"
```

---

### Task 5: 拷贝 settings/ 模块

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/settings/{index.html,settings.css,settings.ts,appearance-settings-state.ts}` → `GUI/src/renderer/settings/`

**Step 1: 拷贝**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/renderer/settings | Out-Null
$src = "example/Cyrene-Agent-master/src/renderer/settings"
$dst = "GUI/src/renderer/settings"
Copy-Item "$src/index.html" $dst
Copy-Item "$src/settings.css" $dst
Copy-Item "$src/settings.ts" $dst
Copy-Item "$src/appearance-settings-state.ts" $dst
```

> 不拷 `music-playback.ts`、`music-view-state.ts`（音乐功能，Aliya 无对应）。

**Step 2: 验证**

Run: `Get-ChildItem GUI/src/renderer/settings -Name`
Expected: 4 个文件。

**Step 3: 提交**

```bash
git add GUI/src/renderer/settings
git commit -m "feat(gui): copy settings module"
```

---

### Task 6: 拷贝 live2d/ 模块

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/live2d/` → `GUI/src/renderer/live2d/`（全部源码，跳过 *.test.ts）

**Step 1: 拷贝**

```powershell
New-Item -ItemType Directory -Force -Path GUI/src/renderer/live2d | Out-Null
Copy-Item "example/Cyrene-Agent-master/src/renderer/live2d/*.ts" "GUI/src/renderer/live2d/" -Exclude "*.test.ts"
```

**Step 2: 验证**

Run: `Get-ChildItem GUI/src/renderer/live2d -Name`
Expected: manager.ts / interaction.ts / focus.ts / expression-reset.ts / mouth-sync.ts / speaking-motion.ts / click-through.ts / opener-bubble.ts / lifecycle-diagnostics.ts（9 个源码）。

**Step 3: 提交**

```bash
git add GUI/src/renderer/live2d
git commit -m "feat(gui): copy live2d module"
```

---

### Task 7: 拷贝桌宠入口与公共资源

**Files:**
- Copy: `example/Cyrene-Agent-master/src/renderer/{index.html,main.ts,global.d.ts}` → `GUI/src/renderer/`
- Copy: `example/Cyrene-Agent-master/src/renderer/public/` → `GUI/src/renderer/public/`（models/stickers/avatars/icons/audio/live2dcubismcore.min.js 全量）

**Step 1: 拷贝入口文件**

```powershell
Copy-Item "example/Cyrene-Agent-master/src/renderer/index.html" "GUI/src/renderer/index.html"
Copy-Item "example/Cyrene-Agent-master/src/renderer/main.ts" "GUI/src/renderer/main.ts"
Copy-Item "example/Cyrene-Agent-master/src/renderer/global.d.ts" "GUI/src/renderer/global.d.ts"
```

**Step 2: 拷贝 public 资源（全量）**

```powershell
Copy-Item -Recurse "example/Cyrene-Agent-master/src/renderer/public" "GUI/src/renderer/public"
```

**Step 3: 验证**

Run: `Test-Path GUI/src/renderer/public/live2dcubismcore.min.js; Test-Path GUI/src/renderer/public/models/cyrene/Cyrene.model3.json`
Expected: 两个 `True`。

> 阿库露模型替换在 P4 完成；此阶段保留 Cyrene 昔涟资源作为占位，保证桌宠可加载。

**Step 4: 提交**

```bash
git add GUI/src/renderer
git commit -m "feat(gui): copy pet entry + public assets"
```

---

### Task 8: 渲染层构建盘点（记录错误清单）

**Files:**
- Create: `GUI/BUILD_GAPS.md`（记录待 P2/P3 消解的错误清单）

**Step 1: 运行 vite build 暴露缺失依赖**

Run: `cd GUI && npm run build:renderer`
Expected: 构建**失败**或部分失败——这是预期行为。错误主要是：
1. `window.chat / window.agui / window.chatStore` 等 preload 桥未暴露（P2 解决）
2. `settings/index.html` 引用 music/sticker 面板 JS（P3 裁剪解决）
3. `chat/main.ts` 引用的 `document-processing` 内部接口与后端不一致（P3 适配）

**Step 2: 记录错误清单**

将第一步的完整错误输出整理进 `GUI/BUILD_GAPS.md`，按文件分组，标注每类错误的消解阶段（P2/P3）。

**Step 3: 提交**

```bash
git add GUI/BUILD_GAPS.md
git commit -m "chore(gui): record renderer build gaps"
```

---

## 完成标准

- [ ] `GUI/src/shared/` 13 个文件到位
- [ ] `GUI/src/renderer/ui/` 5 个文件到位
- [ ] `GUI/src/renderer/chat/` 9 个文件到位
- [ ] `GUI/src/renderer/sidebar/` 3 个文件到位
- [ ] `GUI/src/renderer/settings/` 4 个文件到位
- [ ] `GUI/src/renderer/live2d/` 9 个文件到位
- [ ] 桌宠入口 + public 资源（含 live2dcubismcore.min.js）到位
- [ ] `BUILD_GAPS.md` 错误清单已记录
- [ ] 各 Task 均有独立 commit
