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

1. 情绪引擎：对话中发送"我好开心！"→ 侧边栏/Live2D 情绪变为 happy 并带分数；发送负面消息情绪相应变化（情绪由 LLM 心情观察器判定 + 平滑器累积，非关键词跳变）
2. 情绪持久：语气注入生效（模型回复带情绪色彩/语气规则），且每轮后自动清除不残留；连续对话同一情绪稳定，轻微单次观测不导致情绪跳变
3. 情绪可查：`get_emotion_state` 请求返回当前 `dominant` 与 `scores`
4. 主动聊天：设置打开主动聊天后，到达配置的时间点 Aliya 主动发消息；处于"不打搅时段"或"对话进行中"时不触发
5. 多会话：GUI 聊天窗口显示会话列表，可新建/切换/删除会话；切换后消息历史各自独立；重启后端后会话列表保留
6. `pytest tests/agent` 全绿（M2 新增测试不破坏 M1 测试）

---

## Part A · 情绪引擎（`agent/emotion/`）

> 参考：**`example/Cyrene-Agent-master` 的三层机制**——①`runtime-state-smoother.ts`（状态平滑器）②`observeRuntimeState`（LLM 心情观察器）③`tone-injector.ts`（embedding 场景匹配 + 语气注入）。Cyrene 参考的是**机制**，情绪**标签体系沿用 Aliya GUI 已固定的 19 个英文 VAD 标签**（`GUI/src/live2d/emotion-map.js` 的 `FEELING_TO_EMOTION` key，GUI 契约不可破坏）。`core/llm/service.py` 的 `set_emotion_patch`/`_clear_patches` 机制已就绪。

### Task A1 情绪标签定义 + 配置

- **涉及文件**：
  - `agent/emotion/__init__.py`（新建，导出 `EmotionState`、`EmotionEngine`、`FeelingScores`、`VAD_EMOTIONS`、`create_emotion_engine`）
  - `agent/emotion/emotion_state.py`（新建）
  - `data/config/main.yml`（修改，新增 `cosmos.service.agent.emotion:` 配置段）
