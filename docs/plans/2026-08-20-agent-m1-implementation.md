# Aliya-cosmos Agent 层 M1 基础闭环实施计划

- 日期：2026-08-20
- 前置文档：`docs/plans/2026-08-20-agent-architecture-design.md`（架构蓝图）
- 目标范围：M1 基础闭环（core/llm 扩展 FC → 事件流 → 两阶段循环 → 基础工具 → WS 网关 → GUI 升级）
- 技术栈：Python（继承 core/）、asyncio、FastAPI + uvicorn（WS 已由现有依赖提供）

---

## 0. 成功标准（M1 验收清单）

实现完成后，手动验收需全部满足：

1. 启动后端 → GUI 聊天窗口可连接 WS
2. 发送普通消息 → Aliya 流式回复逐字渲染
3. 发送"还记得我们上次聊什么吗" → 触发 `memory_query` 工具调用，工具调用过程在 GUI 可见，且回答使用记忆内容
4. 工具确认流程：调用高风险工具（如改名的测试工具）时弹出确认横幅，允许/拒绝均生效
5. `stop` 中断：流式过程中点击停止，回复截断并提示"已停止回复"
6. 后端崩溃/重启后，GUI 自动重连
7. `pytest tests/agent tests/llm` 全部通过

**执行方式**：本计划中每个 Task 描述为"可一步走完"的最小动作单元（涉及文件、修改点、验证方式），按顺序执行，前一步完成并验证后再进入下一步。

---

## 1. 前置检查与目录重建

### Task 1.1 创建 `agent/` 包骨架与测试目录

- **涉及文件**：
  - `agent/__init__.py`（新建，空文件或版本号）
  - `tests/agent/__init__.py`（新建，空文件）
  - `tests/agent/conftest.py`（新建）
- **修改点**：
  1. 创建 `agent/__init__.py`，写入模块 docstring 与 `__version__ = "0.1.0"`
  2. 创建 `tests/agent/__init__.py`
  3. 创建 `tests/agent/conftest.py`，内容为共享 fixtures 占位（后续 Task 补充）：
     ```python
     """agent 模块共享测试夹具"""
     import pytest
     ```
- **验证**：`python -c "import agent"` 无报错；`pytest tests/agent --collect-only` 正常收集（0 个测试）。

### Task 1.2 校验现有测试基线

- **涉及文件**：无（只运行命令）
- **修改点**：运行 `uv run pytest tests/llm tests/memory --cov=agent --cov=core --cov-report=term`（注：`--cov=agent` 需 agent 目录存在，Task 1.1 已建）
- **验证**：全部通过；若 `--cov=agent` 报错（无匹配文件），改在 pyproject.toml 确认 `--cov=agent` 已配置且目录存在。

---

## 2. core/llm 扩展原生 function calling（D1）

> 目标：让 `core/llm` 支持 OpenAI 兼容的 `tools` / `tool_calls` 协议，且不破坏现有调用（`--cov=core` 覆盖率不下降）。

### Task 2.1 扩展 `Message` 数据模型

- **涉及文件**：`core/llm/models.py`
- **修改点**：
  1. `Message.role` 的 `Literal` 增加 `"tool"`：`Literal["system", "user", "assistant", "tool"]`
  2. 新增可选字段：
     ```python
     tool_call_id: str | None = None      # 当 role == "tool" 时必填
     tool_calls: list[dict] | None = None  # assistant 消息携带的工具调用数组（OpenAI 格式）
     ```
  3. 扩展 `to_api_dict()`：当 `role == "tool"` 时返回 `{"role": "tool", "tool_call_id": ..., "content": ...}`；当 `tool_calls` 非空时返回 `{"role": "assistant", "content": ..., "tool_calls": [...]}`
  4. 扩展 `to_full_api_dict()`：同上，保留 `reasoning_content` 逻辑不变
- **验证**：新增测试 `tests/llm/test_models.py`：
  - 构造 `Message(role="tool", content="查询结果", tool_call_id="call_1")`，断言 `to_api_dict()` 含 `tool_call_id`
  - 构造带 `tool_calls` 的 assistant 消息，断言序列化后含 `tool_calls` 键
  - 现有 system/user/assistant 序列化结果不变

### Task 2.2 扩展 `ChatRequest` 与 `ChatResponse`

- **涉及文件**：`core/llm/models.py`
- **修改点**：
  1. `ChatRequest` 新增字段：
     ```python
     tools: list[dict] | None = None         # OpenAI tools schema 数组
     tool_choice: str | dict | None = None   # "auto" / "required" / {"type": "function", "function": {"name": ...}}
     ```
  2. `ChatResponse` 新增字段：
     ```python
     tool_calls: list[dict] | None = None    # 模型请求的工具调用数组
     ```
