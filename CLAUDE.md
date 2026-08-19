# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

《彼方的她-Aliya》—— 基于 LLM 的 AI 伴侣桌面应用。Python 后端提供 WebSocket 服务（LLM 对话、TTS 语音、记忆系统），Electron + Vue 3 前端负责 Live2D 角色呈现与状态面板。角色设定取材自瞳电游的文字冒险游戏《彼方的她-Aliya》。

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

# Docker 编排（app + neo4j；AstraTTS 需 --profile tts）
docker compose up
docker compose --profile tts up -d
```

运行后端前需在 `.env` 配置 `DEEPSEEK_API_KEY`（参考 `.env.example`）。Neo4j 图记忆、Milvus 向量库均为可选——对应服务未启动时相关能力会自动降级禁用，不阻塞主流程。

## 架构总览

### 一次对话的完整流转

前端通过 WebSocket 连接 `ws://<host>:8765/agent/ws`（`main.py` 启动）。一次 `user_message` 的处理链：

1. `agent/ws.py` 为每个连接惰性创建并复用 `AliyaAgent`，将 `user_message` 以后台任务运行（`stop` 消息可即时打断）。
2. `AliyaAgent`（`agent/agent.py`）是稳定门面，内部委托 `AgentPipeline`（`agent/pipeline.py`）编排阶段流转。
3. **阶段一 assemble**（`agent/stages/assemble.py`）：切换工具阶段 system prompt（无角色人格，注入动态工具描述 + 认知上下文），触发对话压缩。
4. **阶段二 think**（`agent/stages/think.py`）：Think → Act → Observe 循环。`Brain.think()` 调用 LLM 得到 JSON 决策，`ToolRegistry.dispatch_all()` 执行工具，结果注入历史后 `think_with_context()` 继续，直至 LLM 返回正式 reply 或达 `max_turns`。
5. **阶段三 soul**（`agent/stages/soul.py`）：切换回人格 system prompt（`PromptManager` 分层构建），注入情绪补丁、压缩摘要、认知/记忆召回上下文，`Brain.generate_soul_reply()` 生成最终回复。
6. **收尾**：`after_turn` 钩子（记忆保存 → 认知后续 → 情绪推进后台任务）→ `agent/response.py` 统一响应（文本发送 + 异步 TTS 播放）。

横切能力（认知、记忆保存、情绪推进）均通过 `HookRegistry`（`agent/hooks.py`）以钩子订阅接入，`AgentContext`（`agent/context.py`）是承载全部依赖的不可变容器，工具执行时直接接收它。

### Agent 内部（agent/）

- **Brain**（`brain.py`）— LLM 交互层。工具阶段期望 LLM 输出 JSON `{"thought", "reply", "tool_calls"}`，`parse_llm_response` 含多层 fallback 解析链。管理超时降级（连续超时退出工具阶段）、`_compressed_context` 摘要、灵魂阶段净化链（`clean_soul_reply`）。原生 thinking 模式（`reasoning_content`）自动捕获为 thought。
- **情绪引擎**（`emotion/engine.py`）— VAD 三维情绪 + `EmotionPersonality` 人格参数；分类器三模式（`rule`/`vector`/`auto`，向量模式用 `core/vector` 做语义分类）。情绪推进在 `after_turn` 钩子中以 fire-and-forget 后台任务运行，不阻塞收尾。
- **工具系统**（`tools/`）— `ToolBase`/`ToolRegistry` 参考 Claude Code 流式工具模式：`is_concurrency_safe` 只读工具分区并行、`ToolPermission` + 配置驱动权限（`Permissions.yml`）、执行时直接接收 `AgentContext`。内置工具在 `create_default_tool_registry()`（`tools/__init__.py`）登记，**新增内置工具只需改这里**。工具描述动态注入工具阶段 prompt。
- **认知引擎**（`cognition/`）— 参考 LAAP 认知架构的模块化引擎，三段式钩子接入管线：`before_turn`（需求 tick、交互记录）、`after_tool`（需求更新、情景记忆、世界模型、自我模型）、`after_turn`（记忆巩固、自主维护）。子模块独立、优雅降级（任一失败不影响整体）。`rust_bridge.py` 实为纯 Python 加速实现（零外部依赖，API 保持兼容）。层次化记忆由它持有并持久化到 `data/memory/hierarchical_memory.json`。

### 核心服务（core/）

