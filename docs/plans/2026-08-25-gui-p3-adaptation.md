# GUI P3 适配打磨 Implementation Plan

> **提交策略：** 本计划中所有 `git commit` 步骤均**跳过**（用户要求：不提交 git、不推送 GitHub）。任务完成标准不变。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在拷贝的 Cyrene 渲染层上完成 Aliya 化适配：文案替换（昔涟→Aliya）、聊天窗口下拉框取舍、设置面板裁剪为 Aliya 可配置项、侧栏状态由 Aliya 事件驱动、渲染层 typecheck 归零、双主题验证。

**Architecture:** 全部改动在 `GUI/src/renderer/` 与 `GUI/src/shared/` 的已拷贝文件内做**外科手术式小改**，不重构。裁剪策略：隐藏导航入口 + 保留 DOM（避免 settings.ts 大量改判），JS 侧对已裁剪面板的事件绑定做短路。

**Tech Stack:** 原生 TS/DOM/CSS，无新依赖。

**前置条件:** P2 完成（preload 桥 + IPC 已通）；`GUI/BUILD_GAPS.md` 清单为本阶段输入。

---

### Task 1: 聊天窗口文案与下拉框取舍

**Files:**
- Modify: `GUI/src/renderer/chat/index.html`
- Modify: `GUI/src/renderer/chat/main.ts`

**Step 1: index.html 文案替换**

将 `chat/index.html` 中：
- `<title>昔涟 · 聊天</title>` → `<title>Aliya · 聊天</title>`
- `class="chat__name">昔涟</span>` → `class="chat__name">Aliya</span>`
- 空态 `昔涟期待与你聊天哦 ✨` → `Aliya 期待与你聊天哦 ✨`
- 快捷预设文案如含"昔涟"一并替换

**Step 2: 模式下拉行为（渲染层过滤）**

在 `chat/main.ts` 的 AGUI 事件渲染入口处，按当前模式过滤 `tool_call_*` 事件。找到渲染工具步骤的分支（P2 Task 1 调研定位，通常 `case "TOOL_CALL_START":`），在其外层加模式判断：

```ts
// chat/main.ts（在 AGUI 事件 switch 前）
const chatMode = document.documentElement.dataset.chatMode ?? "collab"; // collab | talk

// 在 TOOL_CALL_START 等分支内：
if (chatMode === "talk" && event.type.startsWith("TOOL_CALL")) return; // 日常聊天隐藏工具过程
```

**Step 3: 风格/推理下拉标注占位**

- 风格下拉选项保留（5 个人格），但点击后仅更新 UI 选中态 + 写入 `window.localStorage["aliya.chat.style"]`，不发送给后端。
- 推理下拉：P2 调研后若渲染层动态请求 `window.chat.getReasoningState`，则在 preload 补 `getReasoningState`/`setReasoning` 空实现（返回 `{ available: false }`），避免报错。

**Step 4: 提交**

```bash
git add GUI/src/renderer/chat
git commit -m "feat(gui): chat window Aliya copy + mode filter"
```

---

### Task 2: 侧栏文案、头像与情绪驱动

**Files:**
- Modify: `GUI/src/renderer/sidebar/index.html`
- Modify: `GUI/src/renderer/sidebar/sidebar.ts`
- Add: `GUI/src/renderer/sidebar/aliya-avatar.png`（用 `data/liv2d` 相关素材或简单占位头像）

**Step 1: index.html 文案与头像**

- `<title>昔涟 · 状态</title>` → `<title>Aliya · 状态</title>`
- `sidebar__name">昔涟` → `sidebar__name">Aliya`
- `profile__avatar` 的 `src` 改为 `/avatars/aliya-avatar.png`
- `sidebar__version` 文案改 `Aliya v0.1.0`
- 移除"语音通话"按钮（`id="call-btn"`）及其 JS 绑定引用（Aliya 无通话）

**Step 2: 情绪状态驱动**

在 `sidebar.ts` 中，订阅 WS 情绪事件（通过 `window.agui.onEvent` 的 `EMOTION_CHANGED`）更新状态/心情指示器。若 P2 已实现 `state:changed` 广播，也可通过 preload 桥 `window.runtimeState` 订阅：

```ts
// sidebar.ts（追加）
const EMOTION_LABEL: Record<string, string> = {
  joy: "开心", happy: "开心", excited: "兴奋", neutral: "平静",
  sad: "难过", angry: "生气", anxious: "焦虑", tired: "疲惫",
  // 其余按 Aliya 情绪引擎 18 标签补齐
};

function applyEmotion(dominant: string): void {
  const labelEl = document.getElementById("feeling-label");
  const emojiEl = document.getElementById("feeling-emoji");
  if (labelEl) labelEl.textContent = EMOTION_LABEL[dominant] ?? dominant;
  if (emojiEl) emojiEl.textContent = dominant === "neutral" ? "😐" : "😊";
}
```

连接状态：订阅 `window.agui.onEvent` 或主进程 `connection:changed`，更新 `#online-status-label`（在线/离线）。

**Step 3: 提交**

```bash
git add GUI/src/renderer/sidebar
git commit -m "feat(gui): sidebar Aliya branding + emotion drive"
```

---

