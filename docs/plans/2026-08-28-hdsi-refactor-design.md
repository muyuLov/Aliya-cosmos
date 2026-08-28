# Aliya-Cosmos 重构设计：借鉴 HDS-Interlude「幕间连续生活」架构

> **日期**: 2026-08-28
> **状态**: 设计完成，待实现
> **策略**: 全量 5 阶段重构，旧系统整体移除，agent 整体重新设计，不保留接口兼容

## 概述

参考 `example/HDS-Interlude`（幕间系统）的架构模式，将 Aliya-Cosmos 从「两阶段 FC 对话循环」重构为「幕间连续生活」系统。每一次用户消息进入连续生活剧本：主叙事单次写作补写已发生的生活、处理当前事件、决定行为，并结构化产出记忆候选、状态变更、意图与情绪偏移。

**核心约束（用户拍板）**：
- 全量落地 5 阶段（基础设施/记忆分层/结构化输出/Alter+Agency/场景+日志全部实现）
- 叙事模型重构为主叙事单次写作（工具调用退化为受限行动）
- 记忆四层分层统一入口；**原有记忆系统（GRAG 图记忆 + 层次化记忆两套并行）整个不要**，不复用为 FactLayer 底层，改用全新分层记忆体系实现，旧记忆代码不保留
- **旧系统不要了**：GRAG/两阶段循环/固定情绪引擎/基础主动调度直接删除
- **agent 整体重新设计**：loop/context/events/session/emotion/proactive 均重写
- **日志系统整体重新设计**：core/logger 分层化重写
- **TTS 也不要**：TTS 语音合成系统（core/tts 模块 + 配置 + 依赖）从项目移除，不进入新架构；**保留 docker 配置**（compose.yml 的 astratts 服务与 start 脚本段不动）
- **所有接口也要重构**：WS/GUI 线上协议、EventSink 渠道接口、事件类型（AgentEvent+ProtocolEvent）与内外部调用接口整体重新设计，不复用旧协议
- **不保留接口兼容**：允许干净重写核心接口

## HDS-Interlude 关键模式提炼

| 模式 | HDSI 理念 | Aliya 现状 → 目标 |
|------|-----------|-------------------|
| 主叙事单次写作 | 一次 LLM 产出文本+行为+记忆+意图+情绪 | 两阶段 FC → 单次写作 |
| 分层记忆 | Canon/Overlay/Continuity/Facts | GRAG+层次化两套并行 → 四层统一 |
| 固定阶段连续生活 | 用户消息/对话后续/到期意图/独立推进 | 无 → 引入 |
| 串行队列 | 每会话串行，保证一致性 | 无显式序列化 → 会话级串行 |
| 动态阈值 | 对话密度影响情绪灵敏度 | 固定阈值 → Alter 动态 |
| 主体约束 | Agency Window 三因素门控 | 基础调度 → Agency |
| 证据链演化 | StatePatchProposal 带证据/置信度 | 无 → Overlay 证据链 |
| 场景/弧线 | 显式边界，自动压缩 | 无 → 场景管理 |
| 休息窗口 | 可配置低频推进时段 | 无 → 休息窗口 |
| 分层日志 | 阶段感知+颜色+结构化 | 基础日志 → 整体重写 |

---

## 阶段 1：基础设施层

### 1.1 AsyncSingleton（core/infra/singleton.py）
异步安全惰性单例注册表，按 key 独立加锁（不阻塞并行初始化），路径归一化防重复实例。
```python
class AsyncSingleton:
    async def get_or_create(cls, key, factory) -> T   # 异步惰性单例
    def get_sync(cls, key) -> object | None           # 同步快速检查
    def clear(cls, key: str | None = None) -> None    # 清理
```
替代现有 `core/config`/`core/memory` 的 `threading.Lock` 手写单例与 `agent/session.py` 全局状态。

### 1.2 配置环境变量解析缓存（core/config/manager.py）
`${ENV_VAR}` 解析加 LRU 缓存，`reload()` 失效。

### 1.3 会话级串行队列（agent/queue.py）
```python
class SessionQueue:
    async def enqueue(self, text, images=None) -> AsyncGenerator  # 入队，返回事件生成器
    async def start(self, loop_fn) -> None                        # 启动消费者
```
同一会话的用户消息、到期意图、自动推进、场景压缩全部串行。

---

## 阶段 2：分层记忆（core/memory/layers/）

### 2.1 记忆层协议（layers/__init__.py）
```python
@dataclass(frozen=True)
class MemoryEntry:
    id, content, source, confidence, importance, metadata

class MemoryLayer(ABC):
    name: str
    async def write(self, entry) -> None
    async def query(self, text, limit=5) -> list[MemoryEntry]
    async def forget(self, entry_id) -> None
    async def decay(self, factor=0.95) -> None
```

### 2.2 四层定义
| 层 | 职责 | 存储 | 刷新 |
|----|------|------|------|
| L0 CanonLayer | 角色核心设定（只读） | YAML(prompts) | 仅手动 |
| L1 OverlayLayer | 演化人设/关系/世界观（证据链） | SQLite（StatePatch 表） | 每轮后 |
| L2 ContinuityLayer | 近期场景摘要+连续性快照 | SQLite + sqlite-vec 向量 | 场景关闭 |
| L3 FactLayer | 长期事实/承诺/事件 | SQLite + sqlite-vec 向量 | 后台提取 |