- **修改点**：
  1. `emotion_state.py` 定义情绪常量（**与 GUI `emotion-map.js` 的 19 个 key 严格一致，含 `angry` 别名**）：
     ```python
     VAD_EMOTIONS: tuple[str, ...] = (
         "neutral", "calm", "happy", "excited", "shy", "affectionate",
         "curious", "confused", "tired", "sad", "anxiety", "anger",
         "concerned", "surprised", "bored", "grateful", "relieved", "disgusted",
     )
     # GUI emotion-map.js 中 angry 是 anger 的别名，归一化用
     EMOTION_ALIASES: dict[str, str] = {"angry": "anger"}
     ```
  2. 定义情绪分数与状态数据类（对齐 Cyrene `runtime-state-smoother.ts`）：
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
       fast_rise: ["sad", "anxiety", "anger", "disgusted"]  # 负面快速上升（对应 Cyrene FAST_RISE）
       observer_weight: 0.3              # 常规观测权重（对应 Cyrene 0.3）
       fast_rise_weight: 0.62            # 快速上升权重（对应 Cyrene 0.62）
       scene_match_threshold: 0.72       # 场景匹配阈值（对应 Cyrene SCENE_MATCH_THRESHOLD）
     ```
- **验证**：`tests/agent/test_emotion.py`：断言 `VAD_EMOTIONS` 含 `neutral`/`happy`/`sad`，`EMOTION_ALIASES["angry"] == "anger"`，`EmotionState()` 默认 `dominant == "neutral"`。

### Task A2 状态平滑器 `smooth_feeling`（移植 Cyrene `smoothFeeling`）

- **涉及文件**：`agent/emotion/smoother.py`（新建）
- **修改点**：
  1. 移植 Cyrene `runtime-state-smoother.ts` 的算法（**核心：情绪不是跳变，而是按权重平滑累积，负面情绪快速上升**）：
     ```python
     def create_feeling_scores(initial: str = "neutral") -> dict[str, float]:
         """全 0，初始情绪为 1（对齐 Cyrene createFeelingScores）"""
         scores = {e: 0.0 for e in VAD_EMOTIONS}
         scores[initial] = 1.0
         return scores

     def smooth_feeling(
         current: dict[str, float],
         observed: str,
         *,
         fast_rise: set[str] | None = None,
         observer_weight: float = 0.3,
         fast_rise_weight: float = 0.62,
     ) -> EmotionState:
         """对齐 Cyrene smoothFeeling：
         - 观测情绪非合法标签时归一化（angry→anger），仍非法则回落 fallback
         - 所有分数按 (1 - weight) 衰减，观测目标加权累积
         - 负面情绪（sad/anxiety/anger/disgusted）快速上升
         - dominant = 分数最高的标签"""
     ```
  2. 抽成纯函数，便于单测；`EmotionEngine` 持有 `self._scores = create_feeling_scores()`
- **验证**：`tests/agent/test_emotion.py`（对齐 Cyrene `runtime-state-smoother.test.ts` 的三个断言）：
  - 一次轻微观测不翻转 dominant：`create_feeling_scores("neutral")` → `smooth_feeling(scores, "happy")` 后 `dominant` 仍为 `"neutral"`，但 `scores["happy"] > 0`
  - 连续多次一致观测后翻转：`smooth` 三次 `"happy"` → `dominant == "happy"`
  - 负面快速上升：`create_feeling_scores("neutral")` → `smooth_feeling(scores, "sad")` 一次即 `dominant == "sad"`

### Task A3 LLM 心情观察器 `EmotionObserver`（移植 Cyrene `observeRuntimeState`）

- **涉及文件**：`agent/emotion/observer.py`（新建）
- **修改点**：
  1. 定义观测器（对齐 Cyrene `observeRuntimeState`：后台 LLM 判定 + JSON 解析 + 失败保留现状）：
     ```python
     class EmotionObserver:
         """LLM 心情观察器：后台队列调 LLM 判定 Aliya 当前情绪，JSON 解析后交给平滑器。

         关键约束：**不得通过 ConversationService.asend/asend_chat 调用 LLM**——
         它们会在 finally 触发 _clear_patches()（与主对话并发时竞争补丁）、累计 usage 到主会话、
         且走指数退避重试（观察器不需要）。改为直接调 service.provider.async_chat_completion()，
         构造独立 ChatRequest（不进历史、不碰补丁、不污染 usage）。
         """
         def __init__(self, service: ConversationService, *,
                      model: str | None = None,   # 缺省复用 service.provider.model
                      prompt_template: str | None = None,
                      timeout: float = 30.0) -> None:
             self._provider = service.provider      # 持有 provider，非 service
             self._model = model or service.provider.model
             self._prompt = prompt_template or self._DEFAULT_PROMPT   # 缺省用内建模板
             self._timeout = timeout
             self._on_feeling: Callable[[str], Awaitable[None]] | None = None
             self._queue: asyncio.Queue[str] = asyncio.Queue()   # 待观察的对话文本
             self._worker_task: asyncio.Task | None = None
             self.usage = TokenUsage()              # 观察 token 独立统计（不污染会话 usage）
             ...

             # _DEFAULT_PROMPT 完整内容 = 下方第 3 点 system prompt 模板（实现时照抄）

         async def start(self) -> None:
             """启动单 worker 协程（app 装配时调用一次）"""
             self._worker_task = asyncio.create_task(self._worker())

         async def stop(self) -> None:
             """取消 worker（app 关闭时调用）"""
             if self._worker_task:
                 self._worker_task.cancel()

         def set_callback(self, on_feeling: Callable[[str], Awaitable[None]]) -> None:
             """注册观察结果回调（EmotionEngine.apply_observation 传入）"""
             self._on_feeling = on_feeling

         async def observe(self, recent_dialogue: list[dict]) -> None:
             """入队后台任务（串行队列，避免并发触发限流）；
             失败捕获并保留当前情绪（对齐 Cyrene .catch 吞错误）"""
             if not self._on_feeling:
                 logger.debug("观察器未注册回调，跳过观察")
                 return
             text = self._format_dialogue(recent_dialogue)   # 对齐 Cyrene slice(-6)：取最近 6 条
             await self._queue.put(text)

         async def _worker(self) -> None:
             """单 worker：串行消费队列，避免观察请求与主对话并发打爆 provider"""
             while True:
                 try:
                     text = await self._queue.get()
                 except asyncio.CancelledError:
                     return
                 try:
                     await self._run_observation(text)
                 except Exception:
                     logger.exception("情绪观察协程异常，跳过该次观察")
                 finally:
                     self._queue.task_done()

         @staticmethod
         def _format_dialogue(recent_dialogue: list[dict]) -> str:
             """对齐 Cyrene getRecentDialogue：取最近 6 条，拼接为观察 prompt 的用户输入"""
             last = recent_dialogue[-6:]
             return "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in last)
         ```

     说明：`observe` 不再接收 `emotion_engine` 参数——改为 `set_callback` 注册回调，构造/装配解耦（对齐 Cyrene 中 observer 与 emotionEngine 通过回调连接，而非传实例）。worker 从队列取文本 → `_run_observation` → 解析成功 → `await self._on_feeling(feeling)`。
  2. 工作协程 `_run_observation(conversation_text: str)` 内部：
     ```python
     async def _run_observation(self, conversation_text: str) -> None:
         request = ChatRequest(
             messages=[{"role": "system", "content": self._prompt},
                       {"role": "user", "content": conversation_text}],
             model=self._model, temperature=0.2, max_tokens=16,
         )
         try:
             response = await asyncio.wait_for(
                 self._provider.async_chat_completion(request), timeout=self._timeout
             )
         except Exception as exc:
             logger.warning("情绪观察失败，保留当前状态: %s", exc)
             return
         feeling = parse_observer_feeling(response.content)
         if feeling:
             await self._on_feeling(feeling)   # 回调 → emotion_engine.apply_observation
     ```
     - 注意：**不传 tools / tool_choice / max_retries**；`temperature=0.2` 保证判定稳定；`max_tokens=16` 仅够 JSON 输出
     - 观察并发上限 1（单 worker），避免与主对话同时打爆 provider 限流
     - **usage 归属**：观察 token 单独累计到 `EmotionObserver.usage: TokenUsage`（独立于会话 usage，不污染主对话统计）；`response.usage` 有值时累加 `prompt_tokens`/`completion_tokens`/`total_tokens`（字段名对齐 `core/llm/models.py` `TokenUsage`）
  3. system prompt 模板（对齐 Cyrene 2171 行）：
     ```
     你是一个情绪分析器。根据以下对话，判断 Aliya 当前的心情状态。
     可选心情值（只能选其中一个）：neutral / calm / happy / excited / shy / affectionate /
     curious / confused / tired / sad / anxiety / anger / concerned / surprised / bored /
     grateful / relieved / disgusted。
     只返回 JSON，不要任何多余文字：{"feeling": "心情值"}。
     判断规则：以最后一轮对话为主，之前几轮为辅；判断的是 Aliya 的心情，不是用户的心情；
     无法判断时返回 neutral。
     ```
  4. 解析函数 `parse_observer_feeling(text: str) -> str | None`（对齐 Cyrene `parseObserverFeeling`：提取 JSON 中 `feeling` 字段，校验在 `VAD_EMOTIONS` 内，非法返回 None；`angry` 经别名归一化为 `anger`）
  5. 队列用 `asyncio.Queue` + 单 worker 协程；`observe()` 只入队并立即返回（不 await 结果，对齐 Cyrene enqueueLLMTask）
- **验证**：`tests/agent/test_emotion.py`：
  - `parse_observer_feeling('{"feeling": "happy"}') == "happy"`
  - `parse_observer_feeling('{"feeling": "angry"}') == "anger"`（别名归一化）
  - `parse_observer_feeling('{"feeling": "unknown"}') is None`、`parse_observer_feeling("非 JSON") is None`
  - mock `service.provider.async_chat_completion` 返回 `happy` → `await observer._run_observation(...)` 后 engine 状态 `scores["happy"] > 0`
  - mock provider 抛异常 → 观察失败，engine 状态不变
  - 断言观察调用**未**经过 `service.asend`/`asend_chat`（monkeypatch 这两方法使其抛异常，观察仍正常走 provider）

### Task A4 语气注入器 `ToneInjector`（移植 Cyrene `tone-injector.ts`）

- **涉及文件**：`agent/emotion/tone_injector.py`（新建）
- **修改点**：
  1. 场景例句（对齐 Cyrene `scene-embedder.ts` 的 `SCENE_EXAMPLES`：7 场景 × 6 句 = 42 句）：
     ```python
     SCENE_EXAMPLES: dict[str, tuple[str, ...]] = {
         "greeting": ("嗨，我来了。", "你在吗？", "好久不见，想你了。", ...),  # 6 句
         "comfort":  ("今天好累，什么都不想做。", "感觉有点迷茫…", ...),        # 6 句
         "praised":  ("你今天真的好好看。", "还是你最懂我。", ...),             # 6 句
         "playful":  ("哈哈你刚才那个回答绝了。", "来，猜我在想什么。", ...),    # 6 句
         "farewell": ("晚安了，明天再来找你。", "好了我要去睡了，拜拜。", ...),   # 6 句
         "concern":  ("你会累吗？", "你还好吗？", ...),                          # 6 句
         "daily":    ("今天发生什么了。", "无聊，随便聊聊。", ...),              # 6 句
     }
     SCENE_NAMES: dict[str, str] = {"greeting": "打招呼/相遇", "comfort": "安慰/陪伴", ...}
     ```
  2. 场景索引构建（对齐 Cyrene `buildSceneIndex`：每句例句独立向量化，匹配取 max）：
     ```python
     class ToneInjector:
         def __init__(self, embedding: EmbeddingProvider, prompts_dir: str = "data/prompts",
                      threshold: float = 0.72) -> None:
             self._index: dict[str, list[list[float]]] | None = None  # None = 未构建
             self._ready = False                                       # 构建失败后不再重试
             ...
         async def ensure_index(self) -> None:
             """懒加载 + 缓存：首次 match_scene 时构建，后续复用；
             构建失败置 _ready=True（不再重试），match_scene 降级返回 None。
             不阻塞启动——启动时不构建，首次对话需要注入时才构建。"""
         async def match_scene(self, user_input: str, recent_messages: list[dict]) -> str | None:
             """加权向量（当前 0.75/前轮 0.20/再前轮 0.05）+ 余弦 max 相似度，低于阈值返回 None"""
     ```
  3. 注入文本构建（对齐 Cyrene `buildToneInjection` + `loadToneRules`）：
     ```python
     def build_tone_injection(self, scene: str | None) -> str:
         """返回注入 system prompt 末尾的语气段：
         - 未命中场景：仅返回通用语气规则（data/prompts/tone-rules.md）
         - 命中场景：通用语气规则 + 「当前场景：xxx」+ 场景样本台词（从 prompts 或内置样本读）"""
     ```
  4. 场景样本台词文件：`data/prompts/scenes/{scene}.md`（新建 7 个文件，每文件含 `> 「台词」` 行，对齐 Cyrene `skills/cyrene-original-voice/references/{scene}.md`）；文件不存在或读取异常时用 `SCENE_EXAMPLES` 中该场景例句兜底
  5. 加权向量权重：`WEIGHT_CURRENT=0.75 / WEIGHT_PREV=0.20 / WEIGHT_PREV2=0.05`（对齐 Cyrene `scene-embedder.ts`）；只取 user 消息，过滤表情包描述
  6. **embedding 失败降级**：`ensure_index` 中 embedding 调用抛异常 → `logger.warning` + `_ready=True`；此后 `match_scene` 恒返回 `None` → `build_tone_injection(None)` 仅注入通用语气规则（**情绪引擎不因 embedding 不可用而崩溃**，与 Task A5 的无 embedding 降级一致）
- **验证**：`tests/agent/test_tone_injector.py`（新建）：
  - mock embedding（返回预置向量）→ `match_scene` 对 `"我很难过，很迷茫"` 命中 `comfort`；对无关文本返回 `None`（低于阈值）
  - `build_tone_injection(None)` 返回 `tone-rules.md` 内容；`build_tone_injection("comfort")` 含 `当前场景` 与场景样本台词
  - 场景索引构建：mock embedding `embed` 被调用 42 次（或批量），索引含 7 个场景
  - `ensure_index` 调用两次 → embedding 只调用一次（懒加载缓存）
  - mock embedding 抛异常 → `match_scene` 返回 `None`，`build_tone_injection(None)` 仍返回通用语气规则

### Task A5 情绪引擎装配（`EmotionEngine` 组合三件套）

- **涉及文件**：`agent/emotion/engine.py`（新建）
- **修改点**：
  1. `EmotionEngine` 组合平滑器 + 观察器 + 注入器（对齐 Cyrene：`feelingScores` + `observeRuntimeState` + `buildToneInjection` 三者的协作关系）：
     ```python
     class EmotionEngine:
         def __init__(self, service: ConversationService, embedding: EmbeddingProvider | None,
                      *, config: dict | None = None) -> None:
             self._scores = create_feeling_scores()        # 状态平滑器状态
             self._observer = EmotionObserver(service)     # LLM 观察器
             self._observer.set_callback(self.apply_observation)   # 观察结果 → 平滑器
             self._injector = ToneInjector(embedding) if embedding else None  # 语气注入器（无 embedding 则降级）
             self._config = config or {}
             self._on_change: Callable[[dict], Awaitable[None]] | None = None  # dominant 变化广播回调（WS 装配时注册）

         @property
         def usage(self) -> TokenUsage:
             """观察 token 用量（独立于会话 usage）"""
             return self._observer.usage

         async def start(self) -> None:
             """启动观察 worker（app lifespan 调用）"""
             await self._observer.start()

         async def stop(self) -> None:
             """停止观察 worker（app 关闭调用）"""
             await self._observer.stop()

         @property
         def state(self) -> EmotionState:
             return EmotionState(dominant=self._dominant(), scores=dict(self._scores))

         def _dominant(self) -> str:
             """分数最高的情绪标签；全 0 时回落 config.fallback（默认 neutral）"""
             if not self._scores:
                 return self._fallback()
             return max(self._scores, key=self._scores.get)

         def _fallback(self) -> str:
             return str(self._config.get("fallback", "neutral"))

         def _weights(self) -> dict:
             """从 config 读取平滑权重（对齐 Cyrene observerWeight/FAST_RISE）"""
             return {
                 "fast_rise": set(self._config.get("fast_rise", ["sad", "anxiety", "anger", "disgusted"])),
                 "observer_weight": float(self._config.get("observer_weight", 0.3)),
                 "fast_rise_weight": float(self._config.get("fast_rise_weight", 0.62)),
             }

         async def observe(self, recent_dialogue: list[dict]) -> None:
             """每轮对话结束后调用：LLM 观察 → 平滑 → 更新 scores（内部入队，立即返回）"""
             if not self._config.get("enabled", True):
                 return
             await self._observer.observe(recent_dialogue)

         def apply_observation(self, feeling: str) -> None:
             """观察器回调：smooth_feeling 更新 scores；dominant 变化时触发 on_change 广播"""
             new_scores = smooth_feeling(self._scores, feeling, **self._weights()).scores
             old_dominant = self._dominant()
             self._scores = new_scores
             if self._on_change and self._dominant() != old_dominant:
                 # 广播不在本方法内 await——由调用方（观察 worker）驱动，
                 # 避免 apply_observation 变成 async 破坏回调签名；
                 # 广播失败不抛回观察 worker（WS 断开等场景）：
                 async def _safe_broadcast() -> None:
                     try:
                         await self._on_change(self.to_payload())
                     except Exception:
                         logger.warning("情绪广播失败，忽略")
                 asyncio.create_task(_safe_broadcast())

         async def build_tone_injection(self, user_input: str, recent_messages: list[dict]) -> str:
             """每轮 SOUL_PHASE 前调用：场景匹配 → 语气注入文本"""
             if not self._injector:
                 return load_tone_rules()                  # 无 embedding 降级为仅通用语气规则
             await self._injector.ensure_index()
             scene = await self._injector.match_scene(user_input, recent_messages)
             return self._injector.build_tone_injection(scene)

         def to_payload(self) -> dict:
             """{"emotion": dominant, "scores": {...}}，供 WS emotion_changed/emotion_state 使用"""
         ```
         2. **情绪补丁注入方式调整**（对齐 Cyrene：语气注入器输出**完整语气规则段**，而非单情绪标签）：不再用 `set_emotion_patch` 塞一句"当前情绪：xxx"，而是 `EmotionEngine.build_tone_injection()` 输出语气规则段 → 在 `_soul_phase` 中追加到 system 末尾（通过 `inject_soul_context` 的 `emotion_patch` 参数传入，或直接拼进 soul system）。`set_emotion_patch` 保留作为兜底（`emotion_patch` 非空时仍注入）
         - **验证**：`tests/agent/test_emotion.py`：`to_payload()` 含 `emotion`/`scores` 键；无 embedding 时 `build_tone_injection` 返回通用语气规则；`observe` 失败（mock）不抛异常且状态不变；`apply_observation` 触发 dominant 变化时 `on_change` 回调被调度（mock on_change）。

### Task A6 情绪引擎接入 `AgentLoop`（SOUL_PHASE 注入 + 观察时机）

- **涉及文件**：`agent/loop.py`（修改）
- **修改点**（对齐实际 M1 签名 + Cyrene `onAgentRunFinished` 中的观察时机）：
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
         # 对齐 Cyrene buildToneInjection：场景匹配产出完整语气规则段
         emotion_patch = await self.emotion_engine.build_tone_injection(text, recent_user_messages)
     await inject_soul_context(
         self.service, self.context,
         memory_text=memory_text,
         emotion_patch=emotion_patch,     # 原硬编码 "" 改为变量（语气规则段或空串）
         tool_summary=tool_summary,
     )
     ```
     - `recent_user_messages` 构建（对齐 Cyrene `buildWeightedVector`）：
       ```python
       history = await self.service.get_history()          # list[Message]
       user_msgs = [m.content for m in history if m.role == "user" and m.content]
       recent_user_messages = user_msgs[-2:]               # 只取最近 2 轮用户消息
       ```
  3. `RunFinished` 后（对齐 Cyrene `onAgentRunFinished` 末尾调 `observeRuntimeState`）追加：
     ```python
     # 情绪观察入队（后台异步，不阻塞本轮返回）
     if self.emotion_engine:
         history = await self.service.get_history()
         recent = [
             {"role": m.role, "content": m.content}
             for m in history if m.role != "system" and m.content
         ][-8:]                     # 过滤 system、取最近 8 条
         await self.emotion_engine.observe(recent)   # 内部入队，立即返回
     ```
     - 对齐 Cyrene：`getRecentDialogue`（`slice(-6)` 逻辑在 observer 内部执行，见 Task A3 实现说明）
  4. **`emotion_changed` 广播是异步路径（非 loop 内 yield）**：观察 worker 产出新情绪 → `EmotionEngine.apply_observation()`（Task A5 已实现）→ dominant 变化时 `asyncio.create_task(self._on_change(payload))` → WS 装配时注册的 `on_change` 回调向 `_active_ws` 广播 `ProtocolEvent(type="emotion_changed", payload=to_payload())`（对齐 Cyrene `broadcastRuntimeStateChanged`）。**因此 `emotion_changed` 不出现在 `submit_user_message` 生成器的事件序列中**，而是独立异步广播；loop 只需确保 `on_change` 在 WS 装配时已注册
  5. `_clear_patches` 由 `ConversationService.astream_send` 的 finally 自动调用（M1 已确认），无需 loop 手动清理
