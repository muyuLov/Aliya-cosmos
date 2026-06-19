# 悬浮窗 GUI 优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 NagaAgent 悬浮窗 GUI 适配到 Aliya WebSocket 后端，精简代码，优化体验。

**Architecture:** WebSocket 替代 REST API，session.ts/config.ts 大幅精简，FloatingView.vue 去掉 Aliya 不支持的功能，保留截图。

**Tech Stack:** Vue 3 + TypeScript + Electron + WebSocket

---

### Task 1: 创建 WebSocket 客户端

**Files:**
- Create: `src/utils/websocket.ts`

```typescript
// WebSocket 连接管理器
// 负责连接/重连/心跳/消息收发
```

- [ ] **Step 1: Write `src/utils/websocket.ts`**

```typescript
import { ref } from 'vue'

export type WSMessage =
  | { type: 'brain_start'; user_input: string }
  | { type: 'brain_progress'; step: string; detail: string }
  | { type: 'brain_complete'; reply: string; tool_calls: Array<{ tool_name: string; arguments: any }> }
  | { type: 'brain_refine'; reply: string }
  | { type: 'brain_error'; code: string; step: string; message: string }
  | { type: 'tool_start'; tool: string; arguments: any }
  | { type: 'tool_complete'; tool: string; status: string; result?: any; error?: string; error_code?: string }
  | { type: 'tool_summary'; total: number; success: number; fail: number; errors?: string[] }
  | { type: 'pong' }
  | { type: 'history_cleared'; message: string }
  | { type: 'confirm_required'; action: string; message: string }
  | { type: 'performance_stats'; metrics: any }

export type WSStatus = 'disconnected' | 'connecting' | 'connected'

const WS_URL = 'ws://127.0.0.1:8765/agent/ws'
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000, 30000]

export const wsStatus = ref<WSStatus>('disconnected')

let ws: WebSocket | null = null
let reconnectAttempt = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let pingTimer: ReturnType<typeof setInterval> | null = null
let messageHandler: ((msg: WSMessage) => void) | null = null

function startPing() {
  stopPing()
  pingTimer = setInterval(() => {
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }, 30000)
}

function stopPing() {
  if (pingTimer) {
    clearInterval(pingTimer)
    pingTimer = null
  }
}

export function connect() {
  if (ws?.readyState === WebSocket.OPEN || ws?.readyState === WebSocket.CONNECTING) return

  wsStatus.value = 'connecting'
  ws = new WebSocket(WS_URL)

  ws.onopen = () => {
    wsStatus.value = 'connected'
    reconnectAttempt = 0
    startPing()
  }

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data) as WSMessage
      messageHandler?.(msg)
    } catch {
      // ignore malformed messages
    }
  }

  ws.onclose = () => {
    wsStatus.value = 'disconnected'
    stopPing()
    scheduleReconnect()
  }

  ws.onerror = () => {
    ws?.close()
  }
}

function scheduleReconnect() {
  if (reconnectAttempt >= RECONNECT_DELAYS.length) return
  const delay = RECONNECT_DELAYS[reconnectAttempt]!
  reconnectAttempt++
  reconnectTimer = setTimeout(connect, delay)
}

export function disconnect() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  stopPing()
  reconnectAttempt = RECONNECT_DELAYS.length // 阻止重连
  ws?.close()
  ws = null
  wsStatus.value = 'disconnected'
}

export function sendMessage(text: string) {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'user_message', text }))
    return true
  }
  return false
}

export function sendStop() {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'stop' }))
  }
}

export function onMessage(handler: (msg: WSMessage) => void) {
  messageHandler = handler
}
```

- [ ] **Step 2: Verify file created**

### Task 2: 精简 session.ts

**Files:**
- Modify: `src/utils/session.ts`

从 585 行精简到约 30 行，只保留 MESSAGES + Message 类型 + formatRelativeTime。

- [ ] **Step 1: Rewrite `src/utils/session.ts`**

```typescript
import { ref } from 'vue'

export interface Message {
  role: 'system' | 'user' | 'assistant' | 'info'
  content: string
  reasoning?: string
  generating?: boolean
  status?: string
  sender?: string
}

export const MESSAGES = ref<Message[]>([])

export function formatRelativeTime(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return '刚刚'
  if (diffMin < 60) return `${diffMin}分钟前`
  const diffHour = Math.floor(diffMin / 60)
  if (diffHour < 24) return `${diffHour}小时前`
  const diffDay = Math.floor(diffHour / 24)
  if (diffDay < 7) return `${diffDay}天前`
  return d.toLocaleDateString()
}
```