L1/L2/L3 均基于 `core/memory/storage/` 的 SQLite + sqlite-vec 存储（Task 2.2 的 `SQLiteVectorStore`）；L2/L3 的文本经向量化进入 `vec0` 虚拟表做 kNN 检索。

### 2.3 OverlayLayer 证据链（StatePatch）
```python
@dataclass
class StatePatch:
    id, target, proposed_value, evidence, confidence, impact, status,
    source_entry_ids, created_at, applied_at
```
应用门槛：置信度≥0.82（普通）/≥0.95（重大）+ 至少 3 个回合 + 跨 2 个日历日 + 同路径冷却 72h。

### 2.4 旧记忆系统移除
原有记忆系统（`GRAGMemoryManager` 图记忆 + `HierarchicalMemory` 层次化两套并行）整个移除，不复用为 FactLayer 底层。`core/memory/` 中旧实现（graph/extractor/rag_query/task_manager/hierarchical 等）删除，`get_memory_manager()` 返回全新的四层分层统一门面。FactLayer 用全新实现承载长期事实/承诺/事件。

### 2.5 向量持久化：SQLite + sqlite-vec（用户选定）
新分层记忆（FactLayer / ContinuityLayer 的向量检索）采用 **SQLite + sqlite-vec 扩展** 持久化：

- **依赖**：`sqlite-vec` Python 包（内含预编译扩展，`pip install sqlite-vec` 即得）；异步用 `aiosqlite`。
- **扩展加载**：
  ```python
  import aiosqlite, sqlite_vec
  conn = await aiosqlite.connect(path)
  # aiosqlite 需通过底层 sqlite3 连接启用扩展加载：
  #   raw = conn._connection; raw.enable_load_extension(True); sqlite_vec.load(raw); raw.enable_load_extension(False)
  ```
- **vec0 虚拟表**：
  ```sql
  CREATE VIRTUAL TABLE IF NOT EXISTS fact_vectors USING vec0(
      embedding float[{dim}]
  );
  ```
- **写入**：`INSERT INTO fact_vectors(rowid, embedding) VALUES (?, ?)`，向量用 `serialize_float32(vec)`。
- **kNN 检索**：`WHERE embedding MATCH ? ORDER BY distance LIMIT k`，embedding 需归一化（sqlite-vec 的 `distance` 与余弦相关）。
- **元数据表**：`MemoryEntry` 的 id/content/source/confidence/importance/metadata 存普通 SQLite 表，`rowid` 与 vec0 对齐，查询时 join 回填文本与元数据。
- **版本要求**：SQLite 3.41+；macOS 需 Homebrew 版 Python（系统自带的禁止扩展加载）。
- **降级**：sqlite-vec 加载失败时降级为纯内存 numpy 余弦（复用现有 `VectorStore`），不阻塞主流程。

---

## 阶段 3：主叙事单次写作循环（agent 重新设计）

### 3.1 新循环（agent/narrator.py + agent/metadata_parser.py + 重写 loop.py）
一次主叙事调用产出：
- 补写已发生生活（script）
- 处理当前事件/到期意图/自动推进
- 决定行为：`seen` + `reply`（immediate/delayed/silent）+ 受限行动
- 结构化副产物：memory_candidates、state_patches、follow_up_intents、alter、scene

### 3.2 结构化输出协议（NarrativeDecision，参照 HDSI）
**调用形态**：整个上下文作为**单条 JSON 消息**发送（`system` 放固定系统合约，`user` 放 `JSON.stringify(结构化上下文)`），一次 `chat/completions` 产出全部。附加 `response_format: {type:'json_object'}` 要求 JSON。

**完整 schema**（含 prose 与 transport 分离）：
```json
{
  "script": "补写的剧本正文（prose，不拆<sep/>）",
  "alter": 2,
  "agencyWindow": {"activityLoad": "free", "privacy": "private", "deviceAccess": "available"},
  "proactiveContact": null,
  "continuity": {"state": "...", "nextSteps": "...", "recentFacts": []},
  "interaction": {
    "seen": true,
    "reply": {"mode": "immediate|delayed|none", "content": "transport 层可含<sep/>拆气泡", "sendAt": null}
  },
  "groupReply": {"mode": "immediate|none", "content": ""},
  "followUpCommitment": {"summary": "", "type": "promise|reminder"},
  "followUpResolutions": [],
  "automaticDeliverySummary": [],
  "memories": [{"content": "", "importance": 0.6, "participantId": "", "kind": "fact|promise|event"}],
  "intents": [{"type": "delay|reminder|proactive-check|split-message", "summary": "", "notBefore": "", "participantId": ""}],
  "intentUpdates": [],
  "statePatch": {"target": "character|relationship|world|perspective", "value": "", "evidence": "", "confidence": 0.9, "impact": "minor|major"},
  "crossConversationActions": [],
  "actions": [{"type": "tool", "tool": "search", "args": {"q": "..."}}]
}
```