- **验证**：`tests/agent/test_loop.py`（追加）：构造带 `emotion_engine` 的 loop（mock 注入器），断言 `build_tone_injection` 被调用且 `recent_user_messages` 只含 user 消息；观察在 `RunFinished` 后调用 `emotion_engine.observe`；`emotion_changed` 由 `apply_observation` 触发 on_change（mock 回调断言被调度），**不要求**出现在 submit 生成器事件序列中；不带 `emotion_engine` 时无观察调用（M1 回归）。

### Task A7 情绪状态查询接入 WS

- **涉及文件**：`agent/ws.py`（修改）、`agent/app.py`（修改）
- **修改点**：
  1. **装配改造（影响后续 Part B/C）**：`_default_session_factory()` 改造为从应用级单例读取依赖，新增应用装配入口 `agent/app.py` 中 `create_app()` 创建并持有 `EmotionEngine`（含 embedding 注入，缺省 None 降级）/ `ProactiveScheduler`（M2）/ `SessionStore`（M2 Part C）；`create_ws_router(session_factory=None)` 保持签名，会话工厂改为由 app 装配时传入（沿用 M1 的注入模式，`get_emotion_state` 分支通过 `emotion_engine` 闭包捕获）
  2. `get_emotion_state` 分支（当前为 `pass` 占位）替换为：
     ```python
     elif mtype == "get_emotion_state":
         payload = emotion_engine.to_payload()  # {"emotion": dominant, "scores": {...}}
         # GUI ws.js 的 emotion_state handler 读取 data.dominant；状态面板读 emotion/scores
         await ws.send_json({"type": "emotion_state", "dominant": payload["emotion"], **payload})
     ```
  3. **`emotion_changed` 广播装配**（异步路径，Task A6 第 4 点）：WS 装配时注册 `on_change` 回调 → 向 `_active_ws` 发送：
     ```python
     async def _broadcast_emotion(payload: dict) -> None:
         msg = {"type": "emotion_changed", **payload}   # 扁平化：data.emotion / data.scores 被 GUI handler 读取
         for ws in list(_active_ws):
             try:
                 await ws.send_json(msg)
             except Exception:
                 _active_ws.discard(ws)                 # 断开连接容错
     emotion_engine.on_change = _broadcast_emotion
     ```
     - 注：`on_change` 回调在 `apply_observation` 中以 `asyncio.create_task` 调度（Task A5），WS 侧无需另起循环
  4. **观察 worker 生命周期**：`EmotionEngine` 暴露 `async def start()/stop()` 透传观察器 worker（`self._observer.start()/stop()`）；`app.py` 在 FastAPI `lifespan` 中调用 `emotion_engine.start()`（启动）与 `emotion_engine.stop()`（关闭），确保观察队列 worker 随应用启停
  5. `get_token_usage` 响应附带观察器独立 usage：`{"type": "token_usage", "payload": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ..., "emotion_observer": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}}}`（字段名对齐 `TokenUsage`，观察 token 不计入会话统计）