### Task 3: 设置面板裁剪

**Files:**
- Modify: `GUI/src/renderer/settings/index.html`
- Modify: `GUI/src/renderer/settings/settings.ts`

**Step 1: 隐藏裁剪导航项**

在 `settings/index.html` 的导航栏中，为以下 `nav-item` 加 `hidden` 属性（保留 DOM 避免 JS 大面积改判）：
`memory / chat / user / tasks / identity / skills / plugins / preferences / channels / asr / music`

保留：`general / api / cyrene / tts / tokens / appearance / disclaimer`。

**Step 2: 面板标题与文案 Aliya 化**

- `昔涟设置` → `Aliya 设置`；面板内 `RAG / 文档导入`、`表情包发送` 等 Aliya 无对应子项加 `hidden` 或替换为后端能力说明。
- `免责声明` 面板文案替换为 Aliya 项目说明。
- Token 面板数据源：若 `settings.ts` 调 `window.tokenUsage.get`，preload 补空实现（P2 已含 `TOKEN_USAGE_GET` 通道）。

**Step 3: 处理 settings.ts 的裁剪面板引用**

用 P2 调研结果定位 `settings.ts` 中对已裁剪面板的初始化调用（如 `initChannelsPanel()`、`initAsrPanel()`），改为短路：

```ts
// settings.ts 中每个已裁剪面板的初始化点
function initAsrPanel(): void { /* Aliya 无 ASR，留空 */ }
```

若存在 `.querySelector("#asr-panel")` 等可能为 null 的访问，加可选链/判空。

**Step 4: typecheck + 提交**

Run: `cd GUI && npm run typecheck`
Expected: 渲染层错误显著减少；剩余错误记录到 `BUILD_GAPS.md`。

```bash
git add GUI/src/renderer/settings
git commit -m "feat(gui): settings panel trim + Aliya copy"
```

---

### Task 4: 聊天运行链路适配

**Files:**
- Modify: `GUI/src/renderer/chat/main.ts`
- Modify: `GUI/src/renderer/chat/index.html`（如附件/贴纸按钮需禁用）

**Step 1: 禁用贴纸/文档摄入入口**

Aliya 后端无贴纸语义匹配与 RAG 摄入。将 `chat/index.html` 中 `#sticker-picker-btn`、`#attach-btn` 加 `hidden`（或保留按钮但点击提示"功能暂未接入"）。`chat/main.ts` 中对 `window.chat.ingestDroppedFiles` 等的调用点做判空短路。

**Step 2: 确认 sendMessage / AGUI 链路**

对照 P2 的 preload 桥：`window.chat.sendMessage` 已存在；确认 `chat/main.ts` 发送路径最终走 `window.agui.run` 或 `window.chat.sendMessage` 其一即可（P2 两者都实现了转发），避免双发——保留渲染层实际调用那条，另一条在 preload 置空实现。

**Step 3: 工具/确认卡片保留**

确认卡片（`CONFIRM_REQUEST`）渲染逻辑保留；主进程已实现 `confirm_response` 转发（P2）。若有 `window.choice.resolve` 调用，preload 补 `choice` 桥。

**Step 4: typecheck + 提交**

Run: `cd GUI && npm run typecheck`
Expected: 聊天模块错误清零或仅剩 0-2 个可解释项（记入 BUILD_GAPS）。

```bash
git add GUI/src/renderer/chat GUI/src/preload
git commit -m "feat(gui): chat runtime adaptation"
```

---

### Task 5: 渲染层 typecheck 归零与双主题验证

**Files:**
- Modify: `GUI/src/renderer/chat/main.ts`、`GUI/src/renderer/settings/settings.ts` 等（按 BUILD_GAPS 逐项消解）
- Modify: `GUI/src/main/ipc.ts`、`GUI/src/preload/index.ts`（补齐渲染层实际调用的桥方法）

**Step 1: 按 BUILD_GAPS 清单逐项消解**

对清单中每条错误，选择：
- 渲染层可改 → 最小修改；
- preload/主进程缺桥 → 补实现；
- 确实无对应功能 → 在渲染层加判空/短路。

Run: `cd GUI && npm run typecheck`
Expected: **0 错误**。

**Step 2: 双主题验证**

- 启动后在设置 → 外观设置切换 `classic` / `pearl-white`，确认聊天/侧栏/设置三窗口背景、文字、气泡同步切换。
- `theme.ts` 的 `applyTheme` 已通过 `data-ui-theme` 生效；若设置面板主题切换写 `saveGeneral`，确认主进程写入 preferences 并广播 `UI_THEME_CHANGED`。

**Step 3: 提交**

```bash
git add GUI/src
git commit -m "feat(gui): zero typecheck + theme verification"
```

---

## 完成标准

- [ ] 聊天窗口文案 Aliya 化，模式过滤生效，风格/推理下拉不报错
- [ ] 侧栏情绪状态由 Aliya 事件驱动，在线状态随连接变化
- [ ] 设置面板只显示保留项（general/api/cyrene/tts/tokens/appearance/disclaimer）
- [ ] `npm run typecheck` 0 错误
- [ ] 双主题切换在 4 窗口生效
- [ ] 各 Task 均有独立 commit
