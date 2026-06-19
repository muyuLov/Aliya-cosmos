# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

《彼方的她-Aliya》

## 常用命令

```bash
# 运行 GUI 桌面客户端
aliya-gui

# 代码格式化
black .
isort .

# 类型检查（宽松模式）
mypy .

# 运行测试
pytest
pytest tests/test_file.py -v           # 运行单个测试文件
pytest tests/test_file.py::test_func   # 运行单个测试函数

# 启动基础设施（Neo4j + AstraTTS）
cd docker && docker-compose up -d

# 包管理（使用 uv）
uv sync
uv add <package>    # 添加依赖
uv remove <package>  # 移除依赖
uv tree             # 查看依赖树
```

## 项目架构

### 整体分层

```
agent/           ← AI Agent 层：对话主循环、Brain 思考引擎、工具系统、Skill 系统
core/            ← 核心服务层：LLM、TTS、配置管理、日志、异常
memory/          ← 记忆系统层：GRAG (Neo4j 图谱记忆、五元组提取、RAG 查询)
GUI/             ← 桌面客户端层
docker/          ← 基础设施：docker-compose 编排 Neo4j + AstraTTS
data/            ← 配置文件和数据目录
```

### 核心模块详解

**agent/ — AI Agent 引擎**
- `agent.py` — `AliyaAgent` 主循环编排器，管理 WebSocket 连接、消息路由、记忆检索/存储
- `brain.py` — `BrainEngine` LLM 思考引擎，支持多轮迭代推理（最多 5 轮），管理上下文注入（Skills/Tools/Memory）
- `response_parser.py` — 解析 LLM JSON/XML/Text 格式输出
- `skill_loader.py` — 从 markdown 文件加载 Skill 行为指令到 LLM 上下文
- `tools/` — 工具系统：`BaseTool` 基类 + `InternalTool`（结果反馈 LLM）、`ToolRegistry` 注册与并行调度、`ToolLoader` JSON/目录自动发现、内置 `ReplyTool`/`TTSTool`/`WebSearchTool`/`MemoryQueryTool`
- `prompts/aliya_system_prompt.md` — Aliya 角色系统提示词（人设、说话风格、行为红线）
- `skills/` — 可扩展的 Skill 行为指令目录

**core/llm — LLM 服务**
- `service.py` — `ConversationService` 管理对话生命周期、自动消息历史管理（`history_max_chars` 控制字符数阈值）、指数退避重试（最多 3 次）、上下文注入机制（`set_context_injection`）
- `providers/` — 多提供商支持：`OllamaProvider`、`DeepSeekProvider`、`LMStudioProvider`、`OpenAICompatibleProvider`
- `cache.py` / `cache_backend.py` — 对话上下文缓存
- `models.py` — `ChatRequest`、`ChatResponse`、`ConversationContext`、`Message` 数据模型
- `config_validator.py` — 配置合法性校验（必需字段、key 范围）

**core/tts — TTS 语音服务**
- `service.py` — `TTSService` 语音合成服务，支持预取队列
- `providers/` — `EdgeTTSProvider`（edge-tts）、`AstraTTSProvider`（自建 Docker 服务）
- `player/` — 音频播放器（基于 sounddevice），支持 PCM 格式检测、音量控制
- `text_splitter.py` — 长文本分片处理
- `cache.py` — TTS 缓存机制

**memory — GRAG 图记忆系统**
- `memory_manager.py` — `GRAGMemoryManager` 统一接口
- `graph.py` — Neo4j 图谱操作（节点/关系创建、关键词查询、统计）
- `extractor.py` — 五元组 (subject, predicate, object, ...) 提取器，使用 LLM 从文本抽取结构化知识
- `rag_query.py` — RAG 查询引擎（关键词提取 + 图谱检索 + LLM 生成）
- `task_manager.py` — 异步任务管理器，支持并发五元组提取

**core/config — 配置管理**
- `manager.py` — `ConfigManager` 从 YAML 加载配置，支持点路径读写（`get("cosmos.service.llm.history_max_chars")`）和热重载

