# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

《彼方的她-Aliya》—— 基于 LLM 的 AI 伴侣桌面应用。Python 后端提供 WebSocket 服务（LLM 对话、记忆系统），Electron + Vue 3 前端负责 Live2D 角色呈现与状态面板。角色设定取材自瞳电游的文字冒险游戏《彼方的她-Aliya》。

## 常用命令

依赖管理使用 **uv**（锁文件 `uv.lock`）。

```bash
# 安装依赖（含开发工具）
uv sync --extra dev --extra lint

# 启动后端：默认 WS 服务（端口 8765）；--chat 终端聊天；--chat-ws 终端+WS 广播
uv run python main.py
uv run python main.py --chat

# 前端（Electron + Vue 3）
cd GUI && npm install && npm run dev   # vite:dev 可只起渲染进程

# 测试（pytest 已配置 asyncio auto 模式 + 默认 coverage）
uv run pytest
uv run pytest tests/agent/test_pipeline.py -k handle_user_message --no-cov   # 单测/单用例

# 代码质量（来自 [lint] optional-dependencies）
uv run black . && uv run isort .
uv run flake8
uv run mypy agent core

# Docker 编排（app + neo4j + astratts）
docker compose up
```

运行后端前需在 `.env` 配置 `DEEPSEEK_API_KEY`（参考 `.env.example`）。Neo4j 图记忆为可选——对应服务未启动时相关能力会自动降级禁用，不阻塞主流程。

## 环境变量配置

项目使用 `.env` 文件管理环境变量，参考 `.env.example`：

| 变量 | 必填 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API 密钥（用于 LLM 对话） |
| `NEO4J_PASSWORD` | 否 | Neo4j 数据库密码（可选，默认使用 main.yml 中的值） |
| `WS_HOST` | 否 | WebSocket 监听地址（默认 `0.0.0.0`，容器内使用） |
| `WS_PORT` | 否 | WebSocket 监听端口（默认 `8765`） |
| `NEO4J_HOST` | 否 | Neo4j 服务地址（默认 `127.0.0.1`，compose 网络内用 `neo4j`） |
| `NEO4J_PORT` | 否 | Neo4j Bolt 端口（默认 `7687`） |

## 架构总览


### 核心服务（core/）

- **config/** — YAML 配置管理器：`get_config_instance()` 单例，点路径读写（`cfg.get("cosmos.service.llm")`），支持 `${ENV_VAR}` / `${ENV_VAR:default}` 占位符解析与热重载。
- **llm/** — `ConversationService` 管理单会话历史（`history_max_chars=90000` 超限清理最旧消息），异步优先（`asend`/`astream_send`）。提供商通过 `ProviderRegistry` 注册表管理，默认注册 `OpenAICompatibleProvider`（`providers/openai_compatible.py`），通过 `LLMProviders.json` 区分 deepseek/ollama/lmstudio 等。支持动态注册新的提供商类型。`create_from_config()` 是从 YAML 创建服务的统一入口。
- **memory/** — **两套并行记忆系统**（见下）。
- **vector/** — 余弦相似度内存向量库，用于情绪向量分类器。
- **logger/**、**exception/** — 统一日志（YAML 配置：console/file/轮转）与异常体系。

### 记忆系统：两套并行

- **GRAG**（`core/memory/memory_manager.py` 等）— 借鉴 NagaAgent summer_memory：LLM 五元组提取（`extractor.py`，6 元素含类别契约；**放宽策略**：闲聊不再一刀切拒绝、未知实体类型降级「概念」、代词主体按说话人自动调整（`_detect_speaker` 解析文本说话人，"我"→当前说话人、"你"→对话另一方）、移除噪声宾语过滤；仍保留去重/截断/类别白名单）→ Neo4j 图存储（`graph.py`，Schema v4，去 APOC 依赖）→ RAG 召回（`rag_query.py`，检索支持 `include_source` 返回 6 元素元组含来源文本，回答生成引用「来源：…」）→ 并发任务管理（`task_manager.py`）。`get_memory_manager()` 返回集成层 `GRAGMemoryManager`，在 `after_turn` 保存对话记忆、在灵魂阶段注入相关记忆。
- **层次化**（`core/memory/hierarchical.py`）— 参考 LAAP 第 10 章：感知/工作/情景/语义/程序五层 + 元记忆，跨层巩固（重复达阈值触发），JSON 持久化。已接入 `GRAGMemoryManager`：随 `add_conversation_memory()` 同步写入五层，五元组落库时经 `collect_entity_memory_attrs()` 聚合后把五层记忆属性（层/重要性/置信度等）挂载到 Entity 节点（`memory_*` 属性）。遗忘机制双层 + 向量联动：`apply_forgetting()` 按 Ebbinghaus 曲线永久衰减内存各层数值并清理低值条目（同时按 metadata/text 精确删除向量索引中的被遗忘条目及 pending 待同步文本，杜绝幽灵记忆；`add_conversation_memory` 按计数 20 次对话 + 时间 24h 双驱动自动触发，自动路径联动图节点属性衰减）；`graph.decay_memory_nodes` / `prune_memory_nodes`（UNWIND 批量）/ `query_memory_nodes` 支持对图节点执行衰减/清理/查询，`run_memory_forgetting()` 手动编排完整流程。`core/vector/store.py` 提供 `find_ids` / `delete_many` 供遗忘清理定位与批量删除。

### 配置体系

- 主配置 `data/config/main.yml` 为唯一配置源头（日志、WS 端口、情绪、认知、GRAG、向量、LLM、prompt）。所有服务通过 `core.config.get_config_instance()` 读取，各自用 `*_from_config()` 工厂构建。
- 提供商配置分离：`data/config/LLMProviders.json`；密钥走环境变量（`.env` → `${DEEPSEEK_API_KEY}`）。
- 工具权限：`data/config/Permissions.yml`。

## 关键约定

- **代码注释与文档均为中文**，标识符/代码语法保留英文。
- 全部异步优先：LLM/记忆均为 `async`，核心路径不得阻塞事件循环；同步包装仅用于脚本/REPL。
- 单测按模块分目录（`tests/agent`、`tests/memory`、`tests/llm` 等）。pytest `asyncio_mode = "auto"`（无需 `@pytest.mark.asyncio`），默认开启 coverage，可用 `--no-cov` 关闭。markers：`slow`/`integration`/`unit`/`memory`/`llm`。
- 类型检查：`pyright`（`pyrightconfig.json`，standard 模式）与 `mypy` 双轨；`core/config`、`core/logger`、`agent/tools` 已启用 mypy 严格模式。
- 格式：Black（100 列）+ isort（black profile，first-party 为 agent/core/GUI/aliya_cosmos）。
- 降级原则贯穿全项目：可选依赖（Neo4j）初始化失败一律告警降级，不使 Agent 主流程崩溃。

## 常见问题

### LLM 模块重构后注意事项
- `OpenAICompatibleProvider` 现在通过 `ProviderRegistry` 注册表管理，不再直接导出
- 添加新提供商需继承 `LLMProvider` 并调用 `ProviderRegistry.register()` 注册
- 配置中可通过 `provider_type` 字段指定提供商类型（默认 `"openai_compatible"`）

### 流式对话
- `astream_send` 缓冲完整响应后再 yield，确保重试时不会污染输出
- 调用方无需处理重复 token，重试逻辑完全透明

### 记忆系统
- GRAG 和层次化记忆系统并行工作，互相独立
- Neo4j 为可选依赖，未启动时自动降级禁用