**关键机制（HDSI 提炼）**：
- **prose(script) 与 transport(interaction/groupReply/crossConversationActions) 严格分离**：script 是补写的生活剧本，transport 决定"角色实际对谁说/做什么"。`<sep/>` **只出现在 transport 的 reply.content**，用于把一条回复拆成多个气泡；不拆 prose。
- **首条回复提交边界**：`<sep/>` 拆分后**只立即投递首段**，后续段各生成 `split-message` intent（`notBefore = 打字开始 + 累计 typingDelay`）模拟打字。首段投递时设 `firstMessageCommittedRequestId`——新用户消息只有在首条回复**尚未提交**时才作废在途模型请求（`shouldSupersedeNarrativeRequest`）。
- **`seen:false` ≠ `mode:none`**：`seen:false` 是"没注意到"，`seen:true + mode:none` 是"注意到了但不回"。二者语义不同，模型由 prompt 合约约束区分。
- **normalize 防御**（不信任模型输出）：script 截断到 `maxScriptCharacters`；alter 取整限幅 `-5..5`（非法/非有限→None）；seen 强制 boolean；`seen=false` 强制 reply.mode=none；`mode=delayed` 且 sendAt 超出 `[now+minDelayedSec, now+maxDelayedMin]` 则降级为 mode=none；memories/intents/crossConversationActions 的 participantId 白名单过滤；intents 最多 8 条且过滤 `follow-up-commitment` 类型；browserIntents 最多 1 条。
- **空 script 语义失败**：`hasRequiredNarrativeScript` 为假 → 抛错走 **narrative-retry 持久化重试**，绝不推进故事游标留下生活记录空洞。
- **输出恢复**：若 phase 要求可见回复但结构缺失，丢弃本次未落库剧本，用 `outputRecovery:true` 再写一次；仍失败才抛错。

### 3.3 解析与降级
`metadata_parser` 从回复分离 prose 与 transport，防御性归一化（normalize），失败降级纯文本模式。流式过滤仅把 transport 气泡发给用户。

### 3.4 旧系统处置
两阶段 `_tool_phase`/`_soul_phase` 删除；工具调用退化为受限行动（主模型在 `actions` 提出、经权限/Agency 门控执行）；事件模型扩展 `TurnMetadata`/`AlterTriggered`/`ProactiveContact`。

### 3.5 接口层整体重构
所有接口层重新设计，不复用旧协议：
- **线上协议（WS/GUI）**：`agent/ws.py` 重写，新协议承载主叙事事件（turn_metadata / alter_triggered / proactive_contact / scene_closed / agency_decision 等），客户端消息类型（user_message / stop / confirm_response / get_emotion_state / list_sessions 等）重新定义。
- **事件模型（agent/events.py）**：`AgentEvent` + `ProtocolEvent` 双层协议重新设计，`to_protocol` 映射全面更新，旧事件类型（StepStarted/StepFinished/ToolCall* 等两阶段残留）移除。
- **渠道接口（agent/channels/）**：EventSink 接口与 feishu/wechat 渠道重新设计，适配新事件流与主叙事生命周期；新渠道事件订阅模型替代旧 sink 广播。
- **核心服务接口**：`ConversationService` 调用接口、记忆门面接口、情感/主体接口均按新架构重定义，不复用旧方法签名。

### 3.6 agent 模块重新设计蓝图
`agent` 模块作为独立子系统整体重新设计，围绕「幕间连续生活」组织目录与职责：

```
agent/
  app.py            # FastAPI 装配（路由挂载，适配新协议）
  main.py           # 启动入口
  config.py         # agent 配置装载（剧本/模型/频道/情感/主体）
  story/            # 主剧本与参与者
    canonical.py    # Canonical Story 主剧本状态
    participant.py  # 参与者资料/初始关系/演化状态
    entry.py        # ScriptEntry 剧本条目（用户事件/AI回复/受限行动/系统事件）
    script_store.py # 剧本持久化（SQLite）
  loop.py           # 主叙事回合编排（串行队列→主叙事→投递→副作用）
  narrator.py       # 主叙事单次写作器（LLM 一次调用 + <sep/> 拆分）
  metadata_parser.py# 结构化输出解析器
  context.py        # 上下文构建器（recentScript+continuity+记忆+Alter+Agency+结构指令）
  queue.py          # 会话级串行队列
  events.py         # 事件模型（AgentEvent+ProtocolEvent 协议重构）
  protocol.py       # 线上协议 schema（WS/GUI 消息）
  session.py        # 会话生命周期
  session_store.py  # 会话元数据
  ws.py             # WS 网关
  channels/         # 渠道（重构，适配新事件流）
  emotion/
    alter.py        # Alter 动态氛围（替代旧固定情绪引擎）
  proactive/
    agency.py       # Agency Window 三因素门控
    rest_windows.py # 休息窗口
    scheduler.py    # 后台调度（自动推进/到期意图/场景压缩）
  tools/            # 受限行动工具注册表
  mcp/              # MCP 服务
  skills/           # 技能
  knowledge/        # 知识库 RAG
```

**数据流**（单回合）：用户事件/到期意图/自动推进经 `queue.py` 串行取 → `loop.py` 经 `context.py` 组装上下文（recentScript + continuity + 分层记忆 + Alter 氛围 + Agency 容量）→ `narrator.py` 主叙事一次调用产出 script + 行为决策 + 结构化副产物 → `metadata_parser.py` 解析 → 保存剧本/场景/记忆/补丁（`story/` + 分层记忆）→ 投递回复 → 处理受限行动（工具经 `tools/` + Agency 门控）→ 副作用写分层记忆/Alter。旧两阶段循环、旧情绪引擎、旧基础调度器、旧 sink 广播模型全部移除。

