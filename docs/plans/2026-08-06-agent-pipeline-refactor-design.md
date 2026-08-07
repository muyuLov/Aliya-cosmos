# Agent 管线式重构设计文档

> 日期：2026-08-06
> 分支：dev
> 状态：已确认（待实施）

## 背景与动机

当前 `agent` 层经过多轮迭代（两阶段状态机、Brain/Emotion/Cognition 分层、工具系统），存在四个核心痛点：

1. **Brain 侵入 ConversationService 内部**：`brain.py` 直接操作 `conv._lock` / `conv._context` / `conv._save()` 等私有状态（`_sanitize_tool_phase_message`、`compress_conversation`），封装边界被破坏。
2. **依赖注入/构造方式混乱**：`ToolContext` 被重复构造（主流程与 `_speak` 各建一个），构造参数多达 8 个。
3. **认知/情感引擎与主流程硬耦合**：emotion / cognition 的钩子（`before_turn` / `after_tool` / `after_turn`）散落在 `agent.py` 流程各处，新增能力需修改主编排器。
4. **编排器职责过重**：`agent.py`（约 680 行）既当状态机又当调度器，还负责 TTS / 记忆 / 风格 / 情绪推进。

**约束**：两阶段状态机（⚙工具阶段 → 灵魂阶段）作为产品核心保留，对外 API 与 WS 协议零破坏。

## 方案选型

经讨论，选择 **方案 B（管线式重构）为主 + C（钩子/订阅机制）的混合**：

- 管线（Pipeline）为主体，阶段独立模块化；
- 横切能力（TTS / 记忆 / 情绪 / 认知）通过 **混合钩子系统** 接入——顺序敏感操作用同步钩子，耗时/可丢操作用异步 fire-and-forget 钩子；
- 不引入完整事件总线（单会话体量下复杂度成本 > 收益）。

## 目标结构

```
agent/
├── context.py        # AgentContext：会话级统一依赖容器（新）
├── pipeline.py       # AgentPipeline：一轮对话的编排器（新，替代 agent.py 主体）
├── stages/           # 阶段模块（新，从 agent.py 拆出）
│   ├── assemble.py   # 上下文组装（含认知注入）
│   ├── think.py      # 工具阶段 Think/Act/Observe 循环
│   └── soul.py       # 灵魂阶段
├── hooks.py          # 钩子注册表（新）
├── brain.py          # 保留：LLM 交互层（移除私有状态侵入）
├── agent.py          # 薄封装：AliyaAgent 门面，保留对外 API
└── ws.py / config.py / emotion/ / cognition/ / tools/ / prompts/  # 基本不变
```

## 第 1 节：整体架构与模块划分

核心思想：

- **`AgentContext`** 在一次构造中收拢全部依赖（conv / registry / memory / tts / notify / config / emotion / cognition），管线各阶段从它取所需，`ToolContext` 也从它派生——彻底消灭重复构造；
- **`AgentPipeline`** 只做一件事：按顺序驱动阶段流转 + 发通知 + 触发钩子，不再亲自实现 TTS / 记忆 / 风格 / 情绪逻辑；
- **横切能力**（情绪、认知、记忆、TTS）各自注册为钩子订阅者，管线在固定钩子点触发它们。

效果：`agent.py` 从 680 行瘦身为门面层（约 80 行），新增能力只需"注册一个钩子 + 写一个订阅者"，不再修改编排器。

## 第 2 节：钩子系统与阶段流转

### 钩子系统（hooks.py）

定义 `HookPoint` 枚举 + `HookRegistry`。钩子点固定为：

| 钩子点 | 类型 | 说明 |
|---|---|---|
| `before_turn(text)` | 同步 | 认知准备，必须阻塞（结果注入上下文） |
| `after_tool(name, result)` | 同步 | 工具学习，顺序敏感 |
| `after_turn(reply)` | 同步 | 对话收尾（记忆保存、情绪推进调度） |
| `after_reply(reply)` | 异步可丢 | 通知类（TTS 播放、brain_complete 通知） |

- 同步钩子：按注册顺序 `await`，异常由管线捕获降级（不中断主流程）；
- 异步可丢钩子：管线用 `asyncio.create_task` 调度，加 `_log_task_error` 回调，不阻塞回复返回；
- 注册方式：`registry.register(point, handler)`，`ws.py` 的 `build_agent()` 统一组装——把散落在 `agent.py` 里的情绪推进、记忆保存、TTS 播放、风格切换逻辑，各自收敛成独立钩子订阅者对象。

### 阶段流转（pipeline.py）

