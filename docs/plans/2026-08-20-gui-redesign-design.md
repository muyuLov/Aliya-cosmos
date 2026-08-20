# Aliya-cosmos GUI 全新架构重设计

- 日期：2026-08-20
- 参考示例：
  - `example/claude-code`（终端式 Agent 客户端，React/TS，提供对话式交互与工具调用可视化思路）
  - `example/Cyrene-Agent-master`（Live2D 桌面 Agent，Electron + 原生 TS，本设计的架构与模块直接蓝本）
- 前置文档：
  - `GUI/README.md`（现有 Aliya 状态面板，Vue3 + Electron + Live2D）
  - `agent/ws.py` / `agent/events.py`（后端 `/agent/ws` 协议契约，GUI 通信基础）
- 目标范围：**全新 GUI 架构重设计**（非打补丁），覆盖窗口体系、状态管理、通信层、组件结构
- 技术栈：原生 TypeScript（无 Vue/React 框架）+ 原生 Electron + soullink-emotion SDK（Live2D）

---

## 0. 设计决策（已与用户确认）

| # | 决策项 | 结论 |
|---|--------|------|
| 1 | 计划范围 | 全新 GUI 架构重设计，不局限于增强现有面板 |
| 2 | 技术栈 | 原生 TS 无框架 + 原生 Electron（类 Cyrene），弃用 Vue/Pinia/Naive UI/Vite 多入口 |
| 3 | 窗口体系 | 单窗口整合：单一主窗口同时容纳聊天区 + Live2D 嵌入 + 状态栏 |
| 4 | UI 实现 | 聊天/状态 UI 用原生 DOM 手写组件类；Live2D 直接沿用 `example/Cyrene-Agent-master/src/renderer/live2d/` |
| 5 | Live2D 来源 | 直接沿用 Cyrene 的 live2d 模块（manager/interaction/mouth-sync/opener-bubble 等），非现有 Aliya 的 `src/live2d` |

---

## 1. 总体架构与目录结构

全新 GUI 以原生 TypeScript + 原生 Electron 重写。单一主窗口承载全部交互，内部以"原生 DOM 组件 + 事件总线"组织，Live2D 通过 soullink SDK 渲染进同一窗口的 `<canvas>`。

**进程边界（Electron 经典双层）：**
- **主进程（`src/main/`）**：窗口生命周期、系统托盘、配置定点读写、后端 WS 客户端（断线重连）、Token/状态聚合、IPC 桥。
- **渲染进程（`src/renderer/`）**：全部 UI 与 Live2D，纯原生 TS，经 `contextBridge` 受控访问主进程能力。

**目录结构（对标 Cyrene `src/{main,preload,renderer,shared}`）：**

```
GUI/
├── package.json              # tsc 构建 + electron 启动；保留 soullink SDK 依赖
├── tsconfig.json
├── index.html                # 单入口：#chat #live2d-canvas #status-bar 容器
├── src/
│   ├── main/                 # 主进程（原生 TS）
│   │   ├── index.ts          # 生命周期/组装
│   │   ├── windows.ts        # 单主窗口（无边框/透明/圆角）
│   │   ├── tray.ts           # 系统托盘（后台唯一入口）
│   │   ├── config.ts         # main.yml 定点读写
│   │   ├── ws.ts             # 后端 /agent/ws 客户端（自动重连）
│   │   ├── state.ts          # 共享状态 + 批量快照推送
│   │   ├── notifications.ts  # 桌面通知
│   │   └── ipc.ts            # IPC 通道
│   ├── preload/index.ts      # contextBridge 桥接
│   ├── renderer/             # 渲染进程（原生 TS，无框架）
│   │   ├── main.ts           # 入口装配
│   │   ├── bus.ts            # 轻量事件总线（替代 Pinia）
│   │   ├── components/       # 状态栏/设置等手写组件类
│   │   ├── chat/             # 直接沿用 Cyrene chat/（消息列表/输入区/分段/工具卡）
│   │   ├── live2d/           # 直接沿用 Cyrene live2d/（manager/interaction/mouth-sync...）
│   │   ├── styles/           # --rb-* 设计令牌 + 基础样式
│   │   └── types.ts          # 协议事件类型（对齐 events.py）
│   └── shared/               # 主/渲染共享类型与协议常量
```

**关键变更 vs 现有 Aliya GUI：**
- 删除 Vue/SFC/Pinia/Vite 多入口，改为 `tsc` 单产物。
- Live2D 从"独立窗口"改为"同一窗口 canvas 嵌入"（契合单窗口整合）；chat 模块从零补建（现有 Aliya 仅有聊天入口按钮，无对话界面）。
- `shared/` 放协议常量（事件名字符串枚举），主/渲染进程 import 同一份，避免与 `agent/events.py` 失同步。

---

## 2. 主进程职责与后端通信层

主进程是 GUI 与后端 Agent 的唯一桥梁，复用现有 Aliya `main/` 的成熟模块，因单窗口做精简。