**配置**：`data/config/main.yml` 新增 `cosmos.agent.story/narrator/alter/agency/rest_windows` 等节。

### 3.7 agent 核心机制详细设计（重新设计）

**回合模型 Turn**：每个被消费的串行事件构成一个 `Turn`，携带 `kind`（user_message / intent_due / auto_advance）、`source`（参与者/渠道）、`occurred_at`。`loop.py` 为每种 kind 统一走主叙事，但上下文注入的"当前事件"表述不同（用户消息→当前事件；到期意图→承诺/提醒到期；自动推进→纯生活推进）。

**主叙事行为决策 schema**（`narrator.py` 产出，`metadata_parser.py` 解析）：
```json
{
  "script": "补写的剧本正文（可含 <sep/> 拆气泡）",
  "seen": true,
  "reply": {"mode": "immediate|delayed|silent", "content": "", "plan_time": null},
  "actions": [
    {"type": "tool", "tool": "search", "args": {"q": "..."}},
    {"type": "proactive_contact", "motive": "", "target": "", "willingness": 0.8}
  ],
  "memory_candidates": [{"content": "", "importance": 0.6, "layer": "fact"}],
  "state_patches": [{"target": "character", "value": "", "evidence": "", "confidence": 0.9, "impact": "minor"}],
  "follow_up_intents": [{"type": "promise|reminder|delay", "summary": "", "not_before": ""}],
  "alter": 2,
  "scene": {"end": false, "hook": "", "summary": ""}
}
```

**loop.py 回合状态机**（四阶段）：补写剧本 → 处理当前事件与行为决策 → 按模式投递（immediate 立即 / delayed 存 intent / silent 不投）→ 副作用（写剧本/场景/分层记忆/StatePatch/Alter 累计）。每条受限行动先经权限检查（`tools/` PermissionChecker）再经 Agency 门控执行，结果回填剧本为"受限行动"条目。

**context.py 组装优先级**（自上而下，字符预算约束）：Canon（人设）→ recentScript（最近原始剧本）→ continuitySnapshot（低频状态）→ 分层记忆召回（Overlay 演化 + Facts 长期事实）→ Alter 氛围参考 → Agency 容量状态 → 结构化输出指令 → 时间端点（UTC + nowLocal + 时段）。

**narrator.py**：OpenAI-compatible 一次调用，`response_format` 要求结构化 JSON；整个上下文作为单条 JSON 消息（system 固定合约 + user 纯 JSON 结构化上下文）。prose(script) 与 transport(interaction/groupReply) 分离，`<sep/>` 只在 transport 层拆分（首段立即投递 + split-message intent 模拟打字）。超时/重试/降级纯文本。**空 script 语义失败 → 生成 `narrative-retry` intent 持久化重试，绝不推进 cursorAt**。输出恢复：可见回复缺失时 `outputRecovery:true` 重写一次。

**session.py / protocol.py 重新设计**：`AgentSession` 持有 `SessionQueue + loop + 分层记忆门面 + Alter + Agency`；`protocol.py` 定义 WS/GUI 消息 schema（事件流 + 客户端指令），`ws.py` 仅做序列化适配，不承载业务逻辑。

**emotion/alter.py 重新设计**：纯状态机（无 LLM 依赖的核心），`AlterState` 保存累计/权重/方向/历史；达到阈值仅记录待分析标记，可见回复不等待侧端模型；侧端分析复用 narrator 的连接与 failover，失败保留累计并在冷却后重试。

**proactive/scheduler.py 重新设计**：后台调度器消费三类来源（自动推进、到期 intent、proactive-check），全部经 `queue.py` 串行进入主叙事；Agency 在投递前裁决联系候选；休息窗口约束自动推进间隔。替代旧 schedule/idle 触发式调度。

### 3.8 剧本/场景持久化（SQLite 表结构，参照 HDSI）

`agent/story/script_store.py` 用 SQLite 持久化剧本与参与者。核心表（参照 HDSI 11 表精简）：
- **story**：`setting:json, state:json, cursorAt, status`——`cursorAt` 是故事游标（推进时间补写的基础）；`state` 存 Alter/Agency 等运行时状态。
- **participant**：`storyId, personId, profile, relationship:text, state:json, status`——参与者独立资料/初始关系/演化状态。
- **script_entry**：`storyId, participantId, kind(32), actor(32), content:text, occurredAt`——剧本条目（用户事件/AI回复/受限行动/系统事件），`kind` 区分 user_message/character-message/tool_call/system_event。
- **intent**：延迟回复/提醒/承诺/主动联系重查/split-message 的到期意图。
- **scene / arc**：场景与弧线元数据（阶段 5）。

**故事游标与时间补写**：每回合从 `cursorAt` 补写到现在已发生的生活（catch-up），以当前 `nowLocal` 结束；`cursorAt` 在每次落库剧本后推进。**空 script 语义失败时绝不推进 cursorAt**，避免生活记录空洞。

