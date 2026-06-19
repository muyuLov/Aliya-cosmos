# GRAG 记忆系统

基于 Neo4j 图谱的知识记忆系统，支持对话记忆、五元组提取、RAG 查询和并发任务处理。

## 功能特性

- **对话记忆**: 自动提取对话中的知识并存储到图谱
- **五元组提取**: 使用 LLM 从文本中提取结构化关系
- **知识图谱**: 基于 Neo4j 的图谱存储和查询
- **RAG 查询**: 结合图谱检索和 LLM 生成的智能问答
- **并发处理**: 异步任务管理器支持并发五元组提取
- **统计监控**: 完整的系统状态和性能统计

## 快速开始

### 1. 环境准备

确保以下服务正在运行：
- Neo4j 数据库 (默认: `bolt://localhost:7687`)
- LLM 服务 (通过 `core/llm` 模块配置)

### 2. 配置文件

在 `data/config/main.yml` 中配置 GRAG 系统：

```yaml
cosmos:
  service:
    grag:
      enabled: true                    # 启用记忆系统
      auto_extract: true              # 自动提取五元组
      context_length: 10              # 上下文长度
      neo4j:
        uri: "bolt://localhost:7687"
        user: "neo4j"
        password: "your_password"
        database: "neo4j"
      extractor:
        max_retries: 2
        timeout: 30
      task_manager:
        max_workers: 3
        max_queue_size: 100
```

### 3. 基础使用

```python
import asyncio
from memory import get_memory_manager

async def basic_usage():
    # 获取记忆管理器
    memory = get_memory_manager()
    
    # 添加对话记忆
    await memory.add_conversation_memory(
        user_input="我叫张三，是一名软件工程师。",
        ai_response="很高兴认识你，张三！",
        session_id="demo"
    )
    
    # 查询记忆
    answer = await memory.query_memory("张三是做什么工作的？")
    print(answer)

asyncio.run(basic_usage())
```

## 示例文件

- **`quick_start.py`**: 最简单的使用示例
- **`example.py`**: 完整功能演示，包含 6 个详细示例

运行示例：
```bash
# 快速开始
python memory/quick_start.py

# 完整演示
python memory/example.py
```

## 核心组件

### 1. 记忆管理器 (MemoryManager)

主要接口，集成所有功能：

```python
from memory import get_memory_manager

memory = get_memory_manager()

# 添加对话记忆
await memory.add_conversation_memory(user_input, ai_response)

# 查询记忆
answer = await memory.query_memory("问题")

# 获取统计信息
stats = memory.get_memory_stats()
```

### 2. 五元组提取器 (Extractor)

从文本中提取结构化关系：

```python
from memory.extractor import extract_quintuples

# 提取五元组：(主体, 主体类型, 谓语, 宾语, 宾语类型)
quintuples = await extract_quintuples("张三在北京工作。")
# 结果: [("张三", "人物", "在", "北京", "地点"), ("张三", "人物", "工作", "", "")]
```

### 3. 图谱操作 (Graph)

直接操作 Neo4j 图谱：

```python
from memory import store_quintuples, query_graph_by_keywords

# 存储五元组
quintuples = [("张三", "人物", "工作于", "阿里巴巴", "组织")]
store_quintuples(quintuples)

# 关键词查询
results = query_graph_by_keywords(["张三", "阿里巴巴"])
```

### 4. RAG 查询引擎 (RAGQuery)

智能问答系统：

```python
from memory.rag_query import query_knowledge_async

# RAG 查询（关键词提取 + 图谱检索 + LLM 生成）
answer = await query_knowledge_async("张三在哪里工作？")
```

### 5. 任务管理器 (TaskManager)

并发处理五元组提取：

```python
from memory.task_manager import get_task_manager

task_mgr = get_task_manager()

# 提交任务
task_id = await task_mgr.add_task("待提取的文本")

# 获取结果
result, error = await task_mgr.get_task_result(task_id)
```

## 数据结构

### 五元组格式
```python
QuintupleType = Tuple[str, str, str, str, str]
# (主体, 主体类型, 谓语, 宾语, 宾语类型)
# 例如: ("张三", "人物", "工作于", "阿里巴巴", "组织")
```

### 图谱 Schema
- **节点**: `(:Entity)`
  - 属性: `name`(唯一约束), `entity_type`(有索引), `aliases`, `created_at`, `updated_at`
  - 不依赖 APOC 插件，实体类型通过 `entity_type` 属性存储
- **关系**: `(e1)-[r:REL_TYPE]->(e2)`
  - 属性: `source_text`, `session_id`, `confidence`, `occurrence`, `created_at`, `updated_at`

## 异常处理

系统提供完整的异常层次：

```python
from memory.exceptions import (
    GRAGError,           # 基础异常
    GraphConnectionError, # 图谱连接错误
    ExtractionError,     # 提取错误
    RAGQueryError,       # RAG 查询错误
    TaskManagerError,    # 任务管理错误
)

try:
    await memory.add_conversation_memory(user_input, ai_response)
except GRAGError as e:
    print(f"记忆系统错误: {e}")
```

## 性能优化

1. **连接池**: Neo4j 连接自动管理，失败后有冷却期
2. **并发处理**: 任务管理器支持多工作协程并发提取
3. **缓存机制**: 避免重复提取相同文本
4. **异步操作**: 所有 I/O 操作都有异步版本
5. **自动清理**: 定期清理过期任务和缓存

## 监控和维护

### 获取系统状态
```python
from memory import get_service_status, get_graph_stats

# 服务状态
status = get_service_status()

# 图谱统计
stats = get_graph_stats()
```

### 清理操作
```python
# 清空所有记忆
await memory.clear_memory()

# 清理已完成任务
await task_mgr.clear_completed_tasks()
```

## 故障排除

### 常见问题

1. **Neo4j 连接失败**
   - 检查 Neo4j 服务是否运行
   - 验证连接配置 (URI, 用户名, 密码)
   - 查看日志中的连接错误信息

2. **五元组提取失败**
   - 检查 LLM 服务配置
   - 验证 `core/llm` 模块是否正常工作
   - 查看提取器日志

3. **任务队列满**
   - 增加 `max_queue_size` 配置
   - 增加 `max_workers` 数量
   - 清理已完成任务

4. **记忆查询无结果**
   - 确认五元组已成功提取和存储
   - 检查关键词提取是否正确
   - 验证图谱中是否有相关数据

### 日志调试

启用调试日志：
```python
from core.logger import get_logger
logger = get_logger("memory")
logger.setLevel("DEBUG")
```

## 扩展开发

### 自定义提取器
```python
from memory.extractor import QuintupleExtractor

class CustomExtractor(QuintupleExtractor):
    def _parse_response(self, content: str):
        # 自定义解析逻辑
        pass
```

### 自定义 RAG 策略
```python
from memory.rag_query import RAGQueryEngine

class CustomRAGEngine(RAGQueryEngine):
    async def _extract_keywords(self, question: str) -> list[str]:
        # 自定义关键词提取（必须为异步方法）
        pass
```

## 许可证

本项目遵循项目根目录的许可证条款。