# Aliya-cosmos Agent 层架构设计

- 日期：2026-08-20
- 状态：已评审定稿（brainstorming 流程完成，方案 A 全部采纳）
- 定位：为 `Aliya-cosmos` 重构 AI 伴侣 Agent 层的高层架构蓝图
- 参考：`example/Cyrene-Agent-master`（伴侣架构：两阶段 FC 循环、AG-UI 事件流、多渠道、Skill/MCP）、`example/claude-code`（工程化实践：QueryEngine 会话生命周期、权限体系、hooks、上下文管理）

---

## 1. 背景与目标

### 1.1 项目现状

当前项目处于"半重构"状态：

| 部分 | 状态 | 说明 |
|---|---|---|
| `core/` | ✅ 保留 | llm / memory / tts / vector / config / logger / exception 七个子系统完整 |
| `GUI/` | ✅ 保留 | Electron + Vue3 前端完整，`GUI/main/ws.js` 已定义旧 WS 契约 |
| `agent/` | ❌ 已删除 | brain 循环、情绪引擎、认知模块、工具系统、WS 桥接、prompts、hooks 全部移除 |
| `main.py` | ❌ 已删除 | 服务入口 |
| `tests/agent/` | ❌ 已删除 | agent 相关测试全部移除 |
| `data/config/` | ✅ 保留 | `main.yml`（含 agent WS 服务、GRAG、权限、prompt 风格）、`Permissions.yml`、`LLMProviders.json` |
| `data/prompts/` | ✅ 保留 | `identity.md` / `soul.md` / `system.md` / `tone-rules.md` / `tools_system.md` |

### 1.2 目标

在保留的 `core/` 与 `GUI/` 之上，重建一整套对标 Cyrene 全功能的 AI 伴侣 Agent 层：

- **核心能力**：两阶段 FC 对话循环、工具调用 + 权限、记忆/情绪注入、AG-UI 风格事件流
- **伴侣能力**：情绪引擎、主动聊天、多会话历史
- **知识外扩**：RAG 文档库、Skill 系统、MCP 生态
- **多平台渠道**：飞书 / 微信
- **工程化**：会话级生命周期管理、错误降级、完整测试

### 1.3 已确认的关键前提

| 决策点 | 结论 |
|---|---|
| 技术栈 | Python，继承现有 `core/`，asyncio 对接 |
| 架构方案 | 方案 A：进程内分层 + asyncio 事件流（不引入 IPC 总线） |
| 功能范围 | 对标 Cyrene 全功能 |
| WS 协议 | 重构为 AG-UI 风格事件流，GUI 同步升级（分步迁移） |

---

## 2. 总体架构

### 2.1 分层结构

```
GUI (Electron + Vue3)                          ← 现有，WS 层分步升级
   │  WebSocket（AG-UI 风格事件流）
   ▼
agent/（新 Python 层，重建）
├── events.py            # 事件流模型（进程内事件 + 线上协议事件双层）
├── session.py           # AgentSession：会话生命周期（借鉴 QueryEngine）
├── session_store.py     # 多会话历史持久化（列表/切换/标题派生）
├── loop.py              # AgentLoop：两阶段 FC 循环（借鉴 two-phase-fc-loop）
├── context.py           # 上下文构建器（分阶段注入人设/记忆/情绪/工具目录）
├── providers.py         # ProviderAdapter：FC 能力探测与回退（借鉴 vendors/）
├── tools/
│   ├── registry.py      # ToolRegistry：注册/过滤/目录生成
│   ├── base.py          # ToolDefinition + executeTool 执行器
│   ├── permission.py    # 权限检查（Permissions.yml + 风险等级 + 确认等待）
│   └── builtin/         # 内置工具：memory_query/get_current_time/query_recent_conversation 等
├── channels/            # 外部渠道适配层（飞书/微信/本地 WS），复用 AgentSession
├── rag/                 # RAG 文档知识库（混合检索 + reranker）
├── skills/              # Skill 系统（invoke_skill / read_skill_reference）
├── mcp/                 # MCP 客户端（stdio / SSE / HTTP）
├── emotion/             # 情绪引擎（状态 + 分类 + 广播）
├── proactive/           # 主动聊天（触发器 + 护栏 + 渠道路由）
├── ws.py                # WS 网关：事件流收发 + confirm_response 桥接
└── app.py               # 服务装配入口（main.py 重建）
        │  直接调用（asyncio，不跨进程）
        ▼
core/（现有保留，D1 小幅扩展）
├── llm/                 # 扩展：Message 增 tool 角色；ChatRequest 增 tools 字段；ConversationService 支持 tool 消息透传
├── memory/              # GRAG + 层次化记忆（沿用 add_conversation_memory / get_relevant_memories）
├── tts/  vector/  config/  logger/  exception/
```

