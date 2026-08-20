# Memory 模块

基于知识图谱的长期记忆系统（GRAG），从对话中提取五元组关系并存储到 Neo4j，支持 RAG 语义检索。

## 运行演示

`python core/memory/example.py` 运行完整演示。示例 8（`example_memory_retrieval`）专门展示**记忆检索**的四种视角：
1. 关键词图谱查询（`query_graph_by_keywords`，`include_source=True` 返回含来源文本的 6 元素元组）；
2. 按时间链/日期查询（`query_quintuples_by_day_async`，含来源文本）；
3. 记忆节点查询（`query_memory_nodes`，展示实体挂载的五层记忆属性：层/热度/重要性等）；
4. RAG 问答（`query_memory`，关键词 → 图谱 → LLM 回答）。

## 架构概览

```
memory_manager.py   ← 集成层，对外统一入口（持有五层层次化记忆）
    ├── extractor.py    ← 五元组提取（LLM 调用）
    ├── graph.py        ← Neo4j 图谱读写（按天独立实体节点，可挂载五层记忆属性）
    ├── hierarchical.py ← 五层层次化记忆（感知/工作/情景/语义/程序 + 元记忆，随对话写入）
    ├── rag_query.py    ← RAG 知识检索（关键词提取 + 图谱查询 + 回答生成）
    ├── task_manager.py ← 异步并发任务队列
    ├── config.py       ← 配置加载与校验
    ├── exceptions.py   ← 异常定义
    ├── _providers.py   ← 共享 LLM Provider 懒加载
    ├── _retry.py       ← 指数退避重试工具
    └── _utils.py       ← JSON 解析工具
```

**数据流**：`add_conversation_memory()` → 同步写入五层层次化记忆 + 提交异步提取任务 → LLM 提取五元组 → 写入 Neo4j Entity/Day 节点（实体的五层记忆属性经 `collect_entity_memory_attrs()` 聚合后挂载为节点 `memory_*` 属性）→ `query_memory()` 时 RAG 检索并生成回答。

**检索来源文本**：图谱关系存有 `source_text`（写入时的对话原文）。查询函数（`query_graph_by_keywords` / `query_quintuples_by_day`）新增 `include_source=True` 参数——为 True 时返回 6 元素元组 `(head, head_type, rel, tail, tail_type, source_text)`，否则保持 5 元素向后兼容。RAG 回答生成自动启用来源文本，提示词中标注「来源：…」供回答引用。

### 双平行时间链

记忆默认同时写入 **aliya | user** 两条时间链（千年时空设定：COSMOS 身处当下，Aliya 在千年之后）：

- `add_conversation_memory()` 不传 `timeline` 时**默认双写**两条链；aliya 链只提取 AI 发言、user 链只提取用户发言，避免跨链实体串扰。
- `timeline` 以 `"|"` 分隔可显式指定多条链（如 `"aliya|user"`），或传单条链只写该链。
- aliya 时间链日期相对用户正常时间向后偏移 1000 年（`_shift_timeline_date`）。该函数**幂等**：若传入日期已处于 aliya 偏移时间线（年份 ≥ 3000），则不再重复 +1000——调用方传"已预偏移日期"或"正常日期"均可，避免二次偏移导致时间链重复。
- **空提取防断链**：某天对话提取结果为 0 个五元组（闲聊被过滤/LLM 返回空数组）时，`_on_task_completed` 不再直接跳过——`touch_day()` 仍会创建该天的 Day 节点并串联 NEXT_DAY，保证时间链连续（日期本身代表对话发生过）。五元组全被过滤（如非法关系类型）时 `store_quintuples` 同样兜底建 Day。

### 提取规则（放宽策略）

五元组提取（`extractor.py`）采用**放宽**策略，尽可能保留记忆信息：

- **闲聊不再一刀切拒绝**：问候寒暄、情感表达、调侃等文本若包含人物互动关系（类别「人际」）或情感状态，仍会提取；仅完全无实体/互动主体时返回 `[]`。
- **实体类型白名单放宽**：未知类型（LLM 输出的白名单外类型）不再跳过，主体/宾语类型降级为「概念」。
- **代词主体按角色自动调整**：LLM 未替换人称代词时，代码级兜底——从对话文本解析当前说话人（`_detect_speaker`），「我」替换为**当前说话人**（aliya 链→`ai_name`、user 链→`user_name`），「你」替换为**对话中的另一方**（类型设为「人物」）；无法解析说话人时回退（我→`user_name`、你→`ai_name`）。
- **噪声宾语过滤移除**：宾语为"对方…"式表达、空泛互动等不再被过滤。
- 仍保留的校验：6 元素契约、类别白名单、空字段跳过、超长字段截断（64 字符）、完全重复去重。