- **验证**：新增 `tests/llm/test_models.py` 用例：构造带 `tools` 的 ChatRequest、带 `tool_calls` 的 ChatResponse，断言字段序列化/反序列化正确；默认值均为 `None`。

### Task 2.3 `OpenAICompatibleProvider` 透传 tools

- **涉及文件**：`core/llm/providers/openai_compatible.py`
- **修改点**：
  1. `_RESERVED_EXTRA_KEYS` 增加 `"tools"`、`"tool_choice"`
  2. `_build_kwargs()` 中，当 `request.tools` 非空时：
     ```python
     if request.tools:
         kwargs["tools"] = request.tools
     if request.tool_choice is not None:
         kwargs["tool_choice"] = request.tool_choice
     ```
  3. `async_chat_completion()` 返回前，从 `choice.message` 提取 `tool_calls`：
     ```python
     tool_calls = None
     raw_tool_calls = getattr(choice.message, "tool_calls", None)
     if raw_tool_calls:
         tool_calls = [
             {
                 "id": tc.id,
                 "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments},
             }
             for tc in raw_tool_calls
         ]
     ```
     并填入 `ChatResponse(tool_calls=tool_calls, ...)`
  4. `stream_chat_completion()` 中同样收集流式 `delta.tool_calls`（供未来工具阶段流式决策用，本 M1 可先不做完整收集，仅保证非流式路径正确）
- **验证**：新增 `tests/llm/test_openai_compatible.py`：
  - 用 `resp = await provider.async_chat_completion(req)` 的 mock（monkeypatch `self._async_client.chat.completions.create`），返回带 `tool_calls` 的响应，断言 `resp.tool_calls` 解析正确
  - 断言无 tools 时 `_build_kwargs` 不含 `tools` 键

### Task 2.4 `ConversationService` 支持 tool 消息与 tools 参数

- **涉及文件**：`core/llm/service.py`、`core/llm/context_manager.py`
- **修改点**：
  1. `ConversationService.append_message()` 的 `role` 参数类型从 `Literal["system", "user", "assistant"]` 扩展为含 `"tool"`
  2. `append_message()` 新增 `tool_call_id: str | None = None` 与 `tool_calls: list[dict] | None = None` 关键字参数，透传给 `ConversationContextManager.append_message()`
  3. `ConversationContextManager.append_message()` 透传 `tool_call_id` / `tool_calls` 到 `Message`
  4. `ConversationContextManager._build_messages()`（或 `prepare_request()` 内部构建完整消息列表处）确保 `Message.to_full_api_dict()` 已包含 tool 字段
  5. `ConversationService.asend()` / `astream_send()` 已透传 `**kwargs` 到 `ChatRequest`，确认 `tools` / `tool_choice` 可通过 kwargs 传入
- **验证**：新增 `tests/llm/test_conversation_service.py` 用例：
  - `await svc.append_message("tool", "结果", tool_call_id="call_1")` 后 `await svc.get_history()` 返回含 tool 角色消息
  - mock provider 的 `async_chat_completion`，用 `await svc.asend("hi", tools=[...])`，断言传给 provider 的 `ChatRequest.tools` 非空

---

## 3. 事件流基础设施（O2）

> 目标：进程内事件 + 线上协议事件双层模型，供 WS 网关与后续副作用订阅。

### Task 3.1 定义进程内事件模型

- **涉及文件**：`agent/events.py`（新建）
- **修改点**：定义 `RunEvent` 基类与事件数据类：
  ```python
  from dataclasses import dataclass, field
  from typing import Any

  @dataclass(frozen=True)
  class AgentEvent:
      """进程内事件基类"""

  @dataclass(frozen=True)
  class RunStarted(AgentEvent):
      session_id: str

  @dataclass(frozen=True)
  class StepStarted(AgentEvent):
      phase: str                 # "tool" | "soul"

  @dataclass(frozen=True)
  class StepFinished(AgentEvent):
      phase: str

  @dataclass(frozen=True)
  class ToolCallStart(AgentEvent):
      call_id: str
      tool_name: str
      arguments: dict

  @dataclass(frozen=True)
  class ToolCallResult(AgentEvent):
      call_id: str
      output: str

  @dataclass(frozen=True)
  class ToolCallEnd(AgentEvent):
      call_id: str

  @dataclass(frozen=True)
  class TextMessageStart(AgentEvent):
      message_id: str

  @dataclass(frozen=True)
  class TextMessageDelta(AgentEvent):
      message_id: str
      text: str

  @dataclass(frozen=True)
  class TextMessageEnd(AgentEvent):
      message_id: str
      full_text: str

  @dataclass(frozen=True)
  class RunFinished(AgentEvent):
      session_id: str
  ```
- **验证**：新增 `tests/agent/test_events.py`：实例化各事件类，断言字段可访问、frozen（不可修改）。