### 2.2 依赖方向

`agent/` 单向依赖 `core/`，`core/` 不反向依赖 `agent/`。渠道、工具、记忆、情绪均作为 AgentSession 的注入依赖，通过构造注入解耦，便于单测 mock。

---

## 3. 核心抽象

### 3.1 `EventSink`：双层事件模型（优化 O2）

**设计要点**：进程内事件 ≠ 线上协议事件。参考 Cyrene 的"中性 `TwoPhaseEvent` + `CyreneAgent` 包装成 AG-UI `BaseEvent`"双层设计。

- **进程内事件**（丰富，供本地副作用消费）：`StepStarted` / `StepFinished` / `ToolCallStart` / `ToolCallResult` / `ToolCallEnd` / `TextMessageStart` / `TextMessageDelta` / `TextMessageEnd` / `RunStarted` / `RunFinished` / `TokenUsage` / `EmotionChanged` / `StatusChanged`
- **线上协议事件**（精简，映射后转发给 GUI/渠道）：`RUN_STARTED` / `STEP_STARTED` / `STEP_FINISHED` / `TOOL_CALL_START` / `TOOL_CALL_RESULT` / `TOOL_CALL_END` / `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CONTENT` / `TEXT_MESSAGE_END` / `RUN_FINISHED` / `EMOTION_CHANGED` / `STATUS_CHANGED` / `CONFIRM_REQUEST` / `TOKEN_USAGE` / `TTS_FEATURES` / `NOTICE` / `ERROR`

`EventSink` 为订阅接口；本地副作用（记忆/情绪/TTS/日志）订阅进程内事件，WS 网关只订阅并转发线上协议事件。工具内部细节（`step_started`、参数、token 明细）不进线上协议。

### 3.2 `AgentSession`：会话生命周期（借鉴 claude-code QueryEngine）

- 一个对话线程（thread）一个 `AgentSession` 实例，跨轮持有：`ConversationService`（消息历史宿主）、usage 累计、abort controller、`pending_confirmations`
- `submit_user_message(text)` 为异步生成器，逐条产出事件；调用方（WS/渠道）逐条转发
- `interrupt()` 打断当前轮；`reset_abort()` 复位供下一轮使用
- `session_store` 按会话 ID 关联/持久化；会话自动派生标题、`updatedAt` 排序（O5）

### 3.3 `AgentLoop`：两阶段 FC 循环（借鉴 Cyrene two-phase-fc-loop）

```
TOOL_PHASE（每轮）
  1. system = 工具调度规则（tools_system.md）+ 自动生成工具目录
  2. 请求携带 tools schema；首轮可强制 tool_choice（requiredToolName）
  3. 有 tool_calls：
       - 带 tool_calls 的 assistant 消息入历史
       - 逐个执行工具（权限检查 → 确认等待 → executeTool）→ appendToolResults
       - 上下文压缩（compressConversation / truncateToolResult）→ 继续 TOOL_PHASE
  4. 无 tool_calls → 切 SOUL_PHASE
  边界：max_tool_rounds（默认 20）、单轮超时、连续超时强制收尾、工具异常降级

SOUL_PHASE
  1. system = 人设（identity/soul/tone-rules）+ 记忆注入 + 情绪补丁 + 工具结果摘要
  2. 请求不带 tools（避免再次进入工具决策）
  3. 流式产出最终回复 → TEXT_MESSAGE_* 事件
  4. SOUL_PHASE 失败 → 用已收集工具结果拼"任务中断"文案降级返回
```