## 遗忘机制（支持图节点操作）

遗忘机制分为内存层与图节点两层，均按 Ebbinghaus 遗忘曲线 `2^(-age/half_life)` 衰减：

**内存层**（`HierarchicalMemory.apply_forgetting()`）：
- 情景记忆 `importance` 按 1 天半衰期、语义 `confidence` / 程序 `success_rate` 按 7 天半衰期、元记忆 `heat` 按 30 天半衰期**永久衰减**（区别于召回排序时的临时因子）。
- 衰减后数值低于 `_MEMORY_FORGET_THRESHOLD`（0.1）的条目被移除。
- **向量索引联动**：被遗忘条目同时从向量存储清除（语义按 metadata `layer+key`、程序按 `layer+name`、情景按文本精确匹配），并清理尚未入库的待同步队列，杜绝"被遗忘内容仍能被向量召回"的幽灵记忆；清理数计入 `vector_purged` 统计。
- 触发方式：由 `add_conversation_memory()` 按**计数 + 时间双驱动**自动触发（每 `_FORGET_CHECK_INTERVAL`（20）次对话，或距上次维护超过 `_FORGET_MAX_INTERVAL`（24 小时）即触发）；**首次对话仅初始化时间基准**，不在空记忆上执行无意义衰减；自动路径同步执行内存层遗忘 + 图节点属性衰减（不删节点，避免误删知识图谱）；或由维护入口 `run_memory_forgetting()` 显式调用。

**图节点操作**（`graph.py`，作用于 Entity 节点挂载的 `memory_*` 属性）：
- `decay_memory_nodes()` / `decay_memory_nodes_async()`：按 `memory_updated_at` 距今时长批量衰减节点属性（importance/attention_weight/heat 用短半衰期，confidence/success_rate 用长半衰期），并重置 `memory_updated_at`。
- `prune_memory_nodes()` / `prune_memory_nodes_async()`：清理强度（各属性最大值）低于阈值的被遗忘节点——默认仅移除 `memory_*` 属性（实体作为知识图谱一部分保留），`delete_entity=True` 时 DETACH DELETE 整节点。
- `query_memory_nodes()` / `query_memory_nodes_async()`：按当前（衰减后）记忆强度查询挂载五层记忆属性的节点。

**统一维护入口**：`GRAGMemoryManager.run_memory_forgetting()` 一次执行内存层遗忘 + 图节点衰减/清理 + **孤立 Entity 节点自动清理**（`cleanup_orphans=True`，无任何关系：入边/出边/[:ON_DAY]），返回两层统计。自动遗忘路径（`add_conversation_memory` 每 20 次对话或 24 小时触发）同样自动清理孤立节点，防止无用节点长期累积。

## 核心接口

### `get_memory_manager()` — 获取管理器单例

```python
from core.memory import get_memory_manager

mgr = get_memory_manager()
```

线程安全的懒加载单例，多次调用返回同一实例。

---

### `GRAGMemoryManager`

#### `add_conversation_memory()`

```python
success = await mgr.add_conversation_memory(
    user_input="我喜欢喝咖啡",
    ai_response="好的，我记住了",
    session_id="sess_001",   # 可选，用于图谱关系元属性
    day_date="2026-07-11",   # 可选，关联 Day 节点
    timeline="user",         # 可选，时间链标识（"user" / "aliya"）
)
```

将对话追加到 `recent_context`，若 `auto_extract=True` 则异步提交五元组提取任务。

#### `query_memory()`

```python
result = await mgr.query_memory("我有什么饮食偏好？")
# 返回 str 或 None（无结果时）
```

执行 RAG 检索：提取关键词 → 图谱查询 → LLM 生成回答。

#### `get_relevant_memories()`

```python
quintuples = await mgr.get_relevant_memories("咖啡", limit=5)
# 返回 List[Tuple[str, str, str, str, str]]
# 每个元素: (主体, 主体类型, 谓语, 宾语, 宾语类型)
```

直接返回图谱五元组，不经过 LLM 生成。

#### `get_memory_stats()`

```python
stats = mgr.get_memory_stats()
# {
#   "enabled": True,
#   "context_length": 8,      # 当前缓存的对话条数
#   "cache_size": 42,          # 提取缓存条数
#   "inflight_count": 1,       # 进行中的提取任务数
#   "active_tasks": 1,
#   "task_manager": {...},
#   "graph": {...}             # Neo4j 实体/关系/Day 节点统计
# }
```

#### `clear_memory()`

```python
success = await mgr.clear_memory()
```

清空 `recent_context`、提取缓存、活跃任务，并删除 Neo4j 中所有 Entity 和 Day 节点。

---

### `create_memory_service()` — 完整服务初始化