**连续性分层取用（参照 HDSI continuity）**：
- **Canon**：人设起点（identity/soul/tone-rules）。
- **recentScript**：最近原始剧本条目（保留语义/顺序/细节），上下文的主要来源。
- **continuitySnapshot**：低频更新的当前状态/下一步/近期事实（`continuity` 字段返回）。
- **长期事实**：FactLayer 可检索的承诺/事件/关系事实。
- **active consequence**：已发生事件的短期余波，不重写 Canon。
- **Overlay**：达到证据门槛后的稳定演化（StatePatch 应用结果）。
- **Perspective**：独立于 Canon 的外壳人格层，其 overlay 只在相关情境微调判断。
- **Alter / Agency**：氛围惯性 + 外部行动容量，各自独立约束层，不替代上述任何一层。

**字符预算控制**：context 按层自上而下组装，受总字符预算约束（参照 HDSI `maxContextCharacters`）；recentScript 保留原始条目、continuitySnapshot 用低频摘要，保证长会话不爆炸。

### 3.9 时间与调度机制（HDSI 提炼）

**时间双时钟**：
- **DB 全存 ISO-8601 UTC**（`cursorAt`/`notBefore`/`occurredAt`/`last*At`），本地时间仅在渲染时转换。
- `localClockMinutes(value, timezone)` 用 `Intl.DateTimeFormat.formatToParts` 提取 hour/minute 换算为分钟。
- 时区解析：`Intl.DateTimeFormat('en-US',{timeZone:candidate}).format(0)` 试探，抛异常回退 `'UTC'`，结果缓存 `timezoneCache`；默认 `Asia/Shanghai`。
- **formatter 缓存**：`formatterCache` 键为 `kind:locale:timezone`，五种 kind（story/log/clock/day 等），避免反复构造 Intl formatter。

**时段（daypart）与日照**（`storyLocalTimeContext`）：
- 分界（闭区间）：`5≤h<12` morning；`12≤h<18` afternoon；`18≤h<22` evening；else night。
- `daylightExpectation` 日照预期按 period 生成（morning/afternoon 通常有日光，night 通常黑暗），作为主模型时间端点的一部分。

**休息窗口**（`activeRestWindow`）：`start/end` 解析为分钟（`clockMinutes`）。**跨午夜用半开区间**：`start≤end` 用 `localMinutes≥start && <end`；跨午夜 `≥start || <end`。窗口内自动推进间隔取 `randomInteger(minInterval,maxInterval)`（默认 120–240 分钟）。

**后台调度**：
- `sweepIntervalMinutes`（默认 5）扫描到期 intent；记忆压缩 `backgroundIntervalMinutes`（默认 10）；盲模式健康心跳 `healthReportMinutes`。
- 自动推进间隔：休息窗口内 `random(min,max)`；否则 `max(1, intervalMinutes + random(-jitter, jitter))`（带抖动避免同步洪峰）。
- 到期 intent：DB 存 UTC `notBefore`，`scheduleDueIntentWake` 定时唤醒，`dueIntents` 用 `notBefore ≤ now` 查询。

### 3.10 串行与并发、多参与者、压缩、盲模式、分层日志细节（HDSI 提炼）

**故事级串行队列**（`serial<T>(id, task)`）：`queues: Map<id, Promise>`，取旧 promise `.catch(()=>undefined).then(task)`——上一任务无论成败先放行（失败不堵队列），完成时校验自身是队头才删除（版本检查）。用户消息/到期意图/自动推进/压缩/Overlay 全包进 `serial(story_id, ...)`；压缩先 `hasPendingNarrative` 前台让路 + 500ms 轮询，用 `scheduledCompactions` set 去重合并。

**SQLite 写队列**：全局单写连接，串行化 create/set/delete；transient DB 错退避重试最多 7 次（延时 `[100,250,500,1000,2000,3000,5000]ms + jitter`）；读走并发连接（3 次 `[50,125,250]ms`）。布尔闸（narrating/compacting/sweeping/resetting）防后台与前台竞态。

**多参与者私聊边界**：participant payload 基线 `id`，`includeRelationshipDetails` 给 `displayName/profile/relationship/relationshipOverlay/lastUserMessageAt/lastCharacterMessageAt`，另 `unreadMessageCount/pendingReplyCount/updatedAt`。`shareParticipantDetails`（默认 false）控制是否把别参与者原文/私聊事实传给模型；关闭时 compaction 把别参与者条目 content 替换为 `[participant-specific conversation omitted]` 且 `participantId=''`。**Alter 侧端描述固定排除姓名/引用/私聊细节/建议措辞**。

**场景压缩**（compaction）：触发=条目数≥16 或字符≥10000 任一（可 force）；LLM 压缩产出 `{scene{hook,summary,close,presence}, arc, facts, statePatches}`；`hasCompactionEvidence` 要求 fact/patch 的 `sourceEntryIds` 与本次 entries 相交才持久化。`automaticDeliverySummary` 最多 6 项按 `participantId+sourceEntryId` 合并、content clip 240 字符，仅后台/自动推进阶段进 prompt。**承诺回访**每参与者最多 2 条，仅 `user-message`/`intent-due` 进 prompt，`notBefore` 限 5min–12h、`expiresAt` 限 24h，未答复 defer +20min。

**StatePatch/Perspective 门槛**：普通变化 `conf≥0.82` 且 `turns≥max(3,minTurns)` 且 `days≥max(1,minDays=2)`；major 只需 `conf≥0.95`；同 path 冷却 72h；证据按 `occurredAt` 去重计 turns、`calendarDayKey`（YYYY-MM-DD）计 days。