- **config/** — YAML 配置管理器：`get_config_instance()` 单例，点路径读写（`cfg.get("cosmos.service.llm")`），支持 `${ENV_VAR}` / `${ENV_VAR:default}` 占位符解析与热重载。
- **llm/** — `ConversationService` 管理单会话历史（`history_max_chars=90000` 超限清理最旧消息），异步优先（`asend`/`astream_send`）。提供商已收敛为单一 `OpenAICompatibleProvider`（`providers/openai_compatible.py`），通过 `LLMProviders.json` 区分 deepseek/ollama/lmstudio 等。`create_from_config()` 是从 YAML 创建服务的统一入口。
- **memory/** — **两套并行记忆系统**（见下）。
- **tts/** — `TTSService` 分段预取合成 + `AudioPlayer` 弹性播放。提供商工厂注册 `edge`（联网即用）/`astra`（自建服务，`docker compose --profile tts`）。播放失败自动降级到 WebSocket 音频流 / 文件 sink。
- **vector/** — 余弦相似度向量库：内存计算 + 可选 Milvus 持久化，连接失败自动回退纯内存。用于情绪向量分类器。
- **logger/**、**exception/** — 统一日志（YAML 配置：console/file/轮转）与异常体系。

### 记忆系统：两套并行

- **GRAG**（`core/memory/memory_manager.py` 等）— 借鉴 NagaAgent summer_memory：LLM 五元组提取（`extractor.py`）→ Neo4j 图存储（`graph.py`，Schema v4，去 APOC 依赖）→ RAG 召回（`rag_query.py`）→ 并发任务管理（`task_manager.py`）。`get_memory_manager()` 返回集成层 `GRAGMemoryManager`，在 `after_turn` 保存对话记忆、在灵魂阶段注入相关记忆。
- **层次化**（`core/memory/hierarchical.py`）— 参考 LAAP 第 10 章：感知/工作/情景/语义/程序五层 + 元记忆，跨层巩固（重复达阈值触发），JSON 持久化。由认知引擎持有并在灵魂/工具阶段做上下文召回。

### 前端（GUI/）

- **Electron 主进程**（`GUI/main/`）：`index.js` 组装生命周期；`windows.js` 创建透明无边框的 Live2D 窗口 + 状态侧栏；`ws.js` 连接后端 WS 并按 `type` 分发消息（含 5s 断线重连）；`ipc.js` 处理渲染进程交互；`state.js` 集中共享状态。
- **渲染进程**（`GUI/src/`）：Vue 3 + Pinia；Live2D 用 pixi.js + pixi-live2d-display。
- **WS 消息契约**（前后端协作关键，改协议需两侧同步）：
  - 客户端→服务端：`user_message`、`stop`、`set_emotion`、`get_emotion_state`、`get_prompt_config`、`get_cognition_state`、`ping`、`confirm_response`（工具权限确认回复）。
  - 服务端→客户端：`brain_start` / `brain_progress` / `brain_refine` / `brain_complete` / `brain_error`（思考与回复）、`status_changed` / `state_change`（阶段状态）、`emotion_changed` / `emotion_state`、`token_usage`、`tts_features`（口型同步音量数据）、`confirm_request`、`notice`。
  - `stop` 可打断进行中的回复；`user_message` 处理期间收到新消息会返回错误提示。

### 配置体系

- 主配置 `data/config/main.yml` 为唯一配置源头（日志、WS 端口、情绪、认知、GRAG、向量、LLM、TTS、prompt）。所有服务通过 `core.config.get_config_instance()` 读取，各自用 `*_from_config()` 工厂构建。
- 提供商配置分离：`data/config/LLMProviders.json`、`data/config/TTSProviders.json`；密钥走环境变量（`.env` → `${DEEPSEEK_API_KEY}`）。
- 工具权限：`data/config/Permissions.yml`。

## 关键约定

- **代码注释与文档均为中文**，标识符/代码语法保留英文。
- 全部异步优先：LLM/TTS/记忆均为 `async`，核心路径不得阻塞事件循环；同步包装仅用于脚本/REPL。
- 单测按模块分目录（`tests/agent`、`tests/memory`、`tests/llm` 等）。pytest `asyncio_mode = "auto"`（无需 `@pytest.mark.asyncio`），默认开启 coverage，可用 `--no-cov` 关闭。markers：`slow`/`integration`/`unit`/`tts`/`memory`/`llm`。
- 类型检查：`pyright`（`pyrightconfig.json`，standard 模式）与 `mypy` 双轨；`core/config`、`core/logger`、`agent/tools` 已启用 mypy 严格模式。
- 格式：Black（100 列）+ isort（black profile，first-party 为 agent/core/GUI/aliya_cosmos）。
- 降级原则贯穿全项目：可选依赖（Neo4j、Milvus、AstraTTS、音频硬件）初始化失败一律告警降级，不使 Agent 主流程崩溃。