```
handle_user_message(text)
 └─ before_turn 钩子（认知准备）
 └─ Stage: assemble  → 工具阶段 system prompt + 认知注入
 └─ Stage: think     → Brain.think → [tool_calls?] → dispatch_all
 │                       ├─ after_tool 钩子（认知学习）
 │                       ├─ 注入工具结果 → think_with_context（循环）
 │                       └─ 无工具或超限 → 退出
 └─ Stage: soul      → 人格上下文 + 记忆注入 → generate_soul_reply
 └─ after_turn 钩子（记忆/情绪）
 └─ after_reply 钩子（TTS/通知，异步可丢）
```

阶段之间的"切换人格/切换上下文"逻辑从 `_enter_tool_phase` / `_enter_soul_phase` 平移进各 stage 模块；`_STATE_DISPLAY` / `_transition` 通知保留在 pipeline 中（这是编排职责）。

## 第 3 节：AgentContext 依赖容器 + 边界修复

### AgentContext（context.py）

```python
@dataclass
class AgentContext:
    conv: ConversationService
    registry: ToolRegistry
    config: AgentConfig
    prompt_manager: PromptManager
    style_switcher: StyleSwitcher
    brain: Brain
    emotion: EmotionEngine
    cognition: CognitionEngine | None
    memory_manager: Any | None
    tts_service: Any | None
    audio_player: Any | None
    notify: Callable[[dict], Awaitable[None]]
    confirm_callback: Callable[[str, dict], Awaitable[bool]] | None
    permission_config: Any | None

    def make_tool_context(self) -> ToolContext: ...   # 派生，仅一处
```

- `AliyaAgent` 门面持有 `AgentContext`；`ToolContext` 通过 `ctx.make_tool_context()` 派生（TTS 自动播放也用它，消灭第二处重复构造）；
- `make_tool_context()` 返回的 `ToolContext` 字段全部来自 `AgentContext`；
- `ws.py` 的 `build_agent()` 改为组装 `AgentContext` 后传给门面。

### 边界修复（core/llm/service.py）

Brain 的私有状态侵入点有 2 处：

1. `_sanitize_tool_phase_message` —— 替换最后一条 assistant 消息；
2. `compress_conversation` —— 截断历史。

方案：在 `ConversationService` 增加两个公开方法（持锁 + 保存）：

```
async def replace_last_message(content: str, reasoning_content: str = "") -> None
async def truncate_messages(keep: int) -> None
```

Brain 改为只调用公开 API，不再触碰 `_lock` / `_context` / `_save()`。`get_history()`、`append_message()`、`discard_messages()` 已是公开方法，保持不变。

## 第 4 节：错误处理 / 降级 / 测试 / 迁移顺序

### 错误处理与降级

- 管线负责异常边界：`handle_user_message` 的 try/except 保留在 pipeline，`brain_error` 通知、`force_summary_reply` 兜底、`_finalize` 收尾逻辑平移至 pipeline；
- 同步钩子异常：管线捕获并 `logger.warning`，不中断阶段流转；
- 异步可丢钩子（TTS/通知）：`create_task` 加 `_log_task_error` 回调，不阻塞回复；
- `AgentContext` 是冻结数据类，初始化失败即启动失败（fail-fast）。

### 测试策略

- `tests/agent/test_pipeline.py`：管线各阶段独立单测（假 brain/emotion/cognition，验证流转与钩子触发顺序）；
- `tests/agent/test_hooks.py`：钩子注册/触发/异常隔离；
- `tests/agent/test_context.py`：`AgentContext.make_tool_context()` 派生正确性；
- 现有 `test_agent.py` / `test_ws.py` 保持通过（门面层 API 不变）；
- `core/llm` 新增 `replace_last_message` / `truncate_messages` 单测。

### 迁移顺序（4 步，每步独立验证）

1. `core/llm/service.py` 补两个公共方法 + 单测（不碰 agent）；
2. 新建 `context.py` / `hooks.py` / `stages/`，Brain 改用公开 API（此时 agent.py 仍可用）；
3. 新建 `pipeline.py`，把 agent.py 主体逻辑平移，`agent.py` 瘦身为门面（对外 API 不变，WS/GUI 无感）；
4. 清理：`__init__.py` 导出、CLAUDE.md 架构说明更新、跑全量测试。

## 对外兼容性承诺

- `AliyaAgent` 的 `handle_user_message` / `set_style` / `get_emotion_state` / `get_cognition_status` / `warmup` / `close_emotion_classifier` 等全部保留；
- WS 协议消息类型不变；
- 现有 139 个测试用例保持通过。
