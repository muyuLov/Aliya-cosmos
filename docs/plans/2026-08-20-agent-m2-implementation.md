# Aliya-cosmos Agent 层 M2 伴侣能力实施计划

- 日期：2026-08-20
- 前置文档：
  - `docs/plans/2026-08-20-agent-architecture-design.md`（架构蓝图，M2 = 伴侣能力）
  - `docs/plans/2026-08-20-agent-m1-implementation.md`（M1 基础闭环，本计划依赖其交付物）
- 目标范围：M2 伴侣能力 = **情绪引擎 + 主动聊天 + 多会话历史**
- 技术栈：Python（继承 core/）、asyncio、FastAPI（沿用 M1）

---

## 0. 前置依赖（M1 已实施完成）

M1 已实施完成并全量通过（373 tests）。本计划基于 M1 的实际接口（有实施偏差修正，与 M1 计划文档略有差异，以本表为准）：

1. `agent/events.py`：进程内 `AgentEvent`（`RunStarted`/`StepStarted`/`ToolCall*`/`TextMessage*`/`RunFinished`）+ 线上 `ProtocolEvent(type, payload)` + `to_protocol()` 映射 + `EventSink` 接口
2. `agent/loop.py`：`AgentLoop.__init__(service, registry, checker, context, *, max_tool_rounds=20, tool_timeout=30.0, confirm_timeout=30.0, memory=None)`；`_tool_phase(text, tool_summary_parts)` / `_soul_phase(text, tool_summary_parts, interrupted)` 两个子生成器；`submit_user_message` 产出 `AsyncGenerator[AgentEvent | ProtocolEvent]`；`pending_confirmations: dict[str, Future[bool]]` + `resolve_confirmation(call_id, allowed)`
3. `agent/session.py`：`AgentSession(conversation_id, service, loop)`（含 `service`/`loop` property、`submit` 生成器、`interrupt`/`reset_abort`）+ `SessionManager`（`get_or_create(factory)` / `get` / `remove` / `close_all`，内存态）
4. `agent/ws.py`：`create_ws_router(session_factory=None)`；接收循环分发 `user_message`/`stop`/`confirm_response`/`ping`/`get_emotion_state`（**当前 `get_emotion_state` 为 `pass` 占位**）/`close`；发送循环经队列把 `AgentEvent`→`to_protocol`、`ProtocolEvent` 直接发送；连接级 `conversation_id = str(uuid.uuid4())`（M2 Part C 需改造为可携带会话 ID）
5. `core/llm/service.py`：`set_emotion_patch(patch)` 与 `_clear_patches()`（每轮 `astream_send` finally 自动清除）——**已存在，无需改动**
6. GUI WS 层：M1 已支持 AG-UI 事件流，WS_HANDLERS 新增 `run_started`/`step_*`/`text_message_*`/`tool_call_*` 转发至 `chat:stream-*` 通道；`emotion_changed`（读取 `data.emotion`/`data.feeling` + `data.scores`）与 `emotion_state`（读取 `data.dominant`）为旧 handler，**已保留，M2 直接复用**
7. `AgentLoop._soul_phase` 中记忆注入已落地：`self.memory.get_relevant_memories(query=text, limit=3)` → 五元组格式化 → `inject_soul_context(memory_text=...)`；当前 `emotion_patch=""` 硬编码，**M2 注入点即在此**

## 0.1 成功标准（M2 验收清单）

1. 情绪引擎：对话中发送"我好开心！"→ 侧边栏/Live2D 情绪变为 happy 并带分数；发送负面消息情绪相应变化
2. 情绪持久：`set_emotion_patch` 注入生效（模型回复带情绪色彩），且每轮后自动清除不残留
3. 情绪可查：`get_emotion_state` 请求返回当前 `dominant` 与 `scores`
4. 主动聊天：设置打开主动聊天后，到达配置的时间点 Aliya 主动发消息；处于"不打搅时段"或"对话进行中"时不触发
5. 多会话：GUI 聊天窗口显示会话列表，可新建/切换/删除会话；切换后消息历史各自独立；重启后端后会话列表保留
6. `pytest tests/agent` 全绿（M2 新增测试不破坏 M1 测试）

---

## Part A · 情绪引擎（`agent/emotion/`）

> 参考：旧 agent 的 `emotion/engine.py` + `emotion/vad.py`（已删除，从零重建）；`core/llm/service.py` 的 `set_emotion_patch`/`_clear_patches` 机制已就绪。