- [ ] **Step 2: Verify the file**

### Task 3: 精简 config.ts

**Files:**
- Modify: `src/utils/config.ts`

从 ~400 行精简到约 40 行，只保留 CONFIG 引用 + /health 心跳检测。

- [ ] **Step 1: Rewrite `src/utils/config.ts`**

```typescript
import { ref } from 'vue'

export const CONFIG = ref({
  system: { ai_name: 'Aliya' },
  floating: { enabled: false },
})
export const backendConnected = ref(false)

let healthTimer: ReturnType<typeof setInterval> | null = null

async function checkHealth() {
  try {
    const res = await fetch('http://127.0.0.1:8765/health')
    backendConnected.value = res.ok
  } catch {
    backendConnected.value = false
  }
}

export function startHealthCheck() {
  checkHealth()
  healthTimer = setInterval(checkHealth, 10000)
}

export function stopHealthCheck() {
  if (healthTimer) {
    clearInterval(healthTimer)
    healthTimer = null
  }
}

startHealthCheck()
```

- [ ] **Step 2: Verify the file**

### Task 4: 重写 chat.ts（WebSocket 版）

**Files:**
- Modify: `src/utils/chat.ts`

从 REST + SSE 改为 WebSocket 协议，去掉与原 Naga 流式格式相关的所有逻辑。

- [ ] **Step 1: Rewrite `src/utils/chat.ts`**

```typescript
import { ref } from 'vue'
import { MESSAGES } from '@/utils/session'
import { connect, disconnect, onMessage, sendMessage as wsSend, sendStop, wsStatus } from '@/utils/websocket'
import type { WSMessage } from '@/utils/websocket'

export const isSending = ref(false)

// 自动连接
connect()

let currentAssistantMessage: { msg: typeof MESSAGES.value[0]; contentBuf: string } | null = null

// 处理 WebSocket 消息
onMessage((msg: WSMessage) => {
  switch (msg.type) {
    case 'brain_start':
      // 添加占位消息
      MESSAGES.value.push({ role: 'user', content: msg.user_input })
      MESSAGES.value.push({ role: 'assistant', content: '', reasoning: '', generating: true })
      currentAssistantMessage = { msg: MESSAGES.value[MESSAGES.value.length - 1]!, contentBuf: '' }
      isSending.value = true
      break

    case 'brain_progress':
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.status = msg.detail
      }
      break

    case 'brain_complete':
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.content = msg.reply
        currentAssistantMessage.contentBuf = msg.reply
        if (msg.tool_calls?.length) {
          currentAssistantMessage.msg.status = `准备执行 ${msg.tool_calls.length} 个工具...`
        } else {
          // 无工具调用，直接完成
          finishAssistant()
        }
      }
      break

    case 'brain_refine':
      // 工具执行后的最终回复，完成生成
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.content = msg.reply
        currentAssistantMessage.contentBuf = msg.reply
        finishAssistant()
      }
      break

    case 'tool_start':
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.status = `🔧 ${msg.tool}`
      }
      break

    case 'tool_complete': {
      if (currentAssistantMessage) {
        const icon = msg.status === 'success' ? '✅' : '❌'
        const detail = msg.status === 'success' ? '' : `: ${msg.error}`
        currentAssistantMessage.msg.status = `${icon} ${msg.tool}${detail}`
      }
      break
    }

    case 'tool_summary': {
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.status = msg.fail > 0
          ? `工具执行完成: ${msg.success}成功 ${msg.fail}失败`
          : undefined
      }
      break
    }

    case 'brain_error': {
      // 显示错误
      if (currentAssistantMessage) {
        currentAssistantMessage.msg.content = `错误: ${msg.message}`
      } else {
        MESSAGES.value.push({ role: 'assistant', content: `错误: ${msg.message}` })
      }
      finishAssistant()
      break
    }

    case 'pong':
      // 心跳响应，无需处理
      break
  }

  // 触发滚动事件
  window.dispatchEvent(new CustomEvent('token'))
})

function finishAssistant() {
  if (currentAssistantMessage) {
    const m = currentAssistantMessage.msg
    delete m.generating
    delete m.status
    if (!m.reasoning) delete m.reasoning
    currentAssistantMessage = null
  }
  isSending.value = false
}

export function chatStream(content: string, options?: { images?: string[] }) {
  if (wsStatus.value !== 'connected') {
    MESSAGES.value.push({ role: 'system', content: '未连接到后端，请检查服务是否启动' })
    return
  }

  const text = options?.images?.length ? `[截图x${options.images.length}] ${content}` : content
  wsSend(text)
}

export function stopGeneration() {
  sendStop()
  finishAssistant()
}
```