### Task 3.2 定义线上协议事件与映射

- **涉及文件**：`agent/events.py`（追加）
- **修改点**：
  1. 定义 `ProtocolEvent` 数据类（`type: str` + `payload: dict`）：
     ```python
     @dataclass(frozen=True)
     class ProtocolEvent:
         type: str
         payload: dict[str, Any]
     ```
  2. 定义事件类型常量：
     ```python
     RUN_STARTED = "run_started"
     STEP_STARTED = "step_started"
     TOOL_CALL_START = "tool_call_start"
     TOOL_CALL_RESULT = "tool_call_result"
     TEXT_MESSAGE_START = "text_message_start"
     TEXT_MESSAGE_CONTENT = "text_message_content"
     TEXT_MESSAGE_END = "text_message_end"
     RUN_FINISHED = "run_finished"
     CONFIRM_REQUEST = "confirm_request"
     ERROR = "error"
     NOTICE = "notice"
     TOKEN_USAGE = "token_usage"
     STATUS_CHANGED = "status_changed"
     EMOTION_CHANGED = "emotion_changed"
     TTS_FEATURES = "tts_features"
     ```
  3. 定义映射函数 `to_protocol(event: AgentEvent, **ctx) -> ProtocolEvent | None`：
     - `TextMessageStart` → `TEXT_MESSAGE_START`（payload 含 message_id）
     - `TextMessageDelta` → `TEXT_MESSAGE_CONTENT`（payload 含 message_id、text）
     - `TextMessageEnd` → `TEXT_MESSAGE_END`（payload 含 message_id、full_text）
     - `ToolCallStart` → `TOOL_CALL_START`（payload 含 tool_name、arguments）
     - `ToolCallResult` → `TOOL_CALL_RESULT`（payload 含 call_id、output 摘要）
     - `RunStarted` → `RUN_STARTED`；`RunFinished` → `RUN_FINISHED`；`StepStarted`/`StepFinished` → `STEP_STARTED`
     - 其余返回 `None`（内部事件不进线上协议）
- **验证**：新增 `tests/agent/test_events.py`：断言 `to_protocol(TextMessageDelta(...))` 映射正确、`to_protocol(EmotionChanged(...))`（暂未定义的内部事件）返回 `None`。

### Task 3.3 定义 `EventSink` 接口

- **涉及文件**：`agent/events.py`（追加）
- **修改点**：
  ```python
  from typing import Awaitable, Callable, Protocol

  class EventSink(Protocol):
      """事件订阅接口：任何实现者都可订阅进程内事件"""
      async def emit(self, event: AgentEvent) -> None: ...
  ```
- **验证**：`tests/agent/test_events.py` 中用鸭子类型 mock 实现 `EventSink` 的类可被 isinstance 检查（或直接构造一个实现 `emit` 的类调用通过）。

---

## 4. 工具系统（注册表 + 权限 + 内置工具）

### Task 4.1 `ToolDefinition` 与 `ToolContext`

- **涉及文件**：`agent/tools/__init__.py`（新建）、`agent/tools/base.py`（新建）
- **修改点**：
  1. `agent/tools/__init__.py`：导出 `ToolRegistry`、`ToolDefinition`、`ToolContext`、内置工具注册函数
  2. `agent/tools/base.py`：
     ```python
     from dataclasses import dataclass, field
     from typing import Any, Awaitable, Callable

     @dataclass(frozen=True)
     class ToolDefinition:
         id: str                      # 工具标识（permission 用）
         name: str                    # 函数名（LLM 调用名）
         description: str             # 描述（进 tools schema）
         input_schema: dict           # JSON Schema（参数）
         enabled: bool = True
         risk: str = "safe"           # "safe" | "medium" | "high"

     @dataclass
     class ToolContext:
         user_query: str
         conversation_id: str
         memory: Any = None           # GRAGMemoryManager 或 None

     ToolExecutor = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]
     ```
- **验证**：`tests/agent/test_tools.py`：构造 ToolDefinition、ToolContext 断言字段默认值。

### Task 4.2 `ToolRegistry` 注册与过滤

- **涉及文件**：`agent/tools/registry.py`（新建）
- **修改点**：
  ```python
  class ToolRegistry:
      def __init__(self) -> None:
          self._tools: dict[str, tuple[ToolDefinition, ToolExecutor]] = {}

      def register(self, definition: ToolDefinition, executor: ToolExecutor) -> None:
          self._tools[definition.id] = (definition, executor)

      def get(self, tool_id: str) -> tuple[ToolDefinition, ToolExecutor] | None:
          return self._tools.get(tool_id)

      def enabled_definitions(self) -> list[ToolDefinition]:
          return [d for d, _ in self._tools.values() if d.enabled]

      def build_tools_schema(self) -> list[dict]:
          """转 OpenAI tools 数组：[{"type": "function", "function": {"name", "description", "parameters"}}]"""
          return [
              {
                  "type": "function",
                  "function": {
                      "name": d.name,
                      "description": d.description,
                      "parameters": d.input_schema,
                  },
              }
              for d in self.enabled_definitions()
          ]

      def execute(self, tool_id: str, ctx: ToolContext, args: dict[str, Any]) -> Awaitable[str]:
          entry = self._tools.get(tool_id)
          if not entry:
              return ...  # 返回 "[工具未注册]" 的已解析协程
          _, executor = entry
          return executor(ctx, args)
  ```