### Task A1 情绪标签定义与配置

- **涉及文件**：
  - `agent/emotion/__init__.py`（新建，导出 `EmotionState`、`EmotionEngine`、`VAD_EMOTIONS`、`create_emotion_engine`）
  - `agent/emotion/emotion_state.py`（新建）
  - `data/config/main.yml`（修改，新增 `cosmos.service.agent.emotion:` 配置段）
- **修改点**：
  1. `emotion_state.py` 定义情绪常量（**与 GUI `emotion-map.js` 的 19 个标签严格一致**）：
     ```python
     VAD_EMOTIONS: tuple[str, ...] = (
         "neutral", "calm", "happy", "excited", "shy", "affectionate",
         "curious", "confused", "tired", "sad", "anxiety", "anger",
         "concerned", "surprised", "bored", "grateful", "relieved", "disgusted",
     )
     ```
  2. 定义情绪状态数据类：
     ```python
     @dataclass
     class EmotionState:
         dominant: str = "neutral"
         scores: dict[str, float] = field(default_factory=dict)  # 各情绪 0~1
     ```
  3. `main.yml` 新增：
     ```yaml
     emotion:
       enabled: true
       fallback: neutral                 # 无结果时的默认情绪
     ```
- **验证**：`tests/agent/test_emotion.py`：断言 `VAD_EMOTIONS` 含 `neutral`/`happy`/`sad`，`EmotionState()` 默认 `dominant == "neutral"`。

### Task A2 规则化情绪判定引擎

- **涉及文件**：`agent/emotion/engine.py`（新建）
- **修改点**：
  1. 实现 `EmotionEngine`：
     ```python
     class EmotionEngine:
         def __init__(self, config: dict | None = None) -> None:
             self._state = EmotionState()
             self._fallback = (config or {}).get("fallback", "neutral")

         @property
         def state(self) -> EmotionState:
             return self._state

         def update(self, text: str) -> EmotionState:
             """根据用户输入文本规则判定情绪，更新状态并返回"""
             # 关键词规则（M2 先做轻量规则版，M3 可替换为向量分类器）：
             #   happy/excited: 开心|太好了|哈哈|高兴|棒
             #   sad: 难过|伤心|难受|低落|委屈
             #   angry/anger: 生气|气死|愤怒|讨厌
             #   confused: 不懂|困惑|迷茫|为什么
             #   grateful: 谢谢|感谢
             #   tired: 累|疲惫|困
             # 命中多个时取强度最高者；未命中保持当前状态或回落到 fallback
         ```
  2. 判定规则用关键词表常量 `_RULES: list[tuple[str, tuple[str, ...]]]`（情绪 → 关键词元组），保证可测试
  3. `update()` 返回新的 `EmotionState`（dominant + scores），scores 为关键词命中数与最大命中数的归一化
- **验证**：`tests/agent/test_emotion.py`：
  - `update("我今天好开心！")` → `dominant == "happy"`
  - `update("我很难过")` → `dominant == "sad"`
  - `update("谢谢你的帮助")` → `dominant == "grateful"`
  - `update("中午吃了个苹果")`（中性）→ 保持当前/fallback
- **说明**：M2 用规则版保证确定性（可单测）；M3 再引入向量分类器升级（预留 `update` 接口不变）。

### Task A3 情绪补丁生成与注入

- **涉及文件**：`agent/emotion/engine.py`（追加）
- **修改点**：
  1. 新增方法：
     ```python
     def build_patch(self, user_text: str) -> str:
         """根据用户输入生成情绪补丁文本，供 set_emotion_patch 注入"""
         state = self.update(user_text)
         if state.dominant == "neutral":
             return ""
         return f"当前情绪：{state.dominant}（本轮回复请自然体现此情绪，不要生硬提及标签本身）"
     ```
  2. 追加方法 `to_payload() -> dict`：`{"emotion": self._state.dominant, "scores": self._state.scores}`，供 WS 事件使用
- **验证**：`tests/agent/test_emotion.py`：`build_patch("我好开心")` 返回含 `happy` 的文本；`build_patch("普通消息")` 返回空字符串。

### Task A4 情绪事件接入 `AgentLoop`（SOUL_PHASE 注入）