**2.1 窗口（`windows.ts`）**
- 单 `BrowserWindow`：无边框、透明、`roundedCorners: true`、加载 `index.html`。
- 不再创建独立 Live2D 窗口与设置窗口——设置改为渲染层内浮层（`components/Settings.ts`），Live2D 为同窗口 canvas。
- 关闭行为：拦截 `close` → 隐藏到托盘（`isQuitting` 放行真正退出），与现有语义一致。

**2.2 后端通信（`ws.ts`）**
- 连接 `ws://<host>:<port>/agent/ws`（host/port 从 `main.yml` 读取），断线 5s 指数退避重连。
- 收：解析协议事件（`run_started` / `text_message_content` / `tool_call_*` / `run_finished` / `token_usage` / `status_changed` / `emotion_changed` / `tts_features` / `confirm_request`），按类型分流。
- 发：渲染层经 IPC 转发 `user_message` / `stop` / `confirm_response` / `ping` / `get_token_usage`。
- `tts_features` 与 `emotion_changed` 直接推给 Live2D（经 `state.ts` 总线），不绕渲染层业务组件。

**2.3 状态聚合（`state.ts`）**
- 集中持有：连接状态、Token 累计、当前模型、情绪、置顶/缩放。
- 50ms 节流合并为单通道快照（`app:state-snapshot`）推渲染层，降低唤醒频率（沿用现有策略）。

**2.4 配置（`config.ts`）**
- 读：按 key 行匹配标量解析（保留尾注）；写：定点行替换，避免 YAML 整体重写破坏注释。沿用现有实现。

**2.5 通知（`notifications.ts`）**
- 仅当主窗口隐藏（`isVisible()===false`，单窗口下即"所有界面不可见"）时，`run_finished` 触发 Windows 通知，点击 `show()` 主窗口。

**2.6 IPC（`ipc.ts` + `preload`）**
- `contextBridge` 暴露：`sendUserMessage`、`sendStop`、`sendConfirm`、`getStateSnapshot`、`updateConfig`、`onStateSnapshot`（订阅）。

---

## 3. 渲染层组件结构与事件总线

渲染进程纯原生 TS，无框架。用一个极简事件总线（`bus.ts`）替代 Pinia 作为单一数据源，所有组件订阅总线、各自渲染负责的 DOM 片段。

**3.1 事件总线（`bus.ts`）**
- 发布/订阅式：`bus.on(event, handler)` / `bus.emit(event, payload)`。
- 事件源自两类：①主进程 `app:state-snapshot`（连接/Token/模型/情绪）→ 总线内部转译为语义事件；②后端协议事件经主进程透传 → `bus.emit('text_delta', ...)` 等。
- 不引入响应式代理，组件在 handler 内直接操作 DOM（对标 Cyrene `chat/main.ts`）。

**3.2 组件划分（`renderer/components/`）**
- `StatusBar.ts`：顶栏（置顶/最小化/关闭）+ 头像在线徽章（WS 状态驱动）+ 心情/状态卡 + Token 统计 + 设置入口按钮。对应现有 `TopBar/AvatarCard/IndicatorCard/TokenFooter`。
- `Settings.ts`：浮层（单例），身份编辑 + 提供商切换（调 `updateConfig`）+ 服务状态展示。对应现有 `SettingsPanel`。
- `ModelBadge.ts`：当前模型展示 + 切换浮层。

**3.3 聊天模块（`renderer/chat/`，沿用 Cyrene）**
- `main.ts`：聊天容器装配，订阅 `run_started`/`text_message_start`/`text_message_content`/`text_message_end` 维护消息列表。
- 消息列表：用户消息 + Agent 消息气泡；Agent 文本按 `text_message_content` 增量追加。
- 输入区：文本框 + 发送（发 `user_message`）/ 停止（发 `stop`）。
- 工具调用可视化：订阅 `tool_call_start/result/end`，渲染工具名 + 参数 + 结果折叠块（类 claude-code 的 tool use 卡片）。
- 确认请求：`confirm_request` → 渲染允许/拒绝按钮，发 `confirm_response`。

**3.4 Live2D（`renderer/live2d/`，直接沿用 Cyrene）**
- `manager.ts` 初始化 soullink `Live2DRenderer` 进 `#live2d-canvas`。
- `mouth-sync.ts` 订阅总线 `tts_features` 驱动口型；`interaction.ts` 处理鼠标注视；`opener-bubble.ts` 在说话时冒气泡；`emotion_changed` → 表情调制。

**3.5 装配（`renderer/main.ts`）**
- 创建各组件实例 → 绑定到 `index.html` 的 `#chat #live2d-canvas #status-bar` 容器 → 建立 bus 与 IPC 的桥接（接收 `onStateSnapshot`，emit 用户操作）。

**关键取舍**：组件以"类 + 挂载方法"组织（如 `class StatusBar { mount(el) }`），无虚拟 DOM；状态变更即局部 DOM 更新。相比 Vue 更冗长，但零框架依赖、构建链路极简。