**会话状态宿主（O1）**：循环不持有独立 message store，直接操作 `ConversationService` 提供的能力（`append_message` / `discard_messages` / `replace_last_message` / `truncate_messages` / `set_context_injection` / `set_emotion_patch`）。此设计复用现有上下文管理、补丁注入与 usage 统计，避免重复实现。

### 3.4 工具系统（注册表 + 权限 + 执行器）

- `ToolDefinition`：`id` / `name` / `description` / `input_schema` / `enabled` / `risk`（safe / medium / high）/ `needs_context`
- `ToolRegistry`：注册、按 id 查询、`get_enabled_tools()` 过滤、自动生成工具目录文本
- 权限检查（`Permissions.yml` + 风险等级）：
  - `always_allow`：直接放行
  - `confirm`：发出 `CONFIRM_REQUEST` 事件，挂起等待用户 `confirm_response`（O4）
  - `never_allow`：拒绝并返回 `[已拒绝] 原因`
- 工具执行经 `executeTool` 回调注入（注入 `ToolContext`：user_query、conversation_id、agent 实例），异常统一转 `[工具执行失败] …` 文本，不崩循环

### 3.5 `ProviderAdapter`：原生 FC 能力探测与回退（O3）

`LLMProviders.json` 覆盖 deepseek/ollama/lmstudio 等，并非所有后端支持原生 tool calling。

- 启动/运行时探测 provider 能力（`supports_tools`）
- **支持原生 FC** → 走 FC 路径（`finish_reason=tool_calls` 判定工具调用）
- **不支持** → 回退文本 JSON 协议路径（复用 `tools_system.md` 的 `{"tool_calls": []}` 方式，`replace_last_message` 净化 JSON 决策）
- 回退路径保留旧 agent 的成熟策略，保证本地模型可用

---

## 4. 关键技术决策

| 编号 | 决策 | 结论 | 理由 |
|---|---|---|---|
| D1 | 工具调用协议 | 扩展 `core/llm` 支持原生 FC（`Message` 增 `tool` 角色、`ChatRequest` 增 `tools` 字段、`ConversationService` 支持 tool 消息透传） | 与两个 example 对齐；文本 JSON 协议解析脆弱；原生 FC 有 `finish_reason=tool_calls` 可靠判定；同时保留文本协议回退（O3） |
| D2 | WS 协议 | 重构为 AG-UI 风格事件流，GUI `ws.js` 分步升级（O6） | 标准化、支撑流式打字效果；emotion/status/token/tts 字段并入事件 payload |
| D3 | 渠道 | 飞书/微信复用同一 `AgentSession`，仅替换事件源与 EventSink | 与 Cyrene `channels/` 一致，扩展成本最低 |
| D4 | 事件流 | 双层事件模型：进程内事件驱动副作用，线上协议事件驱动 UI | 避免内部细节泄漏到 GUI/渠道（O2） |
| D5 | 会话 | `AgentLoop` 复用 `ConversationService` 为消息历史宿主 | 复用上下文管理/补丁/usage，避免重复造轮子（O1） |
| D6 | 配置 | `main.yml` 新增 `agent:` 配置段 | 统一配置源头，遵循现有配置体系 |
| D7 | 安全 | 渠道凭据用系统 keyring 加密；高风险工具默认 `confirm` | 吸取 Cyrene README 明文存盘教训（O8） |

---

## 5. 单轮对话数据流

```
WS 收到 user_message
  → Session.submit_user_message()
  → AgentLoop 进入 TOOL_PHASE
      → context.py 构建工具 system（tools_system.md + 工具目录）
      → ProviderAdapter 调 LLM（携带 tools schema）
      ├─ 有 tool_calls
      │   → 权限检查
      │     ├─ confirm → CONFIRM_REQUEST 事件 → 等待 confirm_response（Future 挂起）
      │     └─ 拒绝   → 返回 [已拒绝] 文本
      │   → 执行工具 → TOOL_CALL_* 事件（进程内 + 线上）
      │   → appendToolResults → 上下文压缩 → 继续 TOOL_PHASE
      └─ 无 tool_calls（或达最大轮数/超时）→ 切 SOUL_PHASE
  → SOUL_PHASE
      → context.py 构建人设 system（identity/soul/tone-rules + 记忆注入 + 情绪补丁 + 工具结果摘要）
      → 流式回复 → TEXT_MESSAGE_* 事件
  → RUN_FINISHED
      → 副作用（订阅进程内事件）：
        - after_turn：GRAG add_conversation_memory 写入记忆
        - 情绪引擎更新 → EMOTION_CHANGED
        - token 累计 → TOKEN_USAGE
        - 触发 TTS → TTS_FEATURES（口型同步音量数据）
```