- **验证**：`tests/agent/test_ws.py`（追加）：发 `get_emotion_state`，断言收到 `emotion_state` 且含 `emotion` + `dominant` 键；mock `_broadcast_emotion` 断言收到 `{"type": "emotion_changed", "emotion": ..., "scores": ...}` 且对断开连接不抛异常；`get_token_usage` 响应含 `emotion_observer` 段。

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
  1. 构造 `EmotionEngine`（mock 观察器直接 `apply_observation`，不真调 LLM）+ `AgentLoop`（带 emotion）+ `SessionStore`（tmp 路径）+ `SessionManager`
  2. 会话 1：`submit("我今天好开心")` → 观察回调 `apply_observation("happy")` 后断言 `emotion_changed(happy)` 事件、会话标题派生
  3. 会话 2：切换后 `submit("我很难过")` → `apply_observation("sad")` 后断言 `emotion_changed(sad)`，两个会话历史独立
  4. 语气注入：断言 SOUL_PHASE 调用了 `build_tone_injection`（mock 返回语气段），`inject_soul_context` 收到 `emotion_patch` 参数
  5. `SessionStore.save/load` 后元数据完整
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
- [ ] GUI 侧边栏/Live2D 情绪随对话变化（`emotion_changed` 事件链路通，平滑累积非跳变）
- [ ] 语气注入生效（LLM 观察器 → 平滑器 → 语气规则段注入 system），每轮自动清除
- [ ] 主动聊天可配置、护栏生效、消息可达 GUI
- [ ] 会话列表可新建/切换/删除，重启后保留
- [ ] 手动验收 6 条全部通过