**盲模式（blindMode）**：`command/before-execute` 吞掉命令、middleware 识别 interlude 命令直接 return；error/warn 日志置 `blindModeHealthIssue` 并丢弃（隐藏错误/剧本预览）；心跳仅输出 `[失明模式] 运行状态=正常|需关注 后台任务=运行中|未就绪`，无故事/账号/模型/失败细节；`healthReportMinutes` 默认 10（钳制 1–1440）。

**分层日志 LogAction（16 枚举）**：`receive/send/processing/complete/trigger/emotion/memory/advance/agency/group/error/retry/warning/waiting/system`。KAOMOJI 与 SYMBOLS 双模式；明暗 256 色主题；三档密度 `summary<standard<diagnostic` 与 `logging.level` 独立叠加过滤；字段布局 `detectLogAction` 判定动作 + `extractFields` 正则抽键值 + tree 连接符（`├─`/`└─`）。

### 3.11 消息合并与过期请求取消（入口层，HDSI 提炼）

**短时合并**：同一关系分支的连续消息在 `mergeWindowMs`（默认 2 秒）内合并为同一轮事件，避免频繁打断主叙事。
**首条回复提交边界**：主模型尚未提交第一条回复时，新输入**接管本轮**并与旧批次合并重写；首条已提交后，新输入**截断尚未发送的 `<sep/>` 后续气泡**，被截断文字作为"主角本想发送但被打断"的未完成意图交给替代写作，清晰区分已说出口的对话与被打断的念头（`shouldSupersedeNarrativeRequest`：`firstMessageCommittedRequestId !== inFlightRequestId` 时新消息可作废在途请求）。
**过期请求不落库**：合并窗口内过期的模型结果直接丢弃，不写入剧本/记忆，避免陈旧输出污染生活记录。
**空白名确认**：同一关系分支的连续消息 2 秒合并；到期意图/自动推进与用户消息不会互相合并。

---

## 阶段 4：Alter 动态情绪 + Agency 主体约束 + 休息窗口

### 4.1 Alter System（agent/emotion/alter.py，替代固定情绪引擎）
追踪临时氛围惯性，只向主模型注入带方向/强度/权重的氛围参考。**精确公式（HDSI 提炼）**：
- 主叙事每轮返回整数 `alter`（-5..+5），normalize 取整限幅 `-5..5`，非法/非有限/缺失→忽略。
- 动态阈值：`density = min(最近一小时有效回合数 / 10, 1)`；`threshold = max(baseThreshold × 0.5, baseThreshold × (1 - density × densityFactor))`。
- 权重生命周期：已有 offset 时，同向 `weight += abs(alter) × sameDirectionBoost`；反向 `weight -= abs(alter) × oppositeDecay`；限制 0..1；低于 `minWeight` 清除旧 offset（反方向累计值保留，可自然触发新方向）。
- 达到阈值：本轮只保存累计状态并返回可见消息，不阻塞；后台 Alter 分析随后读取最近十段 script + 评分轨迹 + 全局 Overlay + 触发值 + 旧 offset，输出仅 `description` 字段。
- 强度：`intensity = min(abs(triggerValue) / threshold, maxIntensity)`；成功保存新 offset、权重=1、记录方向、清零累计；失败不清零、至少等 5 分钟重试。
- 注入主提示词格式：`emotionalOffset{direction, description, intensity, generatedAt, weight}`；内部累计值/上次方向/评分历史从 story.state 移除后再构造 payload，避免自我强化。
- 替代现有 agent/emotion 观察器/平滑器/注入器。

### 4.2 Agency Window（agent/proactive/agency.py，替代基础调度器）
主体行动现实容量门控。联系候选遵循「生活产生理由→检查日程/隐私/设备→立即/稍后重查/放下」。
- **三因素状态**：`activityLoad(free|occupied|overloaded)`、`privacy(private|shared|public)`、`deviceAccess(available|limited|unavailable)`，由真实剧本条目支撑，受 `maxWindowMinutes` 限制，过期不进入主模型。
- **联系候选**：来源（生活事件/承诺/实际安排/关系后续）、内容敏感度（普通/私人）、目标参与者、motive、真实来源条目、willingness、`send-now|recheck-later|let-go`、最早重查时间、过期时间。候选必须通过白名单，来源 ID 必须来自提供给模型的真实条目。
- **容量矩阵**：设备不可用/受限 → 不立即发；日程过载 → 不立即发；私人内容且环境不私密 → 不立即发；普通忙碌只允许承诺/实际安排突破；普通联系受 `minimumProactiveIntervalMinutes` 限制（承诺可绕过）；willingness 需达 `proactiveWillingnessThreshold`。
- **recheck-later**：复用 intent 表创建 `proactive-check`，只存 motive/来源/约束，不存预写消息；到期重新读取当前生活与 Agency，裁决 send-now / 再 recheck / let-go。同 `participantId+origin+sourceEntryIds` 的 pending 候选去重。
- 替代现有 schedule/idle 基础 `ProactiveScheduler`（旧 scheduler.py 移除）。

### 4.3 休息窗口（agent/proactive/rest_windows.py）
可配置低频推进时段（如 23:00-07:00），自动推进改用更长间隔。