---

## 4. 数据流时序、错误处理与测试策略

**4.1 核心数据流时序（一次对话）**
```
用户输入 → Chat输入区.emit('user_message')
  → IPC → 主进程 ws.send({type:'user_message', text})
  → 后端 Agent 运行，事件流回推
  → ws.ts 解析 → state.ts 分流
      ├─ run_started/text_*/run_finished → 50ms 节流快照 或 直推 bus
      ├─ token_usage → state 累加 → 快照推送
      ├─ tts_features → bus.emit('tts_features') → live2d/mouth-sync
      ├─ emotion_changed → bus.emit('emotion') → live2d/表情
      └─ confirm_request → bus.emit('confirm') → Chat 渲染确认条
  → 渲染层各组件按订阅增量更新 DOM
```
单窗口下，Live2D 与聊天共享同一渲染进程，无需跨窗口 IPC，延迟更低。

**4.2 错误处理**
- **WS 断线**：`ws.ts` 自动重连（指数退避，上限 30s）；`status_changed` 推送 `disconnected` → StatusBar 头像徽章转灰 + Toast 提示。
- **后端 ERROR/NOTICE**：`bus.emit('notice'/'error')` → 聊天区插入系统消息条，不阻塞界面。
- **配置写失败**：`config.ts` 定点写入前先写临时文件再替换，失败时回滚并通知。
- **Live2D 初始化失败**：catch 后隐藏 canvas 区域，聊天/状态栏仍可用（降级而非崩溃）。
- 不为主进程崩溃做兜底（YAGNI），仅保证渲染层局部错误不扩散（每组件 try/catch 包裹挂载）。

**4.3 测试策略（对标 Cyrene 自带 `.test.ts`）**
- 构建链：`tsc --noEmit` 类型检查 + `vitest` 单测。
- 纯逻辑单测：`bus.ts`（订阅/emit/去重）、`chat/message-segmentation.ts`（增量文本分段）、`config.ts`（定点写入不破坏注释）、`state.ts`（快照聚合节流）。
- 组件轻量测试：用 `happy-dom` 挂载组件类，断言 DOM 结构（如 StatusBar 在 disconnected 时徽章 class 变化）。
- 集成：保留 `npm start` 手动启动验证；暂不做 E2E（YAGNI，除非后续需要）。

**4.4 构建与迁移**
- 删除 Vite 多入口 + Vue 依赖；`package.json` 改为 `tsc` 编译 `src/**` 到 `dist/`，`electron .` 加载 `dist/index.html`。
- `shared/` 放协议常量（事件名字符串枚举），主/渲染进程 import 同一份，避免与 `events.py` 失同步。
- 视觉令牌 `--rb-*` 从现有 `styles/tokens.css` 直接沿用。

---

## 5. 成功标准（验收清单）

1. **单窗口整合**：启动后单一主窗口同时显示聊天区、Live2D 角色、状态栏，无独立 Live2D/设置窗口。
2. **对话闭环**：输入文本 → 流式显示 Agent 回复（增量追加）→ 显示 Token 用量增长。
3. **工具可视化**：后端 `tool_call_*` 事件在聊天区渲染为工具名 + 参数 + 结果折叠块。
4. **确认交互**：`confirm_request` 弹出允许/拒绝按钮，选择后正确发 `confirm_response`。
5. **Live2D 驱动**：`tts_features` 驱动口型、`emotion_changed` 调制表情，沿用 Cyrene live2d 模块。
6. **断线恢复**：手动断开后端 → 头像徽章转灰 + 提示；恢复后自动重连并继续对话。
7. **配置生效**：设置浮层修改身份/提供商 → `main.yml` 定点写入不破坏注释，重启生效。
8. **零框架依赖**：`package.json` 无 Vue/React/Naive UI/Vite，仅 soullink SDK + electron + tsc 工具链。
9. **类型与单测**：`tsc --noEmit` 通过；`bus`/`config`/`state`/`message-segmentation` 单测通过。

---

## 6. 实施阶段建议（后续 writing-plans 拆解）

- **Phase 0 脚手架**：`tsconfig.json` + `package.json` 改造 + `index.html` + `shared/` 协议常量。
- **Phase 1 主进程**：搬运并精简现有 `main/`（ws/state/config/ipc/tray/notifications/windows）。
- **Phase 2 渲染基座**：`bus.ts` + `preload` + `main.ts` 装配骨架 + `styles/--rb-*`。
- **Phase 3 聊天模块**：沿用 Cyrene `chat/` 并适配协议事件。
- **Phase 4 状态栏/设置**：`StatusBar.ts` / `Settings.ts` / `ModelBadge.ts` 原生组件。
- **Phase 5 Live2D**：搬运 Cyrene `live2d/` 接入 `#live2d-canvas` 与总线。
- **Phase 6 测试与打磨**：补充单测、降级处理、手动联调。