## 参考对照

| 设计点 | 来源 |
|---|---|
| 情绪标签（19 个 VAD 标签） | `GUI/src/live2d/emotion-map.js`（`FEELING_TO_EMOTION` 的 key） |
| 情绪事件 payload 格式 | `GUI/main/ws.js` `emotion_changed` handler（`emotion` + `scores`） |
| `set_emotion_patch` / `_clear_patches` | `core/llm/service.py`（已存在，每轮自动清理） |
| **状态平滑器**（`smooth_feeling`，负面快速上升 0.62/其他 0.3） | `example/Cyrene-Agent-master/src/main/orchestrator/runtime-state-smoother.ts` + `.test.ts` |
| **LLM 心情观察器**（后台队列 + JSON 解析 + 失败保留） | `example/Cyrene-Agent-master/src/main/index.ts` `observeRuntimeState`/`parseObserverFeeling`（2151/1609 行） |
| **语气注入器**（embedding 场景匹配 + 语气规则 + 样本台词） | `example/Cyrene-Agent-master/src/main/orchestrator/tone-injector.ts` + `scene-embedder.ts`（7 场景 × 42 句例句，阈值 0.72，加权向量 0.75/0.20/0.05） |
| embedding 能力 | `core/vector/embedding.py` `EmbeddingProvider` / `EmbeddingFactory` |
| 场景样本台词文件 | `data/prompts/scenes/{scene}.md`（新建，对齐 Cyrene `skills/cyrene-original-voice/references/{scene}.md` 的 `> 「台词」` 格式） |
| 主动聊天（scheduler + proactive + 护栏） | `example/Cyrene-Agent-master` scheduler/proactive 模块 |
| 多会话 JSON 持久化 | `example/Cyrene-Agent-master` conversationManager |
| 情绪补丁自动清除 | `core/llm/service.py` `astream_send` finally 中 `_clear_patches()` |