---

## 6. 错误处理与降级

| 场景 | 处理策略 |
|---|---|
| LLM 调用失败 | 复用 `ConversationService` 指数退避重试 → 降级文案 |
| 工具执行失败 | `[工具执行失败] …` 文本回传，循环继续 |
| 工具阶段单轮超时 | 连续 N 次超时 → 强制切 SOUL_PHASE 总结 |
| 达到最大工具轮数 | 强制 SOUL_PHASE 总结（含已完成步骤摘要） |
| SOUL_PHASE 失败 | 用已收集工具结果拼"任务中断"文案降级返回 |
| 记忆/向量/TTS 不可用 | 沿用 core 告警降级原则，不阻塞主流程 |
| 渠道断开 | 自动重连，会话状态保留 |
| 用户打断（stop） | `interrupt()` 终止当前轮，输出已停止提示 |

---

## 7. 测试策略

沿用 pytest `asyncio_mode = "auto"`，`tests/agent/` 重建。

- **单测**：loop 状态机（mock LLM 返回 tool_calls/无 tool_calls/超时）、tool registry、权限检查、`ProviderAdapter` 能力探测与回退、context builder、事件序列化、`pending_confirmations` 时序
- **集成**：内存 transport 模拟 WS 全链路（`user_message` → 事件流 → `confirm_response`）、渠道适配器 mock、`ConversationService` + `AgentLoop` 联合
- **markers**：`slow` / `integration` / `unit` / `agent`

---

## 8. 里程碑（实施阶段）

| 阶段 | 范围 | 验收标准 |
|---|---|---|
| **M1 基础闭环** | core/llm 扩展 FC → 事件流基础设施 → 两阶段循环 → 基础工具（memory_query / get_current_time / query_recent_conversation）→ WS 网关（AG-UI 协议）→ GUI WS 层分步升级 | 端到端对话可用：文本回复流式渲染、工具调用可见、`confirm_response` 流程通 |
| **M2 伴侣能力** | 情绪引擎 + 主动聊天（触发器/护栏/路由）+ 多会话历史 | 情绪随对话变化并广播；主动消息按护栏触发；会话可切换/持久化 |
| **M3 知识外扩** | RAG 文档库 + Skill 系统 + MCP | 拖入文档可被检索引用；skill 可被调用；MCP server 可接入 |
| **M4 多渠道** | 飞书 / 微信接入 + 整体打磨 | 渠道复用同一大脑，消息双向往来；性能与稳定性达标 |

每个里程碑完成时跑通对应测试与手动验收，再进入下一阶段。

---

## 9. 参考对照

| 设计点 | 来源 |
|---|---|
| 两阶段 FC 循环（TOOL_PHASE/SOUL_PHASE、边界兜底） | `example/Cyrene-Agent-master/src/main/orchestrator/two-phase-fc-loop.ts` |
| AG-UI 事件流 + 双层事件包装 | `Cyrene-Agent` cyrene-agent.ts + `@ag-ui/core` |
| 会话生命周期 / QueryEngine 模式 | `example/claude-code/src/QueryEngine.ts` |
| 工具权限（allow/confirm/deny） | 现有 `data/config/Permissions.yml` + claude-code 权限体系 |
| 多渠道复用同一大脑 | `Cyrene-Agent` channels/ 适配层 |
| 记忆 L0/L1/L2 → 映射 | 现有 GRAG + 层次化记忆（`core/memory/`），无需重构 |
| Skill / MCP / RAG / proactive | `Cyrene-Agent` 对应模块，按本架构裁剪移植 |