- **涉及文件**：`agent/loop.py`（修改）
- **修改点**（对齐实际 M1 签名）：
  1. `AgentLoop.__init__` 增加可选参数 `emotion_engine: EmotionEngine | None = None`（缺省 `None`，M1 测试不破坏）：
     ```python
     def __init__(self, service, registry, checker, context, *, max_tool_rounds=20,
                  tool_timeout=30.0, confirm_timeout=30.0, memory=None,
                  emotion_engine: EmotionEngine | None = None):
         ...
         self.emotion_engine = emotion_engine
     ```
  2. `_soul_phase` 中，在 `inject_soul_context(...)` 调用前（记忆检索之后、`emotion_patch=""` 处）改为：
     ```python
     emotion_patch = ""
     if self.emotion_engine:
         emotion_patch = self.emotion_engine.build_patch(text)  # text = 用户本轮输入
         if emotion_patch:
             await self.service.set_emotion_patch(emotion_patch)
     await inject_soul_context(
         self.service, self.context,
         memory_text=memory_text,
         emotion_patch=emotion_patch,     # 原硬编码 "" 改为变量
         tool_summary=tool_summary,
     )
     ```
  3. `_soul_phase` 流式结束、`RunFinished` 前（`TextMessageEnd` 之后）追加事件：
     ```python
     if self.emotion_engine:
         payload = self.emotion_engine.to_payload()  # {"emotion": dominant, "scores": {...}}
         yield ProtocolEvent(type="emotion_changed", payload=payload)
     ```
  4. `_clear_patches` 由 `ConversationService.astream_send` 的 finally 自动调用（M1 已确认），无需 loop 手动清理
- **验证**：`tests/agent/test_loop.py`（追加）：构造带 `emotion_engine` 的 loop，mock provider，断言 `set_emotion_patch` 被调用且事件序列含 `emotion_changed`；不带 `emotion_engine` 时无 `emotion_changed` 事件（M1 回归）。

### Task A5 情绪状态查询接入 WS

- **涉及文件**：`agent/ws.py`（修改）、`agent/app.py`（修改）
- **修改点**：
  1. **装配改造（影响后续 Part B/C）**：`_default_session_factory()` 改造为从应用级单例读取依赖，新增应用装配入口 `agent/app.py` 中 `create_app()` 创建并持有 `EmotionEngine` / `ProactiveScheduler`（M2）/ `SessionStore`（M2 Part C）；`create_ws_router(session_factory=None)` 保持签名，会话工厂改为由 app 装配时传入（沿用 M1 的注入模式，`get_emotion_state` 分支通过 `emotion_engine` 闭包捕获）
  2. `get_emotion_state` 分支（当前为 `pass` 占位）替换为：
     ```python
     elif mtype == "get_emotion_state":
         payload = emotion_engine.to_payload()  # {"emotion": dominant, "scores": {...}}
         # GUI ws.js 的 emotion_state handler 读取 data.dominant；状态面板读 emotion/scores
         await ws.send_json({"type": "emotion_state", "dominant": payload["emotion"], **payload})
     ```
  3. 事件转发循环中：`ProtocolEvent(type="emotion_changed")` 直接透传（loop 直接 yield 线上事件，WS 发送循环对 `ProtocolEvent` 直接发送 `{"type": ..., **payload}`，扁平化后 `data.emotion`/`data.scores` 被 GUI `emotion_changed` handler 正确解析）
- **验证**：`tests/agent/test_ws.py`（追加）：发 `get_emotion_state`，断言收到 `emotion_state` 且含 `emotion` + `dominant` 键；`emotion_changed` 事件透传后 GUI `ws.js` 的 `emotion_changed` handler 可解析 `emotion` + `scores`。

---

## Part B · 主动聊天（`agent/proactive/`）

> 参考：Cyrene `scheduler` + `proactive` 模块。原则：触发器定义"何时"、护栏定义"是否允许"、路由定义"投递到哪"。

### Task B1 配置段与触发器模型

- **涉及文件**：
  - `agent/proactive/__init__.py`（新建，导出 `ProactiveScheduler`、`create_proactive_scheduler`）
  - `agent/proactive/scheduler.py`（新建）
  - `data/config/main.yml`（修改，新增 `cosmos.service.agent.proactive:` 配置段）