```python
from core.memory import create_memory_service

builder, engine, recall, client, ext = create_memory_service()
```

| 返回值    | 类型                    | 说明                           |
| --------- | ----------------------- | ------------------------------ |
| `builder` | `GRAGMemoryManager`     | 记忆管理器（添加对话）         |
| `engine`  | `dict`                  | 图谱操作函数集合（同步+异步）  |
| `recall`  | `RAGQueryEngine`        | RAG 检索引擎                   |
| `client`  | `QuintupleTaskManager`  | 任务管理器                     |
| `ext`     | `QuintupleExtractor`    | 五元组提取器                   |

---

## 五元组格式

所有五元组均为 `(主体, 主体类型, 谓语, 宾语, 宾语类型)` 的五元字符串元组。

```python
("我", "人物", "喜欢喝", "咖啡", "食物")
("Alice", "人物", "工作于", "北京", "地点")
```

合法实体类型约 40 种，涵盖人物、地点、组织、物品、时间、概念等，完整列表见 `extractor.py` 的 `VALID_ENTITY_TYPES`。

---

## 图谱 Schema（按天独立实体节点，含角色）

**节点**

| 标签         | 关键属性                                              | 约束/索引                          |
| ------------ | ----------------------------------------------------- | ---------------------------------- |
| `Entity`  | `name`, `entity_type`, `aliases`, `day_date`, `timeline`, `*_at` | `(name, day_date, timeline)` 组合唯一（每天每条时间链独立节点，同一角色如 Aliya 在不同天/链是不同节点） |
| 备注 | 实体即按天独立节点，无独立 EntityDay 节点 |  |
| `Day`        | `date`, `timeline`, `*_at`                           | `(date, timeline)` 组合唯一        |

**关系**

| 关系类型      | 说明                                                                  |
| ------------- | --------------------------------------------------------------------- |
| 五元组谓语    | `(Entity)-[PREDICATE {day_date, timeline}]->(Entity)，连接按天实体，`occurrence` 累加 |
| `INSTANCE_OF` | 无 INSTANCE_OF 关系；ON_DAY 由 Day 直接连按天实体 |
| `ON_DAY`      | `(Day)-[:ON_DAY]->(Entity)`，Day 关联当天被提及的按天实体（每个按天实体仅归属其唯一 Day）        |
| `NEXT_DAY`    | `(Day)-[:NEXT_DAY]->(Day)`，同一时间链上的时序链（乱序落库也连续）                        |

---

## 配置

配置路径：`cosmos.service.grag`（`data/config/main.yml`）

```yaml
cosmos:
  service:
    grag:
      enabled: true
      auto_extract: true       # 是否自动触发五元组提取
      context_length: 10       # recent_context 最大保留条数
      similarity_threshold: 0.7

      neo4j:
        uri: bolt://localhost:7687
        user: neo4j
        password: "your_password"   # enabled=true 时必须配置
        database: neo4j

      extractor:
        max_retries: 2
        timeout: 30

      task_manager:
        max_workers: 3
        max_queue_size: 100
        task_timeout: 30
        auto_cleanup_hours: 24
        cleanup_interval_seconds: 3600
```

配置变更后调用 `reload_config()` 重新加载，或依赖自动监听回调（需先调用 `init_config_listener()`）。

---

## 异常

所有异常继承 `GRAGError`（`StructuredException` 子类），错误码前缀 `MEM_`。

| 异常类                  | 错误码     | 场景                        |
| ----------------------- | ---------- | --------------------------- |
| `GRAGConfigError`       | `MEM_001`  | 配置校验失败                |
| `GRAGNotEnabledError`   | `MEM_002`  | GRAG 未启用                 |
| `GraphConnectionError`  | `MEM_101`  | Neo4j 连接失败              |
| `GraphQueryError`       | `MEM_102`  | 图谱查询异常                |
| `GraphWriteError`       | `MEM_103`  | 图谱写入异常                |
| `ExtractionTimeoutError`| `MEM_201`  | LLM 提取超时                |
| `LLMProviderError`      | `MEM_203`  | LLM 调用失败                |
| `RAGQueryError`         | `MEM_300`  | RAG 检索失败                |
| `RAGGenerationError`    | `MEM_302`  | LLM 回答生成失败            |
| `TaskQueueFullError`    | `MEM_401`  | 任务队列已满                |
| `TaskTimeoutError`      | `MEM_402`  | 任务执行超时                |

---

## 依赖关系

- 内部：`core.llm`（LLM 调用）、`core.config`（配置管理）、`core.logger`（日志）、`core.exception`（异常基类）
- 外部：`py2neo`（Neo4j 客户端，可选——未安装时图谱功能自动降级）
