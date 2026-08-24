# Aliya-cosmos GUI 复刻 Cyrene-Agent UI 设计

- 日期：2026-08-25
- 参考示例：`example/Cyrene-Agent-master`（Electron + 原生 TS 桌面 Live2D Agent）
- 前置事实：
  - 当前工作区无 `GUI/` 目录（旧 GUI 已删，`tsconfig.json` 残留引用 `GUI` 路径）
  - 后端 WS 网关已恢复：`agent/ws.py`（`/agent/ws` 端点）+ `agent/events.py`（AG-UI 风格线上协议）
  - `data/liv2d/` 已有阿库露 Live2D 模型（moc3 + 贴图 + 8 表情 + 1 动作，VTS 导出，model3.json 未引用 Expressions/Motions）
  - 后端 WS 端口默认 `8765`（CLAUDE.md `WS_PORT`），GUI 默认连 `127.0.0.1:8765`
  - 后端无 ASR、无贴纸语义匹配、无 todo 工具（通话/任务/贴纸窗口本次不实现）

## 0. 已确认决策

| # | 决策项 | 结论 |
|---|--------|------|
| 1 | 窗口范围 | 4 窗口：聊天 / 状态侧栏 / 设置 / Live2D 桌宠（通话/任务/贴纸后补） |
| 2 | 复用策略 | 渲染层全复用 Cyrene 代码；主进程重写为 Aliya WS 客户端 |
| 3 | 功能深度 | 只做有后端支撑的窗口；设置/聊天中无后端能力的面板保留 UI 但标注后续 |
| 4 | 角色 | 阿库露模型 + Aliya 名字（UI 文案同步改） |
| 5 | 主题 | 双主题完整复刻（深色粉紫玻璃拟态默认 + pearl-white 浅色可切换） |
| 6 | 工程布局 | `GUI/` 子目录 + Vite（渲染层多入口）+ tsc（主进程/preload） |
| 7 | 后端启动 | 默认自动拉起 `python main.py`（可配置为手动启动模式） |
| 8 | WS 默认地址 | `127.0.0.1:8765`（与后端 `WS_PORT` 默认一致） |

## 1. 总体架构与目录结构

```
GUI/
├── package.json                # electron + vite + tsc + vitest
├── vite.config.ts              # 多入口：chat / sidebar / settings / index(桌宠)
├── tsconfig.*.json             # main / preload / renderer 三套
├── src/
│   ├── main/                   # 【重写】Electron 主进程
│   │   ├── index.ts            # 生命周期 + 组装 + 托盘
│   │   ├── backend.ts          # 【新】Python 后端子进程管理（自动拉起/手动）
│   │   ├── windows.ts          # 4 个 BrowserWindow
│   │   ├── ws.ts               # 【重写】Aliya /agent/ws 客户端（重连+协议映射）
│   │   ├── config.ts           # 【新】读写 data/config/*.yml + *.json
│   │   ├── state.ts            # 状态聚合（emotion/token/connection/sessions）
│   │   ├── ipc.ts              # IPC handler 注册（按 Cyrene 契约）
│   │   └── tray.ts             # 系统托盘
│   ├── preload/index.ts        # 【适配】contextBridge 桥
│   ├── renderer/               # 【全复用 Cyrene】chat/ sidebar/ settings/ live2d/ ui/
│   └── shared/                 # ipc-channels / ui-theme / 协议常量（复用+裁剪）
└── assets/                     # 阿库露模型 + Aliya 头像
```

要点：
- 目录结构对标 Cyrene；渲染层文件直接拷贝，改动集中在 `src/main/` 与 `src/shared/`。
- `shared/` 从 Cyrene 21 个文件裁剪为与本 4 窗口相关通道（移除 music/sticker/tasks/call），保留 `AGUI_EVENT` 事件流契约。

## 2. 主进程（后端管理 + WS 适配）

### 2.1 backend.ts（新）
- 默认"自动拉起"：`spawn(python, [main.py])`，cwd=项目根，复用当前 `.venv` 环境；监控 stdout 与端口轮询判断就绪（3s 超时）；GUI 退出时 `terminate()`。
- 配置项 `autoLaunchBackend`（GUI 偏好，存 userData）：`true` 自动拉起 / `false` 仅连接。设置窗口可切换。

### 2.2 ws.ts（重写）
- 连接 `ws://127.0.0.1:8765/agent/ws`（读环境变量/配置覆盖）；指数退避自动重连；连接状态广播到 state。
- **事件流映射**（核心）：Aliya 线上协议为 AG-UI 风格，与 Cyrene 渲染层期望同构，做薄 normalize：
  - 后端 `run_started / text_message_start / text_message_content / text_message_end / tool_call_start / tool_call_result / tool_call_end / run_finished / confirm_request / error / notice / token_usage / emotion_changed / tts_features / session_list / session_switched / session_deleted / status_changed` → 渲染层 `AGUI_EVENT` 负载（字段按 Cyrene `chat/main.ts` 实际消费方对齐，属实施阶段先行任务）。
  - 渲染层 IPC 反向映射：`chat:send-message`→`user_message`、`AGUI_CANCEL`→`stop`、确认卡→`confirm_response`、会话操作→`list_sessions / switch_session / delete_session`。
- **音频通路**：Aliya 经 WS 二进制帧推 TTS 音频，主进程收到后转发给聊天/桌宠窗口播放，并派发 `tts_features` 驱动嘴型（复用 Cyrene `live2dSpeech` 桥）。