### 4.4 配置示例（main.yml）
```yaml
cosmos:
  agent:
    alter:
      enabled: true
      base_threshold: 10.0            # 基础触发阈值，值越小越敏感
      density_factor: 0.3             # 对话密度影响因子
      same_direction_boost: 0.05      # 同向增强系数
      opposite_decay: 0.15            # 反向衰减系数
      min_weight: 0.2                 # 低于此权重清除旧 offset
      max_intensity: 2.0              # 强度上限
    agency:
      enabled: true
      max_window_minutes: 240         # 行动窗口上限
      min_proactive_interval_minutes: 60  # 普通联系最小间隔（承诺可绕过）
      max_candidate_hours: 24         # 联系候选有效期
    rest_windows:
      - enabled: true
        label: "night sleep"
        start: "23:00"
        end: "07:00"
        min_interval_minutes: 120     # 休息时段自动推进最小间隔
        max_interval_minutes: 240
    proactive_willingness_threshold: 0.7  # 主动联系意愿阈值
    timezone: "Asia/Shanghai"         # 故事本地时区（DB 存 UTC，渲染用本地）
    scheduler:
      sweep_interval_minutes: 5       # 到期 intent 扫描间隔
      advance_interval_minutes: 10    # 常规自动推进间隔（±jitter 抖动）
      advance_jitter_minutes: 2
      background_interval_minutes: 10 # 后台记忆/Overlay 压缩间隔
    narrator:
      max_script_characters: 8000     # script 长度上限
      min_delayed_seconds: 5          # delayed 回复 sendAt 下界
      max_delayed_minutes: 30         # delayed 回复 sendAt 上界
      max_context_characters: 20000   # 上下文总字符预算
```

---

## 阶段 5：场景/弧线管理 + 日志系统整体重新设计

### 5.1 场景/弧线管理（core/memory/scenes.py）
```python
@dataclass
class SceneEntry:
    id: int
    kind: str            # "user_message" | "ai_reply" | "tool_call" | "system_event"
    actor: str           # "user" | "aliya" | "system"
    content: str
    occurred_at: str
    participant_id: str = ""

@dataclass
class Scene:
    id: int
    status: str          # "active" | "closed"
    hook: str            # 场景要点
    summary: str         # 场景摘要
    entry_count: int
    started_at: str
    ended_at: str | None

@dataclass
class Arc:
    id: int
    status: str          # "active" | "closed"
    title: str
    summary: str
    scene_count: int
```
场景关闭阈值触发（条目数 / 字符数 / 静默时长，可配置），关闭时 LLM 压缩为 hook+summary 沉淀到 ContinuityLayer；`Arc` 聚合多条场景构成故事弧线，后台异步整理，与主叙事调用轻量协作。替代现有 `recent_context`/`context_length` 概念。

配置：
```yaml
cosmos:
  memory:
    scenes:
      entry_threshold: 16       # 条目数触发关闭
      char_threshold: 10000     # 字符数触发关闭
      silence_minutes: 30       # 静默时长触发关闭
      hook_chars: 2000          # hook 长度上限
      summary_chars: 8000       # summary 长度上限
```

### 5.2 日志系统整体重新设计（core/logger 重写）
分层日志核心 `core/logger/layered.py` + 重写 formatter/manager：
- **LogAction 16 枚举**（动作检测）：`receive/send/processing/complete/trigger/emotion/memory/advance/agency/group/error/retry/warning/waiting/system`，从消息内容正则判定。
- **阶段感知（Phase-aware）**：绑定主叙事/投递/Alter/Agency/场景压缩/自动推进，header 形如 `[类别] 主角名 + 颜文字 + summary`。
- **颜色主题**：暗色/亮色双主题（256 色 ANSI palette），字段色（protagonist/detail/user/success/alter/memory/warning/error）。
- **颜文字/符号双模式（kaomoji）**：KAOMOJI（如 receive `(*^▽^*)`）与 SYMBOLS（`←→⋯✓⚡`）可切换。
- **三档密度**（`summary<standard<diagnostic`）与 `logging.level`（silent<error<warn<info<debug）**独立叠加**过滤：summary=结果；standard=调度/模型活动；diagnostic=跳过原因/内部计数（含 `故事=id`）。
- **字段布局**：`extractFields` 正则抽 `键=值` 并映射中文 label，tree 连接符（`├─`/`└─`）纵向排布。
- **失明模式（blindMode）**：静默拦截命令、error/warn 置盲标志并丢弃、健康心跳无内容细节。
- **降级**：格式化失败回退默认 logging。

```yaml
cosmos:
  logger:
    layered:
      enabled: true
      colors: true
      color_theme: "dark"      # dark | light
      kaomoji: true
      density: "standard"      # summary | standard | diagnostic
    blind_mode:
      enabled: false
      health_report_minutes: 30
```

---

## 配置总表（main.yml 目标蓝图）

重构后 `data/config/main.yml` 的目标结构（各段分布在对应阶段设计小节，此处汇总）：