- **修改点**：
  1. `main.yml` 新增：
     ```yaml
     proactive:
       enabled: false                     # 默认关闭，GUI 设置页可开
       check_interval_seconds: 60         # 调度轮询间隔
       quiet_hours:                       # 不打搅时段（本地时间）
         start: "23:00"
         end: "07:00"
       idle_timeout_minutes: 30           # 用户无操作静默超时后触发
       triggers:                          # 触发器配置
         - type: schedule                 # 定时触发（每天固定时刻）
           at: "20:00"
           message: "晚上好呀，今天过得怎么样？"
         - type: idle                     # 静默超时触发
           message: "看你半天没说话，是在忙吗？"
     ```
  2. `scheduler.py` 定义触发器数据类与解析：
     ```python
     @dataclass
     class TriggerConfig:
         type: str          # "schedule" | "idle"
         at: str | None = None       # HH:MM，schedule 用
         message: str = ""
     ```
- **验证**：`tests/agent/test_proactive.py`：解析配置构造 `TriggerConfig`，断言字段正确；配置缺省时 `enabled=False`。

### Task B2 护栏逻辑（quiet_hours / 对话中 / 连续未回复）

- **涉及文件**：`agent/proactive/scheduler.py`（追加）
- **修改点**：
  1. 定义护栏检查：
     ```python
     def _in_quiet_hours(now: datetime, quiet_hours: dict) -> bool: ...
     def _is_processing(session: AgentSession) -> bool: ...   # 通过 loop 的 run 状态判断
     ```
  2. `ProactiveScheduler` 状态字段：`last_trigger_time`、`_processing`、`_last_user_message_time`、`_triggered_count`（连续触发计数）
  3. 触发资格判定 `can_trigger(trigger, now) -> bool`：
     - `schedule`：当前时刻 == `at`（分钟精度，且当天未触发过）
     - `idle`：`now - last_user_message_time >= idle_timeout_minutes`
     - 公共护栏：不在 quiet_hours；`_processing` 为 False；`_triggered_count < 3`（连续未回复不触发）
- **验证**：`tests/agent/test_proactive.py`：
  - 23:30 时 `_in_quiet_hours` 为 True
  - `_processing=True` 时 `can_trigger` 为 False
  - 触发 3 次后 `can_trigger` 为 False

### Task B3 调度循环与渠道路由

- **涉及文件**：`agent/proactive/scheduler.py`（追加）、`agent/app.py`（修改）
- **修改点**：
  1. `ProactiveScheduler`：
     ```python
     class ProactiveScheduler:
         def __init__(self, config: dict, sessions: SessionManager, sink: Callable[[str], Awaitable[None]]) -> None:
             """sink：主动消息投递回调（默认 = 发给 GUI 当前连接）"""
         async def start(self) -> None:
             """后台任务：每 check_interval_seconds 轮询一次，can_trigger 通过则调用 self._send(message)"""
         async def stop(self) -> None:
             """取消后台任务"""
         async def notify_user_message(self, session_id: str) -> None:
             """用户发消息时调用：刷新 last_user_message_time、_processing、重置触发计数"""
     ```
  2. 路由：M2 仅支持"投递到当前 GUI 连接"（`sink` 默认实现向所有活跃 ws 连接发 `NOTICE` 事件带消息文本）；飞书/微信路由留到 M4（`sink` 可替换）
  3. `agent/app.py`：`create_app()` 装配 `ProactiveScheduler`，提供 `start`/`stop` 生命周期钩子（FastAPI `lifespan`）
- **验证**：`tests/agent/test_proactive.py`：mock sink，调度 `schedule` 触发器到点时调用 sink；`notify_user_message` 后 idle 触发被推迟。

### Task B4 WS 接入（触发条件上报 + 主动消息广播）

- **涉及文件**：`agent/ws.py`（修改）
- **修改点**（对齐实际 `ws.py` 结构）：
  1. **广播注册表**：`create_ws_router` 增加可选参数 `proactive_scheduler`；WS 发送循环所在函数维护 `_active_ws: set[WebSocket]`（连接 accept 后 add、断开时 discard），供主动消息广播用
  2. 收到 `user_message` 时调用 `scheduler.notify_user_message(session_id)`（刷新静默计时）
  3. 收到 `set_proactive` 消息时更新配置 `enabled`（GUI 设置页调用）：
     ```python
     elif mtype == "set_proactive":
         enabled = bool(data.get("enabled", False))
         await proactive_scheduler.set_enabled(enabled)
     ```
  4. **主动消息投递**：`ProactiveScheduler` 的 `sink` 回调实现 = 向 `_active_ws` 中所有连接发送 `{"type": "notice", "payload": {"message": "...", "proactive": True}}`（非逐连接状态机，复用事件流；GUI `ws.js` 已有 `notice` handler 映射 `chat:notice`）