- **验证**：`tests/agent/test_tools.py`：注册假工具 → `build_tools_schema()` 输出正确；`execute()` 调用 executor 并返回结果；未注册工具返回错误文案。

### Task 4.3 权限检查（Permissions.yml）

- **涉及文件**：`agent/tools/permission.py`（新建）
- **修改点**：
  1. 读取 `data/config/Permissions.yml`（沿用现有 `get_config_instance().get("cosmos.service.agent.permissions.config_path")` 或直接读文件路径）
  2. 定义权限枚举：
     ```python
     class Permission(Enum):
         ALLOW = "allow"
         CONFIRM = "confirm"
         DENY = "deny"

     class PermissionChecker:
         def __init__(self, config_path: str) -> None: ...
         def check(self, tool_id: str) -> Permission:
             """规则优先级：deny > confirm > allow > 默认"""
     ```
  3. 默认策略：`risk == "high"` 且未配置 → `CONFIRM`；`risk == "safe"` 且未配置 → `ALLOW`；`risk == "medium"` → `CONFIRM`
- **验证**：`tests/agent/test_tools.py`（或新建 `test_permission.py`）：临时生成 Permission 测试文件，断言 allow/confirm/deny/默认策略。

### Task 4.4 内置工具：`get_current_time` / `memory_query` / `query_recent_conversation`

- **涉及文件**：
  - `agent/tools/builtin/__init__.py`（新建）
  - `agent/tools/builtin/time_tool.py`（新建）
  - `agent/tools/builtin/memory_tools.py`（新建）
  - `agent/tools/builtin/__init__.py` 中定义 `register_builtin_tools(registry: ToolRegistry) -> None`
- **修改点**：
  1. `time_tool.py`：`get_current_time` 工具，executor 返回 `datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z")`（含本地时区）；`risk="safe"`
  2. `memory_tools.py`：
     - `memory_query`：executor 调用 `ctx.memory.get_relevant_memories(query=args["query"], limit=args.get("limit", 3))`，格式化为可读文本返回；`risk="safe"`
     - `query_recent_conversation`：executor 调用 `ctx.memory.query_memory(question=args["question"])`，返回回答或 `None` 时返回"无相关记忆"；`risk="safe"`
  3. 每个工具定义 `ToolDefinition`（含 input_schema：`query` 字符串必填等）
- **验证**：`tests/agent/test_tools.py`：用 mock 的 `memory` 对象调用各 executor，断言返回值正确；`register_builtin_tools` 后 registry 含 3 个工具。

---

## 5. 上下文构建器（O5 复用 ConversationService）

### Task 5.1 `ContextBuilder` 构建工具阶段 system

- **涉及文件**：`agent/context.py`（新建）
- **修改点**：
  1. 读取提示词文件（复用现有 `data/prompts/`）：
     - 工具阶段：`tools_system.md`（已有内容：工具调度规则）
  2. 定义：
     ```python
     class ContextBuilder:
         def __init__(self, prompts_dir: str = "data/prompts") -> None:
             self._dir = prompts_dir

         def build_tool_system(self) -> str:
             """返回工具调度规则全文（tools_system.md），不拼接工具列表——
             工具列表由 LLM API 的 tools schema 传递"""
     ```
- **验证**：`tests/agent/test_context.py`：`build_tool_system()` 返回字符串且含"工具调度"关键内容。

### Task 5.2 `ContextBuilder` 构建灵魂阶段 system

- **涉及文件**：`agent/context.py`（追加）
- **修改点**：
  1. 读取 `identity.md`、`soul.md`、`tone-rules.md`
  2. 定义：
     ```python
     def build_soul_system(
         self,
         *,
         memory_text: str = "",
         emotion_patch: str = "",
         tool_summary: str = "",
     ) -> str:
         """按顺序拼接 identity + soul + tone-rules + 工具结果摘要 + 记忆 + 情绪补丁"""
     ```
  3. 拼接顺序（与架构文档一致）：人设 → 记忆 → 情绪 → 工具结果摘要
- **验证**：`tests/agent/test_context.py`：传入 memory_text/emotion_patch/tool_summary，断言输出字符串包含全部注入片段且顺序正确。

