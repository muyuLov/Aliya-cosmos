# 悬浮窗 GUI 优化设计

日期：2026-06-07
项目：Aliya-cosmos

## 一、目标

将 NagaAgent 悬浮窗 GUI 适配到 Aliya 后端（WebSocket 协议），同时精简代码、优化视觉体验。

## 二、功能范围

### 保留的功能
- 悬浮窗三种形态：球态（Ball）、紧凑态（Compact）、完整态（Full）
- 消息收发（通过 WebSocket）
- 窗口截图（Electron desktopCapturer）
- 悬浮球拖拽移动
- 窗口固定（Pin）与退出悬浮窗
- AI 回复展示（Markdown 渲染）
- 思考过程中状态提示

### 去除的功能（Aliya 后端不支持的）
- 快捷技能（翻译/概括/真假鉴别/帮我想想）
- 文件上传与解析
- 会话历史面板（Aliya 自动管理历史，无需前端 session CRUD）
- 流式 token 逐字输出（Aliya 协议整条返回）

## 三、架构

### 整体数据流

```
FloatingView.vue
  → chat.ts (编排消息收发)
    → websocket.ts (WebSocket 连接管理)
      → Aliya Backend ws://127.0.0.1:8765/agent/ws
```

### 模块职责

| 模块 | 职责 |
|------|------|
| `websocket.ts` | WebSocket 连接/重连、发送消息、接收消息分发 |
| `chat.ts` | 编排消息生命周期：用户输入 → WS 发送 → 接收 → 更新 UI |
| `session.ts` | 仅保留 MESSAGES ref + Message 类型 + formatRelativeTime |
| `config.ts` | 精简版，仅保留 CONFIG 引用 + /health 心跳 |
| `FloatingView.vue` | 三态 UI，移除非功能对应的按钮/面板 |
| `MessageItem.vue` | 消息气泡（无变化） |
| `Markdown.vue` | Markdown 渲染（无变化） |

## 四、WebSocket 协议映射

### 发送

| 前端事件 | 发送的消息 |
|----------|-----------|
| 发送消息 | `{"type": "user_message", "text": "..."}` |
| 停止生成 | `{"type": "stop"}` |
| 心跳 | `{"type": "ping"}` |

### 接收处理

| 后端消息 | 前端处理 |
|----------|---------|
| `brain_start` | 添加 assistant 占位消息，"思考中" 状态 |
| `brain_progress` | 更新状态文本 |
| `brain_complete` | 设置回复内容，清除生成状态 |
| `brain_refine` | 替换/更新回复内容（工具反馈后优化） |
| `tool_start` | 追加工具执行状态文本 |
| `tool_complete` | 更新工具结果文本 |
| `tool_summary` | 工具执行汇总信息 |
| `brain_error` | 显示错误信息，清除生成状态 |
| `pong` | 忽略（连接保活用） |

## 五、文件变更清单

### 删除
- `src/api/` 整个目录（不再使用 REST API）
- `src/utils/encoding.ts`（流式解码不再需要）
- `src/utils/qqNotification.ts`（不再需要）
- `src/composables/useToolStatus.ts`（重写为只有 toolMessage ref）

### 新增
- `src/utils/websocket.ts` — WebSocket 客户端

### 修改（精简）
- `src/utils/chat.ts` — 重写为 WS 版
- `src/utils/session.ts` — 精简到 MESSAGES + Message 类型
- `src/utils/config.ts` — 精简到仅心跳检测
- `src/views/FloatingView.vue` — 移除快捷技能、文件上传、会话历史面板，保留截图
- `src/electron.d.ts` — 仅保留悬浮窗所需类型

## 六、视觉调整

- 紧凑态/完整态：去掉快捷技能标签行
- 完整态：去掉会话历史面板按钮
- 完整态：去掉文件上传按钮
- 工具执行状态：改为简洁文本提示（无可折叠详情）
- 回复渲染：一次性显示（无流式逐字效果）
- 整体配色保持现有暗色系不变