**docker/**  
- `compose.yml` — 编排 Neo4j（图谱数据库）和 AstraTTS（自建 TTS 服务）
- 用户需在首次启动前自行构建 AstraTTS Docker 镜像

### 关键设计约定

1. **ConversationService 统一管理对话历史**：Agent 和 Brain 不额外维护消息历史列表，全部复用 ConversationService 的内置机制
2. **GRAG 只存储记忆**：对话记忆通过 `add_conversation_memory()` 存储到 Neo4j，不作为 Tool 暴露
3. **上下文注入**：Skills、工具描述、记忆上下文通过 `set_context_injection(skills, tools, memory)` 每轮注入，本轮有效后自动清除
4. **工具并行执行**：使用 `asyncio.gather()` 并行调度工具，执行结果不反馈给下一轮 LLM
5. **LLM 重试**：ConversationService 内置最多 3 次指数退避重试，Brain 层不需要额外重试逻辑
6. **工具分类**：`BaseTool`（外部 dispatch，结果不反馈 LLM）与 `InternalTool`（结果通过 `append_message` 注入对话历史，LLM 继续推理），后者通过 `brain._internal_tools` 注册
7. **工具结果反馈循环（refine）**：外部工具 dispatch 完成后，成功结果摘要通过 `brain.think(tool_results=...)` 反馈给 LLM，LLM 基于真实数据生成最终回复。支持最多 3 轮 refine 循环（LLM 可继续请求新工具 → 继续 dispatch → 再次反馈）
8. **工具参数校验**：每个工具定义 `input_schema`（JSON Schema），dispatch 前通过 `validate_args()` 校验已传参数类型，失败返回 `INVALID_ARGS` 错误码

### 配置体系

所有配置集中在 `data/config/main.yml`，使用点路径访问：
- `cosmos.service.llm` — LLM 提供商配置
- `cosmos.service.tts` — TTS 提供商和播放器配置
- `cosmos.service.grag` — GRAG 记忆系统配置
- `cosmos.service.agent` — Agent 引擎配置
- `cosmos.logger` — 日志配置

LLM 提供商密钥等敏感配置存储在 `data/config/LLMProviders.json` 中。

**访问方式**：统一使用 `get_config_instance(config_path)` 获取单例（替代 `ConfigManager(config_path)`），首次调用时指定路径，后续调用可省略路径参数返回同一实例。

### WebSocket 协议

GUI 悬浮窗与 Agent 通过 WebSocket（默认 127.0.0.1:8765）通信，消息类型包括：
- `user_message` → 用户发送消息
- `brain_start/brain_progress/brain_complete/brain_error` → Brain 思考过程推送
- `brain_refine` → 工具执行结果反馈后优化的最终回复（替换 `brain_complete` 的预回复）
- `tool_start/tool_complete` → 工具执行状态推送（含 `error_code` 字段）
- `tool_summary` → 工具调度执行汇总（`{total, success, fail, errors?}`）
- `stop/clear_history/ping` → 控制消息
- 支持指数退避断线重连（1s/2s/5s/10s/30s，最多 5 次）

### 数据模型

- 五元组格式：`(subject, subject_type, predicate, object, object_type)`
- Neo4j Schema：`(:Entity:Person|Location|Object|Concept|Event|Time)` 节点 + `[:REL_TYPE]` 关系
- LLM 输出格式：JSON 包含 `reply` + `tool_calls` 字段
- 工具接口规范：每个 `BaseTool` 子类定义 `input_schema`（JSON Schema 格式参数描述），通过 `validate_args()` 预校验参数类型；`ToolResult` 含 `error_code` 结构化错误码（如 `TIMEOUT`/`INVALID_ARGS`/`TOOL_NOT_FOUND`）
- 工具分两类：`BaseTool`（外部 dispatch，结果不反馈 LLM）和 `InternalTool`（结果注入对话历史，LLM 继续推理），后者通过 `brain._internal_tools` 注册