### Task 5.3 组装 `ContextBuilder` 与 `ConversationService` 的接线

- **涉及文件**：`agent/context.py`（追加）
- **修改点**：定义组合函数：
  ```python
  async def inject_soul_context(
      service: ConversationService,
      builder: ContextBuilder,
      *,
      memory_text: str,
      emotion_patch: str,
      tool_summary: str = "",
  ) -> None:
      system = builder.build_soul_system(memory_text=memory_text, emotion_patch=emotion_patch, tool_summary=tool_summary)
      await service.set_system_prompt(system)
  ```
  （复用现有 `ConversationService.set_system_prompt`，无需新实现）
- **验证**：`tests/agent/test_context.py` 用 mock service 断言 `set_system_prompt` 被调用且参数正确。

---

## 6. 两阶段循环 `AgentLoop`（Cyrene 模式）

### Task 6.1 `AgentLoop` 类骨架与 `submit_user_message`

- **涉及文件**：`agent/loop.py`（新建）
- **修改点**：
  ```python
  class AgentLoop:
      def __init__(
          self,
          service: ConversationService,
          registry: ToolRegistry,
          checker: PermissionChecker,
          context: ContextBuilder,
          *,
          max_tool_rounds: int = 20,
          tool_timeout: float = 30.0,
      ) -> None:
          ...
          self._abort = False

      def interrupt(self) -> None:
          self._abort = True

      def reset_abort(self) -> None:
          self._abort = False

      async def submit_user_message(self, text: str) -> AsyncGenerator[AgentEvent, None]:
          """异步生成器：逐条产出事件，由 WS 网关/渠道消费"""
          yield RunStarted(session_id=self.service.conversation_id)
          ...
  ```
- **验证**：`tests/agent/test_loop.py`：`interrupt()`/`reset_abort()` 状态切换正确；`submit_user_message` 为异步生成器（先产出 `RunStarted`）。

### Task 6.2 TOOL_PHASE 工具阶段实现

- **涉及文件**：`agent/loop.py`（追加）
- **修改点**：
  1. 工具阶段开始时 `yield StepStarted(phase="tool")`
  2. 循环（`for _ in range(max_tool_rounds)`）：
     - 调 `service.asend(text, store_history=False, tools=registry.build_tools_schema(), tool_choice="auto")`
       - 注：M1 用户消息只入历史一次，工具循环用 `store_history=False` 复用历史 + append tool 消息
     - 解析返回：`service.last_reasoning_content` / 响应 `tool_calls`
     - 若无 `tool_calls` → break 进入 SOUL_PHASE
     - 若有 `tool_calls`：
       - 将 assistant 消息（含 tool_calls）append 进历史
       - 逐个执行：
         1. 权限检查 → `CONFIRM` 时 `yield` 挂起（O4，见 Task 6.4）
         2. `yield ToolCallStart(...)` → 执行 → `yield ToolCallResult(...)` → `yield ToolCallEnd(...)`
         3. 执行结果 append 为 tool 角色消息
       - 检查 `self._abort` → 若中断则 break
  3. 记录 `tool_summary`（本轮所有工具结果的摘要，供 SOUL_PHASE）
- **验证**：`tests/agent/test_loop.py`：mock provider 返回两次 `tool_calls` 后返回空，断言事件序列为 `RunStarted → StepStarted(tool) → ToolCallStart → ToolCallResult → ToolCallEnd → (重复) → StepFinished(tool)`。

### Task 6.3 SOUL_PHASE 灵魂阶段实现

- **涉及文件**：`agent/loop.py`（追加）
- **修改点**：
  1. `yield StepStarted(phase="soul")`
  2. 调 `inject_soul_context(...)`（Task 5.3），用 memory_text（`ctx.memory.get_relevant_memories` 结果，按需可选）与 emotion_patch（M1 可传空）、tool_summary
  3. `async for token in service.astream_send(text, store_history=False)`：
     - `yield TextMessageStart(message_id)` → 每段 `yield TextMessageDelta` → 结束 `yield TextMessageEnd`
     - 支持 `self._abort` 中断：收到中断后 break，并 `yield` 一个 `notice` 提示
  4. `yield RunFinished(session_id=...)`
  5. 收尾副作用：`await ctx.memory.add_conversation_memory(text, full_reply, session_id=...)`（M1 在 service 关闭前调用，记忆写入失败不抛异常——GRAG 内部已捕获返回 bool）
- **验证**：`tests/agent/test_loop.py`：mock provider 流式返回若干 token，断言事件序列包含 `TextMessageStart → TextMessageDelta×n → TextMessageEnd → RunFinished`；mock memory 断言 `add_conversation_memory` 被调用。

### Task 6.4 工具确认的异步等待（O4）