### 2.3 config.ts（新）
- 定点读写 `data/config/main.yml`（`cosmos.service.*` 节点）+ `LLMProviders.json` + `TTSProviders.json`（如存在）；临时文件+替换，失败回滚。
- GUI 自身偏好（主题/字体/后端模式）存 Electron `userData`，不污染后端配置。

### 2.4 state.ts / ipc.ts
- 聚合 emotion / token / connection / sessions，节流批量推送到渲染窗口。
- IPC handler 全部改走上述模块；裁剪 music / sticker / tasks / call 通道。

## 3. 渲染层复用与适配（4 窗口）

### 3.1 聊天窗口（renderer/chat/，全复用）
- 文案替换："昔涟·聊天"→"Aliya·聊天"、"昔涟"→"Aliya"；未连接状态/空态问候语同步改。
- 会话侧栏：Cyrene `chats:*` IPC（本地 JSON）→ 主进程映射到 WS `list_sessions / switch_session / delete_session`，渲染层不动。
- 顶部下拉框取舍：
  - **模式**（协作/日常聊天）：渲染层过滤——协作显示工具步骤，日常聊天隐藏 `tool_call_*`。
  - **风格**（5 种人格）：Aliya 后端仅单一角色卡 `data/prompts/character.md`，无多风格注入 → 保留 UI，选择写入 GUI 偏好，本次不生效（标注后续对接 ContextBuilder）。
  - **推理**：保留 UI 展示当前模型，强度由后端模型决定。
- 工具卡片（confirm/choice/todo/approval）：保留 CSS 与渲染逻辑，后端事件到达即工作。

### 3.2 状态侧栏（renderer/sidebar/，全复用）
- "昔涟"→"Aliya"；头像替换。
- 状态/心情由 `emotion_changed` dominant 标签映射 emoji/文案；在线状态由 WS 连接状态驱动；"正在喂养"读当前 LLM provider 名。

### 3.3 设置窗口（renderer/settings/，面板裁剪）
- 保留：LLM（provider 增删改/当前选择）、TTS（edge/astra + 音色）、外观（双主题/字体）、后端（自动拉起开关/WS 地址）。
- 裁剪：ASR、记忆、RAG、插件/MCP、贴纸、日程、渠道、通话等面板（隐藏或"即将上线"占位）。

### 3.4 Live2D 桌宠（renderer/live2d/ + index.html，全复用）
- 模型路径指向阿库露（拷入 `GUI/assets/models/akulu/`）。
- **修 `model3.json`** 补 `Expressions`/`Motions` 引用（8 个 `.exp3.json` + 1 个 `.motion3.json`）。
- `emotion_changed` → 表情名映射；音频/`tts_features` → 嘴型；点击/拖拽/置顶/穿透沿用 Cyrene 交互模块。

## 4. 数据流

1. **对话闭环**：聊天输入 → IPC `CHAT_SEND_MESSAGE` → WS `user_message` → 后端 → 事件流 → 主进程 normalize → `AGUI_EVENT` → 聊天窗口流式渲染。
2. **情绪流**：`emotion_changed` → state 聚合 → 侧栏状态/心情 + 桌宠表情。
3. **音频流**：WS 二进制帧（TTS 音频）→ 主进程转发 → 播放 + `tts_features` 嘴型。
4. **会话流**：侧栏/聊天操作 → WS sessions 消息 → `session_list` 刷新渲染层。

## 5. 错误处理

- 后端未启动/崩溃：自动拉起失败 → 通知 + 手动启动指引；WS 指数退避重连。
- WS 断线：连接徽章转灰 + toast，不阻塞界面。
- Live2D 加载失败：隐藏 canvas，其余窗口可用（降级不崩溃）。
- 配置写失败：临时文件+替换 → 回滚 + 报错。
- 渲染层局部异常：组件 try/catch 包裹挂载，错误不扩散。

## 6. 测试策略

- 纯逻辑单测（vitest）：`ws.ts` 协议映射、`config.ts` 定点读写不破坏注释、`state.ts` 聚合节流、`backend.ts` 子进程状态机。
- 组件轻测试（happy-dom）：断线徽章 class、emotion→表情映射、会话列表渲染。
- 构建验证：`tsc --noEmit` + `vite build`。
- 手动联调：`npm start` → 自动拉起后端 → 四窗口对话/情绪/音频闭环。

## 7. 实施阶段（writing-plans 拆解）

- **P0 脚手架**：`GUI/` + package.json + vite + tsconfig 三套。
- **P1 拷贝渲染层**：4 窗口 + `ui/`（tokens/theme/base/fonts）+ 阿库露模型资源。
- **P2 主进程**：backend / ws / config / state / ipc / tray / windows。
- **P3 适配打磨**：Aliya 文案、下拉框映射、设置面板裁剪、双主题验证。
- **P4 Live2D 接入**：model3.json 补引用、情绪/嘴型映射。
- **P5 测试与联调**。

## 8. 已知取舍与后续

- 通话/任务/贴纸窗口：后端补齐 ASR、todo 工具、贴纸语义匹配后接入。
- 聊天风格下拉：仅 UI 占位，需后端 ContextBuilder 支持多风格注入。
- 渲染层事件负载字段：实施时先读 Cyrene `chat/main.ts` 精确对齐。
