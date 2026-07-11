# Memory 模块

基于知识图谱的长期记忆系统（GRAG），从对话中提取五元组关系并存储到 Neo4j，支持 RAG 语义检索。

## 架构概览

```
memory_manager.py   ← 集成层，对外统一入口
    ├── extractor.py    ← 五元组提取（LLM 调用）
    ├── graph.py        ← Neo4j 图谱读写（Schema v4）
    ├── rag_query.py    ← RAG 知识检索（关键词提取 + 图谱查询 + 回答生成）
    ├── task_manager.py ← 异步并发任务队列
    ├── config.py       ← 配置加载与校验
    ├── exceptions.py   ← 异常定义
    ├── _providers.py   ← 共享 LLM Provider 懒加载
    ├── _retry.py       ← 指数退避重试工具
    └── _utils.py       ← JSON 解析工具
```

**数据流**：`add_conversation_memory()` → 提交异步提取任务 → LLM 提取五元组 → 写入 Neo4j Entity/Day 节点 → `query_memory()` 时 RAG 检索并生成回答。

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

## 图谱 Schema（v4）

**节点**

| 标签       | 关键属性                                  | 约束/索引                   |
| ---------- | ----------------------------------------- | --------------------------- |
| `Entity`   | `name`, `entity_type`, `aliases`, `*_at` | `name` 唯一约束             |
| `Day`      | `date`, `timeline`, `*_at`               | `(date, timeline)` 组合唯一 |

**关系**

| 关系类型      | 说明                                       |
| ------------- | ------------------------------------------ |
| 五元组谓语    | `(Entity)-[PREDICATE]->(Entity)`，支持 `occurrence` 累加 |
| `ON_DAY`      | `(Entity)-[:ON_DAY]->(Day)`，实体与日期的关联 |
| `NEXT_DAY`    | `(Day)-[:NEXT_DAY]->(Day)`，同一时间链上的时序链 |

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