- **涉及文件**：`agent/loop.py`（追加）、`agent/session.py`（见 Task 7）
- **修改点**：
  1. `AgentLoop` 暴露 `pending_confirmations: dict[str, asyncio.Future[bool]]`
  2. 遇到 `CONFIRM` 权限工具时：创建 `Future`，`yield ProtocolEvent(type=CONFIRM_REQUEST, payload={"tool": ..., "params": ...})`，`await future`；超时（如 30s）返回 `False`（拒绝）
  3. 提供 `async def resolve_confirmation(call_id: str, allowed: bool) -> None`：set future 结果
- **验证**：`tests/agent/test_loop.py`：启动循环到确认点，调用 `resolve_confirmation`，断言循环继续；不 resolve 时超时拒绝。

### Task 6.5 边界兜底（超时/最大轮数/降级）

- **涉及文件**：`agent/loop.py`（追加）
- **修改点**：
  1. 工具阶段单轮 `asyncio.wait_for(..., timeout=tool_timeout)`，超时 → 强制 break 进 SOUL_PHASE
  2. 连续超时计数 ≥ 3 → 强制 SOUL_PHASE 并带"任务中断"提示
  3. 达到 `max_tool_rounds` → 强制 SOUL_PHASE（含已完成步骤摘要）
  4. 工具 executor 抛异常 → 捕获，结果为 `[工具执行失败] {exc}` 文本，循环继续
  5. SOUL_PHASE 流式抛异常 → `yield ProtocolEvent(type=ERROR, payload={"message": ...})`，并 `yield RunFinished`（异常不逃逸）
- **验证**：`tests/agent/test_loop.py`：mock executor 抛异常 → 工具结果含失败文案；mock provider 超时 → 进入 SOUL_PHASE；异常传播路径正确。

---

## 7. 会话生命周期 `AgentSession`（QueryEngine 模式）

### Task 7.1 `AgentSession` 封装

- **涉及文件**：`agent/session.py`（新建）
- **修改点**：
  ```python
  class AgentSession:
      """一个对话线程对应一个实例，持有 AgentLoop + ConversationService"""

      def __init__(
          self,
          conversation_id: str,
          service: ConversationService,
          loop: AgentLoop,
      ) -> None:
          self.conversation_id = conversation_id
          self._service = service
          self._loop = loop
          self.usage = TokenUsage()

      @property
      def service(self) -> ConversationService:
          return self._service

      @property
      def loop(self) -> AgentLoop:
          return self._loop

      async def submit(self, text: str) -> AsyncGenerator[AgentEvent, None]:
          async for event in self._loop.submit_user_message(text):
              yield event

      def interrupt(self) -> None:
          self._loop.interrupt()

      def reset_abort(self) -> None:
          self._loop.reset_abort()
  ```
- **验证**：`tests/agent/test_session.py`：构造 mock loop，`submit` 转发事件；`interrupt`/`reset_abort` 透传。

### Task 7.2 `SessionManager` 多会话注册表

- **涉及文件**：`agent/session.py`（追加，或新建 `agent/session_store.py` 供 M2 用）
- **修改点**：
  ```python
  class SessionManager:
      """会话注册表：conversation_id → AgentSession（M1 内存态，M2 持久化）"""

      def __init__(self) -> None:
          self._sessions: dict[str, AgentSession] = {}

      def get_or_create(self, conversation_id: str, factory) -> AgentSession: ...
      def get(self, conversation_id: str) -> AgentSession | None: ...
      def remove(self, conversation_id: str) -> None: ...
      def close_all(self) -> None: ...
  ```
- **验证**：`tests/agent/test_session.py`：`get_or_create` 重复调用返回同一实例；`remove` 后 `get` 返回 None。

---

## 8. WS 网关（AG-UI 事件流）

### Task 8.1 FastAPI 应用与 `/agent/ws` 端点

- **涉及文件**：`agent/app.py`（新建）、`agent/ws.py`（新建）
- **修改点**：
  1. `agent/app.py`：
     ```python
     from fastapi import FastAPI
     from agent.ws import create_ws_router

     def create_app() -> FastAPI:
         app = FastAPI(title="Aliya Agent")
         app.include_router(create_ws_router())
         return app
     ```
  2. `agent/ws.py`：
     ```python
     from fastapi import APIRouter, WebSocket, WebSocketDisconnect

     def create_ws_router() -> APIRouter:
         router = APIRouter()

         @router.websocket("/agent/ws")
         async def agent_ws(ws: WebSocket):
             await ws.accept()
             ...
         return router
     ```
  3. 端点内维护连接级 `AgentSession`（经 `SessionManager`）与 `asyncio.Queue`（事件发送队列）
- **验证**：`tests/agent/test_ws.py`：用 FastAPI `TestClient` + `WebSocketTestSession` 连接 `/agent/ws`，断言可握手成功。