```yaml
cosmos:
  characters:
    ai_name: Aliya
    user_name: cosmos
  logger:
    layered: { enabled, colors, color_theme, kaomoji, density }
    blind_mode: { enabled, health_report_minutes }
  service:
    agent:
      ws_server: { host, port }
      timezone: "Asia/Shanghai"
      story: {}                          # 主剧本配置
      narrator:
        max_script_characters: 8000
        min_delayed_seconds: 5
        max_delayed_minutes: 30
        max_context_characters: 20000
      scheduler:
        sweep_interval_minutes: 5
        advance_interval_minutes: 10
        advance_jitter_minutes: 2
        background_interval_minutes: 10
      alter:
        enabled: true
        base_threshold: 10.0
        density_factor: 0.3
        same_direction_boost: 0.05
        opposite_decay: 0.15
        min_weight: 0.2
        max_intensity: 2.0
      agency:
        enabled: true
        max_window_minutes: 240
        min_proactive_interval_minutes: 60
        max_candidate_hours: 24
        proactive_willingness_threshold: 0.7
      rest_windows:
        - enabled: true
          label: "night sleep"
          start: "23:00"
          end: "07:00"
          min_interval_minutes: 120
          max_interval_minutes: 240
      permissions: { config_path }
      channels: { feishu, wechat }        # 保留，接口重构
      mcp: { config_path }
      knowledge: { dir }
    memory:
      layers:
        overlay: { confidence_threshold: 0.82, major_confidence_threshold: 0.95, min_turns: 3, min_days: 2, cooldown_hours: 72 }
        fact:
          max_facts: 200
          importance_weight: 0.5
          confidence_weight: 0.35
          recency_weight: 0.15
      storage:
        backend: "sqlite_vec"             # sqlite_vec | in_memory（降级）
        sqlite_path: data/memory/memory.db
        dimension: 4096
      scenes:
        entry_threshold: 16
        char_threshold: 10000
        silence_minutes: 30
        hook_chars: 2000
        summary_chars: 8000
    vector:                               # embedding 配置保留
      enabled: true
      embedding: { model, url, api_key, batch_size, concurrency, dimension }
    llm:
      providers: { name, config_path }
      cot_enabled: true
      reasoning_effort: high
```

## 依赖与 pyproject 变更

**新增依赖**：
- `aiosqlite`（异步 SQLite，记忆/剧本持久化）
- `sqlite-vec`（向量索引扩展，记忆向量检索）

**保留依赖**：`openai`（LLM + embedding）、`httpx`、`PyYAML`、`fastapi`/`uvicorn`（WS 服务）、`numpy`（若降级路径用余弦）、`pydantic`

**移除依赖**：
- `edge-tts`（TTS 移除）
- `numpy` 若仅 TTS 使用则移除（但 vector 降级路径可能仍用——需实现时确认）
- 若 `py2neo` 仅旧 GRAG 使用则移除（新记忆不用 Neo4j）

> 注：Neo4j/py2neo、AstraTTS 相关依赖随旧记忆系统与 TTS 移除一并清理；docker 配置（AstraTTS 服务）保留但不再被 Python 代码依赖。

---

## 文件变更汇总

| 阶段 | 新增 | 重写 | 移除 |
|------|------|------|------|
| 0 | - | - | core/tts/*（保留 docker 配置）, data/config/TTSProviders.json, pyproject.toml 的 TTS 依赖, main.yml 的 tts 段, agent/events.py 的 TTS_FEATURES, README/CLAUDE.md 的 TTS 描述 |
| 1 | core/infra/__init__.py, core/infra/singleton.py, agent/queue.py | core/config/manager.py（LRU 缓存）, agent/session.py | - |
| 2 | core/memory/layers/*, core/memory/storage/*（SQLite+sqlite-vec）, core/memory/config.py（新） | core/memory/memory_manager.py（分层门面） | core/memory 旧实现：graph/extractor/rag_query/task_manager/hierarchical/config/_providers/_retry/_utils/exceptions |
| 3 | agent/story/*, agent/narrator.py, agent/metadata_parser.py, agent/config.py, agent/protocol.py, agent/queue.py | agent/loop.py, agent/context.py, agent/events.py（协议重构）, agent/ws.py（WS/GUI 协议重构）, agent/session.py, agent/session_store.py, agent/app.py, agent/main.py, agent/channels/（渠道接口重构） | 两阶段 FC 循环（_tool_phase/_soul_phase）, 旧事件类型（StepStarted/StepFinished/ToolCall*） |
| 4 | agent/emotion/alter.py, agent/proactive/agency.py, agent/proactive/rest_windows.py, agent/proactive/scheduler.py（重写） | agent/loop.py（集成） | 旧固定情绪引擎（observer/smoother/injector）, 旧 ProactiveScheduler 的 schedule/idle 触发式调度 |
| 5 | core/memory/scenes.py, core/logger/layered.py | core/logger/formatter.py, core/logger/manager.py | 旧日志结构（JSONFormatter/StructuredFormatter 并入重写后 formatter） |

## 实现顺序与依赖
```
阶段0（移除 TTS，前置清理，独立）
  └── 阶段1（基础设施：单例/队列/配置缓存）
        └── 阶段2（分层记忆，依赖单例；存储用 SQLite+sqlite-vec）
              └── 阶段3（主叙事单次写作，依赖队列+分层记忆+story 包）
                    └── 阶段4（Alter/Agency，依赖结构化输出）
                          └── 阶段5（场景/日志，独立可并进）
```

## 风险与降级策略
| 模块 | 降级行为 |
|------|----------|
| 记忆层 | 某层不可用时跳过，不阻塞主流程 |
| 结构化输出 | JSON 解析失败回退纯文本模式 |
| Alter | 禁用或失败时不注入氛围 prompt |
| Agency | 禁用时允许所有主动联系 |
| 场景压缩 | LLM 失败时用简单摘要 |
| 分层日志 | 格式化失败回退默认格式 |