- **验证**：`tests/agent/test_ws.py`（追加）：发 `user_message` 断言 `notify_user_message` 被调用；发 `set_proactive` 断言配置更新；mock 广播发送函数断言主动消息被推送给活跃连接。

---

## Part C · 多会话历史（`agent/session_store.py`）

> 参考：Cyrene `conversationManager`（JSON 持久化）。M1 的 `SessionManager` 为内存态，本 Part 升级为持久化 + 列表管理。

### Task C1 会话元数据模型与 JSON 持久化

- **涉及文件**：`agent/session_store.py`（新建）
- **修改点**：
  1. 会话元数据：
     ```python
     @dataclass
     class ConversationMeta:
         conversation_id: str
         title: str
         created_at: str          # ISO 8601
         updated_at: str          # ISO 8601
     ```
  2. `SessionStore`：
     ```python
     class SessionStore:
         def __init__(self, path: str = "data/sessions.json") -> None:
             self._path = path
             self._conversations: dict[str, ConversationMeta] = {}

         def load(self) -> None: ...
         def save(self) -> None: ...
         def list_meta(self, sort_by_updated: bool = True) -> list[ConversationMeta]: ...
         def upsert(self, meta: ConversationMeta) -> None: ...
         def delete(self, conversation_id: str) -> None: ...
         def get(self, conversation_id: str) -> ConversationMeta | None: ...
     ```
  3. `data/sessions.json` 由程序自动创建（不存在时返回空列表）
- **验证**：`tests/agent/test_session_store.py`（新建）：`tmp_path` 下 upsert/list/delete/save/load 往返正确；`list_meta` 按 `updated_at` 倒序。

### Task C2 标题自动派生与更新时间刷新

- **涉及文件**：`agent/session_store.py`（追加）、`agent/session.py`（修改）
- **修改点**：
  1. `SessionStore.derive_title(text: str) -> str`：取用户首条消息前 20 字符（超出加省略号），strip 后为空则 `"新会话"`（M2 用简单截断，不调 LLM）
  2. `SessionManager` 升级为持久化感知：构造时传入 `store: SessionStore | None = None`；`get_or_create(factory)` 中若会话不存在且 store 有该 ID 元数据则恢复（`conversation_id` 已存在 → 复用），新建时 `store.upsert(meta)`（created_at/updated_at=now，title 为空占位）
  3. **标题派生与刷新时机**（对齐 M1 的 `AgentSession.submit` 流程）：`AgentSession.submit()` 生成器**首次 yield 前**，若当前会话元数据 title 为空且 store 可用，用 `derive_title(text)` 设置并 `store.upsert`；`RunFinished` 后 `store.upsert(updated_at=now)`（仅刷新时间，不改标题）
  4. `SessionStore` 路径可从 `main.yml` 读取（`cosmos.service.agent.session_store.path`，默认 `data/sessions.json`）
  5. 注意 M1 测试兼容：`SessionManager` 新增参数带默认值，`get_or_create` 行为在无 store 时与 M1 完全一致
- **验证**：`tests/agent/test_session_store.py`：`derive_title("帮我写个计划好吗？")` 返回截断标题；首条消息派生标题后再次 submit 不改标题。

### Task C3 会话列表/切换/删除的 WS 协议

- **涉及文件**：`agent/ws.py`（修改）
- **修改点**（对齐实际 `ws.py` 结构）：
  1. **连接级会话 ID 改造**：当前 `conversation_id = str(uuid.uuid4())` 改为从首个客户端消息获取——接收循环读取第一条消息的 `data.get("conversation_id")`，缺省 `"default"`；`session_factory(conversation_id)` 调用前先确定 ID（M1 中 factory 按 ID 创建 `ConversationService`，天然支持按 ID 复用/新建）
  2. 新增消息类型分发（在 `mtype` 分支链中追加）：
     - `list_conversations` → 返回 `{"type": "conversation_list", "payload": {"items": [meta]}}`
     - `create_conversation` → 新建会话（`store.upsert` + 返回新 ID），返回 `conversation_created`（含新 conversation_id）
     - `switch_conversation` → 携带 `conversation_id`，切换当前活跃会话（`SessionManager.get_or_create(factory)` 复用/新建 `ConversationService`）
     - `delete_conversation` → 删除会话（`store.delete` + `SessionManager.remove`），返回 `conversation_deleted`
     - `get_history` → 返回当前会话历史（`session.service.get_history()` 序列化，供 GUI 打开时恢复）
  3. `user_message` 携带 `conversation_id` 时切换对应会话（若该 ID 不存在则视为新建）