### Task 8.2 消息分发（客户端 → 后端）

- **涉及文件**：`agent/ws.py`（追加）
- **修改点**：`agent_ws` 接收循环中，按 `data["type"]` 分发：
  - `user_message` → `text = data["text"]` → 启动 `asyncio.create_task(self._run_agent(text))`
  - `stop` → `session.interrupt()`（打断当前轮；打断后由 agent 流程返回 notice）
  - `confirm_response` → `await loop.resolve_confirmation(data.get("call_id"), allowed=data.get("allowed", False))`
  - `get_emotion_state` / `get_token_usage` → M1 返回空/当前 usage 快照
  - `ping` → `{"type": "pong"}`
- **验证**：`tests/agent/test_ws.py`：发 `ping` 收 `pong`；发 `stop` 断言 `session.interrupt` 被调用（mock）。

### Task 8.3 事件转发（后端 → 客户端）

- **涉及文件**：`agent/ws.py`（追加）
- **修改点**：
  1. 发送循环：`event = await queue.get()` → 若为 `ProtocolEvent` 直接发送 `{"type": ..., **payload}`；若为 `AgentEvent` 先 `to_protocol()` 再发送
  2. 每次回复结束后发送 `TOKEN_USAGE`（payload：`{"total": session.usage.total, "input": ..., "output": ...}`）
  3. `RunFinished` 后若有确认待处理，忽略/超时
- **验证**：`tests/agent/test_ws.py`：mock agent 产出事件，断言客户端收到序列化 JSON 事件流。

### Task 8.4 服务入口 `main.py`

- **涉及文件**：`main.py`（新建，根目录）
- **修改点**：
  ```python
  """Aliya Agent 服务入口"""
  import uvicorn
  from agent.app import create_app
  from core.config import get_config_instance

  def main() -> None:
      cfg = get_config_instance("data/config/main.yml")
      host = cfg.get("cosmos.service.agent.ws_server.host", "127.0.0.1")
      port = int(cfg.get("cosmos.service.agent.ws_server.port", 8765))
      uvicorn.run(create_app(), host=host, port=port, log_level="info")

  if __name__ == "__main__":
      main()
  ```
  确保从项目根目录运行：`uv run python main.py`
- **验证**：`uv run python main.py` 启动无报错；另开终端 `uv run python -c "import websockets; ..."` 或直接用 GUI 连（Task 10 手动验收）。

---

## 9. GUI WS 层分步升级（O6）

> 目标：新增 AG-UI 事件流通道，旧 handler 逐步迁移。本 M1 完成聊天窗口主流程的事件流化。

### Task 9.1 主进程 WS 事件分发升级（`GUI/main/ws.js`）

- **涉及文件**：`GUI/main/ws.js`
- **修改点**：
  1. `WS_HANDLERS` 中新增 AG-UI 事件类型处理器：
     - `text_message_start` / `text_message_content` / `text_message_end` → 聚合为流式文本 → 推送 `chat:reply-delta`（增量）与 `chat:reply`（最终）
     - `tool_call_start` / `tool_call_result` → 推送 `chat:tool-call`（聊天窗口展示工具调用卡片）
     - `run_started` / `run_finished` → 维护 busy 状态推送 `chat:busy`
     - `confirm_request` → 沿用现有 `chat:confirm-request`
     - `error` / `notice` → 沿用现有 `chat:error` / `chat:notice`
     - `token_usage` → 沿用现有 accumulateToken
  2. 保留旧的 `brain_complete` / `emotion_changed` / `status_changed` / `tts_features` handler 不动（向后兼容）
- **验证**：`npm run dev`（GUI）启动后连接后端，手工验证普通对话流式渲染。

### Task 9.2 聊天窗口预加载桥接（`GUI/preload-chat.js`）

- **涉及文件**：`GUI/preload-chat.js`
- **修改点**：`chatAPI` 新增：
  ```js
  onReplyDelta: makeSubscriber('chat:reply-delta'),
  onToolCall: makeSubscriber('chat:tool-call'),
  onBusy: makeSubscriber('chat:busy'),
  ```
- **验证**：渲染进程 `typeof window.chatAPI.onReplyDelta === 'function'`。

### Task 9.3 聊天状态存储支持流式（`GUI/src/components/chat/useChatStore.js`）

- **涉及文件**：`GUI/src/components/chat/useChatStore.js`
- **修改点**：
  1. `chatStore` 新增 `streaming: null`（当前流式消息对象 `{ id, text }`）
  2. `onReplyDelta(data)`：若 `streaming` 为空则 `pushMessage('ai', data.text)` 并记录；否则 append 到 `streaming.text`
  3. `onReply(data)`：`streaming = null; chatStore.busy = false`（最终文本已包含全部内容）
  4. `onToolCall(data)`：`pushMessage('system', `调用工具：${data.tool_name}`)`（简化展示，M1 用 system 气泡）
  5. `onBusy(busy)`：`chatStore.busy = busy`
