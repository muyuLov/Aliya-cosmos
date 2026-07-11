# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

《彼方的她-Aliya》

## Git 工作流

- 所有开发在 `dev` 分支上进行
- **提交代码前必须切换到 `dev` 分支**：`git checkout dev`（如不存在则 `git checkout -b dev`）
- 提交信息使用 Conventional Commits 规范：`feat:` / `fix:` / `refactor:` / `docs:` / `chore:`
- 推送至远程：`git push origin dev`

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
agent/           ← AI Agent 层：对话主循环、工具系统
core/            ← 核心服务层：LLM、TTS、配置管理、日志、异常
core/memory/     ← 记忆系统层：GRAG (Neo4j 图谱记忆、五元组提取、RAG 查询)
core/exception/  ← 异常定义
core/logger/     ← 日志系统
GUI/             ← 桌面客户端层
docker/          ← 基础设施：docker-compose 编排 Neo4j + AstraTTS
data/            ← 配置文件和数据目录
```

### 核心模块详解

**agent/ — AI Agent 引擎**
- `agent.py` — `AliyaAgent` 主编排器，**直接持有** `ConversationService`（无 BrainEngine 中间层）；管理 think→act→refine 循环、进度推送、上下文注入
- `ws.py` — WebSocket 端点处理器，每个 WS 连接一个独立 `AliyaAgent` 实例
- `tools/` — 工具系统：`BaseTool`（Protocol）、`ToolRegistry`（注册+缓存描述+并行调度）、`ReplyTool`（文本回复）、`TTSTool`（语音合成+播放）

**core/llm — LLM 服务**
- `service.py` — `ConversationService` 管理对话生命周期、自动消息历史管理（`history_max_chars` 控制字符数阈值）、指数退避重试（最多 3 次）、上下文注入机制（`set_context_injection`）
- `providers/` — 多提供商支持：`OllamaProvider`、`DeepSeekProvider`、`LMStudioProvider`、`OpenAICompatibleProvider`
- `cache.py` / `cache_backend.py` — 对话上下文缓存
- `models.py` — `ChatRequest`、`ChatResponse`、`ConversationContext`、`Message` 数据模型
- `config_validator.py` — 配置合法性校验（必需字段、key 范围）

**core/tts — TTS 语音服务**
- `service.py` — `TTSService` 语音合成服务，滑动窗口预取流水线
- `providers/` — `EdgeTTSProvider`（edge-tts）、`AstraTTSProvider`（自建 Docker 服务），统一继承 `TTSProvider` 基类
- `player/` — 音频播放器（sounddevice），自动检测 WAV/PCM/MP3 格式
- `models.py` — `TTSRequest`、`VoiceConfig` 数据模型
- `validation.py` — TTS 配置集中参数校验（`TTSConfigError`）
- `exceptions.py` — 结构化异常（TTS_001~TTS_005）：连接失败、请求失败、会话错误、配置错误
- `text_splitter.py` — 长文本分片与动作描写过滤
- `cache.py` — TTS 音频缓存（本地文件 + 可选 Redis）

**core/memory — GRAG 图记忆系统**
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

1. **ConversationService 统一管理对话历史**：Agent 不额外维护消息历史列表，全部复用 ConversationService 的内置机制
2. **GRAG 只存储记忆**：对话记忆通过 `add_conversation_memory()` 存储到 Neo4j，不作为 Tool 暴露
3. **上下文注入**：工具描述、记忆上下文通过 `set_context_injection(tools, memory)` 每轮注入（skills 已在 system_prompt 中），本轮有效后自动清除
4. **工具并行执行**：使用 `asyncio.gather()` 并行调度工具，结果通过 `format_tool_summary()` 摘要后反馈给 LLM
5. **LLM 重试**：ConversationService 内置最多 3 次指数退避重试，Agent 层不需要额外重试逻辑
6. **工具接口**：`BaseTool` 是 `Protocol`，工具只需实现 `name` / `description` / `input_schema` / `execute()`；通过 `ToolContext` 访问 TTS、音频播放器、记忆、消息发送等能力
7. **结果反馈循环（refine）**：工具 dispatch 完成后，成功结果摘要通过 `_refine()` 反馈给 LLM，LLM 基于真实数据生成最终回复。支持最多 3 轮 refine 循环
8. **启动入口**：`main.py` 支持 `--chat`（终端交互）和 `WS 服务器` 两种模式；WS 模式每个连接有独立的 `ConversationService` 实例

### 配置体系

所有配置集中在 `data/config/main.yml`，使用点路径访问：
- `cosmos.service.llm` — LLM 提供商配置
- `cosmos.service.tts` — TTS 提供商和播放器配置
- `cosmos.service.grag` — GRAG 记忆系统配置
- `cosmos.logger` — 日志配置

LLM 提供商密钥等敏感配置存储在 `data/config/LLMProviders.json` 中。

**访问方式**：统一使用 `get_config_instance(config_path)` 获取单例（替代 `ConfigManager(config_path)`），首次调用时指定路径，后续调用可省略路径参数返回同一实例。

### WebSocket 协议

GUI 悬浮窗与 Agent 通过 WebSocket（默认 127.0.0.1:8765）通信，消息类型包括：
- `user_message` → 用户发送消息
- `brain_start/brain_progress/brain_complete/brain_error` → 思考过程推送
- `brain_refine` → 工具执行结果反馈后优化的最终回复（替换 `brain_complete` 的预回复）
- `tool_start/tool_complete` → 工具执行状态推送
- `tool_summary` → 工具调度执行汇总（`{total, success, fail}`）
- `tts_complete` → TTS 合成完成通知
- `stop/clear_history/ping` → 控制消息
- 支持指数退避断线重连（1s/2s/5s/10s/30s，最多 5 次）

### 数据模型

- 五元组格式：`(subject, subject_type, predicate, object, object_type)`
- Neo4j Schema：`(:Entity:Person|Location|Object|Concept|Event|Time)` 节点 + `[:REL_TYPE]` 关系
- LLM 输出格式：JSON 包含 `reply` + `tool_calls` 字段
- 工具接口规范：每个工具定义 `input_schema`（JSON Schema 格式参数描述），通过 `ToolContext` 传入运行时依赖