- **验证**：`tests/agent/test_ws.py`（追加）：`list_conversations` 返回空列表；`create_conversation` 后 `list_conversations` 含 1 项；`delete_conversation` 后列表清空。

### Task C4 GUI 会话列表（升级）

- **涉及文件**：
  - `GUI/main/ws.js`（修改，新增会话列表事件 handler）
  - `GUI/preload-chat.js`（修改，新增 `listConversations`/`createConversation`/`switchConversation`/`deleteConversation` 发送方法）
  - `GUI/src/components/chat/useChatStore.js`（修改，新增 `conversations` 状态）
  - `GUI/src/components/chat/ChatPanel.vue`（修改，新增会话列表 UI）
- **修改点**：
  1. `ws.js` 新增 handler：
     - `conversation_list` → 推送 `chat:conversations`
     - `conversation_created` / `conversation_deleted` → 刷新列表
  2. `preload-chat.js` 新增 `chatAPI` 方法（发送对应消息类型）
  3. `useChatStore.js`：`conversations: []`、`activeConversationId: 'default'`；方法 `loadConversations`/`createConversation`/`switchConversation`/`deleteConversation`；切换时清空当前消息数组并请求 `get_history`
  4. `ChatPanel.vue`：侧边栏会话列表（新建按钮 + 列表项 + 删除按钮），样式沿用现有设计语言
- **验证**：手工验收第 5 步；`npm run build` 无报错。

---

## 集成与收尾

### Task I1 集成测试：情绪 + 多会话 + 主动聊天联动

- **涉及文件**：`tests/agent/test_integration_m2.py`（新建）
- **修改点**：模拟完整场景：
  1. 构造 `EmotionEngine` + `AgentLoop`（带 emotion）+ `SessionStore`（tmp 路径）+ `SessionManager`
  2. 会话 1：`submit("我今天好开心")` → 断言事件序列含 `emotion_changed(happy)`、会话标题派生
  3. 会话 2：切换后 `submit("我很难过")` → 断言 `emotion_changed(sad)`，两个会话历史独立
  4. `SessionStore.save/load` 后元数据完整
- **验证**：`uv run pytest tests/agent/test_integration_m2.py -v` 全绿。

### Task I2 全量回归

- **涉及文件**：无
- **修改点**：运行 `uv run pytest tests/agent tests/llm tests/memory -v`；`uv run pytest --cov=agent --cov=core --cov-report=term-missing`
- **验证**：全部通过，M1 测试不回归。

### Task I3 手动端到端验收

- **涉及文件**：无
- **修改点**：按第 0.1 节成功标准逐条手工验收（需后端运行 + GUI 启动）。
- **验证**：成功标准 1-6 全部满足。

---

## 里程碑完成标准（M2）

- [ ] `uv run pytest tests/agent -v` 全绿（含新增 M2 测试，M1 无回归）
- [ ] GUI 侧边栏/Live2D 情绪随对话变化（`emotion_changed` 事件链路通）
- [ ] 情绪补丁注入生效且每轮自动清除
- [ ] 主动聊天可配置、护栏生效、消息可达 GUI
- [ ] 会话列表可新建/切换/删除，重启后保留
- [ ] 手动验收 6 条全部通过

## 参考对照

| 设计点 | 来源 |
|---|---|
| 情绪标签（19 个 VAD 标签） | `GUI/src/live2d/emotion-map.js`（`FEELING_TO_EMOTION` 的 key） |
| 情绪事件 payload 格式 | `GUI/main/ws.js` `emotion_changed` handler（`emotion` + `scores`） |
| `set_emotion_patch` / `_clear_patches` | `core/llm/service.py`（已存在，每轮自动清理） |
| 主动聊天（scheduler + proactive + 护栏） | `example/Cyrene-Agent-master` scheduler/proactive 模块 |
| 多会话 JSON 持久化 | `example/Cyrene-Agent-master` conversationManager |
| 情绪补丁自动清除 | `core/llm/service.py` `astream_send` finally 中 `_clear_patches()` |