- **验证**：手工发送消息，观察消息逐字出现；工具调用时出现 system 气泡。

### Task 9.4 聊天面板展示（`GUI/src/components/chat/ChatPanel.vue`）

- **涉及文件**：`GUI/src/components/chat/ChatPanel.vue`
- **修改点**：模板/脚本中订阅新事件：
  ```js
  api?.onReplyDelta?.(onReplyDelta);
  api?.onToolCall?.(onToolCall);
  api?.onBusy?.(onBusy);
  ```
  消息气泡文本改为 `{{ msg.text }}`（已支持流式 append）；无需改 CSS
- **验证**：手工验收第 2 步。

---

## 10. 集成测试与收尾

### Task 10.1 `tests/agent/test_integration.py` 端到端（mock LLM + 内存 transport）

- **涉及文件**：`tests/agent/test_integration.py`（新建）
- **修改点**：模拟一次完整对话：
  1. 构造 mock provider（首次返回 `tool_calls` → `memory_query`，随后返回普通文本）
  2. 构造 `AgentLoop`（含 `ToolRegistry` + `PermissionChecker` + mock memory）与 `AgentSession`
  3. `async for event in session.submit("记得我昨天说的吗")`，断言事件序列：
     `RunStarted → StepStarted(tool) → ToolCallStart → ToolCallResult → ToolCallEnd → StepStarted(soul) → TextMessageStart → TextMessageDelta → TextMessageEnd → RunFinished`
  4. 断言 memory mock 的 `add_conversation_memory` 被调用且参数含用户输入与最终回复
- **验证**：`uv run pytest tests/agent -v` 全绿。

### Task 10.2 全量回归

- **涉及文件**：无
- **修改点**：运行
  - `uv run pytest tests/agent tests/llm tests/memory tests/tts tests/gui tests/vector -v`
  - `uv run pytest --cov=agent --cov=core --cov-report=term-missing`
- **验证**：全部通过，`agent` 模块覆盖率不为 0。

### Task 10.3 手动端到端验收

- **涉及文件**：无
- **修改点**：按本计划第 0 节"成功标准"逐条手工验收（需 Neo4j 或 GRAG 禁用时 memory 工具返回降级文案）。
- **验证**：成功标准 1-7 全部满足。

---

## 11. 里程碑完成标准（M1）

- [ ] `uv run pytest tests/agent tests/llm -v` 全绿
- [ ] `uv run pytest --cov=agent --cov=core` 覆盖率不因重构下降
- [ ] GUI 端普通对话流式渲染、工具调用可见、确认流程通、stop 生效
- [ ] `uv run python main.py` 可启动，GUI 自动连接
- [ ] 手动验收 7 条全部通过

## 11.5 M2-M4 后续任务概览（M1 完成后展开为同等粒度计划）

> 以下任务在 M1 验收通过后，按相同模板（涉及文件 → 修改点 → 验证）细化。

### M2 伴侣能力
- 情绪引擎：`agent/emotion/engine.py`（状态机 + 分类器），`main.yml` 增加 emotion 配置段，情绪补丁注入 SOUL_PHASE（`set_emotion_patch`）
- 主动聊天：`agent/proactive/`（触发器：定时/静默检测/环境事件；护栏：不打搅时段、对话中不触发；渠道路由）
- 多会话历史：`agent/session_store.py`（JSON 持久化，标题派生、updatedAt 排序），GUI 会话列表

### M3 知识外扩
- RAG 文档库：`agent/rag/`（文档导入 + 向量检索，对接 `core/vector`）
- Skill 系统：`agent/skills/`（`invoke_skill` / `read_skill_reference` 工具）
- MCP 客户端：`agent/mcp/`（stdio / SSE / HTTP 传输，工具动态注册进 `ToolRegistry`）

### M4 多渠道
- `agent/channels/`：飞书 / 微信适配器（复用 `AgentSession`，仅替换事件源与 EventSink）
- 凭据安全：渠道 token 用系统 keyring 加密（O8）

## 12. 参考对照

| 设计点 | 来源 |
|---|---|
| 两阶段 FC 循环 | `example/Cyrene-Agent-master/src/main/orchestrator/two-phase-fc-loop.ts` |
| AG-UI 事件流 + 双层事件 | `Cyrene-Agent` cyrene-agent.ts + `@ag-ui/core` |
| 会话生命周期 / QueryEngine | `example/claude-code/src/QueryEngine.ts` |
| 工具权限 | 现有 `data/config/Permissions.yml` + claude-code 权限体系 |
| 上下文注入复用 | `core/llm/service.py` 的 `set_context_injection` / `set_system_prompt` |