- [ ] **Step 2: Update FloatingView.vue to use new chat.ts API**

`FloatingView.vue` 中:
- `chatStream(content, { skill: skillName, images })` → `chatStream(content, { images })`（去掉 skill）
- `import { CONFIG } from '@/utils/config'` 保留
- `import { ... } from '@/utils/session'` 只保留 `MESSAGES`
- 去掉 `CURRENT_SESSION_ID`, `IS_TEMPORARY_SESSION`, `loadCurrentSession`, `newSession`, `newTemporarySession`, `switchSession`

### Task 5: 精简 electron.d.ts

**Files:**
- Modify: `src/electron.d.ts`

- [ ] **Step 1: Rewrite `src/electron.d.ts`**

```typescript
export type FloatingState = 'classic' | 'ball' | 'compact' | 'full'

export interface CaptureSource {
  id: string
  name: string
  thumbnail: string
  appIcon: string | null
}

export interface ElectronAPI {
  floating: {
    enter: () => Promise<void>
    exit: () => Promise<void>
    expand: (toFull?: boolean) => Promise<void>
    expandToFull: () => Promise<void>
    collapse: () => Promise<void>
    getState: () => Promise<FloatingState>
    pin: (value: boolean) => void
    fitHeight: (height: number) => void
    setPosition: (x: number, y: number) => void
    onStateChange: (callback: (state: FloatingState) => void) => () => void
    onWindowBlur: (callback: () => void) => () => void
  }
  capture: {
    getSources: () => Promise<CaptureSource[] | { permission: string }>
    captureWindow: (sourceId: string) => Promise<string | null>
    openScreenSettings: () => Promise<void>
  }
  showContextMenu: () => void
  platform: string
}

declare global {
  interface Window {
    electronAPI?: ElectronAPI
  }
}

export {}
```

### Task 6: 删除无用文件

- [ ] **Step 1: Delete directories and files**

```bash
rm -rf src/api
rm -f src/utils/encoding.ts
rm -f src/utils/qqNotification.ts
```

### Task 7: 重写 FloatingView.vue

**Files:**
- Modify: `src/views/FloatingView.vue`

去掉快捷技能、文件上传、会话历史面板、临时会话、新建对话等功能，保留截图。更新导入。

- [ ] **Step 1: Rewrite imports and remove unused state**

Remove from imports:
- `API from '@/api/core'`
- `CURRENT_SESSION_ID`, `formatRelativeTime`, `IS_TEMPORARY_SESSION`, `loadCurrentSession`, `newSession`, `newTemporarySession`, `switchSession` from session

Add import:
- `stopGeneration` from chat

Remove variables/functions:
- `sessionPanelRef`, `showHistory`, `sessions`, `loadingSessions`, `fetchSessions`, `_expandedForHistory`, `toggleHistory`, `closeHistory`, `handleSwitchSession`, `handleDeleteSession`
- `fileInputRef`, `suppressBlur`, `triggerFileUpload`, `handleFileUpload`
- `QUICK_SKILLS`, `activeSkillIndex`, `handleQuickSkill`
- `handleNewSession`, `handleNewTemporarySession`

Update `sendMessage`:
- Remove `skillName` logic, just call `chatStream(input.value, { images })`

Update `fitWindowHeight`:
- Remove `sessionH` calculation

Remove from template:
- Session history panel section
- File upload button and hidden input
- Quick skills row
- Temporary session and new session buttons
- The `.skill-tag`, `.session-panel`, `.session-list`, `.session-item`, `.temporary-tag` CSS classes

- [ ] **Step 2: Apply all changes to `FloatingView.vue`**

### Task 8: 最终清理

- [ ] **Step 1: Verify all imports resolve correctly**

```bash
grep -r "@/api" src/ && echo "仍有 api 引用" || echo "已无 api 引用"
grep -r "encoding" src/ && echo "仍有 encoding 引用" || echo "已无 encoding 引用"
grep -r "qqNotification" src/ && echo "仍有 qqNotification 引用" || echo "已无 qqNotification 引用"
```

- [ ] **Step 2: Clean up unused assets**

```bash
rm -f public/assets/box.9.png
rm -f public/assets/sunflower.9.png
```
